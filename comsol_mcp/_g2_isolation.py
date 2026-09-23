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
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError


# The RemoteAddrValve proof is tied to the one reviewed COMSOL 6.4 Mac
# installation change.  A receipt cannot authorize an arbitrary config path or
# self-report an arbitrary applied hash; the live target and bytes must match
# this reviewed proposal.
_REMOTE_ADDR_VALVE_TARGET = Path("/Applications/COMSOL64/Multiphysics/bin/servers/webbridge/conf/server.xml")
_REMOTE_ADDR_VALVE_APPLIED_SHA256 = "1a029e30e3b0218adfdc8ac01e15379a68ae48bb2fb658bdf397c0ba6de5c357"
_REMOTE_ADDR_VALVE_API_ROUTE = "/webbridge/websocket"
_ACCESS_LOG_NAME_RE = re.compile(r"^mphserverlocalhost_access_log\.(?P<date>\d{4}-\d{2}-\d{2})\.txt$")
_ACCESS_LOG_LINE_RE = re.compile(
    r'^(?P<peer>\S+)\s+\S+\s+\S+\s+\[(?P<timestamp>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<route>\S+)\s+HTTP/(?P<version>[0-9.]+)"\s+'
    r"(?P<http_status>\d{3})\s+\S+(?:\s+.*)?$"
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_mac_isolation_adapter() -> None:
    """Keep the receipt proof tied to the platform it was reviewed for."""
    if sys.platform != "darwin":
        raise ExecutionContractError(
            "UNSUPPORTED_PLATFORM",
            "owned-server isolation proof is only implemented for the reviewed macOS adapter",
        )


def _require_isolation_adapter() -> None:
    """Validate that the current platform has an isolation adapter."""
    if sys.platform not in ("darwin", "win32") and os.name != "nt":
        raise ExecutionContractError(
            "UNSUPPORTED_PLATFORM",
            f"owned-server isolation proof is not implemented for platform {sys.platform}",
        )


def _run_mac_probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    _require_mac_isolation_adapter()
    try:
        return subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "macOS process/socket probe is unavailable") from exc


def _mac_process_snapshot(pid: int) -> dict[str, Any] | None:
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


def _windows_process_snapshot(pid: int) -> dict[str, Any] | None:
    _require_isolation_adapter()
    from ._platform_process import process_identity
    ident = process_identity(pid, platform_name="nt")
    if not ident["alive"]:
        return None
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | Select-Object ProcessId, CommandLine, CreationDate | ConvertTo-Json -Compress"
    ]
    try:
        res = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            command = data.get("CommandLine") or ""
            birth = str(data.get("CreationDate") or ident.get("start_epoch_ms") or "")
            return {
                "pid": pid,
                "birth": birth,
                "command": command,
                "command_sha256": _sha256_text(command),
            }
    except Exception:
        pass
    birth = str(ident.get("start_epoch_ms") or "")
    return {
        "pid": pid,
        "birth": birth,
        "command": "comsol",
        "command_sha256": _sha256_text("comsol"),
    }


def _process_snapshot(pid: int) -> dict[str, Any] | None:
    if type(pid) is not int or pid <= 1:
        return None
    if sys.platform == "win32" or os.name == "nt":
        return _windows_process_snapshot(pid)
    return _mac_process_snapshot(pid)


def _mac_socket_rows(port: int) -> list[dict[str, Any]]:
    _require_mac_isolation_adapter()
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


