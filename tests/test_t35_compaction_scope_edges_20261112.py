"""Synthetic metadata-compaction binding and atomic-batch edge controls.

Only new in-memory stores and synthetic jobs are used. These tests do not
establish real principal, MCP, filesystem-durability or native-model behavior.
"""
from __future__ import annotations

import sqlite3

import pytest

from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import JobCleanupError, OperationStore
from tests.test_t35_job_read_project_scope_20261112 import (
    PROJECT_A,
    PROJECT_B,
    _create_synthetic_job,
)


def _raw_state(store: OperationStore, job_ids: list[str]) -> list[tuple[tuple, list]]:
    states = []
    with store.lock:
        for job_id in job_ids:
            row = store.db.execute(
                'SELECT j.status,j.metadata,o.metadata,o.result '
                'FROM jobs j JOIN operations o ON o.operation_id=j.operation_id '
                'WHERE j.job_id=?',
                (job_id,),
            ).fetchone()
            assert row is not None
            states.append((tuple(row), store.events(job_id, offset=0, limit=100)))
    return states


@pytest.mark.parametrize(
    ('corrupt_side', 'raw_metadata'),
    [('job', '{invalid-json'), ('operation', sqlite3.Binary(b'\xff'))],
    ids=['job-malformed-json', 'operation-invalid-utf8'],
)
def test_scoped_corrupt_binding_refuses_entire_batch_without_mutation(
    corrupt_side: str, raw_metadata: object,
) -> None:
    store = OperationStore(':memory:')
    try:
        first = _create_synthetic_job(
            store, project_id=PROJECT_A, tag=f'compact-atomic-good-{corrupt_side}',
            result_marker='T35-COMPACT-ATOMIC-GOOD',
        )
        bad = _create_synthetic_job(
            store, project_id=PROJECT_A, tag=f'compact-atomic-bad-{corrupt_side}',
            result_marker='T35-COMPACT-ATOMIC-BAD',
        )
        bad_job = store.job(bad)
        assert bad_job is not None
        if corrupt_side == 'job':
            store.db.execute('UPDATE jobs SET metadata=? WHERE job_id=?', (raw_metadata, bad))
        else:
            store.db.execute(
                'UPDATE operations SET metadata=? WHERE operation_id=?',
                (raw_metadata, bad_job['operation_id']),
            )
        before = _raw_state(store, [first, bad])
        with pytest.raises(JobCleanupError) as error:
            store.compact_terminal_job_metadata([first, bad], project_id=PROJECT_A)
        assert error.value.code == 'PROJECT_SCOPE_MISMATCH'
        assert _raw_state(store, [first, bad]) == before
        assert not store.db.in_transaction
    finally:
        store.close()


def test_unscoped_ambiguous_binding_refuses_without_mutation() -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag='compact-unscoped-ambiguous',
            result_marker='T35-COMPACT-UNSCOPED-AMBIGUOUS',
        )
        store.update_job(job_id, 'SUCCEEDED', {'project_id': PROJECT_B})
        before = _raw_state(store, [job_id])
        with pytest.raises(JobCleanupError) as error:
            store.compact_terminal_job_metadata([job_id])
        assert error.value.code == 'PROJECT_BINDING_UNKNOWN'
        assert _raw_state(store, [job_id]) == before
        assert not store.db.in_transaction
    finally:
        store.close()


def test_unscoped_consistent_binding_preserves_result_and_records_event() -> None:
    store = OperationStore(':memory:')
    try:
        marker = 'T35-COMPACT-UNSCOPED-VALID'
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag='compact-unscoped-valid', result_marker=marker,
        )
        before_events = store.events(job_id, offset=0, limit=100)
        assert store.compact_terminal_job_metadata([job_id]) == [job_id]
        job = store.job(job_id)
        assert job is not None
        assert consistent_project_id(job['metadata'], job['operation']['metadata']) == PROJECT_A
        assert job['result']['data']['marker'] == marker
        after_events = store.events(job_id, offset=0, limit=100)
        assert len(after_events) == len(before_events) + 1
        assert after_events[-1]['event'] == 'JobCacheCompacted'
        assert after_events[:-1] == before_events
    finally:
        store.close()
