"""Reviewed public receiver capabilities; no caller-controlled dynamic effect."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, permission_for_effect
from ._g2_contract import NodePath, resolve_node_path, validate_typed_value

VERSION = "6.4.0.293"
MODEL_ENTITY = "com.comsol.model.ModelEntity"
DOC = "api/com/comsol/model/ModelEntity.html"
DOC_SHA = "1e996ce8d28a6570a103c8bf341ca83f057cf8a90ece216b09d0cec192d7c2bf"
IDENTITY_FIELDS = frozenset({"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"})
API_FIELDS = frozenset({"path", "method", "arguments", "java_signature", "declared_effect"})


@dataclass(frozen=True)
class Capability:
    receiver: str
    method: str
    parameters: tuple[str, ...]
    returns: str
    effect: str
    version: str = VERSION

    def as_dict(self) -> dict[str, Any]:
        return {"receiver_interface": self.receiver, "method": self.method,
                "java_signature": list(self.parameters), "java_return": self.returns,
                "effect": self.effect, "permission": permission_for_effect(legacy_effect(self.effect)),
                "runtime_version": self.version, "documentation": {"path": DOC, "sha256": DOC_SHA},
                "argument_contract": [{"kind": "string", "shape": []} for _ in self.parameters]}


CAPABILITIES = (
    Capability(MODEL_ENTITY, "comments", (), "java.lang.String", "READ"),
    Capability(MODEL_ENTITY, "comments", ("java.lang.String",), MODEL_ENTITY, "WRITE"),
)


def registry_sha() -> str:
    return hashlib.sha256(json.dumps([cap.as_dict() for cap in CAPABILITIES], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def routed_arguments(operation: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Closed routing extraction shared by project ACL and the backend boundary."""
    if operation in {"registry_call", "operation_call"}:
        if (set(arguments) != {"operation_id", "arguments"} or arguments.get("operation_id") != "api.invoke"
                or not isinstance(arguments.get("arguments"), Mapping)):
            _refuse("api.invoke fallback requires exact operation_id/arguments body")
        body = arguments["arguments"]
        if set(body) & IDENTITY_FIELDS:
            _refuse("fallback API identity belongs in the execution envelope")
        return body
    if operation not in {"api.invoke", "api_invoke"}:
        _refuse("API route does not match the invoke capability")
    return arguments


def legacy_effect(effect: str) -> str:
    effects = {"READ": "inspect", "WRITE": "project_write", "COMPUTE": "compute"}
    if effect not in effects:
        raise ExecutionContractError("PERMISSION_DENIED", "public capability has no safe effect")
    return effects[effect]


def _refuse(message: str, code: str = "INVALID_REQUEST") -> None:
    raise ExecutionContractError(code, message)


def canonical_signature(signature: str) -> str:
    return "java.lang.String" if signature == "String" else signature


def request_candidates(arguments: Mapping[str, Any]) -> tuple[Capability, ...]:
    """Pure provisional policy: shape+exact overload; never trust declared_effect."""
    if not isinstance(arguments, Mapping):
        _refuse("api.invoke arguments must be an object")
    if set(arguments) - API_FIELDS - IDENTITY_FIELDS:
        _refuse("api.invoke has unknown fields")
    if not {"path", "method", "arguments", "declared_effect"} <= set(arguments):
        _refuse("api.invoke requires path/method/arguments/declared_effect")
    NodePath.from_wire(arguments["path"])
    method = arguments["method"]
    values = arguments["arguments"]
    if not isinstance(method, str) or not isinstance(values, list):
        _refuse("public method and arguments must be string and array")
    validated = [validate_typed_value(value) for value in values]
    signature = arguments.get("java_signature")
    if signature is not None:
        if not isinstance(signature, list) or not all(isinstance(item, str) for item in signature) or len(signature) != len(values):
            _refuse("java_signature must name every argument exactly")
        signature = tuple(canonical_signature(item) for item in signature)
    candidates = []
    for cap in CAPABILITIES:
        if cap.method != method or len(cap.parameters) != len(validated):
            continue
        if signature is not None and signature != cap.parameters:
            continue
        good = True
        for value, parameter in zip(validated, cap.parameters):
            if parameter != "java.lang.String" or value["kind"] != "string" or value["shape"] != []:
                good = False
            declared = value.get("java_signature")
            if declared is not None and canonical_signature(declared) != parameter:
                good = False
        if good:
            candidates.append(cap)
    if not candidates:
        _refuse("public method/typed signature has no reviewed capability", "API_UNSUPPORTED")
    return tuple(candidates)


