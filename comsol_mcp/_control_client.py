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


def _alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Unknown ownership/liveness is never restart permission.


def ensure_control() -> dict:
    home = control_home()
    endpoint = _read_endpoint(home)
    if endpoint:
        try:
            result = _request(endpoint, {"operation": "session_health", "arguments": {}, "execution": {}}, 1.0)
            if result.get("success"):
                return endpoint
        except Exception:
            if _alive(endpoint.get("pid")):
                raise RuntimeError("Existing control process is unresponsive; no replacement started")
    # The daemon acquires the singleton lock before publishing its endpoint.
    # Concurrent transport processes may launch contenders; only one may run.
    log = home / "control.log"
    with log.open("ab") as stream:
        subprocess.Popen(
            [sys.executable, "-m", "comsol_mcp._control_daemon", "--home", str(home)],
            stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
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
    raise RuntimeError("Control startup did not become ready")


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
    except Exception as exc:
        return {
            "success": False,
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "type": type(exc).__name__,
                      "message": "Control response unavailable; query or resubmit with the same idempotency key to reconcile.",
                      "safe_retry": False},
            "execution": {"request_id": execution["request_id"], "idempotency_key": execution["idempotency_key"]},
            "data": {"status": "UNKNOWN"},
        }
