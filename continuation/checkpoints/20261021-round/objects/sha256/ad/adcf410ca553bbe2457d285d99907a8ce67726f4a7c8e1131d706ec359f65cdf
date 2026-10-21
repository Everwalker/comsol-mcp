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
import ntpath
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_argv(argv: Any) -> str:
    if (type(argv) not in (list, tuple) or not argv
            or any(type(arg) is not str or "\x00" in arg for arg in argv)):
        raise ValueError("argument vector is malformed")
    return hashlib.sha256(json.dumps(
        list(argv), ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


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


def _run_mac_probe(command: list[str], *, timeout_s: float | None = None, admission_owner: Any = None) -> subprocess.CompletedProcess[str]:
    _require_mac_isolation_adapter()
    if timeout_s is not None and (type(timeout_s) not in (int,float) or not 0 < timeout_s < float("inf")):
        raise ValueError("private process probe timeout must be finite positive")
    owner=None
    if admission_owner is not None:
        from tools.w24_coupled_admission import require_admission_owner
        owner=require_admission_owner(admission_owner)
        timeout_s=owner.before_effect('G2_native_'+command[0],timeout_s or 10.0)
    try:
        result=subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                              **({"timeout":timeout_s} if timeout_s is not None else {}))
        if owner is not None:owner.wait_cap(1.01)
        return result
    except BaseException as exc:
        if owner is not None:owner.fail('G2_native_probe_failed_or_late',exc)
        if owner is not None and isinstance(exc,TimeoutError):raise
        if isinstance(exc,OSError):
            raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "macOS process/socket probe is unavailable") from exc
        raise


def _mac_process_snapshot(pid: int, *, timeout_s: float | None = None, admission_owner: Any = None) -> dict[str, Any] | None:
    _require_mac_isolation_adapter()
    command=["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="]
    result = _run_mac_probe(command) if timeout_s is None and admission_owner is None else _run_mac_probe(command,timeout_s=timeout_s,**({"admission_owner":admission_owner} if admission_owner is not None else {}))
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
    # F05: Fail closed — if CIM query fails or returns no output, the process
    # snapshot is UNAVAILABLE.  Unknown information must not be fabricated as
    # "comsol" since that would bypass the isolation gate.
    return None


def _process_snapshot(pid: int, *, timeout_s: float | None = None, admission_owner: Any = None) -> dict[str, Any] | None:
    if type(pid) is not int or pid <= 1:
        return None
    if admission_owner is not None and sys.platform!="darwin":
        raise ExecutionContractError("UNSUPPORTED_PLATFORM","coupled native probe owner requires macOS")
    if sys.platform == "win32" or os.name == "nt":
        return _windows_process_snapshot(pid)
    return _mac_process_snapshot(pid) if timeout_s is None and admission_owner is None else _mac_process_snapshot(pid,timeout_s=timeout_s,**({"admission_owner":admission_owner} if admission_owner is not None else {}))


def _mac_socket_rows(port: int, *, admission_owner: Any = None) -> list[dict[str, Any]]:
    _require_mac_isolation_adapter()
    command=["/usr/sbin/lsof", "-nP", f"-iTCP:{port}"]
    result = _run_mac_probe(command) if admission_owner is None else _run_mac_probe(command,admission_owner=admission_owner)
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


def _socket_rows(port: int, *, admission_owner: Any = None) -> list[dict[str, Any]]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ExecutionContractError("INVALID_REQUEST", "socket probe port is invalid")
    if admission_owner is not None and sys.platform!="darwin":
        raise ExecutionContractError("UNSUPPORTED_PLATFORM","coupled native socket probe owner requires macOS")
    if sys.platform == "win32" or os.name == "nt":
        return _windows_socket_rows(port)
    return _mac_socket_rows(port) if admission_owner is None else _mac_socket_rows(port,admission_owner=admission_owner)


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


def verify_owned_server(receipt_path: str | Path, *, endpoint: str, worker_pid: int | None = None, admission_owner: Any = None) -> dict[str, Any]:
    """Return proof metadata or raise a structured fail-closed error."""
    if sys.platform == "darwin":
        _require_mac_isolation_adapter()
    else:
        _require_isolation_adapter()
    if admission_owner is not None:
        from tools.w24_coupled_admission import require_admission_owner
        owner=require_admission_owner(admission_owner)
        owner.before_effect('G2_owned_server_verification',10.0)
        if sys.platform!='darwin':
            raise ExecutionContractError('UNSUPPORTED_PLATFORM','coupled G2 verification requires macOS')
    path = Path(receipt_path).expanduser().resolve()
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionContractError("ISOLATION_PROOF_UNAVAILABLE", "configured isolation receipt is unreadable") from exc
    if isinstance(receipt, Mapping) and _remote_addr_valve_mode(receipt):
        if admission_owner is not None:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED","coupled owner requires original local-loopback receipt")
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
    if admission_owner is not None:
        if (owner.server.proc is not owner.handles.get('server') or pid!=owner.server.proc.pid
                or owner.server.port is None or endpoint!=f'127.0.0.1:{owner.server.port}'
                or owner.handles.get('worker') is None
                or getattr(owner.server.worker, '_process', None) is not owner.handles['worker']
                or worker_pid!=owner.handles['worker'].pid):
            raise ExecutionContractError('ISOLATION_PROOF_REQUIRED','coupled G2 receipt/endpoint/Worker differs from original owner')
    observed = _process_snapshot(pid) if admission_owner is None else _process_snapshot(pid,admission_owner=admission_owner)
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
    rows = _socket_rows(port) if admission_owner is None else _socket_rows(port,admission_owner=admission_owner)
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


