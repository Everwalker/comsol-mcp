"""Authenticated local transport to the persistent control service.

The MCP stdio process can disappear without owning the lifetime of a solve.
No transport error triggers a second submission or terminates an engine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from ._platform_process import process_identity


class ControlStartupError(RuntimeError):
    """The private control daemon could not be started safely."""

    def __init__(self, message: str, *, action: str | None = None) -> None:
        super().__init__(message)
        self.action = action


# Windows MCP stdio hosts are placed in an SDK-created Job Object whose close
# policy terminates descendants.  A control daemon started from that host must
# request all three creation flags together or it is still owned by the Job.
# Keep the numeric fallbacks importable on non-Windows hosts so the policy is
# unit-testable without pretending that this process is Windows.
_WINDOWS_CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
_WINDOWS_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _is_windows() -> bool:
    return os.name == "nt"


def control_home() -> Path:
    from comsol_mcp._server import COMSOL_SERVER_MCP_HOME
    home = COMSOL_SERVER_MCP_HOME.resolve() / "control-private"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if home.is_symlink():
        raise RuntimeError("Control directory must not be a symlink")
    if hasattr(os, "getuid") and home.stat().st_uid != os.getuid():
        raise RuntimeError("Control directory is owned by another user")
    os.chmod(home, 0o700)
    return home


def _request(endpoint: dict, payload: dict, timeout: float) -> dict:
    port = endpoint.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("Invalid control endpoint")
    # Never accept a hostname supplied by a writable discovery file.
    request = Request(
        f"http://127.0.0.1:{port}/rpc",
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + endpoint["token"]},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _read_endpoint(home: Path) -> dict | None:
    path = home / "control.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise RuntimeError("Control endpoint must not be a symlink")
    return json.loads(path.read_text(encoding="utf-8"))


def _alive(pid: int, expected_start_epoch_ms: int | None = None) -> bool:
    """Conservatively test a private daemon without signalling Windows PIDs."""
    identity = process_identity(pid)
    observed_start = identity["start_epoch_ms"]
    if isinstance(expected_start_epoch_ms, int) and isinstance(observed_start, int) and observed_start != expected_start_epoch_ms:
        return False
    return identity["alive"]


def _spawn_control_daemon(home: Path, stream):
    """Start exactly one detached control daemon with platform-safe ownership.

    On Windows this call is deliberately fail-closed.  Retrying without
    ``CREATE_BREAKAWAY_FROM_JOB`` would recreate the SDK Job Object failure
    that kills the daemon when the MCP stdio transport closes.
    """
    command = [sys.executable, "-m", "comsol_mcp._control_daemon", "--home", str(home)]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": stream,
        "stderr": stream,
        "cwd": str(Path(__file__).resolve().parents[1]),
    }
    if _is_windows():
        flags = (
            _WINDOWS_CREATE_BREAKAWAY_FROM_JOB
            | _WINDOWS_DETACHED_PROCESS
            | _WINDOWS_CREATE_NEW_PROCESS_GROUP
        )
        try:
            return subprocess.Popen(command, creationflags=flags, **kwargs)
        except OSError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            winerror = getattr(exc, "winerror", None)
            if winerror is not None:
                detail += f" (WinError {winerror})"
            command_line = subprocess.list2cmdline(command)
            action = (
                "Start the existing control daemon from a user-controlled process outside the MCP SDK Job, "
                f"using: {command_line}"
            )
            raise ControlStartupError(
                "Windows could not safely start the control daemon with the required "
                "CREATE_BREAKAWAY_FROM_JOB, DETACHED_PROCESS, and CREATE_NEW_PROCESS_GROUP flags "
                f"({detail}); automatic control-daemon startup is disabled inside this MCP transport. "
                "The OS may have denied breakaway from the current Job. " + action,
                action=action,
            ) from exc
    kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def ensure_control() -> dict:
    home = control_home()
    endpoint = _read_endpoint(home)
    if endpoint:
        try:
            result = _request(endpoint, {"operation": "session_health", "arguments": {}, "execution": {}}, 1.0)
            if result.get("success"):
                return endpoint
        except Exception:
            if _alive(endpoint.get("pid"), endpoint.get("process_start_epoch_ms")):
                raise RuntimeError("Existing control process is unresponsive; no replacement started")
    # The daemon acquires the singleton lock before publishing its endpoint.
    # Concurrent transport processes may launch contenders; only one may run.
    log = home / "control.log"
    with log.open("ab") as stream:
        _spawn_control_daemon(home, stream)
    deadline = time.monotonic() + 15.0  # Control startup only, never an engine deadline.
    while time.monotonic() < deadline:
        endpoint = _read_endpoint(home)
        if endpoint:
            try:
                if _request(endpoint, {"operation": "session_health", "arguments": {}, "execution": {}}, 0.5).get("success"):
                    return endpoint
            except Exception:
                pass
        time.sleep(0.05)
    if _is_windows():
        command = [sys.executable, "-m", "comsol_mcp._control_daemon", "--home", str(home)]
        action = (
            "Start the existing control daemon from a user-controlled process outside the MCP SDK Job, "
            f"using: {subprocess.list2cmdline(command)}"
        )
        raise ControlStartupError(
            "Control daemon did not become ready after a required Windows detached startup; "
            "inspect the private control log before retrying. " + action,
            action=action,
        )
    raise ControlStartupError(
        "Control daemon did not become ready; inspect the private control log before retrying. "
        "On Windows, start it from a user-controlled process outside the MCP SDK Job."
    )


def dispatch(operation: str, arguments: dict, execution: dict) -> dict:
    execution = dict(execution)
    execution.setdefault("request_id", str(uuid4()))
    execution.setdefault("idempotency_key", str(uuid4()))
    rpc_timeout = execution.get("rpc_timeout_s", 30.0)
    if isinstance(rpc_timeout, bool) or not isinstance(rpc_timeout, (int, float)) or not 0 <= rpc_timeout <= 300:
        return {"success": False, "error": {"code": "INVALID_REQUEST", "message": "rpc_timeout_s must be between 0 and 300"}, "data": {}}
    try:
        endpoint = ensure_control()
        return _request(endpoint, {"operation": operation, "arguments": arguments, "execution": execution}, rpc_timeout + 5.0)
    except ControlStartupError as exc:
        error = {
            "code": "EXECUTION_STATE_UNKNOWN",
            "type": type(exc).__name__,
            "message": str(exc),
            "safe_retry": False,
        }
        if exc.action:
            error["action"] = exc.action
        return {
            "success": False,
            "error": error,
            "execution": {"request_id": execution["request_id"], "idempotency_key": execution["idempotency_key"]},
            "data": {"status": "UNKNOWN"},
        }
    except Exception as exc:
        return {
            "success": False,
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "type": type(exc).__name__,
                      "message": "Control response unavailable; query or resubmit with the same idempotency key to reconcile.",
                      "safe_retry": False},
            "execution": {"request_id": execution["request_id"], "idempotency_key": execution["idempotency_key"]},
            "data": {"status": "UNKNOWN"},
        }
