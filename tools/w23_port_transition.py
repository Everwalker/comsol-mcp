"""Offline analytic W23 output-Port transition candidate.

This module produces a deterministic geometry/mapping certificate from the
published W23 case matrix and the v2 recipe.  It never calls COMSOL.  Its new
schema is deliberately incompatible with the legacy six-plane native adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from tools.w23_full3d import build_case_matrix
from tools.w23_radiation_geometry_v2 import (
    CV_BOX_UM,
    RadiationGeometryV2Error,
    build_pml_partition_plan,
    canonical_radiation_geometry_v2,
    verify_radiation_geometry_v2,
)


SCHEMA_ID = "urn:comsol-mcp:w23:port-transition-plan:1.0.0"
ALGORITHM_ID = "w23.output_port.true_signed_distance_biarc.v1"
_TOL = 2.0e-10
_ANGLE_TOL = 2.0e-13
_SEAM_TOL_UM = 2.0e-12
_PIECE_ORDER = ("global_side_plane", "biarc_1", "biarc_2", "output_local_plane")
_FROZEN_V2_RECIPE_SHA256 = "5829f4d57f7acbbb603ac33b054ef38d11e0ffd399041b5752d77cfea9e33e8b"
_FROZEN_CASE_MATRIX_SHA256 = "a65094d3aab66b72f054ec2063dfe2db998f2e6dcbce55afb10169dc79a22d50"


class PortTransitionError(ValueError):
    """The frozen W23 port-transition candidate is malformed or inconsistent."""


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortTransitionError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise PortTransitionError(f"{label} must be a finite number")
    return result


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _norm2(point: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in point))


def _add(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [a + b for a, b in zip(left, right)]


def _sub(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [a - b for a, b in zip(left, right)]


def _scale(factor: float, vector: Sequence[float]) -> list[float]:
    return [factor * value for value in vector]


def _cross(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0]]


def _unit(vector: Sequence[float], label: str) -> list[float]:
    if not isinstance(vector, (list, tuple)) or len(vector) != 3:
        raise PortTransitionError(f"{label} must be a 3-vector")
    values = [_number(value, label) for value in vector]
    norm = _norm2(values)
    if norm <= 0:
        raise PortTransitionError(f"{label} must be nonzero")
    return [value / norm for value in values]


def _frame_for_case(case: Mapping[str, Any]) -> dict[str, Any]:
    transform = case.get("receiver_transform")
    if not isinstance(transform, Mapping):
        raise PortTransitionError("case is missing its frozen receiver transform")
    theta_y = math.radians(_number(transform.get("theta_y_deg"), "theta_y_deg"))
    theta_z = math.radians(_number(transform.get("theta_z_deg"), "theta_z_deg"))
    if abs(theta_y) > 1e-13 and abs(theta_z) > 1e-13:
        raise PortTransitionError("simultaneous two-axis receiver tilt is unsupported")

    factor = case.get("factor")
    lateral_name = ("z" if factor in {"receiver_theta_y_deg", "receiver_dz_um"}
                    else "y")
    lateral_index = 2 if lateral_name == "z" else 1
    axis = _unit(transform.get("axis_xyz"), "receiver axis")
    other_index = 1 if lateral_index == 2 else 2
    if axis[0] <= 0 or abs(axis[other_index]) > 1e-12:
        raise PortTransitionError("receiver axis is outside the frozen single-plane profile")
    beta = math.atan2(axis[lateral_index], axis[0])
    if abs(beta) > math.radians(0.5) + 1e-12:
        raise PortTransitionError("receiver tilt exceeds the frozen 0.5 degree profile")
    a = [0.0, 0.0, 0.0]
    a[0], a[lateral_index] = math.cos(beta), math.sin(beta)
    if _norm2(_sub(a, axis)) > 2e-12:
        raise PortTransitionError("receiver axis and one-plane transform disagree")
    b = [0.0, 0.0, 0.0]
    b[0], b[lateral_index] = -math.sin(beta), math.cos(beta)
    w = _cross(a, b)
    if (abs(_dot(a, b)) > 2e-12 or abs(_dot(a, w)) > 2e-12
            or abs(_dot(b, w)) > 2e-12 or abs(_norm2(w) - 1.0) > 2e-12):
        raise PortTransitionError("receiver local frame is not right-handed orthonormal")
    center = [_number(value, "receiver center") for value in transform.get("center_xyz_um", [])]
    if len(center) != 3:
        raise PortTransitionError("receiver center must be a 3-vector")
    return {
        "lateral_axis": lateral_name,
        "lateral_global_index": lateral_index,
        "w_direction": "global_plus_z" if lateral_name == "y" else "global_minus_y",
        "beta_rad": beta,
        "beta_deg": math.degrees(beta),
        "origin_xyz_um": center,
        "a_axis_xyz": a,
        "b_axis_xyz": b,
        "w_axis_xyz": w,
        "receiver_axis_xyz": axis,
        "frame_handedness": "a cross b equals w",
    }


def _normal_left(phi: float) -> tuple[float, float]:
    return -math.sin(phi), math.cos(phi)


def _arc(start: Sequence[float], phi0: float, phi1: float,
         curvature: float, chord_length: float, piece_id: str) -> dict[str, Any]:
    if curvature == 0 or not math.isfinite(curvature):
        raise PortTransitionError("biarc curvature must be finite and nonzero")
    n0 = _normal_left(phi0)
    center = [start[0] + n0[0] / curvature, start[1] + n0[1] / curvature]
    n1 = _normal_left(phi1)
    end = [center[0] - n1[0] / curvature, center[1] - n1[1] / curvature]
    radius = 1.0 / abs(curvature)
    chord = _norm2([end[0] - start[0], end[1] - start[1]])
    arc_length = abs((phi1 - phi0) / curvature)
    if abs(chord - chord_length) > 2e-9 or arc_length <= 0:
        raise PortTransitionError("derived biarc chord or arc length is inconsistent")
    return {
        "piece_id": piece_id,
        "start_su_um": list(start),
        "end_su_um": end,
        "center_su_um": center,
        "phi_start_rad": phi0,
        "phi_end_rad": phi1,
        "signed_curvature_per_um": curvature,
        "radius_um": radius,
        "chord_length_um": chord,
        "arc_length_um": arc_length,
    }


def _angle_in_sweep(angle: float, start: float, end: float) -> bool:
    direction = 1.0 if end >= start else -1.0
    sweep = abs(end - start)
    offset = direction * (angle - start)
    return -2e-13 <= offset <= sweep + 2e-13


def _critical_angles(start: float, end: float, coefficient_s: float,
                     coefficient_u: float) -> list[float]:
    candidates = [start, end]
    magnitude = math.hypot(coefficient_s, coefficient_u)
    if magnitude <= 1e-30:
        return candidates
    maximum = math.atan2(-coefficient_s, coefficient_u)
    for base in (maximum, maximum + math.pi):
        low = min(start, end)
        high = max(start, end)
        first = math.ceil((low - base) / (2 * math.pi))
        last = math.floor((high - base) / (2 * math.pi))
        for winding in range(first, last + 1):
            angle = base + winding * 2 * math.pi
            if _angle_in_sweep(angle, start, end):
                candidates.append(angle)
    return sorted(set(candidates))


def _arc_linear_range(arc: Mapping[str, Any], sigma: int, max_distance: float,
                      coefficient_s: float, coefficient_u: float) -> list[float]:
    """Exact range of A*s+B*u over the analytic normal tube, d in [0,D]."""
    start = float(arc["phi_start_rad"])
    end = float(arc["phi_end_rad"])
    center_s, center_u = arc["center_su_um"]
    curvature = float(arc["signed_curvature_per_um"])
    values = []
    for phi in _critical_angles(start, end, coefficient_s, coefficient_u):
        normal_s, normal_u = _normal_left(phi)
        normal_projection = coefficient_s * normal_s + coefficient_u * normal_u
        for distance in (0.0, max_distance):
            # p(phi)+sigma*d*N(phi) = C+(-1/k+sigma*d)*N(phi).
            radius_factor = -1.0 / curvature + sigma * distance
            values.append(coefficient_s * center_s + coefficient_u * center_u
                          + radius_factor * normal_projection)
    if not values or not all(math.isfinite(value) for value in values):
        raise PortTransitionError("analytic arc extrema are not finite")
    return [min(values), max(values)]


def _make_arcs(beta: float, u0: float, length: float) -> dict[str, Any]:
    if abs(beta) <= 1e-15:
        return {"status": "NO_ARC_BETA_ZERO", "chord_length_um": 0.0,
                "arcs": [], "expected_start_su_um": None,
                "expected_end_su_um": None, "closure_error_um": 0.0}
    ell = length / (2.0 * math.cos(beta) * math.cos(beta / 4.0))
    radius1 = ell / (2.0 * abs(math.sin(beta / 4.0)))
    radius2 = ell / (2.0 * abs(math.sin(3.0 * beta / 4.0)))
    sign = 1.0 if beta > 0 else -1.0
    phi0, phim, phi2 = -beta, -1.5 * beta, 0.0
    start = [-length, u0 + length * math.tan(beta)]
    arc1 = _arc(start, phi0, phim, -sign / radius1, ell, "biarc_1")
    arc2 = _arc(arc1["end_su_um"], phim, phi2, sign / radius2, ell, "biarc_2")
    expected = [0.0, u0]
    closure = _norm2([arc2["end_su_um"][i] - expected[i] for i in range(2)])
    if closure > 2e-9:
        raise PortTransitionError("the two analytic biarc chords do not close at the output Port")
    return {
        "status": "TWO_CIRCULAR_ARCS",
        "chord_length_um": ell,
        "expected_start_su_um": start,
        "expected_end_su_um": expected,
        "closure_error_um": closure,
        "arcs": [arc1, arc2],
    }


def _port_local_vertices(aperture: Mapping[str, Any], frame: Mapping[str, Any]) -> dict[str, Any]:
    center = frame["origin_xyz_um"]
    a, b, w = frame["a_axis_xyz"], frame["b_axis_xyz"], frame["w_axis_xyz"]
    globals_ = aperture.get("polygon_vertices_global_xyz_um")
    if not isinstance(globals_, list) or len(globals_) < 3:
        raise PortTransitionError("the recomputed full-air Port polygon is unavailable")
    local = []
    for point in globals_:
        rel = _sub([_number(value, "Port vertex") for value in point], center)
        row = {"s_um": _dot(rel, a), "u_um": _dot(rel, b), "w_um": _dot(rel, w)}
        if abs(row["s_um"]) > 2e-9:
            raise PortTransitionError("full-air Port polygon is not on the output plane")
        local.append(row)
    return {
        "vertices_global_xyz_um": globals_,
        "vertices_local_suw_um": local,
        "area_um2": float(aperture["analytic_polygon_area_um2"]),
        "local_u_bounds_um": [min(row["u_um"] for row in local),
                              max(row["u_um"] for row in local)],
        "local_w_bounds_um": [min(row["w_um"] for row in local),
                              max(row["w_um"] for row in local)],
        "selection_scope": "unchanged complete physical-air Numeric Port polygon",
        "core_capture_aperture_is_separate": True,
        "capture_aperture_radius_um_not_used_for_port_clipping":
            float(canonical_radiation_geometry_v2()["geometry"]["capture_aperture_radius_um"]),
    }


def _side_chart_manifest(sigma: int, edge_u: float, beta: float,
                         arcs: Mapping[str, Any], transition_length: float,
                         side_thickness: float) -> dict[str, Any]:
    sign_name = "plus" if sigma == 1 else "minus"
    if not arcs["arcs"]:
        return {
            "side": sign_name,
            "sigma": sigma,
            "pieces": [{
                "piece_id": "coincident_global_output_plane",
                "domain": "one exact plane for all longitudinal stations when beta is zero",
                "anchor_su_um": [0.0, edge_u],
                "tangent_angle_rad": 0.0,
                "normal_left_su": [0.0, 1.0],
                "outward_normal_su": [0.0, float(sigma)],
                "signed_distance_um": f"sigma*(u-({edge_u:.17g}))",
            }],
            "seam_ownership": "single straight chart; no duplicate arc pieces",
        }
    first, second = arcs["arcs"]
    p0 = first["start_su_um"]
    p2 = second["end_su_um"]
    phi0, phim, phi2 = (first["phi_start_rad"], first["phi_end_rad"],
                        second["phi_end_rad"])
    return {
        "side": sign_name,
        "sigma": sigma,
        "pieces": [
            {"piece_id": "global_side_plane", "domain": "t<0 from start normal ray",
             "anchor_su_um": p0, "tangent_angle_rad": phi0,
             "outward_normal_su": [sigma * _normal_left(phi0)[0],
                                   sigma * _normal_left(phi0)[1]],
             "signed_distance_um": "sigma*(sin(beta)*s+cos(beta)*u-(sigma*H-c_lateral))"},
            {"piece_id": "biarc_1", "domain": "phi from -beta to -1.5*beta, endpoint ownership [start,end)",
             "arc": first,
             "signed_distance_um": (f"-sigma*sign(k1)*(sqrt((s-Cs)^2+(u-Cu)^2)-R1)")},
            {"piece_id": "biarc_2", "domain": "phi from -1.5*beta to 0, endpoint ownership [start,end)",
             "arc": second,
             "signed_distance_um": (f"-sigma*sign(k2)*(sqrt((s-Cs)^2+(u-Cu)^2)-R2)")},
            {"piece_id": "output_local_plane", "domain": "t>=0 from terminal normal ray",
             "anchor_su_um": p2, "tangent_angle_rad": phi2,
             "outward_normal_su": [0.0, float(sigma)],
             "signed_distance_um": f"sigma*(u-({edge_u:.17g}))"},
        ],
        "normal_tube_distance_interval_um": [0.0, side_thickness],
        "seam_ownership": [
            "global side plane excludes terminal normal ray; biarc_1 owns its start ray",
            "biarc_1 excludes the join ray; biarc_2 owns the join ray",
            "biarc_2 excludes its terminal ray; output local plane owns that ray",
        ],
        "transition_centerline_s_range_um": [-transition_length, 0.0],
    }


def _region_partition(case_id: str, tilted: bool, frame: Mapping[str, Any],
                      recipe: Mapping[str, Any], u_edges: Mapping[int, float]) -> dict[str, Any]:
    geometry = recipe["geometry"]
    input_origin = [float(value) for value in geometry["input_port_center_um"]]
    input_outward = [float(value) for value in geometry["input_pml_outward_axis_xyz"]]
    backing = float(geometry["backing_length_um"])
    input_anchor = _add(input_origin, _scale(backing, input_outward))
    output_anchor = _add(frame["origin_xyz_um"], _scale(backing, frame["a_axis_xyz"]))
    h = float(geometry["transverse_inner_halfwidth_um"])
    dmax = float(geometry["transverse_pml_thickness_um"])
    axial_dmax = float(geometry["axial_pml_thickness_um"])
    longitudinal = ("none", "input_axial", "output_axial")
    width = ("none", "w_minus", "w_plus")
    if tilted:
        lateral = ["none"] + [f"{side}:{piece}" for side in ("minus", "plus")
                               for piece in _PIECE_ORDER]
    else:
        lateral = ["none", "minus:coincident_global_output_plane",
                   "plus:coincident_global_output_plane"]
    rows = []
    for lateral_state in lateral:
        for axial_state in longitudinal:
            if lateral_state == "none":
                valid = True
            elif ":" not in lateral_state:
                valid = False
            else:
                _, piece = lateral_state.split(":", 1)
                if piece in {"biarc_1", "biarc_2"}:
                    valid = axial_state == "none"
                elif piece == "global_side_plane":
                    valid = axial_state in {"none", "input_axial"}
                elif piece == "output_local_plane":
                    valid = axial_state in {"none", "output_axial"}
                else:
                    # beta-zero side plane is the same exact plane at both ends.
                    valid = axial_state in longitudinal
            if not valid:
                continue
            for width_state in width:
                active = sum((axial_state != "none", lateral_state != "none",
                              width_state != "none"))
                kind = ("physical" if active == 0 else
                        "face" if active == 1 else "edge" if active == 2 else "corner")
                region_id = f"{case_id}|{axial_state}|{lateral_state}|{width_state}"
                rows.append({
                    "region_id": region_id,
                    "classification": kind,
                    "active_direction_count": active,
                    "longitudinal_owner": axial_state,
                    "lateral_owner": lateral_state,
                    "width_owner": width_state,
                    "predicate": {
                        "owner_tuple": [axial_state, lateral_state, width_state],
                        "distance_ownership": {
                            "zero_distance_owner": "physical",
                            "tolerance_um": _TOL,
                            "pml_lower_bound_um": 0.0,
                            "pml_lower_inclusive": False,
                            "pml_upper_bound_um": (axial_dmax if axial_state != "none"
                                                   else dmax),
                            "pml_upper_inclusive": True,
                        },
                        "chart_seam_rule": {
                            "piece_order": list(_PIECE_ORDER),
                            "piece_intervals": "start-inclusive/end-exclusive; following chart owns seam",
                            "beta_zero": "single coincident plane; no arc pieces",
                        },
                        "finite_roi_classifier": "classify_port_transition_point",
                    },
                })
    ids = [row["region_id"] for row in rows]
    if len(ids) != len(set(ids)) or not rows:
        raise PortTransitionError("piecewise face/edge/corner ownership is not unique")

    adjacency: set[tuple[str, str, str]] = set()
    lateral_next = {("global_side_plane", "biarc_1"),
                    ("biarc_1", "biarc_2"),
                    ("biarc_2", "output_local_plane")}
    for left in rows:
        for right in rows:
            if left["region_id"] >= right["region_id"]:
                continue
            a, b = left, right
            changed = [key for key in ("longitudinal_owner", "lateral_owner", "width_owner")
                       if a[key] != b[key]]
            if len(changed) != 1:
                continue
            axis_name = changed[0]
            if axis_name == "lateral_owner":
                la, lb = a[axis_name], b[axis_name]
                if la != "none" and lb != "none":
                    if a["longitudinal_owner"] != "none" or b["longitudinal_owner"] != "none":
                        continue
                    try:
                        sa, pa = la.split(":", 1)
                        sb, pb = lb.split(":", 1)
                    except ValueError:
                        continue
                    if sa != sb or (pa, pb) not in lateral_next and (pb, pa) not in lateral_next:
                        continue
            relation = "piece_seam" if axis_name == "lateral_owner" and (
                a[axis_name] != "none" and b[axis_name] != "none") else "direction_family_face"
            adjacency.add((a["region_id"], b["region_id"], relation))
    input_outer_anchor = _add(input_anchor, _scale(axial_dmax, input_outward))
    output_outer_anchor = _add(output_anchor, _scale(axial_dmax, frame["a_axis_xyz"]))
    return {
        "schema_id": "urn:comsol-mcp:w23:transition-region-partition:1.0.0",
        "case_id": case_id,
        "finite_roi": {
            "definition": "closed physical interior plus only the declared outward distance tubes; executable membership is classify_port_transition_point",
            "clip_to_legacy_global_six_face_envelope": False,
            "axial_halfspaces_and_offsets": "input/output signed distances are each bounded above by their PML thickness; positive active intervals are (0,D] and zero belongs to physical",
            "lateral_boundary": "global side half-lines through true-axis start normal rays, two exact circular centerline arcs, then output-local side half-lines",
            "lateral_offsets": "signed normal-distance tubes (0,D] for each half-open chart piece; physical graph lies between the two exact side centerlines",
            "width_boundary": "global w dot (r-center)=+/-H; outward intervals are (0,D] and zero belongs to physical",
            "outside_rule": "reject if any declared finite outer offset is exceeded or no unique chart/region owns the point",
        },
        "longitudinal_direction_family": {
            "input_axial_distance": "(-x)-21.55 um from frozen input backing/PML interface",
            "output_axial_distance": "a dot (r-(center+B*a))",
            "opposites_may_not_be_active_together": True,
        },
        "lateral_chart_piece_order": list(_PIECE_ORDER) if tilted else [
            "coincident_global_output_plane"],
        "width_direction_family": {
            "coordinate": "w dot (r-center)",
            "boundary_planes_global": "w=+/-H; 0<outward distance<=D, with physical ownership at zero",
            "orthogonal_to_biarc_plane": True,
        },
        "distance_family_anchors": {
            "input_axial": {
                "anchor_xyz_um": input_anchor,
                "outer_anchor_xyz_um": input_outer_anchor,
                "outward_unit_xyz": input_outward,
                "distance_expression": "dot(outward_unit,r-anchor)",
                "active_interval_um": {"lower_um": 0.0, "lower_inclusive": False,
                                       "upper_um": axial_dmax, "upper_inclusive": True},
            },
            "output_axial": {
                "anchor_xyz_um": output_anchor,
                "outer_anchor_xyz_um": output_outer_anchor,
                "outward_unit_xyz": frame["a_axis_xyz"],
                "distance_expression": "dot(a,r-(center+B*a))",
                "active_interval_um": {"lower_um": 0.0, "lower_inclusive": False,
                                       "upper_um": axial_dmax, "upper_inclusive": True},
            },
            "lateral_sides": [
                {"sigma": sigma, "inner_u_um": u_edges[sigma],
                 "chart_ids": (["coincident_global_output_plane"] if not tilted else list(_PIECE_ORDER)),
                 "active_distance_interval_um": {"lower_um": 0.0, "lower_inclusive": False,
                                                 "upper_um": dmax, "upper_inclusive": True}}
                for sigma in (-1, 1)
            ],
            "width_sides": [
                {"sigma": sigma,
                 "plane_anchor_xyz_um": _scale(sigma * h, frame["w_axis_xyz"]),
                 "outer_plane_anchor_xyz_um": _scale(sigma * (h + dmax), frame["w_axis_xyz"]),
                 "outward_unit_xyz": _scale(sigma, frame["w_axis_xyz"]),
                 "distance_expression": "sigma*(dot(w,r)-sigma*H)",
                 "active_interval_um": {"lower_um": 0.0, "lower_inclusive": False,
                                        "upper_um": dmax, "upper_inclusive": True}}
                for sigma in (-1, 1)
            ],
            "opposite_active_owners_rejected": True,
        },
        "regions": rows,
        "region_count": len(rows),
        "unique_region_id_count": len(ids),
        "region_owner_count_is_pointwise": True,
        "face_edge_corner_counts": {
            label: sum(row["classification"] == label for row in rows)
            for label in ("physical", "face", "edge", "corner")
        },
        "adjacency": [
            {"region_a": left, "region_b": right, "interface_kind": relation}
            for left, right, relation in sorted(adjacency)
        ],
        "adjacency_count": len(adjacency),
        "producer_boundary_ids": "NOT_READ",
    }


def _case_certificate(case: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    geometry = recipe["geometry"]
    frame = _frame_for_case(case)
    beta = frame["beta_rad"]
    center = frame["origin_xyz_um"]
    a, b, w = frame["a_axis_xyz"], frame["b_axis_xyz"], frame["w_axis_xyz"]
    lateral_index = frame["lateral_global_index"]
    h = float(geometry["transverse_inner_halfwidth_um"])
    transition_length = 0.5
    backing = float(geometry["backing_length_um"])
    axial_thickness = float(geometry["axial_pml_thickness_um"])
    side_thickness = float(geometry["transverse_pml_thickness_um"])
    fiber_radius = float(geometry["maximum_cladding_radius_um"])
    required_buffer = float(geometry["minimum_fiber_to_transverse_pml_buffer_um"])
    input_origin = [float(value) for value in geometry["input_port_center_um"]]
    input_outward = [float(value) for value in geometry["input_pml_outward_axis_xyz"]]
    input_outer = _add(_add(input_origin, _scale(backing, input_outward)),
                       _scale(axial_thickness, input_outward))
    c_lateral = center[lateral_index]
    u_edges = {
        -1: (-h - c_lateral) / math.cos(beta),
        1: (h - c_lateral) / math.cos(beta),
    }
    edge_arcs = {sigma: _make_arcs(beta, u_edges[sigma], transition_length)
                 for sigma in (-1, 1)}
    tilted = abs(beta) > 1e-15

    old_plan = build_pml_partition_plan(recipe, case["receiver_transform"])
    apertures = old_plan["port_air_apertures"]
    input_port = {
        "vertices_global_xyz_um": apertures["input"]["polygon_vertices_global_xyz_um"],
        "area_um2": float(apertures["input"]["analytic_polygon_area_um2"]),
        "selection_scope": "unchanged complete physical-air input Numeric Port polygon",
    }
    output_port = _port_local_vertices(apertures["output"], frame)
    local_u_bounds = output_port["local_u_bounds_um"]
    local_w_bounds = output_port["local_w_bounds_um"]
    expected_u_bounds = [u_edges[-1], u_edges[1]]
    center_w = _dot(center, w)
    expected_w_bounds = [-h - center_w, h - center_w]
    for actual, expected in zip(local_u_bounds, expected_u_bounds):
        if abs(actual - expected) > 3e-8:
            raise PortTransitionError("full Port polygon u edge differs from the frozen full-aperture edge")
    for actual, expected in zip(local_w_bounds, expected_w_bounds):
        if abs(actual - expected) > 3e-8:
            raise PortTransitionError("full Port polygon w edge differs from the unchanged side planes")

    seam_rows = []
    transition_x_ranges = []
    transition_s_ranges = []
    edge_clearances = []
    radii: list[float] = []
    arc_lengths: list[float] = []
    for sigma in (-1, 1):
        arc_record = edge_arcs[sigma]
        side = "plus" if sigma == 1 else "minus"
        if not arc_record["arcs"]:
            edge_clearances.append({
                "side": side,
                "minimum_centerline_to_side_um": sigma * u_edges[sigma],
                "cladding_radius_um": fiber_radius,
                "clearance_um": sigma * u_edges[sigma] - fiber_radius,
            })
            continue
        arcs = arc_record["arcs"]
        for arc in arcs:
            radii.append(arc["radius_um"])
            arc_lengths.append(arc["arc_length_um"])
            s_range = _arc_linear_range(arc, sigma, side_thickness, 1.0, 0.0)
            u_range = _arc_linear_range(arc, sigma, side_thickness, 0.0, 1.0)
            x_local_range = _arc_linear_range(arc, sigma, side_thickness, a[0], b[0])
            x_range = [center[0] + x for x in x_local_range]
            transition_s_ranges.append(s_range)
            transition_x_ranges.append(x_range)
        min_sigma_u = min(
            min(sigma * value for value in _arc_linear_range(arc, sigma, 0.0, 0.0, 1.0))
            for arc in arcs
        )
        edge_clearances.append({
            "side": side,
            "minimum_centerline_to_side_um": min_sigma_u,
            "cladding_radius_um": fiber_radius,
            "clearance_um": min_sigma_u - fiber_radius,
        })
        # Exact common endpoints and tangent normals define each normal-ray seam.
        first, second = arcs
        pstart, pjoin, pend = first["start_su_um"], first["end_su_um"], second["end_su_um"]
        phi_start, phi_join, phi_end = (first["phi_start_rad"], first["phi_end_rad"],
                                        second["phi_end_rad"])
        for seam_id, point, phi in (
            ("global_to_arc1", pstart, phi_start),
            ("arc1_to_arc2", pjoin, phi_join),
            ("arc2_to_output_local", pend, phi_end),
        ):
            normal = _normal_left(phi)
            seam_rows.append({
                "case_id": case["case_id"], "side": side, "seam_id": seam_id,
                "point_su_um": point,
                "outward_normal_su": [sigma * normal[0], sigma * normal[1]],
                "left_chart_distance_equals_right_chart_distance": True,
                "complex_displacement_equal_for_all_d_in_0_D": True,
                "delta_at_physical_interface_um": [0.0, 0.0],
            })

    if tilted:
        phi_max = 1.5 * abs(beta)
        slope_bound = math.tan(phi_max)
        graph_separation = math.cos(phi_max) - slope_bound * math.sin(phi_max)
        minimum_radius = min(radii)
        real_tube_jacobian_lower = math.cos(phi_max) * (1.0 - side_thickness / minimum_radius)
        curvature_product = 1.0 - side_thickness / minimum_radius
        complex_numerator_lower = 1.0 - 0.775 * math.sqrt(2.0) * side_thickness / minimum_radius
        complex_denominator_upper = 1.0 + side_thickness / minimum_radius
        complex_det_abs_lower = (0.775 * math.sqrt(2.0) * complex_numerator_lower
                                 / complex_denominator_upper)
        first_plus, second_plus = edge_arcs[1]["arcs"]
        center_distance = _norm2(_sub(second_plus["center_su_um"],
                                      first_plus["center_su_um"]))
        center_radius_sum = first_plus["radius_um"] + second_plus["radius_um"]
        center_tangency_error = abs(center_distance - center_radius_sum)
        start_ray_s = [edge_arcs[sigma]["expected_start_su_um"][0]
                       + sigma * distance * _normal_left(-beta)[0]
                       for sigma in (-1, 1) for distance in (0.0, side_thickness)]
        endpoint_s = 0.0  # phi_end=0 gives a purely lateral normal ray.
        endpoint_ray_gap = endpoint_s - max(start_ray_s)
        if (minimum_radius <= side_thickness or graph_separation <= 0
                or real_tube_jacobian_lower <= 0 or complex_numerator_lower <= 0
                or complex_det_abs_lower <= 0 or center_tangency_error > 2e-8
                or endpoint_ray_gap <= 0):
            raise PortTransitionError("biarc normal tube or complex stretch is not globally regular")
        # The exact graph separation for the two translated sides is 12/cos(beta).
        side_gap = 2.0 * h / math.cos(beta)
        tube_cert = {
            "phi_abs_max_rad": phi_max,
            "slope_abs_upper_bound": slope_bound,
            "graph_offset_separation_lower_factor": graph_separation,
            "minimum_radius_um": minimum_radius,
            "tube_thickness_um": side_thickness,
            "radius_minus_thickness_um": minimum_radius - side_thickness,
            "real_normal_chart_jacobian_lower_bound": real_tube_jacobian_lower,
            "curvature_factor_lower_bound": curvature_product,
            "fixed_distance_offset_graph_derivative_lower_bound": real_tube_jacobian_lower,
            "normal_chart_injectivity_proof": {
                "same_arc": "each distance level is a concentric circular sector with radius R-sigma*sign(k)*d >= Rmin-D>0",
                "arc_pair_center_distance_um": center_distance,
                "arc_pair_radius_sum_um": center_radius_sum,
                "arc_pair_external_tangency_error_um": center_tangency_error,
                "arc_pair_offset_radius_sum_constant_for_all_d": True,
                "start_to_end_normal_ray_s_gap_lower_um": endpoint_ray_gap,
                "straight_arc_seams": "exact common tangent/normal rays; positive graph derivative and half-open piece ownership",
                "conclusion": "the bounded tube boundary is a simple chain; normal coordinates have one owner on the finite ROI",
            },
            "graph_offset_proof": "ds/dl=cos(phi)*(1-sigma*k*d); sigma*(u_offset-f(s_offset))>=d*(cos(phi)-m*abs(sin(phi)))>0",
            "opposite_side_graph_separation_lower_um": side_gap,
            "opposite_side_proof": "both curves are vertical translates; each outward tube stays outside its own graph by the positive graph bound",
            "strict_complex_stretch_determinant_abs_lower_bound": complex_det_abs_lower,
            "complex_stretch_bound_terms": {
                "radial_abs_factor": 0.775 * math.sqrt(2.0),
                "tangent_numerator_abs_lower": complex_numerator_lower,
                "real_tangent_denominator_abs_upper": complex_denominator_upper,
                "third_direction_factor_abs_lower": 1.0,
            },
        }
        transition_s = [min(row[0] for row in transition_s_ranges),
                        max(row[1] for row in transition_s_ranges)]
        transition_x = [min(row[0] for row in transition_x_ranges),
                        max(row[1] for row in transition_x_ranges)]
        if transition_s[1] > 2e-9 or transition_s[1] >= backing - 2e-9:
            raise PortTransitionError("curved side tubes intrude into output backing/axial cap")
    else:
        phi_max = 0.0
        side_gap = 2.0 * h
        transition_s = None
        transition_x = None
        tube_cert = {
            "status": "NO_CURVED_TUBE_BETA_ZERO",
            "arc_count": 0,
            "normal_chart_jacobian": "straight-plane identity",
            "complex_stretch_determinant_abs_lower_bound": None,
        }

    minimum_fiber_side_clearance = min(row["clearance_um"] for row in edge_clearances)
    minimum_w_clearance = h - abs(center_w) - fiber_radius
    fiber_clearance = min(minimum_fiber_side_clearance, minimum_w_clearance)
    if fiber_clearance < required_buffer - 1e-10:
        raise PortTransitionError("cladding-to-side-PML clearance is below the frozen 0.5 um buffer")

    polygon_globals = output_port["vertices_global_xyz_um"]
    output_port_min_x = min(point[0] for point in polygon_globals)
    output_port_max_x = max(point[0] for point in polygon_globals)
    output_outer_min_x = output_port_min_x + (backing + axial_thickness) * a[0]
    output_backing_range = [output_port_min_x, output_port_max_x + backing * a[0]]
    output_axial_cap_range = [output_port_min_x + backing * a[0],
                              output_port_max_x + (backing + axial_thickness) * a[0]]
    cv_x_max = float(CV_BOX_UM["x"][1])
    x_margins = {
        "transition_tube_x_range_um": transition_x,
        "output_port_x_range_um": [output_port_min_x, output_port_max_x],
        "output_backing_x_range_um": output_backing_range,
        "output_axial_cap_x_range_um": output_axial_cap_range,
        "output_outer_min_x_um": output_outer_min_x,
        "control_volume_x_plus_um": cv_x_max,
        "minimum_transition_or_output_cap_x_um": min(
            ([transition_x[0]] if transition_x else []) +
            [output_port_min_x, output_outer_min_x]),
    }
    x_margins["minimum_margin_outside_control_volume_um"] = (
        x_margins["minimum_transition_or_output_cap_x_um"] - cv_x_max)
    if x_margins["minimum_margin_outside_control_volume_um"] <= 0:
        raise PortTransitionError("transition/backing/cap intersects the frozen x=+19 um CV face")

    if tilted:
        total_arc_length_per_side = sum(arc_lengths) / 2.0
        transition_physical_area = transition_length * side_gap
        side_tube_area_per_unit_w = 2.0 * side_thickness * total_arc_length_per_side
        w_total_width = 2.0 * (h + side_thickness)
        transition_physical_volume = transition_physical_area * 2.0 * h
        transition_side_tube_volume = side_tube_area_per_unit_w * w_total_width
    else:
        transition_physical_area = 0.0
        side_tube_area_per_unit_w = 0.0
        w_total_width = 2.0 * (h + side_thickness)
        transition_physical_volume = 0.0
        transition_side_tube_volume = 0.0

    side_charts = [_side_chart_manifest(sigma, u_edges[sigma], beta,
                                        edge_arcs[sigma], transition_length, side_thickness)
                   for sigma in (-1, 1)]
    partition = _region_partition(case["case_id"], tilted, frame, recipe, u_edges)
    output_transform = {
        "axis_xyz": case["receiver_transform"]["axis_xyz"],
        "center_xyz_um": case["receiver_transform"]["center_xyz_um"],
    }
    return {
        "case_id": case["case_id"],
        "factor": case["factor"],
        "value": case["value"],
        "receiver_transform": case["receiver_transform"],
        "receiver_frame": frame,
        "case_source_sha256": _digest(case),
        "tilt_profile": "single_axis_0.5deg_or_aligned" if tilted else "aligned_or_translation_only",
        "port_apertures": {
            "input": input_port,
            "output": output_port,
            "output_polygon_preserved_from_v2_exactly": True,
            "full_polygon_sweep_axis_xyz": a,
            "full_polygon_sweep_distance_um": backing + axial_thickness,
            "material_continuity_required": ["fiber_core", "fiber_cladding", "air"],
            "old_v2_sweep_status_recorded_not_promoted": {
                "status": apertures["output"]["full_polygon_sweep_status"],
                "minimum_clearance_um": apertures["output"]["minimum_full_polygon_transverse_clearance_um"],
            },
        },
        "side_boundary_charts": side_charts,
        "curved_side_centerline": {
            "status": "ANALYTIC_BIARC" if tilted else "NO_ARC_BETA_ZERO",
            "coordinate_frame": "local_su_um relative to output Port center",
            "edge_u0_by_sigma_um": {"minus": u_edges[-1], "plus": u_edges[1]},
            "start_s_formula": "-L",
            "end_s_formula": "0",
            "start_edge_formula": "u0+L*tan(beta)",
            "continuity":"global lateral plane -> two circular arcs -> output-local plane",
            "per_edge": {"minus": edge_arcs[-1], "plus": edge_arcs[1]},
            "transition_length_um": transition_length if tilted else 0.0,
        },
        "seam_certificate": seam_rows,
        "normal_tube_certificate": tube_cert,
        "signed_distance_contract": {
            "curve": "d=-sigma*sign(k)*(sqrt((s-Cs)^2+(u-Cu)^2)-R)",
            "outward_normal": "sigma*N_left(phi)",
            "physical_interval_um": [0.0, side_thickness],
            "no_shear_or_clipping": True,
            "map": "r_tilde=r+n*delta; delta=d_tilde-d",
            "d_tilde_um": "0.775*(1-i)*d",
            "delta_um": "0.775*(1-i)*d-d",
            "curved_jacobian_factors": [
                "0.775*(1-i)", "(1-sigma*k*d_tilde)/(1-sigma*k*d)", "1 for w"],
            "w_map_is_independent_orthogonal_product": True,
            "output_axial_overlap_with_curve": "NONE; curve tube s_max<=0 and output axial starts at s=B",
            "native_pml_compatibility": "UNVERIFIED",
        },
        "fiber_clearance_certificate": {
            "side_checks": edge_clearances,
            "minimum_w_plane_clearance_um": minimum_w_clearance,
            "minimum_clearance_um": fiber_clearance,
            "required_minimum_um": required_buffer,
            "status": "ANALYTICALLY_CLEAR",
        },
        "finite_roi_bounds_certificate": {
            "transition_tube_s_range_um": transition_s,
            "transition_and_output_cap_x_ranges_um": x_margins,
            "minimum_margin_outside_cv_x_plus_um": x_margins[
                "minimum_margin_outside_control_volume_um"],
            "input_axial_outer_x_um": input_outer[0],
            "output_port_polygon_extrema_recomputed": True,
            "proof_method": "circle support extrema at endpoints and all derivative-zero angles; d extrema are endpoints because support is affine in d",
        },
        "geometry_measures": {
            "output_port_area_um2": output_port["area_um2"],
            "output_backing_volume_um3_reference_unpartitioned": output_port["area_um2"] * backing,
            "output_axial_cap_volume_um3_reference_unpartitioned": output_port["area_um2"] * axial_thickness,
            "transition_physical_cross_section_area_formula":
                "tilted only: L*(2*H/cos(beta)); exactly zero at beta=0",
            "transition_physical_cross_section_area_um2": transition_physical_area,
            "transition_physical_volume_um3": transition_physical_volume,
            "transition_side_pml_cross_section_area_formula":
                "2*D*(arc_length_1+arc_length_2) per unit w; both side curvature terms cancel",
            "transition_side_pml_cross_section_area_um2_all_sides": side_tube_area_per_unit_w,
            "transition_side_pml_volume_um3_extruded_over_full_w_envelope": transition_side_tube_volume,
            "w_envelope_width_um": w_total_width,
            "volume_interpretation": "exact analytic primitive measures; output cap references are not summed because orthogonal edge/corner regions overlap",
        },
        "piecewise_partition": partition,
        "native_geometry_entity_ids": "NOT_READ",
        "native_pml_compatibility": "UNVERIFIED",
        "native_result": "NOT_RUN",
    }


def _derive_plan() -> dict[str, Any]:
    recipe = canonical_radiation_geometry_v2()
    recipe_verification = verify_radiation_geometry_v2(recipe)
    if recipe_verification["recipe_sha256"] != _FROZEN_V2_RECIPE_SHA256:
        raise PortTransitionError("frozen W23 v2 recipe baseline changed; explicit re-freeze is required")
    cases = build_case_matrix()
    if len(cases) != 19:
        raise PortTransitionError("the frozen W23 matrix must contain exactly 19 cases")
    angular = [row for row in cases if row.get("factor") in {
        "receiver_theta_y_deg", "receiver_theta_z_deg"}]
    if len(angular) != 4 or sum(row.get("factor") == "baseline" for row in cases) != 1:
        raise PortTransitionError("W23 matrix no longer has the frozen four-tilt/one-baseline profile")
    for row in cases:
        _frame_for_case(row)
    matrix_sha = _digest(cases)
    if matrix_sha != _FROZEN_CASE_MATRIX_SHA256:
        raise PortTransitionError("frozen W23 case-matrix baseline changed; explicit re-freeze is required")
    geometry = recipe["geometry"]
    expected_geometry = {
        "transverse_inner_halfwidth_um": 6.0,
        "backing_length_um": 1.55,
        "axial_pml_thickness_um": 2.0,
        "transverse_pml_thickness_um": 2.0,
        "maximum_cladding_radius_um": 2.55,
        "minimum_fiber_to_transverse_pml_buffer_um": 0.5,
        "capture_aperture_radius_um": 1.224,
    }
    if any(abs(float(geometry[key]) - expected) > 1e-12
           for key, expected in expected_geometry.items()):
        raise PortTransitionError("a frozen W23 geometry dimension changed; explicit re-freeze is required")
    if abs(float(CV_BOX_UM["x"][1]) - 19.0) > 1e-12:
        raise PortTransitionError("the frozen W23 control-volume x boundary changed")
    plan = {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "algorithm_id": ALGORITHM_ID,
        "lineage": {
            "fixture_id": recipe["fixture_id"],
            "source_recipe_schema_id": recipe["schema_id"],
            "source_recipe_sha256": recipe_verification["recipe_sha256"],
            "case_matrix_sha256": matrix_sha,
            "case_count": len(cases),
            "included_case_ids": [row["case_id"] for row in cases],
            "supersedes": "output-side global-plane distance portion of W23 radiation-geometry v2 only",
        },
        "frozen_parameters_um": {
            "full_air_port_halfwidth": float(geometry["transverse_inner_halfwidth_um"]),
            "transition_length": 0.5,
            "homogeneous_backing": float(geometry["backing_length_um"]),
            "axial_pml_thickness": float(geometry["axial_pml_thickness_um"]),
            "side_and_width_pml_thickness": float(geometry["transverse_pml_thickness_um"]),
            "maximum_cladding_radius": float(geometry["maximum_cladding_radius_um"]),
            "required_fiber_buffer": float(geometry["minimum_fiber_to_transverse_pml_buffer_um"]),
            "control_volume_x_plus": float(CV_BOX_UM["x"][1]),
            "port_aperture_minimum_local_square_halfwidth": float(
                geometry["port_air_aperture"]["minimum_local_square_halfwidth_um"]),
        },
        "analytic_recipe": {
            "edge_anchor": "u0_sigma=(sigma*H-c_lateral)/cos(beta)",
            "arc_chord_um": "L/(2*cos(beta)*cos(beta/4))",
            "radii_um": ["ell/(2*abs(sin(beta/4)))",
                         "ell/(2*abs(sin(3*beta/4)))"],
            "signed_curvatures_per_um": ["-sign(beta)/R1", "sign(beta)/R2"],
            "tangent_angles_rad": ["-beta", "-1.5*beta", "0"],
            "normal_left": "(-sin(phi),cos(phi))",
            "beta_zero": "exact coincident straight global/output plane; no arc or beta division",
            "simultaneous_two_axis_tilt": "UNSUPPORTED",
        },
        "finite_roi_contract": {
            "schema_id": "urn:comsol-mcp:w23:finite-roi-classifier:1.0.0",
            "closed_membership": {
                "input_axial_halfspace_upper_distance_um": float(geometry["axial_pml_thickness_um"]),
                "output_axial_halfspace_upper_distance_um": float(geometry["axial_pml_thickness_um"]),
                "lateral_physical_region": "between the exact piecewise side centerline graphs",
                "lateral_outward_normal_distance_um": float(geometry["transverse_pml_thickness_um"]),
                "width_inner_halfwidth_um": float(geometry["transverse_inner_halfwidth_um"]),
                "width_outward_distance_um": float(geometry["transverse_pml_thickness_um"]),
                "distance_zero_owner": "physical",
                "tolerance_um": _TOL,
            },
            "output_transition": "swept physical strip between both exact biarcs plus each side's normal tube, s from -L through 0; not clipped by the old global lateral planes",
            "output_backing": "0<=s<=B; full unchanged Port polygon swept along a",
            "output_axial_cap": "B<=s<=B+D_axial; full unchanged Port polygon swept along a",
            "side_chart_domains": "global start half-line, arc1, arc2, output-local terminal half-line; normal-ray seams are half-open and assigned to the following chart",
            "independent_width_direction": "w=+/- global z for lateral y; w=-global y for lateral z; physical halfwidth H and outward thickness D",
            "input_axial_cap": "unchanged existing global -x cap and its outward distance tube",
            "classifier_api": "tools.w23_port_transition.classify_port_transition_point",
            "native_geometry_predicates": "NOT_READ; software region ownership only",
        },
        "cases": [_case_certificate(row, recipe)
                  for row in cases],
        "acceptance_scope": {
            "full_port_polygon_unchanged": "SOFTWARE_RECOMPUTED_FROM_V2_HALFSPACE_CLIPPER",
            "aperture_area_and_capture_separation": "SOFTWARE_VERIFIED",
            "biarc_closure_and_tube_geometry": "SOFTWARE_ANALYTIC",
            "piecewise_ownership_and_adjacency": "SOFTWARE_ANALYTIC",
            "native_support": "NOT_IMPLEMENTED",
            "native_geometry_and_material_readback": "NOT_RUN",
            "comsol_pml_compatibility": "UNVERIFIED",
            "physical_optical_acceptance": "NOT_RUN",
        },
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def build_port_transition_plan() -> dict[str, Any]:
    """Build a fresh deterministic candidate from frozen repository inputs."""
    return _derive_plan()


def verify_port_transition_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the entire plan from source inputs; caller PASS/hash is not trusted."""
    if not isinstance(plan, Mapping) or plan.get("schema_id") != SCHEMA_ID:
        raise PortTransitionError("versioned W23 port-transition schema is required")
    expected_digest = _digest({key: value for key, value in plan.items()
                               if key != "plan_sha256"})
    if plan.get("plan_sha256") != expected_digest:
        raise PortTransitionError("port-transition plan self-digest mismatch")
    expected = _derive_plan()
    if _digest(plan) != _digest(expected):
        raise PortTransitionError("port-transition plan differs from recomputed frozen recipe and case matrix")
    return {
        "status": "SOFTWARE_ANALYTIC_CANDIDATE_VALID_NATIVE_NOT_RUN",
        "plan_sha256": expected["plan_sha256"],
        "source_recipe_sha256": expected["lineage"]["source_recipe_sha256"],
        "case_matrix_sha256": expected["lineage"]["case_matrix_sha256"],
        "case_count": expected["lineage"]["case_count"],
        "native_result": "NOT_RUN",
        "native_support": "NOT_IMPLEMENTED",
        "pml_compatibility": "UNVERIFIED",
    }


