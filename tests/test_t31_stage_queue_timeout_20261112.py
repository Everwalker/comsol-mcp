"""T31 (20261112): queue-timeout expiry fence for ``experiment.stage_define``
and ``experiment.stage_run`` callback admission.

Question under test (source-observed, not fixed here): both stage routes put
``queue_timeout_s`` only into the identity digest and the durable operation
record, then hand their callback (``register_definition`` /
``preflight_and_record``) straight to the session scheduler.  Neither callback
nor the scheduler admission path performs a generic "am I already too old?"
check before doing model-touching work.

Source anchors in ``comsol_mcp/_control_daemon.py`` (current candidate):
  * stage_define route 2602-2841:
      - 2668 ``self._timeouts(normalized_execution)``
      - 2675 ``project_authority.apply_timeout_caps``
      - 2676-2682 ``canonical_request_hash(..., queue_timeout_s=...)`` (identity)
      - 2694-2702 ``store.begin(..., timeouts=timeouts)`` (durable storage)
      - 2712-2814 ``register_definition`` — NO deadline/expiry check anywhere
      - 2816-2833 ``self.session_scheduler.submit(context, register_definition)``
      - 2834-2840 ``future.result(timeout=timeouts["rpc_timeout_s"])``
  * stage_run route 2842-3247:
      - 2910 timeouts, 2913-2918 digest with queue_timeout_s, 2952-2956
        ``store.begin(..., timeouts=timeouts)``
      - 2967-3227 ``preflight_and_record`` — NO deadline/expiry check; it goes
        ledger/revision gates -> ``begin_stage_attempt`` (3008) -> backend
        ``stage_native_admission`` (3087-3094)
      - 3229-3238 ``self.session_scheduler.submit(context, preflight_and_record)``
      - 3239-3247 bounded ``future.result``
  * ``comsol_mcp/_session_context.py`` 524-617: ``submit`` records the task as
    ``{"state": "QUEUED", ...}`` (568-579) with no admission timestamp or
    deadline; ``admit_and_invoke`` (586-599) only rechecks fence state.
  * ``comsol_mcp/_operation_store.py`` 21: ``TERMINAL`` already includes
    ``"EXPIRED"``, and ``begin`` (404-453) persists ``effective_timeouts``.

Required contract encoded by the two RED tests (QUEUE_TIMEOUT / EXPIRED):
  * executing the captured scheduler callback strictly after the effective
    queue timeout must end the durable operation as ``EXPIRED`` with an error
    code ``QUEUE_TIMEOUT`` (the code name is the contract asserted here; no
    such constant exists yet in the permitted source windows),
  * and must perform NO plan registration, NO new stage attempt, and NO fake
    backend call.
NOTE: ``QUEUE_TIMEOUT`` as an error code is an encoded requirement, not an
observed product constant; it is chosen alongside the existing durable
``EXPIRED`` terminal status.

Clock control (test-only, bounded):
  * The ONLY clock intervention is replacing the module attribute
    ``comsol_mcp._control_daemon.time`` with a proxy that returns a manually
    advanced value for ``monotonic``/``perf_counter`` and delegates every
    other attribute (including ``time`` and ``sleep``) to the real time
    module.  The global ``time`` module is never patched, ``sleep`` is never
    altered, and the original module attribute is restored in ``finally``.
  * Admission (the route call) runs with the fake clock frozen at
    ``_CLOCK_START``; the captured callback is executed only after the test
    advances the fake clock past the effective queue timeout that the REAL
    store persisted in ``operations.effective_timeouts``.  That ordering is
    asserted (enqueue evidence and QUEUED rows first, then the advance), so
    the tests prove "enqueued first, expired second", not a fixture-time race.

What is REAL here (product code actually executed):
  * ``ControlDaemon._dispatch_admitted`` plus the whole
    ``_dispatch_experiment_stage_define`` / ``_dispatch_experiment_stage_run``
    routes, including the real ``validate_call``/``canonical_request_hash``
    gates, the real durable ``OperationStore(":memory:")`` (no DB file on
    disk) doing ``begin`` (with effective timeout persistence),
    ``register_stage_plan``, ``begin_stage_attempt``, ``update_stage_attempt``,
    ``finish``, ``put_metadata``, ``resolve_stage``, the real
    ``validate_native_admission`` gate and real ``_finish``/``_error``/
    ``_exception``/``_pending``/``_timeouts`` helpers bound to an inert
    receiver.
What is FAKE (bounded scaffolding, never an engine/authorization claim):
  * No ``ControlDaemon`` instance, no Worker, no COMSOL, no SSH, no threads,
  no network, no historic job is ever touched.  A fake-backend call counts
  prove code reachability to the backend seam ONLY — they are NOT evidence of
  any Worker RPC or native execution.
  * ``_authorize_project_execution`` and ``project_authority.apply_timeout_caps``
    are inert pass-throughs: this is NOT an authorization or timeout-policy
    proof (the effective timeout is therefore read back from the durable row
    instead of assumed).
  * The fake scheduler captures ``(context, callback)`` and returns a future
    whose ``result()`` immediately raises ``concurrent.futures.TimeoutError``
    (the same ``FutureTimeout`` the routes catch at 2836/3245), so the route
    returns its QUEUED pending envelope without ever executing the callback.
    The test then executes the captured callback itself, manually, on the
    fake clock.
  * The stage-run ACCEPTED predecessor row is seeded through the store's own
    handle with an explicit synthetic marker; it claims no native result and
    no scientific acceptance.
  * The fake native admission is a structurally-formed but explicitly
    NOT_VERIFIED mapping; it exists so the positive control reaches the
    backend seam and stops there durably.

Independence: this file shares no code with
``tests/test_t31_stage_queue_cancel_20261112.py`` (different author) and does
not read or import it.

EXPECTATIONS (this file was WRITTEN, NOT EXECUTED, per task rules):
  * ``test_stage_define_positive_control_registers_before_deadline`` and
    ``test_stage_run_positive_control_reaches_fake_backend_before_deadline``
    are expected GREEN against the current candidate: with the clock advanced
    less than the effective queue timeout, the captured callback really does
    register the plan (define) / create the durable attempt and call the fake
    backend admission (run).  These controls fence the fixture's completeness,
    so a RED timeout failure cannot be re-read as an early fixture rejection.
  * ``test_stage_define_callback_after_queue_timeout_must_expire`` and
    ``test_stage_run_callback_after_queue_timeout_must_expire`` are expected
    RED against the current candidate (the callbacks have no expiry branch, so
    they complete registration/attempts despite the advanced clock).  The
    first failing assertion of each RED test is the defect statement.
  * Uncertainty (documented, not guessed): ``ControlDaemon._timeouts`` parsing/
    clamping was not in the permitted reading window, so the advance amount is
    derived from the durable ``effective_timeouts`` readback, not from the
    requested ``queue_timeout_s``; if the product never reads
    ``monotonic``/``perf_counter`` from ``_control_daemon.time`` the clock
    simply stays unused and the required-behaviour assertions still fail as
    RED (that is the intended failure signal, not an error).  If the module
    exposes no ``time`` attribute at all, the clock patch raises a clearly
    labelled FIXTURE PRECONDITION error in both the RED and the positive
    control of the same scenario, so it is distinguishable from behavioural
    RED.
"""
from __future__ import annotations

