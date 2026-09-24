#!/usr/bin/env python3
"""Real production-stdio recovery and security driver for T012/T027/T055/T028/T035.

It only signals a verified private control daemon or Java worker.  COMSOL is
never stopped, and all control actions use a fresh MCP ``ClientSession``.  On
Windows the run attaches only to a healthy daemon started outside the MCP SDK
Job; a control restart is launched by this user-controlled harness with the
same private home and environment, never by transport auto-spawn.
"""
from __future__ import annotations

import argparse, asyncio, ctypes, hashlib, json, ntpath, os, re, signal, sqlite3, subprocess, sys, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4

from ctypes import wintypes

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
JDK11_DEFAULT = "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
ACTIVE = {"QUEUED", "STARTING", "RUNNING"}


def _safe(value: Any) -> Any:
    if hasattr(value, "model_dump"): return _safe(value.model_dump(mode="json"))
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            sensitive_key = any(x in str(key).lower() for x in ("token", "password", "credential", "authorization", "prefs"))
            # Assertion fields can legitimately include words such as
            # ``credential_path_denied``.  Preserve their boolean/numeric
            # verdicts while never emitting a sensitive string value.
            redacted[str(key)] = "REDACTED" if sensitive_key and isinstance(item, str) else _redact(item)
        return redacted
    if isinstance(value, list): return [_redact(v) for v in value]
    if isinstance(value, str) and any(part in value for part in (".phase1-private", ".phase2-private", "control-private")):
        return "REDACTED_PATH"
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_redact(_safe(value)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file(): raise RuntimeError(f"unsafe or missing discovery record: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise RuntimeError(f"invalid discovery record: {path}")
    return value


def _control_request(endpoint: dict[str, Any], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Probe a verified local daemon without routing the acceptance through MCP."""
    port = endpoint.get("port")
    token = endpoint.get("token")
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not token:
        raise RuntimeError("invalid private control endpoint")
    request = Request(
        f"http://127.0.0.1:{port}/rpc",
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("private control endpoint returned a non-object")
    return value


def _control_home(private_home: Path) -> Path:
    home = private_home.resolve() / "control-private"
    if home.is_symlink(): raise RuntimeError("control-private must not be a symlink")
    return home


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_path_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value.strip().strip('"').replace("/", "\\")
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def _windows_birth_epoch_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _windows_process_snapshot(pid: int) -> dict[str, Any] | None:
    """Read one Windows process through bounded, non-signalling CIM."""
    if type(pid) is not int or pid <= 1:
        raise RuntimeError("invalid Windows process PID")
    script = (
        "$ErrorActionPreference='Stop';"
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=$OutputEncoding;"
        f"$p=Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId={pid}';"
        "if ($null -eq $p) { exit 3 };"
        "$birth=$null;"
        "if ($null -ne $p.CreationDate) { $birth=$p.CreationDate.ToUniversalTime().ToString('o') };"
        "[pscustomobject]@{"
        "pid=[int]$p.ProcessId;"
        "parent_pid=[int]$p.ParentProcessId;"
        "creation_utc=$birth;"
        "executable_path=[string]$p.ExecutablePath;"
        "command_line=[string]$p.CommandLine"
        "} | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=3.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("bounded Windows process identity query failed") from exc
    output = completed.stdout.strip()
    if completed.returncode == 3 and not output:
        return None
    if completed.returncode != 0 or not output:
        raise RuntimeError("bounded Windows process identity query returned an error")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("bounded Windows process identity query returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("pid") != pid:
        raise RuntimeError("bounded Windows process identity returned a different PID")
    return value


def _windows_command_matches(command_line: Any, fragment: str, home: Path) -> bool:
    if not isinstance(command_line, str) or not command_line.strip():
        return False
    if fragment == "comsol_mcp._control_daemon":
        module_pattern = rf"(?<!\S)-m\s+{re.escape(fragment)}(?!\S)"
        if len(re.findall(module_pattern, command_line)) != 1:
            return False
        option = "--home"
        expected = str(home)
    elif fragment == "comsol_mcp.worker_java.PersistentComsolWorker":
        # The Worker is launched by Java with its main class as a positional
        # token; it does not receive the Python daemon's ``-m`` or ``--home``.
        if len(re.findall(rf"(?<!\S){re.escape(fragment)}(?!\S)", command_line)) != 1:
            return False
        option = "--endpoint-file"
        expected = str(home / "worker_endpoint.json")
    else:
        return False
    values = [quoted or bare for quoted, bare in re.findall(
        rf"(?<!\S){re.escape(option)}\s+(?:\"([^\"]+)\"|([^\s]+))", command_line
    )]
    return len(values) == 1 and _windows_path_key(values[0]) == _windows_path_key(expected)


def _verified_pid(endpoint: dict[str, Any], fragment: str, home: Path, comsol_pid: int) -> dict[str, Any]:
    """Validate PID, user, command and private home immediately before a signal."""
    pid = endpoint.get("pid")
    if type(pid) is not int or pid <= 1 or pid == comsol_pid: raise RuntimeError("invalid or protected signal target")
    if _is_windows():
        snapshot = _windows_process_snapshot(pid)
        if snapshot is None:
            raise RuntimeError("Windows process identity is unavailable")
        recorded_start = endpoint.get("process_start_epoch_ms")
        observed_start = _windows_birth_epoch_ms(snapshot.get("creation_utc"))
        if type(recorded_start) is not int or type(observed_start) is not int or recorded_start != observed_start:
            raise RuntimeError("Windows process creation identity changed or is unavailable")
        if not _windows_command_matches(snapshot.get("command_line"), fragment, home):
            raise RuntimeError("Windows process command/home does not identify managed component")
        executable = snapshot.get("executable_path")
        if not isinstance(executable, str) or not executable.strip():
            raise RuntimeError("Windows process executable identity is unavailable")
        return {
            "pid": pid,
            "command": snapshot.get("command_line"),
            "executable_path": executable,
            "creation_utc": snapshot.get("creation_utc"),
            "process_start_epoch_ms": observed_start,
            "parent_pid": snapshot.get("parent_pid"),
        }
    row = subprocess.check_output(["ps", "-p", str(pid), "-o", "uid=,pid=,command="], text=True).strip().split(maxsplit=2)
    if len(row) != 3 or int(row[0]) != os.getuid() or int(row[1]) != pid: raise RuntimeError("PID ownership changed")
    if fragment not in row[2] or str(home) not in row[2]: raise RuntimeError("PID command/home does not identify managed component")
    return {"pid": pid, "command": row[2]}


def _wait_dead(pid: int, timeout_s: float = 15.0) -> bool:
    until = time.monotonic() + timeout_s
    while time.monotonic() < until:
        try: os.kill(pid, 0)
        except ProcessLookupError: return True
        time.sleep(.05)
    return False


class _WindowsProcessHandle:
    """Terminate one already-verified task-owned process through one handle."""

    _QUERY_LIMITED_INFORMATION = 0x1000
    _TERMINATE = 0x0001
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _STILL_ACTIVE = 259
    _WINDOWS_EPOCH_100NS = 116444736000000000

    def __init__(self, target: dict[str, Any]) -> None:
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise RuntimeError("Windows kernel process API is unavailable") from exc
        k = self.kernel32
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        k.GetProcessTimes.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.TerminateProcess.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.CloseHandle.restype = wintypes.BOOL
        self.target = target
        access = self._QUERY_LIMITED_INFORMATION | self._TERMINATE | self._SYNCHRONIZE
        self.handle = k.OpenProcess(access, False, target["pid"])
        if not self.handle:
            raise RuntimeError("Windows verified process handle could not be opened")
        self.closed = False
        try:
            self._verify_handle_identity()
        except Exception:
            self.close()
            raise

    def _filetime_epoch_ms(self, value: wintypes.FILETIME) -> int:
        ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
        return (ticks - self._WINDOWS_EPOCH_100NS) // 10_000

    def _verify_handle_identity(self) -> None:
        creation = wintypes.FILETIME()
        ignored_exit = wintypes.FILETIME()
        ignored_kernel = wintypes.FILETIME()
        ignored_user = wintypes.FILETIME()
        if not self.kernel32.GetProcessTimes(
            self.handle, ctypes.byref(creation), ctypes.byref(ignored_exit),
            ctypes.byref(ignored_kernel), ctypes.byref(ignored_user)
        ):
            raise RuntimeError("Windows handle creation identity query failed")
        expected_start = self.target.get("process_start_epoch_ms")
        if type(expected_start) is not int or self._filetime_epoch_ms(creation) != expected_start:
            raise RuntimeError("Windows handle birth does not match endpoint identity")
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image_buffer))
        if not self.kernel32.QueryFullProcessImageNameW(
            self.handle, 0, image_buffer, ctypes.byref(image_size)
        ):
            raise RuntimeError("Windows handle executable identity query failed")
        if _windows_path_key(image_buffer.value) != _windows_path_key(self.target.get("executable_path")):
            raise RuntimeError("Windows handle executable identity changed")
        exit_code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(exit_code)):
            raise RuntimeError("Windows handle liveness query failed")
        if exit_code.value != self._STILL_ACTIVE:
            raise RuntimeError("verified Windows process is no longer alive")

    def terminate_and_wait(self, timeout_ms: int = 15_000) -> int:
        if not self.kernel32.TerminateProcess(self.handle, 0):
            raise RuntimeError("verified Windows process termination failed")
        result = int(self.kernel32.WaitForSingleObject(self.handle, timeout_ms))
        if result == self._WAIT_TIMEOUT:
            raise RuntimeError("verified Windows process did not exit before timeout")
        if result != self._WAIT_OBJECT_0:
            raise RuntimeError("verified Windows process wait failed")
        exit_code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(exit_code)):
            raise RuntimeError("verified Windows process exit status query failed")
        if exit_code.value == self._STILL_ACTIVE:
            raise RuntimeError("verified Windows process remains active after wait")
        return int(exit_code.value)

    def close(self) -> None:
        if not self.closed:
            self.kernel32.CloseHandle(self.handle)
            self.closed = True


def _terminate_verified(target: dict[str, Any]) -> None:
    """Signal only the just-validated process; never use a Windows PID fallback."""
    if not _is_windows():
        os.kill(target["pid"], signal.SIGTERM)
        if not _wait_dead(target["pid"]):
            raise RuntimeError("verified process did not terminate")
        return
    handle = _WindowsProcessHandle(target)
    try:
        handle.terminate_and_wait()
    finally:
        handle.close()


def _runtime_environment(args: argparse.Namespace) -> dict[str, str]:
    return {
        **os.environ,
        "COMSOL_ROOT": str(Path(args.comsol_root).resolve()),
        "COMSOL_JAVA_HOME": str(Path(args.jdk11).resolve()),
        "JAVA_HOME": str(Path(args.jdk11).resolve()),
        "COMSOL_PREFS_DIR": str(Path(args.prefs).resolve()),
        "COMSOL_SERVER_MCP_HOME": str(Path(args.private_home).resolve()),
        "PYTHONPATH": str(ROOT),
    }


def _windows_runtime_environment(args: argparse.Namespace) -> dict[str, str]:
    """Use the same resolved environment for external Windows daemon launch."""
    return _runtime_environment(args)


def _runtime_preflight(args: argparse.Namespace, environment: dict[str, str]) -> dict[str, Any]:
    """Record a redacted digest of the exact stdio command and runtime env."""
    command = [str(args.python), "-m", "comsol_mcp.mcp_server"]
    command_bytes = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    selected = {
        key: environment.get(key)
        for key in ("COMSOL_ROOT", "COMSOL_JAVA_HOME", "JAVA_HOME", "COMSOL_PREFS_DIR", "COMSOL_SERVER_MCP_HOME", "PYTHONPATH")
    }
    environment_bytes = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "route": "production stdio MCP ClientSession",
        "command_shape": ["<selected-python>", "-m", "comsol_mcp.mcp_server"],
        "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
        "environment_sha256": hashlib.sha256(environment_bytes).hexdigest(),
        "java_home_match": (
            bool(environment.get("JAVA_HOME"))
            and environment.get("JAVA_HOME") == environment.get("COMSOL_JAVA_HOME")
            and (
                environment.get("JAVA_HOME") == str(args.jdk11)
                or Path(environment.get("JAVA_HOME", "")).resolve() == Path(args.jdk11).resolve()
            )
        ),
        "required_environment_keys_present": all(isinstance(selected[key], str) and bool(selected[key]) for key in selected),
    }


def _selected_python_base_executable(args: argparse.Namespace, environment: dict[str, str]) -> str:
    """Resolve the base interpreter behind a Windows venv redirector."""
    selected = str(Path(args.python).resolve())
    probe_environment = dict(environment)
    probe_environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [selected, "-c", "import os,sys; print(os.path.abspath(sys._base_executable))"],
            cwd=str(ROOT),
            env=probe_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("selected Windows Python could not report its base executable") from exc
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) != 1 or not ntpath.isabs(lines[0]):
        raise RuntimeError("selected Windows Python returned an unusable base executable")
    return ntpath.normpath(lines[0])


def _windows_external_control_command(args: argparse.Namespace, control_home: Path) -> list[str]:
    """Build the exact external-daemon argv; callers pass it to Popen as a list."""
    return [
        str(Path(args.python).resolve()),
        "-m",
        "comsol_mcp._control_daemon",
        "--home",
        str(control_home),
    ]


def _validate_windows_external_control_pair(
    process: subprocess.Popen,
    endpoint: dict[str, Any],
    control_home: Path,
    base_executable: str,
    comsol_pid: int,
) -> dict[str, Any]:
    """Validate the Popen launcher and the daemon it directly owns."""
    launcher_pid = getattr(process, "pid", None)
    if type(launcher_pid) is not int or launcher_pid <= 1 or launcher_pid == comsol_pid:
        raise RuntimeError("Windows external control launcher identity is unavailable")
    if process.poll() is not None:
        raise RuntimeError("Windows external control launcher is no longer alive")
    launcher = _windows_process_snapshot(launcher_pid)
    if launcher is None:
        raise RuntimeError("Windows external control launcher identity is unavailable")
    daemon = _windows_process_snapshot(endpoint.get("pid"))
    if daemon is None:
        raise RuntimeError("Windows external control daemon identity is unavailable")
    target = _verified_pid(endpoint, "comsol_mcp._control_daemon", control_home, comsol_pid)
    if daemon.get("pid") != endpoint.get("pid") or launcher.get("pid") != launcher_pid:
        raise RuntimeError("Windows external control CIM identity returned a different PID")
    launcher_birth = _windows_birth_epoch_ms(launcher.get("creation_utc"))
    daemon_birth = _windows_birth_epoch_ms(daemon.get("creation_utc"))
    if type(launcher_birth) is not int or type(daemon_birth) is not int:
        raise RuntimeError("Windows external control creation identity is unavailable")
    if daemon_birth < launcher_birth:
        raise RuntimeError("Windows external control daemon birth precedes its launcher birth")
    if endpoint.get("pid") != launcher_pid and daemon.get("parent_pid") != launcher_pid:
        raise RuntimeError("Windows external control daemon is not the Popen direct child")
    executable = daemon.get("executable_path")
    if not isinstance(executable, str) or _windows_path_key(executable) != _windows_path_key(base_executable):
        raise RuntimeError("Windows external control daemon is not the selected Python base executable")
    command_line = daemon.get("command_line")
    if not _windows_command_matches(command_line, "comsol_mcp._control_daemon", control_home):
        raise RuntimeError("Windows external control daemon command/home is not exact")
    if target.get("process_start_epoch_ms") != daemon_birth:
        raise RuntimeError("Windows external control endpoint birth does not match CIM")
    return {
        "launcher_pid": launcher_pid,
        "launcher_process_start_epoch_ms": launcher_birth,
        "daemon_pid": endpoint.get("pid"),
        "daemon_process_start_epoch_ms": daemon_birth,
        "daemon_parent_pid": daemon.get("parent_pid"),
        "windows_redirector": endpoint.get("pid") != launcher_pid,
        "popen_handle_held": True,
        "launcher_alive": True,
        "daemon_alive": True,
        "base_executable_match": True,
        "command_match": True,
        "daemon_command_sha256": hashlib.sha256(str(command_line).encode("utf-8")).hexdigest(),
    }


def _start_windows_external_control(
    args: argparse.Namespace,
    control_home: Path,
    previous_pid: int,
    run_dir: Path,
    label: str,
) -> dict[str, Any]:
    """Restart the same durable control home outside the MCP stdio process.

    The recovery driver itself is the user-controlled harness.  It starts a
    replacement only after the old endpoint was terminated through the
    verified handle above; the MCP SDK never gets a second spawn opportunity.
    The returned Popen object is retained in evidence only so the harness can
    reconcile its lifecycle later.  No service, scheduled task, or COMSOL
    process is created here.
    """
    command = _windows_external_control_command(args, control_home)
    log_path = control_home / f"{label}.control.log"
    endpoint_path = control_home / "control.json"
    environment = _windows_runtime_environment(args)
    base_executable = _selected_python_base_executable(args, environment)
    process = None
    endpoint: dict[str, Any] | None = None

    def record_failure(reason: str, endpoint: dict[str, Any] | None = None) -> None:
        process_alive = process is not None and process.poll() is None
        record = {
            "status": "BLOCKED",
            "label": label,
            "launcher_pid": process.pid if process is not None else None,
            "launcher_alive": process_alive,
            "daemon_pid": endpoint.get("pid") if isinstance(endpoint, dict) else None,
            "base_executable_sha256": hashlib.sha256(base_executable.encode("utf-8")).hexdigest(),
            "cleanup_required": bool(process_alive),
            "owner": "this user-controlled recovery harness",
            "reason": reason,
        }
        try:
            if process is not None:
                launcher = _windows_process_snapshot(process.pid)
                if launcher is not None:
                    record["launcher_process_start_epoch_ms"] = _windows_birth_epoch_ms(launcher.get("creation_utc"))
            if isinstance(endpoint, dict) and type(endpoint.get("pid")) is int:
                daemon = _windows_process_snapshot(endpoint["pid"])
                if daemon is not None:
                    record["daemon_process_start_epoch_ms"] = _windows_birth_epoch_ms(daemon.get("creation_utc"))
                    record["daemon_parent_pid"] = daemon.get("parent_pid")
        except Exception:
            record["identity_readback"] = "UNAVAILABLE"
        try:
            _write(run_dir / f"{label}.external-control-failure.json", record)
        except Exception:
            # Evidence failure is secondary; preserve the launch/health error.
            pass

    try:
        log = log_path.open("ab")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=str(ROOT),
        )
        owned_processes = getattr(args, "_windows_external_processes", None)
        if not isinstance(owned_processes, list):
            owned_processes = []
            setattr(args, "_windows_external_processes", owned_processes)
        owned_processes.append(process)
    except OSError as exc:
        try: log.close()
        except Exception: pass
        record_failure(f"launch failed: {type(exc).__name__}")
        raise RuntimeError("Windows external control restart could not be started") from exc
    finally:
        try: log.close()
        except Exception: pass

    deadline = time.monotonic() + 15.0
    last_reason = "endpoint not ready"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            record_failure("launcher exited before endpoint readiness")
            raise RuntimeError("Windows external control restart exited before endpoint readiness")
        if endpoint_path.is_file() and not endpoint_path.is_symlink():
            try:
                endpoint = _read_json(endpoint_path)
                identity = _validate_windows_external_control_pair(
                    process, endpoint, control_home, base_executable, args.comsol_pid,
                )
                if identity["daemon_pid"] == previous_pid:
                    last_reason = "endpoint still names the terminated daemon"
                    time.sleep(0.05)
                    continue
                health = _control_request(
                    endpoint,
                    {"operation": "session_health", "arguments": {}, "execution": {}},
                    0.5,
                )
                if health.get("success"):
                    safe = {
                        **identity,
                        "label": label,
                        "private_home_match": True,
                        "health_success": True,
                        "owner": "this user-controlled recovery harness",
                    }
                    _write(run_dir / f"{label}.external-control.json", safe)
                    return {"process": process, "endpoint": endpoint, "target": identity, "safe": safe}
                last_reason = "replacement endpoint failed session_health"
            except (OSError, RuntimeError, json.JSONDecodeError):
                last_reason = "replacement endpoint identity or health was not yet verified"
        time.sleep(0.05)
    record_failure(last_reason, endpoint if isinstance(endpoint, dict) else None)
    raise RuntimeError("Windows external control restart did not publish a fresh verified endpoint")


def _require_windows_external_control(args: argparse.Namespace) -> dict[str, Any]:
    """Require an already-running user-controlled daemon before stdio attach.

    The MCP SDK may place an auto-spawned child in its Windows Job Object.  A
    recovery run must therefore attach an endpoint started outside that
    transport and fail closed when its identity cannot be proven.
    """
    control_home = _control_home(args.private_home)
    endpoint_path = control_home / "control.json"
    try:
        endpoint = _read_json(endpoint_path)
        target = _verified_pid(endpoint, "comsol_mcp._control_daemon", control_home, args.comsol_pid)
        selected_base_executable = _selected_python_base_executable(
            args, _windows_runtime_environment(args)
        )
        if _windows_path_key(target.get("executable_path")) != _windows_path_key(selected_base_executable):
            raise RuntimeError("prestarted external control daemon is not the selected Python base executable")
        health = _control_request(
            endpoint,
            {"operation": "session_health", "arguments": {}, "execution": {}},
            1.0,
        )
        if health.get("success") is not True:
            raise RuntimeError("prestarted external control daemon did not pass session_health")
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Windows recovery requires a user-controlled prestarted external "
            "control daemon outside the MCP stdio Job; start `python -m "
            "comsol_mcp._control_daemon --home PATH` from the external harness "
            "and retry after its control.json identity is healthy"
        ) from exc
    return {
        "daemon_pid": target["pid"],
        "daemon_process_start_epoch_ms": target.get("process_start_epoch_ms"),
        "command_match": True,
        "private_home_match": True,
        "base_executable_match": True,
        "identity_verified": True,
        "health_success": True,
        "owner_scope": "user-controlled external daemon; no SDK auto-spawn",
    }


def _job_row(home: Path, job_id: str) -> dict[str, Any]:
    database = home / "operations.sqlite3"
    if database.is_symlink() or not database.is_file(): raise RuntimeError("durable job ledger unavailable")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT jobs.job_id,jobs.operation_id,jobs.status,jobs.metadata,"
            "jobs.created_at,jobs.started_at,jobs.finished_at,operations.result "
            "FROM jobs JOIN operations ON operations.operation_id=jobs.operation_id "
            "WHERE jobs.job_id=?",
            (job_id,),
        ).fetchone()
        count = db.execute("SELECT count(*) FROM operations WHERE operation_id=(SELECT operation_id FROM jobs WHERE job_id=?)", (job_id,)).fetchone()[0]
    if row is None: raise RuntimeError("job absent from durable ledger")
    return {**dict(row), "operation_count": count}


def _status(payload: dict[str, Any]) -> str | None:
    value = payload.get("data", {})
    return value.get("status") if isinstance(value, dict) and isinstance(value.get("status"), str) else None


_T028_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
_T028_UNCERTAIN = {"UNKNOWN", "RECONCILING", "LOST"}


def _t028_recovery_scope(job_state_at_fault: str | None) -> str:
    """Describe T028 evidence without inferring a missing job state."""
    if job_state_at_fault in ACTIVE:
        return "active_job_recovery"
    if job_state_at_fault in _T028_TERMINAL:
        return "terminal_job_only"
    if job_state_at_fault in _T028_UNCERTAIN:
        return "unknown_or_lost_job"
    return "job_state_unavailable"


def _same_job(payload: dict[str, Any], job_id: str) -> bool:
    return isinstance(payload.get("data"), dict) and payload["data"].get("job_id") == job_id


def _worker_identity(health: dict[str, Any]) -> dict[str, Any] | None:
    session = health.get("data", {}).get("session", {}) if isinstance(health.get("data"), dict) else {}
    worker = session.get("worker") if isinstance(session, dict) else None
    return {k: worker.get(k) for k in ("instance_id", "generation", "pid", "port")} if isinstance(worker, dict) else None


def _model_ref(path: Path) -> dict[str, Any]:
    """Accept either the raw ref or the execution envelope saved by a live run."""
    value = _read_json(path)
    if isinstance(value.get("model_ref"), dict): return value["model_ref"]
    execution = value.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("model_ref"), dict): return execution["model_ref"]
    required = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
    if required <= value.keys(): return value
    raise RuntimeError("model-ref-json contains no model_ref")


def _execution_identity(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    execution = payload.get("execution") if isinstance(payload, dict) else None
    if not isinstance(execution, dict):
        return None, None
    ref, revision = execution.get("model_ref"), execution.get("revision")
    return (ref if isinstance(ref, dict) else None, revision if isinstance(revision, int) and not isinstance(revision, bool) else None)


class Host:
    def __init__(self, params, log, transcript, run_dir, label): self.params, self.log, self.transcript, self.run_dir, self.label = params, log, transcript, run_dir, label
    async def __aenter__(self):
        self.transport = stdio_client(self.params, errlog=self.log); reader, writer = await self.transport.__aenter__()
        self.context = ClientSession(reader, writer, read_timeout_seconds=timedelta(minutes=10)); self.session = await self.context.__aenter__()
        self.transcript.append({"host": self.label, "operation": "initialize", "response": _safe(await self.session.initialize())})
        return self
    async def __aexit__(self, *exc):
        await self.context.__aexit__(*exc); await self.transport.__aexit__(*exc)
    async def call(self, operation: str, arguments: dict | None = None) -> dict:
        started = time.monotonic(); response = await self.session.call_tool(operation, arguments or {}); elapsed = time.monotonic() - started
        try: payload = json.loads(response.content[0].text)
        except Exception: payload = {"success": False, "error": {"code": "NON_JSON_MCP_RESPONSE"}, "raw": _safe(response)}
        # Keep the actual control-plane sample window free of repeated whole-
        # transcript serialization; ``run`` writes this durable evidence once
        # in its finally block after the live operation has been observed.
        self.transcript.append({"host": self.label, "operation": operation, "arguments": arguments or {}, "elapsed_s": elapsed, "outer_isError": bool(response.isError), "payload": payload})
        return {**payload, "_outer_isError": bool(response.isError), "_elapsed_s": elapsed}


async def _query(host: Host, job_id: str) -> dict:
    return {"status": await host.call("job_status", {"job_id": job_id}), "log": await host.call("job_log", {"job_id": job_id, "offset": 0, "limit": 1000}), "result": await host.call("job_result", {"job_id": job_id})}


async def _connect(host: Host, args, prefix: str) -> dict:
    key = prefix + "-" + uuid4().hex
    return await host.call("server_connect", {"host": "127.0.0.1", "port": args.port, "execution": {"idempotency_key": key, "request_id": key}})


async def _terminal(host: Host, job_id: str, wait_s: float) -> dict:
    observed = await _query(host, job_id); deadline = time.monotonic() + wait_s
    while _status(observed["status"]) in ACTIVE and time.monotonic() < deadline:
        await asyncio.sleep(.25)  # actual job polling; never a fake solve delay
        observed = await _query(host, job_id)
    return observed


def _worker_request_submitted(observed: dict) -> bool:
    events = observed.get("log", {}).get("data", {}).get("events", [])
    submitted_run_ids = set()
    observed_ids = set()
    for event in events:
        metadata = event.get("metadata")
        if event.get("event") != "worker_request" or not isinstance(metadata, dict):
            continue
        request_id = metadata.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            continue
        if metadata.get("phase") == "observed":
            observed_ids.add(request_id)
            continue
        payload = metadata.get("metadata")
        if (
            metadata.get("phase") == "submitted"
            and metadata.get("kind") == "call"
            and isinstance(payload, dict)
            and payload.get("method") == "run"
        ):
            submitted_run_ids.add(request_id)
    # A worker `call/run` returns only after COMSOL has answered.  Its submitted
    # event without the matching observed event is therefore the evidence that
    # a solver API call, rather than model preflight, remains pending.
    return bool(submitted_run_ids - observed_ids)


def _result_success(observed: dict) -> bool:
    job = observed.get("result", {}).get("data", {})
    result = job.get("result") if isinstance(job, dict) else None
    return isinstance(result, dict) and result.get("success") is True


def _events(observed: dict, event_name: str | None = None) -> list[dict[str, Any]]:
    value = observed.get("log", {}).get("data", {}).get("events", [])
    if not isinstance(value, list):
        return []
    return [event for event in value if isinstance(event, dict) and (event_name is None or event.get("event") == event_name)]


def _has_worker_submission(observed: dict) -> bool:
    return any(
        event.get("event") == "worker_request"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("phase") == "submitted"
        for event in _events(observed)
    )


def _p95(samples: list[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)]


async def _sample_pending_control_plane(host: Host, job_id: str, *, minimum: int, deadline: float) -> tuple[list[dict[str, float]], dict]:
    """Collect only observations bracketed by a real pending worker `run` call."""
    samples: list[dict[str, float]] = []
    latest = await _query(host, job_id)
    while len(samples) < minimum and time.monotonic() < deadline:
        if _status(latest["status"]) != "RUNNING" or not _worker_request_submitted(latest):
            break
        health = await host.call("session_health")
        status = await host.call("job_status", {"job_id": job_id})
        log = await host.call("job_log", {"job_id": job_id, "offset": 0, "limit": 1000})
        # Do not count a response that was collected after the real solver call
        # completed; this keeps the T012 evidence tied to an actual pending solve.
        latest = {"status": status, "log": log, "result": latest["result"]}
        if _status(status) == "RUNNING" and _worker_request_submitted(latest):
            samples.append({"health_s": health["_elapsed_s"], "status_s": status["_elapsed_s"], "log_s": log["_elapsed_s"]})
            continue
        break
    return samples, latest


async def host_disconnect(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    ref = _model_ref(args.model_ref_json) if args.model_ref_json is not None else None
    revision = args.revision
    async with Host(params, log, transcript, args.run_dir, "submitting-host") as submitting:
        if args.model_path is not None:
            setup_key = "t012-load-" + args.idempotency_key
            connected = await _connect(submitting, args, "t012-load-connect")
            loaded = await submitting.call("model_load", {"path": str(args.model_path), "execution": {
                "idempotency_key": setup_key, "request_id": setup_key,
            }})
            ref, revision = _execution_identity(loaded)
            if ref is None:
                tag = (loaded.get("data") or {}).get("tag")
                if isinstance(tag, str) and tag:
                    adopted = await submitting.call("model_adopt", {"model_tag": tag})
                    ref, revision = _execution_identity(adopted)
                else:
                    adopted = None
            else:
                adopted = None
            evidence.update(setup_connect=connected, setup_load=loaded, setup_adopt=adopted)
        if ref is None or revision is None:
            assertions["production_fixture_load"] = False
            return "BLOCKED"
        assertions["production_fixture_load"] = True
        execution = {
            "model_ref": ref,
            "session_id": ref["session_id"],
            "expected_revision": revision,
            "idempotency_key": args.idempotency_key,
            "request_id": "t027-" + args.idempotency_key,
            "rpc_timeout_s": 0,
            "queue_timeout_s": None,
            "execution_timeout_s": args.execution_timeout_s,
            "no_progress_warning_s": args.no_progress_warning_s,
        }
        submitted_arguments = {"study_tag": args.study_tag, "execution": execution}
        submitted = await submitting.call("run_study", submitted_arguments)
        job_id = ((submitted.get("data") or {}).get("job_id") or (submitted.get("execution") or {}).get("job_id"))
        if not isinstance(job_id, str) or not job_id:
            evidence["submission"] = submitted
            assertions["solve_job_created"] = False
            return "BLOCKED"
        deadline = time.monotonic() + args.running_window_seconds
        before = await _query(submitting, job_id)
        while time.monotonic() < deadline and not (
            _status(before["status"]) == "RUNNING" and _worker_request_submitted(before)
        ):
            if _status(before["status"]) not in ACTIVE:
                break
            await asyncio.sleep(.1)
            before = await _query(submitting, job_id)
        if _status(before["status"]) == "RUNNING" and _worker_request_submitted(before):
            inspect_key = "t012-inspect-" + args.idempotency_key
            inspect = await submitting.call("model_inspect", {"refresh": False, "execution": {
                "model_ref": ref,
                "session_id": ref["session_id"],
                "idempotency_key": inspect_key,
                "request_id": inspect_key,
                "rpc_timeout_s": 0,
                "queue_timeout_s": None,
                "execution_timeout_s": None,
                "no_progress_warning_s": None,
            }})
            inspect_job_id = ((inspect.get("data") or {}).get("job_id") or (inspect.get("execution") or {}).get("job_id"))
            inspect_before = await _query(submitting, inspect_job_id) if isinstance(inspect_job_id, str) else {}
            samples, before = await _sample_pending_control_plane(
                submitting, job_id, minimum=args.control_plane_samples, deadline=deadline)
        else:
            inspect, inspect_job_id, inspect_before, samples = None, None, {}, []
    # The submitting stdio host has now disconnected during its own real solve.
    assertions.update(
        solve_job_created=True,
        real_solve_running_at_disconnect=_status(before["status"]) == "RUNNING",
        worker_request_submitted_before_disconnect=_worker_request_submitted(before),
        control_plane_sample_count=len(samples) >= args.control_plane_samples,
        control_plane_p95_under_one_second=all(
            (_p95([sample[key] for sample in samples]) or float("inf")) < 1.0
            for key in ("health_s", "status_s", "log_s")),
        inspect_job_created=isinstance(inspect_job_id, str),
        inspect_queued_behind_solver=_status(inspect_before.get("status", {})) == "QUEUED",
        inspect_has_no_java_submission_while_solver_pending=not _has_worker_submission(inspect_before),
    )
    if args.execution_timeout_s is None:
        assertions["null_execution_timeout_recorded"] = execution["execution_timeout_s"] is None
    else:
        assertions.update(
            execution_deadline_warned_while_engine_running=any(
                event.get("event") == "ExecutionDeadlineExceeded" for event in _events(before)),
            execution_deadline_never_cancelled=_status(before["status"]) == "RUNNING",
        )
    if args.no_progress_warning_s is not None:
        assertions["no_progress_warning_recorded_while_engine_running"] = any(
            event.get("event") == "NoProgressWarning" for event in _events(before)) and _status(before["status"]) == "RUNNING"
    durable_before = _job_row(control, job_id)
    if not all(assertions.values()):
        evidence.update(submission=submitted, inspect=inspect, inspect_before=inspect_before, control_plane_samples=samples,
                        before_disconnect=before, durable_before=durable_before,
                        effective_execution=execution,
                        blocked_reason="fixture did not expose the required real pending-solver acceptance window")
        return "BLOCKED"
    async with Host(params, log, transcript, args.run_dir, "fresh-host") as fresh:
        # Exact operation/body/key retry must reuse the existing durable job.
        same_key = await fresh.call("run_study", submitted_arguments)
        after = await _terminal(fresh, job_id, args.wait_seconds)
        inspect_after = await _terminal(fresh, inspect_job_id, args.wait_seconds)
    durable_after = _job_row(control, job_id)
    terminal_status = _status(after["status"])
    assertions.update(
        fresh_host_reuses_original_job=(
            ((same_key.get("data") or {}).get("job_id") or (same_key.get("execution") or {}).get("job_id")) == job_id
        ),
        fresh_host_queries_original_job=_same_job(after["status"], job_id),
        job_operation_id_preserved=durable_before["operation_id"] == durable_after["operation_id"],
        single_durable_operation=durable_before["operation_count"] == durable_after["operation_count"] == 1,
        original_result_success=terminal_status == "SUCCEEDED" and _result_success(after),
        inspect_runs_only_after_solver_finished=(
            _status(inspect_after["status"]) == "SUCCEEDED"
            and _has_worker_submission(inspect_after)
            and terminal_status == "SUCCEEDED"
        ),
    )
    evidence.update(submission=submitted, inspect=inspect, inspect_before=inspect_before, inspect_after=inspect_after,
                    control_plane_samples=samples,
                    control_plane_p95_s={key: _p95([sample[key] for sample in samples]) for key in ("health_s", "status_s", "log_s")},
                    effective_execution=execution, same_key_retry=same_key, before_disconnect=before,
                    after_fresh_host=after, durable_before=durable_before, durable_after=durable_after,
                    job_id=job_id)
    if terminal_status in ACTIVE or terminal_status in {"UNKNOWN", "RECONCILING", None}:
        evidence["blocked_reason"] = "original job has no successful terminal result; no replay was performed"
        return "BLOCKED"
    return "PASS" if all(assertions.values()) else "FAIL"


async def control_restart(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    async with Host(params, log, transcript, args.run_dir, "control-before") as first:
        health_before, job_before = await first.call("session_health"), await _query(first, args.job_id)
    target = _verified_pid(_read_json(control / "control.json"), "comsol_mcp._control_daemon", control, args.comsol_pid)
    _terminate_verified(target)
    replacement = _start_windows_external_control(args, control, target["pid"], args.run_dir, "control-restart") if _is_windows() else None
    async with Host(params, log, transcript, args.run_dir, "control-after") as fresh:
        started = await fresh.call("session_health"); connected = await _connect(fresh, args, "recovery-control-connect"); health_after = await fresh.call("session_health"); job_after = await _query(fresh, args.job_id)
        reconciled = await fresh.call("job_reconcile", {"job_id": args.job_id}) if _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"} else None
    endpoint_after = _read_json(control / "control.json")
    job_state_at_fault = _status(job_before["status"])
    assertions.update(control_pid_replaced=endpoint_after.get("pid") != target["pid"], private_control_restarted=started.get("success") is True, reconnected_existing_server=bool(connected.get("success")), same_job_after_restart=_same_job(job_after["status"], args.job_id), sameworker_restart_identity_preserved=_worker_identity(health_before) is not None and _worker_identity(health_before) == _worker_identity(health_after), reconcile_never_replays=reconciled is None or reconciled.get("data", {}).get("metadata", {}).get("replay_performed") is False)
    evidence.update(control_target=target, control_after_pid=endpoint_after.get("pid"), external_control_restart=(replacement or {}).get("safe") if replacement else None, health_before=health_before, health_after=health_after, job_before=job_before, job_after=job_after, reconciled=reconciled, job_state_at_fault=job_state_at_fault, recovery_scope=_t028_recovery_scope(job_state_at_fault))
    return "PASS" if all(assertions.values()) else "FAIL"


async def worker_replace(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    async with Host(params, log, transcript, args.run_dir, "worker-before") as first: health_before, job_before = await first.call("session_health"), await _query(first, args.job_id)
    worker_path = control / "worker" / "worker_endpoint.json"; old_worker = _read_json(worker_path)
    worker_target = _verified_pid(old_worker, "comsol_mcp.worker_java.PersistentComsolWorker", control / "worker", args.comsol_pid)
    old_ref = _model_ref(args.model_ref_json)
    _terminate_verified(worker_target)
    # Restarting the verified daemon after worker loss causes normal private
    # rendezvous recovery; it is not a COMSOL lifecycle operation.
    control_target = _verified_pid(_read_json(control / "control.json"), "comsol_mcp._control_daemon", control, args.comsol_pid)
    _terminate_verified(control_target)
    replacement = _start_windows_external_control(args, control, control_target["pid"], args.run_dir, "worker-replace") if _is_windows() else None
    async with Host(params, log, transcript, args.run_dir, "worker-after") as fresh:
        connected = await _connect(fresh, args, "recovery-worker-connect"); health_after = await fresh.call("session_health")
        key = "recovery-stale-ref-" + uuid4().hex
        stale = await fresh.call("model_inspect", {"execution": {"model_ref": old_ref, "idempotency_key": key, "request_id": key}})
        job_after = await _query(fresh, args.job_id); reconciled = await fresh.call("job_reconcile", {"job_id": args.job_id}) if _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"} else None
    new_worker = _read_json(worker_path); error = stale.get("error", {}) if isinstance(stale.get("error"), dict) else {}
    job_state_at_fault = _status(job_before["status"])
    assertions.update(reconnected_existing_server=bool(connected.get("success")), worker_pid_replaced=new_worker.get("pid") != worker_target["pid"], worker_generation_advanced=type(old_worker.get("generation")) is int and type(new_worker.get("generation")) is int and new_worker["generation"] > old_worker["generation"], old_model_ref_rejected=not stale.get("success") and stale.get("_outer_isError") and error.get("code") == "MODEL_IDENTITY_MISMATCH", same_job_after_worker_replacement=_same_job(job_after["status"], args.job_id), unfinished_job_marked_unknown_or_reconciling=_status(job_before["status"]) not in ACTIVE or _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"}, reconcile_never_replays=reconciled is None or reconciled.get("data", {}).get("metadata", {}).get("replay_performed") is False)
    evidence.update(worker_target=worker_target, worker_before=old_worker, worker_after=new_worker, control_target=control_target, external_control_restart=(replacement or {}).get("safe") if replacement else None, health_before=health_before, health_after=health_after, job_before=job_before, job_after=job_after, stale_model_ref=stale, reconciled=reconciled, job_state_at_fault=job_state_at_fault, recovery_scope=_t028_recovery_scope(job_state_at_fault))
    return "PASS" if all(assertions.values()) else "FAIL"


async def path_security(args, params, evidence, assertions, transcript, log) -> str:
    link = args.run_dir / "symlink-escape.mph"
    if link.exists() or link.is_symlink(): raise RuntimeError("refuse pre-existing path-security fixture")
    outside = Path("/private/tmp") / f"phase2-recovery-outside-{uuid4().hex}.mph"; link.symlink_to(outside)
    requests = {"outside_absolute": str(outside), "parent_escape": "../outside.mph", "private_credential_path": str(args.private_home / ".phase1-private" / "credential.mph"), "symlink_escape": str(link)}
    try:
        async with Host(params, log, transcript, args.run_dir, "path-security") as host:
            connected = await _connect(host, args, "t035-connect")
            if not connected.get("success"):
                evidence["connect_before_path_checks"] = connected
                return "BLOCKED"
            replies = {}
            for name, path in requests.items():
                key = "t035-" + name + "-" + uuid4().hex
                replies[name] = await host.call("model_load", {"path": path, "execution": {"idempotency_key": key, "request_id": key}})
        for name, reply in replies.items():
            error = reply.get("error", {}) if isinstance(reply.get("error"), dict) else {}
            assertions[name + "_denied"] = not reply.get("success") and reply.get("_outer_isError") and error.get("code") == "PERMISSION_DENIED"
        assertions["credential_content_not_logged"] = "credential.mph" not in json.dumps(_redact(replies), ensure_ascii=False)
        evidence.update(path_requests={name: "REDACTED" for name in requests}, path_replies=replies)
        return "PASS" if all(assertions.values()) else "FAIL"
    finally:
        if link.is_symlink(): link.unlink()


async def run(args) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"); args.private_home = args.private_home.resolve()
    args.run_dir = Path(args.run_dir).resolve() if args.run_dir else ROOT / "evidence/phase2/recovery" / stamp; args.run_dir.mkdir(parents=True, exist_ok=False)
    cases = {"host-disconnect": "T012/T027/T055", "control-restart": "T028", "worker-replace": "T028", "path-security": "T035"}; transcript, assertions = [], {}; evidence = {"mode": args.mode, "started_at": time.time()}; result = {"case": cases[args.mode], "status": "NOT_RUN"}
    _write(args.run_dir / "request.json", {"mode": args.mode, "job_id": args.job_id, "model_ref_json": str(args.model_ref_json), "model_path": str(args.model_path) if args.model_path else None, "route": "production stdio MCP ClientSession", "fault_policy": "verified private control/worker only", "control_plane_samples": args.control_plane_samples, "execution_timeout_s": args.execution_timeout_s, "no_progress_warning_s": args.no_progress_warning_s})
    _write(args.run_dir / "environment.json", {"os": sys.platform, "python": sys.version, "comsol_root": args.comsol_root, "jdk11": args.jdk11, "server": {"pid": args.comsol_pid, "port": args.port}, "private_home": "REDACTED"})
    env = _runtime_environment(args); params = StdioServerParameters(command=args.python, args=["-m", "comsol_mcp.mcp_server"], env=env, cwd=str(ROOT))
    try:
        evidence["runtime_preflight"] = _runtime_preflight(args, env)
        _write(args.run_dir / "runtime-preflight.json", evidence["runtime_preflight"])
        if _is_windows():
            evidence["external_control_preflight"] = _require_windows_external_control(args)
            _write(args.run_dir / "windows-external-control-preflight.json", evidence["external_control_preflight"])
        with (args.run_dir / "engine.log").open("w", encoding="utf-8") as log:
            result["status"] = await {"host-disconnect": host_disconnect, "control-restart": control_restart, "worker-replace": worker_replace, "path-security": path_security}[args.mode](args, params, evidence, assertions, transcript, log)
    except Exception as exc: result.update(status="FAIL", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        evidence["finished_at"] = time.time(); _write(args.run_dir / "transcript.json", transcript); _write(args.run_dir / "assertions.json", assertions); _write(args.run_dir / "result.json", {**result, "evidence": evidence}); _write(args.run_dir / "SHA256SUMS.json", {p.name: _sha256(p) for p in args.run_dir.iterdir() if p.is_file()})
    print(json.dumps({"run_dir": str(args.run_dir), "case": result["case"], "status": result["status"]}, ensure_ascii=False)); return 0 if result["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("host-disconnect", "control-restart", "worker-replace", "path-security"), required=True)
    parser.add_argument("--private-home", type=Path, required=True)
    parser.add_argument("--prefs", required=True)
    parser.add_argument("--job-id", help="Existing job for T028; T027 creates its own solve job.")
    parser.add_argument("--idempotency-key", help="T027 solve key, reused verbatim from the fresh host.")
    parser.add_argument("--model-ref-json", type=Path)
    parser.add_argument("--model-path", type=Path,
                        help="Load this isolated MPH through the submitting production MCP host before T012/T027/T055.")
    parser.add_argument("--revision", type=int, help="Expected revision for the T027 run_study submission.")
    parser.add_argument("--study-tag", default="std1")
    parser.add_argument("--running-window-seconds", type=float, default=30.0)
    parser.add_argument("--control-plane-samples", type=int, default=20)
    parser.add_argument("--execution-timeout-s", type=float, default=None,
                        help="Optional T055 execution deadline; it records a warning and never cancels COMSOL.")
    parser.add_argument("--no-progress-warning-s", type=float, default=None,
                        help="Optional no-progress warning budget; null leaves it disabled.")
    parser.add_argument("--comsol-pid", type=int, default=84749)
    parser.add_argument("--port", type=int, default=56388)
    parser.add_argument("--comsol-root", default="/Applications/COMSOL64/Multiphysics")
    parser.add_argument("--jdk11", default=JDK11_DEFAULT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    parser.add_argument("--run-dir")
    args = parser.parse_args()
    if (args.wait_seconds < 0 or args.running_window_seconds < 0 or args.control_plane_samples < 20
            or any(value is not None and value < 0 for value in (args.execution_timeout_s, args.no_progress_warning_s))):
        parser.error("durations must be non-negative and --control-plane-samples must be at least 20")
    if args.mode == "host-disconnect" and (not args.idempotency_key or (args.model_ref_json is None and args.model_path is None) or (args.model_path is None and args.revision is None)):
        parser.error("host-disconnect requires --idempotency-key and either --model-path or --model-ref-json with --revision")
    if args.mode in {"control-restart", "worker-replace"} and not args.job_id:
        parser.error(f"{args.mode} requires --job-id")
    if args.mode == "worker-replace" and args.model_ref_json is None:
        parser.error("worker-replace requires --model-ref-json")
    return asyncio.run(run(args))


if __name__ == "__main__": raise SystemExit(main())
