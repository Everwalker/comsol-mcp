"""Control side proof for model mutators that require an owned COMSOL server.

The proof is deliberately independent of the caller's ``execution`` JSON.  A
deployment may configure one private lifecycle receipt through
``COMSOL_MCP_ISOLATION_RECEIPT``; a request cannot claim isolation by setting a
boolean.  The receipt is checked against live PID/birth/command, the exact
loopback listener, and current established sockets before a broad G2 action
enters the Worker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_mac_isolation_adapter() -> None:
    """Keep the receipt proof tied to the platform it was reviewed for.

    The G2 receipt currently records macOS ``ps``/``lsof`` observations.  A
    different platform must use a separately reviewed adapter; falling through
    to a missing POSIX executable would turn an unverified environment into a
    misleading generic failure.
    """
    if sys.platform != "darwin":
        raise ExecutionContractError(
            "UNSUPPORTED_PLATFORM",
            "owned-server isolation proof is only implemented for the reviewed macOS adapter",
        )


def _run_mac_probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    _require_mac_isolation_adapter()
    try:
        return subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "macOS process/socket probe is unavailable") from exc


def _process_snapshot(pid: int) -> dict[str, Any] | None:
    if type(pid) is not int or pid <= 1:
        return None
    _require_mac_isolation_adapter()
    result = _run_mac_probe(["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="])
    line = result.stdout.strip()
    fields = line.split()
    if result.returncode != 0 or len(fields) < 8:
        return None
    try:
        observed_pid = int(fields[0])
    except ValueError:
        return None
    command = " ".join(fields[7:])
    return {"pid": observed_pid, "birth": " ".join(fields[2:7]), "command": command,
            "command_sha256": _sha256_text(command)}


def _socket_rows(port: int) -> list[dict[str, Any]]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ExecutionContractError("INVALID_REQUEST", "socket probe port is invalid")
    result = _run_mac_probe(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}"])
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "could not inspect COMSOL listener sockets")
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("COMMAND"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            pid = int(fields[1])
        except ValueError:
            continue
        # lsof prints the address immediately before the state token, for
        # example ``127.0.0.1:56388 (LISTEN)``.  Taking the state token itself
        # would make every valid listener fail the exact-endpoint check.
        state_index = next((index for index, field in enumerate(fields) if field in {"(LISTEN)", "(ESTABLISHED)"}), -1)
        endpoint = fields[state_index - 1] if state_index > 0 else next((field for field in fields if "->" in field), "")
        state = "ESTABLISHED" if "(ESTABLISHED)" in fields or "(ESTABLISHED)" in line else "LISTEN" if "(LISTEN)" in fields or "(LISTEN)" in line else "OTHER"
        local_endpoint, remote_endpoint = _split_connection(endpoint)
        rows.append({"pid": pid, "endpoint": endpoint, "local_endpoint": local_endpoint,
                     "remote_endpoint": remote_endpoint, "state": state, "raw": line})
    return rows


_LOOPBACK_ENDPOINT_RE = re.compile(r"^127\.0\.0\.1:(?P<port>[0-9]+)$")


def _split_connection(endpoint: str) -> tuple[str, str | None]:
    """Split one lsof TCP endpoint while retaining an auditable raw value."""
    if not isinstance(endpoint, str):
        return "", None
    if "->" not in endpoint:
        return endpoint, None
    local, remote = endpoint.split("->", 1)
    return local, remote


def _loopback_port(endpoint: str | None) -> int | None:
    if not isinstance(endpoint, str):
        return None
    match = _LOOPBACK_ENDPOINT_RE.fullmatch(endpoint.strip())
    if not match:
        return None
    try:
        value = int(match.group("port"))
    except ValueError:
        return None
    return value if 1 <= value <= 65535 else None


def _row_connection(row: Mapping[str, Any]) -> tuple[str, str | None]:
    local = row.get("local_endpoint")
    remote = row.get("remote_endpoint")
    if isinstance(local, str):
        return local, remote if isinstance(remote, str) else None
    return _split_connection(row.get("endpoint", ""))


def verify_owned_server(receipt_path: str | Path, *, endpoint: str, worker_pid: int | None = None) -> dict[str, Any]:
    """Return proof metadata or raise a structured fail-closed error."""
    _require_mac_isolation_adapter()
    path = Path(receipt_path).expanduser().resolve()
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "configured isolation receipt is unreadable") from exc
    if not isinstance(receipt, Mapping) or receipt.get("status") != "RUNNING":
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "configured isolation receipt is not a running owned receipt")
    process = receipt.get("process")
    if not isinstance(process, Mapping):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "isolation receipt has no process identity")
    pid, birth, command_hash = process.get("pid"), process.get("birth"), process.get("command_sha256")
    if type(pid) is not int or not isinstance(birth, str) or not isinstance(command_hash, str):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "isolation receipt process identity is incomplete")
    observed = _process_snapshot(pid)
    if observed is None or observed["birth"] != birth or observed["command_sha256"] != command_hash:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "owned COMSOL process identity no longer matches receipt")
    lowered = observed["command"].lower()
    if "comsol" not in lowered or ("mphserver" not in lowered and "serverapplication" not in lowered):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "recorded process is not a COMSOL server")
    port = int(str(endpoint).rsplit(":", 1)[-1]) if ":" in str(endpoint) else 0
    if port <= 0:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "current endpoint has no valid port")
    expected = f"127.0.0.1:{port}"
    recorded_port = process.get("port")
    if recorded_port is not None:
        try:
            if int(recorded_port) != port:
                raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "receipt endpoint does not match current endpoint")
        except (TypeError, ValueError) as exc:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "receipt endpoint port is invalid") from exc
    rows = _socket_rows(port)
    listeners = [row for row in rows if row["state"] == "LISTEN"]
    if len(listeners) != 1 or listeners[0]["pid"] != pid or listeners[0]["endpoint"] != expected:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "COMSOL listener is not exactly the owned IPv4 loopback endpoint")
    if _loopback_port(listeners[0].get("endpoint")) != port:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "COMSOL listener is not an IPv4 loopback endpoint")
    clients = [row for row in rows if row["state"] == "ESTABLISHED"]
    if type(worker_pid) is not int or worker_pid <= 1 or worker_pid == pid:
        if clients:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "connected clients cannot be attributed to the bound Worker")
    else:
        # lsof can report a socket owned by the COMSOL PID even when the
        # remote process is hidden or belongs to another user.  PID matching
        # alone therefore does not prove ownership.  Require both directions
        # of the same IPv4 loopback pair: the server row must own the COMSOL
        # port and the Worker row must point back to it, with matching peer
        # ports.  Wildcards, IPv6, non-loopback peers, and an extra client all
        # fail closed.
        unexpected = [row for row in clients if row["pid"] not in {pid, worker_pid}]
        if unexpected:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "an unapproved client socket is connected to the COMSOL server")
        server_pairs: set[int] = set()
        worker_pairs: set[int] = set()
        for row in clients:
            local, remote = _row_connection(row)
            local_port = _loopback_port(local)
            remote_port = _loopback_port(remote)
            if local_port is None or remote_port is None:
                raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "an established COMSOL socket is not an IPv4 loopback pair")
            if row["pid"] == pid:
                if local_port != port:
                    raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "COMSOL server socket does not own the recorded listener port")
                server_pairs.add(remote_port)
            elif row["pid"] == worker_pid:
                if remote_port != port:
                    raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "Worker socket is not connected to the recorded COMSOL endpoint")
                worker_pairs.add(local_port)
        if not server_pairs or not worker_pairs or server_pairs != worker_pairs:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "the exact COMSOL-to-Worker socket pair was not observed in both directions")
    return {"verified": True, "receipt": str(path), "pid": pid, "birth": birth,
            "endpoint": expected, "listener": listeners[0], "worker_pid": worker_pid,
            "established_client_pids": sorted({row["pid"] for row in clients}),
            "proof_scope": "owned process identity + exact IPv4 loopback listener + connected-client observation"}


def configured_receipt() -> Path | None:
    value = os.environ.get("COMSOL_MCP_ISOLATION_RECEIPT", "").strip()
    return Path(value).expanduser() if value else None
