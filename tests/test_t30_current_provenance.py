"""Registered observation controls for exact project, model, and solution binding."""
from __future__ import annotations

from pathlib import Path

import pytest

from comsol_mcp import _observation_store
from comsol_mcp._g3_ops import dispatch
from comsol_mcp._observation_store import observation_context, register_observation
from comsol_mcp._operation_store import OperationStore


class SyntheticWorker:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.dataset_solution = "sol-a"
        self.dataset_error = None
        self.probe_calls = []

    def client(self):
        return self

    def model(self, _tag):
        worker = self

        class Tagged:
            def __init__(self, entries):
                self.entries = entries

            def tags(self):
                return list(self.entries)

            def get(self, tag):
                return self.entries[tag]

        class Dataset:
            def getType(self):
                return "Solution"

            def properties(self):
                return ["solution", "comp", "geom"] if worker.dataset_solution else ["comp", "geom"]

            def getString(self, name):
                worker.probe_calls.append(name)
                if name == "solution" and worker.dataset_error is not None:
                    raise worker.dataset_error
                return {"solution": worker.dataset_solution, "comp": "comp-a", "geom": "geom-a"}[name]

        class Datasets:
            def tags(self):
                return ["dataset-a"]

            def get(self, tag):
                assert tag == "dataset-a"
                return Dataset()

        class Results:
            def dataset(self):
                return Datasets()

        class Model:
            def result(self):
                return Results()

            def sol(self):
                return Tagged({"sol-a": object(), "sol-b": object()})

            def modelNode(self):
                worker.probe_calls.append("modelNode")
                return Tagged({"comp-a": object()})

            def component(self, tag):
                assert tag == "comp-a"
                return type("Component", (), {"geom": lambda self: Tagged({"geom-a": object()})})()

        return Model()


