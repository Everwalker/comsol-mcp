"""T35 streaming-fold integrity tests for ``solver.log_read`` (authored, NOT run).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real ``solver_log_read`` against a real
  fresh ``OperationStore(":memory:")`` ledger with synthetic begin/add_event/
  finish/update state. No daemon, ControlDaemon, Worker, COMSOL, SSH, model,
  or historical UNKNOWN job is touched. Every job and event here is brand-new
  synthetic, and any delete/update touches only this file's own synthetic rows.
- Authorization is enforced one layer above ``solver_log_read`` (the route's
  project authority gate). This file calls the reader directly and does not
  validate, and must not be cited as validating, real project authorization.
- The only patched constants are the NEW T35 module budgets in
  ``comsol_mcp._g3_solver_ops`` (``_MAX_LOG_EVENT_ROW_BYTES``,
  ``_MAX_LOG_PAGE_JSON_BYTES``, ``_MAX_LOG_PROGRESS_JSON_BYTES``); each is
  lowered inside a single test and restored in ``finally``. Store-module
  constants and the existing parent RED file are never touched.
- The per-row/page/progress byte ceilings are proposed operational limits for
  this implementation slice and a compatibility change for oversized prior
  data. Passing these tests is not evidence of a global RSS bound, and
  ``store.job()`` still fully materializes the stored result JSON.
- Nothing here exercises or certifies any T13/T14/W13/T19 refused result or
  any pre-existing UNKNOWN job.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-integrity-session"
MODEL_REF = {"kind": "t35-synthetic-model", "model_key": "t35-integrity-model"}
MODEL_REVISION = 3
PRODUCER = "ModelUtil.showProgress(String)+showProgress(false)"


def _canonical_row_bytes(row: dict) -> int:
    """Same canonical encoding the reader uses to measure admitted rows."""
    return len(json.dumps(row, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _start_job(store: OperationStore, *, project: str, tag: str,
               progress_ready: bool = False) -> dict:
    """Real store.begin with the complete project/execution binding required by
    ``_job_project_binding``; ``progress_ready`` adds the model/revision fields
    that ``_progress_log_summary`` requires for a certified capture."""
    execution = {"project_id": project, "session_id": SESSION}
    if progress_ready:
        execution = {**execution, "model_ref": dict(MODEL_REF),
                     "expected_revision": MODEL_REVISION}
    record, reused = store.begin(
        request_id=f"t35-int-req-{tag}", idempotency_key=f"t35-int-key-{tag}",
        request_hash=hashlib.sha256(f"t35-int-hash-{tag}".encode()).hexdigest(),
        operation="study.run",
        metadata={"operation": "study.run", "arguments": {}, "execution": execution})
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _stored_rows(store: OperationStore, job_id: str) -> list[dict]:
    return store.events(job_id, 0, 1000)


def _progress_binding(record: dict, project: str) -> dict:
    return {
        "schema": "comsol-job-progress-capture/1",
        "producer": PRODUCER,
        "job_id": record["job_id"],
        "operation_id": record["operation_id"],
        "request_id": record["request_id"],
        "request_hash": record["request_hash"],
        "idempotency_key": record["idempotency_key"],
        "project_id": project,
        "session_id": SESSION,
        "model_ref": dict(MODEL_REF),
        "model_revision": MODEL_REVISION,
        "operation": "study.run",
    }


def _add_progress_capture(store: OperationStore, record: dict, *, project: str,
                          capture_id: str = "t35-int-cap-1",
                          texts: tuple[str, ...] = ("alpha-progress-", "beta-progress-"),
                          chunk_add_order: list[int] | None = None) -> str:
    """Write one complete verified capture lifecycle. ``chunk_add_order``
    controls physical insertion (event-id) order so id order can deliberately
    differ from ``chunk_index`` order. Returns the expected captured SHA-256."""
    def named(name: str, extra: dict) -> None:
        store.add_event(record["job_id"], name, {**_progress_binding(record, project),
                                                 "capture_id": capture_id, **extra})
    named("COMSOLProgressCaptureRequested",
          {"capture_state": "REQUESTED", "byte_limit": 1048576, "chunk_bytes": 8192})
    named("COMSOLProgressCaptureActivated", {"capture_state": "ACTIVE"})
    chunks: list[tuple[int, int, bytes]] = []
    offset = 0
    for index, text in enumerate(texts):
        raw = f"{text}{index}".encode("utf-8")
        chunks.append((index, offset, raw))
        offset += len(raw)
    for position in (range(len(chunks)) if chunk_add_order is None else chunk_add_order):
        index, byte_offset, raw = chunks[position]
        named("COMSOLProgressChunk", {
            "chunk_index": index, "byte_offset": byte_offset, "byte_length": len(raw),
            "content_encoding": "base64",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "chunk_sha256": hashlib.sha256(raw).hexdigest(),
        })
    captured = b"".join(raw for _, _, raw in chunks)
    named("COMSOLProgressCaptureCompleted", {
        "capture_state": "CAPTURED", "captured_bytes": len(captured),
        "file_size_bytes": len(captured), "chunk_count": len(chunks),
        "captured_sha256": hashlib.sha256(captured).hexdigest(),
        "truncated": False, "cleanup_verified": True, "scratch_retained": False,
        "complete": True, "captured_sha256_scope": "complete_file",
    })
    return hashlib.sha256(captured).hexdigest()


def test_t35_int_small_prefix_matches_direct_list_helper_and_stays_stable_across_pages():
    """Ordinary small-prefix readback must match the existing direct-list
    helpers (``store.events`` plus ``_event_ids_digest``/``_event_digest``)
    exactly, page cleanly, and keep the frozen prefix stable."""
    project = "t35-int-small-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="small")
        job_id = record["job_id"]
        for index in range(5):
            store.add_event(job_id, f"T35IntEvent{index}", {"index": index})
        rows = _stored_rows(store, job_id)
        assert len(rows) == 5

        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        assert first["job_status"] == "QUEUED"
        assert [event["id"] for event in first["events"]] == [row["id"] for row in rows[:2]]
        assert first["events"][0]["event"] == rows[0]["event"]
        assert first["events"][0]["metadata"] == rows[0]["metadata"]
        assert first["snapshot"]["status"] == "COMPLETE"
        assert first["snapshot"]["event_count"] == 5
        assert first["snapshot"]["last_event_id"] == rows[-1]["id"]
        assert first["snapshot"]["id_sha256"] == ops._event_ids_digest(rows)
        assert first["snapshot"]["events_sha256"] == ops._event_digest(rows)
        assert first["solver_output_log"]["status"] == "NOT_CAPTURED"
        assert first["exception_chain"]["status"] == "NOT_CAPTURED"

        collected = list(first["events"])
        cursor = first["next_cursor"]
        while cursor is not None:
            page = ops.solver_log_read(store, project, {"job_id": job_id, "cursor": cursor})
            assert page["snapshot"]["id_sha256"] == first["snapshot"]["id_sha256"]
            assert page["snapshot"]["events_sha256"] == first["snapshot"]["events_sha256"]
            assert page["snapshot"]["event_count"] == 5
            assert page["event_count"] > 0
            collected.extend(page["events"])
            cursor = page["next_cursor"]
        assert [event["id"] for event in collected] == [row["id"] for row in rows]
    finally:
        store.close()


def test_t35_int_frozen_cursor_excludes_progress_and_chain_appends_from_prefix():
    """Events appended after cursor issuance — including a full progress
    capture and an exception-bearing event — must not leak into the frozen
    prefix; the stored result error chain is job state, not prefix state."""
    project = "t35-int-append-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="append", progress_ready=True)
        job_id, operation_id = record["job_id"], record["operation_id"]
        for index in range(2):
            store.add_event(job_id, f"T35IntPreAppend{index}", {"index": index})
        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 1})
        assert first["next_cursor"] and first["snapshot"]["event_count"] == 2

        _add_progress_capture(store, record, project=project)
        store.add_event(job_id, "T35IntLateChainBearing", {"exception_chain": ["late"]})
        accepted, authoritative = store.finish(
            operation_id, status="FAILED",
            result={"success": False, "error": {
                "code": "T35_INT_SYNTHETIC_FAILED",
                "message": "stored failure after cursor issuance",
                "cause_chain": [{"type": "T35IntException", "message": "stored cause"}]}})
        assert accepted is True and authoritative == "FAILED"
        assert len(_stored_rows(store, job_id)) > 2

        continuation = ops.solver_log_read(
            store, project, {"job_id": job_id, "cursor": first["next_cursor"]})
        assert continuation["event_count"] == 1
        assert continuation["events"][0]["event"] == "T35IntPreAppend1"
        assert all(event["event"] not in {"COMSOLProgressCaptureRequested",
                                          "COMSOLProgressChunk",
                                          "T35IntLateChainBearing"}
                   for event in continuation["events"])
        assert continuation["next_cursor"] is None
        assert continuation["snapshot"]["event_count"] == 2
        # The frozen prefix has no capture events, so progress is NOT_CAPTURED
        # even though the store now contains a complete capture afterwards.
        assert continuation["solver_output_log"]["status"] == "NOT_CAPTURED"
        # Stored result chains come from the durable job record, exactly as before.
        assert continuation["exception_chain"]["status"] == "CAPTURED"
        assert continuation["exception_chain"]["source"] == "stored result.error.cause_chain"

        fresh = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 100})
        assert fresh["snapshot"]["event_count"] > 2
        assert fresh["solver_output_log"]["status"] == "CAPTURED"
    finally:
        store.close()


def test_t35_int_deleted_frozen_prefix_fails_closed_as_cursor_stale():
    """Deleting rows inside (or all of) the frozen prefix must fail the
    continuation closed as CURSOR_STALE, and a later fresh read must be
    allowed to re-freeze the surviving prefix."""
    project = "t35-int-delete-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="delete")
        job_id = record["job_id"]
        for index in range(4):
            store.add_event(job_id, f"T35IntDelete{index}", {"index": index})
        rows = _stored_rows(store, job_id)
        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        assert first["next_cursor"] and first["snapshot"]["event_count"] == 4

        store.db.execute("DELETE FROM job_events WHERE job_id=? AND id=?",
                         (job_id, rows[1]["id"]))
        with pytest.raises(ExecutionContractError) as deleted:
            ops.solver_log_read(store, project, {"job_id": job_id, "cursor": first["next_cursor"]})
        assert deleted.value.code == "CURSOR_STALE"

        store.db.execute("DELETE FROM job_events WHERE job_id=?", (job_id,))
        with pytest.raises(ExecutionContractError) as emptied:
            ops.solver_log_read(store, project, {"job_id": job_id, "cursor": first["next_cursor"]})
        assert emptied.value.code == "CURSOR_STALE"

        after = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        assert after["event_count"] == 0 and after["next_cursor"] is None
        assert after["snapshot"]["event_count"] == 0
    finally:
        store.close()


def test_t35_int_changed_frozen_prefix_content_fails_closed_as_cursor_stale():
    """In-place content change of a prefix row must be detected by the full
    recomputed prefix hashes and fail the continuation as CURSOR_STALE."""
    project = "t35-int-change-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="change")
        job_id = record["job_id"]
        for index in range(4):
            store.add_event(job_id, f"T35IntChange{index}", {"index": index})
        rows = _stored_rows(store, job_id)
        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        assert first["next_cursor"]

        store.db.execute("UPDATE job_events SET metadata=? WHERE id=?",
                         ('{"tampered":true}', rows[2]["id"]))
        with pytest.raises(ExecutionContractError) as changed:
            ops.solver_log_read(store, project, {"job_id": job_id, "cursor": first["next_cursor"]})
        assert changed.value.code == "CURSOR_STALE"

        fresh_after = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 4})
        assert fresh_after["event_count"] == 4
        assert fresh_after["snapshot"]["events_sha256"] != first["snapshot"]["events_sha256"]
        assert fresh_after["events"][2]["metadata"] == {"tampered": True}
    finally:
        store.close()


def test_t35_int_tampered_cursor_digest_or_bounds_fail_closed():
    """A hand-edited cursor must never be trusted: digest, last-id, or offset
    forgeries fail closed without accepting partial hashes."""
    project = "t35-int-tamper-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="tamper")
        job_id = record["job_id"]
        for index in range(4):
            store.add_event(job_id, f"T35IntTamper{index}", {"index": index})
        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        fields = ops._decode_log_cursor(first["next_cursor"])

        for key, forged in (("events_sha256", "0" * 64), ("id_sha256", "1" * 64),
                            ("snapshot_last_id", fields["snapshot_last_id"] + 1)):
            tampered = dict(fields)
            tampered[key] = forged
            with pytest.raises(ExecutionContractError) as exc:
                ops.solver_log_read(store, project,
                                    {"job_id": job_id, "cursor": ops._encode_log_cursor(tampered)})
            assert exc.value.code == "CURSOR_STALE", f"{key} forgery must be CURSOR_STALE"

        offset_forgery = dict(fields)
        offset_forgery["offset"] = fields["snapshot_count"] + 1
        with pytest.raises(ExecutionContractError) as exc:
            ops.solver_log_read(store, project,
                                {"job_id": job_id, "cursor": ops._encode_log_cursor(offset_forgery)})
        assert exc.value.code == "INVALID_REQUEST"
    finally:
        store.close()


def test_t35_int_cursor_project_and_job_binding_rejections_are_preserved():
    """Cursor/project/job binding rejections must fire before any fold, and a
    correctly bound fresh read on the other project must still succeed."""
    store = OperationStore(":memory:")
    try:
        job_a = _start_job(store, project="t35-int-bind-a", tag="bind-a")
        job_b = _start_job(store, project="t35-int-bind-b", tag="bind-b")
        store.add_event(job_a["job_id"], "T35IntBindAEvent", {"index": 0})
        store.add_event(job_a["job_id"], "T35IntBindAEvent", {"index": 1})
        store.add_event(job_b["job_id"], "T35IntBindBEvent", {"index": 0})
        store.add_event(job_b["job_id"], "T35IntBindBEvent", {"index": 1})
        cursor_a = ops.solver_log_read(store, "t35-int-bind-a",
                                       {"job_id": job_a["job_id"], "limit": 1})["next_cursor"]
        cursor_b = ops.solver_log_read(store, "t35-int-bind-b",
                                       {"job_id": job_b["job_id"], "limit": 1})["next_cursor"]

        # Correctly-bound continuations work first, so binding failures below
        # cannot be false positives from a generally broken binding.
        assert ops.solver_log_read(store, "t35-int-bind-a",
                                   {"job_id": job_a["job_id"], "cursor": cursor_a})["job_status"]
        assert ops.solver_log_read(store, "t35-int-bind-b",
                                   {"job_id": job_b["job_id"], "cursor": cursor_b})["job_status"]

        with pytest.raises(ExecutionContractError) as cross_project:
            ops.solver_log_read(store, "t35-int-bind-b",
                                {"job_id": job_b["job_id"], "cursor": cursor_a})
        assert cross_project.value.code == "CURSOR_BINDING_MISMATCH"
        with pytest.raises(ExecutionContractError) as cross_job:
            ops.solver_log_read(store, "t35-int-bind-a",
                                {"job_id": job_a["job_id"], "cursor": cursor_b})
        assert cross_job.value.code == "CURSOR_BINDING_MISMATCH"
        with pytest.raises(ExecutionContractError) as wrong_project:
            ops.solver_log_read(store, "t35-int-bind-b", {"job_id": job_a["job_id"]})
        assert wrong_project.value.code == "PROJECT_IDENTITY_MISMATCH"
        with pytest.raises(ExecutionContractError) as limit_drift:
            ops.solver_log_read(store, "t35-int-bind-a",
                                {"job_id": job_a["job_id"], "cursor": cursor_a, "limit": 7})
        assert limit_drift.value.code == "CURSOR_BINDING_MISMATCH"
    finally:
        store.close()


def test_t35_int_row_exceeding_admission_budget_refuses_the_read():
    """A row above ``_MAX_LOG_EVENT_ROW_BYTES`` cannot be admitted: the whole
    read must fail closed, never skipping the row while certifying the prefix."""
    project = "t35-int-rowbudget-project"
    original = ops._MAX_LOG_EVENT_ROW_BYTES
    store = OperationStore(":memory:")
    try:
        ops._MAX_LOG_EVENT_ROW_BYTES = 400
        record = _start_job(store, project=project, tag="rowbudget")
        job_id = record["job_id"]
        for index in range(2):
            store.add_event(job_id, "T35IntSmall", {"n": index})
        store.add_event(job_id, "T35IntBig", {"payload": "P" * 600})
        store.add_event(job_id, "T35IntSmall", {"n": 3})
        with pytest.raises(ExecutionContractError) as exc:
            # Even a limit-1 window that does not reach the big row must fail:
            # the full prefix cannot be certified without admitting every row.
            ops.solver_log_read(store, project, {"job_id": job_id, "limit": 1})
        assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    finally:
        ops._MAX_LOG_EVENT_ROW_BYTES = original
        store.close()


def test_t35_int_single_event_over_response_ceiling_fails_closed():
    """One admitted row whose canonical JSON exceeds the response ceiling must
    fail closed rather than be silently skipped."""
    project = "t35-int-singlebig-project"
    original = ops._MAX_LOG_PAGE_JSON_BYTES
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="singlebig")
        job_id = record["job_id"]
        store.add_event(job_id, "T35IntBigHeadline", {"payload": "Q" * 800})
        for index in range(3):
            store.add_event(job_id, "T35IntSmall", {"n": index})
        rows = _stored_rows(store, job_id)
        big_bytes = _canonical_row_bytes(rows[0])
        assert big_bytes > 500
        ops._MAX_LOG_PAGE_JSON_BYTES = big_bytes - 1
        with pytest.raises(ExecutionContractError) as exc:
            ops.solver_log_read(store, project, {"job_id": job_id, "limit": 4})
        assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    finally:
        ops._MAX_LOG_PAGE_JSON_BYTES = original
        store.close()


def test_t35_int_page_ceiling_returns_shorter_page_without_dropping_rows():
    """Multiple valid rows above the page ceiling must yield a non-empty
    shorter page whose next cursor resumes at the exact next offset, with the
    caller's original limit retained."""
    project = "t35-int-shortpage-project"
    original = ops._MAX_LOG_PAGE_JSON_BYTES
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="shortpage")
        job_id = record["job_id"]
        for index in range(4):
            store.add_event(job_id, "T35IntPageRow", {"payload": f"P{index}" + "q" * 180})
        rows = _stored_rows(store, job_id)
        sizes = [_canonical_row_bytes(row) for row in rows]
        ceiling = sizes[0] + sizes[1]
        assert max(sizes) <= ceiling
        assert sizes[0] + sizes[1] + sizes[2] > ceiling
        assert sizes[2] + sizes[3] <= ceiling
        ops._MAX_LOG_PAGE_JSON_BYTES = ceiling

        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 4})
        assert first["event_count"] == 2 and 0 < first["event_count"] < 4
        assert [event["id"] for event in first["events"]] == [row["id"] for row in rows[:2]]
        assert first["next_cursor"] is not None
        fields = ops._decode_log_cursor(first["next_cursor"])
        assert fields["offset"] == 2
        assert fields["limit"] == 4 and fields["snapshot_count"] == 4

        second = ops.solver_log_read(store, project,
                                     {"job_id": job_id, "cursor": first["next_cursor"], "limit": 4})
        assert second["event_count"] == 2
        assert [event["id"] for event in second["events"]] == [row["id"] for row in rows[2:]]
        assert second["next_cursor"] is None
        collected = first["events"] + second["events"]
        assert [event["id"] for event in collected] == [row["id"] for row in rows]
    finally:
        ops._MAX_LOG_PAGE_JSON_BYTES = original
        store.close()


