"""T31: claim-failure visibility through same-key stage-run replay.

Only fresh :memory: stores and manually driven fake schedulers/backends are
used. The predecessor, request identities and UNKNOWN are synthetic; no real
COMSOL session, Worker, SSH, old model or historical job is touched. The
public-facing replay must not appear merely pending after its only captured
callback has returned UNKNOWN and the store is readable again.
"""
from __future__ import annotations

from typing import Any

from tests.test_t31_stage_define_pending_idempotency_20261111 import _InFlightScheduler
from tests.test_t31_stage_run_nested_unknown_20261111 import (
    _attempt_rows,
    _dispatch_stage_run,
    _new_receiver,
)


class InjectedClaimFailure(RuntimeError):
    """Test-only transient failure at the callback claim boundary."""


def _run_claim_fault_replay(*, after_commit: bool) -> None:
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    setattr(receiver, "session_scheduler", _InFlightScheduler())
    scheduler = receiver.session_scheduler
    marker = "after" if after_commit else "before"
    request_id = f"t31-claim-fault-replay-{marker}"
    idempotency_key = f"t31-claim-fault-replay-{marker}-key"
    predecessor_id = ids["predecessor"]["attempt_id"]
    original = store.transition_status
    injected: list[tuple[Any, ...]] = []
    try:
        initial = _dispatch_stage_run(
            receiver, predecessor_id, request_id=request_id,
            idempotency_key=idempotency_key,
        )
        assert initial["success"] is True
        assert initial["data"]["status"] == "QUEUED"
        assert len(scheduler.calls) == 1
        _, callback = scheduler.calls[0]
        assert callback.__name__ == "preflight_and_record"
        operation_id = initial["execution"]["operation_id"]
        job_id = initial["execution"]["job_id"]

        def fail_claim(*args: Any, **kwargs: Any) -> Any:
            assert args[0] == job_id
            if after_commit:
                claim = original(*args, **kwargs)
                assert claim[0] is True and claim[1] == "RUNNING", claim
                injected.append(("after", claim[0], claim[1]))
            else:
                injected.append(("before",))
            raise InjectedClaimFailure(f"synthetic transient claim {marker} commit")

        store.transition_status = fail_claim
        immediate = callback()
        store.transition_status = original
        operation_before_replay = store.get_operation(operation_id)
        job_before_replay = store.job(job_id)
        replay = _dispatch_stage_run(
            receiver, predecessor_id, request_id=request_id,
            idempotency_key=idempotency_key,
        )
        operation_after_replay = store.get_operation(operation_id)
        job_after_replay = store.job(job_id)

        # Prove that the target fault was reached and no synthetic stage work
        # or second scheduler admission occurred before judging the outcome.
        assert len(injected) == 1 and injected[0][0] == marker, injected
        assert immediate["success"] is False
        assert immediate["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert immediate["error"]["safe_retry"] is False
        assert immediate["data"]["engine_dispatched"] is None
        assert immediate["execution"]["job_id"] == job_id
        assert immediate["execution"]["operation_id"] == operation_id
        assert len(scheduler.calls) == 1 and receiver.backend.calls == []
        assert _attempt_rows(store, ids["model_ref"]) == []
        assert operation_before_replay is not None and job_before_replay is not None
        assert operation_after_replay is not None and job_after_replay is not None
        assert job_after_replay["job_id"] == job_id
        assert operation_after_replay["operation_id"] == operation_id

        # U07 requires an unresolved UNKNOWN to remain visible and reconcilable
        # without a new key. A callback-only error is lost after the first RPC
        # timed out; a plain QUEUED/RUNNING pending replay hides that outcome.
        assert replay["success"] is False, (
            "same-key replay hid the failed claim callback behind a pending "
            f"result: immediate={immediate!r}, replay={replay!r}, "
            f"before_job_status={job_before_replay['status']!r}"
        )
        assert replay["error"]["code"] == "EXECUTION_STATE_UNKNOWN", replay
        assert replay["error"]["safe_retry"] is False, replay
        assert replay["data"]["engine_dispatched"] is None, replay
        assert replay["execution"]["job_id"] == job_id
        assert replay["execution"]["operation_id"] == operation_id
        assert operation_after_replay["status"] == "UNKNOWN"
        assert job_after_replay["status"] == "UNKNOWN"
        assert operation_after_replay["result"] == replay
    finally:
        store.transition_status = original
        store.close()


def test_stage_run_pre_claim_failure_is_durably_visible_on_same_key_replay():
    _run_claim_fault_replay(after_commit=False)


def test_stage_run_post_claim_failure_is_durably_visible_on_same_key_replay():
    _run_claim_fault_replay(after_commit=True)


def test_stage_run_durable_unknown_replay_positive_control():
    """The same fake route can return a recorded synthetic UNKNOWN normally."""
    receiver, ids = _new_receiver("after_dispatch_clean")
    store = receiver.store
    setattr(receiver, "session_scheduler", _InFlightScheduler())
    scheduler = receiver.session_scheduler
    request_id = "t31-claim-fault-replay-positive"
    idempotency_key = "t31-claim-fault-replay-positive-key"
    predecessor_id = ids["predecessor"]["attempt_id"]
    try:
        initial = _dispatch_stage_run(
            receiver, predecessor_id, request_id=request_id,
            idempotency_key=idempotency_key,
        )
        assert initial["success"] is True and len(scheduler.calls) == 1
        _, callback = scheduler.calls[0]
        immediate = callback()
        job_id = initial["execution"]["job_id"]
        operation_id = initial["execution"]["operation_id"]
        stored = store.get_operation(operation_id)
        job = store.job(job_id)
        replay = _dispatch_stage_run(
            receiver, predecessor_id, request_id=request_id,
            idempotency_key=idempotency_key,
        )
        assert immediate["success"] is False
        assert immediate["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert stored is not None and stored["status"] == "UNKNOWN"
        assert job is not None and job["status"] == "UNKNOWN"
        assert stored["result"] == immediate == replay
        assert replay["error"]["safe_retry"] is False
        assert replay["execution"]["job_id"] == job_id
        assert len(scheduler.calls) == 1 and len(receiver.backend.calls) == 1
        assert len(_attempt_rows(store, ids["model_ref"])) == 1
    finally:
        store.close()
