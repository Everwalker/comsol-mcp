"""No-birth controls on the actual shared client's mutually exclusive mode gate."""
import asyncio
import json
from types import SimpleNamespace
import pytest
import ast
from pathlib import Path
from tools import run_v3_public_mcp_smoke as client


@pytest.mark.parametrize('other', ['science_recipes', 'checkpoint_cost_cycle', 'package_cycle',
                                  'parameter_io', 'parameter_csv', 'runtime_observation'])
def test_thermal_mode_conflict_refuses_before_original_controller_birth(monkeypatch, other):
    # Import first: MCP annotations subscript the real Popen class at import.
    import mcp.client.stdio
    args = SimpleNamespace(thermal_benchmark=True, science_recipes=False, checkpoint_cost_cycle=False,
                           package_cycle=False, parameter_io=False, parameter_csv=False, runtime_observation=False)
    setattr(args, other, True)
    def forbidden(*args, **kwargs):
        pytest.fail('conflicting mode attempted a process birth')
    monkeypatch.setattr(client.subprocess, 'Popen', forbidden)
    with pytest.raises(RuntimeError, match='separate fixed two-study budget'):
        asyncio.run(client.run(args))


def test_actual_thermal_callsite_bodies_use_shared_serializer_and_production_schema():
    from comsol_mcp._g2_registry import validate_call
    from tools import t044_thermal_benchmark as thermal
    common = {'project_id': 'p', 'session_id': 's', 'model_ref': {'schema_version': 1,
              'session_id': 's', 'server_instance_id': 'server', 'model_tag': 'm', 'generation': 1}}
    namespace = {**vars(thermal), 'path': Path('T044ThermalFixture.java'), 'common': common,
                 'project_id': 'p', 'session_id': 's', 'revision': 0, 'elements': 20,
                 'registered': {'data': {'path': 'g2_artifacts/registered/'+('b'*64)+'.java'}},
                 'dataset': {'tag': 'dset1', 'path': {'segments': [{'accessor': 'result'}, {'collection': 'dataset', 'tag': 'dset1'}]}}}
    checked = []
    for node in ast.walk(ast.parse(Path(thermal.__file__).read_text())):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call): continue
        value = node.value
        if not isinstance(value.func, ast.Name) or value.func.id not in ('call', 'action'): continue
        if not value.args or not isinstance(value.args[0], ast.Constant): continue
        operation = value.args[0].value
        arguments = eval(compile(ast.Expression(value.args[1]), thermal.__file__, 'eval'), namespace)
        if value.func.id == 'action':
            body = thermal.public_action_arguments(operation, common, arguments)
        else:
            body = arguments
        identity = {'project_id', 'session_id', 'model_ref', 'expected_revision', 'idempotency_key', 'request_id'}
        if operation == 'study.run':
            assert not ({'project_id', 'session_id', 'model_ref'} & set(body))
        body = client.public_arguments(operation, body, 'shape'+str(len(checked)))
        if operation == 'study.run':
            assert not (identity & set(body))
        validate_call(operation, body); checked.append(operation)
    assert len(checked) == 13 and checked.count('study.run') == 1
    tree = ast.parse(Path(thermal.__file__).read_text())
    action = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == 'action')
    forwarded = next(node.value for node in ast.walk(action)
                     if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
                     and isinstance(node.value.func, ast.Name) and node.value.func.id == 'call')
    outer = {keyword.arg: keyword.value for keyword in forwarded.keywords}
    assert isinstance(outer['ref'], ast.Name) and outer['ref'].id == 'model_ref'
    assert isinstance(outer['revision'], ast.IfExp)