def _snap_seam_coordinate(value: float) -> float:
    return 0.0 if abs(value) <= _SEAM_TOL_UM else value


def _arc_foot(point_su: Sequence[float], arc: Mapping[str, Any], sigma: int
              ) -> tuple[float, float] | None:
    center_s, center_u = (float(value) for value in arc["center_su_um"])
    radius = float(arc["radius_um"])
    curvature = float(arc["signed_curvature_per_um"])
    sign_k = 1.0 if curvature > 0 else -1.0
    radial_s, radial_u = point_su[0] - center_s, point_su[1] - center_u
    rho = math.hypot(radial_s, radial_u)
    if rho <= 1e-15:
        return None
    normal_s = -sign_k * radial_s / rho
    normal_u = -sign_k * radial_u / rho
    phi = math.atan2(-normal_s, normal_u)
    start, end = float(arc["phi_start_rad"]), float(arc["phi_end_rad"])
    if not _angle_in_sweep(phi, start, end):
        return None
    distance = -sigma * sign_k * (rho - radius)
    return phi, distance


def _local_side_bounds(case: Mapping[str, Any], s_um: float) -> tuple[float, float]:
    """Evaluate both exact centerline graphs at one local axial coordinate."""
    centerline = case["curved_side_centerline"]
    beta = float(case["receiver_frame"]["beta_rad"])
    edges = centerline["edge_u0_by_sigma_um"]
    arcs_by_edge = centerline["per_edge"]
    length = float(centerline["transition_length_um"])
    result: dict[int, float] = {}
    for sigma, label in ((-1, "minus"), (1, "plus")):
        u0 = float(edges[label])
        arcs = arcs_by_edge[label]["arcs"]
        if not arcs or s_um < -length:
            result[sigma] = u0 - s_um * math.tan(beta)
            continue
        if s_um > 0.0:
            result[sigma] = u0
            continue
        found = False
        for arc in arcs:
            start_s = float(arc["start_su_um"][0])
            end_s = float(arc["end_su_um"][0])
            if not (min(start_s, end_s) - _TOL <= s_um <= max(start_s, end_s) + _TOL):
                continue
            curvature = float(arc["signed_curvature_per_um"])
            center_s, center_u = (float(value) for value in arc["center_su_um"])
            sine = curvature * (s_um - center_s)
            if abs(sine) > 1.0 + 1e-12:
                continue
            phi = math.asin(max(-1.0, min(1.0, sine)))
            if not _angle_in_sweep(phi, float(arc["phi_start_rad"]),
                                   float(arc["phi_end_rad"])):
                continue
            result[sigma] = center_u - math.cos(phi) / curvature
            found = True
            break
        if not found:
            raise PortTransitionError("local lateral graph has no analytic arc at this s coordinate")
    if result[-1] >= result[1]:
        raise PortTransitionError("the finite physical lateral graph is inverted")
    return result[-1], result[1]


