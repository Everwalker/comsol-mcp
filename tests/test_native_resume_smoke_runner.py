from __future__ import annotations

from tools.run_native_resume_smoke import (
    _frozen_point_records,
    _is_native_study_run_submission,
    _remaining_native_solve_budget,
    _study_node_path,
)
from tools.verify_native_resume_saved_artifact import ZeroSolverCallGuard


def _event(*, phase: str = "submitted", kind: str = "call", operation: str = "study.run",
           worker_type: str = "call", method: str = "run") -> dict:
    return {
        "phase": phase,
        "kind": kind,
        "operation_id": operation,
        "metadata": {"type": worker_type, "method": method},
    }


def test_counts_only_persisted_study_run_worker_submission() -> None:
    # Worker operation_id is a generated UUID, distinct from the domain action.
    assert _is_native_study_run_submission(_event(operation="48c17d30-d746-47d2-a730-9f429727a7b5"))


def test_fixture_study_reference_uses_nodepath_wire_shape() -> None:
    assert _study_node_path("std1") == {
        "segments": [{"collection": "study", "tag": "std1"}],
    }


def test_frozen_two_dimensional_points_use_catalog_object_coordinates() -> None:
    assert _frozen_point_records([[0.25, 0.5], [0.5, 0.75]]) == [
        {"x": 0.25, "y": 0.5},
        {"x": 0.5, "y": 0.75},
    ]


def test_zero_solver_guard_blocks_protected_study_run_before_submit(tmp_path) -> None:
    class FakeWorker:
        def __init__(self):
            self.submitted = []

        def submit(self, kind, payload, **kwargs):
            self.submitted.append((kind, payload, kwargs))
            return {"status": "SUCCEEDED"}

    worker = FakeWorker()
    guard = ZeroSolverCallGuard(worker, event_path=tmp_path / "worker-events.jsonl")
    guard.protect("study-handle", kind="study", tag="std1", java_type="Study")
    try:
        try:
            worker.submit("call", {"handle": "study-handle", "method": "run", "args": []})
        except RuntimeError as exc:
            assert "zero-solve guard blocked" in str(exc)
        else:
            raise AssertionError("guard must refuse protected study execution")
        assert worker.submitted == []
        assert guard.safe_to_stop()
        assert guard.blocked_attempts[0]["phase"] == "blocked_before_worker_submission"
    finally:
        guard.remove()


def test_zero_solver_guard_allows_numerical_feature_sampling_run(tmp_path) -> None:
    class FakeWorker:
        def __init__(self):
            self.submitted = []

        def submit(self, kind, payload, **kwargs):
            self.submitted.append((kind, payload, kwargs))
            return {"status": "SUCCEEDED"}

    worker = FakeWorker()
    guard = ZeroSolverCallGuard(worker, event_path=tmp_path / "worker-events.jsonl")
    guard.protect("study-handle", kind="study", tag="std1", java_type="Study")
    try:
        response = worker.submit("call", {"handle": "interp-handle", "method": "run", "args": []})
        assert response["status"] == "SUCCEEDED"
        assert guard.safe_to_stop()
        assert guard.method_run_events[0]["protected_node"] is None
    finally:
        guard.remove()


def test_remaining_solve_budget_accounts_for_prior_native_call() -> None:
    assert _remaining_native_solve_budget(0) == 2
    assert _remaining_native_solve_budget(1) == 1
    assert _remaining_native_solve_budget(2) == 0


def test_does_not_count_adapter_validation_failure_without_worker_event() -> None:
    assert not _is_native_study_run_submission({"adapter_entered": True})


def test_does_not_count_observation_as_a_second_solver_call() -> None:
    assert not _is_native_study_run_submission(_event(phase="observed"))


def test_does_not_count_other_engine_methods_or_operations() -> None:
    assert not _is_native_study_run_submission(_event(method="getTag"))
    assert not _is_native_study_run_submission(_event(kind="modelutil"))
    assert not _is_native_study_run_submission(_event(worker_type="modelutil"))
