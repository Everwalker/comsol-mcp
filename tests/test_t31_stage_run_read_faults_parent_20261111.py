"""T31 nested-read fault contract using an isolated synthetic memory fixture.

The Qwen-authored module supplies the fake receiver and fault injection only.
Its preimage-specific response assertions are deliberately not called here.
No real model, Worker, process, SSH, geometry, or historical job is used.
"""
from tests import test_t31_stage_run_nested_read_faults_20261111 as fixture


_MISSING = object()


def _exercise(mode, action, hit_index, *, persistent_read_failure=False):
    receiver, ids = fixture._new_receiver(mode)
    store = receiver.store
    blocked_followup_reads = []
    armed_fault_read = store.get_stage_attempt
    first_read_failure = False

    def consistently_unreadable(*args, **kwargs):
        nonlocal first_read_failure
        if first_read_failure:
            blocked_followup_reads.append(args[2])
            raise fixture.InjectedDurableReadFailure('T31 synthetic persistent read fault')
        try:
            return armed_fault_read(*args, **kwargs)
        except fixture.InjectedDurableReadFailure:
            first_read_failure = True
            raise

    if persistent_read_failure:
        store.get_stage_attempt = consistently_unreadable
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-read-fault-first', idempotency_key='t31-key-single-use',
        )
        attempt_id = fixture._solve_attempt_id(receiver)
        hit = fixture._assert_first_handler_reached(receiver, attempt_id, hit_index=hit_index)
        assert hit['action'] == action
        durable = fixture._recovery_read(store, ids['model_ref'], attempt_id)
        assert durable is not None
        assert durable['status'] in {'RUNNING', 'UNKNOWN'}
        fixture._assert_durable_dispatch_witness(durable, receiver.backend)
        if durable['status'] == 'RUNNING':
            fixture._probe_attempt_not_rewritable(
                store, ids['model_ref'], durable,
                expect_code='STAGE_ATTEMPT_STATE_UNKNOWN',
                expect_fragment='engine dispatch evidence cannot be cleared',
            )
        else:
            fixture._probe_attempt_not_rewritable(
                store, ids['model_ref'], durable,
                expect_code='STAGE_ATTEMPT_TRANSITION_INVALID',
                expect_fragment='status transition is not permitted',
            )
        if persistent_read_failure:
            assert first_read_failure is True
            assert blocked_followup_reads == [attempt_id], blocked_followup_reads

        assert result['success'] is False
        assert result['error']['safe_retry'] is False
        assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN', (
            result['error'], fixture._operation_row(store)['status']
        )
        assert fixture._operation_row(store)['status'] == 'UNKNOWN'
        projection = result['data'].get('engine_dispatched', _MISSING)
        assert projection is True or projection is None, (
            'a synthetic durable True dispatch witness cannot be projected as False or omitted',
            projection,
        )
        if persistent_read_failure:
            assert projection is None
            assert result['data']['attempt'] is None
        returned = result['data'].get('attempt')
        if returned is not None:
            assert returned == durable
        fixture._same_key_replay_only_observes(receiver, ids, result)
    finally:
        store.close()


def test_first_read_stage_conflict_is_unknown():
    _exercise('first_read_stage_conflict', 'raise_stage_conflict', 1)


def test_first_read_contract_error_is_unknown():
    _exercise('first_read_contract_fault', 'raise_contract_fault', 1)


def test_second_read_none_is_not_false_dispatch():
    _exercise('second_read_returns_none', 'return_none', 2)


def test_failed_response_read_is_explicitly_unknown():
    _exercise('second_read_sticky_failure', 'raise_read_failure', 2,
              persistent_read_failure=True)
