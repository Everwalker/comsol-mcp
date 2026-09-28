from __future__ import annotations

import copy
import math

import pytest

from tools.w23_full3d import build_case_matrix
from tools.w23_radiation_geometry_v2 import (
    CV_BOX_UM,
    RadiationGeometryV2Error,
    _validate_mapping_partition,
    _digest,
    build_pml_partition_plan,
    canonical_radiation_geometry_v2,
    control_volume_face_contract,
    normalize_control_volume_topology_readback,
    verify_control_volume_readback,
    verify_geometry_clearance,
    verify_pml_region_assignment,
    verify_radiation_geometry_v2,
    _real_map_injectivity_certificate,
)


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _software_pml_readback(plan: dict) -> dict:
    domains = []
    regions = []
    face_by_id = {row["id"]: row for row in plan["faces"]}
    next_domain = 100
    for cell in plan["regions"]:
        active = set(cell["active_stretch_ids"])
        if not active:
            continue
        domain_id = next_domain
        next_domain += 1
        domains.append(domain_id)
        axial_only = active in ({"input_axial"}, {"output_axial"})
        if axial_only:
            roles = ("core", "cladding", "air")
        else:
            roles = ("air",)
        material_paths = []
        for role_index, role in enumerate(roles):
            material_tag = f"mat_{role}"
            if axial_only or role in {"core", "cladding"}:
                segments = ["propagation", "backing", "axial_pml"]
            elif active.intersection({"input_axial", "output_axial"}):
                segments = ["air", "axial_pml", "transverse_pml"]
            else:
                segments = ["air", "transverse_pml"]
            material_paths.append({
                "material_role": role,
                "native_material_tag": material_tag,
                "same_native_material_identity": True,
                "domain_ids": [domain_id] if role_index == 0 else [],
                "segments": segments,
                "material_tags_by_segment": {segment: material_tag for segment in segments},
            })
        # This software fixture has one domain per cell. Core/cladding/air are
        # subdomain identities of the two pure axial caps, not duplicate owners.
        if axial_only:
            material_paths = [
                {**row, "domain_ids": [domain_id] if index == 0 else []}
                for index, row in enumerate(material_paths)
            ]
            # Represent the three material regions with separate domain IDs.
            for row in material_paths[1:]:
                extra_domain = next_domain
                next_domain += 1
                row["domain_ids"] = [extra_domain]
                domains.append(extra_domain)
            cell_domains = [item for row in material_paths for item in row["domain_ids"]]
        else:
            cell_domains = [domain_id]
        directions = []
        for index, face_id in enumerate(cell["active_stretch_ids"]):
            face = face_by_id[face_id]
            directions.append({
                "index": index,
                "face_id": face_id,
                "distance_expression": face["distance_expression"],
                "anchor_point_um": face["backing_pml_interface_um"],
                "dmax_um": face["dmax_um"],
                "gradient_xyz": face["axis"],
            })
        regions.append({
            "region_id": cell["region_id"],
            "active_stretch_ids": cell["active_stretch_ids"],
            "domain_ids": cell_domains,
            "node_tag": "pml_" + cell["region_id"],
            "connected_component_count": 1,
            "directions": directions,
            "material_paths": material_paths,
        })
    return {
        "schema_id": "urn:comsol-mcp:w23:pml-native-readback:1.0.0",
        "ScalingType": "userDefined",
        "stretchingType": "polynomial",
        "PMLfactor": 1.0,
        "PMLgamma": 1.0,
        "typical_wavelength_um": 1.55,
        "regions": regions,
        "pml_domain_ids": domains,
        "complex_mapping": {
            "evidence_scope": "SOFTWARE_ANALYTIC_FIXTURE",
            "partition_plan_sha256": plan["plan_sha256"],
            "shared_interface_count": plan["interfaces"]["seam_count"],
            "maximum_shared_face_jump_um": plan["interfaces"]["max_complex_coordinate_jump_um"],
            "minimum_abs_pml_jacobian_determinant": plan["interfaces"]["minimum_abs_pml_jacobian_determinant"],
            "nonorthogonal_comsol_pml_compatibility": "UNVERIFIED",
        },
    }


