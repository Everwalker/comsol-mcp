"""Authenticated local transport to the persistent control service.

The MCP stdio process can disappear without owning the lifetime of a solve.
No transport error triggers a second submission or terminates an engine.
"""
from __future__ import annotations

import json
import os
import stat
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


class ScmAttachError(ControlStartupError):
    """Explicit existing-service preflight failed; startup was never attempted."""

    def __init__(self):
        super().__init__("Explicit SCM attach-only preflight failed; requested operation was not submitted.")


class LocalAttachOnlyError(ControlStartupError):
    """Local attach-only selection failed before operation submission."""

    def __init__(self):
        super().__init__("Local attach-only control preflight failed; requested operation was not submitted.")


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


def control_home(*, create: bool = True) -> Path:
    from comsol_mcp._server import COMSOL_SERVER_MCP_HOME
    if not create:
        # Inspect the configured lexical root before resolving it. Never create,
        # chmod, or repair an existing directory in attach-only mode.
        if COMSOL_SERVER_MCP_HOME.is_symlink():
            raise RuntimeError("Configured control root must not be a symlink")
        home = COMSOL_SERVER_MCP_HOME.resolve() / "control-private"
        observed = home.lstat()
        if not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
            raise RuntimeError("Control directory must be a real directory")
        if hasattr(os, "getuid") and observed.st_uid != os.getuid():
            raise RuntimeError("Control directory is owned by another user")
        if stat.S_IMODE(observed.st_mode) & 0o077:
            raise RuntimeError("Control directory is not private")
        return home
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
            winerror = getattr(exc, "winerror", None)
            detail = f"{type(exc).__name__}: {exc}"
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
    # Select before control_home: even a blank/malformed locator is attach-only.
    locator = os.environ.get("COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG")
    if locator is not None:
        try:
            from ._windows_scm_endpoint import verify_endpoint
            endpoint = verify_endpoint(locator)
            result = _request(endpoint, {"operation": "session_health", "arguments": {}, "execution": {}}, 1.0)
            if not isinstance(result, dict) or result.get("success") is not True:
                raise ScmAttachError()
            return endpoint
        except Exception:
            # Native/private causes stay with their original reader. Neither
            # endpoint bytes nor transport exception text enter public errors.
            raise ScmAttachError() from None
    mode = os.environ.get("COMSOL_SERVER_MCP_CONTROL_MODE")
    if mode not in (None, "auto", "attach-only"):
        raise LocalAttachOnlyError()
    if mode == "attach-only":
        try:
            home = control_home(create=False)
            endpoint = _read_endpoint(home)
            if not isinstance(endpoint, dict):
                raise LocalAttachOnlyError()
            result = _request(endpoint, {"operation": "session_health", "arguments": {}, "execution": {}}, 1.0)
            if not isinstance(result, dict) or result.get("success") is not True:
                raise LocalAttachOnlyError()
            return endpoint
        except Exception:
            # Never expose endpoint/token/path or the transport exception.
            raise LocalAttachOnlyError() from None
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
    except Exception as exc:
        error = {
            "code": "CONTROL_REQUEST_NOT_SUBMITTED",
            "type": type(exc).__name__,
            "message": (
                str(exc)
                if isinstance(exc, ControlStartupError)
                else "Control preflight failed; the requested operation was not submitted."
            ),
            "safe_retry": False,
        }
        if isinstance(exc, ControlStartupError) and exc.action:
            error["action"] = exc.action
        return {
            "success": False,
            "error": error,
            "execution": {"request_id": execution["request_id"], "idempotency_key": execution["idempotency_key"]},
            "data": {
                "status": "NOT_SUBMITTED",
                "operation_dispatched": False,
                "execution_state_unknown": False,
                "dispatch_stage": "pre_dispatch",
                # ensure_control may have started a daemon before discovering
                # that it is not ready. This describes the requested operation
                # only and makes no claim about control-daemon startup effects.
                "control_startup_outcome": "NOT_ATTEMPTED" if isinstance(exc, (ScmAttachError, LocalAttachOnlyError)) else "UNRESOLVED",
            },
        }

    try:
        return _request(endpoint, {"operation": operation, "arguments": arguments, "execution": execution}, rpc_timeout + 5.0)
    except Exception as exc:
        return {
            "success": False,
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "type": type(exc).__name__,
                      "message": "Control response unavailable; query or resubmit with the same idempotency key to reconcile.",
                      "safe_retry": False},
            "execution": {"request_id": execution["request_id"], "idempotency_key": execution["idempotency_key"]},
            "data": {"status": "UNKNOWN"},
        }