def _session_isolation_refusal(message: str, *, code: str = "ISOLATION_PROOF_REQUIRED",
                               details: Mapping[str, Any] | None = None) -> ExecutionContractError:
    return ExecutionContractError(
        code, message, stage="validation",
        details={"isolation_adapter": "active_session_owned_popen_socket",
                 "target_operation_dispatched": False,
                 "worker_operation_dispatched": False,
                 **(dict(details) if isinstance(details, Mapping) else {})},
    )


def _verify_macos_live_command(pid: int, argv: Any, *, label: str,
                              timeout_s: float = 2.0, admission_owner: Any = None) -> None:
    if type(timeout_s) not in (int, float) or not 0 < timeout_s < float("inf"):
        raise _session_isolation_refusal("the live command probe requires a finite positive timeout")
    if (type(argv) not in (list, tuple) or not argv
            or any(type(arg) is not str or "\x00" in arg for arg in argv)):
        raise _session_isolation_refusal(f"the retained {label} startup command is malformed")
    try:
        observed = _process_snapshot(pid, timeout_s=timeout_s, admission_owner=admission_owner)
    except Exception as exc:
        raise _session_isolation_refusal(
            f"the live {label} command identity probe failed",
            code="ISOLATION_PROOF_UNAVAILABLE",
            details={"cause_type": type(exc).__name__},
        ) from exc
    command = observed.get("command") if isinstance(observed, Mapping) else None
    command_sha256 = observed.get("command_sha256") if isinstance(observed, Mapping) else None
    if (not isinstance(observed, Mapping) or observed.get("pid") != pid
            or not isinstance(command, str) or not command
            or command_sha256 != _sha256_text(command)):
        raise _session_isolation_refusal(
            f"the live {label} command identity is unavailable",
            code="ISOLATION_PROOF_UNAVAILABLE",
        )
    expected = " ".join(" ".join(argv).split())
    if command != expected:
        raise _session_isolation_refusal(f"the live {label} command differs from its retained startup identity")


def _session_popen_alive(process: Any, pid: int, *, label: str) -> None:
    if type(process) is not subprocess.Popen or type(pid) is not int or pid <= 1:
        raise _session_isolation_refusal(
            f"the reviewed macOS adapter requires the exact retained {label} subprocess.Popen owner",
            code="ISOLATION_PROOF_UNAVAILABLE",
            details={"owner_type": type(process).__name__, "platform_scope": "macOS_only"},
        )
    if getattr(process, "pid", None) != pid or not callable(getattr(process, "poll", None)):
        raise _session_isolation_refusal(f"the retained {label} Popen identity changed")
    try:
        returncode = process.poll()
    except Exception as exc:
        raise _session_isolation_refusal(
            f"the retained {label} Popen state could not be read",
            code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__},
        ) from exc
    if returncode is not None:
        raise _session_isolation_refusal(f"the retained {label} process is terminal")


