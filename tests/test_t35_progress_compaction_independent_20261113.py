"""T35 progress-compaction safety tests — independent synthetic derivation.

Scope and honesty contract (read before trusting a green run):

* Self-contained: every test builds a FRESH in-memory OperationStore and
  drives the real production primitives only: ``OperationStore.begin``,
  ``update_job``, ``add_event``, ``compact_terminal_job_metadata``,
  ``solver_log_snapshot``/``fold_job_event_prefix`` and the project-scoped
  ``solver.log_read`` reader ``solver_log_read``. No Worker, no COMSOL, no
  daemon, no network, no real historical UNKNOWN state. Every identity
  (project, job, operation, request, idempotency key, capture id, session,
  model_ref token) is randomized per test — no job/project success is
  hardcoded, and no prior test, historical copy, or log is read or copied.
* Synthetic progress events mirror the real producer's event shape
  (``_comsol_progress_capture.ComsolProgressCapture``): the 12 binding
  fields (schema, producer, job_id, operation_id, request_id, request_hash,
  idempotency_key, project_id, session_id, model_ref, model_revision,
  operation) plus ``capture_id`` on every event, the
  Requested/Activated/Chunk*/Completed lifecycle, base64 chunks with
  byte_offset/byte_length/chunk_sha256, and the terminal receipt fields
  (captured_bytes, file_size_bytes, chunk_count, captured_sha256,
  captured_sha256_scope, truncated, complete, cleanup_verified,
  scratch_retained).
* Tamper/forge cases mutate ONLY synthetic rows through the store's own
  SQLite connection (job/job_events metadata columns) to model damage or
  forgery; they never touch real data.
* NOT RUN: per the delegated task, tests were not executed in this
  environment. One open point is preserved instead of papered over:
  ``_g3_solver_ops._progress_log_summary`` compares the operation-side
  ``execution`` mapping with the job-side ``execution`` mapping
  (``execution != job_execution``). ``compact_terminal_job_metadata``
  deliberately drops the job-side nested ``execution`` (the reader's
  ``_job_project_binding`` has an explicit marker-gated exemption for that
  shape, but the summary path has no symmetric exemption as read here). If
  that reading is accurate, the post-compaction CAPTURED assertions below
  would currently fail with an honest UNKNOWN summary — the product would
  need a compaction exemption in the summary path symmetric to the
  project-binding exemption. The tests assert the acceptance contract
  (terminal CAPTURED evidence stays verifiable before AND after the real
  compactor) rather than silently weakening to the observed behaviour; the
  reviewer must treat a red run on tests 02/03 as a product finding to
  triage, not as a test to relax.

The clean (negative) expected result for every tamper/forgery case is a
fail-closed ``UNKNOWN`` summary or an exact contract-refusal code; in no
case may the reader certify (``CAPTURED``) forged marker shapes, broken
bindings, or damaged digests.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_solver_ops import solver_log_read
from comsol_mcp._operation_store import JobCleanupError, OperationStore, TERMINAL

# Contract literals restated from the producer/reader (tripwire: if the
# product renames them, these tests fail on purpose).
CAPTURE_SCHEMA = "comsol-job-progress-capture/1"
PRODUCER = "ModelUtil.showProgress(String)+showProgress(false)"
PAGE_LIMIT = 2
_CHUNK_SLICE = 4000  # <= the producer's 8192-byte chunk bound


# --------------------------------------------------------------------------
# synthetic construction helpers
# --------------------------------------------------------------------------

def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def store() -> Iterator[OperationStore]:
    s = OperationStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _new_project() -> str:
    return "t35x-proj-" + uuid.uuid4().hex


def _admit(store: OperationStore, project_id: str, *,
           metadata: dict | None = None) -> dict:
    """Admit one synthetic study.run through the production begin()."""
    if metadata is None:
        metadata = {
            "operation": "study.run",
            "execution": {
                "project_id": project_id,
                "session_id": "t35x-session-" + uuid.uuid4().hex,
                "model_ref": {"kind": "t35x-synthetic-model",
                              "token": uuid.uuid4().hex},
                "expected_revision": 3,
            },
        }
    payload = {"operation": "study.run", "metadata": metadata}
    record, replayed = store.begin(
        request_id="t35x-request-" + uuid.uuid4().hex,
        idempotency_key="t35x-idem-" + uuid.uuid4().hex,
        request_hash=_sha(_canonical(payload).encode("utf-8")),
        operation="study.run",
        metadata=metadata,
        timeouts={"hard_seconds": 300},
    )
    assert replayed is False
    return record


def _split_chunks(payload: bytes) -> list[bytes]:
    return [payload[i:i + _CHUNK_SLICE] for i in range(0, len(payload), _CHUNK_SLICE)]


def _append_capture(store: OperationStore, record: dict, project_id: str,
                    payload: bytes) -> dict:
    """Append the producer-shaped capture event sequence for ``record``."""
    job_id = record["job_id"]
    execution = record["metadata"]["execution"]
    binding = {
        "schema": CAPTURE_SCHEMA,
        "producer": PRODUCER,
        "job_id": job_id,
        "operation_id": record["operation_id"],
        "request_id": record["request_id"],
        "request_hash": record["request_hash"],
        "idempotency_key": record["idempotency_key"],
        "project_id": project_id,
        "session_id": execution["session_id"],
        "model_ref": dict(execution["model_ref"]),
        "model_revision": execution["expected_revision"],
        "operation": "study.run",
    }
    capture_id = "t35x-cap-" + uuid.uuid4().hex
    scratch_id = "t35x-scratch-" + uuid.uuid4().hex

    def emit(name: str, extra: dict) -> None:
        store.add_event(job_id, name, {**binding, "capture_id": capture_id, **extra})

    store.update_job(job_id, "RUNNING")
    emit("COMSOLProgressCaptureRequested",
         {"capture_state": "REQUESTED", "scratch_id": scratch_id,
          "byte_limit": 1024 * 1024, "chunk_bytes": 8192})
    emit("COMSOLProgressCaptureActivated",
         {"capture_state": "ACTIVE", "sink_path_persisted": False})
    offset = 0
    for index, raw in enumerate(_split_chunks(payload)):
        emit("COMSOLProgressChunk",
             {"chunk_index": index,
              "byte_offset": offset,
              "byte_length": len(raw),
              "chunk_sha256": _sha(raw),
              "content_base64": base64.b64encode(raw).decode("ascii"),
              "content_encoding": "base64"})
        offset += len(raw)
    emit("COMSOLProgressCaptureCompleted",
         {"capture_state": "CAPTURED",
          "complete": True,
          "truncated": False,
          "file_size_bytes": len(payload),
          "captured_bytes": len(payload),
          "chunk_count": len(_split_chunks(payload)),
          "captured_sha256": _sha(payload),
          "captured_sha256_scope": "complete_file",
          "cleanup_verified": True,
          "scratch_retained": False,
          "scratch_id": scratch_id})
    return {"record": record, "job_id": job_id, "project_id": project_id,
            "binding": binding, "capture_id": capture_id, "payload": payload}


def _make_capture(store: OperationStore, project_id: str | None = None,
                  payload: bytes | None = None, *, terminal: bool = True) -> dict:
    project_id = project_id or _new_project()
    # 400 lines form exactly three test chunks of at most 4000 bytes; the pagination
    # assertions below deliberately exercise three nonempty pages.
    payload = payload if payload is not None else b"t35x synthetic progress line\n" * 400
    ctx = _append_capture(store, _admit(store, project_id), project_id, payload)
    if terminal:
        store.update_job(ctx["job_id"], "SUCCEEDED")
        job_view = store.job(ctx["job_id"])
        assert job_view is not None
        assert job_view["status"] in TERMINAL
    return ctx


# --------------------------------------------------------------------------
# read/tamper helpers over the store's own SQLite connection
# --------------------------------------------------------------------------

def _read(store: OperationStore, project_id: str, job_id: str, *,
          cursor: str | None = None, limit: int | None = None) -> dict:
    args: dict = {"job_id": job_id}
    if cursor is not None:
        args["cursor"] = cursor
    if limit is not None:
        args["limit"] = limit
    return solver_log_read(store, project_id, args)


def _assert_refusal(read_call, expected_code: str):
    try:
        read_call()
    except Exception as exc:
        assert isinstance(exc, ExecutionContractError), (
            f"expected a contract refusal {expected_code}, got "
            f"{type(exc).__name__}: {exc}")
        observed = getattr(exc, "code", None)
        assert observed == expected_code or expected_code in str(exc), (
            f"expected refusal code {expected_code}, got {observed!r}: {exc}")
        return exc
    raise AssertionError(f"expected refusal {expected_code}, but the read succeeded")


def _assert_unknown(store: OperationStore, ctx: dict, reason_substring: str,
                    *, limit: int | None = None) -> dict:
    result = _read(store, ctx["project_id"], ctx["job_id"], limit=limit)
    log = result["solver_output_log"]
    assert log["status"] == "UNKNOWN", (
        f"expected fail-closed UNKNOWN, got {log['status']}: {log.get('reason')}")
    assert log["text"] is None
    assert reason_substring in (log.get("reason") or ""), log.get("reason")
    return result


def _decode_cursor(value: str) -> dict:
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    decoded = json.loads(raw.decode("utf-8"))
    assert decoded["v"] == 1
    return decoded


def _event_rows(store: OperationStore, job_id: str, event_name: str) -> list[tuple[int, dict]]:
    rows = store.db.execute(
        "SELECT id, metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
        (job_id, event_name)).fetchall()
    return [(int(row["id"]), json.loads(row["metadata"])) for row in rows]


def _mutate_event(store: OperationStore, ctx: dict, event_name: str,
                  mutate, index: int = 0) -> None:
    row_id, metadata = _event_rows(store, ctx["job_id"], event_name)[index]
    mutate(metadata)
    store.db.execute("UPDATE job_events SET metadata=? WHERE id=?",
                     (_canonical(metadata), row_id))


def _delete_event(store: OperationStore, ctx: dict, event_name: str,
                  index: int = 0) -> None:
    row_id, _ = _event_rows(store, ctx["job_id"], event_name)[index]
    store.db.execute("DELETE FROM job_events WHERE id=?", (row_id,))


def _mutate_row_metadata(store: OperationStore, table: str, key_column: str,
                         key: str, mutate) -> None:
    row = store.db.execute(
        f"SELECT metadata FROM {table} WHERE {key_column}=?", (key,)).fetchone()
    metadata = json.loads(row["metadata"] or "{}")
    mutate(metadata)
    store.db.execute(f"UPDATE {table} SET metadata=? WHERE {key_column}=?",
                     (_canonical(metadata), key))


def _replace_job_metadata(store: OperationStore, job_id: str, metadata: dict) -> None:
    store.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
                     (_canonical(metadata), job_id))


def _forged_marker(project_id: str) -> dict:
    """A synthetic marker with the real compactor's output SHAPE — used only
    to prove the marker itself is never authentication."""
    return {
        "project_id": project_id,
        "cleanup": {"metadata_compacted": True,
                    "cleaned_at": "2026-11-13T00:00:00Z",
                    "prior_status": "SUCCEEDED",
                    "preserved": ["synthetic forge — never authentication"]},
    }


# --------------------------------------------------------------------------
# positive control: terminal CAPTURED before and after the real compactor
# --------------------------------------------------------------------------

def test_terminal_captured_capture_survives_real_compactor_and_old_cursor(store):
    """Acceptance contract (see module docstring for the open product point).

    Expected:
      * full cursor drain before compaction returns every job event and a
        CAPTURED, fully digest-verified solver_output_log on every page;
      * the real ``compact_terminal_job_metadata`` compacts the job view
        (flat top-level project_id + cleanup tombstone, nested execution
        dropped) and appends exactly one JobCacheCompacted audit event;
      * a fresh read after compaction still certifies CAPTURED (the
        marker-gated project-binding exemption keeps the read admitted;
        see docstring about the summary-side exemption being the open
        point);
      * a cursor issued BEFORE compaction continues with identical frozen
        prefix digests: the compaction audit event appended after the
        frozen bound is never required by, mixed into, or made to
        invalidate the old prefix (no CURSOR_STALE);
      * a direct re-fold of the frozen prefix inside a fresh snapshot
        still completes with the cursor's exact id/events digests.
    """
    ctx = _make_capture(store)
    project, job_id = ctx["project_id"], ctx["job_id"]

    pages, cursor, cursor_before = [], None, None
    while True:
        result = _read(store, project, job_id, cursor=cursor, limit=PAGE_LIMIT)
        assert result["job_status"] == "SUCCEEDED"
        log = result["solver_output_log"]
        assert log["status"] == "CAPTURED", log.get("reason")
        assert log["capture_id"] == ctx["capture_id"]
        assert log["byte_length"] == len(ctx["payload"])
        assert log["sha256"] == _sha(ctx["payload"])
        assert log["sha256_scope"] == "complete_file"
        assert log["chunk_count"] == len(_split_chunks(ctx["payload"]))
        assert log["chunk_event"] == "COMSOLProgressChunk"
        assert log["chunk_encoding"] == "base64"
        assert log["truncated"] is False
        assert log["cleanup_verified"] is True
        assert log["producer"] == PRODUCER
        pages.append(result)
        if cursor is None:
            cursor_before = result["next_cursor"]
        cursor = result["next_cursor"]
        if cursor is None:
            break
    frozen_ids = [event["id"] for page in pages for event in page["events"]]
    frozen_names = [event["event"] for page in pages for event in page["events"]]
    assert frozen_names == ["COMSOLProgressCaptureRequested",
                            "COMSOLProgressCaptureActivated",
                            "COMSOLProgressChunk", "COMSOLProgressChunk",
                            "COMSOLProgressChunk", "COMSOLProgressCaptureCompleted"]
    assert cursor_before is not None
    frozen = _decode_cursor(cursor_before)
    assert frozen["snapshot_count"] == len(frozen_ids)
    assert frozen["snapshot_last_id"] == frozen_ids[-1]
    assert frozen["project_id"] == project and frozen["job_id"] == job_id
    assert frozen["offset"] == PAGE_LIMIT

    cleaned = store.compact_terminal_job_metadata([job_id], project_id=project)
    assert cleaned == [job_id]
    job_view = store.job(job_id)
    assert job_view["status"] in TERMINAL
    # Descriptive shape of the real compactor's output only — never a proof.
    assert job_view["metadata"]["cleanup"]["metadata_compacted"] is True
    assert job_view["metadata"]["project_id"] == project
    assert "execution" not in job_view["metadata"]

    # Direct frozen-prefix revalidation independent of the reader.
    with store.solver_log_snapshot(job_id) as snapshot_view:
        assert snapshot_view is not None
        folded = store.fold_job_event_prefix(
            job_id, snapshot_count=frozen["snapshot_count"],
            snapshot_last_id=frozen["snapshot_last_id"],
            max_row_bytes=4 * 1024 * 1024, visitor=lambda row: None,
            _join_read_snapshot=True)
    assert folded["complete"] is True
    assert folded["id_sha256"] == frozen["id_sha256"]
    assert folded["events_sha256"] == frozen["events_sha256"]

    # Fresh post-compaction read: CAPTURED stays certifiable, and the whole
    # fresh drain now includes the appended audit event exactly once.
    fresh_pages, cursor2 = [], None
    while True:
        result = _read(store, project, job_id, cursor=cursor2, limit=PAGE_LIMIT)
        assert result["solver_output_log"]["status"] == "CAPTURED", \
            result["solver_output_log"].get("reason")
        fresh_pages.append(result)
        cursor2 = result["next_cursor"]
        if cursor2 is None:
            break
    fresh_names = [event["event"] for page in fresh_pages for event in page["events"]]
    assert fresh_names.count("JobCacheCompacted") == 1

    # Pre-issued cursor continuation: same rows as before compaction, same
    # frozen prefix digests, no CURSOR_STALE, and the audit event is never
    # required by or shown inside the old prefix.
    expected_second_page_ids = [event["id"] for event in pages[1]["events"]]
    expected_third_page_ids = [event["id"] for event in pages[2]["events"]]
    cont1 = _read(store, project, job_id, cursor=cursor_before)
    assert [event["id"] for event in cont1["events"]] == expected_second_page_ids
    assert cont1["snapshot"]["event_count"] == len(frozen_ids)
    assert cont1["snapshot"]["id_sha256"] == frozen["id_sha256"]
    assert cont1["snapshot"]["events_sha256"] == frozen["events_sha256"]
    assert cont1["solver_output_log"]["status"] == "CAPTURED", \
        cont1["solver_output_log"].get("reason")
    cont2 = _read(store, project, job_id, cursor=cont1["next_cursor"])
    assert [event["id"] for event in cont2["events"]] == expected_third_page_ids
    assert cont2["next_cursor"] is None
    continuation_names = ([event["event"] for event in cont1["events"]]
                          + [event["event"] for event in cont2["events"]])
    assert "JobCacheCompacted" not in continuation_names
    cont_cursor = _decode_cursor(cont1["next_cursor"])
    assert cont_cursor["snapshot_count"] == frozen["snapshot_count"]
    assert cont_cursor["snapshot_last_id"] == frozen["snapshot_last_id"]
    assert cont_cursor["id_sha256"] == frozen["id_sha256"]
    assert cont_cursor["events_sha256"] == frozen["events_sha256"]


# --------------------------------------------------------------------------
# lifecycle/sequence negatives -> honest UNKNOWN (not CAPTURED, not stored error)
# --------------------------------------------------------------------------

def test_removed_activation_receipt_is_never_certified(store):
    """Expected: missing Activated receipt -> UNKNOWN 'no unique activation
    receipt'; the stored job result stays untouched and is not reported."""
    ctx = _make_capture(store)
    _delete_event(store, ctx, "COMSOLProgressCaptureActivated")
    result = _assert_unknown(store, ctx, "completion has no unique activation receipt")
    assert result["exception_chain"]["status"] == "NOT_CAPTURED"


