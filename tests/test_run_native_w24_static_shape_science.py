from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from tools.run_native_w24_cure_science import BirthBudget, CampaignError, ManagedModelBinding
from tools.w24_static_shape_capture import HEADER, MAGIC
from comsol_mcp._execution_contract import canonical_request_hash
from comsol_mcp._operation_store import IdempotencyConflict, OperationStore
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from tools import run_native_w24_static_shape_science as science
from tools import run_native_w24_static_shape_sensitivity_science as sensitivity_science
from tools.w24_static_shape_sensitivity import sensitivity_configurations


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shape_readback(case: str, model_tag: str, configuration_id: str = "baseline") -> dict[str, Any]:
    configuration = next(row for row in sensitivity_configurations()
                          if row.configuration_id == configuration_id)
    wetting = ({"sel_wet_flat_base": [10]} if case == "flat" else {
        "sel_wet_mesa_top": [10], "sel_wet_mesa_side": [11], "sel_wet_lower_base": [12]})
    return {
        "status": "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK", "native_acceptance": "NOT_RUN",
        "case_id": case, "configuration_id": configuration_id,
        "model_tag": model_tag, "geometry_dimension": 2,
        "geometry_axisymmetric": True, "geometry_domain_count": 2,
        "glue_domain_ids": [1], "gas_domain_ids": [2], "multiphase_domain_ids": [1, 2],
        "substrate_wetting_selections": wetting,
        "wetted_wall_features": [{"feature_tag": f"ww{index}", "surface_selection": name,
                                   "boundary_ids": ids, "contact_angle_expression": "thetaSubstrate"}
                                  for index, (name, ids) in enumerate(wetting.items())],
        "axis_boundary_ids": [1], "flow_compressibility": "incompressible",
        "pressure_reference_value": "0[Pa]", "multiphase_volume_fraction_definition": "pf",
        "phase1_material_link": "matGlue", "phase2_material_link": "matGas",
        "surface_tension_enabled": "on", "surface_tension_mode": "userdef",
        "surface_tension_expression": "sigma0", "phase1_initial_value": "Fluid1phipf",
        "phase2_initial_value": "Fluid2phipf", "phase1_initial_selection": [1],
        "phase2_initial_selection": [2], "phase_initialization_step_type": "PhaseInitialization",
        "transient_step_type": "Transient", "bdf_output_time_policy": "strict",
        "output_mode": "tsteps", "stored_time_steps_policy": 1,
        "parameter_values_and_units": {
            "rhoGlue": {"value_si": 1200.0, "unit": "kg/m^3"},
            "muGlue": {"value_si": 1.0, "unit": "Pa*s"},
            "rhoGas": {"value_si": 1.2, "unit": "kg/m^3"},
            "muGas": {"value_si": 0.018, "unit": "Pa*s"},
            "sigma0": {"value_si": 0.03, "unit": "N/m"},
            "epsPF": {"value_si": configuration.epsilon_m, "unit": "m"},
            "Rdrop": {"value_si": 500e-6, "unit": "m"},
        },
        "configuration_readback": {
            "configuration_id": configuration_id,
            "mesh": {"mesh_tag": "mesh1", "custom": True,
                     "hmax_m": configuration.mesh_hmax_m,
                     "hmin_m": configuration.mesh_hmin_m},
            "maximum_step_s": configuration.maximum_step_s,
        },
        "solution_state_readback": {
            "status": "NO_STORED_SOLUTION_DATA",
            "readback_method": "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]",
            "solver_sequences": [{"solver_sequence_tag": "sol1", "degrees_of_freedom": 0,
                                  "stored_solution_count": 0}],
        },
        "study_run_calls_this_action": 0,
    }


def _write_setup_receipt(path: Path, runner, case: str, binding: ManagedModelBinding) -> str:
    artifact_path = runner.workspace / "outputs" / f"static_shape_{case}.mph"
    artifact_path.write_bytes(f"synthetic unsolved saved artifact: {case}".encode())
    original_readback = binding.as_record()
    original_readback["revision"] -= 1
    receipt = {
        "schema": "W24_STATIC_SHAPE_MANAGED_SETUP_V1",
        "status": "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE",
        "native_acceptance": "NOT_RUN", "project_id": runner.project_id,
        "project_workspace": str(runner.workspace.resolve()), "case_id": case,
        "source_sha256": {"fixture": _sha(science.SETUP_FIXTURE_SOURCE),
                          "readback": _sha(science.SETUP_READBACK_SOURCE)},
        "reopened_configuration_readback_binding": original_readback,
        "reopened_model_binding": binding.as_record(),
        "reopened_model_identity": {"model_binding": original_readback},
        "configuration_id": "baseline",
        "reopened_configuration_readback": _shape_readback(case, binding.model_tag),
        "readback_comparison": {"matches": True, "interpolation_used": False},
        "project_artifact": {"path": str(artifact_path), "size_bytes": artifact_path.stat().st_size,
                             "sha256": _sha(artifact_path)},
        "study_run_submissions": [], "study_run_submission_count": 0,
        "phase_initialization_executed": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    return _sha(path)


def _approval(path: Path, runner, bindings, *, calls: int = 2):
    solve_sha = _sha(science.STUDY_RUN_SOURCE)
    capture_sha = _sha(science.CAPTURE_SOURCE)
    executor_sha = _sha(science.SCIENCE_EXECUTOR_SOURCE)
    fixture_sha = _sha(science.SETUP_FIXTURE_SOURCE)
    readback_sha = _sha(science.SETUP_READBACK_SOURCE)
    setup_receipt_paths = {case: runner.workspace / "evidence" / f"{case}_setup_receipt.json"
                           for case in ("flat", "step")}
    setup_receipt_sha = {case: _write_setup_receipt(setup_receipt_paths[case], runner, case, bindings[case])
                         for case in ("flat", "step")}
    payload = {
        "schema": science.APPROVAL_SCHEMA,
        "status": "APPROVED",
        "campaign_id": "baseline-campaign-0001",
        "configuration_id": "baseline",
        "study_tag": "stdShape",
        "case_order": ["flat", "step"],
        "study_run_submissions": calls,
        "phase_initialization_steps_per_submission": 1,
        "project_id": runner.project_id,
        "project_workspace": str(runner.workspace.resolve()),
        "operation_store_path": str(runner.daemon.store.path.resolve()),
        "model_bindings": {case: science._binding_record(
            bindings[case], project_id=runner.project_id, case_id=case) for case in ("flat", "step")},
        "source_sha256": {"study_run": solve_sha, "history_capture": capture_sha,
                           "science_executor": executor_sha, "setup_fixture": fixture_sha,
                           "setup_readback": readback_sha},
        "setup_receipt_sha256": setup_receipt_sha,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return (_sha(path), solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
            setup_receipt_paths, setup_receipt_sha)


def test_approval_pins_exact_pair_project_store_bindings_and_all_sources(tmp_path):
    runner = _FakeRunner(tmp_path / "runner")
    bindings = {case: _binding(case) for case in ("flat", "step")}
    path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, setup_receipt_sha) = _approval(path, runner, bindings)
    result = science.validate_science_approval(
        path, expected_approval_sha256=approval_sha,
        expected_solve_source_sha256=solve_sha,
        expected_capture_source_sha256=capture_sha,
        expected_science_executor_sha256=executor_sha,
        expected_setup_fixture_source_sha256=fixture_sha,
        expected_setup_readback_source_sha256=readback_sha,
        expected_setup_receipt_sha256=setup_receipt_sha,
        expected_project_id=runner.project_id,
        expected_workspace=runner.workspace.resolve(),
        expected_operation_store_path=runner.daemon.store.path.resolve(),
        expected_bindings=bindings)
    assert result["study_run_submissions"] == 2

    path.write_text(json.dumps({**result, "study_run_submissions": 14}), encoding="utf-8")
    with pytest.raises(CampaignError, match="exactly flat then step"):
        science.validate_science_approval(
            path, expected_approval_sha256=_sha(path),
            expected_solve_source_sha256=solve_sha,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            expected_setup_receipt_sha256=setup_receipt_sha,
            expected_project_id=runner.project_id,
            expected_workspace=runner.workspace.resolve(),
            expected_operation_store_path=runner.daemon.store.path.resolve(),
            expected_bindings=bindings)
def test_approval_rejects_digest_or_source_drift_before_native_dispatch(tmp_path):
    runner = _FakeRunner(tmp_path / "runner")
    bindings = {case: _binding(case) for case in ("flat", "step")}
    path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, setup_receipt_sha) = _approval(path, runner, bindings)
    with pytest.raises(CampaignError, match="differs from the reviewed digest"):
        science.validate_science_approval(
            path, expected_approval_sha256="0" * 64,
            expected_solve_source_sha256=solve_sha,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            expected_setup_receipt_sha256=setup_receipt_sha,
            expected_project_id=runner.project_id,
            expected_workspace=runner.workspace.resolve(),
            expected_operation_store_path=runner.daemon.store.path.resolve(),
            expected_bindings=bindings)
    with pytest.raises(CampaignError, match="bind the exact setup, solve"):
        science.validate_science_approval(
            path, expected_approval_sha256=_sha(path),
            expected_solve_source_sha256="0" * 64,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            expected_setup_receipt_sha256=setup_receipt_sha,
            expected_project_id=runner.project_id,
            expected_workspace=runner.workspace.resolve(),
            expected_operation_store_path=runner.daemon.store.path.resolve(),
            expected_bindings=bindings)


