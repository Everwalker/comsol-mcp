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
from tools.w24_static_shape_sensitivity import sensitivity_configurations


PROJECT_ID = "project-w24-shape-test"
SESSION_ID = "session-w24-shape-test"
SERVER_INSTANCE = "server-instance-shape-test"


def _capture(value: Any) -> dict[str, Any]:
    return {"operation": "synthetic-fixture-only", "status": "READ", "value": value}


def _property_row(name: str, value: str, *, allowed: list[str] | None = None) -> dict[str, Any]:
    return {
        "property_name": name,
        "has_property_capture": _capture(True),
        "listed_in_properties": True,
        "declared_type_capture": _capture("String"),
        "allowed_values_capture": _capture(allowed),
        "value_reader": "getString",
        "value_capture": _capture(value),
        "value_status": "READ_BY_DECLARED_TYPE",
    }


def _property_scope(values: Mapping[str, str]) -> dict[str, Any]:
    return {
        "property_inventory_capture": _capture(list(values)),
        "properties": {name: _property_row(name, value) for name, value in values.items()},
    }


def _feature_node(tag: str, feature_type: str, *, scope: str | None = None,
                  values: Mapping[str, str] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "tag": tag, "path": tag, "resolution_status": "READ", "capture_status": "CAPTURED",
        "type_capture": _capture(feature_type),
        "child_feature_tree": {"capture_status": "CAPTURED", "tags_capture": _capture([]), "nodes": []},
    }
    if scope is not None and values is not None:
        node[scope] = _property_scope(values)
    return node


def _generated_chain() -> dict[str, Any]:
    attached_tag, stored_tag = "solPhaseGraph", "solStoredGraph"
    query_values = {
        "All": [attached_tag, stored_tag], "SolverSequence": [], "CopySolution": [],
        "ParametricStore": [], "Stored": [stored_tag], "Parametric": [], "None": [attached_tag],
    }
    query_types = ["All", "SolverSequence", "CopySolution", "ParametricStore",
                   "Stored", "Parametric", "None"]
    sequence_tags = ["phaseStepNode", "phaseVariablesNode", "stationaryNode", "storeNode",
                     "timeStepNode", "timeVariablesNode", "transientNode"]
    nodes = [
        _feature_node(sequence_tags[0], "StudyStep", scope="study_step_reference_properties",
                     values={"study": "stdShape", "studystep": "phasei"}),
        _feature_node(sequence_tags[1], "Variables", scope="initial_source_properties",
                     values={"control": "phasei", "initmethod": "init", "initsol": "zero",
                             "initsoluse": "current"}),
        _feature_node(sequence_tags[2], "Stationary"),
        _feature_node(sequence_tags[3], "StoreSolution", scope="store_solution_properties",
                     values={"sol": stored_tag}),
        _feature_node(sequence_tags[4], "StudyStep", scope="study_step_reference_properties",
                     values={"study": "stdShape", "studystep": "time"}),
        _feature_node(sequence_tags[5], "Variables", scope="initial_source_properties",
                     values={"control": "time", "initmethod": "sol", "initsol": attached_tag,
                             "initsoluse": stored_tag}),
        _feature_node(sequence_tags[6], "Time"),
    ]
    attached = {
        "requested_tag": attached_tag, "resolution_status": "READ", "capture_complete": True,
        "tag_capture": _capture(attached_tag), "sequence_type_capture": _capture("None"),
        "attached_capture": _capture(True), "study_capture": _capture("stdShape"),
        "feature_tree_capture": {"capture_status": "CAPTURED", "tags_capture": _capture(sequence_tags),
                                 "nodes": nodes},
    }
    stored = {
        "requested_tag": stored_tag, "resolution_status": "READ", "capture_complete": True,
        "tag_capture": _capture(stored_tag), "sequence_type_capture": _capture("Stored"),
        "attached_capture": _capture(False), "study_capture": _capture("stdShape"),
        "feature_tree_capture": {"capture_status": "CAPTURED", "tags_capture": _capture([]), "nodes": []},
    }
    steps = []
    initialization = {}
    for tag, feature_type in (("phasei", "PhaseInitialization"), ("time", "Transient")):
        props = ({"initmethod": "init", "initstudy": "zero", "useinitsol": "off"}
                 if tag == "phasei" else
                 {"initmethod": "sol", "initstudy": "stdShape", "useinitsol": "off"})
        steps.append({"requested_tag": tag, "resolution_status": "READ",
                      "type_capture": _capture(feature_type), "properties": _property_scope(props)})
        initialization[tag] = props
    return {
        "schema": "W24_GENERATED_STUDY_SOLVER_CHAIN_R2", "study_tag": "stdShape",
        "study_step_tags_capture": _capture(["phasei", "time"]),
        "study_step_tags": ["phasei", "time"], "study_steps": steps,
        "study_step_initialization_readback": initialization,
        "sequence_query_types": query_types,
        "sequence_query_rows": [{"query_type": name, "capture": _capture(query_values[name])}
                                for name in query_types],
        "global_solver_tags_capture": _capture([attached_tag, stored_tag]),
        "global_solver_tags": [attached_tag, stored_tag],
        "sequence_tag_union": [attached_tag, stored_tag],
        "sequence_records": [attached, stored],
        "attached_chain_candidate_tags": [attached_tag], "attached_chain_failure_tags": [],
        "selected_attached_sequence_tag": attached_tag, "store_solution_output_tag": stored_tag,
        "semantic_findings": [], "status": "PHASE_INIT_TO_TIME_CHAIN_VALIDATED",
        "native_acceptance": "NOT_RUN", "study_run_calls": 0, "solver_run_calls": 0,
        "model_save_calls": 0, "result_evaluation_calls": 0,
    }


