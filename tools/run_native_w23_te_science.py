#!/usr/bin/env python3
"""Prepare or execute the reviewed W23 planar-TE native science checkpoint.

Preparation is offline: it imports the real managed runtime, checks the
production dependency environment, performs a real MCP stdio initialize and
tools/list, runs software negative controls, and compiles the Java fixture
against the installed COMSOL 6.4/JDK 11 API. Execution is separately gated by
a root approval receipt bound to the exact freeze hash. It is capped at four
actual ``Study.run`` Worker submissions, one owned server, one Worker, and a
birth-inclusive deadline with a cleanup reserve. It includes a core-aperture
capture-mechanism check, but not the later 3D/offset/angle/final-assembly
capture/deformation scope of W23.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
PYTHON_FOR_PIP = Path("/opt/homebrew/opt/python@3.12/bin/python3.12")
EXPECTED_PYTHON = Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
FIXTURE = REPO / "tools/java/NativeW23TEScienceFixture.java"
PERSISTENT_WORKER_CLASS = "comsol_mcp.worker_java.PersistentComsolWorker"
PROTOCOL = REPO / "tools/w23_te_science_protocol.py"
MANAGED_PREFLIGHT = REPO / "tools/run_native_w23_te_managed_preflight.py"
RESUME_HELPERS = REPO / "tools/run_native_resume_smoke.py"
PLAN = REPO / "docs/full_project_execution/w23_overlap/W23_TE_FIXTURE_PROPOSAL.md"
EVIDENCE_ROOT = REPO / "docs/full_project_execution/w23_overlap/evidence"
CANDIDATE_ID = "W23-TE-PLANAR-TE-SCIENCE-17"
PROJECT_LABEL = "W23 managed planar TE science candidate 17"
PROJECT_ID: str | None = None  # Set only from the runtime project.create response.
APPROVAL_STATUS = "ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE"
SOURCE_MAP_DIGEST_SEMANTICS = (
    "SHA-256 of canonical UTF-8 JSON for the sorted source_files mapping; "
    "keys sorted, compact separators, ensure_ascii=false")
SOURCE_MANIFEST_FILE_DIGEST_SEMANTICS = (
    "SHA-256 of the exact raw UTF-8 bytes in source_manifest.json")
CURRENT_FULL_OFFLINE_MODE = "CURRENT_FULL_OFFLINE_V1"
CURRENT_FULL_OFFLINE_GATES = (
    "runtime_imports", "software_negative_controls", "pip_check",
    "stdio_initialize_tools_list", "comsol64_jdk11_javac_javap",
)
W23_R13_BASE_ROOT = Path("/private/tmp/cv3n-w23-zero8-r13-b2cd449e")
W23_R13_BASE_SOURCE_MANIFEST_SHA256 = "ce650bf70ad704f38b243bb63fbf73cf6cc2985f01490f88a7e9e96f673f079a"
W23_R13_BASE_SOURCE_MAP_SHA256 = "3543f72172c673a6c6b4b70741433f47716c260be48e172cc45aff9ae7ef34b6"
W23_R13_BASE_JUNIT_SHA256 = "17f232b6a0ec5cc7e37d3a6dc45aaf25c44bc6136b8b528d271d8cf06914b2e9"
W23_R13_BASE_COMPILE_SHA256 = "b4c8009219555b390b52933e85f10d4858b6897f5bfaa7603d3fd9fbb6c82623"
W23_R13_BASE_ACTIVITY_ID = "b2cd449e-ecdb-4e6f-9df3-a1b69056882f"
W23_R13_BASE_CANDIDATE_ID = "W23-TE-SAVED-MPH-ZERO-SOLVE-READBACK-21"
W23_R13_BASE_CANDIDATE_REVISION = "candidate21-r2"
W23_R13_BASE_SOURCE_COUNT = 448
W23_R13_BASE_TEST_COUNT = 860
W23_R13_ALLOWED_COMMON_SOURCE_DELTA = frozenset({
    "comsol_mcp/_g3_results.py",
    "tests/test_g3_results.py",
    "tests/test_native_w23_te_science_protocol.py",
    # R19 adds focused controls for attempted-versus-verified saved-MPH point reads.
    "tests/test_native_w23_saved_mph_diagnostic.py",
    # R20 adds an inactive loopback TIME_WAIT parser class without relaxing owner or ledger gates.
    "tests/test_native_w23_te_science_runner.py",
    "tools/run_native_w23_saved_mph_diagnostic.py",
    "tools/run_native_w23_te_managed_preflight.py",
    "tools/run_native_w23_te_science.py",
    "tools/w23_te_science_protocol.py",
})
W23_R13_ALLOWED_ADDED_SOURCE_DELTA = frozenset({
    "tests/test_interp_native_default_units.py",
})
W23_R13_JAVA_SOURCE_SHA256 = {
    "comsol_mcp/worker_java/PersistentComsolWorker.java": "6e2f2889655d60b1233b9324826c7058779a4bb9ea2f5ee0b1ce1764375a03d6",
    "tools/java/NativeW23TEScienceFixture.java": "63be8fa15063b914f9f37cd09cd4fe048f18c6a37c856820f44737bb4857e23e",
}
MAX_STUDY_RUN_CALLS = 4
MAX_BIRTH_BUDGET_S = 1800.0
CLEANUP_RESERVE_S = 120.0
CASE_PLAN: tuple[dict[str, Any], ...] = (
    {"case_id": "coarse-phase0", "mesh_level": "coarse", "phase_value": "0[deg]"},
    {"case_id": "coarse-phase90", "mesh_level": "coarse", "phase_value": "90[deg]"},
    {"case_id": "fine-phase0", "mesh_level": "fine", "phase_value": "0[deg]"},
    {"case_id": "fine-phase90", "mesh_level": "fine", "phase_value": "90[deg]"},
)
MESH_LEVEL_SPECS: dict[str, tuple[str, str]] = {
    "coarse": ("0.16[um]", "0.04[um]"),
    "fine": ("0.08[um]", "0.04[um]"),
}
STUDY_TAG = "std1"
CONFIGURED_STUDY_STEPS = (
    {"tag": "bmaInput", "feature_type": "BoundaryModeAnalysis", "port_name": "1",
     "mode_frequency_expression": "f0"},
    {"tag": "bmaOutput", "feature_type": "BoundaryModeAnalysis", "port_name": "2",
     "mode_frequency_expression": "f0"},
    {"tag": "freq", "feature_type": "Frequency", "frequency_expression": "f0"},
)
EXPECTED_FREQUENCY_HZ = 193.414489032258e12
EXPECTED_NEFF_TE0 = 1.540405036791
SOURCE_PREFIXES = (
    REPO / "comsol_mcp",
)
EXPLICIT_SOURCE_PATHS = (
    Path(__file__).resolve(), FIXTURE, PROTOCOL, MANAGED_PREFLIGHT,
    RESUME_HELPERS, PLAN,
    REPO / "docs/full_project_execution/W23_MAIN_MODEL_PLAN.md",
    REPO / "tools/phase4_run_mcp.py",
    REPO / "tests/test_native_w23_te_science_runner.py",
    REPO / "tests/test_native_w23_te_science_protocol.py",
    REPO / "tests/test_w23_native_results.py",
    REPO / "tests/test_runtime_control.py",
    REPO / "tests/test_g3_runtime.py",
    REPO / "docs/full_project_execution/w23_overlap/definition.schema.json",
    REPO / "docs/full_project_execution/w23_overlap/result.schema.json",
    REPO / "docs/full_project_execution/MASTER_GOAL.md",
    REPO / "docs/full_project_execution/MAIN_ACCEPTANCE_PLAN.md",
    REPO / "pyproject.toml",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, default=str, allow_nan=False) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                                default=str, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _source_files() -> dict[str, dict[str, Any]]:
    """Hash runtime sources and package data; AppleDouble files are diagnostic only."""
    paths: set[Path] = set()
    for explicit in EXPLICIT_SOURCE_PATHS:
        paths.add(explicit.resolve())
    for prefix in SOURCE_PREFIXES:
        for path in prefix.rglob("*"):
            if not path.is_file() or path.name.startswith("._"):
                continue
            if path.suffix in {".py", ".java", ".json"}:
                paths.add(path.resolve())
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("source closure is missing: " + ", ".join(missing))
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(paths, key=lambda item: item.relative_to(REPO).as_posix()):
        if path.name.startswith("._"):
            continue
        result[path.relative_to(REPO).as_posix()] = {
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return result


def _source_manifest_sha256(source_files: Mapping[str, Any]) -> str:
    return sha256_json({name: dict(row) for name, row in sorted(source_files.items())})


def _bind_authoritative_project(create_response: Mapping[str, Any],
                                helpers: Mapping[str, Any], *,
                                expected_workspace: Path | None = None) -> dict[str, Any]:
    """Install the exact project ID minted by the production project.create route."""
    global PROJECT_ID
    if create_response.get("success") is not True:
        raise RuntimeError("authoritative project.create did not succeed")
    data = create_response.get("data")
    project = data.get("project") if isinstance(data, Mapping) else None
    project_id = project.get("project_id") if isinstance(project, Mapping) else None
    if not isinstance(project_id, str) or not project_id.strip():
        raise RuntimeError("project.create response lacks data.project.project_id")
    if (project.get("label") != PROJECT_LABEL
            or not isinstance(project.get("workspace"), str)
            or type(project.get("schema_version")) is not int
            or project.get("schema_version") != 1
            or type(project.get("revision")) is not int
            or project["revision"] < 1):
        raise RuntimeError("project.create response lacks the persisted candidate project record")
    workspace = Path(project["workspace"])
    try:
        workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("project.create workspace does not resolve to an existing directory") from exc
    if not workspace.is_dir():
        raise RuntimeError("project.create workspace is not a directory")
    if expected_workspace is not None:
        expected = expected_workspace.resolve(strict=True)
        if workspace != expected:
            raise RuntimeError("project.create workspace differs from the frozen science workspace")
    preflight = helpers.get("preflight_module")
    if preflight is None:
        raise RuntimeError("project.create cannot bind cleanup filters without the preflight module")
    PROJECT_ID = project_id
    # W23 native execution is serialized in one run context; the existing
    # full-page helper reads this module variable for every SQLite ledger filter.
    preflight.PROJECT_ID = project_id
    return {"status": "PASS_RUNTIME_AUTHORITY_PROJECT_BOUND",
            "display_label": PROJECT_LABEL, "project_id": project_id,
            "source": "exact successful project.create response",
            "workspace": str(workspace),
            "ledger_filter_project_id": project_id, "project_record": dict(project)}


def _create_science_project_prebirth(daemon: Any, helpers: Mapping[str, Any],
                                    project_container: Path, evidence: Path) -> dict[str, Any]:
    """Create and persist the registered science workspace before engine birth."""
    host_ceiling = getattr(getattr(daemon, "backend", None), "host_permission_ceiling", None)
    if not isinstance(host_ceiling, (set, frozenset)) or "trusted_code" not in host_ceiling:
        raise RuntimeError("prebirth project authority lacks the explicit trusted_code host grant")
    expected_workspace = project_container / "science"
    if expected_workspace.exists():
        raise FileExistsError(f"refusing to reuse a preexisting science workspace: {expected_workspace}")
    request_id = f"w23-project-create-{uuid4()}"
    idempotency_key = f"w23-project-create-{uuid4()}"
    request = {
        "operation": "project.create",
        "arguments": {
            "label": PROJECT_LABEL,
            "workspace": "science",
            "policy": {"permissions": ["compute", "inspect", "project_write", "trusted_code"]},
        },
        "execution": {"request_id": request_id,
                      "idempotency_key": idempotency_key,
                      "rpc_timeout_s": 30.0, "queue_timeout_s": 30.0},
    }
    response = daemon.dispatch(request)
    write_json(evidence / "project_create_request.json", request)
    write_json(evidence / "project_create_response.json", response)
    receipt = _bind_authoritative_project(
        response, helpers, expected_workspace=expected_workspace)
    project_permissions = receipt["project_record"].get("policy", {}).get("permissions", [])
    if not isinstance(project_permissions, list) or "trusted_code" not in project_permissions:
        raise RuntimeError("persisted science project policy does not include the requested trusted_code grant")
    receipt.update({
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "workspace_role": "registered_science_project_workspace",
        "workspace_created_before_native_server_birth": True,
        "project_create_response": response,
    })
    write_json(evidence / "project_create_receipt.json", receipt)
    return receipt


def _set_prebirth_project_authority_opt_in() -> dict[str, Any]:
    """Set only the task-authorized trusted-code ceiling before daemon creation.

    The isolation receipt is created after server birth and is intentionally
    not populated by this prebirth policy grant.
    """
    os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
    return {"status": "PASS_TASK_TRUSTED_CODE_HOST_OPT_IN_SET",
            "environment_variable": "COMSOL_MCP_TRUSTED_CODE",
            "value": "1", "isolation_receipt_mutated": False,
            "isolation_receipt_check": "NOT_APPLICABLE_BEFORE_SERVER_BIRTH"}


def _project_science_workspace(project_receipt: Mapping[str, Any]) -> Path:
    record = project_receipt.get("project_record")
    raw = record.get("workspace") if isinstance(record, Mapping) else None
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("authoritative project receipt has no science workspace")
    path = Path(raw).resolve(strict=True)
    if not path.is_dir():
        raise RuntimeError("authoritative science workspace is not a directory")
    if project_receipt.get("workspace") != str(path):
        raise RuntimeError("project receipt workspace and persisted project record disagree")
    return path


def _active_project_id() -> str:
    if not isinstance(PROJECT_ID, str) or not PROJECT_ID.strip():
        raise RuntimeError("managed execution is blocked until the authoritative project.create ID is bound")
    return PROJECT_ID


def _validate_cleanup_project_scope(project_receipt: Mapping[str, Any] | None,
                                    immutable_run_project_id: str | None,
                                    preflight: Any,
                                    managed_reconciliation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Bind cleanup evidence to the ID captured from this run's create receipt.

    ``immutable_run_project_id`` is captured once when the authoritative
    ``project.create`` response is accepted.  The mutable runner/preflight
    module globals are only compared against it; they never establish scope.
    """
    receipt_id = project_receipt.get("project_id") if isinstance(project_receipt, Mapping) else None
    expected = immutable_run_project_id
    filter_id = getattr(preflight, "PROJECT_ID", None)
    observed = (managed_reconciliation.get("project_id")
                if isinstance(managed_reconciliation, Mapping) else None)
    matches = (isinstance(expected, str) and bool(expected)
               and receipt_id == expected and filter_id == expected and observed == expected)
    return {
        "status": "PASS_AUTHORITATIVE_PROJECT_LEDGER_SCOPE"
            if matches else "FAIL_PROJECT_LEDGER_SCOPE_MISMATCH",
        "cleanup_allowed": matches,
        "expected_project_id": expected,
        "project_create_receipt_id": receipt_id,
        "ledger_filter_project_id": filter_id,
        "reconciliation_project_id": observed,
        "source": "immutable successful project.create receipt",
    }


def _worker_disconnect_verified(disconnect_result: Any, health_before: Any,
                                health_after: Any) -> bool:
    """Validate the decoded RemoteClient result and same Worker generation."""
    if not all(isinstance(value, Mapping)
               for value in (disconnect_result, health_before, health_after)):
        return False
    instance_id = health_before.get("instance_id")
    before_generation = health_before.get("generation")
    result_generation = disconnect_result.get("generation")
    return bool(
        health_before.get("ok") is True
        and health_before.get("status") == "HEALTHY"
        and type(health_before.get("queued_or_running")) is int
        and health_before.get("queued_or_running") == 0
        and type(before_generation) is int
        and isinstance(instance_id, str) and instance_id
        and disconnect_result.get("connected") is False
        and disconnect_result.get("instance_id") == instance_id
        and type(result_generation) is int
        and result_generation == before_generation + 1
        and health_after.get("ok") is True
        and health_after.get("status") == "HEALTHY"
        and health_after.get("connected") is False
        and type(health_after.get("queued_or_running")) is int
        and health_after.get("queued_or_running") == 0
        and health_after.get("instance_id") == instance_id
        and type(health_after.get("generation")) is int
        and health_after.get("generation") == result_generation
    )


def _cleanup_authorization(project_receipt: Mapping[str, Any] | None,
                           immutable_run_project_id: str | None,
                           preflight: Any,
                           managed_reconciliation: Mapping[str, Any] | None,
                           cleanup_gates: Mapping[str, Any] | None,
                           idle_proof: Mapping[str, Any] | None) -> dict[str, Any]:
    """Combine exact project attribution with ledger, direct-RPC, and idle gates."""
    project_scope = _validate_cleanup_project_scope(
        project_receipt, immutable_run_project_id, preflight, managed_reconciliation)
    ledger_safe = (isinstance(cleanup_gates, Mapping)
                   and cleanup_gates.get("safe_for_owned_cleanup") is True)
    daemon_idle = isinstance(idle_proof, Mapping) and idle_proof.get("idle") is True
    allowed = project_scope["cleanup_allowed"] and ledger_safe and daemon_idle
    return {
        "status": "PASS_EXACT_PROJECT_AND_TERMINAL_IDLE_GATES" if allowed
                  else "BLOCKED_PROJECT_OR_TERMINAL_IDLE_GATE",
        "safe_for_owned_cleanup": allowed,
        "project_scope": project_scope,
        "combined_ledger_gate_safe": ledger_safe,
        "daemon_idle": daemon_idle,
    }


def _write_source_manifest_artifact(candidate_root: Path,
                                    source_files: Mapping[str, Any]) -> dict[str, Any]:
    """Persist source inventory and distinguish its canonical digest from file bytes."""
    payload = {
        "schema_version": 1,
        "source_count": len(source_files),
        "source_manifest_sha256": _source_manifest_sha256(source_files),
        "source_manifest_sha256_semantics": SOURCE_MAP_DIGEST_SEMANTICS,
        "source_files": {name: dict(row) for name, row in sorted(source_files.items())},
    }
    path = candidate_root / "source_manifest.json"
    write_json(path, payload)
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "sha256_semantics": SOURCE_MANIFEST_FILE_DIGEST_SEMANTICS,
    }


def _validate_source_manifest_artifact(freeze_path: Path,
                                      freeze: Mapping[str, Any]) -> None:
    artifact = freeze.get("source_manifest_artifact")
    if not isinstance(artifact, Mapping):
        raise RuntimeError("freeze does not bind a source manifest file-byte hash")
    name = artifact.get("path")
    if (not isinstance(name, str) or name in {"", ".", ".."}
            or Path(name).is_absolute() or len(Path(name).parts) != 1):
        raise RuntimeError("frozen source manifest artifact path is invalid")
    path = freeze_path.parent / name
    if path.is_symlink() or path.resolve().parent != freeze_path.parent.resolve():
        raise RuntimeError("frozen source manifest artifact resolves outside its candidate directory")
    if not path.is_file() or path.stat().st_size != artifact.get("bytes"):
        raise RuntimeError("frozen source manifest artifact is absent or has a byte-count mismatch")
    if artifact.get("sha256_semantics") != SOURCE_MANIFEST_FILE_DIGEST_SEMANTICS:
        raise RuntimeError("frozen source manifest file-byte hash semantics are invalid")
    if sha256_file(path) != artifact.get("sha256"):
        raise RuntimeError("frozen source manifest artifact file-byte SHA-256 mismatch")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("frozen source manifest artifact is not valid UTF-8 JSON") from exc
    if (payload.get("source_files") != freeze.get("source_files")
            or payload.get("source_manifest_sha256") != freeze.get("source_manifest_sha256")
            or payload.get("source_manifest_sha256_semantics") != SOURCE_MAP_DIGEST_SEMANTICS
            or payload.get("source_count") != len(freeze.get("source_files", {}))):
        raise RuntimeError("frozen source manifest artifact does not describe the freeze source map")


def planned_study_calls() -> dict[str, Any]:
    """Describe actual public submissions separately from configured steps."""
    return {
        "study_run_submission_count_max": MAX_STUDY_RUN_CALLS,
        "study_run_submissions": [
            {"ordinal": index + 1, "case_id": case["case_id"],
             "method": "model.study('std1').run()",
             "dispatch_operation": "study.run",
             "configured_study": STUDY_TAG,
             "configured_step_tags_in_order": [row["tag"] for row in CONFIGURED_STUDY_STEPS],
             "bma_input_port": "1", "bma_output_port": "2",
             "frequency_expression": "f0",
             "separate_bma_or_frequency_study_run_submissions": 0}
            for index, case in enumerate(CASE_PLAN)
        ],
        "configured_study_feature_count": len(CONFIGURED_STUDY_STEPS),
        "configured_study_step_instances_across_all_calls": (
            len(CASE_PLAN) * len(CONFIGURED_STUDY_STEPS)),
        "configured_study_features": [dict(row) for row in CONFIGURED_STUDY_STEPS],
        "bma_and_frequency_submission_relationship": (
            "Each case sends one model.study('std1').run() Worker submission for the configured "
            "bmaInput, bmaOutput, and freq StudyFeature nodes. No separate BMA/frequency Study.run "
            "submission is planned; generated StudyStep bindings are read back from the solver tree."),
        "solver_sequence_count_or_internal_solver_execution_count":
            "UNKNOWN_UNTIL_NATIVE_SOLVER_TREE_AND_RUNTIME_EVIDENCE",
        "count_basis": (
            "One submitted Study.run call per frozen case. BMA input, BMA output, and Frequency "
            "are configured features within the same std1 study. Runtime StudyStep bindings are "
            "read from the generated solver tree; no one-sequence-per-step assumption is made. "
            "Internal solver executions are not inferred from this call plan."
        ),
    }