def provisional_effect(arguments: Mapping[str, Any]) -> str:
    candidates = request_candidates(arguments)
    effects = {cap.effect for cap in candidates}
    signatures = {cap.parameters for cap in candidates}
    if len(effects) != 1 or len(signatures) != 1:
        _refuse("public request has ambiguous effect or overload", "API_UNSUPPORTED")
    effect = next(iter(effects))
    legacy_effect(effect)
    return effect


def describe_receiver(worker: Any, node: Any) -> dict[str, Any]:
    describe = getattr(worker, "describe_public", None)
    if not callable(describe):
        _refuse("Worker has no public interface descriptor", "API_UNSUPPORTED")
    descriptor = describe(node)
    if (not isinstance(descriptor, Mapping) or not isinstance(descriptor.get("runtime_version"), str)
            or not isinstance(descriptor.get("interfaces"), list)
            or not all(isinstance(item, str) for item in descriptor["interfaces"])
            or not isinstance(descriptor.get("methods"), list)):
        _refuse("Worker returned invalid public receiver metadata", "EXECUTION_STATE_UNKNOWN")
    methods = []
    for row in descriptor["methods"]:
        if (not isinstance(row, Mapping) or not isinstance(row.get("interface"), str)
                or not isinstance(row.get("method"), str) or not isinstance(row.get("parameters"), list)
                or not all(isinstance(item, str) for item in row["parameters"])
                or not isinstance(row.get("returns"), str)):
            _refuse("Worker returned invalid public signature metadata", "EXECUTION_STATE_UNKNOWN")
        methods.append(dict(row))
    return {"runtime_version": descriptor["runtime_version"], "interfaces": list(descriptor["interfaces"]), "methods": methods}


def supports(descriptor: Mapping[str, Any], owner: str, method: str, parameters: tuple[str, ...], returns: str | None = None) -> bool:
    return owner in descriptor["interfaces"] and any(
        row["interface"] == owner and row["method"] == method
        and tuple(row["parameters"]) == parameters and (returns is None or row["returns"] == returns)
        for row in descriptor["methods"]
    )


