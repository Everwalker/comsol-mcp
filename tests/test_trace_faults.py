"""Portable, side-effect-free AST controls for T35 trace write failures."""
from __future__ import annotations

import ast
import copy
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).parents[1] / "comsol_mcp/_control_daemon.py"


class SyntheticExecutionContractError(Exception):
    def __init__(self, code, message, *, safe_retry=False, details=None):
        super().__init__(message)
        self.code = code
        self.safe_retry = safe_retry
        self.details = dict(details or {})

    def as_dict(self):
        return {"code": self.code, "message": str(self),
                "safe_retry": self.safe_retry,
                **({"details": dict(self.details)} if self.details else {})}


def extracted_method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    daemon = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "ControlDaemon")
    method = copy.deepcopy(next(node for node in daemon.body
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and node.name == name))
    wrapper = ast.Module(body=[ast.ClassDef(name="Harness", bases=[], keywords=[],
                                            body=[method], decorator_list=[])],
                         type_ignores=[])
    namespace = {"ExecutionContractError": SyntheticExecutionContractError,
                 "Mapping": __import__("collections.abc").abc.Mapping,
                 "sqlite3": sqlite3, "TERMINAL": {"SUCCEEDED", "FAILED", "CANCELLED",
                                                      "EXPIRED", "LOST"},
                 "time": time}
    exec(compile(ast.fix_missing_locations(wrapper), str(SOURCE), "exec"), namespace)
    return namespace["Harness"].__dict__[name]


IDENTITY = {key: key for key in ("job_id", "operation_id", "request_id",
                                 "request_hash", "idempotency_key")}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}


class FakeStore:
    """Small store fake with finish/readback and terminal immunity semantics."""
    def __init__(self, fail_event=None, fail_phase=None, transition_error=None,
                 fail_update=None, finish_error=None, readback_error=False):
        self.status = "QUEUED"
        self.persisted_result = None
        self.fail_event = fail_event
        self.fail_phase = fail_phase
        self.transition_error = transition_error
        self.fail_update = fail_update
        self.finish_error = finish_error
        self.readback_error = readback_error
        self.events = []
        self.lock = threading.RLock()
        self.db = SimpleNamespace(in_transaction=False)

    def job(self, _job_id):
        return {"job_id": IDENTITY["job_id"], "operation_id": IDENTITY["operation_id"],
                "status": self.status,
                "operation": {"operation_id": IDENTITY["operation_id"]},
                "result": copy.deepcopy(self.persisted_result)}

    def get_operation(self, _operation_id):
        if self.readback_error:
            raise OSError("synthetic readback failure")
        return {**IDENTITY, "status": self.status,
                "result": copy.deepcopy(self.persisted_result)}

    def unresolved_jobs(self):
        return []

    def update_job(self, _job_id, status, _metadata):
        if status == self.fail_update:
            raise OSError("synthetic job update failure")
        if self.status in TERMINAL and status != self.status:
            return
        self.status = status

    def add_event(self, _job_id, event, metadata=None):
        if event == self.fail_event and (
                self.fail_phase is None or
                (isinstance(metadata, dict) and metadata.get("phase") == self.fail_phase)):
            raise OSError("synthetic trace sink failure")
        self.events.append((event, copy.deepcopy(metadata)))

    def transition_status(self, _job_id, **_kwargs):
        if callable(self.transition_error):
            self.transition_error(self)
        elif self.transition_error is not None:
            raise self.transition_error
        if self.status != "QUEUED":
            return False, self.status, self.job(IDENTITY["job_id"])
        self.status = "RUNNING"
        return True, "RUNNING", self.job(IDENTITY["job_id"])

    def finish(self, _operation_id, *, status, result):
        if self.finish_error is not None:
            raise self.finish_error
        if self.status in TERMINAL:
            return False, self.status
        self.status = status
        self.persisted_result = copy.deepcopy(result)
        return True, status

    def cancel_before_finish(self):
        self.status = "CANCELLED"
        self.persisted_result = {
            "success": True,
            "data": {"status": "CANCELLED", "cancel_accepted": True,
                     "engine_dispatched": False},
            "execution": {"job_id": IDENTITY["job_id"],
                          "operation_id": IDENTITY["operation_id"],
                          "request_id": IDENTITY["request_id"],
                          "request_hash": IDENTITY["request_hash"],
                          "idempotency_key": IDENTITY["idempotency_key"]},
        }


