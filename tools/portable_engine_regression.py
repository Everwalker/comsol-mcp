#!/usr/bin/env python3
"""Portable, bounded W02/W05-W07 engine regression driver.

The fixture is built by the same small official Java API recipe on each host.
The production phase then uses a *new* private MCP control home and its
persistent Java Worker to load, modify, solve, evaluate, and save that local
fixture.  A separate ``reopen`` phase accepts another new private control home
so a fresh Worker can reopen the saved MPH after the caller has released the
first task-owned Worker.  This driver never starts, stops, or replaces COMSOL.

This is an engine integration probe, not a claim of full W04 or scientific
acceptance.  It records a bounded ephemeral-evaluation/tree-preservation check
and the variable setter/readback path.  Existing W04 evidence remains the
authoritative test for preservation of pre-existing numerical, plot, and table
nodes.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback
import uuid
from typing import Any, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Keep direct ``python tools/portable_engine_regression.py`` invocation
# independent of whether the editable package has been installed.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker
from comsol_mcp._control_client import _request as _control_request
from comsol_mcp._execution_contract import ExecutionContractError, canonical_project_path
from comsol_mcp._platform_process import process_identity
from comsol_mcp._phase1_runtime import validate_png


PHASE1_JAVA = ROOT / "comsol_mcp" / "phase1_java"
WORKER_SOURCE = ROOT / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"
JDK11_DEFAULT = "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
COMSOL_ROOT_DEFAULT = "/Applications/COMSOL64/Multiphysics"
# The portable Java recipe creates a two-dimensional fixture.  Keep this
# contract explicit so a fresh private workflow home cannot silently fall
# back to an unconfigured or differently dimensioned model.
PORTABLE_RECIPE_MODEL_DIMENSION = 2


class DriverError(RuntimeError):
    """A bounded prerequisite or engine assertion failed."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_hash() -> str:
    return _sha256(WORKER_SOURCE)


