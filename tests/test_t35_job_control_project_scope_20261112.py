"""T35 — synthetic project-boundary contract for ``job.cancel`` / ``job.reconcile``.

The contract tests below encode a PROPOSED project gate on the two job-control
routes and are not claims of public MCP, real grants, or native behavior. They
extend ``tests/test_t35_job_read_project_scope_20261112.py`` (reads) to the
control side and deliberately leave that file untouched.

Static basis (live ``comsol_mcp/_control_daemon.py`` at authoring time):
- ``_dispatch_admitted`` (1033-…) is the real outer route exercised here.
  ``CACHED_JOB_ACTIONS`` (97-100) contains both ``job.cancel`` and
  ``job.reconcile``, so the canonical names route at 1083-1084 into
  ``_dispatch_catalog_job_control`` (wrapper 5444-5447) and its impl
  (12046-12145).  Legacy underscore names ``job_cancel``/``job_reconcile``
  are ``CONTROL_READS`` members (89-90): ``job_reconcile`` routes at
  1086-1087 straight into ``_dispatch_job_reconcile`` (11944-11997);
  ``job_cancel`` enters ``_dispatch_job_cancel`` (11999) ONLY when
  ``arguments["force_stop"] is True`` (1088-1089) — this file never sends
  ``force_stop`` and never enters that function; ``job_cancel`` is NOT in
  ``_SCOPED_LEGACY_JOB_READS`` (102-105), so the ordinary legacy cancel
  falls to the execution-blind ``self._control_read(operation, arguments)``
  at 1093.
- ``_authorize_cached_job_read`` (125-131) requires a nonempty outer
  ``execution.project_id`` and calls ``authorize_operation(project, "inspect")``,
  but the impl applies it only to ``_SCOPED_CACHED_JOB_READS`` (101), which
  EXCLUDES ``job.cancel`` and ``job.reconcile`` (gate at 12070-12073).
- CURRENT state on these routes (this is what the tests contrast against,
  not what they endorse): canonical ``job.reconcile`` short-circuits at
  12139-12140 into ``_dispatch_job_reconcile``; canonical ordinary
  ``job.cancel`` falls through 12143-12145 into the unscoped
  ``_control_read("job_cancel")`` whose 13382-13389 branch delegates to
  ``_cancel_job`` (13412-13526); the QUEUED branch 13462-13475 calls the
  real ``OperationStore.cancel_queued``.  None of these four routes
  (canonical cancel, canonical reconcile, legacy cancel, legacy reconcile)
  consults ``project_authority`` or compares the durable job/operation
  project binding at all.
- ``_dispatch_job_reconcile`` reads the binding-blind ``store.job`` at
  11948 and, for a status outside ``{UNKNOWN, RECONCILING}`` — every job in
  this file is QUEUED — returns ``{"success": True, "data": job}`` at
  11951-11952 with the WHOLE row: ``OperationStore.job`` (2000) + ``_job``
  (5636-5642) materialize metadata and the nested operation row including
  its durable result.  The Worker-facing 11953-11997 tail is structurally
  unreachable for QUEUED records and is never exercised.
- Store facts: ``OperationStore.begin`` (408-457) writes ONE metadata dict
  to both the jobs and the operations row, both with status QUEUED;
  ``update_job`` (2005-…) merges metadata and, with ``result=``, persists
  the durable operations result without leaving QUEUED; ``cancel_queued``
  (2351-2411) flips job+operation to CANCELLED, writes the OPERATION_CANCELLED
  result and appends the 'CANCELLED' event; ``events`` (3518-3526);
  ``project_job_read_snapshot`` (2148-2217) is the store-consistency
  binding primitive the daemon must pair with its own authorization.
- Catalog metadata (comsol_mcp/data/g2/02_ACTION_CATALOG.json):
  ``job.cancel`` effect HOST_CONTROL, scope project (11282-11331);
  ``job.reconcile`` effect STATE_WRITE, scope project (11369-11394).  The
  modeled proposed gate names follow that effect split: cancel →
  ``authorize_operation(project, "host_control")`` (as ``job.cleanup``
  already does at 12125), reconcile → ``authorize_operation(project,
  "project_write")`` (the catalog effect maps through ``state_write``).
  Both are real ``PROJECT_PERMISSIONS`` names
  (``_project_authority.py`` 24); the exact mechanism (dispatcher gate vs
  re-routing the alias) is left to the implementer — only the observable
  envelope-gated behavior is pinned.

Scope and honesty limits:
- Control-plane software boundary specification only, exercised in-process
  through the real unbound ``ControlDaemon._dispatch_admitted`` against an
  inert receiver with a FAKE per-project permission authority.  A supplied
  execution-envelope project ID is never an authenticated principal; the
  fake authority models the dispatcher gate only, not the MCP wire, real
  grants, or any public exploitability claim.
- All projects/jobs/events/results are unique synthetic literals in a fresh
  ``OperationStore(':memory:')``.  Jobs are task-created and ONLY ever
  QUEUED (cancel) or QUEUED (reconcile).  No real or historical job, no
  real or synthetic UNKNOWN/RECONCILING state, no force-stop, no Worker,
  process, service, SSH, endpoint, credential, model, network, old DB or
  disk state is touched; no actual system-level cancellation happens —
  every write below is a durable-store row inside ':memory:'.
- ``force_stop`` is never sent, so ``_dispatch_job_cancel`` and every
  force/Worker branch stay unreachable; the inert receiver does not even
  expose it.  ``_log_exception`` (which would write control-errors.log) is
  replaced by a failure-only no-I/O sentinel, so reaching the generic
  unknown-state handler fails loudly.
- Real components used unchanged: the actual unbound ``_dispatch_admitted``,
  ``_dispatch_catalog_job_control``, ``_dispatch_catalog_job_control_impl``,
  ``_dispatch_job_reconcile``, ``_control_read``, ``_cancel_job``,
  ``_timeouts``, ``_error``, ``_exception`` and the real catalog
  ``validate_call``; the real ``OperationStore`` begin/add_event/update_job/
  cancel_queued/job/events paths.  Store spies are pass-through recorders
  that never replace return values.
- Proposed contract asserted: (1) canonical and legacy ordinary (non-force)
  ``job.cancel`` require a nonempty outer ``execution.project_id``; the
  per-project identity is never carried in domain arguments; (2) the cancel
  effect for the envelope project is authorized BEFORE any durable write,
  recorded on one shared ordered timeline; (3) the durable job/operation
  project binding must match the envelope project — a project-B caller that
  is itself validly authorized must NOT change an A-bound job's or
  operation's status, events, metadata or durable result (proved by
  before/after read-back), must not leave an unbound record guessable, and
  must fail closed on a missing envelope project identity; (4) QUEUED
  ``job.reconcile`` on both routes is authorized before content retrieval
  and must never disclose another project's job metadata/event/result
  sentinels; (5) the positive controls are real QUEUED_ABORT
  ``cancel_queued`` semantics and the real QUEUED early-return reconcile —
  a blanket denial cannot pass as correct.
- Deliberately NOT touched: force-stop/``_dispatch_job_cancel``, UNKNOWN or
  RECONCILING reconciliation (Worker paths), ``job.resume``,
  ``job.cleanup``, solver dispatch, protected prior read/authority tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

DENIAL_CODE = "T35JC_FAKE_AUTHORITY_DENIED"
DENIAL_MESSAGE = "synthetic t35 job-control fake authority denial"

PROJECT_A = "t35jc-proj-A-20261112"
PROJECT_B = "t35jc-proj-B-20261112"

READ_KINDS = ("job_row", "events")
WRITE_KINDS = ("cancel_queued", "add_event", "update_job")


class _FakeProjectAuthority:
    """Fake authority with explicit per-project permission grants; records calls.

    Deliberately not the production ``ProjectAuthority``; it models only the
    dispatcher-level gate the proposed contract requires.  Authority calls
    share one ordered timeline with the store spies, so
    authorize-before-write/retrieve ordering is provable.
    """

    def __init__(
        self,
        permissions: Mapping[str, set[str]] | None = None,
        timeline: list[tuple[str, ...]] | None = None,
    ) -> None:
        self.permissions: dict[str, set[str]] = {
            project: set(actions) for project, actions in (permissions or {}).items()
        }
        self.calls: list[tuple[str, str]] = []
        self.timeline = timeline

    def authorize_operation(self, project_id: str, action: str) -> None:
        self.calls.append((project_id, action))
        if self.timeline is not None:
            self.timeline.append(("authority", project_id, action))
        if action not in self.permissions.get(project_id, set()):
            raise ExecutionContractError(DENIAL_CODE, DENIAL_MESSAGE)


class _InertDispatchReceiver:
    """Inert receiver that delegates to the REAL unbound daemon methods.

    It exposes exactly the attributes/methods the exercised real paths touch:
    ``.store`` / ``.project_authority`` carry task state; ``_timeouts``,
    ``_error``, ``_exception``, ``_dispatch_catalog_job_control``,
    ``_dispatch_catalog_job_control_impl``, ``_dispatch_job_reconcile``,
    ``_control_read`` and ``_cancel_job`` are bound to the actual
    ``ControlDaemon`` implementations (live 1033-…, 5444-5447, 11944-11997,
    12046-12145, 13261-…, 13412-13526, 13753-13772), never reimplemented.
    ``_dispatch_job_cancel`` (force-stop, 11999-12044) is intentionally NOT
    exposed: these tests never send ``force_stop``, and accidental routing
    into it would hit ``AttributeError`` → the generic handler → the
    ``_log_exception`` AssertionError sentinel.  ``_log_exception`` would
    write ``control-errors.log``; its no-I/O sentinel raise escapes the
    outer handler, so reaching the generic unknown-state path fails loudly
    instead of hiding a defect behind an EXECUTION_STATE_UNKNOWN response.
    """

    def __init__(self, store: OperationStore, project_authority: _FakeProjectAuthority) -> None:
        self.store = store
        self.project_authority = project_authority

    @staticmethod
    def _timeouts(execution: dict[str, Any]) -> dict[str, Any]:
        return ControlDaemon._timeouts(execution)

    def _dispatch_catalog_job_control(
        self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]
    ) -> dict[str, Any]:
        return ControlDaemon._dispatch_catalog_job_control(
            self, inner_operation, inner_arguments, execution=execution  # type: ignore[arg-type]
        )

    def _dispatch_catalog_job_control_impl(
        self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]
    ) -> dict[str, Any]:
        return ControlDaemon._dispatch_catalog_job_control_impl(
            self, inner_operation, inner_arguments, execution=execution  # type: ignore[arg-type]
        )

    def _dispatch_job_reconcile(
        self, arguments: Mapping[str, Any], *, execution: Mapping[str, Any]
    ) -> dict[str, Any]:
        return ControlDaemon._dispatch_job_reconcile(
            self, arguments, execution=execution  # type: ignore[arg-type]
        )

    def _control_read(self, operation: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return ControlDaemon._control_read(self, operation, arguments, **kwargs)  # type: ignore[arg-type]

    def _cancel_job(self, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        # The current ordinary-cancel contract routes the authorized outer
        # project through ``scoped_project_id``; forward it unchanged so the
        # real scoped binding check and strict cancel_queued write run as-is.
        return ControlDaemon._cancel_job(self, arguments, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
        return ControlDaemon._error(code, message, **details)

    @staticmethod
    def _exception(exc: BaseException) -> dict[str, Any]:
        return ControlDaemon._exception(exc)

    @staticmethod
    def _log_exception() -> None:
        raise AssertionError(
            "T35JC inert receiver reached the generic _dispatch_admitted exception path"
        )


def _install_store_spies(store: OperationStore, timeline: list[tuple[str, ...]]) -> None:
    """Pass-through recorders for durable reads AND writes on one shared timeline.

    Return values always come from the real methods.  Install only after
    fixture setup so fixture writes never enter the timeline; then a refused
    control attempt must show zero WRITE_KINDS entries and positive controls
    must show every write strictly after the authority call.
    """
    real_job = store.job
    real_events = store.events
    real_cancel_queued = store.cancel_queued
    real_add_event = store.add_event
    real_update_job = store.update_job

    def spy_job(job_id: str) -> Any:
        timeline.append(("job_row", job_id))
        return real_job(job_id)

    def spy_events(job_id: str, offset: int = 0, limit: int = 100) -> Any:
        timeline.append(("events", job_id))
        return real_events(job_id, offset=offset, limit=limit)

    def spy_cancel_queued(job_id: str, reason: str = "cancelled by user",
                          **kwargs: Any) -> Any:
        # The strict route passes project_id/enforce_project_binding; the
        # spy records and forwards them without weakening the write.
        timeline.append(("cancel_queued", job_id))
        return real_cancel_queued(job_id, reason=reason, **kwargs)

    def spy_add_event(job_id: str, event: str, metadata: dict[str, Any] | None = None) -> Any:
        timeline.append(("add_event", job_id, event))
        return real_add_event(job_id, event, metadata)

    def spy_update_job(
        job_id: str, status: str, metadata: dict[str, Any] | None = None, *,
        result: dict[str, Any] | None = None,
    ) -> Any:
        timeline.append(("update_job", job_id, status))
        return real_update_job(job_id, status, metadata, result=result)

    store.job = spy_job  # type: ignore[method-assign]
    store.events = spy_events  # type: ignore[method-assign]
    store.cancel_queued = spy_cancel_queued  # type: ignore[method-assign]
    store.add_event = spy_add_event  # type: ignore[method-assign]
    store.update_job = spy_update_job  # type: ignore[method-assign]


def _create_queued_job(
    store: OperationStore,
    *,
    project_id: str | None,
    tag: str,
    content_marker: str,
    event_markers: tuple[str, ...] = (),
    result_marker: str | None = None,
) -> str:
    """Create one task-authored QUEUED durable job through the real write paths.

    ``begin`` (408-457) writes the SAME metadata to the jobs and operations
    rows, so a ``project_id`` here yields an unambiguous durable binding on
    BOTH sides; ``project_id=None`` yields a historic-looking synthetic
    record with no usable project binding anywhere.  The QUEUED record's
    durable operations ``result`` (when requested) is seeded through the
    ordinary ``update_job`` write path without leaving QUEUED.
    """
    metadata: dict[str, Any] = {"t35_synthetic": True, "content_marker": content_marker}
    if project_id is not None:
        metadata["execution"] = {"project_id": project_id}
    record, reused = store.begin(
        request_id=f"t35jc-req-{tag}",
        idempotency_key=f"t35jc-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    created_row = store.job(job_id)
    assert created_row is not None and created_row["status"] == "QUEUED"
    for marker in event_markers:
        store.add_event(job_id, "T35JCFixtureEvent", {"marker": marker})
    if result_marker is not None:
        store.update_job(
            job_id, "QUEUED", {"t35_synthetic": True},
            result={"success": True, "data": {"marker": result_marker}},
        )
    settled_row = store.job(job_id)
    assert settled_row is not None and settled_row["status"] == "QUEUED"
    return job_id


def _snapshot(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Before/after durable read-back of job AND operation state plus events.

    ``store.job`` returns the row with the nested operation row and the
    materialized durable result (``_job`` 5636-5642), so one snapshot covers
    job status/metadata/finished_at, operation status/result and events.
    """
    job = store.job(job_id)
    assert job is not None
    return {
        "job_status": job["status"],
        "job_metadata": job["metadata"],
        "finished_at": job["finished_at"],
        "operation_status": job["operation"]["status"],
        "operation_result": job["operation"]["result"],
        "events": store.events(job_id, limit=1000),
    }