@pytest.mark.parametrize("attack", ["missing", "hash_mismatch", "forged_binding"])
def test_unapproved_setup_receipt_refuses_before_any_slot_dispatch(tmp_path, attack):
    runner = _FakeRunner(tmp_path / "runner")
    bindings = {case: _binding(case) for case in ("flat", "step")}
    approval_path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, setup_receipt_sha) = _approval(approval_path, runner, bindings)

    if attack == "missing":
        setup_paths["flat"].unlink()
    elif attack == "hash_mismatch":
        setup_paths["flat"].write_text(setup_paths["flat"].read_text(encoding="utf-8") + " ",
                                       encoding="utf-8")
    else:
        forged = json.loads(setup_paths["flat"].read_text(encoding="utf-8"))
        forged["reopened_model_binding"]["model_ref"]["model_tag"] = "model-forged"
        setup_paths["flat"].write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
        approved = json.loads(approval_path.read_text(encoding="utf-8"))
        approved["setup_receipt_sha256"]["flat"] = _sha(setup_paths["flat"])
        approval_path.write_text(json.dumps(approved, sort_keys=True), encoding="utf-8")
        approval_sha = _sha(approval_path)

    with pytest.raises(CampaignError):
        science.execute_baseline_flat_step_science(
            runner, bindings, setup_receipt_paths=setup_paths,
            approval_path=approval_path, expected_approval_sha256=approval_sha,
            expected_solve_source_sha256=solve_sha,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            birth_budget=BirthBudget(time.time() - 1.0, budget_s=3600.0, cleanup_reserve_s=45.0),
            solve_ledger_path=runner.workspace / "outputs" / "static_shape_study_runs.jsonl")
    assert runner.daemon.dispatch_attempts == []
    assert runner.daemon.worker_invocations == 0
    assert runner.inspected == []


def test_unknown_slot_cannot_be_rekeyed_from_a_new_managed_revision(tmp_path):
    runner = _FakeRunner(tmp_path / "runner")
    bindings = {case: _binding(case) for case in ("flat", "step")}
    approval_path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, _setup_receipt_sha) = _approval(approval_path, runner, bindings)
    newer_flat = ManagedModelBinding(
        bindings["flat"].project_id, bindings["flat"].session_id,
        dict(bindings["flat"].model_ref), bindings["flat"].revision + 1)
    changed = {"flat": newer_flat, "step": bindings["step"]}
    with pytest.raises(CampaignError, match="approval does not pin the exact registered project"):
        science.execute_baseline_flat_step_science(
            runner, changed, setup_receipt_paths=setup_paths,
            approval_path=approval_path, expected_approval_sha256=approval_sha,
            expected_solve_source_sha256=solve_sha,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            birth_budget=BirthBudget(time.time() - 1.0, budget_s=3600.0, cleanup_reserve_s=45.0),
            solve_ledger_path=runner.workspace / "outputs" / "static_shape_study_runs.jsonl")
    assert runner.daemon.dispatch_attempts == []
    assert runner.daemon.worker_invocations == 0


def _raw_history() -> bytes:
    times, radii = (0.0, 1.0), (0.5e-6, 1.5e-6, 2.5e-6)
    z_columns = ((0.0, 1e-6, 2e-6),) * 3
    parts = [HEADER.pack(MAGIC, 1, 2, 3, 9, 3)]

    def doubles(values):
        parts.extend(struct.pack(">d", float(value)) for value in values)

    doubles(times)
    doubles(radii)
    for z in z_columns:
        doubles((z[0],))
        parts.append(struct.pack(">i", len(z)))
        doubles(z)
    doubles((-1.0, -1.0, 1.0) * 6)
    for arc in (0.0, 1e-6, 2e-6):
        doubles((arc, arc, 0.0))
    doubles((-1.0, -1.0, 1.0) * 2)
    doubles((1e-12, 1e-12))
    doubles((0.0, 0.0))
    return b"".join(parts)


def _binding(case: str) -> ManagedModelBinding:
    tag = f"model-{case}"
    ref = {"model_tag": tag, "session_id": "session-1",
           "server_instance_id": "server-1", "generation": 1}
    return ManagedModelBinding("project-1", "session-1", ref, 3)


class _FakeSetupReader:
    @staticmethod
    def _java_action_readback(response, label):
        assert response["success"] is True, label
        return dict(response["native_result"])

    @staticmethod
    def _worker_request_terminal(response):
        data = response.get("data") if isinstance(response, dict) else None
        worker = data.get("worker") if isinstance(data, dict) else None
        return isinstance(worker, dict) and worker.get("status") in {"SUCCEEDED", "FAILED"}


class _FakeDurableDaemon:
    """No-native dispatch adapter backed by the production SQLite OperationStore."""

    def __init__(self, path: Path, *, unknown_before_ledger: bool = False,
                 worker_state: dict[str, Any] | None = None,
                 worker_entered: threading.Event | None = None,
                 worker_release: threading.Event | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.store = OperationStore(path)
        self.unknown_before_ledger = unknown_before_ledger
        self.worker_state = worker_state or {"lock": threading.Lock(), "calls": 0}
        self.worker_entered = worker_entered
        self.worker_release = worker_release
        self.dispatch_attempts = []

    @property
    def worker_invocations(self):
        return self.worker_state["calls"]

    def close(self):
        self.store.close()

    def dispatch(self, request):
        execution = request["execution"]
        wrapper = request["arguments"]
        arguments = wrapper["arguments"]
        key = execution["idempotency_key"]
        semantic_operation = wrapper["operation_id"]
        request_hash = canonical_request_hash(
            semantic_operation, arguments, execution["model_ref"], execution["expected_revision"],
            project_id=execution["project_id"], session_id=execution["session_id"],
            queue_timeout_s=execution["queue_timeout_s"],
            execution_timeout_s=execution["execution_timeout_s"],
        )
        self.dispatch_attempts.append(key)
        try:
            record, reused = self.store.begin(
                request_id=execution["request_id"], idempotency_key=key,
                request_hash=request_hash, operation=request["operation"],
                metadata={"operation": semantic_operation, "arguments": arguments,
                          "execution": execution},
                timeouts={"queue_timeout_s": execution["queue_timeout_s"],
                          "execution_timeout_s": execution["execution_timeout_s"]},
            )
        except IdempotencyConflict:
            return {"success": False, "error": {"code": "IDEMPOTENCY_CONFLICT"},
                    "execution": {"idempotency_key": key, "request_id": execution["request_id"]}}

        if reused:
            if record["result"] is not None:
                return record["result"]
            return {
                "success": True,
                "data": {"job_id": record["job_id"], "status": record["status"]},
                "execution": {name: record[name] for name in
                              ("request_id", "operation_id", "request_hash", "idempotency_key", "job_id")},
            }

        with self.worker_state["lock"]:
            self.worker_state["calls"] += 1
        if self.worker_entered is not None:
            self.worker_entered.set()
        if self.worker_release is not None and not self.worker_release.wait(timeout=10):
            raise TimeoutError("test worker hold was not released")

        action = arguments["arguments"]
        base_execution = {name: record[name] for name in
                          ("request_id", "operation_id", "request_hash", "idempotency_key", "job_id")}
        if self.unknown_before_ledger:
            response = {
                "success": False,
                "data": {"worker": {"status": "UNKNOWN"}},
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False},
                "execution": base_execution,
            }
            self.store.finish(record["operation_id"], status="UNKNOWN", result=response)
            return response

        ledger = Path(action["ledger_path"])
        if action["case_id"] == "step":
            prior_row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
            assert prior_row["case_id"] == "flat"
            assert prior_row["slot_idempotency_key"] == action["previous_slot_idempotency_key"]
            assert prior_row["model_tag"] == action["previous_model_tag"]
            assert prior_row["approval_sha256"] == action["approval_sha256"]
        row = {
            "event": "study_run_submitted", "submission_index": action["submission_index"],
            "case_id": action["case_id"], "study_tag": "stdShape",
            "model_tag": action["expected_model_tag"],
            "slot_idempotency_key": action["slot_idempotency_key"],
            "approval_sha256": action["approval_sha256"],
            "campaign_id": action["campaign_id"],
        }
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        save_path = Path(action["save_path"])
        save_path.write_bytes(f"synthetic solved artifact: {action['case_id']}".encode())
        native_result = {
            "status": "NATIVE_STUDY_RUN_RETURNED", "case_id": action["case_id"],
            "model_tag": action["expected_model_tag"], "study_tag": "stdShape",
            "submission_index": action["submission_index"],
            "study_run_calls_from_this_action": 1,
            "phase_initialization_included": True, "save_path": str(save_path),
            "size_bytes": save_path.stat().st_size, "sha256": _sha(save_path),
        }
        response = {
            "success": True, "data": {"worker": {"status": "SUCCEEDED"}},
            "native_result": native_result,
            "execution": {**base_execution, "project_id": execution["project_id"],
                          "session_id": execution["session_id"],
                          "model_ref": execution["model_ref"],
                          "revision": execution["expected_revision"]},
        }
        self.store.finish(record["operation_id"], status="SUCCEEDED", result=response)
        return response


