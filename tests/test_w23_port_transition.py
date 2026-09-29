from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest

from tools.w23_full3d import build_case_matrix
from tools.w23_port_transition import (
    PortTransitionError,
    _frame_for_case,
    build_port_transition_plan,
    classify_port_transition_point,
    classify_port_transition_points,
    verify_port_transition_plan,
)
from tools.w23_radiation_geometry_v2 import (
    RadiationGeometryV2Error,
    build_pml_partition_plan,
    canonical_radiation_geometry_v2,
    verify_radiation_geometry_v2,
)


def _refresh_plan_hash(plan: dict) -> None:
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps({key: value for key, value in plan.items() if key != "plan_sha256"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   allow_nan=False).encode("utf-8")
    ).hexdigest()


def _shrink_port_polygon(case: dict) -> None:
    vertices = case["port_apertures"]["output"]["vertices_global_xyz_um"]
    centroid = [sum(vertex[axis] for vertex in vertices) / len(vertices)
                for axis in range(3)]
    for vertex in vertices:
        for axis in range(3):
                vertex[axis] = centroid[axis] + 0.999 * (vertex[axis] - centroid[axis])


def _xyz_from_local(case: dict, s: float, u: float, w: float = 0.0) -> list[float]:
    frame = case["receiver_frame"]
    center = frame["origin_xyz_um"]
    return [center[i] + s * frame["a_axis_xyz"][i]
            + u * frame["b_axis_xyz"][i] + w * frame["w_axis_xyz"][i]
            for i in range(3)]


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_port_transition_plan()


def test_recomputable_candidate_covers_exact_19_case_matrix(plan: dict) -> None:
    receipt = verify_port_transition_plan(plan)
    assert receipt["case_count"] == 19
    assert receipt["native_result"] == "NOT_RUN"
    assert receipt["native_support"] == "NOT_IMPLEMENTED"
    assert receipt["pml_compatibility"] == "UNVERIFIED"
    assert [row["case_id"] for row in plan["cases"]] == [
        row["case_id"] for row in build_case_matrix()]
    assert sum(row["curved_side_centerline"]["status"] == "ANALYTIC_BIARC"
               for row in plan["cases"]) == 4


def test_beta_zero_cases_have_no_arcs_and_keep_coincident_planes(plan: dict) -> None:
    for case in plan["cases"]:
        if case["curved_side_centerline"]["status"] == "NO_ARC_BETA_ZERO":
            assert case["curved_side_centerline"]["per_edge"]["plus"]["arcs"] == []
            assert case["curved_side_centerline"]["per_edge"]["minus"]["arcs"] == []
            assert case["piecewise_partition"]["region_count"] == 27
            assert case["geometry_measures"]["transition_physical_volume_um3"] == 0.0
            assert all("biarc" not in piece["piece_id"]
                       for side in case["side_boundary_charts"]
                       for piece in side["pieces"])


def test_full_numeric_port_polygon_is_recomputed_and_unchanged(plan: dict) -> None:
    recipe = canonical_radiation_geometry_v2()
    for row, source_case in zip(plan["cases"], build_case_matrix()):
        old = build_pml_partition_plan(recipe, source_case["receiver_transform"])
        output = row["port_apertures"]["output"]
        input_port = row["port_apertures"]["input"]
        assert output["vertices_global_xyz_um"] == old["port_air_apertures"]["output"][
            "polygon_vertices_global_xyz_um"]
        assert output["area_um2"] == old["port_air_apertures"]["output"][
            "analytic_polygon_area_um2"]
        assert input_port["vertices_global_xyz_um"] == old["port_air_apertures"]["input"][
            "polygon_vertices_global_xyz_um"]
        assert input_port["area_um2"] == old["port_air_apertures"]["input"][
            "analytic_polygon_area_um2"]
        assert row["port_apertures"]["output_polygon_preserved_from_v2_exactly"] is True
        assert output["core_capture_aperture_is_separate"] is True
        assert len(output["vertices_global_xyz_um"]) >= 4


