from __future__ import annotations

import json
import hashlib
from contextlib import nullcontext
from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._execution_contract import canonical_request_hash
from comsol_mcp._operation_store import OperationStore
from tools import run_native_w24_physical_controls as controls
from tools.w24_cure_law_v2 import maxwell_control_reference
from tools.w24_cure_v2_capture import (
    CONTROL_COORDINATES_V2_M, CONTROL_CAPTURE_SCHEMA_V2,
    GEL_EXPRESSIONS, GEL_TIMES_S, MAXWELL_EXPRESSIONS, MAXWELL_TIMES_S,
    CaptureError, validate_capture_artifact, _verify_public_capture_record,
)


def _v2_control_artifact(case_id: str) -> dict:
    coordinates = [list(row) for row in CONTROL_COORDINATES_V2_M]
    if case_id == "maxwell_ramp_hold_control":
        times = list(MAXWELL_TIMES_S)
        expressions, units = list(MAXWELL_EXPRESSIONS), ["Pa", "Pa"]
        reference = maxwell_control_reference(times)
        data = [
            [[value] * len(coordinates) for value in reference["sigma_xx_pa"]],
            [[value] * len(coordinates) for value in reference["sigma_yy_pa"]],
        ]
        study, solver = "stdMaxwell", "solMaxwell"
        tlist = "range(0[s],1[s],901[s])"
        action = "capture_maxwell_control"
    else:
        times = list(GEL_TIMES_S)
        expressions = list(GEL_EXPRESSIONS)
        units = ["Pa"] * 6 + ["1", "1"]
        data = [[[0.0] * len(coordinates) for _ in times] for _ in range(6)]
        data.append([[1.0 if time >= 2 else 0.0] * len(coordinates) for time in times])
        data.append([[1.0 if time >= 2 else 0.0] * len(coordinates) for time in times])
        study, solver = "stdGel", "solGel"
        tlist = "range(0[s],0.5[s],3[s])"
        action = "capture_gel_control"
    args = {
        "campaign_id": "w24-control-test",
        "approval_sha256": "a" * 64,
        "control_plan_sha256": controls.CONTROL_PLAN_SHA256,
        "slot_idempotency_key": "b" * 64,
    }
    shape = [len(expressions), len(times), len(coordinates)]
    return {
        "schema": CONTROL_CAPTURE_SCHEMA_V2,
        "status": "NATIVE_CONTROL_CAPTURED_NO_SOLVE_SUBMITTED",
        "case_id": case_id, **args,
        "study_tag": study, "solver_tag": solver, "dataset_tag": "dsetControl",
        "dataset_type_requested": "Solution", "dataset_solution_readback": solver,
        "stored_times_s": times, "time_source": "SolverSequence.getPVals",
        "study_tlist_readback": tlist, "quasistatic_readback": "Quasistatic",
        "expressions": expressions, "units": units,
        "coordinates_m": coordinates,
        "coordinate_grid": "uniform 3x3x3 control cube at 25/50/75 percent fractions on every axis",
        "shape": shape, "data": data, "native_study_run_calls": 0,
        "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
        "feature_readback": {
            "type": "Interp", "dataset": "dsetControl", "expressions": expressions,
            "units": units, "solnum": "all", "coorderr": "on", "matherr": "on",
            "coordinates_m": coordinates,
            "coordinate_source": "frozen 3x3x3 cube fractions passed to setInterpolationCoordinates",
            "shape": shape,
        },
    }


@pytest.mark.parametrize("case_id", ["maxwell_ramp_hold_control", "gel_stress_free_control"])
def test_v2_controls_capture_all_27_native_points_and_preserve_full_stored_time_axis(case_id):
    artifact = _v2_control_artifact(case_id)
    action = "capture_maxwell_control" if case_id.startswith("maxwell") else "capture_gel_control"
    report = validate_capture_artifact(artifact, expected_action=action)
    assert report["control_capture_schema"] == CONTROL_CAPTURE_SCHEMA_V2
    assert report["coordinate_count"] == 27
    assert report["stored_time_count"] == (902 if case_id.startswith("maxwell") else 7)
    assert report["native_execution_performed_by_validator"] is False


