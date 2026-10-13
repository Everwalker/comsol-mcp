"""Real SolutionBinding/G3/Controller algorithms with recorded018 SDK transport.

These are source controls. Stored getter replies are replayed at the SDK/RPC
boundary with the current synthetic request id; no original native key runs.
"""
import copy
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._solution_binding import SolutionBinding
from tests.test_ephemeral_result_ticket import _public, _call
from tests.test_g3_results import FakeEngineError
from comsol_mcp import _g3_results as results
from tools import run_native_w23_saved_mph_diagnostic as runner

FIXTURE = Path(__file__).parent / 'fixtures/w23_activity018/ACTUAL_DSET1_TYPED_TRANSCRIPT_AND_INVENTORY.json'


def _save(label, data):
    base = os.environ.get('W23_ACTIVITY019_TRANSCRIPT_CAPTURE')
    if base:
        out = Path(base)
        out.mkdir(parents=True, exist_ok=True)
        (out / (label + '.json')).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def _recorded_public(tmp_path, monkeypatch, mutation=None):
    source = json.loads(FIXTURE.read_text())
    daemon, service, ref, project, sdk, tree, nodes, args = _public(tmp_path, monkeypatch)
    info = tree.solver.solution_info
    observation = source['original_frequency_projection']['raw_worker_sequence_parameter_observation']
    for key in ('getLevelNames', 'getLevels', 'getOuterSolnum'):
        info.props[key] = copy.deepcopy(observation['solution_info_count_scope'][key])
    info.units = {'freq': observation['parameter_rows'][0]['unit']}
    pairs = source['original_transcript']['selected_actual_getter_request_reply_pairs']

    def recorded(method, arguments):
        matches = [row for row in pairs if row['method'] == method and row['args'] == list(arguments)]
        assert matches, (method, arguments)
        replies = [row['original_observed_event']['metadata']['reply'] for row in matches]
        assert all((r['ok'], r.get('result')) == (replies[0]['ok'], replies[0].get('result')) for r in replies)
        return copy.deepcopy(replies[0])

    def direct(method, arguments):
        reply = recorded(method, arguments)
        if reply['ok'] is not True:
            error = FakeEngineError(reply['failure']['message'], code=reply['failure']['code'])
            error.reply = reply
            error.failure = copy.deepcopy(reply['failure'])
            raise error
        return reply['result']

    def getsolnum(outer, strict):
        info._guard('getSolnum', (outer, strict))
        return direct('getSolnum', (outer, strict))

    def getsolnums(names):
        info._guard('getSolnums', (names,))
        return direct('getSolnums', (names,))

    info.getSolnum = getsolnum
    info.getSolnums = getsolnums
    originals = {'getPVals', 'getPValsImag', 'getSize', 'getSizeMulti', 'getPNames',
                 'getOuterSolnum', 'getLevels', 'getLevelNames', 'getSolnum', 'getSolnums'}

    def replay(body):
        if body['type'] != 'call' or body.get('method') not in originals:
            return None
        target = sdk.objects.get(body['handle'])
        if target is not info and target is not tree.solver:
            return None
        reply = recorded(body['method'], tuple(body.get('args', [])))
        # Reply contents/FAILED flag remain original; only request correlation
        # is rebased for a source replay, never represented as fresh native work.
        reply['request_id'] = body['request_id']
        if mutation == 'missing-count' and body['method'] == 'getSize':
            reply['result'] = None
        elif mutation == 'multistate' and body['method'] == 'getSizeMulti':
            reply['result'] = [2, 1]
        return reply

    sdk.inject = replay
    if mutation == 'units':
        info.units['freq'] = 'm'  # explicit controlled conflict with unchanged Java GHz receipt
    return source, daemon, service, ref, project, sdk, tree, nodes, args, info


def _indices(daemon, ref, project, tmp_path):
    response = daemon.dispatch({'operation': 'dataset.solution_indices', 'arguments': {'path': 'dset1'},
        'execution': {'project_id': project, 'session_id': ref['session_id'], 'model_ref': ref,
            'expected_revision': 0, 'request_id': 'source018-indices', 'idempotency_key': 'source018-indices',
            'rpc_timeout_s': 10, 'queue_timeout_s': 10, 'execution_timeout_s': None}})
    if response.get('execution', {}).get('job_id'):
        response = runner.science._wait_job(daemon, response, deadline_epoch=time.time()+20,
            cleanup_reserve_s=1, label='source018-indices', evidence=tmp_path)
    assert response['success'], response
    return response, runner._response_body(response)


def _frequency_inventory(source, body):
    value = copy.deepcopy(source['original_normalized_inventory'])
    # Current canonical projection comes only from real G3, with actual018 Java
    # receipts unchanged. No completeness or axis boolean is patched in place.
    value['dataset_inventory']['solution_index_receipts'][0]['solution_indices'] = body
    return value


