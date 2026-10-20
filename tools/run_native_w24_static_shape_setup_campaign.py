#!/usr/bin/env python3
"""W24 fourteen-slot setup-only candidate; it never submits Study.run."""
from __future__ import annotations

import argparse
from tools import native_runtime_binding as fresh_runtime
import hashlib
import importlib
import json
import math
import os
import re
import shlex
import shutil
import stat
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
EXPECTED_PYTHON = Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
EXPLICIT_SITE_PACKAGES = (EXPECTED_PYTHON.parent.parent / "lib" /
                          f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
JAVA_TOOL_OPTIONS = "-XX:ActiveProcessorCount=2 -Xms128m -Xmx1024m"
JCMD_OPTION_ENV_KEYS = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")
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
LEGACY_STORE_TERMINAL_PROOF_OPERATIONS = frozenset({
    "model_create", "model.adopt", "model_load", "model.load", "model.inspect",
})
OVERLAY_PATHS = {
    "tools/run_native_w24_static_shape_setup_campaign.py",
    "tools/run_native_w24_static_shape_setup.py",
    "tests/test_run_native_w24_static_shape_setup_campaign.py",
    "tests/test_w24_static_shape_managed.py",
    "tests/test_w24_operation_store_terminal_proof.py",
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
    "project.create and project.inspect before server birth with host_control scoped to this new private project",
    "one production ControlDaemon.dispatch(session.start) through the fixed W24 OwnedServerLauncher adapter",
    "one production ControlDaemon.dispatch(session.connect) using the exact returned session_id and endpoint",
    "session.inspect; one model_create seed",
    "14 config-major build/save/readback setup slots; register each exact saved MPH through artifact.register, then call managed model.load(artifact_id), require one fresh MCP-owned tag, and compare full post-load readback; every fixture build carries one unique selection_diagnostic_path in the current private static_shape_managed directory, exact configuration/case UUID basename, absent before dispatch",
    "per setup slot: one artifact.register and one managed model.load; this adds one public operation per slot versus legacy model_load while preserving the 14-slot and 0-solve budget",
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
    "planned_public_slot_route_calls": 126,
    "public_slot_route_count_delta_vs_legacy_path_load": 14,
    "configuration_order": CONFIGURATION_ORDER,
    "case_order": ["flat", "step"],
    "max_task_owned_comsol_servers": 1,
    "max_session_start_calls": 1,
    "max_session_connect_calls": 1,
    "max_session_stop_calls": 1,
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
    "project_output_byte_accounting": (
        "count every regular file in outputs/ and g2_artifacts/registered/; each completed slot "
        "accounts for both its saved MPH and the byte-identical registered MPH copy"
    ),
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

SCHEMA_ONLY_ROUTES = [
    "project.create and project.inspect before server birth with exact private host_control grant",
    "one production ControlDaemon.dispatch(session.start) through the fixed W24 OwnedServerLauncher adapter",
    "one production ControlDaemon.dispatch(session.connect) using the exact returned session_id and endpoint",
    "session.inspect; exactly one model_create seed; exactly one trusted W24StaticShapeFixture property_schema_only call on that ModelRef",
    "capture declared type, finite allowed values, and non-mutating scalar/expression values for exactly eight named properties",
    "no Study.run, solver, geometry build, MPH save/reopen, or result evaluation",
    "complete OperationStore/job/Worker-request reconciliation",
    "session.disconnect(retire_worker=true) only after terminal safe ledger",
    "session.inspect retirement readback; exact task Popen server stop",
]
SCHEMA_ONLY_BUDGET = {
    **SETUP_BUDGET,
    "execution_mode": "property_schema_only",
    "planned_setup_receipts": 0,
    "configuration_order": [],
    "case_order": [],
    "max_solver_threads": 0,
    "wall_seconds_from_server_birth_including_cleanup": 1200,
    "max_single_unsolved_mph_bytes": 0,
    "max_total_project_output_bytes": 0,
    "model_create_calls": 1,
    "trusted_java_calls": 1,
    "schema_probe_calls": 1,
    "schema_probe_properties": 8,
    "geometry_configuration_calls": 2,
    "geometry_build_calls": 0,
    "physics_property_set_calls": 0,
    "study_run_submissions": 0,
    "solver_calls": 0,
    "max_gui_processes": 0,
    "future_science_binding_policy": (
        "diagnostic-only model is retired on the same Worker; no MPH or cross-epoch science binding is produced"
    ),
}
EXECUTION_MODE_SETUP14 = "setup14"
EXECUTION_MODE_SCHEMA_ONLY = "property_schema_only"

class CandidateError(RuntimeError):
    pass


class OutcomeUnknown(CandidateError):
    """A persisted operation intent lacks a confirmed terminal response."""

    def __init__(self, message: str, *, response: Mapping[str, Any] | None = None,
                 request_id: str | None = None, idempotency_key: str | None = None):
        super().__init__(message)
        self.response = dict(response) if isinstance(response, Mapping) else None
        self.request_id = request_id
        self.idempotency_key = idempotency_key


class JvmLimitsReadbackHold(CandidateError):
    """The live child could not be proved to have the frozen effective JVM limits."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False, default=str) + "\n").encode()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _model_load_expected_ownership(load_mode: Any) -> str:
    """Map the public legacy loader's two explicit outcomes to their owner class."""
    if load_mode == "adopted-by-path":
        return "user_owned"
    if load_mode == "loaded":
        return "mcp_owned"
    raise CandidateError("model_load returned an unsupported or missing load_mode")


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


def _fresh_source_inventory(repo: Path, binding: Mapping[str, Any]) -> dict[str, Any]:
    current = fresh_runtime.source_inventory(repo)
    if current != binding["source"]:
        raise CandidateError("fresh source closure differs from host authority")
    return {"checkout_kind": "fresh_host_source_closure", "source_files": current["files"],
            "source_closure_sha256": current["sha256"], "runtime_binding_sha256": binding["binding_sha256"]}


CAMPAIGN_DEPENDENCIES = {
    "tools.w24_native_epoch_capability": ("_capture_live_worker_binding", "_verify_live_worker_binding"),
    "tools.run_w21_stage_native": ("_resolve_remote_comsol_identity",),
    "comsol_mcp._control_daemon": ("ControlDaemon",),
    "comsol_mcp._g2_isolation": ("_process_snapshot",),
    "comsol_mcp._platform_process": ("process_identity",),
    "comsol_mcp._session_context": ("runtime_state_root", "session_state_directory"),
    "comsol_mcp._session_server": ("ManagedServerHandle", "OwnedServerError", "OwnedServerLauncher",
                                     "_server_directories", "build_server_command"),
    "tools.run_native_resume_smoke": ("_is_native_study_run_submission",),
    "tools.run_native_w24_cure_preflight": (
        "_actual_study_run_submissions", "_project_job_inventory",
        "_worker_request_activity_inventory", "_worker_request_terminal"),
    "tools.run_native_w24_cure_science": ("ManagedModelBinding",),
    "tools.run_native_w24_static_shape_setup": (
        "CampaignError", "StaticShapeManagedRunner", "prepare_project_sources"),
    "tools.w24_static_shape_sensitivity": (
        "SENSITIVITY_CASE_ORDER", "sensitivity_configurations"),
}


def preflight_campaign_dependencies(repo: Path, *, binding: Mapping[str, Any] | None = None, importer: Callable[[str], Any] | None = None
                                    ) -> dict[str, Any]:
    """Resolve every late-bound setup/cleanup module before any native birth.

    The receipt binds each imported module to the candidate's frozen source
    closure. This keeps an absent helper from surfacing only after a Worker
    has been created and makes the dependency check repeatable at execution.
    """
    source = _fresh_source_inventory(repo, binding) if binding else _source_inventory(repo)
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


def _execution_contract(mode: str) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    if mode == EXECUTION_MODE_SETUP14:
        return SETUP_BUDGET, ROUTES, build_setup_slots()
    if mode == EXECUTION_MODE_SCHEMA_ONLY:
        return SCHEMA_ONLY_BUDGET, SCHEMA_ONLY_ROUTES, []
    raise CandidateError(f"unsupported frozen W24 execution mode: {mode!r}")


def _validate_schema_probe_result(value: Any, *, expected_model_tag: str) -> dict[str, Any]:
    expected = {
        "LaminarFlow.PhysicalModelProperty.IncludeGravity": "IncludeGravity",
        "LaminarFlow.PhysicalModelProperty.Compressibility": "Compressibility",
        "PhaseField.PhaseFieldModel.chiOption": "chiOption",
        "PhaseField.PhaseFieldModel.chi": "chi",
        "PhaseField.PhaseFieldModel.epsilon_pf": "epsilon_pf",
        "TwoPhaseFlowPhaseField.IncludeSurfaceTension": "IncludeSurfaceTension",
        "TwoPhaseFlowPhaseField.SurfaceTensionCoefficient": "SurfaceTensionCoefficient",
        "TwoPhaseFlowPhaseField.sigma": "sigma",
    }
    if (not isinstance(value, Mapping) or
            value.get("status") != "SCHEMA_ONLY_DEFAULTS_CAPTURED_NOT_SOLVED" or
            value.get("action") != "property_schema_only" or
            value.get("model_tag") != expected_model_tag or
            value.get("physics_tags") != ["spf", "pf"] or
            value.get("physics_types") != ["LaminarFlow", "PhaseField"] or
            value.get("coupling_tag") != "tpf1" or
            value.get("coupling_type") != "TwoPhaseFlowPhaseField" or
            value.get("study_tags") != [] or value.get("solver_tags") != [] or
            value.get("physics_property_set_calls") != 0 or value.get("study_run_calls") != 0 or
            value.get("solver_calls") != 0 or value.get("model_save_calls") != 0 or
            value.get("result_evaluation_calls") != 0 or
            value.get("native_acceptance") != "NOT_RUN"):
        raise CandidateError("schema-only Java result differs from the exact unsolved property-probe contract")
    geometry = value.get("geometry_setup")
    if (not isinstance(geometry, Mapping) or geometry.get("dimension") != 2 or
            geometry.get("length_unit") != "m" or geometry.get("length_unit_setter_calls") != 1 or
            geometry.get("axisymmetric") is not True or geometry.get("axisymmetric_setter_calls") != 1 or
            geometry.get("geometry_build_calls") != 0):
        raise CandidateError("schema-only geometry setup differs from the exact partial-fixture scope")
    properties = value.get("properties")
    if not isinstance(properties, Mapping) or set(properties) != set(expected):
        raise CandidateError("schema-only Java result omitted or added a requested property")
    for key, property_name in expected.items():
        row = properties.get(key)
        if (not isinstance(row, Mapping) or row.get("property_name") != property_name or
                type(row.get("has_property")) is not bool):
            raise CandidateError(f"schema-only result has malformed presence metadata for {property_name}")
        value_type = row.get("declared_type")
        if row["has_property"]:
            if not isinstance(value_type, str) or not value_type:
                raise CandidateError(f"schema-only result omitted declared type for {property_name}")
            allowed = row.get("allowed_values")
            if allowed is not None and (not isinstance(allowed, list) or
                    not all(isinstance(item, str) for item in allowed) or len(allowed) != len(set(allowed))):
                raise CandidateError(f"schema-only result has malformed finite values for {property_name}")
            state = row.get("value_status")
            if state not in {"READ_WITH_DECLARED_TYPE_GETTER", "UNSUPPORTED_DECLARED_TYPE"}:
                raise CandidateError(f"schema-only result has malformed value status for {property_name}")
            if state == "UNSUPPORTED_DECLARED_TYPE":
                if (row.get("value_representation") != "unsupported_declared_type" or
                        row.get("value_reader") is not None or row.get("value") is not None or
                        row.get("expression_value") is not None):
                    raise CandidateError(f"unsupported property {property_name} must remain uninterpreted")
            elif value_type == "Double":
                if (row.get("value_reader") != "getString" or
                        row.get("value_representation") != "expression_as_string" or
                        (row.get("value") is not None and not isinstance(row.get("value"), str)) or
                        row.get("expression_value") != row.get("value")):
                    raise CandidateError(f"Double property {property_name} was not preserved as raw expression text")
            elif value_type == "Boolean":
                if (row.get("value_reader") != "getBoolean" or type(row.get("value")) is not bool or
                        row.get("value_representation") != "typed_scalar" or
                        row.get("expression_value") is not None):
                    raise CandidateError(f"Boolean property {property_name} was not read by its Boolean getter")
            elif value_type == "String":
                if (row.get("value_reader") != "getString" or
                        (row.get("value") is not None and not isinstance(row.get("value"), str)) or
                        row.get("value_representation") != "typed_scalar" or
                        row.get("expression_value") is not None):
                    raise CandidateError(f"String property {property_name} was not read by its String getter")
            else:
                raise CandidateError(f"schema-only result attempted an unapproved getter for {property_name}")
        elif (row.get("value_status") != "PROPERTY_ABSENT" or value_type is not None or
              row.get("allowed_values") is not None or row.get("value_reader") is not None or
              row.get("value") is not None or row.get("value_representation") is not None or
              row.get("expression_value") is not None):
            raise CandidateError(f"absent property {property_name} has non-absent schema/value data")
    return {"status": "SCHEMA_ONLY_RESULT_VALIDATED_NOT_SOLVED",
            "property_count": len(expected), "model_tag": expected_model_tag,
            "study_run_submissions": 0, "solver_calls": 0,
            "physics_property_set_calls": 0,
            "geometry_length_unit_setter_calls": 1,
            "geometry_axisymmetric_setter_calls": 1,
            "geometry_build_calls": 0, "model_saves": 0,
            "native_acceptance": "NOT_RUN"}


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
                      install: Path = INSTALL_ROOT, jdk: Path = JAVA11,
                      runtime_authority: Path | None = None,
                      schema_probe_only: bool = False) -> dict[str, Any]:
    if type(schema_probe_only) is not bool:
        raise CandidateError("schema_probe_only must be an exact Boolean")
    mode = EXECUTION_MODE_SCHEMA_ONLY if schema_probe_only else EXECUTION_MODE_SETUP14
    budget, routes, slots = _execution_contract(mode)
    binding = (fresh_runtime.load(runtime_authority, repo=repo, budget=budget, install=install, jdk=jdk)
               if runtime_authority is not None else None)
    if binding is None and (Path(sys.executable).resolve(strict=True) != EXPECTED_PYTHON.resolve(strict=True) or
            sys.version_info[:2] != (3, 12)):
        raise CandidateError(f"offline candidate preparation requires exact Python 3.12 at {EXPECTED_PYTHON}")
    python_isolation = configure_archive_python(repo, binding=binding) if binding else configure_archive_python(repo)
    if binding:
        fresh_runtime.scoped_path(binding, evidence, absent=True)
    if evidence.exists() or evidence.is_symlink() or (binding is None and not evidence.resolve().is_relative_to(EVIDENCE_ROOT.resolve())):
        raise CandidateError("offline candidate evidence path must be new below W24 evidence")
    dependency_preflight = preflight_campaign_dependencies(repo, binding=binding) if binding else preflight_campaign_dependencies(repo)
    source_before = _fresh_source_inventory(repo, binding) if binding else _source_inventory(repo)
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Runtime evidence is written beneath this directory during execute. Keep
    # the parent owner-private and writable; freeze individual prepared inputs
    # below instead of chmodding the whole evidence tree read-only.
    os.chmod(evidence, 0o700)
    compiled = _compile_sources(repo, evidence / "offline_compile/classes", install, jdk)
    source_after = _fresh_source_inventory(repo, binding) if binding else _source_inventory(repo)
    if source_before != source_after or compiled["status"] != "COMPILE_PASS":
        raise CandidateError("source changed or offline Java compilation failed")
    freeze_body = {
        "schema": FREEZE_SCHEMA, "status": "PREPARED_SETUP_ONLY_NOT_NATIVE",
        "execution_mode": mode,
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
        "budget": budget, "routes": routes,
        "ordered_setup_slots": slots,
        "scientific_status": "NOT_RUN",
        "cross_epoch_binding_warning": budget["future_science_binding_policy"],
        "candidate_evidence_dir": str(evidence.resolve()),
    }
    if binding:
        freeze_body["fresh_runtime_binding"] = binding
    freeze = {**freeze_body, "candidate_sha256": _json_hash(freeze_body)}
    _write_new(evidence / FREEZE_FILE, freeze)
    _write_new(evidence / "offline_compile.json", compiled)
    _seal_prepared_candidate_inputs(evidence)
    return freeze


def _seal_prepared_candidate_inputs(evidence: Path) -> None:
    """Freeze prepared files while preserving the private runtime-output parent.

    The execute path must create ``native-setup-run`` under ``evidence`` after
    reviewing the inputs. Files and directory identity are sealed separately:
    regular inputs become read-only (0400), while every owned directory remains
    private and searchable (0700). This preserves the production evidence parent
    needed by the existing append-only run receipt path.
    """
    root = Path(evidence)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise CandidateError("prepared evidence root is unavailable") from exc
    if (stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.getuid()):
        raise CandidateError("prepared evidence root must be a nonsymlink directory owned by this user")
    identity = (root_stat.st_dev, root_stat.st_ino, root_stat.st_uid)
    try:
        paths = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for path in paths:
            item_stat = path.lstat()
            if item_stat.st_uid != os.getuid() or stat.S_ISLNK(item_stat.st_mode):
                raise CandidateError("prepared evidence contains an aliased or foreign-owned path")
            if stat.S_ISREG(item_stat.st_mode):
                path.chmod(0o400, follow_symlinks=False)
            elif stat.S_ISDIR(item_stat.st_mode):
                path.chmod(0o700, follow_symlinks=False)
            else:
                raise CandidateError("prepared evidence contains a non-file/non-directory path")
        root.chmod(0o700, follow_symlinks=False)
    except OSError as exc:
        raise CandidateError("prepared evidence permissions could not be secured") from exc
    final_root = root.lstat()
    if (final_root.st_dev, final_root.st_ino, final_root.st_uid) != identity or stat.S_IMODE(final_root.st_mode) != 0o700:
        raise CandidateError("prepared evidence root identity or private writable mode changed")
    for path in root.rglob("*"):
        item_stat = path.lstat()
        if stat.S_ISREG(item_stat.st_mode):
            if item_stat.st_mode & 0o222:
                raise CandidateError("prepared evidence input remains writable")
        elif stat.S_ISDIR(item_stat.st_mode):
            if stat.S_IMODE(item_stat.st_mode) != 0o700:
                raise CandidateError("prepared evidence directory lost its private traversal mode")
        else:
            raise CandidateError("prepared evidence changed during permission verification")


def verify_candidate(*, repo: Path, evidence: Path,
                     reviewed_sha256: str,
                     schema_probe_only: bool | None = None) -> dict[str, Any]:
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
    mode = freeze.get("execution_mode", EXECUTION_MODE_SETUP14)
    if schema_probe_only is not None and type(schema_probe_only) is not bool:
        raise CandidateError("schema_probe_only review mode must be an exact Boolean")
    requested_mode = mode
    if schema_probe_only is not None:
        requested_mode = EXECUTION_MODE_SCHEMA_ONLY if schema_probe_only else EXECUTION_MODE_SETUP14
    if requested_mode != mode:
        raise CandidateError("requested execution mode differs from the reviewed frozen mode")
    budget, routes, slots = _execution_contract(mode)
    binding = freeze.get("fresh_runtime_binding")
    if binding is not None:
        fresh_runtime.validate(binding, repo=repo, budget=budget)
        fresh_runtime.scoped_path(binding, evidence)
    source = _fresh_source_inventory(repo, binding) if binding is not None else _source_inventory(repo)
    if (source != freeze.get("source") or
            freeze.get("budget") != budget or freeze.get("routes") != routes or
            freeze.get("ordered_setup_slots") != slots):
        raise CandidateError("source closure, route allowlist, execution mode, slots, or budget changed after review")
    current_dependencies = preflight_campaign_dependencies(repo, binding=binding) if binding else preflight_campaign_dependencies(repo)
    if current_dependencies != freeze.get("dependency_preflight"):
        raise CandidateError("pre-birth dependency closure differs from the reviewed candidate")
    return freeze


def _same_controller_argv(observed: Any, expected: Any, *,
                          allowed_argv0: list[str] | None = None) -> bool:
    if (not isinstance(observed, list) or not isinstance(expected, list)
            or len(observed) != len(expected) or len(expected) <= 4
            or not all(isinstance(item, str) for item in [*observed, *expected])
            or observed[1:] != expected[1:]):
        return False
    if allowed_argv0 is None:
        # Preserve the V1 frontend comparison behavior for non-W24 callers.
        return Path(observed[0]).resolve(strict=True) == Path(expected[0]).resolve(strict=True)
    return (all(isinstance(path, str) and Path(path).is_absolute() for path in allowed_argv0)
            and expected[0] in allowed_argv0 and observed[0] in allowed_argv0)


def _controller_inventory_context(repo: Path, evidence: Path, work: Path,
                                  binding: Mapping[str, Any], reviewed_sha256: str,
                                  *, budget: Mapping[str, Any],
                                  schema_probe_only: bool) -> dict[str, Any]:
    """Called only after the original fresh candidate/runtime/environment gates."""
    from comsol_mcp._platform_process import process_identity
    if type(schema_probe_only) is not bool:
        raise CandidateError("W24 controller inventory requires the exact reviewed execution-mode budget")
    execution_mode = (EXECUTION_MODE_SCHEMA_ONLY if schema_probe_only
                      else EXECUTION_MODE_SETUP14)
    expected_budget, _, _ = _execution_contract(execution_mode)
    if not isinstance(budget, Mapping) or dict(budget) != expected_budget:
        raise CandidateError("W24 controller inventory budget differs from its exact execution mode")
    if binding.get("schema") != fresh_runtime.SCHEMA_V2:
        raise CandidateError("W24 native controller exclusion requires the V2 runtime argv0 binding")
    runtime_argv0 = binding.get("runtime_argv0")
    if (not isinstance(runtime_argv0, Mapping)
            or set(runtime_argv0) != {"source", "path", "sha256"}
            or runtime_argv0.get("source") != "sys.orig_argv[0]"
            or not isinstance(runtime_argv0.get("path"), str)
            or not isinstance(runtime_argv0.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", runtime_argv0["sha256"])):
        raise CandidateError("W24 V2 runtime argv0 identity is malformed")
    launcher = Path(runtime_argv0["path"])
    if (not launcher.is_absolute() or launcher.resolve(strict=True) != launcher
            or hashlib.sha256(launcher.read_bytes()).hexdigest() != runtime_argv0["sha256"]):
        raise CandidateError("W24 V2 runtime argv0 path/hash differs from its immutable binding")
    frontend_entry = binding.get("python", {}).get("entry")
    if not isinstance(frontend_entry, str) or not Path(frontend_entry).is_absolute():
        raise CandidateError("W24 V2 frontend entry is not an absolute bound path")
    execute_plan = _build_exact_execute_plan_from_binding(
        repo=repo, evidence=evidence, work=work, reviewed_sha256=reviewed_sha256,
        binding=binding, schema_probe_only=schema_probe_only)
    literal = execute_plan.get("argv")
    if not isinstance(literal, list) or not literal or not all(isinstance(item, str) for item in literal):
        raise CandidateError("source-bound W24 execute plan has no exact controller argv")
    identity = process_identity(os.getpid())
    if not isinstance(identity, Mapping) or identity.get('alive') is not True or type(identity.get('start_epoch_ms')) is not int or identity['start_epoch_ms'] <= 0:
        raise CandidateError('current original controller birth is unavailable')
    return {'literal_argv': literal, 'start_epoch_ms': identity['start_epoch_ms'],
            'binding_schema': binding['schema'], 'frontend_entry': frontend_entry,
            'runtime_argv0': dict(runtime_argv0)}


def fresh_scoped_inventory(*, controller_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Observe process/listener scope without emitting argv or full user paths."""
    from comsol_mcp._platform_process import process_identity
    ps_cmd = ["/bin/ps", "-axo", "pid=,ppid=,uid=,lstart=,command=" if controller_context is not None else "pid=,ppid=,lstart=,command="]
    ps = subprocess.run(ps_cmd, capture_output=True, text=True,
                        check=False, timeout=15)
    matched: dict[int, dict[str, Any]] = {}
    install = str(INSTALL_ROOT).lower()
    for line in ps.stdout.splitlines():
        command_index = 8 if controller_context is not None else 7
        fields = line.strip().split(None, command_index)
        if len(fields) < command_index + 1 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, ppid, command = int(fields[0]), int(fields[1]), fields[command_index]
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
        if controller_context is not None and pid == os.getpid():
            row['observed_uid'] = int(fields[2]) if fields[2].isdigit() else None
            row['command_sha256'] = _sha_bytes(command.encode())
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
        if controller_context is not None and pid == os.getpid():
            row['_controller_command'] = command
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
    excluded_controller = None
    controller_okay = controller_context is None
    if controller_context is not None:
        row = matched.get(os.getpid())
        excluded_controller = {'status': 'CURRENT_CONTROLLER_NOT_EXCLUDED', 'pid': os.getpid()}
        try:
            import jpype
            literal = controller_context['literal_argv']
            context_schema = controller_context.get('binding_schema')
            if context_schema == fresh_runtime.SCHEMA_V2:
                expected_context = {'literal_argv', 'start_epoch_ms', 'binding_schema',
                                    'frontend_entry', 'runtime_argv0'}
                runtime_argv0 = controller_context['runtime_argv0']
                frontend_entry = controller_context['frontend_entry']
                runtime_path = runtime_argv0.get('path') if isinstance(runtime_argv0, Mapping) else None
                runtime_sha = runtime_argv0.get('sha256') if isinstance(runtime_argv0, Mapping) else None
                runtime_path_obj = Path(runtime_path) if isinstance(runtime_path, str) else None
                allowed_argv0 = [frontend_entry, runtime_path]
                launcher_identity_okay = (set(runtime_argv0) == {'source', 'path', 'sha256'}
                    and runtime_argv0.get('source') == 'sys.orig_argv[0]'
                    and isinstance(runtime_path, str) and Path(runtime_path).is_absolute()
                    and runtime_path_obj.resolve(strict=True) == runtime_path_obj
                    and isinstance(runtime_sha, str) and re.fullmatch(r'[0-9a-f]{64}', runtime_sha)
                    and hashlib.sha256(runtime_path_obj.read_bytes()).hexdigest() == runtime_sha
                    and isinstance(sys.orig_argv, list) and bool(sys.orig_argv)
                    and Path(sys.orig_argv[0]).resolve(strict=True) == runtime_path_obj)
            else:
                expected_context = {'literal_argv', 'start_epoch_ms'}
                allowed_argv0 = None
                launcher_identity_okay = context_schema is None
            controller_okay = (set(controller_context) == expected_context
                and launcher_identity_okay
                and (context_schema is None or context_schema == fresh_runtime.SCHEMA_V2)
                and (context_schema is None or frontend_entry == literal[0])
                and literal[1:5] == ['-S', '-m', 'tools.run_native_w24_static_shape_setup_campaign', 'execute']
                and sys.flags.no_site and Path(sys.argv[0]).resolve(strict=True) == Path(__file__).resolve(strict=True)
                and _same_controller_argv(list(sys.orig_argv), literal, allowed_argv0=allowed_argv0) and not jpype.isJVMStarted()
                and row is not None and row['identity_status'] == 'VERIFIED_ALIVE'
                and row.get('observed_uid') == os.getuid()
                and row.get('start_epoch_ms') == controller_context['start_epoch_ms']
                and _same_controller_argv(shlex.split(row['_controller_command']), literal,
                                          allowed_argv0=allowed_argv0)
                and not any(listener['pid'] == os.getpid() for listener in listeners))
        except (KeyError, TypeError, ValueError, OSError):
            controller_okay = False
        if controller_okay:
            matched.pop(os.getpid())
            excluded_controller = {k: v for k, v in row.items() if k != '_controller_command'}
            excluded_controller['status'] = 'EXACT_CURRENT_PYTHON_CONTROLLER_EXCLUDED_NO_JVM_NO_LISTENER'
        for row in matched.values():
            row.pop('_controller_command', None)
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
        "fresh_quiescent_for_scope": okay and controller_okay and identity_complete and not processes and not listeners,
        **({'excluded_controller': excluded_controller} if controller_context is not None else {}),
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


def configure_archive_python(repo: Path, *, binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind imports to the exact source archive without running .pth files."""
    if not getattr(sys.flags, "no_site", False) or "site" in sys.modules:
        raise CandidateError("isolated candidate Python must start with -S; site/.pth processing is forbidden")
    root = repo.resolve(strict=True)
    packages = (Path(binding["python"]["site_packages"]) if binding else EXPLICIT_SITE_PACKAGES).resolve(strict=True)
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


_EXPECTED_EFFECTIVE_JVM_FLAGS = {
    "ActiveProcessorCount": 2,
    "InitialHeapSize": 134217728,
    "MaxHeapSize": 1073741824,
}


def _jcmd_observer_environment(
        parent_environment: Mapping[str, str] | None = None
        ) -> tuple[dict[str, str], dict[str, Any]]:
    """Copy the caller environment for jcmd only, removing JVM option banners."""
    source = dict(os.environ if parent_environment is None else parent_environment)
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in source.items()):
        raise CandidateError("jcmd observer environment must contain only string keys and values")
    stripped = set(JCMD_OPTION_ENV_KEYS)
    observer = {key: value for key, value in source.items() if key not in stripped}
    if set(observer) != (set(source) - stripped) or any(key in observer for key in JCMD_OPTION_ENV_KEYS):
        raise CandidateError("jcmd observer environment did not remove only pinned JVM option variables")
    preserved_keys = (
        "TMPDIR", "TMP", "TEMP", "HOME", "PATH", "JAVA_HOME", "COMSOL_JAVA_HOME",
        "COMSOL_ROOT", "COMSOL_PREFS_DIR", "COMSOL_PROJECT_ROOT",
        "COMSOL_SERVER_MCP_HOME", "COMSOL_MCP_TRUSTED_CODE",
        "COMSOL_MCP_ISOLATION_RECEIPT", "COMSOL_SERVER_VERSION", "PYTHONPATH",
    )
    receipt = {
        "schema": "W24_JCMD_OBSERVER_ENV_V1",
        "status": "ONLY_JCMD_OPTION_INJECTION_VARIABLES_REMOVED",
        "scope": "exact pinned-jcmd observer subprocess only; COMSOL Server and Worker launch environments are unchanged",
        "removed_variable_names": list(JCMD_OPTION_ENV_KEYS),
        "present_in_parent": [key for key in JCMD_OPTION_ENV_KEYS if key in source],
        "absent_in_observer": [key for key in JCMD_OPTION_ENV_KEYS if key not in observer],
        "preserved_required_runtime_variable_names": [key for key in preserved_keys if key in source],
        "preserved_required_runtime_values_unchanged": all(
            observer.get(key) == source.get(key) for key in preserved_keys if key in source),
        "all_other_environment_keys_and_values_preserved": observer == {
            key: value for key, value in source.items() if key not in stripped},
        "parent_environment_mutated": False,
    }
    if receipt["absent_in_observer"] != list(JCMD_OPTION_ENV_KEYS) or not all(
            receipt[key] for key in ("preserved_required_runtime_values_unchanged",
                                     "all_other_environment_keys_and_values_preserved")):
        raise CandidateError("jcmd observer environment preservation proof failed")
    return observer, receipt


def _rewrite_private_mphserver_ini(installed_ini: Path, shadow_ini: Path) -> dict[str, Any]:
    """Apply the reviewed limits to one byte-identical, task-private INI copy."""
    for path, label in ((installed_ini, "installed"), (shadow_ini, "private shadow")):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise CandidateError(f"{label} mphserver INI is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise CandidateError(f"{label} mphserver INI must be a regular non-symlink file")
    installed_before = installed_ini.read_bytes()
    private_before = shadow_ini.read_bytes()
    if installed_before != private_before:
        raise CandidateError("private mphserver INI was not an exact copy before the reviewed edit")
    installed_hash = _sha_bytes(installed_before)
    private_mode = stat.S_IMODE(shadow_ini.stat().st_mode)
    try:
        lines = private_before.decode("utf-8", "strict").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise CandidateError("mphserver INI is not valid UTF-8") from exc
    xms_rows: list[int] = []
    xmx_rows: list[int] = []
    processor_rows: list[int] = []
    allowed_existing_xx = {"-XX:MaxMetaspaceSize=1g", "-XX:-SplitIfBlocks"}
    unknown_xx_rows: list[int] = []
    for index, raw in enumerate(lines):
        value = raw.rstrip("\r\n").strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith("-Xms"):
            xms_rows.append(index)
        if value.startswith("-Xmx"):
            xmx_rows.append(index)
        if value.startswith("-XX:ActiveProcessorCount"):
            processor_rows.append(index)
        if value.startswith("-XX:") and value not in allowed_existing_xx:
            unknown_xx_rows.append(index)
    if ([lines[index].rstrip("\r\n").strip() for index in xms_rows] != ["-Xms40m"] or
            [lines[index].rstrip("\r\n").strip() for index in xmx_rows] != ["-Xmx2g"] or
            processor_rows or unknown_xx_rows):
        raise CandidateError("mphserver INI has absent, duplicate, or conflicting reviewed JVM options")

    def newline_of(line: str) -> str:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
        if line.endswith("\r"):
            return "\r"
        return ""

    xms_index, xmx_index = xms_rows[0], xmx_rows[0]
    xms_newline, xmx_newline = newline_of(lines[xms_index]), newline_of(lines[xmx_index])
    lines[xms_index] = ("-XX:ActiveProcessorCount=2" + (xms_newline or "\n") +
                        "-Xms128m" + xms_newline)
    lines[xmx_index] = "-Xmx1024m" + xmx_newline
    rewritten = "".join(lines).encode("utf-8")
    temporary = shadow_ini.with_name(f".{shadow_ini.name}.w24-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(rewritten)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, private_mode)
        os.replace(temporary, shadow_ini)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    installed_after = installed_ini.read_bytes()
    private_after = shadow_ini.read_bytes()
    if (_sha_bytes(installed_after) != installed_hash or
            private_after != rewritten or
            stat.S_IMODE(shadow_ini.stat().st_mode) != private_mode):
        raise CandidateError("private INI edit failed integrity or installed-source preservation checks")
    return {
        "status": "PRIVATE_MPH_SERVER_INI_PATCHED_INSTALLED_SOURCE_UNCHANGED",
        "installed_path": str(installed_ini),
        "private_path": str(shadow_ini),
        "installed_sha256_before": installed_hash,
        "installed_sha256_after": _sha_bytes(installed_after),
        "private_sha256_before": _sha_bytes(private_before),
        "private_sha256_after": _sha_bytes(private_after),
        "private_mode_before": oct(private_mode),
        "private_mode_after": oct(stat.S_IMODE(shadow_ini.stat().st_mode)),
        "replaced_options": {"-Xms40m": "-Xms128m", "-Xmx2g": "-Xmx1024m"},
        "added_options": ["-XX:ActiveProcessorCount=2"],
        "install_tree_mutated": False,
    }


def _canonical_executable_identity(path: Path) -> dict[str, Any]:
    try:
        canonical = path.resolve(strict=True)
        metadata = canonical.stat()
        if not stat.S_ISREG(metadata.st_mode) or not os.access(canonical, os.X_OK):
            raise CandidateError("expected Java executable is not an executable regular file")
        return {"path": str(canonical), "device": int(metadata.st_dev),
                "inode": int(metadata.st_ino), "sha256": _sha_file(canonical)}
    except (OSError, RuntimeError) as exc:
        raise CandidateError("expected Java executable identity is unavailable") from exc


def _parse_text_executable_identity(stdout: str, *, pid: int,
                                    expected_path: Path) -> dict[str, Any]:
    """Select the exact Java text vnode from lsof's field output."""
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {"pid": None}
    for line in stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            if current.get("path") is not None:
                rows.append(current)
            current = {"pid": int(value) if value.isdigit() else None}
        elif tag == "f":
            if current.get("path") is not None:
                rows.append(current)
            current = {"pid": current.get("pid"), "fd": value}
        elif tag == "D":
            try:
                current["device"] = int(value, 0)
            except ValueError:
                current["device"] = None
        elif tag == "i":
            current["inode"] = int(value) if value.isdigit() else None
        elif tag == "n":
            current["path"] = value
    if current.get("path") is not None:
        rows.append(current)
    canonical_expected = expected_path.resolve(strict=True)
    matches = []
    for row in rows:
        path = row.get("path")
        try:
            canonical = Path(path).resolve(strict=True) if isinstance(path, str) else None
        except (OSError, RuntimeError):
            canonical = None
        if row.get("pid") == pid and row.get("fd") == "txt" and canonical == canonical_expected:
            matches.append(row)
    if len(matches) != 1:
        raise CandidateError("lsof did not identify exactly one expected Java text executable")
    row = matches[0]
    metadata = canonical_expected.stat()
    digest = _sha_file(canonical_expected)
    if (row.get("device") != int(metadata.st_dev) or
            row.get("inode") != int(metadata.st_ino)):
        raise CandidateError("Java text executable device/inode differs from the pinned COMSOL Java")
    return {"path": str(canonical_expected), "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino), "sha256": digest}


def _read_text_executable_identity(pid: int, expected_path: Path) -> dict[str, Any]:
    command = ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-d", "txt", "-FnDfi"]
    result = subprocess.run(command, capture_output=True, text=True,
                            check=False, timeout=15)
    if result.returncode != 0 or result.stderr.strip():
        raise CandidateError("lsof could not read the exact server text executable")
    return {**_parse_text_executable_identity(result.stdout, pid=pid,
                                               expected_path=expected_path),
            "command": command,
            "stdout_sha256": _sha_bytes(result.stdout.encode()),
            "stderr_sha256": _sha_bytes(result.stderr.encode())}


def _read_process_uid(pid: int) -> int:
    command = ["/bin/ps", "-p", str(pid), "-o", "pid=,uid="]
    result = subprocess.run(command, capture_output=True, text=True,
                            check=False, timeout=10)
    if result.returncode != 0 or result.stderr.strip():
        raise CandidateError("fresh process UID query failed")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or not all(item.isdigit() for item in rows[0]):
        raise CandidateError("fresh process UID query was ambiguous")
    observed_pid, uid = map(int, rows[0])
    if observed_pid != pid:
        raise CandidateError("fresh process UID query returned another PID")
    return uid


def _validate_frozen_server_identity(expected: Mapping[str, Any],
                                     current: Mapping[str, Any], *,
                                     proc: Any, original_proc: Any) -> None:
    if proc is not original_proc or getattr(proc, "pid", None) != expected.get("pid") or proc.poll() is not None:
        raise CandidateError("server stop does not retain the original live Popen")
    if list(getattr(proc, "args", [])) != expected.get("launch_command"):
        raise CandidateError("original server Popen argv changed before TERM")
    for key in ("pid", "uid", "birth", "start_epoch_ms", "process_snapshot_birth",
                "post_exec_command_sha256",
                "launch_command_sha256", "java_executable", "post_exec_argv",
                "argv0_display", "allowed_argv0_labels", "argv0_role",
                "listener", "launch_command", "server_paths"):
        if current.get(key) != expected.get(key):
            raise CandidateError(f"server process identity changed before TERM: {key}")


def _validate_server_birth_domains(*, pid: int, snapshot: Mapping[str, Any],
                                   live_identity: Mapping[str, Any],
                                   core_birth: str, core_start_epoch_ms: int) -> dict[str, Any]:
    """Validate Core's epoch birth and preserve the OS snapshot's native birth label separately."""
    process_snapshot_birth = snapshot.get("birth")
    if (snapshot.get("pid") != pid or not isinstance(process_snapshot_birth, str) or
            not process_snapshot_birth or process_snapshot_birth != process_snapshot_birth.strip() or
            "\n" in process_snapshot_birth or "\r" in process_snapshot_birth):
        raise CandidateError("OS process snapshot birth label is missing or malformed")
    if (type(core_start_epoch_ms) is not int or core_start_epoch_ms <= 0 or
            core_birth != f"start_epoch_ms:{core_start_epoch_ms}"):
        raise CandidateError("Core server birth is not bound to its exact start epoch")
    if (live_identity.get("alive") is not True or
            type(live_identity.get("start_epoch_ms")) is not int or
            live_identity.get("start_epoch_ms") != core_start_epoch_ms):
        raise CandidateError("live process epoch differs from the retained Core server birth")
    return {"birth": core_birth, "start_epoch_ms": core_start_epoch_ms,
            "process_snapshot_birth": process_snapshot_birth}


def _build_g2_owned_server_receipt(*, handle: Any,
                                   observer_identity: Mapping[str, Any],
                                   process_snapshot: Mapping[str, Any],
                                   project_id: str, session_id: str,
                                   activity_id: str, candidate_sha256: str) -> dict[str, Any]:
    """Project the verified original Core handle into G2's strict OS receipt schema."""
    from comsol_mcp._session_context import OwnedServerProcessIdentity
    from comsol_mcp._session_server import ManagedServerHandle

    if (not isinstance(handle, ManagedServerHandle) or handle.retired or
            handle.process is None or handle.process.poll() is not None or
            not isinstance(handle.process_identity, OwnedServerProcessIdentity) or
            handle.endpoint is None):
        raise CandidateError("G2 receipt requires the same live non-retired Core ManagedServerHandle")
    pid = handle.process.pid
    core_birth = handle.process_identity.birth
    core_start = handle.start_epoch_ms
    port = handle.endpoint.port
    expected_listener = {"pid": pid, "endpoint": f"127.0.0.1:{port}"}
    from comsol_mcp._session_context import CanonicalSocket
    core_listener = CanonicalSocket("127.0.0.1", port)
    snapshot_birth = process_snapshot.get("birth")
    command = process_snapshot.get("command")
    command_sha = process_snapshot.get("command_sha256")
    java = observer_identity.get("java_executable")
    if (process_snapshot.get("pid") != pid or
            observer_identity.get("status") != "CORE_MANAGED_ORIGINAL_POPEN_JAVA_IDENTITY_FROZEN" or
            observer_identity.get("pid") != pid or observer_identity.get("birth") != core_birth or
            observer_identity.get("start_epoch_ms") != core_start or
            observer_identity.get("process_snapshot_birth") != snapshot_birth or
            observer_identity.get("post_exec_command_sha256") != command_sha or
            observer_identity.get("listener") != expected_listener or
            observer_identity.get("uid") != os.getuid() or
            not isinstance(java, Mapping) or not isinstance(java.get("path"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", str(java.get("sha256", "")))):
        raise CandidateError("G2 receipt inputs differ from the frozen Core/Java/loopback identity")
    if (type(pid) is not int or pid <= 1 or type(port) is not int or not 1 <= port <= 65535 or
            handle.endpoint.address != "127.0.0.1" or
            handle.process_identity.pid != pid or
            handle.process_identity.start_epoch_ms != core_start or
            handle.process_identity.listener_sockets != (core_listener,) or
            not isinstance(core_birth, str) or core_birth != f"start_epoch_ms:{core_start}" or
            type(core_start) is not int or core_start <= 0 or
            not isinstance(snapshot_birth, str) or not snapshot_birth.strip() or
            snapshot_birth != snapshot_birth.strip() or "\n" in snapshot_birth or "\r" in snapshot_birth or
            not isinstance(command, str) or _sha_bytes(command.encode()) != command_sha or
            not isinstance(command_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", command_sha)):
        raise CandidateError("G2 receipt OS birth/command/endpoint tuple is malformed")
    if (not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", project_id) or
            not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", session_id) or
            not isinstance(activity_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", activity_id) or
            not isinstance(candidate_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256)):
        raise CandidateError("G2 receipt must retain its exact W24 project/session/candidate binding")
    if (not isinstance(observer_identity.get("post_exec_argv"), list) or
            not observer_identity.get("post_exec_argv") or
            "comsol" not in command.lower() or
            not any(token.lower() == "mphserver" or "serverapplication" in token.lower()
                    for token in observer_identity["post_exec_argv"])):
        raise CandidateError("G2 receipt does not bind a verified COMSOL Server command")
    return {
        "schema": "COMSOL_MCP_OWNED_SERVER_ISOLATION_RECEIPT_V1",
        "status": "RUNNING",
        "process": {"pid": pid, "birth": snapshot_birth,
                    "command_sha256": command_sha, "port": port},
        "owner_binding": {"project_id": project_id, "session_id": session_id,
                          "activity_id": activity_id,
                          "candidate_sha256": candidate_sha256,
                          "core_birth": core_birth,
                          "core_start_epoch_ms": core_start,
                          "core_executable": handle.process_identity.executable,
                          "server_uid": observer_identity["uid"],
                          "post_exec_command_sha256": command_sha,
                          "java_executable": dict(java),
                          "listener": expected_listener},
    }


def _parse_effective_jvm_flags(stdout: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for name in _EXPECTED_EFFECTIVE_JVM_FLAGS:
        matches = re.findall(
            rf"(?m)^\s*[A-Za-z_][A-Za-z0-9_]*\s+{re.escape(name)}\s*[:=]+\s*(\d+)(?:\s|$)",
            stdout)
        if len(matches) != 1:
            raise CandidateError(f"jcmd output does not contain exactly one valid {name} value")
        values[name] = int(matches[0])
    if values != _EXPECTED_EFFECTIVE_JVM_FLAGS:
        raise CandidateError("effective JVM CPU/heap limits differ from the frozen budget")
    return values


def _read_effective_jvm_limits(*, pid: int, jcmd: Path,
                               identity_reader: Callable[[], Mapping[str, Any]],
                               expected_identity: Mapping[str, Any],
                               evidence_path: Path | None = None) -> dict[str, Any]:
    try:
        if jcmd.name != "jcmd" or jcmd.parent.name != "bin" or jcmd.is_symlink():
            raise CandidateError("jcmd path is not a regular binary in the pinned JDK bin directory")
        jcmd_identity = _canonical_executable_identity(jcmd)
        jdk_home = jcmd.parent.parent.resolve(strict=True)
        if not Path(jcmd_identity["path"]).is_relative_to(jdk_home):
            raise CandidateError("jcmd executable resolved outside its pinned JDK home")
    except Exception as exc:
        hold = {"status": "EFFECTIVE_JVM_LIMITS_READBACK_HOLD",
                "pid": pid, "command": [str(jcmd), str(pid), "VM.flags", "-all"],
                "phase": "jcmd_executable_identity", "identity_before": None,
                "identity_after": None, "jcmd_exit_code": None,
                "stdout_sha256": _sha_bytes(b""), "stdout_bytes": 0,
                "stderr_sha256": _sha_bytes(b""),
                "error_type": type(exc).__name__, "error": _hold_text(exc)}
        if evidence_path is not None:
            _write_new(evidence_path, hold)
        raise JvmLimitsReadbackHold(f"effective JVM limit readback held at jcmd identity: {exc}") from exc
    command = [jcmd_identity["path"], str(pid), "VM.flags", "-all"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    result: Any = None
    observer_environment: dict[str, str] | None = None
    observer_environment_receipt: dict[str, Any] | None = None
    phase = "observer_environment"
    try:
        observer_environment, observer_environment_receipt = _jcmd_observer_environment()
        phase = "pre_read_identity"
        before = dict(identity_reader())
        if before != dict(expected_identity):
            raise CandidateError("jcmd pre-read process identity differs from the frozen child")
        phase = "jcmd_vm_flags_all"
        command_error = None
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    check=False, timeout=30, env=observer_environment)
        except Exception as exc:
            command_error = exc
        phase = "post_read_identity"
        after = dict(identity_reader())
        if after != dict(expected_identity) or after != before:
            raise CandidateError("child process identity changed during jcmd readback")
        if command_error is not None:
            raise CandidateError(f"pinned Java 11 jcmd invocation failed: {type(command_error).__name__}: {command_error}") from command_error
        phase = "effective_flag_parse"
        if result.returncode != 0 or result.stderr.strip():
            raise CandidateError("pinned Java 11 jcmd VM.flags -all failed")
        values = _parse_effective_jvm_flags(result.stdout)
    except Exception as exc:
        stdout_value = (result.stdout if result is not None else
                        getattr(locals().get("command_error"), "stdout", ""))
        stderr_value = (result.stderr if result is not None else
                        getattr(locals().get("command_error"), "stderr", ""))
        stdout = stdout_value.decode("utf-8", "replace") if isinstance(stdout_value, bytes) else stdout_value if isinstance(stdout_value, str) else ""
        stderr = stderr_value.decode("utf-8", "replace") if isinstance(stderr_value, bytes) else stderr_value if isinstance(stderr_value, str) else ""
        hold = {"status": "EFFECTIVE_JVM_LIMITS_READBACK_HOLD",
                "pid": pid, "command": command, "phase": phase,
                "jcmd_executable": jcmd_identity, "jdk_home": str(jdk_home),
                "identity_before": before, "identity_after": after,
                "jcmd_exit_code": getattr(result, "returncode", None),
                "stdout_sha256": _sha_bytes(stdout.encode()),
                "stdout_bytes": len(stdout.encode()),
                "stderr_sha256": _sha_bytes(stderr.encode()),
                "observer_environment": observer_environment_receipt,
                "error_type": type(exc).__name__, "error": _hold_text(exc)}
        if evidence_path is not None:
            _write_new(evidence_path, hold)
        raise JvmLimitsReadbackHold(f"effective JVM limit readback held at {phase}: {exc}") from exc
    receipt = {"status": "EFFECTIVE_JVM_LIMITS_VERIFIED",
               "pid": pid, "command": command,
               "jcmd_executable": jcmd_identity, "jdk_home": str(jdk_home),
               "stdout_sha256": _sha_bytes(result.stdout.encode()),
               "stdout_bytes": len(result.stdout.encode()),
               "stderr_sha256": _sha_bytes(result.stderr.encode()),
               "jcmd_exit_code": result.returncode,
               "identity_before": before, "identity_after": after,
               "observer_environment": observer_environment_receipt,
               "effective_flags": values}
    if evidence_path is not None:
        _write_new(evidence_path, receipt)
    return receipt


def _start_rss_monitor_from_core_observation(
        monitor_box: dict[str, SampledRssMonitor], payload: Mapping[str, Any],
        worker_search_root: Path, events_path: Path) -> None:
    """Start observation from Core's birth event; never resolve or adopt a PID."""
    if payload.get("event") != "BIRTH_OBSERVED" or monitor_box.get("monitor") is not None:
        raise CandidateError("Core birth observation is duplicated or out of phase")
    pid = payload.get("pid")
    birth = payload.get("birth")
    match = re.fullmatch(r"start_epoch_ms:(\d+)", birth) if isinstance(birth, str) else None
    if type(pid) is not int or pid <= 1 or match is None or int(match.group(1)) <= 0:
        raise CandidateError("Core did not provide a typed original Popen birth identity")
    monitor = SampledRssMonitor(server_pid=pid, server_birth_ms=int(match.group(1)),
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


def _hold_text(value: Any) -> str:
    try:
        return str(value)[:512]
    except BaseException:
        return "<" + type(value).__name__ + ": text unavailable>"


def _hold_diagnostic(state: dict[str, Any], phase: str, exc: BaseException) -> None:
    if len(state["diagnostics"]) < 32:
        state["diagnostics"].append({"phase": phase, "type": type(exc).__name__, "error": _hold_text(exc)})


def _capture_hold_handles(server: Any, daemon: Any, first_cause: BaseException | None = None) -> dict[str, Any]:
    """Keep actual receivers before cleanup/diagnostics; never reconstruct PIDs."""
    state = getattr(server, "_unknown_hold_state", None)
    if state is None:
        state = {"server": server, "daemon": daemon, "server_process": getattr(server, "_original_server_popen", None), "server_pid": getattr(server, "_original_server_pid", None),
                 "workers": {}, "backends": {}, "collections": {}, "diagnostics": [],
                 "first_cause": first_cause, "complete": False, "graph_changed": False,
                 "server_handle": getattr(server, "managed_handle", None),
                 "server_key": ((getattr(server, "managed_project_id", None),
                                  getattr(server, "managed_session_id", None))
                                 if getattr(server, "managed_project_id", None) is not None else None),
                 "default_backend": getattr(daemon, "_default_backend", None), "services": {}}
        server._unknown_hold_state = state
    if state["first_cause"] is None:
        state["first_cause"] = first_cause
    if state["server"] is not server or state["daemon"] is not daemon:
        state["graph_changed"] = True
        _hold_diagnostic(state, "owner_replacement", CandidateError("original hold receiver changed"))
        return state
    try:
        if daemon is not None:
            if daemon._default_backend is not state["default_backend"]:
                raise CandidateError("original default backend changed")
            if not daemon._session_connect_lock.acquire(blocking=False):
                state["complete"] = False
                _hold_diagnostic(state, "capture_lock_busy", CandidateError("original session registry lock busy; proof incomplete"))
                return state
            try:
                for name in ("_session_worker_handles", "_session_backends", "_session_worker_child_identities", "_session_server_handles"):
                    collection = getattr(daemon, name)
                    state["collections"].setdefault(name, collection)
                    if state["collections"][name] is not collection:
                        raise CandidateError("original registered owner collection changed")
                workers = dict(daemon._session_worker_handles)
                server_handles = dict(daemon._session_server_handles)
                current_server_handle = getattr(server, "managed_handle", None)
                current_server_key = ((getattr(server, "managed_project_id", None),
                                       getattr(server, "managed_session_id", None))
                                      if getattr(server, "managed_project_id", None) is not None else None)
                if state["server_handle"] is None and current_server_handle is not None:
                    state["server_handle"] = current_server_handle
                    state["server_key"] = current_server_key
                if (state["server_handle"] is not current_server_handle or
                        state["server_key"] != current_server_key):
                    raise CandidateError("original Core ManagedServerHandle binding changed")
                if current_server_handle is None:
                    server_owner_exact = (not server_handles and
                                          getattr(server, "_server_birth_attempted", False) is not True)
                else:
                    server_owner_exact = (
                        current_server_key is not None and
                        len(server_handles) == 1 and
                        server_handles.get(current_server_key) is current_server_handle and
                        current_server_handle.process is getattr(server, "_original_server_popen", None) and
                        current_server_handle.process is getattr(server, "proc", None) and
                        current_server_handle.process.pid == getattr(server, "_original_server_pid", None) and
                        getattr(getattr(server, "launcher", None), "_handles", {}).get(current_server_key)
                        is current_server_handle
                    )
                for key, backend in daemon._session_backends.items():
                    state["backends"].setdefault(key, backend)
                    if state["backends"][key] is not backend:
                        raise CandidateError("original registered backend changed")
                    state["services"].setdefault(key, getattr(backend, "service", None))
                    if state["services"][key] is not getattr(backend, "service", None):
                        raise CandidateError("original registered ExecutionService changed")
                    worker = getattr(backend, "worker", None)
                    if worker is not None:
                        if key in workers and workers[key] is not worker:
                            raise CandidateError("registered backend/Worker receivers differ")
                        workers[key] = worker
                for key, worker in workers.items():
                    process = getattr(worker, "_process", None)
                    saved = state["workers"].get(key)
                    proof = daemon._session_worker_child_identities.get(key)
                    if saved is None:
                        state["workers"][key] = (worker, process, proof, getattr(process, "pid", None))
                    elif saved[0] is not worker or saved[1] is not process:
                        raise CandidateError("original registered Worker/Popen changed")
                complete = (not daemon._control_dispatch_inflight and not daemon._worker_admission_inflight
                            and not daemon.running and server_owner_exact)
                for worker, process, proof, original_pid in state["workers"].values():
                    complete = complete and process is not None and isinstance(proof, Mapping) and proof.get("worker") is worker and proof.get("process") is process and proof.get("pid") == original_pid and type(proof.get("start_epoch_ms")) is int and proof["start_epoch_ms"] > 0
                for key, backend in state["backends"].items():
                    complete = complete and key in state["workers"]
            finally:
                daemon._session_connect_lock.release()
        else:
            complete = True
        original = getattr(server, "_original_server_popen", None)
        if state["server_process"] is None and original is not None:
            state["server_process"] = original
            state["server_pid"] = getattr(server, "_original_server_pid", None)
        if original is not state["server_process"] or getattr(server, "proc", None) is not state["server_process"] or (original is not None and (original.pid != state["server_pid"] or getattr(server, "_original_server_pid", None) != state["server_pid"])):
            raise CandidateError("original server Popen changed or was never captured")
        complete = complete and (original is not None or getattr(server, "_server_birth_attempted", True) is False)
        state["complete"] = bool(complete) and not state["graph_changed"]
    except BaseException as exc:
        state["complete"] = False
        state["graph_changed"] = True
        _hold_diagnostic(state, "original_object_proof", exc)
    return state


def _fence_hold_handles(state: dict[str, Any], monitor: Any) -> None:
    def forbidden(*_args, **_kwargs):
        raise CandidateError("original UNKNOWN owner is fenced; no SDK/lifecycle/admission effect")
    daemon = state["daemon"]
    # Admission/monitor fencing cannot wait for an original in-flight SDK owner.
    if daemon is not None:
        try:
            daemon._closing = True
            daemon._worker_retirement_state = "CLOSED"
            daemon.dispatch = forbidden
            daemon._dispatch_session_control = forbidden
            daemon.close = forbidden
            daemon.closed.set()
            acquired = daemon._admission_lock.acquire(blocking=False)
            if acquired:
                daemon._admission_lock.release()
            else:
                state["complete"] = False
                _hold_diagnostic(state, "admission_lock_busy", CandidateError("original admission lock busy; proof incomplete"))
        except BaseException as exc:
            state["complete"] = False
            _hold_diagnostic(state, "fence_admission", exc)
    observed_backends = list(state["backends"].values()) + ([state["default_backend"]] if state["default_backend"] is not None else [])
    observed_workers = [row[0] for row in state["workers"].values()]
    if daemon is not None:
        try:
            acquired = daemon._session_connect_lock.acquire(blocking=False)
            if acquired:
                try:
                    observed_backends += list(daemon._session_backends.values())
                    observed_backends += [daemon._default_backend]
                    observed_workers += list(daemon._session_worker_handles.values())
                finally:
                    daemon._session_connect_lock.release()
            else:
                state["complete"] = False
                _hold_diagnostic(state, "fence_registry_lock_busy", CandidateError("original session registry lock busy; proof incomplete"))
        except BaseException as exc:
            state["complete"] = False
            _hold_diagnostic(state, "fence_registry_snapshot", exc)
    observed_workers += [getattr(b, "worker", None) for b in observed_backends]
    for receiver, names in [(b, ("execute", "connect", "connect_session", "close")) for b in observed_backends] + [(w, ("_request", "start", "close", "health", "client")) for w in observed_workers if w is not None]:
        try:
            for name in names:
                if callable(getattr(receiver, name, None)):
                    setattr(receiver, name, forbidden)
        except BaseException as exc:
            state["complete"] = False
            _hold_diagnostic(state, "fence_original_receivers", exc)
    if monitor is not None:
        try:
            monitor._stop.set()
        except BaseException as exc:
            state["complete"] = False
            _hold_diagnostic(state, "fence_monitor", exc)


def _hold_forever_with_handles(monitor: Any, *, server: Any = None, daemon: Any = None,
                               first_cause: BaseException | None = None, run_dir: Path | None = None,
                               stop_event: threading.Event | None = None) -> dict[str, Any] | None:
    """Poll only original Popen objects. Diagnostics cannot discard the owner."""
    if server is None:
        while stop_event is None or not stop_event.is_set():
            try: time.sleep(0.25)
            except BaseException: pass
        return None
    state = _capture_hold_handles(server, daemon, first_cause)
    while stop_event is None or not stop_event.is_set():
        try:
            state = _capture_hold_handles(server, daemon, first_cause)
            _fence_hold_handles(state, monitor)
            rows = []; terminal = state["complete"]
            processes = [("server", state["server_process"], state["server_pid"])] + [("worker:" + _hold_text(key), row[1], row[3]) for key, row in state["workers"].items()]
            for label, process, original_pid in processes:
                if process is None:
                    continue
                try:
                    if process.pid != original_pid:
                        raise CandidateError("original Popen PID field changed; no poll of rewritten identity")
                    code = process.poll()
                    rows.append({"label": label, "pid": process.pid, "returncode": code})
                    terminal = terminal and type(code) is int
                except BaseException as exc:
                    terminal = False
                    _hold_diagnostic(state, "original_popen_poll", exc)
            receipt = {"status": "FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL" if terminal else "UNKNOWN_RETAINED_ORIGINAL_OWNER",
                       "original_proof_complete": state["complete"], "children": rows,
                       "safe_retry": False, "signals_sent": False, "new_sdk_calls": False,
                       "action_budget": "UNRESOLVED_WHILE_ORIGINAL_CHILDREN_LIVE_OR_PROOF_INCOMPLETE",
                       "first_cause": None if state["first_cause"] is None else _hold_text(state["first_cause"]),
                       "secondary_diagnostics": state["diagnostics"]}
            if run_dir is not None:
                try:
                    target = run_dir / "original_owner_observation.json"
                    _replace(target, receipt) if target.exists() else _write_new(target, receipt)
                except BaseException as exc:
                    _hold_diagnostic(state, "hold_evidence_write", exc)
            if terminal:
                return receipt
        except BaseException as exc:
            state["complete"] = False
            _hold_diagnostic(state, "hold_observation", exc)
        try: time.sleep(0.25)
        except BaseException as exc: _hold_diagnostic(state, "hold_yield", exc)
    return None

class SampledRssMonitor:
    """One-second, exact-PID sampled admission stop for this candidate only."""

    def __init__(self, *, server_pid: int, server_birth_ms: int,
                 worker_search_root: Path, events_path: Path,
                 threshold_bytes: int = RSS_STOP_BYTES,
                 interval_seconds: float = SAMPLE_INTERVAL_SECONDS,
                 ps_runner: Callable[..., Any] = subprocess.run,
                 identity_reader: Callable[[int], Mapping[str, Any]] | None = None,
                 probe_timeout_reader: Callable[[float], float] | None = None,
                 include_owned_descendants: bool = False,
                 session_reader: Callable[[int], int] | None = None):
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
        self.probe_timeout_reader = probe_timeout_reader
        self.include_owned_descendants = include_owned_descendants
        self.owned_children = {}
        self.owned_groups = {server_pid: server_birth_ms} if include_owned_descendants else {}
        self.retired_owned_leaders = set()
        self.session_reader = session_reader or os.getsid
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
            if self.include_owned_descendants:self.owned_groups[pid]=birth_ms

    def bind_owned_child(self, process: Any, birth_ms: int) -> None:
        if type(getattr(process,"pid",None)) is not int or type(birth_ms) is not int or birth_ms<=0:
            raise CandidateError("owned RSS child requires its actual Popen/birth")
        with self._lock:
            self.owned_children[process.pid]=(process,birth_ms)
            if self.include_owned_descendants:self.owned_groups[process.pid]=birth_ms

    def retire_owned_child(self, process: Any) -> None:
        with self._lock:
            saved=self.owned_children.get(process.pid)
            if saved is None or saved[0] is not process or process.poll() is None:
                raise CandidateError("RSS child cannot retire without its exact terminal Popen")
            # A reaped leader does not retire its original private process group.
            # Fresh complete group absence in sample_once is the only RSS release.
            if self.include_owned_descendants:self.retired_owned_leaders.add(process.pid)
            else:del self.owned_children[process.pid]

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
        command = ["/bin/ps", "-axo", "pid=,ppid=,pgid=,rss=,command=" if self.include_owned_descendants else "pid=,ppid=,rss=,command="]
        try:
            result = self.ps_runner(command, capture_output=True, text=True,
                                    check=False, timeout=(10 if self.probe_timeout_reader is None else self.probe_timeout_reader(10.0)))
            if result.returncode != 0 or result.stderr.strip():
                raise CandidateError("scoped RSS ps probe failed")
            rows: dict[int, dict[str, Any]] = {}
            all_rows = {}
            worker_pids: list[int] = []
            worker_prefix = str(self.worker_search_root).lower()
            observer_seen = False
            for line in result.stdout.splitlines():
                fields = line.strip().split(None, 4 if self.include_owned_descendants else 3)
                required = 5 if self.include_owned_descendants else 4
                numeric = 4 if self.include_owned_descendants else 3
                if len(fields)<required or not all(x.isdigit() for x in fields[:numeric]):
                    if self.include_owned_descendants and line.strip():
                        raise CandidateError("complete owned-group RSS process row is malformed")
                    continue
                pid,ppid=int(fields[0]),int(fields[1])
                pgid=int(fields[2]) if self.include_owned_descendants else None
                rss_kib=int(fields[3] if self.include_owned_descendants else fields[2])
                command_text=fields[4] if self.include_owned_descendants else fields[3]
                if pid==os.getpid():observer_seen=True
                all_rows[pid]={"pid":pid,"ppid":ppid,"rss_bytes":rss_kib*1024,
                               **({"pgid":pgid} if self.include_owned_descendants else {})}
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
            if self.include_owned_descendants:
                if not observer_seen:raise CandidateError("complete group RSS sample omitted observing CLI")
                for pid,row in all_rows.items():
                    if pid<=1:continue
                    try:row['sid']=self.session_reader(pid)
                    except ProcessLookupError:
                        if self.identity_reader(pid).get('alive') is not False:
                            raise CandidateError("live process session identity unavailable in complete owned RSS sample")
                        row['sid']=None
                    if type(row.get('sid')) is not int and row.get('sid') is not None:
                        raise CandidateError("native RSS session identity is not a typed integer")
                if all_rows[self.server_pid].get('pgid')!=self.server_pid or all_rows[self.server_pid].get('sid')!=self.server_pid:
                    raise CandidateError("original Server private PGID/SID changed")
                for pid in worker_pids:
                    if all_rows[pid].get('pgid')!=pid or all_rows[pid].get('sid')!=pid:
                        raise CandidateError("original Worker private PGID/SID changed")
                with self._lock:
                    children=dict(self.owned_children);groups=dict(self.owned_groups)
                for pid,(process,birth_ms) in children.items():
                    if process.poll() is not None:continue
                    ident=self.identity_reader(pid)
                    if (pid not in all_rows or ident.get("alive") is not True or
                            ident.get("start_epoch_ms")!=birth_ms or all_rows[pid].get("pgid")!=pid):
                        raise CandidateError("owned child RSS Popen/birth/private group unavailable or changed")
                    rows[pid]={**all_rows[pid],"kind":"owned_child","start_epoch_ms":birth_ms}
                for pgid,birth_ms in groups.items():
                    members=[row for row in all_rows.values() if row.get("pgid")==pgid or row.get("sid")==pgid]
                    for member in members:
                        pid=member["pid"];ident=self.identity_reader(pid)
                        if (ident.get("alive") is not True or type(ident.get("start_epoch_ms")) is not int
                                or ident["start_epoch_ms"]<birth_ms):
                            raise CandidateError("original owned-group member RSS birth identity unavailable or predates group")
                        rows[pid]={**member,"kind":rows.get(pid,{}).get("kind","owned_group_descendant"),
                                   "start_epoch_ms":ident["start_epoch_ms"],"original_owned_pgid":pgid,"original_owned_sid":pgid}
                    if not members and pgid in children and children[pgid][0].poll() is not None:
                        with self._lock:
                            self.owned_children.pop(pgid,None);self.owned_groups.pop(pgid,None)
                            self.retired_owned_leaders.discard(pgid)
                owned=set(rows)
                changed=True
                while changed:
                    changed=False
                    for pid,row in all_rows.items():
                        if pid not in owned and row["ppid"] in owned:
                            owned.add(pid);rows[pid]={**row,"kind":"owned_descendant"};changed=True
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

    def stop(self, *, timeout_seconds: float | None = None) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=(max(12.0, self.interval_seconds * 12) if timeout_seconds is None else timeout_seconds))
            if thread.is_alive():
                with self._lock:
                    self.stop_reason = self.stop_reason or "RSS_MONITOR_THREAD_DID_NOT_STOP"
                raise CandidateError("RSS monitor thread did not stop; preserve all live process handles")


def _w24_private_tree_manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Hash an installation subtree without following links."""
    if root.is_symlink() or not root.is_dir():
        raise CandidateError("private COMSOL architecture tree must be a real directory")
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            rows[relative] = {"kind": "symlink", "target": os.readlink(path)}
        elif stat.S_ISDIR(metadata.st_mode):
            rows[relative] = {"kind": "directory", "mode": stat.S_IMODE(metadata.st_mode)}
        elif stat.S_ISREG(metadata.st_mode):
            rows[relative] = {"kind": "file", "bytes": metadata.st_size,
                              "mode": stat.S_IMODE(metadata.st_mode),
                              "sha256": _sha_file(path)}
        else:
            raise CandidateError("COMSOL architecture tree contains a special file")
    return rows


class W24FixedDarwinSetupProcessFactory:
    """Narrow OwnedServerLauncher process-factory adapter for one W24 session."""

    def __init__(self, *, install_root: Path, owned_server_root: Path,
                 evidence: Path, popen: Callable[..., Any] = subprocess.Popen,
                 server_np: int = 2):
        if type(server_np) is not int or server_np != 2:
            raise CandidateError("W24 fixed Darwin setup requires the exact reviewed server_np=2")
        self.install_root = install_root.resolve(strict=True)
        self.owned_server_root = owned_server_root.resolve(strict=False)
        self.evidence = evidence
        self.popen = popen
        self.server_np = server_np
        self.launch_command: list[str] | None = None

    def _prepare_private_arch(self, private_installation: Path) -> dict[str, Any]:
        installed_arch = self.install_root / "bin/macarm64"
        shadow_arch = private_installation / "bin/macarm64"
        if (installed_arch.is_symlink() or not installed_arch.is_dir() or
                shadow_arch.is_symlink() is not True or
                shadow_arch.resolve(strict=True) != installed_arch.resolve(strict=True)):
            raise CandidateError("OwnedServerLauncher macarm64 path is not the exact installed symlink")
        installed_before = _w24_private_tree_manifest(installed_arch)
        installed_ini = installed_arch / "comsolmphserver.ini"
        if installed_ini.is_symlink() or not installed_ini.is_file():
            raise CandidateError("installed COMSOL server INI is missing or aliased")
        installed_ini_before = _sha_file(installed_ini)
        shadow_arch.unlink()
        shutil.copytree(installed_arch, shadow_arch, symlinks=True, copy_function=shutil.copy2)
        if shadow_arch.is_symlink() or _w24_private_tree_manifest(shadow_arch) != installed_before:
            raise CandidateError("private macarm64 copy differs from the installed source tree")
        patch = _rewrite_private_mphserver_ini(installed_ini, shadow_arch / "comsolmphserver.ini")
        installed_after = _w24_private_tree_manifest(installed_arch)
        installed_ini_after = _sha_file(installed_ini)
        if installed_after != installed_before or installed_ini_after != installed_ini_before:
            raise CandidateError("installed COMSOL macarm64 bytes changed during private overlay")
        return {"schema": "W24_PRIVATE_MACARM64_SETUP_OVERLAY_V1",
                "status": "PRIVATE_COPY_PATCHED_INSTALLED_BYTES_UNCHANGED",
                "installed_arch": str(installed_arch), "private_arch": str(shadow_arch),
                "installed_tree_sha256": _json_hash(installed_before),
                "installed_tree_sha256_after": _json_hash(installed_after),
                "installed_ini_sha256_before": installed_ini_before,
                "installed_ini_sha256_after": installed_ini_after,
                "private_tree_sha256_before_ini_patch": _json_hash(installed_before),
                "private_ini_patch": patch,
                "install_tree_mutated": False}

    def __call__(self, command: list[str], **options: Any) -> Any:
        """Validate and stage before birth, then return the original Popen directly."""
        if sys.platform != "darwin":
            raise CandidateError("the fixed W24 owned-server process factory is Darwin-only")
        cwd = Path(options.get("cwd", ""))
        if cwd.is_symlink() or cwd.resolve(strict=True) != self.owned_server_root:
            raise CandidateError("OwnedServerLauncher cwd differs from the exact reviewed session root")
        private_installation = self.owned_server_root / "installation"
        from comsol_mcp._session_server import _server_directories, build_server_command
        expected = build_server_command(_server_directories(self.owned_server_root),
                                        platform_name="darwin", validate_launcher=False,
                                        server_np=self.server_np)
        if (not isinstance(command, list) or command != expected or
                command[0] != str(private_installation / "bin/comsol") or
                command[1:2] != ["mphserver"] or
                command.count("-np") != 1 or
                command[command.index("-np") + 1] != str(self.server_np)):
            raise CandidateError("OwnedServerLauncher command differs from the fixed W24 start plan")
        overlay = self._prepare_private_arch(private_installation)
        self.launch_command = list(command)
        receipt = {**overlay, "launch_command": list(command),
                   "popen_options": sorted(options),
                   "popen_environment_mutated": False,
                   "requested_server_np": self.server_np,
                   "fixed_np_arguments": ["-np", str(self.server_np)]}
        _write_new(self.evidence / "private_shadow_w24_jvm_overlay.json", receipt)
        # No logging, readback, or diagnostic step is allowed after child birth here.
        return self.popen(command, **options)


def _w24_owned_server_authorization_ref(*, reviewed_sha256: str,
                                        binding: Mapping[str, Any] | None,
                                        plan: Mapping[str, Any] | None) -> str:
    """Bind the stop audit reference to this exact candidate, execute plan, and consumed once."""
    if binding is None:
        raise CandidateError("W24 host-control lifecycle requires the fresh runtime binding")
    if (not isinstance(plan, Mapping) or
            plan.get("candidate_sha256") != reviewed_sha256 or
            plan.get("runtime_binding_sha256") != binding.get("binding_sha256") or
            plan.get("require_new_external_root_approval_and_once") is not True):
        raise CandidateError("W24 host-control reference lacks the exact candidate execute plan")
    namespace = Path(binding.get("namespace", ""))
    once_path = namespace / "W24_ONCE.json"
    fresh_runtime.scoped_path(binding, once_path)
    try:
        metadata = once_path.lstat()
        once_raw = once_path.read_bytes()
        once = json.loads(once_raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateError("W24 consumed-once receipt is unavailable for host-control binding") from exc
    if (once_path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            once.get("schema") != "W24_FRESH_ONCE_V2" or
            once.get("candidate_sha256") != reviewed_sha256):
        raise CandidateError("W24 consumed-once receipt differs from the reviewed candidate")
    activity = binding.get("activity_id")
    binding_sha = binding.get("binding_sha256")
    if (not isinstance(activity, str) or not activity or
            not isinstance(binding_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", binding_sha)):
        raise CandidateError("W24 authorization reference lacks exact activity/binding identity")
    plan_sha = _json_hash(plan)
    once_sha = _sha_bytes(once_raw)
    reference = (f"w24-owned-server:{activity}:{reviewed_sha256}:"
                 f"{binding_sha}:{plan_sha}:{once_sha}")
    if len(reference) > 512:
        raise CandidateError("W24 plan/once-bound authorization reference exceeds the schema limit")
    return reference


class W24SetupOwnedServerLauncher:
    """W24-only wrapper around the production OwnedServerLauncher owner."""

    def __init__(self, *, install_root: Path, evidence: Path,
                 installation_binding: Mapping[str, Any] | None = None,
                 observer: Any, on_birth_observation: Callable[[Mapping[str, Any]], None] | None = None,
                 process_identity_reader: Callable[[int], Mapping[str, Any]] | None = None,
                 listener_reader: Callable[[int], Any] | None = None,
                 popen: Callable[..., Any] = subprocess.Popen):
        from comsol_mcp._session_server import OwnedServerLauncher

        self.install_root = install_root.resolve(strict=True)
        self.installation_binding = dict(installation_binding) if isinstance(installation_binding, Mapping) else None
        self.evidence = evidence
        self.observer = observer
        self.on_birth_observation = on_birth_observation
        self._popen = popen
        self.server_np = 2
        self._core = OwnedServerLauncher(
            process_identity_reader=process_identity_reader or __import__(
                "comsol_mcp._platform_process", fromlist=["process_identity"]).process_identity,
            listener_reader=listener_reader, platform_name="darwin", server_np=self.server_np,
            ready_timeout_s=45.0, poll_interval_s=0.2)
        self._project_id: str | None = None
        self._workspace: Path | None = None
        self._candidate_sha256: str | None = None
        self._activity_id: str | None = None
        self._handles: dict[tuple[str, str], Any] = {}
        self._factories: dict[tuple[str, str], W24FixedDarwinSetupProcessFactory] = {}
        self._started = False

    def _verify_frozen_installation_identity(self, runtime: Any) -> dict[str, Any]:
        """Verify catalog version and full frozen release identity before Server birth."""
        from comsol_mcp._java_worker import JavaWorkerPaths
        from comsol_mcp._runtime_installation import inspect_installation, runtime_id_for_root

        frozen = self.installation_binding
        required = {"root", "exact_build", "readme_sha256", "classpath_manifest_sha256",
                    "jar_count", "jar_content_sha256"}
        if not isinstance(frozen, Mapping) or required - set(frozen):
            raise CandidateError("W24 session.start requires the full fresh frozen COMSOL installation identity")
        exact_build = frozen.get("exact_build")
        if exact_build != "6.4.0.293":
            raise CandidateError("W24 frozen COMSOL build must remain exactly 6.4.0.293")
        catalog_version = ".".join(exact_build.split(".")[:-1])
        if (not isinstance(frozen.get("root"), str) or
                not isinstance(frozen.get("readme_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", frozen["readme_sha256"]) or
                not isinstance(frozen.get("classpath_manifest_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", frozen["classpath_manifest_sha256"]) or
                type(frozen.get("jar_count")) is not int or frozen["jar_count"] != 25 or
                not isinstance(frozen.get("jar_content_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", frozen["jar_content_sha256"])):
            raise CandidateError("W24 frozen COMSOL build/README/JAR identity is malformed or incomplete")
        try:
            frozen_root = Path(frozen["root"]).resolve(strict=True)
            runtime_root = Path(runtime.installation_root).resolve(strict=True)
            runtime_id = runtime.runtime_id
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise CandidateError("W24 resolved runtime identity is incomplete") from exc
        if (frozen_root != self.install_root or runtime_root != frozen_root or
                runtime_id != runtime_id_for_root(frozen_root) or
                runtime.comsol_version != catalog_version):
            raise CandidateError("W24 resolved runtime root/catalog version differs from its frozen installation")

        try:
            inspected = inspect_installation(runtime_id)["installation"]
        except Exception as exc:
            raise CandidateError("W24 production installation inspection failed before Server birth") from exc
        if (inspected.get("runtime_id") != runtime_id or inspected.get("root") != str(frozen_root) or
                inspected.get("metadata_only") is not True or inspected.get("comsol_started") is not False or
                inspected.get("license_checked_out") is not False):
            raise CandidateError("W24 inspected installation identity is not exact metadata-only evidence")
        version = inspected.get("version")
        build = inspected.get("build")
        if (not isinstance(version, Mapping) or version.get("status") != "OBSERVED" or
                version.get("value") != catalog_version or
                not isinstance(build, Mapping) or build.get("status") != "OBSERVED" or
                build.get("value") != 293):
            raise CandidateError("W24 installed catalog version or exact build differs from its frozen identity")

        readme_path = frozen_root / "readme.txt"
        if readme_path.is_symlink() or not readme_path.is_file():
            raise CandidateError("W24 exact-build README is unavailable or aliased")
        readme_bytes = readme_path.read_bytes()
        readme_match = re.search(rb"^COMSOL\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\s+README\b",
                                 readme_bytes, re.MULTILINE)
        if (readme_match is None or readme_match.group(1).decode("ascii") != exact_build or
                _sha_bytes(readme_bytes) != frozen["readme_sha256"]):
            raise CandidateError("W24 installed README differs from the exact frozen 6.4.0.293 identity")

        java_executable = Path(runtime.java_executable).resolve(strict=True)
        jdk_home = java_executable.parent.parent
        paths = JavaWorkerPaths(frozen_root, jdk_home, project_root=frozen_root)
        try:
            classpath, manifest_sha, jar_count, jar_sha = paths.classpath()
        except Exception as exc:
            raise CandidateError("W24 production classpath/JAR identity could not be inspected before Server birth") from exc
        try:
            actual_classpath = tuple(Path(item).resolve(strict=True)
                                     for item in classpath.split(paths.classpath_separator))
            runtime_classpath = tuple(Path(item).resolve(strict=True) for item in runtime.classpath)
        except (OSError, TypeError, ValueError) as exc:
            raise CandidateError("W24 resolved runtime classpath is unavailable or malformed") from exc
        if (manifest_sha != frozen["classpath_manifest_sha256"] or
                jar_count != frozen["jar_count"] or jar_sha != frozen["jar_content_sha256"] or
                actual_classpath != runtime_classpath):
            raise CandidateError("W24 installed classpath/JAR identity differs from the frozen candidate")
        return {
            "status": "W24_RESOLVED_RUNTIME_AND_FROZEN_INSTALLATION_IDENTITY_VERIFIED_PREBIRTH",
            "runtime_id": runtime_id, "catalog_version": version["value"],
            "exact_build": exact_build, "installation_root": str(frozen_root),
            "readme_sha256": _sha_bytes(readme_bytes),
            "classpath_manifest_sha256": manifest_sha, "jar_count": jar_count,
            "jar_content_sha256": jar_sha, "server_birth_performed": False,
            "comsol_started_by_inspector": inspected["comsol_started"],
            "license_checked_out_by_inspector": inspected["license_checked_out"],
        }

    def bind_reviewed_project(self, *, project_id: str, workspace: Path,
                              authorized_root: Path, candidate_sha256: str,
                              activity_id: str, authorization_ref: str) -> None:
        if self._project_id is not None or self._started:
            raise CandidateError("W24 owned-server adapter project binding is immutable and single-use")
        if (not isinstance(project_id, str) or not project_id or
                not re.fullmatch(r"[0-9a-f-]{36}", project_id) or
                not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256) or
                not isinstance(activity_id, str) or not activity_id or
                not isinstance(authorization_ref, str) or
                not authorization_ref.startswith(f"w24-owned-server:{activity_id}:{candidate_sha256}:") or
                len(authorization_ref) > 512):
            raise CandidateError("W24 owned-server adapter is not bound to a reviewed project/candidate")
        if self.installation_binding is None:
            raise CandidateError("W24 owned-server adapter lacks the fresh frozen installation identity")
        root = authorized_root.resolve(strict=True)
        target = workspace.resolve(strict=True)
        if workspace.is_symlink() or not target.is_relative_to(root) or target == root:
            raise CandidateError("W24 owned-server host_control scope is not the exact new private project")
        self._project_id, self._workspace = project_id, target
        self._candidate_sha256, self._activity_id = candidate_sha256, activity_id
        self.authorization_ref = authorization_ref

    def start(self, runtime: Any, project_id: str, session_id: str, *,
              observation_sink: Callable[[Mapping[str, Any]], None] | None = None,
              process_factory: Callable[..., Any] | None = None) -> Any:
        from comsol_mcp._session_context import runtime_state_root, session_state_directory
        from comsol_mcp._session_server import OwnedServerError

        if (sys.platform != "darwin" or not self._project_id or
                project_id != self._project_id or self._started):
            raise CandidateError("W24 session.start refused an unreviewed project, platform, runtime, or repeat")
        if process_factory is not None:
            raise CandidateError("W24 launcher does not accept caller-supplied process factories")
        if self._bound_mcp_home is None:
            raise CandidateError("W24 launcher is not bound to the exact private ControlDaemon home")
        prebirth_identity = self._verify_frozen_installation_identity(runtime)
        expected_state_root = runtime_state_root(self._bound_mcp_home, platform_name="darwin")
        session_root = session_state_directory(runtime.session_state_root, project_id, session_id)
        owned_root = session_root / "owned-server"
        if runtime.session_state_root.resolve(strict=True) != expected_state_root.resolve(strict=True):
            raise CandidateError("W24 session runtime state root differs from its bound private ControlDaemon")
        _write_new(self.evidence / "server_prebirth_installation_identity.json", {
            **prebirth_identity, "project_id": project_id, "session_id": session_id,
            "candidate_sha256": self._candidate_sha256, "activity_id": self._activity_id,
        })
        factory = W24FixedDarwinSetupProcessFactory(
            install_root=self.install_root, owned_server_root=owned_root,
            evidence=self.evidence, popen=self._popen, server_np=self.server_np)
        key = (project_id, session_id)
        self._started = True
        self._factories[key] = factory

        def combined_observation(payload: Mapping[str, Any]) -> None:
            if observation_sink is not None:
                observation_sink(payload)
            if (payload.get("event") == "BIRTH_OBSERVED" and
                    self.on_birth_observation is not None):
                self.on_birth_observation(payload)

        try:
            handle = self._core.start(runtime, project_id, session_id,
                                      observation_sink=combined_observation,
                                      process_factory=factory)
        except BaseException as exc:
            handle = getattr(exc, "handle", None)
            if handle is not None:
                self._handles[key] = handle
                try:
                    self.observer.bind_managed_handle(self, project_id, session_id, handle)
                except BaseException:
                    raise OwnedServerError("W24 observer binding failed after Server birth; exact Core handle retained",
                                           handle=handle, uncertain=True) from None
            raise
        self._handles[key] = handle
        try:
            self.observer.bind_managed_handle(self, project_id, session_id, handle)
        except BaseException:
            raise OwnedServerError("W24 observer binding failed after ready; exact Core handle retained",
                                   handle=handle, uncertain=True) from None
        return handle

    @property
    def _core_home(self) -> Path | None:
        return getattr(self, "_bound_mcp_home", None)

    def bind_control_home(self, home: Path) -> None:
        if self._started:
            raise CandidateError("W24 ControlDaemon home must bind before session.start")
        self._bound_mcp_home = home.resolve(strict=False)

    def verify(self, handle: Any) -> Any:
        from comsol_mcp._session_server import OwnedServerError

        exact = [(key, value) for key, value in self._handles.items() if value is handle]
        if len(exact) != 1 or self.observer.managed_handle is not handle:
            raise OwnedServerError("W24 verify requires the exact original ManagedServerHandle", handle=handle, uncertain=True)
        identity = self._core.verify(handle)
        self.observer.verify_managed_server_identity()
        return identity

    def stop(self, handle: Any, *, timeout_s: float = 30.0) -> dict[str, Any]:
        from comsol_mcp._session_server import ManagedServerHandle, OwnedServerError

        exact = [(key, value) for key, value in self._handles.items() if value is handle]
        if (len(exact) != 1 or not isinstance(handle, ManagedServerHandle) or handle.retired or
                self.observer.managed_handle is not handle):
            raise OwnedServerError("W24 stop requires the exact original live ManagedServerHandle", handle=handle, uncertain=True)
        identity = self._core.verify(handle)
        before = self.observer.verify_managed_server_identity()
        process = handle.process
        try:
            process.terminate()
            returncode = process.wait(timeout=min(max(float(timeout_s), 0.1), 30.0))
        except subprocess.TimeoutExpired as exc:
            raise OwnedServerError("W24 Server did not exit after one SIGTERM; no SIGKILL fallback", handle=handle, uncertain=True) from exc
        except BaseException as exc:
            raise OwnedServerError("W24 exact Popen SIGTERM/reap failed; original owner retained", handle=handle, uncertain=True) from exc
        if process.poll() is None or type(returncode) is not int:
            raise OwnedServerError("W24 Server Popen terminal status is unconfirmed", handle=handle, uncertain=True)
        endpoint = handle.endpoint
        listeners = list(self._core.listener_reader(endpoint.port)) if endpoint is not None else None
        if listeners:
            raise OwnedServerError("W24 loopback listener remains after the original Popen exited", handle=handle, uncertain=True)
        handle.retired = True
        self._core._close_log(handle)
        return {"status": "W24_EXACT_OWNED_SERVER_SIGTERM_REAPED",
                "pid": identity.pid, "birth": identity.birth,
                "start_epoch_ms": identity.start_epoch_ms,
                "postexec_identity_before_term": before,
                "signal": "SIGTERM", "sigkill_used": False,
                "returncode": returncode, "child_reaped": True,
                "listener_absent": True, "server_stopped": True}


class W24StaticShapeSetupServer:
    """W24 read-only adapter over ControlDaemon's exact ManagedServerHandle."""

    def __init__(self, work: Path, evidence: Path, *, event_log: Path,
                 jdk: Path = JAVA11):
        self.work = work
        self.evidence = evidence
        self.event_log = event_log
        self.shadow_root = work / "unbound-owned-server-installation"
        self.runtime = work / "unbound-owned-server-runtime"
        self.prefs = self.runtime / "preferences"
        self.tmp = self.runtime / "tmp"
        self.recovery = self.runtime / "recovery"
        self.project = work / "project"
        self.worker_state = work / "worker-state"
        self.receipt_path = work / "isolation_receipt.json"
        self.server_log = work / "unbound-owned-server.log"
        self.proc = None
        self.port: int | None = None
        self.process_identity: dict[str, Any] | None = None
        self.birth_monotonic: float | None = None
        self.server_snapshot: dict[str, Any] | None = None
        self.server_postexec_identity: dict[str, Any] | None = None
        self._server_java_text_observation: dict[str, Any] | None = None
        self._private_ini_receipt: dict[str, Any] | None = None
        self._server_log_handle = None
        self._original_server_popen = None
        self._original_server_pid = None
        self._server_birth_attempted = False
        self.launch_command: list[str] | None = None
        self.java_executable_identity: dict[str, Any] | None = None
        self.managed_handle = None
        self.managed_project_id: str | None = None
        self.managed_session_id: str | None = None
        self.launcher: W24SetupOwnedServerLauncher | None = None
        self.jdk = jdk

    def prepare_shadow(self) -> dict[str, Any]:
        if sys.platform != "darwin":
            raise CandidateError("W24 setup is frozen for Darwin only")
        if not INSTALL_ROOT.is_dir() or not self.jdk.is_dir():
            raise CandidateError("the pinned COMSOL 6.4 tree or Java 11 tools are unavailable")
        if self.project.exists() or self.project.is_symlink():
            raise CandidateError("W24 project root must be a new task-private directory")
        self.project.mkdir(mode=0o700, parents=False, exist_ok=False)
        return {"status": "CORE_OWNED_SERVER_SHADOW_WILL_BE_CREATED_BY_SESSION_START",
                "project_root": str(self.project.resolve(strict=True)),
                "independent_server_shadow_created": False,
                "installed_tree_mutated": False}

    def bind_managed_handle(self, launcher: W24SetupOwnedServerLauncher,
                            project_id: str, session_id: str, handle: Any) -> None:
        from comsol_mcp._session_server import ManagedServerHandle
        key = (project_id, session_id)
        if (not isinstance(handle, ManagedServerHandle) or
                launcher._handles.get(key) is not handle or
                self.managed_handle is not None and self.managed_handle is not handle):
            raise CandidateError("W24 observer must bind the exact Core-registered ManagedServerHandle")
        if handle.process is None or type(getattr(handle.process, "pid", None)) is not int:
            raise CandidateError("Core ManagedServerHandle lacks its original Popen object")
        self.launcher = launcher
        self.managed_handle = handle
        self.managed_project_id = project_id
        self.managed_session_id = session_id
        self.proc = handle.process
        self._original_server_popen = handle.process
        self._original_server_pid = handle.process.pid
        self.port = handle.endpoint.port if handle.endpoint is not None else None
        self.process_identity = None
        if handle.endpoint is not None and handle.process_identity is not None:
            self.process_identity = {
                "pid": handle.process_identity.pid,
                "birth": handle.process_identity.birth,
                "start_epoch_ms": handle.start_epoch_ms,
                "endpoint": {"host": handle.endpoint.address, "port": handle.endpoint.port},
                "runtime_id": handle.runtime_id,
            }
        self.server_snapshot = {
            "pid": handle.process_identity.pid if handle.process_identity is not None else handle.process.pid,
            "birth": handle.process_identity.birth if handle.process_identity is not None else None,
            "start_epoch_ms": handle.start_epoch_ms,
            "uid": os.getuid(),
        }
        self.shadow_root = handle.directories.private_installation
        self.runtime = handle.directories.runtime
        self.prefs = handle.directories.preferences
        self.tmp = handle.directories.temporary
        self.recovery = handle.directories.recovery
        self.server_log = handle.directories.log_file
        self._server_log_handle = handle.log_handle
        factory = launcher._factories.get(key)
        self.launch_command = (list(factory.launch_command)
                               if factory is not None and isinstance(factory.launch_command, list)
                               else None)
        self._server_birth_attempted = True
        self.birth_monotonic = self.birth_monotonic or time.monotonic()

    def bind_from_original_daemon(self, daemon: Any, project_id: str,
                                  *, request_id: str, idempotency_key: str) -> str | None:
        """Resolve the durable start identity or retain the sole exact Core handle."""
        jobs = None
        try:
            jobs_result = daemon.store.list_jobs(project_id=project_id, limit=1000)
            jobs = jobs_result if isinstance(jobs_result, list) else getattr(jobs_result, "items", None)
        except BaseException:
            jobs = None
        matches = []
        if isinstance(jobs, list):
            for job in jobs:
                operation = job.get("operation") if isinstance(job, Mapping) else None
                if (not isinstance(operation, Mapping) or operation.get("operation") != "session.start" or
                        operation.get("request_id") != request_id or
                        operation.get("idempotency_key") != idempotency_key):
                    continue
                metadata = operation.get("metadata")
                job_metadata = job.get("metadata")
                session_id = (metadata.get("session_id") if isinstance(metadata, Mapping) else None)
                if not isinstance(session_id, str) and isinstance(job_metadata, Mapping):
                    session_id = job_metadata.get("session_id")
                matches.append(session_id)
        if len(matches) == 1 and isinstance(matches[0], str) and matches[0]:
            session_id = matches[0]
            key = (project_id, session_id)
            handle = daemon._session_server_handles.get(key)
            if handle is not None:
                if self.launcher is None or self.launcher._handles.get(key) is not handle:
                    raise CandidateError("ControlDaemon handle differs from the original W24 launcher owner")
                self.bind_managed_handle(self.launcher, project_id, session_id, handle)
                self.owner_binding_resolution = "EXACT_DURABLE_SESSION_START_OPERATION"
            return session_id

        # This daemon and adapter are fresh and permit exactly one session.start.
        # If durable lookup itself is unavailable or its response was lost,
        # retain only the sole object registered by both original owners.
        daemon_handles = getattr(daemon, "_session_server_handles", {})
        launcher_handles = getattr(self.launcher, "_handles", {}) if self.launcher is not None else {}
        daemon_keys = [key for key in daemon_handles if isinstance(key, tuple) and key[0] == project_id]
        launcher_keys = [key for key in launcher_handles if isinstance(key, tuple) and key[0] == project_id]
        if (self.launcher is None or getattr(self.launcher, "_started", False) is not True or
                self.launcher._project_id != project_id or len(daemon_keys) != 1 or
                len(launcher_keys) != 1 or daemon_keys != launcher_keys):
            raise CandidateError("original session.start identity does not resolve to one exact Core owner")
        key = daemon_keys[0]
        handle = daemon_handles[key]
        if launcher_handles.get(key) is not handle:
            raise CandidateError("same-daemon Core and W24 launcher handle objects differ")
        self.bind_managed_handle(self.launcher, project_id, str(key[1]), handle)
        self.owner_binding_resolution = "SOLE_CORE_HANDLE_IN_FRESH_W24_DAEMON"
        return str(key[1])

    def _observe_server_identity(self) -> tuple[dict[str, Any], dict[str, Any]]:
        from comsol_mcp._g2_isolation import _process_snapshot
        from comsol_mcp._platform_process import process_identity

        handle = self.managed_handle
        if (handle is None or self.launcher is None or self.proc is not self._original_server_popen or
                self.proc is not handle.process or self.proc.pid != self._original_server_pid or
                self.proc.poll() is not None or self.port is None or
                handle.endpoint is None or handle.process_identity is None or
                handle.endpoint.port != self.port):
            raise CandidateError("W24 identity check lacks the same live Core ManagedServerHandle/Popen")
        key = (self.managed_project_id, self.managed_session_id)
        if key not in self.launcher._handles or self.launcher._handles[key] is not handle:
            raise CandidateError("W24 launcher registry no longer contains the original Core handle")
        snapshot = _process_snapshot(self.proc.pid)
        epoch = process_identity(self.proc.pid)
        if (not isinstance(snapshot, Mapping) or snapshot.get("pid") != self.proc.pid or
                not isinstance(epoch, Mapping) or epoch.get("alive") is not True or
                type(epoch.get("start_epoch_ms")) is not int or
                epoch["start_epoch_ms"] != handle.start_epoch_ms or
                epoch["start_epoch_ms"] != handle.process_identity.start_epoch_ms):
            raise CandidateError("fresh OS process identity differs from the retained Core handle birth")
        birth_domains = _validate_server_birth_domains(
            pid=self.proc.pid, snapshot=snapshot, live_identity=epoch,
            core_birth=handle.process_identity.birth,
            core_start_epoch_ms=handle.start_epoch_ms)
        if (not isinstance(snapshot.get("command"), str) or
                _sha_bytes(snapshot["command"].encode()) != snapshot.get("command_sha256")):
            raise CandidateError("fresh server command hash does not bind its observed argv")
        uid = _read_process_uid(self.proc.pid)
        if uid != os.getuid():
            raise CandidateError("live Core-owned COMSOL process UID differs from the task user")
        private_root = handle.directories.private_installation.resolve(strict=True)
        installed_java = (private_root / "java/macarm64/jre/Contents/Home/bin/java").resolve(strict=True)
        if not installed_java.is_relative_to(INSTALL_ROOT.resolve(strict=True)):
            raise CandidateError("server Java resolved outside the pinned COMSOL installation")
        java_identity = _canonical_executable_identity(installed_java)
        java_query = _read_text_executable_identity(self.proc.pid, installed_java)
        if {k: java_query.get(k) for k in ("path", "device", "inode", "sha256")} != java_identity:
            raise CandidateError("live mapped Java executable differs from the pinned private-install Java")
        listener_query = _lsof_listener(self.proc.pid, self.port)
        expected_listener = {"pid": self.proc.pid, "endpoint": f"127.0.0.1:{self.port}"}
        if (listener_query.get("exit_code") != 0 or listener_query.get("stderr_sha256") != _sha_bytes(b"") or
                listener_query.get("rows") != [expected_listener] or
                list(self.launch_command or []) != list(getattr(self.proc, "args", []))):
            raise CandidateError("exact Popen argv or loopback listener differs from the fixed W24 owner")
        argv = shlex.split(snapshot["command"])
        launcher_path = str((private_root / "bin/comsol").resolve(strict=True))
        allowed_argv0 = [launcher_path, java_identity["path"]]
        if not argv or argv[0] not in allowed_argv0:
            raise CandidateError("server argv0 is neither the exact private launcher nor pinned Java label")
        required_flags = ("-XX:ActiveProcessorCount=2", "-XX:ParallelGCThreads=2",
                          "-XX:CICompilerCount=2", "-Xms128m", "-Xmx1024m")
        if any(argv.count(flag) != 1 for flag in required_flags):
            raise CandidateError("server Java argv lacks exactly one reviewed CPU/heap limit set")
        if (sum(token.startswith("-Xms") for token in argv) != 1 or
                sum(token.startswith("-Xmx") for token in argv) != 1 or
                sum(token.startswith("-XX:ActiveProcessorCount") for token in argv) != 1 or
                sum(token.startswith("-XX:ParallelGCThreads") for token in argv) != 1 or
                sum(token.startswith("-XX:CICompilerCount") for token in argv) != 1):
            raise CandidateError("server Java argv contains a conflicting CPU/heap limit")
        allowed_xx = set(required_flags[:3]) | {"-XX:MaxMetaspaceSize=1g", "-XX:-SplitIfBlocks"}
        if any(token.startswith("-XX:") and token not in allowed_xx for token in argv):
            raise CandidateError("server Java argv contains an unapproved XX option")
        expected_paths = {
            "comsol_root": str(private_root),
            "tomcat_root": str(private_root / "bin/tomcat"),
            "port_file": str(handle.directories.port_file),
            "prefs_dir": str(handle.directories.preferences),
            "tmp_dir": str(handle.directories.temporary),
            "recovery_dir": str(handle.directories.recovery),
        }
        for key, value in (("-Dcs.root", expected_paths["comsol_root"]),
                           ("-Dcatalina.home", expected_paths["tomcat_root"]),
                           ("-Djava.io.tmpdir", expected_paths["tmp_dir"])):
            if argv.count(f"{key}={value}") != 1:
                raise CandidateError(f"server Java argv does not bind exact {key} path")
        for flag, value in (("-np", "2"), ("-port", "0"),
                            ("-portfile", expected_paths["port_file"]),
                            ("-prefsdir", expected_paths["prefs_dir"]),
                            ("-tmpdir", expected_paths["tmp_dir"]),
                            ("-recoverydir", expected_paths["recovery_dir"]),
                            ("-login", "auto"), ("-multi", "on")):
            positions = [i for i, token in enumerate(argv) if token == flag]
            if len(positions) != 1 or positions[0] + 1 >= len(argv) or argv[positions[0] + 1] != value:
                raise CandidateError(f"server Java argv does not bind exact {flag} argument")
        if argv.count("-silent") != 1 or argv.count("com.comsol.util.application.ServerApplication") != 1:
            raise CandidateError("server Java argv lacks the fixed silent COMSOL server entrypoint")
        launch_hash = _json_hash(self.launch_command)
        current = {
            "status": "CORE_MANAGED_ORIGINAL_POPEN_JAVA_IDENTITY_FROZEN",
            "pid": self.proc.pid, "uid": uid, **birth_domains,
            "launch_command_sha256": launch_hash,
            "post_exec_command_sha256": snapshot["command_sha256"],
            "post_exec_argv": argv,
            "argv0_display": argv[0],
            "allowed_argv0_labels": allowed_argv0,
            "argv0_role": "PRIVATE_COMSOL_LAUNCHER_DISPLAY" if argv[0] == launcher_path else "PINNED_JAVA_DISPLAY",
            "java_executable": java_query,
            "listener": expected_listener,
            "launch_command": list(self.launch_command),
            "server_paths": expected_paths,
        }
        detail = {"process_snapshot": dict(snapshot), "process_identity": dict(epoch),
                  "process_uid": uid, "java_text_executable": java_query,
                  "listener_query": listener_query}
        return current, detail

    def verify_managed_server_identity(self) -> dict[str, Any]:
        current, detail = self._observe_server_identity()
        if self.server_postexec_identity is None:
            self.server_postexec_identity = current
            self.server_snapshot = {
                "pid": current["pid"], "birth": current["birth"],
                "start_epoch_ms": current["start_epoch_ms"],
                "process_snapshot_birth": current["process_snapshot_birth"],
                "uid": current["uid"],
                "launch_command_sha256": current["launch_command_sha256"],
            }
            self.java_executable_identity = dict(current["java_executable"])
            self._server_java_text_observation = dict(current["java_executable"])
            self.process_identity = {**current, "port": self.port}
        else:
            _validate_frozen_server_identity(
                self.server_postexec_identity, current,
                proc=self.proc, original_proc=self._original_server_popen)
        return {"identity": current, "detail": detail}

    def start_and_verify_listener(self) -> dict[str, Any]:
        if self.managed_handle is None or self.launcher is None:
            raise CandidateError("session.start did not return the original ready ManagedServerHandle")
        verified = self.verify_managed_server_identity()
        identity = verified["identity"]
        listener = {"status": "CORE_OWNED_LOOPBACK_AND_W24_POSTEXEC_IDENTITY_VERIFIED",
                    "pid": self.proc.pid, "port": self.port,
                    "endpoint": f"127.0.0.1:{self.port}",
                    "process_identity": identity,
                    "post_exec_observation": verified["detail"]}
        _write_new(self.evidence / "pre_worker_listener.json", listener)
        _append(self.event_log, {"event": "server_birth_listener_confirmed",
                                 "pid": self.proc.pid, "port": self.port,
                                 "birth": identity["birth"],
                                 "process_snapshot_birth": identity["process_snapshot_birth"],
                                 "launch_command_sha256": identity["launch_command_sha256"],
                                 "post_exec_command_sha256": identity["post_exec_command_sha256"]})
        return listener

    def require_server_java_options(self) -> dict[str, Any]:
        if not isinstance(self.server_postexec_identity, Mapping):
            raise CandidateError("effective JVM check lacks the frozen Core-handle post-exec identity")
        payload = self.server_log.read_bytes()
        text = payload.decode("utf-8", "replace")
        matched = f"Picked up JAVA_TOOL_OPTIONS: {JAVA_TOOL_OPTIONS}" in text
        echo = {"status": "JAVA_TOOL_OPTIONS_ECHO_PRESENT_DIAGNOSTIC" if matched else "JAVA_TOOL_OPTIONS_ECHO_ABSENT_DIAGNOSTIC",
                "path": str(self.server_log), "sha256": _sha_bytes(payload),
                "bytes": len(payload), "required_options": JAVA_TOOL_OPTIONS,
                "observed": matched}
        verified = self.verify_managed_server_identity()
        limits = _read_effective_jvm_limits(
            pid=self.proc.pid, jcmd=self.jdk / "bin/jcmd",
            identity_reader=lambda: self.verify_managed_server_identity()["identity"],
            expected_identity=self.server_postexec_identity,
            evidence_path=self.evidence / "server_jvm_limits_readback.json")
        evidence = {"status": "SERVER_EFFECTIVE_JVM_LIMITS_VERIFIED",
                    "log_echo_diagnostic": echo,
                    "identity_probe_before_jcmd": verified["detail"],
                    "effective_jvm_limits": limits}
        _write_new(self.evidence / "server_java_options_readback.json", evidence)
        return evidence

    def write_g2_isolation_receipt(self) -> dict[str, Any]:
        """Write and immediately verify the exact G2 receipt before Worker birth."""
        from comsol_mcp._g2_isolation import verify_owned_server

        if (self.managed_handle is None or self.launcher is None or
                self.managed_project_id is None or self.managed_session_id is None or
                self.server_postexec_identity is None or self.port is None):
            raise CandidateError("G2 receipt requires the exact reviewed Core owner and frozen server identity")
        expected_path = (self.work / "isolation_receipt.json").resolve(strict=False)
        configured = os.environ.get("COMSOL_MCP_ISOLATION_RECEIPT")
        if (self.receipt_path.resolve(strict=False) != expected_path or
                not isinstance(configured, str) or
                Path(configured).resolve(strict=False) != expected_path or
                Path(configured).is_symlink() or self.receipt_path.exists() or
                self.receipt_path.is_symlink()):
            raise CandidateError("G2 receipt path is not the one fresh reviewed task-private configured path")
        verified = self.verify_managed_server_identity()
        identity = verified["identity"]
        detail = verified["detail"]
        snapshot = detail.get("process_snapshot")
        if not isinstance(snapshot, Mapping):
            raise CandidateError("G2 receipt lacks the current verified OS process snapshot")
        record = _build_g2_owned_server_receipt(
            handle=self.managed_handle, observer_identity=identity,
            process_snapshot=snapshot, project_id=self.managed_project_id,
            session_id=self.managed_session_id,
            activity_id=str(self.launcher._activity_id or ""),
            candidate_sha256=str(self.launcher._candidate_sha256 or ""))
        digest = _write_new(self.receipt_path, record)
        endpoint = f"127.0.0.1:{self.port}"
        proof = verify_owned_server(self.receipt_path, endpoint=endpoint)
        if not isinstance(proof, Mapping) or proof.get("verified") is not True:
            raise CandidateError("production G2 verifier did not confirm the new pre-Worker receipt")
        result = {"status": "W24_G2_OWNED_SERVER_RECEIPT_VERIFIED_BEFORE_WORKER",
                  "path": str(self.receipt_path.resolve(strict=True)),
                  "sha256": digest, "endpoint": endpoint,
                  "record": record, "production_consumer_proof": proof,
                  "worker_born": False}
        _write_new(self.evidence / "g2_isolation_receipt_readback.json", result)
        _append(self.event_log, {"event": "g2_server_isolation_receipt_verified",
                                 "pid": self.proc.pid, "port": self.port,
                                 "receipt_sha256": digest,
                                 "worker_born": False})
        return result


class BudgetedSetupDaemon:
    """Expose the real ControlDaemon/OperationStore behind a setup-only route fence."""

    def __init__(self, daemon: Any, *, monitor: SampledRssMonitor,
                 deadline_monotonic: float, events_path: Path,
                 workspace: Path, project_id: str, native_epoch_capability: Any = None,
                 schema_probe_only: bool = False,
                 selection_diagnostic_dir: Path | None = None):
        self.native_epoch_capability = native_epoch_capability
        if native_epoch_capability is not None:
            from tools.w24_native_epoch_capability import require_native_epoch_capability
            require_native_epoch_capability(native_epoch_capability)
        self._daemon = daemon
        self.monitor = monitor
        self.deadline_monotonic = deadline_monotonic
        self.events_path = events_path
        self.workspace = workspace.resolve(strict=True)
        self.project_id = project_id
        self.selection_diagnostic_dir = (Path(selection_diagnostic_dir)
            if selection_diagnostic_dir is not None else None)
        if self.selection_diagnostic_dir is not None and (
                not self.selection_diagnostic_dir.is_absolute() or
                self.selection_diagnostic_dir.resolve(strict=False) != self.selection_diagnostic_dir or
                self.selection_diagnostic_dir.name != "static_shape_managed"):
            raise CandidateError("selection diagnostics require the canonical current-run static_shape_managed directory")
        self.selection_diagnostic_paths: set[Path] = set()
        self.slot: tuple[str, str] | None = None
        self.slot_stage: str | None = None
        self.slot_model_tag: str | None = None
        self.slot_artifact: dict[str, Any] | None = None
        self.slot_adopt_binding: dict[str, Any] | None = None
        self.slot_load_binding: dict[str, Any] | None = None
        self.completed_slots: set[tuple[str, str]] = set()
        self.unknown = False
        self.bootstrap_model_create_used = False
        if type(schema_probe_only) is not bool:
            raise CandidateError("schema_probe_only mode must be an exact Boolean")
        self.schema_probe_only = schema_probe_only
        self.schema_probe_completed = False
        self.schema_probe_result: dict[str, Any] | None = None

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
        self.slot_adopt_binding = None
        self.slot_load_binding = None

    def clear_slot(self) -> None:
        if self.slot is not None and self.slot_stage == "readback_complete":
            self.completed_slots.add(self.slot)
        self.slot = None
        self.slot_stage = None
        self.slot_model_tag = None
        self.slot_artifact = None
        self.slot_adopt_binding = None
        self.slot_load_binding = None

    def _remaining_rpc(self) -> float:
        return _remaining_admission_rpc(self.deadline_monotonic, float(RPC_CAP_SECONDS))

    def _reserve_selection_diagnostic_path(self, value: Any, *, configuration_id: str,
                                           case_id: str) -> Path:
        directory = self.selection_diagnostic_dir
        if directory is None or not isinstance(value, str) or not value:
            raise CandidateError("fixture build requires its private selection diagnostic path")
        path = Path(value)
        if (not path.is_absolute() or str(path) != value or
                path.resolve(strict=False) != path or path.parent != directory):
            raise CandidateError("selection diagnostic path must be canonical under the current run's private directory")
        try:
            directory_stat = directory.lstat()
        except OSError as exc:
            raise CandidateError("current run selection diagnostic directory is unavailable") from exc
        if (stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode) or
                directory_stat.st_uid != os.getuid() or stat.S_IMODE(directory_stat.st_mode) != 0o700 or
                directory.resolve(strict=True) != directory):
            raise CandidateError("current run selection diagnostic directory must be canonical owner-private mode 0700")
        expected_name = re.fullmatch(
            rf"w24_selection_stage_{re.escape(configuration_id)}_{re.escape(case_id)}_[0-9a-f]{{32}}[.]jsonl",
            path.name)
        if expected_name is None:
            raise CandidateError("selection diagnostic basename must bind the active configuration, case, and UUID")
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise CandidateError("selection diagnostic target must be absent and non-aliased before dispatch")
        if path in self.selection_diagnostic_paths:
            raise CandidateError("selection diagnostic path cannot be reused across setup slots")
        self.selection_diagnostic_paths.add(path)
        return path

    def _validate_operation(self, operation: Any, arguments: Any) -> None:
        if self.unknown:
            raise CandidateError("an earlier setup call is UNKNOWN; no new route may be dispatched")
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
            raise CandidateError("legacy model_load may adopt an already-open path and cannot prove disk reopen; use registered artifact model.load")
        if operation == "model.load":
            if (self.slot is None or self.slot_stage != "registered" or
                    not isinstance(self.slot_artifact, Mapping) or
                    dict(arguments) != {"artifact_id": self.slot_artifact.get("artifact_id")} or
                    not isinstance(self.slot_artifact.get("artifact_id"), str)):
                raise CandidateError("model.load requires the exact registered artifact from the active saved slot")
            return
        if operation == "operation_call":
            if self.schema_probe_only:
                body = arguments.get("arguments")
                if (self.slot is not None or self.schema_probe_completed or
                        arguments.get("operation_id") != "code.execute_java" or
                        not isinstance(body, Mapping) or set(body) != {"source_artifact", "entrypoint", "arguments", "mode"} or
                        body.get("source_artifact") != "W24StaticShapeFixture.java" or
                        body.get("entrypoint") != "W24StaticShapeFixture#run" or
                        body.get("mode") != "trusted" or
                        body.get("arguments") != {"action": "property_schema_only"}):
                    raise CandidateError("schema-only mode permits exactly one trusted property_schema_only call on its reviewed fixture")
                return
            if self.slot is None:
                raise CandidateError("setup operation_call is permitted only inside an active slot")
            if arguments.get("operation_id") == "artifact.register":
                body = arguments.get("arguments")
                if not isinstance(body, Mapping) or self.slot_stage != "saved":
                    raise CandidateError("artifact.register is permitted only for the just-saved active-slot MPH")
                artifact = self.slot_artifact
                if not isinstance(artifact, Mapping):
                    raise CandidateError("artifact.register has no exact saved-slot artifact binding")
                expected_relative = Path(artifact["path"]).relative_to(self.workspace).as_posix()
                expected = {
                    "project_id": self.project_id,
                    "path": expected_relative,
                    "role": "saved_model",
                    "classification": "project_local",
                    "artifact_type": "application/vnd.comsol.mph",
                    "format_version": "comsol-mph-savecopy/1",
                }
                if (set(body) != set(expected) | {"idempotency_key", "request_id"} or
                        any(body.get(key) != value for key, value in expected.items()) or
                        not isinstance(body.get("idempotency_key"), str) or not body["idempotency_key"] or
                        not isinstance(body.get("request_id"), str) or not body["request_id"] or
                        _sha_file(Path(artifact["path"])) != artifact.get("sha256")):
                    raise CandidateError("artifact.register differs from the exact saved-slot path, format, hash, or project")
                return
            if arguments.get("operation_id") != "code.execute_java":
                raise CandidateError("only reviewed Java setup/readback or exact artifact registration calls are permitted inside a slot")
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
                        set(nested) != set(expected) | {"selection_diagnostic_path"} or
                        any(nested.get(key) != value for key, value in expected.items())):
                    raise CandidateError("fixture build differs from the active preregistered slot and closed diagnostic contract")
                self._reserve_selection_diagnostic_path(
                    nested.get("selection_diagnostic_path"), configuration_id=cfg, case_id=case)
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

    def _terminal_proof_context_snapshot(self, execution: Mapping[str, Any]) -> dict[str, Any]:
        """Capture the exact registered session binding for a possible local proof.

        This is deliberately a read-only observation.  It is only consulted when
        one of the three frozen legacy model routes returns without a Worker
        envelope; ordinary Worker envelopes keep their existing contract.
        """
        project_id, session_id = execution.get("project_id"), execution.get("session_id")
        if (not isinstance(project_id, str) or not project_id
                or not isinstance(session_id, str) or not session_id):
            raise CandidateError("operation-store proof requires exact outer project/session identity")
        registry = self.session_registry
        context = registry.get(project_id, session_id)
        if (context.project_id != project_id or context.session_id != session_id
                or context.backend is None or context.service is None or context.worker is None
                or context.client is None or context.client_retired is True
                or context.client_connected is not True):
            raise CandidateError("operation-store proof requires a live exact registered Worker context")
        backend, service, worker, client = context.backend, context.service, context.worker, context.client
        if (getattr(backend, "service", None) is not service
                or getattr(backend, "worker", None) is not worker):
            raise CandidateError("registered session service/backend/Worker pointers do not agree")
        selected_backend = getattr(self._daemon, "_session_backends", {}).get((project_id, session_id))
        selected_worker = getattr(self._daemon, "_session_worker_handles", {}).get((project_id, session_id))
        if selected_backend is not backend or selected_worker is not worker:
            raise CandidateError("registered session backend or retained Worker handle changed")
        ledger = getattr(service, "ledger", None)
        endpoint = getattr(context, "endpoint", None)
        endpoint_peer = getattr(endpoint, "observed_peer", None)
        endpoint_host, endpoint_port = getattr(endpoint, "host", None), getattr(endpoint, "port", None)
        worker_epoch = getattr(context, "worker_epoch", None)
        endpoint_worker_epoch = getattr(endpoint, "worker_epoch", None)
        worker_instance_id = getattr(context, "worker_instance_id", None)
        server_instance_id = getattr(ledger, "server_instance_id", None)
        if (getattr(ledger, "session_id", None) != session_id
                or not isinstance(server_instance_id, str) or not server_instance_id
                or type(worker_epoch) is not int or worker_epoch < 1
                or type(endpoint_worker_epoch) is not int or endpoint_worker_epoch != worker_epoch
                or not isinstance(worker_instance_id, str) or not worker_instance_id
                or not isinstance(endpoint_host, str) or not endpoint_host
                or type(endpoint_port) is not int or not 1 <= endpoint_port <= 65535
                or endpoint_peer is None or getattr(endpoint_peer, "port", None) != endpoint_port):
            raise CandidateError("registered session endpoint, server, or Worker identity is incomplete")
        identity = getattr(backend, "worker_identity", None)
        endpoint_key = getattr(backend, "endpoint_key", None)
        expected_endpoint_key = (f"[{endpoint_host}]:{endpoint_port}" if ":" in endpoint_host
                                 else f"{endpoint_host}:{endpoint_port}")
        if (not isinstance(identity, Mapping)
                or identity.get("worker_instance_id") != worker_instance_id
                or type(identity.get("connection_epoch")) is not int
                or identity.get("connection_epoch") != worker_epoch
                or identity.get("server_instance_id") != server_instance_id
                or identity.get("endpoint") != expected_endpoint_key):
            raise CandidateError("backend Worker identity differs from the registered session")
        if endpoint_key != expected_endpoint_key:
            raise CandidateError("backend endpoint differs from the registered session endpoint")
        lifecycle = self._daemon.session_lifecycle.get(project_id, session_id)
        if (not isinstance(lifecycle, Mapping)
                or lifecycle.get("state") != "CONNECTED"
                or lifecycle.get("client_state") != "CONNECTED"
                or lifecycle.get("project_id") != project_id
                or lifecycle.get("session_id") != session_id
                or lifecycle.get("worker_instance_id") != worker_instance_id
                or type(lifecycle.get("worker_epoch")) is not int
                or lifecycle.get("worker_epoch") != worker_epoch
                or lifecycle.get("server_instance_id") != server_instance_id
                or lifecycle.get("endpoint") != {"host": endpoint_host, "port": endpoint_port}):
            raise CandidateError("persisted session lifecycle does not attest the live registered Worker")
        return {
            "context": context,
            "backend": backend,
            "service": service,
            "ledger": ledger,
            "worker": worker,
            "client": client,
            "project_id": project_id,
            "session_id": session_id,
            "worker_epoch": worker_epoch,
            "worker_instance_id": worker_instance_id,
            "server_instance_id": server_instance_id,
            "endpoint_host": endpoint_host,
            "endpoint_port": endpoint_port,
            "endpoint_peer": endpoint_peer,
            "endpoint_owned_process": getattr(endpoint, "owned_process", None),
            "endpoint": endpoint,
            "backend_endpoint_key": endpoint_key,
            "backend_worker_identity": dict(identity),
            "lifecycle": dict(lifecycle),
        }

    @staticmethod
    def _assert_same_terminal_proof_context(before: Mapping[str, Any],
                                            after: Mapping[str, Any]) -> None:
        """Require one registered service/Worker receiver to span the route."""
        for key in ("context", "backend", "service", "ledger", "worker", "client", "endpoint",
                    "endpoint_peer", "endpoint_owned_process"):
            if after.get(key) is not before.get(key):
                raise CandidateError(f"registered session {key} object changed across the route")
        stable_fields = (
            "project_id", "session_id", "worker_epoch", "worker_instance_id",
            "server_instance_id", "endpoint_host", "endpoint_port",
            "backend_endpoint_key", "backend_worker_identity", "lifecycle",
        )
        if any(after.get(key) != before.get(key) for key in stable_fields):
            raise CandidateError("registered session identity or lifecycle changed across the route")

    @staticmethod
    def _validate_requested_model_ref_context(execution: Mapping[str, Any],
                                              context: Mapping[str, Any]) -> None:
        """Reject an inspect ref that belongs to the default or another session ledger."""
        from comsol_mcp._execution_contract import model_ref_from_mapping
        ref_value = execution.get("model_ref")
        if not isinstance(ref_value, Mapping):
            raise CandidateError("model.inspect request omitted its exact registered-session ModelRef")
        try:
            model_ref = model_ref_from_mapping(dict(ref_value))
            state = context["ledger"]._state_for(model_ref)
        except Exception as exc:
            raise CandidateError("model.inspect ModelRef is not registered in the exact session ledger") from exc
        if (model_ref.session_id != context.get("session_id")
                or model_ref.server_instance_id != context.get("server_instance_id")
                or state.ref != model_ref):
            raise CandidateError("model.inspect ModelRef belongs to a foreign registered-session context")

    def _validate_registered_artifact(self, request: Mapping[str, Any],
                                      response: Mapping[str, Any]) -> dict[str, Any]:
        """Bind artifact.register to the exact saved slot and durable registered copy."""
        data = response.get("data")
        execution = response.get("execution")
        routed_arguments = request.get("arguments")
        outer = request.get("execution")
        nested = routed_arguments.get("arguments") if isinstance(routed_arguments, Mapping) else None
        artifact = self.slot_artifact
        if (self.slot is None or self.slot_stage != "saved"
                or not isinstance(artifact, Mapping)
                or not isinstance(data, Mapping) or not isinstance(execution, Mapping)
                or not isinstance(nested, Mapping) or not isinstance(outer, Mapping)
                or routed_arguments.get("operation_id") != "artifact.register"):
            raise CandidateError("artifact.register lacks its exact just-saved active-slot request/response")
        source_path = Path(str(artifact.get("path", "")))
        try:
            source_relative = source_path.resolve(strict=True).relative_to(self.workspace).as_posix()
        except (OSError, ValueError) as exc:
            raise CandidateError("artifact.register source escaped the registered project workspace") from exc
        source_digest = artifact.get("sha256")
        source_size = artifact.get("size_bytes")
        request_id = outer.get("request_id")
        idempotency_key = outer.get("idempotency_key")
        if (not isinstance(source_digest, str) or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
                or type(source_size) is not int or source_size <= 0
                or source_path.is_symlink() or source_path.stat().st_size != source_size
                or _sha_file(source_path) != source_digest):
            raise CandidateError("artifact.register source no longer matches the saved Java MPH witness")
        expected_body = {
            "project_id": self.project_id, "path": source_relative,
            "role": "saved_model", "classification": "project_local",
            "artifact_type": "application/vnd.comsol.mph",
            "format_version": "comsol-mph-savecopy/1",
            "idempotency_key": idempotency_key, "request_id": request_id,
        }
        if (dict(nested) != expected_body
                or outer.get("project_id") != self.project_id
                or outer.get("session_id") != self._terminal_proof_context_snapshot(outer).get("session_id")
                or execution.get("project_id") != self.project_id
                or execution.get("request_id") != request_id
                or execution.get("idempotency_key") != idempotency_key
                or not isinstance(request_id, str) or not request_id
                or not isinstance(idempotency_key, str) or not idempotency_key):
            raise CandidateError("artifact.register identity or exact saved-slot request differs from its registered project")
        artifact_id = data.get("artifact_id")
        expected_relative = f"g2_artifacts/registered/{source_digest}.mph"
        registered_relative = data.get("path")
        try:
            registered_path = (self.workspace / str(registered_relative)).resolve(strict=True)
            registered_path.relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise CandidateError("artifact.register returned a missing or out-of-project registered MPH") from exc
        if (artifact_id != source_digest or data.get("sha256") != source_digest
                or data.get("size") != source_size or registered_relative != expected_relative
                or registered_path.is_symlink() or not registered_path.is_file()
                or registered_path.stat().st_size != source_size
                or _sha_file(registered_path) != source_digest
                or data.get("role") != "saved_model"
                or data.get("classification") != "project_local"
                or data.get("artifact_type") != "application/vnd.comsol.mph"
                or data.get("format_version") != "comsol-mph-savecopy/1"
                or data.get("host_scope") != "local_loopback_engine_and_current_project"):
            raise CandidateError("artifact.register response does not bind the exact hash-verified registered MPH")
        provenance = data.get("provenance")
        if (not isinstance(provenance, Mapping)
                or provenance.get("source_project_relative_path") != source_relative
                or provenance.get("source_sha256") != source_digest
                or provenance.get("request_id") != request_id
                or provenance.get("registering_operation_id") != execution.get("operation_id")):
            raise CandidateError("artifact.register provenance does not bind this exact saved file and public operation")
        metadata = self.store.get_metadata("artifacts", artifact_id)
        required_metadata = {
            "artifact_id": artifact_id, "sha256": source_digest,
            "size": source_size, "path": expected_relative,
            "project_id": self.project_id, "role": "saved_model",
            "classification": "project_local",
            "artifact_type": "application/vnd.comsol.mph",
            "format_version": "comsol-mph-savecopy/1",
            "provenance": dict(provenance),
        }
        if not isinstance(metadata, Mapping) or any(metadata.get(key) != value
                                                    for key, value in required_metadata.items()):
            raise CandidateError("artifact.register durable metadata differs from the exact public registration response")
        return {
            **dict(artifact), "artifact_id": artifact_id,
            "registered_relative_path": expected_relative,
            "registered_path": str(registered_path),
            "registered_size_bytes": source_size,
            "registered_sha256": source_digest,
            "registration_provenance": dict(provenance),
            "registration_operation_id": execution.get("operation_id"),
            "registration_job_id": execution.get("job_id"),
            "registration_request_id": request_id,
            "registration_idempotency_key": idempotency_key,
        }

    def _validate_managed_artifact_load_binding(self, request: Mapping[str, Any],
                                                response: Mapping[str, Any],
                                                context_before: Mapping[str, Any]) -> dict[str, Any]:
        """Require model.load to create one new MCP-owned ModelRef from the registered MPH."""
        data = response.get("data")
        execution = response.get("execution")
        request_arguments = request.get("arguments")
        request_execution = request.get("execution")
        artifact = self.slot_artifact
        if (not isinstance(data, Mapping) or not isinstance(execution, Mapping)
                or not isinstance(request_arguments, Mapping)
                or not isinstance(request_execution, Mapping)
                or not isinstance(artifact, Mapping)
                or self.slot is None or self.slot_stage != "registered"):
            raise CandidateError("model.load lacks the exact registered active-slot artifact context")
        artifact_id = artifact.get("artifact_id")
        if (not isinstance(artifact_id, str) or re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None
                or dict(request_arguments) != {"artifact_id": artifact_id}
                or request_execution.get("model_ref") is not None
                or request_execution.get("expected_revision") is not None
                or request_execution.get("project_id") != self.project_id
                or request_execution.get("session_id") != context_before.get("session_id")
                or execution.get("project_id") != self.project_id
                or execution.get("session_id") != context_before.get("session_id")
                or execution.get("request_id") != request_execution.get("request_id")
                or execution.get("idempotency_key") != request_execution.get("idempotency_key")):
            raise CandidateError("model.load request/response is not bound to the exact registered project artifact and session")
        if (data.get("status") != "LOADED"
                or data.get("project_id") != self.project_id
                or data.get("artifact_id") != artifact_id
                or data.get("sha256") != artifact.get("registered_sha256")
                or data.get("size_bytes") != artifact.get("registered_size_bytes")
                or data.get("ownership") != "mcp_owned"):
            raise CandidateError("model.load response does not report the exact registered MPH as a fresh MCP-owned load")
        try:
            from comsol_mcp._execution_contract import model_ref_from_mapping
            ref_value = execution.get("model_ref")
            predecessor_value = self.slot_adopt_binding.get("model_ref") if isinstance(self.slot_adopt_binding, Mapping) else None
            if not isinstance(ref_value, Mapping) or not isinstance(predecessor_value, Mapping):
                raise CandidateError("model.load or its preceding build/adopt chain omitted the exact ModelRef")
            model_ref = model_ref_from_mapping(dict(ref_value))
            predecessor_ref = model_ref_from_mapping(dict(predecessor_value))
            ledger = context_before["ledger"]
            model_state = ledger._state_for(model_ref)
            ownership = ledger.model_ownership.get(model_ref.model_tag)
        except CandidateError:
            raise
        except Exception as exc:
            raise CandidateError("model.load ModelRef is not registered in the exact session ledger") from exc
        if (dict(ref_value) != model_ref.as_dict()
                or model_state.ref != model_ref or model_state.dirty
                or model_ref.session_id != context_before.get("session_id")
                or model_ref.server_instance_id != context_before.get("server_instance_id")
                or model_ref.model_tag == predecessor_ref.model_tag
                or predecessor_ref.session_id != model_ref.session_id
                or predecessor_ref.server_instance_id != model_ref.server_instance_id
                or data.get("model_tag") != model_ref.model_tag
                or execution.get("model_ownership") != "mcp_owned"
                or ownership != "mcp_owned"):
            raise CandidateError("model.load did not produce the exact fresh MCP-owned session tag")
        return {
            "source_operation": "model.load",
            "load_mode": "registered-artifact-fresh-tag",
            "slot": list(self.slot or ()), "project_id": self.project_id,
            "session_id": model_ref.session_id,
            "server_instance_id": model_ref.server_instance_id,
            "model_ref": model_ref.as_dict(), "model_ownership": "mcp_owned",
            "loaded_artifact": dict(artifact),
            "operation_id": execution.get("operation_id"),
            "job_id": execution.get("job_id"), "request_id": execution.get("request_id"),
        }

    def _validate_model_load_binding(self, request: Mapping[str, Any], response: Mapping[str, Any],
                                    context_before: Mapping[str, Any]) -> dict[str, Any]:
        """Bind model_load to the exact saved slot and its mode-specific owner semantics.

        The public legacy loader has two distinct results: ``loaded`` creates a
        fresh MCP-owned model, while ``adopted-by-path`` selects the already
        loaded model that was just saved.  The latter is rebound by the managed
        backend and therefore has the same tag with the next ledger generation
        and remains user-owned.  Neither branch accepts a foreign ModelRef.
        """
        data = response.get("data")
        execution = response.get("execution")
        request_arguments = request.get("arguments")
        request_execution = request.get("execution")
        artifact = self.slot_artifact
        if (not isinstance(data, Mapping) or not isinstance(execution, Mapping)
                or not isinstance(request_arguments, Mapping)
                or not isinstance(request_execution, Mapping)
                or not isinstance(artifact, Mapping)
                or self.slot is None or self.slot_stage != "saved"):
            raise CandidateError("model_load lacks the exact active saved-slot request/response context")
        artifact_path = artifact.get("path")
        if (not isinstance(artifact_path, str) or not artifact_path
                or dict(request_arguments) != {"path": artifact_path}
                or data.get("requested_path") != artifact_path
                or data.get("file_path") != artifact_path
                or request_execution.get("model_ref") is not None
                or request_execution.get("expected_revision") is not None):
            raise CandidateError("model_load did not reopen the exact unbound saved artifact path")
        if (request_execution.get("project_id") != self.project_id
                or request_execution.get("project_id") != context_before.get("project_id")
                or request_execution.get("session_id") != context_before.get("session_id")
                or execution.get("project_id") != self.project_id
                or execution.get("project_id") != context_before.get("project_id")
                or execution.get("session_id") != request_execution.get("session_id")):
            raise CandidateError("model_load request/response differs from the exact registered project/session")
        if (not isinstance(self.slot_adopt_binding, Mapping)
                or self.slot_adopt_binding.get("source_operation") != "model.adopt"
                or self.slot_adopt_binding.get("slot") != list(self.slot or ())
                or self.slot_adopt_binding.get("project_id") != self.project_id
                or self.slot_adopt_binding.get("session_id") != context_before.get("session_id")
                or self.slot_adopt_binding.get("server_instance_id") != context_before.get("server_instance_id")
                or self.slot_adopt_binding.get("model_ownership") != "user_owned"):
            raise CandidateError("model_load lacks its exact preceding active-slot user-owned adopt binding")
        try:
            from comsol_mcp._execution_contract import model_ref_from_mapping
            ref_value = execution.get("model_ref")
            predecessor_value = self.slot_adopt_binding.get("model_ref")
            if not isinstance(ref_value, Mapping) or not isinstance(predecessor_value, Mapping):
                raise CandidateError("model_load or its adopt predecessor omitted the exact ModelRef")
            model_ref = model_ref_from_mapping(dict(ref_value))
            predecessor_ref = model_ref_from_mapping(dict(predecessor_value))
            ledger = context_before["ledger"]
            model_state = ledger._state_for(model_ref)
            ownership = ledger.model_ownership.get(model_ref.model_tag)
        except CandidateError:
            raise
        except Exception as exc:
            raise CandidateError("model_load ModelRef is not registered in the exact session ledger") from exc
        if (dict(ref_value) != model_ref.as_dict()
                or model_state.ref != model_ref
                or model_ref.session_id != context_before.get("session_id")
                or model_ref.server_instance_id != context_before.get("server_instance_id")
                or data.get("model_tag") != model_ref.model_tag):
            raise CandidateError("model_load ModelRef is not the exact registered session model")

        load_mode = data.get("load_mode")
        expected_ownership = _model_load_expected_ownership(load_mode)
        if load_mode == "adopted-by-path":
            if (predecessor_ref.session_id != model_ref.session_id
                    or predecessor_ref.server_instance_id != model_ref.server_instance_id
                    or predecessor_ref.model_tag != model_ref.model_tag
                    or model_ref.generation != predecessor_ref.generation + 1):
                raise CandidateError("adopted-by-path model_load differs from the exact rebound active-slot model")
        elif load_mode == "loaded":
            if (predecessor_ref.session_id != model_ref.session_id
                    or predecessor_ref.server_instance_id != model_ref.server_instance_id
                    or predecessor_ref.model_tag == model_ref.model_tag):
                raise CandidateError("loaded model_load is not a distinct new active-session model")
        if (execution.get("model_ownership") != expected_ownership
                or ownership != expected_ownership):
            raise CandidateError("model_load ownership does not match its exact public load_mode")
        return {
            "source_operation": "model_load", "load_mode": load_mode,
            "slot": list(self.slot or ()), "project_id": self.project_id,
            "session_id": model_ref.session_id,
            "server_instance_id": model_ref.server_instance_id,
            "model_ref": model_ref.as_dict(), "model_ownership": expected_ownership,
            "loaded_artifact": dict(artifact),
            "operation_id": execution.get("operation_id"),
            "job_id": execution.get("job_id"), "request_id": execution.get("request_id"),
        }

    @staticmethod
    def _expected_load_binding_ownership(predecessor: Mapping[str, Any]) -> str:
        source_operation = predecessor.get("source_operation")
        if source_operation == "model.load":
            if predecessor.get("load_mode") != "registered-artifact-fresh-tag":
                raise CandidateError("model.inspect predecessor is not an exact registered-artifact fresh-tag load")
            expected = "mcp_owned"
        elif source_operation == "model_load":
            expected = _model_load_expected_ownership(predecessor.get("load_mode"))
        else:
            raise CandidateError("model.inspect predecessor is not an exact managed load binding")
        if predecessor.get("model_ownership") != expected:
            raise CandidateError("managed load predecessor ownership differs from its exact load mode")
        return expected

    def _operation_store_terminal_pair_proof(
        self,
        *, operation: str, request: Mapping[str, Any], response: Any,
        store: Any, context_before: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Derive a wrapper-local terminal gate from exact durable store evidence."""
        if operation not in LEGACY_STORE_TERMINAL_PROOF_OPERATIONS:
            raise CandidateError("operation is outside the exact legacy store-proof allowlist")
        if (not isinstance(response, Mapping) or response.get("success") is not True
                or response.get("error") not in (None, {})
                or response.get("execution_state_unknown") is True):
            raise CandidateError("public route response is not an unambiguous success")
        data = response.get("data")
        execution = response.get("execution")
        request_execution = request.get("execution")
        if (not isinstance(data, Mapping) or "worker" in data
                or data.get("execution_state_unknown") is True
                or not isinstance(execution, Mapping)
                or not isinstance(request_execution, Mapping)):
            raise CandidateError("fallback is allowed only when the public Worker key is genuinely absent")

        # Re-resolve the current registration, then compare object identity and
        # immutable semantic fields against the exact pre-dispatch snapshot.
        after = self._terminal_proof_context_snapshot(request_execution)
        self._assert_same_terminal_proof_context(context_before, after)

        identity_fields = ("operation_id", "job_id", "request_id", "idempotency_key", "request_hash")
        route_identity = {field: execution.get(field) for field in identity_fields}
        if any(not isinstance(value, str) or not value for value in route_identity.values()):
            raise CandidateError("public route response omitted exact durable operation identity")
        if (execution.get("project_id") != context_before.get("project_id")
                or execution.get("session_id") != context_before.get("session_id")
                or request_execution.get("project_id") != context_before.get("project_id")
                or request_execution.get("session_id") != context_before.get("session_id")
                or execution.get("request_id") != request_execution.get("request_id")
                or execution.get("idempotency_key") != request_execution.get("idempotency_key")):
            raise CandidateError("public request/response identity differs from the registered session")

        operation_row = store.get_operation(route_identity["operation_id"])
        job_by_operation = store.operation_job(route_identity["operation_id"])
        job_row = store.job(route_identity["job_id"])
        if not isinstance(operation_row, Mapping) or not isinstance(job_by_operation, Mapping) or not isinstance(job_row, Mapping):
            raise CandidateError("OperationStore did not return the exact operation/job pair")
        if (operation_row.get("operation_id") != route_identity["operation_id"]
                or operation_row.get("operation") != operation
                or operation_row.get("status") != "SUCCEEDED"
                or operation_row.get("request_id") != route_identity["request_id"]
                or operation_row.get("idempotency_key") != route_identity["idempotency_key"]
                or operation_row.get("request_hash") != route_identity["request_hash"]
                or job_by_operation.get("job_id") != route_identity["job_id"]
                or job_by_operation.get("operation_id") != route_identity["operation_id"]
                or job_by_operation.get("status") != "SUCCEEDED"
                or job_row.get("job_id") != route_identity["job_id"]
                or job_row.get("operation_id") != route_identity["operation_id"]
                or job_row.get("status") != "SUCCEEDED"):
            raise CandidateError("durable operation/job identity is mismatched or nonterminal")
        raw_public = dict(response)
        if (operation_row.get("result") != raw_public
                or job_by_operation.get("result") != raw_public
                or job_row.get("result") != raw_public):
            raise CandidateError("stored result does not exactly match the raw public route response")
        op_metadata = operation_row.get("metadata")
        job_metadata = job_row.get("metadata")
        if not isinstance(op_metadata, Mapping) or not isinstance(job_metadata, Mapping):
            raise CandidateError("durable operation metadata is missing")
        durable_execution = op_metadata.get("execution")
        runtime_binding = op_metadata.get("runtime_binding")
        expected_arguments = request.get("arguments")
        if (op_metadata.get("operation") != operation
                or op_metadata.get("arguments") != expected_arguments
                or job_metadata.get("operation") != op_metadata.get("operation")
                or job_metadata.get("arguments") != op_metadata.get("arguments")
                or job_metadata.get("execution") != op_metadata.get("execution")
                or job_metadata.get("runtime_binding") != op_metadata.get("runtime_binding")
                or not isinstance(durable_execution, Mapping)
                or dict(durable_execution) != dict(request_execution)
                or not isinstance(runtime_binding, Mapping)
                or runtime_binding.get("kind") != "registered_session"
                or runtime_binding.get("project_id") != context_before.get("project_id")
                or runtime_binding.get("session_id") != context_before.get("session_id")
                or type(runtime_binding.get("worker_epoch")) is not int
                or runtime_binding.get("worker_epoch") != context_before.get("worker_epoch")
                or runtime_binding.get("worker_instance_id") != context_before.get("worker_instance_id")):
            raise CandidateError("durable request metadata is not bound to the exact registered session")

        snapshot = store.collect_job_event_snapshot(route_identity["job_id"])
        if (not isinstance(snapshot, Mapping) or snapshot.get("complete") is not True
                or snapshot.get("status") != "COMPLETE"
                or snapshot.get("job_id") != route_identity["job_id"]
                or snapshot.get("job_row_count") != 1
                or snapshot.get("terminal_sentinel_checked") is not True
                or snapshot.get("terminal_sentinel_count") != 0
                or snapshot.get("issues") != []
                or snapshot.get("expected_event_count") != snapshot.get("fetched_event_count")
                or snapshot.get("fetched_event_count") != len(snapshot.get("events", []))):
            raise CandidateError("complete bounded OperationStore job-event snapshot is unavailable")
        event_rows = snapshot.get("events")
        if not isinstance(event_rows, list):
            raise CandidateError("OperationStore event snapshot has an invalid row list")
        worker_rows = [row for row in event_rows if isinstance(row, Mapping) and row.get("event") == "worker_request"]
        if not worker_rows:
            raise CandidateError("no terminal Worker request pairs exist for the exact operation")
        rows_by_request: dict[str, dict[str, Mapping[str, Any]]] = {}
        for row in worker_rows:
            metadata = row.get("metadata")
            if (not isinstance(metadata, Mapping)
                    or row.get("job_id") != route_identity["job_id"]
                    or metadata.get("operation_id") != route_identity["operation_id"]):
                raise CandidateError("Worker event belongs to a foreign or malformed durable operation")
            request_id = metadata.get("request_id")
            phase = metadata.get("phase")
            kind = metadata.get("kind")
            request_hash = metadata.get("request_hash")
            inner_metadata = metadata.get("metadata")
            if (not isinstance(request_id, str) or not request_id
                    or phase not in {"submitted", "observed"}
                    or not isinstance(kind, str) or not kind
                    or not isinstance(request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None
                    or not isinstance(inner_metadata, Mapping)
                    or inner_metadata.get("request_id") != request_id
                    or request_id in rows_by_request and phase in rows_by_request[request_id]):
                raise CandidateError("Worker event has a duplicate, malformed, or nonterminal phase")
            rows_by_request.setdefault(request_id, {})[phase] = row
            row_id = row.get("id")
            if type(row_id) is not int or row_id <= 0:
                raise CandidateError("Worker event is missing an exact durable row identity")
        for phase_rows in rows_by_request.values():
            if set(phase_rows) != {"submitted", "observed"}:
                raise CandidateError("Worker request is missing its submitted/observed terminal pair")
            submitted_row, observed_row = phase_rows["submitted"], phase_rows["observed"]
            submitted, observed = submitted_row["metadata"], observed_row["metadata"]
            submitted_inner, observed_inner = submitted.get("metadata"), observed.get("metadata")
            reply = observed.get("reply")
            if (submitted.get("kind") != observed.get("kind")
                    or submitted.get("request_hash") != observed.get("request_hash")
                    or submitted_inner != observed_inner
                    or observed.get("status") != "SUCCEEDED"
                    or not isinstance(reply, Mapping)
                    or reply.get("request_id") != observed.get("request_id")
                    or reply.get("status") != "SUCCEEDED"
                    or reply.get("ok") is not True):
                raise CandidateError("Worker submitted/observed receipt pair is not exactly terminal SUCCEEDED")
            if submitted_row["id"] >= observed_row["id"]:
                raise CandidateError("Worker observed receipt does not follow its submitted receipt")

        from comsol_mcp._execution_contract import model_ref_from_mapping
        response_execution = execution
        model_ref_value = response_execution.get("model_ref")
        if operation in {"model_create", "model.adopt", "model_load", "model.load"}:
            if not isinstance(model_ref_value, Mapping) or data.get("model_tag") != model_ref_value.get("model_tag"):
                raise CandidateError("model route result omitted the exact model tag/ModelRef pair")
        elif operation == "model.inspect":
            model_ref_value = request_execution.get("model_ref")
            if response_execution.get("model_ref") != model_ref_value:
                raise CandidateError("model.inspect result differs from its exact outer ModelRef")
        try:
            model_ref = model_ref_from_mapping(dict(model_ref_value))
            model_state = context_before["service"].ledger._state_for(model_ref)
        except Exception as exc:
            raise CandidateError("route ModelRef is not registered in the exact session ledger") from exc
        ledger_ownership = context_before["service"].ledger.model_ownership.get(model_ref.model_tag)
        if operation == "model_create":
            expected_ownership = "mcp_owned"
            if (model_ref.model_tag != data.get("model_tag")
                    or execution.get("model_ownership") != expected_ownership
                    or ledger_ownership != expected_ownership):
                raise CandidateError("created ModelRef is not the exact MCP-owned session model")
        elif operation == "model_load":
            self._validate_model_load_binding(request, response, context_before)
        elif operation == "model.load":
            self._validate_managed_artifact_load_binding(request, response, context_before)
        elif operation == "model.adopt":
            if (self.slot_stage != "built"
                    or model_ref.model_tag != self.slot_model_tag
                    or execution.get("project_id") != context_before.get("project_id")
                    or execution.get("session_id") != context_before.get("session_id")
                    or execution.get("model_ownership") != "user_owned"
                    or ledger_ownership != "user_owned"):
                raise CandidateError("model.adopt result is not the exact active-slot registered-session user model")
        elif operation == "model.inspect":
            if self.slot_stage == "adopted":
                predecessor = self.slot_adopt_binding
                expected_ownership = "user_owned"
                predecessor_kind = "model.adopt"
            elif self.slot_stage == "loaded":
                predecessor = self.slot_load_binding
                if not isinstance(predecessor, Mapping):
                    raise CandidateError("model.inspect lacks its exact managed load binding")
                expected_ownership = self._expected_load_binding_ownership(predecessor)
                predecessor_kind = str(predecessor.get("source_operation"))
            else:
                raise CandidateError("model.inspect has no exact active-slot adopt/load predecessor")
            if not isinstance(predecessor, Mapping):
                raise CandidateError(f"model.inspect lacks its exact {predecessor_kind} ModelRef binding")
            predecessor_ref = predecessor.get("model_ref")
            observed_ref = data.get("model_identity")
            if (not isinstance(predecessor_ref, Mapping)
                    or not isinstance(observed_ref, Mapping)
                    or dict(predecessor_ref) != model_ref.as_dict()
                    or request_execution.get("model_ref") != model_ref.as_dict()
                    or execution.get("model_ref") != model_ref.as_dict()
                    or dict(observed_ref) != model_ref.as_dict()
                    or predecessor.get("slot") != list(self.slot or ())
                    or predecessor.get("project_id") != context_before.get("project_id")
                    or predecessor.get("session_id") != context_before.get("session_id")
                    or predecessor.get("server_instance_id") != context_before.get("server_instance_id")
                    or predecessor.get("model_ownership") != expected_ownership
                    or execution.get("model_ownership") != expected_ownership
                    or ledger_ownership != expected_ownership):
                raise CandidateError(f"model.inspect differs from the exact {predecessor_kind} identity/ownership chain")
        if (model_ref.session_id != context_before.get("session_id")
                or model_ref.server_instance_id != context_before.get("server_instance_id")
                or model_ref.model_tag != data.get("model_tag", model_ref.model_tag)
                or model_state.ref != model_ref):
            raise CandidateError("route ModelRef does not bind to the exact session/service model state")

        pair_rows = []
        for request_id, phase_rows in sorted(rows_by_request.items()):
            submitted_row, observed_row = phase_rows["submitted"], phase_rows["observed"]
            submitted = submitted_row["metadata"]
            pair_rows.append({
                "request_id": request_id,
                "kind": submitted.get("kind"),
                "request_hash": submitted.get("request_hash"),
                "submitted_event_id": submitted_row["id"],
                "observed_event_id": observed_row["id"],
            })
        proof_body = {
            "schema": "W24_OPERATION_STORE_TERMINAL_PAIR_PROOF_V1",
            "proof_kind": "operation_store_terminal_pair",
            "source": "local_control_daemon_operation_store_and_registered_context",
            "operation": operation,
            "operation_identity": route_identity,
            "project_id": context_before["project_id"],
            "session_id": context_before["session_id"],
            "worker_instance_id": context_before["worker_instance_id"],
            "worker_epoch": context_before["worker_epoch"],
            "server_instance_id": context_before["server_instance_id"],
            "endpoint": {"host": context_before["endpoint_host"],
                         "port": context_before["endpoint_port"],
                         "observed_peer": {"address": context_before["endpoint_peer"].address,
                                           "port": context_before["endpoint_peer"].port}},
            "worker_request_pair_count": len(pair_rows),
            "worker_request_pairs": pair_rows,
            "job_event_snapshot_sha256": snapshot.get("events_sha256"),
            "context_identity_sha256": _sha_bytes(json.dumps({
                "project_id": context_before["project_id"], "session_id": context_before["session_id"],
                "worker_instance_id": context_before["worker_instance_id"],
                "worker_epoch": context_before["worker_epoch"],
                "server_instance_id": context_before["server_instance_id"],
                "endpoint_host": context_before["endpoint_host"],
                "endpoint_port": context_before["endpoint_port"],
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            "raw_public_response_sha256": _sha_bytes(json.dumps(
                raw_public, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False).encode("utf-8")),
        }
        proof_body["proof_sha256"] = _sha_bytes(json.dumps(
            proof_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8"))
        return proof_body

    def _validate_model_inspect_binding(self, request_execution: Mapping[str, Any],
                                        response: Mapping[str, Any],
                                        context_before: Mapping[str, Any]) -> None:
        """Require model.inspect to preserve the exact active-slot ModelRef chain."""
        if self.slot_stage == "adopted":
            predecessor = self.slot_adopt_binding
            expected_ownership = "user_owned"
            predecessor_kind = "model.adopt"
        elif self.slot_stage == "loaded":
            predecessor = self.slot_load_binding
            if not isinstance(predecessor, Mapping):
                raise CandidateError("model.inspect lacks its exact managed load binding")
            expected_ownership = self._expected_load_binding_ownership(predecessor)
            predecessor_kind = str(predecessor.get("source_operation"))
        else:
            raise CandidateError("model.inspect has no exact active-slot adopt/load predecessor")
        if not isinstance(predecessor, Mapping):
            raise CandidateError(f"model.inspect lacks its exact {predecessor_kind} ModelRef binding")
        data = response.get("data")
        execution = response.get("execution")
        if not isinstance(data, Mapping) or not isinstance(execution, Mapping):
            raise CandidateError("model.inspect omitted its public identity envelope")
        try:
            from comsol_mcp._execution_contract import model_ref_from_mapping
            ref_value = request_execution.get("model_ref")
            if not isinstance(ref_value, Mapping):
                raise CandidateError("model.inspect request omitted its exact ModelRef")
            model_ref = model_ref_from_mapping(dict(ref_value))
            model_state = context_before["ledger"]._state_for(model_ref)
            ledger_ownership = context_before["ledger"].model_ownership.get(model_ref.model_tag)
        except CandidateError:
            raise
        except Exception as exc:
            raise CandidateError("model.inspect ModelRef is not registered in the active session ledger") from exc
        predecessor_ref = predecessor.get("model_ref")
        observed_ref = data.get("model_identity")
        expected_project = self.project_id
        expected_session = request_execution.get("session_id")
        if (not isinstance(predecessor_ref, Mapping)
                or not isinstance(observed_ref, Mapping)
                or dict(predecessor_ref) != model_ref.as_dict()
                or dict(ref_value) != model_ref.as_dict()
                or execution.get("model_ref") != model_ref.as_dict()
                or dict(observed_ref) != model_ref.as_dict()
                or predecessor.get("slot") != list(self.slot or ())
                or predecessor.get("project_id") != expected_project
                or request_execution.get("project_id") != expected_project
                or execution.get("project_id") != expected_project
                or predecessor.get("session_id") != expected_session
                or model_ref.session_id != expected_session
                or execution.get("session_id") != expected_session
                or predecessor.get("server_instance_id") != model_ref.server_instance_id
                or model_ref.server_instance_id != context_before.get("server_instance_id")
                or data.get("model_tag", model_ref.model_tag) != model_ref.model_tag
                or model_state.ref != model_ref
                or predecessor.get("model_ownership") != expected_ownership
                or execution.get("model_ownership") != expected_ownership
                or ledger_ownership != expected_ownership):
            raise CandidateError(f"model.inspect differs from the exact {predecessor_kind} identity/ownership chain")

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self.native_epoch_capability is not None:
            from tools.w24_native_epoch_capability import require_native_epoch_capability
            require_native_epoch_capability(self.native_epoch_capability)
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
        is_artifact_registration = (operation == "operation_call"
                                    and routed_arguments.get("operation_id") == "artifact.register")
        if is_artifact_registration:
            registration_body = routed_arguments.get("arguments")
            if (not isinstance(registration_body, Mapping)
                    or registration_body.get("project_id") != self.project_id
                    or registration_body.get("request_id") != execution.get("request_id")
                    or registration_body.get("idempotency_key") != execution.get("idempotency_key")
                    or execution.get("project_id") != self.project_id):
                raise CandidateError("artifact.register nested and outer project/request identities must match exactly")
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
        terminal_context_before = None
        terminal_context_capture_error = None
        if operation in LEGACY_STORE_TERMINAL_PROOF_OPERATIONS or is_artifact_registration:
            try:
                terminal_context_before = self._terminal_proof_context_snapshot(execution)
            except Exception as exc:
                terminal_context_capture_error = f"{type(exc).__name__}: {exc}"
        registered_context_operations = {"model.adopt", "model.inspect", "model_load", "model.load"}
        if operation in registered_context_operations:
            if terminal_context_before is None:
                raise CandidateError(terminal_context_capture_error or
                                     "route requires an exact pre-dispatch registered session context")
            if (execution.get("project_id") != terminal_context_before.get("project_id")
                    or execution.get("session_id") != terminal_context_before.get("session_id")):
                raise CandidateError("route execution identity differs from the exact registered session context")
            if operation == "model.inspect":
                self._validate_requested_model_ref_context(execution, terminal_context_before)
        if operation == "model_create":
            self.bootstrap_model_create_used = True
        worker_required = (operation in {"model_create", "model.adopt", "model.inspect", "model_load", "model.load"}
                           or (operation == "operation_call"
                               and routed_arguments.get("operation_id") != "artifact.register"))
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
        gate_response = response
        terminal_pair_proof = None
        terminal_pair_failure = None
        terminal_context_failure = None
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
        if operation in registered_context_operations or is_artifact_registration:
            try:
                terminal_context_after = self._terminal_proof_context_snapshot(execution)
                self._assert_same_terminal_proof_context(terminal_context_before, terminal_context_after)
            except Exception as exc:
                terminal_context_failure = f"{type(exc).__name__}: {exc}"
        if (worker_required and operation in LEGACY_STORE_TERMINAL_PROOF_OPERATIONS
                and isinstance(response, Mapping) and isinstance(data, Mapping)
                and "worker" not in data):
            try:
                if terminal_context_before is None:
                    raise CandidateError(terminal_context_capture_error or
                                         "pre-dispatch registered session snapshot is unavailable")
                terminal_pair_proof = self._operation_store_terminal_pair_proof(
                    operation=operation, request=routed, response=response,
                    store=self.store, context_before=terminal_context_before,
                )
                proof_path = route_dir / f"{identity_hash}_terminal_pair_proof.json"
                _write_new(proof_path, terminal_pair_proof)
                gate_data = dict(data)
                gate_data["worker"] = {
                    "status": "SUCCEEDED",
                    "proof_kind": "operation_store_terminal_pair",
                    "proof_sha256": terminal_pair_proof["proof_sha256"],
                }
                # This is a wrapper-local gate copy.  The public response above,
                # the raw route-result receipt, and OperationStore.result retain
                # their exact original shape and bytes.
                gate_response = {**response, "data": gate_data}
                worker_status = "SUCCEEDED"
            except Exception as exc:
                terminal_pair_failure = f"{type(exc).__name__}: {exc}"
        uncertain = (not isinstance(response, Mapping) or
                     response.get("execution_state_unknown") is True or
                     (isinstance(data, Mapping) and data.get("execution_state_unknown") is True) or
                     terminal_context_failure is not None or
                     (worker_required and not self._worker_terminal(gate_response)))
        if uncertain:
            self.unknown = True
        _append(self.events_path, {"event": "setup_dispatch_returned",
                                   "operation": operation,
                                   "slot": list(self.slot) if self.slot else None,
                                   "request_id": request_id,
                                   "idempotency_key": idempotency_key,
                                   "success": response.get("success") is True if isinstance(response, Mapping) else False,
                                   "worker_status": worker_status,
                                   "terminal_proof_kind": (terminal_pair_proof.get("proof_kind")
                                                           if isinstance(terminal_pair_proof, Mapping) else None),
                                   "terminal_proof_failure": terminal_pair_failure,
                                   "registered_context_failure": terminal_context_failure,
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
            gate_data = gate_response.get("data")
            worker = gate_data.get("worker") if isinstance(gate_data, Mapping) else None
            if not isinstance(worker, Mapping) or worker.get("status") != "SUCCEEDED":
                self.unknown = True
                raise CandidateError("Worker-backed setup route lacks terminal SUCCEEDED status")

        if operation == "model.inspect":
            try:
                self._validate_model_inspect_binding(execution, response, terminal_context_before)
            except CandidateError:
                self.unknown = True
                raise

        if self.native_epoch_capability is not None and operation == "operation_call" and arguments.get("operation_id") == "code.execute_java":
            try:
                self.native_epoch_capability.verify(response,
                    model_ref=dict(execution["model_ref"]), revision=response.get("execution", {}).get("revision"))
            except BaseException as exc:
                self.unknown = True
                _append(self.events_path, {"event": "actual_native_epoch_guard_failed",
                    "request_id": request_id, "operation": operation, "error": str(exc),
                    "durable_operation_state_rewritten": False, "stop_all_follow_on_routes": True})
                raise CandidateError("actual Java epoch/origin guard failed; no follow-on route") from exc

        if operation == "model.adopt":
            route_execution = response.get("execution")
            data = response.get("data")
            ref = route_execution.get("model_ref") if isinstance(route_execution, Mapping) else None
            try:
                from comsol_mcp._execution_contract import model_ref_from_mapping
                model_ref = model_ref_from_mapping(dict(ref)) if isinstance(ref, Mapping) else None
                ledger = terminal_context_before["ledger"]
                model_state = ledger._state_for(model_ref) if model_ref is not None else None
                ownership = ledger.model_ownership.get(model_ref.model_tag) if model_ref is not None else None
            except Exception as exc:
                self.unknown = True
                raise CandidateError("model.adopt returned a ModelRef not registered in the active session ledger") from exc
            if (model_ref is None or not isinstance(data, Mapping)
                    or model_ref.model_tag != self.slot_model_tag
                    or data.get("model_tag") != self.slot_model_tag
                    or route_execution.get("project_id") != self.project_id
                    or execution.get("project_id") != self.project_id
                    or route_execution.get("session_id") != execution.get("session_id")
                    or model_ref.session_id != route_execution.get("session_id")
                    or model_ref.server_instance_id != terminal_context_before["server_instance_id"]
                    or model_state.ref != model_ref
                    or route_execution.get("model_ownership") != "user_owned"
                    or ownership != "user_owned"):
                self.unknown = True
                raise CandidateError("model.adopt did not return the exact active-slot user-owned ModelRef")
            self.slot_adopt_binding = {
                "source_operation": "model.adopt", "slot": list(self.slot or ()),
                "project_id": route_execution["project_id"], "session_id": route_execution["session_id"],
                "server_instance_id": model_ref.server_instance_id,
                "model_ref": model_ref.as_dict(), "model_ownership": ownership,
                "operation_id": route_execution.get("operation_id"),
                "job_id": route_execution.get("job_id"), "request_id": route_execution.get("request_id"),
            }
            self.slot_stage = "adopted"
        elif operation == "model.inspect":
            self.slot_stage = ("pre_save_inspected" if self.slot_stage == "adopted"
                               else "reopened_inspected")
        elif operation == "model_load":
            try:
                self.slot_load_binding = self._validate_model_load_binding(
                    routed, response, terminal_context_before,
                )
            except CandidateError:
                self.unknown = True
                raise
            self.slot_stage = "loaded"
        elif operation == "model.load":
            try:
                self.slot_load_binding = self._validate_managed_artifact_load_binding(
                    routed, response, terminal_context_before,
                )
            except CandidateError:
                self.unknown = True
                raise
            self.slot_stage = "loaded"
        elif operation == "operation_call":
            if routed_arguments.get("operation_id") == "artifact.register":
                try:
                    self.slot_artifact = self._validate_registered_artifact(routed, response)
                except CandidateError:
                    self.unknown = True
                    raise
                self.slot_stage = "registered"
                return response
            data = response.get("data")
            envelope = data.get("readback") if isinstance(data, Mapping) else None
            nested_result = envelope.get("readback") if isinstance(envelope, Mapping) else None
            body = routed_arguments.get("arguments")
            entrypoint = body.get("entrypoint") if isinstance(body, Mapping) else None
            java_arguments = body.get("arguments") if isinstance(body, Mapping) else None
            action = java_arguments.get("action") if isinstance(java_arguments, Mapping) else None
            if self.schema_probe_only:
                if (entrypoint != "W24StaticShapeFixture#run" or
                        java_arguments != {"action": "property_schema_only"} or
                        not isinstance(nested_result, Mapping) or
                        nested_result.get("action") != "property_schema_only" or
                        not isinstance(nested_result.get("model_tag"), str)):
                    self.unknown = True
                    raise CandidateError("schema-only Worker response lacks its exact terminal fixture result")
                self.schema_probe_result = dict(nested_result)
                self.schema_probe_completed = True
            elif entrypoint == "W24StaticShapeFixture#run":
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
                                   worker_epoch: int, worker_instance_id: str | None = None,
                                   receiver_anchor: Any = None, backend: Any = None) -> dict[str, Any]:
    """Reject all cross-session/epoch ModelRefs before accepting a setup receipt."""
    if not isinstance(binding, Mapping):
        raise CandidateError("setup receipt omitted its ModelRef binding record")
    from copy import deepcopy
    captured_binding=deepcopy(dict(binding))
    ref = binding.get("model_ref")
    if not isinstance(ref, Mapping):
        raise CandidateError("setup receipt omitted its current ModelRef")
    if (type(worker_epoch) is not int or worker_epoch < 1 or
            binding.get("project_id") != project_id or binding.get("session_id") != session_id or
            ref.get("session_id") != session_id or ref.get("server_instance_id") != server_instance_id or
            type(ref.get("generation")) is not int or ref.get("generation") < 1 or
            not isinstance(ref.get("model_tag"), str) or not ref.get("model_tag") or
            isinstance(binding.get("revision"), bool) or
            not isinstance(binding.get("revision"), int) or binding["revision"] < 0):
        raise CandidateError("ModelRef belongs to a prior project/session/Worker epoch or has invalid revision")
    from tools.run_native_w24_cure_science import ManagedModelBinding
    from tools.w24_native_epoch_capability import _verify_live_worker_binding
    try:
        observed = _verify_live_worker_binding(receiver_anchor,
            ManagedModelBinding(project_id,session_id,dict(ref),binding["revision"]),backend)
        if observed["worker_epoch"] != worker_epoch or observed["worker_instance_id"] != worker_instance_id:
            raise ValueError("original Worker instance/epoch differs")
    except Exception as exc:
        raise CandidateError("ModelRef belongs to a prior project/session/Worker epoch or has invalid revision") from exc
    if dict(binding)!=captured_binding:
        raise CandidateError("ModelRef record changed during original Worker binding validation")
    return dict(binding)


def _owned_server_start_identity(response: Mapping[str, Any], *, project_id: str,
                                 runtime_id: str) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, Mapping) else None
    endpoint = data.get("endpoint") if isinstance(data, Mapping) else None
    identity = data.get("server_process_identity") if isinstance(data, Mapping) else None
    session_id = data.get("session_id") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("project_id") != project_id or data.get("runtime_id") != runtime_id or
            data.get("server_ownership") != "mcp_managed" or
            data.get("loopback_only_verified") is not True or
            not isinstance(session_id, str) or not session_id or
            not isinstance(endpoint, Mapping) or endpoint.get("host") != "127.0.0.1" or
            type(endpoint.get("port")) is not int or not 1 <= endpoint["port"] <= 65535 or
            not isinstance(identity, Mapping) or identity.get("runtime_id") != runtime_id or
            identity.get("endpoint") != dict(endpoint) or
            type(identity.get("pid")) is not int or identity["pid"] <= 1 or
            not isinstance(identity.get("birth"), str) or not identity["birth"]):
        raise CandidateError("session.start did not return the exact new owned-server lifecycle binding")
    return {"project_id": project_id, "runtime_id": runtime_id,
            "session_id": session_id, "endpoint": dict(endpoint),
            "server_process_identity": dict(identity),
            "server_listener_sockets": data.get("server_listener_sockets")}


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
    from tools.run_w21_stage_native import _resolve_remote_comsol_identity, RunnerError
    try:
        observed_engine = _resolve_remote_comsol_identity(data or {}, {"version":"6.4.0", "build":"293"})
    except (RunnerError, TypeError, AttributeError) as exc:
        raise CandidateError("session.connect did not bind the exact loopback endpoint and COMSOL 6.4.0.293 identity") from exc
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("project_id") != project_id or
            endpoint not in (f"127.0.0.1:{expected_port}",
                             {"host": "127.0.0.1", "port": expected_port}) or
            not isinstance(peer, Mapping) or peer.get("address") != "127.0.0.1" or
            peer.get("port") != expected_port or
            not all(isinstance(item, str) and item for item in (session_id, server_id, worker_id)) or
            type(epoch) is not int or epoch < 1):
        raise CandidateError("session.connect did not bind the exact loopback endpoint and COMSOL 6.4.0.293 identity")
    return {"project_id": project_id, "session_id": session_id,
            "server_instance_id": server_id, "worker_instance_id": worker_id,
            "worker_epoch": epoch, "endpoint": endpoint,
            "observed_peer": dict(peer), "remote_engine_version": version,
            "remote_engine_build": build, "remote_engine_build_source": data.get("remote_engine_build_source"),
            "observed_engine_identity": observed_engine}


def _require_connected_inspect(response: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    lifecycle = data.get("lifecycle") if isinstance(data, Mapping) else None
    binding = data.get("worker_binding") if isinstance(data, Mapping) else None
    expected_project = session.get("project_id")
    expected_session = session.get("session_id")
    expected_endpoint = session.get("endpoint")
    expected_server = session.get("server_instance_id")
    expected_worker = session.get("worker_instance_id")
    expected_epoch = session.get("worker_epoch")
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("runtime_live") is not True or not isinstance(lifecycle, Mapping) or
            not isinstance(binding, Mapping) or
            not isinstance(expected_project, str) or not expected_project or
            not isinstance(expected_session, str) or not expected_session or
            not isinstance(expected_endpoint, Mapping) or set(expected_endpoint) != {"host", "port"} or
            expected_endpoint.get("host") != "127.0.0.1" or
            type(expected_endpoint.get("port")) is not int or
            not 1 <= expected_endpoint["port"] <= 65535 or
            not isinstance(expected_server, str) or not expected_server or
            not isinstance(expected_worker, str) or not expected_worker or
            type(expected_epoch) is not int or expected_epoch < 1 or
            lifecycle.get("project_id") != expected_project or
            lifecycle.get("session_id") != expected_session or
            lifecycle.get("state") != "CONNECTED" or
            lifecycle.get("endpoint") != dict(expected_endpoint) or
            lifecycle.get("server_instance_id") != expected_server or
            lifecycle.get("worker_instance_id") != expected_worker or
            type(lifecycle.get("worker_epoch")) is not int or
            lifecycle.get("worker_epoch") != expected_epoch or
            binding.get("worker_instance_id") != expected_worker or
            type(binding.get("worker_epoch")) is not int or
            binding.get("worker_epoch") != expected_epoch or
            binding.get("server_lane") != "owned"):
        raise CandidateError("session.inspect did not confirm the exact connected Worker epoch and owned server binding")
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
            raise OutcomeUnknown(f"ControlDaemon {operation} raised after persisted intent; do not replay",
                             request_id=request_id, idempotency_key=key) from exc
    if not isinstance(response, Mapping):
        _write_new(route_dir / f"{stem}_result.json", {
            "schema": "W24_SETUP_DIRECT_ROUTE_RESULT_V1",
            "operation": operation, "request_id": request_id,
            "idempotency_key": key, "response_type": type(response).__name__,
            "response_repr": repr(response), "terminal_identity": False,
        })
        raise OutcomeUnknown(f"ControlDaemon {operation} returned a non-object result; do not replay",
                             request_id=request_id, idempotency_key=key)
    try:
        _write_new(route_dir / f"{stem}_result.json",
                   {"schema": "W24_SETUP_DIRECT_ROUTE_RESULT_V1",
                    "operation": operation, "request_id": request_id,
                    "idempotency_key": key, "response": dict(response),
                    "at_utc": datetime.now(timezone.utc).isoformat()})
    except BaseException as exc:
        raise OutcomeUnknown(f"ControlDaemon {operation} returned but durable response receipt failed; do not replay",
                             response=dict(response), request_id=request_id,
                             idempotency_key=key) from exc
    data = response.get("data")
    if (response.get("execution_state_unknown") is True or
            isinstance(data, Mapping) and data.get("execution_state_unknown") is True):
        raise OutcomeUnknown(f"ControlDaemon {operation} returned UNKNOWN; preserve the original operation identity",
                             response=dict(response), request_id=request_id,
                             idempotency_key=key)
    return dict(response)


def _project_create_before_birth(daemon: Any, authorized_root: Path,
                                 evidence: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from tools.run_native_w24_cure_preflight import (
        PROJECT_WORKSPACE_NAME as PROJECT_WORKSPACE,
        _require_trusted_code_host_grant, _verify_registered_workspace,
    )

    host_grants = _require_trusted_code_host_grant(daemon)
    create_id, create_key = _request_ids("project-create")
    if ("host_control" not in host_grants.get("startup_ceiling", []) or
            "host_control" not in host_grants.get("effective_host_permissions", [])):
        raise CandidateError("W24 private owned-server activity lacks an explicit deployment host_control grant")
    policy = {"permissions": ["inspect", "project_write", "compute", "trusted_code", "host_control"]}
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
            not isinstance(effective, list) or
            not {"trusted_code", "host_control"} <= set(effective)):
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
    evidence = {"status": "JAVA_TOOL_OPTIONS_ECHO_PRESENT_DIAGNOSTIC" if matched else "JAVA_TOOL_OPTIONS_ECHO_ABSENT_DIAGNOSTIC",
                "path": str(log_path), "sha256": _sha_bytes(payload),
                "bytes": len(payload), "required_options": JAVA_TOOL_OPTIONS,
                "observed": matched}
    return evidence


def _worker_jvm_identity(worker: Any, worker_identity: Mapping[str, Any],
                         start_epoch_ms: int) -> dict[str, Any]:
    from comsol_mcp._g2_isolation import _process_snapshot
    from comsol_mcp._platform_process import process_identity

    proc = getattr(worker, "_process", None)
    pid = getattr(proc, "pid", None)
    if (proc is None or type(pid) is not int or pid <= 1 or proc.poll() is not None or
            worker_identity.get("pid") != pid):
        raise CandidateError("Worker JVM gate lost the exact registered Popen handle")
    snapshot = _process_snapshot(pid)
    epoch = process_identity(pid)
    if (not isinstance(snapshot, Mapping) or snapshot.get("pid") != pid or
            snapshot.get("birth") != worker_identity.get("birth") or
            snapshot.get("command_sha256") != worker_identity.get("command_sha256") or
            not isinstance(epoch, Mapping) or epoch.get("alive") is not True or
            epoch.get("start_epoch_ms") != start_epoch_ms):
        raise CandidateError("Worker JVM identity differs from its registered Popen birth snapshot")
    uid = _read_process_uid(pid)
    if uid != os.getuid():
        raise CandidateError("Worker JVM UID differs from the exact task owner")
    return {"pid": pid, "uid": uid, "birth": snapshot["birth"],
            "start_epoch_ms": start_epoch_ms,
            "command_sha256": snapshot["command_sha256"]}


def _verify_worker_jvm_limits(worker: Any, worker_identity: Mapping[str, Any],
                              start_epoch_ms: int, jdk: Path,
                              evidence_path: Path | None = None) -> dict[str, Any]:
    pid = getattr(getattr(worker, "_process", None), "pid", None)
    if type(pid) is not int:
        raise CandidateError("Worker JVM limit check lacks the exact registered Popen PID")
    expected = _worker_jvm_identity(worker, worker_identity, start_epoch_ms)
    return _read_effective_jvm_limits(
        pid=pid, jcmd=jdk / "bin/jcmd",
        identity_reader=lambda: _worker_jvm_identity(worker, worker_identity,
                                                       start_epoch_ms),
        expected_identity=expected, evidence_path=evidence_path)


def _model_create_binding(response: Mapping[str, Any], *, project_id: str,
                          session: Mapping[str, Any], receiver_anchor: Any = None, backend: Any = None) -> Any:
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
            type(ref.get("generation")) is not int or ref.get("generation") < 1 or
            not isinstance(ref.get("model_tag"), str) or not ref.get("model_tag") or
            isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise CandidateError("production model_create did not return the exact current session/server Worker epoch")
    binding = ManagedModelBinding(project_id, str(session["session_id"]), dict(ref), revision)
    validate_current_epoch_binding(binding.as_record(),
        project_id=project_id, session_id=str(session["session_id"]),
        server_instance_id=str(session["server_instance_id"]),
        worker_epoch=int(session["worker_epoch"]), worker_instance_id=session["worker_instance_id"],
        receiver_anchor=receiver_anchor, backend=backend)
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


def _validate_slot_receipt(result: Mapping[str, Any], *, slot: Mapping[str, Any],
                           project_id: str, session: Mapping[str, Any],
                           workspace: Path, expected_sources: Mapping[str, Any],
                           receiver_anchor: Any = None, backend: Any = None) -> dict[str, Any]:
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
            worker_epoch=int(session["worker_epoch"]), worker_instance_id=session["worker_instance_id"],
            receiver_anchor=receiver_anchor, backend=backend)
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
    registered = result.get("registered_project_artifact")
    expected_registered_relative = f"g2_artifacts/registered/{digest}.mph"
    if not isinstance(registered, Mapping):
        raise CandidateError("setup receipt omitted the exact public artifact.register result")
    registered_path = Path(str(registered.get("path", "")))
    try:
        registered_resolved = registered_path.resolve(strict=True)
        registered_resolved.relative_to(workspace.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise CandidateError("registered MPH copy is missing or escaped the exact project workspace") from exc
    provenance = registered.get("provenance")
    expected_source_relative = resolved.relative_to(workspace.resolve(strict=True)).as_posix()
    registration_operation = registered.get("registration_operation_id")
    registration_request = registered.get("registration_request_id")
    registration_idempotency = registered.get("registration_idempotency_key")
    if (registered_path.is_symlink() or not registered_resolved.is_file()
            or registered.get("artifact_id") != digest
            or registered.get("relative_path") != expected_registered_relative
            or registered_resolved.relative_to(workspace.resolve(strict=True)).as_posix() != expected_registered_relative
            or registered.get("size_bytes") != size or registered_resolved.stat().st_size != size
            or registered.get("sha256") != digest or _sha_file(registered_resolved) != digest
            or registered.get("role") != "saved_model"
            or registered.get("classification") != "project_local"
            or registered.get("artifact_type") != "application/vnd.comsol.mph"
            or registered.get("format_version") != "comsol-mph-savecopy/1"
            or not isinstance(provenance, Mapping)
            or provenance.get("source_project_relative_path") != expected_source_relative
            or provenance.get("source_sha256") != digest
            or provenance.get("registering_operation_id") != registration_operation
            or provenance.get("request_id") != registration_request
            or not isinstance(registration_operation, str) or not registration_operation
            or not isinstance(registration_request, str) or not registration_request
            or not isinstance(registration_idempotency, str) or not registration_idempotency):
        raise CandidateError("setup receipt registered MPH does not bind the exact saved source, metadata, and registration provenance")
    return {"artifact": {"path": str(resolved), "size_bytes": size,
                         "sha256": digest},
            "registered_artifact": {"path": str(registered_resolved),
                                    "size_bytes": size, "sha256": digest,
                                    "artifact_id": digest},
            "accounted_output_bytes": 2 * size,
            "model_tags": [row["model_tag"] for row in checked_bindings[1:3]],
            "bindings": checked_bindings,
            "setup_science_status": "NOT_RUN",
            "binding_validity_after_worker_retirement": "HISTORICAL_ONLY_REQUIRES_NEW_WORKER_RE_ADMISSION"}


def _expected_child_environment(work: Path, repo: Path, binding: Mapping[str, Any] | None = None) -> dict[str, str]:
    site_packages = (Path(binding["python"]["site_packages"]) if binding else EXPLICIT_SITE_PACKAGES).resolve(strict=True)
    return {
        "COMSOL_JAVA_HOME": str((Path(binding["jdk"]["home"]) if binding else JAVA11).resolve(strict=True)),
        "JAVA_HOME": str((Path(binding["jdk"]["home"]) if binding else JAVA11).resolve(strict=True)),
        "COMSOL_PROJECT_ROOT": str(work / "project"),
        "COMSOL_SERVER_MCP_HOME": str(work / "mcp-home"),
        "COMSOL_MCP_TRUSTED_CODE": "1",
        "COMSOL_MCP_HOST_CONTROL": "1",
        "COMSOL_MCP_ISOLATION_RECEIPT": str(work / "isolation_receipt.json"),
        "COMSOL_SERVER_VERSION": "6.4.0.293",
        "JAVA_TOOL_OPTIONS": JAVA_TOOL_OPTIONS,
        "PYTHONPATH": os.pathsep.join((str(repo.resolve(strict=True)), str(site_packages))),
    }


def _verify_execution_environment(work: Path, repo: Path, binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    expected = _expected_child_environment(work, repo, binding) if binding else _expected_child_environment(work, repo)
    mismatches = {key: {"expected": value, "observed": os.environ.get(key)}
                  for key, value in expected.items()
                  if os.environ.get(key) != value}
    if mismatches:
        raise CandidateError("candidate subprocess environment does not match its private COMSOL/JVM binding: " +
                             json.dumps(mismatches, ensure_ascii=False))
    return {"status": "ISOLATED_CANDIDATE_ENVIRONMENT_MATCHED",
            "environment_variables": {key: value for key, value in expected.items()
                                       if key != "JAVA_TOOL_OPTIONS"},
            "omitted_legacy_shared_runtime_variables": ["COMSOL_ROOT", "COMSOL_PREFS_DIR"],
            "java_tool_options_sha256": _sha_bytes(JAVA_TOOL_OPTIONS.encode()),
            "parent_process_environment_mutated": False,
            "worker_java_tool_options_source": "inherited from this exact isolated candidate process by PersistentJavaWorker Popen env copy",
            "server_java_tool_options_source": "explicit env=dict(os.environ) on exact COMSOL server Popen"}


def _verify_frozen_runtime(repo: Path, freeze: Mapping[str, Any],
                           install: Path, jdk: Path) -> dict[str, Any]:
    from comsol_mcp._java_worker import JavaWorkerPaths

    mode = freeze.get("execution_mode", EXECUTION_MODE_SETUP14)
    budget, _, _ = _execution_contract(mode)
    if freeze.get("budget") != budget:
        raise CandidateError("frozen runtime budget differs from the candidate execution mode")
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
    binding = freeze.get("fresh_runtime_binding")
    if binding is not None:
        fresh_runtime.validate(binding, repo=repo, budget=budget, install=install, jdk=jdk)
        compiled_root = Path(freeze["candidate_evidence_dir"]) / "offline_compile/classes"
        actual_classes = {}
        for path in sorted(compiled_root.rglob("*.class")):
            raw = fresh_runtime.read_owned(path)
            actual_classes[path.relative_to(compiled_root).as_posix()] = {"bytes": len(raw), "sha256": _sha_bytes(raw)}
        expected_classes = {row["path"]: {"bytes": row["bytes"], "sha256": row["sha256"]} for row in recorded.get("classes", [])}
        if not expected_classes or actual_classes != expected_classes:
            raise CandidateError("fresh frozen Java compile classes changed or closure incomplete")
    if binding is None and Path(sys.executable).resolve(strict=True) != EXPECTED_PYTHON.resolve(strict=True):
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


def _check_process_size(work: Path, *, max_single_mph_bytes: int = MAX_SINGLE_MPH_BYTES,
                        max_total_output_bytes: int = MAX_OUTPUT_BYTES) -> dict[str, Any]:
    if (type(max_single_mph_bytes) is not int or max_single_mph_bytes < 0 or
            type(max_total_output_bytes) is not int or max_total_output_bytes < 0):
        raise CandidateError("output byte budgets must be non-negative integers")
    project = work / "project/science"
    output_dirs = ((project / "outputs", "outputs"),
                   (project / "g2_artifacts/registered", "g2_artifacts/registered"))
    rows: list[dict[str, Any]] = []
    total = 0
    for directory, scope in output_dirs:
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise CandidateError(f"project output area became an alias or non-directory: {scope}")
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise CandidateError(f"project output area contains a symlink/non-file: {scope}")
            size = path.stat().st_size
            total += size
            rows.append({"path": path.relative_to(project).as_posix(),
                         "size_bytes": size, "sha256": _sha_file(path)})
            if path.suffix.lower() == ".mph" and size > max_single_mph_bytes:
                raise CandidateError(f"MPH exceeds the frozen per-file cap: {path.relative_to(project)}")
    if total > max_total_output_bytes:
        raise CandidateError("aggregate project outputs exceed the frozen candidate cap")
    return {"files": rows, "total_bytes": total,
            "max_single_mph_bytes": max_single_mph_bytes,
            "max_total_project_output_bytes": max_total_output_bytes}


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


def _retire_owned_server_without_worker(*, daemon: Any, server: Any,
                                        monitor: SampledRssMonitor | None,
                                        project_id: str, server_session: Mapping[str, Any],
                                        run_dir: Path, timeout_seconds: float) -> dict[str, Any]:
    ledger = _terminal_campaign_ledger(daemon, project_id)
    _write_new(run_dir / "pre_retirement_ledger.json", ledger)
    if ledger.get("safe_to_retire") is not True:
        raise CandidateError("durable ledger is not terminal before no-Worker Server stop")
    key = (project_id, str(server_session["session_id"]))
    if (daemon._session_worker_handles.get(key) is not None or
            key in daemon._session_backends or
            daemon._session_server_handles.get(key) is not server.managed_handle):
        raise CandidateError("public no-Worker session.stop lacks the exact isolated owned-server lifecycle")
    if monitor is not None:
        monitor.set_worker_required_live(False)
        monitor.sample_once()
        monitor.stop()
    response = _direct_dispatch(
        daemon, run_dir, operation="session.stop",
        arguments={"session_id": str(server_session["session_id"]),
                   "authorization_ref": server.launcher.authorization_ref},
        project_id=project_id, session_id=str(server_session["session_id"]),
        request_label="session_stop_owned_server_without_worker",
        timeout_seconds=min(30.0, timeout_seconds))
    data = response.get("data") if isinstance(response, Mapping) else None
    evidence = data.get("stop_evidence") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping) or
            data.get("state") != "STOPPED" or not isinstance(evidence, Mapping) or
            evidence.get("server_stopped") is not True or evidence.get("child_reaped") is not True or
            evidence.get("listener_absent") is not True or evidence.get("sigkill_used") is not False or
            evidence.get("pid") != server.proc.pid or
            evidence.get("birth") != server.managed_handle.process_identity.birth):
        raise CandidateError("public session.stop failed to retire the exact disconnected owned Server")
    _write_new(run_dir / "server_stop_receipt.json", dict(evidence))
    daemon.close()
    return {"status": "PUBLIC_SESSION_STOP_REAPED_WITHOUT_WORKER",
            "pre_retirement_ledger_sha256": _sha_file(run_dir / "pre_retirement_ledger.json"),
            "server_stop_sha256": _sha_file(run_dir / "server_stop_receipt.json")}


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
    stop_response = _direct_dispatch(
        daemon, run_dir, operation="session.stop",
        arguments={"session_id": str(session["session_id"]),
                   "authorization_ref": server.launcher.authorization_ref},
        project_id=project_id, session_id=str(session["session_id"]),
        request_label="session_stop_owned_server", timeout_seconds=min(30.0, timeout))
    stop_data = stop_response.get("data") if isinstance(stop_response, Mapping) else None
    stopped = stop_data.get("stop_evidence") if isinstance(stop_data, Mapping) else None
    if (stop_response.get("success") is not True or not isinstance(stopped, Mapping) or
            stop_data.get("state") != "STOPPED" or stopped.get("server_stopped") is not True or
            stopped.get("child_reaped") is not True or stopped.get("listener_absent") is not True or
            stopped.get("pid") != server.proc.pid or
            stopped.get("birth") != server.managed_handle.process_identity.birth or
            stopped.get("sigkill_used") is not False):
        raise CandidateError("public session.stop did not retire the exact W24 ManagedServerHandle")
    _write_new(run_dir / "server_stop_receipt.json", dict(stopped))
    close_status = "RETURNED"
    try:
        daemon.close()
    except BaseException as exc:
        close_status = f"FAILED:{type(exc).__name__}:{exc}"
        raise CandidateError("ControlDaemon close failed after public owned-server stop") from exc
    return {"status": "EXACT_WORKER_RETIRED_PUBLIC_SESSION_STOP_REAPED",
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
                  reason: str, stop_event: threading.Event | None = None,
                  first_cause: BaseException | None = None) -> dict[str, Any] | None:
    """Retain/fence original objects before diagnostics, without native cleanup."""
    state = _capture_hold_handles(server, daemon, first_cause)
    _fence_hold_handles(state, monitor)
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
            "server_owner_binding_resolution": getattr(server, "owner_binding_resolution", None),
            "server_popen": {"pid": server.proc.pid if server.proc is not None else None,
                             "birth": server.server_snapshot.get("birth") if server.server_snapshot else None,
                             "launch_command_sha256": server.server_snapshot.get("launch_command_sha256") if server.server_snapshot else None,
                             "post_exec_identity": getattr(server, "server_postexec_identity", None),
                             "returncode": server.proc.poll() if server.proc is not None else None,
                             "port": server.port},
            "rss_stop_reason": (monitor.stop_reason if monitor is not None
                                else "RSS_MONITOR_HANDLE_UNAVAILABLE"),
            "reason": reason,
            "policy": "No automatic retry, Worker retirement, daemon close, server stop, or restart while UNKNOWN. Reconcile the original durable operation and process handles outside this run before any future action."}
    try:
        _write_new(run_dir / "unknown_handle_hold.json", hold)
        _append(run_dir / "events.jsonl", {"event": "unknown_handle_hold_entered",
                                        "at_utc": datetime.now(timezone.utc).isoformat(),
                                        "receipt": str(run_dir / "unknown_handle_hold.json")})
    except BaseException as exc:
        _hold_diagnostic(state, "unknown_hold_receipt", exc)
    return _hold_forever_with_handles(monitor, server=server, daemon=daemon,
        first_cause=first_cause, run_dir=run_dir, stop_event=stop_event)


def _new_static_shape_managed_runner(*, daemon: Any, project_id: str,
                                    project_workspace: Path,
                                    project_create_response: Mapping[str, Any],
                                    parent_binding: Any,
                                    source_manifest: Mapping[str, Any],
                                    setup_runner: Any, evidence_dir: Path,
                                    timeout_s: float) -> Any:
    """Reserve a fresh private evidence child, then construct the real runner."""
    from tools.run_native_w24_static_shape_setup import StaticShapeManagedRunner

    # This is an owned leaf for one exact runner construction. Never adopt or
    # reuse a path left by another activity, even if it is already a directory.
    evidence_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    return StaticShapeManagedRunner(
        daemon=daemon, project_id=project_id, project_workspace=project_workspace,
        project_create_response=project_create_response, parent_binding=parent_binding,
        source_manifest=source_manifest, setup_runner=setup_runner,
        evidence_dir=evidence_dir, timeout_s=timeout_s)


def execute_candidate(*, repo: Path, evidence: Path, work: Path,
                      reviewed_sha256: str,
                      install: Path = INSTALL_ROOT, jdk: Path = JAVA11,
                      schema_probe_only: bool = False) -> dict[str, Any]:
    """Execute the exact frozen setup14 or one-shot zero-study schema mode.

    Native entry requires an explicit candidate SHA, a new private work path,
    the exact environment contract, a fresh scoped inventory, and the existing
    owner/UNKNOWN cleanup guards.  Schema mode dispatches one registered model
    and one trusted metadata-only Java entrypoint.
    """
    if type(schema_probe_only) is not bool:
        raise CandidateError("schema_probe_only execution flag must be an exact Boolean")
    # Validate authority/source before isolation and before any native process birth.
    freeze_raw = json.loads(fresh_runtime.read_owned(evidence / FREEZE_FILE), object_pairs_hook=fresh_runtime._pairs)
    frozen_mode = freeze_raw.get("execution_mode", EXECUTION_MODE_SETUP14)
    mode = EXECUTION_MODE_SCHEMA_ONLY if schema_probe_only else EXECUTION_MODE_SETUP14
    if frozen_mode != mode:
        raise CandidateError("execute flag differs from the reviewed frozen execution mode")
    budget, routes, planned_slots = _execution_contract(mode)
    binding = freeze_raw.get("fresh_runtime_binding")
    if binding is not None:
        fresh_runtime.validate(binding, repo=repo, budget=budget, install=install, jdk=jdk)
        fresh_runtime.scoped_path(binding, evidence)
        fresh_runtime.scoped_path(binding, work, absent=True)
        if work != Path(binding["namespace"])/"work":
            raise CandidateError("fresh work must equal immutable namespace/work")
    python_isolation = configure_archive_python(repo, binding=binding) if binding else configure_archive_python(repo)
    freeze = verify_candidate(repo=repo, evidence=evidence,
                              reviewed_sha256=reviewed_sha256,
                              schema_probe_only=schema_probe_only)
    if freeze.get("source", {}).get("checkout_kind") != ("fresh_host_source_closure" if binding else "git_archive_plus_explicit_overlay"):
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
            (binding is None and not str(work).startswith(WORK_PREFIX)) or
            not work.resolve().is_relative_to(Path("/private/tmp"))):
        raise CandidateError("candidate work path must be new and below the frozen private temporary prefix")
    environment_receipt = _verify_execution_environment(work, repo, binding) if binding else _verify_execution_environment(work, repo)
    controller_context = (_controller_inventory_context(
        repo, evidence, work, binding, reviewed_sha256, budget=budget,
        schema_probe_only=schema_probe_only)
                          if binding is not None else None)
    execution_plan = (_build_exact_execute_plan_from_binding(
        repo=repo, evidence=evidence, work=work,
        reviewed_sha256=reviewed_sha256, binding=binding,
        schema_probe_only=schema_probe_only)
        if binding is not None else None)
    host_control_authorization_ref = (
        _w24_owned_server_authorization_ref(
            reviewed_sha256=reviewed_sha256, binding=binding, plan=execution_plan)
        if binding is not None else None)
    if binding is None and work.parent.resolve(strict=True) != Path("/private/tmp").resolve(strict=True):
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
    control_home = work / "mcp-home"
    worker_root = control_home / "session-runtime-state/sessions"
    server = W24StaticShapeSetupServer(work, run_dir, event_log=events_path, jdk=jdk)

    def observe_core_server_birth(payload: Mapping[str, Any]) -> None:
        server.birth_monotonic = time.monotonic()
        _start_rss_monitor_from_core_observation(
            monitor_box, payload, worker_root, events_path)

    owned_server_launcher = W24SetupOwnedServerLauncher(
        install_root=install, evidence=run_dir,
        installation_binding=(binding.get("comsol") if isinstance(binding, Mapping) else None),
        observer=server, on_birth_observation=observe_core_server_birth)
    server.launcher = owned_server_launcher
    daemon = None
    gate: BudgetedSetupDaemon | None = None
    monitor: SampledRssMonitor | None = None
    project_id: str | None = None
    project_response: dict[str, Any] | None = None
    project_creation: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    server_session: dict[str, Any] | None = None
    session_start_request: tuple[str, str] | None = None
    worker_state = "NEVER_DISPATCHED"
    setup_receipts: list[dict[str, Any]] = []
    model_tags: set[str] = set()
    total_outputs = 0
    status = "FAILED_BEFORE_NATIVE_BIRTH"
    error: dict[str, Any] | None = None
    first_cause: BaseException | None = None
    cleanup: dict[str, Any] | None = None
    schema_probe_result: dict[str, Any] | None = None
    schema_probe_validation: dict[str, Any] | None = None
    slots = planned_slots
    receipt_base: dict[str, Any] = {
        "schema": "W24_STATIC_SHAPE_SETUP_CAMPAIGN_RUN_V1",
        "execution_mode": mode,
        "status": "RUNNING_SCHEMA_ONLY" if schema_probe_only else "RUNNING_SETUP_ONLY",
        "candidate_sha256": reviewed_sha256,
        "base_commit": None if binding else BASE_COMMIT,
        "activity_id": binding["activity_id"] if binding else None,
        "runtime_binding_sha256": binding["binding_sha256"] if binding else None,
        "source_closure_sha256": freeze["source"]["source_closure_sha256"],
        "evidence_dir": str(run_dir.resolve()), "work_dir": str(work.resolve()),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_isolation": python_isolation,
        "runtime_recheck": runtime_recheck,
        "runtime_module_origins": origins,
        "route_allowlist": routes,
        "resource_budget": budget,
        "planned_slots": slots,
        "planned_slot_count": len(slots),
        "study_run_calls": 0, "solver_calls": 0,
        "physics_property_set_calls": 0 if schema_probe_only else None,
        "native_scientific_acceptance": "NOT_RUN",
        "future_science_binding_policy": budget["future_science_binding_policy"],
    }
    _append(events_path, {"event": "candidate_execution_start",
                          "at_utc": datetime.now(timezone.utc).isoformat(),
                          "candidate_sha256": reviewed_sha256,
                          "execution_mode": mode,
                          "planned_slots": len(slots)})


    try:
        if mode == EXECUTION_MODE_SETUP14 and (
                len(slots) != 14 or len({row["configuration_id"] for row in slots}) != 7):
            raise CandidateError("frozen variant-aware setup matrix must contain exactly seven configs by two cases")
        if mode == EXECUTION_MODE_SCHEMA_ONLY and (slots or budget.get("schema_probe_calls") != 1):
            raise CandidateError("frozen schema-only mode must contain zero setup slots and exactly one probe call")
        if work.resolve() != work or (binding is None and work.parent.resolve(strict=True) != Path("/private/tmp").resolve(strict=True)):
            raise CandidateError("candidate work path is not canonical private temporary storage")
        shadow_receipt = server.prepare_shadow()
        _append(events_path, {"event": "private_shadow_prepared",
                              "receipt_sha256": _json_hash(shadow_receipt)})
        from comsol_mcp._control_daemon import ControlDaemon
        daemon = ControlDaemon(control_home, project_root=server.project,
                               session_server_launcher=owned_server_launcher)
        owned_server_launcher.bind_control_home(control_home)
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
        owned_server_launcher.bind_reviewed_project(
            project_id=project_id, workspace=workspace,
            authorized_root=server.project, candidate_sha256=reviewed_sha256,
            activity_id=str(binding["activity_id"]) if binding else _sha_bytes(str(reviewed_sha256).encode()),
            authorization_ref=host_control_authorization_ref or
                f"w24-owned-server:test:{reviewed_sha256}:{'0' * 128}")
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
            inventory = (fresh_scoped_inventory(controller_context=controller_context)
                         if controller_context is not None else fresh_scoped_inventory())
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

        from comsol_mcp._runtime_installation import runtime_id_for_root
        runtime_id = runtime_id_for_root(install)
        start_request_id, start_idempotency_key = _request_ids("session_start_owned_server")
        session_start_request = (start_request_id, start_idempotency_key)
        worker_state = "UNKNOWN_SERVER_START"
        try:
            start_response = _direct_dispatch(
                daemon, run_dir, operation="session.start",
                arguments={"runtime_id": runtime_id, "options": {}, "resources": {}},
                project_id=project_id, request_label="session_start_owned_server",
                timeout_seconds=45.0, request_id=start_request_id,
                idempotency_key=start_idempotency_key)
        except OutcomeUnknown as exc:
            try:
                server.bind_from_original_daemon(
                    daemon, project_id, request_id=start_request_id,
                    idempotency_key=start_idempotency_key)
            except BaseException as bind_exc:
                _hold_diagnostic({"diagnostics": []}, "session_start_owner_binding", bind_exc)
            status = "UNKNOWN_SESSION_START"
            error = {"type": type(exc).__name__, "message": str(exc),
                     "stage": "session.start", "outcome": "UNKNOWN_NO_REPLAY",
                     "request_id": start_request_id,
                     "idempotency_key": start_idempotency_key}
            raise
        start_data = start_response.get("data") if isinstance(start_response, Mapping) else None
        if (start_response.get("success") is not True and
                start_response.get("execution_state_unknown") is not True and
                isinstance(start_data, Mapping) and
                start_data.get("server_birth_performed") is False):
            worker_state = "NEVER_DISPATCHED"
            status = "SESSION_START_FAILED_BEFORE_SERVER_BIRTH"
            raise CandidateError("public session.start proved refusal before any server child birth")
        try:
            server_session = _owned_server_start_identity(
                start_response, project_id=project_id, runtime_id=runtime_id)
            server.bind_managed_handle(
                owned_server_launcher, project_id, server_session["session_id"],
                daemon._session_server_handles[(project_id, server_session["session_id"])])
            if (server.managed_handle.process_identity is None or
                    server_session["server_process_identity"].get("pid") != server.proc.pid or
                    server_session["server_process_identity"].get("birth") !=
                    server.managed_handle.process_identity.birth or
                    server_session["endpoint"] != {"host": "127.0.0.1", "port": server.port}):
                raise CandidateError("session.start response does not bind the exact original Core handle")
        except BaseException as exc:
            try:
                server.bind_from_original_daemon(
                    daemon, project_id, request_id=start_request_id,
                    idempotency_key=start_idempotency_key)
            except BaseException:
                pass
            status = "UNKNOWN_SESSION_START"
            worker_state = "UNKNOWN_SERVER_START"
            error = {"type": type(exc).__name__, "message": _hold_text(exc),
                     "stage": "session.start_identity", "outcome": "UNKNOWN_NO_REPLAY"}
            raise
        listener = server.start_and_verify_listener()
        monitor = monitor_box.get("monitor")
        if monitor is None or server.birth_monotonic is None or type(server.port) is not int:
            worker_state = "UNKNOWN_SERVER_START"
            status = "UNKNOWN_SESSION_START"
            raise CandidateError("Core session.start returned without exact original-birth RSS monitoring")
        deadline = server.birth_monotonic + budget["wall_seconds_from_server_birth_including_cleanup"]
        _write_new(run_dir / "server_birth_listener.json", {
            "schema": "W24_SETUP_SERVER_BIRTH_LISTENER_V1",
            "status": "CORE_MANAGED_HANDLE_POSTEXEC_JAVA_AND_LOOPBACK_LISTENER_VERIFIED",
            "project_id": project_id, "session_id": server_session["session_id"],
            "server_process_identity": server_session["server_process_identity"],
            "process": {"pid": server.proc.pid,
                        "birth": server.server_snapshot.get("birth"),
                        "start_epoch_ms": server.server_snapshot.get("start_epoch_ms"),
                        "uid": server.server_snapshot.get("uid"),
                        "launch_command_sha256": server.server_snapshot.get("launch_command_sha256"),
                        "post_exec_command_sha256": server.server_postexec_identity.get("post_exec_command_sha256"),
                        "java_executable": server.java_executable_identity,
                        "post_exec_identity": server.server_postexec_identity},
            "listener": listener, "command": server.launch_command,
            "max_wall_deadline_monotonic": deadline,
            "max_wall_seconds_including_cleanup": budget["wall_seconds_from_server_birth_including_cleanup"],
            "resource_budget": budget,
        })
        _append(events_path, {"event": "server_birth_listener_confirmed",
                              "pid": server.proc.pid, "port": server.port,
                              "birth": server.server_snapshot.get("birth"),
                              "launch_command_sha256": server.server_snapshot.get("launch_command_sha256"),
                              "post_exec_command_sha256": server.server_postexec_identity.get("post_exec_command_sha256")})
        status = "RUNNING_SCHEMA_ONLY" if schema_probe_only else "RUNNING_SETUP_ONLY"
        server_java_options = server.require_server_java_options()
        _append(events_path, {"event": "server_jvm_options_confirmed",
                              "receipt_sha256": _json_hash(server_java_options)})
        g2_isolation_receipt = server.write_g2_isolation_receipt()
        _append(events_path, {"event": "g2_server_isolation_receipt_confirmed",
                              "receipt_sha256": g2_isolation_receipt["sha256"],
                              "worker_born": False})
        if monitor.stop_reason:
            status = "STOPPED_RSS_OR_MONITOR_ADMISSION_BEFORE_WORKER"
            raise CandidateError(f"resource watchdog blocked Worker birth: {monitor.stop_reason}")

        connect_timeout = _remaining_admission_rpc(deadline, float(RPC_CAP_SECONDS))
        worker_state = "UNKNOWN_CONNECT_ATTEMPT"
        try:
            connect = _direct_dispatch(
                daemon, run_dir, operation="session.connect",
                arguments={"runtime_id": runtime_id,
                           "session_id": server_session["session_id"],
                           "endpoint": dict(server_session["endpoint"])},
                project_id=project_id, session_id=server_session["session_id"],
                request_label="session_connect",
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
        if session.get("session_id") != server_session.get("session_id"):
            worker_state = "CONNECTED_UNVERIFIED"
            status = "UNKNOWN_SESSION_CONNECT"
            raise CandidateError("session.connect returned a different session.start lifecycle identity")
        worker_state = "CONNECTED_UNVERIFIED"
        context, worker, worker_identity, worker_state_dir = _worker_identity_from_context(
            daemon, project_id, session)
        _capture_hold_handles(server, daemon)
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
        worker_echo_diagnostic = _verify_worker_java_options(worker_state_dir)
        worker_jvm_limits = _verify_worker_jvm_limits(
            worker, worker_identity, worker_birth_ms, jdk,
            evidence_path=run_dir / "worker_jvm_limits_readback.json")
        worker_java_options = {"status": "WORKER_EFFECTIVE_JVM_LIMITS_VERIFIED",
                               "log_echo_diagnostic": worker_echo_diagnostic,
                               "effective_jvm_limits": worker_jvm_limits}
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

        from tools.w24_native_epoch_capability import _capture_live_worker_binding
        receiver_backend = context.backend
        receiver_anchor = _capture_live_worker_binding(receiver_backend, project_id=project_id,
            session_id=session["session_id"])
        gate = BudgetedSetupDaemon(daemon, monitor=monitor,
            deadline_monotonic=deadline, events_path=events_path,
            workspace=workspace, project_id=project_id,
            schema_probe_only=schema_probe_only,
            selection_diagnostic_dir=run_dir / "static_shape_managed")
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
                                                   session=session, receiver_anchor=receiver_anchor, backend=receiver_backend)
        except CandidateError:
            gate.unknown = True
            status = "STOPPED_UNKNOWN"
            raise
        if schema_probe_only:
            if not gate or slots:
                raise CandidateError("schema-only route requires the existing owner gate and an empty setup matrix")
            source_rows = source_manifest.get("sources") if isinstance(source_manifest, Mapping) else None
            if not isinstance(source_rows, Mapping):
                raise CandidateError("schema-only route lacks the prepared project source manifest")
            frozen_rows = freeze.get("source", {}).get("source_files")
            if not isinstance(frozen_rows, Mapping):
                raise CandidateError("schema-only route lacks the frozen source map")
            for role, relative in (("fixture", "tools/java/W24StaticShapeFixture.java"),
                                   ("readback", "tools/java/W24StaticShapeReadback.java")):
                project_row = source_rows.get(role)
                frozen_row = frozen_rows.get(relative)
                if (not isinstance(project_row, Mapping) or not isinstance(frozen_row, Mapping) or
                        project_row.get("sha256") != frozen_row.get("sha256")):
                    raise CandidateError(f"schema-only project {role} source differs from the frozen candidate")
            if monitor.stop_reason:
                status = "STOPPED_RSS_OR_MONITOR_ADMISSION_BEFORE_SCHEMA_PROBE"
                raise CandidateError(f"resource watchdog blocked the schema probe: {monitor.stop_reason}")
            request_id, idempotency_key = _request_ids("schema-only-property-read")
            schema_response = gate.dispatch({
                "operation": "operation_call",
                "arguments": {"operation_id": "code.execute_java", "arguments": {
                    "source_artifact": "W24StaticShapeFixture.java",
                    "entrypoint": "W24StaticShapeFixture#run",
                    "arguments": {"action": "property_schema_only"},
                    "mode": "trusted"}},
                "execution": {"project_id": project_id,
                              "session_id": session["session_id"],
                              "model_ref": dict(parent_binding.model_ref),
                              "expected_revision": parent_binding.revision,
                              "request_id": request_id,
                              "idempotency_key": idempotency_key,
                              "rpc_timeout_s": float(RPC_CAP_SECONDS),
                              "queue_timeout_s": 60.0,
                              "execution_timeout_s": float(RPC_CAP_SECONDS)}})
            response_data = schema_response.get("data")
            response_envelope = response_data.get("readback") if isinstance(response_data, Mapping) else None
            schema_probe_result = response_envelope.get("readback") if isinstance(response_envelope, Mapping) else None
            schema_probe_validation = _validate_schema_probe_result(
                schema_probe_result, expected_model_tag=parent_binding.model_tag)
            if gate.schema_probe_completed is not True:
                gate.unknown = True
                raise CandidateError("the single schema-only Worker call did not close its one-call gate")
            output_inventory = _check_process_size(
                work, max_single_mph_bytes=budget["max_single_unsolved_mph_bytes"],
                max_total_output_bytes=budget["max_total_project_output_bytes"])
            if output_inventory["total_bytes"] != 0:
                raise CandidateError("schema-only action produced a project output artifact")
            _write_new(run_dir / "schema_only_property_result.json", {
                "schema": "W24_SCHEMA_ONLY_PROPERTY_RESULT_V1",
                "status": schema_probe_validation["status"],
                "candidate_sha256": reviewed_sha256,
                "source_closure_sha256": freeze["source"]["source_closure_sha256"],
                "model_tag": parent_binding.model_tag,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "validated": schema_probe_validation,
                "observed": schema_probe_result,
                "project_output_inventory": output_inventory,
                "study_run_submissions": 0, "solver_calls": 0,
                "physics_property_set_calls": 0,
                "geometry_length_unit_setter_calls": 1,
                "geometry_axisymmetric_setter_calls": 1,
                "geometry_build_calls": 0,
                "native_acceptance": "NOT_RUN"})
            _append(events_path, {"event": "schema_only_property_result_persisted",
                                  "request_id": request_id,
                                  "source_closure_sha256": freeze["source"]["source_closure_sha256"],
                                  "property_count": schema_probe_validation["property_count"],
                                  "study_run_submissions": 0, "solver_calls": 0,
                                  "physics_property_set_calls": 0,
                                  "geometry_build_calls": 0,
                                  "native_acceptance": "NOT_RUN"})
            status = "SCHEMA_ONLY_DIAGNOSTIC_COMPLETE_NOT_SOLVED"
        else:
            from tools import run_native_w24_cure_preflight as preflight_helpers
            setup_adapter = preflight_helpers
            managed_runner = _new_static_shape_managed_runner(
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
                        workspace=workspace, expected_sources=source_manifest.get("sources", {}),
                        receiver_anchor=receiver_anchor, backend=receiver_backend)
                    if any(tag in model_tags for tag in validation["model_tags"]):
                        raise CandidateError("two preregistered setup slots resolved to a duplicate native model tag")
                    model_tags.update(validation["model_tags"])
                    total_outputs += validation["accounted_output_bytes"]
                    if total_outputs > MAX_OUTPUT_BYTES:
                        raise CandidateError("aggregate saved and registered 14-slot MPH bytes exceed the 10 GiB candidate cap")
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
                                          "registered_artifact": validation["registered_artifact"],
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
                                           "registered_artifact": validation["registered_artifact"],
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
        first_cause = first_cause or exc
        error = {"type": type(exc).__name__, "message": _hold_text(exc),
                 "traceback": traceback.format_exc(limit=20)}
        if isinstance(exc, JvmLimitsReadbackHold):
            status = "HOLD_EFFECTIVE_JVM_LIMITS_READBACK"
        elif status in {"RUNNING_SETUP_ONLY", "RUNNING_SCHEMA_ONLY"}:
            status = "FAILED_SETUP_ONLY" if mode == EXECUTION_MODE_SETUP14 else "FAILED_SCHEMA_ONLY"

    monitor = monitor or monitor_box.get("monitor")
    is_unknown = (worker_state in {"UNKNOWN_SERVER_START", "UNKNOWN_CONNECT_ATTEMPT", "CONNECTED_UNVERIFIED", "UNKNOWN"} or
                  (gate is not None and gate.unknown) or
                  isinstance(first_cause, JvmLimitsReadbackHold) or
                  status in {"UNKNOWN_SESSION_START", "UNKNOWN_SESSION_CONNECT", "STOPPED_UNKNOWN", "STOPPED_LEDGER_ACTIVE_OR_UNKNOWN"})
    if is_unknown:
        try:
            _unknown_hold(daemon=daemon, server=server,
                          monitor=monitor,
                          run_dir=run_dir, work=work, project_id=project_id,
                          session=session, candidate_sha256=reviewed_sha256,
                          reason=(error or {}).get("message", status), first_cause=first_cause)
            status = "FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL"
        except BaseException as exc:
            error = {**(error or {}), "unknown_hold_error":
                     f"{type(exc).__name__}: {_hold_text(exc)}"}
            status = "UNKNOWN_PRESERVED_CLEANUP_NOT_AUTHORIZED_OR_UNPROVEN"
            # Preserve all in-process handles; an UNKNOWN result must not fall
            # through to daemon.close() or server termination.
            _hold_forever_with_handles(monitor, server=server, daemon=daemon, first_cause=first_cause, run_dir=run_dir)
            status = "FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL"
    elif daemon is not None and worker_state == "CONNECTED" and session is not None:
        try:
            if monitor is None:
                raise CandidateError("connected Worker has no live resource monitor")
            cleanup = _retire_worker_and_server(
                daemon=daemon, server=server, monitor=monitor,
                project_id=str(project_id), session=session, run_dir=run_dir,
                deadline_monotonic=(server.birth_monotonic or time.monotonic()) +
                    budget["wall_seconds_from_server_birth_including_cleanup"])
        except BaseException as exc:
            error = {**(error or {}), "cleanup_error": f"{type(exc).__name__}: {_hold_text(exc)}"}
            status = "CONNECTED_STATE_PRESERVED_CLEANUP_BLOCKED"
            first_cause = first_cause or exc
            try:
                _unknown_hold(daemon=daemon, server=server, monitor=monitor,
                              run_dir=run_dir, work=work, project_id=str(project_id),
                              session=session, candidate_sha256=reviewed_sha256,
                              reason=error["cleanup_error"], first_cause=first_cause)
            except BaseException as hold_exc:
                error = {**error, "unknown_hold_error": f"{type(hold_exc).__name__}: {_hold_text(hold_exc)}"}
                _hold_forever_with_handles(monitor, server=server, daemon=daemon,
                    first_cause=first_cause, run_dir=run_dir)
            status = "FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL"
    else:
        if (daemon is not None and project_id is not None and
                server_session is not None and server.managed_handle is not None and
                worker_state in {"NEVER_DISPATCHED", "NEVER_CONNECTED"}):
            try:
                cleanup = _retire_owned_server_without_worker(
                    daemon=daemon, server=server, monitor=monitor,
                    project_id=project_id, server_session=server_session,
                    run_dir=run_dir,
                    timeout_seconds=max(1.0, min(30.0, budget["wall_seconds_from_server_birth_including_cleanup"])))
            except BaseException as exc:
                error = {**(error or {}), "public_session_stop_error":
                         f"{type(exc).__name__}: {_hold_text(exc)}"}
                status = "SERVER_HANDLE_PRESERVED_CLEANUP_BLOCKED"
                _unknown_hold(daemon=daemon, server=server, monitor=monitor,
                              run_dir=run_dir, work=work, project_id=project_id,
                              session=server_session, candidate_sha256=reviewed_sha256,
                              reason=error["public_session_stop_error"], first_cause=first_cause or exc)
        elif daemon is not None:
            try:
                daemon.close()
                cleanup = {"control_daemon_close": "RETURNED"}
            except BaseException as exc:
                error = {**(error or {}), "control_daemon_close_error":
                         f"{type(exc).__name__}: {_hold_text(exc)}"}
                status = "PRECONNECT_DAEMON_CLOSE_BLOCKED"

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
                                   "launch_command_sha256": server.server_snapshot.get("launch_command_sha256") if server.server_snapshot else None,
                                   "post_exec_identity": server.server_postexec_identity,
                                   "port": server.port,
                                   "returncode": server.proc.poll()} if server.proc is not None else None),
               "resource_limits": budget,
               "resource_summary": (_check_process_size(
                   work, max_single_mph_bytes=budget["max_single_unsolved_mph_bytes"],
                   max_total_output_bytes=budget["max_total_project_output_bytes"])
                   if work.exists() else None),
               "schema_probe_result": schema_probe_result,
               "schema_probe_validation": schema_probe_validation,
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


def _build_exact_execute_plan_from_binding(*, repo: Path, evidence: Path, work: Path,
                                           reviewed_sha256: str,
                                           binding: Mapping[str, Any],
                                           schema_probe_only: bool = False) -> dict[str, Any]:
    fresh_runtime.scoped_path(binding, work, absent=True)
    if work != Path(binding["namespace"])/"work":
        raise CandidateError("fresh work must equal immutable namespace/work")
    if type(schema_probe_only) is not bool:
        raise CandidateError("execute-plan schema mode must be an exact Boolean")
    argv = [binding["python"]["entry"], "-S", "-m", "tools.run_native_w24_static_shape_setup_campaign", "execute",
            "--evidence", str(evidence), "--work", str(work), "--reviewed-candidate-sha256", reviewed_sha256,
            "--comsol-root", binding["comsol"]["root"], "--jdk11", binding["jdk"]["home"]]
    if schema_probe_only:
        argv.append("--schema-only")
    return {"argv": argv, "argv_sha256": _json_hash(argv), "cwd": str(repo.resolve(strict=True)),
            "environment": _expected_child_environment(work, repo, binding), "candidate_sha256": reviewed_sha256,
            "runtime_binding_sha256": binding["binding_sha256"], "activity_id": binding["activity_id"],
            "require_new_external_root_approval_and_once": True, "native": "NOT_RUN"}


def fresh_exact_execute_plan(*, repo: Path, evidence: Path, work: Path,
                             reviewed_sha256: str) -> dict[str, Any]:
    """No native action; Main binds these bytes to a new external once wrapper."""
    freeze = verify_candidate(repo=repo, evidence=evidence, reviewed_sha256=reviewed_sha256)
    binding = freeze.get("fresh_runtime_binding")
    if binding is None:
        raise CandidateError("fresh exact entry requires a fresh host runtime binding")
    schema_probe_only = freeze.get("execution_mode", EXECUTION_MODE_SETUP14) == EXECUTION_MODE_SCHEMA_ONLY
    return _build_exact_execute_plan_from_binding(
        repo=repo, evidence=evidence, work=work,
        reviewed_sha256=reviewed_sha256, binding=binding,
        schema_probe_only=schema_probe_only)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("export-archive", help="read-only archive of the published base plus this W24 overlay")
    archive.add_argument("--destination", required=True, type=Path)
    prepare = sub.add_parser("prepare", help="offline source audit and Java compile; starts no process")
    prepare.add_argument("--evidence", required=True, type=Path)
    prepare.add_argument("--runtime-authority", type=Path)
    prepare.add_argument("--schema-only", action="store_true",
                         help="freeze the one-call property-schema diagnostic instead of the 14-slot campaign")
    prepare.add_argument("--comsol-root", type=Path, default=INSTALL_ROOT)
    prepare.add_argument("--jdk11", type=Path, default=JAVA11)
    execute = sub.add_parser("execute", help="run only the separately reviewed setup-only candidate")
    execute.add_argument("--evidence", required=True, type=Path)
    execute.add_argument("--work", required=True, type=Path)
    execute.add_argument("--reviewed-candidate-sha256", required=True)
    execute.add_argument("--schema-only", action="store_true",
                         help="execute only the frozen one-call property-schema diagnostic")
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
                install=args.comsol_root, jdk=args.jdk11, runtime_authority=args.runtime_authority,
                schema_probe_only=args.schema_only)
            print(json.dumps({"status": result["status"],
                              "candidate_sha256": result["candidate_sha256"],
                              "execution_mode": result.get("execution_mode", EXECUTION_MODE_SETUP14),
                              "evidence_dir": result["candidate_evidence_dir"],
                              "source_closure_sha256": result["source"]["source_closure_sha256"],
                              "planned_setup_slots": len(result["ordered_setup_slots"]),
                              "schema_probe_calls": result["budget"].get("schema_probe_calls", 0),
                              "study_run_submissions": result["budget"]["study_run_submissions"],
                              "solver_calls": result["budget"]["solver_calls"]},
                             indent=2, ensure_ascii=False))
            return 0
        result = execute_candidate(repo=REPO, evidence=args.evidence,
            work=args.work, reviewed_sha256=args.reviewed_candidate_sha256,
            install=args.comsol_root, jdk=args.jdk11, schema_probe_only=args.schema_only)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        success_states = {"SETUP_14_OF_14_COMPLETE_SCIENCE_NOT_RUN",
                          "SCHEMA_ONLY_DIAGNOSTIC_COMPLETE_NOT_SOLVED"}
        return 0 if result.get("status") in success_states else 1
    except Exception as exc:
        print(json.dumps({"status": "CANDIDATE_GATE_FAILED",
                          "error_type": type(exc).__name__, "error": str(exc)},
                         indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
