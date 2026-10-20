"""Fixed T044 numerical checks; input is actual public result.at_points data.

These checks do not establish a native producer, runtime identity or cleanup.
The public client must separately retain those production receipts.
"""
import math
import re
from pathlib import Path
from uuid import UUID

LENGTH = 0.01
EXPRESSIONS = ['T', 'ht.dfluxx']
UNITS = {'T': 'K', 'ht.dfluxx': 'W/m^2'}
COORDINATES = [i * LENGTH / 100 for i in range(101)]
POINTS = [{'x': x} for x in COORDINATES]
STUDY = {'segments': [{'collection': 'study', 'tag': 'std1'}]}
STUDY_LIMIT = 2
TOTAL_SECONDS = 600
CLEANUP_RESERVE_SECONDS = 90
PUBLIC_CALLS = 30
GEOMETRY = {'segments': [{'collection': 'component', 'tag': 'comp1'}, {'collection': 'geom', 'tag': 'geom1'}]}


def _require(condition, message):
    if not condition:
        raise RuntimeError('T044 ' + message)


def public_action_arguments(operation, common, arguments):
    """Keep managed model identity in the MCP execution envelope for these routes."""
    body = {**common, **arguments}
    if operation in {'study.run', 'model.inspect'}:
        for key in ('project_id', 'session_id', 'model_ref'):
            body.pop(key, None)
    return body


def _numbers(values, expected):
    _require(isinstance(values, list) and len(values) == len(expected), 'coordinate count differs')
    for actual, wanted in zip(values, expected):
        _require(type(actual) in (int, float) and math.isfinite(actual)
                 and math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-14), 'coordinates differ')


def point_arguments(dataset):
    _require(isinstance(dataset, str) and bool(dataset), 'actual dataset required')
    return {'spec': {'expressions': EXPRESSIONS.copy(), 'units': ['K', 'W/m^2'],
                     'solution': {'dataset': dataset}, 'complex_mode': 'real'},
            'points': [p.copy() for p in POINTS], 'coordinate_unit': 'm', 'frame': 'spatial'}


def verify_builder(data, *, model_tag, elements):
    _require(elements in (20, 40) and data.get('model_tag') == model_tag,
             'owned model/coarse-fine mesh differs')
    _require(data.get('dimension') == 1 and data.get('elements') == elements
             and data.get('geometry_length_unit') == 'm', 'actual 1D SI mesh differs')
    _require(all(data.get(k) == v for k, v in {'L': .01, 'k': 10, 'Tleft': 300, 'Tright': 400}.items()),
             'actual material/parameter values differ')
    _require(data.get('conductivity') == ['k'], 'material conductivity readback differs')
    _require(data.get('left_selection') == [1] and data.get('right_selection') == [2]
             and data.get('left_type') == data.get('right_type') == 'TemperatureBoundary'
             and data.get('left_T0') == 'Tleft' and data.get('right_T0') == 'Tright',
             'boundary readback differs')
    types = data.get('physics_feature_types', {})
    _require(types.get('left') == types.get('right') == 'TemperatureBoundary'
             and set(types) == set(data.get('physics_features', []))
             and 'HeatSource' not in types.values(), 'physics/source readback differs')
    _require(data.get('solution_tags') == []
             and data.get('semantics') == 'T044_UNSOLVED_FIXED_1D_NO_SOURCE_NO_SAVE', 'builder solved or unverified')
    vertices = data.get('vertices')
    _require(isinstance(vertices, list) and len(vertices) == 1
             and len(vertices[0]) == elements + 1, 'actual mesh vertex count differs')
    _numbers(sorted(vertices[0]), [i * LENGTH / elements for i in range(elements + 1)])
    return {'elements': elements, 'dimension': 1, 'unsolved': True}


