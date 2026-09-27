"""Managed, approval-pinned executor for the fourteen W24 shape sensitivity runs.

This module never starts COMSOL. It submits only through the registered
ControlDaemon/OperationStore path and refuses all work unless one separately
approved candidate pins the full seven-configuration, flat/step matrix, its
original setup receipts, every original ModelRef/revision, all source hashes,
and explicit output limits. An uncertain slot stops the entire campaign.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from tools.run_native_w24_cure_science import BirthBudget, CampaignError, ManagedModelBinding
from tools.run_native_w24_cure_science import _model_ref_matches_worker_epoch
from tools.run_native_w24_static_shape_setup import (
    STATIC_SHAPE_FIXTURE,
    STATIC_SHAPE_READBACK,
    StaticShapeManagedRunner,
    _append_jsonl_fsynced,
    _project_path,
    _write_json_fsynced,
)
from tools.run_native_w24_static_shape_science import (
    _binding_record,
    _dispatch_solve_slot,
    _operation_store_path,
    sha256_file,
)
from tools.w24_static_shape_capture import CAPTURE_SOURCE, capture_static_shape_history
from tools.w24_static_shape_sensitivity import (
    SENSITIVITY_CASE_ORDER,
    SENSITIVITY_CAPTURE_PROTOCOL,
    build_sensitivity_campaign_plan,
    sensitivity_configurations,
    sensitivity_submission_slots,
    sensitivity_capture_resource_estimate,
)
from tools.w24_static_shape_sensitivity_comparison import (
    compare_sensitivity_campaign,
    compare_sensitivity_case_variant,
)


REPO = Path(__file__).resolve().parents[1]
STUDY_RUN_SOURCE = REPO / "tools/java/W24StaticShapeSensitivityStudyRun.java"
SETUP_FIXTURE_SOURCE = STATIC_SHAPE_FIXTURE
SETUP_READBACK_SOURCE = STATIC_SHAPE_READBACK
SCIENCE_EXECUTOR_SOURCE = Path(__file__).resolve()
APPROVAL_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_SCIENCE_APPROVAL_V3"
READMISSION_SCHEMA = "W24_STATIC_SHAPE_SCIENCE_WORKER_READMISSION_V2"
READMISSION_MANIFEST_SCHEMA = "W24_STATIC_SHAPE_SCIENCE_WORKER_READMISSION_MANIFEST_V2"
READMISSION_REQUEST_SCHEMA = "W24_STATIC_SHAPE_READMISSION_REQUEST_EVIDENCE_V1"
READMISSION_STORE_SCHEMA = "W24_STATIC_SHAPE_READMISSION_OPERATION_STORE_EVIDENCE_V1"
CONTROL_EXECUTION_ID_FIELDS = (
    "request_id", "idempotency_key", "request_hash", "operation_id", "job_id")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAPTURE_KEY_ORDER = tuple(
    f"{configuration.configuration_id}:{case_id}"
    for configuration in sensitivity_configurations()
    for case_id in SENSITIVITY_CASE_ORDER)


def sensitivity_binding_key(configuration_id: str, case_id: str) -> str:
    if configuration_id not in {row.configuration_id for row in sensitivity_configurations()}:
        raise CampaignError("configuration_id is not one of the seven preregistered W24 sensitivity rows")
    if case_id not in SENSITIVITY_CASE_ORDER:
        raise CampaignError("case_id must be exactly flat or step")
    return f"{configuration_id}:{case_id}"


def sensitivity_slot_identity(
    project_id: str,
    binding: ManagedModelBinding,
    configuration_id: str,
    case_id: str,
) -> tuple[str, str]:
    """Create a stable identity for one exact project/config/model/case slot."""
    key = sensitivity_binding_key(configuration_id, case_id)
    if not isinstance(binding, ManagedModelBinding) or binding.project_id != project_id:
        raise CampaignError(f"{key} is not a project-bound ManagedModelBinding")
    identity = {
        "schema": "W24_STATIC_SHAPE_SENSITIVITY_SLOT_V1",
        "project_id": project_id,
        "configuration_id": configuration_id,
        "case_id": case_id,
        "model_binding": binding.as_record(),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    suffix = hashlib.sha256(encoded).hexdigest()
    return (f"w24-static-shape-slot-{suffix}",
            f"w24-static-shape-request-{suffix}")


def _model_epoch(binding: ManagedModelBinding) -> tuple[str, int, str]:
    record = _binding_record(binding, project_id=binding.project_id, case_id="worker-epoch")
    ref = record["model_ref"]
    return record["session_id"], ref["generation"], ref["server_instance_id"]


def _birth_budget_identity(value: Any, *, label: str) -> dict[str, float]:
    """Return the immutable time budget fields, rejecting malformed/resized budgets."""
    raw = value.receipt() if isinstance(value, BirthBudget) else value
    if not isinstance(raw, Mapping):
        raise CampaignError(f"{label} is missing the persisted Worker birth budget")
    fields = ("birth_epoch_s", "deadline_epoch_s", "budget_s", "cleanup_reserve_s")
    normalized: dict[str, float] = {}
    for field in fields:
        item = raw.get(field)
        if isinstance(item, bool) or type(item) not in (int, float) or not math.isfinite(float(item)):
            raise CampaignError(f"{label} has a missing or non-finite {field}")
        normalized[field] = float(item)
    birth = normalized["birth_epoch_s"]
    deadline = normalized["deadline_epoch_s"]
    budget = normalized["budget_s"]
    reserve = normalized["cleanup_reserve_s"]
    if (birth <= 0 or budget <= 0 or reserve < 0 or reserve >= budget or
            not math.isclose(deadline, birth + budget, rel_tol=0.0, abs_tol=1e-6)):
        raise CampaignError(f"{label} has an inconsistent deadline, duration, or cleanup reserve")
    return normalized


def _birth_budget_binding_record(
    birth_budget: BirthBudget,
    worker_epoch: tuple[str, int, str],
) -> dict[str, Any]:
    """Bind one immutable budget identity to the exact registered Worker epoch."""
    if (not isinstance(worker_epoch, tuple) or len(worker_epoch) != 3 or
            not isinstance(worker_epoch[0], str) or not worker_epoch[0] or
            isinstance(worker_epoch[1], bool) or type(worker_epoch[1]) is not int or
            worker_epoch[1] < 1 or not isinstance(worker_epoch[2], str) or not worker_epoch[2]):
        raise CampaignError("Worker birth budget cannot bind an incomplete science Worker epoch")
    return {
        "worker_epoch": {
            "session_id": worker_epoch[0],
            "generation": worker_epoch[1],
            "server_instance_id": worker_epoch[2],
        },
        "budget": _birth_budget_identity(birth_budget, label="supplied Worker birth budget"),
    }


def _validate_birth_budget_observation(
    value: Any,
    *,
    expected_binding: Mapping[str, Any],
    label: str,
) -> float:
    """Check a persisted receipt observation against one immutable budget and return its epoch."""
    if not isinstance(value, Mapping):
        raise CampaignError(f"{label} is missing its Worker birth budget observation")
    identity = _birth_budget_identity(value, label=label)
    if identity != dict(expected_binding.get("budget", {})):
        raise CampaignError(f"{label} changed the Worker birth, absolute deadline, duration, or cleanup reserve")
    elapsed = value.get("elapsed_from_birth_s")
    remaining = value.get("remaining_to_deadline_s")
    if (isinstance(elapsed, bool) or type(elapsed) not in (int, float) or
            not math.isfinite(float(elapsed)) or float(elapsed) < 0 or
            isinstance(remaining, bool) or type(remaining) not in (int, float) or
            not math.isfinite(float(remaining)) or
            not math.isclose(identity["budget_s"] - float(elapsed), float(remaining),
                             rel_tol=0.0, abs_tol=1e-5)):
        raise CampaignError(f"{label} has inconsistent elapsed/remaining budget history")
    return identity["birth_epoch_s"] + float(elapsed)


def _validate_completed_readmission_budget(
    manifest: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, Any],
) -> tuple[float, float, dict[str, float]]:
    """Validate the successful transition's chronological budget evidence."""
    if manifest.get("birth_budget_binding") != dict(expected_binding):
        raise CampaignError("readmission changed or omitted the science Worker's pinned birth budget/epoch")
    start_observed = _validate_birth_budget_observation(
        manifest.get("birth_budget_at_start"), expected_binding=expected_binding,
        label="readmission birth_budget_at_start")
    finish_observed = _validate_birth_budget_observation(
        manifest.get("birth_budget_at_finish"), expected_binding=expected_binding,
        label="readmission birth_budget_at_finish")
    identity = dict(expected_binding["budget"])
    started = manifest.get("transition_started_epoch_s")
    finished = manifest.get("transition_finished_epoch_s")
    elapsed = manifest.get("transition_elapsed_s")
    if any(isinstance(item, bool) or type(item) not in (int, float) or
           not math.isfinite(float(item)) for item in (started, finished, elapsed)):
        raise CampaignError("readmission transition has missing/non-finite start, finish, or elapsed timing")
    started, finished, elapsed = float(started), float(finished), float(elapsed)
    if (started < identity["birth_epoch_s"] - 1e-5 or finished < started or
            finished >= identity["deadline_epoch_s"] - identity["cleanup_reserve_s"] or
            start_observed + 1e-5 < started or finish_observed + 1e-5 < finished or
            start_observed > finish_observed or
            not math.isclose(elapsed, finished - started, rel_tol=0.0, abs_tol=1.0)):
        raise CampaignError("readmission Worker birth budget history is out of order, expired, or inconsistent")
    return started, finished, identity


