"""T31 synthetic diagnostics for typed failures while reporting UNKNOWN."""
from tests import test_t31_stage_run_nested_read_faults_20261111 as fixture
from comsol_mcp._execution_contract import ExecutionContractError


def _check_reporting_diagnostic(mode, expected_type, *, inject_unsafe_retry):
    receiver, ids = fixture._new_receiver(mode)
    store = receiver.store
    built = []
    original_make = store._make_read_fault

    def fault_with_retry_claim(action):
        error = original_make(action)
        assert isinstance(error, ExecutionContractError)
        error.safe_retry = True
        built.append(error)
        return error

    if inject_unsafe_retry:
        store._make_read_fault = fault_with_retry_claim
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-reporting-type-first', idempotency_key='t31-key-single-use',
        )
        attempt_id = fixture._solve_attempt_id(receiver)
        hit = fixture._assert_first_handler_reached(receiver, attempt_id, hit_index=1)
        assert hit['action'] in {'raise_stage_conflict', 'raise_contract_fault'}
        if inject_unsafe_retry:
            assert len(built) == 1 and built[0].safe_retry is True
        assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert result['error']['safe_retry'] is False
        assert result['error']['type'] == expected_type
        assert len(receiver.log_exceptions) == 1
        assert expected_type in receiver.log_exceptions[0]
        assert 'InjectedSolveResponseLoss' in receiver.log_exceptions[0]
        assert fixture._operation_row(store)['status'] == 'UNKNOWN'
        fixture._same_key_replay_only_observes(receiver, ids, result)
    finally:
        store.close()


def test_stage_conflict_reporting_preserves_fault_type():
    _check_reporting_diagnostic(
        'first_read_stage_conflict', 'InjectedStageConflictAtFirstRead',
        inject_unsafe_retry=False,
    )


def test_contract_reporting_never_exposes_unsafe_retry_claim():
    _check_reporting_diagnostic(
        'first_read_contract_fault', 'InjectedContractFaultAtFirstRead',
        inject_unsafe_retry=True,
    )
