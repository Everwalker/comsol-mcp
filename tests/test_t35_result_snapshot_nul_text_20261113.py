"""Synthetic regression for TEXT results whose first character is NUL.

SQLite length(TEXT) stops at raw NUL. A nonempty invalid result must not be
confused with an empty result and replaced by lower-priority event evidence.
All rows are created in fresh :memory: ledgers; this is not principal,
real concurrency, disk durability or whole-process memory evidence.
"""
from __future__ import annotations

import json

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp import _g3_solver_ops as solver
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore


@pytest.fixture
def record():
    store = OperationStore(':memory:')
    project = 't35-nul-synthetic-project'
    operation, reused = store.begin(
        request_id='t35-nul-synthetic-request',
        idempotency_key='t35-nul-synthetic-key',
        request_hash='t35-nul-synthetic-hash',
        operation='study.run',
        metadata={'operation': 'study.run', 'arguments': {},
                  'execution': {'project_id': project, 'session_id': 't35-nul-session'}},
    )
    assert reused is False
    job_id, operation_id = operation['job_id'], operation['operation_id']
    store.add_event(job_id, 'T35NulDecoy', {
        'exception_chain': [{'message': 't35-nul-event-decoy'}],
    })
    accepted, status = store.finish(operation_id, status='FAILED', result={'success': False})
    assert accepted is True and status == 'FAILED'
    try:
        yield store, project, job_id, operation_id
    finally:
        store.close()


def _put_and_snapshot(store, job_id, operation_id, value):
    cursor = store.db.execute('UPDATE operations SET result=? WHERE operation_id=?',
                              (value, operation_id))
    assert cursor.rowcount == 1
    return _snapshot(store, job_id, operation_id)


def _snapshot(store, job_id, operation_id):
    job = store.db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
    op = store.db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    events = store.db.execute('SELECT * FROM job_events WHERE job_id=? ORDER BY id',
                              (job_id,)).fetchall()
    assert job is not None and op is not None
    return dict(job), dict(op), [dict(event) for event in events]


@pytest.mark.parametrize('raw', ['\x00', '\x00{}', '{}\x00'], ids=['nul-only', 'leading-nul', 'trailing-nul'])
def test_raw_nul_text_must_fail_closed_before_event_fallback(record, raw):
    store, project, job_id, operation_id = record
    before = _put_and_snapshot(store, job_id, operation_id, raw)
    assert before[1]['result'] == raw
    assert store.db.execute('SELECT typeof(result) FROM operations WHERE operation_id=?',
                            (operation_id,)).fetchone()[0] == 'text'
    if raw.startswith('\x00'):
        assert store.db.execute('SELECT length(result) FROM operations WHERE operation_id=?',
                                (operation_id,)).fetchone()[0] == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    with pytest.raises(ExecutionContractError) as refused:
        solver.solver_log_read(store, project, {'job_id': job_id, 'limit': 10})
    assert refused.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 't35-nul-event-decoy' not in str(refused.value)
    assert _snapshot(store, job_id, operation_id) == before
    assert store.db.in_transaction is False


def test_true_empty_text_keeps_legacy_event_fallback(record):
    store, project, job_id, operation_id = record
    before = _put_and_snapshot(store, job_id, operation_id, '')
    page = solver.solver_log_read(store, project, {'job_id': job_id, 'limit': 10})
    assert page['exception_chain']['status'] == 'CAPTURED'
    assert page['exception_chain']['source'].startswith('event ')
    assert page['exception_chain']['items'] == [{'message': 't35-nul-event-decoy'}]
    assert _snapshot(store, job_id, operation_id) == before
    assert store.db.in_transaction is False


def test_json_escaped_nul_is_valid_and_keeps_stored_error_precedence(record):
    store, project, job_id, operation_id = record
    raw = json.dumps({'error': {'cause_chain': [{'message': 'left\x00right'}]}})
    assert '\\u0000' in raw and '\x00' not in raw
    before = _put_and_snapshot(store, job_id, operation_id, raw)
    page = solver.solver_log_read(store, project, {'job_id': job_id, 'limit': 10})
    assert page['exception_chain'] == {
        'status': 'CAPTURED', 'source': 'stored result.error.cause_chain',
        'items': [{'message': 'left\x00right'}],
    }
    assert _snapshot(store, job_id, operation_id) == before
    assert store.db.in_transaction is False