def test_t35_int_progress_capture_verified_across_pages_ordered_chunks():
    """A valid capture lifecycle stays certified (chunk digests verified) on
    every page of a paged read, matching the direct whole-prefix helper."""
    project = "t35-int-progress-ordered-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="progress-ordered", progress_ready=True)
        job_id = record["job_id"]
        expected_sha = _add_progress_capture(store, record, project=project)
        baseline = ops._progress_log_summary(store.job(job_id), _stored_rows(store, job_id), project)
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha

        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 3})
        assert first["event_count"] == 3
        assert first["solver_output_log"] == baseline
        second = ops.solver_log_read(store, project,
                                     {"job_id": job_id, "cursor": first["next_cursor"]})
        assert second["solver_output_log"] == baseline
        assert second["next_cursor"] is None
    finally:
        store.close()


def test_t35_int_progress_capture_verified_with_chunk_ids_out_of_order():
    """Chunk events whose id order differs from chunk_index order must still
    certify after the existing sort-by-chunk_index logic."""
    project = "t35-int-progress-reordered-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="progress-reordered", progress_ready=True)
        job_id = record["job_id"]
        _add_progress_capture(store, record, project=project,
                              texts=("first-text-", "second-text-", "third-text-"),
                              chunk_add_order=[2, 0, 1])
        baseline = ops._progress_log_summary(store.job(job_id), _stored_rows(store, job_id), project)
        assert baseline["status"] == "CAPTURED" and baseline["chunk_count"] == 3

        response = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 100})
        assert response["solver_output_log"] == baseline
    finally:
        store.close()


