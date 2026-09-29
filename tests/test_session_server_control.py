from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._platform_process import process_identity
from comsol_mcp._session_context import (
    CanonicalSocket, OwnedServerProcessIdentity, SessionEndpointIdentity, SessionRuntimeConfig,
    SessionRuntimeContext, session_state_directory,
)
from comsol_mcp import _session_server
from comsol_mcp._session_server import (
    OwnedServerError, OwnedServerLauncher, create_server_directories,
    listener_rows, owned_server_preferences_directory,
)


def _sleeping_child():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class _AttachedWorker:
    def __init__(self, process):
        self._process = process
        self.start_calls = 0
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.close_calls = 0
        self._meta = {"pid": process.pid, "instance_id": "owned-server-test-worker",
                      "generation": 1, "connected": False, "server": ""}
        self.event_callback = None

    def start(self):
        self.start_calls += 1
        return {"status": "HEALTHY", **self._meta}

    def runtime_metadata(self):
        return dict(self._meta)

    @contextmanager
    def operation_context(self, _operation_id, *, on_request_event=None):
        self.event_callback = on_request_event
        yield

    def client(self):
        return self

    def connect(self, port, host, **kwargs):
        self.connect_calls += 1
        self._meta.update(generation=self._meta["generation"] + 1,
                          connected=True, server=f"{host}:{port}")
        return {"connected": True, "server": f"{host}:{port}",
                "generation": self._meta["generation"],
                "instance_id": self._meta["instance_id"],
                "engine_version": "6.4.0.293"}

    def disconnect(self, **_kwargs):
        self.disconnect_calls += 1
        self._meta.update(generation=self._meta["generation"] + 1,
                          connected=False, server="")
        return {"connected": False, "generation": self._meta["generation"],
                "instance_id": self._meta["instance_id"]}

    def close(self):
        self.close_calls += 1
        if self._process.poll() is None:
            self._process.terminate()
        self._process.wait(timeout=3)

    def status(self, request_id, **_kwargs):
        return {"ok": True, "request_id": request_id, "status": "SUCCEEDED"}


class _LoopbackComsolCommand:
    """Test-only launcher: substitutes a harmless Python loopback child for COMSOL."""

    def __init__(self):
        self.commands = []
        self.processes = []

    def __call__(self, command, **options):
        self.commands.append(list(command))
        port_file = command[command.index("-portfile") + 1]
        child = textwrap.dedent(
            """
            import http.server, pathlib, sys
            class Quiet(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
                def log_message(self, *args): pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
            pathlib.Path(sys.argv[1]).write_text(str(server.server_port), encoding='ascii')
            server.serve_forever()
            """
        )
        process = subprocess.Popen(
            [sys.executable, "-c", child, port_file], **options,
        )
        self.processes.append(process)
        return process


def _make_daemon(tmp_path, monkeypatch, *, server_launcher=None, worker=None,
                 worker_factory=None, host_control=True):
    if host_control:
        monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    else:
        monkeypatch.delenv("COMSOL_MCP_HOST_CONTROL", raising=False)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True)
    installation = tmp_path / "synthetic-installation"
    (installation / "bin/servers/webbridge/conf").mkdir(parents=True)
    (installation / "bin/servers/webbridge/conf/server.xml").write_text(
        '<Server><Service><Connector port="2036" address="0.0.0.0" /></Service></Server>',
        encoding="utf-8",
    )
    (installation / "bin/comsol").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (installation / "bin/comsol").chmod(0o755)
    source_xml = installation / "bin/servers/webbridge/conf/server.xml"
    source_xml_hash = hashlib.sha256(source_xml.read_bytes()).hexdigest()
    server_command = _LoopbackComsolCommand()
    launcher = server_launcher or OwnedServerLauncher(
        process_factory=server_command,
        process_identity_reader=process_identity,
        listener_reader=lambda port: listener_rows(port, platform_name=sys.platform),
        platform_name=sys.platform,
        ready_timeout_s=5,
        poll_interval_s=0.02,
    )
    session_state_root = tmp_path / "runtime-state"

    def runtime_resolver(runtime_id, project_id, session_id, _project_root, state_root):
        assert runtime_id == "fixture-runtime"
        session_root = session_state_directory(state_root, project_id, session_id)
        return SessionRuntimeConfig(
            runtime_id=runtime_id, comsol_version="6.4.0.293",
            installation_root=installation,
            java_executable=tmp_path / "jdk/bin/java",
            classpath=(installation / "client.jar",),
            preferences_dir=session_root / "preferences",
            session_state_root=state_root,
        )

    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, registry={},
        session_runtime_resolver=runtime_resolver,
        session_worker_factory=(worker_factory if worker_factory is not None else
                                (lambda _runtime, _state: worker) if worker is not None else None),
        session_peer_observer=lambda _worker, port, _metadata: CanonicalSocket("127.0.0.1", port),
        session_server_launcher=launcher,
    )
    permissions = ["inspect", "project_write", "compute"]
    if host_control:
        permissions.append("host_control")
    project_response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "owned-server",
            "workspace": "owned-server",
            "policy": {"permissions": permissions},
        },
        "execution": {"request_id": "create-owned-server", "idempotency_key": "create-owned-server"},
    })
    assert project_response["success"] is True, project_response
    return daemon, project_response["data"]["project"]["project_id"], server_command, source_xml, source_xml_hash


