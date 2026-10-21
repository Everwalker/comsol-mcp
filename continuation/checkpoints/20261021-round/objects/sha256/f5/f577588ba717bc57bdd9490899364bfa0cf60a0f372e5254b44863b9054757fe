"""R10 source controls: real production adapters over the exact R9 getter shape.

All engine and OS boundaries are software fixtures. The R9 values are used as
metadata fixtures only; the temperature/flux arrays are synthetic analytic data.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from comsol_mcp import _g3_results as results
from tools import t044_thermal_benchmark as benchmark
from tests import test_g3_results as helpers
from tests.test_ephemeral_result_ticket import _public, _call

R10_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = R10_ROOT
R9_ACTUAL = REPOSITORY_ROOT / '_state/t044-r9-actual-partial-ff2952bf2c3c/MAIN_ACTUAL_TERMINAL_EVIDENCE_R1.json'
R10_ACTUAL = REPOSITORY_ROOT / '_state/t044-thermal-r13-freeze-consumer-0b8171f9-20261019/evidence/actual_r10'

def _stationary_tree(**changes):
    options = {
        'sdim': 1,
        'length_unit': 'm',
        'dataset_props': {'solution': 'sol1', 'comp': 'comp1', 'geom': 'geom1'},
        'solver_parameter_names': [''],
        'solver_parameter_units': {},
        'solver_size': [43, 1],
        'solver_size_multi': [1, 1],
        'solver_level_names': [],
        'solver_levels': 0,
        'solver_outer_solnums': [],
        'solver_type': 'Stationary',
        'solver_sequence_type': 'SolverSequence',
        'solver_attached': True,
        'complex_': False,
    }
    options.update(changes)
    tree = helpers.build_tree(**options)
    # build_tree defaults an empty sequence to the parameter-name list; the
    # actual R9 SolutionInfo level-name getter returned an empty array.
    tree.solver.solution_info.props['getLevelNames'] = []
    return tree

def _count(tree):
    binding = results._resolve_dataset_binding(tree.model, 'dset1')
    return results._single_stored_solution_count_observation(tree.model, binding, 'sol1')

def test_exact_r9_actual_getter_shape_is_preserved_without_parameter_reinterpretation():
    actual = json.loads(R9_ACTUAL.read_text())
    observations = {row['method']: row.get('reply', {}).get('result') for row in actual['focus_getters']}
    assert observations['getSize'] == [43, 1]
    assert observations['getSizeMulti'] == [1, 1]
    assert observations['getPNames'] == ['']
    assert observations['getLevelNames'] == [] and observations['getLevels'] == 0
    assert observations['getOuterSolnum'] == []
    assert actual['focus_getters'][6]['method'] == 'getOuterSolnum'
    failed = actual['focus_getters'][7]
    assert failed['method'] == 'getSolnums' and failed['arguments'] == [[]]
    assert failed['reply']['failure']['cause_chain'][0]['message'] == 'Unknown_solution'
    # This preserves the actual failure as evidence; it does not replay it.
    tree = _stationary_tree()
    count = _count(tree)
    assert count['status'] == 'VERIFIED_SINGLE_ATTACHED_STATIONARY_NUMERIC_COUNT'
    assert count['witness_kind'] == 'attached_stationary_count_only_singleton'
    assert count['observation']['pnames'] == ['']
    assert count['observation']['parameter_rows'] == []
    assert count['numeric_solution_count'] == 1 and count['solution_axes_pair_mapping_complete'] is False
    methods = {method for entity in tree.entities for method in entity.methods()}
    assert 'getPVals' not in methods and 'getPValsImag' not in methods and 'getUnit' not in methods

def test_public_result_at_points_budget_private_ticket_and_thermal_consumer(tmp_path, monkeypatch):
    daemon, service, ref, project, sdk, tree, nodes, _old_args = _public(tmp_path, monkeypatch)
    try:
        # Keep the production ControlDaemon, ManagedBackend, ExecutionService,
        # budget guard, built-in ticket and result.at_points dispatch path.
        tree.solver.type_id = 'Stationary'
        tree.solver.props.update({
            'getSequenceType': 'SolverSequence', 'isAttached': True,
            'getSize': [43, 1], 'getSizeMulti': [1, 1], 'getPNames': [''],
        })
        info = tree.solver.solution_info
        info.props.update({'getLevelNames': [], 'getLevels': 0, 'getOuterSolnum': []})
        info.units = {}
        tree.solver.parameter_rows = {}
        service.ledger.model_ownership['Model'] = 'mcp_owned'
        geometry = tree.components.items['comp1'].collections['geom'].items['geom1']
        monkeypatch.setattr(geometry, 'getSDim', lambda *args: geometry._guard('getSDim', args) or 1)
        monkeypatch.setattr(geometry, 'lengthUnit', lambda *args: geometry._guard('lengthUnit', args) or 'm')

        def set_coordinates(feature, matrix):
            feature._guard('setInterpolationCoordinates', (matrix,))
            feature.coords = matrix
            xs = matrix[0]
            feature.values = [[ [300.0 + 100.0*x/benchmark.LENGTH for x in xs] ],
                              [ [-100000.0 for _ in xs] ]]

        monkeypatch.setattr(helpers.FInterp, 'setInterpolationCoordinates', set_coordinates, raising=False)
        monkeypatch.setattr(helpers.FInterp, 'getCoordinates', lambda feature: feature.coords, raising=False)
        args = benchmark.point_arguments('dset1')
        response = _call(daemon, ref, project, args, 't044-r10-count-only', 0)
        assert response['success'] is True, response
        from tools import run_native_w23_saved_mph_diagnostic as runner
        data = runner._response_body(response)
        assert data['coordinate_readback']['status'] == 'VERIFIED' and data['coordinate_readback']['unit'] == 'm', data['coordinate_readback']
        assert sdk.scope is not None and sdk.scope.consumed is True and sdk.scope.invalid is False
        assert not [row for row in sdk.calls if row.get('method') == 'getSolnums' and row.get('args') == [[]]], \
            'owned private singleton ticket must skip only the redundant empty-outer tuple diagnostic'
        result = benchmark.verify_points(data, dataset='dset1', solution='sol1')
        assert result['temperature_normalized_max_error'] == pytest.approx(0.0, abs=1e-12)
        assert result['flux_relative_max_error'] == pytest.approx(0.0, abs=1e-12)
        assert result['solution_axis_scope'] == 'numeric_singleton_count_only_no_outer_tuple'
        assert result['outer_tuple_present'] is False and result['field_array_present'] is False
        assert benchmark.verify_mesh_pair(result, result)['flux_relative_max_difference'] == 0.0
        assert data['field_array'] is None and data['solution_axes']['outer'] == data['solution_axes']['inner'] == []
        assert data['cleanup']['removed'] is True and tree.numerical.items == {}
    finally:
        daemon.close()

@pytest.mark.parametrize('mutation', [
    'dof_zero', 'count_two', 'size_wrong_shape', 'size_bool', 'multi_first_two', 'multi_second_two',
    'pname_nonempty', 'pname_extra', 'level_names', 'levels_one', 'levels_bool', 'outer_one',
    'wrong_solution_type', 'wrong_sequence_type', 'detached', 'identity_mismatch', 'study_absent',
    'study_tags_unstable', 'study_tags_duplicate', 'study_tags_wrong_shape', 'study_tags_error',
    'get_size_error', 'get_size_multi_error', 'get_pnames_error', 'levels_error', 'outer_error',
])
def test_stationary_witness_rejects_wrong_shapes_values_types_and_identity(monkeypatch, mutation):
    tree = _stationary_tree()
    if mutation == 'dof_zero': tree.solver.props['getSize'] = [0, 1]
    elif mutation == 'count_two': tree.solver.props['getSize'] = [43, 2]
    elif mutation == 'size_wrong_shape': tree.solver.props['getSize'] = [43]
    elif mutation == 'size_bool': tree.solver.props['getSize'] = [True, 1]
    elif mutation == 'multi_first_two': tree.solver.props['getSizeMulti'] = [2, 1]
    elif mutation == 'multi_second_two': tree.solver.props['getSizeMulti'] = [1, 2]
    elif mutation == 'pname_nonempty': tree.solver.props['getPNames'] = ['T']
    elif mutation == 'pname_extra': tree.solver.props['getPNames'] = ['', 'x']
    elif mutation == 'level_names': tree.solver.solution_info.props['getLevelNames'] = ['p']
    elif mutation == 'levels_one': tree.solver.solution_info.props['getLevels'] = 1
    elif mutation == 'levels_bool': tree.solver.solution_info.props['getLevels'] = True
    elif mutation == 'outer_one': tree.solver.solution_info.props['getOuterSolnum'] = [1]
    elif mutation == 'wrong_solution_type': tree.solver.type_id = 'Eigenvalue'
    elif mutation == 'wrong_sequence_type': tree.solver.props['getSequenceType'] = 'Stored'
    elif mutation == 'detached': tree.solver.props['isAttached'] = False
    elif mutation == 'identity_mismatch': tree.solver.study = lambda *args: 'std-other'
    elif mutation == 'dataset_solution_mismatch': tree.datasets.items['dset1'].props['solution'] = 'sol-other'
    elif mutation == 'study_absent': tree.model.collections['study'].items.clear()
    elif mutation == 'study_tags_unstable':
        tags = iter([['std1'], ['foreign']])
        tree.model.collections['study'].tags = lambda: next(tags, ['foreign'])
    elif mutation == 'study_tags_duplicate': tree.model.collections['study'].tags = lambda: ['std1', 'std1']
    elif mutation == 'study_tags_wrong_shape': tree.model.collections['study'].tags = lambda: 'std1'
    elif mutation == 'study_tags_error': tree.model.collections['study'].raises['tags'] = helpers.FakeEngineError('tags unavailable')
    elif mutation == 'get_size_error': tree.solver.raises['getSize'] = helpers.FakeEngineError('getSize unavailable')
    elif mutation == 'get_size_multi_error': tree.solver.raises['getSizeMulti'] = helpers.FakeEngineError('getSizeMulti unavailable')
    elif mutation == 'get_pnames_error': tree.solver.raises['getPNames'] = helpers.FakeEngineError('getPNames unavailable')
    elif mutation == 'levels_error': tree.solver.solution_info.raises['getLevels'] = helpers.FakeEngineError('getLevels unavailable')
    elif mutation == 'outer_error': tree.solver.solution_info.raises['getOuterSolnum'] = helpers.FakeEngineError('outer unavailable')
    count = _count(tree)
    assert count['status'] != 'VERIFIED_SINGLE_ATTACHED_STATIONARY_NUMERIC_COUNT', mutation
    assert count.get('numeric_solution_count') is None, mutation

def test_existing_frequency_and_detached_eigen_count_only_statuses_are_unchanged():
    frequency = helpers._attached_frequency_tree()
    frequency_count = results._single_stored_solution_count_observation(
        frequency.model, results._resolve_dataset_binding(frequency.model, 'dset1'), 'sol1')
    assert frequency_count['status'] == 'VERIFIED_SINGLE_ATTACHED_PARAMETRIC_NUMERIC_COUNT'
    assert frequency_count['witness_kind'] == 'attached_parametric_frequency_singleton'

    stored = helpers.build_tree(
        sdim=2, length_unit='m',
        dataset_props={'solution': 'sol1', 'comp': 'comp1', 'geom': 'geom1'},
        solver_parameter_names=['lambda'],
        solver_parameter_rows={1: ((193.4e12,), (-2.5e9,))},
        solver_parameter_units={'lambda': '1/m'},
        solver_size=[100, 1], solver_size_multi=[1, 1],
        solver_type='Eigenfrequency', solver_sequence_type='Stored', solver_attached=False,
        solver_level_names=[], solver_levels=0, solver_outer_solnums=[],
    )
    stored_count = results._single_stored_solution_count_observation(
        stored.model, results._resolve_dataset_binding(stored.model, 'dset1'), 'sol1')
    assert stored_count['status'] == 'VERIFIED_SINGLE_STORED_NUMERIC_COUNT'
    assert stored_count['witness_kind'] == 'stored_eigenfrequency_singleton'

def test_existing_frequency_result_at_points_budget_path_is_unchanged(monkeypatch):
    tree = helpers._attached_frequency_tree()
    values = [[[1.0, 2.0]]]
    imag = [[[0.5, -0.25]]]
    tree.numerical.factory = lambda tag, kind: helpers.FInterp(
        tag, kind, complex_=True,
        props={'data': None, 'expr': [], 'unit': ['V/m'], 'coord': None})
    def set_coordinates(feature, matrix):
        feature._guard('setInterpolationCoordinates', (matrix,))
        feature.coords = matrix
        feature.values = values
    monkeypatch.setattr(helpers.FInterp, 'setInterpolationCoordinates', set_coordinates, raising=False)
    monkeypatch.setattr(helpers.FInterp, 'getImagData', lambda feature: imag, raising=False)
    monkeypatch.setattr(helpers.FInterp, 'getCoordinates', lambda feature: feature.coords, raising=False)
    data = results.result_at_points(tree.worker, 'Model', {
        'points': [[0.0, 0.0], [1.0, 0.0]], 'coordinate_unit': 'm',
        'spec': {'expressions': ['ewfd.Ez'], 'solution': {'dataset': 'dset1'},
                 'complex_mode': 'preserve'},
    })
    assert data['status']['ok'] is True, data['status']
    assert data['solution_axes']['numeric_count_observation']['status'] == 'VERIFIED_SINGLE_ATTACHED_PARAMETRIC_NUMERIC_COUNT'
    assert data['result_budget']['records'][0]['status'] == 'PASS'
    assert data['result_budget']['records'][0]['solution_axes_verification_scope'] == 'numeric_singleton_count_only'
    assert data['field_array'] is None
    assert data['values'] == [[[{'real': 1.0, 'imag': 0.5}, {'real': 2.0, 'imag': -0.25}]]]

def _count_only_consumer_payload():
    values = [
        [[300.0 + 100.0*x/benchmark.LENGTH for x in benchmark.COORDINATES]],
        [[-100000.0 for _ in benchmark.COORDINATES]],
    ]
    observation = {
        'solution_type': 'Stationary', 'sequence_type': 'SolverSequence', 'attached': True,
        'get_size': [43, 1], 'get_size_multi': [1, 1], 'pnames': [''],
        'study': 'std1', 'solution_identity_matches_dataset_study': True, 'read_errors': [],
        'solution_info_count_scope': {'getLevelNames': [], 'getLevels': 0,
                                      'getOuterSolnum': [], 'read_errors': []},
    }
    membership = {'status': 'VERIFIED', 'actual_member': True, 'tags_stable': True,
                  'study_identity_stable': True, 'tags_complete': True, 'study': 'std1',
                  'study_tags_before': ['std1'], 'study_tags_after': ['std1'],
                  'solution_study_after': 'std1', 'read_errors': []}
    count = {'status': 'VERIFIED_SINGLE_ATTACHED_STATIONARY_NUMERIC_COUNT',
             'witness_kind': 'attached_stationary_count_only_singleton',
             'numeric_solution_count': 1, 'solution_axes_pair_mapping_complete': False,
             'observation': observation, 'attached_study_membership': membership}
    unit_application = {'status': 'VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ',
                        'requested_expressions': benchmark.EXPRESSIONS.copy(),
                        'requested_units': ['K', 'W/m^2'],
                        'before': {'expressions': benchmark.EXPRESSIONS.copy(), 'units': ['K', 'W/m^2']},
                        'after': {'expressions': benchmark.EXPRESSIONS.copy(), 'units': ['K', 'W/m^2']}}
    budget = {'status': 'PASS', 'allowed': True, 'publish_allowed': True, 'getdata_called': True,
              'solution_axes_verification_scope': 'numeric_singleton_count_only',
              'solution_axes_pair_mapping_complete': False,
              'requested': {'expression_count': 2, 'point_count': 101, 'inner_count': 1}}
    return {
        'status': {'ok': True}, 'dataset': 'dset1', 'solution': 'sol1',
        'expressions': benchmark.EXPRESSIONS.copy(),
        'points': [point.copy() for point in benchmark.POINTS],
        'point_count': len(benchmark.POINTS), 'complex_mode': 'real',
        'dataset_binding': {'binding_complete': True, 'dataset_type': 'Solution',
                            'dataset': 'dset1', 'solution': 'sol1',
                            'sources': {'solution': 'dataset.solution -> model.sol().tags()'}},
        'coordinate_unit': 'm', 'frame': 'spatial', 'expression_units': benchmark.UNITS.copy(),
        'feature_readback': {'data_status': 'VERIFIED', 'data': 'dset1',
                             'requested_unit_application': unit_application},
        'solution_axes': {'outer': [], 'inner': [], 'pair_mapping_complete': False,
                          'axis_metadata_complete': False, 'numeric_count_observation': count},
        'coordinate_readback': {'status': 'VERIFIED', 'unit': 'm', 'coordinates': [benchmark.COORDINATES.copy()]},
        'field_array': None, 'is_complex': False, 'values': values,
        'result_budget': {'status': 'PASS', 'publish_allowed': True, 'records': [budget]},
        'cleanup': {'created': True, 'removed': True, 'type_id': 'Interp',
                    'error': None, 'cleanup_failed': False},
    }

@pytest.mark.parametrize('mutation', [
    'dataset_binding', 'wrong_solution', 'solution_type', 'sequence_type', 'attached', 'count',
    'size_count', 'size_dof', 'size_multi', 'pnames', 'level_names', 'levels', 'outer',
    'study_status', 'study_tags', 'study_identity', 'study_read_error', 'solution_study', 'wrong_study_tag',
    'units', 'native_units', 'coordinates', 'binding_sources_missing',
    'expressions', 'points', 'point_count', 'complex_mode',
    'coordinate_status', 'complex', 'field_array', 'axes', 'budget_status', 'budget_scope',
    'getdata_not_called', 'budget_points', 'cleanup', 'cleanup_type',
    'extra_sample', 'extra_point', 'nonfinite',
])
def test_count_only_thermal_consumer_rejects_incomplete_or_reused_evidence(mutation):
    data = _count_only_consumer_payload()
    count = data['solution_axes']['numeric_count_observation']
    observation = count['observation']
    membership = count['attached_study_membership']
    record = data['result_budget']['records'][0]
    if mutation == 'dataset_binding': data['dataset_binding']['sources']['solution'] = 'caller-asserted'
    elif mutation == 'wrong_solution': data['dataset_binding']['solution'] = 'sol2'
    elif mutation == 'solution_type': observation['solution_type'] = 'Eigenvalue'
    elif mutation == 'sequence_type': observation['sequence_type'] = 'Stored'
    elif mutation == 'attached': observation['attached'] = False
    elif mutation == 'count': count['numeric_solution_count'] = 2
    elif mutation == 'size_count': observation['get_size'][1] = 2
    elif mutation == 'size_dof': observation['get_size'][0] = 0
    elif mutation == 'size_multi': observation['get_size_multi'] = [2, 1]
    elif mutation == 'pnames': observation['pnames'] = ['T']
    elif mutation == 'level_names': observation['solution_info_count_scope']['getLevelNames'] = ['p']
    elif mutation == 'levels': observation['solution_info_count_scope']['getLevels'] = 1
    elif mutation == 'outer': observation['solution_info_count_scope']['getOuterSolnum'] = [1]
    elif mutation == 'study_status': membership['status'] = 'UNVERIFIED'
    elif mutation == 'study_tags': membership['study_tags_after'] = ['other']
    elif mutation == 'study_identity': membership['study_identity_stable'] = False
    elif mutation == 'study_read_error': membership['read_errors'] = [{'method': 'tags'}]
    elif mutation == 'solution_study': observation['study'] = 'std-other'
    elif mutation == 'wrong_study_tag':
        observation['study'] = 'std2'
        membership.update(study='std2', study_tags_before=['std2'],
                          study_tags_after=['std2'], solution_study_after='std2')
    elif mutation == 'binding_sources_missing': data['dataset_binding']['sources'] = None
    elif mutation == 'expressions': data['expressions'] = ['T', 'ht.qx']
    elif mutation == 'points': data['points'][50]['x'] += .0001
    elif mutation == 'point_count': data['point_count'] = 100
    elif mutation == 'complex_mode': data['complex_mode'] = 'preserve'
    elif mutation == 'units': data['expression_units']['ht.dfluxx'] = 'K'
    elif mutation == 'native_units': data['feature_readback']['requested_unit_application']['after']['units'] = ['K', 'W']
    elif mutation == 'coordinates': data['coordinate_readback']['coordinates'][0][50] += .001
    elif mutation == 'coordinate_status': data['coordinate_readback']['status'] = 'UNVERIFIED'
    elif mutation == 'complex': data['is_complex'] = True
    elif mutation == 'field_array': data['field_array'] = {'axes': ['expression', 'outer', 'inner', 'point']}
    elif mutation == 'axes': data['solution_axes']['outer'] = [1]
    elif mutation == 'budget_status': data['result_budget']['status'] = 'BLOCKED'
    elif mutation == 'budget_scope': record['solution_axes_verification_scope'] = 'outer_inner_tuple_mapping'
    elif mutation == 'getdata_not_called': record['getdata_called'] = False
    elif mutation == 'budget_points': record['requested']['point_count'] = 100
    elif mutation == 'cleanup': data['cleanup']['removed'] = False
    elif mutation == 'cleanup_type': data['cleanup']['type_id'] = 'Eval'
    elif mutation == 'extra_sample': data['values'][0].append(list(data['values'][0][0]))
    elif mutation == 'extra_point': data['values'][1][0].pop()
    elif mutation == 'nonfinite': data['values'][1][0][50] = float('nan')
    with pytest.raises(RuntimeError):
        benchmark.verify_points(data, dataset='dset1', solution='sol1')
