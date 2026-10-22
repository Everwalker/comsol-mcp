"""Reviewed public receiver capabilities; no caller-controlled dynamic effect."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, permission_for_effect
from ._g2_contract import (
    NodePath, engine_value_spec, property_schema_from_engine, resolve_node_path,
    signature_spec, typed_value_from_engine, validate_typed_value,
)

VERSION = "6.4.0.293"  # Compatibility constant only; capability checks are release+signature based.
SUPPORTED_RELEASES = frozenset({"6.3", "6.4"})
MODEL_ENTITY = "com.comsol.model.ModelEntity"
PROP_FEATURE = "com.comsol.model.PropFeature"
MODEL_ENTITY_DOC = "api/com/comsol/model/ModelEntity.html"
MODEL_ENTITY_DOC_SHA = "1e996ce8d28a6570a103c8bf341ca83f057cf8a90ece216b09d0cec192d7c2bf"
PROP_FEATURE_DOC = "api/com/comsol/model/PropFeature.html"
PROP_FEATURE_DOC_SHA = "befb8cc1c0e3df1ce74d2fa2cd1377e73d378e5c26170558e9f40dc255cc3bc3"
DOCUMENTATION_SOURCE_VERSION = "6.4.0.293"
SIGNATURE_OBSERVATION_SCOPE = "actual_runtime_receiver_exact_interface_method_parameters_and_return"
MAX_PUBLIC_INTERFACES = 1024
MAX_PUBLIC_METHODS = 8192
IDENTITY_FIELDS = frozenset({"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"})
API_FIELDS = frozenset({"path", "method", "arguments", "java_signature", "declared_effect"})

_VERSION_COMPONENT = r"(?:0|[1-9][0-9]*)"
_CANONICAL_RUNTIME_VERSION = re.compile(
    rf"{_VERSION_COMPONENT}\.{_VERSION_COMPONENT}\.{_VERSION_COMPONENT}\.{_VERSION_COMPONENT}"
)
_COMSOL_BUILD_DECLARATION = re.compile(
    rf"COMSOL Multiphysics (?P<release>{_VERSION_COMPONENT}\.{_VERSION_COMPONENT}(?:\.{_VERSION_COMPONENT})?) "
    rf"\((?P<build_label>开发版本|Build): (?P<build>{_VERSION_COMPONENT})\)"
)


def normalize_public_runtime_version(raw: Any) -> tuple[str, str]:
    """Parse only complete, closed COMSOL version declarations."""
    if type(raw) is not str or not raw or len(raw) > 256:
        raise ValueError("COMSOL runtime version must be a bounded nonempty string")
    if _CANONICAL_RUNTIME_VERSION.fullmatch(raw):
        return raw, "CANONICAL_FOUR_PART"
    match = _COMSOL_BUILD_DECLARATION.fullmatch(raw)
    if match is None:
        raise ValueError("COMSOL runtime version is not a complete recognized declaration")
    release_parts = match.group("release").split(".")
    if len(release_parts) == 2:
        release_parts.append("0")
    semantic_version = ".".join((*release_parts, match.group("build")))
    declaration_format = ("COMSOL_ZH_DEVELOPMENT_BUILD" if match.group("build_label") == "开发版本"
                          else "COMSOL_ENGLISH_BUILD")
    return semantic_version, declaration_format


def supports_release(runtime_version: Any) -> bool:
    """Accept only explicitly supported COMSOL releases, independently of build."""
    try:
        semantic, _format = normalize_public_runtime_version(runtime_version)
    except ValueError:
        return False
    return ".".join(semantic.split(".")[:2]) in SUPPORTED_RELEASES


@dataclass(frozen=True)
class Capability:
    receiver: str
    method: str
    parameters: tuple[str, ...]
    returns: str
    effect: str
    input_contract: tuple[tuple[str, int], ...] = ()
    return_kind: str | None = None
    return_rank: int | None = None
    property_value_type: str | None = None
    property_metadata: bool = False

    def argument_contract(self) -> list[dict[str, Any]]:
        return [{"kind": kind, "shape_rank": rank} for kind, rank in self.input_contract]

    def policy_dict(self) -> dict[str, Any]:
        return {
            "receiver_interface": self.receiver, "method": self.method,
            "java_signature": list(self.parameters), "java_return": self.returns,
            "effect": self.effect, "permission": permission_for_effect(legacy_effect(self.effect)),
            "supported_releases": sorted(SUPPORTED_RELEASES),
            "argument_contract": self.argument_contract(),
            "return_contract": _return_contract(self),
            "documentation": _documentation(self.receiver),
        }

    def as_dict(self, runtime_version: str) -> dict[str, Any]:
        return {
            **self.policy_dict(), "runtime_version": runtime_version,
            "policy_status": "REVIEWED", "invoke_allowed": True,
        }


def _documentation(receiver: str) -> dict[str, Any]:
    path, sha256 = ((PROP_FEATURE_DOC, PROP_FEATURE_DOC_SHA) if receiver == PROP_FEATURE
                    else (MODEL_ENTITY_DOC, MODEL_ENTITY_DOC_SHA))
    return {
        "path": path, "sha256": sha256,
        "source_version": DOCUMENTATION_SOURCE_VERSION,
        "reference_only": True,
        "signature_observation_scope": SIGNATURE_OBSERVATION_SCOPE,
    }


def _return_contract(capability: Capability) -> dict[str, Any] | None:
    if capability.return_kind is None:
        return None
    return {"kind": capability.return_kind, "shape_rank": capability.return_rank}


def _cap(receiver: str, method: str, parameters: tuple[str, ...], returns: str, effect: str,
         input_contract: tuple[tuple[str, int], ...] = (), *, return_kind: str | None = None,
         return_rank: int | None = None, property_value_type: str | None = None,
         property_metadata: bool = False) -> Capability:
    return Capability(receiver, method, parameters, returns, effect, input_contract,
                      return_kind, return_rank, property_value_type, property_metadata)


_STRING = "java.lang.String"
_STRING_ARRAY = "[Ljava.lang.String;"
_STRING_MATRIX = "[[Ljava.lang.String;"
_ENTITY = MODEL_ENTITY
_FEATURE = PROP_FEATURE
_PROPERTY_NAME = (("string", 0),)

_CAPABILITY_ROWS: list[Capability] = [
    _cap(_ENTITY, "comments", (), _STRING, "READ", return_kind="string", return_rank=0),
    _cap(_ENTITY, "comments", (_STRING,), _ENTITY, "WRITE", (("string", 0),)),
    _cap(_ENTITY, "label", (), _STRING, "READ", return_kind="string", return_rank=0),
    _cap(_ENTITY, "label", (_STRING,), _ENTITY, "WRITE", (("string", 0),)),
    _cap(_ENTITY, "tag", (), _STRING, "READ", return_kind="string", return_rank=0),
    _cap(_ENTITY, "isActive", (), "boolean", "READ", return_kind="boolean", return_rank=0),
    _cap(_ENTITY, "active", ("boolean",), _ENTITY, "WRITE", (("boolean", 0),)),
    _cap(_FEATURE, "properties", (), _STRING_ARRAY, "READ", return_kind="string", return_rank=1),
    _cap(_FEATURE, "getType", (), _STRING, "READ", return_kind="string", return_rank=0),
    _cap(_FEATURE, "getValueType", (_STRING,), _STRING, "READ", _PROPERTY_NAME,
         return_kind="string", return_rank=0, property_metadata=True),
    _cap(_FEATURE, "hasProperty", (_STRING,), "boolean", "READ", _PROPERTY_NAME,
         return_kind="boolean", return_rank=0),
    _cap(_FEATURE, "getAllowedPropertyValues", (_STRING,), _STRING_ARRAY, "READ", _PROPERTY_NAME,
         return_kind="string", return_rank=1, property_metadata=True),
]

_TYPED_PROPERTY_FAMILIES = (
    ("Boolean", "boolean", "boolean", "[Z", "[[Z"),
    ("Int", "int32", "int", "[I", "[[I"),
    ("Double", "float64", "double", "[D", "[[D"),
    ("String", "string", _STRING, _STRING_ARRAY, _STRING_MATRIX),
)
for _type_name, _kind, _scalar_sig, _array_sig, _matrix_sig in _TYPED_PROPERTY_FAMILIES:
    for _suffix, _signature, _rank in (("", _scalar_sig, 0), ("Array", _array_sig, 1), ("Matrix", _matrix_sig, 2)):
        _CAPABILITY_ROWS.append(_cap(
            _FEATURE, f"get{_type_name}{_suffix}", (_STRING,), _signature, "READ", _PROPERTY_NAME,
            return_kind=_kind, return_rank=_rank, property_value_type=_type_name + ("Array" if _rank == 1 else "Matrix" if _rank == 2 else ""),
        ))

CAPABILITIES = tuple(_CAPABILITY_ROWS)
_CAPABILITY_KEYS = {
    (cap.receiver, cap.method, cap.parameters, cap.returns): cap for cap in CAPABILITIES
}


def registry_sha() -> str:
    payload = [cap.policy_dict() for cap in CAPABILITIES]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    aliases = {
        "String": _STRING, "String[]": _STRING_ARRAY, "String[][]": _STRING_MATRIX,
        "boolean[]": "[Z", "boolean[][]": "[[Z", "int[]": "[I", "int[][]": "[[I",
        "double[]": "[D", "double[][]": "[[D", "java.lang.String[]": _STRING_ARRAY,
        "java.lang.String[][]": _STRING_MATRIX,
    }
    return aliases.get(signature.strip(), signature.strip())


def _candidate_argument_matches(value: Mapping[str, Any], expected: tuple[str, int],
                                expected_signature: str) -> bool:
    kind, rank = expected
    shape = value["shape"]
    if value["kind"] != kind or len(shape) != rank:
        return False
    declared = value.get("java_signature")
    if declared is None:
        return True
    canonical_declared = canonical_signature(declared)
    spec = signature_spec(canonical_declared)
    return canonical_declared == expected_signature and spec is not None and spec == {"kind": kind, "rank": rank}


def request_candidates(arguments: Mapping[str, Any]) -> tuple[Capability, ...]:
    """Pure provisional policy: exact typed shape+overload; never trust declared_effect."""
    if not isinstance(arguments, Mapping):
        _refuse("api.invoke arguments must be an object")
    if set(arguments) - API_FIELDS - IDENTITY_FIELDS:
        _refuse("api.invoke has unknown fields")
    if not {"path", "method", "arguments", "declared_effect"} <= set(arguments):
        _refuse("api.invoke requires path/method/arguments/declared_effect")
    NodePath.from_wire(arguments["path"])
    method = arguments["method"]
    values = arguments["arguments"]
    if not isinstance(method, str) or not method or len(method) > 128 or not isinstance(values, list) or len(values) > 16:
        _refuse("public method and bounded argument array are required")
    validated = [validate_typed_value(value) for value in values]
    signature = arguments.get("java_signature")
    if signature is not None:
        if (not isinstance(signature, list) or len(signature) != len(values)
                or not all(isinstance(item, str) and item for item in signature)):
            _refuse("java_signature must name every argument exactly")
        signature = tuple(canonical_signature(item) for item in signature)
    candidates = []
    for cap in CAPABILITIES:
        if cap.method != method or len(cap.parameters) != len(validated):
            continue
        if signature is not None and signature != cap.parameters:
            continue
        if len(cap.input_contract) != len(validated):
            continue
        if not all(_candidate_argument_matches(value, contract, signature)
                   for value, contract, signature in zip(validated, cap.input_contract, cap.parameters)):
            continue
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
            or len(descriptor["interfaces"]) > MAX_PUBLIC_INTERFACES
            or not all(isinstance(item, str) and len(item) <= 512 for item in descriptor["interfaces"])
            or not isinstance(descriptor.get("methods"), list)
            or len(descriptor["methods"]) > MAX_PUBLIC_METHODS):
        _refuse("Worker returned invalid or unbounded public receiver metadata", "EXECUTION_STATE_UNKNOWN")
    raw_version = descriptor["runtime_version"]
    try:
        semantic_version, declaration_format = normalize_public_runtime_version(raw_version)
    except ValueError:
        _refuse("Worker returned an unsupported or ambiguous COMSOL runtime version declaration",
                "API_UNSUPPORTED")
    methods: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in descriptor["methods"]:
        if (not isinstance(row, Mapping) or not isinstance(row.get("interface"), str)
                or len(row["interface"]) > 512 or not isinstance(row.get("method"), str)
                or len(row["method"]) > 128 or not isinstance(row.get("parameters"), list)
                or len(row["parameters"]) > 32
                or not all(isinstance(item, str) and len(item) <= 512 for item in row["parameters"])
                or not isinstance(row.get("returns"), str) or len(row["returns"]) > 512):
            _refuse("Worker returned invalid public signature metadata", "EXECUTION_STATE_UNKNOWN")
        key = (row["interface"], row["method"], tuple(row["parameters"]), row["returns"])
        if key in seen:
            continue
        seen.add(key)
        methods.append(dict(row))
    return {
        "runtime_version": semantic_version, "runtime_version_raw": raw_version,
        "runtime_version_format": declaration_format,
        "interfaces": list(descriptor["interfaces"]), "methods": methods,
        "metadata_complete": descriptor.get("metadata_complete") is True,
        "metadata_limits": dict(descriptor.get("metadata_limits", {
            "interfaces": MAX_PUBLIC_INTERFACES, "methods": MAX_PUBLIC_METHODS,
        })),
    }


def supports(descriptor: Mapping[str, Any], owner: str, method: str,
             parameters: tuple[str, ...], returns: str | None = None) -> bool:
    return owner in descriptor["interfaces"] and any(
        row["interface"] == owner and row["method"] == method
        and tuple(row["parameters"]) == parameters and (returns is None or row["returns"] == returns)
        for row in descriptor["methods"]
    )


def _capability_for_row(row: Mapping[str, Any]) -> Capability | None:
    return _CAPABILITY_KEYS.get((row["interface"], row["method"], tuple(row["parameters"]), row["returns"]))


def _controlled_alternative(row: Mapping[str, Any]) -> dict[str, Any]:
    routes = {
        "set": "node.property_set", "setIndex": "node.property_index_set",
        "setEntry": "node.property_entry_set",
    }
    operation = routes.get(row["method"])
    if operation is not None and row["interface"].startswith(PROP_FEATURE):
        return {"operation": operation, "permission": "project_write",
                "authorization_required": True, "authorizes_this_signature": False}
    return {"operation": "code.execute_java", "permission": "trusted_code",
            "authorization_required": True, "authorizes_this_signature": False}


def _method_wire(row: Mapping[str, Any], descriptor: Mapping[str, Any]) -> dict[str, Any]:
    cap = _capability_for_row(row)
    if cap is not None and supports_release(descriptor["runtime_version"]):
        return {
            "interface": row["interface"], "method": row["method"],
            "parameters": list(row["parameters"]), "returns": row["returns"],
            "policy_status": "REVIEWED", "invoke_allowed": True, "effect": cap.effect,
            "permission": permission_for_effect(legacy_effect(cap.effect)),
            "supported_releases": sorted(SUPPORTED_RELEASES),
            "argument_contract": cap.argument_contract(), "return_contract": _return_contract(cap),
            "documentation": _documentation(cap.receiver),
            "controlled_alternative": None,
        }
    return {
        "interface": row["interface"], "method": row["method"],
        "parameters": list(row["parameters"]), "returns": row["returns"],
        "policy_status": "UNREVIEWED", "invoke_allowed": False, "effect": None,
        "permission": None, "supported_releases": [],
        "argument_contract": [{"kind": "unknown", "shape_rank": None}
                              for _ in row["parameters"]],
        "return_contract": None, "documentation": None,
        "controlled_alternative": _controlled_alternative(row),
    }


def prepare_invoke(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    candidates = request_candidates(arguments)
    expected_effect = provisional_effect(arguments)
    if arguments["declared_effect"] != expected_effect:
        _refuse("declared_effect differs from the server capability", "PERMISSION_DENIED")
    node = resolve_node_path(worker.client().model(model_tag), arguments["path"])
    descriptor = describe_receiver(worker, node)
    if not supports_release(descriptor["runtime_version"]):
        _refuse("actual COMSOL release has no reviewed adapter", "API_UNSUPPORTED")
    available = [cap for cap in candidates if supports(
        descriptor, cap.receiver, cap.method, cap.parameters, cap.returns)]
    if len(available) != 1:
        _refuse("actual receiver/version/signature does not identify one reviewed overload", "API_UNSUPPORTED")
    cap = available[0]
    if cap.effect != expected_effect:
        _refuse("actual public effect differs from provisional policy", "PERMISSION_DENIED")
    values = [validate_typed_value(value) for value in arguments["arguments"]]
    property_schema = None
    if cap.property_metadata or cap.property_value_type is not None:
        property_name = values[0]["data"]
        property_schema = property_schema_from_engine(node, property_name, probe_has_property=True)
        if property_schema.get("has_property") is not True:
            _refuse("PropFeature property is absent or its presence is unreadable", "API_UNSUPPORTED")
        if (cap.property_value_type is not None
                and property_schema.get("value_type") != cap.property_value_type):
            _refuse("PropFeature getter does not match the property's actual type/rank", "PROPERTY_TYPE_MISMATCH")
        if cap.property_value_type is not None and property_schema.get("metadata_status") != "KNOWN":
            _refuse("PropFeature getter requires known type metadata", "API_UNSUPPORTED")
    for value, signature in zip(values, cap.parameters):
        value["java_signature"] = signature
    return {
        "node": node, "descriptor": descriptor, "capability": cap, "arguments": values,
        "path": NodePath.from_wire(arguments["path"]).as_dict(),
        "property_schema": property_schema, "effect": legacy_effect(cap.effect),
    }


def describe_api(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if set(arguments) - {"path", "method"} - IDENTITY_FIELDS:
        _refuse("api.describe has unknown fields")
    path = NodePath.from_wire(arguments.get("path"))
    method = arguments.get("method")
    if method is not None and (not isinstance(method, str) or not method or len(method) > 128):
        _refuse("method must be a bounded nonempty string")
    node = resolve_node_path(worker.client().model(model_tag), path)
    descriptor = describe_receiver(worker, node)
    if not supports_release(descriptor["runtime_version"]):
        _refuse("actual COMSOL release has no reviewed adapter", "API_UNSUPPORTED")
    observed = [row for row in descriptor["methods"] if method is None or row["method"] == method]
    if method is not None and not observed:
        _refuse("requested method is absent from the actual public receiver metadata", "API_UNSUPPORTED")
    methods = [_method_wire(row, descriptor) for row in observed]
    complete = descriptor["metadata_complete"]
    status = "OBSERVED" if complete else "INCOMPLETE"
    return {
        "schema_version": 1, "operation": "api.describe", "status": status,
        "complete": complete, "path": path.as_dict(),
        "runtime_version": descriptor["runtime_version"],
        "runtime_version_raw": descriptor["runtime_version_raw"],
        "runtime_version_format": descriptor["runtime_version_format"],
        "metadata_complete": complete, "metadata_limits": descriptor["metadata_limits"],
        "methods": methods, "receiver_interfaces": descriptor["interfaces"],
        "capability_registry_sha": registry_sha(),
        "source_identity": {"model_tag": model_tag},
        "evidence": {"descriptor": descriptor},
        "coverage": [{"path": path.as_dict(), "scope": "observed public COMSOL interface signatures",
                      "status": status, "method_count": len(methods)}],
        "errors": [],
    }


def _typed_result(value: Any, cap: Capability) -> dict[str, Any]:
    if value is None:
        if cap.returns in {"boolean", "byte", "char", "double", "float", "int", "long", "short"}:
            _refuse("public primitive getter returned null", "EXECUTION_STATE_UNKNOWN")
        return {"kind": "null", "java_type": cap.returns}
    if cap.return_kind is None:
        _refuse("public method has no typed return contract", "EXECUTION_STATE_UNKNOWN")
    typed = typed_value_from_engine(value, kind=cap.return_kind)
    typed["java_signature"] = cap.returns
    validate_typed_value(typed)
    if cap.return_rank is not None and len(typed["shape"]) != cap.return_rank:
        # Empty Java arrays can hide trailing dimensions in JSON. Their exact
        # Java signature remains present; a nonempty value must match the rank.
        if not (typed["shape"] == [0] and typed["java_signature"].startswith("[[")):
            _refuse("public getter returned a value with the wrong rank", "EXECUTION_STATE_UNKNOWN")
    return {"kind": "value", "java_type": cap.returns, "value": typed}


def execute_prepared(worker: Any, prepared: Mapping[str, Any]) -> dict[str, Any]:
    cap: Capability = prepared["capability"]
    node = prepared["node"]
    try:
        returned = getattr(node, cap.method)(*prepared["arguments"])
        if cap.effect == "WRITE":
            if cap.returns != MODEL_ENTITY or returned is None:
                _refuse("public setter returned an unexpected receiver", "EXECUTION_STATE_UNKNOWN")
            identity = getattr(worker, "entity_identity", None)
            same_receiver = identity(node, returned) if callable(identity) else returned is node
            if same_receiver is not True:
                _refuse("public setter returned a different receiver", "EXECUTION_STATE_UNKNOWN")
            metadata = describe_receiver(worker, returned)
            if MODEL_ENTITY not in metadata["interfaces"] or metadata["runtime_version"] != prepared["descriptor"]["runtime_version"]:
                _refuse("public setter returned a foreign/unverified ModelEntity", "EXECUTION_STATE_UNKNOWN")
            readback = getattr(node, cap.method if cap.method in {"comments", "label"} else "isActive")()
            expected = prepared["arguments"][0]["data"]
            if readback != expected or (cap.method == "active" and type(readback) is not bool):
                _refuse("public setter readback differs from request", "EXECUTION_STATE_UNKNOWN")
            typed_return = {"kind": "node_ref", "model_ref": prepared["model_ref"],
                            "path": prepared["path"], "public_interface": MODEL_ENTITY}
        else:
            typed_return = _typed_result(returned, cap)
            readback = returned
        return {
            "success": True,
            "data": {
                "schema_version": 1, "operation": "api.invoke", "status": "OBSERVED",
                "complete": True, "path": prepared["path"],
                "capability": cap.as_dict(prepared["descriptor"]["runtime_version"]),
                "resolved_effect": cap.effect,
                "source_identity": {"model_ref": prepared["model_ref"]},
                "evidence": {"descriptor": prepared["descriptor"],
                             "capability_registry_sha": registry_sha()},
                "typed_return": typed_return, "readback": readback,
                "coverage": [{"path": prepared["path"],
                              "scope": "exact signature, actual receiver, and typed readback",
                              "status": "VERIFIED"}],
                "errors": [],
            },
        }
    except Exception as exc:
        return {
            "success": False, "execution_state_unknown": True,
            "partial_change": cap.effect != "READ",
            "data": {
                "schema_version": 1, "operation": "api.invoke", "status": "UNKNOWN",
                "complete": False, "path": prepared["path"], "resolved_effect": cap.effect,
                "source_identity": {"model_ref": prepared["model_ref"]},
                "evidence": {"descriptor": prepared["descriptor"]},
                "coverage": [{"path": prepared["path"],
                              "scope": "post-dispatch return/readback", "status": "UNREADABLE"}],
                "errors": [{"code": getattr(exc, "code", "PUBLIC_API_FAILED"),
                            "message": str(exc), "stage": "post_dispatch"}],
            },
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": str(exc), "safe_retry": False},
        }