def _owned_preferences_fixture(tmp_path, *, project_id="project-a", session_id="session-a"):
    state_root = tmp_path / "runtime-state"
    installation = tmp_path / "source-installation"
    installation.mkdir(parents=True)
    session_root = session_state_directory(state_root, project_id, session_id)
    runtime = SessionRuntimeConfig(
        runtime_id="fixture-runtime", comsol_version="6.4.0.293",
        installation_root=installation,
        java_executable=tmp_path / "jdk/bin/java",
        classpath=(installation / "client.jar",),
        preferences_dir=session_root / "preferences", session_state_root=state_root,
    )
    directories = create_server_directories(runtime, project_id, session_id)
    launcher = directories.private_installation / "bin" / "comsol"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("test-only launcher identity\n", encoding="utf-8")
    identity = OwnedServerProcessIdentity(
        pid=1234, birth="start_epoch_ms:1234", executable=str(launcher.resolve()),
        listener_sockets=(CanonicalSocket("127.0.0.1", 2036),), start_epoch_ms=1234,
    )
    return runtime, directories, identity


def _windows_owned_preferences_fixture(tmp_path):
    runtime, directories, _identity = _owned_preferences_fixture(tmp_path)
    private_bin = directories.private_installation / "bin"
    shutil.rmtree(private_bin)
    runtime_executable = runtime.installation_root / "bin" / "win64" / "comsolmphserver.exe"
    runtime_executable.parent.mkdir(parents=True)
    runtime_executable.write_text("test-only runtime launcher identity\n", encoding="utf-8")
    private_win64 = private_bin / "win64"
    private_win64.parent.mkdir(parents=True)
    private_win64.symlink_to(runtime_executable.parent, target_is_directory=True)
    identity = OwnedServerProcessIdentity(
        pid=1234, birth="start_epoch_ms:1234", executable=str(runtime_executable.resolve()),
        listener_sockets=(CanonicalSocket("127.0.0.1", 2036),), start_epoch_ms=1234,
    )
    return runtime, directories, identity


def test_owned_server_preferences_are_existing_and_session_bound(tmp_path):
    runtime, directories, identity = _owned_preferences_fixture(tmp_path)

    actual = owned_server_preferences_directory(
        runtime, "project-a", "session-a", identity, platform_name="darwin",
    )

    assert actual == directories.preferences.resolve()
    assert actual != runtime.preferences_dir
    assert actual.is_dir()


@pytest.mark.parametrize("bad_path", ["missing", "symlink"], ids=["missing", "alias"])
def test_owned_server_preferences_reject_missing_or_symlinked_path(tmp_path, bad_path):
    runtime, directories, identity = _owned_preferences_fixture(tmp_path)
    if bad_path == "missing":
        directories.preferences.rmdir()
    else:
        directories.preferences.rmdir()
        outside = tmp_path / "outside-preferences"
        outside.mkdir()
        directories.preferences.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OwnedServerError):
        owned_server_preferences_directory(
            runtime, "project-a", "session-a", identity, platform_name="darwin",
        )
    if bad_path == "missing":
        assert not directories.preferences.exists()


def test_owned_server_preferences_reject_process_from_another_session(tmp_path):
    runtime_a, _directories_a, identity_a = _owned_preferences_fixture(
        tmp_path / "a", project_id="project-a", session_id="session-a",
    )
    runtime_b, directories_b, _identity_b = _owned_preferences_fixture(
        tmp_path / "b", project_id="project-b", session_id="session-b",
    )

    with pytest.raises(OwnedServerError, match="different session"):
        owned_server_preferences_directory(
            runtime_b, "project-b", "session-b", identity_a, platform_name="darwin",
        )
    assert directories_b.preferences.is_dir()


def test_owned_server_preferences_accept_windows_runtime_bin_symlink(tmp_path):
    runtime, directories, identity = _windows_owned_preferences_fixture(tmp_path)

    actual = owned_server_preferences_directory(
        runtime, "project-a", "session-a", identity, platform_name="windows",
    )

    assert (directories.private_installation / "bin" / "win64").is_symlink()
    assert actual == directories.preferences.resolve()


