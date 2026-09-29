from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import threading
import time

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionContextMissing,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    SessionSchedulerClosed,
    current_session_context,
    session_state_directory,
)
from comsol_mcp._session_lifecycle import SessionLifecycleProjectConflict, new_lifecycle_record


def _daemon(tmp_path: Path, *, registry=None) -> ControlDaemon:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True)
    return ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        registry=registry if registry is not None else {},
    )


def _create_project(daemon: ControlDaemon, label: str) -> dict:
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": label,
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
    })
    assert response["success"] is True, response
    return response["data"]["project"]


def _record(daemon: ControlDaemon, project_id: str, session_id: str, *, state="CONNECTED") -> dict:
    return daemon.session_lifecycle.save(new_lifecycle_record(
        project_id=project_id,
        session_id=session_id,
        state=state,
        runtime_id="comsol-install:%2Fopt%2Fcomsol64",
        endpoint={"host": "127.0.0.1", "port": 2046},
        client_state="CONNECTED" if state == "CONNECTED" else "DISCONNECTED",
        server_state="SHARED",
        server_ownership="shared",
        worker_instance_id="worker-fixture",
        worker_epoch=3,
        server_instance_id="server-connection-epoch-only",
        health={"status": "HEALTHY", "observed_at": "2026-09-27T00:00:00Z", "source": "cached-worker-health"},
    ))


def test_session_lifecycle_reads_are_project_scoped_and_wrapper_equivalent(tmp_path):
    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "session-reads")
        _record(daemon, project["project_id"], "session-a")

        direct = daemon.dispatch({
            "operation": "session.list",
            "arguments": {"project_id": project["project_id"], "filter": {}},
            "execution": {},
        })
        wrapped = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "session.list", "arguments": {"filter": {}}},
            "execution": {"project_id": project["project_id"]},
        })
        assert direct["success"] is True
        assert wrapped["success"] is True
        assert direct["data"] == wrapped["data"]
        assert direct["data"]["count"] == 1
        assert direct["data"]["sessions"][0]["lifecycle"]["session_id"] == "session-a"
        assert direct["data"]["sessions"][0]["runtime_live"] is False

        inspected = daemon.dispatch({
            "operation": "session.inspect",
            "arguments": {"project_id": project["project_id"], "session_id": "session-a"},
            "execution": {},
        })
        assert inspected["success"] is True
        assert inspected["data"]["lifecycle"]["server_instance_id"] == "server-connection-epoch-only"
        assert inspected["data"]["runtime_live"] is False

        health = daemon.dispatch({
            "operation": "session.health",
            "arguments": {"project_id": project["project_id"], "session_id": "session-a"},
            "execution": {},
        })
        assert health["success"] is True
        assert health["data"]["freshness"] == "CACHED"
        assert health["data"]["worker_rpc_performed"] is False
    finally:
        daemon.close()


def test_session_inspect_rejects_foreign_project_and_unknown_fields(tmp_path):
    daemon = _daemon(tmp_path)
    try:
        owner = _create_project(daemon, "session-owner")
        foreign = _create_project(daemon, "session-foreign")
        _record(daemon, owner["project_id"], "private-session")

        foreign_response = daemon.dispatch({
            "operation": "session.inspect",
            "arguments": {"project_id": foreign["project_id"], "session_id": "private-session"},
            "execution": {},
        })
        assert foreign_response["success"] is False
        assert foreign_response["error"]["code"] == "PROJECT_IDENTITY_MISMATCH"

        malformed = daemon.dispatch({
            "operation": "session.list",
            "arguments": {"project_id": owner["project_id"], "secret": "must-not-be-ignored"},
            "execution": {},
        })
        assert malformed["success"] is False
        assert malformed["error"]["code"] == "INVALID_REQUEST"

        already_active = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {
                "project_id": owner["project_id"],
                "idempotency_key": "connect-1",
                "runtime_id": "comsol-install:%2Fopt%2Fcomsol64",
                "endpoint": {"host": "127.0.0.1", "port": 2046},
            },
            "execution": {},
        })
        assert already_active["success"] is False
        assert already_active["error"]["code"] == "SESSION_ALREADY_ACTIVE"
    finally:
        daemon.close()


class _InjectedConnectWorker:
    def __init__(self, *, block=None, timeout=False, start_timeout=False,
                 disconnect_timeout=False, disconnect_block=None):
        self.block = block
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.disconnect_timeout = disconnect_timeout
        self.disconnect_block = disconnect_block
        self.start_calls = 0
        self.connect_calls = 0
        self.connect_credentials = []
        self.disconnect_calls = 0
        self.close_calls = 0
        self.status_calls = []
        self.request_status = {}
        self.backend_snapshot_calls = 0
        self.current_operation_id = None
        self.model_fingerprint = "session-recovery-model-v1"
        self.model_external_event_counter = 0
        self.event_callback = None
        self.metadata = {
            "pid": 7401, "instance_id": "worker-fixture-1", "generation": 1,
            "connected": False, "server": "",
        }

    def start(self):
        self.start_calls += 1
        if self.start_timeout:
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout("fixture startup timeout")
        return {"status": "HEALTHY", **self.metadata}

    def runtime_metadata(self):
        return dict(self.metadata)

    def backend_snapshot(self, model_tag):
        self.backend_snapshot_calls += 1
        return {
            "model_tag": model_tag,
            "server_instance_id": self.metadata["server"],
            "generation": self.metadata["generation"],
            "instance_id": self.metadata["instance_id"],
            "fingerprint": self.model_fingerprint,
            "external_event_counter": self.model_external_event_counter,
        }

    @contextmanager
    def operation_context(self, operation_id, *, on_request_event=None):
        previous_operation_id = self.current_operation_id
        previous_callback = self.event_callback
        self.current_operation_id = operation_id
        self.event_callback = on_request_event
        try:
            yield
        finally:
            self.current_operation_id = previous_operation_id
            self.event_callback = previous_callback

    def client(self):
        return self

    def connect(self, port, host, **kwargs):
        self.connect_calls += 1
        self.connect_credentials.append({
            "user": kwargs.get("user", ""), "password": kwargs.get("password", ""),
        })
        if self.event_callback:
            self.event_callback({"phase": "submitted", "request_id": kwargs.get("request_id"),
                                 "operation_id": self.current_operation_id, "kind": "connect", "metadata": {
                "user": kwargs.get("user", ""), "password": kwargs.get("password", ""),
            }})
        if self.block is not None:
            entered, release = self.block
            entered.set()
            release.wait(timeout=5)
        if self.timeout:
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout("fixture timeout")
        server = f"{host}:{port}"
        generation = self.metadata["generation"] + 1
        self.metadata.update(generation=generation, connected=True, server=server)
        return {
            "connected": True, "server": server, "generation": generation,
            "instance_id": self.metadata["instance_id"], "engine_version": "6.4.0.293",
        }

    def disconnect(self, **kwargs):
        self.disconnect_calls += 1
        request_id = kwargs.get("request_id")
        if self.event_callback:
            self.event_callback({"phase": "submitted", "request_id": request_id,
                                 "operation_id": self.current_operation_id,
                                 "kind": "disconnect", "metadata": {}})
        if self.disconnect_block is not None:
            entered, release = self.disconnect_block
            entered.set()
            release.wait(timeout=5)
        if self.disconnect_timeout:
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout("fixture disconnect timeout")
        generation = self.metadata["generation"] + 1
        self.metadata.update(generation=generation, connected=False, server="")
        reply = {"connected": False, "generation": generation,
                 "instance_id": self.metadata["instance_id"]}
        self.request_status[request_id] = {"ok": True, "request_id": request_id,
                                           "status": "SUCCEEDED", "result": reply}
        return reply

    def status(self, request_id, *, timeout_s=1.0):
        self.status_calls.append(request_id)
        return dict(self.request_status.get(request_id, {
            "ok": False, "request_id": request_id, "status": "UNKNOWN",
            "failure": {"code": "REQUEST_NOT_FOUND"},
        }))

    def close(self):
        self.close_calls += 1


class _SyntheticChildProcess:
    def __init__(self, pid=7411):
        self.pid = pid
        self.returncode = None
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = 0
        return self.returncode


class _RetirementFailureWorker(_InjectedConnectWorker):
    def close(self):
        self.close_calls += 1
        raise RuntimeError("synthetic close was not confirmed")


