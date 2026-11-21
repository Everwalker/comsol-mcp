"""T35: logger fault after synthetic backend fault inside ``ControlDaemon._execute``.

Test only — written, not executed, per task rules. The parent alone inspects
and runs this file under an established guarded runner; no RED/GREEN status is
claimed here.

Source basis: the frozen snapshot
``_state/t31-stage-queue-lifecycle-20261112/daemon_candidate6.py.txt``
(``_execute`` lines 12862-13100, ``_finish`` 13102-13132, ``_error``/
``_exception``/``_log_exception`` helpers 13681-13694; frozen SHA
e6aae5faac574c6328e06cecfd9077496ee5349fb0a49ad5e7993a92649f6367, not
recomputed here).

What is REAL (product code under test):
- ``ControlDaemon._execute`` is invoked unbound against a task-owned inert
  receiver, with the real ``ControlDaemon._finish`` bound to that receiver and
  the real ``_error``/``_exception`` helpers.  No ``ControlDaemon`` instance,
  daemon, real scheduler, W26 coordinator, engine, Worker, SSH or native path
  is constructed or invoked.
- A fresh real ``OperationStore`` on path ``":memory:"`` performs the only
  admission (``begin``), ``finish``, ``update_job``, ``add_event`` and the
  readback used by assertions (``get_operation``, ``job``, ``events``,
  ``unresolved_jobs``).  The fresh store holds only the admitted QUEUED job,
  which ``_execute`` excludes by job ID; ``_job_quiescence_proven`` /
  ``_unknown_job_conflicts_with_execution`` are never needed (they are
  deliberately absent from the receiver so an unexpected reach surfaces as an
  error).  The store's ``close()`` runs in every ``finally``.

What is FAKE (bounded scaffolding, no attestation value):
- ``InjectedBackendFailure`` and ``InjectedLoggerFailure`` are synthetic
  in-process exceptions.  This models a logger failure on top of a backend
  failure; it is NOT an observation of a real logger, disk, fsync or engine,
  and these tests do not prove that a real backend was dispatched or that
  logs/fsync are durable.
- ``_BackendFake`` exposes only ``invoke`` (this path has ``resume_context``
  None and ``execution`` with no ``project_id``, so ``resume_study_run`` and
  ``project_root_scope`` are never reached).
- ``_LoggerFake`` stands in for ``_log_exception``; ``_ReleaseSpy`` stands in
  for ``_release_terminal_resource_reservations`` and proves only call-order,
  never physical release.
- ``_attach_confirmed_project_id`` is reached by the frozen source (line
  13041) on the success path; a no-op recording fake is supplied because the
  brief's fake inventory does not list it and its real definition is outside
  the allowed read scope (deviation noted to the parent).

Proposed contract asserted by test 2: ``result['data']['diagnostics']`` with
``log_attempt='FAILED'``, ``cause_type=<logger exception type>`` and
``diagnostic_durable=False``.  This is a proposed diagnostic submapping, not
an existing public schema, and is not claimed as one.  Per the frozen text,
the preimage calls ``_log_exception()`` (line 12994 equivalent; frozen line
13094) before constructing UNKNOWN, so the escaping logger fault prevents
``_finish`` — the preimage is expected RED on that reading of the frozen
source only; no execution has occurred, so no RED/GREEN status is claimed.

Filesystem note: the SQLite ``":memory:"`` constructor still attempts the
store's no-op ``Path('.').mkdir`` (``_operation_store.py`` line 177), so
nothing here claims OS-wide no-filesystem-effects.  No tmp_path, no
environment mutation, no process creation, no socket, no test file writes.
"""
from __future__ import annotations

import threading
import time
from typing import Any, cast

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore


class InjectedBackendFailure(RuntimeError):
    """Synthetic in-process backend fault raised by the fake backend."""


class InjectedLoggerFailure(RuntimeError):
    """Synthetic in-process fault raised by the fake logger after recording."""


