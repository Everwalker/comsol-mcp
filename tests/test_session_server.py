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
    runtime_state_root,
    session_state_directory,
)
from comsol_mcp._session_server import (
    ManagedServerHandle,
    OwnedServerError,
    OwnedServerLauncher,
    ServerDirectories,
    _utf16_units_with_nul,
    _parse_lsof_rows,
    build_server_command,
    prepare_private_loopback_installation,
    windows_owned_server_path_budget,
)
from comsol_mcp._process_diagnostics import (
    ProcessDiagnosticError,
    sanitize_startup_observation,
    write_atomic_json_snapshot,
    write_startup_diagnostic,
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
    state_root = _windows_state_root_for_test(tmp_path.anchor, 0)
    budget = windows_owned_server_path_budget(state_root, "project", "session")
    session_root = session_state_directory(state_root, "project", "session")
    assert budget["path_limit_utf16_units_including_nul"] == 260
    assert budget["command_line_limit_utf16_units_including_nul"] == 32767
    assert budget["path_utf16_units_including_nul"]["launcher"] <= 260
    assert budget["path_utf16_units_including_nul"]["critical_image_library"] <= 260
    assert budget["path_utf16_units_including_nul"]["native_recovery_solution_file"] <= 260
    assert budget["path_utf16_units_including_nul"]["owned_server_cwd"] <= 260
    assert budget["command_line_utf16_units_including_nul"] < 32767
    assert len(state_root.parent.parent.name) == 16
    assert len(session_root.name) == 64
    assert all(char in "0123456789abcdef" for char in session_root.name)
    assert not state_root.exists()

    legacy_root = state_root.parent / "session-runtime-state"
    legacy_recovery_file = (
        session_state_directory(legacy_root, "project", "session") / "owned-server" /
        "recovery" / "MPHRecovery9999999999date Sep 29 2026 10-04 PM.mph" /
        f"solution{'9' * 20}.mphbin{'9' * 20}"
    )
    assert (_utf16_units_with_nul(str(legacy_recovery_file))
            == budget["path_utf16_units_including_nul"]["native_recovery_solution_file"] + 20)

    deep_home = tmp_path / ("r" * 120) / ("s" * 120) / "h" / "0123456789abcdef"
    deep_state_root = deep_home / "control-private" / "session-runtime-state"
    with pytest.raises(OwnedServerError, match="path budget exceeded"):
        windows_owned_server_path_budget(deep_state_root, "project", "session")
    assert not deep_home.exists()


def _windows_server_paths_for_test(state_root: Path) -> dict[str, Path]:
    installation = session_state_directory(state_root, "project", "session") / "owned-server" / "installation"
    return {
        "launcher": installation / "bin" / "win64" / "comsolmphserver.exe",
        "critical_image_library": (
            installation / "ext" / "graphicsmagick" / "win64" / "CORE_RL_magick_.dll"
        ),
    }


def _windows_state_root_for_test(anchor: str, padding: int) -> Path:
    parts = [Path(anchor)]
    if padding:
        parts.append(Path("x" * padding))
    parts.extend((Path("w21-budget"), Path("h"), Path("0123456789abcdef"),
                  Path("control-private")))
    return runtime_state_root(Path(*parts), platform_name="Windows")


def test_windows_native_recovery_path_budget_covers_pid_and_long_filename_maxima(tmp_path):
    state_root = _windows_state_root_for_test(tmp_path.anchor, 0)
    budget = windows_owned_server_path_budget(state_root, "project", "session")
    measured = budget["path_utf16_units_including_nul"]["native_recovery_solution_file"]
    session_root = session_state_directory(state_root, "project", "session")
    recovery_file = (
        session_root / "owned-server" / "recovery" /
        "MPHRecovery9999999999date Sep 29 2026 10-04 PM.mph" /
        f"solution{'9' * 20}.mphbin{'9' * 20}"
    )
    assert measured == _utf16_units_with_nul(str(recovery_file))
    assert measured <= 260


def test_windows_native_recovery_path_overflow_fails_closed(tmp_path):
    state_root = None
    for padding in range(0, 180):
        candidate = _windows_state_root_for_test(tmp_path.anchor, padding)
        root = session_state_directory(candidate, "project", "session") / "owned-server"
        native_file = (
            root / "recovery" /
            "MPHRecovery9999999999date Sep 29 2026 10-04 PM.mph" /
            f"solution{'9' * 20}.mphbin{'9' * 20}"
        )
        others = {
            "launcher": root / "installation" / "bin" / "win64" / "comsolmphserver.exe",
            "critical_image_library": (
                root / "installation" / "ext" / "graphicsmagick" /
                "win64" / "CORE_RL_magick_.dll"
            ),
        }
        if (_utf16_units_with_nul(str(native_file)) > 260
                and all(_utf16_units_with_nul(str(path)) <= 260 for path in others.values())):
            state_root = candidate
            break
    assert state_root is not None, "test root must isolate native recovery path overflow"

    with pytest.raises(OwnedServerError, match="path budget exceeded: native_recovery_solution_file"):
        windows_owned_server_path_budget(state_root, "project", "session")
    assert not state_root.exists()


def test_windows_image_library_path_budget_rejects_before_birth_when_launcher_fits(tmp_path):
    # The synthetic root stays under the filesystem anchor so this check does
    # not depend on pytest's path length. No path is created by the test.
    state_root = None
    for padding in range(0, 180):
        candidate = _windows_state_root_for_test(tmp_path.anchor, padding)
        paths = _windows_server_paths_for_test(candidate)
        launcher_units = _utf16_units_with_nul(str(paths["launcher"]))
        library_units = _utf16_units_with_nul(str(paths["critical_image_library"]))
        if launcher_units <= 260 < library_units:
            state_root = candidate
            break
    assert state_root is not None, "test root must expose a launcher-fit / DLL-overflow interval"

    paths = _windows_server_paths_for_test(state_root)
    assert _utf16_units_with_nul(str(paths["launcher"])) <= 260
    assert _utf16_units_with_nul(str(paths["critical_image_library"])) > 260
    session_root = session_state_directory(state_root, "project", "session")
    server_home = state_root.parent.parent
    assert not server_home.exists()
    assert not state_root.exists()
    assert not session_root.exists()
    calls = []
    runtime = SessionRuntimeConfig(
        runtime_id="fixture-runtime", comsol_version="6.4.0.293",
        installation_root=tmp_path / "install", java_executable=tmp_path / "jdk/bin/java.exe",
        classpath=(tmp_path / "client.jar",), preferences_dir=tmp_path / "prefs",
        session_state_root=state_root,
    )
    launcher = OwnedServerLauncher(
        process_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
        platform_name="windows",
    )

    with pytest.raises(OwnedServerError, match="path budget exceeded: critical_image_library requires"):
        launcher.start(runtime, "project", "session")

    assert calls == []
    assert not server_home.exists()
    assert not state_root.exists()
    assert not session_root.exists()


def test_windows_server_budget_counts_exact_260_boundary_without_claiming_launch_success(tmp_path):
    # This is only conservative path arithmetic; it does not establish that
    # CreateProcessW or COMSOL will launch a path at this boundary.
    state_root = None
    for padding in range(0, 180):
        candidate = _windows_state_root_for_test(tmp_path.anchor, padding)
        paths = _windows_server_paths_for_test(candidate)
        if _utf16_units_with_nul(str(paths["critical_image_library"])) == 260:
            state_root = candidate
            break
    assert state_root is not None, "test root must represent the exact counted boundary"

    paths = _windows_server_paths_for_test(state_root)
    assert _utf16_units_with_nul(str(paths["critical_image_library"])) == 260
    assert _utf16_units_with_nul(str(paths["launcher"])) <= 260
    with pytest.raises(OwnedServerError, match="path budget exceeded: native_recovery_solution_file"):
        windows_owned_server_path_budget(state_root, "project", "session")


def test_windows_owned_launcher_passes_exact_validated_executable_to_popen(tmp_path):
    source = _installation(tmp_path / "installed")
    executable = source / "bin/win64/comsolmphserver.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic Windows launcher")
    state_root = runtime_state_root(
        tmp_path.parent / "h" / "0123456789abcdef" / "control-private",
        platform_name="Windows",
    )
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


