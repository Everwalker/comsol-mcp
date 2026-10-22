from __future__ import annotations

import ctypes as C
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp._g2_isolation import verify_session_owned_server
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._session_context import (
    CanonicalSocket, OwnedServerProcessIdentity, SessionEndpointIdentity,
    SessionRuntimeConfig, SessionRuntimeContext, session_state_directory,
    use_session_context,
)
from tests.test_windows_suspended_ownership import _launch_api


_FILETIME_UNIX_EPOCH_MS = 11644473600000


def _completed_native_pair(monkeypatch, tmp_path):
    """Run the production Win32 launch return path over explicit API stubs."""
    profile, api, events, state = _launch_api(monkeypatch)
    state.update({"members": [], "births": {}, "images": {}, "next_pid": 456,
                  "next_handle": 200, "last_open_path": None})

    class PI(C.Structure):
        _fields_ = [("hProcess", C.c_void_p),
                    ("hThread", C.c_void_p), ("dwProcessId", C.c_uint32), ("dwThreadId", C.c_uint32)]

    def create_process(*args):
        info = C.cast(args[-1], C.POINTER(PI)).contents
        pid, handle = state["next_pid"], state["next_handle"]
        state["next_pid"] += 1
        state["next_handle"] += 2
        state["current_pid"] = pid
        image_path = args[1]
        birth = 133800000000000000 + pid
        info.hProcess, info.hThread = handle, handle + 1
        info.dwProcessId, info.dwThreadId = pid, pid + 1000
        state["births"][handle] = birth
        state["images"][handle] = image_path
        state["members"].append(pid)
        events.append(("CREATE_STUB", pid, handle))
        return 1

    api.a.CreateProcessAsUserW = create_process
    api.open_process = lambda pid: 777
    api.process_times = lambda handle: (10, None) if handle == 777 else (state["births"].get(handle, 0), None)
    api.process_image_path = lambda handle: state["images"][handle]
    api.is_process_in_job = lambda *_: True
    api._job_process_ids = lambda _job: list(state["members"])
    api.poll_process = lambda _handle: None

    class Image:
        def source_fingerprint(self, _handle):
            return ((1, 2), 10, 3, 4, 1)

        def close(self, _handle):
            return None

    def open_image(path):
        state["last_open_path"] = path
        return Image(), [], 12, ((1, 2), 10, 3, 4, 1), path

    api._open_image = open_image
    # This software-only control isolates the post-launch ownership consumer.
    # Existing controls keep the real prebirth/policy gates fail-closed.
    monkeypatch.setattr(
        "comsol_mcp._windows_t037_child.observe_retained_child",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "comsol_mcp._windows_t037_acl.check_suspended_child_dacl",
        lambda *_args, **_kwargs: {"state": "OBSERVED", "software_fixture": True},
    )
    monkeypatch.setattr(
        "comsol_mcp._windows_t037_resume.issue_native_resume",
        lambda *_args, **_kwargs: object(),
    )
    def resume(child, _capability):
        child.note_resume_attempt()
        return 1
    monkeypatch.setattr("comsol_mcp._windows_t037_resume.resume_native_child", resume)
    monkeypatch.setattr(
        "comsol_mcp._windows_t037_child.NativeChildProcessApi",
        lambda: SimpleNamespace(current_pid=lambda: 55,
                                process_pid=lambda _handle: state["current_pid"],
                                parent_pid=lambda _pid: 55),
    )

    session_id = "g2-handle-test"
    factory = win.T037SessionProcessFactory(profile, session_id, api=api)
    factory._job = 300
    factory.preflight = lambda: None
    factory._infer_role = lambda command: "server" if command[0] == server_argv[0] else "worker"

    project_root = tmp_path / "project"
    install_root = tmp_path / "comsol-install"
    state_root = tmp_path / "state"
    for path in (project_root, install_root, state_root):
        path.mkdir(parents=True, exist_ok=True)
    runtime_id = "runtime-test"
    runtime = SessionRuntimeConfig(
        runtime_id=runtime_id, comsol_version="6.4", installation_root=install_root,
        java_executable=install_root / "java.exe", classpath=(install_root / "m.jar",),
        preferences_dir=install_root / "prefs", session_state_root=state_root,
    )
    session_home = session_state_directory(state_root, "project-test", session_id)
    (session_home / "worker").mkdir(parents=True)
    from comsol_mcp._session_server import _server_directories, build_server_command
    server_dirs = _server_directories(session_home / "owned-server")
    server_argv = build_server_command(server_dirs, platform_name="win32", validate_launcher=False)
    worker_argv = [profile._launch_image("worker").path, "-cp", "synthetic-classpath", "comsol_mcp.worker.Main"]
    # Set the command selector after its exact Windows session path is known.
    api._hash_image_handle = lambda *_: (
        profile.server_image_sha256 if state["last_open_path"] == server_argv[0]
        else profile.worker_image_sha256
    )

    original_os_name = win.os.name
    monkeypatch.setattr(win.os, "name", "nt")
    try:
        server_process = factory(server_argv)
        worker_process = factory(worker_argv)
    finally:
        monkeypatch.setattr(win.os, "name", original_os_name)
    server_owner = win._native_g2_session_launch_owner(server_process)
    worker_owner = win._native_g2_session_launch_owner(worker_process)
    assert server_owner is not None and worker_owner is not None
    assert server_owner.session_factory is worker_owner.session_factory is factory
    assert server_owner.api is worker_owner.api is api
    assert server_owner.job_handle == worker_owner.job_handle == 300

    start_ms = lambda owner: owner.birth_filetime_100ns // 10000 - _FILETIME_UNIX_EPOCH_MS
    server_identity = OwnedServerProcessIdentity(
        pid=server_owner.pid, birth=f"start_epoch_ms:{start_ms(server_owner)}",
        executable=server_owner.image_path,
        listener_sockets=(CanonicalSocket("127.0.0.1", 56389),),
        start_epoch_ms=start_ms(server_owner), launch_argv_sha256=server_owner.argv_sha256,
        executable_sha256=server_owner.image_sha256, runtime_id=runtime_id,
    )
    endpoint = SessionEndpointIdentity(
        "127.0.0.1", 56389, 1, CanonicalSocket("127.0.0.1", 56389), server_identity,
    )
    service = SimpleNamespace(ledger=SimpleNamespace(
        session_id=session_id, server_ownership="mcp_managed", server_instance_id="server-instance-test",
    ))
    worker = SimpleNamespace(_process=worker_process, state_dir=session_home / "worker", generation=1)
    backend = SimpleNamespace(
        worker=worker, service=service, project_root=project_root,
        endpoint_key="127.0.0.1:56389",
        worker_identity={
            "server_ownership": "mcp_managed", "runtime_id": runtime_id,
            "worker_instance_id": "worker-instance-test", "connection_epoch": 1,
            "server_instance_id": "server-instance-test", "endpoint": "127.0.0.1:56389",
        },
    )
    def process_identity(pid, *, platform_name=None):
        owner = server_owner if pid == server_owner.pid else worker_owner if pid == worker_owner.pid else None
        return {"alive": True, "start_epoch_ms": start_ms(owner)} if owner else {"alive": False}
    monkeypatch.setattr("comsol_mcp._platform_process.process_identity", process_identity)
    monkeypatch.setattr("sys.platform", "win32")
    ManagedBackend._bind_runtime_observation_context(
        backend, installation_root=install_root, runtime_id=runtime_id,
        worker_instance_id="worker-instance-test", connection_epoch=1,
        server_instance_id="server-instance-test", endpoint="127.0.0.1:56389",
        legacy=False,
    )
    binding = backend._runtime_observation_binding
    assert binding.worker_native_owner is worker_owner
    assert binding.worker_process is worker_process
    assert binding.worker_process_start_epoch_ms == start_ms(worker_owner)

    context = SessionRuntimeContext(
        project_id="project-test", session_id=session_id, project_root=project_root,
        runtime=runtime, endpoint=endpoint, backend=backend,
        worker_instance_id="worker-instance-test", worker=worker, service=service,
        server_handle=server_process, server_ownership="mcp_managed",
        process_identity=server_identity, client_connected=True, client_retired=False,
        connected_host="127.0.0.1", connected_port=56389, server_started_by_mcp=True,
    )
    from comsol_mcp._windows_job_limits import FLAGS
    limits = profile.job_limits
    api.job_snapshot = lambda _job: {
        "state": "OBSERVED", "limit_flags": FLAGS, "kill_on_job_close": False,
        "breakaway_allowed": False, "silent_breakaway_allowed": False,
        "active_process_limit": limits.active_process_limit,
        "process_memory_limit_bytes": limits.process_memory_limit_bytes,
        "job_memory_limit_bytes": limits.job_memory_limit_bytes,
        "process_ids": [server_owner.pid, worker_owner.pid], "process_count": 2,
    }
    api.is_process_in_job = lambda *_: True

    from comsol_mcp import _g2_isolation as isolation
    def process_snapshot(pid):
        owner = server_owner if pid == server_owner.pid else worker_owner if pid == worker_owner.pid else None
        command = subprocess.list2cmdline(list(owner.argv)) if owner else ""
        return {"pid": pid, "command": command,
                "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()}
    monkeypatch.setattr(isolation, "_windows_process_snapshot", process_snapshot)
    monkeypatch.setattr(isolation, "_windows_socket_rows", lambda _port: [
        {"pid": server_owner.pid, "state": "LISTEN", "local_endpoint": "127.0.0.1:56389",
         "remote_endpoint": None, "endpoint": "127.0.0.1:56389"},
        {"pid": server_owner.pid, "state": "ESTABLISHED", "local_endpoint": "127.0.0.1:56389",
         "remote_endpoint": "127.0.0.1:50000", "endpoint": "127.0.0.1:56389->127.0.0.1:50000"},
        {"pid": worker_owner.pid, "state": "ESTABLISHED", "local_endpoint": "127.0.0.1:50000",
         "remote_endpoint": "127.0.0.1:56389", "endpoint": "127.0.0.1:50000->127.0.0.1:56389"},
    ])
    return profile, api, factory, server_process, worker_process, server_owner, worker_owner, backend, worker, service, context


def test_real_windows_handle_consumer_accepts_bound_server_worker_socket_pair(monkeypatch, tmp_path):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, _api, _factory, _server, _worker_process, _server_owner, _worker_owner, backend, worker, service, context = values
    proof = verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    assert proof["verified"] is True
    assert proof["server_pid"] == context.process_identity.pid
    assert proof["worker_pid"] == worker._process.pid
    assert proof["endpoint"] == "127.0.0.1:56389"
    assert proof["worker_health_rpc_performed"] is False


def test_production_isolation_dispatch_accepts_only_active_windows_t037_session(monkeypatch, tmp_path):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, _api, _factory, _server, _worker_process, _server_owner, _worker_owner, backend, worker, service, context = values
    with use_session_context(context):
        proof = ManagedBackend._require_g2_isolation(backend)
    assert proof["verified"] is True
    assert "sealed original T037 Server/Worker HANDLEs" in proof["proof_scope"]
    assert proof["worker_health_rpc_performed"] is False


def test_production_dispatch_does_not_fall_back_to_global_receipt_after_active_proof_failure(
    monkeypatch, tmp_path
):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, api, _factory, _server, worker_process, _server_owner, _worker_owner, backend, worker, service, context = values
    from comsol_mcp._execution_contract import ExecutionContractError

    api._g2_session_launch_owners.pop(id(worker_process))
    receipt_calls = []
    monkeypatch.setattr("comsol_mcp._managed_backend.configured_receipt",
                        lambda: receipt_calls.append("called") or {"verified": True})
    with use_session_context(context):
        with pytest.raises(ExecutionContractError):
            ManagedBackend._require_g2_isolation(backend)
    assert receipt_calls == []


def test_production_dispatch_reports_unsupported_platform_without_receipt_fallback(
    monkeypatch, tmp_path
):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, _api, _factory, _server, _worker_process, _server_owner, _worker_owner, backend, worker, service, context = values
    from comsol_mcp._execution_contract import ExecutionContractError

    receipt_calls = []
    monkeypatch.setattr("sys.platform", "freebsd")
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("comsol_mcp._managed_backend.configured_receipt",
                        lambda: receipt_calls.append("called") or {"verified": True})
    with use_session_context(context):
        with pytest.raises(ExecutionContractError) as error:
            ManagedBackend._require_g2_isolation(backend)
    assert error.value.code == "UNSUPPORTED_PLATFORM"
    assert receipt_calls == []


def test_windows_handle_consumer_rejects_unsealed_process_and_changed_runtime_binding(monkeypatch, tmp_path):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, api, factory, _server, worker_process, _server_owner, worker_owner, backend, worker, service, context = values
    from comsol_mcp._execution_contract import ExecutionContractError
    original_owner = worker._process._api._g2_session_launch_owners.pop(id(worker_process))
    try:
        with pytest.raises(ExecutionContractError):
            verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    finally:
        api._g2_session_launch_owners[id(worker_process)] = original_owner
    with pytest.raises(AttributeError, match="immutable"):
        worker_owner.birth_filetime_100ns = worker_owner.birth_filetime_100ns + 1

    old_handle = worker_process._handle
    worker_process._handle = old_handle + 100
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    worker_process._handle = old_handle

    old_api = worker_process._api
    worker_process._api = win._Win32LaunchApi.__new__(win._Win32LaunchApi)
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    worker_process._api = old_api

    old_args = list(worker_process.args)
    worker_process.args[-1] = "tampered"
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    worker_process.args = old_args

    read_birth = api.process_times
    api.process_times = lambda handle: (
        worker_owner.birth_filetime_100ns + 1, None
    ) if handle == worker_owner.process_handle else read_birth(handle)
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    api.process_times = read_birth

    factory._job = 301
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    factory._job = worker_owner.job_handle

    factory.session_id = "different-session"
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    factory.session_id = context.session_id

    original_profile = factory.profile
    factory.profile = object()
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    factory.profile = original_profile

    original_membership = api.is_process_in_job
    api.is_process_in_job = lambda *_: False
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    api.is_process_in_job = original_membership

    backend._runtime_observation_binding = SimpleNamespace(**{
        **vars(backend._runtime_observation_binding), "worker_native_owner": object(),
    })
    with pytest.raises(ExecutionContractError):
        verify_session_owned_server(context, backend=backend, worker=worker, service=service)


def test_windows_handle_consumer_rejects_extra_or_non_loopback_socket(monkeypatch, tmp_path):
    values = _completed_native_pair(monkeypatch, tmp_path)
    _profile, _api, _factory, _server, _worker_process, server_owner, _worker_owner, backend, worker, service, context = values
    from comsol_mcp import _g2_isolation as isolation
    original = isolation._windows_socket_rows
    rows = original(56389)
    isolation._windows_socket_rows = lambda _port: rows + [{
        "pid": server_owner.pid + 99, "state": "ESTABLISHED",
        "local_endpoint": "127.0.0.1:60000", "remote_endpoint": "127.0.0.1:56389",
        "endpoint": "127.0.0.1:60000->127.0.0.1:56389",
    }]
    from comsol_mcp._execution_contract import ExecutionContractError
    try:
        with pytest.raises(ExecutionContractError):
            verify_session_owned_server(context, backend=backend, worker=worker, service=service)
    finally:
        isolation._windows_socket_rows = original
