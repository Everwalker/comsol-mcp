"""Progress identity type equality through the real reader on fresh memory rows.

JSON booleans/numbers compare equal in Python despite different stored types.
The synthetic fault mutates only this test's event or job metadata; it does not
establish a reachable database attacker, real authorization, COMSOL, or wire.
"""
from __future__ import annotations

import json
import uuid

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._operation_store import OperationStore
from tests import test_t35_progress_compaction_independent_20261113 as fixture


CASES = (
    ('event-revision-true', 'event', 'model_revision', True, 1),
    ('event-revision-false', 'event', 'model_revision', False, 0),
    ('event-generation-true', 'event', 'generation', True, 1),
    ('event-generation-float', 'event', 'generation', 1.0, 1),
    ('event-schema-true', 'event', 'schema_version', True, 1),
    ('job-revision-true', 'job', 'expected_revision', True, 1),
    ('job-revision-false', 'job', 'expected_revision', False, 0),
    ('job-generation-true', 'job', 'generation', True, 1),
    ('job-generation-float', 'job', 'generation', 1.0, 1),
    ('job-schema-true', 'job', 'schema_version', True, 1),
    ('event-generation-different', 'event', 'generation', 2, 1),
    ('job-revision-different', 'job', 'expected_revision', 2, 1),
)


def _capture(store: OperationStore, revision: int) -> dict:
    project = fixture._new_project()
    session = 't35-types-session-' + uuid.uuid4().hex
    execution = {
        'project_id': project,
        'session_id': session,
        'model_ref': {
            'schema_version': 1,
            'session_id': session,
            'server_instance_id': 't35-types-server-' + uuid.uuid4().hex,
            'model_tag': 'synthetic-model',
            'generation': 1,
        },
        'expected_revision': revision,
    }
    record = fixture._admit(store, project, metadata={'operation': 'study.run', 'execution': execution})
    ctx = fixture._append_capture(store, record, project, b't35 nested typed binding\n' * 40)
    store.update_job(ctx['job_id'], 'SUCCEEDED')
    return ctx


def _mutate_events(store: OperationStore, job_id: str, field: str, value: object) -> None:
    rows = store.db.execute('SELECT id, event, metadata FROM job_events WHERE job_id=? ORDER BY id',
                            (job_id,)).fetchall()
    changed = 0
    for row in rows:
        if not row['event'].startswith('COMSOLProgress'):
            continue
        metadata = json.loads(row['metadata'])
        if field == 'model_revision':
            metadata[field] = value
        else:
            metadata['model_ref'][field] = value
        store.db.execute('UPDATE job_events SET metadata=? WHERE id=?',
                         (json.dumps(metadata, sort_keys=True), row['id']))
        changed += 1
    assert changed >= 4


def _mutate_job(store: OperationStore, ctx: dict, field: str, value: object) -> None:
    def update(metadata: dict) -> None:
        execution = metadata['execution']
        if field == 'expected_revision':
            execution[field] = value
        else:
            execution['model_ref'][field] = value
    fixture._mutate_row_metadata(store, 'jobs', 'job_id', ctx['job_id'], update)


def _assert_mutation_visible(store: OperationStore, ctx: dict, where: str,
                             field: str, value: object, revision: int) -> None:
    operation = store.db.execute('SELECT metadata FROM operations WHERE operation_id=?',
                                 (ctx['record']['operation_id'],)).fetchone()
    execution = json.loads(operation['metadata'])['execution']
    assert type(execution['expected_revision']) is int and execution['expected_revision'] == revision
    assert type(execution['model_ref']['generation']) is int
    assert type(execution['model_ref']['schema_version']) is int
    if where == 'job':
        row = store.db.execute('SELECT metadata FROM jobs WHERE job_id=?', (ctx['job_id'],)).fetchone()
        mutated = json.loads(row['metadata'])['execution']
        observed = mutated[field] if field == 'expected_revision' else mutated['model_ref'][field]
        assert type(observed) is type(value) and observed == value
        return
    rows = store.db.execute('SELECT event, metadata FROM job_events WHERE job_id=? ORDER BY id',
                            (ctx['job_id'],)).fetchall()
    progress = [json.loads(row['metadata']) for row in rows if row['event'].startswith('COMSOLProgress')]
    assert len(progress) >= 4
    for metadata in progress:
        observed = metadata[field] if field == 'model_revision' else metadata['model_ref'][field]
        assert type(observed) is type(value) and observed == value


@pytest.mark.parametrize('where,field,value,revision', [
    pytest.param(where, field, value, revision, id=name)
    for name, where, field, value, revision in CASES
])
def test_exact_progress_identity_rejects_json_type_confusion(where, field, value, revision):
    store = OperationStore(':memory:')
    try:
        ctx = _capture(store, revision)
        baseline = fixture._read(store, ctx['project_id'], ctx['job_id'])['solver_output_log']
        assert baseline['status'] == 'CAPTURED'
        assert baseline['sha256'] == fixture._sha(ctx['payload'])
        if where == 'event':
            _mutate_events(store, ctx['job_id'], field, value)
        else:
            _mutate_job(store, ctx, field, value)
        _assert_mutation_visible(store, ctx, where, field, value, revision)
        result = fixture._read(store, ctx['project_id'], ctx['job_id'])['solver_output_log']
        assert result['status'] == 'UNKNOWN' and result['text'] is None, result
    finally:
        store.close()
