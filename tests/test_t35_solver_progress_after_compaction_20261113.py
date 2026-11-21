"""T35 proposed contract: a real synthetic progress capture stays readable and
verifiable across ``OperationStore.compact_terminal_job_metadata`` (authored, NOT run).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real ``solver_log_read`` against a real
  fresh ``OperationStore(":memory:")`` ledger with synthetic begin/add_event/
  finish state and one real task-owned in-memory compaction of the same freshly
  created terminal job. No daemon, ControlDaemon, Worker, COMSOL, model,
  engine, SSH, endpoint, network, credential, historical database or
  pre-existing UNKNOWN job is touched. Every job here is brand-new synthetic
  with an explicit FAILED or SUCCEEDED terminal state; every event and result
  is hand-authored in this file.
- The synthetic capture reuses the complete verified ``ModelUtil`` progress
  lifecycle construction mechanism already established in
  ``tests/test_t35_solver_stream_integrity_20261112.py`` (binding schema
  ``comsol-job-progress-capture/1``, producer
  ``ModelUtil.showProgress(String)+showProgress(false)``, base64 chunks with
  per-chunk SHA-256 and a terminal size/count/digest receipt), so the exact
  capture semantics under test are the existing ones, not a weakened copy.
- Static observation of the frozen compaction-compatible solver candidate
  ``_state/t35-log-overflow-parent-20261112/g3_solver_ops_compacted_author_6b602736.py.txt``
  (read-only, no run): ``_job_project_binding`` exempts a genuinely terminal
  compacted job view (terminal status + ``cleanup.metadata_compacted``
  tombstone + exact flat top-level ``project_id``) while still requiring the
  untouched nested operation claim, ``consistent_project_id`` agreement and the
  operation_id linkage. ``_progress_log_summary`` however requires the job view
  ``metadata.execution`` and the operation ``metadata.execution`` to be equal
  mappings.  ``compact_terminal_job_metadata`` drops the nested job-side
  ``execution`` by design, so against THAT candidate the current candidate
  behaviour after compaction is an honest ``solver_output_log`` ``UNKNOWN``
  degrade with no capture claims, even though every capture event survives
  byte-for-byte.  These tests never reconstruct or re-inject the missing job
  ``execution`` from the operation record to force a GREEN CAPTURED claim.
- Proposed contract asserted here: on one and the same task-created
  FAILED/SUCCEEDED job, the owning project can still fully read the job
  through the real reader after real compaction — complete event list
  (original capture lifecycle plus exactly one appended ``JobCacheCompacted``
  event, each row byte-for-byte identical to what was written), stored result
  JSON and its ``error.cause_chain`` intact, terminal status and project
  binding preserved, a cursor frozen before compaction still pages the same
  frozen prefix afterwards, and the capture stays independently verifiable
  from the returned events (chunk digests and whole-capture SHA-256 recomputed
  from the returned rows equal the pre-compaction expectations).  The progress
  summary must be either the exact verified pre-compaction baseline or an
  honest UNKNOWN with no fabricated completion fields — never a CAPTURED
  claim that does not equal the baseline.
- Conflicting and lossy receipts must fail closed across compaction: a capture
  with two conflicting terminal completion receipts, or a chunk whose SHA-256
  does not match its bytes, must yield UNKNOWN with no capture claims before
  compaction, and the same honest refusal after compaction, while the
  offending rows remain readable verbatim for audit.
- Authorization is enforced one layer above ``solver_log_read`` (the route's
  project authority gate). This file calls the reader directly and does not
  validate, and must not be cited as validating, real project authorization.
- No module constant in ``comsol_mcp._g3_solver_ops`` or
  ``comsol_mcp._operation_store`` is patched or lowered anywhere in this file.
- Expected first failure when eventually run: against the pre-candidate
  reader whose ``_job_project_binding`` still requires a nested job
  ``metadata.execution`` claim (the behaviour recorded in
  ``tests/test_t35_compacted_solver_read_20261112.py``), the first
  post-compaction owning-project ``solver_log_read`` call in
  ``test_t35_pac_failed_job_progress_capture_survives_compaction`` raises
  ``PreWriteRefusal`` with code ``PROJECT_IDENTITY_MISMATCH`` before any
  integrity assertion below it executes.  The foreign-refusal, conflicting-
  receipt and lossy-chunk tests are expected to pass both before and after
  the candidate and guard the candidate against over-relaxing the binding or
  re-certifying corrupted receipts.
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-pac-session"
MODEL_REF = {"kind": "t35-synthetic-model", "model_key": "t35-pac-model"}
MODEL_REVISION = 3
PRODUCER = "ModelUtil.showProgress(String)+showProgress(false)"
PROJECT_A = "t35-pac-synthetic-A"
PROJECT_B = "t35-pac-synthetic-B"
CAUSE_CHAIN = [{"type": "T35PacSyntheticRootCause", "message": "t35-pac-own-cause-4f2a"}]
FAILED_RESULT: dict = {
    "success": False,
    "error": {
        "code": "T35_PAC_SYNTHETIC_FAILED",
        "message": "synthetic failed job for progress-after-compaction regression",
        "cause_chain": CAUSE_CHAIN,
    },
}
SUCCEEDED_RESULT: dict = {
    "success": True,
    "data": {"status": "COMPLETED", "note": "t35-pac-synthetic-success-7d13"},
}

# Fields that only a certified capture may claim; an UNKNOWN degrade must not
# carry any of them.  ``capture_id`` alone is deliberately excluded because the
# reader's own honest UNKNOWN diagnostics may still name the capture identity.
_FORBIDDEN_CAPTURE_CLAIMS = (
    "sha256", "sha256_scope", "byte_length", "file_size_bytes", "chunk_count",
    "chunk_event", "chunk_encoding", "producer", "truncated", "cleanup_verified",
    "capture_state",
)


def _start_job(store: OperationStore, *, project: str, tag: str) -> dict:
    """Real store.begin with the complete project/execution binding that both
    ``_job_project_binding`` and ``_progress_log_summary`` require for a
    certifiable capture (model/revision fields included from the start)."""
    record, reused = store.begin(
        request_id=f"t35-pac-req-{tag}", idempotency_key=f"t35-pac-key-{tag}",
        request_hash=f"t35-pac-hash-{tag}", operation="study.run",
        metadata={"project_id": project, "operation": "study.run", "arguments": {},
                  "execution": {"project_id": project, "session_id": SESSION,
                                "model_ref": dict(MODEL_REF),
                                "expected_revision": MODEL_REVISION}})
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
                          capture_id: str = "t35-pac-cap-1",
                          texts: tuple[str, ...] = ("alpha-progress-", "beta-progress-",
                                                    "gamma-progress-"),
                          corrupt_chunk_index: int | None = None,
                          extra_completion: bool = False) -> tuple[str, bytes]:
    """Write one complete synthetic ModelUtil capture lifecycle, exactly the
    mechanism proven in ``test_t35_solver_stream_integrity_20261112.py``.

    ``corrupt_chunk_index`` writes that chunk's ``chunk_sha256`` as zeros (a
    lossy receipt); ``extra_completion`` appends a second terminal completion
    receipt naming a different captured digest (a conflicting receipt).  Both
    tamper modes are authored at event-write time only — no durable row is
    rewritten.  Returns the SHA-256 and bytes of the *correct* capture, which
    is what independent recompute must reproduce when the capture is sound.
    """
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
    for index, byte_offset, raw in chunks:
        chunk_sha = (hashlib.sha256(raw).hexdigest()
                     if index != corrupt_chunk_index else "0" * 64)
        named("COMSOLProgressChunk", {
            "chunk_index": index, "byte_offset": byte_offset, "byte_length": len(raw),
            "content_encoding": "base64",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "chunk_sha256": chunk_sha,
        })
    captured = b"".join(raw for _, _, raw in chunks)
    expected_sha = hashlib.sha256(captured).hexdigest()
    def completion(digest_value: str) -> dict:
        return {"capture_state": "CAPTURED", "captured_bytes": len(captured),
                "file_size_bytes": len(captured), "chunk_count": len(chunks),
                "captured_sha256": digest_value,
                "truncated": False, "cleanup_verified": True, "scratch_retained": False,
                "complete": True, "captured_sha256_scope": "complete_file"}
    named("COMSOLProgressCaptureCompleted", completion(expected_sha))
    if extra_completion:
        named("COMSOLProgressCaptureCompleted",
              completion(hashlib.sha256(b"t35-pac-conflicting-receipt").hexdigest()))
    return expected_sha, captured


def _finish(store: OperationStore, record: dict, *, status: str, result: dict) -> None:
    accepted, authoritative = store.finish(record["operation_id"], status=status,
                                           result=dict(result))
    assert accepted is True and authoritative == status


def _recompute_capture(events: list[dict]) -> tuple[str | None, bytes | None]:
    """Independent event-level verification: re-derive the capture from the
    returned event rows only (digest chain over chunk_index order), mirroring
    what the reader verifies.  Returns ``(None, None)`` on any mismatch, so
    corrupted captures fail closed at this layer too."""
    chunks = sorted((event for event in events
                     if event.get("event") == "COMSOLProgressChunk"),
                    key=lambda item: item["metadata"].get("chunk_index", -1))
    if not chunks:
        return None, None
    digest = hashlib.sha256()
    joined = bytearray()
    offset = 0
    for index, event in enumerate(chunks):
        metadata = event["metadata"]
        try:
            raw = base64.b64decode(metadata["content_base64"], validate=True)
        except Exception:
            return None, None
        if (type(metadata.get("chunk_index")) is not int
                or metadata.get("chunk_index") != index
                or type(metadata.get("byte_offset")) is not int
                or metadata.get("byte_offset") != offset
                or type(metadata.get("byte_length")) is not int
                or metadata.get("byte_length") != len(raw)
                or hashlib.sha256(raw).hexdigest() != metadata.get("chunk_sha256")):
            return None, None
        digest.update(raw)
        joined.extend(raw)
        offset += len(raw)
    return digest.hexdigest(), bytes(joined)


def _assert_no_capture_claims(log: dict) -> None:
    assert log["status"] == "UNKNOWN" and log["text"] is None
    assert isinstance(log.get("reason"), str) and log["reason"]
    for claim in _FORBIDDEN_CAPTURE_CLAIMS:
        assert claim not in log, f"degraded read must not claim capture field {claim!r}"


def _assert_progress_honest(log: dict, baseline: dict) -> None:
    """Either the exact pre-compaction verified baseline, or an honest
    UNKNOWN with zero fabricated completion fields."""
    if log["status"] == "CAPTURED":
        assert log == baseline, ("a post-compaction CAPTURED claim must equal the "
                                 "pre-compaction verified baseline exactly")
        return
    _assert_no_capture_claims(log)


def _assert_compacted_job_view(store: OperationStore, job_id: str,
                               terminal_status: str, stored_result: dict) -> dict:
    """The compaction-effect helper: job view keeps only the flat binding
    claim plus the cleanup tombstone; the operation claim, result JSON and
    every event row are untouched."""
    compacted = store.job(job_id)
    assert compacted is not None
    assert compacted["status"] == terminal_status
    assert "execution" not in compacted["metadata"]
    assert compacted["metadata"].get("project_id") == PROJECT_A
    assert compacted["metadata"]["cleanup"]["metadata_compacted"] is True
    assert consistent_project_id(
        compacted["metadata"], compacted["operation"]["metadata"]
    ) == PROJECT_A
    assert (compacted["operation"]["metadata"]["execution"]["project_id"]
            == PROJECT_A)
    assert compacted["result"] == stored_result
    return compacted


def test_t35_pac_failed_job_progress_capture_survives_compaction() -> None:
    """Same FAILED job: certified capture before compaction, full readable and
    independently verifiable events plus preserved result/cause chain after
    real compaction, with an honest progress-summary degrade at worst."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="failed")
        job_id = record["job_id"]
        expected_sha, expected_bytes = _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)

        before_rows = _stored_rows(store, job_id)
        assert len(before_rows) == 6
        baseline = ops._progress_log_summary(store.job(job_id), before_rows, PROJECT_A)
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha

        before = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert before["project_id"] == PROJECT_A and before["job_status"] == "FAILED"
        assert before["event_count"] == 6 and before["next_cursor"] is None
        assert before["solver_output_log"] == baseline
        assert before["exception_chain"]["status"] == "CAPTURED"
        assert before["exception_chain"]["source"] == "stored result.error.cause_chain"
        assert before["exception_chain"]["items"] == CAUSE_CHAIN

        # Real task-owned in-memory compaction of this freshly FAILED job.
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        _assert_compacted_job_view(store, job_id, "FAILED", FAILED_RESULT)

        after_rows = _stored_rows(store, job_id)
        assert len(after_rows) == 7
        retained = {row["id"]: row for row in after_rows}
        for row in before_rows:
            assert retained.get(row["id"]) == row, "compaction must not alter event rows"
        compacted_rows = [row for row in after_rows if row["event"] == "JobCacheCompacted"]
        assert len(compacted_rows) == 1
        assert compacted_rows[0]["metadata"]["terminal_status"] == "FAILED"

        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert after["project_id"] == PROJECT_A and after["job_status"] == "FAILED"
        assert after["event_count"] == len(after_rows) == 7
        assert after["snapshot"]["event_count"] == len(after_rows)
        assert after["next_cursor"] is None
        by_id = {event["id"]: event for event in after["events"]}
        for row in before_rows:
            returned = by_id[row["id"]]
            assert returned["event"] == row["event"]
            assert returned["metadata"] == row["metadata"]
        assert any(event["event"] == "JobCacheCompacted" for event in after["events"])
        _assert_progress_honest(after["solver_output_log"], baseline)
        # Event-level verifiability survives regardless of the summary: the
        # capture re-derives byte-for-byte from the returned rows alone.
        recomputed_sha, recomputed_bytes = _recompute_capture(after["events"])
        assert recomputed_sha == expected_sha
        assert recomputed_bytes == expected_bytes
        # The stored failed result and its cause chain remain readable as-is.
        assert after["exception_chain"]["status"] == "CAPTURED"
        assert after["exception_chain"]["source"] == "stored result.error.cause_chain"
        assert after["exception_chain"]["items"] == CAUSE_CHAIN
    finally:
        store.close()