def _safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("token", "password", "credential", "authorization")):
                out[str(key)] = "REDACTED"
            else:
                out[str(key)] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_redact(_safe(value)), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DriverError(f"missing or symlinked JSON record: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DriverError(f"JSON record must be an object: {path}")
    return value


def _under_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DriverError(f"path must be inside the project root: {resolved}") from exc
    return resolved


def _artifact_path(path: Path, *, label: str) -> Path:
    """Resolve a model/result path through the production project guard."""
    try:
        return canonical_project_path(ROOT, path)
    except ExecutionContractError as exc:
        raise DriverError(f"{label} is not an approved project artifact: {exc}") from exc


def _new_artifact_directory(path: Path, *, label: str) -> Path:
    """Create a fresh evidence directory outside private credential homes."""
    resolved = _artifact_path(path, label=label)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir() or any(resolved.iterdir()):
            raise DriverError(f"{label} must be a new empty directory: {resolved}")
    else:
        resolved.mkdir(mode=0o700, parents=True)
    try:
        os.chmod(resolved, 0o700)
    except OSError:
        pass
    return resolved


def _new_directory(path: Path, *, label: str) -> Path:
    resolved = _under_root(path)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir() or any(resolved.iterdir()):
            raise DriverError(f"{label} must be a new empty directory: {resolved}")
    else:
        resolved.mkdir(mode=0o700, parents=True)
    try:
        os.chmod(resolved, 0o700)
    except OSError:
        pass
    return resolved


def _existing_private_dir(path: Path, *, label: str) -> Path:
    # This argument is an explicit caller-supplied COMSOL preferences root.
    # It must already exist and must not be a symlink, but it may live beside
    # the checked-out source tree (for example, on a Windows runtime volume).
    resolved = path.expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_dir() or resolved.name == "":
        raise DriverError(f"{label} must be an existing private directory: {resolved}")
    return resolved


def _existing_external_dir(path: Path, *, label: str) -> Path:
    """Read-only installation/JDK location; it may be outside the project."""
    resolved = path.expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_dir() or resolved.name == "":
        raise DriverError(f"{label} must be an existing directory: {resolved}")
    return resolved


def _run_logged(command: list[str], run_dir: Path, name: str, *, timeout_s: float) -> tuple[int, str]:
    log_path = run_dir / f"{name}.log"
    started = time.monotonic()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_s,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise DriverError(f"{name} timed out; inspect private log {log_path}") from exc
    output = log_path.read_text(encoding="utf-8", errors="replace")
    _write(run_dir / f"{name}.process.json", {
        "command": command,
        "returncode": completed.returncode,
        "elapsed_s": time.monotonic() - started,
        "log": str(log_path),
    })
    return completed.returncode, output


def _java_base(paths: JavaWorkerPaths, classes: Path, prefs: Path, tmp_dir: Path) -> list[str]:
    return [
        str(paths.executable("java")),
        "-Dfile.encoding=UTF-8",
        f"-Dcs.prefsdir={prefs}",
        f"-Djava.io.tmpdir={tmp_dir}",
        "-cp",
        str(classes) + paths.classpath_separator + paths.classpath()[0],
    ]


def _prepare_fixture(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    run_dir = _new_artifact_directory(Path(args.run_dir), label="fixture run directory")
    comsol_root = _existing_external_dir(Path(args.comsol_root), label="COMSOL root")
    jdk_home = _existing_external_dir(Path(args.jdk11), label="JDK 11 home")
    prefs = _existing_private_dir(Path(args.prefs), label="COMSOL preferences")
    tmp_dir = run_dir / "java-tmp"
    tmp_dir.mkdir(mode=0o700)
    paths = JavaWorkerPaths(comsol_root, jdk_home, prefs, project_root=ROOT)
    paths.validate()
    classpath, classpath_sha256, jar_count, _ = paths.classpath()
    java_version = subprocess.run(
        [str(paths.executable("java")), "-version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if java_version.returncode != 0 or not re.search(r'version "11\.', java_version.stderr):
        raise DriverError("fixture preparation requires an external JDK 11")
    sources = sorted(PHASE1_JAVA.glob("*.java"))
    if not sources:
        raise DriverError(f"no portable Java fixture sources found under {PHASE1_JAVA}")
    source_hashes = {source.name: _sha256(source) for source in sources}
    classes = run_dir / "classes"
    classes.mkdir(mode=0o700)
    compile_command = [str(paths.executable("javac")), "-cp", classpath, "-d", str(classes)] + [str(source) for source in sources]
    code, _ = _run_logged(compile_command, run_dir, "compile", timeout_s=float(args.compile_timeout_s))
    if code != 0:
        raise DriverError("portable Java fixture compilation failed")
    model_path = run_dir / "fixture.mph"
    png_path = run_dir / "fixture.png"
    tag = "portable_w02_" + uuid.uuid4().hex[:16]
    base = _java_base(paths, classes, prefs, tmp_dir)
    recipe_command = base + [
        "comsol_mcp.phase1_java.BoundPhase1Poc",
        args.host,
        str(args.port),
        tag,
        str(model_path),
        str(png_path),
    ]
    code, recipe_output = _run_logged(recipe_command, run_dir, "recipe", timeout_s=float(args.recipe_timeout_s))
    if code != 0 or "W02_RESULT" not in recipe_output:
        raise DriverError("portable Java fixture recipe failed")
    if not model_path.is_file() or model_path.stat().st_size == 0:
        raise DriverError("portable Java fixture did not produce a nonempty MPH")
    try:
        image_size = validate_png(png_path)
    except Exception as exc:
        raise DriverError(f"portable Java fixture PNG validation failed: {exc}") from exc
    build_match = re.search(r"(?m)^W02_BUILD\s+(.+?)\s*$", recipe_output)
    build_line = build_match.group(0) if build_match else ""
    fixture = {
        "status": "PASS",
        "recipe": "comsol_mcp.phase1_java.BoundPhase1Poc",
        "server": {"host": args.host, "port": args.port},
        "comsol_root": str(comsol_root),
        "jdk11": str(jdk_home),
        "java_version": java_version.stderr,
        "classpath_sha256": classpath_sha256,
        "jar_count": jar_count,
        "fixture_source_sha256": source_hashes,
        "worker_source_sha256": _source_hash(),
        "server_build": build_match.group(1) if build_match else "UNPARSED",
        "server_build_line": build_line or "W02_BUILD UNPARSED",
        "model_tag": tag,
        "model_path": str(model_path),
        "model_sha256": _sha256(model_path),
        "png_path": str(png_path),
        "png_sha256": _sha256(png_path),
        "png_size": image_size,
        "cleanup": "fixture model remains loaded under unique task-owned tag; no server lifecycle action was attempted",
    }
    _write(run_dir / "environment.json", {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "server": fixture["server"],
        "comsol_root": str(comsol_root),
        "jdk11": str(jdk_home),
        "classpath_sha256": classpath_sha256,
        "jar_count": jar_count,
        "fixture_source_sha256": source_hashes,
    })
    _write(run_dir / "request.json", {
        "phase": "prepare-fixture",
        "route": "official Java API fixture only; no persistent Worker",
        "server": fixture["server"],
        "model_path": str(model_path),
        "worker_source_sha256": _source_hash(),
    })
    _write(run_dir / "fixture.json", fixture)
    _write(run_dir / "result.json", fixture)
    _write(run_dir / "SHA256SUMS.json", {
        path.name: _sha256(path)
        for path in run_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.json"
    })
    return model_path, fixture


def _execution(ref: dict[str, Any] | None, revision: int | None, *, key: str, request: str, **timeouts: Any) -> dict[str, Any]:
    execution: dict[str, Any] = {"idempotency_key": key, "request_id": request, **timeouts}
    if ref is not None:
        execution.update({"model_ref": ref, "session_id": ref["session_id"], "expected_revision": revision})
    return execution


def _portable_workflow_configuration(model_path: Path, snapshot_dir: Path, *, snapshot_prefix: str) -> dict[str, Any]:
    """Build the fixed workflow contract used by the portable fixture recipe."""
    return {
        "current_main_model_path": str(model_path),
        "snapshot_dir": str(snapshot_dir),
        "snapshot_prefix": snapshot_prefix,
        "model_dimension": PORTABLE_RECIPE_MODEL_DIMENSION,
        "notes": "Portable bounded W02 fixture regression; no scientific acceptance claim.",
    }


def _extract_execution(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    execution = payload.get("execution", {}) if isinstance(payload, dict) else {}
    if not isinstance(execution, dict):
        return None, None
    ref, revision = execution.get("model_ref"), execution.get("revision")
    return (ref if isinstance(ref, dict) else None), (revision if isinstance(revision, int) and not isinstance(revision, bool) else None)


def _error_message(payload: dict[str, Any], fallback: str) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or fallback)
    if error:
        return str(error)
    return fallback


def _metric_ok(payload: dict[str, Any]) -> bool:
    rows = (payload.get("data") or {}).get("results", [])
    if not payload.get("success") or len(rows) != 1 or not rows[0].get("ok"):
        return False
    value = rows[0].get("value")
    while isinstance(value, list) and value:
        value = value[-1]
    try:
        return abs(float(value)) < 1e-8
    except (TypeError, ValueError):
        return False


def _worker_compilation_evidence(private_home: Path) -> dict[str, Any]:
    endpoint = private_home / "control-private" / "worker" / "worker_endpoint.json"
    if endpoint.is_symlink() or not endpoint.is_file():
        return {"present": False, "reason": "fresh Worker endpoint record not found"}
    record = _read_object(endpoint)
    expected_prefix = _source_hash()[:20]
    classes = endpoint.parent / "classes" / expected_prefix
    return {
        "present": True,
        "pid": record.get("pid"),
        "port": record.get("port"),
        "generation": record.get("generation"),
        "instance_id": record.get("instance_id"),
        "classpath_sha256": record.get("classpath_sha256"),
        "jar_count": record.get("jar_count"),
        "source_sha256": _source_hash(),
        "source_prefix_directory": str(classes),
        "compiled_marker": classes.joinpath(".compiled").is_file(),
    }


def _runtime_environment(args: argparse.Namespace, private_home: Path) -> dict[str, str]:
    """Build the exact runtime environment used by the MCP stdio host."""
    return {
        **os.environ,
        "COMSOL_ROOT": str(Path(args.comsol_root).resolve()),
        "COMSOL_JAVA_HOME": str(Path(args.jdk11).resolve()),
        "JAVA_HOME": str(Path(args.jdk11).resolve()),
        "COMSOL_PREFS_DIR": str(Path(args.prefs).resolve()),
        "COMSOL_SERVER_MCP_HOME": str(private_home.resolve()),
        "PYTHONPATH": str(ROOT),
    }


def _windows_path_key(value: str) -> str:
    """Normalize a Windows path for identity comparison without resolving it."""
    return ntpath.normcase(ntpath.normpath(str(value).strip().strip('"'))).casefold()


def _windows_birth_epoch_ms(value: Any) -> int | None:
    """Convert a CIM ISO-8601 UTC timestamp to the endpoint's millisecond form."""
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
    """Read one process through bounded, non-signalling PowerShell/CIM.

    ``None`` means CIM confirmed that the process no longer exists.  Query
    failures raise so cleanup cannot mistake an unavailable identity for a
    dead process.
    """
    if type(pid) is not int or pid <= 1:
        raise DriverError(f"invalid Windows process PID: {pid!r}")
    # The PID is validated as an integer before interpolation.  The helper
    # reads only Win32_Process fields and never opens a process for signalling.
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
        raise DriverError("bounded Windows CIM process identity query failed") from exc
    output = completed.stdout.strip()
    if completed.returncode == 3 and not output:
        return None
    if completed.returncode != 0:
        raise DriverError("bounded Windows CIM process identity query returned an error")
    if not output:
        raise DriverError("bounded Windows CIM process identity query returned no record")
    try:
        record = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DriverError("bounded Windows CIM process identity query returned invalid JSON") from exc
    if not isinstance(record, dict):
        raise DriverError("bounded Windows CIM process identity query returned a non-object")
    if record.get("pid") != pid:
        raise DriverError("bounded Windows CIM process identity returned a different PID")
    return record


def _selected_python_base_executable(python: Path, environment: Mapping[str, str]) -> str:
    """Ask the selected Python launcher for its real ``sys._base_executable``."""
    selected = str(python.expanduser().resolve())
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
        raise DriverError("selected --python could not report sys._base_executable") from exc
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) != 1:
        raise DriverError("selected --python returned an unusable sys._base_executable")
    base = lines[0]
    if not ntpath.isabs(base):
        raise DriverError("selected --python reported a non-absolute sys._base_executable")
    return ntpath.normpath(base)


def _windows_exact_control_command(command_line: str, control_home: Path) -> bool:
    """Require exactly one module invocation and one current ``--home``."""
    if not isinstance(command_line, str) or not command_line.strip():
        return False
    module_pattern = r"(?<!\S)-m\s+comsol_mcp\._control_daemon(?!\S)"
    if len(re.findall(module_pattern, command_line)) != 1:
        return False
    home = ntpath.normpath(str(control_home))
    variants = {home, f'"{home}"'}
    home_patterns = "|".join(re.escape(item) for item in variants)
    home_pattern = rf"(?<!\S)--home\s+(?:{home_patterns})(?!\S)"
    if len(re.findall(home_pattern, command_line)) != 1:
        return False
    return len(re.findall(r"(?<!\S)--home(?=\s|$)", command_line)) == 1


def _validate_windows_control_identity(
    process: Any,
    endpoint: dict[str, Any],
    control_home: Path,
    base_executable: str,
    *,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a launcher/daemon pair before attaching or terminating it."""
    launcher_pid = getattr(process, "pid", None)
    daemon_pid = endpoint.get("pid")
    if type(launcher_pid) is not int or launcher_pid <= 1:
        raise DriverError("Windows launcher PID is unavailable")
    if type(daemon_pid) is not int or daemon_pid <= 1:
        raise DriverError("Windows control endpoint daemon PID is unavailable")
    if process.poll() is not None:
        raise DriverError("Windows control launcher is no longer alive")
    launcher = _windows_process_snapshot(launcher_pid)
    if launcher is None:
        raise DriverError("Windows control launcher identity is unavailable")
    daemon = launcher if daemon_pid == launcher_pid else _windows_process_snapshot(daemon_pid)
    if daemon is None:
        raise DriverError("Windows control daemon identity is unavailable")
    if launcher.get("pid") != launcher_pid or daemon.get("pid") != daemon_pid:
        raise DriverError("Windows CIM process identity returned a different PID")
    launcher_birth = _windows_birth_epoch_ms(launcher.get("creation_utc"))
    daemon_birth = _windows_birth_epoch_ms(daemon.get("creation_utc"))
    endpoint_birth = endpoint.get("process_start_epoch_ms")
    if type(launcher_birth) is not int or type(daemon_birth) is not int:
        raise DriverError("Windows launcher/daemon creation identity is unavailable")
    if daemon_birth < launcher_birth:
        raise DriverError("Windows control daemon birth precedes its launcher birth")
    if type(endpoint_birth) is not int or endpoint_birth != daemon_birth:
        raise DriverError("Windows control endpoint daemon birth does not match CIM")
    if daemon_pid != launcher_pid and daemon.get("parent_pid") != launcher_pid:
        raise DriverError("Windows control endpoint daemon is not the Popen direct child")
    executable = daemon.get("executable_path")
    if not isinstance(executable, str) or _windows_path_key(executable) != _windows_path_key(base_executable):
        raise DriverError("Windows control daemon executable is not the selected Python base executable")
    command_line = daemon.get("command_line")
    if not _windows_exact_control_command(command_line, control_home):
        raise DriverError("Windows control daemon command does not identify the exact module and unique home")
    if expected is not None:
        if expected.get("launcher_pid") != launcher_pid or expected.get("daemon_pid") != daemon_pid:
            raise DriverError("Windows control launcher/daemon PID identity changed")
        if expected.get("launcher_process_start_epoch_ms") != launcher_birth:
            raise DriverError("Windows control launcher creation identity changed")
        if expected.get("daemon_process_start_epoch_ms") != daemon_birth:
            raise DriverError("Windows control daemon creation identity changed")
    return {
        "launcher_pid": launcher_pid,
        "launcher_process_start_epoch_ms": launcher_birth,
        "launcher_executable": launcher.get("executable_path"),
        "daemon_pid": daemon_pid,
        "daemon_process_start_epoch_ms": daemon_birth,
        "daemon_parent_pid": daemon.get("parent_pid"),
        "daemon_executable": executable,
        "selected_python_base_executable": base_executable,
        "windows_redirector": daemon_pid != launcher_pid,
        "popen_handle_held": True,
        "launcher_alive": True,
        "daemon_alive": True,
        "command_match": True,
        "daemon_command_sha256": hashlib.sha256(command_line.encode("utf-8")).hexdigest(),
    }


class _OwnedControlDaemon:
    """A control daemon created by this regression driver for one run."""

    def __init__(
        self,
        process,
        home: Path,
        endpoint: dict[str, Any],
        label: str,
        *,
        identity: dict[str, Any] | None = None,
        base_executable: str | None = None,
        platform_name: str | None = None,
    ):
        self.process = process
        self.home = home
        self.endpoint = endpoint
        self.label = label
        self.identity = dict(identity or {})
        self.base_executable = base_executable
        self.platform_name = platform_name or os.name

    @property
    def pid(self) -> int:
        # All control RPC/health comparisons use the daemon PID published in
        # control.json.  On Windows a venv redirector may leave Popen.pid as
        # the launcher PID, so callers must not compare against that value.
        return int(self.endpoint["pid"])

    @property
    def launcher_pid(self) -> int:
        return int(self.process.pid)

    @property
    def is_windows(self) -> bool:
        return self.platform_name == "nt"

    def health(self) -> dict[str, Any]:
        return _control_request(
            self.endpoint,
            {"operation": "session_health", "arguments": {}, "execution": {}},
            1.0,
        )

    def safe_identity(self) -> dict[str, Any]:
        if self.is_windows and self.identity:
            return {
                **self.identity,
                "pid": self.pid,
                "process_start_epoch_ms": self.identity.get("daemon_process_start_epoch_ms"),
                "port": self.endpoint.get("port"),
                "home": "REDACTED_CONTROL_HOME",
                "label": self.label,
            }
        return {
            "pid": self.pid,
            "launcher_pid": self.launcher_pid,
            "process_start_epoch_ms": self.endpoint.get("process_start_epoch_ms"),
            "port": self.endpoint.get("port"),
            "home": "REDACTED_CONTROL_HOME",
            "label": self.label,
        }

    def _revalidate_identity(self) -> dict[str, Any]:
        if self.is_windows:
            if not self.base_executable:
                raise DriverError("Windows control base executable identity is unavailable")
            return _validate_windows_control_identity(
                self.process,
                self.endpoint,
                self.home,
                self.base_executable,
                expected=self.identity or None,
            )
        identity = process_identity(self.pid)
        if not identity.get("alive"):
            raise DriverError("owned control PID is no longer alive")
        recorded_start = self.endpoint.get("process_start_epoch_ms")
        observed_start = identity.get("start_epoch_ms")
        if isinstance(recorded_start, int) and isinstance(observed_start, int) and recorded_start != observed_start:
            raise DriverError("owned control PID creation identity changed")
        return {
            "pid": self.pid,
            "launcher_pid": self.launcher_pid,
            "process_start_epoch_ms": observed_start if isinstance(observed_start, int) else recorded_start,
            "popen_handle_held": True,
            "launcher_alive": True,
            "daemon_alive": True,
            "command_match": "NOT_CHECKED_WINDOWS",
        }

    def _wait_for_processes_dead(self, timeout_s: float) -> dict[str, bool]:
        deadline = time.monotonic() + timeout_s
        while True:
            if self.is_windows:
                launcher = _windows_process_snapshot(self.launcher_pid)
                daemon = launcher if self.pid == self.launcher_pid else _windows_process_snapshot(self.pid)
                launcher_alive = launcher is not None
                daemon_alive = daemon is not None
            else:
                launcher_alive = process_identity(self.launcher_pid).get("alive", False)
                daemon_alive = process_identity(self.pid).get("alive", False)
            if not launcher_alive and not daemon_alive:
                return {"launcher_alive": False, "daemon_alive": False}
            if time.monotonic() >= deadline:
                return {"launcher_alive": launcher_alive, "daemon_alive": daemon_alive}
            time.sleep(0.1)

    def cleanup(self, worker_release: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        """Terminate only this daemon after authenticated worker quiescence.

        This intentionally has no force-kill fallback.  A failed identity or
        quiescence check leaves the daemon for manual reconciliation rather
        than risking an unrelated process or an active computation.
        """
        evidence: dict[str, Any] = {
            "action": "owned_control_daemon_cleanup",
            "ownership": "driver-created control daemon only",
            "status": "NOT_RUN",
            "server_lifecycle": "COMSOL Server was never started, stopped, or signalled",
            "worker_release_status": worker_release.get("status") if isinstance(worker_release, dict) else None,
        }
        try:
            if not isinstance(worker_release, dict) or worker_release.get("status") != "PASS":
                evidence.update({
                    "status": "BLOCKED",
                    "reason": "worker disconnect was not verified; control daemon was left running",
                })
                return evidence
            deadline = time.monotonic() + 5.0
            health: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                try:
                    candidate = self.health()
                except Exception:
                    candidate = None
                data = candidate.get("data") if isinstance(candidate, dict) else None
                worker = data.get("worker") if isinstance(data, dict) else None
                active_jobs = data.get("active_jobs") if isinstance(data, dict) else None
                if (
                    isinstance(candidate, dict)
                    and candidate.get("success")
                    and isinstance(active_jobs, list)
                    and not active_jobs
                    and isinstance(worker, dict)
                    and worker.get("connected") is False
                    and worker.get("server") in {"", None}
                    and worker.get("queued_or_running") == 0
                ):
                    health = candidate
                    break
                time.sleep(0.1)
            if health is None:
                evidence.update({
                    "status": "BLOCKED",
                    "reason": "control health did not freshly confirm disconnected idle Worker and active_jobs=[]",
                })
                return evidence

            identity = self._revalidate_identity()
            evidence["before"] = identity
            evidence["health"] = {
                "active_jobs": [],
                "worker_connected": False,
                "worker_queued_or_running": 0,
            }
            # This is the only process mutation: Popen owns the launcher
            # handle.  A Windows redirector child is never terminated by PID.
            self.process.terminate()
            try:
                returncode = self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                evidence.update({
                    "status": "BLOCKED",
                    "reason": "owned control daemon did not exit after a single terminate request; no force kill sent",
                })
                return evidence
            after = self._wait_for_processes_dead(timeout_s=3.0)
            if after["launcher_alive"] or after["daemon_alive"]:
                evidence.update({
                    "status": "BLOCKED",
                    "reason": "owned control launcher or daemon still appears alive after terminate; no force kill sent",
                    "after": after,
                })
                return evidence
            evidence.update({"status": "PASS", "returncode": returncode, "after": after})
            return evidence
        except Exception as exc:
            evidence.update({"status": "BLOCKED", "reason": f"{type(exc).__name__}: cleanup left process untouched"})
            return evidence
        finally:
            _write(run_dir / f"{self.label}.control-cleanup.json", evidence)


def _launch_owned_control(args: argparse.Namespace, private_home: Path, run_dir: Path, label: str) -> _OwnedControlDaemon:
    """Pre-start control outside the MCP stdio transport for this run."""
    control_home = _new_directory(private_home / "control-private", label="fresh control daemon home")
    environment = _runtime_environment(args, private_home)
    base_executable = None
    if os.name == "nt":
        # A venv's Scripts/python.exe can be a redirector.  Ask that exact
        # executable which base interpreter will own the daemon child.
        base_executable = _selected_python_base_executable(Path(args.python), environment)
    command = [
        str(Path(args.python).resolve()),
        "-m",
        "comsol_mcp._control_daemon",
        "--home",
        str(control_home),
    ]
    log_path = control_home / "control.log"
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_path.open("ab"),
        "stderr": subprocess.STDOUT,
        "env": environment,
        "cwd": str(ROOT),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        kwargs["stdout"].close()
        raise DriverError(f"could not pre-start the owned control daemon; inspect private log {log_path}") from exc
    finally:
        # Popen has its own descriptor; keeping the driver-side descriptor
        # open would make Windows log cleanup depend on this process.
        kwargs["stdout"].close()
    endpoint_path = control_home / "control.json"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DriverError(f"owned control daemon exited before readiness; inspect private log {log_path}")
        if endpoint_path.is_file() and not endpoint_path.is_symlink():
            try:
                endpoint = _read_object(endpoint_path)
                if os.name == "nt":
                    identity = _validate_windows_control_identity(
                        process,
                        endpoint,
                        control_home,
                        str(base_executable),
                    )
                else:
                    if endpoint.get("pid") != process.pid:
                        raise DriverError("owned control endpoint PID does not match the pre-started process")
                    identity = process_identity(process.pid)
                    recorded_start = endpoint.get("process_start_epoch_ms")
                    observed_start = identity.get("start_epoch_ms")
                    if not identity.get("alive"):
                        raise DriverError("pre-started control process is no longer alive")
                    if isinstance(recorded_start, int) and isinstance(observed_start, int) and recorded_start != observed_start:
                        raise DriverError("pre-started control process creation identity changed")
                health = _control_request(
                    endpoint,
                    {"operation": "session_health", "arguments": {}, "execution": {}},
                    0.5,
                )
                if health.get("success"):
                    owned = _OwnedControlDaemon(
                        process,
                        control_home,
                        endpoint,
                        label,
                        identity=identity,
                        base_executable=base_executable,
                    )
                    _write(run_dir / f"{label}.control-prestart.json", owned.safe_identity())
                    return owned
            except DriverError as exc:
                # Keep only non-secret launch/daemon identity in the private
                # evidence record.  The endpoint token never enters logs.
                _write(run_dir / f"{label}.control-prestart.json", {
                    "status": "FAIL",
                    "label": label,
                    "launcher_pid": process.pid,
                    "daemon_pid": endpoint.get("pid"),
                    "popen_handle_held": True,
                    "error": str(exc),
                })
                raise
            except Exception:
                pass
        time.sleep(0.05)
    raise DriverError(f"owned control daemon did not become ready; inspect private log {log_path}")


def _ps_start_epoch_ms(pid: int) -> int | None:
    """Read a POSIX child's start time without signalling it."""
    if os.name == "nt":
        return None
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = " ".join(completed.stdout.split())
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=datetime.now().astimezone().tzinfo).timestamp() * 1000)


def _owned_worker_identity(private_home: Path, expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify one driver-created Worker before authenticated housekeeping."""
    endpoint = private_home / "control-private" / "worker" / "worker_endpoint.json"
    record = _read_object(endpoint)
    pid = record.get("pid")
    instance_id = record.get("instance_id")
    expected_pid = expected.get("pid")
    expected_instance = expected.get("instance_id")
    if type(pid) is not int or pid <= 1 or pid != expected_pid:
        raise DriverError("private Worker PID no longer matches the driver-owned endpoint")
    if not isinstance(instance_id, str) or not instance_id or instance_id != expected_instance:
        raise DriverError("private Worker instance identity no longer matches the driver-owned endpoint")
    identity = process_identity(pid)
    if not identity.get("alive"):
        raise DriverError("driver-owned private Worker is no longer alive")
    recorded_start = record.get("process_start_epoch_ms")
    observed_start = identity.get("start_epoch_ms")
    if observed_start is None:
        observed_start = _ps_start_epoch_ms(pid)
    if type(recorded_start) is not int or type(observed_start) is not int:
        raise DriverError("private Worker process creation identity is unavailable")
    if abs(recorded_start - observed_start) > 2000:
        raise DriverError("private Worker PID was reused or its creation identity changed")
    endpoint_path = endpoint.resolve()
    private_root = private_home.resolve()
    command_matches: bool | str = "NOT_CHECKED_WINDOWS"
    if os.name != "nt":
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        command_matches = (
            "comsol_mcp.worker_java.PersistentComsolWorker" in command
            and str(endpoint_path) in command
            and str(private_root) in command
        )
    if os.name != "nt" and not command_matches:
        raise DriverError("private Worker command does not identify the current driver home")
    safe = {
        "pid": pid,
        "process_start_epoch_ms": recorded_start,
        "instance_id": instance_id,
        "generation": record.get("generation"),
        "port": record.get("port"),
        "private_home_match": True,
        "process_start_match": True,
        "command_match": command_matches,
    }
    return record, safe


def _verify_stdio_teardown(
    control: _OwnedControlDaemon,
    private_home: Path,
    expected_worker: dict[str, Any],
) -> dict[str, Any]:
    """Read back the same control/Worker after the MCP stdio host closes."""
    control_health = control.health()
    _, worker_identity = _owned_worker_identity(private_home, expected_worker)
    return {
        "control_alive": bool(
            control_health.get("success")
            and (control_health.get("data") or {}).get("control_pid") == control.pid
        ),
        "worker_alive": bool(worker_identity.get("command_match") is not False),
        "evidence": {
            "control": control.safe_identity(),
            "control_health": {
                "success": control_health.get("success"),
                "control_pid": (control_health.get("data") or {}).get("control_pid"),
            },
            "worker": worker_identity,
        },
    }


def _disconnect_owned_worker(
    args: argparse.Namespace,
    private_home: Path,
    expected: dict[str, Any],
    run_dir: Path,
    label: str,
    control_queue_idle: bool | None,
) -> dict[str, Any]:
    """Release only a Worker started by this driver, through its auth protocol."""
    evidence: dict[str, Any] = {
        "action": "authenticated_private_worker_disconnect",
        "ownership": "driver-created private Worker only",
        "server_lifecycle": "COMSOL Server was never started, stopped, or signalled",
        "control_queue_idle": control_queue_idle,
        "status": "NOT_RUN",
    }
    attached: PersistentJavaWorker | None = None
    try:
        if control_queue_idle is not True:
            evidence.update({
                "status": "BLOCKED",
                "reason": "control active_jobs=[] was not freshly confirmed; no disconnect sent",
            })
            return evidence
        record, identity = _owned_worker_identity(private_home, expected)
        evidence["before"] = identity
        paths = JavaWorkerPaths(
            _existing_external_dir(Path(args.comsol_root), label="COMSOL root"),
            _existing_external_dir(Path(args.jdk11), label="JDK 11 home"),
            _existing_private_dir(Path(args.prefs), label="COMSOL preferences"),
            project_root=ROOT,
        )
        attached = PersistentJavaWorker(
            paths,
            state_dir=private_home / "control-private" / "worker",
        )
        health_before = attached.start()
        if health_before.get("instance_id") != record.get("instance_id"):
            raise DriverError("authenticated Worker instance differs from the verified endpoint")
        if int(health_before.get("queued_or_running", -1)) != 0:
            evidence.update({
                "status": "BLOCKED",
                "reason": "driver-owned Worker still has queued or running requests; no disconnect sent",
                "health_before": {key: health_before.get(key) for key in ("connected", "server", "generation", "instance_id", "queued_or_running")},
            })
            return evidence
        request_id = f"portable-release-{label}-{uuid.uuid4().hex}"
        disconnected = attached.client().disconnect(request_id=request_id, rpc_timeout_s=60)
        health_after = attached.health(timeout_s=5)
        disconnected_idle = (
            not health_after.get("connected")
            and not health_after.get("server")
            and health_after.get("queued_or_running") == 0
        )
        evidence.update({
            "status": "PASS" if disconnected_idle else "FAIL",
            "request_id": request_id,
            "health_before": {key: health_before.get(key) for key in ("connected", "server", "generation", "instance_id", "queued_or_running")},
            "disconnect_reply": {key: disconnected.get(key) for key in ("ok", "status", "generation") if key in disconnected},
            "health_after": {key: health_after.get(key) for key in ("connected", "server", "generation", "instance_id", "queued_or_running")},
        })
    except Exception as exc:
        evidence.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if attached is not None:
            # start() attached an existing process in this path; close() only
            # clears this controller's handles and never terminates that child.
            attached.close()
        _write(run_dir / f"{label}.worker-release.json", evidence)
    return evidence


class _ProductionHost:
    def __init__(self, args: argparse.Namespace, run_dir: Path, transcript: list[dict[str, Any]], label: str):
        self.args = args
        self.run_dir = run_dir
        self.transcript = transcript
        self.label = label
        self.log = None
        self.transport = None
        self.session = None

    async def __aenter__(self) -> "_ProductionHost":
        env = _runtime_environment(self.args, Path(self.args.private_home))
        params = StdioServerParameters(
            command=str(Path(self.args.python).resolve()),
            args=["-m", "comsol_mcp.mcp_server"],
            env=env,
            cwd=str(ROOT),
        )
        self.log = (self.run_dir / f"{self.label}.engine.log").open("w", encoding="utf-8")
        self.transport = stdio_client(params, errlog=self.log)
        reader, writer = await self.transport.__aenter__()
        self.session = ClientSession(reader, writer, read_timeout_seconds=timedelta(minutes=10))
        await self.session.__aenter__()
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.session is not None:
            await self.session.__aexit__(*exc)
        if self.transport is not None:
            await self.transport.__aexit__(*exc)
        if self.log is not None:
            self.log.close()

    async def call(self, operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session is None:
            raise DriverError("production MCP host is not initialized")
        started = time.monotonic()
        reply = await self.session.call_tool(operation, arguments or {})
        elapsed = time.monotonic() - started
        dumped = _safe(reply)
        try:
            payload = json.loads(reply.content[0].text)
        except Exception:
            payload = {"success": False, "error": {"code": "NON_JSON_RESPONSE"}, "raw": dumped}
        payload = _safe(payload)
        self.transcript.append({
            "host": self.label,
            "operation": operation,
            "arguments": arguments or {},
            "elapsed_s": elapsed,
            "outer_isError": bool(reply.isError),
            "payload": payload,
        })
        return {**payload, "_outer_isError": bool(reply.isError), "_elapsed_s": elapsed}


async def _production_chain(args: argparse.Namespace, fixture: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    run_dir = _new_artifact_directory(Path(args.run_dir), label="production run directory")
    private_home = _new_directory(Path(args.private_home), label="fresh private control home")
    model_path = _artifact_path(Path(str(fixture.get("model_path", ""))), label="fixture model")
    if not model_path.is_file():
        raise DriverError(f"fixture model does not exist: {model_path}")
    transcript: list[dict[str, Any]] = []
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {"phase": "production-chain", "status": "NOT_RUN"}
    worker: dict[str, Any] | None = None
    control: _OwnedControlDaemon | None = None
    release_requested = False
    control_queue_idle: bool | None = None
    prefix = _stamp() + "-"
    try:
        _write(run_dir / "request.json", {
            "phase": "production-chain",
            "route": "fresh production MCP stdio -> private control -> private Java Worker",
            "server": {"host": args.host, "port": args.port},
            "fixture_model_sha256": _sha256(model_path),
            "expected_worker_source_sha256": _source_hash(),
            "server_lifecycle": "attach-only; never start/stop/replace COMSOL",
        })
        _write(run_dir / "environment.json", {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "comsol_root": str(Path(args.comsol_root).resolve()),
            "jdk11": str(Path(args.jdk11).resolve()),
            "server": {"host": args.host, "port": args.port},
            "fixture_model_sha256": _sha256(model_path),
            "worker_source_sha256": _source_hash(),
            "private_home": "REDACTED_PRIVATE_HOME",
        })
        control = _launch_owned_control(args, private_home, run_dir, "production")
        _write(run_dir / "control-prestart.json", control.safe_identity())
        async with _ProductionHost(args, run_dir, transcript, "production") as host:
            configured = await host.call(
                "configure_single_main_workflow",
                _portable_workflow_configuration(model_path, run_dir / "snapshots", snapshot_prefix="portable"),
            )
            assertions["workflow_configured"] = bool(configured.get("success"))
            connected = await host.call("server_connect", {
                "host": args.host,
                "port": args.port,
                "execution": _execution(None, None, key=prefix + "connect", request=prefix + "connect"),
            })
            worker = _worker_compilation_evidence(private_home)
            release_requested = bool(worker.get("present"))
            if not connected.get("success"):
                control_health = await host.call("session_health")
                active_jobs = (control_health.get("data") or {}).get("active_jobs")
                control_queue_idle = isinstance(active_jobs, list) and not active_jobs
                code = ((connected.get("error") or {}).get("code") if isinstance(connected.get("error"), dict) else "")
                result.update({
                    "status": "BLOCKED" if code == "ENGINE_BUSY" else "FAIL",
                    "reason": _error_message(connected, "server_connect failed"),
                    "error_code": code,
                })
                return run_dir, result
            assertions["fresh_worker_source_loaded"] = bool(worker.get("compiled_marker") and worker.get("source_sha256") == _source_hash())
            loaded = await host.call("model_load", {
                "path": str(model_path),
                "execution": _execution(None, None, key=prefix + "load", request=prefix + "load"),
            })
            ref, revision = _extract_execution(loaded)
            assertions["connect_load"] = bool(connected.get("success") and loaded.get("success") and ref and isinstance(revision, int))
            if not assertions["connect_load"]:
                control_health = await host.call("session_health")
                active_jobs = (control_health.get("data") or {}).get("active_jobs")
                control_queue_idle = isinstance(active_jobs, list) and not active_jobs
                result.update({"status": "FAIL", "reason": "fresh Worker did not bind the fixture model"})
                return run_dir, result
            tree_before = await host.call("model_tree", {"execution": _execution(ref, revision, key=prefix + "tree-before", request=prefix + "tree-before")})
            guard = await host.call("set_parameters", {
                "parameters_json": '[{"name":"portable_guard","expression":"1"}]',
                "execution": _execution(ref, revision, key=prefix + "parameter", request=prefix + "parameter"),
            })
            guard_ref, guard_revision = _extract_execution(guard)
            if guard_ref is not None:
                ref, revision = guard_ref, guard_revision
            assertions["parameter_write"] = bool(guard.get("success"))
            parameter_readback = await host.call("get_parameters", {
                "execution": _execution(ref, revision, key=prefix + "parameter-read", request=prefix + "parameter-read"),
            })
            parameter_rows = ((parameter_readback.get("data") or {}).get("parameters") or [])
            assertions["parameter_set_readback"] = bool(
                parameter_readback.get("success")
                and any(row.get("name") == "portable_guard" and row.get("expression") == "1" for row in parameter_rows if isinstance(row, dict))
            )
            variables = await host.call("manage_variables", {
                "action": "set",
                "component": "",
                "tag": "portable_vars",
                "name": "portable_q",
                "expression": "5",
                "execution": _execution(ref, revision, key=prefix + "variable", request=prefix + "variable"),
            })
            var_ref, var_revision = _extract_execution(variables)
            if var_ref is not None:
                ref, revision = var_ref, var_revision
            variable_rows = ((variables.get("data") or {}).get("variables") or [])
            assertions["variable_set_readback"] = bool(variables.get("success") and any(row.get("name") == "portable_q" and row.get("expression") == "5" for row in variable_rows if isinstance(row, dict)))
            solved = await host.call("run_study", {
                "study_tag": "std1",
                "execution": _execution(ref, revision, key=prefix + "solve", request=prefix + "solve", rpc_timeout_s=30),
            })
            solve_ref, solve_revision = _extract_execution(solved)
            if solve_ref is not None:
                ref, revision = solve_ref, solve_revision
            assertions["production_solve"] = bool(solved.get("success"))
            tree_before_eval = await host.call("model_tree", {"execution": _execution(ref, revision, key=prefix + "tree-before-eval", request=prefix + "tree-before-eval")})
            metric = await host.call("get_core_metrics", {
                "metrics_json": '[{"name":"portable_max_abs_error","expression":"abs(u-2)","aggregate":"max","domains":[1]}]',
                "execution": _execution(ref, revision, key=prefix + "metric", request=prefix + "metric"),
            })
            metric_ref, metric_revision = _extract_execution(metric)
            if metric_ref is not None:
                ref, revision = metric_ref, metric_revision
            assertions["production_metric"] = _metric_ok(metric)
            tree_after_eval = await host.call("model_tree", {"execution": _execution(ref, revision, key=prefix + "tree-after-eval", request=prefix + "tree-after-eval")})
            assertions["ephemeral_tree_preserved"] = bool(tree_before_eval.get("success") and tree_after_eval.get("success") and tree_before_eval.get("data") == tree_after_eval.get("data"))
            saved_path = run_dir / "after.mph"
            saved = await host.call("save_model", {
                "path": str(saved_path),
                "execution": _execution(ref, revision, key=prefix + "save", request=prefix + "save"),
            })
            assertions["production_save"] = bool(saved.get("success") and saved_path.is_file() and saved_path.stat().st_size > 0)
            result["saved_path"] = str(saved_path)
            result["saved_sha256"] = _sha256(saved_path) if saved_path.is_file() else None
            control_health = await host.call("session_health")
            active_jobs = (control_health.get("data") or {}).get("active_jobs")
            control_queue_idle = isinstance(active_jobs, list) and not active_jobs
            assertions["control_queue_idle_before_release"] = control_queue_idle
        if control is not None and worker is not None:
            try:
                teardown = _verify_stdio_teardown(control, private_home, worker)
                assertions["stdio_teardown_control_alive"] = teardown["control_alive"]
                assertions["stdio_teardown_worker_alive"] = teardown["worker_alive"]
                _write(run_dir / "stdio-teardown-identity.json", teardown["evidence"])
            except Exception as exc:
                assertions["stdio_teardown_control_alive"] = False
                assertions["stdio_teardown_worker_alive"] = False
                _write(run_dir / "stdio-teardown-identity.json", {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        result.update({"status": "PASS" if all(assertions.values()) else "FAIL", "worker": worker})
        _write(run_dir / "reopen_request.json", {
            "saved_path": str(run_dir / "after.mph"),
            "saved_sha256": result.get("saved_sha256"),
            "worker_source_sha256": _source_hash(),
            "next_phase": "release this task-owned private Worker, then invoke reopen with another new private_home",
        })
    except Exception as exc:
        result.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
    finally:
        if release_requested and worker is not None:
            release = _disconnect_owned_worker(args, private_home, worker, run_dir, "production", control_queue_idle)
            result["worker_release"] = release
            assertions["driver_worker_released"] = release.get("status") == "PASS"
            if release.get("status") != "PASS" and result.get("status") == "PASS":
                result.update({"status": "FAIL", "reason": "driver-owned Worker was not released after terminal production operations"})
        if control is not None:
            cleanup = control.cleanup(result.get("worker_release", {}), run_dir)
            result["control_cleanup"] = cleanup
            assertions["driver_control_released"] = cleanup.get("status") == "PASS"
            if cleanup.get("status") != "PASS" and result.get("status") == "PASS":
                result.update({"status": "FAIL", "reason": "owned control daemon cleanup was not verified"})
        _write(run_dir / "transcript.json", transcript)
        _write(run_dir / "assertions.json", assertions)
        _write(run_dir / "result.json", {**result, "assertions": assertions})
        _write(run_dir / "SHA256SUMS.json", {path.name: _sha256(path) for path in run_dir.iterdir() if path.is_file()})
    return run_dir, result


async def _reopen(args: argparse.Namespace, saved_model: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = _new_artifact_directory(Path(args.run_dir), label="reopen run directory")
    private_home = _new_directory(Path(args.private_home), label="fresh reopen private control home")
    saved_model = _artifact_path(saved_model, label="saved model")
    if not saved_model.is_file() or saved_model.stat().st_size == 0:
        raise DriverError(f"saved MPH is missing or empty: {saved_model}")
    transcript: list[dict[str, Any]] = []
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {"phase": "fresh-worker-reopen", "status": "NOT_RUN", "saved_sha256": _sha256(saved_model)}
    worker: dict[str, Any] | None = None
    control: _OwnedControlDaemon | None = None
    release_requested = False
    control_queue_idle: bool | None = None
    prefix = _stamp() + "-"
    try:
        _write(run_dir / "request.json", {
            "phase": "fresh-worker-reopen",
            "route": "new production MCP stdio -> new private control -> new Java Worker",
            "server": {"host": args.host, "port": args.port},
            "saved_path": str(saved_model),
            "saved_sha256": _sha256(saved_model),
            "worker_source_sha256": _source_hash(),
            "server_lifecycle": "attach-only; never start/stop/replace COMSOL",
        })
        control = _launch_owned_control(args, private_home, run_dir, "reopen")
        _write(run_dir / "control-prestart.json", control.safe_identity())
        async with _ProductionHost(args, run_dir, transcript, "reopen") as host:
            configured = await host.call(
                "configure_single_main_workflow",
                _portable_workflow_configuration(saved_model, run_dir / "snapshots", snapshot_prefix="portable-reopen"),
            )
            assertions["workflow_configured"] = bool(configured.get("success"))
            if not assertions["workflow_configured"]:
                result.update({
                    "status": "FAIL",
                    "reason": "fresh reopen workflow configuration failed before engine access",
                    "error_code": ((configured.get("error") or {}).get("code") if isinstance(configured.get("error"), dict) else ""),
                })
                return run_dir, result
            connected = await host.call("server_connect", {
                "host": args.host,
                "port": args.port,
                "execution": _execution(None, None, key=prefix + "connect", request=prefix + "connect"),
            })
            worker = _worker_compilation_evidence(private_home)
            release_requested = bool(worker.get("present"))
            if not connected.get("success"):
                control_health = await host.call("session_health")
                active_jobs = (control_health.get("data") or {}).get("active_jobs")
                control_queue_idle = isinstance(active_jobs, list) and not active_jobs
                code = ((connected.get("error") or {}).get("code") if isinstance(connected.get("error"), dict) else "")
                result.update({"status": "BLOCKED" if code == "ENGINE_BUSY" else "FAIL", "reason": _error_message(connected, "server_connect failed"), "error_code": code})
                return run_dir, result
            assertions["new_worker_source_loaded"] = bool(worker.get("compiled_marker") and worker.get("source_sha256") == _source_hash())
            loaded = await host.call("model_load", {
                "path": str(saved_model),
                "execution": _execution(None, None, key=prefix + "load", request=prefix + "load"),
            })
            ref, revision = _extract_execution(loaded)
            assertions["saved_model_loaded"] = bool(loaded.get("success") and ref and isinstance(revision, int))
            if assertions["saved_model_loaded"]:
                metric = await host.call("get_core_metrics", {
                    "metrics_json": '[{"name":"reopen_max_abs_error","expression":"abs(u-2)","aggregate":"max","domains":[1]}]',
                    "execution": _execution(ref, revision, key=prefix + "metric", request=prefix + "metric"),
                })
                metric_ref, metric_revision = _extract_execution(metric)
                if metric_ref is not None:
                    ref, revision = metric_ref, metric_revision
                assertions["reopen_metric"] = _metric_ok(metric)
                parameter_readback = await host.call("get_parameters", {
                    "execution": _execution(ref, revision, key=prefix + "parameter-read", request=prefix + "parameter-read"),
                })
                parameter_rows = ((parameter_readback.get("data") or {}).get("parameters") or [])
                assertions["reopen_parameter"] = bool(
                    parameter_readback.get("success")
                    and any(row.get("name") == "portable_guard" and row.get("expression") == "1" for row in parameter_rows if isinstance(row, dict))
                )
                variable = await host.call("evaluate_expressions", {
                    "expressions_json": '[{"name":"reopen_variable","expression":"portable_q"}]',
                    "execution": _execution(ref, revision, key=prefix + "variable", request=prefix + "variable"),
                })
                rows = (variable.get("data") or {}).get("results", [])
                assertions["reopen_variable"] = bool(variable.get("success") and len(rows) == 1 and rows[0].get("ok") and float(rows[0].get("last_value")) == 5.0)
            control_health = await host.call("session_health")
            active_jobs = (control_health.get("data") or {}).get("active_jobs")
            control_queue_idle = isinstance(active_jobs, list) and not active_jobs
            assertions["control_queue_idle_before_release"] = control_queue_idle
        if control is not None and worker is not None:
            try:
                teardown = _verify_stdio_teardown(control, private_home, worker)
                assertions["stdio_teardown_control_alive"] = teardown["control_alive"]
                assertions["stdio_teardown_worker_alive"] = teardown["worker_alive"]
                _write(run_dir / "stdio-teardown-identity.json", teardown["evidence"])
            except Exception as exc:
                assertions["stdio_teardown_control_alive"] = False
                assertions["stdio_teardown_worker_alive"] = False
                _write(run_dir / "stdio-teardown-identity.json", {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        result.update({"status": "PASS" if all(assertions.values()) else "FAIL", "worker": worker})
    except Exception as exc:
        result.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
    finally:
        if release_requested and worker is not None:
            release = _disconnect_owned_worker(args, private_home, worker, run_dir, "reopen", control_queue_idle)
            result["worker_release"] = release
            assertions["driver_worker_released"] = release.get("status") == "PASS"
            if release.get("status") != "PASS" and result.get("status") == "PASS":
                result.update({"status": "FAIL", "reason": "driver-owned reopen Worker was not released after terminal operations"})
        if control is not None:
            cleanup = control.cleanup(result.get("worker_release", {}), run_dir)
            result["control_cleanup"] = cleanup
            assertions["driver_control_released"] = cleanup.get("status") == "PASS"
            if cleanup.get("status") != "PASS" and result.get("status") == "PASS":
                result.update({"status": "FAIL", "reason": "owned reopen control daemon cleanup was not verified"})
        _write(run_dir / "transcript.json", transcript)
        _write(run_dir / "assertions.json", assertions)
        _write(run_dir / "result.json", {**result, "assertions": assertions})
        _write(run_dir / "SHA256SUMS.json", {path.name: _sha256(path) for path in run_dir.iterdir() if path.is_file()})
    return run_dir, result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default="127.0.0.1")
    common.add_argument("--port", type=int, required=True)
    common.add_argument("--comsol-root", default=COMSOL_ROOT_DEFAULT)
    common.add_argument("--jdk11", default=JDK11_DEFAULT)
    common.add_argument("--prefs", required=True)
    common.add_argument("--python", default=sys.executable)
    prepare = sub.add_parser("prepare-fixture", parents=[common])
    prepare.add_argument("--run-dir", required=True)
    prepare.add_argument("--compile-timeout-s", type=float, default=180.0)
    prepare.add_argument("--recipe-timeout-s", type=float, default=300.0)
    production = sub.add_parser("production-chain", parents=[common])
    production.add_argument("--fixture-json", type=Path, required=True)
    production.add_argument("--private-home", type=Path, required=True)
    production.add_argument("--run-dir", required=True)
    reopen = sub.add_parser("reopen", parents=[common])
    reopen.add_argument("--saved-mph", type=Path, required=True)
    reopen.add_argument("--private-home", type=Path, required=True)
    reopen.add_argument("--run-dir", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.command == "prepare-fixture":
        try:
            _prepare_fixture(args)
            print(json.dumps({"status": "PASS", "run_dir": str(Path(args.run_dir).resolve())}, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            return 1
    if args.command == "production-chain":
        try:
            fixture = _read_object(args.fixture_json)
            run_dir, result = asyncio.run(_production_chain(args, fixture))
            print(json.dumps({"status": result.get("status"), "run_dir": str(run_dir)}, ensure_ascii=False))
            return 0 if result.get("status") == "PASS" else 1
        except Exception as exc:
            print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            return 1
    try:
        run_dir, result = asyncio.run(_reopen(args, args.saved_mph))
        print(json.dumps({"status": result.get("status"), "run_dir": str(run_dir)}, ensure_ascii=False))
        return 0 if result.get("status") == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
