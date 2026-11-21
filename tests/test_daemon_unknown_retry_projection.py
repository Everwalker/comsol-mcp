"""Outer UNKNOWN retry projection guard for ControlDaemon._execute (T13).

Synthetic in-process exercise of the real unbound ``ControlDaemon._execute``
with in-memory fakes. The UNKNOWN producer chain is the real
``ManagedBackend._dispatch_with_witness`` (static) driving the real
``ExecutionService.execute_legacy`` with an in-memory ``SessionLedger`` and a
synthetic snapshot adapter. No ``ControlDaemon`` instance, Worker, thread,
daemon process, real store, control home, model file, filesystem target,
network, credential, signal, JVM, native engine, discovery operation,
fallback path or cursor is instantiated, touched or used.
"""
from __future__ import annotations

import time
from copy import deepcopy
from types import MethodType, SimpleNamespace

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend


class _FakeStore:
    """Inert in-memory job-store hook for one _execute call."""

    def __init__(self):
        self.events = []
        self.status_updates = []

    def job(self, job_id):
        return None

    def unresolved_jobs(self):
        return []

    def update_job(self, job_id, status, metadata=None):
        self.status_updates.append((job_id, status))

    def add_event(self, job_id, name, meta):
        self.events.append((name, meta))


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RecordingFinish:
    """Fake daemon _finish: records the decided status, returns the result."""

    def __init__(self):
        self.calls = []

    def __call__(self, record, result, status):
        self.calls.append({"job_id": record["job_id"], "status": status, "result": result})
        return result


def _make_fake_self(backend):
    """In-memory stand-in for one ControlDaemon instance; never an instance."""
    fake = SimpleNamespace()
    fake.store = _FakeStore()
    fake.lock = _FakeLock()
    fake.running = {}
    fake._disk_resource_coordinator = None
    fake._memory_resource_coordinator = None
    fake.backend = backend
    fake.finish = _RecordingFinish()
    fake._finish = fake.finish
    fake._error = ControlDaemon._error
    fake._exception = ControlDaemon._exception
    fake.logged_exception = False

    def _no_file_log_exception():
        fake.logged_exception = True

    fake._log_exception = _no_file_log_exception
    fake._attach_confirmed_project_id = MethodType(ControlDaemon._attach_confirmed_project_id, fake)
    fake._release_terminal_resource_reservations = lambda job_id: {}
    fake._desktop_before_model_dispatch = lambda operation, execution, arguments: None
    return fake


def _run_execute(fake_self, operation="set_parameters", arguments=None, execution=None):
    record = {"job_id": "job-1", "operation_id": "op-1"}
    timeouts = {"queue_timeout_s": None}
    return ControlDaemon._execute(
        fake_self, record, operation,
        dict(arguments or {}),
        dict(execution or {"session_id": "synthetic-session"}),
        timeouts, time.monotonic())


class _SyntheticSnapshotAdapter:
    def __init__(self, order):
        self.order = order

    def model_snapshot(self, model_tag):
        self.order.append("snapshot")
        return {"model_tag": model_tag, "server_instance_id": "synthetic-server",
                "fingerprint": "synthetic-fingerprint", "external_event_counter": 0}


class _ServiceBackedBackend:
    """Fake daemon backend feeding the real dispatcher through the real service.

    The bound-model write route uses a real in-memory SessionLedger, a real
    ExecutionService.execute_legacy write ticket and the real static
    ManagedBackend._dispatch_with_witness; only the engine call is synthetic.
    """

    def __init__(self, engine_call):
        self.order = []
        self.events = []
        self.raised = []
        self.engine_call = engine_call
        self.ledger = SessionLedger("synthetic-session", "synthetic-server",
                                    server_ownership="mcp_managed")
        self.ref = self.ledger.bind_model("synthetic-model", ownership="mcp_owned",
                                          fingerprint="synthetic-fingerprint")

    def invoke(self, operation, arguments, execution, operation_id, worker_event):
        service = ExecutionService(self.ledger, _SyntheticSnapshotAdapter(self.order),
                                   project_root="/synthetic-project",
                                   on_state_change=self.events.append)

        def callback(_args):
            self.order.append("callback")
            return ManagedBackend._dispatch_with_witness(operation, self.engine_call,
                                                         effect="project_write")

        try:
            return service.execute_legacy(operation, callback, dict(arguments),
                                          model_ref=self.ref, expected_revision=0,
                                          request_id="synthetic-request")
        except ExecutionContractError as exc:
            self.raised.append(exc)
            raise


class _EnvelopeBackend:
    def __init__(self, envelope):
        self.envelope = envelope
        self.invoked = False

    def invoke(self, operation, arguments, execution, operation_id, worker_event):
        self.invoked = True
        return self.envelope


class _RaisingBackend:
    def __init__(self, exc):
        self.exc = exc
        self.raised = None

    def invoke(self, operation, arguments, execution, operation_id, worker_event):
        try:
            raise self.exc
        except ExecutionContractError as exc:
            self.raised = exc
            raise


def _stageless_unknown_raise():
    raise ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN", "synthetic uncertainty after dispatch", safe_retry=True)