def _startup_runtime(tmp_path: Path) -> SessionRuntimeConfig:
    source = _installation(tmp_path / "installed")
    return SessionRuntimeConfig(
        runtime_id="fixture-runtime", comsol_version="6.4.0.293",
        installation_root=source, java_executable=tmp_path / "jdk/bin/java",
        classpath=(source / "client.jar",), preferences_dir=tmp_path / "prefs",
        session_state_root=tmp_path / "state",
    )


class _StartupProcess:
    def __init__(self, pid=9182, poll_values=(None,)):
        self.pid = pid
        self._poll_values = list(poll_values)
        self.returncode = None
        self.mutation_calls = []

    def poll(self):
        if self._poll_values:
            value = self._poll_values.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return self.returncode

    def terminate(self):
        self.mutation_calls.append("terminate")

    def kill(self):
        self.mutation_calls.append("kill")

    def wait(self, *args, **kwargs):
        self.mutation_calls.append("wait")
        return self.returncode


def _startup_diagnostic_path(runtime: SessionRuntimeConfig, project="project", session="session") -> Path:
    return session_state_directory(runtime.session_state_root, project, session) / "owned-server" / "startup-diagnostic.json"


def _launcher_for_startup(process_factory, *, process_identity_reader=None, listener_reader=None,
                          ready_timeout_s=0.05):
    return OwnedServerLauncher(
        process_factory=process_factory,
        process_identity_reader=process_identity_reader or (lambda _pid: {"alive": True, "start_epoch_ms": 123456}),
        listener_reader=listener_reader or (lambda port: [(9182, CanonicalSocket("127.0.0.1", port))]),
        platform_name="darwin", ready_timeout_s=ready_timeout_s, poll_interval_s=0.001,
    )


