from __future__ import annotations

import hashlib
import copy
from pathlib import Path

import pytest

from tests.w21_initial_stage_fixture import build_initial_stage_fixture
from comsol_mcp._execution_contract import model_ref_from_mapping
from comsol_mcp._w21_stage_backend import validate_initial_output_acceptance


def test_registered_initial_stage_accepts_complete_fake_worker_output(tmp_path):
    value = build_initial_stage_fixture(tmp_path)
    try:
        assert value["defined"]["success"] is True, value["defined"]
        result = value["result"]
        assert result is not None
        assert result["success"] is True, {"result": result, "attempt": value["attempt"],
                                            "transport": value["transport_requests"][-8:]}
        assert result["data"]["acceptance_status"] == "INITIAL_OUTPUT_ACCEPTED"
        assert result["data"]["initial_output_acceptance"]["status"] == "PASS"
        assert result["data"]["initial_output_acceptance"]["scope"] == "INITIAL_OUTPUT_AND_SOURCE_ARTIFACT_ONLY"
        attempt = value["attempt"]
        assert attempt["status"] == "ACCEPTED"
        assert attempt["acceptance_status"] == "INITIAL_OUTPUT_ACCEPTED"
        assert attempt["execution_status"] == "SOLVE_SUCCEEDED"
        proof = result["data"]["initial_output_acceptance"]["native_evidence"]
        row = next(item for item in attempt["evidence"] if item.get("kind") == "initial-output-native-evidence")
        assert row["native_evidence"] == proof
        assert proof["native_output_readback"]["checks"] == []
        assert proof["native_output_readback"]["variables_dof_readback"]["n_dofs"] == 9
        assert proof["native_output_readback"]["solution_mesh_association"]["mesh_tag"] == "mesh1"
        assert proof["mesh_snapshot_readback"]["status"] == "CURRENT_MESH_CAPTURE_ONLY"
        assert proof["saved_artifact"]["sha256"] == proof["saved_artifact_observed_sha256"]
        xmesh_steps = [step for step in proof["native_output_readback"]["revision_chain"]
                       if step.get("readphase") == "initial-stage-active-variables-xmesh"]
        assert len(xmesh_steps) == 1
        xmesh = xmesh_steps[0]
        assert xmesh["operation"] == "solver.inspect"
        assert xmesh["effect"] == "EVALUATE"
        assert xmesh["expected_revision"] == proof["solve_revision"]
        assert xmesh["revision"] == proof["solve_revision"] + 1
        assert xmesh["managed_ticket_operation"] == "w21_initial_variables_xmesh"
        assert len(xmesh["request_hash"]) == 64
        assert sum(row.get("type") == "call" and row.get("method") == "getVariablesXmeshReadback"
                   for row in value["transport_requests"]) == 1
        worker_rows = proof["worker_event_rows"]
        request_phases = {}
        for row in worker_rows:
            metadata = row.get("metadata", {})
            if isinstance(metadata, dict):
                request_phases.setdefault(metadata.get("request_id"), set()).add(metadata.get("phase"))
        assert len(request_phases) == proof["raw_worker_rpc_count"]
        assert all(phases == {"submitted", "observed"} for phases in request_phases.values())
        tlist_failures = [row for row in worker_rows
                          if isinstance(row.get("metadata"), dict)
                          and row["metadata"].get("phase") == "observed"
                          and isinstance(row["metadata"].get("metadata"), dict)
                          and row["metadata"]["metadata"].get("method") == "getDoubleArray"
                          and row["metadata"]["metadata"].get("args") == ["tlist"]]
        assert len(tlist_failures) == 3
        assert all(row["metadata"]["status"] == "FAILED"
                   and row["metadata"]["reply"]["request_id"] == row["metadata"]["request_id"]
                   and row["metadata"]["reply"]["failure"]["code"] == "ENGINE_CALL_FAILED"
                   for row in tlist_failures)
        file_path_reads = [row for row in worker_rows
                           if isinstance(row.get("metadata"), dict)
                           and row["metadata"].get("phase") == "observed"
                           and isinstance(row["metadata"].get("metadata"), dict)
                           and row["metadata"]["metadata"].get("method") == "getFilePath"]
        assert len(file_path_reads) == 2
        assert all(row["metadata"]["reply"]["status"] == "SUCCEEDED"
                   and row["metadata"]["reply"].get("result") is None
                   for row in file_path_reads)
    finally:
        value["daemon"].close()


