"""Synthetic queued-cancel drift refusal and valid-pair controls.

Only fresh in-memory jobs are used. The dispatcher methods are real; the
receiver and project authority are substitutes. No native model, Worker,
network, actual daemon, historical record or UNKNOWN job is accessed.
"""
from __future__ import annotations

import pytest

from comsol_mcp._operation_store import OperationStore
from tests.test_t35_job_control_project_scope_20261112 import (
    PROJECT_A,
    _FakeProjectAuthority,
    _InertDispatchReceiver,
    _canonical_route,
    _create_queued_job,
    _legacy_route,
    _snapshot,
)


@pytest.mark.parametrize(
    ('route', 'drift'),
    (
        pytest.param('canonical', True, id='canonical-drift'),
        pytest.param('legacy', True, id='legacy-drift'),
        pytest.param('canonical', False, id='canonical-consistent'),
        pytest.param('legacy', False, id='legacy-consistent'),
    ),
)
def test_queued_cancel_pair_refusal_or_success(route: str, drift: bool) -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _create_queued_job(
            store,
            project_id=PROJECT_A,
            tag=f'cancel-response-{route}-{drift}',
            content_marker='synthetic-cancel-response',
            event_markers=('synthetic-cancel-event',),
            result_marker='synthetic-existing-result',
        )
        job = store.job(job_id)
        assert job is not None and job['operation'] is not None
        if drift:
            changed = store.db.execute(
                "UPDATE operations SET status='RUNNING' WHERE operation_id=?",
                (job['operation_id'],),
            )
            assert changed.rowcount == 1
        before = _snapshot(store, job_id)
        assert before['job_status'] == 'QUEUED'
        assert before['operation_status'] == ('RUNNING' if drift else 'QUEUED')

        calls = []
        cancel_queued = store.cancel_queued

        def observe_cancel(job_id: str, reason: str = 'cancelled by user'):
            result = cancel_queued(job_id, reason=reason)
            calls.append((job_id, result[0], result[1]))
            return result

        store.cancel_queued = observe_cancel
        receiver = _InertDispatchReceiver(store, _FakeProjectAuthority({PROJECT_A: {'host_control'}}))
        args = {'job_id': job_id, 'reason': 'synthetic state comparison'}
        if route == 'canonical':
            response = _canonical_route(receiver, 'job.cancel', args, PROJECT_A)
        else:
            response = _legacy_route(receiver, 'job_cancel', args, envelope_project=PROJECT_A)
        after = _snapshot(store, job_id)
        assert not store.db.in_transaction
        if drift:
            assert calls == [(job_id, False, 'STATE_MISMATCH')]
            assert response.get('success') is False
            assert response['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
            assert response['error']['safe_retry'] is False
            assert after == before
        else:
            assert calls == [(job_id, True, 'CANCELLED')]
            assert response.get('success') is True
            assert response['data']['mode'] == 'QUEUED_ABORT'
            assert response['data']['cancel_accepted'] is True
            assert response['data']['engine_dispatched'] is False
            assert response['data']['engine_stopped'] is False
            assert after['job_status'] == 'CANCELLED'
            assert after['operation_status'] == 'CANCELLED'
            assert after['operation_result']['error']['code'] == 'OPERATION_CANCELLED'
            assert len(after['events']) == len(before['events']) + 1
            assert after['events'][-1]['event'] == 'CANCELLED'
    finally:
        store.close()
