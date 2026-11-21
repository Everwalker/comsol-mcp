"""Real producer-binding to real progress reader for supported solver routes.

This executes only `_binding_for_job` and synthetic event persistence, not a
Worker, COMSOL, native capture, project principal, or MCP transport. Each
case owns a fresh :memory: ledger and forms valid producer-shaped lifecycle
records from the binding returned by the actual producer function.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._comsol_progress_capture import _binding_for_job
from comsol_mcp._g3_solver_ops import solver_log_read
from comsol_mcp._operation_store import OperationStore


CASES = tuple((route, operation) for route in ('direct', 'registry_call', 'operation_call')
              for operation in ('study.run', 'mesh.convergence_study'))


@pytest.mark.parametrize('route,operation', [
    pytest.param(route, operation, id=f'{route}-{operation}') for route, operation in CASES
])
def test_actual_producer_binding_is_accepted_by_solver_log_reader(route, operation):
    store = OperationStore(':memory:')
    try:
        token = uuid.uuid4().hex
        project, session = 't35-producer-project-' + token, 't35-producer-session-' + token
        model_ref = {'schema_version': 1, 'session_id': session,
                     'server_instance_id': 't35-producer-server-' + token,
                     'model_tag': 'synthetic-model', 'generation': 1}
        execution = {'project_id': project, 'session_id': session,
                     'model_ref': model_ref, 'expected_revision': 1}
        persisted_operation = operation if route == 'direct' else route
        arguments = {} if route == 'direct' else {'operation_id': operation, 'arguments': {}}
        metadata = {'operation': persisted_operation, 'arguments': arguments, 'execution': execution}
        request_hash = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode('utf-8')).hexdigest()
        record, replayed = store.begin(request_id='t35-producer-request-' + token,
                                       idempotency_key='t35-producer-key-' + token,
                                       request_hash=request_hash, operation=persisted_operation,
                                       metadata=metadata)
        assert replayed is False
        job_id = record['job_id']
        binding = _binding_for_job(store, record['operation_id'], operation, execution, model_ref, 1)
        assert binding['job_id'] == job_id and binding['operation'] == operation
        content = ('synthetic progress for ' + operation + '\n').encode('utf-8')
        digest = hashlib.sha256(content).hexdigest()
        store.update_job(job_id, 'RUNNING')
        store.add_event(job_id, 'COMSOLProgressCaptureRequested',
                        {**binding, 'capture_state': 'REQUESTED', 'byte_limit': 1048576, 'chunk_bytes': 8192})
        store.add_event(job_id, 'COMSOLProgressCaptureActivated',
                        {**binding, 'capture_state': 'ACTIVE'})
        store.add_event(job_id, 'COMSOLProgressChunk',
                        {**binding, 'chunk_index': 0, 'byte_offset': 0, 'byte_length': len(content),
                         'content_encoding': 'base64', 'content_base64': base64.b64encode(content).decode('ascii'),
                         'chunk_sha256': digest})
        store.add_event(job_id, 'COMSOLProgressCaptureCompleted',
                        {**binding, 'capture_state': 'CAPTURED', 'captured_bytes': len(content),
                         'file_size_bytes': len(content), 'chunk_count': 1, 'captured_sha256': digest,
                         'truncated': False, 'cleanup_verified': True, 'scratch_retained': False,
                         'complete': True, 'captured_sha256_scope': 'complete_file'})
        store.update_job(job_id, 'SUCCEEDED')
        actual = solver_log_read(store, project, {'job_id': job_id})['solver_output_log']
        assert actual['status'] == 'CAPTURED'
        assert actual['sha256'] == digest and actual['byte_length'] == len(content)
    finally:
        store.close()
