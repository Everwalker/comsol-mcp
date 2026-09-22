"""COMSOL model-definition probe operations.

The probe API is deliberately kept separate from ``_g3_results``.  A model
probe owns its expression and (when configured) a real results table; a table
history request reads that association and never prepares a result as a side
effect.  All mutating paths validate their complete request before the first
``create``/``set``/selection/genResult call and stop at the first post-dispatch
failure.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_contract import NodePath
from ._g2_engine import _call, _model as bound_model
from ._g3_common import (
    BINDABLE_SELECTION_KINDS,
    apply_local_selection,
    call_probe,
    is_typed_value,
    node_type,
    property_definition,
    require_bool,
    require_entity_id_array,
    require_int,
    require_mapping,
    require_number,
    require_string,
    resolve_path,
    selection_state,
    tag_list,
    typed_value_from_wire,
    validate_selection_spec,
    validate_tag,
)


# COMSOL 6.4's native type name for a model-global probe is
# ``GlobalVariable``.  ``Global`` is intentionally absent: accepting it would
# make a successful-looking request create the wrong API subtype.
_TYPE_MAP: dict[str, str] = {
    "DomainProbe": "Domain",
    "BoundaryProbe": "Boundary",
    "EdgeProbe": "Edge",
    "PointProbe": "Point",
    "GlobalProbe": "GlobalVariable",
    "Domain": "Domain",
    "Boundary": "Boundary",
    "Edge": "Edge",
    "Point": "Point",
    "GlobalVariable": "GlobalVariable",
}
SUPPORTED_PROBE_TYPES = frozenset(_TYPE_MAP)

# These names are the documented model-probe properties used by the COMSOL
# 6.4 API.  The list is finite so a typo cannot be reported as an applied
# property.  ``selection`` and result-preparation controls are handled below.
_COMMON_PROPERTIES = frozenset(
    {
        "descr", "expr", "frame", "intorder", "intsurface", "intvolume",
        "method", "points", "probename", "table", "type", "unit", "window",
        "coord", "coords", "coordinates", "coord1", "coord2", "coord3",
        "x", "y", "z", "point", "solnum",
    }
)
_CONTROL_KEYS = frozenset({
    "properties", "selection", "solution", "gen_result", "generate_result",
    "prepare_result", "component",
})
_HISTORY_SOLUTION_KEYS = frozenset({
    "dataset", "solution", "inner", "outer", "time", "frequency", "parameters",
})
_CURSOR_VERSION = 1


def _pre_refusal(code: str, message: str, *, details: Mapping[str, Any] | None = None) -> PreWriteRefusal:
    return PreWriteRefusal(code, message, details=details)


def _as_list(value: Any) -> list[Any] | None:
    """Convert Java/Python array-like results without accepting mappings."""
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    """Convert Java scalar/array values to bounded JSON-shaped values."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL returned a non-finite value")
        return value
    try:
        number = float(value)
    except Exception:
        return str(value)
    if not math.isfinite(number):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL returned a non-finite value")
    return number


def _equal_value(actual: Any, requested: Any) -> bool:
    if isinstance(actual, (list, tuple)) and isinstance(requested, (list, tuple)):
        return len(actual) == len(requested) and all(_equal_value(a, b) for a, b in zip(actual, requested))
    if isinstance(actual, Mapping) and isinstance(requested, Mapping):
        return set(actual) == set(requested) and all(_equal_value(actual[k], requested[k]) for k in actual)
    if isinstance(actual, bool) or isinstance(requested, bool):
        return type(actual) is type(requested) and actual == requested
    if isinstance(actual, (int, float)) and isinstance(requested, (int, float)):
        return math.isclose(float(actual), float(requested), rel_tol=1e-9, abs_tol=1e-12)
    return actual == requested or str(actual) == str(requested)


def _safe_tags(container: Any) -> list[str]:
    """Read the engine's own tag list, refusing malformed readback."""
    try:
        return tag_list(container)
    except AttributeError as exc:
        raise ExecutionContractError("API_UNSUPPORTED", "probe container does not expose tags()") from exc


def _optional_call(node: Any, method: str, *args: Any) -> tuple[bool, Any, dict[str, Any] | None]:
    """Read an optional COMSOL method without turning absence into data."""
    probe = call_probe(node, method, *args)
    if probe["ok"]:
        return True, probe["value"], None
    return False, None, probe.get("error")


def _component_tags(model: Any) -> list[str]:
    try:
        model_node = _call(model, "modelNode")
        raw = _call(model_node, "tags")
    except Exception:
        return []
    values = _as_list(raw)
    if values is None or not all(isinstance(item, str) for item in values):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "modelNode().tags() returned malformed component tags")
    return [str(item) for item in values]


def _probe_containers(model: Any) -> list[tuple[str | None, Any]]:
    """Return global and component probe collections with their owner tags."""
    result: list[tuple[str | None, Any]] = []
    seen: set[int] = set()
    try:
        container = _call(model, "probe")
        if id(container) not in seen:
            result.append((None, container))
            seen.add(id(container))
    except Exception:
        pass
    for component in _component_tags(model):
        try:
            comp_node = _call(model, "component", component)
            container = _call(comp_node, "probe")
        except Exception:
            continue
        if id(container) not in seen:
            result.append((component, container))
            seen.add(id(container))
    if not result:
        raise ExecutionContractError("API_UNSUPPORTED", "model exposes no definition probe container")
    return result


def _probe_container(model: Any, comp_tag: str | None = None, type_id: str | None = None) -> Any:
    """Compatibility helper used by older callers and the live adapter."""
    if comp_tag is not None:
        component = validate_tag(comp_tag, "component")
        return _call(_call(model, "component", component), "probe")
    if type_id in {"GlobalProbe", "GlobalVariable"}:
        return _call(model, "probe")
    containers = _probe_containers(model)
    if len(containers) == 1:
        return containers[0][1]
    component_containers = [(owner, c) for owner, c in containers if owner is not None]
    if len(component_containers) == 1:
        return component_containers[0][1]
    if component_containers:
        raise ExecutionContractError(
            "AMBIGUOUS_NODE_PATH",
            "a spatial probe create request must name its component when multiple components expose probes",
        )
    return containers[0][1]


