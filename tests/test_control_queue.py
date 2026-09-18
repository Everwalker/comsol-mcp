"""Event-driven queue regressions; callbacks are pure test doubles, never COMSOL."""
import threading
import time

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}


def test_same_key_reuses_running_job_and_health_stays_responsive(tmp_path):
    entered, release = threading.Event(), threading.Event(); calls = []
    service = ExecutionService(SessionLedger("s", "server"), Adapter(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    def block(args):
        calls.append(args); entered.set(); release.wait(2); return {"success": True, "data": {"done": True}}
    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": block})
    request = {"operation":"set_parameters","arguments":{"x":1},"execution":{"model_ref":ref,"expected_revision":0,"idempotency_key":"same","rpc_timeout_s":0.01}}
    try:
        first = daemon.dispatch(request); assert entered.wait(1)
        second = daemon.dispatch(request)
        assert first["data"]["job_id"] == second["data"]["job_id"]
        assert daemon.dispatch({"operation":"session_health","arguments":{},"execution":{}})["success"]
        assert calls == [{"x": 1}]
    finally:
        release.set(); time.sleep(.05); daemon.close()


def test_queue_expiry_does_not_invoke_callback(tmp_path):
    entered, release = threading.Event(), threading.Event(); called = []
    service = ExecutionService(SessionLedger("s", "server"), Adapter(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    def first(args): entered.set(); release.wait(2); return {"success": True, "data": {}}
    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": first})
    base={"model_ref":ref,"expected_revision":0,"rpc_timeout_s":0.01}
    try:
        daemon.dispatch({"operation":"set_parameters","arguments":{},"execution":{**base,"idempotency_key":"a"}}); assert entered.wait(1)
        queued=daemon.dispatch({"operation":"set_parameters","arguments":{},"execution":{**base,"idempotency_key":"b","queue_timeout_s":0.001}})
        release.set(); time.sleep(.08)
        job=daemon.dispatch({"operation":"job_status","arguments":{"job_id":queued["data"]["job_id"]},"execution":{}})
        assert job["data"]["status"] == "EXPIRED"
    finally: release.set(); daemon.close()


def test_execution_and_progress_watchdogs_start_only_after_running(tmp_path):
    entered, release, second_called = threading.Event(), threading.Event(), threading.Event()
    service = ExecutionService(SessionLedger("s", "server"), Adapter(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    def block(args): entered.set(); release.wait(2); return {"success": True, "data": {"first": True}}
    def second(args): second_called.set(); return {"success": True, "data": {"second": True}}
    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": block, "save_model": second})
    base={"model_ref":ref,"expected_revision":0,"rpc_timeout_s":0.01,"execution_timeout_s":0.03,"no_progress_warning_s":0.02}
    try:
        one=daemon.dispatch({"operation":"set_parameters","arguments":{},"execution":{**base,"idempotency_key":"one"}}); assert entered.wait(1)
        two=daemon.dispatch({"operation":"save_model","arguments":{},"execution":{**base,"idempotency_key":"two"}})
        time.sleep(.08)
        events=daemon.dispatch({"operation":"job_log","arguments":{"job_id":one["data"]["job_id"]},"execution":{}})["data"]["events"]
        assert {e["event"] for e in events} >= {"ExecutionDeadlineExceeded", "NoProgressWarning"}
        assert not second_called.is_set()
        queued=daemon.dispatch({"operation":"job_status","arguments":{"job_id":two["data"]["job_id"]},"execution":{}})
        assert queued["data"]["status"] == "QUEUED"
        release.set(); time.sleep(.08)
        assert daemon.dispatch({"operation":"job_result","arguments":{"job_id":one["data"]["job_id"]},"execution":{}})["data"]["result"]["success"]
    finally: release.set(); daemon.close()


def test_model_inspect_is_queued_behind_solve_and_does_not_snapshot_early(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class CountingAdapter(Adapter):
        def __init__(self): self.calls = 0
        def model_snapshot(self, tag): self.calls += 1; return super().model_snapshot(tag)
    adapter = CountingAdapter(); service = ExecutionService(SessionLedger("s", "server"), adapter, project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]; baseline = adapter.calls
    def block(args): entered.set(); release.wait(2); return {"success": True, "data": {}}
    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": block})
    try:
        solve=daemon.dispatch({"operation":"set_parameters","arguments":{},"execution":{"model_ref":ref,"expected_revision":0,"idempotency_key":"solve","rpc_timeout_s":0.01}}); assert entered.wait(1)
        inspect=daemon.dispatch({"operation":"model_inspect","arguments":{"refresh":False},"execution":{"model_ref":ref,"idempotency_key":"inspect","rpc_timeout_s":0.01}})
        assert inspect["data"]["status"] == "QUEUED" and adapter.calls == baseline + 1  # solve preflight only
        assert daemon.dispatch({"operation":"session_health","arguments":{},"execution":{}})["success"]
        assert daemon.dispatch({"operation":"job_log","arguments":{"job_id":solve["data"]["job_id"]},"execution":{}})["success"]
    finally: release.set(); daemon.close()