def test_t35_int_progress_accumulator_over_budget_reports_unknown_not_capture():
    """Exceeding the bounded progress budget must produce an honest UNKNOWN
    diagnostic on every page, with no completion claims, while still paging."""
    project = "t35-int-progress-budget-project"
    original = ops._MAX_LOG_PROGRESS_JSON_BYTES
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="progress-budget", progress_ready=True)
        job_id = record["job_id"]
        _add_progress_capture(
            store, record, project=project,
            texts=("chunk-text-" + "z" * 200 + "-", "chunk-text-" + "y" * 200 + "-"))
        lifecycle_rows = [row for row in _stored_rows(store, job_id)
                          if row["event"].startswith("COMSOLProgress")]
        assert len(lifecycle_rows) == 5
        assert min(_canonical_row_bytes(row) for row in lifecycle_rows) >= 350
        ops._MAX_LOG_PROGRESS_JSON_BYTES = 1500  # 5 rows x >=350 bytes must overflow

        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 2})
        log = first["solver_output_log"]
        assert log["status"] == "UNKNOWN" and log["text"] is None
        assert "budget" in log["reason"].lower()
        for forbidden in ("sha256", "byte_length", "chunk_count", "capture_id"):
            assert forbidden not in log, "over-budget read must not claim capture fields"
        assert first["event_count"] == 2 and first["next_cursor"] is not None
        second = ops.solver_log_read(store, project,
                                     {"job_id": job_id, "cursor": first["next_cursor"]})
        assert second["solver_output_log"]["status"] == "UNKNOWN"
        assert second["solver_output_log"] == log
    finally:
        ops._MAX_LOG_PROGRESS_JSON_BYTES = original
        store.close()