def _software_cv_readback() -> dict:
    vertices = {
        0: [CV_BOX_UM["x"][0], CV_BOX_UM["y"][0], CV_BOX_UM["z"][0]],
        1: [CV_BOX_UM["x"][1], CV_BOX_UM["y"][0], CV_BOX_UM["z"][0]],
        2: [CV_BOX_UM["x"][1], CV_BOX_UM["y"][1], CV_BOX_UM["z"][0]],
        3: [CV_BOX_UM["x"][0], CV_BOX_UM["y"][1], CV_BOX_UM["z"][0]],
        4: [CV_BOX_UM["x"][0], CV_BOX_UM["y"][0], CV_BOX_UM["z"][1]],
        5: [CV_BOX_UM["x"][1], CV_BOX_UM["y"][0], CV_BOX_UM["z"][1]],
        6: [CV_BOX_UM["x"][1], CV_BOX_UM["y"][1], CV_BOX_UM["z"][1]],
        7: [CV_BOX_UM["x"][0], CV_BOX_UM["y"][1], CV_BOX_UM["z"][1]],
    }
    loops = {
        "x_minus": ([0, 4, 7, 3], [(9, 1), (8, -1), (12, -1), (4, 1)]),
        "x_plus": ([1, 2, 6, 5], [(2, 1), (11, 1), (6, -1), (10, -1)]),
        "y_minus": ([0, 1, 5, 4], [(1, 1), (10, 1), (5, -1), (9, -1)]),
        "y_plus": ([3, 7, 6, 2], [(12, 1), (7, -1), (11, -1), (3, 1)]),
        "z_minus": ([0, 3, 2, 1], [(4, -1), (3, -1), (2, -1), (1, -1)]),
        "z_plus": ([4, 5, 6, 7], [(5, 1), (6, 1), (7, 1), (8, 1)]),
    }
    faces = []
    for entity_id, contract in enumerate(control_volume_face_contract(), start=501):
        vertex_ids, edges = loops[contract["role"]]
        points = [vertices[i] for i in vertex_ids]
        centroid = [sum(point[i] for point in points) / 4 for i in range(3)]
        edge_loop = []
        for index, (edge_id, orientation) in enumerate(edges):
            start = vertices[vertex_ids[index]]
            end = vertices[vertex_ids[(index + 1) % len(vertex_ids)]]
            middle = [(start[i] + end[i]) / 2 for i in range(3)]
            edge_loop.append({
                "edge_id": edge_id,
                "orientation": orientation,
                "start_vertex_id": vertex_ids[index] + 1,
                "end_vertex_id": vertex_ids[(index + 1) % len(vertex_ids)] + 1,
                "sample_order": "face_orientation",
                "sample_points_um": [start, middle, end],
            })
        faces.append({
            "role": contract["role"],
            "patches": [{
                "boundary_id": entity_id,
                "approximate_area_value_native_units": contract["area_um2"],
                "centroid_um": centroid,
                "outward_normal_xyz": contract["normal"],
                "vertex_coordinates_um": [
                    {"vertex_id": vertex_id + 1, "point_um": vertices[vertex_id]}
                    for vertex_id in vertex_ids
                ],
                "edge_loops": [edge_loop],
                "adjacent_domain_ids": [1, 2],
                "adjacent_material_tags": ["matAir", "matAir"],
                "touches_pml": False,
                "touches_port": False,
            }],
        })
    return {
        "schema_id": "urn:comsol-mcp:w23:control-volume-topology:1.0.0",
        "evidence_scope": "SOFTWARE_FIXTURE",
        "coordinate_unit": "um",
        "geometry_length_unit": "um",
        "geometry_length_unit_readback": True,
        "area_unit": "NOT_MEASURED_IN_PREFLIGHT",
        "partition_feature_type": "PartitionDomains",
        "actual_partition_feature_readback": False,
        "topology_source": "software fixture only",
        "faces": faces,
    }


def _software_java_flat_cv_readback(*, nested: dict | None = None,
                                    wrap_external_bbox: bool = False) -> dict:
    """Mirror the Java producer's flat face-row shape, including raw orientation."""
    nested = copy.deepcopy(nested if nested is not None else _software_cv_readback())
    flat_rows = []
    next_domain_id = 1000
    for face in nested["faces"]:
        role = face["role"]
        contract = next(row for row in control_volume_face_contract() if row["role"] == role)
        axis = next(index for index, value in enumerate(contract["normal"]) if value)
        plane = contract["point_um"][axis]
        normal_sign = contract["normal"][axis]
        for patch in face["patches"]:
            points = [row["point_um"] for row in patch["vertex_coordinates_um"]]
            points.extend(point for loop in patch["edge_loops"] for edge in loop
                          for point in edge["sample_points_um"])
            points.append(patch["centroid_um"])
            inside_id, outside_id = next_domain_id, next_domain_id + 1
            next_domain_id += 2
            inner_bounds = []
            outer_bounds = []
            for coordinate in range(3):
                if coordinate == axis:
                    inward_side = ([plane, plane + 0.1] if normal_sign < 0
                                   else [plane - 0.1, plane])
                    outward_side = ([plane - 0.1, plane] if normal_sign < 0
                                    else [plane, plane + 0.1])
                    inner_bounds.extend(inward_side)
                    outer_bounds.extend(outward_side)
                else:
                    lows = [point[coordinate] for point in points]
                    inner_bounds.extend([min(lows), max(lows)])
                    outer_bounds.extend([min(lows), max(lows)])
            if wrap_external_bbox and role == "x_minus":
                # The exterior domain can wrap around a finite rectangular CV
                # cut; its bbox then crosses the normal plane and box edges.
                outer_bounds[0:2] = [plane - 1.0, plane + 1.0]
                outer_bounds[2:4] = [-6.0, 6.0]
            raw_normal = list(contract["normal"])
            loops = copy.deepcopy(patch["edge_loops"])
            adjacent_orientation = [1, -1]
            if patch["boundary_id"] == 501:
                raw_normal = [-value for value in raw_normal]
                adjacent_orientation = [-1, 1]
                loops = [[
                    {**edge,
                     "start_vertex_id": edge["end_vertex_id"],
                     "end_vertex_id": edge["start_vertex_id"],
                     "orientation": -edge["orientation"],
                     "sample_points_um": list(reversed(edge["sample_points_um"]))}
                    for edge in reversed(loop)
                ] for loop in loops]
            flat_rows.append({
                "boundary_id": patch["boundary_id"],
                "face_parameter_sample_point_um": list(patch["centroid_um"]),
                "raw_face_normal_xyz": raw_normal,
                "adjacent_domain_ids": [inside_id, outside_id],
                "adjacent_domain_orientation": adjacent_orientation,
                "adjacent_domain_bbox_um_by_id": {
                    str(inside_id): inner_bounds,
                    str(outside_id): outer_bounds,
                },
                "adjacent_material_tags": list(patch["adjacent_material_tags"]),
                "touches_pml": patch["touches_pml"],
                "touches_port": patch["touches_port"],
                "vertex_coordinates_um": copy.deepcopy(patch["vertex_coordinates_um"]),
                "edge_loops": loops,
            })
    return {**{key: copy.deepcopy(value) for key, value in nested.items() if key != "faces"},
            "faces": flat_rows}


