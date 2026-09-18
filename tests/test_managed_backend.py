"""Control routing regressions. No test in this module contacts COMSOL."""
from contextlib import nullcontext

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend, EXTERNAL_CHANGE_DETECTION_CAPABILITY
from comsol_mcp._operation_store import OperationStore


class Snapshot:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "fingerprint": "fp", "external_event_counter": 0}


def test_external_change_capability_is_explicitly_scope_limited(tmp_path):
    backend = ManagedBackend(tmp_path, OperationStore(tmp_path / "operations.sqlite3"))
    try:
        capability = backend.cached["external_change_detection"]
        assert capability == EXTERNAL_CHANGE_DETECTION_CAPABILITY
        assert capability["coverage"] == "parameters+shallow_tree_identity"
        assert capability["status"] == "SCOPED_EVIDENCE_ONLY"
        assert capability["event_notifications"] == "UNVERIFIED"
        assert capability["full_model_property_coverage"] == "UNVERIFIED"
        assert capability["cas_guarantee"] == "NONE"
        assert capability["evidence_source"].endswith("142300Z-external-api")
        assert capability["evidence_environment"] == "macOS arm64; COMSOL 6.4.0.293"
        assert capability["other_platforms_or_versions"] == "UNVERIFIED"
        capability["event_notifications"] = "mutated-test-value"
        assert EXTERNAL_CHANGE_DETECTION_CAPABILITY["event_notifications"] == "UNVERIFIED"
    finally:
        backend.store.close()


def test_unbound_visible_load_is_permitted_but_model_reads_still_require_ref(tmp_path):
    service = ExecutionService(SessionLedger("s", "srv"), Snapshot(), project_root=tmp_path)
    calls = []
    callback = lambda args: calls.append(args) or {"success": True, "data": {}}
    for operation in ("load_visible_main_model", "load_current_main_model", "start_visible_main_workflow"):
        assert service.execute_legacy(operation, callback, {})["success"]
    with pytest.raises(ExecutionContractError, match="model_ref"):
        service.execute_legacy("get_parameters", callback, {})
    assert len(calls) == 3


def test_unconnected_local_workflow_tools_and_path_boundary(tmp_path):
    calls = []
    registry = {name: lambda args: calls.append(args) or {"success": True, "data": args}
                for name in ("workflow_info", "check_server_port", "mcp_tool_audit", "configure_single_main_workflow")}
    daemon = ControlDaemon(tmp_path, registry=registry)
    daemon.backend.project_root = tmp_path
    try:
        for name in registry:
            assert daemon.dispatch({"operation": name})["success"]
        denied = daemon.dispatch({"operation": "configure_single_main_workflow",
                                  "arguments": {"current_main_model_path": str(tmp_path.parent / "outside.mph")}})
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert len(calls) == 4
    finally:
        daemon.close()


def test_async_public_study_uses_serial_synchronous_callback(tmp_path):
    service = ExecutionService(SessionLedger("s", "srv"), Snapshot(), project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    calls = []
    daemon = ControlDaemon(tmp_path, service=service, registry={
        "run_study_async": lambda args: pytest.fail("legacy background thread path must not run"),
        "run_study": lambda args: calls.append(args) or {"success": True, "data": {}}})
    try:
        result = daemon.dispatch({"operation": "run_study_async", "arguments": {"study_tag": "std1"},
                                  "execution": {"model_ref": ref, "expected_revision": 0}})
        assert result["success"] and calls == [{"study_tag": "std1"}]
    finally:
        daemon.close()


def test_explicit_reconnect_starts_existing_worker_and_invalidates_old_epoch(tmp_path, monkeypatch):
    import comsol_mcp._server as srv
    for name in ("_remote_client_factory", "_client", "_client_connected", "_connected_host", "_connected_port", "_server_started_by_mcp"):
        monkeypatch.setattr(srv, name, getattr(srv, name))
    class Worker:
        generation = 1
        starts = 0
        replacement_pending = False
        def start(self):
            self.starts += 1
            if self.replacement_pending:
                self.generation += 1
                self.replacement_pending = False
        def health(self): return {"connected": True, "server": "127.0.0.1:56388"}
        def runtime_metadata(self): return {"instance_id": "worker-" + str(self.generation), "generation": self.generation}
        def operation_context(self, *args, **kwargs): return nullcontext()
        def client(self): return self
        def backend_snapshot(self, tag):
            return {"model_tag": tag, "fingerprint": "fp", "external_event_counter": 0,
                    "server_instance_id": "127.0.0.1:56388", **self.runtime_metadata()}
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = Worker()
    backend = ManagedBackend(tmp_path, store, worker=worker)
    args = {"host": "127.0.0.1", "port": 56388}
    try:
        backend.connect(args, "first", lambda _: None)
        ref = backend.service.bind_model("m")["execution"]["model_ref"]
        worker.replacement_pending = True
        backend.connect(args, "explicit-recovery", lambda _: None)
        assert worker.starts == 2
        from comsol_mcp._execution_contract import model_ref_from_mapping
        with pytest.raises(ExecutionContractError, match="another session or server"):
            backend.service.inspect(model_ref_from_mapping(ref))
    finally:
        store.close()


def test_visible_workflow_start_uses_managed_connection_not_legacy_reconnect(tmp_path, monkeypatch):
    from comsol_mcp import _tools_workflow
    service = ExecutionService(SessionLedger("s", "srv"), Snapshot(), project_root=tmp_path)
    store = OperationStore(tmp_path / "operations.sqlite3")
    calls = []
    backend = ManagedBackend(tmp_path, store, service=service,
        registry={"start_visible_main_workflow": lambda args: pytest.fail("legacy lifecycle callback")})
    connection = {"success": True, "data": {"connected": True}}
    monkeypatch.setattr(backend, "connect", lambda *args: calls.append("managed-connect") or connection)
    def helper(**kwargs):
        assert kwargs["managed_connection"] is connection
        assert kwargs["action"] == "start_visible_main_workflow"
        calls.append("load-and-verify")
        return {"loaded": True}
    monkeypatch.setattr(_tools_workflow, "_start_visible_main_workflow_payload", helper)
    try:
        result = backend.invoke("start_visible_main_workflow_async", {}, {}, "op", lambda _: None)
        assert result["success"] and calls == ["managed-connect", "load-and-verify"]
    finally:
        store.close()
