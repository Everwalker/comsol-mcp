"""Synthetic adversarial numeric consumer controls, not SDK/native acceptance."""
from copy import deepcopy
import pytest
from comsol_mcp._solution_binding import FieldArray
from tools.t044_thermal_benchmark import COORDINATES, EXPRESSIONS, UNITS, verify_points, verify_mesh_pair, point_arguments


def payload():
    values = [[[[300 + 100*x/.01 for x in COORDINATES]]], [[[ -100000.0 for _ in COORDINATES]]]]
    field = FieldArray(values, axes=['expression', 'outer', 'inner', 'point'],
                       coords={'expression': EXPRESSIONS, 'outer': [1], 'inner': [1],
                               'point': list(range(1, 102)), 'spatial': [[x] for x in COORDINATES]},
                       units={'expression': UNITS}, metadata={'unit_readback_status': 'VERIFIED', 'parameter_names': [],
                       'solution_pairs': [{'outer': 1, 'inner': 1, 'solnum': 1, 'parameters': {}, 'parameter_units': {}}]}, is_complex=False)
    return {'dataset': 'dset1', 'solution': 'sol1', 'coordinate_unit': 'm', 'frame': 'spatial',
            'expression_units': UNITS.copy(), 'feature_readback': {'data_status': 'VERIFIED', 'data': 'dset1'},
            'solution_axes': {'outer': [1], 'inner': [1], 'pair_mapping_complete': True,
                              'parameters_complete': True, 'axis_metadata_complete': True},
            'coordinate_readback': {'status': 'VERIFIED', 'unit': 'm', 'coordinates': [COORDINATES.copy()]},
            'field_array': field.to_dict()}


def test_exact_analytic_data_through_production_field_array():
    result = verify_points(payload(), dataset='dset1', solution='sol1')
    assert result['temperature_normalized_max_error'] == 0
    assert verify_mesh_pair(result, result)['flux_relative_max_difference'] == 0
    assert len(point_arguments('dset1')['points']) == 101


@pytest.mark.parametrize('case', ['dataset', 'solution', 'unit', 'coordinates', 'shape', 'complex',
                                 'pair', 'native_coordinates', 'temperature', 'flux', 'nan', 'alias'])
def test_strict_rejection(case):
    data = deepcopy(payload())
    if case == 'dataset': data['feature_readback']['data'] = 'other'
    if case == 'solution': data['solution'] = 'other'
    if case == 'unit': data['expression_units']['ht.dfluxx'] = 'W'
    if case == 'coordinates': data['coordinate_readback']['coordinates'][0][50] += .001
    if case == 'shape': data['field_array']['shape'] = [2, 1, 1, 100]
    if case == 'complex': data['field_array']['is_complex'] = True
    if case == 'pair': data['solution_axes']['pair_mapping_complete'] = False
    if case == 'native_coordinates': data['coordinate_readback']['status'] = 'UNVERIFIED'
    if case in ('temperature', 'flux', 'nan'):
        values = data['field_array']['values']
        values[0 if case != 'flux' else 1][0][0][50] = float('nan') if case == 'nan' else (301 if case == 'temperature' else -90000)
        data['field_array']['data'] = values
    if case == 'alias': data['field_array']['data'] = []
    with pytest.raises(RuntimeError): verify_points(data, dataset='dset1', solution='sol1')


def test_mesh_difference_is_independent_check():
    coarse = {'temperature': [300]*101, 'flux': [-100000]*101}
    fine = {'temperature': [300.2]*101, 'flux': [-100000]*101}
    with pytest.raises(RuntimeError): verify_mesh_pair(coarse, fine)


@pytest.mark.parametrize('bad', [[], [float('nan')]*101, [True]*101])
def test_mesh_pair_cannot_zip_truncate_or_accept_nonfinite(bad):
    with pytest.raises(RuntimeError):
        verify_mesh_pair({'temperature': bad, 'flux': [-100000]*101},
                         {'temperature': [300]*101, 'flux': [-100000]*101})