def verify_geometry(data):
    state = data.get('geometry_state', {})
    features = data.get('features', [])
    _require(type(state.get('dimension')) is int and state['dimension'] == 1
             and state.get('length_unit') == 'm', 'actual 1D SI geometry differs')
    _require([(v.get('tag'), v.get('type_id')) for v in features] == [('i1', 'Interval'), ('fin', 'Finalize')],
             'sole Interval and native Finalize required')
    box = state.get('bounding_box', {})
    _require(box.get('error') is None, 'geometry bounding box unavailable')
    _numbers(box.get('value'), [0., LENGTH])
    objects = state.get('object_names', {})
    _require(objects.get('ok') is True and objects.get('value') == ['i1'], 'actual Interval object differs')
    _require(state.get('entity_counters', {}).get('n_entities') == [2, 1], 'two endpoints/one domain required')
    return {'dimension': 1, 'interval': 'i1', 'finalization': 'fin', 'bounds_m': [0., LENGTH]}


def verify_points(data, *, dataset, solution):
    """Require complete native readbacks before evaluating the analytic errors."""
    _require(data.get('dataset') == dataset and data.get('solution') == solution,
             'actual dataset/solution differs')
    _require(data.get('coordinate_unit') == 'm' and data.get('frame') == 'spatial', 'frame/unit differs')
    _require(data.get('expression_units') == UNITS, 'SI expression units differ')
    readback = data.get('feature_readback', {})
    _require(readback.get('data_status') == 'VERIFIED' and readback.get('data') == dataset,
             'native Interp dataset readback unavailable')
    axis = data.get('solution_axes', {})
    coordinate = data.get('coordinate_readback', {})
    _require(coordinate.get('status') == 'VERIFIED' and coordinate.get('unit') == 'm',
             'native coordinate readback unavailable')
    matrix = coordinate.get('coordinates')
    _require(isinstance(matrix, list) and len(matrix) == 1, 'coordinate dimension differs')
    _numbers(matrix[0], COORDINATES)
    field = data.get('field_array', {})
    if field is None:
        _require(data.get('status', {}).get('ok') is True,
                 'production result.at_points did not complete cleanly')
        _require(data.get('expressions') == EXPRESSIONS
                 and data.get('points') == POINTS
                 and data.get('point_count') == len(POINTS)
                 and data.get('complex_mode') == 'real',
                 'fixed T044 expression/point/real request differs')
        count = axis.get('numeric_count_observation', {})
        observation = count.get('observation', {}) if isinstance(count, dict) else {}
        count_scope = observation.get('solution_info_count_scope', {}) if isinstance(observation, dict) else {}
        membership = count.get('attached_study_membership', {}) if isinstance(count, dict) else {}
        binding = data.get('dataset_binding', {})
        binding_sources = binding.get('sources') if isinstance(binding, dict) else None
        _require(isinstance(binding, dict)
                 and binding.get('binding_complete') is True
                 and binding.get('dataset_type') == 'Solution'
                 and binding.get('dataset') == dataset
                 and binding.get('solution') == solution
                 and isinstance(binding_sources, dict)
                 and binding_sources.get('solution') == 'dataset.solution -> model.sol().tags()',
                 'direct dataset-to-solution identity witness unavailable')
        _require(count.get('status') == 'VERIFIED_SINGLE_ATTACHED_STATIONARY_NUMERIC_COUNT'
                 and count.get('witness_kind') == 'attached_stationary_count_only_singleton'
                 and type(count.get('numeric_solution_count')) is int
                 and count.get('numeric_solution_count') == 1
                 and count.get('solution_axes_pair_mapping_complete') is False,
                 'strict attached stationary singleton witness unavailable')
        _require(observation.get('solution_type') == 'Stationary'
                 and observation.get('sequence_type') == 'SolverSequence'
                 and observation.get('attached') is True
                 and observation.get('solution_identity_matches_dataset_study') is True
                 and type(observation.get('get_size')) in (list, tuple)
                 and len(observation['get_size']) == 2
                 and type(observation['get_size'][0]) is int and observation['get_size'][0] > 0
                 and type(observation['get_size'][1]) is int and observation['get_size'][1] == 1
                 and type(observation.get('get_size_multi')) in (list, tuple)
                 and len(observation['get_size_multi']) == 2
                 and all(type(value) is int for value in observation['get_size_multi'])
                 and list(observation['get_size_multi']) == [1, 1]
                 and observation.get('pnames') == [''],
                 'raw stationary singleton allocation shape differs from the reviewed witness')
        _require(not observation.get('read_errors')
                 and not count_scope.get('read_errors')
                 and count_scope.get('getLevelNames') == []
                 and type(count_scope.get('getLevels')) is int and count_scope.get('getLevels') == 0
                 and count_scope.get('getOuterSolnum') == [],
                 'SolutionInfo did not verify zero levels and no outer axis')
        _require(membership.get('status') == 'VERIFIED'
                 and membership.get('actual_member') is True
                 and membership.get('tags_complete') is True
                 and membership.get('tags_stable') is True
                 and membership.get('study_identity_stable') is True
                 and membership.get('study_tags_before') == membership.get('study_tags_after')
                 and not membership.get('read_errors')
                 and isinstance(membership.get('study'), str) and bool(membership['study'])
                 and membership.get('study') == observation.get('study')
                 and membership.get('solution_study_after') == observation.get('study')
                 and observation.get('study') == STUDY['segments'][0]['tag'],
                 'actual same-model Study membership/readback is not stable')
        _require(axis.get('pair_mapping_complete') is False
                 and axis.get('axis_metadata_complete') is False
                 and axis.get('outer') == [] and axis.get('inner') == [],
                 'count-only result must not claim outer/inner tuples')
        _require(data.get('field_array') is None,
                 'count-only result must not synthesize a FieldArray')
        budget = data.get('result_budget', {})
        records = budget.get('records', []) if isinstance(budget, dict) else []
        _require(budget.get('status') == 'PASS' and budget.get('publish_allowed') is True
                 and len(records) == 1
                 and records[0].get('status') == 'PASS'
                 and records[0].get('allowed') is True
                 and records[0].get('getdata_called') is True
                 and records[0].get('solution_axes_verification_scope') == 'numeric_singleton_count_only'
                 and records[0].get('solution_axes_pair_mapping_complete') is False
                 and records[0].get('requested', {}).get('expression_count') == len(EXPRESSIONS)
                 and records[0].get('requested', {}).get('point_count') == len(COORDINATES)
                 and records[0].get('requested', {}).get('inner_count') == 1,
                 'production numeric allocation budget proof is unavailable')
        unit_proof = readback.get('requested_unit_application', {})
        _require(unit_proof.get('status') == 'VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ'
                 and unit_proof.get('requested_expressions') == EXPRESSIONS
                 and unit_proof.get('requested_units') == ['K', 'W/m^2']
                 and unit_proof.get('before', {}).get('expressions') == EXPRESSIONS
                 and unit_proof.get('after', {}).get('expressions') == EXPRESSIONS
                 and unit_proof.get('before', {}).get('units') == ['K', 'W/m^2']
                 and unit_proof.get('after', {}).get('units') == ['K', 'W/m^2'],
                 'native Interp expression/unit setter readback differs')
        cleanup = data.get('cleanup', {})
        _require(cleanup.get('created') is True and cleanup.get('removed') is True
                 and cleanup.get('type_id') == 'Interp' and cleanup.get('error') is None
                 and cleanup.get('cleanup_failed') is False,
                 'temporary Interp cleanup was not confirmed')
        _require(data.get('is_complex') is False, 'T044 count-only values must be real')
        values = data.get('values')
        _require(isinstance(values, list) and len(values) == 2,
                 'count-only expression rows differ from request')
        series = []
        for expression in values:
            _require(isinstance(expression, list) and len(expression) == 1
                     and isinstance(expression[0], list) and len(expression[0]) == 101
                     and all(type(value) in (int, float) and math.isfinite(value) for value in expression[0]),
                     'native [expression][solnum][point] samples must be finite singleton data')
            series.append(expression[0])
        temperature_error = max(abs(v - (300 + 100 * x / LENGTH))
                                for v, x in zip(series[0], COORDINATES)) / 100
        flux_error = max(abs(v + 100000) for v in series[1]) / 100000
        _require(temperature_error <= .001 and flux_error <= .001,
                 'analytic tolerance exceeded')
        return {'temperature': series[0], 'flux': series[1],
                'temperature_normalized_max_error': temperature_error,
                'flux_relative_max_error': flux_error,
                'solution_axis_scope': 'numeric_singleton_count_only_no_outer_tuple',
                'outer_tuple_present': False, 'field_array_present': False}

    _require(isinstance(field, dict) and field.get('axes') == ['expression', 'outer', 'inner', 'point']
             and field.get('shape') == [2, 1, 1, 101] and field.get('is_complex') is False,
             'typed real field shape differs')
    _require(all(axis.get(k) is True for k in ('pair_mapping_complete', 'axis_metadata_complete'))
             and axis.get('outer') == [1] and axis.get('inner') == [1],
             'complete single-solution axes unavailable')
    coords = field.get('coords', {})
    _require(coords.get('expression') == EXPRESSIONS and coords.get('outer') == [1]
             and coords.get('inner') == [1] and coords.get('point') == list(range(1, 102)), 'typed field axes differ')
    _require(coords.get('spatial') == [[x] for x in COORDINATES], 'typed spatial coordinates differ')
    _require(field.get('units', {}).get('expression') == UNITS, 'typed SI units differ')
    _require(field.get('metadata', {}).get('unit_readback_status') == 'VERIFIED', 'unit readback unavailable')
    # T044 is stationary with no parameter sweep. The production binding_complete
    # contract requires actual pair mapping + axes, not parameters_complete=True
    # (the latter is false for absent parameter combinations).
    metadata = field.get('metadata', {})
    _require(metadata.get('parameter_names') == []
             and metadata.get('solution_pairs') == [{'outer': 1, 'inner': 1, 'solnum': 1,
                                                     'parameters': {}, 'parameter_units': {}}],
             'unexpected sweep or unmapped stored solution')
    values = field.get('values')
    _require(values == field.get('data') and isinstance(values, list) and len(values) == 2,
             'field value aliases differ')
    series = []
    for expression in values:
        _require(isinstance(expression, list) and len(expression) == 1
                 and isinstance(expression[0], list) and len(expression[0]) == 1, 'singleton axes differ')
        row = expression[0][0]
        _require(isinstance(row, list) and len(row) == 101
                 and all(type(v) in (int, float) and math.isfinite(v) for v in row), 'finite point values required')
        series.append(row)
    temperature_error = max(abs(v - (300 + 100 * x / LENGTH)) for v, x in zip(series[0], COORDINATES)) / 100
    flux_error = max(abs(v + 100000) for v in series[1]) / 100000
    _require(temperature_error <= .001 and flux_error <= .001, 'analytic tolerance exceeded')
    return {'temperature': series[0], 'flux': series[1],
            'temperature_normalized_max_error': temperature_error, 'flux_relative_max_error': flux_error}