import contextlib
import json
import threading
import traceback
from concurrent.futures import TimeoutError as FutureTimeout
from types import SimpleNamespace
from typing import Any

from comsol_mcp import _control_daemon as control_daemon_module
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import model_ref_from_mapping
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._stage_contract import (
    canonical_json,
    sha256_json,
    validate_stage_plan_definition,
)
from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

_CLOCK_START = 1_000_000.0
_REQUESTED_QUEUE_TIMEOUT_S = 30.0
_EXPIRY_MARGIN_S = 7.0

# ---------------------------------------------------------------------------
# Fake clock: ONLY the ``time`` attribute of the _control_daemon module is
# replaced; everything not overridden delegates to the real time module.
# ---------------------------------------------------------------------------

class _FakeTimeModule:
    """Proxy for ``_control_daemon.time`` with a manually advanced clock.

    ``monotonic``/``perf_counter`` return the controllable fake value (the
    only sane clocks for queue-deadline maths); ``time``, ``sleep`` and every
    other attribute delegate to the real module so unrelated product code
    (timestamps, waits) keeps working unchanged.  Call counters are evidence
    only; they are never asserted as behaviour.
    """

    def __init__(self, real_time_module: Any, start: float) -> None:
        # Set the delegation target FIRST: __getattr__ consults _real.
        self._real = real_time_module
        self._now = float(start)
        self.monotonic_calls = 0
        self.perf_counter_calls = 0

    def monotonic(self) -> float:
        self.monotonic_calls += 1
        return self._now

    def perf_counter(self) -> float:
        self.perf_counter_calls += 1
        return self._now

    @property
    def now(self) -> float:
        return self._now

    def advance(self, delta: float) -> float:
        self._now += float(delta)
        return self._now

    def __getattr__(self, name: str) -> Any:
        # time(), sleep() and everything else delegate to the real time
        # module — sleep semantics are deliberately NOT changed.
        return getattr(self._real, name)


@contextlib.contextmanager
def _fake_control_daemon_clock(start: float):
    """Patch only ``comsol_mcp._control_daemon.time``; restore in ``finally``.

    The global ``time`` module is never touched.
    """
    real_time = getattr(control_daemon_module, "time", None)
    if real_time is None or not hasattr(real_time, "monotonic"):
        raise AssertionError(
            "FIXTURE PRECONDITION: comsol_mcp._control_daemon must expose a "
            "'time' module attribute for the bounded clock patch; this is a "
            "harness precondition error, not a behavioural RED."
        )
    proxy = _FakeTimeModule(real_time, start)
    control_daemon_module.time = proxy
    try:
        yield proxy
    finally:
        control_daemon_module.time = real_time


