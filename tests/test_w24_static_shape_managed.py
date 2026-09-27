from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from tools.run_native_w24_cure_science import CampaignError, ManagedModelBinding
from tools.run_native_w24_static_shape_setup import (
    StaticShapeManagedRunner,
    prepare_project_sources,
)


PROJECT_ID = "project-w24-shape-test"
SESSION_ID = "session-w24-shape-test"
SERVER_INSTANCE = "server-instance-shape-test"


def _model_ref(tag: str) -> dict[str, Any]:
    return {"model_tag": tag, "session_id": SESSION_ID,
            "server_instance_id": SERVER_INSTANCE, "generation": 1}


def _readback(tag: str, case_id: str) -> dict[str, Any]:
    wetting = ({"sel_wet_flat_base": [10]} if case_id == "flat" else {
        "sel_wet_mesa_top": [10], "sel_wet_mesa_side": [11], "sel_wet_lower_base": [12]})
    return {
        "status": "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK",
        "native_acceptance": "NOT_RUN",
        "model_tag": tag,
        "case_id": case_id,
        "geometry_dimension": 2,
        "geometry_axisymmetric": True,
        "geometry_domain_count": 2,
        "geometry_boundary_count": 9,
        "glue_domain_ids": [1],
        "gas_domain_ids": [2],
        "multiphase_domain_ids": [1, 2],
        "substrate_wetting_selections": wetting,
        "wetted_wall_features": [{"feature_tag": f"ww{i}", "surface_selection": name,
                                   "boundary_ids": ids,
                                   "contact_angle_expression": "thetaSubstrate"}
                                  for i, (name, ids) in enumerate(wetting.items())],
        "glue_density_expression": "rhoGlue",
        "gas_density_expression": "rhoGas",
        "glue_viscosity_expression": "muGlue",
        "gas_viscosity_expression": "muGas",
        "phase_field_type": "PhaseFieldInFluids",
        "phase_field_model_type": "PhaseFieldModel",
        "phase_field_epsilon_expression": "epsPF",
        "phase_field_chi_expression": "chiPF",
        "phase1_initial_selection": [1],
        "phase1_initial_value": "Fluid1phipf",
        "phase2_initial_selection": [2],
        "phase2_initial_value": "Fluid2phipf",
        "flow_type": "LaminarFlow",
        "flow_compressibility": "incompressible",
        "gravity_enabled": "off",
        "pressure_reference_type": "PressurePointConstraint",
        "pressure_reference_value": "0[Pa]",
        "pressure_reference_point_ids": [8],
        "multiphase_volume_fraction_definition": "pf",
        "phase1_material_link": "matGlue",
        "phase2_material_link": "matGas",
        "surface_tension_enabled": "on",
        "surface_tension_mode": "userdef",
        "surface_tension_expression": "sigma0",
        "phase_initialization_step_type": "PhaseInitialization",
        "transient_step_type": "Transient",
        "time_list_expression": "range(0[s],tCapillary/2,20*tCapillary)",
        "solver_sequence_tags": ["sol1"],
        "bdf_output_time_policy": "strict",
        "output_mode": "tsteps",
        "stored_time_steps_policy": 1,
        "maximum_step_s": 1.0 / 600.0,
        "axis_boundary_ids": [1],
        "parameter_values_and_units": {
            "rhoGlue": {"value_si": 1200.0, "unit": "kg/m^3"},
            "muGlue": {"value_si": 1.0, "unit": "Pa*s"},
            "rhoGas": {"value_si": 1.2, "unit": "kg/m^3"},
            "muGas": {"value_si": 0.018, "unit": "Pa*s"},
            "sigma0": {"value_si": 0.03, "unit": "N/m"},
            "epsPF": {"value_si": 8e-6, "unit": "m"},
            "Rdrop": {"value_si": 500e-6, "unit": "m"},
        },
        "mesh_tags": ["mesh1"],
        "solution_state_readback": {
            "status": "NO_STORED_SOLUTION_DATA",
            "readback_method": "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]",
            "solver_sequences": [{"solver_sequence_tag": "sol1", "degrees_of_freedom": 0,
                                  "stored_solution_count": 0}],
        },
        "study_run_calls_this_action": 0,
    }


