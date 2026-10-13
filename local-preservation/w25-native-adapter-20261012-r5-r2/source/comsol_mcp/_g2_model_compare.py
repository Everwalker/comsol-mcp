"""Bounded comparison of two current, project-bound managed ModelRefs.

This route observes only the reviewed pure-getter projection vocabulary in
``_g2_checkpoint_diff.Reader``.  It never loads a saved file, dispatches a
caller-selected Java method, or uses tag/file hashes as semantic values.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, ModelRef, model_ref_from_mapping
from ._g2_contract import NodePath
from ._g2_engine import _parse_search_budget
from ._g2_public_api import IDENTITY_FIELDS, VERSION
from ._g2_checkpoint_diff import (
    Budget, BudgetStop, Reader, UnknownObservationStop, _MISSING, canonical,
    compare as compare_projections, path_key,
)

SCHEMA = "comsol-model-semantic-projection-v1"
_STRING = "java.lang.String"
_REF_KEYS = frozenset({"schema_version", "session_id", "server_instance_id", "model_tag", "generation"})
_SCOPE_KEYS = frozenset({"schema_version", "mode", "paths", "properties", "metrics", "budget", "page_size"})
_METRICS = {
    "geometry.spatial_dimension": ("GeomInfo", "getSDim", (), "int", None),
    "geometry.entity_counts": ("GeomSequence", "getNEntities", (), "int[]", None),
    "geometry.bounding_box": ("GeomSequence", "getBoundingBox", (), "double[]", "lengthUnit"),
    "mesh.element_count": ("MeshSequence", "getNumElem", (), "int", None),
    "mesh.vertex_count": ("MeshSequence", "getNumVertex", (), "int", None),
}


def fail(code: str, message: str) -> None:
    raise ExecutionContractError(code, message)


def _object_without_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _strict_ref_mapping(value: Any, label: str) -> ModelRef:
    if not isinstance(value, Mapping) or set(value) != _REF_KEYS:
        fail("MODEL_IDENTITY_MISMATCH", f"{label} must contain exactly the five ModelRef identity fields")
    expected_types = {"schema_version": int, "session_id": str, "server_instance_id": str,
                      "model_tag": str, "generation": int}
    for name, expected in expected_types.items():
        if type(value.get(name)) is not expected:
            fail("MODEL_IDENTITY_MISMATCH", f"{label}.{name} has the wrong exact type")
    try:
        ref = model_ref_from_mapping(value)
    except (ExecutionContractError, TypeError, ValueError) as exc:
        fail("MODEL_IDENTITY_MISMATCH", f"{label} is not a valid current ModelRef: {exc}")
    if ref.schema_version != 1 or ref.as_dict() != dict(value):
        fail("MODEL_IDENTITY_MISMATCH", f"{label} is not the canonical complete ModelRef")
    return ref


def _serialized_other_ref(value: Any) -> ModelRef:
    if not isinstance(value, str) or not value or "\x00" in value:
        fail("INVALID_REQUEST", "other_model_ref must be canonical JSON for a complete ModelRef")
    try:
        decoded = json.loads(value, object_pairs_hook=_object_without_duplicate_keys,
                             parse_constant=lambda _item: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        fail("INVALID_REQUEST", f"other_model_ref is malformed canonical JSON: {exc}")
    if not isinstance(decoded, dict) or canonical(decoded) != value:
        fail("INVALID_REQUEST", "other_model_ref must use the exact compact sorted-key JSON form")
    return _strict_ref_mapping(decoded, "other_model_ref")


def _scope_schema() -> dict[str, Any]:
    from ._g2_checkpoint_ops import input_schema as base_schema
    path = base_schema("api.probe")["properties"]["probe"]["oneOf"][0]["properties"]["path"]
    name_list = {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}
    properties = {
        "schema_version": {"type": "integer", "const": 1},
        "mode": {"type": "string", "enum": ["model", "paths"]},
        "paths": {"type": "array", "items": path},
        "properties": {"type": "array", "items": {
            "type": "object", "required": ["path", "names"],
            "properties": {"path": path, "names": name_list}, "additionalProperties": False,
        }},
        "metrics": {"type": "array", "items": {
            "type": "object", "required": ["path", "names"],
            "properties": {"path": path, "names": {"type": "array", "items": {"type": "string", "enum": sorted(_METRICS)}, "minItems": 1}},
            "additionalProperties": False,
        }},
        "budget": {"type": "object", "properties": {
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 100000},
            "max_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 600},
            "max_rpc": {"type": "integer", "minimum": 1, "maximum": 1000000},
        }, "additionalProperties": False},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
    }
    return {"type": "object", "properties": properties, "additionalProperties": False}


def input_schema() -> dict[str, Any]:
    from ._g2_checkpoint_ops import input_schema as base_schema
    identity = base_schema("api.probe")["properties"]
    identity = {name: value for name, value in identity.items() if name != "probe"}
    return {
        "type": "object",
        "required": ["project_id", "session_id", "model_ref", "other_model_ref"],
        "properties": {
            **identity,
            "other_model_ref": {"type": "string", "minLength": 1,
                                "description": "Canonical compact sorted-key JSON containing the complete second ModelRef."},
            "scope": _scope_schema(),
        },
        "additionalProperties": False,
    }


def normalize(arguments: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"other_model_ref", "scope"} | IDENTITY_FIELDS
    if (not isinstance(arguments, Mapping) or set(arguments) - allowed
            or "other_model_ref" not in arguments):
        fail("INVALID_REQUEST", "model.compare requires a closed other_model_ref/scope body")
    if "expected_revision" in arguments:
        fail("INVALID_REQUEST", "model.compare is READ and does not accept expected_revision")
    other = _serialized_other_ref(arguments["other_model_ref"])
    scope = arguments.get("scope", {})
    if not isinstance(scope, Mapping) or set(scope) - _SCOPE_KEYS:
        fail("INVALID_REQUEST", "model.compare scope has unknown fields")
    version = scope.get("schema_version", 1)
    if type(version) is not int or version != 1:
        fail("INVALID_REQUEST", "model.compare scope.schema_version must be integer 1")
    mode = scope.get("mode", "paths" if any(k in scope for k in ("paths", "properties", "metrics")) else "model")
    if not isinstance(mode, str) or mode not in {"model", "paths"}:
        fail("INVALID_REQUEST", "model.compare scope.mode must be model or paths")
    paths_raw, properties_raw, metrics_raw = scope.get("paths", []), scope.get("properties", []), scope.get("metrics", [])
    if not isinstance(paths_raw, list) or not isinstance(properties_raw, list) or not isinstance(metrics_raw, list):
        fail("INVALID_REQUEST", "model.compare paths, properties and metrics must be arrays")
    if mode == "model" and ("paths" in scope or "properties" in scope):
        fail("INVALID_REQUEST", "model mode cannot narrow paths or named properties")
    paths = [NodePath.from_wire(path).as_dict() for path in paths_raw]
    if len({path_key(path) for path in paths}) != len(paths):
        fail("INVALID_REQUEST", "model.compare scope contains duplicate paths")
    properties = []
    for row in properties_raw:
        if (not isinstance(row, Mapping) or set(row) != {"path", "names"}
                or not isinstance(row["names"], list) or not row["names"]
                or not all(isinstance(name, str) and name for name in row["names"])):
            fail("INVALID_REQUEST", "each properties row requires exactly path and nonempty names")
        if len(row["names"]) != len(set(row["names"])):
            fail("INVALID_REQUEST", "model.compare property names must be unique per path")
        properties.append({"path": NodePath.from_wire(row["path"]).as_dict(), "names": sorted(row["names"])})
    if len({path_key(row["path"]) for row in properties}) != len(properties):
        fail("INVALID_REQUEST", "model.compare scope contains duplicate property paths")
    metrics = []
    for row in metrics_raw:
        if not isinstance(row, Mapping) or set(row) != {"path", "names"} or not isinstance(row["names"], list) or not row["names"]:
            fail("INVALID_REQUEST", "each metrics row requires exactly path and nonempty names")
        names = row["names"]
        if not all(isinstance(name, str) and name in _METRICS for name in names):
            fail("INVALID_REQUEST", "model.compare requested an unsupported named metric")
        if len(names) != len(set(names)):
            fail("INVALID_REQUEST", "model.compare metric names must be unique per path")
        metrics.append({"path": NodePath.from_wire(row["path"]).as_dict(), "names": sorted(names)})
    if len({path_key(row["path"]) for row in metrics}) != len(metrics):
        fail("INVALID_REQUEST", "model.compare scope contains duplicate metric paths")
    if mode == "paths" and not (paths or properties or metrics):
        fail("INVALID_REQUEST", "paths mode requires at least one path, property or metric")
    budget = _parse_search_budget(scope.get("budget"))
    page_size = scope.get("page_size", 100)
    if type(page_size) is not int or not 1 <= page_size <= 500:
        fail("INVALID_REQUEST", "page_size must be an integer from 1 through 500")
    normalized = {"schema_version": 1, "mode": mode}
    if mode == "paths":
        normalized["paths"] = sorted(paths, key=path_key)
        normalized["properties"] = sorted(properties, key=lambda row: path_key(row["path"]))
    if metrics:
        normalized["metrics"] = sorted(metrics, key=lambda row: path_key(row["path"]))
    normalized["budget"] = budget
    normalized["page_size"] = page_size
    reader_scope = {"mode": mode, "paths": sorted(paths, key=path_key),
                    "properties": sorted(properties, key=lambda row: path_key(row["path"]))}
    return {"other_ref": other, "scope": normalized, "reader_scope": reader_scope,
            "budget": budget, "page_size": page_size}


def data_schema() -> dict[str, Any]:
    fields = ["schema_version", "operation", "status", "complete", "source_identity", "left_identity",
              "right_identity", "normalized_scope", "projection_schema", "comparison", "changes",
              "projections", "coverage", "errors", "observations", "provenance", "evidence"]
    properties = {name: {"type": "object"} for name in fields}
    properties.update({
        "schema_version": {"const": 1}, "operation": {"const": "model.compare"},
        "status": {"enum": ["SUCCEEDED", "INCOMPLETE", "UNKNOWN"]}, "complete": {"type": "boolean"},
        "projection_schema": {"const": SCHEMA}, "normalized_scope": {"type": "object"},
        "comparison": {"type": "object", "required": ["status", "equal", "complete"],
                       "properties": {"status": {"enum": ["EQUAL", "DIFFERENT", "INCOMPLETE"]},
                                      "equal": {"type": ["boolean", "null"]}, "complete": {"type": "boolean"}},
                       "additionalProperties": False},
        "changes": {"type": "array"}, "projections": {"type": "object"},
        "coverage": {"type": "array"}, "errors": {"type": "array"},
        "observations": {"type": "object"}, "provenance": {"type": "object"}, "evidence": {"type": "object"},
        "execution_state_unknown": {"type": "boolean"},
    })
    return {"type": "object", "required": fields, "properties": properties, "additionalProperties": False}


class _CapturedClient:
    def __init__(self, worker: "_CapturedWorker") -> None:
        self._capture = worker

    def model(self, tag: str):
        try:
            return self._capture.roots[tag]
        except KeyError as exc:
            fail("MODEL_IDENTITY_MISMATCH", "Reader attempted an unselected model tag")


class _CapturedWorker:
    """Reader adapter that can only resolve already admitted model handles."""
    def __init__(self, worker: Any, roots: Mapping[str, Any]) -> None:
        self._worker = worker
        self.roots = dict(roots)

    def client(self) -> _CapturedClient:
        return _CapturedClient(self)

    def describe_public(self, node: Any) -> Mapping[str, Any]:
        return self._worker.describe_public(node)


def _worker_observation(backend: Any) -> dict[str, Any]:
    worker = backend.worker
    meta_call = getattr(worker, "runtime_metadata", None)
    if not callable(meta_call):
        fail("WORKER_IDENTITY_UNAVAILABLE", "model.compare requires current Worker runtime identity")
    metadata = meta_call()
    generation = getattr(worker, "generation", None)
    identity = getattr(backend, "worker_identity", None)
    ledger = backend.service.ledger
    if (not isinstance(metadata, Mapping) or not isinstance(identity, Mapping)
            or type(generation) is not int or generation < 1
            or metadata.get("generation") != generation or type(metadata.get("generation")) is not int
            or metadata.get("connected") is not True
            or not isinstance(metadata.get("instance_id"), str) or not metadata.get("instance_id")
            or identity.get("worker_instance_id") != metadata.get("instance_id")
            or identity.get("connection_epoch") != generation
            or identity.get("server_instance_id") != ledger.server_instance_id):
        fail("WORKER_IDENTITY_UNAVAILABLE", "live Worker instance/generation does not match the managed session")
    return {"instance_id": metadata["instance_id"], "generation": generation,
            "connected": True, "server_instance_id": ledger.server_instance_id}


def _resolve_handles(backend: Any, refs: Mapping[str, ModelRef], expected_generation: int) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    roots: dict[str, Any] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for side in ("left", "right"):
        ref = refs[side]
        if ref.model_tag in roots:
            receipts[side] = receipts[next(k for k in receipts if refs[k].model_tag == ref.model_tag)]
            continue
        model = backend.worker.client().model(ref.model_tag)
        handle = getattr(model, "_handle", None)
        generation = getattr(model, "_generation", None)
        owner = getattr(model, "_worker", None)
        if (owner is not backend.worker or not isinstance(handle, str) or not handle
                or type(generation) is not int or generation != expected_generation):
            fail("MODEL_IDENTITY_MISMATCH", "Worker model resolution returned a stale or foreign model handle")
        roots[ref.model_tag] = model
        receipts[side] = {"model_tag": ref.model_tag, "model_handle": handle, "worker_generation": generation}
    if (refs["left"].model_tag != refs["right"].model_tag
            and receipts["left"]["model_handle"] == receipts["right"]["model_handle"]):
        fail("MODEL_IDENTITY_MISMATCH", "two different ModelRefs resolved to the same Worker model handle")
    return roots, receipts


def _clean_state_observation(backend: Any, ref: ModelRef) -> dict[str, Any]:
    state = backend.service.ledger._state_for(ref)
    binding = backend.model_project_binding(ref.as_dict())
    return {"model_ref": ref.as_dict(), "revision": state.revision, "dirty": state.dirty,
            "active_operation_id": state.active_operation_id, "fingerprint": state.fingerprint,
            "external_event_counter": state.external_event_counter,
            "observed_external_event_counter": state.observed_external_event_counter,
            "project_binding": binding}


def _identity_tag(reader: Reader, model: Any, ref: ModelRef) -> dict[str, Any]:
    root = {"segments": []}
    try:
        desc = reader.descriptor(model, root)
    except BudgetStop:
        reader.error(root, "identity:model_tag", BudgetStop("max_rpc"), status="TRUNCATED")
        return {"verified": False, "budget_exhausted": True,
                "reason": "budget exhausted before model tag verification"}
    rows = reader.methods(desc, "tag", ())
    if ("com.comsol.model.ModelEntity" not in desc["interfaces"]
            or len(rows) != 1 or rows[0]["returns"] != _STRING):
        fail("MODEL_IDENTITY_MISMATCH", "resolved model handle has no exact public ModelEntity.tag() identity getter")
    try:
        value = reader.scalar(model, desc, root, "identity:model_tag", "tag", returns={_STRING})
    except BudgetStop:
        reader.error(root, "identity:model_tag", BudgetStop("max_rpc"), status="TRUNCATED")
        return {"verified": False, "budget_exhausted": True,
                "reason": "budget exhausted before model tag readback"}
    if value is _MISSING:
        if reader.unknown:
            raise UnknownObservationStop()
        fail("MODEL_IDENTITY_MISMATCH", "resolved model handle could not prove its exact public model tag")
    if value.get("kind") != "value" or value.get("value", {}).get("data") != ref.model_tag:
        fail("MODEL_IDENTITY_MISMATCH", "resolved model handle public tag differs from its exact ModelRef")
    return {"verified": True, "model_tag": ref.model_tag, "typed_value": value}


def _typed_null(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("kind") == "null":
            return True
        return any(_typed_null(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_typed_null(item) for item in value)
    return False


def _mark_null_gaps(reader: Reader, projection: dict[str, Any]) -> None:
    for node in projection.get("nodes", []):
        for field, value in node.get("fields", {}).items():
            if _typed_null(value):
                row = {"side": reader.role, "path": node["path"], "field": field + ".null_validation",
                       "status": "UNREADABLE", "getter": None, "reason": "null is not a comparable semantic value"}
                reader.coverage.append(row)
                reader.errors.append({"side": reader.role, "stage": "typed_validation", "path": node["path"],
                                      "field": field, "code": "NULL_GETTER",
                                      "message": "null is not a comparable semantic value", "dispatched": True,
                                      "mutation_possible": False})


def _add_metrics(reader: Reader, projection: dict[str, Any], model: Any, scope: Mapping[str, Any]) -> None:
    for row in scope.get("metrics", []):
        path = row["path"]
        try:
            reader.budget.take(node=True)
            node = reader.resolve(model, path)
            desc = reader.descriptor(node, path)
        except BudgetStop:
            reader.error(path, "metrics", BudgetStop("max_nodes_or_rpc"), status="TRUNCATED")
            break
        except UnknownObservationStop:
            raise
        except Exception as exc:
            reader.error(path, "metrics", exc, dispatched=False)
            if reader.unknown:
                raise UnknownObservationStop() from exc
            continue
        for name in row["names"]:
            field = "metric:" + name
            interface, method, params, returns, unit_getter = _METRICS[name]
            if "com.comsol.model." + interface not in desc["interfaces"]:
                reader.error(path, field, ExecutionContractError("API_UNSUPPORTED", "named metric requires its exact public typed receiver"), status="UNSUPPORTED")
                continue
            try:
                value = reader.scalar(node, desc, path, field, method, params, (), {returns})
                unit = None
                if unit_getter:
                    unit = reader.scalar(node, desc, path, field + ".unit", unit_getter, returns={_STRING})
            except BudgetStop:
                reader.error(path, field, BudgetStop("max_rpc"), status="TRUNCATED")
                return
            if reader.unknown:
                raise UnknownObservationStop()
            if value is _MISSING or _typed_null(value):
                if value is not _MISSING:
                    reader.error(path, field, ExecutionContractError("NULL_GETTER", "named metric getter returned null"), dispatched=True)
                continue
            # Count and dimension metrics are explicitly dimensionless. Do
            # not leave the unit field implicit for a numeric typed value.
            payload: dict[str, Any] = {"metric": name, "value": value, "unit": "1"}
            if unit_getter:
                if unit is _MISSING or _typed_null(unit) or not isinstance(unit.get("value", {}).get("data"), str) or not unit["value"]["data"].strip():
                    reader.error(path, field + ".unit", ExecutionContractError("API_UNSUPPORTED", "metric has no valid public unit observation"), status="UNSUPPORTED")
                    continue
                payload["unit"] = unit["value"]["data"]
            try:
                reader.record(path, field, payload)
            except BudgetStop:
                reader.error(path, field, BudgetStop("serialized_projection_cap"), status="TRUNCATED")
                return
            if reader.unknown:
                raise UnknownObservationStop()
    _mark_null_gaps(reader, projection)
    projection["nodes"] = [reader.nodes[key] for key in sorted(reader.nodes)]
    projection["coverage"] = reader.coverage
    projection["errors"] = reader.errors
    projection["unknown"] = reader.unknown
    projection["complete"] = all(row["status"] in {"VERIFIED", "NOT_APPLICABLE"} for row in reader.coverage)


def _projection_payload(reader: Reader, model: Any, ref: ModelRef, normalized_scope: Mapping[str, Any],
                        budget: Budget, page_size: int) -> tuple[dict[str, Any], dict[str, Any]]:
    reader_scope = {"mode": normalized_scope["mode"],
                    "paths": normalized_scope.get("paths", []),
                    "properties": normalized_scope.get("properties", [])}
    reader.scope = reader_scope
    projection = reader.project()
    if reader.unknown:
        return reader, projection
    _add_metrics(reader, projection, model, normalized_scope)
    return reader, projection


def _projection_stable(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool | None:
    try:
        if any(row.get("status") == "TRUNCATED" for value in (left, right)
               for row in value.get("coverage", [])):
            return None
        left_coverage = [{"path": row["path"], "field": row["field"], "status": row["status"], "getter": row.get("getter")}
                         for row in left.get("coverage", [])]
        right_coverage = [{"path": row["path"], "field": row["field"], "status": row["status"], "getter": row.get("getter")}
                          for row in right.get("coverage", [])]
        return (canonical(left.get("nodes", [])) == canonical(right.get("nodes", []))
                and canonical(left_coverage) == canonical(right_coverage))
    except (KeyError, TypeError, ValueError):
        return False


def _semantic_type_signatures(value: Any) -> tuple[tuple[str, str, int], ...]:
    """Return exact Java/value-kind/rank signatures without comparing values."""
    found: set[tuple[str, str, int]] = set()
    if isinstance(value, Mapping):
        java_type = value.get("java_type")
        typed = value.get("value")
        if (value.get("kind") == "value" and isinstance(java_type, str)
                and isinstance(typed, Mapping) and isinstance(typed.get("kind"), str)
                and isinstance(typed.get("shape"), list)):
            found.add((java_type, typed["kind"], len(typed["shape"])))
        signature = value.get("java_signature")
        kind, shape = value.get("kind"), value.get("shape")
        if (isinstance(signature, str) and isinstance(kind, str) and isinstance(shape, list)):
            found.add((signature, kind, len(shape)))
        for item in value.values():
            found.update(_semantic_type_signatures(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_semantic_type_signatures(item))
    return tuple(sorted(found))


def _property_type_label(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    typed = value.get("value_type")
    if not isinstance(typed, Mapping) or typed.get("kind") != "value":
        return None
    payload = typed.get("value")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    return data if isinstance(data, str) else None


def _cross_side_type_mismatches(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    """Find shared semantic fields whose public typed contracts disagree."""
    left_nodes = {path_key(node["path"]): node for node in left.get("nodes", []) if isinstance(node, Mapping) and isinstance(node.get("path"), Mapping)}
    right_nodes = {path_key(node["path"]): node for node in right.get("nodes", []) if isinstance(node, Mapping) and isinstance(node.get("path"), Mapping)}
    mismatches: list[tuple[dict[str, Any], str, str]] = []
    for key in left_nodes.keys() & right_nodes.keys():
        left_node, right_node = left_nodes[key], right_nodes[key]
        path = left_node["path"]
        left_fields = left_node.get("fields", {})
        right_fields = right_node.get("fields", {})
        left_interfaces = left_fields.get("public_interfaces")
        right_interfaces = right_fields.get("public_interfaces")
        if left_interfaces != right_interfaces:
            mismatches.append((path, "public_interfaces", "same path resolved to different public typed receiver interfaces"))
        for field in left_fields.keys() & right_fields.keys():
            left_value, right_value = left_fields[field], right_fields[field]
            left_signatures = _semantic_type_signatures(left_value)
            right_signatures = _semantic_type_signatures(right_value)
            if left_signatures and right_signatures and left_signatures != right_signatures:
                mismatches.append((path, field, "same semantic field has different Java/value-kind/rank contracts"))
                continue
            if (field.startswith("property:")
                    and _property_type_label(left_value) is not None
                    and _property_type_label(right_value) is not None
                    and _property_type_label(left_value) != _property_type_label(right_value)):
                mismatches.append((path, field, "same property has different authoritative COMSOL value types"))
    unique: dict[tuple[str, str], tuple[dict[str, Any], str, str]] = {}
    for path, field, reason in mismatches:
        unique[(path_key(path), field)] = (path, field, reason)
    return list(unique.values())


def _add_type_gap(projection: dict[str, Any], side: str, path: Mapping[str, Any], field: str, reason: str) -> None:
    gap_field = field + ".type_compatibility"
    if any(row.get("path") == path and row.get("field") == gap_field for row in projection.get("coverage", [])):
        return
    projection["coverage"].append({"side": side, "path": path, "field": gap_field,
                                   "status": "UNSUPPORTED", "getter": None, "reason": reason})
    projection["errors"].append({"side": side, "stage": "typed_comparison", "path": path,
                                 "field": gap_field, "code": "TYPE_MISMATCH", "message": reason,
                                 "dispatched": False, "mutation_possible": False})
    projection["complete"] = False


def _typed_compatibility_gaps(projections: Mapping[str, dict[str, Any]],
                              repeated: Mapping[str, dict[str, Any]]) -> None:
    mismatches = _cross_side_type_mismatches(projections["left"], projections["right"])
    for path, field, reason in mismatches:
        for side in ("left", "right"):
            _add_type_gap(projections[side], side, path, field, reason)
            _add_type_gap(repeated[side], side, path, field, reason)


def _string_scalar(value: Any) -> str | None:
    if not isinstance(value, Mapping) or value.get("kind") != "value":
        return None
    typed = value.get("value")
    if not isinstance(typed, Mapping) or typed.get("kind") != "string":
        return None
    data = typed.get("data")
    return data if isinstance(data, str) else None


def _apply_unit_compatibility(normalized_scope: Mapping[str, Any], projections: Mapping[str, dict[str, Any]]) -> None:
    for row in normalized_scope.get("metrics", []):
        if "geometry.bounding_box" not in row["names"]:
            continue
        key = path_key(row["path"])
        left_node = next((node for node in projections["left"].get("nodes", []) if path_key(node.get("path")) == key), None)
        right_node = next((node for node in projections["right"].get("nodes", []) if path_key(node.get("path")) == key), None)
        left_metric = (left_node or {}).get("fields", {}).get("metric:geometry.bounding_box")
        right_metric = (right_node or {}).get("fields", {}).get("metric:geometry.bounding_box")
        left_unit = left_metric.get("unit") if isinstance(left_metric, Mapping) else None
        right_unit = right_metric.get("unit") if isinstance(right_metric, Mapping) else None
        if isinstance(left_unit, str) and isinstance(right_unit, str) and left_unit != right_unit:
            for side in ("left", "right"):
                projection = projections[side]
                projection["coverage"].append({"side": side, "path": row["path"],
                    "field": "metric:geometry.bounding_box.unit_compatibility", "status": "UNSUPPORTED",
                    "getter": None, "reason": "bounding-box units differ and no conversion is authorized"})
                projection["errors"].append({"side": side, "stage": "unit_comparison", "path": row["path"],
                    "field": "metric:geometry.bounding_box.unit_compatibility", "code": "UNIT_MISMATCH",
                    "message": "bounding-box values with different units are not comparable", "dispatched": False,
                    "mutation_possible": False})
                projection["complete"] = False
    # Full-model mode also observes GeomSequence.getBoundingBox() as semantic
    # metadata. Pair it with the same receiver's public lengthUnit() result so
    # a unit difference can never be presented as a numeric DIFFERENT result.
    if normalized_scope.get("mode") == "model":
        left_nodes = {path_key(node["path"]): node for node in projections["left"].get("nodes", [])}
        right_nodes = {path_key(node["path"]): node for node in projections["right"].get("nodes", [])}
        for key in left_nodes.keys() & right_nodes.keys():
            left_fields = left_nodes[key].get("fields", {})
            right_fields = right_nodes[key].get("fields", {})
            if "metadata:getBoundingBox" not in left_fields or "metadata:getBoundingBox" not in right_fields:
                continue
            left_unit = _string_scalar(left_fields.get("metadata:lengthUnit"))
            right_unit = _string_scalar(right_fields.get("metadata:lengthUnit"))
            if isinstance(left_unit, str) and isinstance(right_unit, str) and left_unit != right_unit:
                path = left_nodes[key]["path"]
                for side in ("left", "right"):
                    projection = projections[side]
                    field = "metadata:getBoundingBox.unit_compatibility"
                    if any(row.get("path") == path and row.get("field") == field for row in projection.get("coverage", [])):
                        continue
                    projection["coverage"].append({"side": side, "path": path, "field": field,
                        "status": "UNSUPPORTED", "getter": None,
                        "reason": "bounding-box units differ and no conversion is authorized"})
                    projection["errors"].append({"side": side, "stage": "unit_comparison", "path": path,
                        "field": field, "code": "UNIT_MISMATCH",
                        "message": "bounding-box values with different units are not comparable",
                        "dispatched": False, "mutation_possible": False})
                    projection["complete"] = False


def _apply_unit_compatibility_readback(normalized_scope: Mapping[str, Any], initial: Mapping[str, dict[str, Any]],
                                       repeated: Mapping[str, dict[str, Any]]) -> None:
    # A unit mismatch is itself a scope gap. Apply the same gap to each stable
    # pass so unit incompatibility cannot masquerade as observation drift.
    _apply_unit_compatibility(normalized_scope, initial)
    _apply_unit_compatibility(normalized_scope, repeated)


def _unique_refs(refs: Mapping[str, ModelRef]) -> list[ModelRef]:
    out = []
    for side in ("left", "right"):
        if all(refs[side] != item for item in out):
            out.append(refs[side])
    return out


def _root_identity_stable(before: Mapping[str, Any], repeated: Mapping[str, Any]) -> bool:
    for side in ("left", "right"):
        first, again = before.get(side, {}), repeated.get(side, {})
        if first.get("verified") is True and again.get("verified") is True:
            if canonical(first.get("typed_value")) != canonical(again.get("typed_value")):
                return False
        elif first.get("budget_exhausted") is True and again.get("budget_exhausted") is True:
            # The identity could not be re-read within the caller's budget;
            # report an incomplete projection rather than evidence of drift.
            continue
        else:
            return False
    return True


def _guard_unknown(backend: Any, refs: Mapping[str, ModelRef]) -> list[dict[str, Any]]:
    errors = []
    for ref in _unique_refs(refs):
        try:
            state = backend.service.ledger._state_for(ref)
            state.dirty = True
            state.fingerprint = None
        except Exception as exc:
            errors.append({"model_ref": ref.as_dict(), "code": getattr(exc, "code", "MODEL_IDENTITY_MISMATCH"),
                           "message": str(exc), "stage": "unknown_state_guard"})
    return errors


def _side_identity(ref: ModelRef, project_id: str, before: Mapping[str, Any], after: Mapping[str, Any] | None,
                   handle_before: Mapping[str, Any], handle_after: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"model_ref": ref.as_dict(), "project_id": project_id,
            "revision_before": before.get("revision"), "revision_after": after.get("revision") if after else None,
            "project_binding_before": before.get("project_binding"),
            "project_binding_after": after.get("project_binding") if after else None,
            "worker_handle_before": dict(handle_before), "worker_handle_after": dict(handle_after) if handle_after else None}


def _base_data(refs: Mapping[str, ModelRef], project_id: str, scope: Mapping[str, Any], status: str,
               *, complete: bool = False, errors: list[dict[str, Any]] | None = None,
               coverage: list[dict[str, Any]] | None = None, observations: Mapping[str, Any] | None = None,
               projections: Mapping[str, Any] | None = None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    comparison_status = "INCOMPLETE"
    return {"schema_version": 1, "operation": "model.compare", "status": status, "complete": bool(complete),
            "source_identity": {"left_model_ref": refs["left"].as_dict(), "right_model_ref": refs["right"].as_dict(),
                                "project_id": project_id},
            "left_identity": {"model_ref": refs["left"].as_dict(), "project_id": project_id},
            "right_identity": {"model_ref": refs["right"].as_dict(), "project_id": project_id},
            "normalized_scope": dict(scope), "projection_schema": SCHEMA,
            "comparison": {"status": comparison_status, "equal": None, "complete": False},
            "changes": [], "projections": dict(projections or {}), "coverage": list(coverage or []),
            "errors": list(errors or []), "observations": dict(observations or {}),
            "provenance": {"model_tags_and_handles_are_identity_only": True,
                           "model_file_hashes_used_as_equality": False,
                           "comparison_uses_typed_semantic_values": True},
            "evidence": dict(evidence or {})}


def _run_compare(backend: Any, refs: Mapping[str, ModelRef], project_id: str,
                 normalized: Mapping[str, Any], admission: Mapping[str, Any]) -> dict[str, Any]:
    ledger = backend.service.ledger
    unique = _unique_refs(refs)
    before_states: dict[ModelRef, dict[str, Any]] = {}
    before_snapshots: dict[ModelRef, dict[str, Any]] = {}
    for ref in unique:
        state = _clean_state_observation(backend, ref)
        if state["dirty"] or state["active_operation_id"] is not None:
            fail("EXECUTION_STATE_UNKNOWN", "a compared ModelRef is dirty or already busy")
        before_states[ref] = state

    # The READ service preflight takes its own snapshot of the primary model
    # before entering this callback. Revalidate both Worker-backed handles
    # after that observation and before any requested projection getter.
    worker_before = _worker_observation(backend)
    roots_before, handles_before = _resolve_handles(backend, refs, worker_before["generation"])
    if worker_before != admission["worker"] or handles_before != admission["handles"]:
        fail("EXECUTION_STATE_UNKNOWN", "Worker identity or model handle changed after the model.compare admission preflight")

    for ref in unique:
        state = before_states[ref]
        snapshot = backend.service._snapshot(ref.model_tag)
        ledger.observe_engine_state(ref, external_event_counter=snapshot["external_event_counter"],
                                    fingerprint=snapshot["fingerprint"])
        checked = _clean_state_observation(backend, ref)
        if (checked["dirty"] or checked["active_operation_id"] is not None
                or checked["revision"] != state["revision"]
                or snapshot["external_event_counter"] != state["external_event_counter"]
                or snapshot["fingerprint"] != state["fingerprint"]):
            fail("EXECUTION_STATE_UNKNOWN", "a compared model changed before the first requested getter")
        before_states[ref] = checked
        before_snapshots[ref] = snapshot
    budget = Budget(normalized["budget"])
    page_size = normalized["page_size"]
    initial_readers: dict[str, Reader] = {}
    root_identity_before: dict[str, Any] = {}
    for side in ("left", "right"):
        captured = _CapturedWorker(backend.worker, roots_before)
        reader = Reader(captured, refs[side].model_tag, normalized["reader_scope"], budget, page_size,
                        side + "_initial", stop_on_unknown=True)
        initial_readers[side] = reader
        root_identity_before[side] = _identity_tag(reader, roots_before[refs[side].model_tag], refs[side])
    projections: dict[str, dict[str, Any]] = {}
    for side in ("left", "right"):
        reader, projection = _projection_payload(initial_readers[side], roots_before[refs[side].model_tag], refs[side],
                                                 normalized["scope"], budget, page_size)
        projections[side] = projection
        if reader.unknown:
            raise UnknownObservationStop("unresolved Worker observation during first projection")

    worker_mid = _worker_observation(backend)
    if worker_mid != worker_before:
        fail("EXECUTION_STATE_UNKNOWN", "Worker identity changed between the first projection and readback")
    roots_mid, handles_mid = _resolve_handles(backend, refs, worker_mid["generation"])
    if handles_mid != handles_before:
        fail("EXECUTION_STATE_UNKNOWN", "model handle or Worker generation changed before requested-scope readback")

    repeated: dict[str, dict[str, Any]] = {}
    root_identity_repeat: dict[str, Any] = {}
    for side in ("left", "right"):
        captured = _CapturedWorker(backend.worker, roots_mid)
        reader = Reader(captured, refs[side].model_tag, normalized["reader_scope"], budget, page_size,
                        side + "_readback", stop_on_unknown=True)
        root_identity_repeat[side] = _identity_tag(reader, roots_mid[refs[side].model_tag], refs[side])
        if not root_identity_repeat[side]["verified"]:
            repeated[side] = {"nodes": [], "coverage": reader.coverage, "errors": reader.errors,
                              "complete": False, "unknown": False}
            continue
        reader, projection = _projection_payload(reader, roots_mid[refs[side].model_tag], refs[side],
                                                 normalized["scope"], budget, page_size)
        repeated[side] = projection
        if reader.unknown:
            raise UnknownObservationStop("unresolved Worker observation during requested-scope readback")

    after_snapshots: dict[ModelRef, dict[str, Any]] = {}
    after_states: dict[ModelRef, dict[str, Any]] = {}
    for ref in unique:
        snapshot = backend.service._snapshot(ref.model_tag)
        ledger.observe_engine_state(ref, external_event_counter=snapshot["external_event_counter"],
                                    fingerprint=snapshot["fingerprint"])
        after_snapshots[ref] = snapshot
        after_states[ref] = _clean_state_observation(backend, ref)
    worker_after = _worker_observation(backend)
    roots_after, handles_after = _resolve_handles(backend, refs, worker_after["generation"])

    stable_identity = (worker_after == worker_before and handles_after == handles_mid
                       and handles_mid == handles_before
                       and _root_identity_stable(root_identity_before, root_identity_repeat))
    for ref in unique:
        stable_identity = stable_identity and (
            after_states[ref]["revision"] == before_states[ref]["revision"]
            and after_states[ref]["project_binding"] == before_states[ref]["project_binding"]
            and after_states[ref]["dirty"] is False
            and after_states[ref]["active_operation_id"] is None
            and after_snapshots[ref] == before_snapshots[ref]
            and after_states[ref]["fingerprint"] == before_states[ref]["fingerprint"]
            and after_states[ref]["external_event_counter"] == before_states[ref]["external_event_counter"]
        )
    _apply_unit_compatibility_readback(normalized["scope"], projections, repeated)
    _typed_compatibility_gaps(projections, repeated)
    pass_stability = [_projection_stable(projections[side], repeated.get(side, {})) for side in ("left", "right")]
    stable_readback = False if False in pass_stability else None if None in pass_stability else True
    stable = stable_identity and stable_readback is not False
    changes = compare_projections(projections["left"], projections["right"])
    complete = (stable and stable_readback is True and projections["left"].get("complete") is True
                and projections["right"].get("complete") is True
                and repeated.get("left", {}).get("complete") is True
                and repeated.get("right", {}).get("complete") is True)
    status = "SUCCEEDED" if complete else "INCOMPLETE"
    if not stable:
        status = "UNKNOWN"
    comparison_status = ("EQUAL" if complete and not changes else "DIFFERENT" if complete
                         else "INCOMPLETE")
    data = _base_data(refs, project_id, normalized["scope"], status, complete=complete,
                      coverage=(projections["left"].get("coverage", []) + projections["right"].get("coverage", [])
                                + repeated.get("left", {}).get("coverage", []) + repeated.get("right", {}).get("coverage", [])),
                      errors=(projections["left"].get("errors", []) + projections["right"].get("errors", [])
                              + repeated.get("left", {}).get("errors", []) + repeated.get("right", {}).get("errors", [])),
                      observations={"worker_before": worker_before, "worker_after": worker_after,
                                    "root_identity_before": root_identity_before, "root_identity_readback": root_identity_repeat,
                                    "before": {ref.model_tag: {"snapshot": before_snapshots[ref], "state": before_states[ref]} for ref in unique},
                                    "after": {ref.model_tag: {"snapshot": after_snapshots[ref], "state": after_states[ref]} for ref in unique},
                                    "requested_scope_readback_stable": stable_readback,
                                    "identity_revision_counter_handle_stable": stable_identity,
                                    "atomic_native_snapshot_proven": False},
                      projections={side: {"nodes": projections[side].get("nodes", []),
                                          "readback_nodes": repeated.get(side, {}).get("nodes", []),
                                          "complete": projections[side].get("complete") is True,
                                          "readback_complete": repeated.get(side, {}).get("complete") is True}
                                   for side in ("left", "right")},
                      evidence={"work_budget": budget.as_dict(), "read_policy": "one serialized managed READ operation; no WRITE ticket",
                                "write_tickets": 0, "model_loads_or_temporary_models": 0,
                                "projection_passes": 4, "model_handle_resolutions_before_getters": len(roots_before),
                                "no_retry_after_unknown": True})
    data["left_identity"] = _side_identity(refs["left"], project_id, before_states[refs["left"]],
                                           after_states[refs["left"]], handles_before["left"], handles_after["left"])
    data["right_identity"] = _side_identity(refs["right"], project_id, before_states[refs["right"]],
                                            after_states[refs["right"]], handles_before["right"], handles_after["right"])
    data["comparison"] = {"status": comparison_status, "equal": (comparison_status == "EQUAL" if complete else None),
                           "complete": bool(complete)}
    data["changes"] = changes
    data["provenance"].update({"readback_projection_values_required_stable": True,
                                "structure_and_key_properties_are_semantic": True,
                                "named_metrics": sorted(_METRICS)})
    if not stable:
        guard_errors = _guard_unknown(backend, refs)
        data["errors"].extend(guard_errors)
        data["execution_state_unknown"] = True
        return {"success": False, "data": data, "execution_state_unknown": True,
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "model.compare identity, revision, counter, handle or requested projection drifted", "safe_retry": False}}
    return {"success": True, "data": data}


def invoke_compare(backend: Any, arguments: Mapping[str, Any], execution: Mapping[str, Any], operation_id: str) -> dict[str, Any]:
    if backend.service is None or backend.worker is None:
        fail("ENGINE_UNRESPONSIVE", "model.compare requires an existing connected Worker")
    if (not isinstance(execution.get("project_id"), str) or not execution["project_id"]
            or not isinstance(execution.get("session_id"), str) or not execution["session_id"]
            or execution["session_id"] != backend.service.ledger.session_id):
        fail("MODEL_IDENTITY_MISMATCH", "model.compare requires exact outer project/session identity")
    if "expected_revision" in execution:
        fail("INVALID_REQUEST", "model.compare is READ and does not accept expected_revision")
    for field in IDENTITY_FIELDS:
        if field in arguments and arguments[field] != execution.get(field):
            fail("MODEL_IDENTITY_MISMATCH", f"model.compare body {field} conflicts with the execution envelope")
    primary = _strict_ref_mapping(execution.get("model_ref"), "execution.model_ref")
    normalized = normalize(arguments)
    other = normalized["other_ref"]
    if (primary.session_id != execution["session_id"] or other.session_id != execution["session_id"]
            or primary.server_instance_id != backend.service.ledger.server_instance_id
            or other.server_instance_id != backend.service.ledger.server_instance_id):
        fail("MODEL_IDENTITY_MISMATCH", "both ModelRefs must belong to this exact control session and server epoch")
    refs = {"left": primary, "right": other}
    if "inspect" not in backend.service.ledger.permissions:
        fail("PERMISSION_DENIED", "model.compare requires inspect authorization for both models")
    bindings = {}
    for side in ("left", "right"):
        state = backend.service.ledger._state_for(refs[side])
        if state.dirty or state.active_operation_id is not None:
            fail("EXECUTION_STATE_UNKNOWN", f"model.compare {side} ModelRef is dirty or busy")
        binding = backend.model_project_binding(refs[side].as_dict())
        if binding != {"project_id": execution["project_id"], "attribution": "PROJECT_BOUND"}:
            fail("PROJECT_IDENTITY_MISMATCH", f"model.compare {side} ModelRef lacks the exact persisted project binding")
        bindings[side] = binding
    # ExecutionService's normal READ gate takes a model snapshot before it
    # calls the projection callback. Admit both live Worker handles first, so
    # that initial observation cannot run against an unverified second ref.
    admission_worker = _worker_observation(backend)
    _admission_roots, admission_handles = _resolve_handles(backend, refs, admission_worker["generation"])
    admission = {"worker": admission_worker, "handles": admission_handles}
    observed: dict[str, Any] = {}

    def callback(_):
        try:
            result = _run_compare(backend, refs, execution["project_id"], normalized, admission)
        except Exception as exc:
            code = getattr(exc, "code", "EXECUTION_STATE_UNKNOWN")
            message = str(exc) or type(exc).__name__
            guard_errors = _guard_unknown(backend, refs)
            budget = Budget(normalized["budget"])
            data = _base_data(refs, execution["project_id"], normalized["scope"], "UNKNOWN",
                              errors=[{"stage": "observation", "code": code, "message": message,
                                       "cause_type": type(exc).__name__, "dispatched": True,
                                       "mutation_possible": False}, *guard_errors],
                              observations={"identity_project_bindings_before": bindings},
                              evidence={"work_budget": budget.as_dict(), "read_policy": "one serialized managed READ operation; no WRITE ticket",
                                        "no_retry_after_unknown": True})
            data["execution_state_unknown"] = True
            result = {"success": False, "data": data, "execution_state_unknown": True,
                      "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "model.compare observation did not remain safely resolvable", "safe_retry": False}}
        observed["result"] = result
        return result

    result = backend.service.execute_legacy(
        "model_compare", callback, {"other_model_ref": arguments.get("other_model_ref"), "scope": arguments.get("scope", {})},
        model_ref=primary, expected_revision=None, request_id=execution.get("request_id"),
        session_id=execution["session_id"], effect="inspect")
    data = result.get("data") if isinstance(result, Mapping) else None
    if isinstance(data, Mapping) and data.get("execution_state_unknown") is True:
        try:
            backend.persist()
        except Exception as exc:
            mutable = dict(data)
            mutable.setdefault("errors", []).append({"stage": "terminal_persistence", "code": "EXECUTION_STATE_UNKNOWN",
                                                      "message": str(exc), "cause_type": type(exc).__name__,
                                                      "dispatched": False, "mutation_possible": False})
            mutable["status"] = "UNKNOWN"
            mutable["complete"] = False
            mutable["comparison"] = {"status": "INCOMPLETE", "equal": None, "complete": False}
            result = {**result, "success": False, "data": mutable, "execution_state_unknown": True,
                      "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "model.compare dirty-state persistence failed", "safe_retry": False}}
    return result
