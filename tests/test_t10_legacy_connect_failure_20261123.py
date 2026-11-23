"""Legacy connection failure boundaries using a synthetic Worker and real store."""

import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import comsol_mcp._managed_backend as managed_backend
import comsol_mcp._server as server
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._runtime_state import runtime_state_snapshot


class FakeWorker:
    def __init__(self):
        self.started = False
        self.connected = False
        self.connect_calls = 0
        self.server = None

    def start(self):
        self.started = True

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def health(self):
        return {"connected": self.connected, "server": self.server}

    def runtime_metadata(self):
        return {"instance_id": "legacy-worker", "generation": 4,
                "connected": self.connected, "server": self.server}

    def client(self):
        return self

    def connect(self, port, host, **_kwargs):
        self.connect_calls += 1
        self.connected = True
        self.server = f"{host}:{port}"
        return {"connected": True, "server": self.server}


@pytest.fixture
def legacy_daemon(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    monkeypatch.setenv("COMSOL_ROOT", str(tmp_path / "synthetic-comsol"))
    monkeypatch.setenv("COMSOL_JAVA_HOME", str(tmp_path / "synthetic-java"))
    monkeypatch.setattr(managed_backend, "socket", SimpleNamespace(gethostbyname=lambda host: host))
    for name in ("_remote_client_factory", "_client", "_client_connected", "_connected_host",
                 "_connected_port", "_server_started_by_mcp"):
        monkeypatch.setattr(server, name, getattr(server, name))
    worker = FakeWorker()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, worker=worker,
                           registry={"server_connect": lambda _arguments: None})
    monkeypatch.setattr(daemon.backend, "_bind_runtime_observation_context", lambda **_kwargs: None)
    try:
        yield daemon, worker
    finally:
        daemon.close()


def test_post_connect_authority_failure_keeps_worker_and_marks_job_unknown(legacy_daemon):
    daemon, worker = legacy_daemon
    grants = {"inspect", "project_write"}
    daemon.project_authority.permission_provider = lambda: grants
    project = daemon.project_authority.dispatch("project.create", {
        "label": "synthetic", "workspace": "project",
        "policy": {"permissions": ["inspect", "project_write"]},
    })["data"]["project"]
    endpoint = "127.0.0.1:56388"
    session_id = "session-" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]
    server_id = hashlib.sha256(f"{endpoint}:legacy-worker:4".encode()).hexdigest()
    identity = {"runtime_id": "runtime-legacy-worker", "worker_instance_id": "legacy-worker",
                "connection_epoch": 4, "server_instance_id": server_id, "endpoint": endpoint}
    ledger = SessionLedger(session_id, server_id)
    ref = ledger.bind_model("legacy-model", fingerprint="synthetic")
    snapshot = runtime_state_snapshot(ledger, identity)
    snapshot["models"][ref.model_tag]["ref"]["schema_version"] = True
    old_ref = snapshot["models"][ref.model_tag]["ref"]
    old_key = json.dumps(old_ref, sort_keys=True, separators=(",", ":"))
    rows = (("sessions", session_id), ("runtimes", identity["runtime_id"]),
            ("revisions", old_key))
    daemon.store.put_metadata("sessions", session_id, snapshot)
    daemon.store.put_metadata("runtimes", identity["runtime_id"], snapshot)
    daemon.store.put_metadata("revisions", old_key, {"model_ref": old_ref,
                              "project_id": project["project_id"], "revision": 0,
                              "attribution": "PROJECT_BOUND"})
    before = [daemon.store.get_metadata(table, key) for table, key in rows]
    grants.remove("project_write")

    result = daemon.dispatch({"operation": "server_connect",
                              "arguments": {"host": "127.0.0.1", "port": 56388},
                              "execution": {"request_id": "connect", "idempotency_key": "connect"}})

    assert worker.started and worker.connected and worker.connect_calls == 1
    assert daemon.backend.worker is worker and daemon.backend.service is not None
    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["error"]["details"]["cause_code"] == "PERMISSION_DENIED"
    assert daemon.store.job(result["execution"]["job_id"])["status"] == "UNKNOWN"
    assert [daemon.store.get_metadata(table, key) for table, key in rows] == before
    assert not daemon.store.db.in_transaction


def test_invalid_endpoint_fails_before_worker_start(legacy_daemon):
    daemon, worker = legacy_daemon
    result = daemon.dispatch({"operation": "server_connect",
                              "arguments": {"host": "127.0.0.1", "port": 0},
                              "execution": {"request_id": "invalid", "idempotency_key": "invalid"}})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert not worker.started and worker.connect_calls == 0