def test_bound_service_stageless_unknown_cannot_publish_outer_retry_true():
    backend = _ServiceBackedBackend(_stageless_unknown_raise)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    # 1 source-reachable/callback: the real _dispatch_with_witness stamped and
    # re-raised the stage-less UNKNOWN from inside the real service callback.
    assert len(backend.raised) == 1
    raised = backend.raised[0]
    assert isinstance(raised, ExecutionContractError)
    assert raised.code == "EXECUTION_STATE_UNKNOWN"
    assert raised.details.get("cause_code") == "EXECUTION_STATE_UNKNOWN"
    assert raised.details.get("dispatch_stage") == "post_dispatch"
    assert isinstance(raised.details.get("witness"), dict)
    # 2 fake snapshot order: pre-snapshot then callback, no post-snapshot.
    assert backend.order == ["snapshot", "callback"]
    # 3 dirty in-memory ledger from the real write-ticket unknown finish.
    state = backend.ledger._state_for(backend.ref)
    assert state.dirty is True and state.fingerprint is None
    # 4 UNKNOWN finish status captured by the fake daemon _finish hook.
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "UNKNOWN"
    assert fake_self.finish.calls[0]["result"] is result
    # 5 the original exception object keeps its own declared retry claim.
    assert raised.safe_retry is True
    # 6 intended RED before the product patch: the declared outer error must
    # never advertise retry on a job already decided UNKNOWN.
    assert result["error"]["safe_retry"] is False


def test_bound_service_unknown_keeps_exception_evidence_in_outer_error():
    backend = _ServiceBackedBackend(_stageless_unknown_raise)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "UNKNOWN"
    raised = backend.raised[0]
    error = result["error"]
    assert error["code"] == "EXECUTION_STATE_UNKNOWN"
    assert error["message"] == "synthetic uncertainty after dispatch"
    assert error["details"]["cause_code"] == "EXECUTION_STATE_UNKNOWN"
    assert error["details"]["dispatch_stage"] == "post_dispatch"
    assert isinstance(error["details"]["witness"], dict)
    assert raised.details["cause_code"] == "EXECUTION_STATE_UNKNOWN"


def test_returned_unknown_with_declared_retry_true_is_pinned_false():
    envelope = {
        "success": False,
        "execution_state_unknown": True,
        "data": {
            "synthetic_observation": "retained",
            "domain_outcome": {"engine_error": {"code": "SYNTHETIC_ENGINE", "safe_retry": True}},
        },
        "error": {
            "code": "SYNTHETIC_UNCERTAIN", "message": "adapter ambiguity", "safe_retry": True,
            "details": {"engine_error": {"code": "SYNTHETIC_ENGINE", "safe_retry": True}},
        },
    }
    original_error = envelope["error"]
    original_error_before = deepcopy(original_error)
    backend = _EnvelopeBackend(envelope)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    # status/entrypoint proven before any retry-bit check
    assert backend.invoked is True
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "UNKNOWN"
    assert result["error"]["code"] == "SYNTHETIC_UNCERTAIN"
    assert result["error"]["message"] == "adapter ambiguity"
    # intended RED before the patch: an existing declared True survived
    assert result["error"]["safe_retry"] is False
    # post-patch guarantees: adapter input untouched, pinning was on a copy,
    # nested diagnostic records (details/domain_outcome engine_error) intact
    assert original_error == original_error_before
    assert original_error["safe_retry"] is True
    assert result["error"] is not original_error
    assert result["error"]["details"] is original_error["details"]
    assert result["error"]["details"]["engine_error"]["safe_retry"] is True
    assert result["data"]["domain_outcome"]["engine_error"]["safe_retry"] is True


def test_returned_code_only_unknown_with_retry_true_is_pinned_false():
    for code in ("EXECUTION_STATE_UNKNOWN", "UNKNOWN"):
        envelope = {"success": False, "data": {"synthetic_observation": "retained"},
                    "error": {"code": code, "message": "code-only uncertainty",
                              "safe_retry": True}}
        original_error = envelope["error"]
        backend = _EnvelopeBackend(envelope)
        fake_self = _make_fake_self(backend)
        result = _run_execute(fake_self)
        assert backend.invoked is True
        assert len(fake_self.finish.calls) == 1
        assert fake_self.finish.calls[0]["status"] == "UNKNOWN"
        assert result["error"]["code"] == code
        assert original_error["safe_retry"] is True
        assert result["error"]["safe_retry"] is False
        assert result["error"] is not original_error


def test_definite_engine_unresponsive_with_retry_true_stays_failed_true():
    exc = ExecutionContractError("ENGINE_UNRESPONSIVE", "engine socket did not answer",
                                 safe_retry=True)
    backend = _RaisingBackend(exc)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    assert backend.raised is exc
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "FAILED"
    assert exc.safe_retry is True
    assert result["error"]["code"] == "ENGINE_UNRESPONSIVE"
    assert result["error"]["safe_retry"] is True


def test_definite_validation_error_with_retry_true_stays_failed_true():
    exc = ExecutionContractError("VALIDATION_FAILED", "explicit refusal before dispatch",
                                 safe_retry=True, stage="validation")
    backend = _RaisingBackend(exc)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    assert backend.raised is exc
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "FAILED"
    assert exc.safe_retry is True
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert result["error"].get("stage") == "validation"
    assert result["error"]["safe_retry"] is True


def test_definite_returned_failure_with_retry_true_stays_failed_true():
    envelope = {"success": False, "data": {},
                "error": {"code": "SYNTHETIC_REFUSAL", "message": "known refusal",
                          "safe_retry": True}}
    backend = _EnvelopeBackend(envelope)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    assert backend.invoked is True
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "FAILED"
    assert result["error"]["code"] == "SYNTHETIC_REFUSAL"
    assert result["error"]["safe_retry"] is True


def test_healthy_returned_success_stays_succeeded():
    envelope = {"success": True, "data": {"synthetic_observation": "completed"}}
    backend = _EnvelopeBackend(envelope)
    fake_self = _make_fake_self(backend)
    result = _run_execute(fake_self)
    assert backend.invoked is True
    assert len(fake_self.finish.calls) == 1
    assert fake_self.finish.calls[0]["status"] == "SUCCEEDED"
    assert result["success"] is True
    assert result["data"]["synthetic_observation"] == "completed"