def _model_ref(tag: str) -> dict[str, Any]:
    return {"model_tag": tag, "session_id": SESSION_ID,
            "server_instance_id": SERVER_INSTANCE, "generation": 1}


def _readback(tag: str, case_id: str, configuration_id: str = "baseline") -> dict[str, Any]:
    configuration = next(row for row in sensitivity_configurations()
                          if row.configuration_id == configuration_id)
    wetting = ({"sel_wet_flat_base": [10]} if case_id == "flat" else {
        "sel_wet_mesa_top": [10], "sel_wet_mesa_side": [11], "sel_wet_lower_base": [12]})
    return {
        "status": "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK",
        "native_acceptance": "NOT_RUN",
        "model_tag": tag,
        "case_id": case_id,
        "configuration_id": configuration_id,
        "geometry_dimension": 2,
        "geometry_axisymmetric": True,
        "geometry_domain_count": 2,
        "geometry_boundary_count": 9,
        "glue_domain_ids": [1],
        "gas_domain_ids": [2],
        "multiphase_domain_ids": [1, 2],
        "substrate_wetting_selections": wetting,
        "default_wetted_wall_feature_metadata": {
            "feature_tag": "ww1", "feature_type": "WettedWall",
            "classification": "COMSOL_6_4_DEFAULT_ALL_NOT_OVERRIDDEN_SELECTION_DOC_5838_CHUNK_19242",
            "selection_status": "DOCUMENTED_DEFAULT_NOT_EDITABLE",
        },
        "wetted_wall_features": [{"feature_tag": f"wwshape{index + 1}", "feature_type": "WettedWall",
                                   "surface_selection": name, "boundary_ids": ids,
                                   "contact_angle_expression": "thetaSubstrate"}
                                  for index, (name, ids) in enumerate(wetting.items())],
        "glue_density_expression": "rhoGlue",
        "gas_density_expression": "rhoGas",
        "glue_viscosity_expression": "muGlue",
        "gas_viscosity_expression": "muGas",
        "phase_field_type": "PhaseField",
        "phase_field_model_tag": "pfm_resolved_native_tag",
        "phase_field_model_type": "PhaseFieldModel",
        "phase_field_epsilon_expression": "epsPF",
        "phase_field_chi_option": "userdef",
        "phase_field_chi_value_type": "String",
        "phase_field_chi_expression": "chiPF",
        "phase1_initial_feature_tag": "init_phase1_resolved",
        "phase1_initial_feature_type": "init",
        "phase1_initial_values_option": "SpecifyPhase",
        "phase1_initial_value": "Fluid1phipf",
        "phase2_initial_feature_tag": "init_phase2_resolved",
        "phase2_initial_feature_type": "initFluid2",
        "phase2_initial_values_option": "SpecifyPhase",
        "phase2_initial_value": "Fluid2phipf",
        "all_domain_ids": [1, 2],
        "phase_field_domain_ids": [1, 2],
        "phase1_initial_raw_input_domain_ids": [1, 2],
        "phase1_initial_raw_input_entities_read_status": "READ",
        "phase1_initial_raw_named_selection_tag": "",
        "phase1_initial_selection_entities_readback": [1, 2],
        "phase1_initial_effective_domain_ids": [1],
        "phase1_initial_effective_domain_source": "DOCUMENTED_DEFAULT_REMAINDER_DERIVED_FROM_ALL_MINUS_FLUID2_OVERRIDE",
        "phase1_initial_effective_domain_is_direct_readback": False,
        "phase1_initial_selection_semantics":
            "COMSOL_6_4_DEFAULT_SELECTION_ALL_ENTITIES_NOT_OVERRIDDEN_BY_SAME_LEVEL_NODE_DOC_5838_CHUNK_19242",
        "phase1_initial_default_feature_classification": "NOT_EXPOSED_BY_READ_ONLY_API_METADATA",
        "phase1_initial_selection": [1],
        "phase2_initial_raw_input_domain_ids": [2],
        "phase2_initial_raw_input_entities_read_status": "READ",
        "phase2_initial_raw_named_selection_tag": "sel_gas_domain",
        "phase2_initial_selection_entities_readback": [2],
        "phase2_initial_effective_domain_ids": [2],
        "phase2_initial_effective_domain_source": "DIRECT_NAMED_SELECTION_READBACK",
        "phase2_initial_effective_domain_is_direct_readback": True,
        "phase2_initial_selection": [2],
        "flow_type": "LaminarFlow",
        "flow_compressibility": "Incompressible",
        "gravity_enabled": False,
        "pressure_reference_type": "PressurePointConstraint",
        "pressure_reference_value": "0[Pa]",
        "pressure_reference_point_ids": [8],
        "multiphase_volume_fraction_definition": "pf",
        "phase1_material_link": "matGlue",
        "phase2_material_link": "matGas",
        "surface_tension_enabled": True,
        "surface_tension_mode": "userdef",
        "surface_tension_expression": "sigma0",
        "phase_initialization_step_type": "PhaseInitialization",
        "transient_step_type": "Transient",
        "time_list_expression": "range(0[s],tCapillary/2,20*tCapillary)",
        "solver_sequence_tags": ["solPhaseGraph", "solStoredGraph"],
        "study_step_order": ["phasei", "time"],
        "time_initialization_method": "sol",
        "time_initialization_study": "stdShape",
        "use_initial_solution": "off",
        "use_user_tolerance": "on",
        "relative_tolerance": "1e-4",
        "generated_study_solver_chain": _generated_chain(),
        "attached_solver_sequence_tag": "solPhaseGraph",
        "stored_solution_output_tag": "solStoredGraph",
        "time_solver_feature_tag": "t1",
        "time_solver_output_owner": "GENERATED_TIME_SOLVER_FEATURE",
        "bdf_output_time_policy": "strict",
        "output_mode": "tsteps",
        "stored_time_steps_policy": 1,
        "maximum_step_s": configuration.maximum_step_s,
        "configuration_readback": {
            "configuration_id": configuration_id,
            "mesh": {"mesh_tag": "mesh1", "custom": True,
                     "hmax_m": configuration.mesh_hmax_m,
                     "hmin_m": configuration.mesh_hmin_m},
            "maximum_step_s": configuration.maximum_step_s,
        },
        "axis_boundary_ids": [1],
        "parameter_values_and_units": {
            "rhoGlue": {"value_si": 1200.0, "unit": "kg/m^3"},
            "muGlue": {"value_si": 1.0, "unit": "Pa*s"},
            "rhoGas": {"value_si": 1.2, "unit": "kg/m^3"},
            "muGas": {"value_si": 0.018, "unit": "Pa*s"},
            "sigma0": {"value_si": 0.03, "unit": "N/m"},
            "epsPF": {"value_si": configuration.epsilon_m, "unit": "m"},
            "Rdrop": {"value_si": 500e-6, "unit": "m"},
        },
        "mesh_tags": ["mesh1"],
        "solution_state_readback": {
            "status": "NO_STORED_SOLUTION_DATA",
            "readback_method": (
                "SolverSequence.isEmpty() [associated solution data]; "
                "SolverSequence.isInitialized() [solution initialization state]"),
            "solver_sequences": [{"solver_sequence_tag": "sol1", "readback_status": "EMPTY",
                                  "solutions_empty": True, "solution_initialized": False}],
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
        from tests.w24_live_binding_fixture import configure_backend
        configure_backend(self,project_id=PROJECT_ID,session_id=SESSION_ID,server_id=SERVER_INSTANCE)

    def bind(self, model_ref: Mapping[str, Any], project_id: str = PROJECT_ID) -> None:
        from tests.w24_live_binding_fixture import bind_ledger
        bind_ledger(self,model_ref)
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
        self.artifacts: dict[str, dict[str, Any]] = {}

    def get_metadata(self, table: str, key: str):
        assert table == "artifacts"
        value = self.artifacts.get(key)
        return dict(value) if value is not None else None

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
        self.invalid_build_chain = False
        self.mutate_reopen = False
        self.bad_save_hash = False
        self.solution_state_empty = True
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
        if operation == "model.load":
            artifact_id = arguments["artifact_id"]
            relative = f"g2_artifacts/registered/{artifact_id}.mph"
            registered = self.workspace / relative
            record = self.store.get_metadata("artifacts", artifact_id)
            if (not registered.is_file() or record is None
                    or hashlib.sha256(registered.read_bytes()).hexdigest() != artifact_id):
                return {"success": False}
            ref = _model_ref(self._loaded_tag)
            self.backend.bind(ref)
            response = self._worker_response(True, "SUCCEEDED", ref, execution,
                {"status": "LOADED", "project_id": execution["project_id"],
                 "model_tag": self._loaded_tag, "artifact_id": artifact_id,
                 "sha256": artifact_id, "size_bytes": registered.stat().st_size,
                 "ownership": "mcp_owned"})
            response["execution"]["model_ownership"] = "mcp_owned"
            response["execution"]["request_id"] = execution["request_id"]
            response["execution"]["idempotency_key"] = execution["idempotency_key"]
            return response
        if operation != "operation_call":
            raise AssertionError(f"unexpected production operation: {operation}")
        if arguments["operation_id"] == "artifact.register":
            body = arguments["arguments"]
            source = self.workspace / body["path"]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            relative = f"g2_artifacts/registered/{digest}.mph"
            registered = self.workspace / relative
            registered.parent.mkdir(parents=True, exist_ok=True)
            registered.write_bytes(source.read_bytes())
            operation_id = f"fake-register-{digest}"
            provenance = {"source_project_relative_path": body["path"],
                          "source_sha256": digest,
                          "request_id": body["request_id"],
                          "registering_operation_id": operation_id}
            record = {"artifact_id": digest, "sha256": digest,
                      "path": relative, "size": registered.stat().st_size,
                      "project_id": body["project_id"], "role": body["role"],
                      "classification": body["classification"],
                      "artifact_type": body["artifact_type"],
                      "format_version": body["format_version"],
                      "provenance": provenance}
            self.store.artifacts[digest] = record
            return {"success": True,
                    "data": {**record, "host_scope": "local_loopback_engine_and_current_project"},
                    "execution": {"operation_id": operation_id,
                                  "job_id": f"fake-job-{digest}",
                                  "project_id": body["project_id"],
                                  "request_id": body["request_id"],
                                  "idempotency_key": body["idempotency_key"]}}
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
                      "case_id": case_id, "configuration_id": action_args.get("configuration_id", "baseline"),
                      "model_tag": self._shape_tag,
                      "study_run_calls": 0, "phase_initialization_executed": False}
            chain = _generated_chain()
            nested["study_readback"] = {
                "status": "UNSOLVED_CHAIN_CONFIGURED", "study_step_order": ["phasei", "time"],
                "time_initialization_method": "sol", "time_initialization_study": "stdShape",
                "use_initial_solution": "off", "study_run_calls": 0,
                "phase_initialization_executed": False, "time_solver_settings_configured": True,
                "generated_study_solver_chain": chain,
                "solver_sequence_tags": chain["sequence_tag_union"],
                "attached_solver_sequence_tag": chain["selected_attached_sequence_tag"],
                "stored_solution_output_tag": chain["store_solution_output_tag"],
            }
            if self.invalid_build_chain:
                nested["study_readback"]["generated_study_solver_chain"]["sequence_records"][0][
                    "feature_tree_capture"]["nodes"][3]["store_solution_properties"]["properties"][
                    "sol"]["value_capture"]["value"] = "solInvented"
            return self._java_response(nested, ref, execution, revision)
        assert entrypoint == "W24StaticShapeReadback#run"
        action = action_args["action"]
        if action == "readback":
            case_id = self._current_case
            self._readback_count += 1
            nested = _readback(self._loaded_tag if ref["model_tag"] == self._loaded_tag else self._shape_tag,
                               case_id, action_args.get("expected_configuration_id", "baseline"))
            nested["solution_state_readback"]["solver_sequences"][0]["solutions_empty"] = self.solution_state_empty
            nested["solution_state_readback"]["solver_sequences"][0]["readback_status"] = (
                "EMPTY" if self.solution_state_empty else "NONEMPTY")
            if self.mutate_reopen and self._readback_count > 1:
                nested["maximum_step_s"] *= 2
                nested["configuration_readback"]["maximum_step_s"] *= 2
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
                                        "readback_method": (
                                            "SolverSequence.isEmpty() [associated solution data]; "
                                            "SolverSequence.isInitialized() [solution initialization state]"),
                                        "solver_sequences": [{"solver_sequence_tag": "sol1",
                                                              "readback_status": "EMPTY",
                                                              "solutions_empty": True,
                                                              "solution_initialized": False}]},
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

