"""Versioned two-mode power projection for a degenerate full-3D basis.

This module is an offline math/reference and request-contract layer. It does
not register or dispatch a production operation, and every computed result is
labelled SOFTWARE_ONLY / native_result=NOT_RUN. Native G and b must later be
provided by a trusted COMSOL integration route and independently checked
against the raw-field quadrature implemented here.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from tools.w23_full3d_science import (
    build_raw_field_contract,
    circular_port_quadrature,
    rectangular_port_quadrature,
    validate_native_field_readback,
)


BASIS_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap:two-mode-basis:2.0.0"
BASIS_PROFILE_ID = "full3d.reciprocal_lossless_forward_two_mode_subspace_v2"
BASIS_ALGORITHM_ID = "reciprocal_lossless_forward_two_mode_basis_projection_v2"

# Values are fixed before any native basis run. The normalized Gram matrix is
# D^(-1/2) G D^(-1/2); its condition number is a basis-quality gate, not a
# post-hoc choice of which solver mode to call the polarization.
BASIS_POLICY = {
    "policy_id": "w23.full3d.two_mode_basis.numerical_gates.v1",
    "power_unit": "W",
    "field_electric_unit": "V/m",
    "field_magnetic_unit": "A/m",
    "coordinate_unit": "m",
    "area_unit": "m^2",
    "power_floor_w": 1e-12,
    "hermitian_relative_tolerance": 1e-8,
    "max_normalized_gram_condition": 1e6,
    "native_vs_quadrature_relative_tolerance": 1e-3,
    "native_integral_absolute_normalized_tolerance": 1e-5,
    "eta_absolute_tolerance": 1e-10,
}

_REQUIRED_MODEL_REF = {
    "schema_version", "session_id", "server_instance_id", "model_tag", "generation"
}
_SOURCE_KEYS = (
    "dataset_id", "solution_id", "outer_index", "inner_index", "solnum",
    "port_id", "frequency_hz", "project_id", "model_ref", "model_revision",
    "geometry_revision", "coordinate_frame",
)
_VECTOR_AXES = ("x", "y", "z")


class TwoModeBasisError(ValueError):
    """A two-mode basis contract, source, or projection is inadmissible."""


def _fail(message: str) -> None:
    raise TwoModeBasisError(message)


def _hash(value: Any) -> str:
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


def _complex(value: Any, label: str) -> complex:
    if isinstance(value, bool) or not isinstance(value, (int, float, complex)):
        _fail(f"{label} must be finite complex numeric data")
    result = complex(value)
    if not math.isfinite(result.real) or not math.isfinite(result.imag):
        _fail(f"{label} must be finite complex numeric data")
    return result


def _require_finite_complex(value: complex, label: str) -> complex:
    if not math.isfinite(value.real) or not math.isfinite(value.imag):
        _fail(f"{label} overflowed or became non-finite")
    return value


def _complex_magnitude(value: complex, label: str) -> float:
    magnitude = math.hypot(value.real, value.imag)
    if not math.isfinite(magnitude):
        _fail(f"{label} magnitude overflowed")
    return magnitude


def _divide_complex_by_real(value: complex, divisor: float, label: str) -> complex:
    if not math.isfinite(divisor) or divisor <= 0.0:
        _fail(f"{label} scale divisor must be positive and finite")
    return _require_finite_complex(complex(value.real / divisor, value.imag / divisor), label)


def _normalize_gram_entry(value: complex, first_scale: float, second_scale: float,
                          label: str) -> complex:
    # Divide sequentially rather than forming first_scale*second_scale,
    # which can overflow even when the normalized entry is O(1).
    return _divide_complex_by_real(
        _divide_complex_by_real(value, first_scale, label), second_scale, label)


def _finite_complex_sum(values: Sequence[complex], label: str) -> complex:
    try:
        result = complex(math.fsum(value.real for value in values),
                         math.fsum(value.imag for value in values))
    except OverflowError as exc:
        raise TwoModeBasisError(f"{label} sum overflowed") from exc
    return _require_finite_complex(result, label)


def _complex_record(value: complex) -> dict[str, float]:
    return {"real": float(value.real), "imag": float(value.imag)}


def _model_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_MODEL_REF:
        _fail("persisted project-bound ModelRef is required")
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   for key in ("session_id", "server_instance_id", "model_tag"))
            or type(value.get("generation")) is not int or value["generation"] < 1):
        _fail("ModelRef identity is incomplete or malformed")
    return dict(value)


def _source(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(key not in value for key in _SOURCE_KEYS):
        _fail(f"{label} must bind exact dataset, solution, port, frame, and project/model identity")
    result = dict(value)
    for key in ("dataset_id", "solution_id", "port_id", "project_id", "coordinate_frame"):
        if not isinstance(result[key], str) or not result[key].strip():
            _fail(f"{label}.{key} must be a nonempty native identity")
    if any(type(result[key]) is not int or result[key] < 1
           for key in ("outer_index", "inner_index", "solnum")):
        _fail(f"{label} native solution indices must be positive integers")
    result["model_ref"] = _model_ref(result["model_ref"])
    if type(result["model_revision"]) is not int or result["model_revision"] < 0:
        _fail(f"{label}.model_revision must be a nonnegative integer")
    if type(result["geometry_revision"]) is not int or result["geometry_revision"] < 0:
        _fail(f"{label}.geometry_revision must be a nonnegative integer")
    result["frequency_hz"] = _number(result["frequency_hz"], f"{label}.frequency_hz")
    if result["frequency_hz"] <= 0:
        _fail(f"{label}.frequency_hz must be positive")
    return result


def _plane(value: Any, *, expected_id: str) -> dict[str, Any]:
    required = {
        "plane_id", "port_id", "component", "geometry", "selection_tag", "entity_dimension",
        "frame_id", "coordinate_unit", "measure_unit", "center_xyz_um", "axis_xyz",
        "native_normal_sign", "aperture_id", "aperture_shape", "surface_area_m2",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        _fail(f"{expected_id} must include a named, dimensioned, framed native surface and aperture")
    plane = dict(value)
    for key in ("plane_id", "port_id", "component", "geometry", "selection_tag", "frame_id", "aperture_id"):
        if not isinstance(plane[key], str) or not plane[key].strip():
            _fail(f"{expected_id}.{key} must be a nonempty identity")
    if plane["plane_id"] != expected_id or plane["entity_dimension"] != 2:
        _fail(f"{expected_id} must be a named 2-D boundary on the expected port plane")
    if plane["coordinate_unit"] != "um" or plane["measure_unit"] != "m^2":
        _fail("the native geometry-unit and integrated-area-unit contracts must be um and m^2")
    if type(plane["native_normal_sign"]) is not int or plane["native_normal_sign"] not in {-1, 1}:
        _fail("native surface outward-normal orientation must be measured as exactly +1 or -1")
    if plane["aperture_shape"] not in {"circular", "rectangle"}:
        _fail("native mode basis integration requires a registered circular or rectangular aperture")
    if plane["aperture_shape"] == "circular":
        radius = _number(plane.get("sample_radius_um"), f"{expected_id}.sample_radius_um")
        if radius <= 0:
            _fail("circular aperture radius must be positive")
        plane["sample_radius_um"] = radius
    else:
        widths = plane.get("half_widths_uv_um")
        if not isinstance(widths, (tuple, list)) or len(widths) != 2:
            _fail("rectangular aperture requires two local half widths")
        plane["half_widths_uv_um"] = [_number(item, "aperture half width") for item in widths]
        if min(plane["half_widths_uv_um"]) <= 0:
            _fail("rectangular aperture half widths must be positive")
    plane["center_xyz_um"] = [_number(item, "surface center") for item in plane["center_xyz_um"]]
    if len(plane["center_xyz_um"]) != 3:
        _fail("surface center must be a 3-vector")
    plane["axis_xyz"] = [_number(item, "surface axis") for item in plane["axis_xyz"]]
    if len(plane["axis_xyz"]) != 3:
        _fail("surface axis must be a 3-vector")
    norm = math.sqrt(sum(item * item for item in plane["axis_xyz"]))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-10:
        _fail("surface forward axis must be a normalized 3-vector")
    area = _number(plane["surface_area_m2"], f"{expected_id}.surface_area_m2")
    if area <= 0:
        _fail("surface aperture area must be positive")
    plane["surface_area_m2"] = area
    return plane


def _quadrature(plane: Mapping[str, Any], *, radial_intervals: int,
                angular_points: int) -> dict[str, Any]:
    center = [item * 1e-6 for item in plane["center_xyz_um"]]
    if plane["aperture_shape"] == "circular":
        q = circular_port_quadrature(
            center, plane["axis_xyz"], plane["sample_radius_um"] * 1e-6,
            radial_intervals=radial_intervals, angular_points=angular_points)
    else:
        q = rectangular_port_quadrature(
            center, plane["axis_xyz"], [item * 1e-6 for item in plane["half_widths_uv_um"]],
            u_intervals=radial_intervals, v_intervals=angular_points)
    if not math.isclose(q["weight_sum_m2"], plane["surface_area_m2"], rel_tol=1e-10, abs_tol=1e-30):
        _fail("quadrature area does not equal the independently read-back native aperture area")
    return q


def _assert_common_source_identity(sources: Sequence[Mapping[str, Any]], *, frequency_hz: float,
                                   project_id: str, model_ref: Mapping[str, Any],
                                   model_revision: int, geometry_revision: int,
                                   coordinate_frame: str) -> None:
    for source in sources:
        if (source["project_id"] != project_id or source["model_ref"] != model_ref
                or source["model_revision"] != model_revision
                or source["geometry_revision"] != geometry_revision
                or source["coordinate_frame"] != coordinate_frame
                or not math.isclose(source["frequency_hz"], frequency_hz, rel_tol=1e-12, abs_tol=0.0)):
            _fail("basis sources mix project/model revision, geometry frame, or frequency identities")


def build_two_mode_basis_request(
    *, basis_id: str, case: Mapping[str, Any], project_id: str,
    model_ref: Mapping[str, Any], model_revision: int, geometry_revision: int,
    frequency_hz: float, coordinate_frame: str,
    output_surface: Mapping[str, Any], input_surface: Mapping[str, Any],
    signal_source: Mapping[str, Any], mode_sources: Sequence[Mapping[str, Any]],
    incident_source: Mapping[str, Any],
    applicability: Mapping[str, Any], radial_intervals: int = 32,
    angular_points: int = 64,
) -> dict[str, Any]:
    """Build a non-dispatchable v2 request and its exact native-integral plan.

    A future production adapter must compute the listed G and b entries inside
    COMSOL on these exact native solutions and surfaces. This is not an
    operation_call for the currently registered scalar v1 route.
    """
    if not isinstance(basis_id, str) or not basis_id.strip():
        _fail("explicit degenerate-subspace basis identity is required")
    if not isinstance(case, Mapping) or not all(isinstance(case.get(key), str) and case[key]
            for key in ("case_id", "case_identity_sha256")):
        _fail("registered immutable geometry case identity is required")
    if not isinstance(project_id, str) or not project_id.strip():
        _fail("authoritative registered project id is required")
    ref = _model_ref(model_ref)
    if type(model_revision) is not int or model_revision < 0 or type(geometry_revision) is not int or geometry_revision < 0:
        _fail("model and geometry revisions must be nonnegative integers")
    frequency = _number(frequency_hz, "basis frequency")
    if frequency <= 0 or not isinstance(coordinate_frame, str) or not coordinate_frame.strip():
        _fail("positive frequency and explicit coordinate frame are required")
    output = _plane(output_surface, expected_id="receiver_port")
    incident_plane = _plane(input_surface, expected_id="input_port")
    if output["frame_id"] != coordinate_frame or incident_plane["frame_id"] != coordinate_frame:
        _fail("named input/output surfaces must use the exact source coordinate-frame identity")
    if output["port_id"] != "2" or incident_plane["port_id"] != "1":
        _fail("full-3D basis contract must bind output Port 2 and incident Port 1")
    if not isinstance(applicability, Mapping):
        _fail("reciprocal/lossless/forward applicability conditions must be explicitly requested")
    required_applicability = ("reciprocal", "lossless", "forward_propagating", "non_evanescent", "non_leaky")
    if any(applicability.get(key) is not True for key in required_applicability):
        _fail("v2 basis is restricted to explicitly requested reciprocal, lossless, forward propagating, non-leaky modes")
    signal = _source(signal_source, label="signal")
    incident = _source(incident_source, label="incident")
    modes = list(mode_sources) if isinstance(mode_sources, Sequence) and not isinstance(mode_sources, (str, bytes)) else []
    if len(modes) != 2:
        _fail("v2 requires exactly two independently identified mode solutions")
    normalized_modes = []
    for index, row in enumerate(modes):
        if not isinstance(row, Mapping) or not isinstance(row.get("mode_id"), str) or not row["mode_id"].strip():
            _fail("each basis member requires a stable source identity, not a claimed polarization label")
        source = _source(row.get("source"), label=f"mode[{index}]")
        if (type(row.get("mode_index")) is not int or row["mode_index"] < 1
                or source.get("mode_index") != row["mode_index"]):
            _fail("basis members must bind distinct positive Numeric-port mode indices in their sources")
        if source["port_id"] != output["port_id"]:
            _fail("both basis members must come from output Port 2")
        normalized_modes.append({"mode_id": row["mode_id"], "mode_index": row["mode_index"],
                                 "source": source})
    if (len({row["mode_id"] for row in normalized_modes}) != 2
            or len({row["mode_index"] for row in normalized_modes}) != 2
            or len({(row["source"]["dataset_id"], row["source"]["solution_id"],
                     row["source"]["outer_index"], row["source"]["inner_index"],
                     row["source"]["solnum"]) for row in normalized_modes}) != 2):
        _fail("duplicate basis mode IDs, selected indices, or native solution identities are not admissible")
    if signal["port_id"] != output["port_id"] or incident["port_id"] != incident_plane["port_id"]:
        _fail("signal and both output modes must use Port 2; incident normalization must use Port 1")
    output_sources = [signal, *(row["source"] for row in normalized_modes)]
    all_sources = [*output_sources, incident]
    _assert_common_source_identity(all_sources, frequency_hz=frequency, project_id=project_id,
        model_ref=ref, model_revision=model_revision, geometry_revision=geometry_revision,
        coordinate_frame=coordinate_frame)
    out_q = _quadrature(output, radial_intervals=radial_intervals, angular_points=angular_points)
    in_q = _quadrature(incident_plane, radial_intervals=radial_intervals, angular_points=angular_points)
    identity = {
        "schema_id": BASIS_SCHEMA_ID, "schema_version": "2.0.0",
        "profile": BASIS_PROFILE_ID, "algorithm_id": BASIS_ALGORITHM_ID,
        "basis_id": basis_id, "case": dict(case), "project_id": project_id,
        "model_ref": ref, "model_revision": model_revision,
        "geometry_revision": geometry_revision, "frequency_hz": frequency,
        "coordinate_frame": coordinate_frame, "power_unit": "W",
        "field_units": {"electric": "V/m", "magnetic": "A/m"},
        "phasor_convention": "full_physical_complex_phasor_including_reconstructed_envelope_phase",
        "output_surface": output, "input_surface": incident_plane,
        "signal_source": signal, "basis_modes": normalized_modes,
        "incident_source": incident, "applicability": {key: True for key in required_applicability},
        "native_verification": "NOT_RUN", "native_result": "NOT_RUN",
        "dispatchable": False, "production_route_status": "NOT_REGISTERED",
        "policy": dict(BASIS_POLICY),
        "quadratures": {
            "output": {key: out_q[key] for key in ("profile", "quadrature_sha256", "sample_count", "coordinate_unit", "measure_unit")},
            "input": {key: in_q[key] for key in ("profile", "quadrature_sha256", "sample_count", "coordinate_unit", "measure_unit")},
        },
    }
    terms = []
    for i, first in enumerate(normalized_modes):
        for j, second in enumerate(normalized_modes):
            terms.append({"integral_id": f"G{i}{j}", "functional": "B(a,b)",
                          "a_source": first["source"], "b_source": second["source"],
                          "surface_id": output["aperture_id"], "unit": "W"})
    for i, mode in enumerate(normalized_modes):
        terms.append({"integral_id": f"b{i}", "functional": "B(a,b)",
                      "a_source": mode["source"], "b_source": signal,
                      "surface_id": output["aperture_id"], "unit": "W"})
    terms.extend([
        {"integral_id": "P_signal", "functional": "B(a,a)", "a_source": signal,
         "b_source": signal, "surface_id": output["aperture_id"], "unit": "W"},
        {"integral_id": "P_incident", "functional": "B(a,a)", "a_source": incident,
         "b_source": incident, "surface_id": incident_plane["aperture_id"], "unit": "W"},
    ])
    identity["native_integral_plan"] = {
        "origin_required": "COMSOL_NATIVE_INTEGRATION_FEATURES",
        "source_arrays_accepted": False, "terms": terms,
        "status": "PLAN_ONLY_NOT_DISPATCHED",
    }
    request_id = _hash(identity)
    return {**identity, "request_id": request_id, "study_or_solver_invoked": False}


def build_two_mode_basis_field_contracts(
    request: Mapping[str, Any], *, radial_intervals: int = 32, angular_points: int = 64,
) -> dict[str, Any]:
    """Build same-grid raw-field contracts for the software reference path."""
    if (not isinstance(request, Mapping) or request.get("schema_id") != BASIS_SCHEMA_ID
            or request.get("dispatchable") is not False):
        _fail("a complete non-dispatchable v2 request is required")
    if _hash(_basis_request_binding(request)) != request.get("request_id"):
        _fail("v2 request identity digest does not match its immutable source/surface contract")
    case = request.get("case")
    output = request.get("output_surface")
    input_surface = request.get("input_surface")
    modes = request.get("basis_modes")
    if not isinstance(case, Mapping) or not isinstance(output, Mapping) or not isinstance(input_surface, Mapping) or not isinstance(modes, list) or len(modes) != 2:
        _fail("v2 request omitted its immutable case, surfaces, or two basis sources")
    out_q = _quadrature(output, radial_intervals=radial_intervals, angular_points=angular_points)
    in_q = _quadrature(input_surface, radial_intervals=radial_intervals, angular_points=angular_points)
    if (out_q["quadrature_sha256"] != request["quadratures"]["output"]["quadrature_sha256"]
            or in_q["quadrature_sha256"] != request["quadratures"]["input"]["quadrature_sha256"]):
        _fail("requested field-contract grid differs from the frozen two-mode basis quadrature")

    def contract(role: str, source: Mapping[str, Any], plane: Mapping[str, Any], q: Mapping[str, Any]) -> dict[str, Any]:
        base = build_raw_field_contract(case=case, role=role, source=source, plane=plane,
                                        radial_intervals=radial_intervals, angular_points=angular_points)
        base["source"] = dict(source)
        base["identity"] = {"request_id": request["request_id"], "basis_id": request["basis_id"],
                             "case_id": case["case_id"], "project_id": request["project_id"],
                             "model_ref": dict(request["model_ref"]),
                             "model_revision": request["model_revision"],
                             "geometry_revision": request["geometry_revision"],
                             "frequency_hz": request["frequency_hz"],
                             "mode_index": source.get("mode_index")}
        base["quadrature"] = {**base["quadrature"], "quadrature_sha256": q["quadrature_sha256"],
                              "sample_count": q["sample_count"]}
        base["quadrature_sha256"] = q["quadrature_sha256"]
        base["contract_id"] = _hash({"base_contract_id": base["contract_id"],
                                     "source": base["source"], "identity": base["identity"]})
        return base

    output_contracts = {
        "signal": contract("signal", request["signal_source"], output, out_q),
        "modes": [contract("reference_mode", row["source"], output, out_q) for row in modes],
    }
    incident_contract = contract("incident_reference", request["incident_source"], input_surface, in_q)
    return {"request_id": request["request_id"], "basis_id": request["basis_id"],
            "output": output_contracts, "incident": incident_contract,
            "quadratures": {"output": out_q, "input": in_q},
            "native_result": "NOT_RUN", "study_or_solver_invoked": False}


def _field_arrays(fields: Any, label: str, count: int) -> tuple[list[tuple[complex, complex, complex]], list[tuple[complex, complex, complex]]]:
    if not isinstance(fields, Mapping) or set(fields) != {"E", "H"}:
        _fail(f"{label} fields must contain exactly full complex E and H vector arrays")
    parsed = []
    for name in ("E", "H"):
        rows = fields[name]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) != count:
            _fail(f"{label}.{name} sample count differs from the frozen quadrature")
        values = []
        for index, vector in enumerate(rows):
            if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)) or len(vector) != 3:
                _fail(f"{label}.{name}[{index}] must be a full xyz vector")
            values.append(tuple(_complex(item, f"{label}.{name}[{index}]") for item in vector))
        parsed.append(values)
    return parsed[0], parsed[1]


def power_form_integral(
    first: Mapping[str, Any], second: Mapping[str, Any], *,
    normals_xyz: Sequence[Sequence[float]], weights_m2: Sequence[float],
) -> complex:
    """Compute B(first, second) for software-only complex vector samples."""
    count = len(weights_m2)
    if count == 0 or len(normals_xyz) != count:
        _fail("power-form normal and area axes must be nonempty and equal length")
    ea, ha = _field_arrays(first, "first", count)
    eb, hb = _field_arrays(second, "second", count)
    total = 0j
    for index, (normal, weight) in enumerate(zip(normals_xyz, weights_m2)):
        if not isinstance(normal, Sequence) or len(normal) != 3:
            _fail("each integration normal must be a global xyz vector")
        n = tuple(_number(v, "normal") for v in normal)
        norm = math.sqrt(sum(v * v for v in n))
        if abs(norm - 1.0) > 1e-8:
            _fail("integration normals must be unit vectors")
        w = _number(weight, "quadrature area weight")
        if w < 0:
            _fail("quadrature area weights cannot be negative")
        first_cross = _cross(eb[index], tuple(v.conjugate() for v in ha[index]))
        second_cross = _cross(tuple(v.conjugate() for v in ea[index]), hb[index])
        total += w * sum((first_cross[axis] + second_cross[axis]) * n[axis] for axis in range(3)) / 4.0
    return total


def _cross(left: Sequence[complex], right: Sequence[complex]) -> tuple[complex, complex, complex]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def compute_two_mode_basis_projection(
    gram: Sequence[Sequence[complex]], coupling: Sequence[complex], *,
    signal_power_w: float, incident_power_w: float, source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Solve G c=b and return the unclamped software-only subspace projection."""
    if (not isinstance(gram, Sequence) or len(gram) != 2
            or any(not isinstance(row, Sequence) or len(row) != 2 for row in gram)
            or not isinstance(coupling, Sequence) or len(coupling) != 2):
        _fail("two-mode projection requires a 2x2 Gram matrix and two couplings")
    g = [[_complex(gram[i][j], f"G[{i},{j}]") for j in range(2)] for i in range(2)]
    b = [_complex(coupling[i], f"b[{i}]") for i in range(2)]
    signal_power = _number(signal_power_w, "signal power")
    incident_power = _number(incident_power_w, "incident reference power")
    floor = BASIS_POLICY["power_floor_w"]
    if signal_power <= floor or incident_power <= floor:
        _fail("positive signal and incident reference powers must exceed the frozen W floor")
    diagonal = [g[0][0].real, g[1][1].real]
    if min(diagonal) <= floor:
        _fail("basis modes must each have positive forward power above the frozen W floor")
    diagonal_scales = [math.sqrt(diagonal[0]), math.sqrt(diagonal[1])]
    if any(not math.isfinite(value) or value <= 0.0 for value in diagonal_scales):
        _fail("basis modal-power scales must remain positive and finite")

    # Normalize each Hermitian residual by its own modal-power scales. A
    # single max(diagonal) scale can hide a material defect in a weak basis
    # vector when the two members use very different normalizations.
    h00_raw = _normalize_gram_entry(g[0][0], diagonal_scales[0], diagonal_scales[0], "G[0,0]")
    h01_raw = _normalize_gram_entry(g[0][1], diagonal_scales[0], diagonal_scales[1], "G[0,1]")
    h10_raw = _normalize_gram_entry(g[1][0], diagonal_scales[1], diagonal_scales[0], "G[1,0]")
    h11_raw = _normalize_gram_entry(g[1][1], diagonal_scales[1], diagonal_scales[1], "G[1,1]")
    hermitian_residual = max(
        abs(h00_raw.imag), abs(h11_raw.imag),
        _complex_magnitude(h01_raw - h10_raw.conjugate(), "normalized Hermitian residual"),
    )
    hermitian_relative = hermitian_residual
    if hermitian_relative > BASIS_POLICY["hermitian_relative_tolerance"]:
        _fail("native/reference Gram matrix is not Hermitian within the frozen relative tolerance")

    # Solve Gc=b through G=D H D, where D contains sqrt(Gii). This keeps the
    # condition gate and solve invariant under independent basis scaling and
    # avoids raw determinant/square-root products that overflow or underflow.
    rho = _finite_complex_sum((h01_raw / 2.0, h10_raw.conjugate() / 2.0),
                              "normalized Hermitian projection")
    rho = _require_finite_complex(rho, "normalized off-diagonal Gram entry")
    rho_abs = _complex_magnitude(rho, "normalized Gram off-diagonal")
    eigen_min, eigen_max = 1.0 - rho_abs, 1.0 + rho_abs
    if eigen_min <= 0.0:
        _fail("normalized Gram matrix is not positive definite; duplicate/singular basis refused")
    condition = eigen_max / eigen_min
    if not math.isfinite(condition) or condition > BASIS_POLICY["max_normalized_gram_condition"]:
        _fail("normalized Gram matrix exceeds the frozen condition-number limit")
    determinant_normalized = eigen_min * eigen_max
    if not math.isfinite(determinant_normalized) or determinant_normalized <= 0.0:
        _fail("normalized Gram matrix determinant is nonpositive or non-finite")
    rhs = [_divide_complex_by_real(b[index], diagonal_scales[index], f"D^-1 b[{index}]")
           for index in range(2)]
    y = [
        _divide_complex_by_real(rhs[0] - rho * rhs[1], determinant_normalized, "normalized solve y[0]"),
        _divide_complex_by_real(rhs[1] - rho.conjugate() * rhs[0], determinant_normalized, "normalized solve y[1]"),
    ]
    c = [_divide_complex_by_real(y[index], diagonal_scales[index], f"basis coefficient c[{index}]")
         for index in range(2)]
    projected_terms = [_require_finite_complex(rhs[index].conjugate() * y[index],
                                                f"projected power term {index}")
                       for index in range(2)]
    projected_complex = _finite_complex_sum(projected_terms, "projected basis power")
    projection_scale = max(abs(signal_power), abs(projected_complex.real), floor)
    if abs(projected_complex.imag) > BASIS_POLICY["hermitian_relative_tolerance"] * projection_scale:
        _fail("projected basis power has a material imaginary residual")
    projected_power = projected_complex.real
    if projected_power < -floor:
        _fail("projected basis power is reverse-directed or negative")
    eta = projected_power / incident_power
    if not math.isfinite(eta):
        _fail("projected basis efficiency overflowed or became non-finite")
    g01_used = _require_finite_complex(
        g[0][1] / 2.0 + g[1][0].conjugate() / 2.0,
        "Hermitian-projected G[0,1]")
    return {
        "schema_id": BASIS_SCHEMA_ID, "schema_version": "2.0.0",
        "algorithm_id": BASIS_ALGORITHM_ID, "basis_profile": BASIS_PROFILE_ID,
        "status": "SOFTWARE_ONLY_TWO_MODE_PROJECTION",
        "evidence_scope": "matrix power-form software reference; no native integral or caller-array provenance",
        "source_identity": dict(source_identity), "unit": "W",
        "gram_raw": [[_complex_record(value) for value in row] for row in g],
        "gram_used_for_projection": [[_complex_record(complex(diagonal[0], 0.0)),
                                       _complex_record(g01_used)],
                                      [_complex_record(g01_used.conjugate()),
                                       _complex_record(complex(diagonal[1], 0.0))]],
        "coupling": [_complex_record(value) for value in b],
        "coefficients": [_complex_record(value) for value in c],
        "signal_power_w": signal_power, "incident_power_w": incident_power,
        "projected_power_w_raw": projected_power, "projected_power_imag_residual_w": projected_complex.imag,
        "eta_basis_raw": eta, "normalized_gram_condition": condition,
        "normalized_gram_eigenvalues": [eigen_min, eigen_max],
        "hermitian_relative_residual": hermitian_relative,
        "power_values_clamped": False, "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
    }