def _iter_targets(model: Any) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for component, container in _probe_containers(model):
        tags = _safe_tags(container)
        for tag in tags:
            node = None
            try:
                node = _call(container, "get", tag)
            except Exception:
                try:
                    node = getattr(container, "__call__")(tag)
                except Exception:
                    pass
            # A model-global probe can be stored in the root collection while
            # its real owner is recorded by ModelEntity.model(String).  Use
            # that native owner for typed paths and ambiguity checks; never
            # infer an owner merely from the collection in which the tag was
            # found.
            owner = component
            if owner is None and node is not None:
                owner = _read_model_owner(node)
            targets.append({"component": owner, "container": container, "tag": tag, "node": node})
    # COMSOL 6.4 lists a root probe that carries a native owner in both
    # ``model.probe()`` and ``model.component(<owner>).probe()``.  The engine
    # stores that probe once, so a typed path or a tag lookup must not read the
    # two views as two owners.  Rows that share (tag, component) are merged:
    # that pair can only repeat through this root/component mirror, because a
    # component container contributes at most one row per tag and a row found
    # in a *different* component keeps a different owner.  The merged row
    # records the mirror instead of hiding it.
    merged: list[dict[str, Any]] = []
    for row in targets:
        twin = next((existing for existing in merged
                     if existing["tag"] == row["tag"] and existing["component"] == row["component"]), None)
        if twin is None:
            merged.append(row)
            continue
        if twin.get("node") is None and row.get("node") is not None:
            twin["node"] = row["node"]
        twin.setdefault("mirrored_views", 1)
        twin["mirrored_views"] += 1
    return merged


def _canonical_probe_path(component: str | None, tag: str) -> dict[str, Any]:
    segments: list[dict[str, str]] = []
    if component is not None:
        segments.append({"collection": "component", "tag": component})
    segments.append({"collection": "probe", "tag": tag})
    return NodePath.from_wire({"segments": segments}, allow_empty=False).as_dict()


def _path_probe_parts(path: Any) -> tuple[dict[str, Any], str | None, str]:
    parsed = NodePath.from_wire(path, allow_empty=False)
    final = parsed.segments[-1]
    if final.collection != "probe" or not final.tag:
        raise _pre_refusal(
            "INVALID_NODE_PATH",
            "probe path must end in a collection+tag segment with collection='probe'",
        )
    components = [str(seg.tag) for seg in parsed.segments if seg.collection == "component"]
    if len(set(components)) > 1:
        raise _pre_refusal("INVALID_NODE_PATH", "probe path contains multiple component owners")
    return parsed.as_dict(), (components[0] if components else None), str(final.tag)


def _resolve_target(worker: Any, model_tag: str, arguments: Mapping[str, Any], *, require_node: bool = True) -> dict[str, Any]:
    model = bound_model(worker, model_tag)
    if arguments.get("path") is not None:
        canonical, component, tag = _path_probe_parts(arguments["path"])
        # Resolve the typed path through the allow-listed resolver first.  A
        # matching tag scan alone is not enough evidence for a NodePath.
        try:
            _canonical_from_engine, resolved_node = resolve_path(worker, model_tag, canonical, label="path")
        except PreWriteRefusal:
            # COMSOL stores a component-bound GlobalVariable in the root
            # ``model.probe()`` collection, so the structural path
            # ``component/<ctag>/probe/<tag>`` cannot be traversed literally.
            # Accept this one documented shape only when the engine's native
            # ModelEntity.model() readback proves the requested owner.
            resolved_node = None
            fallback_matches = [row for row in _iter_targets(model)
                                if row["tag"] == tag and row["component"] == component]
            if len(fallback_matches) == 1:
                resolved_node = fallback_matches[0].get("node")
            else:
                raise
        matches = [row for row in _iter_targets(model)
                   if row["tag"] == tag and row["component"] == component]
        if len(matches) != 1:
            if not matches:
                raise _pre_refusal("NODE_NOT_FOUND", f"probe path does not name an existing probe: {canonical}")
            raise _pre_refusal("AMBIGUOUS_NODE_PATH", f"probe path resolves to multiple probe owners: {canonical}")
        row = dict(matches[0])
        row["node"] = resolved_node if resolved_node is not None else row.get("node")
        row["path"] = canonical
        return row

    tag = validate_tag(arguments.get("tag"), "tag")
    component_value = arguments.get("component")
    component = validate_tag(component_value, "component") if component_value is not None else None
    matches = [row for row in _iter_targets(model)
               if row["tag"] == tag and (component is None or row["component"] == component)]
    if not matches:
        raise _pre_refusal("NODE_NOT_FOUND", f"probe {tag!r} does not exist")
    if len(matches) != 1:
        raise _pre_refusal(
            "AMBIGUOUS_NODE_PATH",
            f"probe {tag!r} exists in multiple owners; pass a typed path or component",
            details={"matches": [_canonical_probe_path(row["component"], tag) for row in matches]},
        )
    row = dict(matches[0])
    row["path"] = _canonical_probe_path(component, tag)
    if require_node and row.get("node") is None:
        raise ExecutionContractError("API_UNSUPPORTED", f"probe {tag!r} has no readable node accessor")
    return row


def _normalise_filter(arguments: Mapping[str, Any]) -> dict[str, Any]:
    value = arguments.get("filter", arguments)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "filter must be an object")
    unknown = set(value) - {"component", "tag", "type_id"}
    if unknown:
        raise ExecutionContractError("INVALID_REQUEST", f"filter has unsupported fields: {sorted(unknown)}")
    out: dict[str, Any] = {}
    if value.get("component") is not None:
        out["component"] = validate_tag(value["component"], "filter.component")
    if value.get("tag") is not None:
        out["tag"] = validate_tag(value["tag"], "filter.tag")
    if value.get("type_id") is not None:
        out["type_id"] = require_string(value["type_id"], "filter.type_id", max_length=64)
    return out


