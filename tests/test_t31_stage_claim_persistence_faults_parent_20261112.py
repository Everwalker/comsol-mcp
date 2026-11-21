"""T31: failure during/after synthetic stage-claim UNKNOWN persistence.

Each case uses a fresh in-memory store, manually invoked fake scheduler and
synthetic stage request. No daemon, Worker, model, SSH or historical UNKNOWN
is accessed. These added fault controls have no separately observed preimage RED.
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
    pass


class InjectedPersistenceFailure(RuntimeError):
    pass


class _EventInsertFaultConnection:
    """Delegate the real memory connection, failing one UNKNOWN event insert."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.observed: list[str] = []
        self.hit = False

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if sql.startswith("UPDATE jobs SET status='UNKNOWN'"):
            self.observed.append('job-update')
        elif sql.startswith("UPDATE operations SET status='UNKNOWN'"):
            self.observed.append('operation-update')
        elif sql.startswith('INSERT INTO job_events') and "'UNKNOWN'" in sql:
            self.observed.append('unknown-event-fault')
            self.hit = True
            raise InjectedPersistenceFailure('synthetic failure before event insertion')
        elif sql == 'ROLLBACK':
            self.observed.append('rollback')
        return self.connection.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


def _new_queued(request_id: str, key: str) -> tuple[Any, dict[str, Any], Any, dict[str, Any]]:
    receiver, ids = _new_receiver('after_dispatch_clean')
    setattr(receiver, 'session_scheduler', _InFlightScheduler())
    first = _dispatch_stage_run(receiver, ids['predecessor']['attempt_id'],
                                request_id=request_id, idempotency_key=key)
    assert first['success'] is True and first['data']['status'] == 'QUEUED', first
    assert len(receiver.session_scheduler.calls) == 1
    _, callback = receiver.session_scheduler.calls[0]
    assert callback.__name__ == 'preflight_and_record'
    return receiver, ids, callback, first['execution']


def _raise_before_claim(*args: Any, **kwargs: Any) -> Any:
    raise InjectedClaimFailure('synthetic transient error before stage claim')


def _assert_unknown(envelope: dict[str, Any], identity: dict[str, Any]) -> None:
    assert envelope['success'] is False, envelope
    assert envelope['error']['code'] == 'EXECUTION_STATE_UNKNOWN', envelope
    assert envelope['error']['safe_retry'] is False, envelope
    assert envelope['error']['type'] == 'InjectedClaimFailure', envelope
    assert envelope['data']['engine_dispatched'] is None, envelope
    assert envelope['execution']['job_id'] == identity['job_id'], envelope
    assert envelope['execution']['operation_id'] == identity['operation_id'], envelope


def test_claim_unknown_event_write_failure_rolls_back_both_rows_and_keeps_immediate_unknown():
    request_id, key = 't31-claim-atomic-fail', 't31-claim-atomic-fail-key'
    receiver, ids, callback, identity = _new_queued(request_id, key)
    store = receiver.store
    real_connection = store.db
    original_claim = store.transition_status
    proxy = _EventInsertFaultConnection(real_connection)
    try:
        store.transition_status = _raise_before_claim
        store.db = proxy
        immediate = callback()
        store.db = real_connection
        store.transition_status = original_claim
        assert proxy.hit is True, proxy.observed
        assert proxy.observed == ['job-update', 'operation-update', 'unknown-event-fault', 'rollback'], proxy.observed
        _assert_unknown(immediate, identity)
        job = store.job(identity['job_id'])
        operation = store.get_operation(identity['operation_id'])
        assert job['status'] == 'QUEUED' and operation['status'] == 'QUEUED', (job, operation)
        assert operation['result'] is None
        events = store.db.execute('SELECT event FROM job_events WHERE job_id=?', (identity['job_id'],)).fetchall()
        assert [row['event'] for row in events] == [], events
        replay = _dispatch_stage_run(receiver, ids['predecessor']['attempt_id'],
                                     request_id=request_id, idempotency_key=key)
        assert replay['success'] is True and replay['data']['status'] == 'QUEUED', replay
        assert replay['execution']['job_id'] == identity['job_id'], replay
        assert replay['execution']['operation_id'] == identity['operation_id'], replay
        assert len(receiver.session_scheduler.calls) == 1 and receiver.backend.calls == []
        assert _attempt_rows(store, ids['model_ref']) == []
    finally:
        store.db = real_connection
        store.transition_status = original_claim
        store.close()


def test_claim_unknown_postcommit_ack_loss_replays_original_result_without_readmission():
    request_id, key = 't31-claim-commit-loss', 't31-claim-commit-loss-key'
    receiver, ids, callback, identity = _new_queued(request_id, key)
    store = receiver.store
    original_claim = store.transition_status
    original_record = store.record_stage_claim_failure
    commits: list[Any] = []
    try:
        store.transition_status = _raise_before_claim

        def commit_then_lose_ack(*args: Any, **kwargs: Any) -> Any:
            recorded = original_record(*args, **kwargs)
            commits.append(recorded)
            raise InjectedPersistenceFailure('synthetic lost acknowledgement after real commit')

        store.record_stage_claim_failure = commit_then_lose_ack
        immediate = callback()
        store.transition_status = original_claim
        store.record_stage_claim_failure = original_record
        assert len(commits) == 1
        _assert_unknown(immediate, identity)
        assert commits[0] == immediate
        job = store.job(identity['job_id'])
        operation = store.get_operation(identity['operation_id'])
        assert job['status'] == 'UNKNOWN' and operation['status'] == 'UNKNOWN', (job, operation)
        assert operation['result'] == immediate
        events = store.db.execute('SELECT event FROM job_events WHERE job_id=?', (identity['job_id'],)).fetchall()
        assert [row['event'] for row in events] == ['UNKNOWN'], events
        replay = _dispatch_stage_run(receiver, ids['predecessor']['attempt_id'],
                                     request_id=request_id, idempotency_key=key)
        assert replay == immediate, (replay, immediate)
        assert len(receiver.session_scheduler.calls) == 1 and receiver.backend.calls == []
        assert _attempt_rows(store, ids['model_ref']) == []
    finally:
        store.transition_status = original_claim
        store.record_stage_claim_failure = original_record
        store.close()
