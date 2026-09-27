#!/usr/bin/env python3
"""Run the frozen COMSOL 6.4 restart smoke on a private loopback server.

The installed COMSOL tree is read-only.  A small shadow root points at its
immutable payload and carries a private WebBridge server.xml whose Connector
is bound to 127.0.0.1.  The script never adopts/stops an existing server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
FIXTURE_SOURCE = REPO / "tools/java/NativeResumePDEFixture.java"
MAX_WALL_S = 180.0
MAX_REAL_SOLVE_CALLS = 2
MARKER = "0.3141592653589793"
POINT_AXIS = (0.25, 0.50, 0.75)
REAL_TOL = 1e-8
IMAG_TOL = 1e-10
REOPEN_TOL = 1e-12
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return repr(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
    os.replace(temporary, path)


def append_event(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at_utc": utc_now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def update_resume_state(run_state: dict[str, Any], *, active_jobs: list[dict[str, Any]] | None = None,
                        status: str | None = None) -> None:
    state_path = REPO / "docs/full_project_execution/state/RESUME.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["active_jobs"] = active_jobs or []
    state["native_resume_smoke"] = dict(run_state)
    attempts = state.get("native_resume_smoke_attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempt = {key: run_state.get(key) for key in (
        "run_id", "status", "evidence_dir", "work_dir", "native_solve_count",
        "prior_native_solve_calls", "total_native_solve_calls", "max_real_solve_calls",
        "max_total_native_solve_calls", "wall_clock_budget_s", "elapsed_s", "source_job_id",
        "child_job_id", "solved_model_path", "solved_model_sha256", "last_error",
    ) if key in run_state}
    run_id = run_state.get("run_id")
    if isinstance(run_id, str):
        for index, previous in enumerate(attempts):
            if isinstance(previous, dict) and previous.get("run_id") == run_id:
                attempts[index] = {**previous, **attempt}
                break
        else:
            attempts.append(attempt)
    state["native_resume_smoke_attempts"] = attempts
    if status:
        state["native_resume_smoke"]["status"] = status
    write_json(state_path, state)


def require_success(response: dict[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise RuntimeError(f"{label} failed: {json.dumps(response, ensure_ascii=False, default=json_default)[:5000]}")
    return response


def _flatten_numbers(value: Any) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        result: list[float] = []
        for item in value:
            result.extend(_flatten_numbers(item))
        return result
    return []


def _frozen_point_records(points: list[list[float]]) -> list[dict[str, float]]:
    records = []
    for index, point in enumerate(points):
        if len(point) != 2:
            raise AssertionError(f"frozen 2-D sample point {index} must contain x,y; got {point!r}")
        records.append({"x": float(point[0]), "y": float(point[1])})
    return records


def _parse_lsof_listener(stdout: str) -> tuple[int | None, str]:
    """Read lsof's whitespace table without mistaking `(LISTEN)` for NAME."""
    rows = [line for line in stdout.splitlines()
            if line.strip() and not line.lstrip().startswith("COMMAND")]
    if len(rows) != 1:
        return None, ""
    fields = rows[0].split()
    pid = int(fields[1]) if len(fields) >= 2 and fields[1].isdigit() else None
    try:
        endpoint = fields[fields.index("TCP") + 1]
    except (ValueError, IndexError):
        endpoint = ""
    return pid, endpoint


def _engine_build_identity(raw_version: Any) -> dict[str, Any]:
    """Normalize the COMSOL API's localized version string without losing it."""
    raw = str(raw_version)
    full_target = re.search(r"(?<![0-9.])6\.4\.0\.293(?![0-9])", raw) is not None
    family = re.search(r"(?<![0-9.])6\.4(?:\.0)?(?![0-9.])", raw) is not None
    build_marker = re.search(
        r"(?:development\s+(?:version|build)|dev(?:elopment)?\s+(?:version|build)|build|开发版本)\s*[:：]?\s*293\b",
        raw,
        flags=re.IGNORECASE,
    ) is not None
    matches = full_target or (family and build_marker)
    return {
        "raw": raw,
        "frozen_target": "COMSOL Multiphysics 6.4.0.293",
        "normalized_identity": "6.4.0.293" if matches else None,
        "version_family_match": full_target or family,
        "build_293_match": full_target or build_marker,
        "matches_frozen_target": matches,
        "normalization_basis": "exact 6.4.0.293 token" if full_target else (
            "localized/English 6.4 family plus explicit build 293 marker" if matches else None
        ),
    }


def _is_native_study_run_submission(event: Any) -> bool:
    """Recognize a persisted Worker submission, not Python adapter entry."""
    if not isinstance(event, dict) or event.get("phase") != "submitted":
        return False
    worker_request = event.get("metadata")
    return (
        event.get("kind") == "call"
        and isinstance(worker_request, dict)
        and worker_request.get("type") == "call"
        and worker_request.get("method") == "run"
    )


def _study_node_path(tag: str) -> dict[str, list[dict[str, str]]]:
    return {"segments": [{"collection": "study", "tag": tag}]}


def _remaining_native_solve_budget(prior_solve_calls: int) -> int:
    if isinstance(prior_solve_calls, bool) or not isinstance(prior_solve_calls, int):
        raise ValueError("prior native solve count must be an integer")
    if prior_solve_calls < 0 or prior_solve_calls > MAX_REAL_SOLVE_CALLS:
        raise ValueError(f"prior native solve count must be within 0..{MAX_REAL_SOLVE_CALLS}")
    return MAX_REAL_SOLVE_CALLS - prior_solve_calls


