"""Strict synthetic progress-capture continuity across terminal compaction.

This independent test file reuses the author's fresh-memory fixture helpers;
it does not modify that file. A genuine, complete, bound capture should retain
its pre-compaction verified summary, not merely degrade honestly to UNKNOWN.
Malformed captures and a changed execution identity must still fail closed.
These tests call the real in-process reader against new in-memory ledgers only;
they do not establish a real project principal, MCP transport or COMSOL run.
"""
from __future__ import annotations

import copy

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._operation_store import OperationStore
from tests.test_t35_solver_progress_after_compaction_20261113 import (
    CAUSE_CHAIN,
    FAILED_RESULT,
    PROJECT_A,
    SUCCEEDED_RESULT,
    _add_progress_capture,
    _finish,
    _recompute_capture,
    _start_job,
    _stored_rows,
)


@pytest.mark.parametrize('status', ('FAILED', 'SUCCEEDED'))
def test_verified_progress_summary_survives_real_memory_compaction(status: str) -> None:
    store = OperationStore(':memory:')
    try:
        record = _start_job(store, project=PROJECT_A, tag=f'strict-{status.lower()}')
        expected_sha, expected_bytes = _add_progress_capture(store, record, project=PROJECT_A)
        result = FAILED_RESULT if status == 'FAILED' else SUCCEEDED_RESULT
        _finish(store, record, status=status, result=result)
        before_rows = _stored_rows(store, record['job_id'])
        before = ops.solver_log_read(store, PROJECT_A, {'job_id': record['job_id'], 'limit': 100})
        baseline = before['solver_output_log']
        assert baseline['status'] == 'CAPTURED' and baseline['sha256'] == expected_sha
        assert store.compact_terminal_job_metadata([record['job_id']], project_id=PROJECT_A) == [record['job_id']]
        job = store.job(record['job_id'])
        assert job is not None
        assert job['status'] == status and job['result'] == result
        assert 'execution' not in job['metadata']
        after_rows = _stored_rows(store, record['job_id'])
        assert after_rows[:-1] == before_rows
        assert after_rows[-1]['event'] == 'JobCacheCompacted'
        after = ops.solver_log_read(store, PROJECT_A, {'job_id': record['job_id'], 'limit': 100})
        assert after['job_status'] == status
        assert after['event_count'] == len(after_rows) and after['next_cursor'] is None
        assert _recompute_capture(after['events']) == (expected_sha, expected_bytes)
        assert after['solver_output_log'] == baseline
        if status == 'FAILED':
            assert after['exception_chain']['items'] == CAUSE_CHAIN
        else:
            assert after['exception_chain']['status'] == 'NOT_CAPTURED'
    finally:
        store.close()


def test_verified_progress_remains_captured_on_pre_compaction_cursor() -> None:
    store = OperationStore(':memory:')
    try:
        record = _start_job(store, project=PROJECT_A, tag='strict-cursor')
        expected_sha, _ = _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status='FAILED', result=FAILED_RESULT)
        first = ops.solver_log_read(store, PROJECT_A, {'job_id': record['job_id'], 'limit': 2})
        baseline = first['solver_output_log']
        assert baseline['status'] == 'CAPTURED' and baseline['sha256'] == expected_sha
        assert first['next_cursor'] is not None
        assert store.compact_terminal_job_metadata([record['job_id']], project_id=PROJECT_A) == [record['job_id']]
        next_page = ops.solver_log_read(store, PROJECT_A, {
            'job_id': record['job_id'], 'cursor': first['next_cursor'],
        })
        assert next_page['snapshot'] == first['snapshot']
        assert next_page['solver_output_log'] == baseline
    finally:
        store.close()


@pytest.mark.parametrize(
    ('tamper', 'expected_reason'),
    (
        ('duplicate-completion', 'progress capture has duplicate or incomplete lifecycle records'),
        ('chunk-digest', 'progress chunk length or SHA-256 does not match its bytes'),
    ),
)
def test_corrupt_progress_receipt_remains_rejected_for_same_reason_after_compaction(
    tamper: str, expected_reason: str,
) -> None:
    store = OperationStore(':memory:')
    try:
        record = _start_job(store, project=PROJECT_A, tag=f'strict-{tamper}')
        _add_progress_capture(
            store, record, project=PROJECT_A,
            extra_completion=tamper == 'duplicate-completion',
            corrupt_chunk_index=1 if tamper == 'chunk-digest' else None,
        )
        _finish(store, record, status='FAILED', result=FAILED_RESULT)
        before_rows = _stored_rows(store, record['job_id'])
        before = ops.solver_log_read(store, PROJECT_A, {'job_id': record['job_id'], 'limit': 100})
        assert before['solver_output_log']['status'] == 'UNKNOWN'
        assert before['solver_output_log']['reason'] == expected_reason
        assert store.compact_terminal_job_metadata([record['job_id']], project_id=PROJECT_A) == [record['job_id']]
        assert _stored_rows(store, record['job_id'])[:-1] == before_rows
        after = ops.solver_log_read(store, PROJECT_A, {'job_id': record['job_id'], 'limit': 100})
        assert after['solver_output_log']['status'] == 'UNKNOWN'
        assert after['solver_output_log']['reason'] == expected_reason
        assert 'sha256' not in after['solver_output_log']
    finally:
        store.close()


def test_compacted_progress_with_changed_operation_revision_is_not_certified() -> None:
    store = OperationStore(':memory:')
    try:
        record = _start_job(store, project=PROJECT_A, tag='strict-revision')
        _add_progress_capture(store, record, project=PROJECT_A)
        _finish(store, record, status='SUCCEEDED', result=SUCCEEDED_RESULT)
        assert store.compact_terminal_job_metadata([record['job_id']], project_id=PROJECT_A) == [record['job_id']]
        job = copy.deepcopy(store.job(record['job_id']))
        assert job is not None
        rows = _stored_rows(store, record['job_id'])
        job['operation']['metadata']['execution']['expected_revision'] += 1
        assert ops._job_project_binding(job, PROJECT_A)
        summary = ops._progress_log_summary(job, rows, PROJECT_A)
        assert summary['status'] == 'UNKNOWN' and 'sha256' not in summary
        assert summary['reason'] == 'progress producer event identity differs from the exact durable job binding'
    finally:
        store.close()
