from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import types
import importlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tools.run_native_w23_full3d_setup import (
    BMA_PROBE_BUDGET,
    BMA_PROBE_ROUTE_ALLOWLIST,
    BMA_PRODUCER_API_EVIDENCE,
    BMA_MAPPING_PROBE_BUDGET,
    BMA_MAPPING_PROBE_ROUTE_ALLOWLIST,
    BMA_MAPPING_PROBE_API_EVIDENCE,
    EXPLICIT_SITE_PACKAGES,
    EXPECTED_PYTHON,
    PublicDispatchAdapter,
    CleanupRefused,
    CandidateError,
    REPO,
    _assert_no_editable_fallback,
    _audit_loaded_project_modules,
    _bma_run_failure_evidence,
    _lsof_listeners,
    _bind_native_server_identity,
    _job_ledger_terminal,
    _process_identity,
    _start_control_daemon,
    _stop_owned_process,
    _resource_ownership_receipt,
    _source_inventory,
    export_published_archive,
    build_disconnect_request,
    _finalize_science_counters,
    _json_hash,
    orchestrate_cleanup,
    validate_native_mode_configuration,
    verify_candidate,
)


def _retirement(*, worker_id: str = "worker-1", epoch: int = 5,
                pid: int = 4101, birth: int = 1700000000000) -> dict[str, Any]:
    return {"success": True, "data": {
        "project_id": "project-1", "session_id": "session-1",
        "state": "DISCONNECTED", "client_state": "RETIRED",
        "server_stopped": False,
        "worker_retirement": {
            "status": "RETIRED", "worker_instance_id": worker_id,
            "worker_epoch": epoch,
            "process_identity": {"pid": pid, "start_epoch_ms": birth},
            "exact_popen_handle": True,
            "birth_identity_matched_before_close": True,
            "child_exit_confirmed": True, "child_reaped": True,
            "admission_fence": "RETIRED",
            "disconnect_rpc_dispatched": True, "worker_close_started": True,
        },
    }}


def _inspect(*, worker_id: str = "worker-1", epoch: int = 5,
             runtime_live: bool = False) -> dict[str, Any]:
    return {"success": True, "data": {
        "project_id": "project-1", "runtime_live": runtime_live,
        "worker_binding": None,
        "lifecycle": {"session_id": "session-1", "state": "DISCONNECTED",
                      "client_state": "RETIRED", "worker_instance_id": worker_id,
                      "worker_epoch": epoch},
    }}


def _stopped(pid: int = 4201, birth: int = 1700000000100) -> dict[str, Any]:
    return {"status": "STOPPED_AND_REAPED", "pid": pid,
            "start_epoch_ms": birth, "child_exit_confirmed": True,
            "child_reaped": True, "listener_absent": True}


def test_bma_probe_budget_keeps_full_sequence_run_inside_wall_and_cleanup_reserve() -> None:
    budget = BMA_PROBE_BUDGET
    route_caps = budget["route_wait_caps_seconds"]
    assert budget["profile"] == "w23_single_receiver_bma_output_probe_v1"
    assert budget["max_server_processes"] == budget["max_managed_workers"] == 1
    assert budget["max_gui_processes"] == 0
    assert budget["study_run_calls"] == 0 and budget["solver_calls"] == 1
    assert budget["wall_clock_seconds_from_server_birth_including_cleanup"] == 2700
    assert budget["reserved_cleanup_seconds"] == 120
    assert budget["unallocated_margin_seconds"] == 300
    assert sum(route_caps.values()) + budget["reserved_cleanup_seconds"] <= \
        budget["wall_clock_seconds_from_server_birth_including_cleanup"]
    assert (sum(route_caps.values()) + budget["reserved_cleanup_seconds"]
            + budget["unallocated_margin_seconds"]
            == budget["wall_clock_seconds_from_server_birth_including_cleanup"])
    assert BMA_PROBE_ROUTE_ALLOWLIST.count(
        "operation_call:code.execute_java:run_bma_output_probe:SolverSequence.runAll") == 1
    assert not any("Study.run" in route for route in BMA_PROBE_ROUTE_ALLOWLIST)
    assert {entry["api"] for entry in BMA_PRODUCER_API_EVIDENCE} == {
        "Study.createAutoSequences(String)",
        "SolverSequence.runAll() and SolverSequence.isEmpty()",
        "SolutionInfo.getOuterSolnum(), getSolnum(int, boolean), getSolverSequence(int)",
    }


def test_bma_mapping_probe_budget_extends_bma_probe_without_changing_its_contract() -> None:
    budget = BMA_MAPPING_PROBE_BUDGET
    caps = budget["route_wait_caps_seconds"]
    assert BMA_PROBE_BUDGET["wall_clock_seconds_from_server_birth_including_cleanup"] == 2700
    assert BMA_MAPPING_PROBE_BUDGET["wall_clock_seconds_from_server_birth_including_cleanup"] == 3240
    assert budget["profile"] == "w23_single_receiver_bma_basis_field_mapping_probe_v1"
    assert budget["study_run_calls"] == 0 and budget["solver_calls"] == 1
    assert budget["max_server_processes"] == budget["max_managed_workers"] == 1
    assert budget["max_gui_processes"] == 0
    assert budget["reserved_cleanup_seconds"] == 120
    assert budget["unallocated_margin_seconds"] == 300
    assert {name: caps[name] for name in (
        "bma_basis_dataset_list", "bma_basis_dataset_solution_indices",
        "bma_basis_fields_ordinal1", "bma_basis_fields_ordinal2")} == {
            "bma_basis_dataset_list": 90,
            "bma_basis_dataset_solution_indices": 90,
            "bma_basis_fields_ordinal1": 180,
            "bma_basis_fields_ordinal2": 180,
        }
    assert sum(caps.values()) + budget["reserved_cleanup_seconds"] \
        + budget["unallocated_margin_seconds"] == \
        budget["wall_clock_seconds_from_server_birth_including_cleanup"]
    assert BMA_MAPPING_PROBE_ROUTE_ALLOWLIST.count(
        "operation_call:code.execute_java:run_bma_output_probe:SolverSequence.runAll") == 1
    assert BMA_MAPPING_PROBE_ROUTE_ALLOWLIST.count(
        "operation_call:code.execute_java:bma_basis_fields_ordinal1") == 1
    assert BMA_MAPPING_PROBE_ROUTE_ALLOWLIST.count(
        "operation_call:code.execute_java:bma_basis_fields_ordinal2") == 1
    assert len(BMA_MAPPING_PROBE_API_EVIDENCE) == 1
    assert not any("Study.run" in route for route in BMA_MAPPING_PROBE_ROUTE_ALLOWLIST)


def test_candidate_freeze_rejects_mutated_bma_probe_solver_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_native_w23_full3d_setup as runner

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source = {"base_commit": "published-base", "source_closure_sha256": "a" * 64}
    monkeypatch.setattr(runner, "_source_inventory", lambda repo, base: source)
    body = {
        "schema_version": 1,
        "status": "PREPARED_BMA_PROBE_NOT_NATIVE",
        "campaign_profile": "bma_probe",
        "source": source,
        "budget": BMA_PROBE_BUDGET,
        "routes": BMA_PROBE_ROUTE_ALLOWLIST,
        "bma_producer_api_evidence": BMA_PRODUCER_API_EVIDENCE,
        "bma_mapping_api_evidence": [],
        "field_mapping_policy": [],
        "field_semantics_kb_evidence": [],
    }
    freeze = {**body, "candidate_sha256": _json_hash(body)}
    (evidence / "candidate_freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    assert verify_candidate(repo=tmp_path, evidence=evidence,
                            reviewed_sha256=freeze["candidate_sha256"]) == freeze

    body["budget"] = {**BMA_PROBE_BUDGET, "solver_calls": 0}
    mutated = {**body, "candidate_sha256": _json_hash(body)}
    (evidence / "candidate_freeze.json").write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(CandidateError, match="budget or route allowlist"):
        verify_candidate(repo=tmp_path, evidence=evidence,
                         reviewed_sha256=mutated["candidate_sha256"])


def test_candidate_freeze_binds_bma_mapping_profile_budget_policy_and_api_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_native_w23_full3d_setup as runner
    from tools.w23_full3d_science import (
        BMA_FIELD_MAPPING_POLICY, COMSOL_INTERP_UNIT_KB_EVIDENCE,
        COMSOL_PORT_MODE_FIELD_KB_EVIDENCE,
    )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source = {"base_commit": "published-base", "source_closure_sha256": "a" * 64}
    monkeypatch.setattr(runner, "_source_inventory", lambda repo, base: source)
    field_semantics = {
        "port_mode_suffix": dict(COMSOL_PORT_MODE_FIELD_KB_EVIDENCE),
        "interp_units": dict(COMSOL_INTERP_UNIT_KB_EVIDENCE),
    }
    body = {
        "schema_version": 1,
        "status": "PREPARED_BMA_MAPPING_PROBE_NOT_NATIVE",
        "campaign_profile": "bma_mapping_probe",
        "source": source,
        "budget": BMA_MAPPING_PROBE_BUDGET,
        "routes": BMA_MAPPING_PROBE_ROUTE_ALLOWLIST,
        "bma_producer_api_evidence": BMA_PRODUCER_API_EVIDENCE,
        "bma_mapping_api_evidence": BMA_MAPPING_PROBE_API_EVIDENCE,
        "field_mapping_policy": dict(BMA_FIELD_MAPPING_POLICY),
        "field_semantics_kb_evidence": field_semantics,
    }
    freeze = {**body, "candidate_sha256": _json_hash(body)}
    (evidence / "candidate_freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    assert verify_candidate(repo=tmp_path, evidence=evidence,
                            reviewed_sha256=freeze["candidate_sha256"]) == freeze

    body["budget"] = {**BMA_MAPPING_PROBE_BUDGET,
                       "route_wait_caps_seconds": {
                           **BMA_MAPPING_PROBE_BUDGET["route_wait_caps_seconds"],
                           "bma_basis_fields_ordinal2": 181}}
    mutated = {**body, "candidate_sha256": _json_hash(body)}
    (evidence / "candidate_freeze.json").write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(CandidateError, match="budget or route allowlist"):
        verify_candidate(repo=tmp_path, evidence=evidence,
                         reviewed_sha256=mutated["candidate_sha256"])


