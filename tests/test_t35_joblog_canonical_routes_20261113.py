"""T35 canonical/nested job.log pagination through the real daemon router.

Fresh in-memory records, an explicitly fake project authority and an inert
receiver only. No real principal, gateway transport, disk, WAL, worker,
COMSOL, SSH or historical job is involved. The 4 MiB admission budget is a
proposed operational bound, not an approved public MCP specification.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

PROJECT_A = 't35-route-project-a'
PROJECT_B = 't35-route-project-b'
ROUTES = ('job.log', 'registry_call', 'operation_call')


class _Authority:
    def __init__(self, timeline: list[tuple]) -> None:
        self.timeline = timeline

    def authorize_operation(self, project_id: str, permission: str) -> None:
        self.timeline.append(('authority', project_id, permission))
        if project_id not in {PROJECT_A, PROJECT_B} or permission != 'inspect':
            raise ExecutionContractError('PERMISSION_DENIED', 'synthetic authority denial')


class _Receiver:
    _timeouts = staticmethod(ControlDaemon._timeouts)
    _error = staticmethod(ControlDaemon._error)
    _exception = staticmethod(ControlDaemon._exception)
    _page_cursor_offset = staticmethod(ControlDaemon._page_cursor_offset)
    _control_read = ControlDaemon._control_read
    _cached_registry_control = ControlDaemon._cached_registry_control
    _dispatch_catalog_job_control = ControlDaemon._dispatch_catalog_job_control
    _dispatch_catalog_job_control_impl = ControlDaemon._dispatch_catalog_job_control_impl

    def __init__(self, store: OperationStore, timeline: list[tuple]) -> None:
        self.store = store
        self.project_authority = _Authority(timeline)

    @staticmethod
    def _log_exception() -> None:
        raise AssertionError('unexpected generic daemon exception in synthetic router test')


def _seed(store: OperationStore, *, big: bool = False) -> str:
    record, reused = store.begin(
        request_id='t35-routes-request', idempotency_key='t35-routes-key',
        request_hash='t35-routes-hash', operation='job.synthetic-producer',
        metadata={'execution': {'project_id': PROJECT_A}},
    )
    assert not reused
    job_id = record['job_id']
    for index in range(3):
        store.add_event(job_id, f'synthetic-{index}',
                        {'index': index, 'bulk': 'x' * (1_600_000 if big else 1)})
    return job_id


def _spy(store: OperationStore, timeline: list[tuple]) -> None:
    real_snapshot, real_page = store.job_log_read_snapshot, store.job_log_page_bounded

    @contextmanager
    def snapshot(job_id: str, project_id: str | None, *, max_binding_bytes: int):
        with real_snapshot(job_id, project_id, max_binding_bytes=max_binding_bytes) as bound:
            assert store.db.in_transaction
            timeline.append(('snapshot', project_id, bound))
            yield bound

    def page(job_id: str, offset: int, limit: int, *, max_response_bytes: int):
        assert store.db.in_transaction
        timeline.append(('page', offset, limit))
        return real_page(job_id, offset, limit, max_response_bytes=max_response_bytes)

    def forbid_old(*args: Any, **kwargs: Any) -> None:
        raise AssertionError('job.log must not fall back to unbounded job()/events()')

    store.job_log_read_snapshot = snapshot  # type: ignore[method-assign]
    store.job_log_page_bounded = page  # type: ignore[method-assign]
    store.job = forbid_old  # type: ignore[method-assign]
    store.events = forbid_old  # type: ignore[method-assign]


def _route(receiver: _Receiver, route: str, args: dict, project: str | None) -> dict:
    if route == 'job.log':
        operation, arguments = route, args
    else:
        operation, arguments = route, {'operation_id': 'job.log', 'arguments': args}
    execution = {'project_id': project} if project is not None else {}
    return ControlDaemon._dispatch_admitted(
        receiver, {'operation': operation, 'arguments': arguments, 'execution': execution})  # type: ignore[arg-type]


@pytest.mark.parametrize('route', ROUTES)
def test_t35_canonical_and_nested_byte_short_pages_keep_decimal_cursor(route: str) -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _seed(store, big=True)
        timeline: list[tuple] = []
        _spy(store, timeline)
        receiver = _Receiver(store, timeline)
        first = _route(receiver, route, {'job_id': job_id, 'limit': 100}, PROJECT_A)
        assert first['success'] is True
        assert [row['metadata']['index'] for row in first['data']['events']] == [0, 1]
        assert first['data']['next_offset'] == 2 and first['data']['next_cursor'] == '2'
        assert len(json.dumps(first, allow_nan=False).encode('utf-8')) <= 4 * 1024 * 1024
        second = _route(receiver, route, {'job_id': job_id, 'limit': 100, 'cursor': '2'}, PROJECT_A)
        assert second['success'] is True
        assert [row['metadata']['index'] for row in second['data']['events']] == [2]
        assert second['data']['next_offset'] == 3 and second['data']['next_cursor'] == '3'
        assert timeline == [
            ('authority', PROJECT_A, 'inspect'), ('snapshot', PROJECT_A, True), ('page', 0, 100),
            ('authority', PROJECT_A, 'inspect'), ('snapshot', PROJECT_A, True), ('page', 2, 100),
        ]
    finally:
        store.close()


@pytest.mark.parametrize('route', ROUTES)
def test_t35_canonical_and_nested_foreign_binding_refuses_before_page(route: str) -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _seed(store)
        timeline: list[tuple] = []
        _spy(store, timeline)
        receiver = _Receiver(store, timeline)
        response = _route(receiver, route, {'job_id': job_id}, PROJECT_B)
        assert response['success'] is False and response['error']['code'] == 'NODE_NOT_FOUND'
        assert 'synthetic-0' not in json.dumps(response)
        assert timeline == [('authority', PROJECT_B, 'inspect'), ('snapshot', PROJECT_B, False)]
    finally:
        store.close()


@pytest.mark.parametrize('route', ROUTES)
def test_t35_canonical_and_nested_missing_outer_project_refuses_before_snapshot(route: str) -> None:
    store = OperationStore(':memory:')
    try:
        job_id = _seed(store)
        timeline: list[tuple] = []
        _spy(store, timeline)
        receiver = _Receiver(store, timeline)
        response = _route(receiver, route, {'job_id': job_id}, None)
        assert response['success'] is False and response['error']['code'] == 'PROJECT_IDENTITY_REQUIRED'
        assert timeline == []
    finally:
        store.close()
