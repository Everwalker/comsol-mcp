"""W21-authenticated native deformation mapping plan and error gate for W23.

This module never accepts displacement arrays as input.  The plan builder is
fed the durable W21 checkpoint and its registered W17 observation record; the
native Java adapter consumes only the resulting source tags, selections, and
field expressions.  It configures COMSOL General Extrusion and Prescribed
Deformation, but it does not start a study or solver.

Unit-test results from this module are SOFTWARE_ONLY.  Native status remains
NOT_RUN until source and mapped fields are evaluated by COMSOL and independently
compared at held-out coordinates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


class DeformationMappingError(ValueError):
    """The native deformation source or destination is not fully bound."""


_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_EXPRESSION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FRAMES = {"material"}
_AXES = ("x", "y", "z")
_MAPPING_PROFILE = "w23.general_extrusion.withsol.v1"


def _fail(message: str) -> None:
    raise DeformationMappingError(message)


def _digest(record: Mapping[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "sha256"}
    raw = json.dumps(body, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_digest(value: Any) -> str:
    """Match the canonical JSON digest used by the W21 observation identity."""
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_tag(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TAG.fullmatch(value):
        _fail(f"{label} must be a COMSOL tag using letters, digits, and underscore")
    return value


def _require_native_selection(selection: Any, label: str) -> dict[str, Any]:
    if not isinstance(selection, Mapping):
        _fail(f"{label} requires an exact native geometry-selection readback")
    if selection.get("entity_dimension") != 3:
        _fail(f"{label} must read back as a 3D domain selection")
    entities = selection.get("entity_ids")
    if (not isinstance(entities, list) or not entities
            or any(type(entity) is not int or entity < 1 for entity in entities)
            or len(set(entities)) != len(entities)):
        _fail(f"{label} must contain unique positive native entity IDs")
    result = dict(selection)
    result["entity_ids"] = sorted(entities)
    return result


def _time_binding(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    timestamp = checkpoint.get("timestamp_s")
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))):
        _fail("W21 checkpoint must carry its actual finite stored terminal time in seconds")
    indices = checkpoint.get("solution_indices")
    if not isinstance(indices, Mapping) or indices.get("binding_complete") is not True:
        _fail("W21 checkpoint lacks a complete native dataset/solution/index binding")
    if indices.get("dataset") != checkpoint.get("source_dataset"):
        _fail("W21 checkpoint dataset does not match its native solution-index receipt")
    if indices.get("solution") != checkpoint.get("source_solution"):
        _fail("W21 checkpoint solution does not match its native solution-index receipt")
    pairs = indices.get("solnum_pairs")
    if not isinstance(pairs, list):
        _fail("W21 checkpoint has no complete outer/inner-to-solnum map")
    field_array = checkpoint.get("field_array")
    coords = field_array.get("coords") if isinstance(field_array, Mapping) else None
    outer_axis = coords.get("outer") if isinstance(coords, Mapping) else None
    if not isinstance(outer_axis, list) or len(outer_axis) != 1:
        _fail("W21 stage checkpoint must identify exactly one selected outer solution")
    outer = outer_axis[0]
    if type(outer) is not int or outer < 1:
        _fail("W21 checkpoint outer solution index is invalid")
    parameters = indices.get("parameters")
    by_pair = parameters.get("by_pair") if isinstance(parameters, Mapping) else None
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for pair in pairs:
        if not isinstance(pair, Mapping) or pair.get("outer") != outer:
            continue
        key = f"{outer}:{pair.get('inner')}"
        metadata = by_pair.get(key) if isinstance(by_pair, Mapping) else None
        if not isinstance(metadata, Mapping):
            continue
        names, values, units = metadata.get("names"), metadata.get("values"), metadata.get("units")
        if not (isinstance(names, list) and isinstance(values, list) and isinstance(units, list)
                and len(names) == len(values) == len(units)):
            continue
        time_rows = [(name, value, unit) for name, value, unit in zip(names, values, units)
                     if isinstance(name, str) and name.lower() in {"t", "time"}]
        if len(time_rows) != 1:
            continue
        name, value, unit = time_rows[0]
        if (unit not in {"s", "sec"} or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(float(value))):
            continue
        if not math.isclose(float(value), float(timestamp), rel_tol=1e-12, abs_tol=1e-15):
            continue
        if type(pair.get("inner")) is not int or pair["inner"] < 1:
            continue
        if type(pair.get("solnum")) is not int or pair["solnum"] < 1:
            continue
        matches.append((pair, metadata))
    if len(matches) != 1:
        _fail("W21 checkpoint time must resolve to exactly one actual outer/inner/solnum row")
    pair, metadata = matches[0]
    sweep_parameters = []
    names, values, units = metadata["names"], metadata["values"], metadata["units"]
    for name, value, unit in zip(names, values, units):
        if isinstance(name, str) and name.lower() in {"t", "time"}:
            continue
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) or not isinstance(unit, str) or not unit):
            _fail("W21 outer-sweep parameters must have exact names, finite values, and units")
        sweep_parameters.append({"name": name, "value": float(value), "unit": unit})
    return {
        "outer": outer,
        "inner": pair["inner"],
        "solnum": pair["solnum"],
        "time_s": float(timestamp),
        "time_index": pair["inner"],
        "time_selection": "setind(t,inner); exact stored time independently checked",
        "outer_parameters": sweep_parameters,
    }


def build_native_mapping_plan(
    checkpoint: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    expected_model_ref: Mapping[str, Any],
    expected_project_id: str,
    expected_revision: int,
    expected_model_tag: str,
    source_native: Mapping[str, Any],
    target_native: Mapping[str, Any],
    displacement_expressions: Mapping[str, str],
) -> dict[str, Any]:
    """Build a no-solve General Extrusion plan from authenticated W21 records.

    ``checkpoint`` and ``observation`` must first be resolved by the managed
    backend from the exact registered project's OperationStore/ObservationStore.
    The W21 checkpoint itself has a ModelRef but no project/revision fields;
    those are bound through the authenticated managed context and the W17
    observation's stored revision.  This function does not read a caller-
    selected store.  ``source_native`` and ``target_native`` are exact COMSOL
    readbacks from the same bound model, never arrays supplied as field values.
    """
    if not isinstance(checkpoint, Mapping) or checkpoint.get("kind") != "w21stage":
        _fail("a registered W21 stage checkpoint is required")
    if checkpoint.get("sha256") != _digest(checkpoint):
        _fail("W21 stage checkpoint integrity check failed")
    if checkpoint.get("status") != "COMPLETE" or checkpoint.get("checkpoint_status") != "CHECKPOINT_CREATED":
        _fail("only a completed W21 checkpoint can source a mapping")
    if not isinstance(expected_project_id, str) or not expected_project_id:
        _fail("managed execution must supply the exact registered project id")
    if type(expected_revision) is not int or expected_revision < 0:
        _fail("managed execution must supply the exact nonnegative model revision")
    if checkpoint.get("model_ref") != dict(expected_model_ref):
        _fail("W21 checkpoint belongs to another managed ModelRef")
    if not isinstance(observation, Mapping) or observation.get("kind") != "w17_observation":
        _fail("the W21 checkpoint's registered W17 ObservationRef must resolve")
    if observation.get("model_ref") != dict(expected_model_ref):
        _fail("source W17 observation belongs to another managed ModelRef")
    if observation.get("model_tag") != expected_model_tag:
        _fail("source W17 observation belongs to another model tag")
    if observation.get("revision") != expected_revision:
        _fail("source W17 observation is stale for the managed model revision")
    # Some future checkpoint schemas may duplicate these identities.  If they
    # do, they must agree with the authoritative execution context; absence is
    # expected in the current W21 schema and is not treated as project proof.
    if "project_id" in checkpoint and checkpoint.get("project_id") != expected_project_id:
        _fail("W21 checkpoint project attribution conflicts with managed execution")
    if "revision" in checkpoint and checkpoint.get("revision") != expected_revision:
        _fail("W21 checkpoint revision conflicts with managed execution")
    observation_ref = checkpoint.get("observation_ref")
    if (not isinstance(observation_ref, Mapping)
            or observation_ref != {"observation_id": observation.get("observation_id"),
                                  "sha256": observation.get("sha256")}):
        _fail("W21 checkpoint does not bind the exact registered W17 ObservationRef")
    artifact = observation.get("artifact")
    if not isinstance(artifact, Mapping) or observation.get("sha256") != artifact.get("sha256"):
        _fail("source W17 artifact hash binding is incomplete")
    if not isinstance(observation.get("sha256"), str) or not _SHA256.fullmatch(observation["sha256"]):
        _fail("source W17 observation hash is malformed")
    if (checkpoint.get("source_solution") != observation.get("solution")
            or checkpoint.get("source_dataset") != observation.get("dataset")):
        _fail("W21 stage solution/dataset differs from its registered W17 source")
    source_identity_record = observation.get("source_identity")
    if not isinstance(source_identity_record, Mapping):
        _fail("registered W17 observation has no stored native solution identity")
    if source_identity_record.get("solution") != observation.get("solution"):
        _fail("W17 native solution identity names another solution")
    if source_identity_record.get("study") != checkpoint.get("source_study"):
        _fail("W17 native solution identity names another source study")
    source_identity_sha256 = _canonical_digest(source_identity_record)

    if not isinstance(source_native, Mapping) or not isinstance(target_native, Mapping):
        _fail("source and target native geometry readbacks are required")
    if source_native.get("model_tag") != expected_model_tag or target_native.get("model_tag") != expected_model_tag:
        _fail("General Extrusion source and destination must be in the same bound COMSOL Model")
    source_component = _require_tag(source_native.get("component_tag"), "source component")
    target_component = _require_tag(target_native.get("component_tag"), "target component")
    source_geometry = _require_tag(source_native.get("geometry_tag"), "source geometry")
    target_geometry = _require_tag(target_native.get("geometry_tag"), "target geometry")
    source_selection_tag = _require_tag(source_native.get("selection_tag"), "source selection")
    target_selection_tag = _require_tag(target_native.get("selection_tag"), "target selection")
    component_tags = source_native.get("component_tags")
    if (not isinstance(component_tags, list) or not component_tags
            or any(not isinstance(tag, str) or not _TAG.fullmatch(tag) for tag in component_tags)
            or len(component_tags) != len(set(component_tags))
            or source_component not in component_tags or target_component not in component_tags):
        _fail("native model component-tag inventory must bind both source and destination components")

    source_vars = checkpoint.get("sample_request", {}).get("spec", {}).get("expressions") \
        if isinstance(checkpoint.get("sample_request"), Mapping) else None
    if (not isinstance(source_vars, list) or any(not isinstance(item, str) for item in source_vars)
            or len(source_vars) != len(set(source_vars))):
        _fail("W21 checkpoint must retain a unique native expression list")
    if not isinstance(displacement_expressions, Mapping) or set(displacement_expressions) != set(_AXES):
        _fail("displacement_expressions must bind x/y/z to authenticated W21 expressions")
    chosen_input: dict[str, str] = {}
    chosen: dict[str, str] = {}
    for axis in _AXES:
        expr = displacement_expressions[axis]
        if not isinstance(expr, str) or not _EXPRESSION.fullmatch(expr):
            _fail(f"native displacement expression for {axis} is not a simple verified field identifier")
        qualified = [tag for tag in component_tags if expr.startswith(tag + ".")]
        if qualified and qualified != [source_component]:
            _fail(f"W21 displacement expression for {axis} is qualified to a non-source component")
        source_field = expr[len(source_component) + 1:] if qualified else expr
        if expr not in source_vars and source_field not in source_vars:
            _fail(f"W21 source observation did not sample displacement expression {expr!r}")
        chosen_input[axis] = expr
        chosen[axis] = source_field
        if not _EXPRESSION.fullmatch(source_field):
            _fail(f"W21 displacement expression for {axis} is not a source-component field identifier")
    sample_fa = checkpoint.get("field_array")
    units = sample_fa.get("units", {}).get("expression") if isinstance(sample_fa, Mapping) else None
    if isinstance(units, Mapping):
        unit_for = units
    elif isinstance(units, list) and len(units) == len(source_vars):
        unit_for = dict(zip(source_vars, units))
    else:
        _fail("W21 checkpoint has no expression unit readback")
    if any(unit_for.get(chosen_input[axis] if chosen_input[axis] in unit_for else chosen[axis])
           not in {"m", "meter", "metre"} for axis in _AXES):
        _fail("all three source displacement components must be natively unit-bound to metres")

    source_selection = _require_native_selection(source_native.get("selection"), "source selection")
    target_selection = _require_native_selection(target_native.get("selection"), "target selection")
    source_frame = source_native.get("mesh_frame")
    if source_frame not in _FRAMES:
        _fail("structural displacement mapping must use the material reference frame to avoid deformed-coordinate feedback")
    target_frame = target_native.get("coordinate_frame")
    if target_frame != "spatial":
        _fail("destination optical coordinates must be explicitly bound to the spatial frame")
    if source_native.get("coordinate_unit") != "m" or target_native.get("coordinate_unit") != "m":
        _fail("source and target native geometry coordinates must both read back in metres")
    if source_native.get("vector_basis") != "global_xyz" or target_native.get("vector_basis") != "global_xyz":
        _fail("source displacement components and target deformation must use the same global xyz basis")
    if not _SHA256.fullmatch(str(source_native.get("source_solution_fingerprint", ""))):
        _fail("source native solution fingerprint is required")
    if not _SHA256.fullmatch(str(source_native.get("source_geometry_fingerprint", ""))):
        _fail("source native geometry fingerprint is required")
    if not _SHA256.fullmatch(str(target_native.get("target_geometry_fingerprint", ""))):
        _fail("target native geometry fingerprint is required")
    if source_native.get("source_solution_fingerprint") != source_identity_sha256:
        _fail("native source-solution fingerprint differs from the registered W17 solution identity")
    time_binding = _time_binding(checkpoint)
    source_times = source_identity_record.get("times")
    if (not isinstance(source_times, list) or not source_times
            or time_binding["inner"] > len(source_times)):
        _fail("registered W17 solution identity does not contain the selected stored time index")
    native_time = source_times[time_binding["inner"] - 1]
    if (isinstance(native_time, bool) or not isinstance(native_time, (int, float))
            or not math.isfinite(float(native_time))
            or not math.isclose(float(native_time), time_binding["time_s"], rel_tol=1e-12, abs_tol=1e-15)):
        _fail("W21 SolutionInfo time and registered native solution time index do not match exactly")
    source_solution = _require_tag(checkpoint.get("source_solution"), "source solution")
    source_dataset = _require_tag(checkpoint.get("source_dataset"), "source dataset")
    source_study = _require_tag(checkpoint.get("source_study"), "source study")
    if source_native.get("source_solution_tag") != source_solution:
        _fail("native source solution tag differs from W21 checkpoint")
    if source_native.get("source_dataset_tag") != source_dataset:
        _fail("native source dataset tag differs from W21 checkpoint")
    if source_native.get("source_study_tag") != source_study:
        _fail("native source study association differs from W21 checkpoint")

    source_identity = {
        "project_id": expected_project_id,
        "model_ref": dict(expected_model_ref),
        "model_tag": expected_model_tag,
        "revision": expected_revision,
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "observation_ref": dict(observation_ref),
        "source_solution": source_solution,
        "source_dataset": source_dataset,
        "source_study": source_study,
        "source_solution_fingerprint": source_native["source_solution_fingerprint"],
        "source_geometry_fingerprint": source_native["source_geometry_fingerprint"],
        "source_component": source_component,
        "model_component_tags": list(component_tags),
        "source_geometry": source_geometry,
        "source_selection": source_selection_tag,
        "source_selection_entity_ids": source_selection["entity_ids"],
        "source_frame": source_frame,
        "vector_basis": "global_xyz",
        "coordinate_unit": source_native["coordinate_unit"],
        "coordinates": "spatial metres; identity destination map x,y,z",
        "outer": time_binding["outer"],
        "inner": time_binding["inner"],
        "solnum": time_binding["solnum"],
        "time_s": time_binding["time_s"],
        "outer_parameters": time_binding["outer_parameters"],
        "displacement_expressions": chosen,
        "sampled_expressions": chosen_input,
        "displacement_units": {axis: "m" for axis in _AXES},
    }
    if not source_identity["checkpoint_id"]:
        _fail("registered W21 checkpoint id is missing")
    operator_tag = "w23defmap"
    deformation_tag = "w23prescr"
    # setind(t,inner) prevents implicit interpolation between stored times.
    # Exact non-time outer parameters are taken from SolutionInfo and frozen.
    selectors = [f"setind(t,{time_binding['time_index']})"]
    for item in time_binding["outer_parameters"]:
        selectors.append(f"setval({item['name']},{item['value']:.17g}[{item['unit']}])")
    expressions = {
        # Qualify each symbol at its owning component.  The General Extrusion
        # operator is evaluated in the destination component; the field is
        # read from the exact source solution in its source component.
        axis: (f"{target_component}.{operator_tag}(withsol('{source_solution}',"
              f"{source_component}.{chosen[axis]},{','.join(selectors)}))")
        for axis in _AXES
    }
    plan = {
        "schema_version": 1,
        "profile": _MAPPING_PROFILE,
        "status": "CONFIGURATION_PLAN_ONLY",
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
        "source": source_identity,
        "target": {
            "component": target_component,
            "geometry": target_geometry,
            "selection": target_selection_tag,
            "selection_entity_ids": target_selection["entity_ids"],
            "geometry_fingerprint": target_native["target_geometry_fingerprint"],
            "coordinate_frame": "spatial",
            "coordinate_unit": "m",
            "vector_basis": "global_xyz",
        },
        "native_adapter": {
            "operation": "GeneralExtrusion",
            "operator_tag": operator_tag,
            "operator_component": target_component,
            "source_expression_component": source_component,
            "expression_order": "targetComponent.operator(withsol(sourceSolution,sourceComponent.field,selectors))",
            "destination_map": ["x", "y", "z"],
            "source_frame": source_frame,
            "use_source_map": False,
            "mesh_search_method": "usetol",
            "outside_source": "NaN and refusal; nearest-point fallback disabled",
            "extrapolation_tolerance": 0.0,
            "deformation_feature": "PrescribedDeformation",
            "deformation_tag": deformation_tag,
            "deformation_expressions": expressions,
            "deformation_unit": "m",
        },
        "held_out_validation": {
            "source_route": "direct source Dataset point sampling at frozen coordinates; no GeneralExtrusion",
            "mapped_route": "destination component evaluation of configured GeneralExtrusion expressions",
            "coordinate_match_required": True,
            "residual_definition": "max_i ||u_map(x_i)-u_source(x_i)||_2 in metres",
            "relative_definition": "max_i(error_i / max(||u_source(x_i)||_2, absolute_tolerance_m))",
            "tolerance_policy": "must be supplied by the frozen native campaign; no result-derived thresholds",
        },
        "mapping_complete": False,
    }
    return plan


def build_native_mapping_configuration_dispatch(
    plan: Mapping[str, Any], *, source_artifact: str,
    request_id: str, idempotency_key: str, expected_revision: int,
) -> dict[str, Any]:
    """Build one managed call for the native GeneralExtrusion adapter.

    Only identities, native selection IDs, and W21-authenticated expression
    names cross this boundary. Displacement samples remain native COMSOL data.
    """
    if (not isinstance(plan, Mapping) or plan.get("profile") != _MAPPING_PROFILE
            or plan.get("status") != "CONFIGURATION_PLAN_ONLY"
            or plan.get("native_result") != "NOT_RUN"
            or plan.get("study_or_solver_invoked") is not False):
        _fail("a software-only no-solve deformation mapping plan is required")
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("registered managed Java source artifact id is required")
    for value, label in ((request_id, "request_id"), (idempotency_key, "idempotency_key")):
        if not isinstance(value, str) or not value.strip():
            _fail(f"{label} is required for mapping configuration dispatch")
    source, target, adapter = plan.get("source"), plan.get("target"), plan.get("native_adapter")
    if not all(isinstance(value, Mapping) for value in (source, target, adapter)):
        _fail("mapping plan must bind source, target, and adapter data")
    if type(expected_revision) is not int or expected_revision != source.get("revision"):
        _fail("managed revision differs from the authenticated W21 mapping source")
    source_ids, target_ids = source.get("source_selection_entity_ids"), target.get("selection_entity_ids")
    if (not isinstance(source_ids, list) or not source_ids
            or not isinstance(target_ids, list) or not target_ids):
        _fail("mapping adapter dispatch requires exact source and target native entity IDs")
    source_expressions = source.get("sampled_expressions")
    source_units = source.get("displacement_units")
    if (not isinstance(source_expressions, Mapping) or set(source_expressions) != set(_AXES)
            or not isinstance(source_units, Mapping) or set(source_units) != set(_AXES)
            or any(source_units.get(axis) != "m" for axis in _AXES)):
        _fail("mapping dispatch requires authenticated xyz displacement expressions in metres")
    outer_parameters = source.get("outer_parameters")
    if not isinstance(outer_parameters, list):
        _fail("mapping dispatch must bind the registered native outer-solution parameters")
    java_args = {
        "phase": "configure", "model_tag": source["model_tag"],
        "source_component": source["source_component"],
        "target_component": target["component"],
        "source_geometry": source["source_geometry"],
        "target_geometry": target["geometry"],
        "source_selection": source["source_selection"],
        "target_selection": target["selection"],
        "source_selection_entity_ids": copy.deepcopy(source_ids),
        "target_selection_entity_ids": copy.deepcopy(target_ids),
        "source_solution": source["source_solution"],
        "source_dataset": source["source_dataset"],
        "source_study": source["source_study"],
        "source_frame": source["source_frame"],
        "source_outer": source["outer"], "source_inner": source["inner"],
        "source_solnum": source["solnum"], "source_time_s": source["time_s"],
        "source_expressions": copy.deepcopy(dict(source_expressions)),
        "source_units": copy.deepcopy(dict(source_units)),
        "outer_parameters": copy.deepcopy(outer_parameters),
        "operator_tag": adapter["operator_tag"],
        "deformation_tag": adapter["deformation_tag"],
        "study_or_solver_invoked": False, "native_result": "NOT_RUN",
    }
    return {
        "operation": "operation_call",
        "arguments": {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": source_artifact,
            "entrypoint": "NativeW23DeformationMappingFixture#run",
            "mode": "trusted", "arguments": java_args}},
        "execution": {"project_id": source["project_id"],
                      "model_ref": copy.deepcopy(source["model_ref"]),
                      "expected_revision": expected_revision,
                      "request_id": request_id,
                      "idempotency_key": idempotency_key},
        "dispatch_scope": "one managed native deformation configuration call; no Study.run or solver call",
        "native_result": "NOT_RUN", "study_or_solver_invoked": False,
    }


def _finite_vector_rows(value: Any, label: str) -> list[tuple[float, float, float]]:
    if not isinstance(value, list) or not value:
        _fail(f"{label} must contain a nonempty native vector sample set")
    rows: list[tuple[float, float, float]] = []
    for index, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            _fail(f"{label}[{index}] must be an xyz vector")
        converted: list[float] = []
        for component in row:
            if (isinstance(component, bool) or not isinstance(component, (int, float))
                    or not math.isfinite(float(component))):
                _fail(f"{label}[{index}] contains nonfinite/native-out-of-domain data")
            converted.append(float(component))
        rows.append(tuple(converted))
    return rows


def _finite_complex_vector_rows(raw: Mapping[str, Any], label: str) -> tuple[
    list[tuple[float, float, float]], list[tuple[float, float, float]]
]:
    """Read explicit real/imag vectors, retaining the historical real-only form."""
    if "displacement_real_m" not in raw:
        return _finite_vector_rows(raw.get("displacement_m"), f"{label} displacement"), []
    real = _finite_vector_rows(raw.get("displacement_real_m"), f"{label} real displacement")
    imaginary = _finite_vector_rows(raw.get("displacement_imag_m"), f"{label} imaginary displacement")
    if len(real) != len(imaginary):
        _fail(f"{label} real/imaginary displacement counts differ")
    return real, imaginary


def compare_native_mapping_samples(
    plan: Mapping[str, Any],
    source_raw: Mapping[str, Any],
    mapped_raw: Mapping[str, Any],
    *,
    absolute_tolerance_m: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    """Compare held-out values from independent native source and mapped routes.

    The direct source receipt must use the W21 dataset/solution without the
    General Extrusion operator.  The destination receipt must use the mapped
    operator.  Identical coordinates, solution/time identity, units, and raw
    engine bindings are mandatory before calculating residuals.
    """
    for value, label in ((absolute_tolerance_m, "absolute_tolerance_m"),
                         (relative_tolerance, "relative_tolerance")):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) or float(value) <= 0):
            _fail(f"{label} must be a positive finite pre-frozen threshold")
    source = plan.get("source") if isinstance(plan, Mapping) else None
    target = plan.get("target") if isinstance(plan, Mapping) else None
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        _fail("mapping plan must bind exact W21 source and native destination identities")
    if source_raw.get("evidence_scope") != "COMSOL_NATIVE_RAW":
        _fail("source held-out samples must be raw COMSOL native output")
    if mapped_raw.get("evidence_scope") != "COMSOL_NATIVE_RAW":
        _fail("mapped held-out samples must be raw COMSOL native output")
    if source_raw.get("route") != "direct_dataset_point_evaluation":
        _fail("source comparator must use direct Dataset evaluation without General Extrusion")
    if mapped_raw.get("route") != "general_extrusion_destination_evaluation":
        _fail("mapped comparator must use the destination General Extrusion route")
    expected_binding = {
        "project_id": source.get("project_id"),
        "model_ref": source.get("model_ref"),
        "model_tag": source.get("model_tag"),
        "checkpoint_id": source.get("checkpoint_id"),
        "observation_ref": source.get("observation_ref"),
        "source_solution": source.get("source_solution"),
        "source_dataset": source.get("source_dataset"),
        "outer": source.get("outer"), "inner": source.get("inner"),
        "solnum": source.get("solnum"), "time_s": source.get("time_s"),
        "source_solution_fingerprint": source.get("source_solution_fingerprint"),
    }
    for raw, label in ((source_raw, "source"), (mapped_raw, "mapped")):
        binding = raw.get("binding")
        if not isinstance(binding, Mapping):
            _fail(f"{label} receipt has no native source binding")
        for key, expected in expected_binding.items():
            if binding.get(key) != expected:
                _fail(f"{label} receipt {key} differs from the frozen W21 source")
        if raw.get("coordinate_unit") != "m" or raw.get("value_unit") != "m":
            _fail(f"{label} coordinates and displacement values must both use metres")
    if source_raw.get("source_expression_route") == "general_extrusion":
        _fail("direct source comparator is circular: it reuses General Extrusion")
    if mapped_raw.get("operator_tag") != plan.get("native_adapter", {}).get("operator_tag"):
        _fail("mapped receipt operator tag differs from the configured General Extrusion")
    coords_source = _finite_vector_rows(source_raw.get("coordinates_m"), "source coordinates")
    coords_mapped = _finite_vector_rows(mapped_raw.get("coordinates_m"), "mapped coordinates")
    if coords_source != coords_mapped:
        _fail("source and mapped held-out coordinates must match exactly")
    values_source, imag_source = _finite_complex_vector_rows(source_raw, "source")
    values_mapped, imag_mapped = _finite_complex_vector_rows(mapped_raw, "mapped")
    if len(coords_source) != len(values_source) or len(coords_source) != len(values_mapped):
        _fail("source/mapped coordinate and displacement counts differ")
    if (imag_source and len(imag_source) != len(values_source)) or (imag_mapped and len(imag_mapped) != len(values_mapped)):
        _fail("source/mapped imaginary displacement counts differ")
    absolute_errors: list[float] = []
    relative_errors: list[float] = []
    absolute_floor = float(absolute_tolerance_m)
    zeros = [(0.0, 0.0, 0.0)] * len(values_source)
    imag_source_values = imag_source or zeros
    imag_mapped_values = imag_mapped or zeros
    for source_vector, mapped_vector, source_imag, mapped_imag in zip(
            values_source, values_mapped, imag_source_values, imag_mapped_values):
        error = math.sqrt(sum((a - b) ** 2 + (ai - bi) ** 2
                              for a, b, ai, bi in zip(source_vector, mapped_vector,
                                                      source_imag, mapped_imag)))
        scale = max(math.sqrt(sum(a * a + ai * ai for a, ai in zip(source_vector, source_imag))),
                    absolute_floor)
        absolute_errors.append(error)
        relative_errors.append(error / scale)
    max_absolute = max(absolute_errors)
    max_relative = max(relative_errors)
    passed = max_absolute <= absolute_tolerance_m and max_relative <= relative_tolerance
    return {
        "status": "SOFTWARE_GATE_PASS" if passed else "SOFTWARE_GATE_FAIL",
        "native_result": "NOT_RUN",
        "evidence_scope": "SOFTWARE_GATE_OVER_NATIVE_RECEIPT_SCHEMA",
        "sample_count": len(coords_source),
        "max_absolute_error_m": max_absolute,
        "absolute_tolerance_m": float(absolute_tolerance_m),
        "max_relative_error": max_relative,
        "relative_tolerance": float(relative_tolerance),
        "source_route": source_raw["route"],
        "mapped_route": mapped_raw["route"],
        "independent_routes": True,
        "mapping_complete": False,
    }
