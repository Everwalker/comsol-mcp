from __future__ import annotations

import pytest
from contextlib import nullcontext

from comsol_mcp import _g2_registry
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Snapshot:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}


class JavaFileList:
    def tags(self):
        return ["file1", "file2"]


class JavaModel:
    def getFileResourceTags(self):
        return JavaFileList().tags()

    def getComsolVersion(self):
        return "6.4.0.293"

    def getLastComputationTime(self):
        return "2026-09-26T00:00:00Z"

    def getLastComputationDate(self):
        return "2026-09-26"

    def getLastComputationVersion(self):
        return "6.4.0.293"


class Model:
    java = JavaModel()


class Client:
    def model(self, tag):
        assert tag == "m"
        return Model()


class Worker:
    def client(self):
        return Client()

    def operation_context(self, *args, **kwargs):
        return nullcontext()


def _request(operation, arguments, *, session_id="session", project_id=None, model_ref=None, key="k"):
    execution = {"session_id": session_id, "idempotency_key": key}
    if project_id is not None:
        execution["project_id"] = project_id
    if model_ref is not None:
        execution["model_ref"] = model_ref
    return {"operation": operation, "arguments": arguments, "execution": execution}


def _nested(outer, operation, arguments, *, session_id="session", project_id=None, model_ref=None, key="k"):
    execution = {"session_id": session_id, "idempotency_key": key}
    if project_id is not None:
        execution["project_id"] = project_id
    if model_ref is not None:
        execution["model_ref"] = model_ref
    return {"operation": outer, "arguments": {"operation_id": operation, "arguments": arguments}, "execution": execution}


def _create_project(daemon, label):
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": label, "workspace": label,
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
    })
    assert response["success"] is True, response
    return response["data"]["project"]["project_id"]


def test_model_adopt_adapts_server_model_tag_through_all_entrypoints(tmp_path, monkeypatch):
    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=tmp_path)
    daemon = ControlDaemon(tmp_path, service=service, registry={}, worker=Worker(), project_root=tmp_path)
    project_id = _create_project(daemon, "model-adopt-entrypoints")
    calls = []
    monkeypatch.setattr(daemon.backend, "adopt", lambda tag, project_id=None: calls.append((tag, project_id)) or {"success": True, "data": {"model_tag": tag}})
    try:
        direct = daemon.dispatch(_request("model.adopt", {"server_model_tag": "m"}, project_id=project_id, key="direct"))
        registry = daemon.dispatch(_nested("registry_call", "model.adopt", {"server_model_tag": "m"}, project_id=project_id, key="registry"))
        operation = daemon.dispatch(_nested("operation_call", "model.adopt", {"server_model_tag": "m"}, project_id=project_id, key="operation"))
        assert all(item["success"] for item in (direct, registry, operation)), (direct, registry, operation)
        assert calls == [("m", project_id), ("m", project_id), ("m", project_id)]
        assert _g2_registry.is_implemented("model.adopt")
        contract = _g2_registry.operation_describe("model.adopt")["runtime_dispatch_contract"]
        assert contract["engine_queue"] == "serialized_worker_queue"
        assert "server_model_tag" in contract["model_adopt"]

        for request in (
            _request("model.adopt", {"model_tag": "m"}, project_id=project_id, key="bad-alias"),
            _request("model.adopt", {"server_model_tag": "m", "model_ref": {}}, project_id=project_id, key="bad-identity"),
            _nested("registry_call", "model.adopt", {"server_model_tag": "m", "session_id": "spoof"}, project_id=project_id, key="bad-nested"),
        ):
            result = daemon.dispatch(request)
            assert result["success"] is False
            assert result["error"]["code"] == "INVALID_REQUEST"
        assert calls == [("m", project_id), ("m", project_id), ("m", project_id)]
    finally:
        daemon.close()


