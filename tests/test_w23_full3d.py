from __future__ import annotations

from pathlib import Path

import pytest

from tools.w23_full3d import (
    Full3DRecipeError,
    bind_case_matrix,
    build_full3d_case_dispatch,
    build_full3d_fixture_dispatch,
    build_case_matrix,
    canonical_full3d_recipe,
    expected_receiver_frame,
    verify_recipe,
    verify_case_result,
    verify_deformation_target_readback,
    verify_receiver_port_section,
)


def test_full3d_recipe_keeps_the_vector_fixture_true_3d_and_names_rotated_port_geometry():
    recipe = canonical_full3d_recipe()
    receipt = verify_recipe(recipe)
    assert receipt["status"] == "SOFTWARE_RECIPE_VALID"
    assert receipt["native_result"] == "NOT_RUN"
    assert recipe["geometry"]["space_dimension"] == 3
    assert recipe["optics"]["field_components"] == ["Ex", "Ey", "Ez", "Hx", "Hy", "Hz"]
    assert recipe["geometry"]["domain_xmax_um"] > recipe["geometry"]["output_port_x_um"]
    angle = recipe["geometry"]["angular_receiver"]
    assert "same transformed output axis" in angle["port_section"]
    assert angle["selection_readback"] == ["entity IDs", "area", "AABB-centroid", "per-face point and normal"]


def test_angle_cases_are_planned_with_the_same_rigid_transform_selection_route():
    cases = build_case_matrix()
    angle_rows = [row for row in cases if row["factor"].startswith("receiver_theta_")]
    assert len(angle_rows) == 4
    assert {row["factor"] for row in angle_rows} == {"receiver_theta_y_deg", "receiver_theta_z_deg"}
    assert {row["value"] for row in angle_rows} == {-0.5, 0.5}
    assert all(row["native_result"] == "NOT_RUN" for row in angle_rows)
    assert all("rotated_output_fiber_and_local_numeric_port_selection" in row["application_profile"]
               for row in angle_rows)


