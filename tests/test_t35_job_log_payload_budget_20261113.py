"""T35 — job.log payload budgets: oversized stored result vs event-page bytes.

Authored, NOT RUN (no pytest was executed against this file). Nothing here is
a claim of runtime behaviour or a passed result until a pinned runner executes
it against a frozen source snapshot.

What is real (no blanket mocking):
- The REAL unbound ``ControlDaemon._control_read`` (comsol_mcp/_control_daemon.py,
  job_log branch after ``norm_op = operation.replace(".", "_")``) is invoked
  through an inert receiver that binds the REAL unbound helpers ``_error`` and
  ``_exception``. ``_log_exception`` is a no-I/O failure-only sentinel: the
  real one appends to ``control-errors.log`` and must never be reached.
- All durable state goes through REAL ``OperationStore`` paths — ``begin``,
  ``add_event`` (the store's public event-append API; there is no method named
  ``append_event``), ``update_job(..., result=...)``, ``job``, ``events`` —
  on a fresh ``OperationStore(":memory:")`` per test with brand-new synthetic
  job/event/result data. Read-only grounding also uses plain ``SELECT LENGTH``
  / raw-column probes on the same store; nothing is pre-seeded by hand.

HONESTY LIMITS — deliberately NOT verified by these tests:
- This is NOT the real principal/wire: ``_control_read`` is called with the
  unscoped default (``scoped_project_id=None``, the legacy private-caller
  branch where ``job_snapshot`` is ``nullcontext(True)``). No gateway route,
  no MCP wire, no daemon socket, no ProjectAuthority, no execution envelope,
  and no authentication claim is exercised or implied. Outer authority for
  these reads is characterized separately in
  ``tests/test_t35_gateway_real_authority_20261112.py``.
- Synthetic job-owned data only. No COMSOL, no Worker, no SSH, no real
  database, no tmp_path/disk files, no threads/subprocesses, no deletes.
  Nothing here touches previously rejected/fenced validation surfaces (WAL
  guard, protected files, old Worker/SSH, real UNKNOWN jobs), and no rejected
  result is reproduced or reused.
- The current job_log branch (observed while authoring) unconditionally calls
  ``self.store.job(job_id)`` before reading events, and
  ``store.job`` -> ``_job`` -> ``get_operation`` -> ``_op`` ``json.loads`` the
  ENTIRE stored operation result. Line numbers drift; nothing here embeds
  them, and every "current behaviour" claim below is about the public shape
  (``{"success": True, "data": {"job_id", "events", "next_offset"}}``), not
  private structure.

PROPOSED BUDGETS — pending adjudication, NOT an external specification:
- ``PEAK_ALLOWANCE_BYTES`` (4 MiB) is a Python-allocator (tracemalloc)
  regression bound for THESE synthetic pairs only, mirroring
  ``tests/test_t35_result_payload_bound_20261112.py``; it is not a production
  limit. tracemalloc does not measure SQLite C memory, RSS, or file size.
- ``PROPOSED_MAX_PAGE_BYTES`` (4 MiB per single job.log response) and
  ``PROPOSED_TOTAL_EVENT_BYTES`` (8 MiB total event payload across a job's
  tiny-row ledger) are PROPOSED caps awaiting a project ruling on the
  job.log page byte budget. They are asserted ONLY in the two clearly marked
  RED tests. Nothing in this file claims a current API implements them.

Expected outcomes and first-assertion attribution (on failure):
- test 1 (GREEN control): semantic/offset/peak-baseline assertions.
- test 2 (expected RED): every semantic assertion passes first; the FIRST
  proposed-contract assertion reached is the peak-delta bound (the ~16 MiB
  stored result materializes on every read via ``store.job``/``_job``/``_op``).
- test 3 (expected RED): the audit-byte invariance assertion is checked
  FIRST (it should stay GREEN — a read must never delete or rewrite audit
  bytes); only then the fail-closed assertion (the oversized-metadata page
  must not be served as a success envelope) and the EXECUTION_STATE_UNKNOWN
  identifiability assertion. Today the page is served, so the fail-closed
  assertion is the first proposed-contract assertion reached.
- test 4 (GREEN control): limit=1 paging must stay lossless/dup-free with
  contiguous ``next_offset`` and each page under the PROPOSED single-page cap.
- test 5 (expected RED): the paging proof runs first; the FIRST proposed
  assertion reached is that one job.log response must not exceed
  ``PROPOSED_MAX_PAGE_BYTES`` (today one ``limit=3`` call returns the whole
  ~9 MiB ledger in a single response).

If tracemalloc is already tracing when a measurement starts, that test fails
closed immediately and never stops foreign tracing state. Each test closes
only its own ``":memory:"`` store.
"""