class _FakeRunner:
    project_id = "project-1"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = workspace / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir = self.evidence_dir / "responses"
        self.response_dir.mkdir(parents=True, exist_ok=True)
        (workspace / "outputs").mkdir(parents=True, exist_ok=True)
        self.timeout_s = 60.0
        self.setup_runner = _FakeSetupReader()
        self.daemon = _FakeDurableDaemon(workspace / "control" / "operations.sqlite3")
        self.source_paths = {}
        for role, source in (("fixture", science.SETUP_FIXTURE_SOURCE),
                             ("readback", science.SETUP_READBACK_SOURCE)):
            copied = workspace / source.name
            copied.write_bytes(source.read_bytes())
            self.source_paths[role] = copied
        self.verified: list[str] = []
        self.dispatched: list[str] = []
        self.inspected: list[str] = []

    def _verify_persisted_binding(self, binding):
        self.verified.append(binding.model_tag)

    def _validate_shape_readback(self, result, case):
        assert result["case_id"] == case
        return result

    @staticmethod
    def _validate_no_stored_solution_data(value):
        assert value == {"status": "NO_STORED_SOLUTION_DATA"}
        return value

    def _java_action(self, binding, *, source_role, entrypoint, arguments):
        pytest.fail("science must reuse approved setup readback and call read-only model.inspect")

    def _inspect(self, binding):
        self.inspected.append(binding.model_tag)
        return binding, {
            "data": {"model_identity": dict(binding.model_ref), "revision": binding.revision,
                     "structure": {"solutions": ["sol1"]}},
            "model_binding": binding.as_record(),
        }

    def _dispatch(self, operation, request, *, binding, worker_required, timeout_s):
        assert operation == "operation_call"
        self.dispatched.append(binding.model_tag)
        args = request["arguments"]["arguments"]
        case = args["case_id"]
        save_path = Path(args["save_path"])
        save_path.write_bytes(f"synthetic solved artifact: {case}".encode())
        ledger = Path(args["ledger_path"])
        row = {"event": "study_run_submitted", "submission_index": args["submission_index"],
               "case_id": case, "study_tag": "stdShape", "model_tag": binding.model_tag}
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        result = {"status": "NATIVE_STUDY_RUN_RETURNED", "case_id": case,
                  "model_tag": binding.model_tag, "study_tag": "stdShape",
                  "submission_index": args["submission_index"],
                  "study_run_calls_from_this_action": 1,
                  "phase_initialization_included": True, "save_path": str(save_path),
                  "size_bytes": save_path.stat().st_size, "sha256": _sha(save_path)}
        return {"success": True, "native_result": result,
                "execution": {"job_id": f"job-{case}"}}

    def _record_response(self, call_id, response):
        (self.response_dir / f"{call_id}.json").write_text(
            json.dumps(response, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _updated_binding(binding, response):
        return binding


class _ProductionRouteWorker:
    """Worker-shaped stub: exercises ControlDaemon routing without COMSOL/native work."""

    def __init__(self, state):
        self.state = state

    @contextmanager
    def operation_context(self, _operation_id, on_request_event=None):
        yield

    def backend_snapshot(self, model_tag):
        return {"model_tag": model_tag, "fingerprint": "stable-synthetic-fingerprint",
                "external_event_counter": 0}

    def execute_java(self, model_tag, source_artifact, entrypoint, arguments, *, request_id=None):
        with self.state["lock"]:
            self.state["calls"] += 1
        self.state["entered"].set()
        if arguments.get("sensitivity_campaign") is True:
            action = arguments
            ledger = Path(action["ledger_path"])
            row = {
                "event": "study_run_submitted",
                "submission_index": action["submission_index"],
                "configuration_id": action["configuration_id"],
                "case_id": action["case_id"],
                "study_tag": "stdShape",
                "model_tag": model_tag,
                "slot_idempotency_key": action["slot_idempotency_key"],
                "approval_sha256": action["approval_sha256"],
                "campaign_id": action["campaign_id"],
            }
            with ledger.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
            save_path = Path(action["save_path"])
            save_path.write_bytes(f"synthetic solved artifact: {model_tag}".encode())
            with self.state["lock"]:
                self.state["study_run_calls"] = self.state.get("study_run_calls", 0) + 1
            native_result = {
                "status": "NATIVE_STUDY_RUN_RETURNED",
                "configuration_id": action["configuration_id"],
                "case_id": action["case_id"],
                "model_tag": model_tag,
                "submission_index": action["submission_index"],
                "ordered_slots_sha256": action["slot_history_sha256"],
                "study_run_calls_from_this_action": 1,
                "phase_initialization_included": True,
                "save_path": str(save_path),
                "size_bytes": save_path.stat().st_size,
                "sha256": _sha(save_path),
            }
            return {"ok": True, "status": "SUCCEEDED",
                    "result": {"executed": True, "readback": native_result}}
        release = self.state.get("release")
        if release is not None and not release.wait(timeout=10):
            raise TimeoutError("test Worker stub hold was not released")
        if self.state.get("raise_after_dispatch"):
            raise RuntimeError("synthetic Worker transport loss after execute dispatch")
        case = arguments.get("case_id", "flat")
        native_result = {
            "status": "NATIVE_STUDY_RUN_RETURNED", "case_id": case,
            "model_tag": model_tag, "study_tag": "stdShape",
            "submission_index": arguments.get("submission_index", 1),
            "study_run_calls_from_this_action": 1,
            "phase_initialization_included": True,
            "save_path": arguments.get("save_path", "synthetic.mph"),
            "size_bytes": 1, "sha256": "0" * 64,
        }
        return {"ok": True, "status": "SUCCEEDED",
                "result": {"executed": True, "readback": native_result}}

    def client(self):
        java = SimpleNamespace(
            getFileResourceTags=lambda: [], getComsolVersion=lambda: "COMSOL 6.4",
            getLastComputationTime=lambda: None, getLastComputationDate=lambda: None,
            getLastComputationVersion=lambda: None,
        )
        return SimpleNamespace(model=lambda _tag: SimpleNamespace(java=java))


def _real_control_daemon_pair(tmp_path: Path, monkeypatch, worker_state):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    # This test has no COMSOL process and therefore cannot produce the real
    # owned-server isolation receipt. Stub only that predispatch gate; keep the
    # production ControlDaemon, catalog, Worker adapter route, OperationStore,
    # ExecutionService write-ticket and revision logic in the exercised path.
    from comsol_mcp._managed_backend import ManagedBackend
    monkeypatch.setattr(ManagedBackend, "_require_g2_isolation",
                        lambda _self: {"status": "SYNTHETIC_TEST_ONLY_NO_NATIVE_SERVER"})
    project_root = tmp_path / "project-root"
    project_root.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "control-home"
    worker = _ProductionRouteWorker(worker_state)
    services = []
    daemons = []
    bindings = []
    for _index in range(2):
        service = ExecutionService(
            SessionLedger("session-1", "server-1"),
            SimpleNamespace(model_snapshot=lambda tag: {
                "model_tag": tag, "server_instance_id": "server-1",
                "fingerprint": "stable-synthetic-fingerprint", "external_event_counter": 0,
            }),
            project_root=project_root,
        )
        service.ledger.permissions.add("trusted_code")
        ref = service.bind_model("model-flat")["execution"]["model_ref"]
        daemon = ControlDaemon(home, service=service, registry={}, worker=worker,
                               project_root=project_root)
        daemon.backend.worker_identity = {"connection_epoch": 1,
                                          "worker_instance_id": "synthetic-worker-stub"}
        services.append(service)
        daemons.append(daemon)
        bindings.append(ref)

    create = daemons[0].dispatch({
        "operation": "project.create",
        "arguments": {"label": "W24 route test", "workspace": str(project_root / "w24-project"),
                      "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"],
                                 "timeouts": {}}},
        "execution": {"request_id": "create-project", "idempotency_key": "create-project"},
    })
    assert create["success"] is True
    project_id = create["data"]["project"]["project_id"]
    workspace = Path(create["data"]["project"]["workspace"])
    (workspace / "evidence").mkdir()
    source_copy = workspace / science.STUDY_RUN_SOURCE.name
    source_copy.write_bytes(science.STUDY_RUN_SOURCE.read_bytes())
    for daemon, ref in zip(daemons, bindings):
        daemon.backend._bind_model_project(ref, project_id)
        daemon.backend.persist()
    return daemons, services, bindings, project_id, workspace, source_copy


