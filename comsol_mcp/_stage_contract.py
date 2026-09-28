"""Strict, declaration-only contract for immutable W21 stage plans.

This module validates syntax and internal plan consistency. It deliberately does
not resolve study, dataset, solution, or variable names against COMSOL; those
items are persisted as DECLARED_UNVERIFIED until a later execution adapter can
prove them against a live model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictStr

from ._execution_contract import ExecutionContractError


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Quantity(_StrictModel):
    value: StrictInt | float
    unit: StrictStr


class _SolutionSpec(_StrictModel):
    dataset: StrictStr = Field(min_length=1)
    solution: StrictStr | None = Field(default=None, min_length=1)
    inner: Literal["all", "first", "last"] | list[StrictInt] | None = None
    outer: Literal["all", "first", "last"] | list[StrictInt] | None = None
    time: list[_Quantity] | None = Field(default=None, min_length=1)
    frequency: list[_Quantity] | None = Field(default=None, min_length=1)
    parameters: dict[str, Any] | None = None


class _NodeCollectionSegment(_StrictModel):
    collection: StrictStr = Field(min_length=1)
    tag: StrictStr = Field(min_length=1)


class _NodeAccessorSegment(_StrictModel):
    accessor: StrictStr = Field(min_length=1)


class _NodePath(_StrictModel):
    segments: list[_NodeCollectionSegment | _NodeAccessorSegment] = Field(min_length=1)


class _InitialSource(_StrictModel):
    kind: Literal["initial_state"]
    strategy: Literal["declared_initial"]


class _StageSource(_StrictModel):
    kind: Literal["stage"]
    stage_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    selection: _SolutionSpec


class _VariableMapping(_StrictModel):
    source_variable: StrictStr = Field(min_length=1)
    target_variable: StrictStr = Field(min_length=1)
    source_unit: StrictStr = Field(min_length=1)
    target_unit: StrictStr = Field(min_length=1)
    mapping_method: Literal["identity", "interpolate", "projection"]


class _Tolerance(_StrictModel):
    absolute: StrictInt | float = Field(ge=0, allow_inf_nan=False)
    relative: StrictInt | float = Field(ge=0, allow_inf_nan=False)


class _ContinuityCheck(_StrictModel):
    kind: Literal["continuity"]
    check_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    source_variable: StrictStr = Field(min_length=1)
    target_variable: StrictStr = Field(min_length=1)
    unit: StrictStr = Field(min_length=1)
    tolerance: _Tolerance


class _ConservationTerm(_StrictModel):
    side: Literal["source", "target"]
    variable: StrictStr = Field(min_length=1)
    coefficient: StrictInt | float = Field(allow_inf_nan=False)


class _ConservationCheck(_StrictModel):
    kind: Literal["conservation"]
    check_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    quantity: StrictStr = Field(min_length=1)
    terms: list[_ConservationTerm] = Field(min_length=2)
    unit: StrictStr = Field(min_length=1)
    tolerance: _Tolerance


class _InitialReference(_StrictModel):
    strategy: Literal["initial_state"]


class _PredecessorReference(_StrictModel):
    strategy: Literal["predecessor_stage"]
    source_stage_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class _Stage(_StrictModel):
    stage_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    ordinal: StrictInt = Field(ge=1)
    depends_on: list[StrictStr] = Field(json_schema_extra={"uniqueItems": True})
    study_target: _NodePath
    source_selection: _InitialSource | _StageSource
    target_selection: _SolutionSpec
    variable_mappings: list[_VariableMapping] = Field(min_length=1)
    reference_state: _InitialReference | _PredecessorReference
    checks: list[_ContinuityCheck | _ConservationCheck]


class _SelectionSpecV2(_StrictModel):
    kind: Literal["named", "explicit", "all", "spatial", "objects", "inherited"]
    component: StrictStr | None = None
    geometry: StrictStr | None = None
    entity_dimension: StrictInt | None = Field(default=None, ge=0, le=3)
    tag: StrictStr | None = None
    entities: list[StrictInt] | None = None
    object_tags: list[StrictStr] | None = None
    query: dict[str, Any] | None = None
    geometry_revision: StrictInt | None = None


class _ContinuityCheckV2(_StrictModel):
    kind: Literal["continuity"]
    check_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    operator: Literal["pointwise_max_abs"]
    source_variable: StrictStr = Field(min_length=1)
    target_variable: StrictStr = Field(min_length=1)
    source_solution: _SolutionSpec
    target_solution: _SolutionSpec
    source_selection: _SelectionSpecV2
    target_selection: _SelectionSpecV2
    frame: Literal["spatial", "material", "geometry", "mesh"]
    boundary_time: _Quantity
    unit: StrictStr = Field(min_length=1)
    tolerance: _Tolerance


class _ConservationTermV2(_StrictModel):
    side: Literal["source", "target"]
    variable: StrictStr = Field(min_length=1)
    coefficient: StrictInt | float = Field(allow_inf_nan=False)
    selection: _SelectionSpecV2
    entity_dimension: StrictInt = Field(ge=0, le=3)


class _ConservationCheckV2(_StrictModel):
    kind: Literal["conservation"]
    check_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    operator: Literal["native_integral"]
    quantity: StrictStr = Field(min_length=1)
    source_solution: _SolutionSpec
    target_solution: _SolutionSpec
    terms: list[_ConservationTermV2] = Field(min_length=2)
    unit: StrictStr = Field(min_length=1)
    tolerance: _Tolerance


class _StageV2(_StrictModel):
    stage_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    ordinal: StrictInt = Field(ge=1)
    depends_on: list[StrictStr] = Field(json_schema_extra={"uniqueItems": True})
    study_target: _NodePath
    source_selection: _InitialSource | _StageSource
    target_selection: _SolutionSpec
    target_variables: _NodePath | None = None
    mapping_profile: Literal["same_name_same_mesh_initialization"]
    variable_mappings: list[_VariableMapping] = Field(min_length=1)
    reference_state: _InitialReference | _PredecessorReference
    checks: list[_ContinuityCheckV2 | _ConservationCheckV2]


class _StagePlanDefinitionV1(_StrictModel):
    plan_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    stages: list[_Stage] = Field(min_length=1)


class _StagePlanDefinitionV2(_StrictModel):
    version: Literal[2]
    plan_id: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    stages: list[_StageV2] = Field(min_length=1)


class StagePlanDefinition(RootModel[_StagePlanDefinitionV1 | _StagePlanDefinitionV2]):
    """Public MCP schema for v1 declarations and strict version-2 plans."""

    model_config = ConfigDict(strict=True)


def _fail(message: str) -> None:
    raise ExecutionContractError("INVALID_REQUEST", message)


def _finite_number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        _fail(f"{label} must be a finite{' non-negative' if nonnegative else ''} number")
    return number


def _validate_solution_spec(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a SolutionSpec object")
    allowed = {"dataset", "solution", "inner", "outer", "time", "frequency", "parameters"}
    if set(value) - allowed:
        _fail(f"{label} contains unsupported SolutionSpec fields")
    dataset = value.get("dataset")
    if not isinstance(dataset, str) or not dataset.strip():
        _fail(f"{label}.dataset must be a non-empty string")
    result: dict[str, Any] = {"dataset": dataset}
    if "solution" in value:
        solution = value["solution"]
        if not isinstance(solution, str) or not solution.strip():
            _fail(f"{label}.solution must be a non-empty string")
        result["solution"] = solution
    for axis in ("inner", "outer"):
        if axis not in value:
            continue
        selector = value[axis]
        if isinstance(selector, str):
            if selector not in {"all", "first", "last"}:
                _fail(f"{label}.{axis} must be all, first, last, or a positive integer list")
            result[axis] = selector
        elif isinstance(selector, list) and selector:
            if (any(type(item) is not int or item < 1 for item in selector)
                    or len(set(selector)) != len(selector)):
                _fail(f"{label}.{axis} must contain unique positive integers")
            result[axis] = list(selector)
        else:
            _fail(f"{label}.{axis} must be a selector or non-empty integer list")
    for axis in ("time", "frequency"):
        if axis not in value:
            continue
        rows = value[axis]
        if not isinstance(rows, list) or not rows:
            _fail(f"{label}.{axis} must be a non-empty Quantity array")
        normalized = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or set(row) != {"value", "unit"}:
                _fail(f"{label}.{axis}[{index}] must contain only value and unit")
            number = _finite_number(row["value"], f"{label}.{axis}[{index}].value")
            unit = row["unit"]
            if not isinstance(unit, str) or not unit.strip():
                _fail(f"{label}.{axis}[{index}].unit must be a non-empty string")
            normalized.append({"value": row["value"], "unit": unit})
        result[axis] = normalized
    if "parameters" in value:
        parameters = value["parameters"]
        if not isinstance(parameters, Mapping) or any(not isinstance(k, str) or not k for k in parameters):
            _fail(f"{label}.parameters must be an object with non-empty string keys")
        _validate_json_finite(parameters, f"{label}.parameters")
        result["parameters"] = dict(parameters)
    return result


def _validate_json_finite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{label} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{label} contains a non-string object key")
            _validate_json_finite(item, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_finite(item, f"{label}[{index}]")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        _fail(f"{label} is not JSON-compatible")


def _validate_study_path(value: Any, label: str) -> dict[str, Any]:
    from ._g2_contract import NodePath

    try:
        path = NodePath.from_wire(value, allow_empty=False)
    except ExecutionContractError as exc:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a valid non-empty NodePath") from exc
    return path.as_dict()


def _validate_tolerance(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"absolute", "relative"}:
        _fail(f"{label} must contain exactly absolute and relative")
    absolute = _finite_number(value["absolute"], f"{label}.absolute", nonnegative=True)
    relative = _finite_number(value["relative"], f"{label}.relative", nonnegative=True)
    if absolute == 0 and relative == 0:
        _fail(f"{label} must set at least one tolerance above zero")
    return {"absolute": value["absolute"], "relative": value["relative"]}


def _validate_v1_stage_plan_definition(
    value: Any, *, validate_conservation_units: bool = True,
) -> dict[str, Any]:
    """Validate the original immutable declaration without changing its hash."""
    if not isinstance(value, Mapping) or set(value) != {"plan_id", "stages"}:
        _fail("definition must contain exactly plan_id and stages")
    plan_id = value.get("plan_id")
    if not isinstance(plan_id, str) or not _IDENTIFIER.fullmatch(plan_id):
        _fail("definition.plan_id must be a stable identifier")
    rows = value.get("stages")
    if not isinstance(rows, list) or not rows:
        _fail("definition.stages must be a non-empty array")

    stages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for index, raw in enumerate(rows):
        label = f"definition.stages[{index}]"
        if not isinstance(raw, Mapping):
            _fail(f"{label} must be an object")
        required = {"stage_id", "ordinal", "depends_on", "study_target", "source_selection",
                    "target_selection", "variable_mappings", "reference_state", "checks"}
        if set(raw) != required:
            _fail(f"{label} must contain exactly the declared stage fields")
        stage_id = raw.get("stage_id")
        if not isinstance(stage_id, str) or not _IDENTIFIER.fullmatch(stage_id) or stage_id in seen_ids:
            _fail(f"{label}.stage_id must be unique and stable")
        seen_ids.add(stage_id)
        ordinal = raw.get("ordinal")
        if type(ordinal) is not int or ordinal < 1 or ordinal in seen_ordinals:
            _fail(f"{label}.ordinal must be a unique positive integer")
        seen_ordinals.add(ordinal)
        depends = raw.get("depends_on")
        if (not isinstance(depends, list)
                or any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in depends)
                or len(set(depends)) != len(depends)):
            _fail(f"{label}.depends_on must contain unique stage identifiers")
        source = raw.get("source_selection")
        if not isinstance(source, Mapping):
            _fail(f"{label}.source_selection must be an object")
        if source.get("kind") == "initial_state":
            if set(source) != {"kind", "strategy"} or source.get("strategy") != "declared_initial":
                _fail(f"{label}.source_selection initial_state must use declared_initial")
            normalized_source = {"kind": "initial_state", "strategy": "declared_initial"}
        elif source.get("kind") == "stage":
            if set(source) != {"kind", "stage_id", "selection"}:
                _fail(f"{label}.source_selection stage must contain stage_id and SolutionSpec selection")
            predecessor_id = source.get("stage_id")
            if not isinstance(predecessor_id, str) or not _IDENTIFIER.fullmatch(predecessor_id):
                _fail(f"{label}.source_selection.stage_id must be a stable identifier")
            normalized_source = {"kind": "stage", "stage_id": predecessor_id,
                                 "selection": _validate_solution_spec(source.get("selection"), f"{label}.source_selection.selection")}
        else:
            _fail(f"{label}.source_selection.kind must be initial_state or stage")
        reference = raw.get("reference_state")
        if not isinstance(reference, Mapping):
            _fail(f"{label}.reference_state must be an object")
        if reference.get("strategy") == "initial_state":
            if set(reference) != {"strategy"}:
                _fail(f"{label}.reference_state initial_state has unsupported fields")
            normalized_reference = {"strategy": "initial_state"}
        elif reference.get("strategy") == "predecessor_stage":
            if set(reference) != {"strategy", "source_stage_id"}:
                _fail(f"{label}.reference_state predecessor_stage must name source_stage_id")
            reference_source = reference.get("source_stage_id")
            if not isinstance(reference_source, str) or not _IDENTIFIER.fullmatch(reference_source):
                _fail(f"{label}.reference_state.source_stage_id must be a stable identifier")
            normalized_reference = {"strategy": "predecessor_stage", "source_stage_id": reference_source}
        else:
            _fail(f"{label}.reference_state.strategy must be initial_state or predecessor_stage")

        mappings = raw.get("variable_mappings")
        if not isinstance(mappings, list) or not mappings:
            _fail(f"{label}.variable_mappings must be a non-empty array")
        normalized_mappings: list[dict[str, Any]] = []
        source_names: set[str] = set()
        target_names: set[str] = set()
        for map_index, mapping in enumerate(mappings):
            map_label = f"{label}.variable_mappings[{map_index}]"
            expected_map_keys = {"source_variable", "target_variable", "source_unit", "target_unit", "mapping_method"}
            if not isinstance(mapping, Mapping) or set(mapping) != expected_map_keys:
                _fail(f"{map_label} must contain exactly source/target variables, units, and mapping_method")
            src, dst = mapping.get("source_variable"), mapping.get("target_variable")
            src_unit, dst_unit = mapping.get("source_unit"), mapping.get("target_unit")
            method = mapping.get("mapping_method")
            if (not isinstance(src, str) or not src.strip() or src in source_names
                    or not isinstance(dst, str) or not dst.strip() or dst in target_names):
                _fail(f"{map_label} source and target variables must be non-empty and unique")
            if (not isinstance(src_unit, str) or not src_unit.strip()
                    or not isinstance(dst_unit, str) or not dst_unit.strip()):
                _fail(f"{map_label} units must be non-empty strings")
            if method not in {"identity", "interpolate", "projection"}:
                _fail(f"{map_label}.mapping_method must be identity, interpolate, or projection")
            source_names.add(src)
            target_names.add(dst)
            normalized_mappings.append(dict(mapping))

        checks = raw.get("checks")
        if not isinstance(checks, list):
            _fail(f"{label}.checks must be an array, including when no checks are declared")
        check_ids: set[str] = set()
        normalized_checks: list[dict[str, Any]] = []
        for check_index, check in enumerate(checks):
            check_label = f"{label}.checks[{check_index}]"
            if not isinstance(check, Mapping):
                _fail(f"{check_label} must be an object")
            kind = check.get("kind")
            check_id = check.get("check_id")
            if not isinstance(check_id, str) or not _IDENTIFIER.fullmatch(check_id) or check_id in check_ids:
                _fail(f"{check_label}.check_id must be unique and stable")
            check_ids.add(check_id)
            unit = check.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                _fail(f"{check_label}.unit must be a non-empty comparison unit")
            tolerance = _validate_tolerance(check.get("tolerance"), f"{check_label}.tolerance")
            if kind == "continuity":
                if set(check) != {"kind", "check_id", "source_variable", "target_variable", "unit", "tolerance"}:
                    _fail(f"{check_label} continuity fields are incomplete or unsupported")
                src, dst = check.get("source_variable"), check.get("target_variable")
                mapping = next((row for row in normalized_mappings
                                if row["source_variable"] == src and row["target_variable"] == dst), None)
                if mapping is None or mapping["source_unit"] != unit or mapping["target_unit"] != unit:
                    _fail(f"{check_label} continuity variables and units must bind one declared mapping")
                normalized_checks.append({"kind": kind, "check_id": check_id,
                                          "source_variable": src, "target_variable": dst,
                                          "unit": unit, "tolerance": tolerance})
            elif kind == "conservation":
                if set(check) != {"kind", "check_id", "quantity", "terms", "unit", "tolerance"}:
                    _fail(f"{check_label} conservation fields are incomplete or unsupported")
                quantity = check.get("quantity")
                terms = check.get("terms")
                if not isinstance(quantity, str) or not quantity.strip() or not isinstance(terms, list) or len(terms) < 2:
                    _fail(f"{check_label} conservation requires a quantity and at least two terms")
                normalized_terms = []
                sides: set[str] = set()
                for term_index, term in enumerate(terms):
                    term_label = f"{check_label}.terms[{term_index}]"
                    if not isinstance(term, Mapping) or set(term) != {"side", "variable", "coefficient"}:
                        _fail(f"{term_label} must contain exactly side, variable, and coefficient")
                    side, variable = term.get("side"), term.get("variable")
                    if side not in {"source", "target"}:
                        _fail(f"{term_label}.side must be source or target")
                    allowed_names = source_names if side == "source" else target_names
                    if not isinstance(variable, str) or variable not in allowed_names:
                        _fail(f"{term_label}.variable must reference a declared variable mapping")
                    coefficient = _finite_number(term.get("coefficient"), f"{term_label}.coefficient")
                    sides.add(side)
                    normalized_terms.append({"side": side, "variable": variable,
                                             "coefficient": term["coefficient"]})
                if sides != {"source", "target"}:
                    _fail(f"{check_label} conservation must compare source and target terms")
                for term in normalized_terms:
                    mapping = next((row for row in normalized_mappings if (
                        row["source_variable"] == term["variable"] if term["side"] == "source"
                        else row["target_variable"] == term["variable"])), None)
                    if mapping is None:
                        _fail(f"{check_label} conservation term must reference a declared variable mapping")
                    if (validate_conservation_units
                            and mapping["source_unit" if term["side"] == "source" else "target_unit"] != unit):
                        _fail(f"{check_label} conservation term units must match the declared comparison unit")
                normalized_checks.append({"kind": kind, "check_id": check_id, "quantity": quantity,
                                          "terms": normalized_terms, "unit": unit, "tolerance": tolerance})
            else:
                _fail(f"{check_label}.kind must be continuity or conservation")

        if not isinstance(raw.get("study_target"), Mapping):
            _fail(f"{label}.study_target must be a NodePath")
        normalized = {
            "stage_id": stage_id,
            "ordinal": ordinal,
            "depends_on": list(depends),
            "study_target": _validate_study_path(raw["study_target"], f"{label}.study_target"),
            "source_selection": normalized_source,
            "target_selection": _validate_solution_spec(raw.get("target_selection"), f"{label}.target_selection"),
            "variable_mappings": normalized_mappings,
            "reference_state": normalized_reference,
            "checks": normalized_checks,
        }
        stages.append(normalized)

    stages_by_id = {stage["stage_id"]: stage for stage in stages}
    ordinal_by_id = {stage["stage_id"]: stage["ordinal"] for stage in stages}
    first_stage = min(stages, key=lambda stage: stage["ordinal"])
    if first_stage["source_selection"].get("kind") != "initial_state" or first_stage["depends_on"]:
        _fail("the first ordinal stage must declare initial_state and have no dependencies")
    if first_stage["reference_state"].get("strategy") != "initial_state":
        _fail("the first ordinal stage must use initial_state reference_state")
    for stage in stages:
        stage_id = stage["stage_id"]
        dependencies = stage["depends_on"]
        for dependency in dependencies:
            if dependency not in stages_by_id:
                _fail(f"stage {stage_id} depends on an unknown stage")
            if ordinal_by_id[dependency] >= stage["ordinal"]:
                _fail(f"stage {stage_id} dependencies must have smaller ordinals")
        source = stage["source_selection"]
        reference = stage["reference_state"]
        if stage is first_stage:
            continue
        if source.get("kind") != "stage":
            _fail(f"non-initial stage {stage_id} must declare a predecessor stage source")
        source_stage_id = source["stage_id"]
        if source_stage_id not in dependencies:
            _fail(f"stage {stage_id} source stage must be listed in depends_on")
        if reference.get("strategy") != "predecessor_stage" or reference.get("source_stage_id") != source_stage_id:
            _fail(f"stage {stage_id} reference_state must bind its declared predecessor source")

    normalized = {"plan_id": plan_id, "stages": stages}
    # Ensure every value is canonical JSON now; do not defer NaN/object failures
    # until a store write has begun.
    try:
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        _fail("definition must be finite JSON data")
    return normalized


def _validate_single_solution_spec(value: Any, label: str) -> dict[str, Any]:
    """Require a SolutionSpec that selects at most one native solution tuple."""
    normalized = _validate_solution_spec(value, label)
    for axis in ("inner", "outer"):
        selector = normalized.get(axis)
        if selector == "all":
            _fail(f"{label}.{axis} must select one exact solution tuple, not all")
        if isinstance(selector, list) and len(selector) != 1:
            _fail(f"{label}.{axis} must contain exactly one index")
    for axis in ("time", "frequency"):
        selected = normalized.get(axis)
        if isinstance(selected, list) and len(selected) != 1:
            _fail(f"{label}.{axis} must contain exactly one quantity")
    return normalized


def _validate_v2_quantity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"value", "unit"}:
        _fail(f"{label} must contain exactly value and unit")
    _finite_number(value.get("value"), f"{label}.value")
    unit = value.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        _fail(f"{label}.unit must be a non-empty string")
    return {"value": value["value"], "unit": unit}


def _validate_v2_selection(value: Any, label: str) -> dict[str, Any]:
    try:
        from ._g3_common import validate_selection_spec
        normalized = validate_selection_spec(value, label=label)
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} is not a SelectionSpec") from exc
    _validate_json_finite(normalized, label)
    return normalized


def _validate_v2_target_variables(value: Any, label: str) -> dict[str, Any]:
    normalized = _validate_study_path(value, label)
    segments = normalized.get("segments")
    if (not isinstance(segments, list) or len(segments) != 2
            or segments[0].get("collection") != "sol"
            or segments[1].get("collection") != "feature"):
        _fail(f"{label} must identify an explicit sol:<tag>/feature:<Variables-tag> NodePath")
    return normalized


def _validate_v2_stage_plan_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"version", "plan_id", "stages"} or type(value.get("version")) is not int or value.get("version") != 2:
        _fail("version-2 definition must contain exactly version=2, plan_id, and stages")
    plan_id = value.get("plan_id")
    rows = value.get("stages")
    if not isinstance(plan_id, str) or not _IDENTIFIER.fullmatch(plan_id):
        _fail("definition.plan_id must be a stable identifier")
    if not isinstance(rows, list) or not rows:
        _fail("definition.stages must be a non-empty array")

    legacy_rows: list[dict[str, Any]] = []
    raw_checks: list[list[Mapping[str, Any]]] = []
    target_variables: list[dict[str, Any] | None] = []
    for index, raw in enumerate(rows):
        label = f"definition.stages[{index}]"
        if not isinstance(raw, Mapping):
            _fail(f"{label} must be an object")
        expected = {"stage_id", "ordinal", "depends_on", "study_target", "source_selection",
                    "target_selection", "mapping_profile", "variable_mappings", "reference_state", "checks"}
        if "target_variables" in raw:
            expected.add("target_variables")
        if set(raw) != expected:
            _fail(f"{label} must contain exactly the declared version-2 stage fields")
        source = raw.get("source_selection")
        if not isinstance(source, Mapping):
            _fail(f"{label}.source_selection must be an object")
        is_successor = source.get("kind") == "stage"
        if is_successor and "target_variables" not in raw:
            _fail(f"{label}.target_variables is required for a predecessor-sourced stage")
        if not is_successor and "target_variables" in raw:
            _fail(f"{label}.target_variables is only valid for a predecessor-sourced stage")
        target_variables.append(
            _validate_v2_target_variables(raw["target_variables"], f"{label}.target_variables")
            if is_successor else None
        )

        checks = raw.get("checks")
        if not isinstance(checks, list):
            _fail(f"{label}.checks must be an array")
        raw_checks_for_stage: list[Mapping[str, Any]] = []
        legacy_stage = {key: raw[key] for key in (
            "stage_id", "ordinal", "depends_on", "study_target", "source_selection",
            "target_selection", "variable_mappings", "reference_state",
        )}
        legacy_checks: list[dict[str, Any]] = []
        for check_index, check in enumerate(checks):
            check_label = f"{label}.checks[{check_index}]"
            if not isinstance(check, Mapping):
                _fail(f"{check_label} must be an object")
            raw_checks_for_stage.append(check)
            kind = check.get("kind")
            if kind == "continuity":
                fields = {"kind", "check_id", "operator", "source_variable", "target_variable",
                          "source_solution", "target_solution", "source_selection", "target_selection",
                          "frame", "boundary_time", "unit", "tolerance"}
                if set(check) != fields:
                    _fail(f"{check_label} continuity fields are incomplete or unsupported")
                legacy_checks.append({key: check[key] for key in
                                      ("kind", "check_id", "source_variable", "target_variable", "unit", "tolerance")})
            elif kind == "conservation":
                fields = {"kind", "check_id", "operator", "quantity", "source_solution", "target_solution",
                          "terms", "unit", "tolerance"}
                if set(check) != fields:
                    _fail(f"{check_label} conservation fields are incomplete or unsupported")
                if not isinstance(check.get("terms"), list):
                    _fail(f"{check_label}.terms must be an array")
                legacy_terms = []
                for term_index, term in enumerate(check["terms"]):
                    term_label = f"{check_label}.terms[{term_index}]"
                    if not isinstance(term, Mapping) or set(term) != {
                        "side", "variable", "coefficient", "selection", "entity_dimension"
                    }:
                        _fail(f"{term_label} must contain exactly side, variable, coefficient, selection, and entity_dimension")
                    legacy_terms.append({key: term[key] for key in ("side", "variable", "coefficient")})
                legacy_checks.append({"kind": kind, "check_id": check["check_id"],
                                      "quantity": check.get("quantity"), "terms": legacy_terms,
                                      "unit": check.get("unit"), "tolerance": check.get("tolerance")})
            else:
                _fail(f"{check_label}.kind must be continuity or conservation")
        legacy_stage["checks"] = legacy_checks
        legacy_rows.append(legacy_stage)
        raw_checks.append(raw_checks_for_stage)

    # v1 treats conservation ``unit`` as the field unit and keeps that exact
    # legacy contract. In v2 it is the integrated comparison unit, which may
    # include geometric dimensions. The declaration is not native unit
    # readback; runtime evaluation must verify each integral's actual unit.
    base = _validate_v1_stage_plan_definition(
        {"plan_id": plan_id, "stages": legacy_rows},
        validate_conservation_units=False,
    )
    normalized_stages: list[dict[str, Any]] = []
    for index, (raw, stage, stage_checks) in enumerate(zip(rows, base["stages"], raw_checks)):
        label = f"definition.stages[{index}]"
        if raw.get("mapping_profile") != "same_name_same_mesh_initialization":
            _fail(f"{label}.mapping_profile must be same_name_same_mesh_initialization")
        for map_index, mapping in enumerate(stage["variable_mappings"]):
            if (mapping["mapping_method"] != "identity"
                    or mapping["source_variable"] != mapping["target_variable"]
                    or mapping["source_unit"] != mapping["target_unit"]):
                _fail(f"{label}.variable_mappings[{map_index}] is outside the identity/same-unit v2 profile")
        if stage["source_selection"]["kind"] == "stage":
            stage["source_selection"]["selection"] = _validate_single_solution_spec(
                raw["source_selection"]["selection"], f"{label}.source_selection.selection"
            )
        stage["target_selection"] = _validate_single_solution_spec(
            raw["target_selection"], f"{label}.target_selection"
        )

        normalized_checks: list[dict[str, Any]] = []
        mappings = stage["variable_mappings"]
        for check_index, check in enumerate(stage_checks):
            check_label = f"{label}.checks[{check_index}]"
            if check["kind"] == "continuity":
                if check.get("operator") != "pointwise_max_abs":
                    _fail(f"{check_label}.operator must be pointwise_max_abs")
                source_variable = check.get("source_variable")
                target_variable = check.get("target_variable")
                mapping = next((row for row in mappings if row["source_variable"] == source_variable
                                and row["target_variable"] == target_variable), None)
                if mapping is None or mapping["source_unit"] != check.get("unit") or mapping["target_unit"] != check.get("unit"):
                    _fail(f"{check_label} variables and unit must bind one identity mapping")
                frame = check.get("frame")
                if frame not in {"spatial", "material", "geometry", "mesh"}:
                    _fail(f"{check_label}.frame must be spatial, material, geometry, or mesh")
                source_solution = _validate_single_solution_spec(check.get("source_solution"), f"{check_label}.source_solution")
                target_solution = _validate_single_solution_spec(check.get("target_solution"), f"{check_label}.target_solution")
                boundary_time = _validate_v2_quantity(check.get("boundary_time"), f"{check_label}.boundary_time")
                for axis, solution in (("source_solution", source_solution), ("target_solution", target_solution)):
                    selected_times = solution.get("time")
                    if selected_times and (selected_times[0]["unit"] != boundary_time["unit"]
                            or not math.isclose(float(selected_times[0]["value"]), float(boundary_time["value"]),
                                                rel_tol=1e-12, abs_tol=1e-15)):
                        _fail(f"{check_label}.{axis}.time must match boundary_time exactly within numeric tolerance")
                normalized_checks.append({
                    "kind": "continuity", "check_id": check["check_id"],
                    "operator": "pointwise_max_abs", "source_variable": source_variable,
                    "target_variable": target_variable, "source_solution": source_solution,
                    "target_solution": target_solution,
                    "source_selection": _validate_v2_selection(check.get("source_selection"), f"{check_label}.source_selection"),
                    "target_selection": _validate_v2_selection(check.get("target_selection"), f"{check_label}.target_selection"),
                    "frame": frame, "boundary_time": boundary_time, "unit": check["unit"],
                    "tolerance": _validate_tolerance(check.get("tolerance"), f"{check_label}.tolerance"),
                })
            else:
                if check.get("operator") != "native_integral":
                    _fail(f"{check_label}.operator must be native_integral")
                source_solution = _validate_single_solution_spec(check.get("source_solution"), f"{check_label}.source_solution")
                target_solution = _validate_single_solution_spec(check.get("target_solution"), f"{check_label}.target_solution")
                normalized_terms = []
                coefficients: list[float] = []
                for term_index, term in enumerate(check["terms"]):
                    term_label = f"{check_label}.terms[{term_index}]"
                    coefficient = _finite_number(term["coefficient"], f"{term_label}.coefficient")
                    side = term["side"]
                    dimension = term.get("entity_dimension")
                    if type(dimension) is not int or dimension < 0 or dimension > 3:
                        _fail(f"{term_label}.entity_dimension must be an integer from 0 through 3")
                    selection = _validate_v2_selection(term["selection"], f"{term_label}.selection")
                    if dimension != selection.get("entity_dimension", dimension):
                        _fail(f"{term_label}.selection.entity_dimension conflicts with term entity_dimension")
                    if selection.get("kind") in {"explicit", "all"} and "entity_dimension" not in selection:
                        _fail(f"{term_label}.selection must declare entity_dimension for {selection.get('kind')}")
                    mapping = next((row for row in mappings if (
                        row["source_variable"] == term["variable"] if side == "source"
                        else row["target_variable"] == term["variable"])), None)
                    if mapping is None:
                        _fail(f"{term_label} variable must reference a declared mapping on its side")
                    # The term's mapped field unit and the declared integrated
                    # result unit need not match (for example K integrated
                    # over volume has unit K*m^3). A real native integral
                    # readback is required before this check can be evaluated.
                    coefficients.append(coefficient)
                    normalized_terms.append({"side": side, "variable": term["variable"],
                                             "coefficient": term["coefficient"],
                                             "selection": selection,
                                             "entity_dimension": term["entity_dimension"]})
                if all(coefficient == 0 for coefficient in coefficients):
                    _fail(f"{check_label} cannot consist only of zero coefficients")
                normalized_checks.append({
                    "kind": "conservation", "check_id": check["check_id"],
                    "operator": "native_integral", "quantity": check["quantity"],
                    "source_solution": source_solution, "target_solution": target_solution,
                    "terms": normalized_terms, "unit": check["unit"],
                    "tolerance": _validate_tolerance(check.get("tolerance"), f"{check_label}.tolerance"),
                })

        source = stage["source_selection"]
        if normalized_checks and source["kind"] != "stage":
            _fail(f"{label} cannot bind solution checks to an initial_state with no persisted source attempt")
        if source["kind"] == "stage":
            expected_source_solution = source["selection"]
            for check_index, check in enumerate(normalized_checks):
                if canonical_json(check["source_solution"]) != canonical_json(expected_source_solution):
                    _fail(f"{label}.checks[{check_index}].source_solution must exactly match the declared predecessor SolutionSpec")
                if check["kind"] == "conservation" and canonical_json(check["target_solution"]) != canonical_json(stage["target_selection"]):
                    _fail(f"{label}.checks[{check_index}].target_solution must exactly match the declared stage output SolutionSpec")

        if target_variables[index] is not None:
            stage["target_variables"] = target_variables[index]
        stage["mapping_profile"] = "same_name_same_mesh_initialization"
        stage["checks"] = normalized_checks
        normalized_stages.append(stage)
    normalized = {"version": 2, "plan_id": plan_id, "stages": normalized_stages}
    try:
        canonical_json(normalized)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError("INVALID_REQUEST", "definition must be finite JSON data") from exc
    return normalized


def validate_stage_plan_definition(value: Any) -> dict[str, Any]:
    """Validate v1 declarations unchanged or explicit strict v2 plans."""
    if not isinstance(value, Mapping):
        _fail("definition must be an object")
    if "version" not in value:
        return _validate_v1_stage_plan_definition(value)
    if type(value.get("version")) is not int or value.get("version") != 2:
        _fail("definition.version must be the integer 2")
    return _validate_v2_stage_plan_definition(value)


def normalize_state_map_request(value: Any) -> dict[str, Any]:
    """Validate the narrow documented initial-solution selector profile.

    This proves request shape and declared same-name/unit constraints only. It
    does not attest native variable units, field identity, mesh equivalence,
    frame mapping, or preservation of hidden solver history.
    """
    from ._g2_contract import NodePath

    identity_fields = {
        "project_id", "session_id", "model_ref", "expected_revision",
        "idempotency_key", "request_id",
    }
    if not isinstance(value, Mapping) or set(value) - (identity_fields | {"source", "target", "mapping"}):
        _fail("state_map accepts only source, target, mapping, and the managed execution identity")
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) - {"dataset", "solution", "inner", "outer", "time"}:
        _fail("source must be one exact supported SolutionSpec tuple")
    if not isinstance(source.get("dataset"), str) or not source["dataset"]:
        _fail("source.dataset must be a non-empty dataset tag")
    solution = source.get("solution")
    if solution is not None and (not isinstance(solution, str) or not solution):
        _fail("source.solution must be a non-empty solver sequence tag when supplied")

    def exact_index(name: str) -> int:
        raw = source.get(name)
        if isinstance(raw, list):
            if len(raw) != 1:
                _fail(f"source.{name} must identify exactly one solution index")
            raw = raw[0]
        if type(raw) is not int or raw < 1:
            _fail(f"source.{name} must be a positive exact integer")
        return raw

    outer = exact_index("outer") if "outer" in source else None
    inner = exact_index("inner") if "inner" in source else None
    time_items = source.get("time")
    if time_items is not None:
        if (not isinstance(time_items, list) or len(time_items) != 1
                or not isinstance(time_items[0], Mapping) or set(time_items[0]) != {"value", "unit"}):
            _fail("source.time must contain one finite unit-bearing Quantity")
        time_value = time_items[0].get("value")
        time_unit = time_items[0].get("unit")
        if (isinstance(time_value, bool) or not isinstance(time_value, (int, float))
                or not math.isfinite(float(time_value)) or not isinstance(time_unit, str) or not time_unit):
            _fail("source.time must contain one finite unit-bearing Quantity")
        if inner is not None:
            _fail("select one exact inner index or one exact source.time Quantity, not both")
    else:
        time_items = None
        if inner is None:
            _fail("source must select one exact inner index or one exact source.time Quantity")
    if outer is None:
        _fail("source.outer must identify one exact outer solution")

    target_value = value.get("target")
    path = NodePath.from_wire(target_value, allow_empty=False)
    if (len(path.segments) != 2
            or path.segments[0].collection != "sol"
            or path.segments[1].collection != "feature"):
        _fail("target must be a NodePath sol:<tag>/feature:<Variables-tag>")

    mapping = value.get("mapping")
    if (not isinstance(mapping, Mapping) or set(mapping) != {"profile", "variables"}
            or mapping.get("profile") != "same_name_same_mesh_initialization"
            or not isinstance(mapping.get("variables"), list) or not mapping["variables"]):
        _fail("mapping requires the same_name_same_mesh_initialization profile and a non-empty variables list")
    normalized_variables: list[dict[str, str]] = []
    seen: set[str] = set()
    required_mapping_fields = {
        "source_variable", "target_variable", "source_unit", "target_unit", "mapping_method",
    }
    for index, item in enumerate(mapping["variables"]):
        if not isinstance(item, Mapping) or set(item) != required_mapping_fields:
            _fail(f"mapping.variables[{index}] has unknown or missing fields")
        source_variable = item.get("source_variable")
        target_variable = item.get("target_variable")
        source_unit = item.get("source_unit")
        target_unit = item.get("target_unit")
        if (not isinstance(source_variable, str) or not source_variable
                or not isinstance(target_variable, str) or not target_variable
                or not isinstance(source_unit, str) or not source_unit
                or not isinstance(target_unit, str) or not target_unit
                or item.get("mapping_method") != "identity"):
            _fail(f"mapping.variables[{index}] must declare a same-name identity mapping with units")
        if source_variable != target_variable:
            _fail(f"mapping.variables[{index}] identity mapping must preserve the variable name")
        if source_unit != target_unit:
            _fail(f"mapping.variables[{index}] identity mapping must declare exactly matching units")
        if source_variable in seen:
            _fail(f"mapping.variables contains duplicate variable {source_variable!r}")
        seen.add(source_variable)
        normalized_variables.append({
            "source_variable": source_variable,
            "target_variable": target_variable,
            "source_unit": source_unit,
            "target_unit": target_unit,
            "mapping_method": "identity",
        })

    normalized_source: dict[str, Any] = {"dataset": source["dataset"], "outer": outer}
    if solution is not None:
        normalized_source["solution"] = solution
    if inner is not None:
        normalized_source["inner"] = inner
    if time_items is not None:
        normalized_source["time"] = [{"value": float(time_items[0]["value"]), "unit": time_items[0]["unit"]}]
    return {
        "source": normalized_source,
        "target": path.as_dict(),
        "mapping": {
            "profile": "same_name_same_mesh_initialization",
            "variables": normalized_variables,
        },
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