def _software_cv_with_split_face() -> dict:
    """A valid two-patch face fixture with a shared internal diagonal."""
    readback = _software_cv_readback()
    face = next(row for row in readback["faces"] if row["role"] == "x_minus")
    original = face["patches"][0]
    point_by_vertex = {row["vertex_id"]: row["point_um"] for row in original["vertex_coordinates_um"]}

    def edge(edge_id: int, orientation: int, start: int, end: int) -> dict:
        start_point = point_by_vertex[start]
        end_point = point_by_vertex[end]
        middle = [(start_point[index] + end_point[index]) / 2 for index in range(3)]
        return {
            "edge_id": edge_id,
            "orientation": orientation,
            "start_vertex_id": start,
            "end_vertex_id": end,
            "sample_order": "face_orientation",
            "sample_points_um": [start_point, middle, end_point],
        }

    def triangle(boundary_id: int, vertex_ids: list[int], loop: list[dict]) -> dict:
        points = [point_by_vertex[vertex_id] for vertex_id in vertex_ids]
        result = copy.deepcopy(original)
        result["boundary_id"] = boundary_id
        result["centroid_um"] = [sum(point[index] for point in points) / 3 for index in range(3)]
        result["approximate_area_value_native_units"] /= 2
        result["vertex_coordinates_um"] = [
            {"vertex_id": vertex_id, "point_um": point_by_vertex[vertex_id]}
            for vertex_id in vertex_ids
        ]
        result["edge_loops"] = [loop]
        return result

    face["patches"] = [
        triangle(501, [1, 5, 8], [edge(9, 1, 1, 5), edge(8, -1, 5, 8), edge(13, 1, 8, 1)]),
        triangle(507, [1, 8, 4], [edge(13, -1, 1, 8), edge(12, -1, 8, 4), edge(4, 1, 4, 1)]),
    ]
    return readback


def _software_cv_with_annular_face() -> dict:
    """A valid annular patch and its separately bounded central disk."""
    readback = _software_cv_readback()
    face = next(row for row in readback["faces"] if row["role"] == "z_plus")
    annulus = face["patches"][0]
    square = {
        9: [-1.0, -1.0, 5.5], 10: [1.0, -1.0, 5.5],
        11: [1.0, 1.0, 5.5], 12: [-1.0, 1.0, 5.5],
    }
    annulus["vertex_coordinates_um"].extend(
        {"vertex_id": vertex_id, "point_um": point} for vertex_id, point in square.items())

    def edge(edge_id: int, orientation: int, start: int, end: int) -> dict:
        start_point, end_point = square[start], square[end]
        middle = [(start_point[index] + end_point[index]) / 2 for index in range(3)]
        return {
            "edge_id": edge_id,
            "orientation": orientation,
            "start_vertex_id": start,
            "end_vertex_id": end,
            "sample_order": "face_orientation",
            "sample_points_um": [start_point, middle, end_point],
        }

    hole_loop = [edge(16, -1, 9, 12), edge(15, -1, 12, 11),
                 edge(14, -1, 11, 10), edge(13, -1, 10, 9)]
    disk_loop = [edge(13, 1, 9, 10), edge(14, 1, 10, 11),
                 edge(15, 1, 11, 12), edge(16, 1, 12, 9)]
    annulus["edge_loops"].append(hole_loop)
    annulus["approximate_area_value_native_units"] -= 4.0
    disk = copy.deepcopy(annulus)
    disk["boundary_id"] = 507
    disk["approximate_area_value_native_units"] = 4.0
    disk["centroid_um"] = [0.0, 0.0, 5.5]
    disk["vertex_coordinates_um"] = [
        {"vertex_id": vertex_id, "point_um": point} for vertex_id, point in square.items()]
    disk["edge_loops"] = [disk_loop]
    disk["adjacent_domain_ids"] = [1, 3]
    disk["adjacent_material_tags"] = ["matCore", "matCore"]
    face["patches"].append(disk)
    return readback


