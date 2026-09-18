"""Control-plane tests for the persistent Java Worker; they never attach COMSOL."""
from __future__ import annotations

import json
import socket
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import pytest

from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerPaths, PersistentJavaWorker, RemoteClient, RemoteModel


COMSOL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JDK11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    if not (COMSOL_ROOT / "bin/comsolclientpath.txt").is_file() or not (JDK11 / "bin/javac").is_file():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    instance = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path_factory.mktemp("worker-state"))
    instance.start()
    yield instance
    instance.close()


def test_java_worker_is_loopback_authenticated_and_reports_health(worker):
    host, port = worker.endpoint
    assert host == "127.0.0.1"
    health = worker.health()
    assert health["status"] == "HEALTHY"
    assert health["instance_id"]
    assert "token" not in worker.runtime_metadata()
    with socket.create_connection((host, port), timeout=1) as conn:
        stream = conn.makefile("rwb")
        stream.write(b'{"token":"wrong"}\n'); stream.flush()
        reply = json.loads(stream.readline())
    assert reply["code"] == "AUTH_FAILED"


def test_java_worker_keeps_original_request_queryable_after_nonblocking_submit(worker):
    request_id = "control-only-not-connected"
    submitted = worker.submit("model", {"tag": "not-connected"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    assert submitted["request_id"] == request_id
    deadline = time.monotonic() + 2
    status = worker.status(request_id)
    while status["status"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
        time.sleep(0.02); status = worker.status(request_id)
    assert status["status"] == "FAILED"
    assert status["failure"]["code"] == "ENGINE_CALL_FAILED"
    assert worker.health(timeout_s=1)["status"] == "HEALTHY"


def test_java_worker_persists_private_endpoint_metadata(worker, tmp_path_factory):
    # The endpoint has the token needed to reconnect after a control restart,
    # so it must be private and callers must never create a replacement to
    # replay an unknown request.
    endpoint = worker.state_dir / "worker_endpoint.json"
    saved = json.loads(endpoint.read_text())
    assert saved["host"] == "127.0.0.1"
    assert saved["port"] == worker.endpoint[1]
    assert len(saved["token"]) >= 32
    assert endpoint.stat().st_mode & 0o077 == 0


def test_java_worker_reconnects_existing_endpoint_without_spawning_or_replaying(worker):
    reattached = PersistentJavaWorker(worker.paths, state_dir=worker.state_dir)
    observed = reattached.start()
    assert observed["status"] == "HEALTHY"
    assert reattached.endpoint == worker.endpoint
    assert reattached.generation == worker.generation
    # It attached to the existing child, therefore close must not own or kill it.
    reattached.close()
    assert worker.health(timeout_s=1)["status"] == "HEALTHY"


def test_dead_owned_worker_increments_generation_before_replacement(tmp_path):
    if not (COMSOL_ROOT / "bin/comsolclientpath.txt").is_file() or not (JDK11 / "bin/javac").is_file():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    first = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path)
    first.start(); previous_generation = first.generation; first.close()
    endpoint = tmp_path / "worker_endpoint.json"
    saved = json.loads(endpoint.read_text())
    # A stale endpoint's PID is intentionally made impossible here. A PID that
    # is alive but cannot serve its recorded endpoint is a reconciliation
    # blocker, never a reason to start another worker.
    saved["pid"] = 999999
    endpoint.write_text(json.dumps(saved))
    endpoint.chmod(0o600)
    replacement = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11), state_dir=tmp_path)
    try:
        replacement.start()
        assert replacement.generation == previous_generation + 1
    finally:
        replacement.close()