def validate_configured_study_features(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Validate configured StudyFeature order and values before the first solve."""
    steps = inventory.get("study_steps_in_configured_order")
    if not isinstance(steps, list):
        raise ValueError("native study inventory is missing configured StudyFeature nodes")
    expected = [dict(row) for row in CONFIGURED_STUDY_STEPS]
    observed = []
    for row in steps:
        if not isinstance(row, Mapping):
            raise ValueError("native study feature inventory contains a malformed row")
        observed.append({"tag": row.get("tag"), "feature_type": row.get("feature_type")})
    if observed != [{"tag": row["tag"], "feature_type": row["feature_type"]} for row in expected]:
        raise ValueError(f"configured study order/types differ from frozen plan: {observed!r}")
    for actual, desired in zip(steps, expected):
        if desired["tag"] in {"bmaInput", "bmaOutput"}:
            if str(actual.get("PortName", "")) != desired["port_name"]:
                raise ValueError(f"{desired['tag']} native PortName readback did not match port {desired['port_name']}")
            if str(actual.get("modeFreq", "")).strip() != desired["mode_frequency_expression"]:
                raise ValueError(f"{desired['tag']} native modeFreq readback did not match {desired['mode_frequency_expression']}")
        elif str(actual.get("plist", "")).strip() != desired["frequency_expression"]:
            raise ValueError("frequency study step plist readback did not match the frozen expression")
    return {
        "status": "PASS_CONFIGURED_STUDY_FEATURES_BEFORE_SOLVE",
        "study_feature_order": observed,
        "configured_study_feature_count": len(steps),
        "feature_property_readbacks": [
            {key: row.get(key) for key in ("tag", "feature_type", "PortName", "modeFreq", "plist")
             if key in row}
            for row in steps
        ],
    }


def validate_study_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Bind configured step order to documented solver-tree StudyStep fields."""
    configured = validate_configured_study_features(inventory)
    steps = inventory.get("study_steps_in_configured_order")
    sequences = inventory.get("solver_sequences_for_study")
    if not isinstance(steps, list) or not isinstance(sequences, list):
        raise ValueError("native solver inventory is missing generated solver sequences")
    bindings: list[dict[str, Any]] = []
    for sequence in sequences:
        if not isinstance(sequence, Mapping):
            raise ValueError("native solver sequence inventory contains a malformed row")
        rows = sequence.get("study_step_bindings_in_solver_tree_order")
        if not isinstance(rows, list):
            raise ValueError("solver sequence is missing its StudyStep tree readback")
        for binding in rows:
            if not isinstance(binding, Mapping) or binding.get("feature_type") != "StudyStep":
                raise ValueError("solver tree contains a malformed StudyStep binding")
            study_tag = binding.get("study")
            step_tag = binding.get("studystep")
            if study_tag == STUDY_TAG:
                bindings.append({"solver_sequence": sequence.get("tag"),
                                 "path": binding.get("path"), "study": study_tag,
                                 "studystep": step_tag})
    bound_tags = [row.get("studystep") for row in bindings]
    expected_tags = [row["tag"] for row in CONFIGURED_STUDY_STEPS]
    if bound_tags != expected_tags:
        raise ValueError(f"generated solver tree StudyStep order/bindings differ from configured study: {bindings!r}")
    return {
        "status": "PASS_CONFIGURED_STUDY_AND_SOLVER_TREE_BINDINGS",
        "study_feature_order": configured["study_feature_order"],
        "solver_sequence_tags": [row.get("tag") for row in sequences],
        "study_step_bindings_in_solver_tree_order": bindings,
        "configured_study_feature_count": len(steps),
        "actual_solver_execution_count": "NOT_DERIVED_FROM_CONFIGURATION",
    }


def validate_approval_receipt(approval: Mapping[str, Any], freeze: Mapping[str, Any],
                              freeze_sha256: str) -> None:
    """Refuse native execution unless the root approval names the exact freeze."""
    expected = {
        "status": APPROVAL_STATUS,
        "candidate_id": CANDIDATE_ID,
        "freeze_sha256": freeze_sha256,
        "source_manifest_sha256": _source_manifest_sha256(freeze["source_files"]),
        "max_owned_server_processes": 1,
        "max_worker_sessions": 1,
        "max_gui_processes": 0,
        "max_study_run_calls": MAX_STUDY_RUN_CALLS,
        "max_seconds_from_server_birth": MAX_BIRTH_BUDGET_S,
        "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        "retry_unknown_or_nonterminal_worker_request": False,
        "study_or_solver_call_plan_sha256": sha256_json(freeze["study_or_solver_call_plan"]),
    }
    mismatches = {key: {"expected": value, "observed": approval.get(key)}
                  for key, value in expected.items() if approval.get(key) != value}
    if mismatches:
        raise RuntimeError("exact native-run approval is missing or does not match this freeze: "
                           + json.dumps(mismatches, ensure_ascii=False, sort_keys=True))


def _validate_frozen_candidate(freeze_path: Path, expected_sha256: str) -> dict[str, Any]:
    if not freeze_path.is_file() or sha256_file(freeze_path) != expected_sha256:
        raise RuntimeError("freeze file is absent or its SHA-256 differs from the reviewed candidate")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("candidate_id") != CANDIDATE_ID or freeze.get("status") != "FROZEN_AWAITING_ROOT_APPROVAL":
        raise RuntimeError("freeze candidate identity/status is not executable")
    if freeze.get("source_files") != _source_files():
        raise RuntimeError("one or more frozen source files changed; prepare and review a new candidate")
    if freeze.get("source_manifest_sha256") != _source_manifest_sha256(freeze["source_files"]):
        raise RuntimeError("freeze source manifest digest is invalid")
    _validate_source_manifest_artifact(freeze_path, freeze)
    if freeze.get("study_or_solver_call_plan") != planned_study_calls():
        raise RuntimeError("freeze solver-call plan differs from this runner's exact per-slot plan")
    return freeze


def _runtime_helper_imports() -> dict[str, Any]:
    """Resolve the same production modules/helpers used by execute before launch gates."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    preflight = importlib.import_module("tools.run_native_w23_te_managed_preflight")
    helpers = preflight._load_managed_runtime_helpers()
    g3_ops = importlib.import_module("comsol_mcp._g3_ops")
    w23_results = importlib.import_module("comsol_mcp._w23_results")
    for module_name in (
        "comsol_mcp._control_daemon", "comsol_mcp._execution_contract",
        "comsol_mcp._execution_service", "comsol_mcp._operation_store",
        "comsol_mcp._managed_backend", "comsol_mcp._g2_registry",
        "comsol_mcp._project_authority",
        "comsol_mcp._g2_isolation", "comsol_mcp._java_worker",
        "comsol_mcp._mcp_gateway", "comsol_mcp.mcp_server",
    ):
        importlib.import_module(module_name)
    if ("result.mode_overlap" not in g3_ops.DISPATCH
            or not callable(w23_results.OPERATIONS.get("result.mode_overlap"))):
        raise RuntimeError("production runtime import did not publish result.mode_overlap")
    return {**helpers, "preflight_module": preflight, "g3_ops": g3_ops,
            "w23_results": w23_results}


def _actual_stdio_preflight(output: Path) -> dict[str, Any]:
    """Perform initialize/tools/list against the installed production MCP entrypoint."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    log_path = output / "stdio_stderr.txt"
    server_home = output / "stdio_server_home"
    server_home.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(REPO),
        "COMSOL_SERVER_MCP_HOME": str(server_home),
        "COMSOL_MCP_TOOL_PROFILE": "full",
    })
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "comsol_mcp.mcp_server"],
                                   env=env, cwd=str(REPO))

    async def check() -> dict[str, Any]:
        with log_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream,
                                         read_timeout_seconds=timedelta(seconds=45)) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
        names = sorted(tool.name for tool in tools.tools)
        required = {"registry_call", "operation_call"}
        missing = sorted(required - set(names))
        if init is None or missing:
            raise RuntimeError(f"production stdio initialize/tools/list failed; missing={missing}")
        return {
            "status": "PASS_REAL_PRODUCTION_STDIO_INITIALIZE_TOOLS_LIST",
            "entrypoint": "python -m comsol_mcp.mcp_server",
            "python_executable": sys.executable,
            "protocol_version": str(getattr(init, "protocolVersion", "")),
            "server_info": getattr(init, "serverInfo", None).model_dump()
                if getattr(init, "serverInfo", None) is not None else None,
            "tool_count": len(names), "required_tools": sorted(required),
            "required_tools_present": True, "tools": names,
            "stderr_path": str(log_path), "no_comsol_server_configured": True,
        }

    receipt = asyncio.run(check())
    return receipt


