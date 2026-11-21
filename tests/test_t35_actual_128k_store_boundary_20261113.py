"""T35 historical 128,000-event boundary with task-owned memory databases.

Bulk SQL seeds synthetic events; the actual store methods perform reads and
terminal recording after the boundary. No daemon, solver, Worker, COMSOL,
network, historical job, WAL or file-backed SQLite database is used. The
tracemalloc tolerance is a proposed Python-allocation regression bound, not
an RSS, SQLite-C-memory, published transport or production-load guarantee.
"""
from __future__ import annotations

import hashlib
import json
import tracemalloc

from comsol_mcp._operation_store import OperationStore

PROJECT = 't35-threshold-owned-project'
OLD_EVENT_LIMIT = 128_000


def _begin(store: OperationStore, label: str) -> dict:
    row, reused = store.begin(
        request_id=f't35-128k-{label}', idempotency_key=f't35-128k-{label}',
        request_hash=f't35-128k-{label}', operation='synthetic.threshold',
        metadata={'execution': {'project_id': PROJECT}},
    )
    assert reused is False
    return row


def _seed_events(store: OperationStore, job_id: str, count: int) -> None:
    store.db.execute('BEGIN IMMEDIATE')
    try:
        store.db.executemany(
            'INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)',
            ((job_id, 'SyntheticLine', json.dumps({'index': index})) for index in range(count)),
        )
        store.db.execute('COMMIT')
    except BaseException:
        if store.db.in_transaction:
            store.db.execute('ROLLBACK')
        raise
    assert store.db.execute('SELECT count(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0] == count


def test_actual_128001_events_precede_terminal_and_selected_pages_remain_readable() -> None:
    store = OperationStore(':memory:')
    try:
        job = _begin(store, 'terminal')
        job_id = job['job_id']
        _seed_events(store, job_id, OLD_EVENT_LIMIT + 1)
        legacy = store.collect_job_event_snapshot(job_id)
        assert legacy['status'] == 'INCOMPLETE'
        assert legacy['expected_event_count'] == OLD_EVENT_LIMIT + 1
        assert legacy['expected_page_count'] == 129
        assert 'job event snapshot exceeds the bounded page limit' in legacy['issues']

        accepted, status = store.finish(job['operation_id'], status='SUCCEEDED',
                                        result={'success': True, 'marker': 'synthetic-terminal'})
        assert accepted is True and status == 'SUCCEEDED'
        store.add_event(job_id, 'SyntheticTerminal', {'status': 'SUCCEEDED'})
        persisted = store.job(job_id)
        assert persisted is not None and persisted['status'] == 'SUCCEEDED'
        assert persisted['result'] == {'success': True, 'marker': 'synthetic-terminal'}
        assert store.db.execute('SELECT count(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0] == OLD_EVENT_LIMIT + 2

        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4 * 1024 * 1024) as bound:
            assert bound is True and store.db.in_transaction
            first = store.job_log_page_bounded(job_id, 0, 2, max_response_bytes=4 * 1024 * 1024)
            tail = store.job_log_page_bounded(job_id, OLD_EVENT_LIMIT - 1, 3, max_response_bytes=4 * 1024 * 1024)
            assert [item['metadata']['index'] for item in first] == [0, 1]
            assert [item['metadata']['index'] for item in tail[:2]] == [OLD_EVENT_LIMIT - 1, OLD_EVENT_LIMIT]
            assert tail[-1]['event'] == 'SyntheticTerminal'
            assert tail[-1]['metadata'] == {'status': 'SUCCEEDED'}
            assert [item['id'] for item in first + tail] == sorted(item['id'] for item in first + tail)
        assert not store.db.in_transaction
    finally:
        store.close()


def _measure_fold(store: OperationStore, job_id: str, count: int) -> tuple[dict, int]:
    ids = hashlib.sha256()
    events = hashlib.sha256(b'[')
    seen = 0

    def visit(row: dict) -> None:
        nonlocal seen
        assert row['job_id'] == job_id and row['event'] == 'SyntheticLine'
        assert row['metadata'] == {'index': seen}
        if seen:
            ids.update(b',')
            events.update(b',')
        ids.update(str(row['id']).encode('ascii'))
        events.update(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8'))
        seen += 1

    tracemalloc.start()
    try:
        folded = store.fold_job_event_prefix(job_id, snapshot_count=None, snapshot_last_id=None,
                                             max_row_bytes=4096, visitor=visit)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    events.update(b']')
    assert seen == count
    assert folded['complete'] is True and folded['status'] == 'COMPLETE'
    assert folded['expected_event_count'] == folded['fetched_event_count'] == count
    assert folded['id_sha256'] == ids.hexdigest()
    assert folded['events_sha256'] == events.hexdigest()
    return folded, peak


def test_actual_128001_event_fold_is_complete_and_python_peak_does_not_scale_with_count() -> None:
    store = OperationStore(':memory:')
    try:
        small = _begin(store, 'small')['job_id']
        large = _begin(store, 'large')['job_id']
        _seed_events(store, small, 1001)
        _seed_events(store, large, OLD_EVENT_LIMIT + 1)
        _, small_peak = _measure_fold(store, small, 1001)
        _, large_peak = _measure_fold(store, large, OLD_EVENT_LIMIT + 1)
        print(f'T35_128K_SYNTHETIC_PYTHON_PEAK small={small_peak} large={large_peak} delta={large_peak-small_peak}')
        assert large_peak - small_peak <= 4 * 1024 * 1024
    finally:
        store.close()
