"""T31/U07: stage-callback admission-claim edges for ``experiment.stage_define``
and ``experiment.stage_run`` (cancel-vs-claim race, controlled callback
re-entry, and injected exceptions around the claim CAS).

Task-owned, offline; the author's original bytes and 6F/4P run are preserved
under _state. Parent corrected fixture/event expectations and changed the four
fault cases from characterizing non-durable pending to a proposed durable
UNKNOWN requirement. Parent runs are identified separately by source hash.

Source anchors actually read in this worktree candidate (daemon candidate5):
  * 398-433  module-level ``_claim_stage_callback(daemon, record, timeouts,
    submitted)``: CAS ``store.transition_status(job_id, ("QUEUED",), "RUNNING",
    event_name="RUNNING")`` before ANY stage side effect; on a failed claim it
    returns ``job["result"]`` when the job view surfaces one, else
    ``_pending(record, engine_dispatched=None)`` for STARTING/RUNNING/UNKNOWN/
    RECONCILING, else an EXECUTION_STATE_UNKNOWN envelope (safe_retry=False,
    data.engine_dispatched=None); the whole CAS sits inside one ``try`` so an
    injected exception BEFORE or AFTER the commit both land in the same
    UNKNOWN envelope with ``type=<exception name>``.
  * 2753 / 3013  call sites inside the real captured callbacks
    ``register_definition`` / ``preflight_and_record`` — a non-None admission
    is returned directly, so the claim fully gates registration/admission.
  * 2640-2883 / 2884-3292  the two real routes (begin, submit, bounded
    future wait, pending envelope).
  * ``comsol_mcp/_operation_store.py`` 2013-2090 ``transition_status``
    (CAS; a current-status mismatch commits no event; 2125-2185
    ``cancel_queued`` CASes only the jobs row QUEUED->CANCELLED and durably
    stores the QUEUED_ABORT cancel envelope; 1870-1934 ``finish`` terminal
    immunity — none of these three may be called by this file except through
    the real product paths exercised below).
  * 13315-13429 ``_cancel_job`` (real, invoked unbound): QUEUED branch ->
    ``cancel_queued`` + mode QUEUED_ABORT (13365-13378).

What is REAL (product code executed unbound against inert receivers):
  * ``ControlDaemon._dispatch_admitted`` plus the full stage_define/stage_run
    route bodies, ``ControlDaemon._cancel_job`` called as the real unbound
    method inside a reversible ``store.transition_status`` wrapper, and the
    real ``_claim_stage_callback`` / ``_pending`` / ``_finish`` / ``_error`` /
    ``_exception`` / ``_timeouts`` helpers via the reused receivers.
  * A brand-new ``OperationStore(":memory:")`` per test performs every
    begin/CAS/cancel/finish/event observation; each is closed in ``finally``.
  * The callbacks are the real closures produced by the routes
    (``register_definition`` / ``preflight_and_record``), captured by the
    fake scheduler and executed by the tests themselves.
  * The reversible wrapper replaces only the instance attribute
    ``store.transition_status`` (restore via the captured bound original in
    ``finally``). Except for deliberately injected pre-CAS exceptions, the
    wrapper delegates to the REAL CAS; it does not reimplement store logic.

What is FAKE (bounded scaffolding, never an engine/authorization/native
claim): no daemon instance, thread, network, Worker, SSH or COMSOL session;
the fake schedulers only capture ``(context, callback)``; fake backends/
ledgers prove code reachability only; the two injected exception classes and
the nested fixture's UNKNOWN outcome are SYNTHETIC states confined to this
fresh in-memory store and must never be cited as any historical/real
UNKNOWN job. Fixtures are imported from the three already-vetted T31 modules
(read, never modified), exactly as the queue-cancel sibling does. No T19
r3/r4 adapter diff/pins, no T13 installed/cursor surface, no T14/W13
geometry-membership result, and no T21 list result is touched, sought, or
implied here.

Residual reading uncertainties (explicit, not guessed silently):
  * Whether the job view returned by ``transition_status`` on a REJECTED CAS
    carries the durable operation result is relied on by the claim's
    "return the authoritative result" branch. The daemon's own job_result
    route (13312) already depends on ``job["result"]``, so the race tests
    PREDICT that branch is taken; if it is not, the labelled fence below
    fails first and must be reported as observed instead.
  * ``_pending`` envelope internals beyond ``data.job_id`` /
    ``data.engine_dispatched`` were outside the permitted reading window,
    so the re-entry tests assert only the contract-relevant keys.

The revised fault cases target a recoverable durable UNKNOWN after a transient
claim fault. They are an explicit design target rather than a claim about the
current candidate. All observations are scoped to this fake in-memory setup.
"""
from __future__ import annotations

