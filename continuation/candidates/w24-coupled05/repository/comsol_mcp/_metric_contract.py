"""Strict versioned wire contract for the F16 metric actions.

This module validates the scientific meaning requested by a caller.  It does
not claim that a unit, selection, weight, or COMSOL evaluation has been
observed; those facts must be supplied by the native evaluation adapter.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError


METRIC_OPERATIONS = frozenset({
    "metric.define", "metric.list", "metric.evaluate", "metric.remove", "metric.compare",
})
_METRIC_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_AGGREGATES = frozenset({"integral", "average", "minimum", "maximum", "std", "rms"})
_COMPLEX_MODES = frozenset({"preserve", "real", "imag", "abs"})
_ENVELOPE = frozenset({"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"})


_MODEL_REF_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "session_id", "server_instance_id", "model_tag", "generation"],
    "properties": {
        "schema_version": {"type": "integer", "minimum": 1},
        "session_id": {"type": "string", "minLength": 1},
        "server_instance_id": {"type": "string", "minLength": 1},
        "model_tag": {"type": "string", "minLength": 1},
        "generation": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}
_SOLUTION_SCHEMA = {
    "type": "object", "required": ["dataset"],
    "properties": {
        "dataset": {"type": "string", "minLength": 1},
        "solution": {"type": "string", "minLength": 1},
        "outer": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "uniqueItems": True}, {"const": "all"}]},
        "inner": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "uniqueItems": True}, {"const": "all"}]},
    }, "additionalProperties": False,
}
_SELECTION_SCHEMA = {
    "type": "object", "required": ["kind", "component", "geometry", "entity_dimension"],
    "properties": {
        "kind": {"type": "string", "enum": ["all", "named", "explicit"]},
        "component": {"type": "string", "minLength": 1},
        "geometry": {"type": "string", "minLength": 1},
        "entity_dimension": {"type": "integer", "minimum": 0, "maximum": 3},
        "tag": {"type": "string", "minLength": 1},
        "entities": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "uniqueItems": True},
    }, "additionalProperties": False,
}
_DEFINITION_SCHEMA = {
    "type": "object", "required": ["expression", "expected_unit", "aggregate", "solution", "selection"],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "expression": {"type": "string", "minLength": 1},
        "expected_unit": {"type": "string", "minLength": 1},
        "aggregate": {"type": "string", "enum": sorted(_AGGREGATES)},
        "complex_mode": {"type": "string", "enum": sorted(_COMPLEX_MODES)},
        "solution": _SOLUTION_SCHEMA,
        "selection": _SELECTION_SCHEMA,
        "threshold": {"type": "object", "required": ["relation", "value", "unit"], "properties": {
            "relation": {"type": "string", "enum": ["gt", "gte", "lt", "lte"]},
            "value": {"type": "number"}, "unit": {"type": "string", "minLength": 1},
        }, "additionalProperties": False},
        "weight": {"type": "object", "required": ["expression", "expected_unit", "nonnegative"], "properties": {
            "expression": {"type": "string", "minLength": 1},
            "expected_unit": {"type": "string", "const": "1"},
            "nonnegative": {"type": "boolean", "const": True},
        }, "additionalProperties": False},
    }, "additionalProperties": False,
}


def input_schema(operation_id: str) -> dict[str, Any]:
    """Return the strict public schema, including outer execution identity."""
    properties: dict[str, Any] = {
        "project_id": {"type": "string", "minLength": 1},
        "session_id": {"type": "string", "minLength": 1},
        "model_ref": _MODEL_REF_SCHEMA,
        "request_id": {"type": "string"},
    }
    required = ["project_id", "session_id", "model_ref"]
    if operation_id != "metric.list":
        properties.update({
            "expected_revision": {"type": "integer", "minimum": 0},
            "idempotency_key": {"type": "string", "minLength": 1},
        })
        required.extend(["expected_revision", "idempotency_key"])
    if operation_id == "metric.define":
        properties.update({
            "metric_id": {"type": "string", "pattern": _METRIC_ID.pattern},
            "definition": _DEFINITION_SCHEMA,
        })
        required.extend(["metric_id", "definition"])
    elif operation_id == "metric.list":
        properties["filter"] = {"type": "object", "properties": {
            "metric_id": {"type": "string", "pattern": _METRIC_ID.pattern},
            "include_removed": {"type": "boolean"},
        }, "additionalProperties": False}
    elif operation_id == "metric.evaluate":
        properties.update({
            "metric_ids": {"type": "array", "items": {"type": "string", "pattern": _METRIC_ID.pattern}, "minItems": 1, "uniqueItems": True},
            "solution": _SOLUTION_SCHEMA,
        })
        required.append("metric_ids")
    elif operation_id == "metric.remove":
        properties["metric_id"] = {"type": "string", "pattern": _METRIC_ID.pattern}
        required.append("metric_id")
    elif operation_id == "metric.compare":
        properties.update({
            "cases": {"type": "array", "minItems": 2, "items": {"oneOf": [
                {"type": "object", "required": ["evaluation_id", "sha256"], "properties": {
                    "evaluation_id": {"type": "string", "minLength": 1}, "sha256": {"type": "string", "minLength": 1},
                }, "additionalProperties": False},
                {"type": "object", "required": ["experiment_id", "case_id"], "properties": {
                    "experiment_id": {"type": "string", "minLength": 1}, "case_id": {"type": "string", "minLength": 1},
                }, "additionalProperties": False},
            ]}},
            "metric_ids": {"type": "array", "items": {"type": "string", "pattern": _METRIC_ID.pattern}, "minItems": 1, "uniqueItems": True},
            "tolerances": {"type": "object", "additionalProperties": {"type": "object", "required": ["absolute", "relative", "unit"], "properties": {
                "absolute": {"type": "number", "minimum": 0}, "relative": {"type": "number", "minimum": 0},
                "unit": {"type": "string", "minLength": 1}, "complex_distance": {"type": "string", "const": "modulus"},
            }, "additionalProperties": False}},
        })
        required.extend(["cases", "metric_ids", "tolerances"])
    return {"type": "object", "required": required, "properties": properties, "additionalProperties": False}


def _invalid(message: str, code: str = "INVALID_REQUEST") -> None:
    raise ExecutionContractError(code, message)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{label} must be a non-empty string")
    return value.strip()


def _finite_real(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(f"{label} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        _invalid(f"{label} must be a finite real number")
    return result


def _strict_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        _invalid(f"{label} has unsupported fields: {', '.join(extra)}")


def _solution(value: Any, label: str = "solution") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{label} must be an object")
    _strict_keys(value, {"dataset", "solution", "outer", "inner"}, label)
    out: dict[str, Any] = {"dataset": _string(value.get("dataset"), f"{label}.dataset")}
    if "solution" in value:
        out["solution"] = _string(value["solution"], f"{label}.solution")
    for axis in ("outer", "inner"):
        if axis not in value:
            continue
        selector = value[axis]
        if selector == "all":
            out[axis] = "all"
        elif isinstance(selector, int) and not isinstance(selector, bool) and selector >= 1:
            out[axis] = selector
        elif (isinstance(selector, list) and selector and
              all(isinstance(item, int) and not isinstance(item, bool) and item >= 1 for item in selector)):
            if len(set(selector)) != len(selector):
                _invalid(f"{label}.{axis} indices must be unique")
            out[axis] = sorted(selector)
        else:
            _invalid(f"{label}.{axis} must be a positive index, unique positive-index array, or 'all'")
    return out


def _selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("definition.selection must be an object")
    kind = value.get("kind")
    if kind not in {"all", "named", "explicit"}:
        _invalid("definition.selection.kind must be all, named, or explicit")
    allowed = {"kind", "component", "geometry", "entity_dimension", "tag", "entities"}
    _strict_keys(value, allowed, "definition.selection")
    out = {
        "kind": kind,
        "component": _string(value.get("component"), "definition.selection.component"),
        "geometry": _string(value.get("geometry"), "definition.selection.geometry"),
        "entity_dimension": value.get("entity_dimension"),
    }
    dim = out["entity_dimension"]
    if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0 or dim > 3:
        _invalid("definition.selection.entity_dimension must be an integer from 0 through 3")
    if kind == "named":
        out["tag"] = _string(value.get("tag"), "definition.selection.tag")
        if "entities" in value:
            _invalid("named selection cannot also specify entities")
    elif kind == "explicit":
        entities = value.get("entities")
        if (not isinstance(entities, list) or not entities or
                any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in entities)):
            _invalid("explicit selection requires a non-empty array of positive entity indices")
        if len(set(entities)) != len(entities):
            _invalid("explicit selection entity indices must be unique")
        out["entities"] = sorted(entities)
        if "tag" in value:
            _invalid("explicit selection cannot also specify a named tag")
    elif "tag" in value or "entities" in value:
        _invalid("all selection cannot also specify a tag or entities")
    return out


def normalize_definition(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("definition must be an object")
    _strict_keys(value, {
        "schema_version", "expression", "expected_unit", "aggregate", "complex_mode",
        "solution", "selection", "threshold", "weight",
    }, "definition")
    version = value.get("schema_version", 1)
    if isinstance(version, bool) or version != 1:
        _invalid("definition.schema_version must be 1")
    expression = _string(value.get("expression"), "definition.expression")
    expected_unit = _string(value.get("expected_unit"), "definition.expected_unit")
    aggregate = value.get("aggregate")
    if aggregate not in _AGGREGATES:
        _invalid(f"definition.aggregate must be one of {sorted(_AGGREGATES)}")
    complex_mode = value.get("complex_mode", "real")
    if complex_mode == "phase":
        _invalid("phase metrics require verified angle units and a wrap rule", "API_UNSUPPORTED")
    if complex_mode not in _COMPLEX_MODES:
        _invalid(f"definition.complex_mode must be one of {sorted(_COMPLEX_MODES)}")
    if complex_mode == "preserve" and aggregate in {"minimum", "maximum"}:
        _invalid("complex extrema require an explicit real, imaginary, or magnitude projection", "COMPLEX_ORDER_UNDEFINED")
    result: dict[str, Any] = {
        "schema_version": 1,
        "expression": expression,
        "expected_unit": expected_unit,
        "aggregate": aggregate,
        "complex_mode": complex_mode,
        "solution": _solution(value.get("solution")),
        "selection": _selection(value.get("selection")),
    }
    if "threshold" in value:
        threshold = value["threshold"]
        if not isinstance(threshold, Mapping):
            _invalid("definition.threshold must be an object")
        _strict_keys(threshold, {"relation", "value", "unit"}, "definition.threshold")
        relation = threshold.get("relation")
        if relation not in {"gt", "gte", "lt", "lte"}:
            _invalid("definition.threshold.relation must be gt, gte, lt, or lte")
        if complex_mode == "preserve":
            _invalid("complex-preserving definitions cannot apply real-valued thresholds", "API_UNSUPPORTED")
        unit = _string(threshold.get("unit"), "definition.threshold.unit")
        if unit != expected_unit:
            _invalid("threshold unit must exactly match expected_unit")
        result["threshold"] = {"relation": relation, "value": _finite_real(threshold.get("value"), "definition.threshold.value"), "unit": unit}
    if "weight" in value:
        weight = value["weight"]
        if not isinstance(weight, Mapping):
            _invalid("definition.weight must be an object")
        _strict_keys(weight, {"expression", "expected_unit", "nonnegative"}, "definition.weight")
        unit = _string(weight.get("expected_unit"), "definition.weight.expected_unit")
        if unit != "1" or weight.get("nonnegative") is not True:
            _invalid("weight must declare dimensionless unit '1' and nonnegative=true")
        result["weight"] = {
            "expression": _string(weight.get("expression"), "definition.weight.expression"),
            "expected_unit": unit,
            "nonnegative": True,
        }
    return result


def normalize_arguments(operation_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        _invalid("metric arguments must be an object")
    allowed_by_operation = {
        "metric.define": {"metric_id", "definition"},
        "metric.list": {"filter"},
        "metric.evaluate": {"metric_ids", "solution"},
        "metric.remove": {"metric_id"},
        "metric.compare": {"cases", "metric_ids", "tolerances"},
    }
    if operation_id not in allowed_by_operation:
        _invalid("unknown metric operation")
    _strict_keys(arguments, allowed_by_operation[operation_id] | set(_ENVELOPE), operation_id)
    result = {key: value for key, value in arguments.items() if key in _ENVELOPE}
    if operation_id in {"metric.define", "metric.remove"}:
        metric_id = _string(arguments.get("metric_id"), "metric_id")
        if not _METRIC_ID.fullmatch(metric_id):
            _invalid("metric_id must match [A-Za-z][A-Za-z0-9_.-]{0,63}")
        result["metric_id"] = metric_id
    if operation_id == "metric.define":
        result["definition"] = normalize_definition(arguments.get("definition"))
    elif operation_id == "metric.list":
        filter_value = arguments.get("filter", {})
        if not isinstance(filter_value, Mapping):
            _invalid("filter must be an object")
        _strict_keys(filter_value, {"metric_id", "include_removed"}, "filter")
        include_removed = filter_value.get("include_removed", False)
        if type(include_removed) is not bool:
            _invalid("filter.include_removed must be a boolean")
        result["filter"] = {
            **({"metric_id": _string(filter_value["metric_id"], "filter.metric_id")} if "metric_id" in filter_value else {}),
            "include_removed": include_removed,
        }
    elif operation_id == "metric.evaluate":
        ids = arguments.get("metric_ids")
        if not isinstance(ids, list) or not ids:
            _invalid("metric_ids must be a non-empty array")
        normalized = [_string(item, "metric_ids item") for item in ids]
        if any(not _METRIC_ID.fullmatch(item) for item in normalized) or len(set(normalized)) != len(normalized):
            _invalid("metric_ids must be unique valid metric identifiers")
        result["metric_ids"] = normalized
        if "solution" in arguments:
            result["solution"] = _solution(arguments["solution"])
    elif operation_id == "metric.compare":
        cases = arguments.get("cases")
        if not isinstance(cases, list) or len(cases) < 2:
            _invalid("cases must contain at least two authorized references")
        result_cases = []
        for index, case in enumerate(cases):
            if not isinstance(case, Mapping):
                _invalid(f"cases[{index}] must be an object")
            if set(case) == {"evaluation_id", "sha256"}:
                result_cases.append({
                    "evaluation_id": _string(case["evaluation_id"], f"cases[{index}].evaluation_id"),
                    "sha256": _string(case["sha256"], f"cases[{index}].sha256"),
                })
            elif set(case) == {"experiment_id", "case_id"}:
                result_cases.append({
                    "experiment_id": _string(case["experiment_id"], f"cases[{index}].experiment_id"),
                    "case_id": _string(case["case_id"], f"cases[{index}].case_id"),
                })
            else:
                _invalid(f"cases[{index}] must be exactly an evaluation reference or experiment/case reference")
        if len({tuple(sorted(item.items())) for item in result_cases}) != len(result_cases):
            _invalid("cases must not contain duplicate references")
        ids = arguments.get("metric_ids")
        if not isinstance(ids, list) or not ids:
            _invalid("metric_ids must be a non-empty array")
        normalized_ids = [_string(item, "metric_ids item") for item in ids]
        if any(not _METRIC_ID.fullmatch(item) for item in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):
            _invalid("metric_ids must be unique valid metric identifiers")
        tolerances = arguments.get("tolerances")
        if not isinstance(tolerances, Mapping) or set(tolerances) != set(normalized_ids):
            _invalid("tolerances must define exactly one tolerance for each metric_id")
        normalized_tolerances = {}
        for metric_id, tolerance in tolerances.items():
            if not isinstance(tolerance, Mapping):
                _invalid(f"tolerances.{metric_id} must be an object")
            _strict_keys(tolerance, {"absolute", "relative", "unit", "complex_distance"}, f"tolerances.{metric_id}")
            if "absolute" not in tolerance or "relative" not in tolerance or "unit" not in tolerance:
                _invalid(f"tolerances.{metric_id} requires absolute, relative, and unit")
            absolute = _finite_real(tolerance["absolute"], f"tolerances.{metric_id}.absolute")
            relative = _finite_real(tolerance["relative"], f"tolerances.{metric_id}.relative")
            if absolute < 0 or relative < 0:
                _invalid(f"tolerances.{metric_id} values must be nonnegative")
            item = {"absolute": absolute, "relative": relative, "unit": _string(tolerance["unit"], f"tolerances.{metric_id}.unit")}
            if "complex_distance" in tolerance:
                if tolerance["complex_distance"] != "modulus":
                    _invalid(f"tolerances.{metric_id}.complex_distance must be modulus")
                item["complex_distance"] = "modulus"
            normalized_tolerances[metric_id] = item
        result.update(cases=result_cases, metric_ids=normalized_ids, tolerances=normalized_tolerances)
    return result


def definition_sha256(definition: Mapping[str, Any]) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(dict(definition), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