def test_builder_readback_guard():
    from tools.t044_thermal_benchmark import verify_builder
    data = {'model_tag': 'm', 'dimension': 1, 'elements': 20, 'geometry_length_unit': 'm',
            'L': .01, 'k': 10, 'Tleft': 300, 'Tright': 400, 'conductivity': ['k'],
            'left_selection': [1], 'right_selection': [2], 'left_type': 'TemperatureBoundary',
            'right_type': 'TemperatureBoundary', 'left_T0': 'Tleft', 'right_T0': 'Tright',
            'physics_features': ['solid1', 'left', 'right'],
            'physics_feature_types': {'solid1': 'Solid', 'left': 'TemperatureBoundary', 'right': 'TemperatureBoundary'},
            'solution_tags': [], 'semantics': 'T044_UNSOLVED_FIXED_1D_NO_SOURCE_NO_SAVE',
            'vertices': [[i*.01/20 for i in range(21)]]}
    assert verify_builder(data, model_tag='m', elements=20)['unsolved'] is True
    for key, bad in [('solution_tags', ['sol1']), ('right_selection', [1]), ('dimension', 2), ('conductivity', ['other'])]:
        wrong = deepcopy(data); wrong[key] = bad
        with pytest.raises(RuntimeError): verify_builder(wrong, model_tag='m', elements=20)


def test_interval_and_actual_comsol_finalize_geometry_shape():
    from tools.t044_thermal_benchmark import verify_geometry
    # Shape from immutable actual first T044 11-geometry.inspect, no native replay.
    data = {'geometry_state': {'dimension': 1, 'length_unit': 'm',
            'bounding_box': {'value': [0., .01], 'error': None},
            'object_names': {'ok': True, 'value': ['i1']}, 'entity_counters': {'n_entities': [2, 1]}},
            'features': [{'tag': 'i1', 'type_id': 'Interval'}, {'tag': 'fin', 'type_id': 'Finalize'}]}
    assert verify_geometry(data)['finalization'] == 'fin'
    mutations = []
    for kind in ('extra', 'wrong_finalize', 'bbox', 'dimension', 'entities', 'objects', 'unit'):
        wrong = deepcopy(data)
        if kind == 'extra': wrong['features'].append({'tag': 'i2', 'type_id': 'Interval'})
        if kind == 'wrong_finalize': wrong['features'][1]['type_id'] = 'Rectangle'
        if kind == 'bbox': wrong['geometry_state']['bounding_box']['value'][1] = .02
        if kind == 'dimension': wrong['geometry_state']['dimension'] = 2
        if kind == 'entities': wrong['geometry_state']['entity_counters']['n_entities'] = [3, 2]
        if kind == 'objects': wrong['geometry_state']['object_names']['value'] = ['i1', 'i2']
        if kind == 'unit': wrong['geometry_state']['length_unit'] = 'cm'
        mutations.append(wrong)
    for wrong in mutations:
        with pytest.raises(RuntimeError): verify_geometry(wrong)


def test_result_at_points_requests_documented_domain_flux_component():
    from tools.t044_thermal_benchmark import EXPRESSIONS, UNITS
    arguments = point_arguments('dset1')
    assert EXPRESSIONS == ['T', 'ht.dfluxx']
    assert UNITS == {'T': 'K', 'ht.dfluxx': 'W/m^2'}
    assert arguments['spec']['expressions'] == ['T', 'ht.dfluxx']
    assert arguments['spec']['units'] == ['K', 'W/m^2']
    assert arguments['spec']['solution'] == {'dataset': 'dset1'}
    assert 'ht.qx' not in arguments['spec']['expressions']
    assert len(arguments['points']) == 101


def test_stale_undefined_qx_response_is_rejected_by_real_numeric_consumer():
    data = payload()
    data['field_array']['coords']['expression'] = ['T', 'ht.qx']
    with pytest.raises(RuntimeError, match='typed field axes differ'):
        verify_points(data, dataset='dset1', solution='sol1')


