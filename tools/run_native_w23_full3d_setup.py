#!/usr/bin/env python3
"""Prepare and execute the reviewed W23 full-3D managed candidate.

``prepare`` performs source/runtime inventory and offline Java compilation; it
never starts COMSOL. ``execute`` refuses to start any process unless the exact
candidate digest was separately reviewed and supplied on the command line.
The ``setup_only`` profile configures and reads the owned fixture without a
solve. The ``bma_probe`` profile also creates a separate single-step receiver
Port-2 BMA study and runs only its full generated SolverSequence. Both profiles
save an MPH and retire only the exact managed Worker before stopping the
task-owned loopback server; neither submits Study.run.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
FIXTURE = REPO / "tools/java/NativeW23Full3DFixture.java"
EVIDENCE_PREFIX = "/private/tmp/comsol-mcp-w23-full3d-"
MAX_WALL_S = 1800
CLEANUP_RESERVE_S = 120
TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "EXPIRED", "LOST", "CANCELLED"}
JOB_LIST_PAGE_LIMIT = 500
MAX_JOB_LEDGER_ROWS = 10000
EXPECTED_PYTHON = Path("/private/tmp/comsol-mcp-w25-py312-20260926T2155Z/bin/python")
EXPLICIT_SITE_PACKAGES = (EXPECTED_PYTHON.parent.parent / "lib" /
                          f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")

# Hash the whole Python package plus the exact published implementation and
# test-support dependencies. Candidate-specific science, fixture, and test
# changes are explicit overlays; unrelated worktree changes stay outside.
OVERLAY_PATHS = {
    "tools/run_native_w23_full3d_setup.py",
    "tests/test_run_native_w23_full3d_setup.py",
    "tools/java/NativeW23Full3DFixture.java",
    "tools/w23_full3d_science.py",
    "tests/test_w23_full3d_science.py",
    "tests/test_w23_full3d.py",
    "tests/test_w23_mode_basis_v2.py",
}
EXTRA_CLOSURE_PATHS = {
    "tools/run_native_resume_smoke.py",
    "tools/w23_full3d.py",
    "tools/w23_full3d_science.py",
    "tools/w23_mode_basis_v2.py",
    "tools/w23_mode_basis_v2_native_plan.py",
    "tools/java/NativeW23Full3DFixture.java",
    "tests/test_w23_mode_basis_v2_results.py",
    "tests/test_w23_mode_basis_v2_native_plan.py",
    "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
}
TEST_SUPPORT_PATHS = {
    "tests/test_w23_mode_basis_v2_results.py",
    "tests/test_w23_mode_basis_v2_native_plan.py",
}

SETUP_BUDGET = {
    "schema_version": 1,
    "max_server_processes": 1,
    "max_managed_workers": 1,
    "max_gui_processes": 0,
    "wall_clock_seconds_from_server_birth_including_cleanup": MAX_WALL_S,
    "reserved_cleanup_seconds": CLEANUP_RESERVE_S,
    "study_run_calls": 0,
    "solver_calls": 0,
    "route_wait_caps_seconds": {
        "session.connect": 120,
        "model_create": 120,
        "fixture_build": 540,
        "baseline_apply": 540,
        "solution_inventory": 90,
        "model_save": 90,
        "session.disconnect.retire_worker": 90,
    },
    "unknown_policy": "preserve exact owned process handles and evidence; never replay or force-stop an unknown Worker/session",
}

NATIVE_READBACK_API_EVIDENCE = {
    "api": "com.comsol.model.PropFeature.getInt(String)",
    "purpose": "read back BMA neigs as an integer property",
    "manual": "COMSOL 6.4 PropFeature",
    "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html",
    "chunk_id": 23017,
    "source_sha256": "befb8cc1c0e3df1ce74d2fa2cd1377e73d378e5c26170558e9f40dc255cc3bc3",
}

PLANNED_ROUTE_ALLOWLIST = [
    "project.create",
    "session.connect",
    "session.inspect",
    "model_create",
    "operation_call:code.execute_java:build",
    "operation_call:code.execute_java:apply_case(baseline)",
    "operation_call:code.execute_java:solution_inventory",
    "operation_call:code.execute_java:save",
    "job.list",
    "session.disconnect(retire_worker=true)",
    "session.inspect",
]

BMA_PROBE_BUDGET = {
    **SETUP_BUDGET,
    "profile": "w23_single_receiver_bma_output_probe_v1",
    "wall_clock_seconds_from_server_birth_including_cleanup": 2700,
    "unallocated_margin_seconds": 300,
    "solver_calls": 1,
    "route_wait_caps_seconds": {
        **SETUP_BUDGET["route_wait_caps_seconds"],
        "bma_probe_prepare": 90,
        "bma_probe_solver_sequence_runAll": 600,
    },
}
BMA_PROBE_ROUTE_ALLOWLIST = [
    *PLANNED_ROUTE_ALLOWLIST[:6],
    "operation_call:code.execute_java:prepare_bma_output_probe",
    "operation_call:code.execute_java:run_bma_output_probe:SolverSequence.runAll",
    *PLANNED_ROUTE_ALLOWLIST[6:],
]
BMA_MAPPING_PROBE_BUDGET = {
    **BMA_PROBE_BUDGET,
    "profile": "w23_single_receiver_bma_basis_field_mapping_probe_v1",
    "wall_clock_seconds_from_server_birth_including_cleanup": 3240,
    "route_wait_caps_seconds": {
        **BMA_PROBE_BUDGET["route_wait_caps_seconds"],
        "bma_basis_dataset_list": 90,
        "bma_basis_dataset_solution_indices": 90,
        "bma_basis_fields_ordinal1": 180,
        "bma_basis_fields_ordinal2": 180,
    },
}
BMA_MAPPING_PROBE_ROUTE_ALLOWLIST = [
    *BMA_PROBE_ROUTE_ALLOWLIST[:8],
    "operation_call:dataset.list:bma_basis_dataset_list",
    "operation_call:dataset.solution_indices:bma_basis_dataset_solution_indices",
    "operation_call:code.execute_java:bma_basis_fields_ordinal1",
    "operation_call:code.execute_java:bma_basis_fields_ordinal2",
    *BMA_PROBE_ROUTE_ALLOWLIST[8:],
]
BMA_MAPPING_PROBE_API_EVIDENCE = [
    {"api": "Interp.setInterpolationCoordinates(double[][]), getCoordinates(), getData(), getImagData(), isComplex()",
     "claim": "evaluate and read back paired field groups at the exact shared global coordinate grid",
     "manual": "COMSOL 6.4 Interp Object and Methods",
     "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_results.52.082.html",
     "chunk_id": 17293,
     "source_sha256": "156412ab29631518953b57d6948b88a0181bf5312984b1a0f9e840a6ad848421"},
]
BMA_PRODUCER_API_EVIDENCE = [
    {"api": "Study.createAutoSequences(String)",
     "claim": "generate an attached solver sequence with default solver settings; type sol selects solver sequences",
     "manual": "COMSOL 6.4 Study Object and Methods",
     "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model/Study.html",
     "chunk_id": 23089, "source_sha256": "7ab71c87a119f01583dbf0441ec9b032e257c4125aa9994b9d83f72571236546"},
    {"api": "SolverSequence.runAll() and SolverSequence.isEmpty()",
     "claim": "run the complete solver sequence and directly test whether its associated solutions are empty",
     "manual": "COMSOL 6.4 SolverSequence Object and Methods",
     "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model/SolverSequence.html",
     "chunk_id": 23079, "source_sha256": "8fcefe8e2f2171858f49fe53ad6210d4231a6a28651766db67effc39c327a2c5"},
    {"api": "SolutionInfo.getOuterSolnum(), getSolnum(int, boolean), getSolverSequence(int)",
     "claim": "read outer solution indices, strict one-based inner solution indices, and the solver sequence associated with each outer index",
     "manual": "COMSOL 6.4 SolutionInfo Object and Methods",
     "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model/SolutionInfo.html",
     "chunk_id": 23067, "source_sha256": "94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb"},
]


def _is_bma_profile(campaign_profile: str) -> bool:
    return campaign_profile in {"bma_probe", "bma_mapping_probe"}


def _profile_budget_and_routes(campaign_profile: str) -> tuple[dict[str, Any], list[str]]:
    if campaign_profile == "bma_probe":
        return BMA_PROBE_BUDGET, BMA_PROBE_ROUTE_ALLOWLIST
    if campaign_profile == "bma_mapping_probe":
        return BMA_MAPPING_PROBE_BUDGET, BMA_MAPPING_PROBE_ROUTE_ALLOWLIST
    return SETUP_BUDGET, PLANNED_ROUTE_ALLOWLIST


class CandidateError(RuntimeError):
    pass


class CleanupRefused(RuntimeError):
    """Fail-closed cleanup stopped at the first missing proof."""

    def __init__(self, stage: str, message: str, completed: list[str]):
        super().__init__(f"cleanup refused at {stage}: {message}")
        self.stage = stage
        self.completed = list(completed)


class RouteBudgetRefused(CandidateError):
    """The wall/cleanup guard rejected a request before public dispatch."""

    dispatch_started = False


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finalize_science_counters(result: dict[str, Any], campaign_profile: str) -> None:
    """Keep science accounting consistent with observed dispatch and readback."""
    result["study_run_calls"] = 0
    result["numeric_port_mode_field_mapping"] = "UNVERIFIED"
    if campaign_profile == "setup_only":
        result["solver_calls"] = 0
        result["native_scientific_result"] = "NOT_RUN"
        result["mode_producer_lineage"] = "UNVERIFIED"
        result["field_sample_calls"] = 0
        result["field_sample_attempts"] = 0
        result["field_sample_readbacks_validated"] = 0
        result["field_sampling_status"] = "NOT_RUN"
        return
    if campaign_profile not in {"bma_probe", "bma_mapping_probe"}:
        raise CandidateError("unsupported campaign profile during result finalization")
    attempts = result.get("solver_call_attempts", 0)
    if type(attempts) is not int or attempts < 0 or attempts > 1:
        raise CandidateError("BMA probe solver attempt count is invalid")
    if attempts == 0:
        result["solver_calls"] = 0
        result["native_scientific_result"] = "NOT_RUN"
        result["mode_producer_lineage"] = "UNVERIFIED"
    elif result.get("solver_calls") == 0:
        result["solver_calls"] = "UNKNOWN"
    if attempts > 0 and result.get("native_scientific_result") == "NOT_RUN":
        result["native_scientific_result"] = "UNKNOWN"
    if attempts > 0:
        result.setdefault("mode_producer_lineage", "UNVERIFIED")
    if campaign_profile == "bma_probe":
        result["field_sample_calls"] = 0
        result["field_sample_attempts"] = 0
        result["field_sample_readbacks_validated"] = 0
        result["field_sampling_status"] = "NOT_IN_PROFILE"
        return
    field_attempts = result.get("field_sample_attempts", 0)
    field_calls = result.get("field_sample_calls", 0)
    field_readbacks = result.get("field_sample_readbacks_validated", 0)
    if (type(field_attempts) is not int or field_attempts < 0 or field_attempts > 2
            or type(field_calls) is not int or field_calls < 0 or field_calls > field_attempts):
        raise CandidateError("paired field sample attempt/completion counts are invalid")
    if type(field_readbacks) is not int or field_readbacks < 0 or field_readbacks > field_calls:
        raise CandidateError("paired field raw-readback validation count is invalid")
    if field_attempts == 0:
        result["field_sampling_status"] = "NOT_RUN"
    elif field_calls < field_attempts or field_readbacks < 2:
        result["field_sampling_status"] = "UNKNOWN_OR_PARTIAL"
    else:
        result["field_sampling_status"] = "TWO_RAW_SAMPLES_RETURNED_MAPPING_UNVERIFIED"
    result["numeric_port_mode_field_mapping"] = "UNVERIFIED"


def _bma_run_failure_evidence(request: Mapping[str, Any], error: BaseException) -> dict[str, Any]:
    observed = getattr(error, "response", None)
    if observed is not None and not isinstance(observed, Mapping):
        observed = {"non_object_response_repr": repr(observed)}
    elif isinstance(observed, Mapping):
        observed = dict(observed)
    return {
        "request": dict(request),
        "outcome": getattr(error, "outcome", "UNKNOWN"),
        "job_id": getattr(error, "job_id", None),
        "retry_forbidden": getattr(error, "retry_forbidden", True),
        "observed_response": observed,
    }


def _execute_bma_basis_mapping_stage(
    route: Callable[[Mapping[str, Any], str, int], dict[str, Any]], *,
    result: dict[str, Any], project_id: str, model: Mapping[str, Any],
    source_artifact: str, preparation: Mapping[str, Any],
    producer_evidence: Mapping[str, Any], producer_route_evidence: Mapping[str, Any],
    apply_readback: Mapping[str, Any],
    baseline_case: Mapping[str, Any], staged_source_proof: Mapping[str, Any],
    execution_owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Read both exact SolutionInfo tuples and sample their paired native fields once."""
    from tools.w23_full3d_science import (
        _java_action_readback,
        _updated_full3d_model_state, build_full3d_bma_basis_mapping_contracts,
        build_full3d_bma_basis_mapping_dispatch, build_full3d_dataset_indices_dispatch,
        build_full3d_dataset_list_dispatch, circular_port_quadrature,
        resolve_full3d_bma_basis_sources, resolve_full3d_bma_receiver_plane,
        validate_full3d_bma_mapping_route_result,
        validate_full3d_bma_basis_mapping_samples,
        validate_full3d_sampling_cohort_revision_chain,
    )

    stage = result.setdefault("bma_basis_mapping", {
        "status": "RUNNING_BMA_BASIS_MAPPING_PROBE",
        "field_mapping_status": "UNVERIFIED",
        "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
        "routes": {},
    })
    result.setdefault("field_sample_attempts", 0)
    result.setdefault("field_sample_calls", 0)
    result.setdefault("field_sample_readbacks_validated", 0)
    model_state = dict(model)
    starting_revision = model_state.get("revision")

    def call(label: str, cap: int, request: Mapping[str, Any], *,
             expected_revision_delta: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        route_record = {"request": copy.deepcopy(dict(request)),
                        "max_execution_timeout_s": cap,
                        "status": "PREPARING_PUBLIC_DISPATCH"}
        stage["routes"][label] = route_record
        try:
            observed = route(request, label, cap)
        except BaseException as exc:
            route_record["dispatch_failure"] = _bma_run_failure_evidence(request, exc)
            route_record["outcome"] = getattr(exc, "outcome", "UNKNOWN")
            route_record["status"] = ("PREFLIGHT_REFUSED_NOT_DISPATCHED"
                                       if getattr(exc, "dispatch_started", None) is False
                                       else "PUBLIC_DISPATCH_FAILED_OR_UNKNOWN")
            raise
        if (label in {"bma_basis_fields_ordinal1", "bma_basis_fields_ordinal2"}
                and observed.get("outcome") == "SUCCEEDED"):
            result["field_sample_calls"] = result.get("field_sample_calls", 0) + 1
        bound = validate_full3d_bma_mapping_route_result(
            request, observed, expected_revision_delta=expected_revision_delta,
            max_execution_timeout_s=cap)
        response = observed.get("response")
        if not isinstance(response, Mapping):
            raise CandidateError(f"{label} terminal public response is not an object")
        route_record.update({"status": "TERMINAL_ROUTE_VERIFIED",
                             "route_result": copy.deepcopy(observed),
                             "validated_binding": bound})
        data = response.get("data")
        if label in {"bma_basis_fields_ordinal1", "bma_basis_fields_ordinal2"}:
            route_record["readback"] = copy.deepcopy(
                _java_action_readback(response, f"{label} native Interp readback"))
        elif isinstance(data, Mapping):
            route_record["readback"] = copy.deepcopy(dict(data))
        else:
            raise CandidateError(f"{label} terminal response omitted its readback data object")
        return dict(observed), dict(response), bound

    def response_data(response: Mapping[str, Any], label: str) -> dict[str, Any]:
        data = response.get("data")
        if response.get("success") is not True or not isinstance(data, Mapping):
            raise CandidateError(f"{label} successful public result omitted its data object")
        return dict(data)

    model_ref = model_state.get("model_ref")
    model_tag = model_state.get("model_tag")
    revision = model_state.get("revision")
    if not isinstance(model_ref, Mapping) or not isinstance(model_tag, str) or type(revision) is not int:
        raise CandidateError("paired field route has no authoritative current managed ModelRef/revision")

    request_id, key = _new_ids("bma-basis-dataset-list")
    list_request = build_full3d_dataset_list_dispatch(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=key)
    list_result, list_response, _ = call(
        "bma_basis_dataset_list", 90, list_request, expected_revision_delta=0)
    dataset_data = response_data(list_response, "dataset.list")
    dataset_rows = dataset_data.get("datasets")
    tags = [row.get("tag") for row in dataset_rows if isinstance(row, Mapping)] \
        if isinstance(dataset_rows, list) else None
    if (not isinstance(dataset_rows, list) or not isinstance(tags, list)
            or len(tags) != len(dataset_rows) or any(not isinstance(tag, str) or not tag for tag in tags)
            or len(set(tags)) != len(tags) or dataset_data.get("count") != len(dataset_rows)
            or dataset_data.get("tags") != tags or dataset_data.get("read_errors") != []):
        raise CandidateError("public dataset.list did not return a complete, unique, error-free inventory")
    sequence_tag = producer_evidence.get("solver_sequence_tag")
    bound_datasets = [row for row in dataset_rows if isinstance(row, Mapping)
                      and row.get("type_id") == "Solution" and row.get("solution") == sequence_tag]
    if len(bound_datasets) != 1:
        raise CandidateError("BMA producer must bind exactly one live Solution dataset before index inspection")
    dataset_tag = bound_datasets[0].get("tag")
    if not isinstance(dataset_tag, str) or not dataset_tag:
        raise CandidateError("unique BMA Solution dataset omitted its native tag")
    stage["dataset_list"] = {"request": list_request, "route": list_result,
                             "readback": dataset_data,
                             "producer_solution_dataset": dict(bound_datasets[0])}

    request_id, key = _new_ids("bma-basis-solution-indices")
    index_request = build_full3d_dataset_indices_dispatch(
        dataset_tag, project_id=project_id, model_ref=model_ref, model_tag=model_tag,
        revision=revision, request_id=request_id, idempotency_key=key)
    index_result, index_response, _ = call(
        "bma_basis_dataset_solution_indices", 90, index_request,
        expected_revision_delta=0)
    index_data = response_data(index_response, "dataset.solution_indices")
    stage["dataset_solution_indices"] = {"request": index_request,
                                          "route": index_result,
                                          "readback": index_data}
    basis_binding = resolve_full3d_bma_basis_sources(
        dataset_rows, {dataset_tag: index_data}, producer_evidence=producer_evidence)
    plane = resolve_full3d_bma_receiver_plane(
        apply_readback, preparation=preparation, baseline_case=baseline_case)
    stage["basis_binding"] = basis_binding
    stage["receiver_plane"] = plane
    contracts = build_full3d_bma_basis_mapping_contracts(
        case=baseline_case, preparation=preparation, producer_evidence=producer_evidence,
        basis_binding=basis_binding, plane=plane, radial_intervals=32, angular_points=64)
    quadratures = [circular_port_quadrature(
        contract["plane"]["center_xyz_m"], contract["plane"]["axis_xyz"],
        contract["plane"]["sample_radius_m"], radial_intervals=32, angular_points=64)
        for contract in contracts]
    stage["contracts"] = copy.deepcopy(contracts)
    stage["quadrature_bindings"] = [{key: value for key, value in quadrature.items()
                                      if key != "coordinates_m"}
                                     for quadrature in quadratures]

    raw_readbacks: list[dict[str, Any]] = []
    for ordinal, (contract, quadrature) in enumerate(zip(contracts, quadratures), start=1):
        model_ref = model_state.get("model_ref")
        model_tag = model_state.get("model_tag")
        revision = model_state.get("revision")
        request_id, key = _new_ids(f"bma-basis-fields-{ordinal}")
        request = build_full3d_bma_basis_mapping_dispatch(
            contract, source_artifact=source_artifact, quadrature=quadrature,
            project_id=project_id, model_ref=model_ref, model_tag=model_tag,
            revision=revision, request_id=request_id, idempotency_key=key)
        label = f"bma_basis_fields_ordinal{ordinal}"
        result["field_sample_attempts"] = result.get("field_sample_attempts", 0) + 1
        operation, response, binding = call(label, 180, request,
                                             expected_revision_delta=1)
        model_state = _updated_full3d_model_state(response, prior_model=model_state)
        raw = _java_action_readback(response, f"{label} native Interp readback")
        raw_readbacks.append(raw)
        stage.setdefault("field_samples", []).append({
            "basis_ordinal": ordinal, "request": request, "route": operation,
            "validated_binding": binding, "readback": raw,
            "managed_model_revision_after": model_state["revision"],
        })

    diagnostics = validate_full3d_bma_basis_mapping_samples(
        contracts, raw_readbacks, quadratures)
    route_rows = [{"label": label, **row} for label, row in stage["routes"].items()]
    sample_rows = [{"label": f"bma_basis_fields_ordinal{ordinal}",
                    "contract": contracts[ordinal - 1],
                    "quadrature": quadratures[ordinal - 1],
                    "readback": raw_readbacks[ordinal - 1]}
                   for ordinal in (1, 2)]
    cohort_chain = validate_full3d_sampling_cohort_revision_chain(
        route_rows, sample_rows, project_id=project_id, model_ref=model["model_ref"],
        start_revision=starting_revision, source_artifact=source_artifact,
        staged_source_proof=staged_source_proof, execution_owner=execution_owner,
        producer_evidence=producer_evidence,
        producer_route_evidence=producer_route_evidence)
    result["field_sample_readbacks_validated"] = \
        result.get("field_sample_readbacks_validated", 0) + len(raw_readbacks)
    stage.update({"status": "TWO_RAW_BMA_BASIS_SAMPLES_VALIDATED_MAPPING_UNVERIFIED",
                  "diagnostics": diagnostics,
                  "source_cohort_revision_chain": cohort_chain,
                  "field_mapping_status": "UNVERIFIED",
                  "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
                  "numeric_port_mode_field_mapping": "UNVERIFIED",
                  "mapping_policy": diagnostics["policy"],
                  "native_result": "COMSOL_NATIVE_PAIRED_FIELD_READBACK_NOT_MAPPING_ACCEPTANCE",
                  "study_or_solver_invoked_by_sampling": False,
                  "raw_array_units": {"E": "V/m", "H": "A/m", "normal": "1"},
                  "source": "public dataset.list + dataset.solution_indices + exact paired Interp jobs"})
    return {"model": model_state, "evidence": stage}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity_name(value: Any) -> str:
    module = getattr(value, "__module__", type(value).__module__)
    name = getattr(value, "__qualname__", getattr(value, "__name__", type(value).__qualname__))
    return f"{module}.{name}"


def _editable_fallback_surfaces(*, sys_path: list[str] | None = None,
                                path_hooks: list[Any] | None = None,
                                meta_path: list[Any] | None = None) -> list[str]:
    paths = list(sys.path if sys_path is None else sys_path)
    hooks = list(sys.path_hooks if path_hooks is None else path_hooks)
    finders = list(sys.meta_path if meta_path is None else meta_path)
    return [item for item in [*(str(value) for value in paths),
                              *(_identity_name(value) for value in hooks),
                              *(_identity_name(value) for value in finders)]
            if "editable" in item.lower()]


def _assert_no_editable_fallback(*, sys_path: list[str] | None = None,
                                 path_hooks: list[Any] | None = None,
                                 meta_path: list[Any] | None = None) -> list[str]:
    surfaces = _editable_fallback_surfaces(sys_path=sys_path, path_hooks=path_hooks,
                                           meta_path=meta_path)
    if surfaces:
        raise CandidateError("editable Python finder/path hook is present; run the candidate with -S")
    return surfaces


def _configure_archive_python(repo: Path) -> dict[str, Any]:
    """Bind project imports to the archive without executing venv .pth files."""
    if not getattr(sys.flags, "no_site", False) or "site" in sys.modules:
        raise CandidateError("isolated candidate Python must start with -S; site/.pth processing is forbidden")
    if sys.version_info[:2] != (3, 12):
        raise CandidateError(f"isolated candidate Python must be 3.12, observed {sys.version.split()[0]}")
    root = repo.resolve(strict=True)
    runner_path = Path(__file__).resolve(strict=True)
    if not runner_path.is_relative_to(root):
        raise CandidateError("candidate runner itself is not loaded from the isolated archive")
    site_packages = EXPLICIT_SITE_PACKAGES.resolve(strict=True)
    if not site_packages.is_dir():
        raise CandidateError("the exact venv site-packages directory is unavailable")
    _assert_no_editable_fallback()
    normalized = []
    for entry in sys.path:
        resolved = str(Path(entry or Path.cwd()).resolve())
        if resolved != str(root):
            normalized.append(entry)
    sys.path[:] = [str(root), *normalized]
    if str(site_packages) not in sys.path:
        # Add the explicit venv package directory directly. Under -S, this
        # does not execute .pth files or install their path hooks.
        sys.path.append(str(site_packages))
    for entry in sys.path:
        search_root = Path(entry or Path.cwd()).resolve()
        if search_root != root and (search_root / "comsol_mcp").exists():
            raise CandidateError(f"another comsol_mcp package path is visible outside the archive: {search_root}")
    _assert_no_editable_fallback()
    sys.dont_write_bytecode = True
    return {"site_processing_disabled": True,
            "python_no_site_flag": bool(sys.flags.no_site),
            "runner_path": str(runner_path),
            "explicit_site_packages": str(site_packages),
            "archive_root_precedence": str(root),
            "bytecode_writes_disabled": sys.dont_write_bytecode,
            "path_hooks": [_identity_name(item) for item in sys.path_hooks],
            "meta_path_finders": [_identity_name(item) for item in sys.meta_path]}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True,
                            capture_output=True, check=False, timeout=20)
    if result.returncode != 0:
        raise CandidateError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _closure_paths(repo: Path) -> list[str]:
    paths = {path.relative_to(repo).as_posix()
             for path in (repo / "comsol_mcp").rglob("*.py")
             if path.is_file() and not path.name.startswith(".")}
    paths.update(path.relative_to(repo).as_posix()
                 for path in (repo / "comsol_mcp").rglob("*.java")
                 if path.is_file() and not path.name.startswith("."))
    paths.update(path.relative_to(repo).as_posix()
                 for path in (repo / "comsol_mcp/data/g2").glob("*.json")
                 if path.is_file() and not path.name.startswith("."))
    paths.update(EXTRA_CLOSURE_PATHS)
    paths.update(OVERLAY_PATHS)
    missing = sorted(relative for relative in paths if not (repo / relative).is_file())
    if missing:
        raise CandidateError("W23 managed setup source closure is incomplete: " + ", ".join(missing))
    return sorted(paths)


