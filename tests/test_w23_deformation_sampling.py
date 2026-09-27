from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.w23_deformation_mapping import _digest
from tools.w23_deformation_mapping import build_native_mapping_plan
from tools.w23_deformation_sampling import (
    DeformationSamplingError,
    build_mapping_sampling_dispatch,
    build_registered_mapping_sample_contract,
    reconcile_mapping_sample_response,
)
POINTS = [
    [0.0, 0.0, 0.0], [1e-6, 0.0, 0.0],
    [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6],
]


MODEL_REF = {"model_id": "model_123", "epoch": 2}
PROJECT_ID = "project_123"
MODEL_TAG = "ModelW23"


def _records():
    artifact_sha = "a" * 64
    solution_identity = {
        "solution": "sol1", "study": "std1", "computation_date": "2026-09-27T00:00:00Z",
        "computation_version": "COMSOL 6.4.0.293", "times": [0.0, 1.0, 2.0],
    }
    observation = {
        "kind": "w17_observation", "observation_id": "obs_123", "model_tag": MODEL_TAG,
        "model_ref": copy.deepcopy(MODEL_REF), "revision": 7, "dataset": "dset1", "solution": "sol1",
        "source_identity": copy.deepcopy(solution_identity), "artifact": {"sha256": artifact_sha},
        "sha256": artifact_sha,
    }
    checkpoint = {
        "kind": "w21stage", "checkpoint_id": "stage_123", "stage_id": "stageA",
        "model_ref": copy.deepcopy(MODEL_REF),
        "observation_ref": {"observation_id": "obs_123", "sha256": artifact_sha},
        "source_solution": "sol1", "source_dataset": "dset1", "source_study": "std1",
        "timestamp_s": 2.0, "sample_request": {"spec": {"expressions": ["u", "v", "w"]}},
        "solution_indices": {
            "binding_complete": True, "dataset": "dset1", "solution": "sol1",
            "solnum_pairs": [{"outer": 1, "inner": 3, "solnum": 5}],
            "parameters": {"by_pair": {"1:3": {
                "names": ["t", "ambient"], "values": [2.0, 293.15], "units": ["s", "K"],
            }}},
        },
        "field_array": {"coords": {"outer": [1]},
                        "units": {"expression": {"u": "m", "v": "m", "w": "m"}}},
        "status": "COMPLETE", "checkpoint_status": "CHECKPOINT_CREATED",
    }
    checkpoint["sha256"] = _digest(checkpoint)
    source = {
        "model_tag": MODEL_TAG, "component_tag": "compSolid", "geometry_tag": "geomSolid",
        "component_tags": ["compSolid", "compOptical"], "selection_tag": "selSolid",
        "selection": {"entity_dimension": 3, "entity_ids": [1, 3]},
        "mesh_frame": "material", "coordinate_unit": "m", "vector_basis": "global_xyz",
        "source_solution_tag": "sol1", "source_dataset_tag": "dset1", "source_study_tag": "std1",
        "source_solution_fingerprint": _digest(solution_identity),
        "source_geometry_fingerprint": "b" * 64,
    }
    target = {
        "model_tag": MODEL_TAG, "component_tag": "compOptical", "geometry_tag": "geomOptical",
        "selection_tag": "selOptical", "selection": {"entity_dimension": 3, "entity_ids": [2]},
        "coordinate_frame": "spatial", "coordinate_unit": "m", "vector_basis": "global_xyz",
        "target_geometry_fingerprint": "c" * 64,
    }
    return checkpoint, observation, source, target


def _plan():
    checkpoint, observation, source, target = _records()
    return build_native_mapping_plan(
        checkpoint, observation, expected_model_ref=MODEL_REF,
        expected_project_id=PROJECT_ID, expected_revision=7,
        expected_model_tag=MODEL_TAG, source_native=source, target_native=target,
        displacement_expressions={"x": "u", "y": "v", "z": "w"})


def _sample_contract():
    checkpoint, _observation, source_native, target_native = _records()
    checkpoint["selection"] = {"points": copy.deepcopy(POINTS), "coordinate_unit": "m"}
    checkpoint["sample_request"]["points"] = copy.deepcopy(POINTS)
    checkpoint["coordinate_unit"] = "m"
    checkpoint["sha256"] = _digest({key: value for key, value in checkpoint.items() if key != "sha256"})
    plan = _plan()
    contract = build_registered_mapping_sample_contract(
        plan, checkpoint, source_native=source_native, target_native=target_native)
    return plan, contract


