from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools.w23_deformation_mapping import (
    DeformationMappingError,
    _digest,
    build_native_mapping_configuration_dispatch,
    build_native_mapping_plan,
    compare_native_mapping_samples,
)


MODEL_REF = {"model_id": "model_123", "epoch": 2}
PROJECT_ID = "project_123"
MODEL_TAG = "ModelW23"
SOURCE_IDENTITY = {
    "solution": "sol1", "study": "std1", "computation_date": "2026-09-27T00:00:00Z",
    "computation_version": "COMSOL 6.4.0.293", "times": [0.0, 1.0, 2.0],
}


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _records():
    artifact_sha = "a" * 64
    observation = {
        "kind": "w17_observation", "observation_id": "obs_123", "model_tag": MODEL_TAG,
        "model_ref": copy.deepcopy(MODEL_REF), "revision": 7, "dataset": "dset1", "solution": "sol1",
        "source_identity": copy.deepcopy(SOURCE_IDENTITY), "artifact": {"sha256": artifact_sha},
        "sha256": artifact_sha,
    }
    checkpoint = {
        "kind": "w21stage", "checkpoint_id": "stage_123", "stage_id": "stageA",
        "model_ref": copy.deepcopy(MODEL_REF),
        "observation_ref": {"observation_id": "obs_123", "sha256": artifact_sha},
        "source_solution": "sol1", "source_dataset": "dset1", "source_study": "std1",
        "timestamp_s": 2.0,
        "sample_request": {"spec": {"expressions": ["u", "v", "w"]}},
        "solution_indices": {
            "binding_complete": True, "dataset": "dset1", "solution": "sol1",
            "solnum_pairs": [{"outer": 1, "inner": 3, "solnum": 5}],
            "parameters": {"by_pair": {"1:3": {
                "names": ["t", "ambient"], "values": [2.0, 293.15], "units": ["s", "K"],
            }}},
        },
        "field_array": {"coords": {"outer": [1]}, "units": {"expression": {"u": "m", "v": "m", "w": "m"}}},
        "status": "COMPLETE", "checkpoint_status": "CHECKPOINT_CREATED",
    }
    checkpoint["sha256"] = _digest(checkpoint)
    source = {
        "model_tag": MODEL_TAG, "component_tag": "compSolid", "geometry_tag": "geomSolid",
        "component_tags": ["compSolid", "compOptical"],
        "selection_tag": "selSolid", "selection": {"entity_dimension": 3, "entity_ids": [1, 3]},
        "mesh_frame": "material", "coordinate_unit": "m", "vector_basis": "global_xyz",
        "source_solution_tag": "sol1", "source_dataset_tag": "dset1", "source_study_tag": "std1",
        "source_solution_fingerprint": _sha(SOURCE_IDENTITY),
        "source_geometry_fingerprint": "b" * 64,
    }
    target = {
        "model_tag": MODEL_TAG, "component_tag": "compOptical", "geometry_tag": "geomOptical",
        "selection_tag": "selOptical", "selection": {"entity_dimension": 3, "entity_ids": [2]},
        "coordinate_frame": "spatial", "coordinate_unit": "m", "vector_basis": "global_xyz",
        "target_geometry_fingerprint": "c" * 64,
    }
    return checkpoint, observation, source, target


def _plan(**overrides):
    checkpoint, observation, source, target = _records()
    args = {
        "expected_model_ref": MODEL_REF, "expected_project_id": PROJECT_ID,
        "expected_revision": 7, "expected_model_tag": MODEL_TAG,
        "source_native": source, "target_native": target,
        "displacement_expressions": {"x": "u", "y": "v", "z": "w"},
    }
    args.update(overrides)
    return build_native_mapping_plan(checkpoint, observation, **args)


def test_w21_records_bind_exact_stored_solution_time_and_native_mapping():
    plan = _plan()
    assert plan["status"] == "CONFIGURATION_PLAN_ONLY"
    assert plan["native_result"] == "NOT_RUN"
    assert plan["study_or_solver_invoked"] is False
    assert plan["source"]["project_id"] == PROJECT_ID
    assert plan["source"]["revision"] == 7
    assert (plan["source"]["outer"], plan["source"]["inner"], plan["source"]["solnum"]) == (1, 3, 5)
    assert plan["source"]["time_s"] == 2.0
    assert plan["source"]["outer_parameters"] == [{"name": "ambient", "value": 293.15, "unit": "K"}]
    adapter = plan["native_adapter"]
    assert adapter["operation"] == "GeneralExtrusion"
    assert adapter["mesh_search_method"] == "usetol"
    assert adapter["outside_source"].startswith("NaN")
    assert adapter["deformation_expressions"]["x"].startswith(
        "compOptical.w23defmap(withsol('sol1',compSolid.u,setind(t,3)")
    assert adapter["operator_component"] == "compOptical"
    assert adapter["source_expression_component"] == "compSolid"
    assert adapter["expression_order"].startswith("targetComponent.operator(withsol")
    assert plan["mapping_complete"] is False


