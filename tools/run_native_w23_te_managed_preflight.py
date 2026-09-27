#!/usr/bin/env python3
"""Freeze or run the managed, no-solve W23 2D TE API readback preflight.

The prepare phase compiles the Java fixture offline and writes an immutable
candidate/freeze manifest. The execute phase requires that exact freeze hash
on the command line, verifies a fresh quiescent process/listener inventory,
starts one task-owned COMSOL 6.4 server, and makes one managed Worker Java
invocation. It never calls a Study or solver. An unobserved Worker request is
never retried. Cleanup requires full paged Worker-ledger reconciliation and
fresh exact process identities; it may stop the task-owned processes after
terminal Worker observations while preserving an UNKNOWN model result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    # When launched as `python tools/run_native_w23_te_managed_preflight.py`,
    # sys.path[0] is the tools directory, not the repository root. This must
    # happen before importing the other task-local tools modules below.
    sys.path.insert(0, str(REPO))
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
FIXTURE = REPO / "tools/java/NativeW23TEFixture.java"
PLAN = REPO / "docs/full_project_execution/w23_overlap/W23_TE_FIXTURE_PROPOSAL.md"
FREEZE_TEMPLATE = REPO / "docs/full_project_execution/w23_overlap/W23_TE_API_PREFLIGHT_FREEZE_TEMPLATE.md"
EVIDENCE_ROOT = REPO / "docs/full_project_execution/w23_overlap/evidence"
PROJECT_ID = "w23-te-port-api-preflight"
CANDIDATE_ID = "W23-TE-PORT-API-READBACK-06"
MAX_BIRTH_BUDGET_S = 900.0
CLEANUP_RESERVE_S = 60.0
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}
# LOST is a terminal ledger label but does not prove the engine stopped
# processing the native request, so it is deliberately not cleanup-terminal.
WORKER_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
LEDGER_PAGE_SIZE = 1000
EXACT_CLEANUP_GRACE_S = 45.0

# The wrapper and every code path that can start, isolate, dispatch, or record
# the Worker request are pinned into the candidate. This is deliberately a
# small source closure, not a whole repository archive.
SOURCE_PATHS = {
    "managed_runner": Path(__file__).resolve(),
    "java_fixture": FIXTURE,
    "proposal": PLAN,
    "freeze_template": FREEZE_TEMPLATE,
    "package_init": REPO / "comsol_mcp/__init__.py",
    "native_server_helper": REPO / "tools/run_native_resume_smoke.py",
    "atomic_save": REPO / "comsol_mcp/_atomic_save.py",
    "control_daemon": REPO / "comsol_mcp/_control_daemon.py",
    "execution_contract": REPO / "comsol_mcp/_execution_contract.py",
    "execution_service": REPO / "comsol_mcp/_execution_service.py",
    "operation_store": REPO / "comsol_mcp/_operation_store.py",
    "managed_backend": REPO / "comsol_mcp/_managed_backend.py",
    "server": REPO / "comsol_mcp/_server.py",
    "operation_dispatch": REPO / "comsol_mcp/_g3_ops.py",
    "registry": REPO / "comsol_mcp/_g2_registry.py",
    "isolation": REPO / "comsol_mcp/_g2_isolation.py",
    "java_worker": REPO / "comsol_mcp/_java_worker.py",
    "java_worker_source": REPO / "comsol_mcp/worker_java/PersistentComsolWorker.java",
}
# ControlDaemon's registry loads package modules beyond the narrow direct call
# path. Pin every local Python source module so imports from registry/plugin
# setup are covered even when they are not exercised by this one fixture call.
_frozen_package_paths = {path.resolve() for path in SOURCE_PATHS.values()}
for _package_path in sorted((REPO / "comsol_mcp").rglob("*.py")):
    if _package_path.name.startswith("._"):
        continue
    _resolved_package_path = _package_path.resolve()
    if _resolved_package_path not in _frozen_package_paths:
        _source_key = "runtime_package_" + _package_path.relative_to(REPO).as_posix().replace("/", "__")
        SOURCE_PATHS[_source_key] = _package_path
        _frozen_package_paths.add(_resolved_package_path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    sort_keys=True, default=str, allow_nan=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def append_event(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at_utc": utc_now(), "event": event, **fields},
                                ensure_ascii=False, sort_keys=True, default=str,
                                allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _mac_birth_epoch(raw: str) -> float:
    parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def _process_inventory() -> dict[str, Any]:
    """Fresh macOS COMSOL/Worker/listener inventory; failed probes block startup."""
    ps_command = ["/bin/ps", "-axo", "pid=,ppid=,comm=,args="]
    ps = subprocess.run(ps_command, capture_output=True, text=True, check=False, timeout=15)
    engines: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []
    processes: list[dict[str, Any]] = []
    for raw in ps.stdout.splitlines():
        fields = raw.strip().split(None, 3)
        if len(fields) < 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, ppid, comm, argv = int(fields[0]), int(fields[1]), fields[2], fields[3]
        processes.append({"pid": pid, "ppid": ppid, "comm": comm, "argv": argv})
        lowered = (comm + " " + argv).lower()
        is_engine = ("mphserver" in lowered or
                     ("/applications/comsol64/multiphysics/" in lowered and
                      ("java" in comm.lower() or "comsol" in lowered)))
        if is_engine:
            engines.append({"pid": pid, "ppid": ppid, "comm": comm, "argv": argv})
        if "persistentcomsolworker" in lowered or "comsolworker" in lowered:
            workers.append({"pid": pid, "ppid": ppid, "comm": comm, "argv": argv})
    lsof_command = ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]
    lsof = subprocess.run(lsof_command, capture_output=True, text=True, check=False, timeout=15)
    listeners = [line.strip() for line in lsof.stdout.splitlines()[1:]
                 if line.strip() and ("mphserver" in line.lower() or "comsol" in line.lower())]
    probes_ok = (ps.returncode == 0 and lsof.returncode == 0
                 and not ps.stderr.strip() and not lsof.stderr.strip())
    return {
        "at_utc": utc_now(),
        "ps": {"command": ps_command, "exit_code": ps.returncode,
               "stdout": ps.stdout, "stderr": ps.stderr},
        "lsof": {"command": lsof_command, "exit_code": lsof.returncode,
                 "stdout": lsof.stdout, "stderr": lsof.stderr},
        "comsol_engine_processes": engines,
        "persistent_worker_processes": workers,
        "processes": processes,
        "comsol_listener_rows": listeners,
        "probes_ok": probes_ok,
        "quiescent": probes_ok and not engines and not workers and not listeners,
    }


def _java_action_readback(response: dict[str, Any], label: str) -> dict[str, Any]:
    """Require a successful terminal managed Worker receipt and matching readback."""
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError(f"{label} operation failed: {json.dumps(response, ensure_ascii=False, default=str)[:5000]}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise AssertionError(f"{label} is missing operation data")
    worker = data.get("worker")
    wrapped = data.get("readback")
    if (not isinstance(worker, dict) or worker.get("ok") is not True
            or worker.get("status") != "SUCCEEDED" or not isinstance(wrapped, dict)
            or wrapped.get("executed") is not True
            or not isinstance(wrapped.get("readback"), dict)):
        raise AssertionError(f"{label} lacks an observed terminal successful Worker Java receipt")
    nested = wrapped["readback"]
    if worker.get("result", {}).get("readback") != nested:
        raise AssertionError(f"{label} Worker result and operation readback differ")
    return nested


def _validate_native_selection_readback(readback: dict[str, Any]) -> dict[str, Any]:
    """Require exact BoxSelection dimensions and nonempty native entity IDs."""
    port_settings = readback.get("port_selections")
    pml_settings = readback.get("pml_selection")
    port_entities = readback.get("port_entity_readback")
    pml_entities = readback.get("pml_entity_readback")
    if not isinstance(port_settings, list) or len(port_settings) != 2:
        raise AssertionError("native fixture omitted both Port BoxSelection property readbacks")
    if not isinstance(pml_settings, dict) or not isinstance(port_entities, list) or len(port_entities) != 2:
        raise AssertionError("native fixture omitted exact Port/PML entity selection readbacks")

    def validate_box(settings: Any, expected_dimension: int, tag: str) -> dict[str, float]:
        if not isinstance(settings, dict):
            raise AssertionError(f"{tag} BoxSelection readback is missing")
        props = settings.get("requested_properties")
        if not isinstance(props, dict):
            raise AssertionError(f"{tag} BoxSelection has no requested-property readback")
        dimension = props.get("entitydim")
        condition = props.get("condition")
        if not isinstance(dimension, dict) or dimension.get("has_property_exact") is not True:
            raise AssertionError(f"{tag} entitydim property was not confirmed exactly")
        try:
            observed_dimension = int(str(dimension.get("string_readback")))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{tag} entitydim readback is not an integer") from exc
        if observed_dimension != expected_dimension:
            raise AssertionError(f"{tag} entitydim is {observed_dimension}, expected {expected_dimension}")
        if (not isinstance(condition, dict) or condition.get("has_property_exact") is not True
                or str(condition.get("string_readback", "")).lower() != "inside"):
            raise AssertionError(f"{tag} BoxSelection condition was not read back as inside")
        bounds: dict[str, float] = {}
        for key in ("xmin", "xmax", "ymin", "ymax"):
            value = props.get(key)
            if not isinstance(value, dict) or value.get("has_property_exact") is not True:
                raise AssertionError(f"{tag} BoxSelection is missing exact {key} readback")
            try:
                bounds[key] = float(str(value.get("string_readback")))
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"{tag} {key} readback is not numeric") from exc
        return bounds

    def validate_entities(record: Any, expected_tag: str, dimension: int,
                          *, require_assignment: bool) -> list[int]:
        if not isinstance(record, dict) or record.get("selection_tag") != expected_tag:
            raise AssertionError(f"native entity readback is missing {expected_tag}")
        if record.get("entity_dimension") != dimension:
            raise AssertionError(f"{expected_tag} entity dimension is not {dimension}")
        ids = record.get("entity_ids")
        if (not isinstance(ids, list) or not ids
                or any(type(entity_id) is not int or entity_id <= 0 for entity_id in ids)
                or len(set(ids)) != len(ids)):
            raise AssertionError(f"{expected_tag} has no valid, unique native entity IDs")
        if record.get("entity_count") != len(ids):
            raise AssertionError(f"{expected_tag} native entity count does not match its IDs")
        if require_assignment:
            assigned = record.get("assigned_entity_ids")
            if (record.get("matches_assigned_selection") is not True
                    or assigned != ids
                    or record.get("assigned_entity_count") != len(ids)):
                raise AssertionError(f"{expected_tag} IDs do not match the assigned Port selection")
        return ids

    validate_box(port_settings[0], 1, "selInputPort")
    output_bounds = validate_box(port_settings[1], 1, "selOutputPort")
    validate_box(pml_settings, 2, "selPmlDomains")
    expected_semantics = {
        "scope": "API_PROPERTY_READBACK_ONLY",
        "api_only_output_port_x_um": 10.0,
        "science_receiver_plane_x_um": 8.0,
        "output_selection_is_science_receiver": False,
        "must_not_reuse_as_coupling_plane": True,
        "science_receiver_status": "REQUIRED_IN_LATER_SCIENCE_BUILDER_NOT_BUILT",
    }
    if readback.get("port_plane_semantics") != expected_semantics:
        raise AssertionError("fixture did not distinguish the API-only x=10 Port from the x=8 science receiver")
    if not (9.998 <= output_bounds["xmin"] < output_bounds["xmax"] <= 10.002
            and output_bounds["xmin"] <= 10.0 <= output_bounds["xmax"]):
        raise AssertionError("API-only output Port selection is not confined to the x=10 outer boundary")
    input_ids = validate_entities(port_entities[0], "selInputPort", 1, require_assignment=True)
    output_ids = validate_entities(port_entities[1], "selOutputPort", 1, require_assignment=True)
    domain_ids = validate_entities(pml_entities, "selPmlDomains", 2, require_assignment=False)
    if set(input_ids) & set(output_ids):
        raise AssertionError("input and output Port selections overlap in geometric entity IDs")
    return {"input_port_entity_count": len(input_ids),
            "output_port_entity_count": len(output_ids),
            "pml_domain_count": len(domain_ids)}


def frozen_hashes() -> dict[str, dict[str, Any]]:
    missing = [str(path) for path in SOURCE_PATHS.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("W23 frozen source closure is incomplete: " + ", ".join(missing))
    return {name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for name, path in SOURCE_PATHS.items()}


def _javac_tools() -> tuple[Path, Path]:
    javac, javap = JAVA11 / "bin/javac", JAVA11 / "bin/javap"
    if not javac.is_file() or not javap.is_file():
        raise FileNotFoundError(f"frozen Corretto 11 tools are unavailable: javac={javac}; javap={javap}")
    return javac, javap


def compile_fixture(class_dir: Path) -> dict[str, Any]:
    if class_dir.exists():
        raise FileExistsError(f"compile output must be new: {class_dir}")
    class_dir.mkdir(parents=True, exist_ok=False)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from comsol_mcp._java_worker import JavaWorkerPaths

    paths = JavaWorkerPaths(INSTALL_ROOT, JAVA11, project_root=REPO)
    classpath, manifest_hash, jar_count, jar_fingerprint = paths.classpath()
    javac, javap = _javac_tools()
    command = [str(javac), "-encoding", "UTF-8", "-classpath", classpath,
               "-d", str(class_dir), str(FIXTURE)]
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                            timeout=90, check=False)
    (class_dir.parent / "javac.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (class_dir.parent / "javac.stderr.txt").write_text(result.stderr, encoding="utf-8")
    classes = {path.relative_to(class_dir).as_posix(): {
        "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(class_dir.rglob("*.class")) if not path.name.startswith("._")}
    javac_version = subprocess.run([str(javac), "-version"], text=True,
                                   capture_output=True, timeout=10, check=False)
    javap_result = None
    if result.returncode == 0:
        javap_result = subprocess.run(
            [str(javap), "-classpath", classpath + os.pathsep + str(class_dir),
             "NativeW23TEFixture"], cwd=REPO, text=True, capture_output=True,
            timeout=20, check=False)
        (class_dir.parent / "javap.stdout.txt").write_text(javap_result.stdout, encoding="utf-8")
        (class_dir.parent / "javap.stderr.txt").write_text(javap_result.stderr, encoding="utf-8")
    return {
        "javac_command": command,
        "javac_version": (javac_version.stdout + javac_version.stderr).strip(),
        "comsol_version": paths.comsol_version_info(),
        "classpath_manifest_sha256": manifest_hash,
        "classpath_jar_count": jar_count,
        "classpath_jar_content_fingerprint_sha256": jar_fingerprint,
        "javac_returncode": result.returncode,
        "javac_stdout": result.stdout,
        "javac_stderr": result.stderr,
        "javap_returncode": javap_result.returncode if javap_result else None,
        "javap_stdout": javap_result.stdout if javap_result else None,
        "javap_stderr": javap_result.stderr if javap_result else None,
        "compiled_classes": classes,
    }


def prepare(evidence: Path) -> dict[str, Any]:
    if not evidence.as_posix().startswith(str(EVIDENCE_ROOT.resolve()) + "/"):
        raise ValueError("--evidence must be a new directory below the W23 evidence root")
    evidence.mkdir(parents=True, exist_ok=False)
    source_before = frozen_hashes()
    compile_receipt = compile_fixture(evidence / "offline_compile" / "classes")
    if compile_receipt["javac_returncode"] != 0 or compile_receipt["javap_returncode"] != 0:
        raise RuntimeError("offline Java compile/javap gate failed; no native process was started")
    if not compile_receipt["compiled_classes"]:
        raise RuntimeError("offline compile produced no Java class files")
    source_after = frozen_hashes()
    if source_before != source_after:
        raise RuntimeError("frozen source changed while compiling the API fixture")
    compile_receipt["evidence_sha256"] = {
        name: sha256(evidence / "offline_compile" / name)
        for name in ("javac.stdout.txt", "javac.stderr.txt", "javap.stdout.txt", "javap.stderr.txt")
        if (evidence / "offline_compile" / name).is_file()}
    write_json(evidence / "offline_compile" / "compile_receipt.json", compile_receipt)
    freeze = {
        "schema_version": 1,
        "status": "FROZEN_CANDIDATE_AWAITING_EXPLICIT_EXECUTION_SLOT",
        "candidate_id": CANDIDATE_ID,
        "created_utc": utc_now(),
        "scope": "managed native geometry/feature construction and exact API property readback only",
        "acceptance_boundary": {
            "study_or_solver_calls": 0,
            "numeric_port_value_confirmed": False,
            "native_integrals_or_overlap": "NOT_RUN",
            "native_result": "NOT_RUN",
            "scientific_or_physical_acceptance": "NOT_CLAIMED",
        },
        "limits": {
            "max_owned_server_processes": 1,
            "max_worker_sessions": 1,
            "max_managed_fixture_invocations": 1,
            "max_seconds_from_server_birth": MAX_BIRTH_BUDGET_S,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
            "study_or_solver_submissions": 0,
            "retry_unknown_or_nonterminal_worker_request": False,
        },
        "target": {
            "platform": "macOS",
            "comsol_root": str(INSTALL_ROOT),
            "required_engine_identity": "COMSOL Multiphysics 6.4.0.293",
            "jdk_home": str(JAVA11),
            "python_executable": sys.executable,
            "python_version": sys.version,
        },
        "source_files": source_before,
        "offline_compile": {
            "fixture_source_sha256": source_before["java_fixture"]["sha256"],
            "javac_version": compile_receipt["javac_version"],
            "comsol_version": compile_receipt["comsol_version"],
            "classpath_manifest_sha256": compile_receipt["classpath_manifest_sha256"],
            "classpath_jar_count": compile_receipt["classpath_jar_count"],
            "classpath_jar_content_fingerprint_sha256": compile_receipt["classpath_jar_content_fingerprint_sha256"],
            "compiled_classes": compile_receipt["compiled_classes"],
        },
        "evidence_dir": str(evidence),
    }
    write_json(evidence / "freeze.json", freeze)
    freeze["freeze_sha256"] = sha256(evidence / "freeze.json")
    write_json(evidence / "prepare_receipt.json", {
        "status": "PREPARED_NOT_EXECUTED",
        "freeze_path": str(evidence / "freeze.json"),
        "freeze_sha256": freeze["freeze_sha256"],
        "source_file_count": len(source_before),
        "compiled_class_count": len(compile_receipt["compiled_classes"]),
        "native_process_started": False,
        "study_or_solver_invoked": False,
    })
    return freeze


def _worker_request_terminal(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, dict) else None
    return isinstance(worker, dict) and worker.get("status") in WORKER_TERMINAL


def _invoke_once(dispatcher: Callable[..., Any], dispatch_args: dict[str, Any],
                 request_record: Path, request_identity: dict[str, str]) -> Any:
    """Persist one request identity before dispatch; an exception is UNKNOWN, never retried."""
    write_json(request_record, {
        "status": "SUBMITTING_ONCE",
        "request_identity": request_identity,
        "dispatch_count": 1,
        "started_utc": utc_now(),
        "unknown_policy": "NO_REDISPATCH; preserve server and inspect durable daemon/Worker receipt",
    })
    try:
        response = dispatcher(**dispatch_args)
    except BaseException as exc:
        write_json(request_record, {
            "status": "UNKNOWN",
            "request_identity": request_identity,
            "dispatch_count": 1,
            "finished_utc": utc_now(),
            "exception": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "retry": "FORBIDDEN",
        })
        raise
    terminal = _worker_request_terminal(response)
    write_json(request_record, {
        "status": "TERMINAL" if terminal else "UNKNOWN",
        "request_identity": request_identity,
        "dispatch_count": 1,
        "finished_utc": utc_now(),
        "worker_status": ((response.get("data") or {}).get("worker") or {}).get("status")
                         if isinstance(response, dict) and isinstance(response.get("data"), dict) else None,
        "response": response,
        "retry": "FORBIDDEN",
    })
    return response


def _page_project_jobs(store: Any, page_size: int = LEDGER_PAGE_SIZE) -> tuple[list[dict[str, Any]], int]:
    """Read every project job, honoring JobList.has_more instead of guessing."""
    if type(page_size) is not int or page_size < 1:
        raise ValueError("page_size must be a positive integer")
    rows: list[dict[str, Any]] = []
    pages = 0
    offset = 0
    while True:
        page = store.list_jobs(offset=offset, limit=page_size, project_id=PROJECT_ID)
        if not isinstance(page, list):
            raise TypeError(f"OperationStore.list_jobs returned non-list page: {type(page).__name__}")
        page_rows = list(page)
        pages += 1
        rows.extend(page_rows)
        has_more = getattr(page, "has_more", None)
        if has_more is False or (has_more is None and len(page_rows) < page_size):
            break
        if not page_rows:
            break
        offset += len(page_rows)
    return rows, pages


def _page_job_events(store: Any, job_id: str, page_size: int = LEDGER_PAGE_SIZE) -> tuple[list[dict[str, Any]], int]:
    if type(page_size) is not int or page_size < 1:
        raise ValueError("page_size must be a positive integer")
    rows: list[dict[str, Any]] = []
    pages = 0
    offset = 0
    while True:
        page = store.events(job_id, offset=offset, limit=page_size)
        if not isinstance(page, list):
            raise TypeError(f"OperationStore.events returned non-list page: {type(page).__name__}")
        pages += 1
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows, pages


def _all_project_jobs(store: Any) -> list[dict[str, Any]]:
    return _page_project_jobs(store)[0]


def _all_job_events(store: Any, job_id: str) -> list[dict[str, Any]]:
    return _page_job_events(store, job_id)[0]


def _worker_event_identity(metadata: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None, None
    nested = metadata.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    request_id = metadata.get("request_id") or nested.get("request_id")
    phase = metadata.get("phase")
    status = metadata.get("status")
    if not status:
        reply = metadata.get("reply")
        if isinstance(reply, dict):
            status = reply.get("status")
    return (request_id if isinstance(request_id, str) and request_id else None,
            phase if isinstance(phase, str) else None,
            status.upper() if isinstance(status, str) else None)


def _contains_unknown_domain_state(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("execution_state_unknown") is True or value.get("requires_model_reconciliation") is True:
            return True
        if value.get("domain_state") == "unknown":
            return True
        outcome = value.get("domain_outcome")
        if isinstance(outcome, dict) and outcome.get("state") == "unknown":
            return True
        return any(_contains_unknown_domain_state(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_unknown_domain_state(item) for item in value)
    return False


def _ledger_reconciliation(daemon: Any, *, page_size: int = LEDGER_PAGE_SIZE) -> dict[str, Any]:
    """Read the complete managed ledger without changing UNKNOWN job/domain state.

    Cleanup may be safe when the operation row remains UNKNOWN if every
    submitted Worker request has exactly one terminal observation and there
    are no queued/running jobs, orphan observations, or ambiguous identities.
    This is only a process-cleanup decision; it never resolves the model result.
    """
    if daemon is None:
        jobs: list[dict[str, Any]] = []
        job_pages = 0
    else:
        jobs, job_pages = _page_project_jobs(daemon.store, page_size=page_size)
    event_pages_by_job: dict[str, int] = {}
    raw_pages: list[dict[str, Any]] = []
    worker_events: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job.get("job_id", ""))
        events, event_pages = _page_job_events(daemon.store, job_id, page_size=page_size)
        event_pages_by_job[job_id] = event_pages
        raw_pages.append({**job, "events": events})
        for event in events:
            if event.get("event") != "worker_request":
                continue
            metadata = event.get("metadata")
            request_id, phase, status = _worker_event_identity(metadata)
            if phase not in {"submitted", "observed"}:
                continue
            meta = metadata if isinstance(metadata, dict) else {}
            nested = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
            worker_events.append({
                "event_id": event.get("id"), "job_id": job_id,
                "request_id": request_id, "phase": phase, "status": status,
                "kind": meta.get("kind") or nested.get("type"),
                "method": nested.get("method"),
            })

    submitted_by_id: dict[str, list[dict[str, Any]]] = {}
    observed_by_id: dict[str, list[dict[str, Any]]] = {}
    invalid_identity_events: list[dict[str, Any]] = []
    for event in worker_events:
        request_id = event.get("request_id")
        if not request_id:
            invalid_identity_events.append(event)
            continue
        target = submitted_by_id if event["phase"] == "submitted" else observed_by_id
        target.setdefault(request_id, []).append(event)

    pending: list[dict[str, Any]] = []
    orphan_observations: list[dict[str, Any]] = []
    ambiguous_ids: list[dict[str, Any]] = list(invalid_identity_events)
    nonterminal: list[dict[str, Any]] = []
    worker_requests: list[dict[str, Any]] = []
    for request_id in sorted(set(submitted_by_id) | set(observed_by_id)):
        submissions = submitted_by_id.get(request_id, [])
        observations = observed_by_id.get(request_id, [])
        submission_jobs = {row["job_id"] for row in submissions}
        observation_jobs = {row["job_id"] for row in observations}
        same_job = len(submission_jobs | observation_jobs) <= 1
        statuses = [row.get("status") for row in observations]
        record = {"request_id": request_id,
                  "job_ids": sorted(submission_jobs | observation_jobs),
                  "submitted_count": len(submissions),
                  "observed_count": len(observations),
                  "observed_statuses": statuses,
                  "terminal": (len(submissions) == 1 and len(observations) == 1
                               and same_job and statuses[0] in WORKER_TERMINAL)}
        worker_requests.append(record)
        if not submissions:
            orphan_observations.append(record)
        elif not observations:
            pending.append(record)
        if (len(submissions) > 1 or len(observations) > 1 or not same_job
                or (submissions and observations and submission_jobs != observation_jobs)):
            ambiguous_ids.append(record)
        if submissions and not record["terminal"]:
            nonterminal.append(record)

    active_jobs = [job for job in jobs if job.get("status") in {"QUEUED", "RUNNING"}]
    unknown_jobs = [job for job in jobs if job.get("status") == "UNKNOWN"
                    or _contains_unknown_domain_state(job.get("result"))]
    fixture_request_ids = {event["request_id"] for event in worker_events
                           if event.get("kind") == "code_execute"
                           and event.get("phase") == "submitted" and event.get("request_id")}
    terminal_request_ids = {row["request_id"] for row in worker_requests if row.get("terminal")}
    fixture_requests_terminal = (len(fixture_request_ids) == 1
                                 and fixture_request_ids <= terminal_request_ids)
    checks = {
        "all_submissions_observed_once": not pending and not nonterminal and not ambiguous_ids,
        "all_observed_workers_terminal": not nonterminal and not orphan_observations,
        "no_orphan_observations": not orphan_observations,
        "no_ambiguous_worker_ids": not ambiguous_ids,
        "no_queued_or_running_jobs": not active_jobs,
        "exactly_one_fixture_request_terminal": fixture_requests_terminal,
        "unknown_result_preserved": True,
    }
    safe_for_cleanup = all(checks[key] for key in (
        "all_submissions_observed_once", "all_observed_workers_terminal",
        "no_orphan_observations", "no_ambiguous_worker_ids", "no_queued_or_running_jobs"))
    if not safe_for_cleanup:
        ledger_status = "BLOCKED_LEDGER_NOT_QUIESCENT_OR_AMBIGUOUS"
    elif unknown_jobs:
        ledger_status = "PASS_TERMINAL_WORKER_LEDGER_RECONCILED_DURABLE_DOMAIN_UNKNOWN_RETAINED"
    else:
        ledger_status = "PASS_TERMINAL_WORKER_LEDGER_RECONCILED_NO_UNKNOWN_DOMAIN_STATE"
    return {
        "status": ledger_status,
        "created_utc": utc_now(), "project_id": PROJECT_ID,
        "pagination": {"page_size": page_size, "job_pages": job_pages,
                        "job_count": len(jobs), "event_pages_by_job": event_pages_by_job,
                        "event_count": sum(len(row["events"]) for row in raw_pages)},
        "complete_raw_job_and_event_pages": raw_pages,
        "worker_events": worker_events, "worker_requests": worker_requests,
        "submitted_count": sum(len(rows) for rows in submitted_by_id.values()),
        "observed_count": sum(len(rows) for rows in observed_by_id.values()),
        "pending_submissions": pending, "orphan_observations": orphan_observations,
        "ambiguous_ids": ambiguous_ids, "nonterminal_requests": nonterminal,
        "active_queued_or_running_jobs": active_jobs,
        "durable_unknown_jobs": [{"job_id": row.get("job_id"),
                                  "operation_id": row.get("operation_id"),
                                  "status": row.get("status"),
                                  "domain_state_was_not_rewritten": True}
                                 for row in unknown_jobs],
        "durable_result": {"domain_outcome": "UNKNOWN" if unknown_jobs else "NOT_UNKNOWN",
                           "execution_state_unknown": bool(unknown_jobs),
                           "domain_state_was_not_rewritten": True,
                           "safe_retry": False if unknown_jobs else None},
        "fixture_request_ids": sorted(fixture_request_ids),
        "checks": checks, "safe_for_owned_cleanup": safe_for_cleanup,
    }


def _backup_operation_store(daemon: Any, destination: Path) -> dict[str, Any]:
    """Create a consistent read-only SQLite snapshot for the native evidence."""
    store = getattr(daemon, "store", None)
    source_db = getattr(store, "db", None)
    if source_db is None or not hasattr(source_db, "backup"):
        raise TypeError("OperationStore does not expose the expected SQLite backup API")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(str(destination))
    try:
        source_db.backup(backup)
    finally:
        backup.close()
    return {"path": str(destination), "bytes": destination.stat().st_size,
            "sha256": sha256(destination), "consistent_sqlite_backup": True}


def _is_native_study_run_submission(event: Any) -> bool:
    if not isinstance(event, dict) or event.get("phase") != "submitted":
        return False
    worker_request = event.get("metadata")
    return (event.get("kind") == "call" and isinstance(worker_request, dict)
            and worker_request.get("type") == "call"
            and worker_request.get("method") == "run")


def _actual_study_run_submissions(daemon: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in _all_project_jobs(daemon.store):
        for event in _all_job_events(daemon.store, job["job_id"]):
            metadata = event.get("metadata")
            if event.get("event") == "worker_request" and _is_native_study_run_submission(metadata):
                rows.append({"job_id": job["job_id"], "status": job.get("status"),
                             "worker_event": metadata})
    return rows


def _daemon_idle_proof(daemon: Any) -> dict[str, Any]:
    if daemon is None:
        return {"idle": True, "reason": "daemon_not_created", "jobs": []}
    jobs = _all_project_jobs(daemon.store)
    nonterminal = [job for job in jobs if job.get("status") not in TERMINAL]
    requests: list[dict[str, Any]] = []
    unresolved = False
    for job in jobs:
        events = _all_job_events(daemon.store, job["job_id"])
        submitted = {row.get("metadata", {}).get("request_id")
                     for row in events if row.get("event") == "worker_request"
                     and row.get("metadata", {}).get("phase") == "submitted"
                     and row.get("metadata", {}).get("request_id")}
        observed = {row.get("metadata", {}).get("request_id"): row.get("metadata", {}).get("status")
                    for row in events if row.get("event") == "worker_request"
                    and row.get("metadata", {}).get("phase") == "observed"
                    and row.get("metadata", {}).get("request_id")}
        for request_id in submitted:
            status = observed.get(request_id)
            requests.append({"job_id": job["job_id"], "request_id": request_id,
                             "observed_status": status})
            if status not in WORKER_TERMINAL:
                unresolved = True
    idle = not nonterminal and not unresolved
    return {"idle": idle,
            "reason": "all_jobs_and_worker_requests_terminal" if idle else "nonterminal_or_unknown_work",
            "jobs": jobs, "worker_requests": requests,
            "nonterminal_jobs": nonterminal}


def _current_owned_identity(server: Any, process_snapshot: Callable[[int], Any]) -> dict[str, Any] | None:
    if server.proc is None:
        return None
    return process_snapshot(server.proc.pid)


def _parse_lsof_listener(stdout: str) -> tuple[int | None, str]:
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


def _listener_matches(server: Any) -> dict[str, Any]:
    if server.proc is None or server.port is None:
        return {"matches": False, "reason": "server process or port missing"}
    command = ["/usr/sbin/lsof", "-nP", "-a", "-p", str(server.proc.pid),
               f"-iTCP:{server.port}", "-sTCP:LISTEN"]
    probe = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    rows = [line for line in probe.stdout.splitlines()[1:] if line.strip()]
    pid, endpoint = _parse_lsof_listener(probe.stdout)
    return {"matches": probe.returncode == 0 and len(rows) == 1 and pid == server.proc.pid
            and endpoint == f"127.0.0.1:{server.port}",
            "command": command, "returncode": probe.returncode, "stdout": probe.stdout,
            "stderr": probe.stderr, "pid": pid, "endpoint": endpoint}


def _verify_cleanup(server: Any, original: dict[str, Any],
                    process_snapshot: Callable[[int], Any]) -> dict[str, Any]:
    if server.proc is None:
        return {"status": "NOT_STARTED"}
    if server.proc.poll() is None:
        return {"status": "STOP_UNVERIFIED", "reason": "owned child remains alive"}
    current = _current_owned_identity(server, process_snapshot)
    listener = _listener_matches(server)
    identity_same = bool(current and current.get("birth") == original.get("birth")
                         and current.get("command") == original.get("command"))
    if current is not None and identity_same:
        process_state = "ORIGINAL_PROCESS_STILL_PRESENT"
    elif current is not None:
        process_state = "PID_REUSED_BY_DIFFERENT_IDENTITY"
    else:
        process_state = "ORIGINAL_PID_ABSENT"
    return {
        "status": "STOPPED_AND_LISTENER_ABSENT" if current is None and not listener["matches"]
                 else "CLEANUP_UNVERIFIED",
        "owned_identity": original,
        "current_pid_identity": current,
        "process_state": process_state,
        "listener_probe": listener,
        "port": server.port,
    }


def _process_identity_matches(expected: Any, observed: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    for key in ("pid", "birth", "command"):
        if expected.get(key) is None or observed.get(key) != expected.get(key):
            return False
    expected_hash = expected.get("command_sha256")
    if expected_hash is not None and observed.get("command_sha256") != expected_hash:
        return False
    return True


def _ledger_allows_owned_cleanup(reconciliation: Any, *, require_fixture_terminal: bool = False) -> bool:
    if not isinstance(reconciliation, dict) or reconciliation.get("safe_for_owned_cleanup") is not True:
        return False
    checks = reconciliation.get("checks")
    base = (isinstance(checks, dict)
            and checks.get("all_submissions_observed_once") is True
            and checks.get("all_observed_workers_terminal") is True
            and checks.get("no_orphan_observations") is True
            and checks.get("no_ambiguous_worker_ids") is True
            and checks.get("no_queued_or_running_jobs") is True)
    if not base:
        return False
    return (not require_fixture_terminal
            or checks.get("exactly_one_fixture_request_terminal") is True)


def _task_engine_descendants(inventory: dict[str, Any], root_pid: int,
                             task_work: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return exact task-path COMSOL descendants and fail-closed ambiguous ones."""
    rows = inventory.get("processes")
    if not isinstance(rows, list):
        return [], [{"reason": "process rows missing from inventory"}]
    by_pid = {row.get("pid"): row for row in rows if isinstance(row, dict)
              and type(row.get("pid")) is int and type(row.get("ppid")) is int}
    descendants: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    depths = {root_pid: 0}
    while True:
        found = False
        for row in rows:
            if (not isinstance(row, dict) or row.get("pid") in depths
                    or row.get("ppid") not in depths):
                continue
            pid = row.get("pid")
            depths[pid] = depths[row["ppid"]] + 1
            argv = str(row.get("argv", ""))
            lowered = (str(row.get("comm", "")) + " " + argv).lower()
            is_comsol = "mphserver" in lowered or "comsol" in lowered
            if is_comsol:
                if str(task_work) not in argv:
                    ambiguous.append({"pid": pid, "ppid": row.get("ppid"),
                                      "reason": "COMSOL descendant does not carry exact task work path"})
                else:
                    identity = by_pid.get(pid)
                    if identity is None:
                        ambiguous.append({"pid": pid, "reason": "descendant identity row unavailable"})
                    else:
                        descendants.append(identity)
            found = True
        if not found:
            break
    descendants.sort(key=lambda row: depths.get(row.get("pid"), 0), reverse=True)
    return descendants, ambiguous


