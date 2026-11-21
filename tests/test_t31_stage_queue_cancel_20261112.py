"""T31/U07: queue-cancel vs scheduler-callback atomicity fences for
``experiment.stage_define`` / ``experiment.stage_run``.

Task-owned, offline, WRITTEN BUT NEVER EXECUTED by the author of this file.
Each fence is a target contract, NOT a claim that the current product passes
it.  Source reading (below) shows the callbacks never consult job cancel
state, so the late-execution fences are EXPECTED RED against the current
candidate; the two uncancelled positive controls are expected GREEN and
exist to prove the fixtures actually reach the real callback bodies.

Source anchors actually read in this worktree candidate (line numbers of
``comsol_mcp/_control_daemon.py``):
  * 2602-2840  ``_dispatch_experiment_stage_define``: durable ``begin`` at
    2694-2704, scheduler submit at 2816-2833, pending on FutureTimeout at
    2834-2840.  The captured ``register_definition`` callback (2712-2814)
    re-checks policy/revision/ledger binding but NEVER checks the job's
    durable cancel state before ``register_stage_plan`` (2761) and
    ``_finish`` (2797).
  * 2842-3247  ``_dispatch_experiment_stage_run``: durable ``begin`` at
    2952-2960, ``preflight_and_record`` (2967-3227) calls
    ``store.begin_stage_attempt`` (3008-3013) with no cancel check, then
    ``_execute_admitted_experiment_stage`` (3178); submit at 3229-3238;
    pending at 3244-3246.
  * 3248-3548  ``_execute_admitted_experiment_stage``: solve-phase
    ``worker-dispatch`` evidence + RUNNING/engine_dispatched=True durable
    write at 3432-3461; DISPATCH_INTENT at 3494-3517; the single
    ``backend.invoke`` at 3539-3543.  Nothing on this path writes a
    STARTING/RUNNING *job* row — the jobs row stays QUEUED until ``_finish``.
  * 13269-13383 ``_cancel_job`` (the REAL product method, invoked unbound):
    QUEUED branch -> ``store.cancel_queued`` and mode ``QUEUED_ABORT`` /
    ``engine_dispatched=False`` (13319-13332); non-QUEUED non-force path
    records ``CancelRequested`` intent, keeps the observed status,
    ``engine_stopped=False`` (13352-13383).
Store anchors in ``comsol_mcp/_operation_store.py``:
  * 2125-2185 ``cancel_queued``: CAS on the *job* status only — it knows
    nothing about stage-attempt dispatch witnesses.
  * 1870-1934 ``finish`` terminal immunity: a CANCELLED operation keeps its
    durable cancel result; any late result becomes ``LateResultRecorded``.
  * 4527-4639 ``begin_stage_attempt``: binds an attempt by idempotency key
    with no coupling to the job's cancel state.

What is REAL (product code executed unbound against inert receivers):
  * ``ControlDaemon._dispatch_admitted`` plus the full stage_define /
    stage_run route bodies (validate_call, canonical_request_hash, identity
    gates, submit/pending handling), ``ControlDaemon._cancel_job`` called as
    the real unbound method, and the real ``_pending``/``_finish``/
    ``_error``/``_exception``/``_timeouts``/``_redact_session_worker_event``.
  * A brand-new ``OperationStore(":memory:")`` per test (memory only, no DB
    file on disk) performs every begin/finish/cancel/CAS observation.
  * The captured scheduler callbacks are the real closures produced by the
    product routes (names ``register_definition`` / ``preflight_and_record``)
    and are executed by the tests themselves after the cancel.

What is FAKE (bounded test scaffolding, never an engine/authorization/
native claim):
  * No ControlDaemon instance, thread, network, process, Worker, SSH or
    COMSOL session exists.  Receivers reuse the already-vetted inert
    fixtures from ``tests/test_t31_stage_define_pending_idempotency_20261111``
    and ``tests/test_t31_stage_run_nested_unknown_20261111`` (read and
    imported, not re-implemented): inert authorization stubs (NOT an authz
    proof), fake ledgers/backends, fake project authority.
  * ``_InFlightScheduler.submit`` (pending-idempotency module) only captures
    ``(context, callback)``; its future's ``result()`` raises the same
    ``concurrent.futures.TimeoutError`` the product route catches, so the
    route returns a durable PENDING envelope with the callback still
    un-executed.  The ``_InlineRecordingScheduler`` of the nested-unknown
    module runs callbacks inline.  Both are fake schedulers: NO real
    scheduler concurrency, queueing or ordering is exercised or attested.
  * The stage_run fake backend emits synthetic Worker *event shapes*
    (``wrk-`` ids, ``T31-TEST-ONLY`` handles) that the real
    ``StageWorkerDispatchGate`` accepts; the resulting durable
    ``worker-dispatch`` witness is a FAKE witness, not a real RPC.  The
    UNKNOWN outcome it produces lives ONLY inside this fresh in-memory
    store; it is a synthetic test-only state and is NOT connected to, and
    must never be cited as, any historical/real unknown job or solve.
  * Plan data is synthetic (v1 declaration for define; the nested module's
    synthetic v2 declaration for run with an explicitly synthetic
    predecessor row).  No T19 r3/r4 adapter-diff/pins, no T13 installed /
    cursor / fallback surface, no T14/W13 geometry-membership result, and no
    T21 list result is touched, sought, or implied by this file.

ASSERTION DISCIPLINE (per task contract): all observations are captured
first (probe dicts / durable snapshots); assertions then run in order —
reachability first, durable-cancel authority, and the target-contract fence
LAST, so a failing fence means every later assertion simply did not run and
must not be reported as observed.  Each test closes only its own in-test
memory store in ``finally``; nothing else is cleaned up.

EXPECTED OUTCOME (prediction from source reading, not from execution):
  * ``test_stage_define_cancel_while_queued_blocks_late_callback_registration``
    and ``test_stage_run_cancel_while_queued_blocks_late_callback_admission``
    fences: expected RED — the product callbacks re-register / re-admit
    without consulting the CANCELLED job.  Their durable cancel-authority
    assertions are backed by the real ``finish`` terminal immunity and are
    expected GREEN.
  * ``test_cancel_after_fake_dispatch_witness_must_not_claim_queued_abort``
    fence: expected RED if the jobs row is still QUEUED at mid-invoke cancel
    time (the source read shows no job RUNNING write on this path), because
    ``cancel_queued`` CASes only the jobs row and would report
    ``QUEUED_ABORT``/``engine_dispatched=False`` while the stage attempt
    durably holds a dispatch witness.
  * The two uncancelled positive controls: expected GREEN — they prove the
    captured callbacks really do register the plan / really do reach the
    fake backend through the real route.
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace
from typing import Any

from comsol_mcp._control_daemon import ControlDaemon
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _assert_queued_pending_envelope,
    _count,
    _execution_envelope,
    _InFlightScheduler,
    _job_rows,
    _operation_rows,
    _StageDefineReceiver,
    _stage_plan,
)
from tests.test_t31_stage_run_nested_unknown_20261111 import (
    _attempt_rows,
    _dispatch_evidence,
    _dispatch_stage_run,
    _FakeBackend,
    _job_event_rows,
    _new_receiver,
    _operation_row,
    FAKE_MODEL_HANDLE,
    FAKE_STUDY_HANDLE,
    InjectedSolveResponseLoss,
    MODEL_REF,
    PROJECT_ID,
    STUDY_TAG,
    WORKER_GENERATION,
)

# Alias the pending-idempotency helper so the intent reads unambiguously here.
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _dispatch as _dispatch_stage_define,
)


# ---------------------------------------------------------------------------
# Shared honest helpers (durable snapshots only; no mutation)
# ---------------------------------------------------------------------------

def _durable_snapshot(store, job_id: str, operation_id: str) -> dict[str, Any]:
    """Plain durable observation of the job/operation pair."""
    job = store.job(job_id)
    operation = store.get_operation(operation_id)
    return {
        "job_status": job["status"],
        "job_result": job["result"],
        "operation_status": operation["status"],
        "operation_result": operation["result"],
    }


# ===========================================================================
# PART 1 — stage_define: QUEUED_ABORT before the captured callback runs
# ===========================================================================

class _DefineLedger:
    """Minimal fake managed ledger surface (revision 0, clean, idle)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.state = SimpleNamespace(active_operation_id=None, dirty=False, revision=0)

    def _state_for(self, model_ref: Any) -> SimpleNamespace:
        assert model_ref is not None
        return self.state