def test_source_closure_rejects_missing_required_v2_runtime_and_test_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_native_w23_full3d_setup as runner

    required_base_dependencies = {
        "tools/w23_mode_basis_v2.py",
        "tools/w23_mode_basis_v2_native_plan.py",
        "tests/test_w23_mode_basis_v2_results.py",
        "tests/test_w23_mode_basis_v2_native_plan.py",
    }
    required_candidate_overlay = {"tests/test_w23_mode_basis_v2.py"}
    assert required_base_dependencies <= runner.EXTRA_CLOSURE_PATHS
    assert required_candidate_overlay <= runner.OVERLAY_PATHS
    monkeypatch.setattr(runner, "OVERLAY_PATHS", set())
    for relative in sorted(required_base_dependencies):
        monkeypatch.setattr(runner, "EXTRA_CLOSURE_PATHS", {relative})
        with pytest.raises(CandidateError, match="source closure is incomplete") as exc:
            runner._closure_paths(tmp_path)
        assert relative in str(exc.value)
    monkeypatch.setattr(runner, "EXTRA_CLOSURE_PATHS", set())
    for relative in sorted(required_candidate_overlay):
        monkeypatch.setattr(runner, "OVERLAY_PATHS", {relative})
        with pytest.raises(CandidateError, match="source closure is incomplete") as exc:
            runner._closure_paths(tmp_path)
        assert relative in str(exc.value)


def test_isolated_source_manifest_rejects_missing_required_v2_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_native_w23_full3d_setup as runner

    required = {
        "tools/w23_mode_basis_v2.py",
        "tools/w23_mode_basis_v2_native_plan.py",
        "tests/test_w23_mode_basis_v2_results.py",
        "tests/test_w23_mode_basis_v2_native_plan.py",
    }
    overlay = {"candidate_overlay.py", "tests/test_w23_mode_basis_v2.py"}
    monkeypatch.setattr(runner, "OVERLAY_PATHS", overlay)
    monkeypatch.setattr(runner, "EXTRA_CLOSURE_PATHS", required)
    monkeypatch.setattr(runner, "_closure_paths", lambda _repo: sorted(required | overlay))

    base_files = {
        relative: {"bytes": 0, "sha256": "0" * 64}
        for relative in sorted(required - {"tools/w23_mode_basis_v2.py"})
    }
    manifest = {
        "schema_version": 1,
        "status": "EXACT_GIT_ARCHIVE_SOURCE_ONLY",
        "base_commit": "a" * 40,
        "published_origin_main_at_export": "b" * 40,
        "checkout_head_at_export": "b" * 40,
        "source_files": base_files,
        "test_support_files": sorted(runner.TEST_SUPPORT_PATHS),
        "source_closure_sha256": runner._json_hash(base_files),
    }
    manifest["manifest_sha256"] = runner._json_hash(manifest)
    (tmp_path / ".w23_published_archive_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CandidateError, match="exact expected published closure"):
        runner._source_inventory(tmp_path, manifest["base_commit"])


def test_science_finalization_preserves_success_unknown_and_preflight_evidence() -> None:
    success = {"solver_call_attempts": 1, "solver_calls": 1,
        "native_scientific_result": "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED",
        "mode_producer_lineage": "VERIFIED_BY_ISOLATED_ONE_STEP_STUDY_AND_EXACT_SEQUENCE_RUNALL"}
    _finalize_science_counters(success, "bma_probe")
    assert success["solver_calls"] == 1
    assert success["native_scientific_result"] == "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED"
    assert success["mode_producer_lineage"].startswith("VERIFIED_BY_ISOLATED_ONE_STEP")
    assert success["study_run_calls"] == 0
    assert success["numeric_port_mode_field_mapping"] == "UNVERIFIED"

    unknown_after_dispatch = {"solver_call_attempts": 1, "solver_calls": 0,
                              "native_scientific_result": "NOT_RUN"}
    _finalize_science_counters(unknown_after_dispatch, "bma_probe")
    assert unknown_after_dispatch["solver_calls"] == "UNKNOWN"
    assert unknown_after_dispatch["native_scientific_result"] == "UNKNOWN"
    assert unknown_after_dispatch["mode_producer_lineage"] == "UNVERIFIED"

    success_then_cleanup_failure = {"status": "UNKNOWN_PRESERVE_OWNED_RESOURCES",
        "solver_call_attempts": 1, "solver_calls": 1,
        "native_scientific_result": "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED",
        "mode_producer_lineage": "VERIFIED_BY_ISOLATED_ONE_STEP_STUDY_AND_EXACT_SEQUENCE_RUNALL",
        "cleanup_error": {"stage": "worker_retirement"}}
    _finalize_science_counters(success_then_cleanup_failure, "bma_probe")
    assert success_then_cleanup_failure["status"] == "UNKNOWN_PRESERVE_OWNED_RESOURCES"
    assert success_then_cleanup_failure["solver_calls"] == 1
    assert success_then_cleanup_failure["native_scientific_result"] == \
        "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED"
    assert success_then_cleanup_failure["mode_producer_lineage"].startswith(
        "VERIFIED_BY_ISOLATED_ONE_STEP")

    preflight_failure = {"solver_call_attempts": 0, "solver_calls": 0,
                         "native_scientific_result": "NOT_RUN"}
    _finalize_science_counters(preflight_failure, "bma_probe")
    assert preflight_failure["solver_calls"] == 0
    assert preflight_failure["native_scientific_result"] == "NOT_RUN"
    assert preflight_failure["study_run_calls"] == 0
    assert preflight_failure["mode_producer_lineage"] == "UNVERIFIED"


def test_mapping_profile_finalization_preserves_paired_attempts_unknown_and_cleanup_failure() -> None:
    paired_success = {
        "status": "BMA_MAPPING_PROBE_COMPLETE_CLEANUP_VERIFIED_MAPPING_UNVERIFIED",
        "solver_call_attempts": 1, "solver_calls": 1,
        "native_scientific_result": "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED",
        "mode_producer_lineage": "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER",
        "field_sample_attempts": 2, "field_sample_calls": 2,
        "field_sample_readbacks_validated": 2,
    }
    _finalize_science_counters(paired_success, "bma_mapping_probe")
    assert paired_success["study_run_calls"] == 0
    assert paired_success["solver_calls"] == 1
    assert paired_success["field_sample_attempts"] == 2
    assert paired_success["field_sample_calls"] == 2
    assert paired_success["field_sample_readbacks_validated"] == 2
    assert paired_success["field_sampling_status"] == "TWO_RAW_SAMPLES_RETURNED_MAPPING_UNVERIFIED"
    assert paired_success["numeric_port_mode_field_mapping"] == "UNVERIFIED"

    dispatched_unknown = {
        "status": "UNKNOWN_PRESERVE_OWNED_RESOURCES", "solver_call_attempts": 1,
        "solver_calls": 1,
        "native_scientific_result": "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED",
        "field_sample_attempts": 1, "field_sample_calls": 0,
        "field_sample_readbacks_validated": 0,
    }
    _finalize_science_counters(dispatched_unknown, "bma_mapping_probe")
    assert dispatched_unknown["solver_calls"] == 1
    assert dispatched_unknown["field_sample_attempts"] == 1
    assert dispatched_unknown["field_sample_calls"] == 0
    assert dispatched_unknown["field_sampling_status"] == "UNKNOWN_OR_PARTIAL"
    assert dispatched_unknown["native_scientific_result"] == \
        "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED"

    succeeded_then_cleanup_failed = {
        "status": "UNKNOWN_PRESERVE_OWNED_RESOURCES", "solver_call_attempts": 1,
        "solver_calls": 1,
        "native_scientific_result": "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED",
        "mode_producer_lineage": "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER",
        "field_sample_attempts": 2, "field_sample_calls": 2,
        "field_sample_readbacks_validated": 2,
        "cleanup_error": {"stage": "worker_retirement"},
    }
    _finalize_science_counters(succeeded_then_cleanup_failed, "bma_mapping_probe")
    assert succeeded_then_cleanup_failed["status"] == "UNKNOWN_PRESERVE_OWNED_RESOURCES"
    assert succeeded_then_cleanup_failed["field_sample_attempts"] == 2
    assert succeeded_then_cleanup_failed["field_sample_calls"] == 2
    assert succeeded_then_cleanup_failed["field_sample_readbacks_validated"] == 2
    assert succeeded_then_cleanup_failed["field_sampling_status"] == \
        "TWO_RAW_SAMPLES_RETURNED_MAPPING_UNVERIFIED"
    assert succeeded_then_cleanup_failed["mode_producer_lineage"] == \
        "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER"

    preflight = {"solver_call_attempts": 0, "solver_calls": 0,
                 "native_scientific_result": "NOT_RUN", "field_sample_attempts": 0,
                 "field_sample_calls": 0, "field_sample_readbacks_validated": 0}
    _finalize_science_counters(preflight, "bma_mapping_probe")
    assert preflight["solver_calls"] == 0
    assert preflight["field_sampling_status"] == "NOT_RUN"
    assert preflight["native_scientific_result"] == "NOT_RUN"


def test_bma_unknown_failure_receipt_keeps_original_request_job_and_observation() -> None:
    from tools.w23_full3d_science import ManagedRouteOutcomeError

    request = {"operation": "operation_call", "execution": {
        "project_id": "project-1", "request_id": "req-1",
        "idempotency_key": "idem-1"}}
    observed = {"dispatch": {"success": True, "data": {"job_id": "job-1"}},
                "job_wait_responses": [{"success": False, "error": {"code": "TIMEOUT"}}]}
    error = ManagedRouteOutcomeError("bma-run", "UNKNOWN", "job wait timed out",
                                     response=observed, job_id="job-1")
    evidence = _bma_run_failure_evidence(request, error)
    assert evidence == {"request": request, "outcome": "UNKNOWN", "job_id": "job-1",
                        "retry_forbidden": True, "observed_response": observed}


@pytest.mark.parametrize("status,expected_exit", [
    ("BMA_PROBE_FAILED_CLEANUP_VERIFIED", 1),
    ("BMA_PROBE_COMPLETE_CLEANUP_VERIFIED_FULL3D_SCIENCE_NOT_RUN", 0),
    ("BMA_MAPPING_PROBE_FAILED_CLEANUP_VERIFIED", 1),
    ("BMA_MAPPING_PROBE_COMPLETE_CLEANUP_VERIFIED_MAPPING_UNVERIFIED", 0),
])
def test_cli_does_not_report_failed_bma_probe_as_success_even_when_cleanup_verified(
    status: str, expected_exit: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_native_w23_full3d_setup as runner

    monkeypatch.setattr(runner, "execute_candidate", lambda **_kwargs: {"status": status})
    code = runner.main(["execute", "--evidence", str(tmp_path),
                        "--reviewed-candidate-sha256", "a" * 64])
    assert code == expected_exit


def _cleanup(**overrides: Any) -> tuple[list[str], list[str]]:
    events: list[str] = []
    kwargs: dict[str, Any] = {
        "worker_state": "CONNECTED", "project_id": "project-1",
        "session_id": "session-1", "worker_instance_id": "worker-1",
        "connected_epoch": 4, "detached_epoch": 5,
        "all_project_jobs_terminal": True,
        "disconnect": lambda: (events.append("disconnect") or _retirement()),
        "inspect": lambda: (events.append("inspect") or _inspect()),
        "stop_server": lambda: (events.append("server") or _stopped()),
        "stop_control": lambda: (events.append("control") or _stopped(4301, 1700000000200)),
    }
    kwargs.update(overrides)
    return orchestrate_cleanup(**kwargs), events


def test_cleanup_retires_exact_worker_before_server_and_control() -> None:
    completed, events = _cleanup()
    assert events == ["disconnect", "inspect", "server", "control"]
    assert completed == [
        "exact_managed_worker_retired", "public_disconnected_inspect_verified",
        "exact_task_server_stopped_and_reaped", "exact_control_daemon_stopped_and_reaped",
    ]


def test_unknown_disconnect_preserves_server_and_control() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="disconnect outcome unknown"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: (_ for _ in ()).throw(TimeoutError("transport unknown")),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


@pytest.mark.parametrize("response", [
    _retirement(worker_id="different-worker"),
    _retirement(epoch=4),
    _retirement(pid=1),
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "disconnect_rpc_dispatched": False}}},
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "worker_close_started": False}}},
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "child_reaped": False}}},
])
def test_retirement_identity_or_reap_gap_preserves_both_servers(response: dict[str, Any]) -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or response,
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect"]


