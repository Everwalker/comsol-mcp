"""Managed baseline solve/capture orchestration for W24 flat and step shapes.

Nothing in this module starts a COMSOL process. The caller must supply the
already-owned managed runner and a separately reviewed, SHA-pinned approval
for the exact two-run baseline campaign. Each Study.run is submitted once;
an uncertain request stops the sequence without retry.
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
from tools.w24_static_shape_capture import CAPTURE_SOURCE, capture_static_shape_history
from tools.w24_static_shape_pair_comparison import compare_flat_step_captures


REPO = Path(__file__).resolve().parents[1]
STUDY_RUN_SOURCE = REPO / "tools/java/W24StaticShapeStudyRun.java"
SETUP_FIXTURE_SOURCE = STATIC_SHAPE_FIXTURE
SETUP_READBACK_SOURCE = STATIC_SHAPE_READBACK
SCIENCE_EXECUTOR_SOURCE = Path(__file__).resolve()
APPROVAL_SCHEMA = "W24_STATIC_SHAPE_BASELINE_SCIENCE_APPROVAL_V2"
CASE_ORDER = ("flat", "step")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_science_approval(
    approval_path: Path,
    *,
    expected_approval_sha256: str,
    expected_solve_source_sha256: str,
    expected_capture_source_sha256: str,
    expected_science_executor_sha256: str,
    expected_setup_fixture_source_sha256: str,
    expected_setup_readback_source_sha256: str,
    expected_setup_receipt_sha256: Mapping[str, str],
    expected_project_id: str,
    expected_workspace: Path,
    expected_operation_store_path: Path,
    expected_bindings: Mapping[str, ManagedModelBinding],
) -> dict[str, Any]:
    """Validate the reviewed campaign, project, store, model refs, and source bytes."""
    path = Path(approval_path)
    pins = (expected_approval_sha256, expected_solve_source_sha256,
            expected_capture_source_sha256, expected_science_executor_sha256,
            expected_setup_fixture_source_sha256, expected_setup_readback_source_sha256)
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in pins):
        raise CampaignError("science approval/source SHA-256 pins are malformed")
    if path.is_symlink() or not path.is_file() or sha256_file(path) != expected_approval_sha256:
        raise CampaignError("baseline science approval is absent, aliased, or differs from the reviewed digest")
    try:
        approval = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError("baseline science approval cannot be decoded") from exc
    if (not isinstance(approval, Mapping) or approval.get("schema") != APPROVAL_SCHEMA or
            approval.get("status") != "APPROVED" or
            approval.get("configuration_id") != "baseline" or
            approval.get("study_tag") != "stdShape" or
            approval.get("case_order") != list(CASE_ORDER) or
            approval.get("study_run_submissions") != 2 or
            approval.get("phase_initialization_steps_per_submission") != 1 or
            not isinstance(approval.get("campaign_id"), str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", approval["campaign_id"])):
        raise CampaignError("approval does not authorize exactly flat then step baseline Study.run submissions")
    source_hashes = approval.get("source_sha256")
    if (not isinstance(source_hashes, Mapping) or
            source_hashes.get("study_run") != expected_solve_source_sha256 or
            source_hashes.get("history_capture") != expected_capture_source_sha256 or
            source_hashes.get("science_executor") != expected_science_executor_sha256 or
            source_hashes.get("setup_fixture") != expected_setup_fixture_source_sha256 or
            source_hashes.get("setup_readback") != expected_setup_readback_source_sha256):
        raise CampaignError("approval does not bind the exact setup, solve, capture, and science executor bytes")
    setup_hashes = approval.get("setup_receipt_sha256")
    if (not isinstance(expected_setup_receipt_sha256, Mapping) or
            set(expected_setup_receipt_sha256) != set(CASE_ORDER) or
            not isinstance(setup_hashes, Mapping) or
            dict(setup_hashes) != dict(expected_setup_receipt_sha256) or
            any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                for value in expected_setup_receipt_sha256.values())):
        raise CampaignError("approval does not bind the exact full setup/readback receipt for both original models")
    approved_bindings = approval.get("model_bindings")
    expected_binding_records = {
        case: _binding_record(expected_bindings[case], project_id=expected_project_id, case_id=case)
        for case in CASE_ORDER
    }
    if (approval.get("project_id") != expected_project_id or
            approval.get("project_workspace") != str(expected_workspace) or
            approval.get("operation_store_path") != str(expected_operation_store_path) or
            not isinstance(approved_bindings, Mapping) or
            dict(approved_bindings) != expected_binding_records):
        raise CampaignError("approval does not pin the exact registered project, OperationStore, and both original ModelRefs/revisions")
    return dict(approval)


def _load_approved_setup_receipt(
    supplied_path: Path,
    *,
    case_id: str,
    workspace: Path,
    project_id: str,
    binding_record: Mapping[str, Any],
    expected_receipt_sha256: str,
    expected_fixture_source_sha256: str,
    expected_readback_source_sha256: str,
) -> tuple[dict[str, Any], Path, str]:
    """Verify the full original-revision setup proof without a trusted-code call."""
    receipt_path = _project_path(workspace, Path(supplied_path), must_exist=True)
    digest = sha256_file(receipt_path)
    if digest != expected_receipt_sha256:
        raise CampaignError(f"{case_id} setup receipt differs from its separately approved SHA-256")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"{case_id} setup receipt cannot be decoded") from exc
    if (not isinstance(receipt, Mapping) or
            receipt.get("schema") != "W24_STATIC_SHAPE_MANAGED_SETUP_V1" or
            receipt.get("status") != "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE" or
            receipt.get("native_acceptance") != "NOT_RUN" or
            receipt.get("project_id") != project_id or
            receipt.get("project_workspace") != str(workspace) or
            receipt.get("case_id") != case_id or
            receipt.get("reopened_model_binding") != dict(binding_record) or
            receipt.get("study_run_submissions") != [] or
            receipt.get("study_run_submission_count") != 0 or
            receipt.get("phase_initialization_executed") is not False):
        raise CampaignError(f"{case_id} setup receipt is not the exact approved unsolved project/model record")
    source_hashes = receipt.get("source_sha256")
    if (not isinstance(source_hashes, Mapping) or
            source_hashes.get("fixture") != expected_fixture_source_sha256 or
            source_hashes.get("readback") != expected_readback_source_sha256):
        raise CampaignError(f"{case_id} setup receipt does not bind the approved fixture/readback Java sources")
    readback_binding = receipt.get("reopened_configuration_readback_binding")
    final_revision = binding_record.get("revision")
    if (not isinstance(readback_binding, Mapping) or
            {key: value for key, value in readback_binding.items() if key != "revision"} !=
            {key: value for key, value in binding_record.items() if key != "revision"} or
            isinstance(readback_binding.get("revision"), bool) or
            type(readback_binding.get("revision")) is not int or
            type(final_revision) is not int or
            readback_binding["revision"] + 1 != final_revision):
        raise CampaignError(f"{case_id} setup receipt lost the exact pre-readback revision or its declared one-step transition")
    identity = receipt.get("reopened_model_identity")
    identity_binding = identity.get("model_binding") if isinstance(identity, Mapping) else None
    if (not isinstance(identity_binding, Mapping) or
            dict(identity_binding) != dict(readback_binding)):
        raise CampaignError(f"{case_id} setup identity observation is not bound to the configuration readback revision")
    configuration = receipt.get("reopened_configuration_readback")
    try:
        StaticShapeManagedRunner._validate_shape_readback(configuration, case_id)
    except CampaignError as exc:
        raise CampaignError(f"{case_id} approved original-revision full configuration/getSize readback is invalid: {exc}") from exc
    if configuration.get("model_tag") != binding_record.get("model_tag"):
        raise CampaignError(f"{case_id} full setup readback identifies a different native model tag")
    comparison = receipt.get("readback_comparison")
    if (not isinstance(comparison, Mapping) or comparison.get("matches") is not True or
            comparison.get("interpolation_used") is not False):
        raise CampaignError(f"{case_id} setup receipt does not prove exact saved/reopened configuration equality")
    artifact = receipt.get("project_artifact")
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str):
        raise CampaignError(f"{case_id} setup receipt omitted the saved unsolved MPH artifact identity")
    artifact_path = _project_path(workspace, Path(artifact["path"]), must_exist=True)
    artifact_digest = sha256_file(artifact_path)
    if (artifact_digest != artifact.get("sha256") or artifact_path.stat().st_size <= 0 or
            artifact_path.stat().st_size != artifact.get("size_bytes")):
        raise CampaignError(f"{case_id} original unsolved MPH artifact no longer matches its setup receipt")
    return dict(receipt), receipt_path, digest


def _binding_record(binding: Any, *, project_id: str, case_id: str) -> dict[str, Any]:
    if not isinstance(binding, ManagedModelBinding):
        raise CampaignError(f"{case_id} must use the production ManagedModelBinding type")
    record = binding.as_record()
    ref = record["model_ref"]
    if (binding.project_id != project_id or not binding.session_id or
            not isinstance(binding.model_tag, str) or not binding.model_tag or
            ref.get("session_id") != binding.session_id or
            ref.get("model_tag") != binding.model_tag or
            not isinstance(ref.get("server_instance_id"), str) or not ref.get("server_instance_id") or
            isinstance(ref.get("generation"), bool) or type(ref.get("generation")) is not int or
            ref["generation"] < 1):
        raise CampaignError(f"{case_id} is not an exact managed project-bound Worker-epoch ModelRef")
    return record


def _copy_study_source(workspace: Path, expected_sha256: str) -> Path:
    if (STUDY_RUN_SOURCE.is_symlink() or not STUDY_RUN_SOURCE.is_file() or
            sha256_file(STUDY_RUN_SOURCE) != expected_sha256):
        raise CampaignError("live Java study-run source does not match the separately reviewed SHA-256")
    workspace = workspace.resolve(strict=True)
    destination = _project_path(workspace, workspace / STUDY_RUN_SOURCE.name,
                                must_exist=(workspace / STUDY_RUN_SOURCE.name).exists())
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or sha256_file(destination) != expected_sha256:
            raise CampaignError("project workspace already contains a different solve source; refusing overwrite")
    else:
        with STUDY_RUN_SOURCE.open("rb") as reader, destination.open("xb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
    if sha256_file(destination) != expected_sha256:
        raise CampaignError("copied Java study-run source failed its SHA-256 check")
    return destination


def _read_local_solve_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    if path.is_symlink() or not path.is_file():
        raise CampaignError("static-shape solve ledger is not a regular file")
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignError(f"static-shape solve ledger has invalid JSON at row {index}") from exc
        if len(rows) >= len(CASE_ORDER):
            raise CampaignError("static-shape solve ledger exceeds the two-run baseline campaign")
        if (not isinstance(row, Mapping) or row.get("event") != "study_run_submitted" or
                row.get("study_tag") != "stdShape" or row.get("submission_index") != len(rows) + 1 or
                row.get("case_id") != CASE_ORDER[len(rows)] or
                not isinstance(row.get("slot_idempotency_key"), str) or
                not re.fullmatch(r"w24-static-shape-slot-[0-9a-f]{64}", row["slot_idempotency_key"]) or
                not isinstance(row.get("approval_sha256"), str) or
                not SHA256_RE.fullmatch(row["approval_sha256"])):
            raise CampaignError("static-shape solve ledger differs from the exact flat-then-step sequence")
        rows.append(dict(row))
    if len(rows) > len(CASE_ORDER):
        raise CampaignError("static-shape solve ledger exceeds the two-run baseline campaign")
    return rows


def _operation_store_path(managed_runner: Any) -> Path:
    daemon = getattr(managed_runner, "daemon", None)
    store = getattr(daemon, "store", None)
    raw_path = getattr(store, "path", None)
    if not isinstance(raw_path, (str, os.PathLike)):
        raise CampaignError("managed daemon has no durable OperationStore path for campaign admission")
    supplied = Path(raw_path)
    if supplied.is_symlink() or not supplied.is_file():
        raise CampaignError("campaign OperationStore must be an existing regular non-symlink database")
    return supplied.resolve(strict=True)


def _slot_identity(project_id: str, binding: ManagedModelBinding, case_id: str) -> tuple[str, str]:
    """Return deterministic durable operation identities for one immutable model slot.

    The idempotency key intentionally excludes the approval digest. The digest
    is part of the operation arguments/hash. A later or changed approval for
    the same project/model slot therefore conflicts with the already claimed
    OperationStore key instead of manufacturing a second solve attempt.
    """
    identity = {
        "schema": "W24_STATIC_SHAPE_SOLVE_SLOT_V1",
        "project_id": project_id,
        "case_id": case_id,
        "model_binding": binding.as_record(),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    suffix = hashlib.sha256(encoded).hexdigest()
    key = f"w24-static-shape-slot-{suffix}"
    return key, f"w24-static-shape-request-{suffix}"


def _dispatch_solve_slot(
    managed_runner: Any,
    binding: ManagedModelBinding,
    *,
    arguments: Mapping[str, Any],
    idempotency_key: str,
    request_id: str,
    timeout_s: float,
    call_id: str,
    journal: Path,
) -> dict[str, Any]:
    """Use ControlDaemon's SQLite idempotency transaction as the admission claim."""
    if not SHA256_RE.fullmatch(idempotency_key.removeprefix("w24-static-shape-slot-")):
        raise CampaignError("solve slot idempotency identity is malformed")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise CampaignError("solve slot timeout must be positive and finite")
    daemon = getattr(managed_runner, "daemon", None)
    dispatch = getattr(daemon, "dispatch", None)
    if not callable(dispatch):
        raise CampaignError("managed runner lacks the production ControlDaemon.dispatch admission path")
    execution = {
        "project_id": managed_runner.project_id,
        "session_id": binding.session_id,
        "model_ref": dict(binding.model_ref),
        "expected_revision": binding.revision,
        "rpc_timeout_s": timeout_s,
        "queue_timeout_s": 60.0,
        "execution_timeout_s": None,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
    }
    _append_jsonl_fsynced(journal, {
        "event": "durable_solve_slot_dispatch_requested",
        "call_id": call_id,
        "operation": "code.execute_java",
        "project_id": managed_runner.project_id,
        "model_tag": binding.model_tag,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
    })
    try:
        response = dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "code.execute_java", "arguments": dict(arguments)},
            "execution": execution,
        })
    except BaseException as exc:
        _append_jsonl_fsynced(journal, {
            "event": "durable_solve_slot_dispatch_exception",
            "call_id": call_id, "idempotency_key": idempotency_key,
            "terminal_observed": False, "error": f"{type(exc).__name__}: {exc}",
        })
        raise
    if not isinstance(response, dict):
        raise CampaignError("ControlDaemon returned no structured solve-slot response; keep its durable idempotency key")
    error = response.get("error")
    if isinstance(error, Mapping) and error.get("code") == "IDEMPOTENCY_CONFLICT":
        _append_jsonl_fsynced(journal, {
            "event": "durable_solve_slot_payload_conflict",
            "call_id": call_id,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
            "error_code": "IDEMPOTENCY_CONFLICT",
        })
        raise CampaignError("solve slot is already claimed by a different canonical payload; refusing replay")
    execution_result = response.get("execution")
    if (not isinstance(execution_result, Mapping) or
            execution_result.get("idempotency_key") != idempotency_key or
            execution_result.get("request_id") != request_id):
        raise CampaignError("solve-slot response did not echo the original durable OperationStore identity")
    terminal_checker = getattr(getattr(managed_runner, "setup_runner", None),
                               "_worker_request_terminal", None)
    terminal = bool(terminal_checker(response)) if callable(terminal_checker) else False
    record_response = getattr(managed_runner, "_record_response", None)
    if callable(record_response):
        record_response(call_id, response)
    _append_jsonl_fsynced(journal, {
        "event": "durable_solve_slot_dispatch_returned",
        "call_id": call_id,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
        "operation_id": execution_result.get("operation_id"),
        "job_id": execution_result.get("job_id"),
        "terminal_observed": terminal,
        "success": response.get("success") is True,
    })
    if not terminal:
        raise CampaignError("solve slot has no observed terminal result; reentry must reuse this OperationStore key")
    if response.get("success") is not True:
        raise CampaignError("solve slot returned a terminal failure; reentry must reuse this OperationStore key")
    return response


