#!/usr/bin/env python3
"""Bounded W25 COMSOL 6.4 ModelUtil.blockOtherClients experiment.

This runner is intentionally API-only: it creates a private loopback mphserver,
uses two independent Java client JVMs, and never creates or solves a model.
Native execution requires the explicit --execute-native switch.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus
from uuid import uuid4


DEFAULT_COMSOL = Path("/Applications/COMSOL64/Multiphysics")
DEFAULT_JAVAC = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home/bin/javac")
JAVA_SOURCE = Path(__file__).with_name("java") / "BlockOtherClientsClient.java"
CLASSPATH_PREFLIGHT_SOURCE = Path(__file__).with_name("java") / "ClientClasspathPreflight.java"
TOTAL_BUDGET_S = 300.0
SERVER_START_BUDGET_S = 120.0
CONNECT_BUDGET_S = 30.0
ACQUIRE_BUDGET_S = 5.0
BLOCK_OBSERVE_S = 1.0
RELEASE_RECOVERY_S = 5.0
SERVER_SHUTDOWN_S = 15.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_java_home(install_root: Path) -> Path:
    """Return the architecture-matched Java runtime bundled with COMSOL."""
    arch = platform.machine().lower()
    preferred = "macarm64" if arch in {"arm64", "aarch64"} else "macosx64"
    candidates = [
        install_root / "java" / preferred / "jre/Contents/Home",
        install_root / "java" / ("macosx64" if preferred == "macarm64" else "macarm64") / "jre/Contents/Home",
    ]
    for candidate in candidates:
        if (candidate / "bin/java").is_file():
            return candidate
    raise FileNotFoundError(
        f"no bundled COMSOL Java runtime found for {arch}: "
        + ", ".join(map(str, candidates))
    )


def java_major_version(output: str) -> int:
    """Parse the major version from `java -version` output."""
    match = re.search(r'\bversion\s+"([^"]+)"', output, re.IGNORECASE)
    if not match:
        raise ValueError(f"could not parse Java runtime version: {output!r}")
    value = match.group(1)
    if value.startswith("1."):
        value = value.split(".", 2)[1]
    else:
        value = value.split(".", 1)[0]
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"could not parse Java runtime version: {output!r}") from exc


def _classfile_major(path: Path) -> int:
    header = path.read_bytes()[:8]
    if len(header) != 8 or header[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError(f"not a Java class file: {path}")
    return int.from_bytes(header[6:8], "big")


def client_classpath_preflight(*, install_root: Path, java_home: Path,
                               javac: Path, classes: Path) -> dict[str, Any]:
    """Compile the API client and load its full COMSOL classpath without connecting."""
    java_exe = java_home / "bin/java"
    plugins = install_root / "plugins"
    if not java_exe.is_file() or not plugins.is_dir():
        raise FileNotFoundError("the selected COMSOL Java runtime or plugins directory is unavailable")
    if not javac.is_file():
        raise FileNotFoundError(f"Java compiler is unavailable: {javac}")
    if not JAVA_SOURCE.is_file() or not CLASSPATH_PREFLIGHT_SOURCE.is_file():
        raise FileNotFoundError("the frozen client or classpath preflight source is missing")
    if classes.exists():
        if not classes.is_dir() or any(classes.iterdir()):
            raise FileExistsError(f"client preflight class directory is not empty: {classes}")
    else:
        classes.mkdir(parents=True, exist_ok=False)
    home = classes.parent / "home"
    tmpdir = classes.parent / "tmp"
    home.mkdir(exist_ok=True)
    tmpdir.mkdir(exist_ok=True)
    env = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "PYTHONPATH"):
        env.pop(name, None)
    env.update({
        "HOME": str(home),
        "JAVA_HOME": str(java_home),
        "COMSOL_ROOT": str(install_root),
        "TMPDIR": str(tmpdir),
        "LC_ALL": "C",
    })

    version = subprocess.run([str(java_exe), "-version"], text=True, capture_output=True,
                             timeout=15, check=False, env=env, cwd=classes.parent)
    version_output = version.stdout + version.stderr
    if version.returncode != 0:
        raise RuntimeError(f"COMSOL-bundled Java runtime failed: {version_output[-2000:]}")
    major = java_major_version(version_output)
    if major < 17:
        raise RuntimeError(f"COMSOL 6.4 client runtime must be Java 17 or newer; found {major}")

    compiler_version = subprocess.run([str(javac), "-version"], text=True, capture_output=True,
                                      timeout=15, check=False, env=env, cwd=classes.parent)
    if compiler_version.returncode != 0:
        raise RuntimeError(f"Java compiler version probe failed: {compiler_version.stderr[-2000:]}")
    compile_command = [
        str(javac), "-classpath", str(plugins / "*"), "-d", str(classes),
        str(JAVA_SOURCE), str(CLASSPATH_PREFLIGHT_SOURCE),
    ]
    compile_result = subprocess.run(compile_command, text=True, capture_output=True,
                                     timeout=30, check=False, env=env, cwd=classes.parent)
    compile_record = {
        "command": compile_command,
        "exit_code": compile_result.returncode,
        "stdout": compile_result.stdout,
        "stderr": compile_result.stderr,
        "javac_version": compiler_version.stdout + compiler_version.stderr,
    }
    if compile_result.returncode != 0:
        raise RuntimeError(f"COMSOL client source compile failed: {compile_result.stderr[-2000:]}")

    classpath = os.pathsep.join((str(classes), str(plugins / "*")))
    probe_command = [str(java_exe), "-cp", classpath, "ClientClasspathPreflight"]
    probe_result = subprocess.run(probe_command, text=True, capture_output=True,
                                  timeout=30, check=False, env=env, cwd=classes.parent)
    required = {
        "BlockOtherClientsClient",
        "com.comsol.model.util.ModelUtil",
        "org.eclipse.core.runtime.spi.RegistryStrategy",
    }
    loaded = []
    for line in probe_result.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) == 4 and fields[0] == "CLASS_LOADED":
            loaded.append({"class": fields[1], "methods": int(fields[2]), "source": fields[3]})
    if probe_result.returncode != 0 or {row["class"] for row in loaded} != required:
        raise RuntimeError(
            "COMSOL client classpath load probe failed: "
            f"rc={probe_result.returncode}, stdout={probe_result.stdout[-3000:]!r}, "
            f"stderr={probe_result.stderr[-2000:]!r}"
        )

    classfiles = [classes / "BlockOtherClientsClient.class",
                  classes / "BlockOtherClientsClient$InjectedSentinel.class",
                  classes / "ClientClasspathPreflight.class"]
    return {
        "schema": "comsol-mcp-w25-client-runtime-preflight/1",
        "status": "CLIENT_RUNTIME_PREFLIGHT_PASS",
        "install_root": str(install_root),
        "java_home": str(java_home),
        "java_executable": str(java_exe),
        "java_version_output": version_output.strip(),
        "java_major": major,
        "minimum_supported_java_major": 17,
        "javac": str(javac),
        "javac_version_output": (compiler_version.stdout + compiler_version.stderr).strip(),
        "compiler_and_runtime_are_distinct": javac.parent.parent.resolve() != java_home.resolve(),
        "classpath": classpath,
        "classpath_semantics": "compiled client classes plus every JAR in the installed COMSOL plugins directory",
        "compile": compile_record,
        "compiled_classes": {
            path.name: {"sha256": sha256_file(path), "classfile_major": _classfile_major(path)}
            for path in classfiles
        },
        "classpath_probe": {
            "command": probe_command,
            "exit_code": probe_result.returncode,
            "stdout": probe_result.stdout,
            "stderr": probe_result.stderr,
            "loaded_classes": loaded,
            "initialization_performed": False,
            "api_methods_invoked": 0,
            "connection_attempts": 0,
            "server_started": False,
        },
        "source_sha256": {
            str(JAVA_SOURCE.resolve()): sha256_file(JAVA_SOURCE),
            str(CLASSPATH_PREFLIGHT_SOURCE.resolve()): sha256_file(CLASSPATH_PREFLIGHT_SOURCE),
        },
    }


def encode_event_line(line: str) -> dict[str, str] | None:
    """Parse the intentionally narrow tab-delimited child event protocol."""
    fields = line.rstrip("\r\n").split("\t")
    if len(fields) < 2 or fields[0] != "EVENT":
        return None
    return {"name": fields[1], "detail": "\t".join(fields[2:])}


def release_order_is_acceptable(*, child_result_received_ns: int,
                                supervisor_command_sent_ns: int) -> bool:
    """Require an observed result strictly after the supervisor command boundary."""
    return child_result_received_ns > supervisor_command_sent_ns


def recovery_within_budget(*, child_result_received_ns: int,
                           supervisor_command_sent_ns: int,
                           budget_s: float = RELEASE_RECOVERY_S) -> bool:
    elapsed_ns = child_result_received_ns - supervisor_command_sent_ns
    return 0 <= elapsed_ns <= int(budget_s * 1e9)


def _event_fields(detail: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in detail.split("\t"):
        key, separator, value = token.partition("=")
        if separator:
            fields[key] = unquote_plus(value)
    return fields


def exact_pre_control_busy_refusal(*, trial: str, acquired_event: dict[str, Any],
                                   read_command_sent_ns: int,
                                   read_entered_event: dict[str, Any],
                                   read_result_event: dict[str, Any],
                                   control_boundary_ns: int) -> bool:
    """Recognize only the installed API's exact tags() busy refusal in context."""
    if (acquired_event.get("name") != "ACQUIRED" or acquired_event.get("role") != "A"
            or acquired_event.get("detail") != f"trial={trial}"):
        return False
    if (read_entered_event.get("name") != "READ_CALL_ENTERED"
            or read_entered_event.get("role") != "B"
            or read_entered_event.get("detail") != f"label={trial}"):
        return False
    if (read_result_event.get("name") != "READ_CALL_ERROR"
            or read_result_event.get("role") != "B"):
        return False
    fields = _event_fields(str(read_result_event.get("detail", "")))
    if fields != {
            "label": trial,
            "error_class": "com.comsol.util.exceptions.FlException",
            "message": "Server_is_in_use_by_another_client",
    }:
        return False
    acquired_ns = acquired_event.get("received_monotonic_ns")
    entered_ns = read_entered_event.get("received_monotonic_ns")
    result_ns = read_result_event.get("received_monotonic_ns")
    stamps = (acquired_ns, read_command_sent_ns, entered_ns, result_ns, control_boundary_ns)
    if any(not isinstance(stamp, int) or isinstance(stamp, bool) for stamp in stamps):
        return False
    return acquired_ns <= read_command_sent_ns <= entered_ns <= result_ns < control_boundary_ns