def test_retired_inspect_mismatch_prevents_both_process_stops() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="session_inspect"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(runtime_live=True),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect", "inspect"]


def test_nonterminal_or_unknown_project_job_blocks_retirement() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="terminal ledger"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=False,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


def test_server_stop_failure_does_not_stop_control_daemon() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="server_stop"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or {"status": "STOP_UNVERIFIED"},
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect", "inspect", "server"]


def test_control_stop_failure_is_reported_after_exact_server_cleanup() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="control_stop"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or {"status": "STOP_UNVERIFIED"},
        )
    assert events == ["disconnect", "inspect", "server", "control"]


def test_never_dispatched_worker_stops_only_owned_processes() -> None:
    events: list[str] = []
    completed = orchestrate_cleanup(
        worker_state="NEVER_DISPATCHED", project_id=None, session_id=None,
        worker_instance_id=None, connected_epoch=None, detached_epoch=None,
        all_project_jobs_terminal=True, disconnect=None, inspect=None,
        stop_server=lambda: events.append("server") or {"status": "NOT_STARTED", "child_started": False},
        stop_control=lambda: events.append("control") or _stopped(),
    )
    assert events == ["server", "control"]
    assert completed == ["exact_task_server_stopped_and_reaped",
                         "exact_control_daemon_stopped_and_reaped"]