class _DefineBackend:
    """Fake backend supplying only the ledger + revision-key surface."""

    def __init__(self, session_id: str) -> None:
        self.service = SimpleNamespace(ledger=_DefineLedger(session_id))

    def _model_project_key(self, observed: dict[str, Any]) -> str:
        assert isinstance(observed, dict) and observed
        return "t31-queue-cancel-define-model-key"


def _prepare_define_receiver() -> tuple[Any, dict[str, Any]]:
    """Fresh memory-only stage_define receiver with the capturing scheduler."""
    receiver = _StageDefineReceiver()
    execution = _execution_envelope(
        request_id="t31-qc-define-first", idempotency_key="t31-qc-define-key",
    )
    receiver._default_backend = _DefineBackend(execution["session_id"])
    # Lazy receiver: the real route falls back to _default_backend when the
    # session context is None (context=None path at _control_daemon.py:2717).
    receiver._execution_session_context = lambda _execution: None
    receiver.store.put_metadata("revisions", "t31-queue-cancel-define-model-key", {
        "model_ref": dict(execution["model_ref"]),
        "project_id": execution["project_id"],
        "attribution": "PROJECT_BOUND",
        "revision": 0,
        "dirty": False,
    })
    return receiver, execution


def test_stage_define_cancel_while_queued_blocks_late_callback_registration():
    """TARGET CONTRACT (expected RED): cancel the QUEUED define job first,
    only then run the captured ``register_definition``; the plan/index must
    not be (re)registered and the durable cancel result must stay
    authoritative.
    """
    receiver, execution = _prepare_define_receiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])

    try:
        # ---- real route, capturing scheduler: pending, nothing executed ----
        first = _dispatch_stage_define(receiver, definition=plan, execution=execution)
        assert len(scheduler.calls) == 1, scheduler.calls
        context, callback = scheduler.calls[0]
        assert context is None
        assert callable(callback) and getattr(callback, "__name__", "") == "register_definition"
        assert scheduler.executed == []
        rows = _operation_rows(store)
        jobs = _job_rows(store)
        assert len(rows) == 1 and rows[0]["status"] == "QUEUED" and rows[0]["result"] is None
        assert len(jobs) == 1 and jobs[0]["status"] == "QUEUED"
        operation_id = rows[0]["operation_id"]
        job_id = jobs[0]["job_id"]
        _assert_queued_pending_envelope(
            first, request_id="t31-qc-define-first", job_id=job_id,
            operation_id=operation_id, request_hash=rows[0]["request_hash"],
        )
        # Fixture sanity before any cancel: nothing registered yet.
        assert store.get_stage_plan(project_id, model_ref, plan["plan_id"]) is None

        # ---- the REAL product _cancel_job, unbound, before any callback ----
        cancel = ControlDaemon._cancel_job(receiver, {
            "job_id": job_id, "reason": "T31 test-only queued cancel before callback",
        })
        assert cancel["success"] is True, cancel
        assert cancel["data"]["mode"] == "QUEUED_ABORT", cancel
        assert cancel["data"]["cancel_accepted"] is True, cancel
        assert cancel["data"]["engine_dispatched"] is False, cancel
        assert cancel["data"]["engine_stopped"] is False, cancel
        assert store.job(job_id)["status"] == "CANCELLED"
        cancel_snapshot = _durable_snapshot(store, job_id, operation_id)
        assert cancel_snapshot["operation_status"] == "CANCELLED"
        assert (cancel_snapshot["operation_result"] or {}).get("error", {}).get("code") \
            == "OPERATION_CANCELLED"

        # ---- only NOW run the real captured callback ----
        callback_return = callback()

        # ---- durable cancel authority survives the late callback (this part
        #      is backed by the real finish terminal immunity, store:1899) ----
        after = _durable_snapshot(store, job_id, operation_id)
        assert after == cancel_snapshot, (
            "the late register_definition callback mutated the durable cancelled "
            f"job/operation pair: before={cancel_snapshot} after={after}"
        )
        assert len(scheduler.calls) == 1 and scheduler.executed == []
        assert _count(store, "stage_attempts") == 0

        # ---- TARGET-CONTRACT FENCES (last; if these fail, later asserts did
        #      not run and must not be reported as observed) ----
        late_plan = store.get_stage_plan(project_id, model_ref, plan["plan_id"])
        assert late_plan is None, (
            "DEFECT FENCE: the captured register_definition callback registered "
            "a stage plan AFTER the job was durably CANCELLED (QUEUED_ABORT); "
            "the callback never consults the job's cancel state "
            "(_control_daemon.py:2712-2761). No new plan/index may appear."
        )
        assert store.get_stage_id_index(project_id, model_ref, "preheat") is None, (
            "DEFECT FENCE: stage-id index gained 'preheat' after QUEUED_ABORT"
        )
        assert store.get_stage_id_index(project_id, model_ref, "cooldown") is None, (
            "DEFECT FENCE: stage-id index gained 'cooldown' after QUEUED_ABORT"
        )
        assert not (callback_return.get("success") is True
                    and after["job_status"] == "CANCELLED"), (
            "DEFECT FENCE: the late callback returned a success envelope "
            f"{callback_return!r} while the durable job is CANCELLED; the "
            "cancel result must remain the authoritative outcome"
        )
    finally:
        store.close()


