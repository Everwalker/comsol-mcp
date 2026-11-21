"""Bounded positive dispatch-witness control for T31's nested UNKNOWN path.

All state is synthetic and in memory; this is not a native Worker test.
"""
from tests import test_t31_stage_run_nested_unknown_20261111 as fixture


def test_known_dispatched_witness_is_returned_after_nested_read_failure():
    receiver, ids = fixture._new_receiver('after_dispatch_nested_fault')
    store = receiver.store
    get_attempt = store.get_stage_attempt
    successful_reads_after_fault = []

    def observe_attempt_read(*args, **kwargs):
        row = get_attempt(*args, **kwargs)
        if store.nested_read_fault_hits:
            successful_reads_after_fault.append(row)
        return row

    store.get_stage_attempt = observe_attempt_read
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-known-dispatch-first', idempotency_key='t31-known-dispatch-key',
        )
        fixture._assert_common_reachability(receiver)
        assert receiver.backend.fault_hits[0]['after_fake_dispatch_evidence'] is True
        assert len(store.nested_read_fault_hits) == 1
        assert store.nested_read_fault_hits[0]['function'] == 'unknown_result'
        assert len(receiver.log_exceptions) == 1
        assert len(successful_reads_after_fault) == 1
        observed = successful_reads_after_fault[0]
        marker = receiver.backend.solve_marker
        assert marker is not None
        attempt_id = marker['attempt_id']
        assert observed is not None and observed['attempt_id'] == attempt_id
        assert observed['status'] == 'RUNNING' and observed['engine_dispatched'] is True
        assert len(fixture._dispatch_evidence(observed)) == 1
        durable = get_attempt(fixture.PROJECT_ID, ids['model_ref'], attempt_id)
        assert durable == observed
        assert result['success'] is False
        assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert result['error']['safe_retry'] is False
        assert result['data']['engine_dispatched'] is True
        assert result['data']['attempt'] == observed
        assert fixture._operation_row(store)['status'] == 'UNKNOWN'

        replay = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-known-dispatch-replay', idempotency_key='t31-known-dispatch-key',
        )
        assert replay == result
        assert fixture._operation_count(store) == 1
        fixture._assert_no_second_admission_on_replay(receiver, store, ids['model_ref'], 1)
    finally:
        store.close()