def test_unknown_connect_or_worker_state_never_cleans_up() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="UNKNOWN"):
        orchestrate_cleanup(
            worker_state="UNKNOWN", project_id=None, session_id=None,
            worker_instance_id=None, connected_epoch=None, detached_epoch=None,
            all_project_jobs_terminal=True, disconnect=None, inspect=None,
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


def test_disconnect_request_uses_public_scoped_retirement_envelope() -> None:
    request = build_disconnect_request(project_id="project-1", session_id="session-1",
        request_id="request-1", idempotency_key="idem-1")
    assert request["operation"] == "session.disconnect"
    assert request["arguments"] == {"retire_worker": True}
    assert request["execution"]["project_id"] == "project-1"
    assert request["execution"]["session_id"] == "session-1"
    assert request["execution"]["request_id"] == "request-1"
    assert request["execution"]["idempotency_key"] == "idem-1"


def test_disconnect_request_rejects_missing_session_identity() -> None:
    with pytest.raises(Exception, match="exact project/session"):
        build_disconnect_request(project_id="project-1", session_id=" ",
            request_id="request-1", idempotency_key="idem-1")


def _native_build_readback() -> dict[str, Any]:
    def properties(port_name: str) -> dict[str, Any]:
        return {"requested_properties": {
            key: {"has_property_exact": True, "string_readback": value}
            for key, value in {"PortType": "Numeric", "PortName": port_name,
                               "PortModeNumber": "1"}.items()}}
    return {
        "ports": [
            {"tag": "portIn3d", "properties": properties("1")},
            {"tag": "portOut3d", "properties": properties("2")},
        ],
        "study_steps": [
            {"tag": "bmaInput3d", "type": "BoundaryModeAnalysis", "port": "1",
             "modeFreq": "f0", "neigs": 2},
            {"tag": "bmaOutput3d", "type": "BoundaryModeAnalysis", "port": "2",
             "modeFreq": "f0", "neigs": 2},
            {"tag": "freq3d", "type": "Frequency", "plist": "f0"},
        ],
    }


def test_native_configuration_readback_keeps_port_number_separate_from_two_basis_ordinals() -> None:
    proof = validate_native_mode_configuration(_native_build_readback())
    assert proof == {
        "status": "NATIVE_CONFIGURATION_PROPERTIES_READ_BACK",
        "receiver_numeric_port": {"feature_tag": "portOut3d", "port_name": "2",
                                   "port_mode_number": 1},
        "bma_output_step": {"feature_tag": "bmaOutput3d", "port_name": "2",
                             "mode_frequency": "f0", "requested_eigensolutions": 2},
        "basis_ordinals": [1, 2],
        "basis_ordinal_is_not_port_mode_number": True,
        "producer_step_binding": "UNVERIFIED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
    }


@pytest.mark.parametrize("mutation", [
    "wrong_port_name", "wrong_port_type", "wrong_port_mode", "missing_port_property",
    "duplicate_output_port", "wrong_bma_port", "wrong_frequency", "wrong_neigs",
    "boolean_neigs", "duplicate_bma_step", "wrong_bma_type", "missing_neigs",
])
def test_native_configuration_readback_fails_closed_on_tampered_or_missing_identity(mutation: str) -> None:
    readback = _native_build_readback()
    if mutation == "wrong_port_name":
        readback["ports"][1]["properties"]["requested_properties"]["PortName"]["string_readback"] = "3"
    elif mutation == "wrong_port_type":
        readback["ports"][1]["properties"]["requested_properties"]["PortType"]["string_readback"] = "UserDefined"
    elif mutation == "wrong_port_mode":
        readback["ports"][1]["properties"]["requested_properties"]["PortModeNumber"]["string_readback"] = "2"
    elif mutation == "missing_port_property":
        readback["ports"][1]["properties"]["requested_properties"]["PortModeNumber"]["has_property_exact"] = False
    elif mutation == "duplicate_output_port":
        readback["ports"].append(readback["ports"][1])
    elif mutation == "wrong_bma_port":
        readback["study_steps"][1]["port"] = "1"
    elif mutation == "wrong_frequency":
        readback["study_steps"][1]["modeFreq"] = "f1"
    elif mutation == "wrong_neigs":
        readback["study_steps"][1]["neigs"] = 1
    elif mutation == "boolean_neigs":
        readback["study_steps"][1]["neigs"] = True
    elif mutation == "duplicate_bma_step":
        readback["study_steps"].append(dict(readback["study_steps"][1]))
    elif mutation == "wrong_bma_type":
        readback["study_steps"][1]["type"] = "Frequency"
    elif mutation == "missing_neigs":
        del readback["study_steps"][1]["neigs"]
    with pytest.raises(Exception):
        validate_native_mode_configuration(readback)


def test_solution_inventory_rechecks_bma_configuration_without_claiming_producer_lineage() -> None:
    readback = {"study_steps_in_configured_order": [
        {"tag": "bmaInput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "1", "modeFreq": "f0", "neigs": 2},
        {"tag": "bmaOutput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "2", "modeFreq": "f0", "neigs": 2},
        {"tag": "freq3d", "feature_type": "Frequency", "plist": "f0"},
    ]}
    proof = validate_native_mode_configuration(readback, inventory=True)
    assert proof["status"] == "NATIVE_CONFIGURATION_PROPERTIES_READ_BACK"
    assert proof["producer_step_binding"] == "UNVERIFIED"
    assert proof["numeric_port_mode_field_mapping"] == "UNVERIFIED"


class _FakeOwnedProcess:
    def __init__(self, pid: int, return_code: int | None):
        self.pid = pid
        self.returncode = return_code

    def poll(self) -> int | None:
        return self.returncode


def test_resource_receipt_binds_process_identity_to_popen_and_reports_live_worker_state() -> None:
    server = _FakeOwnedProcess(4101, 0)
    control = _FakeOwnedProcess(4102, None)
    receipt = _resource_ownership_receipt(
        server_proc=server, server_identity={"pid": 4101, "start_epoch_ms": 1700000000000},
        server_listener={"status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
                         "pid": 4101, "port": 50101, "endpoint": "127.0.0.1:50101"},
        control_proc=control, control_identity={"pid": 4102, "start_epoch_ms": 1700000000100},
        control_endpoint={"pid": 4102, "port": 50102, "token": "must-not-leak"},
        worker_state="CONNECTED", session={"project_id": "p", "session_id": "s",
            "worker_instance_id": "w", "worker_epoch": 7}, retirement_proof=None,
        process_cleanup={"server": {"status": "STOPPED_AND_REAPED", "listener_absent": True}})
    assert receipt["server"]["status"] == "EXITED_REAPED_EXACT_IDENTITY_BOUND"
    assert receipt["server"]["process_identity"] == {"pid": 4101, "start_epoch_ms": 1700000000000}
    assert receipt["server"]["child_reaped"] is True
    assert receipt["control_daemon"]["status"] == "LIVE_EXACT_IDENTITY_BOUND"
    assert "token" not in receipt["control_daemon"]["endpoint"]
    assert receipt["managed_worker"]["status"] == "CONNECTED"
    assert receipt["managed_worker"]["connected_worker_epoch"] == 7


def test_resource_receipt_does_not_bind_wrong_pid_or_missing_birth_to_popen() -> None:
    process = _FakeOwnedProcess(4101, None)
    receipt = _resource_ownership_receipt(
        server_proc=process, server_identity={"pid": 9999, "start_epoch_ms": 1700000000000},
        server_listener=None, control_proc=None, control_identity=None, control_endpoint=None,
        worker_state="NEVER_DISPATCHED", session=None, retirement_proof=None,
        process_cleanup={})
    assert receipt["server"]["status"] == "LIVE_IDENTITY_UNVERIFIED"
    assert receipt["server"]["process_identity"] is None
    assert receipt["server"]["exact_popen_handle"] is True
    assert receipt["control_daemon"]["status"] == "NOT_STARTED"
    assert receipt["managed_worker"]["status"] == "NEVER_DISPATCHED"


def _start_actual_isolation_helper_process(tmp_path: Path):
    """Start a harmless loopback child and obtain the real _process_snapshot shape."""
    from comsol_mcp._g2_isolation import _process_snapshot

    work = tmp_path.resolve()
    shadow = work / "comsol-shadow"
    shadow.mkdir()
    port_path = work / "listener.port"
    child = (
        "import socket,sys,time; s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); "
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('127.0.0.1',0)); s.listen(1); "
        "open(sys.argv[1],'w').write(str(s.getsockname()[1])); time.sleep(60)"
    )
    proc = subprocess.Popen(
        [str(EXPECTED_PYTHON), "-B", "-S", "-c", child,
         str(port_path), str(shadow), str(work)],
        cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5.0
        while not port_path.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert port_path.is_file() and proc.poll() is None
        port = int(port_path.read_text())
        raw = _process_snapshot(proc.pid)
        assert isinstance(raw, dict)
        assert {"pid", "birth", "command", "command_sha256"} <= set(raw)
        assert "start_epoch_ms" not in raw
        assert raw["pid"] == proc.pid
        assert str(work) in raw["command"] and str(shadow) in raw["command"]
        server = types.SimpleNamespace(
            work=work, shadow_root=shadow, proc=proc, port=port,
            process_identity={**raw, "port": port})
        listener = {
            "status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
            "pid": proc.pid, "port": port,
            "endpoint": f"127.0.0.1:{port}",
            "process_identity": server.process_identity,
        }
        return proc, server, listener
    except BaseException:
        proc.terminate()
        proc.wait(timeout=3.0)
        raise


def _stop_helper_test_child(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=3.0)


def test_native_server_identity_binds_actual_helper_shape_to_exact_popen_birth(tmp_path: Path) -> None:
    from comsol_mcp._g2_isolation import _process_snapshot

    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    try:
        assert "start_epoch_ms" not in server.process_identity
        bound = _bind_native_server_identity(server, listener)
        assert bound == _process_identity(proc.pid)
        assert type(bound["start_epoch_ms"]) is int and bound["start_epoch_ms"] > 0
        assert proc.poll() is None
        assert _process_snapshot(proc.pid)["birth"] == server.process_identity["birth"]
    finally:
        _stop_helper_test_child(proc)


@pytest.mark.parametrize("mutation", [
    "missing_birth", "wrong_pid", "malformed_command_hash", "tampered_command",
    "wrong_port", "listener_pid_mismatch", "listener_snapshot_mismatch",
])
def test_native_server_identity_rejects_actual_helper_shape_tampering(
    tmp_path: Path, mutation: str,
) -> None:
    import copy

    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    try:
        identity = copy.deepcopy(server.process_identity)
        observed_listener = copy.deepcopy(listener)
        if mutation == "missing_birth":
            identity.pop("birth")
        elif mutation == "wrong_pid":
            identity["pid"] += 1
        elif mutation == "malformed_command_hash":
            identity["command_sha256"] = "not-a-sha256"
        elif mutation == "tampered_command":
            identity["command"] += " /unbound-command"
        elif mutation == "wrong_port":
            identity["port"] += 1
        elif mutation == "listener_pid_mismatch":
            observed_listener["pid"] += 1
        elif mutation == "listener_snapshot_mismatch":
            observed_listener["process_identity"] = {**observed_listener["process_identity"], "birth": "tampered"}
        if mutation not in {"listener_pid_mismatch", "listener_snapshot_mismatch"}:
            server.process_identity = identity
            observed_listener["process_identity"] = identity
        with pytest.raises(CandidateError):
            _bind_native_server_identity(server, observed_listener)
    finally:
        _stop_helper_test_child(proc)


def test_native_server_identity_rejects_popen_that_has_exited(tmp_path: Path) -> None:
    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    _stop_helper_test_child(proc)
    assert proc.poll() is not None
    with pytest.raises(CandidateError):
        _bind_native_server_identity(server, listener)


def test_public_adapter_rejects_missing_or_mismatched_job_list_project_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comsol_mcp import _control_client

    adapter = PublicDispatchAdapter(tmp_path, expected_control_home=tmp_path / "control-home",
                                    archive_root=REPO)
    monkeypatch.setattr(adapter, "_verify_owned_control_route", lambda: None)
    dispatched: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    monkeypatch.setattr(_control_client, "dispatch",
                        lambda operation, arguments, execution: dispatched.append(
                            (operation, dict(arguments), dict(execution))) or {"success": True, "data": {}})
    with pytest.raises(CandidateError, match="matching arguments and execution project_id"):
        adapter.dispatch({"operation": "job.list",
                          "arguments": {"project_id": "project-a", "limit": 500},
                          "execution": {}})
    with pytest.raises(CandidateError, match="matching arguments and execution project_id"):
        adapter.dispatch({"operation": "job.list",
                          "arguments": {"project_id": "project-a", "limit": 500},
                          "execution": {"project_id": "project-b"}})
    assert dispatched == []


@pytest.mark.parametrize("surface", ["sys_path", "path_hook", "meta_finder"])
def test_editable_finder_and_path_hook_are_rejected(surface: str) -> None:
    args: dict[str, Any] = {"sys_path": [], "path_hooks": [], "meta_path": []}
    if surface == "sys_path":
        args["sys_path"] = ["__editable__.comsol_mcp-0.1.9.finder.__path_hook__"]
    elif surface == "path_hook":
        args["path_hooks"] = [types.SimpleNamespace(__module__="__editable__.comsol_finder",
                                                     __name__="path_hook")]
    else:
        args["meta_path"] = [types.SimpleNamespace(__module__="editable_finder",
                                                    __name__="Finder")]
    with pytest.raises(Exception, match="editable Python finder/path hook"):
        _assert_no_editable_fallback(**args)


def test_live_project_module_injection_is_rejected_after_initial_archive_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import comsol_mcp

    archive_root = Path.cwd().resolve()
    assert Path(comsol_mcp.__file__).resolve().is_relative_to(archive_root)
    assert _audit_loaded_project_modules(archive_root)["comsol_mcp"] == str(Path(comsol_mcp.__file__).resolve())
    injected = types.ModuleType("comsol_mcp.late_project_module")
    injected.__file__ = str(tmp_path / "live-checkout" / "comsol_mcp" / "late_project_module.py")
    (tmp_path / "live-checkout" / "comsol_mcp").mkdir(parents=True)
    Path(injected.__file__).write_text("# injected escape fixture\n")
    monkeypatch.setitem(sys.modules, "comsol_mcp.late_project_module", injected)
    with pytest.raises(Exception, match="loaded project module escaped the archive"):
        _audit_loaded_project_modules(archive_root)


def test_late_import_through_live_package_path_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import comsol_mcp

    archive_root = Path.cwd().resolve()
    live_package = tmp_path / "live-checkout" / "comsol_mcp"
    live_package.mkdir(parents=True)
    (live_package / "late_import_escape.py").write_text("ORIGIN = 'live checkout'\n", encoding="utf-8")
    original_path = list(comsol_mcp.__path__)
    monkeypatch.setattr(comsol_mcp, "__path__", [*original_path, str(live_package)])
    importlib.invalidate_caches()
    try:
        escaped = importlib.import_module("comsol_mcp.late_import_escape")
        assert Path(escaped.__file__).resolve() == (live_package / "late_import_escape.py").resolve()
        with pytest.raises(Exception, match="package search path escaped the archive|module escaped the archive"):
            _audit_loaded_project_modules(archive_root)
    finally:
        sys.modules.pop("comsol_mcp.late_import_escape", None)
        monkeypatch.setattr(comsol_mcp, "__path__", original_path)
        importlib.invalidate_caches()


def test_comsol_package_search_path_cannot_include_live_checkout(tmp_path: Any) -> None:
    archive_root = Path.cwd().resolve()
    live_package = tmp_path / "live-checkout" / "comsol_mcp"
    live_package.mkdir(parents=True)
    fake = types.ModuleType("comsol_mcp")
    fake.__file__ = str(archive_root / "comsol_mcp" / "__init__.py")
    fake.__path__ = [str(archive_root / "comsol_mcp"), str(live_package)]
    with pytest.raises(Exception, match="package search path escaped the archive"):
        _audit_loaded_project_modules(archive_root, {"comsol_mcp": fake})


def _run_fresh_control_daemon_public_dispatch_smoke(
    tmp_path: Path, archive_root: Path,
) -> dict[str, Any]:
    """Run the production control-home path in an isolated fresh Python process."""
    home_root = tmp_path / "owned-mcp-home"
    project_root = tmp_path / "owned-projects"
    project_root.mkdir()
    env = dict(os.environ)
    env.update({
        "COMSOL_SERVER_MCP_HOME": str(home_root),
        "COMSOL_PROJECT_ROOT": str(project_root),
        "COMSOL_MCP_TRUSTED_CODE": "1",
        "PYTHONPATH": os.pathsep.join((str(archive_root), str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)))),
    })
    os.environ.update(env)
    if "comsol_mcp._server" in sys.modules:
        raise CandidateError("fresh control-home smoke unexpectedly inherited a cached server module")

    capture: dict[str, Any] = {}
    def on_child(proc: Any, identity: Any, endpoint: Any, stream: Any) -> None:
        capture.update(proc=proc, identity=identity, endpoint=endpoint, stream=stream)

    proc, identity, endpoint, stream = _start_control_daemon(
        tmp_path / "work", tmp_path, None, env, on_child)
    response: dict[str, Any] | None = None
    observed_control_home: str | None = None
    assertions_completed = False
    cleanup: dict[str, Any] | None = None
    launch: dict[str, Any] = {}
    try:
        assert proc.args == [str(EXPECTED_PYTHON), "-S", "-m",
                             "comsol_mcp._control_daemon", "--home",
                             str(home_root / "control-private")]
        launch = json.loads((tmp_path / "control_daemon_launch.json").read_text(encoding="utf-8"))
        archive_manifest = json.loads(
            (archive_root / ".w23_published_archive_manifest.json").read_text(encoding="utf-8"))
        source_binding = _source_inventory(archive_root, archive_manifest["base_commit"])
        assert launch["status"] == "Popen_BIRTH_ENDPOINT_BOUND"
        assert launch["python_no_site_switch"] is True
        assert launch["archive_base_commit"] == source_binding["base_commit"]
        assert launch["archive_source_closure_sha256"] == source_binding["source_closure_sha256"]
        assert launch["control_daemon_source_sha256"] == source_binding["source_files"][
            "comsol_mcp/_control_daemon.py"]["sha256"]
        assert launch["explicit_pythonpath"] == env["PYTHONPATH"]
        assert launch["popen_pid"] == endpoint["pid"]
        assert launch["birth_identity"] == identity
        assert launch["endpoint"]["pid"] == endpoint["pid"]
        assert "token" not in launch["endpoint"]
        assert endpoint["pid"] == proc.pid
        assert endpoint["process_start_epoch_ms"] == identity["start_epoch_ms"]
        assert _process_identity(proc.pid) == identity
        from comsol_mcp._control_client import control_home
        observed_control_home = str(control_home().resolve(strict=True))
        assert Path(observed_control_home) == (home_root / "control-private").resolve(strict=True)
        adapter = PublicDispatchAdapter(tmp_path, expected_control_home=home_root / "control-private",
                                       archive_root=archive_root)
        adapter.bind_owned_control(endpoint, identity)
        response = adapter.dispatch({
            "operation": "session.inspect",
            "arguments": {"session_id": "w23-control-smoke-no-session"},
            "execution": {"project_id": "w23-control-smoke-no-project",
                          "request_id": "w23-control-smoke-request",
                          "idempotency_key": "w23-control-smoke-idempotency",
                          "rpc_timeout_s": 2.0},
        })
        assert isinstance(response, dict)
        assert response.get("success") is False
        assert response.get("error", {}).get("code") != "EXECUTION_STATE_UNKNOWN"
        rows = _lsof_listeners(endpoint["port"])
        assert len(rows) == 1
        assert rows[0]["pid"] == proc.pid
        assert rows[0]["endpoint"] == f"127.0.0.1:{endpoint['port']}"
        assertions_completed = True
    finally:
        try:
            cleanup = _stop_owned_process(proc, identity, port=endpoint["port"])
            assert cleanup["status"] == "STOPPED_AND_REAPED"
        finally:
            if stream is not None:
                stream.close()
            receipt = {
                "schema_version": 1,
                "status": "PASS" if (assertions_completed and isinstance(cleanup, dict)
                                       and cleanup.get("status") == "STOPPED_AND_REAPED") else "FAIL",
                "comsol_engine_started": False,
                "native_scientific_result": "NOT_RUN",
                "control_home_observed_before_public_dispatch": observed_control_home,
                "expected_control_home": str((home_root / "control-private").resolve()),
                "control_daemon_command": list(proc.args),
                "control_daemon_cwd": str(archive_root),
                "archive_base_commit": launch.get("archive_base_commit"),
                "archive_manifest_sha256": launch.get("archive_manifest_sha256"),
                "archive_source_closure_sha256": launch.get("archive_source_closure_sha256"),
                "archive_source_file_count": launch.get("archive_source_file_count"),
                "control_daemon_source_sha256": launch.get("control_daemon_source_sha256"),
                "control_daemon_pythonpath": env["PYTHONPATH"],
                "control_daemon_no_site_switch": "-S" in proc.args,
                "server_module_preloaded_before_owned_launch": False,
                "runner_path_hooks": launch.get("runner_path_hooks"),
                "runner_meta_path_finders": launch.get("runner_meta_path_finders"),
                "editable_fallback_surfaces": launch.get("editable_fallback_surfaces"),
                "popen_pid": proc.pid,
                "popen_birth_identity": identity,
                "endpoint": {key: value for key, value in endpoint.items() if key != "token"},
                "public_dispatch": response,
                "cleanup": cleanup,
            }
            (tmp_path / "no_comsol_control_daemon_smoke_receipt.json").write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8")
    return receipt


def test_real_control_daemon_public_dispatch_binds_exact_popen_without_comsol(
    tmp_path: Any,
) -> None:
    repository = Path.cwd().resolve()
    manifest_path = repository / ".w23_published_archive_manifest.json"
    archive_was_created = not manifest_path.is_file()
    if archive_was_created:
        published = subprocess.run(["git", "rev-parse", "origin/main"], cwd=repository,
                                   capture_output=True, text=True, check=False, timeout=20)
        assert published.returncode == 0, published.stderr
        archive_root = Path("/private/tmp") / f"comsol-mcp-w23-full3d-smoke-{uuid4().hex}"
        archive_export = export_published_archive(
            repo=repository, destination=archive_root, base_commit=published.stdout.strip())
    else:
        # A no-.git candidate archive is already the reviewed source boundary.
        # Reuse it directly so this smoke is runnable from the frozen archive.
        from tools import run_native_w23_full3d_setup as runner
        archive_root = repository
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_binding = _source_inventory(archive_root, manifest["base_commit"])
        assert source_binding["archive_manifest_sha256"] == manifest["manifest_sha256"]
        archive_export = {
            "base_commit": manifest["base_commit"],
            "archive_manifest_sha256": manifest["manifest_sha256"],
            "source_closure_sha256": manifest["source_closure_sha256"],
            "source_files": len(manifest["source_files"]),
            "overlay_files": sorted(runner.OVERLAY_PATHS),
        }
    env = dict(os.environ)
    env.pop("COMSOL_SERVER_MCP_HOME", None)
    env.pop("COMSOL_PROJECT_ROOT", None)
    env.pop("COMSOL_MCP_TRUSTED_CODE", None)
    env["PYTHONPATH"] = str(EXPLICIT_SITE_PACKAGES.resolve(strict=True))
    script = (
        "import importlib.util, pathlib, sys; "
        "test_path=pathlib.Path(sys.argv[1]); "
        "spec=importlib.util.spec_from_file_location('w23_setup_smoke_test_module', test_path); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "module._run_fresh_control_daemon_public_dispatch_smoke("
        "pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))"
    )
    receipt_path = Path(tmp_path) / "no_comsol_control_daemon_smoke_receipt.json"
    try:
        completed = subprocess.run(
            [str(EXPECTED_PYTHON), "-B", "-S", "-c", script,
             str(archive_root / "tests/test_run_native_w23_full3d_setup.py"),
             str(tmp_path), str(archive_root)],
            cwd=archive_root, env=env, capture_output=True, text=True,
            timeout=60, check=False)
        assert completed.returncode == 0, (completed.stdout + "\n" + completed.stderr)[-8000:]
        assert receipt_path.is_file()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["status"] == "PASS", receipt
        assert receipt["comsol_engine_started"] is False
        assert receipt["control_home_observed_before_public_dispatch"] == receipt["expected_control_home"]
        assert receipt["control_daemon_cwd"] == str(archive_root)
        assert receipt["archive_manifest_sha256"]
        manifest = json.loads(
            (archive_root / ".w23_published_archive_manifest.json").read_text(encoding="utf-8"))
        assert archive_export["base_commit"] == receipt["archive_base_commit"]
        assert archive_export["archive_manifest_sha256"] == receipt["archive_manifest_sha256"]
        receipt["archive_origin_main_at_export"] = manifest["published_origin_main_at_export"]
        receipt["archive_source_overlay_files"] = archive_export["overlay_files"]
        receipt["archive_source_overlay_hashes"] = {
            relative: hashlib.sha256((archive_root / relative).read_bytes()).hexdigest()
            for relative in archive_export["overlay_files"]}
        receipt["archive_export_source_closure_sha256"] = archive_export["source_closure_sha256"]
        receipt["archive_export_source_file_count"] = archive_export["source_files"]
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False,
                                           allow_nan=False) + "\n", encoding="utf-8")
        assert receipt["archive_source_overlay_files"]
        assert set(receipt["archive_source_overlay_hashes"]) == set(receipt["archive_source_overlay_files"])
    finally:
        import shutil
        if archive_was_created:
            shutil.rmtree(archive_root, ignore_errors=True)