_NO_RESULT: Any = object()
"""Sentinel so an escaped synthetic fault is never compared against an
uninitialized return value."""


class _BackendFake:
    """Records ``invoke`` calls; either raises or returns a fixed envelope."""

    def __init__(self, *, raises: bool, result: dict[str, Any] | None = None) -> None:
        self.raises = raises
        self.result = result
        self.invocations: list[dict[str, Any]] = []

    def invoke(self, operation: str, arguments: dict[str, Any],
               execution: dict[str, Any], operation_id: str,
               worker_event: Any) -> dict[str, Any]:
        self.invocations.append({"operation": operation, "operation_id": operation_id})
        if self.raises:
            raise InjectedBackendFailure("synthetic backend execution failure")
        assert self.result is not None, "success-mode fake backend needs a result"
        # Fresh copy per call: _finish mutates the envelope in place.
        return dict(self.result)


class _LoggerFake:
    """Stands in for ``_log_exception``; optionally faults after recording."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.failure_witness = False
        self._fail = fail

    def _log_exception(self) -> None:
        # Record first: a call that faulted is still an attempted call.
        self.calls += 1
        if self._fail:
            self.failure_witness = True
            raise InjectedLoggerFailure("synthetic logger write failure")


class _ReleaseSpy:
    """Records release invocations; proves call-order only."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _release_terminal_resource_reservations(self, job_id: str) -> dict[str, Any]:
        self.calls.append(job_id)
        return {"state": "SYNTHETIC-SPY", "diagnostic_durable": False}


class _ExecuteReceiver:
    """Inert receiver exposing only what the frozen ``_execute``/``_finish``
    path reads from ``self`` on a no-coordinator, no-project-identity call."""

    def __init__(self, *, backend: _BackendFake, logger: _LoggerFake) -> None:
        # Real ledger, memory-only fresh store.
        self.store = OperationStore(":memory:")
        self.lock = threading.RLock()
        self.running: dict[str, Any] = {}
        # No resource coordinators: _execute reads both attributes but skips
        # the whole admission block when both are None.
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.backend = backend
        self.logger = logger
        self.release_spy = _ReleaseSpy()
        self.desktop_dispatch_calls: list[dict[str, Any]] = []
        self.attach_calls: list[int] = []
        # Real error helpers bound appropriately: _error is a staticmethod
        # (plain function via class access), _exception a classmethod bound
        # to ControlDaemon; neither is reached on these three paths but the
        # brief requires both.  _finish is the real function bound here.
        self._error = ControlDaemon._error
        self._exception = ControlDaemon._exception
        self._finish = ControlDaemon._finish.__get__(self)
        # Fakes for the remaining reached members.
        self._log_exception = logger._log_exception
        self._release_terminal_resource_reservations = (
            self.release_spy._release_terminal_resource_reservations
        )

    def _desktop_before_model_dispatch(self, operation: str,
                                       execution: dict[str, Any],
                                       arguments: dict[str, Any]) -> None:
        self.desktop_dispatch_calls.append({"operation": operation})

    def _attach_confirmed_project_id(self, result: dict[str, Any],
                                     execution: dict[str, Any]) -> None:
        # No-op fake (see module docstring deviation note); no project
        # identity exists on this path.
        self.attach_calls.append(id(result))


_SYNTH_TIMEOUTS = {"queue_timeout_s": 60.0, "execution_timeout_s": 300.0}
_SYNTH_ARGUMENTS = {"synthetic_argument": "t35-logger-fault-unknown"}
# No project_id: _execute must take the nullcontext dispatch path and never
# touch project_authority/_authorize_project_execution.
_SYNTH_EXECUTION = {"execution_mode": "synthetic-no-project"}


