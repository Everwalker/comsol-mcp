import copy
import hashlib
import json

import pytest

from tools.w23_port_transition import build_port_transition_plan
from tools.w23_port_transition_science import (
    PortTransitionAdapterError,
    SCHEMA_ID,
    build_port_transition_dispatch,
    build_port_transition_native_recipes,
    verify_port_transition_native_recipe,
)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def candidate():
    plan = build_port_transition_plan()
    return plan, build_port_transition_native_recipes(plan)


def test_all_frozen_cases_lower_to_exact_nonempty_cells_and_full_air_ports(candidate):
    plan, recipes = candidate
    assert len(recipes) == len(plan["cases"]) == 19
    assert {row["case_id"] for row in recipes} == {row["case_id"] for row in plan["cases"]}
    assert len({row["case_id"] for row in recipes}) == 19

    tilted = 0
    plan_by_case = {row["case_id"]: row for row in plan["cases"]}
    for recipe in recipes:
        assert recipe["schema_id"] == SCHEMA_ID
        assert recipe["construction"]["native_support"] == "CANDIDATE_NOT_RUN"
        assert recipe["construction"]["geometry_compatibility"] == "UNVERIFIED"
        assert recipe["construction"]["pml_compatibility"] == "UNVERIFIED"
        assert recipe["construction"]["study_or_solver_invoked"] is False
        assert recipe["port_contract"]["full_air_aperture_required"] is True
        assert recipe["port_contract"]["capture_aperture_separate"] is True
        frozen_case = plan_by_case[recipe["case_id"]]
        assert recipe["port_contract"]["input_area_um2"] == pytest.approx(
            frozen_case["port_apertures"]["input"]["area_um2"], abs=1e-12)
        assert recipe["port_contract"]["output_area_um2"] == pytest.approx(
            frozen_case["port_apertures"]["output"]["area_um2"], abs=1e-12)
        assert recipe["port_contract"]["input_port_feature_tag"] == "portIn3d"
        assert recipe["port_contract"]["output_port_feature_tag"] == "portOut3d"
        assert recipe["port_contract"]["capture_selection_tag"] == "sel3dOutputCoreCapture"
        assert recipe["profiles"] and recipe["region_cells"] and recipe["pml_groups"]
        if abs(recipe["frame"]["beta_rad"]) > 1e-12:
            tilted += 1
            assert any(p["kind"] == "circular_arc"
                       for profile in recipe["profiles"]
                       for p in profile["boundary_primitives"])
        cell_by_id = {cell["region_id"]: cell for cell in recipe["region_cells"]}
        group_owners = []
        for group in recipe["pml_groups"]:
            assert 1 <= len(group["directions"]) <= 3
            for owner in group["region_ids"]:
                group_owners.append(owner)
                assert len(group["directions"]) == cell_by_id[owner]["expected_active_directions"]
        assert len(group_owners) == len(set(group_owners))
        assert set(group_owners) == {cell["region_id"] for cell in recipe["region_cells"]
                                     if cell["owner_tuple"] != ["none", "none", "none"]}
        profile_ids = {profile["profile_id"] for profile in recipe["profiles"]}
        assert {pid for cell in recipe["region_cells"] for pid in cell["profile_ids"]} == profile_ids
    assert tilted == 4


def test_recipe_verifier_recomputes_cells_instead_of_accepting_rehashed_mutation(candidate):
    _, recipes = candidate
    altered = copy.deepcopy(next(row for row in recipes if abs(row["frame"]["beta_rad"]) > 1e-12))
    arc = next(p for profile in altered["profiles"] for p in profile["boundary_primitives"]
               if p["kind"] == "circular_arc")
    arc["radius_um"] += 0.25
    altered["recipe_sha256"] = _digest({k: v for k, v in altered.items() if k != "recipe_sha256"})
    with pytest.raises(PortTransitionAdapterError, match="recomputed frozen case cells"):
        verify_port_transition_native_recipe(altered)


def test_v2_recipe_is_not_accepted_by_transition_v1_dispatch(candidate):
    _, recipes = candidate
    old = copy.deepcopy(recipes[0])
    old["schema_id"] = "urn:comsol-mcp:w23:radiation-geometry-v2"
    with pytest.raises(PortTransitionAdapterError, match="schema"):
        verify_port_transition_native_recipe(old)


def test_managed_dispatch_binds_full_recipe_and_rejects_missing_identity(candidate):
    _, recipes = candidate
    recipe = recipes[0]
    model_ref = {"schema_version": 1, "model_tag": "w23_owned_model", "generation": 1,
                 "server_instance_id": "server-1", "session_id": "session-1"}
    request = build_port_transition_dispatch(recipe, source_artifact="NativeW23PortTransitionV1.java",
        project_id="project-1", model_ref=model_ref, model_tag="w23_owned_model", revision=17,
        request_id="w23-transition-req-1", idempotency_key="w23-transition-idem-1")
    args = request["arguments"]["arguments"]["arguments"]
    assert args["phase"] == "apply_transition_case"
    assert args["recipe_sha256"] == recipe["recipe_sha256"]
    assert args["managed_identity"]["expected_revision"] == 17
    assert args["managed_identity"]["model_ref"] == model_ref
    assert args["managed_identity"]["session_id"] == "session-1"
    assert args["managed_identity"]["server_instance_id"] == "server-1"
    assert request["execution"]["session_id"] == "session-1"
    assert request["study_or_solver_invoked"] is False
    with pytest.raises(PortTransitionAdapterError, match="exact server identity fields"):
        build_port_transition_dispatch(recipe, source_artifact="NativeW23PortTransitionV1.java",
            project_id="project-1", model_ref={"schema_version": 1, "model_tag": "w23_owned_model",
                "generation": 1, "session_id": "session-1", "server_instance_id": "server-1",
                "extra": "not-a-model-ref"},
            model_tag="w23_owned_model", revision=17,
            request_id="w23-transition-req-2", idempotency_key="w23-transition-idem-2")


def test_port_polygon_frame_is_right_handed_and_native_status_stays_unverified(candidate):
    _, recipes = candidate
    observed_tangent_offset = False
    for recipe in recipes:
        frame = recipe["frame"]
        a, b, w = (frame[key] for key in ("a_axis_xyz", "b_axis_xyz", "w_axis_xyz"))
        cross = [a[1] * b[2] - a[2] * b[1],
                 a[2] * b[0] - a[0] * b[2],
                 a[0] * b[1] - a[1] * b[0]]
        assert cross == pytest.approx(w, abs=1e-12)
        port = recipe["port_contract"]
        delta = [port["output_center_global_xyz_um"][i] - frame["origin_xyz_um"][i]
                 for i in range(3)]
        assert sum(delta[i] * a[i] for i in range(3)) == pytest.approx(0.0, abs=2e-6)
        observed_tangent_offset |= sum(value * value for value in delta) > 1e-8
        assert recipe["control_volume_contract"]["cv_native_readback"] == "NOT_RUN"
    assert observed_tangent_offset