def _compile_sources(output: Path) -> dict[str, Any]:
    """Compile fixture and Worker with the exact installed 6.4 classpath/JDK 11."""
    from comsol_mcp._java_worker import JavaWorkerPaths

    if not INSTALL_ROOT.is_dir() or not JAVA11.is_dir():
        raise FileNotFoundError("frozen COMSOL 6.4 tree or Corretto 11 JDK is unavailable")
    paths = JavaWorkerPaths(INSTALL_ROOT, JAVA11, project_root=REPO)
    classpath, manifest_sha, jar_count, jar_fingerprint = paths.classpath()
    javac, javap = JAVA11 / "bin/javac", JAVA11 / "bin/javap"
    if not javac.is_file() or not javap.is_file():
        raise FileNotFoundError("frozen JDK 11 javac/javap toolchain is unavailable")
    compiled: dict[str, Any] = {}
    for label, source, class_name in (
        ("science_fixture", FIXTURE, "NativeW23TEScienceFixture"),
        ("persistent_worker", REPO / "comsol_mcp/worker_java/PersistentComsolWorker.java",
         PERSISTENT_WORKER_CLASS),
    ):
        class_dir = output / label / "classes"
        class_dir.mkdir(parents=True, exist_ok=False)
        command = [str(javac), "-encoding", "UTF-8", "-classpath", classpath,
                   "-d", str(class_dir), str(source)]
        result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                                timeout=120, check=False)
        (class_dir.parent / "javac.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (class_dir.parent / "javac.stderr.txt").write_text(result.stderr, encoding="utf-8")
        javap_result = None
        if result.returncode == 0:
            javap_result = subprocess.run(
                [str(javap), "-classpath", classpath + os.pathsep + str(class_dir), class_name],
                cwd=REPO, text=True, capture_output=True, timeout=30, check=False)
            (class_dir.parent / "javap.stdout.txt").write_text(javap_result.stdout, encoding="utf-8")
            (class_dir.parent / "javap.stderr.txt").write_text(javap_result.stderr, encoding="utf-8")
        class_files = {path.relative_to(class_dir).as_posix(): {
            "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in sorted(class_dir.rglob("*.class")) if not path.name.startswith("._")}
        compiled[label] = {
            "source": str(source), "source_sha256": sha256_file(source),
            "class_name": class_name, "compile_command": command,
            "javac_returncode": result.returncode, "javac_stdout": result.stdout,
            "javac_stderr": result.stderr,
            "javap_returncode": javap_result.returncode if javap_result else None,
            "javap_stdout": javap_result.stdout if javap_result else None,
            "javap_stderr": javap_result.stderr if javap_result else None,
            "classes": class_files,
        }
        if result.returncode != 0 or javap_result is None or javap_result.returncode != 0:
            raise RuntimeError(f"offline {label} javac/javap gate failed")
    return {
        "status": "PASS_COMSOL_64_JDK11_COMPILE_AND_JAVAP",
        "classpath_manifest_sha256": manifest_sha,
        "classpath_jar_count": jar_count,
        "classpath_jar_content_fingerprint_sha256": jar_fingerprint,
        "comsol_version": paths.comsol_version_info(),
        "jdk_home": str(JAVA11), "compiled_sources": compiled,
    }


def _pip_dependency_check(*, runtime_binding: Mapping[str, Any] | None = None,
                          runtime_budget: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not PYTHON_FOR_PIP.is_file():
        raise FileNotFoundError(f"trusted pip executable missing: {PYTHON_FOR_PIP}")
    if runtime_binding is None:
        if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
            raise RuntimeError(f"runner Python is not the reviewed task venv: {sys.executable}")
        python_entry = str(sys.executable)
        status = "PASS_TASK_VENV_PIP_CHECK"
    else:
        if not isinstance(runtime_budget, Mapping):
            raise RuntimeError("fresh runtime-bound pip check requires its exact phase budget")
        from tools import native_runtime_binding as fresh_runtime
        fresh_runtime.validate(runtime_binding, repo=REPO, budget=dict(runtime_budget),
                               install=INSTALL_ROOT, jdk=JAVA11)
        entry = runtime_binding.get("python", {}).get("entry")
        if (not isinstance(entry, str) or not entry
                or Path(sys.executable).resolve() != Path(entry).resolve()):
            raise RuntimeError("runner Python differs from the validated fresh runtime binding")
        python_entry = entry
        status = "PASS_FRESH_RUNTIME_BOUND_PIP_CHECK"
    command = [str(PYTHON_FOR_PIP), "-m", "pip", "--python", python_entry, "check"]
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                            timeout=60, check=False)
    receipt = {"command": command, "python_executable": python_entry,
               "python_version": sys.version, "returncode": result.returncode,
               "stdout": result.stdout, "stderr": result.stderr}
    if runtime_binding is not None:
        receipt["runtime_binding_sha256"] = runtime_binding.get("binding_sha256")
    if result.returncode != 0:
        if runtime_binding is None:
            raise RuntimeError("reviewed task-venv production dependency check failed")
        raise RuntimeError("validated fresh-runtime production dependency check failed")
    return {"status": status, **receipt}


def offline_preflight(output: Path, *,
                      current_source_files: Mapping[str, Any] | None = None,
                      runtime_binding: Mapping[str, Any] | None = None,
                      runtime_budget: Mapping[str, Any] | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {"status": "RUNNING_OFFLINE_GATES", "gates": {},
                               "native_process_started": False, "native_study_run_calls": 0}
    current_full = current_source_files is not None
    current_map_sha = None
    if current_full:
        if not isinstance(current_source_files, Mapping) or not current_source_files:
            raise RuntimeError("current-full offline gates require the exact nonempty current source map")
        current_map_sha = _source_manifest_sha256(current_source_files)
        if runtime_binding is not None and not isinstance(runtime_budget, Mapping):
            raise RuntimeError("current-full offline gates require the fresh binding phase budget")
        receipt.update({"mode": CURRENT_FULL_OFFLINE_MODE,
                        "source_manifest_sha256": current_map_sha,
                        "source_count": len(current_source_files)})

    def persist_progress() -> None:
        if current_full:
            receipt["gate_source_manifest_sha256"] = {
                name: current_map_sha for name in receipt["gates"]}
            receipt["gate_receipt_sha256"] = {
                name: sha256_json(value) for name, value in receipt["gates"].items()}
        write_json(output / "offline_preflight_progress.json", receipt)

    current_gate = "runtime_imports"
    try:
        helpers = _runtime_helper_imports()
        receipt["gates"][current_gate] = {
            "status": "PASS_EXECUTE_HELPER_IMPORTS",
            "helper_names": sorted(helpers),
            "result_mode_overlap_published": True,
        }
        persist_progress()
        current_gate = "software_negative_controls"
        from tools.w23_te_science_protocol import run_negative_controls
        negative_controls = run_negative_controls()
        receipt["gates"][current_gate] = negative_controls
        persist_progress()
        current_gate = "pip_check"
        pip = _pip_dependency_check(runtime_binding=runtime_binding,
                                    runtime_budget=runtime_budget)
        receipt["gates"][current_gate] = pip
        persist_progress()
        current_gate = "stdio_initialize_tools_list"
        stdio = _actual_stdio_preflight(output / "stdio")
        receipt["gates"][current_gate] = stdio
        persist_progress()
        current_gate = "comsol64_jdk11_javac_javap"
        compile_receipt = _compile_sources(output / "compile")
        receipt["gates"][current_gate] = compile_receipt
        receipt.update({"status": "PASS_ALL_OFFLINE_GATES",
                        "runtime_imports": receipt["gates"]["runtime_imports"],
                        "software_negative_controls": negative_controls,
                        "pip_check": pip, "stdio": stdio, "compile": compile_receipt})
        persist_progress()
        return receipt
    except BaseException as exc:
        receipt.update({"status": "FAIL_OFFLINE_GATE", "failed_gate": current_gate,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()})
        persist_progress()
        write_json(output / "offline_preflight_failure.json", receipt)
        raise


def prepare(evidence: Path) -> dict[str, Any]:
    if not evidence.as_posix().startswith(str(EVIDENCE_ROOT.resolve()) + "/"):
        raise ValueError("--evidence must be a new folder under the W23 evidence root")
    if evidence.exists():
        raise FileExistsError(f"candidate evidence already exists: {evidence}")
    evidence.mkdir(parents=True, exist_ok=False)
    before = _source_files()
    gates = offline_preflight(evidence / "offline_gates")
    after = _source_files()
    if before != after:
        raise RuntimeError("source closure changed while offline candidate gates were running")
    source_manifest_artifact = _write_source_manifest_artifact(evidence, before)
    freeze = {
        "schema_version": 1,
        "status": "FROZEN_AWAITING_ROOT_APPROVAL",
        "candidate_id": CANDIDATE_ID,
        "created_utc": utc_now(),
        "scope": "2D planar TE Numeric Port science and managed result.mode_overlap mechanism checkpoint",
        "scope_limits": {
            "not_full_w23": True,
            "remaining_original_scope": [
                "3D vector polarization/PML where symmetry is broken",
                "nonzero transverse offsets and x/y/z/angle tolerance scans",
                "native thermal/structural deformation mapped into optical geometry",
                "full 3D/final-assembly capture efficiency and calibrated physical applicability",
            ],
            "PML_and_upstream_power_balance": "NOT_CLAIMED_BY_THIS_CANDIDATE",
        },
        "target": {"platform": "macOS", "comsol_root": str(INSTALL_ROOT),
                   "required_engine_identity": "COMSOL Multiphysics 6.4.0.293",
                   "jdk_home": str(JAVA11), "python_executable": sys.executable,
                   "python_version": sys.version},
        "limits": {
            "max_owned_server_processes": 1, "max_worker_sessions": 1,
            "max_gui_processes": 0,
            "max_study_run_calls": MAX_STUDY_RUN_CALLS,
            "max_seconds_from_server_birth": MAX_BIRTH_BUDGET_S,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
            "retry_unknown_or_nonterminal_worker_request": False,
            "stop_after_first_failed_or_unknown_study_run": True,
        },
        "study_or_solver_call_plan": planned_study_calls(),
        "fixture": {"path": str(FIXTURE), "sha256": sha256_file(FIXTURE)},
        "source_files": before,
        "source_manifest_sha256": _source_manifest_sha256(before),
        "source_manifest_artifact": source_manifest_artifact,
        "offline_preflight": gates,
        "acceptance": {
            "analytic_neff": EXPECTED_NEFF_TE0,
            "analytic_neff_tolerance_coarse": 2e-3,
            "analytic_neff_tolerance_fine": 5e-4,
            "normalized_overlap_minimum_coarse": 0.995,
            "normalized_overlap_minimum_fine": 0.999,
            "native_vs_independent_641_point_integrals_relative_tolerance": 1e-3,
            "capture_aperture_id": "receiver_core_aperture",
            "capture_aperture_plane": "output_x8",
            "capture_aperture_kind": "core_only_native_boundary_selection_strict_subset_of_full_output_plane",
            "capture_aperture_selection_tag": "selCoreCaptureX8",
            "capture_aperture_y_bounds_um": [-0.5, 0.5],
            "capture_flux_orientation": {
                "physical_direction": "+x",
                "native_outward_normal_times_declared_normal_sign": 1,
                "signed_flux_preserved": True,
                "absolute_value_or_clipping": False,
            },
            "capture_native_integral_unit": "W/m",
            "capture_denominator": {
                "reference_id": "input_port_mode_1",
                "input_plane_id": "input_xminus10",
                "source": "native incident-port-mode IntLine, independently reconciled to exported raw E/H Simpson integral",
                "must_be_positive_above_power_floor": True,
            },
            "capture_independent_quadrature": {
                "full_plane_sample_count": 641,
                "aperture_y_endpoints_m": [-0.5e-6, 0.5e-6],
                "aperture_subgrid_sample_count": 41,
                "rule": "composite Simpson on uniformly sampled raw native complex E/H Poynting flux",
                "native_flux_and_denominator_and_eta_relative_tolerance": 1e-3,
            },
            "same_solution_321_to_641_quadrature_relative_tolerance": 1e-3,
            "native_input_phase_factor": {"real": 0.0, "imag": 1.0},
            "native_phase_power_and_overlap_invariants_relative_tolerance": 1e-3,
            "software_exported_signal_global_phase_control": {
                "status_required": "PASS_SOFTWARE_SIGNAL_GLOBAL_PHASE_CONTROL",
                "phase_factor": {"real": 0.0, "imag": 1.0},
                "scope": "software-only recomputation; never substitutes for native input Port phase solves",
            },
            "native_reopen_integral_relative_tolerance": 1e-8,
            "material_tensor_readback": {
                "setter_storage_form": "9-entry String[] requested for the symmetric diagonal tensor; COMSOL may canonicalize an isotropic tensor to one entry",
                "getter_selection": "getValueType selects matching getString, getStringArray, or getStringMatrix; MaterialModel.size is captured for every property",
                "getter_capture_status_required": "readback_status must be CAPTURED and MaterialModel.size must be CAPTURED before reconstructing a physical tensor",
                "accepted_isotropic_canonical_shape": "String/getString with raw shape [], or StringArray/getStringArray with exactly one string and raw shape [1]; in either case require captured MaterialModel.size exactly [1,1] or [3,3], preserve the literal returned size, and reconstruct only as isotropic after exact value checks",
                "accepted_string_array_shape": "exactly nine entries, raw shape [9], and stored size dimensions whose product is nine; reconstructed with documented column-wise tensor readback",
                "accepted_string_matrix_shape": "exactly 3x3, raw shape [3,3], stored size [3,3], preserved as returned",
                "required_relative_permittivity": "reconstructed 3x3 tensor with expected expression on the diagonal and zero off-diagonal",
                "required_relative_permeability": "reconstructed 3x3 identity tensor",
                "required_electric_conductivity": "COMSOL 6.4 electricconductivity property is set explicitly to a typed one-entry StringArray [0[S/m]] and read back via matching typed getter; require captured size [1,1] or [3,3], exact raw shape/value, finite numeric zero S/m for core and both cladding regions.",
                "source_basis": "COMSOL 6.4 model.material() documentation (doc4111/chunk16782, SHA-256 80ae61bc99d91b2e91aaeff7c91406bae0a768c7fbadef5b8efe95db33c262ab) permits one value for isotropic matrix properties and describes MaterialModel.size dimensions; Programming Reference 6.4 p.152 defines typed array/matrix getters and column-wise matrix reads. Native Activity004 receipt (material_tensor_raw_readback.json SHA-256 e54058da73b8f6bd7024d760dcd8673cd2e902c651cf9f7b17d07bc0aaf1e5f7) observed all six isotropic properties as StringArray/getStringArray raw_shape [1] while MaterialModel.size returned [3,3]. This demonstrates logical property dimensions are not identical to typed getter storage shape. Admit only exact one-string scalar representations with captured size [1,1] or [3,3], retain that literal size, and then apply the unchanged physical matrix entry checks. The same-repo W15 adapter reconstructs one-entry isotropic values. No whole-object equality or nonsymmetric tensor ordering is claimed.",
                "lossless_material_source_basis": "The COMSOL 6.4 Wave Optics optically_anisotropic_waveguide example (doc22410/chunk98325, source SHA-256 08b5d145ac8adf123ca0e48c74593c6062ecfa21130efc9b033376f8416b9c2c, PDF p.9) uses real relative permittivity, relative permeability 1, and zero S/m electric conductivity for a lossless dielectric. Programming Reference 6.4 doc3626/chunk10909 (SHA-256 5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105, PDF p.154) maps Electric conductivity to API key electricconductivity; LiveLink doc11652/chunk33714 (SHA-256 1045cdfbcaf7cca0b9c12c065a094a76071607878e5777692fc93313c86b91fd, PDF p.17) demonstrates MaterialModel.set with a StringArray conductivity value. Activity007's native exception explicitly reported missing sigma, so candidate16 supplies the documented zero-loss property without changing epsilon/mu or numeric science criteria.",
            },
            "mesh_readback": {
                "generator": "FreeTri",
                "generator_tag": "ftri1",
                "selection_rule": "Selection.all() on the FreeTri selection's current dimension; require dimension 2 and selected 2D domain count equal to geometry.getNDomains()",
                "required_native_readbacks": ["free_tri_built=true", "mesh_is_complete=true", "mesh_is_empty=false", "elements>0", "tri_elements>0", "element_types includes tri"],
                "frozen_level_size_um": {level: {"hmax": values[0], "hmin": values[1]}
                                         for level, values in MESH_LEVEL_SPECS.items()},
                "api_basis": "COMSOL 6.4 FreeTri doc4379/chunk17131 SHA-256 a47a086e293788556ca136312089ba1aee148566543a316ef7e4c55cd5809b7b says FreeTri creates unstructured triangular mesh in 2D and accepts a domain selection; Selection doc7700/chunk23052 SHA-256 451cce8061404cde87fb8fe72d85ae1a1a4c3a7887f4e779a73916f8a24184d6 defines all() as selecting all geometric entities in the current dimension; same-install COMSOL 6.4 javap confirms MeshSequence.getNumElem(String), getTypes(), isEmpty(), and isComplete(). The COMSOL API index doc7308/chunk22383 SHA-256 dd456cb13f9f715e5a596270dbc401427ee9069a4682b36095fea86a217297ce defines MeshSequence.isComplete() as checking that the entire selected geometry has a mesh. Activity005 preserved an actual integer elements=0 from a mesh sequence with no explicit generator; this remains a negative control and is not treated as mesh success.",
            },
        },
        "evidence_policy": {
            "durable_worker_ledger_and_direct_rpc_journal_both_required_for_cleanup": True,
            "durable_domain_unknown_is_never_rewritten": True,
            "source_raw_complex_fields_coordinates_and_saved_mph_retained": True,
            "actual_solver_execution_count": "UNKNOWN_UNLESS_DIRECT_RUNTIME_EVIDENCE_EXISTS",
        },
    }
    freeze_path = evidence / "freeze.json"
    write_json(freeze_path, freeze)
    freeze_sha = sha256_file(freeze_path)
    receipt = {
        "status": "PREPARED_NOT_EXECUTED_AWAITING_EXACT_ROOT_APPROVAL",
        "candidate_id": CANDIDATE_ID,
        "freeze_path": str(freeze_path), "freeze_sha256": freeze_sha,
        "source_count": len(before), "source_manifest_sha256": freeze["source_manifest_sha256"],
        "source_manifest_file_path": str(evidence / source_manifest_artifact["path"]),
        "source_manifest_file_bytes": source_manifest_artifact["bytes"],
        "source_manifest_file_sha256": source_manifest_artifact["sha256"],
        "planned_study_run_calls": MAX_STUDY_RUN_CALLS,
        "configured_study_feature_count": len(CONFIGURED_STUDY_STEPS),
        "actual_solver_execution_count": "UNKNOWN_NOT_INFERRED",
        "native_process_started": False, "study_or_solver_invoked": False,
    }
    write_json(evidence / "prepare_receipt.json", receipt)
    return {**receipt, "freeze": freeze}


class DirectRpcJournal:
    """Durably classify direct Worker RPCs separately from the SQLite jobs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []

    def capture(self, event: Mapping[str, Any]) -> None:
        if event.get("operation_id"):
            return  # Covered by the managed OperationStore event stream.
        raw_phase = event.get("phase")
        phase = ("submitted" if raw_phase == "submitted" else
                 "observed" if raw_phase == "observed" else "unknown")
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        record = {
            "at_utc": utc_now(), "rpc_id": event.get("request_id"), "phase": phase,
            "kind": event.get("kind"), "method": metadata.get("method"),
            "request_hash": event.get("request_hash"),
            "status": str(event.get("status", "")).upper() if phase == "observed" else None,
            "unknown_reason": str(event.get("error")) if phase == "unknown" and event.get("error") else None,
        }
        append_jsonl(self.path, record)
        self.events.append(record)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed durable JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object durable JSONL row at {path}:{line_number}")
        rows.append(row)
    return rows


def _operation_body(response: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError("managed operation response has no data object")
    nested = data.get("result")
    return dict(nested) if isinstance(nested, Mapping) else dict(data)


def _job_id(response: Mapping[str, Any]) -> str | None:
    execution = response.get("execution")
    data = response.get("data")
    candidates = [execution.get("job_id") if isinstance(execution, Mapping) else None,
                  data.get("job_id") if isinstance(data, Mapping) else None]
    return next((value for value in candidates if isinstance(value, str) and value), None)


def _worker_status(response: Mapping[str, Any]) -> str | None:
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, Mapping) else None
    status = worker.get("status") if isinstance(worker, Mapping) else None
    return status.upper() if isinstance(status, str) else None


def _operation_succeeded(response: Mapping[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise RuntimeError(f"{label} failed: {json.dumps(response, ensure_ascii=False, default=str)[:8000]}")
    data = response.get("data")
    if isinstance(data, Mapping):
        outcome = data.get("domain_outcome")
        if isinstance(outcome, Mapping) and outcome.get("state") == "unknown":
            raise RuntimeError(f"{label} has durable domain UNKNOWN; no retry is permitted")
        if data.get("execution_state_unknown") is True:
            raise RuntimeError(f"{label} has execution_state_unknown=true; no retry is permitted")
    return _operation_body(response)


def _load_helper_refs() -> dict[str, Any]:
    global PROJECT_ID
    PROJECT_ID = None
    preflight = importlib.import_module("tools.run_native_w23_te_managed_preflight")
    # Fail closed until execute() creates and binds the authoritative project.
    preflight.PROJECT_ID = "__W23_PROJECT_NOT_CREATED__"
    helpers = preflight._load_managed_runtime_helpers()
    from tools.run_native_resume_smoke import _study_node_path, require_success
    from tools.w23_te_science_protocol import (
        combine_cleanup_gates, compare_native_capture_flux, compare_native_integrals,
        compare_quadrature_refinement, independent_integrals,
        independent_capture_aperture_flux,
        reconcile_direct_rpc_events, run_negative_controls,
        software_global_phase_control, verify_native_phase_pair,
    )
    helpers.update({
        "preflight_module": preflight,
        "_study_node_path": _study_node_path,
        "require_success": require_success,
        "combine_cleanup_gates": combine_cleanup_gates,
        "compare_native_capture_flux": compare_native_capture_flux,
        "compare_native_integrals": compare_native_integrals,
        "compare_quadrature_refinement": compare_quadrature_refinement,
        "independent_integrals": independent_integrals,
        "independent_capture_aperture_flux": independent_capture_aperture_flux,
        "reconcile_direct_rpc_events": reconcile_direct_rpc_events,
        "run_negative_controls": run_negative_controls,
        "software_global_phase_control": software_global_phase_control,
        "verify_native_phase_pair": verify_native_phase_pair,
    })
    return helpers


def _set_runtime_environment(server: Any) -> None:
    os.environ["COMSOL_ROOT"] = str(server.shadow_root)
    os.environ["COMSOL_JAVA_HOME"] = str(JAVA11)
    os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
    os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
    os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
    os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)


def _capture_worker_process_identity(server: Any, helpers: Mapping[str, Any]) -> dict[str, Any]:
    """Capture identity from the exact Popen child even after partial startup."""
    worker = getattr(server, "worker", None)
    process = getattr(worker, "_process", None)
    if process is None or type(getattr(process, "pid", None)) is not int:
        return {"status": "WORKER_IDENTITY_UNAVAILABLE", "pid": None, "port": None,
                "identity": None, "reason": "task-owned Worker Popen identity is unavailable"}
    pid = process.pid
    identity = helpers["_process_snapshot"](pid)
    if not isinstance(identity, Mapping) or identity.get("pid") != pid:
        return {"status": "WORKER_IDENTITY_UNAVAILABLE", "pid": pid, "port": None,
                "identity": identity, "reason": "fresh snapshot did not match the exact Worker Popen PID"}
    port = getattr(worker, "_port", None)
    if isinstance(port, bool) or not isinstance(port, int):
        port = None
    return {"status": "PASS_EXACT_WORKER_POPEN_IDENTITY_CAPTURED", "pid": pid,
            "port": port, "identity": dict(identity),
            "process_running": process.poll() is None,
            "source": "the Worker object created by this runner and its exact subprocess.Popen child"}


def _start_worker_with_journal(server: Any, journal: DirectRpcJournal,
                               helpers: Mapping[str, Any],
                               identity_sink: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start the single task Worker with direct-RPC journal installed first."""
    paths = server.paths_type(server.shadow_root, JAVA11,
                              private_prefs=server.prefs, project_root=server.project)
    worker = server.worker_type(paths, state_dir=server.worker_state)
    worker._on_request_event = journal.capture
    server.worker = worker
    startup = worker.start(startup_timeout_s=25.0)
    # Persist the exact process identity before any subsequent health/connect
    # RPC can time out and leave startup in an UNKNOWN state.
    partial_identity = _capture_worker_process_identity(server, helpers)
    if identity_sink is not None:
        identity_sink.update(partial_identity)
    connected = worker.client().connect(server.port, "127.0.0.1")
    if isinstance(connected, Mapping) and connected.get("status") not in {None, "SUCCEEDED"}:
        raise RuntimeError(f"direct Worker loopback connect did not succeed: {connected!r}")
    engine_version = worker.client().getComsolVersion()
    identity = helpers["_engine_build_identity"](engine_version)
    if identity.get("matches_frozen_target") is not True:
        raise RuntimeError(f"owned engine is not the frozen COMSOL 6.4.0.293: {identity!r}")
    runtime = worker.runtime_metadata()
    pid = runtime.get("pid")
    if type(pid) is not int:
        raise RuntimeError("Worker process identity is not available")
    worker_identity = helpers["_process_snapshot"](pid)
    if not isinstance(worker_identity, Mapping):
        raise RuntimeError("fresh Worker process identity could not be captured")
    proof = helpers["verify_owned_server"](
        server.receipt_path, endpoint=f"127.0.0.1:{server.port}", worker_pid=pid)
    worker_info = {
        "status": "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED",
        "engine_version": str(engine_version), "engine_identity": identity,
        "worker_start": startup, "direct_connect_status": connected,
        "worker_runtime": runtime, "worker_process_identity": worker_identity,
        "server_pid": server.proc.pid if server.proc else None,
        "endpoint": f"127.0.0.1:{server.port}", "isolation_proof": proof,
    }
    if identity_sink is not None:
        identity_sink.update({"status": "PASS_EXACT_WORKER_IDENTITY_CAPTURED",
                              "pid": pid, "port": runtime.get("port"),
                              "identity": dict(worker_identity),
                              "runtime": dict(runtime),
                              "source": "Worker runtime metadata plus fresh PID/birth/command snapshot"})
    return worker_info


def _dispatch(daemon: Any, operation: str, arguments: dict[str, Any], *,
              project_id: str, ref: Mapping[str, Any] | None = None,
              revision: int | None = None, timeout_s: float = 30.0,
              request_id: str | None = None,
              session_id: str | None = None) -> dict[str, Any]:
    execution: dict[str, Any] = {
        "project_id": project_id, "rpc_timeout_s": timeout_s,
        "execution_timeout_s": None, "queue_timeout_s": 60.0,
        "idempotency_key": f"w23-science-idem-{uuid4()}",
        "request_id": request_id or f"w23-science-req-{uuid4()}",
    }
    if ref is not None:
        execution.update({"session_id": ref["session_id"], "model_ref": dict(ref),
                          "expected_revision": revision})
    elif session_id is not None:
        execution["session_id"] = session_id
    result = daemon.dispatch({"operation": operation, "arguments": arguments,
                              "execution": execution})
    if not isinstance(result, dict):
        raise TypeError("managed dispatch returned a non-object response")
    return result


def _wait_job(daemon: Any, response: dict[str, Any], *, deadline_epoch: float,
              cleanup_reserve_s: float, label: str, evidence: Path) -> dict[str, Any]:
    job_id = _job_id(response)
    if job_id is None:
        return response
    initial = daemon.store.job(job_id)
    if not isinstance(initial, Mapping):
        raise RuntimeError(f"{label} returned job id without a durable job row: {job_id}")
    snapshots = []
    job = dict(initial)
    while job.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "UNKNOWN", "LOST"}:
        if time.time() >= deadline_epoch - cleanup_reserve_s:
            write_json(evidence / f"{label}_job_pending.json", {
                "status": "ACTIVE_JOB_LEFT_RUNNING_FOR_RECONCILIATION",
                "job_id": job_id, "job": job, "polls": snapshots,
                "retry": "FORBIDDEN", "cleanup": "BLOCKED_WHILE_JOB_ACTIVE"})
            raise TimeoutError(f"{label} remained active at the cleanup reserve; no retry or cleanup")
        time.sleep(0.25)
        current = daemon.store.job(job_id)
        if not isinstance(current, Mapping):
            raise RuntimeError(f"{label} durable job row disappeared: {job_id}")
        job = dict(current)
        snapshots.append({"at_utc": utc_now(), "status": job.get("status")})
    write_json(evidence / f"{label}_job_terminal.json", {"job_id": job_id,
                "job": job, "polls": snapshots})
    terminal_result = job.get("result")
    if isinstance(terminal_result, Mapping):
        return dict(terminal_result)
    return {"success": job.get("status") == "SUCCEEDED", "data": {},
            "execution": {"job_id": job_id}, "job": job}


def _resolve_wait_response(daemon: Any, response: dict[str, Any], *, deadline_epoch: float,
                           evidence: Path, label: str) -> dict[str, Any]:
    if _job_id(response) is None:
        return response
    return _wait_job(daemon, response, deadline_epoch=deadline_epoch,
                     cleanup_reserve_s=CLEANUP_RESERVE_S,
                     label=label, evidence=evidence)


def _worker_events_for_job(daemon: Any, job_id: str) -> list[dict[str, Any]]:
    preflight = importlib.import_module("tools.run_native_w23_te_managed_preflight")
    events, _pages = preflight._page_job_events(daemon.store, job_id, page_size=1000)
    return events


def _is_study_run_job(job: Mapping[str, Any] | None) -> bool:
    if not isinstance(job, Mapping):
        return False
    operation = job.get("operation")
    if not isinstance(operation, Mapping):
        return False
    # OperationStore.job() attaches the outer operation row here. For the
    # managed route its column is "operation_call"; the requested engine
    # operation is nested in that row's persisted request metadata.
    if operation.get("operation") == "study.run":
        return True
    metadata = operation.get("metadata")
    arguments = metadata.get("arguments") if isinstance(metadata, Mapping) else None
    return isinstance(arguments, Mapping) and arguments.get("operation_id") == "study.run"


def _actual_science_study_run_submissions(daemon: Any, preflight: Any) -> list[dict[str, Any]]:
    """Count only Worker method=run events owned by a durable study.run job."""
    jobs, _job_pages = preflight._page_project_jobs(daemon.store, page_size=1000)
    rows: list[dict[str, Any]] = []
    for job in jobs:
        if not _is_study_run_job(job):
            continue
        job_id = str(job.get("job_id", ""))
        events, _event_pages = preflight._page_job_events(daemon.store, job_id, page_size=1000)
        for event in events:
            metadata = event.get("metadata")
            if event.get("event") == "worker_request" and preflight._is_native_study_run_submission(metadata):
                operation = job.get("operation")
                operation_metadata = operation.get("metadata") if isinstance(operation, Mapping) else None
                arguments = (operation_metadata.get("arguments")
                             if isinstance(operation_metadata, Mapping) else None)
                operation_execution = operation_metadata.get("execution") if isinstance(
                    operation_metadata, Mapping) else None
                result = job.get("result")
                result_data = result.get("data") if isinstance(result, Mapping) else None
                domain_outcome = result_data.get("domain_outcome") if isinstance(result_data, Mapping) else None
                domain_failure = domain_outcome.get("failure") if isinstance(domain_outcome, Mapping) else None
                safe_retry = None
                if isinstance(domain_outcome, Mapping) and type(domain_outcome.get("safe_retry")) is bool:
                    safe_retry = domain_outcome["safe_retry"]
                elif isinstance(domain_failure, Mapping) and type(domain_failure.get("safe_retry")) is bool:
                    safe_retry = domain_failure["safe_retry"]
                worker_metadata = metadata.get("metadata") if isinstance(metadata, Mapping) else None
                rows.append({"job_id": job_id, "operation": "study.run",
                             "operation_id": job.get("operation_id"),
                             "status": job.get("status"),
                             "operation_status": operation.get("status") if isinstance(operation, Mapping) else None,
                             "operation_request_id": operation.get("request_id") if isinstance(operation, Mapping) else None,
                             "requested_operation_id": arguments.get("operation_id") if isinstance(arguments, Mapping) else None,
                             "operation_execution_request_id": operation_execution.get("request_id") if isinstance(operation_execution, Mapping) else None,
                             "worker_event_id": event.get("id"),
                             "worker_event_phase": metadata.get("phase") if isinstance(metadata, Mapping) else None,
                             "worker_event_method": worker_metadata.get("method") if isinstance(worker_metadata, Mapping) else None,
                             "request_id": worker_metadata.get("request_id") if isinstance(worker_metadata, Mapping) else None,
                             "domain_state": domain_outcome.get("state") if isinstance(domain_outcome, Mapping) else None,
                             "execution_state_unknown": result_data.get("execution_state_unknown") if isinstance(result_data, Mapping) else None,
                             "safe_retry": safe_retry,
                             "worker_event": metadata})
    return rows


def _reconcile_study_run_dispatch(
        daemon: Any, preflight: Any, *, response_job_id: str | None,
        before_identities: set[tuple[Any, Any]]) -> dict[str, Any]:
    """Reconcile a single dispatch against its same durable job after waiting.

    A managed dispatch can return a pending job before the Worker submission
    event has reached OperationStore. Calls before the one permitted wait are
    provisional; the call after that wait (including when it raises) is the
    final event snapshot bound to the response's exact durable job identity.
    """
    result: dict[str, Any] = {
        "response_job_id": response_job_id,
        "status": "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP",
        "all_durable_submissions": [],
        "new_durable_submissions_for_this_dispatch": [],
        "response_job_status": None,
        "response_job_operation_id": None,
        "response_job_operation_status": None,
        "response_job_operation_request_id": None,
        "response_job_is_study_run": False,
        "submission_count_for_dispatch": "UNKNOWN",
        "reconciliation_error": None,
    }
    try:
        all_rows = _actual_science_study_run_submissions(daemon, preflight)
    except BaseException as exc:
        result["reconciliation_error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["all_durable_submissions"] = all_rows
    new_rows = [row for row in all_rows
                if (row.get("job_id"), row.get("request_id")) not in before_identities]
    result["new_durable_submissions_for_this_dispatch"] = new_rows
    result["submission_count_for_dispatch"] = len(new_rows) if new_rows else "UNKNOWN"
    if response_job_id is None:
        result["reconciliation_error"] = "dispatch response has no durable job identity"
        return result
    try:
        job = daemon.store.job(response_job_id)
    except BaseException as exc:
        result["reconciliation_error"] = f"{type(exc).__name__}: {exc}"
        return result
    if not isinstance(job, Mapping):
        result["reconciliation_error"] = "response job row is missing after wait"
        return result
    operation = job.get("operation")
    operation = operation if isinstance(operation, Mapping) else {}
    operation_metadata = operation.get("metadata")
    operation_metadata = operation_metadata if isinstance(operation_metadata, Mapping) else {}
    operation_execution = operation_metadata.get("execution")
    operation_execution = operation_execution if isinstance(operation_execution, Mapping) else {}
    result.update({
        "response_job_status": job.get("status"),
        "response_job_operation_id": job.get("operation_id"),
        "response_job_operation_status": operation.get("status"),
        "response_job_operation_request_id": operation.get("request_id"),
        "response_job_execution_request_id": operation_execution.get("request_id"),
        "response_job_is_study_run": _is_study_run_job(job),
    })
    response_rows = [row for row in new_rows if row.get("job_id") == response_job_id]
    result["response_job_worker_submissions"] = response_rows
    identity_bound = (
        len(new_rows) == 1 and len(response_rows) == 1
        and result["response_job_is_study_run"] is True
        and response_rows[0].get("operation_id") == job.get("operation_id")
    )
    if identity_bound:
        result["status"] = "PASS_ONE_DURABLE_WORKER_SUBMISSION_BOUND_TO_DISPATCH_JOB"
    else:
        result["reconciliation_error"] = (
            "post-wait durable Worker submission count or response-job binding is not exactly one")
    return result


def _require_terminal_study_run_success_before_accounting(
        helpers: Mapping[str, Any], terminal_response: Mapping[str, Any],
        label: str, accounting: Mapping[str, Any], *,
        expected_total_dispatch_count: int) -> None:
    """Keep a real terminal COMSOL failure primary over local count mismatch."""
    helpers["require_success"](terminal_response, label)
    reconciliation = accounting.get("post_wait_reconciliation")
    rows = reconciliation.get("all_durable_submissions") if isinstance(reconciliation, Mapping) else None
    if (not isinstance(rows, list)
            or len(rows) != expected_total_dispatch_count):
        raise RuntimeError(
            "Study.run cumulative durable submission count is unverified; stop without another submission")
    if accounting.get("status") != "PASS_ONE_DURABLE_WORKER_SUBMISSION_BOUND_TO_DISPATCH_JOB":
        raise RuntimeError("Study.run durable dispatch accounting is unverified; stop without another submission")


def _runner_study_submission_records(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project durable Worker events in stable event order into runner records.

    OperationStore pages jobs newest-first, while each job's event page is
    oldest-first. Sorting by the globally unique persisted event id makes the
    display-only ordinal independent of job-page order and later insertions.
    """
    if not isinstance(rows, list):
        raise TypeError("durable Study.run submissions must be a list")
    validated: list[tuple[int, Mapping[str, Any]]] = []
    seen_event_ids: set[int] = set()
    seen_identities: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("durable study.run Worker submission row is malformed")
        request_id = row.get("request_id")
        job_id = row.get("job_id")
        event_id = row.get("worker_event_id")
        if not isinstance(job_id, str) or not job_id or not isinstance(request_id, str) or not request_id:
            raise ValueError("durable study.run Worker submission lacks exact job/request identity")
        if type(event_id) is not int or event_id <= 0:
            raise ValueError("durable study.run Worker submission lacks an exact positive worker_event_id")
        if row.get("operation") != "study.run" or row.get("worker_event_phase") != "submitted" \
                or row.get("worker_event_method") != "run":
            raise ValueError("durable study.run row is not an exact submitted Worker method=run event")
        if not isinstance(row.get("operation_id"), str) or not row.get("operation_id"):
            raise ValueError("durable study.run Worker submission lacks its exact operation identity")
        identity = (job_id, request_id)
        if event_id in seen_event_ids or identity in seen_identities:
            raise ValueError("durable study.run Worker submission repeats an event or job/request identity")
        worker_event = row.get("worker_event")
        if not isinstance(worker_event, Mapping):
            raise ValueError("durable study.run Worker submission has no preserved event payload")
        event_metadata = worker_event.get("metadata")
        if (not isinstance(event_metadata, Mapping)
                or event_metadata.get("request_id") != request_id
                or event_metadata.get("method") != "run"
                or worker_event.get("phase") != "submitted"):
            raise ValueError("durable study.run Worker event payload disagrees with its indexed identity")
        seen_event_ids.add(event_id)
        seen_identities.add(identity)
        validated.append((event_id, row))

    validated.sort(key=lambda item: item[0])
    records: list[dict[str, Any]] = []
    for ordinal, (event_id, row) in enumerate(validated, 1):
        request_id = row["request_id"]
        job_id = row["job_id"]
        records.append({
            "ordinal": ordinal,
            "worker_event_id": event_id,
            "at_utc": utc_now(),
            "job_id": job_id,
            "operation": "study.run",
            "request_id": request_id,
            "operation_id": row.get("operation_id"),
            "operation_request_id": row.get("operation_request_id"),
            "durable_job_status": row.get("status"),
            "durable_operation_status": row.get("operation_status"),
            "domain_state": row.get("domain_state"),
            "execution_state_unknown": row.get("execution_state_unknown"),
            "safe_retry": row.get("safe_retry"),
            "worker_event": row.get("worker_event"),
            "method": "model.study('std1').run()",
        })
    return records


def _bind_response_job_study_submission(
        response_rows: Any, runner_records: list[Mapping[str, Any]], *,
        response_job_id: str | None, response_operation_id: Any,
        response_operation_request_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Join the raw dispatch row to its enriched record by durable identity.

    Raw OperationStore rows intentionally have no runner ordinal. The response
    row is authoritative for which job completed; the ordinal is only copied
    from the canonical, globally event-ordered projection after an exact join.
    """
    if not isinstance(response_job_id, str) or not response_job_id:
        raise ValueError("Study.run response has no exact durable job identity")
    if not isinstance(response_operation_id, str) or not response_operation_id:
        raise ValueError("Study.run response job has no exact operation identity")
    if not isinstance(response_operation_request_id, str) or not response_operation_request_id:
        raise ValueError("Study.run response job has no exact operation request identity")
    if not isinstance(response_rows, list) or len(response_rows) != 1:
        raise ValueError("Study.run response job does not have exactly one raw durable Worker submission")
    raw = response_rows[0]
    if not isinstance(raw, Mapping):
        raise ValueError("Study.run response-job Worker submission row is malformed")
    job_id, request_id, event_id = (raw.get("job_id"), raw.get("request_id"),
                                    raw.get("worker_event_id"))
    if (job_id != response_job_id or raw.get("operation_id") != response_operation_id
            or raw.get("operation_request_id") != response_operation_request_id
            or raw.get("operation") != "study.run"
            or raw.get("worker_event_phase") != "submitted"
            or raw.get("worker_event_method") != "run"
            or type(event_id) is not int or event_id <= 0
            or not isinstance(request_id, str) or not request_id):
        raise ValueError("raw Study.run response row does not match exact job/operation/request/event identity")
    matches = [record for record in runner_records
               if isinstance(record, Mapping)
               and record.get("job_id") == job_id
               and record.get("request_id") == request_id
               and record.get("worker_event_id") == event_id]
    if len(matches) != 1:
        raise ValueError("raw Study.run response row has no unique canonical runner record")
    record = matches[0]
    if (record.get("operation_id") != response_operation_id
            or record.get("operation_request_id") != response_operation_request_id
            or type(record.get("ordinal")) is not int or record["ordinal"] <= 0):
        raise ValueError("canonical Study.run runner record does not preserve response identity")
    return dict(raw), dict(record)


def _count_worker_requests(daemon: Any) -> int:
    rows, _ = importlib.import_module("tools.run_native_w23_te_managed_preflight")._page_project_jobs(
        daemon.store, page_size=1000)
    total = 0
    preflight = importlib.import_module("tools.run_native_w23_te_managed_preflight")
    for job in rows:
        events, _ = preflight._page_job_events(daemon.store, str(job["job_id"]), page_size=1000)
        total += sum(1 for row in events if row.get("event") == "worker_request")
    return total


def _unpack_java_response(response: dict[str, Any], helpers: Mapping[str, Any], label: str) -> dict[str, Any]:
    helpers["require_success"](response, label)
    return helpers["preflight_module"]._java_action_readback(response, label)


def _update_ref(response: Mapping[str, Any], daemon: Any,
                ref: dict[str, Any], revision: int) -> tuple[dict[str, Any], int]:
    execution = response.get("execution")
    if isinstance(execution, Mapping):
        new_ref = execution.get("model_ref")
        new_revision = execution.get("revision")
        if isinstance(new_ref, Mapping) and isinstance(new_ref.get("model_tag"), str):
            ref = dict(new_ref)
        if type(new_revision) is int:
            revision = new_revision
            return ref, revision
    from comsol_mcp._execution_contract import model_ref_from_mapping
    state = daemon.service.ledger._state_for(model_ref_from_mapping(ref))
    return ref, int(state.revision)


def _fixture_call(daemon: Any, ref: dict[str, Any], revision: int, fixture_name: str,
                  args: dict[str, Any], helpers: Mapping[str, Any], *,
                  deadline_epoch: float, evidence: Path, label: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    response = _dispatch(
        daemon, "operation_call",
        {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": fixture_name,
            "entrypoint": "NativeW23TEScienceFixture#run",
            "arguments": args, "mode": "trusted"}},
        project_id=_active_project_id(), ref=ref, revision=revision,
        timeout_s=min(300.0, max(1.0, deadline_epoch - time.time() - CLEANUP_RESERVE_S)))
    response = _resolve_wait_response(daemon, response, deadline_epoch=deadline_epoch,
                                      evidence=evidence, label=label)
    result = _unpack_java_response(response, helpers, label)
    ref, revision = _update_ref(response, daemon, ref, revision)
    write_json(evidence / f"{label}.json", {"response": response, "readback": result,
                                              "model_ref": ref, "revision": revision})
    return result, ref, revision


def _data_response(response: Mapping[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise RuntimeError(f"{label} failed: {json.dumps(response, ensure_ascii=False, default=str)[:8000]}")
    return _operation_body(response)


def _frequency_hz(names: list[Any], values: list[Any], units: list[Any]) -> tuple[float, str]:
    aliases = {"f", "freq", "frequency"}  # Exact aliases accepted by the production native router.
    matches = [index for index, name in enumerate(names)
               if str(name).strip().lower() in aliases]
    if len(matches) != 1:
        raise ValueError(f"native SolutionInfo must expose exactly one supported frequency axis; got {names!r}")
    index = matches[0]
    value = values[index]
    unit = str(units[index] or "").strip()
    scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}.get(unit)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or scale is None:
        raise ValueError(f"native frequency value/unit is unsupported: {value!r} {unit!r}")
    return float(value) * scale, str(names[index])


def resolve_mode_overlap_sources(dataset_rows: list[Mapping[str, Any]],
                                 index_by_tag: Mapping[str, Mapping[str, Any]],
                                 solver_tags_by_step: Mapping[str, list[str]]) -> dict[str, Any]:
    """Select only unique, fully native bound sources; never infer missing axes."""
    solution_datasets = [row for row in dataset_rows
                         if isinstance(row, Mapping)
                         and isinstance(row.get("type_id"), str)
                         and row["type_id"].lower() == "solution"]
    unresolved = [row.get("tag") for row in solution_datasets
                  if not isinstance(row.get("tag"), str)
                  or row.get("tag") not in index_by_tag
                  or index_by_tag[row["tag"]].get("binding_complete") is not True]
    if unresolved:
        raise ValueError("native source resolution cannot prove uniqueness while Solution datasets have incomplete axes: "
                         + ", ".join(str(tag) for tag in unresolved))
    output_solver_tags = solver_tags_by_step.get("bmaOutput", [])
    input_solver_tags = solver_tags_by_step.get("bmaInput", [])
    frequency_solver_tags = solver_tags_by_step.get("freq", [])
    if not output_solver_tags or not input_solver_tags or not frequency_solver_tags:
        raise ValueError("generated solver tree does not bind every frozen BMA/frequency step")
    roles = {"signal": set(frequency_solver_tags),
             "reference_mode": set(output_solver_tags),
             "incident_reference": set(input_solver_tags)}
    chosen: dict[str, dict[str, Any]] = {}
    for role, allowed_solutions in roles.items():
        candidates: list[dict[str, Any]] = []
        for dataset in dataset_rows:
            tag = dataset.get("tag")
            type_id = dataset.get("type_id")
            if (not isinstance(tag, str) or not isinstance(type_id, str)
                    or type_id.lower() != "solution"
                    or dataset.get("component") != "comp1" or dataset.get("geometry") != "geom1"):
                continue
            if dataset.get("solution") not in allowed_solutions:
                continue
            axes = index_by_tag.get(tag)
            if not isinstance(axes, Mapping) or axes.get("binding_complete") is not True:
                continue
            solution_id = axes.get("solution")
            if solution_id not in allowed_solutions:
                continue
            pairs = axes.get("parameters", {}).get("by_pair")
            if not isinstance(pairs, Mapping):
                continue
            for pair_key, pair in pairs.items():
                if not isinstance(pair, Mapping):
                    continue
                names = list(pair.get("names") or [])
                values = list(pair.get("values") or [])
                units = list(pair.get("units") or [])
                if len(names) != len(values) or len(names) != len(units):
                    continue
                try:
                    frequency_hz, frequency_name = _frequency_hz(names, values, units)
                except ValueError:
                    continue
                if not math.isclose(frequency_hz, EXPECTED_FREQUENCY_HZ,
                                    rel_tol=1e-12, abs_tol=0.0):
                    continue
                mode_matches = [i for i, name in enumerate(names)
                                if str(name).strip().lower() == "modeindex"]
                if mode_matches and (len(mode_matches) != 1 or values[mode_matches[0]] != 1):
                    continue
                if role != "signal" and len(mode_matches) != 1:
                    continue
                try:
                    outer, inner = (int(part) for part in str(pair_key).split(":"))
                except (TypeError, ValueError):
                    continue
                if outer != 1 or inner != 1 or pair.get("solnum") != 1:
                    continue
                candidates.append({
                    "dataset_id": tag, "solution_id": str(solution_id),
                    "outer_index": outer, "inner_index": inner,
                    "frequency_hz": frequency_hz, "frequency_parameter": frequency_name,
                    "mode_axis_parameter": "modeIndex" if role != "signal" else None,
                    "native_parameter_names": names,
                    "native_parameter_values": values,
                    "native_parameter_units": units,
                    "native_solnum": pair.get("solnum"),
                })
        if len(candidates) != 1:
            raise ValueError(f"{role} requires exactly one native Solution dataset/index pair; found {len(candidates)}")
        chosen[role] = candidates[0]
    signal = chosen["signal"]
    mode = chosen["reference_mode"]
    incident = chosen["incident_reference"]
    if (signal["outer_index"] != mode["outer_index"]
            or signal["outer_index"] != incident["outer_index"]
            or signal["inner_index"] != incident["inner_index"]):
        raise ValueError("native result source indices do not satisfy result.mode_overlap index identity checks")
    return {"status": "PASS_UNIQUE_NATIVE_MODE_OVERLAP_SOURCES", "sources": chosen,
            "solver_step_sequence_tags": {key: list(value) for key, value in solver_tags_by_step.items()},
            "caller_arrays_used": False}


def build_mode_overlap_definition(source_map: Mapping[str, Any],
                                  output_normal_sign: int,
                                  input_normal_sign: int, *, field_scheme: str = "legacy") -> dict[str, Any]:
    from tools.w23_te_science_protocol import field_scheme_roles
    roles = field_scheme_roles(field_scheme)
    sources = source_map.get("sources")
    if not isinstance(sources, Mapping):
        raise ValueError("source map is missing resolved native sources")
    def source(role: str) -> dict[str, Any]:
        item = sources.get(role)
        if not isinstance(item, Mapping):
            raise ValueError(f"source map lacks {role}")
        return {key: item[key] for key in ("dataset_id", "solution_id", "outer_index", "inner_index")}
    def fields(e_prefix: str, h_prefix: str, suffix: str = "") -> dict[str, Any]:
        if not suffix:
            return {"electric":dict(roles["signal_e"]),"magnetic":dict(roles["signal_h"])}
        port=suffix.removeprefix("_")
        return {"electric":dict(roles["mode_e"][port]),"magnetic":dict(roles["mode_h"][port])}
    return {
        "schema_version": "1.0.0",
        "phasor_convention": {
            "time_dependence": "exp(-i omega t)",
            "complex_field_representation": "full_physical_complex_phasor_including_reconstructed_envelope_phase",
        },
        "output_surface": {"plane_id": "output_x8",
                           "selection": {"component": "comp1", "geometry": "geom1", "tag": "selReceiverX8"},
                           "normal_sign": output_normal_sign},
        "signal": {"field_id": "native_frequency_signal", "source": source("signal"),
                   "fields": fields("E", "H")},
        "reference_mode": {"mode_id": "output_port_mode_1", "mode_axis_parameter": "modeIndex",
                           "source": source("reference_mode"),
                           "fields": fields("Emode", "Hmode", "_2")},
        "incident_reference": {"reference_id": "input_port_mode_1", "input_plane_id": "input_xminus10",
                                "selection": {"component": "comp1", "geometry": "geom1", "tag": "selInputPort"},
                                "normal_sign": input_normal_sign,
                                "source": source("incident_reference"),
                                "fields": fields("Emode", "Hmode", "_1")},
        "capture": {"aperture_id": "receiver_core_aperture", "plane_id": "output_x8",
                    "selection": {"component": "comp1", "geometry": "geom1", "tag": "selCoreCaptureX8"},
                    "normal_sign": output_normal_sign,
                    "incident_reference_id": "input_port_mode_1"},
        "power_floor": {"value": 1e-12, "unit": "W/m"},
    }


def validate_capture_aperture_readback(build: Mapping[str, Any]) -> dict[str, Any]:
    """Require a distinct, native-read-back core aperture on the x=8 plane."""
    if not isinstance(build, Mapping):
        raise ValueError("native fixture build receipt is not an object")
    plane = build.get("science_plane")
    geometry = build.get("geometry")
    selection_rows = build.get("selections")
    capture_geometry = geometry.get("capture_aperture") if isinstance(geometry, Mapping) else None
    if (not isinstance(plane, Mapping) or plane.get("x_um") != 8.0
            or not isinstance(capture_geometry, Mapping)
            or capture_geometry.get("plane_x_um") != 8.0
            or capture_geometry.get("y_um") != [-0.5, 0.5]
            or capture_geometry.get("selection_tag") != "selCoreCaptureX8"):
        raise ValueError("capture aperture geometry is not the frozen core-only x=8 um region")
    if not isinstance(selection_rows, list):
        raise ValueError("fixture build receipt lacks native named-selection readbacks")
    by_tag: dict[str, Mapping[str, Any]] = {}
    for row in selection_rows:
        if isinstance(row, Mapping) and isinstance(row.get("tag"), str):
            if row["tag"] in by_tag:
                raise ValueError("fixture build receipt has duplicate selection readbacks")
            by_tag[row["tag"]] = row
    capture, output = by_tag.get("selCoreCaptureX8"), by_tag.get("selReceiverX8")
    if not isinstance(capture, Mapping) or not isinstance(output, Mapping):
        raise ValueError("native output and capture selection readbacks are both required")
    if capture.get("entity_dimension") != 1 or output.get("entity_dimension") != 1:
        raise ValueError("native output and capture selections must both be boundary entities")
    capture_ids, output_ids = capture.get("entity_ids"), output.get("entity_ids")
    if (not isinstance(capture_ids, list) or not capture_ids
            or not isinstance(output_ids, list) or not output_ids
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 1
                   for item in (*capture_ids, *output_ids))):
        raise ValueError("native output or capture selection has invalid entity IDs")
    if len(set(capture_ids)) != len(capture_ids) or len(set(output_ids)) != len(output_ids):
        raise ValueError("native output or capture selection repeats entity IDs")
    if not set(capture_ids) < set(output_ids):
        raise ValueError("capture selection must be a strict native-entity subset of the output measurement plane")
    return {
        "status": "PASS_NATIVE_CAPTURE_SELECTION_READBACK",
        "aperture_id": "receiver_core_aperture", "plane_id": "output_x8",
        "geometry_y_bounds_um": [-0.5, 0.5],
        "selection_tag": "selCoreCaptureX8", "entity_dimension": 1,
        "entity_ids": list(capture_ids), "output_plane_entity_ids": list(output_ids),
        "native_selection_is_strict_subset": True,
    }


def validate_material_tensor_readback(build: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct only documented getter forms with matching stored-size evidence."""
    if not isinstance(build, Mapping):
        raise ValueError("native fixture build receipt is not an object")
    rows = build.get("materials")
    if not isinstance(rows, list):
        raise ValueError("fixture build receipt lacks material getter readbacks")
    expected = {
        "matCore": ("selCoreMaterial", "n_core^2"),
        "matCladdingBottom": ("selCladdingBottom", "n_clad^2"),
        "matCladdingTop": ("selCladdingTop", "n_clad^2"),
    }
    by_tag: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str):
            raise ValueError("material readback row is malformed")
        if row["tag"] in by_tag:
            raise ValueError("material readback contains duplicate tags")
        by_tag[row["tag"]] = row
    if set(by_tag) != set(expected):
        raise ValueError("material readback does not contain the exact frozen core and cladding materials")

    def require_finite_zero_conductivity(payload: Any, *, tag: str) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError(f"UNVERIFIED {tag} electricconductivity: typed getter payload is missing")
        if payload.get("readback_status") != "CAPTURED":
            raise ValueError(f"UNVERIFIED {tag} electricconductivity: typed getter readback is not captured")
        stored_size = payload.get("stored_size")
        if (payload.get("size_getter") != "MaterialModel.size"
                or payload.get("stored_size_status") != "CAPTURED"
                or stored_size not in ([1, 1], [3, 3])):
            raise ValueError(
                f"UNVERIFIED {tag} electricconductivity: MaterialModel.size must be captured as [1,1] or [3,3]")

        value_type, getter = payload.get("value_type"), payload.get("getter")
        raw, shape = payload.get("raw_value"), payload.get("raw_shape")
        if (value_type == "String" and getter == "MaterialModel.getString"
                and isinstance(raw, str) and shape == []):
            values = [raw]
            form = "String-scalar"
        elif (value_type == "StringArray" and getter == "MaterialModel.getStringArray"
              and isinstance(raw, list) and len(raw) == 1
              and all(isinstance(value, str) for value in raw) and shape == [1]):
            values = raw
            form = "StringArray-one-entry-scalar"
        else:
            raise ValueError(
                f"UNVERIFIED {tag} electricconductivity: expected matching String/getString raw_shape [] or StringArray/getStringArray raw_shape [1]")

        for value in values:
            match = re.fullmatch(
                r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:\[([^\]]+)\])?\s*",
                value)
            if match is None or (match.group(2) is not None and match.group(2).strip() != "S/m"):
                raise ValueError(
                    f"UNVERIFIED {tag} electricconductivity: expected a finite numeric zero in native S/m units, got {value!r}")
            try:
                exact_numeric = Decimal(match.group(1))
            except InvalidOperation as exc:
                raise ValueError(
                    f"UNVERIFIED {tag} electricconductivity: conductivity is not a valid decimal value, got {value!r}") from exc
            if not exact_numeric.is_finite():
                raise ValueError(
                    f"UNVERIFIED {tag} electricconductivity: conductivity must be finite zero S/m, got {value!r}")
            try:
                numeric = float(exact_numeric)
            except (OverflowError, ValueError) as exc:
                raise ValueError(
                    f"UNVERIFIED {tag} electricconductivity: conductivity is outside finite numeric range, got {value!r}") from exc
            # Decimal comparison prevents tiny nonzero values (for example
            # 1e-400) from underflowing to float(0.0) and passing this gate.
            if not math.isfinite(numeric) or exact_numeric != Decimal(0):
                raise ValueError(
                    f"UNVERIFIED {tag} electricconductivity: conductivity must be finite zero S/m, got {value!r}")
        return {"value_type": value_type, "getter": getter,
                "raw_shape": list(shape), "stored_size": list(stored_size),
                "size_getter": payload.get("size_getter"), "raw_value": list(values),
                "reconstruction": form, "numeric_value_S_per_m": 0.0,
                "finite": True, "zero": True}

    def reconstruct_tensor(payload: Any, *, tag: str, property_name: str) -> tuple[list[list[str]], dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ValueError(f"UNVERIFIED {tag} {property_name}: getter payload is missing")
        value_type, getter = payload.get("value_type"), payload.get("getter")
        raw, shape = payload.get("raw_value"), payload.get("raw_shape")
        stored_size = payload.get("stored_size")
        if payload.get("readback_status") != "CAPTURED":
            raise ValueError(f"UNVERIFIED {tag} {property_name}: typed getter readback is not captured")
        if (payload.get("size_getter") != "MaterialModel.size"
                or payload.get("stored_size_status") != "CAPTURED"
                or not isinstance(stored_size, list) or len(stored_size) != 2
                or any(type(dimension) is not int or dimension <= 0 for dimension in stored_size)):
            raise ValueError(
                f"UNVERIFIED {tag} {property_name}: MaterialModel.size must return two positive integer dimensions")

        def size_product_is(expected: int) -> bool:
            return stored_size[0] * stored_size[1] == expected

        def has_exact_integer_shape(expected_shape: list[int]) -> bool:
            return (isinstance(shape, list) and len(shape) == len(expected_shape)
                    and all(type(dimension) is int for dimension in shape)
                    and shape == expected_shape)
        scalar_property_size = stored_size in ([1, 1], [3, 3])
        if value_type == "String" and getter == "MaterialModel.getString":
            if not isinstance(raw, str) or shape != [] or not scalar_property_size:
                raise ValueError(
                    f"UNVERIFIED {tag} {property_name}: String scalar requires raw_shape [] and captured size [1,1] or [3,3]")
            matrix = [[raw if r == c else "0" for c in range(3)] for r in range(3)]
            form = ("String-isotropic-scalar-property-size-1x1" if stored_size == [1, 1]
                    else "String-isotropic-scalar-property-size-3x3")
        elif value_type == "StringArray" and getter == "MaterialModel.getStringArray":
            if (not isinstance(raw, list) or any(not isinstance(value, str) for value in raw)):
                raise ValueError(f"UNVERIFIED {tag} {property_name}: StringArray values are malformed")
            if len(raw) == 1 and has_exact_integer_shape([1]) and scalar_property_size:
                # COMSOL may expose the canonical one-value isotropic storage
                # shape [1] for a property whose MaterialModel.size is [3,3].
                # Preserve both raw shape and literal API size, then validate all
                # nine reconstructed physical entries below.
                matrix = [[raw[0] if r == c else "0" for c in range(3)] for r in range(3)]
                form = ("StringArray-isotropic-one-entry-property-size-1x1" if stored_size == [1, 1]
                        else "StringArray-isotropic-one-entry-property-size-3x3")
            elif len(raw) == 9 and has_exact_integer_shape([9]) and size_product_is(9):
                # Programming Reference 6.4 p.152 specifies column-wise matrix
                # readback. This fixture uses symmetric diagonal tensors, so no
                # nonsymmetric ordering claim is needed.
                matrix = [[raw[column * 3 + row] for column in range(3)] for row in range(3)]
                form = "StringArray-nine-column-wise"
            else:
                raise ValueError(
                    f"UNVERIFIED {tag} {property_name}: StringArray requires exact isotropic raw_shape [1] with stored size [1,1] or [3,3], or full raw_shape [9] with size product 9")
        elif value_type == "StringMatrix" and getter == "MaterialModel.getStringMatrix":
            if (not isinstance(raw, list) or len(raw) != 3
                    or any(not isinstance(row, list) or len(row) != 3 for row in raw)
                    or any(not isinstance(cell, str) for row in raw for cell in row)
                    or not has_exact_integer_shape([3, 3]) or stored_size != [3, 3]):
                raise ValueError(
                    f"UNVERIFIED {tag} {property_name}: StringMatrix requires actual 3x3 strings, raw_shape [3,3], and stored size [3,3]")
            matrix = [list(row) for row in raw]
            form = "StringMatrix-three-by-three"
        else:
            raise ValueError(
                f"UNVERIFIED {tag} {property_name}: unsupported or mismatched value_type/getter {value_type!r}/{getter!r}")
        return matrix, {"value_type": value_type, "getter": getter,
                        "raw_shape": list(shape), "stored_size": list(stored_size),
                        "size_getter": payload.get("size_getter"), "reconstruction": form}

    checked = []
    for tag, (selection, expected_eps) in expected.items():
        row = by_tag[tag]
        if row.get("selection") != selection:
            raise ValueError(f"{tag} material selection readback differs from the frozen selection")
        if row.get("expected_diagonal_relative_permittivity") != expected_eps:
            raise ValueError(f"{tag} expected permittivity expression differs from the frozen material")
        eps_matrix, eps_form = reconstruct_tensor(row.get("relative_permittivity"), tag=tag,
                                                  property_name="relpermittivity")
        mu_matrix, mu_form = reconstruct_tensor(row.get("relative_permeability"), tag=tag,
                                                property_name="relpermeability")
        conductivity_form = require_finite_zero_conductivity(
            row.get("electric_conductivity"), tag=tag)
        if row.get("electric_lossless_assumption") != (
                "real_relative_permittivity_with_electric_conductivity_set_to_zero_S_per_m"):
            raise ValueError(f"UNVERIFIED {tag}: fixture did not declare the frozen lossless material model")
        for property_name, matrix, diagonal in (
                ("relpermittivity", eps_matrix, expected_eps),
                ("relpermeability", mu_matrix, "1")):
            for r in range(3):
                for c in range(3):
                    wanted = diagonal if r == c else "0"
                    if matrix[r][c] != wanted:
                        raise ValueError(
                            f"{tag} {property_name}[{r},{c}] read back as {matrix[r][c]!r}, expected {wanted!r}")
        checked.append({"tag": tag, "selection": selection,
                        "relative_permittivity_value_type": eps_form["value_type"],
                        "relative_permittivity_getter": eps_form["getter"],
                        "relative_permittivity_raw_shape": eps_form["raw_shape"],
                        "relative_permittivity_stored_size": eps_form["stored_size"],
                        "relative_permittivity_reconstruction": eps_form["reconstruction"],
                        "relative_permittivity_diagonal": expected_eps,
                        "relative_permeability_value_type": mu_form["value_type"],
                        "relative_permeability_getter": mu_form["getter"],
                        "relative_permeability_raw_shape": mu_form["raw_shape"],
                        "relative_permeability_stored_size": mu_form["stored_size"],
                        "relative_permeability_reconstruction": mu_form["reconstruction"],
                        "relative_permeability_diagonal": "1",
                        "electric_conductivity_value_type": conductivity_form["value_type"],
                        "electric_conductivity_getter": conductivity_form["getter"],
                        "electric_conductivity_raw_shape": conductivity_form["raw_shape"],
                        "electric_conductivity_stored_size": conductivity_form["stored_size"],
                        "electric_conductivity_reconstruction": conductivity_form["reconstruction"],
                        "electric_conductivity_raw_value": conductivity_form["raw_value"],
                        "electric_conductivity_value_S_per_m": conductivity_form["numeric_value_S_per_m"],
                        "electric_conductivity_finite_zero_verified": True,
                        "off_diagonal_entries": "all exactly 0"})
    return {
        "status": "PASS_NATIVE_MATERIAL_TENSOR_AND_LOSSLESS_CONDUCTIVITY_READBACK",
        "requested_storage_form": "9-entry String[] for symmetric diagonal fixture values; COMSOL may canonicalize isotropic tensors to a one-entry stored form",
        "string_array_index_order": "COMSOL 6.4 documented column-wise readback; no nonsymmetric ordering claim is made",
        "material_count": len(checked), "materials": checked,
        "physical_tensor_diagonal_and_zero_off_diagonal_verified": True,
        "electric_conductivity_is_finite_zero_S_per_m_for_every_material": True,
    }


def validate_mesh_readback(readback: Mapping[str, Any], expected_level: str) -> dict[str, Any]:
    """Require the frozen FreeTri, full-domain, native mesh readback for one level."""
    if not isinstance(readback, Mapping):
        raise ValueError("native mesh readback is not an object")
    if expected_level not in MESH_LEVEL_SPECS:
        raise ValueError(f"unsupported frozen mesh level {expected_level!r}")
    expected_hmax, expected_hmin = MESH_LEVEL_SPECS[expected_level]
    if readback.get("mesh_level") != expected_level:
        raise ValueError("native mesh readback does not match the requested frozen mesh level")
    if readback.get("hmax") != expected_hmax or readback.get("hmin") != expected_hmin:
        raise ValueError("native mesh hmax/hmin readback differs from the frozen mesh level")
    elements = readback.get("elements")
    if type(elements) is not int or elements <= 0:
        raise ValueError(f"native mesh total element count must be a positive integer; got {elements!r}")
    tri_elements = readback.get("tri_elements")
    if type(tri_elements) is not int or tri_elements <= 0 or tri_elements > elements:
        raise ValueError(f"native FreeTri element count must be a positive integer no greater than total elements; got {tri_elements!r}")
    if readback.get("mesh_is_empty") is not False:
        raise ValueError("native MeshSequence.isEmpty() must read back false")
    if readback.get("mesh_is_complete") is not True:
        raise ValueError("native MeshSequence.isComplete() must prove the selected geometry is meshed")
    if readback.get("free_tri_tag") != "ftri1" or readback.get("free_tri_built") is not True:
        raise ValueError("native FreeTri generator ftri1 must exist and report built")
    if type(readback.get("free_tri_selection_dimension")) is not int \
            or readback.get("free_tri_selection_dimension") != 2:
        raise ValueError("FreeTri must read back an explicit dimension-2 domain selection")
    domain_count = readback.get("geometry_domain_count")
    domains = readback.get("free_tri_selected_domains")
    if type(domain_count) is not int or domain_count <= 0 or not isinstance(domains, list):
        raise ValueError("native FreeTri geometry/domain selection readback is missing")
    if (any(type(domain) is not int or domain <= 0 for domain in domains)
            or len(set(domains)) != len(domains) or len(domains) != domain_count):
        raise ValueError("FreeTri selected-domain IDs must be unique and cover the full 2D geometry domain count")
    element_types = readback.get("element_types")
    if (not isinstance(element_types, list)
            or any(not isinstance(element_type, str) for element_type in element_types)
            or "tri" not in element_types):
        raise ValueError("native mesh element types must include the FreeTri triangle type 'tri'")
    if readback.get("study_or_solver_invoked") is not False:
        raise ValueError("mesh readback must remain a no-study/no-solver operation")
    return {
        "status": "PASS_NATIVE_FULL_DOMAIN_FREETRI_MESH_READBACK",
        "mesh_level": expected_level,
        "expected_hmax": expected_hmax,
        "actual_hmax": readback.get("hmax"),
        "expected_hmin": expected_hmin,
        "actual_hmin": readback.get("hmin"),
        "total_element_count": elements,
        "triangle_element_count": tri_elements,
        "element_types": list(element_types),
        "mesh_is_empty": False,
        "mesh_is_complete": True,
        "free_tri_tag": "ftri1",
        "free_tri_built": True,
        "free_tri_selection_dimension": 2,
        "free_tri_selected_domains": list(domains),
        "geometry_domain_count": domain_count,
        "full_2d_domain_coverage_by_selection_and_count": True,
        "study_or_solver_invoked": False,
    }


def _require_and_record_mesh_readback(readback: Mapping[str, Any], expected_level: str,
                                      *, evidence: Path, label: str) -> dict[str, Any]:
    try:
        gate = validate_mesh_readback(readback, expected_level)
    except (TypeError, ValueError) as exc:
        write_json(evidence / f"{label}_mesh_readback_gate.json", {
            "status": "FAIL_NATIVE_FULL_DOMAIN_FREETRI_MESH_READBACK",
            "mesh_level": expected_level,
            "error": str(exc),
            "raw_readback": readback,
            "study_or_solver_invoked": False,
        })
        raise RuntimeError(f"native mesh readback failed its frozen gate for {label}: {exc}") from exc
    write_json(evidence / f"{label}_mesh_readback_gate.json", gate)
    return gate


def _managed_call(daemon: Any, operation_id: str, arguments: dict[str, Any],
                  ref: dict[str, Any], revision: int, *, deadline_epoch: float,
                  evidence: Path, label: str, timeout_cap_s: float = 300.0,
                  helpers: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int, dict[str, Any]]:
    if deadline_epoch - time.time() <= CLEANUP_RESERVE_S:
        raise TimeoutError("birth deadline entered cleanup reserve; no more managed RPCs may be submitted")
    timeout_s = min(timeout_cap_s,
                    max(1.0, deadline_epoch - time.time() - CLEANUP_RESERVE_S))
    response = _dispatch(
        daemon, "operation_call", {"operation_id": operation_id, "arguments": arguments},
        project_id=_active_project_id(), ref=ref, revision=revision, timeout_s=timeout_s)
    response = _resolve_wait_response(daemon, response, deadline_epoch=deadline_epoch,
                                      evidence=evidence, label=label)
    body = _data_response(response, label)
    ref, revision = _update_ref(response, daemon, ref, revision)
    write_json(evidence / f"{label}.json", {"operation_id": operation_id,
                "response": response, "body": body, "model_ref": ref,
                "revision": revision})
    return body, ref, revision, response


def _dataset_inventory(daemon: Any, ref: dict[str, Any], revision: int, *,
                       deadline_epoch: float, evidence: Path,
                       helpers: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int]:
    listed, ref, revision, _ = _managed_call(
        daemon, "dataset.list", {}, ref, revision, deadline_epoch=deadline_epoch,
        evidence=evidence, label="dataset_list", helpers=helpers)
    rows = listed.get("datasets")
    if not isinstance(rows, list):
        raise RuntimeError("dataset.list did not return a native datasets list")
    index_by_tag: dict[str, Mapping[str, Any]] = {}
    index_receipts = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("type_id", "")).lower() != "solution":
            continue
        tag = row.get("tag")
        if not isinstance(tag, str):
            continue
        indices, ref, revision, _ = _managed_call(
            daemon, "dataset.solution_indices", {"path": tag}, ref, revision,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label=f"solution_indices_{tag}", helpers=helpers)
        index_receipts.append({"dataset": dict(row), "solution_indices": indices})
        if indices.get("binding_complete") is True:
            index_by_tag[tag] = indices
    solution_tags = [row.get("tag") for row in rows
                     if isinstance(row, Mapping)
                     and isinstance(row.get("type_id"), str)
                     and row["type_id"].lower() == "solution"]
    unresolved = [tag for tag in solution_tags if tag not in index_by_tag]
    if unresolved:
        raise RuntimeError(
            "native source routing is ambiguous because one or more Solution datasets lack complete axes: "
            + ", ".join(str(tag) for tag in unresolved))
    write_json(evidence / "native_dataset_source_inventory.json", {
        "dataset_list": listed, "solution_index_receipts": index_receipts,
        "all_dataset_indices_complete": len(index_by_tag) == sum(
            1 for row in rows if isinstance(row, Mapping)
            and str(row.get("type_id", "")).lower() == "solution"),
    })
    return {"datasets": rows, "solution_index_receipts": index_receipts,
            "index_by_tag": index_by_tag}, ref, revision


def _solver_step_map(binding_receipt: Mapping[str, Any]) -> dict[str, list[str]]:
    rows = binding_receipt.get("study_step_bindings_in_solver_tree_order")
    if not isinstance(rows, list):
        raise ValueError("solver binding receipt is malformed")
    result: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("solver step binding row is malformed")
        tag, sequence = row.get("studystep"), row.get("solver_sequence")
        if isinstance(tag, str) and isinstance(sequence, str):
            result.setdefault(tag, [])
            if sequence not in result[tag]:
                result[tag].append(sequence)
    return result


def _raw_fields_call(daemon: Any, ref: dict[str, Any], revision: int,
                     fixture_name: str, dataset_id: str, plane: str, samples: int,
                     helpers: Mapping[str, Any], *, deadline_epoch: float,
                     evidence: Path, label: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    args = {"phase": "raw_fields", "dataset": dataset_id, "plane": plane,
            "samples": samples, "inner_index": 1, "outer_index": 1}
    return _fixture_call(daemon, ref, revision, fixture_name, args, helpers,
                         deadline_epoch=deadline_epoch, evidence=evidence,
                         label=label)


def _raw_rows(raw: Mapping[str, Any]) -> dict[str, list[complex]]:
    if raw.get("complex_readback") is not True:
        raise ValueError("native raw field export did not preserve complex values")
    rows = raw.get("expressions")
    if not isinstance(rows, list):
        raise ValueError("native raw field export has no expression list")
    result: dict[str, list[complex]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("expression"), str):
            raise ValueError("native raw field expression row is malformed")
        name = row["expression"]
        if name in result:
            raise ValueError(f"native raw field export repeats expression {name!r}")
        real, imag = row.get("real"), row.get("imag")
        if not isinstance(real, list) or not isinstance(imag, list) or len(real) != len(imag):
            raise ValueError(f"native raw field arrays are malformed for {name}")
        result[name] = [complex(float(a), float(b)) for a, b in zip(real, imag)]
    return result


def _port_neff(raw: Mapping[str, Any], expression: str) -> dict[str, Any]:
    fields = _raw_rows(raw)
    values = fields.get(expression)
    if not values:
        raise ValueError(f"documented native port mode index {expression!r} was not returned")
    mean = sum(values, 0j) / len(values)
    scatter = max(abs(value - mean) for value in values)
    if not all(math.isfinite(value.real) and math.isfinite(value.imag) for value in values):
        raise ValueError(f"native effective mode index {expression} contains non-finite values")
    if scatter > 1e-8 or abs(mean.imag) > 1e-8:
        raise ValueError(f"native effective mode index {expression} is not a stable lossless scalar")
    return {"expression": expression, "real": mean.real, "imag": mean.imag,
            "maximum_spatial_scatter": scatter}


def _validate_mode_index(raw: Mapping[str, Any], expression: str,
                         tolerance: float) -> dict[str, Any]:
    observed = _port_neff(raw, expression)
    error = abs(observed["real"] - EXPECTED_NEFF_TE0)
    result = {**observed, "analytic_te0": EXPECTED_NEFF_TE0,
              "absolute_error": error, "tolerance": tolerance,
              "status": "PASS" if error <= tolerance else "FAIL"}
    if error > tolerance:
        raise ValueError(f"{expression} differs from independent TE0 neff by {error} > {tolerance}")
    return result


def _native_overlap_call(daemon: Any, ref: dict[str, Any], revision: int,
                         definition: dict[str, Any], helpers: Mapping[str, Any], *,
                         deadline_epoch: float, evidence: Path,
                         label: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    body, ref, revision, _ = _managed_call(
        daemon, "result.mode_overlap", {"definition": definition}, ref, revision,
        deadline_epoch=deadline_epoch, evidence=evidence, label=label,
        helpers=helpers)
    if body.get("status") != "SUCCEEDED" or body.get("result_status") != "COMPUTED_NATIVE_INTEGRALS":
        raise RuntimeError(f"{label} did not return COMPUTED_NATIVE_INTEGRALS")
    if body.get("evidence", {}).get("cleanup_verified") is not True:
        raise RuntimeError(f"{label} left a temporary native integration feature behind")
    return body, ref, revision


def _assert_call_event_terminal(daemon: Any, row: Mapping[str, Any]) -> str:
    job_id = row.get("job_id")
    request_id = row.get("request_id")
    if not isinstance(job_id, str) or not isinstance(request_id, str):
        raise RuntimeError("persisted study.run event is missing exact job/request identity")
    events = _worker_events_for_job(daemon, job_id)
    submitted = [event for event in events if event.get("event") == "worker_request"
                 and isinstance(event.get("metadata"), Mapping)
                 and event["metadata"].get("phase") == "submitted"
                 and event["metadata"].get("request_id") == request_id]
    observed = [event for event in events if event.get("event") == "worker_request"
                and isinstance(event.get("metadata"), Mapping)
                and event["metadata"].get("phase") == "observed"
                and event["metadata"].get("request_id") == request_id]
    if len(submitted) != 1 or len(observed) != 1:
        raise RuntimeError("study.run Worker request lacks one exact submitted and observed event")
    status = str(observed[0]["metadata"].get("status", "")).upper()
    if status != "SUCCEEDED":
        raise RuntimeError(f"native Study.run Worker status is {status or 'UNKNOWN'}; stop without retry")
    job = daemon.store.job(job_id)
    if not isinstance(job, Mapping) or job.get("status") != "SUCCEEDED":
        raise RuntimeError(f"native Study.run durable job is not SUCCEEDED: {job!r}")
    result = job.get("result")
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise RuntimeError("native Study.run durable job result is failed or ambiguous")
    data = result.get("data")
    if isinstance(data, Mapping):
        if data.get("execution_state_unknown") is True:
            raise RuntimeError("native Study.run has execution_state_unknown=true")
        outcome = data.get("domain_outcome")
        if isinstance(outcome, Mapping) and outcome.get("state") == "unknown":
            raise RuntimeError("native Study.run durable domain state is UNKNOWN")
    return status


def _route_negative_control(daemon: Any, ref: dict[str, Any], revision: int, *,
                            evidence: Path, deadline_epoch: float) -> dict[str, Any]:
    before = _count_worker_requests(daemon)
    invalid = _dispatch(daemon, "operation_call", {
        "operation_id": "result.mode_overlap",
        "arguments": {"definition": {}, "field_arrays": [[1.0]], "coordinates": [[0.0]]},
    }, project_id=_active_project_id(), ref=ref, revision=revision,
       timeout_s=min(30.0, max(1.0, deadline_epoch - time.time() - CLEANUP_RESERVE_S)))
    after = _count_worker_requests(daemon)
    error = invalid.get("error") if isinstance(invalid.get("error"), Mapping) else {}
    code = error.get("code")
    result = {"status": "PASS_CALLER_ARRAY_REJECTED_BEFORE_WORKER_DISPATCH"
              if code == "INVALID_REQUEST" and before == after else "FAIL",
              "error_code": code, "worker_request_count_before": before,
              "worker_request_count_after": after,
              "response": invalid, "dispatch_count": 1}
    write_json(evidence / "native_route_negative_control.json", result)
    if result["status"] != "PASS_CALLER_ARRAY_REJECTED_BEFORE_WORKER_DISPATCH":
        raise RuntimeError("native route negative control failed or reached the Worker")
    return result


def _save_case_model(daemon: Any, ref: dict[str, Any], revision: int,
                     fixture_name: str, case_id: str, helpers: Mapping[str, Any], *,
                     project_workspace: Path, deadline_epoch: float,
                     evidence: Path, receipt_status: str = "PASS_MODEL_SAVED",
                     artifact_role: str = "science_case_model",
                     scientific_acceptance: str = "NOT_ASSESSED") -> tuple[Path, dict[str, Any], dict[str, Any], int]:
    project_root = project_workspace.resolve(strict=True)
    if not project_root.is_dir():
        raise RuntimeError("registered science project workspace is not a directory")
    path = project_root / f"w23_{case_id}.mph"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite frozen case model {path}")
    readback, ref, revision = _fixture_call(
        daemon, ref, revision, fixture_name, {"phase": "save", "path": str(path)},
        helpers, deadline_epoch=deadline_epoch, evidence=evidence,
        label=f"save_{case_id}")
    if readback.get("study_or_solver_invoked") is not False:
        raise RuntimeError("COMSOL Model.save readback does not prove a no-study/no-solver save phase")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("COMSOL Model.save did not produce a nonempty case MPH")
    receipt = {"status": receipt_status, "artifact_role": artifact_role,
               "scientific_acceptance": scientific_acceptance, "case_id": case_id,
               "path": str(path), "bytes": path.stat().st_size,
               "sha256": sha256_file(path), "java_save_readback": readback}
    write_json(evidence / f"saved_{case_id}.json", receipt)
    return path, receipt, ref, revision


def _complete_terminal_study_run_case(
        daemon: Any, helpers: Mapping[str, Any], *,
        terminal_response: Mapping[str, Any], accounting: Mapping[str, Any],
        expected_total_dispatch_count: int, post_wait_reconciliation: Mapping[str, Any],
        study_submissions: list[dict[str, Any]], response_job_id: str,
        case_id: str, completed_case_count: int, ref: dict[str, Any], revision: int,
        fixture_name: str, project_workspace: Path, deadline_epoch: float,
        evidence: Path, summary: dict[str, Any],
        provisional_saved_models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Bind one succeeded run, save a provisional MPH, then enter readback.

    This keeps the raw response row separate from its canonical runner record,
    makes the saved file available before post-solve analysis can fail, and
    starts only the existing zero-solve solution-inventory call afterward.
    """
    _require_terminal_study_run_success_before_accounting(
        helpers, terminal_response, f"Study.run {case_id}", accounting,
        expected_total_dispatch_count=expected_total_dispatch_count)
    raw_row, submission = _bind_response_job_study_submission(
        post_wait_reconciliation.get("response_job_worker_submissions"),
        study_submissions,
        response_job_id=response_job_id,
        response_operation_id=post_wait_reconciliation.get("response_job_operation_id"),
        response_operation_request_id=post_wait_reconciliation.get(
            "response_job_operation_request_id"))
    observed_status = _assert_call_event_terminal(daemon, {
        "job_id": raw_row["job_id"], "request_id": raw_row["request_id"]})
    ref, revision = _update_ref(terminal_response, daemon, ref, revision)
    append_jsonl(evidence / "events.jsonl", {
        "at_utc": utc_now(), "event": "study_run_case_terminal_succeeded",
        "case_id": case_id, "submission_ordinal": submission["ordinal"],
        "worker_event_id": submission["worker_event_id"],
        "job_id": raw_row["job_id"], "request_id": raw_row["request_id"],
        "worker_status": observed_status,
        "study_run_call_count": len(study_submissions),
        "solver_execution_count": "NOT_DERIVED"})

    if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
        raise TimeoutError("birth deadline entered cleanup reserve before provisional model save")
    provisional_id = f"{case_id}_provisional"
    provisional_path, provisional_receipt, ref, revision = _save_case_model(
        daemon, ref, revision, fixture_name, provisional_id, helpers,
        project_workspace=project_workspace, deadline_epoch=deadline_epoch,
        evidence=evidence,
        receipt_status="CAPTURED_PROVISIONAL_MPH_BEFORE_SOLUTION_READBACK",
        artifact_role="provisional_model_capture_only",
        scientific_acceptance="NOT_ACCEPTED")
    provisional_saved_models[case_id] = {
        **provisional_receipt,
        "path": str(provisional_path),
        "parent_case_id": case_id,
        "study_run_submission": submission,
        "scientific_acceptance": "NOT_ACCEPTED",
    }
    summary.update({
        "status": "NATIVE_STUDY_CASE_TERMINAL_PROVISIONAL_MODEL_CAPTURED",
        "study_or_solver_invoked": True,
        "study_run_submission_count": len(study_submissions),
        "study_run_terminal_case_count": completed_case_count,
        "active_case": case_id,
        "provisional_saved_models": dict(provisional_saved_models),
        "latest_case_provisional_model": provisional_saved_models[case_id],
        "native_scientific_acceptance": "NOT_ACCEPTED",
    })
    write_json(evidence / "summary.json", summary)

    inventory, ref, revision = _fixture_call(
        daemon, ref, revision, fixture_name,
        {"phase": "solution_inventory"}, helpers,
        deadline_epoch=deadline_epoch, evidence=evidence,
        label=f"solution_inventory_{case_id}")
    return {
        "response_job_worker_submission": raw_row,
        "submission": submission,
        "observed_status": observed_status,
        "provisional_saved_model": provisional_saved_models[case_id],
        "inventory": inventory,
        "ref": ref,
        "revision": revision,
    }


def _create_project_bound_model(daemon: Any, name: str, evidence: Path) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Create through the public managed legacy route and retain its project binding."""
    project_id = _active_project_id()
    response = _dispatch(daemon, "model_create", {"name": name},
                         project_id=project_id, timeout_s=60.0)
    if response.get("success") is not True:
        raise RuntimeError(f"managed model_create failed: {json.dumps(response, ensure_ascii=False, default=str)[:8000]}")
    execution = response.get("execution")
    data = response.get("data")
    if not isinstance(execution, Mapping) or not isinstance(data, Mapping):
        raise RuntimeError("managed model_create response lacks data/execution records")
    ref = execution.get("model_ref")
    revision = execution.get("revision")
    if not isinstance(ref, Mapping) or type(revision) is not int or revision < 0:
        raise RuntimeError("managed model_create response lacks a valid ModelRef/revision")
    binding = daemon.backend.model_project_binding(ref)
    if (not isinstance(binding, Mapping) or binding.get("attribution") != "PROJECT_BOUND"
            or binding.get("project_id") != project_id):
        raise RuntimeError("managed model_create did not persist the authoritative project binding")
    inspect_response = _dispatch(daemon, "model.inspect", {"detail": "summary"},
                                 project_id=project_id, ref=ref, revision=revision,
                                 timeout_s=60.0)
    if inspect_response.get("success") is not True:
        raise RuntimeError(f"managed model.inspect after create failed: {json.dumps(inspect_response, ensure_ascii=False, default=str)[:8000]}")
    receipt = {
        "status": "PASS_PROJECT_BOUND_MODEL_CREATED_AND_INSPECTED",
        "project_id": project_id, "binding": dict(binding),
        "create_response": response, "inspect_response": inspect_response,
        "model_ref": dict(ref), "revision": revision,
        "model_tag": str(data.get("model_tag") or ref.get("model_tag") or ""),
    }
    if not receipt["model_tag"]:
        raise RuntimeError("managed model_create response lacks its exact model tag")
    write_json(evidence / "model_created.json", receipt)
    return dict(ref), revision, receipt


def _load_and_bind_saved_model(daemon: Any, path: Path, label: str,
                               evidence: Path) -> tuple[dict[str, Any], int, str]:
    """Reload via the managed path-scoped route and verify persistent attribution."""
    project_id = _active_project_id()
    response = _dispatch(daemon, "model_load", {"path": str(path)},
                         project_id=project_id, timeout_s=120.0)
    if response.get("success") is not True:
        raise RuntimeError(f"managed saved-model reload failed: {json.dumps(response, ensure_ascii=False, default=str)[:8000]}")
    execution = response.get("execution")
    data = response.get("data")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if (not isinstance(execution, Mapping)
            or not isinstance(ref, Mapping) or type(revision) is not int):
        raise RuntimeError("managed saved-model reload lacks its project-bound ModelRef/revision")
    binding = daemon.backend.model_project_binding(ref)
    if (not isinstance(binding, Mapping) or binding.get("attribution") != "PROJECT_BOUND"
            or binding.get("project_id") != project_id):
        raise RuntimeError("reopened model was not persisted under the authoritative project ID")
    model_tag = str((data.get("model_tag") if isinstance(data, Mapping) else None)
                    or ref.get("model_tag") or "")
    if not model_tag:
        raise RuntimeError("managed saved-model reload response lacks its exact model tag")
    inspect_response = _dispatch(daemon, "model.inspect", {"detail": "summary"},
                                 project_id=project_id, ref=ref, revision=revision,
                                 timeout_s=60.0)
    if inspect_response.get("success") is not True:
        raise RuntimeError(f"managed model.inspect after reload failed: {json.dumps(inspect_response, ensure_ascii=False, default=str)[:8000]}")
    result = {"status": "SAVED_MODEL_REOPENED_AND_PROJECT_BOUND_IN_SAME_WORKER",
              "path": str(path), "sha256": sha256_file(path),
              "project_id": project_id, "loaded_model_tag": model_tag,
              "model_ref": dict(ref), "revision": revision,
              "binding": dict(binding), "reload_response": response,
              "inspect_response": inspect_response}
    write_json(evidence / f"{label}_reopen_binding.json", result)
    return dict(ref), revision, model_tag


def _compare_reopen_integrals(before: Mapping[str, Any], after: Mapping[str, Any],
                              tolerance: float) -> dict[str, Any]:
    keys = ("signal_power", "reference_mode_power", "incident_reference_power",
            "reciprocal_overlap_numerator", "capture_aperture_signal_flux")
    rows: dict[str, Any] = {}
    for key in keys:
        left = before.get("integrals", {}).get(key)
        right = after.get("integrals", {}).get(key)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            raise ValueError(f"reopen result missing integral {key}")
        a = complex(float(left.get("real")), float(left.get("imag", 0)))
        b = complex(float(right.get("real")), float(right.get("imag", 0)))
        if left.get("unit") != right.get("unit"):
            raise ValueError(f"reopen integral {key} unit changed")
        error = abs(a - b) / max(abs(a), 1e-30)
        rows[key] = {"unit": left.get("unit"), "relative_error": error}
        if error > tolerance:
            raise ValueError(f"reopened model integral {key} changed by {error} > {tolerance}")
    return {"status": "PASS_SAVED_REOPENED_NATIVE_INTEGRALS",
            "tolerance_relative": tolerance, "integrals": rows}


def _junit_counts(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"JUnit receipt is absent or invalid XML: {path}") from exc
    suites = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testsuite"]
    if root.tag.rsplit("}", 1)[-1] == "testsuite" and root not in suites:
        suites.insert(0, root)
    if not suites:
        raise RuntimeError(f"JUnit receipt contains no testsuite: {path}")
    totals = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for name in totals:
            try:
                totals[name] += int(suite.attrib.get(name, "0"))
            except ValueError as exc:
                raise RuntimeError(f"JUnit suite has an invalid {name} count: {path}") from exc
    return totals


def _candidate_receipt_path(candidate_root: Path, relative: Any, label: str) -> Path:
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts):
        raise RuntimeError(f"focused offline {label} path is not a candidate-relative path")
    root = candidate_root.resolve()
    path = root / relative
    cursor = root
    for component in Path(relative).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise RuntimeError(f"focused offline {label} path crosses a symlink: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise RuntimeError(f"focused offline {label} is absent or escapes its candidate: {relative}")
    return path


def _candidate_receipt_directory(candidate_root: Path, relative: Any, label: str) -> Path:
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts):
        raise RuntimeError(f"focused offline {label} path is not a candidate-relative path")
    root = candidate_root.resolve()
    path = root / relative
    cursor = root
    for component in Path(relative).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise RuntimeError(f"focused offline {label} path crosses a symlink: {relative}")
    if not path.is_dir() or not path.resolve().is_relative_to(root):
        raise RuntimeError(f"focused offline {label} is absent or escapes its candidate: {relative}")
    return path


def _source_content_row(row: Any, *, label: str) -> tuple[int, str]:
    if not isinstance(row, Mapping):
        raise RuntimeError(f"{label} source manifest row is malformed")
    size, digest = row.get("bytes"), row.get("sha256")
    if (not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise RuntimeError(f"{label} source manifest row lacks exact byte/hash identity")
    return size, digest


def _validate_inherited_offline_delta(freeze_path: Path,
                                      freeze: Mapping[str, Any]) -> dict[str, Any]:
    """Validate explicit R13 inheritance plus current focused W23 gate evidence."""
    gates = freeze.get("offline_preflight")
    pointer = gates.get("focused_delta_receipt") if isinstance(gates, Mapping) else None
    if not isinstance(pointer, Mapping):
        raise RuntimeError("inherited offline gates lack the focused delta receipt pointer")
    receipt_path = _candidate_receipt_path(
        freeze_path.parent, pointer.get("path"), "delta receipt")
    if sha256_file(receipt_path) != pointer.get("sha256"):
        raise RuntimeError("focused delta receipt SHA-256 differs from the frozen pointer")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("focused delta receipt is not valid UTF-8 JSON") from exc
    if (not isinstance(receipt, Mapping)
            or receipt.get("schema_version") != 1
            or receipt.get("status") != "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA"
            or receipt.get("candidate_id") != freeze.get("candidate_id")
            or receipt.get("candidate_revision") != freeze.get("candidate_revision")
            or receipt.get("activity_id") != freeze.get("activity_id")):
        raise RuntimeError("focused delta receipt identity/status does not match the frozen candidate")

    base_root = W23_R13_BASE_ROOT
    base_manifest_path = base_root / "prepare/source_manifest.json"
    base_junit_path = base_root / "prepare/offline_gates/activity024_focused_junit.xml"
    if (base_manifest_path.is_symlink() or not base_manifest_path.is_file()
            or sha256_file(base_manifest_path) != W23_R13_BASE_SOURCE_MANIFEST_SHA256):
        raise RuntimeError("pinned R13 source manifest is missing or has been tampered with")
    if (base_junit_path.is_symlink() or not base_junit_path.is_file()
            or sha256_file(base_junit_path) != W23_R13_BASE_JUNIT_SHA256):
        raise RuntimeError("pinned R13 860-test JUnit is missing or has been tampered with")
    try:
        base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pinned R13 source manifest is invalid JSON") from exc
    base_files = base_manifest.get("source_files") if isinstance(base_manifest, Mapping) else None
    if (base_manifest.get("schema_version") != 1
            or base_manifest.get("source_count") != W23_R13_BASE_SOURCE_COUNT
            or base_manifest.get("source_manifest_sha256") != W23_R13_BASE_SOURCE_MAP_SHA256
            or not isinstance(base_files, Mapping)
            or len(base_files) != W23_R13_BASE_SOURCE_COUNT
            or _source_manifest_sha256(base_files) != W23_R13_BASE_SOURCE_MAP_SHA256):
        raise RuntimeError("pinned R13 source manifest does not match the approved 448-file map")
    base_junit_counts = _junit_counts(base_junit_path)
    if base_junit_counts != {"tests": W23_R13_BASE_TEST_COUNT,
                             "failures": 0, "errors": 0, "skipped": 0}:
        raise RuntimeError("pinned R13 JUnit is not the exact 860/0/0/0 base suite")
    expected_base = {
        "candidate_id": W23_R13_BASE_CANDIDATE_ID,
        "candidate_revision": W23_R13_BASE_CANDIDATE_REVISION,
        "activity_id": W23_R13_BASE_ACTIVITY_ID,
        "source_manifest_path": str(base_manifest_path),
        "source_manifest_sha256": W23_R13_BASE_SOURCE_MANIFEST_SHA256,
        "source_map_sha256": W23_R13_BASE_SOURCE_MAP_SHA256,
        "source_count": W23_R13_BASE_SOURCE_COUNT,
        "junit_path": str(base_junit_path),
        "junit_sha256": W23_R13_BASE_JUNIT_SHA256,
        "junit_counts": base_junit_counts,
        "compile_receipt_sha256": W23_R13_BASE_COMPILE_SHA256,
    }
    if receipt.get("inherited_base") != expected_base:
        raise RuntimeError("focused delta receipt is missing or changes the pinned R13 baseline identity")

    current_files = freeze.get("source_files")
    current_map_sha = freeze.get("source_manifest_sha256")
    if (not isinstance(current_files, Mapping)
            or not isinstance(current_map_sha, str)
            or _source_manifest_sha256(current_files) != current_map_sha
            or receipt.get("candidate_source_manifest_sha256") != current_map_sha):
        raise RuntimeError("focused delta receipt is not bound to the exact current frozen source map")
    base_names, current_names = set(base_files), set(current_files)
    common_changes = []
    for name in sorted(base_names & current_names):
        before = _source_content_row(base_files[name], label=f"R13 {name}")
        after = _source_content_row(current_files[name], label=f"candidate {name}")
        if before != after:
            common_changes.append({"path": name, "base_bytes": before[0],
                                  "base_sha256": before[1], "candidate_bytes": after[0],
                                  "candidate_sha256": after[1]})
    additions = []
    for name in sorted(current_names - base_names):
        size, digest = _source_content_row(current_files[name], label=f"candidate {name}")
        additions.append({"path": name, "candidate_bytes": size, "candidate_sha256": digest})
    removals = sorted(base_names - current_names)
    actual_delta = {"common_path_changes": common_changes,
                    "added_paths": additions, "removed_paths": removals}
    if actual_delta != receipt.get("source_delta"):
        raise RuntimeError("current source delta differs from the frozen allowlist or its byte/hash rows")
    if ({row["path"] for row in common_changes} != W23_R13_ALLOWED_COMMON_SOURCE_DELTA
            or {row["path"] for row in additions} != W23_R13_ALLOWED_ADDED_SOURCE_DELTA
            or removals):
        raise RuntimeError("W23 candidate contains a source change outside the exact pre-frozen allowlist")

    focus = receipt.get("focused_tests")
    if not isinstance(focus, Mapping):
        raise RuntimeError("focused delta receipt lacks current candidate test evidence")
    test_receipt_path = _candidate_receipt_path(
        freeze_path.parent, focus.get("receipt_path"), "JUnit invocation receipt")
    if sha256_file(test_receipt_path) != focus.get("receipt_sha256"):
        raise RuntimeError("focused JUnit invocation receipt was changed after freezing")
    try:
        test_receipt = json.loads(test_receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("focused JUnit invocation receipt is invalid JSON") from exc
    if (not isinstance(test_receipt, Mapping)
            or test_receipt.get("status") != "PASS_FOCUSED_STRICT_GUARDED_JUNIT"
            or test_receipt.get("returncode") != 0
            or test_receipt.get("candidate_source_manifest_sha256") != current_map_sha):
        raise RuntimeError("focused tests did not pass on the exact current candidate source map")
    junit_path = _candidate_receipt_path(
        freeze_path.parent, focus.get("junit_path"), "focused JUnit")
    if test_receipt.get("junit_path") != str(junit_path):
        raise RuntimeError("focused test receipt JUnit path differs from the frozen candidate-relative path")
    if (sha256_file(junit_path) != focus.get("junit_sha256")
            or test_receipt.get("junit_sha256") != focus.get("junit_sha256")):
        raise RuntimeError("focused JUnit file is absent or its SHA-256 differs from the receipt")
    focused_counts = _junit_counts(junit_path)
    if (focused_counts["tests"] < 25 or focused_counts["failures"]
            or focused_counts["errors"] or focused_counts["skipped"]):
        raise RuntimeError("current focused JUnit does not pass at least 25 affected controls")
    test_paths = test_receipt.get("test_paths")
    if not isinstance(test_paths, list):
        raise RuntimeError("focused test receipt lacks its executed test path list")
    executed_test_files = {
        str(item).split("::", 1)[0] for item in test_paths if isinstance(item, str)
    }
    for relative in (
            "tests/test_interp_native_default_units.py",
            "tests/test_native_w23_te_science_protocol.py",
            "tests/test_native_w23_saved_mph_diagnostic.py",
            "tests/test_native_w23_te_science_runner.py",
            "tests/test_g3_results.py"):
        source_row = current_files.get(relative)
        if not isinstance(source_row, Mapping) or source_row.get("path") not in executed_test_files:
            raise RuntimeError(f"focused suite does not execute the affected control {relative}")

    guard_path = _candidate_receipt_path(
        freeze_path.parent, focus.get("network_guard_path"), "strict network-guard log")
    if sha256_file(guard_path) != focus.get("network_guard_sha256"):
        raise RuntimeError("strict network-guard log was changed after freezing")
    guard_rows = []
    try:
        for line in guard_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                guard_rows.append(json.loads(line))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("strict network-guard log is invalid JSONL") from exc
    if (not guard_rows or guard_rows[0].get("event") != "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"
            or any(row.get("event") in {"DENIED_BEFORE_SOCKET_OPERATION", "GUARD_SETUP_ERROR"}
                   for row in guard_rows)):
        raise RuntimeError("focused candidate tests lack active, no-deny strict network-guard evidence")
    guard_receipt = test_receipt.get("network_guard")
    if (not isinstance(guard_receipt, Mapping)
            or guard_receipt.get("status") != "ACTIVE_NO_DENY_OR_SETUP_ERROR"
            or guard_receipt.get("path") != str(guard_path)
            or guard_receipt.get("sha256") != focus.get("network_guard_sha256")
            or guard_receipt.get("event_count") != len(guard_rows)):
        raise RuntimeError("focused test receipt does not match strict network-guard evidence")
    return {
        "status": "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA",
        "inherited_base": expected_base,
        "current_source_manifest_sha256": current_map_sha,
        "source_delta": actual_delta,
        "focused_test_counts": focused_counts,
        "strict_network_guard_event_count": len(guard_rows),
    }


def _validate_current_full_offline_receipts(
        freeze_path: Path, freeze: Mapping[str, Any],
        gates: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a fresh complete offline gate set against the exact current map."""
    source_files = freeze.get("source_files")
    source_map_sha = freeze.get("source_manifest_sha256")
    if (not isinstance(source_files, Mapping) or not source_files
            or not isinstance(source_map_sha, str)
            or _source_manifest_sha256(source_files) != source_map_sha
            or gates.get("source_manifest_sha256") != source_map_sha
            or gates.get("source_count") != len(source_files)):
        raise RuntimeError("current-full offline receipts do not bind the exact frozen source map")
    for relative, row in source_files.items():
        _source_content_row(row, label=f"current-full source {relative}")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise RuntimeError("current-full source map contains a noncanonical relative path")
        path_value = row.get("path") if isinstance(row, Mapping) else None
        if not isinstance(path_value, str) or not Path(path_value).is_absolute():
            raise RuntimeError(f"current-full source map lacks an absolute file path: {relative}")
        source_path = Path(path_value)
        try:
            resolved = source_path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"current-full source is unavailable: {relative}") from exc
        if (source_path.is_symlink() or not resolved.is_file()
                or resolved.stat().st_size != row.get("bytes")
                or sha256_file(resolved) != row.get("sha256")):
            raise RuntimeError(f"current-full source differs from its frozen map: {relative}")

    full_pointer = gates.get("current_full_receipt")
    if not isinstance(full_pointer, Mapping):
        raise RuntimeError("current-full offline gate receipt pointer is missing")
    progress_path = _candidate_receipt_path(
        freeze_path.parent, full_pointer.get("path"), "current-full gate progress")
    if sha256_file(progress_path) != full_pointer.get("sha256"):
        raise RuntimeError("current-full gate progress changed after freezing")
    try:
        full_receipt = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("current-full gate progress is invalid JSON") from exc
    if (not isinstance(full_receipt, Mapping)
            or full_receipt != gates.get("current_full_gate_receipt")
            or full_receipt.get("status") != "PASS_ALL_OFFLINE_GATES"
            or full_receipt.get("mode") != CURRENT_FULL_OFFLINE_MODE
            or full_receipt.get("source_manifest_sha256") != source_map_sha
            or full_receipt.get("source_count") != len(source_files)):
        raise RuntimeError("current-full gate progress does not match its frozen receipt/map")
    gate_rows = full_receipt.get("gates")
    bound_sources = full_receipt.get("gate_source_manifest_sha256")
    bound_receipts = full_receipt.get("gate_receipt_sha256")
    if (not isinstance(gate_rows, Mapping) or set(gate_rows) != set(CURRENT_FULL_OFFLINE_GATES)
            or not isinstance(bound_sources, Mapping)
            or not isinstance(bound_receipts, Mapping)
            or set(bound_sources) != set(CURRENT_FULL_OFFLINE_GATES)
            or set(bound_receipts) != set(CURRENT_FULL_OFFLINE_GATES)):
        raise RuntimeError("current-full offline preflight is missing a required gate receipt")
    for name in CURRENT_FULL_OFFLINE_GATES:
        if (bound_sources.get(name) != source_map_sha
                or bound_receipts.get(name) != sha256_json(gate_rows[name])):
            raise RuntimeError(f"current-full {name} receipt is not bound to the frozen source map")
    expected_statuses = {
        "runtime_imports": "PASS_EXECUTE_HELPER_IMPORTS",
        "software_negative_controls": "PASS_SOFTWARE_NEGATIVE_CONTROLS",
        "stdio_initialize_tools_list": "PASS_REAL_PRODUCTION_STDIO_INITIALIZE_TOOLS_LIST",
        "comsol64_jdk11_javac_javap": "PASS_COMSOL_64_JDK11_COMPILE_AND_JAVAP",
    }
    for name, expected_status in expected_statuses.items():
        if not isinstance(gate_rows.get(name), Mapping) or gate_rows[name].get("status") != expected_status:
            raise RuntimeError(f"current-full offline gate did not pass: {name}")
    pip_status = gate_rows.get("pip_check", {}).get("status") if isinstance(gate_rows.get("pip_check"), Mapping) else None
    if pip_status not in {"PASS_TASK_VENV_PIP_CHECK", "PASS_FRESH_RUNTIME_BOUND_PIP_CHECK"}:
        raise RuntimeError("current-full pip dependency receipt is not a successful reviewed runtime check")
    aliases = {
        "runtime_imports": "runtime_imports",
        "software_negative_controls": "software_negative_controls",
        "pip_check": "pip_check",
        "stdio": "stdio_initialize_tools_list",
        "compile": "comsol64_jdk11_javac_javap",
    }
    for alias, name in aliases.items():
        if full_receipt.get(alias) != gate_rows[name] or gates.get(alias) != gate_rows[name]:
            raise RuntimeError(f"current-full {alias} gate alias differs from its source-bound receipt")

    compile_receipt = gates.get("compile")
    compiled = compile_receipt.get("compiled_sources") if isinstance(compile_receipt, Mapping) else None
    if not isinstance(compiled, Mapping):
        raise RuntimeError("current-full compile receipt lacks compiled source identities")
    for label, relative in (
            ("persistent_worker", "comsol_mcp/worker_java/PersistentComsolWorker.java"),
            ("science_fixture", "tools/java/NativeW23TEScienceFixture.java")):
        source_row = source_files.get(relative)
        compiled_row = compiled.get(label)
        if (not isinstance(source_row, Mapping) or not isinstance(compiled_row, Mapping)
                or compiled_row.get("source_sha256") != source_row.get("sha256")
                or compiled_row.get("source") != source_row.get("path")):
            raise RuntimeError(f"current-full compile receipt differs from frozen Java source: {relative}")
    compile_pointer = gates.get("compile_receipt")
    if not isinstance(compile_pointer, Mapping):
        raise RuntimeError("current-full compile receipt file pointer is missing")
    compile_path = _candidate_receipt_path(
        freeze_path.parent, compile_pointer.get("path"), "current-full compile receipt")
    if (sha256_file(compile_path) != compile_pointer.get("sha256")
            or json.loads(compile_path.read_text(encoding="utf-8")) != compile_receipt):
        raise RuntimeError("current-full compile receipt file differs from the frozen source-bound result")

    focus_pointer = gates.get("focused_delta_receipt")
    if not isinstance(focus_pointer, Mapping):
        raise RuntimeError("current-full focused delta receipt pointer is missing")
    focus_path = _candidate_receipt_path(
        freeze_path.parent, focus_pointer.get("path"), "current-full focused delta receipt")
    if sha256_file(focus_path) != focus_pointer.get("sha256"):
        raise RuntimeError("current-full focused delta receipt changed after freezing")
    try:
        focused = json.loads(focus_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("current-full focused delta receipt is invalid JSON") from exc
    if (not isinstance(focused, Mapping)
            or focused.get("status") != "PASS_FOCUSED_DELTA_CURRENT_FULL_SOURCE_MAP"
            or focused.get("candidate_source_manifest_sha256") != source_map_sha
            or "inherited_base" in focused or "source_delta" in focused):
        raise RuntimeError("focused delta tests are not bound to the current full source map")
    test_pointer = focused.get("focused_tests")
    if not isinstance(test_pointer, Mapping):
        raise RuntimeError("current-full focused test receipt pointer is missing")
    test_path = _candidate_receipt_path(
        freeze_path.parent, test_pointer.get("receipt_path"), "current-full focused test receipt")
    if sha256_file(test_path) != test_pointer.get("receipt_sha256"):
        raise RuntimeError("current-full focused test receipt changed after freezing")
    try:
        test_receipt = json.loads(test_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("current-full focused test receipt is invalid JSON") from exc
    if (not isinstance(test_receipt, Mapping)
            or test_receipt.get("status") != "PASS_FOCUSED_STRICT_GUARDED_JUNIT"
            or test_receipt.get("returncode") != 0
            or test_receipt.get("candidate_source_manifest_sha256") != source_map_sha):
        raise RuntimeError("strict guarded focused suite did not pass on the current source map")
    test_paths = test_receipt.get("test_paths")
    if not isinstance(test_paths, list):
        raise RuntimeError("current-full focused test receipt lacks its executed source files")
    executed_test_files = {str(item).split("::", 1)[0] for item in test_paths
                           if isinstance(item, str)}
    required_test_sources = (
        "tests/test_w23_r21_direction_discovery.py",
        "tests/test_unit_a_node_public_api.py",
        "tests/test_native_w23_te_science_protocol.py",
        "tests/test_native_w23_saved_mph_diagnostic.py",
        "tests/test_native_w23_te_science_runner.py",
        "tests/test_g3_results.py",
        "tests/test_interp_native_default_units.py",
        "tests/test_g2_registry_packaging.py",
        "tests/test_g2_security_guards.py",
    )
    for relative in required_test_sources:
        source_row = source_files.get(relative)
        if (not isinstance(source_row, Mapping)
                or source_row.get("path") not in executed_test_files):
            raise RuntimeError(f"current-full focused suite omits the source-bound test file {relative}")
    loopback_path = ("tests/test_w23_r21_direction_discovery.py::"
                     "test_science_dispatch_uses_actual_registered_session_runtime_after_public_model_load")
    if loopback_path not in test_receipt.get("excluded_test_paths", []):
        raise RuntimeError("registered Core loopback control was not separated from strict guarded tests")
    junit_path = _candidate_receipt_path(
        freeze_path.parent, test_pointer.get("junit_path"), "current-full focused JUnit")
    if (test_receipt.get("junit_path") != str(junit_path)
            or sha256_file(junit_path) != test_pointer.get("junit_sha256")
            or test_receipt.get("junit_sha256") != test_pointer.get("junit_sha256")):
        raise RuntimeError("current-full focused JUnit path or hash differs from its receipt")
    counts = _junit_counts(junit_path)
    if counts["tests"] < 25 or counts["failures"] or counts["errors"] or counts["skipped"]:
        raise RuntimeError("current-full strict guarded JUnit did not pass 25+ focused tests")
    guard_path = _candidate_receipt_path(
        freeze_path.parent, test_pointer.get("network_guard_path"), "current-full strict network guard")
    if sha256_file(guard_path) != test_pointer.get("network_guard_sha256"):
        raise RuntimeError("current-full strict network guard log changed after freezing")
    try:
        guard_rows = [json.loads(line) for line in guard_path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("current-full strict network guard log is invalid JSONL") from exc
    if (not guard_rows or guard_rows[0].get("event") != "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"
            or any(row.get("event") in {"DENIED_BEFORE_SOCKET_OPERATION", "GUARD_SETUP_ERROR"}
                   for row in guard_rows)):
        raise RuntimeError("current-full strict guarded suite lacks active no-deny isolation evidence")
    guard_receipt = test_receipt.get("network_guard")
    if (not isinstance(guard_receipt, Mapping)
            or guard_receipt.get("status") != "ACTIVE_NO_DENY_OR_SETUP_ERROR"
            or guard_receipt.get("path") != str(guard_path)
            or guard_receipt.get("sha256") != test_pointer.get("network_guard_sha256")
            or guard_receipt.get("event_count") != len(guard_rows)):
        raise RuntimeError("current-full strict guard summary differs from its raw log")
    loopback = focused.get("registered_core_loopback")
    if (not isinstance(loopback, Mapping)
            or loopback.get("status") not in {
                "NOT_RUN_DEFERRED_POST_W24_TERMINAL",
                "PASS_SEPARATE_REGISTERED_CORE_LOOPBACK"}):
        raise RuntimeError("registered Core loopback result is not separately classified")
    if loopback.get("strict_network_guard_enabled") is not False or loopback.get("counted_in_guarded_junit") is not False:
        raise RuntimeError("registered Core loopback control was mixed into the strict guarded JUnit")
    return {
        "status": "PASS_CURRENT_FULL_SOURCE_BOUND_OFFLINE_GATES",
        "source_manifest_sha256": source_map_sha,
        "gate_count": len(CURRENT_FULL_OFFLINE_GATES),
        "focused_test_count": counts["tests"],
        "strict_network_guard_event_count": len(guard_rows),
        "registered_core_loopback": loopback.get("status"),
    }


def _check_offline_receipts(freeze_path: Path, freeze: Mapping[str, Any]) -> dict[str, Any]:
    gates = freeze.get("offline_preflight")
    if not isinstance(gates, Mapping):
        raise RuntimeError("frozen candidate lacks offline-gate receipts")
    gate_status = gates.get("status")
    if gate_status == "PASS_ALL_OFFLINE_GATES":
        inherited_delta = None
    elif gate_status == "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA":
        inherited_delta = _validate_inherited_offline_delta(freeze_path, freeze)
    else:
        raise RuntimeError("frozen candidate lacks successful complete or explicitly inherited offline gates")
    compile_receipt = gates.get("compile")
    if not isinstance(compile_receipt, Mapping):
        raise RuntimeError("frozen candidate has no COMSOL/JDK compile receipt")
    current_full = None
    if gates.get("mode") == CURRENT_FULL_OFFLINE_MODE:
        current_full = _validate_current_full_offline_receipts(freeze_path, freeze, gates)
    elif gates.get("mode") is not None:
        raise RuntimeError("frozen candidate uses an unknown offline-gate mode")
    if inherited_delta is not None:
        compile_path = freeze_path.parent / "offline_gates" / "compile.json"
        if (compile_path.is_symlink() or not compile_path.is_file()
                or sha256_file(compile_path) != W23_R13_BASE_COMPILE_SHA256
                or json.loads(compile_path.read_text(encoding="utf-8")) != compile_receipt):
            raise RuntimeError("inherited Java compile receipt differs from the pinned R13 compile evidence")
        for relative, expected_sha in W23_R13_JAVA_SOURCE_SHA256.items():
            current = freeze.get("source_files", {}).get(relative)
            if not isinstance(current, Mapping):
                raise RuntimeError(f"candidate source map omits inherited Java source {relative}")
            source_path = Path(str(current.get("path", "")))
            if (source_path.is_symlink() or not source_path.is_file()
                    or source_path.stat().st_size != current.get("bytes")
                    or sha256_file(source_path) != expected_sha
                    or current.get("sha256") != expected_sha):
                raise RuntimeError(f"inherited Java source SHA differs in the current snapshot: {relative}")
        compiled = compile_receipt.get("compiled_sources")
        source_expectations = {
            "persistent_worker": W23_R13_JAVA_SOURCE_SHA256[
                "comsol_mcp/worker_java/PersistentComsolWorker.java"],
            "science_fixture": W23_R13_JAVA_SOURCE_SHA256[
                "tools/java/NativeW23TEScienceFixture.java"],
        }
        if (not isinstance(compiled, Mapping)
                or any(not isinstance(compiled.get(label), Mapping)
                       or compiled[label].get("source_sha256") != expected
                       for label, expected in source_expectations.items())):
            raise RuntimeError("inherited Java source hashes differ from the exact R13 compile receipt")
    from comsol_mcp._java_worker import JavaWorkerPaths
    paths = JavaWorkerPaths(INSTALL_ROOT, JAVA11, project_root=REPO)
    _classpath, manifest_sha, jar_count, jar_fingerprint = paths.classpath()
    expected = (compile_receipt.get("classpath_manifest_sha256"),
                compile_receipt.get("classpath_jar_count"),
                compile_receipt.get("classpath_jar_content_fingerprint_sha256"))
    observed = (manifest_sha, jar_count, jar_fingerprint)
    if observed != expected:
        raise RuntimeError("installed COMSOL client classpath changed after frozen javac/javap validation")
    if current_full is not None:
        compiled_root = _candidate_receipt_directory(
            freeze_path.parent, gates.get("compile_artifact_root"), "current-full compile output root")
    else:
        compiled_root = freeze_path.parent / "offline_gates" / "compile"
    classes: dict[str, Any] = {}
    for label, receipt in compile_receipt.get("compiled_sources", {}).items():
        if not isinstance(receipt, Mapping):
            raise RuntimeError("frozen Java compile class receipt is malformed")
        class_dir = compiled_root / label / "classes"
        seen = {}
        for relative, row in receipt.get("classes", {}).items():
            target = class_dir / relative
            if not target.is_file() or target.stat().st_size != row.get("bytes") \
                    or sha256_file(target) != row.get("sha256"):
                raise RuntimeError(f"frozen compiler output is missing or changed: {target}")
            seen[relative] = row
        if not seen:
            raise RuntimeError(f"frozen Java compile output is empty for {label}")
        classes[label] = {"class_count": len(seen), "class_dir": str(class_dir)}
    material = {"status": "PASS_FROZEN_CLASSPATH_AND_CLASS_OUTPUT_REVALIDATED",
                "classpath_manifest_sha256": manifest_sha, "classpath_jar_count": jar_count,
                "classpath_jar_content_fingerprint_sha256": jar_fingerprint,
                "compiled_class_outputs": classes}
    if inherited_delta is not None:
        material["inherited_offline_delta"] = inherited_delta
    if current_full is not None:
        material["current_full_offline_gates"] = current_full
    return material


def execute(freeze_path: Path, freeze_sha256: str, approval_path: Path,
            work: Path, evidence: Path) -> dict[str, Any]:
    """Execute only the exact root-approved frozen candidate."""
    # Resolve all application imports before approvals and before any server birth.
    helpers = _load_helper_refs()
    freeze = _validate_frozen_candidate(freeze_path, freeze_sha256)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if not isinstance(approval, Mapping):
        raise RuntimeError("root approval receipt must be a JSON object")
    validate_approval_receipt(approval, freeze, freeze_sha256)
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise RuntimeError("native runner Python differs from the frozen task-local production/test environment")
    if sys.platform != "darwin":
        raise RuntimeError(f"frozen native science candidate requires macOS, observed {sys.platform}")
    if not work.as_posix().startswith("/private/tmp/comsol-mcp-w23-te-science-"):
        raise ValueError("--work must be a unique task-owned /private/tmp/comsol-mcp-w23-te-science-* path")
    if not evidence.as_posix().startswith(str(EVIDENCE_ROOT.resolve()) + "/"):
        raise ValueError("--evidence must be a new folder below the W23 evidence root")
    if work.exists() or evidence.exists():
        raise FileExistsError("science work/evidence paths must be new; existing files will not be overwritten")
    evidence.mkdir(parents=True, exist_ok=False)
    write_json(evidence / "root_approval.json", dict(approval))
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "candidate_id": CANDIDATE_ID,
        "scope": freeze["scope"],
        "freeze_path": str(freeze_path), "freeze_sha256": freeze_sha256,
        "approval_path": str(approval_path),
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
        "study_run_submission_count": 0,
        "actual_solver_execution_count": "UNKNOWN_UNLESS_DIRECT_RUNTIME_EVIDENCE_EXISTS",
        "worker_state": "NOT_STARTED", "server_birth": None,
        "cleanup": "NOT_STARTED",
    }
    write_json(evidence / "summary.json", summary)
    server = None
    daemon = None
    worker_identity: dict[str, Any] | None = None
    worker_port: int | None = None
    worker_identity_capture: dict[str, Any] = {}
    server_identity: dict[str, Any] | None = None
    deadline_epoch: float | None = None
    birth_epoch: float | None = None
    fixture_dispatched = False
    study_dispatch_started = False
    study_dispatch_attempt_count = 0
    original_add_event: Callable[..., Any] | None = None
    study_submissions: list[dict[str, Any]] = []
    direct_journal = DirectRpcJournal(evidence / "direct_worker_rpcs.jsonl")
    successful_cases: dict[str, dict[str, Any]] = {}
    saved_models: dict[str, dict[str, Any]] = {}
    provisional_saved_models: dict[str, dict[str, Any]] = {}
    opened_started = False
    project_create_receipt: dict[str, Any] | None = None
    immutable_run_project_id: str | None = None
    try:
        # Re-check exact production environment and stdio gates immediately before birth.
        current_sources = _source_files()
        if current_sources != freeze.get("source_files"):
            raise RuntimeError("frozen runtime source closure changed before native launch")
        pip = _pip_dependency_check()
        stdio = _actual_stdio_preflight(evidence / "prelaunch_stdio")
        compile_identity = _check_offline_receipts(freeze_path, freeze)
        write_json(evidence / "fresh_prelaunch_gates.json", {
            "status": "PASS_FRESH_PREBIRTH_RUNTIME_GATES",
            "pip_check": pip, "stdio": stdio,
            "frozen_compile_identity": compile_identity,
            "source_manifest_sha256": _source_manifest_sha256(current_sources),
            "native_process_started": False,
        })
        from tools.w23_te_science_protocol import run_negative_controls
        software_negative = run_negative_controls()
        write_json(evidence / "software_negative_controls.json", software_negative)

        preflight = helpers["preflight_module"]
        prelaunch = preflight._process_inventory()
        write_json(evidence / "prelaunch_inventory.json", prelaunch)
        if not prelaunch.get("quiescent"):
            raise RuntimeError("fresh COMSOL/Worker/listener inventory is not quiescent; no server started")
        server = helpers["NativeLoopbackServer"](work, evidence,
                                                 event_log=evidence / "events.jsonl")
        shadow = server.prepare_shadow()
        write_json(evidence / "private_shadow_receipt.json", shadow)
        if _source_files() != freeze["source_files"]:
            raise RuntimeError("source closure drifted while staging the private COMSOL tree")
        prebirth = preflight._process_inventory()
        write_json(evidence / "prebirth_inventory.json", prebirth)
        if not prebirth.get("quiescent"):
            raise RuntimeError("fresh pre-birth process/listener inventory is not quiescent")
        # Register the actual science workspace before any COMSOL server birth.
        # This is a durable control-plane action and requires no Worker.
        authority_opt_in = _set_prebirth_project_authority_opt_in()
        write_json(evidence / "project_authority_host_opt_in.json", authority_opt_in)
        authority_daemon = helpers["ControlDaemon"](
            work / "control", worker=None, project_root=server.project)
        try:
            project_create_receipt = _create_science_project_prebirth(
                authority_daemon, helpers, server.project, evidence)
            immutable_run_project_id = str(project_create_receipt["project_id"])
            summary["project_create_receipt"] = project_create_receipt
            summary["immutable_run_project_id"] = immutable_run_project_id
            summary["registered_science_workspace"] = project_create_receipt["workspace"]
            write_json(evidence / "summary.json", summary)
        finally:
            authority_daemon.close()
        if _source_files() != freeze["source_files"]:
            raise RuntimeError("runtime source closure changed during prebirth project registration")
        prebirth = preflight._process_inventory()
        write_json(evidence / "prebirth_after_project_create_inventory.json", prebirth)
        if not prebirth.get("quiescent"):
            raise RuntimeError("fresh pre-birth process/listener inventory is not quiescent after project registration")
        listener = server.start_and_verify_listener()
        server_identity = dict(server.process_identity or {})
        birth_text = server_identity.get("birth")
        if not isinstance(birth_text, str):
            raise RuntimeError("owned COMSOL process birth identity is missing")
        birth_epoch = preflight._mac_birth_epoch(birth_text)
        deadline_epoch = birth_epoch + MAX_BIRTH_BUDGET_S
        summary.update({"status": "NATIVE_SERVER_BORN",
                        "server_birth": birth_text,
                        "server_pid": server.proc.pid if server.proc else None,
                        "port": server.port, "deadline_epoch": deadline_epoch,
                        "birth_budget_seconds": MAX_BIRTH_BUDGET_S})
        write_json(evidence / "server_birth.json", {
            "status": "OWNED_SERVER_BIRTH_VERIFIED",
            "process_identity": server_identity, "listener": listener,
            "birth_epoch": birth_epoch, "deadline_epoch": deadline_epoch,
            "budget_seconds": MAX_BIRTH_BUDGET_S,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
            "freeze_sha256": freeze_sha256,
        })
        write_json(evidence / "summary.json", summary)
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("birth budget reached cleanup reserve before Worker startup")

        worker_info = _start_worker_with_journal(
            server, direct_journal, helpers, identity_sink=worker_identity_capture)
        worker_runtime = worker_info.get("worker_runtime", {})
        worker_identity = dict(worker_info["worker_process_identity"])
        worker_port = worker_runtime.get("port")
        if type(worker_port) is not int:
            raise RuntimeError("started Worker has no exact private listener port")
        summary["worker_state"] = "CONNECTED_TO_EXACT_LOOPBACK_SERVER"
        write_json(evidence / "worker_connection.json", worker_info)
        _set_runtime_environment(server)
        daemon = helpers["ControlDaemon"](work / "control", worker=server.worker,
                                          project_root=server.project)
        connection = _dispatch(daemon, "server_connect",
                               {"host": "127.0.0.1", "port": server.port},
                               project_id=_active_project_id(), timeout_s=30.0)
        if connection.get("success") is not True or connection.get("data", {}).get("endpoint") != f"127.0.0.1:{server.port}":
            raise RuntimeError("managed control daemon did not bind the exact owned loopback server")
        write_json(evidence / "managed_connection.json", connection)

        if _source_files() != freeze["source_files"]:
            raise RuntimeError("runtime source closure changed before model fixture staging")
        project_workspace = _project_science_workspace(project_create_receipt)
        fixture_copy = project_workspace / FIXTURE.name
        if fixture_copy.exists():
            raise FileExistsError(f"fixture staging path already exists: {fixture_copy}")
        fixture_copy.write_bytes(FIXTURE.read_bytes())
        if sha256_file(fixture_copy) != freeze["fixture"]["sha256"]:
            raise RuntimeError("staged fixture SHA does not match the frozen Java source")
        ref, revision, model_create_receipt = _create_project_bound_model(
            daemon, "W23 managed planar TE science", evidence)
        write_json(evidence / "model_project_binding.json", {
            "project_create_receipt_id": immutable_run_project_id,
            "model_create_project_id": model_create_receipt["project_id"],
            "project_workspace": str(project_workspace),
            "model_ref": ref, "revision": revision,
            "status": "PASS" if model_create_receipt["project_id"] == immutable_run_project_id else "FAIL",
        })
        if model_create_receipt["project_id"] != immutable_run_project_id:
            raise RuntimeError("created model attribution differs from the immutable project.create receipt")
        build, ref, revision = _fixture_call(
            daemon, ref, revision, FIXTURE.name, {"phase": "build"}, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence, label="fixture_build")
        write_json(evidence / "material_tensor_raw_readback.json", {
            "status": "CAPTURED_BEFORE_VALIDATION",
            "value_types_and_getters_captured": True,
            "materials": build.get("materials") if isinstance(build, Mapping) else None,
            "raw_values_and_getter_shapes_preserved": True,
        })
        if (build.get("fixture_id") != "w23_planar_te_numeric_port_science_v1"
                or build.get("status") != "BUILT_CONFIGURED_NOT_SOLVED"
                or build.get("science_plane", {}).get("x_um") != 8.0
                or build.get("science_plane", {}).get("api_only_x10_boundary_reused") is not False):
            raise RuntimeError("native fixture readback does not prove the frozen x=8 science plane and no x=10 reuse")
        if build.get("study_or_solver_invoked") is not False:
            raise RuntimeError("fixture build unexpectedly claims or performs a study/solver invocation")
        _require_and_record_mesh_readback(build.get("mesh"), "coarse", evidence=evidence,
                                          label="fixture_build")
        material_gate = validate_material_tensor_readback(build)
        write_json(evidence / "material_tensor_readback_gate.json", material_gate)
        capture_selection = validate_capture_aperture_readback(build)
        write_json(evidence / "capture_aperture_native_selection.json", capture_selection)
        pre_solve_inventory, ref, revision = _fixture_call(
            daemon, ref, revision, FIXTURE.name,
            {"phase": "solution_inventory"}, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label="pre_solve_study_configuration")
        configured_study = validate_configured_study_features(pre_solve_inventory)
        if pre_solve_inventory.get("solver_execution_count") != "UNKNOWN_WITHOUT_RUNTIME_SOLVER_EVIDENCE":
            raise RuntimeError("pre-solve inventory made an unsupported solver execution claim")
        write_json(evidence / "pre_solve_study_configuration.json", {
            "status": configured_study["status"],
            "configured_study": configured_study,
            "solver_sequences_present_before_solve": pre_solve_inventory.get("solver_sequences_for_study"),
            "empty_pre_solve_solver_sequence_inventory_is_permitted": True,
            "native_study_run_calls_so_far": 0,
            "solver_execution_count": "UNKNOWN_WITHOUT_RUNTIME_SOLVER_EVIDENCE",
        })
        route_negative = _route_negative_control(daemon, ref, revision,
                                                 evidence=evidence,
                                                 deadline_epoch=deadline_epoch)
        write_json(evidence / "fixture_configuration.json", build)

        # Count exact persisted Worker method=run submissions before send.
        original_add_event = daemon.store.add_event
        native_run_predicate = preflight._is_native_study_run_submission

        def count_study_run_before_send(job_id: str, event: str,
                                        metadata: dict[str, Any]) -> Any:
            job = daemon.store.job(job_id) if event == "worker_request" and native_run_predicate(metadata) else None
            if event == "worker_request" and native_run_predicate(metadata) and _is_study_run_job(job):
                if len(study_submissions) >= MAX_STUDY_RUN_CALLS:
                    append_jsonl(evidence / "events.jsonl", {
                        "at_utc": utc_now(), "event": "study_run_call_blocked_at_frozen_ceiling",
                        "job_id": job_id, "submission_count": len(study_submissions),
                        "max_study_run_calls": MAX_STUDY_RUN_CALLS})
                    raise RuntimeError("exact Study.run Worker submission ceiling reached before send")
                row = {"ordinal": len(study_submissions) + 1, "at_utc": utc_now(),
                       "job_id": job_id,
                       "operation": "study.run",
                       "request_id": metadata.get("request_id"),
                       "worker_event": metadata,
                       "method": "model.study('std1').run()"}
                persisted = original_add_event(job_id, event, metadata)
                study_submissions.append(row)
                append_jsonl(evidence / "events.jsonl", {
                    "at_utc": utc_now(), "event": "study_run_worker_submission_persisted",
                    "submission": row})
                write_json(evidence / "study_run_submission_count.json", {
                    "count": len(study_submissions),
                    "maximum": MAX_STUDY_RUN_CALLS,
                    "submissions": study_submissions})
                return persisted
            return original_add_event(job_id, event, metadata)

        daemon.store.add_event = count_study_run_before_send
        run_rows_by_case: dict[str, dict[str, Any]] = {}
        last_mesh = None
        for case_index, case in enumerate(CASE_PLAN):
            if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
                raise TimeoutError("native solve plan reached cleanup reserve; remaining Study.run calls stopped")
            case_id = str(case["case_id"])
            phase, mesh = str(case["phase_value"]), str(case["mesh_level"])
            phase_readback, ref, revision = _fixture_call(
                daemon, ref, revision, FIXTURE.name,
                {"phase": "set_phase", "phase_value": phase}, helpers,
                deadline_epoch=deadline_epoch, evidence=evidence,
                label=f"set_phase_{case_id}")
            if (phase_readback.get("port_thetap_readback") != phase
                    or phase_readback.get("output_reference_phase_readback") != "0[deg]"
                    or phase_readback.get("output_reference_phase_unchanged") is not True):
                raise RuntimeError(f"native excitation/output reference phase properties did not read back for {case_id}")
            if mesh != last_mesh:
                mesh_readback, ref, revision = _fixture_call(
                    daemon, ref, revision, FIXTURE.name,
                    {"phase": "set_mesh", "mesh_level": mesh}, helpers,
                    deadline_epoch=deadline_epoch, evidence=evidence,
                    label=f"set_mesh_{case_id}")
                _require_and_record_mesh_readback(
                    mesh_readback, mesh, evidence=evidence, label=f"set_mesh_{case_id}")
                last_mesh = mesh
            if _source_files() != freeze["source_files"]:
                raise RuntimeError("frozen runtime source changed during native run; stopping before Study.run")
            remaining = deadline_epoch - time.time() - CLEANUP_RESERVE_S
            if remaining <= 0:
                raise TimeoutError("birth deadline entered cleanup reserve before Study.run")
            pre_dispatch_rows = _actual_science_study_run_submissions(daemon, preflight)
            study_submissions[:] = _runner_study_submission_records(pre_dispatch_rows)
            local_counter_before_dispatch = len(study_submissions)
            before_identities = {(row["job_id"], row["request_id"])
                                 for row in study_submissions}
            if len(study_submissions) >= MAX_STUDY_RUN_CALLS:
                raise RuntimeError("durable Study.run submission ceiling reached before dispatch")
            study_dispatch_started = True
            study_dispatch_attempt_count += 1
            dispatch_exception: BaseException | None = None
            run_response = None
            try:
                run_response = _dispatch(
                    daemon, "operation_call",
                    {"operation_id": "study.run",
                     "arguments": {"study": helpers["_study_node_path"](STUDY_TAG),
                                   "timeout_s": max(1.0, remaining)}},
                    project_id=_active_project_id(), ref=ref, revision=revision,
                    timeout_s=min(1500.0, remaining))
            except BaseException as exc:
                dispatch_exception = exc
            local_counter_after_dispatch = len(study_submissions)
            response_job_id = _job_id(run_response) if isinstance(run_response, Mapping) else None
            pre_wait_reconciliation = _reconcile_study_run_dispatch(
                daemon, preflight, response_job_id=response_job_id,
                before_identities=before_identities)
            pre_wait_rows = pre_wait_reconciliation["all_durable_submissions"]
            try:
                study_submissions[:] = _runner_study_submission_records(pre_wait_rows)
            except BaseException:
                # Keep the last known counter only as a provisional observation;
                # the final durable read after the one wait is authoritative.
                pass
            accounting_status = (
                "DISPATCH_EXCEPTION_NO_FINAL_WAIT_RECONCILIATION"
                if dispatch_exception is not None else
                "WAITING_FOR_TERMINAL_DURABLE_RECONCILIATION")
            accounting = {
                "status": accounting_status,
                "count_basis": "durable study.run operation job plus exact Worker phase=submitted, kind=call, metadata.type=call, metadata.method=run event",
                "case_id": case_id,
                "dispatch_started": True,
                "dispatch_exception": (f"{type(dispatch_exception).__name__}: {dispatch_exception}"
                                       if dispatch_exception is not None else None),
                "response_job_id": response_job_id,
                "pre_dispatch_durable_submission_count": len(pre_dispatch_rows),
                "pre_wait_observed_durable_submission_count": len(pre_wait_rows),
                "pre_wait_reconciliation": pre_wait_reconciliation,
                "new_durable_submissions_for_this_dispatch": pre_wait_reconciliation[
                    "new_durable_submissions_for_this_dispatch"],
                "runner_pre_send_counter_before_dispatch": local_counter_before_dispatch,
                "runner_pre_send_counter_after_dispatch_before_wait": local_counter_after_dispatch,
                "runner_counter_matched_pre_wait_observation": (
                    local_counter_after_dispatch == len(pre_wait_rows)),
                "durable_count_controls_budget_and_retry_decision": True,
                "no_retry_or_second_dispatch_after_this_attempt": True,
                "wait_job_call_count_maximum": 1,
            }
            write_json(evidence / f"study_run_dispatch_accounting_{case_id}.json", accounting)
            write_json(evidence / "study_run_submission_count.json", {
                "count": (len(pre_wait_rows)
                          if len(pre_wait_rows) >= study_dispatch_attempt_count else "UNKNOWN"),
                "observed_durable_worker_event_count": len(pre_wait_rows),
                "maximum": MAX_STUDY_RUN_CALLS,
                "count_basis": accounting["count_basis"],
                "submissions": pre_wait_rows,
                "latest_dispatch_status": accounting_status,
                "unknown_after_dispatch_is_not_reported_as_zero": True,
                "final_reconciliation_pending_until_same_job_wait_terminal": True,
            })
            if dispatch_exception is not None:
                # Durable evidence is already captured; keep the original dispatch
                # exception and never retry this idempotency identity.
                raise dispatch_exception
            if not isinstance(run_response, Mapping):
                raise RuntimeError("Study.run dispatch returned no object response; no retry is permitted")
            if response_job_id is None:
                raise RuntimeError("Study.run dispatch response lacks a durable job identity; no retry is permitted")
            terminal_response: Mapping[str, Any] | None = None
            wait_exception: BaseException | None = None
            try:
                # Exactly one wait is made for the dispatch response's job ID.
                terminal_response = _wait_job(
                    daemon, run_response,
                    deadline_epoch=deadline_epoch, cleanup_reserve_s=CLEANUP_RESERVE_S,
                    label=f"study_run_{case_id}", evidence=evidence)
            except BaseException as exc:
                wait_exception = exc
            # The Worker event can be persisted after the dispatch returned a
            # pending response. Re-read this same job and its events only after
            # the one wait has terminated or raised; never issue another call.
            post_wait_reconciliation = _reconcile_study_run_dispatch(
                daemon, preflight, response_job_id=response_job_id,
                before_identities=before_identities)
            durable_after_rows = post_wait_reconciliation["all_durable_submissions"]
            cumulative_count_matches_attempts = (
                len(durable_after_rows) == study_dispatch_attempt_count)
            if not cumulative_count_matches_attempts:
                post_wait_reconciliation["status"] = (
                    "UNVERIFIED_CUMULATIVE_STUDY_RUN_SUBMISSION_COUNT_STOP")
                post_wait_reconciliation["reconciliation_error"] = (
                    "cumulative durable Worker submission rows do not equal dispatch attempts")
            try:
                study_submissions[:] = _runner_study_submission_records(durable_after_rows)
            except BaseException as exc:
                post_wait_reconciliation["status"] = "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP"
                post_wait_reconciliation["reconciliation_error"] = (
                    f"{type(exc).__name__}: {exc}")
            accounting.update({
                "status": post_wait_reconciliation["status"],
                "final_reconciliation_stage": "AFTER_SAME_RESPONSE_JOB_WAIT_TERMINAL_OR_EXCEPTION",
                "wait_job_call_count": 1,
                "wait_job_exception": (f"{type(wait_exception).__name__}: {wait_exception}"
                                       if wait_exception is not None else None),
                "wait_job_returned_response": isinstance(terminal_response, Mapping),
                "post_wait_observed_durable_submission_count": len(durable_after_rows),
                "cumulative_dispatch_attempt_count": study_dispatch_attempt_count,
                "cumulative_durable_count_matches_dispatch_attempts": cumulative_count_matches_attempts,
                "post_wait_reconciliation": post_wait_reconciliation,
                "new_durable_submissions_for_this_dispatch": post_wait_reconciliation[
                    "new_durable_submissions_for_this_dispatch"],
                "submission_count_for_dispatch": post_wait_reconciliation[
                    "submission_count_for_dispatch"],
                "runner_counter_matched_post_wait_durable_count": (
                    len(study_submissions) == len(durable_after_rows)),
                "durable_count_controls_budget_and_retry_decision": True,
                "no_retry_or_second_dispatch_after_this_attempt": True,
            })
            if wait_exception is not None:
                # Preserve the original wait failure (including timeout/UNKNOWN)
                # after recording the best available same-job evidence.
                write_json(evidence / f"study_run_dispatch_accounting_{case_id}.json", accounting)
                count = (len(durable_after_rows)
                         if len(durable_after_rows) >= study_dispatch_attempt_count else "UNKNOWN")
                write_json(evidence / "study_run_submission_count.json", {
                    "count": count,
                    "observed_durable_worker_event_count": len(durable_after_rows),
                    "maximum": MAX_STUDY_RUN_CALLS,
                    "count_basis": accounting["count_basis"],
                    "submissions": durable_after_rows,
                    "latest_dispatch_status": accounting["status"],
                    "unknown_after_dispatch_is_not_reported_as_zero": True,
                    "final_reconciliation_pending_until_same_job_wait_terminal": False,
                })
                raise wait_exception
            if not isinstance(terminal_response, Mapping):
                raise RuntimeError("Study.run same-job wait returned no terminal response; no retry is permitted")
            write_json(evidence / f"study_run_dispatch_accounting_{case_id}.json", accounting)
            count = (len(durable_after_rows)
                     if len(durable_after_rows) == study_dispatch_attempt_count else "UNKNOWN")
            write_json(evidence / "study_run_submission_count.json", {
                "count": count,
                "observed_durable_worker_event_count": len(durable_after_rows),
                "maximum": MAX_STUDY_RUN_CALLS,
                "count_basis": accounting["count_basis"],
                "submissions": durable_after_rows,
                "latest_dispatch_status": accounting["status"],
                "unknown_after_dispatch_is_not_reported_as_zero": True,
                "final_reconciliation_pending_until_same_job_wait_terminal": False,
            })
            completed_run = _complete_terminal_study_run_case(
                daemon, helpers,
                terminal_response=terminal_response, accounting=accounting,
                expected_total_dispatch_count=study_dispatch_attempt_count,
                post_wait_reconciliation=post_wait_reconciliation,
                study_submissions=study_submissions,
                response_job_id=response_job_id, case_id=case_id,
                completed_case_count=case_index + 1, ref=ref, revision=revision,
                fixture_name=FIXTURE.name, project_workspace=project_workspace,
                deadline_epoch=deadline_epoch, evidence=evidence, summary=summary,
                provisional_saved_models=provisional_saved_models)
            raw_submission = completed_run["response_job_worker_submission"]
            submission = completed_run["submission"]
            observed_status = completed_run["observed_status"]
            provisional_model = completed_run["provisional_saved_model"]
            inventory = completed_run["inventory"]
            ref, revision = completed_run["ref"], completed_run["revision"]
            binding_receipt = validate_study_inventory(inventory)
            solver_step_map = _solver_step_map(binding_receipt)
            dataset_receipt, ref, revision = _dataset_inventory(
                daemon, ref, revision, deadline_epoch=deadline_epoch,
                evidence=evidence / case_id, helpers=helpers)
            source_receipt = resolve_mode_overlap_sources(
                dataset_receipt["datasets"], dataset_receipt["index_by_tag"],
                solver_step_map)
            write_json(evidence / f"source_binding_{case_id}.json", {
                "study_and_solver_inventory": inventory,
                "solver_binding_validation": binding_receipt,
                "dataset_source_inventory": dataset_receipt,
                "resolved_sources": source_receipt,
            })
            signal_source = source_receipt["sources"]["signal"]
            incident_source = source_receipt["sources"]["incident_reference"]
            raw_receipts: dict[str, Any] = {}
            independent_by_count: dict[int, dict[str, Any]] = {}
            for count in (321, 641):
                output_raw, ref, revision = _raw_fields_call(
                    daemon, ref, revision, FIXTURE.name,
                    signal_source["dataset_id"], "output_x8", count,
                    helpers, deadline_epoch=deadline_epoch, evidence=evidence,
                    label=f"raw_{case_id}_output_{count}")
                input_raw, ref, revision = _raw_fields_call(
                    daemon, ref, revision, FIXTURE.name,
                    incident_source["dataset_id"], "input_xminus10", count,
                    helpers, deadline_epoch=deadline_epoch, evidence=evidence,
                    label=f"raw_{case_id}_input_{count}")
                if (output_raw.get("dataset") != signal_source["dataset_id"]
                        or input_raw.get("dataset") != incident_source["dataset_id"]
                        or output_raw.get("sample_count") != count
                        or input_raw.get("sample_count") != count):
                    raise RuntimeError(f"raw native E/H dataset/grid identity mismatch for {case_id}/{count}")
                raw_receipts[str(count)] = {"output": output_raw, "input": input_raw}
                independent_by_count[count] = helpers["independent_integrals"](output_raw, input_raw)
            output641 = raw_receipts["641"]["output"]
            input641 = raw_receipts["641"]["input"]
            software_phase_control = None
            if case_id == "fine-phase0":
                software_phase_control = helpers["software_global_phase_control"](
                    output641, input641)
                write_json(evidence / "software_global_phase_control.json", software_phase_control)
            output_neff = _validate_mode_index(
                output641, "ewfd.neff_2", 2e-3 if mesh == "coarse" else 5e-4)
            input_neff = _validate_mode_index(
                input641, "ewfd.neff_1", 2e-3 if mesh == "coarse" else 5e-4)
            definition = build_mode_overlap_definition(
                source_receipt, int(output641["normal_sign"]), int(input641["normal_sign"]))
            native_overlap, ref, revision = _native_overlap_call(
                daemon, ref, revision, definition, helpers,
                deadline_epoch=deadline_epoch, evidence=evidence,
                label=f"native_mode_overlap_{case_id}")
            comparison = helpers["compare_native_integrals"](
                native_overlap, independent_by_count[641], tolerance=1e-3)
            independent_capture = helpers["independent_capture_aperture_flux"](output641)
            capture_comparison = helpers["compare_native_capture_flux"](
                native_overlap, independent_capture, independent_by_count[641], tolerance=1e-3)
            quadrature = helpers["compare_quadrature_refinement"](
                independent_by_count[321], independent_by_count[641], tolerance=1e-3)
            overlap_min = 0.995 if mesh == "coarse" else 0.999
            observed_overlap = float(native_overlap.get("normalized_overlap"))
            if not math.isfinite(observed_overlap) or observed_overlap < overlap_min:
                raise RuntimeError(f"{case_id} normalized self-overlap {observed_overlap} is below frozen {overlap_min}")
            case_result = {
                "case_id": case_id, "phase_value": phase,
                "output_port_phase": phase_readback["output_reference_phase_readback"],
                "mesh_level": mesh, "mesh_readback": mesh_readback,
                "study_run_submission": submission,
                "response_job_raw_worker_submission": raw_submission,
                "provisional_saved_model": provisional_model,
                "terminal_worker_status": observed_status,
                "study_step_solver_bindings": binding_receipt,
                "native_sources": source_receipt,
                "native_overlap": native_overlap,
                "native_vs_independent_quadrature": comparison,
                "independent_capture_aperture_flux": independent_capture,
                "native_vs_independent_capture_flux": capture_comparison,
                "native_eta_capture": native_overlap["eta_capture"]["value"],
                "independently_recomputed_eta_capture": capture_comparison["independent_eta_capture"],
                "quadrature_refinement_321_to_641": quadrature,
                "output_mode_neff": output_neff,
                "input_mode_neff": input_neff,
                "raw_fields": raw_receipts,
                "independent_integrals": independent_by_count,
                "normalized_overlap_minimum": overlap_min,
                "software_global_phase_control": software_phase_control,
                "status": "PASS_CASE_LOCAL_OVERLAP_CAPTURE_AND_INTEGRAL_CHECKS",
            }
            successful_cases[case_id] = case_result
            write_json(evidence / f"science_case_{case_id}.json", case_result)
            saved_path, saved_receipt, ref, revision = _save_case_model(
                daemon, ref, revision, FIXTURE.name, case_id, helpers,
                project_workspace=project_workspace,
                deadline_epoch=deadline_epoch, evidence=evidence)
            saved_models[case_id] = {"path": str(saved_path), **saved_receipt}
            run_rows_by_case[case_id] = case_result
            summary.update({"status": "NATIVE_STUDY_CASE_CHECKS_PASS",
                            "study_or_solver_invoked": True,
                            "study_run_submission_count": len(study_submissions),
                            "completed_case_count": len(successful_cases),
                            "latest_case": case_id})
            write_json(evidence / "summary.json", summary)

        phase_checks = {}
        for mesh in ("coarse", "fine"):
            phase0 = successful_cases[f"{mesh}-phase0"]
            phase90 = successful_cases[f"{mesh}-phase90"]
            phase_checks[mesh] = helpers["verify_native_phase_pair"](
                {"phase_value": phase0["phase_value"],
                 "output_port_phase": phase0["output_port_phase"],
                 "output_fields": phase0["raw_fields"]["641"]["output"],
                 "native_overlap": phase0["native_overlap"]},
                {"phase_value": phase90["phase_value"],
                 "output_port_phase": phase90["output_port_phase"],
                 "output_fields": phase90["raw_fields"]["641"]["output"],
                 "native_overlap": phase90["native_overlap"]})
        write_json(evidence / "native_phase_controls.json", {
            "status": "PASS_NATIVE_EXCITATION_PHASE_CONTROLS",
            "fixed_output_reference_phase": "0[deg]",
            "phase_pairs": phase_checks,
            "software_global_phase_control": successful_cases["fine-phase0"]["software_global_phase_control"],
            "prelaunch_software_negative_controls": software_negative,
        })

        # Reopen the final refined, 90-degree saved solution in the same Worker.
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("birth deadline entered cleanup reserve before final saved-model reopen")
        final_case = successful_cases["fine-phase90"]
        saved = saved_models["fine-phase90"]
        saved_path = Path(saved["path"])
        if sha256_file(saved_path) != saved["sha256"]:
            raise RuntimeError("saved MPH artifact changed before reopen")
        reopened_ref, reopened_revision, reopened_tag = _load_and_bind_saved_model(
            daemon, saved_path, "fine_phase90", evidence)
        fixture_copy_reopen = project_workspace / f"{FIXTURE.stem}_reopen.java"
        if fixture_copy_reopen.exists():
            raise FileExistsError(f"reopened fixture staging path exists: {fixture_copy_reopen}")
        fixture_copy_reopen.write_bytes(FIXTURE.read_bytes())
        reopened_inventory, reopened_ref, reopened_revision = _fixture_call(
            daemon, reopened_ref, reopened_revision, fixture_copy_reopen.name,
            {"phase": "solution_inventory"}, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label="reopened_solution_inventory")
        reopened_binding = validate_study_inventory(reopened_inventory)
        reopened_steps = _solver_step_map(reopened_binding)
        reopened_datasets, reopened_ref, reopened_revision = _dataset_inventory(
            daemon, reopened_ref, reopened_revision, deadline_epoch=deadline_epoch,
            evidence=evidence / "reopened", helpers=helpers)
        reopened_sources = resolve_mode_overlap_sources(
            reopened_datasets["datasets"], reopened_datasets["index_by_tag"],
            reopened_steps)
        reopened_signal = reopened_sources["sources"]["signal"]
        reopened_incident = reopened_sources["sources"]["incident_reference"]
        reopened_output_raw, reopened_ref, reopened_revision = _raw_fields_call(
            daemon, reopened_ref, reopened_revision, fixture_copy_reopen.name,
            reopened_signal["dataset_id"], "output_x8", 641, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label="reopened_raw_output_641")
        reopened_input_raw, reopened_ref, reopened_revision = _raw_fields_call(
            daemon, reopened_ref, reopened_revision, fixture_copy_reopen.name,
            reopened_incident["dataset_id"], "input_xminus10", 641, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label="reopened_raw_input_641")
        reopened_definition = build_mode_overlap_definition(
            reopened_sources, int(reopened_output_raw["normal_sign"]),
            int(reopened_input_raw["normal_sign"]))
        reopened_native, reopened_ref, reopened_revision = _native_overlap_call(
            daemon, reopened_ref, reopened_revision, reopened_definition, helpers,
            deadline_epoch=deadline_epoch, evidence=evidence,
            label="reopened_native_mode_overlap_fine_phase90")
        reopened_independent_integrals = helpers["independent_integrals"](
            reopened_output_raw, reopened_input_raw)
        reopened_native_vs_raw_integrals = helpers["compare_native_integrals"](
            reopened_native, reopened_independent_integrals, tolerance=1e-3)
        reopened_capture_independent = helpers["independent_capture_aperture_flux"](reopened_output_raw)
        reopened_capture_comparison = helpers["compare_native_capture_flux"](
            reopened_native, reopened_capture_independent, reopened_independent_integrals,
            tolerance=1e-3)
        reopen_integrals = _compare_reopen_integrals(
            final_case["native_overlap"], reopened_native, tolerance=1e-8)
        original_values = _raw_rows(final_case["raw_fields"]["641"]["output"])
        reopened_values = _raw_rows(reopened_output_raw)
        if set(original_values) != set(reopened_values):
            raise RuntimeError("saved/reopened raw native field expression inventory changed")
        reopen_field_errors = {}
        for name in original_values:
            left, right = original_values[name], reopened_values[name]
            if len(left) != len(right):
                raise RuntimeError(f"saved/reopened field {name} has a different sample count")
            error = max((abs(a - b) / max(abs(a), 1e-30) for a, b in zip(left, right)), default=0.0)
            reopen_field_errors[name] = error
            if error > 1e-8:
                raise RuntimeError(f"saved/reopened raw field {name} differs by {error} > 1e-8")
        write_json(evidence / "saved_reopened_science_result.json", {
            "status": "PASS_SAVED_REOPENED_FINE_PHASE90_NATIVE_RESULT",
            "saved_artifact": saved, "loaded_model_tag": reopened_tag,
            "loaded_model_ref": reopened_ref, "loaded_revision": reopened_revision,
            "original_native_overlap": final_case["native_overlap"],
            "reopened_native_overlap": reopened_native,
            "integral_reopen_comparison": reopen_integrals,
            "reopened_native_vs_independent_raw_integrals": reopened_native_vs_raw_integrals,
            "reopened_independent_integrals": reopened_independent_integrals,
            "independent_capture_aperture_flux": reopened_capture_independent,
            "native_vs_independent_capture_flux": reopened_capture_comparison,
            "native_eta_capture": reopened_native["eta_capture"]["value"],
            "independently_recomputed_eta_capture": reopened_capture_comparison["independent_eta_capture"],
            "raw_output_field_max_relative_errors": reopen_field_errors,
            "saved_sha256_after": sha256_file(saved_path),
        })
        actual_submissions = _actual_science_study_run_submissions(daemon, preflight)
        if len(actual_submissions) != MAX_STUDY_RUN_CALLS:
            raise RuntimeError(f"final durable Study.run audit expected exactly four calls, got {len(actual_submissions)}")
        summary.update({
            "status": "PASS_W23_PLANAR_TE_SCIENCE_MECHANISM_CHECKPOINT",
            "study_or_solver_invoked": True,
            "study_run_submission_count": len(actual_submissions),
            "completed_case_count": len(successful_cases),
            "actual_solver_execution_count": "UNKNOWN_INTERNAL_SOLVER_STEPS_NOT_INFERRED",
            "native_result": "PASS_LIMITED_2D_PLANAR_TE_MECHANISM",
            "native_phase_controls": phase_checks,
            "native_route_negative_control": route_negative,
            "saved_models": saved_models,
            "provisional_saved_models": provisional_saved_models,
            "saved_reopen_check": "PASS",
            "upstream_power_balance": "NOT_CLAIMED",
            "pml_power_balance": "NOT_CLAIMED",
            "full_w23_acceptance": "NOT_COMPLETE",
            "remaining_original_scope": freeze["scope_limits"]["remaining_original_scope"],
        })
        write_json(evidence / "study_run_worker_events.json", {
            "count_basis": "durably persisted Worker phase=submitted, kind=call, metadata.type=call, metadata.method=run",
            "study_run_calls": actual_submissions,
            "runner_submission_records": study_submissions,
            "count": len(actual_submissions), "maximum": MAX_STUDY_RUN_CALLS,
            "solver_execution_count": "UNKNOWN_INTERNAL_SOLVER_STEPS_NOT_INFERRED",
            "configured_study_features_per_run": list(CONFIGURED_STUDY_STEPS),
        })
    except BaseException as exc:
        summary.update({"status": "FAIL_OR_INCOMPLETE",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                        "study_run_submission_count": len(study_submissions),
                        "provisional_saved_models": dict(provisional_saved_models),
                        "native_result": "UNKNOWN_OR_NOT_RUN"})
        write_json(evidence / "failure.json", {
            "status": "FAIL_OR_INCOMPLETE",
            "error": summary["error"], "traceback": summary["traceback"],
            "study_run_submissions": study_submissions,
            "provisional_saved_models": dict(provisional_saved_models),
            "no_retry": True,
        })
    finally:
        if worker_identity is None and isinstance(worker_identity_capture.get("identity"), Mapping):
            worker_identity = dict(worker_identity_capture["identity"])
        if worker_port is None and type(worker_identity_capture.get("port")) is int:
            worker_port = worker_identity_capture["port"]
        if server is not None and server.worker is not None:
            # Startup can fail between Popen birth and the normal return path.
            # Capture its exact child identity locally; never infer identity
            # from a PID-only global inventory row.
            try:
                partial = _capture_worker_process_identity(server, helpers)
                if worker_identity is None and isinstance(partial.get("identity"), Mapping):
                    worker_identity = dict(partial["identity"])
                if worker_port is None and type(partial.get("port")) is int:
                    worker_port = partial["port"]
                worker_identity_capture.setdefault("final_local_capture", partial)
            except BaseException as exc:
                worker_identity_capture["final_local_capture_error"] = f"{type(exc).__name__}: {exc}"
        if worker_identity_capture or (server is not None and server.worker is not None):
            write_json(evidence / "worker_identity_reconciliation.json", {
                "status": "EXACT_TASK_WORKER_IDENTITY_RECONCILED"
                    if isinstance(worker_identity, Mapping) else "WORKER_IDENTITY_UNAVAILABLE",
                "capture_before_followup_rpcs": worker_identity_capture,
                "selected_worker_identity": worker_identity,
                "selected_worker_port": worker_port,
                "identity_source": "exact task-owned Worker Popen and fresh process snapshot",
                "no_pid_only_global_attribution": True,
            })
        if daemon is not None and original_add_event is not None:
            try:
                daemon.store.add_event = original_add_event
            except BaseException as exc:
                summary["store_callback_restore_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        durable_study_rows: list[dict[str, Any]] | None = None
        durable_study_count: int | str = 0
        if daemon is not None:
            try:
                durable_study_rows = _actual_science_study_run_submissions(
                    daemon, helpers["preflight_module"])
                study_submissions[:] = _runner_study_submission_records(durable_study_rows)
                observed_count = len(durable_study_rows)
                durable_study_count = ("UNKNOWN" if observed_count < study_dispatch_attempt_count
                                        else observed_count)
            except BaseException as exc:
                durable_study_rows = None
                durable_study_count = "UNKNOWN" if study_dispatch_attempt_count else 0
                summary["study_run_accounting_reconciliation_error"] = f"{type(exc).__name__}: {exc}"
        elif study_dispatch_attempt_count:
            durable_study_count = "UNKNOWN"
        write_json(evidence / "study_run_submission_count.json", {
            "count": durable_study_count,
            "observed_durable_worker_event_count": (len(durable_study_rows)
                                                     if durable_study_rows is not None else "UNAVAILABLE"),
            "dispatch_attempt_count": study_dispatch_attempt_count,
            "maximum": MAX_STUDY_RUN_CALLS,
            "count_basis": "durable study.run job plus exact Worker phase=submitted method=run events",
            "submissions": durable_study_rows if durable_study_rows is not None else study_submissions,
            "unknown_after_dispatch_is_not_reported_as_zero": True,
        })
        summary["study_run_submission_count"] = durable_study_count
        summary["study_run_dispatch_attempt_count"] = study_dispatch_attempt_count
        summary["study_run_submission_count_basis"] = "durable_operation_and_worker_events"
        summary["provisional_saved_models"] = dict(provisional_saved_models)
        managed_reconciliation = None
        direct_reconciliation = None
        cleanup_gates = None
        cleanup_authorization = None
        idle = None
        live_worker_health = None
        endpoint_identity_gate = None
        worker_disconnect_receipt: dict[str, Any] = {"status": "NOT_ATTEMPTED"}
        if daemon is not None:
            try:
                preflight_module = helpers["preflight_module"]
                if server is not None and server.proc is not None:
                    endpoint_identity_gate = preflight_module._task_endpoint_identity_gate(
                        server, server_identity=server_identity or {},
                        worker_identity=worker_identity, worker_port=worker_port,
                        process_snapshot=helpers["_process_snapshot"],
                        require_worker_peer=True)
                    write_json(evidence / "cleanup_endpoint_identity_gate.json",
                               endpoint_identity_gate)
                    if endpoint_identity_gate.get("safe") is True:
                        try:
                            live_worker_health = server.worker.health(timeout_s=5.0)
                            write_json(evidence / "cleanup_worker_health_before_disconnect.json", {
                                "observed_at_utc": utc_now(),
                                "request": "direct Worker health RPC; no model or solver call",
                                "response": live_worker_health,
                                "expected_endpoint": f"127.0.0.1:{server.port}",
                            })
                        except BaseException as exc:
                            write_json(evidence / "cleanup_worker_health_before_disconnect.json", {
                                "observed_at_utc": utc_now(), "status": "HEALTH_UNKNOWN",
                                "error": f"{type(exc).__name__}: {exc}",
                            })
                managed_reconciliation = helpers["preflight_module"]._ledger_reconciliation(daemon)
                try:
                    managed_reconciliation["sqlite_snapshot"] = helpers["preflight_module"]._backup_operation_store(
                        daemon, evidence / "raw_operations.sqlite3")
                except BaseException as exc:
                    managed_reconciliation["sqlite_snapshot_error"] = f"{type(exc).__name__}: {exc}"
                write_json(evidence / "managed_ledger_reconciliation.json", managed_reconciliation)
                direct_events = read_jsonl(direct_journal.path)
                direct_reconciliation = helpers["reconcile_direct_rpc_events"](direct_events)
                write_json(evidence / "direct_rpc_reconciliation_before_disconnect.json", direct_reconciliation)
                cleanup_gates = helpers["combine_cleanup_gates"](managed_reconciliation,
                                                                 direct_reconciliation)
                idle = helpers["preflight_module"]._daemon_idle_proof(
                    daemon, worker_health=live_worker_health,
                    expected_endpoint=(f"127.0.0.1:{server.port}"
                                       if server is not None and server.port is not None else None))
                cleanup_authorization = _cleanup_authorization(
                    project_create_receipt, immutable_run_project_id,
                    helpers["preflight_module"], managed_reconciliation,
                    cleanup_gates, idle)
                write_json(evidence / "cleanup_gates_before_disconnect.json", {
                    "combined": cleanup_gates, "daemon_idle_proof": idle,
                    "durable_domain_unknown_preserved": managed_reconciliation.get("durable_result"),
                })
                write_json(evidence / "cleanup_project_scope_before_disconnect.json", cleanup_authorization)
            except BaseException as exc:
                summary["ledger_reconciliation_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        cleanup = {"status": "NOT_STARTED"}
        if server is not None and server.proc is not None:
            safe = bool(cleanup_authorization
                        and cleanup_authorization.get("safe_for_owned_cleanup") is True
                        and endpoint_identity_gate
                        and endpoint_identity_gate.get("safe") is True
                        and idle
                        and idle.get("idle") is True
                        and idle.get("worker_health_quiescent") is True)
            if safe:
                try:
                    disconnect_request_id = f"w23-cleanup-disconnect-{uuid4()}"
                    # PersistentJavaWorker exposes disconnect through its production
                    # RemoteClient facade, which returns the decoded Worker result mapping.
                    disconnect_response = server.worker.client().disconnect(
                        request_id=disconnect_request_id, rpc_timeout_s=10.0)
                    health_after_disconnect = server.worker.health(timeout_s=5.0)
                    worker_disconnect_ok = _worker_disconnect_verified(
                        disconnect_response, live_worker_health, health_after_disconnect)
                    worker_disconnect_receipt = {
                        "status": "PASS_WORKER_DISCONNECTED_QUIESCENT"
                                  if worker_disconnect_ok else "BLOCKED_WORKER_DISCONNECT_NOT_CONFIRMED",
                        "request_id": disconnect_request_id,
                        "decoded_remote_client_result": disconnect_response,
                        "health_before_disconnect": live_worker_health,
                        "post_disconnect_health": health_after_disconnect,
                        "durable_unknown_preserved": True,
                    }
                    write_json(evidence / "cleanup_worker_disconnect.json", worker_disconnect_receipt)
                    direct_reconciliation = helpers["reconcile_direct_rpc_events"](
                        read_jsonl(direct_journal.path))
                    cleanup_gates = helpers["combine_cleanup_gates"](
                        managed_reconciliation, direct_reconciliation)
                    cleanup_authorization = _cleanup_authorization(
                        project_create_receipt, immutable_run_project_id,
                        helpers["preflight_module"], managed_reconciliation,
                        cleanup_gates, idle)
                    write_json(evidence / "direct_rpc_reconciliation.json", direct_reconciliation)
                    write_json(evidence / "cleanup_gates.json", {
                        "combined": cleanup_gates, "daemon_idle_proof": idle,
                        "durable_domain_unknown_preserved": managed_reconciliation.get("durable_result"),
                        "worker_disconnect": worker_disconnect_receipt,
                    })
                    write_json(evidence / "cleanup_project_scope.json", cleanup_authorization)
                    if worker_disconnect_ok and cleanup_authorization.get("safe_for_owned_cleanup") is True:
                        cleanup = helpers["preflight_module"]._exact_owned_cleanup(
                            server, server_identity=server_identity or {},
                            worker_identity=worker_identity, worker_port=worker_port,
                            process_snapshot=helpers["_process_snapshot"],
                            reconciliation=managed_reconciliation,
                            evidence=evidence, require_fixture_terminal=False)
                    else:
                        cleanup = {
                            "status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                            "reason": "fresh Worker disconnect/health or terminal RPC reconciliation did not pass",
                            "durable_unknown_preserved": True,
                            "worker_disconnect": worker_disconnect_receipt,
                            "cleanup_authorization": cleanup_authorization,
                        }
                        write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
                except BaseException as exc:
                    cleanup = {"status": "CLEANUP_UNVERIFIED",
                               "error": f"{type(exc).__name__}: {exc}",
                               "durable_unknown_preserved": True}
                    write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
            else:
                project_scope = (cleanup_authorization.get("project_scope")
                                 if isinstance(cleanup_authorization, Mapping) else None)
                cleanup = {
                    "status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                    "server_pid": server.proc.pid, "port": server.port,
                    "project_scope": project_scope,
                    "endpoint_identity_gate": endpoint_identity_gate,
                    "worker_health_quiescent": bool(idle and idle.get("worker_health_quiescent")),
                    "managed_ledger_safe": bool(managed_reconciliation and managed_reconciliation.get("safe_for_owned_cleanup")),
                    "direct_rpc_safe": bool(direct_reconciliation and direct_reconciliation.get("safe_for_owned_cleanup")),
                    "daemon_idle": bool(idle and idle.get("idle")),
                    "action": ("no disconnect or signal; fresh exact process/listener/client identity gate failed"
                               if not endpoint_identity_gate or endpoint_identity_gate.get("safe") is not True
                               else "no disconnect or signal; fresh connected Worker health, exact RPC terminality, project scope, or idle proof failed"),
                    "durable_domain_unknown_preserved": (
                        managed_reconciliation.get("durable_result") if managed_reconciliation else "UNAVAILABLE"),
                }
                write_json(evidence / "exact_owned_cleanup_receipt.json", cleanup)
            if cleanup.get("status") != "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
                summary["status"] = "FAIL_OR_INCOMPLETE"
            try:
                final_inventory = helpers["preflight_module"]._process_inventory()
                write_json(evidence / "postcleanup_inventory.json", final_inventory)
            except BaseException as exc:
                summary["postcleanup_inventory_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        elif server is not None:
            cleanup = {"status": "NOT_STARTED", "reason": "server did not birth"}
        if daemon is not None and cleanup.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
            try:
                daemon.close()
            except BaseException as exc:
                summary["daemon_close_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
        if study_dispatch_attempt_count == 0:
            summary["study_or_solver_invoked"] = False
        elif durable_study_rows and study_submissions:
            summary["study_or_solver_invoked"] = True
        else:
            summary["study_or_solver_invoked"] = "UNKNOWN"
        summary["actual_solver_execution_count"] = "UNKNOWN_INTERNAL_SOLVER_STEPS_NOT_INFERRED"
        summary["cleanup"] = cleanup
        if birth_epoch is not None:
            finished_epoch = time.time()
            elapsed = max(0.0, finished_epoch - birth_epoch)
            over_budget = elapsed > MAX_BIRTH_BUDGET_S
            summary["birth_inclusive_budget"] = {
                "birth_epoch": birth_epoch,
                "deadline_epoch": deadline_epoch,
                "finished_epoch_includes_cleanup": finished_epoch,
                "elapsed_seconds_includes_cleanup": elapsed,
                "max_seconds": MAX_BIRTH_BUDGET_S,
                "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
                "budget_exceeded": over_budget,
                "over_budget_seconds": max(0.0, elapsed - MAX_BIRTH_BUDGET_S),
                "deadline_was_not_reset": True,
            }
            if over_budget:
                summary["status"] = "FAIL_OR_INCOMPLETE"
                summary["budget_status"] = "OVER_BUDGET_INCLUDING_CLEANUP"
        summary["finished_utc"] = utc_now()
        summary["direct_rpc_reconciliation"] = direct_reconciliation
        summary["managed_ledger_reconciliation"] = (
            {"status": managed_reconciliation.get("status"),
             "safe_for_owned_cleanup": managed_reconciliation.get("safe_for_owned_cleanup"),
             "durable_result": managed_reconciliation.get("durable_result")}
            if managed_reconciliation else None)
        summary["cleanup_gates"] = cleanup_gates
        summary["cleanup_authorization"] = cleanup_authorization
        summary["project_create_receipt"] = project_create_receipt
        summary["immutable_run_project_id"] = immutable_run_project_id
        write_json(evidence / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="run offline gates and freeze a candidate")
    prep.add_argument("--evidence", required=True, type=Path)
    run_parser = sub.add_parser("execute", help="execute a root-approved exact frozen candidate")
    run_parser.add_argument("--freeze", required=True, type=Path)
    run_parser.add_argument("--freeze-sha256", required=True)
    run_parser.add_argument("--approval", required=True, type=Path)
    run_parser.add_argument("--work", required=True, type=Path)
    run_parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.evidence.resolve())
        print(json.dumps({key: result[key] for key in (
            "status", "candidate_id", "freeze_path", "freeze_sha256", "source_count",
            "source_manifest_sha256", "source_manifest_file_path",
            "source_manifest_file_bytes", "source_manifest_file_sha256",
            "planned_study_run_calls",
            "configured_study_feature_count", "actual_solver_execution_count",
            "native_process_started")}, indent=2, ensure_ascii=False))
        return 0
    result = execute(args.freeze.resolve(), args.freeze_sha256,
                     args.approval.resolve(), args.work.resolve(), args.evidence.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
    return 0 if result.get("status") == "PASS_W23_PLANAR_TE_SCIENCE_MECHANISM_CHECKPOINT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