class _ObservedLifecycleWorker(_InjectedConnectWorker):
    """Worker double that persists the same terminal reply envelope as JavaWorker."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._process = _SyntheticChildProcess(pid=7420)
        self.metadata["pid"] = self._process.pid
        self.disconnect_timeout_after_reply = False
        self.connect_timeout_after_reply = False

    def _observe(self, request_id, kind, result):
        wrapper = {"ok": True, "request_id": request_id, "type": kind,
                   "status": "SUCCEEDED", "result": dict(result)}
        self.request_status[request_id] = wrapper
        if self.event_callback:
            self.event_callback({
                "phase": "observed", "request_id": request_id,
                "operation_id": self.current_operation_id, "kind": kind,
                "reply": wrapper, "metadata": {},
            })

    def connect(self, port, host, **kwargs):
        reply = super().connect(port, host, **kwargs)
        self._observe(kwargs.get("request_id"), "connect", reply)
        if self.connect_timeout_after_reply:
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout("synthetic wait expired after terminal connect reply")
        return reply

    def disconnect(self, **kwargs):
        reply = super().disconnect(**kwargs)
        self._observe(kwargs.get("request_id"), "disconnect", reply)
        if self.disconnect_timeout_after_reply:
            from comsol_mcp._java_worker import JavaWorkerTimeout
            raise JavaWorkerTimeout("synthetic wait expired after terminal disconnect reply")
        return reply


class _ExitThenFailCloseWorker(_ObservedLifecycleWorker):
    def close(self):
        self.close_calls += 1
        if self._process.poll() is None:
            self._process.returncode = 0
        raise RuntimeError("synthetic close raised after exact child exit")


class _ExitOnCloseWorker(_ObservedLifecycleWorker):
    def close(self):
        self.close_calls += 1
        if self._process.poll() is None:
            self._process.returncode = 0


class _StartAndRetirementFailureWorker(_InjectedConnectWorker):
    def start(self):
        self.start_calls += 1
        raise RuntimeError("fixture worker start refused before dispatch")

    def close(self):
        self.close_calls += 1
        raise RuntimeError("fixture worker retirement is not confirmed")


def _connect_request(project_id: str, key: str, *, credentials_ref=None):
    arguments = {
        "project_id": project_id,
        "idempotency_key": key,
        "runtime_id": "fixture-runtime",
        "endpoint": {"host": "127.0.0.1", "port": 2046},
    }
    if credentials_ref is not None:
        arguments["credentials_ref"] = credentials_ref
    return {"operation": "session.connect", "arguments": arguments, "execution": {}}


def _session_mutation_request(operation: str, project_id: str, session_id: str, key: str, *,
                              retire_worker=False):
    arguments = {"project_id": project_id, "session_id": session_id,
                 "idempotency_key": key}
    if retire_worker:
        arguments["retire_worker"] = True
    return {
        "operation": operation,
        "arguments": arguments,
        "execution": {},
    }


def _connect_daemon(tmp_path, worker, *, peer=CanonicalSocket("127.0.0.1", 2046),
                    credentials_resolver=None, monkeypatch=None):
    process = getattr(worker, "_process", None)
    if monkeypatch is not None and process is not None:
        birth = 123460

        def synthetic_process_identity(pid):
            exact = pid == getattr(process, "pid", None)
            alive = bool(exact and process.poll() is None)
            return {"alive": alive, "start_epoch_ms": birth if alive else None}

        monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", synthetic_process_identity)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True)
    state_root = tmp_path / "test-session-state"

    def runtime_resolver(runtime_id, project_id, session_id, _project_root, resolved_state_root):
        assert runtime_id == "fixture-runtime"
        session_home = session_state_directory(resolved_state_root, project_id, session_id)
        return SessionRuntimeConfig(
            runtime_id=runtime_id, comsol_version="6.4.0.293",
            installation_root=tmp_path / "synthetic-comsol",
            java_executable=tmp_path / "synthetic-jdk" / "bin" / "java",
            classpath=(tmp_path / "synthetic-comsol" / "client.jar",),
            preferences_dir=session_home / "preferences",
            session_state_root=resolved_state_root,
        )

    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, registry={},
        session_runtime_resolver=runtime_resolver,
        session_worker_factory=lambda _runtime, _worker_state: worker,
        session_peer_observer=lambda _worker, _port, _metadata: peer,
        session_credentials_resolver=credentials_resolver,
    )
    project = _create_project(daemon, "session-connect")
    return daemon, project["project_id"]


def _unknown_connect_after_terminal_reply(monkeypatch, daemon, project_id, key):
    """Leave a real terminal connect reply behind an UNKNOWN lifecycle result."""
    original_save = daemon.session_lifecycle.save
    failed_once = {"value": False}

    def fail_connected_lifecycle(record, *, expected_revision=None):
        if record.get("state") == "CONNECTED" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("injected durable connect lifecycle interruption")
        return original_save(record, expected_revision=expected_revision)

    monkeypatch.setattr(daemon.session_lifecycle, "save", fail_connected_lifecycle)
    uncertain = daemon.dispatch(_connect_request(project_id, key))
    monkeypatch.setattr(daemon.session_lifecycle, "save", original_save)
    assert uncertain["success"] is False
    assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
    assert source["status"] == "UNKNOWN"
    assert source["operation"]["status"] == "UNKNOWN"
    return uncertain, source, uncertain["data"]["session_id"]


def _unknown_session_model_job(daemon, project_id, session_id, worker, suffix, *,
                               binding_overrides=None, result_ref=None,
                               result_revision=0, result_dirty=False,
                               worker_request_id=None, worker_reply=None,
                               event_operation_id=None):
    context = daemon.session_registry.get(project_id, session_id)
    bound = context.service.bind_model(f"recovery-model-{suffix}")
    model_ref = bound["execution"]["model_ref"]
    # Keep the daemon's outer project-identity guard aligned with the
    # registered-session ledger entry used by the real backend.  bind_model()
    # above creates the session-local model state; this row supplies the
    # durable project attribution consumed before scheduler admission.
    model_key = daemon.backend._model_project_key(model_ref)
    revision_metadata = daemon.store.get_metadata("revisions", model_key) or {
        "model_ref": model_ref,
        "revision": 0,
        "dirty": False,
        "fingerprint": worker.model_fingerprint,
        "active_operation_id": None,
    }
    revision_metadata.update(attribution="PROJECT_BOUND", project_id=project_id)
    daemon.store.put_metadata("revisions", model_key, revision_metadata)
    execution = {
        "project_id": project_id,
        "session_id": session_id,
        "model_ref": model_ref,
        "expected_revision": 0,
    }
    binding = daemon._runtime_binding_for_request(context, execution)
    binding.update(binding_overrides or {})
    record, reused = daemon.store.begin(
        request_id=f"outer-recover-{suffix}",
        idempotency_key=f"outer-recover-{suffix}",
        request_hash=f"outer-hash-{suffix}",
        operation="set_parameters",
        metadata={"operation": "set_parameters", "arguments": {"value": suffix},
                  "execution": execution, "runtime_binding": binding},
    )
    assert reused is False
    reply_ref = model_ref if result_ref is None else result_ref
    original_result = {
        "success": False,
        "data": {"execution_state_unknown": True},
        "error": {"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False},
        "execution": {"model_ref": reply_ref, "revision": result_revision,
                       "dirty": result_dirty},
    }
    daemon.store.update_job(record["job_id"], "UNKNOWN", result=original_result)
    worker_request_id = worker_request_id or f"java-recover-{suffix}"
    daemon.store.add_event(record["job_id"], "worker_request", {
        "phase": "submitted", "request_id": worker_request_id, "kind": "model",
        "operation_id": (record["operation_id"] if event_operation_id is None else event_operation_id),
        "request_hash": f"request-hash-{suffix}",
        "metadata": {"model_tag": model_ref["model_tag"]},
    })
    worker.request_status[worker_request_id] = worker_reply or {
        "ok": True, "request_id": worker_request_id, "type": "model",
        "status": "SUCCEEDED", "result": {"ok": True, "model_tag": model_ref["model_tag"]},
    }
    return record["job_id"], record, original_result, model_ref, worker_request_id


def test_session_connect_uses_injected_worker_and_observed_shared_peer_without_globals(tmp_path):
    import comsol_mcp._server as server

    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    global_before = {name: getattr(server, name) for name in (
        "_client", "_remote_client_factory", "_client_connected", "_connected_host",
        "_connected_port", "_server", "_server_started_by_mcp",
    )}
    env_before = {key: os.environ.get(key) for key in (
        "COMSOL_ROOT", "COMSOL_JAVA_HOME", "JAVA_HOME", "COMSOL_PROJECT_ROOT",
    )}
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-success"))
        assert result["success"] is True, result
        data = result["data"]
        assert data["server_ownership"] == "shared"
        assert data["observed_peer"] == {"address": "127.0.0.1", "port": 2046}
        assert data["remote_engine_version"] == "6.4.0.293"
        assert data["remote_engine_build"] is None
        assert data["remote_engine_build_source"] == "NOT_REPORTED"
        context = daemon.session_registry.get(project_id, data["session_id"])
        assert context.worker is worker
        assert context.endpoint.owned_process is None
        assert context.endpoint.observed_peer == CanonicalSocket("127.0.0.1", 2046)
        assert context.server_ownership == "shared"
        assert worker.connect_calls == 1
        assert {name: getattr(server, name) for name in global_before} == global_before
        assert {key: os.environ.get(key) for key in env_before} == env_before
        lifecycle = daemon.session_lifecycle.get(project_id, data["session_id"])
        assert lifecycle["state"] == "CONNECTED"
        assert lifecycle["server_ownership"] == "shared"
    finally:
        daemon.close()


def test_session_disconnect_detaches_only_client_and_preserves_exact_worker_for_reconnect(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-disconnect-reconnect"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        original_context = daemon.session_registry.get(project_id, session_id)
        original_worker_epoch = original_context.worker_epoch

        disconnected = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-once",
        ))
        assert disconnected["success"] is True, disconnected
        assert disconnected["data"]["server_stopped"] is False
        assert disconnected["data"]["worker_handle_preserved"] is True
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "DISCONNECTED"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.metadata["connected"] is False
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 0
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)

        # An idempotent duplicate returns the original result and does not
        # submit a second Worker disconnect.
        replay = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-once",
        ))
        assert replay == disconnected
        assert worker.disconnect_calls == 1

        reconnected = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-once",
        ))
        assert reconnected["success"] is True, reconnected
        assert reconnected["data"]["worker_reused"] is True
        assert reconnected["data"]["old_handles_invalidated"] is True
        assert reconnected["data"]["worker_epoch"] > original_worker_epoch
        assert worker.start_calls == 1  # the original private Worker was reused
        assert worker.connect_calls == 2
        assert worker.disconnect_calls == 1
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        new_context = daemon.session_registry.get(project_id, session_id)
        assert new_context.worker is worker
        assert new_context.worker_epoch == reconnected["data"]["worker_epoch"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "CONNECTED"
    finally:
        daemon.close()


def test_session_disconnect_can_retire_exact_worker_child_without_stopping_shared_server(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    process = _SyntheticChildProcess()
    worker._process = process
    monkeypatch.setattr(
        "comsol_mcp._control_daemon.process_identity",
        lambda pid: {"alive": pid == process.pid, "start_epoch_ms": 123456 if pid == process.pid else None},
    )
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-retire-worker"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]

        retired = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-and-retire",
            retire_worker=True,
        ))
        assert retired["success"] is True, retired
        data = retired["data"]
        assert data["state"] == "DISCONNECTED"
        assert data["client_state"] == "RETIRED"
        assert data["server_stopped"] is False
        assert data["worker_handle_preserved"] is False
        assert data["worker_retirement"] == {
            "status": "RETIRED",
            "worker_instance_id": "worker-fixture-1",
            "worker_epoch": 3,
            "process_identity": {"pid": process.pid, "start_epoch_ms": 123456},
            "exact_popen_handle": True,
            "birth_identity_matched_before_close": True,
            "child_exit_confirmed": True,
            "child_reaped": True,
            "admission_fence": "RETIRED",
            "disconnect_rpc_dispatched": True,
            "worker_close_started": True,
        }
        assert data["engine_dispatched"] is True
        assert data["disconnect_rpc_dispatched"] is True
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert process.wait_calls == 1
        assert process.poll() == 0
        assert (project_id, session_id) not in daemon._session_worker_handles
        lifecycle = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle["state"] == "DISCONNECTED"
        assert lifecycle["client_state"] == "RETIRED"

        replay = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-and-retire",
            retire_worker=True,
        ))
        assert replay == retired
        reconnect = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-after-retired-worker",
        ))
        assert reconnect["success"] is False
        assert reconnect["error"]["code"] == "WORKER_RETIRED"
        assert reconnect["data"]["new_worker_created"] is False
        assert worker.start_calls == 1
        assert worker.connect_calls == 1
    finally:
        daemon.close()


def test_session_disconnect_retirement_is_available_after_prior_client_detach(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    process = _SyntheticChildProcess(pid=7412)
    worker._process = process
    monkeypatch.setattr(
        "comsol_mcp._control_daemon.process_identity",
        lambda pid: {"alive": pid == process.pid, "start_epoch_ms": 123457 if pid == process.pid else None},
    )
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-detach-then-retire"))
        session_id = connected["data"]["session_id"]
        detached = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "detach-first",
        ))
        assert detached["success"] is True, detached
        assert worker.close_calls == 0
        retired = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retire-after-detach",
            retire_worker=True,
        ))
        assert retired["success"] is True, retired
        assert retired["data"]["worker_retirement"]["worker_epoch"] == 3
        assert retired["data"]["engine_dispatched"] is False
        assert retired["data"]["disconnect_rpc_dispatched"] is False
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert process.wait_calls == 1
    finally:
        daemon.close()


def test_session_disconnect_retirement_rejects_wrong_birth_before_detach(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    process = _SyntheticChildProcess(pid=7413)
    worker._process = process
    birth = {"value": 123458}
    monkeypatch.setattr(
        "comsol_mcp._control_daemon.process_identity",
        lambda pid: {"alive": pid == process.pid,
                     "start_epoch_ms": birth["value"] if pid == process.pid else None},
    )
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-wrong-retirement-birth"))
        session_id = connected["data"]["session_id"]
        birth["value"] += 1
        refused = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retire-wrong-birth",
            retire_worker=True,
        ))
        assert refused["success"] is False
        assert refused["error"]["code"] == "WORKER_RETIREMENT_UNAVAILABLE"
        assert refused["data"]["engine_dispatched"] is False
        assert refused["data"]["worker_handle_preserved"] is True
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        assert process.poll() is None
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "CONNECTED"
    finally:
        daemon.close()


def test_session_disconnect_retirement_close_failure_keeps_handle_unknown(tmp_path, monkeypatch):
    worker = _RetirementFailureWorker()
    process = _SyntheticChildProcess(pid=7414)
    worker._process = process
    monkeypatch.setattr(
        "comsol_mcp._control_daemon.process_identity",
        lambda pid: {"alive": pid == process.pid, "start_epoch_ms": 123459 if pid == process.pid else None},
    )
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-retirement-close-fails"))
        session_id = connected["data"]["session_id"]
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-close-fails",
            retire_worker=True,
        ))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert uncertain["data"]["engine_dispatched"] is True
        assert uncertain["data"]["disconnect_rpc_dispatched"] is True
        assert uncertain["data"]["worker_close_started"] is True
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert process.poll() is None
        reconnect = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "cannot-reconnect-unknown-retirement",
        ))
        assert reconnect["success"] is False
        assert worker.start_calls == 1
    finally:
        daemon.close()


def test_session_disconnect_retire_worker_schema_is_optional_boolean():
    from comsol_mcp._g2_registry import validate_call

    base = {"project_id": "project-fixture", "session_id": "session-fixture",
            "idempotency_key": "disconnect-schema"}
    validate_call("session.disconnect", dict(base))
    validate_call("session.disconnect", {**base, "retire_worker": False})
    validate_call("session.disconnect", {**base, "retire_worker": True})
    with pytest.raises(ExecutionContractError):
        validate_call("session.disconnect", {**base, "retire_worker": "true"})
    with pytest.raises(ExecutionContractError):
        validate_call("session.disconnect", {**base, "retire_comsol_server": True})


class _RealChildRetirementWorker(_InjectedConnectWorker):
    def close(self):
        self.close_calls += 1
        if self._process.poll() is None:
            self._process.terminate()
        self._process.wait(timeout=3)


def test_session_disconnect_retires_real_harmless_task_owned_popen(tmp_path):
    from comsol_mcp._platform_process import process_identity

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker = _RealChildRetirementWorker()
    worker._process = process
    identity = process_identity(process.pid)
    if (identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int
            or identity["start_epoch_ms"] <= 0):
        process.terminate()
        process.wait(timeout=3)
        pytest.skip("host cannot provide exact process birth identity")
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-real-child-retirement"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        result = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retire-real-child",
            retire_worker=True,
        ))
        assert result["success"] is True, result
        evidence = result["data"]["worker_retirement"]
        assert evidence["process_identity"] == {
            "pid": process.pid, "start_epoch_ms": identity["start_epoch_ms"],
        }
        assert evidence["exact_popen_handle"] is True
        assert evidence["birth_identity_matched_before_close"] is True
        assert evidence["child_exit_confirmed"] is True
        assert evidence["child_reaped"] is True
        assert process.poll() is not None
    finally:
        daemon.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_control_daemon_close_retires_only_proven_session_worker_child(tmp_path):
    from comsol_mcp._platform_process import process_identity

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker = _RealChildRetirementWorker()
    worker._process = process
    daemon = None
    try:
        identity = process_identity(process.pid)
        if (identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int
                or identity["start_epoch_ms"] <= 0):
            pytest.skip("host cannot provide exact process birth identity")
        daemon, project_id = _connect_daemon(tmp_path, worker)
        connected = daemon.dispatch(_connect_request(project_id, "connect-close-retires-worker"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        completed = []
        original_finish = daemon._finish

        def capture_finish(record, result, status):
            if record.get("operation") == "session.disconnect":
                completed.append((dict(result), status))
            return original_finish(record, result, status)

        daemon._finish = capture_finish
        daemon.close()
        assert process.poll() is not None
        child_receipt = {
            "status": "RETIRED_BY_CONTROL_DAEMON_CLOSE",
            "pid": process.pid,
            "birth_start_epoch_ms": identity["start_epoch_ms"],
            "exact_popen_handle": True,
            "child_exit_confirmed": process.poll() is not None,
            "child_returncode": process.returncode,
            "post_exit_process_identity": process_identity(process.pid),
            "worker_disconnect_calls": worker.disconnect_calls,
            "worker_close_calls": worker.close_calls,
            "server_stopped": False,
        }
        (tmp_path / "daemon_close_child_receipt.json").write_text(
            json.dumps(child_receipt, sort_keys=True), encoding="utf-8",
        )
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert daemon._session_worker_handles == {}
        assert daemon.session_scheduler.closed is True
        assert daemon.closed.is_set()
        assert completed and completed[-1][1] == "SUCCEEDED"
        assert completed[-1][0]["data"]["server_stopped"] is False
        assert completed[-1][0]["data"]["worker_retirement"]["child_reaped"] is True
        rejected = daemon.dispatch({"operation": "session.list", "arguments": {"project_id": project_id}})
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "CONTROL_DAEMON_CLOSING"
    finally:
        if daemon is not None:
            daemon.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_control_daemon_close_leaves_unproven_worker_child_untouched(tmp_path, monkeypatch):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker = _RealChildRetirementWorker()
    worker._process = process
    daemon = None
    try:
        monkeypatch.setattr(
            "comsol_mcp._control_daemon.process_identity",
            lambda pid: {"alive": pid == process.pid, "start_epoch_ms": None},
        )
        daemon, project_id = _connect_daemon(tmp_path, worker)
        connected = daemon.dispatch(_connect_request(project_id, "connect-close-missing-birth"))
        assert connected["success"] is True, connected
        daemon.close()
        assert process.poll() is None
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        assert daemon._session_worker_handles[(project_id, connected["data"]["session_id"])] is worker
    finally:
        if daemon is not None:
            daemon.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_control_daemon_close_preserves_unknown_session_worker_and_child(tmp_path):
    from comsol_mcp._platform_process import process_identity

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker = _RealChildRetirementWorker()
    worker._process = process
    daemon = None
    try:
        identity = process_identity(process.pid)
        if (identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int
                or identity["start_epoch_ms"] <= 0):
            pytest.skip("host cannot provide exact process birth identity")
        daemon, project_id = _connect_daemon(tmp_path, worker)
        connected = daemon.dispatch(_connect_request(project_id, "connect-close-unknown-preserves-worker"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        current = daemon.session_lifecycle.get(project_id, session_id)
        unknown = daemon._updated_session_lifecycle(
            current, state="UNKNOWN", client_state="UNKNOWN",
            worker_instance_id=current["worker_instance_id"],
            worker_epoch=current["worker_epoch"],
            server_instance_id=current["server_instance_id"],
        )
        daemon.session_lifecycle.save(unknown, expected_revision=current["revision"])

        daemon.close()

        assert process.poll() is None
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._session_lifecycle import SessionLifecycleStore
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            assert SessionLifecycleStore(reopened).get(project_id, session_id)["state"] == "UNKNOWN"
        finally:
            reopened.close()
    finally:
        if daemon is not None:
            daemon.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_control_daemon_close_preserves_busy_session_worker(tmp_path):
    worker = _InjectedConnectWorker()
    process = _SyntheticChildProcess(pid=7416)
    worker._process = process
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-close-busy-preserves-worker"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        pending, reused = daemon.store.begin(
            request_id="close-busy-running-work",
            idempotency_key="close-busy-running-work",
            request_hash="close-busy-running-work",
            operation="study.run",
            metadata={
                "project_id": project_id,
                "session_id": session_id,
                "runtime_binding": {
                    "kind": "registered_session", "project_id": project_id,
                    "session_id": session_id, "worker_instance_id": "worker-fixture-1",
                    "worker_epoch": 2,
                },
            },
        )
        assert reused is False
        daemon.store.update_job(pending["job_id"], "UNKNOWN")

        daemon.close()

        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._session_lifecycle import SessionLifecycleStore
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            assert SessionLifecycleStore(reopened).get(project_id, session_id)["state"] == "CONNECTED"
        finally:
            reopened.close()
        assert process.poll() is None
    finally:
        daemon.close()


def test_control_daemon_close_retries_exact_retirement_after_busy_lane_drains(tmp_path):
    from comsol_mcp._platform_process import process_identity
    from comsol_mcp._operation_store import OperationStore
    from comsol_mcp._session_lifecycle import SessionLifecycleStore

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker = _RealChildRetirementWorker()
    worker._process = process
    daemon = None
    try:
        identity = process_identity(process.pid)
        if (identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int
                or identity["start_epoch_ms"] <= 0):
            pytest.skip("host cannot provide exact process birth identity")
        daemon, project_id = _connect_daemon(tmp_path, worker)
        connected = daemon.dispatch(_connect_request(project_id, "connect-close-retires-after-drain"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        context = daemon.session_registry.get(project_id, session_id)
        pending, reused = daemon.store.begin(
            request_id="close-drain-terminal-work",
            idempotency_key="close-drain-terminal-work",
            request_hash="close-drain-terminal-work",
            operation="study.run",
            metadata={
                "project_id": project_id, "session_id": session_id,
                "runtime_binding": {
                    "kind": "registered_session", "project_id": project_id,
                    "session_id": session_id, "worker_instance_id": "worker-fixture-1",
                    "worker_epoch": 2,
                },
            },
        )
        assert reused is False
        daemon.store.update_job(pending["job_id"], "RUNNING")
        entered, release = threading.Event(), threading.Event()

        def accepted_work():
            entered.set()
            assert release.wait(5)
            daemon.store.update_job(pending["job_id"], "SUCCEEDED")

        future = daemon.session_scheduler.submit(context, accepted_work)
        assert entered.wait(2)
        first_sweep_done = threading.Event()
        original_sweep = daemon._retire_owned_session_workers_for_close
        sweep_count = {"value": 0}

        def observe_sweeps():
            sweep_count["value"] += 1
            original_sweep()
            if sweep_count["value"] == 1:
                first_sweep_done.set()

        daemon._retire_owned_session_workers_for_close = observe_sweeps
        close_done = threading.Event()
        close_thread = threading.Thread(target=lambda: (daemon.close(), close_done.set()))
        close_thread.start()
        try:
            assert first_sweep_done.wait(3)
            assert process.poll() is None
            assert worker.disconnect_calls == 0
            assert worker.close_calls == 0
            release.set()
            future.result(timeout=3)
            assert close_done.wait(5)
            close_thread.join(timeout=1)
        finally:
            release.set()
            if close_thread.is_alive():
                close_thread.join(timeout=5)

        assert not close_thread.is_alive()
        assert sweep_count["value"] == 2
        assert process.poll() is not None
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert daemon._session_worker_handles == {}
        (tmp_path / "daemon_close_after_drain_child_receipt.json").write_text(
            json.dumps({
                "status": "RETIRED_AFTER_SCHEDULER_DRAIN",
                "pid": process.pid,
                "birth_start_epoch_ms": identity["start_epoch_ms"],
                "exact_popen_handle": True,
                "child_exit_confirmed": process.poll() is not None,
                "child_returncode": process.returncode,
                "post_exit_process_identity": process_identity(process.pid),
                "worker_disconnect_calls": worker.disconnect_calls,
                "worker_close_calls": worker.close_calls,
                "server_stopped": False,
                "busy_job_terminal_status": "SUCCEEDED",
            }, sort_keys=True), encoding="utf-8",
        )
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            lifecycle = SessionLifecycleStore(reopened).get(project_id, session_id)
            assert lifecycle["state"] == "DISCONNECTED"
            assert lifecycle["client_state"] == "RETIRED"
            assert reopened.job(pending["job_id"])["status"] == "SUCCEEDED"
        finally:
            reopened.close()
    finally:
        if daemon is not None:
            daemon.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_control_daemon_close_waits_for_admitted_connect_before_session_lock(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    trying_connect = threading.Event()
    result = {}
    original_connect = daemon._dispatch_session_connect

    def observed_connect(routed, execution):
        trying_connect.set()
        return original_connect(routed, execution)

    monkeypatch.setattr(daemon, "_dispatch_session_connect", observed_connect)
    daemon._session_connect_lock.acquire()
    request_thread = threading.Thread(
        target=lambda: result.setdefault("response", daemon.dispatch(
            _connect_request(project_id, "connect-close-entry-barrier"),
        )),
    )
    close_done = threading.Event()
    close_thread = threading.Thread(target=lambda: (daemon.close(), close_done.set()))
    request_thread.start()
    try:
        assert trying_connect.wait(2)
        with daemon._dispatch_condition:
            assert daemon._dispatch_condition.wait_for(
                lambda: daemon._control_dispatch_inflight == 1, timeout=2,
            )
        close_thread.start()
        with daemon._dispatch_condition:
            assert daemon._dispatch_condition.wait_for(lambda: daemon._closing, timeout=2)
            assert not close_done.is_set()
        daemon._session_connect_lock.release()
        request_thread.join(timeout=5)
        assert not request_thread.is_alive()
        assert result["response"]["success"] is False
        assert result["response"]["error"]["code"] == "CONTROL_DAEMON_CLOSING"
        assert worker.start_calls == 0
        assert worker.connect_calls == 0
        assert daemon._session_worker_handles == {}
        assert close_done.wait(5)
        close_thread.join(timeout=1)
    finally:
        try:
            daemon._session_connect_lock.release()
        except RuntimeError:
            pass
        if request_thread.is_alive():
            request_thread.join(timeout=5)
        if close_thread.ident is not None and close_thread.is_alive():
            close_thread.join(timeout=5)
        daemon.close()


def test_session_disconnect_retirement_waits_for_accepted_worker_tasks(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    process = _SyntheticChildProcess(pid=7415)
    worker._process = process
    monkeypatch.setattr(
        "comsol_mcp._control_daemon.process_identity",
        lambda pid: {"alive": pid == process.pid, "start_epoch_ms": 123460 if pid == process.pid else None},
    )
    daemon, project_id = _connect_daemon(tmp_path, worker)
    entered, release = threading.Event(), threading.Event()
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-retirement-busy"))
        session_id = connected["data"]["session_id"]
        context = daemon.session_registry.get(project_id, session_id)

        def accepted_work():
            entered.set()
            assert release.wait(3)

        future = daemon.session_scheduler.submit(context, accepted_work)
        assert entered.wait(1)
        refused = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retirement-busy",
            retire_worker=True,
        ))
        assert refused["success"] is False
        assert refused["error"]["code"] == "SESSION_BUSY"
        assert refused["data"]["engine_dispatched"] is False
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        assert process.poll() is None

        release.set()
        future.result(timeout=2)
        retired = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retirement-after-drain",
            retire_worker=True,
        ))
        assert retired["success"] is True, retired
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
    finally:
        release.set()
        daemon.close()


def test_session_disconnect_unknown_without_exact_popen_does_not_query_worker_or_recover(tmp_path):
    worker = _InjectedConnectWorker(disconnect_timeout=True)
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-disconnect-unknown"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        failed = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-timeout",
        ))
        assert failed["success"] is False
        assert failed["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert failed["data"]["engine_dispatched"] is True
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.close_calls == 0
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)

        duplicate = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-timeout",
        ))
        assert duplicate == failed
        assert worker.disconnect_calls == 1

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-disconnect-timeout",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["replayed_requests"] == 0
        assert recovered["data"]["new_worker_created"] is False
        assert recovered["data"]["lifecycle_state"] == "UNKNOWN"
        assert worker.disconnect_calls == 1
        assert worker.connect_calls == 1
        assert worker.start_calls == 1
        assert worker.status_calls == []
        source = daemon.store.operation_job(failed["execution"]["operation_id"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon._job_quiescence_proven(source) is False
        assert any(item.get("classification") == "WORKER_PROCESS_IDENTITY_UNCONFIRMED"
                   for item in recovered["data"]["request_observations"])

        retry = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-after-unknown",
        ))
        assert retry["success"] is False
        assert retry["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert worker.connect_calls == 1
        assert worker.start_calls == 1
    finally:
        daemon.close()


def test_session_recover_records_model_quiescence_proof_without_rewriting_unknown_history(tmp_path):
    from comsol_mcp._operation_store import OperationStore

    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-recover-model-proof"))
        session_id = connected["data"]["session_id"]
        job_id, source_record, original_result, model_ref, worker_request_id = _unknown_session_model_job(
            daemon, project_id, session_id, worker, "positive",
        )
        source_before = daemon.store.job(job_id)
        assert source_before["status"] == "UNKNOWN"
        assert source_before["result"] == original_result
        original_operation_result = source_before["operation"]["result"]

        request = _session_mutation_request(
            "session.recover", project_id, session_id, "recover-model-proof-once",
        )
        recovered = daemon.dispatch(request)
        assert recovered["success"] is True, recovered
        data = recovered["data"]
        assert data["recovery_status"] == "PARTIAL_HISTORICAL_UNKNOWN_RETAINED"
        assert data["historical_unknown_preserved"] is True
        assert data["replayed_requests"] == 0
        assert data["new_worker_created"] is False
        assert len(data["resolved_jobs"]) == 1
        assert data["resolved_jobs"][0]["job_id"] == job_id
        assert data["resolved_jobs"][0]["outcome_resolution"] == "UNVERIFIED_HISTORICAL_UNKNOWN"
        assert data["resolved_jobs"][0]["quiescence_resolution"] == "PROVEN_AND_AUDITED"
        assert data["resolved_jobs"][0]["model_ref"] == model_ref
        assert worker.status_calls == [worker_request_id]
        assert worker.start_calls == 1
        assert worker.connect_calls == 1

        source_after = daemon.store.job(job_id)
        assert source_after["status"] == "UNKNOWN"
        assert source_after["operation"]["status"] == "UNKNOWN"
        assert source_after["result"] == original_result
        assert source_after["operation"]["result"] == original_operation_result
        assert source_after["metadata"]["reconciled_quiescent"] is True
        assert source_after["metadata"]["session_recovery_resolution"]["model_ref"] == model_ref
        assert source_after["metadata"]["session_recovery_resolution"]["model_revision"] == 0
        proof = daemon.store.session_recovery_resolution(job_id)
        assert proof["event"] == "SessionRecoveryResolution"
        proof_data = proof["metadata"]
        assert proof_data["source_job_id"] == job_id
        assert proof_data["source_operation_id"] == source_record["operation_id"]
        assert proof_data["session_recovery_operation_id"] == recovered["execution"]["operation_id"]
        assert proof_data["resolution_scope"] == "WORKER_REQUEST_QUIESCENCE_ONLY"
        assert proof_data["outcome_resolution"] == "UNVERIFIED_HISTORICAL_UNKNOWN"
        assert proof_data["replay_performed"] is False
        assert proof_data["new_worker_created"] is False
        assert daemon._job_quiescence_proven(source_after) is True
        assert daemon._session_recovery_resolution_is_valid(source_after) is True

        # Same request idempotency returns the same recovery operation. A new
        # read-only recovery does not re-query or append a second proof.
        assert daemon.dispatch(request) == recovered
        assert worker.status_calls == [worker_request_id]
        second = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-model-proof-again",
        ))
        assert second["success"] is True, second
        assert second["data"]["resolved_jobs"][0]["quiescence_resolution"] == "ALREADY_PROVEN"
        assert worker.status_calls == [worker_request_id]
        assert len([item for item in daemon.store.events(job_id)
                    if item["event"] == "SessionRecoveryResolution"]) == 1

        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            durable = reopened.job(job_id)
            assert durable["status"] == "UNKNOWN"
            assert durable["result"] == original_result
            assert durable["metadata"]["session_recovery_resolution"]["evidence_sha256"] == proof_data["evidence_sha256"]
            assert reopened.session_recovery_resolution(job_id)["metadata"] == proof_data
        finally:
            reopened.close()

        # An append-only proof is useful only while its exact historical result
        # remains intact; a later result rewrite must fail the gate closed.
        daemon.store.update_job(job_id, "UNKNOWN", result={"success": False, "tampered": True})
        assert daemon._job_quiescence_proven(daemon.store.job(job_id)) is False
    finally:
        daemon.close()


def test_session_recover_classifies_connect_only_with_exact_live_context_and_peer(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    original_save = daemon.session_lifecycle.save
    failed_once = {"value": False}

    def fail_connected_lifecycle(record, *, expected_revision=None):
        if record.get("state") == "CONNECTED" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("injected durable registration interruption")
        return original_save(record, expected_revision=expected_revision)

    monkeypatch.setattr(daemon.session_lifecycle, "save", fail_connected_lifecycle)
    try:
        uncertain = daemon.dispatch(_connect_request(project_id, "connect-recover-lifecycle-proof"))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        session_id = uncertain["data"]["session_id"]
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        assert daemon.session_registry.get(project_id, session_id).worker is worker
        original_result = source["result"]
        rpc_count = worker.connect_calls

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-connect-lifecycle-proof",
        ))
        assert recovered["success"] is True, recovered
        data = recovered["data"]
        assert data["replayed_requests"] == 0
        assert data["new_worker_created"] is False
        assert data["lifecycle_state"] == "CONNECTED"
        assert data["resolved_jobs"][0]["historical_status"] == "UNKNOWN"
        assert data["resolved_jobs"][0]["classification"] == "CONNECTED_EXACT_REPLY_WORKER_AND_PEER"
        assert worker.connect_calls == rpc_count
        assert len(worker.status_calls) == 1

        source_after = daemon.store.job(source["job_id"])
        assert source_after["status"] == "UNKNOWN"
        assert source_after["operation"]["status"] == "UNKNOWN"
        assert source_after["result"] == original_result
        proof = daemon.store.session_lifecycle_recovery_resolution(source["job_id"])
        assert proof["metadata"]["resolution_scope"] == "SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE"
        assert proof["metadata"]["connection_observation"]["remote_engine_version"] == "6.4.0.293"
        assert proof["metadata"]["connection_observation"]["remote_engine_build"] is None
        assert proof["metadata"]["connection_observation"]["remote_engine_build_source"] == "NOT_REPORTED"
        assert proof["metadata"]["worker_observation"]["remote_engine_health_claim"] is False
        assert proof["metadata"]["worker_observation"]["state"] == "LIVE_EXACT"
        assert proof["metadata"]["worker_observation"]["runtime_pid_matches"] is True
        assert proof["metadata"]["worker_observation"]["process_birth_matches"] is True
        assert daemon._job_quiescence_proven(source_after) is True
        assert daemon._session_lifecycle_recovery_resolution_is_valid(source_after) is True

        status_calls = list(worker.status_calls)
        repeated = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-connect-lifecycle-proof-again",
        ))
        assert repeated["success"] is True
        assert repeated["data"]["resolved_jobs"][0]["quiescence_resolution"] == "ALREADY_PROVEN"
        assert worker.status_calls == status_calls
        assert worker.connect_calls == rpc_count
    finally:
        daemon.close()


def test_session_recover_keeps_attached_connect_without_context_unresolved(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    worker.connect_timeout_after_reply = True
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        uncertain = daemon.dispatch(_connect_request(project_id, "connect-recover-no-context"))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        session_id = uncertain["data"]["session_id"]
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-connect-no-context",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["replayed_requests"] == 0
        assert recovered["data"]["new_worker_created"] is False
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("classification") == "ATTACHED_BUT_CONTEXT_UNAVAILABLE"
                   for item in recovered["data"]["request_observations"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False
        assert worker.connect_calls == 1
        assert worker.start_calls == 1
    finally:
        daemon.close()


def test_session_lifecycle_recovery_gate_revalidates_persisted_live_process_proof(tmp_path, monkeypatch):
    from comsol_mcp._operation_store import session_recovery_evidence_sha256

    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-lifecycle-proof-validator",
        )
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-process-proof-for-validator",
        ))
        assert recovered["success"] is True, recovered
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is True

        resolution = daemon.store.session_lifecycle_recovery_resolution(source["job_id"])
        metadata = dict(resolution["metadata"])
        worker_observation = dict(metadata["worker_observation"])
        worker_observation.update({
            "state": "LIVE_IDENTITY_UNCONFIRMED",
            "runtime_pid_matches": False,
            "process_birth_matches": False,
        })
        metadata["worker_observation"] = worker_observation
        metadata.pop("evidence_sha256", None)
        digest = session_recovery_evidence_sha256(metadata)
        metadata["evidence_sha256"] = digest
        with daemon.store.lock:
            daemon.store.db.execute(
                "UPDATE job_events SET metadata=? WHERE id=?",
                (json.dumps(metadata, sort_keys=True), resolution["id"]),
            )
        current = daemon.store.job(source["job_id"])
        pointer = dict(current["metadata"]["session_lifecycle_recovery_resolution"])
        pointer["evidence_sha256"] = digest
        daemon.store.update_job(source["job_id"], current["status"], {
            "session_lifecycle_recovery_resolution": pointer,
        })
        tampered = daemon.store.job(source["job_id"])
        assert tampered["metadata"]["reconciled_quiescent"] is True
        assert daemon._session_lifecycle_recovery_resolution_is_valid(tampered) is False
        assert daemon._job_quiescence_proven(tampered) is False
    finally:
        daemon.close()


def test_session_recover_rechecks_live_birth_after_status_readback(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    original_status = worker.status
    process = worker._process
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-birth-changes-during-readback",
        )

        def status_then_wrong_birth(request_id, *, timeout_s=1.0):
            result = original_status(request_id, timeout_s=timeout_s)
            monkeypatch.setattr(
                "comsol_mcp._control_daemon.process_identity",
                lambda pid: {"alive": pid == process.pid,
                             "start_epoch_ms": 123461 if pid == process.pid else None},
            )
            return result

        worker.status = status_then_wrong_birth
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-birth-changes-during-readback",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("reason") == "WORKER_POPEN_OR_EPOCH_CHANGED_DURING_LIFECYCLE_READBACK"
                   for item in recovered["data"]["unresolved_items"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False
        assert worker.connect_calls == 1
        assert len(worker.status_calls) == 1
    finally:
        daemon.close()


def test_session_recover_proves_completed_disconnect_without_replay(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-disconnect-recover"))
        session_id = connected["data"]["session_id"]
        worker.disconnect_timeout_after_reply = True
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-terminal-then-timeout",
        ))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        disconnect_count = worker.disconnect_calls
        connect_count = worker.connect_calls

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-disconnect-terminal",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["lifecycle_state"] == "DISCONNECTED"
        assert recovered["data"]["resolved_jobs"][0]["classification"] == "DISCONNECTED_EXACT_WORKER_EPOCH"
        assert recovered["data"]["replayed_requests"] == 0
        assert recovered["data"]["new_worker_created"] is False
        assert worker.disconnect_calls == disconnect_count
        assert worker.connect_calls == connect_count
        assert len(worker.status_calls) == 1
        after = daemon.store.job(source["job_id"])
        assert after["status"] == "UNKNOWN"
        assert after["operation"]["status"] == "UNKNOWN"
        assert daemon._job_quiescence_proven(after) is True
        lifecycle = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle["state"] == "DISCONNECTED"
        assert lifecycle["client_state"] == "DISCONNECTED"
        assert lifecycle["worker_epoch"] == worker.metadata["generation"]
        assert lifecycle["health"]["status"] == "UNKNOWN"
        proof = daemon.store.session_lifecycle_recovery_resolution(source["job_id"])
        assert proof["metadata"]["worker_observation"]["state"] == "LIVE_EXACT"
        assert proof["metadata"]["worker_observation"]["process_birth_matches"] is True
    finally:
        daemon.close()


def test_session_recover_never_claims_reconnect_without_original_context(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-reconnect-recover"))
        session_id = connected["data"]["session_id"]
        detached = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-before-reconnect-recover",
        ))
        assert detached["success"] is True, detached
        original_save = daemon.session_lifecycle.save
        failed_once = {"value": False}

        def fail_reconnected_lifecycle(record, *, expected_revision=None):
            if record.get("state") == "CONNECTED" and not failed_once["value"]:
                failed_once["value"] = True
                raise OSError("injected reconnect lifecycle commit interruption")
            return original_save(record, expected_revision=expected_revision)

        monkeypatch.setattr(daemon.session_lifecycle, "save", fail_reconnected_lifecycle)
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-terminal-without-context",
        ))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        events = daemon.store.events(source["job_id"])
        baseline = [item for item in events if item["event"] == "SessionReconnectBaseline"]
        submitted = [item for item in events if item["event"] == "worker_request"
                     and item["metadata"].get("phase") == "submitted"]
        assert len(baseline) == 1
        assert baseline[0]["metadata"]["baseline_recorded_before_worker_rpc"] is True
        assert baseline[0]["id"] < min(item["id"] for item in submitted)
        assert baseline[0]["metadata"]["detach_required"] is False
        assert [item["metadata"]["kind"] for item in submitted] == ["connect"]
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)
        connect_count = worker.connect_calls

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-reconnect-context-missing",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("classification") == "ATTACHED_BUT_CONTEXT_UNAVAILABLE"
                   for item in recovered["data"]["request_observations"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert worker.connect_calls == connect_count
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
    finally:
        daemon.close()


def test_session_recover_verifies_prior_exact_worker_close_without_closing_again(tmp_path, monkeypatch):
    worker = _ExitOnCloseWorker()
    process = worker._process
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-exit-retirement-recover"))
        session_id = connected["data"]["session_id"]
        original_save = daemon.session_lifecycle.save
        failed_once = {"value": False}

        def fail_retired_lifecycle(record, *, expected_revision=None):
            if record.get("client_state") == "RETIRED" and not failed_once["value"]:
                failed_once["value"] = True
                raise OSError("injected retirement lifecycle commit interruption")
            return original_save(record, expected_revision=expected_revision)

        monkeypatch.setattr(daemon.session_lifecycle, "save", fail_retired_lifecycle)
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "retire-exit-recover", retire_worker=True,
        ))
        assert uncertain["success"] is False
        assert uncertain["data"]["worker_close_started"] is True
        assert process.poll() == 0
        assert process.wait_calls == 1
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        reaped_events = [item for item in daemon.store.events(source["job_id"])
                         if item["event"] == "WorkerCloseReaped"]
        assert len(reaped_events) == 1
        assert reaped_events[0]["metadata"]["wait_confirmed"] is True
        assert reaped_events[0]["metadata"]["exact_popen_handle"] is True
        close_calls, disconnect_calls = worker.close_calls, worker.disconnect_calls

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-exact-retirement-event",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"][0]["classification"] == "EXACT_WORKER_RETIRED"
        assert recovered["data"]["resolved_jobs"][0]["resolution_scope"] == "SESSION_WORKER_RETIRED_EXACT"
        assert recovered["data"]["lifecycle_state"] == "DISCONNECTED"
        assert recovered["data"]["worker_process_observation"]["state"] == "EXITED_EXACT"
        assert recovered["data"]["worker_process_observation"]["wait_confirmed"] is True
        assert recovered["data"]["worker_process_observation"]["wait_evidence_source"] == "persisted_worker_close_reaped_event"
        assert worker.close_calls == close_calls
        assert worker.disconnect_calls == disconnect_calls
        assert process.wait_calls == 1
        assert (project_id, session_id) not in daemon._session_worker_handles
        after = daemon.store.job(source["job_id"])
        assert after["status"] == "UNKNOWN"
        assert daemon._job_quiescence_proven(after) is True
    finally:
        daemon.close()


@pytest.mark.parametrize(
    "inject_submitted_request,expected_resolution",
    [(False, True), (True, False)],
    ids=["valid-zero-submissions", "unexpected-submitted-request"],
)
def test_session_recover_classifies_pre_detached_worker_retirement_exactly(
    tmp_path, monkeypatch, inject_submitted_request, expected_resolution,
):
    worker = _ExitOnCloseWorker()
    process = worker._process
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        connected = daemon.dispatch(_connect_request(
            project_id, "connect-before-pre-detached-retirement",
        ))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        detached = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id,
            "disconnect-before-pre-detached-retirement",
        ))
        assert detached["success"] is True, detached
        assert detached["data"]["state"] == "DISCONNECTED"
        assert worker.disconnect_calls == 1
        assert worker.close_calls == 0
        assert process.wait_calls == 0

        original_save = daemon.session_lifecycle.save
        failed_once = {"value": False}

        def fail_retired_lifecycle(record, *, expected_revision=None):
            if record.get("client_state") == "RETIRED" and not failed_once["value"]:
                failed_once["value"] = True
                raise OSError("injected pre-detached retirement lifecycle commit interruption")
            return original_save(record, expected_revision=expected_revision)

        monkeypatch.setattr(daemon.session_lifecycle, "save", fail_retired_lifecycle)
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id,
            "retire-pre-detached-lifecycle-commit-failure", retire_worker=True,
        ))
        assert uncertain["success"] is False
        assert uncertain["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert uncertain["data"]["worker_close_started"] is True
        assert uncertain["data"]["disconnect_rpc_dispatched"] is False
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        assert source["status"] == "UNKNOWN"
        events = daemon.store.events(source["job_id"])
        close_events = [item for item in events if item["event"] == "WorkerCloseStarted"]
        reaped_events = [item for item in events if item["event"] == "WorkerCloseReaped"]
        assert len(close_events) == 1
        assert len(reaped_events) == 1
        assert close_events[0]["metadata"]["disconnect_rpc_dispatched"] is False
        assert close_events[0]["metadata"]["disconnect_request_id"] is None
        assert not [item for item in events if item["event"] == "worker_request"
                    and item["metadata"].get("phase") == "submitted"]
        assert worker.close_calls == 1
        assert worker.disconnect_calls == 1
        assert worker.connect_calls == 1
        assert process.wait_calls == 1

        if inject_submitted_request:
            daemon.store.add_event(source["job_id"], "worker_request", {
                "phase": "submitted",
                "request_id": "unexpected-pre-detached-worker-rpc",
                "operation_id": source["operation_id"],
                "kind": "disconnect",
                "request_hash": "synthetic-unexpected-request-hash",
            })

        before_recovery = (
            worker.close_calls, worker.disconnect_calls, worker.connect_calls,
            process.wait_calls, list(worker.status_calls),
        )
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-pre-detached-retirement",
        ))
        assert recovered["success"] is True, recovered
        if expected_resolution:
            assert recovered["data"]["resolved_jobs"][0]["classification"] == "EXACT_WORKER_RETIRED"
            assert recovered["data"]["resolved_jobs"][0]["resolution_scope"] == "SESSION_WORKER_RETIRED_EXACT"
            assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is True
            repeated = daemon.dispatch(_session_mutation_request(
                "session.recover", project_id, session_id,
                "recover-pre-detached-retirement-again",
            ))
            assert repeated["success"] is True, repeated
            assert repeated["data"]["resolved_jobs"][0]["quiescence_resolution"] == "ALREADY_PROVEN"
            assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is True
        else:
            assert recovered["data"]["resolved_jobs"] == []
            assert any(
                item.get("job_id") == source["job_id"]
                and item.get("reason") == "UNEXPECTED_WORKER_RPC_FOR_PREDETACHED_RETIREMENT"
                for item in recovered["data"]["unresolved_items"]
            )
            assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
            assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False
            repeated = daemon.dispatch(_session_mutation_request(
                "session.recover", project_id, session_id,
                "recover-pre-detached-unexpected-rpc-again",
            ))
            assert repeated["success"] is True, repeated
            assert repeated["data"]["resolved_jobs"] == []
            assert any(
                item.get("job_id") == source["job_id"]
                and item.get("reason") == "UNEXPECTED_WORKER_RPC_FOR_PREDETACHED_RETIREMENT"
                for item in repeated["data"]["unresolved_items"]
            )
            assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
            assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False

        assert (
            worker.close_calls, worker.disconnect_calls, worker.connect_calls,
            process.wait_calls, list(worker.status_calls),
        ) == before_recovery
    finally:
        daemon.close()


def test_session_recover_does_not_reap_unattributed_exited_worker(tmp_path, monkeypatch):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-worker-exits-without-close-event",
        )
        process = worker._process
        process.returncode = 17
        connect_calls, close_calls = worker.connect_calls, worker.close_calls
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-unattributed-worker-exit",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("job_id") == source["job_id"]
                   for item in recovered["data"]["unresolved_items"])
        observation = next(item for item in recovered["data"]["request_observations"]
                           if item.get("job_id") == source["job_id"])
        assert observation["classification"] == "WORKER_EXITED_DURING_LIFECYCLE_RECOVERY"
        assert observation["worker_observation"]["state"] == "EXITED_EXACT_UNREAPED"
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert not [item for item in daemon.store.events(source["job_id"])
                    if item["event"] in {"WorkerCloseStarted", "WorkerCloseReaped"}]
        assert process.wait_calls == 0
        assert worker.close_calls == close_calls
        assert worker.connect_calls == connect_calls == 1
        assert worker.status_calls == []
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False
    finally:
        daemon.close()


def test_session_recover_requires_prior_persisted_worker_reap_and_never_waits(tmp_path, monkeypatch):
    worker = _ExitThenFailCloseWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-worker-close-without-reap"))
        session_id = connected["data"]["session_id"]
        uncertain = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id,
            "retire-worker-close-without-reap", retire_worker=True,
        ))
        assert uncertain["success"] is False
        assert uncertain["data"]["worker_close_started"] is True
        source = daemon.store.operation_job(uncertain["execution"]["operation_id"])
        events_before = daemon.store.events(source["job_id"])
        assert len([item for item in events_before if item["event"] == "WorkerCloseStarted"]) == 1
        assert not [item for item in events_before if item["event"] == "WorkerCloseReaped"]
        assert worker._process.poll() == 0
        assert worker._process.wait_calls == 0
        close_calls, disconnect_calls = worker.close_calls, worker.disconnect_calls

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-no-prior-worker-reap",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("reason") == "WORKER_CLOSE_REAP_EVIDENCE_NOT_RECORDED"
                   for item in recovered["data"]["unresolved_items"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is False
        assert worker._process.wait_calls == 0
        assert worker.close_calls == close_calls
        assert worker.disconnect_calls == disconnect_calls
        events_after = daemon.store.events(source["job_id"])
        assert len([item for item in events_after if item["event"] == "WorkerCloseStarted"]) == 1
        assert not [item for item in events_after if item["event"] == "WorkerCloseReaped"]
    finally:
        daemon.close()


def test_session_lifecycle_recovery_resolution_rolls_back_event_state_and_gate_on_sqlite_failure(
    tmp_path, monkeypatch,
):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    trigger = "fail_lifecycle_recovery_pointer_update"
    try:
        uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-atomic-lifecycle-recovery",
        )
        original_result = source["result"]
        lifecycle_before = daemon.session_lifecycle.get(project_id, session_id)
        connect_calls = worker.connect_calls
        with daemon.store.lock:
            daemon.store.db.execute(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE OF metadata ON jobs "
                f"WHEN NEW.job_id='{source['job_id']}' "
                "AND instr(NEW.metadata, '\"session_lifecycle_recovery_resolution\"') > 0 "
                "BEGIN SELECT RAISE(ABORT, 'injected source pointer failure'); END"
            )

        interrupted = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-atomic-write-fails",
        ))
        assert interrupted["success"] is False
        assert interrupted["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon.session_lifecycle.get(project_id, session_id) == lifecycle_before
        after = daemon.store.job(source["job_id"])
        assert after["status"] == "UNKNOWN"
        assert after["operation"]["status"] == "UNKNOWN"
        assert after["result"] == original_result
        assert "session_lifecycle_recovery_resolution" not in after["metadata"]
        assert after["metadata"].get("reconciled_quiescent") is not True
        assert daemon._job_quiescence_proven(after) is False
        assert worker.connect_calls == connect_calls

        with daemon.store.lock:
            daemon.store.db.execute(f"DROP TRIGGER {trigger}")
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-atomic-write-retry",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"][0]["quiescence_resolution"] == "PROVEN_AND_AUDITED"
        after_retry = daemon.store.job(source["job_id"])
        assert after_retry["status"] == "UNKNOWN"
        assert after_retry["result"] == original_result
        assert daemon._job_quiescence_proven(after_retry) is True
        status_calls = list(worker.status_calls)
        repeated = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-atomic-write-repeat",
        ))
        assert repeated["success"] is True
        assert repeated["data"]["resolved_jobs"][0]["quiescence_resolution"] == "ALREADY_PROVEN"
        assert worker.status_calls == status_calls
        assert worker.connect_calls == connect_calls
    finally:
        with daemon.store.lock:
            daemon.store.db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        daemon.close()


def test_session_lifecycle_recovery_cas_conflict_keeps_unknown_gate_closed_until_retry(
    tmp_path, monkeypatch,
):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    original_record = daemon.store.record_session_lifecycle_recovery_resolution
    bumped = {"value": False}
    try:
        uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-lifecycle-cas-conflict",
        )
        original_revision = daemon.session_lifecycle.get(project_id, session_id)["revision"]

        def bump_revision_before_cas(job_id, source_operation_id, expected_revision,
                                     lifecycle_after, evidence):
            if not bumped["value"]:
                bumped["value"] = True
                current = daemon.session_lifecycle.get(project_id, session_id)
                daemon.session_lifecycle.save(current, expected_revision=current["revision"])
            return original_record(job_id, source_operation_id, expected_revision,
                                   lifecycle_after, evidence)

        monkeypatch.setattr(daemon.store, "record_session_lifecycle_recovery_resolution",
                            bump_revision_before_cas)
        conflicted = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-lifecycle-cas-conflict",
        ))
        assert conflicted["success"] is True, conflicted
        assert conflicted["data"]["resolved_jobs"] == []
        assert any(item.get("reason") == "SESSION_LIFECYCLE_REVISION_CONFLICT"
                   for item in conflicted["data"]["unresolved_items"])
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        lifecycle_after_conflict = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle_after_conflict["state"] == "UNKNOWN"
        assert lifecycle_after_conflict["revision"] == original_revision + 1
        source_after_conflict = daemon.store.job(source["job_id"])
        assert source_after_conflict["status"] == "UNKNOWN"
        assert source_after_conflict["result"] == source["result"]
        assert source_after_conflict["metadata"].get("reconciled_quiescent") is not True
        assert daemon._job_quiescence_proven(source_after_conflict) is False

        monkeypatch.setattr(daemon.store, "record_session_lifecycle_recovery_resolution", original_record)
        retried = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-lifecycle-cas-retry",
        ))
        assert retried["success"] is True, retried
        assert retried["data"]["resolved_jobs"][0]["quiescence_resolution"] == "PROVEN_AND_AUDITED"
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is True
        assert worker.connect_calls == 1
    finally:
        daemon.close()


@pytest.mark.parametrize("tamper", [
    "worker_instance", "worker_epoch", "request_id", "request_type",
    "request_operation", "backend_peer_identity", "worker_wrong_birth",
    "worker_runtime_pid", "worker_runtime_pid_missing",
    "worker_popen_replaced", "worker_popen_missing",
    "worker_child_identity_missing",
])
def test_session_lifecycle_recovery_rejects_mismatched_source_binding_or_peer(
    tmp_path, monkeypatch, tamper,
):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, f"connect-lifecycle-negative-{tamper}",
        )
        before_result = source["result"]
        if tamper in {"worker_instance", "worker_epoch"}:
            metadata = dict(source["metadata"])
            binding = dict(metadata["runtime_binding"])
            binding["worker_instance_id" if tamper == "worker_instance" else "worker_epoch"] = (
                "foreign-worker" if tamper == "worker_instance"
                else binding["worker_epoch"] + 1
            )
            metadata["runtime_binding"] = binding
            daemon.store.update_job(source["job_id"], source["status"], metadata)
        elif tamper in {"request_id", "request_type", "request_operation"}:
            submitted = [item for item in daemon.store.events(source["job_id"])
                         if item["event"] == "worker_request"
                         and item["metadata"].get("phase") == "submitted"]
            assert len(submitted) == 1
            event = submitted[0]
            event_metadata = dict(event["metadata"])
            if tamper == "request_id":
                event_metadata["request_id"] = "foreign-connect-request"
            elif tamper == "request_type":
                event_metadata["kind"] = "disconnect"
            else:
                event_metadata["operation_id"] = "foreign-operation"
            with daemon.store.lock:
                daemon.store.db.execute(
                    "UPDATE job_events SET metadata=? WHERE id=?",
                    (json.dumps(event_metadata, sort_keys=True), event["id"]),
                )
        elif tamper == "backend_peer_identity":
            context = daemon.session_registry.get(project_id, session_id)
            identity = dict(context.backend.worker_identity)
            identity["server_instance_id"] = "foreign-observed-peer-binding"
            context.backend.worker_identity = identity
        elif tamper == "worker_wrong_birth":
            process = worker._process
            monkeypatch.setattr(
                "comsol_mcp._control_daemon.process_identity",
                lambda pid: {"alive": pid == process.pid,
                             "start_epoch_ms": 123461 if pid == process.pid else None},
            )
        elif tamper == "worker_runtime_pid":
            worker.metadata["pid"] = worker._process.pid + 1
        elif tamper == "worker_runtime_pid_missing":
            worker.metadata.pop("pid", None)
        elif tamper == "worker_popen_replaced":
            worker._process = _SyntheticChildProcess(pid=worker._process.pid + 1)
        elif tamper == "worker_popen_missing":
            worker._process = None
        elif tamper == "worker_child_identity_missing":
            daemon._session_worker_child_identities.pop((project_id, session_id), None)

        before_connect_calls = worker.connect_calls
        before_disconnect_calls = worker.disconnect_calls
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, f"recover-lifecycle-negative-{tamper}",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert recovered["data"]["replayed_requests"] == 0
        assert recovered["data"]["new_worker_created"] is False
        assert any(item.get("job_id") == source["job_id"]
                   for item in recovered["data"]["unresolved_items"])
        source_observation = next(item for item in recovered["data"]["request_observations"]
                                  if item.get("job_id") == source["job_id"])
        if tamper == "worker_wrong_birth":
            assert source_observation["worker_observation"]["state"] == "LIVE_IDENTITY_UNCONFIRMED"
            assert source_observation["worker_observation"]["process_birth_matches"] is False
        elif tamper in {"worker_runtime_pid", "worker_runtime_pid_missing"}:
            assert source_observation["worker_observation"]["state"] == "LIVE_IDENTITY_UNCONFIRMED"
            assert source_observation["worker_observation"]["runtime_pid_matches"] is False
        elif tamper in {"worker_popen_replaced", "worker_popen_missing"}:
            assert source_observation["worker_observation"]["state"] == "MISSING_OR_UNVERIFIABLE"
        elif tamper == "worker_child_identity_missing":
            assert source_observation["worker_observation"]["state"] == "LIVE_WORKER_HEALTH_ONLY"
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        after = daemon.store.job(source["job_id"])
        assert after["status"] == "UNKNOWN"
        assert after["operation"]["status"] == "UNKNOWN"
        assert after["result"] == before_result
        assert after["metadata"].get("reconciled_quiescent") is not True
        assert daemon._job_quiescence_proven(after) is False
        assert worker.connect_calls == before_connect_calls == 1
        assert worker.disconnect_calls == before_disconnect_calls == 0
    finally:
        daemon.close()


def test_session_recover_resolves_lifecycle_source_but_keeps_other_unknown_job_blocking(
    tmp_path, monkeypatch,
):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-lifecycle-with-other-unknown",
        )
        other_job_id, _other, _result, model_ref, _request_id = _unknown_session_model_job(
            daemon, project_id, session_id, worker, "other-running",
            worker_reply={"ok": True, "request_id": "java-recover-other-running",
                          "type": "model", "status": "RUNNING"},
        )
        snapshots_before = worker.backend_snapshot_calls
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-mixed-lifecycle-and-model",
        ))
        assert recovered["success"] is True, recovered
        assert [item["job_id"] for item in recovered["data"]["resolved_jobs"]] == [source["job_id"]]
        assert {item.get("job_id") for item in recovered["data"]["unresolved_items"]} >= {other_job_id}
        assert daemon._job_quiescence_proven(daemon.store.job(source["job_id"])) is True
        assert daemon._job_quiescence_proven(daemon.store.job(other_job_id)) is False
        assert daemon.store.session_recovery_resolution(other_job_id) is None
        assert worker.backend_snapshot_calls == snapshots_before
        blocked = daemon.dispatch({
            "operation": "model.inspect", "arguments": {},
            "execution": {
                "project_id": project_id, "session_id": session_id,
                "model_ref": model_ref, "expected_revision": 0,
                "request_id": "inspect-mixed-unresolved-jobs",
                "idempotency_key": "inspect-mixed-unresolved-jobs",
            },
        })
        assert blocked["success"] is False, blocked
        assert blocked["error"]["code"] in {
            "SESSION_BUSY", "WORKER_RETIRED_OR_UNKNOWN", "EXECUTION_STATE_UNKNOWN",
        }, blocked
        assert worker.backend_snapshot_calls == snapshots_before
    finally:
        daemon.close()


def test_session_lifecycle_recovery_fence_serializes_new_no_modelref_mutation(
    tmp_path, monkeypatch,
):
    worker = _ObservedLifecycleWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, monkeypatch=monkeypatch)
    entered, release, dispatch_started = threading.Event(), threading.Event(), threading.Event()
    original_status = worker.status
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        _uncertain, source, session_id = _unknown_connect_after_terminal_reply(
            monkeypatch, daemon, project_id, "connect-lifecycle-concurrent-fence",
        )
        def blocked_status(request_id, *, timeout_s=1.0):
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("lifecycle recovery status barrier was not released")
            return original_status(request_id, timeout_s=timeout_s)

        worker.status = blocked_status
        recovery_future = pool.submit(daemon.dispatch, _session_mutation_request(
            "session.recover", project_id, session_id, "recover-hold-lifecycle-fence",
        ))
        assert entered.wait(timeout=2)
        connect_before, disconnect_before = worker.connect_calls, worker.disconnect_calls

        def concurrent_disconnect():
            dispatch_started.set()
            return daemon.dispatch(_session_mutation_request(
                "session.disconnect", project_id, session_id, "disconnect-after-recovery-fence",
            ))

        mutation_future = pool.submit(concurrent_disconnect)
        assert dispatch_started.wait(timeout=2)
        assert not mutation_future.done()
        assert worker.connect_calls == connect_before
        assert worker.disconnect_calls == disconnect_before

        release.set()
        recovered = recovery_future.result(timeout=3)
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"][0]["job_id"] == source["job_id"]
        mutation = mutation_future.result(timeout=3)
        assert mutation["success"] is True, mutation
        assert worker.disconnect_calls == disconnect_before + 1
        assert worker.connect_calls == connect_before
    finally:
        release.set()
        pool.shutdown(wait=True)
        daemon.close()


def test_session_recover_admission_fence_blocks_concurrent_model_dispatch(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    entered = threading.Event()
    release = threading.Event()
    original_status = worker.status

    def blocked_status(request_id, *, timeout_s=1.0):
        entered.set()
        if not release.wait(timeout=3):
            raise RuntimeError("recovery status barrier was not released")
        return original_status(request_id, timeout_s=timeout_s)

    worker.status = blocked_status
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-recover-fence"))
        session_id = connected["data"]["session_id"]
        _job_id, _source_record, _original_result, model_ref, _worker_request_id = _unknown_session_model_job(
            daemon, project_id, session_id, worker, "barrier",
        )
        context = daemon.session_registry.get(project_id, session_id)
        snapshots_before = worker.backend_snapshot_calls
        recovery_future = pool.submit(daemon.dispatch, _session_mutation_request(
            "session.recover", project_id, session_id, "recover-with-held-fence",
        ))
        assert entered.wait(timeout=2)

        refused = daemon.dispatch({
            "operation": "model.inspect", "arguments": {},
            "execution": {
                "project_id": project_id, "session_id": session_id,
                "model_ref": model_ref, "expected_revision": 0,
                "request_id": "model-inspect-during-recovery",
                "idempotency_key": "model-inspect-during-recovery",
            },
        })
        assert refused["success"] is False, refused
        assert refused["error"]["code"] == "WORKER_RETIRED_OR_UNKNOWN"
        assert refused["data"]["engine_dispatched"] is False
        assert worker.backend_snapshot_calls == snapshots_before
        assert context.worker is worker

        release.set()
        recovered = recovery_future.result(timeout=3)
        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"]
        assert daemon.session_scheduler.quiescence_snapshot(context)["worker_status"] == "QUIESCENT"
    finally:
        release.set()
        pool.shutdown(wait=True)
        daemon.close()


@pytest.mark.parametrize("guard_mode", ["busy", "unavailable"])
def test_session_recover_does_not_claim_fence_without_acquiring_it(tmp_path, guard_mode):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    entered, release = threading.Event(), threading.Event()
    future = None
    try:
        connected = daemon.dispatch(_connect_request(project_id, f"connect-recover-no-fence-{guard_mode}"))
        session_id = connected["data"]["session_id"]
        _job_id, _source, _result, _model_ref, _worker_request_id = _unknown_session_model_job(
            daemon, project_id, session_id, worker, f"no-fence-{guard_mode}",
        )
        context = daemon.session_registry.get(project_id, session_id)
        status_calls_before = list(worker.status_calls)
        snapshots_before = worker.backend_snapshot_calls

        if guard_mode == "busy":
            def block_accepted_work():
                entered.set()
                assert release.wait(timeout=3)

            future = daemon.session_scheduler.submit(context, block_accepted_work)
            assert entered.wait(timeout=2)
        else:
            @contextmanager
            def unavailable_guard(_worker, _worker_epoch):
                raise SessionSchedulerClosed("injected recovery fence unavailable")
                yield

            daemon.session_scheduler.worker_recovery_admission_guard = unavailable_guard

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, f"recover-no-fence-{guard_mode}",
        ))
        assert recovered["success"] is True, recovered
        assert recovered["data"]["admission_fence"] == "UNAVAILABLE"
        assert recovered["data"]["resolved_jobs"] == []
        assert worker.status_calls == status_calls_before
        assert worker.backend_snapshot_calls == snapshots_before

        if future is not None:
            release.set()
            future.result(timeout=2)
        # Neither a BUSY rejection nor a guard failure leaves a stale fence.
        assert daemon.session_scheduler.submit(
            context, lambda: "admission remains available",
        ).result(timeout=2) == "admission remains available"
    finally:
        release.set()
        daemon.close()


@pytest.mark.parametrize("case", [
    "reply_request_id", "reply_running", "worker_operation_id", "worker_epoch",
    "model_ref", "model_revision", "snapshot_drift", "snapshot_counter_drift",
])
def test_session_recover_keeps_unknown_when_evidence_binding_is_incomplete(tmp_path, case):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, f"connect-recover-negative-{case}"))
        session_id = connected["data"]["session_id"]
        overrides = {}
        result_ref = None
        result_revision = 0
        result_dirty = False
        worker_reply = None
        event_operation_id = None
        if case == "worker_epoch":
            overrides["worker_epoch"] = connected["data"]["worker_epoch"] + 1
        if case == "model_ref":
            result_ref = {**daemon.session_registry.get(project_id, session_id).service.bind_model(
                f"mismatch-ref-{case}"
            )["execution"]["model_ref"], "generation": 99}
        if case == "model_revision":
            result_revision, result_dirty = 2, True
        if case == "reply_request_id":
            worker_reply = {"ok": True, "request_id": "wrong-request", "type": "model",
                            "status": "SUCCEEDED", "result": {"ok": True}}
        if case == "reply_running":
            worker_reply = {"ok": True, "request_id": f"java-recover-{case}", "type": "model",
                            "status": "RUNNING"}
        if case == "worker_operation_id":
            event_operation_id = "different-source-operation"
        job_id, _source, original_result, _model_ref, _request_id = _unknown_session_model_job(
            daemon, project_id, session_id, worker, case,
            binding_overrides=overrides, result_ref=result_ref,
            result_revision=result_revision, result_dirty=result_dirty,
            worker_reply=worker_reply, event_operation_id=event_operation_id,
        )
        durable_model_before = daemon.store.get_metadata("sessions", session_id)["models"][_model_ref["model_tag"]]
        if case == "snapshot_drift":
            worker.model_fingerprint = "different-after-unknown"
        if case == "snapshot_counter_drift":
            worker.model_external_event_counter = 1
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, f"recover-negative-{case}",
        ))
        assert recovered["success"] is True, recovered
        source = daemon.store.job(job_id)
        assert source["status"] == "UNKNOWN"
        assert source["operation"]["status"] == "UNKNOWN"
        assert source["result"] == original_result
        assert "session_recovery_resolution" not in source["metadata"]
        assert daemon.store.session_recovery_resolution(job_id) is None
        assert any(item.get("job_id") == job_id for item in recovered["data"]["unresolved_items"])
        if case in {"snapshot_drift", "snapshot_counter_drift"}:
            assert daemon.store.get_metadata("sessions", session_id)["models"][_model_ref["model_tag"]] == durable_model_before
        if case in {"worker_epoch", "worker_operation_id"}:
            assert worker.status_calls == []
    finally:
        daemon.close()


def test_session_recover_mixed_jobs_resolves_only_the_fully_observed_source(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-recover-mixed"))
        session_id = connected["data"]["session_id"]
        good, _good_record, _good_result, _good_ref, good_request = _unknown_session_model_job(
            daemon, project_id, session_id, worker, "mixed-good",
        )
        running, _running_record, _running_result, _running_ref, running_request = _unknown_session_model_job(
            daemon, project_id, session_id, worker, "mixed-running",
            worker_reply={"ok": True, "request_id": "java-recover-mixed-running",
                          "type": "model", "status": "RUNNING"},
        )
        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-mixed-jobs",
        ))
        assert recovered["success"] is True, recovered
        assert [item["job_id"] for item in recovered["data"]["resolved_jobs"]] == [good]
        assert any(item["job_id"] == running for item in recovered["data"]["unresolved_items"])
        assert daemon._job_quiescence_proven(daemon.store.job(good)) is True
        assert daemon._job_quiescence_proven(daemon.store.job(running)) is False
        assert set(worker.status_calls) == {good_request, running_request}
    finally:
        daemon.close()


def test_session_reconnect_from_connected_binding_retires_old_epoch_before_reattach(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-reconnect-live"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        old_context = daemon.session_registry.get(project_id, session_id)
        old_epoch = old_context.worker_epoch

        reconnected = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-live",
        ))
        assert reconnected["success"] is True, reconnected
        assert worker.disconnect_calls == 1
        assert worker.connect_calls == 2
        assert worker.start_calls == 1
        assert reconnected["data"]["worker_reused"] is True
        assert reconnected["data"]["worker_epoch"] > old_epoch
        new_context = daemon.session_registry.get(project_id, session_id)
        assert new_context is not old_context
        assert new_context.worker is worker
        assert new_context.worker_epoch == reconnected["data"]["worker_epoch"]
        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            daemon.session_scheduler.submit(old_context, lambda: None)
        assert daemon.session_scheduler.submit(
            new_context, lambda: "new epoch admission open",
        ).result(timeout=2) == "new epoch admission open"
    finally:
        daemon.close()


def test_session_disconnect_refuses_binding_with_accepted_worker_task(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    entered, release = threading.Event(), threading.Event()
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-disconnect-busy"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        context = daemon.session_registry.get(project_id, session_id)

        def block():
            entered.set()
            assert release.wait(3)

        future = daemon.session_scheduler.submit(context, block)
        assert entered.wait(1)
        refused = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-busy",
        ))
        assert refused["success"] is False
        assert refused["error"]["code"] == "SESSION_BUSY"
        assert refused["data"]["engine_dispatched"] is False
        assert worker.disconnect_calls == 0
        release.set()
        future.result(timeout=2)

        succeeded = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-after-drain",
        ))
        assert succeeded["success"] is True, succeeded
        assert worker.disconnect_calls == 1
    finally:
        release.set()
        daemon.close()


def _interrupted_session_start(daemon: ControlDaemon, project_id: str, session_id: str,
                               *, lifecycle: dict | None = None) -> dict:
    from comsol_mcp._execution_contract import canonical_request_hash

    runtime_id = "fixture-runtime"
    if lifecycle is None:
        lifecycle = new_lifecycle_record(
            project_id=project_id, session_id=session_id, state="STARTING",
            runtime_id=runtime_id, endpoint=None,
            client_state="DISCONNECTED", server_state="UNKNOWN", server_ownership="unknown",
        )
    daemon.session_lifecycle.save(lifecycle)
    semantic_arguments = {"runtime_id": runtime_id, "options": {}, "resources": {}}
    request_id = f"legacy-start-request-{session_id}"
    idempotency_key = f"legacy-start-key-{session_id}"
    record, reused = daemon.store.begin(
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_hash=canonical_request_hash(
            "session.start", semantic_arguments, None, None, project_id=project_id,
        ),
        operation="session.start",
        metadata={
            "operation": "session.start", "project_id": project_id,
            "session_id": session_id, "runtime_id": runtime_id,
            "arguments": semantic_arguments,
            "execution": {"project_id": project_id, "session_id": session_id},
        },
    )
    assert reused is False
    daemon._start_session_mutation_record(record, operation="session.start", session_id=session_id)
    assert daemon.store.reconcile_after_restart() == [record["job_id"]]
    daemon._control_singleton_lock_held = True
    return record


def _empty_host_start_quiescence() -> dict:
    return {
        "observed_at_utc": "2026-09-29T03:30:00.000Z",
        "process_inventory": {
            "status": "COMPLETE", "candidate_count": 0,
            "unresolved_candidate_count": 0, "task_owned_match_count": 0,
            "canonical_engine_match_count": 0,
        },
        "listener_inventory": {
            "status": "COMPLETE", "rows_scanned": 56,
            "owner_query_unresolved_count": 0, "task_owned_match_count": 0,
            "canonical_engine_match_count": 0,
        },
    }


def test_session_recover_abandons_only_exact_interrupted_start_after_complete_host_observation(
        tmp_path, monkeypatch):
    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "start-recovery-positive")
        project_id = project["project_id"]
        session_id = "session-legacy-start"
        source = _interrupted_session_start(daemon, project_id, session_id)
        monkeypatch.setattr(
            daemon, "_observe_session_start_quiescence",
            lambda *_args: _empty_host_start_quiescence(),
        )

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-legacy-start-once",
        ))

        assert recovered["success"] is True, json.dumps(recovered, sort_keys=True)
        data = recovered["data"]
        assert data["recovery_status"] == "PARTIAL_HISTORICAL_UNKNOWN_RETAINED"
        assert data["replayed_requests"] == 0
        assert data["new_worker_created"] is False
        assert len(data["resolved_jobs"]) == 1
        assert data["resolved_jobs"][0]["job_id"] == source["job_id"]
        assert data["resolved_jobs"][0]["outcome_resolution"] == "UNVERIFIED_HISTORICAL_UNKNOWN"
        assert data["resolved_jobs"][0]["quiescence_resolution"] == "PROVEN_AND_AUDITED"
        assert data["resolved_jobs"][0]["lifecycle_state_after"] == "STOPPED"

        source_after = daemon.store.job(source["job_id"])
        assert source_after["status"] == source_after["operation"]["status"] == "UNKNOWN"
        assert source_after["result"]["success"] is False
        assert source_after["result"]["data"]["server_birth_observed"] == "UNKNOWN"
        assert source_after["result"]["data"]["server_exit_code"] == "UNKNOWN"
        assert source_after["metadata"]["reconciled_quiescent"] is True
        assert daemon._job_quiescence_proven(source_after) is True

        proof = daemon.store.session_lifecycle_recovery_resolution(source["job_id"])
        assert proof["event"] == "SessionLifecycleRecoveryResolution"
        assert proof["metadata"]["resolution_scope"] == (
            "ABANDONED_UNKNOWN_START_CURRENT_HOST_QUIESCENCE"
        )
        assert proof["metadata"]["source_job_id"] == source["job_id"]
        assert proof["metadata"]["session_recovery_operation_id"] == recovered["execution"]["operation_id"]
        assert proof["metadata"]["replay_performed"] is False
        assert proof["metadata"]["new_worker_created"] is False

        lifecycle = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle["state"] == "STOPPED"
        assert lifecycle["endpoint"] is None
        assert lifecycle["server_process_identity"] is None
    finally:
        daemon.close()


def test_verified_interrupted_start_resolution_survives_restart_and_reopens_admission(
        tmp_path, monkeypatch):
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    daemon = _daemon(tmp_path)
    try:
        project_result = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "start-recovery-restart",
                "workspace": "start-recovery-restart",
                "policy": {"permissions": ["inspect", "project_write", "compute", "host_control"]},
            },
            "execution": {"request_id": "create-start-recovery-restart",
                          "idempotency_key": "create-start-recovery-restart"},
        })
        assert project_result["success"] is True, project_result
        project_id = project_result["data"]["project"]["project_id"]
        session_id = "session-start-recovery-restart"
        source = _interrupted_session_start(daemon, project_id, session_id)

        # A second UNKNOWN start with a caller-set marker but no proof must
        # still be reconciled. Only the daemon's full proof verifier may keep
        # a historic UNKNOWN row out of restart reconciliation.
        other_project = _create_project(daemon, "start-recovery-restart-unverified")
        unverified = _interrupted_session_start(
            daemon, other_project["project_id"], "session-start-recovery-unverified",
        )
        daemon.store.update_job(unverified["job_id"], "UNKNOWN", {"reconciled_quiescent": True})

        monkeypatch.setattr(
            daemon, "_observe_session_start_quiescence",
            lambda *_args: _empty_host_start_quiescence(),
        )
        first_recovery = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-start-before-restart",
        ))
        assert first_recovery["success"] is True, first_recovery
        source_before_restart = daemon.store.job(source["job_id"])
        assert source_before_restart["status"] == source_before_restart["operation"]["status"] == "UNKNOWN"
        assert daemon._session_start_lifecycle_recovery_resolution_is_valid(source_before_restart) is True
    finally:
        daemon.close()

    restarted = ControlDaemon(
        tmp_path / "control", project_root=tmp_path / "workspaces", registry={},
    )
    try:
        source_after_restart = restarted.store.job(source["job_id"])
        assert source_after_restart["status"] == source_after_restart["operation"]["status"] == "UNKNOWN"
        assert restarted._session_start_lifecycle_recovery_resolution_is_valid(source_after_restart) is True
        assert restarted.session_lifecycle.get(project_id, session_id)["state"] == "STOPPED"

        unverified_after_restart = restarted.store.job(unverified["job_id"])
        assert (unverified_after_restart["status"]
                == unverified_after_restart["operation"]["status"] == "RECONCILING")

        monkeypatch.setattr(
            restarted, "_observe_session_start_quiescence",
            lambda *_args: pytest.fail("an already verified historical start must not be reprobed"),
        )
        repeated_recovery = restarted.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-start-after-restart",
        ))
        assert repeated_recovery["success"] is True, repeated_recovery
        assert repeated_recovery["data"]["resolved_jobs"][0]["quiescence_resolution"] == "ALREADY_PROVEN"

        inspected = restarted.dispatch({
            "operation": "session.inspect",
            "arguments": {"project_id": project_id, "session_id": session_id},
            "execution": {},
        })
        assert inspected["success"] is True, inspected
        assert inspected["data"]["lifecycle"]["state"] == "STOPPED"

        def stop_before_runtime(*_args):
            raise RuntimeError("synthetic prebirth runtime stop")

        monkeypatch.setattr(restarted, "_resolve_session_runtime", stop_before_runtime)
        next_start = restarted.dispatch({
            "operation": "session.start",
            "arguments": {
                "project_id": project_id, "runtime_id": "fixture-runtime",
                "idempotency_key": "new-start-after-recovery-restart",
            },
            "execution": {},
        })
        assert next_start["success"] is False
        assert next_start["error"]["code"] == "RUNTIME_CONFIGURATION_REQUIRED"
        assert next_start["data"]["server_birth_performed"] is False
        source_final = restarted.store.job(source["job_id"])
        assert source_final["status"] == source_final["operation"]["status"] == "UNKNOWN"
        assert restarted._session_start_lifecycle_recovery_resolution_is_valid(source_final) is True
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "inventory_change",
    [
        {"process_inventory": {"task_owned_match_count": 1}},
        {"process_inventory": {"unresolved_candidate_count": 1}},
        {"listener_inventory": {"owner_query_unresolved_count": 1}},
    ],
    ids=["task-process-match", "incomplete-process-inventory", "unresolved-listener-owner"],
)
def test_session_recover_keeps_interrupted_start_unknown_without_complete_quiescence(
        tmp_path, monkeypatch, inventory_change):
    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "start-recovery-negative")
        project_id = project["project_id"]
        session_id = "session-start-not-quiescent"
        source = _interrupted_session_start(daemon, project_id, session_id)
        observation = _empty_host_start_quiescence()
        for section, fields in inventory_change.items():
            observation[section].update(fields)
        monkeypatch.setattr(daemon, "_observe_session_start_quiescence", lambda *_args: observation)

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-start-negative",
        ))

        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("reason") == "CURRENT_HOST_QUIESCENCE_NOT_PROVEN"
                   for item in recovered["data"]["unresolved_items"])
        source_after = daemon.store.job(source["job_id"])
        assert source_after["status"] == source_after["operation"]["status"] == "UNKNOWN"
        assert source_after["result"]["success"] is False
        assert source_after["metadata"].get("reconciled_quiescent") is not True
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
    finally:
        daemon.close()


def test_session_recover_rechecks_recorded_startup_pid_birth_before_quiescence_proof(
        tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as control_daemon_module

    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "start-recovery-pid")
        project_id = project["project_id"]
        session_id = "session-start-pid-observed"
        source = _interrupted_session_start(daemon, project_id, session_id)
        startup_pid = 39001
        startup_birth = 1790641470225
        daemon.store.add_event(source["job_id"], "SessionServerStartupObservation", {
            "schema": "COMSOL_OWNED_SERVER_STARTUP_DIAGNOSTIC_V1",
            "event": "BIRTH_OBSERVED",
            "observed_at_utc": "2026-09-29T03:30:00.000Z",
            "project_id": project_id, "session_id": session_id,
            "runtime_id": "fixture-runtime", "pid": startup_pid,
            "birth": f"start_epoch_ms:{startup_birth}", "exit_code": None,
            "exception_type": None, "errno": None, "winerror": None,
            "error_category": None,
            "request_id": source["request_id"],
            "idempotency_key": source["idempotency_key"],
            "request_hash": source["request_hash"],
            "operation_id": source["operation_id"], "job_id": source["job_id"],
        })
        original_identity = control_daemon_module.process_identity

        def process_identity(pid):
            if pid == startup_pid:
                return {"alive": True, "start_epoch_ms": startup_birth}
            return original_identity(pid)

        monkeypatch.setattr(control_daemon_module, "process_identity", process_identity)
        monkeypatch.setattr(
            daemon, "_observe_session_start_quiescence",
            lambda *_args: _empty_host_start_quiescence(),
        )

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-start-live-pid",
        ))

        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        observation = recovered["data"]["request_observations"][0]
        assert observation["process_inventory"]["startup_pid_live_count"] == 1
        assert observation["process_inventory"]["startup_pid_unresolved_count"] == 0
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
    finally:
        daemon.close()


def test_interrupted_start_refuses_to_clear_existing_server_or_worker_identity(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "start-recovery-preserve-identity")
        project_id = project["project_id"]
        session_id = "session-start-has-identities"
        endpoint = {"host": "127.0.0.1", "port": 2046}
        lifecycle = new_lifecycle_record(
            project_id=project_id, session_id=session_id, state="UNKNOWN",
            runtime_id="fixture-runtime", endpoint=endpoint,
            client_state="UNKNOWN", server_state="UNKNOWN", server_ownership="unknown",
            worker_instance_id="preserve-worker-id", worker_epoch=7,
            server_instance_id="preserve-server-epoch",
            server_process_identity={
                "pid": 39002, "birth": "start_epoch_ms:1790641470226",
                "executable": r"C:\COMSOL\bin\win64\comsolmphserver.exe",
                "runtime_id": "fixture-runtime", "endpoint": endpoint,
            },
        )
        source = _interrupted_session_start(daemon, project_id, session_id, lifecycle=lifecycle)
        daemon._observe_session_start_quiescence = lambda *_args: pytest.fail(
            "existing lifecycle identities must not be cleared or treated as legacy"
        )

        recovered = daemon.dispatch(_session_mutation_request(
            "session.recover", project_id, session_id, "recover-start-existing-identity",
        ))

        assert recovered["success"] is True, recovered
        assert recovered["data"]["resolved_jobs"] == []
        assert any(item.get("reason") == "SESSION_LIFECYCLE_NOT_INTERRUPTED_START"
                   for item in recovered["data"]["unresolved_items"])
        current = daemon.session_lifecycle.get(project_id, session_id)
        assert current == lifecycle
        assert daemon.store.session_lifecycle_recovery_resolution(source["job_id"]) is None
    finally:
        daemon.close()


def test_session_disconnect_preflight_identity_mismatch_fences_context_and_records_unknown(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-disconnect-preflight-mismatch"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        context = daemon.session_registry.get(project_id, session_id)
        worker.metadata["instance_id"] = "different-worker-instance"

        result = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-preflight-mismatch",
        ))

        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["error"]["safe_retry"] is False
        assert result["data"]["engine_dispatched"] is False
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)
        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            daemon.session_scheduler.submit(context, lambda: None)
    finally:
        daemon.close()


def test_session_reconnect_disconnected_health_conflict_is_unknown_without_worker_birth(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-reconnect-preflight-conflict"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        disconnected = daemon.dispatch(_session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-before-preflight-conflict",
        ))
        assert disconnected["success"] is True, disconnected
        expected_epoch = daemon.session_lifecycle.get(project_id, session_id)["worker_epoch"]

        # The durable lifecycle says detached, but the retained exact Worker
        # reports a live connection. Reconnect must not reinterpret this as a
        # safe detached baseline or issue another attach.
        worker.metadata.update(connected=True, generation=expected_epoch, server="127.0.0.1:2046")
        disconnect_calls = worker.disconnect_calls
        connect_calls = worker.connect_calls

        result = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-preflight-conflict",
        ))

        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["error"]["safe_retry"] is False
        assert result["data"]["state"] == "UNKNOWN"
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.disconnect_calls == disconnect_calls
        assert worker.connect_calls == connect_calls
        assert worker.start_calls == 1
        assert worker.close_calls == 0
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)
    finally:
        daemon.close()


def test_session_lifecycle_mutations_are_idempotent_during_a_concurrent_unknown_wait(tmp_path):
    entered, release = threading.Event(), threading.Event()
    worker = _InjectedConnectWorker(disconnect_block=(entered, release))
    daemon, project_id = _connect_daemon(tmp_path, worker)
    request = None
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-concurrent-disconnect"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        request = _session_mutation_request(
            "session.disconnect", project_id, session_id, "disconnect-concurrent-idempotency",
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_future = pool.submit(daemon.dispatch, request)
            assert entered.wait(2)
            duplicate = daemon.dispatch(request)
            assert duplicate["success"] is True
            assert duplicate["data"]["status"] == "RUNNING"
            assert worker.disconnect_calls == 1
            release.set()
            first = first_future.result(timeout=3)
        assert first["success"] is True, first
        assert worker.disconnect_calls == 1
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "DISCONNECTED"
    finally:
        release.set()
        daemon.close()


def test_unresolved_worker_job_blocks_disconnect_and_reconnect(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        connected = daemon.dispatch(_connect_request(project_id, "connect-unresolved-worker-job"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        pending, _ = daemon.store.begin(
            request_id="unresolved-session-work-request",
            idempotency_key="unresolved-session-work-key",
            request_hash="unresolved-session-work-hash",
            operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": session_id}},
        )
        daemon.store.update_job(pending["job_id"], "UNKNOWN")

        for operation, key in (("session.disconnect", "disconnect-with-unknown-job"),
                               ("session.reconnect", "reconnect-with-unknown-job")):
            refused = daemon.dispatch(_session_mutation_request(operation, project_id, session_id, key))
            assert refused["success"] is False
            assert refused["error"]["code"] == "SESSION_BUSY"
            assert refused["data"]["engine_dispatched"] is False
            assert refused["data"]["blocking_job_ids"] == [pending["job_id"]]

        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "CONNECTED"
        assert worker.disconnect_calls == 0
        assert worker.connect_calls == 1
        assert worker.close_calls == 0
    finally:
        daemon.close()


@pytest.mark.parametrize("missing_binding", [
    "disconnect_worker", "disconnect_context_worker_mismatch",
    "reconnect_worker", "reconnect_runtime", "reconnect_backend",
])
def test_unknown_lifecycle_binding_quarantines_new_admission_and_drains_accepted_work(tmp_path, missing_binding):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    first_entered, release_first = threading.Event(), threading.Event()
    queued_ran = threading.Event()
    try:
        connected = daemon.dispatch(_connect_request(project_id, f"connect-quarantine-{missing_binding}"))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        context = daemon.session_registry.get(project_id, session_id)

        def first_accepted_task():
            first_entered.set()
            assert release_first.wait(3)
            return "first-drained"

        first = daemon.session_scheduler.submit(context, first_accepted_task)
        assert first_entered.wait(1)
        queued = daemon.session_scheduler.submit(context, lambda: queued_ran.set() or "queued-drained")

        if missing_binding == "disconnect_worker":
            daemon._session_worker_handles.pop((project_id, session_id))
            operation = "session.disconnect"
        elif missing_binding == "disconnect_context_worker_mismatch":
            daemon._session_worker_handles[(project_id, session_id)] = _InjectedConnectWorker()
            operation = "session.disconnect"
        else:
            operation = "session.reconnect"
            if missing_binding == "reconnect_worker":
                daemon._session_worker_handles.pop((project_id, session_id))
            elif missing_binding == "reconnect_runtime":
                daemon._session_runtime_configs.pop((project_id, session_id))
            elif missing_binding == "reconnect_backend":
                daemon._session_backends.pop((project_id, session_id))

        refused = daemon.dispatch(_session_mutation_request(
            operation, project_id, session_id, f"quarantine-{missing_binding}",
        ))
        assert refused["success"] is False
        assert refused["error"]["code"] == "WORKER_BINDING_UNKNOWN"
        assert refused["error"]["execution_state_unknown"] is True
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        with pytest.raises(SessionContextMissing):
            daemon.session_registry.get(project_id, session_id)
        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            daemon.session_scheduler.submit(context, lambda: pytest.fail("new work reached unknown binding"))
        assert not first.done()
        assert not queued.done()

        # Existing accepted work, including queued work, drains normally after
        # the uncertain binding closes admission.
        release_first.set()
        assert first.result(timeout=2) == "first-drained"
        assert queued.result(timeout=2) == "queued-drained"
        assert queued_ran.is_set()
        assert worker.connect_calls == 1
        assert worker.disconnect_calls == 0
        assert worker.close_calls == 0
    finally:
        release_first.set()
        daemon.close()


def test_session_runtime_jdk_executable_names_follow_java_worker_platform(tmp_path):
    from comsol_mcp._control_daemon import ControlDaemon

    java_home = tmp_path / "jdk"
    bin_dir = java_home / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "java.exe").touch()
    (bin_dir / "javac.exe").touch()
    assert ControlDaemon._session_jdk_tools(java_home, platform_name="nt") == (
        bin_dir / "java.exe", bin_dir / "javac.exe",
    )


def test_session_connect_keeps_unknown_handle_when_pre_dispatch_worker_close_fails(tmp_path):
    worker = _StartAndRetirementFailureWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-close-failure"))
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["data"]["worker_handle_preserved"] is True
        assert result["data"]["worker_close_failure_type"] == "RuntimeError"
        session_id = result["data"]["session_id"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.connect_calls == 0
        assert worker.close_calls == 1
        blocked = daemon.dispatch(_connect_request(project_id, "connect-after-close-failure"))
        assert blocked["error"]["code"] == "SESSION_ALREADY_ACTIVE"
        assert worker.start_calls == 1
    finally:
        daemon.close()


def test_session_connect_post_attach_service_initialization_failure_is_unknown(tmp_path, monkeypatch):
    import comsol_mcp._managed_backend as managed_backend

    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)

    def fail_service(*_args, **_kwargs):
        raise OSError("fixture durable service setup failure")

    monkeypatch.setattr(managed_backend, "ExecutionService", fail_service)
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-init-failure"))
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["data"]["engine_dispatched"] is True
        assert result["data"]["worker_handle_preserved"] is True
        session_id = result["data"]["session_id"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.connect_calls == 1
        assert worker.close_calls == 0
        blocked = daemon.dispatch(_connect_request(project_id, "connect-after-init-failure"))
        assert blocked["error"]["code"] == "SESSION_ALREADY_ACTIVE"
        assert worker.connect_calls == 1
    finally:
        daemon.close()


def test_session_connect_contract_error_after_attach_is_unknown(tmp_path, monkeypatch):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker)

    def reject_register(_context):
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "fixture registry rejection")

    monkeypatch.setattr(daemon.session_registry, "register", reject_register)
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-register-failure"))
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["data"]["cause_code"] == "MODEL_IDENTITY_MISMATCH"
        assert result["data"]["engine_dispatched"] is True
        assert result["data"]["worker_handle_preserved"] is True
        session_id = result["data"]["session_id"]
        lifecycle = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle["state"] == "UNKNOWN"
        assert lifecycle["worker_instance_id"] == "worker-fixture-1"
        assert lifecycle["worker_epoch"] == 2
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.close_calls == 0
        blocked = daemon.dispatch(_connect_request(project_id, "connect-after-contract-error"))
        assert blocked["error"]["code"] == "SESSION_ALREADY_ACTIVE"
        assert worker.connect_calls == 1
    finally:
        daemon.close()


def test_session_connect_duplicate_idempotency_waits_without_second_worker_birth(tmp_path):
    entered, release = threading.Event(), threading.Event()
    worker = _InjectedConnectWorker(block=(entered, release))
    daemon, project_id = _connect_daemon(tmp_path, worker)
    request = _connect_request(project_id, "connect-concurrent")
    factory_calls = 1
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(daemon.dispatch, request)
            assert entered.wait(timeout=3)
            duplicate = daemon.dispatch(request)
            assert duplicate["success"] is True
            assert duplicate["data"]["status"] == "RUNNING"
            assert worker.connect_calls == factory_calls
            release.set()
            completed = first.result(timeout=5)
        assert completed["success"] is True
        assert worker.connect_calls == 1
    finally:
        release.set()
        daemon.close()


def test_session_connect_timeout_is_durable_unknown_and_never_replays_worker_birth(tmp_path):
    entered, release = threading.Event(), threading.Event()
    worker = _InjectedConnectWorker(timeout=True, block=(entered, release))
    daemon, project_id = _connect_daemon(tmp_path, worker)
    request = _connect_request(project_id, "connect-unknown")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(daemon.dispatch, request)
            assert entered.wait(timeout=3)
            duplicate = daemon.dispatch(request)
            assert duplicate["success"] is True
            assert duplicate["data"]["status"] == "RUNNING"
            assert worker.connect_calls == 1
            release.set()
            first = pending.result(timeout=5)
        assert first["success"] is False
        assert first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert first["data"]["worker_handle_preserved"] is True
        session_id = first["data"]["session_id"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.close_calls == 0

        replay = daemon.dispatch(request)
        assert replay == first
        assert worker.connect_calls == 1

        new_key = daemon.dispatch(_connect_request(project_id, "connect-after-unknown"))
        assert new_key["success"] is False
        assert new_key["error"]["code"] == "SESSION_ALREADY_ACTIVE"
        assert worker.connect_calls == 1
    finally:
        release.set()
        daemon.close()


def test_session_connect_without_os_observed_peer_stays_unknown_and_keeps_worker(tmp_path):
    worker = _InjectedConnectWorker()
    daemon, project_id = _connect_daemon(tmp_path, worker, peer=None)
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-no-peer"))
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["data"]["worker_handle_preserved"] is True
        session_id = result["data"]["session_id"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon.session_registry.list_for_project(project_id) == ()
        assert daemon._session_worker_handles[(project_id, session_id)] is worker
        assert worker.close_calls == 0
    finally:
        daemon.close()


def test_session_worker_start_timeout_is_unknown_without_claiming_remote_dispatch(tmp_path):
    worker = _InjectedConnectWorker(start_timeout=True)
    daemon, project_id = _connect_daemon(tmp_path, worker)
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-start-timeout"))
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["data"]["engine_dispatched"] is False
        assert result["data"]["worker_handle_preserved"] is True
        session_id = result["data"]["session_id"]
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert worker.connect_calls == 0
        assert worker.close_calls == 0
        assert daemon.dispatch(_connect_request(project_id, "connect-start-timeout-retry"))["error"]["code"] == "SESSION_ALREADY_ACTIVE"
        assert worker.start_calls == 1
    finally:
        daemon.close()


def test_session_connect_requires_project_write_before_worker_creation(tmp_path):
    daemon = _daemon(tmp_path)
    try:
        project = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "inspect-only", "workspace": "inspect-only",
                          "policy": {"permissions": ["inspect"]}},
            "execution": {"request_id": "inspect-only-create", "idempotency_key": "inspect-only-create"},
        })["data"]["project"]
        result = daemon.dispatch(_connect_request(project["project_id"], "connect-denied"))
        assert result["success"] is False
        assert result["error"]["code"] == "PERMISSION_DENIED"
        with daemon.store.lock:
            row = daemon.store.db.execute(
                "SELECT operation_id FROM operations WHERE idempotency_key=?", ("connect-denied",),
            ).fetchone()
        assert row is None
    finally:
        daemon.close()


def test_session_connect_secrets_are_resolved_locally_and_never_persisted(tmp_path):
    worker = _InjectedConnectWorker()
    secret_ref = "private-ref-value"
    username, password = "fixture-user", "fixture-password"
    daemon, project_id = _connect_daemon(
        tmp_path, worker,
        credentials_resolver=lambda reference: {"user": username, "password": password}
        if reference == secret_ref else {},
    )
    try:
        result = daemon.dispatch(_connect_request(project_id, "connect-with-secret", credentials_ref=secret_ref))
        assert result["success"] is True, result
        assert result["data"]["credentials_configured"] is True
        operation_id = result["execution"]["operation_id"]
        operation = daemon.store.get_operation(operation_id)
        job = daemon.store.operation_job(operation_id)
        events = daemon.store.events(job["job_id"])
        persisted = str(operation) + str(job) + str(events)
        assert secret_ref not in persisted
        assert password not in persisted
        assert username not in persisted
        worker_event = next(event for event in events if event["event"] == "worker_request")
        assert worker_event["metadata"]["metadata"]["password"] == "[REDACTED]"
        assert worker_event["metadata"]["metadata"]["user"] == "[REDACTED]"
    finally:
        daemon.close()


def test_session_reconnect_reresolves_opaque_credentials_reference_before_detach(tmp_path):
    worker = _InjectedConnectWorker()
    secret_ref = "credential-ref-for-reconnect"
    expected = {"user": "reconnect-user", "password": "reconnect-password"}
    resolved_refs = []

    def resolver(reference):
        resolved_refs.append(reference)
        return dict(expected)

    daemon, project_id = _connect_daemon(tmp_path, worker, credentials_resolver=resolver)
    try:
        connected = daemon.dispatch(_connect_request(
            project_id, "connect-authenticated-reconnect", credentials_ref=secret_ref,
        ))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        assert resolved_refs == [secret_ref]
        assert daemon._session_credentials_refs[(project_id, session_id)] == secret_ref

        reconnected = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-authenticated",
        ))

        assert reconnected["success"] is True, reconnected
        assert resolved_refs == [secret_ref, secret_ref]
        assert worker.connect_credentials == [expected, expected]
        assert worker.disconnect_calls == 1
        assert worker.connect_calls == 2
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "CONNECTED"
        operation = daemon.store.get_operation(reconnected["execution"]["operation_id"])
        job = daemon.store.operation_job(reconnected["execution"]["operation_id"])
        events = daemon.store.events(job["job_id"])
        persisted = str(operation) + str(job) + str(events)
        for secret in (secret_ref, expected["user"], expected["password"]):
            assert secret not in persisted
            assert secret not in str(reconnected)
    finally:
        daemon.close()


@pytest.mark.parametrize("resolver_mode", ["missing", "raises"])
def test_session_reconnect_credentials_failure_happens_before_any_worker_rpc(tmp_path, resolver_mode):
    worker = _InjectedConnectWorker()
    secret_ref = "credential-ref-unavailable-after-connect"
    daemon, project_id = _connect_daemon(
        tmp_path, worker,
        credentials_resolver=lambda reference: {"user": "u", "password": "p"}
        if reference == secret_ref else {},
    )
    try:
        connected = daemon.dispatch(_connect_request(
            project_id, f"connect-auth-preflight-{resolver_mode}", credentials_ref=secret_ref,
        ))
        assert connected["success"] is True, connected
        session_id = connected["data"]["session_id"]
        original_context = daemon.session_registry.get(project_id, session_id)
        if resolver_mode == "missing":
            daemon.session_credentials_resolver = None
        else:
            def unavailable(_reference):
                raise RuntimeError("fixture credential backend offline")
            daemon.session_credentials_resolver = unavailable
        disconnect_calls = worker.disconnect_calls
        connect_calls = worker.connect_calls

        refused = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, f"reconnect-auth-preflight-{resolver_mode}",
        ))

        assert refused["success"] is False
        assert refused["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert refused["error"]["safe_retry"] is True
        assert refused["data"]["engine_dispatched"] is False
        assert refused["data"]["worker_handle_preserved"] is True
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "CONNECTED"
        assert daemon.session_registry.get(project_id, session_id) is original_context
        assert worker.disconnect_calls == disconnect_calls
        assert worker.connect_calls == connect_calls
        assert worker.start_calls == 1
        assert worker.close_calls == 0
        assert secret_ref not in str(refused)
        assert "fixture credential backend offline" not in str(refused)
    finally:
        daemon.close()


def _runtime_context(project_id: str, session_id: str, *, pid: int, port: int, worker: object, backend=None):
    socket = CanonicalSocket("127.0.0.1", port)
    process = OwnedServerProcessIdentity(
        pid=pid,
        birth=f"test-birth-{pid}",
        executable="/opt/comsol/server",
        listener_sockets=(socket,),
        start_epoch_ms=123456,
    )
    runtime = SessionRuntimeConfig(
        runtime_id=f"runtime-{pid}",
        comsol_version="6.4.0.293",
        installation_root=Path("/opt/comsol64"),
        java_executable=Path("/opt/comsol64/java/bin/java"),
        classpath=(Path("/opt/comsol64/plugins/client.jar"),),
        preferences_dir=Path(f"/private/tmp/prefs-{pid}"),
        session_state_root=Path(f"/private/tmp/session-state-{pid}"),
    )
    if backend is None:
        backend = SimpleNamespace(
            name=session_id,
            service=SimpleNamespace(ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"})),
            host_permission_ceiling={"inspect", "project_write", "compute"},
            worker=worker,
        )
    return SessionRuntimeContext(
        project_id=project_id,
        session_id=session_id,
        project_root=Path(f"/private/tmp/workspace-{project_id}"),
        runtime=runtime,
        endpoint=SessionEndpointIdentity(
            host="127.0.0.1", port=port, worker_epoch=1,
            observed_peer=socket, owned_process=process,
        ),
        backend=backend,
        worker_instance_id=f"worker-{pid}",
        worker=worker,
        server_ownership="mcp_managed",
        process_identity=process,
    )


def _request(project_id: str, session_id: str, key: str):
    return {
        "operation": "test_session_engine_action",
        "arguments": {},
        "execution": {
            "project_id": project_id,
            "session_id": session_id,
            "request_id": f"request-{key}",
            "idempotency_key": key,
        },
    }


def _engine_daemon(tmp_path: Path):
    daemon = _daemon(tmp_path, registry={"test_session_engine_action": lambda _args: None})
    project = _create_project(daemon, "session-engine")
    return daemon, project["project_id"]


class _InvocationBackend:
    """Narrow backend double; ControlDaemon._execute remains production code."""

    def __init__(self, name: str, *, worker=None, service=None):
        self.name = name
        self.worker = worker
        self.service = service or SimpleNamespace(
            ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"}),
        )
        self.host_permission_ceiling = {"inspect", "project_write", "compute"}
        self.registry = {"test_session_engine_action": lambda _arguments: None}
        self.project_root_explicit = True
        self.project_root = Path("/private/tmp") / f"{name}-project-root"
        self.worker_identity = None
        self.invoke_hook = None
        self.calls = []

    def project_root_scope(self, _root):
        return nullcontext()

    def model_project_binding(self, _model_ref):
        return None

    def apply_timeout_caps(self, _project_id, timeouts):
        return timeouts

    def invoke(self, operation, arguments, _execution, _operation_id, _worker_event):
        try:
            context = current_session_context()
        except SessionContextMissing:
            context = None
        self.calls.append((operation, context))
        if self.invoke_hook is not None:
            return self.invoke_hook(operation, arguments, context)
        return {"success": True, "data": {"backend": self.name,
                                            "session_id": context.session_id if context else None}}

    def close(self):
        pass


def test_control_daemon_routes_distinct_owned_servers_concurrently(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    barrier = threading.Barrier(2)
    try:
        first = _runtime_context(project_id, "session-6.3", pid=6103, port=2603, worker=object())
        second = _runtime_context(project_id, "session-6.4", pid=6104, port=2604, worker=object())
        daemon.session_registry.register(first)
        daemon.session_registry.register(second)
        monkeypatch.setitem(
            __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
            "test_session_engine_action", "inspect",
        )
        original_execute = daemon._execute

        def execute(record, operation, arguments, execution, timeouts, submitted, **kwargs):
            context = current_session_context()
            barrier.wait(timeout=2)
            return daemon._finish(record, {
                "success": True,
                "data": {"session_id": context.session_id, "backend": context.backend.name},
            }, "SUCCEEDED")

        daemon._execute = execute
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "distinct-one"))
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "distinct-two"))
            result_one, result_two = one.result(timeout=4), two.result(timeout=4)
        assert result_one["success"] is True
        assert result_two["success"] is True
        assert {result_one["data"]["session_id"], result_two["data"]["session_id"]} == {first.session_id, second.session_id}
    finally:
        daemon.close()


def test_control_daemon_serializes_same_server_across_sessions(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    try:
        first = _runtime_context(project_id, "session-a", pid=6110, port=2610, worker=object())
        second = _runtime_context(project_id, "session-b", pid=6110, port=2610, worker=object())
        daemon.session_registry.register(first)
        daemon.session_registry.register(second)
        monkeypatch.setitem(
            __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
            "test_session_engine_action", "inspect",
        )

        def execute(record, _operation, _arguments, execution, _timeouts, _submitted, **_kwargs):
            context = current_session_context()
            if context.session_id == first.session_id:
                entered.set()
                assert release.wait(timeout=3)
            else:
                second_entered.set()
            return daemon._finish(record, {"success": True, "data": {"session_id": context.session_id}}, "SUCCEEDED")

        daemon._execute = execute
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "same-one"))
            assert entered.wait(timeout=2)
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "same-two"))
            assert not second_entered.wait(timeout=0.15)
            release.set()
            assert one.result(timeout=2)["success"] is True
            assert two.result(timeout=2)["success"] is True
            assert second_entered.is_set()
    finally:
        release.set()
        daemon.close()


def test_control_daemon_production_execute_routes_to_each_session_backend(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    barrier = threading.Barrier(2)
    first_backend = _InvocationBackend("session-6.3")
    second_backend = _InvocationBackend("session-6.4")
    default_backend = _InvocationBackend("default")
    daemon._default_backend = default_backend
    first = _runtime_context(project_id, "session-6.3", pid=6203, port=2703,
                             worker=object(), backend=first_backend)
    second = _runtime_context(project_id, "session-6.4", pid=6204, port=2704,
                              worker=object(), backend=second_backend)
    daemon.session_registry.register(first)
    daemon.session_registry.register(second)
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )

    def overlapping(operation, _arguments, context):
        assert operation == "test_session_engine_action"
        assert context is not None
        barrier.wait(timeout=3)
        return {"success": True, "data": {"backend": current_session_context().backend.name}}

    first_backend.invoke_hook = overlapping
    second_backend.invoke_hook = overlapping
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "invoke-one"))
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "invoke-two"))
            result_one, result_two = one.result(timeout=5), two.result(timeout=5)
        assert result_one["success"] is True
        assert result_two["success"] is True
        assert {result_one["data"]["backend"], result_two["data"]["backend"]} == {"session-6.3", "session-6.4"}
        assert default_backend.calls == []
        assert first_backend.calls[0][1] is first
        assert second_backend.calls[0][1] is second
    finally:
        daemon.close()


def test_default_dispatch_cannot_overlap_registered_unknown_endpoint(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    default_entered = threading.Event()
    release_default = threading.Event()
    registered_entered = threading.Event()
    default_backend = _InvocationBackend("default")
    registered_backend = _InvocationBackend("registered-unknown")
    daemon._default_backend = default_backend
    context = _runtime_context(project_id, "session-unknown", pid=6301, port=2801,
                               worker=object(), backend=registered_backend)
    context = replace(
        context,
        endpoint=SessionEndpointIdentity("127.0.0.1", 2801, 1, observed_peer=None),
        server_ownership="shared",
        process_identity=None,
    )
    daemon.session_registry.register(context)
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )

    def hold_default(_operation, _arguments, _context):
        default_entered.set()
        assert release_default.wait(timeout=3)
        return {"success": True, "data": {"lane": "default"}}

    def registered_call(_operation, _arguments, _context):
        registered_entered.set()
        return {"success": True, "data": {"lane": "registered"}}

    default_backend.invoke_hook = hold_default
    registered_backend.invoke_hook = registered_call
    try:
        default_request = {
            "operation": "test_session_engine_action", "arguments": {},
            "execution": {"project_id": project_id, "request_id": "default-gated", "idempotency_key": "default-gated"},
        }
        with ThreadPoolExecutor(max_workers=2) as callers:
            first = callers.submit(daemon.dispatch, default_request)
            assert default_entered.wait(timeout=2)
            second = callers.submit(daemon.dispatch, _request(project_id, context.session_id, "registered-gated"))
            assert not registered_entered.wait(timeout=0.2)
            release_default.set()
            assert first.result(timeout=3)["success"] is True
            assert second.result(timeout=3)["success"] is True
        assert registered_entered.is_set()
        assert default_backend.calls[0][1] is None
        assert registered_backend.calls[0][1] is context
    finally:
        release_default.set()
        daemon.close()


def test_job_reconcile_uses_durable_registered_worker_epoch(tmp_path):
    class Worker:
        def __init__(self, name):
            self.name = name
            self.calls = []

        def status(self, identifier):
            self.calls.append(identifier)
            return {"request_id": identifier, "status": "SUCCEEDED"}

    daemon, project_id = _engine_daemon(tmp_path)
    default_worker = Worker("default")
    target_worker = Worker("target")
    daemon._default_backend = _InvocationBackend("default", worker=default_worker)
    target_backend = _InvocationBackend("target", worker=target_worker)
    context = _runtime_context(project_id, "session-target", pid=6401, port=2901,
                               worker=target_worker, backend=target_backend)
    daemon.session_registry.register(context)
    try:
        binding = {
            "kind": "registered_session", "project_id": project_id,
            "session_id": context.session_id, "worker_epoch": context.worker_epoch,
            "worker_instance_id": context.worker_instance_id,
        }
        record, _ = daemon.store.begin(
            request_id="bound-job-request", idempotency_key="bound-job-key",
            request_hash="bound-job-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": binding},
        )
        job_id = record["job_id"]
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "bound-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(job_id, "UNKNOWN")

        result = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": job_id}, "execution": {},
        })
        assert result["success"] is True
        assert result["data"]["metadata"]["reconciled_quiescent"] is True
        assert target_worker.calls == ["bound-worker-call"]
        assert default_worker.calls == []

        mismatch_binding = {**binding, "worker_instance_id": "different-instance-same-epoch"}
        mismatch, _ = daemon.store.begin(
            request_id="mismatch-request", idempotency_key="mismatch-key",
            request_hash="mismatch-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": mismatch_binding},
        )
        daemon.store.add_event(mismatch["job_id"], "worker_request", {
            "request_id": "mismatch-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(mismatch["job_id"], "UNKNOWN")
        mismatch_result = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": mismatch["job_id"]}, "execution": {},
        })
        assert mismatch_result["success"] is False
        assert mismatch_result["error"]["code"] == "WORKER_BINDING_MISMATCH"
        assert target_worker.calls == ["bound-worker-call"]
    finally:
        daemon.close()


def test_unbound_job_worker_access_fails_closed_and_force_stop_uses_bound_backend(tmp_path, monkeypatch):
    class Worker:
        def __init__(self):
            self.calls = []

        def status(self, identifier):
            self.calls.append(identifier)
            return {"request_id": identifier, "status": "RUNNING"}

    daemon, project_id = _engine_daemon(tmp_path)
    default_worker = Worker()
    default_service = SimpleNamespace(is_shared=False, server_pid=6500, ledger=SimpleNamespace(session_id="default-session"))
    daemon._default_backend = _InvocationBackend("default", worker=default_worker, service=default_service)
    target_worker = Worker()
    shared_service = SimpleNamespace(is_shared=True, server_pid=6501, ledger=SimpleNamespace(session_id="target-session"))
    target_backend = _InvocationBackend("target", worker=target_worker, service=shared_service)
    context = _runtime_context(project_id, "target-session", pid=6501, port=3001,
                               worker=target_worker, backend=target_backend)
    daemon.session_registry.register(context)
    termination_calls = []
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: termination_calls.append(pid) or True)
    try:
        unbound, _ = daemon.store.begin(
            request_id="unbound-request", idempotency_key="unbound-key",
            request_hash="unbound-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {}, "execution": {}},
        )
        daemon.store.add_event(unbound["job_id"], "worker_request", {
            "request_id": "unbound-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(unbound["job_id"], "UNKNOWN")
        refused = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": unbound["job_id"]}, "execution": {},
        })
        assert refused["success"] is False
        assert refused["error"]["code"] == "WORKER_BINDING_UNKNOWN"
        assert default_worker.calls == [] and target_worker.calls == []

        binding = {
            "kind": "registered_session", "project_id": project_id,
            "session_id": context.session_id, "worker_epoch": context.worker_epoch,
            "worker_instance_id": context.worker_instance_id,
        }
        active, _ = daemon.store.begin(
            request_id="force-stop-request", idempotency_key="force-stop-key",
            request_hash="force-stop-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": binding},
        )
        daemon.store.update_job(active["job_id"], "RUNNING")
        force_result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": active["job_id"], "force_stop": True,
                          "server_scope": {"authorized": True, "pid": 6500}},
            "execution": {},
        })
        assert force_result["success"] is False
        assert force_result["error"]["code"] == "CANNOT_TERMINATE_SHARED_SERVER"
        assert termination_calls == []
    finally:
        daemon.close()


def test_control_reconcile_and_force_stop_do_not_wait_behind_busy_backend_invoke(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class Worker:
        def __init__(self):
            self.status_calls = []

        def status(self, identifier):
            self.status_calls.append(identifier)
            return {"request_id": identifier, "status": "SUCCEEDED"}

    worker = Worker()
    service = SimpleNamespace(
        is_shared=False,
        server_pid=6601,
        ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"}, session_id="session-control"),
    )
    backend = _InvocationBackend("session-control", worker=worker, service=service)
    context = _runtime_context(project_id, "session-control", pid=6601, port=3101,
                               worker=worker, backend=backend)
    daemon.session_registry.register(context)
    daemon._default_backend = _InvocationBackend("default")
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity",
                        lambda _pid: {"alive": True, "start_epoch_ms": 123456})
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda _pid, timeout_s=5.0: True)
    backend.invoke_hook = lambda *_args: (
        entered.set(), release.wait(timeout=4), {"success": True, "data": {}}
    )[-1]

    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            engine_call = caller.submit(daemon.dispatch, _request(project_id, context.session_id, "busy-control"))
            assert entered.wait(timeout=2)
            running_job = None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with daemon.store.lock:
                    row = daemon.store.db.execute(
                        "SELECT j.job_id FROM jobs j JOIN operations o USING(operation_id) WHERE o.idempotency_key=?",
                        ("busy-control",),
                    ).fetchone()
                if row:
                    running_job = daemon.store.job(row["job_id"])
                    break
                time.sleep(0.01)
            assert running_job is not None
            job_id = running_job["job_id"]

            binding = running_job["metadata"]["runtime_binding"]
            control_job, _ = daemon.store.begin(
                request_id="parallel-status-request", idempotency_key="parallel-status-key",
                request_hash="parallel-status-hash", operation="study.run",
                metadata={"operation": "study.run", "arguments": {},
                          "execution": {"project_id": project_id, "session_id": context.session_id},
                          "runtime_binding": binding},
            )
            daemon.store.add_event(control_job["job_id"], "worker_request", {
                "request_id": "parallel-status-call", "phase": "submitted",
            })
            daemon.store.update_job(control_job["job_id"], "UNKNOWN")
            started = time.monotonic()
            reconciled = daemon.dispatch({
                "operation": "job.reconcile", "arguments": {"job_id": control_job["job_id"]}, "execution": {},
            })
            assert time.monotonic() - started < 0.5
            assert reconciled["success"] is True
            assert worker.status_calls == ["parallel-status-call"]

            started = time.monotonic()
            stopped = daemon.dispatch({
                "operation": "job.cancel",
                "arguments": {"job_id": job_id, "force_stop": True,
                              "server_scope": {"authorized": True, "pid": 6601,
                                               "process_start_epoch_ms": 123456}},
                "execution": {},
            })
            assert time.monotonic() - started < 0.5
            assert stopped["success"] is True
            assert stopped["data"]["engine_stopped"] is True
            assert entered.is_set() and not release.is_set()
            assert daemon.store.job(job_id)["status"] == "CANCELLED"
            after_fence = daemon.dispatch(_request(project_id, context.session_id, "after-force-stop"))
            assert after_fence["success"] is False
            assert after_fence["error"]["code"] == "WORKER_RETIRED_OR_UNKNOWN"
            assert len(backend.calls) == 1
            release.set()
            # The request was already accepted as a pending RPC; its durable
            # terminal cancellation must survive the late fake callback.
            engine_call.result(timeout=3)
            assert daemon.store.job(job_id)["status"] == "CANCELLED"
    finally:
        release.set()
        daemon.close()
