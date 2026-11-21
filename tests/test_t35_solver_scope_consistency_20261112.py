"""T35 solver-log dual-side project consistency: fresh-memory contract tests.

The reader under test and the SQLite store are real.  These tests neither
construct a daemon nor establish an authenticated principal or MCP-wire
access.  All rows and project names are new synthetic in-memory data; no
existing database, model, Worker, SSH, historic job or UNKNOWN is touched.

The conflict cases expect the same no-ambiguous-project-binding contract used
by the scoped cached job reads: every present top-level and execution project
claim on both records must agree.  Their first failed assertion, if any, bounds
what was actually observed; no pass or failure is claimed by this source.
"""
from __future__ import annotations

import json

import pytest

from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g3_solver_ops import solver_log_read
from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import OperationStore

PROJECT_A = "t35-solver-scope-synthetic-A"
PROJECT_B = "t35-solver-scope-synthetic-B"
MARKER = "t35-solver-scope-own-event-4fa8"


def _new_job(store: OperationStore, tag: str) -> tuple[str, str]:
    record, reused = store.begin(
        request_id=f"scope-solver-request-{tag}",
        idempotency_key=f"scope-solver-key-{tag}",
        request_hash=f"scope-solver-hash-{tag}",
        operation="study.run",
        metadata={"project_id": PROJECT_A, "execution": {"project_id": PROJECT_A}},
    )
    assert reused is False
    job_id, operation_id = record["job_id"], record["operation_id"]
    store.add_event(job_id, "T35ScopeOwnEvent", {"marker": MARKER})
    return job_id, operation_id


def _verified_binding(store: OperationStore, job_id: str) -> str | None:
    job = store.job(job_id)
    assert isinstance(job, dict) and isinstance(job.get("operation"), dict)
    return consistent_project_id(job["metadata"], job["operation"]["metadata"])


def test_solver_log_all_consistent_project_claims_are_readable() -> None:
    store = OperationStore(":memory:")
    try:
        job_id, _ = _new_job(store, "valid")
        assert _verified_binding(store, job_id) == PROJECT_A
        result = solver_log_read(store, PROJECT_A, {"job_id": job_id})
        assert result["project_id"] == PROJECT_A
        assert result["job_id"] == job_id
        assert result["event_count"] == 1
        assert result["events"][0]["metadata"]["marker"] == MARKER
    finally:
        store.close()


@pytest.mark.parametrize("side", ["job", "operation"])
def test_solver_log_refuses_conflicting_flat_project_claim_before_event_fold(side: str) -> None:
    store = OperationStore(":memory:")
    try:
        job_id, operation_id = _new_job(store, side)
        if side == "job":
            store.update_job(job_id, "QUEUED", {"project_id": PROJECT_B})
        else:
            row = store.db.execute(
                "SELECT metadata FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["project_id"] = PROJECT_B
            store.db.execute(
                "UPDATE operations SET metadata=? WHERE operation_id=?",
                (json.dumps(metadata), operation_id),
            )
        assert _verified_binding(store, job_id) is None

        fold_calls: list[str] = []
        real_fold = store.fold_job_event_prefix

        def traced_fold(target: str, **kwargs):
            fold_calls.append(target)
            return real_fold(target, **kwargs)

        store.fold_job_event_prefix = traced_fold  # type: ignore[method-assign]
        with pytest.raises(PreWriteRefusal) as refusal:
            solver_log_read(store, PROJECT_A, {"job_id": job_id})
        assert refusal.value.code == "PROJECT_IDENTITY_MISMATCH"
        assert fold_calls == []
    finally:
        store.close()
