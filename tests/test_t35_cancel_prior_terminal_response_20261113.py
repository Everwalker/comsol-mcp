"""Ordinary-cancel response when another synthetic actor cancels first.

This models a QUEUED->CANCELLED transition at the call boundary using only a
new task-owned in-memory store. It does not use threads, real principals,
workers, force-stop or historic jobs. The prior terminal transition is a test
injection, not a claim that a real race has been observed.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._operation_store import OperationStore
from tests.test_t35_cancel_wiring_synthetic_20261113 import (
    PROJECT_A,
    _dispatch,
    _fixture,
    _snapshot,
)


@pytest.fixture
def owned_store():
    store = OperationStore(':memory:')
    try:
        yield store
    finally:
        store.db.set_trace_callback(None)
        store.close()


@pytest.mark.parametrize('route', ('canonical', 'legacy'))
def test_prior_cancelled_is_terminal_noop_not_new_queue_abort(route, owned_store):
    store, _auth, receiver, real, timeline, job_id, _, _ = _fixture(
        store=owned_store,
        tag=f'T35-PRIOR-CANCEL-{route}',
        bound_project=PROJECT_A,
        status='QUEUED',
        permissions={PROJECT_A: {'host_control'}},
    )
    forwarded_cancel = store.cancel_queued
    witness = {}

    def interleave(job_id_arg, *args, **kwargs):
        assert job_id_arg == job_id
        assert kwargs.get('project_id') == PROJECT_A
        assert kwargs.get('enforce_project_binding') is True
        assert ('authority', PROJECT_A, 'host_control') in timeline
        prior = real['cancel_queued'](
            job_id, reason='simulated prior cancellation',
            project_id=PROJECT_A, enforce_project_binding=True,
        )
        assert prior[0:2] == (True, 'CANCELLED')
        witness['after_prior'] = _snapshot(real['job'], real['events'], job_id)
        returned = forwarded_cancel(job_id_arg, *args, **kwargs)
        assert returned[0:2] == (True, 'ALREADY_CANCELLED')
        witness['returned_code'] = returned[1]
        return returned

    store.cancel_queued = interleave  # type: ignore[method-assign]
    response = _dispatch(receiver, route, {'job_id': job_id}, {'project_id': PROJECT_A})
    assert witness['returned_code'] == 'ALREADY_CANCELLED'
    after = _snapshot(real['job'], real['events'], job_id)
    assert after == witness['after_prior'], 'second cancellation must not add a write or event'
    assert response.get('success') is True
    data = response.get('data') or {}
    assert data.get('job_id') == job_id
    assert data.get('status') == 'CANCELLED'
    assert data.get('mode') == 'TERMINAL_NOOP', response
    assert data.get('cancel_accepted') is False, response