def _software_cv_with_closed_circle_hole() -> dict:
    """The annular interface represented by one native closed-edge entity."""
    readback = _software_cv_with_annular_face()
    face = next(row for row in readback["faces"] if row["role"] == "z_plus")
    annulus, disk = face["patches"]
    circle = [[math.cos(2 * math.pi * index / 16),
               -math.sin(2 * math.pi * index / 16), 5.5]
              for index in range(17)]
    annulus["vertex_coordinates_um"] = [row for row in annulus["vertex_coordinates_um"]
                                         if row["vertex_id"] not in {10, 11, 12}]
    annulus["vertex_coordinates_um"] = [
        row for row in annulus["vertex_coordinates_um"] if row["vertex_id"] != 9]
    annulus["vertex_coordinates_um"].append({"vertex_id": 9, "point_um": circle[0]})
    annulus["edge_loops"][1] = [{
        "edge_id": 13,
        "orientation": -1,
        "start_vertex_id": 9,
        "end_vertex_id": 9,
        "sample_order": "face_orientation",
        "sample_points_um": circle,
    }]
    disk["vertex_coordinates_um"] = [{"vertex_id": 9, "point_um": circle[0]}]
    disk["edge_loops"] = [[{
        "edge_id": 13,
        "orientation": 1,
        "start_vertex_id": 9,
        "end_vertex_id": 9,
        "sample_order": "face_orientation",
        "sample_points_um": list(reversed(circle)),
    }]]
    return readback


def test_v2_recipe_is_versioned_and_never_claims_native_acceptance() -> None:
    recipe = canonical_radiation_geometry_v2()
    result = verify_radiation_geometry_v2(recipe)
    assert result["status"] == "SOFTWARE_RECIPE_VALID_NATIVE_NOT_RUN"
    assert result["native_result"] == "NOT_RUN"
    assert result["pml_junction_native_status"] == "UNVERIFIED"
    changed = copy.deepcopy(recipe)
    changed["ports"]["output"]["slit_type"] = "PECBacked"
    with pytest.raises(RadiationGeometryV2Error):
        verify_radiation_geometry_v2(changed)


def test_tilted_domain_map_lists_all_cells_faces_directions_and_material_obligations() -> None:
    recipe = canonical_radiation_geometry_v2()
    cases = [row for row in build_case_matrix() if "theta" in row["factor"]]
    assert len(cases) == 4
    transforms = [row["receiver_transform"] for row in cases]
    base = copy.deepcopy(next(row["receiver_transform"] for row in cases
                              if row["factor"] == "receiver_theta_y_deg" and row["sign"] == 1))
    theta_y = math.radians(0.5)
    theta_z = math.radians(0.5)
    combined_axis = [math.cos(theta_z) * math.cos(theta_y),
                     math.sin(theta_z) * math.cos(theta_y), -math.sin(theta_y)]
    base.update({
        "theta_y_deg": 0.5,
        "theta_z_deg": 0.5,
        "axis_xyz": combined_axis,
        "center_xyz_um": [5.0 + 15.0 * combined_axis[0],
                          15.0 * combined_axis[1],
                          15.0 * combined_axis[2]],
    })
    transforms.append(base)
    for transform in transforms:
        plan = build_pml_partition_plan(recipe, transform)
        assert len(plan["regions"]) == 27
        assert plan["domain_map_contract"]["physical_cell_count"] == 1
        assert plan["domain_map_contract"]["pml_cell_count"] == 26
        partition = plan["domain_map_contract"]["analytic_partition_volume"]
        assert partition["status"] == "SOFTWARE_ANALYTIC_PARTITION_NO_GAP_OR_OVERLAP"
        assert partition["absolute_volume_residual_um3"] < 1e-8
        assert len(partition["cell_volumes_um3"]) == 27
        injectivity = plan["domain_map_contract"]["real_map_injectivity"]
        assert injectivity["status"] == "SOFTWARE_ANALYTIC_REAL_PROJECTION_INJECTIVE"
        assert injectivity["minimum_strong_monotonicity_bound"] > 0
        assert len(plan["interfaces"]["shared_interfaces"]) == 54
        assert plan["interfaces"]["seam_count"] == 54
        assert plan["interfaces"]["max_complex_coordinate_jump_um"] < 1e-10
        assert plan["interfaces"]["minimum_abs_pml_jacobian_determinant"] > 1.0
        nonorthogonal_cells = [region["region_id"] for region in plan["regions"]
                               if any(abs(value) > 1e-10
                                      for value in region["pairwise_direction_cosines"].values())]
        assert plan["junction_review"]["nonorthogonal_stretch_cells"] == nonorthogonal_cells
        assert plan["junction_review"]["compatibility_status"] == "UNVERIFIED"
        assert plan["native_result"] == "NOT_RUN"
        assert len(plan["faces"]) == 6
        for face in plan["faces"]:
            assert _close(math.sqrt(sum(v * v for v in face["axis"])), 1.0)
            assert _close(face["gradient_norm"], 1.0)
            assert face["anchor_role"] == "actual_backing_to_PML_interface"
        for region in plan["regions"]:
            assert len(region["distance_inequalities"]) == 6
            assert region["domain_material_readback_required"] is True
            assert all(len(vertex) == 3 for vertex in region["vertices_um"])
        for seam in plan["interfaces"]["shared_interfaces"]:
            assert seam["left_region_id"] != seam["right_region_id"]
            assert seam["transition_face_id"] in {face["id"] for face in plan["faces"]}
            assert len(seam["shared_face_vertices_um"]) >= 3
            assert seam["maximum_complex_coordinate_jump_um"] < 1e-10