def test_t35_pac_succeeded_job_progress_capture_survives_compaction() -> None:
    """Same coverage on a SUCCEEDED terminal job: no error chain exists before
    or after compaction, and the terminal status plus result stay readable."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="succeeded")
        job_id = record["job_id"]
        expected_sha, expected_bytes = _add_progress_capture(
            store, record, project=PROJECT_A,
            texts=("alpha-progress-", "beta-progress-"))
        _finish(store, record, status="SUCCEEDED", result=SUCCEEDED_RESULT)

        before_rows = _stored_rows(store, job_id)
        assert len(before_rows) == 5
        baseline = ops._progress_log_summary(store.job(job_id), before_rows, PROJECT_A)
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha

        before = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert before["job_status"] == "SUCCEEDED"
        assert before["solver_output_log"] == baseline
        assert before["exception_chain"]["status"] == "NOT_CAPTURED"

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        _assert_compacted_job_view(store, job_id, "SUCCEEDED", SUCCEEDED_RESULT)

        after_rows = _stored_rows(store, job_id)
        assert len(after_rows) == 6
        compacted_rows = [row for row in after_rows if row["event"] == "JobCacheCompacted"]
        assert len(compacted_rows) == 1
        assert compacted_rows[0]["metadata"]["terminal_status"] == "SUCCEEDED"

        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert after["job_status"] == "SUCCEEDED"
        assert after["event_count"] == len(after_rows) == 6
        assert after["next_cursor"] is None
        retained = {row["id"]: row for row in after_rows}
        for row in before_rows:
            assert retained.get(row["id"]) == row
        _assert_progress_honest(after["solver_output_log"], baseline)
        recomputed_sha, recomputed_bytes = _recompute_capture(after["events"])
        assert recomputed_sha == expected_sha
        assert recomputed_bytes == expected_bytes
        # Compaction never invents an error chain for a succeeded job.
        assert after["exception_chain"]["status"] == "NOT_CAPTURED"
    finally:
        store.close()


def test_t35_pac_frozen_cursor_issued_before_compaction_still_pages_after() -> None:
    """A cursor frozen before compaction keeps pageing the same frozen prefix:
    compaction only appends events above ``snapshot_last_id`` and never edits
    rows, so the bounded prefix digests are unchanged and no CURSOR_STALE is
    produced; the appended ``JobCacheCompacted`` row never joins the prefix."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="cursor")
        job_id = record["job_id"]
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)
        rows = _stored_rows(store, job_id)
        assert len(rows) == 6
        baseline = ops._progress_log_summary(store.job(job_id), rows, PROJECT_A)
        assert baseline["status"] == "CAPTURED" and baseline["sha256"] == expected_sha

        first = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 2})
        assert first["event_count"] == 2 and first["next_cursor"] is not None
        assert first["snapshot"]["event_count"] == 6
        assert first["solver_output_log"] == baseline

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]

        collected = list(first["events"])
        cursor = first["next_cursor"]
        while cursor is not None:
            page = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "cursor": cursor})
            assert page["snapshot"] == first["snapshot"], (
                "compaction must not perturb a frozen prefix's digests or bounds")
            assert page["event_count"] > 0
            _assert_progress_honest(page["solver_output_log"], baseline)
            assert page["exception_chain"]["items"] == CAUSE_CHAIN
            collected.extend(page["events"])
            cursor = page["next_cursor"]
        assert [event["id"] for event in collected] == [row["id"] for row in rows]
        assert all(event["event"] != "JobCacheCompacted" for event in collected)
    finally:
        store.close()


