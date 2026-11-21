"""T35 malformed shared progress-identity controls, all fresh-memory.

The real producer refuses empty request/idempotency IDs, non-lowercase-hex
64-character request hashes, and unsupported solver operations. This synthetic
fault model corrupts both operation and event records; it does NOT establish a
reachable production writer or authentication against a database writer.
"""
from __future__ import annotations

import json
import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._operation_store import OperationStore
from tests import test_t35_progress_compaction_independent_20261113 as fixture


CASES = (
    ('request-missing', 'request_id', None, 'omit'),
    ('request-empty', 'request_id', '', 'same'),
    ('hash-missing', 'request_hash', None, 'omit'),
    ('hash-not-hex', 'request_hash', 'x' * 64, 'same'),
    ('key-missing', 'idempotency_key', None, 'omit'),
    ('key-empty', 'idempotency_key', '', 'same'),
    ('operation-missing', 'operation', None, 'omit'),
)


@pytest.mark.parametrize('case,value,event_mode,compacted', [
    pytest.param(case, value, mode, compacted, id=f'{shape}-{case_id}')
    for shape, compacted in (('uncompacted', False), ('compacted', True))
    for case_id, case, value, mode in CASES
])
def test_malformed_shared_identity_never_certifies_progress(case, value, event_mode, compacted):
    store = OperationStore(':memory:')
    try:
        ctx = fixture._make_capture(store)
        before = fixture._read(store, ctx['project_id'], ctx['job_id'])
        assert before['solver_output_log']['status'] == 'CAPTURED'
        if compacted:
            assert store.compact_terminal_job_metadata([ctx['job_id']], project_id=ctx['project_id']) == [ctx['job_id']]
            assert fixture._read(store, ctx['project_id'], ctx['job_id'])['solver_output_log']['status'] == 'CAPTURED'

        if case == 'operation':
            fixture._mutate_row_metadata(store, 'operations', 'operation_id', ctx['record']['operation_id'],
                                         lambda metadata: metadata.pop('operation'))
        else:
            assert case in {'request_id', 'request_hash', 'idempotency_key'}
            store.db.execute(f'UPDATE operations SET {case}=? WHERE operation_id=?',
                             (value, ctx['record']['operation_id']))
        rows = store.db.execute(
            'SELECT id,event,metadata FROM job_events WHERE job_id=? ORDER BY id', (ctx['job_id'],),
        ).fetchall()
        updated = 0
        for row in rows:
            if not row['event'].startswith('COMSOLProgress'):
                continue
            metadata = json.loads(row['metadata'])
            assert case in metadata
            if event_mode == 'omit':
                metadata.pop(case)
            else:
                metadata[case] = value
            store.db.execute('UPDATE job_events SET metadata=? WHERE id=?',
                             (json.dumps(metadata, sort_keys=True), row['id']))
            updated += 1
        assert updated >= 4
        observed = fixture._read(store, ctx['project_id'], ctx['job_id'])['solver_output_log']
        assert observed['status'] == 'UNKNOWN' and observed['text'] is None, (
            f'malformed {case} {value!r} must not be certified: {observed["status"]}')
    finally:
        store.close()