def _read_string(node: Any, name: str) -> str | None:
    ok, value, _error = _optional_call(node, "getString", name)
    if ok and value is not None:
        return str(value)
    return None


def _read_model_owner(node: Any) -> str | None:
    """Read the native ModelEntity owner set by ``model(String)``."""
    ok, value, _error = _optional_call(node, "model")
    if ok and value is not None:
        text = str(value).strip()
        return text or None
    return None


def probe_list(worker: Any, model_tag: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """List probes and report native COMSOL types and readable properties."""
    args = dict(arguments or {})
    filt = _normalise_filter(args)
    model = bound_model(worker, model_tag)
    items: list[dict[str, Any]] = []
    for row in _iter_targets(model):
        if filt.get("component") is not None and row["component"] != filt["component"]:
            continue
        if filt.get("tag") is not None and row["tag"] != filt["tag"]:
            continue
        node = row.get("node")
        native_type = node_type(node) if node is not None else None
        if filt.get("type_id") is not None and native_type != filt["type_id"]:
            continue
        expression = _read_string(node, "expr") if node is not None else None
        table = _read_string(node, "table") if node is not None else None
        item: dict[str, Any] = {
            "tag": row["tag"],
            "component": row["component"],
            "type_id": native_type,
            "native_type": native_type,
            "expression": expression,
            "table": table,
            "path": _canonical_probe_path(row["component"], row["tag"]),
        }
        items.append(item)
    return {
        "probes": items,
        "count": len(items),
        "tags": [item["tag"] for item in items],
        "readback": {"source": "engine.tags/get", "readable": True},
    }


def _normalise_solution(value: Any, label: str = "solution") -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be an object")
    unknown = set(value) - _HISTORY_SOLUTION_KEYS
    if unknown:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} has unsupported fields: {sorted(unknown)}")
    out = dict(value)
    if "dataset" not in out:
        raise ExecutionContractError("INVALID_REQUEST", f"{label}.dataset is required")
    out["dataset"] = require_string(out["dataset"], f"{label}.dataset")
    if "solution" in out:
        out["solution"] = require_string(out["solution"], f"{label}.solution")
    for key in ("inner", "outer"):
        if key not in out:
            continue
        choice = out[key]
        if isinstance(choice, str):
            if choice not in {"all", "first", "last"}:
                raise ExecutionContractError("INVALID_REQUEST", f"{label}.{key} has unsupported selector")
        elif isinstance(choice, (list, tuple)):
            out[key] = require_entity_id_array(choice, f"{label}.{key}")
        else:
            raise ExecutionContractError("INVALID_REQUEST", f"{label}.{key} must be all/first/last or entity ids")
    for key in ("time", "frequency"):
        if key not in out:
            continue
        values = out[key]
        if not isinstance(values, (list, tuple)):
            raise ExecutionContractError("INVALID_REQUEST", f"{label}.{key} must be an array")
        converted: list[dict[str, Any]] = []
        for index, quantity in enumerate(values):
            if not isinstance(quantity, Mapping) or set(quantity) != {"value", "unit"}:
                raise ExecutionContractError("INVALID_REQUEST", f"{label}.{key}[{index}] must contain value and unit")
            converted.append({
                "value": require_number(quantity["value"], f"{label}.{key}[{index}].value"),
                "unit": require_string(quantity["unit"], f"{label}.{key}[{index}].unit", max_length=64),
            })
        out[key] = converted
    if "parameters" in out:
        if not isinstance(out["parameters"], Mapping):
            raise ExecutionContractError("INVALID_REQUEST", f"{label}.parameters must be an object")
        out["parameters"] = dict(out["parameters"])
    return out


def _unwrap_property_value(value: Any, label: str) -> Any:
    if is_typed_value(value):
        return typed_value_from_wire(value, label)["data"]
    if isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a scalar/array or TypedValue")
    if isinstance(value, float) and not math.isfinite(value):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be finite")
    if isinstance(value, (list, tuple)):
        return [_unwrap_property_value(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if value is None:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} may not be null")
    return value


def _prepare_definition_unchecked(definition: Any, *, type_id: str | None = None) -> dict[str, Any]:
    raw = require_mapping(definition, "definition")
    controls: dict[str, Any] = {}
    if "properties" in raw:
        if set(raw) - (_CONTROL_KEYS | {"properties"}):
            unknown = sorted(set(raw) - (_CONTROL_KEYS | {"properties"}))
            raise ExecutionContractError("INVALID_REQUEST", f"definition has unsupported fields: {unknown}")
        properties = property_definition(raw["properties"], "definition.properties")
    else:
        properties = {}
        for name, value in raw.items():
            if name in _CONTROL_KEYS:
                controls[name] = value
            else:
                properties[name] = value
    for key in _CONTROL_KEYS:
        if key in raw:
            controls[key] = raw[key]
    unknown = sorted(set(properties) - _COMMON_PROPERTIES)
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"definition has unsupported probe properties: {unknown}; use a documented COMSOL property",
        )
    normalised: list[tuple[str, Any]] = []
    for name, value in properties.items():
        normalised.append((str(name), _unwrap_property_value(value, f"definition.{name}")))

    selection = controls.get("selection")
    if selection is not None:
        selection = validate_selection_spec(selection, allowed_kinds=BINDABLE_SELECTION_KINDS)
    solution_spec = controls.get("solution")
    if isinstance(solution_spec, Mapping):
        solution_spec = _normalise_solution(solution_spec, "definition.solution")
    elif solution_spec is not None:
        solution_spec = require_string(solution_spec, "definition.solution")

    generate = False
    gen_value = controls.get("gen_result", controls.get("generate_result", controls.get("prepare_result")))
    solver: str | None = None
    if gen_value is not None:
        if isinstance(gen_value, bool):
            generate = require_bool(gen_value, "definition.gen_result")
            solver = solution_spec if isinstance(solution_spec, str) else None
        elif isinstance(gen_value, str):
            solver = require_string(gen_value, "definition.gen_result")
            generate = True
        elif isinstance(gen_value, Mapping):
            if set(gen_value) - {"solution"}:
                raise ExecutionContractError("INVALID_REQUEST", "definition.gen_result has unsupported fields")
            solver = require_string(gen_value["solution"], "definition.gen_result.solution") if gen_value.get("solution") else None
            generate = True
        else:
            raise ExecutionContractError("INVALID_REQUEST", "definition.gen_result must be boolean, string or object")
    return {
        "properties": normalised,
        "selection": selection,
        "solution": solution_spec,
        "generate_result": generate,
        "solver": solver,
        "component": controls.get("component"),
    }