def _require_public_receipt_shape(receipt, *, operation):
    """Validate the public identity fields retained by the real MCP call wrapper."""
    _require(isinstance(receipt, dict), operation + ' execution receipt unavailable')
    for key in ('operation_id', 'job_id'):
        value = receipt.get(key)
        _require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value) is not None,
                 operation + ' ' + key + ' is not a canonical UUID')
        _require(str(UUID(value)) == value, operation + ' ' + key + ' is not canonical')
    request_id = receipt.get('request_id')
    _require(isinstance(request_id, str) and bool(request_id)
             and receipt.get('idempotency_key') == request_id,
             operation + ' request/idempotency identity differs')
    _require(isinstance(receipt.get('request_hash'), str)
             and re.fullmatch(r'[0-9a-f]{64}', receipt['request_hash']) is not None,
             operation + ' request hash is malformed')
    return receipt


def _validate_public_history(calls):
    """Reject reused request, idempotency, operation, or job identity in the observed call log."""
    _require(isinstance(calls, list) and bool(calls), 'public call history unavailable')
    request_ids, operation_ids, job_ids, idempotency_keys = set(), set(), set(), set()
    for record in calls:
        _require(isinstance(record, dict), 'malformed public call history row')
        request_id = record.get('request_id')
        _require(isinstance(request_id, str) and bool(request_id) and request_id not in request_ids,
                 'public request id missing or reused')
        request_ids.add(request_id)
        receipt = record.get('execution')
        if receipt is None:
            continue
        _require(isinstance(receipt, dict), 'malformed public execution receipt')
        if receipt.get('request_id') is not None:
            _require(receipt.get('request_id') == request_id, 'public call and receipt request ids differ')
        key = receipt.get('idempotency_key')
        if key is not None:
            _require(key == request_id and key not in idempotency_keys,
                     'public idempotency key missing, mismatched, or reused')
            idempotency_keys.add(key)
        operation_id = receipt.get('operation_id')
        if operation_id is not None:
            _require(isinstance(operation_id, str)
                     and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', operation_id) is not None
                     and str(UUID(operation_id)) == operation_id
                     and operation_id not in operation_ids,
                     'public operation id malformed or reused')
            operation_ids.add(operation_id)
        job_id = receipt.get('job_id')
        if job_id is not None:
            _require(isinstance(job_id, str)
                     and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', job_id) is not None
                     and str(UUID(job_id)) == job_id and job_id not in job_ids,
                     'public job id malformed or reused')
            job_ids.add(job_id)
        request_hash = receipt.get('request_hash')
        if request_hash is not None:
            _require(isinstance(request_hash, str) and re.fullmatch(r'[0-9a-f]{64}', request_hash) is not None,
                     'public request hash malformed')


