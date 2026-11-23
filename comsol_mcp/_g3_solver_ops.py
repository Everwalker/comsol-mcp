"""Adapters for six solver catalogue routes: five model-bound actions and the
project-scoped ``solver.log_read`` control-plane action.

The five Worker operations are exported in ``OPERATIONS``. The log reader is
separate because it reads durable OperationStore state without a Worker or
model identity.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import math
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_contract import NodePath, property_schema_from_engine, resolve_node_path, validate_property_set
from ._g2_engine import _call, property_set
from ._g3_common import bound_model, call_probe, node_type, tag_list, validate_tag
from ._g3_results import dataset_solution_indices, result_at_points
from ._job_project_scope import consistent_project_id
from ._operation_store import JOB_EVENT_MAX_ROW_BYTES, TERMINAL


SOLUTION_FIELDS = frozenset({"dataset", "solution", "inner", "outer", "time", "frequency", "parameters"})
_MAX_GRID_POINTS = 65_536
_MAX_LOG_LIMIT = 1000
_MAX_CURSOR_CHARS = 4096
# T35 streaming-read operational ceilings for solver.log_read.  These are
# proposed limits for this implementation slice and a compatibility change for
# oversized prior data (a previously readable over-limit row or an oversized
# stored error now fails closed as EXECUTION_STATE_UNKNOWN).  They are not
# requirements from an external spec and are not evidence of a global RSS or
# full-memory bound: the fold and the bounded result projection bound
# Python-side admission and retention, but the unbounded stored-document
# scan, SQLite C allocations and process memory remain outside them.
_MAX_LOG_EVENT_ROW_BYTES = JOB_EVENT_MAX_ROW_BYTES  # shared with the SQLite insert boundary
_MAX_LOG_PAGE_JSON_BYTES = 8 * 1024 * 1024      # selected page event canonical JSON bytes
_MAX_LOG_PROGRESS_JSON_BYTES = 4 * 1024 * 1024  # buffered progress-evidence canonical JSON bytes
# max_error_bytes admission ceiling handed to the bounded
# store.project_operation_result_error projection.  It bounds only the
# selected last-wins raw result.error JSON subtree admitted into the reader;
# the whole stored document is still fully validated through bounded store
# slices, and this ceiling claims no RSS or full-process-memory bound.
_MAX_LOG_STORED_ERROR_JSON_BYTES = 4 * 1024 * 1024

# Exact issue string published by OperationStore.fold_job_event_prefix when a
# frozen continuation prefix no longer matches rows at or below its bound.
_FOLD_ISSUE_FROZEN_PREFIX_MISMATCH = (
    "frozen continuation prefix count/last-id does not match rows at or below its bound"
)

_PROGRESS_EVENT_NAMES = frozenset({
    "COMSOLProgressCaptureRequested", "COMSOLProgressCaptureActivated",
    "COMSOLProgressCaptureActivationUnknown", "COMSOLProgressCaptureCompleted",
    "COMSOLProgressCaptureFailed", "COMSOLProgressChunk",
})
_RESOURCE_PROPERTIES = frozenset({"ooc", "oocmemory", "memfracooc", "mumpsalloc"})
_DIRECT_SOLVER_DOC = "COMSOL 6.4 API: comsol_api_solver.51.31, Tables 6-38 and 6-39"
_CLEAR_DOC = ("COMSOL 6.4 SolverSequence.clearSolutionData(): clears sequence solution data; "
              "features and result features are unaffected. isEmpty() is the clear readback; "
              "isInitialized() can remain true after clearing")
_INITIAL_VALUE_DOCS = [
    "COMSOL 6.4 Programming Reference, Table 6-170",
    "COMSOL 6.4 StudyFeature initial-value properties",
]


def _invalid(message: str, *, code: str = "INVALID_REQUEST", details: Mapping[str, Any] | None = None):
    raise PreWriteRefusal(code, message, details=dict(details or {}))


def _strict_object(value: Any, allowed: set[str] | frozenset[str], required: set[str] | frozenset[str],
                   label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{label} must be an object")
    missing = sorted(set(required) - set(value))
    unexpected = sorted(set(value) - set(allowed))
    if missing or unexpected:
        _invalid(f"{label} fields invalid; missing={missing}, unsupported={unexpected}")
    return dict(value)


def _string(value: Any, label: str, *, allow_empty: bool = False, max_length: int = 256) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > max_length:
        _invalid(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}string of at most {max_length} characters")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _invalid(f"{label} must be a finite number")
    return float(value)


def _quantity(value: Any, label: str) -> dict[str, Any]:
    raw = _strict_object(value, {"value", "unit"}, {"value", "unit"}, label)
    return {"value": _finite_number(raw["value"], f"{label}.value"),
            "unit": _string(raw["unit"], f"{label}.unit", allow_empty=True, max_length=64)}


def _axis_selector(value: Any, label: str) -> str | list[int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value not in {"all", "first", "last"}:
            _invalid(f"{label} must be all, first, last, or a non-empty positive-integer array")
        return value
    if not isinstance(value, list) or not value:
        _invalid(f"{label} must be all, first, last, or a non-empty positive-integer array")
    if any(type(item) is not int or item < 1 for item in value) or len(set(value)) != len(value):
        _invalid(f"{label} indices must be unique positive integers")
    return list(value)


def _normalize_solution_spec(value: Any, label: str = "solution") -> dict[str, Any]:
    raw = _strict_object(value, SOLUTION_FIELDS, {"dataset"}, label)
    result: dict[str, Any] = {"dataset": validate_tag(raw["dataset"], f"{label}.dataset")}
    if "solution" in raw:
        result["solution"] = validate_tag(raw["solution"], f"{label}.solution")
    for axis in ("inner", "outer"):
        if axis in raw:
            result[axis] = _axis_selector(raw[axis], f"{label}.{axis}")
    for axis in ("time", "frequency"):
        if axis in raw:
            values = raw[axis]
            if not isinstance(values, list) or not values:
                _invalid(f"{label}.{axis} must be a non-empty array of Quantity values")
            result[axis] = [_quantity(item, f"{label}.{axis}[{index}]") for index, item in enumerate(values)]
    if "parameters" in raw:
        params = raw["parameters"]
        if not isinstance(params, Mapping):
            _invalid(f"{label}.parameters must map parameter names to Quantity values")
        normalized: dict[str, Any] = {}
        for name, quantity in params.items():
            _string(name, f"{label}.parameters key", max_length=128)
            normalized[name] = _quantity(quantity, f"{label}.parameters[{name!r}]")
        result["parameters"] = normalized
    return result


def _axis_values(selector: str | list[int] | None, available: Any, label: str) -> set[int] | None:
    if not isinstance(available, (list, tuple)) or not available or any(type(item) is not int or item < 1 for item in available):
        return None
    values = list(available)
    if selector is None or selector == "all":
        return set(values)
    if selector == "first":
        return {values[0]}
    if selector == "last":
        return {values[-1]}
    missing = sorted(set(selector) - set(values))
    if missing:
        _invalid(f"{label} selection does not exist in the bound solution axis: {missing}", code="SOLUTION_NOT_FOUND")
    return set(selector)


def _pair_parameters(indices: Mapping[str, Any], pair: Mapping[str, Any]) -> Mapping[str, Any] | None:
    parameters = indices.get("parameters")
    by_pair = parameters.get("by_pair") if isinstance(parameters, Mapping) else None
    value = by_pair.get(f"{pair.get('outer')}:{pair.get('inner')}") if isinstance(by_pair, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _matching_parameter(row: Mapping[str, Any] | None, name: str, requested: Mapping[str, Any]) -> bool | None:
    if not isinstance(row, Mapping):
        return None
    names, values, units = row.get("names"), row.get("values"), row.get("units")
    if not (isinstance(names, list) and isinstance(values, list) and isinstance(units, list)
            and len(names) == len(values) == len(units)):
        return None
    matches = [i for i, actual_name in enumerate(names) if actual_name == name]
    if len(matches) != 1:
        return None
    i = matches[0]
    actual = values[i]
    if (isinstance(actual, bool) or not isinstance(actual, (int, float))
            or not math.isfinite(float(actual)) or units[i] != requested["unit"]):
        return False
    return math.isclose(float(actual), requested["value"], rel_tol=1e-12, abs_tol=1e-15)


def _matching_axis_quantity(row: Mapping[str, Any] | None, names_wanted: frozenset[str],
                            requested: Sequence[Mapping[str, Any]]) -> bool | None:
    if not isinstance(row, Mapping):
        return None
    names, values, units = row.get("names"), row.get("values"), row.get("units")
    if not (isinstance(names, list) and isinstance(values, list) and isinstance(units, list)
            and len(names) == len(values) == len(units)):
        return None
    found: list[tuple[float, str]] = []
    for name, value, unit in zip(names, values, units):
        if isinstance(name, str) and name.lower() in names_wanted:
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or not isinstance(unit, str)):
                return None
            found.append((float(value), unit))
    if len(found) != 1:
        return None
    value, unit = found[0]
    return any(unit == q["unit"] and math.isclose(value, q["value"], rel_tol=1e-12, abs_tol=1e-15)
               for q in requested)


def _select_solution_pairs(indices: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, list[str]]:
    if indices.get("binding_complete") is not True:
        return [], "UNVERIFIED", ["dataset-to-solution or complete inner/outer-to-solnum binding is unavailable"]
    rows = indices.get("solnum_pairs")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        return [], "UNVERIFIED", ["SolutionInfo pair mapping is malformed"]
    inner_values = _axis_values(spec.get("inner"), indices.get("inner_indices"), "solution.inner")
    outer_values = _axis_values(spec.get("outer"), indices.get("outer_indices"), "solution.outer")
    if inner_values is None or outer_values is None:
        return [], "UNVERIFIED", ["one or more requested solution axes are unavailable"]
    selected: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    seen_pairs: set[tuple[int, int]] = set()
    for row in rows:
        if type(row.get("inner")) is not int or type(row.get("outer")) is not int or type(row.get("solnum")) is not int:
            return [], "UNVERIFIED", ["SolutionInfo returned a pair without exact integer indices"]
        pair_key = (row["outer"], row["inner"])
        if pair_key in seen_pairs:
            return [], "UNVERIFIED", ["SolutionInfo returned a duplicate outer/inner pair"]
        seen_pairs.add(pair_key)
        if row["inner"] not in inner_values or row["outer"] not in outer_values:
            continue
        parameter_row = _pair_parameters(indices, row)
        keep = True
        for axis, names in (("time", frozenset({"time", "t"})),
                            ("frequency", frozenset({"frequency", "freq"}))):
            requests = spec.get(axis)
            if requests:
                matched = _matching_axis_quantity(parameter_row, names, requests)
                if matched is None:
                    unresolved.add(f"{axis} is not bound to an exact named, unit-bearing SolutionInfo value")
                    keep = False
                elif not matched:
                    keep = False
        requested_parameters = spec.get("parameters")
        if requested_parameters:
            if indices.get("parameters_complete") is not True:
                unresolved.add("parameter values are not completely bound to SolutionInfo pairs")
                keep = False
            else:
                for name, requested in requested_parameters.items():
                    matched = _matching_parameter(parameter_row, name, requested)
                    if matched is None:
                        unresolved.add(f"parameter {name!r} is not bound to an exact unit-bearing SolutionInfo value")
                        keep = False
                    elif not matched:
                        keep = False
        if keep:
            selected.append(dict(row))
    if unresolved:
        return [], "UNVERIFIED", sorted(unresolved)
    return selected, "MATCHED" if selected else "NO_MATCH", []


def _probe_solution_state(node: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for method in ("isEmpty", "isInitialized", "getDefaultSolnum"):
        probe = call_probe(node, method)
        if probe.get("ok"):
            value = probe.get("value")
            values[method] = value if isinstance(value, (str, bool, int, float)) or value is None else f"<{type(value).__name__}>"
        else:
            errors[method] = probe.get("error")
    return {"values": values, "errors": errors}


def solver_solution_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _strict_object(arguments, {"solution"}, {"solution"}, "solver.solution_inspect arguments")
    spec = _normalize_solution_spec(args["solution"])
    indices = dataset_solution_indices(worker, model_tag, {"path": spec["dataset"]})
    binding = indices.get("dataset_binding")
    solution_tag = indices.get("solution")
    if spec.get("solution") is not None and solution_tag != spec["solution"]:
        _invalid("requested solver sequence does not match the dataset's bound solution",
                 code="SOLUTION_MISMATCH", details={"dataset": spec["dataset"], "bound_solution": solution_tag,
                                                     "requested_solution": spec["solution"]})
    state = None
    if isinstance(solution_tag, str) and solution_tag:
        model = bound_model(worker, model_tag)
        sol_container = _call(model, "sol")
        if solution_tag in tag_list(sol_container):
            state = _probe_solution_state(_call(sol_container, "get", solution_tag))
    selected, selection_status, selection_reasons = _select_solution_pairs(indices, spec)
    values = state.get("values", {}) if isinstance(state, Mapping) else {}
    if values.get("isEmpty") is True:
        availability = "EMPTY"
    elif values.get("isInitialized") is False:
        availability = "NOT_INITIALIZED"
    elif values.get("isInitialized") is True and indices.get("binding_complete") is True and indices.get("solution_count", 0) > 0:
        availability = "AVAILABLE"
    else:
        availability = "UNVERIFIED"
    return {
        "status": availability, "dataset": spec["dataset"], "solution": solution_tag,
        "dataset_binding": binding, "binding_complete": indices.get("binding_complete") is True,
        "solution_count": indices.get("solution_count"), "solution_state": state,
        "inner_indices": indices.get("inner_indices", []), "outer_indices": indices.get("outer_indices", []),
        "solnum_pairs": indices.get("solnum_pairs", []), "time_values": indices.get("time_values", []),
        "parameters": indices.get("parameters", {}), "selected_pairs": selected,
        "selection_status": selection_status, "selection_unresolved": selection_reasons,
        "read_errors": indices.get("read_errors", []),
        "selection_semantics": "only exact SolutionInfo pair mappings are returned; no missing axis or parameter is inferred",
    }


def _canonical_path(path: Any) -> tuple[NodePath, dict[str, Any]]:
    parsed = NodePath.from_wire(path, allow_empty=False)
    return parsed, parsed.as_dict()


def solver_solution_clear(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _strict_object(arguments, {"path", "scope"}, {"path", "scope"}, "solver.solution_clear arguments")
    if args["scope"] != "solution_data":
        _invalid("only scope='solution_data' is supported; no cache or result-feature clearing is claimed",
                 code="API_UNSUPPORTED", details={"mutation_issued": False})
    path, canonical = _canonical_path(args["path"])
    if len(path.segments) != 1 or path.segments[0].collection != "sol":
        _invalid("solution_data clear requires exactly one solver-sequence path sol:<tag>",
                 code="API_UNSUPPORTED", details={"mutation_issued": False})
    node = resolve_node_path(bound_model(worker, model_tag), path)
    before = _probe_solution_state(node)
    try:
        _call(node, "clearSolutionData")
    except Exception as exc:
        return {
            "status": "EXECUTION_STATE_UNKNOWN", "scope": "solution_data", "path": canonical,
            "clear_dispatched": True, "partial_change": True, "execution_state_unknown": True,
            "before": before, "after": _probe_solution_state(node),
            "error": {"code": "EXECUTION_STATE_UNKNOWN",
                      "message": "clearSolutionData was dispatched but its post-dispatch outcome is unresolved"},
            "dispatch_error": {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)},
            "documented_effect": _CLEAR_DOC, "cache_state": "NOT_CLEARED_OR_OBSERVED",
        }
    after = _probe_solution_state(node)
    values = after.get("values", {})
    # COMSOL 6.4 documents that a cleared sequence can remain initialized;
    # only isEmpty() is authoritative evidence that solution data is gone.
    verified = values.get("isEmpty") is True
    return {
        "status": "CLEARED" if verified else "EXECUTION_STATE_UNKNOWN", "scope": "solution_data",
        "path": canonical, "clear_dispatched": True, "partial_change": not verified,
        "execution_state_unknown": not verified, "before": before, "after": after,
        "documented_effect": _CLEAR_DOC, "result_features": "documented_unaffected",
        "cache_state": "NOT_CLEARED_OR_OBSERVED",
    }


def _resource_path(value: Any) -> tuple[NodePath, dict[str, Any]]:
    parsed, canonical = _canonical_path(value)
    segments = parsed.segments
    if (len(segments) < 2 or segments[0].collection != "sol"
            or any(segment.accessor is not None or segment.collection != "feature" for segment in segments[1:])):
        _invalid("resources.path must be below sol:<tag>/feature:<tag>", code="API_UNSUPPORTED",
                 details={"mutation_issued": False})
    return parsed, canonical


def _resource_schema(node: Any, name: str) -> dict[str, Any]:
    schema = property_schema_from_engine(node, name)
    if schema.get("metadata_status") != "KNOWN" or not schema.get("getter"):
        _invalid(f"COMSOL lacks typed setter/readback metadata for {name!r}", code="API_UNSUPPORTED",
                 details={"property": name, "mutation_issued": False})
    kind = "string" if name == "ooc" else "float64"
    if schema.get("kind") != kind or schema.get("shape_rank") != 0:
        _invalid(f"live property {name!r} differs from its documented scalar type", code="API_UNSUPPORTED",
                 details={"property": name, "live_schema": schema, "mutation_issued": False})
    return schema


def _resource_value(name: str, value: Any) -> dict[str, Any]:
    if name == "ooc":
        if not isinstance(value, str) or value not in {"automatic", "on", "off"}:
            _invalid("ooc must be automatic, on, or off", details={"property": name, "mutation_issued": False})
        return {"kind": "string", "shape": [], "data": value}
    number = _finite_number(value, f"resources.settings.{name}")
    if name == "oocmemory" and number <= 0:
        _invalid("oocmemory must be positive megabytes", details={"property": name, "mutation_issued": False})
    if name == "memfracooc" and not 0 <= number <= 1:
        _invalid("memfracooc must be between 0 and 1", details={"property": name, "mutation_issued": False})
    if name == "mumpsalloc" and number < 1:
        _invalid("mumpsalloc must be at least 1", details={"property": name, "mutation_issued": False})
    return {"kind": "float64", "shape": [], "data": number}


def solver_resource_configure(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _strict_object(arguments, {"resources", "scope"}, {"resources", "scope"}, "solver.resource_configure arguments")
    if args["scope"] != "direct_solver":
        _invalid("only model-scoped direct_solver settings are supported; process cores/RAM are not changed",
                 code="API_UNSUPPORTED", details={"mutation_issued": False})
    resources = _strict_object(args["resources"], {"path", "settings"}, {"path", "settings"}, "resources")
    parsed, canonical = _resource_path(resources["path"])
    settings = resources["settings"]
    if not isinstance(settings, Mapping) or not settings:
        _invalid("resources.settings must be a non-empty object")
    names = set(settings)
    unsupported = sorted(names - _RESOURCE_PROPERTIES)
    if unsupported:
        _invalid(f"unsupported resource settings: {unsupported}; process settings are not solver-node settings",
                 code="API_UNSUPPORTED", details={"unsupported": unsupported, "mutation_issued": False})
    values = {name: _resource_value(name, settings[name]) for name in sorted(names)}
    node = resolve_node_path(bound_model(worker, model_tag), parsed)
    type_probe = call_probe(node, "getType")
    if not type_probe.get("ok") or type_probe.get("value") != "Direct":
        _invalid("resources.path must resolve to an actual COMSOL Direct solver feature",
                 code="API_UNSUPPORTED", details={"type_probe": type_probe, "mutation_issued": False})
    properties = _call(node, "properties")
    if not isinstance(properties, (list, tuple)):
        _invalid("Direct solver properties() did not return an authoritative list", code="API_UNSUPPORTED",
                 details={"mutation_issued": False})
    missing = sorted(names - {str(name) for name in properties})
    if missing:
        _invalid(f"live Direct solver lacks requested properties: {missing}", code="API_UNSUPPORTED",
                 details={"missing_properties": missing, "mutation_issued": False})
    linear_solver = None
    try:
        linear_solver = _call(node, "getString", "linsolver")
    except Exception as exc:
        _invalid("Direct solver linsolver could not be read before configuration", code="API_UNSUPPORTED",
                 details={"error": str(exc), "mutation_issued": False})
    if linear_solver not in {"mumps", "pardiso"}:
        _invalid("ooc and memory options require a verified MUMPS or PARDISO Direct solver",
                 code="API_UNSUPPORTED", details={"linsolver": linear_solver, "mutation_issued": False})
    if "mumpsalloc" in names and linear_solver != "mumps":
        _invalid("mumpsalloc applies only to the actual MUMPS Direct solver", code="API_UNSUPPORTED",
                 details={"linsolver": linear_solver, "mutation_issued": False})
    schemas = {name: _resource_schema(node, name) for name in sorted(names)}
    allowed = schemas.get("ooc", {}).get("allowed_values")
    if "ooc" in names and isinstance(allowed, list) and settings["ooc"] not in allowed:
        _invalid("ooc value is not in COMSOL's live allowed-value list", code="API_UNSUPPORTED",
                 details={"allowed_values": allowed, "mutation_issued": False})
    payload = [{"name": name, "value": values[name]} for name in sorted(names)]
    validate_property_set(payload, schemas=schemas)
    result = property_set(worker, model_tag, canonical, payload)
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied, failed, not_executed = (list(data.get(key) or []) for key in ("applied", "failed", "not_executed"))
    unknown = bool(result.get("execution_state_unknown"))
    status = ("APPLIED" if result.get("success") is True and len(applied) == len(payload)
              else "EXECUTION_STATE_UNKNOWN" if unknown else "PARTIAL_FAILURE" if applied else "FAILED")
    return {
        "status": status, "ok": result.get("success") is True, "path": canonical,
        "scope": "direct_solver", "linear_solver": linear_solver, "applied": applied,
        "failed": failed, "not_executed": not_executed,
        "partial_change": bool(result.get("partial_change")), "execution_state_unknown": unknown,
        "process_resources": "NOT_CHANGED", "api_reference": _DIRECT_SOLVER_DOC,
    }


def _study_target(worker: Any, model_tag: str, value: Any) -> tuple[dict[str, Any], Any, str, str]:
    path, canonical = _canonical_path(value)
    segments = path.segments
    if len(segments) != 2 or segments[0].collection != "study" or segments[1].collection != "feature":
        _invalid("step must identify exactly study:<tag>/feature:<tag>", code="API_UNSUPPORTED",
                 details={"mutation_issued": False})
    node = resolve_node_path(bound_model(worker, model_tag), path)
    type_probe = call_probe(node, "type")
    type_id = type_probe.get("value") if type_probe.get("ok") else node_type(node)
    if type_id not in {"Stationary", "Transient", "Frequency"}:
        _invalid("initial-solution settings support only Stationary, Transient, and Frequency study steps",
                 code="API_UNSUPPORTED", details={"type_id": type_id, "mutation_issued": False})
    return canonical, node, segments[0].tag or "", type_id


def study_initial_solution_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _strict_object(arguments, {"step", "source", "mapping"}, {"step", "source"},
                          "study.initial_solution_set arguments")
    mapping = args.get("mapping", {})
    if not isinstance(mapping, Mapping):
        _invalid("mapping must be an object")
    if mapping:
        _invalid("non-empty variable mappings are unsupported without authoritative variable mapping",
                 code="API_UNSUPPORTED", details={"mutation_issued": False})
    source = _normalize_solution_spec(args["source"], "source")
    target_path, target, target_study, type_id = _study_target(worker, model_tag, args["step"])
    indices = dataset_solution_indices(worker, model_tag, {"path": source["dataset"]})
    source_solution = indices.get("solution")
    if source.get("solution") is not None and source_solution != source["solution"]:
        _invalid("source solver sequence does not match its dataset binding", code="SOLUTION_MISMATCH",
                 details={"requested_solution": source["solution"], "bound_solution": source_solution})
    if indices.get("binding_complete") is not True:
        _invalid("source dataset lacks complete SolutionInfo binding", code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                 details={"dataset_binding": indices.get("dataset_binding"), "mutation_issued": False})
    selected, selection_status, selection_errors = _select_solution_pairs(indices, source)
    if selection_status != "MATCHED" or len(selected) != 1:
        code = {
            "MATCHED": "SOLUTION_SELECTION_AMBIGUOUS",
            "NO_MATCH": "SOLUTION_NOT_FOUND",
            "UNVERIFIED": "SOLUTION_AXIS_METADATA_UNAVAILABLE",
        }.get(selection_status, "SOLUTION_AXIS_METADATA_UNAVAILABLE")
        _invalid("source SolutionSpec must resolve to exactly one fully bound tuple",
                 code=code,
                 details={"selected_count": len(selected), "selection_status": selection_status,
                          "selection_unresolved": selection_errors, "mutation_issued": False})
    pair = selected[0]
    outers = indices.get("outer_indices")
    # The StudyFeature API exposes manualsolnum, but no separate outer selector.
    if not isinstance(outers, list) or len(outers) != 1 or pair.get("outer") != outers[0]:
        _invalid("StudyFeature cannot independently encode this source's outer parameter selector",
                 code="API_UNSUPPORTED", details={"outer_indices": outers, "selected_outer": pair.get("outer"),
                                                     "mutation_issued": False})
    if not isinstance(source_solution, str) or not source_solution:
        _invalid("source dataset has no exact solver sequence", code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                 details={"mutation_issued": False})
    model = bound_model(worker, model_tag)
    solution_container = _call(model, "sol")
    source_solver = _call(solution_container, "get", source_solution)
    source_study_probe = call_probe(source_solver, "study")
    source_study = source_study_probe.get("value") if source_study_probe.get("ok") else None
    if not isinstance(source_study, str) or not source_study:
        _invalid("source solver sequence has no verified study association", code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                 details={"source_solution": source_solution, "study_probe": source_study_probe,
                          "mutation_issued": False})
    solver_tags_probe = call_probe(solution_container, "tags")
    solver_tags = solver_tags_probe.get("value") if solver_tags_probe.get("ok") else None
    if (not isinstance(solver_tags, (list, tuple)) or not solver_tags
            or any(not isinstance(tag, str) or not tag for tag in solver_tags)
            or len(set(solver_tags)) != len(solver_tags)):
        _invalid("model.sol().tags() did not provide a complete unique sequence list",
                 code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                 details={"tags_probe": solver_tags_probe, "mutation_issued": False})
    source_study_associations: list[dict[str, str]] = []
    for solver_tag in solver_tags:
        sequence_probe = call_probe(solution_container, "get", solver_tag)
        if not sequence_probe.get("ok") or sequence_probe.get("value") is None:
            _invalid("model.sol().get(tag) could not resolve every solver sequence",
                     code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                     details={"solver_tag": solver_tag, "sequence_probe": sequence_probe,
                              "mutation_issued": False})
        study_probe = call_probe(sequence_probe["value"], "study")
        study_tag = study_probe.get("value") if study_probe.get("ok") else None
        if not isinstance(study_tag, str) or not study_tag:
            _invalid("a solver sequence has no readable study association; source uniqueness is unverified",
                     code="SOLUTION_AXIS_METADATA_UNAVAILABLE",
                     details={"solver_tag": solver_tag, "study_probe": study_probe,
                              "mutation_issued": False})
        if study_tag == source_study:
            source_study_associations.append({"solver": solver_tag, "study": study_tag})
    if (len(source_study_associations) != 1
            or source_study_associations[0]["solver"] != source_solution):
        _invalid("StudyFeature initstudy names a study, not a solver sequence; its study must have one exact source solver",
                 code="API_UNSUPPORTED",
                 details={"source_solution": source_solution, "source_study": source_study,
                          "study_solver_associations": source_study_associations,
                          "mutation_issued": False})
    if source_study == target_study:
        _invalid("same-study source ordering cannot be proven from SolutionSpec alone; use a distinct source study",
                 code="API_UNSUPPORTED", details={"source_study": source_study, "target_study": target_study,
                                                     "mutation_issued": False})
    settings = {
        "useinitsol": {"kind": "string", "shape": [], "data": "on"},
        "initmethod": {"kind": "string", "shape": [], "data": "sol"},
        "initstudy": {"kind": "string", "shape": [], "data": source_study},
        "solnum": {"kind": "string", "shape": [], "data": "manual"},
        "manualsolnum": {"kind": "int32", "shape": [1], "data": [pair["solnum"]]},
    }
    live_properties = _call(target, "properties")
    if not isinstance(live_properties, (list, tuple)):
        _invalid("target study feature properties() is unavailable", code="API_UNSUPPORTED",
                 details={"mutation_issued": False})
    missing = sorted(set(settings) - {str(name) for name in live_properties})
    if missing:
        _invalid(f"target step lacks documented initial-value properties: {missing}", code="API_UNSUPPORTED",
                 details={"type_id": type_id, "mutation_issued": False})
    schemas = {name: property_schema_from_engine(target, name) for name in settings}
    if any(schema.get("metadata_status") != "KNOWN" or not schema.get("getter") for schema in schemas.values()):
        _invalid("target step lacks typed readback metadata for all initialization settings",
                 code="API_UNSUPPORTED", details={"schemas": schemas, "mutation_issued": False})
    payload = [{"name": name, "value": value} for name, value in settings.items()]
    validate_property_set(payload, schemas=schemas)
    result = property_set(worker, model_tag, target_path, payload)
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied, failed, not_executed = (list(data.get(key) or []) for key in ("applied", "failed", "not_executed"))
    unknown = bool(result.get("execution_state_unknown"))
    status = ("APPLIED" if result.get("success") is True and len(applied) == len(payload)
              else "EXECUTION_STATE_UNKNOWN" if unknown else "PARTIAL_FAILURE" if applied else "FAILED")
    return {
        "status": status, "ok": result.get("success") is True,
        "target": {"path": target_path, "study": target_study, "type_id": type_id},
        "source": {"dataset": source["dataset"], "solution": source_solution, "study": source_study,
                   "outer": pair["outer"], "inner": pair["inner"], "solnum": pair["solnum"],
                   "time_or_parameter_binding": _pair_parameters(indices, pair),
                   "dataset_binding": indices.get("dataset_binding"), "selection_status": selection_status},
        "mapping": {}, "applied": applied, "failed": failed, "not_executed": not_executed,
        "partial_change": bool(result.get("partial_change")), "execution_state_unknown": unknown,
        "solve_dispatched": False,
        "verification": "INITIALIZATION_CONFIGURED_ONLY; no solve, variable-map, mesh, or continuity claim",
        "api_references": list(_INITIAL_VALUE_DOCS),
    }


def _regular_axis(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or not value:
        _invalid(f"{label} must be a non-empty array")
    axis = [_finite_number(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if any(right <= left for left, right in zip(axis, axis[1:])):
        _invalid(f"{label} must be strictly increasing")
    if len(axis) > 2:
        step = axis[1] - axis[0]
        if any(not math.isclose(axis[i] - axis[i - 1], step, rel_tol=1e-9, abs_tol=1e-12)
               for i in range(2, len(axis))):
            _invalid(f"{label} must be uniformly spaced")
    return axis


def result_sample_grid(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _strict_object(arguments, {"spec", "grid"}, {"spec", "grid"}, "result.sample_grid arguments")
    spec_fields = {"expressions", "solution", "selection", "entity_dim", "aggregate", "complex_mode",
                   "complex_transform_order", "units", "weight_expression", "coordinate_frame", "storage"}
    spec = _strict_object(args["spec"], spec_fields, {"expressions", "solution"}, "spec")
    unsupported = []
    if spec.get("selection") is not None or spec.get("entity_dim") is not None:
        unsupported.extend(name for name in ("selection", "entity_dim") if spec.get(name) is not None)
    if spec.get("aggregate", "none") != "none":
        unsupported.append("aggregate")
    if spec.get("units") is not None or spec.get("weight_expression") is not None:
        unsupported.extend(name for name in ("units", "weight_expression") if spec.get(name) is not None)
    if spec.get("coordinate_frame", "spatial") != "spatial":
        unsupported.append("coordinate_frame")
    if spec.get("storage", "inline") != "inline":
        unsupported.append("storage")
    if spec.get("complex_transform_order", "before") != "before":
        unsupported.append("complex_transform_order")
    if unsupported:
        _invalid(f"result.at_points does not implement EvaluationSpec fields: {sorted(set(unsupported))}",
                 code="API_UNSUPPORTED", details={"mutation_issued": False})
    expressions = spec["expressions"]
    if not isinstance(expressions, list) or not expressions or any(not isinstance(item, str) or not item for item in expressions):
        _invalid("spec.expressions must be a non-empty array of strings")
    solution = _normalize_solution_spec(spec["solution"], "spec.solution")
    grid = _strict_object(args["grid"], {"axes", "coordinate_unit"}, {"axes"}, "grid")
    if not isinstance(grid["axes"], list) or not 1 <= len(grid["axes"]) <= 3:
        _invalid("grid.axes must contain one to three coordinate axes")
    axes = [_regular_axis(axis, f"grid.axes[{i}]") for i, axis in enumerate(grid["axes"])]
    count = math.prod(len(axis) for axis in axes)
    if count > _MAX_GRID_POINTS:
        _invalid(f"grid exceeds the {_MAX_GRID_POINTS} point cap", code="RESULT_LIMIT_EXCEEDED",
                 details={"point_count": count, "maximum": _MAX_GRID_POINTS, "mutation_issued": False})
    unit = _string(grid.get("coordinate_unit", "m"), "grid.coordinate_unit", max_length=64)
    points = [list(point) for point in itertools.product(*axes)]
    adapter_spec = {"expressions": expressions, "solution": solution,
                    "complex_mode": spec.get("complex_mode", "real")}
    sample = result_at_points(worker, model_tag, {"spec": adapter_spec, "points": points,
                                                  "coordinate_unit": unit, "frame": "spatial"})
    adapter_status = sample.get("status") if isinstance(sample, Mapping) else None
    if isinstance(adapter_status, Mapping):
        if adapter_status.get("ok") is False:
            action_status = "FAILED"
        elif sample.get("verification_status") == "PASSED":
            action_status = "SAMPLED"
        else:
            action_status = "UNVERIFIED"
    elif isinstance(adapter_status, str) and adapter_status in {"VERIFIED", "PASSED", "SAMPLED"}:
        action_status = "SAMPLED"
    else:
        action_status = "UNVERIFIED"
    return {
        "status": action_status,
        "grid": {"axes": axes, "shape": [len(axis) for axis in axes],
                 "axis_order": ["x", "y", "z"][:len(axes)], "coordinate_unit": unit,
                 "point_order": "Cartesian product; last axis varies fastest", "point_count": count},
        "sample": sample, "result_adapter_status": adapter_status,
        "verification_status": sample.get("verification_status") if isinstance(sample, Mapping) else None,
        "scientific_acceptance": "NOT_EVALUATED",
    }


def _event_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _event_ids_digest(events: Sequence[Mapping[str, Any]]) -> str:
    ids = [event.get("id") for event in events]
    return hashlib.sha256(",".join(str(item) for item in ids).encode("ascii")).hexdigest()


def _canonical_event_json_bytes(event: Mapping[str, Any]) -> int:
    """Transiently materialize exactly one admitted event row to measure it.

    Row admission is already canonical-JSON round-trip safe (strict
    json.loads in the fold), so this measures the same bytes the whole-list
    encoders would emit for that element.
    """
    raw = json.dumps(event, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return len(raw)


def _encode_log_cursor(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_log_cursor(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value or len(value) > _MAX_CURSOR_CHARS:
        _invalid("cursor must be a bounded opaque solver-log cursor")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        result = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _invalid(f"cursor is malformed: {exc}")
    required = {"v", "project_id", "job_id", "snapshot_count", "snapshot_last_id",
                "id_sha256", "events_sha256", "offset", "limit"}
    if not isinstance(result, dict) or set(result) != required or result.get("v") != 1:
        _invalid("cursor fields or version are unsupported")
    for key in ("snapshot_count", "snapshot_last_id", "offset"):
        if type(result.get(key)) is not int or result[key] < 0:
            _invalid("cursor contains invalid snapshot indices")
    if type(result.get("limit")) is not int or not 1 <= result["limit"] <= _MAX_LOG_LIMIT:
        _invalid("cursor contains invalid page limit")
    for key in ("project_id", "job_id", "id_sha256", "events_sha256"):
        if not isinstance(result.get(key), str) or not result[key]:
            _invalid("cursor contains an empty identity or digest")
    if result["offset"] > result["snapshot_count"]:
        _invalid("cursor offset exceeds its captured event count")
    return result


def _compacted_flat_job_claim(job: Mapping[str, Any], project_id: str) -> bool:
    """Match a forgeable subset of the shape of a compacted job view.

    T35 compatibility: OperationStore.compact_terminal_job_metadata rewrites
    a genuinely terminal job's view metadata down to the helper-derived flat
    top-level project_id plus a cleanup tombstone, dropping the nested
    execution mapping.  This predicate only recognizes that subset of
    observable fields (terminal status, the metadata_compacted tombstone,
    the flat project_id and the absent execution key); a caller can write
    the same marker shape into arbitrary job metadata, so a match is
    neither an exact-shape match of the compactor's full output nor proof
    that a genuine compaction happened, and never an authorization proof:
    a caller granting any exemption off this shape must still validate
    every present flat or nested claim on both sides and keep the exact
    operation_id linkage itself.  A job-side execution key that is present
    — even when empty or conflicting — never matches this shape.
    """
    job_meta = job.get("metadata")
    job_cleanup = job_meta.get("cleanup") if isinstance(job_meta, Mapping) else None
    return (
        isinstance(job_meta, Mapping)
        and job.get("status") in TERMINAL
        and isinstance(job_cleanup, Mapping)
        and job_cleanup.get("metadata_compacted") is True
        and "execution" not in job_meta
        and job_meta.get("project_id") == project_id
    )


def _job_project_binding(job: Mapping[str, Any], project_id: str) -> bool:
    job_meta, operation = job.get("metadata"), job.get("operation")
    op_meta = operation.get("metadata") if isinstance(operation, Mapping) else None
    job_execution = job_meta.get("execution") if isinstance(job_meta, Mapping) else None
    op_execution = op_meta.get("execution") if isinstance(op_meta, Mapping) else None
    # T35 compatibility: the compacted flat job claim (_compacted_flat_job_claim,
    # shared verbatim with _progress_log_summary) is only a forgeable
    # subset-shape check of the real compactor's output — neither an
    # exact-shape nor a genuine-compaction proof, and never an
    # authorization proof: the exempted job side
    # must still name exactly the requested project, and consistent_project_id
    # below still validates every present flat or nested claim on both sides,
    # rejecting missing, malformed or conflicting ones.  The operation side
    # keeps its original nested-claim requirement, and the exact operation_id
    # linkage is kept.
    return (
        ((isinstance(job_execution, Mapping) and job_execution.get("project_id") == project_id)
         or _compacted_flat_job_claim(job, project_id))
        and isinstance(op_execution, Mapping) and op_execution.get("project_id") == project_id
        and consistent_project_id(job_meta, op_meta) == project_id
        and isinstance(job.get("operation_id"), str) and operation.get("operation_id") == job.get("operation_id")
    )


def _stored_result_exception_chain(job: Mapping[str, Any]) -> dict[str, Any] | None:
    result = job.get("result")
    error = result.get("error") if isinstance(result, Mapping) else None
    if isinstance(error, Mapping):
        for key in ("exception_chain", "cause_chain", "causes"):
            chain = error.get(key)
            if isinstance(chain, list):
                return {"status": "CAPTURED", "source": f"stored result.error.{key}", "items": chain}
    return None


def _exception_chain_candidate(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """First-match exception evidence for one event, exactly as the historical
    whole-prefix scan would have returned it for this event."""
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("exception_chain", "cause_chain"):
            chain = metadata.get(key)
            if isinstance(chain, list):
                return {"status": "CAPTURED", "source": f"event {event.get('id')} metadata.{key}", "items": chain}
    if event.get("event") == "worker_request":
        event_metadata = event.get("metadata")
        worker_event = event_metadata if isinstance(event_metadata, Mapping) else event
        reply = worker_event.get("reply")
        failure = reply.get("failure") if isinstance(reply, Mapping) else None
        if isinstance(failure, Mapping):
            chain = failure.get("cause_chain")
            if isinstance(chain, list) and chain:
                return {"status": "CAPTURED", "source": f"worker request {event.get('request_id')} failure.cause_chain",
                        "items": chain[:32], "code": failure.get("code")}
            if isinstance(failure.get("code"), str):
                return {"status": "ERROR_SUMMARY_ONLY",
                        "source": f"worker request {event.get('request_id')} reply.failure",
                        "items": [{"type": "WorkerFailure", "code": failure.get("code"),
                                   "message": failure.get("message")}],
                        "cause_chain_status": "NOT_CAPTURED"}
    return None


def _stored_result_error_summary(job: Mapping[str, Any]) -> dict[str, Any] | None:
    result = job.get("result")
    error = result.get("error") if isinstance(result, Mapping) else None
    if isinstance(error, Mapping) and error.get("code"):
        return {"status": "ERROR_SUMMARY_ONLY", "source": "stored result.error",
                "items": [{"code": error.get("code"), "message": error.get("message")}]}
    return None


def _recorded_exception_chain(job: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    chain = _stored_result_exception_chain(job)
    if chain is not None:
        return chain
    for event in events:
        candidate = _exception_chain_candidate(event)
        if candidate is not None:
            return candidate
    summary = _stored_result_error_summary(job)
    if summary is not None:
        return summary
    return {"status": "NOT_CAPTURED", "source": None, "items": []}


_PROGRESS_BINDING_FIELDS = (
    "schema", "producer", "job_id", "operation_id", "request_id",
    "request_hash", "idempotency_key", "project_id", "session_id", "model_ref",
    "model_revision", "operation",
)


def _typed_value_equal(left: Any, right: Any) -> bool:
    """Compare JSON-like identity values without conflating scalar types.

    Decoded JSON mappings and arrays are traversed without a width-sized
    comparison stack. Active-path tracking rejects cyclic in-process values;
    it does not validate identity shape or authenticate either value.
    """
    def mapping_pairs(first: Mapping, second: Mapping) -> Iterator[tuple[Any, Any]]:
        for key in first:
            yield first[key], second[key]

    stack: list[tuple[Iterator[tuple[Any, Any]], tuple[int, int] | None]] = [
        (iter(((left, right),)), None)
    ]
    active: set[tuple[int, int]] = set()
    while stack:
        pairs, owner = stack[-1]
        try:
            first, second = next(pairs)
        except StopIteration:
            stack.pop()
            if owner is not None:
                active.remove(owner)
            continue
        if isinstance(first, Mapping) or isinstance(second, Mapping):
            if (not isinstance(first, Mapping) or not isinstance(second, Mapping)
                    or len(first) != len(second)
                    or any(key not in second for key in first)):
                return False
            children = mapping_pairs(first, second)
        elif isinstance(first, (list, tuple)) or isinstance(second, (list, tuple)):
            if (not isinstance(first, (list, tuple)) or not isinstance(second, (list, tuple))
                    or isinstance(first, list) != isinstance(second, list)
                    or len(first) != len(second)):
                return False
            children = zip(first, second)
        else:
            if type(first) is not type(second) or first != second:
                return False
            continue
        pair = (id(first), id(second))
        if pair in active:
            return False
        active.add(pair)
        stack.append((iter(children), pair))
    return True


def _same_progress_binding(metadata: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """True only when every binding field is itself present in the event
    metadata and has the same JSON-like types and values as the durable
    expected value. Missing fields fail instead of matching a missing or None
    expected value; type-aware equality is not a validity or authority check.
    """
    return all(key in metadata and _typed_value_equal(metadata[key], expected[key])
               for key in _PROGRESS_BINDING_FIELDS)


def _progress_log_summary(job: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
                          project_id: str) -> dict[str, Any]:
    """Verify the exact job-bound capture and its bounded chunks in a frozen snapshot.

    Durable-vs-event identity equality is necessary but not sufficient:
    before any CAPTURED can be emitted, the durable job/operation records
    must also meet the identity lower bounds the real producer enforces in
    ``_comsol_progress_capture._binding_for_job`` — non-empty
    job_id/operation_id/request_id/idempotency_key, a 64-lowercase-hex
    request_hash and a recognized supported solver operation — so durable
    and event records corrupted identically still fail closed to UNKNOWN.
    """
    capture_events = [event for event in events if event.get("event") in _PROGRESS_EVENT_NAMES]
    if not capture_events:
        return {"status": "NOT_CAPTURED", "text": None,
                "reason": "no job-bound COMSOL progress producer event is present"}

    operation = job.get("operation")
    op_metadata = operation.get("metadata") if isinstance(operation, Mapping) else None
    job_metadata = job.get("metadata")
    execution = op_metadata.get("execution") if isinstance(op_metadata, Mapping) else None
    job_execution = job_metadata.get("execution") if isinstance(job_metadata, Mapping) else None
    stored_operation = op_metadata.get("operation") if isinstance(op_metadata, Mapping) else None
    stored_arguments = op_metadata.get("arguments") if isinstance(op_metadata, Mapping) else None
    if stored_operation in {"study.run", "mesh.convergence_study"}:
        bound_operation = stored_operation
    elif (stored_operation in {"registry_call", "operation_call"}
          and isinstance(stored_arguments, Mapping)
          and stored_arguments.get("operation_id") in {"study.run", "mesh.convergence_study"}):
        bound_operation = stored_arguments["operation_id"]
    else:
        bound_operation = None
    # T35 compatibility: the real compact_terminal_job_metadata deliberately
    # drops the job-side nested execution, so the two execution mappings can
    # no longer be equal afterwards.  Exempt exactly that inequality, and only
    # when the job view independently matches the compactor's compacted flat
    # shape — a forgeable subset-shape check (_compacted_flat_job_claim),
    # neither an exact-shape nor a genuine-compaction proof.  Only on that
    # exemption candidate path does this summary call _job_project_binding
    # (the and-chain short-circuits whenever the flat shape does not match);
    # a non-compacted job keeps the direct execution-inequality requirement
    # and never runs two-sided project/operation binding consistency through
    # _job_project_binding here. That check is not authorization. When the
    # exemption is in play the complete
    # _job_project_binding must still hold, so a direct call into this
    # summary can never skip the two-sided project/operation claim and
    # operation_id linkage.  A job-side execution key that is present —
    # empty or conflicting — never matches the shape and keeps the original
    # inequality check in force.  The exemption authenticates nothing: every
    # other condition here and every downstream 12-field event-binding,
    # lifecycle, chunk, base64, digest and receipt check runs unchanged
    # against the durable operation-side execution.
    compacted_capture_continuity = (
        _compacted_flat_job_claim(job, project_id)
        and _job_project_binding(job, project_id)
    )
    # Preserve producer-compatible ordinary equality for the rest of the
    # execution mapping, but compare the model and revision identity subset
    # by type and value. Compaction deliberately removes job execution and
    # continues to rely on the same flat-shape and binding checks above.
    execution_identity_conflict = (
        not compacted_capture_continuity
        and isinstance(execution, Mapping)
        and (not isinstance(job_execution, Mapping)
             or not _typed_value_equal(execution.get("expected_revision"),
                                       job_execution.get("expected_revision"))
             or not _typed_value_equal(execution.get("model_ref"),
                                       job_execution.get("model_ref")))
    )
    if (not isinstance(operation, Mapping) or not isinstance(op_metadata, Mapping)
            or not isinstance(execution, Mapping)
            or (execution != job_execution and not compacted_capture_continuity)
            or execution_identity_conflict
            or operation.get("operation_id") != job.get("operation_id")
            or execution.get("project_id") != project_id
            or not isinstance(execution.get("session_id"), str) or not execution.get("session_id")
            or not isinstance(execution.get("model_ref"), Mapping)
            or type(execution.get("expected_revision")) is not int
            or execution.get("expected_revision") < 0):
        return {"status": "UNKNOWN", "text": None,
                "reason": "durable job and operation do not share a complete project/model/revision binding"}
    # The 12-field comparison below is a pure durable-vs-event equality
    # check: a fault model that corrupts the durable operation row and every
    # progress event identically stays internally consistent and would still
    # certify.  Before any CAPTURED can be emitted, re-check the absolute
    # identity lower bounds the real producer enforces in
    # _comsol_progress_capture._binding_for_job against the durable records:
    # non-empty job_id, non-empty operation_id (operation-side equality is
    # already required above), non-empty request_id and idempotency_key, a
    # 64-lowercase-hex request_hash, and a recognized supported solver
    # operation (bound_operation is study.run, mesh.convergence_study or
    # None).  These are absence/format bounds on the durable records, not
    # authentication, and they change no captured field value.
    if (not isinstance(job.get("job_id"), str) or not job.get("job_id")
            or not isinstance(job.get("operation_id"), str) or not job.get("operation_id")
            or not isinstance(operation.get("request_id"), str) or not operation.get("request_id")
            or not isinstance(operation.get("request_hash"), str)
            or len(operation["request_hash"]) != 64
            or any(char not in "0123456789abcdef" for char in operation["request_hash"])
            or not isinstance(operation.get("idempotency_key"), str)
            or not operation.get("idempotency_key")
            or bound_operation is None):
        return {"status": "UNKNOWN", "text": None,
                "reason": "durable job/operation identity is missing or malformed against the "
                          "progress producer contract (empty ids, non-64-lowercase-hex "
                          "request_hash, or unsupported persisted operation)"}
    expected = {
        "schema": "comsol-job-progress-capture/1",
        "producer": "ModelUtil.showProgress(String)+showProgress(false)",
        "job_id": job.get("job_id"),
        "operation_id": job.get("operation_id"),
        "request_id": operation.get("request_id"),
        "request_hash": operation.get("request_hash"),
        "idempotency_key": operation.get("idempotency_key"),
        "project_id": project_id,
        "session_id": execution.get("session_id"),
        "model_ref": execution.get("model_ref"),
        "model_revision": execution.get("expected_revision"),
        "operation": bound_operation,
    }
    scoped = [event for event in capture_events if isinstance(event.get("metadata"), Mapping)]
    metadata_rows = [event["metadata"] for event in scoped]
    if (len(scoped) != len(capture_events)
            or any(not _same_progress_binding(metadata, expected) for metadata in metadata_rows)):
        return {"status": "UNKNOWN", "text": None,
                "reason": "progress producer event identity differs from the exact durable job binding"}
    capture_ids = {metadata.get("capture_id") for metadata in metadata_rows}
    if len(capture_ids) != 1 or not isinstance(next(iter(capture_ids)), str):
        return {"status": "UNKNOWN", "text": None,
                "reason": "job snapshot has missing or conflicting progress capture identities"}
    capture_id = next(iter(capture_ids))
    event_names = {row.get("event") for row in scoped}
    named = {name: [row["metadata"] for row in scoped if row.get("event") == name]
             for name in event_names}
    requests = named.get("COMSOLProgressCaptureRequested", [])
    completed = named.get("COMSOLProgressCaptureCompleted", [])
    failures = named.get("COMSOLProgressCaptureFailed", [])
    activations = named.get("COMSOLProgressCaptureActivated", [])
    activation_unknown = named.get("COMSOLProgressCaptureActivationUnknown", [])
    chunks = named.get("COMSOLProgressChunk", [])
    if len(requests) != 1 or len(completed) > 1 or len(activations) > 1 or len(activation_unknown) > 1:
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress capture has duplicate or incomplete lifecycle records"}
    if failures or activation_unknown:
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress capture has a failed or uncertain Worker lifecycle receipt"}
    if not completed:
        status = "IN_PROGRESS" if job.get("status") in {"QUEUED", "STARTING", "RUNNING"} else "UNKNOWN"
        return {"status": status, "text": None, "capture_id": capture_id,
                "reason": "progress capture has no terminal disable/readback receipt"}
    if len(activations) != 1 or activations[0].get("capture_state") != "ACTIVE":
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress capture completion has no unique activation receipt"}

    summary = completed[0]
    captured_bytes = summary.get("captured_bytes")
    file_size = summary.get("file_size_bytes")
    chunk_count = summary.get("chunk_count")
    digest_value = summary.get("captured_sha256")
    truncated = summary.get("truncated")
    cleanup_verified = summary.get("cleanup_verified")
    if (type(captured_bytes) is not int or captured_bytes < 0
            or type(file_size) is not int or file_size < captured_bytes
            or type(chunk_count) is not int or chunk_count != len(chunks)
            or not isinstance(digest_value, str) or len(digest_value) != 64
            or any(char not in "0123456789abcdef" for char in digest_value)
            or type(truncated) is not bool or type(cleanup_verified) is not bool
            or cleanup_verified is not True
            or summary.get("scratch_retained") is not False
            or summary.get("complete") is not (not truncated)
            or summary.get("captured_sha256_scope") != ("prefix" if truncated else "complete_file")):
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress completion receipt has malformed size, digest, cleanup or truncation fields"}

    ordered = sorted(chunks, key=lambda item: item.get("chunk_index", -1))
    digest = hashlib.sha256()
    offset = 0
    for index, chunk in enumerate(ordered):
        if (type(chunk.get("chunk_index")) is not int or chunk.get("chunk_index") != index
                or type(chunk.get("byte_offset")) is not int or chunk.get("byte_offset") != offset
                or type(chunk.get("byte_length")) is not int or not 1 <= chunk.get("byte_length") <= 8192
                or chunk.get("content_encoding") != "base64"
                or not isinstance(chunk.get("content_base64"), str)
                or not isinstance(chunk.get("chunk_sha256"), str)):
            return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                    "reason": "progress chunk sequence or encoding is malformed"}
        try:
            raw = base64.b64decode(chunk["content_base64"], validate=True)
        except Exception:
            return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                    "reason": "progress chunk is not valid base64"}
        if (len(raw) != chunk["byte_length"]
                or hashlib.sha256(raw).hexdigest() != chunk["chunk_sha256"]):
            return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                    "reason": "progress chunk length or SHA-256 does not match its bytes"}
        digest.update(raw)
        offset += len(raw)
    if (offset != captured_bytes or len(chunks) != chunk_count
            or digest.hexdigest() != digest_value
            or (not truncated and file_size != captured_bytes)
            or (truncated and file_size <= captured_bytes)):
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "captured progress bytes do not match the terminal size/count/SHA-256 receipt"}
    capture_state = summary.get("capture_state")
    if capture_state not in {"CAPTURED", "CAPTURED_TRUNCATED", "NO_PROGRESS_TEXT"}:
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress producer returned an unsupported terminal state"}
    if ((capture_state == "NO_PROGRESS_TEXT") != (captured_bytes == 0)
            or (capture_state == "CAPTURED_TRUNCATED") != truncated):
        return {"status": "UNKNOWN", "text": None, "capture_id": capture_id,
                "reason": "progress terminal state conflicts with byte/truncation evidence"}
    return {
        "status": capture_state, "text": None, "capture_id": capture_id,
        "producer": expected["producer"], "byte_length": captured_bytes,
        "file_size_bytes": file_size, "sha256": digest_value,
        "sha256_scope": summary["captured_sha256_scope"], "chunk_count": chunk_count,
        "chunk_event": "COMSOLProgressChunk", "chunk_encoding": "base64",
        "truncated": truncated, "cleanup_verified": True,
        "reason": ("COMSOL emitted no progress text for this operation" if capture_state == "NO_PROGRESS_TEXT"
                   else "bounded project-scoped COMSOL progress output was captured and verified"),
    }


class _LogPageFoldAccumulator:
    """Bounded provisional state for one ``fold_job_event_prefix`` pass.

    Retains only the selected page rows (under ``_MAX_LOG_PAGE_JSON_BYTES``
    of canonical event JSON), progress-producer events (under
    ``_MAX_LOG_PROGRESS_JSON_BYTES``; the buffer is discarded the moment that
    budget would be exceeded) and the first exception-chain candidate.  All
    state is provisional: the fold can still fail after visiting early rows,
    so ``solver_log_read`` must discard this accumulator on any incomplete
    fold result or exception.
    """

    def __init__(self, *, offset: int, limit: int) -> None:
        self._offset = offset
        self._limit = limit
        self._seen = 0
        self.page: list[dict[str, Any]] = []
        self._page_bytes = 0
        self._page_closed = False
        self.single_row_over_response_limit = False
        self._progress: list[dict[str, Any]] | None = []
        self._progress_bytes = 0
        self.progress_budget_exceeded = False
        self.exception_candidate: dict[str, Any] | None = None

    @property
    def progress_events(self) -> list[dict[str, Any]] | None:
        return self._progress

    def visit(self, event: dict[str, Any]) -> None:
        index = self._seen
        self._seen += 1
        row_bytes = _canonical_event_json_bytes(event)
        if self._offset <= index < self._offset + self._limit:
            if row_bytes > _MAX_LOG_PAGE_JSON_BYTES:
                # Never silently skip an over-ceiling row and certify a full
                # prefix: the read must fail closed at this offset.
                self.single_row_over_response_limit = True
            elif not self._page_closed and (
                    not self.page or self._page_bytes + row_bytes <= _MAX_LOG_PAGE_JSON_BYTES):
                self.page.append(event)
                self._page_bytes += row_bytes
            else:
                # Valid rows beyond the response ceiling end this page early;
                # the next cursor resumes at exactly the next offset.
                self._page_closed = True
        if event.get("event") in _PROGRESS_EVENT_NAMES and self._progress is not None:
            if self._progress_bytes + row_bytes > _MAX_LOG_PROGRESS_JSON_BYTES:
                self.progress_budget_exceeded = True
                self._progress = None
            else:
                self._progress.append(event)
                self._progress_bytes += row_bytes
        if self.exception_candidate is None:
            self.exception_candidate = _exception_chain_candidate(event)


def solver_log_read(store: Any, project_id: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read a project-owned page of durable job events through one streaming fold.

    Each read opens one reader-owned ``store.solver_log_snapshot(job_id)``
    read transaction (transaction plus store lock held until exit) and issues
    exactly one ``store.fold_job_event_prefix`` call joined to that snapshot
    via ``_join_read_snapshot=True``; the joined fold never commits or rolls
    back the caller-owned transaction, and default self-owned fold semantics
    are unchanged.  The fold runs over a fresh whole-job prefix
    ``(None, None)`` or the frozen cursor prefix
    (``snapshot_count``/``snapshot_last_id``).  The fold recomputes the full
    original v1 id/events digests for that prefix before any data is
    returned; both digests and the last event id are compared to the cursor,
    so an existing cursor is never trusted without re-verifying its entire
    prefix and later appends never join it.  Prefix removal or content change
    fails closed as CURSOR_STALE; malformed or over-admission rows fail
    closed as EXECUTION_STATE_UNKNOWN, and provisional page/summary state is
    discarded whenever the fold is incomplete or raises.

    The job metadata view is read first inside the snapshot (a missing job
    is still NODE_NOT_FOUND) and the existing ``_job_project_binding`` check
    runs before any stored-result bytes are fetched.  That check is
    snapshot-consistent project/operation identity evidence only; it is
    never principal authentication and grants no authority.  The stored
    operation result is then admitted solely through the bounded
    ``store.project_operation_result_error`` projection executed in the same
    transaction — never by re-fetching through ``store.job()``, and never by
    falling back to it when the snapshot context or projection is missing.
    The projection's ``None``/``{}``/``{'error': ...}`` envelope is assigned
    to the job view's ``result`` slot so the unchanged stored-chain,
    event-first, stored-summary and NOT_CAPTURED precedence below sees
    exactly the error visible in this snapshot.  A selected error over
    ``_MAX_LOG_STORED_ERROR_JSON_BYTES``, syntax damage in any part of the
    unbounded stored document, invalid UTF-8 or a BOM, nesting deeper than
    the projection helper admits, an envelope outside the frozen contract,
    or any snapshot/projection store failure fails closed as an explicit
    EXECUTION_STATE_UNKNOWN without echoing the stored payload and without
    defaulting to "no error" or falling back to the event chain.  The page,
    progress and exception views are all computed inside the same snapshot
    before returning; any escaping exception is rolled back by the context.

    Only the selected page rows plus bounded progress/exception evidence are
    retained; a valid row set larger than ``_MAX_LOG_PAGE_JSON_BYTES`` yields
    a non-empty shorter page with a cursor at the exact next offset (the
    caller's limit is retained).  Progress evidence beyond
    ``_MAX_LOG_PROGRESS_JSON_BYTES`` degrades to an honest UNKNOWN diagnostic
    instead of retaining unbounded history.  These ceilings and
    ``_MAX_LOG_STORED_ERROR_JSON_BYTES`` are proposed operational admission
    limits for this slice, not a global RSS or full-memory bound.

    COMSOL progress output is available only when the exact job has a verified
    private ModelUtil progress-sink lifecycle. Worker stderr and other jobs'
    process output are never substituted for that capture.
    """
    args = _strict_object(arguments, {"job_id", "cursor", "limit"}, {"job_id"}, "solver.log_read arguments")
    project = _string(project_id, "project_id", max_length=256)
    job_id = _string(args["job_id"], "job_id", max_length=256)
    cursor = _decode_log_cursor(args["cursor"]) if args.get("cursor") is not None else None
    if cursor is not None and (cursor["project_id"] != project or cursor["job_id"] != job_id):
        _invalid("cursor is bound to a different project or job", code="CURSOR_BINDING_MISMATCH")
    limit = args.get("limit", cursor["limit"] if cursor else 100)
    if type(limit) is not int or not 1 <= limit <= _MAX_LOG_LIMIT:
        _invalid("limit must be an integer from 1 through 1000")
    if cursor is not None and limit != cursor["limit"]:
        _invalid("continuation limit must match the cursor", code="CURSOR_BINDING_MISMATCH")
    accumulator = _LogPageFoldAccumulator(offset=cursor["offset"] if cursor else 0, limit=limit)
    try:
        # One reader-owned snapshot pins the job view, the bounded stored-error
        # projection and the event fold to a single consistent store state.
        # A missing snapshot context, a failed snapshot begin, or a failed
        # snapshot close fails closed as EXECUTION_STATE_UNKNOWN below; it is
        # never answered by the old unbounded store.job() path.
        with store.solver_log_snapshot(job_id) as job:
            if not isinstance(job, Mapping):
                _invalid("job does not exist", code="NODE_NOT_FOUND")
            if not isinstance(job, MutableMapping):
                # The frozen contract yields an updatable metadata view (the
                # result slot receives the bounded projection below); an
                # immutable view cannot carry it and fails closed here.
                _invalid("job snapshot view is not an updatable job metadata view",
                         code="EXECUTION_STATE_UNKNOWN")
            # Strict project/operation binding first; ordering this check
            # before the projection does not grant authority.  It remains
            # pure metadata consistency evidence, never principal proof.
            if not _job_project_binding(job, project):
                _invalid("job and operation project identities do not both match", code="PROJECT_IDENTITY_MISMATCH")
            fold_count = fold_last = None
            if cursor is not None:
                fold_count, fold_last = cursor["snapshot_count"], cursor["snapshot_last_id"]
                if (fold_count == 0) != (fold_last == 0):
                    # A v1 cursor never pairs a zero count with a nonzero last id; the
                    # historical reader reached the same CURSOR_STALE for this pair.
                    _invalid("captured event prefix changed after cursor issuance", code="CURSOR_STALE")
            projection: Any = None
            try:
                projection = store.project_operation_result_error(
                    job["operation_id"], max_error_bytes=_MAX_LOG_STORED_ERROR_JSON_BYTES)
            except ExecutionContractError:
                raise
            except Exception as exc:
                # Covers the frozen helper's SelectedErrorBudgetExceededError,
                # JsonStreamError (syntax damage anywhere in the unbounded
                # document, invalid UTF-8, BOM, unsupported depth) and any
                # sqlite/API failure.  Only the exception class name is
                # echoed; the stored payload is never part of the response.
                _invalid(f"stored result error projection failed closed ({type(exc).__name__}); "
                         "the bounded stored error is not admissible, and no empty-error or "
                         "event-chain fallback is used",
                         code="EXECUTION_STATE_UNKNOWN",
                         details={"max_stored_error_json_bytes": _MAX_LOG_STORED_ERROR_JSON_BYTES,
                                  "failure_type": type(exc).__name__})
            if projection is not None and (not isinstance(projection, Mapping)
                                           or set(projection) not in (set(), {"error"})):
                _invalid("stored result error projection returned an envelope outside the "
                         "frozen None/empty/{'error': value} contract",
                         code="EXECUTION_STATE_UNKNOWN")
            job["result"] = projection
            folded: Any = None
            try:
                folded = store.fold_job_event_prefix(
                    job_id, snapshot_count=fold_count, snapshot_last_id=fold_last,
                    max_row_bytes=_MAX_LOG_EVENT_ROW_BYTES, visitor=accumulator.visit,
                    _join_read_snapshot=True)
            except ExecutionContractError:
                raise
            except Exception as exc:
                _invalid(f"durable job event fold failed: {exc}", code="EXECUTION_STATE_UNKNOWN")
            if not isinstance(folded, Mapping) or folded.get("complete") is not True:
                issues = folded.get("issues") if isinstance(folded, Mapping) else None
                if (cursor is not None and isinstance(issues, list) and len(issues) == 1
                        and issues[0] == _FOLD_ISSUE_FROZEN_PREFIX_MISMATCH):
                    # Only the exact frozen-bounds issue string may look stale; every
                    # other or additional issue fails closed as unknown state.
                    _invalid("captured event prefix changed after cursor issuance", code="CURSOR_STALE",
                             details={"status": folded.get("status"), "issues": issues})
                _invalid("durable job event fold is incomplete", code="EXECUTION_STATE_UNKNOWN",
                         details={"status": folded.get("status") if isinstance(folded, Mapping) else None,
                                  "issues": issues})
            count, last_id = folded.get("expected_event_count"), folded.get("snapshot_last_id")
            id_hash, events_hash = folded.get("id_sha256"), folded.get("events_sha256")
            if (type(count) is not int or type(last_id) is not int or count < 0 or last_id < 0
                    or not isinstance(id_hash, str) or not id_hash
                    or not isinstance(events_hash, str) or not events_hash):
                _invalid("durable job event fold published malformed bounds", code="EXECUTION_STATE_UNKNOWN")
            offset = 0
            if cursor is not None:
                if (count != cursor["snapshot_count"] or last_id != cursor["snapshot_last_id"]
                        or id_hash != cursor["id_sha256"] or events_hash != cursor["events_sha256"]):
                    _invalid("captured event prefix changed after cursor issuance", code="CURSOR_STALE")
                offset = cursor["offset"]
            if accumulator.single_row_over_response_limit:
                _invalid("a selected page event exceeds the response byte limit; the read fails "
                         "closed instead of skipping that event",
                         code="EXECUTION_STATE_UNKNOWN",
                         details={"max_page_event_json_bytes": _MAX_LOG_PAGE_JSON_BYTES})
            page = accumulator.page
            next_offset = offset + len(page)
            next_cursor = None
            if next_offset < count:
                next_cursor = _encode_log_cursor({
                    "v": 1, "project_id": project, "job_id": job_id, "snapshot_count": count,
                    "snapshot_last_id": last_id, "id_sha256": id_hash, "events_sha256": events_hash,
                    "offset": next_offset, "limit": limit,
                })
            if accumulator.progress_budget_exceeded:
                solver_output_log = {"status": "UNKNOWN", "text": None,
                                     "reason": f"the bounded progress accumulator exceeded its "
                                               f"{_MAX_LOG_PROGRESS_JSON_BYTES}-byte budget; the capture is "
                                               "not certified and no complete progress output is claimed"}
            else:
                solver_output_log = _progress_log_summary(job, accumulator.progress_events or [], project)
            exception_chain = (_stored_result_exception_chain(job) or accumulator.exception_candidate
                               or _stored_result_error_summary(job)
                               or {"status": "NOT_CAPTURED", "source": None, "items": []})
            return {
                "job_id": job_id, "project_id": project, "job_status": job.get("status"),
                "events": page, "event_count": len(page), "next_cursor": next_cursor,
                "snapshot": {"status": folded.get("status"), "event_count": count, "last_event_id": last_id,
                             "id_sha256": id_hash, "events_sha256": events_hash},
                "solver_output_log": solver_output_log,
                "exception_chain": exception_chain,
            }
    except ExecutionContractError:
        # NODE_NOT_FOUND, PROJECT_IDENTITY_MISMATCH, CURSOR_STALE and every
        # other PreWriteRefusal raised by _invalid inside the snapshot are
        # contract refusals; they pass through the context rollback with
        # their exact codes and are never re-wrapped as EXECUTION_STATE_UNKNOWN.
        raise
    except Exception as exc:
        # Snapshot begin/close failures (including a store lacking the
        # solver_log_snapshot context) land here; projection and fold call
        # failures were already mapped to their own explicit failures above.
        _invalid(f"durable job snapshot transaction failed: {exc}", code="EXECUTION_STATE_UNKNOWN")
    # Unreachable while the snapshot context honours the contract (every
    # body path returns or raises, and the context rolls back and re-raises);
    # kept as an explicit fail-closed guard so a non-conforming context that
    # swallows an exception can never let this reader fall through to an
    # implicit empty result.
    _invalid("durable job snapshot read produced no page", code="EXECUTION_STATE_UNKNOWN")


OPERATIONS = {
    "solver.solution_inspect": solver_solution_inspect,
    "solver.solution_clear": solver_solution_clear,
    "solver.resource_configure": solver_resource_configure,
    "study.initial_solution_set": study_initial_solution_set,
    "result.sample_grid": result_sample_grid,
}