def _verify_windows_session_owned_server(context: Any, *, backend: Any, worker: Any,
                                          service: Any) -> dict[str, Any]:
    """Verify the exact Windows T037 HANDLE owners and their read-only socket pair."""
    from ._platform_process import process_identity
    from ._runtime_installation import runtime_id_for_root
    from ._windows_t037 import _Win32ChildProcess, _native_g2_session_launch_owner
    from ._session_server import _server_directories, build_server_command
    from ._session_context import (
        CanonicalSocket, OwnedServerProcessIdentity, SessionRuntimeContext,
        session_state_directory,
    )

    if not isinstance(context, SessionRuntimeContext):
        raise _session_isolation_refusal("an exact active SessionRuntimeContext is required")
    if context.backend is not backend or context.worker is not worker or context.service is not service:
        raise _session_isolation_refusal("active SessionRuntimeContext does not match this exact backend/Worker/service")
    if (context.server_ownership != "mcp_managed" or context.server_started_by_mcp is not True
            or context.client_connected is not True or context.client_retired is not False):
        raise _session_isolation_refusal("the selected session is not an active MCP-managed Server binding")
    if (not isinstance(context.project_id, str) or not context.project_id
            or not isinstance(context.session_id, str) or not context.session_id
            or not isinstance(context.worker_instance_id, str) or not context.worker_instance_id
            or not isinstance(context.runtime.runtime_id, str) or not context.runtime.runtime_id):
        raise _session_isolation_refusal("the selected session/project/runtime identity is incomplete")
    ledger = getattr(service, "ledger", None)
    if (getattr(ledger, "session_id", None) != context.session_id
            or getattr(ledger, "server_ownership", None) != "mcp_managed"):
        raise _session_isolation_refusal("active service ledger does not match the selected owned session")
    if (backend.service is not service or backend.worker is not worker
            or not isinstance(getattr(backend, "worker_identity", None), Mapping)):
        raise _session_isolation_refusal("the active ManagedBackend binding is incomplete")
    from ._managed_backend import _RuntimeObservationBinding
    binding = getattr(backend, "_runtime_observation_binding", None)
    if (type(binding) is not _RuntimeObservationBinding
            or binding.worker is not worker or binding.service is not service
            or binding.runtime_id != context.runtime.runtime_id
            or binding.worker_instance_id != context.worker_instance_id
            or binding.connection_epoch != context.worker_epoch
            or binding.server_instance_id != ledger.server_instance_id
            or binding.endpoint != f"127.0.0.1:{context.endpoint.port}"
            or binding.legacy is not False
            or binding.installation_runtime_id != runtime_id_for_root(context.runtime.installation_root)):
        raise _session_isolation_refusal("the Worker identity is not bound to this exact installed session runtime")
    try:
        if Path(context.project_root).resolve() != Path(backend.project_root).resolve():
            raise _session_isolation_refusal("active project root differs from the registered session")
    except (OSError, TypeError, ValueError) as exc:
        raise _session_isolation_refusal("registered project root cannot be resolved",
            code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__}) from exc

    identity_binding = backend.worker_identity
    if (identity_binding.get("server_ownership") != "mcp_managed"
            or identity_binding.get("runtime_id") != context.runtime.runtime_id
            or identity_binding.get("worker_instance_id") != context.worker_instance_id
            or identity_binding.get("connection_epoch") != context.worker_epoch
            or identity_binding.get("server_instance_id") != ledger.server_instance_id):
        raise _session_isolation_refusal("backend Worker/server identity differs from the selected session epoch")
    endpoint_identity = context.endpoint
    if (endpoint_identity.host != "127.0.0.1" or type(endpoint_identity.port) is not int
            or not 1 <= endpoint_identity.port <= 65535
            or type(endpoint_identity.worker_epoch) is not int or endpoint_identity.worker_epoch <= 0
            or not isinstance(endpoint_identity.observed_peer, CanonicalSocket)
            or endpoint_identity.observed_peer != CanonicalSocket("127.0.0.1", endpoint_identity.port)):
        raise _session_isolation_refusal("the selected endpoint is not a bounded IPv4 loopback identity")
    port = endpoint_identity.port
    endpoint = f"127.0.0.1:{port}"
    if (backend.endpoint_key != endpoint or identity_binding.get("endpoint") != endpoint
            or context.connected_host != "127.0.0.1" or context.connected_port != port):
        raise _session_isolation_refusal("active backend/context endpoint differs from the selected loopback endpoint")

    server_identity = context.process_identity
    server_process = context.server_handle
    if (type(server_process) is not _Win32ChildProcess
            or not isinstance(server_identity, OwnedServerProcessIdentity)
            or endpoint_identity.owned_process is not server_identity
            or type(server_identity.start_epoch_ms) is not int or server_identity.start_epoch_ms <= 0
            or server_identity.birth != f"start_epoch_ms:{server_identity.start_epoch_ms}"
            or server_identity.listener_sockets != (CanonicalSocket("127.0.0.1", port),)):
        raise _session_isolation_refusal("the exact owned Server HANDLE birth/listener is incomplete")
    server_owner = _native_g2_session_launch_owner(server_process)
    worker_process = getattr(worker, "_process", None)
    worker_owner = _native_g2_session_launch_owner(worker_process)
    if (server_owner is None or worker_owner is None
            or server_owner.role != "server" or worker_owner.role != "worker"
            or server_identity.pid != server_owner.pid
            or server_owner.session_factory is not worker_owner.session_factory
            or server_owner.api is not worker_owner.api
            or server_owner.job_handle is not worker_owner.job_handle
            or server_owner.profile is not worker_owner.profile
            or server_owner.profile_sha256 != worker_owner.profile_sha256
            or server_owner.session_factory.session_id != context.session_id
            or server_owner.session_factory._api is not server_owner.api
            or server_owner.session_factory._job is not server_owner.job_handle
            or server_owner.session_factory.profile is not server_owner.profile):
        raise _session_isolation_refusal("Server/Worker do not share the exact original T037 API, factory, Job and profile")

    worker_pid = worker_owner.pid
    if (worker_process is not binding.worker_process
            or binding.worker_native_owner is not worker_owner
            or binding.worker_process_pid != worker_pid
            or type(binding.worker_process_start_epoch_ms) is not int
            or binding.worker_process_start_epoch_ms <= 0
            or binding.worker_process_argv_sha256 != worker_owner.argv_sha256
            or tuple(getattr(worker_process, "args", ())) != worker_owner.argv
            or type(getattr(worker, "generation", None)) is not int
            or binding.connection_epoch != worker.generation
            or context.worker_epoch != worker.generation):
        raise _session_isolation_refusal("the exact Worker HANDLE, launch argv, birth or attach epoch changed")

    session_home = session_state_directory(
        context.runtime.session_state_root, context.project_id, context.session_id,
    ).resolve(strict=True)
    expected_state = (session_home / "worker").resolve(strict=True)
    worker_state = getattr(worker, "state_dir", None)
    if (not isinstance(worker_state, (str, os.PathLike)) or Path(worker_state).is_symlink()
            or ntpath.normcase(str(Path(worker_state).resolve(strict=True))) != ntpath.normcase(str(expected_state))
            or binding.worker_state_dir is None
            or ntpath.normcase(binding.worker_state_dir) != ntpath.normcase(str(expected_state))):
        raise _session_isolation_refusal("the Worker state directory differs from this exact project/session")

    directories = _server_directories(session_home / "owned-server")
    expected_server_argv = build_server_command(
        directories, platform_name="win32", validate_launcher=False,
        server_np=server_identity.server_np,
    )
    if (server_owner.argv != tuple(expected_server_argv)
            or server_identity.launch_argv_sha256 != _sha256_argv(expected_server_argv)
            or server_identity.launch_argv_sha256 != server_owner.argv_sha256
            or server_identity.runtime_id != context.runtime.runtime_id
            or ntpath.normcase(server_identity.executable) != ntpath.normcase(server_owner.image_path)
            or server_identity.executable_sha256 != server_owner.image_sha256):
        raise _session_isolation_refusal("Server launch image/argv is not the exact private session/runtime command")

    api = server_owner.api
    profile = server_owner.profile
    job = server_owner.job_handle
    from ._windows_job_limits import require_limits, FLAGS
    limits = require_limits(profile)
    try:
        job_state = api.job_snapshot(job)
        job_pids = job_state.get("process_ids") if isinstance(job_state, Mapping) else None
        if (not isinstance(job_state, Mapping) or job_state.get("state") != "OBSERVED"
                or type(job_pids) is not list or len(job_pids) != len(set(job_pids))
                or any(type(pid) is not int or pid <= 1 for pid in job_pids)
                or server_owner.pid not in job_pids or worker_pid not in job_pids
                or len(job_pids) > limits.active_process_limit
                or job_state.get("process_count") != len(job_pids)
                or job_state.get("limit_flags") != FLAGS
                or job_state.get("active_process_limit") != limits.active_process_limit
                or job_state.get("process_memory_limit_bytes") != limits.process_memory_limit_bytes
                or job_state.get("job_memory_limit_bytes") != limits.job_memory_limit_bytes
                or job_state.get("kill_on_job_close") is not False
                or job_state.get("breakaway_allowed") is not False
                or job_state.get("silent_breakaway_allowed") is not False
                or not api.is_process_in_job(server_owner.process_handle, job)
                or not api.is_process_in_job(worker_owner.process_handle, job)):
            raise _session_isolation_refusal("original shared Job membership/limits do not match the exact Server and Worker owners")
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise _session_isolation_refusal("original shared Job readback is unavailable",
            code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__}) from exc

    def handle_birth(owner, label):
        birth, exited = owner.api.process_times(owner.process_handle)
        epoch_ms = birth // 10000 - 11644473600000
        if (birth != owner.birth_filetime_100ns or exited is not None
                or owner.process.poll() is not None or epoch_ms <= 0):
            raise _session_isolation_refusal(f"original {label} HANDLE birth changed or process is terminal")
        observed = process_identity(owner.pid, platform_name="nt")
        if (not isinstance(observed, Mapping) or observed.get("alive") is not True
                or observed.get("start_epoch_ms") != epoch_ms):
            raise _session_isolation_refusal(f"original {label} PID no longer matches its exact HANDLE birth")
        snap = _windows_process_snapshot(owner.pid)
        expected_command = subprocess.list2cmdline(list(owner.argv))
        if (not isinstance(snap, Mapping) or snap.get("pid") != owner.pid
                or snap.get("command") != expected_command
                or snap.get("command_sha256") != _sha256_text(expected_command)):
            raise _session_isolation_refusal(f"live {label} command differs from its sealed native launch argv")
        return epoch_ms

    server_start = handle_birth(server_owner, "Server")
    worker_start = handle_birth(worker_owner, "Worker")
    if (server_start != server_identity.start_epoch_ms
            or worker_start != binding.worker_process_start_epoch_ms):
        raise _session_isolation_refusal("Server or Worker live kernel birth differs from its attach record")
    rows = _windows_socket_rows(port)
    if not isinstance(rows, list) or len(rows) != 3:
        raise _session_isolation_refusal("Windows endpoint socket inventory contains missing or extra rows")
    listeners = [row for row in rows if isinstance(row, Mapping) and row.get("state") == "LISTEN"]
    established = [row for row in rows if isinstance(row, Mapping) and row.get("state") == "ESTABLISHED"]
    if (len(listeners) != 1 or len(established) != 2
            or listeners[0].get("pid") != server_owner.pid
            or _loopback_port(listeners[0].get("local_endpoint")) != port
            or any(row.get("pid") not in {server_owner.pid, worker_pid} for row in established)):
        raise _session_isolation_refusal("Windows endpoint is shared or its listener/client ownership changed")
    server_pairs, worker_pairs = [], []
    for row in established:
        local, remote = _row_connection(row)
        local_port, remote_port = _loopback_port(local), _loopback_port(remote)
        if local_port is None or remote_port is None:
            raise _session_isolation_refusal("an established Windows endpoint is not an IPv4 loopback socket pair")
        if row.get("pid") == server_owner.pid and local_port == port:
            server_pairs.append((local_port, remote_port))
        elif row.get("pid") == worker_pid and remote_port == port:
            worker_pairs.append((local_port, remote_port))
        else:
            raise _session_isolation_refusal("an established Windows socket is not the exact Server/Worker direction")
    if (len(server_pairs) != 1 or len(worker_pairs) != 1
            or server_pairs[0][1] != worker_pairs[0][0]
            or worker_pairs[0][1] != port):
        raise _session_isolation_refusal("the exact bidirectional Server-to-Worker IPv4 socket pair is absent")
    if (context.server_handle is not server_process or context.process_identity is not server_identity
            or context.worker is not worker or backend.worker is not worker
            or backend.service is not service or worker.generation != context.worker_epoch):
        raise _session_isolation_refusal("active SessionRuntimeContext changed during endpoint verification")
    if handle_birth(server_owner, "Server") != server_start or handle_birth(worker_owner, "Worker") != worker_start:
        raise _session_isolation_refusal("Server or Worker HANDLE birth changed during endpoint verification")
    return {
        "verified": True,
        "proof_scope": "active SessionRuntimeContext + sealed original T037 Server/Worker HANDLEs + shared configured Job + exact Windows IPv4 loopback listener and bidirectional Worker socket pair",
        "project_id": context.project_id,
        "session_id": context.session_id,
        "runtime_id": context.runtime.runtime_id,
        "worker_instance_id": context.worker_instance_id,
        "worker_epoch": context.worker_epoch,
        "server_pid": server_owner.pid,
        "server_birth_filetime_100ns": server_owner.birth_filetime_100ns,
        "server_start_epoch_ms": server_start,
        "worker_pid": worker_pid,
        "worker_birth_filetime_100ns": worker_owner.birth_filetime_100ns,
        "worker_start_epoch_ms": worker_start,
        "endpoint": endpoint,
        "listener": dict(listeners[0]),
        "worker_client_ephemeral_port": worker_pairs[0][0],
        "established_client_pids": sorted({row["pid"] for row in established}),
        "worker_health_rpc_performed": False,
    }