def test_stage_define_control_uncancelled_callback_registers_plan():
    """Positive control: the SAME fixture, never cancelled.  Running the real
    captured callback really does register the plan/index (fixture
    reachability proof for the RED fence above).
    """
    receiver, execution = _prepare_define_receiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()
    project_id = execution["project_id"]
    model_ref = dict(execution["model_ref"])

    try:
        first = _dispatch_stage_define(receiver, definition=plan, execution=execution)
        assert first["success"] is True and first["data"]["status"] == "QUEUED", first
        assert len(scheduler.calls) == 1
        context, callback = scheduler.calls[0]
        assert getattr(callback, "__name__", "") == "register_definition"
        operation_id = first["execution"]["operation_id"]
        job_id = first["execution"]["job_id"]

        result = callback()
        assert result["success"] is True, result
        assert result["data"]["registration"] == "CREATED", result
        assert result["data"]["worker_rpc_performed"] is False
        assert result["data"]["solve_started"] is False
        saved = store.get_stage_plan(project_id, model_ref, plan["plan_id"])
        assert saved is not None and saved["definition"] == plan
        assert store.get_stage_id_index(project_id, model_ref, "preheat")["plan_id"] \
            == plan["plan_id"]
        assert store.get_stage_id_index(project_id, model_ref, "cooldown")["plan_id"] \
            == plan["plan_id"]
        assert store.job(job_id)["status"] == "SUCCEEDED"
        assert store.get_operation(operation_id)["status"] == "SUCCEEDED"
        assert len(scheduler.calls) == 1
    finally:
        store.close()