def test_campaign_factory_creates_exclusive_private_evidence_child_for_real_managed_runner(tmp_path):
    from tools.run_native_w24_static_shape_setup_campaign import _new_static_shape_managed_runner

    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    run_dir = tmp_path / "native-run"
    run_dir.mkdir()
    evidence = run_dir / "static_shape_managed"
    source_manifest = prepare_project_sources(workspace)
    daemon = FakeDaemon(workspace)
    project_create_response = {"success": True, "data": {"project": {
        "project_id": PROJECT_ID, "workspace": str(workspace)}}}
    parent = ManagedModelBinding(PROJECT_ID, SESSION_ID, daemon.parent, 0)

    runner = _new_static_shape_managed_runner(
        daemon=daemon, project_id=PROJECT_ID, project_workspace=workspace,
        project_create_response=project_create_response, parent_binding=parent,
        source_manifest=source_manifest, setup_runner=FakeSetupRunner(),
        evidence_dir=evidence, timeout_s=300.0)

    assert type(runner) is StaticShapeManagedRunner
    assert runner.evidence_dir == evidence.resolve(strict=True)
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert runner.response_dir.is_dir()
    assert runner.response_dir.stat().st_mode & 0o777 == 0o700
    assert daemon.calls == []

    with pytest.raises(FileExistsError):
        _new_static_shape_managed_runner(
            daemon=daemon, project_id=PROJECT_ID, project_workspace=workspace,
            project_create_response=project_create_response, parent_binding=parent,
            source_manifest=source_manifest, setup_runner=FakeSetupRunner(),
            evidence_dir=evidence, timeout_s=300.0)


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
                          "operation_call", "operation_call", "operation_call",
                          "model.load", "model.inspect", "operation_call"]
    assert all("study.run" not in operation for operation in operations)
    save_request = next(request for operation, request in daemon.calls
                        if operation == "operation_call" and
                        request["arguments"]["arguments"]["entrypoint"] == "W24StaticShapeReadback#run" and
                        request["arguments"]["arguments"]["arguments"].get("action") == "save")
    assert save_request["arguments"]["arguments"]["arguments"]["path"].startswith(str(workspace))
    register_request = next(request for operation, request in daemon.calls
                            if operation == "operation_call" and
                            request["arguments"]["operation_id"] == "artifact.register")
    load_request = next(request for operation, request in daemon.calls if operation == "model.load")
    assert load_request["execution"]["session_id"] == SESSION_ID
    assert load_request["arguments"]["artifact_id"] == hashlib.sha256(
        Path(result["registered_project_artifact"]["path"]).read_bytes()).hexdigest()
    assert register_request["arguments"]["arguments"]["path"] == "outputs/static_shape_flat.mph"


