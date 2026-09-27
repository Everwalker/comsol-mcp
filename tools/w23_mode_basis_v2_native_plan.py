"""Build a software-only expression plan for the dedicated W23 v2 adapter.

This helper prepares exact 3-D IntSurface expressions after solution, dataset,
and surface identities have been read back. It does not dispatch them or
authenticate their results. The registered versioned v2 handler owns the
native integration route; this plan is never a public ``result.evaluate``
request. Every returned plan remains native_result=NOT_RUN and
production_route_status=ROUTE_REGISTERED_NOT_DISPATCHED.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from tools.w23_mode_basis_v2 import BASIS_SCHEMA_ID, build_two_mode_basis_field_contracts


NATIVE_PLAN_SCHEMA_ID = "urn:comsol-mcp:w23:two-mode-native-integral-plan:1.0.0"
NATIVE_PLAN_POLICY = {
    "policy_id": "w23.v2.native_integral_plan_readback_gates.v1",
    "frequency_relative_tolerance": 1e-12,
    "surface_area_relative_tolerance": 1e-10,
    "surface_area_absolute_tolerance_m2": 1e-30,
    "surface_axis_absolute_tolerance": 1e-10,
    "surface_center_absolute_tolerance_um": 1e-9,
    "surface_entity_dimension": 2,
    "native_integral_unit": "W",
    "native_feature_type": "IntSurface",
}

_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_UNIT = re.compile(r"^[A-Za-z0-9_*/^.-]+$")
_FREQUENCY_NAMES = {"f", "freq", "frequency"}
_AXES = ("x", "y", "z")
_FIELD_VARIABLES = {
    "signal": {
        "E": {axis: f"ewfd.E{axis}" for axis in _AXES},
        "H": {axis: f"ewfd.H{axis}" for axis in _AXES},
    },
    "mode": {
        "E": {axis: f"ewfd.Emode{axis}_2" for axis in _AXES},
        "H": {axis: f"ewfd.Hmode{axis}_2" for axis in _AXES},
    },
    "incident": {
        "E": {axis: f"ewfd.Emode{axis}_1" for axis in _AXES},
        "H": {axis: f"ewfd.Hmode{axis}_1" for axis in _AXES},
    },
}


class NativePlanError(ValueError):
    """A source, selection, or expression cannot be safely planned."""


def _fail(message: str) -> None:
    raise NativePlanError(message)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{label} must be finite numeric data")
    return result


def _tag(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TAG.fullmatch(value):
        _fail(f"{label} must be a valid COMSOL tag")
    return value


def _request_sources(request: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    modes = request.get("basis_modes")
    if not isinstance(modes, list) or len(modes) != 2:
        _fail("canonical v2 request must include exactly two mode sources")
    return {
        "signal": ("signal", request["signal_source"]),
        "mode_0": ("mode", modes[0]["source"]),
        "mode_1": ("mode", modes[1]["source"]),
        "incident": ("incident", request["incident_source"]),
    }


def _parameter_records(solution_axes: Mapping[str, Any], label: str) -> tuple[list[str], dict[str, tuple[float, str]]]:
    names = solution_axes.get("parameter_names")
    by_name = solution_axes.get("parameters_by_pair")
    if (not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names)
            or len({name.lower() for name in names}) != len(names)
            or not isinstance(by_name, Mapping) or set(by_name) != set(names)):
        _fail(f"{label} native SolutionInfo parameter metadata is incomplete or duplicated")
    values: dict[str, tuple[float, str]] = {}
    for name in names:
        _tag(name, f"{label} parameter name")
        raw = by_name[name]
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            _fail(f"{label} parameter {name!r} must retain its native value/unit pair")
        value = _number(raw[0], f"{label}.{name} value")
        unit = raw[1]
        if unit is None:
            unit = ""
        if not isinstance(unit, str) or (unit and not _UNIT.fullmatch(unit)):
            _fail(f"{label} parameter {name!r} has an unsupported unit spelling")
        values[name] = (value, unit)
    return list(names), values


def _check_solution_readback(
    request: Mapping[str, Any], *, role: str, source_kind: str,
    source: Mapping[str, Any], readback: Any, component: str, geometry: str,
) -> dict[str, Any]:
    if not isinstance(readback, Mapping):
        _fail(f"{role} requires native dataset and SolutionInfo readbacks")
    binding, axes, context = (readback.get("dataset_binding"), readback.get("solution_axes"),
                              readback.get("source_context"))
    if (not isinstance(binding, Mapping) or binding.get("binding_complete") is not True
            or binding.get("dataset") != source.get("dataset_id")
            or binding.get("solution") != source.get("solution_id")
            or binding.get("component") != component or binding.get("geometry") != geometry):
        _fail(f"{role} native dataset binding does not match its exact source and geometry")
    if not isinstance(axes, Mapping):
        _fail(f"{role} native SolutionInfo pair metadata is unavailable")
    for key in ("outer_index", "inner_index", "solnum"):
        if type(axes.get(key)) is not int or axes[key] != source.get(key):
            _fail(f"{role} SolutionInfo {key} differs from the requested native source")
    if type(axes.get("pair_mapping_complete")) is not bool or axes["pair_mapping_complete"] is not True:
        _fail(f"{role} native SolutionInfo pair mapping is not complete")
    if not isinstance(context, Mapping):
        _fail(f"{role} source context must bind project, ModelRef, revisions, and frame")
    for key, expected in (
        ("project_id", request["project_id"]), ("model_ref", request["model_ref"]),
        ("model_revision", request["model_revision"]),
        ("geometry_revision", request["geometry_revision"]),
        ("coordinate_frame", request["coordinate_frame"]),
    ):
        if context.get(key) != expected:
            _fail(f"{role} native source context {key} differs from the registered v2 request")

    names, parameters = _parameter_records(axes, role)
    frequency = axes.get("frequency")
    if not isinstance(frequency, Mapping):
        _fail(f"{role} native frequency metadata is missing")
    matching = [name for name in names if name.strip().lower() in _FREQUENCY_NAMES]
    if len(matching) != 1 or matching[0] != frequency.get("parameter"):
        _fail(f"{role} must expose one exact native frequency parameter")
    frequency_name = matching[0]
    native_value, native_unit = parameters[frequency_name]
    if (frequency.get("value") != native_value or frequency.get("unit") != native_unit
            or frequency.get("frequency_hz") is None):
        _fail(f"{role} frequency summary differs from native SolutionInfo parameters")
    scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}.get(native_unit)
    if scale is None:
        _fail(f"{role} native frequency unit {native_unit!r} is not supported")
    frequency_hz = native_value * scale
    if not math.isfinite(frequency_hz):
        _fail(f"{role} native frequency conversion overflowed")
    requested_frequency = _number(request["frequency_hz"], "request frequency")
    if (not math.isclose(frequency_hz, requested_frequency,
                         rel_tol=NATIVE_PLAN_POLICY["frequency_relative_tolerance"], abs_tol=0.0)
            or not math.isclose(_number(frequency["frequency_hz"], f"{role} frequency_hz"),
                                requested_frequency,
                                rel_tol=NATIVE_PLAN_POLICY["frequency_relative_tolerance"], abs_tol=0.0)):
        _fail(f"{role} native SolutionInfo frequency differs from the v2 request")

    mode_axis = readback.get("mode_axis_parameter")
    mode_index = source.get("mode_index")
    if source_kind == "mode":
        mode_axis = _tag(mode_axis, f"{role} native mode-axis parameter")
        if (type(mode_index) is not int or mode_index < 1
                or type(source.get("inner_index")) is not int or source["inner_index"] < 1
                or mode_axis not in parameters):
            _fail(f"{role} native mode-axis selection does not bind its exact SolutionInfo pair")
    elif mode_axis is not None:
        _fail(f"{role} non-mode source must not claim a mode-axis selector")
    selectors = []
    if source_kind == "mode":
        # mode_index is the Numeric Port basis identity. COMSOL's withsol
        # setind argument selects the exact solution row, which is bound by
        # native SolutionInfo inner_index. These identities must not be
        # conflated: independently saved one-mode BMA solutions can each have
        # inner_index=1 while representing distinct Numeric Port mode indices.
        selectors.append(f"setind({mode_axis},{source['inner_index']})")
    for name in names:
        if source_kind == "mode" and name == mode_axis:
            continue
        value, unit = parameters[name]
        literal = format(value, ".17g") + (f"[{unit}]" if unit else "")
        selectors.append(f"setval({name},{literal})")
    return {
        "role": role, "source_kind": source_kind, "source": dict(source),
        "dataset_binding": dict(binding), "solution_axes": dict(axes),
        "source_context": dict(context), "mode_axis_parameter": mode_axis,
        "withsol_selectors": selectors,
    }


def _surface_readback(
    request: Mapping[str, Any], *, surface_key: str, readback: Any,
) -> dict[str, Any]:
    surface = request["output_surface" if surface_key == "output" else "input_surface"]
    if not isinstance(readback, Mapping):
        _fail(f"{surface_key} port requires named-surface native readback")
    for key in ("component", "geometry", "selection_tag", "frame_id", "coordinate_unit",
                "measure_unit", "aperture_id"):
        if readback.get(key) != surface.get(key):
            _fail(f"{surface_key} native surface readback {key} differs from the requested surface")
    if readback.get("entity_dimension") != NATIVE_PLAN_POLICY["surface_entity_dimension"]:
        _fail(f"{surface_key} native selection is not a 3-D boundary")
    entities = readback.get("entity_ids")
    if (not isinstance(entities, list) or not entities
            or any(type(value) is not int or value < 1 for value in entities)
            or len(set(entities)) != len(entities)):
        _fail(f"{surface_key} native named selection needs nonempty unique boundary IDs")
    if type(readback.get("native_normal_sign")) is not int or readback["native_normal_sign"] != surface["native_normal_sign"]:
        _fail(f"{surface_key} native outward-normal sign differs from the frozen surface orientation")
    try:
        area = _number(readback.get("surface_area_m2"), f"{surface_key} measured aperture area")
    except NativePlanError:
        raise
    if area <= 0.0 or not math.isclose(
        area, surface["surface_area_m2"],
        rel_tol=NATIVE_PLAN_POLICY["surface_area_relative_tolerance"],
        abs_tol=NATIVE_PLAN_POLICY["surface_area_absolute_tolerance_m2"],
    ):
        _fail(f"{surface_key} native aperture area differs from the frozen quadrature surface")
    for key in ("center_xyz_um", "axis_xyz"):
        actual = readback.get(key)
        expected = surface.get(key)
        if (not isinstance(actual, (list, tuple)) or len(actual) != 3
                or not isinstance(expected, (list, tuple)) or len(expected) != 3):
            _fail(f"{surface_key} native {key} must be a three-vector")
        tolerance = (NATIVE_PLAN_POLICY["surface_center_absolute_tolerance_um"]
                     if key == "center_xyz_um" else NATIVE_PLAN_POLICY["surface_axis_absolute_tolerance"])
        if any(abs(_number(actual[i], f"{surface_key}.{key}")
                   - _number(expected[i], f"{surface_key}.{key}")) > tolerance for i in range(3)):
            _fail(f"{surface_key} native {key} differs from the frozen surface frame")
    return {**dict(readback), "selection_tag": surface["selection_tag"],
            "aperture_id": surface["aperture_id"], "entity_ids": list(entities),
            "surface_area_m2": area, "readback_status": "SOFTWARE_VALIDATED_ENVELOPE_ONLY"}


def _withsol(source: Mapping[str, Any], variable: str) -> str:
    solution_tag = _tag(source["source"]["solution_id"], f"{source['role']} solution tag")
    selectors = source["withsol_selectors"]
    if not selectors:
        _fail(f"{source['role']} solution has no exact withsol selectors")
    return f"withsol('{solution_tag}',{variable},{','.join(selectors)})"


def _fields(source: Mapping[str, Any], *, use_withsol: bool) -> dict[str, dict[str, str]]:
    variables = _FIELD_VARIABLES[source["source_kind"]]
    if not use_withsol:
        return {part: dict(values) for part, values in variables.items()}
    return {part: {axis: _withsol(source, expression) for axis, expression in values.items()}
            for part, values in variables.items()}


def _cross_component(left: Mapping[str, str], right: Mapping[str, str], axis: str) -> str:
    pairs = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}
    first, second = pairs[axis]
    return f"({left[first]}*{right[second]}-{left[second]}*{right[first]})"


def _bilinear_expression(a: Mapping[str, Any], b: Mapping[str, Any], *, use_withsol_b: bool) -> str:
    fields_a = _fields(a, use_withsol=False)
    fields_b = _fields(b, use_withsol=use_withsol_b)
    terms = []
    for axis, normal in zip(_AXES, ("nx", "ny", "nz")):
        eb_cross_conj_ha = _cross_component(fields_b["E"],
                                             {key: f"conj({value})" for key, value in fields_a["H"].items()}, axis)
        conj_ea_cross_hb = _cross_component(
            {key: f"conj({value})" for key, value in fields_a["E"].items()},
            fields_b["H"], axis)
        terms.append(f"({eb_cross_conj_ha}+{conj_ea_cross_hb})*{normal}")
    return "0.25*(" + "+".join(terms) + ")"


def build_two_mode_comsol_integral_plan(
    request: Mapping[str, Any], *,
    solution_readbacks: Mapping[str, Any],
    surface_readbacks: Mapping[str, Any],
) -> dict[str, Any]:
    """Return eight exact, non-dispatched native integration expressions.

    ``solution_readbacks`` must contain direct binding/SolutionInfo summaries
    for ``signal``, ``mode_0``, ``mode_1``, and ``incident``. ``surface_readbacks``
    must contain the native boundary ID, dimension, area, normal, and local
    frame readbacks for ``output`` and ``input``. This helper checks their
    envelopes only; the versioned managed v2 route must perform the reads and
    COMSOL ``IntSurface`` integrations itself.
    """
    if (not isinstance(request, Mapping) or request.get("schema_id") != BASIS_SCHEMA_ID
            or request.get("native_result") != "NOT_RUN"
            or request.get("dispatchable") is not False):
        _fail("canonical non-dispatchable v2 basis request is required")
    # This reconstructs the canonical request digest before consuming any
    # caller-provided metadata and also verifies frozen quadrature identities.
    build_two_mode_basis_field_contracts(request)
    if not isinstance(solution_readbacks, Mapping) or set(solution_readbacks) != {
            "signal", "mode_0", "mode_1", "incident"}:
        _fail("exact native SolutionInfo readbacks for all four field identities are required")
    if not isinstance(surface_readbacks, Mapping) or set(surface_readbacks) != {"output", "input"}:
        _fail("exact native output/input boundary readbacks are required")

    surfaces = {
        key: _surface_readback(request, surface_key=key, readback=surface_readbacks[key])
        for key in ("output", "input")
    }
    role_specs = _request_sources(request)
    roles: dict[str, dict[str, Any]] = {}
    for role, (kind, source) in role_specs.items():
        surface_key = "input" if role == "incident" else "output"
        surface = request["input_surface" if surface_key == "input" else "output_surface"]
        roles[role] = _check_solution_readback(
            request, role=role, source_kind=kind, source=source,
            readback=solution_readbacks[role], component=surface["component"],
            geometry=surface["geometry"])

    plans = []
    for term in request["native_integral_plan"]["terms"]:
        term_id = term["integral_id"]
        a_role = next((role for role, (_kind, source) in role_specs.items()
                       if source == term["a_source"]), None)
        b_role = next((role for role, (_kind, source) in role_specs.items()
                       if source == term["b_source"]), None)
        if a_role is None or b_role is None:
            _fail(f"native integral term {term_id} references a source outside the exact v2 request")
        surface_key = "output" if term["surface_id"] == request["output_surface"]["aperture_id"] else (
            "input" if term["surface_id"] == request["input_surface"]["aperture_id"] else None)
        if surface_key is None:
            _fail(f"native integral term {term_id} references an unbound integration aperture")
        source_a, source_b = roles[a_role], roles[b_role]
        if (source_a["source"]["port_id"] != source_b["source"]["port_id"]
                or source_a["source"]["port_id"] != request["output_surface"]["port_id"]
                and surface_key == "output"
                or source_a["source"]["port_id"] != request["input_surface"]["port_id"]
                and surface_key == "input"):
            _fail(f"native integral term {term_id} mixes port fields or the wrong aperture")
        use_withsol_b = a_role != b_role
        surface = surfaces[surface_key]
        source = source_a["source"]
        physical_normal_sign = surface["native_normal_sign"]
        oriented_expression = f"({physical_normal_sign})*({_bilinear_expression(source_a, source_b, use_withsol_b=use_withsol_b)})"
        plans.append({
            "term_id": term_id, "functional": term["functional"],
            "role_a": a_role, "role_b": b_role, "integration_surface": surface_key,
            "expected_unit": "W", "native_feature_type": "IntSurface",
            "expression": oriented_expression,
            "expression_current_solution": source["solution_id"],
            "expression_secondary_solution": (source_b["source"]["solution_id"] if use_withsol_b else None),
            "selection": {"component": surface["component"], "geometry": surface["geometry"],
                          "tag": surface["selection_tag"], "entity_dimension": 2},
            "solution": {"dataset": source["dataset_id"], "solution": source["solution_id"],
                         "outer": source["outer_index"], "inner": source["inner_index"]},
            "expected_result_binding": {
                "dataset_id": source["dataset_id"], "solution_id": source["solution_id"],
                "outer_index": source["outer_index"], "inner_index": source["inner_index"],
                "solnum": source["solnum"], "selection_tag": surface["selection_tag"],
                "entity_dimension": 2, "entity_ids": list(surface["entity_ids"]),
                "aperture_id": surface["aperture_id"], "area_m2": surface["surface_area_m2"],
                "coordinate_frame": request["coordinate_frame"], "unit": "W",
                "complex_readback_required": True, "cleanup_removed_required": True,
            },
        })
    expected_ids = {f"G{i}{j}" for i in range(2) for j in range(2)} | {"b0", "b1", "P_signal", "P_incident"}
    if len(plans) != 8 or {row["term_id"] for row in plans} != expected_ids:
        _fail("the v2 native integral plan must preserve all four G, two b, and two power terms")
    identity = {
        "schema_id": NATIVE_PLAN_SCHEMA_ID, "basis_request_id": request["request_id"],
        "basis_schema_id": BASIS_SCHEMA_ID, "basis_id": request["basis_id"],
        "project_id": request["project_id"], "model_ref": dict(request["model_ref"]),
        "model_revision": request["model_revision"], "geometry_revision": request["geometry_revision"],
        "frequency_hz": request["frequency_hz"], "coordinate_frame": request["coordinate_frame"],
        "surfaces": surfaces, "solution_sources": roles, "policy": dict(NATIVE_PLAN_POLICY),
        "terms": plans, "origin_required": "COMSOL_NATIVE_INTEGRATION_FEATURES",
        "managed_operation_id": "result.mode_overlap_basis_v2",
        "native_result": "NOT_RUN", "study_or_solver_invoked": False,
        "dispatchable": False, "production_route_status": "ROUTE_REGISTERED_NOT_DISPATCHED",
        "provenance_note": "solution/surface metadata supplied to this pure helper is not authenticated; managed route must acquire and attest it",
    }
    return {**identity, "plan_id": _digest(identity)}


__all__ = ["NATIVE_PLAN_POLICY", "NATIVE_PLAN_SCHEMA_ID", "NativePlanError",
           "build_two_mode_comsol_integral_plan"]
