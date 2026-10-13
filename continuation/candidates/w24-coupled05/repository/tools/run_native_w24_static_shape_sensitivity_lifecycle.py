#!/usr/bin/env python3
"""One-process W24 sensitivity lifecycle over the published setup artifacts.

Candidate preparation is offline. Execution keeps the exact new science
server, ControlDaemon, Worker, OperationStore, and birth budget alive across
readmission, external approval, fourteen solves/captures/comparisons, and
terminal cleanup. A later CLI invocation cannot resume this process.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.run_native_w24_cure_science import BirthBudget, CampaignError
from tools.run_native_w24_static_shape_sensitivity_science import (
    CAPTURE_KEY_ORDER, SCIENCE_EXECUTOR_SOURCE, SETUP_FIXTURE_SOURCE,
    SETUP_READBACK_SOURCE, STUDY_RUN_SOURCE, _birth_budget_binding_record,
    _model_epoch, execute_sensitivity_campaign,
    readmit_sensitivity_setup_artifacts, sha256_file,
    sensitivity_capture_resource_estimate,
)
from tools.w24_static_shape_capture import CAPTURE_SOURCE
from tools.w24_static_shape_sensitivity import (
    build_sensitivity_campaign_plan, sensitivity_submission_slots,
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
CANDIDATE_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_LIFECYCLE_CANDIDATE_V1"
APPROVAL_INPUT_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_APPROVAL_INPUT_V1"
APPROVAL_INBOX_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_APPROVAL_INBOX_V1"
LIFECYCLE_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_LIFECYCLE_RUN_V1"
MAX_TRANSITION_WALL_S = 3600
MAX_WAIT_POLL_S = 1.0
SOURCE_PATHS = {
    "study_run": STUDY_RUN_SOURCE,
    "history_capture": CAPTURE_SOURCE,
    "science_executor": SCIENCE_EXECUTOR_SOURCE,
    "setup_fixture": SETUP_FIXTURE_SOURCE,
    "setup_readback": SETUP_READBACK_SOURCE,
}
LIFECYCLE_DEPENDENCY_PATHS = {
    "setup_campaign": Path("tools/run_native_w24_static_shape_setup_campaign.py"),
    "managed_setup": Path("tools/run_native_w24_static_shape_setup.py"),
    "static_shape_science": Path("tools/run_native_w24_static_shape_science.py"),
    "sensitivity_plan": Path("tools/w24_static_shape_sensitivity.py"),
    "sensitivity_comparison": Path("tools/w24_static_shape_sensitivity_comparison.py"),
    "capture_analysis": Path("tools/w24_static_shape_capture.py"),
    "setup_preflight": Path("tools/run_native_w24_cure_preflight.py"),
    "server_helper": Path("tools/run_native_resume_smoke.py"),
    "cure_science": Path("tools/run_native_w24_cure_science.py"),
    "control_daemon": Path("comsol_mcp/_control_daemon.py"),
    "managed_backend": Path("comsol_mcp/_managed_backend.py"),
    "project_authority": Path("comsol_mcp/_project_authority.py"),
    "operation_store": Path("comsol_mcp/_operation_store.py"),
    "session_context": Path("comsol_mcp/_session_context.py"),
    "session_server": Path("comsol_mcp/_session_server.py"),
    "session_lifecycle": Path("comsol_mcp/_session_lifecycle.py"),
    "platform_process": Path("comsol_mcp/_platform_process.py"),
    "g2_isolation": Path("comsol_mcp/_g2_isolation.py"),
    "runtime_installation": Path("comsol_mcp/_runtime_installation.py"),
    "java_worker": Path("comsol_mcp/_java_worker.py"),
}
LIFECYCLE_MODULES = {
    "setup_campaign": "tools.run_native_w24_static_shape_setup_campaign",
    "managed_setup": "tools.run_native_w24_static_shape_setup",
    "static_shape_science": "tools.run_native_w24_static_shape_science",
    "sensitivity_plan": "tools.w24_static_shape_sensitivity",
    "sensitivity_comparison": "tools.w24_static_shape_sensitivity_comparison",
    "capture_analysis": "tools.w24_static_shape_capture",
    "setup_preflight": "tools.run_native_w24_cure_preflight",
    "server_helper": "tools.run_native_resume_smoke",
    "cure_science": "tools.run_native_w24_cure_science",
    "control_daemon": "comsol_mcp._control_daemon",
    "managed_backend": "comsol_mcp._managed_backend",
    "project_authority": "comsol_mcp._project_authority",
    "operation_store": "comsol_mcp._operation_store",
    "session_context": "comsol_mcp._session_context",
    "session_server": "comsol_mcp._session_server",
    "session_lifecycle": "comsol_mcp._session_lifecycle",
    "platform_process": "comsol_mcp._platform_process",
    "g2_isolation": "comsol_mcp._g2_isolation",
    "runtime_installation": "comsol_mcp._runtime_installation",
    "java_worker": "comsol_mcp._java_worker",
}


class LifecycleError(CampaignError):
    """A lifecycle input, gate, or result is not proven safe to continue."""


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False, default=str) + "\n").encode()


def _write_new(path: Path, value: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_bytes(value)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return hashlib.sha256(data).hexdigest()


def _replace_fsynced(path: Path, value: Mapping[str, Any]) -> str:
    data = _canonical_bytes(value)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.is_symlink() or not path.is_file():
        temp.unlink(missing_ok=True)
        raise LifecycleError("lifecycle receipt identity changed before update")
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(data).hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LifecycleError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"{label} is unreadable JSON") from exc
    if not isinstance(value, Mapping):
        raise LifecycleError(f"{label} must be a JSON object")
    return dict(value)


def _private_approval_root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
        raise LifecycleError("approval root must be an exact real directory")
    info = path.stat()
    if (hasattr(os, "geteuid") and info.st_uid != os.geteuid()) or stat.S_IMODE(info.st_mode) & 0o077:
        raise LifecycleError("approval root must be owned by this user and have no group/other access")
    return path


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise LifecycleError(f"{label} is not a lowercase SHA-256")
    return value


def _verified_file(path_value: Any, expected: Any, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise LifecycleError(f"{label} path is missing")
    path = Path(path_value)
    digest = _sha(expected, f"{label} digest")
    if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
        raise LifecycleError(f"{label} is absent, aliased, or has changed bytes")
    return path.resolve(strict=True)


def _one_epoch(binding: Any, project_id: str, key: str) -> tuple[str, int, str]:
    if not isinstance(binding, Mapping) or not isinstance(binding.get("model_ref"), Mapping):
        raise LifecycleError(f"{key} historic setup ModelRef is missing")
    ref = binding["model_ref"]
    session_id, generation, server_id = (
        binding.get("session_id"), ref.get("generation"), ref.get("server_instance_id"))
    if (binding.get("project_id") != project_id or not isinstance(session_id, str) or
            type(generation) is not int or generation < 1 or
            not isinstance(server_id, str) or not server_id):
        raise LifecycleError(f"{key} historic setup ModelRef has a foreign project or incomplete epoch")
    return session_id, generation, server_id


def validate_setup_campaign_receipt(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Recheck all setup slots and the prior exact Worker/server retirement."""
    digest = _sha(expected_sha256, "setup campaign SHA-256")
    campaign_file = _verified_file(str(path), digest, "setup campaign receipt")
    receipt = _read_object(campaign_file, "setup campaign receipt")
    if (receipt.get("schema") != "W24_STATIC_SHAPE_SETUP_CAMPAIGN_RUN_V1" or
            receipt.get("status") != "SETUP_14_OF_14_COMPLETE_SCIENCE_NOT_RUN" or
            receipt.get("native_science_status") != "NOT_RUN" or
            receipt.get("native_result_status") != "NOT_RUN" or
            receipt.get("planned_slot_count") != 14 or
            receipt.get("completed_setup_slot_count") != 14):
        raise LifecycleError("setup receipt is incomplete or claims a native science result")
    work = Path(str(receipt.get("work_dir", "")))
    evidence = Path(str(receipt.get("evidence_dir", "")))
    workspace = Path(str(receipt.get("project_workspace", "")))
    session = receipt.get("session_identity")
    project_id = receipt.get("project_id")
    if (not work.is_absolute() or work.is_symlink() or not work.is_dir() or
            not evidence.is_absolute() or evidence.is_symlink() or not evidence.is_dir() or
            not workspace.is_absolute() or workspace.is_symlink() or not workspace.is_dir() or
            not isinstance(session, Mapping) or not isinstance(project_id, str) or
            session.get("project_id") != project_id or type(session.get("worker_epoch")) is not int):
        raise LifecycleError("setup receipt omits its exact project, workspace, work, or retired Worker")
    workspace = workspace.resolve(strict=True)
    slots = receipt.get("completed_setup_slots")
    if not isinstance(slots, list) or len(slots) != 14:
        raise LifecycleError("setup campaign must contain exactly fourteen setup receipts")
    historic: dict[str, dict[str, Any]] = {}
    epochs: set[tuple[str, int, str]] = set()
    for key, row in zip(CAPTURE_KEY_ORDER, slots):
        if not isinstance(row, Mapping):
            raise LifecycleError(f"{key} setup campaign row is malformed")
        raw_path = _verified_file(row.get("receipt_path"), row.get("receipt_sha256"),
                                  f"historical setup receipt {key}")
        if not raw_path.is_relative_to(evidence.resolve(strict=True)):
            raise LifecycleError(f"{key} historical setup receipt escaped its evidence directory")
        raw = _read_object(raw_path, f"historical setup receipt {key}")
        config_id, case_id = key.split(":", 1)
        if (raw.get("configuration_id") != config_id or raw.get("case_id") != case_id or
                raw.get("status") != "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE" or
                raw.get("native_acceptance") != "NOT_RUN" or
                raw.get("phase_initialization_executed") is not False or
                raw.get("study_run_submission_count") != 0 or
                raw.get("study_run_submissions") != []):
            raise LifecycleError(f"{key} setup receipt is incomplete or includes a solve")
        binding = raw.get("reopened_model_binding")
        epochs.add(_one_epoch(binding, project_id, key))
        readback = raw.get("reopened_configuration_readback")
        artifact = raw.get("project_artifact")
        if not isinstance(readback, Mapping) or not isinstance(artifact, Mapping):
            raise LifecycleError(f"{key} setup receipt lacks full config readback or saved artifact")
        artifact_path = _verified_file(artifact.get("path"), artifact.get("sha256"),
                                       f"{key} unsolved MPH")
        output_root = workspace / "outputs"
        if (artifact_path.suffix.lower() != ".mph" or
                not artifact_path.is_relative_to(output_root) or
                artifact_path.stat().st_size != artifact.get("size_bytes")):
            raise LifecycleError(f"{key} MPH is outside registered outputs or has inconsistent size")
        if not isinstance(readback.get("solution_state_readback"), Mapping):
            raise LifecycleError(f"{key} lacks the setup getSize/solution-state readback")
        historic[key] = {
            "receipt_path": str(raw_path), "receipt_sha256": row["receipt_sha256"],
            "artifact_path": str(artifact_path), "artifact_sha256": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
            "binding": dict(binding), "configuration_readback": dict(readback),
        }
    if len(epochs) != 1 or next(iter(epochs)) != (
            session.get("session_id"), session.get("worker_epoch"), session.get("server_instance_id")):
        raise LifecycleError("setup artifact bindings do not share the exact retired setup Worker epoch")
    cleanup = receipt.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("status") != "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED":
        raise LifecycleError("old setup Worker/server cleanup lacks a terminal retirement receipt")
    expected_evidence = {
        "pre_retirement_ledger.json": cleanup.get("pre_retirement_ledger_sha256"),
        "worker_retirement_proof.json": cleanup.get("worker_retirement_proof_sha256"),
        "disconnected_session_inspect.json": cleanup.get("disconnected_inspect_sha256"),
        "server_stop_receipt.json": cleanup.get("server_stop_sha256"),
    }
    readbacks: dict[str, dict[str, Any]] = {}
    for name, expected in expected_evidence.items():
        p = _verified_file(str(evidence / name), expected, f"setup cleanup {name}")
        readbacks[name] = _read_object(p, f"setup cleanup {name}")
    ledger = readbacks["pre_retirement_ledger.json"]
    retirement = readbacks["worker_retirement_proof.json"]
    inspect = readbacks["disconnected_session_inspect.json"]
    stop = readbacks["server_stop_receipt.json"]
    lifecycle = inspect.get("data", {}).get("lifecycle", {}) if isinstance(inspect.get("data"), Mapping) else {}
    if (ledger.get("safe_to_retire") is not True or ledger.get("study_run_submissions") not in ([], None) or
            retirement.get("status") != "RETIRED" or retirement.get("child_reaped") is not True or
            retirement.get("worker_instance_id") != session.get("worker_instance_id") or
            lifecycle.get("state") != "DISCONNECTED" or lifecycle.get("client_state") != "RETIRED" or
            stop.get("status") != "STOPPED_AND_REAPED" or stop.get("child_reaped") is not True or
            stop.get("listener_absent") is not True):
        raise LifecycleError("setup job ledger/Worker retirement/server stop proof is incomplete")
    server_birth_path = evidence / "server_birth_listener.json"
    if server_birth_path.is_symlink() or not server_birth_path.is_file():
        raise LifecycleError("historical setup Server birth receipt is missing")
    server_birth = _read_object(server_birth_path, "historical setup Server birth")
    if (server_birth.get("status") != "Popen_BIRTH_AND_LOOPBACK_LISTENER_VERIFIED" or
            server_birth.get("process", {}).get("pid") != stop.get("pid") or
            server_birth.get("process", {}).get("birth") is None or
            type(server_birth.get("process", {}).get("start_epoch_ms")) is not int or
            server_birth["process"]["start_epoch_ms"] <= 0 or
            server_birth.get("max_wall_seconds_including_cleanup") != 4 * 60 * 60):
        raise LifecycleError("historical setup Server birth and exact stop do not reconcile")
    source_manifest_path = evidence / "project_java_source_manifest.json"
    project_evidence_path = evidence / "project_create_and_inspect_prebirth.json"
    for p, label in ((source_manifest_path, "setup project Java source manifest"),
                     (project_evidence_path, "setup project create/inspect evidence")):
        if p.is_symlink() or not p.is_file():
            raise LifecycleError(f"{label} is missing")
    source_manifest = _read_object(source_manifest_path, "project Java source manifest")
    project_evidence = _read_object(project_evidence_path, "project create/inspect evidence")
    project_response = project_evidence.get("response")
    if (not isinstance(project_response, Mapping) or project_response.get("success") is not True or
            not isinstance(source_manifest.get("sources"), Mapping)):
        raise LifecycleError("project workspace authority/source evidence failed readback")
    operation_store = work / "mcp-home/operations.sqlite3"
    if operation_store.is_symlink() or not operation_store.is_file():
        raise LifecycleError("historic OperationStore is missing; new project/store creation is refused")
    store_stat = operation_store.stat()
    return {
        "receipt_path": str(campaign_file), "receipt_sha256": digest,
        "work_dir": str(work.resolve(strict=True)), "run_dir": str(evidence.resolve(strict=True)),
        "project_id": project_id, "project_workspace": str(workspace),
        "project_root": str((work / "project").resolve(strict=True)),
        "mcp_home": str((work / "mcp-home").resolve(strict=True)),
        "operation_store_path": str(operation_store.resolve(strict=True)),
        "operation_store_identity": {"device": store_stat.st_dev, "inode": store_stat.st_ino},
        "session_identity": dict(session), "historical_worker_epoch": list(next(iter(epochs))),
        "historical_setup_slots": historic,
        "cleanup_evidence": {name: {"path": str(evidence / name), "sha256": expected_evidence[name]}
                             for name in expected_evidence},
        "setup_server_birth_path": str(server_birth_path.resolve(strict=True)),
        "setup_server_birth_sha256": sha256_file(server_birth_path),
        "historic_setup_server_identity": {
            "pid": server_birth["process"]["pid"],
            "birth": server_birth["process"]["birth"],
            "start_epoch_ms": server_birth["process"]["start_epoch_ms"],
            "birth_epoch_s": server_birth["process"]["start_epoch_ms"] / 1000.0,
            "port": (server_birth.get("listener", {}).get("port")
                     if isinstance(server_birth.get("listener"), Mapping) else None),
        },
        "project_java_source_manifest_path": str(source_manifest_path.resolve(strict=True)),
        "project_java_source_manifest_sha256": sha256_file(source_manifest_path),
        "project_java_source_manifest": source_manifest,
        "project_create_and_inspect_path": str(project_evidence_path.resolve(strict=True)),
        "project_create_and_inspect_sha256": sha256_file(project_evidence_path),
        "project_create_response": dict(project_response),
    }


