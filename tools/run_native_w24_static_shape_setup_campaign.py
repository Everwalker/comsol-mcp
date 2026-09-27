#!/usr/bin/env python3
"""W24 fourteen-slot setup-only candidate; it never submits Study.run."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
BASE_COMMIT = "a365420814b9159230364ad953ee13e8db9cc9be"
EVIDENCE_ROOT = REPO / "docs/full_project_execution/w24/evidence"
EXPECTED_PYTHON = Path("/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python")
EXPLICIT_SITE_PACKAGES = (EXPECTED_PYTHON.parent.parent / "lib" /
                          f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
JAVA_TOOL_OPTIONS = "-XX:ActiveProcessorCount=2 -Xms128m -Xmx1024m"
RSS_STOP_BYTES = 6 * 1024**3
MAX_OUTPUT_BYTES = 10 * 1024**3
MAX_SINGLE_MPH_BYTES = 1024**3
MAX_WALL_SECONDS = 4 * 60 * 60
CLEANUP_RESERVE_SECONDS = 600
RPC_CAP_SECONDS = 120
SAMPLE_INTERVAL_SECONDS = 1.0
PROJECT_WORKSPACE_NAME = "science"
WORK_PREFIX = "/private/tmp/comsol-mcp-w24-static-shape-setup-"
ARCHIVE_MARKER = ".w24_published_archive_manifest.json"
FREEZE_FILE = "candidate_freeze.json"
FREEZE_SCHEMA = "W24_STATIC_SHAPE_SETUP_CAMPAIGN_FREEZE_V1"
TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "EXPIRED", "LOST", "CANCELLED"}
WORKER_CLEANUP_TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"}
OVERLAY_PATHS = {
    "tools/run_native_w24_static_shape_setup_campaign.py",
    "tests/test_run_native_w24_static_shape_setup_campaign.py",
}
EXTRA_PATHS = {
    "pyproject.toml",
    "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
    "docs/full_project_execution/w24/W24_STATIC_SHAPE_IMPLEMENTATION_DRAFT.md",
    "tools/run_native_resume_smoke.py",
    "tools/run_native_w24_cure_preflight.py",
    "tools/run_native_w24_cure_science.py",
    "tools/run_native_w24_static_shape_setup.py",
    "tools/w24_static_shape_sensitivity.py",
    "tools/java/W24StaticShapeFixture.java",
    "tools/java/W24StaticShapeReadback.java",
    "tests/test_w24_static_shape_managed.py",
    "tests/test_w24_static_shape_sensitivity.py",
}
ROUTES = [
    "project.create and project.inspect before server birth",
    "one production ControlDaemon.dispatch(session.connect)",
    "session.inspect; one model_create seed",
    "14 config-major build/save/reopen/full-readback setup slots",
    "complete OperationStore/job/Worker-request reconciliation",
    "session.disconnect(retire_worker=true) only after terminal safe ledger",
    "session.inspect retirement readback; exact task Popen server stop",
]
CONFIGURATION_ORDER = [
    "baseline", "mesh_ratio_1_3", "mesh_ratio_1_1", "epsilon_6um",
    "epsilon_10um", "step_0_05Tc", "step_0_20Tc",
]
SETUP_BUDGET = {
    "schema_version": 1,
    "planned_setup_receipts": 14,
    "configuration_order": CONFIGURATION_ORDER,
    "case_order": ["flat", "step"],
    "max_task_owned_comsol_servers": 1,
    "max_managed_workers": 1,
    "max_solver_threads": 2,
    "max_gui_processes": 0,
    "wall_seconds_from_server_birth_including_cleanup": MAX_WALL_SECONDS,
    "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
    "managed_rpc_cap_seconds": RPC_CAP_SECONDS,
    "study_run_submissions": 0,
    "solver_calls": 0,
    "max_single_unsolved_mph_bytes": MAX_SINGLE_MPH_BYTES,
    "max_total_project_output_bytes": MAX_OUTPUT_BYTES,
    "rss_sampled_admission_stop_threshold_bytes": RSS_STOP_BYTES,
    "rss_sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
    "rss_limit_semantics": (
        "sampled stop threshold, not an OS hard cap; active or UNKNOWN operations are retained "
        "and can exceed the threshold"
    ),
    "child_jvm_options": JAVA_TOOL_OPTIONS,
    "server_np_argv": ["-np", "2"],
    "unknown_policy": (
        "no retry; retain original durable operation, ControlDaemon, Worker handle, exact Popen "
        "and evidence while active or UNKNOWN"
    ),
    "future_science_binding_policy": (
        "these refs are historical and invalid after Worker retirement; a later science campaign "
        "must load every SHA-bound MPH on its new Worker, repeat full parameter/mesh/max-step/"
        "getSize readback, issue new current-epoch receipts, and include that setup cost in budget"
    ),
}

class CandidateError(RuntimeError):
    pass


class OutcomeUnknown(CandidateError):
    """A persisted operation intent lacks a confirmed terminal response."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False, default=str) + "\n").encode()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha_bytes(data)