def test_build_chain_gate_rejects_bad_store_reference_before_model_adoption(tmp_path):
    runner, daemon, _workspace = _setup(tmp_path)
    daemon.invalid_build_chain = True

    with pytest.raises(CampaignError):
        runner.build_save_reopen("flat")

    operations = [operation for operation, _request in daemon.calls]
    assert operations == ["operation_call"]


@pytest.mark.parametrize(("field", "bad_value"), [
    ("gravity_enabled", "off"),
    ("gravity_enabled", 0),
    ("surface_tension_enabled", "on"),
    ("surface_tension_enabled", 1),
    ("flow_compressibility", "incompressible"),
])
def test_shape_readback_rejects_boolean_aliases_and_wrong_enum_case(field, bad_value):
    result = _readback("shape-native-tag", "flat")
    result[field] = bad_value

    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(result, "flat")


@pytest.mark.parametrize(("field", "bad_value"), [
    ("study_step_order", ["time", "phasei"]),
    ("time_initialization_method", "init"),
    ("time_initialization_study", "phasei"),
    ("use_initial_solution", "on"),
    ("use_user_tolerance", "off"),
    ("relative_tolerance", "1e-3"),
    ("time_solver_feature_tag", ""),
    ("time_solver_output_owner", "TRANSIENT_STUDY_FEATURE"),
])
def test_shape_readback_rejects_study_solver_property_domain_drift(field, bad_value):
    result = _readback("shape-native-tag", "flat")
    result[field] = bad_value

    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(result, "flat")


