#!/usr/bin/env python3
"""Guarded one-shot COMSOL 6.4 RemoteAddrValve proof on macOS.

This is a companion to :mod:`phase3_owned_runtime`.  It uses the reviewed
stock ``RemoteAddrValve`` proposal, keeps the installed ``server.xml`` inode
and metadata, and records a fresh task-owned run.  The API probe authenticates
the real COMSOL Java API through ``/webbridge/websocket`` from localhost, then
requires an HTTP 403 for the same API route addressed through the current
non-loopback IPv4 address.  No model operation is sent.

``run --apply-valve`` is the bounded convenience command: it applies the
proposal, starts a private server, performs both probes, and always stops the
owned process and restores the original bytes before returning.  The separate
``start``, ``probe``, and ``cleanup`` commands are retained for a replayable
operator flow; ``start`` deliberately leaves the server running until
``cleanup`` is called.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any, Iterable

# Make the documented ``python tools/<script>.py`` entry point resolve the
# repository package exactly like ``python -m tools.<script>`` does.
_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from comsol_mcp._java_worker import JavaWorkerPaths, JavaWorkerError, PersistentJavaWorker
from tools import phase3_owned_runtime as base


ROOT = _SCRIPT_ROOT
COMSOL_ROOT = base.COMSOL_ROOT
COMSOL_LAUNCHER = base.COMSOL_LAUNCHER
JDK_HOME = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
PRIVATE_ROOT = base.PRIVATE_ROOT
PUBLIC_ROOT = base.PUBLIC_ROOT
SERVER_XML = COMSOL_ROOT / "bin" / "servers" / "webbridge" / "conf" / "server.xml"
# Immutable read-only proposal prepared from the actual ServerApplication
# webbridge Catalina base. The prior tomcat proposal and failed runtime stay
# as historical evidence and are intentionally not reused.
PROPOSAL_DIR = PRIVATE_ROOT / "g2-valve-proposal-webbridge-20260919T232153Z"
PROPOSAL_ORIGINAL = PROPOSAL_DIR / "server.original.xml"
PROPOSAL_VALVE = PROPOSAL_DIR / "server.remote_addr_valve.xml"
PORT = 56389
API_ROUTE = "/webbridge/websocket"
EXPECTED_ORIGINAL_SHA256 = "95478d7624e778c694c714d9215797c0c0076fd707a4eedf2bedbaa6bafe6625"
EXPECTED_VALVE_SHA256 = "1a029e30e3b0218adfdc8ac01e15379a68ae48bb2fb658bdf397c0ba6de5c357"
EXPECTED_VALVE_CLASS = "org.apache.catalina.valves.RemoteAddrValve"
EXPECTED_ALLOW = r"127\.\d+\.\d+\.\d+|::1|0:0:0:0:0:0:0:1"
_ACCESS_LOG_LINE = re.compile(
    r'^(?P<source>\S+)\s+\S+\s+\S+\s+\[(?P<timestamp>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<route>\S+)\s+HTTP/(?P<http_version>[^"]+)"\s+'
    r'(?P<status>\d{3})(?:\s+(?P<bytes>\S+))?(?:\s+.*)?$'
)


class GuardError(RuntimeError):
    """A fail-closed lifecycle or probe error."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata_residual(metadata: dict[str, Any]) -> dict[str, Any]:
    """Use current active-file provenance wording in new receipts."""
    residual = base._metadata_residual(metadata)
    if residual.get("name") == base.KNOWN_RESIDUAL_XATTR_NAME:
        residual["source"] = (
            "introduced by the first in-place write to the active webbridge "
            "server.xml during the approved lifecycle; xattr removal returned EPERM"
        )
    return residual


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _active_valves(data: bytes) -> list[dict[str, str]]:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise GuardError("reviewed server.xml is not valid XML") from exc
    rows: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Valve":
            rows.append({str(key): str(value) for key, value in element.attrib.items()})
    return rows


def _proposal_bytes() -> tuple[bytes, bytes]:
    if not PROPOSAL_ORIGINAL.is_file() or not PROPOSAL_VALVE.is_file():
        raise GuardError("reviewed RemoteAddrValve proposal files are missing")
    original = PROPOSAL_ORIGINAL.read_bytes()
    proposed = PROPOSAL_VALVE.read_bytes()
    if _sha256_bytes(original) != EXPECTED_ORIGINAL_SHA256:
        raise GuardError("reviewed original XML hash does not match the approved baseline")
    if _sha256_bytes(proposed) != EXPECTED_VALVE_SHA256:
        raise GuardError("reviewed RemoteAddrValve XML hash does not match the approved proposal")
    # The installed COMSOL file is CRLF encoded.  Pin the complete reviewed
    # block including its line endings so a proposal cannot smuggle unrelated
    # whitespace or encoding changes into the one approved edit.
    newline = b"\r\n"
    marker = newline.join((
        b'      <!-- Uncomment to restrict access to localhost',
        b'      <Valve className="org.apache.catalina.valves.RemoteAddrValve"',
        b'             allow="127\\.\\d+\\.\\d+\\.\\d+|::1|0:0:0:0:0:0:0:1"/>',
        b'      -->',
    ))
    replacement = newline.join((
        b'      <Valve className="org.apache.catalina.valves.RemoteAddrValve"',
        b'             allow="127\\.\\d+\\.\\d+\\.\\d+|::1|0:0:0:0:0:0:0:1"/>',
    ))
    if original.count(marker) != 1 or original.replace(marker, replacement, 1) != proposed:
        raise GuardError("RemoteAddrValve proposal differs from the original outside the reviewed block")
    original_remote = [
        valve for valve in _active_valves(original)
        if valve.get("className") == EXPECTED_VALVE_CLASS
    ]
    if original_remote:
        raise GuardError("approved original server.xml unexpectedly has an active RemoteAddrValve")
    valves = _active_valves(proposed)
    remote_valves = [
        valve for valve in valves
        if valve.get("className") == EXPECTED_VALVE_CLASS
    ]
    if len(remote_valves) != 1 or remote_valves[0].get("allow") != EXPECTED_ALLOW:
        raise GuardError("approved proposal does not contain exactly the documented localhost RemoteAddrValve")
    return original, proposed


def _new_run_dir() -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = PRIVATE_ROOT / f"g2-valve-runtime-{stamp}"
    run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in ("prefs", "tmp", "recovery", "logs", "control-private"):
        (run_dir / name).mkdir(mode=0o700)
    return run_dir


