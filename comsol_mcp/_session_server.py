"""Private, loopback-only COMSOL Server process ownership adapters.

The daemon keeps these handles in memory and the lifecycle store keeps only a
schema-checked process identity. A persisted PID is never sufficient to start,
stop, or adopt a Server after the original Popen handle has been lost.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Iterable, Mapping

from ._platform_process import process_identity
from ._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionRuntimeConfig,
    session_state_directory,
)


class OwnedServerError(RuntimeError):
    """A Server lifecycle action could not be proven safe or complete."""

    def __init__(self, message: str, *, handle: "ManagedServerHandle | None" = None,
                 uncertain: bool = False):
        super().__init__(message)
        self.handle = handle
        self.uncertain = uncertain


@dataclass(frozen=True)
class ServerDirectories:
    root: Path
    private_installation: Path
    runtime: Path
    preferences: Path
    temporary: Path
    recovery: Path
    logs: Path
    port_file: Path
    log_file: Path


@dataclass
class ManagedServerHandle:
    """Exact in-memory Popen plus independently verified Server endpoint."""

    process: Any
    runtime_id: str
    executable: str
    directories: ServerDirectories
    endpoint: CanonicalSocket | None = None
    process_identity: OwnedServerProcessIdentity | None = None
    start_epoch_ms: int | None = None
    log_handle: Any = None
    retired: bool = False


ListenerRow = tuple[int, CanonicalSocket]
ProcessIdentityReader = Callable[[int], Mapping[str, Any]]
ListenerReader = Callable[[int], Iterable[ListenerRow]]


def _safe_directory(path: Path, *, parents: bool = True) -> Path:
    if path.is_symlink():
        raise OwnedServerError("owned Server state path cannot be a symlink")
    path.mkdir(mode=0o700, parents=parents, exist_ok=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise OwnedServerError("owned Server state path is not a directory")
    try:
        os.chmod(resolved, 0o700)
    except OSError as exc:
        raise OwnedServerError("owned Server state permissions could not be restricted") from exc
    return resolved


def create_server_directories(runtime: SessionRuntimeConfig, project_id: str,
                              session_id: str) -> ServerDirectories:
    """Create a fresh private state root below the already trusted session root."""
    session_root = session_state_directory(runtime.session_state_root, project_id, session_id)
    _safe_directory(session_root)
    root = session_root / "owned-server"
    if root.exists() or root.is_symlink():
        raise OwnedServerError("owned Server state already exists; refusing an implicit restart")
    root.mkdir(mode=0o700)
    child_paths = {
        "private_installation": root / "installation",
        "runtime": root / "runtime",
        "preferences": root / "preferences",
        "temporary": root / "tmp",
        "recovery": root / "recovery",
        "logs": root / "logs",
    }
    for path in child_paths.values():
        _safe_directory(path, parents=False)
    return ServerDirectories(
        root=root,
        private_installation=child_paths["private_installation"],
        runtime=child_paths["runtime"],
        preferences=child_paths["preferences"],
        temporary=child_paths["temporary"],
        recovery=child_paths["recovery"],
        logs=child_paths["logs"],
        port_file=child_paths["runtime"] / "server.port",
        log_file=child_paths["logs"] / "mphserver.log",
    )


def prepare_private_loopback_installation(source_root: Path, private_root: Path) -> dict[str, Any]:
    """Build an isolated installation view and bind its private WebBridge XML to loopback.

    All source installation paths remain read-only. The launcher and ordinary
    root/bin files are copied, immutable non-server payload is referenced by
    symlink, and the mutable ``bin/servers`` tree is copied before XML edit.
    """
    source = Path(source_root).resolve(strict=True)
    target = Path(private_root)
    if not source.is_dir() or not (source / "bin").is_dir():
        raise OwnedServerError("inspected COMSOL installation has no bin directory")
    target_preexisting = target.exists()
    if target.is_symlink():
        raise OwnedServerError("private COMSOL installation root cannot be a symlink")
    if target_preexisting and (not target.is_dir() or any(target.iterdir())):
        raise OwnedServerError("private COMSOL installation root must be a fresh empty directory")
    if target == source or target.is_relative_to(source):
        raise OwnedServerError("private COMSOL installation cannot be inside the inspected source tree")
    source_xml = source / "bin" / "servers" / "webbridge" / "conf" / "server.xml"
    if not source_xml.is_file() or source_xml.is_symlink():
        raise OwnedServerError("installed WebBridge server.xml is missing or aliased")
    source_digest_before = _sha256(source_xml)
    target.mkdir(mode=0o700, parents=True, exist_ok=target_preexisting)
    try:
        for entry in source.iterdir():
            if entry.name == "bin":
                continue
            destination = target / entry.name
            if entry.is_dir() and not entry.is_symlink():
                destination.symlink_to(entry, target_is_directory=True)
            elif entry.is_symlink():
                destination.symlink_to(entry.resolve(strict=True), target_is_directory=entry.resolve().is_dir())
            else:
                shutil.copy2(entry, destination)

        private_bin = target / "bin"
        private_bin.mkdir(mode=0o700)
        for entry in (source / "bin").iterdir():
            destination = private_bin / entry.name
            if entry.name == "servers":
                shutil.copytree(entry, destination, symlinks=True)
            elif entry.is_dir() and not entry.is_symlink():
                destination.symlink_to(entry, target_is_directory=True)
            elif entry.is_symlink():
                destination.symlink_to(entry.resolve(strict=True), target_is_directory=entry.resolve().is_dir())
            else:
                shutil.copy2(entry, destination)

        private_xml = private_bin / "servers" / "webbridge" / "conf" / "server.xml"
        tree = ET.parse(private_xml)
        connectors = tree.findall(".//Connector")
        if len(connectors) != 1:
            raise OwnedServerError(
                f"expected exactly one WebBridge Connector, found {len(connectors)}",
            )
        connectors[0].set("address", "127.0.0.1")
        tree.write(private_xml, encoding="UTF-8", xml_declaration=True)
        reparsed = ET.parse(private_xml)
        verified = reparsed.findall(".//Connector")
        if len(verified) != 1 or verified[0].get("address") != "127.0.0.1":
            raise OwnedServerError("private WebBridge Connector failed loopback readback")
        source_digest_after = _sha256(source_xml)
        if source_digest_after != source_digest_before:
            raise OwnedServerError("inspected installation server.xml changed during shadow preparation")
        return {
            "installed_server_config_sha256_before": source_digest_before,
            "installed_server_config_sha256_after": source_digest_after,
            "private_server_config_sha256": _sha256(private_xml),
            "private_connector_address": "127.0.0.1",
            "install_tree_mutated": False,
        }
    except BaseException:
        # The target belongs to this invocation and has not been exposed to a
        # launched process. Remove only its newly created private files.
        shutil.rmtree(target, ignore_errors=True)
        raise


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_server_command(directories: ServerDirectories, *, platform_name: str | None = None) -> list[str]:
    selected = platform_name or sys.platform
    root = directories.private_installation
    if selected in {"darwin", "mac", "macos"}:
        launcher = root / "bin" / "comsol"
        prefix = [str(launcher), "mphserver"]
    elif selected in {"win32", "nt", "windows"}:
        launcher = root / "bin" / "win64" / "comsolmphserver.exe"
        prefix = [str(launcher)]
    else:
        raise OwnedServerError("owned COMSOL Server launcher is not verified for this platform")
    if not launcher.is_file():
        raise OwnedServerError("private COMSOL Server launcher is missing")
    return [
        *prefix,
        "-port", "0", "-portfile", str(directories.port_file),
        "-prefsdir", str(directories.preferences), "-tmpdir", str(directories.temporary),
        "-recoverydir", str(directories.recovery), "-login", "auto", "-silent", "-multi", "on",
    ]


def _parse_lsof_rows(output: str) -> list[ListenerRow]:
    rows: list[ListenerRow] = []
    pid: int | None = None
    for line in output.splitlines():
        if not line:
            continue
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            endpoint = line[1:].removeprefix("TCP ").removesuffix(" (LISTEN)")
            try:
                address, port_text = _split_address_port(endpoint)
                rows.append((pid, CanonicalSocket(address, int(port_text))))
            except (TypeError, ValueError):
                raise OwnedServerError("lsof returned a malformed listener row") from None
    return rows


def _split_address_port(value: str) -> tuple[str, str]:
    text = value.strip()
    if text.startswith("["):
        closing = text.rfind("]:")
        if closing < 0:
            raise ValueError("malformed bracketed socket")
        return text[1:closing], text[closing + 2:]
    address, sep, port = text.rpartition(":")
    if not sep or not address:
        raise ValueError("malformed socket")
    return address, port


def _darwin_listener_rows(port: int) -> list[ListenerRow]:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"],
        capture_output=True, text=True, check=False, timeout=5,
    )
    if result.returncode not in (0, 1):
        raise OwnedServerError("lsof listener inventory failed")
    return _parse_lsof_rows(result.stdout)


def _windows_listener_rows(port: int) -> list[ListenerRow]:
    script = (
        f"$ErrorActionPreference='Stop'; "
        f"$rows=Get-NetTCPConnection -State Listen -LocalPort {port} | "
        "Select-Object LocalAddress,LocalPort,OwningProcess; "
        "ConvertTo-Json -InputObject @($rows) -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False, timeout=8,
    )
    if result.returncode != 0:
        raise OwnedServerError("Windows TCP listener inventory failed")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OwnedServerError("Windows TCP listener inventory was malformed") from exc
    if not isinstance(parsed, list):
        raise OwnedServerError("Windows TCP listener inventory was not an array")
    rows: list[ListenerRow] = []
    for row in parsed:
        if not isinstance(row, Mapping):
            raise OwnedServerError("Windows TCP listener row was malformed")
        try:
            pid = int(row["OwningProcess"])
            local_port = int(row["LocalPort"])
            address = str(row["LocalAddress"])
            rows.append((pid, CanonicalSocket(address, local_port)))
        except (KeyError, TypeError, ValueError) as exc:
            raise OwnedServerError("Windows TCP listener row was malformed") from exc
    return rows


def listener_rows(port: int, *, platform_name: str | None = None) -> list[ListenerRow]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise OwnedServerError("listener port is invalid")
    selected = platform_name or sys.platform
    if selected in {"darwin", "mac", "macos"}:
        return _darwin_listener_rows(port)
    if selected in {"win32", "nt", "windows"}:
        return _windows_listener_rows(port)
    raise OwnedServerError("listener inventory is not verified for this platform")


def _birth_identity(pid: int, read_process_identity: ProcessIdentityReader) -> tuple[str, int]:
    observed = read_process_identity(pid)
    birth = observed.get("start_epoch_ms") if isinstance(observed, Mapping) else None
    if (not isinstance(observed, Mapping) or observed.get("alive") is not True
            or type(birth) is not int or birth <= 0):
        raise OwnedServerError("exact live process birth identity is unavailable", uncertain=True)
    return f"start_epoch_ms:{birth}", birth


def _verified_loopback_listener(pid: int, port: int,
                                read_listeners: ListenerReader) -> tuple[CanonicalSocket, ...]:
    rows = list(read_listeners(port))
    if (len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != pid
            or rows[0][1] != CanonicalSocket("127.0.0.1", port)):
        raise OwnedServerError("listener inventory does not prove one exact owned loopback endpoint", uncertain=True)
    return (rows[0][1],)


class OwnedServerLauncher:
    """Start and retire a dedicated Server without process-global mutation."""

    def __init__(self, *, process_factory: Callable[..., Any] = subprocess.Popen,
                 process_identity_reader: ProcessIdentityReader = process_identity,
                 listener_reader: Callable[[int], Iterable[ListenerRow]] | None = None,
                 platform_name: str | None = None,
                 ready_timeout_s: float = 45.0,
                 poll_interval_s: float = 0.2):
        self.process_factory = process_factory
        self.process_identity_reader = process_identity_reader
        self.platform_name = platform_name or sys.platform
        self.listener_reader = listener_reader or (
            lambda port: listener_rows(port, platform_name=self.platform_name)
        )
        self.ready_timeout_s = ready_timeout_s
        self.poll_interval_s = poll_interval_s

    def start(self, runtime: SessionRuntimeConfig, project_id: str, session_id: str) -> ManagedServerHandle:
        directories = create_server_directories(runtime, project_id, session_id)
        prepare_private_loopback_installation(runtime.installation_root, directories.private_installation)
        command = build_server_command(directories, platform_name=self.platform_name)
        log_handle = directories.log_file.open("ab", buffering=0)
        options: dict[str, Any] = {
            "cwd": str(directories.root), "stdin": subprocess.DEVNULL,
            "stdout": log_handle, "stderr": subprocess.STDOUT,
        }
        if self.platform_name in {"win32", "nt", "windows"}:
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True
        try:
            process = self.process_factory(command, **options)
        except Exception as exc:
            log_handle.close()
            raise OwnedServerError("COMSOL Server process creation failed before process identity was retained") from exc
        executable = str(Path(command[0]).resolve())
        handle = ManagedServerHandle(
            process=process, runtime_id=runtime.runtime_id, executable=executable,
            directories=directories, log_handle=log_handle,
        )
        try:
            birth, start_epoch_ms = _birth_identity(process.pid, self.process_identity_reader)
            handle.start_epoch_ms = start_epoch_ms
            deadline = time.monotonic() + self.ready_timeout_s
            port: int | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise OwnedServerError("task-owned COMSOL Server exited before listener proof", handle=handle, uncertain=True)
                try:
                    value = int(directories.port_file.read_text(encoding="utf-8").strip())
                    if 1 <= value <= 65535:
                        port = value
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(self.poll_interval_s)
            if port is None:
                raise OwnedServerError("task-owned COMSOL Server did not publish a valid port", handle=handle, uncertain=True)
            listeners = _verified_loopback_listener(process.pid, port, self.listener_reader)
            current_birth, current_start = _birth_identity(process.pid, self.process_identity_reader)
            if current_birth != birth or current_start != start_epoch_ms:
                raise OwnedServerError("COMSOL Server process identity changed during startup", handle=handle, uncertain=True)
            endpoint = CanonicalSocket("127.0.0.1", port)
            identity = OwnedServerProcessIdentity(
                pid=process.pid, birth=birth, executable=executable,
                listener_sockets=listeners, start_epoch_ms=start_epoch_ms,
            )
            handle.endpoint = endpoint
            handle.process_identity = identity
            return handle
        except OwnedServerError as exc:
            if exc.handle is None:
                exc.handle = handle
                exc.uncertain = True
            raise
        except Exception as exc:
            raise OwnedServerError("owned COMSOL Server start lacks complete process/listener proof",
                                   handle=handle, uncertain=True) from exc

    def stop(self, handle: ManagedServerHandle, *, timeout_s: float = 10.0) -> dict[str, Any]:
        if not isinstance(handle, ManagedServerHandle) or handle.retired:
            raise OwnedServerError("exact live owned Server handle is unavailable", uncertain=True)
        process = handle.process
        identity = handle.process_identity
        endpoint = handle.endpoint
        if (identity is None or endpoint is None or getattr(process, "pid", None) != identity.pid
                or handle.start_epoch_ms != identity.start_epoch_ms):
            raise OwnedServerError("owned Server birth/listener evidence is incomplete", handle=handle, uncertain=True)
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
            except Exception as exc:
                raise OwnedServerError("owned Server exited but Popen reap is unconfirmed", handle=handle, uncertain=True) from exc
            if list(self.listener_reader(endpoint.port)):
                raise OwnedServerError("owned Server exited but its listener remains present", handle=handle, uncertain=True)
            handle.retired = True
            self._close_log(handle)
            return {"pid": identity.pid, "birth": identity.birth, "exit_confirmed": True,
                    "child_reaped": True, "listener_absent": True, "returncode": process.returncode,
                    "server_stopped": True}

        self.verify(handle)
        try:
            process.terminate()  # exact Popen object, never a PID command
            try:
                returncode = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                still_same, still_start = _birth_identity(identity.pid, self.process_identity_reader)
                if still_same != identity.birth or still_start != identity.start_epoch_ms:
                    raise OwnedServerError("Server identity changed before forced reap", handle=handle, uncertain=True)
                process.kill()  # exact retained Popen object after birth recheck
                returncode = process.wait(timeout=timeout_s)
        except OwnedServerError:
            raise
        except Exception as exc:
            raise OwnedServerError("exact owned Server termination/reap failed", handle=handle, uncertain=True) from exc
        if process.poll() is None or returncode is None:
            raise OwnedServerError("owned Server child exit could not be confirmed", handle=handle, uncertain=True)
        if list(self.listener_reader(endpoint.port)):
            raise OwnedServerError("owned Server listener is still present after process exit", handle=handle, uncertain=True)
        handle.retired = True
        self._close_log(handle)
        return {"pid": identity.pid, "birth": identity.birth, "exit_confirmed": True,
                "child_reaped": True, "listener_absent": True, "returncode": returncode,
                "server_stopped": True}

    def verify(self, handle: ManagedServerHandle) -> OwnedServerProcessIdentity:
        """Recheck live birth and the complete exact loopback listener before use."""
        if not isinstance(handle, ManagedServerHandle) or handle.retired:
            raise OwnedServerError("exact live owned Server handle is unavailable", uncertain=True)
        identity = handle.process_identity
        process = handle.process
        endpoint = handle.endpoint
        if (identity is None or endpoint is None or getattr(process, "pid", None) != identity.pid
                or handle.start_epoch_ms != identity.start_epoch_ms or process.poll() is not None):
            raise OwnedServerError("owned Server process handle is not a proven live birth", handle=handle, uncertain=True)
        current_birth, current_start = _birth_identity(identity.pid, self.process_identity_reader)
        if current_birth != identity.birth or current_start != identity.start_epoch_ms:
            raise OwnedServerError("live process no longer matches the retained Server birth", handle=handle, uncertain=True)
        current_listeners = _verified_loopback_listener(identity.pid, endpoint.port, self.listener_reader)
        if current_listeners != identity.listener_sockets:
            raise OwnedServerError("live listener set differs from the retained Server handle", handle=handle, uncertain=True)
        return identity

    @staticmethod
    def _close_log(handle: ManagedServerHandle) -> None:
        close = getattr(handle.log_handle, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
