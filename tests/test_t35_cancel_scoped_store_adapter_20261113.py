"""Parent adapter for proposed scoped cancellation and early-return coverage.

The first four tests invoke the unmodified Qwen-authored bodies. The fifth
independently accepts TypeError for an invalid flag only after verifying that
the proposed named interface exists, so a missing keyword is not a pass.
Two additional cases require scope checks before non-QUEUED early returns.
All records are synthetic, fresh, in-memory; this does not authenticate a
principal or prove cross-process behavior.
"""
from __future__ import annotations

import inspect

import pytest

from comsol_mcp._operation_store import OperationStore
from tests import test_t35_cancel_scoped_store_20261113 as authored


@pytest.fixture()
def store():
    s = OperationStore(':memory:')
    try:
        yield s
    finally:
        s.close()


def test_scoped_success_adapter(store):
    authored.test_t35_proposed_scoped_cancel_success_same_project(store)


def test_scoped_cross_project_adapter(store):
    authored.test_t35_proposed_scoped_cancel_cross_project_rejected_without_write(store)


def test_scoped_divergent_binding_adapter(store):
    authored.test_t35_proposed_scoped_cancel_diverged_sides_rejected(store)


def test_scoped_missing_scope_adapter(store):
    authored.test_t35_proposed_strict_mode_missing_scope_rejected(store)


def test_scoped_invalid_boolean_adapter(store):
    params = inspect.signature(store.cancel_queued).parameters
    assert 'project_id' in params and 'enforce_project_binding' in params
    job_id, operation_id = authored._seed_queued_pair(store, {'project_id': authored.PROJ_A})
    before = authored._full_snapshot(store, job_id, operation_id, [authored.PROJ_A, authored.PROJ_B])
    try:
        outcome = store.cancel_queued(
            job_id, project_id=authored.PROJ_A, enforce_project_binding='yes',
        )
    except TypeError as exc:
        assert 'unexpected keyword argument' not in str(exc)
    except ValueError:
        pass
    else:
        assert outcome is False or (isinstance(outcome, tuple) and outcome[0] is False)
    assert not store.db.in_transaction
    after = authored._full_snapshot(store, job_id, operation_id, [authored.PROJ_A, authored.PROJ_B])
    assert after == before


@pytest.mark.parametrize('status', ('CANCELLED', 'RUNNING'))
def test_scoped_existing_status_checks_binding_before_return(store, status):
    job_id, operation_id = authored._seed_queued_pair(store, {'project_id': authored.PROJ_A})
    if status == 'CANCELLED':
        accepted, code, _ = store.cancel_queued(job_id)
        assert accepted is True and code == 'CANCELLED'
    else:
        store.update_job(job_id, 'RUNNING')
    before = authored._full_snapshot(store, job_id, operation_id, [authored.PROJ_A, authored.PROJ_B])
    assert before['job_row']['status'] == status
    assert before['operation_row']['status'] == status

    foreign = store.cancel_queued(
        job_id, project_id=authored.PROJ_B, enforce_project_binding=True,
    )
    assert isinstance(foreign, tuple) and foreign[0] is False
    assert foreign[2] is None
    assert not store.db.in_transaction
    assert authored._full_snapshot(store, job_id, operation_id, [authored.PROJ_A, authored.PROJ_B]) == before

    own = store.cancel_queued(
        job_id, project_id=authored.PROJ_A, enforce_project_binding=True,
    )
    assert isinstance(own, tuple)
    if status == 'CANCELLED':
        assert own[0] is True and own[1] == 'ALREADY_CANCELLED'
    else:
        assert own[0] is False and own[1] == 'RUNNING'
    assert own[2] is not None and own[2]['job_id'] == job_id
    assert not store.db.in_transaction
    assert authored._full_snapshot(store, job_id, operation_id, [authored.PROJ_A, authored.PROJ_B]) == before