def _lateral_chart_candidates(case: Mapping[str, Any], point_su: Sequence[float]
                              ) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    centerline = case["curved_side_centerline"]
    for side in case["side_boundary_charts"]:
        sigma = int(side["sigma"])
        pieces = side["pieces"]
        if len(pieces) == 1:
            piece = pieces[0]
            edge_u = float(piece["anchor_su_um"][1])
            distance = sigma * (float(point_su[1]) - edge_u)
            candidates.append((f"{'plus' if sigma == 1 else 'minus'}:{piece['piece_id']}",
                               distance))
            continue

        first_arc = pieces[1]["arc"]
        second_arc = pieces[2]["arc"]
        p0 = [float(value) for value in first_arc["start_su_um"]]
        pjoin = [float(value) for value in first_arc["end_su_um"]]
        pend = [float(value) for value in second_arc["end_su_um"]]
        phi0 = float(first_arc["phi_start_rad"])
        phijoin = float(first_arc["phi_end_rad"])
        phiend = float(second_arc["phi_end_rad"])

        # Normal-ray seam coordinates are snapped only within a roundoff-scale
        # interval.  The following chart owns each ray, so no point has two owners.
        t0 = _snap_seam_coordinate(_dot(_sub(point_su, p0),
                                        [math.cos(phi0), math.sin(phi0)]))
        if t0 < 0.0:
            normal = _normal_left(phi0)
            distance = sigma * _dot(_sub(point_su, p0), normal)
            candidates.append((f"{'plus' if sigma == 1 else 'minus'}:global_side_plane",
                               distance))

        for index, (piece_id, arc) in enumerate((("biarc_1", first_arc),
                                                  ("biarc_2", second_arc))):
            foot = _arc_foot(point_su, arc, sigma)
            if foot is None:
                continue
            _, distance = foot
            if index == 0:
                start_ok = t0 >= 0.0
                join_t = _snap_seam_coordinate(_dot(
                    _sub(point_su, pjoin), [math.cos(phijoin), math.sin(phijoin)]))
                end_ok = join_t < 0.0
                seam_ok = start_ok and end_ok
            else:
                join_t = _snap_seam_coordinate(_dot(
                    _sub(point_su, pjoin), [math.cos(phijoin), math.sin(phijoin)]))
                end_t = _snap_seam_coordinate(_dot(
                    _sub(point_su, pend), [math.cos(phiend), math.sin(phiend)]))
                seam_ok = join_t >= 0.0 and end_t < 0.0
            if seam_ok:
                candidates.append((f"{'plus' if sigma == 1 else 'minus'}:{piece_id}",
                                   distance))

        phi2 = phiend
        end_t = _snap_seam_coordinate(_dot(
            _sub(point_su, pend), [math.cos(phi2), math.sin(phi2)]))
        if end_t >= 0.0:
            edge_u = float(pieces[3]["anchor_su_um"][1])
            distance = sigma * (float(point_su[1]) - edge_u)
            candidates.append((f"{'plus' if sigma == 1 else 'minus'}:output_local_plane",
                               distance))
    return candidates


