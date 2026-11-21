"""Ordinary typed preflight refusal controls for the T31 stage-run fix.

The receiver, admission, authorization and backend are synthetic; the store is
fresh in-memory state.  These cases make no native authorization claim.
"""
from tests import test_t31_stage_run_nested_unknown_20261111 as fixture
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import StagePlanStoreConflict


def _check_typed_refusal(error, expected_code):
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    admissions = []

    def refuse(**kwargs):
        admissions.append(kwargs['binding']['attempt_id'])
        raise error

    receiver.backend.stage_native_admission = refuse
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-typed-preflight-first', idempotency_key='t31-typed-preflight-key',
        )
        assert len(admissions) == 1
        assert len(receiver.session_scheduler.calls) == 1
        assert receiver.backend.calls == [] and receiver.backend.emitted_events == []
        assert result['success'] is False
        assert result['error']['code'] == expected_code
        assert result['error']['safe_retry'] is False
        assert fixture._operation_row(store)['status'] == 'FAILED'
        rows = fixture._attempt_rows(store, ids['model_ref'])
        assert len(rows) == 1 and rows[0]['attempt_id'] == admissions[0]
        assert rows[0]['status'] == 'ADMITTED' and rows[0]['engine_dispatched'] is False
        replay = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-typed-preflight-replay', idempotency_key='t31-typed-preflight-key',
        )
        assert replay == result
        assert len(receiver.session_scheduler.calls) == 1 and receiver.backend.calls == []
        assert len(fixture._attempt_rows(store, ids['model_ref'])) == 1
        assert fixture._operation_count(store) == 1
    finally:
        store.close()


def test_preflight_store_conflict_remains_a_typed_refusal():
    _check_typed_refusal(
        StagePlanStoreConflict('T31_TEST_PREFLIGHT_CONFLICT', 'synthetic preflight refusal'),
        'T31_TEST_PREFLIGHT_CONFLICT',
    )


def test_preflight_contract_error_remains_a_typed_refusal():
    _check_typed_refusal(
        ExecutionContractError('T31_TEST_PREFLIGHT_CONTRACT', 'synthetic preflight refusal'),
        'T31_TEST_PREFLIGHT_CONTRACT',
    )
