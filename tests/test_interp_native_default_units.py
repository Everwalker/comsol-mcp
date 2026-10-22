"""Production Interp pipeline/FieldArray/public gateway, software SDK seam only."""
import json
import pytest
import time
from comsol_mcp import _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_results import _requested_unit_point_tree, FInterp, _real_public_points_daemon


def complete_tree(monkeypatch):
    tree, nodes, args = _requested_unit_point_tree(monkeypatch)
    info = tree.solver.solution_info
    info.props['getOuterSolnum'] = [1]; info.getSolnum = lambda outer, strict: [1]
    args['spec']['expressions'] = ['emw.neff_1']; args['spec']['units'] = ['']
    return tree, nodes, args


def test_explicit_native_default_remains_exact_raw_blank_in_fieldarray(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    data = results.result_at_points(tree.worker, 'Model', args)
    assert data['status']['ok'] is True
    proof = data['feature_readback']['requested_unit_application']
    assert proof['before']['units'] == proof['after']['units'] == proof['requested_units'] == ['']
    assert proof['native_default_unit_expressions'] == ['emw.neff_1']
    assert data['expression_units'] == {'emw.neff_1': ''}
    assert data['field_array']['units']['expression'] == {'emw.neff_1': ''}
    assert data['field_array']['metadata']['unit_readback_status'] == 'UNVERIFIED'
    assert 'not dimensionless evidence' in data['field_array']['metadata']['native_default_unit_semantics']
    assert not tree.numerical.items


@pytest.mark.parametrize('expression', ['compOdd.user_temperature', 'u_custom', 'x/1[mm]', '2', 'freq'])
def test_native_default_uses_the_same_engine_pipeline_for_task_expressions(monkeypatch, expression):
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec']['expressions'] = [expression]
    args['spec']['units'] = ['']
    data = results.result_at_points(tree.worker, 'Model', args)
    assert data['status']['ok'] is True
    proof = data['feature_readback']['requested_unit_application']
    assert proof['requested_expressions'] == [expression]
    assert proof['before']['units'] == proof['after']['units'] == ['']
    assert data['field_array']['units']['expression'] == {expression: ''}
    assert data['field_array']['metadata']['unit_readback_status'] == 'UNVERIFIED'
    assert nodes and tree.numerical.items == {}


def test_exact_builtin_normals_keep_literal_blank_native_default_readback(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    expressions = ['emw.nx', 'emw.ny']
    args['spec']['expressions'] = expressions
    args['spec']['units'] = ['', '']

    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[1.0]], [[0.0]]]

    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]]])
    result = results.result_at_points(tree.worker, 'Model', args)

    assert result['status']['ok'] is True
    proof = result['feature_readback']['requested_unit_application']
    assert proof['requested_expressions'] == expressions
    assert proof['requested_units'] == ['', '']
    assert proof['native_default_unit_expressions'] == expressions
    assert proof['before']['units'] == proof['after']['units'] == ['', '']
    assert result['expression_units'] == {'emw.nx': '', 'emw.ny': ''}
    assert result['field_array'] is not None
    assert result['field_array']['units']['expression'] == {'emw.nx': '', 'emw.ny': ''}
    assert 'not dimensionless evidence' in result['field_array']['metadata']['native_default_unit_semantics']
    assert not tree.numerical.items


@pytest.mark.parametrize('unit', [' ', '\t', None, 1, False])
def test_only_exact_empty_string_allowed_not_whitespace_or_nonstrings(monkeypatch, unit):
    tree, nodes, args = complete_tree(monkeypatch); args['spec']['units'] = [unit]
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'INVALID_REQUEST' and not nodes


@pytest.mark.parametrize('requested, observed', [('1',''), ('V/m',''), ('V/m','A/m')])
def test_no_empty_or_physical_unit_alias_before_or_after_numeric_read(monkeypatch, requested, observed):
    tree, nodes, args = complete_tree(monkeypatch); args['spec']['units'] = [requested]
    original = FInterp.getStringArray; calls = {'unit': 0}
    def read(self, name):
        if name == 'unit':
            calls['unit'] += 1
            if calls['unit'] >= 2: return [observed]
        return original(self, name)
    monkeypatch.setattr(FInterp, 'getStringArray', read)
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 'explicit unit differs' in str(exc.value)