class FakeBackend:
    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = 0
        self.engine_send_count = 0

    def invoke(self, _operation, _arguments, _execution, _operation_id, callback):
        self.calls += 1
        if self.scenario == "submitted_trace_failure":
            callback({"phase": "submitted"})
            self.engine_send_count += 1
        elif self.scenario == "observed_trace_failure":
            callback({"phase": "submitted"})
            self.engine_send_count += 1
            # This is only a synthetic callback value. The control must not
            # promote it to a durable successful operation result.
            callback({"phase": "observed", "status": "SUCCEEDED"})
        elif self.scenario == "invalid_phase_trace_failure":
            callback({"phase": object()})
            self.engine_send_count += 1
        elif self.scenario == "engine_exception":
            raise RuntimeError("synthetic engine-side exception")
        elif self.scenario == "invalid_phase_success":
            callback({"phase": object()})
            callback({"phase": "x" * 10000})
            self.engine_send_count += 1
        return {"success": True, "data": {}}


def make_control(store, backend):
    class Control:
        pass

    control = Control()
    control.store = store
    control.backend = backend
    control.lock = threading.RLock()
    control.running = {}
    control._disk_resource_coordinator = None
    control._memory_resource_coordinator = None
    control._desktop_before_model_dispatch = lambda *_args: None
    control._authorize_project_execution = lambda *_args: None
    control._attach_confirmed_project_id = lambda *_args: None
    control._log_exception = lambda: None
    control._release_terminal_resource_reservations = lambda _job_id: {
        "state": "COMPLETE", "reservation_state": "SYNTHETIC_RELEASED"}
    control._error = lambda code, message, *, data=None, **details: {
        "success": False, "data": data or {},
        "error": {"code": code, "message": message,
                  "safe_retry": details.pop("safe_retry", False), **details}}

    def exception(exc):
        if isinstance(exc, SyntheticExecutionContractError):
            return {"success": False, "data": {}, "error": exc.as_dict()}
        return control._error("IDEMPOTENCY_CONFLICT", str(exc))

    control._exception = exception
    finish = extracted_method("_finish")
    control._finish = finish.__get__(control, type(control))
    return control


def run_execute(store, backend):
    method = extracted_method("_execute")
    control = make_control(store, backend)
    control._execute = method.__get__(control, type(control))
    return control._execute(IDENTITY, "synthetic.operation", {}, {},
                            {"queue_timeout_s": None}, time.monotonic()), control


def test_submitted_trace_failure_stays_unknown_without_retry():
    store = FakeStore(fail_event="worker_request", fail_phase="submitted")
    backend = FakeBackend("submitted_trace_failure")
    result, _control = run_execute(store, backend)
    assert backend.engine_send_count == 0
    assert store.status == "UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert "trace write failed" in result["error"]["message"]
    assert result["error"]["safe_retry"] is False
    assert result["error"]["details"]["failure_class"] == "TRACE_WRITE_FAILURE"
    assert result["error"]["details"]["cause_code"] == "WORKER_TRACE_WRITE_FAILED"
    assert result["error"]["details"]["phase"] == "submitted"


def test_observed_trace_failure_after_possible_dispatch_stays_unknown():
    store = FakeStore(fail_event="worker_request", fail_phase="observed")
    backend = FakeBackend("observed_trace_failure")
    result, _control = run_execute(store, backend)
    assert backend.engine_send_count == 1
    assert store.status == "UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert "trace write failed" in result["error"]["message"]
    assert result["error"]["safe_retry"] is False
    assert result["error"]["details"]["failure_class"] == "TRACE_WRITE_FAILURE"
    assert result["error"]["details"]["phase"] == "observed"


def test_invalid_non_json_phase_is_sanitized_before_trace_failure_result():
    store = FakeStore(fail_event="worker_request")
    backend = FakeBackend("invalid_phase_trace_failure")
    result, _control = run_execute(store, backend)
    encoded = json.dumps(result)
    assert backend.engine_send_count == 0
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["error"]["details"]["phase"] == "unrecognized"
    assert "synthetic trace sink failure" not in encoded
    assert "object at 0x" not in encoded


def test_non_json_and_oversized_phases_are_finitely_sanitized_on_success():
    store = FakeStore()
    backend = FakeBackend("invalid_phase_success")
    result, _control = run_execute(store, backend)
    worker_events = [metadata for event, metadata in store.events if event == "worker_request"]
    assert backend.engine_send_count == 1
    assert store.status == "SUCCEEDED"
    assert result["success"] is True
    assert [event["phase"] for event in worker_events] == ["unrecognized", "unrecognized"]
    assert all(len(event["phase"]) < 32 for event in worker_events)


def test_engine_exception_does_not_use_trace_write_classification():
    store = FakeStore()
    result, _control = run_execute(store, FakeBackend("engine_exception"))
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["message"] == "backend execution failed; inspect worker evidence"
    assert result["error"].get("details", {}).get("failure_class") != "TRACE_WRITE_FAILURE"