from __future__ import annotations

import gc
import hashlib
import json
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

MIB = 1024 * 1024

# Python-allocator regression allowance for these synthetic pairs (see the
# module docstring: proposed/pinned for THIS file, not a production limit).
PEAK_ALLOWANCE_BYTES = 4 * MIB

# One ~16 MiB stored operation result field (test 2) and one ~16 MiB event
# metadata field (test 3). Markers sit at the head of each padding string so
# leak checks search one short token without ever printing the payload.
LARGE_RESULT_CHARS = 16 * MIB
LARGE_RESULT_MARKER = "T35JLB-LARGE-RESULT-NO-LEAK-MARKER"
OVERSIZED_METADATA_CHARS = 16 * MIB
OVERSIZED_METADATA_MARKER = "T35JLB-OVERSIZED-EVENT-NO-LEAK-MARKER"

# Three events, each metadata < the ~4 MiB single-row band, total > 8 MiB
# (tests 4 and 5). The 4 MiB / 8 MiB caps are PROPOSED, pending adjudication.
PER_EVENT_METADATA_CHARS = 3 * MIB
PROPOSED_MAX_PAGE_BYTES = 4 * MIB
PROPOSED_TOTAL_EVENT_BYTES = 8 * MIB
PAGE_EVENT_COUNT = 3
PAGE_EVENT_MARKER_PREFIX = "T35JLB-PAGE-EVT"

JOB_LOG_DATA_KEYS = {"job_id", "events", "next_offset"}
EVENT_ROW_KEYS = {"id", "job_id", "event", "metadata", "created_at"}


class _UnscopedReadReceiver:
    """Inert receiver binding the REAL unbound daemon helpers.

    Only ``.store`` plus ``_error``/``_exception`` are touched by the exercised
    job_log branch; ``_log_exception`` is a failure-only no-I/O sentinel so an
    unexpected generic-exception path fails loudly instead of writing
    ``control-errors.log``.
    """

    def __init__(self, store: OperationStore) -> None:
        self.store = store

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
        return ControlDaemon._error(code, message, **details)

    @staticmethod
    def _exception(exc: BaseException) -> dict[str, Any]:
        return ControlDaemon._exception(exc)

    @staticmethod
    def _log_exception() -> None:
        raise AssertionError(
            "T35JLB inert receiver reached the generic _log_exception path"
        )