def test_native_default_may_resolve_to_actual_unit_after_getdata_without_scaling(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    expression = 'compOdd.user_temperature'
    args['spec'].update(expressions=[expression, 'freq'], units=['', 'Hz'])

    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[321.25]], [[2.5]]]

    original_get_data = FInterp.getData

    def resolve_native_unit(self):
        raw = original_get_data(self)
        self.props['unit'] = ['K', 'Hz']
        return raw

    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]]])
    monkeypatch.setattr(FInterp, 'getData', resolve_native_unit)

    data = results.result_at_points(tree.worker, 'Model', args)

    assert data['status']['ok'] is True
    assert data['expressions'] == [expression, 'freq']
    assert data['expression_units'] == {expression: 'K', 'freq': 'Hz'}
    assert data['field_array']['units']['expression'] == {expression: 'K', 'freq': 'Hz'}
    assert data['field_array']['metadata']['unit_readback_status'] == 'VERIFIED'
    assert data['values'][0][0][0][0]['real'] == 321.25
    assert data['values'][1][0][0][0]['real'] == 2.5
    proof = data['feature_readback']['requested_unit_application']
    assert proof['before']['expressions'] == proof['after']['expressions'] == [expression, 'freq']
    assert proof['before']['units'] == ['', 'Hz']
    assert proof['after']['units'] == ['K', 'Hz']
    assert proof['native_default_unit_resolution'][expression] == {
        'before_unit': '', 'after_unit': 'K', 'status': 'VERIFIED_ACTUAL_UNIT_READBACK'
    }
    assert proof['no_external_scaling_or_relabeling'] is True
    assert not tree.numerical.items


