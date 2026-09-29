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


def test_private_w21_stage_marker_binds_project_reply_without_changing_generic_calls(tmp_path):
    service = ExecutionService(SessionLedger("s", "srv"), Snapshot(), project_root=tmp_path)
    store = OperationStore(tmp_path / "operations.sqlite3")
    calls = []
    backend = ManagedBackend(tmp_path, store, service=service,
                             registry={"run_study": lambda args: calls.append(args) or {"success": True, "data": {}}})
    try:
        ref = service.bind_model("m")["execution"]["model_ref"]
        key = backend._model_project_key(ref)
        store.put_metadata("revisions", key, {"project_id": "project-a", "model_ref": ref, "revision": 0})
        marker = {
            "attempt_id": "attempt-a", "phase": "solve", "project_id": "project-a",
            "model_ref": ref, "expected_revision": 0, "request_id": "attempt-a:solve",
            "operation_id": "operation-a", "binding_sha256": "a" * 64,
            "study_tag": "std1",
        }
        class Worker:
            def client(self):
                return self

            def model(self, _tag):
                return type("BoundModel", (), {"_handle": "model-h", "_generation": 71})()

            def operation_context(self, *_args, **_kwargs):
                return nullcontext()

        backend.worker = Worker()
        execution = {
            "project_id": "project-a", "session_id": "s", "model_ref": ref,
            "expected_revision": 0, "request_id": "attempt-a:solve",
            "_w21_stage_marker": marker,
        }
        result = backend.invoke("run_study", {"study_tag": "std1"}, execution, "operation-a", lambda _event: None)
        assert result["success"] is True
        assert result["execution"]["project_id"] == "project-a"
        assert result["execution"]["model_ref"] == ref
        assert calls == [{"study_tag": "std1"}]

        tampered = {**execution, "_w21_stage_marker": {**marker, "project_id": "project-b"}}
        with pytest.raises(ExecutionContractError, match="private W21 stage dispatch marker"):
            backend.invoke("run_study", {"study_tag": "std1"}, tampered, "operation-a", lambda _event: None)
        assert calls == [{"study_tag": "std1"}]
    finally:
        store.close()


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


@pytest.mark.parametrize("operation", ["model_create", "model_load"])
def test_model_selection_binds_session_context_model_after_legacy_callback(tmp_path, monkeypatch, operation):
    """The real legacy selection callback and backend must share session-local model state."""
    from pathlib import Path

    from comsol_mcp._execution_contract import SessionLedger
    from comsol_mcp._session_context import (
        CanonicalSocket,
        SessionEndpointIdentity,
        SessionRuntimeConfig,
        SessionRuntimeContext,
        use_session_context,
    )
    import comsol_mcp._server as server_module

    class Worker:
        generation = 7

        def __init__(self):
            self.labels = {}
            self.calls = []

        def client(self):
            from comsol_mcp._java_worker import RemoteClient
            return RemoteClient(self)

        def operation_context(self, *_args, **_kwargs):
            return nullcontext()

        def model_snapshot(self, tag):
            return {"model_tag": tag, "fingerprint": f"fp:{tag}", "external_event_counter": 0}

        def submit(self, kind, payload, **_kwargs):
            self.calls.append((kind, dict(payload)))
            result = None
            if kind == "modelutil":
                method, args = payload["method"], payload.get("args", [])
                if method == "uniquetag":
                    result = "mcp1"
                elif method == "tags":
                    result = []
                elif method in {"create", "load"}:
                    tag = args[0]
                    self.labels[tag] = "Selected model" if method == "create" else Path(args[1]).stem
                    result = {"$worker_handle": f"model:{tag}", "generation": self.generation,
                              "java_type": "com.comsol.model.Model"}
                else:
                    raise AssertionError(f"unexpected modelutil method: {method}")
            elif kind == "call":
                tag = payload["handle"].removeprefix("model:")
                method, args = payload["method"], payload.get("args", [])
                if method == "label":
                    if args:
                        self.labels[tag] = args[0]
                    else:
                        result = self.labels[tag]
                elif method == "tag":
                    result = tag
                elif method == "getFilePath":
                    result = ""
                else:
                    raise AssertionError(f"unexpected model method: {method}")
            else:
                raise AssertionError(f"unexpected Worker command: {kind}")
            return {"ok": True, "status": "SUCCEEDED", "result": result}

    project_root = tmp_path / "workspace"
    project_root.mkdir()
    model_path = project_root / "input.mph"
    model_path.write_bytes(b"synthetic model path fixture")
    runtime = SessionRuntimeConfig(
        runtime_id="runtime-a", comsol_version="6.4.0.293",
        installation_root=tmp_path / "comsol", java_executable=tmp_path / "java",
        classpath=(tmp_path / "client.jar",), preferences_dir=tmp_path / "prefs",
        session_state_root=tmp_path / "session-state",
    )
    worker = Worker()
    service = ExecutionService(
        SessionLedger("session-a", "server-a", server_ownership="mcp_managed"),
        worker, project_root=project_root,
    )
    store = OperationStore(tmp_path / "operations.sqlite3")
    backend = ManagedBackend(
        tmp_path / "backend", store, service=service, worker=worker, project_root=project_root,
    )
    endpoint = SessionEndpointIdentity(
        "127.0.0.1", 1278, worker_epoch=1, observed_peer=CanonicalSocket("127.0.0.1", 1278),
    )
    context = SessionRuntimeContext(
        project_id="project-a", session_id="session-a", project_root=project_root,
        runtime=runtime, endpoint=endpoint, backend=backend, worker=worker,
        worker_instance_id="worker-a", service=service, client=worker.client(),
        client_connected=True, server_ownership="mcp_managed",
    )
    monkeypatch.setattr(server_module, "_current_model", None)
    monkeypatch.setattr(server_module, "_mcp_owned_model_tags", set())
    args = {"name": "Selected model"} if operation == "model_create" else {"path": "input.mph"}

    try:
        with use_session_context(context):
            result = backend.invoke(
                operation, args, {"project_id": "project-a", "session_id": "session-a"},
                f"op-{operation}", lambda _event: None,
            )
            selected_tag = result["data"]["model_tag"]
            assert result["success"] is True
            assert result["execution"]["model_ref"]["model_tag"] == selected_tag
            assert context.current_model._handle == f"model:{selected_tag}"
            assert context.owned_model_tags == {selected_tag}
            assert service.ledger.model_ownership[selected_tag] == "mcp_owned"
            assert any(kind == "call" and payload["method"] == "tag" for kind, payload in worker.calls)
        assert server_module._current_model is None
        assert selected_tag not in server_module._mcp_owned_model_tags
    finally:
        backend.close()
        store.close()