@pytest.mark.parametrize("mutation,match", [
    ("checkpoint_hash", "integrity"),
    ("foreign_model", "ModelRef"),
    ("stale_revision", "stale"),
    ("bad_observation_ref", "ObservationRef"),
    ("artifact_hash", "artifact hash"),
    ("source_identity", "solution identity"),
    ("wrong_solution_tag", "solution tag"),
    ("wrong_dataset_tag", "dataset tag"),
    ("wrong_study_tag", "study association"),
    ("bad_source_frame", "material reference frame"),
    ("bad_target_frame", "spatial frame"),
    ("wrong_vector_basis", "global xyz"),
    ("wrong_coordinate_unit", "metres"),
    ("wrong_selection_dimension", "3D domain"),
    ("duplicate_selection_ids", "unique positive"),
    ("missing_expression", "did not sample"),
    ("wrong_displacement_unit", "metres"),
    ("ambiguous_stored_time", "exactly one"),
    ("wrong_stored_time", "exactly one"),
    ("wrong_solution_time_identity", "registered native solution time index"),
    ("project_conflict", "project attribution"),
])
def test_mapping_plan_fails_closed_on_provenance_time_frame_and_selection(mutation, match):
    checkpoint, observation, source, target = _records()
    kwargs = {
        "expected_model_ref": MODEL_REF, "expected_project_id": PROJECT_ID,
        "expected_revision": 7, "expected_model_tag": MODEL_TAG,
        "source_native": source, "target_native": target,
        "displacement_expressions": {"x": "u", "y": "v", "z": "w"},
    }
    if mutation == "checkpoint_hash":
        checkpoint["stage_id"] = "tampered"
    elif mutation == "foreign_model":
        observation["model_ref"] = {"model_id": "other", "epoch": 1}
    elif mutation == "stale_revision":
        kwargs["expected_revision"] = 8
    elif mutation == "bad_observation_ref":
        checkpoint["observation_ref"]["observation_id"] = "obs_other"
        checkpoint["sha256"] = _digest({k: v for k, v in checkpoint.items() if k != "sha256"})
    elif mutation == "artifact_hash":
        observation["artifact"]["sha256"] = "d" * 64
    elif mutation == "source_identity":
        source["source_solution_fingerprint"] = "e" * 64
    elif mutation == "wrong_solution_tag":
        source["source_solution_tag"] = "sol2"
    elif mutation == "wrong_dataset_tag":
        source["source_dataset_tag"] = "dset2"
    elif mutation == "wrong_study_tag":
        source["source_study_tag"] = "std2"
    elif mutation == "bad_source_frame":
        source["mesh_frame"] = "spatial"
    elif mutation == "bad_target_frame":
        target["coordinate_frame"] = "material"
    elif mutation == "wrong_vector_basis":
        target["vector_basis"] = "local_xyz"
    elif mutation == "wrong_coordinate_unit":
        source["coordinate_unit"] = "mm"
    elif mutation == "wrong_selection_dimension":
        source["selection"]["entity_dimension"] = 2
    elif mutation == "duplicate_selection_ids":
        source["selection"]["entity_ids"] = [1, 1]
    elif mutation == "missing_expression":
        kwargs["displacement_expressions"] = {"x": "u", "y": "v", "z": "not_sampled"}
    elif mutation == "wrong_displacement_unit":
        checkpoint["field_array"]["units"]["expression"]["w"] = "mm"
        checkpoint["sha256"] = _digest({k: v for k, v in checkpoint.items() if k != "sha256"})
    elif mutation == "ambiguous_stored_time":
        indices = checkpoint["solution_indices"]
        indices["solnum_pairs"].append({"outer": 1, "inner": 4, "solnum": 6})
        indices["parameters"]["by_pair"]["1:4"] = {
            "names": ["t"], "values": [2.0], "units": ["s"],
        }
        checkpoint["sha256"] = _digest({k: v for k, v in checkpoint.items() if k != "sha256"})
    elif mutation == "wrong_stored_time":
        checkpoint["solution_indices"]["parameters"]["by_pair"]["1:3"]["values"][0] = 1.5
        checkpoint["sha256"] = _digest({k: v for k, v in checkpoint.items() if k != "sha256"})
    elif mutation == "wrong_solution_time_identity":
        observation["source_identity"]["times"][2] = 3.0
        source["source_solution_fingerprint"] = _sha(observation["source_identity"])
    elif mutation == "project_conflict":
        checkpoint["project_id"] = "project_other"
        checkpoint["sha256"] = _digest({k: v for k, v in checkpoint.items() if k != "sha256"})

    with pytest.raises(DeformationMappingError, match=match):
        build_native_mapping_plan(checkpoint, observation, **kwargs)