from typing import Any

from comsol_mcp._control_daemon import ControlDaemon
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _assert_queued_pending_envelope,
    _count,
    _dispatch as _dispatch_stage_define,
    _execution_envelope,
    _InFlightScheduler,
    _job_rows,
    _operation_rows,
    _stage_plan,
)
from tests.test_t31_stage_queue_cancel_20261112 import (
    _DefineLedger,
    _durable_snapshot,
    _prepare_define_receiver,
)
from tests.test_t31_stage_run_nested_unknown_20261111 import (
    _attempt_rows,
    _dispatch_stage_run,
    _new_receiver,
    InjectedSolveResponseLoss,
)


# ---------------------------------------------------------------------------
# Synthetic, memory-only injected failures (labels are deliberate: they must
# never be confused with any real historical UNKNOWN).
# ---------------------------------------------------------------------------

class InjectedStageCasPreCommitFailure(RuntimeError):
    """Raised BEFORE the real ``transition_status`` CAS is entered."""


class InjectedStageCasPostCommitFailure(RuntimeError):
    """Raised AFTER the real CAS committed QUEUED -> RUNNING."""


# ---------------------------------------------------------------------------
# Reversible store.transition_status wrapper.  Every non-hook path delegates
# to the REAL bound CAS method; nothing here re-implements the CAS.
# ---------------------------------------------------------------------------

_PROBE_PASSTHROUGH = object()


class _TransitionStatusProbe:
    """Instance-attribute wrapper around ONE store's transition_status.

    ``on_first_call(args, kwargs, original, records)`` may:
      * raise (exception injected before or after it called ``original``),
      * return a value (short-circuit, unused here),
      * return ``_PROBE_PASSTHROUGH`` to let the REAL CAS run afterwards.
    """

    def __init__(self, store: Any, on_first_call: Any = None) -> None:
        self.store = store
        self.original = store.transition_status
        self.on_first_call = on_first_call
        self.invocations = 0
        self.cas_results: list[Any] = []
        self.records: dict[str, Any] = {}
        store.transition_status = self._wrapper

    def _wrapper(self, *args: Any, **kwargs: Any) -> Any:
        self.invocations += 1
        if self.on_first_call is not None and self.invocations == 1:
            outcome = self.on_first_call(args, kwargs, self.original, self.records)
            if outcome is not _PROBE_PASSTHROUGH:
                return outcome
        result = self.original(*args, **kwargs)
        self.cas_results.append(result)
        return result

    def restore(self) -> None:
        self.store.transition_status = self.original


def _job_events(store: Any, job_id: str) -> list[str]:
    return [
        row["event"]
        for row in store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    ]


def _operation_request_hash(store: Any, operation: str) -> str:
    row = store.db.execute(
        "SELECT request_hash FROM operations WHERE operation=?", (operation,)
    ).fetchone()
    assert row is not None, f"no durable {operation} operation row"
    return row["request_hash"]


def _assert_claim_unknown_envelope(
    envelope: dict[str, Any], *, job_id: str, operation_id: str, request_id: str,
    idempotency_key: str, request_hash: str, injected_type: str,
) -> None:
    """The claim's own failure envelope: honest UNKNOWN, nothing invented."""
    assert isinstance(envelope, dict) and envelope["success"] is False, envelope
    error = envelope["error"]
    assert error["code"] == "EXECUTION_STATE_UNKNOWN", envelope
    # The claim must NOT fabricate a retryable admission.
    assert error.get("safe_retry") is False, (
        f"claim fabricated safe_retry={error.get('safe_retry')!r}: {envelope!r}"
    )
    assert error.get("type") == injected_type, (
        f"expected the injected failure type {injected_type!r}, got {error!r}"
    )
    data = envelope["data"]
    assert data["job_id"] == job_id, envelope
    # engine_dispatched stays unknown; it may not be dressed up as False.
    assert data["engine_dispatched"] is None, (
        f"claim fabricated engine_dispatched={data['engine_dispatched']!r}: {envelope!r}"
    )
    assert envelope["execution"] == {
        "request_id": request_id, "operation_id": operation_id,
        "request_hash": request_hash, "idempotency_key": idempotency_key,
        "job_id": job_id,
    }, envelope