def test_missing_completion_receipt_is_honest_nonterminal_diagnostic(store):
    """Expected: no Completed receipt on a terminal job -> UNKNOWN 'no
    terminal disable/readback receipt' (the IN_PROGRESS branch would only
    apply while the job itself is QUEUED/STARTING/RUNNING)."""
    ctx = _make_capture(store)
    _delete_event(store, ctx, "COMSOLProgressCaptureCompleted")
    _assert_unknown(store, ctx, "no terminal disable/readback receipt")


def test_nonterminal_capture_without_completion_reports_in_progress(store):
    """Expected: capture without completion while the job is still RUNNING
    -> status IN_PROGRESS with no text, never CAPTURED."""
    ctx = _make_capture(store, terminal=False)
    _delete_event(store, ctx, "COMSOLProgressCaptureCompleted")
    result = _read(store, ctx["project_id"], ctx["job_id"])
    log = result["solver_output_log"]
    assert log["status"] == "IN_PROGRESS"
    assert log["text"] is None
    assert log["capture_id"] == ctx["capture_id"]


# --------------------------------------------------------------------------
# tamper negatives -> UNKNOWN / CURSOR_STALE
# --------------------------------------------------------------------------

def test_operation_execution_revision_tamper_is_unknown(store):
    """Tamper only the operation-side execution.expected_revision: the two
    sides no longer share one exact project/model/revision binding ->
    UNKNOWN; the reader must not certify the capture."""
    ctx = _make_capture(store)
    assert _read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]["status"] == "CAPTURED"

    def bump(metadata: dict) -> None:
        metadata["execution"]["expected_revision"] += 7

    _mutate_row_metadata(store, "operations", "operation_id",
                         ctx["record"]["operation_id"], bump)
    _assert_unknown(store, ctx, "complete project/model/revision binding")