def test_owned_server_preferences_reject_windows_runtime_mismatch(tmp_path):
    from dataclasses import replace

    runtime, _directories, identity = _windows_owned_preferences_fixture(tmp_path)
    other_installation = tmp_path / "other-installation"
    other_executable = other_installation / "bin" / "win64" / "comsolmphserver.exe"
    other_executable.parent.mkdir(parents=True)
    other_executable.write_text("test-only unrelated runtime identity\n", encoding="utf-8")
    mismatched_runtime = replace(runtime, installation_root=other_installation)

    with pytest.raises(OwnedServerError, match="bound runtime installation"):
        owned_server_preferences_directory(
            mismatched_runtime, "project-a", "session-a", identity,
            platform_name="windows",
        )


def test_mcp_managed_worker_rejects_backend_outside_exact_session(tmp_path):
    from comsol_mcp._managed_backend import ManagedBackend, SessionConnectFailure
    from comsol_mcp._operation_store import OperationStore

    runtime, _directories, identity = _owned_preferences_fixture(tmp_path)
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker_factory_calls = []
    backend = ManagedBackend(
        tmp_path / "different-session" / "backend", store,
        session_worker_factory=lambda *args: worker_factory_calls.append(args),
    )
    try:
        with pytest.raises(SessionConnectFailure) as raised:
            backend.connect_session(
                runtime=runtime, project_id="project-a", session_id="session-a",
                host="127.0.0.1", port=2036, operation_id="op", request_id="req",
                event_callback=lambda _event: None, server_ownership="mcp_managed",
                owned_process=identity,
            )
        assert raised.value.code == "RUNTIME_CONFIGURATION_REQUIRED"
        assert worker_factory_calls == []
    finally:
        store.close()


def _start(daemon, project_id, key="server-start"):
    return daemon.dispatch({
        "operation": "session.start",
        "arguments": {"project_id": project_id, "idempotency_key": key,
                       "runtime_id": "fixture-runtime"},
        "execution": {},
    })


def _stop(daemon, project_id, session_id, key="server-stop"):
    return daemon.dispatch({
        "operation": "session.stop",
        "arguments": {"project_id": project_id, "session_id": session_id,
                       "idempotency_key": key, "authorization_ref": "local-test-operator"},
        "execution": {},
    })