def test_biarc_tangency_radius_tube_and_analytic_clearance_certificates(plan: dict) -> None:
    angular = [row for row in plan["cases"]
               if row["curved_side_centerline"]["status"] == "ANALYTIC_BIARC"]
    assert len(angular) == 4
    for case in angular:
        centerline = case["curved_side_centerline"]
        for side in ("minus", "plus"):
            edge = centerline["per_edge"][side]
            assert len(edge["arcs"]) == 2
            first, second = edge["arcs"]
            assert first["end_su_um"] == second["start_su_um"]
            assert first["phi_end_rad"] == second["phi_start_rad"]
            assert abs(first["radius_um"] - 57.2981430563) < 1e-8
            assert abs(second["radius_um"] - 19.0995022278) < 1e-8
            assert first["radius_um"] > 2.0 and second["radius_um"] > 2.0
        tube = case["normal_tube_certificate"]
        assert tube["real_normal_chart_jacobian_lower_bound"] > 0
        assert tube["graph_offset_separation_lower_factor"] > 0
        assert tube["strict_complex_stretch_determinant_abs_lower_bound"] > 0
        assert tube["opposite_side_graph_separation_lower_um"] > 12.0
        proof = tube["normal_chart_injectivity_proof"]
        assert proof["arc_pair_external_tangency_error_um"] < 1e-12
        assert proof["start_to_end_normal_ray_s_gap_lower_um"] > 0
        assert proof["arc_pair_offset_radius_sum_constant_for_all_d"] is True
        clearance = case["fiber_clearance_certificate"]
        assert clearance["minimum_clearance_um"] >= clearance["required_minimum_um"]
        bounds = case["finite_roi_bounds_certificate"]
        assert bounds["transition_tube_s_range_um"][1] <= 2e-9
        assert bounds["minimum_margin_outside_cv_x_plus_um"] > 0
        assert bounds["transition_and_output_cap_x_ranges_um"][
            "transition_tube_x_range_um"][0] > 19.0
        assert len(case["seam_certificate"]) == 6
        assert all(row["left_chart_distance_equals_right_chart_distance"]
                   and row["complex_displacement_equal_for_all_d_in_0_D"]
                   for row in case["seam_certificate"])
        measures = case["geometry_measures"]
        beta = abs(case["receiver_frame"]["beta_rad"])
        assert abs(measures["transition_physical_cross_section_area_um2"]
                   - 0.5 * 12.0 / math.cos(beta)) < 1e-10
        assert abs(measures["transition_physical_volume_um3"]
                   - measures["transition_physical_cross_section_area_um2"] * 12.0) < 1e-10
        assert abs(measures["transition_side_pml_cross_section_area_um2_all_sides"]
                   - 2.0 * 2.0 * sum(arc["arc_length_um"] for arc in
                     case["curved_side_centerline"]["per_edge"]["plus"]["arcs"])) < 1e-10


def test_right_handed_axis_specific_frames_are_not_swapped(plan: dict) -> None:
    for case in plan["cases"]:
        frame = case["receiver_frame"]
        if case["factor"] == "receiver_theta_y_deg":
            assert frame["lateral_axis"] == "z"
            assert frame["w_axis_xyz"] == [0.0, -1.0, 0.0]
            assert abs(frame["beta_deg"] + case["receiver_transform"]["theta_y_deg"]) < 1e-12
        elif case["factor"] == "receiver_theta_z_deg":
            assert frame["lateral_axis"] == "y"
            assert frame["w_axis_xyz"] == [0.0, 0.0, 1.0]
            assert abs(frame["beta_deg"] - case["receiver_transform"]["theta_z_deg"]) < 1e-12


def test_piece_ownership_covers_faces_edges_corners_and_width_intersections(plan: dict) -> None:
    for case in plan["cases"]:
        partition = case["piecewise_partition"]
        rows = partition["regions"]
        ids = [row["region_id"] for row in rows]
        assert len(ids) == len(set(ids)) == partition["unique_region_id_count"]
        assert partition["region_owner_count_is_pointwise"] is True
        assert all(row["predicate"]["distance_ownership"]["zero_distance_owner"]
                   == "physical" and "owner_count" not in row for row in rows)
        assert partition["region_count"] == len(rows)
        assert partition["face_edge_corner_counts"]["physical"] == 1
        assert partition["face_edge_corner_counts"]["face"] > 0
        assert partition["face_edge_corner_counts"]["edge"] > 0
        assert partition["face_edge_corner_counts"]["corner"] > 0
        assert partition["adjacency_count"] == len(partition["adjacency"])
        assert any(row["width_owner"] == "w_plus" and
                   row["lateral_owner"] != "none" for row in rows)
        if case["curved_side_centerline"]["status"] == "ANALYTIC_BIARC":
            assert not any(row["lateral_owner"].endswith(":biarc_1") and
                           row["longitudinal_owner"] != "none" for row in rows)
            assert any(row["lateral_owner"].endswith(":global_side_plane") and
                       row["longitudinal_owner"] == "input_axial" for row in rows)
            assert any(row["lateral_owner"].endswith(":output_local_plane") and
                       row["longitudinal_owner"] == "output_axial" for row in rows)