def _replace(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise CandidateError("receipt replace target is not a regular file")
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = _json_bytes(payload)
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def _append(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(_json_bytes(payload))
        stream.flush()
        os.fsync(stream.fileno())


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                            text=True, check=False, timeout=30)
    if result.returncode:
        raise CandidateError(f"read-only git {args[0]} failed: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()


def _published_paths(repo: Path, base: str) -> list[str]:
    tracked = set(_git(repo, "ls-tree", "-r", "--name-only", base).splitlines())
    missing = sorted(EXTRA_PATHS - tracked)
    if missing:
        raise CandidateError("published base lacks required closure: " + ", ".join(missing))
    paths = {
        row for row in tracked
        if (row.startswith("comsol_mcp/") and
            (row.endswith(".py") or row.endswith(".java") or
             (row.startswith("comsol_mcp/data/g2/") and row.endswith(".json"))))
        or row in EXTRA_PATHS
        or row == "tests/test_w24_static_shape_managed.py"
    }
    return sorted(paths)


def export_published_archive(*, repo: Path, destination: Path,
                             base: str = BASE_COMMIT) -> dict[str, Any]:
    if base != BASE_COMMIT:
        raise CandidateError(f"native candidate source base must remain the published {BASE_COMMIT}")
    if destination.exists() or destination.is_symlink():
        raise CandidateError("archive destination must be new")
    paths = _published_paths(repo, base)
    destination.mkdir(parents=True, exist_ok=False)
    result = subprocess.run(
        ["git", "archive", "--format=tar", base, "--", *paths],
        cwd=repo, capture_output=True, check=False, timeout=120)
    if result.returncode:
        raise CandidateError("read-only git archive failed: " +
                             result.stderr.decode("utf-8", "replace")[:1000])
    import io
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        members = tar.getmembers()
        if any(member.name.startswith("/") or ".." in Path(member.name).parts
               for member in members):
            raise CandidateError("archive contains an unsafe path")
        tar.extractall(destination, members=members, filter="data")
    source_files = {}
    for relative in paths:
        path = destination / relative
        if path.is_symlink() or not path.is_file():
            raise CandidateError(f"archive omitted regular file {relative}")
        source_files[relative] = {"bytes": path.stat().st_size, "sha256": _sha_file(path)}
    manifest_body = {
        "schema": "W24_PUBLISHED_ARCHIVE_SOURCE_MANIFEST_V1",
        "base_commit": base, "source_files": source_files,
        "source_closure_sha256": _json_hash(source_files),
    }
    manifest = {**manifest_body, "manifest_sha256": _json_hash(manifest_body)}
    _write_new(destination / ARCHIVE_MARKER, manifest)
    overlay = {}
    for relative in sorted(OVERLAY_PATHS):
        source = repo / relative
        if source.is_symlink() or not source.is_file():
            raise CandidateError(f"candidate overlay missing or aliased: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        overlay[relative] = {"bytes": len(data), "sha256": _sha_bytes(data)}
    return {
        "status": "EXACT_PUBLISHED_ARCHIVE_PLUS_W24_OVERLAY",
        "base_commit": base, "archive_dir": str(destination.resolve()),
        "published_source_count": len(source_files),
        "source_closure_sha256": manifest["source_closure_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "overlay_files": overlay, "overlay_sha256": _json_hash(overlay),
    }


def _source_inventory(repo: Path) -> dict[str, Any]:
    marker = repo / ARCHIVE_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise CandidateError("prepare requires the exact published archive marker")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if (manifest.get("schema") != "W24_PUBLISHED_ARCHIVE_SOURCE_MANIFEST_V1" or
            manifest.get("base_commit") != BASE_COMMIT or
            manifest.get("manifest_sha256") != _json_hash(body) or
            manifest.get("source_closure_sha256") != _json_hash(manifest.get("source_files"))):
        raise CandidateError("source marker does not bind the published a365420 base")
    base_files = manifest.get("source_files")
    if not isinstance(base_files, Mapping):
        raise CandidateError("source marker omits published source hashes")
    rows = {}
    for relative in sorted(set(base_files) | OVERLAY_PATHS):
        path = repo / relative
        if path.is_symlink() or not path.is_file():
            raise CandidateError(f"missing/aliased source closure file {relative}")
        data = path.read_bytes()
        row = {"bytes": len(data), "sha256": _sha_bytes(data),
               "candidate_overlay": relative in OVERLAY_PATHS}
        if relative not in OVERLAY_PATHS:
            if base_files.get(relative) != {"bytes": len(data), "sha256": row["sha256"]}:
                raise CandidateError(f"published file differs from base archive: {relative}")
            row["base_sha256"] = row["sha256"]
        rows[relative] = row
    return {
        "base_commit": BASE_COMMIT, "checkout_kind": "git_archive_plus_explicit_overlay",
        "archive_manifest_sha256": manifest["manifest_sha256"],
        "source_files": rows, "source_closure_sha256": _json_hash(rows),
    }


CAMPAIGN_DEPENDENCIES = {
    "comsol_mcp._control_daemon": ("ControlDaemon",),
    "comsol_mcp._g2_isolation": ("_process_snapshot",),
    "comsol_mcp._platform_process": ("process_identity",),
    "tools.run_native_resume_smoke": ("NativeLoopbackServer", "_is_native_study_run_submission"),
    "tools.run_native_w24_cure_preflight": (
        "_actual_study_run_submissions", "_project_job_inventory",
        "_worker_request_activity_inventory", "_worker_request_terminal"),
    "tools.run_native_w24_cure_science": ("ManagedModelBinding",),
    "tools.run_native_w24_static_shape_setup": (
        "CampaignError", "StaticShapeManagedRunner", "prepare_project_sources"),
    "tools.w24_static_shape_sensitivity": (
        "SENSITIVITY_CASE_ORDER", "sensitivity_configurations"),
}


def preflight_campaign_dependencies(repo: Path, *, importer: Callable[[str], Any] | None = None
                                    ) -> dict[str, Any]:
    """Resolve every late-bound setup/cleanup module before any native birth.

    The receipt binds each imported module to the candidate's frozen source
    closure. This keeps an absent helper from surfacing only after a Worker
    has been created and makes the dependency check repeatable at execution.
    """
    source = _source_inventory(repo)
    source_rows = source["source_files"]
    root = repo.resolve(strict=True)
    load = importer or importlib.import_module
    modules: dict[str, Any] = {}
    for module_name, required_symbols in sorted(CAMPAIGN_DEPENDENCIES.items()):
        try:
            module = load(module_name)
        except Exception as exc:
            raise CandidateError(
                f"pre-birth campaign dependency is unavailable: {module_name}"
            ) from exc
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            raise CandidateError(f"pre-birth dependency has no file-backed origin: {module_name}")
        path = Path(origin).resolve(strict=True)
        if not path.is_relative_to(root):
            raise CandidateError(f"pre-birth dependency escaped the frozen archive: {module_name}")
        relative = path.relative_to(root).as_posix()
        expected = source_rows.get(relative)
        if not isinstance(expected, Mapping) or expected.get("sha256") != _sha_file(path):
            raise CandidateError(
                f"pre-birth dependency is outside the candidate source closure: {relative}"
            )
        missing = [symbol for symbol in required_symbols if not hasattr(module, symbol)]
        if missing:
            raise CandidateError(
                f"pre-birth dependency {module_name} omits required symbols: {missing}"
            )
        modules[module_name] = {"path": relative, "sha256": expected["sha256"],
                                "symbols": list(required_symbols)}
    return {"status": "PREBIRTH_DEPENDENCY_PREFLIGHT_PASS",
            "module_count": len(modules), "modules": modules,
            "source_closure_sha256": source["source_closure_sha256"]}


def build_setup_slots() -> list[dict[str, Any]]:
    from tools.w24_static_shape_sensitivity import (
        SENSITIVITY_CASE_ORDER, sensitivity_configurations)
    return [
        {"submission_index": i, "configuration_id": cfg.configuration_id,
         "case_id": case, "study_run_calls": 0,
         "phase_initialization_executed": False}
        for i, (cfg, case) in enumerate(
            ((cfg, case) for cfg in sensitivity_configurations()
             for case in SENSITIVITY_CASE_ORDER), 1)
    ]


def _compile_sources(repo: Path, output: Path, install: Path, jdk: Path) -> dict[str, Any]:
    from comsol_mcp._java_worker import JavaWorkerPaths
    if output.exists():
        raise CandidateError("Java compile output must be new")
    paths = JavaWorkerPaths(install, jdk, project_root=repo)
    classpath, manifest_hash, jar_count, jar_hash = paths.classpath()
    sources = [repo / "tools/java/W24StaticShapeFixture.java",
               repo / "tools/java/W24StaticShapeReadback.java"]
    output.mkdir(parents=True, exist_ok=False)
    command = [str(jdk / "bin/javac"), "-encoding", "UTF-8", "-cp",
               classpath, "-d", str(output), *(str(path) for path in sources)]
    result = subprocess.run(command, capture_output=True, text=True,
                            check=False, timeout=180)
    classes = sorted(output.rglob("*.class")) if result.returncode == 0 else []
    return {
        "status": "COMPILE_PASS" if result.returncode == 0 and classes else "COMPILE_FAIL",
        "exit_code": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "source_sha256": {p.name: _sha_file(p) for p in sources},
        "classpath_manifest_sha256": manifest_hash,
        "classpath_jar_count": jar_count,
        "classpath_jar_content_fingerprint_sha256": jar_hash,
        "class_count": len(classes),
        "classes": [{"path": p.relative_to(output).as_posix(),
                     "bytes": p.stat().st_size, "sha256": _sha_file(p)} for p in classes],
        "comsol_version": paths.comsol_version_info(),
        "jdk_version": paths.jdk_version_info(),
        "command": command,
    }


def prepare_candidate(*, repo: Path, evidence: Path,
                      install: Path = INSTALL_ROOT, jdk: Path = JAVA11) -> dict[str, Any]:
    if (Path(sys.executable).resolve(strict=True) != EXPECTED_PYTHON.resolve(strict=True) or
            sys.version_info[:2] != (3, 12)):
        raise CandidateError(f"offline candidate preparation requires exact Python 3.12 at {EXPECTED_PYTHON}")
    python_isolation = configure_archive_python(repo)
    if evidence.exists() or evidence.is_symlink() or not evidence.resolve().is_relative_to(EVIDENCE_ROOT.resolve()):
        raise CandidateError("offline candidate evidence path must be new below W24 evidence")
    dependency_preflight = preflight_campaign_dependencies(repo)
    source_before = _source_inventory(repo)
    evidence.mkdir(parents=True, exist_ok=False)
    compiled = _compile_sources(repo, evidence / "offline_compile/classes", install, jdk)
    source_after = _source_inventory(repo)
    if source_before != source_after or compiled["status"] != "COMPILE_PASS":
        raise CandidateError("source changed or offline Java compilation failed")
    freeze_body = {
        "schema": FREEZE_SCHEMA, "status": "PREPARED_SETUP_ONLY_NOT_NATIVE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source_after,
        "runtime": {
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": sys.version.split()[0],
            "comsol_install_root": str(install.resolve(strict=True)),
            "comsol_version": compiled["comsol_version"],
            "external_jdk_home": str(jdk.resolve(strict=True)),
            "jdk_version": compiled["jdk_version"],
            "classpath_manifest_sha256": compiled["classpath_manifest_sha256"],
            "classpath_jar_count": compiled["classpath_jar_count"],
            "classpath_jar_content_fingerprint_sha256":
                compiled["classpath_jar_content_fingerprint_sha256"],
        },
        "python_isolation": python_isolation,
        "dependency_preflight": dependency_preflight,
        "compile": {k: v for k, v in compiled.items()
                    if k not in {"stdout", "stderr", "command"}},
        "budget": SETUP_BUDGET, "routes": ROUTES,
        "ordered_setup_slots": build_setup_slots(),
        "scientific_status": "NOT_RUN",
        "cross_epoch_binding_warning": SETUP_BUDGET["future_science_binding_policy"],
        "candidate_evidence_dir": str(evidence.resolve()),
    }
    freeze = {**freeze_body, "candidate_sha256": _json_hash(freeze_body)}
    _write_new(evidence / FREEZE_FILE, freeze)
    _write_new(evidence / "offline_compile.json", compiled)
    return freeze


def verify_candidate(*, repo: Path, evidence: Path,
                     reviewed_sha256: str) -> dict[str, Any]:
    freeze_path = evidence / FREEZE_FILE
    if freeze_path.is_symlink() or not freeze_path.is_file():
        raise CandidateError("candidate freeze is missing/aliased")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    body = {k: v for k, v in freeze.items() if k != "candidate_sha256"}
    if (freeze.get("schema") != FREEZE_SCHEMA or
            freeze.get("status") != "PREPARED_SETUP_ONLY_NOT_NATIVE" or
            freeze.get("candidate_sha256") != _json_hash(body) or
            reviewed_sha256 != freeze.get("candidate_sha256")):
        raise CandidateError("reviewed SHA does not match the frozen candidate")
    if (_source_inventory(repo) != freeze.get("source") or
            freeze.get("budget") != SETUP_BUDGET or freeze.get("routes") != ROUTES):
        raise CandidateError("source closure, route allowlist, or budget changed after review")
    current_dependencies = preflight_campaign_dependencies(repo)
    if current_dependencies != freeze.get("dependency_preflight"):
        raise CandidateError("pre-birth dependency closure differs from the reviewed candidate")
    return freeze


def fresh_scoped_inventory() -> dict[str, Any]:
    """Observe process/listener scope without emitting argv or full user paths."""
    from comsol_mcp._platform_process import process_identity
    ps_cmd = ["/bin/ps", "-axo", "pid=,ppid=,lstart=,command="]
    ps = subprocess.run(ps_cmd, capture_output=True, text=True,
                        check=False, timeout=15)
    matched: dict[int, dict[str, Any]] = {}
    install = str(INSTALL_ROOT).lower()
    for line in ps.stdout.splitlines():
        fields = line.strip().split(None, 7)
        if len(fields) < 8 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, ppid, command = int(fields[0]), int(fields[1]), fields[7]
        lowered = command.lower()
        kind = ("java_worker" if "persistentcomsolworker" in lowered else
                "comsol_server" if "mphserver" in lowered or
                (install in lowered and ("java" in lowered or "comsol" in lowered))
                else None)
        if kind is None:
            continue
        # `ps` is the process-presence observation. Keep every scoped PID it
        # finds even when the stronger birth-identity query is missing,
        # malformed, or races with exit. Dropping such a row would turn
        # uncertainty into an empty (and therefore apparently quiescent)
        # inventory.
        row: dict[str, Any] = {
            "kind": kind, "pid": pid, "ppid": ppid,
            "identity_status": "IDENTITY_UNKNOWN",
        }
        try:
            ident = process_identity(pid)
        except Exception as exc:
            row["identity_status"] = "IDENTITY_QUERY_FAILED"
            row["identity_error_type"] = type(exc).__name__
        else:
            if not isinstance(ident, Mapping):
                row["identity_status"] = "IDENTITY_UNKNOWN"
                row["identity_shape"] = type(ident).__name__
            else:
                birth = ident.get("start_epoch_ms")
                alive = ident.get("alive")
                if alive is False:
                    row["identity_status"] = "IDENTITY_NOT_ALIVE"
                elif alive is True and type(birth) is int and birth > 0:
                    row["identity_status"] = "VERIFIED_ALIVE"
                    row["start_epoch_ms"] = birth
                else:
                    row["identity_status"] = "IDENTITY_UNKNOWN"
                    if type(birth) is int:
                        row["observed_start_epoch_ms"] = birth
        matched[pid] = row
    lsof_cmd = ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]
    lsof = subprocess.run(lsof_cmd, capture_output=True, text=True,
                          check=False, timeout=15)
    listeners = []
    for line in lsof.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or not fields[1].isdigit() or int(fields[1]) not in matched:
            continue
        pid = int(fields[1])
        try:
            endpoint = fields[fields.index("TCP") + 1]
        except (ValueError, IndexError):
            endpoint = None
        if endpoint:
            listeners.append({"kind": matched[pid]["kind"], "pid": pid,
                              "endpoint": endpoint})
    processes = sorted(matched.values(), key=lambda row: row["pid"])
    okay = ps.returncode == 0 and lsof.returncode == 0 and not ps.stderr.strip() and not lsof.stderr.strip()
    identity_complete = all(row.get("identity_status") == "VERIFIED_ALIVE"
                            for row in processes)
    return {
        "schema": "W24_SETUP_FRESH_SCOPED_INVENTORY_V1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "commands": {
            "ps": ps_cmd, "ps_exit_code": ps.returncode,
            "ps_stdout_sha256": _sha_bytes(ps.stdout.encode()),
            "lsof": lsof_cmd, "lsof_exit_code": lsof.returncode,
            "lsof_stdout_sha256": _sha_bytes(lsof.stdout.encode()),
        },
        "scope": {
            "process_filters": ["COMSOL installation path", "mphserver",
                                "comsol_mcp.worker_java.PersistentComsolWorker"],
            "listeners": "TCP LISTEN rows owned by matched PIDs only",
            "argv_or_full_user_paths_emitted": False,
        },
        "matching_processes": processes, "matching_listeners": listeners,
        "process_identity_complete": identity_complete,
        "fresh_quiescent_for_scope": okay and identity_complete and not processes and not listeners,
        "limitations": "one scoped instant only; repeat immediately before any native birth",
    }


def _identity_name(value: Any) -> str:
    cls = value if isinstance(value, type) else type(value)
    return f"{getattr(cls, '__module__', '?')}.{getattr(cls, '__qualname__', cls.__name__)}"


def _assert_no_editable_fallback() -> None:
    surfaces = [_identity_name(item) for item in sys.path_hooks]
    surfaces.extend(_identity_name(item) for item in sys.meta_path)
    if any("editable" in item.lower() for item in surfaces):
        raise CandidateError("editable Python finder/path hook is present; run the candidate with -S")


def configure_archive_python(repo: Path) -> dict[str, Any]:
    """Bind imports to the exact source archive without running .pth files."""
    if not getattr(sys.flags, "no_site", False) or "site" in sys.modules:
        raise CandidateError("isolated candidate Python must start with -S; site/.pth processing is forbidden")
    root = repo.resolve(strict=True)
    packages = EXPLICIT_SITE_PACKAGES.resolve(strict=True)
    if not packages.is_dir():
        raise CandidateError("the exact venv site-packages directory is unavailable")
    _assert_no_editable_fallback()
    keep: list[str] = []
    for entry in sys.path:
        resolved = Path(entry or Path.cwd()).resolve()
        if resolved not in {root, root / "tools"}:
            keep.append(entry)
    sys.path[:] = [str(root), *keep]
    if str(packages) not in sys.path:
        sys.path.append(str(packages))
    for entry in sys.path:
        search_root = Path(entry or Path.cwd()).resolve()
        if search_root != root and (search_root / "comsol_mcp").exists():
            raise CandidateError(f"another comsol_mcp package path is visible outside the archive: {search_root}")
    _assert_no_editable_fallback()
    return {"site_processing_disabled": True,
            "explicit_site_packages": str(packages),
            "archive_root_precedence": str(root),
            "path_hooks": [_identity_name(item) for item in sys.path_hooks],
            "meta_path_finders": [_identity_name(item) for item in sys.meta_path]}


def audit_archive_imports(repo: Path) -> dict[str, str]:
    root = repo.resolve(strict=True)
    selected: dict[str, str] = {}
    for name, module in tuple(sys.modules.items()):
        if name != "comsol_mcp" and not name.startswith(("comsol_mcp.", "tools.")):
            continue
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            spec = getattr(module, "__spec__", None)
            origin = getattr(spec, "origin", None) if spec is not None else None
        if not isinstance(origin, str) or origin in {"built-in", "frozen"}:
            raise CandidateError(f"project module lacks a file-backed archive origin: {name}")
        resolved = Path(origin).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise CandidateError(f"project import escaped the frozen archive: {name} -> {resolved}")
        selected[name] = str(resolved)
    package = importlib.import_module("comsol_mcp")
    package_paths = [Path(item).resolve(strict=True) for item in package.__path__]
    if package_paths != [root / "comsol_mcp"]:
        raise CandidateError("comsol_mcp package search path escaped the isolated archive")
    _assert_no_editable_fallback()
    return selected


def _lsof_listener(pid: int, port: int) -> dict[str, Any]:
    command = ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
               f"-iTCP:{port}", "-sTCP:LISTEN"]
    result = subprocess.run(command, capture_output=True, text=True,
                            check=False, timeout=10)
    rows = [line for line in result.stdout.splitlines()
            if line.strip() and not line.lstrip().startswith("COMMAND")]
    parsed: list[dict[str, Any]] = []
    for line in rows:
        fields = line.split()
        row_pid = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None
        try:
            endpoint = fields[fields.index("TCP") + 1]
        except (ValueError, IndexError):
            endpoint = None
        parsed.append({"pid": row_pid, "endpoint": endpoint})
    return {"command": command, "exit_code": result.returncode,
            "stdout_sha256": _sha_bytes(result.stdout.encode()),
            "stderr_sha256": _sha_bytes(result.stderr.encode()),
            "rows": parsed}


def _start_rss_monitor(monitor_box: dict[str, Any], proc: Any,
                       identity: Mapping[str, Any], worker_search_root: Path,
                       events_path: Path) -> None:
    """Start sampling immediately after the exact task-owned server Popen birth."""
    if monitor_box.get("monitor") is not None:
        raise CandidateError("RSS monitor already exists; server birth is single-use")
    pid = getattr(proc, "pid", None)
    birth = identity.get("start_epoch_ms")
    if type(pid) is not int or type(birth) is not int:
        raise CandidateError("RSS monitoring requires the exact Popen PID and birth epoch")
    monitor = SampledRssMonitor(server_pid=pid, server_birth_ms=birth,
                               worker_search_root=worker_search_root,
                               events_path=events_path)
    monitor_box["monitor"] = monitor
    monitor.start()


def _process_start_epoch_for_identity(identity: Mapping[str, Any], worker: Any) -> int:
    """Bind the Worker Popen snapshot to the OS birth identity used by the watchdog."""
    from comsol_mcp._platform_process import process_identity

    process = getattr(worker, "_process", None)
    pid = getattr(process, "pid", None)
    if (not isinstance(identity, Mapping) or type(pid) is not int or pid <= 1 or
            identity.get("pid") != pid):
        raise CandidateError("Worker birth check does not identify the exact registered Popen")
    observed = process_identity(pid)
    birth = observed.get("start_epoch_ms") if isinstance(observed, Mapping) else None
    if (not isinstance(observed, Mapping) or observed.get("alive") is not True or
            type(birth) is not int or birth <= 0 or process.poll() is not None):
        raise CandidateError("Worker Popen is not live with a verifiable process birth epoch")
    return birth


def _hold_forever_with_handles(monitor: Any, *, stop_event: threading.Event | None = None) -> None:
    """Fail closed without closing a daemon, Worker, or server after uncertain state."""
    while stop_event is None or not stop_event.is_set():
        if isinstance(monitor, SampledRssMonitor):
            thread = monitor._thread
            if thread is None or not thread.is_alive():
                monitor.sample_once()
        time.sleep(2.0)


class SampledRssMonitor:
    """One-second, exact-PID sampled admission stop for this candidate only."""

    def __init__(self, *, server_pid: int, server_birth_ms: int,
                 worker_search_root: Path, events_path: Path,
                 threshold_bytes: int = RSS_STOP_BYTES,
                 interval_seconds: float = SAMPLE_INTERVAL_SECONDS,
                 ps_runner: Callable[..., Any] = subprocess.run,
                 identity_reader: Callable[[int], Mapping[str, Any]] | None = None):
        from comsol_mcp._platform_process import process_identity

        if type(server_pid) is not int or server_pid <= 1 or type(server_birth_ms) is not int or server_birth_ms <= 0:
            raise CandidateError("RSS monitor requires an exact server Popen PID/birth identity")
        if threshold_bytes <= 0 or interval_seconds <= 0:
            raise CandidateError("RSS monitor threshold and interval must be positive")
        self.server_pid = server_pid
        self.server_birth_ms = server_birth_ms
        self.worker_search_root = worker_search_root.resolve()
        self.events_path = events_path
        self.threshold_bytes = int(threshold_bytes)
        self.interval_seconds = float(interval_seconds)
        self.ps_runner = ps_runner
        self.identity_reader = identity_reader or process_identity
        self.worker_required_live = False
        self.expected_worker_pid: int | None = None
        self.expected_worker_birth_ms: int | None = None
        self.stop_reason: str | None = None
        self.last_sample: dict[str, Any] | None = None
        self.last_sample_monotonic: float | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise CandidateError("RSS monitor was already started")
        self.sample_once()
        self._thread = threading.Thread(target=self._run,
                                        name="w24-setup-rss-watchdog", daemon=True)
        self._thread.start()

    def set_worker_required_live(self, required: bool) -> None:
        if type(required) is not bool:
            raise CandidateError("Worker monitoring state must be boolean")
        with self._lock:
            self.worker_required_live = required

    def bind_worker(self, pid: int, birth_ms: int) -> None:
        if type(pid) is not int or pid <= 1 or type(birth_ms) is not int or birth_ms <= 0:
            raise CandidateError("Worker RSS monitoring requires its exact Popen PID/birth")
        with self._lock:
            if self.expected_worker_pid is not None:
                raise CandidateError("the RSS monitor cannot bind a replacement Worker")
            self.expected_worker_pid = pid
            self.expected_worker_birth_ms = birth_ms
            self.worker_required_live = True

    def _record(self, payload: Mapping[str, Any]) -> None:
        _append(self.events_path, {"event": "rss_sample", **dict(payload)})

    def sample_once(self) -> dict[str, Any]:
        with self._sample_lock:
            return self._sample_once_locked()

    def _sample_once_locked(self) -> dict[str, Any]:
        sample_started = time.monotonic()
        actual_interval = (None if self.last_sample_monotonic is None else
                           max(0.0, sample_started - self.last_sample_monotonic))
        self.last_sample_monotonic = sample_started
        command = ["/bin/ps", "-axo", "pid=,ppid=,rss=,command="]
        try:
            result = self.ps_runner(command, capture_output=True, text=True,
                                    check=False, timeout=10)
            if result.returncode != 0 or result.stderr.strip():
                raise CandidateError("scoped RSS ps probe failed")
            rows: dict[int, dict[str, Any]] = {}
            worker_pids: list[int] = []
            worker_prefix = str(self.worker_search_root).lower()
            for line in result.stdout.splitlines():
                fields = line.strip().split(None, 3)
                if len(fields) < 4 or not fields[0].isdigit() or not fields[1].isdigit() or not fields[2].isdigit():
                    continue
                pid, ppid, rss_kib = int(fields[0]), int(fields[1]), int(fields[2])
                command_text = fields[3]
                if pid == self.server_pid:
                    rows[pid] = {"kind": "server", "pid": pid, "ppid": ppid,
                                 "rss_bytes": rss_kib * 1024}
                elif ("persistentcomsolworker" in command_text.lower() and
                      worker_prefix in command_text.lower()):
                    worker_pids.append(pid)
                    rows[pid] = {"kind": "worker", "pid": pid, "ppid": ppid,
                                 "rss_bytes": rss_kib * 1024}
            server_row = rows.get(self.server_pid)
            if server_row is None:
                raise CandidateError("exact task server PID is absent from the RSS sample")
            server_identity = self.identity_reader(self.server_pid)
            if (not isinstance(server_identity, Mapping) or server_identity.get("alive") is not True or
                    server_identity.get("start_epoch_ms") != self.server_birth_ms):
                raise CandidateError("server process birth changed or became unavailable during RSS sampling")
            if len(worker_pids) > 1:
                raise CandidateError("more than one Worker process matches the candidate-private state root")
            if self.worker_required_live and len(worker_pids) != 1:
                raise CandidateError("connected candidate Worker is absent from the RSS sample")
            for pid in worker_pids:
                identity = self.identity_reader(pid)
                if (not isinstance(identity, Mapping) or identity.get("alive") is not True or
                        type(identity.get("start_epoch_ms")) is not int or identity["start_epoch_ms"] <= 0):
                    raise CandidateError("candidate Worker process birth identity is unavailable")
                if (self.expected_worker_pid is not None and
                        (pid != self.expected_worker_pid or
                         identity.get("start_epoch_ms") != self.expected_worker_birth_ms)):
                    raise CandidateError("RSS sample does not match the exact connected Worker Popen birth")
                rows[pid]["start_epoch_ms"] = identity["start_epoch_ms"]
            server_row["start_epoch_ms"] = self.server_birth_ms
            total = sum(int(row["rss_bytes"]) for row in rows.values())
            status = "RSS_THRESHOLD_REACHED" if total >= self.threshold_bytes else "SAMPLED_WITHIN_THRESHOLD"
            sample = {"schema": "W24_SETUP_RSS_SAMPLE_V1",
                      "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                      "ps_command": command, "ps_exit_code": result.returncode,
                      "ps_stdout_sha256": _sha_bytes(result.stdout.encode()),
                      "target_sample_interval_seconds": self.interval_seconds,
                      "actual_interval_since_prior_sample_seconds": actual_interval,
                      "status": status, "processes": sorted(rows.values(), key=lambda item: item["pid"]),
                      "combined_comsol_worker_rss_bytes": total,
                      "threshold_bytes": self.threshold_bytes,
                      "hard_cap": False}
        except BaseException as exc:
            sample = {"schema": "W24_SETUP_RSS_SAMPLE_V1",
                      "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                      "target_sample_interval_seconds": self.interval_seconds,
                      "actual_interval_since_prior_sample_seconds": actual_interval,
                      "status": "RSS_MONITOR_FAILED", "error_type": type(exc).__name__,
                      "error": str(exc), "hard_cap": False}
        with self._lock:
            self.last_sample = sample
            if sample.get("status") in {"RSS_THRESHOLD_REACHED", "RSS_MONITOR_FAILED"} and self.stop_reason is None:
                self.stop_reason = sample["status"]
        try:
            self._record(sample)
        except BaseException as exc:
            with self._lock:
                self.stop_reason = self.stop_reason or "RSS_MONITOR_EVIDENCE_WRITE_FAILED"
                self.last_sample = {**sample, "status": "RSS_MONITOR_FAILED",
                                    "evidence_error_type": type(exc).__name__,
                                    "evidence_error": str(exc)}
            return self.last_sample
        return sample

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample_once()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(12.0, self.interval_seconds * 12))
            if thread.is_alive():
                with self._lock:
                    self.stop_reason = self.stop_reason or "RSS_MONITOR_THREAD_DID_NOT_STOP"
                raise CandidateError("RSS monitor thread did not stop; preserve all live process handles")


