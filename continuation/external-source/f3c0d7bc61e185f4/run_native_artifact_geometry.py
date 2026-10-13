#!/usr/bin/env python3
"""Run the frozen zero-solver COMSOL geometry artifact probe on a private loopback server."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
FIXTURE_SOURCE = REPO / "tools/java/NativeArtifactGeometryFixture.java"
FREEZE_DOC = REPO / "docs/full_project_execution/evidence/luna_f10_artifact_import/native_geometry/F10_RECOVERY_FIXTURE_FREEZE.md"
MAX_WALL_S = 1200.0
CLEANUP_RESERVE_S = 20.0
PROJECT_ID = "native-artifact-geometry-probe"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}

SOURCE_PATHS = {
    "native_geometry_runner": REPO / "tools/run_native_artifact_geometry.py",
    "java_fixture": FIXTURE_SOURCE,
    "fixture_freeze_document": FREEZE_DOC,
    "owned_server_helper": REPO / "tools/run_native_resume_smoke.py",
    "operation_store": REPO / "comsol_mcp/_operation_store.py",
    "artifact_store": REPO / "comsol_mcp/_artifact_store.py",
    "control_daemon": REPO / "comsol_mcp/_control_daemon.py",
    "managed_backend": REPO / "comsol_mcp/_managed_backend.py",
    "execution_service": REPO / "comsol_mcp/_execution_service.py",
    "domain_outcome": REPO / "comsol_mcp/_domain_outcome.py",
    "g3_w14_geometry": REPO / "comsol_mcp/_g3_w14.py",
    "g3_dispatch": REPO / "comsol_mcp/_g3_ops.py",
    "g2_registry": REPO / "comsol_mcp/_g2_registry.py",
    "g2_isolation": REPO / "comsol_mcp/_g2_isolation.py",
    "java_worker": REPO / "comsol_mcp/_java_worker.py",
    "java_worker_source": REPO / "comsol_mcp/worker_java/PersistentComsolWorker.java",
    "g2_code": REPO / "comsol_mcp/_g2_code.py",
    "g2_engine": REPO / "comsol_mcp/_g2_engine.py",
    "g2_contract": REPO / "comsol_mcp/_g2_contract.py",
    "action_catalog": REPO / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mac_birth_epoch(birth: str) -> float:
    parsed = datetime.strptime(birth, "%a %b %d %H:%M:%S %Y")
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _append_event(path: Path, name: str, **details: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at_monotonic_s": time.monotonic(), "event": name, **details},
                                ensure_ascii=False, default=str, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _require_success(response: dict[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise RuntimeError(f"{label} failed: {json.dumps(response, ensure_ascii=False, default=str)[:7000]}")
    return response


def _java_action_readback(response: dict[str, Any], label: str) -> dict[str, Any]:
    """Unwrap the actual code.execute_java -> Worker result nesting fail-closed."""
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError(f"{label} response is not successful")
    data = response.get("data")
    if not isinstance(data, dict):
        raise AssertionError(f"{label} response is missing data object")
    worker = data.get("worker")
    if not isinstance(worker, dict) or worker.get("ok") is not True or worker.get("status") != "SUCCEEDED":
        raise AssertionError(f"{label} response is missing successful Worker receipt")
    wrapped = data.get("readback")
    if not isinstance(wrapped, dict) or wrapped.get("executed") is not True:
        raise AssertionError(f"{label} response is missing the Worker execution wrapper")
    readback = wrapped.get("readback")
    if not isinstance(readback, dict):
        raise AssertionError(f"{label} response is missing nested Java fixture readback")
    worker_result = worker.get("result")
    if not isinstance(worker_result, dict) or worker_result.get("readback") != readback:
        raise AssertionError(f"{label} Worker result and managed readback layers disagree")
    return readback


def _execution_identity_arguments(operation: str, body: dict[str, Any], idempotency_key: str,
                                  request_id: str) -> dict[str, Any]:
    """Keep artifact.register's body identity in lockstep with the execution envelope."""
    if operation != "registry_call" or body.get("operation_id") != "artifact.register":
        return body
    nested = body.get("arguments")
    if not isinstance(nested, dict):
        raise ValueError("artifact.register requires an arguments object")
    return {**body, "arguments": {**nested, "idempotency_key": idempotency_key, "request_id": request_id}}


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", project_id):
        raise ValueError("--project-id must be 1-63 lowercase letters, digits, or hyphens, starting with a letter or digit")
    return project_id


def _registration_reuse_roots(registration_receipt: dict[str, Any], *,
                              allowed_task_prefix: str = "/private/tmp/comsol-mcp-native-artifact-geometry-") -> tuple[Path, Path, Path]:
    """Resolve a previously registered project and its existing OperationStore.

    Reuse runs have a new, unique task work directory for the private server
    and evidence. Their original project and OperationStore remain at the
    paths frozen in the successful registration receipt.
    """
    project_value = registration_receipt.get("new_project_root")
    store_value = registration_receipt.get("new_operation_store_path")
    if not isinstance(project_value, str) or not isinstance(store_value, str):
        raise ValueError("registration receipt must identify its original project root and OperationStore path")
    project_input = Path(project_value)
    store_input = Path(store_value)
    if not project_input.is_absolute() or not store_input.is_absolute():
        raise ValueError("registration reuse project and OperationStore paths must be absolute")
    try:
        project_root = project_input.resolve(strict=True)
        store_path = store_input.resolve(strict=True)
    except OSError as exc:
        raise ValueError("registered task project or OperationStore is no longer available") from exc
    if project_input.is_symlink() or store_input.is_symlink() or project_root != project_input or store_path != store_input:
        raise ValueError("registration reuse paths must not traverse symlinks")
    task_root = project_root.parent
    prefix = Path(allowed_task_prefix)
    if (project_root.name != "project" or not task_root.is_relative_to(prefix.parent)
            or not task_root.name.startswith(prefix.name)):
        raise ValueError("registered project root is outside the task-owned artifact geometry workspace")
    expected_store = task_root / "control" / "operations.sqlite3"
    if store_path != expected_store or not project_root.is_dir() or not store_path.is_file():
        raise ValueError("registration receipt project and OperationStore roots are inconsistent or missing")
    if store_path.is_symlink() or project_root.is_symlink():
        raise ValueError("registration reuse project and OperationStore must be regular task-owned paths")
    return task_root, project_root, store_path