def verify_session_owned_server(context: Any, *, backend: Any, worker: Any,
                                service: Any) -> dict[str, Any]:
    """Prove an exact registered native session owns the live COMSOL/Worker socket pair.

    This route uses the daemon's immutable SessionRuntimeContext, retained
    Server Popen and OwnedServerProcessIdentity. Windows T037 CreateProcess
    Windows support uses only the sealed original T037 API/HANDLE owner and
    existing read-only process/socket probes. This route does not consult a
    process global, environment variable, or
    request-supplied receipt. The Worker UUID, epoch, startup Popen and state
    directory are bound through the exact original in-process Worker object;
    no health RPC is sent before the requested model operation.
    """
    try:
        _require_isolation_adapter()
        if sys.platform == "win32" or os.name == "nt":
            return _verify_windows_session_owned_server(
                context, backend=backend, worker=worker, service=service,
            )
        if sys.platform != "darwin" or os.name == "nt":
            raise _session_isolation_refusal(
                "the session-owned G2 process adapter is unavailable on this platform",
                code="UNSUPPORTED_PLATFORM",
                details={"platform": sys.platform, "platform_scope": "macOS_only"},
            )
        from ._platform_process import process_identity
        from ._runtime_installation import runtime_id_for_root
        from ._session_server import (
            _server_directories, build_server_command, owned_server_preferences_directory,
        )
        from ._session_context import (
            CanonicalSocket, OwnedServerProcessIdentity, SessionRuntimeContext,
            session_state_directory,
        )

        if not isinstance(context, SessionRuntimeContext):
            raise _session_isolation_refusal("an exact active SessionRuntimeContext is required")
        if context.backend is not backend or context.worker is not worker or context.service is not service:
            raise _session_isolation_refusal("active SessionRuntimeContext does not match this exact backend/Worker/service")
        if (context.server_ownership != "mcp_managed" or context.server_started_by_mcp is not True
                or context.client_connected is not True or context.client_retired is not False):
            raise _session_isolation_refusal("the selected session is not an active MCP-managed Server binding")
        if (not isinstance(context.project_id, str) or not context.project_id
                or not isinstance(context.session_id, str) or not context.session_id
                or not isinstance(context.worker_instance_id, str) or not context.worker_instance_id
                or not isinstance(context.runtime.runtime_id, str) or not context.runtime.runtime_id):
            raise _session_isolation_refusal("the selected session/project/runtime identity is incomplete")
        if (getattr(getattr(service, "ledger", None), "session_id", None) != context.session_id
                or getattr(getattr(service, "ledger", None), "server_ownership", None) != "mcp_managed"):
            raise _session_isolation_refusal("active service ledger does not match the selected owned session")
        if (not hasattr(backend, "worker_identity") or not isinstance(backend.worker_identity, Mapping)
                or backend.service is not service or backend.worker is not worker):
            raise _session_isolation_refusal("the active ManagedBackend binding is incomplete")
        from ._managed_backend import _RuntimeObservationBinding
        worker_binding = getattr(backend, "_runtime_observation_binding", None)
        if (type(worker_binding) is not _RuntimeObservationBinding
                or worker_binding.worker is not worker or worker_binding.service is not service
                or worker_binding.runtime_id != context.runtime.runtime_id
                or worker_binding.worker_instance_id != context.worker_instance_id
                or worker_binding.connection_epoch != context.worker_epoch
                or worker_binding.server_instance_id != service.ledger.server_instance_id
                or worker_binding.endpoint != f"127.0.0.1:{context.endpoint.port}"
                or worker_binding.legacy is not False
                or worker_binding.installation_runtime_id != runtime_id_for_root(context.runtime.installation_root)):
            raise _session_isolation_refusal(
                "the Worker UUID/epoch is not bound to this exact Worker object and installed runtime",
            )
        try:
            if Path(context.project_root).resolve() != Path(backend.project_root).resolve():
                raise _session_isolation_refusal("the active backend project root differs from the registered session")
        except (OSError, TypeError, ValueError) as exc:
            raise _session_isolation_refusal(
                "the registered session project root cannot be resolved",
                code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__},
            ) from exc
        identity_binding = backend.worker_identity
        if (identity_binding.get("server_ownership") != "mcp_managed"
                or identity_binding.get("runtime_id") != context.runtime.runtime_id
                or identity_binding.get("worker_instance_id") != context.worker_instance_id
                or identity_binding.get("connection_epoch") != context.worker_epoch
                or identity_binding.get("server_instance_id") != service.ledger.server_instance_id):
            raise _session_isolation_refusal("backend Worker/server binding differs from the selected session epoch")

        endpoint_identity = context.endpoint
        if (endpoint_identity.host != "127.0.0.1" or type(endpoint_identity.port) is not int
                or not 1 <= endpoint_identity.port <= 65535 or type(endpoint_identity.worker_epoch) is not int
                or endpoint_identity.worker_epoch <= 0 or not isinstance(endpoint_identity.observed_peer, CanonicalSocket)):
            raise _session_isolation_refusal("the selected session endpoint is not a bounded IPv4 loopback identity")
        port = endpoint_identity.port
        endpoint = f"127.0.0.1:{port}"
        if (backend.endpoint_key != endpoint or identity_binding.get("endpoint") != endpoint
                or context.connected_host != "127.0.0.1" or context.connected_port != port):
            raise _session_isolation_refusal("active backend/context endpoint differs from the selected loopback endpoint")

        server_identity = context.process_identity
        server_process = context.server_handle
        if (not isinstance(server_identity, OwnedServerProcessIdentity)
                or endpoint_identity.owned_process is not server_identity
                or server_identity.start_epoch_ms is None
                or server_identity.birth != f"start_epoch_ms:{server_identity.start_epoch_ms}"
                or server_identity.listener_sockets != (CanonicalSocket("127.0.0.1", port),)
                or not server_identity.attests_peer(endpoint_identity.observed_peer)
                or endpoint_identity.observed_peer != CanonicalSocket("127.0.0.1", port)):
            raise _session_isolation_refusal("the exact owned Server birth/listener does not attest the selected endpoint")
        session_home = session_state_directory(
            context.runtime.session_state_root, context.project_id, context.session_id,
        ).resolve(strict=True)
        server_directories = _server_directories(session_home / "owned-server")
        server_command = build_server_command(
            server_directories, platform_name="darwin", validate_launcher=False,
            server_np=server_identity.server_np,
        )
        if (server_identity.runtime_id != context.runtime.runtime_id
                or server_identity.launch_argv_sha256 != _sha256_argv(server_command)):
            raise _session_isolation_refusal(
                "the owned Server launch record is not bound to this runtime and exact command",
            )
        try:
            preferences = owned_server_preferences_directory(
                context.runtime, context.project_id, context.session_id,
                server_identity, platform_name="darwin",
            )
        except Exception as exc:
            raise _session_isolation_refusal(
                "the owned Server identity does not resolve to this exact session/runtime installation",
                details={"cause_type": type(exc).__name__},
            ) from exc
        expected_preferences = server_directories.preferences.resolve(strict=True)
        executable = Path(server_identity.executable).resolve(strict=True)
        source_executable = (Path(context.runtime.installation_root).resolve(strict=True)
                             / "bin" / "comsol").resolve(strict=True)
        private_executable_sha = _sha256_file(executable) if executable.is_file() else None
        source_executable_sha = _sha256_file(source_executable) if source_executable.is_file() else None
        if (preferences != expected_preferences or executable != Path(server_command[0]).resolve(strict=True)
                or private_executable_sha is None or source_executable_sha is None
                or private_executable_sha != source_executable_sha
                or private_executable_sha != server_identity.executable_sha256):
            raise _session_isolation_refusal(
                "the private Server executable differs from the runtime/session launcher sealed at process birth",
            )
        _session_popen_alive(server_process, server_identity.pid, label="Server")
        try:
            server_birth_before = process_identity(server_identity.pid)
        except Exception as exc:
            raise _session_isolation_refusal(
                "the Server kernel birth could not be read", code="ISOLATION_PROOF_UNAVAILABLE",
                details={"cause_type": type(exc).__name__},
            ) from exc
        if (not isinstance(server_birth_before, Mapping) or server_birth_before.get("alive") is not True
                or server_birth_before.get("start_epoch_ms") != server_identity.start_epoch_ms):
            raise _session_isolation_refusal("the original Server Popen no longer matches its kernel birth")
        argv = getattr(server_process, "args", None)
        if (type(argv) not in (list, tuple) or not argv
                or any(type(arg) is not str or "\x00" in arg for arg in argv)):
            raise _session_isolation_refusal("the original Server Popen has no exact launcher argv")
        if list(argv) != server_command or _sha256_argv(argv) != server_identity.launch_argv_sha256:
            raise _session_isolation_refusal("the original Server Popen command differs from the exact owned runtime/session launch")

        worker_process = getattr(worker, "_process", None)
        worker_pid = getattr(worker_process, "pid", None)
        _session_popen_alive(worker_process, worker_pid, label="Worker")
        worker_state = getattr(worker, "state_dir", None)
        expected_worker_state = session_home / "worker"
        if (not isinstance(worker_state, (str, os.PathLike))
                or Path(worker_state).is_symlink()
                or Path(worker_state).resolve(strict=True) != expected_worker_state.resolve(strict=True)):
            raise _session_isolation_refusal("the exact Worker state directory differs from this project/session")
        if (worker_binding.worker_process is not worker_process
                or worker_binding.worker_process_pid != worker_pid
                or type(worker_binding.worker_process_start_epoch_ms) is not int
                or worker_binding.worker_process_start_epoch_ms <= 0
                or worker_binding.worker_state_dir != str(expected_worker_state.resolve(strict=True))
                or type(getattr(worker, "generation", None)) is not int
                or worker_binding.connection_epoch != worker.generation):
            raise _session_isolation_refusal("the retained Worker process/state/connection epoch differs from its fixed attach binding")
        worker_args = getattr(worker_process, "args", None)
        if (type(worker_args) not in (list, tuple)
                or any(type(arg) is not str or "\x00" in arg for arg in worker_args)
                or _sha256_argv(worker_args) != worker_binding.worker_process_argv_sha256):
            raise _session_isolation_refusal("the exact Worker Popen startup command changed after attach")
        owner = getattr(worker, "_coupled_admission_owner", None)
        if owner is not None:
            from tools.w24_coupled_admission import require_admission_owner
            owner = require_admission_owner(owner)
            owner_server = getattr(owner, "server", None)
            owner_handles = getattr(owner, "handles", None)
            if (owner_server is None or owner_server.worker is not worker
                    or owner_server.proc is not server_process or owner_server.port != port
                    or not isinstance(owner_handles, Mapping)
                    or set(owner_handles) != {"server", "worker"}
                    or owner_handles.get("server") is not server_process
                    or owner_handles.get("worker") is not worker_process):
                raise _session_isolation_refusal("coupled admission owner differs from the selected session Popen/endpoint")
            owner.before_effect("managed_G2_session_process_birth", 2.0)
        _verify_macos_live_command(worker_pid, worker_args, label="Worker",
                                   timeout_s=2.0, admission_owner=owner)
        if getattr(worker, "generation", None) != context.worker_epoch:
            raise _session_isolation_refusal("the exact Worker object has a different connection epoch")
        try:
            worker_birth_before = process_identity(worker_pid)
        except Exception as exc:
            raise _session_isolation_refusal(
                "the Worker kernel birth could not be read", code="ISOLATION_PROOF_UNAVAILABLE",
                details={"cause_type": type(exc).__name__},
            ) from exc
        worker_start = worker_birth_before.get("start_epoch_ms") if isinstance(worker_birth_before, Mapping) else None
        if (not isinstance(worker_birth_before, Mapping) or worker_birth_before.get("alive") is not True
                or type(worker_start) is not int or worker_start <= 0
                or worker_start != worker_binding.worker_process_start_epoch_ms):
            raise _session_isolation_refusal("the exact Worker Popen has no live kernel birth")

        if owner is not None:
            owner.wait_cap(1.01)
        rows = _socket_rows(port, admission_owner=owner) if owner is not None else _socket_rows(port)
        if not isinstance(rows, list) or len(rows) != 3:
            raise _session_isolation_refusal("the endpoint socket inventory contains missing or extra client rows")
        listeners = [row for row in rows if isinstance(row, Mapping) and row.get("state") == "LISTEN"]
        established = [row for row in rows if isinstance(row, Mapping) and row.get("state") == "ESTABLISHED"]
        if (len(listeners) != 1 or len(established) != 2
                or listeners[0].get("pid") != server_identity.pid
                or listeners[0].get("endpoint") != endpoint
                or listeners[0].get("local_endpoint") not in (None, endpoint)
                or any(row.get("pid") not in {server_identity.pid, worker_pid} for row in established)):
            raise _session_isolation_refusal("the endpoint is shared or its exact listener/client ownership changed")
        server_pairs: list[tuple[int, int]] = []
        worker_pairs: list[tuple[int, int]] = []
        for row in established:
            local, remote = _row_connection(row)
            local_port = _loopback_port(local)
            remote_port = _loopback_port(remote)
            if local_port is None or remote_port is None:
                raise _session_isolation_refusal("an established endpoint is not an IPv4 loopback socket pair")
            if row.get("pid") == server_identity.pid and local_port == port:
                server_pairs.append((local_port, remote_port))
            elif row.get("pid") == worker_pid and remote_port == port:
                worker_pairs.append((local_port, remote_port))
            else:
                raise _session_isolation_refusal("an established socket is not the exact Server/Worker direction")
        if (len(server_pairs) != 1 or len(worker_pairs) != 1
                or server_pairs[0][1] != worker_pairs[0][0]
                or worker_pairs[0][1] != port):
            raise _session_isolation_refusal("the exact bidirectional Server-to-Worker socket pair is not present")

        # Close the observation window against PID reuse, handle replacement,
        # Worker epoch drift, or a listener/client swap during the read.
        _session_popen_alive(server_process, server_identity.pid, label="Server")
        _session_popen_alive(worker_process, worker_pid, label="Worker")
        if (context.server_handle is not server_process or context.process_identity is not server_identity
                or context.worker is not worker or backend.worker is not worker
                or backend.service is not service or getattr(worker, "generation", None) != context.worker_epoch):
            raise _session_isolation_refusal("the active session binding changed during endpoint verification")
        if owner is not None:
            owner.before_effect("managed_G2_session_process_birth_recheck", 2.0)
        try:
            server_birth_after = process_identity(server_identity.pid)
            worker_birth_after = process_identity(worker_pid)
        except Exception as exc:
            raise _session_isolation_refusal(
                "process birth changed or became unavailable during endpoint verification",
                code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__},
            ) from exc
        if (not isinstance(server_birth_after, Mapping) or server_birth_after.get("alive") is not True
                or server_birth_after.get("start_epoch_ms") != server_identity.start_epoch_ms
                or not isinstance(worker_birth_after, Mapping) or worker_birth_after.get("alive") is not True
                or worker_birth_after.get("start_epoch_ms") != worker_start):
            raise _session_isolation_refusal("Server or Worker kernel birth changed during endpoint verification")
        _verify_macos_live_command(worker_pid, worker_args, label="Worker",
                                   timeout_s=2.0, admission_owner=owner)
        if owner is not None:
            owner.wait_cap(1.01)
        return {
            "verified": True,
            "proof_scope": "active SessionRuntimeContext + same ManagedBackend/Worker/service + original Server Popen/kernel birth + exact IPv4 loopback listener + live bidirectional Worker socket pair",
            "project_id": context.project_id,
            "session_id": context.session_id,
            "runtime_id": context.runtime.runtime_id,
            "worker_instance_id": context.worker_instance_id,
            "worker_epoch": context.worker_epoch,
            "server_pid": server_identity.pid,
            "server_birth": server_identity.birth,
            "server_start_epoch_ms": server_identity.start_epoch_ms,
            "worker_pid": worker_pid,
            "worker_start_epoch_ms": worker_start,
            "endpoint": endpoint,
            "listener": dict(listeners[0]),
            "worker_client_ephemeral_port": worker_pairs[0][0],
            "established_client_pids": sorted({row["pid"] for row in established}),
            "worker_health_rpc_performed": False,
        }
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise _session_isolation_refusal(
            "owned-session isolation proof failed before target operation dispatch",
            code=exc.code, details={**exc.details, "cause_stage": exc.stage},
        ) from exc
    except Exception as exc:
        raise _session_isolation_refusal(
            "owned-session process/socket proof is unavailable before target operation dispatch",
            code="ISOLATION_PROOF_UNAVAILABLE", details={"cause_type": type(exc).__name__},
        ) from exc


def configured_receipt() -> Path | None:
    value = os.environ.get("COMSOL_MCP_ISOLATION_RECEIPT", "").strip()
    return Path(value).expanduser() if value else None