def test_executable_classifier_owns_zero_seams_faces_edges_corners_and_swept_roi(
        plan: dict) -> None:
    case = next(row for row in plan["cases"]
                if row["curved_side_centerline"]["status"] == "ANALYTIC_BIARC"
                and row["receiver_frame"]["beta_rad"] > 0)
    u0 = float(case["curved_side_centerline"]["edge_u0_by_sigma_um"]["plus"])
    port_b = float(plan["frozen_parameters_um"]["homogeneous_backing"])
    axial_d = float(plan["frozen_parameters_um"]["axial_pml_thickness"])
    width_h = float(plan["frozen_parameters_um"]["full_air_port_halfwidth"])
    side_d = float(plan["frozen_parameters_um"]["side_and_width_pml_thickness"])
    arc1, arc2 = case["curved_side_centerline"]["per_edge"]["plus"]["arcs"]
    seam_points = []
    seam_expected = []
    for arc, endpoint, phi in (
        (arc1, arc1["start_su_um"], arc1["phi_start_rad"]),
        (arc1, arc1["end_su_um"], arc1["phi_end_rad"]),
        (arc2, arc2["end_su_um"], arc2["phi_end_rad"]),
    ):
        normal = (-math.sin(phi), math.cos(phi))
        tangent = (math.cos(phi), math.sin(phi))
        seam = (endpoint[0] + side_d * normal[0],
                endpoint[1] + side_d * normal[1])
        before = (seam[0] - 1e-5 * tangent[0], seam[1] - 1e-5 * tangent[1])
        after = (seam[0] + 1e-5 * tangent[0], seam[1] + 1e-5 * tangent[1])
        seam_points.extend(_xyz_from_local(case, q[0], q[1])
                           for q in (before, seam, after))

    seam_expected = [
        "plus:global_side_plane", "plus:biarc_1", "plus:biarc_1",
        "plus:biarc_1", "plus:biarc_2", "plus:biarc_2",
        "plus:biarc_2", "plus:output_local_plane", "plus:output_local_plane",
    ]

    # The final cap point is beyond the legacy global lateral plane for this
    # positive tilt, yet remains in the new true-axis swept ROI.
    swept_cap = _xyz_from_local(case, port_b + axial_d, u0)
    lateral_index = case["receiver_frame"]["lateral_global_index"]
    assert abs(swept_cap[lateral_index]) > width_h

    input_anchor = case["piecewise_partition"]["distance_family_anchors"][
        "input_axial"]["anchor_xyz_um"]
    input_outward = case["piecewise_partition"]["distance_family_anchors"][
        "input_axial"]["outward_unit_xyz"]
    lateral_unit = [0.0, 0.0, 0.0]
    lateral_unit[lateral_index] = 1.0
    w_axis = case["receiver_frame"]["w_axis_xyz"]
    input_corner = [input_anchor[i] + axial_d * input_outward[i]
                    + (width_h + side_d) * lateral_unit[i]
                    + (width_h + side_d) * w_axis[i] for i in range(3)]

    points = [
        _xyz_from_local(case, 0.75, 0.0),                      # physical interior
        _xyz_from_local(case, 1.0, u0),                        # lateral d=0 -> physical
        _xyz_from_local(case, 1.0, u0 + side_d),               # lateral d=D -> PML face
        *seam_points,                                           # both sides and exact point of each seam
        _xyz_from_local(case, 1.0, u0 + 1.0, width_h + 1.0),  # PML edge
        _xyz_from_local(case, port_b + axial_d, u0 + side_d,
                        width_h + side_d),                     # PML corner
        swept_cap,                                               # true-axis cap, old clip exceeds
        input_corner,                                            # input/global-side/width corner
        _xyz_from_local(case, 1.0, 0.0, width_h),               # width d=0 -> physical
        input_anchor,                                             # input axial d=0 -> physical
        _xyz_from_local(case, port_b, 0.0),                      # output axial d=0 -> physical
    ]
    results = classify_port_transition_points(plan, case["case_id"], points)
    assert [result["owner_count"] for result in results] == [1] * len(points)
    assert results[0]["classification"] == "physical"
    assert results[1]["owners"]["lateral"] == "none"
    assert results[1]["classification"] == "physical"
    assert results[2]["owners"]["lateral"] == "plus:output_local_plane"
    assert results[2]["classification"] == "face"
    assert [results[i]["owners"]["lateral"] for i in range(3, 12)] == seam_expected
    assert results[12]["classification"] == "edge"
    assert results[13]["classification"] == "corner"
    assert results[14]["owners"] == {"longitudinal": "output_axial",
                                      "lateral": "none", "width": "none"}
    assert results[15]["owners"] == {"longitudinal": "input_axial",
                                      "lateral": "plus:global_side_plane",
                                      "width": "w_plus"}
    assert results[15]["classification"] == "corner"
    assert results[16]["owners"]["width"] == "none"
    assert results[17]["owners"]["longitudinal"] == "none"
    assert results[18]["owners"]["longitudinal"] == "none"
    assert results[17]["classification"] == results[18]["classification"] == "physical"

    with pytest.raises(PortTransitionError, match="outside the finite ROI"):
        classify_port_transition_point(
            plan, case["case_id"], _xyz_from_local(case, 1.0, u0 + side_d + 1e-6))
    with pytest.raises(PortTransitionError, match="exactly three"):
        classify_port_transition_point(plan, case["case_id"], [0.0, 1.0])

    aligned = next(row for row in plan["cases"]
                   if row["curved_side_centerline"]["status"] == "NO_ARC_BETA_ZERO")
    aligned_u = float(aligned["curved_side_centerline"]["edge_u0_by_sigma_um"]["plus"])
    straight = classify_port_transition_point(
        plan, aligned["case_id"], _xyz_from_local(aligned, 1.0, aligned_u + side_d))
    assert straight["owners"]["lateral"] == "plus:coincident_global_output_plane"
    assert straight["classification"] == "face"


