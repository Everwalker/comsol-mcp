from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import struct
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from tools import run_native_w24_cure_science as runner
from tools.w24_cure_v2_capture import (
    CaptureError, HISTORY_EXPRESSIONS, HISTORY_UNITS, verify_public_model_load,
)


def _write_utf(stream: io.BytesIO, value: str) -> None:
    raw = value.encode("utf-8")
    stream.write(struct.pack(">H", len(raw)))
    stream.write(raw)


def _snapshot_bytes(times: list[float]) -> bytes:
    names = ["comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_Duv_rel", "comp1_qpost",
             "comp1_u", "comp1_w"]
    payload = io.BytesIO()
    _write_utf(payload, "W24-DOF-SNAPSHOT-3")
    payload.write(struct.pack(">ii", 2, len(names)))
    payload.write(struct.pack(">i", len(names)))
    for name in names:
        _write_utf(payload, name)
        payload.write(struct.pack(">i", 1))
    payload.write(struct.pack(">i", len(names)))
    for name in names:
        _write_utf(payload, name)
    payload.write(struct.pack(">i", len(names)))
    for index in range(len(names)):
        payload.write(struct.pack(">iiii", 1, index + 1, index, index))
        payload.write(struct.pack(">dd", (index + 1) * 1e-6, 0.0))
    payload.write(struct.pack(">i", 1))
    _write_utf(payload, "geom1")
    payload.write(struct.pack(">i", 1))
    _write_utf(payload, "mesh1")
    payload.write(struct.pack(">ii", len(names), 2))
    for name in names:
        _write_utf(payload, name)
    payload.write(struct.pack(">" + "d" * len(names) * 2,
                               *([index * 1e-6 for index in range(len(names))] + [0.0] * len(names))))
    payload.write(struct.pack(">ii", 2, len(names)))
    payload.write(struct.pack(">" + "d" * len(names) * 2,
                               *([index * 1e-6 for index in range(len(names))] + [0.0] * len(names))))
    payload.write(struct.pack(">ii", 1, len(names)))
    for index in range(len(names)):
        payload.write(struct.pack(">i", index + 1))
    payload.write(struct.pack(">i", len(names)))
    for index in range(len(names)):
        payload.write(struct.pack(">i", index))
    payload.write(struct.pack(">i", len(times)))
    for time_s in times:
        values = [300.0 + time_s, 0.2, 0.1, time_s, 0.0, 1e-6, 0.0]
        payload.write(struct.pack(">di", time_s, len(values)))
        payload.write(struct.pack(">" + "d" * len(values), *values))
    return gzip.compress(payload.getvalue())


def _history_artifact(study_tag: str, solver_tag: str, times: list[float]) -> dict:
    coordinates = [[25e-6, 520e-6], [50e-6, 530e-6], [75e-6, 540e-6]]
    values = []
    for expression in HISTORY_EXPRESSIONS:
        series = []
        for time_s in times:
            if expression == "T":
                value = 300.0 + time_s
            elif expression == "alpha":
                value = 0.2
            elif expression == "Duv_rel":
                value = time_s
            elif expression == "qpost":
                value = 0.0
            elif expression in {"u", "w"}:
                value = 0.0
            else:
                value = 1.0
            series.append([value] * len(coordinates))
        values.append(series)
    shape = [len(HISTORY_EXPRESSIONS), len(times), len(coordinates)]
    feature = {
        "type": "Interp", "dataset": "dset-v2", "expressions": list(HISTORY_EXPRESSIONS),
        "units": list(HISTORY_UNITS), "solnum": "all", "coorderr": "on", "matherr": "on",
        "coordinates_m": coordinates, "coordinate_source": "Java setInterpolationCoordinates",
        "shape": shape,
    }
    return {
        "schema": "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1",
        "status": "NATIVE_HISTORY_CAPTURED_NO_SOLVE_SUBMITTED",
        "study_tag": study_tag, "solver_tag": solver_tag, "dataset_tag": "dset-v2",
        "dataset_type_requested": "Solution", "dataset_solution_readback": solver_tag,
        "stored_times_s": times, "time_source": "SolverSequence.getPVals",
        "study_tlist_readback": "fixture exact stored times", "quasistatic_readback": "Quasistatic",
        "expressions": list(HISTORY_EXPRESSIONS), "units": list(HISTORY_UNITS),
        "coordinates_m": coordinates, "shape": shape, "data": values,
        "feature_readback": feature, "native_study_run_calls": 0,
        "maxwell_branch_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
    }


def _contract_readback() -> dict:
    fields = {
        "comp1_T": 1e-4, "comp1_alpha": 1e-8, "comp1_alpha_iso": 1e-8,
        "comp1_qpost": 1e-8, "comp1_u": 1e-12, "comp1_w": 1e-12,
    }
    solvers = {}
    for stage in ("stdUV", "stdBake", "stdCool"):
        solvers[stage] = {
            "rtol": 1e-5, "atolglobalmethod": "unscaled", "atolglobal": 1e-8,
            "field_tolerances": {
                name: {"atolmethod": "unscaled", "atolvaluemethod": "manual", "atol": atol}
                for name, atol in fields.items()
            },
        }
    return {
        "cure_law_version": "W24_CURE_LAW_V2",
        "relative_exposure_dose": {
            "field": "Duv_rel", "unit": "s", "dependent_variable_quantity": "time",
            "source": "Irel", "source_term_quantity": "dimensionless", "initial": "0[s]",
            "source_scope": "adhesive_only",
        },
        "spatial_uv_readback": {
            "synthetic_estimated": True, "absolute_irradiance": False,
            "z_surface_m": 550e-6,
            "intensity_expression": "S_uv*exp(-muUV*(zUVSurface-z))",
        },
        "activation_readback": {
            "feature_type": "Activation", "alpha_gel": 0.5, "actfac": 1e-5,
            "actfac_was_set": False, "selection": [4],
        },
        "viscoelastic_readback": {
            "feature_type": "Viscoelasticity", "material_model": "GeneralizedMaxwell",
            "deformation_model": "full", "Kvm_v": ["K1"], "Gvm": ["G1"],
            "tauvm": ["300[s]"],
            "branch_order_binding": "same ordered index across Kvm_v/Gvm/tauvm",
        },
        "dose_solver_tolerance_readbacks": {
            stage: {"field": "comp1_Duv_rel", "scale": "unscaled", "method": "manual",
                    "absolute_tolerance": 1e-8}
            for stage in ("stdUV", "stdBake", "stdCool")
        },
        "solver_readbacks": solvers,
        "native_study_run_calls": 0,
    }


@pytest.mark.parametrize("readback", [{}, {"cure_law_version": "W24_CURE_LAW_V1"}])
def test_setup_router_keeps_legacy_v1_explicitly_outside_v2_acceptance(readback):
    result = runner._validated_setup_cure_v2_claim(readback)
    assert result["enabled"] is False
    assert result["status"] == "LEGACY_OR_UNDECLARED_V1_SETUP_NOT_V2_ACCEPTANCE"


@pytest.mark.parametrize("readback", [
    {"cure_law_version": "W24_CURE_LAW_V1", "relative_exposure_dose": {}},
    {"cure_law_version": "W24_CURE_LAW_V2"},
    {"cure_law_version": "W24_CURE_LAW_V2", "relative_exposure_dose": {"field": "Duv_rel"}},
    {"cure_law_version": "W24_CURE_LAW_V3"},
])
def test_setup_router_rejects_mixed_or_incomplete_v2_instead_of_falling_back(readback):
    with pytest.raises(runner.CampaignError, match="declares an incomplete or invalid cure-law v2"):
        runner._validated_setup_cure_v2_claim(readback)


@pytest.mark.parametrize("mutation", ["rtol_nan", "global_atol_infinite", "dose_atol_nan"])
def test_v2_contract_readback_rejects_nonfinite_solver_tolerances(mutation):
    from tools.w24_cure_v2_capture import CaptureError, validate_cure_law_v2_contract_readback

    readback = _contract_readback()
    if mutation == "rtol_nan":
        readback["solver_readbacks"]["stdUV"]["rtol"] = float("nan")
    elif mutation == "global_atol_infinite":
        readback["solver_readbacks"]["stdBake"]["atolglobal"] = float("inf")
    else:
        readback["dose_solver_tolerance_readbacks"]["stdCool"]["absolute_tolerance"] = float("nan")
    with pytest.raises(CaptureError):
        validate_cure_law_v2_contract_readback(readback)


class _RouteSnapshot:
    def __init__(self, server_instance_id: str = "server-link-test"):
        self.server_instance_id = server_instance_id

    def model_snapshot(self, model_tag):
        return {"model_tag": model_tag, "server_instance_id": self.server_instance_id,
                "fingerprint": "stable-link-test", "external_event_counter": 0}


class _LoadedModelTag:
    def __init__(self, tag: str):
        self._tag = tag
        self.java = self

    def tag(self):
        return self._tag


