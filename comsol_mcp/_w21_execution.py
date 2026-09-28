"""W21 orchestration inside the existing serialized managed Worker call.

No secondary queue or engine and no analytical production output.
"""
import copy
import hashlib
import itertools
import json
import math
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

from ._artifact_store import ArtifactStore, trusted_project_root
from ._execution_contract import PreWriteRefusal
from ._g2_engine import _call
from ._g3_common import bound_model, tag_list
from ._g3_w13 import parameter_get, parameter_set
from ._g3_w16 import study_run
from ._g3_results import result_at_points, dataset_solution_indices
from ._observation_store import current_context, register_observation, resolve_observation, numeric_values


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def require(value, message):
    if not value:
        raise PreWriteRefusal('INVALID_REQUEST', message)


def successful(result):
    status = result.get('status')
    return (result.get('ok', True) is not False
            and not result.get('execution_state_unknown') and not result.get('failed') and not result.get('not_executed')
            and (status.get('ok') is True if isinstance(status, dict)
                 else status in {'PASS', 'COMPLETED', 'SUCCEEDED', 'APPLIED', 'VERIFIED'}))


def _execution_is_unknown(value):
    if not isinstance(value, dict):
        return False
    status = value.get('status')
    code = value.get('code')
    error = value.get('error')
    return (value.get('execution_state_unknown') is True
            or status in {'UNKNOWN', 'EXECUTION_STATE_UNKNOWN', 'ENGINE_STATE_UNKNOWN'}
            or code in {'EXECUTION_STATE_UNKNOWN', 'ENGINE_UNRESPONSIVE'}
            or isinstance(error, dict) and error.get('code') in {'EXECUTION_STATE_UNKNOWN', 'ENGINE_UNRESPONSIVE'})


def _exception_is_unknown(exc):
    code = getattr(exc, 'code', None)
    return code in {'EXECUTION_STATE_UNKNOWN', 'ENGINE_UNRESPONSIVE', 'WORKER_TIMEOUT'}


def key_for(kind, tag, name):
    return kind + ':' + digest([current_context()['model_ref'], tag, name])


def persist(kind, key, data):
    ctx = current_context()
    record = dict(kind=kind, model_ref=ctx['model_ref'], producer=ctx['producer'], **data)
    record['sha256'] = digest(record)
    ctx['store'].persist_artifact(key, record)
    return record


def validate_parameters(worker, tag, values, units):
    require(isinstance(values, dict) and values and set(values) == set(units), 'Explicit values and units required for every parameter')
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values.values()), 'Finite parameter values required')
    # Read first: missing parameters must not be silently created.
    parameter_get(worker, tag, {'names': list(values), 'evaluate': True})
    return [{'name': name, 'expression': repr(float(value)) + '[' + units[name] + ']', 'unit': units[name]}
            for name, value in values.items()]


def apply_parameters(worker, tag, values, units):
    items = validate_parameters(worker, tag, values, units)
    result = parameter_set(worker, tag, {'parameters': items})
    require(successful(result), 'Parameter write/readback failed: ' + str(result))
    return parameter_get(worker, tag, {'names': list(values), 'evaluate': True})


def _case_parameter_readback(worker, tag, values, units, attempt_id, readback=None):
    """Bind each experiment parameter to a native scalar in its declared unit.

    ``ModelParam.evaluate(String)`` returns a value in the model root's base
    unit system. For dimensional parameters, explicitly request the frozen
    declared unit so the returned scalar can be compared with the exact grid
    value without inventing a client-side conversion.
    """
    if readback is None:
        readback = parameter_get(worker, tag, {'names': list(values), 'evaluate': True})
    rows = readback.get('parameters') if isinstance(readback, dict) else None
    require(isinstance(rows, list), 'Native parameter readback is unavailable')
    from ._g3_common import bound_model
    model = bound_model(worker, tag)
    by_name = {row.get('name'): row for row in rows if isinstance(row, dict)}
    require(set(by_name) == set(values), 'Native parameter readback names do not match the frozen case')
    for name, expected in values.items():
        row = by_name[name]
        unit = units[name]
        declared_unit = row.get('unit')
        if unit in {'1', 'dimensionless'}:
            require(declared_unit in {None, '1', 'dimensionless'},
                    f'Native parameter unit for {name!r} does not match the frozen dimensionless unit')
            evaluated = row.get('evaluated')
            measured = evaluated.get('data') if isinstance(evaluated, dict) else None
            source = 'ModelParam.evaluate(String); dimensionless base-unit readback'
            observed_unit = '1'
        else:
            require(declared_unit == unit,
                    f'Native parameter unit for {name!r} does not match the frozen declared unit')
            group = row.get('group')
            parameter_node = _call(model, 'param', group) if isinstance(group, str) and group else _call(model, 'param')
            measured = _call(parameter_node, 'evaluate', name, unit)
            source = 'ModelParam.evaluate(String,String); explicitly requested declared unit'
            observed_unit = unit
        require(not isinstance(measured, bool) and isinstance(measured, (int, float))
                and math.isfinite(float(measured)),
                f'Native parameter value for {name!r} is not a finite real scalar')
        # Parameter values are written as an exact decimal scalar with this
        # same unit. A small relative allowance accommodates the engine's
        # unit conversion round trip; zero remains exact.
        require(math.isclose(float(measured), float(expected), rel_tol=1e-12, abs_tol=0.0),
                f'Native parameter readback for {name!r} differs from the frozen case value')
        row['case_attempt_id'] = attempt_id
        row['case_value_readback'] = {
            'value': float(measured), 'unit': observed_unit, 'source': source,
            'expected_value': float(expected), 'relative_tolerance': 1e-12,
        }
    readback['case_attempt_id'] = attempt_id
    readback['value_comparison'] = 'native_scalar_in_frozen_declared_unit_with_rel_tol_1e-12'
    return readback


def _verify_experiment_case_attempt(ctx, case_id, study, values, units, metric_definitions, binding):
    """Check the durable attempt claim before any Worker work for this case."""
    semantic_case_id = binding.get('case_id')
    execution_case_id = binding.get('execution_case_id')
    experiment_id = binding.get('experiment_id')
    run_id = binding.get('run_id')
    attempt_id = binding.get('attempt_id')
    if (not isinstance(semantic_case_id, str) or not semantic_case_id.startswith('case-')
            or execution_case_id != case_id
            or case_id != f'{experiment_id}:{semantic_case_id}'
            or not isinstance(experiment_id, str) or not experiment_id.startswith('exp_')
            or not isinstance(run_id, str) or not run_id.startswith('run_')
            or not isinstance(attempt_id, str) or not attempt_id.startswith('att_')):
        raise PreWriteRefusal('INVALID_METRIC_CASE_BINDING',
                              'metric evaluation requires a semantic case id and its exact internal execution key')
    key = f'w21experimentattempt:{experiment_id}:{semantic_case_id}'
    attempt = ctx['store'].get_metadata('artifacts', key)
    attempt_payload = ({name: value for name, value in attempt.items() if name != 'sha256'}
                       if isinstance(attempt, dict) else None)
    metric_refs = [
        {'metric_id': row.get('metric_id'), 'version': row.get('version'),
         'definition_sha256': row.get('definition_sha256')}
        for row in metric_definitions if isinstance(row, dict)
    ]
    valid = (
        isinstance(attempt, dict)
        and attempt.get('kind') == 'w21experiment_case_attempt'
        and attempt.get('attempt_id') == attempt_id
        and attempt.get('experiment_id') == experiment_id
        and attempt.get('run_id') == run_id
        and attempt.get('case_id') == semantic_case_id
        and type(attempt.get('case_ordinal')) is int
        and type(binding.get('case_ordinal')) is int
        and attempt.get('case_ordinal') == binding.get('case_ordinal')
        and attempt.get('study') == study
        and attempt.get('project_id') == ctx.get('project_id')
        and attempt.get('session_id') == ctx.get('model_ref', {}).get('session_id')
        and attempt.get('model_ref') == ctx.get('model_ref')
        and attempt.get('model_revision') == ctx.get('revision')
        and attempt.get('producer') == ctx.get('producer')
        and attempt.get('design_sha256') == binding.get('design_sha256')
        and attempt.get('parameters') == values
        and attempt.get('units') == units
        and attempt.get('metric_definition_refs') == metric_refs
        and attempt.get('sha256') == digest(attempt_payload)
        and attempt.get('sha256') == binding.get('attempt_sha256')
        and binding.get('planned_parameters') == values
        and binding.get('study') == study
    )
    if not valid:
        raise PreWriteRefusal('INVALID_METRIC_CASE_BINDING',
                              'durable experiment attempt is absent, changed, or belongs to another case')
    return attempt


