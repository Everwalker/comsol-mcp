from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from tools.w23_full3d import build_case_matrix
from tools.w23_scaling_map_candidate import (
    BASE_COMMIT,
    CASE_IDS,
    CV_BOX_UM,
    SCHEMA_ID,
    TRANSITION_END_UM,
    TRANSITION_START_UM,
    _active_regions,
    _case_geometry,
    _case_record,
    build_candidate_manifest,
    evaluate_complex_map,
    evaluate_direction,
    evaluate_raw_jacobian,
    map_expression_hashes,
    render_map_expressions,
)

CANDIDATE_ROOT = Path(__file__).resolve().parents[1]


def _transition_point(g, x, u, v):
    t = (x - TRANSITION_START_UM) / (TRANSITION_END_UM - TRANSITION_START_UM)
    w = 6 * t**5 - 15 * t**4 + 10 * t**3
    eu, ev, p = g["eu"], g["ev"], g["center"]
    m = [[1 - w + w * eu[1], w * eu[2]],
         [w * ev[1], 1 - w + w * ev[2]]]
    c = [w * (eu[0] * (x - p[0]) - eu[1] * p[1] - eu[2] * p[2]),
         w * (ev[0] * (x - p[0]) - ev[1] * p[1] - ev[2] * p[2])]
    rhs = [u - c[0], v - c[1]]
    det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    assert det > 0.0
    y = (rhs[0] * m[1][1] - m[0][1] * rhs[1]) / det
    z = (m[0][0] * rhs[1] - rhs[0] * m[1][0]) / det
    return [x, y, z]


def _assert_jacobian_matches_finite_difference(point, geometry, zone, active, tol=3e-6):
    analytic = evaluate_raw_jacobian(point, geometry, zone, active)
    step = 1e-5
    for j in range(3):
        left = list(point)
        right = list(point)
        left[j] -= step
        right[j] += step
        mapped_left = evaluate_complex_map(left, geometry, zone, active)
        mapped_right = evaluate_complex_map(right, geometry, zone, active)
        for i in range(3):
            numeric = (mapped_right[i] - mapped_left[i]) / (2 * step)
            assert abs(analytic[i][j] - numeric) < tol


def test_manifest_freezes_19_cases_and_complete_zone_region_matrix():
    assert len(build_case_matrix()) == 19
    assert tuple(row["case_id"] for row in build_case_matrix()) == CASE_IDS
    manifest = build_candidate_manifest()
    assert manifest["base_commit"] == BASE_COMMIT
    assert manifest["schema_id"] == SCHEMA_ID
    assert manifest["one_factor_case_count"] == 19
    assert manifest["native_status"] == "NOT_RUN"
    assert manifest["runtime_complex_map_support"] == "UNVERIFIED"
    assert manifest["physics_coordinate_transform"] == "UNVERIFIED"
    assert manifest["solver_or_study_invoked"] is False
    assert len(manifest["cases"]) == 19
    for case in manifest["cases"]:
        assert len(case["region_maps"]) == 42
        assert case["native_domain_ids"] == "NOT_READ"
        assert case["control_volume_um"] == CV_BOX_UM
        assert case["straight_physical_fibers"] is True
        assert case["physical_material_continuation"] == ["fiber_core", "fiber_cladding", "air"]
        assert len(case["full_port_polygon_local_uv_um"]) >= 3
        assert case["minimum_local_square_halfwidth_um"] == 5.5
        assert all(row["domain_binding_status"] == "NOT_READ" for row in case["region_maps"])
        assert all(row["complex_map_property_support"] == "UNVERIFIED" for row in case["region_maps"])
    hashes = map_expression_hashes(manifest)
    assert len(hashes) == 19 * 42


def test_region_combinations_are_disjoint_and_exhaustive_per_zone():
    assert len(_active_regions(zone="global")) == 17
    assert len(_active_regions(zone="transition")) == 8
    assert len(_active_regions(zone="output_cap")) == 17
    for zone in ("global", "transition", "output_cap"):
        for active in _active_regions(zone=zone):
            assert active
            assert len(active) == len(set(active))
            assert not ({"transverse_u_minus", "transverse_u_plus"} <= set(active))
            assert not ({"transverse_v_minus", "transverse_v_plus"} <= set(active))
            assert not ({"input_axial", "output_axial"} <= set(active))


def test_map_uses_literal_raw_gradient_and_complex_profile():
    geometry = _case_geometry(_case_record(CASE_IDS[7]))
    maps = render_map_expressions(geometry, "transition", ["transverse_u_plus"])
    assert len(maps) == 3
    assert all("1-i" in expr for expr in maps)
    assert all("1.55[um]" in expr and "2[um]" in expr for expr in maps)
    assert all("sqrt(" not in expr and "norm(" not in expr.lower() for expr in maps)
    assert all("grad(" not in expr.lower() for expr in maps)
    assert all("if(" not in expr.lower() for expr in maps)
    assert "((x-19[um])/0.9[um])" in maps[0]
    assert "0.9[um]" in maps[0]