class FakeSetupRunner:
    @staticmethod
    def _worker_request_terminal(response: Mapping[str, Any]) -> bool:
        worker = response.get("data", {}).get("worker")
        return isinstance(worker, Mapping) and worker.get("status") in {
            "SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"}

    @staticmethod
    def _java_action_readback(response: dict[str, Any], label: str) -> dict[str, Any]:
        assert response.get("success") is True, label
        data = response["data"]
        assert data["worker"]["ok"] is True
        assert data["worker"]["status"] == "SUCCEEDED"
        assert data["readback"]["executed"] is True
        nested = data["readback"]["readback"]
        assert data["worker"]["result"]["readback"] == nested
        return nested


class FakeBackend:
    def __init__(self):
        self.bindings: dict[str, dict[str, Any]] = {}
        self.worker_identity = {"server_instance_id": SERVER_INSTANCE}

    def bind(self, model_ref: Mapping[str, Any], project_id: str = PROJECT_ID) -> None:
        self.bindings[json.dumps(dict(model_ref), sort_keys=True)] = {
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        }

    def model_project_binding(self, model_ref: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.bindings.get(json.dumps(dict(model_ref), sort_keys=True))


class FakeSessionRegistry:
    def __init__(self, project_id: str, session_id: str, backend: FakeBackend):
        self._context = SimpleNamespace(project_id=project_id, session_id=session_id,
                                        backend=backend)

    def get(self, project_id: str, session_id: str):
        if (project_id, session_id) != (self._context.project_id, self._context.session_id):
            raise LookupError("session not registered")
        return self._context


class FakePage(list):
    def __init__(self, values=(), total=0):
        super().__init__(values)
        self.total = total


class FakeStore:
    def __init__(self):
        self.jobs: list[dict[str, Any]] = []

    def list_jobs(self, *, offset: int, limit: int, project_id: str):
        rows = [row for row in self.jobs if row.get("project_id") == project_id]
        return FakePage(rows[offset:offset + limit], len(rows))

    def events(self, job_id: str, *, offset: int, limit: int):
        return FakePage([], 0)


class FakeDaemon:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.backend = FakeBackend()
        self.session_registry = FakeSessionRegistry(PROJECT_ID, SESSION_ID, self.backend)
        self.store = FakeStore()
        self.parent = _model_ref("parent-model")
        self.backend.bind(self.parent)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.build_outcome = "SUCCEEDED"
        self.mutate_reopen = False
        self.bad_save_hash = False
        self.solution_state_size = (0, 0)
        self._shape_tag = "shape-native-tag"
        self._loaded_tag = "shape-reopened-tag"
        self._readback_count = 0

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        arguments = request.get("arguments", {})
        execution = request.get("execution", {})
        self.calls.append((operation, request))
        if operation == "model.adopt":
            tag = arguments["server_model_tag"]
            ref = _model_ref(tag)
            self.backend.bind(ref)
            return {"success": True, "execution": {
                "project_id": execution["project_id"], "session_id": execution["session_id"],
                "model_ref": ref, "revision": 0, "request_id": execution["request_id"]}}
        if operation == "model.inspect":
            ref = dict(execution["model_ref"])
            response = self._worker_response(True, "SUCCEEDED", ref, execution, {
                "model_identity": ref, "structure": {"studies": ["stdShape"]}})
            return response
        if operation == "model_load":
            if Path(arguments["path"]).parent != self.workspace / "outputs":
                return {"success": False}
            ref = _model_ref(self._loaded_tag)
            self.backend.bind(ref)
            return self._worker_response(True, "SUCCEEDED", ref, execution,
                                         {"model_tag": self._loaded_tag})
        if operation != "operation_call":
            raise AssertionError(f"unexpected production operation: {operation}")
        assert arguments["operation_id"] == "code.execute_java"
        java = arguments["arguments"]
        entrypoint = java["entrypoint"]
        action_args = java["arguments"]
        ref = dict(execution["model_ref"])
        revision = int(execution["expected_revision"]) + 1
        if entrypoint == "W24StaticShapeFixture#run":
            if self.build_outcome != "SUCCEEDED":
                status = "FAILED" if self.build_outcome == "FAILED" else "UNKNOWN"
                return self._worker_response(False, status, ref, execution, {})
            case_id = action_args["case_id"]
            nested = {"status": "BUILT_NOT_SOLVED", "native_acceptance": "NOT_RUN",
                      "case_id": case_id, "model_tag": self._shape_tag,
                      "study_run_calls": 0, "phase_initialization_executed": False}
            return self._java_response(nested, ref, execution, revision)
        assert entrypoint == "W24StaticShapeReadback#run"
        action = action_args["action"]
        if action == "readback":
            case_id = self._current_case
            self._readback_count += 1
            nested = _readback(self._loaded_tag if ref["model_tag"] == self._loaded_tag else self._shape_tag,
                               case_id)
            nested["solution_state_readback"]["solver_sequences"][0]["degrees_of_freedom"] = self.solution_state_size[0]
            nested["solution_state_readback"]["solver_sequences"][0]["stored_solution_count"] = self.solution_state_size[1]
            if self.mutate_reopen and self._readback_count > 1:
                nested["maximum_step_s"] *= 2
            return self._java_response(nested, ref, execution, revision)
        assert action == "save"
        path = Path(action_args["path"])
        path.write_bytes(b"synthetic nonempty unsolved MPH bytes")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.bad_save_hash:
            digest = "0" * 64
        return self._java_response({"status": "SAVED_UNSOLVED_STATIC_SHAPE_MODEL",
                                    "model_tag": self._shape_tag,
                                    "path": str(path), "size_bytes": path.stat().st_size,
                                    "sha256": digest, "solution_data_present": False,
                                    "solution_state_readback": {
                                        "status": "NO_STORED_SOLUTION_DATA",
                                        "readback_method": "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]",
                                        "solver_sequences": [{"solver_sequence_tag": "sol1",
                                                              "degrees_of_freedom": 0,
                                                              "stored_solution_count": 0}]},
                                    "study_run_calls_this_action": 0}, ref, execution, revision)

    @staticmethod
    def _worker_response(success: bool, status: str, ref: Mapping[str, Any],
                         execution: Mapping[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        data["worker"] = {"ok": success, "status": status,
                           "result": {"model_ref": dict(ref)}}
        return {"success": success, "data": data,
                "execution": {"project_id": execution.get("project_id"),
                              "session_id": ref.get("session_id"),
                              "model_ref": dict(ref), "revision": execution.get("revision", 0),
                              "request_id": execution.get("request_id")}}

    def _java_response(self, nested: dict[str, Any], ref: Mapping[str, Any],
                       execution: Mapping[str, Any], revision: int) -> dict[str, Any]:
        wrapped = {"executed": True, "readback": nested}
        worker = {"ok": True, "status": "SUCCEEDED", "result": {"readback": nested}}
        return {"success": True, "data": {"worker": worker, "readback": wrapped},
                "execution": {"project_id": execution.get("project_id"),
                              "session_id": ref.get("session_id"), "model_ref": dict(ref),
                              "revision": revision, "request_id": execution.get("request_id")}}


def _setup(tmp_path: Path, *, daemon: FakeDaemon | None = None,
           case_id: str = "flat") -> tuple[StaticShapeManagedRunner, FakeDaemon, Path]:
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_manifest = prepare_project_sources(workspace)
    fake = daemon or FakeDaemon(workspace)
    fake._current_case = case_id
    project_create_response = {"success": True, "data": {"project": {
        "project_id": PROJECT_ID, "workspace": str(workspace)}}}
    parent = ManagedModelBinding(PROJECT_ID, SESSION_ID, fake.parent, 0)
    runner = StaticShapeManagedRunner(
        daemon=fake, project_id=PROJECT_ID, project_workspace=workspace,
        project_create_response=project_create_response, parent_binding=parent,
        source_manifest=source_manifest, setup_runner=FakeSetupRunner(), evidence_dir=evidence)
    return runner, fake, workspace


def test_source_copy_is_hash_bound_and_existing_exact_copy_is_reusable(tmp_path):
    workspace = tmp_path / "registered"
    workspace.mkdir()
    first = prepare_project_sources(workspace)
    second = prepare_project_sources(workspace)
    assert first["sources"]["fixture"]["sha256"] == second["sources"]["fixture"]["sha256"]
    assert second["sources"]["fixture"]["copied_this_call"] is False
    assert Path(first["sources"]["readback"]["project_path"]).is_file()


def test_managed_flat_shape_build_save_and_reopen_use_exact_production_bindings(tmp_path):
    runner, daemon, workspace = _setup(tmp_path)

    result = runner.build_save_reopen("flat")

    assert result["status"] == "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE"
    assert result["native_acceptance"] == "NOT_RUN"
    assert result["study_run_submissions"] == []
    assert result["study_run_submission_count"] == 0
    assert result["phase_initialization_executed"] is False
    assert result["readback_comparison"]["matches"] is True
    assert result["parent_model_binding"]["model_tag"] == "parent-model"
    assert result["new_model_binding"]["model_tag"] == "shape-native-tag"
    assert result["reopened_model_binding"]["model_tag"] == "shape-reopened-tag"
    assert Path(result["project_artifact"]["path"]).is_relative_to(workspace)
    operations = [operation for operation, _ in daemon.calls]
    assert operations == ["operation_call", "model.adopt", "model.inspect",
                          "operation_call", "operation_call", "model_load",
                          "model.inspect", "operation_call"]
    assert all("study.run" not in operation for operation in operations)
    save_request = next(request for operation, request in daemon.calls
                        if operation == "operation_call" and
                        request["arguments"]["arguments"]["entrypoint"] == "W24StaticShapeReadback#run" and
                        request["arguments"]["arguments"]["arguments"].get("action") == "save")
    assert save_request["arguments"]["arguments"]["arguments"]["path"].startswith(str(workspace))
    load_request = next(request for operation, request in daemon.calls if operation == "model_load")
    assert load_request["execution"]["session_id"] == SESSION_ID


def test_managed_step_shape_uses_all_three_named_wetting_surfaces(tmp_path):
    runner, _, _ = _setup(tmp_path, case_id="step")
    result = runner.build_save_reopen("step")
    assert set(result["pre_save_configuration_readback"]["substrate_wetting_selections"]) == {
        "sel_wet_mesa_top", "sel_wet_mesa_side", "sel_wet_lower_base"}
    assert len(result["pre_save_configuration_readback"]["wetted_wall_features"]) == 3


def test_project_create_identity_must_match_before_any_worker_dispatch(tmp_path):
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    sources = prepare_project_sources(workspace)
    daemon = FakeDaemon(workspace)
    parent = ManagedModelBinding(PROJECT_ID, SESSION_ID, daemon.parent, 0)
    with pytest.raises(CampaignError, match="project.create response"):
        StaticShapeManagedRunner(
            daemon=daemon, project_id=PROJECT_ID, project_workspace=workspace,
            project_create_response={"success": True, "data": {"project": {
                "project_id": "foreign-project", "workspace": str(workspace)}}},
            parent_binding=parent, source_manifest=sources, setup_runner=FakeSetupRunner(),
            evidence_dir=evidence)
    assert daemon.calls == []


def test_project_source_hash_mismatch_fails_before_native_dispatch(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    manifest = prepare_project_sources(runner.workspace)
    manifest["sources"]["fixture"]["sha256"] = "0" * 64
    evidence = tmp_path / "other-evidence"
    evidence.mkdir()
    with pytest.raises(CampaignError, match="reviewed source hash"):
        StaticShapeManagedRunner(
            daemon=daemon, project_id=PROJECT_ID, project_workspace=runner.workspace,
            project_create_response={"success": True, "data": {"project": {
                "project_id": PROJECT_ID, "workspace": str(runner.workspace)}}},
            parent_binding=runner.parent_binding, source_manifest=manifest,
            setup_runner=FakeSetupRunner(), evidence_dir=evidence)
    assert daemon.calls == []


def test_parent_model_ref_must_belong_to_current_worker_epoch(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.backend.worker_identity = {"server_instance_id": "different-worker-epoch"}
    with pytest.raises(CampaignError, match="active Worker server instance"):
        runner.build_save_reopen("flat")
    assert daemon.calls == []


def test_setup_only_runner_refuses_project_with_existing_study_run_submission(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.store.jobs = [{"job_id": "old-study-run", "project_id": PROJECT_ID,
                          "status": "SUCCEEDED"}]
    daemon.store.events = lambda job_id, *, offset, limit: FakePage([
        {"event": "worker_request", "metadata": {
            "phase": "submitted", "kind": "call",
            "metadata": {"type": "call", "method": "run"}}}
    ][:limit], 1)
    with pytest.raises(CampaignError, match="already contains a study.run"):
        runner.build_save_reopen("flat")
    assert daemon.calls == []


@pytest.mark.parametrize("outcome", ["FAILED", "UNKNOWN"])
def test_non_successful_or_nonterminal_build_stops_without_follow_on_calls(tmp_path, outcome):
    runner, daemon, _ = _setup(tmp_path)
    daemon.build_outcome = outcome
    with pytest.raises(CampaignError):
        runner.build_save_reopen("flat")
    assert [operation for operation, _ in daemon.calls] == ["operation_call"]
    assert not any((tmp_path / "registered-project" / "outputs").glob("*.mph"))


def test_save_hash_mismatch_stops_before_managed_reopen(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.bad_save_hash = True
    with pytest.raises(CampaignError, match="size/hash"):
        runner.build_save_reopen("flat")
    assert "model_load" not in [operation for operation, _ in daemon.calls]


def test_managed_setup_refuses_preexisting_stored_solution_without_clearing_it(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.solution_state_size = (100, 1)

    with pytest.raises(CampaignError, match="solver-sequence readback"):
        runner.build_save_reopen("flat")

    assert [operation for operation, _ in daemon.calls].count("operation_call") == 2
    assert "model_load" not in [operation for operation, _ in daemon.calls]
    assert not list((tmp_path / "registered-project" / "outputs").glob("*.mph"))


@pytest.mark.parametrize("size", [(0, 1), (1, 0), (-1, 0)])
def test_solution_state_readback_rejects_inconsistent_or_negative_sizes(size):
    value = {"status": "NO_STORED_SOLUTION_DATA",
             "readback_method": "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]",
             "solver_sequences": [{"solver_sequence_tag": "sol1",
                                   "degrees_of_freedom": size[0],
                                   "stored_solution_count": size[1]}]}
    with pytest.raises(CampaignError, match="solver-sequence readback"):
        StaticShapeManagedRunner._validate_no_stored_solution_data(value)


def test_reopened_native_configuration_must_match_the_saved_model(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.mutate_reopen = True
    with pytest.raises(CampaignError, match="changed after managed save/reopen"):
        runner.build_save_reopen("flat")
    assert [operation for operation, _ in daemon.calls].count("model_load") == 1


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf"), True])
def test_native_parameter_readback_rejects_nonfinite_and_boolean_values(tmp_path, bad_value):
    readback = _readback("shape-native-tag", "flat")
    readback["parameter_values_and_units"]["rhoGlue"]["value_si"] = bad_value
    with pytest.raises(CampaignError, match="rhoGlue"):
        StaticShapeManagedRunner._validate_shape_readback(readback, "flat")


class _RouteFakeBackend:
    def __init__(self, project_id: str, session_id: str, worker: object,
                 model_ref: Mapping[str, Any], workspace: Path):
        self.name = "registered-session-backend"
        self.project_root = workspace
        self.host_permission_ceiling = {"inspect", "project_write", "compute"}
        self.worker = worker
        self.worker_identity = {"server_instance_id": SERVER_INSTANCE}
        self.service = SimpleNamespace(ledger=SimpleNamespace(
            permissions={"inspect", "project_write", "compute"}, session_id=session_id))
        self._project_id = project_id
        self._session_id = session_id
        self._model_ref = dict(model_ref)
        self.workspace = workspace
        self.calls: list[dict[str, Any]] = []

    def model_project_binding(self, model_ref: Mapping[str, Any]):
        if dict(model_ref) == self._model_ref:
            return {"attribution": "PROJECT_BOUND", "project_id": self._project_id}
        return {"attribution": "UNATTRIBUTED", "project_id": None}

    @contextmanager
    def project_root_scope(self, root):
        assert Path(root).resolve() == self.workspace.resolve()
        yield self.workspace

    def invoke(self, operation, arguments, execution, operation_id, event_callback):
        from comsol_mcp._session_context import current_session_context

        context = current_session_context()
        self.calls.append({"operation": operation, "arguments": dict(arguments),
                           "execution": dict(execution), "context_session": context.session_id,
                           "context_backend": context.backend.name})
        assert operation == "model_load"
        artifact = Path(arguments["path"]).resolve(strict=True)
        assert artifact.is_relative_to(self.workspace.resolve())
        return {"success": True, "data": {"model_tag": self._model_ref["model_tag"],
                                          "worker": {"ok": True, "status": "SUCCEEDED"}},
                "execution": {"project_id": self._project_id,
                              "session_id": self._session_id,
                              "model_ref": dict(self._model_ref), "revision": 0}}


def test_actual_control_daemon_model_load_routes_to_exact_registered_session_backend(tmp_path):
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._session_context import (
        CanonicalSocket, SessionEndpointIdentity, SessionRuntimeConfig, SessionRuntimeContext,
    )

    project_root = tmp_path / "authorized-projects"
    project_root.mkdir()
    worker = object()
    daemon = ControlDaemon(tmp_path / "control", registry={"model_load": lambda _args: None},
                           project_root=project_root)
    try:
        project_response = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "w24-session-route", "workspace": "w24-session-route",
                          "policy": {"permissions": ["inspect", "project_write", "compute"]}},
            "execution": {"request_id": "w24-create-session-route",
                          "idempotency_key": "w24-create-session-route"},
        })
        assert project_response["success"] is True
        project = project_response["data"]["project"]
        workspace = Path(project["workspace"])
        artifact_dir = workspace / "outputs"
        artifact_dir.mkdir()
        artifact = artifact_dir / "existing-unsolved.mph"
        artifact.write_bytes(b"fake artifact for route-only test")

        socket = CanonicalSocket("127.0.0.1", 27824)
        runtime = SessionRuntimeConfig(
            runtime_id="runtime-w24-session-route", comsol_version="6.4.0.293",
            installation_root=tmp_path / "comsol", java_executable=tmp_path / "java",
            classpath=(tmp_path / "client.jar",), preferences_dir=tmp_path / "prefs",
            session_state_root=tmp_path / "session-state")
        parent_ref = _model_ref("parent-model")
        backend = _RouteFakeBackend(project["project_id"], SESSION_ID, worker,
                                    parent_ref, workspace)
        context = SessionRuntimeContext(
            project_id=project["project_id"], session_id=SESSION_ID,
            project_root=workspace, runtime=runtime,
            endpoint=SessionEndpointIdentity("127.0.0.1", 27824, 1, observed_peer=socket),
            backend=backend, worker_instance_id="worker-w24-session-route", worker=worker,
            service=backend.service)
        daemon.session_registry.register(context)

        evidence = tmp_path / "route-evidence"
        evidence.mkdir()
        source_manifest = prepare_project_sources(workspace)
        binding = ManagedModelBinding(project["project_id"], SESSION_ID, parent_ref, 0)
        runner = StaticShapeManagedRunner(
            daemon=daemon, project_id=project["project_id"], project_workspace=workspace,
            project_create_response=project_response, parent_binding=binding,
            source_manifest=source_manifest, setup_runner=FakeSetupRunner(), evidence_dir=evidence)

        response = runner._dispatch("model_load", {"path": str(artifact)},
                                    session_id=SESSION_ID, worker_required=True)
        loaded = ManagedModelBinding.from_load_response(response, project_id=project["project_id"])

        assert loaded.session_id == SESSION_ID
        assert len(backend.calls) == 1
        assert backend.calls[0]["execution"]["session_id"] == SESSION_ID
        assert backend.calls[0]["context_session"] == SESSION_ID
        assert backend.calls[0]["context_backend"] == "registered-session-backend"
    finally:
        daemon.close()