@pytest.mark.parametrize("mutate", [
    lambda chain: chain["sequence_records"][0]["feature_tree_capture"]["nodes"][3][
        "store_solution_properties"]["properties"]["sol"].update(value_capture=_capture("solInvented")),
    lambda chain: chain["sequence_records"][0]["feature_tree_capture"]["nodes"][3][
        "store_solution_properties"]["properties"]["sol"].update(declared_type_capture=_capture("Int")),
    lambda chain: chain["sequence_records"][0]["feature_tree_capture"]["nodes"][5][
        "initial_source_properties"]["properties"]["initsol"].update(value_capture=_capture("solWrong")),
    lambda chain: chain["sequence_records"][0]["feature_tree_capture"]["nodes"][0][
        "study_step_reference_properties"]["properties"]["studystep"].update(value_capture=_capture("time")),
    lambda chain: chain["sequence_query_rows"][4]["capture"].update(value=["solWrongStored"]),
    lambda chain: chain["study_step_initialization_readback"]["time"].update(initstudy="zero"),
    lambda chain: chain["sequence_records"][1]["attached_capture"].update(value=True),
    lambda chain: chain["sequence_records"][0].__setitem__("capture_complete", False),
])
def test_generated_chain_gate_rejects_typed_reference_and_topology_drift(mutate):
    result = _readback("shape-native-tag", "flat")
    chain = json.loads(json.dumps(result["generated_study_solver_chain"]))
    mutate(chain)
    result["generated_study_solver_chain"] = chain
    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(result, "flat")