def _write_public(path: Path, value: dict[str, Any]) -> None:
    sanitized = dict(value)
    for key in ("private_receipt", "private_process_receipt", "private_probe_receipt"):
        sanitized.pop(key, None)
    base._write_json(path, sanitized, mode=0o644)


def _public_path(run_dir: Path) -> Path:
    return PUBLIC_ROOT / f"{run_dir.name}.json"


def _wildcard_or_loopback_listener(records: list[dict[str, Any]], launcher_pid: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not records:
        raise GuardError("owned server has not opened its listener")
    if any(int(row.get("pid", -1)) != launcher_pid and not base._is_descendant(int(row.get("pid", -1)), launcher_pid) for row in records):
        raise GuardError("listener process is not a child of the owned launcher")
    pids = {int(row["pid"]) for row in records}
    if len(pids) != 1:
        raise GuardError("more than one process owns the task listener")
    listener_pid = next(iter(pids))
    identity = base._process_snapshot(listener_pid)
    if identity is None or not base._server_command(identity["command"]):
        raise GuardError("listener process identity is not a COMSOL server")
    return identity, records


def _socket_rows(port: int = PORT) -> list[dict[str, Any]]:
    result = base._run([base.LSOF, "-nP", f"-iTCP:{port}"])
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise GuardError("could not inspect COMSOL listener and client sockets")
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
        state_index = next((index for index, field in enumerate(fields) if field in {"(LISTEN)", "(ESTABLISHED)"}), -1)
        endpoint = fields[state_index - 1] if state_index > 0 else next((field for field in fields if "->" in field), "")
        state = "ESTABLISHED" if "(ESTABLISHED)" in fields else "LISTEN" if "(LISTEN)" in fields else "OTHER"
        local, remote = endpoint.split("->", 1) if "->" in endpoint else (endpoint, None)
        rows.append({"pid": pid, "endpoint": endpoint, "local_endpoint": local, "remote_endpoint": remote, "state": state})
    return rows


def _public_socket_rows(rows: list[dict[str, Any]], owned_pid: int | None = None) -> list[dict[str, Any]]:
    owned_pids: set[int] | None = None
    if owned_pid is not None:
        owned_pids = {owned_pid}
    return [
        {key: row.get(key) for key in ("pid", "endpoint", "local_endpoint", "remote_endpoint", "state")}
        for row in rows
        if owned_pids is None or row.get("pid") in owned_pids
    ]


def _endpoint_is_loopback(endpoint: Any) -> bool:
    if not isinstance(endpoint, str) or not endpoint:
        return False
    host = endpoint.rsplit(":", 1)[0].strip("[]").lower()
    return host == "127.0.0.1" or host == "::1"


def _assert_authenticated_socket_pair(
    rows: list[dict[str, Any]], *, server_pid: int, worker_pid: int, port: int,
) -> list[dict[str, Any]]:
    """Require the Java Worker and COMSOL server to be the observed client pair."""
    if type(worker_pid) is not int or worker_pid <= 1 or worker_pid == server_pid:
        raise GuardError("authenticated localhost Worker identity is unavailable")
    relevant = [row for row in rows if row.get("state") in {"LISTEN", "ESTABLISHED"}]
    listeners = [row for row in relevant if row.get("state") == "LISTEN"]
    if len(listeners) != 1 or listeners[0].get("pid") != server_pid:
        raise GuardError("localhost API probe did not observe one listener owned by the COMSOL server")
    established = [row for row in relevant if row.get("state") == "ESTABLISHED"]
    if not established:
        raise GuardError("localhost API probe did not observe an established Java API socket")
    allowed = {server_pid, worker_pid}
    if any(row.get("pid") not in allowed for row in established):
        raise GuardError("localhost API probe observed an unapproved client socket")
    server_rows = [row for row in established if row.get("pid") == server_pid]
    worker_rows = [row for row in established if row.get("pid") == worker_pid]
    if not server_rows or not worker_rows:
        raise GuardError("localhost API probe did not observe both directions of the Worker socket")
    server_remote_ports: set[int] = set()
    worker_local_ports: set[int] = set()
    for row in established:
        local = row.get("local_endpoint")
        remote = row.get("remote_endpoint")
        if not _endpoint_is_loopback(local) or not _endpoint_is_loopback(remote):
            raise GuardError("localhost API probe observed a non-loopback Java API peer")
        try:
            local_port = int(str(local).rsplit(":", 1)[1])
            remote_port = int(str(remote).rsplit(":", 1)[1])
        except (ValueError, IndexError):
            raise GuardError("localhost API probe observed an invalid socket endpoint")
        if row.get("pid") == server_pid:
            if local_port != port:
                raise GuardError("COMSOL server socket does not own the recorded API port")
            server_remote_ports.add(remote_port)
        elif row.get("pid") == worker_pid:
            if remote_port != port:
                raise GuardError("Worker socket is not connected to the recorded COMSOL API port")
            worker_local_ports.add(local_port)
    if not server_remote_ports or server_remote_ports != worker_local_ports:
        raise GuardError("localhost API probe did not observe the exact bidirectional Worker socket pair")
    return _public_socket_rows(relevant, owned_pid=None)


def _current_nonloopback_ipv4() -> str:
    candidates: list[str] = []
    for interface in ("en0", "en1", "en2", "bridge0"):
        result = base._run(["/usr/sbin/ipconfig", "getifaddr", interface])
        if result.returncode == 0:
            candidates.append(result.stdout.strip())
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback and not address.is_link_local:
            return candidate
    raise GuardError("no current non-loopback IPv4 address was found on this Mac")


def _endpoint_tuple(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, tuple) or len(value) < 2:
        return None
    try:
        return {"host": str(value[0]), "port": int(value[1])}
    except (TypeError, ValueError):
        return None


def _websocket_api_probe(address: str, *, source_address: str | None = None) -> dict[str, Any]:
    """Probe the COMSOL API WebSocket route with an optional explicit source bind.

    The returned record contains only endpoint/status facts. In particular,
    response bodies, headers, credentials, and exception text are discarded.
    """
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {API_ROUTE} HTTP/1.1\r\n"
        f"Host: {address}:{PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")
    local_endpoint: dict[str, Any] | None = None
    peer_endpoint: dict[str, Any] | None = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(5.0)
            if source_address is not None:
                connection.bind((source_address, 0))
            connection.connect((address, PORT))
            local_endpoint = _endpoint_tuple(connection.getsockname())
            peer_endpoint = _endpoint_tuple(connection.getpeername())
            connection.sendall(request)
            received = bytearray()
            while b"\r\n\r\n" not in received and len(received) < 65536:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
    except OSError as exc:
        return {
            "status": "CONNECT_ERROR",
            "address": address,
            "route": API_ROUTE,
            "source_bind_requested": source_address,
            "local_endpoint": local_endpoint,
            "peer_endpoint": peer_endpoint,
            "http_status": None,
            "error_class": type(exc).__name__,
        }
    header = bytes(received).split(b"\r\n\r\n", 1)[0]
    first_line = header.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    match = re.match(r"^HTTP/\d(?:\.\d)?\s+(\d{3})(?:\s+(.*))?$", first_line)
    if not match:
        return {
            "status": "INVALID_RESPONSE",
            "address": address,
            "route": API_ROUTE,
            "source_bind_requested": source_address,
            "local_endpoint": local_endpoint,
            "peer_endpoint": peer_endpoint,
            "http_status": None,
            "error_class": "HTTP_STATUS_LINE_MISSING",
        }
    status = int(match.group(1))
    return {
        "status": "HTTP_RESPONSE",
        "address": address,
        "route": API_ROUTE,
        "source_bind_requested": source_address,
        "local_endpoint": local_endpoint,
        "peer_endpoint": peer_endpoint,
        "http_status": status,
        "status_line_class": match.group(2) or "",
    }


