"""T35 — bounded job.log: independent adversarial synthetic edges (2026-11-13).

Author-final bytes were frozen before the parent corrected SQL-trace and
fixture assumptions. These revised bytes have not yet been run; see the pinned
receipt for any subsequently observed result.

Independence: this file is self-contained. It does not import, exec, or reuse
helpers from the two frozen author tests
(``test_joblog_budget_author_667f55a3`` / ``test_joblog_project_edges_author_761d9282``)
and it does not load any rejected/sealed test. It mirrors only their public
conventions (unbound ``ControlDaemon`` helpers, inert receivers, ``_error``
envelope shape). It calls NO proposed-new store method directly: the durable
primitives touched are exactly the pre-existing public API the daemon itself
uses — ``begin``, ``add_event``, ``update_job``, ``job``, ``events`` — plus the
unbound ``ControlDaemon._control_read`` / ``_dispatch_admitted`` / ``_error`` /
``_exception`` / ``_timeouts``.

What is real (no blanket mocking):
- Two REAL routes, both unbound (no ControlDaemon instance):
  1. ``ControlDaemon._dispatch_admitted`` with legacy ``job_log``: the
     CONTROL_READS branch -> ``_SCOPED_LEGACY_JOB_READS`` -> module-level
     ``_authorize_cached_job_read`` -> REAL ``_control_read`` with a non-None
     ``scoped_project_id`` (the store's scoped, bounded binding snapshot).
  2. REAL ``ControlDaemon._control_read`` called DIRECTLY with the dotted
     ``"job.log"`` name and the default unscoped ``scoped_project_id=None``
     (the legacy private-caller branch, without principal authentication).
- Each test uses a fresh ``OperationStore(":memory:")``; the raw UTF-8
  escape-expansion case updates its own synthetic event with parameterized SQL.
- SQL ordering is observed with ``sqlite3.Connection.set_trace_callback`` on
  each test's fresh ``:memory:`` connection. SQLite expands bound parameters;
  the callback must never be attached to real data. Fixture writes occur
  before attachment, no payload is intentionally printed, and the callback is
  detached in ``finally``. Selected SQL is checked for a binding query, event
  probes and absence of a result-column read; this is not a universal query
  monitor, OS sandbox or principal-authentication proof.

Enumerated expectations pinned/claimed here:
- Cross-project non-leakage: an A-bound job read through an outer B envelope
  (fake authority grants B) collapses to the plain identical not-found refusal
  before ANY ``job_events`` SQL and before any result-column path, with no
  sentinel echo; a missing/empty outer project is refused with ZERO SQL on the
  connection.
- First-row oversized refusal (proposed): a single event whose durable
  metadata alone exceeds the 4 MiB proposed page budget must not be served as
  a success page; because one row cannot be short-paged, the only conforming
  outcomes are an explicit refusal with an empty data envelope (no payload
  echo); an empty successful page would silently skip evidence and is rejected.
- UTF-8 vs escaped: raw UTF-8 JSON strictly under 4 MiB but strictly over
  4 MiB once re-serialized with the transport encoder
  (``json.dumps(..., allow_nan=False)``, default ``ensure_ascii=True``) must
  not be silently served over budget: short-page or explicit no-echo refusal.
- A >4 MiB event name with small metadata must fail closed without fetching
  that row's body, while an offset past it can still return later events.
- Multi-row full traversal using only ``data.next_offset`` with one fixed
  limit is lossless/dup-free, and the raw durable ledger bytes are unchanged
  (ledger hash stable) across the whole walk.

HONESTY LIMITS — deliberately NOT verified here:
- The fake authority grants whatever it is asked and only records the call.
  It is NOT a real principal, NOT authentication, NOT the real
  ProjectAuthority; the unscoped direct ``_control_read`` route is the legacy
  private-caller branch only. No gateway route, no MCP wire, no daemon socket,
  no WAL/RSS claim, no Worker, no COMSOL, no SSH, no real database, no disk
  files, no threads/subprocesses, no deletes, no reuse of old resources or
  previously rejected fenced surfaces.
- All measured byte counts are Python-level serializations (transport-style
  ``json.dumps``) or SQL ``LENGTH(CAST(... AS BLOB))`` probes of this in-memory
  store. SQLite C allocations, WAL, RSS, and process memory are NOT bounded or
  claimed by these numbers.

PROPOSED BUDGETS — pending adjudication, NOT an assumed spec:
- ``PROPOSED_MAX_PAGE_BYTES`` (4 MiB per single job.log response) is a proposed
  operational budget, not a user-specified public API guarantee. Relevant
  assertions require an explicit refusal or a nonempty lossless short page.

Selected checks cover authorization/binding/content order, denial before
event/result SQL, scoped and unscoped reads, raw UTF-8 escape expansion,
oversized names/metadata, and actual byte-driven short-page continuation.

The tests attach their observer only to connections that they themselves
created, not to a previously configured or shared connection.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

MIB = 1024 * 1024

# PROPOSED operational budget (adjudication pending; see module docstring).
PROPOSED_MAX_PAGE_BYTES = 4 * MIB

PROJECT_A = "t35jla-proj-A-20261113"
PROJECT_B = "t35jla-proj-B-20261113"

JOB_LOG_DATA_KEYS = {"job_id", "events", "next_offset"}
EVENT_ROW_KEYS = {"id", "job_id", "event", "metadata", "created_at"}

_RESULT_TOKEN_RE = re.compile(r"\bresult\b", re.IGNORECASE)


class _GrantRecordingAuthority:
    """FAKE authority: grants everything asked, records call markers only.

    It proves call ordering only; the REAL ProjectAuthority decision logic is
    characterized elsewhere. Synthetic, not a real principal.
    """

    def __init__(self, markers: list[tuple[str, ...]]) -> None:
        self._markers = markers

    def authorize_operation(self, project_id: str, permission: str) -> None:
        self._markers.append(("authority", project_id, permission))


class _DispatchReceiver:
    """Inert receiver for the REAL unbound ``_dispatch_admitted`` legacy route.

    Only ``.store``/``.project_authority`` plus ``_timeouts``/``_control_read``/
    ``_error``/``_exception`` are touched by the exercised job_log branch;
    ``_log_exception`` is a failure-only no-I/O sentinel that must never fire.
    """

    def __init__(self, store: OperationStore, authority: _GrantRecordingAuthority) -> None:
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
            "T35JLA inert receiver reached the generic _dispatch_admitted exception path"
        )


class _BranchReceiver:
    """Inert receiver for DIRECT unbound ``_control_read`` calls (no authority)."""

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
        raise AssertionError("T35JLA branch receiver reached the _log_exception sentinel")


class _SqlTrace:
    """Observe SQL only on this test's own synthetic ``:memory:`` store.

    SQLite supplies expanded SQL, including bound values. Attach only after
    fixture writes; never use this observer with real data or print SQL values.
    An optional shared timeline lets a test check authorization before SQL.
    """

    def __init__(self, store: OperationStore, timeline: list[tuple[str, ...]] | None = None) -> None:
        self.db = store.db
        self.statements: list[str] = []
        self.timeline = timeline
        self.db.set_trace_callback(self._record)

    def _record(self, statement: str) -> None:
        self.statements.append(statement)
        if self.timeline is not None:
            self.timeline.append(("sql",))

    def reset(self) -> None:
        self.statements.clear()

    def detach(self) -> None:
        self.db.set_trace_callback(None)

    def selects(self) -> list[str]:
        return [s for s in self.statements if s.strip().upper().startswith("SELECT")]

    def events_reads(self) -> list[str]:
        return [s for s in self.statements if "job_events" in s]

    def first_index(self, needle: str) -> int:
        for index, statement in enumerate(self.statements):
            if needle in statement:
                return index
        return -1


def _start_job(store: OperationStore, *, tag: str, project_id: str | None) -> dict[str, Any]:
    """One synthetic job through the real ``begin`` path.

    With ``project_id`` the same metadata (``execution.project_id``) lands on
    both the operations and jobs rows, so the durable binding gate sees one
    unambiguous identical claim on each side.
    """
    metadata: dict[str, Any] = {"t35_synthetic": True}
    if project_id is not None:
        metadata["execution"] = {"project_id": project_id}
    record, reused = store.begin(
        request_id=f"t35jla-req-{tag}",
        idempotency_key=f"t35jla-key-{tag}",
        request_hash=f"t35jla-hash-{tag}",
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _assert_refusal(response: dict[str, Any], *, code: str, message: str,
                    label: str, sentinels: tuple[str, ...] = ()) -> None:
    """Real ``_error``/``_exception`` envelope: {"success","data","error"}."""
    assert response.get("success") is False, f"{label}: expected refusal, got {response!r}"
    assert set(response) == {"success", "data", "error"}, (
        f"{label}: unexpected refusal envelope keys {sorted(response)}"
    )
    assert response["data"] == {}, f"{label}: expected empty data, got {response['data']!r}"
    error = response["error"]
    assert {"code", "message", "safe_retry"} <= set(error), (
        f"{label}: unexpected error keys {sorted(error)}"
    )
    assert error.get("code") == code, f"{label}: expected code {code}, got {error!r}"
    assert error.get("message") == message, f"{label}: expected exact message, got {error!r}"
    assert error.get("safe_retry") is False, f"{label}: expected safe_retry False: {error!r}"
    if sentinels:
        blob = json.dumps(response, sort_keys=True, default=str)
        for sentinel in sentinels:
            assert sentinel not in blob, f"{label}: sentinel {sentinel!r} echoed in refusal"


def _assert_success_page(response: dict[str, Any], *, job_id: str,
                         expected_events: list[dict[str, Any]], offset: int) -> None:
    assert response.get("success") is True, f"job.log should succeed, got {response!r}"
    assert set(response) == {"success", "data"}, f"unexpected envelope keys {sorted(response)}"
    data = response["data"]
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


def _transport_bytes(payload: dict[str, Any]) -> int:
    """The control listener's own serialization: json.dumps(..., allow_nan=False)."""
    serialized = json.dumps(payload, allow_nan=False)
    try:
        return len(serialized.encode("utf-8"))
    finally:
        del serialized