def _readmission_operation_identity(
    *, project_id: str, configuration_id: str, case_id: str,
    artifact_sha256: str, worker_epoch: tuple[str, int, str], purpose: str,
) -> tuple[str, str]:
    identity = {
        "schema": "W24_STATIC_SHAPE_READMISSION_SLOT_V1",
        "purpose": purpose,
        "project_id": project_id,
        "configuration_id": configuration_id,
        "case_id": case_id,
        "artifact_sha256": artifact_sha256,
        "worker_epoch": list(worker_epoch),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    suffix = hashlib.sha256(encoded).hexdigest()
    return (f"w24-shape-readmit-{purpose}-{suffix}",
            f"w24-shape-readmit-request-{purpose}-{suffix}")


def _ordered_slot_records(
    project_id: str,
    bindings: Mapping[str, ManagedModelBinding],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(bindings, Mapping) or set(bindings) != set(CAPTURE_KEY_ORDER):
        raise CampaignError("sensitivity campaign requires exactly fourteen config/case bindings")
    records: dict[str, dict[str, Any]] = {}
    worker_epoch: tuple[str, int, str] | None = None
    seen_refs: set[str] = set()
    for key in CAPTURE_KEY_ORDER:
        config_id, case_id = key.split(":", 1)
        record = _binding_record(bindings[key], project_id=project_id, case_id=key)
        ref = record["model_ref"]
        epoch = (record["session_id"], ref["generation"], ref["server_instance_id"])
        if worker_epoch is None:
            worker_epoch = epoch
        elif worker_epoch != epoch:
            raise CampaignError("all fourteen sensitivity models must use one current registered Worker epoch")
        ref_json = json.dumps(ref, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if ref_json in seen_refs:
            raise CampaignError("each sensitivity configuration/case requires a distinct original ModelRef")
        seen_refs.add(ref_json)
        records[key] = record

    slots: list[dict[str, Any]] = []
    for planned in sensitivity_submission_slots():
        key = sensitivity_binding_key(planned["configuration_id"], planned["case_id"])
        slot_key, request_id = sensitivity_slot_identity(
            project_id, bindings[key], planned["configuration_id"], planned["case_id"])
        slots.append({
            "submission_index": planned["submission_index"],
            "configuration_id": planned["configuration_id"],
            "case_id": planned["case_id"],
            "model_tag": records[key]["model_tag"],
            "slot_idempotency_key": slot_key,
            "request_id": request_id,
        })
    return slots, records


def _slot_history_sha256(slots: list[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for slot in slots:
        row = (f"{slot['submission_index']}|{slot['configuration_id']}|{slot['case_id']}|"
               f"{slot['model_tag']}|{slot['slot_idempotency_key']}\n")
        digest.update(row.encode("utf-8"))
    return digest.hexdigest()


def validate_sensitivity_approval(
    approval_path: Path,
    *,
    expected_approval_sha256: str,
    expected_source_sha256: Mapping[str, str],
    expected_setup_receipt_sha256: Mapping[str, str],
    expected_historical_setup_receipt_sha256: Mapping[str, str],
    expected_readmission_manifest_sha256: str,
    expected_readmission_transition_id: str,
    expected_project_id: str,
    expected_workspace: Path,
    expected_operation_store_path: Path,
    expected_bindings: Mapping[str, ManagedModelBinding],
    expected_slots: list[Mapping[str, Any]],
    expected_birth_budget: BirthBudget,
) -> dict[str, Any]:
    """Fail closed unless an APPROVED freeze pins every native-action input."""
    path = Path(approval_path)
    if (path.is_symlink() or not path.is_file() or
            not SHA256_RE.fullmatch(str(expected_approval_sha256)) or
            sha256_file(path) != expected_approval_sha256):
        raise CampaignError("14-slot sensitivity approval is absent, aliased, or differs from its reviewed SHA-256")
    try:
        approval = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError("sensitivity approval is not readable canonical JSON") from exc
    plan = build_sensitivity_campaign_plan()
    setup_hashes = approval.get("setup_receipt_sha256") if isinstance(approval, Mapping) else None
    expected_limits = plan["comparison_limits_after_each_case_passes"]
    if (not isinstance(approval, Mapping) or approval.get("schema") != APPROVAL_SCHEMA or
            approval.get("status") != "APPROVED" or
            not isinstance(approval.get("campaign_id"), str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", approval["campaign_id"]) or
            approval.get("project_id") != expected_project_id or
            approval.get("project_workspace") != str(expected_workspace) or
            approval.get("operation_store_path") != str(expected_operation_store_path) or
            approval.get("configuration_order") != plan["configuration_order"] or
            approval.get("case_order") != list(SENSITIVITY_CASE_ORDER) or
            approval.get("study_run_submissions") != 14 or
            approval.get("phase_initialization_steps_per_submission") != 1 or
            approval.get("capture_grid_protocol") != SENSITIVITY_CAPTURE_PROTOCOL or
            approval.get("comparison_limits") != expected_limits):
        raise CampaignError("approval does not authorize exactly the frozen fourteen-slot W24 sensitivity matrix")
    if (not isinstance(expected_source_sha256, Mapping) or
            set(expected_source_sha256) != {"study_run", "history_capture", "science_executor",
                                           "setup_fixture", "setup_readback"} or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in expected_source_sha256.values()) or
            not isinstance(approval.get("source_sha256"), Mapping) or
            dict(approval["source_sha256"]) != dict(expected_source_sha256)):
        raise CampaignError("approval does not bind all exact W24 sensitivity Java/Python source bytes")
    if (not isinstance(expected_setup_receipt_sha256, Mapping) or
            set(expected_setup_receipt_sha256) != set(CAPTURE_KEY_ORDER) or
            not isinstance(setup_hashes, Mapping) or dict(setup_hashes) != dict(expected_setup_receipt_sha256) or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in expected_setup_receipt_sha256.values())):
        raise CampaignError("approval does not pin the fourteen original unsolved setup receipts")
    if (not isinstance(expected_historical_setup_receipt_sha256, Mapping) or
            set(expected_historical_setup_receipt_sha256) != set(CAPTURE_KEY_ORDER) or
            not isinstance(approval.get("historical_setup_receipt_sha256"), Mapping) or
            dict(approval["historical_setup_receipt_sha256"]) !=
            dict(expected_historical_setup_receipt_sha256) or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in expected_historical_setup_receipt_sha256.values()) or
            not SHA256_RE.fullmatch(str(expected_readmission_manifest_sha256)) or
            approval.get("readmission_manifest_sha256") != expected_readmission_manifest_sha256 or
            approval.get("readmission_transition_id") != expected_readmission_transition_id or
            approval.get("setup_epoch_transition") != READMISSION_SCHEMA):
        raise CampaignError("approval does not pin the fourteen historical setup receipts and exact new-Worker readmission manifest")
    binding_records = {
        key: _binding_record(expected_bindings[key], project_id=expected_project_id, case_id=key)
        for key in CAPTURE_KEY_ORDER
    }
    worker_epochs = {_model_epoch(expected_bindings[key]) for key in CAPTURE_KEY_ORDER}
    if len(worker_epochs) != 1:
        raise CampaignError("sensitivity approval requires one exact science Worker epoch")
    expected_budget_binding = _birth_budget_binding_record(
        expected_birth_budget, next(iter(worker_epochs)))
    if approval.get("science_worker_birth_budget") != expected_budget_binding:
        raise CampaignError("approval does not pin the exact science Worker birth, deadline, and cleanup reserve")
    approved_bindings = approval.get("model_bindings")
    if not isinstance(approved_bindings, Mapping) or dict(approved_bindings) != binding_records:
        raise CampaignError("approval does not pin all fourteen original ModelRefs and revisions")
    approved_slots = approval.get("ordered_slots")
    if not isinstance(approved_slots, list) or approved_slots != [dict(slot) for slot in expected_slots]:
        raise CampaignError("approval does not pin the exact deterministic OperationStore slot order/keys")
    limits = approval.get("resource_limits")
    estimate = sensitivity_capture_resource_estimate()
    if (not isinstance(limits, Mapping) or
            limits.get("maximum_study_run_submissions") != 14 or
            limits.get("maximum_capture_files") != 14 or
            isinstance(limits.get("maximum_single_capture_bytes"), bool) or
            type(limits.get("maximum_single_capture_bytes")) is not int or
            limits["maximum_single_capture_bytes"] < estimate["maximum_single_history_bytes"] or
            limits["maximum_single_capture_bytes"] > estimate["per_history_hard_limit_bytes"] or
            isinstance(limits.get("maximum_total_raw_capture_bytes"), bool) or
            type(limits.get("maximum_total_raw_capture_bytes")) is not int or
            limits["maximum_total_raw_capture_bytes"] < estimate["total_raw_capture_bytes"] or
            isinstance(limits.get("maximum_total_project_output_bytes"), bool) or
            type(limits.get("maximum_total_project_output_bytes")) is not int or
            limits["maximum_total_project_output_bytes"] <= 0 or
            isinstance(limits.get("maximum_single_solved_mph_bytes"), bool) or
            type(limits.get("maximum_single_solved_mph_bytes")) is not int or
            limits["maximum_single_solved_mph_bytes"] <= 0 or
            isinstance(limits.get("maximum_campaign_wall_time_s"), bool) or
            type(limits.get("maximum_campaign_wall_time_s")) is not int or
            limits["maximum_campaign_wall_time_s"] <= 0 or
            limits["maximum_campaign_wall_time_s"] > 3600):
        raise CampaignError("approval lacks a sufficient explicit 14-run/raw-output/project-output/wall-time resource ceiling")
    return dict(approval)


def _load_approved_sensitivity_setup_receipt(
    supplied_path: Path,
    *,
    configuration_id: str,
    case_id: str,
    workspace: Path,
    project_id: str,
    binding_record: Mapping[str, Any],
    expected_receipt_sha256: str,
    expected_fixture_sha256: str,
    expected_readback_sha256: str,
) -> dict[str, Any]:
    key = sensitivity_binding_key(configuration_id, case_id)
    receipt_path = _project_path(workspace, Path(supplied_path), must_exist=True)
    if sha256_file(receipt_path) != expected_receipt_sha256:
        raise CampaignError(f"{key} setup receipt differs from its approved byte hash")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"{key} setup receipt cannot be decoded") from exc
    expected_schema = ("W24_STATIC_SHAPE_MANAGED_SETUP_V1" if configuration_id == "baseline"
                       else "W24_STATIC_SHAPE_MANAGED_SETUP_V2")
    if (not isinstance(receipt, Mapping) or receipt.get("schema") != expected_schema or
            receipt.get("status") != "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE" or
            receipt.get("native_acceptance") != "NOT_RUN" or
            receipt.get("project_id") != project_id or receipt.get("project_workspace") != str(workspace) or
            receipt.get("configuration_id") != configuration_id or receipt.get("case_id") != case_id or
            receipt.get("reopened_model_binding") != dict(binding_record) or
            receipt.get("study_run_submissions") != [] or
            receipt.get("study_run_submission_count") != 0 or
            receipt.get("phase_initialization_executed") is not False):
        raise CampaignError(f"{key} setup receipt is not the exact approved unsolved project/model record")
    source_hashes = receipt.get("source_sha256")
    if (not isinstance(source_hashes, Mapping) or
            source_hashes.get("fixture") != expected_fixture_sha256 or
            source_hashes.get("readback") != expected_readback_sha256):
        raise CampaignError(f"{key} setup receipt does not pin the approved setup Java sources")
    readback_binding = receipt.get("reopened_configuration_readback_binding")
    if (not isinstance(readback_binding, Mapping) or
            {key_name: value for key_name, value in readback_binding.items() if key_name != "revision"} !=
            {key_name: value for key_name, value in binding_record.items() if key_name != "revision"} or
            isinstance(readback_binding.get("revision"), bool) or
            type(readback_binding.get("revision")) is not int or
            readback_binding["revision"] + 1 != binding_record.get("revision")):
        raise CampaignError(f"{key} setup receipt lost the exact full-readback source revision")
    identity = receipt.get("reopened_model_identity")
    identity_binding = identity.get("model_binding") if isinstance(identity, Mapping) else None
    if not isinstance(identity_binding, Mapping) or dict(identity_binding) != dict(readback_binding):
        raise CampaignError(f"{key} setup identity inspection is not bound to its full readback revision")
    configuration_readback = receipt.get("reopened_configuration_readback")
    try:
        StaticShapeManagedRunner._validate_shape_readback(
            configuration_readback, case_id, configuration_id)
    except CampaignError as exc:
        raise CampaignError(f"{key} approved configuration/mesh/solver/getSize readback is invalid: {exc}") from exc
    if configuration_readback.get("model_tag") != binding_record.get("model_tag"):
        raise CampaignError(f"{key} complete setup readback identifies another native model")
    comparison = receipt.get("readback_comparison")
    if (not isinstance(comparison, Mapping) or comparison.get("matches") is not True or
            comparison.get("interpolation_used") is not False):
        raise CampaignError(f"{key} save/reopen configuration comparison is not exact")
    artifact = receipt.get("project_artifact")
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str):
        raise CampaignError(f"{key} setup receipt omitted its unsolved MPH identity")
    artifact_path = _project_path(workspace, Path(artifact["path"]), must_exist=True)
    digest = sha256_file(artifact_path)
    if (digest != artifact.get("sha256") or artifact_path.stat().st_size <= 0 or
            artifact_path.stat().st_size != artifact.get("size_bytes")):
        raise CampaignError(f"{key} original unsolved MPH no longer matches its receipt")
    return dict(receipt)


def _read_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"{label} is not readable JSON") from exc
    if not isinstance(value, Mapping):
        raise CampaignError(f"{label} must be a JSON object")
    return dict(value)


def _recompute_persisted_request_hash(request: Mapping[str, Any],
                                     timeouts: Mapping[str, Any]) -> str:
    """Recompute the daemon's canonical hash from its persisted wire request."""
    request_execution = request.get("execution")
    request_arguments = request.get("arguments")
    semantic_operation = request.get("operation")
    if (not isinstance(request_execution, Mapping) or
            not isinstance(request_arguments, Mapping) or
            not isinstance(semantic_operation, str)):
        raise CampaignError("persisted ControlDaemon request is not a canonical operation envelope")
    semantic_arguments = dict(request_arguments)
    if semantic_operation in {"registry_call", "operation_call"}:
        nested_operation = semantic_arguments.get("operation_id")
        nested_arguments = semantic_arguments.get("arguments", {})
        if isinstance(nested_operation, str) and isinstance(nested_arguments, Mapping):
            semantic_operation = nested_operation
            semantic_arguments = dict(nested_arguments)
    from comsol_mcp._execution_contract import canonical_request_hash
    return canonical_request_hash(
        semantic_operation, semantic_arguments,
        request_execution.get("model_ref"), request_execution.get("expected_revision"),
        project_id=request_execution.get("project_id"),
        session_id=request_execution.get("session_id"),
        queue_timeout_s=timeouts.get("queue_timeout_s"),
        execution_timeout_s=timeouts.get("execution_timeout_s"),
        no_progress_warning_s=timeouts.get("no_progress_warning_s"),
    )


def _request_metadata_matches(metadata: Any, request: Mapping[str, Any]) -> bool:
    """Match request identity while allowing public job lifecycle metadata additions."""
    return (isinstance(metadata, Mapping) and
            metadata.get("operation") == request.get("operation") and
            metadata.get("arguments") == dict(request.get("arguments", {})) and
            metadata.get("execution") == dict(request.get("execution", {})))


def _validate_model_load_response_source(response: Mapping[str, Any], artifact_path: Path,
                                         *, label: str) -> None:
    """Require model_load's own public result to echo both requested and loaded MPH paths."""
    data = response.get("data")
    requested_path = data.get("requested_path") if isinstance(data, Mapping) else None
    loaded_path = data.get("file_path") if isinstance(data, Mapping) else None
    try:
        requested_resolved = Path(str(requested_path)).resolve(strict=True)
        loaded_resolved = Path(str(loaded_path)).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CampaignError(f"{label} public model_load response omitted a resolvable requested/loaded file path") from exc
    if requested_resolved != artifact_path or loaded_resolved != artifact_path:
        raise CampaignError(f"{label} public model_load response identifies a different source MPH")


def _capture_operation_store_identity(
    managed_runner: Any,
    *,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    snapshot_path: Path,
) -> dict[str, Any]:
    """Persist the actual terminal public OperationStore operation/job pair."""
    execution = response.get("execution")
    if not isinstance(execution, Mapping) or any(
            not isinstance(execution.get(field), str) or not execution.get(field)
            for field in CONTROL_EXECUTION_ID_FIELDS):
        raise CampaignError("public dispatch response omitted its complete operation/job identity")
    store = getattr(getattr(managed_runner, "daemon", None), "store", None)
    get_operation = getattr(store, "get_operation", None)
    get_job = getattr(store, "operation_job", None)
    store_path_value = getattr(store, "path", None)
    if not callable(get_operation) or not callable(get_job) or store_path_value is None:
        raise CampaignError("public ControlDaemon OperationStore cannot attest dispatch identity")
    store_path = Path(store_path_value).resolve(strict=True)
    store_stat = store_path.stat()
    operation = get_operation(execution["operation_id"])
    job = get_job(execution["operation_id"])
    if not isinstance(operation, Mapping) or not isinstance(job, Mapping):
        raise CampaignError("public OperationStore omitted the terminal operation/job pair")
    if (operation.get("operation_id") != execution["operation_id"] or
            operation.get("request_id") != execution["request_id"] or
            operation.get("idempotency_key") != execution["idempotency_key"] or
            operation.get("request_hash") != execution["request_hash"] or
            operation.get("operation") != request.get("operation") or
            operation.get("status") != "SUCCEEDED" or
            job.get("operation_id") != operation.get("operation_id") or
            job.get("job_id") != execution["job_id"] or
            job.get("status") != "SUCCEEDED" or
            operation.get("result") != dict(response)):
        raise CampaignError("public response identity differs from its terminal OperationStore records")
    metadata = operation.get("metadata")
    request_execution = request.get("execution")
    request_arguments = request.get("arguments")
    if (not isinstance(metadata, Mapping) or not isinstance(request_execution, Mapping) or
            not isinstance(request_arguments, Mapping) or
            metadata.get("operation") != request.get("operation") or
            metadata.get("arguments") != dict(request_arguments) or
            metadata.get("execution") != dict(request_execution)):
        raise CampaignError("OperationStore's persisted request differs from the exact dispatched envelope")
    timeouts = operation.get("effective_timeouts")
    if not isinstance(timeouts, Mapping):
        raise CampaignError("OperationStore omitted effective canonical-request timeouts")
    recomputed_hash = _recompute_persisted_request_hash(request, timeouts)
    if recomputed_hash != operation.get("request_hash"):
        raise CampaignError("OperationStore request_hash does not recompute from the persisted request and effective timeouts")
    if (not _request_metadata_matches(job.get("metadata"), request) or
            job.get("effective_timeouts") != dict(timeouts) or
            job.get("result") != dict(response) or job.get("operation") != dict(operation)):
        raise CampaignError("public job record does not retain the exact terminal operation/request/result identity")
    snapshot = {
        "schema": READMISSION_STORE_SCHEMA,
        "operation_store_path": str(store_path),
        "operation_store_identity": {"device": store_stat.st_dev, "inode": store_stat.st_ino},
        # Keep the exact public records, including terminal timestamps, result,
        # and job lifecycle metadata added after the original request began.
        "operation": dict(operation),
        "job": dict(job),
        "response_execution": {name: execution.get(name) for name in CONTROL_EXECUTION_ID_FIELDS},
        "canonical_request_hash_recomputed": recomputed_hash,
    }
    _write_json_fsynced(snapshot_path, snapshot)
    return {
        "operation_store_path": str(store_path),
        "operation_store_identity": snapshot["operation_store_identity"],
        "path": str(snapshot_path),
        "sha256": sha256_file(snapshot_path),
        "operation": snapshot["operation"],
        "job": snapshot["job"],
        "response_execution": snapshot["response_execution"],
        "canonical_request_hash_recomputed": recomputed_hash,
    }


