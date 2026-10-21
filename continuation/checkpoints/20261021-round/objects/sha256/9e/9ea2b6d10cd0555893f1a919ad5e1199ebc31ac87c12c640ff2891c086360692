"""Explicit complex Scaling-map software candidate for W23 local-cap geometry.

This module creates inspectable COMSOL expression strings and independent raw-
gradient math. It never calls COMSOL, mutates a model, or asserts that the
Scaling map accepts complex values or is consumed by a physics interface.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.w23_full3d import build_case_matrix
from tools.w23_radiation_geometry_v2 import (
    _face_definitions,
    _port_aperture_polygon,
    canonical_radiation_geometry_v2,
)

SCHEMA_ID = "urn:comsol-mcp:w23:explicit-complex-scaling-map:1.0.0"
BASE_COMMIT = "3d63ad9f8bfe8d04903e1a09b1b1aee4e103a959"
CASE_IDS = tuple(row["case_id"] for row in build_case_matrix())
CV_BOX_UM = {"x": [-19.0, 19.0], "y": [-5.5, 5.5], "z": [-5.5, 5.5]}
TRANSITION_START_UM = 19.0
TRANSITION_END_UM = 19.9
# Keep the frozen 0.9 um interval literal so rendered expressions are stable.
TRANSITION_LENGTH_UM = 0.9
BACKING_UM = 1.55
PML_THICKNESS_UM = 2.0
LAMBDA_UM = 1.55
MAX_TILT_DEG = 0.5

_DIRECTION_PAIRS = (
    ("input_axial", "output_axial"),
    ("transverse_u_minus", "transverse_u_plus"),
    ("transverse_v_minus", "transverse_v_plus"),
)
_COORD_NAMES = ("x", "y", "z")
_I3 = [[float(i == j) for j in range(3)] for i in range(3)]


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def _fmt(value: float) -> str:
    value = _finite(value, "expression coefficient")
    if abs(value) < 5e-18:
        value = 0.0
    return format(value, ".15g")


def _frame(theta_y_deg: float, theta_z_deg: float) -> tuple[list[float], list[float], list[float]]:
    ty, tz = math.radians(theta_y_deg), math.radians(theta_z_deg)
    cy, sy, cz, sz = math.cos(ty), math.sin(ty), math.cos(tz), math.sin(tz)
    rotation = (
        (cz * cy, -sz, cz * sy),
        (sz * cy, cz, sz * sy),
        (-sy, 0.0, cy),
    )
    axis = [rotation[i][0] for i in range(3)]
    eu = [rotation[i][1] for i in range(3)]
    ev = [rotation[i][2] for i in range(3)]
    return axis, eu, ev


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def _mat_outer(a: Sequence[float], b: Sequence[float]) -> list[list[float]]:
    return [[float(a[i]) * float(b[j]) for j in range(3)] for i in range(3)]


def _mat_add_inplace(target: list[list[complex]], addition: Sequence[Sequence[complex]]) -> None:
    for i in range(3):
        for j in range(3):
            target[i][j] += addition[i][j]


def _case_record(case_id: str) -> Mapping[str, Any]:
    if case_id not in CASE_IDS:
        raise ValueError("case_id is not one of the 19 frozen W23 one-factor cases")
    return next(row for row in build_case_matrix() if row["case_id"] == case_id)


def _case_geometry(case: Mapping[str, Any]) -> dict[str, Any]:
    transform = case.get("receiver_transform")
    if not isinstance(transform, Mapping):
        raise ValueError("canonical receiver transform is required")
    theta_y = _finite(transform.get("theta_y_deg"), "theta_y_deg")
    theta_z = _finite(transform.get("theta_z_deg"), "theta_z_deg")
    if abs(theta_y) > MAX_TILT_DEG + 1e-12 or abs(theta_z) > MAX_TILT_DEG + 1e-12:
        raise ValueError("receiver tilt exceeds the frozen +/-0.5 degree envelope")
    if abs(theta_y) > 1e-12 and abs(theta_z) > 1e-12:
        raise ValueError("simultaneous compound tilt is outside the frozen 19-case matrix")
    axis, eu, ev = _frame(theta_y, theta_z)
    center = [_finite(x, "receiver center") for x in transform["center_xyz_um"]]
    if len(center) != 3:
        raise ValueError("receiver center must have three coordinates")
    recipe = canonical_radiation_geometry_v2()
    geom = recipe["geometry"]
    faces = _face_definitions(
        transform,
        float(geom["backing_length_um"]),
        float(geom["axial_pml_thickness_um"]),
        float(geom["transverse_pml_thickness_um"]),
        float(geom["transverse_inner_halfwidth_um"]),
    )
    aperture = _port_aperture_polygon(recipe, transform, faces, "output")
    uv = aperture.get("polygon_vertices_local_uv_um")
    if not isinstance(uv, list) or len(uv) < 3:
        raise ValueError("full physical halfspace/Port-plane aperture polygon is missing")
    u_values = [_finite(point[0], "port local u") for point in uv]
    v_values = [_finite(point[1], "port local v") for point in uv]
    bounds = (min(u_values), max(u_values), min(v_values), max(v_values))
    if not bounds[0] < bounds[1] or not bounds[2] < bounds[3]:
        raise ValueError("full Port aperture has degenerate local bounds")
    return {
        "case_id": case["case_id"],
        "case_name": case["case_name"],
        "factor": case["factor"],
        "value": case["value"],
        "transform": transform,
        "axis": axis,
        "eu": eu,
        "ev": ev,
        "center": center,
        "local_bounds_uv_um": list(bounds),
        "full_port_polygon_local_uv_um": uv,
        "full_port_polygon_global_xyz_um": aperture["polygon_vertices_global_xyz_um"],
        "full_port_polygon_area_um2": aperture["analytic_polygon_area_um2"],
        "minimum_square_halfwidth_um": geom["port_air_aperture"]["minimum_local_square_halfwidth_um"],
    }


def _active_regions(*, zone: str) -> list[tuple[str, ...]]:
    if zone == "global":
        states = ((None, "input_axial"), (None, "transverse_u_minus", "transverse_u_plus"),
                  (None, "transverse_v_minus", "transverse_v_plus"))
    elif zone == "transition":
        states = ((None,), (None, "transverse_u_minus", "transverse_u_plus"),
                  (None, "transverse_v_minus", "transverse_v_plus"))
    elif zone == "output_cap":
        states = ((None, "output_axial"), (None, "transverse_u_minus", "transverse_u_plus"),
                  (None, "transverse_v_minus", "transverse_v_plus"))
    else:
        raise ValueError("zone must be global, transition, or output_cap")
    rows: list[tuple[str, ...]] = []
    for choices in itertools.product(*states):
        active = tuple(value for value in choices if value is not None)
        if active:
            rows.append(active)
    return rows


def _coord_expr(coefficients: Sequence[float], point: Sequence[float]) -> str:
    terms = [f"({_fmt(coefficients[i])})*({_COORD_NAMES[i]}-({_fmt(point[i])}[um]))"
             for i in range(3)]
    return "(" + "+".join(terms) + ")"


def _unit_expr(vector: Sequence[float]) -> tuple[str, str, str]:
    return tuple(_fmt(float(value)) for value in vector)  # type: ignore[return-value]


def _transition_fields_expr(geometry: Mapping[str, Any], axis_name: str,
                            side: str) -> tuple[str, tuple[str, str, str]]:
    if axis_name == "u":
        ec, global_axis, global_name = geometry["eu"], (0.0, 1.0, 0.0), "y"
        lo, hi = geometry["local_bounds_uv_um"][:2]
    else:
        ec, global_axis, global_name = geometry["ev"], (0.0, 0.0, 1.0), "z"
        lo, hi = geometry["local_bounds_uv_um"][2:]
    center = geometry["center"]
    local_dot = _coord_expr(ec, center)
    label = f"({local_dot}-({global_name}))"
    t = f"((x-{_fmt(TRANSITION_START_UM)}[um])/{_fmt(TRANSITION_LENGTH_UM)}[um])"
    w = f"(6*({t})^5-15*({t})^4+10*({t})^3)"
    wp = f"(30*({t})^2*(1-({t}))^2/{_fmt(TRANSITION_LENGTH_UM)}[um])"
    global_expr = global_name
    global_vector = global_axis
    ec_expr = _unit_expr(ec)
    if side == "plus":
        offset = hi - 6.0
        distance = f"(({global_expr})+({w})*({label})-(6[um]+({w})*({_fmt(offset)}[um])))"
        grad = (
            f"(({w})*({ec_expr[0]})+({wp})*(({label})-({_fmt(offset)}[um])))",
            f"((1-({w}))*({_fmt(global_vector[1])})+({w})*({ec_expr[1]}))",
            f"((1-({w}))*({_fmt(global_vector[2])})+({w})*({ec_expr[2]}))",
        )
    elif side == "minus":
        offset = lo + 6.0
        distance = f"(-6[um]+({w})*({_fmt(offset)}[um])-({global_expr})-({w})*({label}))"
        grad = (
            f"(({wp})*(({_fmt(offset)}[um])-({label}))-({w})*({ec_expr[0]}))",
            f"(-((1-({w}))*({_fmt(global_vector[1])})+({w})*({ec_expr[1]})))",
            f"(-((1-({w}))*({_fmt(global_vector[2])})+({w})*({ec_expr[2]})))",
        )
    else:
        raise ValueError("side must be plus or minus")
    return distance, grad


def _field_expressions(geometry: Mapping[str, Any], zone: str,
                       direction: str) -> tuple[str, tuple[str, str, str]]:
    if direction == "input_axial":
        return "(-(x+21.55[um]))", ("-1", "0", "0")
    if direction == "output_axial":
        axis = geometry["axis"]
        point = [geometry["center"][i] + BACKING_UM * axis[i] for i in range(3)]
        return _coord_expr(axis, point), _unit_expr(axis)
    prefix, side = direction.rsplit("_", 1)
    axis_name = "u" if prefix == "transverse_u" else "v"
    if zone == "global":
        sign = 1.0 if side == "plus" else -1.0
        coord = "y" if axis_name == "u" else "z"
        distance = f"({coord}-6[um])" if side == "plus" else f"(-6[um]-{coord})"
        vector = (0.0, sign, 0.0) if axis_name == "u" else (0.0, 0.0, sign)
        return distance, _unit_expr(vector)
    if zone == "transition":
        return _transition_fields_expr(geometry, axis_name, side)
    if zone == "output_cap":
        vector = geometry["eu"] if axis_name == "u" else geometry["ev"]
        low, high = (geometry["local_bounds_uv_um"][:2] if axis_name == "u"
                     else geometry["local_bounds_uv_um"][2:])
        if side == "plus":
            return f"({_coord_expr(vector, geometry['center'])}-{_fmt(high)}[um])", _unit_expr(vector)
        return f"({_fmt(low)}[um]-{_coord_expr(vector, geometry['center'])})", _unit_expr([-x for x in vector])
    raise ValueError("unknown map zone")


def _delta_expr(distance_expr: str) -> str:
    return f"(1.55[um]*(({distance_expr})/(2[um]))*(1-i)-({distance_expr}))"


def render_map_expressions(geometry: Mapping[str, Any], zone: str,
                           active_directions: Sequence[str]) -> tuple[str, str, str]:
    """Render the documented raw-gradient coordinate map for one selected cell."""
    allowed = {direction for pair in _DIRECTION_PAIRS for direction in pair}
    active = tuple(active_directions)
    if not active or len(set(active)) != len(active) or any(item not in allowed for item in active):
        raise ValueError("one unique nonempty registered stretch-direction set is required")
    for left, right in (("input_axial", "output_axial"),
                        ("transverse_u_minus", "transverse_u_plus"),
                        ("transverse_v_minus", "transverse_v_plus")):
        if left in active and right in active:
            raise ValueError("opposing stretch directions cannot own the same partition cell")
    if zone == "transition" and any(item in active for item in ("input_axial", "output_axial")):
        raise ValueError("the transition slab contains only transverse side/corner cells")
    if zone == "global" and "output_axial" in active:
        raise ValueError("the output axial cap does not occupy the global-frame zone")
    if zone == "output_cap" and "input_axial" in active:
        raise ValueError("the input axial cap does not occupy the output-cap zone")
    terms: list[list[str]] = [[], [], []]
    for direction in active:
        distance, gradient = _field_expressions(geometry, zone, direction)
        delta = _delta_expr(distance)
        for index in range(3):
            terms[index].append(f"({delta})*({gradient[index]})")
    return tuple(
        _COORD_NAMES[index] + "".join("+" + term for term in terms[index])
        for index in range(3)
    )  # type: ignore[return-value]


def _transition_numeric(point_um: Sequence[float], geometry: Mapping[str, Any],
                         axis_name: str, side: str) -> tuple[float, list[float], list[list[float]]]:
    x, y, z = [float(value) for value in point_um]
    eu, ev, center = geometry["eu"], geometry["ev"], geometry["center"]
    if axis_name == "u":
        ec, global_axis, global_coordinate = eu, [0.0, 1.0, 0.0], y
        low, high = geometry["local_bounds_uv_um"][:2]
    else:
        ec, global_axis, global_coordinate = ev, [0.0, 0.0, 1.0], z
        low, high = geometry["local_bounds_uv_um"][2:]
    length = TRANSITION_LENGTH_UM
    t = (x - TRANSITION_START_UM) / length
    w = 6 * t**5 - 15 * t**4 + 10 * t**3
    wp = 30 * t**2 * (1 - t) ** 2 / length
    wpp = 60 * t * (1 - t) * (1 - 2 * t) / (length * length)
    r_minus_p = [point_um[i] - center[i] for i in range(3)]
    local_coordinate = _dot(ec, r_minus_p)
    label = local_coordinate - global_coordinate
    h = global_coordinate + w * label
    grad_h = [global_axis[i] + w * (ec[i] - global_axis[i]) for i in range(3)]
    grad_h[0] += wp * label
    hessian_h = [[0.0] * 3 for _ in range(3)]
    hessian_h[0][0] = wpp * label
    for i in range(3):
        hessian_h[0][i] += wp * (ec[i] - global_axis[i])
        hessian_h[i][0] += wp * (ec[i] - global_axis[i])
    ex = [1.0, 0.0, 0.0]
    if side == "plus":
        offset = high - 6.0
        distance = h - (6.0 + w * offset)
        grad = [grad_h[i] - (wp * offset if i == 0 else 0.0) for i in range(3)]
        hessian = [row[:] for row in hessian_h]
        hessian[0][0] -= wpp * offset
    elif side == "minus":
        offset = low + 6.0
        distance = (-6.0 + w * offset) - h
        grad = [(wp * offset if i == 0 else 0.0) - grad_h[i] for i in range(3)]
        hessian = [[-hessian_h[i][j] for j in range(3)] for i in range(3)]
        hessian[0][0] += wpp * offset
    else:
        raise ValueError("side must be plus or minus")
    return distance, grad, hessian


def evaluate_direction(point_um: Sequence[float], geometry: Mapping[str, Any],
                       zone: str, direction: str) -> tuple[float, list[float], list[list[float]]]:
    """Evaluate d, g, H in micrometers for raw-map software validation."""
    if len(point_um) != 3 or not all(math.isfinite(float(x)) for x in point_um):
        raise ValueError("point_um must be a finite 3-vector")
    x, y, z = [float(value) for value in point_um]
    zero = [[0.0] * 3 for _ in range(3)]
    if direction == "input_axial":
        return -x - 21.55, [-1.0, 0.0, 0.0], zero
    if direction == "output_axial":
        axis = geometry["axis"]
        p_back = [geometry["center"][i] + BACKING_UM * axis[i] for i in range(3)]
        d = _dot(axis, [point_um[i] - p_back[i] for i in range(3)])
        return d, list(axis), zero
    prefix, side = direction.rsplit("_", 1)
    axis_name = "u" if prefix == "transverse_u" else "v"
    if zone == "global":
        if axis_name == "u":
            return (y - 6.0, [0.0, 1.0, 0.0], zero) if side == "plus" else (-6.0 - y, [0.0, -1.0, 0.0], zero)
        return (z - 6.0, [0.0, 0.0, 1.0], zero) if side == "plus" else (-6.0 - z, [0.0, 0.0, -1.0], zero)
    if zone == "transition":
        return _transition_numeric(point_um, geometry, axis_name, side)
    if zone == "output_cap":
        vector = geometry["eu"] if axis_name == "u" else geometry["ev"]
        low, high = (geometry["local_bounds_uv_um"][:2] if axis_name == "u"
                     else geometry["local_bounds_uv_um"][2:])
        local_coordinate = _dot(vector, [point_um[i] - geometry["center"][i] for i in range(3)])
        if side == "plus":
            return local_coordinate - high, list(vector), zero
        return low - local_coordinate, [-x for x in vector], zero
    raise ValueError("unknown map zone")


def _delta(distance_um: float) -> complex:
    if not math.isfinite(distance_um):
        raise ValueError("PML distance must be finite")
    if distance_um < -1e-9 or distance_um > PML_THICKNESS_UM + 1e-9:
        raise ValueError("distance is outside the registered PML [0,2 um] layer")
    d = min(max(distance_um, 0.0), PML_THICKNESS_UM)
    return LAMBDA_UM * (d / PML_THICKNESS_UM) * (1.0 - 1.0j) - d


def evaluate_complex_map(point_um: Sequence[float], geometry: Mapping[str, Any],
                         zone: str, active_directions: Sequence[str]) -> list[complex]:
    mapped = [complex(float(value), 0.0) for value in point_um]
    if not active_directions:
        return mapped
    for direction in active_directions:
        distance, gradient, _ = evaluate_direction(point_um, geometry, zone, direction)
        delta = _delta(distance)
        for index in range(3):
            mapped[index] += delta * gradient[index]
    return mapped


def evaluate_raw_jacobian(point_um: Sequence[float], geometry: Mapping[str, Any],
                          zone: str, active_directions: Sequence[str]) -> list[list[complex]]:
    matrix = [[complex(_I3[i][j], 0.0) for j in range(3)] for i in range(3)]
    delta_prime = LAMBDA_UM / PML_THICKNESS_UM * (1.0 - 1.0j) - 1.0
    for direction in active_directions:
        distance, gradient, hessian = evaluate_direction(point_um, geometry, zone, direction)
        delta = _delta(distance)
        outer = _mat_outer(gradient, gradient)
        contribution = [[delta * hessian[i][j] + delta_prime * outer[i][j]
                         for j in range(3)] for i in range(3)]
        _mat_add_inplace(matrix, contribution)
    return matrix


def _region_id(zone: str, active: Sequence[str]) -> str:
    return zone + "__pml__" + "__".join(active)


def build_case_maps(case_id: str) -> dict[str, Any]:
    case = _case_record(case_id)
    geometry = _case_geometry(case)
    zones = ("global", "transition", "output_cap")
    rows = []
    for zone in zones:
        for active in _active_regions(zone=zone):
            maps = render_map_expressions(geometry, zone, active)
            rows.append({
                "region_id": _region_id(zone, active),
                "zone": zone,
                "active_directions": list(active),
                "scaling_map_xyz": list(maps),
                "scaling_map_sha256": hashlib.sha256("\n".join(maps).encode()).hexdigest(),
                "domain_ids": None,
                "domain_binding_status": "NOT_READ",
                "complex_map_property_support": "UNVERIFIED",
                "physics_equation_transform_status": "UNVERIFIED",
            })
    return {
        "schema_id": SCHEMA_ID,
        "case_id": case_id,
        "case_name": geometry["case_name"],
        "case_factor": geometry["factor"],
        "case_value": geometry["value"],
        "receiver_axis_xyz": geometry["axis"],
        "receiver_local_eu_xyz": geometry["eu"],
        "receiver_local_ev_xyz": geometry["ev"],
        "receiver_port_center_um": geometry["center"],
        "full_port_polygon_local_uv_um": geometry["full_port_polygon_local_uv_um"],
        "full_port_polygon_global_xyz_um": geometry["full_port_polygon_global_xyz_um"],
        "full_port_polygon_area_um2": geometry["full_port_polygon_area_um2"],
        "minimum_local_square_halfwidth_um": geometry["minimum_square_halfwidth_um"],
        "straight_physical_fibers": True,
        "physical_material_continuation": ["fiber_core", "fiber_cladding", "air"],
        "backing_um": BACKING_UM,
        "axial_pml_um": PML_THICKNESS_UM,
        "transverse_pml_um": PML_THICKNESS_UM,
        "control_volume_um": CV_BOX_UM,
        "transition_x_um": [TRANSITION_START_UM, TRANSITION_END_UM],
        "zone_partition_required": True,
        "region_maps": rows,
        "native_domain_ids": "NOT_READ",
        "native_model_status": "NOT_RUN",
        "map_expression_support": "UNVERIFIED",
        "physics_adoption_and_equation_transform": "UNVERIFIED",
    }


def build_candidate_manifest() -> dict[str, Any]:
    expected_ids = tuple(row["case_id"] for row in build_case_matrix())
    if expected_ids != CASE_IDS or len(expected_ids) != 19 or len(set(expected_ids)) != 19:
        raise ValueError("published W23 one-factor case matrix changed from the frozen 19-case scope")
    cases = [build_case_maps(case_id) for case_id in CASE_IDS]
    if any(len(row["region_maps"]) != 42 for row in cases):
        raise ValueError("each case must contain the three disjoint global/transition/output-cap partitions")
    return {
        "schema_id": SCHEMA_ID,
        "base_commit": BASE_COMMIT,
        "status": "SOFTWARE_MAP_CANDIDATE_ONLY",
        "native_status": "NOT_RUN",
        "runtime_complex_map_support": "UNVERIFIED",
        "physics_coordinate_transform": "UNVERIFIED",
        "solver_or_study_invoked": False,
        "coordinate_map": "G(r)=r+sum_i Delta_i(d_i(r))*grad(d_i(r)); raw gradient, no normalization",
        "raw_complex_jacobian": "DG=I+sum_i[Delta_i*H_i+Delta_i_prime*(g_i outer g_i)]",
        "pml_profile": {
            "typical_wavelength_um": LAMBDA_UM,
            "dmax_um": PML_THICKNESS_UM,
            "factor": 1.0,
            "gamma": 1.0,
            "delta": "lambda*(d/dmax)*(1-i)-d",
        },
        "one_factor_case_count": len(cases),
        "case_ids": list(CASE_IDS),
        "zone_partition": {
            "global_frame_x_max_um": TRANSITION_START_UM,
            "local_transition_x_um": [TRANSITION_START_UM, TRANSITION_END_UM],
            "receiver_local_cap_x_min_um": TRANSITION_END_UM,
            "opposing_direction_overlap_forbidden": True,
        },
        "geometry_contract": {
            "port_aperture": "complete Numeric Port plane intersected with registered physical halfspaces; retain full polygon and 5.5 um square",
            "capture_surface_separate": True,
            "fiber_axes": "physical core/cladding remain straight on their actual registered axes",
            "backing_materials": ["fiber_core", "fiber_cladding", "air"],
            "backing_um": BACKING_UM,
            "axial_pml_um": PML_THICKNESS_UM,
            "control_volume_um": CV_BOX_UM,
            "control_volume_coordinate_map": "identity; no scaling-domain assignment",
            "geometry_and_domain_readback": "NOT_RUN",
        },
        "cases": cases,
    }


def map_expression_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    if manifest.get("schema_id") != SCHEMA_ID or manifest.get("base_commit") != BASE_COMMIT:
        raise ValueError("candidate manifest identity differs from the frozen schema/base")
    result: dict[str, str] = {}
    for case in manifest.get("cases", []):
        case_id = case.get("case_id")
        for row in case.get("region_maps", []):
            key = f"{case_id}/{row['region_id']}"
            actual = hashlib.sha256("\n".join(row["scaling_map_xyz"]).encode()).hexdigest()
            if actual != row.get("scaling_map_sha256"):
                raise ValueError("caller-tampered Scaling map expression digest")
            result[key] = actual
    if len(result) != 19 * 42:
        raise ValueError("candidate map closure is incomplete or has duplicate region keys")
    return result