def _ledger_fingerprint(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Raw durable job_events bytes (no JSON decode) digested with unit separators.

    Proves a read deleted, reordered, or rewrote nothing. Used outside any
    byte-budget measurement; the payload itself is never printed.
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


def _stored_blob_bytes(store: OperationStore, job_id: str) -> tuple[int, ...]:
    """SQL-side durable metadata byte lengths per row (lengths only, never text)."""
    return tuple(
        int(row[0]) for row in store.db.execute(
            "SELECT length(CAST(metadata AS BLOB)) FROM job_events WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# T1 GREEN — positive control through the REAL scoped route: fake-authority
# grant -> durable binding probe SQL -> result-column path -> bounded events
# SQL, in that exact order, observed at the connection level.
# ---------------------------------------------------------------------------

def test_t35jla_t1_scoped_positive_control_authority_bind_content_sql_order() -> None:
    """Direct fake-authority, synthetic data only; no real principal/MCP/WAL/RSS.

    PINS the new (scoped) read path: the metadata-only binding JOIN probe
    precedes the first unscoped ``SELECT * FROM jobs`` / ``SELECT * FROM
    operations`` (the result-column materialization path) which precedes the
    single bounded ``job_events`` ``LIMIT ? OFFSET ?`` read.
    """
    event_marker = "T35JLA-T1-EVT-3f01"
    result_marker = "T35JLA-T1-RES-9ac4"
    store = OperationStore(":memory:")
    trace: _SqlTrace | None = None
    try:
        markers: list[tuple[str, ...]] = []
        receiver = _DispatchReceiver(store, _GrantRecordingAuthority(markers))
        record = _start_job(store, tag="t1-bound", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlaT1Event", {"marker": event_marker})
        store.update_job(job_id, "SUCCEEDED", {"t35_synthetic": True},
                         result={"success": True, "data": {"marker": result_marker}})
        trace = _SqlTrace(store, markers)

        response = ControlDaemon._dispatch_admitted(receiver, {  # type: ignore[arg-type]
            "operation": "job_log",
            "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
            "execution": {"project_id": PROJECT_A},
        })

        assert response.get("success") is True, f"same-project read should succeed: {response!r}"
        data = response["data"]
        assert set(data) == JOB_LOG_DATA_KEYS
        assert data["job_id"] == job_id and data["next_offset"] == 1
        assert [event["metadata"]["marker"] for event in data["events"]] == [event_marker]
        # job.log serves the ledger only — the stored result must not echo.
        assert result_marker not in json.dumps(response, default=str), (
            "stored result leaked into the job.log event page"
        )
        assert markers[0] == ("authority", PROJECT_A, "inspect")
        assert sum(marker[0] == "authority" for marker in markers) == 1
        assert any(marker[0] == "sql" for marker in markers[1:])

        probe_idx = trace.first_index("FROM jobs j JOIN operations o")
        events_reads = trace.events_reads()
        assert probe_idx >= 0, "the durable binding gate must issue its metadata-only SQL probe"
        assert "length(CAST(" in trace.statements[probe_idx]
        assert "job_events" not in trace.statements[probe_idx]
        assert not _RESULT_TOKEN_RE.search(trace.statements[probe_idx]), (
            "the binding probe must select metadata columns only"
        )
        assert len(events_reads) >= 2 and re.search(r"\bLIMIT\s+100\s+OFFSET\s+0\b", events_reads[0], re.I)
        assert "length(CAST(" in events_reads[0]
        events_idx = trace.statements.index(events_reads[0])
        assert probe_idx < events_idx
        assert not any(_RESULT_TOKEN_RE.search(statement) for statement in trace.selects())
        assert not any(re.search(r"SELECT\s+\*\s+FROM\s+operations", statement, re.I)
                       for statement in trace.selects())
        assert not store.db.in_transaction, "read snapshot must not leave an open transaction"
    finally:
        if trace is not None:
            trace.detach()
        store.close()


# ---------------------------------------------------------------------------
# T2 GREEN — A/B cross-project: fake authority happily grants B, the durable
# binding gate must hide the A-bound job as plain not-found BEFORE any
# event SQL or result-column path, with zero sentinel echo; a later healthy
# same-project read still succeeds (no dangling snapshot transaction).
# ---------------------------------------------------------------------------

def test_t35jla_t2_foreign_project_refusal_precedes_event_and_result_sql() -> None:
    """Direct fake-authority, synthetic data only; no real principal/MCP/WAL/RSS.

    Connection-level SQL observation (not just store-method spies): the denial
    phase contains the metadata-only JOIN probe but ZERO ``job_events``
    statement and ZERO unscoped ``SELECT *`` over jobs/operations.
    """
    event_marker = "T35JLA-T2-EVT-77b2"
    result_marker = "T35JLA-T2-RES-1c98"
    store = OperationStore(":memory:")
    trace: _SqlTrace | None = None
    try:
        markers: list[tuple[str, ...]] = []
        receiver = _DispatchReceiver(store, _GrantRecordingAuthority(markers))
        record = _start_job(store, tag="t2-foreign", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlaT2Event", {"marker": event_marker})
        store.update_job(job_id, "SUCCEEDED", {"t35_synthetic": True},
                         result={"success": True, "data": {"marker": result_marker}})
        trace = _SqlTrace(store)

        foreign = ControlDaemon._dispatch_admitted(receiver, {  # type: ignore[arg-type]
            "operation": "job_log",
            "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
            "execution": {"project_id": PROJECT_B},
        })
        _assert_refusal(
            foreign, code="NODE_NOT_FOUND", message="job not found",
            label="B envelope vs A-bound job.log", sentinels=(event_marker, result_marker),
        )
        assert ("authority", PROJECT_B, "inspect") in markers, (
            f"the outer B grant should have run before the binding gate: {markers!r}"
        )

        selects = trace.selects()
        assert selects, "the binding gate must actually execute SQL, not just Python"
        assert not trace.events_reads(), (
            f"event-ledger SQL preceded the refusal: {selects!r}"
        )
        assert all("FROM jobs j JOIN operations o" in statement for statement in selects)
        assert not any(_RESULT_TOKEN_RE.search(statement) for statement in selects)
        assert not store.db.in_transaction, "refusal must conclude the owned read snapshot"

        # The same connection/store still serves a healthy same-project read.
        trace.reset()
        healthy = ControlDaemon._dispatch_admitted(receiver, {  # type: ignore[arg-type]
            "operation": "job_log",
            "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
            "execution": {"project_id": PROJECT_A},
        })
        assert healthy.get("success") is True, (
            f"healthy read after a scoped refusal must succeed: {healthy!r}"
        )
        assert healthy["data"]["events"][0]["metadata"]["marker"] == event_marker
        assert trace.events_reads(), "healthy read must issue its bounded event SQL"
    finally:
        if trace is not None:
            trace.detach()
        store.close()


# ---------------------------------------------------------------------------
# T3 GREEN — missing/empty outer execution.project_id: refused inside the
# module-level cached-job-read gate with ZERO SQL on this connection.
# ---------------------------------------------------------------------------

def test_t35jla_t3_missing_outer_project_is_refused_before_any_sql() -> None:
    """Direct fake-authority, synthetic data only; no real principal/MCP/WAL/RSS.

    The identity refusal strictly precedes even the binding probe: the trace
    observes no statement at all (not BEGIN, not SELECT).
    """
    event_marker = "T35JLA-T3-EVT-d4e5"
    store = OperationStore(":memory:")
    trace: _SqlTrace | None = None
    try:
        markers: list[tuple[str, ...]] = []
        receiver = _DispatchReceiver(store, _GrantRecordingAuthority(markers))
        record = _start_job(store, tag="t3-missing", project_id=PROJECT_A)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlaT3Event", {"marker": event_marker})
        trace = _SqlTrace(store)

        for label, execution in (("missing key", {}), ("empty string", {"project_id": ""})):
            trace.reset()
            markers.clear()
            refusal = ControlDaemon._dispatch_admitted(receiver, {  # type: ignore[arg-type]
                "operation": "job_log",
                "arguments": {"job_id": job_id, "offset": 0, "limit": 100},
                "execution": dict(execution),
            })
            _assert_refusal(
                refusal, code="PROJECT_IDENTITY_REQUIRED",
                message="job reads require outer execution.project_id",
                label=f"job.log {label} outer project", sentinels=(event_marker,),
            )
            assert trace.statements == [], (
                f"{label}: identity refusal must precede ALL SQL, got {trace.statements!r}"
            )
            assert markers == [], f"{label}: authority must not be consulted at all"
    finally:
        if trace is not None:
            trace.detach()
        store.close()


# ---------------------------------------------------------------------------
# T4 GREEN — the OLD unscoped read path (legacy private-caller branch of the
# REAL _control_read) still fetches the ledger through SQL-level LIMIT/OFFSET
# and honours offset/limit validation.
# ---------------------------------------------------------------------------

def test_t35jla_t4_legacy_unscoped_read_is_sql_bounded_and_validated() -> None:
    """Legacy direct ``_control_read`` (scoped_project_id=None default), synthetic
    data only; this branch authenticates nothing and is NOT a principal path.

    Pins that row COUNT is bounded at the SQL level (one
    ``LIMIT ? OFFSET ?`` read), and that the known result-column
    materialization (``SELECT * FROM operations`` before the ledger) precedes
    the event read — the observation motivating the proposed page budget; no
    proposed-contract assertion is made in this test.
    """
    store = OperationStore(":memory:")
    trace: _SqlTrace | None = None
    try:
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="t4-unscoped", project_id=None)
        job_id = record["job_id"]
        for index in range(50):
            store.add_event(job_id, f"T35JlaT4Event{index}", {"index": index})
        ground = store.events(job_id, 0, 1000)
        trace = _SqlTrace(store)

        page = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 0, "limit": 7},  # type: ignore[arg-type]
        )
        assert len(ground) == 50
        _assert_success_page(page, job_id=job_id, expected_events=ground[0:7], offset=0)
        events_reads = trace.events_reads()
        assert events_reads and re.search(r"\bLIMIT\s+7\s+OFFSET\s+0\b", events_reads[0], re.I)
        assert "length(CAST(" in events_reads[0]
        assert not any(_RESULT_TOKEN_RE.search(statement) for statement in trace.selects())

        trace.reset()
        tail = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 48, "limit": 7},  # type: ignore[arg-type]
        )
        _assert_success_page(tail, job_id=job_id, expected_events=ground[48:50], offset=48)

        negative = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": -1, "limit": 100},  # type: ignore[arg-type]
        )
        _assert_refusal(negative, code="INVALID_REQUEST", message="invalid log offset/limit",
                        label="job.log negative offset")
        assert not store.db.in_transaction
    finally:
        if trace is not None:
            trace.detach()
        store.close()


