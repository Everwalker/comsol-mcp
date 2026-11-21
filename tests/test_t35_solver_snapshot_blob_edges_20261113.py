"""T35 BLOB-stored-result edge tests for ``solver.log_read`` and the bounded
``project_operation_result_error`` projection (authored, NOT RUN).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only: fresh ``OperationStore(":memory:")`` ledgers with
  brand-new synthetic ``begin``/``add_event``/``finish`` state, driven
  through the real ``solver_log_read`` / ``solver_log_snapshot`` /
  ``project_operation_result_error`` primitives on each store's own single
  SQLite connection. No daemon, ControlDaemon, Worker, COMSOL, SSH, model,
  endpoint, credential, historical/prior DB, or real UNKNOWN job is
  touched; nothing here uses tmp_path fixtures, disk files, threads,
  subprocesses, network, or deletes.
- Every stored result below is this file's own synthesis inserted through a
  parameterized bytes bind (``UPDATE operations SET result=?`` with a
  ``bytes`` value), never historical data and never interpolated SQL text.
  The synthesis runs only on this file's own synthetic rows inside its own
  fresh in-memory ledger, and no synthetic damage is ever repaired,
  truncated further, or rewritten back afterwards.
- These are internal store-consistency primitives: nothing here is, or is
  evidence for, authentication, real project authorization, principal
  identity, WAL behaviour, durability across processes, concurrency, or
  whole-process memory. The ``journal_mode=WAL`` pragma the store issues on
  ``":memory:"`` is asserted and relied upon nowhere.
- ``BLOB_CHUNK_BYTES = 65536`` is a file-local sizing constant that mirrors
  the BLOB slice width documented and implemented inside the currently
  published ``project_operation_result_error`` source (``blob_chunk_bytes =
  65536``). This file patches no module constant and asserts nothing about
  the module's internal names.
- Grounded from the current sources read at authoring time (no line numbers
  or hashes embedded): the projection returns ``None`` for a NULL or empty
  TEXT/BLOB result, slices BLOB in 65536-byte ``substr`` units fed "once,
  in order, as UTF-8 byte chunks" to the ``project_error_chunks`` helper
  imported by ``_operation_store``, resolves duplicate top-level ``error``
  keys last-wins exactly like ``json.loads``, propagates document damage /
  invalid UTF-8 rather than disguising it, and ``solver_log_read`` maps
  every such projection failure to ``EXECUTION_STATE_UNKNOWN`` echoing
  only the exception class name, never the stored payload, with no
  empty-error or event-chain fallback.
- Explicit authoring-time inference (labelled unknown): the exact incremental
  UTF-8 decoder inside ``project_error_chunks`` was NOT in this file's
  permitted reading scope; the "multi-byte character split across the
  65536-byte chunk boundary still projects exactly" expectations in
  tests 1 and 2 therefore rest on the projection's published
  byte-chunk/last-wins contract itself, not on a direct read of that
  helper. Likewise the exact exception CLASS raised by the helper for NUL,
  invalid UTF-8, or tail damage is not pinned here (``solver_log_read``
  deliberately collapses it to ``EXECUTION_STATE_UNKNOWN`` plus the class
  name); the direct-primitive refusals are asserted as "some non-KeyError
  exception with a marker-free surface".
- Per-test expected status against the current source (all expected GREEN;
  this file was authored and never executed, so nothing below may be cited
  as a run or a passed result):
  * test 1 (one error, 4-byte UTF-8 char split exactly at byte 65536):
    first tripwire is the ``exception_chain["items"]`` equality.
  * test 2 (duplicate top-level errors, winner entirely in slice 2):
    first tripwire is the direct-projection envelope equality, then the
    decoy-marker absence.
  * tests 3/4 (raw NUL / invalid UTF-8 byte inside slice 2): first tripwire
    is ``pytest.raises`` around the direct projection.
  * test 5 (empty BLOB positive control): first tripwire is the direct
    projection returning exactly ``None``.
  * test 6 (valid error early, corrupted tail inside slice 2): first
    tripwire is ``pytest.raises`` around the direct projection — returning
    the early error would mask the tail damage.
- Each test opens its own fresh ``":memory:"`` store and closes only its
  own store in ``finally``; the parent agent owns running this file.
"""
from __future__ import annotations

import json

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp import _g3_solver_ops as solver
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-blob-session"

# File-local mirror of the projection's documented 65536-byte BLOB slice
# width (see module docstring); used only to size synthesis across slices.
BLOB_CHUNK_BYTES = 65536