def _basis_request_binding(request: Mapping[str, Any]) -> dict[str, Any]:
    # The canonical digest body is owned by the installable runtime package so
    # production validators never depend on the development-only ``tools`` tree.
    from comsol_mcp._w23_basis_v2_contract import basis_request_binding

    return basis_request_binding(request)


def _basis_reference_identity(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request["request_id"], "basis_id": request["basis_id"],
        "project_id": request["project_id"], "model_ref": request["model_ref"],
        "model_revision": request["model_revision"], "geometry_revision": request["geometry_revision"],
        "frequency_hz": request["frequency_hz"], "coordinate_frame": request["coordinate_frame"],
        "case": request["case"], "output_aperture_id": request["output_surface"]["aperture_id"],
        "input_aperture_id": request["input_surface"]["aperture_id"],
        "mode_sources": [dict(row["source"]) for row in request["basis_modes"]],
        "signal_source": dict(request["signal_source"]),
        "incident_source": dict(request["incident_source"]),
    }


def validate_two_mode_native_integral_response(
    request: Mapping[str, Any], response: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the future v2 native-integral response envelope, without auth claims."""
    if (not isinstance(request, Mapping) or request.get("schema_id") != BASIS_SCHEMA_ID
            or request.get("dispatchable") is not False):
        _fail("a canonical, non-dispatchable v2 request is required")
    if not isinstance(response, Mapping):
        _fail("native v2 integral response must be an object")
    if (set(response) != {"schema_id", "status", "result_status", "origin", "request_id", "binding", "integrals"}
            or _hash(_basis_request_binding(request)) != request.get("request_id")
            or response.get("schema_id") != BASIS_SCHEMA_ID
            or response.get("status") != "SUCCEEDED"
            or response.get("result_status") != "COMPUTED_NATIVE_BASIS_INTEGRALS"
            or response.get("origin") != "COMSOL_NATIVE_INTEGRATION_FEATURES"
            or response.get("request_id") != request.get("request_id")
            or response.get("binding") != _basis_request_binding(request)):
        _fail("native integral response is not bound to the exact v2 project/model/source/surface request")
    integrals = response.get("integrals")
    if not isinstance(integrals, Mapping) or set(integrals) != {"gram", "coupling", "signal_power", "incident_power"}:
        _fail("native v2 response must contain G, b, signal power and incident power integrals")
    gram_rows, coupling_rows = integrals["gram"], integrals["coupling"]
    if (not isinstance(gram_rows, Mapping) or set(gram_rows) != {"00", "01", "10", "11"}
            or not isinstance(coupling_rows, Mapping) or set(coupling_rows) != {"0", "1"}):
        _fail("native v2 response must contain all four G entries and both b entries")

    def integral(value: Any, label: str, *, power: bool = False) -> complex:
        if not isinstance(value, Mapping) or set(value) != {"real", "imag", "unit"} or value.get("unit") != "W":
            _fail(f"native {label} must be a complex W integral with explicit imaginary component")
        result = complex(_number(value["real"], label + ".real"),
                         _number(value["imag"], label + ".imag"))
        if power and abs(result.imag) > 1e-10 * max(abs(result.real), BASIS_POLICY["power_floor_w"]):
            _fail(f"native {label} time-average power has a material imaginary residual")
        return result

    gram = [[integral(gram_rows[f"{i}{j}"], f"G[{i},{j}]") for j in range(2)] for i in range(2)]
    coupling = [integral(coupling_rows[str(i)], f"b[{i}]") for i in range(2)]
    signal_power = integral(integrals["signal_power"], "signal_power", power=True).real
    incident_power = integral(integrals["incident_power"], "incident_power", power=True).real
    if min(signal_power, incident_power) <= BASIS_POLICY["power_floor_w"]:
        _fail("native signal and incident powers must exceed the frozen W floor")
    return {"status": "SOFTWARE_NATIVE_ENVELOPE_VALIDATION_ONLY",
            "evidence_scope": "shape/unit/request-identity checks only; response origin is not authenticated here",
            "request_id": request["request_id"], "gram": gram, "coupling": coupling,
            "signal_power_w": signal_power, "incident_power_w": incident_power,
            "native_result": "NOT_RUN", "route_authentication": "NOT_PROVIDED"}


def compare_two_mode_native_integrals_to_field_reference(
    request: Mapping[str, Any], native_response: Mapping[str, Any],
    independent: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare a future native integral result to the independent field oracle.

    The response remains untrusted until a registered production adapter
    supplies managed provenance; this function is only the frozen numerical
    comparison layer.
    """
    if independent.get("native_result") != "NOT_RUN" or independent.get("status") not in {
            "SOFTWARE_ONLY_TWO_MODE_PROJECTION",
            "SOFTWARE_RECOMPUTED_TWO_MODE_BASIS_FROM_FIELD_SAMPLES"}:
        _fail("independent software basis reference is required")
    if independent.get("source_identity") != _basis_reference_identity(request):
        _fail("independent field quadrature is bound to a different v2 source/case identity")
    envelope = validate_two_mode_native_integral_response(request, native_response)
    native_projection = compute_two_mode_basis_projection(
        envelope["gram"], envelope["coupling"],
        signal_power_w=envelope["signal_power_w"], incident_power_w=envelope["incident_power_w"],
        source_identity={"request_id": request["request_id"], "basis_id": request["basis_id"]})

    # Do not trust mutable derived fields in the reference object. Re-parse
    # finite complex values and recompute its projection from the reported
    # matrix/coupling/powers before comparing any native envelope values.
    gram_rows = independent.get("gram_raw")
    coupling_rows = independent.get("coupling")
    if (not isinstance(gram_rows, list) or len(gram_rows) != 2
            or any(not isinstance(row, list) or len(row) != 2 for row in gram_rows)
            or not isinstance(coupling_rows, list) or len(coupling_rows) != 2
            or any(not isinstance(value, Mapping) or set(value) != {"real", "imag"}
                   for row in gram_rows for value in row)
            or any(not isinstance(value, Mapping) or set(value) != {"real", "imag"}
                   for value in coupling_rows)):
        _fail("independent field reference must contain a finite 2x2 Gram matrix and two couplings")
    independent_gram = [[complex(_number(row["real"], f"independent G[{i},{j}].real"),
                                _number(row["imag"], f"independent G[{i},{j}].imag"))
                         for j, row in enumerate(rows)]
                        for i, rows in enumerate(gram_rows)]
    independent_coupling = [
        complex(_number(row["real"], f"independent b[{index}].real"),
                _number(row["imag"], f"independent b[{index}].imag"))
        for index, row in enumerate(coupling_rows)]
    signal_power = _number(independent.get("signal_power_w"), "independent signal power")
    incident_power = _number(independent.get("incident_power_w"), "independent incident power")
    reference_projection = compute_two_mode_basis_projection(
        independent_gram, independent_coupling, signal_power_w=signal_power,
        incident_power_w=incident_power, source_identity=_basis_reference_identity(request))
    atol = BASIS_POLICY["native_integral_absolute_normalized_tolerance"]
    rtol = BASIS_POLICY["native_vs_quadrature_relative_tolerance"]
    relative_errors: dict[str, float] = {}
    normalized_absolute_errors: dict[str, float] = {}
    failures: list[str] = []
    normalized_mode_powers = [independent_gram[index][index].real for index in range(2)]
    mode_scales = [math.sqrt(value) for value in normalized_mode_powers]
    for i in range(2):
        for j in range(2):
            expected = independent_gram[i][j]
            native_scaled = _normalize_gram_entry(envelope["gram"][i][j], mode_scales[i],
                                                  mode_scales[j], f"native G[{i},{j}]")
            expected_scaled = _normalize_gram_entry(expected, mode_scales[i], mode_scales[j],
                                                    f"independent G[{i},{j}]")
            normalized_error = _complex_magnitude(native_scaled - expected_scaled,
                                                  f"normalized G[{i},{j}] error")
            normalized_expected = _complex_magnitude(expected_scaled,
                                                     f"normalized G[{i},{j}] reference")
            name = f"G[{i},{j}]"
            if normalized_expected <= atol:
                normalized_absolute_errors[name] = normalized_error
                if normalized_error > atol:
                    failures.append(f"{name} normalized absolute error exceeds {atol:g}")
            else:
                relative_error = normalized_error / normalized_expected
                if not math.isfinite(relative_error):
                    _fail(f"{name} normalized relative error became non-finite")
                relative_errors[name] = relative_error
                if relative_errors[name] > rtol:
                    failures.append(f"{name} relative error exceeds {rtol:g}")
    if signal_power <= BASIS_POLICY["power_floor_w"]:
        _fail("independent signal power must exceed the frozen W floor")
    signal_scale = math.sqrt(signal_power)
    for i in range(2):
        expected = independent_coupling[i]
        native_scaled = _normalize_gram_entry(envelope["coupling"][i], mode_scales[i],
                                              signal_scale, f"native b[{i}]")
        expected_scaled = _normalize_gram_entry(expected, mode_scales[i], signal_scale,
                                                f"independent b[{i}]")
        normalized_error = _complex_magnitude(native_scaled - expected_scaled,
                                              f"normalized b[{i}] error")
        normalized_expected = _complex_magnitude(expected_scaled,
                                                 f"normalized b[{i}] reference")
        name = f"b[{i}]"
        if normalized_expected <= atol:
            normalized_absolute_errors[name] = normalized_error
            if normalized_error > atol:
                failures.append(f"{name} normalized absolute error exceeds {atol:g}")
        else:
            relative_error = normalized_error / normalized_expected
            if not math.isfinite(relative_error):
                _fail(f"{name} normalized relative error became non-finite")
            relative_errors[name] = relative_error
            if relative_errors[name] > rtol:
                failures.append(f"{name} relative error exceeds {rtol:g}")
    for key, expected, observed in (
        ("signal_power", signal_power, envelope["signal_power_w"]),
        ("incident_power", incident_power, envelope["incident_power_w"]),
    ):
        if expected <= BASIS_POLICY["power_floor_w"]:
            _fail(f"independent {key} must exceed the frozen W floor")
        relative_errors[key] = abs(observed / expected - 1.0)
        if not math.isfinite(relative_errors[key]):
            _fail(f"{key} relative error became non-finite")
        if relative_errors[key] > rtol:
            failures.append(f"{key} relative error exceeds {rtol:g}")
    eta_expected = reference_projection["eta_basis_raw"]
    eta_observed = native_projection["eta_basis_raw"]
    eta_atol = BASIS_POLICY["eta_absolute_tolerance"]
    if abs(eta_expected) <= eta_atol:
        eta_error = abs(eta_observed - eta_expected)
        if not math.isfinite(eta_error):
            _fail("eta_basis absolute error became non-finite")
        normalized_absolute_errors["eta_basis"] = eta_error
        if eta_error > eta_atol:
            failures.append(f"eta_basis absolute error exceeds {eta_atol:g}")
    else:
        relative_errors["eta_basis"] = abs(eta_observed / eta_expected - 1.0)
        if not math.isfinite(relative_errors["eta_basis"]):
            _fail("eta_basis relative error became non-finite")
        if relative_errors["eta_basis"] > rtol:
            failures.append(f"eta_basis relative error exceeds {rtol:g}")
    if failures:
        _fail(f"native two-mode integrals disagree with independent complex-field quadrature: {failures}")
    return {"status": "SOFTWARE_NATIVE_INTEGRAL_COMPARISON_VALIDATION_ONLY",
            "native_result": "NOT_RUN", "route_authentication": "NOT_PROVIDED",
            "comparison_policy": dict(BASIS_POLICY), "relative_errors": relative_errors,
            "normalized_absolute_errors": normalized_absolute_errors,
            "native_projection": native_projection,
            "evidence_scope": "numerical envelope/reference comparison only; no production route or native acceptance claim"}


def independent_two_mode_basis_from_samples(
    *, signal: Mapping[str, Any], modes: Sequence[Mapping[str, Any]],
    incident: Mapping[str, Any], output_normals_xyz: Sequence[Sequence[float]],
    output_weights_m2: Sequence[float], input_normals_xyz: Sequence[Sequence[float]],
    input_weights_m2: Sequence[float], source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Independent software quadrature for a two-mode power projection."""
    if len(modes) != 2:
        _fail("exactly two complex mode field records are required")
    if len(output_weights_m2) != len(output_normals_xyz) or len(input_weights_m2) != len(input_normals_xyz):
        _fail("field, normal, and quadrature axes differ")
    matrix = [[power_form_integral(modes[i], modes[j], normals_xyz=output_normals_xyz,
                                   weights_m2=output_weights_m2) for j in range(2)] for i in range(2)]
    coupling = [power_form_integral(modes[i], signal, normals_xyz=output_normals_xyz,
                                    weights_m2=output_weights_m2) for i in range(2)]
    signal_power = power_form_integral(signal, signal, normals_xyz=output_normals_xyz,
                                       weights_m2=output_weights_m2).real
    incident_power = power_form_integral(incident, incident, normals_xyz=input_normals_xyz,
                                         weights_m2=input_weights_m2).real
    result = compute_two_mode_basis_projection(
        matrix, coupling, signal_power_w=signal_power, incident_power_w=incident_power,
        source_identity=source_identity)
    result["status"] = "SOFTWARE_RECOMPUTED_TWO_MODE_BASIS_FROM_FIELD_SAMPLES"
    result["evidence_scope"] = "software quadrature reference; use only after exact COMSOL field-readback validation"
    result["gram_source"] = "independently sampled full-complex E/H; not a native COMSOL integral"
    return result


def _field_record_from_readback(
    raw: Mapping[str, Any], contract: Mapping[str, Any], *, role: str,
) -> dict[str, list[tuple[complex, complex, complex]]]:
    rows = raw.get("expressions")
    if not isinstance(rows, list):
        _fail("native field readback is missing its expression inventory")
    records: dict[str, tuple[list[float], list[float]]] = {}
    count = contract["quadrature"]["sample_count"]
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("expression"), str):
            _fail("native field expression record is malformed")
        name = row["expression"]
        if name in records:
            _fail(f"native field expression {name!r} is duplicated")
        real, imag = row.get("real"), row.get("imag")
        if not isinstance(real, list) or not isinstance(imag, list) or len(real) != count or len(imag) != count:
            _fail(f"native field expression {name!r} has the wrong sample axis")
        records[name] = ([_number(v, name + ".real") for v in real],
                         [_number(v, name + ".imag") for v in imag])
    if set(records) != set(contract["field_names"]):
        _fail("native field expression inventory differs from the exact two-mode contract")
    suffix = "" if role == "signal" else ("_1" if role == "incident_reference" else "_2")
    prefix = "" if role == "signal" else "mode"

    def vector(kind: str) -> list[tuple[complex, complex, complex]]:
        result = []
        for sample in range(count):
            vector_values = []
            for axis in _VECTOR_AXES:
                values = records[f"ewfd.{kind}{prefix}{axis}{suffix}"]
                vector_values.append(complex(values[0][sample], values[1][sample]))
            result.append(tuple(vector_values))
        return result

    return {"E": vector("E"), "H": vector("H")}


def independent_two_mode_basis_from_native_field_readbacks(
    request: Mapping[str, Any], contracts: Mapping[str, Any], *,
    signal_raw: Mapping[str, Any], mode_raws: Sequence[Mapping[str, Any]],
    incident_raw: Mapping[str, Any], radial_intervals: int = 32,
    angular_points: int = 64,
) -> dict[str, Any]:
    """Validate exact source/grid-bound readback envelopes, then recompute G,b.

    The returned computation remains software-only. This function does not
    authenticate the production dispatcher or promote input envelopes into
    native result acceptance; a future registered adapter must provide that
    managed evidence separately.
    """
    expected = build_two_mode_basis_field_contracts(
        request, radial_intervals=radial_intervals, angular_points=angular_points)
    if (not isinstance(contracts, Mapping) or contracts.get("request_id") != request.get("request_id")
            or contracts.get("basis_id") != request.get("basis_id")
            or not isinstance(contracts.get("output"), Mapping)
            or not isinstance(contracts.get("incident"), Mapping)):
        _fail("field contracts are not bound to the requested v2 basis identity")
    expected_output = expected["output"]
    actual_output = contracts["output"]
    expected_modes = expected_output.get("modes")
    actual_modes = actual_output.get("modes")
    if (not isinstance(expected_modes, list) or not isinstance(actual_modes, list)
            or len(expected_modes) != 2 or len(actual_modes) != 2
            or len(mode_raws) != 2):
        _fail("two exact output-mode contracts/readbacks are required")

    role_items = [
        ("signal", expected_output["signal"], actual_output.get("signal"), signal_raw),
        ("mode_0", expected_modes[0], actual_modes[0], mode_raws[0]),
        ("mode_1", expected_modes[1], actual_modes[1], mode_raws[1]),
        ("incident", expected["incident"], contracts["incident"], incident_raw),
    ]
    validated: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]]] = {}
    for label, expected_contract, supplied_contract, raw in role_items:
        if (not isinstance(supplied_contract, Mapping)
                or supplied_contract.get("contract_id") != expected_contract.get("contract_id")
                or supplied_contract != expected_contract):
            _fail(f"{label} raw-field contract differs from the canonical v2 request")
        quadrature = expected["quadratures"]["input" if label == "incident" else "output"]
        role = expected_contract["role"]
        try:
            validation = validate_native_field_readback(
                expected_contract, raw, expected_quadrature=quadrature)
        except Exception as exc:
            if isinstance(exc, TwoModeBasisError):
                raise
            raise TwoModeBasisError(f"{label} native field readback failed its exact contract: {exc}") from exc
        validated[label] = (expected_contract, raw, validation)

    # Signal and both modes use the same output plane, solution frequency,
    # output ROI, normal and quadrature; incident power has its own input face.
    signal_contract = validated["signal"][0]
    for label in ("mode_0", "mode_1"):
        member_contract = validated[label][0]
        if (member_contract["plane"] != signal_contract["plane"]
                or member_contract["quadrature_sha256"] != signal_contract["quadrature_sha256"]
                or member_contract["identity"]["frequency_hz"] != signal_contract["identity"]["frequency_hz"]
                or member_contract["identity"]["model_ref"] != signal_contract["identity"]["model_ref"]
                or member_contract["identity"]["model_revision"] != signal_contract["identity"]["model_revision"]):
            _fail("signal and both basis modes must bind the identical output surface/grid/model/frequency")

    field_records = {}
    for label, (contract, raw, _validation) in validated.items():
        field_records[label] = _field_record_from_readback(raw, contract, role=contract["role"])
    output_plane = signal_contract["plane"]
    incident_plane = validated["incident"][0]["plane"]
    output_normal = output_plane["physical_forward_normal_xyz"]
    input_normal = incident_plane["physical_forward_normal_xyz"]
    out_count = len(expected["quadratures"]["output"]["weights_m2"])
    in_count = len(expected["quadratures"]["input"]["weights_m2"])
    source_identity = _basis_reference_identity(request)
    result = independent_two_mode_basis_from_samples(
        signal=field_records["signal"],
        modes=[field_records["mode_0"], field_records["mode_1"]],
        incident=field_records["incident"],
        output_normals_xyz=[output_normal] * out_count,
        output_weights_m2=expected["quadratures"]["output"]["weights_m2"],
        input_normals_xyz=[input_normal] * in_count,
        input_weights_m2=expected["quadratures"]["input"]["weights_m2"],
        source_identity=source_identity)
    result["field_contract_ids"] = {
        label: row[0]["contract_id"] for label, row in validated.items()}
    result["field_readback_validations"] = {
        label: row[2] for label, row in validated.items()}
    result["evidence_scope"] = (
        "software quadrature recomputation from contract-validated field readback envelopes; "
        "managed provenance and native integral acceptance remain separate gates")
    return result


def change_basis_coordinates(
    gram: Sequence[Sequence[complex]], coupling: Sequence[complex],
    transform: Sequence[Sequence[complex]],
) -> tuple[list[list[complex]], list[complex]]:
    """Apply M_new=M_old*T, so G_new=T^H G T and b_new=T^H b."""
    if (len(gram) != 2 or any(len(row) != 2 for row in gram) or len(coupling) != 2
            or len(transform) != 2 or any(len(row) != 2 for row in transform)):
        _fail("basis change requires two-dimensional complex matrix inputs")
    g = [[_complex(gram[i][j], f"G[{i},{j}]") for j in range(2)] for i in range(2)]
    b = [_complex(coupling[i], f"b[{i}]") for i in range(2)]
    t = [[_complex(transform[i][j], f"T[{i},{j}]") for j in range(2)] for i in range(2)]
    determinant = t[0][0] * t[1][1] - t[0][1] * t[1][0]
    if abs(determinant) <= 1e-14:
        _fail("basis transform must be invertible")
    # First multiply G*T, then T^H*(G*T).
    gt = [[sum(g[i][k] * t[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    transformed_g = [[sum(t[k][i].conjugate() * gt[k][j] for k in range(2)) for j in range(2)]
                     for i in range(2)]
    transformed_b = [sum(t[k][i].conjugate() * b[k] for k in range(2)) for i in range(2)]
    return transformed_g, transformed_b


__all__ = [
    "BASIS_ALGORITHM_ID", "BASIS_POLICY", "BASIS_PROFILE_ID", "BASIS_SCHEMA_ID",
    "TwoModeBasisError", "build_two_mode_basis_field_contracts", "build_two_mode_basis_request",
    "change_basis_coordinates", "compute_two_mode_basis_projection",
    "compare_two_mode_native_integrals_to_field_reference",
    "independent_two_mode_basis_from_native_field_readbacks",
    "independent_two_mode_basis_from_samples", "power_form_integral",
    "validate_two_mode_native_integral_response",
]
