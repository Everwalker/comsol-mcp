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
    finally:
        store.close()