class _InstrumentedDefineLedger(_DefineLedger):
    """The reused fake ledger with a state-read counter (reachability probe)."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.state_reads = 0

    def _state_for(self, model_ref: Any) -> Any:
        self.state_reads += 1
        return super()._state_for(model_ref)


# ===========================================================================
# PART 1 — stage_define: real _cancel_job inserted before the claim CAS
# ===========================================================================

def test_stage_define_cancel_wins_before_claim_cas_returns_authoritative_cancel_result():
    """QUEUED_ABORT wins the claim CAS; the callback must return the ORIGINAL
    durable cancel result verbatim with zero plan/index/attempt/backend work.
    """
    receiver, execution = _prepare_define_receiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])
    receiver._default_backend.service.ledger = _InstrumentedDefineLedger(
        execution["session_id"]
    )
    ledger = receiver._default_backend.service.ledger
    probe: _TransitionStatusProbe | None = None

    try:
        first = _dispatch_stage_define(
            receiver, definition=plan,
            execution=_execution_envelope(
                request_id="t31-claimrace-define-1", idempotency_key="t31-claimrace-define-key",
            ),
        )
        assert len(scheduler.calls) == 1, scheduler.calls
        _context, callback = scheduler.calls[0]
        assert getattr(callback, "__name__", "") == "register_definition"
        rows = _operation_rows(store)
        jobs = _job_rows(store)
        assert len(rows) == 1 and rows[0]["status"] == "QUEUED" and rows[0]["result"] is None
        assert len(jobs) == 1 and jobs[0]["status"] == "QUEUED"
        operation_id, job_id, request_hash = (
            rows[0]["operation_id"], jobs[0]["job_id"], rows[0]["request_hash"],
        )
        _assert_queued_pending_envelope(
            first, request_id="t31-claimrace-define-1", job_id=job_id,
            operation_id=operation_id, request_hash=request_hash,
        )
        assert store.get_stage_plan(project_id, model_ref, plan["plan_id"]) is None

        def cancel_before_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            assert args[0] == job_id, (args[0], job_id)
            records["job_at_hook"] = dict(store.job(args[0]))
            records["cancel_response"] = ControlDaemon._cancel_job(receiver, {
                "job_id": args[0],
                "reason": "T31 test-only cancel inserted before the claim CAS",
            })
            return _PROBE_PASSTHROUGH  # then run the REAL CAS

        probe = _TransitionStatusProbe(store, on_first_call=cancel_before_cas)
        callback_return = callback()

        # ---- the real _cancel_job fired exactly once, strictly pre-CAS ----
        assert probe.invocations == 1, probe.invocations
        hook_job = probe.records["job_at_hook"]
        assert hook_job["status"] == "QUEUED", hook_job
        cancel = probe.records["cancel_response"]
        assert cancel["success"] is True, cancel
        assert cancel["data"]["mode"] == "QUEUED_ABORT", cancel
        assert cancel["data"]["cancel_accepted"] is True, cancel
        assert cancel["data"]["engine_dispatched"] is False, cancel
        assert cancel["data"]["engine_stopped"] is False, cancel
        assert store.job(job_id)["status"] == "CANCELLED"
        assert len(probe.cas_results) == 1
        claimed, status, job_view = probe.cas_results[0]
        assert claimed is False and status == "CANCELLED", probe.cas_results[0]

        # ---- FENCE: the rejected CAS must surface the durable cancel result
        #      through the job view (the claim's authoritative-result branch).
        durable_cancel = store.get_operation(operation_id)["result"]
        assert durable_cancel is not None
        assert durable_cancel["error"]["code"] == "OPERATION_CANCELLED", durable_cancel
        assert durable_cancel["data"]["mode"] == "QUEUED_ABORT", durable_cancel
        assert (job_view or {}).get("result") == durable_cancel, (
            "DEFECT FENCE: the rejected transition_status job view did not "
            f"surface the durable cancel result (observed {job_view!r}); the "
            "claim cannot return the authoritative outcome without it"
        )
        # ---- FENCE: callback returns the ORIGINAL authoritative cancel result
        assert callback_return == durable_cancel, (
            "DEFECT FENCE: the late register_definition callback did not "
            f"return the durable QUEUED_ABORT result verbatim; returned "
            f"{callback_return!r} vs durable {durable_cancel!r}"
        )
        assert callback_return["success"] is False

        # ---- zero stage side effects, pair untouched after the cancel ----
        assert store.get_stage_plan(project_id, model_ref, plan["plan_id"]) is None
        assert store.get_stage_id_index(project_id, model_ref, "preheat") is None
        assert store.get_stage_id_index(project_id, model_ref, "cooldown") is None
        assert _count(store, "stage_attempts") == 0
        assert ledger.state_reads == 0, "cancelled callback touched the fake ledger"
        after = _durable_snapshot(store, job_id, operation_id)
        assert after["job_status"] == "CANCELLED"
        assert after["operation_status"] == "CANCELLED"
        assert len(scheduler.calls) == 1
        # CAS rejection wrote no events; finish was never reached.
        assert _job_events(store, job_id) == ["CANCELLED"], _job_events(store, job_id)
    finally:
        if probe is not None:
            probe.restore()
        store.close()


def test_stage_define_claim_cas_control_commits_running_and_registers():
    """Positive control (no interference): the real claim CAS commits
    QUEUED -> RUNNING and the real callback registers the plan normally.
    """
    receiver, execution = _prepare_define_receiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])
    receiver._default_backend.service.ledger = _InstrumentedDefineLedger(
        execution["session_id"]
    )
    ledger = receiver._default_backend.service.ledger

    try:
        first = _dispatch_stage_define(
            receiver, definition=plan,
            execution=_execution_envelope(
                request_id="t31-claimctl-define-1", idempotency_key="t31-claimctl-define-key",
            ),
        )
        assert len(scheduler.calls) == 1
        _context, callback = scheduler.calls[0]
        operation_id = first["execution"]["operation_id"]
        job_id = first["execution"]["job_id"]

        result = callback()
        assert result["success"] is True and result["data"]["registration"] == "CREATED", result
        assert ledger.state_reads >= 1, "real callback never touched the ledger gate"
        saved = store.get_stage_plan(project_id, model_ref, plan["plan_id"])
        assert saved is not None and saved["definition"] == plan
        assert store.job(job_id)["status"] == "SUCCEEDED"
        assert store.get_operation(operation_id)["status"] == "SUCCEEDED"
        # The real _finish appends SUCCEEDED after the single RUNNING claim.
        assert _job_events(store, job_id) == ["RUNNING", "SUCCEEDED"], _job_events(store, job_id)
        assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1
    finally:
        store.close()


# ===========================================================================
# PART 2 — stage_run: same race on the real preflight callback
# ===========================================================================

def test_stage_run_cancel_wins_before_claim_cas_returns_authoritative_cancel_result():
    """Same race on ``preflight_and_record``: QUEUED_ABORT wins the claim CAS;
    no stage attempt may be bound and the fake backend may see zero calls.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]
    receiver.session_scheduler = _InFlightScheduler()
    scheduler = receiver.session_scheduler
    probe: _TransitionStatusProbe | None = None

    try:
        first = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id="t31-claimrace-run-1", idempotency_key="t31-claimrace-run-key",
        )
        assert first["success"] is True and first["data"]["status"] == "QUEUED", first
        assert first["data"]["rpc_wait_expired"] is True, first
        assert len(scheduler.calls) == 1
        _context, callback = scheduler.calls[0]
        assert getattr(callback, "__name__", "") == "preflight_and_record"
        assert receiver.backend.calls == []
        assert _attempt_rows(store, model_ref) == []
        job_id = first["execution"]["job_id"]
        operation_id = first["execution"]["operation_id"]

        def cancel_before_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            assert args[0] == job_id, (args[0], job_id)
            records["job_at_hook"] = dict(store.job(args[0]))
            records["cancel_response"] = ControlDaemon._cancel_job(receiver, {
                "job_id": args[0],
                "reason": "T31 test-only cancel inserted before the stage_run claim CAS",
            })
            return _PROBE_PASSTHROUGH

        probe = _TransitionStatusProbe(store, on_first_call=cancel_before_cas)
        callback_return = callback()

        assert probe.invocations == 1
        assert probe.records["job_at_hook"]["status"] == "QUEUED"
        cancel = probe.records["cancel_response"]
        assert cancel["success"] is True and cancel["data"]["mode"] == "QUEUED_ABORT", cancel
        claimed, status, job_view = probe.cas_results[0]
        assert claimed is False and status == "CANCELLED", probe.cas_results[0]

        durable_cancel = store.get_operation(operation_id)["result"]
        assert durable_cancel is not None
        assert durable_cancel["data"]["mode"] == "QUEUED_ABORT", durable_cancel
        # ---- FENCES: authoritative-result contract ----
        assert (job_view or {}).get("result") == durable_cancel, (
            f"DEFECT FENCE: rejected CAS job view hid the durable cancel result: {job_view!r}"
        )
        assert callback_return == durable_cancel, (
            "DEFECT FENCE: the late preflight_and_record callback did not return "
            f"the durable QUEUED_ABORT result verbatim; got {callback_return!r}"
        )
        assert callback_return["success"] is False

        # ---- zero admission/dispatch side effects ----
        assert _attempt_rows(store, model_ref) == []
        assert receiver.backend.calls == []
        assert store.job(job_id)["status"] == "CANCELLED"
        assert _job_events(store, job_id) == ["CANCELLED"], _job_events(store, job_id)
    finally:
        if probe is not None:
            probe.restore()
        store.close()