def op_parameter_case_manage(worker, model_tag, arguments):
    action = arguments.get('action')
    group = arguments.get('group', 'default')
    case = arguments.get('case_tag')
    ctx = current_context()
    key = key_for('w21case', model_tag, [group, case])
    if action == 'create':
        values, units = arguments.get('values'), arguments.get('units', {})
        validate_parameters(worker, model_tag, values, units)
        require(ctx['store'].get_metadata('artifacts', key) is None, 'Case already exists')
        return persist('w21case', key, dict(group=group, case_id=case, parameters=values, units=units, status='COMPLETE', case_status='REGISTERED'))
    if action == 'list':
        return {'status': 'PASS', 'cases': [r for r in ctx['store'].list_metadata('artifacts')
                if r.get('kind') == 'w21case' and r.get('model_ref') == ctx['model_ref'] and r.get('group') == group]}
    record = ctx['store'].get_metadata('artifacts', key)
    require(record and record.get('kind') == 'w21case', 'Case not registered')
    if action == 'inspect':
        return record
    require(action == 'apply', 'Supported actions: create/list/inspect/apply')
    readback = apply_parameters(worker, model_tag, record['parameters'], record['units'])
    record.update(status='APPLIED', readback=readback)
    ctx['store'].persist_artifact(key, record)
    return record


def exported_input_identity(source, model):
    """Replace export-only interpolation paths with verified payload identities.

    COMSOL extracts imported tables to a new temporary directory on every Java
    export. Keep the live filename binding and hash those extracted bytes; never
    remove arbitrary paths or assume declared input files equal imported data.
    """
    bindings = []
    pattern = re.compile(r'(model\.func\(("(?:\\.|[^"\\])*")\)\s*\.set\("filename",\s*)("(?:\\.|[^"\\])*")(\);)')

    def replace(match):
        try:
            function = _call(model, 'func', json.loads(match.group(2)))
            if _call(function, 'getType') != 'Interpolation':
                return match.group(0)
            filename = _call(function, 'getString', 'filename')
            payload = Path(json.loads(match.group(3))).read_bytes()
            bindings.append({'function': json.loads(match.group(2)), 'filename': filename,
                             'imported_sha256': hashlib.sha256(payload).hexdigest()})
            return match.group(1) + json.dumps('sha256:' + hashlib.sha256(payload).hexdigest()) + match.group(4)
        except Exception:
            # An unreadable import cannot become a reusable cache identity.
            bindings.append({'unresolved_import': uuid.uuid4().hex})
            return match.group(0)

    return pattern.sub(replace, source), bindings


def model_identity(worker, tag, parameter_names, definition, study):
    """Hash engine-exported model configuration, actual build and input bytes.

    Only the swept parameter expressions are removed; their exact requested
    values/units belong to the cache key. Solver/mesh/material settings remain.
    """
    from ._g3_runtime import _engine_identity
    artifacts = ArtifactStore(trusted_project_root(worker))
    path = artifacts.resolve_safe_path('w21/config/Config' + uuid.uuid4().hex + '.java')
    path.parent.mkdir(parents=True, exist_ok=True)
    model = bound_model(worker, tag)
    # RemoteModel.save is the atomic MPH publisher, whose second argument is
    # copy (not the Java file type). Dispatch this documented native overload
    # explicitly through the same Worker/witness channel.
    from ._java_worker import RemoteJava
    # Compact command history into the current configuration. This does not
    # reset a physical solution/history; prior exports remain append-only.
    _call(model, 'resetHist')
    if isinstance(model, RemoteJava):
        model._call('save', str(path), 'java')
    else:
        _call(model, 'save', str(path), 'java')
    source, imported_inputs = exported_input_identity(path.read_text(encoding='utf-8'), model)
    # COMSOL Java export header carries wall-clock time and class filename.
    lines = []
    for line in source.splitlines():
        if line.lstrip().startswith(('*', '//', '/*', 'package ', 'public class ')):
            continue
        if any(re.search(r'\.param\(\)\.set\("' + re.escape(name) + r'"\s*,', line) for name in parameter_names):
            continue
        lines.append(line)
    inputs = []
    for name in definition.get('input_artifacts', []):
        input_path = artifacts.resolve_safe_path(name, allow_overwrite=True)
        inputs.append([str(input_path), hashlib.sha256(input_path.read_bytes()).hexdigest()])
    initial_sources = []
    cache_policy = 'REUSE_VERIFIED_CONFIGURATION'
    try:
        from ._observation_store import solution_identity
        study_node = _call(model, 'study', study)
        for step_tag in tag_list(_call(study_node, 'feature')):
            step = _call(study_node, 'feature', step_tag)
            if _call(step, 'getString', 'useinitsol') != 'on':
                continue
            source_study = _call(step, 'getString', 'initstudy')
            source_solutions = [sol for sol in tag_list(_call(model, 'sol'))
                                if _call(_call(model, 'sol', sol), 'study') == source_study]
            require(source_solutions, 'Initial source solution is not resolved')
            initial_sources.extend(solution_identity(worker, tag, sol) for sol in source_solutions)
    except Exception as exc:
        # Unknown initial dependencies must never share a cache identity.
        # The actual model can still solve within the normal dispatch budget.
        cache_policy = 'BYPASS_UNRESOLVED_INITIAL_DEPENDENCY: ' + str(exc)
        initial_sources = [{'non_reusable_request': uuid.uuid4().hex}]
    return dict(model_ref=current_context()['model_ref'], configuration_sha256=digest(lines),
                engine=_engine_identity(worker), inputs=inputs, imported_inputs=imported_inputs, initial_sources=initial_sources,
                cache_policy=cache_policy)


def validate_definition(worker, tag, study, definition):
    require(isinstance(study, str) and study, 'Explicit study tag required')
    model = bound_model(worker, tag)
    require(study in tag_list(_call(model, 'study')), 'Requested study does not exist')
    require(isinstance(definition, dict) and definition.get('sample'), 'Explicit definition.sample required')
    sample = definition['sample']
    require(sample.get('spec', {}).get('solution', {}).get('dataset'), 'Explicit stored dataset required')
    require(sample.get('spec', {}).get('expressions') and sample.get('points'), 'Expressions and points required')
    require(definition.get('metrics'), 'Explicit result metrics required')
    require(definition.get('times'), 'Explicit stored times required')
    require(definition.get('validation', {}).get('range'), 'W20 selected validation range required')


def extract_metrics(sample, metrics):
    fa = sample.get('field_array')
    require(fa and fa.get('axes') == ['expression', 'outer', 'inner', 'point'], 'Complete W17 FieldArray required')
    result = {}
    for name, selector in metrics.items():
        require(selector.get('unit'), 'Metric unit required')
        expression = selector['expression']
        expressions = sample['expressions']
        require(expression in expressions, 'Metric expression was not sampled')
        index = expressions.index(expression)
        units = fa.get('units', {}).get('expression', {})
        actual_unit = units.get(expression) if isinstance(units, dict) else units[index]
        require(actual_unit == selector['unit'], f'Metric unit mismatch: {actual_unit}')
        value = sample['values'][index]
        for axis_index in selector['indices']:
            require(type(axis_index) is int and axis_index >= 0, 'Nonnegative array index required')
            value = value[axis_index]
        require(isinstance(value, (int, float)) and math.isfinite(value), 'Metric is missing or nonfinite')
        if 'target' in selector:
            value = abs(value - float(selector['target']))
        result[name] = value
    return result


def stored_times(worker, tag, sample):
    indices = dataset_solution_indices(worker, tag, {'path': sample['dataset']})
    require(indices.get('binding_complete') and indices.get('solution') == sample['solution'],
            'Complete native stored-solution binding required: ' + str(indices))
    times = indices.get('time_values')
    require(isinstance(times, list) and times, 'Native stored time axis unavailable: ' + str(indices))
    require(len(times) == len(sample['values'][0][0]), 'Native time count differs from sampled inner axis')
    return times, indices


