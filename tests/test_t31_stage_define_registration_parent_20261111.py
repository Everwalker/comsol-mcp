"""T31 supplemental stage-definition registration through an inert receiver.

The actual dispatcher, registration callback, OperationStore and _finish run;
project authorization, session binding, model ledger and scheduler are fakes.
Only a fresh in-memory SQLite database is used. No ControlDaemon is created and
no Worker, COMSOL model, network, process or historic job is accessed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from comsol_mcp._control_daemon import ControlDaemon
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _StageDefineReceiver,
    _dispatch,
    _execution_envelope,
    _stage_plan,
)


def test_direct_then_nested_pending_replay_and_real_memory_registration():
    receiver: Any = _StageDefineReceiver()
    store = receiver.store
    execution = _execution_envelope(request_id='first', idempotency_key='stage-key')
    model_ref = dict(execution['model_ref'])
    project_id = execution['project_id']
    calls = []

    class FakeLedger:
        session_id = execution['session_id']

        def _state_for(self, ref):
            assert ref.as_dict() == model_ref
            calls.append('ledger_state')
            return SimpleNamespace(active_operation_id=None, dirty=False, revision=0)

    class FakeBackend:
        service = SimpleNamespace(ledger=FakeLedger())

        def _model_project_key(self, observed):
            assert observed == model_ref
            calls.append('model_project_key')
            return 'memory-only-model-key'

    receiver._default_backend = FakeBackend()
    receiver._execution_session_context = lambda _execution: None
    receiver._cached_registry_control = ControlDaemon._cached_registry_control.__get__(receiver)
    store.put_metadata('revisions', 'memory-only-model-key', {
        'model_ref': model_ref, 'project_id': project_id,
        'attribution': 'PROJECT_BOUND', 'revision': 0, 'dirty': False,
    })
    try:
        plan = _stage_plan()
        first = _dispatch(receiver, definition=plan, execution=execution)
        assert first['success'] is True and first['data']['status'] == 'QUEUED', first
        assert len(receiver.session_scheduler.calls) == 1
        operation_id, job_id = first['execution']['operation_id'], first['execution']['job_id']
        assert calls == []  # Captured callback has not been executed.

        second = ControlDaemon._dispatch_admitted(receiver, {
            'operation': 'registry_call',
            'arguments': {'operation_id': 'experiment.stage_define', 'arguments': {'definition': plan}},
            'execution': {**execution, 'request_id': 'nested-replay'},
        })
        assert second['success'] is True and second['data']['status'] == 'QUEUED', second
        assert second['execution']['operation_id'] == operation_id
        assert second['execution']['job_id'] == job_id
        assert second['execution']['request_id'] == 'first'
        assert len(receiver.session_scheduler.calls) == 1
        assert calls == []

        context, register_definition = receiver.session_scheduler.calls[0]
        assert context is None and callable(register_definition)
        result = register_definition()  # Actual captured product callback, called once by this test.
        assert result['success'] is True, result
        assert result['data']['registration'] == 'CREATED'
        assert result['data']['worker_rpc_performed'] is False
        assert result['data']['solve_started'] is False
        assert result['execution']['operation_id'] == operation_id
        assert result['execution']['job_id'] == job_id
        assert calls == ['ledger_state', 'model_project_key']
        saved = store.get_stage_plan(project_id, model_ref, plan['plan_id'])
        assert saved is not None and saved['definition'] == plan
        assert store.get_stage_id_index(project_id, model_ref, 'preheat')['plan_id'] == plan['plan_id']
        assert store.get_stage_id_index(project_id, model_ref, 'cooldown')['plan_id'] == plan['plan_id']
        assert store.job(job_id)['status'] == 'SUCCEEDED'
        assert store.job(job_id)['result'] == result

        replay = _dispatch(receiver, definition=plan,
                           execution={**execution, 'request_id': 'completed-replay'})
        assert replay == result
        assert len(receiver.session_scheduler.calls) == 1
        assert calls == ['ledger_state', 'model_project_key']
        assert store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 1
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
    finally:
        store.close()


def test_unknown_result_replay_retains_original_failure_without_resubmit():
    receiver: Any = _StageDefineReceiver()
    store = receiver.store
    execution = _execution_envelope(request_id='unknown-first', idempotency_key='unknown-key')
    try:
        first = _dispatch(receiver, definition=_stage_plan(), execution=execution)
        assert first['success'] is True and len(receiver.session_scheduler.calls) == 1, first
        operation_id, job_id = first['execution']['operation_id'], first['execution']['job_id']
        unknown = {'success': False, 'data': {},
                   'error': {'code': 'EXECUTION_STATE_UNKNOWN', 'safe_retry': False}}
        assert store.finish(operation_id, status='UNKNOWN', result=unknown) == (True, 'UNKNOWN')
        replay = _dispatch(receiver, definition=_stage_plan(),
                           execution={**execution, 'request_id': 'unknown-replay'})
        assert replay == unknown and replay['error']['safe_retry'] is False
        assert store.job(job_id)['status'] == 'UNKNOWN'
        assert len(receiver.session_scheduler.calls) == 1
        assert store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 1
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
    finally:
        store.close()
