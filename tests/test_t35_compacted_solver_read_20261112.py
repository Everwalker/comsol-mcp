"""T35 proposed contract: ``solver.log_read`` must stay readable after synthetic compaction.

This file is a NEW synthetic in-memory regression test for a PROPOSED contract;
it has NOT been executed, and no pass or failure is claimed by this source.

Static observation of the real code (read-only, no run):
``OperationStore.compact_terminal_job_metadata`` rewrites a newly created
terminal job's view metadata down to the helper-confirmed flat top-level
``project_id`` (derived solely from ``consistent_project_id``) plus the
``cleanup`` tombstone, drops the nested ``execution`` mapping, and appends one
durable ``JobCacheCompacted`` event; the operation row, idempotency record,
result JSON and all job events are left untouched.  ``_job_project_scope``
documents that compaction may project the project ID into a top-level claim.
But ``_g3_solver_ops._job_project_binding`` still requires BOTH sides to carry
a nested ``metadata.execution.project_id``.  Therefore the predicted current
behaviour is that after compaction ``solver.log_read`` refuses the owning
project's own job with ``PROJECT_IDENTITY_MISMATCH``.

Proposed contract asserted here: a compacted job view remains readable through
the real ``solver.log_read`` for its bound project, with the complete original
event list (including the ``JobCacheCompacted`` event), the stored failed
result's original ``error.cause_chain`` still surfaced, the terminal FAILED
status and project binding preserved, and foreign projects still refused.
Expected first failure, when these tests are eventually run against the
current reader: the post-compaction ``solver.log_read`` call in the first test
raises ``PreWriteRefusal(PROJECT_IDENTITY_MISMATCH)`` before returning.  The
foreign-refusal test is expected to pass both before and after the fix and
guards the fix against over-relaxing the binding.

All data is new synthetic in-memory state: fresh ``OperationStore(':memory:')``
stores, self-created project names, one explicitly synthetic FAILED result and
hand-authored events/cause_chain.  This touches no existing or old database, no
real job, no UNKNOWN state (only the explicit synthetic FAILED terminal state
is established), no model, Worker, SSH, endpoint, credential, disk or platform
state, and no MCP-wire or authenticated-principal access.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g3_solver_ops import solver_log_read
from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import OperationStore

PROJECT_A = "t35-compacted-read-synthetic-A"
PROJECT_B = "t35-compacted-read-synthetic-B"
MARKER = "t35-compacted-read-own-event-9c31"
OWN_EVENT = "T35CompactedReadOwnEvent"
CAUSE_CHAIN = [{"type": "SyntheticRootCause", "message": "t35-compacted-read-own-cause-5b7e"}]
FAILED_RESULT: dict = {
    "ok": False,
    "error": {
        "code": "T35_SYNTHETIC_FAILURE",
        "message": "synthetic failed job for compacted-read regression",
        "cause_chain": CAUSE_CHAIN,
    },
}


def _create_failed_job(store: OperationStore, tag: str) -> tuple[str, str]:
    """Self-authored dual-claim job, one own event, and one explicit synthetic FAILED result."""
    record, reused = store.begin(
        request_id=f"compacted-read-request-{tag}",
        idempotency_key=f"compacted-read-key-{tag}",
        request_hash=f"compacted-read-hash-{tag}",
        operation="study.run",
        metadata={"project_id": PROJECT_A, "execution": {"project_id": PROJECT_A}},
    )
    assert reused is False
    job_id, operation_id = record["job_id"], record["operation_id"]
    store.add_event(job_id, OWN_EVENT, {"marker": MARKER})
    accepted, status = store.finish(operation_id, status="FAILED", result=dict(FAILED_RESULT))
    assert accepted is True
    assert status == "FAILED"
    return job_id, operation_id


def _events(store: OperationStore, job_id: str) -> list[dict]:
    return store.events(job_id, offset=0, limit=100)


def test_solver_log_read_remains_readable_after_compaction() -> None:
    """Positive control, real in-memory compaction, then the proposed post-compaction read."""
    store = OperationStore(":memory:")
    try:
        job_id, _ = _create_failed_job(store, "compacted-read")

        # Positive control: the real reader succeeds before compaction.
        before_read = solver_log_read(store, PROJECT_A, {"job_id": job_id})
        assert before_read["project_id"] == PROJECT_A
        assert before_read["job_status"] == "FAILED"
        assert before_read["event_count"] == 1
        assert before_read["events"][0]["metadata"]["marker"] == MARKER
        assert before_read["exception_chain"]["status"] == "CAPTURED"
        assert before_read["exception_chain"]["items"] == CAUSE_CHAIN
        before_events = _events(store, job_id)
        assert [row["event"] for row in before_events] == [OWN_EVENT]

        # Real task-owned in-memory compaction of the freshly FAILED job.
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]

        # Compaction effect helper: the job view keeps only the flat binding
        # claim plus the cleanup tombstone; events and result are retained.
        compacted = store.job(job_id)
        assert compacted is not None
        assert compacted["status"] == "FAILED"
        assert "execution" not in compacted["metadata"]
        assert compacted["metadata"].get("project_id") == PROJECT_A
        assert compacted["metadata"]["cleanup"]["metadata_compacted"] is True
        assert consistent_project_id(
            compacted["metadata"], compacted["operation"]["metadata"]
        ) == PROJECT_A
        assert compacted["result"] == FAILED_RESULT
        after_events = _events(store, job_id)
        retained = {row["id"]: row for row in after_events}
        for row in before_events:
            assert retained.get(row["id"]) == row
        compacted_rows = [row for row in after_events if row["event"] == "JobCacheCompacted"]
        assert len(compacted_rows) == 1
        assert compacted_rows[0]["metadata"]["terminal_status"] == "FAILED"

        # Proposed contract (expected first failure against the current
        # reader: PreWriteRefusal PROJECT_IDENTITY_MISMATCH, because
        # _job_project_binding still demands a nested job execution claim):
        # the owning project can still read the full compacted job through
        # the real reader.
        after_read = solver_log_read(store, PROJECT_A, {"job_id": job_id})
        assert after_read["project_id"] == PROJECT_A
        assert after_read["job_status"] == "FAILED"
        assert after_read["event_count"] == len(after_events)
        assert after_read["snapshot"]["event_count"] == len(after_events)
        assert after_read["next_cursor"] is None
        names = [event.get("event") for event in after_read["events"]]
        assert OWN_EVENT in names
        assert "JobCacheCompacted" in names
        own_rows = [event for event in after_read["events"] if event.get("event") == OWN_EVENT]
        assert own_rows[0]["metadata"]["marker"] == MARKER
        assert after_read["exception_chain"]["status"] == "CAPTURED"
        assert after_read["exception_chain"]["items"] == CAUSE_CHAIN
    finally:
        store.close()


def test_foreign_project_read_still_refused_after_compaction() -> None:
    """A compacted flat claim never authorizes another project; expected to pass before and after the fix."""
    store = OperationStore(":memory:")
    try:
        job_id, _ = _create_failed_job(store, "compacted-foreign")
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        events_before = _events(store, job_id)

        with pytest.raises(PreWriteRefusal) as refusal:
            solver_log_read(store, PROJECT_B, {"job_id": job_id})
        assert refusal.value.code == "PROJECT_IDENTITY_MISMATCH"

        # The refusal changed nothing durable.
        assert _events(store, job_id) == events_before
        after = store.job(job_id)
        assert after is not None
        assert after["status"] == "FAILED"
        assert after["metadata"].get("project_id") == PROJECT_A
        assert after["result"] == FAILED_RESULT
    finally:
        store.close()
