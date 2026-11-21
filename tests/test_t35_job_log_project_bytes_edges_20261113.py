"""T35 — job.log scoped project binding + adversarial byte/escape edges.

The author-final bytes were frozen and six tests ran on the pre-bounded
candidate. The parent subsequently adapted method spies and the short-page
contract for a proposed new bounded implementation; these revised bytes have
not yet run. See the pinned run receipt rather than inferring a result here.

What is real (no blanket mocking):
- Two REAL routes are exercised, both unbound (no ControlDaemon instance):
  1. ``ControlDaemon._dispatch_admitted`` with the LEGACY operation name
     ``job_log``: the CONTROL_READS branch -> ``_SCOPED_LEGACY_JOB_READS`` ->
     module-level ``_authorize_cached_job_read`` -> REAL ``_control_read``
     with a non-None ``scoped_project_id``.
  2. REAL ``ControlDaemon._control_read`` called DIRECTLY with the dotted
     canonical name ``"job.log"`` and an explicit ``scoped_project_id``, so
     the scoped ``job_snapshot`` gate -> ``OperationStore.project_job_read_snapshot``
     -> pure ``consistent_project_id`` chain runs unchanged.
- All durable state goes through REAL ``OperationStore`` paths — ``begin``,
  ``add_event``, ``update_job`` (merge semantics produce the conflicting-binding
  shape through the public API only), and snapshot/page methods — on a fresh
  ``OperationStore(":memory:")`` per test. The single deliberately malformed
  metadata row is shaped by one direct ``UPDATE jobs SET metadata=...`` and is
  clearly labelled fixture-only corruption shaping.
- Pass-through spies installed AFTER fixture setup track the proposed bounded
  snapshot/page methods and unwanted old ``store.job``/``store.events``
  fallback. They check method ordering, not arbitrary SQL reads or principal
  authenticity. Separate database-level instrumentation is needed for that.
- The escape-expansion measurement in the C1 test re-serializes the REAL
  read response with the exact same call the control HTTP listener uses
  (``json.dumps(result, allow_nan=False)``), so the measured growth is the
  real transport encoder's growth, not a hand-picked serializer.

AUTHENTICITY OF THE OUTER NAME IS NOT ESTABLISHED HERE (separate annotation):
- The outer ``execution.project_id`` is only a caller-selected selector, and
  the project authority in the ``_dispatch_admitted`` tests is a
  ``_RecordingFakeAuthority`` that GRANTS whatever it is asked and merely
  records the call. It is NOT a real principal, NOT authentication, and NOT
  the real ``ProjectAuthority`` (policy/host-grant/workspace semantics are
  characterized in ``tests/test_t35_gateway_real_authority_20261112.py``).
  What these tests pin is: GIVEN an outer selector that passed the (fake)
  grant, the durable job/operation binding gate alone must refuse A/B
  mismatches and incomplete/conflicting/malformed durable metadata before
  any job or event content is read.
- No gateway route, no MCP wire, no daemon socket, no Worker, no COMSOL, no
  SSH, no real database, no threads/subprocesses, no deletes. The dotted
  catalog action ``job.log`` via ``_dispatch_catalog_job_control`` is NOT
  exercised here (only the legacy CONTROL_READS route and the direct
  ``_control_read`` scoped branch are). The real ``_log_exception`` tail
  (which would write ``control-errors.log``) is replaced by a failure-only
  no-I/O sentinel and must never be reached.

PROPOSED BUDGETS — operational test assumptions, NOT an established spec:
- ``PROPOSED_MAX_PAGE_BYTES`` (4 MiB) mirrors
  ``tests/test_t35_job_log_payload_budget_20261113.py``. It is a PROPOSAL
  shared with the sibling payload-budget file. D1 now accepts a bounded short
  page only if continuation is lossless; a refusal must be explicit and not
  echo the synthetic payload. The preserved author bytes instead pinned the
  pre-change unbounded response and did not test successful short pages.

Intended coverage on the proposed candidate (these revised bytes are unrun):
- A1: same-project scoped read succeeds; the fake authority marker precedes
  the bounded binding and page methods, without the old job/events fallback.
- A2: envelope B vs A-bound job -> NODE_NOT_FOUND without page/fallback calls
  or sentinel echo; missing/empty outer project -> PROJECT_IDENTITY_REQUIRED
  with zero authority and zero read markers.
- B1: unbound / conflicting / malformed durable bindings all refuse with
  NODE_NOT_FOUND without page/fallback calls; non-string job_id ->
  NODE_NOT_FOUND without touching the store; empty-string job_id currently
  raises ValueError from the store primitive (asymmetry pinned as observed
  behaviour, documented, not endorsed); a later bound read still succeeds,
  proving no transaction was left dangling.
- C1: non-ASCII/quote/control payloads round-trip byte-exactly through the
  real read, and the real transport re-serialization strictly inflates
  versus an ``ensure_ascii=False`` rendering (escapes; astral surrogate
  pairs), with zero raw NUL bytes on the wire.
- D1: near-page-bound single-row and multi-row ledgers: a bounded short page
  or explicit refusal; a limit=1 walk and successful limit=3 continuation
  remain lossless/dup-free/contiguous, with unchanged audit bytes.
- D2: scoped input validation runs AFTER the binding gate and before any
  bounded page method, with INVALID_REQUEST and unchanged audit bytes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

MIB = 1024 * 1024

PROJECT_A = "t35jlp-proj-A-20261113"
PROJECT_B = "t35jlp-proj-B-20261113"

# PROPOSED only (adjudication pending; see module docstring). Mirrors the
# sibling payload-budget file; NOT claimed to be implemented anywhere.
PROPOSED_MAX_PAGE_BYTES = 4 * MIB

# Near-boundary fixtures for D1: one ~3.5 MB row (under the PROPOSED per-page
# cap) and a three-row ledger totalling ~4.26 MB (over it). Bytes only; the
# payload is never printed.
NEAR_UNDER_SINGLE_ROW_CHARS = 3_500_000
NEAR_MULTI_ROW_CHARS = 1_420_000

JOB_LOG_DATA_KEYS = {"job_id", "events", "next_offset"}
REFUSAL_ENVELOPE_KEYS = {"success", "data", "error"}
ERROR_KEYS_MINIMAL = {"code", "message", "safe_retry"}


class _RecordingFakeAuthority:
    """FAKE recorder that grants everything it is asked (see module docstring).

    It proves call ordering only. The REAL ProjectAuthority decision logic
    (project policy vs live host grant, workspace binding, revocation) is
    exercised exclusively in tests/test_t35_gateway_real_authority_20261112.py.
    """

    def __init__(self, timeline: list[tuple[str, ...]]) -> None:
        self._timeline = timeline

    def authorize_operation(self, project_id: str, permission: str) -> None:
        self._timeline.append(("authority", "authorize_operation", project_id, permission))


class _ScopedDispatchReceiver:
    """Inert receiver for the REAL unbound ``_dispatch_admitted`` legacy route.

    Only ``.store`` / ``.project_authority`` plus ``_timeouts`` /
    ``_control_read`` / ``_error`` / ``_exception`` are touched by the
    exercised job_log CONTROL_READS branch; ``_log_exception`` is a
    failure-only no-I/O sentinel so a generic-exception defect fails loudly
    instead of writing ``control-errors.log``.
    """

    def __init__(self, store: OperationStore, authority: _RecordingFakeAuthority) -> None:
        self.store = store
        self.project_authority = authority

    @staticmethod
    def _timeouts(execution: dict[str, Any]) -> dict[str, Any]:
        return ControlDaemon._timeouts(execution)

    def _control_read(self, operation: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return ControlDaemon._control_read(self, operation, arguments, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
        return ControlDaemon._error(code, message, **details)

    @staticmethod
    def _exception(exc: BaseException) -> dict[str, Any]:
        return ControlDaemon._exception(exc)

    @staticmethod
    def _log_exception() -> None:
        raise AssertionError(
            "T35JLP inert receiver reached the generic _dispatch_admitted exception path"
        )


class _BranchReceiver:
    """Inert receiver for DIRECT unbound ``_control_read`` calls (no authority).

    The scoped job_log branch touches only ``.store`` plus ``_error`` /
    ``_exception``; the project grant decision is outside this call by design
    (the caller-side ``_authorize_cached_job_read`` is exercised in A1/A2).
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
        raise AssertionError("T35JLP branch receiver reached the _log_exception sentinel")