def test_windows_listener_inventory_accepts_empty_array_and_filters_client_side(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(_session_server, "subprocess", SimpleNamespace(run=fake_run))

    assert listener_rows(11961, platform_name="win32") == []

    script = captured["command"][-1]
    assert "Get-NetTCPConnection -ErrorAction Stop" in script
    assert "Get-NetTCPConnection -State" not in script
    assert "Get-NetTCPConnection -LocalPort" not in script
    assert "Where-Object" in script
    assert "$_.State -eq 'Listen'" in script
    assert "$_.LocalPort -eq 11961" in script
    assert "SilentlyContinue" not in script
    assert "ErrorAction Ignore" not in script
    assert captured["kwargs"]["check"] is False


@pytest.mark.parametrize(("returncode", "stdout"), [
    (1, "[]"),
    (0, "not-json"),
    (0, "{}"),
    (0, '[{"LocalAddress":"127.0.0.1","LocalPort":11961}]'),
])
def test_windows_listener_inventory_rejects_provider_and_payload_errors(
        monkeypatch, returncode, stdout):
    def fake_run(command, **_kwargs):
        stderr = "CmdletizationQuery_NotFound" if returncode else ""
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(_session_server, "subprocess", SimpleNamespace(run=fake_run))

    with pytest.raises(OwnedServerError):
        listener_rows(11961, platform_name="win32")


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_public_owned_server_start_attach_disconnect_retire_stop(tmp_path, monkeypatch):
    worker_process = _sleeping_child()
    worker = _AttachedWorker(worker_process)
    daemon = None
    server_process = None
    try:
        identity = process_identity(worker_process.pid)
        if identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int:
            worker_process.terminate(); worker_process.wait(timeout=3)
            pytest.skip("host cannot provide exact process birth identity")
        worker_runtimes = []

        def worker_factory(runtime, _worker_state):
            worker_runtimes.append(runtime)
            return worker

        daemon, project_id, server_command, installed_xml, installed_hash = _make_daemon(
            tmp_path, monkeypatch, worker_factory=worker_factory,
        )

        started = _start(daemon, project_id)
        assert started["success"] is True, started
        data = started["data"]
        session_id = data["session_id"]
        endpoint = data["endpoint"]
        assert data["server_ownership"] == "mcp_managed"
        assert data["loopback_only_verified"] is True
        assert endpoint["host"] == "127.0.0.1"
        server_process = server_command.processes[0]
        server_birth = int(data["server_process_identity"]["birth"].split(":", 1)[1])
        assert process_identity(server_process.pid)["start_epoch_ms"] == server_birth
        assert installed_xml.read_bytes() == (
            b'<Server><Service><Connector port="2036" address="0.0.0.0" /></Service></Server>'
        )
        assert hashlib.sha256(installed_xml.read_bytes()).hexdigest() == installed_hash

        implicit_attach = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {"project_id": project_id, "idempotency_key": "implicit-owned-attach",
                          "runtime_id": "fixture-runtime", "endpoint": endpoint},
            "execution": {},
        })
        assert implicit_attach["success"] is False
        assert implicit_attach["error"]["code"] == "SESSION_ID_REQUIRED"
        assert worker.start_calls == 0 and worker.connect_calls == 0
        assert server_process.poll() is None

        connected = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {"project_id": project_id, "idempotency_key": "explicit-owned-attach",
                          "runtime_id": "fixture-runtime", "session_id": session_id,
                          "endpoint": endpoint},
            "execution": {},
        })
        assert connected["success"] is True, connected
        assert connected["data"]["server_ownership"] == "mcp_managed"
        assert len(worker_runtimes) == 1
        server_command_args = server_command.commands[0]
        server_preferences = Path(
            server_command_args[server_command_args.index("-prefsdir") + 1]
        ).resolve()
        original_runtime = daemon._session_runtime_configs[(project_id, session_id)]
        assert worker_runtimes[0] is not original_runtime
        assert worker_runtimes[0].preferences_dir == server_preferences
        assert server_preferences != original_runtime.preferences_dir
        lifecycle = daemon.session_lifecycle.get(project_id, session_id)
        assert lifecycle["state"] == "CONNECTED"
        assert lifecycle["server_ownership"] == "mcp_managed"
        context = daemon.session_registry.get(project_id, session_id)
        assert context.server_started_by_mcp is True
        assert context.endpoint.owned_process.pid == server_process.pid
        assert context.endpoint.owned_process.start_epoch_ms == server_birth

        connected_stop = _stop(daemon, project_id, session_id, key="stop-before-disconnect")
        assert connected_stop["success"] is False
        assert connected_stop["error"]["code"] == "SESSION_CLIENT_NOT_RETIRED"
        assert server_process.poll() is None

        disconnected = daemon.dispatch({
            "operation": "session.disconnect",
            "arguments": {"project_id": project_id, "session_id": session_id,
                          "idempotency_key": "retire-attached-worker", "retire_worker": True},
            "execution": {},
        })
        assert disconnected["success"] is True, disconnected
        assert disconnected["data"]["worker_retirement"]["child_reaped"] is True
        assert worker_process.poll() is not None
        assert worker.close_calls == 1

        stopped = _stop(daemon, project_id, session_id)
        assert stopped["success"] is True, stopped
        assert stopped["data"]["state"] == "STOPPED"
        assert stopped["data"]["stop_evidence"]["child_reaped"] is True
        assert stopped["data"]["stop_evidence"]["listener_absent"] is True
        assert server_process.poll() is not None
        assert daemon._session_server_handles == {}
        assert hashlib.sha256(installed_xml.read_bytes()).hexdigest() == installed_hash
    finally:
        if daemon is not None:
            daemon.close()
        if server_process is not None and server_process.poll() is None:
            server_process.terminate(); server_process.wait(timeout=3)
        if worker_process.poll() is None:
            worker_process.terminate(); worker_process.wait(timeout=3)


def test_session_start_requires_host_control_before_any_child_birth(tmp_path, monkeypatch):
    daemon = None
    try:
        daemon, project_id, server_command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, host_control=False,
        )
        result = _start(daemon, project_id)
        assert result["success"] is False
        assert result["error"]["code"] == "PERMISSION_DENIED"
        assert server_command.processes == []
    finally:
        if daemon is not None:
            daemon.close()


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_owned_server_start_idempotency_reuses_original_process(tmp_path, monkeypatch):
    daemon = None
    try:
        daemon, project_id, server_command, _xml, _hash = _make_daemon(tmp_path, monkeypatch)
        first = _start(daemon, project_id, key="same-owned-server-start")
        assert first["success"] is True, first
        second = _start(daemon, project_id, key="same-owned-server-start")
        assert second == first
        assert len(server_command.processes) == 1
        assert server_command.processes[0].poll() is None
    finally:
        if daemon is not None:
            daemon.close()
        if daemon is not None:
            for process in daemon.session_server_launcher.process_factory.processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)


def _startup_observation(event, project_id, session_id, runtime_id, **changes):
    return {
        "schema": "COMSOL_OWNED_SERVER_STARTUP_DIAGNOSTIC_V1",
        "event": event,
        "observed_at_utc": "2026-09-29T00:00:00.000Z",
        "project_id": project_id,
        "session_id": session_id,
        "runtime_id": runtime_id,
        "pid": None,
        "birth": None,
        "exit_code": None,
        "exception_type": None,
        "errno": None,
        "winerror": None,
        "error_category": None,
        **changes,
    }