def prepare_invoke(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    candidates = request_candidates(arguments)
    expected_effect = provisional_effect(arguments)
    if arguments["declared_effect"] != expected_effect:
        _refuse("declared_effect differs from the server capability", "PERMISSION_DENIED")
    node = resolve_node_path(worker.client().model(model_tag), arguments["path"])
    descriptor = describe_receiver(worker, node)
    available = [cap for cap in candidates if descriptor["runtime_version"] == cap.version
                 and supports(descriptor, cap.receiver, cap.method, cap.parameters, cap.returns)]
    if len(available) != 1:
        _refuse("actual receiver/version does not identify one reviewed overload", "API_UNSUPPORTED")
    cap = available[0]
    if cap.effect != expected_effect:
        _refuse("actual public effect differs from provisional policy", "PERMISSION_DENIED")
    values = [validate_typed_value(value) for value in arguments["arguments"]]
    for value, signature in zip(values, cap.parameters):
        value["java_signature"] = signature
    return {"node": node, "descriptor": descriptor, "capability": cap, "arguments": values,
            "path": NodePath.from_wire(arguments["path"]).as_dict(), "effect": legacy_effect(cap.effect)}


def describe_api(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if set(arguments) - {"path", "method"} - IDENTITY_FIELDS:
        _refuse("api.describe has unknown fields")
    path = NodePath.from_wire(arguments.get("path"))
    method = arguments.get("method")
    if method is not None and not isinstance(method, str):
        _refuse("method must be a string")
    node = resolve_node_path(worker.client().model(model_tag), path)
    descriptor = describe_receiver(worker, node)
    rows = [cap.as_dict() for cap in CAPABILITIES if (method is None or cap.method == method)
            and descriptor["runtime_version"] == cap.version
            and supports(descriptor, cap.receiver, cap.method, cap.parameters, cap.returns)]
    if not rows:
        _refuse("method is not an allowed public capability for this receiver/version", "API_UNSUPPORTED")
    return {"schema_version": 1, "operation": "api.describe", "status": "OBSERVED", "complete": True,
            "path": path.as_dict(), "runtime_version": descriptor["runtime_version"], "methods": rows,
            "receiver_interfaces": descriptor["interfaces"], "capability_registry_sha": registry_sha(),
            "source_identity": {"model_tag": model_tag}, "evidence": {"descriptor": descriptor},
            "coverage": [{"path": path.as_dict(), "scope": "reviewed public capability registry", "status": "VERIFIED"}], "errors": []}


def execute_prepared(worker: Any, prepared: Mapping[str, Any]) -> dict[str, Any]:
    cap = prepared["capability"]
    node = prepared["node"]
    try:
        returned = getattr(node, cap.method)(*prepared["arguments"])
        if cap.returns == "java.lang.String":
            if returned is not None and not isinstance(returned, str):
                _refuse("public getter returned an unexpected type", "EXECUTION_STATE_UNKNOWN")
            value = ({"kind": "null", "java_type": cap.returns} if returned is None else
                     {"kind": "value", "java_type": cap.returns, "value": {"kind": "string", "shape": [], "data": returned}})
            readback = returned
        else:
            metadata = describe_receiver(worker, returned)
            if MODEL_ENTITY not in metadata["interfaces"] or metadata["runtime_version"] != cap.version:
                _refuse("public setter returned a foreign/unverified node", "EXECUTION_STATE_UNKNOWN")
            original_path = node.resolveModelPath()
            returned_path = returned.resolveModelPath()
            if not isinstance(original_path, str) or not original_path or returned_path != original_path:
                _refuse("public setter returned a different receiver path", "EXECUTION_STATE_UNKNOWN")
            readback = node.comments()
            if readback != prepared["arguments"][0]["data"]:
                _refuse("public setter readback differs from request", "EXECUTION_STATE_UNKNOWN")
            value = {"kind": "node_ref", "model_ref": prepared["model_ref"], "path": prepared["path"], "public_interface": MODEL_ENTITY}
        return {"success": True, "data": {"schema_version": 1, "operation": "api.invoke", "status": "OBSERVED",
                "complete": True, "path": prepared["path"], "capability": cap.as_dict(), "resolved_effect": cap.effect,
                "source_identity": {"model_ref": prepared["model_ref"]}, "evidence": {"descriptor": prepared["descriptor"], "capability_registry_sha": registry_sha()},
                "typed_return": value, "readback": readback, "coverage": [{"path": prepared["path"], "scope": "exact capability + actual receiver", "status": "VERIFIED"}], "errors": []}}
    except Exception as exc:
        return {"success": False, "execution_state_unknown": True, "partial_change": cap.effect != "READ",
                "data": {"schema_version": 1, "operation": "api.invoke", "status": "UNKNOWN", "complete": False,
                         "path": prepared["path"], "resolved_effect": cap.effect,
                         "source_identity": {"model_ref": prepared["model_ref"]}, "evidence": {"descriptor": prepared["descriptor"]},
                         "coverage": [{"path": prepared["path"], "scope": "post-dispatch return/readback", "status": "UNREADABLE"}],
                         "errors": [{"code": getattr(exc, "code", "PUBLIC_API_FAILED"), "message": str(exc), "stage": "post_dispatch"}]},
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": str(exc), "safe_retry": False}}