def _admitted(receiver: _ExecuteReceiver, *, request_id: str,
              idempotency_key: str) -> dict[str, Any]:
    """Open one fresh QUEUED operation/job pair through the real store.begin."""
    record, reused = receiver.store.begin(
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_hash="t35-log-synthetic-request-hash",
        operation="study.run",
        metadata={"synthetic_marker": "t35-logger-fault-unknown"},
        timeouts=dict(_SYNTH_TIMEOUTS),
    )
    assert reused is False, record
    assert record["job_id"], record
    # The just-admitted QUEUED job is unresolved, but _execute excludes its
    # own job ID before applying the UNKNOWN/conflict checks.
    unresolved = receiver.store.unresolved_jobs()
    assert len(unresolved) == 1 and unresolved[0]["job_id"] == record["job_id"], unresolved
    return record


def _invoke_execute(receiver: _ExecuteReceiver, record: dict[str, Any]) -> dict[str, Any]:
    """Invoke the real unbound _execute on the inert receiver."""
    return ControlDaemon._execute(
        cast(ControlDaemon, receiver),
        record,
        "study.run",
        dict(_SYNTH_ARGUMENTS),
        dict(_SYNTH_EXECUTION),
        dict(_SYNTH_TIMEOUTS),
        time.monotonic(),
    )


def test_execute_backend_failure_logger_ok_stores_unknown_without_release():
    """Baseline positive control: backend fault + working logger.

    The generic ``except Exception`` branch (frozen lines 13093-13096) logs,
    builds the UNKNOWN envelope via real ``_error`` with the backend type in
    ``error.type``, pops the running entry, and the real bound ``_finish``
    persists UNKNOWN.  The generic baseline adds no extra top-level
    ``execution_state_unknown`` field, so none is required either way here.
    """
    backend = _BackendFake(raises=True)
    logger = _LoggerFake()
    receiver = _ExecuteReceiver(backend=backend, logger=logger)
    try:
        record = _admitted(receiver, request_id="t35-log-base-req",
                           idempotency_key="t35-log-base-key")

        returned = _invoke_execute(receiver, record)

        # Injected branch was reached exactly once (test fails if the fake
        # backend was never invoked).
        assert len(backend.invocations) == 1, backend.invocations
        assert backend.invocations[0]["operation_id"] == record["operation_id"]
        assert len(receiver.desktop_dispatch_calls) == 1, receiver.desktop_dispatch_calls
        assert logger.calls == 1, logger.calls
        assert logger.failure_witness is False, logger.failure_witness

        assert returned["success"] is False, returned
        assert returned["error"]["code"] == "EXECUTION_STATE_UNKNOWN", returned["error"]
        assert returned["error"]["safe_retry"] is False, returned["error"]
        assert returned["error"]["type"] == "InjectedBackendFailure", returned["error"]

        operation = receiver.store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "UNKNOWN", operation["status"]
        assert operation["result"] == returned, operation["result"]
        # UNKNOWN is a durable nonterminal observation: finished_at stays unset.
        assert operation["finished_at"] is None, operation["finished_at"]

        job = receiver.store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "UNKNOWN", job["status"]

        events = receiver.store.events(record["job_id"])
        assert [event["event"] for event in events] == ["RUNNING", "UNKNOWN"], events

        # UNKNOWN is not terminal: no fake release on this path.
        assert receiver.release_spy.calls == [], receiver.release_spy.calls
        # The finally block removed the running entry.
        assert receiver.running == {}, receiver.running
    finally:
        receiver.store.close()