def _outer_route(
    receiver: _InertDispatchReceiver,
    operation: str,
    arguments: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    """The real unbound outer route — nothing below bypasses it.

    ``_dispatch_admitted`` turns a raised ExecutionContractError (including a
    fake-authority denial) into an error response via ``_exception``, so
    refusals here surface as non-success responses, not raises.
    """
    return ControlDaemon._dispatch_admitted(
        receiver,  # type: ignore[arg-type]
        {
            "operation": operation,
            "arguments": dict(arguments),
            "execution": dict(execution),
        },
    )


def _canonical_route(
    receiver: _InertDispatchReceiver, operation: str, inner: Mapping[str, Any], envelope_project: str
) -> dict[str, Any]:
    """Canonical CACHED_JOB_ACTIONS route via outer ``_dispatch_admitted`` (1083-1084)."""
    return _outer_route(receiver, operation, inner, {"project_id": envelope_project})


def _legacy_route(
    receiver: _InertDispatchReceiver,
    legacy_operation: str,
    arguments: Mapping[str, Any],
    *,
    envelope_project: str | None,
) -> dict[str, Any]:
    """Legacy underscore route via outer ``_dispatch_admitted`` (1085-1093).

    The project identity is routed ONLY in the outer execution envelope,
    never as a domain argument; ``envelope_project=None`` makes the absence
    explicit as ``execution={}`` for the fail-closed-on-absence control.
    """
    execution: dict[str, Any] = {} if envelope_project is None else {"project_id": envelope_project}
    return _outer_route(receiver, legacy_operation, arguments, execution)


def _attempt(call: Callable[[], dict[str, Any]]) -> tuple[ExecutionContractError | None, dict[str, Any] | None]:
    try:
        return None, call()
    except ExecutionContractError as exc:
        return exc, None


def _serialized(exc: ExecutionContractError | None, response: dict[str, Any] | None) -> str:
    payload = exc.as_dict() if exc is not None else response
    return json.dumps(payload, sort_keys=True, default=str)


def _assert_refused_without_sentinel(
    exc: ExecutionContractError | None,
    response: dict[str, Any] | None,
    sentinels: tuple[str, ...],
    *,
    label: str,
    deny_no_force: bool = False,
) -> None:
    """Proposed-contract refusal: not successful, sentinel nowhere in payload."""
    assert exc is not None or (
        isinstance(response, dict) and response.get("success") is not True
    ), f"{label}: expected a refusal, got {response!r}"
    blob = _serialized(exc, response)
    for sentinel in sentinels:
        assert sentinel not in blob, f"{label}: sentinel leaked into refusal payload"
    if deny_no_force:
        assert "force_stop" not in blob and "FORCE_STOP" not in blob, (
            f"{label}: refusal must not introduce force-stop semantics"
        )


def _no_store_writes(timeline: list[tuple[str, ...]], label: str) -> None:
    writes = [entry for entry in timeline if entry[0] in WRITE_KINDS]
    assert not writes, f"{label}: durable write happened despite the required refusal: {writes!r}"


def _assert_authority_precedes_every_write(timeline: list[tuple[str, ...]], label: str) -> None:
    authority_at = [i for i, entry in enumerate(timeline) if entry[0] == "authority"]
    write_at = [i for i, entry in enumerate(timeline) if entry[0] in WRITE_KINDS]
    assert authority_at, f"{label}: expected a recorded authority call"
    assert write_at, f"{label}: expected the durable control write to have happened"
    assert min(authority_at) < min(write_at), (
        f"{label}: a durable write happened before the authority call: {timeline!r}"
    )


# ---------------------------------------------------------------------------
# (1) Canonical ``job.cancel`` — ordinary (no force_stop ever sent), QUEUED only.
# ---------------------------------------------------------------------------

def test_t35jctl_canonical_cancel_queued_job_of_authorized_project_A_succeeds_gate_first() -> None:
    """PROPOSED CONTRACT (RED today): authorized A envelope + A-bound QUEUED job.
    The success half is the REAL current QUEUED_ABORT behavior (impl 12143-12145
    → _control_read 13382-13389 → _cancel_job 13462-13475 → store.cancel_queued
    2351-2411): CANCELLED, cancel_accepted, engine_dispatched/engine_stopped
    False, mode QUEUED_ABORT, durable CANCELLED event/result written — all in
    ':memory:' with no Worker, process or force-stop.  The PROPOSED addition is
    the gate ordering: ``authorize_operation(A, 'host_control')`` recorded
    before any durable write.  Today no authority is consulted at all, so
    ``authority.calls`` is empty — the first assertion below that fails is
    exactly that authority call, not the cancel outcome."""
    events = ("T35JC-EVT-CANCELPOSALPHA-3f10",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="can-pos-a",
            content_marker="T35JC-RES-CANCELPOSALPHA-3f10", event_markers=events,
        )
        before = _snapshot(store, job_id)
        assert before["job_status"] == "QUEUED" and before["operation_status"] == "QUEUED"
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(
            receiver, "job.cancel", {"job_id": job_id, "reason": "synthetic t35 queued cancel"}, PROJECT_A
        )
        assert response.get("success") is True
        data = response["data"]
        assert data["job_id"] == job_id
        assert data["status"] == "CANCELLED"
        assert data["cancel_accepted"] is True
        assert data["engine_stopped"] is False
        assert data["engine_dispatched"] is False
        assert data["mode"] == "QUEUED_ABORT"
        blob = _serialized(None, response)
        assert "force_stop" not in blob and "FORCE_STOP" not in blob

        after = _snapshot(store, job_id)
        assert after["job_status"] == "CANCELLED"
        assert after["operation_status"] == "CANCELLED"
        assert after["finished_at"] is not None
        assert after["operation_result"]["error"]["code"] == "OPERATION_CANCELLED"
        assert [event["metadata"].get("marker") for event in before["events"]] == list(events)
        assert any(
            event["event"] == "CANCELLED" and event["metadata"].get("mode") == "QUEUED_ABORT"
            for event in after["events"]
        )
        # PROPOSED gate part of the positive contract too:
        assert authority.calls == [(PROJECT_A, "host_control")]
        assert timeline[0] == ("authority", PROJECT_A, "host_control")
        _assert_authority_precedes_every_write(timeline, "canonical cancel A positive")
    finally:
        store.close()


def test_t35jctl_canonical_cancel_authorized_B_cannot_change_project_A_queued_job() -> None:
    """PROPOSED CONTRACT (RED today): caller validly authorized for B (and only
    B) must NOT change an A-bound QUEUED job's durable state.  Today the
    canonical cancel falls through impl 12143-12145 with no authority and no
    binding comparison, so the cancel SUCCEEDS and the before/after read-back
    of job status, operation status, result and events differs — that is the
    failure this test pins.  The proposal also forbids force-stop semantics
    creeping into the refusal."""
    marker = "T35JC-RES-CANCELFORBID-72aa"
    events = ("T35JC-EVT-CANCELFORBID-72aa",)
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_queued_job(
            store, project_id=PROJECT_A, tag="can-xproj-b",
            content_marker=marker, event_markers=events, result_marker=marker,
        )
        before = _snapshot(store, foreign_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver, "job.cancel",
                {"job_id": foreign_job, "reason": "synthetic t35 cross-project cancel"}, PROJECT_B,
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker,), label="cross-project canonical job.cancel", deny_no_force=True
        )
        after = _snapshot(store, foreign_job)
        assert after == before, (
            "B-authorized cancel changed A-bound durable state; "
            f"before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "cross-project canonical job.cancel")
        # Proposed gate for the B caller's own envelope project still runs and
        # precedes anything else the route does:
        assert authority.calls == [(PROJECT_B, "host_control")]
        assert timeline[0] == ("authority", PROJECT_B, "host_control")
    finally:
        store.close()


def test_t35jctl_canonical_cancel_unbound_queued_job_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): an A-authorized envelope plus a NEW
    synthetic record with no usable project binding anywhere must fail closed —
    no guessed ownership, and the record's durable state must be untouched
    (today it is cancelled to CANCELLED)."""
    marker = "T35JC-RES-CANCELUNBOUND-45c9"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_queued_job(
            store, project_id=None, tag="can-unbound", content_marker=marker,
            event_markers=("T35JC-EVT-CANCELUNBOUND-45c9",),
        )
        before = _snapshot(store, unbound_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver, "job.cancel", {"job_id": unbound_job, "reason": "synthetic t35 unbound cancel"}, PROJECT_A
            )
        )
        _assert_refused_without_sentinel(exc, response, (marker,), label="unbound canonical job.cancel")
        after = _snapshot(store, unbound_job)
        assert after == before, f"unbound record changed: before={before!r} after={after!r}"
        _no_store_writes(timeline, "unbound canonical job.cancel")
    finally:
        store.close()


def test_t35jctl_canonical_cancel_without_outer_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): an explicitly absent ``execution.project_id``
    is a missing identity, not a wildcard — the ordinary canonical cancel must
    fail closed (today it runs the execution-blind fall-through at 12145 and
    cancels the A-bound QUEUED job)."""
    marker = "T35JC-RES-CANCELNOENV-8db1"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="can-noenv", content_marker=marker,
            event_markers=("T35JC-EVT-CANCELNOENV-8db1",),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _outer_route(
                receiver, "job.cancel", {"job_id": job_id, "reason": "synthetic t35 no-envelope cancel"}, {}
            )
        )
        _assert_refused_without_sentinel(exc, response, (marker,), label="no-envelope canonical job.cancel")
        after = _snapshot(store, job_id)
        assert after == before, f"no-envelope cancel changed durable state: before={before!r} after={after!r}"
        _no_store_writes(timeline, "no-envelope canonical job.cancel")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (2) Legacy ``job_cancel`` — ordinary (never force_stop), QUEUED only.