def _tracked_closure_paths(repo: Path, base_commit: str) -> list[str]:
    tracked = set(_git(repo, "ls-tree", "-r", "--name-only", base_commit).splitlines())
    paths = {relative for relative in tracked
             if (relative.startswith("comsol_mcp/") and
                 (relative.endswith(".py") or relative.endswith(".java") or
                  (relative.startswith("comsol_mcp/data/g2/") and relative.endswith(".json"))))}
    paths.update(relative for relative in EXTRA_CLOSURE_PATHS - OVERLAY_PATHS
                 if relative in tracked)
    required = EXTRA_CLOSURE_PATHS - OVERLAY_PATHS
    missing = sorted(required - tracked)
    if missing:
        raise CandidateError("published base is missing required closure files: " + ", ".join(missing))
    return sorted(paths)


def export_published_archive(*, repo: Path, destination: Path, base_commit: str) -> dict[str, Any]:
    """Export only the registered runtime/source closure plus this candidate.

    `git archive` is read-only and writes no checkout metadata. The requested
    base must remain reachable from the published origin/main; later published
    runtime changes and unrelated W24/runtime WIP remain outside this archive.
    """
    if not str(destination).startswith(EVIDENCE_PREFIX) or destination.exists():
        raise CandidateError(f"archive destination must be new below {EVIDENCE_PREFIX}*")
    head = _git(repo, "rev-parse", "HEAD")
    remote = _git(repo, "rev-parse", "origin/main")
    resolved_base = _git(repo, "rev-parse", f"{base_commit}^{{commit}}")
    if resolved_base != base_commit:
        raise CandidateError("archive base must be the exact immutable commit SHA")
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", base_commit, "origin/main"],
                              cwd=repo, capture_output=True, text=True, check=False, timeout=20)
    if ancestry.returncode != 0:
        raise CandidateError("archive base must be reachable from the published origin/main")
    tracked_paths = _tracked_closure_paths(repo, base_commit)
    destination.mkdir(parents=True, exist_ok=False)
    archive = subprocess.run(["git", "archive", "--format=tar", base_commit, "--", *tracked_paths],
        cwd=repo, capture_output=True, check=False, timeout=90)
    if archive.returncode != 0:
        raise CandidateError("git archive failed: " + archive.stderr.decode("utf-8", "replace")[:3000])
    with tarfile.open(fileobj=__import__("io").BytesIO(archive.stdout), mode="r:") as tar:
        members = tar.getmembers()
        for member in members:
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise CandidateError("published source archive contains an unsafe path")
        tar.extractall(destination, members=members, filter="data")
    base_files = {}
    for relative in tracked_paths:
        path = destination / relative
        if not path.is_file() or path.is_symlink():
            raise CandidateError(f"published archive omitted a regular source file: {relative}")
        base_files[relative] = {"bytes": path.stat().st_size, "sha256": _sha256_file(path)}
    manifest = {"schema_version": 1, "status": "EXACT_GIT_ARCHIVE_SOURCE_ONLY",
                "base_commit": base_commit, "published_origin_main_at_export": remote,
                "checkout_head_at_export": head, "source_files": base_files,
                "test_support_files": sorted(TEST_SUPPORT_PATHS),
                "source_closure_sha256": _json_hash(base_files)}
    manifest["manifest_sha256"] = _json_hash(manifest)
    _write_json(destination / ".w23_published_archive_manifest.json", manifest)
    for relative in sorted(OVERLAY_PATHS):
        source = repo / relative
        if not source.is_file() or source.is_symlink():
            raise CandidateError(f"candidate overlay is missing or unsafe: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
    return {"archive_dir": str(destination.resolve()), "base_commit": base_commit,
            "source_files": len(base_files), "source_closure_sha256": manifest["source_closure_sha256"],
            "archive_manifest_sha256": manifest["manifest_sha256"],
            "overlay_files": sorted(OVERLAY_PATHS)}


def _source_inventory(repo: Path, base_commit: str) -> dict[str, Any]:
    paths = _closure_paths(repo)
    archive_manifest_path = repo / ".w23_published_archive_manifest.json"
    isolated_archive = archive_manifest_path.is_file()
    if isolated_archive:
        manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "EXACT_GIT_ARCHIVE_SOURCE_ONLY" or manifest.get("base_commit") != base_commit:
            raise CandidateError("isolated source archive manifest does not bind the requested published commit")
        manifest_body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if manifest.get("manifest_sha256") != _json_hash(manifest_body):
            raise CandidateError("isolated source archive manifest digest is invalid")
        base_files = manifest.get("source_files")
        if not isinstance(base_files, Mapping):
            raise CandidateError("isolated source archive manifest omitted source file hashes")
        if manifest.get("test_support_files") != sorted(TEST_SUPPORT_PATHS):
            raise CandidateError("isolated source archive manifest misclassified or omitted test-support files")
        if manifest.get("source_closure_sha256") != _json_hash(base_files):
            raise CandidateError("isolated source archive closure digest is invalid")
        expected_base = set(paths) - OVERLAY_PATHS
        if set(base_files) != expected_base:
            raise CandidateError("isolated source archive does not contain the exact expected published closure")
        head, remote = base_commit, base_commit
        archive_manifest_sha256 = manifest["manifest_sha256"]
    else:
        head = _git(repo, "rev-parse", "HEAD")
        remote = _git(repo, "rev-parse", "origin/main")
        if head != base_commit or remote != base_commit:
            raise CandidateError("candidate base must be the exact published HEAD==origin/main commit")
        changed = set(_git(repo, "diff", "--name-only", base_commit, "--", *paths).splitlines())
        forbidden = sorted(changed - OVERLAY_PATHS)
        if forbidden:
            raise CandidateError("published runtime/catalog source differs from requested base: " + ", ".join(forbidden))
        untracked_rows = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *paths).splitlines()
        untracked = set(untracked_rows)
        if untracked - OVERLAY_PATHS:
            raise CandidateError("unexpected untracked files are inside the candidate closure: "
                                 + ", ".join(sorted(untracked - OVERLAY_PATHS)))
        archive_manifest_sha256 = None
    files: dict[str, dict[str, Any]] = {}
    for relative in paths:
        path = repo / relative
        payload = path.read_bytes()
        is_overlay = relative in OVERLAY_PATHS
        base_blob = None
        if is_overlay:
            if not isolated_archive and relative not in changed | untracked:
                # An already-published copy of this candidate remains valid.
                base_blob = _git(repo, "rev-parse", f"{base_commit}:{relative}")
        elif isolated_archive:
            expected = base_files[relative]
            if len(payload) != expected.get("bytes") or _sha256_bytes(payload) != expected.get("sha256"):
                raise CandidateError(f"isolated archive source differs from its base manifest: {relative}")
            base_blob = expected["sha256"]
        else:
            base_blob = _git(repo, "rev-parse", f"{base_commit}:{relative}")
            local_blob = _git(repo, "hash-object", relative)
            if local_blob != base_blob:
                raise CandidateError(f"candidate source differs from published base: {relative}")
        files[relative] = {
            "bytes": len(payload), "sha256": _sha256_bytes(payload),
            "base_blob": base_blob, "candidate_overlay": is_overlay,
        }
    return {"base_commit": base_commit, "head": head, "origin_main": remote,
            "checkout_kind": "git_archive" if isolated_archive else "repo_worktree",
            "archive_manifest_sha256": archive_manifest_sha256,
            "source_files": files, "source_closure_sha256": _json_hash(files)}