# ===========================================================================
# PART 2 — stage_run: QUEUED_ABORT before the captured preflight callback
# ===========================================================================

def test_stage_run_cancel_while_queued_blocks_late_callback_admission():
    """TARGET CONTRACT (expected RED): with the capturing scheduler, cancel
    the QUEUED stage_run job first (QUEUED_ABORT), then run the captured
    ``preflight_and_record``; it must not create a stage attempt and the
    fake backend must show zero invoke calls.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]
    # Swap the inline fake for the capturing fake: the callback stays
    # un-executed until this test itself runs it, post-cancel.
    receiver.session_scheduler = _InFlightScheduler()
    scheduler = receiver.session_scheduler

    try:
        first = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                    request_id="t31-qc-run-1",
                                    idempotency_key="t31-qc-run-key")
        assert first["success"] is True and first["data"]["status"] == "QUEUED", first
        assert first["data"]["rpc_wait_expired"] is True, first
        assert first["data"]["engine_dispatched"] is None, first
        assert first["data"]["stage_attempt_id"] is None, first
        assert len(scheduler.calls) == 1, scheduler.calls
        context, callback = scheduler.calls[0]
        assert context.backend is receiver.backend
        assert getattr(callback, "__name__", "") == "preflight_and_record"
        assert receiver.backend.calls == []
        assert _attempt_rows(store, model_ref) == []

        job_id = first["execution"]["job_id"]
        operation_id = first["execution"]["operation_id"]
        cancel = ControlDaemon._cancel_job(receiver, {
            "job_id": job_id, "reason": "T31 test-only queued cancel before stage_run callback",
        })
        assert cancel["success"] is True and cancel["data"]["mode"] == "QUEUED_ABORT", cancel
        cancel_snapshot = _durable_snapshot(store, job_id, operation_id)
        assert cancel_snapshot["job_status"] == "CANCELLED"

        # ---- only NOW run the real captured callback ----
        callback()

        # ---- durable cancel authority survives (finish terminal immunity) ----
        after = _durable_snapshot(store, job_id, operation_id)
        assert after == cancel_snapshot, (
            f"the late preflight callback mutated the durable cancelled pair: "
            f"before={cancel_snapshot} after={after}"
        )
        assert len(scheduler.calls) == 1

        # ---- TARGET-CONTRACT FENCES (last) ----
        attempt_rows = _attempt_rows(store, model_ref)
        assert attempt_rows == [], (
            "DEFECT FENCE: the captured preflight_and_record callback created a "
            f"durable stage attempt AFTER the job was CANCELLED via QUEUED_ABORT "
            f"(observed rows: {[{r['attempt_id']: r['status']} for r in attempt_rows]}); "
            "begin_stage_attempt at _control_daemon.py:3008 has no cancel guard"
        )
        assert receiver.backend.calls == [], (
            "DEFECT FENCE: after QUEUED_ABORT the late callback reached the fake "
            f"backend {receiver.backend.calls!r}; zero fake-backend invocations are "
            "allowed once the queued operation was aborted"
        )
    finally:
        store.close()


def test_stage_run_control_uncancelled_callback_reaches_fake_backend():
    """Positive control: uncancelled inline path — the real callback reaches
    the fake backend (exactly one fake solve), and the whole UNKNOWN outcome
    is synthetic, confined to this fresh in-memory store, and must never be
    associated with any historical/real unknown job.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]

    try:
        result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                     request_id="t31-qc-run-control-1",
                                     idempotency_key="t31-qc-run-control-key")
        # Fixture-reachability: the REAL callback reached the fake backend.
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        assert receiver.backend.calls[0]["study_tag"] == STUDY_TAG
        assert len(receiver.session_scheduler.calls) == 1
        solve_marker = receiver.backend.solve_marker
        assert solve_marker is not None, "fake backend never saw the solve marker"
        attempt_id = solve_marker["attempt_id"]
        durable = store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
        assert durable is not None
        # FAKE dispatch witness + synthetic interruption, memory-only.
        assert durable["engine_dispatched"] is True, durable
        assert len(_dispatch_evidence(durable)) == 1, durable["evidence"]
        # The synthetic UNKNOWN belongs only to this fresh store; no
        # historical job exists here to relate it to.
        assert durable["status"] == "UNKNOWN" and result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["type"] == InjectedSolveResponseLoss.__name__
        assert result["error"]["safe_retry"] is False
        assert store.job(_job_id_of(store))["status"] == "UNKNOWN"
        assert len(_attempt_rows(store, model_ref)) == 1
        assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1  # only the dispatched stage_run op; predecessor seeding touches only stage_attempts
    finally:
        store.close()


