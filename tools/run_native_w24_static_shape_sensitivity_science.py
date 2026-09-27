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
APPROVAL_SCHEMA = "W24_STATIC_SHAPE_SENSITIVITY_SCIENCE_APPROVAL_V1"
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
    expected_project_id: str,
    expected_workspace: Path,
    expected_operation_store_path: Path,
    expected_bindings: Mapping[str, ManagedModelBinding],
    expected_slots: list[Mapping[str, Any]],
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
    binding_records = {
        key: _binding_record(expected_bindings[key], project_id=expected_project_id, case_id=key)
        for key in CAPTURE_KEY_ORDER
    }
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


def execute_sensitivity_campaign(
    managed_runner: Any,
    bindings: Mapping[str, ManagedModelBinding],
    *,
    setup_receipt_paths: Mapping[str, Path],
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
    project_id = getattr(managed_runner, "project_id", None)
    supplied_workspace = Path(getattr(managed_runner, "workspace", ""))
    if not isinstance(project_id, str) or not project_id or supplied_workspace.is_symlink():
        raise CampaignError("managed runner lacks the exact registered sensitivity project/workspace")
    workspace = supplied_workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise CampaignError("registered sensitivity workspace is not a real directory")
    campaign_started = time.time()
    operation_store_path = _operation_store_path(managed_runner)
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
    if not isinstance(setup_receipt_paths, Mapping) or set(setup_receipt_paths) != set(CAPTURE_KEY_ORDER):
        raise CampaignError("14-slot sensitivity requires exactly fourteen original setup receipt paths")
    receipt_hashes: dict[str, str] = {}
    for key in CAPTURE_KEY_ORDER:
        supplied = setup_receipt_paths[key]
        if not isinstance(supplied, (str, os.PathLike)):
            raise CampaignError(f"{key} setup receipt path must be a project file")
        path = _project_path(workspace, Path(supplied), must_exist=True)
        receipt_hashes[key] = sha256_file(path)
    store_stat = operation_store_path.stat()
    store_identity = (store_stat.st_dev, store_stat.st_ino)
    approval = validate_sensitivity_approval(
        approval_path,
        expected_approval_sha256=expected_approval_sha256,
        expected_source_sha256=source_sha,
        expected_setup_receipt_sha256=receipt_hashes,
        expected_project_id=project_id,
        expected_workspace=workspace,
        expected_operation_store_path=operation_store_path,
        expected_bindings=bindings,
        expected_slots=slots,
    )
    approved_campaign_deadline = min(
        birth_budget.deadline_epoch_s,
        campaign_started + approval["resource_limits"]["maximum_campaign_wall_time_s"] +
        birth_budget.cleanup_reserve_s,
    )

    def require_campaign_time(label: str) -> None:
        if time.time() >= approved_campaign_deadline - birth_budget.cleanup_reserve_s:
            raise CampaignError(f"approved sensitivity campaign wall-time limit reached {label}")

    def campaign_rpc_timeout(preferred_s: float) -> float:
        require_campaign_time("before a managed RPC")
        return min(birth_budget.rpc_timeout_s(preferred_s),
                   approved_campaign_deadline - time.time() - birth_budget.cleanup_reserve_s)
    verified_setups: dict[str, dict[str, Any]] = {}
    for key in CAPTURE_KEY_ORDER:
        config_id, case_id = key.split(":", 1)
        verified_setups[key] = _load_approved_sensitivity_setup_receipt(
            setup_receipt_paths[key], configuration_id=config_id, case_id=case_id,
            workspace=workspace, project_id=project_id, binding_record=binding_records[key],
            expected_receipt_sha256=receipt_hashes[key],
            expected_fixture_sha256=expected_setup_fixture_source_sha256,
            expected_readback_sha256=expected_setup_readback_source_sha256,
        )

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
        "project_id": project_id,
        "project_workspace": str(workspace),
        "operation_store_path": str(operation_store_path),
        "original_model_bindings": binding_records,
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
        "maximum_campaign_wall_time_s": resource_limits["maximum_campaign_wall_time_s"],
        "initial_output_bytes": initial_outputs,
        "source_copies": {
            "study_run": {"path": str(solve_source_copy), "sha256": expected_study_run_source_sha256},
            "history_capture": {"path": str(capture_source_copy), "sha256": expected_capture_source_sha256},
            "science_executor": {"path": str(executor_source_copy), "sha256": expected_science_executor_sha256},
        },
        "approved_setup_receipts": {
            key: {"path": str(_project_path(workspace, Path(setup_receipt_paths[key]), must_exist=True)),
                  "sha256": receipt_hashes[key],
                  "original_readback_binding": verified_setups[key]["reopened_configuration_readback_binding"],
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