def test_dfluxx_si_unit_is_mandatory():
    data = payload()
    data['expression_units']['ht.dfluxx'] = 'K'
    with pytest.raises(RuntimeError, match='SI expression units differ'):
        verify_points(data, dataset='dset1', solution='sol1')



def test_model_inspect_identity_is_carried_only_in_public_execution_envelope(tmp_path):
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
    from comsol_mcp._execution_service import ExecutionService
    from comsol_mcp._g2_registry import _effective_input_schema, _entry, validate_call
    from tools.t044_thermal_benchmark import public_action_arguments

    common = {
        'project_id': 'project-r12-test',
        'session_id': 'session-r12-test',
        'model_ref': {
            'schema_version': 1, 'session_id': 'session-r12-test',
            'model_tag': 'mcp2', 'generation': 1,
            'server_instance_id': 'r12-test-server',
        },
    }
    body = public_action_arguments('model.inspect', common, {})
    assert body == {}
    schema = _effective_input_schema(_entry('model.inspect').input_schema, 'model.inspect')
    assert set(schema['properties']) == {'detail'}
    assert schema['required'] == []
    assert schema['additionalProperties'] is False
    validate_call('model.inspect', body)

    summary_body = public_action_arguments('model.inspect', common, {'detail': 'summary'})
    assert summary_body == {'detail': 'summary'}
    validate_call('model.inspect', summary_body)
    with pytest.raises(ExecutionContractError, match='detail must be'):
        validate_call('model.inspect', {'detail': 'not-a-public-detail'})

    class Snapshot:
        def model_snapshot(self, tag):
            return {'model_tag': tag, 'server_instance_id': 'r12-test-server',
                    'fingerprint': 'fixture', 'external_event_counter': 0}

    class Worker:
        def client(self):
            raise AssertionError('invalid nested identity must reject before worker access')

    project_root = tmp_path / 'projects'
    project_root.mkdir()
    service = ExecutionService(SessionLedger('session-r12-test', 'r12-test-server'),
                               Snapshot(), project_root=project_root)
    daemon = ControlDaemon(tmp_path / 'control', service=service, registry={},
                           worker=Worker(), project_root=project_root)
    daemon.backend.worker_identity = {
        'runtime_id': 'r12-test-runtime', 'worker_instance_id': 'r12-test-worker',
        'connection_epoch': 1, 'server_instance_id': 'r12-test-server',
    }
    try:
        create_key = 'r12-create-test-project'
        created = daemon.dispatch({
            'operation': 'project.create',
            'arguments': {'label': 'r12-model-inspect-body', 'workspace': 'r12-model-inspect-body',
                          'policy': {'permissions': ['inspect', 'project_write', 'compute']}},
            'execution': {'request_id': create_key, 'idempotency_key': create_key},
        })
        assert created['success'] is True, created
        project_id = created['data']['project']['project_id']
        bound = service.bind_model('r12-mcp2', ownership='mcp_owned')
        model_ref = bound['execution']['model_ref']
        daemon.backend._bind_model_project(model_ref, project_id)
        daemon.backend.persist()
        nested_values = [
            ('project_id', project_id),
            ('session_id', 'session-r12-test'),
            ('model_ref', model_ref),
        ]
        for index, (field, value) in enumerate(nested_values):
            key = f'r12-invalid-model-inspect-{index}'
            result = daemon.dispatch({
                'operation': 'operation_call',
                'arguments': {'operation_id': 'model.inspect', 'arguments': {field: value}},
                'execution': {'project_id': project_id, 'session_id': 'session-r12-test',
                              'model_ref': model_ref, 'request_id': key, 'idempotency_key': key},
            })
            assert result['success'] is False
            assert result['error']['code'] == 'INVALID_REQUEST', result
            assert 'identity belongs in the outer execution envelope' in result['error']['message']
    finally:
        daemon.close()
