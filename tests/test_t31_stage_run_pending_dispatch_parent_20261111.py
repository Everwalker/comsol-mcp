"""In-flight stage-run observations with a synthetic, in-memory backend.

These cases re-enter the real dispatch path while its fake backend is inside
its first call, after a synthetic dispatch witness was stored.  They do not
operate on a real daemon, Worker, COMSOL model, SSH session or historical job.
"""
from tests import test_t31_stage_run_nested_read_faults_20261111 as fixture


FIRST_REQUEST = 't31-pending-dispatch-original'
SECOND_REQUEST = 't31-pending-dispatch-observer'
KEY = 't31-pending-dispatch-one-key'


def _observe_during_fake_solve(observer_request):
    receiver, ids = fixture._new_receiver('first_read_stage_conflict')
    store = receiver.store
    observations = []

    def capture_and_lose_response(*, note):
        try:
            operation_id = receiver.backend.calls[0]['operation_id']
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            operation = store.get_operation(operation_id)
            job = store.operation_job(operation_id)
            before_calls = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            pending = fixture._dispatch_stage_run(
                receiver, ids['predecessor']['attempt_id'],
                request_id=observer_request, idempotency_key=KEY,
            )
            after_calls = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((attempt, operation, job, before_calls, pending, after_calls))
        except Exception as error:
            # Exceptions raised inside the fake backend are caught by product
            # UNKNOWN handling; preserve the observer failure for the test.
            observations.append(error)
        raise fixture.InjectedSolveResponseLoss(note)

    receiver.backend._raise_solve_loss = capture_and_lose_response
    try:
        original = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1
        assert not isinstance(observations[0], Exception), repr(observations[0])
        attempt, operation, job, before_calls, pending, after_calls = observations[0]
        assert operation['result'] is None
        assert attempt['request_id'] == FIRST_REQUEST and attempt['idempotency_key'] == KEY
        assert attempt['operation_id'] == operation['operation_id']
        assert attempt['engine_dispatched'] is True
        fixture._assert_durable_dispatch_witness(attempt, receiver.backend)
        assert before_calls == (1, 1) and after_calls == (1, 1)
        assert pending['success'] is True and pending['data']['reused_pending'] is True
        assert pending['execution']['operation_id'] == operation['operation_id']
        assert pending['execution']['request_id'] == FIRST_REQUEST
        assert pending['data']['job_id'] == job['job_id']
        assert original['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert len(receiver.session_scheduler.calls) == 1 and len(receiver.backend.calls) == 1
        assert fixture._operation_count(store) == 1
        assert len(fixture._attempt_rows(store, ids['model_ref'])) == 1
        return pending, attempt
    finally:
        store.close()


def test_inflight_same_request_projects_known_dispatch():
    pending, attempt = _observe_during_fake_solve(FIRST_REQUEST)
    assert pending['data']['stage_attempt_id'] == attempt['attempt_id']
    assert pending['data']['engine_dispatched'] is True


def test_inflight_new_request_projects_original_dispatch():
    pending, attempt = _observe_during_fake_solve(SECOND_REQUEST)
    assert pending['data']['engine_dispatched'] is True
    assert pending['data']['stage_attempt_id'] == attempt['attempt_id']