def test_both_side_revision_tamper_breaks_event_binding_only(store):
    """Tamper both durable execution mappings consistently: project binding
    still holds and the two sides still agree, but the durable revision now
    contradicts the 12-field binding carried by every progress event ->
    UNKNOWN 'progress producer event identity differs'."""
    ctx = _make_capture(store)

    def bump(metadata: dict) -> None:
        metadata["execution"]["expected_revision"] += 7

    _mutate_row_metadata(store, "operations", "operation_id",
                         ctx["record"]["operation_id"], bump)
    _mutate_row_metadata(store, "jobs", "job_id", ctx["job_id"], bump)
    _assert_unknown(store, ctx, "progress producer event identity differs")


def test_event_payload_tamper_breaks_new_read_and_frozen_cursor(store):
    """Tamper one stored COMSOLProgressChunk row (byte_length):
      * a fresh read must return UNKNOWN with the chunk SHA/length reason;
      * a cursor issued before the tamper must fail closed as CURSOR_STALE,
        because the recomputed prefix events_sha256 no longer matches the
        cursor — a pre-issued cursor is never trusted without re-verifying
        its whole prefix."""
    ctx = _make_capture(store)
    first = _read(store, ctx["project_id"], ctx["job_id"], limit=PAGE_LIMIT)
    old_cursor = first["next_cursor"]
    assert old_cursor is not None

    def inflate(metadata: dict) -> None:
        metadata["byte_length"] += 1

    _mutate_event(store, ctx, "COMSOLProgressChunk", inflate)
    _assert_unknown(store, ctx, "chunk length or SHA-256 does not match its bytes")
    _assert_refusal(
        lambda: _read(store, ctx["project_id"], ctx["job_id"],
                      cursor=old_cursor, limit=PAGE_LIMIT),
        "CURSOR_STALE")