def test_same_request_id_with_different_body_is_rejected_without_replay(worker):
    request_id = "idempotency-conflict-control-only"
    worker.submit("model", {"tag": "first"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    conflict = worker.submit("model", {"tag": "second"}, request_id=request_id, queue_timeout_s=0, rpc_timeout_s=1)
    assert conflict["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert worker.status(request_id)["request_id"] == request_id


def test_unknown_command_is_rejected_and_parallel_control_queries_remain_responsive(worker):
    unknown = worker._request({"type": "not_a_worker_command"}, timeout_s=1)
    assert unknown["code"] == "UNKNOWN_COMMAND"
    codec = worker._request({"type": "codec_selftest"}, timeout_s=1)
    assert codec["result"] == {"kind": "map", "nested": {"value": 7}, "array": ["x", 2]}
    reflection = worker._request({"type": "reflection_selftest"}, timeout_s=1)
    assert reflection["result"]["duplicate_interface_tag"] == "resolved"
    assert reflection["result"]["numerical_allowed"] is True
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies = list(executor.map(lambda _: worker.health(timeout_s=1), range(16)))
    assert all(reply["status"] == "HEALTHY" for reply in replies)


def test_operation_context_publishes_request_linkage_and_redacts_credentials(worker):
    events: list[dict] = []
    with worker.operation_context("op-worker-link", on_request_event=events.append):
        worker.submit("model", {"tag": "no-engine", "password": "do-not-log"},
                      request_id="linked-control-only", queue_timeout_s=0, rpc_timeout_s=1)
    assert events[0]["operation_id"] == "op-worker-link"
    assert events[0]["request_id"] == "linked-control-only"
    assert len(events[0]["request_hash"]) == 64
    assert events[0]["metadata"]["password"] == "<redacted>"
    assert any(event["phase"] == "observed" for event in events)


def test_generation_sync_updates_private_reconnect_record(tmp_path):
    endpoint = tmp_path / "worker_endpoint.json"
    endpoint.write_text(json.dumps({"generation": 1, "token": "x" * 32, "pid": 1, "port": 1}))
    endpoint.chmod(0o600)
    probe = PersistentJavaWorker(JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path), state_dir=tmp_path)
    probe._sync_generation({"result": {"generation": 2}})
    assert probe.generation == 2
    assert json.loads(endpoint.read_text())["generation"] == 2


class _SaveModel(RemoteModel):
    def __init__(self, project_root: Path, current_path: Path) -> None:
        self._worker = SimpleNamespace(generation=1, paths=JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=project_root))
        self._generation, self._handle, self.java_type = 1, "test", "test"
        self.current_path, self.raw_paths = current_path, []

    def _call(self, method, *args, **kwargs):
        assert method == "getFilePath"
        return str(self.current_path)

    def _raw_save(self, path: str, **kwargs):
        self.raw_paths.append(Path(path))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("model.txt", "complete candidate")


def test_remote_model_save_never_uses_empty_path_as_in_place_bypass(tmp_path):
    destination = tmp_path / "current.mph"
    model = _SaveModel(tmp_path, destination)
    published = model.save("")
    assert published["path"] == str(destination)
    assert model.raw_paths and model.raw_paths[0] != destination
    assert model.raw_paths[0].name.startswith(".current.mph.")
    assert destination.is_file()


def test_remote_model_save_rejects_paths_outside_configured_project_root(tmp_path):
    model = _SaveModel(tmp_path, tmp_path / "current.mph")
    with pytest.raises(Exception):
        model.save(str(tmp_path.parent / "outside.mph"))
    assert not model.raw_paths


def test_remote_client_load_serializes_path_objects_as_strings(tmp_path):
    class Worker:
        generation = 1
        def __init__(self): self.payload = None
        def submit(self, kind, payload, **_kwargs):
            self.payload = (kind, payload)
            return {"ok": True, "status": "SUCCEEDED", "result": {"$worker_handle": "model", "generation": 1}}
    worker = Worker()
    RemoteClient(worker).load(tmp_path / "model.mph", tag="loaded")
    assert worker.payload == ("modelutil", {"method": "load", "args": ["loaded", str(tmp_path / "model.mph")]})


def test_two_workers_cannot_own_the_same_global_endpoint_lock(tmp_path):
    if not (COMSOL_ROOT / "bin/comsolclientpath.txt").is_file() or not (JDK11 / "bin/javac").is_file():
        pytest.skip("COMSOL 6.4/JDK 11 local Worker build environment unavailable")
    lock_root = tmp_path / "global-endpoint-locks"
    paths = JavaWorkerPaths(COMSOL_ROOT, JDK11, project_root=tmp_path, global_lock_root=lock_root)
    first = PersistentJavaWorker(paths, state_dir=tmp_path / "first")
    second = PersistentJavaWorker(paths, state_dir=tmp_path / "second")
    try:
        first.start(); second.start()
        held = first.submit("lock_selftest", {"port": 65001}, request_id="first-lock", rpc_timeout_s=1)
        rejected = second.submit("lock_selftest", {"port": 65001}, request_id="second-lock", rpc_timeout_s=1)
        assert held["status"] == "SUCCEEDED"
        assert rejected["status"] == "FAILED"
        assert rejected["failure"]["code"] == "ENGINE_BUSY"
    finally:
        second.close(); first.close()
