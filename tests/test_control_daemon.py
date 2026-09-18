from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def model_snapshot(self, tag): return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}


def test_health_without_worker_and_unknown_operation(tmp_path):
    daemon = ControlDaemon(tmp_path)
    assert daemon.dispatch({"operation": "session_health", "arguments": {}, "execution": {}})["success"]
    assert daemon.dispatch({"operation": "nope", "arguments": {}, "execution": {}})["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_daemon_routes_callback_through_service_and_idempotency(tmp_path):
    service = ExecutionService(SessionLedger("s", "server"), Adapter(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": lambda args: {"success": True, "data": args}})
    request = {"operation": "set_parameters", "arguments": {"x": 1}, "execution": {"model_ref": ref, "expected_revision": 0, "idempotency_key": "key", "request_id": "r"}}
    one = daemon.dispatch(request)
    two = daemon.dispatch(request)
    assert one["success"] and two == one


def test_lazy_bootstrap_requires_explicit_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("COMSOL_ROOT", raising=False)
    monkeypatch.delenv("COMSOL_JAVA_HOME", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    result = ControlDaemon(tmp_path).dispatch({"operation": "server_connect", "arguments": {"host": "127.0.0.1", "port": 5678}, "execution": {}})
    assert result["success"] is False
    assert result["error"]["code"] == "RUNTIME_CONFIGURATION_REQUIRED"


def test_timeout_fields_reject_bool_and_nonfinite_without_callback(tmp_path):
    daemon = ControlDaemon(tmp_path)
    for value in (True, float("inf"), -1):
        result = daemon.dispatch({"operation": "session_health", "arguments": {}, "execution": {"queue_timeout_s": value}})
        assert result["error"]["code"] == "INVALID_REQUEST"


def test_reconcile_uses_durable_completion_after_worker_replacement(tmp_path):
    class Replacement:
        def status(self, identifier):
            pytest.fail("completed old request must not be resubmitted or queried in replacement")
    import pytest
    daemon = ControlDaemon(tmp_path, worker=Replacement())
    try:
        record, _ = daemon.store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="model_load")
        job_id = record["job_id"]
        for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
            daemon.store.add_event(job_id, "worker_request", {"request_id": "old-worker-load", "phase": phase, "status": status})
        daemon.store.update_job(job_id, "UNKNOWN")
        result = daemon.dispatch({"operation": "job_reconcile", "arguments": {"job_id": job_id}})
        assert result["data"]["status"] == "UNKNOWN"
        assert result["data"]["metadata"]["reconciled_quiescent"] is True
        assert result["data"]["metadata"]["replay_performed"] is False
    finally:
        daemon.close()


def test_reconcile_never_ignores_pending_request_beyond_first_log_page(tmp_path):
    class Replacement:
        def status(self, identifier): return {"request_id": identifier, "status": "RUNNING"}
    daemon = ControlDaemon(tmp_path, worker=Replacement())
    try:
        record, _ = daemon.store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="run_study")
        job_id = record["job_id"]
        for index in range(501):
            daemon.store.add_event(job_id, "worker_request", {"request_id": str(index), "phase": "submitted"})
            if index < 500:
                daemon.store.add_event(job_id, "worker_request", {"request_id": str(index), "phase": "observed", "status": "SUCCEEDED"})
        daemon.store.update_job(job_id, "UNKNOWN")
        result = daemon.dispatch({"operation": "job_reconcile", "arguments": {"job_id": job_id}})
        assert result["data"]["status"] == "RECONCILING"
        assert result["data"]["metadata"]["reconciled_quiescent"] is False
    finally:
        daemon.close()


def test_callback_exception_persists_unknown_and_restart_reconciles_without_replay(tmp_path):
    calls = []
    service = ExecutionService(SessionLedger("s", "server"), Adapter(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]

    def lost_worker_callback(_args):
        calls.append("called")
        raise RuntimeError("worker transport disappeared after dispatch")

    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": lost_worker_callback})
    request = {
        "operation": "set_parameters",
        "arguments": {"x": 1},
        "execution": {"model_ref": ref, "expected_revision": 0, "idempotency_key": "lost", "request_id": "lost"},
    }
    result = daemon.dispatch(request)
    job_id = result["execution"]["job_id"]
    assert calls == ["called"]
    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    observed = daemon.dispatch({"operation": "job_result", "arguments": {"job_id": job_id}, "execution": {}})
    assert observed["data"]["status"] == "UNKNOWN"
    assert observed["data"]["finished_at"] is None
    daemon.close()

    restarted = ControlDaemon(tmp_path)
    try:
        after_restart = restarted.dispatch({"operation": "job_status", "arguments": {"job_id": job_id}, "execution": {}})
        assert after_restart["data"]["status"] == "RECONCILING"
        reconciled = restarted.dispatch({"operation": "job_reconcile", "arguments": {"job_id": job_id}, "execution": {}})
        assert reconciled["success"] is False
        assert reconciled["error"]["safe_retry"] is False
        assert calls == ["called"]
    finally:
        restarted.close()