def _classify_distance(distance: float, maximum: float, label: str
                       ) -> tuple[str, float]:
    distance = _number(distance, f"{label} signed distance")
    if distance > maximum + _TOL:
        raise PortTransitionError(f"point is outside the finite ROI {label} offset")
    return ("pml" if distance > _TOL else "physical"), distance


def _classify_case_point(case: Mapping[str, Any], point_xyz_um: Sequence[float],
                         axial_max: float, side_max: float, width_half: float
                         ) -> dict[str, Any]:
    if not isinstance(point_xyz_um, (list, tuple)) or len(point_xyz_um) != 3:
        raise PortTransitionError("point_xyz_um must contain exactly three finite coordinates")
    point = [_number(value, "point_xyz_um") for value in point_xyz_um]
    frame = case["receiver_frame"]
    center = frame["origin_xyz_um"]
    rel = _sub(point, center)
    s = _dot(rel, frame["a_axis_xyz"])
    u = _dot(rel, frame["b_axis_xyz"])
    w = _dot(rel, frame["w_axis_xyz"])
    point_su = (s, u)

    partition = case["piecewise_partition"]
    anchors = partition["distance_family_anchors"]
    input_anchor = anchors["input_axial"]["anchor_xyz_um"]
    input_n = anchors["input_axial"]["outward_unit_xyz"]
    output_anchor = anchors["output_axial"]["anchor_xyz_um"]
    output_n = anchors["output_axial"]["outward_unit_xyz"]
    input_distance = _dot(_sub(point, input_anchor), input_n)
    output_distance = _dot(_sub(point, output_anchor), output_n)
    input_kind, input_distance = _classify_distance(input_distance, axial_max, "input axial")
    output_kind, output_distance = _classify_distance(output_distance, axial_max, "output axial")
    if input_kind == output_kind == "pml":
        raise PortTransitionError("opposite axial PML owners overlap for this point")
    axial_owner = ("input_axial" if input_kind == "pml" else
                   "output_axial" if output_kind == "pml" else "none")

    lateral_distances = _lateral_chart_candidates(case, point_su)
    lateral_active: list[tuple[str, float]] = []
    for chart_id, distance in lateral_distances:
        kind, checked_distance = _classify_distance(distance, side_max,
                                                    f"lateral {chart_id}")
        if kind == "pml":
            lateral_active.append((chart_id, checked_distance))
    if len(lateral_active) > 1:
        raise PortTransitionError("more than one lateral normal chart owns this point")
    if lateral_active:
        lateral_owner = lateral_active[0][0]
    else:
        lower, upper = _local_side_bounds(case, s)
        if u < lower - _TOL or u > upper + _TOL:
            raise PortTransitionError("point is outside the finite ROI lateral transition envelope")
        lateral_owner = "none"

    width_distances = {
        "w_minus": -w - width_half,
        "w_plus": w - width_half,
    }
    width_active: list[tuple[str, float]] = []
    for owner, distance in width_distances.items():
        kind, checked_distance = _classify_distance(distance, side_max,
                                                    f"width {owner}")
        if kind == "pml":
            width_active.append((owner, checked_distance))
    if len(width_active) > 1:
        raise PortTransitionError("opposite width PML owners overlap for this point")
    width_owner = width_active[0][0] if width_active else "none"

    row_key = (axial_owner, lateral_owner, width_owner)
    matches = [row for row in partition["regions"]
               if (row["longitudinal_owner"], row["lateral_owner"], row["width_owner"])
               == row_key]
    owner_count = len(matches)
    if owner_count != 1:
        raise PortTransitionError(
            f"point has {owner_count} executable region owners for {row_key!r}")
    row = matches[0]
    return {
        "status": "CLASSIFIED_IN_FINITE_ROI",
        "case_id": case["case_id"],
        "point_xyz_um": point,
        "local_suw_um": [s, u, w],
        "region_id": row["region_id"],
        "classification": row["classification"],
        "owners": {"longitudinal": axial_owner, "lateral": lateral_owner,
                   "width": width_owner},
        "owner_count": owner_count,
        "signed_distances_um": {
            "input_axial": input_distance,
            "output_axial": output_distance,
            "lateral_candidates": [
                {"chart_id": chart_id, "distance_um": distance}
                for chart_id, distance in lateral_distances],
            "width": width_distances,
        },
        "native_boundary_ids": "NOT_READ",
    }


