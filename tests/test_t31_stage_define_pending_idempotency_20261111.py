"""T31/U07: in-flight idempotency regression for ``experiment.stage_define``.

Targeted defect (source-observed, not touched here):
``ControlDaemon._dispatch_experiment_stage_define`` (_control_daemon.py:2691-2701)
only returns early on ``reused and record.get("result") is not None``.  When the
same idempotency key is replayed while the first attempt is still in flight
(result is None), execution falls through and re-enters the scheduler path, so
``self.session_scheduler.submit`` can run a second time for one durable
operation.  The generic route ``ControlDaemon._dispatch_admitted``
(_control_daemon.py:1308-1309) instead returns
``record["result"] if record["result"] is not None else self._pending(record)``
for any reused key; the stage_define route must honour the same invariant.

What is REAL in these tests (product code under test):
- ``ControlDaemon._dispatch_admitted`` and its stage_define route
  ``ControlDaemon._dispatch_experiment_stage_define`` are invoked as unbound
  functions against an inert fake receiver; no ControlDaemon instance is ever
  constructed or started.
- ``ControlDaemon._timeouts`` / ``_pending`` / ``_finish`` / ``_error`` /
  ``_exception`` / terminal-resource accounting helpers run as the real
  functions on the receiver.
- The real ``OperationStore`` (in-memory SQLite only, path ``":memory:"``)
  performs admission (``begin``), the idempotency-hash conflict check, and the
  row-count observations; the real ``validate_call`` schema/shape validation and
  ``canonical_request_hash`` digest run before admission.

What is FAKE (deliberate, bounded, and NOT a real authorization or scheduling
attestation):
- ``_StageDefineReceiver._authorize_project_execution`` and
  ``project_authority.apply_timeout_caps`` are inert stubs: this is NOT a
  project-policy/authorization proof.
- ``_execution_session_context`` returns a module sentinel instead of a real
  session context.
- ``_InFlightScheduler.submit`` only captures ``(context, callback)`` and
  returns a future whose ``result()`` immediately raises
  ``concurrent.futures.TimeoutError`` (the same ``FutureTimeout`` class the
  product route catches at _control_daemon.py:2830).  The captured callback is
  never executed, so nothing registers a plan and no Worker/COMSOL/model work
  happens.  The submit count therefore measures scheduler admission calls only,
  never real Worker dispatch.

Plan data is a self-contained synthetic v1 declaration shaped exactly after the
already-validated shape in tests/test_stage_definition.py::``_plan`` (read, not
imported, to avoid that module's heavy imports and fixtures); it is checked here
by the real ``validate_call``/``validate_stage_plan_definition`` anyway.

NOTE ON EXPECTATION: ``test_in_flight_same_key_replay_reuses_job_without_resubmit``
encodes the REQUIRED invariant and is expected to FAIL against the current
unfixed product at _control_daemon.py:2699-2701 (the fall-through causes a
second captured scheduler.submit).  The control tests (different key, same-key
conflict, completed replay, malformed definition) pass either way and fence the
defect's exact scope.  These tests were written, not executed, per task rules.
"""
from __future__ import annotations

import traceback
from concurrent.futures import TimeoutError as FutureTimeout

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

# Sentinel returned by the fake ``_execution_session_context``; the fake
# scheduler asserts it is the context it receives, proving the real route
# handed its session context to ``session_scheduler.submit``.
_SESSION_CONTEXT_SENTINEL = object()