class W24StaticShapeSetupServer:
    """Published private-shadow helper with the reviewed COMSOL -np 2 birth."""

    def __init__(self, work: Path, evidence: Path, *, event_log: Path,
                 on_birth: Callable[[Any, Mapping[str, Any]], None] | None = None):
        from tools.run_native_resume_smoke import NativeLoopbackServer

        self._base = NativeLoopbackServer(work, evidence, event_log=event_log)
        self.__dict__.update(self._base.__dict__)
        self.on_birth = on_birth
        self.birth_monotonic: float | None = None
        self.server_snapshot: dict[str, Any] | None = None

    def prepare_shadow(self) -> dict[str, Any]:
        return self._base.prepare_shadow()

    def start_and_verify_listener(self) -> dict[str, Any]:
        from comsol_mcp._g2_isolation import _process_snapshot
        from comsol_mcp._platform_process import process_identity

        if self.proc is not None:
            raise CandidateError("task-owned server may be born only once")
        port_file = self.runtime / "server.port"
        command = [str(self.shadow_root / "bin/comsol"), "mphserver", "-np", "2",
                   "-port", "0", "-portfile", str(port_file),
                   "-prefsdir", str(self.prefs), "-tmpdir", str(self.tmp),
                   "-recoverydir", str(self.recovery), "-login", "auto",
                   "-silent", "-multi", "on"]
        log_handle = self.server_log.open("ab", buffering=0)
        self._server_log_handle = log_handle
        self.proc = subprocess.Popen(command, cwd=str(self.work), env=dict(os.environ),
                                     stdin=subprocess.DEVNULL, stdout=log_handle,
                                     stderr=subprocess.STDOUT, text=True,
                                     start_new_session=True)
        self.birth_monotonic = time.monotonic()
        identity = _process_snapshot(self.proc.pid)
        if not isinstance(identity, Mapping) or identity.get("pid") != self.proc.pid:
            raise CandidateError("task server Popen birth/command identity is unavailable")
        epoch = process_identity(self.proc.pid)
        if (not isinstance(epoch, Mapping) or epoch.get("alive") is not True or
                type(epoch.get("start_epoch_ms")) is not int or epoch["start_epoch_ms"] <= 0):
            raise CandidateError("task server Popen start epoch is unavailable")
        self.server_snapshot = {**dict(identity), "start_epoch_ms": epoch["start_epoch_ms"]}
        if self.on_birth is not None:
            self.on_birth(self.proc, self.server_snapshot)
        _append(self.event_log, {"event": "server_popen_birth",
                                 "at_utc": datetime.now(timezone.utc).isoformat(),
                                 "pid": self.proc.pid, "birth": identity.get("birth"),
                                 "command_sha256": identity.get("command_sha256"),
                                 "argv": command, "required_np": 2})
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise CandidateError(f"task-owned COMSOL server exited {self.proc.returncode}; log={self.server_log}")
            if port_file.is_file() and not port_file.is_symlink():
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
        listener_deadline = time.monotonic() + 15.0
        probe: dict[str, Any] | None = None
        row: dict[str, Any] | None = None
        while time.monotonic() < listener_deadline:
            if self.proc.poll() is not None:
                raise CandidateError(f"task-owned COMSOL server exited before listener proof; log={self.server_log}")
            probe = _lsof_listener(self.proc.pid, self.port)
            if (probe["exit_code"] == 0 and len(probe["rows"]) == 1 and
                    probe["rows"][0] == {"pid": self.proc.pid,
                                         "endpoint": f"127.0.0.1:{self.port}"}):
                row = probe["rows"][0]
                break
            time.sleep(0.2)
        if row is None or probe is None:
            raise CandidateError("lsof did not prove exactly one IPv4 loopback listener for the task Popen")
        self.process_identity = dict(self.server_snapshot)
        self.process_identity["port"] = self.port
        receipt = {"schema_version": 2, "status": "RUNNING",
                   "process": {"pid": self.proc.pid,
                               "birth": self.server_snapshot.get("birth"),
                               "command_sha256": self.server_snapshot.get("command_sha256"),
                               "port": self.port}}
        _write_new(self.receipt_path, receipt)
        listener = {"status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
                    "pid": self.proc.pid, "port": self.port,
                    "endpoint": row["endpoint"], "lsof_command": probe["command"],
                    "lsof_stdout_sha256": probe["stdout_sha256"],
                    "isolation_receipt": str(self.receipt_path),
                    "process_identity": {key: receipt["process"][key]
                                         for key in ("pid", "birth", "command_sha256")}}
        _write_new(self.evidence / "pre_worker_listener.json", listener)
        return listener

    def require_server_java_options(self) -> dict[str, Any]:
        payload = self.server_log.read_bytes()
        text = payload.decode("utf-8", "replace")
        matched = f"Picked up JAVA_TOOL_OPTIONS: {JAVA_TOOL_OPTIONS}" in text
        evidence = {"status": "JAVA_TOOL_OPTIONS_OBSERVED" if matched else "JAVA_TOOL_OPTIONS_NOT_OBSERVED",
                    "path": str(self.server_log), "sha256": _sha_bytes(payload),
                    "bytes": len(payload), "required_options": JAVA_TOOL_OPTIONS,
                    "observed": matched}
        if not matched:
            raise CandidateError("actual COMSOL server log did not confirm the frozen JAVA_TOOL_OPTIONS")
        _write_new(self.evidence / "server_java_options_readback.json", evidence)
        return evidence