def test_owned_server_start_records_created_birth_and_ready_snapshot(tmp_path):
    runtime = _startup_runtime(tmp_path)
    process = _StartupProcess()
    sink_rows = []

    def observe(row):
        # The sidecar is already the current snapshot when the higher-level
        # job-event sink receives the same sanitized observation.
        assert json.loads(_startup_diagnostic_path(runtime).read_text(encoding="utf-8")) == row
        sink_rows.append(dict(row))

    def create(command, **_options):
        port_file = Path(command[command.index("-portfile") + 1])
        port_file.write_text("2036", encoding="ascii")
        return process

    handle = _launcher_for_startup(create).start(
        runtime, "project", "session", observation_sink=observe,
    )

    assert handle.process is process
    assert [row["event"] for row in sink_rows] == ["PROCESS_CREATED", "BIRTH_OBSERVED", "READY"]
    assert all(set(row) == {
        "schema", "event", "observed_at_utc", "project_id", "session_id", "runtime_id",
        "pid", "birth", "exit_code", "exception_type", "errno", "winerror", "error_category",
    } for row in sink_rows)
    assert sink_rows[0]["pid"] == 9182 and sink_rows[0]["birth"] is None
    assert sink_rows[1]["birth"] == "start_epoch_ms:123456"
    assert json.loads(_startup_diagnostic_path(runtime).read_text(encoding="utf-8")) == sink_rows[-1]


def test_owned_server_start_fast_exit_records_actual_exit_and_never_mutates_child(tmp_path):
    runtime = _startup_runtime(tmp_path)
    process = _StartupProcess(poll_values=(17,))
    sink_rows = []

    with pytest.raises(OwnedServerError, match="exited before listener proof") as caught:
        _launcher_for_startup(lambda *_args, **_kwargs: process).start(
            runtime, "project", "session", observation_sink=sink_rows.append,
        )

    assert caught.value.handle.process is process
    assert [row["event"] for row in sink_rows] == ["PROCESS_CREATED", "BIRTH_OBSERVED", "EXIT_OBSERVED"]
    assert sink_rows[-1]["birth"] == "start_epoch_ms:123456"
    assert sink_rows[-1]["exit_code"] == 17
    assert json.loads(_startup_diagnostic_path(runtime).read_text(encoding="utf-8")) == sink_rows[-1]
    assert process.mutation_calls == []


def test_owned_server_start_missing_birth_records_unknown_and_retains_handle(tmp_path):
    runtime = _startup_runtime(tmp_path)
    process = _StartupProcess()
    sink_rows = []

    with pytest.raises(OwnedServerError) as caught:
        _launcher_for_startup(
            lambda *_args, **_kwargs: process,
            process_identity_reader=lambda _pid: {"alive": False},
        ).start(runtime, "project", "session", observation_sink=sink_rows.append)

    assert caught.value.handle.process is process and caught.value.uncertain is True
    assert [row["event"] for row in sink_rows] == ["PROCESS_CREATED", "STARTUP_UNKNOWN"]
    assert sink_rows[-1]["error_category"] == "BIRTH_OBSERVATION_FAILED"
    assert sink_rows[-1]["birth"] is None