def test_t35_pac_foreign_project_refused_after_compaction() -> None:
    """A compacted flat claim never authorizes another project; expected to
    pass both before and after the candidate fix and guards the candidate
    against over-relaxing the binding."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="foreign")
        job_id = record["job_id"]
        _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        events_before = _stored_rows(store, job_id)

        with pytest.raises(PreWriteRefusal) as refusal:
            ops.solver_log_read(store, PROJECT_B, {"job_id": job_id})
        assert refusal.value.code == "PROJECT_IDENTITY_MISMATCH"

        # The refusal changed nothing durable.
        assert _stored_rows(store, job_id) == events_before
        after = store.job(job_id)
        assert after is not None
        assert after["status"] == "FAILED"
        assert after["metadata"].get("project_id") == PROJECT_A
        assert "execution" not in after["metadata"]
        assert after["result"] == FAILED_RESULT
    finally:
        store.close()


def test_t35_pac_conflicting_completion_receipt_fails_closed_across_compaction() -> None:
    """Two conflicting terminal completion receipts must never certify the
    capture — before compaction and after it the reader refuses to claim any
    capture fields, while both receipts stay readable verbatim for audit."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="conflict")
        job_id = record["job_id"]
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A,
                                                extra_completion=True)
        _finish(store, record, status="FAILED", result=FAILED_RESULT)
        before_rows = _stored_rows(store, job_id)
        assert len(before_rows) == 8
        assert (len([row for row in before_rows
                     if row["event"] == "COMSOLProgressCaptureCompleted"]) == 2)

        before = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert before["solver_output_log"]["status"] == "UNKNOWN"
        _assert_no_capture_claims(before["solver_output_log"])

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        after_rows = _stored_rows(store, job_id)
        assert len(after_rows) == 9
        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert after["job_status"] == "FAILED"
        assert after["event_count"] == len(after_rows)
        _assert_no_capture_claims(after["solver_output_log"])
        # The conflicting receipts themselves survive compaction unchanged —
        # fail-closed verification never rewrites or hides audit evidence.
        retained = {row["id"]: row for row in after_rows}
        for row in before_rows:
            assert retained.get(row["id"]) == row
        # Independent event-level verification also fails closed: the two
        # receipts claim different digests, and neither may be adopted as truth.
        recomputed_sha, _ = _recompute_capture(after["events"])
        assert recomputed_sha != after["solver_output_log"].get("sha256")
    finally:
        store.close()