class ProbeFailure(GuardError):
    """A probe failed after collecting a sanitized proof record."""

    def __init__(self, message: str, proof: dict[str, Any]):
        super().__init__(message)
        self.proof = proof


def _worker_failure_record(exc: JavaWorkerError) -> dict[str, Any]:
    failure = exc.failure if isinstance(exc.failure, dict) else {}
    failure_text = " ".join(
        str(failure.get(key, "")) for key in ("code", "message", "detail", "error")
    ).strip()
    explicit_http_403 = bool(
        re.search(
            r"(?:\bhttp(?:/\d(?:\.\d)?)?|\bstatus(?:[_\s-]?code)?|\bresponse(?:[_\s-]?status|\s+code)?|\bcode)\s*[:=_\s-]*403\b"
            r"|\b403\s+forbidden\b",
            failure_text,
            flags=re.IGNORECASE,
        )
    )
    return {
        "status": "REJECTED",
        "error_code": str(failure.get("code") or "JAVA_API_CONNECT_FAILED"),
        "failure_class": type(exc).__name__,
        "failure_text_sha256": _sha256_bytes(failure_text.encode("utf-8")) if failure_text else None,
        # Require a standalone HTTP status number; this avoids treating a
        # port, PID, or unrelated diagnostic number containing 403 as proof.
        "http_403_explicitly_observed_in_worker_error": explicit_http_403,
        "http_failure_category": "HTTP_403" if explicit_http_403 else "UNCLASSIFIED",
    }


def _worker_probe(
    run_dir: Path,
    paths: JavaWorkerPaths,
    *,
    label: str,
    address: str,
    server_pid: int,
    expect_loopback: bool,
) -> dict[str, Any]:
    """Run one fresh Worker probe and capture lsof before disconnect/close."""
    worker = PersistentJavaWorker(
        paths,
        state_dir=run_dir / "control-private" / f"worker-{label}",
    )
    result: dict[str, Any] = {
        "status": "NOT_RUN",
        "address": address,
        "port": PORT,
        "authenticated": False,
        "socket_rows_before_disconnect": [],
        "credentials_source": "fresh Worker with the run-owned private prefs; credential material not recorded",
    }
    connected = False
    worker_pid: int | None = None
    connect_request_id = f"g2-valve-{label}-connect"
    result["java_request_id"] = connect_request_id
    try:
        started = worker.start(startup_timeout_s=30.0)
        result["worker_start_status"] = started.get("status") if isinstance(started, dict) else None
        try:
            worker.client().connect(PORT, address, request_id=connect_request_id)
            metadata = worker.runtime_metadata()
            worker_pid = metadata.get("pid") if type(metadata.get("pid")) is int else None
            connected = bool(metadata.get("connected"))
            result.update({
                "authenticated": connected,
                "worker_pid": worker_pid,
                "server_reported_endpoint": metadata.get("server"),
            })
            if connected and metadata.get("server") == f"{address}:{PORT}":
                result["status"] = "PASS" if expect_loopback else "UNEXPECTED_SUCCESS"
            else:
                result["status"] = "FAIL"
                result["failure_class"] = "ENDPOINT_IDENTITY_MISMATCH"
        except JavaWorkerError as exc:
            failure = _worker_failure_record(exc)
            result.update(failure)
            result["status"] = "FAIL" if expect_loopback else "REJECTED"
        except Exception as exc:
            result.update({"status": "ERROR", "failure_class": type(exc).__name__})
    except JavaWorkerError as exc:
        result.update({"status": "ERROR", "worker_start": _worker_failure_record(exc)})
    finally:
        try:
            rows = _socket_rows()
            result["socket_rows_before_disconnect"] = _public_socket_rows(rows)
            if connected and expect_loopback:
                try:
                    result["socket_pair"] = _assert_authenticated_socket_pair(
                        rows, server_pid=server_pid, worker_pid=worker_pid, port=PORT,
                    )
                    result["socket_pair_status"] = "PASS"
                except GuardError as exc:
                    result["socket_pair_status"] = "FAIL"
                    result["socket_pair_error"] = str(exc)
            elif connected and not expect_loopback:
                result["socket_pair_status"] = "RECORDED_ONLY"
        except GuardError as exc:
            result["socket_rows_error"] = type(exc).__name__
        if connected:
            try:
                result["disconnect"] = worker.client().disconnect(
                    request_id=f"g2-valve-{label}-disconnect",
                )
            except Exception as exc:
                result["disconnect_error_class"] = type(exc).__name__
        worker.close()
    return result


def _access_log_path(run_dir: Path) -> Path:
    """Return the one fresh run-owned Tomcat access log."""
    logs_dir = (run_dir / "prefs" / "tomcat" / "logs").resolve()
    candidates = sorted(logs_dir.glob("mphserverlocalhost_access_log.*.txt"))
    candidates = [path for path in candidates if path.is_file() and not path.is_symlink()]
    if len(candidates) != 1:
        raise GuardError("run-owned Tomcat access log is not uniquely identifiable")
    return candidates[0].resolve()