def _stage_plan(plan_id="heat-cycle", first_id="preheat"):
    """Synthetic v1 stage plan mirroring the validated _plan shape.

    Provenance: tests/test_stage_definition.py::``_plan`` was read (lines
    66-111) and reproduced in spirit here so this file never imports that test
    module (its fixtures and heavy product imports must not run).
    """
    return {
        "plan_id": plan_id,
        "stages": [
            {
                "stage_id": first_id,
                "ordinal": 1,
                "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "solution": "sol1"},
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T_preheat",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"},
                "checks": [{
                    "kind": "continuity", "check_id": "temperature-boundary",
                    "source_variable": "T", "target_variable": "T_preheat", "unit": "K",
                    "tolerance": {"absolute": 0.1, "relative": 0.0},
                }],
            },
            {
                "stage_id": "cooldown",
                "ordinal": 2,
                "depends_on": [first_id],
                "study_target": {"segments": [{"collection": "study", "tag": "std2"}]},
                "source_selection": {"kind": "stage", "stage_id": first_id,
                                     "selection": {"dataset": "dset1", "solution": "sol1"}},
                "target_selection": {"dataset": "dset2", "solution": "sol2"},
                "variable_mappings": [{
                    "source_variable": "T_preheat", "target_variable": "T_cool",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "interpolate",
                }],
                "reference_state": {"strategy": "predecessor_stage", "source_stage_id": first_id},
                "checks": [{
                    "kind": "conservation", "check_id": "temperature-balance",
                    "quantity": "temperature",
                    "terms": [
                        {"side": "source", "variable": "T_preheat", "coefficient": 1.0},
                        {"side": "target", "variable": "T_cool", "coefficient": -1.0},
                    ],
                    "unit": "K", "tolerance": {"absolute": 0.2, "relative": 0.01},
                }],
            },
        ],
    }


def _execution_envelope(*, request_id, idempotency_key):
    """Execution envelope carrying the full managed identity (outer is
    authoritative, exactly as the production stage_define route expects)."""
    return {
        "project_id": "project-t31-offline",
        "session_id": "session-t31-offline",
        "model_ref": {
            "schema_version": 1,
            "session_id": "session-t31-offline",
            "server_instance_id": "server-t31-offline",
            "model_tag": "model-t31-offline",
            "generation": 1,
        },
        "expected_revision": 0,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
    }


class _InFlightFuture:
    """Future modelling an admitted-but-unfinished scheduler callback."""

    def result(self, timeout=None):
        raise FutureTimeout("t31 fake future: stage definition still in flight")


class _InFlightScheduler:
    """Fake ``session_scheduler``: captures submits, never runs callbacks.

    ``calls`` records every ``(context, callback)`` admission; the counter this
    test suite asserts on is a scheduler-callback count, NOT real Worker or
    engine dispatch.
    """

    def __init__(self):
        self.calls: list[tuple[object, object]] = []
        self.executed: list[object] = []  # stays empty by construction

    def submit(self, context, callback):
        self.calls.append((context, callback))
        return _InFlightFuture()


class _FakeProjectAuthority:
    """Inert timeout-cap passthrough (explicitly NOT real project authority)."""

    def apply_timeout_caps(self, project_id, timeouts):
        assert isinstance(project_id, str) and project_id
        return dict(timeouts)


class _StageDefineReceiver:
    """Inert receiver for the unbound ControlDaemon methods.

    Real product functions run on it via unbound-method binding; everything it
    supplies itself (authorization stubs, fake session context, fake scheduler)
    is test scaffolding and proves scheduling/idempotency behaviour only —
    never real authorization, session connectivity, or model writes.
    """

    _dispatch_experiment_stage_define = ControlDaemon._dispatch_experiment_stage_define
    _pending = ControlDaemon._pending
    _finish = ControlDaemon._finish
    _release_terminal_accounting = ControlDaemon._release_terminal_accounting
    _release_terminal_disk_reservation = ControlDaemon._release_terminal_disk_reservation
    _release_terminal_resource_reservations = ControlDaemon._release_terminal_resource_reservations
    _error = ControlDaemon._error
    _exception = ControlDaemon._exception
    # Re-wrap explicitly as a staticmethod: plain attribute access would hand
    # the receiver as a first positional argument and break the call signature.
    _timeouts = staticmethod(ControlDaemon._timeouts)

    def __init__(self):
        import threading

        self.lock = threading.RLock()
        # Real ledger, memory-only: no SQLite file is opened on disk.
        self.store = OperationStore(":memory:")
        self.session_scheduler = _InFlightScheduler()
        self.project_authority = _FakeProjectAuthority()
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.log_exceptions: list[str] = []

    # --- FAKE security/session surface (documented above) -----------------
    def _authorize_project_execution(self, operation, arguments, execution):
        # Deliberately inert: this harness does not exercise real project
        # authorization and must not be read as an authz proof.
        return None

    def _execution_session_context(self, execution):
        return _SESSION_CONTEXT_SENTINEL

    def _log_exception(self):
        self.log_exceptions.append(traceback.format_exc())