def test_job_ledger_pages_real_public_control_daemon_sqlite_route(tmp_path: Path) -> None:
    from comsol_mcp._control_daemon import ControlDaemon

    project_id = "w23-public-route-project"
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control-home", project_root=project_root)

    def seed_job(index: int, *, project: str, status: str) -> None:
        record, reused = daemon.store.begin(
            request_id=f"w23-route-request-{index}",
            idempotency_key=f"w23-route-key-{index}",
            request_hash=hashlib.sha256(f"seed-{index}".encode()).hexdigest(),
            operation="w23.route.seed",
            metadata={"operation": "w23.route.seed", "project_id": project,
                      "execution": {"project_id": project}, "engine_dispatched": False},
        )
        assert reused is False
        daemon.store.finish(record["operation_id"], status=status,
                            result={"success": status == "SUCCEEDED", "data": {"engine_dispatched": False}})

    class DirectPublicControlRoute:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(json.loads(json.dumps(request)))
            return daemon.dispatch(request)

    route = DirectPublicControlRoute()
    try:
        for index in range(1007):
            seed_job(index, project=project_id, status="SUCCEEDED")
        seed_job(2000, project="w23-other-project", status="RUNNING")
        assert daemon.backend.worker is None

        ledger = _job_ledger_terminal(route, project_id)
        assert ledger["count"] == 1007
        assert ledger["total_count"] == 1007
        assert ledger["page_count"] == 3
        assert ledger["all_terminal"] is True
        assert ledger["nonterminal"] == []
        assert [request["arguments"]["offset"] for request in route.requests] == [0, 500, 1000]
        assert all(request["arguments"]["limit"] == 500 for request in route.requests)
        assert all(request["arguments"]["project_id"] == project_id
                   and request["execution"]["project_id"] == project_id
                   for request in route.requests)

        seed_job(2001, project=project_id, status="UNKNOWN")
        route.requests.clear()
        nonterminal = _job_ledger_terminal(route, project_id)
        assert nonterminal["count"] == 1008
        assert nonterminal["all_terminal"] is False
        assert len(nonterminal["nonterminal"]) == 1
        assert nonterminal["nonterminal"][0]["status"] == "UNKNOWN"

        action_catalog = json.loads(
            (REPO / "comsol_mcp/data/g2/02_ACTION_CATALOG.json").read_text(encoding="utf-8"))
        job_list = next(item for item in action_catalog["operations"]
                        if item["operation_id"] == "job.list")
        assert job_list["input_schema"]["properties"]["limit"]["maximum"] == 500
        too_large = daemon.dispatch({
            "operation": "job.list",
            "arguments": {"project_id": project_id, "offset": 0, "limit": 501},
            "execution": {"project_id": project_id},
        })
        assert too_large["success"] is False
        assert too_large["error"]["code"] == "INVALID_REQUEST"
    finally:
        daemon.close()