def test_owned_server_start_timeout_records_unknown_without_terminating_child(tmp_path):
    runtime = _startup_runtime(tmp_path)
    process = _StartupProcess()
    sink_rows = []
    launcher = _launcher_for_startup(lambda *_args, **_kwargs: process, ready_timeout_s=0.0)

    with pytest.raises(OwnedServerError, match="did not publish a valid port") as caught:
        launcher.start(runtime, "project", "session", observation_sink=sink_rows.append)

    assert caught.value.handle.process is process
    assert [row["event"] for row in sink_rows] == ["PROCESS_CREATED", "BIRTH_OBSERVED", "STARTUP_UNKNOWN"]
    assert sink_rows[-1]["error_category"] == "STARTUP_TIMEOUT"
    assert process.mutation_calls == []


def test_owned_server_start_popen_failure_records_safe_type_and_no_pid(tmp_path, monkeypatch):
    runtime = _startup_runtime(tmp_path)
    sink_rows = []

    def fail(*_args, **_kwargs):
        raise PermissionError(13, "do not persist this secret path")

    with pytest.raises(OwnedServerError, match="process creation failed") as caught_primary:
        _launcher_for_startup(fail).start(runtime, "project", "session", observation_sink=sink_rows.append)

    assert caught_primary.value.startup_exception_type == "PermissionError"
    assert caught_primary.value.startup_errno == 13 and caught_primary.value.startup_winerror is None
    assert caught_primary.value.diagnostic_persistence_failed is False
    assert "secret path" not in str(caught_primary.value)
    assert len(sink_rows) == 1
    row = sink_rows[0]
    assert row["event"] == "PROCESS_CREATE_FAILED"
    assert row["pid"] is None and row["birth"] is None
    assert row["exception_type"] == "PermissionError" and row["errno"] == 13
    assert "secret path" not in json.dumps(row)
    assert json.loads(_startup_diagnostic_path(runtime).read_text(encoding="utf-8")) == row

    # A diagnostic sink failure cannot change the no-child launch-failure
    # contract or invent a retained process handle.
    no_sink_runtime = _startup_runtime(tmp_path / "sink-failure")
    failed_sink_rows = []

    def fail_sink(_row):
        failed_sink_rows.append(dict(_row))
        raise OSError("secret sink failure")

    with pytest.raises(OwnedServerError, match="process creation failed") as caught:
        _launcher_for_startup(fail).start(
            no_sink_runtime, "project", "session", observation_sink=fail_sink,
        )
    assert caught.value.handle is None and caught.value.uncertain is False
    assert caught.value.startup_exception_type == "PermissionError" and caught.value.startup_errno == 13
    assert caught.value.startup_winerror is None and caught.value.diagnostic_persistence_failed is True
    assert json.loads(_startup_diagnostic_path(no_sink_runtime).read_text(encoding="utf-8"))["event"] == "PROCESS_CREATE_FAILED"
    assert len(failed_sink_rows) == 1 and failed_sink_rows[0]["event"] == "PROCESS_CREATE_FAILED"
    assert "secret sink failure" not in json.dumps(failed_sink_rows)

    windows_error = OSError("synthetic Windows create failure")
    windows_error.winerror = 1234
    win_runtime = _startup_runtime(tmp_path / "win-error")

    def fail_windows(*_args, **_kwargs):
        raise windows_error

    with pytest.raises(OwnedServerError, match="process creation failed"):
        _launcher_for_startup(fail_windows).start(win_runtime, "project", "session")
    win_row = json.loads(_startup_diagnostic_path(win_runtime).read_text(encoding="utf-8"))
    assert win_row["exception_type"] == "OSError" and win_row["winerror"] == 1234
    win_caught = None
    try:
        _launcher_for_startup(fail_windows).start(_startup_runtime(tmp_path / "win-error-metadata"), "project", "session")
    except OwnedServerError as exc:
        win_caught = exc
    assert win_caught is not None
    assert win_caught.startup_exception_type == "OSError" and win_caught.startup_winerror == 1234
    assert win_caught.diagnostic_persistence_failed is False

    import comsol_mcp._session_server as session_server
    sidecar_failure_runtime = _startup_runtime(tmp_path / "sidecar-failure")
    monkeypatch.setattr(
        session_server, "write_startup_diagnostic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("diagnostic disk failure")),
    )
    sink_after_writer_failure = []
    with pytest.raises(OwnedServerError, match="process creation failed") as caught_write:
        _launcher_for_startup(fail).start(
            sidecar_failure_runtime, "project", "session",
            observation_sink=sink_after_writer_failure.append,
        )
    assert caught_write.value.handle is None and caught_write.value.uncertain is False
    assert caught_write.value.startup_exception_type == "PermissionError"
    assert caught_write.value.startup_errno == 13 and caught_write.value.diagnostic_persistence_failed is True
    assert sink_after_writer_failure == []
    assert not _startup_diagnostic_path(sidecar_failure_runtime).exists()

