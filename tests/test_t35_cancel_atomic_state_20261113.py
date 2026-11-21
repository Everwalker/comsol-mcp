"""Synthetic QUEUED cancellation must not commit a split job/operation state.

The inconsistent operation status is injected into a brand-new in-memory ledger
only. This models pre-existing row drift; it does not assert that a real worker
or historical job was in this state. No daemon, network, native process, model,
UNKNOWN job or file-backed database is used.
"""
from __future__ import annotations

from comsol_mcp._operation_store import OperationStore


def _new_job(store: OperationStore, tag: str) -> dict:
    record, reused = store.begin(
        request_id=f't35-cancel-atomic-request-{tag}',
        idempotency_key=f't35-cancel-atomic-key-{tag}',
        request_hash=f't35-cancel-atomic-hash-{tag}',
        operation='study.run',
        metadata={'execution': {'project_id': 't35-cancel-atomic-project'}},
    )
    assert reused is False
    store.add_event(record['job_id'], 'SyntheticAudit', {'tag': tag})
    return record


def _snapshot(store: OperationStore, job_id: str) -> tuple[dict, list[dict]]:
    job = store.job(job_id)
    assert job is not None and job['operation'] is not None
    return job, store.events(job_id, 0, 100)


def test_cancel_queued_consistent_pair_updates_both_rows_and_event() -> None:
    store = OperationStore(':memory:')
    try:
        record = _new_job(store, 'positive')
        accepted, code, _ = store.cancel_queued(record['job_id'], reason='synthetic')
        assert accepted is True and code == 'CANCELLED'
        job, events = _snapshot(store, record['job_id'])
        assert job['status'] == job['operation']['status'] == 'CANCELLED'
        assert job['operation']['result']['error']['code'] == 'OPERATION_CANCELLED'
        assert [event['event'] for event in events] == ['SyntheticAudit', 'CANCELLED']
        assert not store.db.in_transaction
    finally:
        store.close()


def test_cancel_queued_operation_status_drift_refuses_without_partial_write() -> None:
    store = OperationStore(':memory:')
    try:
        record = _new_job(store, 'drift')
        changed = store.db.execute(
            "UPDATE operations SET status='RUNNING' WHERE operation_id=?",
            (record['operation_id'],),
        )
        assert changed.rowcount == 1
        before = _snapshot(store, record['job_id'])
        assert before[0]['status'] == 'QUEUED'
        assert before[0]['operation']['status'] == 'RUNNING'
        accepted, _code, _updated = store.cancel_queued(record['job_id'], reason='synthetic')
        assert accepted is False, 'cancel must reject an inconsistent pair atomically'
        assert _snapshot(store, record['job_id']) == before
        assert not store.db.in_transaction
    finally:
        store.close()
