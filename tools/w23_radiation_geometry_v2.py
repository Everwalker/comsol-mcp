"""Versioned software contract for the W23 3-D radiation/PML geometry.

The module describes a finite half-space arrangement, validates its PML
partition and mapped-coordinate seams, and validates a closed control-volume
surface readback. It never calls COMSOL or upgrades a software fixture to
native/scientific acceptance. The v1 fixture remains untouched.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from tools.w23_full3d import FIXTURE_ID as V1_FIXTURE_ID, build_case_matrix


class RadiationGeometryV2Error(ValueError):
    """The versioned W23 radiation geometry or native readback is inadmissible."""


SCHEMA_ID = "urn:comsol-mcp:w23:radiation-geometry:2.0.0"
FIXTURE_ID = "w23_full3d_radiation_geometry_v2"
CV_BOX_UM = {"x": [-19.0, 19.0], "y": [-5.5, 5.5], "z": [-5.5, 5.5]}
_TOL = 1e-8
_FACE_ORDER = (
    "input_axial", "output_axial", "transverse_y_minus",
    "transverse_y_plus", "transverse_z_minus", "transverse_z_plus",
)
_OPPOSITES = (
    frozenset(("transverse_y_minus", "transverse_y_plus")),
    frozenset(("transverse_z_minus", "transverse_z_plus")),
    frozenset(("input_axial", "output_axial")),
)


def _fail(message: str) -> None:
    raise RadiationGeometryV2Error(message)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _unit(v: Sequence[float], label: str) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        _fail(f"{label} must be a 3-vector")
    out = []
    for raw in v:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            _fail(f"{label} must contain finite numbers")
        out.append(float(raw))
    norm = math.sqrt(sum(x * x for x in out))
    if norm <= 0:
        _fail(f"{label} must be nonzero")
    return [x / norm for x in out]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _add(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def _scale(s: float, a: Sequence[float]) -> list[float]:
    return [s * x for x in a]


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _matrix_solve3(rows: Sequence[Sequence[float]], values: Sequence[float]) -> list[float] | None:
    a = [list(map(float, row)) + [float(value)] for row, value in zip(rows, values)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda i: abs(a[i][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        a[col] = [x / div for x in a[col]]
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            a[row] = [x - factor * y for x, y in zip(a[row], a[col])]
    return [a[i][3] for i in range(3)]


def _vertices(constraints: Sequence[tuple[Sequence[float], float]], tol: float = 1e-7) -> list[list[float]]:
    points: list[list[float]] = []
    for indexes in itertools.combinations(range(len(constraints)), 3):
        candidate = _matrix_solve3([constraints[i][0] for i in indexes],
                                   [constraints[i][1] for i in indexes])
        if candidate is None:
            continue
        if any(_dot(normal, candidate) > bound + tol for normal, bound in constraints):
            continue
        if not any(_norm([x - y for x, y in zip(candidate, old)]) <= tol for old in points):
            points.append(candidate)
    return points


def _full_dimensional(points: Sequence[Sequence[float]],
                      constraints: Sequence[tuple[Sequence[float], float]],
                      tol: float = 1e-7) -> bool:
    if len(points) < 4:
        return False
    center = [sum(point[i] for point in points) / len(points) for i in range(3)]
    return all(bound - _dot(normal, center) > tol for normal, bound in constraints)


def _affine_dimension(points: Sequence[Sequence[float]], tol: float = 1e-7) -> int:
    if not points:
        return -1
    origin = points[0]
    vectors = [[p[i] - origin[i] for i in range(3)] for p in points[1:]]
    first = next((v for v in vectors if _norm(v) > tol), None)
    if first is None:
        return 0
    cross_nonzero = False
    for vector in vectors:
        cross = [first[1] * vector[2] - first[2] * vector[1],
                 first[2] * vector[0] - first[0] * vector[2],
                 first[0] * vector[1] - first[1] * vector[0]]
        if _norm(cross) > tol:
            cross_nonzero = True
            second = vector
            break
    if not cross_nonzero:
        return 1
    normal = [first[1] * second[2] - first[2] * second[1],
              first[2] * second[0] - first[0] * second[2],
              first[0] * second[1] - first[1] * second[0]]
    return 3 if any(abs(_dot(normal, vector)) > tol for vector in vectors) else 2


def _face_definitions(receiver: Mapping[str, Any], backing_um: float,
                      axial_pml_um: float, transverse_pml_um: float,
                      halfwidth_um: float) -> list[dict[str, Any]]:
    axis_out = _validate_receiver_transform(receiver)
    center_out = [float(x) for x in receiver["center_xyz_um"]]
    input_axis = [-1.0, 0.0, 0.0]
    input_port = [-20.0, 0.0, 0.0]
    output_port = center_out
    return [
        {"id": "input_axial", "axis": input_axis,
         "port_point_um": input_port,
         "backing_pml_interface_um": _add(input_port, _scale(backing_um, input_axis)),
         "direction_source": "negative_input_propagation_axis",
         "dmax_um": axial_pml_um, "region_type": "axial_cap"},
        {"id": "output_axial", "axis": axis_out,
         "port_point_um": output_port,
         "backing_pml_interface_um": _add(output_port, _scale(backing_um, axis_out)),
         "direction_source": "actual_rotated_receiver_axis",
         "dmax_um": axial_pml_um, "region_type": "axial_cap"},
        {"id": "transverse_y_minus", "axis": [0.0, -1.0, 0.0],
         "port_point_um": None, "backing_pml_interface_um": [0.0, -halfwidth_um, 0.0],
         "direction_source": "global_minus_y", "dmax_um": transverse_pml_um,
         "region_type": "transverse_side"},
        {"id": "transverse_y_plus", "axis": [0.0, 1.0, 0.0],
         "port_point_um": None, "backing_pml_interface_um": [0.0, halfwidth_um, 0.0],
         "direction_source": "global_plus_y", "dmax_um": transverse_pml_um,
         "region_type": "transverse_side"},
        {"id": "transverse_z_minus", "axis": [0.0, 0.0, -1.0],
         "port_point_um": None, "backing_pml_interface_um": [0.0, 0.0, -halfwidth_um],
         "direction_source": "global_minus_z", "dmax_um": transverse_pml_um,
         "region_type": "transverse_side"},
        {"id": "transverse_z_plus", "axis": [0.0, 0.0, 1.0],
         "port_point_um": None, "backing_pml_interface_um": [0.0, 0.0, halfwidth_um],
         "direction_source": "global_plus_z", "dmax_um": transverse_pml_um,
         "region_type": "transverse_side"},
    ]


def _clip_polygon_halfspace(polygon: Sequence[Sequence[float]],
                            a: float, b: float, upper: float) -> list[list[float]]:
    """Clip a local 2-D polygon by a*u+b*v <= upper."""
    if not polygon:
        return []
    output: list[list[float]] = []
    previous = list(polygon[-1])
    previous_value = a * previous[0] + b * previous[1] - upper
    previous_inside = previous_value <= 1e-10
    for current_raw in polygon:
        current = list(current_raw)
        current_value = a * current[0] + b * current[1] - upper
        current_inside = current_value <= 1e-10
        if current_inside != previous_inside:
            denominator = previous_value - current_value
            if abs(denominator) <= 1e-15:
                _fail("port aperture plane clipping encountered a degenerate crossing")
            fraction = previous_value / denominator
            output.append([previous[i] + fraction * (current[i] - previous[i])
                           for i in range(2)])
        if current_inside:
            output.append(current)
        previous, previous_value, previous_inside = current, current_value, current_inside
    deduplicated: list[list[float]] = []
    for point in output:
        if not deduplicated or _norm([point[0] - deduplicated[-1][0],
                                      point[1] - deduplicated[-1][1], 0.0]) > 1e-9:
            deduplicated.append(point)
    if len(deduplicated) > 1 and _norm([deduplicated[0][0] - deduplicated[-1][0],
                                        deduplicated[0][1] - deduplicated[-1][1], 0.0]) <= 1e-9:
        deduplicated.pop()
    return deduplicated


def _port_aperture_polygon(recipe: Mapping[str, Any], receiver: Mapping[str, Any],
                           faces: Sequence[Mapping[str, Any]], port_role: str) -> dict[str, Any]:
    if port_role == "input":
        center = [float(value) for value in recipe["geometry"]["input_port_center_um"]]
        propagation_axis = [1.0, 0.0, 0.0]
        outward_axis = [-1.0, 0.0, 0.0]
        basis_u, basis_v = [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
    elif port_role == "output":
        center = [float(value) for value in receiver["center_xyz_um"]]
        propagation_axis = _unit(receiver["axis_xyz"], "output port axis")
        outward_axis = propagation_axis
        theta_y, theta_z = math.radians(float(receiver["theta_y_deg"])), math.radians(float(receiver["theta_z_deg"]))
        basis_u = [-math.sin(theta_z), math.cos(theta_z), 0.0]
        basis_v = [math.cos(theta_z) * math.sin(theta_y),
                   math.sin(theta_z) * math.sin(theta_y), math.cos(theta_y)]
        if abs(_dot(propagation_axis, basis_u)) > 1e-10 or abs(_dot(propagation_axis, basis_v)) > 1e-10:
            _fail("rotated Numeric Port aperture frame is not orthonormal")
    else:
        _fail("Numeric Port aperture role must be input or output")

    polygon: list[list[float]] = [[-100.0, -100.0], [100.0, -100.0],
                                  [100.0, 100.0], [-100.0, 100.0]]
    for face in faces:
        d_center = _dot(face["axis"], [center[i] - face["backing_pml_interface_um"][i]
                                      for i in range(3)])
        slope_u = _dot(face["axis"], basis_u)
        slope_v = _dot(face["axis"], basis_v)
        polygon = _clip_polygon_halfspace(polygon, slope_u, slope_v, -d_center)
        if len(polygon) < 3:
            _fail("actual physical halfspaces do not bound a full-dimensional Numeric Port air aperture")
    area = abs(sum(polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
                   - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
                   for index in range(len(polygon))) / 2.0)
    if not math.isfinite(area) or area <= 0:
        _fail("full-air Numeric Port aperture has invalid analytic area")
    global_points = [[center[i] + local[0] * basis_u[i] + local[1] * basis_v[i]
                      for i in range(3)] for local in polygon]
    local_halfwidth = float(recipe["geometry"]["port_air_aperture"]["minimum_local_square_halfwidth_um"])
    square_corners = [[u, v] for u, v in ((-local_halfwidth, -local_halfwidth),
                                          (local_halfwidth, -local_halfwidth),
                                          (local_halfwidth, local_halfwidth),
                                          (-local_halfwidth, local_halfwidth))]
    square_margin = math.inf
    for corner in square_corners:
        point = [center[i] + corner[0] * basis_u[i] + corner[1] * basis_v[i]
                 for i in range(3)]
        for face in faces:
            distance = _dot(face["axis"], [point[i] - face["backing_pml_interface_um"][i]
                                           for i in range(3)])
            square_margin = min(square_margin, -distance)
            if distance > 1e-9:
                _fail("minimum full-air Numeric Port square is clipped by a registered physical boundary")
    backing = float(recipe["geometry"]["backing_length_um"])
    axial_pml = float(recipe["geometry"]["axial_pml_thickness_um"])
    sweep_rows = []
    minimum_transverse_clearance = math.inf
    transverse_faces = [face for face in faces if face["id"].startswith("transverse_")]
    for station_role, distance_along_axis in (("port_plane", 0.0),
                                               ("backing_outer_face", backing),
                                               ("axial_pml_outer_face", backing + axial_pml)):
        station_points = []
        station_clearance = math.inf
        for local, global_point in zip(polygon, global_points):
            swept = [global_point[i] + distance_along_axis * outward_axis[i]
                     for i in range(3)]
            clearances = [
                -_dot(face["axis"], [swept[i] - face["backing_pml_interface_um"][i]
                                     for i in range(3)])
                for face in transverse_faces
            ]
            station_clearance = min(station_clearance, *clearances)
            station_points.append({"local_uv_um": local, "global_xyz_um": swept,
                                   "transverse_clearances_um": clearances})
        minimum_transverse_clearance = min(minimum_transverse_clearance, station_clearance)
        sweep_rows.append({"station": station_role,
                           "distance_along_outward_axis_um": distance_along_axis,
                           "minimum_transverse_clearance_um": station_clearance,
                           "vertices": station_points})
    sweep_status = ("SOFTWARE_FULL_POLYGON_SWEEP_CLEAR"
                    if minimum_transverse_clearance >= -1e-9
                    else "CONFLICT_WITH_GLOBAL_TRANSVERSE_PML_ENVELOPE")
    return {
        "port_role": port_role,
        "geometry": "full physical halfspace-envelope intersection with the actual Numeric Port plane",
        "selection_scope": "all plane boundary pieces, including air, cladding, and core sections; not a cladding-radius disk",
        "center_xyz_um": center,
        "normal_xyz": propagation_axis,
        "outward_sweep_axis_xyz": outward_axis,
        "local_basis_uv_xyz": {"u": basis_u, "v": basis_v},
        "polygon_vertices_local_uv_um": polygon,
        "polygon_vertices_global_xyz_um": global_points,
        "analytic_polygon_area_um2": area,
        "minimum_local_square_halfwidth_um": local_halfwidth,
        "minimum_square_corners_inside_full_polygon": True,
        "minimum_square_to_physical_halfspace_clearance_um": square_margin,
        "axial_material_continuity_required": ["fiber_core", "fiber_cladding", "air"],
        "axial_sweep_stations": sweep_rows,
        "minimum_full_polygon_transverse_clearance_um": minimum_transverse_clearance,
        "full_polygon_sweep_status": sweep_status,
        "native_boundary_ids_and_material_membership": "NOT_READ",
    }


def _validate_receiver_transform(receiver: Mapping[str, Any]) -> list[float]:
    """Reject an inconsistent frame rather than silently normalizing caller data."""
    if not isinstance(receiver, Mapping):
        _fail("receiver transform must be a mapping")
    if receiver.get("rotation_order") != "right-handed global +y then +z":
        _fail("receiver transform must use the registered right-handed y-then-z frame")
    raw_axis = receiver.get("axis_xyz")
    axis = _unit(raw_axis, "receiver axis")
    if _norm([float(raw_axis[i]) - axis[i] for i in range(3)]) > 1e-10:
        _fail("receiver axis readback must already be unit length")
    pivot = receiver.get("pivot_xyz_um")
    center = receiver.get("center_xyz_um")
    if not isinstance(pivot, (list, tuple)) or len(pivot) != 3:
        _fail("receiver rotation pivot must be a finite 3-vector")
    if not isinstance(center, (list, tuple)) or len(center) != 3:
        _fail("receiver Port center must be a finite 3-vector")
    pivot_values = [float(value) for value in pivot]
    center_values = [float(value) for value in center]
    if not all(math.isfinite(value) for value in pivot_values + center_values):
        _fail("receiver pivot and Port center must be finite")
    theta_y, theta_z = receiver.get("theta_y_deg"), receiver.get("theta_z_deg")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(float(value)) or abs(float(value)) > 0.5 + 1e-12
           for value in (theta_y, theta_z)):
        _fail("receiver angular transform is outside the registered +/-0.5-degree envelope")
    ry, rz = math.radians(float(theta_y)), math.radians(float(theta_z))
    expected_axis = [math.cos(rz) * math.cos(ry),
                     math.sin(rz) * math.cos(ry), -math.sin(ry)]
    if _norm([a - b for a, b in zip(axis, expected_axis)]) > 1e-10:
        _fail("receiver axis differs from the registered rigid-rotation oracle")
    center_delta = [center_values[i] - pivot_values[i] for i in range(3)]
    axial_length = _dot(center_delta, axis)
    perpendicular = [center_delta[i] - axial_length * axis[i] for i in range(3)]
    if axial_length <= 0.0 or _norm(perpendicular) > 1e-8:
        _fail("receiver Port center is not downstream on the actual rotated fiber axis")
    radius = receiver.get("cladding_radius_um")
    if (isinstance(radius, bool) or not isinstance(radius, (int, float))
            or not math.isfinite(float(radius)) or not (0.0 < float(radius) <= 2.55 + 1e-12)):
        _fail("receiver cladding radius is outside the registered tolerance envelope")
    return axis


def _dist(face: Mapping[str, Any], point: Sequence[float]) -> float:
    return _dot(face["axis"], [point[i] - face["backing_pml_interface_um"][i] for i in range(3)])


def _constraints_for(active: frozenset[str], faces: Sequence[Mapping[str, Any]]) -> list[tuple[list[float], float]]:
    output = []
    for face in faces:
        axis = list(face["axis"])
        origin = _dot(axis, face["backing_pml_interface_um"])
        if face["id"] in active:
            # PML cell: 0 <= d <= thickness.
            output.append((axis, origin + float(face["dmax_um"])))
            output.append(([-x for x in axis], -origin))
        else:
            # Physical or other-side cell: d <= 0.
            output.append((axis, origin))
    return output


def _active_regions(faces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for count in range(len(faces) + 1):
        for subset in itertools.combinations(_FACE_ORDER, count):
            active = frozenset(subset)
            constraints = _constraints_for(active, faces)
            points = _vertices(constraints)
            if not _full_dimensional(points, constraints):
                continue
            if len(active) > 3:
                _fail("the geometry requires more than three simultaneous PML stretching directions")
            if any(pair.issubset(active) for pair in _OPPOSITES):
                _fail("opposing/overlapping axial PML ownership is not admitted")
            regions.append({
                "region_id": "physical" if not active else "pml__" + "__".join(sorted(active)),
                "active_stretch_ids": sorted(active),
                "pairwise_direction_cosines": {
                    f"{left}|{right}": _dot(
                        next(face["axis"] for face in faces if face["id"] == left),
                        next(face["axis"] for face in faces if face["id"] == right))
                    for left, right in itertools.combinations(sorted(active), 2)},
                "distance_inequalities": [
                    {"face_id": face["id"],
                     "expression": "0 <= d_face <= dmax" if face["id"] in active else "d_face <= 0",
                     "lower_um": 0.0 if face["id"] in active else None,
                     "upper_um": float(face["dmax_um"]) if face["id"] in active else 0.0,
                     "active_stretch": face["id"] in active}
                    for face in faces
                ],
                "vertices_um": points,
                "domain_owner_count_expected": 0 if not active else 1,
                "domain_material_readback_required": True,
                "fiber_material_continuity_required_when_intersected": [
                    "fiber_core", "fiber_cladding", "air"],
            })
    if not any(not row["active_stretch_ids"] for row in regions):
        _fail("the physical interior is empty or unbounded")
    if not any(row["active_stretch_ids"] for row in regions):
        _fail("the outer envelope contains no PML region")
    return regions


def _polyhedron_volume(points: Sequence[Sequence[float]],
                       constraints: Sequence[tuple[Sequence[float], float]]) -> float:
    """Compute a convex polyhedron's analytic-coordinate volume from its facets."""
    if len(points) < 4:
        _fail("halfspace cell is not a bounded three-dimensional polyhedron")
    center = [sum(point[i] for point in points) / len(points) for i in range(3)]
    seen_planes: set[tuple[float, float, float, float]] = set()
    volume = 0.0
    facet_count = 0
    for normal_raw, bound_raw in constraints:
        length = _norm(normal_raw)
        if length <= 0 or not math.isfinite(length):
            _fail("halfspace cell contains an invalid facet normal")
        normal = [float(value) / length for value in normal_raw]
        bound = float(bound_raw) / length
        first = next((value for value in normal if abs(value) > 1e-12), 1.0)
        if first < 0:
            normal = [-value for value in normal]
            bound = -bound
        plane_key = tuple(round(value, 10) for value in normal) + (round(bound, 10),)
        if plane_key in seen_planes:
            continue
        seen_planes.add(plane_key)
        facet_points = [point for point in points
                        if abs(_dot(normal, point) - bound) <= 1e-7]
        if len(facet_points) < 3 or _affine_dimension(facet_points) < 2:
            continue
        facet_center = [sum(point[i] for point in facet_points) / len(facet_points)
                        for i in range(3)]
        reference = [1.0, 0.0, 0.0] if abs(normal[0]) < 0.8 else [0.0, 1.0, 0.0]
        basis_u = [normal[1] * reference[2] - normal[2] * reference[1],
                   normal[2] * reference[0] - normal[0] * reference[2],
                   normal[0] * reference[1] - normal[1] * reference[0]]
        basis_u = _unit(basis_u, "polyhedron facet basis")
        basis_v = [normal[1] * basis_u[2] - normal[2] * basis_u[1],
                   normal[2] * basis_u[0] - normal[0] * basis_u[2],
                   normal[0] * basis_u[1] - normal[1] * basis_u[0]]
        projected = [(_dot([point[i] - facet_center[i] for i in range(3)], basis_u),
                      _dot([point[i] - facet_center[i] for i in range(3)], basis_v))
                     for point in facet_points]
        projected.sort(key=lambda pair: math.atan2(pair[1], pair[0]))
        area = abs(sum(projected[index][0] * projected[(index + 1) % len(projected)][1]
                       - projected[(index + 1) % len(projected)][0] * projected[index][1]
                       for index in range(len(projected)))) / 2.0
        height = abs(bound - _dot(normal, center))
        if not math.isfinite(area) or not math.isfinite(height) or area <= 0 or height <= 0:
            _fail("halfspace cell contains a degenerate analytic facet")
        volume += area * height / 3.0
        facet_count += 1
    if facet_count < 4 or not math.isfinite(volume) or volume <= 0:
        _fail("halfspace cell has incomplete or degenerate analytic facets")
    return volume