def test_phase_field_default_remainder_and_fluid2_override_are_provenance_checked():
    result = _readback("shape-native-tag", "flat")
    StaticShapeManagedRunner._validate_shape_readback(result, "flat")

    direct_default = json.loads(json.dumps(result))
    direct_default["phase1_initial_selection_entities_readback"] = [1]
    direct_default["phase1_initial_effective_domain_source"] = (
        "DIRECT_SELECTION_GETTER_REMAINDER_MATCHING_DOCUMENTED_DEFAULT")
    direct_default["phase1_initial_effective_domain_is_direct_readback"] = True
    StaticShapeManagedRunner._validate_shape_readback(direct_default, "flat")

    mutations = [
        ("phase1_initial_raw_input_domain_ids", [1]),
        ("phase1_initial_raw_named_selection_tag", "sel_glue_domain"),
        ("phase1_initial_default_feature_classification", "DEFAULT"),
        ("phase1_initial_selection_entities_readback", [2]),
        ("phase2_initial_raw_named_selection_tag", ""),
        ("phase2_initial_effective_domain_ids", [1]),
        ("phase1_initial_feature_type", "InitialValues"),
        ("phase2_initial_feature_type", "init"),
    ]
    for field, value in mutations:
        changed = json.loads(json.dumps(result))
        changed[field] = value
        with pytest.raises(CampaignError):
            StaticShapeManagedRunner._validate_shape_readback(changed, "flat")