def prepare_candidate(*, setup_campaign_receipt: Path, expected_setup_campaign_receipt_sha256: str,
                      output_path: Path, campaign_id: str, readmission_transition_id: str,
                      approval_inbox_path: Path, approval_root: Path) -> dict[str, Any]:
    """Freeze historical artifacts and current sources without launching anything."""
    if not ID_RE.fullmatch(campaign_id) or not ID_RE.fullmatch(readmission_transition_id):
        raise LifecycleError("campaign and readmission identifiers must be stable 8..128 character values")
    setup = validate_setup_campaign_receipt(setup_campaign_receipt,
                                            expected_setup_campaign_receipt_sha256)
    root = approval_root.expanduser().absolute()
    inbox = approval_inbox_path.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise LifecycleError("approval root must already be a real directory")
    root = _private_approval_root(root.resolve(strict=True))
    try:
        inbox.relative_to(root)
    except ValueError as exc:
        raise LifecycleError("approval inbox is outside the preauthorized approval root") from exc
    if inbox.exists() or inbox.is_symlink():
        raise LifecycleError("approval inbox must be a new one-shot path")
    source_pins = {}
    for name, path in SOURCE_PATHS.items():
        if path.is_symlink() or not path.is_file():
            raise LifecycleError(f"reviewed source is missing or aliased: {name}")
        source_pins[name] = sha256_file(path)
    lifecycle_dependency_pins = {}
    for name, relative in LIFECYCLE_DEPENDENCY_PATHS.items():
        path = REPO / relative
        if path.is_symlink() or not path.is_file():
            raise LifecycleError(f"lifecycle dependency is missing or aliased: {relative}")
        lifecycle_dependency_pins[name] = sha256_file(path)
    source_rows = setup["project_java_source_manifest"].get("sources", {})
    if (not isinstance(source_rows, Mapping) or
            source_rows.get("fixture", {}).get("sha256") != source_pins["setup_fixture"] or
            source_rows.get("readback", {}).get("sha256") != source_pins["setup_readback"]):
        raise LifecycleError("historic project Java copies differ from the reviewed fixture/readback sources")
    orchestrator_sha = sha256_file(Path(__file__).resolve())
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "status": "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN",
        "campaign_id": campaign_id,
        "readmission_transition_id": readmission_transition_id,
        "setup_campaign": setup,
        "source_sha256": source_pins,
        "lifecycle_dependency_sha256": lifecycle_dependency_pins,
        "orchestrator_sha256": orchestrator_sha,
        "sensitivity_plan": build_sensitivity_campaign_plan(),
        "ordered_slots": list(sensitivity_submission_slots()),
        "capture_resource_estimate": sensitivity_capture_resource_estimate(),
        "new_science_server_budget": {
            "birth_source": "new science Server exact Popen start epoch, before the separate managed Worker birth",
            "wall_seconds_including_all_readmission_approval_wait_science_cleanup": 3600,
            "cleanup_reserve_seconds": 90,
            "worker_birth_is_separately_recorded_and_must_not_precede_server": True,
            "setup_server_budget_is_historical_and_never_reused": True,
        },
        "approval_protocol": {
            "inbox_schema": APPROVAL_INBOX_SCHEMA,
            "approval_inbox_path": str(inbox),
            "approval_root": str(root),
            "approval_input_status": "PENDING_NOT_APPROVED",
            "expected_approval_sha256_must_come_from_external_inbox": True,
            "one_observation_only_no_replacement_hash_retry": True,
            "same_live_runtime_required_no_restart_or_handle_reconstruction": True,
        },
        "native_acceptance": "NOT_RUN",
    }
    output = output_path.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise LifecycleError("candidate output must be a new file; no evidence overwrite")
    digest = _write_new(output, candidate)
    return {"status": candidate["status"], "candidate_path": str(output),
            "candidate_sha256": digest, "candidate": candidate}