# A refusal/diagnostic surface must stay small enough to log safely.
ERROR_SURFACE_MAX_CHARS = 4096

DECOY_CHAIN = [{"message": "t35-blob-event-decoy"}]
DECOY_MARKER = "T35-BLOB-EVENT-DECOY"

CROSS_ERROR = {
    "code": "T35_BLOB_CROSS_CHUNK",
    "message": "t35-blob-cross-chunk 顶层错误 \u2705",
    "cause_chain": [
        {"type": "T35BlobCrossChunkException",
         "message": "t35-blob-cross-chunk-chain-1 边界"},
    ],
}

FIRST_DECOY_CODE = "T35_BLOB_FIRST_DECOY"
FIRST_DECOY_MARKER = "T35-BLOB-FIRST-DUPLICATE-ERROR-DECOY"
LAST_WINS_MARKER = "T35-BLOB-LAST-DUPLICATE-ERROR-WINS"
LAST_WINS_ERROR = {
    "code": "T35_BLOB_LAST_WINS",
    "message": f"{LAST_WINS_MARKER} 顶层错误 \u2705",
    "cause_chain": [
        {"type": "T35BlobLastWinsException",
         "message": f"{LAST_WINS_MARKER}-chain-1 跨切片"},
    ],
}

NUL_MARKER = "T35-BLOB-RAW-NUL-MARKER"
BAD_UTF8_MARKER = "T35-BLOB-BAD-UTF8-MARKER"
CORRUPT_TAIL_MARKER = "T35-BLOB-CORRUPT-TAIL-MARKER"


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _seed_failed_job(store: OperationStore, project: str, tag: str) -> dict:
    """Fresh synthetic FAILED job with the complete project/execution
    binding ``_g3_solver_ops._job_project_binding`` requires on both rows,
    one plain event and one small exception-chain decoy event."""
    record, reused = store.begin(
        request_id=f"t35-blob-req-{tag}",
        idempotency_key=f"t35-blob-key-{tag}",
        request_hash=f"t35-blob-hash-{tag}",
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {},
            "execution": {"project_id": project, "session_id": SESSION},
        },
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    store.add_event(record["job_id"], "T35BlobPlain", {"n": 0})
    store.add_event(record["job_id"], f"{DECOY_MARKER}{tag}",
                    {"exception_chain": DECOY_CHAIN})
    accepted, authoritative = store.finish(
        record["operation_id"], status="FAILED",
        result={"success": False, "note": "t35-blob-placeholder"},
    )
    assert accepted is True and authoritative == "FAILED"
    return record


def _put_blob(store: OperationStore, operation_id: str, blob: bytes) -> None:
    """Parameterized BLOB bind on this test's own row (never SQL text)."""
    cursor = store.db.execute(
        "UPDATE operations SET result=? WHERE operation_id=?", (blob, operation_id)
    )
    assert cursor.rowcount == 1