def _access_log_snapshot(path: Path) -> dict[str, Any]:
    """Capture an inode-bound prefix offset and hash without recording lines."""
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise GuardError("run-owned Tomcat access log is not a regular file")
    info = path.stat()
    size = int(info.st_size)
    with path.open("rb") as handle:
        data = handle.read(size)
    if len(data) < size:
        raise GuardError("run-owned Tomcat access log changed while being read")
    data = data[:size]
    after = path.stat()
    if int(after.st_ino) != int(info.st_ino):
        raise GuardError("run-owned Tomcat access log inode changed while being read")
    return {
        "path": str(path),
        "inode": int(info.st_ino),
        "offset": size,
        "sha256": _sha256_bytes(data),
        "captured_at": _utc_now(),
    }


def _parse_access_log_segment(segment: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Parse sanitized access facts; never retain raw access-log lines."""
    entries: list[dict[str, Any]] = []
    parse_error = False
    for raw_line in segment.splitlines():
        if not raw_line.strip():
            continue
        line = raw_line.decode("utf-8", errors="replace")
        match = _ACCESS_LOG_LINE.match(line)
        if match is None:
            parse_error = True
            continue
        entries.append({
            "peer": match.group("source"),
            "method": match.group("method"),
            "route": match.group("route"),
            "http_status": int(match.group("status")),
        })
    return entries, parse_error


def _correlate_access_log(
    path: Path,
    before: dict[str, Any],
    *,
    nonloopback: str,
    java_request_id: str,
) -> dict[str, Any]:
    """Correlate only the nonloopback Java-connect interval before raw HTTP."""
    path = path.resolve()
    started_at = str(before.get("captured_at") or _utc_now())
    proof: dict[str, Any] = {
        "status": "FAIL",
        "phase": "java_connect_only",
        "path": str(path),
        "inode": before.get("inode"),
        "offset_before": before.get("offset"),
        "offset_after": before.get("offset"),
        "before_sha256": before.get("sha256"),
        "after_sha256": before.get("sha256"),
        "segment_sha256": _sha256_bytes(b""),
        "java_request_id": java_request_id,
        "started_at": started_at,
        "finished_at": started_at,
        "raw_probe_started_after_capture": True,
        "entries": [],
    }
    # The installed Tomcat StandardEngine uses a 10-second background cycle;
    # AccessLogValve flushes its buffered writer from backgroundProcess().
    # Wait through one cycle without changing the Server's log configuration.
    deadline = time.monotonic() + 15.0
    segment = b""
    after: dict[str, Any] | None = None
    last_offset: int | None = None
    try:
        while True:
            after = _access_log_snapshot(path)
            if int(after["inode"]) != int(before["inode"]):
                raise GuardError("run-owned Tomcat access log inode changed during correlation")
            if int(after["offset"]) < int(before["offset"]):
                raise GuardError("run-owned Tomcat access log offset moved backwards")
            with path.open("rb") as handle:
                handle.seek(int(before["offset"]))
                segment = handle.read(int(after["offset"]) - int(before["offset"]))
            complete = bool(segment) and segment.endswith(b"\n")
            if complete and last_offset == int(after["offset"]):
                break
            last_offset = int(after["offset"])
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        entries, parse_error = _parse_access_log_segment(segment)
        nonloopback_entries = [
            row for row in entries
            if row.get("peer") not in {"127.0.0.1", "::1"}
        ]
        expected = [
            row for row in nonloopback_entries
            if row.get("peer") == nonloopback
            and row.get("method") == "GET"
            and row.get("route") == API_ROUTE
            and row.get("http_status") == 403
        ]
        target_entries = [row for row in entries if row.get("route") == API_ROUTE]
        proof.update({
            "offset_after": after["offset"],
            "after_sha256": after["sha256"],
            "segment_sha256": _sha256_bytes(segment),
            "entries": entries,
            "target_entry_count": len(target_entries),
            "nonloopback_entry_count": len(nonloopback_entries),
            "parse_error": parse_error,
            "status": "PASS" if not parse_error and len(nonloopback_entries) == 1 and len(expected) == 1 else "FAIL",
        })
    except (GuardError, OSError) as exc:
        proof.update({
            "error_class": type(exc).__name__,
            "error_category": "ACCESS_LOG_CORRELATION_FAILED",
        })
        if after is not None:
            proof["offset_after"] = after.get("offset", before.get("offset"))
    proof["finished_at"] = _utc_now()
    return proof


def _probe_api(run_dir: Path, server_pid: int) -> dict[str, Any]:
    nonloopback = _current_nonloopback_ipv4()
    prefs = run_dir / "prefs"
    paths = JavaWorkerPaths(COMSOL_ROOT, JDK_HOME, private_prefs=prefs, project_root=ROOT)

    # Each address gets a separate Worker process. This avoids ModelUtil
    # endpoint caching and makes both attempts use the same run-owned login
    # material without ever printing it.
    loopback_worker = _worker_probe(
        run_dir, paths, label="loopback", address="127.0.0.1", server_pid=server_pid, expect_loopback=True,
    )
    loopback_http = _websocket_api_probe("127.0.0.1", source_address="127.0.0.1")
    process_before = base._process_snapshot(server_pid)
    if process_before is None:
        raise GuardError("COMSOL server identity disappeared before nonloopback API proof")
    access_log: dict[str, Any] | None = None
    access_log_path: Path | None = None
    access_log_before: dict[str, Any] | None = None
    try:
        access_log_path = _access_log_path(run_dir)
        access_log_before = _access_log_snapshot(access_log_path)
    except (GuardError, OSError) as exc:
        access_log = {
            "status": "FAIL",
            "phase": "java_connect_only",
            "path": str((run_dir / "prefs" / "tomcat" / "logs").resolve()),
            "inode": None,
            "offset_before": None,
            "offset_after": None,
            "segment_sha256": _sha256_bytes(b""),
            "java_request_id": "g2-valve-nonloopback-connect",
            "started_at": _utc_now(),
            "finished_at": _utc_now(),
            "raw_probe_started_after_capture": True,
            "entries": [],
            "error_class": type(exc).__name__,
            "error_category": "ACCESS_LOG_CAPTURE_FAILED",
        }
    nonloopback_worker = _worker_probe(
        run_dir, paths, label="nonloopback", address=nonloopback, server_pid=server_pid, expect_loopback=False,
    )
    if access_log is None and access_log_path is not None and access_log_before is not None:
        access_log = _correlate_access_log(
            access_log_path,
            access_log_before,
            nonloopback=nonloopback,
            java_request_id=str(nonloopback_worker.get("java_request_id") or "g2-valve-nonloopback-connect"),
        )
    # Explicitly bind the raw API transport to the current LAN address. The
    # recorded getsockname/getpeername values are the evidence, regardless of
    # how the kernel routes a self-address.
    nonloopback_http = _websocket_api_probe(nonloopback, source_address=nonloopback)

    identity = base._process_snapshot(server_pid)
    if identity is None:
        raise GuardError("COMSOL server identity disappeared while recording API proof")
    if identity.get("birth") != process_before.get("birth"):
        raise GuardError("COMSOL server process epoch changed during API proof")
    explicit_local = nonloopback_http.get("local_endpoint") or {}
    explicit_peer = nonloopback_http.get("peer_endpoint") or {}
    explicit_source_match = (
        explicit_local.get("host") == nonloopback
        and explicit_peer.get("host") == nonloopback
        and explicit_peer.get("port") == PORT
    )
    loopback_ok = (
        loopback_worker.get("status") == "PASS"
        and loopback_worker.get("authenticated") is True
        and loopback_worker.get("socket_pair_status") == "PASS"
    )
    nonloopback_ok = (
        nonloopback_worker.get("status") == "REJECTED"
        and nonloopback_worker.get("authenticated") is False
        and nonloopback_worker.get("address") == nonloopback
        and nonloopback_worker.get("port") == PORT
        and isinstance(access_log, dict)
        and access_log.get("status") == "PASS"
        and nonloopback_http.get("http_status") == 403
        and explicit_source_match
    )
    proof = {
        "schema_version": 3,
        "status": "PASS" if loopback_ok and nonloopback_ok else "FAIL",
        "probe_kind": "comsol_java_api",
        "kind": "remote_addr_valve",
        "source": "COMSOL Java API via fresh PersistentJavaWorker processes; WebSocket route /webbridge/websocket",
        "observed_at": _utc_now(),
        "pid": server_pid,
        "birth": identity["birth"],
        "process": {"pid": server_pid, "birth": identity["birth"]},
        "port": PORT,
        "config_sha256": EXPECTED_VALVE_SHA256,
        "target": str(SERVER_XML),
        "config_guard": "actual webbridge RemoteAddrValve proposal hash and original hash verified",
        "api_route": API_ROUTE,
        "listener_binding": "wildcard_or_platform_listener; address filtering is separate from socket binding",
        "credentials_source": "same private prefs and auto-login credential file for both fresh Workers; material not recorded",
        "loopback": {
            "status": "PASS" if loopback_ok else "FAIL",
            "address": "127.0.0.1",
            "port": PORT,
            "authenticated": loopback_worker.get("authenticated") is True,
            "authenticated_api_success": loopback_worker.get("authenticated") is True,
            "http_status": loopback_http.get("http_status"),
            "http_probe": loopback_http,
            "java_api": loopback_worker,
        },
        "nonloopback": {
            "status": "PASS" if nonloopback_ok else "FAIL",
            "address": nonloopback,
            "port": PORT,
            "authenticated": False,
            "http_status": nonloopback_http.get("http_status"),
            "api_exception_http_status": (
                403 if nonloopback_worker.get("http_403_explicitly_observed_in_worker_error") is True else None
            ),
            "server_access_log_http_status": 403 if isinstance(access_log, dict) and access_log.get("status") == "PASS" else None,
            "server_access_log": access_log,
            "http_probe": nonloopback_http,
            "java_api": nonloopback_worker,
            "explicit_source_match": explicit_source_match,
            "address_filter": "RemoteAddrValve",
        },
        "model_operations": "NOT_RUN",
        "solve_operations": "NOT_RUN",
    }
    if not loopback_ok:
        raise ProbeFailure("authenticated localhost Java API/socket proof failed", proof)
    if not nonloopback_ok:
        if nonloopback_worker.get("status") == "UNEXPECTED_SUCCESS":
            raise ProbeFailure("non-loopback Java API connection unexpectedly succeeded", proof)
        raise ProbeFailure(
            f"non-loopback API isolation proof failed (java={nonloopback_worker.get('status')}, http={nonloopback_http.get('http_status')})",
            proof,
        )
    return proof


def _config_receipt(run_dir: Path, original: bytes, proposed: bytes, original_metadata: dict[str, Any], inode: int, mode: int) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "phase": "PREPARED",
        "target": str(SERVER_XML),
        "original_sha256": EXPECTED_ORIGINAL_SHA256,
        "proposed_sha256": EXPECTED_VALVE_SHA256,
        "original_content_sha256_verified_before_apply": _sha256_bytes(original),
        "original_mode": mode,
        "target_inode": inode,
        "original_metadata": original_metadata,
        "metadata_residual": _metadata_residual(original_metadata),
        "change": "uncomment exactly the documented localhost RemoteAddrValve block",
        "proposal": "RemoteAddrValve address filter; it does not claim loopback socket binding",
        "prepared_at": _utc_now(),
    }


def _assert_webbridge_metadata(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    """Check ownership/ACL and allow only the known macOS write residual.

    macOS can attach ``com.apple.provenance`` to an existing installation file
    on the first in-place write.  The residual is pinned by
    :mod:`phase3_owned_runtime`; it is preserved in place and recorded rather
    than removed.  Every other metadata change remains a fail-closed error.
    """
    base._assert_approved_target_metadata(observed)
    for key in ("uid", "gid", "mode", "acl_entries"):
        if observed.get(key) != expected.get(key):
            raise GuardError(f"server.xml {key} changed unexpectedly")
    if observed.get("xattrs") == expected.get("xattrs") and observed.get("xattr_values") == expected.get("xattr_values"):
        return
    if expected.get("xattrs") == [] and observed.get("xattrs") == [base.KNOWN_RESIDUAL_XATTR_NAME]:
        value = observed.get("xattr_values", {}).get(base.KNOWN_RESIDUAL_XATTR_NAME)
        if base._residual_token_ok(value):
            return
    if (expected.get("xattrs") == [base.KNOWN_RESIDUAL_XATTR_NAME]
            and observed.get("xattrs") == [base.KNOWN_RESIDUAL_XATTR_NAME]
            and base._residual_token_ok(observed.get("xattr_values", {}).get(base.KNOWN_RESIDUAL_XATTR_NAME))):
        # macOS re-minted the OS-managed provenance token during this in-place
        # write; the new value is recorded in the receipt.  Any other xattr
        # difference still fails closed below.
        return
    raise GuardError("server.xml xattrs changed unexpectedly")


def _restore_original_webbridge(receipt: dict[str, Any], original: bytes) -> dict[str, Any]:
    """Restore the actual webbridge config using the recorded inode/metadata."""
    config = receipt.get("config")
    if not isinstance(config, dict):
        raise GuardError("private receipt has no configuration identity")
    target = Path(str(config.get("target", SERVER_XML)))
    if target != SERVER_XML:
        raise GuardError("private receipt target is not the approved webbridge server.xml")
    if config.get("proposed_sha256") != EXPECTED_VALVE_SHA256:
        raise GuardError("private receipt has an unexpected webbridge proposal hash")
    before_sha256 = _sha256(target)
    if before_sha256 != EXPECTED_VALVE_SHA256:
        raise GuardError("webbridge server.xml changed unexpectedly; refusing restoration")
    mode = config.get("original_mode")
    if type(mode) is not int or mode < 0 or mode > 0o7777:
        raise GuardError("private receipt has no valid original file mode")
    metadata = config.get("original_metadata")
    if not isinstance(metadata, dict):
        raise GuardError("private receipt has no original ownership/ACL metadata")
    target_inode = config.get("target_inode")
    if type(target_inode) is not int or target_inode <= 0:
        raise GuardError("private receipt has no valid webbridge target inode")
    if int(target.stat().st_ino) != target_inode:
        raise GuardError("webbridge server.xml inode changed unexpectedly; refusing restoration")
    base._assert_approved_target_metadata(metadata)
    current_metadata = base._file_metadata(target)
    _assert_webbridge_metadata(current_metadata, metadata)
    inode = int(target.stat().st_ino)
    write_result = base._in_place_write(target, original, current_metadata)
    restored_metadata = base._file_metadata(target)
    _assert_webbridge_metadata(restored_metadata, current_metadata)
    if _sha256(target) != EXPECTED_ORIGINAL_SHA256 or base._mode(target) != mode:
        raise GuardError("webbridge server.xml restoration verification failed")
    if write_result["inode"] != inode:
        raise GuardError("webbridge server.xml inode changed during restoration")
    return {
        "status": "PASS",
        "restored_at": _utc_now(),
        "content_sha256_before_restore": before_sha256,
        "content_sha256_after_restore": EXPECTED_ORIGINAL_SHA256,
        "sha256": EXPECTED_ORIGINAL_SHA256,
        "mode": mode,
        "inode": inode,
        "metadata_residual": _metadata_residual(restored_metadata),
        "metadata_before_restore": _metadata_residual(current_metadata),
    }


def _start(args: argparse.Namespace) -> Path:
    if not args.apply_valve:
        raise GuardError("start requires --apply-valve to acknowledge the approved temporary XML change")
    if SERVER_XML.is_symlink() or not SERVER_XML.is_file() or not COMSOL_LAUNCHER.is_file():
        raise GuardError("COMSOL 6.4 installation or server.xml is missing")
    original, proposed = _proposal_bytes()
    current = _sha256(SERVER_XML)
    if current != EXPECTED_ORIGINAL_SHA256:
        raise GuardError("installed server.xml does not match the approved original hash")
    original_metadata = base._file_metadata(SERVER_XML)
    base._assert_approved_target_metadata(original_metadata)
    inode = int(SERVER_XML.stat().st_ino)
    mode = base._mode(SERVER_XML)
    base._assert_no_existing_server()
    run_dir = _new_run_dir()
    config = _config_receipt(run_dir, original, proposed, original_metadata, inode, mode)
    config_path = run_dir / "config.receipt.json"
    base._write_json(config_path, config)
    public_path = _public_path(run_dir)
    public = {
        "schema_version": 2,
        "run_id": run_dir.name,
        "status": "PREPARED",
        "port": PORT,
        "source_sha256": {
            "tools/phase3_remote_addr_valve_runtime.py": _sha256(Path(__file__)),
            "tools/phase3_owned_runtime.py": _sha256(Path(base.__file__)),
            "comsol_mcp/_g2_isolation.py": _sha256(ROOT / "comsol_mcp" / "_g2_isolation.py"),
        },
        "config_change": "documented localhost RemoteAddrValve only",
        "original_sha256": EXPECTED_ORIGINAL_SHA256,
        "proposed_sha256": EXPECTED_VALVE_SHA256,
        "global_firewall_changes": False,
        "windows_probe": "NOT_RUN",
        "model_operations": "NOT_RUN",
        "solve_operations": "NOT_RUN",
        "credentials": "not_recorded",
        "api_route": API_ROUTE,
        "run_dir": _relative(run_dir),
        "public_evidence": _relative(public_path),
        "metadata_residual": _metadata_residual(original_metadata),
    }
    _write_public(public_path, public)
    process: subprocess.Popen[bytes] | None = None
    log_handle = None
    try:
        if _sha256(SERVER_XML) != EXPECTED_ORIGINAL_SHA256:
            raise GuardError("server.xml original hash changed before apply")
        applied = base._in_place_write(SERVER_XML, proposed, original_metadata)
        applied_metadata = base._file_metadata(SERVER_XML)
        _assert_webbridge_metadata(applied_metadata, original_metadata)
        if applied["inode"] != inode or applied["sha256"] != EXPECTED_VALVE_SHA256:
            raise GuardError("RemoteAddrValve proposal verification failed after in-place apply")
        config.update({"phase": "APPLIED", "applied_at": _utc_now(), "content_sha256_after_apply": applied["sha256"], "inode_after_apply": applied["inode"], "applied_metadata": applied_metadata, "metadata_residual_after_apply": _metadata_residual(applied_metadata)})
        base._write_json(config_path, config)
        prefs, tmp, recovery = run_dir / "prefs", run_dir / "tmp", run_dir / "recovery"
        portfile = run_dir / "server.port"
        log_path = run_dir / "logs" / "server.log"
        command = [str(COMSOL_LAUNCHER), "-np", "2", "-prefsdir", str(prefs), "-tmpdir", str(tmp), "-recoverydir", str(recovery),
                   "mphserver", "-multi", "on", "-silent", "-port", str(PORT), "-portfile", str(portfile), "-login", "auto"]
        log_handle = log_path.open("wb")
        os.chmod(log_path, 0o600)
        process = subprocess.Popen(command, cwd=str(run_dir), stdin=subprocess.DEVNULL, stdout=log_handle, stderr=log_handle,
                                   close_fds=True, start_new_session=True)
        log_handle.close(); log_handle = None
        deadline = time.monotonic() + args.timeout_s
        identity = None; listeners = None; launcher_identity = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise GuardError(f"COMSOL server exited before listener verification (exit {process.returncode})")
            launcher_identity = base._process_snapshot(process.pid)
            try:
                identity, listeners = _wildcard_or_loopback_listener(base._listener_records(PORT), process.pid)
            except GuardError:
                time.sleep(0.5)
                continue
            if portfile.is_file() and portfile.read_text(encoding="utf-8").strip() == str(PORT):
                os.chmod(portfile, 0o600)
                break
            time.sleep(0.5)
        else:
            raise GuardError("COMSOL server did not reach a listener before timeout")
        if identity is None or listeners is None or launcher_identity is None:
            raise GuardError("owned process identity was unavailable after listener startup")
        process_receipt = {
            "schema_version": 2,
            "pid": identity["pid"],
            "ppid": identity["ppid"],
            "birth": identity["birth"],
            "command_sha256": identity["command_sha256"],
            "launcher_pid": launcher_identity["pid"],
            "launcher_pid_birth": launcher_identity["birth"],
            "launcher_pid_command_sha256": launcher_identity["command_sha256"],
            "port": PORT,
            "listeners": [{"pid": row["pid"], "endpoint": row["endpoint"]} for row in listeners],
            "started_at": _utc_now(),
            "log": str(log_path),
        }
        process_path = run_dir / "process.receipt.json"
        base._write_json(process_path, process_receipt)
        receipt = {
            "schema_version": 2,
            "status": "RUNNING",
            "isolation_mode": "remote_addr_valve",
            "run_id": run_dir.name,
            "config": config,
            "process": process_receipt,
            "paths": {"run_dir": str(run_dir), "prefs": str(prefs), "tmp": str(tmp), "recovery": str(recovery), "portfile": str(portfile), "log": str(log_path)},
        }
        receipt_path = run_dir / "lifecycle.receipt.json"
        base._write_json(receipt_path, receipt)
        public.update({"status": "RUNNING", "target_inode": inode, "pid": identity["pid"], "birth": identity["birth"],
                       "listener": listeners[0]["endpoint"], "prefs_dir": _relative(prefs), "tmp_dir": _relative(tmp),
                       "recovery_dir": _relative(recovery), "portfile": _relative(portfile),
                       "metadata_residual_after_apply": _metadata_residual(applied_metadata),
                       "cleanup_command": f"python tools/phase3_remote_addr_valve_runtime.py cleanup --run-dir {_relative(run_dir)}",
                       "private_receipt": str(receipt_path), "private_process_receipt": str(process_path)})
        _write_public(public_path, public)
        print(json.dumps({"status": "RUNNING", "pid": identity["pid"], "birth": identity["birth"], "port": PORT,
                          "listener": listeners[0]["endpoint"], "run_dir": str(run_dir), "receipt": str(receipt_path)}, ensure_ascii=False))
        return receipt_path
    except BaseException as exc:
        if log_handle is not None:
            log_handle.close()
        base._stop_failed_attempt(process)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                if not base._listener_records(PORT):
                    break
            except GuardError:
                pass
            time.sleep(0.25)
        restored = False
        restore_error = None
        try:
            if _sha256(SERVER_XML) == EXPECTED_VALVE_SHA256:
                restore = _restore_original_webbridge({"config": config}, original)
                restored = restore.get("sha256") == EXPECTED_ORIGINAL_SHA256
                config.update({"phase": "RESTORED_AFTER_FAILURE", "restore": restore})
                base._write_json(config_path, config)
        except Exception as restore_exc:
            restore_error = f"{type(restore_exc).__name__}: {restore_exc}"
        public.update({"status": "FAIL", "failure": type(exc).__name__, "failure_reason": str(exc), "restored": restored,
                       "content_sha256_at_failure": _sha256(SERVER_XML), "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
        if restore_error:
            public["restore_error"] = restore_error
        _write_public(public_path, public)
        raise GuardError(str(exc)) from exc


def _probe(receipt_path: Path) -> dict[str, Any]:
    receipt = base._read_json(receipt_path)
    if receipt.get("status") != "RUNNING":
        raise GuardError("lifecycle receipt is not RUNNING")
    observed = base._assert_owned_identity(receipt)
    try:
        proof = _probe_api(Path(str(receipt["paths"]["run_dir"])), int(observed["pid"]))
    except ProbeFailure as exc:
        proof = exc.proof
        proof["listener_observation"] = [{"pid": row["pid"], "endpoint": row["endpoint"]} for row in base._listener_records(PORT)]
        receipt["proof"] = proof
        receipt["probe"] = proof
        receipt["isolation_mode"] = "remote_addr_valve"
        receipt["status"] = "PROBE_FAIL"
        receipt["probed_at"] = _utc_now()
        base._write_json(receipt_path, receipt)
        public_path = _public_path(Path(str(receipt["paths"]["run_dir"])))
        public = base._read_json(public_path) if public_path.is_file() else {"run_id": receipt.get("run_id"), "port": PORT}
        public.update({"status": "PROBE_FAIL", "proof": proof, "failure": type(exc).__name__, "failure_reason": str(exc),
                       "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
        _write_public(public_path, public)
        raise
    proof["listener_observation"] = [{"pid": row["pid"], "endpoint": row["endpoint"]} for row in base._listener_records(PORT)]
    receipt["proof"] = proof
    receipt["probe"] = proof
    receipt["isolation_mode"] = "remote_addr_valve"
    # Control-side verification consumes a live RUNNING receipt. Keep this
    # status after probing; cleanup transitions it to STOPPED_RESTORED.
    receipt["status"] = "RUNNING"
    receipt["probed_at"] = _utc_now()
    base._write_json(receipt_path, receipt)
    public_path = _public_path(Path(str(receipt["paths"]["run_dir"])))
    public = base._read_json(public_path) if public_path.is_file() else {"run_id": receipt.get("run_id"), "port": PORT}
    public.update({"status": "PROBE_PASS", "proof": proof, "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
    _write_public(public_path, public)
    print(json.dumps({"status": "PROBE_PASS", "run_id": receipt.get("run_id"), "proof": proof}, ensure_ascii=False))
    return proof


def _cleanup(run_dir: Path) -> dict[str, Any]:
    receipt_path = run_dir / "lifecycle.receipt.json"
    receipt = base._read_json(receipt_path)
    if receipt.get("status") not in {"RUNNING", "PROBED", "PROBE_FAIL", "STOPPING"}:
        raise GuardError("lifecycle receipt is not in a running state")
    original, _proposed = _proposal_bytes()
    public_path = _public_path(run_dir)
    public = base._read_json(public_path) if public_path.is_file() else {"run_id": run_dir.name, "port": PORT}
    receipt["status"] = "STOPPING"; receipt["stop_requested_at"] = _utc_now(); base._write_json(receipt_path, receipt)
    try:
        observed_process = receipt.get("process", {}).get("pid") if isinstance(receipt.get("process"), dict) else None
        if type(observed_process) is int and base._process_snapshot(observed_process) is not None:
            stopped = base._stop_owned_process(receipt, timeout_s=30.0)
        else:
            if base._listener_records(PORT):
                raise GuardError("recorded process is gone but the owned port listener remains")
            stopped = {"status": "PASS", "pid": observed_process, "already_stopped": True, "stopped_at": _utc_now()}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        receipt["cleanup_error"] = detail
        base._write_json(receipt_path, receipt)
        public.update({"status": "CLEANUP_FAILED", "cleanup_error": detail, "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
        _write_public(public_path, public)
        raise GuardError(f"owned COMSOL stop failed: {detail}") from exc
    try:
        restored = _restore_original_webbridge(receipt, original)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        receipt["cleanup_error"] = detail
        receipt["stopped"] = stopped
        base._write_json(receipt_path, receipt)
        public.update({"status": "CLEANUP_FAILED", "cleanup_error": detail, "stopped": stopped,
                       "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
        _write_public(public_path, public)
        raise GuardError(f"server.xml restoration failed: {detail}") from exc
    receipt.update({"status": "STOPPED_RESTORED", "stopped": stopped, "restored": restored})
    base._write_json(receipt_path, receipt)
    public.update({"status": "STOPPED_RESTORED", "stopped_at": stopped["stopped_at"], "restored_at": restored["restored_at"],
                   "content_sha256_before_restore": restored["content_sha256_before_restore"],
                   "content_sha256_after_restore": restored["content_sha256_after_restore"], "target_inode": restored["inode"],
                   "restored_sha256": EXPECTED_ORIGINAL_SHA256, "listener": "none", "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
    _write_public(public_path, public)
    print(json.dumps({"status": "STOPPED_RESTORED", "run_dir": str(run_dir), "pid": receipt["process"]["pid"],
                      "restored_sha256": restored["sha256"], "public_evidence": str(public_path)}, ensure_ascii=False))
    return receipt


def _run(args: argparse.Namespace) -> int:
    receipt_path: Path | None = None
    try:
        receipt_path = _start(args)
        _probe(receipt_path)
        _cleanup(receipt_path.parent)
        return 0
    except BaseException as exc:
        cleanup_error: Exception | None = None
        cleanup_succeeded = False
        if receipt_path is not None:
            try:
                receipt = base._read_json(receipt_path)
                if receipt.get("status") in {"RUNNING", "PROBED", "PROBE_FAIL", "STOPPING"}:
                    _cleanup(receipt_path.parent)
                    cleanup_succeeded = True
            except Exception as cleanup_exc:
                cleanup_error = cleanup_exc
                try:
                    receipt = base._read_json(receipt_path)
                    public_path = _public_path(receipt_path.parent)
                    public = base._read_json(public_path) if public_path.is_file() else {"run_id": receipt.get("run_id")}
                    detail = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                    public.update({"status": "CLEANUP_FAILED", "cleanup_error": detail,
                                   "failure": type(exc).__name__, "failure_reason": str(exc),
                                   "model_operations": "NOT_RUN", "solve_operations": "NOT_RUN"})
                    _write_public(public_path, public)
                except Exception:
                    pass
            if cleanup_succeeded:
                try:
                    receipt = base._read_json(receipt_path)
                    receipt["run_failure"] = {
                        "type": type(exc).__name__,
                        "reason": str(exc),
                        "recorded_at": _utc_now(),
                    }
                    base._write_json(receipt_path, receipt)
                    public_path = _public_path(receipt_path.parent)
                    public = base._read_json(public_path) if public_path.is_file() else {"run_id": receipt.get("run_id")}
                    public.update({
                        "status": "FAIL_RESTORED",
                        "failure": type(exc).__name__,
                        "failure_reason": str(exc),
                        "cleanup_status": "STOPPED_RESTORED",
                        "model_operations": "NOT_RUN",
                        "solve_operations": "NOT_RUN",
                    })
                    _write_public(public_path, public)
                except Exception:
                    pass
        if isinstance(exc, GuardError):
            if cleanup_error is not None:
                raise GuardError(f"{exc}; cleanup failed: {cleanup_error}") from exc
            raise
        if cleanup_error is not None:
            raise GuardError(f"{exc}; cleanup failed: {cleanup_error}") from exc
        raise GuardError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--apply-valve", action="store_true")
    start.add_argument("--timeout-s", type=float, default=90.0)
    start.set_defaults(handler=lambda args: (_start(args), 0)[1])
    probe = subparsers.add_parser("probe")
    probe.add_argument("--run-dir", required=True)
    probe.set_defaults(handler=lambda args: (_probe(Path(args.run_dir).resolve() / "lifecycle.receipt.json"), 0)[1])
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--run-dir", required=True)
    cleanup.set_defaults(handler=lambda args: (_cleanup(Path(args.run_dir).resolve()), 0)[1])
    run = subparsers.add_parser("run")
    run.add_argument("--apply-valve", action="store_true")
    run.add_argument("--timeout-s", type=float, default=90.0)
    run.set_defaults(handler=_run)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except GuardError as exc:
        print(f"OWNED_REMOTE_ADDR_VALVE_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