def load_candidate(path: Path, expected_sha256: str) -> dict[str, Any]:
    candidate_path = _verified_file(str(path), expected_sha256, "lifecycle candidate")
    candidate = _read_object(candidate_path, "lifecycle candidate")
    if (candidate.get("schema") != CANDIDATE_SCHEMA or
            candidate.get("status") != "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN" or
            candidate.get("native_acceptance") != "NOT_RUN"):
        raise LifecycleError("candidate is not the reviewed pre-run software freeze")
    setup_record = candidate.get("setup_campaign")
    if not isinstance(setup_record, Mapping):
        raise LifecycleError("candidate omitted its verified setup input")
    setup = validate_setup_campaign_receipt(Path(str(setup_record.get("receipt_path", ""))),
                                             str(setup_record.get("receipt_sha256", "")))
    if dict(setup_record) != setup:
        raise LifecycleError("setup receipts, cleanup evidence, project, or OperationStore changed after freeze")
    source_pins = candidate.get("source_sha256")
    if not isinstance(source_pins, Mapping) or set(source_pins) != set(SOURCE_PATHS):
        raise LifecycleError("candidate source closure is incomplete")
    for name, source in SOURCE_PATHS.items():
        if (source.is_symlink() or not source.is_file() or
                sha256_file(source) != _sha(source_pins.get(name), f"source pin {name}")):
            raise LifecycleError(f"reviewed W24 source changed after candidate freeze: {name}")
    dependency_pins = candidate.get("lifecycle_dependency_sha256")
    if (not isinstance(dependency_pins, Mapping) or
            set(dependency_pins) != set(LIFECYCLE_DEPENDENCY_PATHS)):
        raise LifecycleError("lifecycle support dependency pins are incomplete")
    for name, relative in LIFECYCLE_DEPENDENCY_PATHS.items():
        source = REPO / relative
        if (source.is_symlink() or not source.is_file() or
                sha256_file(source) != _sha(dependency_pins.get(name),
                                            f"lifecycle dependency pin {name}")):
            raise LifecycleError(f"lifecycle support source changed after candidate freeze: {name}")
    if sha256_file(Path(__file__).resolve()) != _sha(candidate.get("orchestrator_sha256"),
                                                       "orchestrator source pin"):
        raise LifecycleError("lifecycle orchestrator source changed after candidate freeze")
    if (not ID_RE.fullmatch(str(candidate.get("campaign_id", ""))) or
            not ID_RE.fullmatch(str(candidate.get("readmission_transition_id", "")))):
        raise LifecycleError("candidate campaign/readmission identifier is malformed")
    loaded = {**candidate, "candidate_sha256": expected_sha256}
    _validate_candidate_shape(loaded)
    return loaded


def _validate_candidate_shape(candidate: Mapping[str, Any]) -> None:
    if (candidate.get("schema") != CANDIDATE_SCHEMA or
            candidate.get("status") != "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN" or
            candidate.get("native_acceptance") != "NOT_RUN" or
            not ID_RE.fullmatch(str(candidate.get("campaign_id", ""))) or
            not ID_RE.fullmatch(str(candidate.get("readmission_transition_id", "")))):
        raise LifecycleError("lifecycle candidate is not the frozen pending software candidate")
    _sha(candidate.get("candidate_sha256"), "candidate SHA-256")
    setup = candidate.get("setup_campaign")
    if not isinstance(setup, Mapping):
        raise LifecycleError("candidate omitted the readmission setup campaign")
    _sha(setup.get("receipt_sha256"), "setup campaign receipt SHA-256")
    if (not isinstance(setup.get("project_id"), str) or not setup.get("project_id") or
            not Path(str(setup.get("project_workspace", ""))).is_absolute() or
            not Path(str(setup.get("mcp_home", ""))).is_absolute() or
            not Path(str(setup.get("operation_store_path", ""))).is_absolute() or
            not isinstance(setup.get("operation_store_identity"), Mapping)):
        raise LifecycleError("setup campaign omits its exact registered project or existing OperationStore")
    history = setup.get("historical_setup_slots")
    if not isinstance(history, Mapping) or set(history) != set(CAPTURE_KEY_ORDER):
        raise LifecycleError("candidate does not pin the complete ordered fourteen-slot setup history")
    for key, row in history.items():
        if not isinstance(row, Mapping):
            raise LifecycleError(f"candidate historical setup row is malformed: {key}")
        _sha(row.get("receipt_sha256"), f"historical setup receipt {key}")
        _sha(row.get("artifact_sha256"), f"historical setup artifact {key}")
    old_epoch = setup.get("historical_worker_epoch")
    old_server = setup.get("historic_setup_server_identity")
    if (not isinstance(old_epoch, list) or len(old_epoch) != 3 or
            not isinstance(old_server, Mapping) or type(old_server.get("pid")) is not int or
            not isinstance(old_server.get("birth"), str) or
            type(old_server.get("start_epoch_ms")) is not int or
            type(old_server.get("birth_epoch_s")) not in (int, float)):
        raise LifecycleError("historical setup Worker/Server identity is incomplete")
    source_pins = candidate.get("source_sha256")
    if not isinstance(source_pins, Mapping) or set(source_pins) != set(SOURCE_PATHS):
        raise LifecycleError("candidate source pin set differs from the sensitivity science contract")
    for name, digest in source_pins.items():
        _sha(digest, f"source pin {name}")
    dependency_pins = candidate.get("lifecycle_dependency_sha256")
    if (not isinstance(dependency_pins, Mapping) or
            set(dependency_pins) != set(LIFECYCLE_DEPENDENCY_PATHS)):
        raise LifecycleError("candidate lifecycle import/cleanup source closure is incomplete")
    for name, digest in dependency_pins.items():
        _sha(digest, f"lifecycle source pin {name}")
    if (candidate.get("ordered_slots") != list(sensitivity_submission_slots()) or
            candidate.get("sensitivity_plan") != build_sensitivity_campaign_plan()):
        raise LifecycleError("candidate fourteen-slot plan differs from the frozen W24 configuration order")
    budget = candidate.get("new_science_server_budget")
    if (not isinstance(budget, Mapping) or
            budget.get("birth_source") != "new science Server exact Popen start epoch, before the separate managed Worker birth" or
            budget.get("wall_seconds_including_all_readmission_approval_wait_science_cleanup") != 3600 or
            budget.get("cleanup_reserve_seconds") != 90 or
            budget.get("setup_server_budget_is_historical_and_never_reused") is not True):
        raise LifecycleError("candidate does not freeze the one new science Server birth budget")
    protocol = candidate.get("approval_protocol")
    if (not isinstance(protocol, Mapping) or
            protocol.get("approval_input_status") != "PENDING_NOT_APPROVED" or
            protocol.get("expected_approval_sha256_must_come_from_external_inbox") is not True or
            protocol.get("one_observation_only_no_replacement_hash_retry") is not True or
            protocol.get("same_live_runtime_required_no_restart_or_handle_reconstruction") is not True):
        raise LifecycleError("candidate approval boundary is not explicit and one-shot")
    try:
        approval_root = _private_approval_root(Path(str(protocol.get("approval_root", ""))))
        inbox = Path(str(protocol.get("approval_inbox_path", "")))
        inbox.relative_to(approval_root)
    except (ValueError, OSError) as exc:
        raise LifecycleError("candidate approval inbox is not inside its private preauthorized root") from exc
    if not inbox.is_absolute() or inbox.exists() or inbox.is_symlink():
        raise LifecycleError("candidate approval inbox must be one new absolute path")


@dataclass(frozen=True)
class ApprovalEnvelope:
    approval_path: Path
    expected_approval_sha256: str
    inbox_sha256: str


def _validate_approval_inbox(*, candidate: Mapping[str, Any], approval_input_path: Path,
                             approval_input_sha256: str) -> ApprovalEnvelope:
    protocol = candidate["approval_protocol"]
    inbox_path = Path(protocol["approval_inbox_path"])
    inbox = _read_object(inbox_path, "external approval inbox")
    inbox_sha = sha256_file(inbox_path)
    if (inbox.get("schema") != APPROVAL_INBOX_SCHEMA or
            inbox.get("campaign_id") != candidate["campaign_id"] or
            inbox.get("candidate_sha256") != candidate["candidate_sha256"] or
            inbox.get("approval_input_path") != str(approval_input_path) or
            inbox.get("approval_input_sha256") != approval_input_sha256):
        raise LifecycleError("approval inbox identifies another campaign or readmission input")
    raw_approval_path = inbox.get("approval_path")
    if not isinstance(raw_approval_path, str) or not raw_approval_path:
        raise LifecycleError("approval inbox omitted approval_path")
    approval_path = Path(raw_approval_path)
    approval_root = Path(protocol["approval_root"])
    approval_root = _private_approval_root(approval_root.resolve(strict=True))
    try:
        approval_path.relative_to(approval_root)
    except ValueError as exc:
        raise LifecycleError("approval file escapes the preauthorized private approval root") from exc
    expected = _sha(inbox.get("expected_approval_sha256"),
                    "externally provided expected approval SHA-256")
    if not approval_path.is_absolute():
        raise LifecycleError("approval file path must be absolute")
    cursor = approval_root
    try:
        for part in approval_path.relative_to(approval_root).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise LifecycleError("approval path cannot traverse a symlink")
    except ValueError as exc:
        raise LifecycleError("approval file path escapes the preauthorized private root") from exc
    approved = _verified_file(str(approval_path), expected, "external approval file")
    if not approved.is_relative_to(approval_root):
        raise LifecycleError("resolved approval file escapes the preauthorized private root")
    if (sha256_file(inbox_path) != inbox_sha or sha256_file(approved) != expected):
        raise LifecycleError("approval inbox or approval bytes changed during validation")
    return ApprovalEnvelope(approved, expected, inbox_sha)


def wait_for_external_approval(*, candidate: Mapping[str, Any], approval_input_path: Path,
                               approval_input_sha256: str, birth_budget: BirthBudget,
                               clock: Callable[[], float] = time.time,
                               sleep: Callable[[float], None] = time.sleep) -> ApprovalEnvelope:
    """Wait only inside the same server-birth budget; the first inbox is final."""
    inbox = Path(candidate["approval_protocol"]["approval_inbox_path"])
    while True:
        if inbox.exists() or inbox.is_symlink():
            return _validate_approval_inbox(
                candidate=candidate, approval_input_path=approval_input_path,
                approval_input_sha256=approval_input_sha256)
        now = clock()
        usable_end = birth_budget.deadline_epoch_s - birth_budget.cleanup_reserve_s
        if now >= usable_end:
            raise LifecycleError("approval did not arrive before cleanup reserve; zero solves may start")
        sleep(min(MAX_WAIT_POLL_S, usable_end - now))


def _store_identity(path: Path) -> dict[str, int]:
    if path.is_symlink() or not path.is_file():
        raise LifecycleError("the existing project OperationStore is absent or aliased")
    stat = path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino}


def _session_epoch(session: Mapping[str, Any]) -> tuple[str, int, str]:
    session_id, epoch, server_id = (session.get("session_id"), session.get("worker_epoch"),
                                   session.get("server_instance_id"))
    if (not isinstance(session_id, str) or not session_id or type(epoch) is not int or epoch < 1 or
            not isinstance(server_id, str) or not server_id):
        raise LifecycleError("public session.connect returned an incomplete Worker epoch")
    return session_id, epoch, server_id


def _validate_birth_records(server: Mapping[str, Any], worker: Mapping[str, Any]) -> float:
    epochs = []
    for label, record in (("server", server), ("worker", worker)):
        epoch = record.get("birth_epoch_s")
        start_ms = record.get("start_epoch_ms")
        if (type(record.get("pid")) is not int or record["pid"] <= 1 or
                not isinstance(record.get("birth"), str) or not record["birth"] or
                isinstance(epoch, bool) or type(epoch) not in (int, float) or
                not math.isfinite(float(epoch)) or epoch <= 0 or
                type(start_ms) is not int or start_ms <= 0 or
                not math.isclose(float(epoch), start_ms / 1000.0,
                                 rel_tol=0.0, abs_tol=1e-6)):
            raise LifecycleError(f"exact {label} Popen birth identity is incomplete")
        epochs.append(float(epoch))
    if epochs[1] + 1e-5 < epochs[0]:
        raise LifecycleError("Worker Popen birth predates the science Server; no budget reset is allowed")
    return epochs[0]