def test_t35_int_exception_chain_stored_result_takes_precedence():
    """Stored result.error chains still outrank prefix event chains."""
    project = "t35-int-excprecedence-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="excprecedence")
        job_id, operation_id = record["job_id"], record["operation_id"]
        store.add_event(job_id, "T35IntEventChain", {"exception_chain": [{"type": "EventChain"}]})
        store.finish(operation_id, status="FAILED", result={"success": False, "error": {
            "code": "T35_INT_EXC", "message": "stored",
            "cause_chain": [{"type": "StoredChain"}]}})
        response = ops.solver_log_read(store, project, {"job_id": job_id})
        assert response["exception_chain"]["status"] == "CAPTURED"
        assert response["exception_chain"]["source"] == "stored result.error.cause_chain"
        assert response["exception_chain"]["items"] == [{"type": "StoredChain"}]
    finally:
        store.close()


def test_t35_int_first_qualifying_event_chain_wins_and_survives_paging():
    """Without a stored chain, the first qualifying event in id order wins —
    including when that event sits on a later page than the read request."""
    project = "t35-int-excorder-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="excorder")
        job_id = record["job_id"]
        store.add_event(job_id, "T35IntPlain", {"n": 0})
        store.add_event(job_id, "T35IntFirstChain", {"exception_chain": ["a", "b"]})
        store.add_event(job_id, "T35IntSecondChain", {"cause_chain": ["c"]})
        rows = _stored_rows(store, job_id)
        first = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 1})
        assert first["events"][0]["id"] == rows[0]["id"]
        assert first["exception_chain"]["status"] == "CAPTURED"
        assert first["exception_chain"]["source"] == f"event {rows[1]['id']} metadata.exception_chain"
        assert first["exception_chain"]["items"] == ["a", "b"]
        second = ops.solver_log_read(store, project,
                                     {"job_id": job_id, "cursor": first["next_cursor"]})
        assert second["exception_chain"] == first["exception_chain"]
    finally:
        store.close()


