"""T31/U07: claim-owner races at the stage-callback admission CAS (stage_run).

Task-owned, offline regression tests using fresh memory-only synthetic state.
The author did not execute the original version. The parent preserved and
executed those original bytes, then revised the duplicate-result expectation
and the post-commit case as a proposed durable-UNKNOWN regression. Results for
the two versions are separate and do not establish actual daemon or crash
behavior.

Three fenced scenarios on the real captured ``preflight_and_record`` closure:

  1. ``test_stage_run_same_closure_reentry_claim_cas_fault_keeps_running_
     owner_and_one_dispatch``: the outer REAL CAS wins QUEUED -> RUNNING
     first; before the outer body proceeds, the SAME callback object is
     re-entered manually and its claim CAS raises exactly once.  The losing
     pass must not touch the durable RUNNING state owned by the other
     (outer) invocation, must not fabricate a retryable admission, and must
     cause zero stage side effects; the outer pass then completes with
     exactly ONE (fake) backend dispatch.  This is MANUAL same-closure
     re-entry only — it does not exercise and must not be cited as proof of
     real thread concurrency.  "The loser leaves the winner's RUNNING state
     untouched" is the stated conservative design goal asserted here.

  2. ``test_stage_run_duplicate_claim_cas_fault_cannot_overwrite_recorded_
     unknown_witness``: a SYNTHETIC job of THIS fresh store has already been
     durably recorded UNKNOWN with ``engine_dispatched=True`` (fake witness)
     through the real route; re-invoking the same callback while its claim
     CAS raises once returns either the authoritative recorded envelope or
     an honest UNKNOWN/None envelope, but the durable recorded result, job
     status, and stage-attempt dispatch witness must remain exactly as
     recorded, with no second scheduler admission and no second fake-backend
     call. The labelled positive control is the real
     same-idempotency-key replay returning the recorded envelope verbatim.
     Nothing here reads, writes, cites or replays any historical/real
     UNKNOWN job; this file never even opens a disk-backed store.

  3. ``test_stage_run_post_commit_job_view_failure_remains_reconcilable_
     on_same_key``: a one-shot fault is injected into the ONE internal
     ``self.job(job_id)`` call inside ``OperationStore.transition_status``.
     Source anchors (``comsol_mcp/_operation_store.py`` 2013-2090): the
     success branch COMMITs the jobs/operations RUNNING update and the
     ``event_name`` insert (2057-2085) BEFORE building the returned job view
     via ``updated = self.job(job_id)`` (2086); the not-found and
     status-mismatch branches never call ``self.job`` (they return
     ``self._job(row)`` inline).  ``transition_status`` has no other
     ``self.job`` call site in that window, so arming the fault exactly
     before the first real CAS deterministically targets 2086 (an armed-
     once override plus a caller-frame hit record prove the site).  The
     claim's honest UNKNOWN envelope and a proposed durable UNKNOWN result
     observable by same-key replay are asserted on the same connection; no
     restart or cross-connection attestation is claimed. The baseline had
     left the job RUNNING after the failure. The exception type must identify
     the injected post-commit read failure rather than an attempted ROLLBACK
     after COMMIT masking it with an unrelated OperationalError.

REAL (product code run unbound against inert receivers): the whole
``experiment.stage_run`` route through ``ControlDaemon._dispatch_admitted``,
the real ``_claim_stage_callback`` (_control_daemon.py 398-433),
``_pending`` (13171-13180), ``_error``/``_exception`` (13667-13675),
``_finish`` (13088-13118, including the F02 terminal-immunity
``LateResultRecorded`` path whose events this file asserts do NOT appear),
and the real ``OperationStore.begin`` (404-453), ``transition_status`` CAS
(2013-2090), ``finish`` (1870-1934, UNKNOWN as durable nonterminal),
``job``/``_job`` (1946-1949, 5190-5196).  Every non-hook path of the probes
below calls the REAL bound store method; no CAS or store logic is
re-implemented in this file.

FAKE (bounded scaffolding, never an engine/authorization/native claim): no
ControlDaemon instance, thread, network, Worker, SSH or COMSOL session.  The
receiver, fake ledger/backend/solve-loss fault and seeding helpers are the
already-vetted fixtures imported unmodified from
``tests/test_t31_stage_run_nested_unknown_20261111.py``; the fake scheduler
is the captured-only ``_InFlightScheduler`` from
``tests/test_t31_stage_define_pending_idempotency_20261111.py`` (both read,
never modified).  Every injected exception class below is SYNTHETIC and
confined to one fresh ``OperationStore(":memory:")`` per test; each store is
closed in ``finally`` and every patch is reverted in ``finally``.

Import-safety note (parent steering, 2026-11-12): this file deliberately
imports NOTHING from ``tests/test_t31_stage_queue_claim_edges_20261112.py``;
that live path was being edited by the parent while this file was authored,
so it is not treated as frozen or SHA-pinned here.  The probe classes below
are independent scaffolding written in this file.
"""
from __future__ import annotations

