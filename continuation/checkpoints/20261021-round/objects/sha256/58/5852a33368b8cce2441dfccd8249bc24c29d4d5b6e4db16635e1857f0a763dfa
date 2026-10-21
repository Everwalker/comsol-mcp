"""R11 receipt lineage controls using the unmodified captured R10 public response."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.t044_thermal_benchmark import verify_count_only_public_response


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
ACTUAL = CANDIDATE_ROOT / '_state/t044-thermal-r13-freeze-consumer-0b8171f9-20261019/evidence/actual_r10'
RAW_CALLS = {
    'artifact.register': '8-artifact.register.json',
    'code.execute_java': '9-code.execute_java.json',
    'parameter.get': '10-parameter.get.json',
    'geometry.inspect': '11-geometry.inspect.json',
    'study.inspect': '12-study.inspect.json',
    'study.run': '13-study.run.json',
    'dataset.list': '14-dataset.list.json',
    'dataset.inspect': '15-dataset.inspect.json',
    'result.at_points': '16-result.at_points.json',
}


def _actual():
    summary = json.loads((ACTUAL / 'SUMMARY.json').read_text(encoding='utf-8'))
    responses = {
        operation: json.loads((ACTUAL / filename).read_text(encoding='utf-8'))['structuredContent']
        for operation, filename in RAW_CALLS.items()
    }
    return summary, responses


def _context(summary):
    created = summary['calls'][6]['execution']
    result = json.loads((ACTUAL / RAW_CALLS['result.at_points']).read_text(encoding='utf-8'))['structuredContent']
    return {
        'dataset': 'dset1', 'solution': 'sol1',
        'project_id': result['execution']['project_id'],
        'session_id': result['execution']['session_id'],
        'model_ref': created['model_ref'],
        'revision': result['execution']['revision'],
    }


def _verify(response, summary):
    original_summary, _ = _actual()
    return verify_count_only_public_response(
        response, **_context(original_summary), summary=summary)


def test_captured_r10_count_only_response_passes_exact_public_receipt_lineage():
    source_summary, responses = _actual()
    summary = {'calls': deepcopy(source_summary['calls'][:16])}
    response = deepcopy(responses['result.at_points'])
    assert response['data']['field_array'] is None
    assert 'observation_ref' not in response['data']

    accepted = _verify(response, summary)

    assert accepted['temperature_normalized_max_error'] <= 0.001
    assert accepted['flux_relative_max_error'] <= 0.001
    assert accepted['receipt_scope'] == 'exact_public_result_bound_to_builder_study_and_current_model_revision'
    assert accepted['result_operation_id'] == response['execution']['operation_id']
    assert accepted['result_request_id'] == response['execution']['request_id']


class _FineMeshStop(Exception):
    pass


def test_run_cycle_consumes_captured_coarse_response_then_stops_at_fine_create(tmp_path):
    source_summary, responses = _actual()
    summary = {'calls': deepcopy(source_summary['calls'][:7])}
    operations = []
    model_ref = source_summary['calls'][6]['execution']['model_ref']
    attached = source_summary['actual_attach']
    runtime_id = responses['code.execute_java']['execution']['runtime_observation']['installation_runtime_id']
    fixture = (CANDIDATE_ROOT / '_state/t044-thermal-r13-freeze-consumer-0b8171f9-20261019/evidence/actual_r11/T044ThermalFixture.java').read_bytes()
    save_bytes = b'R11 synthetic save receipt fixture; no MPH or numerical sample.'

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    async def call(operation, body, *, ref=None, revision=None, cleanup=False):
        operations.append(operation)
        if operation == 'model.create':
            raise _FineMeshStop('stop exactly at the following fine-mesh model.create')
        if operation == 'model.save':
            destination = tmp_path / 't044-coarse.mph'
            destination.write_bytes(save_bytes)
            request_id = 't044-r11-synthetic-save-fixture'
            execution = {
                'project_id': source_summary['calls'][2]['execution'].get('project_id') or source_summary['calls'][6]['execution'].get('project_id'),
                'session_id': source_summary['calls'][6]['execution']['session_id'],
                'model_ref': model_ref, 'revision': 3,
                'operation_id': 'e9f79ec7-150c-4b28-9cf5-6f07103bcc54',
                'job_id': 'f4e8132d-cc61-4ee1-9f9e-9bbf42da7f5b',
                'request_id': request_id, 'idempotency_key': request_id,
                'request_hash': hashlib.sha256(b'synthetic non-scientific test response').hexdigest(),
            }
            summary['calls'].append({'operation': operation, 'request_id': request_id,
                                     'state': 'OBSERVED', 'success': True, 'isError': False,
                                     'execution': execution, 'error': None})
            return {'success': True, 'error': None,
                    'execution': execution,
                    'data': {'destination': destination.name, 'sha256': digest(destination),
                             'size_bytes': destination.stat().st_size}}
        index = len(summary['calls'])
        expected = source_summary['calls'][index]
        assert expected['operation'] == operation
        summary['calls'].append(deepcopy(expected))
        return deepcopy(responses[operation])

    from tools import t044_thermal_benchmark as thermal
    with pytest.raises(_FineMeshStop, match='following fine-mesh model.create'):
        asyncio.run(thermal.run_cycle(
            call, workspace=tmp_path, project_id=source_summary['calls'][6]['execution']['project_id'],
            session_id=source_summary['calls'][6]['execution']['session_id'], model_ref=model_ref,
            revision=source_summary['calls'][6]['execution']['revision'], fixture=fixture,
            summary=summary, verify_artifact=lambda *_: None, digest=digest,
            runtime_id=runtime_id, attached=attached))

    assert operations[-2:] == ['model.save', 'model.create']
    assert operations.count('result.at_points') == 1
    assert summary['calls'][-1]['operation'] == 'model.save'
    assert summary['calls'][-2]['operation'] == 'result.at_points'
    assert summary['calls'][-2]['execution']['operation_id'] == responses['result.at_points']['execution']['operation_id']
    assert 't044_thermal' not in summary


@pytest.mark.parametrize('mutation', [
    'project', 'session', 'model_ref', 'revision', 'operation_id', 'job_id', 'request_hash',
    'request_id', 'idempotency_key', 'summary_execution', 'duplicate_prior_request',
    'missing_builder', 'missing_study', 'missing_field_array',
    'synthesized_field_array', 'synthesized_observation_ref',
])
def test_count_only_public_receipt_rejects_identity_reuse_and_forged_lineage(mutation):
    source_summary, responses = _actual()
    summary = {'calls': deepcopy(source_summary['calls'][:16])}
    response = deepcopy(responses['result.at_points'])
    current = response['execution']
    history = summary['calls']
    if mutation == 'project': current['project_id'] = 'another-project'
    elif mutation == 'session': current['session_id'] = 'another-session'
    elif mutation == 'model_ref': current['model_ref']['generation'] += 1
    elif mutation == 'revision': current['revision'] += 1
    elif mutation == 'operation_id': current['operation_id'] = history[8]['execution']['operation_id']
    elif mutation == 'job_id': current['job_id'] = history[8]['execution']['job_id']
    elif mutation == 'request_hash': current['request_hash'] = 'bad-hash'
    elif mutation == 'request_id': current['request_id'] = 'other-request'
    elif mutation == 'idempotency_key': current['idempotency_key'] = 'other-key'
    elif mutation == 'summary_execution': history[-1]['execution']['request_hash'] = '0' * 64
    elif mutation == 'duplicate_prior_request': history[0]['request_id'] = current['request_id']
    elif mutation == 'missing_builder': history[8]['execution']['model_ref']['generation'] += 1
    elif mutation == 'missing_study': history[12]['execution']['model_ref']['generation'] += 1
    elif mutation == 'missing_field_array': response['data'].pop('field_array')
    elif mutation == 'synthesized_field_array': response['data']['field_array'] = {'values': []}
    elif mutation == 'synthesized_observation_ref': response['data']['observation_ref'] = {'observation_id': 'invented'}

    with pytest.raises(RuntimeError):
        _verify(response, summary)