# --------------------------------------------------------------------------
# marker / project-binding negatives: reject or never certify
# --------------------------------------------------------------------------

def test_missing_compaction_marker_flat_job_claim_is_not_certified(store):
    """A terminal job with ONLY a flat top-level project_id (no nested
    execution, no compaction marker) is never admitted: the marker-gated
    exemption exists only for the real compactor's output shape."""
    ctx = _make_capture(store)
    _replace_job_metadata(store, ctx["job_id"], {"project_id": ctx["project_id"]})
    _assert_refusal(lambda: _read(store, ctx["project_id"], ctx["job_id"]),
                    "PROJECT_IDENTITY_MISMATCH")


def test_nonterminal_job_marker_is_rejected_by_store_and_reader(store):
    """Expected:
      * the real compactor refuses a non-terminal job (JOB_NOT_TERMINAL);
      * forging the marker on a RUNNING job does not authenticate the flat
        claim: the reader still refuses with PROJECT_IDENTITY_MISMATCH."""
    ctx = _make_capture(store, terminal=False)
    with pytest.raises(JobCleanupError) as excinfo:
        store.compact_terminal_job_metadata([ctx["job_id"]], project_id=ctx["project_id"])
    assert excinfo.value.code == "JOB_NOT_TERMINAL"

    _replace_job_metadata(store, ctx["job_id"], _forged_marker(ctx["project_id"]))
    forged_view = store.job(ctx["job_id"])
    assert forged_view is not None
    assert forged_view["status"] not in TERMINAL
    _assert_refusal(lambda: _read(store, ctx["project_id"], ctx["job_id"]),
                    "PROJECT_IDENTITY_MISMATCH")