def _load_and_validate_dispatch_evidence(
    workspace: Path,
    *,
    request_path_value: Any,
    request_sha256: Any,
    response_path_value: Any,
    response_sha256: Any,
    operation_store_path_value: Any,
    operation_store_sha256: Any,
    expected_transition_id: str,
    expected_slot_id: str,
    expected_action: str,
    expected_source: Mapping[str, Any],
    expected_operation: str,
    expected_arguments: Mapping[str, Any],
    expected_project_id: str,
    expected_session_id: str,
    expected_model_ref: Mapping[str, Any] | None,
    expected_revision: int | None,
    expected_control_identity: Mapping[str, Any],
    expected_store_path: Path,
    expected_store_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a saved request, response, and store/job snapshot to one slot."""
    request_path = _project_path(workspace, Path(str(request_path_value)), must_exist=True)
    if not SHA256_RE.fullmatch(str(request_sha256)) or sha256_file(request_path) != request_sha256:
        raise CampaignError(f"{expected_slot_id} persisted public request differs from its pinned SHA-256")
    request_record = _read_json_mapping(request_path, label=f"{expected_slot_id} dispatch request evidence")
    if (request_record.get("schema") != READMISSION_REQUEST_SCHEMA or
            request_record.get("transition_id") != expected_transition_id or
            request_record.get("slot_id") != expected_slot_id or
            request_record.get("action") != expected_action or
            request_record.get("source") != dict(expected_source)):
        raise CampaignError(f"{expected_slot_id} request evidence changed its slot, source, or transition binding")
    request = request_record.get("request")
    if (not isinstance(request, Mapping) or set(request) != {"operation", "arguments", "execution"} or
            request.get("operation") != expected_operation or
            request.get("arguments") != dict(expected_arguments)):
        raise CampaignError(f"{expected_slot_id} saved request envelope differs from its approved public route/payload")
    request_execution = request.get("execution")
    if (not isinstance(request_execution, Mapping) or
            request_execution.get("project_id") != expected_project_id or
            request_execution.get("session_id") != expected_session_id or
            request_execution.get("idempotency_key") != expected_control_identity.get("idempotency_key") or
            request_execution.get("request_id") != expected_control_identity.get("request_id") or
            request_execution.get("model_ref") != (dict(expected_model_ref) if expected_model_ref is not None else None) or
            request_execution.get("expected_revision") != expected_revision):
        raise CampaignError(f"{expected_slot_id} saved request key/ID/project/ModelRef/revision changed")
    if (isinstance(request_execution.get("rpc_timeout_s"), bool) or
            not isinstance(request_execution.get("rpc_timeout_s"), (int, float)) or
            not math.isfinite(float(request_execution["rpc_timeout_s"])) or
            request_execution["rpc_timeout_s"] <= 0):
        raise CampaignError(f"{expected_slot_id} saved request has an invalid RPC wait budget")

    response_path = _project_path(workspace, Path(str(response_path_value)), must_exist=True)
    if not SHA256_RE.fullmatch(str(response_sha256)) or sha256_file(response_path) != response_sha256:
        raise CampaignError(f"{expected_slot_id} public response is missing or differs from its pinned SHA-256")
    response = _read_json_mapping(response_path, label=f"{expected_slot_id} public dispatch response")
    response_execution = response.get("execution")
    if (response.get("success") is not True or not isinstance(response_execution, Mapping) or
            any(not isinstance(response_execution.get(field), str) or not response_execution.get(field)
                for field in CONTROL_EXECUTION_ID_FIELDS) or
            any(response_execution.get(field) != expected_control_identity.get(field)
                for field in CONTROL_EXECUTION_ID_FIELDS)):
        raise CampaignError(f"{expected_slot_id} public response operation/job identity differs from its approved request")

    store_snapshot_path = _project_path(
        workspace, Path(str(operation_store_path_value)), must_exist=True)
    if (not SHA256_RE.fullmatch(str(operation_store_sha256)) or
            sha256_file(store_snapshot_path) != operation_store_sha256):
        raise CampaignError(f"{expected_slot_id} OperationStore/job snapshot is missing or has changed")
    store_snapshot = _read_json_mapping(
        store_snapshot_path, label=f"{expected_slot_id} OperationStore/job identity evidence")
    operation = store_snapshot.get("operation")
    job = store_snapshot.get("job")
    operation_store_identity = store_snapshot.get("operation_store_identity")
    if (store_snapshot.get("schema") != READMISSION_STORE_SCHEMA or
            store_snapshot.get("operation_store_path") != str(expected_store_path) or
            operation_store_identity != dict(expected_store_identity) or
            not isinstance(operation, Mapping) or not isinstance(job, Mapping) or
            operation.get("operation") != expected_operation or operation.get("status") != "SUCCEEDED" or
            job.get("status") != "SUCCEEDED" or
            any(operation.get(field) != expected_control_identity.get(field)
                for field in CONTROL_EXECUTION_ID_FIELDS if field != "job_id") or
            job.get("job_id") != expected_control_identity.get("job_id") or
            job.get("operation_id") != expected_control_identity.get("operation_id") or
            store_snapshot.get("response_execution") != {
                field: response_execution.get(field) for field in CONTROL_EXECUTION_ID_FIELDS}):
        raise CampaignError(f"{expected_slot_id} saved OperationStore operation/job is foreign or nonterminal")
    metadata = operation.get("metadata")
    if (not _request_metadata_matches(metadata, request) or
            not _request_metadata_matches(job.get("metadata"), request) or
            job.get("effective_timeouts") != operation.get("effective_timeouts") or
            job.get("result") != response or job.get("operation") != dict(operation)):
        raise CampaignError(f"{expected_slot_id} OperationStore request source differs from the saved public envelope")
    try:
        recomputed_hash = _recompute_persisted_request_hash(
            request, operation.get("effective_timeouts", {}))
    except CampaignError:
        raise
    if (store_snapshot.get("canonical_request_hash_recomputed") != operation.get("request_hash") or
            recomputed_hash != operation.get("request_hash") or
            response_execution.get("request_hash") != operation.get("request_hash") or
            expected_control_identity.get("request_hash") != operation.get("request_hash")):
        raise CampaignError(f"{expected_slot_id} canonical request hash is not bound to its saved request")
    if operation.get("result") != response:
        raise CampaignError(f"{expected_slot_id} public response is not the result retained by its OperationStore job")
    return dict(request), response


def _load_approved_sensitivity_readmission_receipt(
    supplied_path: Path,
    *,
    configuration_id: str,
    case_id: str,
    workspace: Path,
    project_id: str,
    binding_record: Mapping[str, Any],
    expected_receipt_sha256: str,
    expected_historical_receipt_sha256: str,
    expected_fixture_sha256: str,
    expected_readback_sha256: str,
    expected_transition_id: str,
    expected_operation_store_path: Path,
    expected_operation_store_identity: Mapping[str, Any],
    expected_birth_budget_binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one new-Worker setup proof against its immutable old artifact proof."""
    key = sensitivity_binding_key(configuration_id, case_id)
    path = _project_path(workspace, Path(supplied_path), must_exist=True)
    if sha256_file(path) != expected_receipt_sha256:
        raise CampaignError(f"{key} new-Worker readmission receipt differs from its approved byte hash")
    receipt = _read_json_mapping(path, label=f"{key} new-Worker readmission receipt")
    expected_parent_keys = {
        "schema": READMISSION_SCHEMA,
        "status": "SCIENCE_WORKER_ARTIFACT_READMIT_READBACK_COMPLETE",
        "native_acceptance": "NOT_RUN",
        "project_id": project_id,
        "project_workspace": str(workspace),
        "configuration_id": configuration_id,
        "case_id": case_id,
        "transition_id": expected_transition_id,
        "reopened_model_binding": dict(binding_record),
        "study_run_calls_this_action": 0,
        "operation_store_path": str(expected_operation_store_path.resolve(strict=True)),
        "operation_store_identity": dict(expected_operation_store_identity),
    }
    if any(receipt.get(field) != expected for field, expected in expected_parent_keys.items()):
        raise CampaignError(f"{key} receipt does not bind this exact new Worker/configuration/case readmission")
    if receipt.get("birth_budget_binding") != dict(expected_birth_budget_binding):
        raise CampaignError(f"{key} readmission receipt changed the science Worker birth budget or epoch")
    _validate_birth_budget_observation(
        receipt.get("birth_budget_at_readback"),
        expected_binding=expected_birth_budget_binding,
        label=f"{key} readmission birth_budget_at_readback")
    source_hashes = receipt.get("source_sha256")
    if (not isinstance(source_hashes, Mapping) or
            source_hashes.get("fixture") != expected_fixture_sha256 or
            source_hashes.get("readback") != expected_readback_sha256):
        raise CampaignError(f"{key} readmission receipt does not pin the reviewed setup Java source bytes")

    historical_path_value = receipt.get("historical_setup_receipt_path")
    if not isinstance(historical_path_value, str):
        raise CampaignError(f"{key} readmission receipt omitted its historical setup proof path")
    historical_path = _project_path(workspace, Path(historical_path_value), must_exist=True)
    if (receipt.get("historical_setup_receipt_sha256") != expected_historical_receipt_sha256 or
            sha256_file(historical_path) != expected_historical_receipt_sha256):
        raise CampaignError(f"{key} historical setup receipt no longer matches the pinned SHA-256")
    old_receipt = _read_json_mapping(historical_path, label=f"{key} historical setup receipt")
    old_binding = old_receipt.get("reopened_model_binding")
    if not isinstance(old_binding, Mapping):
        raise CampaignError(f"{key} historical setup receipt omitted its retired Worker binding")
    historical = _load_approved_sensitivity_setup_receipt(
        historical_path, configuration_id=configuration_id, case_id=case_id,
        workspace=workspace, project_id=project_id, binding_record=old_binding,
        expected_receipt_sha256=expected_historical_receipt_sha256,
        expected_fixture_sha256=expected_fixture_sha256,
        expected_readback_sha256=expected_readback_sha256,
    )
    if receipt.get("historical_model_binding") != historical.get("reopened_model_binding"):
        raise CampaignError(f"{key} readmission receipt substituted the retired historical ModelRef")

    loaded_record = receipt.get("reopened_configuration_readback_binding")
    if not isinstance(loaded_record, Mapping):
        raise CampaignError(f"{key} full readback lacks its exact pre-readback ModelRef/revision")
    final_binding = dict(binding_record)
    if ({name: value for name, value in loaded_record.items() if name != "revision"} !=
            {name: value for name, value in final_binding.items() if name != "revision"} or
            isinstance(loaded_record.get("revision"), bool) or
            type(loaded_record.get("revision")) is not int or
            loaded_binding_revision_delta(final_binding, loaded_record) != 1):
        raise CampaignError(f"{key} full readback revision transition is not the exact one-step trusted-readback transition")
    historic_binding_obj = ManagedModelBinding(
        project_id, historical["reopened_model_binding"]["session_id"],
        dict(historical["reopened_model_binding"]["model_ref"]),
        historical["reopened_model_binding"]["revision"],
    )
    current_binding_obj = ManagedModelBinding(
        project_id, final_binding["session_id"], dict(final_binding["model_ref"]), final_binding["revision"])
    if _model_epoch(historic_binding_obj) == _model_epoch(current_binding_obj):
        raise CampaignError(f"{key} science ModelRef still belongs to the retired setup Worker epoch")
    epoch_record = receipt.get("current_worker_epoch")
    expected_epoch = {
        "session_id": current_binding_obj.session_id,
        "generation": current_binding_obj.model_ref["generation"],
        "server_instance_id": current_binding_obj.model_ref["server_instance_id"],
    }
    if epoch_record != expected_epoch:
        raise CampaignError(f"{key} readmission receipt has a stale or inconsistent new Worker epoch")

    configuration_readback = receipt.get("reopened_configuration_readback")
    try:
        StaticShapeManagedRunner._validate_shape_readback(configuration_readback, case_id, configuration_id)
    except CampaignError as exc:
        raise CampaignError(f"{key} new-Worker full configuration/mesh/solver/getSize readback is invalid: {exc}") from exc
    if configuration_readback.get("model_tag") != final_binding.get("model_tag"):
        raise CampaignError(f"{key} new-Worker configuration readback identifies another model tag")
    old_readback = historical.get("reopened_configuration_readback")
    if not isinstance(old_readback, Mapping):
        raise CampaignError(f"{key} historic readback is missing the original complete configuration")
    old_comparable = {name: value for name, value in old_readback.items() if name != "model_tag"}
    new_comparable = {name: value for name, value in configuration_readback.items() if name != "model_tag"}
    comparison = receipt.get("readback_comparison")
    if (old_comparable != new_comparable or not isinstance(comparison, Mapping) or
            comparison.get("matches") is not True or comparison.get("interpolation_used") is not False or
            comparison.get("comparison") != "exact JSON-native configuration values"):
        raise CampaignError(f"{key} new Worker did not reproduce the complete historic setup configuration exactly")

    identity = receipt.get("reopened_model_identity")
    if not isinstance(identity, Mapping) or identity.get("model_binding") != final_binding:
        raise CampaignError(f"{key} final identity inspection does not prove the current post-readback revision")
    for field in ("study_run_submissions_before", "study_run_submissions_after"):
        if receipt.get(field) != []:
            raise CampaignError(f"{key} readmission stage did not preserve a zero-Study.run project ledger")
    load = receipt.get("model_load")
    if (not isinstance(load, Mapping) or load.get("status") != "SUCCEEDED" or
            not isinstance(load.get("idempotency_key"), str) or
            not isinstance(load.get("request_id"), str)):
        raise CampaignError(f"{key} readmission lacks one terminal, durably identified model_load")
    java_readback = receipt.get("configuration_readback")
    if (not isinstance(java_readback, Mapping) or java_readback.get("status") != "SUCCEEDED" or
            not isinstance(java_readback.get("idempotency_key"), str) or
            not isinstance(java_readback.get("request_id"), str) or
            not SHA256_RE.fullmatch(str(java_readback.get("response_sha256")))):
        raise CampaignError(f"{key} readmission lacks a terminal, idempotently identified Java configuration readback")
    java_readback_path = _project_path(
        workspace, Path(str(java_readback.get("response_path", ""))), must_exist=True)
    if sha256_file(java_readback_path) != java_readback["response_sha256"]:
        raise CampaignError(f"{key} saved Java configuration readback response changed")
    java_response = _read_json_mapping(java_readback_path, label=f"{key} Java configuration readback response")
    java_execution = java_response.get("execution")
    java_data = java_response.get("data")
    result_envelope = java_data.get("readback") if isinstance(java_data, Mapping) else None
    java_result = result_envelope.get("readback") if isinstance(result_envelope, Mapping) else None
    if (java_response.get("success") is not True or not isinstance(java_execution, Mapping) or
            (java_execution.get("project_id") is not None and
             java_execution.get("project_id") != project_id) or
            java_execution.get("session_id") != current_binding_obj.session_id or
            java_execution.get("model_ref") != dict(current_binding_obj.model_ref) or
            java_execution.get("revision") != final_binding.get("revision") or
            java_result != configuration_readback):
        raise CampaignError(f"{key} saved Java response does not prove the exact terminal full configuration readback")
    artifact = receipt.get("project_artifact")
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str):
        raise CampaignError(f"{key} readmission receipt omitted its original hash-bound MPH")
    old_artifact = historical.get("project_artifact")
    if dict(artifact) != dict(old_artifact):
        raise CampaignError(f"{key} readmission receipt changed the approved saved MPH identity")
    artifact_path = _project_path(workspace, Path(artifact["path"]), must_exist=True)
    if sha256_file(artifact_path) != artifact.get("sha256"):
        raise CampaignError(f"{key} hash-bound setup MPH changed after science Worker readmission")

    expected_load_key, expected_load_request = _readmission_operation_identity(
        project_id=project_id, configuration_id=configuration_id, case_id=case_id,
        artifact_sha256=str(artifact.get("sha256")),
        worker_epoch=_model_epoch(current_binding_obj), purpose="model-load")
    load_identity = load.get("control_identity")
    if (load.get("slot_id") != key or load.get("idempotency_key") != expected_load_key or
            load.get("request_id") != expected_load_request or
            not isinstance(load_identity, Mapping) or
            load_identity.get("idempotency_key") != expected_load_key or
            load_identity.get("request_id") != expected_load_request or
            load.get("source_artifact_path") != str(artifact_path) or
            load.get("source_artifact_sha256") != artifact.get("sha256") or
            load.get("historical_setup_receipt_path") != str(historical_path) or
            load.get("historical_setup_receipt_sha256") != expected_historical_receipt_sha256):
        raise CampaignError(f"{key} model_load source artifact or stable public slot identity is not approved")
    load_response_path = _project_path(
        workspace, Path(str(load.get("response_path", ""))), must_exist=True)
    load_response = _read_json_mapping(load_response_path, label=f"{key} model_load response")
    _validate_model_load_response_source(load_response, artifact_path, label=key)
    loaded_from_response = ManagedModelBinding.from_load_response(load_response, project_id=project_id)
    load_execution = load_response.get("execution")
    if (loaded_from_response.as_record() != dict(loaded_record) or
            not isinstance(load_execution, Mapping) or
            (load_execution.get("project_id") is not None and
             load_execution.get("project_id") != project_id) or
            load_execution.get("session_id") != current_binding_obj.session_id or
            load_execution.get("model_ref") != dict(current_binding_obj.model_ref) or
            load_execution.get("revision") != loaded_record.get("revision")):
        raise CampaignError(f"{key} saved model_load response identifies another current ModelRef/revision")
    load_request, _ = _load_and_validate_dispatch_evidence(
        workspace,
        request_path_value=load.get("request_path"),
        request_sha256=load.get("request_sha256"),
        response_path_value=load.get("response_path"),
        response_sha256=load.get("response_sha256"),
        operation_store_path_value=load.get("operation_store_record_path"),
        operation_store_sha256=load.get("operation_store_record_sha256"),
        expected_transition_id=expected_transition_id,
        expected_slot_id=key,
        expected_action="model_load",
        expected_source={
            "artifact_path": str(artifact_path),
            "artifact_sha256": artifact["sha256"],
            "historical_setup_receipt_path": str(historical_path),
            "historical_setup_receipt_sha256": expected_historical_receipt_sha256,
        },
        expected_operation="model_load",
        expected_arguments={"path": str(artifact_path)},
        expected_project_id=project_id,
        expected_session_id=current_binding_obj.session_id,
        expected_model_ref=None,
        expected_revision=None,
        expected_control_identity=load_identity,
        expected_store_path=expected_operation_store_path.resolve(strict=True),
        expected_store_identity=expected_operation_store_identity,
    )
    load_exec = load_request["execution"]
    if "model_ref" in load_exec or "expected_revision" in load_exec:
        raise CampaignError(f"{key} model_load request must bind the Worker session without borrowing a ModelRef")

    expected_readback_key, expected_readback_request = _readmission_operation_identity(
        project_id=project_id, configuration_id=configuration_id, case_id=case_id,
        artifact_sha256=str(artifact.get("sha256")),
        worker_epoch=_model_epoch(current_binding_obj), purpose="configuration-readback")
    readback_identity = java_readback.get("control_identity")
    readback_source_path = _project_path(
        workspace, workspace / SETUP_READBACK_SOURCE.name, must_exist=True)
    if (sha256_file(readback_source_path) != expected_readback_sha256 or
            java_readback.get("slot_id") != key or
            java_readback.get("idempotency_key") != expected_readback_key or
            java_readback.get("request_id") != expected_readback_request or
            not isinstance(readback_identity, Mapping) or
            readback_identity.get("idempotency_key") != expected_readback_key or
            readback_identity.get("request_id") != expected_readback_request or
            java_readback.get("source_path") != str(readback_source_path) or
            java_readback.get("source_sha256") != expected_readback_sha256):
        raise CampaignError(f"{key} Java readback source or stable public slot identity is not approved")
    expected_java_arguments = {
        "operation_id": "code.execute_java",
        "arguments": {
            "source_artifact": readback_source_path.name,
            "entrypoint": "W24StaticShapeReadback#run",
            "arguments": {"action": "readback", "expected_configuration_id": configuration_id},
            "mode": "trusted",
        },
    }
    _load_and_validate_dispatch_evidence(
        workspace,
        request_path_value=java_readback.get("request_path"),
        request_sha256=java_readback.get("request_sha256"),
        response_path_value=java_readback.get("response_path"),
        response_sha256=java_readback.get("response_sha256"),
        operation_store_path_value=java_readback.get("operation_store_record_path"),
        operation_store_sha256=java_readback.get("operation_store_record_sha256"),
        expected_transition_id=expected_transition_id,
        expected_slot_id=key,
        expected_action="configuration_readback",
        expected_source={
            "role": "readback", "path": str(readback_source_path),
            "sha256": expected_readback_sha256,
        },
        expected_operation="operation_call",
        expected_arguments=expected_java_arguments,
        expected_project_id=project_id,
        expected_session_id=current_binding_obj.session_id,
        expected_model_ref=current_binding_obj.model_ref,
        expected_revision=loaded_record["revision"],
        expected_control_identity=readback_identity,
        expected_store_path=expected_operation_store_path.resolve(strict=True),
        expected_store_identity=expected_operation_store_identity,
    )
    return receipt, historical