def _prepare_definition(definition: Any, *, type_id: str | None = None) -> dict[str, Any]:
    """Validate a definition before any model lookup or mutation-class call.

    The shared validators use ``ExecutionContractError`` for ordinary
    request-shape failures.  Probe create/update are write operations, so a
    failure from this phase must carry the explicit pre-dispatch validation
    stage; otherwise the managed backend cannot prove that an invalid property
    was rejected before the engine and publishes ``EXECUTION_STATE_UNKNOWN``.
    """
    try:
        return _prepare_definition_unchecked(definition, type_id=type_id)
    except PreWriteRefusal:
        raise
    except ExecutionContractError as exc:
        raise _pre_refusal(exc.code, str(exc), details=exc.details) from exc


def _validate_component(component: Any) -> str | None:
    return validate_tag(component, "component") if component is not None else None


def _validate_table_reference(model: Any, properties: Sequence[tuple[str, Any]]) -> str | None:
    table_value = next((value for name, value in properties if name == "table"), None)
    if table_value is None:
        return None
    table = require_string(table_value, "definition.table")
    result = _call(model, "result")
    table_container = _call(result, "table")
    if table not in _safe_tags(table_container):
        raise _pre_refusal("NODE_NOT_FOUND", f"linked results table {table!r} does not exist")
    return table


def _read_property(node: Any, name: str, requested: Any) -> dict[str, Any]:
    if isinstance(requested, bool):
        methods = ("getBoolean", "getString")
    elif isinstance(requested, int) and not isinstance(requested, bool):
        methods = ("getInt", "getDouble", "getString")
    elif isinstance(requested, float):
        methods = ("getDouble", "getInt", "getString")
    elif isinstance(requested, str):
        methods = ("getString",)
    elif isinstance(requested, (list, tuple)):
        flat = list(requested)
        if any(isinstance(item, (list, tuple)) for item in flat):
            matrix_items = [item for row in flat if isinstance(row, (list, tuple)) for item in row]
            if matrix_items and all(isinstance(item, str) for item in matrix_items):
                methods = ("getStringMatrix", "getString")
            elif matrix_items and all(isinstance(item, int) and not isinstance(item, bool) for item in matrix_items):
                methods = ("getIntMatrix", "getDoubleMatrix", "getString")
            else:
                methods = ("getDoubleMatrix", "getStringMatrix", "getString")
        elif flat and all(isinstance(item, str) for item in flat):
            methods = ("getStringArray", "getString")
        elif flat and all(isinstance(item, int) and not isinstance(item, bool) for item in flat):
            methods = ("getIntArray", "getDoubleArray", "getString")
        else:
            methods = ("getDoubleArray", "getString")
    else:
        methods = ("getString",)
    errors: list[Any] = []
    for method in methods:
        ok, value, error = _optional_call(node, method, name)
        if ok:
            safe = _json_safe(value)
            return {"readable": True, "method": method, "value": safe, "match": _equal_value(safe, requested)}
        if error is not None:
            errors.append(error)
    return {"readable": False, "method": None, "value": None, "match": False, "errors": errors}