class _StartupObservationFailureLauncher:
    def __init__(self, *, born):
        self.born = born

    def start(self, runtime, project_id, session_id, *, observation_sink=None):
        if self.born:
            handle = SimpleNamespace(
                process=SimpleNamespace(pid=77123), runtime_id=runtime.runtime_id,
                endpoint=None, process_identity=None,
            )
            payload = _startup_observation(
                "PROCESS_CREATED", project_id, session_id, runtime.runtime_id, pid=77123,
            )
        else:
            handle = None
            payload = _startup_observation(
                "PROCESS_CREATE_FAILED", project_id, session_id, runtime.runtime_id,
                exception_type="OSError", errno=2, error_category="PROCESS_CREATE_FAILED",
            )
        try:
            observation_sink(payload)
        except Exception:
            if self.born:
                raise OwnedServerError(
                    "startup observation failed after process creation", handle=handle, uncertain=True,
                ) from None
            raise
        if self.born:
            raise OwnedServerError("injected incomplete startup proof", handle=handle, uncertain=True)
        raise OwnedServerError("injected process creation failure")


def _startup_observation_events(daemon, job_id):
    return [event for event in daemon.store.events(job_id)
            if event["event"] == "SessionServerStartupObservation"]


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="session launcher adapters are supported on Darwin and Windows")
def test_session_start_persists_safe_startup_observation_with_original_operation_identity(
        tmp_path, monkeypatch):
    daemon = None
    try:
        daemon, project_id, _command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, server_launcher=_StartupObservationFailureLauncher(born=True),
        )
        result = _start(daemon, project_id, key="startup-observation-unknown")

        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        execution = result["execution"]
        job = daemon.store.job(execution["job_id"])
        assert job["status"] == "UNKNOWN"
        assert job["operation"]["status"] == "UNKNOWN"
        assert job["operation"]["request_id"] == execution["request_id"]
        assert job["operation"]["idempotency_key"] == "startup-observation-unknown"
        assert job["operation_id"] == execution["operation_id"]

        events = _startup_observation_events(daemon, execution["job_id"])
        assert len(events) == 1
        metadata = events[0]["metadata"]
        assert metadata["event"] == "PROCESS_CREATED"
        assert metadata["pid"] == 77123 and metadata["birth"] is None
        assert metadata["project_id"] == project_id
        assert metadata["session_id"] == result["data"]["session_id"]
        assert metadata["runtime_id"] == "fixture-runtime"
        assert metadata["request_id"] == execution["request_id"]
        assert metadata["idempotency_key"] == "startup-observation-unknown"
        assert metadata["request_hash"] == execution["request_hash"]
        assert metadata["operation_id"] == execution["operation_id"]
        assert metadata["job_id"] == execution["job_id"]
        serialized = json.dumps(metadata, sort_keys=True).casefold()
        assert "token" not in serialized and "commandline" not in serialized
        assert "executable" not in serialized and "endpoint" not in serialized
        assert daemon.session_lifecycle.get(project_id, result["data"]["session_id"])["state"] == "UNKNOWN"
    finally:
        if daemon is not None:
            daemon.close()


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="session launcher adapters are supported on Darwin and Windows")
def test_session_start_process_create_failure_records_failed_without_raw_exception(
        tmp_path, monkeypatch):
    daemon = None
    try:
        daemon, project_id, _command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, server_launcher=_StartupObservationFailureLauncher(born=False),
        )
        result = _start(daemon, project_id, key="startup-prebirth-failure")

        assert result["success"] is False
        assert result["error"]["code"] == "SERVER_START_FAILED"
        assert result["data"]["server_birth_performed"] is False
        execution = result["execution"]
        job = daemon.store.job(execution["job_id"])
        assert job["status"] == job["operation"]["status"] == "FAILED"
        events = _startup_observation_events(daemon, execution["job_id"])
        assert len(events) == 1
        metadata = events[0]["metadata"]
        assert metadata["event"] == "PROCESS_CREATE_FAILED"
        assert metadata["pid"] is None and metadata["exit_code"] is None
        assert metadata["exception_type"] == "OSError" and metadata["errno"] == 2
        assert "message" not in metadata and "traceback" not in metadata
        assert daemon.session_lifecycle.get(project_id, result["data"]["session_id"])["state"] == "STOPPED"
    finally:
        if daemon is not None:
            daemon.close()


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="session launcher adapters are supported on Darwin and Windows")
def test_session_start_observation_store_failure_remains_unknown_and_finish_readback_is_authoritative(
        tmp_path, monkeypatch):
    daemon = None
    try:
        daemon, project_id, _command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, server_launcher=_StartupObservationFailureLauncher(born=True),
        )
        add_event = daemon.store.add_event

        def fail_startup_event(job_id, event, metadata=None):
            if event == "SessionServerStartupObservation":
                raise OSError("synthetic private store detail")
            return add_event(job_id, event, metadata)

        monkeypatch.setattr(daemon.store, "add_event", fail_startup_event)
        update_job = daemon.store.update_job

        def fail_after_unknown_finish(job_id, status, metadata=None, *, result=None):
            if status == "UNKNOWN":
                raise OSError("synthetic post-finish update failure")
            return update_job(job_id, status, metadata, result=result)

        monkeypatch.setattr(daemon.store, "update_job", fail_after_unknown_finish)
        result = _start(daemon, project_id, key="startup-observation-store-failure")

        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert result["data"]["startup_observation_persist_error_type"] == "OSError"
        execution = result["execution"]
        job = daemon.store.job(execution["job_id"])
        assert job["status"] == job["operation"]["status"] == "UNKNOWN"
        assert job["result"] == result
        assert _startup_observation_events(daemon, execution["job_id"]) == []
    finally:
        if daemon is not None:
            daemon.close()