class NativeLoopbackServer:
    """Owns only the task-private COMSOL server process and Java Worker."""

    def __init__(self, work: Path, evidence: Path, *, event_log: Path) -> None:
        from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker

        self.work = work
        self.evidence = evidence
        self.event_log = event_log
        self.shadow_root = work / "comsol-shadow"
        self.runtime = work / "runtime"
        self.prefs = self.runtime / "prefs"
        self.tmp = self.runtime / "tmp"
        self.recovery = self.runtime / "recovery"
        self.project = work / "project"
        self.worker_state = work / "worker-state"
        self.paths_type = JavaWorkerPaths
        self.worker_type = PersistentJavaWorker
        self.proc: subprocess.Popen[str] | None = None
        self.worker: Any = None
        self.port: int | None = None
        self.process_identity: dict[str, Any] | None = None
        self.receipt_path = work / "isolation_receipt.json"
        self.server_log = work / "mphserver.log"

    def prepare_shadow(self) -> dict[str, Any]:
        if sys.platform != "darwin":
            raise RuntimeError(f"native resume smoke is frozen for macOS 6.4, got {sys.platform}")
        if not INSTALL_ROOT.is_dir() or not JAVA11.is_dir():
            raise RuntimeError("the pre-inventoried COMSOL 6.4 tree or external Java 11 tree is unavailable")
        for directory in (self.runtime, self.prefs, self.tmp, self.recovery, self.project, self.worker_state):
            directory.mkdir(parents=True, exist_ok=False)

        # Point the shadow root at immutable installation payload; copy launcher
        # files and the COMSOL server configuration into this task-owned tree.
        self.shadow_root.mkdir()
        for entry in INSTALL_ROOT.iterdir():
            if entry.name == "bin":
                continue
            target = self.shadow_root / entry.name
            if entry.is_dir():
                target.symlink_to(entry, target_is_directory=True)
            else:
                shutil.copy2(entry, target)
        private_bin = self.shadow_root / "bin"
        private_bin.mkdir()
        for entry in (INSTALL_ROOT / "bin").iterdir():
            target = private_bin / entry.name
            if entry.name == "servers":
                shutil.copytree(entry, target, symlinks=True)
            elif entry.is_dir():
                target.symlink_to(entry, target_is_directory=True)
            else:
                shutil.copy2(entry, target)

        installed_xml = INSTALL_ROOT / "bin/servers/webbridge/conf/server.xml"
        private_xml = self.shadow_root / "bin/servers/webbridge/conf/server.xml"
        installed_before = sha256(installed_xml)
        tree = ET.parse(private_xml)
        connectors = tree.findall(".//Connector")
        if len(connectors) != 1:
            raise RuntimeError(f"expected exactly one COMSOL WebBridge Connector, found {len(connectors)}")
        connectors[0].set("address", "127.0.0.1")
        tree.write(private_xml, encoding="UTF-8", xml_declaration=True)
        if sha256(installed_xml) != installed_before:
            raise RuntimeError("installed COMSOL server.xml changed while staging the private copy")
        private_reparse = ET.parse(private_xml)
        private_connectors = private_reparse.findall(".//Connector")
        if len(private_connectors) != 1 or private_connectors[0].get("address") != "127.0.0.1":
            raise RuntimeError("private COMSOL WebBridge Connector did not retain the loopback-only bind")

        # Verify that the shadow launcher reads its own paths and supports the
        # frozen documented options before any persistent server is started.
        help_result = subprocess.run(
            [str(private_bin / "comsol"), "mphserver", "-help"],
            capture_output=True, text=True, timeout=25, check=False,
        )
        if (help_result.returncode != 0 or "Multiphysics Server options:" not in help_result.stdout
                or "-portfile <path>" not in help_result.stdout):
            raise RuntimeError("private launcher help did not resolve the COMSOL server command: " + help_result.stderr[-1500:])

        metadata = {
            "status": "PRIVATE_SHADOW_PREPARED",
            "installed_root": str(INSTALL_ROOT),
            "shadow_root": str(self.shadow_root),
            "launcher": str(private_bin / "comsol"),
            "server_config_source": str(installed_xml),
            "server_config_shadow": str(private_xml),
            "installed_server_config_sha256_before": installed_before,
            "installed_server_config_sha256_after": sha256(installed_xml),
            "private_server_config_sha256": sha256(private_xml),
            "private_connector_address": "127.0.0.1",
            "private_launcher_help_exit_code": help_result.returncode,
            "private_launcher_help_sha256": hashlib.sha256(help_result.stdout.encode()).hexdigest(),
            "server_options_verified": ["-port 0", "-portfile", "-prefsdir", "-tmpdir", "-recoverydir", "-login auto", "-silent", "-multi on"],
            "install_tree_mutated": False,
        }
        write_json(self.evidence / "private_shadow_receipt.json", metadata)
        append_event(self.event_log, "private_shadow_prepared", **metadata)
        return metadata

    def start_and_verify_listener(self) -> dict[str, Any]:
        from comsol_mcp._g2_isolation import _process_snapshot

        port_file = self.runtime / "server.port"
        command = [
            str(self.shadow_root / "bin/comsol"), "mphserver",
            "-port", "0", "-portfile", str(port_file),
            "-prefsdir", str(self.prefs), "-tmpdir", str(self.tmp),
            "-recoverydir", str(self.recovery), "-login", "auto", "-silent", "-multi", "on",
        ]
        self._server_log_handle = self.server_log.open("ab", buffering=0)
        self.proc = subprocess.Popen(command, cwd=str(self.work), stdout=self._server_log_handle,
                                     stderr=subprocess.STDOUT, text=True, start_new_session=True)
        append_event(self.event_log, "server_process_started", command=command, pid=self.proc.pid,
                     work_dir=str(self.work), listener_precondition="no Worker connection until lsof confirms 127.0.0.1")
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                try:
                    self._server_log_handle.flush()
                except Exception:
                    pass
                raise RuntimeError(f"task-owned COMSOL server exited {self.proc.returncode}; log={self.server_log}")
            if port_file.is_file():
                try:
                    port = int(port_file.read_text(encoding="utf-8").strip())
                    if 1 <= port <= 65535:
                        self.port = port
                        break
                except (OSError, ValueError):
                    pass
            time.sleep(0.2)
        if self.port is None:
            raise TimeoutError(f"task-owned COMSOL server did not publish a listener port in 45s; log={self.server_log}")

        probe_command = ["/usr/sbin/lsof", "-nP", "-a", "-p", str(self.proc.pid),
                         f"-iTCP:{self.port}", "-sTCP:LISTEN"]
        probe = subprocess.run(probe_command, capture_output=True, text=True, check=False, timeout=10)
        lines = [line for line in probe.stdout.splitlines()[1:] if line.strip()]
        if probe.returncode != 0 or len(lines) != 1:
            raise RuntimeError(f"owned listener lsof did not return exactly one socket: rc={probe.returncode}, out={probe.stdout}, err={probe.stderr}")
        pid_field, endpoint = _parse_lsof_listener(probe.stdout)
        if pid_field != self.proc.pid or endpoint != f"127.0.0.1:{self.port}":
            raise RuntimeError(f"refusing Worker connection: owned COMSOL endpoint is {endpoint!r}, pid={pid_field}; expected 127.0.0.1:{self.port}, pid={self.proc.pid}")

        self.process_identity = _process_snapshot(self.proc.pid)
        if self.process_identity is None:
            raise RuntimeError("task-owned COMSOL PID identity could not be read")
        self.process_identity = {**self.process_identity, "port": self.port}
        write_json(self.receipt_path, {"schema_version": 2, "status": "RUNNING", "process": self.process_identity})
        listener = {
            "status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
            "pid": self.proc.pid,
            "port": self.port,
            "endpoint": endpoint,
            "lsof_command": probe_command,
            "lsof_stdout": probe.stdout,
            "lsof_stderr": probe.stderr,
            "isolation_receipt": str(self.receipt_path),
            "process_identity": self.process_identity,
        }
        write_json(self.evidence / "pre_worker_listener.json", listener)
        append_event(self.event_log, "loopback_listener_verified_before_worker", **listener)
        return listener

    def start_worker(self) -> dict[str, Any]:
        if self.port is None or self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("task-owned listener is not available")
        paths = self.paths_type(self.shadow_root, JAVA11, private_prefs=self.prefs, project_root=self.project)
        self.worker = self.worker_type(paths, state_dir=self.worker_state)
        startup = self.worker.start(startup_timeout_s=25.0)
        self.worker.client().connect(self.port, "127.0.0.1")
        version = self.worker.client().getComsolVersion()
        version_identity = _engine_build_identity(version)
        runtime = self.worker.runtime_metadata()
        from comsol_mcp._g2_isolation import verify_owned_server
        endpoint = f"127.0.0.1:{self.port}"
        proof = verify_owned_server(self.receipt_path, endpoint=endpoint, worker_pid=runtime.get("pid"))
        metadata = {
            "status": "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED",
            "engine_version": str(version),
            "engine_version_identity": version_identity,
            "worker_start": startup,
            "worker_runtime": runtime,
            "server_pid": self.proc.pid,
            "endpoint": endpoint,
            "isolation_proof": proof,
        }
        write_json(self.evidence / "worker_connection.json", metadata)
        append_event(self.event_log, "worker_connected_and_pair_verified", **metadata)
        return metadata

    def worker_path_metadata(self) -> dict[str, Any]:
        return {"shadow_root": self.shadow_root, "prefs_dir": self.prefs,
                "project_root": self.project, "worker_state_dir": self.worker_state}

    def close_worker(self) -> None:
        if self.worker is not None:
            try:
                self.worker.client().disconnect()
            except Exception:
                pass
            self.worker.close()
            self.worker = None

    def stop_server(self, *, allow_stop: bool) -> dict[str, Any]:
        if self.proc is None:
            return {"status": "NOT_STARTED"}
        if not allow_stop:
            return {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN", "pid": self.proc.pid, "port": self.port}
        pid = self.proc.pid
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Only this exact Popen-owned task server may receive a hard stop.
                self.proc.kill()
                self.proc.wait(timeout=3)
        result = {"status": "STOPPED" if self.proc.poll() is not None else "STOP_UNVERIFIED",
                  "pid": pid, "port": self.port, "return_code": self.proc.returncode}
        append_event(self.event_log, "task_owned_server_stopped", **result)
        try:
            self._server_log_handle.close()
        except Exception:
            pass
        return result


def _bind_model(daemon: Any, worker: Any, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    model = worker.client().create(name)
    tag = str(model.java.tag())
    binding = daemon.service.bind_model(tag, ownership="mcp_owned")
    daemon.backend.persist()
    return binding, daemon.backend._resume_model_readback(worker.client().model(tag))


def _dispatch(daemon: Any, operation: str, arguments: dict[str, Any], *, project_id: str,
              ref: dict[str, Any] | None = None, revision: int | None = None,
              idempotency_key: str | None = None, request_id: str | None = None,
              rpc_timeout_s: float = 30.0) -> dict[str, Any]:
    execution: dict[str, Any] = {"project_id": project_id, "rpc_timeout_s": rpc_timeout_s,
                                 "execution_timeout_s": None, "queue_timeout_s": 60.0}
    if ref is not None:
        execution.update({"session_id": ref["session_id"], "model_ref": ref,
                          "expected_revision": revision})
    if idempotency_key is not None:
        execution["idempotency_key"] = idempotency_key
    if request_id is not None:
        execution["request_id"] = request_id
    return daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})