def test_component_qualified_source_fields_are_normalized_and_foreign_component_is_rejected():
    plan = _plan(displacement_expressions={"x": "compSolid.u", "y": "v", "z": "w"})
    assert plan["source"]["displacement_expressions"]["x"] == "u"
    assert plan["source"]["sampled_expressions"]["x"] == "compSolid.u"
    with pytest.raises(DeformationMappingError, match="non-source component"):
        _plan(displacement_expressions={"x": "compOptical.u", "y": "v", "z": "w"})


def test_physics_qualified_fields_keep_the_source_component_and_material_frame():
    checkpoint, observation, source, target = _records()
    checkpoint["sample_request"]["spec"]["expressions"] = ["solid.u", "solid.v", "solid.w"]
    checkpoint["field_array"]["units"]["expression"] = {
        "solid.u": "m", "solid.v": "m", "solid.w": "m",
    }
    checkpoint["sha256"] = _digest({key: value for key, value in checkpoint.items() if key != "sha256"})
    plan = build_native_mapping_plan(
        checkpoint, observation,
        expected_model_ref=MODEL_REF, expected_project_id=PROJECT_ID, expected_revision=7,
        expected_model_tag=MODEL_TAG, source_native=source, target_native=target,
        displacement_expressions={"x": "solid.u", "y": "solid.v", "z": "solid.w"},
    )
    assert plan["native_adapter"]["source_frame"] == "material"
    assert plan["native_adapter"]["deformation_expressions"]["x"].startswith(
        "compOptical.w23defmap(withsol('sol1',compSolid.solid.u,")


def test_java_adapter_is_configuration_only_and_qualifies_both_expression_contexts():
    from pathlib import Path

    fixture = (Path(__file__).resolve().parents[1]
               / "tools/java/NativeW23DeformationMappingFixture.java").read_text(encoding="utf-8")
    assert '"configure".equals(String.valueOf(args.get("phase")))' in fixture
    assert "model.sol(sourceSolution).getPVals()" in fixture
    assert 'sourceComponent + "." + sourceExpression' in fixture
    assert 'return targetComponent + "." + operator + "(" + sourceValue + ")";' in fixture
    assert 'result.put("study_or_solver_invoked", false)' in fixture
    assert 'requiredEntityIds(args.get("source_selection_entity_ids")' in fixture
    assert 'requiredEntityIds(args.get("target_selection_entity_ids")' in fixture
    assert 'sameSet(sourceEntities' in fixture and 'sameSet(targetEntities' in fixture
    assert ".run(" not in fixture
    assert 'if (!"material".equals(sourceFrame))' in fixture


def test_mapping_adapter_dispatch_is_bound_to_exact_registered_model_and_native_selections():
    plan = _plan()
    dispatch = build_native_mapping_configuration_dispatch(
        plan, source_artifact="artifact-native-w23-mapping", request_id="mapping-request",
        idempotency_key="mapping-idem", expected_revision=7)
    assert dispatch["operation"] == "operation_call"
    assert dispatch["execution"] == {
        "project_id": PROJECT_ID, "model_ref": MODEL_REF, "expected_revision": 7,
        "request_id": "mapping-request", "idempotency_key": "mapping-idem"}
    call = dispatch["arguments"]
    assert call["operation_id"] == "code.execute_java"
    java = call["arguments"]
    assert java["entrypoint"] == "NativeW23DeformationMappingFixture#run"
    args = java["arguments"]
    assert args["source_selection_entity_ids"] == [1, 3]
    assert args["target_selection_entity_ids"] == [2]
    assert args["source_expressions"] == {"x": "u", "y": "v", "z": "w"}
    assert args["source_units"] == {"x": "m", "y": "m", "z": "m"}
    assert "displacement_m" not in args and "field_values" not in args
    assert dispatch["native_result"] == "NOT_RUN"
    assert dispatch["study_or_solver_invoked"] is False


