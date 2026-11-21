"""T31 matched/failed pending-read controls on a fresh in-memory store."""
from tests import test_t31_stage_run_nested_unknown_20261111 as fixture


FIRST_REQUEST = 't31-pending-matched-first'
OBSERVER_REQUEST = 't31-pending-matched-observer'
KEY = 't31-pending-matched-one-key'


def test_pending_matching_dispatched_attempt_remains_true():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    original_loss = receiver.backend._raise_solve_loss
    observations = []

    def observe_then_lose_response(*, after_fake_dispatch, note):
        try:
            assert after_fake_dispatch is True
            operation_id = receiver.backend.calls[0]['operation_id']
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            pending = fixture._dispatch_stage_run(
                receiver, ids['predecessor']['attempt_id'],
                request_id=OBSERVER_REQUEST, idempotency_key=KEY,
            )
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((operation_id, attempt, before, pending, after))
        except Exception as error:
            observations.append(error)
        return original_loss(after_fake_dispatch=after_fake_dispatch, note=note)

    receiver.backend._raise_solve_loss = observe_then_lose_response
    try:
        fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        operation_id, attempt, before, pending, after = observations[0]
        assert attempt['engine_dispatched'] is True
        assert len(fixture._dispatch_evidence(attempt)) == 1
        assert before == (1, 1) and after == (1, 1)
        assert pending['execution']['operation_id'] == operation_id
        assert pending['data']['stage_attempt_id'] == attempt['attempt_id']
        assert pending['data']['engine_dispatched'] is True
    finally:
        store.close()


def test_pending_matching_admitted_attempt_remains_false():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    original_admission = receiver.backend.stage_native_admission
    observations = []

    def observe_before_solve(*, binding, plan, stage):
        try:
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], binding['operation_id'],
            )
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            pending = fixture._dispatch_stage_run(
                receiver, ids['predecessor']['attempt_id'],
                request_id=OBSERVER_REQUEST, idempotency_key=KEY,
            )
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((binding['operation_id'], attempt, before, pending, after))
        except Exception as error:
            observations.append(error)
        return original_admission(binding=binding, plan=plan, stage=stage)

    receiver.backend.stage_native_admission = observe_before_solve
    try:
        fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        operation_id, attempt, before, pending, after = observations[0]
        assert attempt['status'] == 'ADMITTED' and attempt['engine_dispatched'] is False
        assert attempt['evidence'] == [] and before == (1, 0) and after == (1, 0)
        assert pending['execution']['operation_id'] == operation_id
        assert pending['data']['stage_attempt_id'] == attempt['attempt_id']
        assert pending['data']['engine_dispatched'] is False
    finally:
        store.close()


def test_pending_failed_attempt_read_remains_unknown():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    original_loss = receiver.backend._raise_solve_loss
    original_list = store.list_stage_attempts
    observations, hits = [], []

    def fail_attempt_list(*args, **kwargs):
        hits.append((args, kwargs))
        raise RuntimeError('injected T31 pending read error')

    def observe_then_lose_response(*, after_fake_dispatch, note):
        try:
            assert after_fake_dispatch is True
            operation_id = receiver.backend.calls[0]['operation_id']
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            store.list_stage_attempts = fail_attempt_list
            try:
                pending = fixture._dispatch_stage_run(
                    receiver, ids['predecessor']['attempt_id'],
                    request_id=OBSERVER_REQUEST, idempotency_key=KEY,
                )
            finally:
                store.list_stage_attempts = original_list
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((operation_id, attempt, before, pending, after))
        except Exception as error:
            observations.append(error)
        return original_loss(after_fake_dispatch=after_fake_dispatch, note=note)

    receiver.backend._raise_solve_loss = observe_then_lose_response
    try:
        fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        operation_id, attempt, before, pending, after = observations[0]
        assert len(hits) == 1 and attempt['engine_dispatched'] is True
        assert before == (1, 1) and after == (1, 1)
        assert pending['execution']['operation_id'] == operation_id
        assert pending['data']['engine_dispatched'] is None
        assert pending['data']['stage_attempt_id'] is None
        assert pending['data']['stage_attempt_status'] == 'UNKNOWN'
    finally:
        store.list_stage_attempts = original_list
        store.close()