class _AuthenticatedLinkWorker:
    """No native engine: exercises the real daemon route and durable SQLite rows."""

    def __init__(self, times: list[float]):
        self.times = times
        self.study_runs = 0
        self.actions = []
        self.server_instance_id = "server-link-test"
        self.save_path_override = None
        self.omit_save_receipt = False
        self.save_bytes_override = None
        self.synthetic_ledger_prefix_count = 0

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, model_tag):
        return _RouteSnapshot(self.server_instance_id).model_snapshot(model_tag)

    def execute_java(self, model_tag, source_artifact, entrypoint, arguments, *, request_id=None):
        action = arguments["action"]
        self.actions.append(action)
        study_tag = arguments.get("study_tag", "stdUV")
        solver_tag = "sol-v2"
        if action == "readback":
            readback = {
                "status": "SCIENCE_ACTIONS_READY_NOT_SOLVED", "study_run_calls": 0,
                "studies": ["stdUV", "stdBake", "stdCool", "stdCont"],
                "solver_readbacks": {
                    study: {"sequence_attached": True, "sequence_study": study,
                            "timemethod": "bdf", "tstepsbdf": "strict", "tout": "tsteps",
                            "tstepsstore": 1, "maxstepbdf": 1.0, "sequence_tag": solver_tag}
                    for study in ("stdUV", "stdBake", "stdCool", "stdCont")
                },
                "study_time_readbacks": {study: {"tlist": "exact"} for study in
                                         ("stdUV", "stdBake", "stdCool", "stdCont")},
                "mesh_tags": ["mesh1"], "quasistatic_readback": "Quasistatic",
            }
        elif action == "cure_v2_contract_readback":
            readback = getattr(self, "contract_readback", _contract_readback())
        elif action == "study_run":
            self.study_runs += 1
            ledger_path = arguments.get("ledger_path")
            submission_index = self.study_runs
            if isinstance(ledger_path, str):
                ledger = Path(ledger_path)
                ledger.parent.mkdir(parents=True, exist_ok=True)
                rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
                        if line.strip()] if ledger.is_file() else []
                if not rows and self.synthetic_ledger_prefix_count:
                    rows = [
                        {"event": "study_run_submitted", "submission_index": index,
                         "case_id": slot.case_id, "study_tag": slot.study_tag,
                         "at_utc": f"synthetic-prior-submission-{index}"}
                        for index, slot in enumerate(
                            runner.SOLVE_PLAN[:self.synthetic_ledger_prefix_count], start=1)
                    ]
                    with ledger.open("w", encoding="utf-8") as stream:
                        stream.write("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
                submission_index = len(rows) + 1
                with ledger.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({
                        "event": "study_run_submitted", "submission_index": submission_index,
                        "case_id": arguments["case_id"], "study_tag": arguments["study_tag"],
                        "at_utc": "synthetic-worker-invocation",
                    }, sort_keys=True) + "\n")
            readback = {
                "status": "NATIVE_STUDY_RUN_RETURNED", "case_id": arguments["case_id"],
                "study_tag": arguments["study_tag"], "study_run_calls_from_this_action": 1,
                "solver_sequence": solver_tag, "submission_index": submission_index,
                "immediate_save_path": None, "immediate_save_receipt": None,
            }
            save_path = arguments.get("save_after_success_path")
            if isinstance(save_path, str):
                actual_path = Path(self.save_path_override) if self.save_path_override else Path(save_path)
                actual_path.parent.mkdir(parents=True, exist_ok=True)
                payload = self.save_bytes_override or (
                    b"synthetic model bytes saved by the Study.run route: " +
                    f"{arguments['case_id']}:{arguments['study_tag']}".encode())
                actual_path.write_bytes(payload)
                readback["immediate_save_path"] = str(actual_path)
                if not self.omit_save_receipt:
                    readback["immediate_save_receipt"] = {
                        "status": "STUDY_RUN_MPH_SAVED_AND_HASHED",
                        "path": str(actual_path), "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
        elif action == "solution_snapshot_v3":
            path = Path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = _snapshot_bytes(self.times)
            path.write_bytes(raw)
            readback = {
                "status": "SOLUTION_SNAPSHOT_V3_WRITTEN", "schema": "W24-DOF-SNAPSHOT-3",
                "solver_tag": solver_tag, "study_tag": study_tag, "path": str(path),
                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "stored_time_count": len(self.times), "xmesh_n_dofs": 7,
                "dof_count": 7,
                "field_names": ["comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_Duv_rel", "comp1_qpost", "comp1_u", "comp1_w"],
                "field_ndofs": [1] * 7, "coordinate_axes": 2,
                "real_solution": True, "complete_xmesh_dofs": True,
                "complete_internal_dof_capture": True,
                "unmapped_xmesh_dof_rows": 0, "invalid_xmesh_solution_indices": 0,
                "out_of_range_xmesh_solution_indices": 0, "duplicate_solution_vector_index_rows": 0,
                "unrepresented_solution_vector_indices": 0,
                "invalid_element_dof_references": 0,
                "element_local_map_group_count": 1, "element_local_map_entries": 7,
                "sol_vector_length": 7,
                "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
                "study_tlist_readback": "fixture stored times", "quasistatic_readback": "Quasistatic",
            }
        elif action == "history_capture_v2":
            path = Path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            artifact = _history_artifact(study_tag, solver_tag, self.times)
            raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
            path.write_bytes(raw)
            readback = {
                "status": "NATIVE_HISTORY_CAPTURE_WRITTEN", "schema": artifact["schema"],
                "study_tag": study_tag, "solver_tag": solver_tag, "dataset_tag": "dset-v2",
                "path": str(path), "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }
        elif action == "solution_snapshot":
            path = Path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = _snapshot_bytes(self.times)
            path.write_bytes(raw)
            readback = {
                "status": "SOLUTION_SNAPSHOT_WRITTEN", "solver_tag": solver_tag,
                "path": str(path), "size_bytes": len(raw),
                "dof_count": 7, "stored_time_count": len(self.times),
                "dof_names": ["comp1_T", "comp1_alpha", "comp1_alpha_iso",
                              "comp1_Duv_rel", "comp1_qpost", "comp1_u", "comp1_w"],
                "real_solution": True,
            }
        elif action == "cure_metrics_capture":
            path = Path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (json.dumps({"status": "NATIVE_CURE_METRICS_CAPTURED",
                               "solver_tag": solver_tag}, sort_keys=True) + "\n").encode()
            path.write_bytes(raw)
            readback = {"status": "NATIVE_CURE_METRICS_CAPTURED", "solver_tag": solver_tag,
                        "path": str(path), "size_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}
        else:
            raise AssertionError(f"unexpected harmless route action: {action}")
        source_hash = hashlib.sha256(Path(source_artifact).read_bytes()).hexdigest()
        java = {"executed": True, "model_tag": model_tag, "source_sha256": source_hash,
                "entrypoint": entrypoint, "readback": readback}
        return {"ok": True, "status": "SUCCEEDED", "result": {"readback": java}}


def _real_daemon_adapter(tmp_path: Path, monkeypatch, times: list[float]):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    worker = _AuthenticatedLinkWorker(times)
    service = ExecutionService(
        SessionLedger("session-link", "server-link-test",
                      permissions={"inspect", "project_write", "compute", "trusted_code"}),
        _RouteSnapshot(), project_root=projects)
    def model_load(arguments):
        from comsol_mcp import _server
        model_tag = "loaded-" + Path(arguments["path"]).stem
        _server._current_model = _LoadedModelTag(model_tag)
        return {"success": True, "data": {"model_tag": model_tag,
                                             "requested_path": arguments["path"]}}

    daemon = ControlDaemon(tmp_path / "control", service=service,
                           registry={"model_load": model_load}, worker=worker,
                           project_root=projects)
    daemon.backend.worker_identity = {
        "runtime_id": "capture-link-test-runtime",
        "worker_instance_id": "capture-link-test-worker",
        "connection_epoch": 1,
        "server_instance_id": "server-link-test",
    }
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "capture-link", "workspace": "capture-link",
                      "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
        "execution": {"request_id": "create-link-project", "idempotency_key": "create-link-project"},
    })
    assert created["success"] is True
    project_id = created["data"]["project"]["project_id"]
    project = projects / "capture-link"
    sources = {}
    for name in ("W24CureScienceFixture.java", "W24CureLawV2ControlFixture.java",
                 "W24CureCouponFixture.java"):
        source = project / "tools/java" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"// test source {name}\n", encoding="utf-8")
        sources[name] = source
    bound = service.bind_model("model-link")
    model_ref = bound["execution"]["model_ref"]
    daemon.backend._bind_model_project(model_ref, project_id)
    revision_key = daemon.backend._model_project_key(model_ref)
    daemon.store.put_metadata("revisions", revision_key, {
        "model_ref": model_ref, "project_id": project_id, "attribution": "PROJECT_BOUND",
        "revision": 0, "dirty": False, "fingerprint": "stable-link-test",
        "active_operation_id": None,
    })
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    binding = runner.ManagedModelBinding(project_id, "session-link", model_ref, 0)
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.daemon = daemon
    adapter.project_id = project_id
    adapter._immutable_project_id = project_id
    adapter.workspace = project.resolve(strict=True)
    adapter.evidence = project / "evidence"
    adapter.fixture = sources["W24CureScienceFixture.java"]
    adapter.v2_control_fixture = sources["W24CureLawV2ControlFixture.java"]
    adapter.coupon_fixture = sources["W24CureCouponFixture.java"]
    adapter.fixture_source_hashes = {
        "science": runner.sha256(adapter.fixture),
        "v2_control": runner.sha256(adapter.v2_control_fixture),
        "coupon": runner.sha256(adapter.coupon_fixture),
    }
    adapter.cure_law_v2_enabled = True
    adapter.stress_components = {}
    adapter.models = {binding.model_tag: binding}
    adapter.worker_sessions = 1
    adapter.mechanics_models = {}
    adapter.captures = {}
    adapter.slot_solver_tags = {}
    adapter.v2_contract_readbacks = {}
    adapter.slot_native_setup_readbacks = {}
    adapter.slot_study_run_actions = {}
    adapter.staged_baseline_saved_model = None
    adapter.project_ledger = None
    adapter.setup_runner = SimpleNamespace(
        _worker_request_terminal=lambda response: response.get("success") is True and
            response.get("data", {}).get("worker", {}).get("status") == "SUCCEEDED",
        _java_action_readback=lambda response, _label: response["data"]["readback"]["readback"]["readback"],
    )
    adapter._dispatch = lambda operation, arguments, *, binding=None, timeout_s=120.0: __import__(
        "tools.run_native_resume_smoke", fromlist=["_dispatch"])._dispatch(
            adapter.daemon, operation, dict(arguments), project_id=project_id,
            ref=dict(binding.model_ref) if binding is not None else None,
            revision=binding.revision if binding is not None else None,
            idempotency_key=f"capture-link-{hashlib.sha256((str(arguments)+str(binding.model_ref if binding else None)+str(binding.revision if binding else 0)).encode()).hexdigest()}",
            request_id=f"capture-link-request-{hashlib.sha256((str(arguments)+str(binding.model_ref if binding else None)+str(binding.revision if binding else 0)).encode()).hexdigest()}",
            rpc_timeout_s=timeout_s)
    adapter._log_response = lambda *_args, **_kwargs: None
    return daemon, worker, adapter, binding