def test_registered_observation_requires_matching_project_model_and_solution(tmp_path, monkeypatch):
    project_root = tmp_path / "project-a"
    project_root.mkdir()
    worker = SyntheticWorker(project_root)
    store = OperationStore(tmp_path / "operations.sqlite")
    producer, reused = store.begin(
        request_id="producer-request",
        idempotency_key="producer-key",
        request_hash="producer-hash",
        operation="result.evaluate",
    )
    assert reused is False
    store.finish(producer["operation_id"], status="SUCCEEDED", result={"success": True})

    model_ref = {
        "session_id": "synthetic-session",
        "server_instance_id": "synthetic-server",
        "model_tag": "model-a",
        "generation": 3,
    }
    solution_identity = {
        "solution": "sol-a",
        "study": "study-a",
        "computation_version": "synthetic-runtime",
        "times": [0.0],
    }
    monkeypatch.setattr(
        _observation_store,
        "solution_identity",
        lambda *_args: dict(solution_identity),
    )

    try:
        with observation_context(
            store, model_ref, 8, producer["operation_id"], project_id="project-a"
        ):
            observation_ref = register_observation(worker, "model-a", {
                "status": {"ok": True},
                "solution": "sol-a",
                "dataset": "dataset-a",
                "metadata": {"dataset": "dataset-a"},
                "values": [300.0, 301.0],
                "field_array": {"axes": ["expression", "point"]},
                "expressions": ["T"],
            })

        def validate(*, project_id="project-a", ref=model_ref, revision=8, criteria=None):
            with observation_context(store, ref, revision, "consumer-operation", project_id=project_id):
                return dispatch("validate.solution", worker, "model-a", {
                    "observation_ref": observation_ref,
                    "solution": {"dataset": "dataset-a"},
                    "criteria": criteria or {"range": [0.0, 400.0]},
                })

        accepted = validate()
        assert accepted["status"] == "PASS"
        assert accepted["execution_status"] == "PASS"
        assert accepted["numerical_verification_status"] == "PASS"
        assert accepted["physical_validation_status"] == "UNVERIFIED"
        assert accepted["scope"] == "MODEL_VALIDATED"
        assert accepted["observation_origin"] == "REGISTERED_W17"
        assert accepted["model_validated"] is True
        assert accepted["checks"]["dataset_binding"] is True

        # The old solution still exists and its identity is unchanged, but the
        # dataset now points at another solution. Its tag alone proves nothing.
        worker.dataset_solution = "sol-b"
        rebound = validate()
        assert rebound["numerical_verification_status"] == "UNVERIFIED"
        assert rebound["model_validated"] is False

        worker.dataset_solution = None
        unreadable_binding = validate()
        assert unreadable_binding["numerical_verification_status"] == "UNVERIFIED"
        assert unreadable_binding["model_validated"] is False
        worker.dataset_solution = "sol-a"

        from comsol_mcp._execution_contract import ExecutionContractError
        for unknown_error in (
            ExecutionContractError("ENGINE_CALL_FAILED", "synthetic unknown",
                                   details={"execution_state_unknown": True}),
            ExecutionContractError("ENGINE_UNRESPONSIVE", "synthetic timeout", safe_retry=False),
        ):
            worker.dataset_error = unknown_error
            worker.probe_calls.clear()
            with pytest.raises(ExecutionContractError) as binding_unknown:
                validate()
            assert binding_unknown.value.code == "EXECUTION_STATE_UNKNOWN"
            assert worker.probe_calls == ["solution"]
        worker.dataset_error = None

        # A requested range must not be silently skipped just because its
        # bounds are malformed: these are real, registered model samples.
        invalid_range = validate(criteria={"range": [0.0]})
        assert invalid_range["checks"]["range"] is False
        assert invalid_range["numerical_verification_status"] != "PASS"
        assert invalid_range["model_validated"] is False

        outside_range = validate(criteria={"range": [0.0, 299.0]})
        assert outside_range["checks"]["range"] is False
        assert outside_range["numerical_verification_status"] == "FAIL"
        assert outside_range["model_validated"] is False

        wrong_project = validate(project_id="project-b")
        assert wrong_project["scope"] == "PROJECT_IDENTITY_MISMATCH"
        assert wrong_project["model_validated"] is False
        assert wrong_project["numerical_verification_status"] != "PASS"

        wrong_model = validate(ref={**model_ref, "server_instance_id": "other-server"})
        assert wrong_model["scope"] == "CROSS_MODEL_REFUSED"
        assert wrong_model["model_validated"] is False

        stale_revision = validate(revision=9)
        assert stale_revision["scope"] == "STALE_OBSERVATION"
        assert stale_revision["model_validated"] is False

        monkeypatch.setattr(
            _observation_store,
            "solution_identity",
            lambda *_args: {**solution_identity, "computation_version": "replacement-runtime"},
        )
        stale_solution = validate()
        assert stale_solution["scope"] == "STALE_SOLUTION"
        assert stale_solution["model_validated"] is False

        monkeypatch.setattr(
            _observation_store,
            "solution_identity",
            lambda *_args: dict(solution_identity),
        )
        caller_override = validate(criteria={"values": [999.0], "range": [0.0, 1000.0]})
        assert caller_override["scope"] == "OBSERVATION_OVERRIDE_REFUSED"
        assert caller_override["model_validated"] is False
        assert caller_override["numerical_verification_status"] != "PASS"

        def unknown_solution(*_args):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic Worker state unknown")

        monkeypatch.setattr(_observation_store, "solution_identity", unknown_solution)
        with pytest.raises(ExecutionContractError) as unknown:
            validate()
        assert unknown.value.code == "EXECUTION_STATE_UNKNOWN"

        monkeypatch.setattr(_observation_store, "solution_identity", lambda *_args: dict(solution_identity))
        calls = []

        class ResultNode:
            def dataset(self):
                calls.append("dataset")
                raise ExecutionContractError("ENGINE_CALL_FAILED", "synthetic unknown",
                                             details={"execution_state_unknown": True})

            def tags(self):
                calls.append("fallback_tags")
                return []

        class Client:
            def model(self, _tag):
                return SimpleModel()

        class SimpleModel:
            def result(self):
                return ResultNode()

        monkeypatch.setattr(worker, "client", lambda: Client(), raising=False)
        with pytest.raises(ExecutionContractError) as nested_unknown:
            validate()
        assert nested_unknown.value.code == "EXECUTION_STATE_UNKNOWN"
        assert calls == ["dataset"]

        class UnavailableDataset(ResultNode):
            def dataset(self):
                calls.append("dataset")
                raise ExecutionContractError("API_UNSUPPORTED", "synthetic missing dataset getter")

            def tags(self):
                calls.append("result_tags")
                return ["dataset-a"]  # A plot tag is not a dataset tag.

        class UnavailableModel:
            def result(self):
                return UnavailableDataset()

        calls.clear()
        monkeypatch.setattr(worker, "client", lambda: type("Client", (), {"model": lambda self, _: UnavailableModel()})())
        unverified = validate()
        assert unverified["numerical_verification_status"] == "UNVERIFIED"
        assert unverified["model_validated"] is False
        assert unverified["checks"]["dataset_exists"] == "UNVERIFIED"
        assert calls == ["dataset"]

        class EmptyDatasets:
            def tags(self):
                return []

        class EmptyDatasetResults(UnavailableDataset):
            def dataset(self):
                calls.append("dataset")
                return EmptyDatasets()

        class EmptyDatasetModel:
            def result(self):
                return EmptyDatasetResults()

        calls.clear()
        monkeypatch.setattr(worker, "client", lambda: type("Client", (), {"model": lambda self, _: EmptyDatasetModel()})())
        missing = validate()
        assert missing["checks"]["dataset_exists"] is False
        assert missing["model_validated"] is False
        assert calls == ["dataset"]

        from comsol_mcp import _convergence_store
        from comsol_mcp._g3_w20_validation import create_transient_oracle, validate_convergence

        monkeypatch.delattr(worker, "client", raising=False)

        def unresponsive(*_args):
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "synthetic Worker timeout", safe_retry=False)

        monkeypatch.setattr(_convergence_store, "register_case", unresponsive)
        selectors = {name: [0] for name in create_transient_oracle().required_observations}
        with observation_context(store, model_ref, 8, "consumer-operation", project_id="project-a"):
            with pytest.raises(ExecutionContractError) as registration_unknown:
                dispatch("validate.solution", worker, "model-a", {
                    "observation_ref": observation_ref,
                    "solution": {"dataset": "dataset-a"},
                    "criteria": {"oracle": "transient_diffusion"},
                    "observation_selectors": selectors,
                })
        assert registration_unknown.value.code == "EXECUTION_STATE_UNKNOWN"

        monkeypatch.setattr(_convergence_store, "resolve_cases", unresponsive)
        with pytest.raises(ExecutionContractError) as convergence_unknown:
            validate_convergence(worker, "model-a", {"cases": [{"convergence_id": "synthetic"}]})
        assert convergence_unknown.value.code == "EXECUTION_STATE_UNKNOWN"
    finally:
        store.close()