def test_wetted_wall_default_and_custom_inventory_are_separate_and_exact():
    result = _readback("shape-native-tag", "flat")
    StaticShapeManagedRunner._validate_shape_readback(result, "flat")

    mutations = [
        ("feature_tag", "wwshape1"),
        ("feature_type", "Generic"),
        ("classification", "CUSTOM_EDITABLE_SELECTION"),
        ("selection_status", "MUTATED"),
    ]
    for field, value in mutations:
        changed = json.loads(json.dumps(result))
        changed["default_wetted_wall_feature_metadata"][field] = value
        with pytest.raises(CampaignError):
            StaticShapeManagedRunner._validate_shape_readback(changed, "flat")

    extra_default_count = json.loads(json.dumps(result))
    extra_default_count["wetted_wall_features"].append({
        "feature_tag": "ww1", "feature_type": "WettedWall",
        "surface_selection": "sel_wet_flat_base", "boundary_ids": [10],
        "contact_angle_expression": "thetaSubstrate",
    })
    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(extra_default_count, "flat")

    wrong_custom = json.loads(json.dumps(result))
    wrong_custom["wetted_wall_features"][0]["feature_tag"] = "ww2"
    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(wrong_custom, "flat")

    wrong_angle = json.loads(json.dumps(result))
    wrong_angle["wetted_wall_features"][0]["contact_angle_expression"] = "90[deg]"
    with pytest.raises(CampaignError):
        StaticShapeManagedRunner._validate_shape_readback(wrong_angle, "flat")

    overlapping = _readback("shape-native-tag", "step")
    overlapping["substrate_wetting_selections"]["sel_wet_mesa_side"] = [10]
    overlapping["wetted_wall_features"][1]["boundary_ids"] = [10]
    with pytest.raises(CampaignError, match="overlap"):
        StaticShapeManagedRunner._validate_shape_readback(overlapping, "step")

