from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
)
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._host_scoped_provider import (
    CONFIG_SCHEMA,
    HostScopedProvider,
    destination_registration_sha256,
    load_host_scoped_provider,
    scope_sha256,
)


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _expiry(*, delta: timedelta = timedelta(hours=1)) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant(project_id: str, action: str, authorization_ref: str, scope: dict, *, expiry=None) -> dict:
    return {"project_id": project_id, "action": action,
            "authorization_ref_sha256": hashlib.sha256(authorization_ref.encode()).hexdigest(),
            "scope_sha256": scope_sha256(project_id, action, scope),
            "expires_at_utc": expiry or _expiry()}


def _write_config(directory: Path, config: dict) -> Path:
    _private_dir(directory)
    path = directory / "host-provider.json"
    path.write_text(json.dumps(config, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_provider_exact_scopes_expiry_pinned_config_and_local_noclobber_sink(tmp_path):
    project_id = "project-test"
    sink = _private_dir(tmp_path / "local-sink")
    destination_ref = "local-test-sink"
    registration = destination_registration_sha256(project_id, destination_ref, sink)
    artifacts = [
        {"artifact_id": "b" * 64, "sha256": "d" * 64, "size_bytes": 27},
        {"artifact_id": "a" * 64, "sha256": "c" * 64, "size_bytes": 19},
    ]
    publish_scope = {"project_id": project_id, "action": "artifact.publish",
                     "destination_ref": destination_ref,
                     "destination_registration_sha256": registration,
                     "artifacts": artifacts}
    model_ref = {"schema_version": 1, "session_id": "session-1", "server_instance_id": "server-1",
                 "model_tag": "mcp1", "generation": 1}
    close_scope = {"model_ref": model_ref, "expected_revision": 4, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [
                  _grant(project_id, "artifact.publish", "publish-once", publish_scope),
                  _grant(project_id, "model.close", "close-once", close_scope),
                  _grant(project_id, "model.close", "expired-close", close_scope,
                         expiry=_expiry(delta=timedelta(seconds=-1))),
              ]}
    config_path = _write_config(tmp_path / "host-config", config)
    provider = load_host_scoped_provider(config_path)
    try:
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "publish-once") is True
        reordered = {**publish_scope, "artifacts": list(reversed(artifacts))}
        assert provider.verify_scope(project_id, "artifact.publish", reordered, "publish-once") is True
        changed = {**publish_scope, "artifacts": [dict(artifacts[0], size_bytes=28), artifacts[1]]}
        assert provider.verify_scope(project_id, "artifact.publish", changed, "publish-once") is False
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "wrong-ref") is False
        assert provider.verify_scope(project_id, "artifact.publish", publish_scope, "close-once") is False
        assert provider.verify_scope(project_id, "model.close", close_scope, "close-once") is True
        assert provider.verify_scope(project_id, "model.close",
                                     {**close_scope, "expected_revision": 5}, "close-once") is False
        assert provider.verify_scope(project_id, "model.close", close_scope, "expired-close") is False

        destination = provider.resolve_destination(project_id, destination_ref)
        payload = b"registered local sink bytes"
        artifact_id = "e" * 64
        digest = hashlib.sha256(payload).hexdigest()
        receipt = destination["publish_chunks"](
            destination, artifact_id + ".csv", iter((payload[:8], b"", payload[8:])),
            expected_sha256=digest, expected_size=len(payload), artifact_id=artifact_id,
        )
        assert receipt["status"] == "PUBLISHED"
        assert receipt["no_clobber"] is True and receipt["readback_verified"] is True
        assert (sink / (artifact_id + ".csv")).read_bytes() == payload
        with pytest.raises(ExecutionContractError) as duplicate:
            destination["publish_chunks"](
                destination, artifact_id + ".csv", iter((payload,)),
                expected_sha256=digest, expected_size=len(payload), artifact_id=artifact_id,
            )
        assert duplicate.value.code == "DESTINATION_EXISTS"

        # Same-inode byte modification is detected before consuming any grant.
        with config_path.open("ab") as handle:
            handle.write(b" ")
        with pytest.raises(ExecutionContractError) as tampered:
            provider.verify_scope(project_id, "model.close", close_scope, "close-once")
        assert tampered.value.code == "AUTHORIZATION_STATE_UNKNOWN"
    finally:
        provider.close()


def test_provider_refuses_insecure_config_and_sink_replacement(tmp_path):
    project_id = "project-test"
    sink = _private_dir(tmp_path / "registered-sink")
    destination_ref = "sink"
    scope = {"model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "i",
                           "model_tag": "m", "generation": 1},
             "expected_revision": 0, "destination_ref": None}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [_grant(project_id, "model.close", "ref", scope)]}
    config_path = _write_config(tmp_path / "host-config", config)
    os.chmod(config_path, 0o640)
    with pytest.raises(ExecutionContractError) as insecure:
        load_host_scoped_provider(config_path)
    assert insecure.value.code == "HOST_PROVIDER_CONFIG_INVALID"
    os.chmod(config_path, 0o600)

    provider = load_host_scoped_provider(config_path)
    parked = tmp_path / "registered-sink-parked"
    sink.rename(parked)
    replacement = _private_dir(sink)
    try:
        with pytest.raises(ExecutionContractError) as changed:
            provider.resolve_destination(project_id, destination_ref)
        assert changed.value.code == "DESTINATION_STATE_UNKNOWN"
    finally:
        provider.close()
    assert replacement.is_dir() and parked.is_dir()


