"""Managed adapter payload for the W23 true-distance output-port transition.

This module converts the already frozen analytic transition plan into exact
2-D line/circular-arc cells and 3-D extrusion/PML assignments.  It does not
call COMSOL; the Java adapter performs the native geometry/readback stage.  A
successful offline recipe check is not evidence that COMSOL accepted the BRep
or PML coordinate systems.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from tools.w23_port_transition import (
    PortTransitionError,
    build_port_transition_plan,
    verify_port_transition_plan,
)


SCHEMA_ID = "urn:comsol-mcp:w23:port-transition-native-recipe:1.0.0"
JAVA_SOURCE = "NativeW23PortTransitionV1.java"
JAVA_ENTRYPOINT = "NativeW23PortTransitionV1#run"
COMPONENT_TAG = "comp3d"
GEOMETRY_TAG = "geom3d"
FIXTURE_ID = "w23_full3d_fiber_ball_lens_vector_pml_v1"
_PROFILE_TAG_RE = re.compile(r"^[a-z][a-z0-9_]{0,55}$")
_CLOSE_TOL_UM = 2.0e-8
_AREA_TOL_UM2 = 2.0e-8


class PortTransitionAdapterError(ValueError):
    """The frozen transition plan cannot be lowered to native cell primitives."""


def _sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortTransitionAdapterError(f"{label} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise PortTransitionAdapterError(f"{label} must be finite numeric data")
    return result


def _point(value: Sequence[Any], label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PortTransitionAdapterError(f"{label} must be a two-coordinate point")
    return [_finite(value[0], label), _finite(value[1], label)]


def _line(start: Sequence[float], end: Sequence[float]) -> dict[str, Any]:
    p0, p1 = _point(start, "line start"), _point(end, "line end")
    if math.dist(p0, p1) <= 1.0e-13:
        raise PortTransitionAdapterError("zero-length line is not a valid profile primitive")
    return {"kind": "line", "start_su_um": p0, "end_su_um": p1}


def _arc(center: Sequence[float], radius: float, angle_start: float,
         angle_end: float) -> dict[str, Any]:
    c = _point(center, "arc center")
    r = _finite(radius, "arc radius")
    a0, a1 = _finite(angle_start, "arc start angle"), _finite(angle_end, "arc end angle")
    if r <= 0 or abs(a1 - a0) <= 1.0e-13 or abs(a1 - a0) >= math.pi:
        raise PortTransitionAdapterError("arc radius/sweep is outside the frozen short-arc profile")
    p0 = [c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)]
    p1 = [c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)]
    return {
        "kind": "circular_arc", "center_su_um": c, "radius_um": r,
        "angle_start_rad": a0, "angle_end_rad": a1,
        "angle_start_deg": math.degrees(a0), "angle_end_deg": math.degrees(a1),
        "sweep_sign": 1 if a1 > a0 else -1,
        "start_su_um": p0, "end_su_um": p1,
    }


def _reverse(primitive: Mapping[str, Any]) -> dict[str, Any]:
    if primitive["kind"] == "line":
        return _line(primitive["end_su_um"], primitive["start_su_um"])
    if primitive["kind"] == "circular_arc":
        return _arc(primitive["center_su_um"], primitive["radius_um"],
                    primitive["angle_end_rad"], primitive["angle_start_rad"])
    raise PortTransitionAdapterError("unsupported profile primitive")


def _area_and_closure(primitives: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if len(primitives) < 3:
        raise PortTransitionAdapterError("closed profile needs at least three exact primitives")
    gap = 0.0
    signed_area_twice = 0.0
    for index, primitive in enumerate(primitives):
        start = primitive["start_su_um"]
        end = primitive["end_su_um"]
        next_start = primitives[(index + 1) % len(primitives)]["start_su_um"]
        gap = max(gap, math.dist(end, next_start))
        if primitive["kind"] == "line":
            signed_area_twice += start[0] * end[1] - start[1] * end[0]
        elif primitive["kind"] == "circular_arc":
            cx, cy = primitive["center_su_um"]
            r = primitive["radius_um"]
            a0, a1 = primitive["angle_start_rad"], primitive["angle_end_rad"]
            signed_area_twice += r * (
                cx * (math.sin(a1) - math.sin(a0))
                - cy * (math.cos(a1) - math.cos(a0))
            ) + r * r * (a1 - a0)
        else:
            raise PortTransitionAdapterError("unknown exact profile primitive")
    area = abs(0.5 * signed_area_twice)
    if gap > _CLOSE_TOL_UM or area <= _AREA_TOL_UM2:
        raise PortTransitionAdapterError(
            f"profile is open or degenerate (closure={gap:.3g} um, area={area:.3g} um^2)")
    return area, gap


def _profile(profile_id: str, role: str,
             primitives: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not _PROFILE_TAG_RE.fullmatch(profile_id):
        raise PortTransitionAdapterError("profile identifier is not a native-safe tag")
    segments = [copy.deepcopy(dict(row)) for row in primitives]
    area, closure = _area_and_closure(segments)
    return {
        "profile_id": profile_id,
        "role": role,
        "coordinate_order": "(s,u), micrometres",
        "boundary_primitives": segments,
        "exact_enclosed_area_um2": area,
        "closure_max_error_um": closure,
        "topology_evidence": "analytic directed primitive loop; native geometry remains NOT_RUN",
    }


def _intersection(line_a: tuple[float, float, float],
                  line_b: tuple[float, float, float]) -> list[float]:
    a1, b1, c1 = line_a
    a2, b2, c2 = line_b
    determinant = a1 * b2 - a2 * b1
    if abs(determinant) <= 1e-14:
        raise PortTransitionAdapterError("profile boundary lines are parallel or coincident")
    return [(c1 * b2 - c2 * b1) / determinant,
            (a1 * c2 - a2 * c1) / determinant]


def _frame_data(case: Mapping[str, Any]) -> dict[str, Any]:
    frame = case["receiver_frame"]
    return {
        "origin_xyz_um": [_finite(v, "frame origin") for v in frame["origin_xyz_um"]],
        "a_axis_xyz": [_finite(v, "a axis") for v in frame["a_axis_xyz"]],
        "b_axis_xyz": [_finite(v, "b axis") for v in frame["b_axis_xyz"]],
        "w_axis_xyz": [_finite(v, "w axis") for v in frame["w_axis_xyz"]],
        "lateral_axis": frame["lateral_axis"],
        "w_direction": frame["w_direction"],
        "beta_rad": _finite(frame["beta_rad"], "beta"),
    }


def _local_from_global(case: Mapping[str, Any], xyz: Sequence[float]) -> list[float]:
    frame = case["receiver_frame"]
    rel = [float(xyz[i]) - float(frame["origin_xyz_um"][i]) for i in range(3)]
    return [sum(rel[i] * float(frame[key][i]) for i in range(3))
            for key in ("a_axis_xyz", "b_axis_xyz")]


def _geometry_context(plan: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    frame = _frame_data(case)
    params = plan["frozen_parameters_um"]
    partition = case["piecewise_partition"]
    anchors = partition["distance_family_anchors"]
    input_x = float(anchors["input_axial"]["anchor_xyz_um"][0])
    input_outer_x = float(anchors["input_axial"]["outer_anchor_xyz_um"][0])
    h = float(params["full_air_port_halfwidth"])
    d = float(params["side_and_width_pml_thickness"])
    backing = float(params["homogeneous_backing"])
    axial_d = float(params["axial_pml_thickness"])
    beta = frame["beta_rad"]
    lateral_index = 1 if frame["lateral_axis"] == "y" else 2
    center_lateral = frame["origin_xyz_um"][lateral_index]
    input_port_vertices = case["port_apertures"]["input"]["vertices_global_xyz_um"]
    input_port_x_values = [_finite(point[0], "input port x") for point in input_port_vertices]
    if max(input_port_x_values) - min(input_port_x_values) > 1.0e-10:
        raise PortTransitionAdapterError("input Port polygon is not on one frozen global-x plane")
    input_port_x = input_port_x_values[0]
    cx = frame["origin_xyz_um"][0]
    # local x = cx + cos(beta)*s - sin(beta)*u
    input_boundary = (math.cos(beta), -math.sin(beta), input_x - cx)
    input_outer_boundary = (math.cos(beta), -math.sin(beta), input_outer_x - cx)
    # global lateral coordinate = c_l + sin(beta)*s + cos(beta)*u
    side_inner = {
        sigma: (math.sin(beta), math.cos(beta), sigma * h - center_lateral)
        for sigma in (-1, 1)
    }
    side_outer = {
        sigma: (math.sin(beta), math.cos(beta), sigma * (h + d) - center_lateral)
        for sigma in (-1, 1)
    }
    u_edges = {(-1 if side == "minus" else 1): _finite(value, "side edge u")
               for side, value in case["curved_side_centerline"]["edge_u0_by_sigma_um"].items()}
    return {
        "frame": frame, "h": h, "side_d": d, "axial_d": axial_d,
        "backing": backing, "input_x": input_x, "input_outer_x": input_outer_x,
        "input_port_x": input_port_x,
        "input_boundary": input_boundary,
        "input_outer_boundary": input_outer_boundary,
        "side_inner": side_inner, "side_outer": side_outer,
        "center_lateral": center_lateral,
        "u_edges": u_edges,
    }


def _line_hits(c: Mapping[str, Any], xline: tuple[float, float, float],
               side_line: tuple[float, float, float]) -> list[float]:
    return _intersection(xline, side_line)


def _line_side_path(case: Mapping[str, Any], context: Mapping[str, Any], sigma: int,
                    end_s: float) -> list[dict[str, Any]]:
    """Inner physical side boundary from input plane to s=end_s."""
    centerline = case["curved_side_centerline"]
    if centerline["status"] == "NO_ARC_BETA_ZERO":
        u = float(context["u_edges"][sigma])
        start = _line_hits(context, context["input_boundary"], context["side_inner"][sigma])
        return [_line(start, [end_s, u])]

    edge_label = "plus" if sigma == 1 else "minus"
    edge = centerline["per_edge"][edge_label]
    arcs = edge["arcs"]
    p0 = _point(arcs[0]["start_su_um"], "biarc start")
    start = _line_hits(context, context["input_boundary"], context["side_inner"][sigma])
    primitives: list[dict[str, Any]] = [_line(start, p0)]
    for source_arc in arcs:
        center = source_arc["center_su_um"]
        curvature = float(source_arc["signed_curvature_per_um"])
        radius = float(source_arc["radius_um"])
        phi0 = float(source_arc["phi_start_rad"])
        phi1 = float(source_arc["phi_end_rad"])
        theta0 = phi0 - math.copysign(math.pi / 2.0, curvature)
        theta1 = theta0 + (phi1 - phi0)
        primitives.append(_arc(center, radius, theta0, theta1))
    end = [end_s, float(context["u_edges"][sigma])]
    pend = _point(arcs[-1]["end_su_um"], "biarc end")
    if end_s > pend[0] + 1e-12:
        primitives.append(_line(pend, end))
    elif math.dist(pend, end) > _CLOSE_TOL_UM:
        raise PortTransitionAdapterError("requested side path stops before the frozen arc endpoint")
    return primitives


def _side_pml_profile(case: Mapping[str, Any], context: Mapping[str, Any],
                      sigma: int, piece_id: str) -> list[dict[str, Any]]:
    """Exact 2-D PML strip profile clipped to the permitted axial slab."""
    d = float(context["side_d"])
    beta = float(context["frame"]["beta_rad"])
    if piece_id == "global_side_plane":
        start = _point(case["side_boundary_charts"][0 if sigma < 0 else 1]["pieces"][0]["anchor_su_um"],
                       "global side start")
        phi = float(case["side_boundary_charts"][0 if sigma < 0 else 1]["pieces"][0]["tangent_angle_rad"])
        normal = [-math.sin(phi), math.cos(phi)]
        outer_start = [start[i] + sigma * d * normal[i] for i in range(2)]
        inner_in = _line_hits(context, context["input_boundary"], context["side_inner"][sigma])
        outer_in = _line_hits(context, context["input_boundary"], context["side_outer"][sigma])
        return [_line(inner_in, start), _line(start, outer_start),
                _line(outer_start, outer_in), _line(outer_in, inner_in)]

    if piece_id == "coincident_global_output_plane":
        inner_in = _line_hits(context, context["input_boundary"], context["side_inner"][sigma])
        outer_in = _line_hits(context, context["input_boundary"], context["side_outer"][sigma])
        inner_out = [float(context["backing"]), float(context["u_edges"][sigma])]
        outer_out = [float(context["backing"]), inner_out[1] + sigma * d]
        return [_line(inner_in, inner_out), _line(inner_out, outer_out),
                _line(outer_out, outer_in), _line(outer_in, inner_in)]

    if piece_id in {"biarc_1", "biarc_2"}:
        label = "plus" if sigma == 1 else "minus"
        source_arcs = case["curved_side_centerline"]["per_edge"][label]["arcs"]
        index = 0 if piece_id == "biarc_1" else 1
        source_arc = source_arcs[index]
        curvature = float(source_arc["signed_curvature_per_um"])
        sign_k = math.copysign(1.0, curvature)
        outer_radius = float(source_arc["radius_um"]) - sigma * sign_k * d
        if outer_radius <= d:
            raise PortTransitionAdapterError("normal PML offset crosses the circle center")
        phi0 = float(source_arc["phi_start_rad"])
        phi1 = float(source_arc["phi_end_rad"])
        theta0 = phi0 - math.copysign(math.pi / 2.0, curvature)
        theta1 = theta0 + (phi1 - phi0)
        center = _point(source_arc["center_su_um"], "arc center")
        radius = float(source_arc["radius_um"])
        inner = _arc(center, radius, theta0, theta1)
        outer = _arc(center, outer_radius, theta0, theta1)
        return [inner, _line(inner["end_su_um"], outer["end_su_um"]),
                _reverse(outer), _line(outer["start_su_um"], inner["start_su_um"])]

    if piece_id == "output_local_plane":
        u0 = float(context["u_edges"][sigma])
        outer_u = u0 + sigma * d
        lower = [0.0, u0]
        upper = [float(context["backing"]), u0]
        upper_outer = [float(context["backing"]), outer_u]
        lower_outer = [0.0, outer_u]
        return [_line(lower, upper), _line(upper, upper_outer),
                _line(upper_outer, lower_outer), _line(lower_outer, lower)]

    raise PortTransitionAdapterError(f"unsupported side chart piece {piece_id!r}")


def _main_physical_profile(case: Mapping[str, Any], context: Mapping[str, Any],
                           segment: str) -> list[dict[str, Any]]:
    """Physical central area, divided only at exact input/output Port planes."""
    beta = float(context["frame"]["beta_rad"])
    lower_x = context["input_x"]
    input_port_x = float(context["input_port_x"])
    backing = float(context["backing"])
    if segment == "input_backing":
        xline = context["input_boundary"]
        out_line = (math.cos(beta), -math.sin(beta), input_port_x - context["frame"]["origin_xyz_um"][0])
        lower_in = _line_hits(context, xline, context["side_inner"][-1])
        upper_in = _line_hits(context, xline, context["side_inner"][1])
        lower_out = _line_hits(context, out_line, context["side_inner"][-1])
        upper_out = _line_hits(context, out_line, context["side_inner"][1])
        return [_line(lower_in, lower_out), _line(lower_out, upper_out),
                _line(upper_out, upper_in), _line(upper_in, lower_in)]
    if segment == "between_ports":
        in_line = (math.cos(beta), -math.sin(beta), input_port_x - context["frame"]["origin_xyz_um"][0])
        lower_in = _line_hits(context, in_line, context["side_inner"][-1])
        upper_in = _line_hits(context, in_line, context["side_inner"][1])
        lower_path = _line_side_path(case, context, -1, 0.0)
        upper_path = _line_side_path(case, context, 1, 0.0)
        lower_path[0] = _line(lower_in, lower_path[0]["end_su_um"])
        upper_path[0] = _line(upper_in, upper_path[0]["end_su_um"])
        upper_reverse = [_reverse(primitive) for primitive in reversed(upper_path)]
        return lower_path + [_line([0.0, context["u_edges"][-1]],
                                   [0.0, context["u_edges"][1]])] + upper_reverse + [
            _line(upper_in, lower_in)]
    if segment == "backing":
        u_minus, u_plus = context["u_edges"][-1], context["u_edges"][1]
        return [_line([0.0, u_minus], [backing, u_minus]),
                _line([backing, u_minus], [backing, u_plus]),
                _line([backing, u_plus], [0.0, u_plus]),
                _line([0.0, u_plus], [0.0, u_minus])]
    raise PortTransitionAdapterError("unknown physical subcell split")


def _input_cap_profile(context: Mapping[str, Any], *, sigma: int | None) -> list[dict[str, Any]]:
    x0, x1 = context["input_outer_boundary"], context["input_boundary"]
    if sigma is None:
        lower0 = _line_hits(context, x0, context["side_inner"][-1])
        upper0 = _line_hits(context, x0, context["side_inner"][1])
        lower1 = _line_hits(context, x1, context["side_inner"][-1])
        upper1 = _line_hits(context, x1, context["side_inner"][1])
        return [_line(lower0, lower1), _line(lower1, upper1),
                _line(upper1, upper0), _line(upper0, lower0)]
    inner0 = _line_hits(context, x0, context["side_inner"][sigma])
    inner1 = _line_hits(context, x1, context["side_inner"][sigma])
    outer1 = _line_hits(context, x1, context["side_outer"][sigma])
    outer0 = _line_hits(context, x0, context["side_outer"][sigma])
    return [_line(inner0, inner1), _line(inner1, outer1),
            _line(outer1, outer0), _line(outer0, inner0)]


def _output_cap_profile(context: Mapping[str, Any], *, sigma: int | None) -> list[dict[str, Any]]:
    backing, thickness = float(context["backing"]), float(context["axial_d"])
    if sigma is None:
        lower, upper = float(context["u_edges"][-1]), float(context["u_edges"][1])
        return [_line([backing, lower], [backing + thickness, lower]),
                _line([backing + thickness, lower], [backing + thickness, upper]),
                _line([backing + thickness, upper], [backing, upper]),
                _line([backing, upper], [backing, lower])]
    u0 = float(context["u_edges"][sigma])
    outer = u0 + sigma * float(context["side_d"])
    return [_line([backing, u0], [backing + thickness, u0]),
            _line([backing + thickness, u0], [backing + thickness, outer]),
            _line([backing + thickness, outer], [backing, outer]),
            _line([backing, outer], [backing, u0])]


def _pml_direction_expressions(case: Mapping[str, Any], context: Mapping[str, Any],
                               row: Mapping[str, Any]) -> list[dict[str, str]]:
    def dot_expr(axis: Sequence[float], origin: Sequence[float], *, subtract: float = 0.0) -> str:
        terms = []
        for coefficient, variable, center in zip(axis, ("x", "y", "z"), origin):
            if abs(float(coefficient)) < 1e-16:
                continue
            terms.append(f"({float(coefficient):.17g})*({variable}-({float(center):.17g})[um])")
        expression = "+".join(terms) if terms else "0[um]"
        if abs(subtract) > 1e-16:
            expression += f"-({float(subtract):.17g})[um]"
        return f"({expression})"

    directions: list[dict[str, str]] = []
    axial_owner = row["longitudinal_owner"]
    if axial_owner == "input_axial":
        expr = "(-x-(21.55)[um])"
        directions.append({"owner": axial_owner, "distance_expression": expr,
                           "dmax_expression": "2[um]"})
    elif axial_owner == "output_axial":
        origin = case["piecewise_partition"]["distance_family_anchors"]["output_axial"]["anchor_xyz_um"]
        expr = dot_expr(context["frame"]["a_axis_xyz"], origin)
        directions.append({"owner": axial_owner, "distance_expression": expr,
                           "dmax_expression": "2[um]"})

    lateral_owner = str(row["lateral_owner"])
    if lateral_owner != "none":
        side, piece = lateral_owner.split(":", 1)
        sigma = -1 if side == "minus" else 1
        frame = context["frame"]
        if piece == "global_side_plane":
            variable = frame["lateral_axis"]
            expr = f"({sigma})*({variable})-(6)[um]"
        elif piece in {"biarc_1", "biarc_2"}:
            label = "plus" if sigma == 1 else "minus"
            source_arc = case["curved_side_centerline"]["per_edge"][label]["arcs"][
                0 if piece == "biarc_1" else 1]
            center_s, center_u = source_arc["center_su_um"]
            radius = source_arc["radius_um"]
            curvature_sign = 1 if source_arc["signed_curvature_per_um"] > 0 else -1
            s_expr = dot_expr(frame["a_axis_xyz"], frame["origin_xyz_um"], subtract=float(center_s))
            u_expr = dot_expr(frame["b_axis_xyz"], frame["origin_xyz_um"], subtract=float(center_u))
            expr = f"({-sigma * curvature_sign})*(sqrt(({s_expr})^2+({u_expr})^2)-({float(radius):.17g})[um])"
        elif piece in {"output_local_plane", "coincident_global_output_plane"}:
            u0 = float(context["u_edges"][sigma])
            u_expr = dot_expr(frame["b_axis_xyz"], frame["origin_xyz_um"], subtract=u0)
            expr = f"({sigma})*({u_expr})"
        else:
            raise PortTransitionAdapterError(f"unsupported lateral owner {lateral_owner!r}")
        directions.append({"owner": lateral_owner, "distance_expression": expr,
                           "dmax_expression": "2[um]"})

    width_owner = str(row["width_owner"])
    if width_owner != "none":
        sigma = -1 if width_owner == "w_minus" else 1
        frame = context["frame"]
        w_expr = dot_expr(frame["w_axis_xyz"], frame["origin_xyz_um"],
                          subtract=sigma * context["h"])
        expr = f"({sigma})*({w_expr})"
        directions.append({"owner": width_owner, "distance_expression": expr,
                           "dmax_expression": "2[um]"})
    if len(directions) != int(row["active_direction_count"]):
        raise PortTransitionAdapterError("direction expression count differs from the frozen owner tuple")
    if len(directions) > 3:
        raise PortTransitionAdapterError("PML cell exceeds the COMSOL 1-to-3 direction contract")
    return directions


def _build_case_cells(plan: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    context = _geometry_context(plan, case)
    profiles: dict[str, dict[str, Any]] = {}

    def add(profile_id: str, role: str, primitives: Sequence[Mapping[str, Any]]) -> str:
        if profile_id in profiles:
            raise PortTransitionAdapterError(f"duplicate native profile id {profile_id}")
        profiles[profile_id] = _profile(profile_id, role, primitives)
        return profile_id

    add("air_main_inport", "physical_between_input_interface_and_input_Port",
        _main_physical_profile(case, context, "input_backing"))
    add("air_between_ports", "physical_between_input_Port_and_output_Port",
        _main_physical_profile(case, context, "between_ports"))
    add("air_output_backing", "physical_between_output_Port_and_axial_PML",
        _main_physical_profile(case, context, "backing"))
    add("air_input_axial", "input_axial_physical_cross_section",
        _input_cap_profile(context, sigma=None))
    add("air_output_axial", "output_axial_physical_cross_section",
        _output_cap_profile(context, sigma=None))

    piece_ids = (["coincident_global_output_plane"]
                 if case["curved_side_centerline"]["status"] == "NO_ARC_BETA_ZERO"
                 else ["global_side_plane", "biarc_1", "biarc_2", "output_local_plane"])
    for sigma, side in ((-1, "minus"), (1, "plus")):
        for piece in piece_ids:
            profile_id = f"side_{side}_{piece}"
            add(profile_id, f"{side}_lateral_PML_{piece}",
                _side_pml_profile(case, context, sigma, piece))
        add(f"input_side_{side}", f"input_axial_and_{side}_lateral_PML",
            _input_cap_profile(context, sigma=sigma))
        add(f"output_side_{side}", f"output_axial_and_{side}_lateral_PML",
            _output_cap_profile(context, sigma=sigma))

    profile_for: dict[tuple[str, str], list[str]] = {}
    for key, segment in (
        (("none", "none"), "air_main_inport"),
        (("none", "none"), "air_between_ports"),
        (("none", "none"), "air_output_backing"),
        (("input_axial", "none"), "air_input_axial"),
        (("output_axial", "none"), "air_output_axial"),
    ):
        profile_for.setdefault(key, []).append(segment)

    for row in case["piecewise_partition"]["regions"]:
        axial = row["longitudinal_owner"]
        lateral = row["lateral_owner"]
        width = row["width_owner"]
        key = (axial, lateral)
        if key in profile_for:
            continue
        if axial == "none" and lateral != "none":
            side, piece = lateral.split(":", 1)
            profile_id = f"side_{side}_{piece}"
        elif axial == "input_axial" and lateral != "none":
            side, piece = lateral.split(":", 1)
            if piece not in {"global_side_plane", "coincident_global_output_plane"}:
                raise PortTransitionAdapterError("input axial PML may only meet global side strips")
            profile_id = f"input_side_{side}"
        elif axial == "output_axial" and lateral != "none":
            side, piece = lateral.split(":", 1)
            if piece not in {"output_local_plane", "coincident_global_output_plane"}:
                raise PortTransitionAdapterError("output axial PML may only meet the output side plane")
            profile_id = f"output_side_{side}"
        else:
            raise PortTransitionAdapterError(f"unsupported region owner tuple {key!r}")
        profile_for[key] = [profile_id]

    h, d = context["h"], context["side_d"]
    width_ranges = {
        "none": [-h, h],
        "w_minus": [-h - d, -h],
        "w_plus": [h, h + d],
    }
    region_cells = []
    pml_by_signature: dict[str, dict[str, Any]] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    for row in case["piecewise_partition"]["regions"]:
        key = (row["longitudinal_owner"], row["lateral_owner"], row["width_owner"])
        if key in seen_keys:
            raise PortTransitionAdapterError("the frozen owner tuple list contains a duplicate region")
        seen_keys.add(key)
        cell_profile_ids = profile_for.get(key[:2])
        if not cell_profile_ids:
            raise PortTransitionAdapterError(f"no exact 2-D cell profile for owner tuple {key!r}")
        direction_rows = _pml_direction_expressions(case, context, row)
        signature = _sha(direction_rows) if direction_rows else None
        cell = {
            "region_id": row["region_id"],
            "classification": row["classification"],
            "owner_tuple": list(key),
            "profile_ids": list(cell_profile_ids),
            "width_owner": row["width_owner"],
            "width_interval_um": width_ranges[row["width_owner"]],
            "expected_active_directions": int(row["active_direction_count"]),
            "native_domain_ids": "NOT_READ",
        }
        region_cells.append(cell)
        if direction_rows:
            group = pml_by_signature.setdefault(signature, {
                "group_id": "pml_" + signature[:12],
                "direction_signature_sha256": signature,
                "directions": direction_rows,
                "region_ids": [],
            })
            group["region_ids"].append(row["region_id"])

    if len(region_cells) != int(case["piecewise_partition"]["region_count"]):
        raise PortTransitionAdapterError("lowered region tuple count differs from frozen partition")
    if len(seen_keys) != len(region_cells):
        raise PortTransitionAdapterError("owner tuple lowering is not unique")

    # Verify every profile is used by at least one tuple, and every PML group
    # has nonempty domain ownership before the Java adapter receives it.
    used_profiles = {profile_id for cell in region_cells for profile_id in cell["profile_ids"]}
    if used_profiles != set(profiles):
        raise PortTransitionAdapterError("a 2-D primitive profile is orphaned or unreferenced")
    if any(not group["region_ids"] for group in pml_by_signature.values()):
        raise PortTransitionAdapterError("empty PML selection group is forbidden")

    # Exact cap/case facts copied from the frozen plan are included so the Java
    # readback can check the same Port polygon and existing CV without reducing
    # this offline recipe to caller-supplied PASS strings.
    port = case["port_apertures"]
    input_vertices = copy.deepcopy(port["input"]["vertices_global_xyz_um"])
    output_vertices = copy.deepcopy(port["output"]["vertices_global_xyz_um"])
    input_center = [sum(float(point[axis]) for point in input_vertices) / len(input_vertices)
                    for axis in range(3)]
    output_center = [sum(float(point[axis]) for point in output_vertices) / len(output_vertices)
                     for axis in range(3)]
    return {
        "case_id": case["case_id"],
        "factor": case["factor"],
        "receiver_transform": copy.deepcopy(case["receiver_transform"]),
        "frame": context["frame"],
        "profiles": list(profiles.values()),
        "region_cells": region_cells,
        "pml_groups": list(pml_by_signature.values()),
        "port_contract": {
            "input_polygon_global_xyz_um": input_vertices,
            "input_area_um2": float(port["input"]["area_um2"]),
            "input_center_global_xyz_um": input_center,
            "input_normal_axis_xyz": [1.0, 0.0, 0.0],
            "output_polygon_global_xyz_um": output_vertices,
            "output_area_um2": float(port["output"]["area_um2"]),
            "output_center_global_xyz_um": output_center,
            "output_normal_axis_xyz": list(context["frame"]["a_axis_xyz"]),
            "input_port_feature_tag": "portIn3d",
            "output_port_feature_tag": "portOut3d",
            "input_selection_tag": "sel3dInputPort",
            "output_selection_tag": "sel3dOutputPort",
            "capture_selection_tag": "sel3dOutputCoreCapture",
            "full_air_aperture_required": True,
            "capture_aperture_separate": bool(port["output"]["core_capture_aperture_is_separate"]),
        },
        "control_volume_contract": {
            "box_um": {"x": [-19.0, 19.0], "y": [-5.5, 5.5], "z": [-5.5, 5.5]},
            "transition_clearance_min_um": float(
                case["finite_roi_bounds_certificate"]["minimum_margin_outside_cv_x_plus_um"]),
            "cv_native_readback": "NOT_RUN",
        },
    }


def build_port_transition_native_recipe(
        plan: Mapping[str, Any] | None = None, *, case_id: str) -> dict[str, Any]:
    """Lower one frozen case into exact WorkPlane/Extrude/PML inputs.

    The returned cells form a non-overlapping owner-tuple partition by their
    directed analytic boundaries; COMSOL still has to confirm the resulting
    topology, selections, full Port faces, and PML properties natively.
    """
    frozen = build_port_transition_plan() if plan is None else plan
    verification = verify_port_transition_plan(frozen)
    return _build_native_recipe(frozen, case_id=case_id, verification=verification)


def _build_native_recipe(frozen: Mapping[str, Any], *, case_id: str,
                         verification: Mapping[str, Any] | None = None) -> dict[str, Any]:
    verification = verify_port_transition_plan(frozen) if verification is None else verification
    if verification["native_result"] != "NOT_RUN":
        raise PortTransitionAdapterError("native results cannot replace the frozen software plan")
    matches = [case for case in frozen["cases"] if case.get("case_id") == case_id]
    if len(matches) != 1:
        raise PortTransitionAdapterError("case_id must identify one case in the recomputed 19-case matrix")
    case_recipe = _build_case_cells(frozen, matches[0])
    result = {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "case_id": case_id,
        "source_plan_sha256": verification["plan_sha256"],
        "source_recipe_sha256": verification["source_recipe_sha256"],
        "case_matrix_sha256": verification["case_matrix_sha256"],
        "java_source_artifact": JAVA_SOURCE,
        "java_entrypoint": JAVA_ENTRYPOINT,
        "target_component": COMPONENT_TAG,
        "target_geometry": GEOMETRY_TAG,
        "source_fixture_id": FIXTURE_ID,
        "construction": {
            "primitive_space": "exact 2-D line/circular-arc closed cell profiles",
            "workplane_frame": "origin=c, local x=a, local y=b, normal=w=a cross b",
            "volume_generation": "each profile extruded over one disjoint w-owner interval",
            "boolean_policy": "FormUnion with interior boundaries retained; one explicit selection per owner tuple",
            "pml_policy": "one userDefined PML coordinate system per identical active-distance signature",
            "pml_stretch": {"ScalingType": "userDefined", "stretchingType": "polynomial",
                            "wavelengthSourceType": "userDefined", "typicalWavelength": "lambda0",
                            "PMLfactor": 1.0, "PMLgamma": 1.0},
            "native_support": "CANDIDATE_NOT_RUN",
            "geometry_compatibility": "UNVERIFIED",
            "pml_compatibility": "UNVERIFIED",
            "study_or_solver_invoked": False,
        },
        **case_recipe,
    }
    result["recipe_sha256"] = _sha(result)
    return result


def build_port_transition_native_recipes(
        plan: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Lower the complete frozen matrix without re-verifying it for each case."""
    frozen = build_port_transition_plan() if plan is None else plan
    verification = verify_port_transition_plan(frozen)
    if verification["native_result"] != "NOT_RUN":
        raise PortTransitionAdapterError("native results cannot replace the frozen software plan")
    recipes = [_build_native_recipe(frozen, case_id=str(case["case_id"]),
                                    verification=verification)
               for case in frozen["cases"]]
    if len(recipes) != len(frozen["cases"]) or len({row["case_id"] for row in recipes}) != len(recipes):
        raise PortTransitionAdapterError("the frozen case matrix did not lower uniquely")
    return recipes


