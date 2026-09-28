from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from comsol_mcp._platform_process import process_identity
from comsol_mcp._session_context import (
    CanonicalSocket, OwnedServerProcessIdentity, SessionRuntimeConfig,
)
from comsol_mcp._session_server import (
    ManagedServerHandle,
    OwnedServerError,
    OwnedServerLauncher,
    ServerDirectories,
    _parse_lsof_rows,
    build_server_command,
    prepare_private_loopback_installation,
    windows_owned_server_path_budget,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _installation(root: Path) -> Path:
    (root / "bin/servers/webbridge/conf").mkdir(parents=True)
    (root / "bin/servers/webbridge/conf/server.xml").write_text(
        '<Server><Service><Connector port="2036" address="0.0.0.0" /></Service></Server>',
        encoding="utf-8",
    )
    (root / "bin/comsol").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(root / "bin/comsol", 0o755)
    (root / "lib").mkdir()
    (root / "lib/comsol.jar").write_bytes(b"synthetic")
    return root


def test_private_loopback_installation_changes_only_private_server_config(tmp_path):
    source = _installation(tmp_path / "installed")
    installed_xml = source / "bin/servers/webbridge/conf/server.xml"
    source_hash = _sha256(installed_xml)
    private = tmp_path / "private" / "install"
    private.parent.mkdir()
    private.mkdir()

    receipt = prepare_private_loopback_installation(source, private)

    private_xml = private / "bin/servers/webbridge/conf/server.xml"
    assert receipt["installed_server_config_sha256_before"] == source_hash
    assert receipt["installed_server_config_sha256_after"] == source_hash
    assert receipt["private_connector_address"] == "127.0.0.1"
    assert receipt["install_tree_mutated"] is False
    assert _sha256(installed_xml) == source_hash
    assert 'address="127.0.0.1"' in private_xml.read_text(encoding="utf-8")
    assert (private / "lib").is_symlink()
    assert not (private / "bin/servers").is_symlink()


def test_private_loopback_installation_rejects_multiple_connectors(tmp_path):
    source = _installation(tmp_path / "installed")
    xml = source / "bin/servers/webbridge/conf/server.xml"
    xml.write_text('<Server><Connector/><Connector/></Server>', encoding="utf-8")
    private = tmp_path / "private"

    with pytest.raises(OwnedServerError, match="exactly one WebBridge Connector"):
        prepare_private_loopback_installation(source, private)

    assert not private.exists()
    assert xml.read_text(encoding="utf-8") == '<Server><Connector/><Connector/></Server>'


def test_server_command_is_session_scoped_and_platform_explicit(tmp_path):
    dirs = ServerDirectories(
        root=tmp_path, private_installation=tmp_path / "install", runtime=tmp_path / "runtime",
        preferences=tmp_path / "prefs", temporary=tmp_path / "tmp", recovery=tmp_path / "recovery",
        logs=tmp_path / "logs", port_file=tmp_path / "runtime/port",
        log_file=tmp_path / "logs/server.log",
    )
    (dirs.private_installation / "bin").mkdir(parents=True)
    (dirs.private_installation / "bin/comsol").write_text("launcher", encoding="utf-8")

    mac = build_server_command(dirs, platform_name="darwin")
    assert mac[:2] == [str(dirs.private_installation / "bin/comsol"), "mphserver"]
    assert mac[mac.index("-portfile") + 1] == str(dirs.port_file)
    assert mac[mac.index("-prefsdir") + 1] == str(dirs.preferences)
    assert "-multi" in mac and mac[mac.index("-multi") + 1] == "on"

    windows_launcher = dirs.private_installation / "bin/win64/comsolmphserver.exe"
    windows_launcher.parent.mkdir()
    windows_launcher.write_text("launcher", encoding="utf-8")
    windows = build_server_command(dirs, platform_name="windows")
    assert windows[0] == str(windows_launcher)
    assert "mphserver" not in windows


def test_windows_server_budget_uses_full_hashed_session_suffix_and_fails_closed(tmp_path):
    state_root = tmp_path / "h" / "0123456789abcdef" / "control-private" / "session-runtime-state"
    budget = windows_owned_server_path_budget(state_root, "project", "session")
    assert budget["path_limit_utf16_units_including_nul"] == 260
    assert budget["command_line_limit_utf16_units_including_nul"] == 32767
    assert budget["path_utf16_units_including_nul"]["launcher"] <= 260
    assert budget["path_utf16_units_including_nul"]["owned_server_cwd"] <= 260
    assert budget["command_line_utf16_units_including_nul"] < 32767
    assert not state_root.exists()

    deep_home = tmp_path / ("r" * 120) / ("s" * 120) / "h" / "0123456789abcdef"
    deep_state_root = deep_home / "control-private" / "session-runtime-state"
    with pytest.raises(OwnedServerError, match="path budget exceeded"):
        windows_owned_server_path_budget(deep_state_root, "project", "session")
    assert not deep_home.exists()


def test_windows_owned_launcher_passes_exact_validated_executable_to_popen(tmp_path):
    source = _installation(tmp_path / "installed")
    executable = source / "bin/win64/comsolmphserver.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic Windows launcher")
    state_root = tmp_path / "short" / "h" / "0123456789abcdef" / "control-private" / "session-runtime-state"
    runtime = SessionRuntimeConfig(
        runtime_id="fixture-runtime", comsol_version="6.4.0.293",
        installation_root=source, java_executable=tmp_path / "jdk/bin/java.exe",
        classpath=(source / "client.jar",), preferences_dir=tmp_path / "prefs",
        session_state_root=state_root,
    )
    calls = []

    def refuse_at_popen(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        raise OSError("synthetic launch boundary; no child created")

    launcher = OwnedServerLauncher(process_factory=refuse_at_popen, platform_name="windows")
    with pytest.raises(OwnedServerError, match="process creation failed"):
        launcher.start(runtime, "project", "session")

    assert len(calls) == 1
    command, options = calls[0]
    assert options["executable"] == command[0]
    assert command[0].endswith("/installation/bin/win64/comsolmphserver.exe")
    assert Path(command[0]).is_file()
    assert options["creationflags"] == getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    assert "start_new_session" not in options


def test_lsof_field_parser_preserves_pid_and_canonical_socket_rows():
    rows = _parse_lsof_rows(
        "p321\nnTCP 127.0.0.1:2036 (LISTEN)\n"
        "p654\nnTCP [::1]:2037 (LISTEN)\n"
    )
    assert rows == [
        (321, CanonicalSocket("127.0.0.1", 2036)),
        (654, CanonicalSocket("::1", 2037)),
    ]


def _real_loopback_child(tmp_path: Path):
    port_file = tmp_path / "port"
    code = textwrap.dedent(
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
        [sys.executable, "-c", code, str(port_file)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = __import__("time").monotonic() + 5
    while __import__("time").monotonic() < deadline and not port_file.exists():
        if process.poll() is not None:
            raise RuntimeError("harmless loopback child exited before port publication")
        __import__("time").sleep(0.02)
    assert port_file.exists()
    return process, int(port_file.read_text(encoding="ascii"))


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="exact listener adapter is supported on Darwin and Windows")
def test_exact_birth_and_listener_gate_before_stopping_task_owned_child(tmp_path):
    from comsol_mcp._session_server import listener_rows

    process, port = _real_loopback_child(tmp_path)
    probe = lambda candidate: listener_rows(candidate, platform_name=sys.platform)
    try:
        observed = process_identity(process.pid)
        if observed.get("alive") is not True or type(observed.get("start_epoch_ms")) is not int:
            pytest.skip("host cannot provide exact process birth identity")
        rows = probe(port)
        assert rows == [(process.pid, CanonicalSocket("127.0.0.1", port))]
        identity = OwnedServerProcessIdentity(
            pid=process.pid, birth=f"start_epoch_ms:{observed['start_epoch_ms']}",
            executable=str(Path(sys.executable).resolve()), listener_sockets=(CanonicalSocket("127.0.0.1", port),),
            start_epoch_ms=observed["start_epoch_ms"],
        )
        dirs = ServerDirectories(
            root=tmp_path, private_installation=tmp_path, runtime=tmp_path, preferences=tmp_path,
            temporary=tmp_path, recovery=tmp_path, logs=tmp_path, port_file=tmp_path / "port",
            log_file=tmp_path / "server.log",
        )
        handle = ManagedServerHandle(
            process=process, runtime_id="test-runtime", executable=identity.executable,
            directories=dirs, endpoint=CanonicalSocket("127.0.0.1", port),
            process_identity=identity, start_epoch_ms=identity.start_epoch_ms,
        )
        launcher = OwnedServerLauncher(
            process_identity_reader=process_identity,
            listener_reader=probe,
            platform_name=sys.platform,
        )

        wrong_birth = OwnedServerProcessIdentity(
            pid=identity.pid, birth="start_epoch_ms:1", executable=identity.executable,
            listener_sockets=identity.listener_sockets, start_epoch_ms=identity.start_epoch_ms,
        )
        handle.process_identity = wrong_birth
        with pytest.raises(OwnedServerError, match="birth"):
            launcher.stop(handle)
        assert process.poll() is None
        handle.process_identity = identity

        absent_probe = OwnedServerLauncher(
            process_identity_reader=process_identity,
            listener_reader=lambda _port: [],
            platform_name=sys.platform,
        )
        with pytest.raises(OwnedServerError, match="listener"):
            absent_probe.stop(handle)
        assert process.poll() is None

        result = launcher.stop(handle)
        assert result["exit_confirmed"] is True
        assert result["child_reaped"] is True
        assert result["listener_absent"] is True
        assert result["server_stopped"] is True
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