@pytest.mark.parametrize("corruption", ["scaled_axis", "wrong_axis", "off_axis_center", "wrong_rotation_order"])
def test_receiver_transform_must_be_the_registered_unit_rigid_frame(corruption: str) -> None:
    recipe = canonical_radiation_geometry_v2()
    transform = copy.deepcopy(next(row for row in build_case_matrix()
                                   if row["factor"] == "receiver_theta_y_deg"
                                   and row["sign"] == 1)["receiver_transform"])
    if corruption == "scaled_axis":
        transform["axis_xyz"] = [2.0 * value for value in transform["axis_xyz"]]
    elif corruption == "wrong_axis":
        transform["axis_xyz"] = [1.0, 0.0, 0.0]
    elif corruption == "off_axis_center":
        transform["center_xyz_um"][1] += 0.01
    else:
        transform["rotation_order"] = "z then y"
    with pytest.raises(RadiationGeometryV2Error):
        build_pml_partition_plan(recipe, transform)


def test_zero_stretch_singular_jacobian_is_rejected() -> None:
    recipe = canonical_radiation_geometry_v2()
    plan = build_pml_partition_plan(recipe)
    with pytest.raises(RadiationGeometryV2Error, match="singular/degenerate Jacobian"):
        _validate_mapping_partition(plan["regions"], plan["faces"], 1.55, 0.0, 1.0)


def test_piecewise_real_map_rejects_a_nonpositive_global_injectivity_bound() -> None:
    plan = build_pml_partition_plan(canonical_radiation_geometry_v2())
    faces = copy.deepcopy(plan["faces"])
    for face in faces:
        face["dmax_um"] = 10.0
    with pytest.raises(RadiationGeometryV2Error, match="may fold or overlap"):
        _real_map_injectivity_certificate(plan["regions"], faces, 1.55, 1.0, 1.0)


def test_registered_clearance_matrix_keeps_air_port_and_capture_selections_separate() -> None:
    result = verify_geometry_clearance(canonical_radiation_geometry_v2())
    assert result["case_count"] == 19
    assert result["minimum_fiber_to_transverse_pml_margin_um"] >= 0.5
    assert result["required_buffer_um"] == 0.5
    assert result["port_aperture_case_count"] == 19
    assert result["port_aperture_selection"] == (
        "full plane intersection with actual six-face physical envelope; all air/cladding/core boundary pieces")
    assert result["minimum_local_square_halfwidth_um"] == 5.5
    assert result["minimum_square_to_physical_halfspace_clearance_um"] >= 0.25 - 1e-9
    angular_conflicts = result["full_polygon_backing_and_axial_pml_sweep_conflicts"]
    assert len(angular_conflicts) == 4
    assert all(item["port_role"] == "output" for item in angular_conflicts)
    assert all(item["minimum_full_polygon_transverse_clearance_um"] < -0.03
               for item in angular_conflicts)
    assert result["status"] == "SOFTWARE_FIBER_CLEARANCE_VALID_PORT_BACKING_SWEEP_CONFLICT"


def test_full_air_port_polygon_sweep_conflict_is_explicit_and_capture_stays_separate() -> None:
    recipe = canonical_radiation_geometry_v2()
    plan = build_pml_partition_plan(recipe)
    output = plan["port_air_apertures"]["output"]
    assert output["geometry"] == "full physical halfspace-envelope intersection with the actual Numeric Port plane"
    assert len(output["polygon_vertices_local_uv_um"]) >= 4
    assert output["analytic_polygon_area_um2"] > 4 * 5.5**2
    assert output["minimum_local_square_halfwidth_um"] == 5.5
    assert output["minimum_square_corners_inside_full_polygon"] is True
    assert output["axial_material_continuity_required"] == ["fiber_core", "fiber_cladding", "air"]
    assert output["native_boundary_ids_and_material_membership"] == "NOT_READ"
    # The aligned baseline has no transverse shift along its backing/PML axis.
    assert output["full_polygon_sweep_status"] == "SOFTWARE_FULL_POLYGON_SWEEP_CLEAR"

    tilted = next(row["receiver_transform"] for row in build_case_matrix()
                  if row["factor"] == "receiver_theta_y_deg" and row["sign"] == 1)
    tilted_aperture = build_pml_partition_plan(recipe, tilted)["port_air_apertures"]["output"]
    assert tilted_aperture["full_polygon_sweep_status"] == "CONFLICT_WITH_GLOBAL_TRANSVERSE_PML_ENVELOPE"
    assert tilted_aperture["axial_sweep_stations"][1]["minimum_transverse_clearance_um"] < 0
    assert tilted_aperture["axial_sweep_stations"][2]["minimum_transverse_clearance_um"] < 0