#     Outer 1085-1093: without force_stop the legacy name reaches the
#     execution-blind ``_control_read(operation, arguments)`` at 1093.
# ---------------------------------------------------------------------------

def test_t35jctl_legacy_cancel_queued_job_of_authorized_project_A_succeeds_gate_first() -> None:
    """PROPOSED CONTRACT (RED today): same positive contract as the canonical
    route on the legacy alias — real QUEUED_ABORT success plus the
    ``authorize_operation(A, 'host_control')`` call recorded before the first
    durable write (today the legacy route never consults any authority)."""
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="leg-pos-a",
            content_marker="T35JC-RES-LEGACYPOSALPHA-e03b",
            event_markers=("T35JC-EVT-LEGACYPOSALPHA-e03b",),
        )
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _legacy_route(
            receiver, "job_cancel", {"job_id": job_id, "reason": "synthetic t35 legacy cancel"},
            envelope_project=PROJECT_A,
        )
        assert response.get("success") is True
        assert response["data"]["status"] == "CANCELLED"
        assert response["data"]["cancel_accepted"] is True
        assert response["data"]["mode"] == "QUEUED_ABORT"
        assert response["data"]["engine_stopped"] is False
        after = _snapshot(store, job_id)
        assert after["job_status"] == "CANCELLED"
        assert after["operation_status"] == "CANCELLED"
        # PROPOSED gate:
        assert authority.calls == [(PROJECT_A, "host_control")]
        assert timeline[0] == ("authority", PROJECT_A, "host_control")
        _assert_authority_precedes_every_write(timeline, "legacy job_cancel A positive")
    finally:
        store.close()