# ---------------------------------------------------------------------------
# T5 GREEN — large event NAME + small metadata: a single row can never be
# fetched unbounded in row count (SQL LIMIT/OFFSET), and an
# oversized-name row skipped via offset never enters the response bytes.
# ---------------------------------------------------------------------------

def test_t35jla_t5_large_name_small_metadata_row_bounded_and_unechoed_when_skipped() -> None:
    """Legacy direct ``_control_read`` branch, synthetic data only; no real
    principal/MCP/WAL/RSS. Pins observed behaviour, does not endorse it.

    Observation (pinned, not endorsed): the store schema and ``add_event``
    validate NO event-name size, so one row's BYTES stay unbounded; only the
    row COUNT is SQL-bounded. The proposed per-row/per-page byte budget is
    exercised in T6/T7, not here.
    """
    name_sentinel = "T35JLA-T5-BIGNAME-e0b7"
    store = OperationStore(":memory:")
    trace: _SqlTrace | None = None
    try:
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="t5-blename", project_id=None)
        job_id = record["job_id"]
        huge_name = name_sentinel + ("N" * (5 * MIB - len(name_sentinel)))
        store.add_event(job_id, huge_name, {"small": 1})
        for index in range(4):
            store.add_event(job_id, f"T35JlaT5Small{index}", {"index": index})
        huge_name_bytes = store.db.execute(
            "SELECT length(CAST(event AS BLOB)) FROM job_events WHERE job_id=? ORDER BY id LIMIT 1",
            (job_id,),
        ).fetchone()[0]
        assert huge_name_bytes > PROPOSED_MAX_PAGE_BYTES
        trace = _SqlTrace(store)

        # Skip the oversized-name row: it must never materialize in bytes.
        skipped = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 1, "limit": 10},  # type: ignore[arg-type]
        )
        ground = store.events(job_id, 1, 10)
        _assert_success_page(skipped, job_id=job_id, expected_events=ground, offset=1)
        assert name_sentinel not in json.dumps(skipped, default=str), (
            "an offset-skipped oversized-name row leaked into the response bytes"
        )

        # Reading the huge row itself still goes through a bounded SQL window.
        trace.reset()
        head = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 0, "limit": 1},  # type: ignore[arg-type]
        )
        assert head.get("success") is False and head.get("data") == {}
        assert head["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert head["error"]["safe_retry"] is False
        assert name_sentinel not in json.dumps(head, default=str)
        assert len(trace.events_reads()) == 1 and "length(CAST(" in trace.events_reads()[0]
        assert not store.db.in_transaction
    finally:
        if trace is not None:
            trace.detach()
        store.close()


# ---------------------------------------------------------------------------
# T6 EXPECTED RED — raw UTF-8 JSON under the proposed 4 MiB budget but over it
# once the transport encoder ASCII-escapes: the response must be short-paged
# or explicitly refused with no payload echo. Today the whole row is served.
# ---------------------------------------------------------------------------

def test_t35jla_t6_utf8_under_budget_but_escaped_over_budget_must_not_be_served() -> None:
    """Legacy direct ``_control_read`` branch, synthetic data only; byte numbers
    only — the payload is never printed. No real principal/MCP/WAL/RSS claim.

    FIRST proposed assertion reached on RED: a served page's transport bytes
    must be <= PROPOSED_MAX_PAGE_BYTES. The preconditions and the lossless
    round-trip (if served) are checked first and pass today.
    """
    marker = "T35JLA-T6-UTF8-5d19"
    store = OperationStore(":memory:")
    try:
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="t6-utf8", project_id=None)
        job_id = record["job_id"]

        # One CJK char: 3 UTF-8 bytes raw, 6 escaped chars. 700_000 chars ->
        # ~2.1 MB raw vs ~4.2 MB escaped: strictly under, then strictly over.
        payload = {"marker": marker, "filler": "\u952e" * 700_000}
        raw_utf8_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        escaped_utf8_bytes = len(json.dumps(payload, allow_nan=False).encode("utf-8"))
        assert raw_utf8_bytes < PROPOSED_MAX_PAGE_BYTES < escaped_utf8_bytes, (
            f"fixture must straddle the budget raw={raw_utf8_bytes} escaped={escaped_utf8_bytes}"
        )
        store.add_event(job_id, "T35JlaT6Utf8Event", {"marker": "fixture-placeholder"})
        raw_json = json.dumps(payload, ensure_ascii=False)
        assert store.db.execute("UPDATE job_events SET metadata=? WHERE job_id=?", (raw_json, job_id)).rowcount == 1
        stored = _stored_blob_bytes(store, job_id)
        assert len(stored) == 1 and stored[0] == raw_utf8_bytes < PROPOSED_MAX_PAGE_BYTES

        page = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 0, "limit": 100},  # type: ignore[arg-type]
        )
        if page.get("success") is True:
            events = page["data"]["events"]
            # If anything is served it must be lossless (never a silent
            # truncation), and under the proposed page budget.
            assert events and events[0]["metadata"]["marker"] == marker, (
                "a served row must round-trip exactly; no silent truncation"
            )
            served_bytes = _transport_bytes(page)
            assert served_bytes <= PROPOSED_MAX_PAGE_BYTES, (
                f"PROPOSED contract: a {served_bytes}-byte escaped page exceeds the "
                f"{PROPOSED_MAX_PAGE_BYTES}-byte budget and must be short-paged instead "
                "(first proposed assertion; expected RED on the current candidate)"
            )
        else:
            # The only conforming alternative: explicit refusal, no echo.
            error = page.get("error") if isinstance(page.get("error"), dict) else {}
            assert page.get("data") == {} and error.get("code"), (
                f"refusal must carry a code and empty data, got {page!r}"
            )
            assert marker not in json.dumps(page, sort_keys=True, default=str), (
                "refusal must not echo the payload"
            )
        assert not store.db.in_transaction
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T7 EXPECTED RED — first-row oversized: one row whose durable metadata alone
# exceeds the page budget cannot be short-paged, so fail-closed is the only
# conforming outcome: explicit refusal, empty data, zero payload echo.
# ---------------------------------------------------------------------------