def test_recipe_cannot_drop_rotated_port_selection_or_domain_clearance_without_refreezing():
    recipe = canonical_full3d_recipe()
    recipe["geometry"]["domain_xmax_um"] = recipe["geometry"]["output_port_x_um"]
    # A changed recipe must also have a recomputed digest; the semantic gate
    # remains independently testable after a valid new digest is supplied.
    import hashlib
    import json
    recipe["recipe_sha256"] = hashlib.sha256(json.dumps(
        {key: value for key, value in recipe.items() if key != "recipe_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with pytest.raises(Full3DRecipeError, match="angular receiver"):
        verify_recipe(recipe)


def test_full3d_java_builder_has_rotation_and_local_section_readback_but_no_study_submission():
    java = (Path(__file__).resolve().parents[1] / "tools/java/NativeW23Full3DFixture.java").read_text(encoding="utf-8")
    assert 'rotate(geom, "rotCoreOutY", "coreOut"' in java
    assert 'rotate(geom, "rotCoreOutZ", "rotCoreOutY"' in java
    assert 'rotate(geom, "rotCladShellOutY", "cladShellOut"' in java
    assert 'rotate(geom, "rotCladShellOutZ", "rotCladShellOutY"' in java
    assert 'selection.set("axis", axis)' in java
    assert 'selection.set("pos", base)' in java
    assert 'selection.set("entitydim", 2)' in java
    assert 'selection.set("condition", "inside")' in java
    assert 'getArea()' in java
    assert 'getBoundingBox()' in java
    assert 'faceParamRange(entity)' in java
    assert 'faceNormal(entity, params)' in java
    assert 'native output port face normal is not parallel' in java
    assert 'case "receiver_theta_y_deg"' in java
    assert 'case "receiver_theta_z_deg"' in java
    assert '"sel3dDeformationTarget", "Explicit"' in java
    assert '"air remainder", "PML shell"' in java
    assert 'outside the frozen full-3D one-factor matrix' in java
    apply_start = java.index("private static Map<String, Object> applyCase")
    geometry_update = java.index("geom(GEOMETRY).run();", apply_start)
    selection_update = java.index('configureLocalPortCylinder(portSelection, frame.axis', apply_start)
    mesh_update = java.index('mesh("mesh3d").run();', apply_start)
    assert geometry_update < selection_update < mesh_update
    assert 'study_or_solver_invoked", false' in java
    assert '.study("std3d").run' not in java
    assert 'model.sol().run' not in java


def test_bma_producer_probe_uses_one_isolated_output_bma_study_and_full_sequence_run():
    java = (Path(__file__).resolve().parents[1] / "tools/java/NativeW23Full3DFixture.java").read_text(encoding="utf-8")
    prepare = java[java.index("private static Map<String, Object> prepareBmaOutputProbe"):
                   java.index("private static Map<String, Object> runBmaOutputProbe")]
    run = java[java.index("private static Map<String, Object> runBmaOutputProbe"):
               java.index("private static Map<String, Object> readManagedIdentity")]
    assert 'study().create(BMA_PROBE_STUDY)' in prepare
    assert 'feature().create(BMA_PROBE_STEP, "BoundaryModeAnalysis")' in prepare
    assert 'configureBma(bma, "2")' in prepare
    assert 'createAutoSequences("sol")' in prepare
    assert 'solverSequenceReadback(sequence, BMA_PROBE_STUDY)' in prepare
    assert 'solutionState(sequence, sequenceTags[0])' in prepare
    assert '"Variables", "Eigenvalue", "StoreSolution"' in java
    assert "sequence.runAll();" in run
    assert 'BMA_OUTPUT_PROBE_SOLVER_SEQUENCE_RETURNED_TWO_SOLUTION_ROWS' in run
    assert 'study_tag", BMA_PROBE_STUDY' in run
    assert 'post_solve_solution_state' in run
    assert 'field_mapping_status", "UNVERIFIED"' in run
    assert 'basis_ordinal_mapping", "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"' in run


def _managed_binding():
    return {
        "project_id": "project-w23",
        "model_ref": {"schema_version": 1, "session_id": "session-1",
                      "server_instance_id": "epoch-1", "model_tag": "model1", "generation": 3},
        "model_tag": "model1",
        "revision": 17,
        "experiment_id": "experiment-w23-full3d",
    }


def test_full3d_builder_and_case_routes_are_revision_bound_configuration_only_calls():
    recipe = canonical_full3d_recipe()
    binding = _managed_binding()
    build = build_full3d_fixture_dispatch(
        recipe, source_artifact="artifact-native-w23-full3d",
        project_id=binding["project_id"], model_ref=binding["model_ref"],
        model_tag=binding["model_tag"], revision=binding["revision"],
        request_id="request-build", idempotency_key="idem-build")
    assert build["operation"] == "operation_call"
    assert build["execution"] == {
        "project_id": binding["project_id"], "session_id": "session-1",
        "model_ref": binding["model_ref"],
        "expected_revision": binding["revision"], "request_id": "request-build",
        "idempotency_key": "idem-build"}
    wrapped = build["arguments"]["arguments"]
    assert build["arguments"]["operation_id"] == "code.execute_java"
    assert wrapped["entrypoint"] == "NativeW23Full3DFixture#run"
    assert wrapped["arguments"]["recipe_sha256"] == recipe["recipe_sha256"]
    assert build["study_or_solver_invoked"] is False
    assert build["native_result"] == "NOT_RUN"

    plan = bind_case_matrix(recipe, **binding)
    angle = next(row for row in plan["cases"] if row["factor"] == "receiver_theta_z_deg"
                 and row["value"] == 0.5)
    route = build_full3d_case_dispatch(
        plan, angle, source_artifact="artifact-native-w23-full3d",
        request_id="request-angle", idempotency_key="idem-angle")
    java_args = route["arguments"]["arguments"]["arguments"]
    assert java_args["phase"] == "apply_case"
    assert java_args["case"]["case_identity_sha256"] == angle["case_identity_sha256"]
    assert java_args["case"]["factor"] == "receiver_theta_z_deg"
    assert route["execution"]["project_id"] == angle["project_id"]
    assert route["execution"]["session_id"] == "session-1"
    assert route["execution"]["expected_revision"] == angle["expected_revision"]
    assert route["study_or_solver_invoked"] is False
    assert route["native_result"] == "NOT_RUN"


def test_full3d_case_dispatch_rejects_identity_tamper_or_case_from_another_plan():
    binding = _managed_binding()
    recipe = canonical_full3d_recipe()
    plan = bind_case_matrix(recipe, **binding)
    case = next(row for row in plan["cases"] if row["factor"] == "receiver_theta_y_deg"
                and row["value"] == -0.5)
    tampered = dict(case)
    tampered["value"] = -7.0
    with pytest.raises(Full3DRecipeError, match="member"):
        build_full3d_case_dispatch(plan, tampered, source_artifact="artifact",
                                   request_id="r", idempotency_key="i")
    foreign = bind_case_matrix(
        recipe, project_id="project-other", model_ref=binding["model_ref"],
        model_tag=binding["model_tag"], revision=binding["revision"],
        experiment_id=binding["experiment_id"])
    with pytest.raises(Full3DRecipeError, match="member"):
        build_full3d_case_dispatch(foreign, case, source_artifact="artifact",
                                   request_id="r", idempotency_key="i")


def test_full3d_recipe_binds_ordered_bma_ports_and_shared_mode_frequency():
    recipe = canonical_full3d_recipe()
    recipe["optics"]["configured_study_steps"][0]["port"] = "2"
    import hashlib
    import json
    recipe["recipe_sha256"] = hashlib.sha256(json.dumps(
        {key: value for key, value in recipe.items() if key != "recipe_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with pytest.raises(Full3DRecipeError, match="ordered input BMA"):
        verify_recipe(recipe)


@pytest.mark.parametrize("factor,value,expected_axis,expected_center", [
    ("receiver_theta_y_deg", 0.5,
     [0.9999619230641713, 0.0, -0.008726535498373935],
     [19.9994288460, 0.0, -0.1308980325]),
    ("receiver_theta_y_deg", -0.5,
     [0.9999619230641713, 0.0, 0.008726535498373935],
     [19.9994288460, 0.0, 0.1308980325]),
    ("receiver_theta_z_deg", 0.5,
     [0.9999619230641713, 0.008726535498373935, 0.0],
     [19.9994288460, 0.1308980325, 0.0]),
    ("receiver_theta_z_deg", -0.5,
     [0.9999619230641713, -0.008726535498373935, 0.0],
     [19.9994288460, -0.1308980325, 0.0]),
])
def test_angle_frame_uses_independently_pinned_right_handed_rigid_transform(
        factor, value, expected_axis, expected_center):
    frame = expected_receiver_frame(factor, value)
    assert frame["rotation_order"] == "right-handed global +y then +z"
    assert frame["pivot_xyz_um"] == [5.0, 0.0, 0.0]
    assert frame["axis_xyz"] == pytest.approx(expected_axis, abs=1e-12)
    assert frame["center_xyz_um"] == pytest.approx(expected_center, abs=1e-9)
    assert frame["cladding_radius_um"] == 2.5


def _synthetic_port_section(case):
    """Software fixture for testing rejection logic; it is not native evidence."""
    frame = case["receiver_transform"]
    axis = frame["axis_xyz"]
    center = frame["center_xyz_um"]
    radius = frame["cladding_radius_um"]
    half = frame["selection_half_length_um"]
    base = [c - half * n for c, n in zip(center, axis)]
    extents = [radius * (1 - n * n) ** 0.5 for n in axis]
    bounds = [coordinate for c, extent in zip(center, extents)
              for coordinate in (c - extent, c + extent)]
    area = 3.141592653589793 * radius * radius
    return {
        "evidence_scope": "COMSOL_NATIVE_GEOMETRY_READBACK",
        "tag": "sel3dOutputPort", "selection_type": "Cylinder", "selection_geometry": "geom3d",
        "entity_dimension": 2, "entity_ids": [41], "coordinate_unit": "um", "normal_basis": "global_xyz",
        "selection_condition": "inside", "selection_axis_type": "cartesian",
        "selection_axis_xyz": axis, "selection_center_um": center, "selection_base_um": base,
        "selection_radius_um": radius + frame["selection_radial_margin_um"],
        "selection_bottom_um": 0.0, "selection_top_um": 2 * half,
        "area_um2": area, "expected_circle_area_um2": area, "area_relative_error": 0.0,
        "area_tolerance": "3% geometry-measure approximation gate", "centroid_um": center,
        "expected_transformed_center_um": center, "centroid_error_um": 0.0,
        "bounding_box_um": bounds, "native_face_oriented_axis_sign": 1,
        "faces": [{"boundary_id": 41, "point_um": center, "unit_normal_xyz": axis, "axis_dot": 1.0}],
    }


def _synthetic_deformation_target():
    """Software fixture only; not a native geometry receipt."""
    return {
        "evidence_scope": "COMSOL_NATIVE_GEOMETRY_READBACK",
        "tag": "sel3dDeformationTarget", "selection_type": "Explicit",
        "component_tag": "comp3d", "geometry_tag": "geom3d", "entity_dimension": 3,
        "entity_ids": [2, 4, 6], "coordinate_frame": "spatial", "coordinate_unit": "m",
        "vector_basis": "global_xyz",
        "selected_material_domains": ["core input", "core output", "cladding input", "cladding output", "ball lens"],
        "excluded_domains": ["air remainder", "PML shell"],
        "entity_ids_derived_from": "native geometry result selections after geometry build",
        "deformation_mapping": "W21 source displacement field; no caller arrays",
    }


def test_receiver_section_gate_recomputes_transform_area_aabb_and_raw_face_normals():
    case = next(row for row in build_case_matrix()
                if row["factor"] == "receiver_theta_y_deg" and row["value"] == 0.5)
    synthetic = _synthetic_port_section(case)
    result = verify_receiver_port_section(case, synthetic)
    assert result["status"] == "SOFTWARE_NATIVE_GEOMETRY_READBACK_VALID"
    assert result["native_result"] == "NOT_RUN"
    assert result["study_or_solver_invoked"] is False
    wrong_axis = dict(synthetic, selection_axis_xyz=[1.0, 0.0, 0.0])
    with pytest.raises(Full3DRecipeError, match="selection axis"):
        verify_receiver_port_section(case, wrong_axis)
    wrong_area = dict(synthetic, area_um2=synthetic["area_um2"] * 1.1)
    with pytest.raises(Full3DRecipeError, match="independent recomputation"):
        verify_receiver_port_section(case, wrong_area)
    wrong_normal = {**synthetic, "faces": [{**synthetic["faces"][0], "axis_dot": -1.0}]}
    with pytest.raises(Full3DRecipeError, match="orientation summary"):
        verify_receiver_port_section(case, wrong_normal)


def test_w21_deformation_target_is_native_optical_material_selection_without_air_or_pml():
    synthetic = _synthetic_deformation_target()
    receipt = verify_deformation_target_readback(synthetic)
    assert receipt["status"] == "SOFTWARE_NATIVE_TARGET_SELECTION_VALID"
    assert receipt["native_result"] == "NOT_RUN"
    assert receipt["entity_ids"] == [2, 4, 6]
    wrong = {**synthetic, "excluded_domains": ["air remainder"]}
    with pytest.raises(Full3DRecipeError, match="exclude air and PML"):
        verify_deformation_target_readback(wrong)


def test_case_result_requires_native_local_port_readback_and_keeps_science_not_run():
    binding = _managed_binding()
    recipe = canonical_full3d_recipe()
    plan = bind_case_matrix(recipe, **binding)
    case = next(row for row in plan["cases"] if row["factor"] == "receiver_theta_z_deg"
                and row["value"] == 0.5)
    response = {key: case[key] for key in (
        "project_id", "model_ref", "model_tag", "experiment_id", "case_id",
        "case_identity_sha256", "recipe_sha256")}
    response.update({"native_result": "NOT_RUN", "study_or_solver_invoked": False,
                     "receiver_port_section": _synthetic_port_section(case),
                     "deformation_target_selection": _synthetic_deformation_target()})
    verified = verify_case_result(plan, case, response, current_revision=binding["revision"])
    assert verified["status"] == "SOFTWARE_CASE_IDENTITY_PASS"
    assert verified["receiver_port_section"]["status"] == "SOFTWARE_NATIVE_GEOMETRY_READBACK_VALID"
    assert verified["deformation_target_selection"]["status"] == "SOFTWARE_NATIVE_TARGET_SELECTION_VALID"
    assert verified["native_result"] == "NOT_RUN"
    response["receiver_port_section"]["selection_axis_xyz"] = [1.0, 0.0, 0.0]
    with pytest.raises(Full3DRecipeError, match="selection axis"):
        verify_case_result(plan, case, response, current_revision=binding["revision"])