def test_port_aperture_contract_rejects_shrinking_back_to_cladding_scale() -> None:
    recipe = canonical_radiation_geometry_v2()
    recipe["geometry"]["port_air_aperture"]["minimum_local_square_halfwidth_um"] = 2.57
    recipe["recipe_sha256"] = _digest({key: value for key, value in recipe.items()
                                        if key != "recipe_sha256"})
    with pytest.raises(RadiationGeometryV2Error, match="full physical-air Port aperture"):
        verify_radiation_geometry_v2(recipe)


def test_versioned_pml_readback_recomputes_cell_ownership_and_keeps_status_software() -> None:
    plan = build_pml_partition_plan(canonical_radiation_geometry_v2())
    result = verify_pml_region_assignment(plan, _software_pml_readback(plan))
    assert result["status"] == "SOFTWARE_PML_PARTITION_CONTRACT_VALID_NATIVE_NOT_RUN"
    assert result["native_result"] == "NOT_RUN"
    assert result["pml_region_count"] == 26
    assert result["pml_node_count"] == 26


@pytest.mark.parametrize("mutation", [
    "dmax", "anchor", "gradient", "duplicate_region", "foreign_domain",
    "duplicate_node_tag", "wrong_material_tag", "wrong_material_path",
    "mapping_jump", "mapping_jacobian", "mapping_plan", "mapping_claim",
])
def test_pml_readback_rejects_detached_or_inconsistent_evidence(mutation: str) -> None:
    plan = build_pml_partition_plan(canonical_radiation_geometry_v2())
    readback = _software_pml_readback(plan)
    if mutation == "duplicate_region":
        readback["regions"].append(copy.deepcopy(readback["regions"][0]))
    elif mutation == "foreign_domain":
        readback["regions"][0]["domain_ids"] = [999]
    elif mutation == "duplicate_node_tag":
        readback["regions"][1]["node_tag"] = readback["regions"][0]["node_tag"]
    elif mutation == "wrong_material_tag":
        readback["regions"][0]["material_paths"][0]["native_material_tag"] = "changed"
    elif mutation == "wrong_material_path":
        readback["regions"][0]["material_paths"][0]["segments"] = ["air", "backing"]
    else:
        mapping = readback["complex_mapping"]
        if mutation == "dmax":
            readback["regions"][0]["directions"][0]["dmax_um"] *= 2
        elif mutation == "anchor":
            readback["regions"][0]["directions"][0]["anchor_point_um"][0] += 1
        elif mutation == "gradient":
            readback["regions"][0]["directions"][0]["gradient_xyz"] = [0.0, 0.0, 1.0]
        elif mutation == "mapping_jump":
            mapping["maximum_shared_face_jump_um"] = 0.1
        elif mutation == "mapping_jacobian":
            mapping["minimum_abs_pml_jacobian_determinant"] = 0.0
        elif mutation == "mapping_plan":
            mapping["partition_plan_sha256"] = "0" * 64
        elif mutation == "mapping_claim":
            mapping["nonorthogonal_comsol_pml_compatibility"] = "VERIFIED"
    with pytest.raises(RadiationGeometryV2Error):
        verify_pml_region_assignment(plan, readback)


def test_pml_plan_digest_rejects_caller_edited_domain_inequalities() -> None:
    plan = build_pml_partition_plan(canonical_radiation_geometry_v2())
    readback = _software_pml_readback(plan)
    changed = copy.deepcopy(plan)
    changed["regions"][0]["distance_inequalities"][0]["upper_um"] = 1000
    with pytest.raises(RadiationGeometryV2Error, match="identity or digest"):
        verify_pml_region_assignment(changed, readback)


def test_control_volume_topology_contract_requires_six_unique_closed_internal_faces() -> None:
    result = verify_control_volume_readback(_software_cv_readback())
    assert result["status"] == "SOFTWARE_CV_TOPOLOGY_CONTRACT_VALID_NATIVE_NOT_RUN"
    assert result["native_result"] == "NOT_RUN"
    assert result["face_count"] == 6
    assert result["closed_edge_count"] == 12
    assert result["power_balance_tolerance"] == "NOT_FROZEN"
    assert result["native_surface_integral_of_one"] == "NOT_RUN"


def test_java_flat_boundary_rows_are_grouped_and_normalized_in_production_route() -> None:
    flat = _software_java_flat_cv_readback()
    assert all("patches" not in row and "role" not in row for row in flat["faces"])
    assert all("raw_face_normal_xyz" in row and "face_parameter_sample_point_um" in row
               for row in flat["faces"])
    normalized = normalize_control_volume_topology_readback(flat)
    assert [row["role"] for row in normalized["faces"]] == [
        row["role"] for row in control_volume_face_contract()]
    assert all(row["patches"] for row in normalized["faces"])
    corrected = normalized["faces"][0]["patches"][0]
    assert corrected["raw_face_normal_xyz"] == [1.0, 0.0, 0.0]
    assert corrected["raw_normal_to_outward_dot"] == -1.0
    assert corrected["outward_normal_xyz"] == [-1.0, 0.0, 0.0]
    assert corrected["normal_orientation_correction"] == "EDGE_LOOPS_REVERSED_TO_INSIDE_TO_OUTSIDE"
    assert corrected["adjacent_domain_side_ids"]["inside"] == 1000
    assert corrected["adjacent_domain_side_ids"]["outside"] == 1001
    assert corrected["adjacent_domain_orientation_by_id"] == {"1000": -1, "1001": 1}
    # This call exercises the real verifier's flat-row adapter, not a
    # test-only conversion into the nested shape it expects internally.
    result = verify_control_volume_readback(flat)
    assert result["native_result"] == "NOT_RUN"
    assert result["boundary_entity_count"] == 6
    assert result["flat_face_normalization"]["status"] == "SOFTWARE_GROUPED_FROM_FLAT_JAVA_ROWS"
    assert len(result["flat_face_normalization"]["patch_orientation_audit"]) == 6