def test_ordinary_backend_success_finishes_authoritatively():
    store = FakeStore()
    backend = FakeBackend("success")
    result, _control = run_execute(store, backend)
    assert backend.calls == 1
    assert store.status == "SUCCEEDED"
    assert store.persisted_result["success"] is True
    assert result["execution"]["operation_id"] == IDENTITY["operation_id"]


def test_running_trace_failure_is_terminal_pre_dispatch_failure():
    store = FakeStore(fail_event="RUNNING")
    backend = FakeBackend("success")
    result, _control = run_execute(store, backend)
    assert backend.calls == 0
    assert store.status == "FAILED"
    assert result["error"]["code"] == "PRE_DISPATCH_TRACE_WRITE_FAILED"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["engine_dispatched"] is False


def test_running_status_write_failure_is_terminal_pre_dispatch_failure():
    store = FakeStore(fail_update="RUNNING")
    backend = FakeBackend("success")
    result, _control = run_execute(store, backend)
    assert backend.calls == 0
    assert store.status == "FAILED"
    assert result["error"]["code"] == "PRE_DISPATCH_STATE_WRITE_FAILED"
    assert result["data"]["failed_step"] == "running_status_write"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["engine_dispatched"] is False


def test_running_transition_write_failure_finishes_before_dispatch():
    store = FakeStore(transition_error=sqlite3.IntegrityError("synthetic event trigger"))
    backend = FakeBackend("success")
    control = make_control(store, backend)
    release_calls = []
    control._release_terminal_resource_reservations = lambda job_id: (
        release_calls.append((store.status, job_id)) or {
            "state": "COMPLETE", "reservation_state": "SYNTHETIC_RELEASED"})

    class DiskCoordinator:
        def requires_reservation(self, **_kwargs):
            return True

        def reserve_queued_job(self, *_args, **_kwargs):
            return {"operation_id": "op", "reservation_id": "res", "requested_bytes": 1,
                    "trusted_estimate_bytes": 1, "disk_resource_profile_id": "profile",
                    "disk_resource_profile_sha256": "0" * 64, "host_id": "host",
                    "storage_id": "storage", "enforcement": "synthetic",
                    "evidence_classification": "synthetic"}

    control._disk_resource_coordinator = DiskCoordinator()
    control._execute = extracted_method("_execute").__get__(control, type(control))
    result = control._execute(IDENTITY, "synthetic.operation", {}, {},
                              {"queue_timeout_s": None}, time.monotonic())
    assert backend.calls == 0
    assert store.status == "FAILED"
    assert result["error"]["code"] == "PRE_DISPATCH_STATE_WRITE_FAILED"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["engine_dispatched"] is False
    assert release_calls == [("FAILED", IDENTITY["job_id"])]


def test_cancel_wins_transition_exception_and_resource_release_is_authorized():
    def cancel_then_fail(store):
        store.cancel_before_finish()
        raise sqlite3.IntegrityError("synthetic transition failure")

    store = FakeStore(transition_error=cancel_then_fail)
    backend = FakeBackend("success")
    control = make_control(store, backend)
    release_calls = []
    control._release_terminal_resource_reservations = lambda job_id: (
        release_calls.append(job_id) or {
            "state": "COMPLETE", "reservation_state": "SYNTHETIC_RELEASED"})

    class DiskCoordinator:
        def requires_reservation(self, **_kwargs):
            return True

        def reserve_queued_job(self, *_args, **_kwargs):
            return {"operation_id": "op", "reservation_id": "res", "requested_bytes": 1,
                    "trusted_estimate_bytes": 1, "disk_resource_profile_id": "profile",
                    "disk_resource_profile_sha256": "0" * 64, "host_id": "host",
                    "storage_id": "storage", "enforcement": "synthetic",
                    "evidence_classification": "synthetic"}

    control._disk_resource_coordinator = DiskCoordinator()
    control._execute = extracted_method("_execute").__get__(control, type(control))
    result = control._execute(IDENTITY, "synthetic.operation", {}, {},
                              {"queue_timeout_s": None}, time.monotonic())
    assert backend.calls == 0
    assert store.status == "CANCELLED"
    assert result["data"]["status"] == "CANCELLED"
    assert release_calls == [IDENTITY["job_id"]]
    assert result["execution"]["pre_dispatch_resource_release_observation"]["state"] == "COMPLETE"