def _native_mesh_readback(*, phase="initial", domains=(2, 9)):
    from copy import deepcopy
    attempts = ["feature_tags_before", "mesh_tag", "geometry_tag", "geometry_dimension", "domain_count", "up_down",
                "generator_tag", "generator_type", "selection_geometry", "selection_dimension", "selection_dimensions",
                "selection_entities", "selection_is_geom_raw", "selection_is_remaining_raw", "size_custom", "size_hmax",
                "size_hmin", "mesh_dimension", "is_empty", "element_count", "is_complete", "has_problems", "problems"]
    o = {"schema": "W23_FULL3D_OWNED_MESH_V1", "status": "NATIVE_MESH_GETTERS_ACCEPTED_NO_SCIENCE_CLAIM",
         "phase": phase, "getter_attempts": attempts, "getter_errors": {},
         "feature_tags_before": ["size"] + ([] if phase == "initial" else ["w23tet"]),
         "selection_action_provenance": ("geom_3_all_called_this_initial_creation" if phase == "initial"
             else "same_owned_generator_initial_geom_3_all_reused_no_selection_repair"),
         "generator_created_in_call": 1 if phase == "initial" else 0,
         "mesh_tag": "mesh3d", "geometry_tag": "geom3d", "geometry_dimension": 3,
         "domain_count": len(domains), "up_down": [list(domains), [0] * len(domains)],
         "domain_ids": list(domains), "generator_tag": "w23tet", "generator_type": "FreeTet",
         "selection_geometry": "geom3d", "selection_dimension": 3, "selection_dimensions": [3],
         "selection_entities": list(domains), "selection_is_geom_raw": False, "selection_is_remaining_raw": True,
         "size_custom": "on", "size_hmax": "lambda0/(5*w23Nlens)", "size_hmin": "lambda0/(12*w23Nlens)",
         "mesh_run_attempted": True, "mesh_run_returned": True, "mesh_dimension": 3, "is_empty": False,
         "element_count": 19, "is_complete": True, "has_problems": False, "problems": []}
    return deepcopy({"mesh": {"tag": "mesh3d", "geometry": "geom3d", "elements": 19,
                             "hmax": o["size_hmax"], "hmin": o["size_hmin"], "observation": o}})


def test_native_mesh_accepts_sparse_actual_ids_and_fresh_case_topology_without_guessing_all_flag():
    from tools.run_native_w23_full3d_setup import validate_native_mesh_readback
    before = validate_native_mesh_readback(_native_mesh_readback(), phase="initial")
    case = _native_mesh_readback(phase="apply_case", domains=(4, 17, 31))
    case["mesh"]["observation"]["selection_entities"] = [31, 4, 17]
    case["mesh"]["observation"]["selection_is_geom_raw"] = True
    case["mesh"]["observation"]["selection_is_remaining_raw"] = False
    proof = validate_native_mesh_readback(case, phase="apply_case", previous=before)
    assert proof["domain_ids"] == [4, 17, 31]
    assert proof["scientific_acceptance"] == "NOT_RUN"