def _raw(contract, role, displacement, *, route, expression_route, operator_tag=None):
    selection = contract["native_selections"][role]
    binding = contract["expected_binding"]
    result = {
        "evidence_scope": "COMSOL_NATIVE_RAW",
        "sample_contract_id": contract["sample_contract_id"],
        "coordinate_sha256": contract["coordinate_sha256"],
        "sample_count": len(POINTS),
        "coordinates_m": copy.deepcopy(POINTS),
        "coordinate_unit": "m", "value_unit": "m",
        "binding": {key: binding[key] for key in (
            "project_id", "model_ref", "model_tag", "checkpoint_id", "observation_ref",
            "source_solution", "source_dataset", "outer", "inner", "solnum", "time_s",
            "source_solution_fingerprint")},
        "route": route, "source_expression_route": expression_route,
        "selection": selection["tag"], "selection_geometry": selection["geometry"],
        "selection_entity_dimension": 3, "selection_entity_ids": selection["entity_ids"],
        "geometry_frame": selection["frame"], "dataset": binding["source_dataset"],
        "expressions": [contract["direct_expressions" if role == "source" else "mapped_expressions"][axis]
                        for axis in ("x", "y", "z")],
        "real_solution_selection": str(binding["solnum"]),
        "outer_solution_selection": str(binding["outer"]),
        "complex_readback": True,
        "displacement_real_m": copy.deepcopy(displacement),
        "displacement_imag_m": [[0.0, 0.0, 0.0] for _ in POINTS],
    }
    if operator_tag is not None:
        result["operator_tag"] = operator_tag
    return result


def test_registered_native_sampler_contract_binds_w21_points_selection_and_exact_indices():
    plan, contract = _sample_contract()
    assert contract["status"] == "READY_FOR_ONE_MANAGED_READBACK_CALL"
    assert contract["native_result"] == "NOT_RUN"
    assert contract["study_or_solver_invoked"] is False
    assert contract["sample_count"] == 4
    assert contract["coordinates_m"] == POINTS
    assert contract["native_selections"]["source"]["entity_ids"] == [1, 3]
    assert contract["native_selections"]["target"]["entity_ids"] == [2]
    assert "setind(t,3)" in contract["direct_expressions"]["x"]
    assert contract["operator_tag"] == plan["native_adapter"]["operator_tag"]
    assert contract["expected_binding"]["revision"] == 7