def test_actual018_replayed_getters_real_solutionbinding_and_public_g3_restore_no_outer_count(tmp_path, monkeypatch):
    source, daemon, service, ref, project, sdk, tree, nodes, args, info = _recorded_public(tmp_path, monkeypatch)
    try:
        mapping = SolutionBinding.resolve_solution_info(info)
        assert mapping['outer_indices'] == mapping['inner_indices'] == mapping['solnum_pairs'] == []
        assert mapping['axis_metadata_complete'] is mapping['pair_mapping_complete'] is False
        assert not any(method == 'getSolnum' for method, _ in info.calls)
        # The original convenience query really returned inner1. Its mere
        # availability must not be promoted into a published parameter axis.
        assert info.getSolnum(1, True) == [1]
        response, body = _indices(daemon, ref, project, tmp_path)
        for key in ('axis_metadata_complete', 'pair_mapping_complete', 'binding_complete', 'parameters_complete'):
            assert body[key] is False
        assert body['outer_indices'] == body['inner_indices'] == body['solnum_pairs'] == []
        assert body['dataset_binding']['binding_complete'] is True
        assert body['solver_sequence_parameter_observation'] == source['original_frequency_projection']['raw_worker_sequence_parameter_observation']
        inventory = _frequency_inventory(source, body)
        count = runner.protocol.resolve_main_frequency_dataset(inventory['study_inventory'], inventory['dataset_inventory'])
        assert count['status'] == 'PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON'
        assert service.ledger._models['Model'].revision == 0 and not nodes
        _save('positive-actual018-current-g3', {'source_only': True, 'native': 'NOT_RUN',
            'real_solutionbinding': mapping, 'actual_public_request_reply': response,
            'current_g3_projection': body, 'real_unchanged_science_protocol_count': count,
            'original_recorded_sdk_boundary': source['original_transcript']})
    finally:
        daemon.close()


def test_old_r10_production_solutionbinding_reproduces_axis_promotion_from_same018_getters(tmp_path, monkeypatch):
    source, daemon, service, ref, project, sdk, tree, nodes, args, info = _recorded_public(tmp_path, monkeypatch)
    try:
        parent = runner.REPO.parent / 'w23-saved-mph-science-readback-20261013-candidate20-r10'
        freeze = json.loads((parent / 'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1/freeze.json').read_text())
        path = parent / 'comsol_mcp/_solution_binding.py'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == freeze['source_files']['comsol_mcp/_solution_binding.py']['sha256']
        spec = importlib.util.spec_from_file_location('comsol_mcp._frozen_actual018_solution_binding', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = module.SolutionBinding.resolve_solution_info(info)
        assert old['outer_indices'] == old['inner_indices'] == [1]
        assert old['pair_mapping_complete'] is old['axis_metadata_complete'] is True
        current = SolutionBinding.resolve_solution_info(info)
        assert current['outer_indices'] == [] and current['pair_mapping_complete'] is False
        old_for_capture=copy.deepcopy(old)
        for key in ('solnum_by_pair','parameter_names_by_pair','parameter_values_by_pair','parameter_units_by_pair'):
            old_for_capture[key]=[{'pair':list(pair),'value':value} for pair,value in old_for_capture[key].items()]
        _save('old-r10-vs-restored-r11-same018-getters', {'source_only': True, 'native': 'NOT_RUN', 'old': old_for_capture, 'current': current})
    finally:
        daemon.close()


@pytest.mark.parametrize('selector', ['outer', 'inner'])
def test_no_outer_actual018_transcript_explicit_axis_selection_refused_before_interp(tmp_path, monkeypatch, selector):
    source, daemon, service, ref, project, sdk, tree, nodes, args, info = _recorded_public(tmp_path, monkeypatch)
    try:
        args['spec']['solution'][selector] = 1
        with pytest.raises(ExecutionContractError, match='complete SolutionInfo mapping'):
            results.result_at_points(tree.worker, 'Model', args)
        assert not nodes and service.ledger._models['Model'].revision == 0
    finally:
        daemon.close()


@pytest.mark.parametrize('mutation', ['missing-count', 'multistate', 'units'])
def test_actual018_g3_count_or_unit_negative_cannot_enter_strict_frequency_witness(tmp_path, monkeypatch, mutation):
    source, daemon, service, ref, project, sdk, tree, nodes, args, info = _recorded_public(tmp_path, monkeypatch, mutation)
    try:
        response, body = _indices(daemon, ref, project, tmp_path)
        assert body['axis_metadata_complete'] is body['parameters_complete'] is False
        inventory = _frequency_inventory(source, body)
        with pytest.raises(runner.protocol.ScienceProtocolError) as refusal:
            runner.protocol.resolve_main_frequency_dataset(inventory['study_inventory'], inventory['dataset_inventory'])
        assert not nodes and service.ledger._models['Model'].revision == 0
        _save('negative-'+mutation, {'source_only': True, 'native': 'NOT_RUN', 'actual_g3': body,
            'actual_protocol_refusal': str(refusal.value), 'controlled_boundary_mutation': mutation})
    finally:
        daemon.close()


@pytest.mark.parametrize('mutation', ['units', 'revision'])
def test_actual018_public_units_revision_negative_stops_before_worker_submit(tmp_path, monkeypatch, mutation):
    source, daemon, service, ref, project, sdk, tree, nodes, args, info = _recorded_public(tmp_path, monkeypatch)
    try:
        runner._set_dispatch_allowlist(daemon, tmp_path/'closed-slot-allowlist.jsonl')
        before = len(sdk.calls)
        if mutation == 'units':
            args['spec']['units'] = ['A/m']
            with pytest.raises(RuntimeError, match='allowlist'):
                _call(daemon, ref, project, args, 'bad-units', 0)
        else:
            response = _call(daemon, ref, project, args, 'bad-revision', 1)
            assert response['success'] is False
        assert len(sdk.calls) == before and not nodes and service.ledger._models['Model'].revision == 0
    finally:
        daemon.close()