def _approval_input(*, candidate: Mapping[str, Any], readmission: Mapping[str, Any],
                    managed_runner: Any, birth_budget: BirthBudget,
                    server_birth: Mapping[str, Any], worker_birth: Mapping[str, Any]) -> dict[str, Any]:
    bindings = readmission.get("bindings")
    receipt_hashes = readmission.get("setup_receipt_sha256")
    if (not isinstance(bindings, Mapping) or set(bindings) != set(CAPTURE_KEY_ORDER) or
            not isinstance(receipt_hashes, Mapping) or set(receipt_hashes) != set(CAPTURE_KEY_ORDER)):
        raise LifecycleError("readmission did not produce exactly fourteen current bindings/receipts")
    records = {key: bindings[key].as_record() for key in CAPTURE_KEY_ORDER}
    epochs = {_model_epoch(bindings[key]) for key in CAPTURE_KEY_ORDER}
    if len(epochs) != 1:
        raise LifecycleError("readmission ModelRefs span multiple Worker epochs")
    worker_epoch = next(iter(epochs))
    manifest_path = Path(str(readmission.get("manifest_path", "")))
    manifest_hash = _sha(readmission.get("manifest_sha256"), "readmission manifest SHA-256")
    if manifest_path.is_symlink() or not manifest_path.is_file() or sha256_file(manifest_path) != manifest_hash:
        raise LifecycleError("readmission manifest changed before root approval")
    return {
        "schema": APPROVAL_INPUT_SCHEMA, "status": "PENDING_NOT_APPROVED",
        "campaign_id": candidate["campaign_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "setup_campaign_receipt_sha256": candidate["setup_campaign"]["receipt_sha256"],
        "project_id": managed_runner.project_id,
        "project_workspace": str(managed_runner.workspace),
        "operation_store_path": str(Path(managed_runner.daemon.store.path).resolve(strict=True)),
        "operation_store_identity": _store_identity(Path(managed_runner.daemon.store.path)),
        "historical_setup_receipt_sha256": {
            key: row["receipt_sha256"] for key, row in
            candidate["setup_campaign"]["historical_setup_slots"].items()},
        "new_readmission_receipt_sha256": dict(receipt_hashes),
        "readmission_transition_id": candidate["readmission_transition_id"],
        "readmission_manifest_path": str(manifest_path.resolve(strict=True)),
        "readmission_manifest_sha256": manifest_hash,
        "source_sha256": dict(candidate["source_sha256"]),
        "science_server_birth": dict(server_birth),
        "science_worker_birth": dict(worker_birth),
        "science_worker_epoch": {
            "session_id": worker_epoch[0], "generation": worker_epoch[1],
            "server_instance_id": worker_epoch[2]},
        "science_server_birth_budget": birth_budget.receipt(),
        "science_worker_birth_budget_binding": _birth_budget_binding_record(
            birth_budget, worker_epoch),
        "model_bindings": records,
        "ordered_slots": list(sensitivity_submission_slots()),
        "resource_limit_contract": {
            "maximum_study_run_submissions": 14,
            "maximum_capture_files": 14,
            "minimum_single_capture_bytes": sensitivity_capture_resource_estimate()["maximum_single_history_bytes"],
            "maximum_single_capture_bytes": sensitivity_capture_resource_estimate()["per_history_hard_limit_bytes"],
            "minimum_total_raw_capture_bytes": sensitivity_capture_resource_estimate()["total_raw_capture_bytes"],
            "maximum_campaign_wall_time_s": {"minimum": 1, "maximum": 3600},
            "maximum_total_project_output_bytes": "root must set an explicit positive integer",
            "maximum_single_solved_mph_bytes": "root must set an explicit positive integer",
        },
        "capture_resource_estimate": sensitivity_capture_resource_estimate(),
        "approval_status_required": "APPROVED",
        "approval_created_by_runner": False,
        "native_acceptance": "NOT_RUN",
    }


def _persist_run(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        _replace_fsynced(path, payload)
    else:
        _write_new(path, payload)


def _claim_science_birth_once(candidate: Mapping[str, Any]) -> dict[str, str]:
    """Durably claim the one science-server birth allowed from one setup freeze."""
    setup = candidate.get("setup_campaign")
    if not isinstance(setup, Mapping):
        raise LifecycleError("birth claim requires the exact frozen setup campaign")
    mcp_home = Path(str(setup.get("mcp_home", "")))
    if (not mcp_home.is_absolute() or mcp_home.is_symlink() or not mcp_home.is_dir() or
            mcp_home.resolve(strict=True) != mcp_home):
        raise LifecycleError("birth claim requires the existing exact campaign OperationStore home")
    claim_root = mcp_home / "w24-static-shape-science-birth-claims"
    if claim_root.is_symlink():
        raise LifecycleError("science birth claim directory cannot be aliased")
    claim_root.mkdir(mode=0o700, exist_ok=True)
    if claim_root.resolve(strict=True) != claim_root or not claim_root.is_dir():
        raise LifecycleError("science birth claim directory is not a real project control child")
    claim_stat = claim_root.stat()
    if ((hasattr(os, "geteuid") and claim_stat.st_uid != os.geteuid()) or
            stat.S_IMODE(claim_stat.st_mode) & 0o077):
        raise LifecycleError("science birth claim directory must be owned by this user and private")
    setup_sha = _sha(setup.get("receipt_sha256"), "setup campaign receipt SHA-256")
    claim_path = claim_root / f"{setup_sha}.json"
    payload = {
        "schema": "W24_STATIC_SHAPE_SCIENCE_SERVER_BIRTH_CLAIM_V1",
        "status": "CLAIMED_ONE_BIRTH_NO_RESTART",
        "setup_campaign_receipt_sha256": setup_sha,
        "candidate_sha256": candidate.get("candidate_sha256"),
        "campaign_id": candidate.get("campaign_id"),
        "readmission_transition_id": candidate.get("readmission_transition_id"),
        "claim_semantics": "one science Server birth per setup receipt; a new CLI/hash cannot reset the transition",
    }
    digest = _write_new(claim_path, payload)
    return {"path": str(claim_path), "sha256": digest}


class _LifecycleDispatchRejected(LifecycleError):
    """A synchronous route refusal that is proven to precede ControlDaemon dispatch."""


class _AdmissionDaemon:
    """Public ControlDaemon proxy with frozen phase/resource/route admission."""

    def __init__(self, owner: "ProductionSensitivityRuntime", daemon: Any):
        self._owner = owner
        self._daemon = daemon

    def __getattr__(self, name: str) -> Any:
        return getattr(self._daemon, name)

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._owner._admit_route(request)
        try:
            response = self._daemon.dispatch(request)
        except BaseException:
            self._owner._unknown = True
            raise
        self._owner._observe_route_result(request, response)
        return response


def _caused_by_predispatch_refusal(error: BaseException) -> bool:
    pending: BaseException | None = error
    seen: set[int] = set()
    while pending is not None and id(pending) not in seen:
        if isinstance(pending, _LifecycleDispatchRejected):
            return True
        seen.add(id(pending))
        pending = pending.__cause__ or pending.__context__
    return False


def run_reviewed_lifecycle(*, candidate: Mapping[str, Any], candidate_sha256: str,
                           evidence_dir: Path, server_work: Path,
                           runtime_factory: Any,
                           approval_waiter: Callable[..., ApprovalEnvelope],
                           readmit: Callable[..., Mapping[str, Any]] = readmit_sensitivity_setup_artifacts,
                           execute: Callable[..., Mapping[str, Any]] = execute_sensitivity_campaign,
                           clock: Callable[[], float] = time.time,
                           sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """State machine used by the production CLI and handle-injection tests."""
    _validate_candidate_shape(candidate)
    setup = candidate.get("setup_campaign")
    if not isinstance(setup, Mapping) or candidate.get("status") != "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN":
        raise LifecycleError("candidate did not pass the offline setup/source freeze")
    if _sha(candidate_sha256, "candidate SHA-256") != candidate.get("candidate_sha256"):
        raise LifecycleError("candidate hash was not carried from the exact frozen file")
    evidence_dir = evidence_dir.expanduser().absolute()
    server_work = server_work.expanduser().absolute()
    if evidence_dir.exists() or evidence_dir.is_symlink():
        raise LifecycleError("lifecycle evidence path must be new")
    if server_work.exists() or server_work.is_symlink() or not server_work.is_relative_to(Path("/private/tmp")):
        raise LifecycleError("science Server work path must be new under /private/tmp")
    if server_work == Path(setup["work_dir"]):
        raise LifecycleError("science Server work cannot replace the historic setup work")
    inbox = Path(candidate["approval_protocol"]["approval_inbox_path"])
    if inbox.exists() or inbox.is_symlink():
        raise LifecycleError("stale approval inbox prevents one-shot campaign birth")
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    server_work.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = evidence_dir / "lifecycle_receipt.json"
    result: dict[str, Any] = {
        "schema": LIFECYCLE_SCHEMA, "status": "PREBIRTH_SETUP_RUNNING",
        "candidate_sha256": candidate_sha256, "campaign_id": candidate["campaign_id"],
        "project_id": setup["project_id"], "project_workspace": setup["project_workspace"],
        "setup_campaign_receipt_sha256": setup["receipt_sha256"],
        "server_work": str(server_work), "evidence_dir": str(evidence_dir),
        "study_run_submissions": 0, "native_acceptance": "NOT_RUN",
    }
    _persist_run(receipt_path, result)
    runtime = None
    birth_budget: BirthBudget | None = None
    session: Mapping[str, Any] | None = None
    managed_runner: Any = None
    readmission: Mapping[str, Any] | None = None
    uncertain = False
    try:
        runtime_factory.preflight(candidate=candidate, evidence=evidence_dir,
                                  server_work=server_work)
        runtime = runtime_factory.create(candidate=candidate, evidence=evidence_dir,
                                         server_work=server_work)
        prebirth = runtime.prepare_project_and_server()
        if not isinstance(prebirth, Mapping) or prebirth.get("status") != "PREBIRTH_GATES_PASSED":
            raise LifecycleError("project/store/source prebirth verification failed")
        result["prebirth"] = dict(prebirth)
        _persist_run(receipt_path, result)
        birth_claim = _claim_science_birth_once(candidate)
        result["science_server_birth_claim"] = birth_claim
        _persist_run(receipt_path, result)
        server_birth = runtime.start_server()
        if not isinstance(server_birth, Mapping) or server_birth.get("birth_observed") is not True:
            raise LifecycleError("science Server Popen/listener birth is unverified")
        server_epoch = _validate_server_birth(server_birth)
        historical_server = setup.get("historic_setup_server_identity")
        budget_spec = candidate.get("new_science_server_budget")
        if (not isinstance(historical_server, Mapping) or
                type(historical_server.get("start_epoch_ms")) is not int or
                not isinstance(budget_spec, Mapping) or
                budget_spec.get("wall_seconds_including_all_readmission_approval_wait_science_cleanup") != 3600 or
                budget_spec.get("cleanup_reserve_seconds") != 90 or
                budget_spec.get("setup_server_budget_is_historical_and_never_reused") is not True):
            raise LifecycleError("candidate does not freeze the exact new-Server budget separate from historical setup")
        if (server_birth.get("pid") == historical_server.get("pid") and
                server_birth.get("birth") == historical_server.get("birth") and
                server_birth.get("start_epoch_ms") == historical_server.get("start_epoch_ms")):
            raise LifecycleError("science Server Popen identity is the retired historical setup Server")
        if server_epoch <= float(historical_server.get("birth_epoch_s", 0.0)):
            raise LifecycleError("science Server birth does not postdate the setup Server birth")
        birth_budget = BirthBudget(server_epoch, budget_s=3600.0, cleanup_reserve_s=90.0)
        budget_binder = getattr(runtime, "bind_birth_budget", None)
        if not callable(budget_binder):
            raise LifecycleError("live runtime cannot bind the one server-birth-derived immutable budget")
        budget_binder(birth_budget, server_birth)
        result.update({"status": "SCIENCE_SERVER_BIRTH_VERIFIED",
                       "server_birth": dict(server_birth),
                       "server_birth_budget": birth_budget.receipt(now_epoch_s=clock())})
        _persist_run(receipt_path, result)

        connected = runtime.connect_worker(birth_budget=birth_budget)
        if not isinstance(connected, Mapping):
            raise LifecycleError("public session.connect returned no structured identity")
        session = connected.get("session")
        worker_birth = connected.get("worker_birth")
        managed_runner = connected.get("managed_runner")
        if not isinstance(session, Mapping) or not isinstance(worker_birth, Mapping) or managed_runner is None:
            raise LifecycleError("live session/Worker Popen/managed runner handles are incomplete")
        _validate_birth_records(server_birth, worker_birth)
        if runtime.current_worker_epoch() != _session_epoch(session):
            raise LifecycleError("live public session identity differs from its Worker epoch")
        current_birth_reader = getattr(runtime, "current_worker_birth", None)
        if (not callable(current_birth_reader) or
                current_birth_reader() != _birth_identity(worker_birth)):
            raise LifecycleError("public session Worker birth differs from the exact managed Popen")
        if _session_epoch(session) == tuple(setup["historical_worker_epoch"]):
            raise LifecycleError("fresh science Worker reused the retired setup epoch")
        result.update({"status": "SCIENCE_WORKER_CONNECTED",
                       "session_identity": dict(session), "worker_birth": dict(worker_birth)})
        _persist_run(receipt_path, result)
        readmission_authorizer = getattr(runtime, "authorize_readmission_phase", None)
        if not callable(readmission_authorizer):
            raise LifecycleError("runtime cannot close bootstrap routes before readmission")
        readmission_authorizer()

        pins = candidate["source_sha256"]
        readmission = readmit(
            managed_runner,
            historical_setup_receipt_paths={
                key: Path(row["receipt_path"])
                for key, row in setup["historical_setup_slots"].items()},
            expected_historical_setup_receipt_sha256={
                key: row["receipt_sha256"]
                for key, row in setup["historical_setup_slots"].items()},
            expected_setup_fixture_source_sha256=pins["setup_fixture"],
            expected_setup_readback_source_sha256=pins["setup_readback"],
            transition_id=candidate["readmission_transition_id"],
            birth_budget=birth_budget,
            maximum_transition_wall_time_s=MAX_TRANSITION_WALL_S)
        if runtime.has_unknown_operations():
            uncertain = True
            raise LifecycleError("readmission left an UNKNOWN operation; approval and Study.run are forbidden")
        if (not isinstance(readmission, Mapping) or
                readmission.get("status") != "SCIENCE_WORKER_READMISSION_COMPLETE" or
                set(readmission.get("bindings", {})) != set(CAPTURE_KEY_ORDER)):
            raise LifecycleError("readmission did not prove all fourteen hash-bound artifacts on the new Worker")
        approval_input = _approval_input(
            candidate=candidate, readmission=readmission, managed_runner=managed_runner,
            birth_budget=birth_budget, server_birth=server_birth, worker_birth=worker_birth)
        approval_input_path = evidence_dir / "approval_input.json"
        approval_input_sha = _write_new(approval_input_path, approval_input)
        result.update({
            "status": "READMISSION_COMPLETE_AWAITING_EXTERNAL_APPROVAL",
            "readmission": {
                "transition_id": readmission["transition_id"],
                "manifest_path": str(readmission["manifest_path"]),
                "manifest_sha256": readmission["manifest_sha256"],
                "completed_model_loads": 14, "native_acceptance": "NOT_RUN"},
            "approval_input": {"path": str(approval_input_path),
                               "sha256": approval_input_sha,
                               "status": "PENDING_NOT_APPROVED"},
            "birth_budget_at_approval_wait_start": birth_budget.receipt(now_epoch_s=clock()),
        })
        _persist_run(receipt_path, result)

        envelope = approval_waiter(
            candidate=candidate, approval_input_path=approval_input_path,
            approval_input_sha256=approval_input_sha, birth_budget=birth_budget,
            clock=clock, sleep=sleep)
        if not isinstance(envelope, ApprovalEnvelope):
            raise LifecycleError("external approval waiter returned no validated one-shot approval")
        # Re-read the exact inbox; replacement of its identity/hash is a refusal.
        exact = _validate_approval_inbox(
            candidate=candidate, approval_input_path=approval_input_path,
            approval_input_sha256=approval_input_sha)
        if exact != envelope:
            raise LifecycleError("approval inbox changed after its first observation; no hash retry")
        if clock() >= birth_budget.deadline_epoch_s - birth_budget.cleanup_reserve_s:
            raise LifecycleError("approval arrived in reserved cleanup time; no Study.run may start")
        if (runtime.current_worker_epoch() != _session_epoch(session) or
                runtime.current_worker_birth() != _birth_identity(worker_birth)):
            raise LifecycleError("Worker was replaced while approval was pending")
        if runtime.has_unknown_operations():
            uncertain = True
            raise LifecycleError("an operation became UNKNOWN while approval was pending; no solve may start")
        runtime.authorize_science_phase()
        result.update({"status": "SCIENCE_EXECUTION_RUNNING",
                       "approval": {"path": str(envelope.approval_path),
                                    "sha256": envelope.expected_approval_sha256,
                                    "inbox_sha256": envelope.inbox_sha256},
                       "birth_budget_at_science_start": birth_budget.receipt(now_epoch_s=clock())})
        _persist_run(receipt_path, result)

        science = execute(
            managed_runner, readmission["bindings"],
            setup_receipt_paths=readmission["setup_receipt_paths"],
            historical_setup_receipt_sha256=readmission["historical_setup_receipt_sha256"],
            readmission_manifest_path=Path(readmission["manifest_path"]),
            expected_readmission_manifest_sha256=readmission["manifest_sha256"],
            readmission_transition_id=candidate["readmission_transition_id"],
            approval_path=envelope.approval_path,
            expected_approval_sha256=envelope.expected_approval_sha256,
            expected_study_run_source_sha256=pins["study_run"],
            expected_capture_source_sha256=pins["history_capture"],
            expected_science_executor_sha256=pins["science_executor"],
            expected_setup_fixture_source_sha256=pins["setup_fixture"],
            expected_setup_readback_source_sha256=pins["setup_readback"],
            birth_budget=birth_budget,
            solve_ledger_path=Path(setup["project_workspace"]) / "outputs" /
                "static_shape_sensitivity_study_runs.jsonl")
        result["science"] = dict(science)
        result["study_run_submissions"] = science.get("actual_study_run_submissions")
        result["status"] = science.get("status", "SCIENCE_FAIL_OR_UNKNOWN")
        _persist_run(receipt_path, result)
        uncertain = bool(runtime.has_unknown_operations())
    except BaseException as exc:
        uncertain = uncertain or bool(runtime.has_unknown_operations()) if runtime is not None else uncertain
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        result["status"] = ("PREBIRTH_REFUSED_NO_ENGINE" if birth_budget is None
                            else "FAIL_OR_UNKNOWN_NO_RETRY")
        _persist_run(receipt_path, result)

    if runtime is not None:
        try:
            cleanup = runtime.cleanup(
                session=session, birth_budget=birth_budget, allow_cleanup=not uncertain)
        except BaseException as exc:
            cleanup = {"status": "CLEANUP_UNVERIFIED",
                       "error": f"{type(exc).__name__}: {exc}"}
        result["cleanup"] = dict(cleanup) if isinstance(cleanup, Mapping) else {"status": "UNKNOWN"}
        cleanup_status = cleanup.get("status") if isinstance(cleanup, Mapping) else None
        verified_cleanups = {
            "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED",
            "EXACT_SERVER_STOPPED_NO_WORKER_BIRTH",
            "PREBIRTH_DAEMON_CLOSED_NO_SERVER",
        }
        if cleanup_status not in verified_cleanups:
            result["status"] = "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED"
        elif cleanup_status == "PREBIRTH_DAEMON_CLOSED_NO_SERVER":
            result["status"] = "PREBIRTH_REFUSED_NO_ENGINE"
        elif cleanup_status == "EXACT_SERVER_STOPPED_NO_WORKER_BIRTH":
            result["status"] = "PREWORKER_FAILURE_CLEANUP_VERIFIED"
        elif result.get("science", {}).get("status") == "ALL_14_CAPTURED_SENSITIVITY_GATES_PASS_REVIEW_REQUIRED":
            result["status"] = "SCIENCE_TERMINAL_CLEANUP_VERIFIED_REQUIRES_INDEPENDENT_REVIEW"
        elif "science" in result:
            result["status"] = "SCIENCE_FAILURE_CLEANUP_VERIFIED"
        result["native_acceptance"] = "NOT_ACCEPTED"
        result["science_server_birth_budget_final"] = (
            birth_budget.receipt(now_epoch_s=clock()) if birth_budget else None)
        _persist_run(receipt_path, result)
        if result["status"] == "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED":
            preserve = getattr(runtime, "preserve_handles", None)
            if callable(preserve):
                preserve()
    return result


def _validate_server_birth(server: Mapping[str, Any]) -> float:
    epoch = server.get("birth_epoch_s")
    if (type(server.get("pid")) is not int or server["pid"] <= 1 or
            not isinstance(server.get("birth"), str) or not server["birth"] or
            isinstance(epoch, bool) or type(epoch) not in (int, float) or
            not math.isfinite(float(epoch)) or epoch <= 0 or
            type(server.get("start_epoch_ms")) is not int or server["start_epoch_ms"] <= 0 or
            not math.isclose(float(epoch), server["start_epoch_ms"] / 1000.0,
                             rel_tol=0.0, abs_tol=1e-6)):
        raise LifecycleError("server Popen birth lacks exact PID, OS birth, or finite epoch")
    return float(epoch)


def _birth_identity(record: Mapping[str, Any]) -> tuple[int, str, int]:
    pid, birth, start_ms = record.get("pid"), record.get("birth"), record.get("start_epoch_ms")
    if type(pid) is not int or not isinstance(birth, str) or type(start_ms) is not int:
        raise LifecycleError("Worker Popen identity lacks pid/birth/start_epoch_ms")
    return pid, birth, start_ms


class ProductionRuntimeFactory:
    """Lazily instantiate the published public ControlDaemon/Worker routes."""

    @staticmethod
    def preflight(*, candidate: Mapping[str, Any], evidence: Path, server_work: Path) -> None:
        setup = candidate["setup_campaign"]
        budget_spec = candidate.get("new_science_server_budget")
        if (not isinstance(budget_spec, Mapping) or
                budget_spec.get("wall_seconds_including_all_readmission_approval_wait_science_cleanup") != 3600 or
                budget_spec.get("cleanup_reserve_seconds") != 90 or
                budget_spec.get("setup_server_budget_is_historical_and_never_reused") is not True):
            raise LifecycleError("science Server budget differs from the frozen 3600 s/90 s-reserve contract")
        if Path(setup["operation_store_path"]).resolve(strict=True) != Path(setup["operation_store_path"]):
            raise LifecycleError("historical OperationStore path changed or is aliased")
        if _store_identity(Path(setup["operation_store_path"])) != setup["operation_store_identity"]:
            raise LifecycleError("historical OperationStore inode changed since setup freeze")
        if Path(setup["project_workspace"]).resolve(strict=True) != Path(setup["project_workspace"]):
            raise LifecycleError("registered project workspace changed or is aliased")
        if (server_work.exists() or server_work.is_symlink() or
                server_work.parent.resolve(strict=True) != Path("/private/tmp").resolve(strict=True)):
            raise LifecycleError("science Server work directory is not new")
        if (Path.cwd().resolve(strict=True) != REPO.resolve(strict=True) or
                Path(sys.executable).resolve(strict=True) != Path(
                    "/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python").resolve(strict=True) or
                sys.version_info[:2] != (3, 12)):
            raise LifecycleError("production lifecycle requires the frozen source archive cwd and Python 3.12")
        inbox = Path(candidate["approval_protocol"]["approval_inbox_path"])
        if inbox.exists() or inbox.is_symlink():
            raise LifecycleError("approval inbox exists before new science Worker birth")
        dependency_pins = candidate.get("lifecycle_dependency_sha256")
        if not isinstance(dependency_pins, Mapping) or set(dependency_pins) != set(LIFECYCLE_DEPENDENCY_PATHS):
            raise LifecycleError("candidate omitted the complete lifecycle cleanup/import hash closure")
        origins: dict[str, dict[str, str]] = {}
        for name, module_name in LIFECYCLE_MODULES.items():
            module = importlib.import_module(module_name)
            origin = getattr(module, "__file__", None)
            expected_path = REPO / LIFECYCLE_DEPENDENCY_PATHS[name]
            if (not isinstance(origin, str) or Path(origin).resolve(strict=True) !=
                    expected_path.resolve(strict=True) or expected_path.is_symlink() or
                    sha256_file(expected_path) != _sha(dependency_pins.get(name),
                                                       f"lifecycle dependency pin {name}")):
                raise LifecycleError(f"lifecycle import/hash closure mismatch before birth: {name}")
            origins[name] = {"module": module_name, "path": str(expected_path),
                             "sha256": dependency_pins[name]}
        if (Path(setup["project_workspace"]).resolve(strict=True) != Path(setup["project_workspace"]) or
                _store_identity(Path(setup["operation_store_path"])) != setup["operation_store_identity"]):
            raise LifecycleError("historic registered project or OperationStore changed before birth")
        from tools.run_native_w24_static_shape_setup_campaign import (
            _verify_execution_environment, fresh_scoped_inventory,
        )
        environment = _verify_execution_environment(server_work, REPO)
        inventory = fresh_scoped_inventory()
        _write_new(evidence / "prebirth_inventory.json", inventory)
        if inventory.get("fresh_quiescent_for_scope") is not True:
            raise LifecycleError("fresh scoped COMSOL/Worker/listener inventory unavailable or non-quiescent")
        _write_new(evidence / "dependency_preflight.json", {
            "status": "PREBIRTH_DEPENDENCY_PREFLIGHT_PASS",
            "modules": origins, "source_hashes": dict(dependency_pins),
            "environment": environment, "python_executable": str(Path(sys.executable).resolve()),
            "python_version": sys.version.split()[0],
        })

    @staticmethod
    def create(*, candidate: Mapping[str, Any], evidence: Path,
               server_work: Path) -> "ProductionSensitivityRuntime":
        return ProductionSensitivityRuntime(candidate, evidence, server_work)


class ProductionSensitivityRuntime:
    def __init__(self, candidate: Mapping[str, Any], evidence: Path, server_work: Path):
        self.candidate = candidate
        self.setup = candidate["setup_campaign"]
        self.evidence = evidence
        self.server_work = server_work
        self.run_dir = evidence / "runtime"
        self.run_dir.mkdir(mode=0o700)
        self.events = self.run_dir / "events.jsonl"
        self.events.write_bytes(b"")
        self.server: Any = None
        self.daemon: Any = None
        self.monitor: Any = None
        self.session: Mapping[str, Any] | None = None
        self.worker_birth: Mapping[str, Any] | None = None
        self.managed_runner: Any = None
        self.birth_budget: BirthBudget | None = None
        self._unknown = False
        self._phase = "bootstrap"
        self._worker: Any = None
        self._worker_process: Any = None
        self._dispatch_daemon: _AdmissionDaemon | None = None
        self._bootstrap_model_create_count = 0
        self._route_count = 0

    def prepare_project_and_server(self) -> Mapping[str, Any]:
        from comsol_mcp._control_daemon import ControlDaemon
        from tools.run_native_w24_static_shape_setup_campaign import W24StaticShapeSetupServer
        self.daemon = ControlDaemon(Path(self.setup["mcp_home"]),
                                    project_root=Path(self.setup["project_root"]))
        project = self.daemon.project_authority.get_project(self.setup["project_id"])
        if (not isinstance(project, Mapping) or project.get("project_id") != self.setup["project_id"] or
                Path(str(project.get("workspace", ""))).resolve(strict=True) !=
                Path(self.setup["project_workspace"]).resolve(strict=True)):
            raise LifecycleError("ControlDaemon did not recover the exact registered project")
        store_path = Path(self.daemon.store.path).resolve(strict=True)
        if store_path != Path(self.setup["operation_store_path"]).resolve(strict=True):
            raise LifecycleError("lifecycle attached to a new OperationStore instead of the setup authority")
        self.store_identity = _store_identity(store_path)
        if self.store_identity != self.setup["operation_store_identity"]:
            raise LifecycleError("existing OperationStore inode changed before readmission")
        self.server_work.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.server = W24StaticShapeSetupServer(self.server_work, self.run_dir,
                                                event_log=self.events)
        return {"status": "PREBIRTH_GATES_PASSED",
                "project_id": self.setup["project_id"],
                "project_workspace": self.setup["project_workspace"],
                "operation_store_path": str(store_path),
                "operation_store_identity": self.store_identity,
                "server_work": str(self.server_work),
                "prior_setup_worker_epoch": self.setup["historical_worker_epoch"]}

    def start_server(self) -> Mapping[str, Any]:
        from tools.run_native_w24_static_shape_setup_campaign import (
            _start_rss_monitor, fresh_scoped_inventory,
        )
        box: dict[str, Any] = {}
        worker_root = Path(self.setup["mcp_home"]) / "session-runtime-state/sessions"
        self.server.on_birth = lambda proc, identity: _start_rss_monitor(
            box, proc, identity, worker_root, self.events)
        shadow = self.server.prepare_shadow()
        # Shadow preparation and launcher inspection can take long enough to
        # stale the earlier inventory. Recheck directly at the Popen gate.
        inventory = fresh_scoped_inventory()
        _write_new(self.evidence / "birth_inventory.json", inventory)
        if inventory.get("fresh_quiescent_for_scope") is not True:
            raise LifecycleError("birth-time scoped COMSOL/Worker/listener inventory is unavailable or non-quiescent")
        listener = self.server.start_and_verify_listener()
        self.monitor = box.get("monitor")
        if self.monitor is None:
            self._unknown = True
            raise LifecycleError("server Popen started without an active RSS monitor")
        jvm = self.server.require_server_java_options()
        epoch_ms = self.server.server_snapshot.get("start_epoch_ms") if self.server.server_snapshot else None
        if type(epoch_ms) is not int or epoch_ms <= 0:
            self._unknown = True
            raise LifecycleError("server birth lacks the exact OS Popen epoch")
        self.server_birth = {
            "birth_observed": True, "pid": self.server.proc.pid,
            "birth": self.server.server_snapshot.get("birth"),
            "command_sha256": self.server.server_snapshot.get("command_sha256"),
            "start_epoch_ms": epoch_ms, "birth_epoch_s": epoch_ms / 1000.0,
            "port": self.server.port, "listener": listener,
            "actual_server_jvm_options": jvm, "shadow_receipt": shadow,
        }
        return dict(self.server_birth)

    def bind_birth_budget(self, birth_budget: BirthBudget,
                          server_birth: Mapping[str, Any]) -> None:
        if (self.server_birth is None or dict(server_birth) != self.server_birth or
                not isinstance(birth_budget, BirthBudget) or
                not math.isclose(birth_budget.birth_epoch_s,
                                 self.server_birth["birth_epoch_s"], rel_tol=0.0, abs_tol=1e-6) or
                birth_budget.budget_s != 3600.0 or birth_budget.cleanup_reserve_s != 90.0):
            raise LifecycleError("runtime rejected a replaced or non-server-derived immutable budget")
        if self.birth_budget is not None:
            raise LifecycleError("birth budget is immutable and may be bound only once")
        self.birth_budget = birth_budget

    def connect_worker(self, *, birth_budget: BirthBudget) -> Mapping[str, Any]:
        from comsol_mcp._runtime_installation import runtime_id_for_root
        from tools.run_native_w24_static_shape_setup_campaign import (
            _direct_dispatch, _model_create_binding, _request_ids,
            _require_connected_inspect, _session_identity,
            _verify_worker_java_options, _worker_identity_from_context,
            _process_start_epoch_for_identity,
        )
        if (self.server is None or self.server.proc is None or self.server.port is None or
                self.birth_budget is not birth_budget):
            raise LifecycleError("live server/handles absent or birth budget was replaced")
        try:
            self._dispatch_daemon = _AdmissionDaemon(self, self.daemon)
            connect = _direct_dispatch(
                self._dispatch_daemon, self.run_dir, operation="session.connect",
                arguments={"runtime_id": runtime_id_for_root(
                    Path("/Applications/COMSOL64/Multiphysics")),
                    "endpoint": {"host": "127.0.0.1", "port": self.server.port}},
                project_id=self.setup["project_id"], request_label="science_session_connect",
                timeout_seconds=min(120.0, birth_budget.rpc_timeout_s(120.0)))
        except BaseException as exc:
            if not _caused_by_predispatch_refusal(exc):
                self._unknown = True
            raise
        try:
            session = _session_identity(connect, project_id=self.setup["project_id"],
                                        expected_port=self.server.port)
        except BaseException:
            if connect.get("success") is True or connect.get("execution_state_unknown") is True:
                self._unknown = True
            raise
        self.session = session
        try:
            _context, worker, identity, worker_state = _worker_identity_from_context(
                self.daemon, self.setup["project_id"], session)
        except BaseException:
            self._unknown = True
            raise
        worker_epoch_ms = _process_start_epoch_for_identity(identity, worker)
        self.monitor.bind_worker(int(identity["pid"]), worker_epoch_ms)
        worker_birth = {
            "pid": identity["pid"], "birth": identity["birth"],
            "command_sha256": identity["command_sha256"],
            "start_epoch_ms": worker_epoch_ms, "birth_epoch_s": worker_epoch_ms / 1000.0,
            "worker_state_dir": str(worker_state),
            "actual_worker_java_options": _verify_worker_java_options(worker_state),
        }
        self.worker_birth = worker_birth
        self._worker = worker
        self._worker_process = getattr(worker, "_process", None)
        sample = self.monitor.sample_once()
        if sample.get("status") != "SAMPLED_WITHIN_THRESHOLD" or self.monitor.stop_reason:
            raise LifecycleError("exact Worker RSS identity/readback blocked admission")
        inspect = _direct_dispatch(
            self._dispatch_daemon, self.run_dir, operation="session.inspect",
            arguments={"session_id": session["session_id"]},
            project_id=self.setup["project_id"], session_id=session["session_id"],
            request_label="science_session_inspect_connected",
            timeout_seconds=min(30.0, birth_budget.rpc_timeout_s(30.0)))
        _require_connected_inspect(inspect, session)
        request_id, key = _request_ids("science-parent-model-create")
        parent = _direct_dispatch(
            self._dispatch_daemon, self.run_dir, operation="model_create",
            arguments={"name": "w24_static_shape_sensitivity_readmission_parent"},
            project_id=self.setup["project_id"], session_id=session["session_id"],
            request_label="science_parent_model_create",
            timeout_seconds=min(120.0, birth_budget.rpc_timeout_s(120.0)),
            request_id=request_id, idempotency_key=key)
        parent_binding = _model_create_binding(parent, project_id=self.setup["project_id"],
                                               session=session)
        from tools import run_native_w24_cure_preflight as setup_adapter
        from tools.run_native_w24_static_shape_setup import StaticShapeManagedRunner
        runner_evidence = (Path(self.setup["project_workspace"]) / "outputs" /
                           f"w24_sensitivity_lifecycle_{self.candidate['readmission_transition_id']}")
        outputs = runner_evidence.parent
        if (outputs.is_symlink() or not outputs.is_dir() or
                outputs.resolve(strict=True) != outputs.resolve() or
                runner_evidence.exists() or runner_evidence.is_symlink()):
            raise LifecycleError("managed readmission evidence must be a new registered-project outputs child")
        runner_evidence.mkdir(mode=0o700)
        self.managed_runner = StaticShapeManagedRunner(
            daemon=self._dispatch_daemon, project_id=self.setup["project_id"],
            project_workspace=Path(self.setup["project_workspace"]),
            project_create_response=self.setup["project_create_response"],
            parent_binding=parent_binding,
            source_manifest=self.setup["project_java_source_manifest"],
            setup_runner=setup_adapter, evidence_dir=runner_evidence,
            timeout_s=min(120.0, birth_budget.rpc_timeout_s(120.0)))
        return {"session": dict(session), "worker_birth": worker_birth,
                "managed_runner": self.managed_runner}

    def current_worker_epoch(self) -> tuple[str, int, str]:
        if self.daemon is None or self.session is None:
            raise LifecycleError("no public session handles are available; process restart cannot resume")
        context = self.daemon.session_registry.get(self.setup["project_id"], self.session["session_id"])
        if (getattr(context, "project_id", None) != self.setup["project_id"] or
                getattr(context, "session_id", None) != self.session["session_id"] or
                getattr(context, "worker_instance_id", None) != self.session["worker_instance_id"] or
                getattr(context, "worker_epoch", None) != self.session["worker_epoch"]):
            raise LifecycleError("live SessionRuntimeContext was replaced or changed epoch")
        if self._worker is not None and getattr(context, "worker", None) is not self._worker:
            raise LifecycleError("public SessionRuntimeContext now points at another Worker object")
        return _session_epoch(self.session)

    def current_worker_birth(self) -> tuple[int, str, int]:
        self.current_worker_epoch()
        from comsol_mcp._g2_isolation import _process_snapshot
        from comsol_mcp._platform_process import process_identity
        process = self._worker_process
        pid = getattr(process, "pid", None)
        if (process is None or type(pid) is not int or pid <= 1 or
                getattr(self._worker, "_process", None) is not process or process.poll() is not None):
            raise LifecycleError("exact approved Worker Popen handle is absent or no longer live")
        snapshot = _process_snapshot(pid)
        observed = process_identity(pid)
        birth = snapshot.get("birth") if isinstance(snapshot, Mapping) else None
        start_ms = observed.get("start_epoch_ms") if isinstance(observed, Mapping) else None
        if (not isinstance(snapshot, Mapping) or snapshot.get("pid") != pid or
                not isinstance(birth, str) or not birth or
                not isinstance(observed, Mapping) or observed.get("alive") is not True or
                type(start_ms) is not int or start_ms <= 0):
            raise LifecycleError("current Worker birth identity cannot be verified")
        return pid, birth, start_ms

    def authorize_readmission_phase(self) -> None:
        if self._phase != "bootstrap" or self._unknown or self.session is None:
            raise LifecycleError("readmission cannot follow an unknown or unconnected bootstrap")
        self._phase = "readmission"

    def authorize_science_phase(self) -> None:
        if (self._phase != "readmission" or self._unknown or self.monitor is None or
                self.monitor.stop_reason is not None):
            raise LifecycleError("science phase cannot follow an unproven or stopped readmission")
        self.current_worker_epoch()
        self.current_worker_birth()
        self._phase = "science"

    def _admit_route(self, request: Mapping[str, Any]) -> None:
        if self._unknown:
            raise _LifecycleDispatchRejected("an earlier operation is UNKNOWN; no further route may dispatch")
        if self.birth_budget is None or self.monitor is None:
            raise _LifecycleDispatchRejected("exact Server birth budget or RSS monitor is unavailable")
        now = time.time()
        try:
            remaining = self.birth_budget.rpc_timeout_s(120.0, now_epoch_s=now)
        except BaseException as exc:
            raise _LifecycleDispatchRejected(str(exc)) from exc
        sample = self.monitor.last_sample
        if (self.monitor.stop_reason or not isinstance(sample, Mapping) or
                sample.get("status") != "SAMPLED_WITHIN_THRESHOLD" or
                self.monitor.last_sample_monotonic is None or
                time.monotonic() - self.monitor.last_sample_monotonic > 3.5):
            raise _LifecycleDispatchRejected("fresh exact RSS sample is absent, stale, over threshold, or failed")
        operation = request.get("operation")
        arguments = request.get("arguments", {})
        execution = request.get("execution", {})
        if not isinstance(arguments, Mapping) or not isinstance(execution, Mapping):
            raise _LifecycleDispatchRejected("public ControlDaemon request envelope is malformed")
        if execution.get("project_id") != self.setup["project_id"]:
            raise _LifecycleDispatchRejected("route project id differs from the registered setup project")
        expected_session = self.session.get("session_id") if self.session else None
        if self._phase != "bootstrap" and execution.get("session_id") != expected_session:
            raise _LifecycleDispatchRejected("route session differs from the exact connected Worker")
        if self._phase == "bootstrap":
            if operation not in {"session.connect", "session.inspect", "model_create"}:
                raise _LifecycleDispatchRejected(f"bootstrap route fence rejected {operation!r}")
            if operation == "model_create" and self._bootstrap_model_create_count != 0:
                raise _LifecycleDispatchRejected("bootstrap permits one parent model_create only")
        elif self._phase == "readmission":
            if operation not in {"model_load", "model.inspect", "operation_call"}:
                raise _LifecycleDispatchRejected(f"readmission route fence rejected {operation!r}")
            if operation == "model.inspect" and arguments != {"detail": "summary"}:
                raise _LifecycleDispatchRejected("readmission model.inspect is not the public summary route")
            if operation == "operation_call":
                self._validate_java_route(arguments, phase="readmission")
        elif self._phase == "science":
            if operation not in {"model.inspect", "operation_call"}:
                raise _LifecycleDispatchRejected(f"science route fence rejected {operation!r}")
            if operation == "model.inspect" and arguments != {"detail": "summary"}:
                raise _LifecycleDispatchRejected("science model.inspect is not the public summary route")
            if operation == "operation_call":
                self._validate_java_route(arguments, phase="science")
        else:
            raise _LifecycleDispatchRejected("lifecycle phase is closed")
        if operation == "model_create":
            self._bootstrap_model_create_count += 1
        self._route_count += 1
        try:
            from tools.run_native_w24_static_shape_setup_campaign import _append
            _append(self.events, {
                "event": "lifecycle_route_admitted", "phase": self._phase,
                "operation": operation, "route_index": self._route_count,
                "request_id": execution.get("request_id"),
                "idempotency_key": execution.get("idempotency_key"),
                "remaining_usable_budget_seconds": remaining, "at_epoch_s": now,
            })
        except BaseException as exc:
            raise _LifecycleDispatchRejected("route admission evidence could not be persisted") from exc

    @staticmethod
    def _validate_java_route(arguments: Mapping[str, Any], *, phase: str) -> None:
        if arguments.get("operation_id") != "code.execute_java":
            raise _LifecycleDispatchRejected("only public code.execute_java routes are admitted")
        body = arguments.get("arguments")
        if (not isinstance(body, Mapping) or body.get("mode") != "trusted" or
                not isinstance(body.get("arguments"), Mapping)):
            raise _LifecycleDispatchRejected("trusted Java operation envelope is malformed")
        source, entrypoint, nested = body.get("source_artifact"), body.get("entrypoint"), body["arguments"]
        if phase == "readmission":
            if (source != "W24StaticShapeReadback.java" or
                    entrypoint != "W24StaticShapeReadback#run" or nested.get("action") != "readback" or
                    set(nested) != {"action", "expected_configuration_id"}):
                raise _LifecycleDispatchRejected("readmission permits only the approved configuration readback")
        elif entrypoint == "W24StaticShapeSensitivityStudyRun#run":
            expected = {"action", "sensitivity_campaign", "configuration_id", "case_id",
                        "expected_model_tag", "submission_index", "workspace_path", "ledger_path",
                        "save_path", "slot_idempotency_key", "approval_sha256", "campaign_id",
                        "ordered_slots", "slot_history_sha256"}
            if (source != "W24StaticShapeSensitivityStudyRun.java" or nested.get("action") != "run" or
                    set(nested) != expected):
                raise _LifecycleDispatchRejected("Study.run payload differs from the frozen Java contract")
        elif entrypoint == "W24StaticShapeHistoryCapture#run":
            expected = {"action", "case_id", "workspace_path", "path"}
            if source != "W24StaticShapeHistoryCapture.java" or nested.get("action") != "capture":
                raise _LifecycleDispatchRejected("history capture payload differs from the frozen Java contract")
            if set(nested) not in {frozenset(expected), frozenset(expected | {"configuration_id"})}:
                raise _LifecycleDispatchRejected("history capture arguments contain missing or unreviewed fields")
        else:
            raise _LifecycleDispatchRejected("science Java entrypoint is outside Study.run/history capture allowlist")

    def _observe_route_result(self, request: Mapping[str, Any], response: Any) -> None:
        operation = request.get("operation")
        data = response.get("data") if isinstance(response, Mapping) else None
        worker = data.get("worker") if isinstance(data, Mapping) else None
        worker_routed = operation in {"model_load", "operation_call", "model_create"}
        if (not isinstance(response, Mapping) or response.get("execution_state_unknown") is True or
                (worker_routed and isinstance(worker, Mapping) and worker.get("status") not in {
                    "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}) or
                (operation in {"model_load", "operation_call"} and not isinstance(worker, Mapping)) or
                (operation == "session.connect" and isinstance(data, Mapping) and
                 response.get("success") is not True and
                 (data.get("worker_birth_performed") is not False or
                  data.get("engine_dispatched") is not False))):
            self._unknown = True
        try:
            from tools.run_native_w24_static_shape_setup_campaign import _append
            _append(self.events, {
                "event": "lifecycle_route_returned", "phase": self._phase,
                "operation": operation,
                "success": response.get("success") is True if isinstance(response, Mapping) else False,
                "execution_state_unknown": response.get("execution_state_unknown") is True
                    if isinstance(response, Mapping) else True,
                "worker_status": worker.get("status") if isinstance(worker, Mapping) else None,
                "outcome_unknown": self._unknown,
            })
        except BaseException:
            self._unknown = True

    def preserve_handles(self) -> None:
        from tools.run_native_w24_static_shape_setup_campaign import (
            _append, _hold_forever_with_handles,
        )
        hold = {
            "schema": "W24_STATIC_SHAPE_LIFECYCLE_UNKNOWN_HOLD_V1",
            "status": "UNKNOWN_OR_ACTIVE_HANDLES_RETAINED_NO_SIGNAL_NO_RESTART",
            "candidate_sha256": self.candidate.get("candidate_sha256"),
            "campaign_id": self.candidate.get("campaign_id"),
            "server_pid": getattr(getattr(self.server, "proc", None), "pid", None),
            "server_birth": getattr(self.server, "server_birth", None),
            "worker_birth": dict(self.worker_birth) if self.worker_birth else None,
            "session_id": self.session.get("session_id") if self.session else None,
            "unknown_operation_seen": self._unknown,
            "rss_stop_reason": getattr(self.monitor, "stop_reason", None),
            "signal_sent": False, "automatic_restart": False,
            "recorded_at_epoch_s": time.time(),
        }
        _write_new(self.run_dir / "unknown_handle_hold.json", hold)
        _append(self.events, {"event": "unknown_handle_hold_entered",
                             "receipt": str(self.run_dir / "unknown_handle_hold.json"),
                             "at_epoch_s": time.time()})
        if self.monitor is None:
            while True:
                time.sleep(2.0)
        _hold_forever_with_handles(self.monitor)

    def cleanup(self, *, session: Mapping[str, Any] | None,
                birth_budget: BirthBudget | None, allow_cleanup: bool) -> Mapping[str, Any]:
        if self.server is None or self.server.proc is None:
            if self.daemon is not None:
                try:
                    self.daemon.close()
                except BaseException as exc:
                    return {"status": "PREBIRTH_DAEMON_CLOSE_UNVERIFIED",
                            "error": f"{type(exc).__name__}: {exc}"}
            return {"status": "PREBIRTH_DAEMON_CLOSED_NO_SERVER"}
        exact_session = session or self.session
        if not allow_cleanup or self.daemon is None or self.monitor is None:
            return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED",
                    "server_pid": self.server.proc.pid,
                    "worker_pid": self.worker_birth.get("pid") if self.worker_birth else None,
                    "signal_sent": False}
        if exact_session is None:
            if (self._unknown or self.monitor.expected_worker_pid is not None or
                    self.monitor.worker_required_live):
                return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED",
                        "server_pid": self.server.proc.pid,
                        "worker_pid": self.worker_birth.get("pid") if self.worker_birth else None,
                        "signal_sent": False}
            from tools.run_native_w24_static_shape_setup_campaign import _stop_exact_server
            sample = self.monitor.sample_once()
            worker_rows = [row for row in sample.get("processes", [])
                           if isinstance(row, Mapping) and row.get("kind") == "worker"]
            if sample.get("status") != "SAMPLED_WITHIN_THRESHOLD" or worker_rows:
                return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED",
                        "server_pid": self.server.proc.pid,
                        "no_worker_birth_proven": not worker_rows,
                        "signal_sent": False}
            cleanup_remaining = (birth_budget.deadline_epoch_s - time.time()
                                 if birth_budget else 30.0)
            if cleanup_remaining <= 1.0:
                return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED",
                        "server_pid": self.server.proc.pid,
                        "reason": "birth budget expired before exact no-Worker server cleanup",
                        "signal_sent": False}
            self.monitor.stop()
            self.daemon.close()
            identity = self.server.process_identity
            if not isinstance(identity, Mapping) or type(self.server.port) is not int:
                return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED",
                        "server_pid": self.server.proc.pid, "signal_sent": False}
            stopped = _stop_exact_server(self.server, {
                "pid": self.server.proc.pid,
                "start_epoch_ms": identity.get("start_epoch_ms"),
                "birth": identity.get("birth"),
                "command_sha256": identity.get("command_sha256"),
            }, int(self.server.port), timeout_seconds=min(30.0, max(
                1.0, cleanup_remaining)))
            if getattr(self.server, "_server_log_handle", None) is not None:
                self.server._server_log_handle.close()
            _write_new(self.run_dir / "server_stop_no_worker_receipt.json", stopped)
            return {"status": "EXACT_SERVER_STOPPED_NO_WORKER_BIRTH",
                    "server_stop_receipt_sha256": sha256_file(
                        self.run_dir / "server_stop_no_worker_receipt.json"),
                    "no_worker_birth_proven": True, "signal_sent": stopped.get("signal") == "SIGTERM"}
        from tools.run_native_w24_static_shape_setup_campaign import _retire_worker_and_server
        return _retire_worker_and_server(
            daemon=self.daemon, server=self.server, monitor=self.monitor,
            project_id=self.setup["project_id"], session=exact_session, run_dir=self.run_dir,
            deadline_monotonic=self.server.birth_monotonic +
                (birth_budget.budget_s if birth_budget else 3600.0))