def test_serialized_t044_study_run_reaches_production_sdk_one_without_body_identity(tmp_path, monkeypatch):
    from tools import t044_thermal_benchmark as thermal
    from tools.test_t050_checkpoint_cost_cycle import _environment
    from comsol_mcp._g2_registry import validate_call

    daemon, worker, service, project, ref, _call, saves = _environment(
        tmp_path, monkeypatch, permissions={'inspect', 'project_write', 'compute'})
    try:
        request_id = 't044-study-run-wire-sdk-one'
        revision = service.ledger._models[ref['model_tag']].revision
        model_proxy = worker.models[ref['model_tag']]
        engine_model = model_proxy.java
        model_proxy.study = engine_model.study
        model_proxy.sol = engine_model.sol
        from tests.test_g3_w16 import FList, FNode
        study_node = FNode(tag='std1', type_id='Study', state={
            'getLastComputationTime': None,
            'getLastComputationDate': None,
            'getLastComputationVersion': None,
        })
        study_node.declare('feature', FList(factory=lambda tag, *args: FNode(
            tag=tag, type_id=args[0] if args else 'StudyStep')))
        study_node.collections['feature'].items['stat'] = FNode(
            tag='stat', type_id='Stationary', state={'solveFor': True})
        engine_model.collections['study'].items['std1'] = study_node
        common = {'project_id': project['project_id'], 'session_id': 's', 'model_ref': ref,
                  'expected_revision': revision, 'idempotency_key': 'inner-idempotency',
                  'request_id': 'inner-request'}
        helper_body = thermal.public_action_arguments(
            'study.run', common, {'study': thermal.STUDY, 'timeout_s': 180, 'idempotency_key': 'new'})
        body = client.public_arguments('study.run', helper_body, request_id)
        identity = {'project_id', 'session_id', 'model_ref', 'expected_revision', 'idempotency_key', 'request_id'}
        assert body == {'study': thermal.STUDY, 'timeout_s': 180}
        assert not (identity & set(body))
        validate_call('study.run', body)

        execution = {
            'request_id': request_id,
            'idempotency_key': request_id,
            'rpc_timeout_s': 10,
            'queue_timeout_s': 15,
            'execution_timeout_s': 10,
            'project_id': project['project_id'],
            'session_id': 's',
            'model_ref': ref,
            'expected_revision': revision,
        }
        call_arguments = {'operation_id': 'study.run', 'arguments': body, 'execution': execution}
        serialized_call = json.dumps(
            {'jsonrpc': '2.0', 'method': 'tools/call',
             'params': {'name': 'operation_call', 'arguments': call_arguments}},
            sort_keys=True, separators=(',', ':'), allow_nan=False)
        decoded_call = json.loads(serialized_call)
        captured = tmp_path / 't044-study-run-final-mcp-call.json'
        captured.write_text(json.dumps(decoded_call, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        final_params = decoded_call['params']['arguments']
        assert not (identity & set(final_params['arguments']))
        assert final_params['execution']['expected_revision'] == revision
        assert final_params['execution']['model_ref'] == ref
        assert final_params['execution']['request_id'] == request_id

        # Exercise the real ControlDaemon and ExecutionService through the existing
        # SDK-only worker fixture; no COMSOL process or parallel executor is used.
        request = {
            'operation': 'operation_call',
            'arguments': {'operation_id': final_params['operation_id'],
                          'arguments': final_params['arguments']},
            'execution': final_params['execution'],
        }
        result = daemon.dispatch(request)
        assert result['success'], json.dumps(result, ensure_ascii=False, indent=2)
        assert daemon.backend.service is service
        assert service.ledger._models[ref['model_tag']].revision >= revision
        assert saves == []
        assert json.loads(captured.read_text(encoding='utf-8')) == decoded_call
    finally:
        daemon.close()


@pytest.mark.parametrize('thermal_mode', [False, True])
def test_actual_thermal_project_policy_is_granted_only_for_selected_fixed_mode(tmp_path, monkeypatch, thermal_mode):
    from tools.test_t050_checkpoint_cost_cycle import _environment
    from comsol_mcp._execution_contract import ExecutionContractError
    monkeypatch.setenv('COMSOL_MCP_TRUSTED_CODE', '1'); monkeypatch.setenv('COMSOL_MCP_HOST_CONTROL', '1')
    daemon, worker, service, project, ref, call, saves = _environment(tmp_path, monkeypatch,
        permissions={'inspect', 'project_write', 'compute', 'host_control', 'trusted_code'})
    node = next(n.value for n in ast.walk(ast.parse(Path(client.__file__).read_text()))
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name) and n.value.func.id == 'call'
                and n.value.args and isinstance(n.value.args[0], ast.Constant)
                and n.value.args[0].value == 'project.create')
    body = eval(compile(ast.Expression(node.args[1]), client.__file__, 'eval'),
                {'root': Path('T044policy'), 'cost': False, 'science': False, 'thermal': thermal_mode,
                 'args': SimpleNamespace(runtime_observation=False, package_cycle=False)})
    try:
        result = daemon.dispatch({'operation': 'operation_call', 'arguments': {'operation_id': 'project.create',
                 'arguments': client.public_arguments('project.create', body, 'thermal-project')},
                 'execution': {'request_id': 'thermal-project', 'idempotency_key': 'thermal-project'}})
        assert result['success'], result
        pid = result['data']['project']['project_id']
        if thermal_mode:
            assert 'trusted_code' in daemon.project_authority.authorize_operation(pid, 'trusted_code')['policy']['permissions']
        else:
            with pytest.raises(ExecutionContractError): daemon.project_authority.authorize_operation(pid, 'trusted_code')
        assert saves == []
    finally:
        daemon.close()