def _save_experiment_case_model_copy(
    worker, tag, *, binding, study, values, units, parameter_readback,
    sample, solution_indices, solution_identity, sample_observation_ref,
):
    """Save the current case model through its same Worker before the next case.

    The file is a hash-verified project artifact.  COMSOL reload/restore is a
    separate native acceptance step and is deliberately left unclaimed here.
    """
    from ._artifact_store import ArtifactStore, trusted_project_root

    base = {
        'schema_version': 1,
        'kind': 'w21_experiment_case_model_artifact',
        'project_id': binding['project_id'],
        'experiment_id': binding['experiment_id'],
        'run_id': binding['run_id'],
        'case_id': binding['case_id'],
        'case_ordinal': binding['case_ordinal'],
        'attempt_id': binding['attempt_id'],
        'attempt_sha256': binding['attempt_sha256'],
        'producer': binding['producer'],
        'session_id': binding['session_id'],
        'model_ref': copy.deepcopy(binding['model_ref']),
        'model_revision': binding['model_revision'],
        'revision_scope': 'run_admission_revision; exact case solution is separately bound below',
        'design_sha256': binding['design_sha256'],
        'study': study,
        'parameters': copy.deepcopy(values),
        'units': copy.deepcopy(units),
        'parameter_readback_sha256': digest(parameter_readback),
        'dataset': sample.get('dataset'),
        'solution': sample.get('solution'),
        'solution_indices': copy.deepcopy(solution_indices),
        'solution_identity': copy.deepcopy(solution_identity),
        'solution_identity_sha256': digest(solution_identity),
        'sample_observation_ref': copy.deepcopy(sample_observation_ref),
        'format': 'mph',
        'save_copy': True,
        'save_api': 'RemoteModel.save(path, saveCopy=True)',
        'verification': 'ATOMIC_MPH_ZIP_CRC_AND_SHA256',
        'restore_status': 'NOT_RELOAD_VERIFIED',
    }

    def failed(reason_code, stage=None):
        return {
            **base,
            'status': 'SAVE_FAILED',
            'reason_code': reason_code,
            'stage': stage if isinstance(stage, str) and stage else None,
            'restore_status': 'NOT_AVAILABLE',
        }

    try:
        experiment_id = binding.get('experiment_id')
        run_id = binding.get('run_id')
        case_id = binding.get('case_id')
        attempt_id = binding.get('attempt_id')
        if (not isinstance(experiment_id, str) or re.fullmatch(r'exp_[0-9a-f]{32}', experiment_id) is None
                or not isinstance(run_id, str) or re.fullmatch(r'run_[0-9a-f]{32}', run_id) is None
                or not isinstance(case_id, str) or re.fullmatch(r'case-[0-9]{4}', case_id) is None
                or not isinstance(attempt_id, str) or re.fullmatch(r'att_[0-9a-f]{32}', attempt_id) is None):
            return failed('INVALID_CASE_ARTIFACT_IDENTITY')
        relative_path = (
            f'w21/experiment_cases/{experiment_id}/{run_id}/{case_id}/{attempt_id}.mph'
        )
        artifact_store = ArtifactStore(trusted_project_root(worker))
        destination = artifact_store.resolve_safe_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creating directories to catch a symlink inserted
        # between the initial path check and the creation.
        destination = artifact_store.resolve_safe_path(relative_path)
        saved = bound_model(worker, tag).save(str(destination), True, overwrite=False)
        artifact = saved.get('artifact') if isinstance(saved, dict) else None
        if (not isinstance(saved, dict)
                or saved.get('path') != str(destination)
                or not isinstance(artifact, dict)
                or artifact.get('path') != str(destination)
                or artifact.get('verified') is not True
                or type(artifact.get('size')) is not int or artifact['size'] <= 0
                or not isinstance(artifact.get('sha256'), str)
                or re.fullmatch(r'[0-9a-f]{64}', artifact['sha256']) is None):
            return failed('SAVE_RESULT_UNVERIFIED')
        return {
            **base,
            'status': 'SAVED_HASH_VERIFIED_RELOAD_UNVERIFIED',
            'relative_path': relative_path,
            'file_sha256': artifact['sha256'],
            'file_size': artifact['size'],
            'save_copy': True,
            'restore_status': 'NOT_RELOAD_VERIFIED',
        }
    except Exception as exc:
        if _exception_is_unknown(exc):
            raise
        stage = getattr(exc, 'stage', None)
        code = getattr(exc, 'code', None)
        reason = code if isinstance(code, str) else 'SAVE_COPY_FAILED'
        return failed(reason, stage)


def execute_case(worker, tag, study, definition, values, budget, case_id, *,
                 metric_definitions=None, experiment_binding=None):
    from ._g3_w21 import ResultCache
    from ._g3_w20_validation import validate_solution
    ctx = current_context()
    metric_definitions = [] if metric_definitions is None else metric_definitions
    metric_association_requested = bool(metric_definitions)
    experiment_case_requested = experiment_binding is not None or metric_association_requested
    if experiment_case_requested:
        if (not isinstance(experiment_binding, dict)
                or not isinstance(experiment_binding.get('attempt_id'), str)
                or not experiment_binding.get('attempt_id')
                or experiment_binding.get('project_id') != ctx.get('project_id')
                or experiment_binding.get('model_ref') != ctx.get('model_ref')
                or experiment_binding.get('model_revision') != ctx.get('revision')
                or experiment_binding.get('producer') != ctx.get('producer')
                or experiment_binding.get('session_id') != ctx.get('model_ref', {}).get('session_id')):
            raise PreWriteRefusal('INVALID_METRIC_CASE_BINDING',
                                  'metric evaluation requires the exact server-created experiment attempt binding')
        _verify_experiment_case_attempt(
            ctx, case_id, study, values, definition['units'], metric_definitions, experiment_binding,
        )
    units = definition['units']
    validate_parameters(worker, tag, values, units)
    identity = model_identity(worker, tag, values, definition, study)
    cache_key = ResultCache.compute_key(digest(identity), values, dict(definition=definition, study=study), identity['engine']['comsol_version'])
    cache = ResultCache(ctx['store'])
    # Every experiment case must prove its exact attempt and own a same-Worker
    # model copy; a previous W21 cache entry cannot provide that identity.
    record = None if experiment_case_requested else cache.get(cache_key)
    if record and record.get('status') == 'COMPLETED':
        result = copy.deepcopy(record['result'])
        require(digest(result) == record['result_digest'], 'Cached result integrity mismatch')
        _, original = resolve_observation(worker, tag, result['observation_ref'], allow_historical=True)
        require(digest(original) == digest(result['sample']), 'Cached W17 sample differs from original artifact')
        result.update(status='CACHED', cache_hit=True, requested_case_id=case_id)
        return result
    if not budget.can_evaluate():
        result = {'status': 'NOT_RUN', 'reason': 'BUDGET_EXHAUSTED', 'case_id': case_id, 'cache_hit': False}
        if experiment_case_requested:
            result['case_attempt_id'] = experiment_binding['attempt_id']
        return result
    # The outer managed queue serializes this check with dispatch; no inner enqueue.
    budget.cases_evaluated += 1
    result = {'case_id': case_id, 'parameters': values, 'units': units, 'producer': ctx['producer'],
              'model_ref': ctx['model_ref'], 'cache_hit': False, 'engine': identity['engine'], 'cache_policy':identity['cache_policy']}
    if experiment_case_requested:
        result['case_attempt_id'] = experiment_binding['attempt_id']
    try:
        parameter_readback = apply_parameters(worker, tag, values, units)
        if experiment_case_requested:
            parameter_readback = _case_parameter_readback(
                worker, tag, values, units, experiment_binding['attempt_id'], parameter_readback,
            )
        result['parameter_readback'] = parameter_readback
        solve_result = study_run(worker, tag, {'study': {'segments': [{'collection': 'study', 'tag': study}]}})
        if experiment_case_requested:
            require(isinstance(solve_result, dict), 'Study execution did not return a structured native result')
            solve_result = copy.deepcopy(solve_result)
            solve_result['case_attempt_id'] = experiment_binding['attempt_id']
        result['solve'] = solve_result
        if _execution_is_unknown(result['solve']):
            budget.cases_failed += 1
            budget.total_failures += 1
            result.update(status='UNKNOWN', error='Study execution state is unknown; reconcile before another case')
            persist('w21result', key_for('w21result', tag, [ctx['producer'], case_id]), {'result': result})
            return result
        require(successful(result['solve']), 'Solve failed or remains unverified')
        sample = result_at_points(worker, tag, definition['sample'])
        require(successful(sample), 'W17 sampling failed')
        if experiment_case_requested:
            require(isinstance(sample, dict), 'W21 sample did not return a structured native result')
            sample = copy.deepcopy(sample)
            sample['case_attempt_id'] = experiment_binding['attempt_id']
        result['sample'] = sample
        times, result['solution_indices'] = stored_times(worker, tag, sample)
        require(times == definition['times'], 'Requested times do not match stored solution times: ' + str(times))
        source_identity = None
        if experiment_case_requested:
            from ._observation_store import solution_identity
            source_identity = solution_identity(worker, tag, sample['solution'])
            result['case_solution_identity'] = copy.deepcopy(source_identity)
            result['case_solution_identity_sha256'] = digest(source_identity)
        result['observation_ref'] = register_observation(worker, tag, sample)
        result['validation'] = validate_solution(worker, tag, {'solution': {'dataset': sample['dataset']},
            'observation_ref': result['observation_ref'], 'criteria': definition['validation']})
        require(result['validation']['numerical_verification_status'] == 'PASS', 'W20 validation failed')
        result.update(extract_metrics(sample, definition['metrics']))
        if experiment_case_requested:
            result['case_model_artifact'] = _save_experiment_case_model_copy(
                worker, tag, binding=experiment_binding, study=study, values=values, units=units,
                parameter_readback=result['parameter_readback'], sample=sample,
                solution_indices=result['solution_indices'], solution_identity=source_identity,
                sample_observation_ref=result['observation_ref'],
            )
        if metric_association_requested:
            from ._g3_metrics import evaluate_experiment_case_metrics
            case_proof = {
                **copy.deepcopy(experiment_binding),
                'study': study,
                'parameters': copy.deepcopy(values),
                'units': copy.deepcopy(units),
                'parameter_readback': copy.deepcopy(result['parameter_readback']),
                'dataset': sample['dataset'],
                'solution': sample['solution'],
                'solution_indices': copy.deepcopy(result['solution_indices']),
                'solution_identity': source_identity,
                'solution_identity_sha256': digest(source_identity),
                'sample_observation_ref': copy.deepcopy(result['observation_ref']),
                'solve_status': copy.deepcopy(result['solve'].get('status')) if isinstance(result.get('solve'), dict) else None,
                'solve_result_sha256': digest(result['solve']),
                'sample_status': copy.deepcopy(sample.get('status')),
                'validation_status': result['validation']['numerical_verification_status'],
            }
            result['metric_evaluation'] = evaluate_experiment_case_metrics(
                worker, tag, metric_definitions, case_proof=case_proof,
            )
        result['status'] = 'COMPLETED'
        budget.cases_failed = 0
        post_identity = model_identity(worker, tag, values, definition, study)
        post_key = ResultCache.compute_key(digest(post_identity), values, dict(definition=definition, study=study), post_identity['engine']['comsol_version'])
        if not experiment_case_requested:
            cache.store(post_key, {'status': 'COMPLETED', 'identity': post_identity,
                    'result': result, 'result_digest': digest(result)})
    except Exception as exc:
        budget.cases_failed += 1
        budget.total_failures += 1
        result.update(status='UNKNOWN' if _exception_is_unknown(exc) else 'FAILED', error=str(exc))
    persist('w21result', key_for('w21result', tag, [ctx['producer'], case_id]), {'result': result})
    return result


