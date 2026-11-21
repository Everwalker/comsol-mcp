"""Exact missing-row projection control for the T31 synthetic stage run."""
from tests import test_t31_stage_run_nested_read_faults_20261111 as fixture


def test_missing_final_attempt_is_explicitly_unknown():
    receiver, ids = fixture._new_receiver('second_read_returns_none')
    store = receiver.store
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-missing-attempt-first', idempotency_key='t31-key-single-use',
        )
        attempt_id = fixture._solve_attempt_id(receiver)
        hit = fixture._assert_first_handler_reached(receiver, attempt_id, hit_index=2)
        assert hit['action'] == 'return_none'
        durable = fixture._recovery_read(store, ids['model_ref'], attempt_id)
        assert durable is not None and durable['status'] == 'UNKNOWN'
        fixture._assert_durable_dispatch_witness(durable, receiver.backend)
        assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert result['error']['safe_retry'] is False
        assert result['data']['attempt'] is None
        assert result['data']['engine_dispatched'] is None
        assert fixture._operation_row(store)['status'] == 'UNKNOWN'
        fixture._same_key_replay_only_observes(receiver, ids, result)
    finally:
        store.close()