def test_t35jctl_legacy_cancel_authorized_B_cannot_change_project_A_queued_job() -> None:
    """PROPOSED CONTRACT (RED today): B-authorized caller on the legacy alias
    must not change an A-bound QUEUED job — job/operation status, events,
    metadata and durable result identical before/after (today the
    execution-blind 1093 fall-through cancels it).  Only the observable
    envelope-gated refusal is pinned, not the implementing mechanism."""
    marker = "T35JC-RES-LEGACYFORBID-c618"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_queued_job(
            store, project_id=PROJECT_A, tag="leg-xproj-b",
            content_marker=marker, event_markers=("T35JC-EVT-LEGACYFORBID-c618",),
            result_marker=marker,
        )
        before = _snapshot(store, foreign_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_cancel", {"job_id": foreign_job, "reason": "synthetic t35 legacy cross cancel"},
                envelope_project=PROJECT_B,
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker,), label="cross-project legacy job_cancel", deny_no_force=True
        )
        after = _snapshot(store, foreign_job)
        assert after == before, (
            f"legacy B cancel changed A-bound durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "cross-project legacy job_cancel")
    finally:
        store.close()


def test_t35jctl_legacy_cancel_unbound_queued_job_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): legacy alias + A envelope on an unbound
    synthetic record fails closed; the record stays QUEUED with identical
    events and operation result."""
    marker = "T35JC-RES-LEGACYUNBOUND-1f74"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_queued_job(
            store, project_id=None, tag="leg-unbound", content_marker=marker,
            event_markers=("T35JC-EVT-LEGACYUNBOUND-1f74",),
        )
        before = _snapshot(store, unbound_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_cancel", {"job_id": unbound_job, "reason": "synthetic t35 legacy unbound cancel"},
                envelope_project=PROJECT_A,
            )
        )
        _assert_refused_without_sentinel(exc, response, (marker,), label="unbound legacy job_cancel")
        after = _snapshot(store, unbound_job)
        assert after == before, f"legacy unbound cancel changed state: before={before!r} after={after!r}"
        _no_store_writes(timeline, "unbound legacy job_cancel")
    finally:
        store.close()


def test_t35jctl_legacy_cancel_without_outer_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): legacy cancel with the outer execution
    project identity EXPLICITLY absent (``execution={}``) must fail closed —
    fail-closed-on-absence only, no authenticated-principal claim."""
    marker = "T35JC-RES-LEGCANCELNOENV-9a52"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="leg-noenv", content_marker=marker,
            event_markers=("T35JC-EVT-LEGCANCELNOENV-9a52",),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_cancel", {"job_id": job_id, "reason": "synthetic t35 legacy no-envelope cancel"},
                envelope_project=None,
            )
        )
        _assert_refused_without_sentinel(exc, response, (marker,), label="no-envelope legacy job_cancel")
        after = _snapshot(store, job_id)
        assert after == before, f"legacy no-envelope cancel changed state: before={before!r} after={after!r}"
        _no_store_writes(timeline, "no-envelope legacy job_cancel")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (3) ``job.reconcile`` QUEUED-only — both routes reach the real