def test_stage_run_claim_cas_control_reaches_attempt_binding():
    """Positive control (no interference): the real claim CAS commits RUNNING
    and the real preflight binds one attempt and reaches the fake backend
    seam. The resulting UNKNOWN is a SYNTHETIC state of this fresh store.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]

    try:
        result = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id="t31-claimctl-run-1", idempotency_key="t31-claimctl-run-key",
        )
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        attempts = _attempt_rows(store, model_ref)
        assert len(attempts) == 1, attempts
        job_id = _job_rows(store)[0]["job_id"]
        # The fake backend can emit worker_request events before _finish.
        events = _job_events(store, job_id)
        assert events and events[0] == "RUNNING" and events.count("RUNNING") == 1 and events[-1] == "UNKNOWN", events
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["type"] == InjectedSolveResponseLoss.__name__
        assert store.job(job_id)["status"] == "UNKNOWN"
    finally:
        store.close()


# ===========================================================================
# PART 3 — controlled re-entry of the SAME callback while the first is RUNNING
# ===========================================================================

def test_stage_define_callback_reentry_at_running_must_not_register_or_claim_dispatch():
    """The wrapper commits the outer claim CAS (job durably RUNNING), then
    re-invokes the SAME captured callback before the outer body starts.  The
    second pass must be rejected at the CAS, must not register anything, and
    must not claim dispatch.
    """
    receiver, execution = _prepare_define_receiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])
    receiver._default_backend.service.ledger = _InstrumentedDefineLedger(
        execution["session_id"]
    )
    ledger = receiver._default_backend.service.ledger
    probe: _TransitionStatusProbe | None = None

    try:
        first = _dispatch_stage_define(
            receiver, definition=plan,
            execution=_execution_envelope(
                request_id="t31-reentry-define-1", idempotency_key="t31-reentry-define-key",
            ),
        )
        assert len(scheduler.calls) == 1
        _context, callback = scheduler.calls[0]
        assert getattr(callback, "__name__", "") == "register_definition"
        job_id = first["execution"]["job_id"]
        operation_id = first["execution"]["operation_id"]

        def reenter_after_outer_cas(args: Any, kwargs: Any, original: Any,
                                    records: dict[str, Any]) -> Any:
            result = original(*args, **kwargs)          # outer claim commits RUNNING
            records["outer_cas"] = result
            records["plan_before_inner"] = store.get_stage_plan(
                project_id, model_ref, plan["plan_id"]
            )
            records["events_before_inner"] = _job_events(store, job_id)
            records["inner_return"] = callback()        # SAME callback object, re-entered
            records["events_after_inner"] = _job_events(store, job_id)
            records["plan_after_inner"] = store.get_stage_plan(
                project_id, model_ref, plan["plan_id"]
            )
            records["attempts_after_inner"] = _count(store, "stage_attempts")
            records["ledger_reads_after_inner"] = ledger.state_reads
            records["scheduler_calls_during"] = len(scheduler.calls)
            return result

        probe = _TransitionStatusProbe(store, on_first_call=reenter_after_outer_cas)
        outer_return = callback()

        assert probe.invocations == 2, probe.invocations
        claimed, status, _job_view = probe.records["outer_cas"]
        assert claimed is True and status == "RUNNING", probe.records["outer_cas"]

        # The re-entered pass lost the CAS: second claim rejected on RUNNING.
        inner_claimed, inner_status, inner_view = probe.cas_results[0]
        assert inner_claimed is False and inner_status == "RUNNING", probe.cas_results[0]
        assert (inner_view or {}).get("result") is None, inner_view

        rec = probe.records
        assert rec["plan_before_inner"] is None and rec["plan_after_inner"] is None
        assert rec["events_before_inner"] == rec["events_after_inner"] == ["RUNNING"]
        assert rec["attempts_after_inner"] == 0
        assert rec["ledger_reads_after_inner"] == 0, "re-entry touched the fake ledger"
        assert rec["scheduler_calls_during"] == 1, "re-entry re-entered the scheduler"
        inner = rec["inner_return"]
        assert inner["success"] is True, inner
        assert inner["data"]["job_id"] == job_id, inner
        # Not a registration, and no fabricated dispatch claim.
        assert inner["data"].get("registration") not in ("CREATED", "IDEMPOTENT_EXISTING"), inner
        assert inner["data"]["engine_dispatched"] is not True, inner

        # The OUTER pass still owns the work and finishes exactly once.
        assert outer_return["success"] is True
        assert outer_return["data"]["registration"] == "CREATED", outer_return
        saved = store.get_stage_plan(project_id, model_ref, plan["plan_id"])
        assert saved is not None
        assert store.job(job_id)["status"] == "SUCCEEDED"
        assert store.get_operation(operation_id)["status"] == "SUCCEEDED"
        assert len(_operation_rows(store)) == 1
        # The rejected inner CAS wrote no event; outer finish adds SUCCEEDED.
        assert _job_events(store, job_id) == ["RUNNING", "SUCCEEDED"], _job_events(store, job_id)
    finally:
        if probe is not None:
            probe.restore()
        store.close()


def test_stage_run_callback_reentry_at_running_must_not_admit_or_claim_dispatch():
    """Same controlled re-entry on ``preflight_and_record``: while the outer
    claim holds durable RUNNING, the second pass must not bind an attempt,
    must not reach the fake backend, and must not claim dispatch.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]
    receiver.session_scheduler = _InFlightScheduler()
    scheduler = receiver.session_scheduler
    probe: _TransitionStatusProbe | None = None

    try:
        first = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id="t31-reentry-run-1", idempotency_key="t31-reentry-run-key",
        )
        assert len(scheduler.calls) == 1
        _context, callback = scheduler.calls[0]
        assert getattr(callback, "__name__", "") == "preflight_and_record"
        job_id = first["execution"]["job_id"]

        def reenter_after_outer_cas(args: Any, kwargs: Any, original: Any,
                                    records: dict[str, Any]) -> Any:
            result = original(*args, **kwargs)
            records["outer_cas"] = result
            records["attempts_before_inner"] = len(_attempt_rows(store, model_ref))
            records["backend_calls_before_inner"] = len(receiver.backend.calls)
            records["events_before_inner"] = _job_events(store, job_id)
            records["inner_return"] = callback()
            records["events_after_inner"] = _job_events(store, job_id)
            records["attempts_after_inner"] = len(_attempt_rows(store, model_ref))
            records["backend_calls_after_inner"] = len(receiver.backend.calls)
            return result

        probe = _TransitionStatusProbe(store, on_first_call=reenter_after_outer_cas)
        outer_return = callback()

        assert probe.invocations == 2
        claimed, status, _view = probe.records["outer_cas"]
        assert claimed is True and status == "RUNNING"
        inner_claimed, inner_status, _inner_view = probe.cas_results[0]
        assert inner_claimed is False and inner_status == "RUNNING", probe.cas_results[0]

        rec = probe.records
        assert rec["attempts_before_inner"] == 0 and rec["attempts_after_inner"] == 0
        assert rec["events_before_inner"] == rec["events_after_inner"] == ["RUNNING"]
        assert rec["backend_calls_before_inner"] == 0 and rec["backend_calls_after_inner"] == 0
        inner = rec["inner_return"]
        assert inner["success"] is True, inner
        assert inner["data"]["job_id"] == job_id, inner
        assert inner["data"].get("stage_attempt_id") is None, inner
        assert inner["data"]["engine_dispatched"] is not True, inner

        # Outer pass owns the only attempt; the synthetic UNKNOWN is memory-only.
        attempts = _attempt_rows(store, model_ref)
        assert len(attempts) == 1, attempts
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        assert outer_return["success"] is False
        assert store.job(job_id)["status"] == "UNKNOWN"
        events = _job_events(store, job_id)
        assert events and events[0] == "RUNNING" and events.count("RUNNING") == 1 and events[-1] == "UNKNOWN", events
        assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
    finally:
        if probe is not None:
            probe.restore()
        store.close()


