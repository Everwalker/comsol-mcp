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

    @contextmanager
    def operation_context(self, _operation_id, *, on_request_event=None):
        self.event_callback = on_request_event
        yield

    def client(self):
        return self

    def connect(self, port, host, **kwargs):
        self.connect_calls += 1
        self.connect_credentials.append({
            "user": kwargs.get("user", ""), "password": kwargs.get("password", ""),
        })
        if self.event_callback:
            self.event_callback({"phase": "submitted", "request_id": kwargs.get("request_id"), "metadata": {
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


def _connect_daemon(tmp_path, worker, *, peer=CanonicalSocket("127.0.0.1", 2046), credentials_resolver=None):
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


def test_session_disconnect_unknown_retains_handle_and_recover_only_queries_original_request(tmp_path):
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
        assert len(worker.status_calls) == 1
        assert worker.status_calls[0].endswith(":disconnect")

        retry = daemon.dispatch(_session_mutation_request(
            "session.reconnect", project_id, session_id, "reconnect-after-unknown",
        ))
        assert retry["success"] is False
        assert retry["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert worker.connect_calls == 1
        assert worker.start_calls == 1
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