def _result_probe(store: OperationStore, operation_id: str) -> tuple[str, int]:
    row = store.db.execute(
        "SELECT typeof(result) AS result_type,length(result) AS result_length "
        "FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return row["result_type"], row["result_length"]


def _result_bytes(store: OperationStore, operation_id: str) -> bytes:
    value = store.db.execute(
        "SELECT result FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]
    assert isinstance(value, (bytes, bytearray))
    return bytes(value)


def _assert_durable_blob(store: OperationStore, record: dict,
                         blob: bytes) -> None:
    job_id, operation_id = record["job_id"], record["operation_id"]
    assert _result_probe(store, operation_id) == ("blob", len(blob))
    assert _result_bytes(store, operation_id) == blob
    for table, key in (("jobs", job_id), ("operations", operation_id)):
        status = store.db.execute(
            f"SELECT status FROM {table} WHERE {table[:-1]}_id=?", (key,)
        ).fetchone()[0]
        assert status == "FAILED"
    assert len(store.events(job_id)) == 2
    assert store.db.in_transaction is False


def _crossing_blob(prefix: bytes, tail: bytes) -> bytes:
    """Assemble a BLOB whose 4-byte UTF-8 character straddles byte
    ``BLOB_CHUNK_BYTES`` exactly: the lead byte is the last byte of slice 1
    and the three continuation bytes are the first bytes of slice 2.
    ``prefix`` must end inside an open JSON string."""
    filler = BLOB_CHUNK_BYTES - 1 - len(prefix)
    assert filler > 0
    blob = (prefix + b"p" * filler + "\U0001F600".encode("utf-8")
            + b"q" * 512 + tail)
    assert len(blob) > BLOB_CHUNK_BYTES
    assert blob[BLOB_CHUNK_BYTES - 1] == 0xF0
    assert blob[BLOB_CHUNK_BYTES:BLOB_CHUNK_BYTES + 3] == b"\x9f\x98\x80"
    return blob


def _refusal_surface(exc: ExecutionContractError) -> str:
    surface = str(exc)
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        surface += json.dumps(details, default=str)
    return surface


def test_t35_blob_multibyte_split_at_65536_boundary_keeps_top_level_error():
    """One stored BLOB result > 65536 bytes whose multi-byte UTF-8 padding
    is split exactly across the projection's first BLOB slice boundary must
    still yield the top-level ``error`` exactly, both through the direct
    bounded projection and through the bound ``solver_log_read``."""
    project = "t35-blob-cross-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "cross")
        job_id, operation_id = record["job_id"], record["operation_id"]

        prefix = (b'{"success": false, "error": ' + _json_bytes(CROSS_ERROR)
                  + b', "pad": "')
        blob = _crossing_blob(prefix, b'"}')
        _put_blob(store, operation_id, blob)
        assert _result_probe(store, operation_id) == ("blob", len(blob))

        # Grounding control for the synthesis only (pure CPython): the
        # exact durable bytes are valid UTF-8 and decode to this error.
        control = json.loads(blob.decode("utf-8"))
        assert control["error"] == CROSS_ERROR

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None and view["result"] is None
            projection = store.project_operation_result_error(
                operation_id, max_error_bytes=16384
            )
            assert projection == {"error": CROSS_ERROR}
        assert store.db.in_transaction is False

        page = solver.solver_log_read(store, project,
                                      {"job_id": job_id, "limit": 10})
        chain = page["exception_chain"]
        assert chain["status"] == "CAPTURED"
        assert chain["source"] == "stored result.error.cause_chain"
        assert chain["items"] == CROSS_ERROR["cause_chain"]
        surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        assert DECOY_MARKER not in surface
        assert store.db.in_transaction is False

        _assert_durable_blob(store, record, blob)
    finally:
        store.close()


def test_t35_blob_duplicate_top_level_errors_last_wins_across_slices():
    """Two top-level ``error`` keys with the second (accurate, non-ASCII)
    error entirely inside slice 2: the bounded projection must resolve
    duplicate-key LAST-wins across the 65536-byte slice boundary, never
    the slice-1 decoy."""
    project = "t35-blob-dupkeys-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "dupkeys")
        job_id, operation_id = record["job_id"], record["operation_id"]

        decoy = {
            "code": FIRST_DECOY_CODE,
            "message": FIRST_DECOY_MARKER,
            "cause_chain": [{"type": "T35BlobFirstDecoyChain",
                             "message": f"{FIRST_DECOY_MARKER}-chain"}],
        }
        prefix = (b'{"success": false, "error": ' + _json_bytes(decoy)
                  + b', "pad": "')
        blob = _crossing_blob(prefix, b'", "error": ' + _json_bytes(LAST_WINS_ERROR) + b"}")
        assert blob.count(b'"error"') == 2
        # The winning key begins after the first slice boundary, so its
        # JSON bytes are admitted only in a later chunk than the decoy's.
        assert blob.rindex(b'"error"') >= BLOB_CHUNK_BYTES
        _put_blob(store, operation_id, blob)
        assert _result_probe(store, operation_id) == ("blob", len(blob))

        # Semantics reference (pure CPython json.loads on the exact
        # durable bytes): the duplicate resolves to the LAST occurrence.
        control = json.loads(blob.decode("utf-8"))
        assert control["error"] == LAST_WINS_ERROR

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None
            projection = store.project_operation_result_error(
                operation_id, max_error_bytes=16384
            )
            assert projection == {"error": LAST_WINS_ERROR}
        assert store.db.in_transaction is False

        page = solver.solver_log_read(store, project,
                                      {"job_id": job_id, "limit": 10})
        chain = page["exception_chain"]
        assert chain["status"] == "CAPTURED"
        assert chain["source"] == "stored result.error.cause_chain"
        assert chain["items"] == LAST_WINS_ERROR["cause_chain"]
        surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        assert FIRST_DECOY_MARKER not in surface and FIRST_DECOY_CODE not in surface, (
            "the bounded BLOB projection must follow duplicate-key last-wins, "
            "not a first-slice selection"
        )
        assert store.db.in_transaction is False

        _assert_durable_blob(store, record, blob)
    finally:
        store.close()


def test_t35_blob_raw_nul_in_slice2_fails_closed_without_leak():
    """A raw 0x00 byte inside slice 2 of a BLOB result is invalid strict
    JSON: both the direct bounded projection and ``solver_log_read`` must
    fail closed (never returning an envelope, never falling back to the
    decoy event chain) and neither surface may leak the marker or the NUL."""
    project = "t35-blob-nul-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "nul")
        job_id, operation_id = record["job_id"], record["operation_id"]

        decoy = {
            "code": FIRST_DECOY_CODE,
            "message": FIRST_DECOY_MARKER,
            "cause_chain": [{"type": "T35BlobNulDecoyChain",
                             "message": f"{FIRST_DECOY_MARKER}-chain"}],
        }
        prefix = (b'{"success": false, "error": ' + _json_bytes(decoy)
                  + b', "pad": "')
        filler = b"a" * (BLOB_CHUNK_BYTES + 64 - len(prefix))
        blob = (prefix + filler + b"\x00"
                + NUL_MARKER.encode("ascii") + b' end"}')
        assert len(blob) > BLOB_CHUNK_BYTES
        assert blob.count(b"\x00") == 1
        assert blob.index(b"\x00") > BLOB_CHUNK_BYTES
        _put_blob(store, operation_id, blob)
        assert _result_probe(store, operation_id) == ("blob", len(blob))

        # Grounded invalidity reference (pure CPython): the bytes ARE valid
        # UTF-8 (NUL decodes) but the raw control character makes the JSON
        # invalid — this is document damage, not a decoding shortcut.
        decoded = blob.decode("utf-8")
        assert "\x00" in decoded
        with pytest.raises(json.JSONDecodeError):
            json.loads(decoded)

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None
            with pytest.raises(Exception) as refused:
                store.project_operation_result_error(
                    operation_id, max_error_bytes=16384
                )
            assert not isinstance(refused.value, KeyError)
            direct_surface = str(refused.value)
            assert NUL_MARKER not in direct_surface and "\x00" not in direct_surface
            # The projection never commits or rolls back the owner's
            # snapshot; the context still concludes it.
            assert store.db.in_transaction is True
        assert store.db.in_transaction is False

        with pytest.raises(ExecutionContractError) as refused:
            solver.solver_log_read(store, project,
                                   {"job_id": job_id, "limit": 10})
        assert refused.value.code == "EXECUTION_STATE_UNKNOWN"
        surface = _refusal_surface(refused.value)
        assert NUL_MARKER not in surface and "\x00" not in surface
        assert DECOY_MARKER not in surface, (
            "a damaged stored BLOB must never fall back to the event chain"
        )
        assert len(surface) <= ERROR_SURFACE_MAX_CHARS

        _assert_durable_blob(store, record, blob)
    finally:
        store.close()


def test_t35_blob_invalid_utf8_in_slice2_fails_closed_without_leak():
    """An invalid UTF-8 sequence inside slice 2 of a BLOB result must fail
    closed on both surfaces with a marker-free refusal and no event-chain
    substitution."""
    project = "t35-blob-badutf8-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "badutf8")
        job_id, operation_id = record["job_id"], record["operation_id"]

        decoy = {
            "code": FIRST_DECOY_CODE,
            "message": FIRST_DECOY_MARKER,
            "cause_chain": [{"type": "T35BlobBadUtf8DecoyChain",
                             "message": f"{FIRST_DECOY_MARKER}-chain"}],
        }
        prefix = (b'{"success": false, "error": ' + _json_bytes(decoy)
                  + b', "pad": "')
        filler = b"a" * (BLOB_CHUNK_BYTES + 64 - len(prefix))
        # 0xC3 lead byte followed by '(' is not a valid UTF-8 continuation.
        blob = (prefix + filler + b"\xc3\x28"
                + BAD_UTF8_MARKER.encode("ascii") + b'"}')
        assert len(blob) > BLOB_CHUNK_BYTES
        assert blob.index(b"\xc3\x28") > BLOB_CHUNK_BYTES
        _put_blob(store, operation_id, blob)
        assert _result_probe(store, operation_id) == ("blob", len(blob))

        # Grounded invalidity reference (pure CPython strict decode).
        with pytest.raises(UnicodeDecodeError):
            blob.decode("utf-8")

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None
            with pytest.raises(Exception) as refused:
                store.project_operation_result_error(
                    operation_id, max_error_bytes=16384
                )
            assert not isinstance(refused.value, KeyError)
            assert BAD_UTF8_MARKER not in str(refused.value)
            assert store.db.in_transaction is True
        assert store.db.in_transaction is False

        with pytest.raises(ExecutionContractError) as refused:
            solver.solver_log_read(store, project,
                                   {"job_id": job_id, "limit": 10})
        assert refused.value.code == "EXECUTION_STATE_UNKNOWN"
        surface = _refusal_surface(refused.value)
        assert BAD_UTF8_MARKER not in surface and DECOY_MARKER not in surface
        assert len(surface) <= ERROR_SURFACE_MAX_CHARS

        _assert_durable_blob(store, record, blob)
    finally:
        store.close()


def test_t35_empty_blob_is_empty_projection_with_legacy_event_fallback():
    """Positive control: an empty BLOB (typeof ``blob``, length 0) projects
    to exactly ``None`` — the same empty mapping as NULL/empty TEXT — and
    the bound read keeps the legacy event-chain fallback instead of any
    refusal, leaving the empty BLOB durable."""
    project = "t35-blob-empty-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "empty")
        job_id, operation_id = record["job_id"], record["operation_id"]

        _put_blob(store, operation_id, b"")
        assert _result_probe(store, operation_id) == ("blob", 0)

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None
            projection = store.project_operation_result_error(
                operation_id, max_error_bytes=16384
            )
            assert projection is None
        assert store.db.in_transaction is False

        page = solver.solver_log_read(store, project,
                                      {"job_id": job_id, "limit": 10})
        chain = page["exception_chain"]
        assert chain["status"] == "CAPTURED"
        assert chain["source"].startswith("event ")
        assert chain["items"] == DECOY_CHAIN
        assert store.db.in_transaction is False

        _assert_durable_blob(store, record, b"")
    finally:
        store.close()


def test_t35_blob_valid_error_with_corrupt_tail_is_never_masked():
    """A BLOB whose valid prefix carries a top-level ``error`` early and
    whose tail is cut mid-string inside slice 2: a streaming reader could
    see the error before the damage, so both surfaces must fail closed on
    the whole-document damage instead of returning the early error (which
    would mask the corrupt tail) or degrading to an event-chain fallback."""
    project = "t35-blob-corrupttail-project"
    store = OperationStore(":memory:")
    try:
        record = _seed_failed_job(store, project, "corrupttail")
        job_id, operation_id = record["job_id"], record["operation_id"]

        decoy = {
            "code": FIRST_DECOY_CODE,
            "message": f"{FIRST_DECOY_MARKER} {CORRUPT_TAIL_MARKER}",
            "cause_chain": [{"type": "T35BlobCorruptTailChain",
                             "message": f"{CORRUPT_TAIL_MARKER}-chain"}],
        }
        prefix = (b'{"success": false, "error": ' + _json_bytes(decoy)
                  + b', "pad": "')
        full = _crossing_blob(prefix, b'", "extra": "tail"}')
        # Cut inside the ASCII 'q' run of slice 2: the string stays open,
        # so the damage is proven without relying on any SQLite JSON
        # function, and it lies strictly after the admitted error.
        corrupt = full[:BLOB_CHUNK_BYTES + 16]
        assert corrupt[-1] == 0x71  # inside the pad string
        assert corrupt.count(b'"error"') == 1
        assert corrupt.index(b'"error"') < BLOB_CHUNK_BYTES
        _put_blob(store, operation_id, corrupt)
        assert _result_probe(store, operation_id) == ("blob", len(corrupt))

        # Grounded invalidity reference (pure CPython): valid UTF-8,
        # unterminated JSON string.
        with pytest.raises(json.JSONDecodeError):
            json.loads(corrupt.decode("utf-8"))

        with store.solver_log_snapshot(job_id) as view:
            assert view is not None
            with pytest.raises(Exception) as refused:
                projection = store.project_operation_result_error(
                    operation_id, max_error_bytes=16384
                )
                pytest.fail(
                    "a corrupt BLOB tail must not be projected as the "
                    f"pre-damage error envelope: {projection!r}"
                )
            assert not isinstance(refused.value, KeyError)
            assert CORRUPT_TAIL_MARKER not in str(refused.value)
            assert store.db.in_transaction is True
        assert store.db.in_transaction is False

        with pytest.raises(ExecutionContractError) as refused:
            solver.solver_log_read(store, project,
                                   {"job_id": job_id, "limit": 10})
        assert refused.value.code == "EXECUTION_STATE_UNKNOWN"
        surface = _refusal_surface(refused.value)
        assert CORRUPT_TAIL_MARKER not in surface and DECOY_MARKER not in surface
        assert len(surface) <= ERROR_SURFACE_MAX_CHARS

        _assert_durable_blob(store, record, corrupt)
    finally:
        store.close()