def _compile_fixture(repo: Path, install_root: Path, jdk_home: Path, out_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        raise CandidateError(f"offline compile directory must be new: {out_dir}")
    if not EXPECTED_PYTHON.is_file() or Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise CandidateError(f"run with the exact Python 3.12 executable: {EXPECTED_PYTHON}")
    if sys.version_info[:2] != (3, 12):
        raise CandidateError(f"expected Python 3.12, observed {sys.version.split()[0]}")
    if not (jdk_home / "bin/javac").is_file() or not (jdk_home / "bin/javap").is_file():
        raise CandidateError("frozen external JDK 11 javac/javap are unavailable")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from comsol_mcp._java_worker import JavaWorkerPaths

    paths = JavaWorkerPaths(install_root, jdk_home, project_root=repo)
    classpath, manifest_sha, jar_count, jar_fingerprint = paths.classpath()
    javac, javap = jdk_home / "bin/javac", jdk_home / "bin/javap"
    out_dir.mkdir(parents=True, exist_ok=False)
    command = [str(javac), "-encoding", "UTF-8", "-classpath", classpath,
               "-d", str(out_dir), str(FIXTURE)]
    compiled = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                               timeout=90, check=False)
    (out_dir.parent / "javac.stdout.txt").write_text(compiled.stdout, encoding="utf-8")
    (out_dir.parent / "javac.stderr.txt").write_text(compiled.stderr, encoding="utf-8")
    if compiled.returncode != 0:
        raise CandidateError("offline Java fixture compilation failed; no native process was started")
    classes = {path.relative_to(out_dir).as_posix(): {
        "bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for path in sorted(out_dir.rglob("*.class"))}
    if not classes:
        raise CandidateError("offline Java compile produced no .class files")
    classpath_with_classes = classpath + os.pathsep + str(out_dir)
    javap_result = subprocess.run(
        [str(javap), "-classpath", classpath_with_classes, "NativeW23Full3DFixture"],
        cwd=repo, capture_output=True, text=True, timeout=20, check=False)
    (out_dir.parent / "javap.stdout.txt").write_text(javap_result.stdout, encoding="utf-8")
    (out_dir.parent / "javap.stderr.txt").write_text(javap_result.stderr, encoding="utf-8")
    if javap_result.returncode != 0:
        raise CandidateError("offline javap inspection failed; no native process was started")
    javac_version = subprocess.run([str(javac), "-version"], capture_output=True,
                                   text=True, timeout=10, check=False)
    return {
        "source_sha256": _sha256_file(FIXTURE), "javac_command": command,
        "javac_version": (javac_version.stdout + javac_version.stderr).strip(),
        "javac_returncode": compiled.returncode, "javap_returncode": javap_result.returncode,
        "classpath_manifest_sha256": manifest_sha, "classpath_jar_count": jar_count,
        "classpath_jar_content_fingerprint_sha256": jar_fingerprint,
        "compiled_classes": classes,
        "compiler_log_sha256": {
            name: _sha256_file(out_dir.parent / name)
            for name in ("javac.stdout.txt", "javac.stderr.txt", "javap.stdout.txt", "javap.stderr.txt")
        },
    }


def prepare_candidate(*, repo: Path, evidence: Path, base_commit: str,
                      install_root: Path = INSTALL_ROOT, jdk_home: Path = JAVA11,
                      campaign_profile: str = "setup_only") -> dict[str, Any]:
    if campaign_profile not in {"setup_only", "bma_probe", "bma_mapping_probe"}:
        raise CandidateError("campaign profile must be setup_only, bma_probe or bma_mapping_probe")
    if not str(evidence).startswith(EVIDENCE_PREFIX) or evidence.exists():
        raise CandidateError(f"--evidence must be a new unique directory below {EVIDENCE_PREFIX}*")
    if Path.cwd().resolve(strict=True) != repo.resolve(strict=True):
        raise CandidateError("offline candidate preparation must run with cwd equal to the isolated archive root")
    if not (repo / ".w23_published_archive_manifest.json").is_file():
        raise CandidateError("offline candidate preparation requires the exact published git-archive directory")
    python_isolation = _configure_archive_python(repo)
    source_before = _source_inventory(repo, base_commit)
    if source_before.get("checkout_kind") != "git_archive":
        raise CandidateError("candidate source is not an isolated published git archive")
    evidence.mkdir(parents=True, exist_ok=False)
    compile_receipt = _compile_fixture(repo, install_root, jdk_home,
                                       evidence / "offline_compile/classes")
    import_audit = _archive_import_audit(repo)
    source_after = _source_inventory(repo, base_commit)
    if source_before != source_after:
        raise CandidateError("candidate source closure changed during offline compilation")
    from comsol_mcp._java_worker import JavaWorkerPaths
    paths = JavaWorkerPaths(install_root, jdk_home, project_root=repo)
    runtime_fingerprint = {
        "install_root": str(install_root.resolve(strict=True)),
        "comsol_version": paths.comsol_version_info(),
        "jdk_home": str(jdk_home.resolve(strict=True)),
        "jdk_version": paths.jdk_version_info(),
        "python_invocation_path": str(EXPECTED_PYTHON),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version.split()[0],
    }
    budget, routes = _profile_budget_and_routes(campaign_profile)
    bma_profile = _is_bma_profile(campaign_profile)
    mapping_profile = campaign_profile == "bma_mapping_probe"
    if mapping_profile:
        from tools.w23_full3d_science import (
            BMA_FIELD_MAPPING_POLICY, COMSOL_INTERP_UNIT_KB_EVIDENCE,
            COMSOL_PORT_MODE_FIELD_KB_EVIDENCE,
        )
        mapping_policy = dict(BMA_FIELD_MAPPING_POLICY)
        field_semantics_evidence = {
            "port_mode_suffix": dict(COMSOL_PORT_MODE_FIELD_KB_EVIDENCE),
            "interp_units": dict(COMSOL_INTERP_UNIT_KB_EVIDENCE),
        }
    else:
        mapping_policy = []
        field_semantics_evidence = []
    status = ("PREPARED_BMA_MAPPING_PROBE_NOT_NATIVE" if mapping_profile else
              "PREPARED_BMA_PROBE_NOT_NATIVE" if bma_profile else
              "PREPARED_SETUP_ONLY_NOT_NATIVE")
    body = {
        "schema_version": 1,
        "status": status,
        "campaign_profile": campaign_profile,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source_after, "runtime": runtime_fingerprint,
        "compile": compile_receipt, "budget": budget,
        "native_readback_api_evidence": NATIVE_READBACK_API_EVIDENCE,
        "bma_producer_api_evidence": BMA_PRODUCER_API_EVIDENCE if bma_profile else [],
        "bma_mapping_api_evidence": BMA_MAPPING_PROBE_API_EVIDENCE if mapping_profile else [],
        "field_mapping_policy": mapping_policy,
        "field_semantics_kb_evidence": field_semantics_evidence,
        "python_isolation": python_isolation,
        "runtime_import_audit": import_audit,
        "routes": routes,
        "scientific_status": "NOT_RUN",
        "mode_producer_lineage": "UNVERIFIED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
        "fixture_contract": {
            "receiver_numeric_port": "portOut3d", "receiver_port_name": "2",
            "receiver_port_mode_number": 1,
            "bma_output_step": "bmaOutput3d", "bma_output_neigs": 2,
            "basis_ordinals": [1, 2],
            "basis_ordinal_is_not_port_mode_number": True,
            "isolated_bma_probe_study": ("std3dBmaOutputProbe with only bmaOutputProbe(PortName=2,modeFreq=f0,neigs=2)"
                                          if bma_profile else "NOT_IN_PROFILE"),
            "solver_sequence_method": "SolverSequence.runAll" if bma_profile else "NOT_IN_PROFILE",
            "study_run_calls": 0,
            "solver_calls": 1 if bma_profile else 0,
            "field_mapping_status": "UNVERIFIED",
            "paired_field_probe": ({"basis_tuples": 2, "native_interp_groups_per_tuple": 3,
                                     "coordinate_grid": "32 radial Simpson intervals x 64 periodic trapezoid points",
                                     "route_wait_caps_seconds": {"dataset.list": 90,
                                         "dataset.solution_indices": 90,
                                         "paired_fields_per_tuple": 180},
                                     "field_mapping_status": "UNVERIFIED"}
                                    if mapping_profile else "NOT_IN_PROFILE"),
            "mapping_policy_id": (mapping_policy.get("policy_id")
                                  if isinstance(mapping_policy, Mapping) else "NOT_IN_PROFILE"),
        },
        "evidence_dir": str(evidence.resolve()),
    }
    freeze = {**body, "candidate_sha256": _json_hash(body)}
    _write_json(evidence / "candidate_freeze.json", freeze)
    return freeze


def verify_candidate(*, repo: Path, evidence: Path, reviewed_sha256: str) -> dict[str, Any]:
    freeze_path = evidence / "candidate_freeze.json"
    if not freeze_path.is_file() or freeze_path.is_symlink():
        raise CandidateError("candidate freeze is missing or unsafe")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    digest = freeze.get("candidate_sha256")
    body = {key: value for key, value in freeze.items() if key != "candidate_sha256"}
    if digest != _json_hash(body) or reviewed_sha256 != digest:
        raise CandidateError("the separately reviewed candidate SHA-256 does not match the frozen candidate")
    base_commit = freeze.get("source", {}).get("base_commit")
    current_source = _source_inventory(repo, base_commit)
    if current_source != freeze.get("source"):
        raise CandidateError("current published source/overlay closure differs from the reviewed candidate")
    campaign_profile = freeze.get("campaign_profile")
    if campaign_profile not in {"setup_only", "bma_probe", "bma_mapping_probe"}:
        raise CandidateError("frozen campaign profile is missing or unsupported")
    expected_budget, expected_routes = _profile_budget_and_routes(campaign_profile)
    expected_status = ("PREPARED_BMA_MAPPING_PROBE_NOT_NATIVE" if campaign_profile == "bma_mapping_probe"
                       else "PREPARED_BMA_PROBE_NOT_NATIVE" if campaign_profile == "bma_probe"
                       else "PREPARED_SETUP_ONLY_NOT_NATIVE")
    expected_api_evidence = BMA_PRODUCER_API_EVIDENCE if _is_bma_profile(campaign_profile) else []
    expected_mapping_evidence = (BMA_MAPPING_PROBE_API_EVIDENCE
                                 if campaign_profile == "bma_mapping_probe" else [])
    if campaign_profile == "bma_mapping_probe":
        from tools.w23_full3d_science import (
            BMA_FIELD_MAPPING_POLICY, COMSOL_INTERP_UNIT_KB_EVIDENCE,
            COMSOL_PORT_MODE_FIELD_KB_EVIDENCE,
        )
        expected_mapping_policy: Any = dict(BMA_FIELD_MAPPING_POLICY)
        expected_field_semantics: Any = {
            "port_mode_suffix": dict(COMSOL_PORT_MODE_FIELD_KB_EVIDENCE),
            "interp_units": dict(COMSOL_INTERP_UNIT_KB_EVIDENCE),
        }
    else:
        expected_mapping_policy = []
        expected_field_semantics = []
    if (freeze.get("budget") != expected_budget or freeze.get("routes") != expected_routes
            or freeze.get("status") != expected_status
            or freeze.get("bma_producer_api_evidence") != expected_api_evidence
            or freeze.get("bma_mapping_api_evidence") != expected_mapping_evidence
            or freeze.get("field_mapping_policy") != expected_mapping_policy
            or freeze.get("field_semantics_kb_evidence") != expected_field_semantics):
        raise CandidateError("candidate budget or route allowlist differs from the reviewed freeze")
    return freeze


def build_disconnect_request(*, project_id: str, session_id: str,
                             request_id: str, idempotency_key: str) -> dict[str, Any]:
    if any(not isinstance(item, str) or not item.strip() for item in
           (project_id, session_id, request_id, idempotency_key)):
        raise CandidateError("exact project/session and request identities are required for retirement")
    return {"operation": "session.disconnect",
            "arguments": {"retire_worker": True},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_id, "idempotency_key": idempotency_key,
                          "rpc_timeout_s": 30.0, "execution_timeout_s": 90.0,
                          "queue_timeout_s": 30.0}}


def validate_retirement_response(response: Mapping[str, Any], *, project_id: str,
                                 session_id: str, worker_instance_id: str,
                                 connected_epoch: int,
                                 detached_epoch: int | None = None) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, Mapping) else None
    proof = data.get("worker_retirement") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping)
            or data.get("project_id") != project_id or data.get("session_id") != session_id
            or data.get("state") != "DISCONNECTED" or data.get("client_state") != "RETIRED"
            or data.get("server_stopped") is not False or not isinstance(proof, Mapping)):
        raise CleanupRefused("worker_retirement", "disconnect did not report a successful Worker-only retirement", [])
    identity = proof.get("process_identity")
    pid = identity.get("pid") if isinstance(identity, Mapping) else None
    birth = identity.get("start_epoch_ms") if isinstance(identity, Mapping) else None
    observed_epoch = proof.get("worker_epoch")
    if (proof.get("status") != "RETIRED"
            or proof.get("worker_instance_id") != worker_instance_id
            or type(observed_epoch) is not int or observed_epoch <= connected_epoch
            or (detached_epoch is not None and observed_epoch != detached_epoch)
            or type(pid) is not int or pid <= 1 or type(birth) is not int or birth <= 0
            or proof.get("exact_popen_handle") is not True
            or proof.get("birth_identity_matched_before_close") is not True
            or proof.get("child_exit_confirmed") is not True
            or proof.get("child_reaped") is not True
            or proof.get("admission_fence") != "RETIRED"
            or proof.get("disconnect_rpc_dispatched") is not True
            or proof.get("worker_close_started") is not True):
        raise CleanupRefused("worker_retirement", "retirement proof does not bind the expected exact Worker and child", [])
    return dict(proof)