# ===========================================================================
# PART 4 — injected exceptions around the real CAS (define)
# ===========================================================================

def _define_claim_exception_preamble(request_id: str, idempotency_key: str) -> tuple:
    """Fresh define receiver admitted to QUEUED with its captured callback."""
    receiver, execution = _prepare_define_receiver()
    # The reused inert receiver accidentally binds this production staticmethod.
    # Assigning the function on the instance restores the real call signature.
    receiver._error = ControlDaemon._error
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])
    receiver._default_backend.service.ledger = _InstrumentedDefineLedger(
        execution["session_id"]
    )
    ledger = receiver._default_backend.service.ledger
    first = _dispatch_stage_define(
        receiver, definition=plan,
        execution=_execution_envelope(request_id=request_id, idempotency_key=idempotency_key),
    )
    assert len(scheduler.calls) == 1
    _context, callback = scheduler.calls[0]
    assert getattr(callback, "__name__", "") == "register_definition"
    rows = _operation_rows(store)
    return receiver, store, plan, project_id, model_ref, ledger, callback, (
        rows[0]["operation_id"], rows[0]["request_hash"], first["execution"]["job_id"],
    )


def test_stage_define_pre_cas_injected_exception_is_unknown_without_side_effects():
    """A transient pre-CAS failure should persist UNKNOWN without side effects.
    """
    (receiver, store, plan, project_id, model_ref, ledger, callback,
     (operation_id, request_hash, job_id)) = _define_claim_exception_preamble(
        "t31-preexc-define-1", "t31-preexc-define-key",
    )
    probe: _TransitionStatusProbe | None = None
    try:
        def raise_before_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            records["job_at_hook"] = dict(store.job(args[0]))
            records["raised_before_original"] = True
            raise InjectedStageCasPreCommitFailure(
                "T31 test-only failure injected before the claim CAS (synthetic, memory-only)"
            )

        probe = _TransitionStatusProbe(store, on_first_call=raise_before_cas)
        envelope = callback()

        assert probe.invocations >= 1 and probe.records["raised_before_original"] is True
        assert probe.records["job_at_hook"]["status"] == "QUEUED"
        _assert_claim_unknown_envelope(
            envelope, job_id=job_id, operation_id=operation_id,
            request_id="t31-preexc-define-1", idempotency_key="t31-preexc-define-key",
            request_hash=request_hash,
            injected_type=InjectedStageCasPreCommitFailure.__name__,
        )
        assert store.get_stage_plan(project_id, model_ref, plan["plan_id"]) is None
        assert _count(store, "stage_attempts") == 0
        assert ledger.state_reads == 0
        assert "RUNNING" not in _job_events(store, job_id)
        # Original job identity intact.
        assert store.operation_job(operation_id)["job_id"] == job_id
        operation = store.get_operation(operation_id)
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert operation["status"] == "UNKNOWN" and operation["result"] == envelope
    finally:
        if probe is not None:
            probe.restore()
        store.close()