def _readback(node: Any, properties: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    property_rows: list[dict[str, Any]] = []
    all_readable = True
    all_match = True
    for name, value in properties:
        row = _read_property(node, name, value)
        property_rows.append({"property": name, **row})
        all_readable = all_readable and bool(row.get("readable"))
        all_match = all_match and bool(row.get("match"))
    return {
        "readable": all_readable,
        "properties": property_rows,
        "match": all_match,
        "type": node_type(node),
    }


def _completion(*, applied: Sequence[Any], failed: Sequence[Any], not_executed: Sequence[Any],
                readback: Mapping[str, Any], dispatched: bool = True,
                execution_state_unknown: bool = False) -> dict[str, Any]:
    applied_list = list(applied)
    failed_list = list(failed)
    not_executed_list = list(not_executed)
    readable = bool(readback.get("readable"))
    unknown = bool(execution_state_unknown or (dispatched and not readable))
    if not dispatched:
        status = "NOT_EXECUTED"
    elif failed_list:
        status = "PARTIAL_FAILURE" if applied_list else "FAILED"
    elif unknown:
        status = "DISPATCHED_UNVERIFIED"
    else:
        status = "APPLIED"
    state = "SUCCEEDED" if status == "APPLIED" else ("UNKNOWN" if unknown else ("PARTIAL" if applied_list else "FAILED"))
    verification = "VERIFIED" if status == "APPLIED" else ("UNVERIFIED" if unknown else "FAILED")
    return {
        "ok": status == "APPLIED",
        "status": status,
        "partial_change": bool(applied_list) or unknown,
        "execution_state_unknown": unknown,
        "applied": applied_list,
        "failed": failed_list,
        "not_executed": not_executed_list,
        "applied_count": len(applied_list),
        "failed_count": len(failed_list),
        "not_executed_count": len(not_executed_list),
        "readback": dict(readback),
        "readback_match": bool(readback.get("match", False)),
        "domain_outcome": {
            "state": state,
            "verification_status": verification,
            "execution_state_unknown": unknown,
            "partial_change": bool(applied_list) or unknown,
            "applied_count": len(applied_list),
            "failed_count": len(failed_list),
            "not_executed_count": len(not_executed_list),
        },
    }


def _write_properties(node: Any, properties: Sequence[tuple[str, Any]]) -> tuple[list[Any], list[Any], list[Any], bool]:
    applied: list[Any] = []
    failed: list[Any] = []
    not_executed: list[Any] = []
    unknown = False
    for index, (name, value) in enumerate(properties):
        try:
            _call(node, "set", name, value)
        except Exception as exc:
            code = getattr(exc, "code", "ENGINE_CALL_FAILED")
            failed.append({"step": "property", "property": name, "error": {"code": code, "message": str(exc)}})
            not_executed.extend({"step": "property", "property": later_name} for later_name, _ in properties[index + 1:])
            unknown = True
            break
        readback = _read_property(node, name, value)
        applied.append({"step": "property", "property": name, "requested": value, "readback": readback})
        if not readback.get("readable") or not readback.get("match"):
            code = "EXECUTION_STATE_UNKNOWN" if not readback.get("readable") else "READBACK_MISMATCH"
            failed.append({
                "step": "property", "property": name,
                "error": {"code": code, "message": f"property {name!r} readback did not confirm the requested value"},
            })
            not_executed.extend({"step": "property", "property": later_name} for later_name, _ in properties[index + 1:])
            unknown = not readback.get("readable")
            break
    return applied, failed, not_executed, unknown


def _apply_selection(node: Any, worker: Any, model_tag: str, component: str | None,
                     selection: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    if selection is None:
        return None, None, False
    try:
        local = _call(node, "selection")
        result = apply_local_selection(local, worker, model_tag, component, selection, owner=node)
        return result, None, False
    except Exception as exc:
        return None, {"step": "selection", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}, getattr(exc, "code", None) == "EXECUTION_STATE_UNKNOWN"


def _prepare_generation(prepared: Mapping[str, Any], node: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    if not prepared.get("generate_result"):
        return None, None, False
    solver = prepared.get("solver")
    try:
        _call(node, "genResult", solver)
        return {"step": "genResult", "solver": solver, "readback": {"called": True}}, None, False
    except Exception as exc:
        return None, {"step": "genResult", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}, True


def probe_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create a probe using native type names and truthful post-write status."""
    tag = validate_tag(arguments.get("tag"), "tag")
    requested_type = require_string(arguments.get("type_id"), "type_id", max_length=64)
    if requested_type not in _TYPE_MAP:
        raise _pre_refusal("API_UNSUPPORTED", f"Probe type {requested_type!r} is not in {sorted(SUPPORTED_PROBE_TYPES)}")
    prepared = _prepare_definition(arguments.get("definition", {}), type_id=requested_type)
    model = bound_model(worker, model_tag)
    component = _validate_component(arguments.get("component", prepared.get("component")))
    component_resolution = "explicit" if component is not None else None
    if component is not None:
        components = _component_tags(model)
        if component not in components:
            raise _pre_refusal("NODE_NOT_FOUND", f"component {component!r} does not exist in the model")
    elif requested_type in {"GlobalProbe", "GlobalVariable"}:
        # A root ProbeFeature is not enough for result generation: COMSOL
        # requires its ModelEntity owner.  Resolve an omitted owner only when
        # the model has exactly one native component; multiple components are
        # intentionally ambiguous and an empty component list cannot prove a
        # valid parent.
        components = _component_tags(model)
        if len(components) == 1:
            component = components[0]
            component_resolution = "single_component"
        elif len(components) > 1:
            raise _pre_refusal(
                "AMBIGUOUS_NODE_PATH",
                "GlobalVariable probe creation requires an explicit component when the model has multiple components",
                details={"components": components},
            )
        else:
            raise _pre_refusal(
                "NODE_NOT_FOUND",
                "GlobalVariable probe creation requires a readable owning component",
            )
    table = _validate_table_reference(model, prepared["properties"])
    # GlobalVariable is created in the root model.probe() collection, then
    # bound to the requested component through ModelEntity.model(String).
    # Spatial probes are created in their component collection as before.
    container = _probe_container(
        model,
        None if requested_type in {"GlobalProbe", "GlobalVariable"} else component,
        requested_type,
    )
    existing = _safe_tags(container)
    if tag in existing:
        raise _pre_refusal("TAG_CONFLICT", f"probe {tag!r} already exists")

    applied: list[Any] = []
    failed: list[Any] = []
    not_executed: list[Any] = []
    execution_unknown = False
    try:
        node = _call(container, "create", tag, _TYPE_MAP[requested_type])
    except Exception as exc:
        failed.append({"step": "create", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}})
        not_executed.extend({"step": "property", "property": name} for name, _ in prepared["properties"])
        return {
            "tag": tag, "type_id": requested_type, "native_type": _TYPE_MAP[requested_type], "created": False,
            **_completion(applied=applied, failed=failed, not_executed=not_executed,
                          readback={"readable": False, "match": False}, execution_state_unknown=True),
        }
    applied.append({"step": "create", "method": "probe.create", "requested": {"tag": tag, "type_id": _TYPE_MAP[requested_type]}})
    if node is None:
        try:
            node = _call(container, "get", tag)
        except Exception:
            node = None
    after = _safe_tags(container)
    if tag not in after or node is None:
        failed.append({"step": "create", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-create tag/node readback did not confirm the new probe"}})
        not_executed.extend({"step": "property", "property": name} for name, _ in prepared["properties"])
        readback = {"readable": False, "match": False, "tags": after}
        return {"tag": tag, "type_id": requested_type, "native_type": _TYPE_MAP[requested_type], "created": True, **_completion(applied=applied, failed=failed, not_executed=not_executed, readback=readback, execution_state_unknown=True)}

    if requested_type in {"GlobalProbe", "GlobalVariable"}:
        # Bind before writing properties or calling genResult so every later
        # step sees the same native parent.  The getter is the authoritative
        # readback; an echoed request is not accepted as ownership evidence.
        try:
            _call(node, "model", component)
            owner = _read_model_owner(node)
            model_readback = {"readable": owner is not None, "value": owner,
                              "match": owner == component, "method": "model"}
        except Exception as exc:
            model_readback = {"readable": False, "value": None, "match": False,
                              "method": "model", "error": str(exc)}
        if not model_readback["readable"] or not model_readback["match"]:
            failed.append({"step": "model", "requested": component,
                           "error": {"code": "READBACK_MISMATCH" if model_readback["readable"] else "EXECUTION_STATE_UNKNOWN",
                                      "message": "probe model owner readback did not confirm the requested component"}})
            not_executed.extend({"step": "property", "property": name} for name, _ in prepared["properties"])
            if prepared.get("selection") is not None:
                not_executed.append({"step": "selection"})
            if prepared.get("generate_result"):
                not_executed.append({"step": "genResult"})
            readback = {"readable": False, "match": False, "model": model_readback, "tags": after}
            return {
                "tag": tag, "type_id": requested_type, "native_type": _TYPE_MAP[requested_type],
                "created": True, "component": component, "component_resolution": component_resolution,
                "path": _canonical_probe_path(component, tag), "table": table,
                **_completion(applied=applied, failed=failed, not_executed=not_executed,
                              readback=readback, execution_state_unknown=not model_readback["readable"]),
            }
        applied.append({"step": "model", "requested": component, "readback": model_readback})

    prop_applied, prop_failed, prop_not_executed, prop_unknown = _write_properties(node, prepared["properties"])
    applied.extend(prop_applied)
    failed.extend(prop_failed)
    not_executed.extend(prop_not_executed)
    execution_unknown = execution_unknown or prop_unknown
    if not failed:
        selection_result, selection_failed, selection_unknown = _apply_selection(node, worker, model_tag, component, prepared.get("selection"))
        if selection_result is not None:
            applied.append({"step": "selection", **selection_result})
        if selection_failed is not None:
            failed.append(selection_failed)
            execution_unknown = execution_unknown or selection_unknown
        if not failed:
            generation, generation_failed, generation_unknown = _prepare_generation(prepared, node)
            if generation is not None:
                applied.append(generation)
            if generation_failed is not None:
                failed.append(generation_failed)
                execution_unknown = execution_unknown or generation_unknown
    readback = _readback(node, prepared["properties"])
    if requested_type in {"GlobalProbe", "GlobalVariable"}:
        readback["model"] = {
            "readable": _read_model_owner(node) is not None,
            "value": _read_model_owner(node),
            "match": _read_model_owner(node) == component,
            "method": "model",
        }
    readback["tags"] = after
    readback["type"] = node_type(node) or _TYPE_MAP[requested_type]
    readback["table"] = table
    if prepared.get("selection") is not None:
        try:
            readback["selection"] = selection_state(_call(node, "selection"))
        except Exception as exc:
            readback["selection"] = {"readable": False, "error": str(exc)}
            execution_unknown = True
    result = {
        "tag": tag, "type_id": requested_type, "native_type": _TYPE_MAP[requested_type], "created": True,
        "path": _canonical_probe_path(component, tag), "component": component,
        "component_resolution": component_resolution, "table": table,
        **_completion(applied=applied, failed=failed, not_executed=not_executed, readback=readback, execution_state_unknown=execution_unknown),
    }
    return result


def probe_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Update a typed probe path and stop immediately at the first failure."""
    if arguments.get("path") is None and arguments.get("tag") is None:
        raise _pre_refusal("INVALID_REQUEST", "probe.update requires path")
    prepared = _prepare_definition(arguments.get("definition", {}))
    target = _resolve_target(worker, model_tag, arguments)
    component = target["component"]
    model = bound_model(worker, model_tag)
    table = _validate_table_reference(model, prepared["properties"])
    node = target["node"]
    applied, failed, not_executed, execution_unknown = _write_properties(node, prepared["properties"])
    if not failed:
        selection_result, selection_failed, selection_unknown = _apply_selection(node, worker, model_tag, component, prepared.get("selection"))
        if selection_result is not None:
            applied.append({"step": "selection", **selection_result})
        if selection_failed is not None:
            failed.append(selection_failed)
            execution_unknown = execution_unknown or selection_unknown
        if not failed:
            generation, generation_failed, generation_unknown = _prepare_generation(prepared, node)
            if generation is not None:
                applied.append(generation)
            if generation_failed is not None:
                failed.append(generation_failed)
                execution_unknown = execution_unknown or generation_unknown
    readback = _readback(node, prepared["properties"])
    readback["table"] = table
    return {
        "path": target["path"], "tag": target["tag"], "component": component,
        "type_id": node_type(node), "updated": True,
        **_completion(applied=applied, failed=failed, not_executed=not_executed, readback=readback, execution_state_unknown=execution_unknown),
    }


def probe_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove one unambiguous probe and verify the owning tag list."""
    if arguments.get("path") is None and arguments.get("tag") is None:
        raise _pre_refusal("INVALID_REQUEST", "probe.remove requires path")
    target = _resolve_target(worker, model_tag, arguments, require_node=False)
    tag = target["tag"]
    try:
        _call(target["container"], "remove", tag)
    except Exception as exc:
        return {
            "tag": tag, "path": target["path"], "removed": False,
            **_completion(applied=[], failed=[{"step": "remove", "error": {"code": getattr(exc, "code", "ENGINE_CALL_FAILED"), "message": str(exc)}}], not_executed=[], readback={"readable": False, "match": False}, execution_state_unknown=True),
        }
    after = _safe_tags(target["container"])
    if tag in after:
        return {
            "tag": tag, "path": target["path"], "removed": True,
            **_completion(applied=[{"step": "remove", "tag": tag}], failed=[{"step": "remove", "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "post-remove tags still contain the probe"}}], not_executed=[], readback={"readable": False, "tags": after, "match": False}, execution_state_unknown=True),
        }
    return {
        "tag": tag, "path": target["path"], "removed": True, "verified_removed": True,
        **_completion(applied=[{"step": "remove", "tag": tag}], failed=[], not_executed=[], readback={"readable": True, "tags": after, "match": True}),
    }


def _normalise_matrix(value: Any, label: str) -> list[list[Any]]:
    raw = _as_list(value)
    if raw is None:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{label} did not return an array")
    if not raw:
        return []
    rows: list[list[Any]] = []
    for index, row in enumerate(raw):
        converted = _as_list(row)
        if converted is None:
            if index == 0:
                return [[_json_safe(item) for item in raw]]
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{label} returned ragged rows")
        rows.append([_json_safe(item) for item in converted])
    widths = {len(row) for row in rows}
    if len(widths) > 1:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{label} returned ragged rows")
    return rows


# TableBaseFeature in COMSOL 6.4 does not expose a time getter.  A generated
# probe table records its native time axis as a labelled column instead.  Keep
# the accepted labels deliberately small: accepting any column containing the
# word "time" would let an unrelated user column masquerade as an axis.
_TIME_HEADER_RE = re.compile(
    r"^(?P<label>time|时间)(?:\s*(?:\((?P<paren>s|sec|second|seconds)\)|\[(?P<bracket>s|sec|second|seconds)\]))?$",
    re.IGNORECASE,
)


def _history_scalar(value: Any) -> float | None:
    """Return a finite real scalar from a native table cell, if one is present."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        if "real" not in value:
            return None
        imaginary = value.get("imag", 0.0)
        if isinstance(imaginary, bool):
            return None
        try:
            if not math.isfinite(float(imaginary)) or float(imaginary) != 0.0:
                return None
        except (TypeError, ValueError):
            return None
        value = value.get("real")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time_header(value: Any) -> tuple[str, str | None] | None:
    """Return (normalised label, unit) for a documented native time label."""
    if not isinstance(value, str):
        return None
    match = _TIME_HEADER_RE.fullmatch(" ".join(value.strip().split()))
    if match is None:
        return None
    unit = match.group("paren") or match.group("bracket")
    return match.group("label"), (unit.lower() if unit else None)


def derive_history_time_axis(headers: Any, rows: Any) -> dict[str, Any]:
    """Derive a probe table's time axis from native headers and rows.

    The result is metadata rather than a guessed list.  ``status=VERIFIED`` is
    emitted only for exactly one recognised native time header, rectangular
    rows, finite real values, and a non-decreasing axis.  Missing/ambiguous
    headers and malformed values remain explicit ``UNVERIFIED`` results so a
    caller cannot turn a user table column, a complex value, or a stale echo
    into time metadata.  The stored solution's output times are intentionally
    not inferred here; that comparison belongs to the dataset binding layer.
    """
    result: dict[str, Any] = {
        "status": "UNVERIFIED",
        "source": "none",
        "header": None,
        "column_index": None,
        "unit": None,
        "values": None,
        "row_count": 0,
        "native_table_axis": True,
        "stored_solution_axis": "not_inferred",
    }
    if not isinstance(headers, list) or not headers:
        result["reason"] = "no_column_headers"
        return result
    candidates = [(index, header, _time_header(header)) for index, header in enumerate(headers)]
    candidates = [(index, header, parsed) for index, header, parsed in candidates if parsed is not None]
    if not candidates:
        result["reason"] = "no_validated_time_header"
        return result
    if len(candidates) != 1:
        result["reason"] = "ambiguous_time_headers"
        result["candidates"] = [str(header) for _index, header, _parsed in candidates]
        return result
    index, header, (_label, unit) = candidates[0]
    result.update(header=str(header), column_index=index, unit=unit)
    if not isinstance(rows, list):
        result["reason"] = "rows_not_array"
        return result
    result["row_count"] = len(rows)
    if not rows:
        result["reason"] = "no_rows"
        return result
    values: list[float] = []
    width = len(headers)
    for row_index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) != width:
            result["reason"] = "row_shape_mismatch"
            result["row_index"] = row_index
            result["expected_width"] = width
            result["observed_width"] = len(row) if isinstance(row, (list, tuple)) else None
            return result
        value = _history_scalar(row[index])
        if value is None:
            result["reason"] = "time_value_nonfinite_or_complex"
            result["row_index"] = row_index
            return result
        values.append(value)
    if any(current < previous for previous, current in zip(values, values[1:])):
        result["reason"] = "time_axis_not_monotonic"
        return result
    result.update(status="VERIFIED", source="native_table_column", values=values)
    return result


def _history_table(node: Any, model: Any) -> tuple[str, Any]:
    table_tag = _read_string(node, "table")
    if table_tag is None or not table_tag.strip() or table_tag.strip().lower() == "none":
        raise _pre_refusal("NO_HISTORY", "probe has no linked results table; history was not generated")
    try:
        table_container = _call(_call(model, "result"), "table")
        tags = _safe_tags(table_container)
    except Exception as exc:
        raise ExecutionContractError("NO_HISTORY", f"linked table container is unavailable: {exc}") from exc
    if table_tag not in tags:
        raise _pre_refusal("NO_HISTORY", f"linked results table {table_tag!r} does not exist")
    return table_tag, _call(table_container, "get", table_tag)


def _read_history_table(table: Any) -> dict[str, Any]:
    ok_headers, raw_headers, header_error = _optional_call(table, "getColumnHeaders")
    headers = [str(item) for item in (_as_list(raw_headers) or [])] if ok_headers else []
    ok_rows, raw_real, real_error = _optional_call(table, "getReal")
    if not ok_rows:
        ok_rows, raw_real, real_error = _optional_call(table, "getTableData")
    if not ok_rows:
        raise ExecutionContractError("API_UNSUPPORTED", f"linked table has no readable real data: {real_error}")
    real = _normalise_matrix(raw_real, "table real data")
    ok_imag, raw_imag, imag_error = _optional_call(table, "getImag")
    imag: list[list[Any]] | None = None
    if ok_imag:
        imag = _normalise_matrix(raw_imag, "table imaginary data")
        if len(imag) != len(real) or any(len(a) != len(b) for a, b in zip(imag, real)):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "table real/imaginary data shapes differ")
    ok_complex, complex_value, _complex_error = _optional_call(table, "isComplex")
    if ok_complex and complex_value is True and imag is None:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "table reports complex data but has no readable imaginary part")
    rows: list[list[Any]] = []
    for r_index, row in enumerate(real):
        converted: list[Any] = []
        for c_index, value in enumerate(row):
            converted.append({"real": value, "imag": imag[r_index][c_index]} if imag is not None else value)
        rows.append(converted)
    ok_row_headers, raw_row_headers, row_error = _optional_call(table, "getRowHeaders")
    row_headers = [str(item) for item in (_as_list(raw_row_headers) or [])] if ok_row_headers else []
    metadata: dict[str, Any] = {
        "time_axis": derive_history_time_axis(headers, rows),
    }
    time_axis = metadata["time_axis"]
    if time_axis.get("status") == "VERIFIED":
        metadata["time"] = list(time_axis["values"])
    for output_name, method_names in {
        "frequency": ("getFrequencyValues", "getFrequencies", "getFrequency"),
        "parameters": ("getParameterValues", "getParameters", "getPVals"),
    }.items():
        for method in method_names:
            ok, value, _error = _optional_call(table, method)
            if ok:
                metadata[output_name] = _json_safe(value)
                break
    return {
        "headers": headers,
        "column_headers": headers,
        "row_headers": row_headers,
        "rows": rows,
        "data": rows,
        "metadata": metadata,
        "readback": {
            "headers_readable": ok_headers,
            "real_readable": ok_rows,
            "imag_readable": ok_imag,
            "row_headers_readable": ok_row_headers,
            "time_axis_status": time_axis.get("status"),
            "header_error": header_error,
            "imag_error": imag_error,
            "row_header_error": row_error,
        },
    }