def test_executable_classifier_recomputes_all_four_single_axis_tilt_signs(plan: dict) -> None:
    angular = [row for row in plan["cases"]
               if row["curved_side_centerline"]["status"] == "ANALYTIC_BIARC"]
    assert len(angular) == 4
    observed = set()
    for case in angular:
        frame = case["receiver_frame"]
        point = _xyz_from_local(case, 0.75, 0.0, 0.0)
        result = classify_port_transition_point(plan, case["case_id"], point)
        assert result["owner_count"] == 1
        assert result["classification"] == "physical"
        observed.add((case["factor"], math.copysign(1, frame["beta_rad"])))
    assert observed == {
        ("receiver_theta_y_deg", -1.0), ("receiver_theta_y_deg", 1.0),
        ("receiver_theta_z_deg", -1.0), ("receiver_theta_z_deg", 1.0),
    }


def test_two_axis_tilt_is_rejected() -> None:
    case = copy.deepcopy(build_case_matrix()[0])
    case["factor"] = "receiver_theta_y_deg"
    case["receiver_transform"]["theta_y_deg"] = 0.5
    case["receiver_transform"]["theta_z_deg"] = 0.5
    with pytest.raises(PortTransitionError, match="two-axis"):
        _frame_for_case(case)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row["curved_side_centerline"]["per_edge"]["plus"]["arcs"][0].__setitem__(
            "signed_curvature_per_um", -row["curved_side_centerline"]["per_edge"]["plus"][
                "arcs"][0]["signed_curvature_per_um"]),
        lambda row: row["seam_certificate"][0]["point_su_um"].__setitem__(0, 1.0),
        lambda row: row["seam_certificate"][0]["outward_normal_su"].__setitem__(1, 0.0),
        lambda row: row["side_boundary_charts"][0]["pieces"].pop(1),
        lambda row: row["side_boundary_charts"][0]["pieces"].append(
            copy.deepcopy(row["side_boundary_charts"][0]["pieces"][1])),
        lambda row: row["piecewise_partition"]["regions"].pop(),
        _shrink_port_polygon,
        lambda row: row["curved_side_centerline"]["per_edge"]["plus"]["arcs"][1].__setitem__(
            "radius_um", 1.0),
        lambda row: row["receiver_frame"]["w_axis_xyz"].__setitem__(2,
            -row["receiver_frame"]["w_axis_xyz"][2]),
    ],
    ids=["wrong-curvature-sign", "wrong-seam-point", "wrong-seam-normal",
         "missing-chart-piece", "duplicate-chart-piece", "missing-region", "shrunk-port",
         "radius-under-pml-thickness", "wrong-right-handed-frame"],
)
def test_tampered_candidate_fails_after_attacker_refreshes_self_hash(plan: dict, mutation) -> None:
    candidate = copy.deepcopy(plan)
    tilted = next(row for row in candidate["cases"]
                  if row["curved_side_centerline"]["status"] == "ANALYTIC_BIARC")
    mutation(tilted)
    _refresh_plan_hash(candidate)
    with pytest.raises(PortTransitionError, match="recomputed frozen"):
        verify_port_transition_plan(candidate)


def test_new_schema_is_not_accepted_by_legacy_v2_native_adapter(plan: dict) -> None:
    with pytest.raises(RadiationGeometryV2Error, match="v2 schema"):
        verify_radiation_geometry_v2(plan)