def test_stage_define_post_cas_injected_exception_records_unknown_same_connection():
    """Exception injected AFTER the real CAS committed QUEUED -> RUNNING:
    the original claim is observed RUNNING, then its fault should be recorded
    UNKNOWN on THIS connection; restart/cross-connection visibility is not attested.
    """
    (receiver, store, plan, project_id, model_ref, ledger, callback,
     (operation_id, request_hash, job_id)) = _define_claim_exception_preamble(
        "t31-postexc-define-1", "t31-postexc-define-key",
    )
    probe: _TransitionStatusProbe | None = None
    try:
        def raise_after_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            result = original(*args, **kwargs)   # REAL CAS, then commit is durable
            records["first_cas"] = result
            if result[0] is not True:
                raise AssertionError(
                    f"FIXTURE PRECONDITION: claim CAS should have claimed, got {result!r}"
                )
            raise InjectedStageCasPostCommitFailure(
                "T31 test-only failure injected after the claim CAS commit (synthetic, memory-only)"
            )

        probe = _TransitionStatusProbe(store, on_first_call=raise_after_cas)
        envelope = callback()

        assert probe.invocations >= 1
        claimed, status, _view = probe.records["first_cas"]
        assert claimed is True and status == "RUNNING"
        _assert_claim_unknown_envelope(
            envelope, job_id=job_id, operation_id=operation_id,
            request_id="t31-postexc-define-1", idempotency_key="t31-postexc-define-key",
            request_hash=request_hash,
            injected_type=InjectedStageCasPostCommitFailure.__name__,
        )
        # No stage-side work happened after the claim.
        assert store.get_stage_plan(project_id, model_ref, plan["plan_id"]) is None
        assert _count(store, "stage_attempts") == 0
        assert ledger.state_reads == 0
        assert store.operation_job(operation_id)["job_id"] == job_id
        events = _job_events(store, job_id)
        assert events and events[0] == "RUNNING" and events.count("RUNNING") == 1, events
        operation = store.get_operation(operation_id)
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert operation["status"] == "UNKNOWN" and operation["result"] == envelope
    finally:
        if probe is not None:
            probe.restore()
        store.close()


