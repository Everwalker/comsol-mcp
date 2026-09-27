#!/usr/bin/env python3
"""Read a solved resume-smoke MPH with two fresh Workers, without solving.

This is a separately timed supplement to the frozen two-solve native smoke.
It reloads only the already-saved task-owned artifact. A guard blocks known
study/solver execution calls on their exact COMSOL node handles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.run_native_resume_smoke import (  # noqa: E402
    INSTALL_ROOT,
    JAVA11,
    IMAG_TOL,
    MARKER,
    POINT_AXIS,
    REAL_TOL,
    REOPEN_TOL,
    NativeLoopbackServer,
    _dispatch,
    _engine_build_identity,
    _flatten_numbers,
    _sample,
    append_event,
    json_default,
    require_success,
    sha256,
    utc_now,
    write_json,
)

RUN08_EVIDENCE = REPO / "docs/full_project_execution/evidence/luna_native_resume_smoke/run_20260926T070024Z_08"
RUN08_RECEIPT = RUN08_EVIDENCE / "solved_child_immediate_save.json"
PROJECT_ID = "native-resume-saved-artifact-supplement"
TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED", "LOST", "CANCELLED"}
SOLVER_EXECUTION_METHODS = {"run", "runAll", "runFromTo", "runNoGen", "runFromToNoGen"}


def write_resume_progress(supplement: dict[str, Any], active: bool) -> None:
    path = REPO / "docs/full_project_execution/state/RESUME.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["native_resume_saved_artifact_supplement"] = supplement
    attempts = state.get("native_resume_saved_artifact_supplement_attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempt = {key: supplement.get(key) for key in (
        "supplement_id", "status", "evidence_dir", "work_dir", "source_run_id",
        "source_run_status", "saved_artifact_sha256", "solve_calls_allowed",
        "server_pid", "port", "worker_a_pid", "worker_b_pid", "elapsed_s", "last_error",
    ) if key in supplement}
    if isinstance(attempt.get("supplement_id"), str):
        for index, previous in enumerate(attempts):
            if isinstance(previous, dict) and previous.get("supplement_id") == attempt["supplement_id"]:
                attempts[index] = {**previous, **attempt}
                break
        else:
            attempts.append(attempt)
    state["native_resume_saved_artifact_supplement_attempts"] = attempts
    state["active_jobs"] = ([{
        "job_id": supplement["supplement_id"],
        "operation": "native_saved_mph_readback_only",
        "status": supplement.get("status", "RUNNING"),
        "solve_calls_allowed": 0,
    }] if active else [])
    note = state.get("note", "")
    boundary = "Saved-MPH readback supplement uses separate time/evidence and zero study/solver calls; run08 remains FAIL_OR_INCOMPLETE."
    if boundary not in note:
        state["note"] = (note + " " + boundary).strip()
    temporary = path.with_name(path.name + ".supplement.tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class ZeroSolverCallGuard:
    """Wrap one Worker and refuse execution on discovered study/solver handles."""

    def __init__(self, worker: Any, *, event_path: Path) -> None:
        self.worker = worker
        self.event_path = event_path
        self.protected_handles: dict[str, dict[str, Any]] = {}
        self.observed_requests: dict[str, str] = {}
        self.blocked_attempts: list[dict[str, Any]] = []
        self.method_run_events: list[dict[str, Any]] = []
        self._original_submit = worker.submit
        worker.submit = self._guarded_submit

    def protect(self, handle: str, *, kind: str, tag: str, java_type: str) -> None:
        if not handle:
            raise RuntimeError(f"cannot install zero-solve guard without a {kind} handle")
        self.protected_handles[handle] = {"kind": kind, "tag": tag, "java_type": java_type}

    def _guarded_submit(self, kind: str, payload: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        method = payload.get("method") if isinstance(payload, Mapping) else None
        handle = payload.get("handle") if isinstance(payload, Mapping) else None
        request_id = str(kwargs.get("request_id") or f"supplement-{uuid4()}")
        record = {
            "phase": "attempt",
            "at_utc": utc_now(),
            "request_id": request_id,
            "kind": kind,
            "method": method,
            "handle": handle,
            "protected_node": self.protected_handles.get(str(handle)) if handle is not None else None,
        }
        if kind == "call" and method in SOLVER_EXECUTION_METHODS:
            self.method_run_events.append(record)
        protected = self.protected_handles.get(str(handle)) if handle is not None else None
        if kind == "call" and method in SOLVER_EXECUTION_METHODS and protected is not None:
            blocked = {**record, "phase": "blocked_before_worker_submission", "reason": "zero_solver_call_budget"}
            self.blocked_attempts.append(blocked)
            append_event(self.event_path, "solver_call_blocked", **blocked)
            raise RuntimeError(f"zero-solve guard blocked {method} on {protected['kind']} {protected['tag']}")

        try:
            submit_kwargs = dict(kwargs)
            if not submit_kwargs.get("request_id"):
                submit_kwargs["request_id"] = request_id
            response = self._original_submit(kind, payload, **submit_kwargs)
        except BaseException as exc:
            self.observed_requests[request_id] = "UNKNOWN"
            append_event(self.event_path, "worker_request_unknown", **record, error=f"{type(exc).__name__}: {exc}")
            raise
        status = str(response.get("status", "")) if isinstance(response, Mapping) else ""
        self.observed_requests[request_id] = status or "OBSERVED"
        append_event(self.event_path, "worker_request_observed", **record, status=status or "OBSERVED")
        return response

    def remove(self) -> None:
        self.worker.submit = self._original_submit

    def safe_to_stop(self) -> bool:
        return all(
            status not in {"UNKNOWN", "RUNNING", "QUEUED"} for status in self.observed_requests.values()
        )


def _process_inventory() -> dict[str, Any]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,comm=,args="],
        capture_output=True, text=True, timeout=10, check=False,
    )
    relevant = []
    for line in completed.stdout.splitlines():
        low = line.lower()
        if "/applications/comsol64/" in low or "comsol mphserver" in low or "/java" in low:
            relevant.append(line.strip())
    return {
        "exit_code": completed.returncode,
        "filtered_comsol_java_processes": relevant,
        "active_comsol_or_java_engine_seen": bool(relevant),
        "unrelated_python_mcp_services": "not stopped or signaled",
    }


def _protect_model_solver_handles(worker: Any, model_tag: str, guard: ZeroSolverCallGuard) -> dict[str, Any]:
    model = worker.client().model(model_tag)
    studies = model.study()
    study_tags = sorted(str(item) for item in studies.tags())
    protected = []
    for tag in study_tags:
        node = studies.get(tag)
        guard.protect(node._handle, kind="study", tag=tag, java_type=node.java_type)
        protected.append({"kind": "study", "tag": tag, "handle": node._handle, "java_type": node.java_type})
    solvers = model.sol()
    solver_tags = sorted(str(item) for item in solvers.tags())
    for tag in solver_tags:
        node = solvers.get(tag)
        guard.protect(node._handle, kind="solver_sequence", tag=tag, java_type=node.java_type)
        protected.append({"kind": "solver_sequence", "tag": tag, "handle": node._handle, "java_type": node.java_type})
    if not study_tags or not solver_tags:
        raise RuntimeError(f"saved MPH lacks expected study/solver identity: studies={study_tags}, solvers={solver_tags}")
    return {"study_tags": study_tags, "solver_tags": solver_tags, "protected_execution_handles": protected}


def _db_job_statuses(daemon: Any) -> list[dict[str, str]]:
    rows = daemon.store.db.execute("SELECT job_id,status FROM jobs ORDER BY rowid").fetchall()
    return [{"job_id": str(row[0]), "status": str(row[1])} for row in rows]


def _db_operation_rows(daemon: Any) -> list[dict[str, Any]]:
    rows = daemon.store.db.execute(
        "SELECT j.job_id,j.status,o.operation,o.metadata FROM jobs j "
        "JOIN operations o USING(operation_id) ORDER BY j.rowid"
    ).fetchall()
    result = []
    for row in rows:
        metadata = json.loads(row["metadata"] or "{}")
        arguments = metadata.get("arguments", {}) if isinstance(metadata, dict) else {}
        result.append({
            "job_id": str(row["job_id"]),
            "status": str(row["status"]),
            "operation": str(row["operation"]),
            "inner_operation": arguments.get("operation_id") if isinstance(arguments, dict) else None,
        })
    return result


def _execution_revision(sample: dict[str, Any], daemon: Any, model_ref: dict[str, Any]) -> tuple[int, int]:
    execution = sample["response"].get("execution", {})
    public_revision = execution.get("revision")
    if isinstance(public_revision, bool) or not isinstance(public_revision, int):
        raise AssertionError(f"result.at_points response omitted integer execution.revision: {execution!r}")
    from comsol_mcp._execution_contract import model_ref_from_mapping
    ledger_revision = daemon.service.ledger._state_for(model_ref_from_mapping(model_ref)).revision
    if ledger_revision != public_revision:
        raise AssertionError(f"public result revision {public_revision} differs from current ledger revision {ledger_revision}")
    return public_revision, ledger_revision


def _connect_daemon(server: NativeLoopbackServer, home: Path) -> tuple[Any, dict[str, Any]]:
    from comsol_mcp._control_daemon import ControlDaemon

    daemon = ControlDaemon(home, worker=server.worker, project_root=server.project)
    connection = _dispatch(
        daemon,
        "server_connect",
        {"host": "127.0.0.1", "port": server.port},
        project_id=PROJECT_ID,
        idempotency_key=f"supplement-connect-{uuid4()}",
    )
    require_success(connection, "supplement managed server_connect")
    endpoint = connection.get("data", {}).get("endpoint")
    if endpoint != f"127.0.0.1:{server.port}":
        raise AssertionError(f"managed service connected to unexpected endpoint {endpoint!r}")
    return daemon, connection


def _reopen_and_sample(server: NativeLoopbackServer, *, worker: Any, worker_runtime: dict[str, Any],
                       phase: str, artifact: Path, expected_signature: str | None,
                       expected_tags: dict[str, list[str]] | None, evidence: Path,
                       event_path: Path, phase_states: list[dict[str, Any]]) -> dict[str, Any]:
    guard = ZeroSolverCallGuard(worker, event_path=event_path)
    phase_state: dict[str, Any] = {"phase": phase, "guard": guard, "nonterminal_jobs": None,
                                   "protected_handles": None, "operation_rows": None,
                                   "sample_revisions": []}
    phase_states.append(phase_state)
    daemon = None
    try:
        daemon, connection = _connect_daemon(server, server.work / f"control-{phase}")
        loaded_tag = f"supp{phase[:1]}_{uuid4().hex[:12]}"
        loaded = worker.client().load(str(artifact), tag=loaded_tag)
        loaded_tag = str(loaded.java.tag())
        binding = daemon.service.bind_model(loaded_tag, ownership="mcp_owned")
        daemon.backend.persist()
        model_ref = binding["execution"]["model_ref"]
        revision = int(binding["execution"]["revision"])
        readback = daemon.backend._resume_model_readback(worker.client().model(loaded_tag))
        state = readback.get("state", {})
        datasets = sorted(str(item) for item in state.get("datasets", []))
        solutions = sorted(str(item) for item in state.get("solutions", []))
        marker_rows = [row for row in state.get("parameters", []) if row.get("name") == "smoke_marker"]
        if len(marker_rows) != 1 or str(marker_rows[0].get("expression")) != MARKER:
            raise AssertionError(f"{phase} marker readback mismatch: {marker_rows!r}")
        if not datasets or not solutions:
            raise AssertionError(f"{phase} model has no saved dataset/solution tags: {state!r}")
        tag_identity = {"datasets": datasets, "solutions": solutions}
        if expected_tags is not None and tag_identity != expected_tags:
            raise AssertionError(f"{phase} saved solution/dataset identity changed: {tag_identity!r} != {expected_tags!r}")
        if expected_signature is not None and readback.get("signature") != expected_signature:
            raise AssertionError(f"{phase} model readback signature changed: {readback.get('signature')} != {expected_signature}")

        protected = _protect_model_solver_handles(worker, loaded_tag, guard)
        phase_state["protected_handles"] = protected
        append_event(event_path, "zero_solver_guard_armed", phase=phase, **protected)
        dataset = datasets[0]
        points = [[x, y] for x in POINT_AXIS for y in POINT_AXIS]
        real = _sample(daemon, model_ref, revision, dataset, points, "real", PROJECT_ID, f"{phase}-real")
        real_revision, real_ledger_revision = _execution_revision(real, daemon, model_ref)
        phase_state["sample_revisions"].append({"sample": "real", "expected_revision": revision,
                                                 "public_revision": real_revision,
                                                 "ledger_revision": real_ledger_revision})
        readback_after_real = daemon.backend._resume_model_readback(worker.client().model(loaded_tag))
        if readback_after_real.get("signature") != readback.get("signature"):
            raise AssertionError(f"{phase} model structure/parameter/solution readback changed after real sampling")
        imaginary = _sample(daemon, model_ref, real_revision, dataset, points, "imag", PROJECT_ID, f"{phase}-imag")
        imaginary_revision, imaginary_ledger_revision = _execution_revision(imaginary, daemon, model_ref)
        phase_state["sample_revisions"].append({"sample": "imaginary", "expected_revision": real_revision,
                                                  "public_revision": imaginary_revision,
                                                  "ledger_revision": imaginary_ledger_revision})
        readback_after_imaginary = daemon.backend._resume_model_readback(worker.client().model(loaded_tag))
        if readback_after_imaginary.get("signature") != readback.get("signature"):
            raise AssertionError(f"{phase} model structure/parameter/solution readback changed after imaginary sampling")
        real_values = real["values"]
        imag_values = imaginary["values"]
        if len(real_values) != 9 or len(imag_values) != 9:
            raise AssertionError(f"{phase} must read exactly nine real and imaginary points")
        if not all(math.isfinite(value) for value in real_values + imag_values):
            raise AssertionError(f"{phase} point values must all be finite")
        real_errors = [abs(value - 1.0) for value in real_values]
        imag_errors = [abs(value) for value in imag_values]
        if max(real_errors) > REAL_TOL:
            raise AssertionError(f"{phase} real error exceeds frozen tolerance: {max(real_errors)}")
        if max(imag_errors) > IMAG_TOL:
            raise AssertionError(f"{phase} imaginary error exceeds frozen tolerance: {max(imag_errors)}")
        for label, sample in (("real", real), ("imaginary", imaginary)):
            data = sample["response"].get("data", {})
            if data.get("dataset") != dataset:
                raise AssertionError(f"{phase} {label} result dataset readback mismatch: {data.get('dataset')!r}")
            if data.get("solution") not in solutions:
                raise AssertionError(f"{phase} {label} result solution not in saved tags: {data.get('solution')!r}")
            feature_binding = data.get("dataset_binding")
            if isinstance(feature_binding, Mapping) and feature_binding.get("solution") not in {None, *solutions}:
                raise AssertionError(f"{phase} {label} dataset binding does not map to saved solution: {feature_binding!r}")
        requests = {
            "requests": guard.observed_requests,
            "method_run_events": guard.method_run_events,
            "blocked_attempts": guard.blocked_attempts,
        }
        if not guard.safe_to_stop():
            raise RuntimeError(f"{phase} Worker has unknown calls; preserve owned process: {requests!r}")
        # Point interpolation uses NumericalFeature.run. It is allowed and is
        # distinct from all protected Study/SolverSequence execution handles.
        unexpected = [row for row in guard.method_run_events if row.get("protected_node") is not None]
        if unexpected:
            raise AssertionError(f"{phase} attempted a protected study/solver run: {unexpected!r}")
        jobs = _db_job_statuses(daemon)
        nonterminal = [row for row in jobs if row["status"] not in TERMINAL]
        phase_state["nonterminal_jobs"] = nonterminal
        if nonterminal:
            raise RuntimeError(f"{phase} control store still has nonterminal work; preserve process: {nonterminal!r}")
        operations = _db_operation_rows(daemon)
        phase_state["operation_rows"] = operations
        unexpected_operations = [row for row in operations if not (
            row["operation"] == "server_connect"
            or (row["operation"] == "operation_call" and row["inner_operation"] == "result.at_points")
        )]
        if unexpected_operations:
            raise AssertionError(f"{phase} dispatched an operation outside server_connect/result.at_points: {unexpected_operations!r}")
        if any(row.get("protected_node") is not None for row in guard.method_run_events):
            raise AssertionError(f"{phase} attempted study/solver execution on a protected Java handle")
        guard.remove()
        receipt = {
            "status": "PASS_READ_ONLY_SAVED_MPH_REOPEN",
            "phase": phase,
            "saved_artifact_path": str(artifact),
            "worker_runtime": worker_runtime,
            "managed_connection": connection,
            "loaded_model_tag": loaded_tag,
            "model_ref": model_ref,
            "revision": revision,
            "readback": readback,
            "readback_after_real_sample": readback_after_real,
            "readback_after_imaginary_sample": readback_after_imaginary,
            "marker_readback": marker_rows,
            "solution_dataset_identity": tag_identity,
            "protected_study_solver_handles": protected,
            "control_operations": operations,
            "sampling_revision_chain": phase_state["sample_revisions"],
            "points_m": points,
            "real_values": real_values,
            "imaginary_values": imag_values,
            "max_abs_real_minus_one": max(real_errors),
            "max_abs_imaginary": max(imag_errors),
            "real_tolerance": REAL_TOL,
            "imaginary_tolerance": IMAG_TOL,
            "guard": requests,
            "nonterminal_jobs": nonterminal,
        }
        write_json(evidence / f"{phase}_readback.json", receipt)
        return receipt
    finally:
        if daemon is not None:
            try:
                phase_state["nonterminal_jobs"] = [
                    row for row in _db_job_statuses(daemon) if row["status"] not in TERMINAL
                ]
                phase_state["operation_rows"] = _db_operation_rows(daemon)
            except Exception as exc:
                phase_state["nonterminal_jobs"] = [{"status": "UNKNOWN", "error": str(exc)}]
            try:
                daemon.close()
            except Exception:
                pass
        guard.remove()


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    evidence = Path(args.evidence).resolve()
    work = Path(args.work).resolve()
    artifact = Path(args.saved_model).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    events = evidence / "events.jsonl"
    receipt = json.loads(RUN08_RECEIPT.read_text(encoding="utf-8"))
    expected_hash = args.expected_sha256 or receipt["saved_sha256"]
    expected_path = Path(receipt["saved_path"]).resolve()
    if artifact != expected_path or str(expected_hash) != receipt.get("saved_sha256"):
        raise RuntimeError("read-only supplement input path/hash must match the immutable run08 immediate-save receipt")
    before_hash = sha256(artifact)
    if before_hash != expected_hash:
        raise RuntimeError(f"saved artifact integrity mismatch: {before_hash} != {expected_hash}")
    if not INSTALL_ROOT.is_dir() or not JAVA11.is_dir():
        raise RuntimeError("the pre-inventoried COMSOL 6.4 or Java 11 runtime is unavailable")
    processes = _process_inventory()
    if processes["exit_code"] != 0:
        raise RuntimeError(f"cannot verify process inventory before private server start: {processes!r}")
    # Existing Python MCP/KB services are intentionally outside this filter and
    # are never signaled or adopted.
    if processes["active_comsol_or_java_engine_seen"]:
        raise RuntimeError(f"an existing COMSOL/Java process needs ownership review before starting: {processes!r}")

    supplement_id = f"saved_mph_readback_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:6]}"
    progress = {
        "supplement_id": supplement_id,
        "status": "PREPARED_ZERO_SOLVE_READBACK",
        "evidence_dir": str(evidence),
        "work_dir": str(work),
        "source_run_id": receipt.get("run_id"),
        "source_run_status": "FAIL_OR_INCOMPLETE",
        "saved_artifact_sha256": expected_hash,
        "solve_calls_allowed": 0,
        "study_solver_methods_blocked": sorted(SOLVER_EXECUTION_METHODS),
        "active_jobs": [],
    }
    write_json(evidence / "supplement_start.json", {
        "supplement": progress,
        "at_utc": utc_now(),
        "process_inventory": processes,
        "source_hashes": {
            name: sha256(path)
            for name, path in {
                "runner": REPO / "tools/run_native_resume_smoke.py",
                "managed_backend": REPO / "comsol_mcp/_managed_backend.py",
                "control_daemon": REPO / "comsol_mcp/_control_daemon.py",
                "java_worker": REPO / "comsol_mcp/_java_worker.py",
                "result_adapter": REPO / "comsol_mcp/_g3_results.py",
            }.items()
        },
        "inputs": {"artifact_path": str(artifact), "sha256_before": before_hash, "size_bytes": artifact.stat().st_size},
        "original_wall_clock_budget_applies": False,
        "native_solver_calls_allowed": 0,
    })
    write_resume_progress(progress, active=True)
    server: NativeLoopbackServer | None = None
    daemon: Any = None
    safe_to_stop = False
    workers: list[dict[str, Any]] = []
    phase_states: list[dict[str, Any]] = []
    try:
        server = NativeLoopbackServer(work, evidence, event_log=events)
        shadow = server.prepare_shadow()
        listener = server.start_and_verify_listener()
        progress.update({"status": "PRIVATE_LOOPBACK_SERVER_VERIFIED", "server_pid": listener["pid"],
                         "port": listener["port"], "listener_verified": True})
        write_resume_progress(progress, active=True)
        first_runtime = server.start_worker()
        if not first_runtime.get("engine_version_identity", {}).get("matches_frozen_target"):
            raise RuntimeError(f"first Worker runtime does not match frozen COMSOL 6.4.0.293: {first_runtime.get('engine_version_identity')!r}")
        progress.update({"status": "WORKER_A_CONNECTED", "worker_a_pid": first_runtime["worker_runtime"].get("pid"),
                         "engine_version_identity": first_runtime.get("engine_version_identity")})
        write_resume_progress(progress, active=True)
        os.environ["COMSOL_ROOT"] = str(server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(JAVA11)
        os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
        os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)
        first = _reopen_and_sample(server, worker=server.worker,
                                   worker_runtime=first_runtime["worker_runtime"], phase="worker_a",
                                   artifact=artifact, expected_signature=None, expected_tags=None,
                                   evidence=evidence, event_path=events, phase_states=phase_states)
        workers.append({"phase": "worker_a", "runtime": first_runtime["worker_runtime"]})
        progress.update({"status": "WORKER_A_SAVED_MPH_READBACK_COMPLETE",
                         "worker_a_readback_signature": first["readback"]["signature"],
                         "solution_dataset_identity": first["solution_dataset_identity"]})
        write_resume_progress(progress, active=True)

        old_pid = first_runtime["worker_runtime"].get("pid")
        server.close_worker()
        worker_started = False
        paths = server.paths_type(server.shadow_root, JAVA11, private_prefs=server.prefs,
                                  project_root=server.project)
        second_worker = server.worker_type(paths, state_dir=work / "worker-b-state")
        server.worker = second_worker
        second_worker.start(startup_timeout_s=25.0)
        second_worker.client().connect(server.port, "127.0.0.1")
        second_runtime = second_worker.runtime_metadata()
        if not isinstance(second_runtime.get("pid"), int) or second_runtime.get("pid") == old_pid:
            raise RuntimeError(f"second Java Worker did not have a distinct PID: old={old_pid}, new={second_runtime!r}")
        from comsol_mcp._g2_isolation import verify_owned_server
        second_isolation = verify_owned_server(server.receipt_path,
                                               endpoint=f"127.0.0.1:{server.port}",
                                               worker_pid=second_runtime.get("pid"))
        if not second_isolation.get("verified"):
            raise RuntimeError(f"second Worker does not match private server ownership receipt: {second_isolation!r}")
        second_version = str(second_worker.client().getComsolVersion())
        second_identity = _engine_build_identity(second_version)
        if not second_identity.get("matches_frozen_target"):
            raise RuntimeError(f"second Worker runtime does not match frozen COMSOL 6.4.0.293: {second_identity!r}")
        progress.update({"status": "WORKER_B_CONNECTED", "worker_b_pid": second_runtime.get("pid"),
                         "engine_version_identity": second_identity})
        write_resume_progress(progress, active=True)
        second = _reopen_and_sample(server, worker=second_worker,
                                    worker_runtime={**second_runtime, "engine_version": second_version,
                                                   "engine_version_identity": second_identity,
                                                   "server_pid": server.proc.pid,
                                                   "server_endpoint": f"127.0.0.1:{server.port}",
                                                   "isolation_proof": second_isolation},
                                    phase="worker_b", artifact=artifact,
                                    expected_signature=first["readback"]["signature"],
                                    expected_tags=first["solution_dataset_identity"],
                                    evidence=evidence, event_path=events, phase_states=phase_states)
        workers.append({"phase": "worker_b", "runtime": second_runtime})
        progress.update({"status": "WORKER_B_SAVED_MPH_READBACK_COMPLETE",
                         "worker_b_readback_signature": second["readback"]["signature"]})
        write_resume_progress(progress, active=True)
        if second["readback"]["signature"] != first["readback"]["signature"]:
            raise AssertionError("distinct Worker reopen changed the saved model identity signature")
        if second["solution_dataset_identity"] != first["solution_dataset_identity"]:
            raise AssertionError("distinct Worker reopen changed the saved solution/dataset identity")
        real_deltas = [abs(a - b) for a, b in zip(first["real_values"], second["real_values"])]
        imag_deltas = [abs(a - b) for a, b in zip(first["imaginary_values"], second["imaginary_values"])]
        if max(real_deltas) > REOPEN_TOL or max(imag_deltas) > REOPEN_TOL:
            raise AssertionError(f"distinct Worker results differ beyond frozen {REOPEN_TOL}: {real_deltas}, {imag_deltas}")
        final_hash = sha256(artifact)
        if final_hash != expected_hash:
            raise AssertionError(f"read-only verification changed saved artifact bytes: {final_hash} != {expected_hash}")
        safe_to_stop = True
        result = {
            "status": "PASS_READ_ONLY_SAVED_MPH_REOPEN_ZERO_SOLVES",
            "supplement_id": supplement_id,
            "source_run_id": receipt["run_id"],
            "source_run_status_preserved": "FAIL_OR_INCOMPLETE",
            "started_at_utc": json.loads((evidence / "supplement_start.json").read_text(encoding="utf-8"))["at_utc"],
            "completed_at_utc": utc_now(),
            "elapsed_s_outside_original_180s_budget": round(time.monotonic() - started, 3),
            "native_solver_calls_allowed": 0,
            "native_solver_calls_observed": 0,
            "solver_guard": {
                "protected_methods": sorted(SOLVER_EXECUTION_METHODS),
                "protected_study_and_solver_node_handles": True,
                "study_or_solver_execution_attempts": 0,
                "no_study_run_operation_dispatched": True,
                "sample_interpolator_runs_are_not_solver_calls": True,
            },
            "original_second_and_final_solve_budget_consumed": 2,
            "server": {"pid": server.proc.pid, "port": server.port,
                       "endpoint": f"127.0.0.1:{server.port}", "listener": listener,
                       "private_shadow": shadow},
            "workers": workers,
            "worker_a": first,
            "worker_b": second,
            "artifact": {"path": str(artifact), "sha256_before": before_hash,
                         "sha256_after": final_hash, "size_bytes": artifact.stat().st_size,
                         "source_receipt": str(RUN08_RECEIPT)},
            "cross_worker_max_abs_real_delta": max(real_deltas),
            "cross_worker_max_abs_imaginary_delta": max(imag_deltas),
            "cross_worker_tolerance": REOPEN_TOL,
            "scope": "saved model, marker, solution/dataset tag identity, and nine-point native result readback only; no new solve or W23/W24 scientific claim",
        }
        write_json(evidence / "supplement_summary.json", result)
        progress.update({"status": result["status"], "completed_at_utc": result["completed_at_utc"],
                         "elapsed_s": result["elapsed_s_outside_original_180s_budget"],
                         "worker_pids": [row["runtime"].get("pid") for row in workers],
                         "artifact_sha256_after": final_hash, "solve_calls_observed": 0,
                         "summary": str(evidence / "supplement_summary.json")})
        write_resume_progress(progress, active=False)
        return result
    except BaseException as exc:
        safe_to_stop = bool(phase_states) and all(
            state["guard"].safe_to_stop()
            and isinstance(state.get("nonterminal_jobs"), list)
            and not state["nonterminal_jobs"]
            for state in phase_states
        )
        write_json(evidence / "supplement_failure.json", {
            "status": "FAIL_OR_INCOMPLETE_READ_ONLY_SUPPLEMENT",
            "at_utc": utc_now(), "error_type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "elapsed_s": round(time.monotonic() - started, 3),
            "artifact_sha256_after": sha256(artifact) if artifact.is_file() else None,
            "workers": workers,
            "solver_calls_allowed": 0,
            "worker_guards": [{
                "phase": state["phase"],
                "observed_requests": state["guard"].observed_requests,
                "method_run_events": state["guard"].method_run_events,
                "blocked_attempts": state["guard"].blocked_attempts,
                "protected_handles": state.get("protected_handles"),
                "operation_rows": state.get("operation_rows"),
                "sample_revision_chain": state.get("sample_revisions"),
                "nonterminal_jobs": state.get("nonterminal_jobs"),
            } for state in phase_states],
            "owned_processes_preserved_if_state_unknown": not safe_to_stop,
        })
        progress.update({"status": "FAIL_OR_INCOMPLETE_READ_ONLY_SUPPLEMENT",
                         "last_error": {"type": type(exc).__name__, "message": str(exc)},
                         "elapsed_s": round(time.monotonic() - started, 3)})
        write_resume_progress(progress, active=not safe_to_stop)
        raise
    finally:
        if server is not None and safe_to_stop:
            try:
                server.close_worker()
            except Exception:
                pass
            stop = server.stop_server(allow_stop=True)
            write_json(evidence / "owned_process_cleanup.json", {
                "status": "TASK_OWNED_WORKER_CLOSED_AND_SERVER_STOPPED",
                "worker_close_requested": True,
                "server_stop": stop,
                "server_pid": server.proc.pid if server.proc else None,
                "port": server.port,
            })
        elif server is not None:
            write_json(evidence / "owned_process_preserved.json", {
                "status": "PRESERVED_FOR_RECONCILIATION",
                "server_pid": server.proc.pid if server.proc else None,
                "server_return_code": server.proc.poll() if server.proc else None,
                "port": server.port,
                "worker_state_dir": str(server.worker.state_dir) if server.worker else None,
                "worker_process_pid": (server.worker._process.pid
                                       if server.worker and server.worker._process else None),
                "reason": "no terminal-and-idle proof was available; no owned process was signaled",
            })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--saved-model", default=str(json.loads(RUN08_RECEIPT.read_text(encoding="utf-8"))["saved_path"]))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--work", required=True)
    parser.add_argument("--evidence", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), indent=2, ensure_ascii=False, default=json_default))
