"""T13 live-service correction: returned UNKNOWN stops before any new snapshot.

Nine permanent cases exercising the real ExecutionService, SessionLedger and
classify_envelope through task-owned in-memory fake adapters and callbacks.
No daemon, Worker, COMSOL, real model, filesystem target, network, discovery,
getter, pagination, cursor or external identity environment is used.
"""
from __future__ import annotations

import pytest

import comsol_mcp._execution_service as service_module
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class SyntheticAdapter:
    def __init__(self, order):
        self.order = order

    def model_snapshot(self, tag):
        self.order.append('snapshot')
        return {'model_tag': tag, 'server_instance_id': 'synthetic-server',
                'fingerprint': 'synthetic-fingerprint', 'external_event_counter': 0}


class Harness:
    def __init__(self):
        self.order = []
        self.events = []
        self.ledger = SessionLedger('synthetic-session', 'synthetic-server',
                                    server_ownership='mcp_managed')
        self.ref = self.ledger.bind_model('synthetic-model', ownership='mcp_owned',
                                          fingerprint='synthetic-fingerprint')
        self.service = ExecutionService(self.ledger, SyntheticAdapter(self.order),
                                        project_root='/synthetic-project',
                                        on_state_change=self.events.append)

    def run(self, response):
        def callback(_args):
            assert self.order == ['snapshot']
            self.order.append('callback')
            return response
        return self.service.execute_legacy('set_parameters', callback, {}, model_ref=self.ref,
                                           expected_revision=0, request_id='synthetic-request')

    def run_close(self, monkeypatch, response):
        # The synthetic ledger is explicitly granted the host permission the
        # in-memory fake close requires; this is a fixture grant, not a claim
        # that any real host control ran.
        self.ledger.permissions.add('host_control')
        monkeypatch.setattr(service_module, 'permission_for_legacy_tool', lambda _tool: 'host_control')
        def callback(_args):
            assert self.order == ['snapshot']
            self.order.append('callback')
            return response
        return self.service.execute_legacy('model_close', callback, {}, model_ref=self.ref,
                                           expected_revision=0, request_id='synthetic-close',
                                           effect='host_control', terminal_model_close=True)

    @property
    def state(self):
        return self.ledger._state_for(self.ref)


def _flagged_unknown():
    return {'success': False, 'execution_state_unknown': True, 'partial_change': True,
            'data': {'synthetic_observation': 'retained'},
            'error': {'code': 'EXECUTION_STATE_UNKNOWN', 'message': 'synthetic uncertainty',
                      'safe_retry': False}}


def test_t13_flagged_unknown_stops_before_post_snapshot():
    h = Harness()
    result = h.run(_flagged_unknown())
    assert h.order == ['snapshot', 'callback']
    assert result['success'] is False
    assert result['data']['execution_state_unknown'] is True
    assert h.state.dirty is True and h.state.fingerprint is None
    assert h.state.active_operation_id is None and h.state.revision == 1


def test_t13_code_only_unknown_resolves_ticket_unknown():
    h = Harness()
    result = h.run({'success': False, 'data': {'synthetic_observation': 'retained'},
                    'error': {'code': 'EXECUTION_STATE_UNKNOWN',
                              'message': 'synthetic uncertainty', 'safe_retry': False}})
    assert h.order == ['snapshot', 'callback']
    assert result['success'] is False
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert result['data']['execution_state_unknown'] is True
    assert h.state.dirty is True and h.state.fingerprint is None
    assert h.state.active_operation_id is None and h.state.revision == 1


def test_t13_cleanup_uncertainty_stops_before_post_snapshot():
    h = Harness()
    result = h.run({'success': True, 'data': {'cleanup': {'cleanup_failed': True},
                                             'synthetic_observation': 'retained'}})
    assert h.order == ['snapshot', 'callback']
    assert result['success'] is False
    assert result['data']['cleanup']['cleanup_failed'] is True
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert h.state.dirty is True and h.state.fingerprint is None


def test_t13_reported_partial_change_is_retained():
    h = Harness()
    result = h.run(_flagged_unknown())
    assert result['data']['partial_change'] is True
    assert result['data']['synthetic_observation'] == 'retained'
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert result['error']['safe_retry'] is False


def test_t13_safe_retry_projected_false_without_mutating_callback_input():
    h = Harness()
    response = {'success': False, 'data': {},
                'error': {'code': 'EXECUTION_STATE_UNKNOWN',
                          'message': 'synthetic conflicting metadata', 'safe_retry': True}}
    result = h.run(response)
    assert result['success'] is False
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert result['error']['safe_retry'] is False
    # The callback's own mapping is never mutated; projection is by copy.
    assert response['error']['safe_retry'] is True
    assert h.state.dirty is True and h.order == ['snapshot', 'callback']


def test_t13_known_success_still_takes_post_snapshot():
    h = Harness()
    result = h.run({'success': True, 'data': {'synthetic_observation': 'completed'}})
    assert h.order == ['snapshot', 'callback', 'snapshot']
    assert result['success'] is True and result['data']['synthetic_observation'] == 'completed'
    assert h.state.dirty is False and h.state.revision == 1


def test_t13_definite_refusal_stays_clean_and_takes_post_snapshot():
    h = Harness()
    result = h.run({'success': False, 'error': {'code': 'SYNTHETIC_REFUSAL',
                                               'message': 'known refusal', 'safe_retry': False}})
    assert h.order == ['snapshot', 'callback', 'snapshot']
    assert result['success'] is False and result['error']['code'] == 'SYNTHETIC_REFUSAL'
    assert h.state.dirty is False and h.state.revision == 0


def _exact_close_proof(h):
    return {'status': 'CLOSED', 'model_ref': h.ref.as_dict(),
            'model_tag': h.ref.model_tag, 'model_tag_absent': True,
            'other_model_tags_preserved': True, 'terminal_close_verified': True}


def test_t13_valid_terminal_close_retires_without_resnapshot(monkeypatch):
    h = Harness()
    result = h.run_close(monkeypatch, {'success': True, 'data': _exact_close_proof(h)})
    assert result['success'] is True
    assert h.order == ['snapshot', 'callback']
    state = h.ledger._models[h.ref.model_tag]
    assert state.retired is True and state.active_operation_id is None


def test_t13_contradictory_success_unknown_close_cannot_retire(monkeypatch):
    h = Harness()
    response = {'success': True, 'execution_state_unknown': True,
                'data': _exact_close_proof(h),
                'error': {'code': 'EXECUTION_STATE_UNKNOWN', 'message': 'unresolved',
                          'safe_retry': False}}
    result = h.run_close(monkeypatch, response)
    assert result['success'] is False
    assert h.order == ['snapshot', 'callback']
    state = h.ledger._models[h.ref.model_tag]
    assert state.retired is False and state.dirty is True and state.active_operation_id is None