def _load_approved_readmission_manifest(
    supplied_path: Path,
    *,
    expected_sha256: str,
    expected_transition_id: str,
    workspace: Path,
    project_id: str,
    operation_store_path: Path,
    operation_store_identity: Mapping[str, Any],
    source_sha256: Mapping[str, str],
    expected_readmission_receipt_sha256: Mapping[str, str],
    expected_historical_receipt_sha256: Mapping[str, str],
    expected_bindings: Mapping[str, ManagedModelBinding],
    expected_birth_budget: BirthBudget,
) -> dict[str, Any]:
    path = _project_path(workspace, Path(supplied_path), must_exist=True)
    if not SHA256_RE.fullmatch(str(expected_sha256)) or sha256_file(path) != expected_sha256:
        raise CampaignError("readmission transition manifest differs from its separately approved SHA-256")
    manifest = _read_json_mapping(path, label="readmission transition manifest")
    expected_epoch = _model_epoch(expected_bindings[CAPTURE_KEY_ORDER[0]])
    expected_budget_binding = _birth_budget_binding_record(expected_birth_budget, expected_epoch)
    expected_slot_records = {
        key: _binding_record(expected_bindings[key], project_id=project_id, case_id=key)
        for key in CAPTURE_KEY_ORDER
    }
    slots = manifest.get("slots")
    if manifest.get("birth_budget_binding") != expected_budget_binding:
        raise CampaignError("readmission transition changed the science Worker birth budget or epoch")
    if (manifest.get("schema") != READMISSION_MANIFEST_SCHEMA or
            manifest.get("status") != "SCIENCE_WORKER_READMISSION_COMPLETE" or
            manifest.get("native_acceptance") != "NOT_RUN" or
            manifest.get("transition_id") != expected_transition_id or
            manifest.get("project_id") != project_id or
            manifest.get("project_workspace") != str(workspace) or
            manifest.get("operation_store_path") != str(operation_store_path) or
            manifest.get("operation_store_identity") != dict(operation_store_identity) or
            manifest.get("science_worker_epoch") != {
                "session_id": expected_epoch[0], "generation": expected_epoch[1],
                "server_instance_id": expected_epoch[2]} or
            manifest.get("source_sha256") != dict(source_sha256) or
            manifest.get("expected_historical_setup_receipt_sha256") !=
            dict(expected_historical_receipt_sha256) or
            manifest.get("planned_model_loads") != 14 or
            manifest.get("completed_model_loads") != 14 or
            manifest.get("study_run_submissions_before") != [] or
            manifest.get("study_run_submissions_after") != [] or
            not isinstance(slots, Mapping) or set(slots) != set(CAPTURE_KEY_ORDER)):
        raise CampaignError("transition manifest does not prove exactly fourteen current-epoch artifact readbacks")
    started_observed, finished_observed, _budget_identity = _validate_completed_readmission_budget(
        manifest, expected_binding=expected_budget_binding)
    slot_budget_observations: list[float] = []
    for key in CAPTURE_KEY_ORDER:
        row = slots.get(key)
        if (not isinstance(row, Mapping) or
                row.get("historical_setup_receipt_sha256") != expected_historical_receipt_sha256[key] or
                row.get("readmission_receipt_sha256") != expected_readmission_receipt_sha256[key] or
                row.get("new_binding") != expected_slot_records[key] or
                row.get("birth_budget_binding") != expected_budget_binding):
            raise CampaignError(f"transition manifest does not bind the exact old artifact and new ModelRef for {key}")
        receipt_path = _project_path(workspace, Path(str(row.get("readmission_receipt_path", ""))),
                                     must_exist=True)
        if sha256_file(receipt_path) != expected_readmission_receipt_sha256[key]:
            raise CampaignError(f"transition manifest readmission receipt changed for {key}")
        slot_receipt = _read_json_mapping(receipt_path, label=f"{key} readmission receipt")
        load = slot_receipt.get("model_load")
        readback = slot_receipt.get("configuration_readback")
        duplicated_identity = {
            "artifact_path": slot_receipt.get("project_artifact", {}).get("path")
                if isinstance(slot_receipt.get("project_artifact"), Mapping) else None,
            "artifact_sha256": slot_receipt.get("project_artifact", {}).get("sha256")
                if isinstance(slot_receipt.get("project_artifact"), Mapping) else None,
            "model_load_control_identity": load.get("control_identity") if isinstance(load, Mapping) else None,
            "model_load_request_path": load.get("request_path") if isinstance(load, Mapping) else None,
            "model_load_request_sha256": load.get("request_sha256") if isinstance(load, Mapping) else None,
            "model_load_response_path": load.get("response_path") if isinstance(load, Mapping) else None,
            "model_load_response_sha256": load.get("response_sha256") if isinstance(load, Mapping) else None,
            "model_load_operation_store_record_path": load.get("operation_store_record_path")
                if isinstance(load, Mapping) else None,
            "model_load_operation_store_record_sha256": load.get("operation_store_record_sha256")
                if isinstance(load, Mapping) else None,
            "configuration_readback_idempotency_key": readback.get("idempotency_key")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_request_id": readback.get("request_id")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_request_path": readback.get("request_path")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_request_sha256": readback.get("request_sha256")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_response_path": readback.get("response_path")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_response_sha256": readback.get("response_sha256")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_control_identity": readback.get("control_identity")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_operation_store_record_path": readback.get("operation_store_record_path")
                if isinstance(readback, Mapping) else None,
            "configuration_readback_operation_store_record_sha256":
                readback.get("operation_store_record_sha256") if isinstance(readback, Mapping) else None,
            "birth_budget_binding": slot_receipt.get("birth_budget_binding"),
            "birth_budget_at_readback": slot_receipt.get("birth_budget_at_readback"),
        }
        if (not isinstance(load, Mapping) or not isinstance(readback, Mapping) or
                any(row.get(field) != value for field, value in duplicated_identity.items())):
            raise CampaignError(f"transition manifest omits the exact model_load/readback OperationStore identity for {key}")
        slot_observed = _validate_birth_budget_observation(
            slot_receipt.get("birth_budget_at_readback"),
            expected_binding=expected_budget_binding,
            label=f"{key} readmission slot budget")
        if slot_observed + 1e-5 < started_observed or slot_observed > finished_observed + 1e-5:
            raise CampaignError(f"{key} readback budget observation falls outside the saved Worker transition")
        slot_budget_observations.append(slot_observed)
    if (slot_budget_observations != sorted(slot_budget_observations) or
            any(right + 1e-5 < left for left, right in
                zip(slot_budget_observations, slot_budget_observations[1:]))):
        raise CampaignError("readmission slot budget observations are not chronological")
    return manifest


def loaded_binding_revision_delta(final_binding: Mapping[str, Any], loaded_binding: Mapping[str, Any]) -> int:
    """Return the final minus pre-readback revision after rejecting malformed values."""
    final_revision = final_binding.get("revision")
    loaded_revision = loaded_binding.get("revision")
    if (isinstance(final_revision, bool) or type(final_revision) is not int or
            isinstance(loaded_revision, bool) or type(loaded_revision) is not int):
        return -1
    return final_revision - loaded_revision