def _make_authenticated_capture(adapter, binding, study_tag: str, times: list[float], tmp_path: Path,
                               *, case_id: str = "staged_baseline", include_study_run: bool = True,
                               reference_suffix: str = "", save_after_success_path: Path | None = None,
                               ledger_path: Path | None = None):
    adapter.daemon.backend.worker.times = list(times)
    slot = next((row for row in runner.SOLVE_PLAN
                 if row.case_id == case_id and row.study_tag == study_tag),
                runner.SolveSlot(case_id, study_tag, "synthetic capture-link test"))
    adapter.slot_solver_tags[(slot.case_id, slot.study_tag)] = "sol-v2"
    state_key = f"{slot.case_id}:{slot.study_tag}"

    setup_before = binding
    response_token = f"{study_tag}{reference_suffix}"
    binding, setup_response, _setup = adapter._fixture_action(
        binding, "readback", {}, timeout_s=10.0, source_fixture=adapter.fixture)
    setup_ref = adapter._record_public_java_action(
        action="readback", response=setup_response, binding_before=setup_before,
        binding_after=binding, source_fixture=adapter.fixture,
        response_dir=adapter.evidence / "responses", response_label=f"{response_token}-setup")
    adapter.slot_native_setup_readbacks[state_key] = setup_ref

    binding, contract = adapter._ensure_v2_contract_readback(
        slot, binding, timeout_s=10.0, state_key=state_key)
    assert contract is not None

    study_ref = None
    if include_study_run:
        if ledger_path is not None:
            (adapter.workspace / "native").mkdir(exist_ok=True)
            adapter.models[case_id] = binding
            adapter.run_study(slot, ledger_path=ledger_path,
                              save_after_success_path=save_after_success_path, timeout_s=10.0)
            binding = adapter.models[case_id]
            study_ref = adapter.slot_study_run_actions[state_key]
        else:
            study_before = binding
            binding, study_response, _study = adapter._fixture_action(
                binding, "study_run", {"case_id": slot.case_id, "study_tag": study_tag},
                timeout_s=10.0, source_fixture=adapter.fixture,
                entrypoint="W24CureScienceFixture#run")
            study_ref = adapter._record_public_java_action(
                action="study_run", response=study_response, binding_before=study_before,
                binding_after=binding, source_fixture=adapter.fixture,
                response_dir=adapter.evidence / "responses", response_label=f"{response_token}-study")
            adapter.slot_study_run_actions[state_key] = study_ref

    (adapter.workspace / "native").mkdir(exist_ok=True)
    snapshot_path = adapter.workspace / f"native/{response_token}-snapshot.gz"
    snapshot_before = binding
    binding, snapshot_response, snapshot_receipt = adapter._fixture_action(
        binding, "solution_snapshot_v3",
        {"study_tag": study_tag, "solver_tag": "sol-v2", "path": str(snapshot_path)},
        timeout_s=10.0, source_fixture=adapter.v2_control_fixture,
        entrypoint="W24CureLawV2ControlFixture#run")
    snapshot_ref = adapter._record_public_java_action(
        action="solution_snapshot_v3", response=snapshot_response,
        binding_before=snapshot_before, binding_after=binding,
        source_fixture=adapter.v2_control_fixture,
        response_dir=adapter.evidence / "responses", response_label=f"{response_token}-snapshot")
    snapshot_evidence = runner._evidence_copy(
        snapshot_path, adapter.evidence / f"{response_token}-snapshot.gz",
        status="V3_PUBLIC_FULL_XMESH_SNAPSHOT_NATIVE_REVIEW_REQUIRED")

    history_path = adapter.workspace / f"native/{response_token}-history.json"
    history_before = binding
    binding, history_response, history_receipt = adapter._fixture_action(
        binding, "history_capture_v2",
        {"study_tag": study_tag, "solver_tag": "sol-v2", "path": str(history_path)},
        timeout_s=10.0, source_fixture=adapter.fixture,
        entrypoint="W24CureScienceFixture#run")
    history_ref = adapter._record_public_java_action(
        action="history_capture_v2", response=history_response,
        binding_before=history_before, binding_after=binding,
        source_fixture=adapter.fixture,
        response_dir=adapter.evidence / "responses", response_label=f"{response_token}-history")
    history_evidence = runner._evidence_copy(
        history_path, adapter.evidence / f"{response_token}-history.json",
        status="V2_PUBLIC_HISTORY_CAPTURE_NATIVE_REVIEW_REQUIRED")

    snapshot_verified = adapter._reauthenticate_public_java_action(snapshot_ref)
    history_verified = adapter._reauthenticate_public_java_action(history_ref)
    capture = {
        "cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED",
        "v2_capture": {
            "status": "V2_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED",
            "case_id": slot.case_id, "study_tag": study_tag, "solver_tag": "sol-v2",
            "model_contract": {"reference": contract["reference"]},
            "setup_readback": setup_ref, "study_run": study_ref,
            "solution_snapshot": {
                "operation": snapshot_ref, "evidence": snapshot_evidence,
                "artifact_receipt": snapshot_receipt,
                "capture_validation": snapshot_verified["capture_validation"],
            },
            "history_capture": {
                "operation": history_ref, "evidence": history_evidence,
                "artifact_receipt": history_receipt,
                "capture_validation": history_verified["capture_validation"],
                "stored_times_s": times,
            },
            "snapshot_stored_times_s": times,
            "model_binding_after_history": binding.as_record(),
            "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
            "native_acceptance": "NOT_RUN",
        },
    }
    return binding, slot, capture


def _prepare_production_v2_slot(adapter, binding, slot, *, run_study: bool,
                                state_key: str | None = None, label: str,
                                ledger_path: Path | None = None,
                                save_after_success_path: Path | None = None):
    """Drive the real public setup/contract/Study.run routes used by production."""
    state_key = state_key or f"{slot.case_id}:{slot.study_tag}"
    adapter.slot_solver_tags[(slot.case_id, slot.study_tag)] = "sol-v2"
    adapter.evidence.mkdir(parents=True, exist_ok=True)
    (adapter.workspace / "native").mkdir(exist_ok=True)
    setup_before = binding
    binding, setup_response, _setup = adapter._fixture_action(
        binding, "readback", {}, timeout_s=10.0, source_fixture=adapter.fixture)
    setup_ref = adapter._record_public_java_action(
        action="readback", response=setup_response, binding_before=setup_before,
        binding_after=binding, source_fixture=adapter.fixture,
        response_dir=adapter.evidence / "responses", response_label=f"{label}-setup")
    adapter.slot_native_setup_readbacks[state_key] = setup_ref
    binding, contract = adapter._ensure_v2_contract_readback(
        slot, binding, timeout_s=10.0, state_key=state_key)
    assert contract is not None
    adapter.models[slot.case_id] = binding
    study_ref = None
    if run_study:
        if ledger_path is not None:
            (adapter.workspace / "native").mkdir(exist_ok=True)
            adapter.run_study(slot, ledger_path=ledger_path,
                              save_after_success_path=save_after_success_path, timeout_s=10.0)
            binding = adapter.models[slot.case_id]
            study_ref = adapter.slot_study_run_actions[f"{slot.case_id}:{slot.study_tag}"]
        else:
            study_before = binding
            binding, study_response, _study = adapter._fixture_action(
                binding, "study_run", {"case_id": slot.case_id, "study_tag": slot.study_tag},
                timeout_s=10.0, source_fixture=adapter.fixture,
                entrypoint="W24CureScienceFixture#run")
            study_ref = adapter._record_public_java_action(
                action="study_run", response=study_response, binding_before=study_before,
                binding_after=binding, source_fixture=adapter.fixture,
                response_dir=adapter.evidence / "responses", response_label=f"{label}-study")
            adapter.slot_study_run_actions[f"{slot.case_id}:{slot.study_tag}"] = study_ref
    return binding, setup_ref, contract, study_ref