def _real_solve_arguments(binding: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    return {
        "source_artifact": science.STUDY_RUN_SOURCE.name,
        "entrypoint": "W24StaticShapeStudyRun#run",
        "arguments": {"action": "run", "case_id": "flat",
                      "expected_model_tag": binding["model_tag"],
                      "submission_index": 1,
                      "workspace_path": str(workspace),
                      "ledger_path": str(workspace / "outputs" / "ledger.jsonl"),
                      "save_path": str(workspace / "outputs" / "synthetic.mph")},
        "mode": "trusted",
    }


class _ProductionRouteRunner:
    def __init__(self, daemon, project_id, evidence_dir):
        self.daemon = daemon
        self.project_id = project_id
        self.setup_runner = _FakeSetupReader()
        self.evidence_dir = evidence_dir

    def _record_response(self, call_id, response):
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"{call_id}.json"
        path.write_text(json.dumps(response, sort_keys=True), encoding="utf-8")


def _invoke_production_solve(daemon, project_id, binding, workspace, journal, *, call_id):
    slot_binding = ManagedModelBinding(project_id, binding["session_id"], binding, 0)
    slot_key, request_id = science._slot_identity(project_id, slot_binding, "flat")
    return science._dispatch_solve_slot(
        _ProductionRouteRunner(daemon, project_id, journal.parent), slot_binding,
        arguments=_real_solve_arguments(binding, workspace),
        idempotency_key=slot_key, request_id=request_id,
        timeout_s=5.0, call_id=call_id, journal=journal)


def test_real_control_daemon_inspect_is_revision_stable_and_trusted_java_write_advances_once(
        tmp_path, monkeypatch):
    worker_state = {"lock": threading.Lock(), "calls": 0, "entered": threading.Event()}
    daemons, services, bindings, project_id, workspace, _source_copy = _real_control_daemon_pair(
        tmp_path, monkeypatch, worker_state)
    try:
        from comsol_mcp import _model_ops
        monkeypatch.setattr(_model_ops, "_model_tree_data", lambda _model: {
            "components": ["comp1"], "component_details": [], "parameters": [],
            "studies": ["stdShape"], "solutions": ["sol1"], "datasets": [], "results": [],
        })
        ref = bindings[0]
        inspect = daemons[0].dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "model.inspect", "arguments": {"detail": "summary"}},
            "execution": {"project_id": project_id, "session_id": ref["session_id"],
                          "model_ref": ref, "expected_revision": 0,
                          "idempotency_key": "w24-real-route-inspect", "request_id": "w24-real-route-inspect",
                          "rpc_timeout_s": 5.0, "queue_timeout_s": 5.0,
                          "execution_timeout_s": None},
        })
        assert inspect["success"] is True
        assert inspect["data"]["structure"]["solutions"] == ["sol1"]
        assert inspect["execution"]["revision"] == 0
        ref_object = services[0].ledger._models["model-flat"].ref
        assert services[0].ledger.revision(ref_object) == 0

        result = _invoke_production_solve(
            daemons[0], project_id, ref, workspace,
            workspace / "evidence" / "successful_control_daemon_dispatch.jsonl",
            call_id="successful-control-daemon-dispatch")
        assert result["success"] is True
        assert result["data"]["worker"]["status"] == "SUCCEEDED"
        assert result["data"]["readback"]["readback"]["status"] == "NATIVE_STUDY_RUN_RETURNED"
        assert worker_state["calls"] == 1
        assert services[0].ledger.revision(ref_object) == 1
    finally:
        for daemon in daemons:
            daemon.close()


def test_independent_control_daemon_store_connections_claim_once_and_unknown_reentry_never_replays(
        tmp_path, monkeypatch):
    release = threading.Event()
    worker_state = {"lock": threading.Lock(), "calls": 0, "entered": threading.Event(),
                    "release": release, "raise_after_dispatch": True}
    daemons, _services, bindings, project_id, workspace, _source_copy = _real_control_daemon_pair(
        tmp_path, monkeypatch, worker_state)
    binding = bindings[0]
    slot_binding = ManagedModelBinding(project_id, binding["session_id"], binding, 0)
    expected_key, expected_request_id = science._slot_identity(project_id, slot_binding, "flat")
    journal_a = workspace / "evidence" / "caller_a.jsonl"
    journal_b = workspace / "evidence" / "caller_b.jsonl"

    pool = ThreadPoolExecutor(max_workers=2)
    first = pool.submit(
        _invoke_production_solve, daemons[0], project_id, binding, workspace, journal_a,
        call_id="caller-a")
    try:
        assert worker_state["entered"].wait(timeout=5), "first daemon request never reached the Worker stub"
        second_key, second_request = science._slot_identity(project_id, slot_binding, "flat")
        assert (second_key, second_request) == (expected_key, expected_request_id)
        with pytest.raises(CampaignError, match="no observed terminal result"):
            science._dispatch_solve_slot(
                _ProductionRouteRunner(daemons[1], project_id, journal_b.parent), slot_binding,
                arguments=_real_solve_arguments(binding, workspace),
                idempotency_key=second_key, request_id=second_request,
                timeout_s=5.0, call_id="caller-b", journal=journal_b)
        assert worker_state["calls"] == 1
    finally:
        release.set()
    with pytest.raises(CampaignError, match="no observed terminal result"):
        first.result(timeout=10)

    # Reentry after terminal UNKNOWN sees the exact durable result, while a
    # changed request key or revision is outside the reviewed approval slot.
    with pytest.raises(CampaignError, match="no observed terminal result"):
        science._dispatch_solve_slot(
            _ProductionRouteRunner(daemons[1], project_id, journal_b.parent), slot_binding,
            arguments=_real_solve_arguments(binding, workspace),
            idempotency_key=expected_key, request_id=expected_request_id,
            timeout_s=5.0, call_id="caller-b-reentry", journal=journal_b)
    assert worker_state["calls"] == 1

    changed_arguments = _real_solve_arguments(binding, workspace)
    changed_arguments["arguments"]["approval_sha256"] = "f" * 64
    with pytest.raises(CampaignError, match="different canonical payload"):
        science._dispatch_solve_slot(
            _ProductionRouteRunner(daemons[1], project_id, journal_b.parent), slot_binding,
            arguments=changed_arguments,
            idempotency_key=expected_key, request_id=expected_request_id,
            timeout_s=5.0, call_id="caller-b-changed-approval", journal=journal_b)
    assert worker_state["calls"] == 1
    assert daemons[0].store.db.execute(
        "SELECT COUNT(*) FROM operations WHERE idempotency_key=?", (expected_key,)).fetchone()[0] == 1
    assert daemons[0].store.db.execute(
        "SELECT status FROM operations WHERE idempotency_key=?", (expected_key,)).fetchone()[0] == "UNKNOWN"
    pool.shutdown(wait=True)
    for daemon in daemons:
        daemon.close()