# ---------------------------------------------------------------------------
# Capturing scheduler / inert authority (shared by both scenarios)
# ---------------------------------------------------------------------------

class _NeverCompletingFuture:
    """Future for an admitted-but-never-executed scheduler callback."""

    def result(self, timeout: Any = None) -> Any:
        raise FutureTimeout(
            "t31 queue-timeout fake future: callback captured, never executed "
            "by this fake scheduler"
        )


class _CapturingScheduler:
    """Fake ``session_scheduler``: records submits, never runs callbacks.

    ``calls`` counts scheduler admissions only — never real Worker or engine
    dispatch.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self.executed: list[Any] = []  # stays empty by construction

    def submit(self, context: Any, callback: Any) -> _NeverCompletingFuture:
        self.calls.append((context, callback))
        return _NeverCompletingFuture()


class _FakeProjectAuthority:
    """Inert timeout-cap passthrough (explicitly NOT real project authority).

    Because the cap is inert, the effective queue timeout used by the tests
    is read back from the durable operation row instead of assumed.
    """

    def apply_timeout_caps(self, project_id: str, timeouts: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(project_id, str) and project_id
        return dict(timeouts)


# ---------------------------------------------------------------------------
# Shared durable helpers
# ---------------------------------------------------------------------------

def _operation_row(store: OperationStore, operation: str) -> Any:
    row = store.db.execute(
        "SELECT * FROM operations WHERE operation=? ORDER BY rowid", (operation,)
    ).fetchone()
    assert row is not None, f"no durable {operation} operation row was created"
    return row


def _effective_queue_timeout(row: Any) -> float:
    """Ground-truth effective queue timeout persisted by the REAL store.

    Today this already passes: the routes DO persist queue_timeout_s into
    ``effective_timeouts`` (the observed defect is that nothing later reads a
    deadline derived from it).  If the durable row had no positive value, that
    is a labelled fixture precondition error, not behavioural RED.
    """
    timeouts = json.loads(row["effective_timeouts"] or "{}")
    value = timeouts.get("queue_timeout_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise AssertionError(
            "FIXTURE PRECONDITION: the durable operation record must persist a "
            f"positive effective queue_timeout_s; observed {timeouts!r}"
        )
    return float(value)


def _assert_pending_queued(response: dict[str, Any]) -> None:
    assert response["success"] is True, response
    assert response["data"]["status"] == "QUEUED", response
    assert response["data"]["rpc_wait_expired"] is True, response


def _assert_captured_callback(scheduler: _CapturingScheduler, name: str) -> Any:
    assert len(scheduler.calls) == 1, scheduler.calls
    assert scheduler.executed == [], "the fake scheduler must never execute a callback"
    _context, callback = scheduler.calls[0]
    assert callable(callback) and getattr(callback, "__name__", "") == name, (
        f"expected the real route to submit its '{name}' callback, got "
        f"{getattr(callback, '__name__', callback)!r}"
    )
    return callback


# ===========================================================================
# Scenario A — experiment.stage_define
# ===========================================================================

DEFINE_PROJECT = "project-t31-queue-timeout-define"
DEFINE_SESSION = "session-t31-queue-timeout-define"
DEFINE_PLAN_ID = "t31-queue-timeout-plan"
DEFINE_MODEL_REF = model_ref_from_mapping({
    "schema_version": 1,
    "session_id": DEFINE_SESSION,
    "server_instance_id": "server-t31-queue-timeout-define",
    "model_tag": "model-t31-queue-timeout-define",
    "generation": 1,
}).as_dict()


def _define_plan() -> dict[str, Any]:
    """Synthetic v1 declaration (declaration-only plan is sufficient here).

    Provenance: shape reproduced in spirit from the plan data in
    tests/test_t31_stage_define_pending_idempotency_20261111.py (read, never
    imported); validated by the real ``validate_stage_plan_definition`` inside
    the route anyway.
    """
    return {
        "plan_id": DEFINE_PLAN_ID,
        "stages": [
            {
                "stage_id": "preheat",
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
        ],
    }


def _define_execution(*, request_id: str, idempotency_key: str) -> dict[str, Any]:
    return {
        "project_id": DEFINE_PROJECT,
        "session_id": DEFINE_SESSION,
        "model_ref": dict(DEFINE_MODEL_REF),
        "expected_revision": 0,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
        # The request-side queue timeout; the advance amount used by the tests
        # comes from the durable effective_timeouts readback, not this value.
        "queue_timeout_s": _REQUESTED_QUEUE_TIMEOUT_S,
    }


class _DefineFakeLedger:
    session_id = DEFINE_SESSION

    def __init__(self) -> None:
        self.state_reads = 0

    def _state_for(self, model_ref: Any) -> SimpleNamespace:
        self.state_reads += 1
        assert model_ref is not None
        return SimpleNamespace(active_operation_id=None, dirty=False, revision=0)


class _DefineFakeBackend:
    """Fake managed backend seam; call counters prove reachability only."""

    def __init__(self) -> None:
        self.service = SimpleNamespace(ledger=_DefineFakeLedger())
        self.model_project_key_calls = 0

    def _model_project_key(self, observed: dict[str, Any]) -> str:
        assert observed == DEFINE_MODEL_REF
        self.model_project_key_calls += 1
        return "t31-queue-timeout-define-model-key"


class _StageDefineTimeoutReceiver:
    """Inert receiver for the unbound stage_define route (memory store only)."""

    _dispatch_experiment_stage_define = ControlDaemon._dispatch_experiment_stage_define
    _pending = ControlDaemon._pending
    _finish = ControlDaemon._finish
    _release_terminal_accounting = ControlDaemon._release_terminal_accounting
    _release_terminal_disk_reservation = ControlDaemon._release_terminal_disk_reservation
    _release_terminal_resource_reservations = ControlDaemon._release_terminal_resource_reservations
    _error = staticmethod(ControlDaemon._error)
    _exception = ControlDaemon._exception
    # Re-wrap explicitly as a staticmethod: plain attribute access would hand
    # the receiver as a first positional argument and break the call signature.
    _timeouts = staticmethod(ControlDaemon._timeouts)

    def __init__(self) -> None:
        self.lock = threading.RLock()
        # Real ledger, memory-only: no SQLite file is opened on disk.
        self.store = OperationStore(":memory:")
        self.session_scheduler = _CapturingScheduler()
        self.project_authority = _FakeProjectAuthority()
        self._default_backend = _DefineFakeBackend()
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.log_exceptions: list[str] = []
        # Satisfy the durable revision-binding gate so the callback CAN fully
        # succeed on a non-expired clock (fixture completeness for the
        # positive control).
        self.store.put_metadata("revisions", "t31-queue-timeout-define-model-key", {
            "model_ref": dict(DEFINE_MODEL_REF), "project_id": DEFINE_PROJECT,
            "attribution": "PROJECT_BOUND", "revision": 0, "dirty": False,
        })

    # --- FAKE security/session surface (documented above) -----------------
    def _authorize_project_execution(self, operation: Any, arguments: Any, execution: Any) -> None:
        # Deliberately inert: NOT a project-policy/authorization proof.
        return None

    def _execution_session_context(self, execution: Any) -> None:
        return None  # route then falls back to self._default_backend

    def _log_exception(self) -> None:
        self.log_exceptions.append(traceback.format_exc())


def _dispatch_define(receiver: Any, *, definition: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    return ControlDaemon._dispatch_admitted(receiver, {
        "operation": "experiment.stage_define",
        "arguments": {"definition": definition},
        "execution": dict(execution),
    })


def test_stage_define_positive_control_registers_before_deadline() -> None:
    """GREEN control: inside the deadline the captured callback registers.

    The clock advances only to half the effective queue timeout before the
    callback runs, so any deadline-aware implementation must (and today's
    deadline-free one trivially does) complete the REAL registration.
    """
    receiver = _StageDefineTimeoutReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    try:
        with _fake_control_daemon_clock(_CLOCK_START) as clock:
            admission_clock = clock.now
            first = _dispatch_define(receiver, definition=_define_plan(),
                                     execution=_define_execution(request_id="t31-qt-define-pos-1",
                                                                 idempotency_key="t31-qt-define-pos-key"))
            # --- enqueued FIRST, on the frozen clock ----------------------
            register_definition = _assert_captured_callback(scheduler, "register_definition")
            assert scheduler.calls[0][0] is None  # route used the default-backend fallback
            assert clock.now == admission_clock, "the fake clock must stay frozen during admission"
            row = _operation_row(store, "experiment.stage_define")
            assert row["status"] == "QUEUED" and row["result"] is None, row
            queue_timeout = _effective_queue_timeout(row)
            assert receiver._default_backend.model_project_key_calls == 0
            assert receiver._default_backend.service.ledger.state_reads == 0

            # --- still well inside the deadline when the callback runs ----
            clock.advance(queue_timeout / 2.0)
            assert clock.now < admission_clock + queue_timeout

            result = register_definition()
            assert result["success"] is True, result
            assert result["data"]["registration"] == "CREATED"
            assert result["data"]["plan_id"] == DEFINE_PLAN_ID
            saved = store.get_stage_plan(DEFINE_PROJECT, DEFINE_MODEL_REF, DEFINE_PLAN_ID)
            assert saved is not None and saved["definition"]["plan_id"] == DEFINE_PLAN_ID
            job_id = first["execution"]["job_id"]
            sealed_job = store.job(job_id)
            assert sealed_job is not None, job_id
            assert sealed_job["status"] == "SUCCEEDED"
            assert sealed_job["result"] == result
            assert receiver._default_backend.model_project_key_calls == 1
            assert receiver.log_exceptions == []
    finally:
        store.close()


def test_stage_define_callback_after_queue_timeout_must_expire() -> None:
    """DEFECT-FENCING TEST — expected RED against the current candidate.

    Sequence actually executed: enqueue at the frozen clock (real store QUEUED
    row + captured ``register_definition``), advance the fake clock past the
    effective queue timeout persisted by the real store, then manually run the
    captured callback.  Required behaviour: EXPIRED + QUEUE_TIMEOUT, with no
    plan registration and no fake backend call.  Today the callback has no
    expiry branch, so it registers anyway — the first failing assertion is the
    defect statement.
    """
    receiver = _StageDefineTimeoutReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    try:
        with _fake_control_daemon_clock(_CLOCK_START) as clock:
            admission_clock = clock.now
            response = _dispatch_define(receiver, definition=_define_plan(),
                                        execution=_define_execution(request_id="t31-qt-define-exp-1",
                                                                    idempotency_key="t31-qt-define-exp-key"))
            # --- enqueued FIRST on the frozen clock; timeout only persisted
            # into identity + durable storage at admission -----------------
            register_definition = _assert_captured_callback(scheduler, "register_definition")
            assert clock.now == admission_clock, "the fake clock must stay frozen during admission"
            row = _operation_row(store, "experiment.stage_define")
            assert row["status"] == "QUEUED" and row["result"] is None, row
            queue_timeout = _effective_queue_timeout(row)

            # --- expired SECOND (strict admission/callback clock control) --
            callback_clock = clock.advance(queue_timeout + _EXPIRY_MARGIN_S)
            assert callback_clock >= admission_clock + queue_timeout

            result = register_definition()

            # --- required contract (RED today: no expiry branch exists) ---
            assert result["success"] is False, (
                "DEFECT: the stage_define scheduler callback completed "
                "registration although it was executed strictly after the "
                "effective queue timeout expired (clock went "
                f"{admission_clock} -> {callback_clock} vs effective "
                f"queue_timeout_s={queue_timeout}; monotonic reads="
                f"{clock.monotonic_calls}, perf_counter reads="
                f"{clock.perf_counter_calls}). No generic expiry check exists "
                "in _dispatch_experiment_stage_define's register_definition "
                "(_control_daemon.py:2712-2814) nor in scheduler admission "
                "(_session_context.py:568-599); queue_timeout_s only reaches "
                "the identity digest and effective_timeouts."
            )
            assert result.get("error", {}).get("code") == "QUEUE_TIMEOUT", result
            expired_row = _operation_row(store, "experiment.stage_define")
            assert expired_row["status"] == "EXPIRED", expired_row["status"]
            assert json.loads(expired_row["result"]) == result, "the expired result must be the durable one"
            # --- no plan registration, no fake backend call ---------------
            assert store.get_stage_plan(DEFINE_PROJECT, DEFINE_MODEL_REF, DEFINE_PLAN_ID) is None, (
                "DEFECT: an expired stage_define callback still registered the plan"
            )
            assert store.get_stage_id_index(DEFINE_PROJECT, DEFINE_MODEL_REF, "preheat") is None
            assert receiver._default_backend.model_project_key_calls == 0, (
                "DEFECT: an expired stage_define callback still reached the backend seam"
            )
            assert store.db.execute("SELECT COUNT(*) FROM stage_attempts").fetchone()[0] == 0
            assert receiver.log_exceptions == [], receiver.log_exceptions
    finally:
        store.close()


# ===========================================================================
# Scenario B — experiment.stage_run
# ===========================================================================

RUN_PROJECT = "project-t31-queue-timeout-run"
RUN_SESSION = "session-t31-queue-timeout-run"
RUN_PLAN_ID = "t31-queue-timeout-run-plan"
RUN_STAGE_ID = "cooldown"
RUN_PREDECESSOR_STAGE_ID = "preheat"
RUN_MODEL_REF = model_ref_from_mapping({
    "schema_version": 1,
    "session_id": RUN_SESSION,
    "server_instance_id": "server-t31-queue-timeout-run",
    "model_tag": "model-t31-queue-timeout-run",
    "generation": 1,
}).as_dict()


def _run_plan_definition() -> dict[str, Any]:
    """Synthetic v2 declaration; the run stage is an ordinary successor stage.

    Provenance: shape reproduced in spirit from the v2 plan in
    tests/test_t31_stage_run_nested_unknown_20261111.py (read, never
    imported); normalised by the real ``validate_stage_plan_definition``.
    """
    return {
        "version": 2,
        "plan_id": RUN_PLAN_ID,
        "stages": [
            {
                "stage_id": RUN_PREDECESSOR_STAGE_ID,
                "ordinal": 1,
                "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "solution": "sol1"},
                "mapping_profile": "same_name_same_mesh_initialization",
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T", "source_unit": "K",
                    "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"},
                "checks": [],
            },
            {
                "stage_id": RUN_STAGE_ID,
                "ordinal": 2,
                "depends_on": [RUN_PREDECESSOR_STAGE_ID],
                "study_target": {"segments": [{"collection": "study", "tag": "std2"}]},
                "source_selection": {
                    "kind": "stage", "stage_id": RUN_PREDECESSOR_STAGE_ID,
                    "selection": {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [2]},
                },
                "target_selection": {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]},
                "target_variables": {"segments": [
                    {"collection": "sol", "tag": "sol2"},
                    {"collection": "feature", "tag": "v2"},
                ]},
                "mapping_profile": "same_name_same_mesh_initialization",
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T", "source_unit": "K",
                    "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "predecessor_stage",
                                    "source_stage_id": RUN_PREDECESSOR_STAGE_ID},
                "checks": [],
            },
        ],
    }


def _register_run_plan(store: OperationStore) -> dict[str, Any]:
    """Register the plan through the real durable registration API."""
    definition = validate_stage_plan_definition(_run_plan_definition())
    record = {
        "schema_version": 1,
        "kind": "w21_stage_plan",
        "project_id": RUN_PROJECT,
        "model_ref": dict(RUN_MODEL_REF),
        "plan_id": definition["plan_id"],
        "definition": definition,
        "definition_sha256": sha256_json(definition),
        "declaration_revision": 0,
        "declaration_status": "DECLARED_UNVERIFIED",
        "declaration_evidence": {
            "fixture": "T31-QUEUE-TIMEOUT-OFFLINE-TEST-ONLY-PLAN-REGISTRATION",
            "native_validation": False,
            "scientific_acceptance_claim": False,
        },
    }
    record["sha256"] = sha256_json(record)
    stored, created = store.register_stage_plan(
        project_id=RUN_PROJECT, model_ref=dict(RUN_MODEL_REF), plan_record=record,
    )
    assert created is True and stored["plan_id"] == RUN_PLAN_ID
    return stored


def _seed_test_only_accepted_predecessor(store: OperationStore) -> dict[str, Any]:
    """Give the successor stage its required ACCEPTED predecessor.

    The generic attempt CAS forbids ACCEPTED, so this mirrors the read-only
    observed pattern in tests/test_t31_stage_run_nested_unknown_20261111.py:
    begin through the real API, then rewrite via the store's own connection.
    The row is visibly synthetic and claims no native result and no scientific
    acceptance.
    """
    record, reused = store.begin_stage_attempt(
        project_id=RUN_PROJECT, model_ref=dict(RUN_MODEL_REF), stage_id=RUN_PREDECESSOR_STAGE_ID,
        expected_revision=0, request_id="t31-qt-test-only-predecessor-request",
        operation_id="t31-qt-test-only-predecessor-operation",
        idempotency_key="t31-qt-test-only-predecessor-idempotency", request_hash="a" * 64,
    )
    assert reused is False
    synthetic = dict(record)
    synthetic.pop("sha256", None)
    synthetic.update({
        "status": "ACCEPTED", "version": record["version"] + 1,
        "engine_dispatched": True, "execution_status": "SOLVE_SUCCEEDED",
        "acceptance_status": "ACCEPTED",
        "evidence": [{"kind": "test_only_synthetic_predecessor", "synthetic": True,
                      "native_result": False, "scientific_acceptance_claim": False}],
        "result": {"fixture_only": True, "native_acceptance_claimed": False},
    })
    synthetic["sha256"] = sha256_json(synthetic)
    store.db.execute(
        "UPDATE stage_attempts SET status=?,version=?,record_json=? WHERE attempt_id=?",
        (synthetic["status"], synthetic["version"], canonical_json(synthetic),
         synthetic["attempt_id"]),
    )
    assert store.get_stage_attempt(RUN_PROJECT, RUN_MODEL_REF, record["attempt_id"]) == synthetic
    return synthetic


class _RunFakeLedger:
    session_id = RUN_SESSION

    def __init__(self) -> None:
        self.state_reads = 0

    def _state_for(self, model_ref: Any) -> SimpleNamespace:
        self.state_reads += 1
        assert model_ref is not None
        return SimpleNamespace(active_operation_id=None, dirty=False, revision=0)


class _RunFakeBackend:
    """Fake managed backend seam: records the native-admission call only.

    Reaching this seam proves code reachability; it is NOT a Worker RPC or
    native execution.  The returned admission is a structurally-formed but
    NOT_VERIFIED mapping, so the real route durably records
    NOT_DISPATCHED_UNVERIFIED instead of entering the solve executor.
    """

    worker = None  # no Worker object exists in this harness

    def __init__(self) -> None:
        self.service = SimpleNamespace(ledger=_RunFakeLedger())
        self.admission_calls: list[dict[str, Any]] = []

    @staticmethod
    def _model_project_key(model_ref: dict[str, Any]) -> str:
        return (f"t31-queue-timeout-model-key:{model_ref['model_tag']}"
                f":{model_ref['generation']}")

    def stage_native_admission(self, *, binding: Any, plan: Any, stage: Any) -> dict[str, Any]:
        # Successor (non-initial-state) stages hit the 3-kwarg provider form
        # at _control_daemon.py:3094.
        assert isinstance(binding, dict) and isinstance(plan, dict) and isinstance(stage, dict)
        self.admission_calls.append({"binding": dict(binding)})
        return {
            "contract": ADMISSION_CONTRACT,
            "producer": "TEST-ONLY-FAKE-BACKEND-UNVERIFIED-ADMISSION",
            "status": "NOT_VERIFIED",
            "binding": dict(binding),
            "facts": {},
            "evidence_refs": [],
        }


class _StageRunTimeoutReceiver:
    """Inert receiver for the unbound stage_run route (memory store only)."""

    _dispatch_experiment_stage_run = ControlDaemon._dispatch_experiment_stage_run
    _pending = ControlDaemon._pending
    _finish = ControlDaemon._finish
    _error = staticmethod(ControlDaemon._error)
    _exception = ControlDaemon._exception
    _release_terminal_accounting = ControlDaemon._release_terminal_accounting
    _release_terminal_disk_reservation = ControlDaemon._release_terminal_disk_reservation
    _release_terminal_resource_reservations = ControlDaemon._release_terminal_resource_reservations
    # Explicit staticmethod re-wrapping (same convention as the allowed T31
    # harnesses): plain attribute access would pass the receiver first.
    _timeouts = staticmethod(ControlDaemon._timeouts)
    _redact_session_worker_event = staticmethod(ControlDaemon._redact_session_worker_event)

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.store = OperationStore(":memory:")
        self.session_scheduler = _CapturingScheduler()
        self.project_authority = _FakeProjectAuthority()
        self.backend = _RunFakeBackend()
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.log_exceptions: list[str] = []
        # Fresh durable state so the callback CAN reach the fake backend on a
        # non-expired clock (fixture completeness for the positive control).
        _register_run_plan(self.store)
        self.store.put_metadata("revisions", self.backend._model_project_key(RUN_MODEL_REF), {
            "model_ref": dict(RUN_MODEL_REF), "project_id": RUN_PROJECT,
            "attribution": "PROJECT_BOUND", "revision": 0, "dirty": False,
        })
        self.predecessor = _seed_test_only_accepted_predecessor(self.store)

    # --- FAKE security/session surface (documented above) -----------------
    def _authorize_project_execution(self, operation: Any, arguments: Any, execution: Any) -> None:
        # Deliberately inert; not an authorization proof.
        return None

    def _execution_session_context(self, execution: Any) -> SimpleNamespace:
        return SimpleNamespace(
            backend=self.backend, service=self.backend.service,
            project_id=RUN_PROJECT, session_id=RUN_SESSION,
        )

    def _log_exception(self) -> None:
        self.log_exceptions.append(traceback.format_exc())


def _dispatch_stage_run(receiver: Any, *, request_id: str,
                        idempotency_key: str) -> dict[str, Any]:
    # ``receiver`` is an inert harness receiver bound via unbound methods; the
    # same Any convention as the allowed T31 harnesses (not a ControlDaemon).
    return ControlDaemon._dispatch_admitted(receiver, {
        "operation": "experiment.stage_run",
        "arguments": {"stage_id": RUN_STAGE_ID,
                      "source_attempt_id": receiver.predecessor["attempt_id"]},
        "execution": {
            "project_id": RUN_PROJECT, "session_id": RUN_SESSION,
            "model_ref": dict(RUN_MODEL_REF), "expected_revision": 0,
            "idempotency_key": idempotency_key, "request_id": request_id,
            # Request-side value; the test advance is calibrated from the
            # durable effective_timeouts readback below.
            "queue_timeout_s": _REQUESTED_QUEUE_TIMEOUT_S,
        },
    })


def _successor_attempt_rows(store: OperationStore) -> list[dict[str, Any]]:
    return store.list_stage_attempts(RUN_PROJECT, RUN_MODEL_REF, stage_id=RUN_STAGE_ID)


def test_stage_run_positive_control_reaches_fake_backend_before_deadline() -> None:
    """GREEN control: inside the deadline the captured callback reaches the
    fake backend seam and durably binds a stage attempt.

    The clock advances only to half the effective queue timeout, so the
    fixture's completeness is proven BEFORE any timeout expectation is read:
    the real preflight runs the ledger/revision/plan gates, creates the
    successor attempt, and calls the fake ``stage_native_admission`` exactly
    once.  (Reaching the fake backend proves code reachability only — it is
    not a Worker/COMSOL execution claim.)
    """
    receiver = _StageRunTimeoutReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    try:
        with _fake_control_daemon_clock(_CLOCK_START) as clock:
            admission_clock = clock.now
            response = _dispatch_stage_run(receiver, request_id="t31-qt-run-pos-1",
                                           idempotency_key="t31-qt-run-pos-key")
            # --- enqueued FIRST, on the frozen clock ----------------------
            preflight_and_record = _assert_captured_callback(scheduler, "preflight_and_record")
            assert scheduler.calls[0][0].backend is receiver.backend
            assert clock.now == admission_clock, "the fake clock must stay frozen during admission"
            row = _operation_row(store, "experiment.stage_run")
            assert row["status"] == "QUEUED" and row["result"] is None, row
            queue_timeout = _effective_queue_timeout(row)
            assert receiver.backend.admission_calls == []
            assert _successor_attempt_rows(store) == []

            # --- still inside the deadline when the callback runs ---------
            clock.advance(queue_timeout / 2.0)
            assert clock.now < admission_clock + queue_timeout

            result = preflight_and_record()

            # --- fake backend really reached, attempt really bound --------
            assert len(receiver.backend.admission_calls) == 1, receiver.backend.admission_calls
            binding = receiver.backend.admission_calls[0]["binding"]
            assert isinstance(binding, dict) and binding, receiver.backend.admission_calls
            attempts = _successor_attempt_rows(store)
            assert len(attempts) == 1, attempts
            assert attempts[0]["idempotency_key"] == "t31-qt-run-pos-key"
            # The preflight completed its durable write: the operation left
            # QUEUED.  (The exact failure code behind the NOT_VERIFIED fake
            # admission is intentionally not pinned here.)
            finished_row = _operation_row(store, "experiment.stage_run")
            assert finished_row["status"] != "QUEUED", finished_row["status"]
            assert finished_row["result"] is not None
            assert isinstance(result, dict) and result.get("success") is False
    finally:
        store.close()


def test_stage_run_callback_after_queue_timeout_must_expire() -> None:
    """DEFECT-FENCING TEST — expected RED against the current candidate.

    Same strict ordering as the define twin: enqueue on the frozen clock
    (QUEUED row + captured ``preflight_and_record``), advance past the durable
    effective queue timeout, then run the captured callback.  Required
    behaviour: EXPIRED + QUEUE_TIMEOUT, no new stage attempt, no fake backend
    call.  Today the preflight has no expiry branch, so it binds a durable
    attempt and reaches the backend seam anyway — the first failing assertion
    is the defect statement.
    """
    receiver = _StageRunTimeoutReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    try:
        with _fake_control_daemon_clock(_CLOCK_START) as clock:
            admission_clock = clock.now
            response = _dispatch_stage_run(receiver, request_id="t31-qt-run-exp-1",
                                           idempotency_key="t31-qt-run-exp-key")
            # --- enqueued FIRST on the frozen clock ------------------------
            preflight_and_record = _assert_captured_callback(scheduler, "preflight_and_record")
            assert clock.now == admission_clock, "the fake clock must stay frozen during admission"
            row = _operation_row(store, "experiment.stage_run")
            assert row["status"] == "QUEUED" and row["result"] is None, row
            queue_timeout = _effective_queue_timeout(row)
            attempts_at_admission = len(_successor_attempt_rows(store))
            assert attempts_at_admission == 0

            # --- expired SECOND -------------------------------------------
            callback_clock = clock.advance(queue_timeout + _EXPIRY_MARGIN_S)
            assert callback_clock >= admission_clock + queue_timeout

            result = preflight_and_record()

            # --- required contract (RED today: no expiry branch exists) ---
            assert result["success"] is False, (
                "DEFECT: the stage_run scheduler callback completed preflight "
                "and bound a stage attempt although it was executed strictly "
                "after the effective queue timeout expired (clock went "
                f"{admission_clock} -> {callback_clock} vs effective "
                f"queue_timeout_s={queue_timeout}; monotonic reads="
                f"{clock.monotonic_calls}, perf_counter reads="
                f"{clock.perf_counter_calls}). No generic expiry check exists "
                "in _dispatch_experiment_stage_run's preflight_and_record "
                "(_control_daemon.py:2967-3227) nor in scheduler admission "
                "(_session_context.py:568-599); queue_timeout_s only reaches "
                "the identity digest and effective_timeouts."
            )
            assert result.get("error", {}).get("code") == "QUEUE_TIMEOUT", result
            expired_row = _operation_row(store, "experiment.stage_run")
            assert expired_row["status"] == "EXPIRED", expired_row["status"]
            assert json.loads(expired_row["result"]) == result, "the expired result must be the durable one"
            # --- no new stage attempt, no fake backend call ---------------
            assert len(_successor_attempt_rows(store)) == attempts_at_admission, (
                "DEFECT: an expired stage_run callback still created a new "
                f"durable stage attempt (now {len(_successor_attempt_rows(store))})"
            )
            assert receiver.backend.admission_calls == [], (
                "DEFECT: an expired stage_run callback still reached the fake "
                f"backend admission seam: {receiver.backend.admission_calls!r}"
            )
            assert receiver.log_exceptions == [], receiver.log_exceptions
    finally:
        store.close()