@pytest.mark.parametrize("key,value", [
    ("mesh_tag", "foreign"), ("geometry_tag", "foreign"), ("generator_tag", "foreign"),
    ("generator_type", "FreeTri"), ("selection_geometry", "foreign"),
    ("geometry_dimension", 2), ("selection_dimension", 2), ("mesh_dimension", 2),
    ("geometry_dimension", 3.0), ("selection_dimension", True), ("mesh_dimension", 3.0),
    ("selection_dimensions", [3, 2]), ("selection_dimensions", [3.0]), ("selection_dimensions", []),
    ("domain_count", True), ("domain_count", 2.0), ("domain_count", 3),
    ("domain_ids", [1, 2]), ("domain_ids", [2, 2]), ("domain_ids", [0, 9]), ("domain_ids", [2.0, 9]),
    ("selection_entities", []), ("selection_entities", [2]), ("selection_entities", [2, 9, 12]),
    ("selection_entities", [2, 2, 9]), ("selection_entities", [0, 2, 9]), ("selection_entities", [-1, 2, 9]),
    ("selection_entities", [True, 9]), ("selection_entities", [2.0, 9]),
    ("up_down", None), ("up_down", [[2], [0, 0]]), ("up_down", [[0], [0]]),
    ("up_down", [[-2, 9], [0, 0]]), ("up_down", [[2.0, 9], [0, 0]]),
    ("up_down", [[True, 9], [0, 0]]), ("up_down", [[], []]),
    ("is_empty", True), ("is_empty", 0), ("is_complete", False), ("is_complete", 1),
    ("has_problems", True), ("has_problems", 0), ("problems", ["partial"]), ("problems", None),
    ("element_count", 0), ("element_count", -1), ("element_count", True), ("element_count", 19.0),
    ("selection_is_geom_raw", 0), ("selection_is_remaining_raw", 1),
    ("size_custom", "off"), ("size_hmax", "foreign"), ("size_hmin", "foreign"),
    ("feature_tags_before", ["size", "w23tet"]), ("feature_tags_before", ["size", "size"]),
    ("feature_tags_before", []), ("feature_tags_before", [None]),
    ("generator_created_in_call", True), ("generator_created_in_call", 2),
    ("selection_action_provenance", "all"), ("mesh_run_attempted", False), ("mesh_run_returned", False),
    ("getter_errors", {"is_complete": {"type": "NativeError", "message": "actual error"}}),
    ("getter_attempts", []), ("status", "PASS"), ("phase", "apply_case")])
def test_native_mesh_rejects_tampered_partial_or_ambiguous_actual_getters(key, value):
    from tools.run_native_w23_full3d_setup import validate_native_mesh_readback
    r = _native_mesh_readback(); r["mesh"]["observation"][key] = value
    with pytest.raises(CandidateError, match="native mesh"):
        validate_native_mesh_readback(r, phase="initial")


@pytest.mark.parametrize("mutation", ["outer_count_bool", "outer_count_drift", "outer_size_drift", "no_observation",
                                     "getter_attempt_removed", "run_error", "failure_cause", "apply_create", "apply_no_tag",
                                     "apply_size_changed", "apply_no_prior"])
def test_native_mesh_cross_stage_identity_and_error_observations_fail_closed(mutation):
    from tools.run_native_w23_full3d_setup import validate_native_mesh_readback
    before = validate_native_mesh_readback(_native_mesh_readback(), phase="initial")
    phase = "apply_case" if mutation.startswith("apply_") else "initial"
    r = _native_mesh_readback(phase=phase); o = r["mesh"]["observation"]
    if mutation == "outer_count_bool": r["mesh"]["elements"] = True
    elif mutation == "outer_count_drift": r["mesh"]["elements"] = 20
    elif mutation == "outer_size_drift": r["mesh"]["hmax"] = "other"
    elif mutation == "no_observation": del r["mesh"]["observation"]
    elif mutation == "getter_attempt_removed": o["getter_attempts"].remove("domain_count")
    elif mutation == "run_error": o["mesh_run_error"] = {"type": "NativeError", "message": "partial"}
    elif mutation == "failure_cause": o["failure_cause"] = {"message": "failed"}
    elif mutation == "apply_create": o["generator_created_in_call"] = 1
    elif mutation == "apply_no_tag": o["feature_tags_before"] = ["size"]
    elif mutation == "apply_size_changed":
        o["size_hmax"] = "(lambda0/(5*w23Nlens))*0.8"; o["size_hmin"] = "(lambda0/(12*w23Nlens))*0.8"
    else: before = None
    with pytest.raises(CandidateError, match="native mesh"):
        validate_native_mesh_readback(r, phase=phase, previous=before if phase == "apply_case" else None)


def test_native_mesh_consumer_precedes_existing_bma_dispatch_and_does_not_change_frozen_budgets():
    import ast
    from tools import run_native_w23_full3d_setup as m
    s = Path(m.__file__).read_text(); tree = ast.parse(s)
    execute = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "execute_candidate")
    segment = ast.get_source_segment(s, execute)
    build = segment.index('validate_native_mesh_readback(build_readback, phase="initial")')
    apply = segment.index('apply_mesh_readback = validate_native_mesh_readback(')
    assert build < apply < segment.index('if _is_bma_profile(freeze["campaign_profile"])')
    assert m.SETUP_BUDGET["study_run_calls"] == 0 and m.SETUP_BUDGET["solver_calls"] == 0
    assert m.SETUP_BUDGET["wall_clock_seconds_from_server_birth_including_cleanup"] == 1800
    assert m.SETUP_BUDGET["reserved_cleanup_seconds"] == 120