def _dispatch(receiver, *, definition, execution):
    """One admission through the REAL unbound _dispatch_admitted route."""
    return ControlDaemon._dispatch_admitted(receiver, {
        "operation": "experiment.stage_define",
        "arguments": {"definition": definition},
        "execution": dict(execution),
    })


def _operation_rows(store):
    return store.db.execute(
        "SELECT operation_id,request_id,idempotency_key,request_hash,operation,status,result"
        " FROM operations ORDER BY rowid"
    ).fetchall()


def _job_rows(store):
    return store.db.execute(
        "SELECT job_id,operation_id,status FROM jobs ORDER BY rowid"
    ).fetchall()


def _count(store, table):
    return store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _assert_queued_pending_envelope(response, *, request_id, job_id, operation_id, request_hash):
    assert response["success"] is True, response
    assert response["data"]["status"] == "QUEUED", response
    assert response["data"]["job_id"] == job_id
    assert response["data"]["rpc_wait_expired"] is True
    assert response["data"]["engine_dispatched"] is False
    assert response["data"]["stage_attempt"] is None
    assert response["execution"]["request_id"] == request_id
    assert response["execution"]["operation_id"] == operation_id
    assert response["execution"]["request_hash"] == request_hash
    assert response["execution"]["idempotency_key"]


def test_in_flight_same_key_replay_reuses_job_without_resubmit():
    """DEFECT-FENCING TEST — expected RED until _control_daemon.py:2699-2701 is fixed.

    First request must reach ``session_scheduler.submit`` exactly once and be
    reported as a queued pending job.  A replay with the SAME idempotency key
    and payload but a different request_id, while the first attempt is still
    in flight, must reuse the same durable operation/job and must NOT admit a
    second scheduler.submit — mirroring the generic reused branch at
    _control_daemon.py:1308-1309.
    """
    receiver = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler

    plan = _stage_plan()
    first = _dispatch(receiver, definition=plan,
                      execution=_execution_envelope(request_id="t31-req-a", idempotency_key="t31-key-1"))

    # The first request really arrived at the scheduler exactly once, with the
    # real route's session context and its uncaptured-nowhere register callback.
    assert len(scheduler.calls) == 1, scheduler.calls
    context, callback = scheduler.calls[0]
    assert context is _SESSION_CONTEXT_SENTINEL
    assert callable(callback) and getattr(callback, "__name__", "") == "register_definition"
    assert scheduler.executed == []

    rows = _operation_rows(store)
    jobs = _job_rows(store)
    assert len(rows) == 1 and rows[0]["operation"] == "experiment.stage_define"
    assert rows[0]["status"] == "QUEUED" and rows[0]["result"] is None
    assert len(jobs) == 1 and jobs[0]["status"] == "QUEUED"
    assert jobs[0]["operation_id"] == rows[0]["operation_id"]
    # Nothing was registered because the callback was captured, never executed.
    assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0

    operation_id, job_id, request_hash = rows[0]["operation_id"], jobs[0]["job_id"], rows[0]["request_hash"]
    _assert_queued_pending_envelope(
        first, request_id="t31-req-a", job_id=job_id, operation_id=operation_id,
        request_hash=request_hash,
    )

    # Replay: same key, same payload, different request_id, first still in
    # flight.  Required invariant (generic-route parity): no second submit.
    second = _dispatch(receiver, definition=plan,
                       execution=_execution_envelope(request_id="t31-req-b", idempotency_key="t31-key-1"))

    assert len(scheduler.calls) == 1, (
        "same idempotency key replay while in flight admitted a second "
        "scheduler.submit; _dispatch_experiment_stage_define fell through its "
        "reused branch at _control_daemon.py:2699-2701"
    )
    # Same durable identity is reused (real store evidence first).
    assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1
    assert second["execution"]["operation_id"] == operation_id
    assert second["execution"]["job_id"] == job_id
    assert second["execution"]["request_hash"] == request_hash
    # The durable record keeps the ORIGINAL request identity; the replay's new
    # request_id must not overwrite or shadow it.
    assert second["execution"]["request_id"] == "t31-req-a"
    assert second["data"]["job_id"] == job_id
    assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0
    assert receiver.log_exceptions == []