def test_direct_and_nested_model_adopt_share_project_aware_idempotency_identity(tmp_path, monkeypatch):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=workspace_root)
    daemon = ControlDaemon(tmp_path / "control", service=service, registry={}, worker=Worker(), project_root=workspace_root)
    created_project = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "same-semantic-request",
            "workspace": "same-semantic-request",
            "policy": {"permissions": ["inspect", "project_write"]},
        },
        "execution": {"request_id": "create-project", "idempotency_key": "create-project"},
    })
    project_id = created_project["data"]["project"]["project_id"]
    calls = []
    monkeypatch.setattr(
        daemon.backend,
        "adopt",
        lambda tag, project_id=None: calls.append((tag, project_id)) or {
            "success": True, "data": {"model_tag": tag}, "execution": {"project_id": project_id},
        },
    )
    execution = {
        "project_id": project_id,
        "session_id": "session",
        "request_id": "direct-request",
        "idempotency_key": "same-model-adopt",
    }
    try:
        direct = daemon.dispatch({
            "operation": "model.adopt",
            "arguments": {"server_model_tag": "m"},
            "execution": execution,
        })
        assert direct["success"] is True
        for outer in ("registry_call", "operation_call"):
            replay = daemon.dispatch({
                "operation": outer,
                "arguments": {"operation_id": "model.adopt", "arguments": {"server_model_tag": "m"}},
                "execution": {**execution, "request_id": f"{outer}-request"},
            })
            assert replay == direct
        assert calls == [("m", project_id)]
    finally:
        daemon.close()


def test_model_inspect_reads_identity_structure_solutions_and_scoped_dependencies(tmp_path, monkeypatch):
    from comsol_mcp import _model_ops

    monkeypatch.setattr(_model_ops, "_model_tree_data", lambda _model: {
        "components": ["comp1"], "component_details": [{"tag": "comp1", "geometries": ["geom1"],
            "meshes": ["mesh1"], "physics": ["emw"], "materials": ["mat1"]}],
        "parameters": ["lambda0"], "studies": ["std1"], "solutions": ["sol1"],
        "datasets": ["dset1"], "results": ["pg1"], "file_path": "/private/model.mph",
    })
    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=tmp_path)
    daemon = ControlDaemon(tmp_path, service=service, registry={}, worker=Worker(), project_root=tmp_path)
    project_id = _create_project(daemon, "model-inspect")
    daemon.backend.worker_identity = {
        "runtime_id": "model-inspect-runtime",
        "worker_instance_id": "model-inspect-worker",
        "connection_epoch": 1,
        "server_instance_id": "model-inspect-connection-epoch",
    }
    try:
        adopted = daemon.dispatch(_request("model.adopt", {"server_model_tag": "m"}, project_id=project_id, key="adopt-model"))
        assert adopted["success"] is True, adopted
        model_ref = adopted["execution"]["model_ref"]
        result = daemon.dispatch(_request("model.inspect", {"detail": "summary"}, project_id=project_id, model_ref=model_ref, key="inspect-summary"))
        assert result["success"] is True, result
        data = result["data"]
        assert data["model_identity"] == model_ref
        assert data["revision"] == 0
        assert data["structure"]["solutions"] == ["sol1"]
        assert data["dependency_inventory"]["file_resource_tags"] == ["file1", "file2"]
        assert data["dependency_inventory"]["external_dependencies"] == "NOT_EXHAUSTIVELY_ENUMERATED"
        assert data["comsol_version"] == "6.4.0.293"
        assert data["file_path"] == "OMITTED"
        assert "/private/model.mph" not in str(data)
        assert _g2_registry.is_implemented("model.inspect")

        structure_only = daemon.dispatch(_nested("operation_call", "model.inspect", {"detail": "structure"},
                                                  project_id=project_id, model_ref=model_ref, key="inspect-structure"))
        assert structure_only["success"] is True
        assert structure_only["data"]["structure"]["components"] == ["comp1"]
        assert structure_only["data"]["dependency_inventory"] is None

        denied = daemon.dispatch(_request("model.inspect", {"detail": "unknown"}, project_id=project_id, model_ref=model_ref, key="bad-detail"))
        assert denied["success"] is False
        assert denied["error"]["code"] == "INVALID_REQUEST"

        service.ledger.permissions.remove("inspect")
        unauthorized = daemon.dispatch(_request("model.inspect", {}, project_id=project_id, model_ref=model_ref, key="no-permission"))
        assert unauthorized["success"] is False
        assert unauthorized["error"]["code"] == "PERMISSION_DENIED"
    finally:
        daemon.close()