def test_native_default_rejects_nonempty_unit_drift_after_read(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec'].update(expressions=['compOdd.user_temperature'], units=[''])
    original_get = FInterp.getStringArray
    unit_reads = {'count': 0}

    def drift(self, name):
        value = original_get(self, name)
        if name == 'unit':
            unit_reads['count'] += 1
            if unit_reads['count'] == 1:
                return ['K']
            if unit_reads['count'] == 2:
                return ['V/m']
        return value

    monkeypatch.setattr(FInterp, 'getStringArray', drift)
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 'changed from a nonempty pre-read native unit' in str(exc.value)
    assert 'run' in nodes[0].methods()
    assert tree.numerical.items == {}


def test_native_default_accepts_stable_nonempty_pre_read_unit(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    expression = 'compOdd.user_temperature'
    args['spec'].update(expressions=[expression], units=[''])
    original_get = FInterp.getStringArray

    def resolved_before_read(self, name):
        value = original_get(self, name)
        return ['K'] if name == 'unit' else value

    monkeypatch.setattr(FInterp, 'getStringArray', resolved_before_read)
    data = results.result_at_points(tree.worker, 'Model', args)
    assert data['status']['ok'] is True
    assert data['feature_readback']['requested_unit_application']['before']['units'] == ['K']
    assert data['feature_readback']['requested_unit_application']['after']['units'] == ['K']
    assert data['expression_units'] == {expression: 'K'}
    assert nodes and tree.numerical.items == {}


@pytest.mark.parametrize('mutation', ['expression_order', 'unit_shape_before', 'unit_shape_after'])
def test_native_default_resolution_preserves_exact_expression_and_array_shape(monkeypatch, mutation):
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec'].update(expressions=['compOdd.user_temperature', 'freq'], units=['', 'Hz'])
    original = FInterp.getStringArray
    calls = {'expr': 0, 'unit': 0}

    def malformed(self, name):
        value = original(self, name)
        if mutation == 'expression_order' and name == 'expr':
            calls['expr'] += 1
            if calls['expr'] == 2:
                return list(reversed(value))
        if mutation.startswith('unit_shape') and name == 'unit':
            calls['unit'] += 1
            target_call = 1 if mutation == 'unit_shape_before' else 2
            if calls['unit'] == target_call:
                return ['']
        return value

    monkeypatch.setattr(FInterp, 'getStringArray', malformed)
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert nodes and tree.numerical.items == {}
    if mutation == 'unit_shape_before':
        assert 'run' not in nodes[0].methods()
    else:
        assert 'run' in nodes[0].methods()


@pytest.mark.parametrize('bad_read', ['before_numeric_read', 'after_numeric_read'])
def test_native_default_rejects_whitespace_only_actual_unit(monkeypatch, bad_read):
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec'].update(expressions=['compOdd.user_temperature'], units=[''])
    original = FInterp.getStringArray
    calls = {'unit': 0}

    def whitespace(self, name):
        value = original(self, name)
        if name == 'unit':
            calls['unit'] += 1
            target_call = 1 if bad_read == 'before_numeric_read' else 2
            if calls['unit'] == target_call:
                return ['  ']
        return value

    monkeypatch.setattr(FInterp, 'getStringArray', whitespace)
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 'whitespace-only native unit' in str(exc.value)
    assert ('run' in nodes[0].methods()) == (bad_read == 'after_numeric_read')
    assert tree.numerical.items == {}


def test_bma_explicit_unit_one_is_exact_before_and_after_numeric_read(monkeypatch):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    tree, nodes, args = _requested_unit_point_tree(monkeypatch)
    expressions = list(runner.protocol.bma_point_expressions('1', field_scheme='emw_down'))
    requested = ['1/m', '1', '1/m']
    args['spec'].update(expressions=expressions, units=requested)

    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[6e6]], [[1.5]], [[4e6]]]

    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]], [[0.0]]])
    result = results.result_at_points(tree.worker, 'Model', args)

    assert result['status']['ok'] is True
    proof = result['feature_readback']['requested_unit_application']
    assert proof['requested_units'] == requested
    assert proof['before']['units'] == proof['after']['units'] == requested
    assert proof['native_default_unit_expressions'] == []
    assert result['expression_units'] == dict(zip(expressions, requested))
    assert not tree.numerical.items


@pytest.mark.parametrize('changed_stage', ['before_numeric_read', 'after_numeric_read'])
def test_bma_explicit_unit_one_rejects_blank_canonicalization(monkeypatch, changed_stage):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    tree, nodes, args = _requested_unit_point_tree(monkeypatch)
    expressions = list(runner.protocol.bma_point_expressions('1', field_scheme='emw_down'))
    requested = ['1/m', '1', '1/m']
    args['spec'].update(expressions=expressions, units=requested)

    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[6e6]], [[1.5]], [[4e6]]]

    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]], [[0.0]]])
    original = FInterp.getStringArray
    unit_reads = {'count': 0}

    def canonicalized(self, name):
        value = original(self, name)
        if name == 'unit':
            unit_reads['count'] += 1
            target_read = 1 if changed_stage == 'before_numeric_read' else 2
            if unit_reads['count'] == target_read:
                return ['1/m', '', '1/m']
        return value

    monkeypatch.setattr(FInterp, 'getStringArray', canonicalized)
    with pytest.raises(ExecutionContractError) as exc:
        results.result_at_points(tree.worker, 'Model', args)
    assert exc.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 'explicit unit differs' in str(exc.value)
    if changed_stage == 'before_numeric_read':
        assert 'run' not in nodes[0].methods()
    else:
        assert 'run' in nodes[0].methods()
    assert tree.numerical.items == {}