def _invoke_production_sensitivity_solve(daemon, project_id, binding, workspace, journal,
                                         *, call_id, configuration_id="baseline", case_id="flat"):
    slot_key, request_id = sensitivity_science.sensitivity_slot_identity(
        project_id, binding, configuration_id, case_id)
    source_name = sensitivity_science.STUDY_RUN_SOURCE.name
    return science._dispatch_solve_slot(
        _ProductionRouteRunner(daemon, project_id, journal.parent), binding,
        arguments={
            "source_artifact": source_name,
            "entrypoint": "W24StaticShapeSensitivityStudyRun#run",
            "arguments": {
                "action": "run", "sensitivity_campaign": True,
                "configuration_id": configuration_id, "case_id": case_id,
                "expected_model_tag": binding.model_tag,
                "submission_index": 1, "workspace_path": str(workspace),
                "ledger_path": str(workspace / "outputs" / "static_shape_sensitivity_study_runs.jsonl"),
                "save_path": str(workspace / "outputs" / f"static_shape_{configuration_id}_{case_id}_solved.mph"),
                "slot_idempotency_key": slot_key, "approval_sha256": "a" * 64,
                "campaign_id": "test-campaign-0001",
                "ordered_slots": [{
                    "submission_index": 1, "configuration_id": configuration_id,
                    "case_id": case_id, "model_tag": binding.model_tag,
                    "slot_idempotency_key": slot_key,
                }],
                "slot_history_sha256": "b" * 64,
            },
            "mode": "trusted",
        },
        idempotency_key=slot_key, request_id=request_id,
        timeout_s=5.0, call_id=call_id, journal=journal)


def test_independent_control_daemon_sensitivity_callers_claim_one_stable_slot_and_unknown_never_replays(
        tmp_path, monkeypatch):
    release = threading.Event()
    worker_state = {"lock": threading.Lock(), "calls": 0, "entered": threading.Event(),
                    "release": release, "raise_after_dispatch": True}
    daemons, _services, bindings, project_id, workspace, _source_copy = _real_control_daemon_pair(
        tmp_path, monkeypatch, worker_state)
    binding_record = bindings[0]
    binding = ManagedModelBinding(project_id, binding_record["session_id"], binding_record, 0)
    sensitivity_source = workspace / sensitivity_science.STUDY_RUN_SOURCE.name
    sensitivity_source.write_bytes(sensitivity_science.STUDY_RUN_SOURCE.read_bytes())
    slot_key, request_id = sensitivity_science.sensitivity_slot_identity(
        project_id, binding, "baseline", "flat")
    assert (slot_key, request_id) == sensitivity_science.sensitivity_slot_identity(
        project_id, binding, "baseline", "flat")
    changed_revision = ManagedModelBinding(project_id, binding.session_id,
                                           binding.model_ref, binding.revision + 1)
    changed_key, _ = sensitivity_science.sensitivity_slot_identity(
        project_id, changed_revision, "baseline", "flat")
    assert changed_key != slot_key  # caller must remain pinned to the approval's original binding

    journal_a = workspace / "evidence" / "sensitivity-caller-a.jsonl"
    journal_b = workspace / "evidence" / "sensitivity-caller-b.jsonl"
    pool = ThreadPoolExecutor(max_workers=2)
    first = pool.submit(
        _invoke_production_sensitivity_solve, daemons[0], project_id, binding, workspace, journal_a,
        call_id="sensitivity-caller-a")
    try:
        assert worker_state["entered"].wait(timeout=5), "first sensitivity request never reached the Worker stub"
        second_key, second_request = sensitivity_science.sensitivity_slot_identity(
            project_id, binding, "baseline", "flat")
        assert (second_key, second_request) == (slot_key, request_id)
        with pytest.raises(CampaignError, match="no observed terminal result"):
            _invoke_production_sensitivity_solve(
                daemons[1], project_id, binding, workspace, journal_b,
                call_id="sensitivity-caller-b")
        assert worker_state["calls"] == 1
    finally:
        release.set()
    with pytest.raises(CampaignError, match="no observed terminal result"):
        first.result(timeout=10)

    with pytest.raises(CampaignError, match="no observed terminal result"):
        _invoke_production_sensitivity_solve(
            daemons[1], project_id, binding, workspace, journal_b,
            call_id="sensitivity-caller-b-unknown-reentry")
    assert worker_state["calls"] == 1
    assert daemons[0].store.db.execute(
        "SELECT COUNT(*) FROM operations WHERE idempotency_key=?", (slot_key,)).fetchone()[0] == 1
    assert daemons[0].store.db.execute(
        "SELECT status FROM operations WHERE idempotency_key=?", (slot_key,)).fetchone()[0] == "UNKNOWN"
    assert changed_key != daemons[0].store.db.execute(
        "SELECT idempotency_key FROM operations WHERE idempotency_key=?", (slot_key,)).fetchone()[0]
    pool.shutdown(wait=True)
    for daemon in daemons:
        daemon.close()


def _sensitivity_bindings_and_approval(tmp_path: Path):
    plan = sensitivity_science.build_sensitivity_campaign_plan()
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    project_id = "sensitivity-project-1"
    bindings = {}
    for slot in sensitivity_science.sensitivity_submission_slots():
        key = sensitivity_science.sensitivity_binding_key(
            slot["configuration_id"], slot["case_id"])
        tag = "model-" + key.replace(":", "-")
        ref = {"model_tag": tag, "session_id": "session-sens-1",
               "server_instance_id": "worker-sens-1", "generation": 4}
        bindings[key] = ManagedModelBinding(project_id, "session-sens-1", ref, 11)
    slots, records = sensitivity_science._ordered_slot_records(project_id, bindings)
    source_hashes = {key: (chr(ord("a") + index) * 64) for index, key in enumerate(
        ("study_run", "history_capture", "science_executor", "setup_fixture", "setup_readback"))}
    receipt_hashes = {key: ("e" * 64) for key in sensitivity_science.CAPTURE_KEY_ORDER}
    estimate = sensitivity_science.sensitivity_capture_resource_estimate()
    payload = {
        "schema": sensitivity_science.APPROVAL_SCHEMA, "status": "APPROVED",
        "campaign_id": "sensitivity-campaign-0001", "project_id": project_id,
        "project_workspace": str(workspace),
        "operation_store_path": str(workspace / "control" / "operations.sqlite3"),
        "configuration_order": plan["configuration_order"],
        "case_order": ["flat", "step"], "study_run_submissions": 14,
        "phase_initialization_steps_per_submission": 1,
        "capture_grid_protocol": sensitivity_science.SENSITIVITY_CAPTURE_PROTOCOL,
        "comparison_limits": plan["comparison_limits_after_each_case_passes"],
        "source_sha256": source_hashes,
        "setup_receipt_sha256": receipt_hashes,
        "model_bindings": records,
        "ordered_slots": slots,
        "resource_limits": {
            "maximum_study_run_submissions": 14, "maximum_capture_files": 14,
            "maximum_single_capture_bytes": estimate["maximum_single_history_bytes"],
            "maximum_total_raw_capture_bytes": estimate["total_raw_capture_bytes"],
            "maximum_total_project_output_bytes": 10 * 1024 * 1024 * 1024,
            "maximum_single_solved_mph_bytes": 1024 * 1024 * 1024,
            "maximum_campaign_wall_time_s": 3000,
        },
    }
    approval_path = workspace / "sensitivity_approval.json"
    approval_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return (approval_path, hashlib.sha256(approval_path.read_bytes()).hexdigest(),
            source_hashes, receipt_hashes, project_id, workspace,
            workspace / "control" / "operations.sqlite3", bindings, slots, payload)