def verify_port_transition_native_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the exact recipe from source plan inputs; never trust self-hash alone."""
    if not isinstance(recipe, Mapping) or recipe.get("schema_id") != SCHEMA_ID:
        raise PortTransitionAdapterError("exact W23 transition native recipe schema is required")
    expected_digest = _sha({key: value for key, value in recipe.items()
                            if key != "recipe_sha256"})
    if recipe.get("recipe_sha256") != expected_digest:
        raise PortTransitionAdapterError("native transition recipe self-digest mismatch")
    expected = build_port_transition_native_recipe(case_id=str(recipe.get("case_id", "")))
    if _sha(recipe) != _sha(expected):
        raise PortTransitionAdapterError("native recipe differs from the recomputed frozen case cells")
    return {
        "status": "OFFLINE_RECIPE_VERIFIED_NATIVE_NOT_RUN",
        "recipe_sha256": expected["recipe_sha256"],
        "source_plan_sha256": expected["source_plan_sha256"],
        "case_id": expected["case_id"],
        "profile_count": len(expected["profiles"]),
        "region_cell_count": len(expected["region_cells"]),
        "pml_group_count": len(expected["pml_groups"]),
        "native_result": "NOT_RUN",
        "geometry_compatibility": "UNVERIFIED",
        "pml_compatibility": "UNVERIFIED",
    }


def build_port_transition_dispatch(
        recipe: Mapping[str, Any], *, source_artifact: str, project_id: str,
        model_ref: Mapping[str, Any], model_tag: str, revision: int,
        request_id: str, idempotency_key: str) -> dict[str, Any]:
    """Build one exact managed `code.execute_java` call; this does not dispatch it."""
    verification = verify_port_transition_native_recipe(recipe)
    if not isinstance(source_artifact, str) or source_artifact != JAVA_SOURCE:
        raise PortTransitionAdapterError("registered source artifact must be the V1 Java adapter")
    if not isinstance(project_id, str) or not project_id.strip():
        raise PortTransitionAdapterError("managed project identity is required")
    if not isinstance(model_ref, Mapping) or not model_ref:
        raise PortTransitionAdapterError("persisted managed ModelRef is required")
    allowed_ref_keys = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
    if set(model_ref) != allowed_ref_keys:
        raise PortTransitionAdapterError("managed ModelRef must contain the exact server identity fields")
    if (type(model_ref.get("schema_version")) is not int
            or model_ref.get("schema_version") != 1
            or type(model_ref.get("generation")) is not int or model_ref.get("generation") < 1):
        raise PortTransitionAdapterError("managed ModelRef schema/generation is invalid")
    session_id = model_ref.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise PortTransitionAdapterError("ModelRef must carry its exact session_id")
    server_instance_id = model_ref.get("server_instance_id")
    if not isinstance(server_instance_id, str) or not server_instance_id.strip():
        raise PortTransitionAdapterError("ModelRef must carry its exact server_instance_id")
    if not isinstance(model_tag, str) or not model_tag.strip():
        raise PortTransitionAdapterError("managed model tag is required")
    if model_ref.get("model_tag") != model_tag:
        raise PortTransitionAdapterError("ModelRef model_tag differs from the selected model")
    if type(revision) is not int or revision < 0:
        raise PortTransitionAdapterError("expected_revision must be a nonnegative integer")
    if any(not isinstance(value, str) or not value.strip()
           for value in (request_id, idempotency_key)):
        raise PortTransitionAdapterError("request_id and idempotency_key are required")

    binding = {
        "project_id": project_id,
        "session_id": session_id,
        "server_instance_id": server_instance_id,
        "model_ref": copy.deepcopy(dict(model_ref)),
        "model_tag": model_tag,
        "expected_revision": revision,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
    }
    java_arguments = {
        "phase": "apply_transition_case",
        "recipe": copy.deepcopy(dict(recipe)),
        "recipe_sha256": verification["recipe_sha256"],
        "managed_identity": {key: binding[key] for key in
                             ("project_id", "session_id", "server_instance_id", "model_ref",
                              "model_tag", "expected_revision")},
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
    }
    request = {
        "operation": "operation_call",
        "arguments": {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": source_artifact,
            "entrypoint": JAVA_ENTRYPOINT,
            "mode": "trusted",
            "arguments": java_arguments,
        }},
        "execution": {
            "project_id": project_id,
            "session_id": session_id,
            "model_ref": copy.deepcopy(dict(model_ref)),
            "expected_revision": revision,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        },
        "dispatch_scope": "one case geometry/PML mutation and native readback; no Study.run or solver call",
        "recipe_sha256": verification["recipe_sha256"],
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
    }
    return request