def test_different_idempotency_key_admits_a_new_request():
    """Positive control: key isolation is intact — a different key legitimately
    creates its own operation/job and exactly one new scheduler.submit."""
    receiver = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    plan = _stage_plan()

    first = _dispatch(receiver, definition=plan,
                      execution=_execution_envelope(request_id="t31-req-c", idempotency_key="t31-key-1"))
    assert first["success"] is True and len(scheduler.calls) == 1
    first_job_id = first["data"]["job_id"]

    second = _dispatch(receiver, definition=plan,
                       execution=_execution_envelope(request_id="t31-req-d", idempotency_key="t31-key-2"))
    assert len(scheduler.calls) == 2, "a distinct idempotency key must still admit work"
    assert second["success"] is True and second["data"]["status"] == "QUEUED"
    assert second["data"]["job_id"] != first_job_id
    assert len(_operation_rows(store)) == 2 and len(_job_rows(store)) == 2
    assert scheduler.executed == []


def test_same_key_different_payload_conflicts_without_submit():
    """Negative control: the real store's idempotency-hash guard rejects a
    payload change under one key before any scheduler admission."""
    receiver = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler

    _dispatch(receiver, definition=_stage_plan(),
              execution=_execution_envelope(request_id="t31-req-e", idempotency_key="t31-key-1"))
    assert len(scheduler.calls) == 1

    changed = _stage_plan()
    changed["stages"][0]["target_selection"]["solution"] = "sol-changed"
    conflict = _dispatch(receiver, definition=changed,
                         execution=_execution_envelope(request_id="t31-req-f", idempotency_key="t31-key-1"))
    assert conflict["success"] is False
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT", conflict
    assert len(scheduler.calls) == 1, "a conflicted replay must not submit again"
    assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1
    assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0


def test_completed_replay_returns_stored_result_without_submit():
    """Completed-replay control: once the durable operation carries a result
    (here sealed via the REAL OperationStore.finish API, a synthetic test-only
    result — NOT a real registration outcome), the reused branch at
    _control_daemon.py:2699-2700 replays it with no further admission."""
    receiver = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler

    first = _dispatch(receiver, definition=_stage_plan(),
                      execution=_execution_envelope(request_id="t31-req-g", idempotency_key="t31-key-1"))
    assert len(scheduler.calls) == 1
    operation_id = _operation_rows(store)[0]["operation_id"]

    synthetic_result = {
        "success": True,
        "data": {"plan_id": "heat-cycle", "registration": "TEST_ONLY_SEALED",
                 "worker_rpc_performed": False, "solve_started": False},
        "_t31_test_only_synthetic_result": True,
        "_t31_note": "sealed via real store.finish to model callback completion; not a real plan registration",
    }
    accepted, authoritative = store.finish(operation_id, status="SUCCEEDED", result=synthetic_result)
    assert accepted is True and authoritative == "SUCCEEDED"

    replay = _dispatch(receiver, definition=_stage_plan(),
                       execution=_execution_envelope(request_id="t31-req-h", idempotency_key="t31-key-1"))
    assert replay == synthetic_result
    assert len(scheduler.calls) == 1, "a completed replay must never re-enter the scheduler"
    assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1
    assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0
    assert scheduler.executed == []


def test_malformed_definition_is_rejected_before_durable_admission():
    """Input-validation control: the real validate_call/contract path fails a
    malformed plan before any operation row or scheduler admission exists."""
    receiver = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler

    response = _dispatch(receiver, definition={"plan_id": "1-invalid id!", "stages": []},
                         execution=_execution_envelope(request_id="t31-req-i", idempotency_key="t31-key-3"))
    assert response["success"] is False
    assert response["error"]["code"] == "INVALID_REQUEST", response
    assert len(scheduler.calls) == 0
    assert len(_operation_rows(store)) == 0 and len(_job_rows(store)) == 0
