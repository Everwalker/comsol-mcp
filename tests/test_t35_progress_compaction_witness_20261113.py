"""T35 witness slice: the ``JobCacheCompacted`` event is a post-compaction
continuity signal, NOT an authorization or anti-tamper marker (authored, NOT run).

This file is written alongside, and never modifies, the author originals
``tests/test_t35_solver_progress_after_compaction_20261113.py`` (whose fixture
helpers are reused here by import) and ``tests/test_t35_progress_compaction_strict_20261113.py``.
It adds exactly the witness-contract cases the author tests do not yet cover.

Source-identity honesty (binding): while this file was authored, sibling agents
were concurrently editing the whole files ``comsol_mcp/_operation_store.py``
(their ``cancel_queued`` work; this file read only ``add_event`` and
``compact_terminal_job_metadata`` by name) and ``comsol_mcp/_g3_solver_ops.py``
(read only ``_progress_log_summary`` and ``solver_log_read`` by name). Neither
whole file has a frozen identity, and nothing here was executed, so no test in
this file is a run-proof against any pinned source revision; expectations below
describe the behaviour observed at read time only.

Scope and honesty boundaries for every test here:

- Fresh layers only: every job is a brand-new synthetic capture written into a
  fresh ``OperationStore(":memory:")`` ledger (real ``begin``/``add_event``/
  ``finish`` plus one real task-owned ``compact_terminal_job_metadata``), read
  through the real in-process ``solver_log_read``. No daemon, ControlDaemon,
  Worker, COMSOL, model, engine, SSH, endpoint, network, credential, durable
  on-disk database, pre-existing job or UNKNOWN job is touched, and no module
  constant in either production module is patched or lowered.
- The only deliberate row-level writes are plain ``UPDATE`` statements against
  the in-memory rows this file's own compaction just created — exactly the
  privilege level of any same-level database writer. That is the point: the
  ``JobCacheCompacted`` row is forgeable at rest, so a reader may use it as a
  continuity witness (matching ``operation_id``, terminal status and cleanup
  time against the job-view ``cleanup`` tombstone) but must never treat its
  presence as a tamper-proof seal, as project authorization, or as proof about
  real principals, concurrent writers or durable storage. Authorization lives
  one layer above ``solver_log_read``; its absence of a marker authorizes
  nothing either way.
- Expected first failure if eventually run: the two positives
  (``test_t35_wit_genuine_real_compaction_fresh_read_stays_captured`` and
  ``test_t35_wit_pre_compaction_cursor_continuation_stays_captured_outside_frozen_prefix``)
  are expected RED now: at read time ``_progress_log_summary`` requires the
  operation ``metadata.execution`` and the job ``metadata.execution`` to be
  equal mappings while ``compact_terminal_job_metadata`` drops the job-side
  ``execution`` by design, so the first post-compaction ``CAPTURED`` assertion
  fails on an actual ``UNKNOWN`` with reason "durable job and operation do not
  share a complete project/model/revision binding". The witness-tamper
  negatives are expected to pass now only vacuously — that same UNKNOWN gate
  already blocks every post-compaction ``CAPTURED`` claim regardless of the
  witness state — so a GREEN there today proves nothing about witness
  validation; their real value is that once compaction-compatible certification
  lands, tampered witnesses must still refuse the ``CAPTURED`` claim while the
  forged rows stay readable verbatim for audit.
"""
from __future__ import annotations

import json

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._operation_store import OperationStore
from tests.test_t35_solver_progress_after_compaction_20261113 import (
    _FORBIDDEN_CAPTURE_CLAIMS,
    CAUSE_CHAIN,
    FAILED_RESULT,
    PROJECT_A,
    SUCCEEDED_RESULT,
    _add_progress_capture,
    _finish,
    _start_job,
    _stored_rows,
)

FORGED_OPERATION_ID = "t35-wit-forged-operation-id"
FORGED_CLEANED_AT = "2000-01-01T00:00:00Z"


def _compacted_witness_row(store: OperationStore, job_id: str) -> dict:
    """The single ``JobCacheCompacted`` row this test's own real compaction
    appended; a genuine one must already agree with the job-view tombstone."""
    rows = [row for row in _stored_rows(store, job_id)
            if row["event"] == "JobCacheCompacted"]
    assert len(rows) == 1
    return rows[0]


def _forge_witness_row(store: OperationStore, row: dict, *, changes: dict) -> None:
    """Same-level database write: rewrite only this test's own in-memory
    witness row (never any pre-existing data), mirroring ``add_event``'s
    storage encoding. This is exactly why the row cannot be a seal."""
    metadata = dict(row["metadata"])
    metadata.update(changes)
    with store.lock:
        updated = store.db.execute(
            "UPDATE job_events SET metadata=? WHERE id=? AND event='JobCacheCompacted'",
            (json.dumps(metadata, sort_keys=True), row["id"]),
        )
    assert updated.rowcount == 1


def _assert_not_certified_with_forgery_visible(log: dict, stored_rows: list[dict],
                                               forged_row_id: int,
                                               forged_field: str, forged_value) -> None:
    assert log["status"] != "CAPTURED", (
        "a forged compaction witness may never be certified as CAPTURED")
    for claim in _FORBIDDEN_CAPTURE_CLAIMS:
        assert claim not in log, f"refused read must not claim capture field {claim!r}"
    forged = [row for row in stored_rows if row["id"] == forged_row_id
              and row["event"] == "JobCacheCompacted"]
    assert len(forged) == 1, "fail-closed refusal must not delete the forged witness row"
    assert forged[0]["metadata"].get(forged_field) == forged_value, (
        "the forged row must remain readable verbatim for audit")