def _job_log(receiver: _UnscopedReadReceiver, job_id: str, *,
             offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """One REAL ``ControlDaemon._control_read`` job.log call, unscoped.

    The dotted canonical name is used deliberately: the branch normalizes via
    ``operation.replace(".", "_")`` and the public response shape is what the
    MCP tools ultimately expose. No authority/routing is claimed (module
    docstring).
    """
    return ControlDaemon._control_read(
        receiver, "job.log", {"job_id": job_id, "offset": offset, "limit": limit},  # type: ignore[arg-type]
    )


def _start_job(store: OperationStore, *, tag: str) -> dict[str, Any]:
    """One synthetic job through the real ``begin`` path (both metadata rows)."""
    record, reused = store.begin(
        request_id=f"t35jlb-req-{tag}",
        idempotency_key=f"t35jlb-key-{tag}",
        request_hash=f"t35jlb-hash-{tag}",
        operation="job.synthetic-producer",
        metadata={"t35_synthetic": True, "note": f"t35jlb-{tag}"},
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _add_events(store: OperationStore, job_id: str,
                events: list[tuple[str, dict[str, Any]]]) -> None:
    """Append events through the real public ``add_event`` path."""
    for name, metadata in events:
        store.add_event(job_id, name, metadata)


def _succeed_with_result(store: OperationStore, job_id: str, result: dict[str, Any]) -> None:
    """Move the synthetic job to SUCCEEDED and durably store its result."""
    store.update_job(job_id, "SUCCEEDED", {"t35_synthetic": True}, result=result)


def _tiny_events(count: int = 3) -> list[tuple[str, dict[str, Any]]]:
    return [(f"T35JlbEvent{index}", {"index": index}) for index in range(count)]


def _db_result_length(store: OperationStore, operation_id: str) -> int:
    """Durable result column size (characters) — grounding outside tracing."""
    return store.db.execute(
        "SELECT LENGTH(result) FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]


def _db_raw_result_contains(store: OperationStore, operation_id: str, marker: str) -> bool:
    raw = store.db.execute(
        "SELECT result FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]
    try:
        return marker in raw
    finally:
        del raw


def _db_event_metadata_lengths(store: OperationStore, job_id: str) -> tuple[int, ...]:
    return tuple(
        row[0] for row in store.db.execute(
            "SELECT LENGTH(metadata) FROM job_events WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()
    )


def _audit_fingerprint(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Exact raw audit-byte fingerprint of one job's durable event ledger.

    Reads the raw TEXT bytes of every ``job_events`` row (no JSON decode) and
    digests id/event/metadata with unit separators. Used ONLY outside any
    tracemalloc session; proves a read deleted or rewrote nothing.
    """
    rows = store.db.execute(
        "SELECT id,event,metadata FROM job_events WHERE job_id=? ORDER BY id",
        (job_id,),
    ).fetchall()
    try:
        digest = hashlib.sha256()
        for row in rows:
            digest.update(str(row["id"]).encode("ascii"))
            digest.update(b"\x1f")
            digest.update((row["event"] or "").encode("utf-8"))
            digest.update(b"\x1f")
            metadata = row["metadata"]
            digest.update(metadata if isinstance(metadata, bytes)
                          else (metadata or "").encode("utf-8"))
            digest.update(b"\x1e")
        return {
            "count": len(rows),
            "ids": tuple(int(row["id"]) for row in rows),
            "sha256": digest.hexdigest(),
        }
    finally:
        del rows


def _response_bytes(response: dict[str, Any]) -> int:
    serialized = json.dumps(response, sort_keys=True, ensure_ascii=False, default=str)
    try:
        return len(serialized.encode("utf-8"))
    finally:
        del serialized


def _serialized_blob_contains(response: dict[str, Any], marker: str) -> bool:
    serialized = json.dumps(response, sort_keys=True, ensure_ascii=False, default=str)
    try:
        return marker in serialized
    finally:
        del serialized


def _assert_success_page_shape(page: dict[str, Any], *, job_id: str,
                               expected_events: list[dict[str, Any]],
                               offset: int) -> None:
    """Exact public job_log shape for one successful read (tiny events only)."""
    assert set(page) == {"success", "data"}, f"unexpected envelope keys {sorted(page)}"
    assert page["success"] is True, f"job.log should succeed, got {page!r}"
    data = page["data"]
    assert set(data) == JOB_LOG_DATA_KEYS, f"unexpected job_log data keys {sorted(data)}"
    assert data["job_id"] == job_id, f"job_id echo mismatch: {data['job_id']!r}"
    events = data["events"]
    assert events == expected_events, (
        "event page differs from the durable ledger for this offset/limit window"
    )
    for event in events:
        assert set(event) == EVENT_ROW_KEYS, f"unexpected event row keys {sorted(event)}"
    assert data["next_offset"] == offset + len(events), (
        f"next_offset {data['next_offset']!r} must equal offset + len(events) "
        f"= {offset} + {len(events)}"
    )


def _assert_refusal_shape(refusal: dict[str, Any], *, code: str, label: str) -> dict[str, Any]:
    """Real ``_error`` envelope shape: {"success", "data", "error"}."""
    assert refusal.get("success") is False, f"{label}: expected refusal, got {refusal!r}"
    assert set(refusal) == {"success", "data", "error"}, (
        f"{label}: unexpected refusal envelope keys {sorted(refusal)}"
    )
    assert refusal["data"] == {}, f"{label}: expected empty data, got {refusal['data']!r}"
    error = refusal["error"]
    assert error.get("code") == code, f"{label}: expected error code {code}, got {error!r}"
    assert error.get("safe_retry") is False, f"{label}: expected safe_retry False: {error!r}"
    return error


def _read_with_isolated_tracing(receiver: _UnscopedReadReceiver, job_id: str,
                                *, offset: int, limit: int) -> tuple[dict[str, Any], int]:
    """One job.log read inside a tracemalloc session started AFTER load.

    Fails closed (never stopping foreign state) if tracing is already active.
    """
    if tracemalloc.is_tracing():
        pytest.fail(
            "tracemalloc was already tracing before this test's session; "
            "failing closed and leaving the foreign tracing state untouched"
        )
    tracemalloc.start()
    try:
        page = _job_log(receiver, job_id, offset=offset, limit=limit)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return page, peak


# ---------------------------------------------------------------------------
# 1. GREEN positive control: tiny stored result — exact public page shape,
#    offset/next_offset semantics (full page, tail slice, at-end, past-end),
#    and a real INVALID_REQUEST refusal. Isolated Python peak stays small.
# ---------------------------------------------------------------------------

def test_t35jlb_small_result_job_log_page_semantics_and_peak_positive_control() -> None:
    """Baseline for the proposed result-size comparison below.

    Event names and metadata have the same shape as test 2. This
    control pins the current public shape so a future "fix" cannot silently
    change the response shape while adding a budget.
    """
    store = OperationStore(":memory:")
    try:
        receiver = _UnscopedReadReceiver(store)
        record = _start_job(store, tag="small-ctl")
        job_id = record["job_id"]
        _add_events(store, job_id, _tiny_events(3))
        _succeed_with_result(store, job_id, {
            "success": True,
            "data": {"note": "t35jlb-small-result"},
        })
        assert _db_result_length(store, record["operation_id"]) < 4096

        ground = store.events(job_id, 0, 1000)
        assert len(ground) == 3
        assert _db_event_metadata_lengths(store, job_id) == tuple(
            len(json.dumps(row["metadata"], sort_keys=True)) for row in ground
        )
        gc.collect()

        page, peak = _read_with_isolated_tracing(receiver, job_id, offset=0, limit=100)

        # Completeness/semantics first, peak bound only afterwards.
        _assert_success_page_shape(page, job_id=job_id, expected_events=ground, offset=0)
        ids = [event["id"] for event in page["data"]["events"]]
        assert ids == sorted(set(ids)), "event ids must be strictly increasing and unique"

        tail = _job_log(receiver, job_id, offset=1, limit=100)
        _assert_success_page_shape(tail, job_id=job_id,
                                   expected_events=ground[1:], offset=1)
        at_end = _job_log(receiver, job_id, offset=3, limit=100)
        _assert_success_page_shape(at_end, job_id=job_id, expected_events=[], offset=3)
        past_end = _job_log(receiver, job_id, offset=5, limit=1)
        _assert_success_page_shape(past_end, job_id=job_id, expected_events=[], offset=5)

        # Negative control: the read path itself validates offset/limit with
        # the real _error envelope (current implemented behaviour).
        negative = _job_log(receiver, job_id, offset=-1, limit=100)
        _assert_refusal_shape(negative, code="INVALID_REQUEST",
                              label="job.log negative offset")

        assert peak <= PEAK_ALLOWANCE_BYTES, (
            f"small-result job.log read already exceeded the Python baseline "
            f"bound ({peak} bytes); the RED deltas below would be unattributable"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 2. PROPOSED-contract RED: a ~16 MiB stored result on an otherwise tiny
#    event log must not inflate job.log's Python peak. Current implementation
#    is expected to violate this: job_log calls ``store.job`` before events,
#    and ``_job`` -> ``get_operation`` -> ``_op`` json.loads the ENTIRE
#    stored result on every read. FIRST proposed assertion reached on RED is
#    the peak-delta bound; every semantic assertion above it passes first.
# ---------------------------------------------------------------------------

def test_t35jlb_oversized_stored_result_job_log_peak_delta_red() -> None:
    """Two jobs with matching tiny event names and metadata.

    The proposed bound demands the Python-allocator peak delta between the
    two job.log reads stays within ``PEAK_ALLOWANCE_BYTES``. Their generated
    job/event IDs, timestamps, fixture tags and small result fields also differ;
    this is not a strict one-variable comparison. Byte counts are reported;
    the payload is never printed or reconstructed.
    """
    store = OperationStore(":memory:")
    try:
        receiver = _UnscopedReadReceiver(store)
        small = _start_job(store, tag="res-small")
        large = _start_job(store, tag="res-large")
        assert small["job_id"] != large["job_id"]
        _add_events(store, small["job_id"], _tiny_events(3))
        _add_events(store, large["job_id"], _tiny_events(3))

        _succeed_with_result(store, small["job_id"], {
            "success": True,
            "data": {"note": "t35jlb-small-result"},
        })
        large_field = LARGE_RESULT_MARKER + "L" * (LARGE_RESULT_CHARS - len(LARGE_RESULT_MARKER))
        _succeed_with_result(store, large["job_id"], {
            "success": True,
            "data": {"note": "t35jlb-large-result"},
            "large_synthetic_field": large_field,
        })
        del large_field

        # Durable grounding BEFORE any measurement.
        assert _db_result_length(store, small["operation_id"]) < 4096
        assert _db_result_length(store, large["operation_id"]) >= LARGE_RESULT_CHARS
        assert _db_raw_result_contains(store, large["operation_id"], LARGE_RESULT_MARKER)

        small_ground = store.events(small["job_id"], 0, 1000)
        large_ground = store.events(large["job_id"], 0, 1000)
        assert [(row["event"], row["metadata"]) for row in small_ground] == [
            (row["event"], row["metadata"]) for row in large_ground
        ], "synthetic event names and metadata must match across the two jobs"
        gc.collect()

        small_page, small_peak = _read_with_isolated_tracing(
            receiver, small["job_id"], offset=0, limit=100)
        _assert_success_page_shape(small_page, job_id=small["job_id"],
                                   expected_events=small_ground, offset=0)
        assert small_peak <= PEAK_ALLOWANCE_BYTES, (
            f"small-result control read exceeded the Python baseline bound "
            f"({small_peak} bytes)"
        )
        del small_page
        gc.collect()

        large_page, large_peak = _read_with_isolated_tracing(
            receiver, large["job_id"], offset=0, limit=100)
        _assert_success_page_shape(large_page, job_id=large["job_id"],
                                   expected_events=large_ground, offset=0)
        assert not _serialized_blob_contains(large_page, LARGE_RESULT_MARKER), (
            "the huge stored result must not leak into the job.log page itself"
        )
        del large_page
        gc.collect()

        # The proposed memory bound compares these two separately created
        # jobs; their identities and small metadata also differ.
        delta = large_peak - small_peak
        assert delta <= PEAK_ALLOWANCE_BYTES, (
            f"the two job.log reads differed in Python traced peak by {delta} "
            f"bytes (small_peak={small_peak}, large_peak={large_peak}; "
            f"payload not printed)"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 3. Proposed contract: preserve a readable small prefix, do not serve an
#    oversized event, refuse at its continuation offset, and never change
#    durable audit bytes. A direct refusal of the larger request is also safe.
# ---------------------------------------------------------------------------

def test_t35jlb_single_oversized_event_metadata_fail_closed_audit_invariant_red() -> None:
    """A nonempty prefix may be returned before an oversized row is refused."""
    store = OperationStore(":memory:")
    try:
        receiver = _UnscopedReadReceiver(store)
        record = _start_job(store, tag="evt-oversized")
        job_id = record["job_id"]
        _add_events(store, job_id, [("T35JlbSmallAuditEvent", {"marker": "t35jlb-small-audit"})])
        huge_padding = OVERSIZED_METADATA_MARKER + "M" * (
            OVERSIZED_METADATA_CHARS - len(OVERSIZED_METADATA_MARKER))
        _add_events(store, job_id, [
            ("T35JlbOversizedAuditEvent", {"marker": huge_padding[:64], "bulk": huge_padding}),
        ])
        del huge_padding

        lengths = _db_event_metadata_lengths(store, job_id)
        assert len(lengths) == 2
        assert lengths[0] < 4096
        assert lengths[1] >= OVERSIZED_METADATA_CHARS
        small_ground = store.events(job_id, 0, 1)
        gc.collect()

        audit_before = _audit_fingerprint(store, job_id)

        # Positive control: the page excluding the oversized row is served.
        small_page = _job_log(receiver, job_id, offset=0, limit=1)
        _assert_success_page_shape(small_page, job_id=job_id,
                                   expected_events=small_ground, offset=0)

        # Exercise the page that contains the oversized row.
        try:
            response: dict[str, Any] | None = _job_log(receiver, job_id, offset=0, limit=100)
            raised: tuple[str, str] | None = None
        except Exception as exc:  # normalization for observation, not swallowing
            response, raised = None, (type(exc).__name__, str(exc)[:4096])

        # (a) audit bytes are untouched regardless of the read outcome
        audit_after = _audit_fingerprint(store, job_id)
        assert audit_after == audit_before, (
            "a job.log read deleted or rewrote durable job_events audit bytes: "
            f"before={audit_before['count']} rows sha256={audit_before['sha256']}, "
            f"after={audit_after['count']} rows sha256={audit_after['sha256']}"
        )
        assert _db_event_metadata_lengths(store, job_id) == lengths, (
            "event metadata lengths changed across the read"
        )

        # (b) A short success must contain exactly the preceding small row,
        # stay within budget, and point at the oversized event. It cannot
        # serve that event or silently advance past it.
        if response is not None and response.get("success") is True:
            _assert_success_page_shape(response, job_id=job_id,
                                       expected_events=small_ground, offset=0)
            assert _response_bytes(response) <= PROPOSED_MAX_PAGE_BYTES
            try:
                response = _job_log(receiver, job_id, offset=1, limit=100)
                raised = None
            except Exception as exc:
                response, raised = None, (type(exc).__name__, str(exc)[:4096])
            assert _audit_fingerprint(store, job_id) == audit_before

        if response is not None and response.get("success") is True:
            pytest.fail("the oversized event itself must not be returned in a success page")

        # (c) identifiability of the fail-closed outcome, envelope or raise.
        if raised is not None:
            exc_type, exc_text = raised
            assert "EXECUTION_STATE_UNKNOWN" in exc_text or "EXECUTION_STATE_UNKNOWN" in exc_type, (
                f"the oversized-metadata read failed with {exc_type} instead of an "
                f"identifiable EXECUTION_STATE_UNKNOWN fail-closed: {exc_text[:512]!r}"
            )
        else:
            assert response is not None, (
                "the oversized-metadata read neither returned an envelope nor raised"
            )
            error = _assert_refusal_shape(response, code="EXECUTION_STATE_UNKNOWN",
                                          label="oversized event metadata page")
            error_text = json.dumps(error, ensure_ascii=False, default=str)
            assert len(error_text) < 64 * 1024, (
                f"the refusal envelope itself is {len(error_text)} bytes; it must not "
                "re-embed the oversized audit payload"
            )
            assert OVERSIZED_METADATA_MARKER not in error_text, (
                "the oversized payload marker leaked into the refusal"
            )

        # Audit remains fully readable through the public path afterwards.
        reread = _job_log(receiver, job_id, offset=0, limit=1)
        _assert_success_page_shape(reread, job_id=job_id,
                                   expected_events=small_ground, offset=0)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 4. GREEN control for the page-byte story: three events, each metadata
#    below the ~4 MiB single-row band, total above the PROPOSED 8 MiB. The
#    current offset/limit paging must already be lossless, duplication-free
#    and offset-contiguous, and each limit=1 page stays under the PROPOSED
#    4 MiB single-page cap. The 4/8 MiB caps are proposals pending
#    adjudication (module docstring), not an external spec.
# ---------------------------------------------------------------------------

def _page_events() -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"T35JlbPageEvent{index}", {
            "marker": f"{PAGE_EVENT_MARKER_PREFIX}{index}",
            "bulk": f"P{index}" * (PER_EVENT_METADATA_CHARS // 2),
        })
        for index in range(PAGE_EVENT_COUNT)
    ]


def test_t35jlb_three_sub_page_events_offset_contiguous_paging_green() -> None:
    """Paging a >8 MiB (PROPOSED) tiny-row ledger without loss or duplication.

    Every page is served under the PROPOSED 4 MiB per-page cap at limit=1;
    concatenated pages equal the full durable ledger exactly, in id order.
    This is expected GREEN against the CURRENT offset/limit semantics and
    pins it so any future byte-budget admission cannot drop or duplicate
    events while shifting offsets.
    """
    store = OperationStore(":memory:")
    try:
        receiver = _UnscopedReadReceiver(store)
        record = _start_job(store, tag="page-paging")
        job_id = record["job_id"]
        events = _page_events()
        _add_events(store, job_id, events)
        _succeed_with_result(store, job_id, {"success": True, "data": {"note": "t35jlb-page-ledger"}})

        lengths = _db_event_metadata_lengths(store, job_id)
        assert len(lengths) == PAGE_EVENT_COUNT
        assert sum(lengths) > PROPOSED_TOTAL_EVENT_BYTES, (
            "fixture must exceed the PROPOSED total event budget"
        )
        for length in lengths:
            assert length < PROPOSED_MAX_PAGE_BYTES, (
                "each stored row must stay under the ~4 MiB single-row band"
            )
        ground = store.events(job_id, 0, 1000)
        assert len(ground) == PAGE_EVENT_COUNT

        collected: list[dict[str, Any]] = []
        offsets = [0, 1, 2, 3]
        for offset in offsets:
            page = _job_log(receiver, job_id, offset=offset, limit=1)
            expected = ground[offset:offset + 1]
            _assert_success_page_shape(page, job_id=job_id,
                                       expected_events=expected, offset=offset)
            assert _response_bytes(page) <= PROPOSED_MAX_PAGE_BYTES, (
                f"limit=1 page at offset {offset} exceeded the PROPOSED "
                "single-page cap; the paging control's premise is broken"
            )
            collected.extend(page["data"]["events"])
        collected_ids = [event["id"] for event in collected]
        assert collected_ids == [event["id"] for event in ground], (
            "offset-contiguous paging lost, reordered, or duplicated events"
        )
        assert len(set(collected_ids)) == PAGE_EVENT_COUNT, "duplicate event ids across pages"
        markers = [event["metadata"]["marker"] for event in collected]
        assert markers == [f"{PAGE_EVENT_MARKER_PREFIX}{index}" for index in range(PAGE_EVENT_COUNT)]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 5. PROPOSED-contract RED: the same ledger must not be servable as ONE
#    response above the PROPOSED 4 MiB page cap. The lossless limit=1
#    paging proof runs FIRST; the FIRST proposed-contract assertion reached
#    is the cap on the single limit=3 response (today one call materializes
#    the whole ~9 MiB ledger with no per-page byte admission).
# ---------------------------------------------------------------------------

def test_t35jlb_single_page_over_proposed_budget_red() -> None:
    """One job.log call over the PROPOSED single-page byte budget.

    Expected RED today: ``limit=3`` returns all three ~3 MiB events in one
    response. Expected GREEN after adjudication+implementation: the call is
    bounded (refused or truncated with explicit continuation semantics) while
    the proven limit=1 paging keeps every event reachable exactly once.
    """
    store = OperationStore(":memory:")
    try:
        receiver = _UnscopedReadReceiver(store)
        record = _start_job(store, tag="page-over-budget")
        job_id = record["job_id"]
        events = _page_events()
        _add_events(store, job_id, events)
        _succeed_with_result(store, job_id, {"success": True, "data": {"note": "t35jlb-over-budget"}})

        ground = store.events(job_id, 0, 1000)
        assert len(ground) == PAGE_EVENT_COUNT
        assert sum(_db_event_metadata_lengths(store, job_id)) > PROPOSED_TOTAL_EVENT_BYTES

        # Proof of reachability first (current semantics, GREEN): one page
        # per event, contiguous offsets.
        for offset in range(PAGE_EVENT_COUNT):
            page = _job_log(receiver, job_id, offset=offset, limit=1)
            _assert_success_page_shape(page, job_id=job_id,
                                       expected_events=ground[offset:offset + 1], offset=offset)

        # THE proposed page-cap (expected RED today). Bytes only; the payload
        # is never printed.
        single = _job_log(receiver, job_id, offset=0, limit=PAGE_EVENT_COUNT)
        single_bytes = _response_bytes(single)
        assert single_bytes <= PROPOSED_MAX_PAGE_BYTES, (
            f"FIRST proposed assertion reached: one job.log response carried "
            f"{single_bytes} bytes, above the PROPOSED {PROPOSED_MAX_PAGE_BYTES}-byte "
            "single-page budget (adjudication pending); the current branch applies "
            "no per-response byte admission on top of store.events (payload not "
            "printed)"
        )
        if single.get("success") is False:
            _assert_refusal_shape(single, code="EXECUTION_STATE_UNKNOWN",
                                  label="over-budget event page")
        else:
            # A shorter successful page must advance and remain fully
            # traversable at the same requested limit; an empty success is
            # not a valid way to satisfy the size check.
            collected_ids: list[int] = []
            offset = 0
            page = single
            while offset < PAGE_EVENT_COUNT:
                assert page.get("success") is True
                data = page["data"]
                rows = data["events"]
                assert data["job_id"] == job_id and rows
                assert rows == ground[offset:offset + len(rows)]
                assert data["next_offset"] == offset + len(rows)
                assert _response_bytes(page) <= PROPOSED_MAX_PAGE_BYTES
                collected_ids.extend(row["id"] for row in rows)
                offset = data["next_offset"]
                if offset < PAGE_EVENT_COUNT:
                    page = _job_log(receiver, job_id, offset=offset, limit=PAGE_EVENT_COUNT)
            assert collected_ids == [row["id"] for row in ground]
    finally:
        store.close()