def test_execute_backend_failure_logger_fault_stores_unknown_with_failed_log_diagnostics():
    """Proposed contract: a faulting logger must not block UNKNOWN persistence.

    Preimage is expected RED from the frozen source reading only (the escaping
    ``InjectedLoggerFailure`` at frozen line 13094 prevents ``_finish``); no
    execution has run here and no RED/GREEN status is claimed.  The
    ``data.diagnostics`` mapping is the proposed submapping from the brief, not
    an existing public schema.
    """
    backend = _BackendFake(raises=True)
    logger = _LoggerFake(fail=True)
    receiver = _ExecuteReceiver(backend=backend, logger=logger)
    try:
        record = _admitted(receiver, request_id="t35-log-fault-req",
                           idempotency_key="t35-log-fault-key")

        returned: Any = _NO_RESULT
        leaked: BaseException | None = None
        try:
            returned = _invoke_execute(receiver, record)
        except InjectedLoggerFailure as exc:
            # Witness the escape without ever comparing an uninitialized
            # return below (the sentinel guards that).
            leaked = exc

        # Verify the target fault actually fired even on the RED preimage.
        assert len(backend.invocations) == 1, backend.invocations
        assert logger.calls == 1, logger.calls
        assert logger.failure_witness is True, logger.failure_witness
        assert receiver.running == {}, receiver.running
        assert receiver.release_spy.calls == [], receiver.release_spy.calls

        # Proposed contract: the synthetic logger fault must not leak.
        # On the unfixed preimage this assertion is the designed failure
        # point (expected RED per the frozen source, not yet executed).
        assert leaked is None, f"synthetic logger fault leaked from _execute: {leaked!r}"

        assert returned is not _NO_RESULT, returned
        assert returned["success"] is False, returned
        assert returned["error"]["code"] == "EXECUTION_STATE_UNKNOWN", returned["error"]
        assert returned["error"]["safe_retry"] is False, returned["error"]
        # Primary backend exception type preserved.
        assert returned["error"]["type"] == "InjectedBackendFailure", returned["error"]

        # Proposed diagnostic submapping (not an existing public schema).
        diagnostics = returned["data"]["diagnostics"]
        assert diagnostics["log_attempt"] == "FAILED", diagnostics
        assert diagnostics["cause_type"] == "InjectedLoggerFailure", diagnostics
        assert diagnostics["diagnostic_durable"] is False, diagnostics

        operation = receiver.store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "UNKNOWN", operation["status"]
        assert operation["result"] == returned, operation["result"]

        job = receiver.store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "UNKNOWN", job["status"]

    finally:
        receiver.store.close()


def test_execute_backend_success_skips_logger_and_records_one_release_call():
    """Normal control: ordinary success bypasses the logger and releases once.

    The release spy's single call proves call-order only, never a physical
    resource release.
    """
    normal = {"success": True, "data": {"marker": "synthetic-normal"}}
    backend = _BackendFake(raises=False, result=normal)
    logger = _LoggerFake()
    receiver = _ExecuteReceiver(backend=backend, logger=logger)
    try:
        record = _admitted(receiver, request_id="t35-normal-req",
                           idempotency_key="t35-normal-key")

        returned = _invoke_execute(receiver, record)

        # Injected success branch was reached exactly once.
        assert len(backend.invocations) == 1, backend.invocations
        assert backend.invocations[0]["operation_id"] == record["operation_id"]
        assert len(receiver.desktop_dispatch_calls) == 1, receiver.desktop_dispatch_calls
        assert len(receiver.attach_calls) == 1, receiver.attach_calls

        # The fake logger must not be touched on the success path.
        assert logger.calls == 0, logger.calls
        assert logger.failure_witness is False, logger.failure_witness

        assert returned["success"] is True, returned
        assert returned["data"]["marker"] == "synthetic-normal", returned["data"]
        assert "error" not in returned, returned
        assert returned["execution"]["job_id"] == record["job_id"], returned["execution"]

        operation = receiver.store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "SUCCEEDED", operation["status"]
        assert operation["result"] == returned, operation["result"]
        assert operation["finished_at"] is not None, operation["finished_at"]

        job = receiver.store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "SUCCEEDED", job["status"]

        events = receiver.store.events(record["job_id"])
        assert [event["event"] for event in events] == ["RUNNING", "SUCCEEDED"], events

        # SUCCEEDED is terminal: exactly one recorded release call.
        assert receiver.release_spy.calls == [record["job_id"]], receiver.release_spy.calls
        assert receiver.running == {}, receiver.running
    finally:
        receiver.store.close()
