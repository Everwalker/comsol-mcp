"""A non-identity numeric representation control for progress capture.

The producer compares execution dictionaries using ordinary Python equality.
A timeout value 30/30.0 is permitted by the control-plane contract and is not
itself part of the 12-field progress identity. All records are new :memory:
fixtures; the producer's binding function is called without a Worker or COMSOL.
"""
from __future__ import annotations

import json
import uuid

from comsol_mcp._comsol_progress_capture import _binding_for_job
from comsol_mcp._operation_store import OperationStore
from tests import test_t35_progress_compaction_independent_20261113 as fixture


def test_nonidentity_timeout_int_float_preserves_producer_and_reader_capture():
    store = OperationStore(':memory:')
    try:
        project = fixture._new_project()
        session = 't35-nonidentity-session-' + uuid.uuid4().hex
        execution = {
            'project_id': project,
            'session_id': session,
            'model_ref': {'schema_version': 1, 'session_id': session,
                          'server_instance_id': 't35-nonidentity-server',
                          'model_tag': 'synthetic-model', 'generation': 1},
            'expected_revision': 1,
            'execution_timeout_s': 30,
        }
        record = fixture._admit(store, project, metadata={'operation': 'study.run', 'execution': execution})
        ctx = fixture._append_capture(store, record, project, b't35 nonidentity control\n' * 40)
        store.update_job(ctx['job_id'], 'SUCCEEDED')
        clean = fixture._read(store, project, ctx['job_id'])['solver_output_log']
        assert clean['status'] == 'CAPTURED'
        fixture._mutate_row_metadata(store, 'jobs', 'job_id', ctx['job_id'],
                                     lambda metadata: metadata['execution'].__setitem__('execution_timeout_s', 30.0))
        job = json.loads(store.db.execute('SELECT metadata FROM jobs WHERE job_id=?',
                                          (ctx['job_id'],)).fetchone()['metadata'])['execution']
        op = json.loads(store.db.execute('SELECT metadata FROM operations WHERE operation_id=?',
                                         (record['operation_id'],)).fetchone()['metadata'])['execution']
        assert type(job['execution_timeout_s']) is float and job['execution_timeout_s'] == 30.0
        assert type(op['execution_timeout_s']) is int and op['execution_timeout_s'] == 30
        binding = _binding_for_job(store, record['operation_id'], 'study.run', op, op['model_ref'], 1)
        assert binding['job_id'] == ctx['job_id'] and binding['model_revision'] == 1
        after = fixture._read(store, project, ctx['job_id'])['solver_output_log']
        assert after['status'] == 'CAPTURED' and after['sha256'] == fixture._sha(ctx['payload'])
    finally:
        store.close()
