"""Pre-solve control for the T31 stage-run UNKNOWN response fix.

This uses the existing isolated synthetic receiver and a fresh memory store;
there is no real daemon, Worker, COMSOL, geometry operation or historical job.
"""
from tests import test_t31_stage_run_nested_unknown_20261111 as fixture


def test_preflight_exception_retains_confirmed_no_solve_dispatch_and_replay():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    calls = []

    def fail_admission(**kwargs):
        calls.append(kwargs['binding']['attempt_id'])
        raise RuntimeError('T31 synthetic pre-solve admission failure')

    receiver.backend.stage_native_admission = fail_admission
    try:
        result = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-preflight-control-first', idempotency_key='t31-preflight-control-key',
        )
        assert len(calls) == 1 and len(receiver.log_exceptions) == 1
        assert receiver.backend.calls == [] and receiver.backend.emitted_events == []
        assert len(receiver.session_scheduler.calls) == 1
        assert result['success'] is False
        assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert result['error']['safe_retry'] is False
        assert result['data']['engine_dispatched'] is False
        attempt = result['data']['attempt']
        assert attempt['attempt_id'] == calls[0]
        assert attempt['status'] == 'UNKNOWN'
        assert attempt['engine_dispatched'] is False
        assert attempt['evidence'] == [{'kind': 'control-state', 'error_type': 'RuntimeError'}]
        assert store.get_stage_attempt(fixture.PROJECT_ID, ids['model_ref'], calls[0]) == attempt
        assert fixture._operation_row(store)['status'] == 'UNKNOWN'
        replay = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id='t31-preflight-control-replay', idempotency_key='t31-preflight-control-key',
        )
        assert replay == result
        assert len(receiver.session_scheduler.calls) == 1 and receiver.backend.calls == []
        assert len(fixture._attempt_rows(store, ids['model_ref'])) == 1
        assert fixture._operation_count(store) == 1
    finally:
        store.close()
