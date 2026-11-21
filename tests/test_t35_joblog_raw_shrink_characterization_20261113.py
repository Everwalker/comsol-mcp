"""T35 conservative raw-byte admission on valid noncanonical JSON.

Fresh in-memory fixture only. A deliberately whitespace-padded, syntactically
valid metadata value models a historical/noncanonical row; this does not
assert that the current event writer normally produces that padding. Limits are
reduced for the experiment and are proposed engineering values, not a public
MCP limit. No daemon, solver, Worker, COMSOL, network or disk database.
"""
from __future__ import annotations

import json
import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._operation_store import OperationStore


def test_valid_raw_metadata_can_be_refused_even_when_normalized_envelope_fits():
    store = OperationStore(':memory:')
    try:
        project = 't35-raw-shrink-project'
        record, reused = store.begin(
            request_id='t35-raw-shrink-request', idempotency_key='t35-raw-shrink-key',
            request_hash='t35-raw-shrink-hash', operation='synthetic.log',
            metadata={'execution': {'project_id': project}},
        )
        assert not reused
        job_id = record['job_id']
        store.add_event(job_id, 'SyntheticPrefix', {'index': 0})
        raw_metadata = '{"value":' + ' ' * 4096 + '0}'
        assert json.loads(raw_metadata) == {'value': 0}
        store.db.execute('INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)',
                         (job_id, 'SyntheticWhitespace', raw_metadata))
        store.add_event(job_id, 'SyntheticSuffix', {'index': 2})
        raw_row = dict(store.db.execute(
            'SELECT id,job_id,event,metadata,created_at FROM job_events WHERE job_id=? AND event=?',
            (job_id, 'SyntheticWhitespace'),
        ).fetchone())
        normalized = {**raw_row, 'metadata': json.loads(raw_row['metadata'])}
        max_bytes = 1024
        envelope = {'success': True, 'data': {'job_id': job_id, 'events': [normalized],
                                               'next_offset': 2, 'next_cursor': '2'}}
        assert len(raw_metadata.encode('utf-8')) > max_bytes
        assert len(json.dumps(envelope, allow_nan=False).encode('ascii')) < max_bytes

        with store.job_log_read_snapshot(job_id, project, max_binding_bytes=max_bytes) as bound:
            assert bound is True
            prefix = store.job_log_page_bounded(job_id, 0, 3, max_response_bytes=max_bytes)
            assert [event['event'] for event in prefix] == ['SyntheticPrefix']
            with pytest.raises(ValueError, match='exceeds the response byte budget'):
                store.job_log_page_bounded(job_id, 1, 3, max_response_bytes=max_bytes)
            suffix = store.job_log_page_bounded(job_id, 2, 3, max_response_bytes=max_bytes)
            assert [event['event'] for event in suffix] == ['SyntheticSuffix']
        assert not store.db.in_transaction
    finally:
        store.close()