def execute_baseline_flat_step_science(
    managed_runner: Any,
    bindings: Mapping[str, ManagedModelBinding],
    *,
    setup_receipt_paths: Mapping[str, Path],
    approval_path: Path,
    expected_approval_sha256: str,
    expected_solve_source_sha256: str,
    expected_capture_source_sha256: str,
    expected_science_executor_sha256: str,
    expected_setup_fixture_source_sha256: str,
    expected_setup_readback_source_sha256: str,
    birth_budget: BirthBudget,
    solve_ledger_path: Path,
) -> dict[str, Any]:
    """Run/capture the approved baseline pair on prebuilt managed models.

    Both models are verified unsolved before the first study call. Every case
    is then submitted and captured in order, with no retry. Sensitivity cases
    are intentionally handled only by the separate 14-submission plan until
    a variant-aware fixture and capture protocol are reviewed.
    """
    if not isinstance(bindings, Mapping) or set(bindings) != set(CASE_ORDER):
        raise CampaignError("baseline static-shape campaign requires exactly flat and step bindings")
    if not isinstance(birth_budget, BirthBudget):
        raise CampaignError("baseline study.run calls require the exact owned-engine BirthBudget")
    if sha256_file(STUDY_RUN_SOURCE) != expected_solve_source_sha256:
        raise CampaignError("Java study-run source drifted after approval validation")
    if sha256_file(CAPTURE_SOURCE) != expected_capture_source_sha256:
        raise CampaignError("Java capture source drifted after approval validation")
    if sha256_file(SCIENCE_EXECUTOR_SOURCE) != expected_science_executor_sha256:
        raise CampaignError("science executor source drifted after approval validation")
    if (sha256_file(SETUP_FIXTURE_SOURCE) != expected_setup_fixture_source_sha256 or
            sha256_file(SETUP_READBACK_SOURCE) != expected_setup_readback_source_sha256):
        raise CampaignError("static-shape setup fixture/readback source drifted after approval validation")
    project_id = getattr(managed_runner, "project_id", None)
    workspace = Path(getattr(managed_runner, "workspace", ""))
    if not isinstance(project_id, str) or not project_id or workspace.is_symlink():
        raise CampaignError("managed runner lacks its exact project/workspace identity")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise CampaignError("registered project workspace is not a real directory")
    operation_store_path = _operation_store_path(managed_runner)
    records = {case: _binding_record(bindings[case], project_id=project_id, case_id=case)
               for case in CASE_ORDER}
    flat_ref, step_ref = records["flat"]["model_ref"], records["step"]["model_ref"]
    if (records["flat"]["model_tag"] == records["step"]["model_tag"] or
            flat_ref == step_ref or
            records["flat"]["session_id"] != records["step"]["session_id"] or
            flat_ref["server_instance_id"] != step_ref["server_instance_id"] or
            flat_ref["generation"] != step_ref["generation"]):
        raise CampaignError("flat and step must be distinct model refs on the same current Worker epoch")
    if not isinstance(setup_receipt_paths, Mapping) or set(setup_receipt_paths) != set(CASE_ORDER):
        raise CampaignError("baseline campaign requires exactly one original setup receipt path for flat and step")
    declared_source_paths = getattr(managed_runner, "source_paths", None)
    if not isinstance(declared_source_paths, Mapping):
        raise CampaignError("managed runner cannot verify the exact project-copied setup Java sources")
    for role, expected in (("fixture", expected_setup_fixture_source_sha256),
                           ("readback", expected_setup_readback_source_sha256)):
        copied = declared_source_paths.get(role)
        if not isinstance(copied, (str, os.PathLike)):
            raise CampaignError(f"managed runner omitted its project-copied {role} Java source")
        copied_path = Path(copied)
        if copied_path.is_symlink() or not copied_path.is_file() or sha256_file(copied_path) != expected:
            raise CampaignError(f"project-copied {role} Java source differs from the approved bytes")
    setup_receipts: dict[str, dict[str, Any]] = {}
    setup_receipt_digests: dict[str, str] = {}
    for case in CASE_ORDER:
        supplied = setup_receipt_paths[case]
        if not isinstance(supplied, (str, os.PathLike)):
            raise CampaignError(f"{case} setup receipt path must be a project-owned file path")
        receipt_path = _project_path(workspace, Path(supplied), must_exist=True)
        receipt_digest = sha256_file(receipt_path)
        setup_receipt_digests[case] = receipt_digest
        setup_receipts[case] = {}
    approval = validate_science_approval(
        approval_path,
        expected_approval_sha256=expected_approval_sha256,
        expected_solve_source_sha256=expected_solve_source_sha256,
        expected_capture_source_sha256=expected_capture_source_sha256,
        expected_science_executor_sha256=expected_science_executor_sha256,
        expected_setup_fixture_source_sha256=expected_setup_fixture_source_sha256,
        expected_setup_readback_source_sha256=expected_setup_readback_source_sha256,
        expected_setup_receipt_sha256=setup_receipt_digests,
        expected_project_id=project_id,
        expected_workspace=workspace,
        expected_operation_store_path=operation_store_path,
        expected_bindings=bindings,
    )
    for case in CASE_ORDER:
        setup_receipts[case], _, _ = _load_approved_setup_receipt(
            setup_receipt_paths[case], case_id=case, workspace=workspace,
            project_id=project_id, binding_record=records[case],
            expected_receipt_sha256=setup_receipt_digests[case],
            expected_fixture_source_sha256=expected_setup_fixture_source_sha256,
            expected_readback_source_sha256=expected_setup_readback_source_sha256)
    store_identity = (operation_store_path.stat().st_dev, operation_store_path.stat().st_ino)
    source_copy = _copy_study_source(workspace, expected_solve_source_sha256)
    ledger_path = _project_path(workspace, Path(solve_ledger_path), must_exist=False)
    if ledger_path.name != "static_shape_study_runs.jsonl" or ledger_path.exists():
        raise CampaignError("a baseline campaign requires a new exact project-owned solve ledger")
    output_dir = workspace / "outputs"
    if (output_dir.is_symlink() or not output_dir.is_dir() or
            output_dir.resolve(strict=True) != output_dir):
        raise CampaignError("registered project outputs directory is missing or aliased")
    if _read_local_solve_ledger(ledger_path):
        raise CampaignError("baseline solve ledger must be empty before the first native invocation")

    current: dict[str, ManagedModelBinding] = {case: bindings[case] for case in CASE_ORDER}
    verifier = getattr(managed_runner, "_verify_persisted_binding", None)
    if not callable(verifier):
        raise CampaignError("managed runner cannot verify persisted project/model associations")
    # Validate both handles before issuing any Worker requests.
    for case in CASE_ORDER:
        verifier(current[case])

    receipt: dict[str, Any] = {
        "schema": "W24_STATIC_SHAPE_BASELINE_SCIENCE_EXECUTION_V1",
        "status": "RUNNING",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_AND_INDEPENDENT_REVIEW_REQUIRED",
        "approval_sha256": expected_approval_sha256,
        "configuration_id": "baseline",
        "case_order": list(CASE_ORDER),
        "planned_study_run_submissions": 2,
        "actual_study_run_submissions": 0,
        "phase_initialization_included_per_submission": True,
        "campaign_id": approval["campaign_id"],
        "project_id": project_id,
        "project_workspace": str(workspace),
        "operation_store_path": str(operation_store_path),
        "original_model_bindings": records,
        "source_copies": {"study_run": str(source_copy),
                           "study_run_sha256": expected_solve_source_sha256},
        "approved_setup_receipts": {
            case: {
                "path": str(_project_path(workspace, Path(setup_receipt_paths[case]), must_exist=True)),
                "sha256": setup_receipt_digests[case],
                "original_readback_binding": setup_receipts[case]["reopened_configuration_readback_binding"],
                "verified_configuration_readback": setup_receipts[case]["reopened_configuration_readback"],
            }
            for case in CASE_ORDER
        },
        "study_run_ledger": str(ledger_path),
        "cases": {},
        "retry_policy": "none; failed, unknown, or unobserved request stops all later work",
    }
    events = Path(managed_runner.evidence_dir) / "static_shape_science_events.jsonl"
    _append_jsonl_fsynced(events, {"event": "baseline_pair_campaign_started",
                                   "case_order": list(CASE_ORDER),
                                   "approval_sha256": expected_approval_sha256,
                                   "birth_budget": birth_budget.receipt()})

    try:
        # Consume the already reviewed full configuration/getSize readback at
        # its recorded original revision. Rechecking current identity uses the
        # real READ-only model.inspect route, which must preserve that binding.
        # The returned `solutions` inventory is recorded for context only;
        # solver-sequence tags do not establish stored solution data counts.
        for case in CASE_ORDER:
            if time.time() >= birth_budget.deadline_epoch_s - birth_budget.cleanup_reserve_s:
                raise CampaignError("birth budget entered cleanup reserve before both setup readbacks")
            remaining = birth_budget.rpc_timeout_s(120.0)
            prior_timeout = getattr(managed_runner, "timeout_s", None)
            if prior_timeout is not None:
                managed_runner.timeout_s = remaining
            try:
                updated, identity = managed_runner._inspect(current[case])
            finally:
                if prior_timeout is not None:
                    managed_runner.timeout_s = prior_timeout
            if updated.as_record() != records[case]:
                raise CampaignError(f"{case} read-only model.inspect changed the exact approved ModelRef/revision")
            observed_data = identity.get("data") if isinstance(identity, Mapping) else None
            if (not isinstance(observed_data, Mapping) or
                    observed_data.get("model_identity") != records[case]["model_ref"] or
                    observed_data.get("revision") != records[case]["revision"]):
                raise CampaignError(f"{case} read-only model.inspect did not echo the exact approved model/revision")
            current[case] = updated
            receipt.setdefault("pre_solve_readbacks", {})[case] = {
                "source": "approved_setup_receipt.reopened_configuration_readback",
                "setup_receipt_sha256": setup_receipt_digests[case],
                "configuration_readback_binding": setup_receipts[case]["reopened_configuration_readback_binding"],
                "configuration_readback": setup_receipts[case]["reopened_configuration_readback"],
                "current_model_identity_inspection": identity,
                "current_model_binding": updated.as_record(),
            }
            _append_jsonl_fsynced(events, {"event": "baseline_model_verified_unsolved",
                                           "case_id": case, "model_tag": updated.model_tag,
                                           "setup_receipt_sha256": setup_receipt_digests[case],
                                           "configuration_readback_revision": setup_receipts[case][
                                               "reopened_configuration_readback_binding"]["revision"],
                                           "current_revision": updated.revision,
                                           "remaining_budget_s": birth_budget.receipt()["remaining_to_deadline_s"]})

        for index, case in enumerate(CASE_ORDER, 1):
            if time.time() >= birth_budget.deadline_epoch_s - birth_budget.cleanup_reserve_s:
                raise CampaignError(f"birth budget entered cleanup reserve before {case} Study.run")
            current_store_path = _operation_store_path(managed_runner)
            store_stat = current_store_path.stat()
            if (current_store_path != operation_store_path or
                    (store_stat.st_dev, store_stat.st_ino) != store_identity):
                raise CampaignError("OperationStore path/inode changed after campaign approval; refusing a new slot")
            prior_ledger = _read_local_solve_ledger(ledger_path)
            if len(prior_ledger) != index - 1:
                raise CampaignError("native solve ledger no longer matches the exact next baseline pair slot")
            save_path = output_dir / f"static_shape_{case}_solved.mph"
            if save_path.exists() or save_path.is_symlink():
                raise CampaignError(f"solved project artifact already exists for {case}; refusing overwrite")
            timeout = birth_budget.rpc_timeout_s(600.0)
            binding = current[case]
            if binding.as_record() != records[case]:
                raise CampaignError("the exact originally approved ModelRef/revision changed; a new readback revision cannot create a new solve slot")
            verifier(binding)
            slot_key, slot_request_id = _slot_identity(project_id, binding, case)
            solve_call_id = uuid4().hex
            _append_jsonl_fsynced(events, {"event": "study_run_dispatch_started",
                                           "call_id": solve_call_id, "case_id": case,
                                           "submission_index": index,
                                           "model_tag": binding.model_tag,
                                           "idempotency_key": slot_key,
                                           "request_id": slot_request_id})
            response = _dispatch_solve_slot(
                managed_runner, binding,
                arguments={
                    "source_artifact": source_copy.name,
                    "entrypoint": "W24StaticShapeStudyRun#run",
                    "arguments": {
                        "action": "run", "case_id": case,
                        "expected_model_tag": binding.model_tag,
                        "submission_index": index,
                        "workspace_path": str(workspace),
                        "ledger_path": str(ledger_path),
                        "save_path": str(save_path),
                        "slot_idempotency_key": slot_key,
                        "approval_sha256": expected_approval_sha256,
                        "campaign_id": approval["campaign_id"],
                        "previous_slot_idempotency_key": (_slot_identity(
                            project_id, bindings["flat"], "flat")[0] if case == "step" else None),
                        "previous_model_tag": (bindings["flat"].model_tag if case == "step" else None),
                    },
                    "mode": "trusted",
                },
                idempotency_key=slot_key,
                request_id=slot_request_id,
                timeout_s=timeout,
                call_id=solve_call_id,
                journal=events,
            )
            result = managed_runner.setup_runner._java_action_readback(
                response, f"W24 static-shape Study.run/{case}")
            updated = managed_runner._updated_binding(binding, response)
            after_ledger = _read_local_solve_ledger(ledger_path)
            if (len(after_ledger) != index or after_ledger[-1].get("case_id") != case or
                    after_ledger[-1].get("slot_idempotency_key") != slot_key or
                    after_ledger[-1].get("approval_sha256") != expected_approval_sha256):
                raise CampaignError(f"{case} Study.run lacks exactly one matching durable local submission")
            if (result.get("status") != "NATIVE_STUDY_RUN_RETURNED" or
                    result.get("case_id") != case or result.get("study_tag") != "stdShape" or
                    result.get("model_tag") != binding.model_tag or
                    result.get("submission_index") != index or
                    result.get("study_run_calls_from_this_action") != 1 or
                    result.get("phase_initialization_included") is not True or
                    result.get("save_path") != str(save_path)):
                raise CampaignError(f"{case} Study.run returned a mismatched or incomplete native receipt")
            saved = _project_path(workspace, save_path, must_exist=True)
            digest = sha256_file(saved)
            if (saved.stat().st_size <= 0 or result.get("size_bytes") != saved.stat().st_size or
                    result.get("sha256") != digest):
                raise CampaignError(f"{case} post-solve MPH save did not match native size/SHA readback")
            current[case] = updated
            receipt["actual_study_run_submissions"] = len(after_ledger)
            receipt["cases"][case] = {
                "status": "STUDY_RUN_RETURNED_CAPTURE_PENDING",
                "model_binding": updated.as_record(),
                "study_run": dict(result),
                "saved_mph": {"path": str(saved), "size_bytes": saved.stat().st_size,
                              "sha256": digest},
            }
            _append_jsonl_fsynced(events, {"event": "study_run_terminal_succeeded",
                                           "call_id": solve_call_id, "case_id": case,
                                           "submission_index": index,
                                           "job_id": response.get("execution", {}).get("job_id")
                                           if isinstance(response.get("execution"), Mapping) else None,
                                           "native_result_sha256": hashlib.sha256(
                                               json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()})

            remaining_capture = birth_budget.rpc_timeout_s(600.0)
            prior_timeout = getattr(managed_runner, "timeout_s", None)
            if prior_timeout is not None:
                managed_runner.timeout_s = remaining_capture
            try:
                updated, analysis = capture_static_shape_history(
                    managed_runner, updated, case_id=case,
                    expected_capture_source_sha256=expected_capture_source_sha256)
            finally:
                if prior_timeout is not None:
                    managed_runner.timeout_s = prior_timeout
            if (analysis.get("case_id") != case or analysis.get("model_tag") != updated.model_tag or
                    analysis.get("managed_binding") != updated.as_record()):
                raise CampaignError(f"{case} raw-history receipt does not bind the post-solve ModelRef")
            current[case] = updated
            receipt["cases"][case].update({"status": "SOLVED_AND_RAW_HISTORY_CAPTURED",
                                           "capture": analysis})
            _append_jsonl_fsynced(events, {"event": "native_history_capture_completed",
                                           "case_id": case,
                                           "raw_sha256": analysis.get("raw_capture", {}).get("sha256")})

        comparison = compare_flat_step_captures(
            receipt["cases"]["flat"]["capture"], receipt["cases"]["step"]["capture"],
            expected_project_id=project_id, expected_workspace=workspace)
        receipt.update({"status": "BASELINE_PAIR_CAPTURED_DESCRIPTIVE_ONLY",
                        "actual_study_run_submissions": len(_read_local_solve_ledger(ledger_path)),
                        "comparison": comparison,
                        "birth_budget_at_finish": birth_budget.receipt()})
        _append_jsonl_fsynced(events, {"event": "baseline_pair_campaign_complete",
                                       "study_run_submissions": receipt["actual_study_run_submissions"],
                                       "status": receipt["status"]})
        return receipt
    except BaseException as exc:
        try:
            actual = len(_read_local_solve_ledger(ledger_path))
        except Exception:
            actual = None
        receipt.update({"status": "FAIL_OR_INCOMPLETE_NO_RETRY",
                        "actual_study_run_submissions": actual,
                        "error": f"{type(exc).__name__}: {exc}",
                        "birth_budget_at_failure": birth_budget.receipt()})
        _append_jsonl_fsynced(events, {"event": "baseline_pair_campaign_stopped_no_retry",
                                       "study_run_submissions": actual,
                                       "error": receipt["error"]})
        return receipt