def test_mapping_adapter_dispatch_refuses_stale_revision_or_unregistered_entity_ids():
    plan = _plan()
    with pytest.raises(DeformationMappingError, match="revision"):
        build_native_mapping_configuration_dispatch(
            plan, source_artifact="artifact", request_id="r", idempotency_key="i",
            expected_revision=8)
    plan["source"]["source_selection_entity_ids"] = []
    with pytest.raises(DeformationMappingError, match="entity IDs"):
        build_native_mapping_configuration_dispatch(
            plan, source_artifact="artifact", request_id="r", idempotency_key="i",
            expected_revision=7)


def _raw_receipts():
    plan = _plan()
    source = plan["source"]
    binding = {key: source[key] for key in (
        "project_id", "model_ref", "model_tag", "checkpoint_id", "observation_ref",
        "source_solution", "source_dataset", "outer", "inner", "solnum", "time_s",
        "source_solution_fingerprint",
    )}
    coordinates = [[0.0, 1e-6, 2e-6], [2e-6, 3e-6, 4e-6], [0.0, 0.0, 0.0]]
    common = {"evidence_scope": "COMSOL_NATIVE_RAW", "coordinate_unit": "m", "value_unit": "m"}
    source_raw = {**common, "binding": copy.deepcopy(binding),
                  "coordinates_m": copy.deepcopy(coordinates), "route": "direct_dataset_point_evaluation",
                  "source_expression_route": "native_source_component_expression",
                  "displacement_m": [[1e-9, 2e-9, 3e-9], [0.0, 0.0, 0.0], [1e-12, 2e-12, 3e-12]]}
    mapped_raw = {**common, "binding": copy.deepcopy(binding),
                  "coordinates_m": copy.deepcopy(coordinates), "route": "general_extrusion_destination_evaluation",
                  "operator_tag": plan["native_adapter"]["operator_tag"],
                  "displacement_m": copy.deepcopy(source_raw["displacement_m"])}
    return plan, source_raw, mapped_raw


def test_held_out_residual_requires_independent_native_routes_and_exact_bindings():
    plan, source_raw, mapped_raw = _raw_receipts()
    result = compare_native_mapping_samples(
        plan, source_raw, mapped_raw, absolute_tolerance_m=1e-10, relative_tolerance=1e-3)
    assert result["status"] == "SOFTWARE_GATE_PASS"
    assert result["native_result"] == "NOT_RUN"
    assert result["independent_routes"] is True
    assert result["mapping_complete"] is False


@pytest.mark.parametrize("mutation,match", [
    ("circular_source", "circular"),
    ("wrong_target_op", "operator tag"),
    ("wrong_time", "time_s"),
    ("foreign_solution", "source_solution"),
    ("coordinate_mismatch", "coordinates must match"),
    ("wrong_unit", "both use metres"),
    ("outside_nan", "nonfinite/native-out-of-domain"),
    ("count_mismatch", "counts differ"),
])
def test_held_out_comparison_rejects_circular_or_mismatched_native_receipts(mutation, match):
    plan, source_raw, mapped_raw = _raw_receipts()
    if mutation == "circular_source":
        source_raw["source_expression_route"] = "general_extrusion"
    elif mutation == "wrong_target_op":
        mapped_raw["operator_tag"] = "otherop"
    elif mutation == "wrong_time":
        mapped_raw["binding"]["time_s"] = 1.0
    elif mutation == "foreign_solution":
        source_raw["binding"]["source_solution"] = "sol2"
    elif mutation == "coordinate_mismatch":
        mapped_raw["coordinates_m"][1][0] += 1e-15
    elif mutation == "wrong_unit":
        source_raw["value_unit"] = "mm"
    elif mutation == "outside_nan":
        mapped_raw["displacement_m"][0][1] = float("nan")
    elif mutation == "count_mismatch":
        mapped_raw["displacement_m"].pop()
    with pytest.raises(DeformationMappingError, match=match):
        compare_native_mapping_samples(
            plan, source_raw, mapped_raw, absolute_tolerance_m=1e-10, relative_tolerance=1e-3)