def post_release_handshake_order_is_acceptable(
        *, trial: str, release_command_sent_ns: int,
        release_returned_event: dict[str, Any], owner_ready_event: dict[str, Any],
        fresh_read_command_sent_ns: int, fresh_read_entered_event: dict[str, Any],
        fresh_read_result_event: dict[str, Any], finish_command_sent_ns: int,
        finish_accepted_event: dict[str, Any], disconnect_requested_event: dict[str, Any]) -> bool:
    """Prove B's independent read completed while A waited connected for FINISH."""
    if (release_returned_event.get("name") != "RELEASE_RETURNED"
            or release_returned_event.get("role") != "A"
            or release_returned_event.get("detail") != f"trial={trial}"):
        return False
    if (owner_ready_event.get("name") != "OWNER_READY_FOR_POST_RELEASE_READ"
            or owner_ready_event.get("role") != "A"
            or owner_ready_event.get("detail") != f"trial={trial}"):
        return False
    if (fresh_read_entered_event.get("name") != "READ_CALL_ENTERED"
            or fresh_read_entered_event.get("role") != "B"
            or fresh_read_entered_event.get("detail") != f"label=post_release_{trial}"):
        return False
    result_fields = _event_fields(str(fresh_read_result_event.get("detail", "")))
    if (fresh_read_result_event.get("role") != "B"
            or fresh_read_result_event.get("name") not in {"READ_CALL_RETURNED", "READ_CALL_ERROR"}
            or result_fields.get("label") != f"post_release_{trial}"):
        return False
    if (finish_accepted_event.get("name") != "OWNER_FINISH_ACCEPTED"
            or finish_accepted_event.get("role") != "A"
            or finish_accepted_event.get("detail") != f"trial={trial}"):
        return False
    if (disconnect_requested_event.get("name") != "DISCONNECT_REQUESTED"
            or disconnect_requested_event.get("role") != "A"
            or disconnect_requested_event.get("detail") != f"trial={trial}"):
        return False
    stamps = (
        release_command_sent_ns,
        release_returned_event.get("received_monotonic_ns"),
        owner_ready_event.get("received_monotonic_ns"),
        fresh_read_command_sent_ns,
        fresh_read_entered_event.get("received_monotonic_ns"),
        fresh_read_result_event.get("received_monotonic_ns"),
        finish_command_sent_ns,
        finish_accepted_event.get("received_monotonic_ns"),
        disconnect_requested_event.get("received_monotonic_ns"),
    )
    if any(not isinstance(stamp, int) or isinstance(stamp, bool) for stamp in stamps):
        return False
    if not all(left <= right for left, right in zip(stamps, stamps[1:])):
        return False
    # These boundaries must be strictly ordered to prove the read did not race
    # the release return or the owner's disconnect.
    return (
        release_returned_event["received_monotonic_ns"] < owner_ready_event["received_monotonic_ns"]
        and owner_ready_event["received_monotonic_ns"] < fresh_read_command_sent_ns
        and fresh_read_command_sent_ns < fresh_read_entered_event["received_monotonic_ns"]
        and fresh_read_result_event["received_monotonic_ns"] < finish_command_sent_ns
        and finish_command_sent_ns < finish_accepted_event["received_monotonic_ns"]
    )


def _mac_process_path(pid: int) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = ctypes.create_string_buffer(4096)
        libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libproc.proc_pidpath.restype = ctypes.c_int
        size = libproc.proc_pidpath(pid, buffer, len(buffer))
        if size <= 0:
            return None
        return os.fsdecode(buffer.raw[:size])
    except Exception:
        return None


def _process_start_identity(pid: int) -> str | None:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        text=True, capture_output=True, timeout=3, check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def process_identity(pid: int) -> dict[str, Any] | None:
    start = _process_start_identity(pid)
    path = _mac_process_path(pid)
    if not start or not path:
        return None
    return {"pid": pid, "executable_path": path, "start_identity": start}


def validate_process_identity(actual: dict[str, Any] | None,
                              expected: dict[str, Any] | None,
                              expected_executable: Path) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    try:
        actual_path = Path(str(actual["executable_path"])).resolve()
        expected_path = Path(str(expected["executable_path"])).resolve()
        allowed_path = expected_executable.resolve()
        return (
            actual.get("pid") == expected.get("pid")
            and actual.get("start_identity") == expected.get("start_identity")
            and actual_path == expected_path == allowed_path
        )
    except (KeyError, OSError, RuntimeError):
        return False


class PrelaunchInventoryUnavailable(RuntimeError):
    """Raised when the supervisor cannot prove that no foreign COMSOL process exists."""


def _foreign_comsol_processes() -> list[dict[str, str]]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,comm=,args="],
        text=True, capture_output=True, timeout=5, check=False,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise PrelaunchInventoryUnavailable(
            f"process inventory failed closed: rc={result.returncode}, stderr={result.stderr[-1000:]}"
        )
    rows = []
    own_pid = os.getpid()
    own_pid_seen = False
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        if int(parts[0]) == own_pid:
            own_pid_seen = True
            continue
        low = line.lower()
        comm = Path(parts[1]).name.lower()
        comsol_java_entrypoint = any(marker in low for marker in (
            "mphserver",
            "com.comsol.util.application.serverapplication",
            "comsol_mcp.worker_java",
            "persistentcomsolworker",
        ))
        if "comsol" in comm or comsol_java_entrypoint:
            rows.append({"pid": parts[0], "comm": parts[1], "command": parts[2]})
    if not own_pid_seen:
        raise PrelaunchInventoryUnavailable(
            f"process inventory omitted supervisor PID {own_pid}; completeness is unverified"
        )
    return rows