def _sample(daemon: Any, ref: dict[str, Any], revision: int, dataset: str,
            points: list[list[float]], mode: str, project_id: str, label: str) -> dict[str, Any]:
    args = {"spec": {"expressions": ["u"], "solution": {"dataset": dataset}, "complex_mode": mode},
            "points": _frozen_point_records(points), "coordinate_unit": "m", "frame": "spatial"}
    result = _dispatch(daemon, "operation_call", {"operation_id": "result.at_points", "arguments": args},
                       project_id=project_id, ref=ref, revision=revision,
                       idempotency_key=f"{label}-{uuid4()}", request_id=f"{label}-{uuid4()}")
    require_success(result, f"result.at_points/{mode}")
    data = result.get("data", {})
    values = _flatten_numbers(data.get("values"))
    if len(values) != len(points) or not all(math.isfinite(value) for value in values):
        raise AssertionError(f"{mode} native values must contain {len(points)} finite point values; got {values!r}")
    if data.get("cleanup", {}).get("cleanup_failed") is True:
        raise AssertionError(f"{mode} result interpolator cleanup failed: {data.get('cleanup')}")
    return {"response": result, "values": values}


def run(args: argparse.Namespace) -> dict[str, Any]:
    started_mono = time.monotonic()
    prior_native_solve_calls = args.prior_native_solve_calls
    remaining_native_solve_calls = _remaining_native_solve_budget(prior_native_solve_calls)
    if remaining_native_solve_calls == 0:
        raise RuntimeError("the authorized native study.run call budget is already exhausted")
    work = Path(args.work).resolve()
    evidence = Path(args.evidence).resolve()
    run_id = evidence.name
    if not work.as_posix().startswith("/private/tmp/comsol-mcp-native-resume-smoke-"):
        raise RuntimeError("--work must be a unique task directory under /private/tmp/comsol-mcp-native-resume-smoke-*")
    if evidence.exists():
        raise RuntimeError(f"evidence run path already exists: {evidence}")
    evidence.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    event_log = evidence / "events.jsonl"
    project_id = "native-resume-smoke"
    runtime_state: dict[str, Any] = {
        "status": "PREPARING_PRIVATE_SHADOW",
        "run_id": run_id,
        "evidence_dir": str(evidence),
        "work_dir": str(work),
        "server_pid": None,
        "port": None,
        "listener_verified": False,
        "worker_state": "NOT_STARTED",
        "native_solve_count": 0,
        "prior_native_solve_calls": prior_native_solve_calls,
        "total_native_solve_calls": prior_native_solve_calls,
        "max_real_solve_calls": remaining_native_solve_calls,
        "max_total_native_solve_calls": MAX_REAL_SOLVE_CALLS,
        "wall_clock_budget_s": MAX_WALL_S,
        "fixture_sha256": sha256(FIXTURE_SOURCE),
    }
    update_resume_state(runtime_state)
    write_json(evidence / "run_started.json", {
        "run_id": run_id, "started_at_utc": utc_now(), "started_monotonic_s": started_mono,
        "work_dir": str(work), "evidence_dir": str(evidence), "python": sys.version,
        "python_executable": sys.executable, "install_root": str(INSTALL_ROOT),
        "fixture_source": str(FIXTURE_SOURCE), "fixture_source_sha256": sha256(FIXTURE_SOURCE),
        "limits": {"wall_clock_s": MAX_WALL_S,
                   "real_study_run_calls_max_this_run": remaining_native_solve_calls,
                   "prior_native_study_run_calls": prior_native_solve_calls,
                   "real_study_run_calls_max_total": MAX_REAL_SOLVE_CALLS},
    })
    append_event(event_log, "run_started", work_dir=work, evidence_dir=evidence,
                 fixture_sha256=sha256(FIXTURE_SOURCE), max_wall_clock_s=MAX_WALL_S)

    server = NativeLoopbackServer(work, evidence, event_log=event_log)
    daemon = None
    fresh_daemon = None
    worker_started = False
    fresh_worker_started = False
    solve_calls: list[dict[str, Any]] = []
    source_id: str | None = None
    child_id: str | None = None
    terminal_child = False
    summary: dict[str, Any] = {"status": "RUNNING", "run_id": run_id}
    original_study_run = None
    original_store_add_event = None
    resume_dispatch_started = False
    successful_native_child = False
    solved_mph_durable = False
    solved_model_path: Path | None = None
    solved_model_sha256: str | None = None
    solved_model_save_result: Any = None
    import comsol_mcp._g3_ops as g3_ops

    terminal_statuses = TERMINAL_JOB_STATUSES

    def budget_check(stage: str) -> None:
        elapsed = time.monotonic() - started_mono
        if elapsed >= MAX_WALL_S:
            raise TimeoutError(f"180-second total native smoke budget elapsed before {stage}; elapsed={elapsed:.3f}s")
        summary["elapsed_s"] = round(elapsed, 3)

    def ensure_evidence_source_hashes() -> None:
        for name, path, expected in runtime_state["frozen_source_hashes"].values():
            observed = sha256(Path(path))
            if observed != expected:
                raise RuntimeError(f"frozen source changed after pre-solve freeze: {name}: expected {expected}, got {observed}")

    def daemon_idle_proof(candidate: Any) -> dict[str, Any]:
        """Prove this private daemon has no queued/running/unknown engine work."""
        if candidate is None:
            return {"idle": True, "reason": "daemon_not_created", "jobs": [], "worker_requests": []}
        jobs: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = candidate.store.list_jobs(offset=offset, limit=1000)
            jobs.extend(list(page))
            offset += len(page)
            if len(page) < 1000:
                break
        if any(job.get("status") not in terminal_statuses for job in jobs):
            return {"idle": False, "reason": "nonterminal_job", "jobs": jobs, "worker_requests": []}
        requests: list[dict[str, Any]] = []
        for job in jobs:
            events: list[dict[str, Any]] = []
            event_offset = 0
            while True:
                page = candidate.store.events(job["job_id"], offset=event_offset, limit=1000)
                events.extend(page)
                event_offset += len(page)
                if len(page) < 1000:
                    break
            submitted = {row.get("metadata", {}).get("request_id") for row in events
                         if row.get("event") == "worker_request"
                         and row.get("metadata", {}).get("phase") == "submitted"
                         and row.get("metadata", {}).get("request_id")}
            observed = {row.get("metadata", {}).get("request_id"): row.get("metadata", {}).get("status")
                        for row in events if row.get("event") == "worker_request"
                        and row.get("metadata", {}).get("phase") == "observed"
                        and row.get("metadata", {}).get("request_id")}
            requests.extend({"job_id": job["job_id"], "request_id": request_id,
                             "observed_status": observed.get(request_id)} for request_id in submitted)
            if any(observed.get(request_id) not in {"SUCCEEDED", "FAILED"} for request_id in submitted):
                return {"idle": False, "reason": "worker_request_unobserved_or_unknown",
                        "jobs": jobs, "worker_requests": requests}
        return {"idle": True, "reason": "all_jobs_terminal_and_worker_requests_observed",
                "jobs": jobs, "worker_requests": requests}

    def all_owned_work_idle() -> dict[str, Any]:
        proofs = {"control_daemon": daemon_idle_proof(daemon),
                  "fresh_control_daemon": daemon_idle_proof(fresh_daemon)}
        return {"idle": all(item["idle"] for item in proofs.values()), "daemons": proofs}

    try:
        shadow_receipt = server.prepare_shadow()
        runtime_state["status"] = "PRIVATE_SHADOW_PREPARED_NO_SERVER"
        update_resume_state(runtime_state)
        budget_check("private shadow preparation completion")
        budget_check("server start")
        listener = server.start_and_verify_listener()
        runtime_state.update({"status": "LOOPBACK_LISTENER_VERIFIED", "server_pid": listener["pid"],
                              "port": listener["port"], "listener_verified": True})
        update_resume_state(runtime_state)
        budget_check("owned loopback listener verification")
        worker_info = server.start_worker()
        worker_started = True
        runtime_state.update({"status": "WORKER_CONNECTED", "worker_state": "CONNECTED",
                              "engine_version": worker_info["engine_version"],
                              "engine_version_identity": worker_info["engine_version_identity"],
                              "worker_pid": worker_info["worker_runtime"].get("pid")})
        update_resume_state(runtime_state)
        if not worker_info["engine_version_identity"]["matches_frozen_target"]:
            raise RuntimeError(f"native engine runtime identity does not match frozen COMSOL 6.4.0.293: {worker_info['engine_version_identity']!r}")
        os.environ["COMSOL_ROOT"] = str(server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(JAVA11)
        os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
        os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)
        budget_check("control daemon connect")

        from comsol_mcp._control_daemon import ControlDaemon
        from comsol_mcp._execution_contract import model_ref_from_mapping
        daemon = ControlDaemon(work / "control", worker=server.worker, project_root=server.project)
        connection = _dispatch(daemon, "server_connect", {"host": "127.0.0.1", "port": server.port},
                              project_id=project_id, idempotency_key=f"server-connect-{uuid4()}")
        require_success(connection, "managed server_connect")
        endpoint = connection.get("data", {}).get("endpoint")
        if endpoint != f"127.0.0.1:{server.port}":
            raise AssertionError(f"managed backend connected to unexpected endpoint: {endpoint!r}")
        budget_check("managed server connection")

        fixture_in_project = server.project / FIXTURE_SOURCE.name
        shutil.copy2(FIXTURE_SOURCE, fixture_in_project)
        frozen_paths = {
            "fixture": (FIXTURE_SOURCE.name, FIXTURE_SOURCE, sha256(FIXTURE_SOURCE)),
            "runner": (Path(__file__).name, Path(__file__).resolve(), sha256(Path(__file__).resolve())),
            "managed_backend": ("_managed_backend.py", REPO / "comsol_mcp/_managed_backend.py", sha256(REPO / "comsol_mcp/_managed_backend.py")),
            "control_daemon": ("_control_daemon.py", REPO / "comsol_mcp/_control_daemon.py", sha256(REPO / "comsol_mcp/_control_daemon.py")),
            "worker": ("_java_worker.py", REPO / "comsol_mcp/_java_worker.py", sha256(REPO / "comsol_mcp/_java_worker.py")),
            "result_adapter": ("_g3_results.py", REPO / "comsol_mcp/_g3_results.py", sha256(REPO / "comsol_mcp/_g3_results.py")),
        }
        runtime_state["frozen_source_hashes"] = {key: list(value) for key, value in frozen_paths.items()}
        freeze = {
            "status": "FROZEN_BEFORE_SOLVE",
            "frozen_at_utc": utc_now(),
            "limits": {"wall_clock_s_from_harness_start": MAX_WALL_S,
                       "max_real_study_run_calls_this_run": remaining_native_solve_calls,
                       "prior_native_study_run_calls": prior_native_solve_calls,
                       "max_real_study_run_calls_total": MAX_REAL_SOLVE_CALLS},
            "fixture": {"path": str(FIXTURE_SOURCE), "sha256": sha256(FIXTURE_SOURCE)},
            "runner": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__).resolve())},
            "relevant_source_hashes": {key: {"path": str(path), "sha256": digest}
                                       for key, (_name, path, digest) in frozen_paths.items()},
            "model": {"geometry": "2-D 1 m x 1 m rectangle", "physics": "CoefficientFormPDE c=1,a=0,f=0 with DirichletBoundary r=1 on all boundaries", "study": "std1/stat1 Stationary", "mesh": "automatic hauto=5", "marker": MARKER, "expected_analytic_field": "u(x,y)=1"},
            "points_m": [[x, y] for x in POINT_AXIS for y in POINT_AXIS],
            "acceptance": {"all_values_finite": True, "max_abs_real_minus_one": REAL_TOL,
                           "max_abs_imaginary": IMAG_TOL, "pre_vs_reopen_abs_tolerance": REOPEN_TOL},
            "native_route": {"pre_run_snapshot": "actual COMSOL Model.save through restart implementation", "parent_status": "injected after snapshot binding, before study.run; not a native solve failure", "child": "one actual serialized job.resume restore into a fresh model tag then study.run", "fresh_worker": "saved MPH load and result.at_points readback on a distinct Java Worker process"},
            "server": {"installed_tree_read_only": True, "task_private_shadow": str(server.shadow_root), "private_connector_address": "127.0.0.1", "worker_connects_only_after_lsof_loopback_proof": True},
            "runtime": {"os": subprocess.run(["/usr/bin/sw_vers", "-productVersion"], capture_output=True, text=True, check=False).stdout.strip(), "vendor_os_status": "observed native on unlisted macOS 27; not vendor OS certification", "jdk11": str(JAVA11), "required_engine_identity": "COMSOL Multiphysics 6.4.0.293; accept API presentation only when it proves family 6.4 and explicit build 293, preserving the raw localized string"},
        }
        write_json(evidence / "fixture_freeze.json", freeze)
        ensure_evidence_source_hashes()
        runtime_state["status"] = "FROZEN_BEFORE_PARENT_INJECTION"
        update_resume_state(runtime_state)
        append_event(event_log, "fixture_and_thresholds_frozen", freeze_sha256=sha256(evidence / "fixture_freeze.json"),
                     fixture_sha256=sha256(FIXTURE_SOURCE), thresholds=freeze["acceptance"])

        budget_check("model build")
        source_binding, prebuild_readback = _bind_model(daemon, server.worker, "Native Resume Laplace Smoke")
        source_ref = source_binding["execution"]["model_ref"]
        source_revision = int(source_binding["execution"]["revision"])
        runtime_state.update({"status": "BUILDING_FIXTURE", "source_model_ref": source_ref,
                              "source_revision": source_revision})
        update_resume_state(runtime_state)
        build = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": fixture_in_project.name,
                          "entrypoint": "NativeResumePDEFixture#run", "arguments": {}, "mode": "trusted"},
        }, project_id=project_id, ref=source_ref, revision=source_revision,
           idempotency_key=f"fixture-build-{uuid4()}", rpc_timeout_s=45.0)
        require_success(build, "native fixture Java build")
        source_ref = build["execution"]["model_ref"]
        source_revision = int(build["execution"]["revision"])
        built_readback = daemon.backend._resume_model_readback(server.worker.client().model(source_ref["model_tag"]))
        if built_readback["state"].get("solutions") or built_readback["state"].get("file_resource_tags"):
            raise AssertionError("fixture build unexpectedly created pre-existing solutions or file resources")
        marker_rows = [row for row in built_readback["state"].get("parameters", []) if row.get("name") == "smoke_marker"]
        if len(marker_rows) != 1 or str(marker_rows[0].get("expression")) != MARKER:
            raise AssertionError(f"smoke marker did not read back exactly: {marker_rows!r}")
        write_json(evidence / "fixture_build_readback.json", {
            "build_response": build, "prebuild_readback": prebuild_readback,
            "built_readback": built_readback, "marker_readback": marker_rows,
        })
        append_event(event_log, "fixture_built_and_read_back", model_ref=source_ref, revision=source_revision,
                     readback_signature=built_readback["signature"], marker=marker_rows[0])
        runtime_state.update({"status": "FIXTURE_BUILT_NO_SOLVE", "source_model_ref": source_ref,
                              "source_revision": source_revision})
        update_resume_state(runtime_state)
        budget_check("fixture build and model readback")

        # Replace only the Python dispatch target for this source job.  The
        # production study.run function remains saved and is restored before
        # the job.resume call.  The failure occurs after a real pre-run MPH save.
        original_study_run = g3_ops.DISPATCH["study.run"]
        injection = {"count": 0, "at_utc": None, "native_parent_solve": False}

        def injected_parent_failure(worker: Any, model_tag: str, body: dict[str, Any]) -> dict[str, Any]:
            injection["count"] += 1
            injection["at_utc"] = utc_now()
            from comsol_mcp._g3_w16 import _completion
            return _completion(
                dispatched=True, applied=[], failed=[], not_executed=[],
                readback={"readable": True, "state": "TEST_INJECTION_BEFORE_STUDY_RUN"},
                error={"code": "TEST_INJECTED_PARENT_FAILURE",
                       "message": "intentional harness failure after real pre-run snapshot, before native study.run"},
                require_readback=False,
            )

        g3_ops.DISPATCH["study.run"] = injected_parent_failure
        parent_arguments = {"study": _study_node_path("std1"),
                            "recovery_policy": {"mode": "restart_from_checkpoint"}}
        parent = _dispatch(daemon, "study.run", parent_arguments, project_id=project_id,
                           ref=source_ref, revision=source_revision,
                           idempotency_key=f"native-smoke-parent-{uuid4()}", rpc_timeout_s=45.0)
        g3_ops.DISPATCH["study.run"] = original_study_run
        original_study_run = None
        if injection["count"] != 1 or injection["native_parent_solve"]:
            raise AssertionError(f"parent failure injection was not applied exactly once without a solve: {injection!r}")
        if parent.get("success") is not False:
            raise AssertionError(f"injected source operation was expected to fail before native study.run: {parent!r}")
        source_id = parent.get("execution", {}).get("job_id")
        if not isinstance(source_id, str) or not source_id:
            raise AssertionError("failed injected parent has no durable job ID")
        parent_job = daemon.store.job(source_id)
        source_contract = (parent_job or {}).get("metadata", {}).get("resume_contract")
        if (not parent_job or parent_job.get("status") != "FAILED" or not isinstance(source_contract, dict)
                or source_contract.get("checkpoint_id") is None):
            raise AssertionError(f"real pre-run checkpoint/failed source is not durably bound: {parent_job!r}")
        checkpoint_path = Path(source_contract["checkpoint_path"])
        if not checkpoint_path.is_file() or sha256(checkpoint_path) != source_contract["checkpoint_sha256"]:
            raise AssertionError("pre-run checkpoint did not survive with the recorded SHA-256")
        if injection["count"] != 1:
            raise AssertionError("source injection dispatch count changed")
        parent_events = daemon.store.events(source_id, offset=0, limit=1000)
        submitted = {row.get("metadata", {}).get("request_id") for row in parent_events
                     if row.get("event") == "worker_request"
                     and row.get("metadata", {}).get("phase") == "submitted"
                     and row.get("metadata", {}).get("request_id")}
        observed = {row.get("metadata", {}).get("request_id"): row.get("metadata", {}).get("status")
                    for row in parent_events if row.get("event") == "worker_request"
                    and row.get("metadata", {}).get("phase") == "observed"
                    and row.get("metadata", {}).get("request_id")}
        if not submitted or not submitted.issubset(observed) or any(observed[key] not in {"SUCCEEDED", "FAILED"} for key in submitted):
            raise AssertionError("the real pre-run checkpoint Worker requests are not all observed before resume")
        parent_execution = parent.get("execution") if isinstance(parent.get("execution"), dict) else {}
        parent_current_revision = parent_execution.get("revision")
        if (parent_execution.get("model_ref") != source_ref
                or isinstance(parent_current_revision, bool) or not isinstance(parent_current_revision, int)
                or parent_current_revision < source_revision):
            raise AssertionError(f"parent response lacks a current source revision for the same ModelRef: {parent_execution!r}")
        source_ref_obj = model_ref_from_mapping(source_ref)
        current_source_inspect = daemon.service.inspect(source_ref_obj)
        current_execution = current_source_inspect.get("execution", {})
        if (current_execution.get("model_ref") != source_ref
                or current_execution.get("revision") != parent_current_revision):
            raise AssertionError(
                "current source inspection differs from the parent completion receipt: "
                f"parent_revision={parent_current_revision!r}, inspect={current_execution!r}"
            )
        source_state = daemon.service.ledger._state_for(source_ref_obj)
        if (source_state.external_event_counter != source_contract.get("source_external_event_counter")
                or source_state.observed_external_event_counter != source_state.external_event_counter):
            raise RuntimeError(
                "task-owned resume authorization does not cover external changes after the checkpoint: "
                f"snapshot_counter={source_contract.get('source_external_event_counter')}, "
                f"current={source_state.external_event_counter}, observed={source_state.observed_external_event_counter}"
            )
        freeze_sha = sha256(evidence / "fixture_freeze.json")
        model_ref_sha = hashlib.sha256(json.dumps(
            source_ref, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        authorization_ref = (
            "docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md:96; "
            "docs/full_project_execution/evidence/luna_native_resume_smoke/FIXTURE_FREEZE.md; "
            f"fixture_freeze_sha256={freeze_sha}; fixture_source_sha256={sha256(FIXTURE_SOURCE)}; "
            f"source_model_ref_sha256={model_ref_sha}"
        )
        write_json(evidence / "parent_current_revision_authorization.json", {
            "source_job_id": source_id,
            "source_model_ref": source_ref,
            "snapshot_revision": source_contract.get("source_revision"),
            "current_revision_from_parent": parent_current_revision,
            "current_revision_from_live_inspect": current_execution.get("revision"),
            "dirty_after_explicit_test_injection": bool(current_execution.get("dirty")),
            "external_event_counter": source_state.external_event_counter,
            "observed_external_event_counter": source_state.observed_external_event_counter,
            "worker_request_count": len(submitted),
            "worker_request_statuses": sorted({observed[key] for key in submitted}),
            "authorization_ref": authorization_ref,
            "authorization_basis": "main-agent decision in MAIN_ACCEPTANCE_PLAN.md:96 for this frozen task-owned fixture; restart loads a fresh model tag and preserves the source ModelRef",
            "source_model_policy": "preserve original model/reference; no selection or overwrite",
        })
        runtime_state.update({"source_snapshot_revision": source_contract.get("source_revision"),
                              "source_current_revision": parent_current_revision,
                              "source_model_dirty_authorized": bool(current_execution.get("dirty")),
                              "authorization_ref_sha256": hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()})
        update_resume_state(runtime_state)
        budget_check("injected parent and snapshot verification")
        write_json(evidence / "injected_parent_and_snapshot.json", {
            "classification": "TEST_INJECTION_NO_NATIVE_PARENT_SOLVE",
            "injection": injection, "source_response": parent, "source_job": parent_job,
            "checkpoint_path": str(checkpoint_path), "checkpoint_sha256": sha256(checkpoint_path),
            "checkpoint_metadata": next((row for row in daemon.store.list_metadata("checkpoints")
                                          if row.get("checkpoint_id") == source_contract["checkpoint_id"]), None),
            "worker_submitted_count": len(submitted), "worker_observed_count": len(observed),
            "worker_events": parent_events,
        })
        append_event(event_log, "parent_failed_by_explicit_test_injection_after_native_snapshot",
                     source_job_id=source_id, injection=injection,
                     checkpoint_sha256=source_contract["checkpoint_sha256"],
                     native_parent_solver_calls=0)
        runtime_state.update({"status": "PARENT_FAILED_BY_TEST_INJECTION_SNAPSHOT_VERIFIED",
                              "source_job_id": source_id, "checkpoint_sha256": source_contract["checkpoint_sha256"],
                              "native_solve_count": 0})
        update_resume_state(runtime_state)

        # Count only the persisted Worker event emitted immediately before
        # JavaWorker sends Model.study(tag).run(). Entering the Python adapter
        # can fail request validation without dispatching any COMSOL call.
        original_store_add_event = daemon.store.add_event
        resume_child_job_ids: set[str] = set()

        def capture_native_study_run(job_id: str, event: str, metadata: dict[str, Any]) -> Any:
            if (event == "ResumeContinuationQueued" and isinstance(metadata, dict)
                    and metadata.get("source_job_id") == source_id):
                resume_child_job_ids.add(job_id)
            if (event == "worker_request" and job_id in resume_child_job_ids
                    and _is_native_study_run_submission(metadata)):
                if len(solve_calls) >= remaining_native_solve_calls:
                    blocked = {"job_id": job_id, "worker_event": metadata,
                               "reason": "frozen native study.run submission ceiling reached"}
                    append_event(event_log, "native_study_run_submission_blocked_at_ceiling", **blocked)
                    raise RuntimeError("frozen native study.run Worker submission ceiling exceeded")
                row = {"call_index": len(solve_calls) + 1, "at_utc": utc_now(), "job_id": job_id,
                       "request_id": metadata.get("request_id"), "operation_id": metadata.get("operation_id"),
                       "worker_event": metadata}
                # This durable event is committed before the Worker sends the
                # Java request. Count only after its persistence succeeds.
                result = original_store_add_event(job_id, event, metadata)
                solve_calls.append(row)
                append_event(event_log, "native_study_run_worker_submission", **row)
                return result
            return original_store_add_event(job_id, event, metadata)

        daemon.store.add_event = capture_native_study_run
        resume_request = {
            "operation": "job.resume", "arguments": {"job_id": source_id,
                                                          "authorization_ref": authorization_ref},
            "execution": {"session_id": source_ref["session_id"], "model_ref": source_ref,
                          "expected_revision": parent_current_revision,
                          "idempotency_key": f"native-smoke-resume-{uuid4()}",
                          "request_id": f"native-smoke-resume-request-{uuid4()}",
                          "project_id": project_id, "rpc_timeout_s": 0.2,
                          "execution_timeout_s": None, "queue_timeout_s": 60.0},
        }
        resume_dispatch_started = True
        child_response = daemon.dispatch(resume_request)
        if not child_response.get("success") and child_response.get("error", {}).get("code") not in {"RPC_WAIT_EXPIRED", "EXECUTION_PENDING"}:
            raise RuntimeError("job.resume was not admitted: " + json.dumps(child_response, ensure_ascii=False, default=json_default)[:5000])
        child_id = child_response.get("execution", {}).get("job_id")
        if not isinstance(child_id, str) or not child_id:
            child_id = child_response.get("data", {}).get("job_id")
        if not isinstance(child_id, str) or not child_id:
            raise RuntimeError("job.resume admission returned no continuation job ID")
        runtime_state.update({"status": "NATIVE_RESUME_CHILD_RUNNING", "child_job_id": child_id,
                              "native_solve_count": len(solve_calls)})
        update_resume_state(runtime_state, active_jobs=[{"job_id": child_id, "operation": "job.resume->study.run", "status": "RUNNING"}])
        append_event(event_log, "resume_child_admitted", source_job_id=source_id, child_job_id=child_id,
                     response=child_response, idempotency_key=resume_request["execution"]["idempotency_key"])
        budget_check("resume child admission")

        child_job: dict[str, Any] | None = None
        while time.monotonic() - started_mono < MAX_WALL_S - 3.0:
            child_job = daemon.store.job(child_id)
            if child_job and child_job.get("status") in terminal_statuses:
                terminal_child = True
                break
            time.sleep(0.15)
        if not terminal_child:
            # Inspect the original operation and owned Worker.  Do not retry or
            # terminate while a solver request may still be active.
            child_job = daemon.store.job(child_id)
            try:
                worker_status = server.worker.health(timeout_s=0.5)
            except Exception as exc:
                worker_status = {"status": "UNKNOWN", "error": type(exc).__name__}
            try:
                process_snapshot = subprocess.run(["/bin/ps", "-p", str(server.proc.pid), "-o", "pid=,ppid=,lstart=,command="],
                                                  capture_output=True, text=True, check=False, timeout=5)
                process_row = process_snapshot.stdout.strip()
            except Exception as exc:
                process_row = f"unavailable:{type(exc).__name__}"
            timeout_evidence = {"status": child_job.get("status") if child_job else "MISSING",
                                "child_job": child_job, "worker_health": worker_status,
                                "server_pid": server.proc.pid, "server_process_row": process_row,
                                "listener_endpoint": f"127.0.0.1:{server.port}",
                                "action": "NO_BLIND_RESUBMIT_OR_TERMINATION"}
            write_json(evidence / "child_timeout_inspection.json", timeout_evidence)
            runtime_state.update({"status": "CHILD_STILL_ACTIVE_AT_BUDGET_INSPECTION",
                                  "child_job_id": child_id, "native_solve_count": len(solve_calls),
                                  "timeout_inspection": timeout_evidence})
            update_resume_state(runtime_state, active_jobs=[{"job_id": child_id, "status": timeout_evidence["status"]}])
            write_json(evidence / "summary.json", {"status": "PENDING_ACTIVE_NATIVE_JOB", **timeout_evidence,
                                                   "elapsed_s": round(time.monotonic() - started_mono, 3)})
            # Do not close a worker or stop the server if the solve is not
            # conclusively terminal; its owned process stays available to the
            # parent for inspection and safe continuation.
            summary.update(timeout_evidence)
            return summary

        if not child_job:
            raise RuntimeError("terminal resume child record disappeared")
        child_terminal_result = child_job.get("result") if isinstance(child_job.get("result"), dict) else {}
        if child_job.get("status") == "SUCCEEDED" and child_terminal_result.get("success") is True:
            successful_native_child = True
            child_data_for_save = child_terminal_result.get("data") if isinstance(child_terminal_result.get("data"), dict) else {}
            resume_data_for_save = child_data_for_save.get("resume") if isinstance(child_data_for_save.get("resume"), dict) else {}
            new_ref_for_save = resume_data_for_save.get("new_model_ref")
            if not isinstance(new_ref_for_save, dict) or not isinstance(new_ref_for_save.get("model_tag"), str):
                raise AssertionError("successful continuation has no fresh model identity to save")
            budget_check("immediate save of successful resumed model")
            solved_model_path = server.project / "native_resume_child.mph"
            solved_model = server.worker.client().model(new_ref_for_save["model_tag"])
            solved_model_save_result = solved_model.save(str(solved_model_path))
            if not solved_model_path.is_file() or solved_model_path.stat().st_size <= 0:
                raise AssertionError("successful native child did not produce a non-empty immediate MPH save")
            solved_model_sha256 = sha256(solved_model_path)
            solved_mph_durable = True
            immediate_save_receipt = {
                "status": "SOLVED_CHILD_MPH_SAVED_BEFORE_READBACK_CHECKS",
                "run_id": run_id,
                "source_job_id": source_id,
                "source_status": "FAILED_BY_TEST_INJECTION",
                "source_model_ref": source_ref,
                "snapshot_revision": source_contract.get("source_revision"),
                "source_current_revision": parent_current_revision,
                "source_authorization_ref_sha256": hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest(),
                "checkpoint_sha256": source_contract.get("checkpoint_sha256"),
                "child_job_id": child_id,
                "child_operation_id": child_job.get("operation_id"),
                "child_status": child_job.get("status"),
                "child_model_ref": new_ref_for_save,
                "child_result_success": child_terminal_result.get("success"),
                "child_domain_outcome": child_data_for_save.get("domain_outcome"),
                "fixture_source_sha256": sha256(FIXTURE_SOURCE),
                "fixture_freeze_sha256": sha256(evidence / "fixture_freeze.json"),
                "saved_path": str(solved_model_path),
                "saved_sha256": solved_model_sha256,
                "saved_size_bytes": solved_model_path.stat().st_size,
                "save_result": solved_model_save_result,
                "save_order": "immediately_after_child_SUCCEEDED_and_new_ModelRef; before Worker event audits, point sampling, or fresh Worker setup",
                "native_study_run_call_ceiling_this_run": remaining_native_solve_calls,
                "native_study_run_call_ceiling_total": MAX_REAL_SOLVE_CALLS,
            }
            write_json(evidence / "solved_child_immediate_save.json", immediate_save_receipt)
            append_event(event_log, "successful_child_model_saved_before_verification", **immediate_save_receipt)
            runtime_state.update({"status": "SOLVED_CHILD_SAVED_PENDING_VERIFICATION",
                                  "native_solve_count": len(solve_calls), "child_job_id": child_id,
                                  "solved_model_path": str(solved_model_path),
                                  "solved_model_sha256": solved_model_sha256})
            update_resume_state(runtime_state)
            budget_check("successful child MPH saved")
        child_events = []
        while True:
            page = daemon.store.events(child_id, offset=len(child_events), limit=1000)
            child_events.extend(page)
            if len(page) < 1000:
                break
        child_worker_events = [row.get("metadata") for row in child_events
                               if row.get("event") == "worker_request" and isinstance(row.get("metadata"), dict)]
        observed_run_status = {
            row.get("request_id"): row.get("status") for row in child_worker_events
            if row.get("phase") == "observed"
        }
        for row in solve_calls:
            row["observed_status"] = observed_run_status.get(row["request_id"])
        if any(row.get("observed_status") not in {"SUCCEEDED", "FAILED"} for row in solve_calls):
            raise AssertionError(f"COMSOL study.run Worker calls lack terminal observed status: {solve_calls!r}")
        write_json(evidence / "native_study_run_worker_events.json", {
            "source_job_id": source_id, "child_job_id": child_id,
            "study_run_submission_count": len(solve_calls),
            "max_real_study_run_calls_this_run": remaining_native_solve_calls,
            "prior_native_study_run_calls": prior_native_solve_calls,
            "max_real_study_run_calls_total": MAX_REAL_SOLVE_CALLS,
            "submissions": solve_calls,
            "raw_worker_request_events": [row for row in child_worker_events
                                          if _is_native_study_run_submission(row)
                                          or (row.get("phase") == "observed"
                                              and row.get("request_id") in {item.get("request_id") for item in solve_calls})],
            "count_basis": "persisted Worker phase=submitted, kind=call, nested metadata.method=run, scoped to the durable resume child job; operation_id is an internal UUID, not the domain action name",
        })
        # The job is terminal, so no later native submission can be pending.
        # Restore the store method before idempotent replay and readback work.
        daemon.store.add_event = original_store_add_event
        original_store_add_event = None
        if child_job.get("status") != "SUCCEEDED" or not (child_job.get("result") or {}).get("success"):
            write_json(evidence / "failed_child.json", {"child_response": child_response,
                                                          "child_job": child_job,
                                                          "worker_study_run_submissions": solve_calls,
                                                          "raw_worker_events": child_worker_events,
                                                          "adapter_entry_is_not_a_solve_count": True})
            raise RuntimeError("native resumed child did not reach SUCCEEDED; raw job and worker events retained")
        if len(solve_calls) != 1:
            raise AssertionError(f"expected exactly one submitted COMSOL study.run Worker call; observed {len(solve_calls)}")
        budget_check("native continuation job completion")
        repeated_resume = daemon.dispatch(resume_request)
        repeated_job_id = repeated_resume.get("execution", {}).get("job_id")
        if repeated_job_id is None:
            repeated_job_id = repeated_resume.get("data", {}).get("job_id")
        if repeated_job_id != child_id or len(solve_calls) != 1:
            raise AssertionError(f"idempotent resume retry did not return the same completed child: response={repeated_resume!r}, child={child_id!r}, solve_count={len(solve_calls)}")
        write_json(evidence / "resume_idempotent_retry.json", {
            "status": "PASS", "source_job_id": source_id, "child_job_id": child_id,
            "repeated_response": repeated_resume, "native_solve_calls_after_retry": len(solve_calls),
            "same_request_body_and_idempotency_key": True,
        })
        budget_check("resume idempotent retry")
        child_result = child_job["result"]
        child_data = child_result.get("data", {})
        resume_data = child_data.get("resume", {})
        new_ref = resume_data.get("new_model_ref")
        if not isinstance(new_ref, dict):
            raise AssertionError("continuation result did not return its fresh model identity")
        child_execution = child_result.get("execution", {})
        child_revision = child_execution.get("revision")
        if isinstance(child_revision, bool) or not isinstance(child_revision, int):
            child_revision = daemon.service.ledger._state_for(
                __import__("comsol_mcp._execution_contract", fromlist=["model_ref_from_mapping"]).model_ref_from_mapping(new_ref)
            ).revision
        child_readback = daemon.backend._resume_model_readback(server.worker.client().model(new_ref["model_tag"]))
        if child_readback["signature"] == source_contract["source_model_readback"]["signature"]:
            raise AssertionError("post-solve fresh model readback signature did not reflect the solved model")
        datasets = child_readback["state"].get("datasets", [])
        solutions = child_readback["state"].get("solutions", [])
        if not datasets or not solutions:
            raise AssertionError(f"native solve did not leave readable solution and dataset tags: {child_readback['state']!r}")
        dataset = datasets[0]
        points = [[x, y] for x in POINT_AXIS for y in POINT_AXIS]
        real_before = _sample(daemon, new_ref, child_revision, dataset, points, "real", project_id, "resume-real")
        budget_check("native real point sampling")
        imag_before = _sample(daemon, new_ref, child_revision, dataset, points, "imag", project_id, "resume-imag")
        budget_check("native imaginary point sampling")
        real_values = real_before["values"]
        imag_values = imag_before["values"]
        real_errors = [abs(value - 1.0) for value in real_values]
        imag_errors = [abs(value) for value in imag_values]
        if max(real_errors) > REAL_TOL:
            raise AssertionError(f"nine-point real field tolerance failed: max={max(real_errors)}, values={real_values}")
        if max(imag_errors) > IMAG_TOL:
            raise AssertionError(f"nine-point imaginary field tolerance failed: max={max(imag_errors)}, values={imag_values}")
        marker_rows = [row for row in child_readback["state"].get("parameters", []) if row.get("name") == "smoke_marker"]
        if len(marker_rows) != 1 or str(marker_rows[0].get("expression")) != MARKER:
            raise AssertionError(f"resumed model marker readback changed: {marker_rows!r}")
        runtime_state.update({"status": "NATIVE_RESUME_SOLVE_AND_POINT_CHECKS_PASS",
                              "native_solve_count": len(solve_calls), "child_job_id": child_id,
                              "child_model_ref": new_ref, "child_revision": child_revision,
                              "child_readback_signature": child_readback["signature"],
                              "dataset": dataset, "solutions": solutions})
        update_resume_state(runtime_state)

        saved_path = solved_model_path
        saved_sha = solved_model_sha256
        save_result = solved_model_save_result
        if saved_path is None or saved_sha is None or not saved_path.is_file():
            raise AssertionError("the child was not durably saved before result inspection")
        if sha256(saved_path) != saved_sha:
            raise AssertionError("the immediately saved solved child MPH changed during verification")
        write_json(evidence / "native_child_result_before_reopen.json", {
            "source_job_id": source_id, "child_job_id": child_id,
            "child_job": child_job, "real_study_run_calls": solve_calls,
            "child_result": child_result, "child_readback": child_readback,
            "marker_readback": marker_rows, "dataset": dataset, "solutions": solutions,
            "points_m": points, "real": real_before, "imaginary": imag_before,
            "max_abs_real_minus_one": max(real_errors), "max_abs_imaginary": max(imag_errors),
            "save_result": save_result, "saved_path": str(saved_path), "saved_sha256": saved_sha,
        })
        append_event(event_log, "native_child_values_verified_and_saved", child_job_id=child_id,
                     saved_sha256=saved_sha, max_real_error=max(real_errors), max_imag_error=max(imag_errors))
        budget_check("native child save")

        # The fresh-worker phase has no live mutation on the previous worker.
        daemon.close()
        daemon = None
        server.close_worker()
        worker_started = False
        fresh_paths = server.paths_type(server.shadow_root, JAVA11, private_prefs=server.prefs,
                                        project_root=server.project)
        fresh_worker = server.worker_type(fresh_paths, state_dir=work / "fresh-worker-state")
        server.worker = fresh_worker
        fresh_worker.start(startup_timeout_s=25.0)
        fresh_worker.client().connect(server.port, "127.0.0.1")
        fresh_worker_started = True
        fresh_runtime = fresh_worker.runtime_metadata()
        old_worker_pid = worker_info["worker_runtime"].get("pid")
        if not isinstance(fresh_runtime.get("pid"), int) or fresh_runtime["pid"] == old_worker_pid:
            raise AssertionError(f"fresh worker process identity was not distinct: old={old_worker_pid}, new={fresh_runtime}")
        from comsol_mcp._g2_isolation import verify_owned_server
        fresh_isolation = verify_owned_server(server.receipt_path, endpoint=f"127.0.0.1:{server.port}",
                                              worker_pid=fresh_runtime["pid"])
        fresh_daemon = ControlDaemon(work / "fresh-control", worker=fresh_worker, project_root=server.project)
        fresh_connection = _dispatch(fresh_daemon, "server_connect", {"host": "127.0.0.1", "port": server.port},
                                     project_id=project_id, idempotency_key=f"fresh-server-connect-{uuid4()}")
        require_success(fresh_connection, "fresh worker managed server_connect")
        budget_check("fresh Java Worker and managed connection")
        loaded = fresh_worker.client().load(str(saved_path), tag=f"fresh_{uuid4().hex[:12]}")
        loaded_tag = str(loaded.java.tag())
        fresh_binding = fresh_daemon.service.bind_model(loaded_tag, ownership="mcp_owned")
        fresh_daemon.backend.persist()
        fresh_ref = fresh_binding["execution"]["model_ref"]
        fresh_revision = int(fresh_binding["execution"]["revision"])
        fresh_readback = fresh_daemon.backend._resume_model_readback(fresh_worker.client().model(loaded_tag))
        if fresh_readback["signature"] != child_readback["signature"]:
            raise AssertionError(f"fresh Worker model signature differs from saved solved model: before={child_readback['signature']}, after={fresh_readback['signature']}")
        fresh_marker_rows = [row for row in fresh_readback["state"].get("parameters", []) if row.get("name") == "smoke_marker"]
        if len(fresh_marker_rows) != 1 or str(fresh_marker_rows[0].get("expression")) != MARKER:
            raise AssertionError(f"fresh Worker marker readback changed: {fresh_marker_rows!r}")
        fresh_real = _sample(fresh_daemon, fresh_ref, fresh_revision, dataset, points, "real", project_id, "fresh-real")
        budget_check("fresh Worker real point sampling")
        fresh_imag = _sample(fresh_daemon, fresh_ref, fresh_revision, dataset, points, "imag", project_id, "fresh-imag")
        budget_check("fresh Worker imaginary point sampling")
        reopen_real_deltas = [abs(a - b) for a, b in zip(real_values, fresh_real["values"])]
        reopen_imag_deltas = [abs(a - b) for a, b in zip(imag_values, fresh_imag["values"])]
        if max(reopen_real_deltas) > REOPEN_TOL or max(reopen_imag_deltas) > REOPEN_TOL:
            raise AssertionError(f"fresh Worker saved solution did not match pre-save points within {REOPEN_TOL}: real={reopen_real_deltas}, imag={reopen_imag_deltas}")
        if max(abs(value - 1.0) for value in fresh_real["values"]) > REAL_TOL:
            raise AssertionError("fresh Worker real field no longer matches frozen analytic solution")
        if max(abs(value) for value in fresh_imag["values"]) > IMAG_TOL:
            raise AssertionError("fresh Worker imaginary field no longer matches frozen analytic solution")
        write_json(evidence / "fresh_worker_reopen.json", {
            "status": "PASS", "saved_path": str(saved_path), "saved_sha256_before": saved_sha,
            "saved_sha256_after": sha256(saved_path), "fresh_worker_runtime": fresh_runtime,
            "previous_worker_pid": old_worker_pid, "fresh_isolation_proof": fresh_isolation,
            "fresh_connection": fresh_connection, "fresh_loaded_tag": loaded_tag,
            "fresh_model_ref": fresh_ref, "fresh_revision": fresh_revision,
            "pre_save_readback_signature": child_readback["signature"],
            "fresh_readback": fresh_readback, "fresh_marker_readback": fresh_marker_rows,
            "pre_save_real": real_before, "fresh_real": fresh_real,
            "pre_save_imaginary": imag_before, "fresh_imaginary": fresh_imag,
            "max_reopen_real_abs_delta": max(reopen_real_deltas),
            "max_reopen_imag_abs_delta": max(reopen_imag_deltas),
            "reopen_abs_tolerance": REOPEN_TOL,
        })

        budget_check("final native resume smoke acceptance")

        final_status = {
            "status": "PASS_NATIVE_RESUME_SMOKE",
            "run_id": run_id,
            "started_at_utc": json.loads((evidence / "run_started.json").read_text())["started_at_utc"],
            "completed_at_utc": utc_now(),
            "elapsed_s": round(time.monotonic() - started_mono, 3),
            "native_engine_version": worker_info["engine_version"],
            "native_engine_version_identity": worker_info["engine_version_identity"],
            "os_scope": "observed native on unlisted macOS 27; not vendor certification",
            "server": {"pid": server.proc.pid, "port": server.port, "endpoint": f"127.0.0.1:{server.port}",
                       "pre_worker_lsof": listener, "worker_isolation_proof": worker_info["isolation_proof"]},
            "source_job_id": source_id, "source_status": parent_job["status"],
            "parent_failure_classification": "TEST_INJECTION_NO_NATIVE_PARENT_SOLVE",
            "checkpoint_sha256": source_contract["checkpoint_sha256"],
            "child_job_id": child_id, "child_status": child_job["status"],
            "child_model_ref": new_ref, "child_revision": child_revision,
            "real_native_study_run_calls": solve_calls,
            "native_solve_count": len(solve_calls),
            "total_native_solve_count": prior_native_solve_calls + len(solve_calls),
            "max_solve_calls_this_run": remaining_native_solve_calls,
            "max_solve_calls_total": MAX_REAL_SOLVE_CALLS,
            "marker": MARKER, "dataset": dataset, "solutions": solutions,
            "max_abs_real_minus_one": max(real_errors), "real_tolerance": REAL_TOL,
            "max_abs_imaginary": max(imag_errors), "imaginary_tolerance": IMAG_TOL,
            "max_reopen_real_abs_delta": max(reopen_real_deltas),
            "max_reopen_imag_abs_delta": max(reopen_imag_deltas), "reopen_tolerance": REOPEN_TOL,
            "saved_model": {"path": str(saved_path), "sha256": saved_sha, "size_bytes": saved_path.stat().st_size},
            "source_readback_signature": source_contract["source_model_readback"]["signature"],
            "child_readback_signature": child_readback["signature"],
            "fresh_worker_readback_signature": fresh_readback["signature"],
            "fresh_worker_pid": fresh_runtime["pid"], "old_worker_pid": old_worker_pid,
            "fresh_worker": "distinct JVM process; saved MPH loaded and point samples repeated",
            "criteria_scope": "synthetic restart/solve/persistence API smoke only; not W23/W24 scientific acceptance",
        }
        summary = final_status
        write_json(evidence / "summary.json", final_status)
        append_event(event_log, "native_resume_smoke_passed", summary=final_status)
        runtime_state.update({"status": final_status["status"], "native_solve_count": len(solve_calls),
                              "total_native_solve_calls": prior_native_solve_calls + len(solve_calls),
                              "source_job_id": source_id, "child_job_id": child_id,
                              "saved_model_sha256": saved_sha, "fresh_worker_pid": fresh_runtime["pid"],
                              "completed_at_utc": final_status["completed_at_utc"],
                              "elapsed_s": final_status["elapsed_s"]})
        update_resume_state(runtime_state)
        return summary
    except BaseException as exc:
        try:
            if original_study_run is not None:
                g3_ops.DISPATCH["study.run"] = original_study_run
            failure = {
                "status": "FAIL_OR_INCOMPLETE",
                "at_utc": utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "elapsed_s": round(time.monotonic() - started_mono, 3),
                "source_job_id": source_id,
                "child_job_id": child_id,
                "native_solve_count": len(solve_calls),
                "prior_native_solve_calls": prior_native_solve_calls,
                "total_native_solve_calls": prior_native_solve_calls + len(solve_calls),
                "real_study_run_calls": solve_calls,
            }
            write_json(evidence / "failure.json", failure)
            append_event(event_log, "native_resume_smoke_failed", **failure)
            runtime_state.update({"status": "FAIL_OR_INCOMPLETE", "native_solve_count": len(solve_calls),
                                  "total_native_solve_calls": prior_native_solve_calls + len(solve_calls),
                                  "source_job_id": source_id, "child_job_id": child_id,
                                  "last_error": {"type": type(exc).__name__, "message": str(exc)},
                                  "elapsed_s": failure["elapsed_s"]})
            if child_id and not terminal_child and daemon is not None:
                job = daemon.store.job(child_id)
                if job and job.get("status") not in terminal_statuses:
                    update_resume_state(runtime_state, active_jobs=[{"job_id": child_id, "status": job.get("status")}])
                else:
                    update_resume_state(runtime_state)
            else:
                update_resume_state(runtime_state)
        except Exception:
            traceback.print_exc()
        raise
    finally:
        if (original_store_add_event is not None and daemon is not None
                and (terminal_child or not resume_dispatch_started)):
            try:
                daemon.store.add_event = original_store_add_event
            except Exception:
                pass
        idle_proof = all_owned_work_idle()
        preserve_successful_unsaved_model = successful_native_child and not solved_mph_durable
        safe_to_stop = idle_proof["idle"] and not preserve_successful_unsaved_model
        if safe_to_stop and fresh_daemon is not None:
            try:
                fresh_daemon.close()
            except Exception:
                pass
        if safe_to_stop and daemon is not None:
            try:
                daemon.close()
            except Exception:
                pass
        if safe_to_stop and (fresh_worker_started or worker_started):
            try:
                server.close_worker()
            except Exception:
                pass
        if preserve_successful_unsaved_model:
            stop = {"status": "LEFT_RUNNING_FOR_SOLVED_MODEL_INSPECTION",
                    "pid": server.proc.pid if server.proc is not None else None,
                    "port": server.port,
                    "reason": "child succeeded but immediate MPH save did not become durable",
                    "worker_state_dir": str(server.worker_state), "project_dir": str(server.project)}
        else:
            stop = server.stop_server(allow_stop=safe_to_stop)
        try:
            write_json(evidence / "cleanup.json", {"server": stop,
                "worker_closed": server.worker is None,
                "idle_proof": idle_proof,
                "cleanup_policy": "stop only the task-owned server after terminal job and durable solved-model save; preserve a successful unsaved model for inspection"})
        except Exception:
            pass
        if safe_to_stop:
            runtime_state["active_jobs"] = []
        elif preserve_successful_unsaved_model:
            runtime_state["status"] = "SOLVED_CHILD_SERVER_PRESERVED_NO_DURABLE_MPH"
            runtime_state["active_jobs"] = [{"job_id": child_id, "status": "SUCCEEDED",
                                              "server_pid": stop.get("pid"), "port": stop.get("port"),
                                              "reason": stop["reason"],
                                              "project_dir": str(server.project)}]
        update_resume_state(runtime_state, active_jobs=runtime_state.get("active_jobs", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--prior-native-solve-calls", type=int, default=0,
                        help="previous successful/attempted native Study.run submissions in this frozen smoke budget")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=json_default, allow_nan=False))
    return 0 if result.get("status") == "PASS_NATIVE_RESUME_SMOKE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