def test_t35_wit_genuine_real_compaction_fresh_read_stays_captured(
        terminal_status: str) -> None:
    """Genuine complete capture + genuine terminal compactor: the owning
    project's fresh full read after compaction must still certify the exact
    pre-compaction baseline, and the appended JobCacheCompacted witness must
    itself agree with the job-view cleanup tombstone."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag=f"wit-{terminal_status.lower()}")
        job_id = record["job_id"]
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A)
        result = FAILED_RESULT if terminal_status == "FAILED" else SUCCEEDED_RESULT
        _finish(store, record, status=terminal_status, result=result)

        before_rows = _stored_rows(store, job_id)
        baseline = ops._progress_log_summary(store.job(job_id), before_rows, PROJECT_A)
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        after_rows = _stored_rows(store, job_id)
        assert after_rows[:-1] == before_rows
        witness = _compacted_witness_row(store, job_id)
        job = store.job(job_id)
        assert job is not None and job["result"] == result
        assert witness["metadata"]["operation_id"] == record["operation_id"]
        assert witness["metadata"]["terminal_status"] == terminal_status
        assert witness["metadata"]["at"] == job["metadata"]["cleanup"]["cleaned_at"]

        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert after["job_status"] == terminal_status
        assert after["event_count"] == len(after_rows) and after["next_cursor"] is None
        # The proposed continuity contract (RED now per the module docstring).
        assert after["solver_output_log"]["status"] == "CAPTURED"
        assert after["solver_output_log"] == baseline
        if terminal_status == "FAILED":
            assert after["exception_chain"]["items"] == CAUSE_CHAIN
        else:
            assert after["exception_chain"]["status"] == "NOT_CAPTURED"
    finally:
        store.close()


@pytest.mark.parametrize("tamper", ("operation-id", "cleanup-time"))
def test_t35_wit_forged_job_cache_compacted_witness_blocks_captured_claim(
        tamper: str) -> None:
    """Same genuine data as the positive, but this test forges its own
    compaction witness row at same-writer privilege (associated ``operation_id``
    mismatch, or event ``at`` disagreeing with the job cleanup timestamp): the
    summary must never claim CAPTURED, while the forged row and every other
    row stay readable verbatim and nothing else changes."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag=f"wit-forge-{tamper}")
        job_id = record["job_id"]
        _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        witness = _compacted_witness_row(store, job_id)
        job = store.job(job_id)
        assert job is not None
        if tamper == "operation-id":
            assert witness["metadata"]["operation_id"] == record["operation_id"]
            changes, field, value = (
                {"operation_id": FORGED_OPERATION_ID}, "operation_id", FORGED_OPERATION_ID)
        else:
            cleaned_at = job["metadata"]["cleanup"]["cleaned_at"]
            assert witness["metadata"]["at"] == cleaned_at != FORGED_CLEANED_AT
            changes, field, value = {"at": FORGED_CLEANED_AT}, "at", FORGED_CLEANED_AT
        _forge_witness_row(store, witness, changes=changes)

        after_rows = _stored_rows(store, job_id)
        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        # Masking note (not an assertion): today every post-compaction summary
        # is already UNKNOWN, so this refusal is currently vacuous; once the
        # continuity contract is GREEN, this is what keeps forged witnesses out.
        _assert_not_certified_with_forgery_visible(
            after["solver_output_log"], after_rows, witness["id"], field, value)
        assert after["event_count"] == len(after_rows)
        assert after["job_status"] == "FAILED"
        # The refusal rewrote nothing durable.
        job_after = store.job(job_id)
        assert job_after is not None
        assert job_after["status"] == "FAILED" and job_after["result"] == FAILED_RESULT
        assert job_after["metadata"]["cleanup"]["cleaned_at"] == (
            job["metadata"]["cleanup"]["cleaned_at"])
    finally:
        store.close()


def test_t35_wit_pre_compaction_cursor_continuation_stays_captured_outside_frozen_prefix() -> None:
    """A legitimate cursor frozen before compaction: the appended witness row
    sits outside its frozen prefix, so continuation must still certify the
    baseline CAPTURED summary while the frozen prefix's snapshot bounds and
    digests stay byte-identical and never absorb the appended row."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="wit-cursor")
        job_id = record["job_id"]
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)
        rows = _stored_rows(store, job_id)
        assert len(rows) == 6

        first = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 2})
        baseline = first["solver_output_log"]
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha
        assert first["next_cursor"] is not None and first["snapshot"]["event_count"] == 6

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        assert _compacted_witness_row(store, job_id)["metadata"]["operation_id"] == (
            record["operation_id"])

        collected = list(first["events"])
        cursor = first["next_cursor"]
        while cursor is not None:
            page = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "cursor": cursor})
            assert page["snapshot"] == first["snapshot"], (
                "compaction outside a frozen prefix must not perturb its bounds or digests")
            # The proposed continuity contract (RED now per the module docstring).
            assert page["solver_output_log"]["status"] == "CAPTURED"
            assert page["solver_output_log"] == baseline
            assert page["exception_chain"]["items"] == CAUSE_CHAIN
            collected.extend(page["events"])
            cursor = page["next_cursor"]
        assert [event["id"] for event in collected] == [row["id"] for row in rows]
        assert all(event["event"] != "JobCacheCompacted" for event in collected), (
            "the witness row lives outside the frozen prefix and never joins it")
    finally:
        store.close()