def op_study_sweep_manage(worker, model_tag, arguments):
    from ._g3_w21 import ComputationBudget
    require(arguments.get('action', 'run') == 'run', 'Only run is supported')
    definition, study = arguments.get('definition'), arguments.get('study')
    validate_definition(worker, model_tag, study, definition)
    parameters = definition.get('parameters')
    require(isinstance(parameters, dict) and parameters, 'Explicit parameter grid required')
    budget = ComputationBudget(max_cases=arguments.get('max_cases', 30), max_wall_time_s=arguments.get('max_wall_time_s', 300))
    cases = []
    for index, point in enumerate(itertools.product(*parameters.values()), 1):
        values = dict(zip(parameters, point))
        case = execute_case(worker, model_tag, study, definition, values, budget, f'case-{index}')
        case['case_ordinal'] = index
        cases.append(case)
        if case['status'] in {'FAILED', 'UNKNOWN', 'NOT_RUN'}:
            break
    unknown = any(c['status'] == 'UNKNOWN' for c in cases)
    failed = any(c['status'] == 'FAILED' for c in cases)
    budget_stopped = any(c['status'] == 'NOT_RUN' for c in cases)
    # A bounded scheduler completed its request when it stops before dispatch.
    # Calling this PARTIAL would poison the shared revision ledger despite the
    # known completed case and explicitly unstarted remainder.
    return {'status': 'EXECUTION_STATE_UNKNOWN' if unknown else 'FAILED' if failed else 'COMPLETE',
            'execution_state_unknown': unknown,
            'completion_status': 'EXECUTION_STATE_UNKNOWN' if unknown else 'CASE_FAILED' if failed else 'BUDGET_EXHAUSTED' if budget_stopped else 'ALL_CASES_COMPLETED',
            'cases': cases, 'index_table': {'cases': [{'case_id': c['case_id'], 'case_ordinal': c['case_ordinal'],
            'parameters': c.get('parameters'), 'solution': c.get('sample', {}).get('solution'),
            'axes': c.get('sample', {}).get('field_array')} for c in cases]},
            'budget': budget.status(), 'cache_hits': sum(c.get('cache_hit', False) for c in cases)}


def op_optimization_bounded_run(worker, model_tag, arguments):
    from ._g3_w21 import BoundedOptimizer, ComputationBudget
    definition, study = arguments.get('definition'), arguments.get('study')
    validate_definition(worker, model_tag, study, definition)
    require(arguments.get('parameter_bounds') and arguments.get('objective_name') and arguments.get('constraints'),
            'Bounds, objective and constraints required')
    budget = ComputationBudget(max_cases=arguments.get('max_cases',25), max_wall_time_s=arguments.get('max_wall_time_s',300))
    optimizer = BoundedOptimizer(arguments['objective_name'], arguments.get('minimize',True), arguments['parameter_bounds'], arguments['constraints'], budget)
    # Optimizer owns candidate budget, executor owns compute count. Cache hits
    # do not consume the compute budget; both figures are returned.
    compute_budget = ComputationBudget(max_cases=budget.max_cases, max_wall_time_s=budget.max_wall_time_s)
    unknown_case = {'result': None}
    def evaluate(params):
        result = execute_case(worker, model_tag, study, definition, params, compute_budget, 'opt-' + str(len(optimizer.history)+1))
        if result.get('status') == 'UNKNOWN':
            unknown_case['result'] = result
        return result
    result = optimizer.run_bounded_search(arguments.get('grid_points_per_dim',3), evaluate)
    result['search_status'] = result['status']
    if unknown_case['result'] is not None:
        result['status'] = 'EXECUTION_STATE_UNKNOWN'
        result['execution_state_unknown'] = True
        result['stopped_case'] = unknown_case['result']
    else:
        result['status'] = 'COMPLETE'
    result['compute_budget'] = compute_budget.status()
    result['optimality'] = 'BEST_VERIFIED_FEASIBLE_SO_FAR' if result['best_candidate'] else 'NO_FEASIBLE_FOUND'
    return result


def _strict_grid_design(worker, model_tag, definition):
    """Validate the intentionally narrow, predeclared W21 experiment profile."""
    require(isinstance(definition, dict), 'definition must be an object')
    allowed = {'study', 'sample', 'metrics', 'times', 'validation', 'parameters', 'units', 'sampling', 'budget',
               'metric_evaluations', 'objective', 'constraints'}
    required_fields = allowed - {'metric_evaluations', 'objective', 'constraints'}
    require(required_fields.issubset(definition) and set(definition).issubset(allowed),
            'Supported experiment definition fields are: ' + ', '.join(sorted(allowed)))
    require(definition.get('sampling') == {'kind': 'cartesian_grid'},
            'Only an explicitly declared cartesian_grid sampling profile is supported')
    validate_definition(worker, model_tag, definition['study'], definition)
    parameters, units = definition.get('parameters'), definition.get('units')
    require(isinstance(parameters, dict) and parameters and isinstance(units, dict)
            and set(parameters) == set(units), 'Grid parameter names and units must match exactly')
    for name, values in parameters.items():
        require(isinstance(name, str) and name, 'Parameter names must be nonempty strings')
        require(isinstance(values, list) and values, f'Grid values for {name!r} must be a nonempty array')
        require(all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values),
                f'Grid values for {name!r} must be finite numbers')
        require(len({float(v) for v in values}) == len(values), f'Grid values for {name!r} must be unique')
        require(isinstance(units[name], str) and units[name], f'Unit for {name!r} is required')
    first = {name: float(values[0]) for name, values in parameters.items()}
    validate_parameters(worker, model_tag, first, units)
    budget = definition.get('budget')
    require(isinstance(budget, dict) and set(budget) == {'max_cases', 'max_wall_time_s'},
            'A frozen budget with max_cases and max_wall_time_s is required')
    max_cases, max_wall = budget.get('max_cases'), budget.get('max_wall_time_s')
    require(type(max_cases) is int and 1 <= max_cases <= 500, 'budget.max_cases must be an integer in [1, 500]')
    require(isinstance(max_wall, (int, float)) and not isinstance(max_wall, bool)
            and math.isfinite(max_wall) and 0 < max_wall <= 86400,
            'budget.max_wall_time_s must be finite and in (0, 86400]')
    points = list(itertools.product(*(parameters[name] for name in parameters)))
    require(len(points) <= 500, 'Cartesian design exceeds the 500-case definition limit')
    cases = []
    for ordinal, point in enumerate(points, 1):
        cases.append({'case_id': f'case-{ordinal:04d}',
                      'parameters': {name: float(value) for name, value in zip(parameters, point)}})
    metric_refs = definition.get('metric_evaluations')
    if metric_refs is not None:
        require(isinstance(metric_refs, list) and metric_refs,
                'metric_evaluations must be a non-empty array of exact immutable version references')
        seen_metric_ids = set()
        for reference in metric_refs:
            require(isinstance(reference, dict)
                    and set(reference) == {'metric_id', 'version', 'definition_sha256'},
                    'metric_evaluations entries require only metric_id, version, and definition_sha256')
            metric_id = reference.get('metric_id')
            version = reference.get('version')
            definition_hash = reference.get('definition_sha256')
            require(isinstance(metric_id, str) and metric_id and metric_id not in seen_metric_ids,
                    'metric_evaluations metric_id values must be nonempty and unique')
            require(type(version) is int and version >= 1,
                    'metric_evaluations.version must be a positive integer')
            require(isinstance(definition_hash, str) and re.fullmatch(r'[0-9a-f]{64}', definition_hash) is not None,
                    'metric_evaluations.definition_sha256 must be a lowercase SHA-256 digest')
            seen_metric_ids.add(metric_id)
    has_objective = 'objective' in definition
    has_constraints = 'constraints' in definition
    require(has_objective == has_constraints,
            'objective and constraints must be supplied together; constraints may be an explicit empty array')
    if has_objective:
        objective = definition.get('objective')
        require(isinstance(objective, dict)
                and set(objective) == {'metric_ref', 'tuple', 'direction', 'unit'},
                'objective requires only metric_ref, tuple, direction, and unit')
        _validate_objective_metric_reference(objective.get('metric_ref'), 'objective.metric_ref')
        _validate_metric_tuple(objective.get('tuple'), 'objective.tuple')
        require(objective.get('direction') in {'minimize', 'maximize'},
                'objective.direction must be minimize or maximize')
        require(isinstance(objective.get('unit'), str) and objective['unit'],
                'objective.unit must be a nonempty exact observed unit')
        constraints = definition.get('constraints')
        require(isinstance(constraints, list), 'constraints must be an array')
        seen_constraint_ids = set()
        for index, constraint in enumerate(constraints):
            label = f'constraints[{index}]'
            require(isinstance(constraint, dict)
                    and set(constraint) == {'constraint_id', 'metric_ref', 'tuple', 'relation', 'value', 'unit'},
                    f'{label} requires only constraint_id, metric_ref, tuple, relation, value, and unit')
            constraint_id = constraint.get('constraint_id')
            require(isinstance(constraint_id, str) and constraint_id and constraint_id not in seen_constraint_ids,
                    f'{label}.constraint_id must be nonempty and unique')
            seen_constraint_ids.add(constraint_id)
            _validate_objective_metric_reference(constraint.get('metric_ref'), label + '.metric_ref')
            _validate_metric_tuple(constraint.get('tuple'), label + '.tuple')
            require(constraint.get('relation') in {'<', '<=', '>', '>='},
                    f'{label}.relation must be <, <=, >, or >=')
            bound = constraint.get('value')
            require(isinstance(bound, (int, float)) and not isinstance(bound, bool)
                    and math.isfinite(bound), f'{label}.value must be a finite real number')
            require(isinstance(constraint.get('unit'), str) and constraint['unit'],
                    f'{label}.unit must be a nonempty exact observed unit')
    return cases


