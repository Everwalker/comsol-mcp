from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import _RuntimeObservationBinding
from comsol_mcp._runtime_installation import runtime_id_for_root
from comsol_mcp._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    session_state_directory,
)
from comsol_mcp._session_server import _server_directories, build_server_command


def _session_fixture(tmp_path: Path, monkeypatch, *, identity_server_np=2,
                     process_server_np=2, launch_hash_override=None):
    from comsol_mcp import _g2_isolation as isolation
    from comsol_mcp import _platform_process, _session_server

    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(isolation, "_require_isolation_adapter", lambda: None)

    installation = tmp_path / "install"
    source_launcher = installation / "bin" / "comsol"
    source_launcher.parent.mkdir(parents=True)
    source_launcher.write_bytes(b"synthetic inert COMSOL launcher bytes")
    runtime_id = "fixture-runtime-np-consumer"
    state_root = tmp_path / "control" / "session-runtime-state"
    project_id, session_id, worker_instance_id = "project-fixture", "session-fixture", "worker-fixture"
    session_home = session_state_directory(state_root, project_id, session_id)
    worker_state = session_home / "worker"
    worker_state.mkdir(parents=True)
    project_root = tmp_path / "project"
    project_root.mkdir()

    runtime = SessionRuntimeConfig(
        runtime_id=runtime_id,
        comsol_version="6.4.0.293",
        installation_root=installation,
        java_executable=tmp_path / "java" / "bin" / "java",
        classpath=(tmp_path / "classpath" / "comsol.jar",),
        preferences_dir=tmp_path / "prefs",
        session_state_root=state_root,
    )
    owned_root = session_home / "owned-server"
    directories = _server_directories(owned_root)
    directories.preferences.mkdir(parents=True)
    private_launcher = directories.private_installation / "bin" / "comsol"
    private_launcher.parent.mkdir(parents=True)
    private_launcher.write_bytes(source_launcher.read_bytes())
    for path in (directories.runtime, directories.temporary, directories.recovery,
                 directories.logs):
        path.mkdir(parents=True, exist_ok=True)

    port, server_pid, worker_pid = 23145, 90101, 90102
    endpoint_socket = CanonicalSocket("127.0.0.1", port)
    command = build_server_command(
        directories, platform_name="darwin", server_np=process_server_np,
    )
    identity_command = build_server_command(
        directories, platform_name="darwin", server_np=identity_server_np,
    )
    launch_hash = launch_hash_override or hashlib.sha256(
        json.dumps(identity_command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    server_identity = OwnedServerProcessIdentity(
        pid=server_pid,
        birth="start_epoch_ms:1800000000450",
        executable=str(private_launcher.resolve()),
        listener_sockets=(endpoint_socket,),
        start_epoch_ms=1_800_000_000_450,
        launch_argv_sha256=launch_hash,
        executable_sha256=hashlib.sha256(source_launcher.read_bytes()).hexdigest(),
        runtime_id=runtime_id,
        server_np=identity_server_np,
    )
    server_process = SimpleNamespace(pid=server_pid, args=list(command))
    worker_args = ["fixture-worker", "--state", str(worker_state)]
    worker_process = SimpleNamespace(pid=worker_pid, args=list(worker_args))
    worker = SimpleNamespace(_process=worker_process, state_dir=str(worker_state), generation=7)
    ledger = SimpleNamespace(
        session_id=session_id,
        server_ownership="mcp_managed",
        server_instance_id="server-epoch-fixture",
    )
    service = SimpleNamespace(ledger=ledger)
    binding = _RuntimeObservationBinding(
        worker=worker,
        service=service,
        installation_runtime_id=runtime_id_for_root(installation),
        runtime_id=runtime_id,
        worker_instance_id=worker_instance_id,
        connection_epoch=7,
        server_instance_id=ledger.server_instance_id,
        endpoint=f"127.0.0.1:{port}",
        legacy=False,
        comsol_version="6.4.0.293",
        configured_version="6.4.0.293",
        worker_process=worker_process,
        worker_process_pid=worker_pid,
        worker_process_start_epoch_ms=1_800_000_000_470,
        worker_process_argv_sha256=hashlib.sha256(
            json.dumps(worker_args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        worker_state_dir=str(worker_state.resolve()),
    )
    backend = SimpleNamespace(
        service=service,
        worker=worker,
        _runtime_observation_binding=binding,
        worker_identity={
            "server_ownership": "mcp_managed",
            "runtime_id": runtime_id,
            "worker_instance_id": worker_instance_id,
            "connection_epoch": 7,
            "server_instance_id": ledger.server_instance_id,
            "endpoint": f"127.0.0.1:{port}",
        },
        endpoint_key=f"127.0.0.1:{port}",
        project_root=project_root,
    )
    endpoint = SessionEndpointIdentity(
        host="127.0.0.1", port=port, worker_epoch=7,
        observed_peer=endpoint_socket, owned_process=server_identity,
    )
    context = SessionRuntimeContext(
        project_id=project_id,
        session_id=session_id,
        project_root=project_root,
        runtime=runtime,
        endpoint=endpoint,
        backend=backend,
        worker_instance_id=worker_instance_id,
        worker=worker,
        service=service,
        server_handle=server_process,
        server_ownership="mcp_managed",
        process_identity=server_identity,
        client_connected=True,
        client_retired=False,
        connected_host="127.0.0.1",
        connected_port=port,
        server_started_by_mcp=True,
    )

    births = {
        server_pid: {"alive": True, "start_epoch_ms": server_identity.start_epoch_ms},
        worker_pid: {"alive": True, "start_epoch_ms": binding.worker_process_start_epoch_ms},
    }
    monkeypatch.setattr(_platform_process, "process_identity", lambda pid: births[pid])

    def alive(process, pid, *, label):
        assert process.pid == pid
        assert label in {"Server", "Worker"}

    monkeypatch.setattr(isolation, "_session_popen_alive", alive)
    monkeypatch.setattr(
        isolation,
        "_verify_macos_live_command",
        lambda pid, argv, **_kwargs: assert_worker_command(pid, argv, worker_pid, worker_args),
    )
    monkeypatch.setattr(isolation, "_socket_rows", lambda _port, **_kwargs: [
        {"pid": server_pid, "state": "LISTEN", "endpoint": f"127.0.0.1:{port}",
         "local_endpoint": f"127.0.0.1:{port}", "remote_endpoint": None},
        {"pid": server_pid, "state": "ESTABLISHED", "endpoint": f"127.0.0.1:{port}->127.0.0.1:42001",
         "local_endpoint": f"127.0.0.1:{port}", "remote_endpoint": "127.0.0.1:42001"},
        {"pid": worker_pid, "state": "ESTABLISHED", "endpoint": f"127.0.0.1:42001->127.0.0.1:{port}",
         "local_endpoint": "127.0.0.1:42001", "remote_endpoint": f"127.0.0.1:{port}"},
    ])

    original_builder = _session_server.build_server_command
    seen_np = []

    def capture_builder(dirs, **kwargs):
        seen_np.append(kwargs.get("server_np"))
        return original_builder(dirs, **kwargs)

    monkeypatch.setattr(_session_server, "build_server_command", capture_builder)
    return isolation, context, backend, worker, service, server_identity, seen_np


def assert_worker_command(pid, argv, worker_pid, worker_args):
    assert pid == worker_pid
    assert list(argv) == worker_args


def test_darwin_isolation_consumer_rebuilds_exact_core_np2_command(tmp_path, monkeypatch):
    isolation, context, backend, worker, service, _identity, seen_np = _session_fixture(tmp_path, monkeypatch)

    proof = isolation.verify_session_owned_server(
        context, backend=backend, worker=worker, service=service,
    )

    assert proof["verified"] is True
    assert proof["endpoint"] == "127.0.0.1:23145"
    assert proof["server_pid"] == 90101
    assert proof["worker_pid"] == 90102
    # Both the isolation command reconstruction and the preferences consumer
    # receive the same recorded Core server_np value.
    assert seen_np == [2, 2]


@pytest.mark.parametrize("tamper", ["server_np", "popen_argv", "launch_hash"])
def test_darwin_isolation_consumer_rejects_np_argv_or_hash_drift(tmp_path, monkeypatch, tamper):
    if tamper == "server_np":
        values = _session_fixture(tmp_path, monkeypatch, identity_server_np=1, process_server_np=2)
    elif tamper == "popen_argv":
        values = _session_fixture(tmp_path, monkeypatch, identity_server_np=2, process_server_np=1)
    else:
        values = _session_fixture(tmp_path, monkeypatch, launch_hash_override="a" * 64)
    isolation, context, backend, worker, service, _identity, _seen_np = values

    with pytest.raises(ExecutionContractError) as caught:
        isolation.verify_session_owned_server(
            context, backend=backend, worker=worker, service=service,
        )
    assert caught.value.code == "ISOLATION_PROOF_REQUIRED"