class BudgetedSetupDaemon:
    """Expose the real ControlDaemon/OperationStore behind a setup-only route fence."""

    def __init__(self, daemon: Any, *, monitor: SampledRssMonitor,
                 deadline_monotonic: float, events_path: Path,
                 workspace: Path, project_id: str):
        self._daemon = daemon
        self.monitor = monitor
        self.deadline_monotonic = deadline_monotonic
        self.events_path = events_path
        self.workspace = workspace.resolve(strict=True)
        self.project_id = project_id
        self.slot: tuple[str, str] | None = None
        self.slot_stage: str | None = None
        self.slot_model_tag: str | None = None
        self.slot_artifact: dict[str, Any] | None = None
        self.completed_slots: set[tuple[str, str]] = set()
        self.unknown = False
        self.bootstrap_model_create_used = False

    @property
    def store(self) -> Any:
        return self._daemon.store

    @property
    def session_registry(self) -> Any:
        return self._daemon.session_registry

    def set_slot(self, configuration_id: str, case_id: str) -> None:
        slot = (configuration_id, case_id)
        if (self.slot is not None or configuration_id not in CONFIGURATION_ORDER or
                case_id not in {"flat", "step"} or slot in self.completed_slots or
                len(self.completed_slots) >= 14):
            raise CandidateError("setup slot is active, duplicated, or outside the frozen 14-slot matrix")
        self.slot = slot
        self.slot_stage = "begin"
        self.slot_model_tag = None
        self.slot_artifact = None

    def clear_slot(self) -> None:
        if self.slot is not None and self.slot_stage == "readback_complete":
            self.completed_slots.add(self.slot)
        self.slot = None
        self.slot_stage = None
        self.slot_model_tag = None
        self.slot_artifact = None

    def _remaining_rpc(self) -> float:
        return _remaining_admission_rpc(self.deadline_monotonic, float(RPC_CAP_SECONDS))

    def _validate_operation(self, operation: Any, arguments: Any) -> None:
        if not isinstance(operation, str) or not isinstance(arguments, Mapping):
            raise CandidateError("setup dispatch requires an operation and object arguments")
        blocked = {"study.run", "study_run", "solve", "solver.run", "model.solve",
                   "phase_initialization", "phase.initialization"}
        if operation.lower() in blocked:
            raise CandidateError("setup-only route fence rejected a solver or Phase Initialization operation")
        if operation == "model_create":
            if (self.slot is not None or self.bootstrap_model_create_used or
                    dict(arguments) != {"name": "w24_static_shape_setup_parent"}):
                raise CandidateError("the campaign permits exactly one registered parent model_create")
            return
        if operation == "model.adopt":
            if (self.slot is None or self.slot_stage != "built" or
                    set(arguments) != {"server_model_tag"} or
                    arguments.get("server_model_tag") != self.slot_model_tag):
                raise CandidateError("model.adopt is allowed only inside an active setup slot")
            return
        if operation == "model.inspect":
            if (self.slot is None or self.slot_stage not in {"adopted", "loaded"} or
                    dict(arguments) != {"detail": "summary"}):
                raise CandidateError("model.inspect is allowed only as the exact active-slot identity readback")
            return
        if operation == "model_load":
            if self.slot is None or not isinstance(arguments.get("path"), str):
                raise CandidateError("model_load is allowed only for the active slot's saved MPH")
            target = Path(arguments["path"])
            try:
                resolved = target.resolve(strict=True)
            except OSError as exc:
                raise CandidateError("model_load target is not an existing saved artifact") from exc
            try:
                output_root = (self.workspace / "outputs").resolve(strict=True)
            except OSError as exc:
                raise CandidateError("registered outputs directory is unavailable") from exc
            if (target.is_symlink() or resolved.parent != output_root or
                    resolved.suffix.lower() != ".mph"):
                raise CandidateError("model_load target escaped the registered outputs directory")
            if (self.slot_stage != "saved" or self.slot_artifact is None or
                    resolved != Path(self.slot_artifact["path"]) or
                    _sha_file(resolved) != self.slot_artifact["sha256"]):
                raise CandidateError("model_load must reopen the exact unchanged MPH saved in this slot")
            return
        if operation == "operation_call":
            if self.slot is None or arguments.get("operation_id") != "code.execute_java":
                raise CandidateError("only reviewed Java setup/readback calls are permitted inside a slot")
            body = arguments.get("arguments")
            if not isinstance(body, Mapping) or body.get("mode") != "trusted":
                raise CandidateError("trusted Java call envelope is malformed")
            nested = body.get("arguments")
            entrypoint = body.get("entrypoint")
            role_file = body.get("source_artifact")
            if not isinstance(nested, Mapping) or role_file not in {
                    "W24StaticShapeFixture.java", "W24StaticShapeReadback.java"}:
                raise CandidateError("Java setup call source is outside the reviewed W24 allowlist")
            cfg, case = self.slot
            if entrypoint == "W24StaticShapeFixture#run":
                expected = {"action": "build", "case_id": case,
                            "configuration_id": cfg}
                if (self.slot_stage != "begin" or role_file != "W24StaticShapeFixture.java" or
                        dict(nested) != expected):
                    raise CandidateError("fixture build differs from the active preregistered slot")
            elif entrypoint == "W24StaticShapeReadback#run":
                action = nested.get("action")
                if role_file != "W24StaticShapeReadback.java":
                    raise CandidateError("configuration readback source is outside the reviewed allowlist")
                if action == "readback":
                    if (self.slot_stage not in {"pre_save_inspected", "reopened_inspected"} or
                            dict(nested) != {"action": "readback",
                                             "expected_configuration_id": cfg}):
                        raise CandidateError("readback differs from the active preregistered configuration")
                elif action == "save":
                    required = {"action": "save", "workspace_path": str(self.workspace)}
                    if (nested.get("action") != "save" or
                            nested.get("workspace_path") != required["workspace_path"] or
                            set(nested) != {"action", "workspace_path", "path"} or
                            self.slot_stage != "pre_save_readback"):
                        raise CandidateError("save call is not bound to the exact registered workspace")
                    target = Path(str(nested.get("path", "")))
                    expected_name = (f"static_shape_{case}.mph" if cfg == "baseline" else
                                     f"static_shape_{cfg}_{case}.mph")
                    try:
                        output_root = (self.workspace / "outputs").resolve(strict=True)
                    except OSError as exc:
                        raise CandidateError("registered outputs directory is unavailable") from exc
                    if (target.is_symlink() or not target.is_absolute() or
                            target.parent.resolve(strict=True) != output_root or
                            target.name != expected_name or
                            target.suffix.lower() != ".mph"):
                        raise CandidateError("save target escaped the registered outputs directory")
                else:
                    raise CandidateError("Java readback action must be readback or save")
            else:
                raise CandidateError("Java entrypoint is outside the setup-only allowlist")
            return
        raise CandidateError(f"setup-only route fence rejected {operation!r}")

    @staticmethod
    def _worker_terminal(response: Any) -> bool:
        if not isinstance(response, Mapping):
            return False
        data = response.get("data")
        worker = data.get("worker") if isinstance(data, Mapping) else None
        return isinstance(worker, Mapping) and worker.get("status") in {
            "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise CandidateError("setup dispatch request must be a mapping")
        operation = request.get("operation")
        arguments = request.get("arguments", {})
        self._validate_operation(operation, arguments)
        if self.unknown:
            raise CandidateError("an earlier setup call is UNKNOWN; no new route may be dispatched")
        if self.monitor.stop_reason:
            raise CandidateError(f"RSS admission stop blocks new setup routes: {self.monitor.stop_reason}")
        remaining = self._remaining_rpc()
        routed = {key: value for key, value in dict(request).items()}
        routed_arguments = dict(arguments)
        execution = dict(routed.get("execution", {}))
        for timeout_key in ("rpc_timeout_s", "queue_timeout_s", "execution_timeout_s"):
            value = execution.get(timeout_key)
            try:
                requested = float(value) if value is not None else remaining
            except (TypeError, ValueError):
                requested = remaining
            if not math.isfinite(requested) or requested <= 0:
                requested = remaining
            execution[timeout_key] = min(requested, remaining)
        request_id, idempotency_key = execution.get("request_id"), execution.get("idempotency_key")
        if (request_id is None) != (idempotency_key is None):
            raise CandidateError("managed setup call must provide both request_id and idempotency_key or neither")
        if request_id is None:
            request_id, idempotency_key = _request_ids("managed-" + operation.replace(".", "-"))
            execution["request_id"] = request_id
            execution["idempotency_key"] = idempotency_key
        if (not isinstance(request_id, str) or not request_id or
                not isinstance(idempotency_key, str) or not idempotency_key):
            raise CandidateError("managed setup route requires nonempty stable operation identity")
        routed["arguments"] = routed_arguments
        routed["execution"] = execution
        identity_hash = _sha_bytes(request_id.encode("utf-8"))
        route_dir = self.events_path.parent / "managed_routes"
        route_dir.mkdir(parents=True, exist_ok=True)
        intent_path = route_dir / f"{identity_hash}_intent.json"
        _write_new(intent_path, {
            "schema": "W24_SETUP_MANAGED_ROUTE_INTENT_V1",
            "operation": operation, "slot": list(self.slot) if self.slot else None,
            "request_id": request_id, "idempotency_key": idempotency_key,
            "request": routed,
            "persisted_before_dispatch": True,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        if operation == "model_create":
            self.bootstrap_model_create_used = True
        worker_required = operation in {"model_create", "model.inspect", "model_load", "operation_call"}
        try:
            response = self._daemon.dispatch(routed)
        except BaseException as exc:
            self.unknown = True
            _write_new(route_dir / f"{identity_hash}_exception.json", {
                "schema": "W24_SETUP_MANAGED_ROUTE_EXCEPTION_V1",
                "operation": operation, "slot": list(self.slot) if self.slot else None,
                "request_id": request_id, "idempotency_key": idempotency_key,
                "outcome": "UNKNOWN_NO_REPLAY",
                "error_type": type(exc).__name__, "error": str(exc),
                "at_utc": datetime.now(timezone.utc).isoformat(),
            })
            _append(self.events_path, {"event": "setup_dispatch_exception_unknown",
                                       "operation": operation,
                                       "slot": list(self.slot) if self.slot else None,
                                       "request_id": request_id,
                                       "idempotency_key": idempotency_key,
                                       "error_type": type(exc).__name__, "error": str(exc)})
            raise
        worker_status = None
        data = response.get("data") if isinstance(response, Mapping) else None
        worker = data.get("worker") if isinstance(data, Mapping) else None
        if isinstance(worker, Mapping):
            worker_status = worker.get("status")
        if isinstance(response, Mapping):
            route_result = {"schema": "W24_SETUP_MANAGED_ROUTE_RESULT_V1",
                            "operation": operation,
                            "slot": list(self.slot) if self.slot else None,
                            "request_id": request_id,
                            "idempotency_key": idempotency_key,
                            "response": dict(response),
                            "at_utc": datetime.now(timezone.utc).isoformat()}
        else:
            route_result = {"schema": "W24_SETUP_MANAGED_ROUTE_RESULT_V1",
                            "operation": operation, "request_id": request_id,
                            "idempotency_key": idempotency_key,
                            "response_type": type(response).__name__,
                            "terminal_identity": False}
        try:
            _write_new(route_dir / f"{identity_hash}_result.json", route_result)
        except BaseException as exc:
            self.unknown = True
            raise CandidateError("managed setup result could not be durably recorded; preserve the original operation") from exc
        uncertain = (not isinstance(response, Mapping) or
                     response.get("execution_state_unknown") is True or
                     (isinstance(data, Mapping) and data.get("execution_state_unknown") is True) or
                     (worker_required and not self._worker_terminal(response)))
        if uncertain:
            self.unknown = True
        _append(self.events_path, {"event": "setup_dispatch_returned",
                                   "operation": operation,
                                   "slot": list(self.slot) if self.slot else None,
                                   "request_id": request_id,
                                   "idempotency_key": idempotency_key,
                                   "success": response.get("success") is True if isinstance(response, Mapping) else False,
                                   "worker_status": worker_status,
                                   "execution_state_unknown": uncertain,
                                   "remaining_budget_seconds": max(0.0, self.deadline_monotonic - time.monotonic()),
                                   "rss_stop_reason": self.monitor.stop_reason})
        if not isinstance(response, dict):
            self.unknown = True
            raise CandidateError("ControlDaemon returned a non-object setup response")
        if uncertain:
            raise CandidateError("setup route completed without a fully observed terminal identity; no retry or follow-on route")
        if response.get("success") is not True:
            if worker_required:
                self.unknown = True
                raise CandidateError("Worker-backed setup route failed; preserve the original Worker and do not follow up")
            return response
        if worker_required:
            data = response.get("data")
            worker = data.get("worker") if isinstance(data, Mapping) else None
            if not isinstance(worker, Mapping) or worker.get("status") != "SUCCEEDED":
                self.unknown = True
                raise CandidateError("Worker-backed setup route lacks terminal SUCCEEDED status")

        if operation == "model.adopt":
            execution = response.get("execution")
            ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
            if not isinstance(ref, Mapping) or ref.get("model_tag") != self.slot_model_tag:
                self.unknown = True
                raise CandidateError("model.adopt returned a different tag; preserve the active Worker")
            self.slot_stage = "adopted"
        elif operation == "model.inspect":
            self.slot_stage = ("pre_save_inspected" if self.slot_stage == "adopted"
                               else "reopened_inspected")
        elif operation == "model_load":
            self.slot_stage = "loaded"
        elif operation == "operation_call":
            data = response.get("data")
            envelope = data.get("readback") if isinstance(data, Mapping) else None
            nested_result = envelope.get("readback") if isinstance(envelope, Mapping) else None
            body = routed_arguments.get("arguments")
            entrypoint = body.get("entrypoint") if isinstance(body, Mapping) else None
            java_arguments = body.get("arguments") if isinstance(body, Mapping) else None
            action = java_arguments.get("action") if isinstance(java_arguments, Mapping) else None
            if entrypoint == "W24StaticShapeFixture#run":
                tag = nested_result.get("model_tag") if isinstance(nested_result, Mapping) else None
                if not isinstance(tag, str) or not tag:
                    self.unknown = True
                    raise CandidateError("native build response omitted its authoritative model tag")
                self.slot_model_tag = tag
                self.slot_stage = "built"
            elif action == "readback" and self.slot_stage == "pre_save_inspected":
                self.slot_stage = "pre_save_readback"
            elif action == "save":
                path = nested_result.get("path") if isinstance(nested_result, Mapping) else None
                size = nested_result.get("size_bytes") if isinstance(nested_result, Mapping) else None
                digest = nested_result.get("sha256") if isinstance(nested_result, Mapping) else None
                try:
                    supplied_target = Path(str(path))
                    target = supplied_target.resolve(strict=True)
                    outputs = (self.workspace / "outputs").resolve(strict=True)
                except OSError as exc:
                    self.unknown = True
                    raise CandidateError("successful Java save omitted its exact artifact binding") from exc
                if (self.slot_stage != "pre_save_readback" or
                        supplied_target.is_symlink() or not target.is_relative_to(outputs) or
                        not target.is_file() or type(size) is not int or
                        target.stat().st_size != size or digest != _sha_file(target)):
                    self.unknown = True
                    raise CandidateError("Java save result does not bind the exact new slot artifact")
                self.slot_artifact = {"path": str(target), "size_bytes": size,
                                      "sha256": digest}
                self.slot_stage = "saved"
            elif action == "readback" and self.slot_stage == "reopened_inspected":
                self.slot_stage = "readback_complete"
        return response


def validate_current_epoch_binding(binding: Any, *, project_id: str,
                                   session_id: str, server_instance_id: str,
                                   worker_epoch: int) -> dict[str, Any]:
    """Reject all cross-session/epoch ModelRefs before accepting a setup receipt."""
    if not isinstance(binding, Mapping):
        raise CandidateError("setup receipt omitted its ModelRef binding record")
    ref = binding.get("model_ref")
    if not isinstance(ref, Mapping):
        raise CandidateError("setup receipt omitted its current ModelRef")
    if (type(worker_epoch) is not int or worker_epoch < 1 or
            binding.get("project_id") != project_id or binding.get("session_id") != session_id or
            ref.get("session_id") != session_id or ref.get("server_instance_id") != server_instance_id or
            type(ref.get("generation")) is not int or ref.get("generation") != worker_epoch or
            not isinstance(ref.get("model_tag"), str) or not ref.get("model_tag") or
            isinstance(binding.get("revision"), bool) or
            not isinstance(binding.get("revision"), int) or binding["revision"] < 0):
        raise CandidateError("ModelRef belongs to a prior project/session/Worker epoch or has invalid revision")
    return dict(binding)


def _session_identity(response: Mapping[str, Any], *, project_id: str,
                      expected_port: int) -> dict[str, Any]:
    data = response.get("data")
    peer = data.get("observed_peer") if isinstance(data, Mapping) else None
    session_id = data.get("session_id") if isinstance(data, Mapping) else None
    server_id = data.get("server_instance_id") if isinstance(data, Mapping) else None
    worker_id = data.get("worker_instance_id") if isinstance(data, Mapping) else None
    epoch = data.get("worker_epoch") if isinstance(data, Mapping) else None
    endpoint = data.get("endpoint") if isinstance(data, Mapping) else None
    version = data.get("remote_engine_version") if isinstance(data, Mapping) else None
    build = data.get("remote_engine_build") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("project_id") != project_id or
            endpoint not in (f"127.0.0.1:{expected_port}",
                             {"host": "127.0.0.1", "port": expected_port}) or
            not isinstance(peer, Mapping) or peer.get("address") != "127.0.0.1" or
            peer.get("port") != expected_port or
            not all(isinstance(item, str) and item for item in (session_id, server_id, worker_id)) or
            type(epoch) is not int or epoch < 1 or
            not isinstance(version, str) or "6.4" not in version or
            not isinstance(build, str) or "293" not in build):
        raise CandidateError("session.connect did not bind the exact loopback endpoint and COMSOL 6.4.0.293 identity")
    return {"project_id": project_id, "session_id": session_id,
            "server_instance_id": server_id, "worker_instance_id": worker_id,
            "worker_epoch": epoch, "endpoint": endpoint,
            "observed_peer": dict(peer), "remote_engine_version": version,
            "remote_engine_build": build}


def _require_connected_inspect(response: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    lifecycle = data.get("lifecycle") if isinstance(data, Mapping) else None
    binding = data.get("worker_binding") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("project_id") != session.get("project_id") or
            data.get("runtime_live") is not True or not isinstance(lifecycle, Mapping) or
            not isinstance(binding, Mapping) or lifecycle.get("session_id") != session.get("session_id") or
            lifecycle.get("state") != "CONNECTED" or
            lifecycle.get("worker_instance_id") != session.get("worker_instance_id") or
            lifecycle.get("worker_epoch") != session.get("worker_epoch") or
            binding.get("worker_instance_id") != session.get("worker_instance_id") or
            binding.get("worker_epoch") != session.get("worker_epoch")):
        raise CandidateError("session.inspect did not confirm the exact connected Worker epoch")
    return dict(response)


def _request_ids(label: str) -> tuple[str, str]:
    token = uuid4().hex
    return f"w24-setup-{label}-{token}", f"w24-setup-idem-{label}-{token}"


def _remaining_admission_rpc(deadline_monotonic: float, preferred_seconds: float) -> float:
    remaining = deadline_monotonic - time.monotonic() - CLEANUP_RESERVE_SECONDS
    if not math.isfinite(remaining) or remaining <= 1.0:
        raise CandidateError("native setup is inside its reserved cleanup window; no new RPC may start")
    return min(float(preferred_seconds), remaining)


def _direct_dispatch(daemon: Any, evidence: Path, *, operation: str,
                     arguments: Mapping[str, Any], project_id: str | None = None,
                     session_id: str | None = None, request_label: str,
                     timeout_seconds: float, request_id: str | None = None,
                     idempotency_key: str | None = None) -> dict[str, Any]:
    generated_request_id, generated_key = _request_ids(request_label)
    request_id = request_id or generated_request_id
    key = idempotency_key or generated_key
    execution: dict[str, Any] = {
        "request_id": request_id, "idempotency_key": key,
        "rpc_timeout_s": timeout_seconds, "queue_timeout_s": min(60.0, timeout_seconds),
        "execution_timeout_s": timeout_seconds,
    }
    if project_id is not None:
        execution["project_id"] = project_id
    if session_id is not None:
        execution["session_id"] = session_id
    request = {"operation": operation, "arguments": dict(arguments), "execution": execution}
    route_dir = evidence / "direct_routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    index = len(list(route_dir.glob("*_intent.json"))) + 1
    stem = f"{index:03d}_{request_label}"
    _write_new(route_dir / f"{stem}_intent.json", {
        "schema": "W24_SETUP_DIRECT_ROUTE_INTENT_V1",
        "operation": operation, "request_id": request_id,
        "idempotency_key": key, "request": request,
        "persisted_before_dispatch": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    try:
        response = daemon.dispatch(request)
    except BaseException as exc:
        try:
            _write_new(route_dir / f"{stem}_exception.json", {
                "schema": "W24_SETUP_DIRECT_ROUTE_EXCEPTION_V1",
                "operation": operation, "request_id": request_id,
                "idempotency_key": key, "outcome": "UNKNOWN_NO_REPLAY",
                "error_type": type(exc).__name__, "error": str(exc),
                "at_utc": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            raise OutcomeUnknown(f"ControlDaemon {operation} raised after persisted intent; do not replay") from exc
    if not isinstance(response, Mapping):
        _write_new(route_dir / f"{stem}_result.json", {
            "schema": "W24_SETUP_DIRECT_ROUTE_RESULT_V1",
            "operation": operation, "request_id": request_id,
            "idempotency_key": key, "response_type": type(response).__name__,
            "response_repr": repr(response), "terminal_identity": False,
        })
        raise OutcomeUnknown(f"ControlDaemon {operation} returned a non-object result; do not replay")
    try:
        _write_new(route_dir / f"{stem}_result.json",
                   {"schema": "W24_SETUP_DIRECT_ROUTE_RESULT_V1",
                    "operation": operation, "request_id": request_id,
                    "idempotency_key": key, "response": dict(response),
                    "at_utc": datetime.now(timezone.utc).isoformat()})
    except BaseException as exc:
        raise OutcomeUnknown(f"ControlDaemon {operation} returned but durable response receipt failed; do not replay") from exc
    data = response.get("data")
    if (response.get("execution_state_unknown") is True or
            isinstance(data, Mapping) and data.get("execution_state_unknown") is True):
        raise OutcomeUnknown(f"ControlDaemon {operation} returned UNKNOWN; preserve the original operation identity")
    return dict(response)


def _project_create_before_birth(daemon: Any, authorized_root: Path,
                                 evidence: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from tools.run_native_w24_cure_preflight import (
        PROJECT_WORKSPACE_NAME as PROJECT_WORKSPACE,
        _require_trusted_code_host_grant, _verify_registered_workspace,
    )

    host_grants = _require_trusted_code_host_grant(daemon)
    create_id, create_key = _request_ids("project-create")
    policy = {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}
    create_arguments = {
        "label": "W24 static-shape setup campaign",
        "workspace": PROJECT_WORKSPACE,
        "policy": policy,
        "idempotency_key": create_key,
        "request_id": create_id,
    }
    response = _direct_dispatch(
        daemon, evidence, operation="project.create", arguments=create_arguments,
        request_label="project_create", timeout_seconds=30.0,
        request_id=create_id, idempotency_key=create_key)
    if response.get("success") is not True:
        raise OutcomeUnknown("project.create did not return a confirmed success; preserve its original operation identity")
    data = response.get("data")
    record = data.get("project") if isinstance(data, Mapping) else None
    if not isinstance(record, Mapping):
        raise OutcomeUnknown("project.create returned without its authoritative project record")
    project_id, workspace = record.get("project_id"), record.get("workspace")
    if (not isinstance(project_id, str) or not project_id or
            not isinstance(workspace, str) or not Path(workspace).is_absolute()):
        raise OutcomeUnknown("project.create omitted the authoritative project/workspace binding")
    try:
        project_id, resolved = _verify_registered_workspace(dict(record), authorized_root)
    except BaseException as exc:
        raise OutcomeUnknown("project.create workspace identity could not be verified") from exc
    if project_id != record.get("project_id") or str(resolved) != workspace:
        raise OutcomeUnknown("registered project workspace did not survive its exact filesystem readback")
    permissions = record.get("policy", {}).get("permissions") if isinstance(record.get("policy"), Mapping) else None
    if not isinstance(permissions, list) or set(permissions) != set(policy["permissions"]):
        raise OutcomeUnknown("project.create did not persist the exact reviewed policy")
    inspect_id, inspect_key = _request_ids("project-inspect")
    inspection = _direct_dispatch(
        daemon, evidence, operation="project.inspect",
        arguments={"project_id": project_id, "request_id": inspect_id},
        project_id=project_id, request_label="project_inspect_prebirth",
        timeout_seconds=30.0, request_id=inspect_id,
        idempotency_key=inspect_key)
    inspect_data = inspection.get("data")
    inspected = inspect_data.get("project") if isinstance(inspect_data, Mapping) else None
    effective = inspect_data.get("effective_permissions") if isinstance(inspect_data, Mapping) else None
    inspect_policy = inspected.get("policy") if isinstance(inspected, Mapping) else None
    inspect_permissions = inspect_policy.get("permissions") if isinstance(inspect_policy, Mapping) else None
    if (inspection.get("success") is not True or not isinstance(inspected, Mapping) or
            inspected.get("project_id") != project_id or inspected.get("workspace") != workspace or
            not isinstance(inspect_permissions, list) or
            set(inspect_permissions) != set(policy["permissions"]) or
            not isinstance(effective, list) or "trusted_code" not in effective):
        raise OutcomeUnknown("production project.inspect failed exact project/policy readback")
    created = {"status": "PROJECT_CREATED_BEFORE_ENGINE_BIRTH",
               "project_id": project_id, "workspace": workspace,
               "response": dict(response), "inspection_response": dict(inspection),
               "host_grants": host_grants,
               "request_id": create_id, "idempotency_key": create_key}
    _write_new(evidence / "project_create_and_inspect_prebirth.json", created)
    return created, dict(response)


def _worker_identity_from_context(daemon: Any, project_id: str,
                                  session: Mapping[str, Any]) -> tuple[Any, Any, dict[str, Any], Path]:
    from comsol_mcp._g2_isolation import _process_snapshot
    from comsol_mcp._session_context import session_state_directory

    context = daemon.session_registry.get(project_id, str(session["session_id"]))
    if (getattr(context, "project_id", None) != project_id or
            getattr(context, "session_id", None) != session.get("session_id") or
            getattr(context, "worker_instance_id", None) != session.get("worker_instance_id") or
            getattr(context, "worker_epoch", None) != session.get("worker_epoch")):
        raise CandidateError("registered SessionRuntimeContext differs from the exact public connect identity")
    worker = getattr(context, "worker", None)
    process = getattr(worker, "_process", None)
    pid = getattr(process, "pid", None)
    if (process is None or type(pid) is not int or pid <= 1 or process.poll() is not None):
        raise CandidateError("public connected Worker lacks its exact live Popen handle")
    identity = _process_snapshot(pid)
    if (not isinstance(identity, Mapping) or identity.get("pid") != pid or
            not isinstance(identity.get("birth"), str) or not identity.get("birth") or
            not isinstance(identity.get("command_sha256"), str)):
        raise CandidateError("connected Worker Popen birth/command identity is unavailable")
    runtime = daemon._session_runtime_configs.get((project_id, str(session["session_id"])))
    if runtime is None:
        raise CandidateError("ControlDaemon did not retain this exact session runtime configuration")
    worker_state = session_state_directory(runtime.session_state_root, project_id,
                                           str(session["session_id"])) / "worker"
    if worker_state.resolve(strict=True) != Path(worker.state_dir).resolve(strict=True):
        raise CandidateError("Worker log root differs from the daemon's exact project/session state directory")
    return context, worker, dict(identity), worker_state


def _verify_worker_java_options(worker_state: Path) -> dict[str, Any]:
    log_path = worker_state / "worker.stderr.log"
    if log_path.is_symlink() or not log_path.is_file():
        raise CandidateError("actual managed Worker stderr log is unavailable")
    payload = log_path.read_bytes()
    text = payload.decode("utf-8", "replace")
    matched = f"Picked up JAVA_TOOL_OPTIONS: {JAVA_TOOL_OPTIONS}" in text
    evidence = {"status": "JAVA_TOOL_OPTIONS_OBSERVED" if matched else "JAVA_TOOL_OPTIONS_NOT_OBSERVED",
                "path": str(log_path), "sha256": _sha_bytes(payload),
                "bytes": len(payload), "required_options": JAVA_TOOL_OPTIONS,
                "observed": matched}
    if not matched:
        raise CandidateError("actual Worker stderr did not confirm the frozen JAVA_TOOL_OPTIONS")
    return evidence


def _model_create_binding(response: Mapping[str, Any], *, project_id: str,
                          session: Mapping[str, Any]) -> Any:
    from tools.run_native_w24_cure_science import ManagedModelBinding

    execution = response.get("execution")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if (response.get("success") is not True or not isinstance(execution, Mapping) or
            execution.get("project_id") != project_id or
            execution.get("session_id") != session.get("session_id") or
            not isinstance(ref, Mapping) or
            ref.get("session_id") != session.get("session_id") or
            ref.get("server_instance_id") != session.get("server_instance_id") or
            ref.get("generation") != session.get("worker_epoch") or
            not isinstance(ref.get("model_tag"), str) or not ref.get("model_tag") or
            isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise CandidateError("production model_create did not return the exact current session/server Worker epoch")
    binding = ManagedModelBinding(project_id, str(session["session_id"]), dict(ref), revision)
    validate_current_epoch_binding(binding.as_record(),
        project_id=project_id, session_id=str(session["session_id"]),
        server_instance_id=str(session["server_instance_id"]),
        worker_epoch=int(session["worker_epoch"]))
    return binding


def _terminal_campaign_ledger(daemon: Any, project_id: str) -> dict[str, Any]:
    from tools.run_native_w24_cure_preflight import (
        _actual_study_run_submissions, _project_job_inventory,
        _worker_request_activity_inventory,
    )

    jobs = _project_job_inventory(daemon, project_id)
    workers = _worker_request_activity_inventory(daemon.store, project_id)
    study_runs = _actual_study_run_submissions(daemon, project_id)
    safe = (jobs.get("status") == "TERMINAL" and jobs.get("active") == [] and
            jobs.get("unknown") == [] and workers.get("safe_to_cleanup") is True and
            workers.get("execution_state_unknown_request_ids") == [] and
            workers.get("pending_request_ids") == [] and
            workers.get("orphan_observed_request_ids") == [] and
            workers.get("ambiguous_events") == [] and study_runs == [])
    return {"status": "SAFE_TO_RETIRE" if safe else "ACTIVE_OR_UNKNOWN",
            "safe_to_retire": safe, "project_jobs": jobs,
            "worker_request_activity": workers,
            "study_run_submissions": study_runs,
            "study_run_submission_count": len(study_runs)}


def _stop_exact_server(server: Any, expected: Mapping[str, Any], port: int,
                       *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """TERM only; timeout retains the exact Popen and all evidence."""
    proc = getattr(server, "proc", None)
    if proc is None or getattr(proc, "pid", None) != expected.get("pid"):
        raise CandidateError("server stop lacks the exact task Popen handle")
    if type(expected.get("start_epoch_ms")) is not int or expected["start_epoch_ms"] <= 0:
        raise CandidateError("server stop lacks a fresh Popen birth epoch")
    from comsol_mcp._platform_process import process_identity
    from comsol_mcp._g2_isolation import _process_snapshot

    if proc.poll() is None:
        observed = process_identity(proc.pid)
        if (not isinstance(observed, Mapping) or observed.get("alive") is not True or
                observed.get("start_epoch_ms") != expected["start_epoch_ms"]):
            raise CandidateError("server PID/birth changed before TERM; exact process retained")
        snapshot = _process_snapshot(proc.pid)
        if (not isinstance(snapshot, Mapping) or
                snapshot.get("birth") != expected.get("birth") or
                snapshot.get("command_sha256") != expected.get("command_sha256")):
            raise CandidateError("server command/birth snapshot changed before TERM; exact process retained")
        listener = _lsof_listener(proc.pid, port)
        if (listener.get("exit_code") != 0 or listener.get("rows") !=
                [{"pid": proc.pid, "endpoint": f"127.0.0.1:{port}"}]):
            raise CandidateError("server stop lacks the exact owned loopback listener proof")
        proc.terminate()
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise CandidateError("server did not exit after SIGTERM; no SIGKILL fallback, handle retained") from exc
    if proc.poll() is None:
        raise CandidateError("server child remains live after TERM wait")
    after = _lsof_listener(proc.pid, port)
    if after.get("rows"):
        raise CandidateError("server listener remains after exact child exit")
    return {"status": "STOPPED_AND_REAPED", "pid": proc.pid,
            "start_epoch_ms": expected["start_epoch_ms"],
            "signal": "SIGTERM", "sigkill_used": False,
            "child_exit_confirmed": True, "child_reaped": True,
            "listener_absent": True, "post_stop_lsof_exit_code": after.get("exit_code"),
            "post_stop_lsof_stdout_sha256": after.get("stdout_sha256")}


def _validate_slot_receipt(result: Mapping[str, Any], *, slot: Mapping[str, Any],
                           project_id: str, session: Mapping[str, Any],
                           workspace: Path, expected_sources: Mapping[str, Any]) -> dict[str, Any]:
    from tools.run_native_w24_static_shape_setup import CampaignError

    config, case = slot["configuration_id"], slot["case_id"]
    if (result.get("status") != "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE" or
            result.get("configuration_id") != config or result.get("case_id") != case or
            result.get("project_id") != project_id or
            result.get("native_acceptance") != "NOT_RUN" or
            result.get("phase_initialization_executed") is not False or
            result.get("study_run_submission_count") != 0 or
            result.get("study_run_submissions") != []):
        raise CandidateError("setup adapter receipt is incomplete or reports a solve/acceptance")
    checked_bindings: list[dict[str, Any]] = []
    for key in ("parent_model_binding", "new_model_binding", "reopened_model_binding",
                "reopened_configuration_readback_binding"):
        binding = validate_current_epoch_binding(
            result.get(key), project_id=project_id,
            session_id=str(session["session_id"]),
            server_instance_id=str(session["server_instance_id"]),
            worker_epoch=int(session["worker_epoch"]))
        checked_bindings.append(binding)
    if (checked_bindings[2]["model_ref"] != checked_bindings[3]["model_ref"] or
            checked_bindings[3]["revision"] > checked_bindings[2]["revision"]):
        raise CandidateError("reopened configuration/getSize evidence lost its precise pre-readback binding")
    if result.get("readback_comparison", {}).get("matches") is not True:
        raise CandidateError("saved/reopened full parameter/mesh/solver readback did not compare exactly")
    sources = result.get("source_sha256")
    if (not isinstance(sources, Mapping) or
            {role: sources.get(role) for role in ("fixture", "readback")} !=
            {role: expected_sources.get(role, {}).get("sha256") for role in ("fixture", "readback")}):
        raise CandidateError("setup receipt Java source hashes differ from the frozen source copies")
    artifact = result.get("project_artifact")
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str):
        raise CandidateError("setup receipt omitted the saved MPH artifact binding")
    target = Path(artifact["path"])
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("saved MPH artifact is missing") from exc
    outputs = (workspace / "outputs").resolve(strict=True)
    if (target.is_symlink() or not resolved.is_relative_to(outputs) or
            resolved.suffix.lower() != ".mph" or not resolved.is_file()):
        raise CandidateError("saved MPH artifact escaped the exact registered outputs directory")
    size = resolved.stat().st_size
    digest = _sha_file(resolved)
    if (size <= 0 or size > MAX_SINGLE_MPH_BYTES or
            artifact.get("size_bytes") != size or artifact.get("sha256") != digest):
        raise CandidateError("saved MPH exceeds its slot budget or differs from native save hash/size")
    return {"artifact": {"path": str(resolved), "size_bytes": size,
                         "sha256": digest},
            "model_tags": [row["model_tag"] for row in checked_bindings[1:3]],
            "bindings": checked_bindings,
            "setup_science_status": "NOT_RUN",
            "binding_validity_after_worker_retirement": "HISTORICAL_ONLY_REQUIRES_NEW_WORKER_RE_ADMISSION"}


def _expected_child_environment(work: Path, repo: Path) -> dict[str, str]:
    site_packages = EXPLICIT_SITE_PACKAGES.resolve(strict=True)
    return {
        "COMSOL_ROOT": str(work / "comsol-shadow"),
        "COMSOL_JAVA_HOME": str(JAVA11.resolve(strict=True)),
        "JAVA_HOME": str(JAVA11.resolve(strict=True)),
        "COMSOL_PREFS_DIR": str(work / "runtime/prefs"),
        "COMSOL_PROJECT_ROOT": str(work / "project"),
        "COMSOL_SERVER_MCP_HOME": str(work / "mcp-home"),
        "COMSOL_MCP_TRUSTED_CODE": "1",
        "COMSOL_MCP_ISOLATION_RECEIPT": str(work / "isolation_receipt.json"),
        "COMSOL_SERVER_VERSION": "6.4.0.293",
        "JAVA_TOOL_OPTIONS": JAVA_TOOL_OPTIONS,
        "PYTHONPATH": os.pathsep.join((str(repo.resolve(strict=True)), str(site_packages))),
    }


def _verify_execution_environment(work: Path, repo: Path) -> dict[str, Any]:
    expected = _expected_child_environment(work, repo)
    mismatches = {key: {"expected": value, "observed": os.environ.get(key)}
                  for key, value in expected.items()
                  if os.environ.get(key) != value}
    if mismatches:
        raise CandidateError("candidate subprocess environment does not match its private COMSOL/JVM binding: " +
                             json.dumps(mismatches, ensure_ascii=False))
    return {"status": "ISOLATED_CANDIDATE_ENVIRONMENT_MATCHED",
            "environment_variables": {key: value for key, value in expected.items()
                                       if key != "JAVA_TOOL_OPTIONS"},
            "java_tool_options_sha256": _sha_bytes(JAVA_TOOL_OPTIONS.encode()),
            "parent_process_environment_mutated": False,
            "worker_java_tool_options_source": "inherited from this exact isolated candidate process by PersistentJavaWorker Popen env copy",
            "server_java_tool_options_source": "explicit env=dict(os.environ) on exact COMSOL server Popen"}


def _verify_frozen_runtime(repo: Path, freeze: Mapping[str, Any],
                           install: Path, jdk: Path) -> dict[str, Any]:
    from comsol_mcp._java_worker import JavaWorkerPaths

    paths = JavaWorkerPaths(install, jdk, project_root=repo)
    classpath, manifest_hash, jar_count, jar_hash = paths.classpath()
    recorded = freeze.get("compile", {})
    runtime = freeze.get("runtime", {})
    observed = {"classpath_manifest_sha256": manifest_hash,
                "classpath_jar_count": jar_count,
                "classpath_jar_content_fingerprint_sha256": jar_hash,
                "comsol_version": paths.comsol_version_info(),
                "jdk_version": paths.jdk_version_info()}
    if any(observed.get(key) != recorded.get(key) for key in observed):
        raise CandidateError("COMSOL/JDK/classpath differs from the frozen offline compile receipt")
    if runtime.get("comsol_install_root") != str(install.resolve(strict=True)):
        raise CandidateError("COMSOL install root differs from the frozen candidate")
    if runtime.get("external_jdk_home") != str(jdk.resolve(strict=True)):
        raise CandidateError("external JDK differs from the frozen candidate")
    if Path(sys.executable).resolve(strict=True) != EXPECTED_PYTHON.resolve(strict=True):
        raise CandidateError(f"candidate must use the exact Python executable {EXPECTED_PYTHON}")
    if sys.version_info[:2] != (3, 12):
        raise CandidateError(f"candidate requires Python 3.12, got {sys.version.split()[0]}")
    return {"status": "FROZEN_RUNTIME_RECHECK_PASS",
            "python_executable": str(Path(sys.executable).resolve(strict=True)),
            "python_version": sys.version.split()[0],
            "classpath_manifest_sha256": manifest_hash,
            "classpath_jar_count": jar_count,
            "classpath_jar_content_fingerprint_sha256": jar_hash,
            "comsol_version": observed["comsol_version"],
            "jdk_version": observed["jdk_version"],
            "classpath_entry_count": len([entry for entry in classpath.split(paths.classpath_separator) if entry])}


def _check_process_size(work: Path) -> dict[str, Any]:
    output = work / "project/science/outputs"
    rows: list[dict[str, Any]] = []
    total = 0
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise CandidateError("project outputs directory became an alias or non-directory")
        for path in sorted(output.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise CandidateError("project output directory contains a symlink/non-file")
            size = path.stat().st_size
            total += size
            rows.append({"name": path.name, "size_bytes": size,
                         "sha256": _sha_file(path)})
            if path.suffix.lower() == ".mph" and size > MAX_SINGLE_MPH_BYTES:
                raise CandidateError(f"MPH exceeds the 1 GiB per-file cap: {path.name}")
    if total > MAX_OUTPUT_BYTES:
        raise CandidateError("aggregate project outputs exceed the 10 GiB candidate cap")
    return {"files": rows, "total_bytes": total,
            "max_single_mph_bytes": MAX_SINGLE_MPH_BYTES,
            "max_total_project_output_bytes": MAX_OUTPUT_BYTES}


def _safe_session_from_lifecycle(daemon: Any, project_id: str,
                                 port: int) -> dict[str, Any] | None:
    try:
        rows = daemon.session_lifecycle.list_for_project(project_id)
    except Exception:
        return None
    matches = [row for row in rows if isinstance(row, Mapping) and
               row.get("endpoint") == {"host": "127.0.0.1", "port": port}]
    if len(matches) != 1:
        return None
    row = matches[0]
    keys = ("project_id", "session_id", "runtime_id", "endpoint", "state",
            "client_state", "worker_instance_id", "worker_epoch", "server_instance_id")
    return {key: row.get(key) for key in keys}


def _validate_worker_retirement_response(response: Mapping[str, Any], *,
                                        project_id: str, session_id: str,
                                        worker_instance_id: str,
                                        connected_epoch: int) -> dict[str, Any]:
    """Prove the real ControlDaemon retired only the expected exact Worker."""
    data = response.get("data") if isinstance(response, Mapping) else None
    proof = data.get("worker_retirement") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("project_id") != project_id or data.get("session_id") != session_id or
            data.get("state") != "DISCONNECTED" or data.get("client_state") != "RETIRED" or
            data.get("server_stopped") is not False or not isinstance(proof, Mapping)):
        raise CandidateError("session.disconnect did not prove an exact Worker-only retirement")
    identity = proof.get("process_identity")
    pid = identity.get("pid") if isinstance(identity, Mapping) else None
    birth = identity.get("start_epoch_ms") if isinstance(identity, Mapping) else None
    retired_epoch = proof.get("worker_epoch")
    if (proof.get("status") != "RETIRED" or
            proof.get("worker_instance_id") != worker_instance_id or
            type(retired_epoch) is not int or retired_epoch <= connected_epoch or
            type(pid) is not int or pid <= 1 or type(birth) is not int or birth <= 0 or
            proof.get("exact_popen_handle") is not True or
            proof.get("birth_identity_matched_before_close") is not True or
            proof.get("child_exit_confirmed") is not True or
            proof.get("child_reaped") is not True or
            proof.get("admission_fence") != "RETIRED" or
            proof.get("disconnect_rpc_dispatched") is not True or
            proof.get("worker_close_started") is not True):
        raise CandidateError("Worker retirement proof does not bind the expected exact child and epoch")
    return dict(proof)


def _validate_disconnected_session_inspect(response: Mapping[str, Any], *,
                                           project_id: str, session_id: str,
                                           worker_instance_id: str,
                                           worker_epoch: int) -> None:
    """Require public readback to confirm the exact detached Worker epoch."""
    data = response.get("data") if isinstance(response, Mapping) else None
    lifecycle = data.get("lifecycle") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("runtime_live") is not False or not isinstance(lifecycle, Mapping) or
            lifecycle.get("project_id") != project_id or lifecycle.get("session_id") != session_id or
            lifecycle.get("state") != "DISCONNECTED" or
            lifecycle.get("client_state") != "RETIRED" or
            lifecycle.get("worker_instance_id") != worker_instance_id or
            lifecycle.get("worker_epoch") != worker_epoch or
            data.get("worker_binding") is not None):
        raise CandidateError("session.inspect did not prove this exact retired session and Worker epoch")


def _retire_worker_and_server(*, daemon: Any, server: Any,
                              monitor: SampledRssMonitor,
                              project_id: str, session: Mapping[str, Any],
                              run_dir: Path, deadline_monotonic: float) -> dict[str, Any]:
    ledger = _terminal_campaign_ledger(daemon, project_id)
    _write_new(run_dir / "pre_retirement_ledger.json", ledger)
    if ledger.get("safe_to_retire") is not True:
        raise CandidateError("durable job/Worker-request ledger is not fully terminal; preserve the exact Worker")
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 1.0:
        raise CandidateError("wall-clock budget is exhausted before exact cleanup")
    timeout = min(float(RPC_CAP_SECONDS), remaining)
    monitor.set_worker_required_live(False)
    monitor.sample_once()
    response = _direct_dispatch(
        daemon, run_dir, operation="session.disconnect",
        arguments={"session_id": str(session["session_id"]), "retire_worker": True},
        project_id=project_id, session_id=str(session["session_id"]),
        request_label="session_disconnect_retire_worker", timeout_seconds=timeout)
    proof = _validate_worker_retirement_response(
        response, project_id=project_id, session_id=str(session["session_id"]),
        worker_instance_id=str(session["worker_instance_id"]),
        connected_epoch=int(session["worker_epoch"]))
    _write_new(run_dir / "worker_retirement_proof.json", proof)
    inspect = _direct_dispatch(
        daemon, run_dir, operation="session.inspect",
        arguments={"session_id": str(session["session_id"])},
        project_id=project_id, session_id=str(session["session_id"]),
        request_label="session_inspect_retired", timeout_seconds=min(30.0, timeout))
    _validate_disconnected_session_inspect(
        inspect, project_id=project_id, session_id=str(session["session_id"]),
        worker_instance_id=str(session["worker_instance_id"]),
        worker_epoch=int(proof["worker_epoch"]))
    _write_new(run_dir / "disconnected_session_inspect.json", inspect)
    monitor.sample_once()
    monitor.stop()
    close_status = "RETURNED"
    try:
        daemon.close()
    except BaseException as exc:
        close_status = f"FAILED:{type(exc).__name__}:{exc}"
        raise CandidateError("ControlDaemon close failed after Worker retirement; server handle retained") from exc
    if not isinstance(server.process_identity, Mapping):
        raise CandidateError("exact server process identity is unavailable after Worker retirement")
    expected = {"pid": server.proc.pid if server.proc is not None else None,
                "start_epoch_ms": server.process_identity.get("start_epoch_ms"),
                "birth": server.process_identity.get("birth"),
                "command_sha256": server.process_identity.get("command_sha256")}
    stopped = _stop_exact_server(server, expected, int(server.port), timeout_seconds=min(30.0, timeout))
    try:
        server._server_log_handle.close()
    except Exception:
        pass
    _write_new(run_dir / "server_stop_receipt.json", stopped)
    return {"status": "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED",
            "pre_retirement_ledger_sha256": _sha_file(run_dir / "pre_retirement_ledger.json"),
            "worker_retirement_proof_sha256": _sha_file(run_dir / "worker_retirement_proof.json"),
            "disconnected_inspect_sha256": _sha_file(run_dir / "disconnected_session_inspect.json"),
            "control_daemon_close": close_status,
            "server_stop_sha256": _sha_file(run_dir / "server_stop_receipt.json"),
            "rss_monitor_final_sample": monitor.last_sample,
            "rss_stop_reason": monitor.stop_reason}


def _unknown_hold(*, daemon: Any, server: Any, monitor: SampledRssMonitor | None,
                  run_dir: Path, work: Path, project_id: str | None,
                  session: Mapping[str, Any] | None, candidate_sha256: str,
                  reason: str, stop_event: threading.Event | None = None) -> None:
    """Persist UNKNOWN identity and retain all process/runtime handles indefinitely."""
    lifecycle = (_safe_session_from_lifecycle(daemon, project_id,
                  int(server.port) if type(server.port) is int else -1)
                 if daemon is not None and isinstance(project_id, str) else None)
    context_rows = []
    try:
        contexts = (daemon.session_registry.list_for_project(project_id)
                    if daemon is not None and isinstance(project_id, str) else [])
        for context in contexts:
            worker = getattr(context, "worker", None)
            proc = getattr(worker, "_process", None)
            pid = getattr(proc, "pid", None)
            context_rows.append({"session_id": getattr(context, "session_id", None),
                                 "worker_instance_id": getattr(context, "worker_instance_id", None),
                                 "worker_epoch": getattr(context, "worker_epoch", None),
                                 "worker_pid": pid,
                                 "worker_returncode": proc.poll() if proc is not None else None,
                                 "worker_state_dir": str(getattr(worker, "state_dir", ""))})
    except Exception as exc:
        context_rows = [{"inventory_error": f"{type(exc).__name__}: {exc}"}]
    hold = {"schema": "W24_SETUP_UNKNOWN_HANDLE_HOLD_V1",
            "status": "UNKNOWN_PRESERVED_NO_REPLAY_NO_CLOSE_NO_SERVER_STOP",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidate_sha256": candidate_sha256, "project_id": project_id,
            "session": dict(session) if isinstance(session, Mapping) else None,
            "lifecycle": lifecycle, "registered_contexts": context_rows,
            "server_popen": {"pid": server.proc.pid if server.proc is not None else None,
                             "birth": server.server_snapshot.get("birth") if server.server_snapshot else None,
                             "command_sha256": server.server_snapshot.get("command_sha256") if server.server_snapshot else None,
                             "returncode": server.proc.poll() if server.proc is not None else None,
                             "port": server.port},
            "rss_stop_reason": (monitor.stop_reason if monitor is not None
                                else "RSS_MONITOR_HANDLE_UNAVAILABLE"),
            "reason": reason,
            "policy": "No automatic retry, Worker retirement, daemon close, server stop, or restart while UNKNOWN. Reconcile the original durable operation and process handles outside this run before any future action."}
    _write_new(run_dir / "unknown_handle_hold.json", hold)
    _append(run_dir / "events.jsonl", {"event": "unknown_handle_hold_entered",
                                        "at_utc": datetime.now(timezone.utc).isoformat(),
                                        "receipt": str(run_dir / "unknown_handle_hold.json")})
    _hold_forever_with_handles(monitor, stop_event=stop_event)


def execute_candidate(*, repo: Path, evidence: Path, work: Path,
                      reviewed_sha256: str,
                      install: Path = INSTALL_ROOT, jdk: Path = JAVA11) -> dict[str, Any]:
    """Execute exactly the reviewed 14-slot setup-only campaign.

    This function is intentionally never invoked during software-only review.
    It requires an explicit candidate SHA, a new private work path, the exact
    environment contract, a fresh scoped inventory, and zero-solve route guards.
    """
    python_isolation = configure_archive_python(repo)
    freeze = verify_candidate(repo=repo, evidence=evidence,
                              reviewed_sha256=reviewed_sha256)
    if freeze.get("source", {}).get("checkout_kind") != "git_archive_plus_explicit_overlay":
        raise CandidateError("native setup may execute only from the frozen published git archive plus overlay")
    if Path.cwd().resolve(strict=True) != repo.resolve(strict=True):
        raise CandidateError("native setup cwd must be the exact isolated source archive root")
    if sys.platform != "darwin":
        raise CandidateError("this W24 native setup candidate is frozen for macOS only")
    if not isinstance(python_isolation, Mapping) or python_isolation.get("site_processing_disabled") is not True:
        raise CandidateError("candidate Python no-site import isolation did not pass")
    runtime_recheck = _verify_frozen_runtime(repo, freeze, install, jdk)
    if evidence.exists() is False or evidence.is_symlink():
        raise CandidateError("reviewed candidate evidence directory is missing or aliased")
    if (work.exists() or work.is_symlink() or
            not str(work).startswith(WORK_PREFIX) or
            not work.resolve().is_relative_to(Path("/private/tmp"))):
        raise CandidateError("candidate work path must be new and below the frozen private temporary prefix")
    environment_receipt = _verify_execution_environment(work, repo)
    if work.parent.resolve(strict=True) != Path("/private/tmp").resolve(strict=True):
        raise CandidateError("candidate work directory must be a direct private /private/tmp child")
    work.mkdir(mode=0o700, parents=False, exist_ok=False)
    run_dir = evidence / "native-setup-run"
    if run_dir.exists() or run_dir.is_symlink():
        raise CandidateError("native setup evidence directory must be new")
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    events_path = run_dir / "events.jsonl"
    with events_path.open("xb") as stream:
        stream.write(b"")
        stream.flush()
        os.fsync(stream.fileno())

    origins = audit_archive_imports(repo)
    _write_new(run_dir / "python_isolation.json", python_isolation)
    _write_new(run_dir / "runtime_recheck.json", runtime_recheck)
    _write_new(run_dir / "candidate_environment.json", environment_receipt)
    monitor_box: dict[str, SampledRssMonitor] = {}
    worker_root = work / "mcp-home/session-runtime-state/sessions"
    server = W24StaticShapeSetupServer(
        work, run_dir, event_log=events_path,
        on_birth=lambda proc, ident: _start_rss_monitor(
            monitor_box, proc, ident, worker_root, events_path))
    daemon = None
    gate: BudgetedSetupDaemon | None = None
    monitor: SampledRssMonitor | None = None
    project_id: str | None = None
    project_response: dict[str, Any] | None = None
    project_creation: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    worker_state = "NEVER_DISPATCHED"
    setup_receipts: list[dict[str, Any]] = []
    model_tags: set[str] = set()
    total_outputs = 0
    status = "FAILED_BEFORE_NATIVE_BIRTH"
    error: dict[str, Any] | None = None
    cleanup: dict[str, Any] | None = None
    slots = build_setup_slots()
    receipt_base: dict[str, Any] = {
        "schema": "W24_STATIC_SHAPE_SETUP_CAMPAIGN_RUN_V1",
        "status": "RUNNING_SETUP_ONLY",
        "candidate_sha256": reviewed_sha256,
        "base_commit": BASE_COMMIT,
        "source_closure_sha256": freeze["source"]["source_closure_sha256"],
        "evidence_dir": str(run_dir.resolve()), "work_dir": str(work.resolve()),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_isolation": python_isolation,
        "runtime_recheck": runtime_recheck,
        "runtime_module_origins": origins,
        "route_allowlist": ROUTES,
        "planned_slots": slots,
        "planned_slot_count": len(slots),
        "study_run_calls": 0, "solver_calls": 0,
        "native_scientific_acceptance": "NOT_RUN",
        "future_science_binding_policy": SETUP_BUDGET["future_science_binding_policy"],
    }
    _append(events_path, {"event": "candidate_execution_start",
                          "at_utc": datetime.now(timezone.utc).isoformat(),
                          "candidate_sha256": reviewed_sha256,
                          "planned_slots": len(slots)})

    def stop_server_known() -> dict[str, Any] | None:
        nonlocal monitor
        if server.proc is None:
            return None
        expected_snapshot = server.server_snapshot
        if not isinstance(expected_snapshot, Mapping):
            raise CandidateError("server Popen exists without birth/command identity; preserve exact handle")
        if monitor is not None:
            monitor.stop()
        expected = {"pid": server.proc.pid,
                    "start_epoch_ms": expected_snapshot.get("start_epoch_ms"),
                    "birth": expected_snapshot.get("birth"),
                    "command_sha256": expected_snapshot.get("command_sha256")}
        if type(server.port) is not int:
            raise CandidateError("server has no verified port; preserve exact Popen until identity is reconciled")
        stopped = _stop_exact_server(server, expected, int(server.port))
        try:
            server._server_log_handle.close()
        except Exception:
            pass
        _write_new(run_dir / "server_stop_receipt.json", stopped)
        return stopped

    try:
        if len(slots) != 14 or len({row["configuration_id"] for row in slots}) != 7:
            raise CandidateError("frozen variant-aware setup matrix must contain exactly seven configs by two cases")
        if work.resolve() != work or work.parent.resolve(strict=True) != Path("/private/tmp").resolve(strict=True):
            raise CandidateError("candidate work path is not canonical private temporary storage")
        shadow_receipt = server.prepare_shadow()
        _append(events_path, {"event": "private_shadow_prepared",
                              "receipt_sha256": _json_hash(shadow_receipt)})
        from comsol_mcp._control_daemon import ControlDaemon
        daemon = ControlDaemon(work / "mcp-home", project_root=server.project)
        try:
            project_creation, project_response = _project_create_before_birth(
                daemon, server.project, run_dir)
        except OutcomeUnknown as exc:
            worker_state = "UNKNOWN"
            status = "UNKNOWN_PREBIRTH_PROJECT_ADMISSION"
            error = {"type": type(exc).__name__, "message": str(exc),
                     "stage": "project.create/project.inspect", "outcome": "UNKNOWN_NO_REPLAY"}
            raise
        project_id = str(project_creation["project_id"])
        workspace = Path(project_creation["workspace"]).resolve(strict=True)
        from tools.run_native_w24_static_shape_setup import prepare_project_sources
        source_manifest = prepare_project_sources(
            workspace,
            fixture_source=repo / "tools/java/W24StaticShapeFixture.java",
            readback_source=repo / "tools/java/W24StaticShapeReadback.java")
        _write_new(run_dir / "project_java_source_manifest.json", source_manifest)
        _append(events_path, {"event": "project_workspace_ready_before_server_birth",
                              "project_id": project_id,
                              "workspace": str(workspace),
                              "project_create_response_sha256": _json_hash(project_response)})

        try:
            inventory = fresh_scoped_inventory()
        except BaseException as exc:
            inventory = {"schema": "W24_SETUP_FRESH_SCOPED_INVENTORY_V1",
                         "status": "INVENTORY_UNAVAILABLE",
                         "error_type": type(exc).__name__, "error": str(exc),
                         "observed_at_utc": datetime.now(timezone.utc).isoformat()}
        _write_new(run_dir / "prebirth_inventory.json", inventory)
        _append(events_path, {"event": "fresh_prebirth_inventory",
                              "status": inventory.get("fresh_quiescent_for_scope", False),
                              "sha256": _sha_file(run_dir / "prebirth_inventory.json"),
                              "observed_at_utc": inventory.get("observed_at_utc")})
        if inventory.get("fresh_quiescent_for_scope") is not True:
            status = "REFUSED_PREBIRTH_INVENTORY_UNAVAILABLE_OR_NONQUIESCENT"
            raise CandidateError("fresh scoped COMSOL/Worker/listener inventory is unavailable or non-quiescent")

        listener = server.start_and_verify_listener()
        monitor = monitor_box.get("monitor")
        if monitor is None or server.birth_monotonic is None or type(server.port) is not int:
            raise CandidateError("server birth/listener/watchdog evidence is incomplete")
        deadline = server.birth_monotonic + MAX_WALL_SECONDS
        _write_new(run_dir / "server_birth_listener.json", {
            "schema": "W24_SETUP_SERVER_BIRTH_LISTENER_V1",
            "status": "Popen_BIRTH_AND_LOOPBACK_LISTENER_VERIFIED",
            "process": {"pid": server.proc.pid,
                        "birth": server.server_snapshot.get("birth"),
                        "start_epoch_ms": server.server_snapshot.get("start_epoch_ms"),
                        "command_sha256": server.server_snapshot.get("command_sha256")},
            "listener": listener, "command": [str(server.shadow_root / "bin/comsol"),
                                                 "mphserver", "-np", "2", "-port", "0",
                                                 "-portfile", str(server.runtime / "server.port"),
                                                 "-prefsdir", str(server.prefs), "-tmpdir", str(server.tmp),
                                                 "-recoverydir", str(server.recovery),
                                                 "-login", "auto", "-silent", "-multi", "on"],
            "max_wall_deadline_monotonic": deadline,
            "max_wall_seconds_including_cleanup": MAX_WALL_SECONDS,
            "resource_budget": SETUP_BUDGET,
        })
        _append(events_path, {"event": "server_birth_listener_confirmed",
                              "pid": server.proc.pid, "port": server.port,
                              "birth": server.server_snapshot.get("birth"),
                              "command_sha256": server.server_snapshot.get("command_sha256")})
        status = "RUNNING_SETUP_ONLY"
        server_java_options = server.require_server_java_options()
        _append(events_path, {"event": "server_jvm_options_confirmed",
                              "receipt_sha256": _json_hash(server_java_options)})
        if monitor.stop_reason:
            status = "STOPPED_RSS_OR_MONITOR_ADMISSION_BEFORE_WORKER"
            raise CandidateError(f"resource watchdog blocked Worker birth: {monitor.stop_reason}")

        from comsol_mcp._runtime_installation import runtime_id_for_root
        connect_timeout = _remaining_admission_rpc(deadline, float(RPC_CAP_SECONDS))
        worker_state = "UNKNOWN_CONNECT_ATTEMPT"
        try:
            connect = _direct_dispatch(
                daemon, run_dir, operation="session.connect",
                arguments={"runtime_id": runtime_id_for_root(install),
                           "endpoint": {"host": "127.0.0.1", "port": int(server.port)}},
                project_id=project_id, request_label="session_connect",
                timeout_seconds=connect_timeout)
        except BaseException as exc:
            status = "UNKNOWN_SESSION_CONNECT"
            error = {"type": type(exc).__name__, "message": str(exc),
                     "stage": "session.connect", "outcome": "UNKNOWN"}
            raise
        if connect.get("success") is not True:
            data = connect.get("data") if isinstance(connect.get("data"), Mapping) else {}
            explicitly_prebirth_failed = (data.get("worker_birth_performed") is False and
                                           data.get("engine_dispatched") is False and
                                           connect.get("execution_state_unknown") is not True)
            if explicitly_prebirth_failed:
                worker_state = "NEVER_CONNECTED"
                status = "SESSION_CONNECT_FAILED_BEFORE_WORKER_BIRTH"
            else:
                status = "UNKNOWN_SESSION_CONNECT"
            raise CandidateError("production session.connect did not establish one managed Worker" +
                                 ("; no Worker birth was explicitly proven" if explicitly_prebirth_failed
                                  else "; preserve the original ambiguous session operation"))
        session = _session_identity(connect, project_id=project_id,
                                    expected_port=int(server.port))
        worker_state = "CONNECTED_UNVERIFIED"
        context, worker, worker_identity, worker_state_dir = _worker_identity_from_context(
            daemon, project_id, session)
        worker_birth_ms = _process_start_epoch_for_identity(worker_identity, worker)
        monitor.bind_worker(int(worker_identity["pid"]), worker_birth_ms)
        _write_new(run_dir / "worker_popen_identity.json", {
            "schema": "W24_SETUP_WORKER_POPEN_IDENTITY_V1",
            "status": "EXACT_CONTROLDAEMON_REGISTERED_WORKER_HANDLE",
            "pid": worker_identity["pid"], "birth": worker_identity["birth"],
            "command_sha256": worker_identity["command_sha256"],
            "start_epoch_ms": worker_birth_ms,
            "worker_state_dir": str(worker_state_dir),
            "project_id": project_id, "session_id": session["session_id"],
            "worker_instance_id": session["worker_instance_id"],
            "worker_epoch": session["worker_epoch"]})
        worker_java_options = _verify_worker_java_options(worker_state_dir)
        _write_new(run_dir / "worker_java_options_readback.json", worker_java_options)
        connected_inspect = _direct_dispatch(
            daemon, run_dir, operation="session.inspect",
            arguments={"session_id": session["session_id"]},
            project_id=project_id, session_id=session["session_id"],
            request_label="session_inspect_connected",
            timeout_seconds=_remaining_admission_rpc(deadline, 30.0))
        _require_connected_inspect(connected_inspect, session)
        _write_new(run_dir / "connected_session_inspect.json", connected_inspect)
        _append(events_path, {"event": "session_connected_identity_verified",
                              "project_id": project_id,
                              "session_id": session["session_id"],
                              "server_instance_id": session["server_instance_id"],
                              "worker_instance_id": session["worker_instance_id"],
                              "worker_epoch": session["worker_epoch"],
                              "worker_pid": worker_identity["pid"]})
        worker_state = "CONNECTED"
        if monitor.stop_reason:
            status = "STOPPED_RSS_OR_MONITOR_ADMISSION_BEFORE_MODEL_CREATE"
            raise CandidateError(f"resource watchdog blocked model_create: {monitor.stop_reason}")

        gate = BudgetedSetupDaemon(daemon, monitor=monitor,
            deadline_monotonic=deadline, events_path=events_path,
            workspace=workspace, project_id=project_id)
        model_request_id, model_idempotency_key = _request_ids("model-create-parent")
        model_create = gate.dispatch({
            "operation": "model_create",
            "arguments": {"name": "w24_static_shape_setup_parent"},
            "execution": {"project_id": project_id,
                          "session_id": session["session_id"],
                          "request_id": model_request_id,
                          "idempotency_key": model_idempotency_key,
                          "rpc_timeout_s": float(RPC_CAP_SECONDS),
                          "queue_timeout_s": 60.0,
                          "execution_timeout_s": float(RPC_CAP_SECONDS)}})
        _write_new(run_dir / "registered_parent_model_create.json", model_create)
        try:
            parent_binding = _model_create_binding(model_create, project_id=project_id,
                                                   session=session)
        except CandidateError:
            gate.unknown = True
            status = "STOPPED_UNKNOWN"
            raise
        from tools.run_native_w24_static_shape_setup import StaticShapeManagedRunner
        from tools import run_native_w24_cure_preflight as preflight_helpers
        setup_adapter = preflight_helpers
        managed_runner = StaticShapeManagedRunner(
            daemon=gate, project_id=project_id, project_workspace=workspace,
            project_create_response=project_response,
            parent_binding=parent_binding, source_manifest=source_manifest,
            setup_runner=setup_adapter, evidence_dir=run_dir / "static_shape_managed",
            timeout_s=float(RPC_CAP_SECONDS))

        for slot in slots:
            if gate.unknown:
                status = "STOPPED_UNKNOWN"
                break
            if monitor.stop_reason:
                status = "STOPPED_RSS_OR_MONITOR_ADMISSION"
                break
            if time.monotonic() >= deadline - CLEANUP_RESERVE_SECONDS:
                status = "STOPPED_WALL_BUDGET_CLEANUP_RESERVE"
                break
            gate.set_slot(slot["configuration_id"], slot["case_id"])
            slot_dir_name = f"{slot['submission_index']:02d}_{slot['configuration_id']}_{slot['case_id']}"
            try:
                result = managed_runner.build_save_reopen(
                    slot["case_id"], slot["configuration_id"])
                if gate.slot_stage != "readback_complete":
                    gate.unknown = True
                    status = "STOPPED_UNKNOWN"
                    raise CandidateError("slot returned without the complete staged build/save/reopen/readback route")
                validation = _validate_slot_receipt(
                    result, slot=slot, project_id=project_id, session=session,
                    workspace=workspace, expected_sources=source_manifest.get("sources", {}))
                if any(tag in model_tags for tag in validation["model_tags"]):
                    raise CandidateError("two preregistered setup slots resolved to a duplicate native model tag")
                model_tags.update(validation["model_tags"])
                total_outputs += validation["artifact"]["size_bytes"]
                if total_outputs > MAX_OUTPUT_BYTES:
                    raise CandidateError("aggregate 14-slot MPH artifacts exceed the 10 GiB candidate cap")
                slot_path = run_dir / "sensitivity_setup_receipts" / f"{slot_dir_name}.json"
                receipt_hash = _write_new(slot_path, result)
                ledger = _terminal_campaign_ledger(daemon, project_id)
                ledger_path = run_dir / "slot_ledgers" / f"{slot_dir_name}.json"
                ledger_hash = _write_new(ledger_path, ledger)
                if ledger.get("safe_to_retire") is not True:
                    gate.unknown = True
                    status = "STOPPED_LEDGER_ACTIVE_OR_UNKNOWN"
                    raise CandidateError("slot ended without a fully terminal OperationStore/Worker ledger")
                size_inventory = _check_process_size(work)
                _append(events_path, {"event": "setup_slot_receipt_persisted",
                                      "submission_index": slot["submission_index"],
                                      "configuration_id": slot["configuration_id"],
                                      "case_id": slot["case_id"],
                                      "receipt": str(slot_path),
                                      "receipt_sha256": receipt_hash,
                                      "ledger_receipt": str(ledger_path),
                                      "ledger_sha256": ledger_hash,
                                      "artifact": validation["artifact"],
                                      "resource_bytes": size_inventory["total_bytes"],
                                      "worker_epoch": session["worker_epoch"],
                                      "binding_validity_after_retirement":
                                          "HISTORICAL_ONLY_REQUIRES_NEW_WORKER_RE_ADMISSION"})
                setup_receipts.append({"submission_index": slot["submission_index"],
                                       "configuration_id": slot["configuration_id"],
                                       "case_id": slot["case_id"],
                                       "receipt_path": str(slot_path),
                                       "receipt_sha256": receipt_hash,
                                       "ledger_path": str(ledger_path),
                                       "ledger_sha256": ledger_hash,
                                       "artifact": validation["artifact"],
                                       "binding_validity_after_retirement":
                                           "HISTORICAL_ONLY_REQUIRES_NEW_WORKER_RE_ADMISSION"})
            finally:
                gate.clear_slot()
            if monitor.stop_reason:
                status = "STOPPED_RSS_OR_MONITOR_ADMISSION"
                break
        if gate.unknown:
            status = "STOPPED_UNKNOWN"
        elif (len(setup_receipts) == len(slots) and
              len(gate.completed_slots) == len(slots)):
            status = "SETUP_14_OF_14_COMPLETE_SCIENCE_NOT_RUN"
        elif status == "RUNNING_SETUP_ONLY":
            status = "STOPPED_BEFORE_14_OF_14"
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc),
                 "traceback": traceback.format_exc(limit=20)}
        if status == "RUNNING_SETUP_ONLY":
            status = "FAILED_SETUP_ONLY"

    monitor = monitor or monitor_box.get("monitor")
    is_unknown = (worker_state in {"UNKNOWN_CONNECT_ATTEMPT", "CONNECTED_UNVERIFIED", "UNKNOWN"} or
                  (gate is not None and gate.unknown) or
                  status in {"UNKNOWN_SESSION_CONNECT", "STOPPED_UNKNOWN", "STOPPED_LEDGER_ACTIVE_OR_UNKNOWN"})
    if is_unknown:
        try:
            _unknown_hold(daemon=daemon, server=server,
                          monitor=monitor,
                          run_dir=run_dir, work=work, project_id=project_id,
                          session=session, candidate_sha256=reviewed_sha256,
                          reason=(error or {}).get("message", status))
        except BaseException as exc:
            error = {**(error or {}), "unknown_hold_error":
                     f"{type(exc).__name__}: {exc}"}
            status = "UNKNOWN_PRESERVED_CLEANUP_NOT_AUTHORIZED_OR_UNPROVEN"
            # Preserve all in-process handles; an UNKNOWN result must not fall
            # through to daemon.close() or server termination.
            _hold_forever_with_handles(monitor)
    elif daemon is not None and worker_state == "CONNECTED" and session is not None:
        try:
            if monitor is None:
                raise CandidateError("connected Worker has no live resource monitor")
            cleanup = _retire_worker_and_server(
                daemon=daemon, server=server, monitor=monitor,
                project_id=str(project_id), session=session, run_dir=run_dir,
                deadline_monotonic=(server.birth_monotonic or time.monotonic()) + MAX_WALL_SECONDS)
        except BaseException as exc:
            error = {**(error or {}), "cleanup_error": f"{type(exc).__name__}: {exc}"}
            status = "CONNECTED_STATE_PRESERVED_CLEANUP_BLOCKED"
            _unknown_hold(daemon=daemon, server=server, monitor=monitor,
                          run_dir=run_dir, work=work, project_id=str(project_id),
                          session=session, candidate_sha256=reviewed_sha256,
                          reason=error["cleanup_error"])
    else:
        if daemon is not None:
            try:
                daemon.close()
                cleanup = {"control_daemon_close": "RETURNED"}
            except BaseException as exc:
                error = {**(error or {}), "control_daemon_close_error":
                         f"{type(exc).__name__}: {exc}"}
                status = "PRECONNECT_DAEMON_CLOSE_BLOCKED"
        if server.proc is not None and server.proc.poll() is None:
            try:
                cleanup = {**(cleanup or {}), "server": stop_server_known()}
            except BaseException as exc:
                error = {**(error or {}), "server_stop_error": f"{type(exc).__name__}: {exc}"}
                status = "SERVER_HANDLE_PRESERVED_CLEANUP_BLOCKED"
                if daemon is not None and project_id is not None:
                    _hold_forever_with_handles(monitor)

    receipt = {**receipt_base,
               "status": status,
               "completed_setup_slots": setup_receipts,
               "completed_setup_slot_count": len(setup_receipts),
               "unique_native_model_tag_count": len(model_tags),
               "project_id": project_id,
               "project_workspace": project_creation.get("workspace") if isinstance(project_creation, Mapping) else None,
               "session_identity": dict(session) if isinstance(session, Mapping) else None,
               "worker_state": worker_state,
               "server_process": ({"pid": server.proc.pid,
                                   "birth": server.server_snapshot.get("birth") if server.server_snapshot else None,
                                   "command_sha256": server.server_snapshot.get("command_sha256") if server.server_snapshot else None,
                                   "port": server.port,
                                   "returncode": server.proc.poll()} if server.proc is not None else None),
               "resource_limits": SETUP_BUDGET,
               "resource_summary": _check_process_size(work) if work.exists() else None,
               "rss_stop_reason": monitor.stop_reason if monitor is not None else None,
               "rss_last_sample": monitor.last_sample if monitor is not None else None,
               "cleanup": cleanup,
               "error": error,
               "native_science_status": "NOT_RUN",
               "native_result_status": "NOT_RUN"}
    _write_new(run_dir / "campaign_receipt.json", receipt)
    _append(events_path, {"event": "campaign_receipt_persisted",
                          "at_utc": datetime.now(timezone.utc).isoformat(),
                          "receipt": str(run_dir / "campaign_receipt.json"),
                          "status": status,
                          "receipt_sha256": _sha_file(run_dir / "campaign_receipt.json")})
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("export-archive", help="read-only archive of the published base plus this W24 overlay")
    archive.add_argument("--destination", required=True, type=Path)
    prepare = sub.add_parser("prepare", help="offline source audit and Java compile; starts no process")
    prepare.add_argument("--evidence", required=True, type=Path)
    prepare.add_argument("--comsol-root", type=Path, default=INSTALL_ROOT)
    prepare.add_argument("--jdk11", type=Path, default=JAVA11)
    execute = sub.add_parser("execute", help="run only the separately reviewed setup-only candidate")
    execute.add_argument("--evidence", required=True, type=Path)
    execute.add_argument("--work", required=True, type=Path)
    execute.add_argument("--reviewed-candidate-sha256", required=True)
    execute.add_argument("--comsol-root", type=Path, default=INSTALL_ROOT)
    execute.add_argument("--jdk11", type=Path, default=JAVA11)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export-archive":
            result = export_published_archive(repo=REPO, destination=args.destination)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "prepare":
            result = prepare_candidate(repo=REPO, evidence=args.evidence,
                install=args.comsol_root, jdk=args.jdk11)
            print(json.dumps({"status": result["status"],
                              "candidate_sha256": result["candidate_sha256"],
                              "evidence_dir": result["candidate_evidence_dir"],
                              "source_closure_sha256": result["source"]["source_closure_sha256"],
                              "planned_setup_slots": len(result["ordered_setup_slots"]),
                              "study_run_submissions": result["budget"]["study_run_submissions"],
                              "solver_calls": result["budget"]["solver_calls"]},
                             indent=2, ensure_ascii=False))
            return 0
        result = execute_candidate(repo=REPO, evidence=args.evidence,
            work=args.work, reviewed_sha256=args.reviewed_candidate_sha256,
            install=args.comsol_root, jdk=args.jdk11)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get("status") == "SETUP_14_OF_14_COMPLETE_SCIENCE_NOT_RUN" else 1
    except Exception as exc:
        print(json.dumps({"status": "CANDIDATE_GATE_FAILED",
                          "error_type": type(exc).__name__, "error": str(exc)},
                         indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