def test_t35_pac_lossy_chunk_digest_fails_closed_across_compaction() -> None:
    """A chunk whose SHA-256 does not match its bytes makes the capture
    unverifiable before compaction and stays unverifiable after it: the reader
    must never claim completion fields even though the corrupt row itself is
    returned intact and the stored result is unaffected."""
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=PROJECT_A, tag="lossy")
        job_id = record["job_id"]
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A,
                                                corrupt_chunk_index=1)
        _finish(store, record, status="SUCCEEDED", result=SUCCEEDED_RESULT)
        before_rows = _stored_rows(store, job_id)
        assert len(before_rows) == 5

        before = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert before["solver_output_log"]["status"] == "UNKNOWN"
        _assert_no_capture_claims(before["solver_output_log"])

        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        after_rows = _stored_rows(store, job_id)
        assert len(after_rows) == 6
        after = ops.solver_log_read(store, PROJECT_A, {"job_id": job_id, "limit": 100})
        assert after["job_status"] == "SUCCEEDED"
        assert after["event_count"] == len(after_rows)
        assert after["exception_chain"]["status"] == "NOT_CAPTURED"
        _assert_no_capture_claims(after["solver_output_log"])
        # The corrupt row is still returned byte-for-byte for audit, but the
        # independent event-level recompute also fails closed instead of
        # silently adopting the expected digest.
        retained = {row["id"]: row for row in after_rows}
        for row in before_rows:
            assert retained.get(row["id"]) == row
        recomputed_sha, recomputed_bytes = _recompute_capture(after["events"])
        assert recomputed_sha is None and recomputed_bytes is None
        corrupt_rows = [event for event in after["events"]
                        if event["event"] == "COMSOLProgressChunk"
                        and event["metadata"].get("chunk_index") == 1]
        assert len(corrupt_rows) == 1
        assert corrupt_rows[0]["metadata"]["chunk_sha256"] == "0" * 64
    finally:
        store.close()