def _execute_candidate_bad_mesh_software_control(monkeypatch, tmp_path, *, bad_stage):
    """Actual execute/route/binding/validator flow; explicit fake host launch seams only."""
    import copy
    import socket
    from tools import run_native_w23_full3d_setup as m
    from tools import w23_full3d_science as science
    from tools.w23_full3d import canonical_full3d_recipe
    actual_path_class = Path
    root = actual_path_class(m.__file__).resolve().parents[1]
    trace, guards, forbidden = [], [], []
    model_ref = {"schema_version": 1, "session_id": "software-session", "server_instance_id": "software-server",
                 "model_tag": "SoftwareModel", "generation": 1}
    install, jdk, packages = [tmp_path / n for n in ("fake-install", "fake-jdk", "fake-packages")]
    for p in (install, jdk, packages): p.mkdir()
    preflight = {"schema_version": 1, "campaign_profile": "bma_probe", "source": {"checkout_kind": "git_archive"},
        "runtime": {"install_root": str(install), "jdk_home": str(jdk), "comsol_version": "SOFTWARE_FAKE_6.4.0.293",
                    "jdk_version": "SOFTWARE_FAKE_JDK11"},
        "compile": {"classpath_manifest_sha256": "1" * 64, "classpath_jar_count": 1,
                    "classpath_jar_content_fingerprint_sha256": "2" * 64},
        "python_isolation": {"site_processing_disabled": True, "explicit_site_packages": str(packages)},
        "budget": copy.deepcopy(m.BMA_PROBE_BUDGET)}
    assert preflight["budget"] == m.BMA_PROBE_BUDGET  # original legal profile, no budget alteration.
    monkeypatch.chdir(root)
    monkeypatch.setattr(m, "INSTALL_ROOT", install);monkeypatch.setattr(m, "JAVA11", jdk)
    monkeypatch.setattr(m, "EXPECTED_PYTHON", actual_path_class(sys.executable))
    monkeypatch.setattr(m, "EXPLICIT_SITE_PACKAGES", packages)
    monkeypatch.setattr(m, "_configure_archive_python", lambda repo: {"explicit_site_packages": str(packages), "SOFTWARE_HOST_FAKE": True})
    monkeypatch.setattr(m, "verify_candidate", lambda **kw: copy.deepcopy(preflight))
    monkeypatch.setattr(m, "_import_published_runtime_closure", lambda repo: {"SOFTWARE_HOST_FAKE": "no app import"})
    monkeypatch.setattr(m, "_archive_import_audit", lambda repo: {"SOFTWARE_HOST_FAKE": True})
    # Redirect only the hardcoded private work base into the real APFS test temp.
    # Production path/identity/recipe functions elsewhere remain actual.
    def software_path(value):
        return tmp_path if value == "/private/tmp" else actual_path_class(value)
    software_path.cwd = actual_path_class.cwd
    monkeypatch.setattr(m, "Path", software_path)
    for key in ("COMSOL_ROOT", "COMSOL_JAVA_HOME", "JAVA_HOME", "COMSOL_PREFS_DIR", "COMSOL_PROJECT_ROOT",
                "COMSOL_SERVER_MCP_HOME", "COMSOL_MCP_TRUSTED_CODE", "COMSOL_MCP_ISOLATION_RECEIPT",
                "COMSOL_SERVER_VERSION", "PYTHONPATH"):
        monkeypatch.setenv(key, os.environ.get(key, "SOFTWARE_TEST_BEFORE"))
    def deny(*a, **kw):
        forbidden.append("attempted real process/network/host probe");raise AssertionError("real launcher/network/host probe forbidden")
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, deny)
    monkeypatch.setattr(os, "system", deny)
    monkeypatch.setattr(socket, "socket", deny)
    for name in ("_process_identity", "_lsof_listeners", "_lsof_process_listeners", "_stop_owned_process", "_stop_server_adapter"):
        monkeypatch.setattr(m, name, deny)
    def fake_module(name, **members):
        mod = types.ModuleType(name)
        for k,v in members.items(): setattr(mod,k,v)
        monkeypatch.setitem(sys.modules,name,mod)
        return mod
    package = fake_module("comsol_mcp");package.__path__ = []
    class FakeJavaPaths:
        def __init__(self,*a,**kw): trace.append({"host_fake": "JavaWorkerPaths no JVM"})
        def classpath(self): return ("SOFTWARE_FAKE_NO_JAR_LAUNCH", "1"*64, 1, "2"*64)
        def comsol_version_info(self): return "SOFTWARE_FAKE_6.4.0.293"
        def jdk_version_info(self): return "SOFTWARE_FAKE_JDK11"
    fake_module("comsol_mcp._java_worker", JavaWorkerPaths=FakeJavaPaths)
    def control_home():
        home=actual_path_class(os.environ["COMSOL_SERVER_MCP_HOME"])/"control-private"
        home.mkdir(parents=True,exist_ok=True);return home
    fake_module("comsol_mcp._control_client", control_home=control_home)
    fake_module("comsol_mcp._runtime_installation", runtime_id_for_root=lambda p: "SOFTWARE_FAKE_RUNTIME_ID")
    class FakeProc:
        pid = 999991  # synthetic software identity, never queried or signalled.
    class FakeServer:
        def __init__(self,work,evidence,**kw):
            trace.append({"host_fake": "NativeLoopbackServer constructor, no process"})
            self.project=work/"project";self.project.mkdir(parents=True);(self.project/"science").mkdir()
            self.proc=FakeProc();self.port=51234
            self.process_identity={"pid": self.proc.pid, "start_epoch_ms": int(time.time()*1000)}
        def prepare_shadow(self): return {"SOFTWARE_HOST_FAKE": True}
        def start_and_verify_listener(self):
            trace.append({"host_fake": "synthetic listener, no bind"});return {"SOFTWARE_HOST_FAKE": True}
    fake_module("tools.run_native_resume_smoke", NativeLoopbackServer=FakeServer)
    def start_daemon(work,evidence,server,env,capture):
        proc=types.SimpleNamespace(pid=999992)
        ident={"pid":proc.pid,"start_epoch_ms":int(time.time()*1000)}
        endpoint={"pid":proc.pid,"port":51235,"process_start_epoch_ms":ident["start_epoch_ms"],"token":"SOFTWARE_FAKE"}
        capture(proc,ident,endpoint,None);trace.append({"host_fake":"control daemon no process"})
        return proc,ident,endpoint,None
    monkeypatch.setattr(m,"_start_control_daemon",start_daemon)
    monkeypatch.setattr(m,"_bind_native_server_identity",lambda server, listener:dict(server.process_identity))
    monkeypatch.setattr(m,"_resource_ownership_receipt",lambda **kw:{"SOFTWARE_HOST_FAKE_NO_RESOURCES":True})
    # No real resources exist. Retain the original guard exception without invoking retirement/signals.
    monkeypatch.setattr(m,"_job_ledger_terminal",lambda *a:{"all_terminal":False,"count":1,"jobs":[{"status":"RUNNING_SOFTWARE_FAKE"}]})
    def java_response(request, phase):
        args=request["arguments"]["arguments"]["arguments"]
        readback=_native_mesh_readback(phase="initial" if phase=="build" else "apply_case")
        readback.update({"fixture_id":args["fixture_id"],"recipe_sha256":args["recipe_sha256"],
            "managed_identity":copy.deepcopy(args["managed_identity"]),"native_result":"NOT_RUN",
            "study_or_solver_invoked":False,"status":"BUILT_CONFIGURED_NOT_SOLVED" if phase=="build" else "GEOMETRY_CASE_CONFIGURED_NOT_SOLVED"})
        if phase=="build":
            readback.update(_native_build_readback())
            if bad_stage=="build_incomplete": readback["mesh"]["observation"]["is_complete"]=False
        else:
            case=args["case"]
            readback.update({out:case[key] for out,key in [("case_id","case_id"),("case_identity_sha256","case_identity_sha256"),
                ("experiment_id","experiment_id"),("factor","factor"),("factor_value","value")]})
            if bad_stage=="apply_wrong_domain": readback["mesh"]["observation"]["selection_entities"]=[2,17]
        return {"success":True,"execution":{"model_ref":copy.deepcopy(model_ref),"revision":2 if phase=="build" else 3},
            "data":{"worker":{"ok":True,"status":"SUCCEEDED","result":{"readback":readback}},
                    "readback":{"executed":True,"readback":readback}}}
    class FakeAdapter:
        def __init__(self,*a,**kw): trace.append({"host_fake":"PublicDispatchAdapter no HTTP"})
        def bind_owned_control(self,*a): trace.append({"host_fake":"synthetic owned control binding"})
        def dispatch(self,request):
            op=request["operation"]
            phase=(request.get("arguments",{}).get("arguments",{}).get("arguments",{}).get("phase")
                   if op=="operation_call" else None)
            trace.append({"operation":op,"phase":phase,"request":copy.deepcopy(request)})
            if op=="project.create":
                # Same actual containment/permission binder runs on this persisted-shaped response.
                project=next(x for x in tmp_path.rglob("project/science"))
                return {"success":True,"data":{"project":{"project_id":"software-project","workspace":str(project),
                    "schema_version":1,"revision":0,"policy":{"permissions":["inspect","project_write","compute","trusted_code"]}}}}
            if op=="session.connect":return {"success":True,"data":{"project_id":"software-project",
                "session_id":"software-session","server_instance_id":"software-server","worker_instance_id":"software-worker",
                "worker_epoch":1,"endpoint":{"host":"127.0.0.1","port":51234},"observed_peer":{"address":"127.0.0.1","port":51234},
                "remote_engine_version":"SOFTWARE_FAKE_6.4","remote_engine_build":"SOFTWARE_FAKE_293"}}
            if op=="session.inspect":return {"success":True,"data":{"runtime_live":True,
                "lifecycle":{"state":"CONNECTED","worker_instance_id":"software-worker","worker_epoch":1},
                "worker_binding":{"worker_instance_id":"software-worker","worker_epoch":1}}}
            if op=="model_create":return {"success":True,"execution":{"model_ref":copy.deepcopy(model_ref),"revision":1}}
            if op=="operation_call" and phase in {"build","apply_case"}:return java_response(request,phase)
            raise AssertionError("unexpected downstream route was attempted: "+op+" phase="+str(phase))
    monkeypatch.setattr(m,"PublicDispatchAdapter",FakeAdapter)
    actual_validator=m.validate_native_mesh_readback
    def observed_actual_validator(readback,**kw):
        record={"phase":kw["phase"],"actual_input":copy.deepcopy(readback),"actual_validator_called":True}
        guards.append(record)
        try:
            result=actual_validator(readback,**kw);record["returned"]=copy.deepcopy(result);return result
        except CandidateError as exc:
            record["actual_error"]={"type":type(exc).__name__,"message":str(exc)};raise
    monkeypatch.setattr(m,"validate_native_mesh_readback",observed_actual_validator)
    result=None
    try:
        result=m.execute_candidate(repo=root,evidence=tmp_path/"software-evidence",reviewed_sha256="a"*64,
                                   install_root=install,jdk_home=jdk)
    finally:
        receipt={"scope":"SOFTWARE_FUNCTIONAL_ACTUAL_EXECUTE_AND_VALIDATOR_WITH_FAKE_HOST_PREFLIGHT_AND_ROUTES_NOT_NATIVE",
            "bad_stage":bad_stage,"original_legal_profile_budget":preflight,"actual_target_guard_observations":guards,
            "actual_route_trace":trace,"actual_execute_result":result,"forbidden_real_launches":forbidden,
            "actual_production_sources":{"execute":str(actual_path_class(m.__file__).resolve()),
                "science_bindings_and_dispatch":str(actual_path_class(science.__file__).resolve())}}
        (tmp_path/"functional-mesh-control-result.json").write_text(json.dumps(receipt,indent=2,sort_keys=True))
    assert result is not None and result["error"]["type"]=="CandidateError",result
    expected="run/empty/completeness/problems/positive element count invalid" if bad_stage=="build_incomplete" else "FreeTet does not cover exact actual full geometry domain set"
    assert result["error"]["message"]=="full3D native mesh: "+expected
    assert len(guards)==(1 if bad_stage=="build_incomplete" else 2)
    assert guards[-1]["actual_error"]==result["error"]
    if bad_stage=="apply_wrong_domain":assert "returned" in guards[0]
    actual_routes=[r for r in trace if "operation" in r]
    expected_phases=["build"] if bad_stage=="build_incomplete" else ["build","apply_case"]
    assert [r["phase"] for r in actual_routes if r["operation"]=="operation_call"]==expected_phases
    assert not any(r["operation"] in {"study.run","run_study","solver.run","runAll"}
                   or (r["phase"] and ("bma" in r["phase"].lower() or "run" in r["phase"].lower())) for r in actual_routes)
    assert result["solver_call_attempts"]==result["solver_calls"]==result["study_run_calls"]==0
    assert forbidden==[]


def test_execute_candidate_incomplete_build_mesh_blocks_all_bma_study_solver_dispatch(monkeypatch,tmp_path):
    _execute_candidate_bad_mesh_software_control(monkeypatch,tmp_path,bad_stage="build_incomplete")


def test_execute_candidate_wrong_apply_domain_blocks_all_bma_study_solver_dispatch(monkeypatch,tmp_path):
    _execute_candidate_bad_mesh_software_control(monkeypatch,tmp_path,bad_stage="apply_wrong_domain")