def validate_native_mesh_readback(readback: Mapping[str, Any], *, phase: str,
                                  previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify actual whole-domain mesh getters before any existing solve route.

    The all() action is provenance; fresh topology/selection equality supplies
    the coverage evidence. No native isAll getter or count-derived IDs exist.
    """
    def fail(message: str) -> None:
        raise CandidateError("full3D native mesh: " + message)

    def positive_ids(value: Any, label: str) -> set[int]:
        if (not isinstance(value, list) or not value
                or any(type(v) is not int or v <= 0 for v in value)
                or len(set(value)) != len(value)):
            fail(label + " needs unique positive actual int IDs")
        return set(value)

    mesh = readback.get("mesh")
    if not isinstance(mesh, Mapping) or mesh.get("tag") != "mesh3d" or mesh.get("geometry") != "geom3d":
        fail("mesh identity missing")
    o = mesh.get("observation")
    if not isinstance(o, Mapping):
        fail("native getter observation missing")
    expected_attempts = [
        "feature_tags_before", "mesh_tag", "geometry_tag", "geometry_dimension", "domain_count", "up_down",
        "generator_tag", "generator_type", "selection_geometry", "selection_dimension", "selection_dimensions",
        "selection_entities", "selection_is_geom_raw", "selection_is_remaining_raw", "size_custom", "size_hmax",
        "size_hmin", "mesh_dimension", "is_empty", "element_count", "is_complete", "has_problems", "problems"]
    if (o.get("schema") != "W23_FULL3D_OWNED_MESH_V1"
            or o.get("status") != "NATIVE_MESH_GETTERS_ACCEPTED_NO_SCIENCE_CLAIM"
            or o.get("phase") != phase or phase not in {"initial", "apply_case"}
            or o.get("getter_attempts") != expected_attempts
            or o.get("getter_errors") != {} or "failure_cause" in o or "mesh_run_error" in o):
        fail("schema, stage, getter attempts or errors invalid")
    tags = o.get("feature_tags_before")
    if (not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.strip() for t in tags)
            or len(set(tags)) != len(tags) or "size" not in tags):
        fail("feature tag inventory incomplete/ambiguous")
    initial = phase == "initial"
    if (type(o.get("generator_created_in_call")) is not int
            or o["generator_created_in_call"] != (1 if initial else 0)
            or ("w23tet" in tags) == initial
            or o.get("selection_action_provenance") != (
                "geom_3_all_called_this_initial_creation" if initial
                else "same_owned_generator_initial_geom_3_all_reused_no_selection_repair")):
        fail("generator ownership or configured all action provenance invalid")
    if (o.get("mesh_tag") != "mesh3d" or o.get("geometry_tag") != "geom3d"
            or o.get("generator_tag") != "w23tet" or o.get("generator_type") != "FreeTet"
            or o.get("selection_geometry") != "geom3d"):
        fail("native identity/type mismatch")
    for key in ("geometry_dimension", "selection_dimension", "mesh_dimension"):
        if type(o.get(key)) is not int or o[key] != 3:
            fail(key + " is not actual dimension 3")
    dims = o.get("selection_dimensions")
    if not isinstance(dims, list) or len(dims) != 1 or type(dims[0]) is not int or dims[0] != 3:
        fail("selection dimension inventory invalid")
    up_down = o.get("up_down")
    if (not isinstance(up_down, list) or len(up_down) != 2 or any(not isinstance(row, list) for row in up_down)
            or not up_down[0] or len(up_down[0]) != len(up_down[1])
            or any(type(v) is not int or v < 0 for row in up_down for v in row)):
        fail("native getUpDown malformed")
    domains = {v for row in up_down for v in row if v > 0}
    if not domains or type(o.get("domain_count")) is not int or o["domain_count"] != len(domains):
        fail("getNDomains cardinality differs from actual topology IDs")
    if positive_ids(o.get("domain_ids"), "domain_ids") != domains:
        fail("domain IDs differ from actual fresh getUpDown")
    if positive_ids(o.get("selection_entities"), "selection_entities") != domains:
        fail("FreeTet does not cover exact actual full geometry domain set")
    for key in ("selection_is_geom_raw", "selection_is_remaining_raw", "is_empty", "is_complete", "has_problems"):
        if type(o.get(key)) is not bool:
            fail(key + " is not native Boolean")
    if (o.get("mesh_run_attempted") is not True or o.get("mesh_run_returned") is not True
            or o["is_empty"] is not False or o["is_complete"] is not True or o["has_problems"] is not False
            or o.get("problems") != [] or type(o.get("element_count")) is not int or o["element_count"] <= 0
            or type(mesh.get("elements")) is not int or mesh["elements"] != o["element_count"]):
        fail("run/empty/completeness/problems/positive element count invalid")
    sizes = (o.get("size_hmax"), o.get("size_hmin"))
    base = ("lambda0/(5*w23Nlens)", "lambda0/(12*w23Nlens)")
    frozen_sizes = {base, *((f"({base[0]})*{s}", f"({base[1]})*{s}") for s in ("0.8", "0.64"))}
    if o.get("size_custom") != "on" or any(not isinstance(v, str) for v in sizes) or sizes not in frozen_sizes:
        fail("Size is outside original frozen expression pairs")
    if initial:
        if mesh.get("hmax") != sizes[0] or mesh.get("hmin") != sizes[1]:
            fail("legacy Size readback differs from actual mesh getters")
        if previous is not None:
            fail("initial mesh cannot inherit prior proof")
    else:
        if not isinstance(previous, Mapping) or previous.get("phase") != "initial":
            fail("apply needs exact initial mesh proof")
        if previous.get("size_hmax") != sizes[0] or previous.get("size_hmin") != sizes[1]:
            fail("apply changed original build Size")
    return {"status": "NATIVE_MESH_GETTERS_VALIDATED_NO_SCIENCE_CLAIM", "phase": phase,
            "mesh_tag": "mesh3d", "generator_tag": "w23tet", "generator_type": "FreeTet",
            "domain_ids": sorted(domains), "size_hmax": sizes[0], "size_hmin": sizes[1],
            "element_count": o["element_count"], "Study_or_Solver_RUN": False,
            "scientific_acceptance": "NOT_RUN"}


def validate_native_mode_configuration(readback: Mapping[str, Any], *, inventory: bool = False) -> dict[str, Any]:
    """Validate only native configuration readback, never mode-solution identity.

    The fixture has one Numeric receiver (Port 2, configured PortModeNumber 1)
    and a BMA output step requesting two eigensolutions. This does not establish
    which later SolutionInfo indices were produced by that step or what field
    values map to those indices.
    """
    if inventory:
        port_rows = None
        step_rows = readback.get("study_steps_in_configured_order")
    else:
        port_rows = readback.get("ports")
        step_rows = readback.get("study_steps")

    if port_rows is not None:
        if not isinstance(port_rows, list):
            raise CandidateError("native fixture readback omitted the Numeric Port inventory")
        tags = [row.get("tag") for row in port_rows if isinstance(row, Mapping)]
        if len(tags) != len(port_rows) or sorted(tags) != ["portIn3d", "portOut3d"]:
            raise CandidateError("native fixture readback does not contain exactly input Port 1 and receiver Port 2")
        for tag, port_name in (("portIn3d", "1"), ("portOut3d", "2")):
            row = next(item for item in port_rows if item.get("tag") == tag)
            feature = row.get("properties")
            requested = feature.get("requested_properties") if isinstance(feature, Mapping) else None
            if not isinstance(requested, Mapping):
                raise CandidateError(f"native {tag} property readback is missing")
            for key, expected in (("PortType", "Numeric"), ("PortName", port_name),
                                  ("PortModeNumber", "1")):
                prop = requested.get(key)
                if (not isinstance(prop, Mapping) or prop.get("has_property_exact") is not True
                        or prop.get("readback_error") is not None
                        or prop.get("string_readback") != expected):
                    raise CandidateError(f"native {tag}.{key} readback differs from the frozen Port configuration")

    if not isinstance(step_rows, list):
        raise CandidateError("native fixture readback omitted the study-step inventory")
    if inventory:
        tags = [row.get("tag") for row in step_rows if isinstance(row, Mapping)]
        if len(tags) != len(step_rows) or sorted(tags) != ["bmaInput3d", "bmaOutput3d", "freq3d"]:
            raise CandidateError("native solution inventory does not contain the exact configured study steps")
    matches = [row for row in step_rows
               if isinstance(row, Mapping) and row.get("tag") == "bmaOutput3d"]
    if len(matches) != 1:
        raise CandidateError("native readback must contain exactly one bmaOutput3d step")
    output = matches[0]
    port_name = output.get("PortName") if inventory else output.get("port")
    neigs = output.get("neigs")
    feature_type = output.get("feature_type", output.get("type"))
    if (feature_type != "BoundaryModeAnalysis" or port_name != "2"
            or output.get("modeFreq") != "f0" or type(neigs) is not int or neigs != 2):
        raise CandidateError("native bmaOutput3d PortName/modeFreq/neigs readback differs from the frozen two-eigensolution request")
    return {
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


def _validate_disconnected_inspect(response: Mapping[str, Any], *, project_id: str,
                                   session_id: str, worker_instance_id: str,
                                   worker_epoch: int) -> None:
    data = response.get("data") if isinstance(response, Mapping) else None
    lifecycle = data.get("lifecycle") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(data, Mapping)
            or data.get("project_id") != project_id or data.get("runtime_live") is not False
            or not isinstance(lifecycle, Mapping)
            or lifecycle.get("session_id") != session_id
            or lifecycle.get("state") != "DISCONNECTED"
            or lifecycle.get("client_state") != "RETIRED"
            or lifecycle.get("worker_instance_id") != worker_instance_id
            or lifecycle.get("worker_epoch") != worker_epoch
            or data.get("worker_binding") is not None):
        raise CleanupRefused("session_inspect", "public readback does not prove this exact retired session", [])


def orchestrate_cleanup(*, worker_state: str, project_id: str | None,
                        session_id: str | None, worker_instance_id: str | None,
                        connected_epoch: int | None, detached_epoch: int | None,
                        all_project_jobs_terminal: bool,
                        disconnect: Callable[[], Mapping[str, Any]] | None,
                        inspect: Callable[[], Mapping[str, Any]] | None,
                        stop_server: Callable[[], Mapping[str, Any]],
                        stop_control: Callable[[], Mapping[str, Any]]) -> list[str]:
    """Order exact Worker retirement, server stop, then control child reap.

    Every callback is a narrow injected adapter. UNKNOWN or failed evidence
    stops the sequence, leaving all later process handles untouched.
    """
    completed: list[str] = []
    if worker_state == "UNKNOWN":
        raise CleanupRefused("worker_state", "session/Worker outcome is UNKNOWN; preserve every process", completed)
    if worker_state == "CONNECTED":
        if (not all_project_jobs_terminal or not all((project_id, session_id, worker_instance_id,
                                                       connected_epoch is not None))
                or disconnect is None or inspect is None):
            raise CleanupRefused("pre_retirement", "missing terminal ledger or exact session identity", completed)
        try:
            retired = disconnect()
        except BaseException as exc:
            raise CleanupRefused("worker_retirement", f"disconnect outcome unknown ({type(exc).__name__})", completed) from exc
        proof = validate_retirement_response(
            retired, project_id=str(project_id), session_id=str(session_id),
            worker_instance_id=str(worker_instance_id), connected_epoch=int(connected_epoch),
            detached_epoch=detached_epoch)
        completed.append("exact_managed_worker_retired")
        try:
            observed = inspect()
        except BaseException as exc:
            raise CleanupRefused("session_inspect", f"retired session readback unavailable ({type(exc).__name__})", completed) from exc
        _validate_disconnected_inspect(
            observed, project_id=str(project_id), session_id=str(session_id),
            worker_instance_id=str(worker_instance_id), worker_epoch=proof["worker_epoch"])
        completed.append("public_disconnected_inspect_verified")
    elif worker_state != "NEVER_DISPATCHED":
        raise CleanupRefused("worker_state", f"unsupported Worker state {worker_state!r}", completed)
    if not all_project_jobs_terminal:
        raise CleanupRefused("job_ledger", "project contains nonterminal/unknown jobs", completed)
    try:
        server_result = stop_server()
    except BaseException as exc:
        raise CleanupRefused("server_stop", f"owned server cleanup failed ({type(exc).__name__})", completed) from exc
    if not _exact_child_stopped(server_result):
        raise CleanupRefused("server_stop", "exact server Popen/birth/listener/reap evidence is incomplete", completed)
    completed.append("exact_task_server_stopped_and_reaped")
    try:
        control_result = stop_control()
    except BaseException as exc:
        raise CleanupRefused("control_stop", f"owned control-daemon cleanup failed ({type(exc).__name__})", completed) from exc
    if not _exact_child_stopped(control_result):
        raise CleanupRefused("control_stop", "exact control Popen/birth/listener/reap evidence is incomplete", completed)
    completed.append("exact_control_daemon_stopped_and_reaped")
    return completed


def _exact_child_stopped(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return False
    if result.get("status") == "NOT_STARTED":
        return result.get("child_started") is False
    return (result.get("status") == "STOPPED_AND_REAPED"
            and type(result.get("pid")) is int and result["pid"] > 1
            and type(result.get("start_epoch_ms")) is int and result["start_epoch_ms"] > 0
            and result.get("child_exit_confirmed") is True
            and result.get("child_reaped") is True
            and result.get("listener_absent") is True)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _new_ids(label: str) -> tuple[str, str]:
    token = uuid4().hex
    return f"w23f3d-{label}-{token}", f"w23f3d-idem-{label}-{token}"


class PublicDispatchAdapter:
    """Call only the production `_control_client.dispatch` HTTP transport."""

    def __init__(self, evidence: Path, *, expected_control_home: Path, archive_root: Path):
        self.evidence = evidence
        self.requests_path = evidence / "public_dispatches.jsonl"
        self.expected_control_home = expected_control_home.resolve()
        self.archive_root = archive_root.resolve()
        self.owned_endpoint: dict[str, Any] | None = None
        self.owned_process_identity: dict[str, Any] | None = None

    def bind_owned_control(self, endpoint: Mapping[str, Any],
                           process_identity: Mapping[str, Any]) -> None:
        if (type(endpoint.get("pid")) is not int
                or type(endpoint.get("port")) is not int
                or type(endpoint.get("process_start_epoch_ms")) is not int
                or process_identity.get("pid") != endpoint.get("pid")
                or process_identity.get("start_epoch_ms") != endpoint.get("process_start_epoch_ms")):
            raise CandidateError("owned control endpoint does not bind its exact Popen birth identity")
        self.owned_endpoint = dict(endpoint)
        self.owned_process_identity = {"pid": process_identity["pid"],
                                       "start_epoch_ms": process_identity["start_epoch_ms"]}

    def _verify_owned_control_route(self) -> None:
        if self.owned_endpoint is None or self.owned_process_identity is None:
            raise CandidateError("public dispatch has no bound task-owned control daemon")
        from comsol_mcp._control_client import control_home

        observed_home = control_home().resolve(strict=True)
        if observed_home != self.expected_control_home:
            raise CandidateError("public control_home differs from this candidate's owned daemon home")
        endpoint_path = observed_home / "control.json"
        if not endpoint_path.is_file() or endpoint_path.is_symlink():
            raise CandidateError("public control endpoint file is missing or unsafe")
        endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
        for key in ("pid", "port", "process_start_epoch_ms", "token"):
            if endpoint.get(key) != self.owned_endpoint.get(key):
                raise CandidateError("public control endpoint no longer matches the exact task-owned daemon")
        if _process_identity(endpoint["pid"]) != self.owned_process_identity:
            raise CandidateError("public control daemon birth no longer matches the exact Popen")
        listeners = _lsof_listeners(endpoint["port"])
        if (len(listeners) != 1 or listeners[0]["pid"] != endpoint["pid"]
                or listeners[0]["endpoint"] != f"127.0.0.1:{endpoint['port']}"):
            raise CandidateError("public dispatch endpoint is not the uniquely owned loopback listener")
        _audit_loaded_project_modules(self.archive_root)
        _assert_no_editable_fallback()

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise CandidateError("public route request must be a mapping")
        operation = request.get("operation")
        arguments, execution = request.get("arguments", {}), request.get("execution", {})
        if not isinstance(operation, str) or not isinstance(arguments, dict) or not isinstance(execution, dict):
            raise CandidateError("public route request has invalid operation/arguments/execution")
        if operation == "job.list":
            filter_project = arguments.get("project_id")
            envelope_project = execution.get("project_id")
            if (not isinstance(filter_project, str) or not filter_project.strip()
                    or not isinstance(envelope_project, str) or envelope_project != filter_project):
                raise CandidateError("project-scoped public job.list requires matching arguments and execution project_id")
        if operation in {"study.run", "solve", "solver.run", "model.solve"}:
            raise CandidateError("setup-only route guard rejected a solve operation")
        self._verify_owned_control_route()
        from comsol_mcp._control_client import dispatch
        response = dispatch(operation, arguments, execution)
        self._verify_owned_control_route()
        row = {"operation": operation, "arguments": arguments, "execution": execution,
               "response": response, "at_utc": datetime.now(timezone.utc).isoformat()}
        with self.requests_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return response


def _require_route_ok(result: Mapping[str, Any], label: str) -> dict[str, Any]:
    response = result.get("response")
    if result.get("outcome") != "SUCCEEDED" or not isinstance(response, Mapping):
        raise CandidateError(f"managed route {label} did not complete with an observed success")
    return dict(response)


def _public_connect_request(*, project_id: str, runtime_id: str, port: int) -> dict[str, Any]:
    request_id, key = _new_ids("session-connect")
    return {"operation": "session.connect",
            "arguments": {"runtime_id": runtime_id,
                          "endpoint": {"host": "127.0.0.1", "port": port}},
            "execution": {"project_id": project_id, "request_id": request_id,
                          "idempotency_key": key, "rpc_timeout_s": 30.0,
                          "execution_timeout_s": 120.0, "queue_timeout_s": 30.0}}


def _session_identity(response: Mapping[str, Any], *, project_id: str,
                     expected_port: int) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, Mapping) or response.get("success") is not True:
        raise CandidateError("public session.connect did not return a proven connection")
    endpoint = data.get("endpoint")
    peer = data.get("observed_peer")
    session_id, server_id = data.get("session_id"), data.get("server_instance_id")
    worker_id, worker_epoch = data.get("worker_instance_id"), data.get("worker_epoch")
    if (data.get("project_id") != project_id
            or endpoint not in (f"127.0.0.1:{expected_port}",
                         {"host": "127.0.0.1", "port": expected_port})
            or not isinstance(peer, Mapping)
            or peer.get("address") != "127.0.0.1" or peer.get("port") != expected_port
            or not all(isinstance(value, str) and value for value in (session_id, server_id, worker_id))
            or type(worker_epoch) is not int or worker_epoch < 1):
        raise CandidateError("session.connect identity/endpoint/peer readback does not match the task-owned server")
    version = data.get("remote_engine_version")
    build = data.get("remote_engine_build")
    if not isinstance(version, str) or "6.4" not in version or not isinstance(build, str) or "293" not in build:
        raise CandidateError("public session.connect engine version/build differs from frozen COMSOL 6.4.0.293")
    return {"project_id": data.get("project_id"), "session_id": session_id,
            "server_instance_id": server_id, "worker_instance_id": worker_id,
            "worker_epoch": worker_epoch, "endpoint": endpoint,
            "observed_peer": dict(peer), "remote_engine_version": version,
            "remote_engine_build": build}


def _managed_model_request(project_id: str, session: Mapping[str, Any]) -> dict[str, Any]:
    from tools.w23_full3d_science import build_full3d_model_create_request
    request_id, key = _new_ids("model-create")
    return build_full3d_model_create_request(
        project_id=project_id, session_id=str(session["session_id"]),
        name="w23-full3d-setup-only", request_id=request_id, idempotency_key=key)


def _job_ledger_terminal(dispatcher: PublicDispatchAdapter, project_id: str) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id.strip():
        raise CandidateError("public job.list requires an exact project identity")
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    expected_total: int | None = None
    page_count = 0
    while True:
        page_count += 1
        request = {"operation": "job.list",
            "arguments": {"project_id": project_id, "offset": offset,
                          "limit": JOB_LIST_PAGE_LIMIT},
            # job.list is cataloged as project-scoped. Carry the same scope in
            # the public execution envelope as well as the list filter.
            "execution": {"project_id": project_id, "rpc_timeout_s": 30.0}}
        response = dispatcher.dispatch(request)
        if response.get("success") is not True:
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            message = error.get("message") if isinstance(error, Mapping) else None
            raise CandidateError(
                f"public job.list could not verify the project operation ledger: {code or 'UNKNOWN'}: {message or 'no error detail'}")
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise CandidateError("public job.list omitted its data object")
        page = data.get("jobs")
        if not isinstance(page, list):
            raise CandidateError("public job.list omitted its jobs array")
        total = data.get("total", data.get("total_count"))
        total_count = data.get("total_count")
        if (type(total) is not int or total < 0
                or (total_count is not None and (type(total_count) is not int or total_count != total))):
            raise CandidateError("public job.list omitted a stable non-negative total count")
        response_offset = data.get("offset")
        response_limit = data.get("limit")
        has_more = data.get("has_more")
        next_cursor = data.get("next_cursor")
        if (type(response_offset) is not int or response_offset != offset
                or type(response_limit) is not int or response_limit != JOB_LIST_PAGE_LIMIT
                or type(has_more) is not bool):
            raise CandidateError("public job.list page metadata does not match its request")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise CandidateError("public job.list total changed while paging the project ledger")
        if len(page) > JOB_LIST_PAGE_LIMIT or offset + len(page) > total:
            raise CandidateError("public job.list returned an oversized or out-of-range page")
        for item in page:
            if not isinstance(item, Mapping):
                raise CandidateError("public job.list returned a malformed job row")
            job_id = item.get("job_id")
            if not isinstance(job_id, str) or not job_id.strip() or job_id in seen_ids:
                raise CandidateError("public job.list returned a missing or duplicate job identity")
            seen_ids.add(job_id)
            jobs.append(dict(item))
        if len(jobs) > MAX_JOB_LEDGER_ROWS:
            raise CandidateError("public job.list project ledger exceeds the frozen 10,000-row inspection bound")
        next_offset = offset + len(page)
        if has_more:
            if not page or next_cursor != str(next_offset) or next_offset >= total:
                raise CandidateError("public job.list continuation cursor/has_more proof is inconsistent")
            if page_count >= (MAX_JOB_LEDGER_ROWS + JOB_LIST_PAGE_LIMIT - 1) // JOB_LIST_PAGE_LIMIT:
                raise CandidateError("public job.list exceeds the frozen page inspection bound")
            offset = next_offset
            continue
        if next_cursor is not None or next_offset != total:
            raise CandidateError("public job.list final page does not close the complete project ledger")
        break
    nonterminal = [job for job in jobs if job.get("status") not in TERMINAL_JOB_STATES]
    return {"jobs": jobs, "count": len(jobs), "total_count": expected_total,
            "page_count": page_count, "nonterminal": nonterminal,
            "all_terminal": not nonterminal}


def _process_identity(pid: int) -> dict[str, Any] | None:
    from comsol_mcp._platform_process import process_identity
    value = process_identity(pid)
    if not isinstance(value, Mapping):
        return None
    birth = value.get("start_epoch_ms")
    if value.get("alive") is not True or type(birth) is not int or birth <= 0:
        return None
    return {"pid": pid, "start_epoch_ms": birth}


def _bind_native_server_identity(server: Any, listener: Mapping[str, Any] | None) -> dict[str, int]:
    """Bind the published Popen to Darwin's numeric birth identity.

    NativeLoopbackServer's `_g2_isolation._process_snapshot` readback contains
    the formatted `birth` string and a command digest, not `start_epoch_ms`.
    Read the numeric value through `_platform_process.process_identity` while
    the exact Popen remains live, and cross-check that the helper snapshot and
    listener both identify that Popen and private shadow.
    """
    proc = getattr(server, "proc", None)
    pid = getattr(proc, "pid", None)
    port = getattr(server, "port", None)
    helper_identity = getattr(server, "process_identity", None)
    if (type(pid) is not int or pid <= 1 or type(port) is not int or not 1 <= port <= 65535
            or not callable(getattr(proc, "poll", None)) or proc.poll() is not None):
        raise CandidateError("task-owned server Popen/port is not live for numeric birth binding")
    if not isinstance(helper_identity, Mapping):
        raise CandidateError("NativeLoopbackServer omitted its process snapshot")
    helper_pid = helper_identity.get("pid")
    helper_birth = helper_identity.get("birth")
    helper_command = helper_identity.get("command")
    helper_command_sha = helper_identity.get("command_sha256")
    helper_port = helper_identity.get("port")
    work = getattr(server, "work", None)
    shadow_root = getattr(server, "shadow_root", None)
    if (helper_pid != pid or not isinstance(helper_birth, str) or not helper_birth.strip()
            or not isinstance(helper_command, str) or not helper_command
            or not isinstance(helper_command_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", helper_command_sha) is None
            or _sha256_bytes(helper_command.encode("utf-8")) != helper_command_sha
            or helper_port != port or work is None or shadow_root is None
            or str(work) not in helper_command or str(shadow_root) not in helper_command):
        raise CandidateError("NativeLoopbackServer process snapshot does not match its exact Popen/private shadow")
    if (not isinstance(listener, Mapping)
            or listener.get("status") != "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER"
            or listener.get("pid") != pid or listener.get("port") != port
            or listener.get("endpoint") != f"127.0.0.1:{port}"
            or listener.get("process_identity") != dict(helper_identity)):
        raise CandidateError("pre-Worker listener evidence does not bind the same helper process snapshot")
    first = _process_identity(pid)
    if first is None or proc.poll() is not None:
        raise CandidateError("native process birth identity could not be read while the exact Popen was live")
    second = _process_identity(pid)
    if second != first or proc.poll() is not None:
        raise CandidateError("native process birth identity changed while binding the exact Popen")
    listeners = _lsof_listeners(port)
    if (len(listeners) != 1 or listeners[0]["pid"] != pid
            or listeners[0]["endpoint"] != f"127.0.0.1:{port}"):
        raise CandidateError("native birth binding lacks one current loopback listener owned by the exact Popen")
    return first


def _lsof_listeners(port: int) -> list[dict[str, Any]]:
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise CandidateError("lsof cannot verify task-owned listener state")
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("COMMAND"):
            continue
        fields = line.split()
        if len(fields) < 2 or not fields[1].isdigit():
            raise CandidateError("lsof returned an unparsable listener row")
        try:
            endpoint = fields[fields.index("TCP") + 1]
        except (ValueError, IndexError):
            raise CandidateError("lsof listener row omitted its TCP endpoint")
        rows.append({"pid": int(fields[1]), "endpoint": endpoint, "line": line})
    return rows


def _lsof_process_listeners(pid: int) -> list[dict[str, Any]]:
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
                             "-iTCP", "-sTCP:LISTEN"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise CandidateError("lsof cannot verify the exact task Popen listener")
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("COMMAND"):
            continue
        fields = line.split()
        try:
            row_pid = int(fields[1])
            endpoint = fields[fields.index("TCP") + 1]
        except (ValueError, IndexError):
            raise CandidateError("lsof process-listener row is malformed")
        rows.append({"pid": row_pid, "endpoint": endpoint, "line": line})
    return rows


def _stop_owned_process(proc: subprocess.Popen[Any], expected: Mapping[str, Any], *, port: int,
                        timeout_s: float = 8.0) -> dict[str, Any]:
    pid = proc.pid
    if type(pid) is not int or pid <= 1:
        raise CandidateError("exact owned Popen is missing before verified cleanup")
    return_code = proc.poll()
    expected_identity = {"pid": expected.get("pid"),
                         "start_epoch_ms": expected.get("start_epoch_ms")}
    if (expected_identity["pid"] != pid
            or type(expected_identity["start_epoch_ms"]) is not int
            or expected_identity["start_epoch_ms"] <= 0):
        raise CandidateError("expected birth identity does not bind the exact task-owned Popen")
    if return_code is not None:
        # poll() on the exact Popen reaps its child. A spontaneously exited
        # owned server needs no signal; still require its formerly bound port
        # to be listener-free before declaring teardown verified.
        after = _lsof_listeners(port)
        if after:
            raise CandidateError("exited exact child left a listener on its owned port")
        return {"status": "STOPPED_AND_REAPED", "pid": pid,
                "start_epoch_ms": expected_identity["start_epoch_ms"],
                "child_exit_confirmed": True, "child_reaped": True,
                "listener_absent": True, "spontaneous_exit": True,
                "return_code": return_code}
    observed = _process_identity(pid)
    if observed != expected_identity:
        raise CandidateError("process birth identity no longer matches exact Popen before stop")
    listeners = _lsof_listeners(port)
    if len(listeners) != 1 or listeners[0]["pid"] != pid or listeners[0]["endpoint"] != f"127.0.0.1:{port}":
        raise CandidateError("listener port lacks one exact 127.0.0.1 endpoint owned by this Popen")
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # This is the same task-owned Popen and matching birth, checked above.
        proc.kill()
        proc.wait(timeout=3.0)
    if proc.poll() is None:
        raise CandidateError("exact task-owned child remains live after terminate/kill and wait")
    after = _lsof_listeners(port)
    if after:
        raise CandidateError("listener port was not proven absent after exact child exit")
    return {"status": "STOPPED_AND_REAPED", "pid": pid,
            "start_epoch_ms": expected["start_epoch_ms"],
            "child_exit_confirmed": proc.poll() is not None,
            "child_reaped": True, "listener_absent": True,
            "return_code": proc.returncode}


def _owned_process_receipt(name: str, proc: Any, identity: Mapping[str, Any] | None,
                           endpoint: Mapping[str, Any] | None,
                           cleanup: Mapping[str, Any] | None) -> dict[str, Any]:
    if proc is None:
        return {"resource": name, "status": "NOT_STARTED", "exact_popen_handle": False,
                "child_exit_confirmed": False, "child_reaped": False,
                "endpoint": None, "cleanup_result": dict(cleanup) if isinstance(cleanup, Mapping) else None}
    pid = getattr(proc, "pid", None)
    return_code = proc.poll()
    birth = identity.get("start_epoch_ms") if isinstance(identity, Mapping) else None
    identity_pid = identity.get("pid") if isinstance(identity, Mapping) else None
    identity_bound = (type(pid) is int and pid > 1 and identity_pid == pid
                      and type(birth) is int and birth > 0)
    sanitized_endpoint = None
    if isinstance(endpoint, Mapping):
        sanitized_endpoint = {key: endpoint[key] for key in
                              ("status", "host", "pid", "port", "endpoint",
                               "process_start_epoch_ms") if key in endpoint}
    if return_code is None:
        status = "LIVE_EXACT_IDENTITY_BOUND" if identity_bound else "LIVE_IDENTITY_UNVERIFIED"
    else:
        status = "EXITED_REAPED_EXACT_IDENTITY_BOUND" if identity_bound else "EXITED_REAPED_IDENTITY_UNVERIFIED"
    return {"resource": name, "status": status, "exact_popen_handle": True,
            "pid": pid,
            "process_identity": ({"pid": pid, "start_epoch_ms": birth}
                                 if identity_bound else None),
            "birth_identity_bound_to_popen_pid": identity_bound,
            "endpoint": sanitized_endpoint,
            "child_exit_confirmed": return_code is not None,
            "child_reaped": return_code is not None,
            "return_code": return_code,
            "cleanup_result": dict(cleanup) if isinstance(cleanup, Mapping) else None}


def _resource_ownership_receipt(*, server_proc: Any, server_identity: Mapping[str, Any] | None,
                                server_listener: Mapping[str, Any] | None,
                                control_proc: Any, control_identity: Mapping[str, Any] | None,
                                control_endpoint: Mapping[str, Any] | None,
                                worker_state: str, session: Mapping[str, Any] | None,
                                retirement_proof: Mapping[str, Any] | None,
                                process_cleanup: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "server": _owned_process_receipt("task_owned_comsol_server", server_proc,
            server_identity, server_listener, process_cleanup.get("server")),
        "control_daemon": _owned_process_receipt("task_owned_public_control_daemon", control_proc,
            control_identity, control_endpoint, process_cleanup.get("control_daemon")),
        "managed_worker": {
            "status": worker_state,
            "project_id": session.get("project_id") if isinstance(session, Mapping) else None,
            "session_id": session.get("session_id") if isinstance(session, Mapping) else None,
            "worker_instance_id": session.get("worker_instance_id") if isinstance(session, Mapping) else None,
            "connected_worker_epoch": session.get("worker_epoch") if isinstance(session, Mapping) else None,
            "retirement_proof": dict(retirement_proof) if isinstance(retirement_proof, Mapping) else None,
        },
    }


def _await_control_endpoint(home: Path, proc: subprocess.Popen[Any], expected_identity: Mapping[str, Any],
                            timeout_s: float = 20.0) -> dict[str, Any]:
    endpoint_path = home / "control.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise CandidateError(f"task-owned control daemon exited {proc.returncode} before readiness")
        if endpoint_path.is_file() and not endpoint_path.is_symlink():
            endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
            if (endpoint.get("pid") != proc.pid
                    or endpoint.get("process_start_epoch_ms") != expected_identity.get("start_epoch_ms")
                    or type(endpoint.get("port")) is not int or not 1 <= endpoint["port"] <= 65535):
                raise CandidateError("control endpoint does not bind the exact owned daemon birth identity")
            rows = _lsof_listeners(endpoint["port"])
            if (len(rows) != 1 or rows[0]["pid"] != proc.pid
                    or rows[0]["endpoint"] != f"127.0.0.1:{endpoint['port']}"):
                raise CandidateError("control listener is not uniquely owned by the task Popen")
            return endpoint
        time.sleep(0.1)
    raise TimeoutError("task-owned public control daemon did not publish its exact endpoint")


def _start_control_daemon(work: Path, evidence: Path, server: Any, env: Mapping[str, str],
                          on_owned_child: Callable[[Any, Any, Any, Any], None]):
    home = Path(env["COMSOL_SERVER_MCP_HOME"]) / "control-private"
    archive_root = Path(__file__).resolve().parents[1]
    if not getattr(sys.flags, "no_site", False):
        raise CandidateError("the setup runner must remain under -S before starting its public daemon")
    if Path(sys.executable).resolve(strict=True) != EXPECTED_PYTHON.resolve(strict=True):
        raise CandidateError("public control daemon must be launched from the exact reviewed Python 3.12")
    expected_pythonpath = os.pathsep.join((str(archive_root.resolve()),
                                           str(EXPLICIT_SITE_PACKAGES.resolve(strict=True))))
    if env.get("PYTHONPATH") != expected_pythonpath:
        raise CandidateError("control daemon PYTHONPATH must contain only the archive and explicit venv site-packages")
    if env.get("PYTHONHOME") or env.get("PYTHONSTARTUP"):
        raise CandidateError("control daemon environment must not override Python home or startup hooks")
    if home.exists():
        if home.is_symlink() or not home.is_dir() or any(home.iterdir()):
            raise CandidateError("owned control home exists but is not a new empty private directory")
    else:
        home.mkdir(parents=True, exist_ok=False)
    manifest_path = archive_root / ".w23_published_archive_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CandidateError("control daemon source root lacks the exact published archive manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_commit = manifest.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit:
        raise CandidateError("control daemon source manifest omitted its published base commit")
    source_binding = _source_inventory(archive_root, base_commit)
    daemon_module = source_binding.get("source_files", {}).get("comsol_mcp/_control_daemon.py")
    if not isinstance(daemon_module, Mapping):
        raise CandidateError("control daemon source hash is absent from the frozen archive closure")
    log_path = evidence / "control-daemon.log"
    stream = log_path.open("ab", buffering=0)
    command = [str(EXPECTED_PYTHON), "-S", "-m", "comsol_mcp._control_daemon", "--home", str(home)]
    daemon_env = dict(env)
    daemon_env.pop("PYTHONHOME", None)
    daemon_env.pop("PYTHONSTARTUP", None)
    daemon_env["PYTHONDONTWRITEBYTECODE"] = "1"
    _write_json(evidence / "control_daemon_launch.json", {
        "status": "Popen_NOT_YET_CONFIRMED",
        "command": command,
        "cwd": str(archive_root.resolve(strict=True)),
        "archive_base_commit": base_commit,
        "archive_manifest_sha256": manifest.get("manifest_sha256"),
        "archive_source_closure_sha256": source_binding["source_closure_sha256"],
        "archive_source_file_count": len(source_binding["source_files"]),
        "control_daemon_source_sha256": daemon_module["sha256"],
        "explicit_pythonpath": daemon_env["PYTHONPATH"],
        "explicit_site_packages": str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)),
        "python_no_site_switch": "-S" in command,
        "pythonhome_override_absent": "PYTHONHOME" not in daemon_env,
        "pythonstartup_hook_absent": "PYTHONSTARTUP" not in daemon_env,
        "bytecode_writes_disabled": daemon_env["PYTHONDONTWRITEBYTECODE"] == "1",
        "runner_path_hooks": [_identity_name(item) for item in sys.path_hooks],
        "runner_meta_path_finders": [_identity_name(item) for item in sys.meta_path],
        "editable_fallback_surfaces": _assert_no_editable_fallback(),
    })
    proc = subprocess.Popen(command, cwd=str(archive_root), env=daemon_env, stdin=subprocess.DEVNULL,
                            stdout=stream, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    on_owned_child(proc, None, None, stream)
    identity = _process_identity(proc.pid)
    if identity is None:
        raise CandidateError("task-owned public control daemon birth identity is unavailable")
    on_owned_child(proc, identity, None, stream)
    endpoint = _await_control_endpoint(home, proc, identity)
    on_owned_child(proc, identity, endpoint, stream)
    _write_json(evidence / "control_daemon_launch.json", {
        "status": "Popen_BIRTH_ENDPOINT_BOUND",
        "command": command,
        "cwd": str(archive_root.resolve(strict=True)),
        "archive_base_commit": base_commit,
        "archive_manifest_sha256": manifest.get("manifest_sha256"),
        "archive_source_closure_sha256": source_binding["source_closure_sha256"],
        "archive_source_file_count": len(source_binding["source_files"]),
        "control_daemon_source_sha256": daemon_module["sha256"],
        "explicit_pythonpath": daemon_env["PYTHONPATH"],
        "explicit_site_packages": str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)),
        "python_no_site_switch": "-S" in command,
        "pythonhome_override_absent": "PYTHONHOME" not in daemon_env,
        "pythonstartup_hook_absent": "PYTHONSTARTUP" not in daemon_env,
        "bytecode_writes_disabled": daemon_env["PYTHONDONTWRITEBYTECODE"] == "1",
        "popen_pid": proc.pid,
        "birth_identity": identity,
        "endpoint": {key: value for key, value in endpoint.items() if key != "token"},
        "runner_path_hooks": [_identity_name(item) for item in sys.path_hooks],
        "runner_meta_path_finders": [_identity_name(item) for item in sys.meta_path],
        "editable_fallback_surfaces": _assert_no_editable_fallback(),
    })
    return proc, identity, endpoint, stream


def _connect_inspect(dispatcher: PublicDispatchAdapter, project_id: str,
                     session: Mapping[str, Any]) -> dict[str, Any]:
    request_id, key = _new_ids("session-inspect-connected")
    response = dispatcher.dispatch({"operation": "session.inspect",
        "arguments": {"session_id": session["session_id"]},
        "execution": {"project_id": project_id, "request_id": request_id,
                      "idempotency_key": key, "rpc_timeout_s": 20.0}})
    data = response.get("data")
    lifecycle = data.get("lifecycle") if isinstance(data, Mapping) else None
    binding = data.get("worker_binding") if isinstance(data, Mapping) else None
    if (response.get("success") is not True or not isinstance(lifecycle, Mapping)
            or not isinstance(binding, Mapping) or data.get("runtime_live") is not True
            or lifecycle.get("state") != "CONNECTED"
            or lifecycle.get("worker_instance_id") != session["worker_instance_id"]
            or lifecycle.get("worker_epoch") != session["worker_epoch"]
            or binding.get("worker_instance_id") != session["worker_instance_id"]
            or binding.get("worker_epoch") != session["worker_epoch"]):
        raise CandidateError("public session.inspect does not prove the connected Worker identity")
    return dict(response)


def _import_published_runtime_closure(repo: Path) -> dict[str, str]:
    """Import runtime modules only after archive root wins sys.path priority."""
    import importlib

    root = repo.resolve(strict=True)
    _assert_no_editable_fallback()
    if str(root) in sys.path:
        sys.path.remove(str(root))
    sys.path.insert(0, str(root))
    names = [
        "comsol_mcp._control_client", "comsol_mcp._control_daemon",
        "comsol_mcp._execution_contract", "comsol_mcp._operation_store",
        "comsol_mcp._managed_backend", "comsol_mcp._project_authority",
        "comsol_mcp._session_context", "comsol_mcp._session_lifecycle",
        "comsol_mcp._runtime_installation", "comsol_mcp._java_worker",
        "comsol_mcp._g2_isolation", "comsol_mcp._platform_process",
        "comsol_mcp._g2_registry", "tools.run_native_resume_smoke",
        "tools.w23_full3d", "tools.w23_full3d_science",
    ]
    origins: dict[str, str] = {}
    for name in names:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            raise CandidateError(f"runtime module has no file-backed source origin: {name}")
        resolved = Path(origin).resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise CandidateError(f"runtime module escaped the reviewed archive: {name} -> {resolved}") from exc
        origins[name] = str(resolved)
    return origins


def _audit_loaded_project_modules(repo: Path,
                                  modules: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Audit every loaded project namespace module and its full search path."""
    root = repo.resolve(strict=True)
    table = sys.modules if modules is None else modules
    selected: dict[str, str] = {}
    for name, module in table.items():
        if name.split(".", 1)[0] not in {"comsol_mcp", "tools"}:
            continue
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            spec = getattr(module, "__spec__", None)
            origin = getattr(spec, "origin", None) if spec is not None else None
        package_path = getattr(module, "__path__", None)
        if package_path is not None:
            paths = [Path(item).resolve(strict=True) for item in package_path]
            if not paths:
                raise CandidateError(f"loaded project package has an empty search path: {name}")
            for package_root in paths:
                try:
                    package_root.relative_to(root)
                except ValueError as exc:
                    raise CandidateError(f"loaded project package search path escaped the archive: {name} -> {package_root}") from exc
            if name in {"comsol_mcp", "tools"}:
                expected = (root / name).resolve(strict=True)
                if paths != [expected]:
                    raise CandidateError(f"loaded project namespace has an unexpected search path: {name}")
        if isinstance(origin, str) and origin not in {"built-in", "frozen", "namespace"}:
            resolved = Path(origin).resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise CandidateError(f"loaded project module escaped the archive: {name} -> {resolved}") from exc
            selected[name] = str(resolved)
        elif package_path is not None and origin in {None, "namespace"}:
            selected[name] = "namespace:" + ",".join(str(item) for item in paths)
        else:
            raise CandidateError(f"loaded project module lacks a file-backed archive origin: {name}")
    return selected


def _archive_import_audit(repo: Path) -> dict[str, Any]:
    import importlib

    root = repo.resolve(strict=True)
    _assert_no_editable_fallback()
    origins = _import_published_runtime_closure(root)
    package = importlib.import_module("comsol_mcp")
    package_paths = [str(Path(item).resolve(strict=True)) for item in package.__path__]
    expected_package_path = str((root / "comsol_mcp").resolve(strict=True))
    if package_paths != [expected_package_path]:
        raise CandidateError("COMSOL MCP package search path is not confined to the isolated archive")
    module_origins = _audit_loaded_project_modules(root)
    if any(not origin.startswith("namespace:") and not Path(origin).is_relative_to(root)
           for origin in module_origins.values()):
        raise CandidateError("loaded project module escaped the isolated archive")
    tools_module = importlib.import_module("tools")
    tools_paths = [str(Path(item).resolve(strict=True)) for item in tools_module.__path__]
    expected_tools_path = str((root / "tools").resolve(strict=True))
    if tools_paths != [expected_tools_path]:
        raise CandidateError("W23 tools namespace search path is not confined to the isolated archive")
    _assert_no_editable_fallback()
    editable_fallback_surfaces = _assert_no_editable_fallback()
    editable_path_hook = any("editable" in _identity_name(item).lower() for item in sys.path_hooks)
    return {"cwd": str(Path.cwd().resolve(strict=True)),
            "python_sys_path": [str(Path(item or Path.cwd()).resolve()) for item in sys.path],
            "site_processing_disabled": bool(getattr(sys.flags, "no_site", False)),
            "python_no_site_flag": bool(getattr(sys.flags, "no_site", False)),
            "site_module_loaded": "site" in sys.modules,
            "bytecode_writes_disabled": bool(sys.dont_write_bytecode),
            "sys_path_hooks": [_identity_name(item) for item in sys.path_hooks],
            "meta_path_finders": [_identity_name(item) for item in sys.meta_path],
            "explicit_site_packages": str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)),
            "comsol_mcp_package_paths": package_paths,
            "tools_package_paths": tools_paths,
            "module_origins": module_origins,
            "preselected_runtime_module_origins": origins,
            "all_audited_imports_from_archive": True,
            "editable_fallback_surfaces": editable_fallback_surfaces,
            "editable_fallback_used": bool(editable_fallback_surfaces),
            "editable_path_hook_loaded": editable_path_hook}


