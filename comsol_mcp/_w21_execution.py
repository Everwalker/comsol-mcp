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


def execute_case(worker, tag, study, definition, values, budget, case_id):
    from ._g3_w21 import ResultCache
    from ._g3_w20_validation import validate_solution
    ctx = current_context()
    units = definition['units']
    validate_parameters(worker, tag, values, units)
    identity = model_identity(worker, tag, values, definition, study)
    cache_key = ResultCache.compute_key(digest(identity), values, dict(definition=definition, study=study), identity['engine']['comsol_version'])
    cache = ResultCache(ctx['store'])
    record = cache.get(cache_key)
    if record and record.get('status') == 'COMPLETED':
        result = copy.deepcopy(record['result'])
        require(digest(result) == record['result_digest'], 'Cached result integrity mismatch')
        _, original = resolve_observation(worker, tag, result['observation_ref'], allow_historical=True)
        require(digest(original) == digest(result['sample']), 'Cached W17 sample differs from original artifact')
        result.update(status='CACHED', cache_hit=True, requested_case_id=case_id)
        return result
    if not budget.can_evaluate():
        return {'status': 'NOT_RUN', 'reason': 'BUDGET_EXHAUSTED', 'case_id': case_id, 'cache_hit': False}
    # The outer managed queue serializes this check with dispatch; no inner enqueue.
    budget.cases_evaluated += 1
    result = {'case_id': case_id, 'parameters': values, 'units': units, 'producer': ctx['producer'],
              'model_ref': ctx['model_ref'], 'cache_hit': False, 'engine': identity['engine'], 'cache_policy':identity['cache_policy']}
    try:
        result['parameter_readback'] = apply_parameters(worker, tag, values, units)
        result['solve'] = study_run(worker, tag, {'study': {'segments': [{'collection': 'study', 'tag': study}]}})
        require(successful(result['solve']), 'Solve failed or remains unverified')
        sample = result_at_points(worker, tag, definition['sample'])
        require(successful(sample), 'W17 sampling failed')
        result['sample'] = sample
        times, result['solution_indices'] = stored_times(worker, tag, sample)
        require(times == definition['times'], 'Requested times do not match stored solution times: ' + str(times))
        result['observation_ref'] = register_observation(worker, tag, sample)
        result['validation'] = validate_solution(worker, tag, {'solution': {'dataset': sample['dataset']},
            'observation_ref': result['observation_ref'], 'criteria': definition['validation']})
        require(result['validation']['numerical_verification_status'] == 'PASS', 'W20 validation failed')
        result.update(extract_metrics(sample, definition['metrics']), status='COMPLETED')
        budget.cases_failed = 0
        post_identity = model_identity(worker, tag, values, definition, study)
        post_key = ResultCache.compute_key(digest(post_identity), values, dict(definition=definition, study=study), post_identity['engine']['comsol_version'])
        cache.store(post_key, {'status': 'COMPLETED', 'identity': post_identity,
                'result': result, 'result_digest': digest(result)})
    except Exception as exc:
        budget.cases_failed += 1
        budget.total_failures += 1
        result.update(status='FAILED', error=str(exc))
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
        if case['status'] in {'FAILED', 'NOT_RUN'}:
            break
    failed = any(c['status'] == 'FAILED' for c in cases)
    budget_stopped = any(c['status'] == 'NOT_RUN' for c in cases)
    # A bounded scheduler completed its request when it stops before dispatch.
    # Calling this PARTIAL would poison the shared revision ledger despite the
    # known completed case and explicitly unstarted remainder.
    return {'status': 'FAILED' if failed else 'COMPLETE',
            'completion_status': 'CASE_FAILED' if failed else 'BUDGET_EXHAUSTED' if budget_stopped else 'ALL_CASES_COMPLETED',
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
    def evaluate(params):
        return execute_case(worker, model_tag, study, definition, params, compute_budget, 'opt-' + str(len(optimizer.history)+1))
    result = optimizer.run_bounded_search(arguments.get('grid_points_per_dim',3), evaluate)
    result['search_status'] = result['status']
    result['status'] = 'COMPLETE'
    result['compute_budget'] = compute_budget.status()
    result['optimality'] = 'BEST_VERIFIED_FEASIBLE_SO_FAR' if result['best_candidate'] else 'NO_FEASIBLE_FOUND'
    return result


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