def test_owned_server_start_observation_failure_does_not_retry_sink_or_drop_child(tmp_path, monkeypatch):
    import comsol_mcp._session_server as session_server

    runtime = _startup_runtime(tmp_path)
    process = _StartupProcess()
    sink_calls = []

    def fail_sink(row):
        sink_calls.append(dict(row))
        raise OSError("secret sink detail")

    with pytest.raises(OwnedServerError, match="observation could not be persisted") as caught:
        _launcher_for_startup(lambda *_args, **_kwargs: process).start(
            runtime, "project", "session", observation_sink=fail_sink,
        )
    assert caught.value.handle.process is process and caught.value.uncertain is True
    assert len(sink_calls) == 1
    assert sink_calls[0]["event"] == "PROCESS_CREATED"
    sidecar = _startup_diagnostic_path(runtime).read_text(encoding="utf-8")
    assert "secret sink detail" not in sidecar

    # A sidecar write failure happens before callback dispatch and must not
    # recurse through the same broken writer while handling the failure.
    sink_calls.clear()
    monkeypatch.setattr(session_server, "write_startup_diagnostic", lambda *_a, **_k: (_ for _ in ()).throw(OSError("secret disk detail")))
    disk_failure_runtime = _startup_runtime(tmp_path / "disk-failure")
    disk_failure_process = _StartupProcess(pid=9183)
    with pytest.raises(OwnedServerError, match="observation could not be persisted") as caught_write:
        _launcher_for_startup(lambda *_args, **_kwargs: disk_failure_process).start(
            disk_failure_runtime, "project", "session", observation_sink=fail_sink,
        )
    assert caught_write.value.handle.process is disk_failure_process
    assert sink_calls == []


def test_startup_diagnostic_rejects_unlisted_secret_fields_and_replace_failure_preserves_old_snapshot(
        tmp_path, monkeypatch):
    import comsol_mcp._process_diagnostics as diagnostics

    valid = {
        "schema": "COMSOL_OWNED_SERVER_STARTUP_DIAGNOSTIC_V1", "event": "PROCESS_CREATED",
        "observed_at_utc": "2026-09-29T12:00:00.000Z", "project_id": "project",
        "session_id": "session", "runtime_id": "runtime", "pid": 9182, "birth": None,
        "exit_code": None, "exception_type": None, "errno": None, "winerror": None,
        "error_category": None,
    }
    for secret_field in ("token", "path", "command_line", "endpoint", "request_id", "operation_id", "job_id"):
        with pytest.raises(ProcessDiagnosticError, match="fixed schema"):
            sanitize_startup_observation({**valid, secret_field: "secret"})

    target = tmp_path / "startup-diagnostic.json"
    previous_row = sanitize_startup_observation(valid)
    target.write_text(json.dumps(previous_row, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    previous = target.read_bytes()
    monkeypatch.setattr(diagnostics.os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(ProcessDiagnosticError, match="could not be persisted"):
        write_startup_diagnostic(tmp_path, valid)
    assert target.read_bytes() == previous
    assert json.loads(target.read_text(encoding="utf-8")) == previous_row
    assert list(tmp_path.glob(".atomic-json-snapshot-*.tmp")) == []


def test_atomic_json_snapshot_supports_a_separate_validated_schema(tmp_path):
    # The caller owns this schema validation; the shared primitive only
    # performs the private atomic write and does not invent identity fields.
    identity = {
        "schema": "COMSOL_CONTROL_DAEMON_IDENTITY_V1",
        "pid": 12001,
        "process_start_epoch_ms": 123456,
    }
    path = tmp_path / "control-daemon-identity.json"
    assert write_atomic_json_snapshot(path, identity) == identity
    assert json.loads(path.read_text(encoding="utf-8")) == identity
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(ProcessDiagnosticError, match="safe basename"):
        write_atomic_json_snapshot(tmp_path / "identity.txt", identity)


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