def test_production_capture_pair_tracks_actual_revision_steps_and_reauthenticates(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 1.0])
    try:
        slot = runner.SolveSlot("staged_baseline", "stdUV", "production chain fixture")
        binding, setup_ref, contract, study_ref = _prepare_production_v2_slot(
            adapter, binding, slot, run_study=True, label="production")
        assert setup_ref["binding_after"]["revision"] == contract["reference"]["binding_before"]["revision"]
        assert contract["reference"]["binding_after"]["revision"] == study_ref["binding_before"]["revision"]
        assert study_ref["binding_after"]["revision"] == binding.revision
        assert binding.revision >= setup_ref["binding_before"]["revision"] + 3

        output_dir = adapter.evidence / "production-capture"
        output_dir.mkdir()
        binding, report = adapter._capture_v2_pair(
            slot, binding, output_dir, adapter.workspace / "native/stdUV", "production-stdUV",
            "sol-v2", timeout_s=10.0)
        capture = {"cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED", "v2_capture": report}
        frames, lineage = adapter._authenticated_v2_capture_frames(
            capture, expected_case=slot.case_id, expected_study=slot.study_tag)
        snapshot_ref = report["solution_snapshot"]["operation"]
        history_ref = report["history_capture"]["operation"]
        assert snapshot_ref["binding_before"]["revision"] == study_ref["binding_after"]["revision"]
        assert snapshot_ref["binding_after"]["revision"] == history_ref["binding_before"]["revision"]
        assert report["model_binding_after_history"]["revision"] == binding.revision
        assert lineage["source_identity_authenticated"] is True
        assert lineage["native_acceptance"] == "NOT_RUN"
        assert worker.study_runs == 1
        assert len(frames) == 2
    finally:
        daemon.close()


def test_production_worker2_reopen_rejects_missing_complete_staged_source_chain(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [960.0, 1500.0])
    try:
        worker.synthetic_ledger_prefix_count = 4
        slot = next(row for row in runner.SOLVE_PLAN
                    if row.case_id == "staged_baseline" and row.study_tag == "stdCool")
        saved_path = adapter.workspace / "native/staged_baseline_saved.mph"
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        binding, _setup_ref, _contract, producer_ref = _prepare_production_v2_slot(
            adapter, binding, slot, run_study=True, label="original",
            ledger_path=ledger_path, save_after_success_path=saved_path)
        assert producer_ref is not None
        assert adapter.staged_baseline_saved_model["producer_study_run"] == producer_ref
        saved_hash = runner.sha256(saved_path)
        original_dir = adapter.evidence / "original-stdCool-capture"
        original_dir.mkdir()
        binding, original_report = adapter._capture_v2_pair(
            slot, binding, original_dir, adapter.workspace / "native/stdCool-original",
            "original-stdCool", "sol-v2", timeout_s=10.0)
        original_capture = {
            "cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED",
            "v2_capture": original_report,
        }
        original_frames, _original_lineage = adapter._authenticated_v2_capture_frames(
            original_capture, expected_case=slot.case_id, expected_study="stdCool")
        assert len(original_frames) == 2

        daemon.backend.worker_identity = {
            "runtime_id": "capture-link-test-runtime-worker2",
            "worker_instance_id": "capture-link-test-worker2",
            "connection_epoch": 2,
            "server_instance_id": "server-link-test-worker2",
        }
        daemon.service.ledger.server_instance_id = "server-link-test-worker2"
        daemon.service.adapter.server_instance_id = "server-link-test-worker2"
        worker.server_instance_id = "server-link-test-worker2"
        load_response = daemon.dispatch({
            "operation": "model_load", "arguments": {"path": str(saved_path)},
            "execution": {"project_id": adapter.project_id, "session_id": "session-link",
                          "request_id": "worker2-production-load-request",
                          "idempotency_key": "worker2-production-load-key"},
        })
        assert load_response["success"] is True, load_response
        loaded = runner.ManagedModelBinding.from_load_response(
            load_response, project_id=adapter.project_id)
        assert loaded.model_ref["server_instance_id"] == "server-link-test-worker2"
        public_load_ref = adapter._record_public_model_load(
            name="staged_baseline_worker2", path=saved_path,
            binding=loaded, response=load_response)
        load_receipt = {
            "model_name": "staged_baseline", "input_path": str(saved_path),
            "input_sha256": saved_hash, "binding": loaded.as_record(),
            "public_identity": public_load_ref["public_identity"],
            "response_evidence": public_load_ref["response_evidence"],
            "public_load_reference": public_load_ref,
        }

        adapter.worker_sessions = 2
        worker2_state = f"{slot.case_id}:{slot.study_tag}:worker2"
        loaded, _worker2_setup, _worker2_contract, worker2_study = _prepare_production_v2_slot(
            adapter, loaded, slot, run_study=False, state_key=worker2_state, label="worker2")
        assert worker2_study is None
        reopen_dir = adapter.evidence / "worker2-reopened-capture"
        reopen_dir.mkdir()
        with pytest.raises(runner.CampaignError, match="complete original staged source chain"):
            adapter._capture_v2_pair(
                slot, loaded, reopen_dir, adapter.workspace / "native/stdCool-worker2",
                "worker2-stdCool", "sol-v2", timeout_s=10.0,
                reopened_origin=original_capture, reopened_model_load=load_receipt)
        assert worker.study_runs == 1, "Worker2 capture must use the saved producer without a second Study.run"
    finally:
        daemon.close()


def test_real_control_daemon_authenticated_capture_reaches_full_dof_schedule_comparison(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 1.0])
    try:
        binding, slot, capture = _make_authenticated_capture(adapter, binding, "stdUV", [0.0, 1.0], tmp_path)
        adapter.models[slot.case_id] = binding
        frames, lineage = adapter._authenticated_v2_capture_frames(
            capture, expected_case=slot.case_id, expected_study=slot.study_tag)
        assert lineage["source_identity_authenticated"] is True
        assert lineage["native_acceptance"] == "NOT_RUN"
        assert len(frames) == 2
        assert set(frames[0]["dofs"]["dofNames"]) == {
            "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_Duv_rel",
            "comp1_qpost", "comp1_u", "comp1_w"}
        result = adapter._compare_authenticated_v2_frames(
            frames, frames, [lineage], [lineage], label="same authenticated public schedule")
        assert result["status"] == (
            "V2_AUTHENTICATED_VISIBLE_HISTORY_GATES_MATCH_FULL_XMESH_DIAGNOSTIC_ONLY"
            "_TOLERANCE_NOT_FROZEN_BRANCH_STATE_UNVERIFIED")
        assert "PASS" not in result["status"]
        assert result["frozen_visible_history_gates"] == "PASS"
        assert result["full_xmesh_diagnostics"]["status"] == "FULL_XMESH_DIAGNOSTICS_ONLY_TOLERANCE_NOT_FROZEN"
        assert result["source_identity_authenticated"] is True
        assert result["native_acceptance"] == "NOT_RUN"
        assert result["maxwell_branch_reference_state"].startswith("UNVERIFIED")
        forged_frames = copy.deepcopy(frames)
        forged_frames[0]["u_real"][4] = 123.0
        with pytest.raises(runner.CampaignError, match="differs from the exact frame parsed"):
            adapter._compare_authenticated_v2_frames(
                forged_frames, frames, [lineage], [lineage],
                label="caller-edited internal solution vector")
        forged_lineage = copy.deepcopy(lineage)
        forged_lineage["snapshot_sha256"] = "0" * 64
        with pytest.raises(runner.CampaignError, match="complete authenticated lineage content"):
            adapter._compare_authenticated_v2_frames(
                frames, frames, [forged_lineage], [lineage],
                label="caller-edited capture identity")
        assert worker.study_runs == 1
    finally:
        daemon.close()


@pytest.mark.parametrize("mutation", [
    "nested_model_ref", "nested_operation_ids", "nested_tolerance",
    "maxwell_state", "native_status",
])
def test_authenticated_capture_rejects_in_place_lineage_mutation(
        tmp_path, monkeypatch, mutation):
    daemon, _worker, adapter, binding = _real_daemon_adapter(
        tmp_path, monkeypatch, [0.0, 1.0])
    try:
        binding, slot, capture = _make_authenticated_capture(
            adapter, binding, "stdUV", [0.0, 1.0], tmp_path)
        frames, lineage = adapter._authenticated_v2_capture_frames(
            capture, expected_case=slot.case_id, expected_study=slot.study_tag)
        if mutation == "nested_model_ref":
            lineage["model_ref"]["generation"] += 1
        elif mutation == "nested_operation_ids":
            lineage["operation_ids"][0] = "caller-mutated-operation"
        elif mutation == "nested_tolerance":
            key = next(iter(lineage["full_dof_absolute_tolerances"]))
            lineage["full_dof_absolute_tolerances"][key] *= 2.0
        elif mutation == "maxwell_state":
            lineage["maxwell_branch_reference_state"] = "CALLER_ASSERTED_VERIFIED"
        else:
            lineage["native_acceptance"] = "PASS"
        with pytest.raises(runner.CampaignError, match="complete authenticated lineage content"):
            adapter._validate_authenticated_v2_frame_set(
                frames, [lineage], label=f"in-place lineage mutation: {mutation}")
    finally:
        daemon.close()