def test_published_case_matrix_rejects_compound_tilt():
    case = copy.deepcopy(_case_record(CASE_IDS[7]))
    case["receiver_transform"]["theta_z_deg"] = 0.25
    with pytest.raises(ValueError, match="compound tilt"):
        _case_geometry(case)
    case["receiver_transform"]["theta_y_deg"] = 0.5001
    case["receiver_transform"]["theta_z_deg"] = 0.0
    with pytest.raises(ValueError, match="envelope"):
        _case_geometry(case)


def test_map_rejects_opposing_or_wrong_zone_stretch_ownership():
    geometry = _case_geometry(_case_record(CASE_IDS[0]))
    with pytest.raises(ValueError, match="opposing"):
        render_map_expressions(geometry, "global", ["transverse_u_minus", "transverse_u_plus"])
    with pytest.raises(ValueError, match="output axial cap"):
        render_map_expressions(geometry, "global", ["output_axial"])
    with pytest.raises(ValueError, match="transition slab"):
        render_map_expressions(geometry, "transition", ["output_axial"])
    with pytest.raises(ValueError, match="input axial cap"):
        render_map_expressions(geometry, "output_cap", ["input_axial"])


def test_raw_jacobian_matches_finite_difference_across_all_19_cases():
    for case_id in CASE_IDS:
        geometry = _case_geometry(_case_record(case_id))
        lo_u, hi_u, lo_v, hi_v = geometry["local_bounds_uv_um"]

        point = [0.0, 7.0, -7.0]
        _assert_jacobian_matches_finite_difference(
            point, geometry, "global", ["transverse_u_plus", "transverse_v_minus"])

        x = 0.5 * (TRANSITION_START_UM + TRANSITION_END_UM)
        t = (x - TRANSITION_START_UM) / (TRANSITION_END_UM - TRANSITION_START_UM)
        w = 6 * t**5 - 15 * t**4 + 10 * t**3
        u_hi = 6.0 + w * (hi_u - 6.0)
        v_lo = -6.0 + w * (lo_v + 6.0)
        point = _transition_point(geometry, x, u_hi + 1.0, v_lo - 0.75)
        d_u = evaluate_direction(point, geometry, "transition", "transverse_u_plus")[0]
        d_v = evaluate_direction(point, geometry, "transition", "transverse_v_minus")[0]
        assert math.isclose(d_u, 1.0, abs_tol=1e-9)
        assert math.isclose(d_v, 0.75, abs_tol=1e-9)
        _assert_jacobian_matches_finite_difference(
            point, geometry, "transition", ["transverse_u_plus", "transverse_v_minus"])

        axis, eu, ev = geometry["axis"], geometry["eu"], geometry["ev"]
        p_back = [geometry["center"][i] + 1.55 * axis[i] for i in range(3)]
        u_center = 0.5 * (lo_u + hi_u)
        v_center = 0.5 * (lo_v + hi_v)
        point = [p_back[i] + axis[i] + eu[i] * (hi_u + 1.0) + ev[i] * v_center
                 for i in range(3)]
        assert math.isclose(evaluate_direction(point, geometry, "output_cap", "output_axial")[0],
                            1.0, abs_tol=1e-9)
        assert math.isclose(evaluate_direction(point, geometry, "output_cap", "transverse_u_plus")[0],
                            1.0, abs_tol=1e-9)
        _assert_jacobian_matches_finite_difference(
            point, geometry, "output_cap", ["output_axial", "transverse_u_plus"])

        point = [-22.55, 7.0, -7.0]
        assert math.isclose(evaluate_direction(point, geometry, "global", "input_axial")[0],
                            1.0, abs_tol=1e-9)
        _assert_jacobian_matches_finite_difference(
            point, geometry, "global", ["input_axial", "transverse_u_plus", "transverse_v_minus"])


def test_control_volume_remains_identity_and_invalid_distance_fails_closed():
    geometry = _case_geometry(_case_record(CASE_IDS[0]))
    for point in ([x, y, z] for x in CV_BOX_UM["x"] for y in CV_BOX_UM["y"] for z in CV_BOX_UM["z"]):
        assert evaluate_complex_map(point, geometry, "global", []) == [complex(v, 0.0) for v in point]
    with pytest.raises(ValueError, match="outside"):
        evaluate_complex_map([0.0, 9.0, 0.0], geometry, "global", ["transverse_u_plus"])


def test_manifest_map_hash_detects_detached_expression():
    manifest = build_candidate_manifest()
    map_expression_hashes(manifest)
    manifest["cases"][0]["region_maps"][0]["scaling_map_xyz"][0] += "+1[um]"
    with pytest.raises(ValueError, match="digest"):
        map_expression_hashes(manifest)


def test_java_probe_is_compile_only_and_never_claims_acceptance():
    source = CANDIDATE_ROOT / "tools/java/NativeW23ScalingMapProbe.java"
    text = source.read_text()
    assert "inspectExistingManagedModel" in text
    assert "ModelUtil" not in text
    assert "public static void main" not in text
    assert ".run()" not in text
    assert "connectServer" not in text
    assert "getStringArray(\"map\")" in text
    assert "getInfoTable(id, \"recursive\", \"all\")" in text
    assert "PROPERTY_TEXT_PRESERVED_NOT_PHYSICS_PROOF" in text
    assert "equation_view_proves_scaling_map_consumption\", false" in text
    assert "equation_view_proves_complex_map_survives\", false" in text
    assert "overall_acceptance\", \"UNVERIFIED\"" in text