def _job_id_of(store) -> str:
    job = store.operation_job(_operation_row(store)["operation_id"])
    assert job is not None
    return job["job_id"]


# ===========================================================================
# PART 3 — cancel inserted AFTER a fake dispatch witness, before invoke ends
# ===========================================================================

class _CancelProbeBackend(_FakeBackend):
    """Fake backend that inserts the REAL unbound ``_cancel_job`` mid-invoke.

    Sequence inside ``invoke``: emit the fake Study read pair, emit the fake
    ``run`` submission (which the real ``StageWorkerDispatchGate`` turns into
    the durable RUNNING/engine_dispatched=True witness), then capture
    probe observations, call the real ``_cancel_job`` through the injected
    handler, and only afterwards inject the ordinary solve-response loss.
    ``invoke`` therefore never returns normally: the cancel happens strictly
    between the durable dispatch witness and the (never-produced) reply.
    """

    def __init__(self, store, model_ref: dict[str, Any], probe: list[dict[str, Any]]) -> None:
        super().__init__(store, "after_dispatch_clean")
        self.model_ref = dict(model_ref)
        self.probe = probe
        # Set by the test to the receiver-bound real ControlDaemon._cancel_job
        # before dispatch; Any keeps the duck-typed inert-receiver harness.
        self.cancel_handler: Any = None

    def invoke(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any],
               operation_id: str, event_callback: Any) -> dict[str, Any]:
        assert operation == "run_study", operation
        marker = execution.get("_w21_stage_marker")
        assert isinstance(marker, dict) and isinstance(marker.get("attempt_id"), str)
        self.solve_marker = dict(marker)
        self.calls.append({"operation": operation, "study_tag": arguments["study_tag"],
                           "operation_id": operation_id})
        self._emit_call(event_callback, operation_id, "study", FAKE_MODEL_HANDLE, [STUDY_TAG],
                        phase_pair=True,
                        reply_result={"$worker_handle": FAKE_STUDY_HANDLE,
                                      "generation": WORKER_GENERATION, "java_type": "Study"})
        self._emit_call(event_callback, operation_id, "run", FAKE_STUDY_HANDLE, [],
                        phase_pair=False)

        # Fake dispatch witness is durable now; insert the real cancel.
        job_at = self.store.operation_job(operation_id)
        attempt_at = self.store.get_stage_attempt(PROJECT_ID, self.model_ref,
                                                  marker["attempt_id"])
        entry: dict[str, Any] = {
            "attempt_id": marker["attempt_id"],
            "operation_id": operation_id,
            "job_at_cancel": dict(job_at) if job_at is not None else None,
            "attempt_at_cancel": dict(attempt_at) if attempt_at is not None else None,
            "call_chain_control_frames": [
                f"{(frame.filename or '').rsplit('/', 1)[-1]}#{frame.name}"
                for frame in traceback.extract_stack()
                if (frame.filename or '').endswith("_control_daemon.py")
            ],
        }
        assert job_at is not None, "stage solve has no durable job to cancel"
        entry["cancel_response"] = self.cancel_handler({
            "job_id": job_at["job_id"],
            "reason": "T31 test-only cancel inserted after the fake dispatch witness",
        })
        entry["job_after_cancel"] = self.store.operation_job(operation_id)
        self.probe.append(entry)

        self._raise_solve_loss(
            after_fake_dispatch=True,
            note="injected immediately after the inserted real _cancel_job, "
                 "before invoke could return any reply",
        )
        raise AssertionError("unreachable: _raise_solve_loss always raises")