def _lsof_listener_rows(result: Any) -> list[str]:
    lines = result.stdout.splitlines()
    if lines and lines[0].strip().split()[:1] == ["COMMAND"]:
        lines = lines[1:]
    return [line for line in lines if line.strip()]


def _lsof_probe_complete(result: Any) -> bool:
    """Accept normal lsof no-match exit 1, but reject diagnostic/partial output."""
    rows = _lsof_listener_rows(result)
    if result.stderr.strip():
        return False
    if result.returncode == 0:
        return True
    return result.returncode == 1 and not rows


def _has_process_birth(identity: dict[str, Any] | None, pid: int) -> bool:
    return (
        isinstance(identity, dict)
        and identity.get("pid") == pid
        and isinstance(identity.get("start_identity"), str)
        and bool(identity["start_identity"])
        and isinstance(identity.get("executable_path"), str)
        and bool(identity["executable_path"])
    )


def _shutdown_status(*, owned_birth_reaped: bool,
                     birth_identity: dict[str, Any] | None,
                     listener_probe: Any | None) -> str:
    if (not owned_birth_reaped or not isinstance(birth_identity, dict)
            or not _has_process_birth(birth_identity, birth_identity.get("pid"))):
        return "STOP_UNVERIFIED"
    if listener_probe is None or not _lsof_probe_complete(listener_probe):
        return "STOP_UNVERIFIED"
    return "STOPPED" if not _lsof_listener_rows(listener_probe) else "STOP_UNVERIFIED"


class EventJournal:
    def __init__(self, path: Path, start_monotonic_ns: int) -> None:
        self.path = path
        self.start_monotonic_ns = start_monotonic_ns
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, *, sampled_monotonic_ns: int | None = None,
              **fields: Any) -> dict[str, Any]:
        sample = time.monotonic_ns() if sampled_monotonic_ns is None else sampled_monotonic_ns
        row = {
            "at_utc": utc_now(),
            "event": event,
            "supervisor_monotonic_ns": sample,
            "elapsed_monotonic_ns": sample - self.start_monotonic_ns,
            **fields,
        }
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self.lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return row