def test_java_flat_adapter_accepts_wraparound_exterior_bbox_and_uses_interior_domain() -> None:
    flat = _software_java_flat_cv_readback(wrap_external_bbox=True)
    x_minus = next(row for row in flat["faces"] if row["boundary_id"] == 501)
    outside_bbox = x_minus["adjacent_domain_bbox_um_by_id"]["1001"]
    assert outside_bbox[0] < CV_BOX_UM["x"][0] < outside_bbox[1]
    assert outside_bbox[2] < CV_BOX_UM["y"][0]
    assert outside_bbox[3] > CV_BOX_UM["y"][1]
    normalized = normalize_control_volume_topology_readback(flat)
    patch = next(row for row in normalized["faces"] if row["role"] == "x_minus")["patches"][0]
    assert patch["adjacent_domain_side_ids"] == {"inside": 1000, "outside": 1001}
    assert verify_control_volume_readback(flat)["native_result"] == "NOT_RUN"


def test_java_flat_adapter_preserves_closed_circle_hole_sampling_and_annular_cover() -> None:
    flat = _software_java_flat_cv_readback(nested=_software_cv_with_closed_circle_hole())
    result = verify_control_volume_readback(flat)
    assert result["boundary_entity_count"] == 7
    assert result["patch_count_by_face"]["z_plus"] == 2
    assert result["patch_hole_count_by_boundary_id"][506] == 1
    ring = next(row for row in flat["faces"] if row["boundary_id"] == 506)
    closed_hole_edge = ring["edge_loops"][1][0]
    assert closed_hole_edge["start_vertex_id"] == closed_hole_edge["end_vertex_id"]
    assert len(closed_hole_edge["sample_points_um"]) == 17


@pytest.mark.parametrize("mutation", [
    "missing_orientation", "same_orientation", "no_interior_bbox", "normal_not_parallel",
    "bbox_does_not_touch_plane",
])
def test_java_flat_adapter_rejects_ambiguous_or_inconsistent_native_orientation(mutation: str) -> None:
    flat = _software_java_flat_cv_readback()
    row = next(row for row in flat["faces"] if row["boundary_id"] == 501)
    if mutation == "missing_orientation":
        row.pop("adjacent_domain_orientation")
    elif mutation == "same_orientation":
        row["adjacent_domain_orientation"] = [1, 1]
    elif mutation == "no_interior_bbox":
        row["adjacent_domain_bbox_um_by_id"]["1000"][0] = CV_BOX_UM["x"][0] - 0.2
    elif mutation == "normal_not_parallel":
        row["raw_face_normal_xyz"] = [0.0, 1.0, 0.0]
    elif mutation == "bbox_does_not_touch_plane":
        row["adjacent_domain_bbox_um_by_id"]["1000"][0] += 0.1
        row["adjacent_domain_bbox_um_by_id"]["1000"][1] += 0.1
    with pytest.raises(RadiationGeometryV2Error):
        verify_control_volume_readback(flat)


def test_control_volume_topology_accepts_partitioned_face_and_internal_shared_edge() -> None:
    result = verify_control_volume_readback(_software_cv_with_split_face())
    assert result["status"] == "SOFTWARE_CV_TOPOLOGY_CONTRACT_VALID_NATIVE_NOT_RUN"
    assert result["patch_count_by_face"]["x_minus"] == 2
    assert result["closed_edge_count"] == 13
    assert result["boundary_entity_count"] == 7


def test_control_volume_topology_accepts_annulus_plus_filled_disk() -> None:
    result = verify_control_volume_readback(_software_cv_with_annular_face())
    assert result["patch_count_by_face"]["z_plus"] == 2
    assert result["patch_hole_count_by_boundary_id"][506] == 1
    assert result["patch_euler_characteristic_by_boundary_id"][506] == 0
    assert result["patch_euler_characteristic_by_boundary_id"][507] == 1
    assert result["patch_euler_characteristic_sum_by_face"]["z_plus"] == 1
    assert result["native_result"] == "NOT_RUN"


def test_control_volume_topology_accepts_annular_patch_with_single_closed_edge() -> None:
    result = verify_control_volume_readback(_software_cv_with_closed_circle_hole())
    assert result["patch_hole_count_by_boundary_id"][506] == 1
    assert result["patch_euler_characteristic_sum_by_face"]["z_plus"] == 1