def _history_cursor_encode(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _history_cursor_decode(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionContractError("INVALID_CURSOR", "cursor must be a non-empty string")
    try:
        payload = json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ExecutionContractError("INVALID_CURSOR", "cursor is not a valid continuation token") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ExecutionContractError("INVALID_CURSOR", "cursor has an unsupported format or version")
    return payload


def _history_scope(path: Mapping[str, Any], table_tag: str, solution: Mapping[str, Any] | None,
                   table_data: Mapping[str, Any]) -> str:
    rows = table_data.get("rows") or []
    canonical = {
        "path": path, "table": table_tag, "solution": solution,
        "headers": table_data.get("headers"), "row_headers": table_data.get("row_headers"),
        "shape": [len(rows), len(rows[0]) if rows else 0],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def probe_history(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read the probe's actual linked table without calling ``genResult``."""
    if arguments.get("path") is None and arguments.get("tag") is None:
        raise _pre_refusal("INVALID_REQUEST", "probe.history requires path")
    cursor_payload = _history_cursor_decode(arguments["cursor"]) if arguments.get("cursor") is not None else None
    solution = _normalise_solution(arguments.get("solution"), "solution")
    # A continuation request may carry only the opaque cursor.  Reuse the
    # cursor's previously validated solution scope instead of silently
    # changing the query to a different table view.
    if solution is None and cursor_payload is not None and cursor_payload.get("solution") is not None:
        solution = _normalise_solution(cursor_payload["solution"], "cursor.solution")
    limit = require_int(arguments.get("limit", 100), "limit", minimum=1, maximum=5000)
    target = _resolve_target(worker, model_tag, arguments)
    table_tag, table = _history_table(target["node"], bound_model(worker, model_tag))
    table_data = _read_history_table(table)
    scope = _history_scope(target["path"], table_tag, solution, table_data)
    offset = 0
    if cursor_payload is not None:
        payload = cursor_payload
        expected = {"v": _CURSOR_VERSION, "path": target["path"], "table": table_tag, "solution": solution, "scope": scope}
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ExecutionContractError("CURSOR_STALE", f"history cursor does not match current {key}")
        offset = payload.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ExecutionContractError("INVALID_CURSOR", "history cursor offset is invalid")
    rows = list(table_data["rows"])
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < len(rows):
        next_cursor = _history_cursor_encode({
            "v": _CURSOR_VERSION, "path": target["path"], "table": table_tag,
            "solution": solution, "scope": scope, "offset": next_offset,
        })
    unit = _read_string(target["node"], "unit")
    metadata = dict(table_data.get("metadata") or {})
    time_metadata = metadata.get("time_axis")
    if isinstance(time_metadata, Mapping):
        time_metadata = dict(time_metadata)
        full_values = time_metadata.get("values")
        if time_metadata.get("status") == "VERIFIED" and isinstance(full_values, list):
            page_values = full_values[offset:offset + len(page)]
            time_metadata["values"] = page_values
            time_metadata["scope"] = "page"
            time_metadata["page_offset"] = offset
            time_metadata["page_count"] = len(page_values)
            time_metadata["full_row_count"] = len(full_values)
            page_time = page_values
        else:
            page_time = None
    else:
        page_time = None
    return {
        "path": target["path"], "tag": target["tag"], "component": target["component"],
        "table": table_tag, "unit": unit,
        "headers": table_data["headers"], "column_headers": table_data["column_headers"],
        "row_headers": table_data["row_headers"], "rows": page, "data": page,
        "time": page_time, "time_metadata": time_metadata,
        "frequency": metadata.get("frequency"),
        "parameters": metadata.get("parameters"), "solution": solution,
        "offset": offset, "limit": limit, "total_rows": len(rows),
        "has_more": next_cursor is not None, "next_cursor": next_cursor,
        "source": {"probe_table_property": table_tag, "real_table_readback": True, "gen_result_called": False},
        "readback": table_data["readback"],
    }


# The host dispatcher imports this table.  History is a pure read; all other
# mutating probe operations are isolated by the catalogue effect.
OPERATIONS: dict[str, Any] = {
    "probe.list": probe_list,
    "probe.create": probe_create,
    "probe.update": probe_update,
    "probe.remove": probe_remove,
    "probe.history": probe_history,
}


__all__ = [
    "OPERATIONS", "SUPPORTED_PROBE_TYPES", "probe_list", "probe_create",
    "probe_update", "probe_remove", "probe_history", "derive_history_time_axis",
]
