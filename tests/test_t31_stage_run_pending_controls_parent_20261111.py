"""Supplemental T31 pending-observation controls on a fresh memory store."""
from tests import test_t31_stage_run_nested_read_faults_20261111 as fixture


ORIGINAL_REQUEST = 't31-pending-controls-original'
OBSERVER_REQUEST = 't31-pending-controls-observer'
KEY = 't31-pending-controls-one-key'


def test_pending_admitted_attempt_reports_confirmed_false_without_resubmit():
    receiver, ids = fixture._new_receiver('first_read_stage_conflict')
    store = receiver.store
    observations = []
    original_admission = receiver.backend.stage_native_admission

    def observe_before_fake_solve(*, binding, plan, stage):
        try:
            attempt = store.original_get_stage_attempt(
                fixture.PROJECT_ID, ids['model_ref'], binding['attempt_id'],
            )
            operation = store.get_operation(binding['operation_id'])
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            pending = fixture._dispatch_stage_run(
                receiver, ids['predecessor']['attempt_id'],
                request_id=OBSERVER_REQUEST, idempotency_key=KEY,
            )
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((attempt, operation, pending, before, after))
        except Exception as error:
            observations.append(error)
        return original_admission(binding=binding, plan=plan, stage=stage)

    receiver.backend.stage_native_admission = observe_before_fake_solve
    try:
        fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=ORIGINAL_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        attempt, operation, pending, before, after = observations[0]
        assert operation['result'] is None
        assert attempt['operation_id'] == operation['operation_id']
        assert attempt['status'] == 'ADMITTED' and attempt['engine_dispatched'] is False
        assert attempt['evidence'] == []
        assert before == (1, 0) and after == (1, 0)
        assert pending['data']['reused_pending'] is True
        assert pending['data']['stage_attempt_id'] == attempt['attempt_id']
        assert pending['data']['stage_attempt_status'] == 'ADMITTED'
        assert pending['data']['engine_dispatched'] is False
        assert pending['execution']['operation_id'] == operation['operation_id']
        assert len(receiver.session_scheduler.calls) == 1 and len(receiver.backend.calls) == 1
    finally:
        store.close()


def test_pending_attempt_read_error_reports_unknown_without_resubmit():
    receiver, ids = fixture._new_receiver('first_read_stage_conflict')
    store = receiver.store
    observations, hits = [], []
    original_list = store.list_stage_attempts

    def observe_with_failed_read(*, note):
        try:
            operation_id = receiver.backend.calls[0]['operation_id']
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))

            def fail_list(*args, **kwargs):
                hits.append((args, kwargs))
                raise RuntimeError('t31 synthetic pending attempt read failure')

            store.list_stage_attempts = fail_list
            try:
                pending = fixture._dispatch_stage_run(
                    receiver, ids['predecessor']['attempt_id'],
                    request_id=OBSERVER_REQUEST, idempotency_key=KEY,
                )
            finally:
                store.list_stage_attempts = original_list
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((attempt, operation_id, pending, before, after))
        except Exception as error:
            observations.append(error)
        raise fixture.InjectedSolveResponseLoss(note)

    receiver.backend._raise_solve_loss = observe_with_failed_read
    try:
        fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=ORIGINAL_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        attempt, operation_id, pending, before, after = observations[0]
        assert len(hits) == 1
        assert attempt['operation_id'] == operation_id and attempt['engine_dispatched'] is True
        fixture._assert_durable_dispatch_witness(attempt, receiver.backend)
        assert before == (1, 1) and after == (1, 1)
        assert pending['data']['reused_pending'] is True
        assert pending['data']['engine_dispatched'] is None
        assert pending['data']['stage_attempt_id'] is None
        assert pending['data']['stage_attempt_status'] == 'UNKNOWN'
        assert pending['execution']['operation_id'] == operation_id
        assert len(receiver.session_scheduler.calls) == 1 and len(receiver.backend.calls) == 1
    finally:
        store.close()