@pytest.mark.parametrize("mutation,match", [
    ("checkpoint_points", "hash"),
    ("request_points", "differ"),
    ("coplanar", "non-coplanar"),
    ("duplicate", "duplicate"),
    ("wrong_source_selection", "selection"),
])
def test_registered_sample_contract_fails_closed_on_checkpoint_and_selection_changes(mutation, match):
    checkpoint, _observation, source_native, target_native = _records()
    checkpoint["selection"] = {"points": copy.deepcopy(POINTS), "coordinate_unit": "m"}
    checkpoint["sample_request"]["points"] = copy.deepcopy(POINTS)
    checkpoint["coordinate_unit"] = "m"
    if mutation == "checkpoint_points":
        checkpoint["selection"]["points"][0][0] = 1e-9
    elif mutation == "request_points":
        checkpoint["sample_request"]["points"][0][0] = 1e-9
    elif mutation == "coplanar":
        flat = [[0.0, 0.0, 0.0], [1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [1e-6, 1e-6, 0.0]]
        checkpoint["selection"]["points"] = flat
        checkpoint["sample_request"]["points"] = copy.deepcopy(flat)
    elif mutation == "duplicate":
        checkpoint["selection"]["points"][1] = copy.deepcopy(checkpoint["selection"]["points"][0])
        checkpoint["sample_request"]["points"] = copy.deepcopy(checkpoint["selection"]["points"])
    elif mutation == "wrong_source_selection":
        source_native["selection_tag"] = "selOther"
    if mutation != "checkpoint_points":
        checkpoint["sha256"] = _digest({key: value for key, value in checkpoint.items() if key != "sha256"})
    plan = _plan()
    with pytest.raises(DeformationSamplingError, match=match):
        build_registered_mapping_sample_contract(
            plan, checkpoint, source_native=source_native, target_native=target_native)


def test_native_dispatch_is_single_project_bound_code_action_with_original_revision():
    _plan_value, contract = _sample_contract()
    dispatch = build_mapping_sampling_dispatch(
        contract, source_artifact="artifact_w23_sampler", request_id="sample_req",
        idempotency_key="sample_idem", expected_revision=7)
    assert dispatch["operation"] == "operation_call"
    assert dispatch["execution"]["project_id"] == "project_123"
    assert dispatch["execution"]["model_ref"] == {"model_id": "model_123", "epoch": 2}
    assert dispatch["execution"]["expected_revision"] == 7
    assert dispatch["arguments"]["arguments"]["entrypoint"] == "NativeW23DeformationMappingSampler#run"
    assert dispatch["arguments"]["arguments"]["arguments"]["phase"] == "sample_mapping"
    assert dispatch["arguments"]["arguments"]["arguments"]["contract"]["sample_contract_id"] == contract["sample_contract_id"]
    with pytest.raises(DeformationSamplingError, match="revision"):
        build_mapping_sampling_dispatch(
            contract, source_artifact="artifact_w23_sampler", request_id="sample_req",
            idempotency_key="sample_idem", expected_revision=8)


def test_native_sample_reconciliation_preserves_complex_data_and_checks_cleanup_and_selection():
    plan, contract = _sample_contract()
    values = [[1e-9, 2e-9, 3e-9], [2e-9, 3e-9, 4e-9], [3e-9, 4e-9, 5e-9], [4e-9, 5e-9, 6e-9]]
    source = _raw(contract, "source", values,
                  route="direct_dataset_point_evaluation",
                  expression_route="native_source_component_expression")
    mapped = _raw(contract, "target", values,
                  route="general_extrusion_destination_evaluation",
                  expression_route="destination_component_general_extrusion",
                  operator_tag=plan["native_adapter"]["operator_tag"])
    readback = {
        "sample_contract_id": contract["sample_contract_id"],
        "coordinate_sha256": contract["coordinate_sha256"],
        "native_result": "COMSOL_NATIVE_RAW", "study_or_solver_invoked": False,
        "cleanup": {"removed": True, "cleanup_failed": False},
        "source": source, "mapped": mapped,
    }
    result = reconcile_mapping_sample_response(
        plan, contract, readback, absolute_tolerance_m=1e-10, relative_tolerance=1e-3)
    assert result["status"] == "SOFTWARE_GATE_PASS"
    assert result["native_result"] == "COMSOL_NATIVE_RAW"
    assert result["mapping_complete"] is False
    assert result["full_w23_acceptance"] == "NOT_RUN"

    for changed, match in (
        ({**readback, "cleanup": {"removed": False, "cleanup_failed": True}}, "Interp"),
        ({**readback, "mapped": {**mapped, "selection_entity_ids": [99]}}, "selection"),
        ({**readback, "source": {**source, "displacement_imag_m": []}}, "imaginary"),
        ({**readback, "native_result": "SOFTWARE_ONLY"}, "software output"),
    ):
        with pytest.raises(DeformationSamplingError, match=match):
            reconcile_mapping_sample_response(
                plan, contract, changed, absolute_tolerance_m=1e-10, relative_tolerance=1e-3)


def test_java_sampler_is_an_owned_no_solve_interpolation_call_with_fail_closed_cleanup():
    source = (Path(__file__).resolve().parents[1]
             / "tools/java/NativeW23DeformationMappingSampler.java").read_text(encoding="utf-8")
    assert '"sample_mapping".equals' in source
    assert 'create(tags[0], "Interp")' in source and 'create(tags[1], "Interp")' in source
    assert 'feature.set("coorderr", "on")' in source
    assert 'feature.set("ext", 0.0)' in source
    assert 'feature.set("outersolnum", outer)' in source
    assert 'model.sol(solution).getPVals()' in source
    assert 'model.result().numerical().remove(tags[1])' in source
    assert 'model.result().numerical().remove(tags[0])' in source
    assert 'model.study(' not in source
    assert 'solver(' not in source
    assert 'study_or_solver_invoked", false' in source