def test_empty_request_does_not_relax_expression_after_readback(monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    original = FInterp.getStringArray; calls = {'expr': 0}
    def read(self, name):
        if name == 'expr':
            calls['expr'] += 1
            if calls['expr'] >= 2: return ['wrong']
        return original(self, name)
    monkeypatch.setattr(FInterp, 'getStringArray', read)
    with pytest.raises(ExecutionContractError): results.result_at_points(tree.worker, 'Model', args)


@pytest.mark.parametrize('expression', ['emw.neff_1', 'compOdd.user_temperature'])
def test_exact_empty_request_through_real_public_gateway_and_registered_observation(tmp_path, monkeypatch, expression):
    from tools.run_native_w23_saved_mph_diagnostic import science, _response_body
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec']['expressions'] = [expression]
    study = tree.studies.items['std1']
    study.getLastComputationDate = lambda: 123
    study.getLastComputationVersion = lambda: '6.4.0.293'
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    daemon.backend.worker.project_root = tmp_path/'public-project-root'/'points'
    response = daemon.dispatch({'operation': 'operation_call', 'arguments': {'operation_id': 'result.at_points',
               'arguments': {**args, 'points': [{'x': 0., 'y': 0.}], 'frame': 'spatial'}},
               'execution': {'project_id': project, 'session_id': ref['session_id'], 'model_ref': ref,
                             'expected_revision': 0, 'request_id': 'native-default', 'idempotency_key': 'native-default',
                             'rpc_timeout_s': 30, 'queue_timeout_s': 30, 'execution_timeout_s': None}})
    if response.get('execution', {}).get('job_id'):
        response = science._wait_job(daemon, response, deadline_epoch=time.time()+20,
                                    cleanup_reserve_s=1, label='native-default', evidence=tmp_path)
    assert response['success'], response
    data = _response_body(response)
    assert data['expression_units'][expression] == ''
    assert data['field_array']['metadata']['unit_readback_status'] == 'UNVERIFIED'
    assert data['observation_ref']['sha256']
    stored = daemon.store.operation_job(response['execution']['operation_id'])
    assert stored['status'] == 'SUCCEEDED'


def test_public_gateway_publishes_resolved_native_default_unit_for_unknown_expression(tmp_path, monkeypatch):
    from tools.run_native_w23_saved_mph_diagnostic import science, _response_body

    tree, nodes, args = complete_tree(monkeypatch)
    expression = 'compOdd.user_temperature'
    args['spec'].update(expressions=[expression, 'freq'], units=['', 'Hz'])

    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[321.25]], [[2.5]]]

    original_get_data = FInterp.getData

    def resolve_native_unit(self):
        raw = original_get_data(self)
        self.props['unit'] = ['K', 'Hz']
        return raw

    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]]])
    monkeypatch.setattr(FInterp, 'getData', resolve_native_unit)
    study = tree.studies.items['std1']
    study.getLastComputationDate = lambda: 123
    study.getLastComputationVersion = lambda: '6.4.0.293'
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    trusted_root = tmp_path/'public-project-root'/'resolved-native-unit'
    trusted_root.mkdir(parents=True)
    daemon.backend.worker.project_root = trusted_root
    response = daemon.dispatch({'operation': 'operation_call', 'arguments': {
        'operation_id': 'result.at_points',
        'arguments': {**args, 'points': [{'x': 0.0, 'y': 0.0}], 'frame': 'spatial'}},
        'execution': {'project_id': project, 'session_id': ref['session_id'], 'model_ref': ref,
                      'expected_revision': 0, 'request_id': 'native-default-resolves-unit',
                      'idempotency_key': 'native-default-resolves-unit', 'rpc_timeout_s': 30,
                      'queue_timeout_s': 30, 'execution_timeout_s': None}})
    if response.get('execution', {}).get('job_id'):
        response = science._wait_job(daemon, response, deadline_epoch=time.time()+20,
                                    cleanup_reserve_s=1, label='native-default-resolves-unit', evidence=tmp_path)

    assert response['success'] is True, json.dumps(response.get('error', response), ensure_ascii=False, indent=2)
    data = _response_body(response)
    assert data['expression_units'] == {expression: 'K', 'freq': 'Hz'}
    assert data['field_array']['units']['expression'] == {expression: 'K', 'freq': 'Hz'}
    assert data['field_array']['metadata']['unit_readback_status'] == 'VERIFIED'
    assert data['values'][0][0][0][0]['real'] == 321.25
    assert data['values'][1][0][0][0]['real'] == 2.5
    assert data['feature_readback']['requested_unit_application']['after']['units'] == ['K', 'Hz']
    assert daemon.store.operation_job(response['execution']['operation_id'])['status'] == 'SUCCEEDED'
    assert tree.numerical.items == {}


