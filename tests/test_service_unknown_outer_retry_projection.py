"""T13: final UNKNOWN error projection and verified_outcome pinning.

Permanent portable regression for the bound-write returned-UNKNOWN branch of
ExecutionService.execute_legacy: after the branch decides UNKNOWN, the
published error is a copied mapping pinned to safe_retry=False that keeps a
declared direct or immediate-data error/refusal/engine_error record's code,
message and diagnostic fields, synthesizes the ordinary UNKNOWN error only
when no such record applies, and publishes verified_outcome='unknown' while
leaving the separate verification axis exactly as classified.  A definite
failure keeps its declared safe_retry statement and its original post
snapshot.  Real service and ledger with in-memory fakes only; no daemon,
Worker, COMSOL, filesystem target, network, cursor or fallback.  Identity
binding is external (task-owned guard plugin), never required for ordinary
collection of this file.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def model_snapshot(self, tag):
        self.calls.append('snapshot')
        return {'model_tag': tag, 'server_instance_id': 'fake-server',
                'fingerprint': 'fake-fingerprint', 'external_event_counter': 0}


class Harness:
    def __init__(self):
        self.ledger = SessionLedger('fake-session', 'fake-server',
                                    server_ownership='mcp_managed')
        self.ref = self.ledger.bind_model('fake-model', ownership='mcp_owned',
                                          fingerprint='fake-fingerprint')
        self.service = ExecutionService(self.ledger, FakeAdapter(),
                                        project_root='/synthetic-project',
                                        on_state_change=lambda _event: None)

    def run(self, response):
        def callback(_args):
            self.calls.append('callback')
            return response
        return self.service.execute_legacy('set_parameters', callback, {},
                                           model_ref=self.ref, expected_revision=0,
                                           request_id='fake-request')

    @property
    def calls(self):
        return self.service.adapter.calls

    @property
    def state(self):
        return self.ledger._state_for(self.ref)


def _unknown_record(message):
    return {'code': 'EXECUTION_STATE_UNKNOWN', 'message': message,
            'safe_retry': True, 'details': {'probe': 'retained-diagnostic'}}


def test_top_level_unknown_error_keeps_declared_code_and_pins_retry_false():
    h = Harness()
    response = {'success': False, 'data': {}, 'error': _unknown_record('top uncertainty')}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert h.state.dirty is True and h.state.fingerprint is None
    assert result['success'] is False
    assert result['data']['verified_outcome'] == 'unknown'
    error = result['error']
    assert isinstance(error, dict)
    assert error['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert error['message'] == 'top uncertainty'
    assert error['details'] == {'probe': 'retained-diagnostic'}
    assert error['safe_retry'] is False
    assert response['error']['safe_retry'] is True


def test_top_level_unknown_refusal_projects_retry_false():
    h = Harness()
    response = {'success': False, 'data': {},
                'refusal': {'code': 'UNKNOWN', 'message': 'refused while unresolved',
                            'safe_retry': True}}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert result['data']['execution_state_unknown'] is True
    assert result['data']['verified_outcome'] == 'unknown'
    assert result['error']['code'] == 'UNKNOWN'
    assert result['error']['safe_retry'] is False
    assert response['refusal']['safe_retry'] is True


@pytest.mark.parametrize('record_key', ['error', 'refusal', 'engine_error'])
def test_immediate_data_unknown_record_is_published(record_key):
    h = Harness()
    response = {'success': False, 'data': {record_key: _unknown_record('nested uncertainty')}}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert h.state.dirty is True and h.state.fingerprint is None
    assert result['success'] is False
    assert result['data']['execution_state_unknown'] is True
    assert result['data']['verified_outcome'] == 'unknown'
    error = result['error']
    assert isinstance(error, dict)
    assert error['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert error['message'] == 'nested uncertainty'
    assert error['details'] == {'probe': 'retained-diagnostic'}
    assert error['safe_retry'] is False
    assert response['data'][record_key]['safe_retry'] is True


def test_conflicting_success_with_nested_unknown_publishes_dict_error():
    h = Harness()
    response = {'success': True, 'data': {},
                'error': {'code': 'UNKNOWN', 'message': 'contradictory success',
                          'safe_retry': True}}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert h.state.dirty is True
    assert result['success'] is False
    assert result['data']['execution_state_unknown'] is True
    assert result['data']['verified_outcome'] == 'unknown'
    error = result['error']
    assert isinstance(error, dict)
    assert error['code'] == 'UNKNOWN'
    assert error['message'] == 'contradictory success'
    assert error['safe_retry'] is False
    assert response['error']['safe_retry'] is True


def test_undeclared_unknown_synthesizes_honest_unknown_error():
    h = Harness()
    result = h.run({'success': False, 'execution_state_unknown': True,
                    'data': {'synthetic_observation': 'retained'}})
    assert h.calls == ['snapshot', 'callback']
    assert result['success'] is False
    assert result['data']['verified_outcome'] == 'unknown'
    error = result['error']
    assert isinstance(error, dict)
    assert error['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert error['safe_retry'] is False


def test_verification_axis_is_preserved_not_fabricated():
    h = Harness()
    result = h.run({'success': False, 'execution_state_unknown': True,
                    'verification_status': 'PASSED',
                    'data': {'synthetic_observation': 'retained'}})
    assert result['data']['verified_outcome'] == 'unknown'
    assert result['data']['verification_status'] == 'PASSED'


def test_definite_failure_keeps_declared_retry_true_and_post_snapshot():
    h = Harness()
    result = h.run({'success': False, 'data': {},
                    'error': {'code': 'DEFINITE_FAILURE', 'message': 'synthetic definite',
                              'safe_retry': True}})
    assert h.calls == ['snapshot', 'callback', 'snapshot']
    assert h.state.dirty is False
    assert result['error']['safe_retry'] is True
    assert result['error']['code'] == 'DEFINITE_FAILURE'


def test_empty_error_on_unknown_does_not_mask_synthesized_error():
    h = Harness()
    response = {'success': False, 'execution_state_unknown': True, 'error': {}, 'data': {}}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert h.state.dirty is True and h.state.fingerprint is None
    assert response['error'] == {}
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert result['error']['message']
    assert result['error']['safe_retry'] is False


def test_empty_top_error_does_not_mask_nested_unknown_error():
    h = Harness()
    record = {'code': 'UNKNOWN', 'message': 'nested unresolved', 'safe_retry': True}
    response = {'success': False, 'error': {}, 'data': {'error': record}}
    result = h.run(response)
    assert h.calls == ['snapshot', 'callback']
    assert h.state.dirty is True and h.state.fingerprint is None
    assert response['error'] == {} and record['safe_retry'] is True
    assert result['error']['code'] == 'UNKNOWN'
    assert result['error']['message'] == 'nested unresolved'
    assert result['error']['safe_retry'] is False