def test_forged_marker_with_wrong_project_is_never_authentication(store):
    """A marker-shaped claim naming a different project must not certify the
    read for the requested project, even though both sides look internally
    well-formed."""
    ctx = _make_capture(store)
    _replace_job_metadata(store, ctx["job_id"], _forged_marker("t35x-foreign-" + uuid.uuid4().hex))
    _assert_refusal(lambda: _read(store, ctx["project_id"], ctx["job_id"]),
                    "PROJECT_IDENTITY_MISMATCH")


def test_execution_key_present_but_empty_is_denied(store):
    """execution exists but names no project ID: no side independently
    contributes a claim, so the store compactor refuses
    (PROJECT_BINDING_UNKNOWN) and the reader refuses
    (PROJECT_IDENTITY_MISMATCH)."""
    project = _new_project()
    record = _admit(store, project, metadata={"operation": "study.run", "execution": {}})
    job_id = record["job_id"]
    store.update_job(job_id, "SUCCEEDED")
    with pytest.raises(JobCleanupError) as excinfo:
        store.compact_terminal_job_metadata([job_id])
    assert excinfo.value.code == "PROJECT_BINDING_UNKNOWN"
    _assert_refusal(lambda: _read(store, project, job_id), "PROJECT_IDENTITY_MISMATCH")