def test_control_daemon_accepts_exact_normal_native_default_through_production_adapter(tmp_path, monkeypatch):
    from tools.run_native_w23_saved_mph_diagnostic import science, _response_body
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec']['expressions'] = ['emw.nx', 'emw.ny']
    args['spec']['units'] = ['', '']
    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[1.0]], [[0.0]]]
    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.0]], [[0.0]]])
    monkeypatch.setattr(FInterp, 'getCoordinates', lambda self: self.coords)
    study = tree.studies.items['std1']
    study.getLastComputationDate = lambda: 123
    study.getLastComputationVersion = lambda: '6.4.0.293'
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    trusted_root = tmp_path/'public-project-root'/'normals'
    trusted_root.mkdir(parents=True)
    daemon.backend.worker.project_root = trusted_root
    jobs_before = daemon.store.list_jobs(project_id=project).total
    response = daemon.dispatch({'operation': 'operation_call', 'arguments': {'operation_id': 'result.at_points',
               'arguments': {**args, 'points': [{'x': 0., 'y': 0.}], 'frame': 'spatial'}},
               'execution': {'project_id': project, 'session_id': ref['session_id'], 'model_ref': ref,
                             'expected_revision': 0, 'request_id': 'normal-native-default',
                             'idempotency_key': 'normal-native-default', 'rpc_timeout_s': 30,
                             'queue_timeout_s': 30, 'execution_timeout_s': None}})
    if response.get('execution', {}).get('job_id'):
        response = science._wait_job(daemon, response, deadline_epoch=time.time()+20,
                                    cleanup_reserve_s=1, label='normal-native-default', evidence=tmp_path)
    assert response['success'] is True, json.dumps(response.get('error', response), ensure_ascii=False, indent=2)
    data = _response_body(response)
    assert data['expression_units'] == {'emw.nx': '', 'emw.ny': ''}
    assert data['feature_readback']['requested_unit_application']['native_default_unit_expressions'] == ['emw.nx', 'emw.ny']
    assert daemon.store.list_jobs(project_id=project).total == jobs_before + 1


def test_control_daemon_rejects_empty_expression_before_operation_dispatch(tmp_path, monkeypatch):
    tree, nodes, args = complete_tree(monkeypatch)
    args['spec']['expressions'] = [' ']
    args['spec']['units'] = ['']
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    jobs_before = daemon.store.list_jobs(project_id=project).total
    response = daemon.dispatch({'operation': 'operation_call', 'arguments': {'operation_id': 'result.at_points',
               'arguments': {**args, 'points': [{'x': 0., 'y': 0.}], 'frame': 'spatial'}},
               'execution': {'project_id': project, 'session_id': ref['session_id'], 'model_ref': ref,
                             'expected_revision': 0, 'request_id': 'invalid-normal-alias',
                             'idempotency_key': 'invalid-normal-alias', 'rpc_timeout_s': 30,
                             'queue_timeout_s': 30, 'execution_timeout_s': None}})
    assert response['success'] is False
    assert response['error']['code'] == 'INVALID_REQUEST'
    assert daemon.store.list_jobs(project_id=project).total == jobs_before
    assert nodes == [] and tree.numerical.items == {}
    assert tree.engine_methods() == set()