def test_t35jla_t7_oversized_first_row_must_fail_closed_without_echo() -> None:
    """Legacy direct ``_control_read`` branch, synthetic data only; byte numbers
    only — the payload is never printed. No real principal/MCP/WAL/RSS claim.

    FIRST proposed assertion reached on RED: the read must NOT succeed with an
    over-budget single-row page (and today it does, echoing the durable
    metadata marker). Lossless-if-served and the durable byte facts are
    asserted first and pass today.
    """
    marker = "T35JLA-T7-OVERSIZED-8f60"
    filler_chars = 9_500_000
    store = OperationStore(":memory:")
    try:
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="t7-oversized", project_id=None)
        job_id = record["job_id"]
        store.add_event(job_id, "T35JlaT7OversizedEvent",
                        {"marker": marker, "filler": "q" * filler_chars})
        store.add_event(job_id, "T35JlaT7TinyAfter", {"index": 1})
        stored = _stored_blob_bytes(store, job_id)
        assert len(stored) == 2 and stored[0] > PROPOSED_MAX_PAGE_BYTES, (
            f"the first durable row must alone exceed the proposed budget: {stored!r}"
        )

        page = ControlDaemon._control_read(
            receiver, "job.log", {"job_id": job_id, "offset": 0, "limit": 100},  # type: ignore[arg-type]
        )
        assert page.get("success") is False and page.get("data") == {}
        assert page["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert page["error"]["safe_retry"] is False
        assert marker not in json.dumps(page, sort_keys=True, default=str)
        assert not store.db.in_transaction
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T8 GREEN — many small rows: full traversal with ONE fixed limit driven only
# by data.next_offset is lossless/dup-free/ordered, every page stays under the
# proposed budget, and the durable ledger hash is unchanged by the walk.
# ---------------------------------------------------------------------------

def test_t35jla_t8_full_traversal_identical_limit_pages_keep_ledger_hash_stable() -> None:
    """Legacy direct ``_control_read`` branch, synthetic data only; no real
    principal/MCP/WAL/RSS claim. All GREEN pins: traversal completeness,
    next_offset contiguity, per-page boundedness, and an unchanged
    raw-byte ledger fingerprint before vs after the whole walk.
    """
    row_count = 8
    walk_limit = 8
    store = OperationStore(":memory:")
    try:
        receiver = _BranchReceiver(store)
        record = _start_job(store, tag="t8-walk", project_id=None)
        job_id = record["job_id"]
        for index in range(row_count):
            store.add_event(job_id, f"T35JlaT8Event{index}", {"index": index, "bulk": "z" * 700_000})
        ground = store.events(job_id, 0, 1000)
        assert len(ground) == row_count
        before = _ledger_fingerprint(store, job_id)

        assembled: list[dict[str, Any]] = []
        offsets: list[int] = []
        offset = 0
        # Loop only on the response's own next_offset; the limit never changes.
        for _ in range(row_count + 2):  # hard anti-runaway bound
            page = ControlDaemon._control_read(
                receiver, "job.log", {"job_id": job_id, "offset": offset, "limit": walk_limit},  # type: ignore[arg-type]
            )
            events = page["data"]["events"]
            assert page.get("success") is True and events
            _assert_success_page(page, job_id=job_id,
                                 expected_events=ground[offset:offset + len(events)], offset=offset)
            assert _transport_bytes(page) <= PROPOSED_MAX_PAGE_BYTES, (
                "small-row pages must all stay under the proposed per-page budget"
            )
            assembled.extend(events)
            offsets.append(offset)
            assert page["data"]["next_offset"] == offset + len(events)
            offset = page["data"]["next_offset"]
            if offset == row_count:
                break
        assert assembled == ground, "walk must be lossless and duplication-free"
        ids = [event["id"] for event in assembled]
        assert ids == sorted(set(ids)), "event ids must be strictly increasing and unique"
        assert len(offsets) > 1, "the byte cap must cause a real short-page continuation"

        after = _ledger_fingerprint(store, job_id)
        assert after == before, (
            "reads must never rewrite, reorder, or delete durable ledger bytes"
        )
        assert not store.db.in_transaction
    finally:
        store.close()
