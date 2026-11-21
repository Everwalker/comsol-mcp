"""Synthetic T35 compaction must not launder project-binding provenance.

Only new in-memory SQLite stores and synthetic records are touched. The store's
real metadata compaction and the real pure binding predicate are exercised;
this is not a public MCP, real grant, persistent disk or native-model test.
"""
from __future__ import annotations

from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import JobCleanupError, OperationStore
from tests.test_t35_job_read_project_scope_20261112 import (
    PROJECT_A, PROJECT_B, _FakeProjectAuthority, _InertDispatchReceiver,
    _canonical_route, _create_synthetic_job,
)


def _binding(store: OperationStore, job_id: str) -> str | None:
    job = store.job(job_id)
    assert job is not None and job['operation'] is not None
    return consistent_project_id(job['metadata'], job['operation']['metadata'])


def _result_marker(store: OperationStore, job_id: str) -> str:
    job = store.job(job_id)
    assert job is not None
    return job['result']['data']['marker']


def test_compaction_must_not_promote_arguments_into_an_absent_binding() -> None:
    marker = 'T35-COMPACT-UNBOUND-RESULT'
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag='compact-unbound', result_marker=marker)
        # Synthetic change in the jobs row only; the operation remains bound A.
        store.update_job(job_id, 'SUCCEEDED', {'execution': None, 'arguments': {'project_id': PROJECT_A}})
        assert _binding(store, job_id) is None
        try:
            cleaned = store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A)
        except JobCleanupError as exc:
            assert exc.code in {'PROJECT_SCOPE_MISMATCH', 'PROJECT_BINDING_UNKNOWN'}
        else:
            assert cleaned == [job_id]
        # A job-argument field is not an independent project binding. A
        # specific binding refusal or a safe compaction are both acceptable.
        assert _binding(store, job_id) is None
        assert _result_marker(store, job_id) == marker
    finally:
        store.close()


def test_compaction_preserves_valid_execution_binding_when_arguments_conflict() -> None:
    marker = 'T35-COMPACT-VALID-RESULT'
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag='compact-conflict', result_marker=marker)
        store.update_job(job_id, 'SUCCEEDED', {'arguments': {'project_id': PROJECT_B}})
        assert _binding(store, job_id) == PROJECT_A
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        assert _binding(store, job_id) == PROJECT_A
        assert _result_marker(store, job_id) == marker
    finally:
        store.close()


def test_compaction_preserves_ordinary_valid_binding_and_result() -> None:
    marker = 'T35-COMPACT-POSITIVE-RESULT'
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag='compact-positive', result_marker=marker)
        assert _binding(store, job_id) == PROJECT_A
        assert store.compact_terminal_job_metadata([job_id], project_id=PROJECT_A) == [job_id]
        assert _binding(store, job_id) == PROJECT_A
        assert _result_marker(store, job_id) == marker
    finally:
        store.close()


def test_cleanup_foreign_argument_claim_cannot_authorize_project_B_mutation() -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag='cleanup-foreign', result_marker='T35-CLEANUP-FOREIGN'
        )
        store.update_job(job_id, 'SUCCEEDED', {'arguments': {'project_id': PROJECT_B}})
        before = store.job(job_id)
        before_events = store.events(job_id, offset=0, limit=100)
        assert before is not None
        authority = _FakeProjectAuthority({PROJECT_B: {'host_control'}})
        receiver = _InertDispatchReceiver(store, authority)
        response = _canonical_route(receiver, 'job.cleanup', {'job_ids': [job_id]}, PROJECT_B)
        after = store.job(job_id)
        after_events = store.events(job_id, offset=0, limit=100)
        assert after is not None
        assert response.get('success') is not True, f'foreign cleanup unexpectedly succeeded: {response!r}'
        assert after['metadata'] == before['metadata']
        assert after_events == before_events
        assert authority.calls == [(PROJECT_B, 'host_control')]
    finally:
        store.close()


def test_cleanup_authorized_matching_project_remains_available() -> None:
    marker = 'T35-CLEANUP-OWN'
    store = OperationStore(':memory:')
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag='cleanup-own', result_marker=marker)
        authority = _FakeProjectAuthority({PROJECT_A: {'host_control'}})
        receiver = _InertDispatchReceiver(store, authority)
        response = _canonical_route(receiver, 'job.cleanup', {'job_ids': [job_id]}, PROJECT_A)
        assert response.get('success') is True
        assert response['data']['cleaned_job_ids'] == [job_id]
        assert _binding(store, job_id) == PROJECT_A
        assert _result_marker(store, job_id) == marker
        assert authority.calls == [(PROJECT_A, 'host_control')]
    finally:
        store.close()