import traceback
from typing import Any

from comsol_mcp._operation_store import OperationStore
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _InFlightScheduler,
)
from tests.test_t31_stage_run_nested_unknown_20261111 import (
    MODEL_REF,
    PROJECT_ID,
    InjectedSolveResponseLoss,
    _attempt_rows,
    _dispatch_stage_run,
    _new_receiver,
    _register_synthetic_plan,
    _seed_test_only_accepted_predecessor,
    _StageRunReceiver,
)


# ---------------------------------------------------------------------------
# Synthetic, memory-only injected failures.  These labels must never be
# confused with any historical or real UNKNOWN job state.
# ---------------------------------------------------------------------------

class InjectedInnerClaimCasFailure(RuntimeError):
    """Raised by the probe on the SECOND wrapper call, strictly before the
    real CAS is entered, for the losing re-entered pass."""


class InjectedDuplicateClaimCasFailure(RuntimeError):
    """Raised by the probe for a duplicate invocation of an already-recorded
    callback, strictly before the real CAS is entered."""


class InjectedPostCommitJobViewFailure(RuntimeError):
    """Raised inside ``transition_status`` after its COMMIT, at the internal
    post-commit ``self.job(job_id)`` job-view call (2013-2090 anchors)."""


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

_PASSTHROUGH = object()


class _ScriptedTransitionProbe:
    """Instance-attribute wrapper around ONE store's ``transition_status``.

    ``actions`` are consumed in invocation order; each receives
    ``(args, kwargs, original_bound_cas, records)`` and may raise, return a
    value (short-circuit), or return ``_PASSTHROUGH`` to let the REAL CAS run
    afterwards.  Every unscripted call delegates to the REAL bound CAS and is
    recorded.  Nothing here re-implements the CAS.
    """

    def __init__(self, store: Any, actions: list[Any]) -> None:
        self.store = store
        self.original = store.transition_status
        self.actions = list(actions)
        self.invocations = 0
        self.cas_results: list[Any] = []
        self.records: dict[str, Any] = {}
        store.transition_status = self._wrapper

    def _wrapper(self, *args: Any, **kwargs: Any) -> Any:
        self.invocations += 1
        if self.invocations <= len(self.actions):
            outcome = self.actions[self.invocations - 1](
                args, kwargs, self.original, self.records
            )
            if outcome is not _PASSTHROUGH:
                return outcome
        result = self.original(*args, **kwargs)
        self.cas_results.append(result)
        return result

    def restore(self) -> None:
        self.store.transition_status = self.original