def _validate_objective_metric_reference(reference, label):
    require(isinstance(reference, dict)
            and set(reference) == {'metric_id', 'version', 'definition_sha256'},
            f'{label} requires only metric_id, version, and definition_sha256')
    require(isinstance(reference.get('metric_id'), str) and reference['metric_id'],
            f'{label}.metric_id must be a nonempty string')
    require(type(reference.get('version')) is int and reference['version'] >= 1,
            f'{label}.version must be a positive integer')
    require(isinstance(reference.get('definition_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', reference['definition_sha256']) is not None,
            f'{label}.definition_sha256 must be a lowercase SHA-256 digest')


def _validate_metric_tuple(value, label):
    require(isinstance(value, dict) and set(value) == {'outer', 'inner'},
            f'{label} requires exactly outer and inner indices')
    require(type(value.get('outer')) is int and value['outer'] >= 1
            and type(value.get('inner')) is int and value['inner'] >= 1,
            f'{label} outer and inner must be positive integer indices')


def _validate_experiment_objective_contract(definition, metric_records):
    """Bind objective and constraints to exact frozen metric definitions."""
    if 'objective' not in definition and 'constraints' not in definition:
        return
    references = definition.get('metric_evaluations')
    require(isinstance(references, list) and references,
            'an objective requires frozen metric_evaluations')
    records_by_ref = {}
    for record in metric_records:
        ref = (record.get('metric_id'), record.get('version'), record.get('definition_sha256'))
        records_by_ref[ref] = record
    frozen_refs = {
        (reference.get('metric_id'), reference.get('version'), reference.get('definition_sha256'))
        for reference in references if isinstance(reference, dict)
    }
    require(len(frozen_refs) == len(references), 'metric_evaluations references must be unique')

    def resolve(reference, unit, label):
        key = (reference.get('metric_id'), reference.get('version'), reference.get('definition_sha256'))
        require(key in frozen_refs, f'{label} must exactly match a frozen metric_evaluations reference')
        record = records_by_ref.get(key)
        require(isinstance(record, Mapping), f'{label} frozen metric definition is unavailable')
        metric_definition = record.get('definition')
        require(isinstance(metric_definition, Mapping)
                and metric_definition.get('complex_mode') in {'real', 'imag', 'abs'},
                f'{label} requires a frozen real, imaginary, or magnitude projection')
        require(unit == metric_definition.get('expected_unit'),
                f'{label}.unit must exactly equal the frozen metric unit; conversions are unsupported')

    objective = definition['objective']
    resolve(objective['metric_ref'], objective['unit'], 'objective.metric_ref')
    for index, constraint in enumerate(definition['constraints']):
        resolve(constraint['metric_ref'], constraint['unit'], f'constraints[{index}].metric_ref')


def _metric_definition_snapshots(ctx, definition, *, require_latest):
    """Resolve public metric refs to immutable, project-owned version records."""
    references = definition.get('metric_evaluations')
    if references is None:
        return []
    try:
        records = ctx['store'].read_metric_definition_versions(
            ctx['project_id'], references, require_latest=require_latest, require_active_head=True,
        )
    except ValueError as exc:
        raise PreWriteRefusal('METRIC_VERSION_UNAVAILABLE', str(exc), stage='validation') from exc
    except Exception as exc:
        raise PreWriteRefusal('INTEGRITY_COMPROMISED', 'pinned metric definition registry failed integrity validation',
                              stage='validation') from exc
    from ._metric_contract import definition_sha256, normalize_definition
    for reference, record in zip(references, records):
        try:
            normalized = normalize_definition(record.get('definition'))
        except Exception as exc:
            raise PreWriteRefusal('INTEGRITY_COMPROMISED', 'pinned metric definition has an invalid semantic schema',
                                  stage='validation') from exc
        if (normalized != record.get('definition')
                or definition_sha256(normalized) != record.get('definition_sha256')
                or record.get('metric_id') != reference.get('metric_id')
                or record.get('version') != reference.get('version')
                or record.get('definition_sha256') != reference.get('definition_sha256')):
            raise PreWriteRefusal('INTEGRITY_COMPROMISED', 'pinned metric definition differs from its immutable version reference',
                                  stage='validation')
        # The experiment case solve is the source of the evaluated solution.
        # Freeze the metric's dataset with the design and reject a design that
        # points at a different stored solution family.
        sample_solution = definition['sample'].get('spec', {}).get('solution', {})
        metric_solution = normalized['solution']
        if metric_solution.get('dataset') != sample_solution.get('dataset'):
            raise PreWriteRefusal('METRIC_CASE_DATASET_MISMATCH',
                                  'metric definition dataset must match the experiment case sample dataset',
                                  stage='validation')
        if (metric_solution.get('solution') is not None and sample_solution.get('solution') is not None
                and metric_solution['solution'] != sample_solution['solution']):
            raise PreWriteRefusal('METRIC_CASE_SOLUTION_MISMATCH',
                                  'metric definition solution selector conflicts with the case sample solution',
                                  stage='validation')
    return copy.deepcopy(records)


def op_solver_solution_transfer(worker, model_tag, arguments):
    """Configure one exact stored solution as a target Variables initial value.

    COMSOL 6.4 Variables Table 6-80 documents ``initsol`` as a solution
    object, ``initsoluse=manual`` with ``initsolusesolnum`` as the outer
    selector, and ``solnum=manual``/``manualsolnum`` as the inner selector.
    A time Quantity is accepted only when the exact SolutionInfo pair reports
    that time parameter and unit. No solve is issued here. Variable remapping,
    mesh compatibility, interpolation, conservation and history continuity are
    outside this partial profile and requests that need them are refused.
    """
    require(isinstance(arguments, dict) and set(arguments) == {'source', 'target', 'mapping'},
            'This partial profile accepts only source, target, and mapping; mesh/history verification and interpolation are unsupported')
    source = arguments.get('source')
    require(isinstance(source, dict) and set(source).issubset({'dataset', 'solution', 'inner', 'outer', 'time'})
            and {'dataset', 'outer'}.issubset(source),
            'source must identify dataset and outer; this profile refuses frequency, parameter-map, and other selectors')
    dataset, solution = source.get('dataset'), source.get('solution')
    inner, outer = source.get('inner'), source.get('outer')
    require(isinstance(dataset, str) and dataset,
            'An explicit source dataset tag is required')
    require(solution is None or (isinstance(solution, str) and solution),
            'source.solution must be a nonempty solver-sequence tag when provided')
    require(type(outer) is int and outer >= 1,
            'source.outer must be a positive integer index')
    require(inner is None or (type(inner) is int and inner >= 1),
            'source.inner must be a positive integer index when provided')
    requested_time = None
    if 'time' in source:
        time_items = source['time']
        require(isinstance(time_items, list) and len(time_items) == 1,
                'source.time must contain exactly one explicit Quantity')
        requested_time = time_items[0]
        require(isinstance(requested_time, dict) and set(requested_time) == {'value', 'unit'}
                and isinstance(requested_time.get('value'), (int, float))
                and not isinstance(requested_time.get('value'), bool)
                and math.isfinite(requested_time['value'])
                and isinstance(requested_time.get('unit'), str) and requested_time['unit'],
                'source.time requires one finite value with an explicit unit')
    require(inner is not None or requested_time is not None,
            'Select one exact source.inner index or one exact source.time Quantity')
    mapping = arguments.get('mapping')
    require(isinstance(mapping, dict), 'mapping must be an object')
    require(not mapping,
            'Nonempty variable mappings are refused: this adapter cannot yet verify source/target variables or apply a mapping')
    target = arguments.get('target')
    require(isinstance(target, dict) and set(target) == {'segments'} and isinstance(target['segments'], list)
            and len(target['segments']) == 2, 'target must be sol:<tag>/feature:<Variables-tag>')
    solver_segment, feature_segment = target['segments']
    require(isinstance(solver_segment, dict)
            and solver_segment == {'collection': 'sol', 'tag': solver_segment.get('tag')}
            and isinstance(solver_segment.get('tag'), str) and solver_segment['tag'],
            'target must begin with an explicit solver sequence')
    require(isinstance(feature_segment, dict)
            and feature_segment == {'collection': 'feature', 'tag': feature_segment.get('tag')}
            and isinstance(feature_segment.get('tag'), str) and feature_segment['tag'],
            'target must end with one explicit solver feature')
    indices = dataset_solution_indices(worker, model_tag, {'path': dataset})
    source_solution = indices.get('solution')
    require(indices.get('binding_complete') is True and indices.get('dataset') == dataset
            and isinstance(source_solution, str) and source_solution
            and (solution is None or indices.get('solution') == solution),
            'Dataset does not prove the requested source solution binding: ' + str(indices))
    candidates = [row for row in indices.get('solnum_pairs', [])
                  if isinstance(row, dict) and row.get('outer') == outer
                  and (inner is None or row.get('inner') == inner)]

    def pair_time(row):
        """Return a typed time value only when SolutionInfo bound it to this pair."""
        if indices.get('parameters_complete') is not True:
            return None
        by_pair = (indices.get('parameters') or {}).get('by_pair')
        if not isinstance(by_pair, dict):
            return None
        pair_data = by_pair.get(f"{row.get('outer')}:{row.get('inner')}")
        if not isinstance(pair_data, dict):
            return None
        names, values, units = pair_data.get('names'), pair_data.get('values'), pair_data.get('units')
        if not (isinstance(names, list) and isinstance(values, list) and isinstance(units, list)
                and len(names) == len(values) == len(units)):
            return None
        matches = []
        for name, value, unit in zip(names, values, units):
            if isinstance(name, str) and name.lower() in {'t', 'time'}:
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and isinstance(unit, str) and unit):
                    matches.append({'name': name, 'value': float(value), 'unit': unit})
                else:
                    return None
        return matches[0] if len(matches) == 1 else None

    if requested_time is not None:
        candidates = [row for row in candidates
                      if (bound_time := pair_time(row)) is not None
                      and bound_time['unit'] == requested_time['unit']
                      and math.isclose(float(requested_time['value']), bound_time['value'], rel_tol=1e-12, abs_tol=1e-15)]
    pairs = candidates
    require(len(pairs) == 1 and type(pairs[0].get('solnum')) is int and pairs[0]['solnum'] >= 1,
            'Dataset metadata does not uniquely resolve the requested exact outer/inner or time-bound solution')
    selected_pair = pairs[0]
    time_binding = pair_time(selected_pair)
    if requested_time is not None:
        require(time_binding is not None,
                'source.time cannot be proven from complete, unit-bearing SolutionInfo metadata for this exact pair')
    model = bound_model(worker, model_tag)
    sol_list = _call(model, 'sol')
    solver_tags = tag_list(sol_list)
    target_solver_tag = solver_segment['tag']
    require(source_solution in solver_tags and target_solver_tag in solver_tags,
            'Source and target solver sequences must exist in the bound model')
    require(target_solver_tag != source_solution, 'Target must be a distinct solver sequence from its source solution')
    source_node = _call(model, 'sol', source_solution)
    source_study = _call(source_node, 'study')
    require(isinstance(source_study, str) and source_study,
            'Source solver sequence study association is unavailable')
    target_solver = _call(model, 'sol', target_solver_tag)
    feature_list = _call(target_solver, 'feature')
    require(feature_segment['tag'] in tag_list(feature_list),
            'Target solver feature does not exist')
    variables = _call(feature_list, 'get', feature_segment['tag'])
    require(_call(variables, 'getType') == 'Variables',
            'Target solver feature must be COMSOL Variables')
    selected_solnum = selected_pair['solnum']
    requested = {
        'useinitsol': 'on',
        'initmethod': 'sol',
        'initsol': source_solution,
        'initsoluse': 'manual',
        'initsolusesolnum': outer,
        'solnum': 'manual',
        'manualsolnum': selected_solnum,
    }
    for prop in ('useinitsol', 'initmethod', 'initsol', 'initsoluse', 'solnum'):
        _call(variables, 'set', prop, requested[prop])
    for prop in ('initsolusesolnum', 'manualsolnum'):
        _call(variables, 'set', prop, requested[prop])
    readback = {prop: _call(variables, 'getString', prop)
                for prop in ('useinitsol', 'initmethod', 'initsol', 'initsoluse', 'solnum')}
    readback.update({prop: _call(variables, 'getInt', prop)
                     for prop in ('initsolusesolnum', 'manualsolnum')})
    require(readback == requested,
            'COMSOL Variables initial-solution selector did not read back exactly: ' + str(readback))
    return {
        'status': 'APPLIED',
        'coverage_status': 'PARTIAL',
        'contract': 'solver.solution_transfer/v1',
        'profile': 'exact-selected-solution-initial-value-selector-only',
        'source': {'dataset': dataset, 'solution': source_solution, 'study': source_study,
                   'outer': outer, 'inner': selected_pair['inner'], 'manualsolnum': selected_solnum,
                   'selection_mode': 'time_quantity' if requested_time is not None else 'solution_index',
                   'time_binding': time_binding,
                   'dataset_binding_source': indices.get('binding_source')},
        'target': target,
        'mapping': {},
        'variable_mapping_applied': False,
        'initialization_readback': readback,
        'solve_dispatched': False,
        'verification': {
            'status': 'CONFIGURED_ONLY',
            'mesh_compatibility': 'UNVERIFIED',
            'state_continuity': 'NOT_RUN',
            'history_preserved': False,
            'geometry_framework': 'UNSUPPORTED',
            'interpolation': 'UNSUPPORTED',
            'conservation_error': 'NOT_COMPUTED',
            'limitation': 'This partial profile only selects an exact source solution as the Variables initialization source. It does not map variables, verify source/target fields or mesh identity, interpolate across meshes, measure conservation error, or preserve/verify history. Requests needing those guarantees are unsupported.',
        },
        'api_basis': {
            'title': 'COMSOL 6.4 Variables, Table 6-80',
            'doc_id': 4652,
            'chunk_id': 17614,
            'sha256': '1b86563b282f32a7c7d506b43dbba1a310e9509a2bd605c40d9a1f8108094466',
        },
    }


