"""A normal server-generated page cannot continue after a reported gap.

The fixed replies are synthetic; no cursor is forged, no Worker or COMSOL
is launched, and this is not a cursor-tampering test.
"""
import pytest
from comsol_mcp._g2_engine import find_nodes
from tests.test_t13_discovery_cursor_parent_20261109 import SequenceWorker, _reply


@pytest.mark.parametrize('reason', ['node_budget', 'time_budget', 'match_limit'])
def test_error_and_truncation_does_not_issue_continuation(reason):
    fault = {'collection': 'component', 'code': 'GETTER_FAILED', 'message': 'completed probe failure'}
    reply = _reply(visited=1, truncated=reason, errors=[fault])
    if reason == 'match_limit':
        reply['matches'] = [{'segments': [{'collection': 'component', 'tag': 'comp1'}],
                             'tag': 'comp1', 'type_id': 'Component', 'label': 'Component 1'}]
        reply['matched_in_call'] = 1
    worker = SequenceWorker([reply])
    result = find_nodes(worker, 'm', {'type': 'Component'}, model_revision=4)
    assert len(worker.calls) == 1, 'batch reply must actually be consumed once'
    assert result['status'] == 'INCOMPLETE' and result['complete'] is False
    assert result['incomplete'] is True and result['truncated'] is True
    assert result['truncated_reason'] == reason and result['errors'] == [fault]
    assert result['next_cursor'] is None, 'a later clean page cannot repair this observed gap'
    assert result['count'] == (1 if reason == 'match_limit' else 0)


def test_error_without_truncation_is_still_incomplete():
    fault = {'collection': 'component', 'code': 'GETTER_FAILED', 'message': 'completed probe failure'}
    worker = SequenceWorker([_reply(visited=1, truncated=None, errors=[fault])])
    result = find_nodes(worker, 'm', {'type': 'Component'}, model_revision=4)
    assert len(worker.calls) == 1
    assert result['status'] == 'INCOMPLETE' and result['complete'] is False
    assert result['next_cursor'] is None and result['errors'] == [fault]


def test_clean_match_limit_can_continue_from_server_cursor():
    first_reply = _reply(visited=1, truncated='match_limit', errors=[])
    first_reply['matches'] = [{'segments': [{'collection': 'component', 'tag': 'comp1'}],
                               'tag': 'comp1', 'type_id': 'Component', 'label': 'Component 1'}]
    first_reply['matched_in_call'] = 1
    worker = SequenceWorker([first_reply, _reply(visited=2, truncated=None, errors=[])])
    first = find_nodes(worker, 'm', {'type': 'Component'}, model_revision=4)
    assert first['next_cursor'] is not None and first['count'] == 1 and first['errors'] == []
    last = find_nodes(worker, 'm', {'type': 'Component'}, model_revision=4, cursor=first['next_cursor'])
    assert last['status'] == 'SUCCEEDED' and last['complete'] is True
    assert [call['skip_visited'] for call in worker.calls] == [0, 1]


def test_g2_backend_action_retains_incomplete_without_resume():
    # Real backend action dispatch, with its construction/authorization bypassed
    # and a synthetic batch worker: this is not a public daemon or engine test.
    from comsol_mcp._managed_backend import ManagedBackend
    fault = {'collection': 'component', 'code': 'GETTER_FAILED', 'message': 'completed probe failure'}
    worker = SequenceWorker([_reply(visited=1, truncated='node_budget', errors=[fault])])
    backend = ManagedBackend.__new__(ManagedBackend)
    backend.worker = worker
    envelope = backend._run_g2_action('node.find', 'm', {'query': {'type': 'Component'}},
                                      'synthetic-operation', model_revision=4)
    assert len(worker.calls) == 1
    assert envelope['success'] is True
    result = envelope['data']
    assert result['status'] == 'INCOMPLETE' and result['complete'] is False
    assert result['errors'] == [fault] and result['next_cursor'] is None