def test_control_daemon_sidecar_distinguishes_serve_loop_from_process_exit(tmp_path):
    from comsol_mcp._control_daemon import _write_control_daemon_identity

    base = {
        "schema": "COMSOL_CONTROL_DAEMON_IDENTITY_V1",
        "observed_at_utc": "2026-09-29T03:30:00.000Z",
        "pid": 39003,
        "birth": "start_epoch_ms:1790641470227",
        "exit_code": None,
        "exception_type": None,
        "errno": None,
        "winerror": None,
        "error_category": None,
    }
    ready = {**base, "event": "DAEMON_READY"}
    assert _write_control_daemon_identity(tmp_path, ready) == ready

    returned = {
        **base, "event": "DAEMON_SERVE_RETURNED",
        "error_category": "DAEMON_SERVE_LOOP_RETURNED",
    }
    assert _write_control_daemon_identity(tmp_path, returned) == returned
    persisted = json.loads((tmp_path / "control-daemon-identity.json").read_text(encoding="utf-8"))
    assert persisted == returned
    assert persisted["exit_code"] is None

    failed = {
        **base, "event": "DAEMON_SERVE_FAILED",
        "exception_type": "RuntimeError",
        "error_category": "DAEMON_SERVE_LOOP_RAISED",
    }
    assert _write_control_daemon_identity(tmp_path, failed) == failed

    for invalid in (
        {**base, "event": "DAEMON_EXIT_OBSERVED", "exit_code": 0,
         "error_category": "DAEMON_EXITED_NORMALLY"},
        {**returned, "exit_code": 0},
        {**returned, "token": "synthetic-secret"},
    ):
        with pytest.raises(ValueError):
            _write_control_daemon_identity(tmp_path, invalid)