def test_authenticated_capture_rejects_mutated_operation_identity_and_snapshot_times(tmp_path, monkeypatch):
    daemon, _worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 1.0])
    try:
        binding, slot, capture = _make_authenticated_capture(adapter, binding, "stdUV", [0.0, 1.0], tmp_path)
        bad_operation = copy.deepcopy(capture)
        bad_operation["v2_capture"]["study_run"]["public_identity"]["job_id"] = "foreign-job"
        with pytest.raises(runner.CampaignError, match="identity differs"):
            adapter._authenticated_v2_capture_frames(
                bad_operation, expected_case=slot.case_id, expected_study=slot.study_tag)

        bad_times = copy.deepcopy(capture)
        bad_times["v2_capture"]["snapshot_stored_times_s"] = [0.0, 2.0]
        with pytest.raises(runner.CampaignError, match="stored times"):
            adapter._authenticated_v2_capture_frames(
                bad_times, expected_case=slot.case_id, expected_study=slot.study_tag)
    finally:
        daemon.close()


def test_staged_handoffs_and_continuous_schedule_use_authenticated_public_sources(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 120.0])
    try:
        worker.synthetic_ledger_prefix_count = 2
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        saved_path = adapter.workspace / "native/staged-baseline-terminal.mph"
        stage_specs = (
            ("stdUV", [0.0, 120.0]),
            ("stdBake", [120.0, 960.0]),
            ("stdCool", [960.0, 1500.0]),
        )
        staged_captures = {}
        for study_tag, times in stage_specs:
            binding, slot, capture = _make_authenticated_capture(
                adapter, binding, study_tag, times, tmp_path,
                save_after_success_path=saved_path if study_tag == "stdCool" else None,
                ledger_path=ledger_path)
            staged_captures[f"staged_baseline:{study_tag}"] = capture
            adapter.models[slot.case_id] = binding

        binding, continuous_slot, continuous_capture = _make_authenticated_capture(
            adapter, binding, "stdCont", [0.0, 120.0, 960.0, 1500.0], tmp_path,
            case_id="continuous_comparator", ledger_path=ledger_path)
        adapter.models[continuous_slot.case_id] = binding

        staged_frames, staged_lineages = adapter._authenticated_staged_v2_schedule(staged_captures)
        continuous_frames, continuous_lineage = adapter._authenticated_v2_capture_frames(
            continuous_capture, expected_case="continuous_comparator", expected_study="stdCont")
        ledger_rows = runner.read_solve_ledger(ledger_path)
        assert len(ledger_rows) == 6
        assert [row["at_utc"] for row in ledger_rows[:2]] == [
            "synthetic-prior-submission-1", "synthetic-prior-submission-2"]
        assert (ledger_rows[-1]["case_id"], ledger_rows[-1]["study_tag"]) == (
            "continuous_comparator", "stdCont")
        assert [row["time_s"] for row in staged_frames] == [0.0, 120.0, 960.0, 1500.0]
        comparison = adapter._compare_authenticated_v2_frames(
            staged_frames, continuous_frames, staged_lineages, [continuous_lineage],
            label="authenticated staged versus continuous schedule")
        assert comparison["status"] == (
            "V2_AUTHENTICATED_VISIBLE_HISTORY_GATES_MATCH_FULL_XMESH_DIAGNOSTIC_ONLY"
            "_TOLERANCE_NOT_FROZEN_BRANCH_STATE_UNVERIFIED")
        assert comparison["source_identity_authenticated"] is True

        uv_frames, uv_lineage = adapter._authenticated_v2_capture_frames(
            staged_captures["staged_baseline:stdUV"],
            expected_case="staged_baseline", expected_study="stdUV")
        bake_frames, bake_lineage = adapter._authenticated_v2_capture_frames(
            staged_captures["staged_baseline:stdBake"],
            expected_case="staged_baseline", expected_study="stdBake")
        uv_index = next(i for i, row in enumerate(uv_frames) if row["time_s"] == 120.0)
        bake_index = next(i for i, row in enumerate(bake_frames) if row["time_s"] == 120.0)
        handoff = adapter._compare_authenticated_v2_frames(
            [uv_frames[uv_index]], [bake_frames[bake_index]], [uv_lineage], [bake_lineage],
            label="authenticated stdUV→stdBake boundary", handoff=True)
        assert handoff["status"] == (
            "V2_AUTHENTICATED_VISIBLE_HISTORY_GATES_MATCH_FULL_XMESH_DIAGNOSTIC_ONLY"
            "_TOLERANCE_NOT_FROZEN_BRANCH_STATE_UNVERIFIED")
        assert handoff["source_identity_authenticated"] is True
    finally:
        daemon.close()


