"""Real attach producers and strict consumer, with a synthetic Worker boundary."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_installation import runtime_id_for_root
from comsol_mcp._session_context import CanonicalSocket, SessionRuntimeConfig
from comsol_mcp._g3_managed_models import _test_runtime_observation


class Worker:
    def __init__(self, root):
        self.paths = SimpleNamespace(comsol_root=root)
        self.meta = {"instance_id": "worker-original", "generation": 1,
                     "connected": False, "server": ""}
        self.version = "COMSOL Multiphysics 6.4 (开发版本: 293)"
        self.version_calls = 0
        self.after_version = None

    def start(self):
        return self.health()

    def health(self):
        return dict(self.meta)

    def runtime_metadata(self):
        return self.health()

    def operation_context(self, *args, **kwargs):
        return nullcontext()

    def client(self):
        return self

    def connect(self, port, host, **kwargs):
        self.meta.update(connected=True, server=f"{host}:{port}", generation=self.meta["generation"] + 1)
        return {**self.meta, "engine_version": "6.4.0.293"}

    def submit(self, kind, payload, **kwargs):
        assert kind == "modelutil" and payload["method"] == "getComsolVersion"
        self.version_calls += 1
        if isinstance(self.version, Exception):
            raise self.version
        if self.after_version:
            self.after_version()
        return {"ok": True, "status": "SUCCEEDED", "result": self.version}

    def getComsolVersion(self):
        return self.submit("modelutil", {"method": "getComsolVersion"})["result"]

    def backend_snapshot(self, tag):
        return {**self.meta, "server_instance_id": self.meta["server"],
                "model_tag": tag, "fingerprint": "synthetic-original-model", "external_event_counter": 0}


def installation(root):
    root.mkdir()
    (root / "readme.txt").write_text("COMSOL 6.4.0.293 README\n")
    launcher = root / "bin/macarm64/comsollauncher"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"synthetic executable header")
    return root


@pytest.fixture(params=["legacy", "registered"])
def attached(request, tmp_path, monkeypatch):
    root = installation(tmp_path / "comsol")
    worker = Worker(root)
    store = OperationStore(tmp_path / "operations.sqlite3")
    backend = ManagedBackend(tmp_path / "control", store, worker=worker, project_root=tmp_path,
        session_worker_factory=lambda *_args: worker,
        session_peer_observer=lambda *_args: CanonicalSocket("127.0.0.1", 25432))
    if request.param == "legacy":
        from comsol_mcp._server import session_server as srv
        for name in ("_remote_client_factory", "_client", "_client_connected", "_connected_host", "_connected_port", "_server_started_by_mcp"):
            monkeypatch.setattr(srv, name, getattr(srv, name))
        result = backend.connect({"host": "127.0.0.1", "port": 25432}, "attach-legacy", lambda _: None)
        assert result["success"] is True
        expected_label = "runtime-worker-original"
    else:
        config = SessionRuntimeConfig("named-catalog-label", "6.4.0.293", root,
            tmp_path / "java/bin/java", (tmp_path / "api.jar",), tmp_path / "prefs", tmp_path / "sessions")
        backend.connect_session(runtime=config, project_id="project-original", session_id="session-original",
            host="127.0.0.1", port=25432, operation_id="attach-registered", request_id="attach-request",
            event_callback=lambda _: None, project_permissions={"inspect", "project_write"})
        expected_label = config.runtime_id
    backend.service.bind_model("owned", ownership="mcp_owned")
    try:
        yield backend, worker, expected_label, root
    finally:
        backend.close()
        store.close()


def record_and_context(backend, observation):
    service = backend.service
    ref = service.ledger._models["owned"].ref.as_dict()
    identity = backend.worker_identity
    record = {"metadata": {"runtime_binding": {"worker_instance_id": identity["worker_instance_id"],
        "worker_epoch": identity["connection_epoch"], "endpoint": backend.endpoint_key,
        "project_id": "project-original", "session_id": service.ledger.session_id}},
        "result": {"execution": {"runtime_observation": observation, "model_ref": ref}}}
    context = {"backend": backend, "execution_service": service, "project_id": "project-original"}
    return record, context


def test_both_attach_paths_preserve_namespace_and_reach_strict_consumer(attached):
    backend, worker, expected_label, root = attached
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "OBSERVED", observation
    assert observation["comsol_version"] == "6.4.0.293"
    assert observation["installation_runtime_id"] == runtime_id_for_root(root)
    assert observation["runtime_identity"]["runtime_id"] == expected_label
    assert expected_label != observation["installation_runtime_id"]
    assert worker.version_calls == 1
    record, context = record_and_context(backend, observation)
    assert _test_runtime_observation(context, record, expected_version="6.4.0.293", require_current_worker=True)
    backend.persist()
    assert backend.store.get_metadata("sessions", backend.service.ledger.session_id)["worker_identity"]["runtime_id"] == expected_label


@pytest.mark.parametrize("field", ["runtime_id", "worker_instance_id", "connection_epoch", "endpoint", "server_instance_id", "installation_runtime_id"])
def test_coordinated_mutable_identity_tampering_cannot_replace_original_binding(attached, tmp_path, field):
    backend, worker, _, _ = attached
    if field == "installation_runtime_id":
        worker.paths.comsol_root = installation(tmp_path / "wrong-installation")
    elif field == "worker_instance_id":
        worker.meta["instance_id"] = backend.worker_identity[field] = "wrong-worker"
    elif field == "connection_epoch":
        worker.meta["generation"] = backend.worker_identity[field] = 99
    elif field == "endpoint":
        worker.meta["server"] = backend.endpoint_key = backend.worker_identity[field] = "127.0.0.1:25433"
    elif field == "server_instance_id":
        backend.service.ledger.server_instance_id = backend.worker_identity[field] = "wrong-server"
    else:
        backend.worker_identity[field] = "wrong-catalog"
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "UNVERIFIED"
    assert field in observation["mismatched_fields"]
    assert worker.version_calls == 0
    record, context = record_and_context(backend, observation)
    assert _test_runtime_observation(context, record, expected_version="6.4.0.293", require_current_worker=True) is None


@pytest.mark.parametrize("version,reason", [("6.4.0.294", "RUNTIME_VERSION_MISMATCH"),
    (RuntimeError("synthetic SDK failure"), "RUNTIME_VERSION_UNAVAILABLE")])
def test_wrong_build_or_failed_version_call_remains_unverified(attached, version, reason):
    backend, worker, _, _ = attached
    worker.version = version
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "UNVERIFIED"
    assert observation["reason_code"] == reason


def test_identity_change_during_version_read_is_rejected(attached):
    backend, worker, _, _ = attached
    worker.after_version = lambda: worker.meta.update(generation=99)
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "UNVERIFIED"
    assert "connection_epoch" in observation["mismatched_fields"]


def test_current_identity_dict_alone_cannot_mint_observation(attached):
    backend, _, _, _ = attached
    backend._runtime_observation_binding = None
    assert backend._code_execution_runtime_observation()["status"] == "UNVERIFIED"


def test_coordinated_build_and_metadata_change_cannot_replace_attach_build(attached):
    backend, worker, _, root = attached
    (root / "readme.txt").write_text("COMSOL 6.4.0.294 README\n")
    worker.version = "6.4.0.294"
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "UNVERIFIED"
    assert observation["reason_code"] == "RUNTIME_VERSION_MISMATCH"


def test_missing_local_build_never_infers_it_from_sdk_version(attached):
    backend, _, _, root = attached
    (root / "readme.txt").unlink()
    observation = backend._code_execution_runtime_observation()
    assert observation["status"] == "UNVERIFIED"
    assert observation["reason_code"] == "RUNTIME_VERSION_UNAVAILABLE"


def test_strict_registration_consumer_rejects_wrong_installation_and_replaced_worker(attached):
    backend, worker, _, _ = attached
    observed = backend._code_execution_runtime_observation()
    record, context = record_and_context(backend, observed)
    assert _test_runtime_observation(context, record, expected_version="6.4.0.293", require_current_worker=True)
    observed["installation_runtime_id"] = "comsol-install:wrong-root"
    assert _test_runtime_observation(context, record, expected_version="6.4.0.293", require_current_worker=True) is None
    observed["installation_runtime_id"] = backend._runtime_observation_binding.installation_runtime_id
    backend.worker = Worker(worker.paths.comsol_root)
    backend.worker.meta = dict(worker.meta)
    assert _test_runtime_observation(context, record, expected_version="6.4.0.293", require_current_worker=True) is None