@pytest.mark.parametrize("mutation", ["point_missing", "coordinate_wrong", "plan_wrong", "shape_short"])
def test_v2_control_capture_fails_closed_on_grid_or_approval_identity_drift(mutation):
    artifact = _v2_control_artifact("maxwell_ramp_hold_control")
    if mutation == "point_missing":
        artifact["coordinates_m"] = artifact["coordinates_m"][:-1]
        artifact["feature_readback"]["coordinates_m"] = artifact["coordinates_m"]
    elif mutation == "coordinate_wrong":
        artifact["coordinates_m"][0][0] += 1e-9
        artifact["feature_readback"]["coordinates_m"] = artifact["coordinates_m"]
    elif mutation == "plan_wrong":
        artifact["control_plan_sha256"] = "c" * 64
    else:
        artifact["shape"][2] -= 1
        artifact["feature_readback"]["shape"] = list(artifact["shape"])
    if mutation == "plan_wrong":
        with pytest.raises(controls.PhysicalControlError, match="approved V2 slot"):
            controls._compare_control_capture(
                artifact, case_id="maxwell_ramp_hold_control",
                candidate={"campaign_id": "w24-control-test"},
                approval_sha="a" * 64, slot_key="b" * 64)
    else:
        with pytest.raises(CaptureError):
            validate_capture_artifact(artifact, expected_action="capture_maxwell_control")


def test_maxwell_comparator_uses_frozen_absolute_relative_tolerance_at_each_point():
    artifact = _v2_control_artifact("maxwell_ramp_hold_control")
    report = controls._compare_control_capture(
        artifact, case_id="maxwell_ramp_hold_control", candidate={"campaign_id": "w24-control-test"},
        approval_sha="a" * 64, slot_key="b" * 64)
    assert report["status"] == "MAXWELL_CONTROL_ANALYTIC_COMPARISON_PASS_REVIEW_REQUIRED"
    assert report["threshold_pa"] == {"absolute": 100.0, "relative": 0.01}
    assert len(report["per_point"]) == 27
    assert report["checked_times_s"] == [1.0, 301.0, 601.0, 901.0]

    # The sampled point error is over max(100 Pa, 1% of its analytic value).
    target = maxwell_control_reference([901.0])["sigma_xx_pa"][0]
    artifact["data"][0][artifact["stored_times_s"].index(901.0)][13] += max(100.0, 0.01 * abs(target)) + 1.0
    with pytest.raises(controls.PhysicalControlError, match="later control solve stopped"):
        controls._compare_control_capture(
            artifact, case_id="maxwell_ramp_hold_control", candidate={"campaign_id": "w24-control-test"},
            approval_sha="a" * 64, slot_key="b" * 64)


def test_gel_comparator_checks_six_full_components_and_activation_at_all_points():
    artifact = _v2_control_artifact("gel_stress_free_control")
    report = controls._compare_control_capture(
        artifact, case_id="gel_stress_free_control", candidate={"campaign_id": "w24-control-test"},
        approval_sha="a" * 64, slot_key="b" * 64)
    assert report["status"] == "GEL_CONTROL_STRESS_FREE_COMPARISON_PASS_REVIEW_REQUIRED"
    assert report["threshold_pa"] == 100.0
    assert report["checked_points"] == 27
    assert set(report["per_point"][0]["components"]) == set(GEL_EXPRESSIONS[:6])
    assert report["activation_reference_state"] == "UNVERIFIED"
    assert report["activation_exact_readback"]["solid.wasactive"]["2.5"] == [1.0] * 27

    artifact["data"][4][artifact["stored_times_s"].index(2.5)][26] = 100.01
    with pytest.raises(controls.PhysicalControlError, match="frozen 100 Pa"):
        controls._compare_control_capture(
            artifact, case_id="gel_stress_free_control", candidate={"campaign_id": "w24-control-test"},
            approval_sha="a" * 64, slot_key="b" * 64)