def test_normal_native_default_and_legacy_unit_one_paths_keep_numeric_normal_checks(monkeypatch):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    from tests.test_activity022_boundary_selection_ticket import install_boundary_selection_fixture
    tree, nodes, args = _requested_unit_point_tree(monkeypatch)
    expressions = runner.plane_field_expressions('2'); units = runner.plane_field_units('2')
    args['spec'].update(expressions=list(expressions), units=list(units),
                        selection=runner.plane_selection('2'), entity_dim=1)
    args['points'] = [[8e-6, 0.]]
    install_boundary_selection_fixture(tree, monkeypatch)
    def coordinates(self, matrix):
        self.coords = matrix
        self.values = [[[0. if e == 'emw.ny' else 1. for _ in matrix[0]]] for e in expressions]
    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates)
    monkeypatch.setattr(FInterp, 'getImagData', lambda self: [[[0.]] for _ in expressions])
    native = results.result_at_points(tree.worker, 'Model', args)
    count = {'status': 'PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON', 'dataset_tag': 'dset1',
             'solver_sequence_tag': 'sol1', 'study_tag': 'std1', 'get_size': [100, 1],
             'raw_parameter': {'name': 'freq', 'real': 193414489032258.03, 'imag': 0., 'unit': 'Hz'}}
    def decode():
        return runner.protocol.decode_production_complex_point_readback(native, dataset_tag='dset1', solution_tag='sol1',
            expressions=expressions, expected_points=[[8e-6, 0.]], expected_solution_count=1,
            plane_id='output_x8', expected_expression_units=runner.FIELD_UNITS,
            expected_requested_units=units, require_x_plane_normal=True,
            expected_count_witness=count,
            expected_selection=runner.plane_selection('2'), field_scheme='emw_down')
    decoded = decode()
    assert decoded['expression_units']['emw.nx'] == decoded['expression_units']['emw.ny'] == ''
    assert decoded['native_default_unit_expressions'] == ['emw.nx', 'emw.ny']
    assert 'not dimensionless evidence' in decoded['native_default_unit_semantics']
    # A native-default declaration never substitutes for actual constant unit-x normal data.
    native['values'][-2] = [[{'real': 1.2, 'imag': 0.}]]
    with pytest.raises(runner.protocol.ScienceProtocolError): decode()
    native['values'][-2] = [[{'real': 1.0, 'imag': 0.}]]
    native['expression_units']['emw.ny'] = '1'
    with pytest.raises(runner.protocol.ScienceProtocolError, match='unit'):
        decode()
    legacy_units = runner.plane_field_legacy_units('2')
    legacy_args = dict(args)
    legacy_args['spec'] = {**args['spec'], 'units': list(legacy_units)}
    legacy_native = results.result_at_points(tree.worker, 'Model', legacy_args)
    legacy_decoded = runner.protocol.decode_production_complex_point_readback(
        legacy_native, dataset_tag='dset1', solution_tag='sol1', expressions=expressions,
        expected_points=[[8e-6, 0.]], expected_solution_count=1, plane_id='output_x8',
        expected_expression_units=runner.FIELD_UNITS, expected_requested_units=legacy_units,
        require_x_plane_normal=True, expected_count_witness=count,
        expected_selection=runner.plane_selection('2'), field_scheme='emw_down')
    assert legacy_decoded['expression_units']['emw.nx'] == legacy_decoded['expression_units']['emw.ny'] == '1'


def test_fresh_w23_units_use_native_defaults_only_for_normal_builtins():
    from tools import run_native_w23_saved_mph_diagnostic as runner
    assert runner.plane_field_units('1')[-2:] == ('', '')
    assert runner.plane_field_units('2')[-2:] == ('', '')
    assert runner.plane_field_legacy_units('1')[-2:] == ('1', '1')
    assert runner.plane_field_legacy_units('2')[-2:] == ('1', '1')
    assert runner.mode_units('1') == {'emw.beta_1': '1/m', 'emw.neff_1': '1', 'emw.k0': '1/m'}
    assert runner.mode_units('2') == {'emw.beta_2': '1/m', 'emw.neff_2': '1', 'emw.k0': '1/m'}
