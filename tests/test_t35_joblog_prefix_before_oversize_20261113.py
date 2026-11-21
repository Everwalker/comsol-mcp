"""Synthetic T35: a later oversized event must not hide an earlier prefix.

The 4 MiB daemon cap is a proposed engineering limit, not a published MCP
contract. This tests only new in-memory records; no real authority, transport,
concurrency, filesystem durability, SQLite C allocation or RSS is exercised.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore


PROJECT = 't35-prefix-before-oversize'


class _Receiver:
    def __init__(self, store: OperationStore) -> None:
        self.store = store

    @staticmethod
    def _error(code: str, message: str, **details: object) -> dict:
        return ControlDaemon._error(code, message, **details)


def _seed(store: OperationStore, *, big_size: int) -> str:
    record, reused = store.begin(
        request_id='t35-prefix-request', idempotency_key='t35-prefix-key',
        request_hash='t35-prefix-hash', operation='job.synthetic-producer',
        metadata={'execution': {'project_id': PROJECT}},
    )
    assert not reused
    job_id = record['job_id']
    store.add_event(job_id, 'small-first', {'marker': 'first'})
    store.add_event(job_id, 'synthetic-oversize-' + 'x' * big_size, {'marker': 'large'})
    store.add_event(job_id, 'small-last', {'marker': 'last'})
    return job_id


def test_t35_store_short_prefix_before_raw_oversize_then_refuse_same_offset() -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _seed(store, big_size=5000)
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            page = store.job_log_page_bounded(job_id, 0, 100, max_response_bytes=1024)
            assert [row['event'] for row in page] == ['small-first']
            with pytest.raises(ValueError, match='response byte budget'):
                store.job_log_page_bounded(job_id, 1, 100, max_response_bytes=1024)
        assert not store.db.in_transaction
        assert store.db.execute('SELECT COUNT(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0] == 3
    finally:
        store.close()


def test_t35_scoped_daemon_returns_prefix_then_refuses_oversize_without_echo() -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _seed(store, big_size=4 * 1024 * 1024)
        receiver = _Receiver(store)
        statements: list[str] = []
        store.db.set_trace_callback(statements.append)
        try:
            first = ControlDaemon._control_read(
                receiver, 'job_log', {'job_id': job_id, 'offset': 0, 'limit': 100},  # type: ignore[arg-type]
                scoped_project_id=PROJECT,
            )
            assert first['success'] is True
            assert first['data']['next_offset'] == 1
            assert [row['event'] for row in first['data']['events']] == ['small-first']
            fetches = [s for s in statements if s.lstrip().upper().startswith('SELECT ID,JOB_ID,EVENT,METADATA,CREATED_AT FROM JOB_EVENTS')]
            assert len(fetches) == 1, 'only the admitted small event should be fetched'
            statements.clear()
            blocked = ControlDaemon._control_read(
                receiver, 'job_log', {'job_id': job_id, 'offset': 1, 'limit': 100},  # type: ignore[arg-type]
                scoped_project_id=PROJECT,
            )
            assert blocked['success'] is False and blocked['data'] == {}
            assert blocked['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
            assert blocked['error']['safe_retry'] is False
            assert 'synthetic-oversize-' not in str(blocked)
            assert not any(s.lstrip().upper().startswith('SELECT ID,JOB_ID,EVENT,METADATA,CREATED_AT FROM JOB_EVENTS') for s in statements)
        finally:
            store.db.set_trace_callback(None)
        assert store.db.execute('SELECT COUNT(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0] == 3
        assert not store.db.in_transaction
    finally:
        store.close()