def test_t35_int_worker_and_stored_error_summaries_are_preserved():
    """Worker-failure code-only summaries and stored result.error summaries
    keep their exact legacy shapes."""
    store = OperationStore(":memory:")
    try:
        worker_job = _start_job(store, project="t35-int-workersummary-project", tag="workersummary")
        store.add_event(worker_job["job_id"], "worker_request", {
            "request_id": "t35-wr-1",
            "reply": {"failure": {"code": "T35_WORKER_E", "message": "worker said no"}}})
        worker_page = ops.solver_log_read(store, "t35-int-workersummary-project",
                                          {"job_id": worker_job["job_id"]})
        assert worker_page["exception_chain"]["status"] == "ERROR_SUMMARY_ONLY"
        assert worker_page["exception_chain"]["items"] == [
            {"type": "WorkerFailure", "code": "T35_WORKER_E", "message": "worker said no"}]
        assert worker_page["exception_chain"]["cause_chain_status"] == "NOT_CAPTURED"

        stored_job = _start_job(store, project="t35-int-storedsummary-project", tag="storedsummary")
        store.finish(stored_job["operation_id"], status="FAILED",
                     result={"success": False,
                             "error": {"code": "T35_STORED_E", "message": "stored summary"}})
        stored_page = ops.solver_log_read(store, "t35-int-storedsummary-project",
                                          {"job_id": stored_job["job_id"]})
        assert stored_page["exception_chain"]["status"] == "ERROR_SUMMARY_ONLY"
        assert stored_page["exception_chain"]["source"] == "stored result.error"
        assert stored_page["exception_chain"]["items"] == [
            {"code": "T35_STORED_E", "message": "stored summary"}]
    finally:
        store.close()