def test_execution_key_conflicting_claims_are_denied(store):
    """A top-level project claim that conflicts with the nested execution
    claim yields no unambiguous binding: even when each side individually
    matches the requested project, the disagreement fails closed."""
    project = _new_project()
    execution_project = project
    top_level_conflict = project + "-conflict"
    record = _admit(store, execution_project, metadata={
        "operation": "study.run",
        "project_id": top_level_conflict,
        "execution": {
            "project_id": execution_project,
            "session_id": "t35x-session-" + uuid.uuid4().hex,
            "model_ref": {"kind": "t35x-synthetic-model", "token": uuid.uuid4().hex},
            "expected_revision": 1,
        },
    })
    job_id = record["job_id"]
    store.update_job(job_id, "SUCCEEDED")
    with pytest.raises(JobCleanupError):
        store.compact_terminal_job_metadata([job_id])
    _assert_refusal(lambda: _read(store, execution_project, job_id),
                    "PROJECT_IDENTITY_MISMATCH")


def test_malformed_project_claim_is_denied(store):
    """A present-but-empty-string project ID claim is malformed: no binding
    is certified by the store or admitted by the reader."""
    project = _new_project()
    record = _admit(store, project, metadata={
        "operation": "study.run",
        "execution": {"project_id": "", "session_id": "t35x-s",
                      "model_ref": {"kind": "t35x", "token": "x"},
                      "expected_revision": 0},
    })
    job_id = record["job_id"]
    store.update_job(job_id, "SUCCEEDED")
    with pytest.raises(JobCleanupError) as excinfo:
        store.compact_terminal_job_metadata([job_id])
    assert excinfo.value.code == "PROJECT_BINDING_UNKNOWN"
    _assert_refusal(lambda: _read(store, project, job_id), "PROJECT_IDENTITY_MISMATCH")