def test_finish_and_readback_failure_keep_original_status_and_reservation():
    store = FakeStore(
        transition_error=sqlite3.IntegrityError("synthetic transition failure"),
        finish_error=sqlite3.OperationalError("synthetic finish failure"),
        readback_error=True,
    )
    backend = FakeBackend("success")
    control = make_control(store, backend)
    release_calls = []
    control._release_terminal_resource_reservations = lambda job_id: release_calls.append(job_id)

    class DiskCoordinator:
        def requires_reservation(self, **_kwargs):
            return True

        def reserve_queued_job(self, *_args, **_kwargs):
            return {"operation_id": "op", "reservation_id": "res", "requested_bytes": 1,
                    "trusted_estimate_bytes": 1, "disk_resource_profile_id": "profile",
                    "disk_resource_profile_sha256": "0" * 64, "host_id": "host",
                    "storage_id": "storage", "enforcement": "synthetic",
                    "evidence_classification": "synthetic"}

    control._disk_resource_coordinator = DiskCoordinator()
    control._execute = extracted_method("_execute").__get__(control, type(control))
    result = control._execute(IDENTITY, "synthetic.operation", {}, {},
                              {"queue_timeout_s": None}, time.monotonic())
    observation = result["execution"]["pre_dispatch_resource_release_observation"]
    assert backend.calls == 0
    assert store.status == "QUEUED"
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert observation["state"] == "NOT_ATTEMPTED"
    assert observation["reservation_state"] == "UNCONFIRMED"
    assert release_calls == []


def test_finish_failure_with_nonterminal_readback_stays_unknown():
    store = FakeStore(finish_error=sqlite3.OperationalError("synthetic finish failure"))
    control = make_control(store, FakeBackend("success"))
    release_calls = []
    control._release_terminal_resource_reservations = lambda job_id: release_calls.append(job_id)
    result = control._finish(
        IDENTITY, {"success": False, "data": {}, "execution": {}}, "FAILED",
        _pre_dispatch_resource_reserved=True,
    )
    observation = result["execution"]["pre_dispatch_resource_release_observation"]
    assert store.status == "QUEUED"
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert observation["state"] == "NOT_ATTEMPTED"
    assert observation["reservation_state"] == "UNCONFIRMED"
    assert release_calls == []


def test_non_sqlite_finish_failure_is_not_masked_or_committed():
    store = FakeStore(finish_error=TypeError("synthetic serialization failure"))
    control = make_control(store, FakeBackend("success"))
    finish = control._finish
    try:
        finish(IDENTITY, {"success": False, "data": {}, "execution": {}}, "FAILED",
               _pre_dispatch_resource_reserved=True)
    except TypeError as exc:
        assert str(exc) == "synthetic serialization failure"
    else:
        raise AssertionError("_finish unexpectedly masked its non-SQLite failure")
    assert store.status == "QUEUED"
    assert store.persisted_result is None


class TerminalStore:
    def __init__(self):
        self.persisted_result = None
        self.status = None

    def finish(self, _operation_id, *, status, result):
        self.status = status
        self.persisted_result = json.loads(json.dumps(result))
        return True, status

    def update_job(self, *_args):
        return None

    def add_event(self, _job_id, event, _metadata=None):
        if event == "SUCCEEDED":
            raise OSError("synthetic terminal trace failure")


def run_terminal_finish(*, fail_diagnostic_log=False):
    method = extracted_method("_finish")
    store = TerminalStore()

    class Control:
        _finish = method

        def __init__(self):
            self.store = store
            if fail_diagnostic_log:
                def failed_log():
                    raise OSError("synthetic control-errors.log failure")
                self._log_exception = failed_log
            else:
                self._log_exception = lambda: None
            self._release_terminal_resource_reservations = lambda _job_id: {"released": True}

    record = {key: key for key in ("job_id", "operation_id", "request_id",
                                   "request_hash", "idempotency_key")}
    result = Control()._finish(record, {"success": True, "data": {}, "execution": {}}, "SUCCEEDED")
    return store, result


def test_terminal_trace_failure_preserves_status_and_is_response_only():
    store, result = run_terminal_finish()
    warning = result["execution"]["post_commit_warning"]
    assert store.status == "SUCCEEDED"
    assert store.persisted_result["execution"].get("post_commit_warning") is None
    assert warning["code"] == "POST_COMMIT_WRITE_FAILURE"
    assert warning["failed_steps"] == ["terminal_event_write"]
    assert warning["failure_types"] == {"terminal_event_write": "OSError"}
    assert warning["diagnostic_durable"] is False
    assert warning["durability_scope"] == "response_only"


def test_terminal_trace_and_diagnostic_log_failures_keep_both_causes():
    store, result = run_terminal_finish(fail_diagnostic_log=True)
    warning = result["execution"]["post_commit_warning"]
    assert store.status == "SUCCEEDED"
    assert warning["failed_steps"] == ["terminal_event_write", "control_errors_log_write"]
    assert warning["failure_types"] == {
        "terminal_event_write": "OSError", "control_errors_log_write": "OSError"}
    assert warning["diagnostic_durable"] is False
    assert warning["durability_scope"] == "response_only"


def main():
    tests = [value for name, value in globals().items()
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS total={len(tests)}")


if __name__ == "__main__":
    main()
