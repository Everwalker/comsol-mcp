"""Synthetic dispatcher regression for cancel-state refusal fallthrough.

Each test creates its own in-memory QUEUED job, injects operation-row RUNNING
status only in that ledger, and invokes the real unbound dispatcher with an
inert receiver and fake authority. It does not touch a real Worker, UNKNOWN
job, daemon, model, network, disk database or historical resource. The fake
project grant is not a real principal authentication claim.
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


@pytest.mark.parametrize('route', ('canonical', 'legacy'))
def test_cancel_pair_state_mismatch_does_not_fall_through_to_intent(route: str) -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag=f'drift-fallback-{route}',
            content_marker=f't35-drift-fallback-{route}',
            event_markers=(f't35-drift-event-{route}',),
            result_marker=f't35-drift-result-{route}',
        )
        job = store.job(job_id)
        assert job is not None and job['operation'] is not None
        changed = store.db.execute(
            "UPDATE operations SET status='RUNNING' WHERE operation_id=?",
            (job['operation_id'],),
        )
        assert changed.rowcount == 1
        before = _snapshot(store, job_id)
        assert before['job_status'] == 'QUEUED' and before['operation_status'] == 'RUNNING'
        receiver = _InertDispatchReceiver(store, _FakeProjectAuthority({PROJECT_A: {'host_control'}}))
        args = {'job_id': job_id, 'reason': 'synthetic state drift'}
        if route == 'canonical':
            response = _canonical_route(receiver, 'job.cancel', args, PROJECT_A)
        else:
            response = _legacy_route(receiver, 'job_cancel', args, envelope_project=PROJECT_A)
        after = _snapshot(store, job_id)
        assert after == before, 'state mismatch must not fall through into intent writes'
        assert response.get('success') is False
        assert not store.db.in_transaction
    finally:
        store.close()