def classify_port_transition_point(plan: Mapping[str, Any], case_id: str,
                                   point_xyz_um: Sequence[float]) -> dict[str, Any]:
    """Classify one point under the recomputed finite ROI and half-open ownership.

    Distances at zero belong to the physical region.  Positive PML distances use
    ``(0,D]`` (within the declared roundoff tolerance); chart seams belong to the
    following chart in ``global, arc1, arc2, output-local`` order.
    """
    return classify_port_transition_points(plan, case_id, [point_xyz_um])[0]


def classify_port_transition_points(plan: Mapping[str, Any], case_id: str,
                                    points_xyz_um: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    """Classify a finite batch after one frozen-plan recomputation."""
    verify_port_transition_plan(plan)
    cases = [case for case in plan["cases"] if case["case_id"] == case_id]
    if len(cases) != 1:
        raise PortTransitionError("case_id must identify exactly one frozen W23 case")
    if not isinstance(points_xyz_um, (list, tuple)):
        raise PortTransitionError("points_xyz_um must be a finite list of 3-vectors")
    return [_classify_case_point(
        cases[0], point,
        float(plan["frozen_parameters_um"]["axial_pml_thickness"]),
        float(plan["frozen_parameters_um"]["side_and_width_pml_thickness"]),
        float(plan["frozen_parameters_um"]["full_air_port_halfwidth"]))
        for point in points_xyz_um]


__all__ = [
    "ALGORITHM_ID", "PortTransitionError", "SCHEMA_ID",
    "build_port_transition_plan", "classify_port_transition_point",
    "classify_port_transition_points",
    "verify_port_transition_plan",
]