def test_resume_model_readback_uses_narrow_model_file_tag_adapter(monkeypatch):
    from types import SimpleNamespace

    from comsol_mcp import _model_ops
    from comsol_mcp._managed_backend import ManagedBackend

    calls = []
    model = SimpleNamespace(java=SimpleNamespace(
        getFileResourceTags=lambda: calls.append("getFileResourceTags") or ["res1", "res2"]))
    monkeypatch.setattr(_model_ops, "_model_tree_data", lambda _model: {
        "components": ["comp1"], "component_details": [], "studies": ["std1"],
        "solutions": [], "datasets": [], "results": [],
    })
    monkeypatch.setattr(_model_ops, "_parameter_rows", lambda _model: [])

    result = ManagedBackend._resume_model_readback(model)

    assert calls == ["getFileResourceTags"]
    assert result["state"]["file_resource_tags"] == ["res1", "res2"]
    assert result["scope"] == "structure+global_parameters+solutions+Model.FileResourceList"


def test_worker_file_tag_adapter_is_allowlisted_and_model_type_checked():
    from pathlib import Path

    source_path = Path(__file__).parents[1] / "comsol_mcp/worker_java/PersistentComsolWorker.java"
    source = source_path.read_text(encoding="utf-8")
    methods_start = source.index("Set<String> METHODS = new HashSet<>(Arrays.asList(")
    methods_end = source.index("));", methods_start)
    methods = source[methods_start:methods_end]

    assert '"getFileResourceTags"' in methods
    assert 'if ("getFileResourceTags".equals(method))' in source
    assert "if (!(target instanceof Model))" in source
    assert "((Model) target).file().tags()" in source


def test_preconnected_worker_binds_only_its_health_verified_endpoint():
    from comsol_mcp._managed_backend import ManagedBackend

    class FakeWorker:
        def __init__(self, connected, server):
            self.state = {"connected": connected, "server": server}
            self.connect_calls = []

        def health(self):
            return dict(self.state)

        def client(self):
            return self

        def connect(self, port, host):
            self.connect_calls.append((host, port))
            self.state = {"connected": True, "server": f"{host}:{port}"}

    endpoint = "127.0.0.1:5656"
    backend = object.__new__(ManagedBackend)
    backend.endpoint_key = None
    backend.worker = FakeWorker(True, endpoint)
    health = backend._bind_confirmed_worker_endpoint(endpoint, 5656, "127.0.0.1")
    assert health == {"connected": True, "server": endpoint}
    assert backend.endpoint_key == endpoint
    assert backend.worker.connect_calls == []

    for reported_server in ("127.0.0.1:5657", ""):
        backend = object.__new__(ManagedBackend)
        backend.endpoint_key = None
        backend.worker = FakeWorker(True, reported_server)
        with pytest.raises(ExecutionContractError):
            backend._bind_confirmed_worker_endpoint(endpoint, 5656, "127.0.0.1")
        assert backend.endpoint_key is None
        assert backend.worker.connect_calls == []

    backend = object.__new__(ManagedBackend)
    backend.endpoint_key = None
    backend.worker = FakeWorker(False, "")
    health = backend._bind_confirmed_worker_endpoint(endpoint, 5656, "127.0.0.1")
    assert health == {"connected": True, "server": endpoint}
    assert backend.endpoint_key == endpoint
    assert backend.worker.connect_calls == [("127.0.0.1", 5656)]