def _attached_solver_sequence_evidence(model, target_solver_tag):
    """Read the target solver's exact study association and attachment state.

    ``SolverSequence.study()`` names its associated Study, while
    ``SolverSequence.isAttached()`` distinguishes a sequence that is actually
    attached.  Enumerating all solver sequences lets this adapter fail closed
    unless the target is the one and only attached sequence reported for that
    Study.  These readbacks happen before any Variables selector is changed.
    """
    try:
        study_tags = tag_list(_call(model, 'study'))
        solver_list = _call(model, 'sol')
        solver_tags = tag_list(solver_list)
        rows = []
        for solver_tag in solver_tags:
            solver = _call(solver_list, 'get', solver_tag)
            associated = _call(solver, 'study')
            attached = _call(solver, 'isAttached')
            if type(attached) is not bool:
                raise ValueError('isAttached() did not return a boolean')
            if isinstance(associated, str) and associated:
                if associated not in study_tags:
                    raise ValueError(f'solver {solver_tag!r} reports unknown Study {associated!r}')
            elif attached:
                raise ValueError(f'attached solver {solver_tag!r} has no Study association')
            rows.append({
                'solver': solver_tag,
                'study': associated if isinstance(associated, str) and associated else None,
                'is_attached': attached,
            })
    except Exception as exc:
        raise PreWriteRefusal(
            'SOLVER_ATTACHMENT_READBACK_UNAVAILABLE',
            'COMSOL could not prove the target solver sequence has a unique attached Study binding',
            safe_retry=True,
            details={'cause_type': type(exc).__name__, 'cause_code': getattr(exc, 'code', None)},
        ) from exc

    target_rows = [row for row in rows if row['solver'] == target_solver_tag]
    if len(target_rows) != 1:
        raise PreWriteRefusal('TARGET_SOLVER_NOT_FOUND', 'target solver sequence is not uniquely listed')
    target = target_rows[0]
    if target['is_attached'] is not True or target['study'] not in study_tags:
        raise PreWriteRefusal(
            'TARGET_SOLVER_NOT_ATTACHED',
            'target solver sequence is not attached to an existing Study',
        )
    attached_for_study = [row['solver'] for row in rows
                          if row['study'] == target['study'] and row['is_attached'] is True]
    if attached_for_study != [target_solver_tag]:
        raise PreWriteRefusal(
            'TARGET_STUDY_SOLVER_AMBIGUOUS',
            'target Study does not have exactly one attached solver sequence',
            details={'target_study': target['study'], 'attached_solver_tags': attached_for_study},
        )
    return {
        'status': 'VERIFIED',
        'study': target['study'],
        'solver': target_solver_tag,
        'is_attached': True,
        'unique_attached_solver_tags': attached_for_study,
        'readback_methods': ['SolverSequence.study()', 'SolverSequence.isAttached()'],
    }