def _history_receipt(calls, *, operation, project_id, session_id, model_ref):
    matches = [(index, row) for index, row in enumerate(calls)
               if row.get('operation') == operation
               and isinstance(row.get('execution'), dict)
               and row['execution'].get('model_ref') == model_ref]
    _require(len(matches) == 1, 'exact same-model ' + operation + ' receipt unavailable')
    index, row = matches[0]
    _require(row.get('state') == 'OBSERVED' and row.get('success') is True and row.get('isError') is False,
             operation + ' was not an observed successful public call')
    receipt = _require_public_receipt_shape(row['execution'], operation=operation)
    _require(row.get('request_id') == receipt.get('request_id')
             and receipt.get('project_id') == project_id
             and receipt.get('session_id') == session_id
             and receipt.get('model_ref') == model_ref
             and type(receipt.get('revision')) is int,
             operation + ' project/session/model/revision receipt differs')
    return index, receipt


def verify_count_only_public_response(response, *, dataset, solution, project_id, session_id,
                                      model_ref, revision, summary):
    """Consume a count-only result only when its exact observed public-call lineage is bound."""
    _require(isinstance(response, dict) and response.get('success') is True
             and response.get('error') is None, 'result.at_points public response did not succeed')
    data = response.get('data')
    _require(isinstance(data, dict) and 'field_array' in data and data['field_array'] is None
             and data.get('observation_ref') is None,
             'count-only result must not synthesize a FieldArray or W17 observation reference')
    calls = summary.get('calls') if isinstance(summary, dict) else None
    _validate_public_history(calls)
    _require(calls[-1].get('operation') == 'result.at_points',
             'result.at_points is not the current observed public call')
    row = calls[-1]
    execution = _require_public_receipt_shape(response.get('execution'), operation='result.at_points')
    _require(row.get('state') == 'OBSERVED' and row.get('success') is True and row.get('isError') is False
             and row.get('request_id') == execution.get('request_id')
             and row.get('execution') == execution,
             'public call summary does not bind the exact result execution receipt')
    _require(execution.get('project_id') == project_id
             and execution.get('session_id') == session_id
             and execution.get('model_ref') == model_ref
             and type(execution.get('revision')) is int and execution.get('revision') == revision,
             'result execution project/session/model/current revision differs')
    builder_index, builder = _history_receipt(calls[:-1], operation='code.execute_java',
                                               project_id=project_id, session_id=session_id,
                                               model_ref=model_ref)
    study_index, study = _history_receipt(calls[:-1], operation='study.run',
                                           project_id=project_id, session_id=session_id,
                                           model_ref=model_ref)
    result_index = len(calls) - 1
    _require(builder_index < study_index < result_index
             and type(builder.get('revision')) is int and builder['revision'] < study.get('revision')
             and study.get('revision') == revision,
             'builder/Study/result receipt order or revision lineage differs')
    accepted = verify_points(data, dataset=dataset, solution=solution)
    return {**accepted, 'result_request_id': execution['request_id'],
            'result_operation_id': execution['operation_id'],
            'result_request_hash': execution['request_hash'],
            'receipt_scope': 'exact_public_result_bound_to_builder_study_and_current_model_revision'}