def test_runner_frozen_initial_plan_accepts_through_same_fake_worker_path(tmp_path):
    value = build_initial_stage_fixture(
        tmp_path, runner_profile=True, expected_revision=2,
        stage_define_request_id="frozen-initial-define-request",
        stage_define_idempotency_key="frozen-initial-define-key",
        stage_run_request_id="frozen-initial-run-request",
        stage_run_idempotency_key="frozen-initial-run-key",
        stage_define_execution_options={"rpc_timeout_s": 45},
        stage_run_execution_options={
            "queue_timeout_s": 30, "execution_timeout_s": 240, "rpc_timeout_s": 45,
        },
    )
    try:
        assert value["defined"]["success"] is True, value["defined"]
        result = value["result"]
        assert result is not None and result["success"] is True, result
        attempt = value["attempt"]
        assert attempt["status"] == "ACCEPTED"
        assert attempt["acceptance_status"] == "INITIAL_OUTPUT_ACCEPTED"
        assert attempt["plan_id"] == "w21_initial_stage"
        assert attempt["stage_id"] == "thermal_initial"
        assert attempt["expected_revision"] == 2
        assert value["stage_define_execution"]["request_id"] == "frozen-initial-define-request"
        assert value["stage_define_execution"]["idempotency_key"] == "frozen-initial-define-key"
        assert value["stage_define_execution"]["rpc_timeout_s"] == 45
        assert value["stage_run_execution"]["request_id"] == "frozen-initial-run-request"
        assert value["stage_run_execution"]["idempotency_key"] == "frozen-initial-run-key"
        assert value["stage_run_execution"]["queue_timeout_s"] == 30
        assert value["stage_run_execution"]["execution_timeout_s"] == 240
        assert value["stage_run_execution"]["rpc_timeout_s"] == 45
        assert [row["operation"] for row in value["public_dispatches"]] == [
            "experiment.stage_define", "experiment.stage_run",
        ]
        acceptance = result["data"]["initial_output_acceptance"]
        proof = acceptance["native_evidence"]
        assert acceptance["status"] == "PASS"
        assert proof["managed_internal_action_plan"] == [
            "study.inspect:preflight", "study.inspect:postsolve", "solver.inspect:active-variables",
            "Variables.xmeshInfo:active-variables", "dataset.solution_indices:output-tuple",
            "dataset.solution_indices:solution-mesh-association", "dataset.solution_indices:field-binding:0",
            "result.evaluate:field:0", "mesh.inspect:post-stage",
        ]
        assert proof["internal_read_cap"] == 12
        assert result["data"]["internal_read_count"] == proof["internal_read_count"]
        assert result["data"]["internal_read_count"] <= proof["internal_read_cap"]
        assert result["data"]["raw_worker_rpc_count"] == proof["raw_worker_rpc_count"] > 0
        row = next(item for item in attempt["evidence"]
                   if item.get("kind") == "initial-output-native-evidence")
        assert row["native_evidence"] == proof
    finally:
        value["daemon"].close()


