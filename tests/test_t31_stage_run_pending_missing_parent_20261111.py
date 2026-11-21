"""T31 pending projection when the attempt list has no matching row.

The source-bound tests use a fresh in-memory store and inert receiver.  The
empty-list case after a synthetic dispatch is an injected inconsistent-read
fault model, not an observation of a real store or COMSOL Worker failure.
"""
from tests import test_t31_stage_run_nested_unknown_20261111 as fixture


FIRST_REQUEST = 't31-pending-missing-first'
OBSERVER_REQUEST = 't31-pending-missing-observer'
KEY = 't31-pending-missing-one-key'


def test_pending_empty_read_must_not_deny_known_synthetic_dispatch():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    original_list = store.list_stage_attempts
    original_loss = receiver.backend._raise_solve_loss
    observations, hits = [], []

    def empty_attempt_list(*args, **kwargs):
        hits.append((args, kwargs))
        return []

    def observe_then_lose_response(*, after_fake_dispatch, note):
        try:
            assert after_fake_dispatch is True
            operation_id = receiver.backend.calls[0]['operation_id']
            attempt = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            listed = fixture._attempt_rows(store, ids['model_ref'])
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            store.list_stage_attempts = empty_attempt_list
            try:
                pending = fixture._dispatch_stage_run(
                    receiver, ids['predecessor']['attempt_id'],
                    request_id=OBSERVER_REQUEST, idempotency_key=KEY,
                )
            finally:
                store.list_stage_attempts = original_list
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            persisted = store.get_stage_attempt_for_operation(
                fixture.PROJECT_ID, ids['model_ref'], operation_id,
            )
            observations.append((operation_id, attempt, listed, before, pending, after, persisted))
        except Exception as error:
            observations.append(error)
        return original_loss(after_fake_dispatch=after_fake_dispatch, note=note)

    receiver.backend._raise_solve_loss = observe_then_lose_response
    try:
        original = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        operation_id, attempt, listed, before, pending, after, persisted = observations[0]
        assert len(hits) == 1 and hits[0][1]['stage_id'] == fixture.STAGE_ID
        assert len(listed) == 1 and listed[0] == attempt == persisted
        assert attempt['operation_id'] == operation_id and attempt['engine_dispatched'] is True
        witness = fixture._dispatch_evidence(attempt)
        assert len(witness) == 1 and witness[0]['worker_method'] == 'run'
        assert before == (1, 1) and after == (1, 1)
        assert fixture._operation_count(store) == 1
        assert pending['success'] is True and pending['data']['reused_pending'] is True
        assert pending['execution']['operation_id'] == operation_id
        assert original['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        # An empty result is not positive evidence that dispatch did not occur.
        assert pending['data']['engine_dispatched'] is None
    finally:
        store.list_stage_attempts = original_list
        store.close()


def test_pending_before_attempt_admission_reports_unknown_not_false():
    receiver, ids = fixture._new_receiver('after_dispatch_clean')
    store = receiver.store
    original_context = receiver._execution_session_context
    observations = []

    def observe_before_attempt(execution):
        try:
            operation_row = fixture._operation_row(store)
            assert operation_row is not None and operation_row['result'] is None
            operation_id = operation_row['operation_id']
            attempts = fixture._attempt_rows(store, ids['model_ref'])
            before = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            pending = fixture._dispatch_stage_run(
                receiver, ids['predecessor']['attempt_id'],
                request_id=OBSERVER_REQUEST, idempotency_key=KEY,
            )
            after = (len(receiver.session_scheduler.calls), len(receiver.backend.calls))
            observations.append((operation_id, attempts, before, pending, after))
        except Exception as error:
            observations.append(error)
        return original_context(execution)

    receiver._execution_session_context = observe_before_attempt
    try:
        original = fixture._dispatch_stage_run(
            receiver, ids['predecessor']['attempt_id'],
            request_id=FIRST_REQUEST, idempotency_key=KEY,
        )
        assert len(observations) == 1 and not isinstance(observations[0], Exception), observations
        operation_id, attempts, before, pending, after = observations[0]
        assert attempts == [] and before == (0, 0) and after == (0, 0)
        assert fixture._operation_count(store) == 1
        assert len(receiver.session_scheduler.calls) == 1 and len(receiver.backend.calls) == 1
        assert pending['success'] is True and pending['data']['reused_pending'] is True
        assert pending['execution']['operation_id'] == operation_id
        assert pending['data']['stage_attempt_id'] is None
        assert original['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
        assert pending['data']['engine_dispatched'] is None
    finally:
        store.close()