# ===========================================================================
# PART 5 — injected exceptions around the real CAS (run)
# ===========================================================================

def _run_claim_exception_preamble(request_id: str, idempotency_key: str) -> tuple:
    """Fresh stage_run receiver admitted to QUEUED with its captured callback."""
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]
    receiver.session_scheduler = _InFlightScheduler()
    scheduler = receiver.session_scheduler
    first = _dispatch_stage_run(
        receiver, ids["predecessor"]["attempt_id"],
        request_id=request_id, idempotency_key=idempotency_key,
    )
    assert len(scheduler.calls) == 1
    _context, callback = scheduler.calls[0]
    assert getattr(callback, "__name__", "") == "preflight_and_record"
    operation_id = first["execution"]["operation_id"]
    job_id = first["execution"]["job_id"]
    request_hash = _operation_request_hash(store, "experiment.stage_run")
    return receiver, store, model_ref, callback, (operation_id, request_hash, job_id)


def test_stage_run_pre_cas_injected_exception_is_unknown_without_side_effects():
    """Pre-CAS fault: persist UNKNOWN with no attempt or fake backend call."""
    receiver, store, model_ref, callback, (operation_id, request_hash, job_id) = (
        _run_claim_exception_preamble("t31-preexc-run-1", "t31-preexc-run-key")
    )
    probe: _TransitionStatusProbe | None = None
    try:
        def raise_before_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            records["job_at_hook"] = dict(store.job(args[0]))
            records["raised_before_original"] = True
            raise InjectedStageCasPreCommitFailure(
                "T31 test-only failure injected before the stage_run claim CAS (memory-only)"
            )

        probe = _TransitionStatusProbe(store, on_first_call=raise_before_cas)
        envelope = callback()

        assert probe.invocations >= 1 and probe.records["raised_before_original"] is True
        assert probe.records["job_at_hook"]["status"] == "QUEUED"
        _assert_claim_unknown_envelope(
            envelope, job_id=job_id, operation_id=operation_id,
            request_id="t31-preexc-run-1", idempotency_key="t31-preexc-run-key",
            request_hash=request_hash,
            injected_type=InjectedStageCasPreCommitFailure.__name__,
        )
        assert _attempt_rows(store, model_ref) == []
        assert receiver.backend.calls == []
        assert "RUNNING" not in _job_events(store, job_id)
        assert store.operation_job(operation_id)["job_id"] == job_id
        operation = store.get_operation(operation_id)
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert operation["status"] == "UNKNOWN" and operation["result"] == envelope
    finally:
        if probe is not None:
            probe.restore()
        store.close()