class PostCommitJobViewFaultStore(OperationStore):
    """Real in-memory OperationStore with one armed fault at the ONE
    post-commit internal ``self.job`` call inside ``transition_status``.

    The fault is armed exactly once, immediately before the first REAL
    ``transition_status`` runs, and fires on the next ``job()`` call.  Given
    the read window (2013-2090) the claim CAS is the only ``transition_status``
    caller on this route and its success branch is the only internal
    ``self.job`` call site, so the hit record's caller frame is the
    reachability proof of the injection site.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._first_transition_armed = False
        self._job_view_fault_armed = False
        self.job_view_fault_hits: list[dict[str, Any]] = []

    @staticmethod
    def _nearest_store_frame() -> dict[str, Any]:
        frames = [frame for frame in traceback.extract_stack()
                  if frame.filename and frame.filename.endswith("_operation_store.py")]
        if not frames:
            return {"function": None, "line": None, "source": None}
        frame = frames[-1]
        return {"function": frame.name, "line": frame.lineno,
                "source": frame.filename.rsplit("/", 1)[-1]}

    def transition_status(self, *args: Any, **kwargs: Any) -> Any:
        if not self._first_transition_armed:
            self._first_transition_armed = True
            self._job_view_fault_armed = True
        return super().transition_status(*args, **kwargs)

    def job(self, job_id: str) -> dict[str, Any] | None:
        if self._job_view_fault_armed:
            self._job_view_fault_armed = False
            self.job_view_fault_hits.append({
                **self._nearest_store_frame(),
                "job_id": job_id,
                "error_type": InjectedPostCommitJobViewFailure.__name__,
            })
            raise InjectedPostCommitJobViewFailure(
                "T31 test-only injected failure at the post-commit job view "
                "inside transition_status (synthetic, memory-only)"
            )
        return super().job(job_id)


def _job_event_names(store: Any, job_id: str) -> list[str]:
    return [
        row["event"]
        for row in store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    ]


def _assert_claim_failure_envelope(
    envelope: dict[str, Any], *, job_id: str, operation_id: str, request_id: str,
    idempotency_key: str, request_hash: str, expected_types: set[str],
) -> None:
    """The claim's own failure envelope: honest UNKNOWN, nothing invented."""
    assert isinstance(envelope, dict) and envelope["success"] is False, envelope
    error = envelope["error"]
    assert error["code"] == "EXECUTION_STATE_UNKNOWN", envelope
    # The claim must NOT fabricate a retryable admission.
    assert error.get("safe_retry") is False, (
        f"claim fabricated safe_retry={error.get('safe_retry')!r}: {envelope!r}"
    )
    assert error.get("type") in expected_types, (
        f"expected one of {sorted(expected_types)!r} as the surfaced failure "
        f"type, got {error!r}"
    )
    data = envelope["data"]
    assert data["job_id"] == job_id, envelope
    # engine_dispatched stays unknown; it may not be dressed up as False/True.
    assert data["engine_dispatched"] is None, (
        f"claim fabricated engine_dispatched={data['engine_dispatched']!r}: {envelope!r}"
    )
    assert envelope["execution"] == {
        "request_id": request_id, "operation_id": operation_id,
        "request_hash": request_hash, "idempotency_key": idempotency_key,
        "job_id": job_id,
    }, envelope