def test_worker2_reopen_capture_authenticates_saved_producer_load_and_current_chain(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [960.0, 1500.0])
    try:
        source_specs = (("stdUV", [0.0, 120.0]),
                        ("stdBake", [120.0, 960.0]),
                        ("stdCool", [960.0, 1500.0]))
        source_captures = {}
        saved_path = adapter.workspace / "native/staged-baseline-saved.mph"
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        worker.synthetic_ledger_prefix_count = 4
        for source_study, source_times in source_specs:
            binding, _source_slot, source_capture = _make_authenticated_capture(
                adapter, binding, source_study, source_times, tmp_path,
                save_after_success_path=saved_path if source_study == "stdCool" else None,
                ledger_path=ledger_path if source_study == "stdCool" else None)
            source_captures[f"staged_baseline:{source_study}"] = source_capture
        original_capture = source_captures["staged_baseline:stdCool"]
        original_report = original_capture["v2_capture"]
        saved_hash = runner.sha256(saved_path)
        assert adapter.staged_baseline_saved_model["sha256"] == saved_hash
        assert adapter.staged_baseline_saved_model["producer_study_run"] == original_report["study_run"]

        loaded_response = daemon.dispatch({
            "operation": "model_load", "arguments": {"path": str(saved_path)},
            "execution": {"project_id": adapter.project_id, "session_id": "session-link",
                          "request_id": "worker2-model-load-request",
                          "idempotency_key": "worker2-model-load-key"},
        })
        assert loaded_response["success"] is True, loaded_response
        operation_row = daemon.store.get_operation(loaded_response["execution"]["operation_id"])
        job_row = daemon.store.operation_job(loaded_response["execution"]["operation_id"])
        assert (operation_row.get("operation"), operation_row.get("status"),
                job_row.get("status"), job_row.get("operation_id")) == (
                    "model_load", "SUCCEEDED", "SUCCEEDED", operation_row.get("operation_id"))
        assert daemon.store.job(job_row["job_id"]) == job_row, (
            "model_load job identity must come from the durable Job row joined by operation_id")
        loaded_binding = runner.ManagedModelBinding(
            adapter.project_id, "session-link", loaded_response["execution"]["model_ref"],
            loaded_response["execution"]["revision"])
        public_load_ref = adapter._record_public_model_load(
            name="staged_baseline_worker2", path=saved_path,
            binding=loaded_binding, response=loaded_response)
        load_receipt = {
            "model_name": "staged_baseline", "input_path": str(saved_path),
            "input_sha256": saved_hash, "binding": loaded_binding.as_record(),
            "public_identity": public_load_ref["public_identity"],
            "response_evidence": public_load_ref["response_evidence"],
            "public_load_reference": public_load_ref,
            "persisted_project_binding": loaded_response.get("data", {}).get("persisted_project_binding"),
        }
        assert adapter._reauthenticate_public_model_load(load_receipt)["sha256"] == saved_hash

        load_identity = {
            "project_root": adapter.workspace,
            "requested_path": saved_path,
            "expected_file_sha256": saved_hash,
            "expected_project_id": adapter.project_id,
            "expected_session_id": loaded_binding.session_id,
            "expected_model_ref": loaded_binding.model_ref,
            "expected_revision": loaded_binding.revision,
        }
        authoritative_job_id = job_row["job_id"]
        assert public_load_ref["public_identity"]["job_id"] == authoritative_job_id
        other_operation_row = daemon.store.db.execute(
            "SELECT operation_id FROM operations WHERE operation_id<>? LIMIT 1",
            (operation_row["operation_id"],),
        ).fetchone()
        assert other_operation_row is not None
        other_operation_id = other_operation_row["operation_id"]
        other_job_row = daemon.store.operation_job(other_operation_id)
        assert other_job_row is not None and other_job_row["job_id"] != authoritative_job_id
        bad_job = copy.deepcopy(loaded_response)
        bad_job["execution"]["job_id"] = other_job_row["job_id"]
        with pytest.raises(CaptureError, match="durable Job row"):
            verify_public_model_load(daemon, bad_job, **load_identity)

        bad_operation = copy.deepcopy(loaded_response)
        bad_operation["execution"]["operation_id"] = other_operation_id
        with pytest.raises(CaptureError, match="exact successful terminal"):
            verify_public_model_load(daemon, bad_operation, **load_identity)

        for location in ("response", "response.data", "response.execution"):
            foreign_project = copy.deepcopy(loaded_response)
            if location == "response":
                foreign_project["project_id"] = "foreign-project"
            elif location == "response.data":
                foreign_project.setdefault("data", {})["project_id"] = "foreign-project"
            else:
                foreign_project["execution"]["project_id"] = "foreign-project"
            with pytest.raises(CaptureError, match="different project id"):
                verify_public_model_load(daemon, foreign_project, **load_identity)

        forged_copy = copy.deepcopy(loaded_response)
        forged_copy["caller_metadata"] = {
            "operation_id": loaded_response["execution"]["operation_id"],
            "job_id": "caller-supplied-job-copy",
            "project_id": adapter.project_id,
        }
        with pytest.raises(CaptureError, match="immutable OperationStore or Job result"):
            verify_public_model_load(daemon, forged_copy, **load_identity)

        with pytest.raises(CaptureError, match="saved MPH bytes"):
            verify_public_model_load(
                daemon, loaded_response,
                **{**load_identity, "expected_file_sha256": "0" * 64})
        other_saved_path = adapter.workspace / "native/foreign-saved-model.mph"
        other_saved_path.write_bytes(saved_path.read_bytes())
        with pytest.raises(CaptureError, match="operation path"):
            verify_public_model_load(
                daemon, loaded_response,
                **{**load_identity, "requested_path": other_saved_path})

        adapter.worker_sessions = 2
        worker2_captures = {}
        for study_tag, times in source_specs:
            daemon.backend.worker.times = list(times)
            slot = runner.SolveSlot("staged_baseline", study_tag, "synthetic Worker2 staged reopen")
            state_key = f"{slot.case_id}:{slot.study_tag}:worker2"
            loaded_binding, _setup, _contract, study_run = _prepare_production_v2_slot(
                adapter, loaded_binding, slot, run_study=False,
                state_key=state_key, label=f"load-test-worker2-{study_tag}")
            assert study_run is None
            output_dir = adapter.evidence / f"load-test-worker2-{study_tag}"
            output_dir.mkdir()
            loaded_binding, reopened_report = adapter._capture_v2_pair(
                slot, loaded_binding, output_dir, adapter.workspace / f"native/load-test-worker2-{study_tag}",
                f"load-test-worker2-{study_tag}", "sol-v2", timeout_s=10.0,
                reopened_origin={**source_captures[f"staged_baseline:{study_tag}"],
                                 "staged_source_captures": source_captures,
                                 "worker2_prior_captures": dict(worker2_captures)},
                reopened_model_load=load_receipt)
            worker2_captures[f"staged_baseline:{study_tag}"] = {
                "cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED",
                "model_binding": loaded_binding.as_record(),
                "v2_capture": reopened_report,
            }
        reopened_capture = worker2_captures["staged_baseline:stdCool"]
        reopened = reopened_capture["v2_capture"]
        frames, lineage = adapter._authenticated_v2_capture_frames(
            reopened_capture, expected_case="staged_baseline", expected_study="stdCool")
        assert len(frames) == 2
        assert lineage["source_identity_authenticated"] is True
        assert public_load_ref["public_identity"]["project_id"] == adapter.project_id
        assert "V3_FULL_XMESH_CAPTURE_CHAIN_REAUTHENTICATED" == lineage["status"]
        assert lineage["full_xmesh_internal_dof_capture"].startswith("COMPLETE_MAPPING_CAPTURED")
        assert worker.study_runs == 3, "Worker2 must not add a Study.run across its three reopened stages"

        tampered = copy.deepcopy(reopened_capture)
        tampered["v2_capture"]["model_load"]["saved_model"]["producer_study_run"]["public_identity"]["job_id"] = "foreign-job"
        with pytest.raises(runner.CampaignError):
            adapter._authenticated_v2_capture_frames(
                tampered, expected_case="staged_baseline", expected_study="stdCool")

        bad_saved_hash = copy.deepcopy(reopened_capture)
        bad_saved_hash["v2_capture"]["model_load"]["saved_model"]["sha256"] = "0" * 64
        with pytest.raises(runner.CampaignError):
            adapter._authenticated_v2_capture_frames(
                bad_saved_hash, expected_case="staged_baseline", expected_study="stdCool")

        bad_worker_load = copy.deepcopy(reopened_capture)
        bad_worker_load["v2_capture"]["model_load"]["current_worker_model_load"][
            "public_identity"]["job_id"] = other_job_row["job_id"]
        with pytest.raises(runner.CampaignError):
            adapter._authenticated_v2_capture_frames(
                bad_worker_load, expected_case="staged_baseline", expected_study="stdCool")

        bad_load_response_hash = copy.deepcopy(reopened_capture)
        bad_load_response_hash["v2_capture"]["model_load"]["current_worker_model_load"][
            "response_evidence"]["sha256"] = "0" * 64
        with pytest.raises(runner.CampaignError, match="response evidence"):
            adapter._authenticated_v2_capture_frames(
                bad_load_response_hash, expected_case="staged_baseline", expected_study="stdCool")
    finally:
        daemon.close()