def _external_control_approval(tmp_path, *, mutation=None):
    from types import SimpleNamespace
    from tools.run_native_w24_static_shape_sensitivity_lifecycle import (
        APPROVAL_INBOX_SCHEMA, _validate_approval_inbox,
    )

    root = tmp_path / "private-approval"
    root.mkdir(mode=0o700)
    inbox_path = root / "approval-inbox.json"
    candidate = controls.build_control_candidate(
        campaign_id="w24-control-test", approval_root=root,
        approval_inbox_path=inbox_path)
    input_path = tmp_path / "pending-control-input.json"
    input_path.write_text('{"status":"PENDING_NOT_APPROVED"}\n', encoding="utf-8")
    input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    ordered_slots = [
        {"index": slot["index"], "case_id": slot["case_id"],
         "study_tag": slot["study_tag"], "study_run_submissions": 1}
        for slot in controls.CONTROL_PLAN["slots"]
    ]
    approval = {
        "schema": controls.APPROVAL_SCHEMA, "status": "APPROVED",
        "campaign_id": candidate["campaign_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "approval_input_sha256": input_sha,
        "control_plan_sha256": controls.CONTROL_PLAN_SHA256,
        "study_run_submissions": 2, "ordered_slots": ordered_slots,
        "source_sha256": candidate["source_sha256"],
    }
    if mutation == "candidate":
        approval["candidate_sha256"] = "c" * 64
    elif mutation == "input":
        approval["approval_input_sha256"] = "d" * 64
    elif mutation == "plan":
        approval["control_plan_sha256"] = "e" * 64
    elif mutation == "count":
        approval["study_run_submissions"] = 10
    elif mutation == "order":
        approval["ordered_slots"].reverse()
    elif mutation == "source":
        approval["source_sha256"] = {}
    elif mutation == "old_schema":
        approval["schema"] = "W24_STATIC_SHAPE_SCIENCE_APPROVAL_V1"
    approval_path = root / "physical-control-approval.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True, separators=(",", ":")) + "\n",
                             encoding="utf-8")
    approval_sha = hashlib.sha256(approval_path.read_bytes()).hexdigest()
    inbox = {
        "schema": APPROVAL_INBOX_SCHEMA,
        "campaign_id": candidate["campaign_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "approval_input_path": str(input_path),
        "approval_input_sha256": input_sha,
        "approval_path": str(approval_path),
        "expected_approval_sha256": approval_sha,
    }
    inbox_path.write_text(json.dumps(inbox, sort_keys=True, separators=(",", ":")) + "\n",
                          encoding="utf-8")
    envelope = _validate_approval_inbox(candidate=candidate,
                                        approval_input_path=input_path,
                                        approval_input_sha256=input_sha)
    prepared = SimpleNamespace(candidate=candidate,
                               candidate_sha256=candidate["candidate_sha256"],
                               approval_input_path=input_path,
                               approval_input_sha256=input_sha)
    return prepared, envelope, approval_sha


def test_control_approval_envelope_binds_exact_candidate_input_plan_slots_and_sources(tmp_path):
    prepared, envelope, expected_sha = _external_control_approval(tmp_path)
    observed_sha, approval = controls._approval_envelope(prepared, envelope)
    assert observed_sha == expected_sha
    assert approval["control_plan_sha256"] == controls.CONTROL_PLAN_SHA256
    assert approval["study_run_submissions"] == 2


@pytest.mark.parametrize("mutation", ["candidate", "input", "plan", "count", "order", "source", "old_schema"])
def test_control_approval_rejects_candidate_plan_budget_slot_or_source_substitution(tmp_path, mutation):
    prepared, envelope, _ = _external_control_approval(tmp_path, mutation=mutation)
    with pytest.raises(controls.PhysicalControlError, match="exact candidate"):
        controls._approval_envelope(prepared, envelope)


def test_candidate_hash_pins_exact_control_plan_and_never_marks_pending_input_approved(tmp_path):
    root = tmp_path / "private-approval"
    root.mkdir(mode=0o700)
    inbox = root / "approval-inbox.json"
    candidate = controls.build_control_candidate(
        campaign_id="w24-control-test", approval_root=root, approval_inbox_path=inbox)
    assert candidate["status"] == "PENDING_NOT_APPROVED_NOT_RUN"
    assert candidate["native_acceptance"] == "NOT_RUN"
    assert candidate["control_plan"]["planned_study_run_submissions"] == 2
    assert candidate["control_plan"]["slots"][0]["sigma_error_limit"] == "max(100[Pa],0.01*abs(analytic_sigma[Pa]))"
    path = tmp_path / "frozen-control-candidate.json"
    fingerprint = controls.write_control_candidate(path, candidate)
    assert fingerprint == candidate["candidate_sha256"]
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert controls._validate_candidate(loaded, fingerprint)["candidate_sha256"] == fingerprint
    loaded["control_plan"]["slots"][1]["post_gel_component_abs_limit_pa"] = 1e9
    with pytest.raises(controls.PhysicalControlError, match="candidate/hash/plan/status"):
        controls._validate_candidate(loaded, fingerprint)


def _public_control_study_record(tmp_path, *, mutate=None):
    project = tmp_path / "project"
    source = project / "tools/java/W24CureLawV2ControlFixture.java"
    source.parent.mkdir(parents=True)
    source.write_text("// synthetic public-route source", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    save_path = project / "outputs/maxwell_ramp_hold_control.mph"
    save_path.parent.mkdir()
    save_path.write_bytes(b"synthetic immediate save bytes")
    model_ref = {"session_id": "session-a", "server_instance_id": "server-a",
                 "model_tag": "control-model", "generation": 2, "schema_version": 1}
    args = {
        "action": "study_run_control", "case_id": "maxwell_ramp_hold_control",
        "study_tag": "stdMaxwell", "solver_tag": "solControl",
        "campaign_id": "w24-control-test", "approval_sha256": "a" * 64,
        "control_plan_sha256": controls.CONTROL_PLAN_SHA256,
        "slot_idempotency_key": "b" * 64,
        "ledger_path": str(project / "outputs/study_run_events.jsonl"),
        "save_after_success_path": str(save_path),
    }
    receipt = {
        "status": "NATIVE_STUDY_RUN_RETURNED", "case_id": args["case_id"],
        "study_tag": args["study_tag"], "solver_sequence": args["solver_tag"],
        "campaign_id": args["campaign_id"], "approval_sha256": args["approval_sha256"],
        "control_plan_sha256": args["control_plan_sha256"],
        "slot_idempotency_key": args["slot_idempotency_key"],
        "submission_index": 1, "study_run_calls_from_this_action": 1,
        "native_study_run_calls": 1, "quasistatic_readback": "Quasistatic",
        "immediate_save_path": str(save_path),
        "immediate_save_receipt": {
            "status": "STUDY_RUN_MPH_SAVED_AND_HASHED", "path": str(save_path),
            "size_bytes": save_path.stat().st_size,
            "sha256": hashlib.sha256(save_path.read_bytes()).hexdigest(),
        },
    }
    if mutate == "receipt_plan":
        receipt["control_plan_sha256"] = "c" * 64
    if mutate == "receipt_slot":
        receipt["slot_idempotency_key"] = "d" * 64
    if mutate == "missing_save":
        args.pop("save_after_success_path")
        receipt.pop("immediate_save_path")
        receipt.pop("immediate_save_receipt")
    if mutate == "wrong_save_path":
        receipt["immediate_save_path"] = str(project / "outputs/foreign.mph")
    if mutate == "tampered_save":
        save_path.write_bytes(b"tampered after native save")
    body = {"source_artifact": "tools/java/W24CureLawV2ControlFixture.java",
            "entrypoint": "W24CureLawV2ControlFixture#run",
            "arguments": args, "mode": "trusted"}
    metadata = {
        "arguments": {"operation_id": "code.execute_java", "arguments": body},
        "execution": {"project_id": "project-a", "session_id": "session-a",
                      "model_ref": model_ref, "expected_revision": 7},
    }
    timeouts = {"rpc_timeout_s": 120.0, "queue_timeout_s": 60.0,
                "execution_timeout_s": None, "no_progress_warning_s": None}
    request_id, key = "control-request-a", "control-key-a"
    request_hash = canonical_request_hash(
        "code.execute_java", body, model_ref, 7, project_id="project-a",
        session_id="session-a", queue_timeout_s=60.0,
        execution_timeout_s=None, no_progress_warning_s=None)
    store = OperationStore(tmp_path / "operations.sqlite")
    record, reused = store.begin(request_id=request_id, idempotency_key=key,
                                 request_hash=request_hash, operation="operation_call",
                                 metadata=metadata, timeouts=timeouts)
    assert not reused
    java = {"executed": True, "model_tag": model_ref["model_tag"],
            "source_sha256": source_sha, "entrypoint": body["entrypoint"],
            "readback": receipt}
    reply = {
        "success": True,
        "data": {"source_sha256": source_sha, "entrypoint": body["entrypoint"],
                 "worker": {"ok": True, "status": "SUCCEEDED", "result": {"readback": java}},
                 "readback": {"readback": java}},
        "execution": {"project_id": "project-a", "session_id": "session-a",
                      "model_ref": model_ref, "revision": 8,
                      "request_id": request_id, "idempotency_key": key,
                      "request_hash": request_hash, "operation_id": record["operation_id"],
                      "job_id": record["job_id"]},
    }
    store.finish(record["operation_id"], status="SUCCEEDED", result=reply)
    stored = store.get_operation(record["operation_id"])
    job = store.operation_job(record["operation_id"])
    stored.update({"job_id": job["job_id"], "job_status": job["status"],
                   "job_result": job["result"]})
    return project, source, source_sha, reply, stored, model_ref, store


@pytest.mark.parametrize("mutation", [None, "receipt_plan", "receipt_slot", "missing_save",
                                      "wrong_save_path", "tampered_save"])
def test_control_study_run_requires_authenticated_approved_slot_and_immediate_saved_bytes(tmp_path, mutation):
    project, source, source_sha, reply, record, model_ref, store = _public_control_study_record(
        tmp_path, mutate=mutation)
    try:
        if mutation is None:
            result = _verify_public_capture_record(
                reply, record, project_root=project, source_artifact_path=source,
                expected_source_sha256=source_sha, expected_action="study_run_control",
                expected_project_id="project-a", expected_session_id="session-a",
                expected_model_ref=model_ref, expected_revision=7)
            assert result["status"] == "PUBLIC_CONTROL_STUDY_RUN_AND_SAVE_AUTHENTICATED_NATIVE_REVIEW_REQUIRED"
            assert result["artifact"]["size_bytes"] > 0
            assert result["capture_validation"]["control_plan_sha256"] == controls.CONTROL_PLAN_SHA256
            assert result["native_acceptance"] == "NOT_RUN"
        else:
            with pytest.raises(CaptureError):
                _verify_public_capture_record(
                    reply, record, project_root=project, source_artifact_path=source,
                    expected_source_sha256=source_sha, expected_action="study_run_control",
                    expected_project_id="project-a", expected_session_id="session-a",
                    expected_model_ref=model_ref, expected_revision=7)
    finally:
        store.close()


class _ControlRouteSnapshot:
    def model_snapshot(self, model_tag):
        return {"model_tag": model_tag, "server_instance_id": "server-a",
                "fingerprint": "stable-control-test-model", "external_event_counter": 0}


class _HarmlessControlWorker:
    """Exercises the real public ControlDaemon/SQLite route without COMSOL."""
    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, model_tag):
        return _ControlRouteSnapshot().model_snapshot(model_tag)

    def execute_java(self, model_tag, source_artifact, entrypoint, arguments, *, request_id=None):
        action = arguments["action"]
        if action == "study_run_control":
            ledger = Path(arguments["ledger_path"])
            save_path = Path(arguments["save_after_success_path"])
            ledger.parent.mkdir(parents=True, exist_ok=True)
            prior = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
            ordinal = len(prior) + 1
            ledger_row = {
                "event": "study_run_submitted", "submission_index": ordinal,
                "campaign_id": arguments["campaign_id"],
                "approval_sha256": arguments["approval_sha256"],
                "control_plan_sha256": arguments["control_plan_sha256"],
                "slot_idempotency_key": arguments["slot_idempotency_key"],
                "case_id": arguments["case_id"], "study_tag": arguments["study_tag"],
                "at_utc": "2026-09-28T00:00:00Z",
            }
            with ledger.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(ledger_row, sort_keys=True, separators=(",", ":")) + "\n")
            save_path.write_bytes(b"synthetic harmless Worker immediate-save receipt bytes")
            receipt = {
                "status": "NATIVE_STUDY_RUN_RETURNED", "case_id": arguments["case_id"],
                "study_tag": arguments["study_tag"], "solver_sequence": arguments["solver_tag"],
                "campaign_id": arguments["campaign_id"],
                "approval_sha256": arguments["approval_sha256"],
                "control_plan_sha256": arguments["control_plan_sha256"],
                "slot_idempotency_key": arguments["slot_idempotency_key"],
                "submission_index": ordinal, "study_run_calls_from_this_action": 1,
                "native_study_run_calls": 1, "quasistatic_readback": "Quasistatic",
                "immediate_save_path": str(save_path),
                "immediate_save_receipt": {
                    "status": "STUDY_RUN_MPH_SAVED_AND_HASHED", "path": str(save_path),
                    "size_bytes": save_path.stat().st_size,
                    "sha256": hashlib.sha256(save_path.read_bytes()).hexdigest(),
                },
            }
            java = {"executed": True, "model_tag": model_tag,
                    "source_sha256": hashlib.sha256(Path(source_artifact).read_bytes()).hexdigest(),
                    "entrypoint": entrypoint, "readback": receipt}
            return {"ok": True, "status": "SUCCEEDED", "result": {"readback": java}}
        case_id = ("maxwell_ramp_hold_control" if action == "capture_maxwell_control"
                   else "gel_stress_free_control")
        artifact = _v2_control_artifact(case_id)
        for key in ("campaign_id", "approval_sha256", "control_plan_sha256", "slot_idempotency_key"):
            artifact[key] = arguments[key]
        path = Path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
        path.write_bytes(raw)
        receipt = {
            "status": "NATIVE_CONTROL_CAPTURE_WRITTEN",
            "schema": artifact["schema"], "case_id": artifact["case_id"],
            "campaign_id": artifact["campaign_id"],
            "approval_sha256": artifact["approval_sha256"],
            "control_plan_sha256": artifact["control_plan_sha256"],
            "slot_idempotency_key": artifact["slot_idempotency_key"],
            "study_tag": artifact["study_tag"], "solver_tag": artifact["solver_tag"],
            "dataset_tag": artifact["dataset_tag"], "path": str(path),
            "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }
        java = {"executed": True, "model_tag": model_tag,
                "source_sha256": hashlib.sha256(Path(source_artifact).read_bytes()).hexdigest(),
                "entrypoint": entrypoint, "readback": receipt}
        return {"ok": True, "status": "SUCCEEDED", "result": {"readback": java}}


@pytest.mark.parametrize("action", ["capture_maxwell_control", "capture_gel_control"])
def test_v2_capture_uses_real_control_daemon_sqlite_and_authorized_project_store(tmp_path, monkeypatch, action):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    service = ExecutionService(
        SessionLedger("session-a", "server-a",
                      permissions={"inspect", "project_write", "compute", "trusted_code"}),
        _ControlRouteSnapshot(), project_root=projects)
    daemon = ControlDaemon(
        tmp_path / "control", service=service, registry={}, worker=_HarmlessControlWorker(),
        project_root=projects)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "v2-control-route", "workspace": "v2-control-route",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
            "execution": {"request_id": "control-project", "idempotency_key": "control-project"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        project_root = projects / "v2-control-route"
        source = project_root / "tools/java/W24CureLawV2ControlFixture.java"
        source.parent.mkdir(parents=True)
        source.write_text("public final class W24CureLawV2ControlFixture {}\n", encoding="utf-8")
        bound = service.bind_model("model-control")
        ref = bound["execution"]["model_ref"]
        daemon.backend._bind_model_project(ref, project_id)
        revision_key = daemon.backend._model_project_key(ref)
        daemon.store.put_metadata("revisions", revision_key, {
            "model_ref": ref, "project_id": project_id, "attribution": "PROJECT_BOUND",
            "revision": 0, "dirty": False, "fingerprint": "stable-control-test-model",
            "active_operation_id": None,
        })
        # Only the native isolation check is replaced; OperationStore, Job,
        # public Java dispatch, project authorization, and source hashing are real.
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
        case_id = ("maxwell_ramp_hold_control" if action == "capture_maxwell_control"
                   else "gel_stress_free_control")
        study, solver = (("stdMaxwell", "solMaxwell") if action == "capture_maxwell_control"
                         else ("stdGel", "solGel"))
        import tools.w24_cure_v2_capture as capture_module
        dispatch_capture = capture_module.dispatch_capture
        captured = dispatch_capture(
            daemon, project_root=project_root,
            source_artifact="tools/java/W24CureLawV2ControlFixture.java",
            expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            action=action,
            arguments={"solver_tag": solver, "study_tag": study,
                       "campaign_id": "w24-control-test", "approval_sha256": "a" * 64,
                       "control_plan_sha256": controls.CONTROL_PLAN_SHA256,
                       "slot_idempotency_key": "b" * 64,
                       "path": str(project_root / "outputs/control-capture.json")},
            project_id=project_id, session_id="session-a", model_ref=ref, revision=0,
        )
        assert captured["status"] == "PUBLIC_CAPTURE_ENVELOPE_AND_ARTIFACT_MATCHED_NATIVE_REVIEW_REQUIRED"
        assert captured["artifact_data"]["case_id"] == case_id
        assert captured["capture_validation"]["coordinate_count"] == 27
        assert captured["native_acceptance"] == "NOT_RUN"
        stored = daemon.store.get_operation(captured["operation_id"])
        job = daemon.store.operation_job(captured["operation_id"])
        assert stored["metadata"]["execution"]["project_id"] == project_id
        assert stored["status"] == job["status"] == "SUCCEEDED"
    finally:
        daemon.close()


def test_control_study_run_save_is_authenticated_through_real_daemon_sqlite_and_job(tmp_path, monkeypatch):
    from tools.run_native_resume_smoke import _dispatch
    from tools.w24_cure_v2_capture import verify_public_capture

    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    service = ExecutionService(
        SessionLedger("session-a", "server-a",
                      permissions={"inspect", "project_write", "compute", "trusted_code"}),
        _ControlRouteSnapshot(), project_root=projects)
    daemon = ControlDaemon(
        tmp_path / "control", service=service, registry={}, worker=_HarmlessControlWorker(),
        project_root=projects)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "control-study-run", "workspace": "control-study-run",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
            "execution": {"request_id": "control-run-project", "idempotency_key": "control-run-project"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        project_root = projects / "control-study-run"
        source = project_root / "tools/java/W24CureLawV2ControlFixture.java"
        source.parent.mkdir(parents=True)
        source.write_text("public final class W24CureLawV2ControlFixture {}\n", encoding="utf-8")
        (project_root / "outputs").mkdir()
        bound = service.bind_model("model-control-run")
        ref = bound["execution"]["model_ref"]
        daemon.backend._bind_model_project(ref, project_id)
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(ref), {
            "model_ref": ref, "project_id": project_id, "attribution": "PROJECT_BOUND",
            "revision": 0, "dirty": False, "fingerprint": "stable-control-run-test-model",
            "active_operation_id": None,
        })
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
        save_path = project_root / "outputs/maxwell_ramp_hold_control.mph"
        ledger_path = project_root / "outputs/study_run_events.jsonl"
        slot_key = "b" * 64
        arguments = {
            "action": "study_run_control", "case_id": "maxwell_ramp_hold_control",
            "study_tag": "stdMaxwell", "solver_tag": "solMaxwell",
            "campaign_id": "w24-control-route-test", "approval_sha256": "a" * 64,
            "control_plan_sha256": controls.CONTROL_PLAN_SHA256,
            "slot_idempotency_key": slot_key, "ledger_path": str(ledger_path),
            "save_after_success_path": str(save_path),
        }
        nested = {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": "tools/java/W24CureLawV2ControlFixture.java",
            "entrypoint": "W24CureLawV2ControlFixture#run",
            "arguments": arguments, "mode": "trusted"}}
        response = _dispatch(
            daemon, "operation_call", nested, project_id=project_id,
            ref=ref, revision=0, idempotency_key="control-study-run-slot-once",
            request_id="control-study-run-request-once", rpc_timeout_s=120.0)
        operation_id = response["execution"]["operation_id"]
        verified = verify_public_capture(
            daemon, response, operation_id=operation_id,
            project_root=project_root, source_artifact_path=source,
            expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            expected_action="study_run_control", expected_project_id=project_id,
            expected_session_id="session-a", expected_model_ref=ref, expected_revision=0)
        assert verified["status"] == "PUBLIC_CONTROL_STUDY_RUN_AND_SAVE_AUTHENTICATED_NATIVE_REVIEW_REQUIRED"
        assert verified["capture_validation"]["slot_idempotency_key"] == slot_key
        assert verified["artifact"]["sha256"] == hashlib.sha256(save_path.read_bytes()).hexdigest()
        assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1
        assert daemon.store.get_operation(operation_id)["status"] == "SUCCEEDED"
        assert daemon.store.operation_job(operation_id)["status"] == "SUCCEEDED"
    finally:
        daemon.close()