def test_stage_run_post_cas_injected_exception_records_unknown_same_connection():
    """After-CAS fault: record UNKNOWN on this connection with no attempt.
    """
    receiver, store, model_ref, callback, (operation_id, request_hash, job_id) = (
        _run_claim_exception_preamble("t31-postexc-run-1", "t31-postexc-run-key")
    )
    probe: _TransitionStatusProbe | None = None
    try:
        def raise_after_cas(args: Any, kwargs: Any, original: Any, records: dict[str, Any]) -> Any:
            result = original(*args, **kwargs)
            records["first_cas"] = result
            if result[0] is not True:
                raise AssertionError(
                    f"FIXTURE PRECONDITION: claim CAS should have claimed, got {result!r}"
                )
            raise InjectedStageCasPostCommitFailure(
                "T31 test-only failure injected after the stage_run claim CAS (memory-only)"
            )

        probe = _TransitionStatusProbe(store, on_first_call=raise_after_cas)
        envelope = callback()

        assert probe.invocations >= 1
        claimed, status, _view = probe.records["first_cas"]
        assert claimed is True and status == "RUNNING"
        _assert_claim_unknown_envelope(
            envelope, job_id=job_id, operation_id=operation_id,
            request_id="t31-postexc-run-1", idempotency_key="t31-postexc-run-key",
            request_hash=request_hash,
            injected_type=InjectedStageCasPostCommitFailure.__name__,
        )
        assert _attempt_rows(store, model_ref) == []
        assert receiver.backend.calls == []
        assert store.operation_job(operation_id)["job_id"] == job_id
        events = _job_events(store, job_id)
        assert events and events[0] == "RUNNING" and events.count("RUNNING") == 1, events
        operation = store.get_operation(operation_id)
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert operation["status"] == "UNKNOWN" and operation["result"] == envelope
    finally:
        if probe is not None:
            probe.restore()
        store.close()