def test_production_worker2_reopens_full_three_study_chain_from_terminal_saved_model(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 120.0])
    try:
        stage_specs = (
            ("stdUV", [0.0, 120.0]),
            ("stdBake", [120.0, 960.0]),
            ("stdCool", [960.0, 1500.0]),
        )
        staged_captures = {}
        terminal_saved_path = adapter.workspace / "native/staged-baseline-terminal.mph"
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.open("x", encoding="utf-8").close()
        worker.synthetic_ledger_prefix_count = 2
        for study_tag, times in stage_specs:
            daemon.backend.worker.times = list(times)
            slot = next(row for row in runner.SOLVE_PLAN
                        if row.case_id == "staged_baseline" and row.study_tag == study_tag)
            binding, _setup_ref, _contract, study_ref = _prepare_production_v2_slot(
                adapter, binding, slot, run_study=True, label=f"source-{study_tag}",
                ledger_path=ledger_path,
                save_after_success_path=terminal_saved_path if study_tag == "stdCool" else None)
            if study_tag == "stdCool":
                assert study_ref is not None
                assert study_ref["readback_data"]["immediate_save_path"] == str(terminal_saved_path)
                assert study_ref["artifact"]["sha256"] == runner.sha256(terminal_saved_path)
                assert adapter.staged_baseline_saved_model["producer_study_run"] == study_ref
            output_dir = adapter.evidence / f"production-source-{study_tag}"
            source_capture = adapter._capture_native_files(
                slot, binding, output_dir, timeout_s=10.0)
            binding = runner.ManagedModelBinding(
                adapter.project_id, binding.session_id, binding.model_ref,
                int(source_capture["model_binding"]["revision"]))
            staged_captures[f"staged_baseline:{study_tag}"] = source_capture

        staged_frames, staged_lineages = adapter._authenticated_staged_v2_schedule(staged_captures)
        assert [row["study_tag"] for row in staged_lineages] == ["stdUV", "stdBake", "stdCool"]
        assert worker.study_runs == 3
        ledger_rows = runner.read_solve_ledger(ledger_path)
        assert len(ledger_rows) == 5
        assert [row["at_utc"] for row in ledger_rows[:2]] == [
            "synthetic-prior-submission-1", "synthetic-prior-submission-2"]
        assert [(row["case_id"], row["study_tag"]) for row in ledger_rows[2:]] == [
            ("staged_baseline", "stdUV"), ("staged_baseline", "stdBake"),
            ("staged_baseline", "stdCool")]

        # Reopen the same persistent project/OperationStore through a new
        # ControlDaemon and a distinct synthetic Worker identity.
        daemon.close()
        worker1 = worker
        worker = _AuthenticatedLinkWorker([])
        projects = adapter.workspace.parent
        service2 = ExecutionService(
            SessionLedger("session-link", "server-link-test-worker2",
                          permissions={"inspect", "project_write", "compute", "trusted_code"}),
            _RouteSnapshot("server-link-test-worker2"), project_root=projects)

        def model_load_worker2(arguments):
            from comsol_mcp import _server
            model_tag = "loaded-" + Path(arguments["path"]).stem
            _server._current_model = _LoadedModelTag(model_tag)
            return {"success": True, "data": {"model_tag": model_tag,
                                                 "requested_path": arguments["path"]}}

        daemon = ControlDaemon(tmp_path / "control", service=service2,
                               registry={"model_load": model_load_worker2}, worker=worker,
                               project_root=projects)
        daemon.backend.worker_identity = {
            "runtime_id": "capture-link-test-runtime-worker2",
            "worker_instance_id": "capture-link-test-worker2",
            "connection_epoch": 2,
            "server_instance_id": "server-link-test-worker2",
        }
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
        adapter.daemon = daemon

        load_response = daemon.dispatch({
            "operation": "model_load", "arguments": {"path": str(terminal_saved_path)},
            "execution": {"project_id": adapter.project_id, "session_id": "session-link",
                          "request_id": "worker2-terminal-load-request",
                          "idempotency_key": "worker2-terminal-load-key"},
        })
        assert load_response["success"] is True, load_response
        loaded = runner.ManagedModelBinding.from_load_response(
            load_response, project_id=adapter.project_id)
        load_ref = adapter._record_public_model_load(
            name="staged_baseline_worker2", path=terminal_saved_path,
            binding=loaded, response=load_response)
        load_receipt = {
            "model_name": "staged_baseline", "input_path": str(terminal_saved_path),
            "input_sha256": runner.sha256(terminal_saved_path), "binding": loaded.as_record(),
            "public_identity": load_ref["public_identity"],
            "response_evidence": load_ref["response_evidence"],
            "public_load_reference": load_ref,
            "persisted_project_binding": load_response.get("data", {}).get("persisted_project_binding"),
        }

        reopened_captures = {}
        adapter.worker_sessions = 2
        for study_tag, times in stage_specs:
            daemon.backend.worker.times = list(times)
            slot = runner.SolveSlot("staged_baseline", study_tag, "production three-stage Worker2 fixture")
            worker2_state = f"{slot.case_id}:{slot.study_tag}:worker2"
            loaded, _setup_ref, _contract, study_ref = _prepare_production_v2_slot(
                adapter, loaded, slot, run_study=False,
                state_key=worker2_state, label=f"worker2-{study_tag}")
            assert study_ref is None
            output_dir = adapter.evidence / f"production-worker2-{study_tag}"
            if study_tag == "stdCool":
                v2_action_count = sum(action in {"solution_snapshot_v3", "history_capture_v2"}
                                      for action in worker.actions)
                with pytest.raises(runner.CampaignError,
                                   match="must retain every ordered prior stage exactly once"):
                    adapter._capture_v2_pair(
                        slot, loaded, adapter.evidence / "production-worker2-skipped-stage",
                        adapter.workspace / "native/production-worker2-skipped-stage",
                        "production-worker2-skipped-stage", "sol-v2", timeout_s=10.0,
                        reopened_origin={**staged_captures[f"staged_baseline:{study_tag}"],
                                        "staged_source_captures": staged_captures,
                                        "worker2_prior_captures": {
                                            "staged_baseline:stdUV": reopened_captures[
                                                "staged_baseline:stdUV"]}},
                        reopened_model_load=load_receipt)
                assert sum(action in {"solution_snapshot_v3", "history_capture_v2"}
                           for action in worker.actions) == v2_action_count
                assert worker.study_runs == 0
            reopened_capture = adapter._capture_native_files(
                slot, loaded, output_dir, timeout_s=10.0,
                reopened_origin={**staged_captures[f"staged_baseline:{study_tag}"],
                                 "staged_source_captures": staged_captures,
                                 "worker2_prior_captures": dict(reopened_captures)},
                reopened_model_load=load_receipt)
            loaded = runner.ManagedModelBinding(
                adapter.project_id, loaded.session_id, loaded.model_ref,
                int(reopened_capture["model_binding"]["revision"]))
            frames, lineage = adapter._authenticated_v2_capture_frames(
                reopened_capture, expected_case="staged_baseline", expected_study=study_tag)
            assert len(frames) == len(times)
            assert lineage["source_identity_authenticated"] is True
            assert reopened_capture["v2_capture"]["study_run"] is None
            reopened_captures[f"staged_baseline:{study_tag}"] = reopened_capture

        reopened_frames, reopened_lineages = adapter._authenticated_staged_v2_schedule(reopened_captures)
        comparison = adapter._compare_authenticated_v2_frames(
            staged_frames, reopened_frames, staged_lineages, reopened_lineages,
            label="production Worker1→Worker2 full stdUV/stdBake/stdCool schedule")
        assert comparison["status"] == (
            "V2_AUTHENTICATED_VISIBLE_HISTORY_GATES_MATCH_FULL_XMESH_DIAGNOSTIC_ONLY"
            "_TOLERANCE_NOT_FROZEN_BRANCH_STATE_UNVERIFIED")
        assert worker1.study_runs == 3
        assert worker.study_runs == 0, "fresh Worker 2 must load all three stored stages without Study.run"

        missing_stage = copy.deepcopy(reopened_captures)
        del missing_stage["staged_baseline:stdBake"]
        with pytest.raises(runner.CampaignError, match="exactly stdUV, stdBake, and stdCool"):
            adapter._authenticated_staged_v2_schedule(missing_stage)

        reordered = copy.deepcopy(reopened_captures)
        reordered["staged_baseline:stdUV"], reordered["staged_baseline:stdBake"] = (
            reordered["staged_baseline:stdBake"], reordered["staged_baseline:stdUV"])
        with pytest.raises(runner.CampaignError, match="case/study identity"):
            adapter._authenticated_staged_v2_schedule(reordered)

        foreign_case = copy.deepcopy(reopened_captures)
        foreign_case["staged_baseline:stdBake"]["v2_capture"]["case_id"] = "foreign_case"
        with pytest.raises(runner.CampaignError, match="case/study identity"):
            adapter._authenticated_staged_v2_schedule(foreign_case)

        foreign_study = copy.deepcopy(reopened_captures)
        foreign_study["staged_baseline:stdBake"]["v2_capture"]["study_tag"] = "stdCool"
        with pytest.raises(runner.CampaignError, match="case/study identity"):
            adapter._authenticated_staged_v2_schedule(foreign_study)

        foreign_model_bound = daemon.backend.service.bind_model("foreign-staged-model")
        foreign_model_ref = foreign_model_bound["execution"]["model_ref"]
        daemon.backend._bind_model_project(foreign_model_ref, adapter.project_id)
        foreign_revision_key = daemon.backend._model_project_key(foreign_model_ref)
        daemon.store.put_metadata("revisions", foreign_revision_key, {
            "model_ref": foreign_model_ref, "project_id": adapter.project_id,
            "attribution": "PROJECT_BOUND", "revision": 0, "dirty": False,
            "fingerprint": "stable-link-test", "active_operation_id": None,
        })
        foreign_binding = runner.ManagedModelBinding(
            adapter.project_id, "session-link", foreign_model_ref, 0)
        foreign_slot = runner.SolveSlot(
            "staged_baseline", "stdBake", "foreign ModelRef negative control")
        foreign_binding, _setup, _contract, _foreign_study = _prepare_production_v2_slot(
            adapter, foreign_binding, foreign_slot, run_study=True, label="foreign-model")
        foreign_output = adapter.evidence / "foreign-model-source-capture"
        foreign_output.mkdir()
        foreign_binding, foreign_report = adapter._capture_v2_pair(
            foreign_slot, foreign_binding, foreign_output,
            adapter.workspace / "native/foreign-model", "foreign-model", "sol-v2", timeout_s=10.0)
        foreign_capture = {
            "cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED",
            "model_binding": foreign_binding.as_record(), "v2_capture": foreign_report,
        }
        assert foreign_model_ref != staged_captures["staged_baseline:stdBake"]["v2_capture"][
            "setup_readback"]["binding_before"]["model_ref"]
        foreign_chain = copy.deepcopy(staged_captures)
        foreign_chain["staged_baseline:stdBake"] = foreign_capture
        with pytest.raises(runner.CampaignError, match="revision chain breaks"):
            adapter._authenticated_staged_v2_schedule(foreign_chain)

        wrong_producer = copy.deepcopy(reopened_captures["staged_baseline:stdUV"])
        wrong_producer["v2_capture"]["model_load"]["saved_model"]["producer_study_run"] = \
            staged_captures["staged_baseline:stdUV"]["v2_capture"]["study_run"]
        with pytest.raises(runner.CampaignError, match="terminal staged source chain"):
            adapter._authenticated_v2_capture_frames(
                wrong_producer, expected_case="staged_baseline", expected_study="stdUV")
    finally:
        daemon.close()


def test_replay_saved_producer_root_reproducer_rejects_manual_post_run_attribution(
        tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 120.0])
    try:
        worker.synthetic_ledger_prefix_count = 2
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        stage_specs = (("stdUV", [0.0, 120.0]),
                       ("stdBake", [120.0, 960.0]),
                       ("stdCool", [960.0, 1500.0]))
        for study_tag, times in stage_specs:
            daemon.backend.worker.times = list(times)
            slot = next(row for row in runner.SOLVE_PLAN
                        if row.case_id == "staged_baseline" and row.study_tag == study_tag)
            binding, _setup, _contract, producer = _prepare_production_v2_slot(
                adapter, binding, slot, run_study=True, label=f"root-reproducer-{study_tag}",
                ledger_path=ledger_path)
            if study_tag == "stdCool":
                assert producer["readback_data"]["immediate_save_path"] is None
                assert producer["artifact"] is None
                # Reproduce the old test's manual post-run attribution exactly:
                # bytes and summary do not come from Study.run or its response.
                saved_path = adapter.workspace / "native/staged-baseline-terminal.mph"
                saved_path.write_bytes(b"unrelated synthetic model written after Study.run")
                adapter.staged_baseline_saved_model = {
                    "path": str(saved_path), "size_bytes": saved_path.stat().st_size,
                    "sha256": runner.sha256(saved_path), "case_id": "staged_baseline",
                    "study_tag": "stdCool", "producer_study_run": producer,
                    "model_binding_after_solve": binding.as_record(), "native_acceptance": "NOT_RUN",
                }
                before_captures = sum(
                    action in {"solution_snapshot_v3", "history_capture_v2"}
                    for action in worker.actions)
                output_dir = adapter.evidence / "manual-post-run-attribution"
                output_dir.mkdir()
                with pytest.raises(runner.CampaignError,
                                   match="no authenticated immediate-save byte receipt"):
                    adapter._capture_v2_pair(
                        slot, binding, output_dir, adapter.workspace / "native/manual-attributed",
                        "manual-attributed", "sol-v2", timeout_s=10.0)
                after_captures = sum(
                    action in {"solution_snapshot_v3", "history_capture_v2"}
                    for action in worker.actions)
                assert after_captures == before_captures
            else:
                output_dir = adapter.evidence / f"root-reproducer-{study_tag}"
                output_dir.mkdir()
                binding, _report = adapter._capture_v2_pair(
                    slot, binding, output_dir, adapter.workspace / f"native/root-reproducer-{study_tag}",
                    f"root-reproducer-{study_tag}", "sol-v2", timeout_s=10.0)
            adapter.models[slot.case_id] = binding
        assert worker.study_runs == 3
    finally:
        daemon.close()