def _windows_socket_rows(port: int) -> list[dict[str, Any]]:
    _require_isolation_adapter()
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "Windows socket probe is unavailable") from exc

    if result.returncode != 0:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "could not inspect COMSOL listener sockets on Windows")

    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or not line.upper().startswith("TCP"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local_str, remote_str, state_str, pid_str = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        def _safe_port(ep: str) -> int:
            t = str(ep).strip()
            s = t.rsplit("]:", 1)[-1] if (t.startswith("[") and "]" in t) else (t.rsplit(":", 1)[-1] if ":" in t else "")
            try:
                return int(s)
            except ValueError:
                return -1

        local_port = _safe_port(local_str)
        remote_port = _safe_port(remote_str)
        if local_port != port and remote_port != port:
            continue

        state = "LISTEN" if state_str.upper() in ("LISTENING", "LISTEN") else "ESTABLISHED" if state_str.upper() == "ESTABLISHED" else "OTHER"
        endpoint = local_str if state == "LISTEN" else f"{local_str}->{remote_str}"
        rows.append({
            "pid": pid,
            "endpoint": endpoint,
            "local_endpoint": local_str,
            "remote_endpoint": remote_str if remote_str not in ("0.0.0.0:0", "[::]:0", "*:*") else None,
            "state": state,
            "raw": line,
        })
    return rows


def _socket_rows(port: int) -> list[dict[str, Any]]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ExecutionContractError("INVALID_REQUEST", "socket probe port is invalid")
    if sys.platform == "win32" or os.name == "nt":
        return _windows_socket_rows(port)
    return _mac_socket_rows(port)


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


def _remote_addr_valve_mode(receipt: Mapping[str, Any]) -> bool:
    """Return whether the receipt opts into the one reviewed valve schema."""
    return receipt.get("isolation_mode") == "remote_addr_valve"


def _endpoint_port(endpoint: str) -> int:
    text = str(endpoint).strip()
    if text.startswith("[") and "]" in text:
        suffix = text.rsplit("]:", 1)[-1]
    else:
        suffix = text.rsplit(":", 1)[-1] if ":" in text else ""
    try:
        port = int(suffix)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "current endpoint has no valid port") from exc
    if not 1 <= port <= 65535:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "current endpoint has no valid port")
    return port


def _endpoint_host(endpoint: str) -> str:
    text = str(endpoint).strip()
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")]
    if ":" in text:
        return text.rsplit(":", 1)[0]
    return ""


def _is_loopback_host(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    elif text.count(":") == 1 and not text.startswith("::"):
        text = text.rsplit(":", 1)[0]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _wildcard_listener(endpoint: Any, port: int) -> bool:
    if not isinstance(endpoint, str):
        return False
    value = endpoint.strip().lower()
    return value in {f"*:{port}", f"0.0.0.0:{port}", f"[::]:{port}", f":::{port}"}


def _remote_config_identity(receipt: Mapping[str, Any]) -> tuple[Path, int, str]:
    config = receipt.get("config")
    if not isinstance(config, Mapping):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt has no configuration identity")
    target_value = config.get("target")
    inode = config.get("target_inode")
    applied_sha = config.get("content_sha256_after_apply")
    if not isinstance(target_value, str) or not target_value.strip():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt has no config target")
    if type(inode) is not int or inode <= 0 or not isinstance(applied_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", applied_sha):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve config identity is incomplete")
    target = Path(target_value).expanduser()
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve config target is unavailable") from exc
    if target.is_symlink() or not resolved.is_file():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve config target is not a regular file")
    try:
        approved_target = _REMOTE_ADDR_VALVE_TARGET.resolve(strict=False)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "reviewed RemoteAddrValve target is unavailable") from exc
    if resolved != approved_target:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve target is outside the reviewed Mac installation")
    if applied_sha.lower() != _REMOTE_ADDR_VALVE_APPLIED_SHA256:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt does not use the reviewed config hash")
    observed_stat = resolved.stat()
    if int(observed_stat.st_ino) != inode:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve config inode changed")
    observed_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if observed_sha.lower() != _REMOTE_ADDR_VALVE_APPLIED_SHA256:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve config hash changed")
    return resolved, inode, observed_sha


def _parse_utc_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", f"server access log {field} is missing")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", f"server access log {field} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", f"server access log {field} is not UTC")
    return parsed.astimezone(timezone.utc)


def _parse_access_log_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log contains an invalid timestamp") from exc
    return parsed


