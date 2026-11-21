"""T35 exact job-log envelope bounds and read-snapshot controls.

These are proposed operational byte contracts, not a published MCP limit.
Each test uses fresh in-memory synthetic records only; no authentication,
actual wire, concurrency, filesystem durability, SQLite C allocation or RSS
claim follows. No historical job, UNKNOWN, Worker, COMSOL, SSH or WAL fixture
is touched.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from comsol_mcp._operation_store import OperationStore

PROJECT = 't35-wire-boundary-project'


def _seed(store: OperationStore, *, with_event: bool = True, large_binding: bool = False) -> dict:
    metadata: dict[str, Any] = {'execution': {'project_id': PROJECT}}
    if large_binding:
        metadata['synthetic_padding'] = 'p' * 4096
    record, reused = store.begin(
        request_id='t35-wire-request', idempotency_key='t35-wire-key',
        request_hash='t35-wire-hash', operation='synthetic.event.producer',
        metadata=metadata,
    )
    assert reused is False
    if with_event:
        store.add_event(record['job_id'], 'SyntheticEvent', {'message': 'small'})
    return record


def _canonical_bytes(job_id: str, events: list[dict], next_offset: int) -> int:
    # Mirrors the daemon control listener serializer plus the canonical
    # catalog route's decimal next_cursor, including its actual digits.
    response = {'success': True, 'data': {'job_id': job_id, 'events': events,
                                           'next_offset': next_offset,
                                           'next_cursor': str(next_offset)}}
    return len(json.dumps(response, allow_nan=False).encode('utf-8'))


def test_t35_wire_cursor_digits_must_fit_even_at_exact_one_byte_edge() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store)
        job_id = record['job_id']
        rows = store.events(job_id, 0, 1)
        assert len(rows) == 1
        exact = _canonical_bytes(job_id, rows, 1)
        # Verify the row's raw scalar fields fit so the test reaches response
        # accounting, not the separate raw-row oversize check.
        raw = store.db.execute(
            'SELECT length(CAST(job_id AS BLOB)) + length(CAST(event AS BLOB)) + '
            'length(CAST(metadata AS BLOB)) + length(CAST(created_at AS BLOB)) '
            'FROM job_events WHERE job_id=?', (job_id,),
        ).fetchone()[0]
        assert raw < exact - 1
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            with pytest.raises(ValueError):
                store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=exact - 1)
        assert not store.db.in_transaction
    finally:
        store.close()


def test_t35_wire_exact_fit_is_accepted() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store)
        job_id = record['job_id']
        rows = store.events(job_id, 0, 1)
        exact = _canonical_bytes(job_id, rows, 1)
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            page = store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=exact)
        assert page == rows
        assert _canonical_bytes(job_id, page, 1) == exact
    finally:
        store.close()


def test_t35_wire_empty_page_envelope_is_also_budgeted() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store, with_event=False)
        job_id = record['job_id']
        exact = _canonical_bytes(job_id, [], 0)
        assert exact > 1
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            with pytest.raises(ValueError):
                store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=exact - 1)
        assert not store.db.in_transaction
    finally:
        store.close()


def test_t35_wire_empty_tail_counts_offset_and_cursor_digits() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store, with_event=False)
        job_id, offset = record['job_id'], 1_000_000
        exact = _canonical_bytes(job_id, [], offset)
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            with pytest.raises(ValueError):
                store.job_log_page_bounded(job_id, offset, 1, max_response_bytes=exact - 1)
        assert not store.db.in_transaction
    finally:
        store.close()


def test_t35_wire_raw_utf8_fits_probe_but_escaped_row_does_not() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store)
        job_id = record['job_id']
        payload = {'marker': 'synthetic-escape-boundary', 'value': '🧪' * 200}
        raw_json = json.dumps(payload, ensure_ascii=False)
        assert store.db.execute('UPDATE job_events SET metadata=? WHERE job_id=?',
                                (raw_json, job_id)).rowcount == 1
        rows = store.events(job_id, 0, 1)
        exact = _canonical_bytes(job_id, rows, 1)
        raw_total = store.db.execute(
            'SELECT length(CAST(job_id AS BLOB)) + length(CAST(event AS BLOB)) + '
            'length(CAST(metadata AS BLOB)) + length(CAST(created_at AS BLOB)) '
            'FROM job_events WHERE job_id=?', (job_id,),
        ).fetchone()[0]
        budget = (raw_total + exact) // 2
        assert raw_total < budget < exact
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            with pytest.raises(ValueError):
                store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=budget)
        assert store.events(job_id, 0, 1) == rows
    finally:
        store.close()


def test_t35_wire_null_optional_event_columns_keep_legacy_shape() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store)
        job_id = record['job_id']
        assert store.db.execute(
            'UPDATE job_events SET event=NULL, metadata=NULL, created_at=NULL WHERE job_id=?',
            (job_id,),
        ).rowcount == 1
        original = store.events(job_id, 0, 1)
        assert original[0]['event'] is None and original[0]['created_at'] is None
        assert original[0]['metadata'] == {}
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound
            page = store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=4096)
        assert page == original
    finally:
        store.close()


def test_t35_wire_borrowed_transaction_is_not_committed_or_rolled_back() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store)
        job_id = record['job_id']
        before = store.db.execute('SELECT metadata FROM jobs WHERE job_id=?', (job_id,)).fetchone()[0]
        store.db.execute('BEGIN IMMEDIATE')
        pending = json.dumps({'execution': {'project_id': PROJECT}, 'temporary': True})
        store.db.execute('UPDATE jobs SET metadata=? WHERE job_id=?', (pending, job_id))
        with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=4096) as bound:
            assert bound and store.db.in_transaction
            assert len(store.job_log_page_bounded(job_id, 0, 1, max_response_bytes=4096)) == 1
        assert store.db.in_transaction
        assert store.db.execute('SELECT metadata FROM jobs WHERE job_id=?', (job_id,)).fetchone()[0] == pending
        store.db.execute('ROLLBACK')
        assert store.db.execute('SELECT metadata FROM jobs WHERE job_id=?', (job_id,)).fetchone()[0] == before
        assert not store.db.in_transaction
    finally:
        if store.db.in_transaction:
            store.db.execute('ROLLBACK')
        store.close()


def test_t35_wire_oversize_binding_is_rejected_before_metadata_fetch() -> None:
    store = OperationStore(':memory:')
    try:
        record = _seed(store, large_binding=True)
        job_id = record['job_id']
        statements: list[str] = []
        # SQLite trace supplies expanded SQL, not placeholders. All bound
        # values here are synthetic IDs; no real database or payload is used.
        store.db.set_trace_callback(statements.append)
        try:
            with store.job_log_read_snapshot(job_id, PROJECT, max_binding_bytes=1024) as bound:
                assert bound is False
        finally:
            store.db.set_trace_callback(None)
        sql = [entry.lower() for entry in statements]
        assert any('length(cast(j.metadata as blob))' in entry for entry in sql)
        assert not any('select j.metadata as job_metadata' in entry for entry in sql)
        assert not any('job_events' in entry or '.result' in entry for entry in sql)
        assert not store.db.in_transaction
    finally:
        store.close()