def _port_listener_probe(port: int | None) -> dict[str, Any]:
    if type(port) is not int or not 1 <= port <= 65535:
        return {"absent": False, "reason": "port unavailable", "port": port}
    command = ["/usr/sbin/lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN"]
    probe = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    rows = [line.strip() for line in probe.stdout.splitlines()[1:] if line.strip()]
    absent = probe.returncode == 1 and not rows and not probe.stderr.strip()
    return {"port": port, "command": command, "returncode": probe.returncode,
            "stdout": probe.stdout, "stderr": probe.stderr,
            "rows": rows, "absent": absent}


def _wait_identity_absent(pid: int, expected: dict[str, Any],
                          process_snapshot: Callable[[int], Any], deadline: float) -> dict[str, Any]:
    while True:
        current = process_snapshot(pid)
        if current is None:
            return {"pid": pid, "original_identity_absent": True, "current_identity": None}
        if not _process_identity_matches(expected, current):
            return {"pid": pid, "original_identity_absent": True,
                    "pid_reused_by_different_identity": True, "current_identity": current}
        if time.monotonic() >= deadline:
            return {"pid": pid, "original_identity_absent": False,
                    "current_identity": current}
        time.sleep(0.2)


def _exact_owned_cleanup(server: Any, *, server_identity: dict[str, Any],
                         worker_identity: dict[str, Any] | None,
                         worker_port: int | None,
                         process_snapshot: Callable[[int], Any],
                         reconciliation: dict[str, Any], evidence: Path,
                         require_fixture_terminal: bool = False) -> dict[str, Any]:
    """TERM only freshly matched task-owned identities after full ledger proof.

    This deliberately never escalates to SIGKILL. An unresolved request,
    active job, identity drift, or listener mismatch leaves the private server
    running with a durable failure receipt for human reconciliation.
    """
    receipt: dict[str, Any] = {
        "at_utc": utc_now(), "status": "CLEANUP_BLOCKED",
        "durable_unknown_preserved": True,
        "durable_unknown": reconciliation.get("durable_result"),
        "ledger_status": reconciliation.get("status"),
        "ledger_safe_for_owned_cleanup": _ledger_allows_owned_cleanup(
            reconciliation, require_fixture_terminal=require_fixture_terminal),
        "actions": [],
    }
    if not _ledger_allows_owned_cleanup(reconciliation, require_fixture_terminal=require_fixture_terminal):
        receipt["reason"] = "full Worker ledger does not prove terminal quiescence"
        write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt
    if server.proc is None:
        receipt.update({"status": "NOT_STARTED", "reason": "server process was not started"})
        write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt

    inventory = _process_inventory()
    receipt["precleanup_inventory"] = inventory
    if not inventory.get("probes_ok"):
        receipt["reason"] = "fresh process/listener inventory failed"
        write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt

    server_pid = server.proc.pid
    server_current = process_snapshot(server_pid)
    server_alive = server.proc.poll() is None
    listener = _listener_matches(server)
    receipt["identities"] = {"server_expected": server_identity,
                             "server_current": server_current,
                             "server_listener": listener}
    if server_alive and (not _process_identity_matches(server_identity, server_current)
                         or not listener.get("matches")):
        receipt["reason"] = "owned server PID/birth/command/listener identity changed"
        write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt

    worker_proc = getattr(getattr(server, "worker", None), "_process", None)
    worker_current = None
    if worker_proc is not None and worker_proc.poll() is None:
        worker_pid = worker_proc.pid
        worker_current = process_snapshot(worker_pid)
        if not _process_identity_matches(worker_identity, worker_current):
            receipt["reason"] = "owned Worker PID/birth/command identity changed or unavailable"
            receipt["identities"]["worker_current"] = worker_current
            write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
            return receipt
    elif worker_identity is not None:
        receipt["identities"]["worker_current"] = process_snapshot(worker_identity.get("pid"))

    descendants, ambiguous_descendants = _task_engine_descendants(inventory, server_pid, server.work)
    descendant_identities: list[dict[str, Any]] = []
    for row in descendants:
        identity = process_snapshot(row["pid"])
        if (not isinstance(identity, dict) or identity.get("pid") != row.get("pid")
                or not isinstance(identity.get("birth"), str)
                or not isinstance(identity.get("command"), str)
                or str(server.work) not in identity.get("command", "")):
            receipt["reason"] = "task descendant identity could not be captured exactly"
            receipt["identities"]["descendant_failure"] = {"row": row, "identity": identity}
            write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
            return receipt
        descendant_identities.append(identity)
    receipt["identities"]["worker_expected"] = worker_identity
    receipt["identities"]["worker_current"] = worker_current
    receipt["identities"]["task_engine_descendants"] = descendant_identities
    receipt["ambiguous_descendants"] = ambiguous_descendants
    if ambiguous_descendants:
        receipt["reason"] = "COMSOL descendant attribution is ambiguous"
        write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt

    # Persist intent and all identities before sending any signal.
    receipt["status"] = "TERM_INTENT_RECORDED"
    write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
    cleanup_deadline = time.monotonic() + EXACT_CLEANUP_GRACE_S

    def term_exact(pid: int, expected: dict[str, Any], *, popen: Any = None, role: str) -> dict[str, Any]:
        current = process_snapshot(pid)
        if current is None:
            return {"pid": pid, "role": role, "status": "ALREADY_ABSENT"}
        if not _process_identity_matches(expected, current):
            return {"pid": pid, "role": role, "status": "IDENTITY_CHANGED_NO_SIGNAL",
                    "current_identity": current}
        try:
            if popen is not None:
                if popen.poll() is not None:
                    return {"pid": pid, "role": role, "status": "ALREADY_EXITED"}
                popen.terminate()
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return {"pid": pid, "role": role, "status": "ALREADY_ABSENT"}
        except BaseException as exc:
            return {"pid": pid, "role": role, "status": "SIGNAL_FAILED",
                    "error": f"{type(exc).__name__}: {exc}"}
        return {"at_utc": utc_now(), "pid": pid, "role": role, "signal": "SIGTERM",
                "status": "SIGNAL_SENT", "identity": expected}

    # Stop the one Worker first, then descendants deepest-first, and the owned
    # server last. UNKNOWN remains durable regardless of process cleanup.
    if worker_proc is not None and worker_proc.poll() is None and worker_identity is not None:
        receipt["actions"].append(term_exact(worker_proc.pid, worker_identity,
                                               popen=worker_proc, role="PersistentJavaWorker"))
    for identity in reversed(descendant_identities):
        receipt["actions"].append(term_exact(identity["pid"], identity,
                                               role="task-owned COMSOL descendant"))
    if server.proc.poll() is None:
        receipt["actions"].append(term_exact(server_pid, server_identity,
                                               popen=server.proc, role="owned COMSOL server"))

    # Wait without escalation. Popen.wait is used only for the exact child
    # objects created by this runner; descendants are checked by full identity.
    for proc in (worker_proc, server.proc):
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=max(0.0, min(10.0, cleanup_deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
    absent: dict[str, Any] = {}
    expected_processes = [(server_pid, server_identity, "server")]
    if worker_identity is not None:
        expected_processes.append((worker_identity["pid"], worker_identity, "worker"))
    expected_processes.extend((identity["pid"], identity, f"descendant_{index}")
                              for index, identity in enumerate(descendant_identities))
    for pid, identity, label in expected_processes:
        absent[label] = _wait_identity_absent(pid, identity, process_snapshot, cleanup_deadline)

    if worker_proc is not None and worker_proc.poll() is not None:
        try:
            server.worker.close()
        except BaseException as exc:
            receipt["worker_local_close_error"] = f"{type(exc).__name__}: {exc}"
    try:
        if hasattr(server, "_server_log_handle"):
            server._server_log_handle.close()
    except BaseException as exc:
        receipt["server_log_close_error"] = f"{type(exc).__name__}: {exc}"

    server_port = _port_listener_probe(server.port)
    worker_port_probe = ({"port": None, "absent": True, "not_started": True}
                         if worker_port is None else _port_listener_probe(worker_port))
    post_inventory = _process_inventory()
    try:
        write_json(evidence / "postcleanup_inventory.json", post_inventory)
    except BaseException as exc:
        receipt["postcleanup_inventory_write_error"] = f"{type(exc).__name__}: {exc}"
    receipt["postcheck"] = {
        "pids_absent": absent,
        "server_port": server_port,
        "worker_port": worker_port_probe,
        "global_inventory": {"probes_ok": post_inventory.get("probes_ok"),
                             "quiescent": post_inventory.get("quiescent"),
                             "engine_pids": [row.get("pid") for row in post_inventory.get("comsol_engine_processes", [])],
                             "worker_pids": [row.get("pid") for row in post_inventory.get("persistent_worker_processes", [])]},
    }
    absent_ok = all(value.get("original_identity_absent") is True for value in absent.values())
    verified = (absent_ok and server_port.get("absent") is True
                and worker_port_probe.get("absent") is True
                and post_inventory.get("probes_ok") is True
                and post_inventory.get("quiescent") is True)
    receipt["status"] = ("CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT"
                         if verified else "CLEANUP_UNVERIFIED")
    receipt["durable_unknown_preserved"] = True
    receipt["finished_utc"] = utc_now()
    write_json(evidence / "exact_owned_cleanup_receipt.json", receipt)
    return receipt


def _approve_freeze(freeze_path: Path, expected_sha256: str) -> dict[str, Any]:
    if not freeze_path.as_posix().startswith(str(EVIDENCE_ROOT.resolve()) + "/") \
            or freeze_path.name != "freeze.json":
        raise RuntimeError("freeze must be the generated freeze.json under the W23 evidence root")
    observed = sha256(freeze_path)
    if observed != expected_sha256:
        raise RuntimeError(f"freeze hash mismatch: expected {expected_sha256}, observed {observed}")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("status") != "FROZEN_CANDIDATE_AWAITING_EXPLICIT_EXECUTION_SLOT":
        raise RuntimeError("freeze status is not an executable prepared candidate")
    if freeze.get("candidate_id") != CANDIDATE_ID:
        raise RuntimeError("freeze candidate ID does not match the W23 TE API readback campaign")
    if freeze.get("scope") != "managed native geometry/feature construction and exact API property readback only":
        raise RuntimeError("freeze scope differs from the authorized no-solve property-readback scope")
    target = freeze.get("target", {})
    if (target.get("platform") != "macOS"
            or target.get("comsol_root") != str(INSTALL_ROOT)
            or target.get("required_engine_identity") != "COMSOL Multiphysics 6.4.0.293"
            or target.get("jdk_home") != str(JAVA11)
            or target.get("python_executable") != sys.executable
            or target.get("python_version") != sys.version):
        raise RuntimeError("platform, engine/JDK, or Python environment differs from the reviewed freeze")
    if freeze.get("limits", {}).get("study_or_solver_submissions") != 0:
        raise RuntimeError("freeze does not prohibit all study/solver submissions")
    limits = freeze.get("limits", {})
    if (limits.get("max_owned_server_processes") != 1
            or limits.get("max_worker_sessions") != 1
            or limits.get("max_managed_fixture_invocations") != 1
            or limits.get("max_seconds_from_server_birth") != MAX_BIRTH_BUDGET_S
            or limits.get("cleanup_reserve_seconds") != CLEANUP_RESERVE_S
            or limits.get("retry_unknown_or_nonterminal_worker_request") is not False):
        raise RuntimeError("freeze budget or unknown-request stop rules differ from this runner")
    boundary = freeze.get("acceptance_boundary", {})
    if (boundary.get("numeric_port_value_confirmed") is not False
            or boundary.get("native_integrals_or_overlap") != "NOT_RUN"
            or boundary.get("native_result") != "NOT_RUN"
            or boundary.get("scientific_or_physical_acceptance") != "NOT_CLAIMED"):
        raise RuntimeError("freeze acceptance boundary is missing required NOT_RUN/NOT_CLAIMED limits")
    current = frozen_hashes()
    if current != freeze.get("source_files"):
        raise RuntimeError("one or more frozen sources changed after prepare; create and review a new candidate")
    offline = freeze.get("offline_compile", {})
    if not offline.get("compiled_classes") or not offline.get("classpath_manifest_sha256"):
        raise RuntimeError("freeze is missing its offline compilation/classpath receipt")
    class_dir = freeze_path.parent / "offline_compile" / "classes"
    for relative, receipt in offline["compiled_classes"].items():
        class_file = class_dir / relative
        if not class_file.is_file() or class_file.stat().st_size != receipt.get("bytes") \
                or sha256(class_file) != receipt.get("sha256"):
            raise RuntimeError(f"frozen compiled class is missing or changed: {class_file}")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from comsol_mcp._java_worker import JavaWorkerPaths
    installed_paths = JavaWorkerPaths(INSTALL_ROOT, JAVA11, project_root=REPO)
    _, manifest_hash, jar_count, jar_fingerprint = installed_paths.classpath()
    if (manifest_hash != offline.get("classpath_manifest_sha256")
            or jar_count != offline.get("classpath_jar_count")
            or jar_fingerprint != offline.get("classpath_jar_content_fingerprint_sha256")):
        raise RuntimeError("installed COMSOL client classpath differs from the frozen compile receipt")
    return freeze


def _load_managed_runtime_helpers() -> dict[str, Any]:
    """Import and resolve every external runtime helper before freeze/launch gates."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._g2_isolation import _process_snapshot, verify_owned_server
    from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker
    from tools.run_native_resume_smoke import (
        NativeLoopbackServer, _bind_model, _dispatch, _engine_build_identity,
    )
    return {
        "ControlDaemon": ControlDaemon,
        "JavaWorkerPaths": JavaWorkerPaths,
        "PersistentJavaWorker": PersistentJavaWorker,
        "_process_snapshot": _process_snapshot,
        "verify_owned_server": verify_owned_server,
        "NativeLoopbackServer": NativeLoopbackServer,
        "_bind_model": _bind_model,
        "_dispatch": _dispatch,
        "_engine_build_identity": _engine_build_identity,
    }


def execute(freeze_path: Path, expected_freeze_sha256: str, work: Path,
            evidence: Path) -> dict[str, Any]:
    helpers = _load_managed_runtime_helpers()
    freeze = _approve_freeze(freeze_path, expected_freeze_sha256)
    NativeLoopbackServer = helpers["NativeLoopbackServer"]
    _bind_model = helpers["_bind_model"]
    _dispatch = helpers["_dispatch"]
    _engine_build_identity = helpers["_engine_build_identity"]
    ControlDaemon = helpers["ControlDaemon"]
    if not work.as_posix().startswith("/private/tmp/comsol-mcp-w23-te-api-"):
        raise ValueError("--work must be a new task-owned /private/tmp/comsol-mcp-w23-te-api-* path")
    if not evidence.as_posix().startswith(str(EVIDENCE_ROOT.resolve()) + "/"):
        raise ValueError("--evidence must be a new directory below the W23 evidence root")
    if work.exists() or evidence.exists():
        raise FileExistsError("work and evidence paths must be new; no existing data will be overwritten")
    if sys.platform != "darwin":
        raise RuntimeError(f"native W23 API readback requires macOS, observed {sys.platform}")

    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    event_log = evidence / "events.jsonl"
    summary: dict[str, Any] = {
        "status": "FAIL_OR_INCOMPLETE",
        "candidate_id": freeze["candidate_id"],
        "scope": "managed 2D TE feature/geometry API property readback only",
        "study_or_solver_invoked": "UNKNOWN",
        "native_result": "NOT_RUN",
        "freeze_path": str(freeze_path),
        "freeze_sha256": expected_freeze_sha256,
    }
    server = None
    daemon = None
    request_terminal = True
    worker_connected = False
    fixture_dispatch_started = False
    original_process_identity: dict[str, Any] | None = None
    worker_process_identity: dict[str, Any] | None = None
    worker_port: int | None = None
    deadline_epoch: float | None = None
    cleanup: dict[str, Any] | None = None
    request_id = f"w23-api-request-{uuid4()}"
    idempotency_key = f"w23-api-idempotency-{uuid4()}"
    try:
        if not INSTALL_ROOT.is_dir() or not JAVA11.is_dir():
            raise FileNotFoundError("frozen COMSOL 6.4 or Corretto 11 installation is unavailable")
        prelaunch = _process_inventory()
        write_json(evidence / "prelaunch_inventory.json", prelaunch)
        if not prelaunch["quiescent"]:
            raise RuntimeError("fresh process/listener inventory is unavailable or not quiescent; no server started")
        if frozen_hashes() != freeze["source_files"]:
            raise RuntimeError("frozen source closure changed immediately before launch")

        server = NativeLoopbackServer(work, evidence, event_log=event_log)
        shadow = server.prepare_shadow()
        write_json(evidence / "private_shadow_receipt.json", shadow)
        birth_inventory = _process_inventory()
        write_json(evidence / "prebirth_inventory.json", birth_inventory)
        if not birth_inventory["quiescent"]:
            raise RuntimeError("fresh pre-birth inventory is not quiescent; no owned server started")
        listener = server.start_and_verify_listener()
        original_process_identity = server.process_identity or {}
        birth = original_process_identity.get("birth")
        if not isinstance(birth, str):
            raise RuntimeError("owned engine birth identity is missing")
        birth_epoch = _mac_birth_epoch(birth)
        deadline_epoch = birth_epoch + MAX_BIRTH_BUDGET_S
        write_json(evidence / "server_birth.json", {
            "status": "OWNED_SERVER_BIRTH_VERIFIED",
            "process_identity": original_process_identity,
            "listener": listener,
            "birth_epoch": birth_epoch,
            "deadline_epoch": deadline_epoch,
            "budget_seconds": MAX_BIRTH_BUDGET_S,
            "freeze_sha256": expected_freeze_sha256,
        })
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("native birth budget reached cleanup reserve before Worker startup")

        worker_info = server.start_worker()
        worker_connected = True
        worker_runtime = worker_info.get("worker_runtime", {})
        worker_pid = worker_runtime.get("pid") if isinstance(worker_runtime, dict) else None
        worker_port_value = worker_runtime.get("port") if isinstance(worker_runtime, dict) else None
        if type(worker_pid) is not int or type(worker_port_value) is not int:
            raise RuntimeError("Worker PID/port identity is missing from the owned Worker runtime receipt")
        worker_process_identity = helpers["_process_snapshot"](worker_pid)
        worker_port = worker_port_value
        if not isinstance(worker_process_identity, dict):
            raise RuntimeError("Worker PID/birth/command identity could not be captured")
        worker_info["worker_process_identity"] = worker_process_identity
        engine_identity = _engine_build_identity(worker_info.get("engine_version"))
        write_json(evidence / "worker_connection.json", worker_info)
        if not engine_identity.get("matches_frozen_target"):
            raise RuntimeError(f"connected COMSOL engine does not match 6.4.0.293: {engine_identity}")

        os.environ["COMSOL_ROOT"] = str(server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(JAVA11)
        os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
        os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)
        daemon = ControlDaemon(work / "control", worker=server.worker, project_root=server.project)
        request_terminal = False
        connect_response = _dispatch(
            daemon, "server_connect", {"host": "127.0.0.1", "port": server.port},
            project_id=PROJECT_ID, idempotency_key=f"w23-connect-{uuid4()}")
        write_json(evidence / "managed_connection.json", connect_response)
        if (connect_response.get("success") is not True
                or connect_response.get("data", {}).get("endpoint") != f"127.0.0.1:{server.port}"):
            raise RuntimeError("managed server connection did not verify the owned loopback endpoint")
        request_terminal = True

        if frozen_hashes() != freeze["source_files"]:
            raise RuntimeError("frozen source closure changed before managed Java invocation")
        fixture_copy = server.project / FIXTURE.name
        shutil.copy2(FIXTURE, fixture_copy)
        if sha256(fixture_copy) != freeze["source_files"]["java_fixture"]["sha256"]:
            raise RuntimeError("staged Java source does not match frozen fixture hash")
        request_terminal = False
        binding, before = _bind_model(daemon, server.worker, "W23 planar TE API readback")
        request_terminal = True
        model_ref = binding["execution"]["model_ref"]
        revision = int(binding["execution"]["revision"])
        write_json(evidence / "model_created.json", {"binding": binding,
                                                        "prebuild_readback": before})
        remaining = deadline_epoch - time.time() - CLEANUP_RESERVE_S
        if remaining <= 0:
            raise TimeoutError("birth budget entered cleanup reserve before the one permitted fixture invocation")

        identity = {"request_id": request_id, "idempotency_key": idempotency_key,
                    "model_ref": model_ref.get("model_id", model_ref.get("tag", "unknown")),
                    "revision": str(revision), "entrypoint": "NativeW23TEFixture#run"}
        request_args = {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "NativeW23TEFixture#run",
                          "arguments": {"phase": "api_preflight"}, "mode": "trusted"},
        }
        request_terminal = False
        fixture_dispatch_started = True
        response = _invoke_once(
            lambda **kwargs: _dispatch(daemon, "operation_call", request_args,
                                       project_id=PROJECT_ID, ref=model_ref,
                                       revision=revision, idempotency_key=idempotency_key,
                                       request_id=request_id,
                                       rpc_timeout_s=max(1.0, min(600.0, remaining))),
            {}, evidence / "worker_request.json", identity)
        request_terminal = _worker_request_terminal(response)
        write_json(evidence / "managed_operation_response.json", response)
        if not request_terminal:
            raise RuntimeError("Worker request is not observed terminal; response is UNKNOWN and will not be retried")

        readback = _java_action_readback(response, "W23 managed TE API preflight")
        if (readback.get("fixture_id") != "w23_planar_te_port_api_preflight_v2"
                or readback.get("study_or_solver_invoked") is not False
                or readback.get("native_result") != "NOT_RUN"):
            raise AssertionError("fixture response did not preserve the frozen no-solve/NOT_RUN boundary")
        ports = readback.get("ports")
        if not isinstance(ports, list) or len(ports) != 2:
            raise AssertionError("native response did not include both Port property readbacks")
        for index, port in enumerate(ports):
            props = port.get("requested_properties") if isinstance(port, dict) else None
            if not isinstance(props, dict) or "PortType" not in props:
                raise AssertionError(f"port {index} is missing exact PortType property readback")
        selection_counts = _validate_native_selection_readback(readback)
        submissions = _actual_study_run_submissions(daemon)
        summary["study_run_submissions"] = submissions
        summary["study_or_solver_invoked"] = bool(submissions)
        if submissions:
            raise AssertionError("unexpected study.run Worker submission observed during no-solve preflight")
        idle = _daemon_idle_proof(daemon)
        if not idle["idle"]:
            raise RuntimeError("managed daemon is not idle after the terminal fixture request")
        write_json(evidence / "native_api_readback.json", {
            "status": "API_PROPERTY_READBACK_COMPLETE_NOT_NUMERIC",
            "readback": readback,
            "selection_counts": selection_counts,
            "port_plane_semantics": readback["port_plane_semantics"],
            "model_ref": model_ref,
            "model_revision": response.get("execution", {}).get("revision"),
            "study_run_submissions": submissions,
            "daemon_idle_proof": idle,
        })
        summary.update({
            "status": "NATIVE_API_PROPERTY_READBACK_COMPLETE_NOT_SOLVED",
            "engine_identity": engine_identity,
            "server_pid": server.proc.pid if server.proc else None,
            "server_birth": birth,
            "server_command": original_process_identity.get("command"),
            "port": server.port,
            "deadline_epoch": deadline_epoch,
            "freeze_sha256": expected_freeze_sha256,
            "worker_request_status": "TERMINAL",
            "port_type_readbacks": [port["requested_properties"]["PortType"] for port in ports],
            "native_selection_counts": selection_counts,
            "study_run_submissions": 0,
            "study_or_solver_invoked": False,
            "native_integrals": "NOT_RUN",
            "native_result": "NOT_RUN",
        })
    except BaseException as exc:
        summary.update({"status": "FAIL_OR_INCOMPLETE",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                        "worker_request_status": "UNKNOWN" if not request_terminal else "NOT_PENDING"})
        append_event(event_log, "managed_preflight_failed_or_incomplete",
                     error=summary["error"], request_terminal=request_terminal)
    finally:
        reconciliation: dict[str, Any] | None = None
        if daemon is not None:
            try:
                reconciliation = _ledger_reconciliation(daemon)
                try:
                    reconciliation["sqlite_snapshot"] = _backup_operation_store(
                        daemon, evidence / "raw_operations.sqlite3")
                except BaseException as exc:
                    reconciliation["sqlite_snapshot_error"] = f"{type(exc).__name__}: {exc}"
                write_json(evidence / "worker_ledger_reconciliation.json", reconciliation)
                summary["worker_ledger_reconciliation"] = {
                    "path": str(evidence / "worker_ledger_reconciliation.json"),
                    "status": reconciliation.get("status"),
                    "safe_for_owned_cleanup": reconciliation.get("safe_for_owned_cleanup"),
                    "durable_result": reconciliation.get("durable_result"),
                }
            except BaseException as exc:
                summary["worker_ledger_reconciliation_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        if server is not None:
            if server.proc is None:
                cleanup = {"status": "NOT_STARTED"}
            else:
                require_fixture_terminal = fixture_dispatch_started
                if reconciliation is None or not _ledger_allows_owned_cleanup(
                        reconciliation, require_fixture_terminal=require_fixture_terminal):
                    cleanup = {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                               "pid": server.proc.pid, "port": server.port,
                               "ledger_reconciliation": reconciliation,
                               "action": "no stop because full Worker ledger did not prove owned cleanup safe"}
                else:
                    try:
                        cleanup = _exact_owned_cleanup(
                            server, server_identity=original_process_identity or {},
                            worker_identity=worker_process_identity, worker_port=worker_port,
                            process_snapshot=helpers["_process_snapshot"],
                            reconciliation=reconciliation, evidence=evidence,
                            require_fixture_terminal=require_fixture_terminal)
                    except BaseException as exc:
                        cleanup = {"status": "CLEANUP_UNVERIFIED",
                                   "error": f"{type(exc).__name__}: {exc}"}
                        write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
            summary["cleanup"] = cleanup
            if cleanup.get("status") not in {
                    "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT", "NOT_STARTED"}:
                summary["status"] = "FAIL_OR_INCOMPLETE"
            if daemon is not None:
                try:
                    summary["daemon_idle_proof_at_cleanup"] = _daemon_idle_proof(daemon)
                except BaseException as exc:
                    summary["daemon_idle_error"] = f"{type(exc).__name__}: {exc}"
                    summary["status"] = "FAIL_OR_INCOMPLETE"
                try:
                    final_submissions = _actual_study_run_submissions(daemon)
                    summary["study_run_submissions"] = final_submissions
                    if final_submissions:
                        summary["study_or_solver_invoked"] = True
                        summary["status"] = "FAIL_OR_INCOMPLETE"
                    elif fixture_dispatch_started and not request_terminal:
                        if summary.get("study_or_solver_invoked") is not True:
                            summary["study_or_solver_invoked"] = "UNKNOWN"
                    else:
                        if summary.get("study_or_solver_invoked") is not True:
                            summary["study_or_solver_invoked"] = False
                except BaseException as exc:
                    summary["study_run_audit_error"] = f"{type(exc).__name__}: {exc}"
                    if summary.get("study_or_solver_invoked") is not True:
                        summary["study_or_solver_invoked"] = "UNKNOWN"
                    summary["status"] = "FAIL_OR_INCOMPLETE"
            elif fixture_dispatch_started:
                summary["study_or_solver_invoked"] = "UNKNOWN"
            else:
                summary["study_or_solver_invoked"] = False
            if cleanup and cleanup.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
                try:
                    daemon.close()
                except BaseException as exc:
                    summary["daemon_close_error"] = f"{type(exc).__name__}: {exc}"
                    summary["status"] = "FAIL_OR_INCOMPLETE"
        summary["finished_utc"] = utc_now()
        if server is None:
            summary["study_or_solver_invoked"] = False
        summary["native_result"] = "NOT_RUN"
        write_json(evidence / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="compile and freeze a candidate without starting COMSOL")
    prep.add_argument("--evidence", required=True, type=Path)
    run_parser = sub.add_parser("execute", help="run one already reviewed/frozen no-solve preflight")
    run_parser.add_argument("--freeze", required=True, type=Path)
    run_parser.add_argument("--approved-freeze-sha256", required=True)
    run_parser.add_argument("--work", required=True, type=Path)
    run_parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.evidence.resolve())
        print(json.dumps({"status": result["status"], "freeze_path": str(args.evidence.resolve() / "freeze.json"),
                          "freeze_sha256": result["freeze_sha256"],
                          "source_count": len(result["source_files"]),
                          "native_process_started": False}, indent=2))
        return 0
    result = execute(args.freeze.resolve(), args.approved_freeze_sha256,
                     args.work.resolve(), args.evidence.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
    return 0 if result.get("status") == "NATIVE_API_PROPERTY_READBACK_COMPLETE_NOT_SOLVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