#     ``_dispatch_job_reconcile`` (canonical via impl 12139-12140, legacy via
#     outer 1086-1087).  QUEUED early-returns at 11951-11952 with the FULL
#     binding-blind row; the UNKNOWN/RECONCILING Worker tail (11953-11997) is
#     structurally unreachable here and never exercised.
# ---------------------------------------------------------------------------

def test_t35jctl_canonical_reconcile_queued_job_of_authorized_project_A_reads_gate_first() -> None:
    """PROPOSED CONTRACT (RED today): authorized A envelope + A-bound QUEUED
    job.  The read half is the real current early return — success with the
    job's own row, still QUEUED, no writes; QUEUED never touches events or a
    Worker.  The PROPOSED addition: ``authorize_operation(A, 'project_write')`` is
    recorded before the job-row retrieval (today none happens)."""
    marker = "T35JC-RES-RECONPOSALPHA-2e6d"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="rec-pos-a", content_marker=marker,
            event_markers=("T35JC-EVT-RECONPOSALPHA-2e6d",),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(receiver, "job.reconcile", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["status"] == "QUEUED"
        assert response["data"]["metadata"]["content_marker"] == marker
        after = _snapshot(store, job_id)
        assert after == before, "QUEUED reconcile must not write; state changed"
        _no_store_writes(timeline, "canonical reconcile A positive")
        # PROPOSED gate:
        assert authority.calls == [(PROJECT_A, "project_write")]
        assert timeline[0] == ("authority", PROJECT_A, "project_write")
    finally:
        store.close()


def test_t35jctl_canonical_reconcile_authorized_B_cannot_read_project_A_queued_job() -> None:
    """PROPOSED CONTRACT (RED today): B-project-write caller must NOT obtain the
    A-bound QUEUED job's content.  Today ``_dispatch_job_reconcile`` reads the
    binding-blind ``store.job`` and returns the whole row, including metadata
    and materialized durable result, but not the separately stored events.
    Refusal must withhold sentinels and keep the record QUEUED and unchanged."""
    marker = "T35JC-RES-RECONFORBID-b907"
    event_marker = "T35JC-EVT-RECONFORBID-b907"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_queued_job(
            store, project_id=PROJECT_A, tag="rec-xproj-b", content_marker=marker,
            event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, foreign_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.reconcile", {"job_id": foreign_job}, PROJECT_B)
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="cross-project canonical job.reconcile"
        )
        after = _snapshot(store, foreign_job)
        assert after == before, "reconcile must be read-only anyway; state changed"
        _no_store_writes(timeline, "cross-project canonical job.reconcile")
        assert authority.calls == [(PROJECT_B, "project_write")]
    finally:
        store.close()


def test_t35jctl_legacy_reconcile_queued_job_of_authorized_project_A_reads_gate_first() -> None:
    """PROPOSED CONTRACT (RED today): the legacy ``job_reconcile`` alias
    (outer 1086-1087) serves the authorized A case with the same real QUEUED
    early-return row and the same proposed authority-before-retrieval
    ordering as the canonical route — wrapper adds no bypass and no double
    gate."""
    marker = "T35JC-RES-LEGRECONPOSALPHA-54fd"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="lrec-pos-a", content_marker=marker,
            event_markers=("T35JC-EVT-LEGRECONPOSALPHA-54fd",),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _legacy_route(receiver, "job_reconcile", {"job_id": job_id}, envelope_project=PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["status"] == "QUEUED"
        after = _snapshot(store, job_id)
        assert after == before, "QUEUED reconcile must not write; state changed"
        _no_store_writes(timeline, "legacy job_reconcile A positive")
        assert authority.calls == [(PROJECT_A, "project_write")]
        assert timeline[0] == ("authority", PROJECT_A, "project_write")
    finally:
        store.close()


def test_t35jctl_legacy_reconcile_authorized_B_cannot_read_project_A_queued_job() -> None:
    """PROPOSED CONTRACT (RED today): the legacy alias shares the same
    binding-blind QUEUED disclosure — a B envelope with B-project-write authority
    must not receive the A-bound record's metadata/result sentinels
    through ``_dispatch_admitted`` either."""
    marker = "T35JC-RES-LEGRECONFORBID-08ce"
    event_marker = "T35JC-EVT-LEGRECONFORBID-08ce"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_queued_job(
            store, project_id=PROJECT_A, tag="lrec-xproj-b", content_marker=marker,
            event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, foreign_job)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_reconcile", {"job_id": foreign_job}, envelope_project=PROJECT_B
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="cross-project legacy job_reconcile"
        )
        after = _snapshot(store, foreign_job)
        assert after == before, "legacy reconcile changed state"
        _no_store_writes(timeline, "cross-project legacy job_reconcile")
        assert authority.calls == [(PROJECT_B, "project_write")]
    finally:
        store.close()