def _partition_volume_certificate(regions: Sequence[Mapping[str, Any]],
                                  faces: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cell_volumes = []
    for region in regions:
        constraints = _constraints_for(frozenset(region["active_stretch_ids"]), faces)
        cell_volumes.append({
            "region_id": region["region_id"],
            "volume_um3": _polyhedron_volume(region["vertices_um"], constraints),
        })
    envelope_constraints = [
        (face["axis"], _dot(face["axis"], face["backing_pml_interface_um"])
         + float(face["dmax_um"]))
        for face in faces
    ]
    envelope_vertices = _vertices(envelope_constraints)
    envelope_volume = _polyhedron_volume(envelope_vertices, envelope_constraints)
    cell_volume = sum(row["volume_um3"] for row in cell_volumes)
    residual = abs(cell_volume - envelope_volume)
    if residual > max(1e-8, envelope_volume * 1e-10):
        _fail("active-distance cells leave an analytic-volume gap or overlap in the PML envelope")
    return {
        "method": "convex_halfspace_facet_volume_sum_against_outer_envelope",
        "cell_volumes_um3": cell_volumes,
        "cell_volume_sum_um3": cell_volume,
        "outer_envelope_volume_um3": envelope_volume,
        "absolute_volume_residual_um3": residual,
        "disjoint_interior_rule": "unique six-distance sign pattern; cell interiors cannot overlap",
        "status": "SOFTWARE_ANALYTIC_PARTITION_NO_GAP_OR_OVERLAP",
    }


def _real_map_injectivity_certificate(regions: Sequence[Mapping[str, Any]],
                                      faces: Sequence[Mapping[str, Any]],
                                      wavelength: float, factor: float,
                                      gamma: float) -> dict[str, Any]:
    if gamma != 1.0:
        _fail("global real-map injectivity certificate is implemented only for the frozen gamma=1 profile")
    max_active = max(len(region["active_stretch_ids"]) for region in regions)
    worst_negative_slope = max(
        max(0.0, 1.0 - factor * wavelength / float(face["dmax_um"]))
        for face in faces)
    monotonicity_lower_bound = 1.0 - max_active * worst_negative_slope
    if monotonicity_lower_bound <= 1e-10:
        _fail("the registered piecewise real-coordinate map may fold or overlap")
    return {
        "map_component": "real_part_of_complex_coordinate_map",
        "method": "strong_monotonicity_bound_for_piecewise_linear_gamma1_map",
        "maximum_active_stretch_directions": max_active,
        "maximum_negative_directional_slope": worst_negative_slope,
        "minimum_strong_monotonicity_bound": monotonicity_lower_bound,
        "status": "SOFTWARE_ANALYTIC_REAL_PROJECTION_INJECTIVE",
        "scope_note": "proves no interior overlap for this registered mathematical map; does not prove COMSOL nonorthogonal PML implementation correctness",
    }


def _polynomial_delta(distance: float, thickness: float, wavelength: float,
                      factor: float, gamma: float) -> complex:
    xi = distance / thickness
    f = factor * (xi ** gamma) * (1.0 - 1.0j)
    return wavelength * f - distance


def _mapped_coordinate(point: Sequence[float], active: Sequence[str],
                       faces_by_id: Mapping[str, Mapping[str, Any]],
                       wavelength: float, factor: float, gamma: float) -> list[complex]:
    mapped = [complex(float(x), 0.0) for x in point]
    for face_id in active:
        face = faces_by_id[face_id]
        distance = _dist(face, point)
        if distance < -1e-7 or distance > float(face["dmax_um"]) + 1e-7:
            _fail("PML distance escaped its declared [0,dmax] coordinate range")
        delta = _polynomial_delta(max(0.0, distance), float(face["dmax_um"]),
                                  wavelength, factor, gamma)
        for i in range(3):
            mapped[i] += face["axis"][i] * delta
    return mapped


def _complex_det3(matrix: Sequence[Sequence[complex]]) -> complex:
    a = matrix
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def _jacobian(active: Sequence[str], point: Sequence[float],
              faces_by_id: Mapping[str, Mapping[str, Any]], wavelength: float,
              factor: float, gamma: float) -> list[list[complex]]:
    matrix = [[complex(float(i == j), 0.0) for j in range(3)] for i in range(3)]
    for face_id in active:
        face = faces_by_id[face_id]
        distance = max(0.0, _dist(face, point))
        thickness = float(face["dmax_um"])
        xi = distance / thickness
        # d(tilde_d)/dd = (lambda/dmax) f'(xi); gamma is frozen to 1 below.
        if gamma != 1.0:
            derivative_f = factor * gamma * (xi ** (gamma - 1.0)) * (1.0 - 1.0j)
        else:
            derivative_f = factor * (1.0 - 1.0j)
        stretch = wavelength / thickness * derivative_f
        axis = face["axis"]
        for i in range(3):
            for j in range(3):
                matrix[i][j] += axis[i] * axis[j] * (stretch - 1.0)
    return matrix


def _validate_mapping_partition(regions: Sequence[Mapping[str, Any]],
                                faces: Sequence[Mapping[str, Any]],
                                wavelength: float, factor: float, gamma: float) -> dict[str, Any]:
    faces_by_id = {row["id"]: row for row in faces}
    smallest_det = math.inf
    largest_det = 0.0
    for region in regions:
        active = region["active_stretch_ids"]
        if not active:
            continue
        for point in region["vertices_um"] + [[sum(p[i] for p in region["vertices_um"]) /
                                                len(region["vertices_um"]) for i in range(3)]]:
            mapped = _mapped_coordinate(point, active, faces_by_id, wavelength, factor, gamma)
            if not all(math.isfinite(value.real) and math.isfinite(value.imag) for value in mapped):
                _fail("complex coordinate map contains a nonfinite value")
            jacobian = _jacobian(active, point, faces_by_id, wavelength, factor, gamma)
            if any(not (math.isfinite(value.real) and math.isfinite(value.imag))
                   for row in jacobian for value in row):
                _fail("complex PML Jacobian contains a nonfinite value")
            det = abs(_complex_det3(jacobian))
            if not math.isfinite(det) or det <= 1e-10:
                _fail("complex PML coordinate map has a singular/degenerate Jacobian")
            smallest_det = min(smallest_det, det)
            largest_det = max(largest_det, det)

    seam_count = 0
    max_residual = 0.0
    seam_receipts: list[dict[str, Any]] = []
    by_active = {frozenset(row["active_stretch_ids"]): row for row in regions}
    for left_set, left in by_active.items():
        for axis_id in _FACE_ORDER:
            if axis_id in left_set:
                continue
            right_set = frozenset((*left_set, axis_id))
            right = by_active.get(right_set)
            if right is None:
                continue
            shared_constraints = _constraints_for(left_set, faces) + _constraints_for(right_set, faces)
            shared = _vertices(shared_constraints)
            if _affine_dimension(shared) < 2:
                continue
            seam_count += 1
            samples = shared + [[sum(p[i] for p in shared) / len(shared) for i in range(3)]]
            seam_max_residual = 0.0
            for point in samples:
                lm = _mapped_coordinate(point, left["active_stretch_ids"], faces_by_id,
                                         wavelength, factor, gamma)
                rm = _mapped_coordinate(point, right["active_stretch_ids"], faces_by_id,
                                         wavelength, factor, gamma)
                residual = max(abs(a - b) for a, b in zip(lm, rm))
                max_residual = max(max_residual, residual)
                seam_max_residual = max(seam_max_residual, residual)
                if residual > 1e-7:
                    _fail("complex PML coordinate map is discontinuous across a shared partition face")
            seam_receipts.append({
                "left_region_id": left["region_id"],
                "right_region_id": right["region_id"],
                "transition_face_id": axis_id,
                "shared_face_vertices_um": shared,
                "sample_count": len(samples),
                "maximum_complex_coordinate_jump_um": seam_max_residual,
            })
    if seam_count == 0:
        _fail("PML partition has no auditable shared faces")
    return {"seam_count": seam_count, "shared_interfaces": seam_receipts,
            "max_complex_coordinate_jump_um": max_residual,
            "minimum_abs_pml_jacobian_determinant": smallest_det,
            "maximum_abs_pml_jacobian_determinant": largest_det,
            "mapping_status": "SOFTWARE_ANALYTIC_MAP_VALID_NATIVE_UNVERIFIED",
            "nonorthogonal_comsol_pml_compatibility": "UNVERIFIED"}


def build_pml_partition_plan(recipe: Mapping[str, Any],
                             receiver_transform: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a reviewable half-space tiling and map for one exact transform."""
    verified = verify_radiation_geometry_v2(recipe)
    geometry = recipe["geometry"]
    if receiver_transform is None:
        receiver_transform = build_case_matrix()[0]["receiver_transform"]
    faces = _face_definitions(receiver_transform,
                              float(geometry["backing_length_um"]),
                              float(geometry["axial_pml_thickness_um"]),
                              float(geometry["transverse_pml_thickness_um"]),
                              float(geometry["transverse_inner_halfwidth_um"]))
    for face in faces:
        face["axis"] = _unit(face["axis"], f"{face['id']} direction")
        face["distance_expression"] = (
            f"({face['axis'][0]:.17g})*(x-({face['backing_pml_interface_um'][0]:.17g}[um]))+"
            f"({face['axis'][1]:.17g})*(y-({face['backing_pml_interface_um'][1]:.17g}[um]))+"
            f"({face['axis'][2]:.17g})*(z-({face['backing_pml_interface_um'][2]:.17g}[um]))")
        face["gradient_norm"] = _norm(face["axis"])
        face["anchor_role"] = "actual_backing_to_PML_interface"
    regions = _active_regions(faces)
    mapping = _validate_mapping_partition(
        regions, faces, float(recipe["pml"]["typical_wavelength_um"]),
        float(recipe["pml"]["factor"]), float(recipe["pml"]["gamma"]))
    partition_volume = _partition_volume_certificate(regions, faces)
    real_map_injectivity = _real_map_injectivity_certificate(
        regions, faces, float(recipe["pml"]["typical_wavelength_um"]),
        float(recipe["pml"]["factor"]), float(recipe["pml"]["gamma"]))
    nonorthogonal_cells = [
        row["region_id"] for row in regions
        if any(abs(value) > 1e-10 for value in row["pairwise_direction_cosines"].values())]
    apertures = {
        role: _port_aperture_polygon(recipe, receiver_transform, faces, role)
        for role in ("input", "output")
    }
    plan = {
        "schema_id": "urn:comsol-mcp:w23:pml-partition-plan:1.0.0",
        "fixture_id": FIXTURE_ID,
        "transform": dict(receiver_transform),
        "faces": faces,
        "regions": regions,
        "interfaces": mapping,
        "port_air_apertures": apertures,
        "domain_map_contract": {
            "cell_count": len(regions),
            "physical_cell_count": sum(not row["active_stretch_ids"] for row in regions),
            "pml_cell_count": sum(bool(row["active_stretch_ids"]) for row in regions),
            "analytic_partition_volume": partition_volume,
            "real_map_injectivity": real_map_injectivity,
            "pml_component_rule": "one distinct native PML feature per listed PML cell after actual connectivity readback",
            "materials": "per-domain native identity readback; preserve intersected core/cladding/air paths through backing and axial PML",
            "interfaces": "each adjacency lists its exact halfspace transition and shared planar polygon vertices",
            "native_entities": "NOT_READ",
        },
        "junction_review": {
            "nonorthogonal_stretch_cells": nonorthogonal_cells,
            "compatibility_status": "UNVERIFIED" if nonorthogonal_cells else "SOFTWARE_ORTHOGONAL_CASE_ONLY_NATIVE_UNVERIFIED",
            "reason": "analytic map continuity and Jacobian do not establish COMSOL's physical correctness for nonorthogonal PML junctions",
        },
        "exclusive_owner_rule": "each positive full-dimensional active-distance cell has exactly one PML node",
        "geometry_partition_status": "SOFTWARE_HALFSPACE_TILING_VALID_NATIVE_ENTITY_IDS_NOT_READ",
        "native_result": "NOT_RUN",
        "recipe_sha256": verified["recipe_sha256"],
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def verify_geometry_clearance(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Check fiber clearance and full Port-plane/backing aperture coverage."""
    verify_radiation_geometry_v2(recipe)
    geometry = recipe["geometry"]
    halfwidth = float(geometry["transverse_inner_halfwidth_um"])
    buffer = float(geometry["minimum_fiber_to_transverse_pml_buffer_um"])
    radius = float(geometry["maximum_cladding_radius_um"])
    backing = float(geometry["backing_length_um"])
    thickness = float(geometry["axial_pml_thickness_um"])
    margins: list[dict[str, Any]] = []
    aperture_cases: list[dict[str, Any]] = []
    for case in build_case_matrix():
        transform = case["receiver_transform"]
        axis = _unit(transform["axis_xyz"], "receiver axis")
        center = [float(v) for v in transform["center_xyz_um"]]
        pivot = [float(v) for v in transform["pivot_xyz_um"]]
        outer = _add(center, _scale(backing + thickness, axis))
        for point_role, point in (("fiber_start", pivot), ("port_plane", center), ("axial_pml_outer", outer)):
            for index, coordinate in ((1, "y"), (2, "z")):
                transverse_radius = radius * math.sqrt(max(0.0, 1.0 - axis[index] ** 2))
                margin = halfwidth - abs(point[index]) - transverse_radius
                margins.append({"case_id": case["case_id"], "point_role": point_role,
                               "coordinate": coordinate, "margin_um": margin})
                if margin < buffer - 1e-9:
                    _fail("worst registered receiver transform collides with the 0.5-um transverse-PML buffer")
        faces = _face_definitions(transform, backing, thickness,
                                  float(geometry["transverse_pml_thickness_um"]), halfwidth)
        aperture_cases.append({
            "case_id": case["case_id"],
            "input": _port_aperture_polygon(recipe, transform, faces, "input"),
            "output": _port_aperture_polygon(recipe, transform, faces, "output"),
        })
    if float(geometry["capture_aperture_radius_um"]) >= float(
            geometry["port_air_aperture"]["minimum_local_square_halfwidth_um"]):
        _fail("core capture aperture must remain separate from the full-air Port aperture")
    sweep_conflicts = [
        {"case_id": case["case_id"], "port_role": role,
         "minimum_full_polygon_transverse_clearance_um": aperture["minimum_full_polygon_transverse_clearance_um"]}
        for case in aperture_cases for role, aperture in case.items() if role != "case_id"
        if aperture["full_polygon_sweep_status"] != "SOFTWARE_FULL_POLYGON_SWEEP_CLEAR"
    ]
    return {"status": ("SOFTWARE_FIBER_CLEARANCE_VALID_PORT_BACKING_SWEEP_CONFLICT"
                       if sweep_conflicts else "SOFTWARE_CLEARANCE_VALID_NATIVE_GEOMETRY_NOT_RUN"),
            "case_count": len(build_case_matrix()),
            "minimum_fiber_to_transverse_pml_margin_um": min(row["margin_um"] for row in margins),
            "required_buffer_um": buffer, "checked_points": margins,
            "port_aperture_case_count": len(aperture_cases),
            "port_aperture_selection": "full plane intersection with actual six-face physical envelope; all air/cladding/core boundary pieces",
            "minimum_local_square_halfwidth_um": geometry["port_air_aperture"]["minimum_local_square_halfwidth_um"],
            "minimum_square_to_physical_halfspace_clearance_um": min(
                aperture["minimum_square_to_physical_halfspace_clearance_um"]
                for case in aperture_cases for role, aperture in case.items() if role != "case_id"),
            "full_polygon_backing_and_axial_pml_sweep_conflicts": sweep_conflicts,
            "port_aperture_cases": aperture_cases,
            "port_backing_material_continuity": "REQUIRED_CORE_CLADDING_AIR_NATIVE_READBACK_NOT_RUN"}


def canonical_radiation_geometry_v2() -> dict[str, Any]:
    """Return the candidate v2 recipe; all lengths are expressed in um."""
    recipe = {
        "schema_id": SCHEMA_ID,
        "schema_version": 2,
        "fixture_id": FIXTURE_ID,
        "supersedes_recipe": "v1-preserved-separately",
        "upstream_fixture_id": V1_FIXTURE_ID,
        "status": "SOFTWARE_CANDIDATE_NATIVE_NOT_RUN",
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
        "geometry": {
            "space_dimension": 3,
            "length_unit": "um",
            "input_port_center_um": [-20.0, 0.0, 0.0],
            "input_propagation_axis_xyz": [1.0, 0.0, 0.0],
            "input_pml_outward_axis_xyz": [-1.0, 0.0, 0.0],
            "receiver_port_center_source": "same right-handed +y then +z rigid-transform oracle as v1",
            "receiver_axis_source": "actual output-fiber transformed local +x axis",
            "transverse_inner_halfwidth_um": 6.0,
            "transverse_outer_halfwidth_um": 8.0,
            "backing_length_um": 1.55,
            "axial_pml_thickness_um": 2.0,
            "transverse_pml_thickness_um": 2.0,
            "minimum_fiber_to_transverse_pml_buffer_um": 0.5,
            "maximum_tilt_deg": 0.5,
            "maximum_translation_component_um": 0.25,
            "maximum_core_radius_um": 1.224,
            "maximum_cladding_radius_um": 2.55,
            "port_air_aperture": {
                "definition": "actual transformed Numeric Port plane intersected with the complete six-halfspace physical-air envelope",
                "minimum_local_square_halfwidth_um": 5.5,
                "selection_membership": "all cross-section boundary pieces across air, cladding, and core; no cladding-radius cutoff",
                "aperture_and_capture_are_distinct": True,
                "axial_extension": "sweep the full Port polygon through 1-lambda homogeneous backing and 2-um axial PML",
                "required_material_identities": ["fiber_core", "fiber_cladding", "air"],
                "transverse_shell_conflicts_must_be_reported": True,
                "native_boundary_and_material_readback": "REQUIRED",
            },
            "capture_aperture_radius_um": 1.224,
            "selection_separation": ["full_air_numeric_port_aperture", "cladding_material", "core_capture"],
            "pml_inner_plane_anchor": "backing/PML interface; never the Port plane",
            "pml_distance_convention": "d=a_out dot (r-p); outward unit vector; 0<=d<=dmax",
            "partition_rule": "arrangement of six outward halfspaces; one active-direction cell per face/edge/corner",
            "control_volume": {
                "status": "GEOMETRY_CANDIDATE_ONLY",
                "box_um": CV_BOX_UM,
                "all_six_faces_required": True,
                "closed_topology_required": True,
                "unique_surface_coverage_required": True,
                "port_entities_forbidden": True,
                "pml_entities_forbidden": True,
                "normal_area_adjacency_material_readback_required": True,
                "signed_power_balance_tolerance": "NOT_FROZEN",
            },
        },
        "materials": {
            "fiber_core": {"backing_and_axial_pml": "same native material identity as propagation segment"},
            "fiber_cladding": {"backing_and_axial_pml": "same native material identity as propagation segment"},
            "aperture_background": {"backing_and_axial_pml": "same homogeneous air material identity"},
            "PML_background": "spatial material assignment remains per core/cladding/air domain, not one all-air mask",
            "native_material_readback": "REQUIRED",
        },
        "ports": {
            "input": {"feature": "portIn3d", "port_number": "1", "type": "Numeric",
                      "slit": 1, "slit_type": "DomainBacked", "plane": "input_port_center_um",
                      "backing_outward_direction": "negative_input_propagation_axis"},
            "output": {"feature": "portOut3d", "port_number": "2", "type": "Numeric",
                       "slit": 1, "slit_type": "DomainBacked", "plane": "transformed receiver section",
                       "backing_outward_direction": "actual_rotated_receiver_axis"},
            "native_setter_and_readback": "REQUIRED; software/XML enum evidence is not runtime acceptance",
        },
        "pml": {
            "ScalingType": "userDefined",
            "stretchingType": "polynomial",
            "factor": 1.0,
            "gamma": 1.0,
            "typical_wavelength_um": 1.55,
            "formula_source": {
                "document": "COMSOL 6.4 PML Implementation doc5132 chunk18251",
                "sha256": "dc141c7bbd5bc2b41ce1524740f6378215df19010259975e62085a46dbb5ac6e",
                "displacement": "Delta=typical_wavelength*f_p(xi)-dmax*xi",
                "polynomial": "f_p(xi)=factor*xi^gamma*(1-i)",
            },
            "multi_direction_rule": "sum independent vector displacements; verify complex map on every shared face and nonzero Jacobian",
            "nonorthogonal_junction_status": "SOFTWARE_ANALYTIC_CHECK_REQUIRED_NATIVE_UNVERIFIED",
            "disconnected_region_rule": "one PML feature per simply connected PML component",
        },
        "acceptance_limits": {
            "absolute_power_balance_tolerance": "NOT_FROZEN",
            "mesh_and_solve_resource_admission": "NOT_FROZEN",
            "native_geometry": "NOT_RUN",
            "native_pml_compatibility": "UNVERIFIED",
            "capture_quadrature_convergence": "NOT_RUN",
            "full_science": "NOT_RUN",
        },
    }
    recipe["recipe_sha256"] = _digest(recipe)
    return recipe


def verify_radiation_geometry_v2(recipe: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, Mapping) or recipe.get("schema_id") != SCHEMA_ID:
        _fail("versioned W23 radiation-geometry v2 schema is required")
    if recipe.get("fixture_id") != FIXTURE_ID or recipe.get("upstream_fixture_id") != V1_FIXTURE_ID:
        _fail("v2 candidate must retain explicit v1 lineage")
    if recipe.get("recipe_sha256") != _digest({k: v for k, v in recipe.items() if k != "recipe_sha256"}):
        _fail("radiation geometry v2 recipe digest mismatch")
    if recipe.get("native_result") != "NOT_RUN" or recipe.get("study_or_solver_invoked") is not False:
        _fail("software recipe verification cannot upgrade native execution")
    geometry, pml, ports = recipe.get("geometry"), recipe.get("pml"), recipe.get("ports")
    if not all(isinstance(value, Mapping) for value in (geometry, pml, ports)):
        _fail("geometry, PML, and port contracts are required")
    if geometry.get("space_dimension") != 3 or geometry.get("length_unit") != "um":
        _fail("only the full 3-D micron fixture is admissible")
    lam = float(pml.get("typical_wavelength_um", math.nan))
    backing = float(geometry.get("backing_length_um", math.nan))
    axial = float(geometry.get("axial_pml_thickness_um", math.nan))
    transverse = float(geometry.get("transverse_pml_thickness_um", math.nan))
    if not all(math.isfinite(x) and x > 0 for x in (lam, backing, axial, transverse)):
        _fail("backing/PML/wavelength dimensions must be finite and positive")
    if abs(lam - 1.55) > 1e-12 or abs(backing - lam) > 1e-12 or abs(axial - 2.0) > 1e-12:
        _fail("the approved 1-lambda backing and 2-um axial PML candidate changed")
    if pml.get("ScalingType") != "userDefined" or pml.get("stretchingType") != "polynomial":
        _fail("v2 requires userDefined PML geometry scaling with the frozen polynomial profile")
    if float(pml.get("factor", math.nan)) != 1.0 or float(pml.get("gamma", math.nan)) != 1.0:
        _fail("software map fixture is frozen to PML factor=gamma=1")
    if geometry.get("pml_inner_plane_anchor") != "backing/PML interface; never the Port plane":
        _fail("signed PML distance must originate at the actual backing/PML interface")
    if geometry.get("pml_distance_convention") != "d=a_out dot (r-p); outward unit vector; 0<=d<=dmax":
        _fail("v2 PML distance must use the actual unit outward direction and bounded signed distance")
    if ports.get("input", {}).get("slit_type") != "DomainBacked" or ports.get("output", {}).get("slit_type") != "DomainBacked":
        _fail("both Numeric Port slits require the explicit DomainBacked candidate setting")
    if ports.get("input", {}).get("slit") != 1 or ports.get("output", {}).get("slit") != 1:
        _fail("both Numeric Port slits must request PortSlit=1")
    expected_aperture = {
        "definition": "actual transformed Numeric Port plane intersected with the complete six-halfspace physical-air envelope",
        "minimum_local_square_halfwidth_um": 5.5,
        "selection_membership": "all cross-section boundary pieces across air, cladding, and core; no cladding-radius cutoff",
        "aperture_and_capture_are_distinct": True,
        "axial_extension": "sweep the full Port polygon through 1-lambda homogeneous backing and 2-um axial PML",
        "required_material_identities": ["fiber_core", "fiber_cladding", "air"],
        "transverse_shell_conflicts_must_be_reported": True,
        "native_boundary_and_material_readback": "REQUIRED",
    }
    if geometry.get("port_air_aperture") != expected_aperture:
        _fail("full physical-air Port aperture and axial material sweep contract changed")
    if geometry.get("selection_separation") != ["full_air_numeric_port_aperture", "cladding_material", "core_capture"]:
        _fail("full Port aperture, cladding, and core capture must remain separate selections")
    cv = geometry.get("control_volume")
    if not isinstance(cv, Mapping) or cv.get("signed_power_balance_tolerance") != "NOT_FROZEN":
        _fail("control-volume geometry is separate from an unfrozen power-balance tolerance")
    if recipe.get("materials", {}).get("native_material_readback") != "REQUIRED":
        _fail("homogeneous backing material continuations require native readback")
    return {"status": "SOFTWARE_RECIPE_VALID_NATIVE_NOT_RUN", "fixture_id": FIXTURE_ID,
            "recipe_sha256": recipe["recipe_sha256"], "native_result": "NOT_RUN",
            "power_balance_tolerance": "NOT_FROZEN", "pml_junction_native_status": "UNVERIFIED"}


def verify_pml_region_assignment(plan: Mapping[str, Any], readback: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exclusive domain/node ownership and exact indexed PML readback."""
    if not isinstance(plan, Mapping) or not isinstance(readback, Mapping):
        _fail("PML partition plan and readback are required")
    if (plan.get("schema_id") != "urn:comsol-mcp:w23:pml-partition-plan:1.0.0"
            or plan.get("fixture_id") != FIXTURE_ID
            or plan.get("recipe_sha256") != canonical_radiation_geometry_v2()["recipe_sha256"]
            or plan.get("plan_sha256") != _digest({k: v for k, v in plan.items() if k != "plan_sha256"})):
        _fail("PML partition plan identity or digest is invalid")
    expected_plan = build_pml_partition_plan(canonical_radiation_geometry_v2(), plan.get("transform"))
    if expected_plan.get("plan_sha256") != plan.get("plan_sha256"):
        _fail("PML partition cells or shared-interface map differ from the canonical transform plan")
    if readback.get("schema_id") != "urn:comsol-mcp:w23:pml-native-readback:1.0.0":
        _fail("versioned PML native readback schema is required")
    if readback.get("ScalingType") != "userDefined" or readback.get("stretchingType") != "polynomial":
        _fail("actual PML node must read back userDefined geometry scaling and polynomial stretch")
    if (float(readback.get("PMLfactor", math.nan)) != 1.0
            or float(readback.get("PMLgamma", math.nan)) != 1.0
            or abs(float(readback.get("typical_wavelength_um", math.nan)) - 1.55) > 1e-12):
        _fail("actual PML profile differs from the frozen factor/gamma/wavelength contract")
    expected_regions = {row["region_id"]: row for row in plan.get("regions", [])
                        if row["active_stretch_ids"]}
    actual_regions = readback.get("regions")
    if (not isinstance(actual_regions, list) or len(actual_regions) != len(expected_regions)
            or any(not isinstance(row, Mapping) for row in actual_regions)
            or {row.get("region_id") for row in actual_regions} != set(expected_regions)):
        _fail("PML region readback is missing or adds a face/edge/corner cell")
    all_seen: list[int] = []
    tags: set[str] = set()
    for row in actual_regions:
        region_id = row["region_id"]
        expected = expected_regions[region_id]
        if row.get("active_stretch_ids") != expected["active_stretch_ids"]:
            _fail("PML region does not bind the exact active face/edge/corner stretch set")
        ids = row.get("domain_ids")
        if not isinstance(ids, list) or not ids or any(type(x) is not int or x < 1 for x in ids):
            _fail("each PML cell must read back nonempty positive domain IDs")
        if len(ids) != len(set(ids)):
            _fail("PML cell domain IDs contain duplicates")
        all_seen.extend(ids)
        tag = row.get("node_tag")
        if not isinstance(tag, str) or not tag or tag in tags:
            _fail("each simply connected PML region must have its own exact feature tag")
        tags.add(tag)
        if row.get("connected_component_count") != 1:
            _fail("a single PML feature cannot own multiple disconnected components")
        directions = row.get("directions")
        if not isinstance(directions, list) or len(directions) != len(expected["active_stretch_ids"]):
            _fail("actual PML direction count does not match this partition cell")
        expected_by_id = {face["id"]: face for face in plan["faces"]}
        for index, (actual, face_id) in enumerate(zip(directions, expected["active_stretch_ids"])):
            face = expected_by_id[face_id]
            if (actual.get("index") != index or actual.get("face_id") != face_id
                    or actual.get("distance_expression") != face["distance_expression"]):
                _fail("indexed PML distance expression is detached from the approved anchor/frame")
            if actual.get("anchor_point_um") != face["backing_pml_interface_um"]:
                _fail("indexed PML distance anchor is not the backing/PML interface")
            if abs(float(actual.get("dmax_um", math.nan)) - float(face["dmax_um"])) > 1e-10:
                _fail("indexed PML dmax differs from the geometric layer thickness")
            gradient = _unit(actual.get("gradient_xyz", []), "actual PML distance gradient")
            if (_norm([a - b for a, b in zip(gradient, face["axis"])]) > 1e-10
                    or abs(_norm(actual.get("gradient_xyz", [])) - 1.0) > 1e-10):
                _fail("actual PML distance gradient must equal the registered unit outward direction")
        material_paths = row.get("material_paths")
        if not isinstance(material_paths, list) or not material_paths:
            _fail("PML cell must read back its actual material continuation membership")
        active = set(expected["active_stretch_ids"])
        axial_only = active in ({"input_axial"}, {"output_axial"})
        expected_roles = {"core", "cladding", "air"} if axial_only else {"air"}
        roles = {material.get("material_role") for material in material_paths
                 if isinstance(material, Mapping)}
        if roles != expected_roles or len(material_paths) != len(expected_roles):
            _fail("PML material-domain roles do not match the axial cap or outer air cell")
        material_domain_ids: list[int] = []
        for material in material_paths:
            if not isinstance(material, Mapping):
                _fail("PML material path record is malformed")
            if (material.get("material_role") not in {"core", "cladding", "air"}
                    or material.get("same_native_material_identity") is not True
                    or not isinstance(material.get("native_material_tag"), str)
                    or not material.get("native_material_tag")):
                _fail("backing/PML material identity changes or is not read back")
            material_ids = material.get("domain_ids")
            if (not isinstance(material_ids, list) or not material_ids
                    or any(type(value) is not int or value not in ids for value in material_ids)):
                _fail("each material identity must bind actual domain IDs within its exact PML cell")
            material_domain_ids.extend(material_ids)
            if axial_only or material.get("material_role") in {"core", "cladding"}:
                expected_segments = ["propagation", "backing", "axial_pml"]
                if material.get("segments") != expected_segments:
                    _fail("fiber material identity is not continuous through propagation, backing, and axial PML")
            else:
                expected_segments = (["air", "axial_pml", "transverse_pml"]
                                     if any(face_id in active for face_id in {"input_axial", "output_axial"})
                                     else ["air", "transverse_pml"])
                if material.get("segments") != expected_segments:
                    _fail("outer PML air-domain path does not match its active axial/transverse directions")
            segment_tags = material.get("material_tags_by_segment")
            if (not isinstance(segment_tags, Mapping)
                    or list(segment_tags.keys()) != expected_segments
                    or any(tag != material["native_material_tag"] for tag in segment_tags.values())):
                _fail("material continuity must be proven by the same native material tag at each segment")
        if Counter(material_domain_ids) != Counter(ids):
            _fail("PML material identities do not partition the cell domains exactly once")
    domain_ids = readback.get("pml_domain_ids")
    if not isinstance(domain_ids, list) or any(type(x) is not int or x < 1 for x in domain_ids):
        _fail("actual total PML domain selection is required")
    if len(domain_ids) != len(set(domain_ids)) or Counter(all_seen) != Counter(domain_ids):
        _fail("PML cell ownership has a duplicate domain, uncovered strip, or foreign domain")
    mapping = readback.get("complex_mapping")
    if not isinstance(mapping, Mapping) or mapping.get("evidence_scope") not in {
            "SOFTWARE_ANALYTIC_FIXTURE", "COMSOL_NATIVE_GEOMETRY_READBACK"}:
        _fail("full complex-coordinate seam/Jacobian evidence is required")
    if mapping.get("maximum_shared_face_jump_um", math.inf) > 1e-7:
        _fail("complex PML map is discontinuous at a shared face")
    if mapping.get("minimum_abs_pml_jacobian_determinant", 0.0) <= 1e-10:
        _fail("complex PML map Jacobian is singular/degenerate")
    if (mapping.get("partition_plan_sha256") != plan["plan_sha256"]
            or mapping.get("shared_interface_count") != plan["interfaces"]["seam_count"]
            or mapping.get("nonorthogonal_comsol_pml_compatibility") != "UNVERIFIED"):
        _fail("complex mapping evidence is detached from this plan or overstates junction compatibility")
    expected_mapping = plan["interfaces"]
    if (abs(float(mapping.get("maximum_shared_face_jump_um", math.inf))
            - expected_mapping["max_complex_coordinate_jump_um"]) > 1e-12
            or abs(float(mapping.get("minimum_abs_pml_jacobian_determinant", 0.0))
                   - expected_mapping["minimum_abs_pml_jacobian_determinant"]) > 1e-10):
        _fail("reported complex-coordinate seam/Jacobian summary differs from recomputation")
    status = ("SOFTWARE_PML_PARTITION_CONTRACT_VALID_NATIVE_NOT_RUN"
              if mapping.get("evidence_scope") == "SOFTWARE_ANALYTIC_FIXTURE"
              else "NATIVE_GEOMETRY_READBACK_CONTRACT_VALID_COMPATIBILITY_STILL_REQUIRES_REVIEW")
    return {"status": status, "native_result": "NOT_RUN" if status.startswith("SOFTWARE") else "UNVERIFIED",
            "pml_region_count": len(actual_regions), "pml_domain_count": len(domain_ids),
            "pml_node_count": len(tags), "complex_mapping": dict(mapping)}


def control_volume_face_contract() -> list[dict[str, Any]]:
    x0, x1 = CV_BOX_UM["x"]
    y0, y1 = CV_BOX_UM["y"]
    z0, z1 = CV_BOX_UM["z"]
    return [
        {"role": "x_minus", "normal": [-1.0, 0.0, 0.0], "point_um": [x0, 0.0, 0.0], "area_um2": (y1-y0)*(z1-z0)},
        {"role": "x_plus", "normal": [1.0, 0.0, 0.0], "point_um": [x1, 0.0, 0.0], "area_um2": (y1-y0)*(z1-z0)},
        {"role": "y_minus", "normal": [0.0, -1.0, 0.0], "point_um": [0.0, y0, 0.0], "area_um2": (x1-x0)*(z1-z0)},
        {"role": "y_plus", "normal": [0.0, 1.0, 0.0], "point_um": [0.0, y1, 0.0], "area_um2": (x1-x0)*(z1-z0)},
        {"role": "z_minus", "normal": [0.0, 0.0, -1.0], "point_um": [0.0, 0.0, z0], "area_um2": (x1-x0)*(y1-y0)},
        {"role": "z_plus", "normal": [0.0, 0.0, 1.0], "point_um": [0.0, 0.0, z1], "area_um2": (x1-x0)*(y1-y0)},
    ]


def normalize_control_volume_topology_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    """Group Java flat boundary rows and derive outward orientation from the CV cut.

    The Java producer reports one record per boundary entity, the raw face
    normal, edge loops in that raw orientation, and adjacent-domain bounding
    boxes. This adapter groups patches by their actual registered plane and
    identifies the CV-interior adjacent domain by its registered-box-bounded
    native bbox and verifies the two native adjacency orientations are
    opposed. An exterior neighbor's bbox may wrap around the finite cut and
    cross the plane, so it is never classified by a one-sided bbox test.
    Raw normals and loops are retained for audit.
    """
    if not isinstance(readback, Mapping) or not isinstance(readback.get("faces"), list):
        _fail("flat Java control-volume face rows are required for normalization")
    flat_rows = readback["faces"]
    if not flat_rows or any(not isinstance(row, Mapping) or "patches" in row for row in flat_rows):
        _fail("control-volume normalization expects nonempty flat boundary rows")
    expected = {row["role"]: row for row in control_volume_face_contract()}
    groups: dict[str, list[dict[str, Any]]] = {role: [] for role in expected}
    seen_boundary_ids: set[int] = set()

    for raw in flat_rows:
        boundary_id = raw.get("boundary_id")
        if type(boundary_id) is not int or boundary_id < 1 or boundary_id in seen_boundary_ids:
            _fail("flat Java control-volume rows require unique positive boundary IDs")
        seen_boundary_ids.add(boundary_id)
        vertices = raw.get("vertex_coordinates_um")
        loops = raw.get("edge_loops")
        representative = raw.get("face_parameter_sample_point_um")
        if (not isinstance(vertices, list) or not vertices or not isinstance(loops, list) or not loops
                or not isinstance(representative, list) or len(representative) != 3):
            _fail("flat Java boundary row lacks vertex, loop, or face-parameter sample readback")
        points: list[list[float]] = []
        for vertex in vertices:
            point = vertex.get("point_um") if isinstance(vertex, Mapping) else None
            if (not isinstance(point, list) or len(point) != 3
                    or not all(math.isfinite(float(value)) for value in point)):
                _fail("flat Java boundary row contains malformed vertex coordinates")
            points.append([float(value) for value in point])
        if not all(math.isfinite(float(value)) for value in representative):
            _fail("flat Java face-parameter sample is nonfinite")
        points.append([float(value) for value in representative])
        for loop in loops:
            if not isinstance(loop, list) or not loop:
                _fail("flat Java boundary row contains an empty edge loop")
            for edge in loop:
                samples = edge.get("sample_points_um") if isinstance(edge, Mapping) else None
                if not isinstance(samples, list) or len(samples) < 2:
                    _fail("flat Java boundary row lacks resolved edge-curve samples")
                for point in samples:
                    if (not isinstance(point, list) or len(point) != 3
                            or not all(math.isfinite(float(value)) for value in point)):
                        _fail("flat Java edge-curve sample is malformed")
                    points.append([float(value) for value in point])

        matching_roles = []
        for role, contract in expected.items():
            axis = next(index for index, value in enumerate(contract["normal"]) if value)
            plane = float(contract["point_um"][axis])
            if all(abs(point[axis] - plane) <= 1e-7 for point in points):
                matching_roles.append(role)
        if len(matching_roles) != 1:
            _fail("flat Java boundary row does not lie on exactly one registered control-volume face plane")
        role = matching_roles[0]
        contract = expected[role]
        axis = next(index for index, value in enumerate(contract["normal"]) if value)
        plane = float(contract["point_um"][axis])

        raw_normal = _unit(raw.get("raw_face_normal_xyz", []), "raw Java control-volume face normal")
        outward = [float(value) for value in contract["normal"]]
        normal_dot = _dot(raw_normal, outward)
        if abs(abs(normal_dot) - 1.0) > 1e-7:
            _fail("raw Java face normal is not parallel to the registered planar face")

        adjacent = raw.get("adjacent_domain_ids")
        adjacent_orientations = raw.get("adjacent_domain_orientation")
        boxes = raw.get("adjacent_domain_bbox_um_by_id")
        if (not isinstance(adjacent, list) or len(adjacent) != 2
                or any(type(value) is not int or value < 1 for value in adjacent)
                or len(set(adjacent)) != 2 or not isinstance(boxes, Mapping)
                or not isinstance(adjacent_orientations, list) or len(adjacent_orientations) != 2
                or any(type(value) is not int or value not in (-1, 1) for value in adjacent_orientations)
                or sum(adjacent_orientations) != 0):
            _fail("flat Java row requires two adjacent domains, opposite getAdjOrient readbacks, and actual bounds")

        side_ids: dict[str, int] = {}
        normal_sign = float(outward[axis])
        cv_box = [CV_BOX_UM[name] for name in ("x", "y", "z")]
        for domain_id in adjacent:
            bbox = boxes.get(str(domain_id), boxes.get(domain_id))
            if (not isinstance(bbox, list) or len(bbox) != 6
                    or not all(math.isfinite(float(value)) for value in bbox)):
                _fail("each adjacent domain requires a finite six-value native bounding box")
            bbox_values = [float(value) for value in bbox]
            if any(bbox_values[2 * index] > bbox_values[2 * index + 1] for index in range(3)):
                _fail("adjacent domain bounding box has reversed coordinate limits")
            for point in points:
                if any(point[index] < bbox_values[2 * index] - 1e-7
                       or point[index] > bbox_values[2 * index + 1] + 1e-7
                       for index in range(3)):
                    _fail("registered boundary samples are outside an adjacent domain's native bounding box")
            lo = bbox_values[2 * axis]
            hi = bbox_values[2 * axis + 1]
            signed_bounds = sorted((normal_sign * (lo - plane), normal_sign * (hi - plane)))
            bbox_inside_registered_cv = all(
                bbox_values[2 * index] >= cv_box[index][0] - 1e-7
                and bbox_values[2 * index + 1] <= cv_box[index][1] + 1e-7
                for index in range(3))
            touches_registered_plane = (abs(signed_bounds[0]) <= 1e-7
                                        or abs(signed_bounds[1]) <= 1e-7)
            lies_inside_halfspace = signed_bounds[1] <= 1e-7 and signed_bounds[0] < -1e-7
            if bbox_inside_registered_cv and touches_registered_plane and lies_inside_halfspace:
                side_ids.setdefault("inside", domain_id)
            else:
                side_ids.setdefault("outside", domain_id)
        if (set(side_ids) != {"inside", "outside"}
                or len(adjacent) != len(set(side_ids.values()))):
            _fail("registered-box interior domain and its distinct exterior neighbor are not uniquely determined")

        patch = copy.deepcopy(dict(raw))
        patch["face_sample_point_um"] = [float(value) for value in representative]
        patch["raw_face_normal_xyz"] = raw_normal
        patch["raw_normal_to_outward_dot"] = normal_dot
        patch["outward_normal_xyz"] = outward
        patch["adjacent_domain_side_ids"] = side_ids
        patch["adjacent_domain_orientation_by_id"] = {
            str(domain_id): orientation
            for domain_id, orientation in zip(adjacent, adjacent_orientations)}
        patch["normal_orientation_correction"] = (
            "RAW_FACE_ORIENTATION_ALREADY_OUTWARD" if normal_dot > 0
            else "EDGE_LOOPS_REVERSED_TO_INSIDE_TO_OUTSIDE")
        patch["raw_edge_loops"] = copy.deepcopy(loops)
        if normal_dot < 0:
            corrected_loops = []
            for loop in loops:
                corrected_loop = []
                for edge in reversed(loop):
                    item = copy.deepcopy(edge)
                    item["start_vertex_id"], item["end_vertex_id"] = (
                        item["end_vertex_id"], item["start_vertex_id"])
                    item["orientation"] = -int(item["orientation"])
                    item["sample_points_um"] = list(reversed(item["sample_points_um"]))
                    corrected_loop.append(item)
                corrected_loops.append(corrected_loop)
            patch["edge_loops"] = corrected_loops
        groups[role].append(patch)
    if any(not rows for rows in groups.values()):
        _fail("flat Java rows do not provide at least one patch on all six registered CV planes")
    normalized = copy.deepcopy(dict(readback))
    normalized["faces"] = [
        {"role": role, "patches": sorted(rows, key=lambda row: row["boundary_id"])}
        for role, rows in groups.items()
    ]
    normalized["flat_face_normalization"] = {
        "status": "SOFTWARE_GROUPED_FROM_FLAT_JAVA_ROWS",
        "method": "registered_plane_plus_interior_domain_bbox_and_getAdjOrient_pair",
        "source_flat_face_rows_sha256": _digest(flat_rows),
        "raw_face_normals_and_edge_loops_preserved": True,
        "patch_orientation_audit": [
            {
                "role": role,
                "boundary_id": patch["boundary_id"],
                "raw_face_normal_xyz": patch["raw_face_normal_xyz"],
                "raw_normal_to_registered_outward_dot": patch["raw_normal_to_outward_dot"],
                "registered_outward_normal_xyz": patch["outward_normal_xyz"],
                "normal_orientation_correction": patch["normal_orientation_correction"],
                "adjacent_domain_side_ids": patch["adjacent_domain_side_ids"],
                "adjacent_domain_orientation_by_id": patch["adjacent_domain_orientation_by_id"],
            }
            for role, patches in groups.items() for patch in patches
        ],
        "native_surface_integral_of_one": "NOT_RUN",
    }
    return normalized


def _cv_polygon_area_2d(points: Sequence[Sequence[float]]) -> float:
    return 0.5 * sum(points[index][0] * points[(index + 1) % len(points)][1]
                     - points[(index + 1) % len(points)][0] * points[index][1]
                     for index in range(len(points)))


def _cv_point_on_segment_2d(point: Sequence[float], start: Sequence[float],
                            end: Sequence[float], tol: float = 1e-9) -> bool:
    dx, dy = end[0] - start[0], end[1] - start[1]
    px, py = point[0] - start[0], point[1] - start[1]
    cross = dx * py - dy * px
    if abs(cross) > tol * max(1.0, math.hypot(dx, dy)):
        return False
    return (min(start[0], end[0]) - tol <= point[0] <= max(start[0], end[0]) + tol
            and min(start[1], end[1]) - tol <= point[1] <= max(start[1], end[1]) + tol)


def _cv_point_in_polygon_2d(point: Sequence[float], polygon: Sequence[Sequence[float]],
                            strict: bool = False) -> bool:
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _cv_point_on_segment_2d(point, start, end):
            return not strict
        if (start[1] > point[1]) != (end[1] > point[1]):
            cross_x = start[0] + (point[1] - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
            if point[0] < cross_x:
                inside = not inside
    return inside


def _cv_segments_intersect_2d(a: Sequence[float], b: Sequence[float],
                              c: Sequence[float], d: Sequence[float],
                              tol: float = 1e-9) -> bool:
    def orient(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    ab_c, ab_d = orient(a, b, c), orient(a, b, d)
    cd_a, cd_b = orient(c, d, a), orient(c, d, b)
    if ab_c * ab_d < -tol * tol and cd_a * cd_b < -tol * tol:
        return True
    return (abs(ab_c) <= tol and _cv_point_on_segment_2d(c, a, b, tol)
            or abs(ab_d) <= tol and _cv_point_on_segment_2d(d, a, b, tol)
            or abs(cd_a) <= tol and _cv_point_on_segment_2d(a, c, d, tol)
            or abs(cd_b) <= tol and _cv_point_on_segment_2d(b, c, d, tol))


def _cv_polygon_is_simple_2d(points: Sequence[Sequence[float]]) -> bool:
    if len(points) < 3:
        return False
    segment_count = len(points)
    for first in range(segment_count):
        a, b = points[first], points[(first + 1) % segment_count]
        for second in range(first + 1, segment_count):
            if second == first + 1 or (first == 0 and second == segment_count - 1):
                continue
            c, d = points[second], points[(second + 1) % segment_count]
            if _cv_segments_intersect_2d(a, b, c, d):
                return False
    return True


def verify_control_volume_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    """Require a unique, oriented, internal six-face surface with edge closure."""
    if (isinstance(readback, Mapping) and isinstance(readback.get("faces"), list)
            and readback["faces"] and any("patches" not in row for row in readback["faces"]
                                            if isinstance(row, Mapping))):
        readback = normalize_control_volume_topology_readback(readback)
    if not isinstance(readback, Mapping) or readback.get("schema_id") != "urn:comsol-mcp:w23:control-volume-topology:1.0.0":
        _fail("versioned control-volume topology readback is required")
    if (readback.get("coordinate_unit") != "um"
            or readback.get("geometry_length_unit") != "um"
            or readback.get("geometry_length_unit_readback") is not True):
        _fail("control-volume geometry unit must be read back from the actual geometry")
    evidence_scope = readback.get("evidence_scope")
    if evidence_scope not in {"SOFTWARE_FIXTURE", "COMSOL_NATIVE_PARTITION_DOMAINS"}:
        _fail("control-volume evidence scope must be explicitly software or native PartitionDomains readback")
    if evidence_scope == "COMSOL_NATIVE_PARTITION_DOMAINS" and (
            readback.get("topology_source") != "GeomInfo.getAdj/getAdjOrient+GeomMeasure"
            or readback.get("partition_feature_type") != "PartitionDomains"
            or readback.get("actual_partition_feature_readback") is not True):
        _fail("native control volume must bind actual PartitionDomains and geometry topology API readback")
    raw_faces = readback.get("faces")
    expected = {row["role"]: row for row in control_volume_face_contract()}
    if (not isinstance(raw_faces, list) or len(raw_faces) != len(expected)
            or any(not isinstance(row, Mapping) for row in raw_faces)
            or {row.get("role") for row in raw_faces} != set(expected)):
        _fail("all six unique control-volume faces are required")
    all_entities: list[int] = []
    global_vertices: dict[int, list[float]] = {}
    vertex_roles: dict[int, set[str]] = defaultdict(set)
    edge_uses: dict[int, list[dict[str, Any]]] = defaultdict(list)
    role_patch_ids: dict[str, set[int]] = defaultdict(set)
    role_vertex_ids: dict[str, set[int]] = defaultdict(set)
    role_edge_ids: dict[str, set[int]] = defaultdict(set)
    patch_hole_count: dict[int, int] = {}
    patch_euler_characteristic: dict[int, int] = {}
    patch_net_sampled_area_um2: dict[int, float] = {}
    for face in raw_faces:
        role = face["role"]
        contract = expected[role]
        axis = next(i for i, value in enumerate(contract["normal"]) if value)
        pieces = face.get("patches")
        if not isinstance(pieces, list) or not pieces:
            _fail("each control-volume face requires actual boundary patches")
        for patch in pieces:
            entity = patch.get("boundary_id")
            if type(entity) is not int or entity < 1:
                _fail("control-volume boundary entities must be positive native IDs")
            all_entities.append(entity)
            role_patch_ids[role].add(entity)
            normal = _unit(patch.get("outward_normal_xyz", []), "native control-volume normal")
            if _dot(normal, contract["normal"]) < 1.0 - 1e-8:
                _fail("observed surface normal is not the requested outward control-volume normal")
            face_point = patch.get("face_sample_point_um", patch.get("centroid_um"))
            if not isinstance(face_point, list) or len(face_point) != 3 or not all(math.isfinite(float(v)) for v in face_point):
                _fail("control-volume face patch requires a finite native face point")
            if abs(float(face_point[axis]) - float(contract["point_um"][axis])) > 1e-7:
                _fail("native surface patch is not on its registered planar control-volume face")
            if any(float(face_point[i]) < CV_BOX_UM[axis_name][0] - 1e-7
                   or float(face_point[i]) > CV_BOX_UM[axis_name][1] + 1e-7
                   for i, axis_name in enumerate(("x", "y", "z"))):
                _fail("native control-volume patch centroid lies outside the registered box")
            vertex_map_raw = patch.get("vertex_coordinates_um")
            edge_loops = patch.get("edge_loops")
            if not isinstance(vertex_map_raw, list) or not isinstance(edge_loops, list) or not edge_loops:
                _fail("control-volume patch requires native edge loops and endpoint-vertex coordinates")
            vertex_map: dict[int, list[float]] = {}
            for vertex in vertex_map_raw:
                if (not isinstance(vertex, Mapping) or type(vertex.get("vertex_id")) is not int
                        or vertex["vertex_id"] < 1):
                    _fail("native control-volume vertex identity is malformed")
                point = vertex.get("point_um")
                if (not isinstance(point, list) or len(point) != 3
                        or not all(math.isfinite(float(value)) for value in point)):
                    _fail("native control-volume vertex coordinate is malformed")
                if vertex["vertex_id"] in vertex_map:
                    _fail("native control-volume vertex identity is duplicated")
                point_values = [float(value) for value in point]
                vertex_id = vertex["vertex_id"]
                prior = global_vertices.get(vertex_id)
                if prior is not None and _norm([point_values[i] - prior[i] for i in range(3)]) > 1e-7:
                    _fail("the same native vertex ID has inconsistent coordinates across control-volume patches")
                global_vertices[vertex_id] = point_values
                vertex_roles[vertex_id].add(role)
                role_vertex_ids[role].add(vertex_id)
                vertex_map[vertex_id] = point_values
            if not vertex_map:
                _fail("native control-volume patch has no endpoint vertices")
            for vertex_point in vertex_map.values():
                if abs(vertex_point[axis] - float(contract["point_um"][axis])) > 1e-7:
                    _fail("control-volume edge endpoint is not on its registered plane")
                if any(vertex_point[i] < CV_BOX_UM[axis_name][0] - 1e-7
                       or vertex_point[i] > CV_BOX_UM[axis_name][1] + 1e-7
                       for i, axis_name in enumerate(("x", "y", "z"))):
                    _fail("control-volume edge endpoint lies outside the registered box")
            adjacent = patch.get("adjacent_domain_ids")
            materials = patch.get("adjacent_material_tags")
            if not isinstance(adjacent, list) or len(adjacent) != 2 or len(set(adjacent)) != 2:
                _fail("each internal control-volume face patch must have two distinct adjacent domains")
            if not isinstance(materials, list) or len(materials) != 2 or not all(isinstance(v, str) and v for v in materials):
                _fail("material membership must be read back for both adjacent domains")
            if patch.get("touches_pml") is not False or patch.get("touches_port") is not False:
                _fail("control-volume surface must exclude all PML and Port entities")
            loop_geometry: list[dict[str, Any]] = []
            patch_loop_edges: set[int] = set()
            patch_loop_vertices: set[int] = set()
            u_axis, v_axis = (axis + 1) % 3, (axis + 2) % 3
            for loop in edge_loops:
                if not isinstance(loop, list) or not loop:
                    _fail("control-volume edge loop is empty or malformed")
                oriented_edges = []
                for item in loop:
                    if (not isinstance(item, Mapping) or type(item.get("edge_id")) is not int
                            or item["edge_id"] < 1 or type(item.get("orientation")) is not int
                            or item.get("orientation") not in (-1, 1)
                            or type(item.get("start_vertex_id")) is not int
                            or type(item.get("end_vertex_id")) is not int
                            or item["start_vertex_id"] not in vertex_map
                            or item["end_vertex_id"] not in vertex_map):
                        _fail("closed topology requires exact oriented edge endpoints and +/- orientation")
                    if item["start_vertex_id"] == item["end_vertex_id"]:
                        if len(loop) != 1:
                            _fail("a closed one-edge boundary loop cannot be mixed with other edges")
                    if item["edge_id"] in patch_loop_edges:
                        _fail("one native edge cannot occur in multiple loops of the same patch")
                    patch_loop_edges.add(item["edge_id"])
                    patch_loop_vertices.update((item["start_vertex_id"], item["end_vertex_id"]))
                    oriented_edges.append(item)
                if len(oriented_edges) > 1:
                    for current, following in zip(oriented_edges, oriented_edges[1:] + oriented_edges[:1]):
                        if current["end_vertex_id"] != following["start_vertex_id"]:
                            _fail("actual face edge ordering does not form a closed vertex loop")
                for item in oriented_edges:
                    edge_samples = item.get("sample_points_um")
                    if not isinstance(edge_samples, list) or len(edge_samples) < 2:
                        _fail("each actual boundary edge requires sampled geometry coordinates")
                    if item.get("sample_order") != "face_orientation":
                        _fail("edge samples must preserve actual face-orientation order")
                    for sample in edge_samples:
                        if (not isinstance(sample, list) or len(sample) != 3
                                or not all(math.isfinite(float(value)) for value in sample)):
                            _fail("actual edge sample coordinate is malformed")
                        if abs(float(sample[axis]) - float(contract["point_um"][axis])) > 1e-7:
                            _fail("actual edge sample is not planar on its registered control-volume face")
                        if any(float(sample[i]) < CV_BOX_UM[axis_name][0] - 1e-7
                               or float(sample[i]) > CV_BOX_UM[axis_name][1] + 1e-7
                               for i, axis_name in enumerate(("x", "y", "z"))):
                            _fail("actual edge sample lies outside the registered control-volume box")
                    start_point = vertex_map[item["start_vertex_id"]]
                    end_point = vertex_map[item["end_vertex_id"]]
                    if (_norm([float(edge_samples[0][i]) - start_point[i] for i in range(3)]) > 1e-7
                            or _norm([float(edge_samples[-1][i]) - end_point[i] for i in range(3)]) > 1e-7):
                        _fail("edge curve samples are detached from their actual oriented endpoint vertices")
                    edge_uses[item["edge_id"]].append({
                        "role": role,
                        "boundary_id": entity,
                        "orientation": item["orientation"],
                        "start_vertex_id": item["start_vertex_id"],
                        "end_vertex_id": item["end_vertex_id"],
                        "sample_points_um": [[float(value) for value in sample]
                                             for sample in edge_samples],
                    })
                    role_edge_ids[role].add(item["edge_id"])
                # Preserve each loop's projected polygon. Outer and inner
                # loops have opposite orientation under the surface normal.
                polygon: list[list[float]] = []
                for item in oriented_edges:
                    samples = item["sample_points_um"]
                    polygon.extend([[float(value) for value in sample] for sample in samples[:-1]])
                if len(polygon) < 3:
                    _fail("control-volume face patch loop has fewer than three geometric vertices")
                projected = [[point[u_axis], point[v_axis]] for point in polygon]
                if not _cv_polygon_is_simple_2d(projected):
                    _fail("control-volume patch loop self-intersects in the registered face plane")
                signed_area = _cv_polygon_area_2d(projected)
                if not math.isfinite(signed_area) or abs(signed_area) <= 1e-12:
                    _fail("control-volume face patch loop is geometrically degenerate")
                loop_geometry.append({"points": projected, "signed_area_um2": signed_area})
            if len(patch_loop_vertices) != sum(
                    len({item["start_vertex_id"] for item in loop}) for loop in edge_loops):
                _fail("separate outer and hole loops of one patch must have distinct vertex identities")
            areas = [abs(row["signed_area_um2"]) for row in loop_geometry]
            outer_index = max(range(len(areas)), key=areas.__getitem__)
            outer_area = areas[outer_index]
            if sum(1 for area in areas if abs(area - outer_area) <= 1e-10) != 1:
                _fail("control-volume patch outer loop is ambiguous among equal-area loops")
            normal_sign = 1.0 if float(contract["normal"][axis]) > 0 else -1.0
            if loop_geometry[outer_index]["signed_area_um2"] * normal_sign <= 0:
                _fail("control-volume patch outer loop opposes the observed outward normal")
            hole_areas = []
            outer_polygon = loop_geometry[outer_index]["points"]
            hole_polygons = []
            for index, loop_row in enumerate(loop_geometry):
                if index == outer_index:
                    continue
                hole_polygon = loop_row["points"]
                if loop_row["signed_area_um2"] * normal_sign >= 0:
                    _fail("control-volume patch hole loop must wind opposite its outer loop")
                if abs(loop_row["signed_area_um2"]) >= outer_area - 1e-10:
                    _fail("control-volume patch hole must be smaller than and enclosed by its outer loop")
                if not all(_cv_point_in_polygon_2d(point, outer_polygon, strict=True)
                           for point in hole_polygon):
                    _fail("control-volume patch hole loop is not strictly contained by its outer loop")
                for other_hole in hole_polygons:
                    if (any(_cv_point_in_polygon_2d(point, other_hole, strict=True) for point in hole_polygon)
                            or any(_cv_point_in_polygon_2d(point, hole_polygon, strict=True) for point in other_hole)):
                        _fail("control-volume patch hole loops overlap or nest")
                    if any(_cv_segments_intersect_2d(hole_polygon[i], hole_polygon[(i + 1) % len(hole_polygon)],
                                                     other_hole[j], other_hole[(j + 1) % len(other_hole)])
                           for i in range(len(hole_polygon)) for j in range(len(other_hole))):
                        _fail("control-volume patch hole loops intersect")
                hole_polygons.append(hole_polygon)
                hole_areas.append(abs(loop_row["signed_area_um2"]))
            net_area = outer_area - sum(hole_areas)
            if net_area <= 1e-12:
                _fail("control-volume patch hole area consumes its outer loop")
            patch_hole_count[entity] = len(hole_areas)
            patch_euler_characteristic[entity] = 1 - len(hole_areas)
            patch_net_sampled_area_um2[entity] = net_area
    if len(all_entities) != len(set(all_entities)):
        _fail("a control-volume boundary entity was multiply claimed")

    if evidence_scope == "COMSOL_NATIVE_PARTITION_DOMAINS":
        source_faces = readback.get("partition_source_face_ids")
        target_domains = readback.get("partition_target_domain_ids")
        native_boundary_ids = readback.get("boundary_ids")
        for values, label, expected_count in ((source_faces, "PartitionDomains source face IDs", 6),
                                              (target_domains, "PartitionDomains target domain IDs", None),
                                              (native_boundary_ids, "native control-volume boundary IDs", None)):
            if (not isinstance(values, list) or not values
                    or any(type(value) is not int or value < 1 for value in values)
                    or len(values) != len(set(values))
                    or (expected_count is not None and len(values) != expected_count)):
                _fail(f"{label} must be present as unique positive native IDs")
        if set(native_boundary_ids) != set(all_entities):
            _fail("native boundary ID inventory differs from the actual control-volume face patches")

    if not edge_uses:
        _fail("control-volume surface has no actual edge topology")
    for edge_id, uses in edge_uses.items():
        if len(uses) != 2 or uses[0]["orientation"] + uses[1]["orientation"] != 0:
            _fail("each actual control-volume edge must have two opposite oriented face incidences")
        left, right = uses
        if (left["start_vertex_id"] != right["end_vertex_id"]
                or left["end_vertex_id"] != right["start_vertex_id"]):
            _fail("a shared native edge ID has inconsistent reversed endpoint-vertex identity")
        left_samples = left["sample_points_um"]
        right_samples = right["sample_points_um"]
        if left["start_vertex_id"] == left["end_vertex_id"]:
            # Closed native edges have one endpoint identity. Their reported
            # orientation supplies the canonical traversal direction.
            left_canonical = left_samples if left["orientation"] > 0 else list(reversed(left_samples))
            right_canonical = right_samples if right["orientation"] > 0 else list(reversed(right_samples))
        else:
            left_canonical = (left_samples if left["start_vertex_id"] < left["end_vertex_id"]
                              else list(reversed(left_samples)))
            right_canonical = (right_samples if right["start_vertex_id"] < right["end_vertex_id"]
                               else list(reversed(right_samples)))
        if len(left_canonical) != len(right_canonical) or any(
                _norm([left_canonical[index][axis] - right_canonical[index][axis]
                       for axis in range(3)]) > 1e-7
                for index in range(len(left_canonical))):
            _fail("the same native edge ID has inconsistent sampled curve geometry")

    # Every face's internal patch graph must be one connected sheet. This allows
    # native PartitionDomains to create many face patches and edge segments.
    face_topology_summary: dict[str, dict[str, Any]] = {}
    for role, patch_ids in role_patch_ids.items():
        adjacency: dict[int, set[int]] = {patch_id: set() for patch_id in patch_ids}
        for uses in edge_uses.values():
            if uses[0]["role"] == role and uses[1]["role"] == role:
                left_id, right_id = uses[0]["boundary_id"], uses[1]["boundary_id"]
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
        reached: set[int] = set()
        pending = [next(iter(patch_ids))]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(adjacency[current] - reached)
        if reached != patch_ids:
            _fail("partitioned patches on one control-volume face do not form one connected sheet")

        boundary_edges = []
        internal_edges = []
        edges = role_edge_ids[role]
        for edge_id in edges:
            incidences = [use for use in edge_uses[edge_id] if use["role"] == role]
            if len(incidences) not in (1, 2):
                _fail("control-volume face edge has invalid patch incidence count")
            if len(incidences) == 2:
                if (incidences[0]["boundary_id"] == incidences[1]["boundary_id"]
                        or incidences[0]["orientation"] + incidences[1]["orientation"] != 0
                        or incidences[0]["start_vertex_id"] != incidences[1]["end_vertex_id"]
                        or incidences[0]["end_vertex_id"] != incidences[1]["start_vertex_id"]):
                    _fail("internal edge must join distinct patches with opposite orientation")
                internal_edges.append(edge_id)
            else:
                other = next(use for use in edge_uses[edge_id] if use["role"] != role)
                if other["role"] not in expected:
                    _fail("face boundary edge is not shared with another registered control-volume face")
                boundary_edges.append(edge_id)

        # Cancelling every paired internal edge leaves one connected, simple
        # external cycle. Together with connected patches and correctly wound
        # hole loops, this certifies the complete face sheet is a disk without
        # excluding annular material patches.
        boundary_adjacency: dict[int, set[int]] = defaultdict(set)
        boundary_degree: dict[int, int] = defaultdict(int)
        for edge_id in boundary_edges:
            use = next(use for use in edge_uses[edge_id] if use["role"] == role)
            start, end = use["start_vertex_id"], use["end_vertex_id"]
            if start == end:
                _fail("closed boundary curve cannot be part of the rectangular outer perimeter")
            boundary_adjacency[start].add(end)
            boundary_adjacency[end].add(start)
            boundary_degree[start] += 1
            boundary_degree[end] += 1
        if len(boundary_edges) < 4 or any(degree != 2 for degree in boundary_degree.values()):
            _fail("cancelling internal patch edges must leave a segmented closed outer boundary cycle")
        reached_boundary: set[int] = set()
        pending_boundary = [next(iter(boundary_adjacency))]
        while pending_boundary:
            current = pending_boundary.pop()
            if current in reached_boundary:
                continue
            reached_boundary.add(current)
            pending_boundary.extend(boundary_adjacency[current] - reached_boundary)
        if reached_boundary != set(boundary_adjacency):
            _fail("control-volume face exterior boundary contains multiple disconnected cycles")
        face_topology_summary[role] = {
            "patch_count": len(patch_ids),
            "vertex_identity_count": len(role_vertex_ids[role]),
            "unique_edge_entity_count": len(edges),
            "internal_shared_edge_entity_count": len(internal_edges),
            "external_boundary_edge_segment_count": len(boundary_edges),
            "patch_euler_characteristic_sum": sum(
                patch_euler_characteristic[patch_id] for patch_id in patch_ids),
            "outer_boundary_cycle_count": 1,
            "whole_face_topology": "CONNECTED_DISK_WITH_COMPLETE_OUTER_CYCLE",
        }

    axes = ("x", "y", "z")
    limits = [CV_BOX_UM[name] for name in axes]
    expected_outer_lines: dict[tuple[int, tuple[tuple[int, float], ...]], frozenset[str]] = {}
    for free_axis in range(3):
        fixed_axes = [index for index in range(3) if index != free_axis]
        for first_side in (0, 1):
            for second_side in (0, 1):
                first_axis, second_axis = fixed_axes
                fixed = ((first_axis, float(limits[first_axis][first_side])),
                         (second_axis, float(limits[second_axis][second_side])))
                first_role = f"{axes[first_axis]}_{'minus' if first_side == 0 else 'plus'}"
                second_role = f"{axes[second_axis]}_{'minus' if second_side == 0 else 'plus'}"
                expected_outer_lines[(free_axis, fixed)] = frozenset((first_role, second_role))
    line_intervals: dict[tuple[int, tuple[tuple[int, float], ...]], list[tuple[float, float, int, int]]] = defaultdict(list)
    for edge_id, uses in edge_uses.items():
        roles = frozenset((uses[0]["role"], uses[1]["role"]))
        if len(roles) == 1:
            continue
        if len(roles) != 2:
            _fail("shared control-volume edge must be internal to one face or on two adjacent box faces")
        matched = []
        points = uses[0]["sample_points_um"]
        for line_key, line_roles in expected_outer_lines.items():
            free_axis, fixed = line_key
            if roles != line_roles:
                continue
            fixed_match = all(abs(point[axis] - value) <= 1e-7
                              for point in points for axis, value in fixed)
            if fixed_match:
                matched.append(line_key)
        if len(matched) != 1:
            _fail("cross-face edge does not lie on exactly one registered control-volume box edge")
        free_axis = matched[0][0]
        start_id, end_id = uses[0]["start_vertex_id"], uses[0]["end_vertex_id"]
        start_coordinate = global_vertices[start_id][free_axis]
        end_coordinate = global_vertices[end_id][free_axis]
        low, high = sorted((start_coordinate, end_coordinate))
        if high - low <= 1e-9:
            _fail("registered control-volume outer edge segment has zero length")
        low_vertex, high_vertex = ((start_id, end_id) if start_coordinate < end_coordinate
                                   else (end_id, start_id))
        line_intervals[matched[0]].append((low, high, low_vertex, high_vertex))

    for line_key, line_roles in expected_outer_lines.items():
        intervals = sorted(line_intervals.get(line_key, []), key=lambda item: (item[0], item[1]))
        free_axis = line_key[0]
        if not intervals:
            _fail("one of the twelve registered outer control-volume edges has no face coverage")
        if abs(intervals[0][0] - limits[free_axis][0]) > 1e-7:
            _fail("outer control-volume edge coverage does not start at the registered box corner")
        for current, following in zip(intervals, intervals[1:]):
            if following[0] < current[1] - 1e-7:
                _fail("outer control-volume edge segments overlap")
            if abs(following[0] - current[1]) > 1e-7 or following[2] != current[3]:
                _fail("outer control-volume edge segments have a gap or detached endpoint vertex")
        if abs(intervals[-1][1] - limits[free_axis][1]) > 1e-7:
            _fail("outer control-volume edge coverage does not reach the registered box corner")

    # A complete rectangular boundary has eight unique corners shared by their
    # three incident face sheets; analytic normal-area cancellation is not used.
    for x_side in (0, 1):
        for y_side in (0, 1):
            for z_side in (0, 1):
                target = [float(limits[0][x_side]), float(limits[1][y_side]), float(limits[2][z_side])]
                matching_ids = [vertex_id for vertex_id, point in global_vertices.items()
                                if _norm([point[i] - target[i] for i in range(3)]) <= 1e-7]
                expected_roles = {
                    f"x_{'minus' if x_side == 0 else 'plus'}",
                    f"y_{'minus' if y_side == 0 else 'plus'}",
                    f"z_{'minus' if z_side == 0 else 'plus'}",
                }
                if len(matching_ids) != 1 or vertex_roles[matching_ids[0]] != expected_roles:
                    _fail("each registered box corner must be one globally shared vertex on its three incident faces")

    total_area = sum(row["area_um2"] for row in expected.values())
    patch_euler_by_face = {
        role: sum(patch_euler_characteristic[patch_id] for patch_id in patch_ids)
        for role, patch_ids in role_patch_ids.items()
    }
    return {"status": "SOFTWARE_CV_TOPOLOGY_CONTRACT_VALID_NATIVE_NOT_RUN"
            if evidence_scope == "SOFTWARE_FIXTURE"
            else "NATIVE_CV_TOPOLOGY_READBACK_SHAPE_VALID_POWER_BALANCE_NOT_RUN",
            "native_result": "NOT_RUN" if evidence_scope == "SOFTWARE_FIXTURE" else "UNVERIFIED",
            "face_count": 6, "boundary_entity_count": len(all_entities),
            "closed_edge_count": len(edge_uses), "analytic_total_surface_area_um2": total_area,
            "analytic_rectangle_face_areas_um2": {row["role"]: row["area_um2"] for row in expected.values()},
            "native_surface_integral_of_one": "NOT_RUN",
            "area_preflight_scope": "ANALYTIC_RECTANGLE_REFERENCE_AND_ACTUAL_SHARED_EDGE_CORNER_COVERAGE; NATIVE_AREA_INTEGRALS_NOT_RUN",
            "outer_box_edge_line_count": len(expected_outer_lines),
            "outer_box_edge_segment_count": sum(len(rows) for rows in line_intervals.values()),
            "global_vertex_identity_count": len(global_vertices),
            "patch_count_by_face": {role: len(patch_ids) for role, patch_ids in role_patch_ids.items()},
            "patch_hole_count_by_boundary_id": patch_hole_count,
            "patch_euler_characteristic_by_boundary_id": patch_euler_characteristic,
            "sampled_patch_net_area_um2": patch_net_sampled_area_um2,
            "patch_euler_characteristic_sum_by_face": patch_euler_by_face,
            "face_topology_summary": face_topology_summary,
            "flat_face_normalization": copy.deepcopy(readback.get("flat_face_normalization")),
            "surface_closure_evidence": "global vertex coordinates, canonical shared-edge endpoints/curve samples, connected patches, outer/hole loop winding and containment, paired internal edges, one connected external boundary cycle per face, twelve complete box-edge interval chains and eight unique three-face corners",
            "power_balance_tolerance": "NOT_FROZEN"}
