"""Synthetic T35 strict-list bounds and malformed metadata controls.

Fresh in-memory stores and synthetic IDs only. tracemalloc observes Python
allocations, not SQLite C allocation, RSS, physical disk or other processes.
"""
from __future__ import annotations

import gc
import sqlite3
import tracemalloc

from comsol_mcp._operation_store import OperationStore
from tests.test_t35_job_scope_store_20261112 import PROJECT_A, _begin_pair


def _bad_blob_job(store: OperationStore, tag: str) -> str:
    job_id = _begin_pair(store, tag, {'project_id': PROJECT_A})
    with store.lock:
        store.db.execute('UPDATE jobs SET metadata=? WHERE job_id=?', (sqlite3.Binary(b'\xff'), job_id))
    return job_id


def test_snapshot_treats_invalid_utf8_metadata_as_unbound() -> None:
    store = OperationStore(':memory:')
    try:
        bad_id = _bad_blob_job(store, 'bad-utf8-snapshot')
        with store.project_job_read_snapshot(bad_id, PROJECT_A) as bound:
            assert bound is False
        assert store.db.in_transaction is False
        good_id = _begin_pair(store, 'after-bad-utf8-snapshot', {'project_id': PROJECT_A})
        with store.project_job_read_snapshot(good_id, PROJECT_A) as bound:
            assert bound is True
    finally:
        store.close()


def test_strict_list_skips_invalid_utf8_and_returns_matching_rows() -> None:
    store = OperationStore(':memory:')
    try:
        bad_id = _bad_blob_job(store, 'bad-utf8-list')
        good_id = _begin_pair(store, 'good-after-bad-utf8', {'project_id': PROJECT_A})
        jobs = store.list_jobs(project_id=PROJECT_A, enforce_project_binding=True)
        assert jobs.total == 1
        assert [job['job_id'] for job in jobs] == [good_id]
        assert bad_id not in {job['job_id'] for job in jobs}
        assert store.db.in_transaction is False
    finally:
        store.close()


def test_strict_list_deep_offset_does_not_retain_skipped_ids_in_python() -> None:
    store = OperationStore(':memory:')
    try:
        # Insertion occurs before tracemalloc starts, so the measured delta is
        # the list call, not fixture creation. Long synthetic IDs amplify any
        # accidental retention of all matching IDs before the requested page.
        count = 6001
        suffix = 't' * 768
        metadata = '{"project_id":"' + PROJECT_A + '"}'
        with store.lock:
            store.db.executemany(
                'INSERT INTO operations(operation_id,operation,status,metadata) VALUES(?,?,?,?)',
                ((f'op-{i}', 'synthetic', 'QUEUED', metadata) for i in range(count)),
            )
            store.db.executemany(
                'INSERT INTO jobs(job_id,operation_id,status,metadata) VALUES(?,?,?,?)',
                ((f'job-{i:05d}-{suffix}', f'op-{i}', 'QUEUED', metadata) for i in range(count)),
            )

        def measure(offset: int) -> tuple[int, str, int]:
            gc.collect()
            tracemalloc.start()
            try:
                page = store.list_jobs(offset=offset, limit=1, project_id=PROJECT_A, enforce_project_binding=True)
                _, peak = tracemalloc.get_traced_memory()
                assert len(page) == 1
                return peak, page[0]['job_id'], page.total
            finally:
                tracemalloc.stop()

        first_peak, first_id, first_total = measure(0)
        far_peak, far_id, far_total = measure(count - 1)
        assert first_total == far_total == count
        assert first_id == f'job-{count - 1:05d}-{suffix}'
        assert far_id == f'job-{0:05d}-{suffix}'
        # Internal control for this fixture, not a product-wide RSS promise.
        assert far_peak <= first_peak + 1_500_000, (first_peak, far_peak)
    finally:
        store.close()