class _ProductionSensitivitySetupReader:
    @staticmethod
    def _java_action_readback(response, label):
        assert response["success"] is True, label
        return dict(response["data"]["readback"]["readback"])

    @staticmethod
    def _worker_request_terminal(response):
        data = response.get("data") if isinstance(response, dict) else None
        worker = data.get("worker") if isinstance(data, dict) else None
        return isinstance(worker, dict) and worker.get("status") in {"SUCCEEDED", "FAILED"}


class _ProductionSensitivityCampaignRunner:
    """Campaign-shaped test runner; solve dispatch remains production ControlDaemon."""

    def __init__(self, daemon, project_id, workspace, bindings):
        self.daemon = daemon
        self.project_id = project_id
        self.workspace = workspace
        self.evidence_dir = workspace / "evidence"
        self.setup_runner = _ProductionSensitivitySetupReader()
        self.timeout_s = 120.0
        self.source_paths = {}
        self.response_dir = self.evidence_dir / "sensitivity_responses"
        self.response_dir.mkdir(parents=True, exist_ok=True)
        for role, source in (("fixture", sensitivity_science.SETUP_FIXTURE_SOURCE),
                             ("readback", sensitivity_science.SETUP_READBACK_SOURCE)):
            copied = workspace / source.name
            copied.write_bytes(source.read_bytes())
            self.source_paths[role] = copied
        self.bindings = bindings

    def _verify_persisted_binding(self, binding):
        persisted = self.daemon.backend.model_project_binding(binding.model_ref)
        assert persisted == {"attribution": "PROJECT_BOUND", "project_id": self.project_id}
        return persisted

    def _inspect(self, binding):
        response = self.daemon.dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "model.inspect", "arguments": {"detail": "summary"}},
            "execution": {
                "project_id": self.project_id, "session_id": binding.session_id,
                "model_ref": dict(binding.model_ref), "expected_revision": binding.revision,
                "idempotency_key": f"campaign-preflight-{binding.model_tag}",
                "request_id": f"campaign-preflight-{binding.model_tag}",
                "rpc_timeout_s": self.timeout_s, "queue_timeout_s": 30.0,
                "execution_timeout_s": None,
            },
        })
        assert response["success"] is True
        return binding, {"data": dict(response["data"]), "response": response}

    def _updated_binding(self, prior, response):
        execution = response.get("execution")
        assert isinstance(execution, Mapping)
        return ManagedModelBinding(
            self.project_id, prior.session_id, dict(execution["model_ref"]), execution["revision"])

    def _record_response(self, call_id, response):
        (self.response_dir / f"{call_id}.json").write_text(
            json.dumps(response, sort_keys=True), encoding="utf-8")


def _real_sensitivity_campaign_fixture(tmp_path, monkeypatch):
    worker_state = {"lock": threading.Lock(), "calls": 0, "entered": threading.Event()}
    daemons, services, _initial_refs, project_id, workspace, _unused = _real_control_daemon_pair(
        tmp_path, monkeypatch, worker_state)
    daemon, service = daemons[0], services[0]
    from comsol_mcp import _model_ops
    monkeypatch.setattr(_model_ops, "_model_tree_data", lambda _model: {
        "components": ["comp1"], "component_details": [], "parameters": [],
        "studies": ["stdShape"], "solutions": ["sol1"], "datasets": [], "results": [],
    })
    (workspace / "outputs").mkdir(exist_ok=True)
    (workspace / "evidence").mkdir(exist_ok=True)
    bindings = {}
    receipt_paths = {}
    receipt_hashes = {}
    setup_dir = workspace / "evidence" / "sensitivity_setup_receipts"
    setup_dir.mkdir()
    for planned in sensitivity_science.sensitivity_submission_slots():
        configuration_id, case_id = planned["configuration_id"], planned["case_id"]
        key = sensitivity_science.sensitivity_binding_key(configuration_id, case_id)
        model_tag = "sensitivity-" + key.replace(":", "-")
        metadata = service.bind_model(model_tag)
        model_ref = metadata["execution"]["model_ref"]
        ref_object = service.ledger._models[model_tag].ref
        # These fixtures represent models after their already-recorded setup
        # writes. Seed only the synthetic ledger revision; keep engine counters
        # equal so the subsequent real production inspect route stays clean.
        service.ledger._state_for(ref_object).revision = 1
        assert service.ledger.revision(ref_object) == 1
        daemon.backend._bind_model_project(model_ref, project_id)
        daemon.backend.persist()
        binding = ManagedModelBinding(project_id, model_ref["session_id"], model_ref, 1)
        bindings[key] = binding

        original_readback_binding = binding.as_record()
        original_readback_binding["revision"] -= 1
        artifact_path = workspace / "outputs" / f"setup_{configuration_id}_{case_id}.mph"
        artifact_path.write_bytes(f"synthetic unsolved setup artifact {key}".encode())
        receipt = {
            "schema": ("W24_STATIC_SHAPE_MANAGED_SETUP_V1" if configuration_id == "baseline"
                       else "W24_STATIC_SHAPE_MANAGED_SETUP_V2"),
            "status": "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE",
            "native_acceptance": "NOT_RUN", "project_id": project_id,
            "project_workspace": str(workspace.resolve()),
            "configuration_id": configuration_id, "case_id": case_id,
            "source_sha256": {
                "fixture": _sha(sensitivity_science.SETUP_FIXTURE_SOURCE),
                "readback": _sha(sensitivity_science.SETUP_READBACK_SOURCE),
            },
            "reopened_configuration_readback_binding": original_readback_binding,
            "reopened_model_binding": binding.as_record(),
            "reopened_model_identity": {"model_binding": original_readback_binding},
            "reopened_configuration_readback": _shape_readback(case_id, model_tag, configuration_id),
            "readback_comparison": {"matches": True, "interpolation_used": False},
            "project_artifact": {"path": str(artifact_path), "size_bytes": artifact_path.stat().st_size,
                                 "sha256": _sha(artifact_path)},
            "study_run_submissions": [], "study_run_submission_count": 0,
            "phase_initialization_executed": False,
        }
        receipt_path = setup_dir / f"{configuration_id}_{case_id}.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        receipt_paths[key] = receipt_path
        receipt_hashes[key] = _sha(receipt_path)

    runner = _ProductionSensitivityCampaignRunner(daemon, project_id, workspace, bindings)
    source_hashes = {
        "study_run": _sha(sensitivity_science.STUDY_RUN_SOURCE),
        "history_capture": _sha(sensitivity_science.CAPTURE_SOURCE),
        "science_executor": _sha(sensitivity_science.SCIENCE_EXECUTOR_SOURCE),
        "setup_fixture": _sha(sensitivity_science.SETUP_FIXTURE_SOURCE),
        "setup_readback": _sha(sensitivity_science.SETUP_READBACK_SOURCE),
    }
    slots, binding_records = sensitivity_science._ordered_slot_records(project_id, bindings)
    plan = sensitivity_science.build_sensitivity_campaign_plan()
    estimate = sensitivity_science.sensitivity_capture_resource_estimate()
    approval = {
        "schema": sensitivity_science.APPROVAL_SCHEMA, "status": "APPROVED",
        "campaign_id": "w24-stop-test-0001", "project_id": project_id,
        "project_workspace": str(workspace.resolve()),
        "operation_store_path": str(daemon.store.path.resolve()),
        "configuration_order": plan["configuration_order"], "case_order": ["flat", "step"],
        "study_run_submissions": 14, "phase_initialization_steps_per_submission": 1,
        "capture_grid_protocol": sensitivity_science.SENSITIVITY_CAPTURE_PROTOCOL,
        "comparison_limits": plan["comparison_limits_after_each_case_passes"],
        "source_sha256": source_hashes, "setup_receipt_sha256": receipt_hashes,
        "model_bindings": binding_records, "ordered_slots": slots,
        "resource_limits": {
            "maximum_study_run_submissions": 14, "maximum_capture_files": 14,
            "maximum_single_capture_bytes": estimate["maximum_single_history_bytes"],
            "maximum_total_raw_capture_bytes": estimate["total_raw_capture_bytes"],
            "maximum_total_project_output_bytes": 10 * 1024 * 1024 * 1024,
            "maximum_single_solved_mph_bytes": 1024 * 1024 * 1024,
            "maximum_campaign_wall_time_s": 3000,
        },
    }
    approval_path = workspace / "sensitivity_approval.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True), encoding="utf-8")
    return (runner, worker_state, bindings, receipt_paths, approval_path,
            _sha(approval_path), source_hashes, daemon, services, daemons)