def op_experiment_state_map(worker, model_tag, arguments):
    """Configure the documented initial-solution selector for a strict identity profile.

    This is a bounded WRITE adapter.  It verifies the exact stored source
    solution and the target Variables node, then configures and reads back the
    Variables initial-solution selectors.  The current API evidence does not
    prove per-variable dimensionality, source/target field equivalence, mesh
    topology, boundary-frame equality, or hidden solver-history continuity;
    those remain explicitly unverified and this operation never calls solve.
    """
    from ._stage_contract import normalize_state_map_request

    try:
        normalized = normalize_state_map_request(arguments)
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), safe_retry=True,
                              details=getattr(exc, 'details', None)) from exc
    model = bound_model(worker, model_tag)
    target_solver_tag = normalized['target']['segments'][0]['tag']
    attachment = _attached_solver_sequence_evidence(model, target_solver_tag)

    # The established adapter performs exact dataset/solution/tuple resolution,
    # checks the Variables feature subtype, and reads back every documented
    # initial-solution selector.  The high-level identity declaration is never
    # forwarded as proof or as a per-variable mapping instruction.
    configured = op_solver_solution_transfer(worker, model_tag, {
        'source': normalized['source'],
        'target': normalized['target'],
        'mapping': {},
    })
    declarations = normalized['mapping']['variables']
    configured.update({
        'contract': 'experiment.state_map/v1',
        'profile': normalized['mapping']['profile'],
        'coverage_status': 'PARTIAL',
        'target_solver_attachment_readback': attachment,
        'declared_variable_mappings': copy.deepcopy(declarations),
        'variable_mapping_applied': False,
        'mapping_evidence': {
            'status': 'UNVERIFIED',
            'reason': ('the documented Variables initial-solution selector does not expose a per-variable '
                       'mapping readback in the available adapter'),
            'source_field_identity': 'UNVERIFIED',
            'target_field_identity': 'UNVERIFIED',
            'source_target_units': 'UNVERIFIED',
            'source_target_mesh_identity': 'UNVERIFIED',
            'frame_equivalence': 'UNVERIFIED',
            'hidden_solver_history': 'NOT_VERIFIED',
        },
        'solve_dispatched': False,
        'api_basis': {
            'variables': {
                'title': 'COMSOL 6.4 Variables, Table 6-80',
                'doc_id': 4652,
                'chunk_id': 17614,
                'sha256': '1b86563b282f32a7c7d506b43dbba1a310e9509a2bd605c40d9a1f8108094466',
            },
            'solver_attachment': {
                'title': 'COMSOL 6.4 SolverSequence',
                'doc_id': 7714,
                'chunk_id': 23084,
                'sha256': '8fcefe8e2f2171858f49fe53ad6210d4231a6a28651766db67effc39c327a2c5',
            },
        },
    })
    return configured


def op_experiment_design(worker, model_tag, arguments):
    ctx = current_context()
    require(isinstance(ctx.get('project_id'), str) and ctx['project_id'],
            'Managed project identity is required for durable experiment design')
    require(isinstance(ctx.get('model_ref'), dict) and ctx['model_ref'],
            'Managed ModelRef is required for durable experiment design')
    definition = arguments.get('definition')
    cases = _strict_grid_design(worker, model_tag, definition)
    metric_snapshots = _metric_definition_snapshots(ctx, definition, require_latest=True)
    _validate_experiment_objective_contract(definition, metric_snapshots)
    design_id = 'exp_' + uuid.uuid4().hex
    record = {
        'schema_version': 1,
        'kind': 'w21experiment',
        'experiment_id': design_id,
        'project_id': ctx['project_id'],
        'model_ref': copy.deepcopy(ctx['model_ref']),
        # STATE_WRITE completes after this callback; bind the record to the
        # resulting revision so experiment.run cannot start against a stale model.
        'model_revision': ctx['revision'] + 1,
        'producer': ctx['producer'],
        'study': definition['study'],
        'sampling_profile': 'cartesian_grid',
        'definition': copy.deepcopy(definition),
        'definition_sha256': digest(definition),
        'metric_definition_snapshots': metric_snapshots,
        'cases': cases,
        'budget': copy.deepcopy(definition['budget']),
        'model_binding_scope': 'project_id+ModelRef+managed_revision',
        'external_change_detection_scope': 'managed_execution_ledger; not a full-model external CAS guarantee',
    }
    record['sha256'] = digest(record)
    key = 'w21experiment:' + design_id
    existing = ctx['store'].register_artifact_if_absent(key, record)
    require(existing is None, 'Experiment identifier collision; existing design was preserved')
    return copy.deepcopy(record)


def _resolved_experiment(ctx, experiment_id):
    require(isinstance(experiment_id, str) and experiment_id.startswith('exp_'),
            'experiment_id must name a registered W21 experiment')
    key = 'w21experiment:' + experiment_id
    record = ctx['store'].get_metadata('artifacts', key)
    require(isinstance(record, dict) and record.get('kind') == 'w21experiment'
            and record.get('experiment_id') == experiment_id,
            'Registered experiment design was not found')
    require(record.get('sha256') == digest({k: v for k, v in record.items() if k != 'sha256'}),
            'Experiment design integrity check failed')
    require(record.get('project_id') == ctx.get('project_id'),
            'Experiment belongs to a different project')
    require(record.get('model_ref') == ctx.get('model_ref'),
            'Experiment belongs to a different ModelRef')
    require(type(record.get('model_revision')) is int and ctx.get('revision') == record['model_revision'],
            'Model revision changed since experiment design; create a fresh design after reconciling')
    require(record.get('definition_sha256') == digest(record.get('definition')),
            'Experiment definition hash mismatch')
    metric_snapshots = _metric_definition_snapshots(ctx, record['definition'], require_latest=False)
    require(record.get('metric_definition_snapshots', []) == metric_snapshots,
            'Experiment metric definition snapshot differs from its exact immutable project version')
    return record


def _experiment_resources(record, arguments):
    frozen = record['budget']
    requested = arguments.get('resources')
    require(requested is None or isinstance(requested, dict), 'resources override must be an object')
    requested = {} if requested is None else requested
    require(set(requested).issubset({'max_cases', 'max_wall_time_s'}),
            'resources supports only max_cases and max_wall_time_s scheduler budgets')
    max_cases = frozen['max_cases']
    if 'max_cases' in requested:
        max_cases = requested['max_cases']
        require(type(max_cases) is int and 1 <= max_cases <= frozen['max_cases'],
                'resources.max_cases may only lower the predeclared case budget')
    max_wall = float(frozen['max_wall_time_s'])
    if 'max_wall_time_s' in requested:
        max_wall = requested['max_wall_time_s']
        require(isinstance(max_wall, (int, float)) and not isinstance(max_wall, bool)
                and math.isfinite(max_wall) and 0 < max_wall <= frozen['max_wall_time_s'],
                'resources.max_wall_time_s must be finite, positive, and cannot exceed the frozen wall budget')
        max_wall = float(max_wall)
    timeout = arguments.get('timeout_s')
    if timeout is not None:
        require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                and math.isfinite(timeout) and 0 < timeout <= frozen['max_wall_time_s'],
                'timeout_s must be positive and cannot exceed the frozen wall budget')
        max_wall = min(float(max_wall), float(timeout))
    return {'max_cases': max_cases, 'max_wall_time_s': float(max_wall)}