def test_cross_project_read_and_cursor_are_denied(store):
    """Reading another project's job with this project's ID is refused
    (PROJECT_IDENTITY_MISMATCH), and a cursor bound to one project/job pair
    cannot be replayed against another (CURSOR_BINDING_MISMATCH)."""
    ctx_a = _make_capture(store)
    ctx_b = _make_capture(store)
    _assert_refusal(
        lambda: _read(store, ctx_a["project_id"], ctx_b["job_id"]),
        "PROJECT_IDENTITY_MISMATCH")
    first_a = _read(store, ctx_a["project_id"], ctx_a["job_id"], limit=PAGE_LIMIT)
    cursor_a = first_a["next_cursor"]
    assert cursor_a is not None
    _assert_refusal(
        lambda: _read(store, ctx_a["project_id"], ctx_b["job_id"],
                      cursor=cursor_a, limit=PAGE_LIMIT),
        "CURSOR_BINDING_MISMATCH")
    _assert_refusal(
        lambda: _read(store, ctx_b["project_id"], ctx_a["job_id"],
                      cursor=cursor_a, limit=PAGE_LIMIT),
        "CURSOR_BINDING_MISMATCH")


# --------------------------------------------------------------------------
# compactor scope negatives (store side)
# --------------------------------------------------------------------------

def test_compactor_cross_scope_selection_is_refused(store):
    """Scoping the compactor to a different project must refuse the whole
    batch (PROJECT_SCOPE_MISMATCH) before rewriting anything."""
    ctx = _make_capture(store)
    with pytest.raises(JobCleanupError) as excinfo:
        store.compact_terminal_job_metadata(
            [ctx["job_id"]], project_id="t35x-foreign-" + uuid.uuid4().hex)
    assert excinfo.value.code == "PROJECT_SCOPE_MISMATCH"
    scope_view = store.job(ctx["job_id"])
    assert scope_view is not None
    assert "execution" in scope_view["metadata"]


# --------------------------------------------------------------------------
# parametrized producer-contract negatives: base64, digest, sequence,
# 12-field binding, terminal receipt, capture identity
# --------------------------------------------------------------------------

def _mut_bad_base64(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressChunk",
                  lambda m: m.update(content_base64="%%%not-base64%%%"))