def test_managed_step_shape_uses_all_three_named_wetting_surfaces(tmp_path):
    runner, _, _ = _setup(tmp_path, case_id="step")
    result = runner.build_save_reopen("step")
    assert set(result["pre_save_configuration_readback"]["substrate_wetting_selections"]) == {
        "sel_wet_mesa_top", "sel_wet_mesa_side", "sel_wet_lower_base"}
    assert len(result["pre_save_configuration_readback"]["wetted_wall_features"]) == 3


def test_variant_setup_binds_actual_epsilon_mesh_and_solver_step_readbacks(tmp_path):
    runner, _, workspace = _setup(tmp_path)
    result = runner.build_save_reopen("flat", "epsilon_6um")

    assert result["schema"] == "W24_STATIC_SHAPE_MANAGED_SETUP_V2"
    assert result["configuration_id"] == "epsilon_6um"
    readback = result["reopened_configuration_readback"]
    assert readback["configuration_id"] == "epsilon_6um"
    assert readback["parameter_values_and_units"]["epsPF"]["value_si"] == 6e-6
    assert readback["configuration_readback"]["mesh"]["hmax_m"] == 3e-6
    assert readback["configuration_readback"]["mesh"]["hmin_m"] == 1.5e-6
    assert readback["configuration_readback"]["maximum_step_s"] == pytest.approx(1 / 600)
    assert Path(result["project_artifact"]["path"]).is_relative_to(workspace)


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
    assert "model.load" not in [operation for operation, _ in daemon.calls]


def test_managed_setup_refuses_preexisting_stored_solution_without_clearing_it(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.solution_state_empty = False

    with pytest.raises(CampaignError, match="solver-sequence readback"):
        runner.build_save_reopen("flat")

    assert [operation for operation, _ in daemon.calls].count("operation_call") == 2
    assert "model.load" not in [operation for operation, _ in daemon.calls]
    assert not list((tmp_path / "registered-project" / "outputs").glob("*.mph"))


def test_solution_state_readback_accepts_exact_uninitialized_sequence():
    value = {
        "status": "NO_STORED_SOLUTION_DATA",
        "readback_method": (
            "SolverSequence.isEmpty() [associated solution data]; "
            "SolverSequence.isInitialized() [solution initialization state]"),
        "solver_sequences": [{"solver_sequence_tag": "sol1", "readback_status": "EMPTY",
                              "solutions_empty": True, "solution_initialized": False}],
    }
    assert StaticShapeManagedRunner._validate_no_stored_solution_data(value) == value


@pytest.mark.parametrize("row", [
    {"solver_sequence_tag": "sol1", "readback_status": "NONEMPTY",
     "solutions_empty": False, "solution_initialized": True},
    {"solver_sequence_tag": "sol1", "readback_status": "EMPTY",
     "solutions_empty": True, "solution_initialized": "false"},
    {"solver_sequence_tag": "sol1", "readback_status": "EMPTY",
     "solution_empty": True, "solution_initialized": False},
])
def test_solution_state_readback_rejects_nonempty_or_incomplete_status(row):
    value = {"status": "NO_STORED_SOLUTION_DATA",
             "readback_method": (
                 "SolverSequence.isEmpty() [associated solution data]; "
                 "SolverSequence.isInitialized() [solution initialization state]"),
             "solver_sequences": [row]}
    with pytest.raises(CampaignError, match="solver-sequence readback"):
        StaticShapeManagedRunner._validate_no_stored_solution_data(value)


def test_reopened_native_configuration_must_match_the_saved_model(tmp_path):
    runner, daemon, _ = _setup(tmp_path)
    daemon.mutate_reopen = True
    with pytest.raises(CampaignError, match="maximum step differs|changed after managed save/reopen"):
        runner.build_save_reopen("flat")
    assert [operation for operation, _ in daemon.calls].count("model.load") == 1


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
        from tests.w24_live_binding_fixture import configure_backend,bind_ledger
        configure_backend(self,project_id=project_id,session_id=session_id,server_id=SERVER_INSTANCE)
        bind_ledger(self,model_ref)
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
            backend=backend, worker_instance_id="worker-w24-session-route", worker=backend.worker,
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