def _install_read_spies(store: OperationStore, timeline: list[tuple[str, ...]]) -> None:
    """Pass-through recorders for proposed snapshot and page entry points.

    Old job/events calls are recorded as unwanted fallback. These method
    spies alone cannot establish the absence of arbitrary SQL reads.
    """
    real_job = store.job
    real_events = store.events
    real_snapshot = store.job_log_read_snapshot
    real_page = store.job_log_page_bounded

    def spy_job(job_id: str) -> Any:
        timeline.append(("job_row", job_id))
        return real_job(job_id)

    def spy_events(job_id: str, offset: int = 0, limit: int = 100) -> Any:
        timeline.append(("events", job_id))
        return real_events(job_id, offset=offset, limit=limit)

    @contextmanager
    def spy_snapshot(job_id: str, project_id: str | None, *, max_binding_bytes: int):
        timeline.append(("binding_start", job_id))
        with real_snapshot(job_id, project_id, max_binding_bytes=max_binding_bytes) as allowed:
            timeline.append(("binding_allowed" if allowed else "binding_rejected", job_id))
            yield allowed

    def spy_page(job_id: str, offset: int, limit: int, *, max_response_bytes: int) -> Any:
        timeline.append(("bounded_page", job_id))
        return real_page(job_id, offset, limit, max_response_bytes=max_response_bytes)

    store.job = spy_job  # type: ignore[method-assign]
    store.events = spy_events  # type: ignore[method-assign]
    store.job_log_read_snapshot = spy_snapshot  # type: ignore[method-assign]
    store.job_log_page_bounded = spy_page  # type: ignore[method-assign]