def test_control_daemon_serve_terminal_observations_never_claim_process_exit(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    class FakeLock:
        def __init__(self, _path):
            pass

        def close(self):
            pass

    class FakeDaemon:
        def __init__(self, _home):
            self._control_singleton_lock_held = False

    class FakeServer:
        server_port = 24888

        def __init__(self, *, raised=None):
            self.raised = raised
            self.daemon_threads = False

        def serve_forever(self):
            if self.raised is not None:
                raise self.raised

    returned_server = FakeServer()
    failed_server = FakeServer(raised=RuntimeError("synthetic serve loop failure"))
    servers = iter((returned_server, failed_server))
    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: next(servers))
    monkeypatch.setattr(
        daemon_module, "process_identity",
        lambda _pid: {"alive": True, "start_epoch_ms": 1790641470228},
    )

    path_replace = Path.replace

    def discard_temporary_control_endpoint(path, target):
        target_path = Path(target)
        if path.name == "control.json.tmp" and target_path.name == "control.json":
            path.unlink()
            return target_path
        return path_replace(path, target)

    monkeypatch.setattr(Path, "replace", discard_temporary_control_endpoint)

    returned_home = tmp_path / "returned"
    daemon_module.serve(returned_home)
    returned = json.loads((returned_home / "control-daemon-identity.json").read_text(encoding="utf-8"))
    assert returned["event"] == "DAEMON_SERVE_RETURNED"
    assert returned["error_category"] == "DAEMON_SERVE_LOOP_RETURNED"
    assert returned["exit_code"] is None
    assert not (returned_home / "control.json").exists()

    failed_home = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="synthetic serve loop failure"):
        daemon_module.serve(failed_home)
    failed = json.loads((failed_home / "control-daemon-identity.json").read_text(encoding="utf-8"))
    assert failed["event"] == "DAEMON_SERVE_FAILED"
    assert failed["error_category"] == "DAEMON_SERVE_LOOP_RAISED"
    assert failed["exception_type"] == "RuntimeError"
    assert failed["exit_code"] is None
    assert "synthetic serve loop failure" not in json.dumps(failed)
    assert not (failed_home / "control.json").exists()


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_owned_server_stop_unknown_retains_handle_and_close_never_blind_kills(tmp_path, monkeypatch):
    daemon = None
    process = None
    try:
        daemon, project_id, server_command, _xml, _hash = _make_daemon(tmp_path, monkeypatch)
        started = _start(daemon, project_id, key="unknown-stop-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = server_command.processes[0]
        key = (project_id, session_id)
        handle = daemon._session_server_handles[key]
        calls = []

        def uncertain_stop(candidate):
            calls.append(candidate)
            raise OwnedServerError("test injected uncertain stop", handle=candidate, uncertain=True)

        monkeypatch.setattr(daemon.session_server_launcher, "stop", uncertain_stop)
        first = _stop(daemon, project_id, session_id, key="unknown-stop-operation")
        assert first["success"] is False
        assert first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_server_handles[key] is handle
        assert process.poll() is None

        repeated = _stop(daemon, project_id, session_id, key="unknown-stop-operation")
        assert repeated == first
        refused = _stop(daemon, project_id, session_id, key="unknown-stop-new-key")
        assert refused["success"] is False
        assert refused["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert len(calls) == 1

        daemon.close()
        assert process.poll() is None
        assert daemon._session_server_handles[key] is handle
        assert len(calls) == 1
    finally:
        if daemon is not None:
            daemon.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_owned_server_stop_keeps_unknown_when_windows_listener_query_fails(tmp_path, monkeypatch):
    daemon = None
    process = None
    provider_calls = []
    try:
        daemon, project_id, server_command, _xml, _hash = _make_daemon(tmp_path, monkeypatch)
        started = _start(daemon, project_id, key="provider-error-stop-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = server_command.processes[0]
        key = (project_id, session_id)
        handle = daemon._session_server_handles[key]

        def failed_provider(command, **_kwargs):
            provider_calls.append(list(command))
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="CmdletizationQuery_NotFound,CimJobException",
            )

        monkeypatch.setattr(_session_server, "subprocess", SimpleNamespace(run=failed_provider))
        monkeypatch.setattr(
            daemon.session_server_launcher, "listener_reader",
            lambda port: listener_rows(port, platform_name="win32"),
        )

        result = _stop(daemon, project_id, session_id, key="provider-error-stop")
        assert result["success"] is False
        assert result["error"]["code"] == "SERVER_OWNERSHIP_UNKNOWN"
        assert result["error"]["execution_state_unknown"] is True
        assert result["data"]["server_stopped"] is False
        assert daemon.store.job(result["execution"]["job_id"])["status"] == "UNKNOWN"
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "DISCONNECTED"
        assert daemon._session_server_handles[key] is handle
        assert process.poll() is None
        assert len(provider_calls) == 1

        daemon.close()
        assert len(provider_calls) == 1
        assert process.poll() is None
        assert daemon._session_server_handles[key] is handle
    finally:
        if daemon is not None:
            daemon.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_daemon_close_retires_attached_worker_before_owned_server(tmp_path, monkeypatch):
    worker_process = _sleeping_child()
    worker = _AttachedWorker(worker_process)
    daemon = None
    server_process = None
    session_id = None
    project_id = None
    try:
        identity = process_identity(worker_process.pid)
        if identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int:
            worker_process.terminate()
            worker_process.wait(timeout=3)
            pytest.skip("host cannot provide exact process birth identity")
        daemon, project_id, server_command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, worker=worker,
        )
        started = _start(daemon, project_id, key="close-owned-server-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        server_process = server_command.processes[0]
        connected = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {"project_id": project_id, "session_id": session_id,
                          "idempotency_key": "close-owned-server-connect",
                          "runtime_id": "fixture-runtime", "endpoint": started["data"]["endpoint"]},
            "execution": {},
        })
        assert connected["success"] is True, connected
        assert worker_process.poll() is None and server_process.poll() is None

        daemon.close()

        assert worker.disconnect_calls == 1
        assert worker.close_calls == 1
        assert worker_process.poll() is not None
        assert server_process.poll() is not None
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._session_lifecycle import SessionLifecycleStore
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            lifecycle = SessionLifecycleStore(reopened).get(project_id, session_id)
            assert lifecycle["state"] == "STOPPED"
            assert lifecycle["server_state"] == "STOPPED"
            assert lifecycle["client_state"] == "RETIRED"
        finally:
            reopened.close()
        assert daemon._session_server_handles == {}
        assert daemon._session_worker_handles == {}
    finally:
        if daemon is not None:
            daemon.close()
        for process in (server_process, worker_process):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_daemon_close_preserves_owned_server_when_shutdown_stop_is_uncertain(tmp_path, monkeypatch):
    daemon = None
    process = None
    try:
        daemon, project_id, server_command, _xml, _hash = _make_daemon(tmp_path, monkeypatch)
        started = _start(daemon, project_id, key="close-unknown-owned-server-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = server_command.processes[0]
        key = (project_id, session_id)
        handle = daemon._session_server_handles[key]

        def uncertain_stop(candidate):
            assert candidate is handle
            return {"server_stopped": True, "child_reaped": True}

        monkeypatch.setattr(daemon.session_server_launcher, "stop", uncertain_stop)
        daemon.close()

        assert process.poll() is None
        assert daemon._session_server_handles[key] is handle
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._session_lifecycle import SessionLifecycleStore
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            lifecycle = SessionLifecycleStore(reopened).get(project_id, session_id)
            assert lifecycle["state"] == "UNKNOWN"
            assert lifecycle["server_state"] == "MCP_MANAGED"
            assert lifecycle["server_ownership"] == "mcp_managed"
            assert lifecycle["server_process_identity"] is not None
        finally:
            reopened.close()
    finally:
        if daemon is not None:
            daemon.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact process/listener proof adapter is supported on Darwin and Windows")
def test_cross_project_attach_and_stop_cannot_bypass_owned_endpoint_boundary(tmp_path, monkeypatch):
    from comsol_mcp._session_lifecycle import new_lifecycle_record

    worker_births = []

    def worker_factory(_runtime, _state):
        process = _sleeping_child()
        worker = _AttachedWorker(process)
        worker_births.append(worker)
        return worker

    daemon = None
    server_process = None
    try:
        daemon, owner_project, server_command, _xml, _hash = _make_daemon(
            tmp_path, monkeypatch, worker_factory=worker_factory,
        )
        other_project_result = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "other-project", "workspace": "other-project",
                          "policy": {"permissions": ["inspect", "project_write", "compute"]}},
            "execution": {"request_id": "create-other-project", "idempotency_key": "create-other-project"},
        })
        assert other_project_result["success"] is True, other_project_result
        other_project = other_project_result["data"]["project"]["project_id"]

        started = _start(daemon, owner_project, key="cross-project-owner-start")
        assert started["success"] is True, started
        owner_session = started["data"]["session_id"]
        server_process = server_command.processes[0]
        endpoint = started["data"]["endpoint"]

        attempted = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {"project_id": other_project, "idempotency_key": "cross-project-alias-attach",
                          "runtime_id": "fixture-runtime", "endpoint": endpoint},
            "execution": {},
        })
        assert attempted["success"] is False
        assert attempted["error"]["code"] == "SERVER_ENDPOINT_OWNERSHIP_CONFLICT"
        assert attempted["data"]["engine_dispatched"] is False
        assert attempted["data"]["worker_birth_performed"] is False
        assert owner_session not in json.dumps(attempted, sort_keys=True)
        assert worker_births == []
        assert server_process.poll() is None

        # Model a pre-existing cross-project connection with an alias endpoint.
        # This is the durable/runtime state a prior daemon could have left
        # before the new global attach check was installed.
        alias_session = "legacy-cross-project-alias"
        alias_worker = object()
        owner_runtime = daemon._session_runtime_configs[(owner_project, owner_session)]
        other_project_record = daemon.project_authority.get_project(other_project)
        alias_lifecycle = new_lifecycle_record(
            project_id=other_project, session_id=alias_session, state="CONNECTED",
            runtime_id="fixture-runtime", endpoint={"host": "localhost", "port": endpoint["port"]},
            client_state="CONNECTED", server_state="SHARED", server_ownership="shared",
            worker_instance_id="legacy-worker", worker_epoch=1,
            server_instance_id="legacy-server-instance",
        )
        daemon.session_lifecycle.save(alias_lifecycle)
        alias_context = SessionRuntimeContext(
            project_id=other_project, session_id=alias_session,
            project_root=Path(other_project_record["workspace"]), runtime=owner_runtime,
            endpoint=SessionEndpointIdentity(
                "localhost", endpoint["port"], worker_epoch=1,
                observed_peer=CanonicalSocket("127.0.0.1", endpoint["port"]),
            ),
            worker=alias_worker, worker_instance_id="legacy-worker",
            server_ownership="shared", client_connected=True,
            connected_host="localhost", connected_port=endpoint["port"],
        )
        daemon.session_registry.register(alias_context)
        daemon._session_worker_handles[(other_project, alias_session)] = alias_worker

        blocked_stop = _stop(daemon, owner_project, owner_session, key="owner-stop-with-legacy-alias")
        assert blocked_stop["success"] is False
        assert blocked_stop["error"]["code"] == "SERVER_ENDPOINT_IN_USE"
        assert blocked_stop["data"]["server_stopped"] is False
        assert alias_session not in json.dumps(blocked_stop, sort_keys=True)
        assert server_process.poll() is None

        daemon.close()
        assert server_process.poll() is None
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._session_lifecycle import SessionLifecycleStore
        reopened = OperationStore(daemon.home / "operations.sqlite3")
        try:
            owner_state = SessionLifecycleStore(reopened).get(owner_project, owner_session)
            assert owner_state["state"] == "UNKNOWN"
            assert owner_state["server_ownership"] == "mcp_managed"
            assert owner_state["server_process_identity"] is not None
        finally:
            reopened.close()
    finally:
        if daemon is not None:
            daemon.close()
        for worker in worker_births:
            if worker._process.poll() is None:
                worker._process.terminate()
                worker._process.wait(timeout=3)
        if server_process is not None and server_process.poll() is None:
            server_process.terminate()
            server_process.wait(timeout=3)