def _synthetic_v2_sensitivity_analysis(runner, binding, *, case_id, configuration_id, gate_status):
    raw_path = runner.workspace / "outputs" / f"capture_{configuration_id}_{case_id}.w24bin"
    raw_path.write_bytes(b"synthetic v2 capture receipt payload")
    return {
        "schema": "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V2",
        "status": "NATIVE_CAPTURE_DECODED",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED",
        "case_id": case_id, "configuration_id": configuration_id,
        "model_tag": binding.model_tag, "managed_binding": binding.as_record(),
        "capture_sha256": _sha(raw_path),
        "capture_grid_protocol": {"schema": sensitivity_science.SENSITIVITY_CAPTURE_PROTOCOL},
        "shape_history_gate": {"status": gate_status},
        "raw_capture": {"path": str(raw_path), "sha256": _sha(raw_path),
                        "size_bytes": raw_path.stat().st_size},
    }


@pytest.mark.parametrize(
    "stop_case,expected_worker_calls,expected_slots",
    [("baseline_unstable", 1, ["baseline:flat"]),
     ("variant_compare_fail", 3, ["baseline:flat", "baseline:step", "mesh_ratio_1_3:flat"]),
     ("variant_compare_error", 3, ["baseline:flat", "baseline:step", "mesh_ratio_1_3:flat"])],
)
def test_real_control_daemon_campaign_stops_and_preserves_first_failed_capture(
        tmp_path, monkeypatch, stop_case, expected_worker_calls, expected_slots):
    worker_state = None
    daemons = []
    (runner, worker_state, bindings, receipt_paths, approval_path, approval_sha,
     source_hashes, daemon, _services, daemons) = _real_sensitivity_campaign_fixture(tmp_path, monkeypatch)
    capture_calls = []

    def capture(_runner, binding, *, case_id, configuration_id, expected_capture_source_sha256):
        capture_calls.append(f"{configuration_id}:{case_id}")
        gate = ("FAIL" if stop_case == "baseline_unstable" and
                configuration_id == "baseline" and case_id == "flat" else "STABLE_WINDOW_PASS")
        return binding, _synthetic_v2_sensitivity_analysis(
            runner, binding, case_id=case_id,
            configuration_id=configuration_id, gate_status=gate)

    monkeypatch.setattr(sensitivity_science, "capture_static_shape_history", capture)
    if stop_case == "variant_compare_fail":
        monkeypatch.setattr(sensitivity_science, "compare_sensitivity_case_variant",
                            lambda *_args, **_kwargs: {"status": "FAIL", "controlled": True})
    elif stop_case == "variant_compare_error":
        def fail_comparison(*_args, **_kwargs):
            raise RuntimeError("synthetic immediate-comparison failure")
        monkeypatch.setattr(sensitivity_science, "compare_sensitivity_case_variant", fail_comparison)

    try:
        result = sensitivity_science.execute_sensitivity_campaign(
            runner, bindings, setup_receipt_paths=receipt_paths,
            approval_path=approval_path, expected_approval_sha256=approval_sha,
            expected_study_run_source_sha256=source_hashes["study_run"],
            expected_capture_source_sha256=source_hashes["history_capture"],
            expected_science_executor_sha256=source_hashes["science_executor"],
            expected_setup_fixture_source_sha256=source_hashes["setup_fixture"],
            expected_setup_readback_source_sha256=source_hashes["setup_readback"],
            birth_budget=BirthBudget(time.time(), budget_s=3600.0, cleanup_reserve_s=90.0),
            solve_ledger_path=runner.workspace / "outputs" /
            "static_shape_sensitivity_study_runs.jsonl")
        assert result["status"] == "FAIL_OR_INCOMPLETE_NO_RETRY"
        assert result["actual_study_run_submissions"] == expected_worker_calls
        assert worker_state["calls"] == expected_worker_calls
        assert worker_state["study_run_calls"] == expected_worker_calls
        assert capture_calls == expected_slots
        ledger_rows = sensitivity_science._read_sensitivity_ledger(
            runner.workspace / "outputs" / "static_shape_sensitivity_study_runs.jsonl",
            result["ordered_slots"], approval_sha, "w24-stop-test-0001")
        assert [f"{row['configuration_id']}:{row['case_id']}" for row in ledger_rows] == expected_slots
        assert len({row["slot_idempotency_key"] for row in ledger_rows}) == expected_worker_calls
        durable_rows = daemon.store.db.execute(
            "SELECT idempotency_key, status FROM operations WHERE idempotency_key LIKE 'w24-static-shape-slot-%'"
        ).fetchall()
        assert len(durable_rows) == expected_worker_calls
        assert {row["status"] for row in durable_rows} == {"SUCCEEDED"}
        saved_receipt = json.loads((runner.evidence_dir / "static_shape_sensitivity_campaign_receipt.json").read_text())
        failed_key = expected_slots[-1]
        assert failed_key in saved_receipt["cases"]
        assert saved_receipt["cases"][failed_key]["capture"]["raw_capture"]["size_bytes"] > 0
        assert (runner.workspace / "outputs" /
                f"capture_{failed_key.replace(':', '_')}.w24bin").is_file()
        if stop_case == "variant_compare_fail":
            assert saved_receipt["incremental_baseline_variant_comparisons"][failed_key]["status"] == "FAIL"
        elif stop_case == "variant_compare_error":
            assert saved_receipt["incremental_baseline_variant_comparisons"][failed_key]["status"] == "COMPARISON_ERROR"
            assert "synthetic immediate-comparison failure" in saved_receipt[
                "incremental_baseline_variant_comparisons"][failed_key]["error"]
    finally:
        for close_daemon in daemons:
            close_daemon.close()


def test_sensitivity_approval_pins_all_original_bindings_receipts_sources_and_wall_budget(tmp_path):
    (path, digest, sources, receipts, project_id, workspace, store_path,
     bindings, slots, payload) = _sensitivity_bindings_and_approval(tmp_path)
    accepted = sensitivity_science.validate_sensitivity_approval(
        path, expected_approval_sha256=digest, expected_source_sha256=sources,
        expected_setup_receipt_sha256=receipts, expected_project_id=project_id,
        expected_workspace=workspace, expected_operation_store_path=store_path,
        expected_bindings=bindings, expected_slots=slots)
    assert accepted["resource_limits"]["maximum_campaign_wall_time_s"] == 3000
    assert len(accepted["ordered_slots"]) == 14

    changed = dict(bindings)
    original = changed["baseline:flat"]
    changed["baseline:flat"] = ManagedModelBinding(
        project_id, original.session_id, original.model_ref, original.revision + 1)
    changed_slots, _ = sensitivity_science._ordered_slot_records(project_id, changed)
    with pytest.raises(CampaignError, match="ModelRefs and revisions"):
        sensitivity_science.validate_sensitivity_approval(
            path, expected_approval_sha256=digest, expected_source_sha256=sources,
            expected_setup_receipt_sha256=receipts, expected_project_id=project_id,
            expected_workspace=workspace, expected_operation_store_path=store_path,
            expected_bindings=changed, expected_slots=changed_slots)

    mutated_receipts = dict(receipts)
    mutated_receipts["baseline:flat"] = "f" * 64
    with pytest.raises(CampaignError, match="setup receipts"):
        sensitivity_science.validate_sensitivity_approval(
            path, expected_approval_sha256=digest, expected_source_sha256=sources,
            expected_setup_receipt_sha256=mutated_receipts, expected_project_id=project_id,
            expected_workspace=workspace, expected_operation_store_path=store_path,
            expected_bindings=bindings, expected_slots=slots)

    payload["resource_limits"].pop("maximum_campaign_wall_time_s")
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(CampaignError, match="wall-time resource ceiling"):
        sensitivity_science.validate_sensitivity_approval(
            path, expected_approval_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_source_sha256=sources, expected_setup_receipt_sha256=receipts,
            expected_project_id=project_id, expected_workspace=workspace,
            expected_operation_store_path=store_path, expected_bindings=bindings,
            expected_slots=slots)