def test_provider_rejects_duplicate_json_keys_and_schema_extras(tmp_path):
    config_dir = _private_dir(tmp_path / "host-config")
    path = config_dir / "duplicate.json"
    path.write_text('{"schema":"x","schema":"y","destinations":[],"grants":[]}', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ExecutionContractError) as duplicate:
        load_host_scoped_provider(path)
    assert duplicate.value.code == "HOST_PROVIDER_CONFIG_INVALID"

    extra = {"schema": CONFIG_SCHEMA, "destinations": [], "grants": [], "allow_all": True}
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(ExecutionContractError) as schema:
        load_host_scoped_provider(path)
    assert schema.value.code == "HOST_PROVIDER_CONFIG_INVALID"


def test_control_daemon_loads_host_config_and_publishes_to_registered_local_sink(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    endpoint = "127.0.0.1:24971"
    raw_authorization_ref = "only-in-test-memory"
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = _private_dir(tmp_path / "projects")
    control_home = tmp_path / "control"
    daemon = ControlDaemon(control_home, project_root=projects)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "provider-project", "workspace": "provider-project",
                          "policy": {"permissions": ["inspect", "project_write", "host_control"]}},
            "execution": {"request_id": "provider-project-create",
                          "idempotency_key": "provider-project-create"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        project_root = projects / "provider-project"
        source = project_root / "reports" / "source.csv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x,y\n0,1\n")
        daemon.backend.endpoint_key = endpoint
        registered = register_project_artifact(
            project_root, daemon.store, "reports/source.csv", project_id=project_id,
            role="result_data", classification="project_local",
            host_identity=local_artifact_host_identity(),
            engine_host_identity=local_engine_host_identity(endpoint),
            request_id="provider-artifact-register", registering_operation_id="artifact.register",
            artifact_type="text/csv", format_version="test-csv/1",
        )
    finally:
        daemon.close()

    sink = _private_dir(tmp_path / "registered-sink")
    destination_ref = "local-provider-sink"
    registration = destination_registration_sha256(project_id, destination_ref, sink)
    scope = {"project_id": project_id, "action": "artifact.publish",
             "destination_ref": destination_ref,
             "destination_registration_sha256": registration,
             "artifacts": [{"artifact_id": registered["artifact_id"],
                            "sha256": registered["sha256"],
                        "size_bytes": registered["size"]}]}
    config = {"schema": CONFIG_SCHEMA,
              "destinations": [{"project_id": project_id, "destination_ref": destination_ref,
                                "sink_root": str(sink)}],
              "grants": [_grant(project_id, "artifact.publish", raw_authorization_ref, scope)]}
    config_path = _write_config(tmp_path / "host-config", config)

    daemon = ControlDaemon(control_home, project_root=projects, host_provider_config=config_path)
    daemon.backend.endpoint_key = endpoint
    try:
        denied = daemon.dispatch({
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": destination_ref, "authorization_ref": "wrong-ref"},
            "execution": {"project_id": project_id, "request_id": "provider-publish-denied",
                          "idempotency_key": "provider-publish-denied"},
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert list(sink.iterdir()) == []
        denied_record = daemon.store.get_operation(denied["execution"]["operation_id"])
        persisted = denied_record["metadata"]["arguments"]
        assert "authorization_ref" not in persisted
        assert persisted["authorization_ref_sha256"] == hashlib.sha256(b"wrong-ref").hexdigest()

        request = {
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": destination_ref,
                          "authorization_ref": raw_authorization_ref},
            "execution": {"project_id": project_id, "request_id": "provider-publish-valid",
                          "idempotency_key": "provider-publish-valid"},
        }
        published = daemon.dispatch(request)
        assert published["success"] is True, published
        receipt = published["data"]["published"][0]["provider_receipt"]
        assert receipt["no_clobber"] is True and receipt["readback_verified"] is True
        output = sink / (registered["artifact_id"] + ".csv")
        assert output.read_bytes() == source.read_bytes()
        assert hashlib.sha256(output.read_bytes()).hexdigest() == registered["sha256"]
        operation = daemon.store.get_operation(published["execution"]["operation_id"])
        persisted = operation["metadata"]["arguments"]
        assert "authorization_ref" not in persisted
        assert persisted["authorization_ref_sha256"] == hashlib.sha256(raw_authorization_ref.encode()).hexdigest()
        assert daemon.backend.scoped_host_provider.config_sha256
    finally:
        daemon.close()


def test_control_daemon_serve_installs_host_provider_config_from_startup_argument(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = {}

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    class FakeDaemon:
        def __init__(self, _home, *, host_provider_config=None):
            observed["config"] = host_provider_config
            self._control_singleton_lock_held = False

    class FakeServer:
        server_port = 24972
        daemon_threads = False
        def serve_forever(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: FakeServer())
    monkeypatch.setattr(daemon_module, "process_identity", lambda _pid: {"start_epoch_ms": 100})
    config = tmp_path / "managed-by-host.json"
    daemon_module.serve(tmp_path / "serve-home", host_provider_config=config)
    assert observed["config"] == config


def test_control_daemon_serve_reads_host_provider_config_from_environment(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = {}

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    class FakeDaemon:
        def __init__(self, _home, *, host_provider_config=None):
            observed["config"] = host_provider_config
            self._control_singleton_lock_held = False

    class FakeServer:
        server_port = 24973
        daemon_threads = False
        def serve_forever(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: FakeServer())
    monkeypatch.setattr(daemon_module, "process_identity", lambda _pid: {"start_epoch_ms": 100})
    config = tmp_path / "host-provider-from-env.json"
    monkeypatch.setenv("COMSOL_MCP_HOST_PROVIDER_CONFIG", str(config))
    daemon_module.serve(tmp_path / "serve-home-from-env")
    assert observed["config"] == str(config)
