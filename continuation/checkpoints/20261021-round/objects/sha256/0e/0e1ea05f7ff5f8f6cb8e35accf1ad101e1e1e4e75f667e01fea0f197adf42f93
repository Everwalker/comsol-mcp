#!/usr/bin/env python3
"""Two-phase, externally approved W24 Maxwell and gel control campaign.

This module never creates a Server or Worker.  Preparation uses an already
admitted runtime to build/read back the two unsolved fixtures and emits a
PENDING approval input.  Execution consumes the original in-memory handles,
the existing ApprovalEnvelope, and the one Server-birth BirthBudget.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.run_native_w24_cure_science import (
    BirthBudget, CampaignError, ManagedModelBinding, _capture_worker_process_identity,
    _identity_same, sha256,
)
from tools.w24_cure_v2_capture import (
    CONTROL_COORDINATES_V2_M, CONTROL_CAPTURE_SCHEMA_V2,
    validate_capture_artifact, validate_control_configuration_readback,
)
from tools.w24_cure_law_v2 import maxwell_control_reference


REPO = Path(__file__).resolve().parents[1]
CAMPAIGN_SCHEMA = "W24_PHYSICAL_CONTROL_CANDIDATE_V1"
APPROVAL_INPUT_SCHEMA = "W24_PHYSICAL_CONTROL_APPROVAL_INPUT_V1"
APPROVAL_SCHEMA = "W24_PHYSICAL_CONTROL_APPROVAL_V1"
CONTROL_PLAN = {
    "schema": "W24_PHYSICAL_CONTROL_PLAN_V1",
    "slots": [
        {"index": 1, "case_id": "maxwell_ramp_hold_control", "study_tag": "stdMaxwell",
         "stored_times_s": "range(0[s],1[s],901[s])", "stored_time_count": 902,
         "analytic_check_times_s": [1.0, 301.0, 601.0, 901.0],
         "spatial_grid": "100um_cube_xyz_fractions_25_50_75_percent",
         "spatial_point_count": 27, "expressions": ["solid.sx", "solid.sy"],
         "units": ["Pa", "Pa"],
         "sigma_error_limit": "max(100[Pa],0.01*abs(analytic_sigma[Pa]))",
         "branch_reference_state": "UNVERIFIED_UNTIL_NATIVE_OBSERVATION"},
        {"index": 2, "case_id": "gel_stress_free_control", "study_tag": "stdGel",
         "stored_times_s": "range(0[s],0.5[s],3[s])", "stored_time_count": 7,
         "post_gel_check_times_s": [2.0, 2.5, 3.0],
         "activation_check_times_s": {"solid.isactive": [2.0, 2.5, 3.0],
                                       "solid.wasactive": [2.5, 3.0]},
         "spatial_grid": "100um_cube_xyz_fractions_25_50_75_percent",
         "spatial_point_count": 27,
         "stress_components": ["solid.sx", "solid.sy", "solid.sz", "solid.sxy",
                               "solid.sxz", "solid.syz"],
         "stress_units": ["Pa", "Pa", "Pa", "Pa", "Pa", "Pa"],
         "post_gel_component_abs_limit_pa": 100.0,
         "activation_exact_values": {"solid.isactive": 1.0, "solid.wasactive": 1.0},
         "activation_reference_state": "UNVERIFIED_UNTIL_NATIVE_OBSERVATION"},
    ],
    "planned_study_run_submissions": 2,
    "study_run_order_is_fixed": True,
    "stop_after_first_failed_or_unknown_slot": True,
    "single_server_and_worker_birth_budget_seconds": 3600.0,
    "cleanup_reserve_seconds": 90.0,
    "sampled_combined_server_worker_rss_threshold_bytes": 6 * 1024**3,
    "rss_sampling_interval_seconds": 1.0,
    "rss_threshold_is_hard_cap": False,
    "maximum_single_saved_mph_bytes": 1024**3,
    "maximum_total_capture_json_bytes": 50 * 1024**2,
    "study_run_unknown_or_save_failure_is_terminal_no_retry": True,
    "native_activation_and_maxwell_branch_reference_semantics": "UNVERIFIED",
    "native_acceptance": "NOT_RUN",
}
CONTROL_PLAN_SHA256 = hashlib.sha256(
    json.dumps(CONTROL_PLAN, sort_keys=True, separators=(",", ":"),
               ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
SOURCE_PATHS = {
    "tools/run_native_w24_physical_controls.py": Path(__file__).resolve(),
    "tools/run_native_w24_cure_science.py": REPO / "tools/run_native_w24_cure_science.py",
    "tools/w24_cure_v2_capture.py": REPO / "tools/w24_cure_v2_capture.py",
    "tools/w24_cure_law_v2.py": REPO / "tools/w24_cure_law_v2.py",
    "tools/java/W24CureLawV2ControlFixture.java": REPO / "tools/java/W24CureLawV2ControlFixture.java",
    "comsol_mcp/_control_daemon.py": REPO / "comsol_mcp/_control_daemon.py",
    "comsol_mcp/_operation_store.py": REPO / "comsol_mcp/_operation_store.py",
    "comsol_mcp/_execution_contract.py": REPO / "comsol_mcp/_execution_contract.py",
}
EXPECTED_CASES = (
    ("maxwell_ramp_hold_control", "stdMaxwell", "build_maxwell_ramp_hold",
     "readback_maxwell_ramp_hold", "capture_maxwell_control"),
    ("gel_stress_free_control", "stdGel", "build_gel_stress_free",
     "readback_gel_stress_free", "capture_gel_control"),
)
MAXIMUM_APPROVAL_WAIT_POLL_S = 1.0


class PhysicalControlError(CampaignError):
    """A control input, public route, runtime binding, or comparison failed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise PhysicalControlError(f"{label} must be a lowercase SHA-256")
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> str:
    if path.exists() or path.is_symlink():
        raise PhysicalControlError("candidate/evidence path already exists; evidence is append-only")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise PhysicalControlError("candidate/evidence parent must be an existing nonsymlink directory")
    raw = _canonical_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise PhysicalControlError("control event ledger must be an existing regular nonsymlink file")
    raw = _canonical_bytes(dict(row)) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _write_empty(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PhysicalControlError("new control ledger path already exists")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _control_ledger_rows(path: Path, *, candidate: Mapping[str, Any],
                         approval_sha: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise PhysicalControlError("original native solve ledger is missing or aliased")
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PhysicalControlError(f"native solve ledger row {number} is malformed") from exc
        if (not isinstance(row, Mapping) or row.get("event") != "study_run_submitted" or
                row.get("submission_index") != number or
                row.get("campaign_id") != candidate.get("campaign_id") or
                row.get("approval_sha256") != approval_sha or
                row.get("control_plan_sha256") != candidate.get("control_plan_sha256") or
                row.get("case_id") not in {case[0] for case in EXPECTED_CASES} or
                not isinstance(row.get("slot_idempotency_key"), str) or
                len(row["slot_idempotency_key"]) != 64):
            raise PhysicalControlError("native solve ledger no longer authenticates the exact original campaign/slot")
        expected_case, expected_study = EXPECTED_CASES[number - 1][:2] if number <= 2 else (None, None)
        if row.get("case_id") != expected_case or row.get("study_tag") != expected_study:
            raise PhysicalControlError("native solve ledger order differs from the frozen two-slot campaign")
        rows.append(dict(row))
    if len(rows) > 2 or len({row["slot_idempotency_key"] for row in rows}) != len(rows):
        raise PhysicalControlError("native solve ledger contains too many slots or duplicate slot keys")
    return rows


def build_control_candidate(*, campaign_id: str, approval_root: Path,
                            approval_inbox_path: Path) -> dict[str, Any]:
    """Create a pending, hashable two-slot candidate; never create approval."""
    if not isinstance(campaign_id, str) or not campaign_id.replace("-", "").replace("_", "").isalnum() or len(campaign_id) > 64:
        raise PhysicalControlError("campaign_id must be a stable alphanumeric token")
    from tools.run_native_w24_static_shape_sensitivity_lifecycle import _private_approval_root
    root = _private_approval_root(approval_root.expanduser().absolute())
    inbox = approval_inbox_path.expanduser().absolute()
    try:
        inbox.relative_to(root)
    except ValueError as exc:
        raise PhysicalControlError("external approval inbox must stay inside its private root") from exc
    if inbox.exists() or inbox.is_symlink():
        raise PhysicalControlError("external approval inbox must be a new one-shot path")
    source_pins = {}
    for relative, path in SOURCE_PATHS.items():
        if path.is_symlink() or not path.is_file():
            raise PhysicalControlError(f"frozen control source is absent or aliased: {relative}")
        source_pins[relative] = sha256(path)
    fixture_path = SOURCE_PATHS["tools/java/W24CureLawV2ControlFixture.java"]
    candidate = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "PENDING_NOT_APPROVED_NOT_RUN",
        "campaign_id": campaign_id,
        "control_plan": CONTROL_PLAN,
        "control_plan_sha256": CONTROL_PLAN_SHA256,
        "source_sha256": source_pins,
        "java_fixture_source": str(fixture_path),
        "approval_protocol": {
            "inbox_schema": "W24_STATIC_SHAPE_SENSITIVITY_APPROVAL_INBOX_V1",
            "approval_root": str(root),
            "approval_inbox_path": str(inbox),
            "expected_approval_sha256_must_come_from_external_inbox": True,
            "one_observation_no_replacement_hash_retry": True,
            "same_live_adapter_worker_and_birth_budget_required": True,
        },
        "native_acceptance": "NOT_RUN",
    }
    candidate["candidate_sha256"] = _digest(candidate)
    return candidate


def write_control_candidate(path: Path, candidate: Mapping[str, Any]) -> str:
    value = dict(candidate)
    declared = value.pop("candidate_sha256", None)
    digest = _digest(value)
    if declared != digest or value.get("control_plan_sha256") != CONTROL_PLAN_SHA256:
        raise PhysicalControlError("candidate is not the exact fixed two-slot control plan")
    value["candidate_sha256"] = digest
    _write_new_json(path, value)
    return digest


def _validate_candidate(candidate: Mapping[str, Any], expected_sha256: str) -> dict[str, Any]:
    row = dict(candidate)
    declared = _sha(row.pop("candidate_sha256", None), "candidate_sha256")
    expected = _sha(expected_sha256, "external expected candidate SHA-256")
    if (declared != expected or _digest(row) != expected or
            row.get("schema") != CAMPAIGN_SCHEMA or
            row.get("status") != "PENDING_NOT_APPROVED_NOT_RUN" or
            row.get("control_plan") != CONTROL_PLAN or
            row.get("control_plan_sha256") != CONTROL_PLAN_SHA256 or
            row.get("native_acceptance") != "NOT_RUN"):
        raise PhysicalControlError("candidate/hash/plan/status differs from this exact pending control candidate")
    pins = row.get("source_sha256")
    if not isinstance(pins, Mapping) or set(pins) != set(SOURCE_PATHS):
        raise PhysicalControlError("candidate source closure is incomplete")
    for relative, path in SOURCE_PATHS.items():
        if path.is_symlink() or not path.is_file() or sha256(path) != _sha(pins.get(relative), relative):
            raise PhysicalControlError(f"control source changed after candidate freeze: {relative}")
    return {**row, "candidate_sha256": expected}


def _birth_budget_context(adapter: Any, monitor: Any, birth_budget: BirthBudget,
                          *, clock: Callable[[], float]) -> dict[str, Any]:
    if not isinstance(birth_budget, BirthBudget):
        raise PhysicalControlError("controls require the existing owned-Server BirthBudget object")
    if (birth_budget.budget_s != 3600.0 or birth_budget.cleanup_reserve_s != 90.0 or
            not math.isfinite(birth_budget.birth_epoch_s)):
        raise PhysicalControlError("control campaign may not reset or change the 3600s/90s Server-birth budget")
    server = getattr(adapter, "server", None)
    proc = getattr(server, "proc", None)
    server_identity = getattr(server, "process_identity", None)
    if (proc is None or type(getattr(proc, "pid", None)) is not int or proc.poll() is not None or
            not isinstance(server_identity, Mapping) or server_identity.get("pid") != proc.pid or
            not isinstance(server_identity.get("birth"), str) or not server_identity.get("birth")):
        raise PhysicalControlError("exact owned Server Popen and OS birth identity are required")
    setup_runner = getattr(adapter, "setup_runner", None)
    to_epoch = getattr(setup_runner, "_mac_birth_epoch", None)
    if not callable(to_epoch):
        raise PhysicalControlError("same-runtime OS birth-time conversion is unavailable")
    server_epoch = float(to_epoch(server_identity["birth"]))
    if not math.isclose(server_epoch, birth_budget.birth_epoch_s, rel_tol=0.0, abs_tol=1e-6):
        raise PhysicalControlError("BirthBudget does not originate from this exact Server Popen birth")
    worker_identity = getattr(adapter, "_worker_process_identity", None)
    current_worker = _capture_worker_process_identity(server)
    if (not isinstance(worker_identity, Mapping) or not _identity_same(worker_identity, current_worker) or
            getattr(adapter, "worker_sessions", None) != 1):
        raise PhysicalControlError("control campaign requires the original connected Worker epoch with live handles")
    worker_birth = worker_identity.get("birth")
    if not isinstance(worker_birth, str) or float(to_epoch(worker_birth)) + 1e-5 < server_epoch:
        raise PhysicalControlError("Worker Popen birth is missing or predates its Server")
    if (getattr(monitor, "server_pid", None) != proc.pid or
            getattr(monitor, "expected_worker_pid", None) != worker_identity.get("pid") or
            getattr(monitor, "worker_required_live", None) is not True or
            getattr(monitor, "threshold_bytes", None) != 6 * 1024**3 or
            getattr(monitor, "interval_seconds", None) != 1.0 or
            getattr(monitor, "server_birth_ms", None) != round(server_epoch * 1000) or
            getattr(monitor, "expected_worker_birth_ms", None) != round(float(to_epoch(worker_birth)) * 1000)):
        raise PhysicalControlError("exact same-runtime RSS monitor, Worker identity, or 6GiB/1s policy differs")
    if clock() >= birth_budget.deadline_epoch_s - birth_budget.cleanup_reserve_s:
        raise PhysicalControlError("Server-birth budget is in cleanup reserve; no control operation may start")
    sample = monitor.sample_once()
    if (not isinstance(sample, Mapping) or sample.get("status") != "SAMPLED_WITHIN_THRESHOLD" or
            monitor.stop_reason is not None or
            sample.get("combined_comsol_worker_rss_bytes", 6 * 1024**3) >= 6 * 1024**3):
        raise PhysicalControlError("existing sampled RSS admission did not admit this control action")
    return {
        "server": {"pid": proc.pid, "birth": server_identity["birth"],
                   "birth_epoch_s": server_epoch},
        "worker": {"pid": worker_identity["pid"], "birth": worker_birth,
                   "birth_epoch_s": float(to_epoch(worker_birth)),
                   "session_count": getattr(adapter, "worker_sessions", None)},
        "birth_budget": birth_budget.receipt(now_epoch_s=clock()),
        "rss_sample": {key: sample.get(key) for key in
                       ("observed_at_utc", "status", "combined_comsol_worker_rss_bytes",
                        "threshold_bytes", "target_sample_interval_seconds", "hard_cap")},
    }


def _operation_store_binding(adapter: Any) -> dict[str, Any]:
    daemon = getattr(adapter, "daemon", None)
    store = getattr(daemon, "store", None)
    raw_path = getattr(store, "path", None)
    if not isinstance(raw_path, (str, os.PathLike)):
        raise PhysicalControlError("active project OperationStore path is unavailable")
    path = Path(raw_path)
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        raise PhysicalControlError("active OperationStore must be an exact regular nonsymlink file")
    st = path.stat()
    authority = getattr(daemon, "project_authority", None)
    project_id = getattr(adapter, "project_id", None)
    project = authority.get_project(project_id) if authority is not None else None
    workspace = Path(str(getattr(adapter, "workspace", "")))
    if (not isinstance(project_id, str) or not project_id or not isinstance(project, Mapping) or
            project.get("project_id") != project_id or workspace.is_symlink() or
            Path(str(project.get("workspace", ""))).resolve(strict=True) != workspace.resolve(strict=True)):
        raise PhysicalControlError("active daemon did not read the exact registered project/workspace binding")
    return {"project_id": project_id, "project_workspace": str(workspace.resolve(strict=True)),
            "operation_store_path": str(path), "device": st.st_dev, "inode": st.st_ino,
            "store_object_id": id(store)}


def _verify_no_unknown_or_active(adapter: Any) -> None:
    from tools.run_native_w24_cure_preflight import (
        _project_job_inventory, _worker_request_activity_inventory,
    )
    jobs = _project_job_inventory(adapter.daemon, adapter.project_id)
    activity = _worker_request_activity_inventory(adapter.daemon.store, adapter.project_id)
    if (jobs.get("status") != "TERMINAL" or jobs.get("active") != [] or jobs.get("unknown") != [] or
            activity.get("safe_to_cleanup") is not True or
            activity.get("execution_state_unknown_request_ids") != [] or
            activity.get("pending_request_ids") != [] or
            activity.get("orphan_observed_request_ids") != [] or
            activity.get("ambiguous_events") != []):
        raise PhysicalControlError("existing project OperationStore/Worker ledger is active or UNKNOWN")


def _same_project_binding(adapter: Any, binding: ManagedModelBinding) -> None:
    project_id = adapter.project_id
    if binding.project_id != project_id:
        raise PhysicalControlError("prepared control model changed its registered project")
    backend = adapter.daemon.backend
    stored = backend.model_project_binding(dict(binding.model_ref))
    if (not isinstance(stored, Mapping) or stored.get("attribution") != "PROJECT_BOUND" or
            stored.get("project_id") != project_id):
        raise PhysicalControlError("prepared ModelRef lacks authoritative project association in the live backend")
    key = backend._model_project_key(dict(binding.model_ref))
    revision = adapter.daemon.store.get_metadata("revisions", key)
    if (not isinstance(revision, Mapping) or revision.get("project_id") != project_id or
            revision.get("model_ref") != dict(binding.model_ref) or
            revision.get("revision") != binding.revision):
        raise PhysicalControlError("prepared ModelRef revision differs from its authoritative OperationStore row")


def _slot_key(candidate: Mapping[str, Any], approval_sha: str,
              case_id: str, binding: ManagedModelBinding, source_sha: str) -> str:
    return _digest({"campaign_id": candidate["campaign_id"],
                    "candidate_sha256": candidate["candidate_sha256"],
                    "approval_sha256": approval_sha,
                    "control_plan_sha256": candidate["control_plan_sha256"],
                    "case_id": case_id, "project_id": binding.project_id,
                    "session_id": binding.session_id,
                    "model_ref": dict(binding.model_ref), "revision": binding.revision,
                    "source_sha256": source_sha})


def _operation_identities(action_ref: Mapping[str, Any]) -> dict[str, Any]:
    identity = action_ref.get("public_identity")
    if not isinstance(identity, Mapping):
        raise PhysicalControlError("verified action lacks its actual public OperationStore identity")
    required = ("request_id", "operation_id", "idempotency_key", "request_hash", "job_id",
                "project_id", "session_id", "model_ref", "revision_before", "revision_after",
                "source_path", "source_sha256")
    if any(identity.get(key) is None for key in required):
        raise PhysicalControlError("verified action identity is incomplete")
    return {key: identity[key] for key in required}


def _record_action(adapter: Any, *, binding: ManagedModelBinding, action: str,
                   arguments: Mapping[str, Any], response_dir: Path,
                   response_label: str, request_key: str,
                   timeout_s: float) -> tuple[ManagedModelBinding, dict[str, Any], dict[str, Any], dict[str, Any]]:
    request_id = f"w24-control-request-{request_key}"
    idempotency_key = f"w24-control-slot-{request_key}"
    before = binding
    updated, response, result = adapter._fixture_action(
        binding, action, arguments, timeout_s=timeout_s,
        source_fixture=adapter.v2_control_fixture,
        request_id=request_id, idempotency_key=idempotency_key)
    action_ref = adapter._record_public_java_action(
        action=action, response=response, binding_before=before, binding_after=updated,
        source_fixture=adapter.v2_control_fixture, response_dir=response_dir,
        response_label=response_label)
    reauthenticated = adapter._reauthenticate_public_java_action(action_ref)
    if (reauthenticated.get("public_identity") != action_ref.get("public_identity") or
            reauthenticated.get("readback_data") != action_ref.get("readback_data")):
        raise PhysicalControlError("post-operation reauthentication changed the original public response identity")
    return updated, dict(response), dict(result), action_ref


@dataclass
class PreparedPhysicalControls:
    candidate: dict[str, Any]
    candidate_sha256: str
    approval_input_path: Path
    approval_input_sha256: str
    adapter: Any
    monitor: Any
    birth_budget: BirthBudget
    context_identity: dict[str, Any]
    project_binding: dict[str, Any]
    project_dir: Path
    evidence_dir: Path
    ledger_path: Path
    event_log: Path
    model_bindings: dict[str, ManagedModelBinding]
    setup_refs: dict[str, list[dict[str, Any]]]
    used: bool = False
    unknown: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


def prepare_physical_control_campaign(*, adapter: Any, monitor: Any,
                                      candidate: Mapping[str, Any],
                                      expected_candidate_sha256: str,
                                      birth_budget: BirthBudget,
                                      approval_input_path: Path,
                                      evidence_dir: Path,
                                      clock: Callable[[], float] = time.time) -> PreparedPhysicalControls:
    """Build and authenticate two unsolved controls, then emit PENDING input."""
    frozen = _validate_candidate(candidate, expected_candidate_sha256)
    protocol = frozen.get("approval_protocol")
    if (not isinstance(protocol, Mapping) or
            protocol.get("expected_approval_sha256_must_come_from_external_inbox") is not True or
            protocol.get("one_observation_no_replacement_hash_retry") is not True or
            protocol.get("same_live_adapter_worker_and_birth_budget_required") is not True):
        raise PhysicalControlError("control candidate does not use the one-shot external approval protocol")
    if getattr(adapter, "cure_law_v2_enabled", None) is not True:
        raise PhysicalControlError("control models require a validated public W24 v2 setup mode")
    context = _birth_budget_context(adapter, monitor, birth_budget, clock=clock)
    project_binding = _operation_store_binding(adapter)
    _verify_no_unknown_or_active(adapter)
    inbox = Path(str(protocol.get("approval_inbox_path", "")))
    approval_root = Path(str(protocol.get("approval_root", "")))
    if (inbox != inbox.absolute() or inbox.exists() or inbox.is_symlink() or
            approval_input_path.exists() or approval_input_path.is_symlink()):
        raise PhysicalControlError("one-shot approval input/inbox paths must be new and non-aliased")
    try:
        inbox.relative_to(approval_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise PhysicalControlError("external inbox is outside the candidate private approval root") from exc
    workspace = Path(project_binding["project_workspace"])
    outputs = workspace / "outputs"
    if outputs.is_symlink() or not outputs.is_dir() or outputs.resolve(strict=True) != outputs:
        raise PhysicalControlError("registered project outputs directory is unavailable or aliased")
    project_dir = outputs / f"{frozen['campaign_id']}_physical_controls"
    if project_dir.exists() or project_dir.is_symlink():
        raise PhysicalControlError("physical-control project directory already exists; no campaign replay")
    project_dir.mkdir(mode=0o700)
    ledger_path = project_dir / "study_run_events.jsonl"
    event_log = project_dir / "control_actions.jsonl"
    _write_empty(ledger_path)
    _write_empty(event_log)
    evidence_dir = evidence_dir.expanduser().absolute()
    if evidence_dir.exists() or evidence_dir.is_symlink() or not evidence_dir.parent.is_dir():
        raise PhysicalControlError("physical-control evidence directory must be new under an existing parent")
    evidence_dir.mkdir(mode=0o700)

    fixture = Path(adapter.v2_control_fixture)
    source_sha = sha256(fixture)
    pinned_fixture = frozen["source_sha256"]["tools/java/W24CureLawV2ControlFixture.java"]
    if source_sha != pinned_fixture:
        raise PhysicalControlError("registered project Java fixture differs from the external candidate source hash")
    models: dict[str, ManagedModelBinding] = {}
    setup_refs: dict[str, list[dict[str, Any]]] = {}
    approval_sha_pending = "PENDING_EXTERNAL_APPROVAL"
    setup_response_dir = evidence_dir / "setup_responses"
    setup_response_dir.mkdir(mode=0o700)
    try:
        for case_id, study_tag, build_action, readback_action, _capture_action in EXPECTED_CASES:
            _verify_no_unknown_or_active(adapter)
            context = _birth_budget_context(adapter, monitor, birth_budget, clock=clock)
            name = f"w24pc_{frozen['campaign_id']}_{case_id}"
            create_key = _digest({"campaign": frozen["candidate_sha256"], "case": case_id,
                                  "phase": "model_create"})
            response = adapter._dispatch(
                "model_create", {"name": name}, timeout_s=birth_budget.rpc_timeout_s(120.0, now_epoch_s=clock()),
                request_id=f"w24-control-create-{create_key}",
                idempotency_key=f"w24-control-create-slot-{create_key}")
            execution = response.get("execution") if isinstance(response, Mapping) else None
            ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
            if not isinstance(ref, Mapping) or not isinstance(ref.get("model_tag"), str):
                raise PhysicalControlError("public model_create omitted the exact newly minted ModelRef")
            binding = ManagedModelBinding.from_adopt_response(
                response, project_id=adapter.project_id, expected_model_tag=ref["model_tag"])
            if not adapter._validate_current_worker_binding(binding, name=case_id):
                raise PhysicalControlError("created control model did not bind to the current Worker epoch")
            _same_project_binding(adapter, binding)
            refs: list[dict[str, Any]] = [{
                "phase": "model_create", "response_execution": dict(execution),
                "binding": binding.as_record(),
            }]
            for action, label in ((build_action, "build"), (readback_action, "readback")):
                _verify_no_unknown_or_active(adapter)
                context = _birth_budget_context(adapter, monitor, birth_budget, clock=clock)
                action_key = _digest({"campaign": frozen["candidate_sha256"], "case": case_id,
                                      "phase": action, "model_ref": dict(binding.model_ref),
                                      "revision": binding.revision})
                updated, _response, result, action_ref = _record_action(
                    adapter, binding=binding, action=action,
                    arguments={"campaign_id": frozen["campaign_id"],
                               "control_plan_sha256": frozen["control_plan_sha256"],
                               "approval_sha256": approval_sha_pending},
                    response_dir=setup_response_dir, response_label=f"{case_id}_{label}",
                    request_key=action_key,
                    timeout_s=birth_budget.rpc_timeout_s(180.0, now_epoch_s=clock()))
                readback = action_ref.get("readback_data")
                if not isinstance(readback, Mapping):
                    raise PhysicalControlError(f"{case_id} {label} omitted its public readback")
                validate_control_configuration_readback(readback, case_id=case_id)
                if readback.get("study_tag") != study_tag or result.get("case_id") != case_id:
                    raise PhysicalControlError(f"{case_id} {label} route changed its exact case/study binding")
                if label == "readback":
                    solver_tag = readback.get("solver_sequence")
                    if not isinstance(solver_tag, str) or not solver_tag:
                        raise PhysicalControlError(f"{case_id} exact attached solver tag is missing")
                    adapter.slot_solver_tags[(case_id, study_tag)] = solver_tag
                _same_project_binding(adapter, updated)
                refs.append({"phase": label, "action": action,
                             "operation_identity": _operation_identities(action_ref),
                             "binding_after": updated.as_record(),
                             "readback_data": dict(readback),
                             "response_evidence": dict(action_ref["response_evidence"]),
                             "capture_validation": dict(action_ref["capture_validation"])})
                binding = updated
                adapter.models[case_id] = binding
                context = _birth_budget_context(adapter, monitor, birth_budget, clock=clock)
            models[case_id] = binding
            setup_refs[case_id] = refs
        actual_store = _operation_store_binding(adapter)
        if (actual_store["operation_store_path"] != project_binding["operation_store_path"] or
                actual_store["device"] != project_binding["device"] or
                actual_store["inode"] != project_binding["inode"] or
                actual_store["store_object_id"] != project_binding["store_object_id"]):
            raise PhysicalControlError("control setup switched to a different project OperationStore")
        approval_input = {
            "schema": APPROVAL_INPUT_SCHEMA, "status": "PENDING_NOT_APPROVED",
            "campaign_id": frozen["campaign_id"],
            "candidate_sha256": frozen["candidate_sha256"],
            "control_plan": CONTROL_PLAN,
            "control_plan_sha256": CONTROL_PLAN_SHA256,
            "project_binding": {k: v for k, v in project_binding.items() if k != "store_object_id"},
            "server_worker_birth_and_budget": context,
            "model_bindings_after_public_setup_readback": {
                case: models[case].as_record() for case, _, _, _, _ in EXPECTED_CASES},
            "authenticated_setup_operations": setup_refs,
            "source_sha256": dict(frozen["source_sha256"]),
            "ordered_slots": [{"index": row["index"], "case_id": row["case_id"],
                               "study_tag": row["study_tag"],
                               "planned_study_run_submissions": 1}
                              for row in CONTROL_PLAN["slots"]],
            "old_ten_slot_approval_inherited": False,
            "birth_budget_is_same_server_birth_object": True,
            "approval_is_pending_and_not_self_generated": True,
            "native_study_run_submissions": 0,
            "native_acceptance": "NOT_RUN",
        }
        approval_input_sha = _write_new_json(approval_input_path.expanduser().absolute(), approval_input)
        _append_jsonl(event_log, {"event": "PREPARED_AWAITING_EXTERNAL_APPROVAL",
                                  "campaign_id": frozen["campaign_id"],
                                  "candidate_sha256": frozen["candidate_sha256"],
                                  "control_plan_sha256": CONTROL_PLAN_SHA256,
                                  "approval_input_path": str(approval_input_path.expanduser().absolute()),
                                  "approval_input_sha256": approval_input_sha,
                                  "birth_budget": birth_budget.receipt(now_epoch_s=clock()),
                                  "native_study_run_submissions": 0})
        return PreparedPhysicalControls(
            candidate=frozen, candidate_sha256=frozen["candidate_sha256"],
            approval_input_path=approval_input_path.expanduser().absolute(),
            approval_input_sha256=approval_input_sha, adapter=adapter, monitor=monitor,
            birth_budget=birth_budget, context_identity=context,
            project_binding=project_binding, project_dir=project_dir,
            evidence_dir=evidence_dir, ledger_path=ledger_path, event_log=event_log,
            model_bindings=models, setup_refs=setup_refs)
    except BaseException as exc:
        try:
            _append_jsonl(event_log, {"event": "PREPARE_FAILED_OR_UNKNOWN_NO_REENTRY",
                                      "error_type": type(exc).__name__, "error": str(exc),
                                      "native_study_run_submissions": 0})
        except BaseException:
            pass
        raise


def _check_prepared_runtime(prepared: PreparedPhysicalControls,
                            *, clock: Callable[[], float]) -> dict[str, Any]:
    if prepared.used:
        raise PhysicalControlError("prepared controls were already consumed; approval/hash reentry is forbidden")
    context = _birth_budget_context(prepared.adapter, prepared.monitor,
                                   prepared.birth_budget, clock=clock)
    store = _operation_store_binding(prepared.adapter)
    if any(store.get(key) != prepared.project_binding.get(key)
           for key in ("project_id", "project_workspace", "operation_store_path", "device", "inode", "store_object_id")):
        raise PhysicalControlError("project, workspace, or original OperationStore changed after preparation")
    for case, binding in prepared.model_bindings.items():
        _same_project_binding(prepared.adapter, binding)
        if prepared.adapter.models.get(case) != binding:
            raise PhysicalControlError("prepared model handle/revision changed while external approval was pending")
    _verify_no_unknown_or_active(prepared.adapter)
    return context


def _approval_envelope(prepared: PreparedPhysicalControls, envelope: Any) -> tuple[str, dict[str, Any]]:
    from tools.run_native_w24_static_shape_sensitivity_lifecycle import (
        ApprovalEnvelope, _validate_approval_inbox, _read_object, sha256_file,
    )
    if not isinstance(envelope, ApprovalEnvelope):
        raise PhysicalControlError("control execution requires the existing validated ApprovalEnvelope type")
    exact = _validate_approval_inbox(
        candidate=prepared.candidate,
        approval_input_path=prepared.approval_input_path,
        approval_input_sha256=prepared.approval_input_sha256)
    if exact != envelope:
        raise PhysicalControlError("external approval inbox changed after its first observation; no replacement hash")
    if (envelope.approval_path.is_symlink() or not envelope.approval_path.is_file() or
            sha256_file(envelope.approval_path) != envelope.expected_approval_sha256):
        raise PhysicalControlError("approval bytes differ from the externally expected SHA-256")
    approval = _read_object(envelope.approval_path, "physical-control external approval")
    approved_slots = [
        {"index": slot["index"], "case_id": slot["case_id"],
         "study_tag": slot["study_tag"], "study_run_submissions": 1}
        for slot in CONTROL_PLAN["slots"]
    ]
    if (approval.get("schema") != APPROVAL_SCHEMA or
            approval.get("status") != "APPROVED" or
            approval.get("campaign_id") != prepared.candidate["campaign_id"] or
            approval.get("candidate_sha256") != prepared.candidate_sha256 or
            approval.get("approval_input_sha256") != prepared.approval_input_sha256 or
            approval.get("control_plan_sha256") != CONTROL_PLAN_SHA256 or
            approval.get("study_run_submissions") != 2 or
            approval.get("ordered_slots") != approved_slots or
            approval.get("source_sha256") != prepared.candidate["source_sha256"]):
        raise PhysicalControlError(
            "external approval does not authorize the exact candidate, pending setup input, two ordered slots, and source bytes")
    return envelope.expected_approval_sha256, approval


def _verify_capture_bytes(action_ref: Mapping[str, Any], project_root: Path) -> dict[str, Any]:
    artifact = action_ref.get("artifact")
    if not isinstance(artifact, Mapping):
        raise PhysicalControlError("authenticated control capture omitted its raw artifact receipt")
    path = Path(str(artifact.get("path", "")))
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        raise PhysicalControlError("capture artifact is absent, aliased, or not a regular file")
    try:
        path.relative_to(project_root.resolve(strict=True))
    except ValueError as exc:
        raise PhysicalControlError("capture artifact escaped its registered project") from exc
    size, digest = path.stat().st_size, sha256(path)
    if (size <= 0 or size != artifact.get("size_bytes") or digest != artifact.get("sha256") or
            size > 25 * 1024**2):
        raise PhysicalControlError("capture bytes do not match the authenticated public artifact receipt/resource limit")
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhysicalControlError("capture artifact is not complete JSON") from exc
    if not isinstance(row, Mapping):
        raise PhysicalControlError("capture raw artifact must be an object")
    return dict(row)


def _compare_control_capture(artifact: Mapping[str, Any], *, case_id: str,
                             candidate: Mapping[str, Any], approval_sha: str,
                             slot_key: str) -> dict[str, Any]:
    shape_report = validate_capture_artifact(artifact,
        expected_action=("capture_maxwell_control" if case_id == "maxwell_ramp_hold_control"
                         else "capture_gel_control"))
    if (artifact.get("schema") != CONTROL_CAPTURE_SCHEMA_V2 or
            artifact.get("campaign_id") != candidate["campaign_id"] or
            artifact.get("approval_sha256") != approval_sha or
            artifact.get("control_plan_sha256") != CONTROL_PLAN_SHA256 or
            artifact.get("slot_idempotency_key") != slot_key):
        raise PhysicalControlError("raw metrics are not from this externally approved V2 slot")
    times = artifact["stored_times_s"]
    expressions = artifact["expressions"]
    data = artifact["data"]
    coordinates = artifact["coordinates_m"]
    if case_id == "maxwell_ramp_hold_control":
        required_times = [1.0, 301.0, 601.0, 901.0]
        expected = maxwell_control_reference(required_times)
        report: dict[str, Any] = {
            "status": "MAXWELL_CONTROL_ANALYTIC_COMPARISON_PASS_REVIEW_REQUIRED",
            "threshold_pa": {"absolute": 100.0, "relative": 0.01},
            "checked_points": len(coordinates), "checked_times_s": required_times,
            "per_point": [], "branch_reference_state": "UNVERIFIED",
        }
        for point_index, coordinate in enumerate(coordinates):
            point = {"coordinate_m": list(coordinate), "components": {}}
            for expression, reference_key in (("solid.sx", "sigma_xx_pa"),
                                              ("solid.sy", "sigma_yy_pa")):
                expression_index = expressions.index(expression)
                samples = []
                for check_index, target_time in enumerate(required_times):
                    time_index = times.index(target_time)
                    actual = float(data[expression_index][time_index][point_index])
                    expected_value = float(expected[reference_key][check_index])
                    error = abs(actual - expected_value)
                    limit = max(100.0, 0.01 * abs(expected_value))
                    if error > limit:
                        raise PhysicalControlError(
                            f"{expression} error {error:g} Pa exceeds frozen {limit:g} Pa at "
                            f"point {point_index}, t={target_time:g} s; later control solve stopped")
                    samples.append({"time_s": target_time, "actual_pa": actual,
                                    "analytic_pa": expected_value, "absolute_error_pa": error,
                                    "allowed_error_pa": limit})
                point["components"][expression] = samples
            report["per_point"].append(point)
        report["maxwell_branch_reference_state"] = "UNVERIFIED"
        report["capture_schema_validation"] = shape_report
        return report
    components = ("solid.sx", "solid.sy", "solid.sz", "solid.sxy", "solid.sxz", "solid.syz")
    check_times = (2.0, 2.5, 3.0)
    report = {"status": "GEL_CONTROL_STRESS_FREE_COMPARISON_PASS_REVIEW_REQUIRED",
              "threshold_pa": 100.0, "checked_points": len(coordinates),
              "checked_times_s": list(check_times), "per_point": [],
              "activation_reference_state": "UNVERIFIED"}
    maximum = 0.0
    for point_index, coordinate in enumerate(coordinates):
        point = {"coordinate_m": list(coordinate), "components": {}}
        for expression in components:
            expression_index = expressions.index(expression)
            samples = []
            for target_time in check_times:
                time_index = times.index(target_time)
                actual = float(data[expression_index][time_index][point_index])
                error = abs(actual)
                if error > 100.0:
                    raise PhysicalControlError(
                        f"{expression} stress {actual:g} Pa exceeds frozen 100 Pa at "
                        f"point {point_index}, t={target_time:g} s")
                maximum = max(maximum, error)
                samples.append({"time_s": target_time, "actual_pa": actual,
                                "absolute_error_pa": error, "allowed_error_pa": 100.0})
            point["components"][expression] = samples
        report["per_point"].append(point)
    activation_values: dict[str, dict[str, list[float]]] = {}
    for expression, checkpoints in (("solid.isactive", (2.0, 2.5, 3.0)),
                                    ("solid.wasactive", (2.5, 3.0))):
        expression_index = expressions.index(expression)
        activation_values[expression] = {}
        for target_time in checkpoints:
            time_index = times.index(target_time)
            values = [float(value) for value in data[expression_index][time_index]]
            if any(value != 1.0 for value in values):
                raise PhysicalControlError(
                    f"{expression} is not exactly active at t={target_time:g} s across the full control grid")
            activation_values[expression][str(target_time)] = values
    report["activation_exact_readback"] = activation_values
    report["activation_reference_state"] = "UNVERIFIED"
    report["max_abs_post_gel_stress_pa"] = maximum
    report["capture_schema_validation"] = shape_report
    return report


def execute_approved_physical_controls(*, prepared: PreparedPhysicalControls,
                                       approval_waiter: Callable[..., Any],
                                       clock: Callable[[], float] = time.time,
                                       sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Consume a same-process approval and run at most the two approved slots."""
    if not isinstance(prepared, PreparedPhysicalControls):
        raise PhysicalControlError("physical-control execution requires its original in-memory prepared handles")
    _check_prepared_runtime(prepared, clock=clock)
    prepared.used = True
    from tools.run_native_w24_static_shape_sensitivity_lifecycle import (
        ApprovalEnvelope, _validate_approval_inbox,
    )
    try:
        envelope = approval_waiter(
            candidate=prepared.candidate,
            approval_input_path=prepared.approval_input_path,
            approval_input_sha256=prepared.approval_input_sha256,
            birth_budget=prepared.birth_budget, clock=clock, sleep=sleep)
        if not isinstance(envelope, ApprovalEnvelope):
            raise PhysicalControlError("external waiter returned no validated ApprovalEnvelope")
        approval_sha, _approval = _approval_envelope(prepared, envelope)
        if _validate_approval_inbox(
                candidate=prepared.candidate,
                approval_input_path=prepared.approval_input_path,
                approval_input_sha256=prepared.approval_input_sha256) != envelope:
            raise PhysicalControlError("approval inbox changed after first observation; no replacement hash")
        context = _check_prepared_runtime_after_use(prepared, clock=clock)
    except BaseException as exc:
        _append_jsonl(prepared.event_log, {
            "event": "APPROVAL_OR_RUNTIME_GATE_FAILED_NO_SOLVE",
            "error_type": type(exc).__name__, "error": str(exc),
            "birth_budget": prepared.birth_budget.receipt(now_epoch_s=clock()),
            "native_study_run_submissions": 0,
        })
        raise

    source_sha = prepared.candidate["source_sha256"]["tools/java/W24CureLawV2ControlFixture.java"]
    result: dict[str, Any] = {
        "schema": "W24_PHYSICAL_CONTROL_EXECUTION_V1",
        "status": "RUNNING",
        "campaign_id": prepared.candidate["campaign_id"],
        "candidate_sha256": prepared.candidate_sha256,
        "control_plan_sha256": CONTROL_PLAN_SHA256,
        "approval_sha256": approval_sha,
        "approval_input_sha256": prepared.approval_input_sha256,
        "birth_budget": prepared.birth_budget.receipt(now_epoch_s=clock()),
        "runtime_identity": context,
        "planned_study_run_submissions": 2,
        "actual_study_run_submissions": 0,
        "terminal_confirmed_study_run_calls": 0,
        "durable_study_run_slot_intents": 0,
        "slots": [], "native_acceptance": "NOT_ESTABLISHED_REQUIRES_INDEPENDENT_REVIEW",
        "maxwell_branch_reference_state": "UNVERIFIED",
        "gel_activation_reference_state": "UNVERIFIED",
    }
    _append_jsonl(prepared.event_log, {"event": "EXTERNAL_APPROVAL_VALIDATED",
                                       "approval_sha256": approval_sha,
                                       "approval_input_sha256": prepared.approval_input_sha256,
                                       "budget": prepared.birth_budget.receipt(now_epoch_s=clock()),
                                       "same_runtime_handles": True})
    operation_response_dir = prepared.evidence_dir / "operation_responses"
    operation_response_dir.mkdir(mode=0o700)
    try:
        _verify_no_unknown_or_active(prepared.adapter)
        for case_id, study_tag, _build, _readback, capture_action in EXPECTED_CASES:
            context = _check_prepared_runtime_after_use(prepared, clock=clock)
            binding = prepared.model_bindings[case_id]
            readback = prepared.setup_refs[case_id][-1]["readback_data"]
            solver_tag = readback["solver_sequence"]
            slot_key = _slot_key(prepared.candidate, approval_sha, case_id, binding, source_sha)
            base_args = {
                "campaign_id": prepared.candidate["campaign_id"],
                "approval_sha256": approval_sha,
                "control_plan_sha256": CONTROL_PLAN_SHA256,
                "slot_idempotency_key": slot_key,
                "case_id": case_id, "study_tag": study_tag, "solver_tag": solver_tag,
                "ledger_path": str(prepared.ledger_path),
            }
            save_path = prepared.project_dir / f"{case_id}.mph"
            if save_path.exists() or save_path.is_symlink():
                raise PhysicalControlError("immediate save target already exists; no replacement or retry")
            _append_jsonl(prepared.event_log, {
                "event": "STUDY_RUN_INTENT_PERSISTED",
                "slot_index": len(result["slots"]) + 1,
                "case_id": case_id, "study_tag": study_tag,
                "slot_idempotency_key": slot_key,
                "approval_sha256": approval_sha,
                "control_plan_sha256": CONTROL_PLAN_SHA256,
                "model_binding_before": binding.as_record(),
                "save_after_success_path": str(save_path),
                "no_retry_after_unknown_or_save_failure": True,
                "birth_budget": prepared.birth_budget.receipt(now_epoch_s=clock()),
            })
            try:
                _verify_no_unknown_or_active(prepared.adapter)
                context = _check_prepared_runtime_after_use(prepared, clock=clock)
                timeout = prepared.birth_budget.rpc_timeout_s(1800.0, now_epoch_s=clock())
                run_key = _digest({"slot_key": slot_key, "phase": "study_run_control"})
                updated, _response, run_result, run_ref = _record_action(
                    prepared.adapter, binding=binding, action="study_run_control",
                    arguments={**base_args, "save_after_success_path": str(save_path)},
                    response_dir=operation_response_dir,
                    response_label=f"{case_id}_study_run", request_key=run_key,
                    timeout_s=timeout)
                ledger_rows = _control_ledger_rows(
                    prepared.ledger_path, candidate=prepared.candidate,
                    approval_sha=approval_sha)
                if (len(ledger_rows) != len(result["slots"]) + 1 or
                        ledger_rows[-1].get("slot_idempotency_key") != slot_key):
                    raise PhysicalControlError("Java Study.run intent ledger differs from this exact operation slot")
                _same_project_binding(prepared.adapter, updated)
                artifact = run_ref.get("artifact")
                save_receipt = run_ref.get("artifact_receipt")
                if (not isinstance(artifact, Mapping) or not isinstance(save_receipt, Mapping) or
                        artifact.get("path") != str(save_path) or
                        artifact.get("size_bytes", 0) <= 0 or
                        artifact.get("size_bytes") > 1024**3 or
                        artifact.get("sha256") != sha256(save_path) or
                        save_receipt.get("path") != str(save_path) or
                        save_receipt.get("sha256") != artifact.get("sha256")):
                    raise PhysicalControlError("actual public Study.run immediate-save receipt failed the frozen path/size/hash gate")
                run_identity = _operation_identities(run_ref)
                adapter_ref = dict(run_ref)
                adapter_ref["control_slot_key"] = slot_key
                slot_result: dict[str, Any] = {
                    "case_id": case_id, "study_tag": study_tag,
                    "slot_idempotency_key": slot_key,
                    "study_run_public_identity": run_identity,
                    "study_run_save_artifact": dict(artifact),
                    "study_run_save_receipt": dict(save_receipt),
                    "study_run_source_sha256": source_sha,
                    "model_binding_before": binding.as_record(),
                    "model_binding_after": updated.as_record(),
                    "status": "STUDY_RUN_AND_IMMEDIATE_SAVE_AUTHENTICATED",
                }
                prepared.model_bindings[case_id] = updated
                prepared.adapter.models[case_id] = updated
                result["actual_study_run_submissions"] += 1
                result["terminal_confirmed_study_run_calls"] += 1
                result["durable_study_run_slot_intents"] = len(ledger_rows)
                _append_jsonl(prepared.event_log, {
                    "event": "STUDY_RUN_TERMINAL_SUCCESS_AND_SAVE_HASHED",
                    "case_id": case_id, "operation_identity": run_identity,
                    "save_artifact": dict(artifact),
                    "birth_budget": prepared.birth_budget.receipt(now_epoch_s=clock()),
                })
            except BaseException as exc:
                prepared.unknown = True
                try:
                    rows = _control_ledger_rows(
                        prepared.ledger_path, candidate=prepared.candidate,
                        approval_sha=approval_sha)
                except BaseException:
                    rows = []
                result["durable_study_run_slot_intents"] = len(rows)
                result["actual_study_run_submissions"] = None
                _append_jsonl(prepared.event_log, {
                    "event": "STUDY_RUN_FAILED_OR_UNKNOWN_SLOT_CONSUMED_NO_RETRY",
                    "case_id": case_id, "slot_idempotency_key": slot_key,
                    "error_type": type(exc).__name__, "error": str(exc),
                    "java_ledger_path": str(prepared.ledger_path),
                    "java_ledger_size_bytes": (prepared.ledger_path.stat().st_size
                                                if prepared.ledger_path.exists() else None),
                    "terminal_confirmed_study_run_calls": result["terminal_confirmed_study_run_calls"],
                    "durable_study_run_slot_intents": len(rows),
                    "durable_unknown_preserved": True,
                })
                raise
            capture_path = prepared.project_dir / f"{case_id}_capture.json"
            if capture_path.exists() or capture_path.is_symlink():
                prepared.unknown = True
                raise PhysicalControlError("capture target already exists; no capture overwrite or solve replay")
            capture_args = {**base_args, "path": str(capture_path)}
            capture_key = _digest({"slot_key": slot_key, "phase": capture_action,
                                   "model_ref": dict(updated.model_ref), "revision": updated.revision})
            captured, _capture_response, _capture_result, capture_ref = _record_action(
                prepared.adapter, binding=updated, action=capture_action,
                arguments=capture_args, response_dir=operation_response_dir,
                response_label=f"{case_id}_capture", request_key=capture_key,
                timeout_s=prepared.birth_budget.rpc_timeout_s(600.0, now_epoch_s=clock()))
            artifact_data = _verify_capture_bytes(capture_ref, prepared.project_dir)
            if (artifact_data.get("path") not in (None, str(capture_path)) or
                    artifact_data.get("campaign_id") != prepared.candidate["campaign_id"] or
                    artifact_data.get("approval_sha256") != approval_sha):
                raise PhysicalControlError("raw metrics artifact lost its original approved campaign/slot identity")
            compare_report = _compare_control_capture(
                artifact_data, case_id=case_id, candidate=prepared.candidate,
                approval_sha=approval_sha, slot_key=slot_key)
            capture_identity = _operation_identities(capture_ref)
            slot_result.update({
                "status": "CONTROL_CAPTURE_AUTHENTICATED_AND_ANALYTIC_GATE_MATCHED",
                "capture_public_identity": capture_identity,
                "capture_artifact": dict(capture_ref["artifact"]),
                "capture_response_evidence": dict(capture_ref["response_evidence"]),
                "capture_validation": dict(capture_ref["capture_validation"]),
                "capture_schema": artifact_data.get("schema"),
                "capture_control_plan_sha256": artifact_data.get("control_plan_sha256"),
                "analytic_comparison": compare_report,
                "model_binding_after_capture": captured.as_record(),
                "native_acceptance": "NOT_ESTABLISHED_REQUIRES_INDEPENDENT_REVIEW",
            })
            prepared.model_bindings[case_id] = captured
            prepared.adapter.models[case_id] = captured
            result["slots"].append(slot_result)
            _append_jsonl(prepared.event_log, {
                "event": "CONTROL_CAPTURE_AUTHENTICATED_AND_COMPARED",
                "case_id": case_id, "slot_idempotency_key": slot_key,
                "capture_operation_identity": capture_identity,
                "capture_artifact_sha256": capture_ref["artifact"].get("sha256"),
                "comparison_status": compare_report["status"],
            })
            if sum(Path(row["capture_artifact"]["path"]).stat().st_size
                   for row in result["slots"]) > CONTROL_PLAN["maximum_total_capture_json_bytes"]:
                raise PhysicalControlError("total control capture JSON exceeded its frozen 50MiB limit")
        if (result["actual_study_run_submissions"] != 2 or
                result["terminal_confirmed_study_run_calls"] != 2 or
                result["durable_study_run_slot_intents"] != 2 or len(result["slots"]) != 2):
            raise PhysicalControlError("two-slot control plan did not complete exactly its approved run count")
        if prepared.ledger_path.stat().st_size <= 0:
            raise PhysicalControlError("Java fsynced solve ledger is empty after reported completed Study.run calls")
        result.update({"status": "BOTH_PHYSICAL_CONTROL_ANALYTIC_GATES_MATCH_REVIEW_REQUIRED",
                       "java_solve_ledger_path": str(prepared.ledger_path),
                       "java_solve_ledger_sha256": sha256(prepared.ledger_path),
                       "birth_budget_at_finish": prepared.birth_budget.receipt(now_epoch_s=clock()),
                       "native_acceptance": "NOT_ESTABLISHED_REQUIRES_INDEPENDENT_REVIEW",
                       "maxwell_branch_reference_state": "UNVERIFIED",
                       "gel_activation_reference_state": "UNVERIFIED"})
        receipt_hash = _write_new_json(prepared.evidence_dir / "control_campaign_receipt.json", result)
        result["receipt_sha256"] = receipt_hash
        return result
    except BaseException as exc:
        try:
            durable_intents = (len(_control_ledger_rows(
                prepared.ledger_path, candidate=prepared.candidate,
                approval_sha=approval_sha)) if prepared.ledger_path.exists() else None)
        except BaseException:
            durable_intents = None
        result.update({"status": "CONTROL_CAMPAIGN_FAILED_OR_UNKNOWN_NO_RETRY",
                       "error_type": type(exc).__name__, "error": str(exc),
                       "java_solve_ledger_path": str(prepared.ledger_path),
                       "java_solve_ledger_sha256": (sha256(prepared.ledger_path)
                                                    if prepared.ledger_path.exists() else None),
                       "durable_study_run_slot_intents": durable_intents,
                       "birth_budget_at_failure": prepared.birth_budget.receipt(now_epoch_s=clock()),
                       "durable_unknown_preserved": prepared.unknown,
                       "native_acceptance": "NOT_ESTABLISHED",
                       "maxwell_branch_reference_state": "UNVERIFIED",
                       "gel_activation_reference_state": "UNVERIFIED"})
        _append_jsonl(prepared.event_log, {
            "event": "CAMPAIGN_FAILED_OR_UNKNOWN_NO_SLOT_REPLAY",
            "error_type": type(exc).__name__, "error": str(exc),
            "observed_run_count": result["actual_study_run_submissions"],
            "durable_unknown_preserved": prepared.unknown,
        })
        try:
            _write_new_json(prepared.evidence_dir / "control_campaign_receipt.json", result)
        except BaseException:
            pass
        raise


def _check_prepared_runtime_after_use(prepared: PreparedPhysicalControls,
                                      *, clock: Callable[[], float]) -> dict[str, Any]:
    context = _birth_budget_context(prepared.adapter, prepared.monitor,
                                    prepared.birth_budget, clock=clock)
    _verify_no_unknown_or_active(prepared.adapter)
    for case, binding in prepared.model_bindings.items():
        _same_project_binding(prepared.adapter, binding)
        if prepared.adapter.models.get(case) != binding:
            raise PhysicalControlError("active ModelRef/revision differs from the exact prepared binding")
    return context
