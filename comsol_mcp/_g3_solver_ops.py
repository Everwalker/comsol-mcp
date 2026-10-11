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
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_contract import NodePath, property_schema_from_engine, resolve_node_path, validate_property_set
from ._g2_engine import _call, property_set
from ._g3_common import bound_model, call_probe, node_type, tag_list, validate_tag
from ._g3_results import dataset_solution_indices, result_at_points


SOLUTION_FIELDS = frozenset({"dataset", "solution", "inner", "outer", "time", "frequency", "parameters"})
_MAX_GRID_POINTS = 65_536
_MAX_LOG_LIMIT = 1000
_MAX_CURSOR_CHARS = 4096
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


def _job_project_binding(job: Mapping[str, Any], project_id: str) -> bool:
    job_meta, operation = job.get("metadata"), job.get("operation")
    op_meta = operation.get("metadata") if isinstance(operation, Mapping) else None
    job_execution = job_meta.get("execution") if isinstance(job_meta, Mapping) else None
    op_execution = op_meta.get("execution") if isinstance(op_meta, Mapping) else None
    return (
        isinstance(job_execution, Mapping) and isinstance(op_execution, Mapping)
        and job_execution.get("project_id") == project_id and op_execution.get("project_id") == project_id
        and isinstance(job.get("operation_id"), str) and operation.get("operation_id") == job.get("operation_id")
    )


def _recorded_exception_chain(job: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = job.get("result")
    error = result.get("error") if isinstance(result, Mapping) else None
    if isinstance(error, Mapping):
        for key in ("exception_chain", "cause_chain", "causes"):
            chain = error.get(key)
            if isinstance(chain, list):
                return {"status": "CAPTURED", "source": f"stored result.error.{key}", "items": chain}
    for event in events:
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("exception_chain", "cause_chain"):
                chain = metadata.get(key)
                if isinstance(chain, list):
                    return {"status": "CAPTURED", "source": f"event {event.get('id')} metadata.{key}", "items": chain}
    if isinstance(error, Mapping) and error.get("code"):
        return {"status": "ERROR_SUMMARY_ONLY", "source": "stored result.error",
                "items": [{"code": error.get("code"), "message": error.get("message")}]}
    return {"status": "NOT_CAPTURED", "source": None, "items": []}


def solver_log_read(store: Any, project_id: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read a project-owned snapshot page of durable job events.

    OperationStore does not persist COMSOL stdout/stderr; that transcript and
    any exception chain absent from stored metadata stay explicitly unavailable.
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
    try:
        job = store.job(job_id)
    except Exception as exc:
        _invalid(f"job store read failed: {exc}", code="EXECUTION_STATE_UNKNOWN")
    if not isinstance(job, Mapping):
        _invalid("job does not exist", code="NODE_NOT_FOUND")
    if not _job_project_binding(job, project):
        _invalid("job and operation project identities do not both match", code="PROJECT_IDENTITY_MISMATCH")
    try:
        snapshot = store.collect_job_event_snapshot(job_id)
    except Exception as exc:
        _invalid(f"durable job event snapshot failed: {exc}", code="EXECUTION_STATE_UNKNOWN")
    if not isinstance(snapshot, Mapping) or snapshot.get("complete") is not True:
        _invalid("durable job event snapshot is incomplete", code="EXECUTION_STATE_UNKNOWN",
                 details={"status": snapshot.get("status") if isinstance(snapshot, Mapping) else None,
                          "issues": snapshot.get("issues") if isinstance(snapshot, Mapping) else None})
    events = snapshot.get("events")
    if not isinstance(events, list):
        _invalid("durable snapshot omitted event rows", code="EXECUTION_STATE_UNKNOWN")
    if cursor is None:
        count, last_id = snapshot.get("expected_event_count"), snapshot.get("snapshot_last_id")
        frozen, offset = events, 0
        id_hash, events_hash = snapshot.get("id_sha256"), snapshot.get("events_sha256")
        if type(count) is not int or type(last_id) is not int:
            _invalid("durable snapshot has malformed bounds", code="EXECUTION_STATE_UNKNOWN")
    else:
        count, last_id = cursor["snapshot_count"], cursor["snapshot_last_id"]
        if count > len(events):
            _invalid("events were removed after this cursor was issued", code="CURSOR_STALE")
        frozen = events[:count]
        actual_last_id = frozen[-1].get("id") if frozen else 0
        if (actual_last_id != last_id or _event_ids_digest(frozen) != cursor["id_sha256"]
                or _event_digest(frozen) != cursor["events_sha256"]):
            _invalid("captured event prefix changed after cursor issuance", code="CURSOR_STALE")
        offset, id_hash, events_hash = cursor["offset"], cursor["id_sha256"], cursor["events_sha256"]
    page = frozen[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < count:
        next_cursor = _encode_log_cursor({
            "v": 1, "project_id": project, "job_id": job_id, "snapshot_count": count,
            "snapshot_last_id": last_id, "id_sha256": id_hash, "events_sha256": events_hash,
            "offset": next_offset, "limit": limit,
        })
    return {
        "job_id": job_id, "project_id": project, "job_status": job.get("status"),
        "events": page, "event_count": len(page), "next_cursor": next_cursor,
        "snapshot": {"status": snapshot.get("status"), "event_count": count, "last_event_id": last_id,
                     "id_sha256": id_hash, "events_sha256": events_hash},
        "solver_output_log": {"status": "NOT_CAPTURED", "text": None,
                              "reason": "OperationStore persists job events, not COMSOL stdout/stderr"},
        "exception_chain": _recorded_exception_chain(job, frozen),
    }


OPERATIONS = {
    "solver.solution_inspect": solver_solution_inspect,
    "solver.solution_clear": solver_solution_clear,
    "solver.resource_configure": solver_resource_configure,
    "study.initial_solution_set": study_initial_solution_set,
    "result.sample_grid": result_sample_grid,
}
