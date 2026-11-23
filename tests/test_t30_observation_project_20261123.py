"""Registered-observation project provenance controls with synthetic data."""
import hashlib
import json
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._observation_store import observation_context, register_observation, resolve_observation


class MemoryRecords:
    def __init__(self, record):
        self.record = record

    def get_operation(self, producer):
        return {"status": "SUCCEEDED"} if producer == "producer" else None

    def get_metadata(self, table, key):
        assert table == "artifacts"
        return self.record if key == "obs-test" else None


@pytest.mark.parametrize("context_project,expected_error", [
    ("project-a", None), ("project-b", "PROJECT_IDENTITY_MISMATCH"),
    (None, "PROJECT_IDENTITY_REQUIRED"),
])
def test_observation_must_belong_to_admitted_project(tmp_path, monkeypatch, context_project, expected_error):
    payload = json.dumps({"metadata": {"dataset": "dset"}, "values": [1.0, 2.0]}).encode()
    (tmp_path / "observation.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    model_ref = {"session_id": "s", "server_instance_id": "server", "model_tag": "model", "generation": 1}
    solution = {"solution": "solution"}
    record = {"kind": "w17_observation", "producer": "producer", "project_id": "project-a",
              "model_tag": "model", "model_ref": model_ref, "revision": 0, "solution": "solution",
              "source_identity": solution, "sha256": digest, "artifact": {"file_path": "observation.json"}}
    worker = SimpleNamespace(project_root=tmp_path)
    monkeypatch.setattr("comsol_mcp._observation_store.solution_identity", lambda *_: solution)
    with observation_context(MemoryRecords(record), model_ref, 0, "consumer", project_id=context_project):
        if expected_error is None:
            found, sample = resolve_observation(worker, "model", {"observation_id": "obs-test", "sha256": digest})
            assert found["project_id"] == context_project
            assert sample["values"] == [1.0, 2.0]
        else:
            with pytest.raises(ExecutionContractError) as exc:
                resolve_observation(worker, "model", {"observation_id": "obs-test", "sha256": digest})
            assert exc.value.code == expected_error


@pytest.mark.parametrize(
    "context_project,caller_project,expected_scope",
    [
        ("project-a", "project-b", "REGISTERED_NATIVE_REFINEMENT"),
        ("project-b", "project-a", "PROJECT_IDENTITY_MISMATCH"),
        (None, "project-a", "PROJECT_IDENTITY_REQUIRED"),
    ],
)
def test_convergence_resolution_inherits_only_outer_project_context(
    tmp_path, monkeypatch, context_project, caller_project, expected_scope
):
    from comsol_mcp import _convergence_store as convergence
    from comsol_mcp._g3_w20_validation import validate_convergence

    worker = SimpleNamespace(project_root=tmp_path)
    store = OperationStore(tmp_path / "operations.sqlite")
    model_ref = {
        "session_id": "session-test",
        "server_instance_id": "server-test",
        "model_tag": "model",
        "generation": 1,
    }
    source_identity = {
        "solution": "solution-test",
        "computation_version": "6.4.0.293",
        "times": [0.0, 0.01, 0.03, 0.1],
    }
    monkeypatch.setattr(
        "comsol_mcp._observation_store.solution_identity",
        lambda *_args: source_identity,
    )
    producer, reused = store.begin(
        request_id="request-producer",
        idempotency_key="producer-key",
        request_hash="producer-hash",
        operation="result.evaluate",
    )
    assert not reused
    store.finish(producer["operation_id"], status="SUCCEEDED", result={"success": True})

    with observation_context(
        store, model_ref, 0, producer["operation_id"], project_id="project-a"
    ):
        observation_ref = register_observation(
            worker,
            "model",
            {
                "status": {"ok": True},
                "solution": "solution-test",
                "dataset": "dataset-test",
                "values": [[[[303.0, 304.0]]]],
                "field_array": {"axes": ["expression", "outer", "inner", "point"]},
                "expressions": ["T"],
            },
        )

    refs = []
    for index in range(3):
        case = {
            "kind": "w20_convergence",
            "convergence_id": f"conv-{index}",
            "producer": producer["operation_id"],
            "model_ref": model_ref,
            "revision": 0,
            "observation_ref": observation_ref,
            "oracle": "transient_sine_diffusion",
            "oracle_results": {},
            "source_identity": source_identity,
            "error": 0.1 / (index + 1),
            "mesh": f"mesh-{index}",
            "elements": 100 * (index + 1),
            "max_element_volume": 0.001 / (index + 1),
            "mesh_size_metric": (0.001 / (index + 1)) ** (1 / 3),
            "dofs": 100 * (index + 1),
            "tolerance": 1e-3 / (index + 1),
            "tlist": "0 .01 .03 .1",
            "sample_points": [],
            "coordinate_unit": "m",
        }
        case["sha256"] = convergence.digest(case)
        store.persist_artifact(case["convergence_id"], case)
        refs.append({"convergence_id": case["convergence_id"], "sha256": case["sha256"]})

    try:
        with observation_context(
            store, model_ref, 0, producer["operation_id"], project_id=context_project
        ):
            result = validate_convergence(
                worker,
                "model",
                {"cases": refs, "project_id": caller_project},
            )
        assert result["scope"] == expected_scope
        if expected_scope == "REGISTERED_NATIVE_REFINEMENT":
            assert result["cases_count"] == 3
            assert all(case["observation_ref"] == observation_ref for case in result["registered_cases"])
    finally:
        store.close()