def op_experiment_run(worker, model_tag, arguments):
    from ._g3_w21 import ComputationBudget
    ctx = current_context()
    experiment = _resolved_experiment(ctx, arguments.get('experiment_id'))
    effective_budget = _experiment_resources(experiment, arguments)
    run_key = 'w21experimentrun:' + experiment['experiment_id']
    run_id = 'run_' + uuid.uuid4().hex
    run_record = {
        'schema_version': 1,
        'kind': 'w21experiment_run',
        'run_id': run_id,
        'experiment_id': experiment['experiment_id'],
        'project_id': ctx['project_id'],
        'model_ref': copy.deepcopy(ctx['model_ref']),
        'session_id': ctx['model_ref'].get('session_id'),
        'model_revision': ctx['revision'],
        'revision_scope': 'experiment_run_admission_revision; each case binds separately to its native solution identity',
        'design_sha256': experiment['sha256'],
        'producer': ctx['producer'],
        'status': 'RUNNING',
        'effective_budget': effective_budget,
        'timeout_semantics': 'scheduler_admission_budget; timeout does not cancel an in-flight COMSOL call',
        'cases': [],
    }
    run_record['sha256'] = digest(run_record)
    existing = ctx['store'].register_artifact_if_absent(run_key, run_record)
    require(existing is None, 'Experiment already has a run claim; inspect or reconcile its persisted run state before retrying')
    budget = ComputationBudget(max_cases=effective_budget['max_cases'],
                               max_wall_time_s=effective_budget['max_wall_time_s'])
    cases = []
    terminal = None
    for row in experiment['cases']:
        if not budget.can_evaluate():
            terminal = 'BUDGET_EXHAUSTED'
            break
        metric_definitions = experiment.get('metric_definition_snapshots', [])
        experiment_case_id = experiment['experiment_id'] + ':' + row['case_id']
        attempt_id = 'att_' + uuid.uuid4().hex
        attempt = {
            'schema_version': 1,
            'kind': 'w21experiment_case_attempt',
            'attempt_id': attempt_id,
            'experiment_id': experiment['experiment_id'],
            'run_id': run_id,
            'case_id': row['case_id'],
            'case_ordinal': int(row['case_id'].split('-')[-1]),
            'study': experiment['study'],
            'project_id': ctx['project_id'],
            'session_id': ctx['model_ref'].get('session_id'),
            'model_ref': copy.deepcopy(ctx['model_ref']),
            'model_revision': ctx['revision'],
            'producer': ctx['producer'],
            'design_sha256': experiment['sha256'],
            'parameters': copy.deepcopy(row['parameters']),
            'units': copy.deepcopy(experiment['definition']['units']),
            'metric_definition_refs': [
                {key: item[key] for key in ('metric_id', 'version', 'definition_sha256')}
                for item in metric_definitions
            ],
            'admission_state': 'ATTEMPT_ADMITTED_BEFORE_CASE_DISPATCH',
        }
        attempt['sha256'] = digest(attempt)
        attempt_key = 'w21experimentattempt:' + experiment['experiment_id'] + ':' + row['case_id']
        existing_attempt = ctx['store'].register_artifact_if_absent(attempt_key, attempt)
        require(existing_attempt is None, 'Case attempt already exists; inspect its durable state without replaying the case')
        binding = {
            'attempt_id': attempt_id,
            'attempt_sha256': attempt['sha256'],
            'experiment_id': experiment['experiment_id'],
            'run_id': run_id,
            'case_id': row['case_id'],
            'execution_case_id': experiment_case_id,
            'case_ordinal': attempt['case_ordinal'],
            'project_id': ctx['project_id'],
            'session_id': ctx['model_ref'].get('session_id'),
            'model_ref': copy.deepcopy(ctx['model_ref']),
            'model_revision': ctx['revision'],
            'producer': ctx['producer'],
            'design_sha256': experiment['sha256'],
            'planned_parameters': copy.deepcopy(row['parameters']),
            'study': experiment['study'],
        }
        result = execute_case(worker, model_tag, experiment['study'], experiment['definition'],
                              row['parameters'], budget, experiment_case_id,
                              metric_definitions=metric_definitions, experiment_binding=binding)
        if (not isinstance(result, dict)
                or result.get('case_attempt_id') != attempt_id):
            raise PreWriteRefusal('INVALID_METRIC_CASE_BINDING',
                                  'case callback did not preserve its pre-dispatch attempt identity')
        result['case_id'] = row['case_id']
        result['case_ordinal'] = int(row['case_id'].split('-')[-1])
        cases.append(result)
        case_key = 'w21experimentcase:' + experiment['experiment_id'] + ':' + row['case_id']
        persist('w21experiment_case', case_key, {
            'experiment_id': experiment['experiment_id'],
            'run_id': run_id,
            'project_id': ctx['project_id'],
            'session_id': ctx['model_ref'].get('session_id'),
            'model_revision': ctx['revision'],
            'case': result,
        })
        if result.get('status') == 'UNKNOWN':
            terminal = 'EXECUTION_STATE_UNKNOWN'
            break
        if result.get('status') == 'FAILED':
            terminal = 'CASE_FAILED'
            break
    if terminal is None:
        terminal = 'ALL_CASES_COMPLETED'
    if terminal == 'EXECUTION_STATE_UNKNOWN':
        status = 'EXECUTION_STATE_UNKNOWN'
    elif terminal == 'CASE_FAILED':
        status = 'FAILED'
    elif terminal == 'BUDGET_EXHAUSTED':
        status = 'PARTIAL'
    else:
        status = 'COMPLETE'
    run_record.update(status=status, completion_status=terminal, cases=cases, budget=budget.status(),
                      execution_state_unknown=(terminal == 'EXECUTION_STATE_UNKNOWN'))
    run_record['sha256'] = digest({k: v for k, v in run_record.items() if k != 'sha256'})
    ctx['store'].persist_artifact(run_key, run_record)
    return copy.deepcopy(run_record)


def op_stage_checkpoint_create(worker, model_tag, arguments):
    require(arguments.get('sample') and arguments.get('stage_id'), 'Source W17 sample specification and stage_id required')
    require(arguments.get('variables') is None, 'Client field values cannot define a native checkpoint')
    sample = result_at_points(worker, model_tag, arguments['sample'])
    require(successful(sample), 'Stage source sampling failed')
    fa = sample['field_array']
    times, indices = stored_times(worker, model_tag, sample)
    require(times and arguments.get('timestamp_s') == times[-1], 'Checkpoint must select actual stored terminal time')
    require(len(fa['coords']['outer']) == 1, 'Stage source must select one outer solution')
    require(arguments.get('units') == fa['units']['expression'], 'Stage units must match engine units')
    source_study = _call(_call(bound_model(worker, model_tag), 'sol', sample['solution']), 'study')
    require(source_study, 'Source solution study association unavailable')
    ref = register_observation(worker, model_tag, sample)
    checkpoint = dict(checkpoint_id='stage_' + uuid.uuid4().hex, stage_id=arguments['stage_id'],
                      observation_ref=ref, source_solution=sample['solution'], source_dataset=sample['dataset'],
                      source_study=source_study, timestamp_s=times[-1], units=fa['units']['expression'],
                      selection={'points': sample['points'], 'coordinate_unit': sample['coordinate_unit']},
                      sample_request=arguments['sample'], solution_indices=indices, field_array=fa, status='COMPLETE', checkpoint_status='CHECKPOINT_CREATED')
    return persist('w21stage', checkpoint['checkpoint_id'], checkpoint)


def op_stage_state_transfer(worker, model_tag, arguments):
    ctx = current_context()
    checkpoint = ctx['store'].get_metadata('artifacts', arguments.get('checkpoint_id', ''))
    require(checkpoint and checkpoint.get('kind') == 'w21stage', 'Registered stage checkpoint required')
    recorded_hash = checkpoint['sha256']
    require(digest({k:v for k,v in checkpoint.items() if k != 'sha256'}) == recorded_hash, 'Checkpoint metadata hash mismatch')
    record, source = resolve_observation(worker, model_tag, checkpoint['observation_ref'])
    require(not arguments.get('reset_history'), 'History resets are prohibited')
    mapping = arguments.get('variable_mapping')
    expressions = source['expressions']
    require(mapping == {name:name for name in expressions}, 'Only identical dependent-variable mapping on the same mesh is supported')
    target_study = arguments.get('target_stage_id')
    target_step = arguments.get('target_step', 'time')
    require(target_study and target_study != checkpoint['source_study'], 'Distinct target study required')
    target_request = arguments.get('target_sample')
    require(target_request and target_request.get('points') == source['points'], 'Target verification must use source spatial points')
    require(target_request.get('spec',{}).get('expressions') == expressions, 'Target verification expressions must match source')
    tolerance = arguments.get('initial_tolerance')
    require(isinstance(tolerance, (float,int)) and math.isfinite(tolerance) and tolerance > 0, 'Predeclared initial-state tolerance required')
    model = bound_model(worker, model_tag)
    require(target_study in tag_list(_call(model, 'study')), 'Target study missing')
    target = _call(_call(model, 'study', target_study), 'feature', target_step)
    require(_call(target, 'getType') == 'Transient', 'Target must be a transient study step')
    settings = {'useinitsol':'on', 'initmethod':'sol', 'initstudy':checkpoint['source_study'], 'solnum':'last'}
    for key,value in settings.items():
        _call(target, 'set', key, value)
    readback = {key:_call(target, 'getString', key) for key in settings}
    require(readback == settings, 'Target initialization settings did not read back')
    solve = study_run(worker, model_tag, {'study':{'segments':[{'collection':'study','tag':target_study}]}})
    require(successful(solve), 'Stage continuation solve failed')
    target_sample = result_at_points(worker, model_tag, target_request)
    require(successful(target_sample), 'Target stage sample failed')
    source_fa, target_fa = source['field_array'], target_sample['field_array']
    require(target_fa['units']['expression'] == checkpoint['units'], 'Target field unit mismatch')
    target_times, target_indices = stored_times(worker, model_tag, target_sample)
    require(target_times[0] == checkpoint['timestamp_s'], 'Target initial time must equal source terminal time')
    errors = [abs(a-b) for i in range(len(expressions))
              for a,b in zip(source['values'][i][0][-1],target_sample['values'][i][0][0])]
    require(errors and len(errors) == len(expressions)*len(source['points']), 'Stage spatial field shape mismatch')
    verified = max(errors) <= tolerance
    result = dict(status='COMPLETE' if verified else 'FAILED', source_checkpoint_id=checkpoint['checkpoint_id'],
                  source_hash_verified=True, initialization_readback=readback, solve=solve,
                  target_sample=target_sample, solution_indices=target_indices, initial_max_abs_error=max(errors), initial_tolerance=tolerance,
                  history_preserved=verified, t047_generic_subitem_status='PASS' if verified else 'FAIL',
                  domain_physical_status='DEFERRED_TO_W24',
                  observation_ref=register_observation(worker, model_tag, target_sample))
    persist('w21transfer', 'transfer_'+uuid.uuid4().hex, {'result':result})
    return result