def _stop_server_adapter(server: Any, expected_identity: Mapping[str, Any], port: int) -> dict[str, Any]:
    if server.proc is None:
        return {"status": "STOPPED_AND_REAPED", "pid": expected_identity["pid"],
                "start_epoch_ms": expected_identity["start_epoch_ms"],
                "child_exit_confirmed": True, "child_reaped": True,
                "listener_absent": True, "not_started": True}
    return _stop_owned_process(server.proc, expected_identity, port=port)


def execute_candidate(*, repo: Path, evidence: Path, reviewed_sha256: str,
                      install_root: Path = INSTALL_ROOT, jdk_home: Path = JAVA11) -> dict[str, Any]:
    python_isolation = _configure_archive_python(repo)
    freeze = verify_candidate(repo=repo, evidence=evidence, reviewed_sha256=reviewed_sha256)
    campaign_profile = freeze["campaign_profile"]
    if freeze.get("source", {}).get("checkout_kind") != "git_archive":
        raise CandidateError("native setup may execute only from the frozen source-only git archive")
    if Path.cwd().resolve(strict=True) != repo.resolve(strict=True):
        raise CandidateError("native setup cwd must be the exact frozen git archive root")
    if sys.platform != "darwin":
        raise CandidateError("this native setup candidate is frozen for Darwin only")
    if freeze["runtime"]["install_root"] != str(install_root.resolve(strict=True)):
        raise CandidateError("COMSOL install root differs from the reviewed candidate")
    if freeze["runtime"]["jdk_home"] != str(jdk_home.resolve(strict=True)):
        raise CandidateError("external JDK 11 path differs from the reviewed candidate")
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise CandidateError(f"use exact Python executable {EXPECTED_PYTHON}")
    if (freeze.get("python_isolation", {}).get("site_processing_disabled") is not True
            or freeze.get("python_isolation", {}).get("explicit_site_packages")
               != python_isolation.get("explicit_site_packages")):
        raise CandidateError("runtime Python no-site/explicit-dependency policy differs from the frozen candidate")
    if install_root.resolve(strict=True) != INSTALL_ROOT.resolve(strict=True):
        raise CandidateError("NativeLoopbackServer is frozen to the exact reviewed COMSOL install root")
    if jdk_home.resolve(strict=True) != JAVA11.resolve(strict=True):
        raise CandidateError("NativeLoopbackServer is frozen to the exact reviewed external JDK 11 root")
    # Recheck the offline compile closure before a server process is born.
    if str(REPO) in sys.path:
        sys.path.remove(str(REPO))
    sys.path.insert(0, str(REPO))
    from comsol_mcp._java_worker import JavaWorkerPaths
    exact_paths = JavaWorkerPaths(install_root, jdk_home, project_root=repo)
    _classpath, manifest_sha, jar_count, jar_fingerprint = exact_paths.classpath()
    compile_receipt = freeze.get("compile", {})
    if (manifest_sha != compile_receipt.get("classpath_manifest_sha256")
            or jar_count != compile_receipt.get("classpath_jar_count")
            or jar_fingerprint != compile_receipt.get("classpath_jar_content_fingerprint_sha256")
            or exact_paths.comsol_version_info() != freeze["runtime"].get("comsol_version")
            or exact_paths.jdk_version_info() != freeze["runtime"].get("jdk_version")):
        raise CandidateError("current COMSOL/JDK classpath or version differs from offline candidate evidence")
    run_dir = evidence / ("native-bma-mapping-probe-run" if campaign_profile == "bma_mapping_probe"
                          else "native-bma-probe-run" if campaign_profile == "bma_probe"
                          else "native-setup-run")
    if run_dir.exists():
        raise CandidateError("native run evidence directory already exists")
    run_dir.mkdir(parents=True, exist_ok=False)
    work = Path("/private/tmp") / ("comsol-mcp-w23-full3d-work-" + uuid4().hex)
    if work.exists():
        raise CandidateError("unique private work directory unexpectedly exists")
    work.mkdir(parents=True, exist_ok=False)
    events_path = run_dir / "events.jsonl"
    process_cleanup: dict[str, Mapping[str, Any]] = {}

    def event(name: str, **payload: Any) -> None:
        row = {"at_utc": datetime.now(timezone.utc).isoformat(), "event": name, **payload}
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, default=str) + "\n")
            stream.flush(); os.fsync(stream.fileno())

    def stop_server() -> Mapping[str, Any]:
        nonlocal server_identity, server_port
        if server is None or server.proc is None:
            result = {"status": "NOT_STARTED", "child_started": False}
            process_cleanup["server"] = result
            return result
        exact_identity = server_identity
        exact_port = server_port
        if exact_identity is None and isinstance(server.process_identity, Mapping):
            exact_identity = _bind_native_server_identity(server, server_listener)
            server_identity = exact_identity
        if exact_port is None and type(server.port) is int:
            exact_port = server.port
            server_port = exact_port
        try:
            if exact_identity is None or exact_port is None:
                raise CandidateError("server Popen exists without exact birth/port proof")
            result = _stop_server_adapter(server, exact_identity, exact_port)
            process_cleanup["server"] = result
            return result
        except BaseException as exc:
            process_cleanup["server"] = {"status": "STOP_REFUSED", "error_type": type(exc).__name__,
                                          "error": str(exc)}
            raise

    def stop_control() -> Mapping[str, Any]:
        nonlocal daemon_endpoint
        if daemon_proc is None:
            result = {"status": "NOT_STARTED", "child_started": False}
            process_cleanup["control_daemon"] = result
            return result
        try:
            if daemon_identity is None:
                raise CandidateError("exact task-owned control Popen birth is unavailable")
            endpoint = daemon_endpoint
            if endpoint is None:
                if daemon_proc.poll() is None:
                    rows = _lsof_process_listeners(daemon_proc.pid)
                    if (len(rows) != 1 or rows[0]["pid"] != daemon_proc.pid
                            or not rows[0]["endpoint"].startswith("127.0.0.1:")):
                        raise CandidateError("control endpoint readback absent and exact Popen has no unique loopback listener")
                    port_text = rows[0]["endpoint"].rsplit(":", 1)[-1]
                    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
                        raise CandidateError("control Popen listener has an invalid loopback port")
                    endpoint = {"status": "DISCOVERED_FROM_EXACT_POPEN_LISTENER",
                                "pid": daemon_proc.pid, "port": int(port_text)}
                    daemon_endpoint = endpoint
                else:
                    # An exited exact task Popen needs no signal. poll() has
                    # reaped it; check the same PID has no surviving listener.
                    rows = _lsof_process_listeners(daemon_proc.pid)
                    if rows:
                        raise CandidateError("control Popen exited but a listener remains on its process identity")
                    result = {"status": "STOPPED_AND_REAPED", "pid": daemon_proc.pid,
                              "start_epoch_ms": daemon_identity["start_epoch_ms"],
                              "child_exit_confirmed": True, "child_reaped": True,
                              "listener_absent": True, "spontaneous_exit": True,
                              "return_code": daemon_proc.returncode}
                    process_cleanup["control_daemon"] = result
                    return result
            result = _stop_owned_process(daemon_proc, daemon_identity, port=endpoint["port"])
            process_cleanup["control_daemon"] = result
            return result
        except BaseException as exc:
            process_cleanup["control_daemon"] = {"status": "STOP_REFUSED", "error_type": type(exc).__name__,
                                                 "error": str(exc)}
            raise

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": ("RUNNING_BMA_MAPPING_PROBE_CANDIDATE" if campaign_profile == "bma_mapping_probe"
                   else "RUNNING_BMA_PROBE_CANDIDATE" if campaign_profile == "bma_probe"
                   else "RUNNING_SETUP_ONLY"),
        "campaign_profile": campaign_profile, "evidence_dir": str(run_dir),
        "work_dir": str(work), "candidate_sha256": reviewed_sha256,
        "native_scientific_result": "NOT_RUN", "study_run_calls": 0, "solver_calls": 0,
        "solver_call_attempts": 0,
        "field_sample_calls": 0,
        "field_sample_attempts": 0,
        "field_sample_readbacks_validated": 0,
        "mode_producer_lineage": "UNVERIFIED", "numeric_port_mode_field_mapping": "UNVERIFIED",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_module_origins": {},
    }
    server = None
    server_identity: dict[str, Any] | None = None
    server_port: int | None = None
    server_listener: dict[str, Any] | None = None
    server_birth_mono: float | None = None
    daemon_proc = None
    daemon_identity: dict[str, Any] | None = None
    daemon_endpoint: dict[str, Any] | None = None
    daemon_log = None
    dispatcher: PublicDispatchAdapter | None = None
    project: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    worker_state = "NEVER_DISPATCHED"
    ambiguous = False
    route_outcomes: list[dict[str, Any]] = []
    cleanup: list[str] = []
    worker_retirement_proof: dict[str, Any] | None = None

    try:
        env = dict(os.environ)
        env.update({
            "COMSOL_ROOT": str(install_root), "COMSOL_JAVA_HOME": str(jdk_home),
            "JAVA_HOME": str(jdk_home), "COMSOL_PREFS_DIR": str(work / "runtime" / "prefs"),
            "COMSOL_PROJECT_ROOT": str(work / "project"),
            "COMSOL_SERVER_MCP_HOME": str(work / "mcp-home"),
            "COMSOL_MCP_TRUSTED_CODE": "1",
            "COMSOL_MCP_ISOLATION_RECEIPT": str(work / "isolation_receipt.json"),
            "COMSOL_SERVER_VERSION": "6.4.0.293",
            "PYTHONPATH": os.pathsep.join((str(repo.resolve()),
                                            str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)))),
        })
        # Importing _server freezes COMSOL_SERVER_MCP_HOME. Bind env first,
        # then import the published closure; never dispatch through a stale home.
        os.environ.update(env)
        origins = _import_published_runtime_closure(repo)
        import_audit = _archive_import_audit(repo)
        result["runtime_module_origins"] = origins
        result["runtime_import_audit"] = import_audit

        from comsol_mcp._control_client import control_home
        expected_control_home = (Path(env["COMSOL_SERVER_MCP_HOME"]) / "control-private").resolve()
        actual_control_home = control_home().resolve(strict=True)
        if actual_control_home != expected_control_home:
            raise CandidateError("first public control_home is not the exact task-owned daemon home")

        from tools.run_native_resume_smoke import NativeLoopbackServer
        from tools.w23_full3d import (
            bind_case_matrix, build_full3d_case_dispatch, build_full3d_fixture_dispatch,
            canonical_full3d_recipe, verify_recipe,
        )
        from tools.w23_full3d_science import (
            ManagedRouteOutcomeError, _check_full3d_fixture_readback, _java_action_readback,
            _updated_full3d_model_state, bind_full3d_model_create_response,
            bind_full3d_project_create_response, build_full3d_project_create_request,
            build_full3d_bma_probe_prepare_dispatch, build_full3d_bma_probe_run_dispatch,
            build_full3d_save_dispatch, build_full3d_solution_inventory_dispatch,
            dispatch_public_managed_route, stage_full3d_fixture_source,
            validate_full3d_bma_probe_preparation,
            validate_full3d_bma_probe_run_readback,
        )
        from comsol_mcp._runtime_installation import runtime_id_for_root

        dispatcher = PublicDispatchAdapter(run_dir,
            expected_control_home=expected_control_home, archive_root=repo)
        server = NativeLoopbackServer(work, run_dir, event_log=events_path)
        shadow = server.prepare_shadow()
        event("private_shadow_prepared", receipt=shadow)
        def capture_daemon_child(proc: Any, identity: Any, endpoint: Any, stream: Any) -> None:
            nonlocal daemon_proc, daemon_identity, daemon_endpoint, daemon_log
            daemon_proc, daemon_identity, daemon_endpoint, daemon_log = proc, identity, endpoint, stream

        daemon_proc, daemon_identity, daemon_endpoint, daemon_log = _start_control_daemon(
            work, run_dir, server, env, capture_daemon_child)
        dispatcher.bind_owned_control(daemon_endpoint, daemon_identity)
        event("public_control_daemon_ready", pid=daemon_proc.pid,
              process_identity=daemon_identity, endpoint={k: v for k, v in daemon_endpoint.items() if k != "token"})

        project_id_request, project_key = _new_ids("project-create")
        project_req = build_full3d_project_create_request(
            label=("W23 full-3D isolated BMA producer probe candidate"
                   if _is_bma_profile(campaign_profile) else "W23 full-3D setup-only candidate"),
            request_id=project_id_request,
            idempotency_key=project_key)
        project_result = dispatch_public_managed_route(
            dispatcher, project_req, label="project.create", timeout_s=30)
        project_response = _require_route_ok(project_result, "project.create")
        project = bind_full3d_project_create_response(
            project_response, authorized_container=str(server.project))
        result["project"] = project
        route_outcomes.append({"route": "project.create", "outcome": "SUCCEEDED",
                               "job_id": project_result.get("job_id")})

        listener = server.start_and_verify_listener()
        server_listener = listener
        if server.proc is None or type(server.port) is not int or not isinstance(server.process_identity, Mapping):
            raise CandidateError("task-owned server helper omitted Popen/port/birth proof")
        server_port = server.port
        server_identity = _bind_native_server_identity(server, listener)
        # Map exact OS birth epoch to monotonic time so startup/listener wait is
        # charged to the profile's frozen wall budget.
        server_birth_mono = time.monotonic() - max(0.0, time.time() - server_identity["start_epoch_ms"] / 1000.0)
        event("task_owned_server_listener_ready", listener=listener,
              server_identity=server_identity,
              server_identity_source="comsol_mcp._platform_process.process_identity",
              helper_birth_format=server.process_identity.get("birth"),
              budget_birth_epoch_ms=server_identity["start_epoch_ms"])

        def route(request: Mapping[str, Any], label: str, cap: int) -> dict[str, Any]:
            nonlocal ambiguous
            assert dispatcher is not None and server_birth_mono is not None
            wall_budget = float(freeze["budget"]["wall_clock_seconds_from_server_birth_including_cleanup"])
            cleanup_reserve = float(freeze["budget"]["reserved_cleanup_seconds"])
            remaining = wall_budget - (time.monotonic() - server_birth_mono)
            allowed = min(float(cap), remaining - cleanup_reserve)
            if allowed <= 0:
                raise RouteBudgetRefused("setup budget has reached the protected cleanup reserve")
            bounded_request = {**dict(request),
                "arguments": dict(request.get("arguments", {})),
                "execution": dict(request.get("execution", {}))}
            bounded_execution = bounded_request["execution"]
            bounded_execution.update({
                "execution_timeout_s": allowed,
                "queue_timeout_s": min(60.0, allowed),
                "rpc_timeout_s": min(30.0, allowed),
            })
            try:
                observed = dispatch_public_managed_route(
                    dispatcher, bounded_request, label=label, timeout_s=allowed, poll_interval_s=0.2)
            except ManagedRouteOutcomeError as exc:
                route_outcomes.append({"route": label, "outcome": exc.outcome,
                    "job_id": exc.job_id, "request_id": bounded_request.get("execution", {}).get("request_id"),
                    "retry_forbidden": exc.retry_forbidden})
                if exc.outcome == "UNKNOWN":
                    ambiguous = True
                raise
            observed["submitted_request"] = bounded_request
            route_outcomes.append({"route": label, "outcome": observed.get("outcome"),
                                   "job_id": observed.get("job_id")})
            if observed.get("outcome") == "UNKNOWN":
                ambiguous = True
            return observed

        connect_request = _public_connect_request(
            project_id=project["project_id"],
            runtime_id=runtime_id_for_root(install_root), port=server_port)
        worker_state = "UNKNOWN"  # Set before the one-shot birth request.
        connect_result = route(connect_request, "session.connect", 120)
        connect_response = _require_route_ok(connect_result, "session.connect")
        session = _session_identity(connect_response, project_id=project["project_id"],
                                    expected_port=server_port)
        worker_state = "CONNECTED"
        result["session"] = session
        event("public_managed_session_connected", identity=session)
        inspect_connected = _connect_inspect(dispatcher, project["project_id"], session)
        result["connected_session_inspect"] = inspect_connected

        model_create_request = _managed_model_request(project["project_id"], session)
        created = route(model_create_request, "model_create", 120)
        create_response = _require_route_ok(created, "model_create")
        model = bind_full3d_model_create_response(
            create_response, project_id=project["project_id"], session=session)
        recipe = canonical_full3d_recipe()
        recipe_check = verify_recipe(recipe)
        fixture_sha = _sha256_file(FIXTURE)
        staged = stage_full3d_fixture_source(FIXTURE,
            project_workspace=project["workspace"], expected_sha256=fixture_sha)

        request_id, key = _new_ids("fixture-build")
        build_request = build_full3d_fixture_dispatch(
            recipe, source_artifact=staged["source_artifact"],
            project_id=project["project_id"], model_ref=model["model_ref"],
            model_tag=model["model_tag"], revision=model["revision"],
            request_id=request_id, idempotency_key=key)
        built = route(build_request, "fixture_build", 540)
        build_response = _require_route_ok(built, "fixture build")
        model = _updated_full3d_model_state(build_response, prior_model=model)
        build_readback = _java_action_readback(build_response, "full3d_fixture_build")
        _check_full3d_fixture_readback(build_readback,
            recipe_sha256=recipe_check["recipe_sha256"], project_id=project["project_id"], model=model)
        build_mesh_readback = validate_native_mesh_readback(build_readback, phase="initial")
        configuration_readback = validate_native_mode_configuration(build_readback)

        plan = bind_case_matrix(recipe, project_id=project["project_id"],
            model_ref=model["model_ref"], model_tag=model["model_tag"],
            revision=model["revision"], experiment_id="w23-full3d-setup-only")
        baseline_rows = [case for case in plan["cases"] if case.get("factor") == "baseline"]
        if len(baseline_rows) != 1:
            raise CandidateError("full-3D immutable plan must contain exactly one baseline case")
        request_id, key = _new_ids("baseline-apply")
        apply_request = build_full3d_case_dispatch(
            plan, baseline_rows[0], source_artifact=staged["source_artifact"],
            request_id=request_id, idempotency_key=key)
        applied = route(apply_request, "baseline_apply", 540)
        apply_response = _require_route_ok(applied, "baseline case apply")
        model = _updated_full3d_model_state(apply_response, prior_model=model)
        apply_readback = _java_action_readback(apply_response, "full3d_baseline_apply")
        _check_full3d_fixture_readback(apply_readback,
            recipe_sha256=recipe_check["recipe_sha256"], project_id=project["project_id"],
            model=model, case=baseline_rows[0])
        apply_mesh_readback = validate_native_mesh_readback(
            apply_readback, phase="apply_case", previous=build_mesh_readback)
        if (build_readback.get("study_or_solver_invoked") is not False
                or apply_readback.get("study_or_solver_invoked") is not False):
            raise CandidateError("fixture builder reported a Study/solver invocation")

        if _is_bma_profile(freeze["campaign_profile"]):
            request_id, key = _new_ids("bma-probe-prepare")
            prepare_request = build_full3d_bma_probe_prepare_dispatch(
                source_artifact=staged["source_artifact"], project_id=project["project_id"],
                model_ref=model["model_ref"], model_tag=model["model_tag"],
                revision=model["revision"], request_id=request_id, idempotency_key=key)
            prepare_result = route(prepare_request, "bma_probe_prepare", 90)
            prepare_response = _require_route_ok(prepare_result, "BMA probe preparation")
            model = _updated_full3d_model_state(prepare_response, prior_model=model)
            prepare_readback = _java_action_readback(prepare_response, "full3d_bma_probe_prepare")
            result["bma_probe_preparation_readback"] = prepare_readback
            preparation = validate_full3d_bma_probe_preparation(
                prepare_readback, project_id=project["project_id"],
                model_tag=model["model_tag"], model_ref=model["model_ref"])
            result["bma_probe_preparation"] = {
                "operation": prepare_result, "readback": prepare_readback,
                "validated": preparation}
            event("isolated_bma_output_probe_prepared",
                  solver_sequence=preparation["solver_sequence"],
                  parent_study=preparation["probe_study"],
                  solution_state=preparation["pre_solve_solution_state"])

            request_id, key = _new_ids("bma-probe-runAll")
            run_request = build_full3d_bma_probe_run_dispatch(
                source_artifact=staged["source_artifact"],
                solver_sequence_tag=preparation["solver_sequence"]["tag"],
                project_id=project["project_id"], model_ref=model["model_ref"],
                model_tag=model["model_tag"], revision=model["revision"],
                request_id=request_id, idempotency_key=key)
            result["solver_call_attempts"] = 1
            result["bma_probe_run_request"] = run_request
            try:
                bma_run_result = route(run_request, "bma_probe_solver_sequence_runAll", 600)
            except ManagedRouteOutcomeError as dispatch_exc:
                if dispatch_exc.outcome == "UNKNOWN":
                    ambiguous = True
                route_outcomes.append({"route": "bma_probe_solver_sequence_runAll",
                    "outcome": dispatch_exc.outcome, "job_id": dispatch_exc.job_id,
                    "request_id": request_id, "retry_forbidden": dispatch_exc.retry_forbidden})
                result["bma_probe_run_dispatch_failure"] = _bma_run_failure_evidence(
                    run_request, dispatch_exc)
                raise
            bma_run_response = _require_route_ok(bma_run_result, "BMA producer sequence runAll")
            result["solver_calls"] = 1
            result["native_scientific_result"] = "BMA_PROBE_RUN_RETURNED_FULL3D_OVERLAP_NOT_EVALUATED"
            result["bma_probe_run_operation"] = bma_run_result
            result["bma_probe_run_response"] = bma_run_response
            model = _updated_full3d_model_state(bma_run_response, prior_model=model)
            bma_run_readback = _java_action_readback(bma_run_response, "full3d_bma_probe_run")
            result["bma_probe_run_readback"] = bma_run_readback
            producer_proof = validate_full3d_bma_probe_run_readback(
                bma_run_readback, preparation=preparation,
                project_id=project["project_id"], model_tag=model["model_tag"],
                model_ref=model["model_ref"], run_request=run_request,
                route_result=bma_run_result)
            result.update({
                "solver_calls": 1, "study_run_calls": 0,
                "native_scientific_result": "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED",
                "mode_producer_lineage": producer_proof["producer_step_binding"],
                "numeric_port_mode_field_mapping": "UNVERIFIED",
                "bma_probe_run": {"request": run_request, "operation": bma_run_result,
                    "readback": bma_run_readback, "validated": producer_proof},
            })
            event("isolated_bma_output_probe_solved",
                  producer_status=producer_proof["status"],
                  solution_pairs=producer_proof["eigensolution_solution_pairs"],
                  field_mapping_status=producer_proof["numeric_port_mode_field_mapping"])

            if campaign_profile == "bma_mapping_probe":
                mapping = _execute_bma_basis_mapping_stage(
                    route, result=result, project_id=project["project_id"], model=model,
                    source_artifact=staged["source_artifact"], preparation=preparation,
                    producer_evidence=producer_proof,
                    producer_route_evidence={
                        "preparation": preparation, "request": run_request,
                        "route_result": bma_run_result, "readback": bma_run_readback},
                    apply_readback=apply_readback,
                    baseline_case=baseline_rows[0], staged_source_proof=staged,
                    execution_owner={
                        "candidate_owned_server": True, "gui_attached": False,
                        "project_id": project["project_id"],
                        "server_process_identity": server_identity,
                        "listener": server_listener,
                        "session_identity": session,
                        "model_ref": model["model_ref"],
                    })
                model = mapping["model"]
                result.update({"model": model,
                    "native_scientific_result": "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED",
                    "mode_producer_lineage": producer_proof["producer_step_binding"],
                    "numeric_port_mode_field_mapping": "UNVERIFIED"})
                event("paired_bma_basis_fields_read",
                      sample_count=len(mapping["evidence"].get("field_samples", [])),
                      field_mapping_status="UNVERIFIED",
                      basis_ordinals=[row.get("basis_ordinal") for row in mapping["evidence"].get("field_samples", [])])

        request_id, key = _new_ids("solution-inventory")
        inventory_request = build_full3d_solution_inventory_dispatch(
            source_artifact=staged["source_artifact"], project_id=project["project_id"],
            model_ref=model["model_ref"], model_tag=model["model_tag"],
            revision=model["revision"], request_id=request_id, idempotency_key=key)
        inventory_result = route(inventory_request, "solution_inventory", 90)
        inventory_response = _require_route_ok(inventory_result, "solution inventory")
        inventory = _java_action_readback(inventory_response, "full3d_solution_inventory")
        model = _updated_full3d_model_state(inventory_response, prior_model=model)
        if inventory.get("model_tag") != model["model_tag"]:
            raise CandidateError("solution inventory does not bind the current managed model")
        inventory_configuration = validate_native_mode_configuration(inventory, inventory=True)

        mph_name = ("w23_full3d_bma_mapping_probe.mph" if campaign_profile == "bma_mapping_probe"
                    else "w23_full3d_bma_probe.mph" if campaign_profile == "bma_probe"
                    else "w23_full3d_setup_only.mph")
        mph_path = Path(project["workspace"]) / mph_name
        request_id, key = _new_ids("model-save")
        save_request = build_full3d_save_dispatch(
            source_artifact=staged["source_artifact"], path=str(mph_path),
            project_id=project["project_id"], model_ref=model["model_ref"],
            model_tag=model["model_tag"], revision=model["revision"],
            request_id=request_id, idempotency_key=key)
        saved = route(save_request, "model_save", 90)
        save_response = _require_route_ok(saved, "model save")
        save_readback = _java_action_readback(save_response, "full3d_model_save")
        model = _updated_full3d_model_state(save_response, prior_model=model)
        resolved_workspace = Path(project["workspace"]).resolve(strict=True)
        resolved_mph = mph_path.resolve(strict=True)
        if (resolved_mph.parent != resolved_workspace or not resolved_mph.is_file()
                or resolved_mph.stat().st_size <= 0):
            raise CandidateError("saved MPH is not a nonempty file in the authoritative project workspace")
        saved_artifact = {"path": str(resolved_mph), "bytes": resolved_mph.stat().st_size,
                          "sha256": _sha256_file(resolved_mph), "readback": save_readback}
        result.update({
            "status": ("BMA_MAPPING_PROBE_NATIVE_SAMPLED_MAPPING_UNVERIFIED"
                       if campaign_profile == "bma_mapping_probe" else
                       "BMA_PROBE_NATIVE_SOLVED_FULL3D_SCIENCE_NOT_EVALUATED"
                       if campaign_profile == "bma_probe" else "SETUP_COMPLETE_NATIVE_SOLVE_NOT_RUN"),
            "model": model, "staged_fixture": staged,
            "recipe_sha256": recipe_check["recipe_sha256"],
            "build_readback": build_readback, "baseline_readback": apply_readback,
            "configuration_readback": configuration_readback,
            "build_mesh_readback": build_mesh_readback, "apply_mesh_readback": apply_mesh_readback,
            "inventory_configuration_readback": inventory_configuration,
            "solution_inventory": inventory, "saved_mph": saved_artifact,
            "study_run_calls": 0,
            "numeric_port_mode_field_mapping": "UNVERIFIED"})
        if campaign_profile == "setup_only":
            result.update({"solver_calls": 0, "native_scientific_result": "NOT_RUN",
                           "mode_producer_lineage": "UNVERIFIED"})
        elif campaign_profile == "bma_probe":
            result.update({"solver_calls": 1,
                "native_scientific_result": "BMA_PROBE_ONLY_FULL3D_OVERLAP_NOT_EVALUATED"})
        else:
            result.update({"solver_calls": 1,
                "native_scientific_result": "BMA_TWO_BASIS_FIELDS_SAMPLED_FULL3D_OVERLAP_NOT_EVALUATED",
                "field_mapping_status": "UNVERIFIED"})

        ledger = _job_ledger_terminal(dispatcher, project["project_id"])
        result["pre_retirement_job_ledger"] = {
            "count": ledger["count"], "all_terminal": ledger["all_terminal"],
            "statuses": [job.get("status") for job in ledger["jobs"] if isinstance(job, Mapping)]}
        if not ledger["all_terminal"]:
            raise CandidateError("public job ledger contains active or UNKNOWN project work; cleanup is held")
        request_id, key = _new_ids("worker-retirement")
        retirement_req = build_disconnect_request(
            project_id=project["project_id"], session_id=session["session_id"],
            request_id=request_id, idempotency_key=key)
        detached_epoch: int | None = None

        def disconnect() -> Mapping[str, Any]:
            nonlocal detached_epoch, ambiguous, worker_state, worker_retirement_proof
            observed = dispatch_public_managed_route(
                dispatcher, retirement_req, label="session.disconnect.retire_worker",
                timeout_s=90, poll_interval_s=0.2)
            if observed.get("outcome") == "UNKNOWN":
                ambiguous = True
            response = _require_route_ok(observed, "session.disconnect.retire_worker")
            data = response.get("data")
            if not isinstance(data, Mapping):
                raise CleanupRefused("worker_retirement", "disconnect response lacks lifecycle readback", [])
            proof = validate_retirement_response(response,
                project_id=project["project_id"], session_id=session["session_id"],
                worker_instance_id=session["worker_instance_id"],
                connected_epoch=session["worker_epoch"])
            detached_epoch = proof["worker_epoch"]
            worker_retirement_proof = proof
            # Prevent exception recovery from issuing another retirement if a
            # later inspect or exact server cleanup fails.
            worker_state = "RETIRED"
            return response

        def inspect() -> Mapping[str, Any]:
            request_id2, key2 = _new_ids("session-inspect-retired")
            return dispatcher.dispatch({"operation": "session.inspect",
                "arguments": {"session_id": session["session_id"]},
                "execution": {"project_id": project["project_id"],
                              "request_id": request_id2, "idempotency_key": key2,
                              "rpc_timeout_s": 20.0}})

        cleanup = orchestrate_cleanup(worker_state="CONNECTED",
            project_id=project["project_id"], session_id=session["session_id"],
            worker_instance_id=session["worker_instance_id"],
            connected_epoch=session["worker_epoch"], detached_epoch=detached_epoch,
            all_project_jobs_terminal=ledger["all_terminal"], disconnect=disconnect,
            inspect=inspect, stop_server=stop_server, stop_control=stop_control)
        result["cleanup"] = cleanup
        worker_state = "RETIRED"
        result["status"] = ("BMA_MAPPING_PROBE_COMPLETE_CLEANUP_VERIFIED_MAPPING_UNVERIFIED"
                             if campaign_profile == "bma_mapping_probe" else
                             "BMA_PROBE_COMPLETE_CLEANUP_VERIFIED_FULL3D_SCIENCE_NOT_RUN"
                             if campaign_profile == "bma_probe" else
                             "SETUP_COMPLETE_CLEANUP_VERIFIED_SOLVE_NOT_RUN")
        return result
    except BaseException as exc:
        if _is_bma_profile(campaign_profile) and result.get("solver_call_attempts") == 1 and result.get("solver_calls") == 0:
            result["solver_calls"] = "UNKNOWN"
        result["status"] = "UNKNOWN_PRESERVE_OWNED_RESOURCES" if ambiguous or worker_state == "UNKNOWN" else "SETUP_FAILED"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        result["route_outcomes"] = route_outcomes
        result["cleanup"] = cleanup
        if worker_state == "CONNECTED" and not ambiguous and project and session and dispatcher:
            try:
                ledger = _job_ledger_terminal(dispatcher, project["project_id"])
                if ledger["all_terminal"]:
                    request_id, key = _new_ids("failure-cleanup-retirement")
                    req = build_disconnect_request(project_id=project["project_id"],
                        session_id=session["session_id"], request_id=request_id, idempotency_key=key)
                    detached_epoch: int | None = None

                    def disconnect_failure() -> Mapping[str, Any]:
                        nonlocal detached_epoch, worker_state
                        outcome = dispatch_public_managed_route(dispatcher, req,
                            label="failure_cleanup_retirement", timeout_s=90, poll_interval_s=0.2)
                        if outcome.get("outcome") == "UNKNOWN":
                            raise CleanupRefused("worker_retirement", "disconnect outcome is UNKNOWN", cleanup)
                        response = _require_route_ok(outcome, "failure cleanup retirement")
                        proof = validate_retirement_response(response,
                            project_id=project["project_id"], session_id=session["session_id"],
                            worker_instance_id=session["worker_instance_id"],
                            connected_epoch=session["worker_epoch"])
                        detached_epoch = proof["worker_epoch"]
                        worker_state = "RETIRED"
                        return response

                    def inspect_failure() -> Mapping[str, Any]:
                        rid, idem = _new_ids("failure-cleanup-inspect")
                        return dispatcher.dispatch({"operation": "session.inspect",
                            "arguments": {"session_id": session["session_id"]},
                            "execution": {"project_id": project["project_id"],
                                          "request_id": rid, "idempotency_key": idem}})

                    cleanup = orchestrate_cleanup(worker_state="CONNECTED",
                        project_id=project["project_id"], session_id=session["session_id"],
                        worker_instance_id=session["worker_instance_id"],
                        connected_epoch=session["worker_epoch"], detached_epoch=detached_epoch,
                        all_project_jobs_terminal=True, disconnect=disconnect_failure,
                        inspect=inspect_failure,
                        stop_server=stop_server, stop_control=stop_control)
                    result["cleanup"] = cleanup
                    result["status"] = ("BMA_MAPPING_PROBE_FAILED_CLEANUP_VERIFIED"
                                         if campaign_profile == "bma_mapping_probe" else
                                         "BMA_PROBE_FAILED_CLEANUP_VERIFIED"
                                         if campaign_profile == "bma_probe" else
                                         "SETUP_FAILED_CLEANUP_VERIFIED")
            except BaseException as cleanup_exc:
                result["cleanup_error"] = {"type": type(cleanup_exc).__name__,
                                            "message": str(cleanup_exc),
                                            "completed": getattr(cleanup_exc, "completed", [])}
                result["status"] = "UNKNOWN_PRESERVE_OWNED_RESOURCES"
        elif worker_state == "NEVER_DISPATCHED":
            try:
                ledger_ok = True
                ledger_summary = None
                if project is not None and dispatcher is not None:
                    ledger = _job_ledger_terminal(dispatcher, project["project_id"])
                    ledger_ok = ledger["all_terminal"]
                    ledger_summary = {"count": ledger["count"],
                                      "all_terminal": ledger["all_terminal"]}
                if ledger_ok:
                    cleanup = orchestrate_cleanup(worker_state="NEVER_DISPATCHED",
                        project_id=project["project_id"] if project else None,
                        session_id=None, worker_instance_id=None, connected_epoch=None,
                        detached_epoch=None, all_project_jobs_terminal=True,
                        disconnect=None, inspect=None,
                        stop_server=stop_server, stop_control=stop_control)
                    result["cleanup"] = cleanup
                    result["prebirth_cleanup_job_ledger"] = ledger_summary
                    result["status"] = ("BMA_MAPPING_PROBE_FAILED_CLEANUP_VERIFIED"
                                         if campaign_profile == "bma_mapping_probe" else
                                         "BMA_PROBE_FAILED_CLEANUP_VERIFIED"
                                         if campaign_profile == "bma_probe" else
                                         "SETUP_FAILED_CLEANUP_VERIFIED")
            except BaseException as cleanup_exc:
                result["cleanup_error"] = {"type": type(cleanup_exc).__name__,
                                            "message": str(cleanup_exc),
                                            "completed": getattr(cleanup_exc, "completed", [])}
                result["status"] = "UNKNOWN_PRESERVE_OWNED_RESOURCES"
        return result
    finally:
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        if server_birth_mono is not None:
            result["elapsed_from_server_birth_s"] = time.monotonic() - server_birth_mono
        result["route_outcomes"] = route_outcomes
        _finalize_science_counters(result, campaign_profile)
        if server is not None and server.proc is not None:
            if server_identity is None and isinstance(server.process_identity, Mapping):
                try:
                    server_identity = _bind_native_server_identity(server, server_listener)
                except Exception as identity_exc:
                    result["server_identity_binding_error"] = {
                        "type": type(identity_exc).__name__, "message": str(identity_exc)}
            if server_port is None and type(server.port) is int:
                server_port = server.port
        result["resource_ownership"] = _resource_ownership_receipt(
            server_proc=server.proc if server is not None else None,
            server_identity=server_identity,
            server_listener=server_listener,
            control_proc=daemon_proc, control_identity=daemon_identity,
            control_endpoint=daemon_endpoint,
            worker_state=worker_state, session=session,
            retirement_proof=worker_retirement_proof,
            process_cleanup=process_cleanup)
        _write_json(run_dir / "setup_result.json", result)
        event("candidate_finished", status=result["status"],
              elapsed_from_server_birth_s=result.get("elapsed_from_server_birth_s"),
              cleanup=result.get("cleanup"), error=result.get("error"),
              cleanup_error=result.get("cleanup_error"))
        if daemon_log is not None:
            try:
                daemon_log.close()
            except Exception:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("export-archive", help="export the exact published source closure and this overlay")
    archive.add_argument("--destination", required=True, type=Path)
    archive.add_argument("--base-commit", required=True)
    prepare = sub.add_parser("prepare", help="offline source audit and Java compile only")
    prepare.add_argument("--evidence", required=True, type=Path)
    prepare.add_argument("--base-commit", required=True)
    prepare.add_argument("--profile", choices=("setup_only", "bma_probe", "bma_mapping_probe"), default="setup_only")
    prepare.add_argument("--comsol-root", type=Path, default=INSTALL_ROOT)
    prepare.add_argument("--jdk11", type=Path, default=JAVA11)
    execute = sub.add_parser("execute", help="run only the separately reviewed setup-only candidate")
    execute.add_argument("--evidence", required=True, type=Path)
    execute.add_argument("--reviewed-candidate-sha256", required=True)
    execute.add_argument("--comsol-root", type=Path, default=INSTALL_ROOT)
    execute.add_argument("--jdk11", type=Path, default=JAVA11)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export-archive":
            exported = export_published_archive(repo=REPO, destination=args.destination,
                                                base_commit=args.base_commit)
            print(json.dumps(exported, indent=2, ensure_ascii=False))
            return 0
        if args.command == "prepare":
            candidate = prepare_candidate(repo=REPO, evidence=args.evidence,
                base_commit=args.base_commit, install_root=args.comsol_root, jdk_home=args.jdk11,
                campaign_profile=args.profile)
            print(json.dumps({"status": candidate["status"],
                              "candidate_sha256": candidate["candidate_sha256"],
                              "evidence_dir": candidate["evidence_dir"],
                              "source_closure_sha256": candidate["source"]["source_closure_sha256"],
                              "compiled_classes": candidate["compile"]["compiled_classes"]},
                             indent=2, ensure_ascii=False))
            return 0
        result = execute_candidate(repo=REPO, evidence=args.evidence,
            reviewed_sha256=args.reviewed_candidate_sha256,
            install_root=args.comsol_root, jdk_home=args.jdk11)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get("status") in {
            "SETUP_COMPLETE_CLEANUP_VERIFIED_SOLVE_NOT_RUN",
            "BMA_PROBE_COMPLETE_CLEANUP_VERIFIED_FULL3D_SCIENCE_NOT_RUN",
            "BMA_MAPPING_PROBE_COMPLETE_CLEANUP_VERIFIED_MAPPING_UNVERIFIED",
        } else 1
    except Exception as exc:
        print(json.dumps({"status": "PREPARE_OR_GATE_FAILED", "type": type(exc).__name__,
                          "message": str(exc)}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