def test_sensitivity_executor_rejects_changed_setup_receipt_before_any_worker_dispatch(
        tmp_path, monkeypatch):
    (path, _digest, _sources, _receipts, project_id, workspace, store_path,
     bindings, slots, payload) = _sensitivity_bindings_and_approval(tmp_path)
    live_sources = {
        "study_run": _sha(sensitivity_science.STUDY_RUN_SOURCE),
        "history_capture": _sha(sensitivity_science.CAPTURE_SOURCE),
        "science_executor": _sha(sensitivity_science.SCIENCE_EXECUTOR_SOURCE),
        "setup_fixture": _sha(sensitivity_science.SETUP_FIXTURE_SOURCE),
        "setup_readback": _sha(sensitivity_science.SETUP_READBACK_SOURCE),
    }
    payload["source_sha256"] = live_sources
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    approved_digest = hashlib.sha256(path.read_bytes()).hexdigest()

    source_paths = {}
    for role, source in (("fixture", sensitivity_science.SETUP_FIXTURE_SOURCE),
                         ("readback", sensitivity_science.SETUP_READBACK_SOURCE)):
        copied = workspace / source.name
        copied.write_bytes(source.read_bytes())
        source_paths[role] = copied
    setup_paths = {}
    (workspace / "outputs").mkdir(exist_ok=True)
    for key in sensitivity_science.CAPTURE_KEY_ORDER:
        receipt_path = workspace / "evidence" / f"{key.replace(':', '_')}_setup.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text("synthetic original setup receipt", encoding="utf-8")
        setup_paths[key] = receipt_path
    # The approved receipt SHA values intentionally remain from the earlier bytes.
    # The executor must reject this binding mismatch before calling a daemon or Worker.

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = OperationStore(store_path)
    runner = SimpleNamespace(
        project_id=project_id, workspace=workspace,
        daemon=SimpleNamespace(store=store), evidence_dir=workspace / "evidence",
        source_paths=source_paths, timeout_s=120.0,
        setup_runner=_FakeSetupReader(),
        _verify_persisted_binding=lambda _binding: pytest.fail("preflight must fail before binding dispatch"),
        _inspect=lambda _binding: pytest.fail("preflight must fail before managed inspection"),
    )
    try:
        with pytest.raises(CampaignError, match="setup receipts"):
            sensitivity_science.execute_sensitivity_campaign(
                runner, bindings,
                setup_receipt_paths=setup_paths,
                approval_path=path,
                expected_approval_sha256=approved_digest,
                expected_study_run_source_sha256=live_sources["study_run"],
                expected_capture_source_sha256=live_sources["history_capture"],
                expected_science_executor_sha256=live_sources["science_executor"],
                expected_setup_fixture_source_sha256=live_sources["setup_fixture"],
                expected_setup_readback_source_sha256=live_sources["setup_readback"],
                birth_budget=BirthBudget(time.time(), budget_s=3600.0, cleanup_reserve_s=90.0),
                solve_ledger_path=workspace / "outputs" / "static_shape_sensitivity_study_runs.jsonl",
            )
        assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    finally:
        store.close()


def _synthetic_analysis(runner: _FakeRunner, case: str, binding: ManagedModelBinding):
    path = runner.workspace / "outputs" / f"{case}.w24bin"
    raw = _raw_history()
    path.write_bytes(raw)
    record = binding.as_record()
    return {
        "schema": "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V1",
        "status": "NATIVE_CAPTURE_DECODED",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED",
        "model_tag": binding.model_tag, "case_id": case, "configuration_id": "baseline",
        "managed_binding": record,
        "shape_history_gate": {"status": "STABLE_WINDOW_PASS"},
        "parameters_and_units": {"epsPF": {"value_si": 8e-6, "unit": "m"}},
        "capture_grid_protocol": {"schema": "W24SHAP1/v1", "epsilon_m": 8e-6,
                                   "maximum_spacing_m": 2e-6},
        "raw_capture": {"path": str(path), "sha256": _sha(path), "size_bytes": len(raw),
                        "stored_time_count": 2, "radial_count": 3,
                        "profile_point_count": 9, "wall_point_count": 3},
        "native_result_feature_readbacks": {},
    }


def test_synthetic_managed_campaign_runs_exact_pair_then_captures_without_native_claim(tmp_path, monkeypatch):
    runner = _FakeRunner(tmp_path)
    bindings = {case: _binding(case) for case in ("flat", "step")}
    approval_path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, setup_receipt_sha) = _approval(approval_path, runner, bindings)
    monkeypatch.setattr(
        science, "capture_static_shape_history",
        lambda managed, binding, *, case_id, expected_capture_source_sha256:
        (binding, _synthetic_analysis(managed, case_id, binding)))
    budget = BirthBudget(time.time() - 1.0, budget_s=3600.0, cleanup_reserve_s=45.0)

    result = science.execute_baseline_flat_step_science(
        runner, bindings, setup_receipt_paths=setup_paths,
        approval_path=approval_path, expected_approval_sha256=approval_sha,
        expected_solve_source_sha256=solve_sha,
        expected_capture_source_sha256=capture_sha,
        expected_science_executor_sha256=executor_sha,
        expected_setup_fixture_source_sha256=fixture_sha,
        expected_setup_readback_source_sha256=readback_sha,
        birth_budget=budget,
        solve_ledger_path=tmp_path / "outputs" / "static_shape_study_runs.jsonl")

    assert result["status"] == "BASELINE_PAIR_CAPTURED_DESCRIPTIVE_ONLY"
    assert result["actual_study_run_submissions"] == 2
    assert len(runner.daemon.dispatch_attempts) == 2
    assert runner.daemon.worker_invocations == 2
    assert runner.inspected == ["model-flat", "model-step"]
    assert result["pre_solve_readbacks"]["flat"]["current_model_identity_inspection"][
        "data"]["structure"]["solutions"] == ["sol1"]
    assert result["native_acceptance"] == "NOT_ESTABLISHED_CAPTURE_AND_INDEPENDENT_REVIEW_REQUIRED"
    assert result["comparison"]["scientific_acceptance"] == "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED"


def test_mismatched_worker_epoch_refuses_before_native_dispatch(tmp_path):
    runner = _FakeRunner(tmp_path)
    bindings = {"flat": _binding("flat"), "step": _binding("step")}
    approval_path = tmp_path / "approval.json"
    (approval_sha, solve_sha, capture_sha, executor_sha, fixture_sha, readback_sha,
     setup_paths, setup_receipt_sha) = _approval(approval_path, runner, bindings)
    step = _binding("step")
    mismatched_ref = dict(step.model_ref, server_instance_id="server-2")
    mismatched = ManagedModelBinding(step.project_id, step.session_id, mismatched_ref, step.revision)

    with pytest.raises(CampaignError, match="same current Worker epoch"):
        science.execute_baseline_flat_step_science(
            runner, {"flat": _binding("flat"), "step": mismatched}, setup_receipt_paths=setup_paths,
            approval_path=approval_path, expected_approval_sha256=approval_sha,
            expected_solve_source_sha256=solve_sha,
            expected_capture_source_sha256=capture_sha,
            expected_science_executor_sha256=executor_sha,
            expected_setup_fixture_source_sha256=fixture_sha,
            expected_setup_readback_source_sha256=readback_sha,
            birth_budget=BirthBudget(time.time() - 1.0, budget_s=3600.0, cleanup_reserve_s=45.0),
            solve_ledger_path=tmp_path / "outputs" / "static_shape_study_runs.jsonl")
    assert runner.daemon.dispatch_attempts == []