def test_initial_acceptance_rejects_missing_foreign_and_tampered_root_terminals(tmp_path):
    value = build_initial_stage_fixture(tmp_path, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is True, result
        proof = result["data"]["initial_output_acceptance"]["native_evidence"]
        stage = next(row for row in value["definition"]["stages"]
                     if row["stage_id"] == value["stage_id"])

        def validate(candidate):
            return validate_initial_output_acceptance(
                candidate, binding=candidate["stage_attempt_binding"], stage=stage,
                stage_run_operation_id=candidate["stage_run_operation_id"],
                solve_revision=candidate["solve_revision"],
                output_revision=candidate["output_revision"],
                observed_artifact_sha256=candidate["saved_artifact_observed_sha256"],
            )

        assert validate(proof) == (True, [])
        solve_dispatch = next(row for row in proof["solve_save_dispatches"]
                              if row["operation"] == "run_study")
        solve_id = solve_dispatch["worker_request_id"]
        xmesh_step = next(row for row in proof["native_output_readback"]["revision_chain"]
                          if row.get("readphase") == "initial-stage-active-variables-xmesh")
        xmesh_id = xmesh_step["worker_requests"][0]["worker_request_id"]

        tampered = copy.deepcopy(proof)
        row = next(row for row in tampered["worker_event_rows"]
                   if row.get("metadata", {}).get("request_id") == solve_id
                   and row.get("metadata", {}).get("phase") == "observed")
        row["metadata"]["w21_backend_binding"]["request_id"] = "foreign-stage-request"
        assert validate(tampered)[0] is False

        tampered = copy.deepcopy(proof)
        row = next(row for row in tampered["worker_event_rows"]
                   if row.get("metadata", {}).get("request_id") == xmesh_id
                   and row.get("metadata", {}).get("phase") == "observed")
        row["metadata"]["reply"]["request_id"] = "foreign-worker-request"
        assert validate(tampered)[0] is False

        tampered = copy.deepcopy(proof)
        tampered["worker_event_rows"] = [
            row for row in tampered["worker_event_rows"]
            if not (row.get("metadata", {}).get("request_id") == xmesh_id
                    and row.get("metadata", {}).get("phase") == "observed")
        ]
        assert validate(tampered)[0] is False

        tampered = copy.deepcopy(proof)
        row = next(row for row in tampered["worker_event_rows"]
                   if row.get("metadata", {}).get("request_id") == solve_id
                   and row.get("metadata", {}).get("phase") == "observed")
        row["metadata"]["status"] = "FAILED"
        row["metadata"]["reply"].update(ok=False, status="FAILED")
        assert validate(tampered)[0] is False
    finally:
        value["daemon"].close()


@pytest.mark.parametrize(("kind", "field", "replacement"), [
    ("solve", "method", "clear"),
    ("solve", "handle", "foreign-study-handle"),
    ("solve", "args", ["foreign"]),
    ("model", "tag", "foreign-model-tag"),
    ("model_snapshot", "tag", "foreign-snapshot-tag"),
])
def test_initial_acceptance_rejects_mutated_worker_payload_shape(tmp_path, kind, field, replacement):
    value = build_initial_stage_fixture(tmp_path, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is True, result
        proof = result["data"]["initial_output_acceptance"]["native_evidence"]
        stage = next(row for row in value["definition"]["stages"]
                     if row["stage_id"] == value["stage_id"])

        def validate(candidate):
            return validate_initial_output_acceptance(
                candidate, binding=candidate["stage_attempt_binding"], stage=stage,
                stage_run_operation_id=candidate["stage_run_operation_id"],
                solve_revision=candidate["solve_revision"],
                output_revision=candidate["output_revision"],
                observed_artifact_sha256=candidate["saved_artifact_observed_sha256"],
            )

        assert validate(proof) == (True, [])
        if kind == "solve":
            solve_id = next(row["worker_request_id"] for row in proof["solve_save_dispatches"]
                            if row["operation"] == "run_study")
        else:
            model_tag = proof["stage_attempt_binding"]["model_ref"]["model_tag"]
            solve_id = next(
                row["metadata"]["request_id"] for row in proof["worker_event_rows"]
                if row.get("metadata", {}).get("phase") == "submitted"
                and row.get("metadata", {}).get("kind") == kind
                and row.get("metadata", {}).get("metadata", {}).get("tag") == model_tag
            )
        tampered = copy.deepcopy(proof)
        pair = [row for row in tampered["worker_event_rows"]
                if row.get("metadata", {}).get("request_id") == solve_id]
        assert {row["metadata"]["phase"] for row in pair} == {"submitted", "observed"}
        for row in pair:
            payload = row["metadata"]["metadata"]
            assert payload.get(field) != replacement
            payload[field] = replacement
        assert validate(tampered)[0] is False
    finally:
        value["daemon"].close()


@pytest.mark.parametrize("phase", ["solve", "save"])
def test_initial_acceptance_binds_auxiliary_revision_to_stage_revision(tmp_path, phase):
    value = build_initial_stage_fixture(tmp_path, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is True, result
        proof = result["data"]["initial_output_acceptance"]["native_evidence"]
        stage = next(row for row in value["definition"]["stages"]
                     if row["stage_id"] == value["stage_id"])

        def validate(candidate):
            return validate_initial_output_acceptance(
                candidate, binding=candidate["stage_attempt_binding"], stage=stage,
                stage_run_operation_id=candidate["stage_run_operation_id"],
                solve_revision=candidate["solve_revision"],
                output_revision=candidate["output_revision"],
                observed_artifact_sha256=candidate["saved_artifact_observed_sha256"],
            )

        assert validate(proof) == (True, [])
        root_ids = {row["worker_request_id"] for row in proof["solve_save_dispatches"]}
        tampered = copy.deepcopy(proof)
        changed = set()
        for row in tampered["worker_event_rows"]:
            event = row.get("metadata")
            if not isinstance(event, dict) or event.get("request_id") in root_ids:
                continue
            backend_binding = event.get("w21_backend_binding")
            if isinstance(backend_binding, dict) and backend_binding.get("phase") == phase:
                backend_binding["expected_revision"] = 9876
                changed.add(event["request_id"])
        assert changed
        assert validate(tampered)[0] is False
    finally:
        value["daemon"].close()


@pytest.mark.parametrize("terminal_status", ["UNKNOWN", "SUCCEEDED", None],
                         ids=["unknown", "succeeded", "missing"])
def test_initial_acceptance_requires_failed_status_for_known_tlist_terminal(tmp_path, terminal_status):
    value = build_initial_stage_fixture(tmp_path, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is True, result
        proof = result["data"]["initial_output_acceptance"]["native_evidence"]
        stage = next(row for row in value["definition"]["stages"]
                     if row["stage_id"] == value["stage_id"])

        def validate(candidate):
            return validate_initial_output_acceptance(
                candidate, binding=candidate["stage_attempt_binding"], stage=stage,
                stage_run_operation_id=candidate["stage_run_operation_id"],
                solve_revision=candidate["solve_revision"],
                output_revision=candidate["output_revision"],
                observed_artifact_sha256=candidate["saved_artifact_observed_sha256"],
            )

        assert validate(proof) == (True, [])
        tampered = copy.deepcopy(proof)
        changed = 0
        for row in tampered["worker_event_rows"]:
            event = row.get("metadata")
            payload = event.get("metadata") if isinstance(event, dict) else None
            if (isinstance(event, dict) and event.get("phase") == "observed"
                    and isinstance(payload, dict) and payload.get("method") == "getDoubleArray"
                    and payload.get("args") == ["tlist"]):
                event["status"] = terminal_status
                changed += 1
        assert changed == 3
        assert validate(tampered)[0] is False
    finally:
        value["daemon"].close()


@pytest.mark.parametrize("fault", [
    "tlist_timeout", "tlist_request_id_mismatch", "tlist_unrelated_failure",
])
def test_optional_tlist_probe_does_not_hide_unknown_or_unrelated_worker_failures(tmp_path, fault):
    value = build_initial_stage_fixture(tmp_path, fault=fault, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is False, result
        attempt = value["attempt"]
        assert attempt["status"] != "ACCEPTED"
        assert attempt["acceptance_status"] != "INITIAL_OUTPUT_ACCEPTED"

        submitted = [row for row in value["transport_requests"]
                     if row.get("type") == "call" and row.get("method") == "getDoubleArray"
                     and row.get("args") == ["tlist"]]
        assert len(submitted) == 3
        job = value["daemon"].store.operation_job(attempt["operation_id"])
        assert isinstance(job, dict) and isinstance(job.get("job_id"), str)
        events = value["daemon"].store.events(job["job_id"], limit=1000)
        tlist_ids = {row["request_id"] for row in submitted}
        observed = [row for row in events if row.get("event") == "worker_request"
                    and isinstance(row.get("metadata"), dict)
                    and row["metadata"].get("request_id") in tlist_ids]
        if fault in {"tlist_timeout", "tlist_request_id_mismatch"}:
            assert not any(row["metadata"].get("phase") == "observed" for row in observed)
            assert any(row["metadata"].get("phase") in {"unknown", "unresponsive"}
                       for row in observed)
        else:
            failed = [row for row in observed
                      if row["metadata"].get("phase") == "observed"
                      and isinstance(row["metadata"].get("reply"), dict)]
            assert failed
            assert all(row["metadata"]["reply"].get("status") == "FAILED"
                       for row in failed)
    finally:
        value["daemon"].close()


def test_multiple_active_variables_features_make_zero_xmesh_calls(tmp_path):
    value = build_initial_stage_fixture(tmp_path, fault="ambiguous_variables", runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is False, result
        assert value["attempt"]["status"] != "ACCEPTED"
        variables_reads = [row for row in value["transport_requests"]
                           if row.get("type") == "call" and row.get("method") == "isActive"
                           and row.get("handle") in {"variables-v1", "variables-v2"}]
        assert len(variables_reads) == 2
        xmesh_calls = [row for row in value["transport_requests"]
                       if row.get("type") == "call"
                       and row.get("method") == "getVariablesXmeshReadback"]
        assert xmesh_calls == []
    finally:
        value["daemon"].close()


def test_public_solver_inspect_stays_read_only_and_rejects_ticket_flags(tmp_path):
    value = build_initial_stage_fixture(tmp_path, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is True, result
        ref = value["model_ref"]
        state = value["service"].ledger._state_for(model_ref_from_mapping(ref))
        revision = result["execution"]["revision"]
        before_revision = state.revision
        assert before_revision == revision and state.dirty is False

        execution = {
            "project_id": value["project_id"], "session_id": ref["session_id"],
            "model_ref": ref, "expected_revision": revision,
            "request_id": "public-solver-inspect-read",
            "idempotency_key": "public-solver-inspect-read",
        }
        path = {"segments": [{"collection": "sol", "tag": "sol1"}]}
        public_read = value["daemon"].dispatch({
            "operation": "solver.inspect", "arguments": {"path": path},
            "execution": execution,
        })
        assert public_read["success"] is True, public_read
        assert state.revision == revision and state.dirty is False

        feature_path = public_read["data"]["features"][0]["path"]
        flagged = value["daemon"].dispatch({
            "operation": "solver.inspect",
            "arguments": {"path": feature_path, "variables_xmesh_ticket": True,
                           "effect_override": "EVALUATE"},
            "execution": {**execution, "request_id": "public-solver-inspect-forged-ticket",
                          "idempotency_key": "public-solver-inspect-forged-ticket",
                              "variables_xmesh_ticket": True, "effect_override": "EVALUATE"},
        })
        assert flagged["success"] is False
        assert flagged["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert flagged["error"]["details"]["cause_code"] == "INVALID_REQUEST"
        assert flagged["error"]["details"]["witness"]["engine_calls"] == 0
        assert state.revision == revision and state.dirty is False
        assert sum(row.get("type") == "call" and row.get("method") == "getVariablesXmeshReadback"
                   for row in value["transport_requests"]) == 1
    finally:
        value["daemon"].close()


def test_unknown_xmesh_ticket_marks_model_dirty_and_is_not_replayed_or_saved(tmp_path):
    value = build_initial_stage_fixture(tmp_path, fault="xmesh_timeout", runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = value["attempt"]
        assert attempt["status"] == "UNKNOWN"
        assert attempt["acceptance_status"] == "UNKNOWN"
        model_state = value["service"].ledger._state_for(
            model_ref_from_mapping(value["model_ref"]),
        )
        assert model_state.dirty is True
        xmesh_calls = [row for row in value["transport_requests"]
                       if row.get("type") == "call"
                       and row.get("method") == "getVariablesXmeshReadback"]
        assert len(xmesh_calls) == 1
        job = value["daemon"].store.operation_job(attempt["operation_id"])
        assert isinstance(job, dict) and isinstance(job.get("job_id"), str)
        events = value["daemon"].store.events(job["job_id"], limit=1000)
        xmesh_id = xmesh_calls[0]["request_id"]
        xmesh_events = [row for row in events
                        if row.get("event") == "worker_request"
                        and isinstance(row.get("metadata"), dict)
                        and row["metadata"].get("request_id") == xmesh_id]
        phases = [row["metadata"].get("phase") for row in xmesh_events]
        assert phases.count("submitted") == 1
        assert phases.count("unknown") == 1
        assert "observed" not in phases
        assert sum(row.get("type") == "call" and row.get("method") == "save"
                   for row in value["transport_requests"]) == 0
    finally:
        value["daemon"].close()


@pytest.mark.parametrize("fault", [
    "inactive_variables", "missing_dof", "bad_frame", "wrong_tuple",
    "wrong_unit", "forced_unit", "missing_unit", "wrong_control_numeric", "wrong_mesh",
])
def test_registered_initial_stage_mismatches_never_accept(tmp_path, fault):
    value = build_initial_stage_fixture(tmp_path, fault=fault, runner_profile=True)
    try:
        assert value["defined"]["success"] is True, value["defined"]
        result = value["result"]
        assert result is not None and result["success"] is False
        assert result["error"]["code"] in {"STAGE_ACCEPTANCE_UNVERIFIED", "EXECUTION_STATE_UNKNOWN"}
        attempt = value["attempt"]
        assert attempt["status"] != "ACCEPTED"
        assert attempt["acceptance_status"] != "INITIAL_OUTPUT_ACCEPTED"
    finally:
        value["daemon"].close()


@pytest.mark.parametrize("fault", ["unknown_after_eval", "dispatch_mismatch"])
def test_unknown_initial_field_dispatch_is_not_replayed_or_saved(tmp_path, fault):
    value = build_initial_stage_fixture(tmp_path, fault=fault, runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = value["attempt"]
        assert attempt["status"] == "UNKNOWN"
        assert attempt["execution_status"] == "UNKNOWN"
        calls = [row for row in value["transport_requests"] if row.get("type") == "call"]
        assert sum(row.get("method") == "run" and row.get("handle") == "study-std1" for row in calls) == 1
        assert sum(row.get("method") == "getStrictFieldReadback" for row in calls) == 1
        assert not any(row.get("method") == "save" for row in calls)
    finally:
        value["daemon"].close()


def test_initial_artifact_bytes_changed_after_first_hash_remain_unknown(tmp_path):
    value = build_initial_stage_fixture(tmp_path, fault="artifact_mismatch", runner_profile=True)
    try:
        result = value["result"]
        assert result is not None and result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = value["attempt"]
        assert attempt["status"] == "UNKNOWN"
        assert attempt["acceptance_status"] == "UNKNOWN"
        artifact = next(item for item in attempt["evidence"]
                        if item.get("kind") == "saved-stage-artifact")
        path = artifact["path"]
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert digest != artifact["sha256"]
        calls = [row for row in value["transport_requests"] if row.get("type") == "call"]
        assert sum(row.get("method") == "save" for row in calls) == 1
    finally:
        value["daemon"].close()