def _canonical_access_log_entries(segment: bytes) -> tuple[list[dict[str, Any]], list[datetime], list[str]]:
    if not segment or not segment.endswith(b"\n"):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof does not end at a complete line")
    try:
        text = segment.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof is not valid UTF-8") from exc
    entries: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    local_dates: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof contains a blank line")
        match = _ACCESS_LOG_LINE_RE.fullmatch(line.rstrip("\r"))
        if match is None:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof contains an unparsable entry")
        peer = match.group("peer")
        try:
            ipaddress.ip_address(peer)
        except ValueError as exc:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log peer is not an IP address") from exc
        entries.append({
            "peer": peer,
            "method": match.group("method"),
            "route": match.group("route"),
            "http_status": int(match.group("http_status")),
        })
        local_timestamp = _parse_access_log_timestamp(match.group("timestamp"))
        timestamps.append(local_timestamp)
        local_dates.append(local_timestamp.date().isoformat())
    if not entries:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof contains no entries")
    return entries, timestamps, local_dates


def _server_access_log_path(receipt_path: Path, receipt: Mapping[str, Any], record: Mapping[str, Any]) -> Path:
    paths = receipt.get("paths")
    if not isinstance(paths, Mapping):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof has no private run paths")
    run_dir_value = paths.get("run_dir")
    prefs_value = paths.get("prefs")
    path_value = record.get("path")
    if not all(isinstance(value, str) and value.strip() for value in (run_dir_value, prefs_value, path_value)):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof paths are incomplete")
    raw_prefs = Path(prefs_value).expanduser()
    if raw_prefs.is_symlink():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log prefs path is a symlink")
    try:
        receipt_run_dir = receipt_path.parent.resolve(strict=True)
        run_dir = Path(run_dir_value).expanduser().resolve(strict=True)
        prefs = raw_prefs.resolve(strict=True)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log private run path is unavailable") from exc
    if run_dir != receipt_run_dir or prefs != run_dir / "prefs" or not prefs.is_dir() or prefs.is_symlink():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log is not bound to the receipt's private prefs")
    raw_path = Path(path_value).expanduser()
    if not raw_path.is_absolute() or raw_path.is_symlink():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log path is not an owned absolute file")
    expected_dir = prefs / "tomcat" / "logs"
    try:
        expected_dir = expected_dir.resolve(strict=True)
        resolved = raw_path.resolve(strict=True)
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log file is unavailable") from exc
    if not expected_dir.is_dir() or resolved.parent != expected_dir or not resolved.is_file() or resolved.is_symlink():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log is outside the private prefs log directory")
    if _ACCESS_LOG_NAME_RE.fullmatch(resolved.name) is None:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log filename is not the COMSOL localhost access log")
    return resolved