@pytest.mark.parametrize("mutation", [
    "duplicate_face_role", "duplicate_boundary", "wrong_normal", "geometry_unit",
    "one_domain", "pml_touch", "port_touch", "missing_edge", "same_edge_orientation",
    "outside_box", "fake_native_scope", "missing_partition_readback",
    "disconnected_faces", "shared_edge_endpoint", "shared_edge_curve", "patch_winding",
    "missing_hole_fill", "reverse_hole_winding",
])
def test_control_volume_readback_rejects_open_or_unbound_topology(mutation: str) -> None:
    readback = _software_cv_readback()
    if mutation == "duplicate_face_role":
        readback["faces"].append(copy.deepcopy(readback["faces"][0]))
    elif mutation == "duplicate_boundary":
        readback["faces"][1]["patches"][0]["boundary_id"] = readback["faces"][0]["patches"][0]["boundary_id"]
    elif mutation == "wrong_normal":
        readback["faces"][0]["patches"][0]["outward_normal_xyz"] = [1, 0, 0]
    elif mutation == "geometry_unit":
        readback["geometry_length_unit"] = "m"
    elif mutation == "one_domain":
        readback["faces"][0]["patches"][0]["adjacent_domain_ids"] = [1]
    elif mutation == "pml_touch":
        readback["faces"][0]["patches"][0]["touches_pml"] = True
    elif mutation == "port_touch":
        readback["faces"][0]["patches"][0]["touches_port"] = True
    elif mutation == "missing_edge":
        readback["faces"][0]["patches"][0]["edge_loops"][0].pop()
    elif mutation == "same_edge_orientation":
        readback["faces"][1]["patches"][0]["edge_loops"][0][0]["orientation"] *= -1
    elif mutation == "outside_box":
        readback["faces"][0]["patches"][0]["centroid_um"][1] = 100.0
    elif mutation == "fake_native_scope":
        readback["evidence_scope"] = "CALLER_ASSERTED_NATIVE"
    elif mutation == "missing_partition_readback":
        readback["evidence_scope"] = "COMSOL_NATIVE_PARTITION_DOMAINS"
    elif mutation == "disconnected_faces":
        # Reproduce the detached-six-faces attack: preserve IDs but shrink each
        # face and all of its edge samples around that face's own centroid.
        for face in readback["faces"]:
            for patch in face["patches"]:
                center = patch["centroid_um"]
                for vertex in patch["vertex_coordinates_um"]:
                    vertex["point_um"] = [
                        center[index] + 0.5 * (vertex["point_um"][index] - center[index])
                        for index in range(3)
                    ]
                for loop in patch["edge_loops"]:
                    for item in loop:
                        item["sample_points_um"] = [
                            [center[index] + 0.5 * (point[index] - center[index]) for index in range(3)]
                            for point in item["sample_points_um"]
                        ]
    elif mutation == "shared_edge_endpoint":
        patch = next(row for row in readback["faces"] if row["role"] == "z_minus")["patches"][0]
        item = next(item for item in patch["edge_loops"][0] if item["edge_id"] == 2)
        item["end_vertex_id"] = 1
    elif mutation == "shared_edge_curve":
        patch = next(row for row in readback["faces"] if row["role"] == "x_plus")["patches"][0]
        item = next(item for item in patch["edge_loops"][0] if item["edge_id"] == 2)
        item["sample_points_um"][1][1] += 0.25
    elif mutation == "patch_winding":
        patch = next(row for row in readback["faces"] if row["role"] == "x_minus")["patches"][0]
        patch["edge_loops"][0].reverse()
    elif mutation == "missing_hole_fill":
        readback = _software_cv_with_annular_face()
        face = next(row for row in readback["faces"] if row["role"] == "z_plus")
        face["patches"].pop()
    elif mutation == "reverse_hole_winding":
        readback = _software_cv_with_annular_face()
        patch = next(row for row in readback["faces"] if row["role"] == "z_plus")["patches"][0]
        patch["edge_loops"][1] = [
            {**item, "start_vertex_id": item["end_vertex_id"],
             "end_vertex_id": item["start_vertex_id"],
             "orientation": -item["orientation"],
             "sample_points_um": list(reversed(item["sample_points_um"]))}
            for item in reversed(patch["edge_loops"][1])
        ]
    with pytest.raises(RadiationGeometryV2Error):
        verify_control_volume_readback(readback)


def test_native_cv_scope_requires_partitiondomains_and_actual_topology_api_receipt() -> None:
    readback = _software_cv_readback()
    readback["evidence_scope"] = "COMSOL_NATIVE_PARTITION_DOMAINS"
    readback["topology_source"] = "GeomInfo.getAdj/getAdjOrient+GeomMeasure"
    readback["actual_partition_feature_readback"] = True
    readback["partition_source_face_ids"] = [101, 102, 103, 104, 105, 106]
    readback["partition_target_domain_ids"] = [201, 202]
    readback["boundary_ids"] = [patch["boundary_id"] for face in readback["faces"] for patch in face["patches"]]
    assert verify_control_volume_readback(readback)["native_result"] == "UNVERIFIED"
    assert verify_control_volume_readback(readback)["power_balance_tolerance"] == "NOT_FROZEN"