def _copy_source(workspace: Path, source: Path, expected_sha256: str) -> Path:
    if (source.is_symlink() or not source.is_file() or
            not SHA256_RE.fullmatch(expected_sha256) or sha256_file(source) != expected_sha256):
        raise CampaignError(f"reviewed source bytes changed: {source.name}")
    destination = _project_path(workspace, workspace / source.name,
                                must_exist=(workspace / source.name).exists())
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or sha256_file(destination) != expected_sha256:
            raise CampaignError(f"registered workspace has different bytes for {source.name}")
    else:
        with source.open("rb") as reader, destination.open("xb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
    if sha256_file(destination) != expected_sha256:
        raise CampaignError(f"project copy of {source.name} failed its SHA-256 check")
    return destination


def _read_sensitivity_ledger(path: Path, slots: list[Mapping[str, Any]],
                             approval_sha256: str, campaign_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise CampaignError("sensitivity solve ledger is aliased or not a regular file")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignError(f"sensitivity ledger row {line_number} is invalid JSON") from exc
        if len(rows) >= len(slots) or not isinstance(row, Mapping):
            raise CampaignError("sensitivity ledger has more than fourteen or malformed rows")
        slot = slots[len(rows)]
        expected = {
            "event": "study_run_submitted",
            "submission_index": slot["submission_index"],
            "configuration_id": slot["configuration_id"],
            "case_id": slot["case_id"],
            "study_tag": "stdShape",
            "model_tag": slot["model_tag"],
            "slot_idempotency_key": slot["slot_idempotency_key"],
            "approval_sha256": approval_sha256,
            "campaign_id": campaign_id,
        }
        if dict(row) != expected:
            raise CampaignError("sensitivity ledger prefix differs from the approved ordered slots")
        rows.append(dict(row))
    return rows


def _project_output_bytes(output_dir: Path) -> int:
    total = 0
    for path in output_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            raise CampaignError("project outputs may contain only regular files during the 14-slot campaign")
        total += path.stat().st_size
    return total


def _replace_campaign_receipt_fsynced(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably publish the current campaign receipt without following aliases."""
    if path.is_symlink() or not path.is_file():
        raise CampaignError("campaign receipt must remain a regular file while the campaign is active")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False, default=str) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or not path.is_file():
            raise CampaignError("campaign receipt identity changed before its atomic update")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def readmit_sensitivity_setup_artifacts(
    managed_runner: Any,
    *,
    historical_setup_receipt_paths: Mapping[str, Path],
    expected_historical_setup_receipt_sha256: Mapping[str, str],
    expected_setup_fixture_source_sha256: str,
    expected_setup_readback_source_sha256: str,
    transition_id: str,
    birth_budget: BirthBudget,
    maximum_transition_wall_time_s: int,
) -> dict[str, Any]:
    """Load and re-prove the fourteen hash-bound unsolved MPH files on one new Worker.

    This is a setup/readback-only phase.  It never calls Study.run, and every
    model_load/readback gets a durable local intent before dispatch.  A
    timeout, exception, or nonterminal response ends this transition without
    retry; the transition directory makes the same campaign id non-replayable.
    """
    if not isinstance(birth_budget, BirthBudget):
        raise CampaignError("new-Worker artifact readmission requires the exact Worker birth wall-clock budget")
    supplied_budget_identity = _birth_budget_identity(
        birth_budget, label="new-Worker artifact readmission budget")
    if (isinstance(maximum_transition_wall_time_s, bool) or
            type(maximum_transition_wall_time_s) is not int or
            not 1 <= maximum_transition_wall_time_s <= 3600):
        raise CampaignError("readmission requires a positive transition wall-time ceiling no greater than one hour")
    if (not isinstance(transition_id, str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", transition_id)):
        raise CampaignError("readmission transition_id must be a stable 8..128 character approved identifier")
    project_id = getattr(managed_runner, "project_id", None)
    workspace_arg = Path(getattr(managed_runner, "workspace", ""))
    if (not isinstance(project_id, str) or not project_id or workspace_arg.is_symlink() or
            not workspace_arg.exists()):
        raise CampaignError("readmission requires the exact registered project and real workspace")
    workspace = workspace_arg.resolve(strict=True)
    if not workspace.is_dir():
        raise CampaignError("readmission project workspace is not a directory")
    evidence_arg = Path(getattr(managed_runner, "evidence_dir", ""))
    if evidence_arg.is_symlink() or not evidence_arg.is_dir():
        raise CampaignError("readmission evidence directory must already be a real project-owned directory")
    evidence_dir = evidence_arg.resolve(strict=True)
    _project_path(workspace, evidence_dir / "readmission-anchor", must_exist=False)
    if not SHA256_RE.fullmatch(expected_setup_fixture_source_sha256) or not SHA256_RE.fullmatch(
            expected_setup_readback_source_sha256):
        raise CampaignError("readmission source SHA-256 pins are malformed")
    source_paths = getattr(managed_runner, "source_paths", None)
    if not isinstance(source_paths, Mapping):
        raise CampaignError("readmission runner cannot verify the project-copied setup sources")
    for role, expected in (("fixture", expected_setup_fixture_source_sha256),
                           ("readback", expected_setup_readback_source_sha256)):
        source = source_paths.get(role)
        if (not isinstance(source, (str, os.PathLike)) or Path(source).is_symlink() or
                not Path(source).is_file() or sha256_file(Path(source)) != expected):
            raise CampaignError(f"readmission runner project source is absent or has changed: {role}")
    source_hashes = {
        "fixture": expected_setup_fixture_source_sha256,
        "readback": expected_setup_readback_source_sha256,
    }

    if (not isinstance(historical_setup_receipt_paths, Mapping) or
            set(historical_setup_receipt_paths) != set(CAPTURE_KEY_ORDER) or
            not isinstance(expected_historical_setup_receipt_sha256, Mapping) or
            set(expected_historical_setup_receipt_sha256) != set(CAPTURE_KEY_ORDER) or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in expected_historical_setup_receipt_sha256.values())):
        raise CampaignError("readmission requires exactly fourteen separately hash-pinned historical setup receipts")

    parent_binding = getattr(managed_runner, "parent_binding", None)
    verify_binding = getattr(managed_runner, "_verify_persisted_binding", None)
    inspect_binding = getattr(managed_runner, "_inspect", None)
    java_action = getattr(managed_runner, "_java_action", None)
    study_ledger = getattr(managed_runner, "_study_run_ledger", None)
    if (not isinstance(parent_binding, ManagedModelBinding) or parent_binding.project_id != project_id or
            not all(callable(item) for item in (verify_binding, inspect_binding, java_action, study_ledger))):
        raise CampaignError("readmission requires the production managed runner's current Worker routes")
    parent_epoch = _model_epoch(parent_binding)
    verify_binding(parent_binding)
    birth_budget_binding = _birth_budget_binding_record(birth_budget, parent_epoch)
    if time.time() >= (supplied_budget_identity["deadline_epoch_s"] -
                       supplied_budget_identity["cleanup_reserve_s"]):
        raise CampaignError("new-Worker readmission budget is expired or already in its reserved cleanup window")

    operation_store_path = _operation_store_path(managed_runner)
    store_stat = operation_store_path.stat()
    store_identity = (store_stat.st_dev, store_stat.st_ino)
    prior_solve_rows = list(study_ledger())
    if prior_solve_rows:
        raise CampaignError("science project already has Study.run submissions; no re-admission or ledger reset is permitted")

    historical: dict[str, dict[str, Any]] = {}
    historical_bindings: dict[str, dict[str, Any]] = {}
    old_epochs: set[tuple[str, int, str]] = set()
    seen_old_refs: set[str] = set()
    for key in CAPTURE_KEY_ORDER:
        configuration_id, case_id = key.split(":", 1)
        path = _project_path(workspace, Path(historical_setup_receipt_paths[key]), must_exist=True)
        digest = expected_historical_setup_receipt_sha256[key]
        if sha256_file(path) != digest:
            raise CampaignError(f"{key} historical setup receipt differs from its separately frozen SHA-256")
        raw = _read_json_mapping(path, label=f"{key} historical setup receipt")
        binding_record = raw.get("reopened_model_binding")
        if not isinstance(binding_record, Mapping):
            raise CampaignError(f"{key} historical setup receipt omitted its retired model binding")
        historic_binding = _binding_record(
            ManagedModelBinding(project_id, str(binding_record.get("session_id", "")),
                                dict(binding_record.get("model_ref", {})),
                                binding_record.get("revision")),
            project_id=project_id, case_id=key)
        old_epochs.add(_model_epoch(ManagedModelBinding(
            project_id, historic_binding["session_id"], historic_binding["model_ref"],
            historic_binding["revision"])))
        ref_identity = json.dumps(historic_binding["model_ref"], sort_keys=True, separators=(",", ":"))
        if ref_identity in seen_old_refs:
            raise CampaignError("historical setup receipts must retain fourteen distinct retired ModelRefs")
        seen_old_refs.add(ref_identity)
        original = _load_approved_sensitivity_setup_receipt(
            path, configuration_id=configuration_id, case_id=case_id,
            workspace=workspace, project_id=project_id, binding_record=historic_binding,
            expected_receipt_sha256=digest,
            expected_fixture_sha256=expected_setup_fixture_source_sha256,
            expected_readback_sha256=expected_setup_readback_source_sha256,
        )
        historical[key] = original
        historical_bindings[key] = historic_binding
    if len(old_epochs) != 1:
        raise CampaignError("all historical setup receipts must identify one exact retired Worker epoch")
    historical_epoch = next(iter(old_epochs))
    if historical_epoch == parent_epoch:
        raise CampaignError("science readmission Worker must be a different epoch from all historical setup refs")

    readmission_root = evidence_dir / "static_shape_sensitivity_readmissions"
    if readmission_root.exists():
        if readmission_root.is_symlink() or not readmission_root.is_dir():
            raise CampaignError("readmission evidence root is aliased or not a directory")
    else:
        readmission_root.mkdir(mode=0o700)
    transition_dir = readmission_root / transition_id
    _project_path(workspace, transition_dir, must_exist=False)
    try:
        transition_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise CampaignError("this readmission transition already has evidence; no retry or overwrite is permitted") from exc
    slots_dir = transition_dir / "slots"
    requests_dir = transition_dir / "requests"
    responses_dir = transition_dir / "responses"
    slots_dir.mkdir(mode=0o700)
    requests_dir.mkdir(mode=0o700)
    responses_dir.mkdir(mode=0o700)
    manifest_path = transition_dir / "transition_manifest.json"
    events_path = transition_dir / "transition_events.jsonl"
    started_epoch_s = time.time()
    work_deadline = min(birth_budget.deadline_epoch_s,
                        started_epoch_s + maximum_transition_wall_time_s)
    manifest: dict[str, Any] = {
        "schema": READMISSION_MANIFEST_SCHEMA,
        "status": "RUNNING",
        "native_acceptance": "NOT_RUN",
        "transition_id": transition_id,
        "project_id": project_id,
        "project_workspace": str(workspace),
        "operation_store_path": str(operation_store_path),
        "operation_store_identity": {"device": store_identity[0], "inode": store_identity[1]},
        "historical_worker_epoch": {"session_id": historical_epoch[0],
                                     "generation": historical_epoch[1],
                                     "server_instance_id": historical_epoch[2]},
        "science_worker_epoch": {"session_id": parent_epoch[0], "generation": parent_epoch[1],
                                 "server_instance_id": parent_epoch[2]},
        "science_worker_parent_binding": parent_binding.as_record(),
        "birth_budget_binding": birth_budget_binding,
        "source_sha256": source_hashes,
        "expected_historical_setup_receipt_sha256": dict(expected_historical_setup_receipt_sha256),
        "planned_model_loads": len(CAPTURE_KEY_ORDER),
        "completed_model_loads": 0,
        "study_run_submissions_before": prior_solve_rows,
        "study_run_submissions_after": None,
        "maximum_transition_wall_time_s": maximum_transition_wall_time_s,
        "transition_started_epoch_s": started_epoch_s,
        "birth_budget_at_start": birth_budget.receipt(),
        "slots": {},
        "status_detail": "artifact hash validation passed before first model_load",
    }
    _write_json_fsynced(manifest_path, manifest)
    _append_jsonl_fsynced(events_path, {
        "event": "readmission_transition_started", "transition_id": transition_id,
        "planned_model_loads": len(CAPTURE_KEY_ORDER),
        "historical_worker_epoch": manifest["historical_worker_epoch"],
        "science_worker_epoch": manifest["science_worker_epoch"],
    })

    def persist_manifest() -> None:
        _replace_campaign_receipt_fsynced(manifest_path, manifest)

    def rpc_timeout(label: str) -> float:
        now = time.time()
        remaining = min(birth_budget.deadline_epoch_s, work_deadline) - now - birth_budget.cleanup_reserve_s
        if not math.isfinite(remaining) or remaining <= 1.0:
            raise CampaignError(f"readmission reached the reserved cleanup window before {label}")
        return min(birth_budget.rpc_timeout_s(120.0, now_epoch_s=now), remaining)

    def inspect_with_budget(binding: ManagedModelBinding, label: str):
        prior_timeout = getattr(managed_runner, "timeout_s", None)
        timeout = rpc_timeout(label)
        if prior_timeout is not None:
            managed_runner.timeout_s = timeout
        try:
            return inspect_binding(binding)
        finally:
            if prior_timeout is not None:
                managed_runner.timeout_s = prior_timeout

    try:
        for key in CAPTURE_KEY_ORDER:
            config_id, case_id = key.split(":", 1)
            original = historical[key]
            old_path = _project_path(
                workspace, Path(historical_setup_receipt_paths[key]), must_exist=True)
            artifact = original["project_artifact"]
            artifact_path = _project_path(workspace, Path(artifact["path"]), must_exist=True)
            if (sha256_file(old_path) != expected_historical_setup_receipt_sha256[key] or
                    sha256_file(artifact_path) != artifact["sha256"]):
                raise CampaignError(f"{key} historical receipt or saved MPH changed after readmission preflight")
            idempotency_key, request_id = _readmission_operation_identity(
                project_id=project_id,
                configuration_id=config_id, case_id=case_id,
                artifact_sha256=artifact["sha256"], worker_epoch=parent_epoch,
                purpose="model-load")
            call_id = idempotency_key.removeprefix("w24-shape-readmit-model-load-")
            current_store = _operation_store_path(managed_runner)
            current_stat = current_store.stat()
            if (current_store != operation_store_path or
                    (current_stat.st_dev, current_stat.st_ino) != store_identity):
                raise CampaignError("OperationStore identity changed during the readmission transition")
            timeout = rpc_timeout(f"model_load {key}")
            request = {
                "operation": "model_load",
                "arguments": {"path": str(artifact_path)},
                "execution": {
                    "project_id": project_id,
                    "session_id": parent_binding.session_id,
                    "idempotency_key": idempotency_key,
                    "request_id": request_id,
                    "rpc_timeout_s": timeout,
                    "queue_timeout_s": min(60.0, timeout),
                    "execution_timeout_s": None,
                },
            }
            request_path = requests_dir / f"{call_id}.json"
            response_path = responses_dir / f"{call_id}.json"
            store_evidence_path = responses_dir / f"{call_id}.store.json"
            request_evidence = {
                "schema": READMISSION_REQUEST_SCHEMA,
                "transition_id": transition_id,
                "slot_id": key,
                "action": "model_load",
                "source": {
                    "artifact_path": str(artifact_path),
                    "artifact_sha256": artifact["sha256"],
                    "historical_setup_receipt_path": str(old_path),
                    "historical_setup_receipt_sha256": expected_historical_setup_receipt_sha256[key],
                },
                "request": request,
            }
            _write_json_fsynced(request_path, request_evidence)
            request_sha256 = sha256_file(request_path)
            intent = {
                "event": "model_load_dispatch_started", "slot": key,
                "call_id": call_id, "idempotency_key": idempotency_key,
                "request_id": request_id, "request_path": str(request_path),
                "request_sha256": request_sha256,
                "historical_setup_receipt_path": str(old_path),
                "historical_setup_receipt_sha256": expected_historical_setup_receipt_sha256[key],
                "artifact_path": str(artifact_path), "artifact_sha256": artifact["sha256"],
            }
            _append_jsonl_fsynced(events_path, intent)
            manifest["active_slot"] = key
            manifest["active_slot_intent"] = intent
            persist_manifest()
            try:
                response = managed_runner.daemon.dispatch(request)
            except BaseException as exc:
                _append_jsonl_fsynced(events_path, {
                    "event": "model_load_dispatch_unknown", "slot": key,
                    "idempotency_key": idempotency_key,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{type(exc).__name__}: {exc}"
                manifest["stopped_at_slot"] = key
                manifest["birth_budget_at_failure"] = birth_budget.receipt()
                persist_manifest()
                raise CampaignError(f"{key} model_load outcome is unknown; this transition is non-replayable") from exc
            _write_json_fsynced(response_path, response if isinstance(response, Mapping) else {
                "unstructured_response": repr(response)})
            if not isinstance(response, Mapping) or response.get("success") is not True:
                _append_jsonl_fsynced(events_path, {
                    "event": "model_load_dispatch_not_succeeded", "slot": key,
                    "idempotency_key": idempotency_key,
                })
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{key} model_load did not return success=true"
                manifest["stopped_at_slot"] = key
                persist_manifest()
                raise CampaignError(manifest["error"])
            try:
                load_store_evidence = _capture_operation_store_identity(
                    managed_runner, request=request, response=response,
                    snapshot_path=store_evidence_path)
            except CampaignError as exc:
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{type(exc).__name__}: {exc}"
                manifest["stopped_at_slot"] = key
                manifest["birth_budget_at_failure"] = birth_budget.receipt()
                persist_manifest()
                raise
            try:
                loaded = ManagedModelBinding.from_load_response(response, project_id=project_id)
                _validate_model_load_response_source(response, artifact_path, label=key)
            except CampaignError as exc:
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{type(exc).__name__}: {exc}"
                manifest["stopped_at_slot"] = key
                persist_manifest()
                raise
            load_execution = response.get("execution")
            if (not isinstance(load_execution, Mapping) or
                    (load_execution.get("project_id") is not None and
                     load_execution.get("project_id") != project_id) or
                    load_execution.get("session_id") != parent_binding.session_id or
                    load_execution.get("model_ref") != dict(loaded.model_ref) or
                    load_execution.get("revision") != loaded.revision):
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{key} model_load response omitted or changed its exact project/Worker ModelRef/revision"
                manifest["stopped_at_slot"] = key
                persist_manifest()
                raise CampaignError(manifest["error"])
            if loaded.session_id != parent_binding.session_id or _model_epoch(loaded) != parent_epoch:
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{key} model_load returned a stale or foreign Worker epoch"
                manifest["stopped_at_slot"] = key
                persist_manifest()
                raise CampaignError(manifest["error"])
            if _model_epoch(loaded) == _model_epoch(ManagedModelBinding(
                    project_id, historical[key]["reopened_model_binding"]["session_id"],
                    dict(historical[key]["reopened_model_binding"]["model_ref"]),
                    historical[key]["reopened_model_binding"]["revision"])):
                raise CampaignError(f"{key} model_load returned the retired historical Worker ref")
            verify_binding(loaded)
            if loaded.model_tag in {item.get("new_binding", {}).get("model_tag")
                                    for item in manifest["slots"].values()}:
                raise CampaignError("model_load returned a duplicate ModelRef tag for two sensitivity slots")

            loaded_inspected, inspection = inspect_with_budget(loaded, f"pre-readback inspect {key}")
            inspection_data = inspection.get("data") if isinstance(inspection, Mapping) else None
            if (loaded_inspected.as_record() != loaded.as_record() or
                    not isinstance(inspection_data, Mapping) or
                    inspection_data.get("model_identity") != dict(loaded.model_ref) or
                    inspection_data.get("revision") != loaded.revision):
                raise CampaignError(f"{key} read-only pre-readback inspection changed or misidentified the loaded binding")
            readback_started_binding = loaded.as_record()
            if time.time() >= min(birth_budget.deadline_epoch_s, work_deadline) - birth_budget.cleanup_reserve_s:
                raise CampaignError(f"readmission reached the reserved cleanup window before full readback {key}")
            readback_idempotency_key, readback_request_id = _readmission_operation_identity(
                project_id=project_id,
                configuration_id=config_id, case_id=case_id,
                artifact_sha256=artifact["sha256"], worker_epoch=parent_epoch,
                purpose="configuration-readback")
            readback_call_id = readback_idempotency_key.removeprefix(
                "w24-shape-readmit-configuration-readback-")
            readback_request_path = requests_dir / f"{readback_call_id}.json"
            readback_response_path = responses_dir / f"{readback_call_id}.json"
            readback_store_evidence_path = responses_dir / f"{readback_call_id}.store.json"
            readback_source_path = _project_path(
                workspace, workspace / SETUP_READBACK_SOURCE.name, must_exist=True)
            if sha256_file(readback_source_path) != expected_setup_readback_source_sha256:
                raise CampaignError(f"{key} project-copied readback Java source changed before dispatch")
            readback_intent = {
                "event": "configuration_readback_dispatch_started", "slot": key,
                "model_load_idempotency_key": idempotency_key,
                "idempotency_key": readback_idempotency_key,
                "request_id": readback_request_id,
                "request_path": str(readback_request_path),
                "model_binding": loaded.as_record(),
            }
            _append_jsonl_fsynced(events_path, readback_intent)
            manifest["active_readback_intent"] = readback_intent
            persist_manifest()
            timeout_before = getattr(managed_runner, "timeout_s", None)
            readback_timeout = rpc_timeout(f"full configuration/getSize readback {key}")
            if timeout_before is not None:
                managed_runner.timeout_s = readback_timeout
            try:
                post_readback, readback_response, readback_raw = java_action(
                    loaded, source_role="readback", entrypoint="W24StaticShapeReadback#run",
                    arguments={"action": "readback", "expected_configuration_id": config_id},
                    idempotency_key=readback_idempotency_key,
                    request_id=readback_request_id,
                    request_capture_path=readback_request_path,
                    request_capture_metadata={
                        "transition_id": transition_id,
                        "slot_id": key,
                        "action": "configuration_readback",
                        "source": {
                            "role": "readback",
                            "path": str(readback_source_path),
                            "sha256": expected_setup_readback_source_sha256,
                        },
                    },
                )
            finally:
                if timeout_before is not None:
                    managed_runner.timeout_s = timeout_before
            _write_json_fsynced(readback_response_path, readback_response)
            readback_response_sha256 = sha256_file(readback_response_path)
            try:
                readback_request_record = _read_json_mapping(
                    readback_request_path, label=f"{key} Java readback request evidence")
                actual_readback_request = readback_request_record.get("request")
                readback_store_evidence = _capture_operation_store_identity(
                    managed_runner, request=actual_readback_request,
                    response=readback_response, snapshot_path=readback_store_evidence_path)
            except (CampaignError, TypeError) as exc:
                manifest["status"] = "FAIL_OR_UNKNOWN_NO_RETRY"
                manifest["error"] = f"{type(exc).__name__}: {exc}"
                manifest["stopped_at_slot"] = key
                manifest["birth_budget_at_failure"] = birth_budget.receipt()
                persist_manifest()
                if isinstance(exc, CampaignError):
                    raise
                raise CampaignError(f"{key} saved Java request evidence is malformed") from exc
            readback_request_sha256 = sha256_file(readback_request_path)
            readback = StaticShapeManagedRunner._validate_shape_readback(readback_raw, case_id, config_id)
            if (readback.get("model_tag") != loaded.model_tag or
                    post_readback.project_id != project_id or
                    post_readback.session_id != loaded.session_id or
                    dict(post_readback.model_ref) != dict(loaded.model_ref) or
                    post_readback.revision != loaded.revision + 1):
                raise CampaignError(f"{key} Java readback did not produce the exact approved one-revision transition")
            old_config = historical[key]["reopened_configuration_readback"]
            if ({name: value for name, value in old_config.items() if name != "model_tag"} !=
                    {name: value for name, value in readback.items() if name != "model_tag"}):
                raise CampaignError(f"{key} reloaded MPH differs from the historic complete configuration/mesh/max-step/getSize proof")
            if readback.get("study_run_calls_this_action") != 0:
                raise CampaignError(f"{key} full setup readback unexpectedly invoked Study.run")
            StaticShapeManagedRunner._validate_no_stored_solution_data(readback.get("solution_state_readback"))
            verify_binding(post_readback)
            final_inspected, final_inspection = inspect_with_budget(post_readback, f"final inspect {key}")
            final_data = final_inspection.get("data") if isinstance(final_inspection, Mapping) else None
            if (final_inspected.as_record() != post_readback.as_record() or
                    not isinstance(final_data, Mapping) or
                    final_data.get("model_identity") != dict(post_readback.model_ref) or
                    final_data.get("revision") != post_readback.revision):
                raise CampaignError(f"{key} final read-only identity inspection did not confirm the readback revision")
            study_rows_after = list(study_ledger())
            if study_rows_after != prior_solve_rows or study_rows_after:
                raise CampaignError(f"{key} setup readmission changed the zero-Study.run project ledger")

            binding_record = post_readback.as_record()
            birth_budget_at_readback = birth_budget.receipt()
            slot_receipt = {
                "schema": READMISSION_SCHEMA,
                "status": "SCIENCE_WORKER_ARTIFACT_READMIT_READBACK_COMPLETE",
                "native_acceptance": "NOT_RUN",
                "project_id": project_id,
                "project_workspace": str(workspace),
                "operation_store_path": str(operation_store_path),
                "operation_store_identity": {"device": store_identity[0], "inode": store_identity[1]},
                "transition_id": transition_id,
                "configuration_id": config_id,
                "case_id": case_id,
                "source_sha256": dict(source_hashes),
                "historical_setup_receipt_path": str(old_path),
                "historical_setup_receipt_sha256": expected_historical_setup_receipt_sha256[key],
                "historical_model_binding": historical[key]["reopened_model_binding"],
                "project_artifact": dict(artifact),
                "model_load": {
                    "status": "SUCCEEDED", "slot_id": key,
                    "idempotency_key": idempotency_key, "request_id": request_id,
                    "request_path": str(request_path), "request_sha256": request_sha256,
                    "response_path": str(response_path),
                    "response_sha256": sha256_file(response_path),
                    "operation_store_record_path": load_store_evidence["path"],
                    "operation_store_record_sha256": load_store_evidence["sha256"],
                    "control_identity": load_store_evidence["response_execution"],
                    "source_artifact_path": str(artifact_path),
                    "source_artifact_sha256": artifact["sha256"],
                    "historical_setup_receipt_path": str(old_path),
                    "historical_setup_receipt_sha256": expected_historical_setup_receipt_sha256[key],
                },
                "configuration_readback": {
                    "status": "SUCCEEDED", "slot_id": key,
                    "idempotency_key": readback_idempotency_key,
                    "request_id": readback_request_id,
                    "request_path": str(readback_request_path),
                    "request_sha256": readback_request_sha256,
                    "response_path": str(readback_response_path),
                    "response_sha256": readback_response_sha256,
                    "operation_store_record_path": readback_store_evidence["path"],
                    "operation_store_record_sha256": readback_store_evidence["sha256"],
                    "control_identity": readback_store_evidence["response_execution"],
                    "source_path": str(readback_source_path),
                    "source_sha256": expected_setup_readback_source_sha256,
                },
                "current_worker_epoch": {
                    "session_id": post_readback.session_id,
                    "generation": post_readback.model_ref["generation"],
                    "server_instance_id": post_readback.model_ref["server_instance_id"],
                },
                "reopened_configuration_readback_binding": readback_started_binding,
                "reopened_configuration_readback": dict(readback),
                "readback_comparison": {
                    "matches": True, "interpolation_used": False,
                    "comparison": "exact JSON-native configuration values",
                    "compared_fields": "all fields except the Worker-local model_tag",
                },
                "reopened_model_binding": binding_record,
                "reopened_model_identity": {
                    "model_binding": final_inspected.as_record(),
                    "data": dict(final_data),
                },
                "study_run_submissions_before": prior_solve_rows,
                "study_run_submissions_after": study_rows_after,
                "study_run_calls_this_action": 0,
                "birth_budget_binding": birth_budget_binding,
                "birth_budget_at_readback": birth_budget_at_readback,
            }
            slot_receipt_path = slots_dir / f"{config_id}__{case_id}.json"
            _write_json_fsynced(slot_receipt_path, slot_receipt)
            slot_digest = sha256_file(slot_receipt_path)
            manifest["slots"][key] = {
                "historical_setup_receipt_sha256": expected_historical_setup_receipt_sha256[key],
                "artifact_path": artifact["path"],
                "artifact_sha256": artifact["sha256"],
                "readmission_receipt_path": str(slot_receipt_path),
                "readmission_receipt_sha256": slot_digest,
                "new_binding": binding_record,
                "model_load_control_identity": load_store_evidence["response_execution"],
                "model_load_request_path": str(request_path),
                "model_load_request_sha256": request_sha256,
                "model_load_response_path": str(response_path),
                "model_load_response_sha256": sha256_file(response_path),
                "model_load_operation_store_record_path": load_store_evidence["path"],
                "model_load_operation_store_record_sha256": load_store_evidence["sha256"],
                "configuration_readback_idempotency_key": readback_idempotency_key,
                "configuration_readback_request_id": readback_request_id,
                "configuration_readback_request_path": str(readback_request_path),
                "configuration_readback_request_sha256": readback_request_sha256,
                "configuration_readback_response_path": str(readback_response_path),
                "configuration_readback_response_sha256": readback_response_sha256,
                "configuration_readback_control_identity": readback_store_evidence["response_execution"],
                "configuration_readback_operation_store_record_path": readback_store_evidence["path"],
                "configuration_readback_operation_store_record_sha256": readback_store_evidence["sha256"],
                "birth_budget_binding": birth_budget_binding,
                "birth_budget_at_readback": birth_budget_at_readback,
            }
            manifest["completed_model_loads"] = len(manifest["slots"])
            manifest.pop("active_slot", None)
            manifest.pop("active_slot_intent", None)
            manifest.pop("active_readback_intent", None)
            persist_manifest()
            _append_jsonl_fsynced(events_path, {
                "event": "model_load_and_full_readback_completed", "slot": key,
                "model_tag": post_readback.model_tag,
                "readback_receipt_sha256": slot_digest,
                "current_worker_epoch": manifest["science_worker_epoch"],
            })

        if set(manifest["slots"]) != set(CAPTURE_KEY_ORDER):
            raise CampaignError("readmission completed without exactly fourteen unique config/case receipts")
        final_rows = list(study_ledger())
        if final_rows != prior_solve_rows or final_rows:
            raise CampaignError("readmission finished with a changed or nonempty Study.run ledger")
        transition_finished_epoch_s = time.time()
        manifest.update({
            "status": "SCIENCE_WORKER_READMISSION_COMPLETE",
            "study_run_submissions_after": final_rows,
            "transition_finished_epoch_s": transition_finished_epoch_s,
            "transition_elapsed_s": max(0.0, transition_finished_epoch_s - started_epoch_s),
            "birth_budget_at_finish": birth_budget.receipt(),
            "native_acceptance": "NOT_RUN",
        })
        _append_jsonl_fsynced(events_path, {
            "event": "readmission_transition_complete", "status": manifest["status"],
            "completed_model_loads": manifest["completed_model_loads"],
            "study_run_submissions": 0,
        })
        persist_manifest()
        return {
            "status": manifest["status"],
            "transition_id": transition_id,
            "manifest_path": manifest_path,
            "manifest_sha256": sha256_file(manifest_path),
            "manifest": dict(manifest),
            "bindings": {
                key: ManagedModelBinding(
                    project_id, item["new_binding"]["session_id"],
                    item["new_binding"]["model_ref"], item["new_binding"]["revision"])
                for key, item in manifest["slots"].items()
            },
            "setup_receipt_paths": {
                key: Path(item["readmission_receipt_path"])
                for key, item in manifest["slots"].items()
            },
            "setup_receipt_sha256": {
                key: item["readmission_receipt_sha256"]
                for key, item in manifest["slots"].items()
            },
            "historical_setup_receipt_sha256": dict(expected_historical_setup_receipt_sha256),
            "historical_setup_receipt_paths": {
                key: _project_path(workspace, Path(historical_setup_receipt_paths[key]), must_exist=True)
                for key in CAPTURE_KEY_ORDER
            },
            "birth_budget_binding": birth_budget_binding,
            "birth_budget_at_finish": birth_budget.receipt(),
        }
    except BaseException as exc:
        if manifest.get("status") == "RUNNING":
            manifest.update({
                "status": "FAIL_OR_UNKNOWN_NO_RETRY",
                "error": f"{type(exc).__name__}: {exc}",
                "stopped_at_slot": manifest.get("active_slot"),
                "transition_failed_epoch_s": time.time(),
                "transition_elapsed_s": max(0.0, time.time() - started_epoch_s),
                "birth_budget_at_failure": birth_budget.receipt(),
            })
            _append_jsonl_fsynced(events_path, {
                "event": "readmission_transition_stopped_no_retry",
                "stopped_at_slot": manifest.get("stopped_at_slot"),
                "error": manifest["error"],
            })
            persist_manifest()
        raise


def execute_sensitivity_campaign(
    managed_runner: Any,
    bindings: Mapping[str, ManagedModelBinding],
    *,
    setup_receipt_paths: Mapping[str, Path],
    historical_setup_receipt_sha256: Mapping[str, str],
    readmission_manifest_path: Path,
    expected_readmission_manifest_sha256: str,
    readmission_transition_id: str,
    approval_path: Path,
    expected_approval_sha256: str,
    expected_study_run_source_sha256: str,
    expected_capture_source_sha256: str,
    expected_science_executor_sha256: str,
    expected_setup_fixture_source_sha256: str,
    expected_setup_readback_source_sha256: str,
    birth_budget: BirthBudget,
    solve_ledger_path: Path,
) -> dict[str, Any]:
    """Execute one separately approved fourteen-run campaign, stopping on uncertainty."""
    if not isinstance(birth_budget, BirthBudget):
        raise CampaignError("14-slot W24 sensitivity requires its separately frozen owned-birth wall-clock budget")
    supplied_budget_identity = _birth_budget_identity(
        birth_budget, label="14-slot science campaign Worker birth budget")
    project_id = getattr(managed_runner, "project_id", None)
    supplied_workspace = Path(getattr(managed_runner, "workspace", ""))
    if not isinstance(project_id, str) or not project_id or supplied_workspace.is_symlink():
        raise CampaignError("managed runner lacks the exact registered sensitivity project/workspace")
    workspace = supplied_workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise CampaignError("registered sensitivity workspace is not a real directory")
    campaign_started = time.time()
    operation_store_path = _operation_store_path(managed_runner)
    store_stat = operation_store_path.stat()
    store_identity = (store_stat.st_dev, store_stat.st_ino)
    operation_store_identity = {"device": store_identity[0], "inode": store_identity[1]}
    source_sha = {
        "study_run": expected_study_run_source_sha256,
        "history_capture": expected_capture_source_sha256,
        "science_executor": expected_science_executor_sha256,
        "setup_fixture": expected_setup_fixture_source_sha256,
        "setup_readback": expected_setup_readback_source_sha256,
    }
    for role, source in (("study_run", STUDY_RUN_SOURCE), ("history_capture", CAPTURE_SOURCE),
                         ("science_executor", SCIENCE_EXECUTOR_SOURCE),
                         ("setup_fixture", SETUP_FIXTURE_SOURCE),
                         ("setup_readback", SETUP_READBACK_SOURCE)):
        if (not SHA256_RE.fullmatch(source_sha[role]) or source.is_symlink() or
                not source.is_file() or sha256_file(source) != source_sha[role]):
            raise CampaignError(f"sensitivity approval source hash is absent/stale for {role}")
    declared_sources = getattr(managed_runner, "source_paths", None)
    if not isinstance(declared_sources, Mapping):
        raise CampaignError("managed runner cannot verify project-copied setup sources")
    for role, expected in (("fixture", expected_setup_fixture_source_sha256),
                           ("readback", expected_setup_readback_source_sha256)):
        supplied = declared_sources.get(role)
        if not isinstance(supplied, (str, os.PathLike)):
            raise CampaignError(f"managed runner omitted project source path for {role}")
        path = Path(supplied)
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise CampaignError(f"project-copied {role} source is not the separately approved byte sequence")

    slots, binding_records = _ordered_slot_records(project_id, bindings)
    expected_epoch = _model_epoch(bindings[CAPTURE_KEY_ORDER[0]])
    expected_budget_binding = _birth_budget_binding_record(birth_budget, expected_epoch)
    if (not isinstance(setup_receipt_paths, Mapping) or
            set(setup_receipt_paths) != set(CAPTURE_KEY_ORDER)):
        raise CampaignError("14-slot sensitivity requires exactly fourteen new-Worker readmission receipt paths")
    if (not isinstance(historical_setup_receipt_sha256, Mapping) or
            set(historical_setup_receipt_sha256) != set(CAPTURE_KEY_ORDER) or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in historical_setup_receipt_sha256.values())):
        raise CampaignError("14-slot sensitivity requires the separately pinned fourteen historical setup receipt hashes")
    receipt_hashes: dict[str, str] = {}
    for key in CAPTURE_KEY_ORDER:
        supplied = setup_receipt_paths[key]
        if not isinstance(supplied, (str, os.PathLike)):
            raise CampaignError(f"{key} setup receipt path must be a project file")
        path = _project_path(workspace, Path(supplied), must_exist=True)
        receipt_hashes[key] = sha256_file(path)
    verified_setups: dict[str, dict[str, Any]] = {}
    for key in CAPTURE_KEY_ORDER:
        config_id, case_id = key.split(":", 1)
        receipt, _historical = _load_approved_sensitivity_readmission_receipt(
            setup_receipt_paths[key], configuration_id=config_id, case_id=case_id,
            workspace=workspace, project_id=project_id, binding_record=binding_records[key],
            expected_receipt_sha256=receipt_hashes[key],
            expected_historical_receipt_sha256=historical_setup_receipt_sha256[key],
            expected_fixture_sha256=expected_setup_fixture_source_sha256,
            expected_readback_sha256=expected_setup_readback_source_sha256,
            expected_transition_id=readmission_transition_id,
            expected_operation_store_path=operation_store_path,
            expected_operation_store_identity=operation_store_identity,
            expected_birth_budget_binding=expected_budget_binding,
        )
        verified_setups[key] = receipt
    manifest = _load_approved_readmission_manifest(
        readmission_manifest_path,
        expected_sha256=expected_readmission_manifest_sha256,
        expected_transition_id=readmission_transition_id,
        workspace=workspace, project_id=project_id,
        operation_store_path=operation_store_path,
        operation_store_identity=operation_store_identity,
        source_sha256={"fixture": expected_setup_fixture_source_sha256,
                       "readback": expected_setup_readback_source_sha256},
        expected_readmission_receipt_sha256=receipt_hashes,
        expected_historical_receipt_sha256=historical_setup_receipt_sha256,
        expected_bindings=bindings,
        expected_birth_budget=birth_budget,
    )
    approval = validate_sensitivity_approval(
        approval_path,
        expected_approval_sha256=expected_approval_sha256,
        expected_source_sha256=source_sha,
        expected_setup_receipt_sha256=receipt_hashes,
        expected_historical_setup_receipt_sha256=historical_setup_receipt_sha256,
        expected_readmission_manifest_sha256=expected_readmission_manifest_sha256,
        expected_readmission_transition_id=readmission_transition_id,
        expected_project_id=project_id,
        expected_workspace=workspace,
        expected_operation_store_path=operation_store_path,
        expected_bindings=bindings,
        expected_slots=slots,
        expected_birth_budget=birth_budget,
    )
    if time.time() >= (supplied_budget_identity["deadline_epoch_s"] -
                       supplied_budget_identity["cleanup_reserve_s"]):
        raise CampaignError("readmission Worker birth budget is expired or already in its reserved cleanup window")
    campaign_wall_origin = manifest["transition_started_epoch_s"]
    approved_campaign_deadline = min(
        birth_budget.deadline_epoch_s,
        campaign_wall_origin + approval["resource_limits"]["maximum_campaign_wall_time_s"] +
        birth_budget.cleanup_reserve_s,
    )

    def require_campaign_time(label: str) -> None:
        if time.time() >= approved_campaign_deadline - birth_budget.cleanup_reserve_s:
            raise CampaignError(f"approved sensitivity campaign wall-time limit reached {label}")

    def campaign_rpc_timeout(preferred_s: float) -> float:
        require_campaign_time("before a managed RPC")
        return min(birth_budget.rpc_timeout_s(preferred_s),
                   approved_campaign_deadline - time.time() - birth_budget.cleanup_reserve_s)
    ledger = _project_path(workspace, Path(solve_ledger_path), must_exist=False)
    exact_ledger = workspace / "outputs" / "static_shape_sensitivity_study_runs.jsonl"
    if ledger != exact_ledger or ledger.exists():
        raise CampaignError("sensitivity campaign requires a new exact 14-slot ledger; an existing/UNKNOWN ledger is never replayed")
    output_dir = workspace / "outputs"
    if output_dir.is_symlink() or not output_dir.is_dir() or output_dir.resolve(strict=True) != output_dir:
        raise CampaignError("registered project outputs directory is missing or aliased")
    if _read_sensitivity_ledger(ledger, slots, expected_approval_sha256, approval["campaign_id"]):
        raise CampaignError("sensitivity solve ledger is nonempty; refusing a fresh campaign or UNKNOWN replay")
    resource_limits = approval["resource_limits"]
    initial_output_bytes = _project_output_bytes(output_dir)
    if initial_output_bytes > resource_limits["maximum_total_project_output_bytes"]:
        raise CampaignError("prebuilt project artifacts already exceed the approved total output ceiling")
    evidence_dir = Path(getattr(managed_runner, "evidence_dir", ""))
    if (evidence_dir.is_symlink() or not evidence_dir.is_dir() or
            evidence_dir.resolve(strict=True) != evidence_dir):
        raise CampaignError("sensitivity evidence directory must be a real, non-aliased directory")
    receipt_path = evidence_dir / "static_shape_sensitivity_campaign_receipt.json"
    events = evidence_dir / f"static_shape_sensitivity_science_events_{approval['campaign_id']}.jsonl"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise CampaignError("a prior sensitivity campaign receipt exists; evidence and native work will not be overwritten")
    if events.exists() or events.is_symlink():
        raise CampaignError("this campaign id already has an event journal; a new attempt must not overwrite it")

    operation_store_path = _operation_store_path(managed_runner)
    stat_after = operation_store_path.stat()
    if operation_store_path != Path(approval["operation_store_path"]) or (
            stat_after.st_dev, stat_after.st_ino) != store_identity:
        raise CampaignError("OperationStore path/inode changed after approval validation")
    verifier = getattr(managed_runner, "_verify_persisted_binding", None)
    inspector = getattr(managed_runner, "_inspect", None)
    if not callable(verifier) or not callable(inspector):
        raise CampaignError("managed runner cannot verify persisted bindings through production read-only routes")
    for key in CAPTURE_KEY_ORDER:
        binding = bindings[key]
        verifier(binding)
    for key in CAPTURE_KEY_ORDER:
        binding = bindings[key]
        require_campaign_time("during the fourteen-model read-only preflight")
        old_timeout = getattr(managed_runner, "timeout_s", None)
        remaining = campaign_rpc_timeout(120.0)
        if old_timeout is not None:
            managed_runner.timeout_s = remaining
        try:
            updated, inspection = inspector(binding)
        finally:
            if old_timeout is not None:
                managed_runner.timeout_s = old_timeout
        if updated.as_record() != binding_records[key]:
            raise CampaignError(f"{key} read-only model.inspect changed the exact approved original revision")
        data = inspection.get("data") if isinstance(inspection, Mapping) else None
        if (not isinstance(data, Mapping) or data.get("model_identity") != binding.model_ref or
                data.get("revision") != binding.revision):
            raise CampaignError(f"{key} current identity inspection does not echo the approved ModelRef/revision")
        setup = verified_setups[key]
        config = setup["reopened_configuration_readback"]
        solution = config.get("solution_state_readback") if isinstance(config, Mapping) else None
        StaticShapeManagedRunner._validate_no_stored_solution_data(solution)

    solve_source_copy = _copy_source(workspace, STUDY_RUN_SOURCE, expected_study_run_source_sha256)
    capture_source_copy = _copy_source(workspace, CAPTURE_SOURCE, expected_capture_source_sha256)
    executor_source_copy = _copy_source(workspace, SCIENCE_EXECUTOR_SOURCE, expected_science_executor_sha256)
    for slot in slots:
        save = output_dir / (
            f"static_shape_{slot['configuration_id']}_{slot['case_id']}_solved.mph")
        if save.exists() or save.is_symlink():
            raise CampaignError("sensitivity solved-model output already exists; refusing to overwrite or re-solve")
    history_sha = _slot_history_sha256(slots)
    initial_outputs = _project_output_bytes(output_dir)
    receipt: dict[str, Any] = {
        "schema": "W24_STATIC_SHAPE_SENSITIVITY_SCIENCE_EXECUTION_V1",
        "status": "RUNNING",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_AND_INDEPENDENT_REVIEW_REQUIRED",
        "scientific_acceptance": "NOT_ESTABLISHED",
        "approval_sha256": expected_approval_sha256,
        "campaign_id": approval["campaign_id"],
        "readmission_transition_id": readmission_transition_id,
        "readmission_manifest_path": str(_project_path(
            workspace, Path(readmission_manifest_path), must_exist=True)),
        "readmission_manifest_sha256": expected_readmission_manifest_sha256,
        "project_id": project_id,
        "project_workspace": str(workspace),
        "operation_store_path": str(operation_store_path),
        "science_worker_model_bindings": binding_records,
        "historical_setup_receipt_sha256": dict(historical_setup_receipt_sha256),
        "ordered_slots": slots,
        "ordered_slots_sha256": history_sha,
        "planned_study_run_submissions": 14,
        "actual_study_run_submissions": 0,
        "terminal_confirmed_study_run_submissions": 0,
        "phase_initialization_included_per_submission": True,
        "capture_grid_protocol": SENSITIVITY_CAPTURE_PROTOCOL,
        "capture_resource_estimate": sensitivity_capture_resource_estimate(),
        "approved_resource_limits": dict(resource_limits),
        "campaign_execution_started_epoch_s": campaign_started,
        "campaign_wall_clock_origin_epoch_s": campaign_wall_origin,
        "elapsed_before_science_campaign_s": max(0.0, campaign_started - campaign_wall_origin),
        "birth_budget_at_science_start": birth_budget.receipt(),
        "maximum_campaign_wall_time_s": resource_limits["maximum_campaign_wall_time_s"],
        "science_worker_birth_budget": expected_budget_binding,
        "initial_output_bytes": initial_outputs,
        "source_copies": {
            "study_run": {"path": str(solve_source_copy), "sha256": expected_study_run_source_sha256},
            "history_capture": {"path": str(capture_source_copy), "sha256": expected_capture_source_sha256},
            "science_executor": {"path": str(executor_source_copy), "sha256": expected_science_executor_sha256},
        },
        "approved_setup_receipts": {
            key: {"path": str(_project_path(workspace, Path(setup_receipt_paths[key]), must_exist=True)),
                  "sha256": receipt_hashes[key],
                  "historical_setup_receipt_sha256": historical_setup_receipt_sha256[key],
                  "readback_started_binding": verified_setups[key]["reopened_configuration_readback_binding"],
                  "configuration_readback": verified_setups[key]["reopened_configuration_readback"]}
            for key in CAPTURE_KEY_ORDER
        },
        "study_run_ledger": str(ledger),
        "cases": {},
        "incremental_baseline_variant_comparisons": {},
        "retry_policy": "none; an unknown or unobserved slot stops all later work and no fresh revision can create a replacement slot",
    }
    _write_json_fsynced(receipt_path, receipt)
    _append_jsonl_fsynced(events, {
        "event": "sensitivity_campaign_started",
        "campaign_id": approval["campaign_id"],
        "approval_sha256": expected_approval_sha256,
        "study_run_slots": 14,
        "birth_budget": birth_budget.receipt(),
        "readmission_transition_id": readmission_transition_id,
        "readmission_manifest_sha256": expected_readmission_manifest_sha256,
        "ordered_slots_sha256": history_sha,
    })

    try:
        current_bindings = {key: bindings[key] for key in CAPTURE_KEY_ORDER}
        for slot_index, slot in enumerate(slots):
            index = slot["submission_index"]
            key = sensitivity_binding_key(slot["configuration_id"], slot["case_id"])
            require_campaign_time(f"before sensitivity slot {index}")
            store_path_now = _operation_store_path(managed_runner)
            store_stat_now = store_path_now.stat()
            if (store_path_now != operation_store_path or
                    (store_stat_now.st_dev, store_stat_now.st_ino) != store_identity):
                raise CampaignError("OperationStore path/inode changed during the approved 14-slot campaign")
            prior_rows = _read_sensitivity_ledger(
                ledger, slots, expected_approval_sha256, approval["campaign_id"])
            if len(prior_rows) != index - 1:
                raise CampaignError("sensitivity ledger is not at the exact next slot; refusing retry or skip")
            if _project_output_bytes(output_dir) > resource_limits["maximum_total_project_output_bytes"]:
                raise CampaignError("project output bytes already exceed the approved campaign ceiling")
            binding = current_bindings[key]
            if binding.as_record() != binding_records[key]:
                raise CampaignError("sensitivity slot must use its exact original approved ModelRef/revision")
            verifier(binding)
            save_path = output_dir / f"static_shape_{slot['configuration_id']}_{slot['case_id']}_solved.mph"
            if save_path.exists() or save_path.is_symlink():
                raise CampaignError(f"{key} solved MPH already exists; no repeat solve is permitted")
            timeout = campaign_rpc_timeout(600.0)
            call_id = uuid4().hex
            _append_jsonl_fsynced(events, {
                "event": "sensitivity_slot_dispatch_started", "call_id": call_id,
                "submission_index": index, "configuration_id": slot["configuration_id"],
                "case_id": slot["case_id"], "model_tag": binding.model_tag,
                "idempotency_key": slot["slot_idempotency_key"], "request_id": slot["request_id"],
            })
            ordered_for_java = [{name: row[name] for name in (
                "submission_index", "configuration_id", "case_id", "model_tag", "slot_idempotency_key")}
                for row in slots]
            response = _dispatch_solve_slot(
                managed_runner, binding,
                arguments={
                    "source_artifact": solve_source_copy.name,
                    "entrypoint": "W24StaticShapeSensitivityStudyRun#run",
                    "arguments": {
                        "action": "run", "sensitivity_campaign": True,
                        "configuration_id": slot["configuration_id"], "case_id": slot["case_id"],
                        "expected_model_tag": binding.model_tag,
                        "submission_index": index,
                        "workspace_path": str(workspace), "ledger_path": str(ledger),
                        "save_path": str(save_path),
                        "slot_idempotency_key": slot["slot_idempotency_key"],
                        "approval_sha256": expected_approval_sha256,
                        "campaign_id": approval["campaign_id"],
                        "ordered_slots": ordered_for_java,
                        "slot_history_sha256": history_sha,
                    },
                    "mode": "trusted",
                },
                idempotency_key=slot["slot_idempotency_key"],
                request_id=slot["request_id"], timeout_s=timeout,
                call_id=call_id, journal=events)
            result = managed_runner.setup_runner._java_action_readback(
                response, f"W24 sensitivity Study.run/{slot['configuration_id']}/{slot['case_id']}")
            updated = managed_runner._updated_binding(binding, response)
            rows_after = _read_sensitivity_ledger(
                ledger, slots, expected_approval_sha256, approval["campaign_id"])
            if (len(rows_after) != index or rows_after[-1].get("slot_idempotency_key") != slot["slot_idempotency_key"]):
                raise CampaignError(f"{key} lacks exactly one durable slot intent matching its approved key")
            if (result.get("status") != "NATIVE_STUDY_RUN_RETURNED" or
                    result.get("configuration_id") != slot["configuration_id"] or
                    result.get("case_id") != slot["case_id"] or
                    result.get("model_tag") != binding.model_tag or
                    result.get("submission_index") != index or
                    result.get("ordered_slots_sha256") != history_sha or
                    result.get("study_run_calls_from_this_action") != 1 or
                    result.get("phase_initialization_included") is not True or
                    result.get("save_path") != str(save_path)):
                raise CampaignError(f"{key} native solve receipt differs from its approved sensitivity slot")
            solved = _project_path(workspace, save_path, must_exist=True)
            if solved.stat().st_size > resource_limits["maximum_single_solved_mph_bytes"]:
                raise CampaignError(f"{key} solved MPH exceeded the approved per-file output ceiling")
            if _project_output_bytes(output_dir) > resource_limits["maximum_total_project_output_bytes"]:
                raise CampaignError(f"{key} solved MPH exceeded the approved total project output ceiling")
            current_bindings[key] = updated
            receipt["actual_study_run_submissions"] = len(rows_after)
            receipt["terminal_confirmed_study_run_submissions"] = index
            case_result = {
                "status": "STUDY_RUN_RETURNED_CAPTURE_PENDING",
                "configuration_id": slot["configuration_id"],
                "case_id": slot["case_id"],
                "submission_index": index,
                "original_model_binding": binding_records[key],
                "post_solve_model_binding": updated.as_record(),
                "study_run": dict(result),
                "solved_mph": {"path": str(solved), "size_bytes": solved.stat().st_size,
                               "sha256": sha256_file(solved)},
            }
            receipt["cases"][key] = case_result
            _append_jsonl_fsynced(events, {
                "event": "sensitivity_slot_terminal_succeeded", "call_id": call_id,
                "submission_index": index, "configuration_id": slot["configuration_id"],
                "case_id": slot["case_id"], "job_id": response.get("execution", {}).get("job_id")
                if isinstance(response.get("execution"), Mapping) else None,
            })

            capture_timeout = campaign_rpc_timeout(600.0)
            previous_timeout = getattr(managed_runner, "timeout_s", None)
            if previous_timeout is not None:
                managed_runner.timeout_s = capture_timeout
            try:
                updated_after_capture, analysis = capture_static_shape_history(
                    managed_runner, updated,
                    case_id=slot["case_id"],
                    configuration_id=slot["configuration_id"],
                    expected_capture_source_sha256=expected_capture_source_sha256,
                )
            finally:
                if previous_timeout is not None:
                    managed_runner.timeout_s = previous_timeout
            if (analysis.get("schema") != "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V2" or
                    analysis.get("case_id") != slot["case_id"] or
                    analysis.get("configuration_id") != slot["configuration_id"] or
                    analysis.get("model_tag") != binding.model_tag or
                    analysis.get("managed_binding") != updated_after_capture.as_record() or
                    analysis.get("capture_grid_protocol", {}).get("schema") != SENSITIVITY_CAPTURE_PROTOCOL):
                raise CampaignError(f"{key} W24SHAP1/v2 capture did not bind the solved revision/configuration")
            raw_record = analysis.get("raw_capture")
            raw_size = raw_record.get("size_bytes") if isinstance(raw_record, Mapping) else None
            if (isinstance(raw_size, bool) or type(raw_size) is not int or raw_size <= 0 or
                    raw_size > resource_limits["maximum_single_capture_bytes"] or
                    raw_size > sensitivity_capture_resource_estimate()["per_history_hard_limit_bytes"]):
                raise CampaignError(f"{key} raw v2 history exceeded its approved per-file capture ceiling")
            prior_raw_bytes = sum(
                int(case.get("capture", {}).get("raw_capture", {}).get("size_bytes", 0))
                for case in receipt["cases"].values()
                if isinstance(case, Mapping) and isinstance(case.get("capture"), Mapping) and
                isinstance(case["capture"].get("raw_capture"), Mapping)
            )
            if prior_raw_bytes + raw_size > resource_limits["maximum_total_raw_capture_bytes"]:
                raise CampaignError(f"{key} raw histories exceeded their approved aggregate capture ceiling")
            current_bindings[key] = updated_after_capture
            case_result.update({"status": "SOLVED_AND_V2_CAPTURED",
                                "capture": analysis})
            receipt["cases"][key] = case_result
            _replace_campaign_receipt_fsynced(receipt_path, receipt)
            if _project_output_bytes(output_dir) > resource_limits["maximum_total_project_output_bytes"]:
                raise CampaignError(f"{key} capture artifact exceeded the approved total output ceiling")
            gate = analysis.get("shape_history_gate")
            gate_status = gate.get("status") if isinstance(gate, Mapping) else None
            _append_jsonl_fsynced(events, {
                "event": "sensitivity_shape_history_gate_evaluated",
                "submission_index": index,
                "configuration_id": slot["configuration_id"],
                "case_id": slot["case_id"],
                "shape_history_gate": gate_status,
            })
            if gate_status != "STABLE_WINDOW_PASS":
                raise CampaignError(
                    f"{key} shape-history gate is {gate_status!r}; later sensitivity slots are stopped"
                )
            if slot["configuration_id"] != "baseline":
                baseline_key = f"baseline:{slot['case_id']}"
                baseline_case = receipt["cases"].get(baseline_key)
                baseline_capture = baseline_case.get("capture") if isinstance(baseline_case, Mapping) else None
                if not isinstance(baseline_capture, Mapping):
                    raise CampaignError(f"{key} has no already-captured same-case baseline for its immediate gate")
                comparison_key = f"{slot['configuration_id']}:{slot['case_id']}"
                try:
                    incremental = compare_sensitivity_case_variant(
                        baseline_capture,
                        analysis,
                        variant_configuration_id=slot["configuration_id"],
                        expected_case_id=slot["case_id"],
                        expected_project_id=project_id,
                        expected_workspace=workspace,
                    )
                except BaseException as exc:
                    receipt["incremental_baseline_variant_comparisons"][comparison_key] = {
                        "status": "COMPARISON_ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                        "baseline_capture_sha256": baseline_capture.get("capture_sha256"),
                        "variant_capture_sha256": analysis.get("capture_sha256"),
                    }
                    _replace_campaign_receipt_fsynced(receipt_path, receipt)
                    _append_jsonl_fsynced(events, {
                        "event": "sensitivity_incremental_comparison_error",
                        "submission_index": index,
                        "configuration_id": slot["configuration_id"],
                        "case_id": slot["case_id"],
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    raise CampaignError(
                        f"{key} immediate baseline comparison raised; later sensitivity slots are stopped"
                    ) from exc
                receipt["incremental_baseline_variant_comparisons"][comparison_key] = incremental
                _replace_campaign_receipt_fsynced(receipt_path, receipt)
                _append_jsonl_fsynced(events, {
                    "event": "sensitivity_incremental_comparison_completed",
                    "submission_index": index,
                    "configuration_id": slot["configuration_id"],
                    "case_id": slot["case_id"],
                    "comparison_status": incremental.get("status"),
                })
                if incremental.get("status") != "PASS":
                    raise CampaignError(
                        f"{key} immediate baseline comparison status is {incremental.get('status')!r}; "
                        "later sensitivity slots are stopped"
                    )
            _append_jsonl_fsynced(events, {
                "event": "sensitivity_slot_v2_capture_completed",
                "submission_index": index, "configuration_id": slot["configuration_id"],
                "case_id": slot["case_id"],
                "raw_sha256": analysis.get("raw_capture", {}).get("sha256"),
                "shape_history_gate": analysis.get("shape_history_gate", {}).get("status"),
            })

        captures = {key: receipt["cases"][key]["capture"] for key in CAPTURE_KEY_ORDER}
        comparison = compare_sensitivity_campaign(
            captures, expected_project_id=project_id, expected_workspace=workspace)
        final_rows = _read_sensitivity_ledger(ledger, slots, expected_approval_sha256, approval["campaign_id"])
        all_stable = all(capture["shape_history_gate"].get("status") == "STABLE_WINDOW_PASS"
                         for capture in captures.values())
        receipt.update({
            "status": ("ALL_14_CAPTURED_SENSITIVITY_GATES_PASS_REVIEW_REQUIRED"
                       if comparison.get("status") == "SENSITIVITY_GATES_PASS" and all_stable
                       else "SENSITIVITY_CAPTURED_GATES_FAIL_OR_INCOMPLETE"),
            "actual_study_run_submissions": len(final_rows),
            "terminal_confirmed_study_run_submissions": len(final_rows),
            "comparison": comparison,
            "birth_budget_at_finish": birth_budget.receipt(),
            "worker_elapsed_from_birth_s": max(
                0.0, time.time() - supplied_budget_identity["birth_epoch_s"]),
            "campaign_elapsed_s": max(0.0, time.time() - campaign_started),
            "final_project_output_bytes": _project_output_bytes(output_dir),
            "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
            "scientific_acceptance": "NOT_ESTABLISHED_INDEPENDENT_REVIEW_REQUIRED",
        })
        _append_jsonl_fsynced(events, {
            "event": "sensitivity_campaign_complete",
            "status": receipt["status"],
            "study_run_submissions": len(final_rows),
            "sensitivity_gate_status": comparison.get("status"),
        })
        _replace_campaign_receipt_fsynced(receipt_path, receipt)
        return receipt
    except BaseException as exc:
        try:
            rows = _read_sensitivity_ledger(ledger, slots, expected_approval_sha256, approval["campaign_id"])
            admitted_rows = len(rows)
        except Exception:
            admitted_rows = None
        receipt.update({
            "status": "FAIL_OR_INCOMPLETE_NO_RETRY",
            "actual_study_run_submissions": admitted_rows,
            "terminal_confirmed_study_run_submissions": len(receipt.get("cases", {})),
            "error": f"{type(exc).__name__}: {exc}",
            "birth_budget_at_failure": birth_budget.receipt(),
            "worker_elapsed_from_birth_s": max(
                0.0, time.time() - supplied_budget_identity["birth_epoch_s"]),
            "campaign_elapsed_s": max(0.0, time.time() - campaign_started),
            "native_acceptance": "NOT_ESTABLISHED",
            "scientific_acceptance": "NOT_ESTABLISHED",
        })
        _append_jsonl_fsynced(events, {
            "event": "sensitivity_campaign_stopped_no_retry",
            "admitted_slot_intents": admitted_rows,
            "error": receipt["error"],
        })
        _replace_campaign_receipt_fsynced(receipt_path, receipt)
        return receipt