class ManagedJavaClient:
    def __init__(self, *, role: str, process: subprocess.Popen[str],
                 expected_java: Path, journal: EventJournal, raw_log: Path) -> None:
        self.role = role
        self.process = process
        self.expected_java = expected_java
        self.journal = journal
        self.raw_log = raw_log
        self.expected_identity = process_identity(process.pid)
        if self.expected_identity is None or not validate_process_identity(
                self.expected_identity, self.expected_identity, expected_java):
            raise RuntimeError(f"could not prove {role} child is the expected Java executable: pid={process.pid}")
        self._events: deque[dict[str, Any]] = deque()
        self._condition = threading.Condition()
        self.pending_read_label: str | None = None
        self.acquired_event: dict[str, Any] | None = None
        self._reader = threading.Thread(target=self._read_stdout, name=f"{role}-stdout", daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        with self.raw_log.open("a", encoding="utf-8") as raw:
            for line in self.process.stdout:
                raw.write(line)
                raw.flush()
                parsed = encode_event_line(line)
                received = time.monotonic_ns()
                if parsed is not None:
                    event = {
                        **parsed,
                        "role": self.role,
                        "child_pid": self.process.pid,
                        "received_monotonic_ns": received,
                    }
                    self.journal.write(
                        "CLIENT_EVENT_RECEIVED",
                        sampled_monotonic_ns=received,
                        role=self.role,
                        child_pid=self.process.pid,
                        child_event=parsed["name"],
                        detail=parsed["detail"],
                    )
                    with self._condition:
                        self._events.append(event)
                        self._condition.notify_all()
                else:
                    self.journal.write("CLIENT_STDOUT", role=self.role,
                                       child_pid=self.process.pid, line=line.rstrip("\r\n"))
        with self._condition:
            self._condition.notify_all()

    def wait_event(self, name: str, timeout_s: float, *, detail_prefix: str | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                for index, event in enumerate(self._events):
                    if event["name"] == "FATAL":
                        del self._events[index]
                        raise RuntimeError(f"{self.role} client fatal event: {event['detail']}")
                    if event["name"] == name and (
                            detail_prefix is None or event["detail"].startswith(detail_prefix)):
                        del self._events[index]
                        return event
                if self.process.poll() is not None:
                    raise RuntimeError(f"{self.role} client exited with {self.process.returncode} before {name}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out waiting for {self.role} event {name}")
                self._condition.wait(min(remaining, 0.1))

    def wait_read_result(self, label: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        prefix = f"label={label}\t"
        with self._condition:
            while True:
                for index, event in enumerate(self._events):
                    if event["name"] == "FATAL":
                        del self._events[index]
                        raise RuntimeError(f"{self.role} client fatal event: {event['detail']}")
                    if event["name"] in {"READ_CALL_RETURNED", "READ_CALL_ERROR"} and event["detail"].startswith(prefix):
                        del self._events[index]
                        return event
                if self.process.poll() is not None:
                    raise RuntimeError(f"{self.role} client exited during tags() read for {label}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out waiting for observer tags() result {label}")
                self._condition.wait(min(remaining, 0.1))

    def has_event(self, name: str) -> dict[str, Any] | None:
        with self._condition:
            for index, event in enumerate(self._events):
                if event["name"] == name:
                    del self._events[index]
                    return event
        return None

    def send(self, command: str, *, event_name: str) -> int:
        if self.process.poll() is not None or self.process.stdin is None:
            raise RuntimeError(f"cannot send command to exited {self.role} client")
        # The sampled supervisor time is the command boundary. It is sampled
        # immediately before the write and is never compared with JVM clocks.
        sampled = time.monotonic_ns()
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        self.journal.write(event_name, sampled_monotonic_ns=sampled,
                           role=self.role, child_pid=self.process.pid,
                           command=command, send_completed_monotonic_ns=time.monotonic_ns())
        return sampled

    def verify_owned_child(self) -> None:
        current = process_identity(self.process.pid)
        if not validate_process_identity(current, self.expected_identity, self.expected_java):
            raise RuntimeError(f"refusing signal to {self.role}: child PID/start/executable identity changed")

    def terminate_exact_child(self, *, event_name: str) -> int:
        self.verify_owned_child()
        sampled = time.monotonic_ns()
        self.process.terminate()
        sent_completed = time.monotonic_ns()
        self.journal.write(event_name, sampled_monotonic_ns=sampled,
                           role=self.role, child_pid=self.process.pid,
                           child_identity=self.expected_identity,
                           signal="SIGTERM", send_completed_monotonic_ns=sent_completed)
        return sampled

    def wait_exit(self, timeout_s: float) -> int:
        return self.process.wait(timeout=timeout_s)


class PrivateComsolServer:
    def __init__(self, *, work: Path, install_root: Path, java_home: Path,
                 journal: EventJournal) -> None:
        self.work = work
        self.install_root = install_root
        self.java_home = java_home
        self.journal = journal
        self.shadow = work / "engine-shadow"
        self.runtime = work / "runtime"
        self.port: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.process_identity_at_birth: dict[str, Any] | None = None
        self.log_path = work / "mphserver.log"
        self._log_file = None
        self.install_config_hash_before: str | None = None
        self.shadow_config: Path | None = None

    def prepare(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError(f"native experiment is frozen for macOS; got {sys.platform}")
        if not self.install_root.is_dir() or not self.java_home.is_dir():
            raise RuntimeError("the inventoried COMSOL 6.4 installation or bundled Java runtime is unavailable")
        if not JAVA_SOURCE.is_file() or not CLASSPATH_PREFLIGHT_SOURCE.is_file():
            raise RuntimeError("the frozen Java client or classpath preflight source is missing")
        dirs = [self.runtime / name for name in ("prefs", "tmp", "recovery", "home", "project", "classes")]
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=False)

        self.shadow.mkdir()
        for entry in self.install_root.iterdir():
            if entry.name == "bin":
                continue
            target = self.shadow / entry.name
            target.symlink_to(entry, target_is_directory=entry.is_dir())
        private_bin = self.shadow / "bin"
        private_bin.mkdir()
        for entry in (self.install_root / "bin").iterdir():
            target = private_bin / entry.name
            if entry.name == "servers":
                shutil.copytree(entry, target, symlinks=True)
            elif entry.is_dir():
                target.symlink_to(entry, target_is_directory=True)
            else:
                shutil.copy2(entry, target)

        source_config = self.install_root / "bin/servers/webbridge/conf/server.xml"
        self.shadow_config = self.shadow / "bin/servers/webbridge/conf/server.xml"
        self.install_config_hash_before = sha256_file(source_config)
        tree = ET.parse(self.shadow_config)
        connectors = tree.findall(".//Connector")
        if len(connectors) != 1:
            raise RuntimeError(f"expected one WebBridge connector, found {len(connectors)}")
        connectors[0].set("address", "127.0.0.1")
        tree.write(self.shadow_config, encoding="UTF-8", xml_declaration=True)
        if sha256_file(source_config) != self.install_config_hash_before:
            raise RuntimeError("installed COMSOL server.xml changed while staging private copy")
        private_connectors = ET.parse(self.shadow_config).findall(".//Connector")
        if len(private_connectors) != 1 or private_connectors[0].get("address") != "127.0.0.1":
            raise RuntimeError("shadow server WebBridge connector is not loopback-only")

        help_result = subprocess.run(
            [str(private_bin / "comsol"), "mphserver", "-help"],
            capture_output=True, text=True, timeout=25, check=False,
        )
        if help_result.returncode != 0 or "-portfile <path>" not in help_result.stdout:
            raise RuntimeError("private COMSOL launcher help did not confirm portfile options")
        self.journal.write(
            "PRIVATE_SHADOW_PREPARED",
            install_root=str(self.install_root), shadow_root=str(self.shadow),
            installed_server_xml_sha256=self.install_config_hash_before,
            private_server_xml_sha256=sha256_file(self.shadow_config),
            private_connector_address="127.0.0.1",
            help_exit_code=help_result.returncode,
            help_stdout_sha256=hashlib.sha256(help_result.stdout.encode()).hexdigest(),
            help_stderr=help_result.stderr[-2000:],
            install_tree_mutated=False,
        )

    def start(self, deadline: float) -> int:
        port_file = self.runtime / "server.port"
        cmd = [
            str(self.shadow / "bin/comsol"), "mphserver", "-port", "0",
            "-portfile", str(port_file), "-prefsdir", str(self.runtime / "prefs"),
            "-tmpdir", str(self.runtime / "tmp"), "-recoverydir", str(self.runtime / "recovery"),
            "-login", "auto", "-silent", "-multi", "on",
        ]
        self._log_file = self.log_path.open("ab", buffering=0)
        self.process = subprocess.Popen(
            cmd, cwd=self.work, stdout=self._log_file, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.process_identity_at_birth = process_identity(self.process.pid)
        self.journal.write("OWNED_SERVER_STARTED", pid=self.process.pid,
                           command=cmd, process_identity=self.process_identity_at_birth)
        if not _has_process_birth(self.process_identity_at_birth, self.process.pid):
            self.journal.write("OWNED_SERVER_PROCESS_IDENTITY_UNKNOWN", pid=self.process.pid)
            raise RuntimeError("could not verify the owned server PID, executable path, and birth identity")
        local_deadline = min(deadline, time.monotonic() + SERVER_START_BUDGET_S)
        while time.monotonic() < local_deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"owned COMSOL server exited early ({self.process.returncode})")
            if port_file.is_file():
                try:
                    port = int(port_file.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    port = 0
                if 1 <= port <= 65535:
                    self.port = port
                    break
            time.sleep(0.2)
        if self.port is None:
            raise TimeoutError("owned COMSOL server did not publish a port within 120 seconds")

        probe = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-a", "-p", str(self.process.pid),
             f"-iTCP:{self.port}", "-sTCP:LISTEN"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        rows = _lsof_listener_rows(probe)
        fields = rows[0].split() if len(rows) == 1 else []
        endpoint = fields[fields.index("TCP") + 1] if "TCP" in fields and fields.index("TCP") + 1 < len(fields) else ""
        pid = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None
        if (probe.returncode != 0 or probe.stderr.strip() or len(rows) != 1
                or pid != self.process.pid or endpoint != f"127.0.0.1:{self.port}"):
            raise RuntimeError(
                f"refusing client connect; exact owned loopback listener not verified: "
                f"rc={probe.returncode}, pid={pid}, endpoint={endpoint!r}, "
                f"stderr={probe.stderr!r}, stdout={probe.stdout!r}"
            )
        self.journal.write("LOOPBACK_LISTENER_VERIFIED", pid=pid, port=self.port,
                           endpoint=endpoint, lsof_stdout=probe.stdout, lsof_stderr=probe.stderr)
        return self.port

    def shutdown(self, *, clients_gone: bool, fresh_read_ok: bool,
                 no_clients_ever_started: bool) -> dict[str, Any]:
        if self.process is None:
            return {"status": "NOT_STARTED"}
        if not clients_gone or (not fresh_read_ok and not no_clients_ever_started):
            return {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN", "pid": self.process.pid}
        if self.process.poll() is None:
            self.journal.write("OWNED_SERVER_SHUTDOWN_REQUESTED", pid=self.process.pid,
                               shutdown_kind=(
                                   "normal SIGTERM after no client ever connected"
                                   if no_clients_ever_started and not fresh_read_ok else
                                   "normal SIGTERM after client exit and fresh API read"
                               ))
            self.process.terminate()
            try:
                self.process.wait(timeout=min(SERVER_SHUTDOWN_S, max(0.0, self._remaining_budget())))
            except subprocess.TimeoutExpired:
                return {"status": "SHUTDOWN_UNKNOWN_LEFT_RUNNING", "pid": self.process.pid}
        listener_probe = subprocess.run(
            ["/usr/sbin/lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"],
            text=True, capture_output=True, timeout=5, check=False,
        ) if self.port is not None else None
        listener_rows = [] if listener_probe is None else _lsof_listener_rows(listener_probe)
        source_config = self.install_root / "bin/servers/webbridge/conf/server.xml"
        after_hash = sha256_file(source_config)
        birth_reaped = self.process.poll() is not None
        shutdown_status = _shutdown_status(
            owned_birth_reaped=birth_reaped,
            birth_identity=self.process_identity_at_birth,
            listener_probe=listener_probe,
        )
        result = {
            "status": shutdown_status,
            "pid": self.process.pid,
            "return_code": self.process.returncode,
            "owned_server_birth_identity": self.process_identity_at_birth,
            "owned_server_birth_reaped": birth_reaped,
            "listener_probe_returncode": None if listener_probe is None else listener_probe.returncode,
            "listener_probe_stderr": None if listener_probe is None else listener_probe.stderr,
            "listener_probe_complete": listener_probe is not None and _lsof_probe_complete(listener_probe),
            "listener_probe_state": (
                "UNKNOWN" if listener_probe is None or not _lsof_probe_complete(listener_probe) else
                "LISTENER_PRESENT" if listener_rows else "NO_MATCH_CONFIRMED"
            ),
            "listener_after_stop": listener_rows,
            "installed_server_xml_sha256_before": self.install_config_hash_before,
            "installed_server_xml_sha256_after": after_hash,
            "install_tree_mutated": after_hash != self.install_config_hash_before,
        }
        self.journal.write("OWNED_SERVER_SHUTDOWN_RESULT", **result)
        if self._log_file is not None:
            self._log_file.close()
        return result

    def _remaining_budget(self) -> float:
        deadline = getattr(self, "overall_deadline", None)
        return TOTAL_BUDGET_S if deadline is None else deadline - time.monotonic()


class Experiment:
    def __init__(self, *, work: Path, install_root: Path, java_home: Path,
                 javac: Path,
                 budget_s: float = TOTAL_BUDGET_S) -> None:
        self.work = work
        self.install_root = install_root
        self.java_home = java_home
        self.javac = javac
        self.budget_s = budget_s
        self.started_ns = time.monotonic_ns()
        self.deadline = time.monotonic() + budget_s
        self.work.mkdir(parents=True, exist_ok=False)
        os.chmod(self.work, 0o700)
        self.runtime = self.work / "runtime"
        self.journal = EventJournal(self.work / "events.jsonl", self.started_ns)
        self.server = PrivateComsolServer(work=self.work, install_root=install_root,
                                          java_home=java_home, journal=self.journal)
        self.server.overall_deadline = self.deadline
        self.java_exe = java_home / "bin/java"
        self.classes = self.runtime / "classes"
        self.clients: list[ManagedJavaClient] = []
        self.observer: ManagedJavaClient | None = None
        self.owner: ManagedJavaClient | None = None
        self.fresh_read_ok = False
        self.result: dict[str, Any] = {
            "schema": "comsol-mcp-w25-block-other-clients/1",
            "status": "RUNNING",
            "target": "COMSOL 6.4 API-only, task-owned loopback mphserver",
            "started_at_utc": utc_now(),
            "work_dir": str(work),
            "budget_s": budget_s,
            "native_models_created": 0,
            "native_solves": 0,
            "trials": [],
            "not_tested": ["Desktop GUI", "model/window binding", "COMSOL 6.3", "Windows", "solver behavior"],
        }

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def stage_wait(self, timeout_s: float, *, reserve_cleanup: bool = True) -> float:
        available = self.remaining() - (25.0 if reserve_cleanup else 0.0)
        left = min(timeout_s, available)
        if left <= 0:
            raise TimeoutError("overall 300-second experiment budget exhausted")
        return left

    def _clean_java_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "PYTHONPATH"):
            env.pop(name, None)
        env.update({
            "HOME": str(self.runtime / "home"),
            "JAVA_HOME": str(self.java_home),
            "COMSOL_ROOT": str(self.install_root),
            "COMSOL_PREFS_DIR": str(self.runtime / "prefs"),
            "TMPDIR": str(self.runtime / "tmp"),
            "LC_ALL": "C",
        })
        return env

    def compile_source(self) -> dict[str, Any]:
        record = client_classpath_preflight(
            install_root=self.install_root,
            java_home=self.java_home,
            javac=self.javac,
            classes=self.classes,
        )
        (self.work / "runtime_preflight.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.work / "javac.json").write_text(
            json.dumps(record["compile"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.result["client_runtime_preflight"] = {
            "status": record["status"],
            "java_home": record["java_home"],
            "java_version_output": record["java_version_output"],
            "javac": record["javac"],
            "javac_version_output": record["javac_version_output"],
            "classpath": record["classpath"],
            "loaded_classes": record["classpath_probe"]["loaded_classes"],
            "runtime_preflight_sha256": sha256_file(self.work / "runtime_preflight.json"),
        }
        self.journal.write("CLIENT_RUNTIME_PREFLIGHT_PASS",
                           java_home=record["java_home"],
                           java_version=record["java_version_output"],
                           javac=record["javac"],
                           javac_version=record["javac_version_output"],
                           loaded_classes=record["classpath_probe"]["loaded_classes"],
                           runtime_preflight_sha256=self.result["client_runtime_preflight"]["runtime_preflight_sha256"])
        self.journal.write("JAVA_CLIENT_COMPILE_PASS", **record["compile"],
                           compiled_classes=record["compiled_classes"],
                           java_source_sha256=record["source_sha256"][str(JAVA_SOURCE.resolve())])
        return record

    def _client_command(self, *args: str) -> list[str]:
        if self.server.port is None:
            raise RuntimeError("server port is not verified")
        prefs = (self.runtime / "prefs").resolve()
        return [str(self.java_exe), f"-Dcs.prefsdir={prefs}",
                "-cp", f"{self.classes}:{self.install_root}/plugins/*",
                "BlockOtherClientsClient", *args[:1], "127.0.0.1", str(self.server.port), *args[1:]]

    def _start_client(self, role: str, *args: str) -> ManagedJavaClient:
        cmd = self._client_command(*args)
        log_path = self.work / f"{role.lower()}-{uuid4().hex[:8]}.stdout.log"
        process = subprocess.Popen(
            cmd, cwd=self.work, env=self._clean_java_env(), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, start_new_session=True,
        )
        self.journal.write("JAVA_CLIENT_STARTED", role=role, pid=process.pid, command=cmd,
                           process_identity=process_identity(process.pid))
        client = ManagedJavaClient(role=role, process=process,
                                   expected_java=self.java_exe, journal=self.journal,
                                   raw_log=log_path)
        self.clients.append(client)
        return client

    def _observer(self) -> ManagedJavaClient:
        if self.observer is None or self.observer.process.poll() is not None:
            self.observer = self._start_client("B", "observer", "observer")
            self.observer.wait_event("CONNECTED", self.stage_wait(CONNECT_BUDGET_S))
        return self.observer

    def _observer_read_any(self, label: str, *,
                           timeout_s: float = RELEASE_RECOVERY_S) -> dict[str, Any]:
        client = self._observer()
        command_ns = client.send(f"READ\t{label}", event_name="OBSERVER_READ_COMMAND_SENT")
        client.pending_read_label = label
        entered = client.wait_event("READ_CALL_ENTERED", self.stage_wait(timeout_s),
                                    detail_prefix=f"label={label}")
        result = client.wait_read_result(label, self.stage_wait(timeout_s))
        client.pending_read_label = None
        if result["name"] == "READ_CALL_RETURNED":
            self.fresh_read_ok = True
        return {"command_sent_ns": command_ns, "entered": entered, "result": result}

    def _observer_read(self, label: str, *, timeout_s: float = RELEASE_RECOVERY_S) -> dict[str, Any]:
        read = self._observer_read_any(label, timeout_s=timeout_s)
        if read["result"]["name"] != "READ_CALL_RETURNED":
            raise RuntimeError(
                f"observer tags() failed outside a measured blocking trial: {read['result']['detail']}"
            )
        return {"entered": read["entered"], "returned": read["result"],
                "command_sent_ns": read["command_sent_ns"]}

    def _start_owner(self, mode: str, trial: str) -> ManagedJavaClient:
        args = (mode, trial)
        client = self._start_client("A", *args)
        self.owner = client
        client.wait_event("CONNECTED", self.stage_wait(ACQUIRE_BUDGET_S),
                          detail_prefix=f"role=A\ttrial={trial}")
        client.acquired_event = client.wait_event(
            "ACQUIRED", self.stage_wait(ACQUIRE_BUDGET_S), detail_prefix=f"trial={trial}")
        return client

    def _verify_blocking(self, trial: str, command_event: str | None) -> dict[str, Any]:
        observer = self._observer()
        read_command_ns = observer.send(f"READ\t{trial}", event_name="OBSERVER_BLOCKED_READ_SENT")
        observer.pending_read_label = trial
        entered = observer.wait_event("READ_CALL_ENTERED", self.stage_wait(ACQUIRE_BUDGET_S),
                                      detail_prefix=f"label={trial}")
        try:
            early = observer.wait_read_result(trial, self.stage_wait(BLOCK_OBSERVE_S))
        except TimeoutError:
            early = None
        if early is not None and early["name"] == "READ_CALL_RETURNED":
            observer.pending_read_label = None
            self.fresh_read_ok = True
            self.journal.write("BLOCKING_ASSERTION_FAILED", trial=trial,
                               reason="observer read returned before release/injection command",
                               result_event_received_ns=early["received_monotonic_ns"])
            raise AssertionError(f"observer read returned before {command_event}")
        if early is not None:
            observer.pending_read_label = None
            self.journal.write("OBSERVER_PRE_RELEASE_API_ERROR_CAPTURED", trial=trial,
                               detail=early["detail"],
                               result_event_received_ns=early["received_monotonic_ns"])
        return {
            "blocking_observation": (
                "PRE_RELEASE_API_ERROR" if early is not None else
                "READ_DID_NOT_RETURN_DURING_1S_WINDOW"
            ),
            "read_command_sent_ns": read_command_ns,
            "read_entered_event": entered,
            "pre_release_result_event": early,
        }

    def _finish_observer_after_release(
            self, trial: str, owner: ManagedJavaClient, command_ns: int,
            proof: dict[str, Any], release_returned: dict[str, Any],
            owner_ready: dict[str, Any]) -> dict[str, Any]:
        observer = self._observer()
        if owner.process.poll() is not None:
            raise RuntimeError("owner exited before the independent post-release read")

        original = proof.get("pre_release_result_event")
        blocking_verdict = ""
        if original is not None:
            is_busy = exact_pre_control_busy_refusal(
                trial=trial,
                acquired_event=owner.acquired_event or {},
                read_command_sent_ns=proof["read_command_sent_ns"],
                read_entered_event=proof["read_entered_event"],
                read_result_event=original,
                control_boundary_ns=command_ns,
            )
            blocking_verdict = "BUSY_REFUSAL" if is_busy else "UNCLASSIFIED_PRE_RELEASE_API_ERROR"
        else:
            original = observer.wait_read_result(trial, self.stage_wait(RELEASE_RECOVERY_S))
            observer.pending_read_label = None
            if original["name"] == "READ_CALL_RETURNED":
                if not release_order_is_acceptable(
                        child_result_received_ns=original["received_monotonic_ns"],
                        supervisor_command_sent_ns=command_ns):
                    blocking_verdict = "RETURNED_BEFORE_RELEASE_BOUNDARY"
                elif not recovery_within_budget(
                        child_result_received_ns=original["received_monotonic_ns"],
                        supervisor_command_sent_ns=command_ns):
                    blocking_verdict = "WAIT_COMPLETION_EXCEEDED_BUDGET"
                else:
                    blocking_verdict = "WAITED_COMPLETION"
            elif original["received_monotonic_ns"] < command_ns:
                is_busy = exact_pre_control_busy_refusal(
                    trial=trial,
                    acquired_event=owner.acquired_event or {},
                    read_command_sent_ns=proof["read_command_sent_ns"],
                    read_entered_event=proof["read_entered_event"],
                    read_result_event=original,
                    control_boundary_ns=command_ns,
                )
                blocking_verdict = "BUSY_REFUSAL" if is_busy else "UNCLASSIFIED_PRE_RELEASE_API_ERROR"
            else:
                blocking_verdict = "UNCLASSIFIED_POST_RELEASE_API_ERROR"

        self.journal.write("PRE_RELEASE_BLOCKING_VERDICT", trial=trial,
                           verdict=blocking_verdict,
                           result_event=original,
                           owner_acquired_event=owner.acquired_event,
                           read_command_sent_ns=proof["read_command_sent_ns"],
                           read_entered_event=proof["read_entered_event"],
                           release_command_sent_ns=command_ns)

        # The owner remains connected and makes no API calls after RELEASE_RETURNED.
        # This is a new B.tags() request, independent of the possibly pending call.
        fresh = self._observer_read_any("post_release_" + trial,
                                        timeout_s=RELEASE_RECOVERY_S)
        finish_ns = owner.send("FINISH", event_name="OWNER_FINISH_COMMAND_SENT")
        finish_accepted = owner.wait_event("OWNER_FINISH_ACCEPTED",
                                           self.stage_wait(RELEASE_RECOVERY_S),
                                           detail_prefix=f"trial={trial}")
        disconnect_requested = owner.wait_event(
            "DISCONNECT_REQUESTED", self.stage_wait(RELEASE_RECOVERY_S),
            detail_prefix=f"trial={trial}")
        disconnected = owner.wait_event("DISCONNECTED", self.stage_wait(RELEASE_RECOVERY_S),
                                        detail_prefix=f"trial={trial}")
        owner.wait_exit(self.stage_wait(RELEASE_RECOVERY_S))
        self.owner = None
        if not post_release_handshake_order_is_acceptable(
                trial=trial,
                release_command_sent_ns=command_ns,
                release_returned_event=release_returned,
                owner_ready_event=owner_ready,
                fresh_read_command_sent_ns=fresh["command_sent_ns"],
                fresh_read_entered_event=fresh["entered"],
                fresh_read_result_event=fresh["result"],
                finish_command_sent_ns=finish_ns,
                finish_accepted_event=finish_accepted,
                disconnect_requested_event=disconnect_requested):
            raise AssertionError("FINISH handshake does not prove an independent read before disconnect")

        fresh_ok = fresh["result"]["name"] == "READ_CALL_RETURNED"
        if fresh_ok:
            self.fresh_read_ok = True
        if blocking_verdict == "WAITED_COMPLETION" and fresh_ok:
            verdict = "WAITED_COMPLETION"
            accepted = True
        elif blocking_verdict == "BUSY_REFUSAL" and fresh_ok:
            verdict = "BUSY_REFUSAL_RECOVERED"
            accepted = True
        elif blocking_verdict == "BUSY_REFUSAL":
            verdict = "BUSY_REFUSAL_RECOVERY_FAILED"
            accepted = False
        elif not fresh_ok:
            verdict = "POST_RELEASE_FRESH_READ_FAILED"
            accepted = False
        else:
            verdict = blocking_verdict
            accepted = False
        return {
            "blocking_verdict": blocking_verdict,
            "verdict": verdict,
            "accepted_for_acceptance": accepted,
            "original_b_read_result": original,
            "post_release_independent_b_read": fresh,
            "owner_ready_received_ns": owner_ready["received_monotonic_ns"],
            "release_returned_received_ns": release_returned["received_monotonic_ns"],
            "owner_finish_command_sent_ns": finish_ns,
            "owner_finish_accepted_received_ns": finish_accepted["received_monotonic_ns"],
            "owner_disconnect_requested_received_ns": disconnect_requested["received_monotonic_ns"],
            "owner_disconnected_received_ns": disconnected["received_monotonic_ns"],
        }

    def run_explicit_release(self) -> dict[str, Any]:
        trial = "explicit_release"
        self._observer_read("pre_" + trial)
        owner = self._start_owner("explicit", trial)
        proof = self._verify_blocking(trial, "RELEASE_COMMAND_SENT")
        command_ns = owner.send("RELEASE", event_name="RELEASE_COMMAND_SENT")
        self.journal.write("RELEASE_COMMAND_BOUNDARY", trial=trial,
                           supervisor_command_sent_ns=command_ns)
        release_requested = owner.wait_event("RELEASE_REQUESTED", self.stage_wait(RELEASE_RECOVERY_S))
        release_returned = owner.wait_event("RELEASE_RETURNED", self.stage_wait(RELEASE_RECOVERY_S),
                                            detail_prefix=f"trial={trial}")
        owner_ready = owner.wait_event("OWNER_READY_FOR_POST_RELEASE_READ",
                                       self.stage_wait(RELEASE_RECOVERY_S),
                                       detail_prefix=f"trial={trial}")
        observer_outcome = self._finish_observer_after_release(
            trial, owner, command_ns, proof, release_returned, owner_ready)
        return {"trial": trial,
                "status": observer_outcome["verdict"],
                "accepted_for_acceptance": observer_outcome["accepted_for_acceptance"],
                **proof, "observer_outcome": observer_outcome,
                "supervisor_release_command_sent_ns": command_ns,
                "a_release_requested_received_ns": release_requested["received_monotonic_ns"]}

    def run_finally_release(self) -> dict[str, Any]:
        trial = "exception_finally"
        self._observer_read("pre_" + trial)
        owner = self._start_owner("finally", trial)
        proof = self._verify_blocking(trial, "INJECT_COMMAND_SENT")
        command_ns = owner.send("INJECT", event_name="INJECT_COMMAND_SENT")
        self.journal.write("INJECT_COMMAND_BOUNDARY", trial=trial,
                           supervisor_command_sent_ns=command_ns)
        injected = owner.wait_event("INJECT_ACCEPTED", self.stage_wait(RELEASE_RECOVERY_S))
        sentinel = owner.wait_event("SENTINEL_CAUGHT", self.stage_wait(RELEASE_RECOVERY_S))
        release_requested = owner.wait_event("RELEASE_REQUESTED", self.stage_wait(RELEASE_RECOVERY_S))
        release_returned = owner.wait_event("RELEASE_RETURNED", self.stage_wait(RELEASE_RECOVERY_S),
                                            detail_prefix=f"trial={trial}")
        owner_ready = owner.wait_event("OWNER_READY_FOR_POST_RELEASE_READ",
                                       self.stage_wait(RELEASE_RECOVERY_S),
                                       detail_prefix=f"trial={trial}")
        observer_outcome = self._finish_observer_after_release(
            trial, owner, command_ns, proof, release_returned, owner_ready)
        return {"trial": trial,
                "status": observer_outcome["verdict"],
                "accepted_for_acceptance": observer_outcome["accepted_for_acceptance"],
                **proof, "observer_outcome": observer_outcome,
                "supervisor_inject_command_sent_ns": command_ns,
                "a_inject_accepted_received_ns": injected["received_monotonic_ns"],
                "a_sentinel_caught_received_ns": sentinel["received_monotonic_ns"],
                "a_release_requested_received_ns": release_requested["received_monotonic_ns"]}

    def run_disconnect_recovery(self) -> dict[str, Any]:
        trial = "owner_disconnect"
        self._observer_read("pre_" + trial)
        owner = self._start_owner("disconnect", trial)
        owner.send("HOLD", event_name="OWNER_HOLD_COMMAND_SENT")
        owner.wait_event("HOLD_ACCEPTED", self.stage_wait(ACQUIRE_BUDGET_S))
        proof = self._verify_blocking(trial, "OWNER_TERMINATE_SENT")
        terminate_ns = owner.terminate_exact_child(event_name="OWNER_TERMINATE_SENT")
        try:
            owner.wait_exit(self.stage_wait(ACQUIRE_BUDGET_S))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("owned A child did not exit after exact-child termination") from exc
        original = proof.get("pre_release_result_event")
        if original is None:
            original = self._observer().wait_read_result(trial, self.stage_wait(RELEASE_RECOVERY_S))
            self._observer().pending_read_label = None
        if original["name"] == "READ_CALL_RETURNED":
            if (release_order_is_acceptable(
                    child_result_received_ns=original["received_monotonic_ns"],
                    supervisor_command_sent_ns=terminate_ns)
                    and recovery_within_budget(
                        child_result_received_ns=original["received_monotonic_ns"],
                        supervisor_command_sent_ns=terminate_ns)):
                blocking_verdict = "OWNER_DISCONNECT_WAITED_COMPLETION"
            else:
                blocking_verdict = "RETURNED_BEFORE_OR_TOO_LONG_AFTER_DISCONNECT"
        elif original["received_monotonic_ns"] < terminate_ns:
            busy = exact_pre_control_busy_refusal(
                trial=trial,
                acquired_event=owner.acquired_event or {},
                read_command_sent_ns=proof["read_command_sent_ns"],
                read_entered_event=proof["read_entered_event"],
                read_result_event=original,
                control_boundary_ns=terminate_ns,
            )
            blocking_verdict = "BUSY_REFUSAL" if busy else "UNCLASSIFIED_PRE_DISCONNECT_API_ERROR"
        else:
            blocking_verdict = "UNCLASSIFIED_POST_DISCONNECT_API_ERROR"
        self.owner = None
        fresh = self._observer_read_any("post_disconnect_" + trial,
                                        timeout_s=RELEASE_RECOVERY_S)
        fresh_ok = fresh["result"]["name"] == "READ_CALL_RETURNED"
        if blocking_verdict == "OWNER_DISCONNECT_WAITED_COMPLETION" and fresh_ok:
            verdict = "OWNER_DISCONNECT_WAITED_COMPLETION"
            accepted = True
        elif blocking_verdict == "BUSY_REFUSAL" and fresh_ok:
            verdict = "OWNER_DISCONNECT_BUSY_REFUSAL_RECOVERED"
            accepted = True
        elif not fresh_ok:
            verdict = "POST_DISCONNECT_FRESH_READ_FAILED"
            accepted = False
        else:
            verdict = blocking_verdict
            accepted = False
        return {"trial": trial,
                "status": verdict,
                "accepted_for_acceptance": accepted,
                **proof,
                "blocking_verdict": blocking_verdict,
                "original_b_read_result": original,
                "supervisor_owner_terminate_sent_ns": terminate_ns,
                "post_disconnect_independent_b_read": fresh}

    def journal_last(self, event_name: str) -> int:
        latest = None
        for line in (self.work / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == event_name:
                latest = row.get("supervisor_monotonic_ns")
        if not isinstance(latest, int):
            raise RuntimeError(f"missing supervisor event boundary {event_name}")
        return latest

    def run(self) -> dict[str, Any]:
        foreign = _foreign_comsol_processes()
        self.journal.write("PRELAUNCH_COMSOL_INVENTORY", rows=foreign)
        if foreign:
            self.result.update(status="BLOCKED_FOREIGN_COMSOL_PROCESS", foreign_comsol_processes=foreign)
            return self.result
        self.server.prepare()
        self.compile_source()
        self.server.start(self.deadline)
        observer = self._observer()
        baseline = self._observer_read("initial_baseline")
        if "tag_count=0" not in baseline["returned"]["detail"]:
            raise RuntimeError("the new task-owned server was not empty before any trial")
        self.result["baseline"] = {
            "observer_connected": True,
            "tags_read_returned": baseline["returned"]["detail"],
            "model_count": 0,
        }
        trial_runners = (
            ("explicit_release", self.run_explicit_release),
            ("exception_finally", self.run_finally_release),
            ("owner_disconnect", self.run_disconnect_recovery),
        )

        def record_not_run(start_index: int, reason: str) -> None:
            for skipped_name, _ in trial_runners[start_index:]:
                self.result["trials"].append({
                    "trial": skipped_name,
                    "status": "NOT_RUN",
                    "accepted_for_acceptance": False,
                    "reason": reason,
                })

        for index, (trial_name, run_trial) in enumerate(trial_runners):
            try:
                trial_result = run_trial()
            except TimeoutError as exc:
                self.result["trials"].append({
                    "trial": trial_name,
                    "status": "UNKNOWN",
                    "accepted_for_acceptance": False,
                    "error": str(exc),
                })
                record_not_run(index + 1, "prior distinct trial ended UNKNOWN; no automatic retry")
                raise
            except Exception as exc:
                self.result["trials"].append({
                    "trial": trial_name,
                    "status": "FAILED",
                    "accepted_for_acceptance": False,
                    "error": repr(exc),
                })
                record_not_run(index + 1, "prior distinct trial failed; no automatic retry")
                raise
            self.result["trials"].append(trial_result)
            if not trial_result.get("accepted_for_acceptance"):
                record_not_run(index + 1,
                               "prior distinct trial did not reach an accepted terminal verdict")
                break
        return self.result

    def cleanup(self) -> dict[str, Any]:
        # Resolve owned Java processes before server shutdown. A held lock is
        # released by exact-owner disconnect if a trial did not finish.
        clients_gone = True
        if self.owner is not None and self.owner.process.poll() is None:
            try:
                self.owner.terminate_exact_child(event_name="CLEANUP_OWNER_TERMINATE_SENT")
                self.owner.wait_exit(self.stage_wait(ACQUIRE_BUDGET_S, reserve_cleanup=False))
            except Exception as exc:
                clients_gone = False
                self.journal.write("CLEANUP_OWNER_UNKNOWN", error=repr(exc), pid=self.owner.process.pid)
        if self.observer is not None and self.observer.process.poll() is None:
            try:
                # Complete an existing read after owner disconnect, or issue
                # one cleanup probe only when no prior API call is pending.
                label = self.observer.pending_read_label
                if label is not None:
                    completed = self.observer.wait_read_result(
                        label, self.stage_wait(RELEASE_RECOVERY_S, reserve_cleanup=False))
                    self.observer.pending_read_label = None
                    self.fresh_read_ok = completed["name"] == "READ_CALL_RETURNED"
                    if not self.fresh_read_ok:
                        label = "cleanup_probe_after_api_error"
                        self.observer.send(f"READ\t{label}", event_name="CLEANUP_OBSERVER_READ_SENT")
                        self.observer.pending_read_label = label
                        self.observer.wait_event("READ_CALL_ENTERED", self.stage_wait(
                            RELEASE_RECOVERY_S, reserve_cleanup=False), detail_prefix=f"label={label}")
                        completed = self.observer.wait_read_result(
                            label, self.stage_wait(RELEASE_RECOVERY_S, reserve_cleanup=False))
                        self.observer.pending_read_label = None
                        self.fresh_read_ok = completed["name"] == "READ_CALL_RETURNED"
                elif not self.fresh_read_ok:
                    label = "cleanup_probe"
                    self.observer.send(f"READ\t{label}", event_name="CLEANUP_OBSERVER_READ_SENT")
                    self.observer.pending_read_label = label
                    self.observer.wait_event("READ_CALL_ENTERED", self.stage_wait(
                        RELEASE_RECOVERY_S, reserve_cleanup=False), detail_prefix=f"label={label}")
                    self.observer.wait_event("READ_CALL_RETURNED", self.stage_wait(
                        RELEASE_RECOVERY_S, reserve_cleanup=False), detail_prefix=f"label={label}")
                    self.observer.pending_read_label = None
                    self.fresh_read_ok = True
                if self.fresh_read_ok:
                    self.observer.send("DISCONNECT", event_name="OBSERVER_DISCONNECT_COMMAND_SENT")
                    self.observer.wait_event("DISCONNECTED", self.stage_wait(RELEASE_RECOVERY_S, reserve_cleanup=False))
                    self.observer.wait_exit(self.stage_wait(RELEASE_RECOVERY_S, reserve_cleanup=False))
                else:
                    self.observer.terminate_exact_child(event_name="CLEANUP_OBSERVER_TERMINATE_SENT")
                    self.observer.wait_exit(self.stage_wait(ACQUIRE_BUDGET_S, reserve_cleanup=False))
            except Exception as exc:
                clients_gone = False
                self.journal.write("CLEANUP_OBSERVER_UNKNOWN", error=repr(exc), pid=self.observer.process.pid)
        remaining = [c.process.pid for c in self.clients if c.process.poll() is None]
        clients_gone = clients_gone and not remaining
        server_result = self.server.shutdown(
            clients_gone=clients_gone,
            fresh_read_ok=self.fresh_read_ok,
            no_clients_ever_started=not self.clients,
        )
        self.result["cleanup"] = {
            "clients_gone": clients_gone,
            "remaining_java_pids": remaining,
            "fresh_api_read_proved_server_responsive": self.fresh_read_ok,
            "server": server_result,
        }
        return self.result["cleanup"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-native", action="store_true",
                      help="start the private COMSOL 6.4 server and run the frozen API-only experiment")
    mode.add_argument("--client-runtime-preflight-only", action="store_true",
                      help="compile and load the COMSOL client classpath offline; never starts or connects to a server")
    parser.add_argument("--work", type=Path, required=True,
                        help="new task-owned APFS directory under /private/tmp")
    parser.add_argument("--comsol-root", type=Path, default=DEFAULT_COMSOL)
    parser.add_argument("--java-home", type=Path,
                        help="client runtime Java home; defaults to the architecture-matched COMSOL-bundled runtime")
    parser.add_argument("--javac", type=Path, default=DEFAULT_JAVAC,
                        help="compiler executable, recorded separately from the client runtime")
    parser.add_argument("--budget-s", type=float, default=TOTAL_BUDGET_S)
    args = parser.parse_args(argv)
    install_root = args.comsol_root.expanduser().resolve()
    try:
        java_home = args.java_home.expanduser().resolve() if args.java_home else default_java_home(install_root)
    except (FileNotFoundError, OSError) as exc:
        parser.error(str(exc))
    javac = args.javac.expanduser().resolve()
    work = args.work.expanduser().resolve()

    if args.client_runtime_preflight_only:
        preflight_prefix = "/private/tmp/comsol-mcp-w25-runtime-preflight-"
        if not str(work).startswith(preflight_prefix):
            parser.error("offline preflight --work must be a unique task directory under " + preflight_prefix + "*")
        if work.exists():
            parser.error(f"--work already exists: {work}")
        work.mkdir(parents=True, exist_ok=False)
        os.chmod(work, 0o700)
        try:
            record = client_classpath_preflight(
                install_root=install_root,
                java_home=java_home,
                javac=javac,
                classes=work / "classes",
            )
            record["created_at_utc"] = utc_now()
            record["work_dir"] = str(work)
            record["runner_source_sha256"] = sha256_file(Path(__file__).resolve())
        except Exception as exc:
            record = {
                "schema": "comsol-mcp-w25-client-runtime-preflight/1",
                "status": "CLIENT_RUNTIME_PREFLIGHT_FAIL",
                "created_at_utc": utc_now(),
                "work_dir": str(work),
                "install_root": str(install_root),
                "java_home": str(java_home),
                "javac": str(javac),
                "runner_source_sha256": sha256_file(Path(__file__).resolve()),
                "server_started": False,
                "connection_attempts": 0,
                "api_methods_invoked": 0,
                "error": repr(exc),
            }
        receipt = work / "runtime_preflight.json"
        receipt.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(record, indent=2, ensure_ascii=False))
        print(f"evidence_dir={work}")
        return 0 if record.get("status") == "CLIENT_RUNTIME_PREFLIGHT_PASS" else 1

    if args.budget_s <= 0 or args.budget_s > TOTAL_BUDGET_S:
        parser.error(f"--budget-s must be in (0, {TOTAL_BUDGET_S}]")
    if not str(work).startswith("/private/tmp/comsol-mcp-w25-blockotherclients-"):
        parser.error("--work must be a unique task directory under /private/tmp/comsol-mcp-w25-blockotherclients-*")
    if work.exists():
        parser.error(f"--work already exists: {work}")
    experiment = Experiment(work=work, install_root=install_root, java_home=java_home,
                            javac=javac, budget_s=args.budget_s)
    try:
        result = experiment.run()
        if result.get("status") == "RUNNING":
            result["status"] = "PASS" if len(result.get("trials", [])) == 3 and all(
                trial.get("accepted_for_acceptance") is True for trial in result["trials"]
            ) else "FAILED"
    except PrelaunchInventoryUnavailable as exc:
        experiment.result.update(status="BLOCKED_PREFLIGHT_PROCESS_INVENTORY_UNAVAILABLE", error=str(exc))
        experiment.journal.write("EXPERIMENT_BLOCKED_PREFLIGHT_PROCESS_INVENTORY_UNAVAILABLE", error=str(exc))
    except TimeoutError as exc:
        experiment.result.update(status="UNKNOWN", error=str(exc))
        experiment.journal.write("EXPERIMENT_UNKNOWN", error=str(exc))
    except Exception as exc:
        experiment.result.update(status="FAILED", error=repr(exc))
        experiment.journal.write("EXPERIMENT_FAILED", error=repr(exc))
    finally:
        cleanup = experiment.cleanup()
        if cleanup.get("server", {}).get("status") in {
                "LEFT_RUNNING_ACTIVE_OR_UNKNOWN", "SHUTDOWN_UNKNOWN_LEFT_RUNNING", "STOP_UNVERIFIED"}:
            experiment.result["status"] = "UNKNOWN"
        experiment.result["finished_at_utc"] = utc_now()
        experiment.result["elapsed_s"] = (time.monotonic_ns() - experiment.started_ns) / 1e9
        experiment.result["source_sha256"] = {
            str(JAVA_SOURCE): sha256_file(JAVA_SOURCE),
            str(CLASSPATH_PREFLIGHT_SOURCE): sha256_file(CLASSPATH_PREFLIGHT_SOURCE),
            str(Path(__file__).resolve()): sha256_file(Path(__file__).resolve()),
        }
        receipt = work / "receipt.json"
        receipt.write_text(json.dumps(experiment.result, indent=2, ensure_ascii=False,
                                      allow_nan=False) + "\n", encoding="utf-8")
        experiment.journal.write("RECEIPT_WRITTEN", path=str(receipt), sha256=sha256_file(receipt),
                                 status=experiment.result.get("status"))
        print(json.dumps(experiment.result, indent=2, ensure_ascii=False, allow_nan=False))
        print(f"evidence_dir={work}")
    return 0 if experiment.result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