def _verify_server_access_log(
    receipt_path: Path,
    receipt: Mapping[str, Any],
    nonloopback: Mapping[str, Any],
    *,
    address: str,
    port: int,
) -> dict[str, Any]:
    record = nonloopback.get("server_access_log")
    if not isinstance(record, Mapping) or record.get("status") != "PASS" or record.get("phase") != "java_connect_only":
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof is missing or not a Java-connect-only PASS")
    if record.get("raw_probe_started_after_capture") is not True or not isinstance(record.get("java_request_id"), str) or not record.get("java_request_id").strip():
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof capture identity is incomplete")
    started_at = _parse_utc_timestamp(record.get("started_at"), field="started_at")
    finished_at = _parse_utc_timestamp(record.get("finished_at"), field="finished_at")
    if finished_at < started_at:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof capture window is inverted")
    try:
        path = _server_access_log_path(receipt_path, receipt, record)
    except ExecutionContractError:
        raise
    inode = record.get("inode")
    offset_before = record.get("offset_before")
    offset_after = record.get("offset_after")
    segment_sha256 = record.get("segment_sha256")
    if (
        type(inode) is not int
        or inode <= 0
        or type(offset_before) is not int
        or type(offset_after) is not int
        or offset_before < 0
        or offset_after <= offset_before
        or not isinstance(segment_sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", segment_sha256) is None
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log byte identity is incomplete")
    try:
        observed_stat = path.stat()
        if int(observed_stat.st_ino) != inode or observed_stat.st_size < offset_after:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log inode or bounded interval changed")
        with path.open("rb") as handle:
            if offset_before:
                handle.seek(offset_before - 1)
                if handle.read(1) != b"\n":
                    raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log interval does not start at a line boundary")
            handle.seek(offset_before)
            segment = handle.read(offset_after - offset_before)
        if len(segment) != offset_after - offset_before:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log bounded interval is truncated")
        if hashlib.sha256(segment).hexdigest().lower() != segment_sha256.lower():
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log bounded segment hash changed")
        if segment[-1:] != b"\n":
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log bounded interval is not line complete")
        if path.stat().st_ino != inode:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log inode changed during verification")
    except OSError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log bounded interval is unreadable") from exc
    parsed_entries, parsed_timestamps, parsed_local_dates = _canonical_access_log_entries(segment)
    filename_match = _ACCESS_LOG_NAME_RE.fullmatch(path.name)
    if filename_match is None or any(local_date != filename_match.group("date") for local_date in parsed_local_dates):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log filename is unrelated to its bounded entries")
    recorded_entries = record.get("entries")
    if not isinstance(recorded_entries, list):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log parsed entries are missing")
    canonical_recorded: list[dict[str, Any]] = []
    for entry in recorded_entries:
        if not isinstance(entry, Mapping) or set(entry) != {"peer", "method", "route", "http_status"}:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log parsed entry schema is invalid")
        if not isinstance(entry.get("peer"), str) or not isinstance(entry.get("method"), str) or not isinstance(entry.get("route"), str) or type(entry.get("http_status")) is not int:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log parsed entry values are invalid")
        canonical_recorded.append({
            "peer": entry["peer"],
            "method": entry["method"],
            "route": entry["route"],
            "http_status": entry["http_status"],
        })
    if canonical_recorded != parsed_entries:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log parsed entries do not match the bounded bytes")
    nonloop_entries: list[tuple[dict[str, Any], datetime]] = []
    for entry, timestamp in zip(parsed_entries, parsed_timestamps):
        try:
            peer = ipaddress.ip_address(entry["peer"])
        except ValueError as exc:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log peer is not an IP address") from exc
        if not peer.is_loopback:
            nonloop_entries.append((entry, timestamp))
    if len(nonloop_entries) != 1:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log does not contain exactly one non-loopback request")
    entry, timestamp = nonloop_entries[0]
    expected_peer = str(ipaddress.ip_address(address))
    if (
        str(ipaddress.ip_address(entry["peer"])) != expected_peer
        or entry["method"] != "GET"
        or entry["route"] != _REMOTE_ADDR_VALVE_API_ROUTE
        or entry["http_status"] != 403
        or timestamp < started_at - timedelta(seconds=1)
        or timestamp > finished_at + timedelta(seconds=1)
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log does not prove the exact non-loopback API 403")
    java_api = nonloopback.get("java_api")
    if (
        not isinstance(java_api, Mapping)
        or java_api.get("status") != "REJECTED"
        or java_api.get("authenticated") is not False
        or java_api.get("address") != address
        or java_api.get("port") != port
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe is not a bound rejection")
    http_probe = nonloopback.get("http_probe")
    local_endpoint = http_probe.get("local_endpoint") if isinstance(http_probe, Mapping) else None
    peer_endpoint = http_probe.get("peer_endpoint") if isinstance(http_probe, Mapping) else None
    if (
        not isinstance(http_probe, Mapping)
        or http_probe.get("address") != address
        or http_probe.get("source_bind_requested") != address
        or http_probe.get("route") != _REMOTE_ADDR_VALVE_API_ROUTE
        or http_probe.get("status") != "HTTP_RESPONSE"
        or http_probe.get("http_status") != 403
        or not isinstance(local_endpoint, Mapping)
        or local_endpoint.get("host") != address
        or type(local_endpoint.get("port")) is not int
        or not 1 <= local_endpoint.get("port") <= 65535
        or not isinstance(peer_endpoint, Mapping)
        or peer_endpoint.get("host") != address
        or peer_endpoint.get("port") != port
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "raw non-loopback API probe is not an explicit source-bound 403")
    return {
        "status": "PASS",
        "phase": "java_connect_only",
        "path": str(path),
        "inode": inode,
        "offset_before": offset_before,
        "offset_after": offset_after,
        "segment_sha256": segment_sha256.lower(),
        "java_request_id": record["java_request_id"],
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "entries": parsed_entries,
        "parsed_nonloopback_entry": entry,
        "raw_probe_started_after_capture": True,
    }


def _remote_probe_identity(receipt: Mapping[str, Any], *, target: Path, port: int, config_sha: str, receipt_path: Path | None = None) -> Mapping[str, Any]:
    probe = receipt.get("probe")
    if not isinstance(probe, Mapping):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt has no API probe")
    if probe.get("probe_kind") != "comsol_java_api" or probe.get("status") not in {"PASS", "FAIL"}:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve probe has an invalid COMSOL Java API status")
    probe_process = probe.get("process")
    loopback = probe.get("loopback")
    nonloopback = probe.get("nonloopback")
    observed_at = probe.get("observed_at")
    probe_port = probe.get("port")
    probe_sha = probe.get("config_sha256")
    if (
        not isinstance(probe_process, Mapping)
        or type(probe_process.get("pid")) is not int
        or not isinstance(probe_process.get("birth"), str)
        or type(probe_port) is not int
        or not isinstance(probe_sha, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", probe_sha)
        or not isinstance(observed_at, str)
        or not observed_at.strip()
        or not isinstance(loopback, Mapping)
        or not isinstance(nonloopback, Mapping)
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve Java API probe identity is incomplete")
    process = receipt.get("process")
    if not isinstance(process, Mapping) or (
        probe_process.get("pid") != process.get("pid")
        or probe_process.get("birth") != process.get("birth")
        or probe_port != port
        or probe_sha.lower() != config_sha.lower()
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve probe is bound to another process, port, or config")
    if (
        loopback.get("address") != "127.0.0.1"
        or loopback.get("port") != port
        or loopback.get("authenticated_api_success") is not True
    ):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "authenticated COMSOL Java API loopback probe did not pass")
    nonloopback_address = nonloopback.get("address")
    if not isinstance(nonloopback_address, str):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe address is missing")
    try:
        parsed_nonloopback = ipaddress.ip_address(nonloopback_address)
    except ValueError as exc:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe address is not an IP") from exc
    if parsed_nonloopback.is_loopback or parsed_nonloopback.is_unspecified or parsed_nonloopback.is_multicast:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe address is not valid")
    if nonloopback.get("port") != port:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe port does not match the COMSOL endpoint")
    api_exception_status = nonloopback.get("api_exception_http_status")
    if api_exception_status not in (None, 403):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API probe has an unsupported HTTP status")
    java_api = nonloopback.get("java_api")
    if isinstance(java_api, Mapping) and (java_api.get("status") == "UNEXPECTED_SUCCESS" or java_api.get("authenticated") is True):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "non-loopback Java API unexpectedly succeeded")
    if api_exception_status == 403:
        if probe.get("status") != "PASS":
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "Java API HTTP 403 branch is not a passing probe")
        return {
            "probe_kind": probe["probe_kind"],
            "observed_at": observed_at,
            "loopback_address": loopback["address"],
            "loopback_port": loopback["port"],
            "authenticated_api_success": True,
            "nonloopback_address": str(parsed_nonloopback),
            "nonloopback_port": nonloopback["port"],
            "nonloopback_api_exception_http_status": 403,
            "denial_evidence_source": "java_api_exception_http_403",
        }
    if receipt_path is None:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "server access log proof has no lifecycle receipt path")
    access_log = _verify_server_access_log(
        receipt_path,
        receipt,
        nonloopback,
        address=str(parsed_nonloopback),
        port=port,
    )
    return {
        "probe_kind": probe["probe_kind"],
        "observed_at": observed_at,
        "loopback_address": loopback["address"],
        "loopback_port": loopback["port"],
        "authenticated_api_success": True,
        "nonloopback_address": str(parsed_nonloopback),
        "nonloopback_port": nonloopback["port"],
        "nonloopback_api_exception_http_status": None,
        "denial_evidence_source": "server_access_log",
        "server_access_log": access_log,
    }


def _verify_remote_addr_valve(
    receipt_path: Path, receipt: Mapping[str, Any], *, endpoint: str, worker_pid: int | None,
) -> dict[str, Any]:
    process = receipt.get("process")
    if not isinstance(process, Mapping):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt has no process identity")
    pid, birth, command_hash = process.get("pid"), process.get("birth"), process.get("command_sha256")
    if type(pid) is not int or not isinstance(birth, str) or not isinstance(command_hash, str):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve process identity is incomplete")
    observed = _process_snapshot(pid)
    if observed is None or observed["birth"] != birth or observed["command_sha256"] != command_hash:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "owned COMSOL process identity no longer matches receipt")
    lowered = observed["command"].lower()
    if "comsol" not in lowered or ("mphserver" not in lowered and "serverapplication" not in lowered):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "recorded process is not a COMSOL server")
    port = _endpoint_port(endpoint)
    if not _is_loopback_host(_endpoint_host(endpoint)):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "current Worker endpoint is not loopback")
    receipt_port = process.get("port")
    if type(receipt_port) is not int or receipt_port != port:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "receipt endpoint port does not match current endpoint")
    target, inode, config_sha = _remote_config_identity(receipt)
    probe = _remote_probe_identity(receipt, target=target, port=port, config_sha=config_sha, receipt_path=receipt_path)
    rows = _socket_rows(port)
    listeners = [row for row in rows if row["state"] == "LISTEN"]
    if len(listeners) != 1 or listeners[0]["pid"] != pid:
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "owned RemoteAddrValve listener identity is not unique")
    listener_endpoint = listeners[0].get("endpoint")
    if not _wildcard_listener(listener_endpoint, port):
        raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "RemoteAddrValve proof does not show the owned wildcard listener")
    clients = [row for row in rows if row["state"] == "ESTABLISHED"]
    if type(worker_pid) is not int or worker_pid <= 1 or worker_pid == pid:
        if clients:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "connected clients cannot be attributed to the bound Worker")
    else:
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
    return {
        "verified": True,
        "receipt": str(receipt_path),
        "pid": pid,
        "birth": birth,
        "endpoint": str(endpoint),
        "listener": listeners[0],
        "listener_binding": "wildcard",
        "loopback_listener": False,
        "worker_pid": worker_pid,
        "established_client_pids": sorted({row["pid"] for row in clients}),
        "config_target": str(target),
        "config_inode": inode,
        "config_sha256": config_sha,
        "api_probe": dict(probe),
        "proof_scope": (
            "owned process identity + in-place RemoteAddrValve config + authenticated loopback API PASS + "
            f"{probe['denial_evidence_source']} + exact IPv4 loopback Worker peer"
        ),
    }


def verify_owned_server(receipt_path: str | Path, *, endpoint: str, worker_pid: int | None = None) -> dict[str, Any]:
    """Return proof metadata or raise a structured fail-closed error."""
    if sys.platform == "darwin":
        _require_mac_isolation_adapter()
    else:
        _require_isolation_adapter()
    path = Path(receipt_path).expanduser().resolve()
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "configured isolation receipt is unreadable") from exc
    if isinstance(receipt, Mapping) and _remote_addr_valve_mode(receipt):
        if receipt.get("status") != "RUNNING":
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "remote_addr_valve receipt is not a running owned receipt")
        return _verify_remote_addr_valve(receipt_path=path, receipt=receipt, endpoint=endpoint, worker_pid=worker_pid)
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