def verify_mesh_pair(coarse, fine):
    for sample in (coarse, fine):
        for name in ('temperature', 'flux'):
            row = sample.get(name)
            _require(isinstance(row, list) and len(row) == 101
                     and all(type(v) in (int, float) and math.isfinite(v) for v in row),
                     'mesh comparison requires all finite 101 points')
    temperature_difference = max(abs(a-b) for a,b in zip(coarse['temperature'], fine['temperature'])) / 100
    flux_difference = max(abs(a-b) for a,b in zip(coarse['flux'], fine['flux'])) / 100000
    _require(temperature_difference <= .001 and flux_difference <= .001, 'coarse/fine tolerance exceeded')
    return {'temperature_normalized_max_difference': temperature_difference,
            'flux_relative_max_difference': flux_difference}


async def run_cycle(call, *, workspace, project_id, session_id, model_ref, revision,
                    fixture, summary, verify_artifact, digest, runtime_id, attached):
    """Two fixed unsolved builds, each solved only by a normal public study.run."""
    path = workspace/'T044ThermalFixture.java'
    with path.open('xb') as handle:
        handle.write(fixture)
    registered = await call('artifact.register', {'project_id': project_id, 'path': path.name,
                            'role': 'java-source', 'classification': 'project_private', 'idempotency_key': 'new'})
    verify_artifact(workspace, registered['data'])
    _require(registered['data']['sha256'] == digest(path), 'registered source SHA differs')
    wanted_runtime = {'runtime_id': runtime_id, 'worker_instance_id': attached['worker_instance_id'],
                      'connection_epoch': attached['worker_epoch'], 'server_instance_id': attached['server_instance_id'],
                      'endpoint': attached['endpoint']['host']+':'+str(attached['endpoint']['port'])}
    samples = []
    for elements in (20, 40):
        if elements == 40:
            created = await call('model.create', {'project_id': project_id, 'session_id': session_id,
                                 'label': 'T044-fine40', 'dimension': 2, 'idempotency_key': 'new'})
            fine_ref = created['data']['model_ref']
            _require(fine_ref != model_ref and fine_ref['model_tag'] != model_ref['model_tag'], 'fresh fine model required')
            model_ref, revision = fine_ref, created['execution']['revision']
        common = {'project_id': project_id, 'session_id': session_id, 'model_ref': model_ref}
        async def action(operation, arguments, mutate=False):
            body = public_action_arguments(operation, common, arguments)
            return await call(operation, body, ref=model_ref,
                              revision=revision if mutate else None)
        built = await action('code.execute_java', {'source_artifact': registered['data']['path'],
                             'entrypoint': 'T044ThermalFixture', 'arguments': {'elements': elements},
                             'mode': 'trusted', 'timeout_s': 60, 'expected_revision': revision,
                             'idempotency_key': 'new'}, True)
        observation = built['execution'].get('runtime_observation', {})
        _require(observation.get('status') == 'OBSERVED' and observation.get('runtime_identity') == wanted_runtime
                 and observation.get('installation_runtime_id') == runtime_id
                 and built['execution'].get('model_ref') == model_ref
                 and built['data'].get('source_sha256') == registered['data']['sha256'], 'actual source/model/runtime producer differs')
        revision = built['execution']['revision']
        builder = built['data']['readback']['readback']
        verify_builder(builder, model_tag=model_ref['model_tag'], elements=elements)
        params = await action('parameter.get', {'names': ['L', 'k', 'Tleft', 'Tright'], 'evaluate': True})
        rows = params['data']['parameters']
        for row, (name, expression, value) in zip(rows, [('L','0.01[m]',.01),('k','10[W/(m*K)]',10),
                                                       ('Tleft','300[K]',300),('Tright','400[K]',400)]):
            _require(row.get('name') == name and row.get('expression') == expression
                     and row.get('unit_error') is None and row.get('evaluate_errors') is None
                     and row.get('evaluated', {}).get('kind') == 'float64'
                     and row.get('evaluated', {}).get('shape') == []
                     and type(row.get('evaluated', {}).get('data')) in (float, int)
                     and row.get('evaluated', {}).get('data') == value, 'parameter SI readback differs')
        _require(len(rows) == 4, 'all four physical parameters required')
        geometry = await action('geometry.inspect', {'path': GEOMETRY})
        verify_geometry(geometry['data'])
        study = await action('study.inspect', {'path': STUDY})
        _require(study['data'].get('study') == 'std1' and study['data'].get('step_count') == 1
                 and study['data']['steps'][0].get('type_id') == 'Stationary', 'single stationary step required')
        solved = await action('study.run', {'study': STUDY, 'timeout_s': 180, 'idempotency_key': 'new'}, True)
        revision = solved['execution']['revision']
        _require(solved['data'].get('computation_timestamp_changed') is True, 'actual computation timestamp did not change')
        listed = await action('dataset.list', {})
        candidates = [v for v in listed['data'].get('datasets', []) if v.get('type_id') == 'Solution']
        _require(len(candidates) == 1 and bool(candidates[0].get('solution')), 'unique actual solution dataset required')
        dataset = candidates[0]
        inspected = await action('dataset.inspect', {'path': dataset['path']})
        binding = inspected['data']
        _require(binding.get('binding_complete') is True and binding.get('solution') == dataset['solution']
                 and binding.get('component') == 'comp1' and binding.get('geometry') == 'geom1', 'actual dataset binding incomplete')
        evaluated = await action('result.at_points', {**point_arguments(dataset['tag']), 'idempotency_key': 'new'})
        result_data = evaluated.get('data') if isinstance(evaluated, dict) else None
        _require(isinstance(result_data, dict), 'result.at_points public data unavailable')
        if result_data.get('field_array') is None:
            accepted = verify_count_only_public_response(
                evaluated, dataset=dataset['tag'], solution=dataset['solution'],
                project_id=project_id, session_id=session_id, model_ref=model_ref,
                revision=revision, summary=summary)
            samples.append({'elements': elements, 'model_ref': model_ref, 'builder': builder,
                            'producer_observation': observation, 'study_run': solved['data'],
                            'numeric': accepted})
        else:
            _require(evaluated['execution'].get('model_ref') == model_ref
                     and bool(result_data.get('observation_ref')),
                     'registered exact-model observation required')
            accepted = verify_points(result_data, dataset=dataset['tag'], solution=dataset['solution'])
            samples.append({'elements': elements, 'model_ref': model_ref, 'builder': builder,
                            'producer_observation': observation, 'study_run': solved['data'],
                            'observation_ref': result_data['observation_ref'], 'numeric': accepted})
        if elements == 20:
            saved = await action('model.save', {'destination': 't044-coarse.mph', 'overwrite': False,
                                 'include_solution': True, 'expected_revision': revision, 'idempotency_key': 'new'}, True)
            revision = saved['execution']['revision']
            saved_path = workspace/saved['data']['destination']
            _require(not saved_path.is_symlink() and digest(saved_path) == saved['data']['sha256']
                     and saved_path.stat().st_size == saved['data']['size_bytes'], 'coarse saved MPH bytes differ')
    inspected = await action('model.inspect', {})
    summary['model_readback'] = inspected['data']
    saved = await action('model.save', {'destination': 't044-fine.mph', 'overwrite': False,
                         'include_solution': True, 'expected_revision': revision, 'idempotency_key': 'new'}, True)
    revision = saved['execution']['revision']
    saved_path = workspace/saved['data']['destination']
    _require(not saved_path.is_symlink() and digest(saved_path) == saved['data']['sha256']
             and saved_path.stat().st_size == saved['data']['size_bytes'], 'fine saved MPH bytes differ')
    summary['saved_model'] = saved['data']; summary['saved_artifact_hash_verified'] = True
    summary['t044_thermal'] = {'scope': 'fixed1Dslab20and40elements', 'samples': samples,
                              'mesh_difference': verify_mesh_pair(samples[0]['numeric'], samples[1]['numeric']),
                              'study_run_calls': 2, 'geometry_GUI': False}
    return model_ref, revision