def _mut_bad_chunk_sha(store, ctx):
    def flip(m: dict) -> None:
        digest = m["chunk_sha256"]
        m["chunk_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]

    _mutate_event(store, ctx, "COMSOLProgressChunk", flip)


def _mut_offset_gap(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressChunk",
                  lambda m: m.update(byte_offset=m["byte_offset"] + 1))


def _mut_drop_binding_field_request_hash(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressCaptureActivated",
                  lambda m: m.pop("request_hash"))


def _mut_conflicting_capture_id(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressChunk",
                  lambda m: m.update(capture_id="t35x-cap-conflict-" + uuid.uuid4().hex))


def _mut_conflicting_binding_model_revision(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressCaptureRequested",
                  lambda m: m.update(model_revision=m["model_revision"] + 5))


def _mut_duplicate_completed(store, ctx):
    completed = _event_rows(store, ctx["job_id"], "COMSOLProgressCaptureCompleted")[0][1]
    store.add_event(ctx["job_id"], "COMSOLProgressCaptureCompleted", completed)


def _mut_cleanup_not_verified(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressCaptureCompleted",
                  lambda m: m.update(cleanup_verified=False, scratch_retained=True))


def _mut_receipt_sha_mismatch(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressCaptureCompleted",
                  lambda m: m.update(captured_sha256=_sha(b"t35x different bytes")))


def _mut_terminal_state_byte_conflict(store, ctx):
    _mutate_event(store, ctx, "COMSOLProgressCaptureCompleted",
                  lambda m: m.update(capture_state="NO_PROGRESS_TEXT"))


_CONTRACT_CASES = [
    pytest.param(_mut_bad_base64, "not valid base64", id="chunk-not-base64"),
    pytest.param(_mut_bad_chunk_sha, "SHA-256 does not match its bytes", id="chunk-sha-mismatch"),
    pytest.param(_mut_offset_gap, "chunk sequence or encoding is malformed", id="chunk-offset-gap"),
    pytest.param(_mut_drop_binding_field_request_hash,
                 "progress producer event identity differs", id="binding-field-dropped"),
    pytest.param(_mut_conflicting_capture_id,
                 "missing or conflicting progress capture identities", id="capture-id-conflict"),
    pytest.param(_mut_conflicting_binding_model_revision,
                 "progress producer event identity differs", id="binding-revision-conflict"),
    pytest.param(_mut_duplicate_completed,
                 "duplicate or incomplete lifecycle records", id="duplicate-completed"),
    pytest.param(_mut_cleanup_not_verified,
                 "malformed size, digest, cleanup or truncation fields", id="cleanup-not-verified"),
    pytest.param(_mut_receipt_sha_mismatch,
                 "do not match the terminal size/count/SHA-256 receipt", id="receipt-sha-mismatch"),
    pytest.param(_mut_terminal_state_byte_conflict,
                 "terminal state conflicts with byte/truncation evidence", id="state-byte-conflict"),
]


@pytest.mark.parametrize("mutate,reason_substring", _CONTRACT_CASES)
def test_damaged_producer_contract_is_unknown_never_certified(store, mutate, reason_substring):
    """Each mutation damages exactly one producer-contract guarantee (12-field
    binding, capture identity, lifecycle sequence, chunk base64/SHA/sequence,
    terminal receipt bytes/digest/cleanup/truncation consistency). Expected:
    the reader fails closed to UNKNOWN with the matching reason — never
    CAPTURED and never a silent 'no capture present' fallback."""
    ctx = _make_capture(store)
    mutate(store, ctx)
    _assert_unknown(store, ctx, reason_substring, limit=PAGE_LIMIT)


@pytest.mark.parametrize("field", (
    "schema", "producer", "job_id", "operation_id", "request_id",
    "request_hash", "idempotency_key", "project_id", "session_id",
    "model_ref", "model_revision", "operation",
))
def test_compacted_progress_rechecks_each_binding_field(store, field):
    """The compaction shape must not mask a producer binding mismatch."""
    ctx = _make_capture(store)
    assert _read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]["status"] == "CAPTURED"
    assert store.compact_terminal_job_metadata([ctx["job_id"]], project_id=ctx["project_id"]) == [ctx["job_id"]]

    def mutate(metadata: dict) -> None:
        before = metadata[field]
        metadata[field] = ({"synthetic_mismatch": True} if isinstance(before, dict)
                           else before + 1 if type(before) is int
                           else f"synthetic-mismatch:{before}")
        assert metadata[field] != before

    _mutate_event(store, ctx, "COMSOLProgressCaptureRequested", mutate)
    _assert_unknown(store, ctx, "progress producer event identity differs")


def test_compacted_progress_detects_changed_operation_revision(store):
    ctx = _make_capture(store)
    assert _read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]["status"] == "CAPTURED"
    assert store.compact_terminal_job_metadata([ctx["job_id"]], project_id=ctx["project_id"]) == [ctx["job_id"]]
    _mutate_row_metadata(store, "operations", "operation_id", ctx["record"]["operation_id"],
                         lambda metadata: metadata["execution"].__setitem__("expected_revision", 999))
    _assert_unknown(store, ctx, "progress producer event identity differs")


def test_compacted_progress_detects_corrupt_chunk_bytes(store):
    ctx = _make_capture(store)
    assert _read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]["status"] == "CAPTURED"
    assert store.compact_terminal_job_metadata([ctx["job_id"]], project_id=ctx["project_id"]) == [ctx["job_id"]]
    _mut_bad_chunk_sha(store, ctx)
    _assert_unknown(store, ctx, "chunk length or SHA-256 does not match its bytes")


def test_marker_with_present_empty_execution_is_not_compacted_shape(store):
    ctx = _make_capture(store)
    metadata = _forged_marker(ctx["project_id"])
    metadata["execution"] = {}
    _replace_job_metadata(store, ctx["job_id"], metadata)
    _assert_refusal(lambda: _read(store, ctx["project_id"], ctx["job_id"]),
                    "PROJECT_IDENTITY_MISMATCH")