def _queued_stage_run_with_callback(
    request_id: str, idempotency_key: str,
) -> tuple[Any, dict[str, Any], Any, dict[str, Any]]:
    """Fresh stage_run receiver admitted to QUEUED with its captured callback.

    The synthetic UNKNOWN/UNKNOWN-witness states produced by later steps are
    confined to this fresh :memory: store and are never presented as any
    historical job.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    receiver.session_scheduler = _InFlightScheduler()
    scheduler = receiver.session_scheduler
    first = _dispatch_stage_run(
        receiver, ids["predecessor"]["attempt_id"],
        request_id=request_id, idempotency_key=idempotency_key,
    )
    assert first["success"] is True and first["data"]["status"] == "QUEUED", first
    assert first["data"]["rpc_wait_expired"] is True, first
    assert first["data"]["stage_attempt_id"] is None, first
    assert len(scheduler.calls) == 1, scheduler.calls
    _context, callback = scheduler.calls[0]
    assert getattr(callback, "__name__", "") == "preflight_and_record"
    assert receiver.backend.calls == []
    assert _attempt_rows(store, ids["model_ref"]) == []
    execution = first["execution"]
    identities = {
        "operation_id": execution["operation_id"],
        "job_id": execution["job_id"],
        "request_hash": execution["request_hash"],
        "request_id": request_id,
        "idempotency_key": idempotency_key,
    }
    return receiver, ids, callback, identities


def _new_post_commit_fault_receiver() -> tuple[Any, dict[str, Any], Any]:
    """Fresh stage_run receiver whose store is the fault-injecting subclass.

    Seeding mirrors the vetted ``_new_receiver`` exactly (plan registration,
    revision binding, synthetic predecessor) but on the subclass store.  The
    receiver's original store is returned so the caller closes it too.
    """
    receiver = _StageRunReceiver("after_dispatch_clean")
    legacy_store = receiver.store
    fault_store = PostCommitJobViewFaultStore(":memory:")
    receiver.store = fault_store
    model_ref = dict(MODEL_REF)
    _register_synthetic_plan(fault_store, model_ref)
    fault_store.put_metadata("revisions", receiver.backend._model_project_key(model_ref), {
        "model_ref": model_ref, "revision": 0, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": PROJECT_ID,
    })
    predecessor = _seed_test_only_accepted_predecessor(fault_store, model_ref)
    return receiver, {"model_ref": model_ref, "predecessor": predecessor}, legacy_store


# ===========================================================================
# 1 — manual same-closure re-entry: outer real CAS wins, inner CAS raises
# ===========================================================================

def test_stage_run_same_closure_reentry_claim_cas_fault_keeps_running_owner_and_one_dispatch():
    """Manual re-entry only (NOT a real-concurrency proof): the outer REAL
    claim CAS commits QUEUED -> RUNNING first; the SAME callback object is
    then re-entered and its claim CAS raises exactly once.  The loser must
    not touch the RUNNING state owned by the outer invocation, must not
    fabricate a safe retry, must bind zero stage side effects, and the outer
    completion must reach the fake backend exactly once.  The loser keeping
    the winner's RUNNING state is the conservative design goal asserted here.
    """
    receiver, ids, callback, ident = _queued_stage_run_with_callback(
        "t31-ownerrace-reentry-1", "t31-ownerrace-reentry-1-key",
    )
    store = receiver.store
    model_ref = ids["model_ref"]
    job_id, operation_id = ident["job_id"], ident["operation_id"]
    probe: _ScriptedTransitionProbe | None = None

    try:
        def outer_claim_then_reentry(args: Any, kwargs: Any, original: Any,
                                     records: dict[str, Any]) -> Any:
            assert args[0] == job_id, (args[0], job_id)
            result = original(*args, **kwargs)      # outer REAL CAS wins first
            records["outer_cas"] = result
            if result[0] is not True or result[1] != "RUNNING":
                raise AssertionError(
                    f"FIXTURE PRECONDITION: outer claim CAS should have claimed, got {result!r}"
                )
            records["attempts_before_inner"] = len(_attempt_rows(store, model_ref))
            records["backend_before_inner"] = len(receiver.backend.calls)
            records["inner_return"] = callback()    # SAME callback object, re-entered
            records["attempts_after_inner"] = len(_attempt_rows(store, model_ref))
            records["backend_after_inner"] = len(receiver.backend.calls)
            records["job_after_inner"] = dict(store.job(job_id))
            records["events_after_inner"] = _job_event_names(store, job_id)
            return result                           # outer claim proceeds normally

        def inner_claim_cas_fault(args: Any, kwargs: Any, original: Any,
                                  records: dict[str, Any]) -> Any:
            assert args[0] == job_id, (args[0], job_id)
            raise InjectedInnerClaimCasFailure(
                "T31 test-only single injected claim-CAS failure for the losing "
                "re-entered pass (synthetic, memory-only)"
            )

        probe = _ScriptedTransitionProbe(
            store, [outer_claim_then_reentry, inner_claim_cas_fault]
        )
        outer_return = callback()

        # The outer and inner calls must both reach the scripted probe. A
        # subsequent conditional recovery CAS is permitted if it leaves the
        # actual owner's state and side effects intact, as checked below.
        assert probe.invocations >= 2, probe.invocations
        claimed, status, _view = probe.records["outer_cas"]
        assert claimed is True and status == "RUNNING", probe.records["outer_cas"]

        # ---- loser's envelope: honest UNKNOWN, no fabricated retry/dispatch
        inner = probe.records["inner_return"]
        _assert_claim_failure_envelope(
            inner, job_id=job_id, operation_id=operation_id,
            request_id=ident["request_id"], idempotency_key=ident["idempotency_key"],
            request_hash=ident["request_hash"],
            expected_types={InjectedInnerClaimCasFailure.__name__},
        )

        # ---- loser caused zero side effects and never touched the owner's
        #      durable RUNNING state (conservative design goal)
        rec = probe.records
        assert rec["attempts_before_inner"] == 0 and rec["attempts_after_inner"] == 0
        assert rec["backend_before_inner"] == 0 and rec["backend_after_inner"] == 0
        assert rec["job_after_inner"]["status"] == "RUNNING", rec["job_after_inner"]
        inner_events = rec["events_after_inner"]
        assert inner_events.count("RUNNING") == 1, inner_events
        assert "UNKNOWN" not in inner_events, inner_events
        assert not [event for event in inner_events
                    if event in ("LateTransitionRejected", "LateResultRecorded")], inner_events

        # ---- POSITIVE CONTROL: the outer pass still owns and finishes the
        #      work exactly once, with exactly one fake backend dispatch.
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        assert len(receiver.backend.fault_hits) == 1, receiver.backend.fault_hits
        attempts = _attempt_rows(store, model_ref)
        assert len(attempts) == 1, attempts
        durable_attempt = store.get_stage_attempt(PROJECT_ID, model_ref, attempts[0]["attempt_id"])
        assert durable_attempt is not None, attempts
        # Synthetic witness of THIS fresh store only; not any historical state.
        assert durable_attempt["status"] == "UNKNOWN", durable_attempt
        assert durable_attempt["engine_dispatched"] is True, durable_attempt

        assert outer_return["success"] is False, outer_return
        assert outer_return["error"]["code"] == "EXECUTION_STATE_UNKNOWN", outer_return
        assert outer_return["error"]["type"] == InjectedSolveResponseLoss.__name__, outer_return
        assert outer_return["error"]["safe_retry"] is False, outer_return
        assert outer_return["data"]["engine_dispatched"] is True, outer_return
        recorded = store.get_operation(operation_id)["result"]
        assert recorded == outer_return, (recorded, outer_return)
        assert store.job(job_id)["status"] == "UNKNOWN", store.job(job_id)

        # ---- events: exactly one RUNNING (the winner's claim) and exactly
        #      one UNKNOWN (the outer finish); the injected loser wrote none.
        events = _job_event_names(store, job_id)
        assert events.count("RUNNING") == 1, events
        assert events.count("UNKNOWN") == 1, events
        assert not [event for event in events
                    if event in ("LateTransitionRejected", "LateResultRecorded")], events

        assert len(receiver.session_scheduler.calls) == 1, (
            "re-entry re-entered the scheduler admission"
        )
        # The injected claim failure is caught inside _claim_stage_callback
        # itself and the solve-loss fault is caught by the intact inner
        # handler, so the generic branch (and _log_exception) never ran.
        assert receiver.log_exceptions == [], receiver.log_exceptions
    finally:
        if probe is not None:
            probe.restore()
        store.close()


# ===========================================================================
# 2 — recorded UNKNOWN witness (engine_dispatched=True) survives a weaker
#     duplicate-claim CAS failure
# ===========================================================================

def test_stage_run_duplicate_claim_cas_fault_cannot_overwrite_recorded_unknown_witness():
    """The durable recorded result of this fresh store's synthetic UNKNOWN job
    (engine_dispatched=True fake witness) must NOT be overwritten by a weaker
    UNKNOWN/None envelope from a duplicate invocation whose claim CAS raises
    once, and the fake backend must see no second call.  This file never
    touches any historical/real UNKNOWN job.
    """
    receiver, ids, callback, ident = _queued_stage_run_with_callback(
        "t31-ownerrace-dup-2", "t31-ownerrace-dup-2-key",
    )
    store = receiver.store
    model_ref = ids["model_ref"]
    job_id, operation_id = ident["job_id"], ident["operation_id"]
    probe: _ScriptedTransitionProbe | None = None

    try:
        # --- phase 1: the only real run, recorded durably as the synthetic
        #     UNKNOWN with a fake engine-dispatch witness (memory-only).
        recorded = callback()
        assert recorded["success"] is False, recorded
        assert recorded["error"]["code"] == "EXECUTION_STATE_UNKNOWN", recorded
        assert recorded["error"]["type"] == InjectedSolveResponseLoss.__name__, recorded
        assert recorded["error"]["safe_retry"] is False, recorded
        assert recorded["data"]["engine_dispatched"] is True, recorded
        assert store.job(job_id)["status"] == "UNKNOWN", store.job(job_id)
        operation_after_record = store.get_operation(operation_id)
        assert operation_after_record["status"] == "UNKNOWN", operation_after_record
        assert operation_after_record["result"] == recorded, operation_after_record
        attempts = _attempt_rows(store, model_ref)
        assert len(attempts) == 1, attempts
        attempt_id = attempts[0]["attempt_id"]
        durable_attempt = store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
        assert durable_attempt is not None and durable_attempt["status"] == "UNKNOWN"
        assert durable_attempt["engine_dispatched"] is True, durable_attempt
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        events_after_record = _job_event_names(store, job_id)
        assert events_after_record.count("RUNNING") == 1, events_after_record

        # --- phase 2: duplicate invocation of the SAME callback; its claim
        #     CAS raises exactly once, strictly before the real CAS.
        def duplicate_claim_cas_fault(args: Any, kwargs: Any, original: Any,
                                      records: dict[str, Any]) -> Any:
            assert args[0] == job_id, (args[0], job_id)
            raise InjectedDuplicateClaimCasFailure(
                "T31 test-only duplicate-claim CAS failure against an already "
                "recorded synthetic UNKNOWN (synthetic, memory-only)"
            )

        probe = _ScriptedTransitionProbe(store, [duplicate_claim_cas_fault])
        duplicate = callback()

        # A subsequent conditional recovery CAS may run; it must not replace
        # the already recorded result or produce any new backend dispatch.
        assert probe.invocations >= 1, probe.invocations
        # Returning the already recorded stronger result is also legitimate;
        # otherwise the immediate failure must remain an honest UNKNOWN.
        if duplicate != recorded:
            _assert_claim_failure_envelope(
                duplicate, job_id=job_id, operation_id=operation_id,
                request_id=ident["request_id"], idempotency_key=ident["idempotency_key"],
                request_hash=ident["request_hash"],
                expected_types={InjectedDuplicateClaimCasFailure.__name__},
            )

        after = store.get_operation(operation_id)
        assert after["status"] == "UNKNOWN", after
        assert after["result"] == recorded, (after["result"], recorded)
        job_after = store.job(job_id)
        assert job_after["status"] == "UNKNOWN", job_after
        assert job_after["result"] == recorded, job_after
        assert store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id) == durable_attempt, (
            "the recorded synthetic UNKNOWN attempt witness was mutated"
        )
        assert len(_attempt_rows(store, model_ref)) == 1
        # No second fake backend call, no second admission, no new events.
        assert len(receiver.backend.calls) == 1, receiver.backend.calls
        assert len(receiver.backend.fault_hits) == 1, receiver.backend.fault_hits
        assert len(receiver.session_scheduler.calls) == 1
        events_after_duplicate = _job_event_names(store, job_id)
        assert events_after_duplicate == events_after_record, (
            events_after_duplicate, events_after_record
        )
        assert not [event for event in events_after_duplicate
                    if event in ("LateTransitionRejected", "LateResultRecorded")], (
            events_after_duplicate
        )
        assert receiver.log_exceptions == [], receiver.log_exceptions

        # --- POSITIVE CONTROL: the real same-idempotency-key replay returns
        #     the recorded envelope verbatim with no new admission or solve.
        replay = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id="t31-ownerrace-dup-2-replay",
            idempotency_key=ident["idempotency_key"],
        )
        assert replay == recorded, (replay, recorded)
        assert store.get_operation(operation_id)["result"] == recorded
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert len(receiver.session_scheduler.calls) == 1, (
            "same-key replay re-admitted the scheduler"
        )
        assert len(receiver.backend.calls) == 1, (
            "same-key replay reached backend.invoke again (second synthetic solve)"
        )
        assert len(_attempt_rows(store, model_ref)) == 1
    finally:
        if probe is not None:
            probe.restore()
        store.close()


# ===========================================================================
# 3 — one-shot fault at transition_status's internal post-commit self.job()
# ===========================================================================

def test_stage_run_post_commit_job_view_failure_remains_reconcilable_on_same_key():
    """Injects exactly one fault at the internal ``self.job(job_id)`` job view
    inside ``transition_status`` (the only such call site in 2013-2090, on the
    success branch strictly AFTER the COMMIT at 2085).  Asserts: the hit
    record (caller frame inside the real store), the claim's honest UNKNOWN
    envelope, and the proposed durable UNKNOWN result on this same :memory:
    connection, retrievable by the same key. No restart/cross-connection
    attestation is made. The one-shot fault and normal reads are controls.
    """
    receiver, ids, legacy_store = _new_post_commit_fault_receiver()
    store = receiver.store
    request_id = "t31-ownerrace-jobview-3"
    idempotency_key = "t31-ownerrace-jobview-3-key"
    try:
        envelope = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id=request_id, idempotency_key=idempotency_key,
        )
        operations = store.db.execute(
            "SELECT operation_id FROM operations ORDER BY rowid"
        ).fetchall()
        assert len(operations) == 1, operations
        operation_id = operations[0]["operation_id"]
        job_row = store.operation_job(operation_id)
        assert job_row is not None
        job_id = job_row["job_id"]
        request_hash = store.get_operation(operation_id)["request_hash"]

        # ---- hit record: the fault fired inside the REAL transition_status
        hits = store.job_view_fault_hits
        assert len(hits) == 1, hits
        hit = hits[0]
        assert hit["job_id"] == job_id, hit
        assert hit["source"] == "_operation_store.py", hit
        assert hit["function"] == "transition_status", hit
        assert hit["error_type"] == InjectedPostCommitJobViewFailure.__name__, hit
        assert store._job_view_fault_armed is False, "fault must be one-shot"

        # ---- claim envelope: honest UNKNOWN, with the original failure type
        #      rather than a post-COMMIT ROLLBACK masking it.
        _assert_claim_failure_envelope(
            envelope, job_id=job_id, operation_id=operation_id,
            request_id=request_id, idempotency_key=idempotency_key,
            request_hash=request_hash,
            expected_types={InjectedPostCommitJobViewFailure.__name__},
        )

        # ---- Same-connection observation: RUNNING was committed before the
        #      injected read failure, then its uncertainty must be visible.
        job = store.job(job_id)
        assert job is not None and job["status"] == "UNKNOWN", job
        operation = store.get_operation(operation_id)
        assert operation["status"] == "UNKNOWN" and operation["result"] == envelope, operation
        events = _job_event_names(store, job_id)
        assert events.count("RUNNING") == 1, events
        assert events.count("UNKNOWN") == 1, events
        assert not [event for event in events
                    if event in ("LateTransitionRejected", "LateResultRecorded")], events
        # No stage side effect ran: the claim never returned None.
        assert len(_attempt_rows(store, ids["model_ref"])) == 0
        assert receiver.backend.calls == []
        assert len(receiver.session_scheduler.calls) == 1
        assert receiver.log_exceptions == [], receiver.log_exceptions

        # ---- same-key query returns the original UNKNOWN rather than
        #      scheduling again or presenting an apparently live RUNNING job.
        replay = _dispatch_stage_run(
            receiver, ids["predecessor"]["attempt_id"],
            request_id="t31-ownerrace-jobview-3-replay",
            idempotency_key=idempotency_key,
        )
        assert replay == envelope, (replay, envelope)
        # POSITIVE CONTROL: normal (unfaulted) store reads work again.
        assert len(store.job_view_fault_hits) == 1, store.job_view_fault_hits
        assert store.job(job_id)["status"] == "UNKNOWN", store.job(job_id)
        assert store.get_operation(operation_id)["status"] == "UNKNOWN"
        assert len(receiver.session_scheduler.calls) == 1, (
            "same-key query re-admitted the scheduler"
        )
        assert receiver.backend.calls == []
        assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
    finally:
        store.close()
        legacy_store.close()