def test_cancel_after_fake_dispatch_witness_must_not_claim_queued_abort():
    """TARGET CONTRACT (expected RED per source reading): with the fake
    backend already holding a durable RUNNING/engine_dispatched=True witness,
    a mid-invoke ``_cancel_job`` must not masquerade as QUEUED_ABORT /
    ``engine_dispatched=False``; the test records the job status and the
    control call chain at the moment of the cancel and accepts a non-force
    cancel intent with ``engine_stopped=False``.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    model_ref = ids["model_ref"]
    probe: list[dict[str, Any]] = []
    backend = _CancelProbeBackend(store, model_ref, probe)
    # Lazy receiver: the real route builds its session context at submit time
    # (``_execution_session_context`` reads ``self.backend`` then), so the
    # swap below is observed by the running route.
    receiver.backend = backend
    backend.cancel_handler = lambda args: ControlDaemon._cancel_job(receiver, args)

    try:
        result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                     request_id="t31-qc-mid-1",
                                     idempotency_key="t31-qc-mid-key")

        # ---- reachability (must hold regardless of the fence outcome) ----
        assert len(probe) == 1, probe
        assert len(backend.calls) == 1, backend.calls
        assert len(backend.fault_hits) == 1, backend.fault_hits
        assert backend.fault_hits[0]["after_fake_dispatch_evidence"] is True
        assert len(backend.emitted_events) == 3, backend.emitted_events
        assert len(receiver.session_scheduler.calls) == 1
        p = probe[0]

        # ---- the earlier stage attempt was RUNNING/engine_dispatched True
        #      at the exact moment of the inserted cancel ----
        attempt_at = p["attempt_at_cancel"]
        assert attempt_at is not None and attempt_at["attempt_id"] == p["attempt_id"]
        assert attempt_at["status"] == "RUNNING" and attempt_at["engine_dispatched"] is True, (
            f"expected RUNNING/True witness at cancel time, observed {attempt_at}"
        )
        assert len(_dispatch_evidence(attempt_at)) == 1, attempt_at["evidence"]

        # ---- recorded job status + control call chain at the moment ----
        job_at = p["job_at_cancel"]
        assert job_at is not None and job_at["status"] in {"QUEUED", "STARTING", "RUNNING"}, job_at
        chain = p["call_chain_control_frames"]
        for expected_frame in ("_execute_admitted_experiment_stage", "preflight_and_record",
                               "_dispatch_experiment_stage_run", "_dispatch_admitted"):
            assert any(expected_frame in frame for frame in chain), (
                f"call chain at cancel time lacks {expected_frame!r}: {chain}"
            )

        cancel = p["cancel_response"]
        assert cancel["success"] is True, cancel
        assert cancel["data"]["job_id"] == job_at["job_id"], cancel
        # Accepted semantics: a non-force cancel intent that never stops an
        # engine.
        assert cancel["data"]["cancel_accepted"] is True, cancel
        assert cancel["data"]["engine_stopped"] is False, cancel

        # ---- durable post-state after the injected solve-response loss ----
        durable = store.get_stage_attempt(PROJECT_ID, model_ref, p["attempt_id"])
        assert durable is not None
        assert durable["engine_dispatched"] is True, (
            "the durable fake dispatch witness must never be cleared "
            f"(observed {durable['status']}/{durable['engine_dispatched']})"
        )
        assert result["success"] is False, result
        final_job = p["job_after_cancel"]
        final_now = store.job(final_job["job_id"])
        assert final_now["status"] in {"UNKNOWN", "CANCELLED"}, final_now

        # ---- TARGET-CONTRACT FENCES (last; if one fails the rest did not
        #      run and must not be reported as observed) ----
        assert cancel["data"].get("mode") != "QUEUED_ABORT", (
            "DEFECT FENCE: _cancel_job reported QUEUED_ABORT for a job whose "
            "stage attempt already carries a durable fake dispatch witness "
            f"(job_at_cancel={job_at['status']}, attempt=RUNNING/engine_dispatched=True, "
            f"version={attempt_at['version']}); cancel_queued at "
            "_operation_store.py:2125 only CASes the jobs row and ignores "
            "attempt witnesses. A non-force cancel intent "
            "(mode UNSUPPORTED_NATIVE_CANCEL) is the accepted behaviour."
        )
        assert cancel["data"].get("engine_dispatched") is not False, (
            "DEFECT FENCE: cancel response asserted engine_dispatched=False "
            f"while the attempt witness says otherwise: {cancel!r}"
        )
        assert cancel["data"].get("status") == job_at["status"], (
            "DEFECT FENCE: cancel response did not honestly carry the job "
            f"status observed at call time (recorded {job_at['status']}, "
            f"reported {cancel['data'].get('status')})"
        )
    finally:
        store.close()