def test_staged_terminal_producer_requires_actual_save_request_and_std_cool_identity(tmp_path, monkeypatch):
    for mode in ("no-save", "foreign-study-with-save"):
        case_tmp = tmp_path / mode
        case_tmp.mkdir()
        daemon, worker, adapter, binding = _real_daemon_adapter(case_tmp, monkeypatch, [0.0, 120.0])
        try:
            if mode == "no-save":
                study_tag = "stdCool"
                worker.synthetic_ledger_prefix_count = 4
                save_path = None
            else:
                study_tag = "stdUV"
                worker.synthetic_ledger_prefix_count = 2
                save_path = adapter.workspace / "native/foreign-stage-save.mph"
            slot = runner.SolveSlot("staged_baseline", study_tag, "save producer identity negative")
            binding, _setup, _contract, study_ref = _prepare_production_v2_slot(
                adapter, binding, slot, run_study=False, label=f"{mode}-setup")
            assert study_ref is None
            ledger_path = adapter.workspace / "native/study_run_events.jsonl"
            adapter.run_study(slot, ledger_path=ledger_path,
                              save_after_success_path=save_path, timeout_s=10.0)
            producer = adapter.slot_study_run_actions[f"{slot.case_id}:{slot.study_tag}"]
            if mode == "no-save":
                assert producer["artifact"] is None
                with pytest.raises(runner.CampaignError, match="no authenticated immediate-save byte receipt"):
                    adapter._validate_staged_baseline_save(producer)
            else:
                assert producer["artifact"]["path"] == str(save_path)
                with pytest.raises(runner.CampaignError, match="not the exact terminal staged-baseline stdCool"):
                    adapter._validate_staged_baseline_save(producer)
            assert worker.study_runs == 1
        finally:
            daemon.close()


@pytest.mark.parametrize("failure", ["missing_receipt", "wrong_path"])
def test_study_run_save_receipt_failure_is_rejected_without_retry(tmp_path, monkeypatch, failure):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [960.0, 1500.0])
    try:
        worker.synthetic_ledger_prefix_count = 4
        slot = runner.SolveSlot("staged_baseline", "stdCool", "save receipt failure negative")
        binding, _setup, _contract, study_ref = _prepare_production_v2_slot(
            adapter, binding, slot, run_study=False, label=f"bad-save-{failure}-setup")
        assert study_ref is None
        save_path = adapter.workspace / "native/staged-baseline-terminal.mph"
        if failure == "missing_receipt":
            worker.omit_save_receipt = True
        else:
            worker.save_path_override = adapter.workspace / "native/other-output.mph"
        with pytest.raises(runner.CampaignError, match="exact requested immediate-save byte receipt"):
            adapter.run_study(slot, ledger_path=adapter.workspace / "native/study_run_events.jsonl",
                              save_after_success_path=save_path, timeout_s=10.0)
        assert worker.study_runs == 1, "failed save receipt must not trigger a second Study.run"
        assert adapter.staged_baseline_saved_model is None
    finally:
        daemon.close()


def test_changed_saved_bytes_summary_and_new_model_load_cannot_replace_study_run_receipt(
        tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [960.0, 1500.0])
    try:
        worker.synthetic_ledger_prefix_count = 4
        slot = next(row for row in runner.SOLVE_PLAN
                    if row.case_id == "staged_baseline" and row.study_tag == "stdCool")
        binding, _setup, _contract, study_ref = _prepare_production_v2_slot(
            adapter, binding, slot, run_study=False, label="saved-producer-replacement-setup")
        assert study_ref is None
        saved_path = adapter.workspace / "native/staged-baseline-terminal.mph"
        adapter.run_study(slot, ledger_path=adapter.workspace / "native/study_run_events.jsonl",
                          save_after_success_path=saved_path, timeout_s=10.0)
        producer = adapter.slot_study_run_actions["staged_baseline:stdCool"]
        assert isinstance(adapter.staged_baseline_saved_model, dict)

        # Change the bytes after the authenticated Study.run, then update every
        # mutable summary field and perform a fresh, valid public model_load.
        replacement_bytes = b"different MPH bytes written after the producer Study.run"
        saved_path.write_bytes(replacement_bytes)
        replacement_hash = runner.sha256(saved_path)
        summary = adapter.staged_baseline_saved_model
        summary["size_bytes"] = saved_path.stat().st_size
        summary["sha256"] = replacement_hash
        forged_save_receipt = copy.deepcopy(summary["save_receipt"])
        forged_save_receipt["artifact"] = {
            "path": str(saved_path), "size_bytes": saved_path.stat().st_size,
            "sha256": replacement_hash,
        }
        forged_save_receipt["artifact_receipt"] = {
            "status": "STUDY_RUN_MPH_SAVED_AND_HASHED", "path": str(saved_path),
            "size_bytes": saved_path.stat().st_size, "sha256": replacement_hash,
        }
        summary["save_receipt"] = forged_save_receipt

        loaded_response = daemon.dispatch({
            "operation": "model_load", "arguments": {"path": str(saved_path)},
            "execution": {"project_id": adapter.project_id, "session_id": "session-link",
                          "request_id": "replacement-bytes-load-request",
                          "idempotency_key": "replacement-bytes-load-key"},
        })
        assert loaded_response["success"] is True, loaded_response
        loaded_binding = runner.ManagedModelBinding.from_load_response(
            loaded_response, project_id=adapter.project_id)
        public_load_ref = adapter._record_public_model_load(
            name="replacement_bytes", path=saved_path, binding=loaded_binding,
            response=loaded_response)
        new_load_receipt = {
            "input_path": str(saved_path), "input_sha256": replacement_hash,
            "binding": loaded_binding.as_record(),
            "public_identity": public_load_ref["public_identity"],
            "response_evidence": public_load_ref["response_evidence"],
        }
        assert adapter._reauthenticate_public_model_load(new_load_receipt)["sha256"] == replacement_hash
        with pytest.raises(runner.CampaignError, match="stored study_run response"):
            adapter._validate_staged_baseline_save(producer, saved_summary=summary)
        assert producer["artifact"]["sha256"] != replacement_hash
        assert worker.study_runs == 1
    finally:
        daemon.close()


def test_run_study_refuses_nonempty_preexisting_ledger_before_dispatch(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 1.0])
    try:
        slot = runner.SolveSlot("staged_baseline", "stdUV", "preexisting ledger refusal")
        binding, _setup, _contract, _study = _prepare_production_v2_slot(
            adapter, binding, slot, run_study=False, label="nonempty-ledger-setup")
        ledger_path = adapter.workspace / "native/study_run_events.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        first_slot = runner.SOLVE_PLAN[0]
        ledger_path.write_text(json.dumps({
            "event": "study_run_submitted", "submission_index": 1,
            "case_id": first_slot.case_id, "study_tag": first_slot.study_tag,
            "at_utc": "synthetic-existing-attempt",
        }) + "\n", encoding="utf-8")
        with pytest.raises(runner.CampaignError, match="must begin with an empty native solve ledger"):
            adapter.run_study(slot, ledger_path=ledger_path,
                              save_after_success_path=None, timeout_s=10.0)
        assert worker.study_runs == 0
        assert runner.read_solve_ledger(ledger_path)[0]["at_utc"] == "synthetic-existing-attempt"
    finally:
        daemon.close()


def test_declared_v2_loaded_model_contract_failure_stops_before_study_run(tmp_path, monkeypatch):
    daemon, worker, adapter, binding = _real_daemon_adapter(tmp_path, monkeypatch, [0.0, 1.0])
    try:
        slot = runner.SolveSlot("staged_baseline", "stdUV", "invalid declared-v2 fixture")
        state_key = f"{slot.case_id}:{slot.study_tag}"
        binding_before = binding
        binding, setup_response, _ = adapter._fixture_action(
            binding, "readback", {}, timeout_s=10.0, source_fixture=adapter.fixture)
        setup_ref = adapter._record_public_java_action(
            action="readback", response=setup_response, binding_before=binding_before,
            binding_after=binding, source_fixture=adapter.fixture,
            response_dir=adapter.evidence / "responses", response_label="v2-invalid-setup")
        adapter.slot_native_setup_readbacks[state_key] = setup_ref
        broken = _contract_readback()
        del broken["relative_exposure_dose"]["field"]
        worker.contract_readback = broken
        with pytest.raises(runner.CampaignError, match="relative-dose"):
            adapter._ensure_v2_contract_readback(slot, binding, timeout_s=10.0, state_key=state_key)
        assert worker.study_runs == 0
    finally:
        daemon.close()