def _verify_registration_reuse(registration_receipt: dict[str, Any], *,
                               allowed_task_prefix: str = "/private/tmp/comsol-mcp-native-artifact-geometry-") -> dict[str, Any]:
    """Read-only verify a content-addressed artifact against its durable Store row."""
    from comsol_mcp._artifact_store import local_artifact_host_identity, project_root_identity

    task_root, project_root, store_path = _registration_reuse_roots(
        registration_receipt, allowed_task_prefix=allowed_task_prefix
    )
    project_id = registration_receipt.get("project_id")
    artifact_id = registration_receipt.get("artifact_id")
    registration = registration_receipt.get("registration")
    expected_record = registration_receipt.get("durable_store_record")
    if (not isinstance(project_id, str) or not isinstance(artifact_id, str)
            or not isinstance(registration, dict) or not isinstance(expected_record, dict)
            or registration.get("artifact_id") != artifact_id
            or registration.get("sha256") != artifact_id
            or registration.get("role") != "geometry_source"
            or registration.get("classification") != "internal"):
        raise ValueError("registration receipt does not identify one internal geometry-source artifact")
    if (registration_receipt.get("new_registration_count") != 1
            or expected_record.get("schema_version") != 2
            or expected_record.get("project_id") != project_id
            or expected_record.get("artifact_id") != artifact_id
            or expected_record.get("sha256") != artifact_id
            or expected_record.get("role") != "geometry_source"
            or expected_record.get("classification") != "internal"):
        raise ValueError("registration receipt's durable row has a mismatched project or artifact identity")

    relative = registration.get("path")
    if not isinstance(relative, str):
        raise ValueError("registered artifact path is missing")
    portable = PurePosixPath(relative)
    if (portable.is_absolute() or portable.as_posix() != relative
            or any(part in {"", ".", ".."} for part in portable.parts)
            or portable.parts[:2] != ("g2_artifacts", "registered")):
        raise ValueError("registered artifact path is not canonical and project-contained")
    managed_path = project_root.joinpath(*portable.parts)
    if managed_path.is_symlink() or not managed_path.is_file():
        raise ValueError("registered managed artifact is not an existing regular file")
    managed_digest = _sha256(managed_path)
    managed_stat = managed_path.stat()
    file_identity = {
        "device": managed_stat.st_dev,
        "inode": managed_stat.st_ino,
        "mtime_ns": managed_stat.st_mtime_ns,
        "size": managed_stat.st_size,
    }
    if (managed_digest != artifact_id or managed_stat.st_size != registration.get("size")
            or expected_record.get("file_identity") != file_identity):
        raise ValueError("registered managed artifact hash or filesystem identity differs from its Store record")

    source = registration_receipt.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration receipt has no project input source identity")
    source_relative = source.get("relative_path")
    if not isinstance(source_relative, str):
        raise ValueError("registration receipt project input path is missing")
    source_portable = PurePosixPath(source_relative)
    if (source_portable.is_absolute() or source_portable.as_posix() != source_relative
            or any(part in {"", ".", ".."} for part in source_portable.parts)):
        raise ValueError("registration receipt project input path is not canonical and project-contained")
    source_path = project_root.joinpath(*source_portable.parts)
    if (source_path.is_symlink() or not source_path.is_file()
            or source.get("path") != str(source_path)):
        raise ValueError("registered project input source is not an existing regular file")
    source_digest = _sha256(source_path)
    if (source_digest != artifact_id or source.get("sha256") != artifact_id
            or source_path.stat().st_size != source.get("size_bytes")):
        raise ValueError("project input source and registered artifact no longer have the same bytes")

    current_host_identity = local_artifact_host_identity()
    current_root_identity = project_root_identity(project_root)
    if (expected_record.get("project_root_identity") != current_root_identity
            or expected_record.get("host_identity") != current_host_identity
            or expected_record.get("engine_host_identity") != current_host_identity):
        raise ValueError("registered Store row belongs to a different project root or host")

    sidecars = [Path(f"{store_path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]
    existing_sidecars = [str(path) for path in sidecars if path.exists()]
    if existing_sidecars:
        raise ValueError(f"OperationStore has uncheckpointed SQLite sidecars; refusing immutable reuse read: {existing_sidecars!r}")
    try:
        connection = sqlite3.connect(store_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            row = connection.execute(
                "SELECT metadata FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("existing OperationStore could not be verified read-only") from exc
    if row is None:
        raise ValueError("registered artifact is absent from the existing OperationStore")
    try:
        durable_record = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("existing OperationStore artifact metadata is not valid JSON") from exc
    if durable_record != expected_record:
        raise ValueError("existing OperationStore artifact row differs from the successful registration receipt")

    return {
        "status": "VERIFIED_EXISTING_PROJECT_STORE_ARTIFACT_READ_ONLY",
        "project_id": project_id,
        "artifact_id": artifact_id,
        "task_root": str(task_root),
        "project_root": str(project_root),
        "project_root_identity": current_root_identity,
        "operation_store_path": str(store_path),
        "operation_store_sha256": _sha256(store_path),
        "store_read_mode": "sqlite mode=ro immutable=1; no WAL/SHM/journal sidecars present",
        "managed_copy": {"path": str(managed_path), "size_bytes": managed_stat.st_size,
                         "sha256": managed_digest, "file_identity": file_identity},
        "project_input": {"path": str(source_path), "size_bytes": source_path.stat().st_size,
                          "sha256": source_digest},
        "durable_store_record": durable_record,
        "owner_identity": {"host_identity": current_host_identity,
                           "engine_host_identity": expected_record.get("engine_host_identity")},
    }


def _bounded_rpc_timeout(requested_s: float, deadline_unix: float, *, now_unix: float,
                         cleanup_reserve_s: float = CLEANUP_RESERVE_S) -> float:
    """Keep an RPC timeout from consuming the time reserved for owned cleanup."""
    available_s = deadline_unix - now_unix - cleanup_reserve_s
    if available_s <= 0:
        raise TimeoutError("native engine wall budget reached the reserved cleanup window")
    return min(requested_s, available_s)


def _action_count_receipt(*, native_export_attempts: int = 0, native_export_successes: int = 0,
                          registration_attempts: int = 0, registration_successes: int = 0) -> dict[str, int]:
    values = (native_export_attempts, native_export_successes, registration_attempts, registration_successes)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("native action counters must be nonnegative integers")
    if native_export_successes > native_export_attempts or registration_successes > registration_attempts:
        raise ValueError("successful native action responses cannot exceed attempted dispatches")
    return {
        "native_export_attempt_count_this_round": native_export_attempts,
        "native_export_count_this_round": native_export_successes,
        "artifact_registration_attempt_count_this_round": registration_attempts,
        "artifact_registration_count_this_round": registration_successes,
    }


def _operation_call(daemon: Any, operation_id: str, arguments: dict[str, Any], *, ref: dict[str, Any] | None,
                    revision: int | None, label: str, project_id: str = PROJECT_ID,
                    rpc_timeout_s: float = 180.0) -> dict[str, Any]:
    from tools.run_native_resume_smoke import _dispatch

    execution = {"operation_id": operation_id, "arguments": arguments}
    return _dispatch(
        daemon,
        "operation_call",
        execution,
        project_id=project_id,
        ref=ref,
        revision=revision,
        idempotency_key=f"native-geometry-{label}-{uuid4()}",
        request_id=f"native-geometry-{label}-{uuid4()}",
        rpc_timeout_s=rpc_timeout_s,
    )


def _response_revision(response: dict[str, Any], daemon: Any, ref: dict[str, Any]) -> int:
    from comsol_mcp._execution_contract import model_ref_from_mapping

    returned = response.get("execution", {}).get("revision")
    if isinstance(returned, bool) or not isinstance(returned, int):
        raise AssertionError(f"operation response did not return an integer revision: {response.get('execution')!r}")
    ledger = daemon.service.ledger._state_for(model_ref_from_mapping(ref)).revision
    if returned != ledger:
        raise AssertionError(f"response revision {returned} differs from durable ledger revision {ledger}")
    return returned


def _geometry_path() -> dict[str, Any]:
    return {"segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "geom", "tag": "geom1"},
    ]}


def _process_inventory() -> dict[str, Any]:
    proc = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,comm=,args="], capture_output=True,
                          text=True, check=False, timeout=10)
    rows = []
    related_python_services = []
    for line in proc.stdout.splitlines():
        lower = line.lower()
        fields = line.strip().split(None, 3)
        if len(fields) < 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, ppid, _comm, argv_text = fields
        argv = argv_text.split()
        executable = Path(argv[0]).name.lower() if argv else ""
        is_java = executable in {"java", "java.exe"} or executable.startswith("java-")
        is_python = "python" in executable
        # COMSOL's Java server is recognized by its installed Java runtime or
        # a COMSOL classpath/argument; the long-lived Python MCP and KB services
        # are inventoried separately and never treated as native engines.
        comsol_engine = (
            "mphserver" in lower
            or ("/applications/comsol64/multiphysics/" in lower and (is_java or executable == "comsol"))
            or (is_java and "comsol" in lower)
        )
        if comsol_engine and "run_native_artifact_geometry.py" not in lower:
            rows.append({"pid": int(pid), "ppid": int(ppid), "executable": executable, "argv": argv_text.strip()})
        elif is_python and ("comsol-mcp-server" in lower or "comsol-6.4-kb" in lower):
            related_python_services.append({"pid": int(pid), "ppid": int(ppid), "executable": executable,
                                            "argv": argv_text.strip(), "classification": "unrelated_python_mcp_or_kb_service"})
    listeners = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                               capture_output=True, text=True, check=False, timeout=10)
    comsol_listeners = [line.strip() for line in listeners.stdout.splitlines()[1:]
                        if "mphserver" in line.lower() or "comsol" in line.lower()]
    return {
        "ps_exit_code": proc.returncode,
        "preexisting_comsol_engine_processes": rows,
        "unrelated_python_service_processes": related_python_services,
        "lsof_exit_code": listeners.returncode,
        "preexisting_comsol_listener_rows": comsol_listeners,
        "server_start_allowed": proc.returncode == 0 and listeners.returncode == 0 and not rows and not comsol_listeners,
    }


def _source_hashes() -> dict[str, dict[str, str]]:
    missing = [str(path) for path in SOURCE_PATHS.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("frozen native probe inputs missing: " + ", ".join(missing))
    return {name: {"path": str(path), "sha256": _sha256(path)} for name, path in SOURCE_PATHS.items()}


def _worker_compilation_identity(server: Any) -> dict[str, Any]:
    worker = getattr(server, "worker", None)
    classes_dir = getattr(worker, "_classes_dir", None)
    if not isinstance(classes_dir, Path) or not classes_dir.is_dir():
        raise RuntimeError("Worker startup did not expose its task-private compiled cache directory")
    marker = classes_dir / ".compiled"
    cache_receipt_path = classes_dir / "cache_receipt.json"
    if not marker.is_file() or not cache_receipt_path.is_file():
        raise RuntimeError("Worker compiled-cache marker or identity receipt is missing")
    cache_receipt = json.loads(cache_receipt_path.read_text(encoding="utf-8"))
    _classpath, classpath_sha256, jar_count, jar_content_sha256 = worker.paths.classpath()
    source_path = SOURCE_PATHS["java_worker_source"]
    source_sha256 = _sha256(source_path)
    if cache_receipt.get("source_sha256") != source_sha256:
        raise RuntimeError("compiled Worker source hash differs from the frozen repository source")
    class_files = sorted(path for path in classes_dir.rglob("*") if path.is_file() and path.suffix == ".class")
    class_manifest = [{"path": path.relative_to(classes_dir).as_posix(), "sha256": _sha256(path)}
                      for path in class_files]
    marker_value = marker.read_text(encoding="ascii").strip()
    if marker_value != classes_dir.name or not class_manifest:
        raise RuntimeError("compiled Worker cache marker is inconsistent or has no compiled classes")
    return {
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "cache_key": classes_dir.name,
        "compiled_marker": marker_value,
        "cache_receipt": cache_receipt,
        "classpath_sha256": classpath_sha256,
        "jar_count": jar_count,
        "jar_content_sha256": jar_content_sha256,
        "compiled_class_count": len(class_manifest),
        "compiled_class_manifest_sha256": hashlib.sha256(
            json.dumps(class_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _refresh_resume_state(evidence: Path, status: str, active_jobs: list[dict[str, Any]]) -> None:
    path = REPO / "docs/full_project_execution/state/RESUME.json"
    if not path.is_file():
        return
    state = json.loads(path.read_text(encoding="utf-8"))
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["active_jobs"] = active_jobs
    state["native_geometry_probe"] = {
        "status": status,
        "evidence_dir": str(evidence),
        "updated_at": state["updated_at"],
        "scope": "COMSOL 6.4 artifact.register to native MPHTXT geometry import/build/readback; zero study/solver calls",
    }
    _write_json(path, state)


def _idle_proof(daemon: Any | None, project_id: str) -> dict[str, Any]:
    if daemon is None:
        return {"idle": True, "reason": "no_control_daemon_or_engine_operation_started", "jobs": []}
    jobs = daemon.store.list_jobs(offset=0, limit=1000, project_id=project_id)
    if jobs.total > len(jobs):
        return {"idle": False, "reason": "job_inventory_truncated", "total": jobs.total, "jobs": list(jobs)}
    request_evidence = []
    bad = []
    for job in jobs:
        if job.get("status") not in TERMINAL:
            bad.append({"job_id": job.get("job_id"), "status": job.get("status")})
        submitted: set[str] = set()
        observed: dict[str, str] = {}
        for event in daemon.store.events(job["job_id"], offset=0, limit=1000):
            meta = event.get("metadata") or {}
            request_id = meta.get("request_id")
            if event.get("event") == "worker_request" and request_id:
                if meta.get("phase") == "submitted":
                    submitted.add(request_id)
                elif meta.get("phase") == "observed":
                    observed[request_id] = meta.get("status")
        for request_id in sorted(submitted):
            status = observed.get(request_id)
            request_evidence.append({"job_id": job.get("job_id"), "request_id": request_id, "observed_status": status})
            if status not in {"SUCCEEDED", "FAILED"}:
                bad.append({"job_id": job.get("job_id"), "request_id": request_id, "status": status})
        operation = daemon.store.get_operation(job.get("operation_id", ""))
        if operation:
            meta = operation.get("metadata") or {}
            nested = meta.get("arguments") or {}
            if operation.get("operation") == "study.run" or nested.get("operation_id") == "study.run":
                bad.append({"job_id": job.get("job_id"), "reason": "study.run found in durable operation journal"})
    return {"idle": not bad, "reason": "all project jobs terminal and Worker submissions observed" if not bad else "unresolved project work",
            "bad_items": bad, "jobs": list(jobs), "worker_requests": request_evidence}


def run(args: argparse.Namespace) -> dict[str, Any]:
    # Registration reuse is a zero-export mode. Require the previously verified
    # native export receipt before importing any runner machinery, creating
    # paths, inventorying processes, or starting an engine. Without this gate,
    # omitting the receipt silently fell through to a fresh native export.
    if args.registration_receipt and not args.native_export_receipt:
        raise ValueError(
            "registration reuse requires a verified --native-export-receipt; "
            "implicit native export is forbidden"
        )
    from tools.run_native_resume_smoke import NativeLoopbackServer, _dispatch, _engine_build_identity

    started = time.monotonic()
    preflight_started_unix = time.time()
    project_id = _validate_project_id(args.project_id or PROJECT_ID)
    campaign_budget_s = args.budget_seconds if args.budget_seconds is not None else MAX_WALL_S
    if not (0 < campaign_budget_s <= MAX_WALL_S):
        raise ValueError("--budget-seconds must be positive and at most 1200")
    budget_origin_unix = args.budget_origin_unix
    budget_deadline_unix = args.budget_deadline_unix
    if (args.budget_origin_unix is None) != (args.budget_deadline_unix is None):
        raise ValueError("--budget-origin-unix and --budget-deadline-unix must be supplied together")
    if (budget_origin_unix is not None and budget_deadline_unix is not None
            and (budget_deadline_unix <= budget_origin_unix
                 or budget_deadline_unix - budget_origin_unix > MAX_WALL_S + 0.01)):
        raise ValueError("the fixed campaign budget window must be positive and at most 1200 seconds")
    if budget_deadline_unix is not None and time.time() >= budget_deadline_unix:
        raise TimeoutError("the fixed campaign wall-clock deadline has already elapsed")
    registration_reuse: dict[str, Any] | None = None
    registration_reuse_preflight: dict[str, Any] | None = None
    if args.registration_receipt:
        registration_path = Path(args.registration_receipt).resolve()
        if not registration_path.is_file():
            raise FileNotFoundError(f"successful artifact registration receipt not found: {registration_path}")
        registration_reuse = json.loads(registration_path.read_text(encoding="utf-8"))
        registration = registration_reuse.get("registration")
        if (registration_reuse.get("project_id") != project_id
                or not isinstance(registration, dict)
                or registration_reuse.get("artifact_id") != registration.get("artifact_id")
                or registration.get("sha256") != registration.get("artifact_id")
                or registration.get("role") != "geometry_source"
                or registration.get("classification") != "internal"):
            raise ValueError("registration receipt does not identify the approved task-local geometry artifact")
        registration_reuse_preflight = _verify_registration_reuse(registration_reuse)
    native_export_receipt: dict[str, Any] | None = None
    native_export_action: dict[str, Any] | None = None
    native_export_source: Path | None = None
    if args.native_export_receipt:
        receipt_path = Path(args.native_export_receipt).resolve()
        if not receipt_path.is_file():
            raise FileNotFoundError(f"native export receipt not found: {receipt_path}")
        native_export_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if native_export_receipt.get("status") != "VERIFIED_TASK_OWNED_NATIVE_EXPORT":
            raise ValueError("native export receipt is not a verified task-owned COMSOL export")
        source_unresolved = Path(native_export_receipt.get("source_path", ""))
        native_export_source = source_unresolved.resolve()
        source_work = Path("/private/tmp")
        if (not native_export_source.as_posix().startswith("/private/tmp/comsol-mcp-native-artifact-geometry-")
                or not native_export_source.is_relative_to(source_work)
                or native_export_source.suffix.lower() != ".mphtxt"
                or source_unresolved.is_symlink() or not native_export_source.is_file()):
            raise ValueError("native export source must be a task-owned regular MPHTXT under /private/tmp")
        expected_digest = native_export_receipt.get("source_sha256")
        expected_size = native_export_receipt.get("source_size_bytes")
        if (not isinstance(expected_size, int) or expected_size <= 0
                or native_export_source.stat().st_size != expected_size
                or _sha256(native_export_source) != expected_digest):
            raise ValueError("task-owned native MPHTXT source no longer matches its receipt hash")
        evidence_copy = REPO / native_export_receipt.get("durable_evidence_copy", "")
        if (not evidence_copy.is_file() or evidence_copy.is_symlink()
                or _sha256(evidence_copy) != expected_digest):
            raise ValueError("durable native MPHTXT evidence copy does not match its receipt hash")
        raw_action = REPO / native_export_receipt.get("worker_action_evidence", "")
        if (not raw_action.is_file() or _sha256(raw_action) != native_export_receipt.get("worker_action_sha256")):
            raise ValueError("original successful Worker export action changed or is missing")
        native_export_action = json.loads(raw_action.read_text(encoding="utf-8"))
        source_geometry_readback = _java_action_readback(native_export_action, "reused native export")
        if source_geometry_readback != native_export_receipt.get("geometry_readback"):
            raise ValueError("native export receipt does not match its original Worker action readback")
        if (native_export_receipt.get("java_fixture_sha256") != _sha256(FIXTURE_SOURCE)
                or native_export_receipt.get("java_entrypoint") != "NativeArtifactGeometryFixture#run"):
            raise ValueError("native export was produced by a different Java fixture revision")
        if registration_reuse is not None and (
            native_export_receipt.get("source_sha256") != registration_reuse.get("artifact_id")
            or native_export_receipt.get("source_size_bytes") != registration_reuse.get("registration", {}).get("size")
        ):
            raise ValueError("native export receipt does not identify the registered geometry artifact bytes")
    work = Path(args.work).resolve()
    evidence = Path(args.evidence).resolve()
    if not work.as_posix().startswith("/private/tmp/comsol-mcp-native-artifact-geometry-"):
        raise ValueError("--work must be a unique task-owned directory under /private/tmp/comsol-mcp-native-artifact-geometry-*")
    if evidence.exists() or work.exists():
        raise FileExistsError("both evidence and task-owned work paths must be new for this round")
    evidence.mkdir(parents=True, exist_ok=False)
    if not work.exists():
        work.mkdir(parents=True, exist_ok=False)
    events = evidence / "events.jsonl"
    _write_json(evidence / "run_started.json", {"at_unix": time.time(), "at_monotonic": started,
                                                  "work_dir": str(work), "evidence_dir": str(evidence),
                                                  "campaign_budget_origin_unix": budget_origin_unix,
                                                  "campaign_budget_deadline_unix": budget_deadline_unix,
                                                  "campaign_budget_total_s": campaign_budget_s,
                                                  "campaign_budget_remaining_at_start_s":
                                                  (campaign_budget_s if budget_deadline_unix is None else
                                                   max(0.0, budget_deadline_unix - time.time()))})
    if registration_reuse_preflight is not None:
        _write_json(evidence / "registration_reuse_preflight.json", registration_reuse_preflight)
    status = "PREFLIGHT"
    server = None
    daemon = None
    worker_connected = False
    dispatch_count = 0
    native_export_attempts = 0
    native_export_successes = 0
    registration_attempts = 0
    registration_successes = 0
    solver_guard_attempts: list[dict[str, Any]] = []
    original_study_run = None
    active_jobs: list[dict[str, Any]] = []
    latest_engine_identity: dict[str, Any] | None = None
    summary: dict[str, Any] = {"status": "FAIL_OR_INCOMPLETE", "scope": "native artifact registration/import; no study/solver"}
    import comsol_mcp._g3_ops as g3_ops

    def budget(label: str, *, required_remaining_s: float = CLEANUP_RESERVE_S) -> None:
        if budget_deadline_unix is None:
            if time.time() - preflight_started_unix >= campaign_budget_s:
                raise TimeoutError(f"pre-engine setup exceeded the {campaign_budget_s:.0f}s campaign preparation cap at {label}")
            return
        remaining = budget_deadline_unix - time.time()
        if remaining <= required_remaining_s:
            elapsed = time.time() - budget_origin_unix
            raise TimeoutError(
                f"fixed native geometry wall budget reached its cleanup reserve at {label}: "
                f"campaign elapsed={elapsed:.3f}s, remaining={remaining:.3f}s, "
                f"required_reserve={required_remaining_s:.3f}s"
            )

    def campaign_elapsed_s() -> float | None:
        return round(time.time() - budget_origin_unix, 3) if budget_origin_unix is not None else None

    def campaign_remaining_s() -> float | None:
        return round(budget_deadline_unix - time.time(), 3) if budget_deadline_unix is not None else None

    def operation_rpc_timeout(label: str, requested_s: float = 180.0) -> float:
        budget(label)
        if budget_deadline_unix is None:
            return requested_s
        return _bounded_rpc_timeout(requested_s, budget_deadline_unix, now_unix=time.time())

    def dispatch(label: str, operation: str, body: dict[str, Any], *, ref=None, revision=None,
                 rpc_timeout_s: float = 180.0) -> dict[str, Any]:
        nonlocal dispatch_count, registration_attempts, registration_successes
        budget(label + " before dispatch")
        if operation == "study.run":
            solver_guard_attempts.append({"label": label, "operation": operation, "at_monotonic_s": time.monotonic()})
            raise AssertionError("study.run is frozen as forbidden in this zero-solver probe")
        is_artifact_registration = operation == "registry_call" and body.get("operation_id") == "artifact.register"
        dispatch_count += 1
        idempotency_key = f"native-geometry-{label}-{uuid4()}"
        request_id = f"native-geometry-{label}-{uuid4()}"
        body = _execution_identity_arguments(operation, body, idempotency_key, request_id)
        effective_rpc_timeout_s = (rpc_timeout_s if budget_deadline_unix is None else
                                   _bounded_rpc_timeout(rpc_timeout_s, budget_deadline_unix, now_unix=time.time()))
        if is_artifact_registration:
            registration_attempts += 1
        response = _dispatch(
            daemon, operation, body, project_id=project_id, ref=ref, revision=revision,
            idempotency_key=idempotency_key, request_id=request_id, rpc_timeout_s=effective_rpc_timeout_s,
        )
        _write_json(evidence / f"action_{dispatch_count:02d}_{label.replace('.', '_')}.json", response)
        _append_event(events, "action_result", label=label, operation=operation, response=response)
        _require_success(response, label)
        if is_artifact_registration:
            registration_successes += 1
        budget(label + " after response")
        return response

    try:
        if not FREEZE_DOC.is_file() or not FIXTURE_SOURCE.is_file():
            raise FileNotFoundError("the frozen native geometry plan or Java fixture is missing")
        if "size `(2 m, 1 m)`" not in FREEZE_DOC.read_text(encoding="utf-8"):
            raise AssertionError("fixture freeze document no longer describes the frozen 2x1 rectangle")
        inventory = _process_inventory()
        _write_json(evidence / "preflight_inventory.json", inventory)
        if not inventory["server_start_allowed"]:
            raise RuntimeError("refusing to start COMSOL because native-engine process/listener inventory is incomplete or occupied")
        hashes_before = _source_hashes()
        freeze = {
            "status": "FROZEN_BEFORE_COMSOL_START",
            "frozen_at_unix": time.time(),
            "project_id": project_id,
            "scope": ("new task-owned project/store and target model; reuse verified native MPHTXT source; one fresh production artifact.register; one geometry.import/build/measure/validate; zero export/study/solver"
                      if not registration_reuse and native_export_receipt else
                      "new task-owned project/store and target model; one native MPHTXT export and one production artifact.register; one geometry.import/build/measure/validate; zero study/solver"
                      if not registration_reuse else
                      "fresh engine/session/model; reuse the prior project, OperationStore, registration, and verified MPHTXT source; zero new export/register; one geometry.import/build/type/measure/validate; zero study/solver"
                      if native_export_receipt else
                      "one native MPHTXT geometry export, one artifact.register, one geometry.import/build; no study/solver"),
            "work_dir": str(work), "evidence_dir": str(evidence),
            "wall_clock_budget_s": campaign_budget_s,
            "cleanup_reserve_s": CLEANUP_RESERVE_S,
            "fixed_budget_window": {"origin_unix": budget_origin_unix,
                                    "deadline_unix": budget_deadline_unix,
                                    "remaining_at_freeze_s": (campaign_budget_s if budget_deadline_unix is None else
                                                               max(0.0, budget_deadline_unix - time.time())),
                                    "duration_s": campaign_budget_s,
                                    "origin_basis": "first new task-owned Engine PID lstart readback"},
            "max_comsol_study_run_calls": 0,
            "max_solver_calls": 0,
            "max_native_exports_this_round": 0 if native_export_receipt else 1,
            "max_artifact_registrations_this_round": 0 if registration_reuse else 1,
            "fixture": {"shape": "2D rectangle", "corner_m": [0.0, 0.0], "size_m": [2.0, 1.0],
                        "format": "COMSOL native MPHTXT", "source_file": str(FIXTURE_SOURCE),
                        "source_sha256": hashes_before["java_fixture"]["sha256"]},
            "native_export_reuse": ({"used": True, "receipt_path": str(Path(args.native_export_receipt).resolve()),
                                     "receipt_sha256": _sha256(Path(args.native_export_receipt).resolve()),
                                     "native_export_count_this_campaign": 0,
                                     "source_sha256": native_export_receipt["source_sha256"]}
                                    if native_export_receipt else {"used": False, "native_export_count_this_campaign": 0}),
            "registration_reuse": ({"used": True,
                                    "prior_receipt": str(Path(args.registration_receipt).resolve()),
                                    "prior_receipt_sha256": _sha256(Path(args.registration_receipt).resolve()),
                                    "artifact_id": registration_reuse["artifact_id"],
                                    "new_registration_count": 0,
                                    "project_root": registration_reuse_preflight["project_root"],
                                    "operation_store_path": registration_reuse_preflight["operation_store_path"],
                                    "preflight_status": registration_reuse_preflight["status"]}
                                   if registration_reuse else {"used": False}),
            "new_registration_count": 0 if registration_reuse else 1,
            "acceptance": {"dimension": 2, "domains": 1, "entity_counts": {"2": 1},
                           "bounding_box_m": [0.0, 2.0, 0.0, 1.0], "bounding_box_abs_tolerance_m": 1e-10,
                           "area_m2": 2.0, "area_abs_tolerance_m2": 1e-8, "length_unit": "m",
                           "native_import_type": "native", "build": True},
            "engine_target": {"install_root": str(INSTALL_ROOT), "java_home": str(JAVA11),
                              "api_identity": "COMSOL Multiphysics 6.4.0.293",
                              "os_scope": "observed native on macOS 27; not vendor OS certification"},
            "preexisting_process_inventory": inventory,
            "source_sha256_manifest": hashes_before,
            "documentation_basis": {"fixture_freeze_doc_sha256": _sha256(FREEZE_DOC),
                                    "api_refs": [
                                        {"topic": "GeomSequence.exportFinal(String)", "doc_id": 7599, "chunk_id": 22895,
                                         "sha256": "047a6aeebc08362e81db383f1b758b688303a702b26a7d020e18146fa63ad661"},
                                        {"topic": "Import mphbin/mphtxt", "doc_id": 4241, "chunk_id": 16969,
                                         "sha256": "c6314b25ce71f0874f8d849711c5dc42b2aee37bce8085234717a6bbdb353650"},
                                        {"topic": "GeomInfo", "doc_id": 7584, "chunk_id": 22864,
                                         "sha256": "cc9d4949351c49c4e6f43801fb47b891d6d121bcc1a3fd0b467448aa026885f2"},
                                        {"topic": "Measurement Methods", "doc_id": 4187, "chunk_id": 16900,
                                         "sha256": "b45dde195715138541dfc01826d9e93ce02b8967fa9c0ceb6c9893a60e99120e"},
                                    ]},
        }
        _write_json(evidence / "fixture_freeze.json", freeze)
        _append_event(events, "fixture_frozen_before_server", freeze_sha256=_sha256(evidence / "fixture_freeze.json"),
                      server_start_allowed=True, no_study_solver=True)
        _refresh_resume_state(evidence, "FROZEN_BEFORE_SERVER_START", [])
        budget("private server preparation")

        server = NativeLoopbackServer(work, evidence, event_log=events)
        if registration_reuse:
            suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", evidence.name).strip("-_")[:64]
            if not suffix:
                suffix = uuid4().hex[:12]
            preserved_project = Path(registration_reuse_preflight["project_root"])
            preserved_store = Path(registration_reuse_preflight["operation_store_path"])
            server.shadow_root = work / f"comsol-shadow-{suffix}"
            server.runtime = work / f"runtime-{suffix}"
            server.prefs = server.runtime / "prefs"
            server.tmp = server.runtime / "tmp"
            server.recovery = server.runtime / "recovery"
            server.project = work / f"project-{suffix}-init"
            server.worker_state = work / f"worker-state-{suffix}"
            server.receipt_path = evidence / "isolation_receipt.json"
            server.server_log = evidence / "mphserver.log"
            shadow = server.prepare_shadow()
            server.project = preserved_project
        else:
            shadow = server.prepare_shadow()
        _write_json(evidence / "private_shadow_receipt.json", shadow)
        listener = server.start_and_verify_listener()
        if budget_origin_unix is None and budget_deadline_unix is None:
            process_birth = _mac_birth_epoch(listener["process_identity"]["birth"])
            budget_origin_unix = process_birth
            budget_deadline_unix = process_birth + campaign_budget_s
            run_started_record = json.loads((evidence / "run_started.json").read_text(encoding="utf-8"))
            run_started_record["campaign_budget_origin_unix"] = budget_origin_unix
            run_started_record["campaign_budget_deadline_unix"] = budget_deadline_unix
            run_started_record["campaign_budget_total_s"] = campaign_budget_s
            run_started_record["campaign_budget_origin_basis"] = "task-owned COMSOL PID lstart readback"
            _write_json(evidence / "run_started.json", run_started_record)
            _write_json(evidence / "campaign_budget_origin.json", {
                "status": "OPEN_FROM_ACTUAL_ENGINE_BIRTH",
                "budget_seconds": campaign_budget_s,
                "origin_unix": budget_origin_unix,
                "origin_lstart": listener["process_identity"]["birth"],
                "origin_timezone": "Asia/Shanghai",
                "deadline_unix": budget_deadline_unix,
                "deadline_utc": datetime.fromtimestamp(budget_deadline_unix, tz=ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                "server_pid": listener["pid"],
                "endpoint": listener["endpoint"],
                "max_engine_starts": 1,
                "exports_this_round": 0,
                "registrations_this_round": 0,
                "study_solver_calls_allowed": 0,
            })
            _append_event(events, "engine_birth_budget_opened", origin_unix=budget_origin_unix,
                          deadline_unix=budget_deadline_unix, server_pid=listener["pid"],
                          birth=listener["process_identity"]["birth"])
        budget("actual process birth initialization")
        active_jobs = [{"kind": "native_geometry_private_server", "pid": listener["pid"],
                        "port": listener["port"], "endpoint": listener["endpoint"], "evidence_dir": str(evidence)}]
        _refresh_resume_state(evidence, "LOOPBACK_LISTENER_VERIFIED", active_jobs)
        budget("worker startup", required_remaining_s=CLEANUP_RESERVE_S + 60.0)
        worker_info = server.start_worker()
        worker_info["worker_compile_identity"] = _worker_compilation_identity(server)
        _write_json(evidence / "worker_connection.json", worker_info)
        _append_event(events, "worker_compile_identity_frozen",
                      worker_compile_identity=worker_info["worker_compile_identity"])
        worker_connected = True
        latest_engine_identity = worker_info["engine_version_identity"]
        if not latest_engine_identity.get("matches_frozen_target"):
            raise AssertionError(f"COMSOL runtime build did not match frozen 6.4.0.293: {latest_engine_identity!r}")
        os.environ["COMSOL_ROOT"] = str(server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(JAVA11)
        os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
        os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)

        from comsol_mcp._control_daemon import ControlDaemon
        control_root = preserved_store.parent if registration_reuse else work / "control"
        daemon = ControlDaemon(control_root, worker=server.worker, project_root=server.project)
        connected = dispatch("server_connect", "server_connect", {"host": "127.0.0.1", "port": server.port}, ref=None, revision=None)
        endpoint = connected.get("data", {}).get("endpoint")
        if endpoint != f"127.0.0.1:{server.port}":
            raise AssertionError(f"managed control daemon bound a different endpoint: {endpoint!r}")

        # Deny the one logical solver operation at its production dispatch point.
        original_study_run = g3_ops.DISPATCH.get("study.run")
        def forbidden_study_run(*call_args: Any, **call_kwargs: Any) -> Any:
            solver_guard_attempts.append({"source": "_g3_ops.DISPATCH", "at_monotonic_s": time.monotonic()})
            raise AssertionError("study.run is forbidden in the zero-solver geometry probe")
        g3_ops.DISPATCH["study.run"] = forbidden_study_run

        source_tag = None
        if native_export_receipt is None:
            source_model = server.worker.client().create(
                "Native Artifact Geometry Source", rpc_timeout_s=operation_rpc_timeout("source model creation")
            )
            source_tag = str(source_model.java.tag())
        fixture_in_project = server.project / FIXTURE_SOURCE.name
        if fixture_in_project.is_symlink():
            raise RuntimeError("task project Java fixture path must not be a symlink")
        if fixture_in_project.is_file():
            if _sha256(fixture_in_project) != _sha256(FIXTURE_SOURCE):
                raise RuntimeError("task project Java fixture differs from the frozen source; refusing overwrite")
        else:
            shutil.copy2(FIXTURE_SOURCE, fixture_in_project)
        exported = server.project / "inputs" / "rectangle_2x1.mphtxt"
        exported.parent.mkdir(parents=True, exist_ok=True)
        if native_export_receipt is None:
            source_binding = daemon.service.bind_model(source_tag, ownership="mcp_owned")
            source_ref = source_binding["execution"]["model_ref"]
            source_revision = source_binding["execution"]["revision"]
            native_export_attempts += 1
            export_response = _operation_call(
                daemon, "code.execute_java",
                {"source_artifact": fixture_in_project.name, "entrypoint": "NativeArtifactGeometryFixture#run",
                 "arguments": {"phase": "export", "path": str(exported)}, "mode": "trusted"},
                ref=source_ref, revision=source_revision, label="native_geometry_export", project_id=project_id,
                rpc_timeout_s=operation_rpc_timeout("native geometry export"),
            )
            dispatch_count += 1
            _write_json(evidence / f"action_{dispatch_count:02d}_native_geometry_export.json", export_response)
            _append_event(events, "action_result", label="native_geometry_export", operation="code.execute_java",
                          response=export_response)
            _require_success(export_response, "native geometry export")
            native_export_successes += 1
            export_readback = _java_action_readback(export_response, "native geometry export")
            if not exported.is_file() or exported.stat().st_size == 0:
                raise AssertionError("COMSOL GeomSequence.exportFinal did not create a nonempty MPHTXT")
            _response_revision(export_response, daemon, source_ref)
            source_tag = str(export_response.get("data", {}).get("worker", {}).get("result", {}).get("model_tag", source_tag))
            export_origin = {"kind": "native_export_in_this_run", "worker_action": export_response}
        else:
            if exported.is_symlink():
                raise RuntimeError("task project MPHTXT source path must not be a symlink")
            if exported.is_file():
                if _sha256(exported) != native_export_receipt["source_sha256"]:
                    raise RuntimeError("task project MPHTXT source differs from the frozen receipt; refusing overwrite")
            else:
                shutil.copy2(native_export_source, exported)
            export_readback = dict(native_export_receipt["geometry_readback"])
            source_tag = native_export_action.get("data", {}).get("worker", {}).get("result", {}).get("model_tag")
            export_origin = {"kind": "reused_prior_task_owned_native_export",
                             "receipt_path": str(Path(args.native_export_receipt).resolve()),
                             "receipt_sha256": _sha256(Path(args.native_export_receipt).resolve()),
                             "prior_source_path": str(native_export_source)}
            _append_event(events, "native_export_reused_from_prior_owned_attempt",
                          source_sha256=native_export_receipt["source_sha256"], receipt=export_origin)
        if (not exported.is_file() or exported.stat().st_size == 0
                or export_readback.get("source_dimension") != 2
                or export_readback.get("source_domains") != 1
                or export_readback.get("source_bounding_box") != [0.0, 2.0, 0.0, 1.0]
                or export_readback.get("source_length_unit") != "m"):
            raise AssertionError(f"native source fixture differs from frozen geometry values: {export_readback!r}")
        source_file_receipt = {"path": str(exported), "relative_path": exported.relative_to(server.project).as_posix(),
                               "size_bytes": exported.stat().st_size, "sha256": _sha256(exported),
                               "source_geometry_readback": export_readback, "provenance": export_origin}
        _write_json(evidence / "native_export_receipt.json", source_file_receipt)
        source_hash_before_register = source_file_receipt["sha256"]
        if native_export_source is not None:
            source_digest = native_export_receipt["source_sha256"]
            protected_copy = REPO / native_export_receipt["durable_evidence_copy"]
            source_integrity_before_register = {
                "source_path": str(native_export_source),
                "source_size_bytes": native_export_source.stat().st_size,
                "source_sha256": _sha256(native_export_source),
                "project_input_path": str(exported),
                "project_input_size_bytes": exported.stat().st_size,
                "project_input_sha256": _sha256(exported),
                "protected_ssd_copy_path": str(protected_copy),
                "protected_ssd_copy_size_bytes": protected_copy.stat().st_size,
                "protected_ssd_copy_sha256": _sha256(protected_copy),
                "expected_sha256": source_digest,
                "expected_size_bytes": native_export_receipt["source_size_bytes"],
            }
            if (any(source_integrity_before_register[key] != source_digest for key in
                    ("source_sha256", "project_input_sha256", "protected_ssd_copy_sha256"))
                    or any(source_integrity_before_register[key] != native_export_receipt["source_size_bytes"] for key in
                           ("source_size_bytes", "project_input_size_bytes", "protected_ssd_copy_size_bytes"))):
                raise AssertionError("MPHTXT source bytes changed before artifact.register")
            _write_json(evidence / "artifact_register_input_integrity.json", source_integrity_before_register)
        else:
            source_integrity_before_register = None

        if registration_reuse:
            registration = registration_reuse["registration"]
            artifact_id = registration_reuse["artifact_id"]
            managed_file = server.project / registration["path"]
            durable_record = daemon.store.get_metadata("artifacts", artifact_id)
            if (durable_record != registration_reuse.get("durable_store_record")
                    or durable_record.get("project_id") != project_id
                    or durable_record.get("artifact_id") != artifact_id
                    or durable_record.get("sha256") != artifact_id
                    or not managed_file.is_file() or _sha256(managed_file) != artifact_id):
                raise AssertionError("registered artifact did not survive same-project Store readback on the new Engine")
            _write_json(evidence / "artifact_registration_receipt.json", {
                "project_id": project_id, "artifact_id": artifact_id, "registration": registration,
                "source": source_file_receipt,
                "managed_copy": {"path": str(managed_file), "size_bytes": managed_file.stat().st_size,
                                 "sha256": _sha256(managed_file)},
                "durable_store_record": durable_record,
                "reuse_proof": {"prior_receipt": str(Path(args.registration_receipt).resolve()),
                                "prior_receipt_sha256": _sha256(Path(args.registration_receipt).resolve()),
                                "new_registration_count": 0,
                                "same_project_root": str(server.project),
                                "new_operation_store_path": str(preserved_store),
                                "new_server_endpoint": server.process_identity.get("port")},
                "new_registration_count": 0,
                "new_project_root": str(server.project),
                "new_operation_store_path": str(preserved_store),
            })
            _append_event(events, "prior_artifact_registration_reused_after_engine_restart",
                          artifact_id=artifact_id, new_registration_count=0)
        else:
            register_body = {"project_id": project_id,
                             "path": source_file_receipt["relative_path"], "role": "geometry_source",
                             "classification": "internal"}
            register_response = dispatch("artifact_register", "registry_call",
                                         {"operation_id": "artifact.register", "arguments": register_body})
            registration = register_response.get("data", {})
            artifact_id = registration.get("artifact_id")
            if artifact_id != source_file_receipt["sha256"] or registration.get("sha256") != artifact_id:
                raise AssertionError(f"artifact registration did not bind the exported bytes by SHA-256: {registration!r}")
            if not isinstance(registration.get("path"), str) or not registration["path"].startswith("g2_artifacts/registered/"):
                raise AssertionError(f"artifact registration did not return the project-managed content-addressed path: {registration!r}")
            managed_file = server.project / registration["path"]
            if not managed_file.is_file() or _sha256(managed_file) != artifact_id:
                raise AssertionError("registered project artifact failed native filesystem hash readback")
            durable_record = daemon.store.get_metadata("artifacts", artifact_id)
            if (not isinstance(durable_record, dict)
                    or durable_record.get("project_id") != project_id
                    or durable_record.get("artifact_id") != artifact_id
                    or durable_record.get("sha256") != artifact_id
                    or durable_record.get("size") != native_export_receipt.get("source_size_bytes")
                    or durable_record.get("path") != registration.get("path")
                    or durable_record.get("role") != "geometry_source"
                    or durable_record.get("classification") != "internal"):
                raise AssertionError("new production OperationStore record did not read back the registered project and source identity")
            source_integrity_after_register = None
            if native_export_source is not None:
                protected_copy = REPO / native_export_receipt["durable_evidence_copy"]
                source_integrity_after_register = {
                    "source_size_bytes": native_export_source.stat().st_size,
                    "source_sha256": _sha256(native_export_source),
                    "project_input_size_bytes": exported.stat().st_size,
                    "project_input_sha256": _sha256(exported),
                    "protected_ssd_copy_size_bytes": protected_copy.stat().st_size,
                    "protected_ssd_copy_sha256": _sha256(protected_copy),
                    "managed_copy_size_bytes": managed_file.stat().st_size,
                    "managed_copy_sha256": _sha256(managed_file),
                    "store_record_sha256": durable_record.get("sha256"),
                }
                if (any(source_integrity_after_register[key] != artifact_id for key in
                        ("source_sha256", "project_input_sha256", "protected_ssd_copy_sha256",
                         "managed_copy_sha256", "store_record_sha256"))
                        or any(source_integrity_after_register[key] != native_export_receipt["source_size_bytes"] for key in
                               ("source_size_bytes", "project_input_size_bytes", "protected_ssd_copy_size_bytes",
                                "managed_copy_size_bytes"))):
                    raise AssertionError("MPHTXT source or registered copy changed after artifact.register")
            _write_json(evidence / "artifact_registration_receipt.json", {
                "project_id": project_id, "artifact_id": artifact_id, "registration": registration,
                "source": source_file_receipt, "managed_copy": {"path": str(managed_file), "size_bytes": managed_file.stat().st_size,
                                                                   "sha256": _sha256(managed_file)},
                "durable_store_record": daemon.store.get_metadata("artifacts", artifact_id),
                "source_integrity_before_register": source_integrity_before_register,
                "source_integrity_after_register": source_integrity_after_register,
                "new_registration_count": 1,
                "new_project_root": str(server.project),
                "new_operation_store_path": str(work / "control" / "operations.sqlite3"),
            })

        target_model = server.worker.client().create(
            "Native Artifact Geometry Target", rpc_timeout_s=operation_rpc_timeout("target model creation")
        )
        target_tag = str(target_model.java.tag())
        target_binding = daemon.service.bind_model(target_tag, ownership="mcp_owned")
        target_ref = target_binding["execution"]["model_ref"]
        target_revision = target_binding["execution"]["revision"]
        target_response = _operation_call(
            daemon, "code.execute_java",
            {"source_artifact": fixture_in_project.name, "entrypoint": "NativeArtifactGeometryFixture#run",
             "arguments": {"phase": "target"}, "mode": "trusted"},
            ref=target_ref, revision=target_revision, label="target_geometry_prepare", project_id=project_id,
            rpc_timeout_s=operation_rpc_timeout("target geometry preparation"),
        )
        dispatch_count += 1
        _write_json(evidence / f"action_{dispatch_count:02d}_target_geometry_prepare.json", target_response)
        _append_event(events, "action_result", label="target_geometry_prepare", operation="code.execute_java",
                      response=target_response)
        _require_success(target_response, "empty target geometry preparation")
        target_data = _java_action_readback(target_response, "empty target geometry preparation")
        if not isinstance(target_data, dict) or target_data.get("target_dimension") != 2 or target_data.get("target_length_unit") != "m":
            raise AssertionError(f"empty target geometry preparation differs from freeze: {target_data!r}")
        target_revision = _response_revision(target_response, daemon, target_ref)

        geom_path = _geometry_path()
        import_response = _operation_call(
            daemon, "geometry.import",
            {"geometry": geom_path, "tag": "imp1", "artifact_id": artifact_id,
             "options": {"build": True, "includevirtual": False}},
            ref=target_ref, revision=target_revision, label="geometry_import_build", project_id=project_id,
            rpc_timeout_s=operation_rpc_timeout("geometry import and build"),
        )
        dispatch_count += 1
        _write_json(evidence / f"action_{dispatch_count:02d}_geometry_import_build.json", import_response)
        _append_event(events, "action_result", label="geometry_import_build", operation="geometry.import",
                      response=import_response)
        _require_success(import_response, "registered artifact geometry.import")
        import_data = import_response.get("data", {})
        if import_data.get("artifact_id") != artifact_id or import_data.get("artifact_path_verbatim") is not False:
            raise AssertionError(f"geometry.import did not preserve the registered digest identity: {import_data!r}")
        if import_data.get("import_type_readback") != "native":
            raise AssertionError(f"COMSOL did not read the MPHTXT Import type as native: {import_data.get('import_type_readback')!r}")
        build_data = import_data.get("build")
        if not isinstance(build_data, dict) or build_data.get("built") is not True or build_data.get("ok") is not True:
            raise AssertionError(f"native imported geometry did not build successfully: {build_data!r}")
        target_revision = _response_revision(import_response, daemon, target_ref)

        measure_response = _operation_call(
            daemon, "geometry.measure",
            {"geometry": geom_path, "query": {"mode": "entities", "entity_dimension": 2,
                                                   "all": True,
                                                   "metrics": ["area", "bounding_box", "n_entities"]}},
            ref=target_ref, revision=target_revision, label="geometry_measure_readback", project_id=project_id,
            rpc_timeout_s=operation_rpc_timeout("geometry measurement"),
        )
        dispatch_count += 1
        _write_json(evidence / f"action_{dispatch_count:02d}_geometry_measure_readback.json", measure_response)
        _append_event(events, "action_result", label="geometry_measure_readback", operation="geometry.measure",
                      response=measure_response)
        _require_success(measure_response, "geometry.measure")
        measure_data = measure_response.get("data", {})
        metrics = measure_data.get("metrics", {})
        measured_area = metrics.get("area", {}).get("value")
        measured_box = metrics.get("bounding_box", {}).get("value")
        if not isinstance(measured_area, (int, float)) or abs(float(measured_area) - 2.0) > 1e-8:
            raise AssertionError(f"measured geometry area violates frozen 2 m^2 ±1e-8: {measured_area!r}")
        if not isinstance(measured_box, list) or len(measured_box) != 4:
            raise AssertionError(f"measured geometry bounding box is unavailable or malformed: {measured_box!r}")
        if max(abs(float(actual) - expected) for actual, expected in zip(measured_box, [0.0, 2.0, 0.0, 1.0])) > 1e-10:
            raise AssertionError(f"measured geometry bounding box violates frozen values: {measured_box!r}")
        if measure_data.get("length_unit") != "m":
            raise AssertionError(f"measured geometry length unit differs from frozen m: {measure_data.get('length_unit')!r}")
        target_revision = _response_revision(measure_response, daemon, target_ref)

        validate_response = _operation_call(
            daemon, "geometry.validate",
            {"geometry": geom_path, "expectations": {
                "dimension": 2, "entity_counts": {"2": 1}, "length_unit": "m",
                "bounding_box": [0.0, 2.0, 0.0, 1.0], "area": {"value": 2.0, "tolerance": 1e-8},
                "tolerance": 1e-10, "feature_tags": {"present": ["imp1"]},
            }},
            ref=target_ref, revision=target_revision, label="geometry_validate_readback", project_id=project_id,
            rpc_timeout_s=operation_rpc_timeout("geometry validation"),
        )
        dispatch_count += 1
        _write_json(evidence / f"action_{dispatch_count:02d}_geometry_validate_readback.json", validate_response)
        _append_event(events, "action_result", label="geometry_validate_readback", operation="geometry.validate",
                      response=validate_response)
        _require_success(validate_response, "geometry.validate")
        validation = validate_response.get("data", {})
        if validation.get("ok") is not True or validation.get("failed_checks"):
            raise AssertionError(f"geometry.validate did not pass all frozen checks: {validation!r}")
        target_revision = _response_revision(validate_response, daemon, target_ref)

        idle = _idle_proof(daemon, project_id)
        if not idle["idle"]:
            raise AssertionError(f"native probe left a nonterminal operation or unobserved Worker request: {idle!r}")
        if solver_guard_attempts:
            raise AssertionError(f"study.run guard was invoked: {solver_guard_attempts!r}")
        final_hashes = _source_hashes()
        if final_hashes != hashes_before:
            raise AssertionError("one or more frozen source files changed during the native probe")
        jobs = list(daemon.store.list_jobs(offset=0, limit=1000, project_id=project_id))
        study_rows = []
        for job in jobs:
            operation = daemon.store.get_operation(job.get("operation_id", ""))
            if operation:
                metadata = operation.get("metadata") or {}
                nested = metadata.get("arguments") or {}
                if operation.get("operation") == "study.run" or nested.get("operation_id") == "study.run":
                    study_rows.append({"job_id": job.get("job_id"), "operation": operation})
        if study_rows:
            raise AssertionError(f"durable store contains forbidden study.run calls: {study_rows!r}")
        budget("final acceptance")
        summary = {
            "status": "PASS_NATIVE_ARTIFACT_GEOMETRY_ZERO_SOLVER",
            "project_id": project_id,
            "scope": ("COMSOL 6.4.0.293 new task-owned project/store and model; verified protected native MPHTXT source; one production artifact.register; geometry import/build/measure/validate; zero export/study/solver"
                      if not registration_reuse and native_export_receipt else
                      "COMSOL 6.4.0.293 new Engine/session/model; same-project registered artifact store readback; registered geometry import/build and geometry readback"
                      if registration_reuse else
                      "COMSOL 6.4.0.293 native MPHTXT export, project artifact registration, registered geometry import/build and geometry readback"),
            "criteria_scope": "one 2-D rectangle fixture only; no CAD add-on formats, Desktop, W23/W24, physics or solver acceptance",
            "started_unix": json.loads((evidence / "run_started.json").read_text())["at_unix"],
            "completed_unix": time.time(), "elapsed_s": round(time.monotonic() - started, 3),
            "wall_clock_budget_s": campaign_budget_s,
            "planned_maximum_native_exports": 0 if native_export_receipt else 1,
            "planned_maximum_artifact_registrations": 0 if registration_reuse else 1,
            **_action_count_receipt(native_export_attempts=native_export_attempts,
                                    native_export_successes=native_export_successes,
                                    registration_attempts=registration_attempts,
                                    registration_successes=registration_successes),
            "campaign_elapsed_s": campaign_elapsed_s(),
            "campaign_budget_remaining_s": campaign_remaining_s(),
            "campaign_budget_origin_unix": budget_origin_unix,
            "campaign_budget_deadline_unix": budget_deadline_unix,
            "engine_version": worker_info["engine_version"], "engine_version_identity": latest_engine_identity,
            "os_scope": "observed native on macOS 27; not vendor OS certification",
            "listener": listener, "worker_connection": worker_info,
            "source_model_tag": source_tag, "target_model_tag": target_tag, "target_model_ref": target_ref,
            "target_final_revision": target_revision, "native_export": source_file_receipt,
            "artifact_registration": json.loads((evidence / "artifact_registration_receipt.json").read_text()),
            "geometry_import": import_data,
            "geometry_measure": measure_data,
            "geometry_validate": validation,
            "study_run_guard_attempts": solver_guard_attempts,
            "durable_study_run_jobs": study_rows,
            "durable_jobs": jobs,
            "worker_quiescence": idle,
            "source_sha256_manifest_before": hashes_before,
            "source_sha256_manifest_after": final_hashes,
        }
        _write_json(evidence / "summary.json", summary)
        _append_event(events, "native_artifact_geometry_passed", status=summary["status"], elapsed_s=summary["elapsed_s"],
                      artifact_id=artifact_id, target_revision=target_revision)
        status = summary["status"]
        _refresh_resume_state(evidence, status, active_jobs)
        return summary
    except BaseException as exc:
        summary = {"status": "FAIL_OR_INCOMPLETE", "at_unix": time.time(), "error_type": type(exc).__name__,
                   "error": str(exc), "traceback": traceback.format_exc(),
                   "elapsed_s": round(time.monotonic() - started, 3), "dispatch_count": dispatch_count,
                   "planned_maximum_native_exports": 0 if native_export_receipt else 1,
                   "planned_maximum_artifact_registrations": 0 if registration_reuse else 1,
                   **_action_count_receipt(native_export_attempts=native_export_attempts,
                                           native_export_successes=native_export_successes,
                                           registration_attempts=registration_attempts,
                                           registration_successes=registration_successes),
                   "campaign_elapsed_s": campaign_elapsed_s(),
                   "campaign_budget_remaining_s": campaign_remaining_s(),
                   "campaign_budget_origin_unix": budget_origin_unix,
                   "campaign_budget_deadline_unix": budget_deadline_unix,
                   "study_run_guard_attempts": solver_guard_attempts, "engine_identity": latest_engine_identity}
        try:
            _write_json(evidence / "failure.json", summary)
            _append_event(events, "native_artifact_geometry_failed", **summary)
            status = summary["status"]
        except Exception:
            pass
        if daemon is not None:
            idle = _idle_proof(daemon, project_id)
            summary["worker_quiescence"] = idle
            try:
                _write_json(evidence / "failure.json", summary)
            except Exception:
                pass
            if not idle["idle"]:
                active_jobs = [{"kind": "native_geometry_unresolved_work", "jobs": idle.get("bad_items", []),
                                "server_pid": server.proc.pid if server and server.proc else None,
                                "port": server.port if server else None, "evidence_dir": str(evidence)}]
        try:
            _refresh_resume_state(evidence, status, active_jobs)
        except Exception:
            pass
        raise
    finally:
        if original_study_run is not None:
            g3_ops.DISPATCH["study.run"] = original_study_run
        idle = _idle_proof(daemon, project_id)
        safe_to_stop = idle["idle"]
        cleanup: dict[str, Any] = {"idle_proof": idle, "worker_closed": False, "server": {"status": "NOT_STARTED"}}
        if safe_to_stop and daemon is not None:
            try:
                daemon.close()
                cleanup["control_daemon_closed"] = True
            except Exception as exc:
                cleanup["control_daemon_close_error"] = repr(exc)
                safe_to_stop = False
        if safe_to_stop and server is not None and worker_connected:
            try:
                server.close_worker()
                cleanup["worker_closed"] = server.worker is None
            except Exception as exc:
                cleanup["worker_close_error"] = repr(exc)
                safe_to_stop = False
        if server is not None:
            try:
                cleanup["server"] = server.stop_server(allow_stop=safe_to_stop)
            except Exception as exc:
                cleanup["server_stop_error"] = repr(exc)
        server_stopped = server is None or cleanup.get("server", {}).get("status") == "STOPPED"
        worker_closed_or_not_started = not worker_connected or cleanup.get("worker_closed") is True
        cleanup_verified = safe_to_stop and server_stopped and worker_closed_or_not_started
        if cleanup_verified:
            active_jobs = []
        try:
            _write_json(evidence / "cleanup.json", cleanup)
            if status == "PASS_NATIVE_ARTIFACT_GEOMETRY_ZERO_SOLVER" and (
                not cleanup_verified or cleanup.get("worker_closed") is not True
            ):
                summary["status"] = "FAIL_CLEANUP_UNVERIFIED"
                summary["cleanup"] = cleanup
                _write_json(evidence / "summary.json", summary)
                _refresh_resume_state(evidence, summary["status"], active_jobs)
            elif status != "PASS_NATIVE_ARTIFACT_GEOMETRY_ZERO_SOLVER":
                _refresh_resume_state(evidence, status, active_jobs)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--budget-origin-unix", type=float)
    parser.add_argument("--budget-deadline-unix", type=float)
    parser.add_argument("--budget-seconds", type=float)
    parser.add_argument("--native-export-receipt")
    parser.add_argument("--registration-receipt")
    args = parser.parse_args()
    try:
        result = run(args)
    except BaseException as exc:
        failure = {"status": "FAIL_OR_INCOMPLETE", "error_type": type(exc).__name__, "error": str(exc)}
        # Before the evidence directory exists, this runner has not crossed its
        # first possible engine-birth point or dispatched a native action.
        if not Path(args.evidence).resolve().exists():
            failure["engine_birth_count_this_round"] = 0
            failure.update(_action_count_receipt())
        print(json.dumps(failure, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str, allow_nan=False))
    return 0 if result.get("status") == "PASS_NATIVE_ARTIFACT_GEOMETRY_ZERO_SOLVER" else 2


if __name__ == "__main__":
    raise SystemExit(main())