def _start_job(store: OperationStore, *, tag: str, project_id: str | None) -> dict[str, Any]:
    """One synthetic job through the real ``begin`` path.

    With ``project_id`` set, the SAME metadata (``execution.project_id``)
    lands on both the operations and the jobs row, so ``consistent_project_id``
    sees one unambiguous identical claim on each side. With ``project_id``
    None, neither side names any project at all (the unbound shape).
    """
    metadata: dict[str, Any] = {"t35_synthetic": True}
    if project_id is not None:
        metadata["execution"] = {"project_id": project_id}
    record, reused = store.begin(
        request_id=f"t35jlp-req-{tag}",
        idempotency_key=f"t35jlp-key-{tag}",
        request_hash=f"t35jlp-hash-{tag}",
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _refusal_blob(response: dict[str, Any]) -> str:
    return json.dumps(response, sort_keys=True, default=str)


def _assert_refusal_shape(refusal: dict[str, Any], *, code: str, message: str,
                          label: str, sentinels: tuple[str, ...] = ()) -> None:
    """Real ``_error`` / ``_exception`` envelope: {"success","data","error"}."""
    assert refusal.get("success") is False, f"{label}: expected refusal, got {refusal!r}"
    assert set(refusal) == REFUSAL_ENVELOPE_KEYS, (
        f"{label}: unexpected refusal envelope keys {sorted(refusal)}"
    )
    assert refusal["data"] == {}, f"{label}: expected empty data, got {refusal['data']!r}"
    error = refusal["error"]
    assert ERROR_KEYS_MINIMAL <= set(error), f"{label}: unexpected error keys {sorted(error)}"
    assert error.get("code") == code, f"{label}: expected code {code}, got {error!r}"
    assert error.get("message") == message, f"{label}: expected exact message, got {error!r}"
    assert error.get("safe_retry") is False, f"{label}: expected safe_retry False: {error!r}"
    if sentinels:
        blob = _refusal_blob(refusal)
        for sentinel in sentinels:
            assert sentinel not in blob, f"{label}: sentinel {sentinel!r} echoed in refusal"


def _audit_fingerprint(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Exact raw audit-byte fingerprint of one job's durable event ledger.

    Reads raw TEXT bytes of every ``job_events`` row (no JSON decode) and
    digests id/event/metadata with unit separators. Used only to prove a
    scoped read deleted, reordered, or rewrote nothing.
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
        return {"count": len(rows), "ids": tuple(int(row["id"]) for row in rows),
                "sha256": digest.hexdigest()}
    finally:
        del rows


def _raw_row_bytes(store: OperationStore, event_id: int) -> bytes:
    return store.db.execute(
        "SELECT CAST(metadata AS BLOB) FROM job_events WHERE id=?", (event_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# A1: GREEN — legacy ``job_log`` through the REAL unbound _dispatch_admitted:
# fake authority grants the outer A selector, the durable binding matches,
# and the scoped read succeeds with exact public shape and strict
# authority-precedes-materialization ordering.
# ---------------------------------------------------------------------------

def test_t35jlp_a1_same_project_scoped_job_log_allowed_authority_precedes_reads() -> None:
    event_marker = "T35JLP-EVT-SAMEPROJ-41a7"
    result_marker = "T35JLP-RES-SAMEPROJ-8b13"
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        authority = _RecordingFakeAuthority(timeline)
        receiver = _ScopedDispatchReceiver(store, authority)
        record = _start_job(store, tag="a1-bound", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlpSameProjectEvent", {"marker": event_marker})
        store.update_job(job_id, "SUCCEEDED", {"t35_synthetic": True},
                         result={"success": True, "data": {"marker": result_marker}})
        _install_read_spies(store, timeline)

        response = ControlDaemon._dispatch_admitted(receiver, {
            "operation": "job_log",
            "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
            "execution": {"project_id": PROJECT_A},
        })  # type: ignore[arg-type]

        assert response.get("success") is True, f"same-project read should succeed: {response!r}"
        assert set(response) == {"success", "data"}, f"unexpected envelope keys {sorted(response)}"
        data = response["data"]
        assert set(data) == JOB_LOG_DATA_KEYS, f"unexpected job_log data keys {sorted(data)}"
        assert data["job_id"] == job_id
        assert data["next_offset"] == 1
        events = data["events"]
        assert [event["metadata"]["marker"] for event in events] == [event_marker]
        # job.log serves the event ledger only, not the stored result.
        assert result_marker not in json.dumps(response, default=str), (
            "stored result leaked into the job.log event page"
        )

        authority_at = [i for i, entry in enumerate(timeline) if entry[0] == "authority"]
        binding_at = [i for i, entry in enumerate(timeline) if entry[0] == "binding_allowed"]
        page_at = [i for i, entry in enumerate(timeline) if entry[0] == "bounded_page"]
        assert authority_at == [0], f"exactly one fake-authority marker expected: {timeline!r}"
        assert timeline[0] == ("authority", "authorize_operation", PROJECT_A, "inspect")
        assert binding_at and page_at and authority_at[0] < binding_at[0] < page_at[0], (
            f"authority/materialization order violated: {timeline!r}"
        )
        assert not any(entry[0] in {"job_row", "events"} for entry in timeline)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# A2: GREEN — the SAME route refuses outer/inner mismatches and missing outer
# identity BEFORE any result or event content is materialized, with no echo.
# ---------------------------------------------------------------------------

def test_t35jlp_a2_foreign_or_missing_outer_project_refused_before_reads() -> None:
    event_marker = "T35JLP-EVT-FOREIGN-9d2c"
    result_marker = "T35JLP-RES-FOREIGN-6e54"
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        authority = _RecordingFakeAuthority(timeline)
        receiver = _ScopedDispatchReceiver(store, authority)
        record = _start_job(store, tag="a2-foreign", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlpForeignEvent", {"marker": event_marker})
        store.update_job(job_id, "SUCCEEDED", {"t35_synthetic": True},
                         result={"success": True, "data": {"marker": result_marker}})
        _install_read_spies(store, timeline)

        # (i) Envelope B (fake authority happily grants it): the durable
        # binding gate must hide the A-bound job as plain not-found.
        foreign = ControlDaemon._dispatch_admitted(receiver, {
            "operation": "job_log",
            "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
            "execution": {"project_id": PROJECT_B},
        })  # type: ignore[arg-type]
        _assert_refusal_shape(
            foreign, code="NODE_NOT_FOUND", message="job not found",
            label="B envelope vs A-bound job.log", sentinels=(event_marker, result_marker),
        )
        assert ("authority", "authorize_operation", PROJECT_B, "inspect") in timeline, (
            f"outer B grant should have run before the binding gate: {timeline!r}"
        )
        assert not any(entry[0] in {"job_row", "events", "bounded_page"} for entry in timeline), (
            f"result/event materialization preceded the refusal: {timeline!r}"
        )

        # (ii) Missing outer project identity: refused inside
        # _authorize_cached_job_read BEFORE the authority is even consulted
        # and with zero store reads.
        for label, execution in (("missing key", {}), ("empty string", {"project_id": ""})):
            before = len(timeline)
            missing = ControlDaemon._dispatch_admitted(receiver, {
                "operation": "job_log",
                "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
                "execution": dict(execution),
            })  # type: ignore[arg-type]
            _assert_refusal_shape(
                missing, code="PROJECT_IDENTITY_REQUIRED",
                message="job reads require outer execution.project_id",
                label=f"job.log {label} outer project",
                sentinels=(event_marker, result_marker),
            )
            assert timeline[before:] == [], (
                f"{label}: expected no authority/read markers, got {timeline[before:]!r}"
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# B1: GREEN — DIRECT unbound _control_read ("job.log" dotted name, explicit
# scoped_project_id): unbound, conflicting, and malformed durable metadata all
# collapse to plain NODE_NOT_FOUND without any read spy firing; job_id shape
# edges are characterized (None/non-string -> not-found; "" currently raises
# ValueError from the store primitive — pinned as observed asymmetry, not
# endorsed); afterwards a healthy bound read still succeeds (no dangling
# read transaction).
# ---------------------------------------------------------------------------

def test_t35jlp_b1_scoped_branch_unbound_conflict_malformed_shapes() -> None:
    event_marker = "T35JLP-EVT-BINDINGS-27f8"
    result_marker = "T35JLP-RES-BINDINGS-5c90"
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        receiver = _BranchReceiver(store)

        # (i) durable rows name NO project at all -> binding gate refuses even
        # for the matching selector; the job must never become "claimable".
        unbound = _start_job(store, tag="b1-unbound", project_id=None)
        store.add_event(unbound["job_id"], "T35JlpUnboundEvent", {"marker": event_marker})
        store.update_job(unbound["job_id"], "SUCCEEDED", {"t35_synthetic": True},
                         result={"success": True, "data": {"marker": result_marker}})

        # (ii) conflicting claims via the REAL merge path only: begin binds
        # both rows to A, then update_job's top-level "execution" merge
        # replaces the JOB-side claim with B while the OPERATION side stays A.
        conflict = _start_job(store, tag="b1-conflict", project_id=PROJECT_A)
        store.add_event(conflict["job_id"], "T35JlpConflictEvent", {"marker": event_marker})
        store.update_job(conflict["job_id"], "QUEUED", {"execution": {"project_id": PROJECT_B}})
        job_meta = json.loads(store.db.execute(
            "SELECT metadata FROM jobs WHERE job_id=?", (conflict["job_id"],)).fetchone()[0])
        op_meta = json.loads(store.db.execute(
            "SELECT metadata FROM operations WHERE operation_id=?",
            (conflict["operation_id"],)).fetchone()[0])
        assert job_meta["execution"]["project_id"] == PROJECT_B
        assert op_meta["execution"]["project_id"] == PROJECT_A

        # (iii) malformed durable job metadata. Fixture-only corruption
        # shaping (public API cannot write invalid JSON); the documented
        # store contract classifies the row as unbound instead of raising.
        malformed = _start_job(store, tag="b1-malformed", project_id=PROJECT_A)
        store.add_event(malformed["job_id"], "T35JlpMalformedEvent", {"marker": event_marker})
        with store.lock:
            store.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
                             ("{not-json", malformed["job_id"]))

        # (iv) healthy control job for the post-state liveness check.
        healthy = _start_job(store, tag="b1-healthy", project_id=PROJECT_A)
        store.add_event(healthy["job_id"], "T35JlpHealthyEvent", {"marker": event_marker})

        _install_read_spies(store, timeline)

        denials = [
            ("unbound claims", unbound["job_id"], PROJECT_A),
            ("conflict claims, A selector", conflict["job_id"], PROJECT_A),
            ("conflict claims, B selector", conflict["job_id"], PROJECT_B),
            ("malformed job metadata", malformed["job_id"], PROJECT_A),
            ("missing job row", "t35jlp-no-such-job", PROJECT_A),
        ]
        for label, job_id_probe, selector in denials:
            refusal = ControlDaemon._control_read(
                receiver, "job.log",
                {"job_id": job_id_probe, "offset": 0, "limit": 100},
                scoped_project_id=selector,
            )  # type: ignore[arg-type]
            _assert_refusal_shape(
                refusal, code="NODE_NOT_FOUND", message="job not found",
                label=f"scoped job.log {label}", sentinels=(event_marker, result_marker),
            )
        assert not any(entry[0] in {"job_row", "events", "bounded_page"} for entry in timeline), (
            f"a binding-gate denial materialized job/events content: {timeline!r}"
        )

        # job_id shape edges: a non-string job_id never reaches the store.
        before_non_string = list(timeline)
        for probe in (None, 123, {"job_id": "nested"}):
            refusal = ControlDaemon._control_read(
                receiver, "job.log", {"job_id": probe, "offset": 0, "limit": 100},
                scoped_project_id=PROJECT_A,
            )  # type: ignore[arg-type]
            _assert_refusal_shape(
                refusal, code="NODE_NOT_FOUND", message="job not found",
                label=f"scoped job.log non-string job_id {type(probe).__name__}",
                sentinels=(event_marker, result_marker),
            )
        assert timeline == before_non_string, "non-string job_id probes must not enter the traced store methods"

        # The empty-string job_id currently RAISES from the store primitive
        # (the isinstance guard lets "" through). Pinned as observed
        # behaviour; through the full dispatcher such a ValueError would land
        # on the generic EXECUTION_STATE_UNKNOWN tail plus _log_exception —
        # NOT exercised here. The primitive validates before opening any
        # transaction, so the store must stay usable.
        with pytest.raises(ValueError, match="non-empty job_id"):
            ControlDaemon._control_read(
                receiver, "job.log", {"job_id": "", "offset": 0, "limit": 100},
                scoped_project_id=PROJECT_A,
            )  # type: ignore[arg-type]
        assert store.db.in_transaction is False, "empty job_id must not leave a transaction open"

        # Post-state liveness: the healthy same-project read still succeeds,
        # proving the denials and the ValueError left the read snapshot clean.
        good = ControlDaemon._control_read(
            receiver, "job.log",
            {"job_id": healthy["job_id"], "offset": 0, "limit": 100},
            scoped_project_id=PROJECT_A,
        )  # type: ignore[arg-type]
        assert good.get("success") is True, f"healthy scoped read should succeed: {good!r}"
        assert [event["metadata"]["marker"] for event in good["data"]["events"]] == [event_marker]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# C1: GREEN — non-ASCII / quote / control-character payloads round-trip
# byte-exactly through the real scoped read, while the transport encoder
# (the exact json.dumps(result, allow_nan=False) the control listener uses)
# inflates escaping: \uXXXX for BMP non-ASCII, surrogate pairs for astral,
# and no raw NUL ever reaches the wire.
# ---------------------------------------------------------------------------

def test_t35jlp_c1_escape_roundtrip_and_wire_encoding_expansion() -> None:
    ascii_marker = "T35JLP-EVT-ESCAPE-33b1"
    weird = (
        '中文 \U0001F600 quote"back\\slash '
        "line\nbreak\ttab\x00nul\x1funitsep\x7fdel 'apos' end"
    )
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="c1-escape", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlpEscapeEvent",
                        {"marker": ascii_marker, "weird": weird, "bulk": "x" * 4096})
        store.add_event(job_id, "T35JlpPlainEvent", {"marker": f"{ascii_marker}-plain"})
        _install_read_spies(store, timeline)

        page = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 0, "limit": 100},
            scoped_project_id=PROJECT_A,
        )  # type: ignore[arg-type]
        assert page.get("success") is True, f"escape read should succeed: {page!r}"
        events = page["data"]["events"]
        assert len(events) == 2 and page["data"]["next_offset"] == 2

        # (a) read-level fidelity: no truncation, no re-escaping at the
        # Python value level — the decoded string is the exact original.
        assert events[0]["metadata"]["weird"] == weird

        # (b) durable shape: the stored row already JSON-escapes non-ASCII
        # and controls, and contains no raw NUL or raw newline bytes.
        raw = _raw_row_bytes(store, events[0]["id"])
        assert b"\\u4e2d" in raw, "stored row must keep the JSON-escaped BMP form"
        assert b"\\u0000" in raw and b"\x00" not in raw, (
            "NUL must be escaped inside the durable row, never stored raw"
        )
        assert b"\n" not in raw, "literal newline byte must not sit inside the stored row"

        # (c) transport-level expansion: the listener's exact serializer
        # (json.dumps(result, allow_nan=False), ensure_ascii default True)
        # strictly inflates versus the content's own UTF-8 rendering.
        body = json.dumps(page, allow_nan=False).encode("utf-8")
        semantic = json.dumps(page, ensure_ascii=False, allow_nan=False).encode("utf-8")
        assert len(body) > len(semantic), (
            f"expected escape expansion under the listener encoder: "
            f"wire={len(body)} vs semantic={len(semantic)} bytes"
        )
        assert b"\\ud83d\\ude00" in body, (
            "astral character must serialize as a JSON surrogate-pair escape "
            "on the control wire, expanding one 4-byte UTF-8 code point to 12 ASCII bytes"
        )
        assert b"\\u4e2d" in body and b"\x00" not in body, (
            "wire form must keep escapes and carry zero raw NUL bytes"
        )
        # Structural integrity: the wire body stays one line (no raw control
        # characters smuggled by the payload) and decodes back losslessly.
        assert body.count(b"\n") == 0
        assert json.loads(body)["data"]["events"][0]["metadata"]["weird"] == weird
    finally:
        store.close()


# ---------------------------------------------------------------------------
# D1: proposed near-page-bound single-row and multi-row ledgers. A bounded
# success must advance without losing events; a refusal must be explicit and
# not echo payload. The raw audit bytes never move. The 4 MiB cap is PROPOSED.
# ---------------------------------------------------------------------------

def test_t35jlp_d1_near_page_boundary_single_and_multi_row() -> None:
    single_marker = "T35JLP-EVT-NEARSINGLE-7ab4"
    multi_prefix = "T35JLP-EVT-NEARMULTI"
    store = OperationStore(":memory:")
    try:
        receiver = _BranchReceiver(store)

        single = _start_job(store, tag="d1-single", project_id=PROJECT_A)
        store.add_event(single["job_id"], "T35JlpNearSingleEvent", {
            "marker": single_marker, "bulk": "P" * NEAR_UNDER_SINGLE_ROW_CHARS,
        })
        multi = _start_job(store, tag="d1-multi", project_id=PROJECT_A)
        for index in range(3):
            store.add_event(multi["job_id"], f"T35JlpNearMultiEvent{index}", {
                "marker": f"{multi_prefix}{index}", "bulk": f"Q{index}" * (NEAR_MULTI_ROW_CHARS // 2),
            })

        stored_single = _raw_row_bytes(store, store.db.execute(
            "SELECT id FROM job_events WHERE job_id=? ORDER BY id",
            (single["job_id"],)).fetchone()[0])
        stored_multi_total = sum(
            len(_raw_row_bytes(store, row[0])) for row in store.db.execute(
                "SELECT id FROM job_events WHERE job_id=? ORDER BY id",
                (multi["job_id"],)).fetchall())
        assert len(stored_single) < PROPOSED_MAX_PAGE_BYTES
        assert stored_multi_total > PROPOSED_MAX_PAGE_BYTES, (
            "multi fixture must cross the PROPOSED page budget"
        )
        del stored_single, stored_multi_total

        multi_audit_before = _audit_fingerprint(store, multi["job_id"])
        multi_ground = store.events(multi["job_id"], 0, 1000)
        assert len(multi_ground) == 3
        single_ground = store.events(single["job_id"], 0, 10)
        assert len(single_ground) == 1

        # One row just under the PROPOSED cap: today fully served.
        single_page = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": single["job_id"], "offset": 0, "limit": 1},
            scoped_project_id=PROJECT_A,
        )  # type: ignore[arg-type]
        assert single_page.get("success") is True, "near-bound single row is under the PROPOSED cap"
        assert single_page["data"]["next_offset"] == 1
        assert single_page["data"]["events"] == single_ground

        # The multi-row ledger in one call must be bounded or explicitly refused.
        whole = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": multi["job_id"], "offset": 0, "limit": 3},
            scoped_project_id=PROJECT_A,
        )  # type: ignore[arg-type]
        whole_bytes = len(json.dumps(whole, allow_nan=False).encode("utf-8"))
        assert whole_bytes <= PROPOSED_MAX_PAGE_BYTES
        if whole.get("success") is False:
            # Refusal must carry an explicit non-empty code and must never
            # echo the near-boundary payload.
            error = whole["error"]
            assert isinstance(error.get("code"), str) and error["code"], (
                f"a near-boundary refusal needs an explicit code: {error!r}"
            )
            assert isinstance(error.get("message"), str) and error["message"], (
                f"a near-boundary refusal needs an explicit message: {error!r}"
            )
            blob = _refusal_blob(whole)
            assert blob, "refusal blob computed"
            for sentinel in (multi_prefix, single_marker):
                assert sentinel not in blob, f"near-boundary refusal echoed {sentinel!r}"
        else:
            # A successful short page must remain bounded and traverse the
            # entire ledger at the original requested limit.
            collected_at_limit_three: list[dict[str, Any]] = []
            current, offset = whole, 0
            while offset < 3:
                assert current.get("success") is True
                assert len(json.dumps(current, allow_nan=False).encode("utf-8")) <= PROPOSED_MAX_PAGE_BYTES
                rows = current["data"]["events"]
                assert rows and rows == multi_ground[offset:offset + len(rows)]
                assert current["data"]["next_offset"] == offset + len(rows)
                collected_at_limit_three.extend(rows)
                offset = current["data"]["next_offset"]
                if offset < 3:
                    current = ControlDaemon._control_read(
                        receiver, "job.log", {"job_id": multi["job_id"], "offset": offset, "limit": 3},
                        scoped_project_id=PROJECT_A,
                    )  # type: ignore[arg-type]
            assert collected_at_limit_three == multi_ground

        # Invariants any budget must preserve: a limit=1 walk is contiguous,
        # lossless and duplication-free, each small page under the PROPOSED
        # cap, and the durable audit bytes are byte-identical throughout.
        collected: list[dict[str, Any]] = []
        offset = 0
        while offset < 3:
            step = ControlDaemon._control_read(
                receiver, "job.log", {"job_id": multi["job_id"], "offset": offset, "limit": 1},
                scoped_project_id=PROJECT_A,
            )  # type: ignore[arg-type]
            assert step.get("success") is True, f"limit=1 walk must succeed: {step!r}"
            rows = step["data"]["events"]
            assert rows and step["data"]["next_offset"] == offset + len(rows), (
                f"offset must strictly advance by the page size at {offset}: {step['data']!r}"
            )
            assert rows == multi_ground[offset:offset + len(rows)]
            assert len(json.dumps(step, allow_nan=False).encode("utf-8")) < PROPOSED_MAX_PAGE_BYTES
            collected.extend(rows)
            offset = step["data"]["next_offset"]
        collected_ids = [row["id"] for row in collected]
        assert collected_ids == [row["id"] for row in multi_ground], (
            "offset-contiguous paging lost, reordered, or duplicated events"
        )
        assert [row["metadata"]["marker"] for row in collected] == [
            f"{multi_prefix}{index}" for index in range(3)]
        assert _audit_fingerprint(store, multi["job_id"]) == multi_audit_before, (
            "near-boundary reads deleted or rewrote durable job_events audit bytes"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# D2: scoped input validation ordering: binding happens before offset/limit
# validation; rejected windows do not fetch a page and audit bytes stay put.
# ---------------------------------------------------------------------------

def test_t35jlp_d2_scoped_validation_runs_after_binding_gate() -> None:
    marker = "T35JLP-EVT-VALIDATION-e18d"
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="d2-validate", project_id=PROJECT_A)
        job_id = record["job_id"]
        for index in range(3):
            store.add_event(job_id, f"T35JlpValidationEvent{index}", {"index": index})
        audit_before = _audit_fingerprint(store, job_id)
        _install_read_spies(store, timeline)

        bad_windows = [
            {"offset": -1, "limit": 100},
            {"offset": 0, "limit": 0},
            {"offset": 0, "limit": 1001},
            {"offset": "0", "limit": 100},
            {"offset": 0, "limit": "100"},
            {"offset": True, "limit": 100},
        ]
        for window in bad_windows:
            refusal = ControlDaemon._control_read(
                receiver, "job.log", {"job_id": job_id, **window},
                scoped_project_id=PROJECT_A,
            )  # type: ignore[arg-type]
            _assert_refusal_shape(
                refusal, code="INVALID_REQUEST", message="invalid log offset/limit",
                label=f"scoped job.log window {window!r}", sentinels=(marker,),
            )
        binding_reads = [entry for entry in timeline if entry[0] == "binding_allowed"]
        event_reads = [entry for entry in timeline if entry[0] in {"events", "bounded_page"}]
        assert len(binding_reads) == len(bad_windows), (
            f"binding gate must accept before each window validation: {timeline!r}"
        )
        assert not any(entry[0] == "job_row" for entry in timeline)
        assert event_reads == [], (
            f"a refused offset/limit window still read the event ledger: {timeline!r}"
        )
        assert _audit_fingerprint(store, job_id) == audit_before, (
            "validation refusals mutated the durable audit ledger"
        )
    finally:
        store.close()