def run_sensitivity_lifecycle(*, candidate_path: Path, expected_candidate_sha256: str,
                              evidence_dir: Path, server_work: Path,
                              approval_waiter: Callable[..., ApprovalEnvelope],
                              runtime_factory: Any = ProductionRuntimeFactory,
                              readmit: Callable[..., Mapping[str, Any]] | None = None,
                              execute: Callable[..., Mapping[str, Any]] | None = None,
                              clock: Callable[[], float] = time.time,
                              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    candidate = load_candidate(candidate_path, expected_candidate_sha256)
    return run_reviewed_lifecycle(
        candidate=candidate, candidate_sha256=expected_candidate_sha256,
        evidence_dir=evidence_dir, server_work=server_work,
        runtime_factory=runtime_factory, approval_waiter=approval_waiter,
        readmit=(readmit if readmit is not None else readmit_sensitivity_setup_artifacts),
        execute=(execute if execute is not None else execute_sensitivity_campaign),
        clock=clock, sleep=sleep)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="offline setup/artifact/source validation")
    prepare.add_argument("--setup-campaign-receipt", required=True, type=Path)
    prepare.add_argument("--expected-setup-campaign-sha256", required=True)
    prepare.add_argument("--candidate-out", required=True, type=Path)
    prepare.add_argument("--campaign-id", required=True)
    prepare.add_argument("--readmission-transition-id", required=True)
    prepare.add_argument("--approval-root", required=True, type=Path)
    prepare.add_argument("--approval-inbox", required=True, type=Path)
    execute = commands.add_parser("execute", help="one live same-process readmission/approval/science lifecycle")
    execute.add_argument("--candidate", required=True, type=Path)
    execute.add_argument("--expected-candidate-sha256", required=True)
    execute.add_argument("--evidence", required=True, type=Path)
    execute.add_argument("--server-work", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_candidate(
                setup_campaign_receipt=args.setup_campaign_receipt,
                expected_setup_campaign_receipt_sha256=args.expected_setup_campaign_sha256,
                output_path=args.candidate_out, campaign_id=args.campaign_id,
                readmission_transition_id=args.readmission_transition_id,
                approval_inbox_path=args.approval_inbox, approval_root=args.approval_root)
        else:
            result = run_sensitivity_lifecycle(
                candidate_path=args.candidate,
                expected_candidate_sha256=args.expected_candidate_sha256,
                evidence_dir=args.evidence, server_work=args.server_work,
                approval_waiter=wait_for_external_approval)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
        if result.get("status") == "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN":
            return 0
        if result.get("status") == "SCIENCE_TERMINAL_CLEANUP_VERIFIED_REQUIRES_INDEPENDENT_REVIEW":
            return 0
        return 2
    except BaseException as exc:
        print(json.dumps({"status": "CANDIDATE_GATE_FAILED",
                          "error_type": type(exc).__name__, "error": str(exc),
                          "native_acceptance": "NOT_RUN"},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
