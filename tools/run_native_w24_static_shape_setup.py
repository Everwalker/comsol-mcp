#!/usr/bin/env python3
"""Managed build/save/reopen readback adapter for the W24 static-shape fixture.

This module is the native-action boundary used after a separately approved
setup has created a registered project, an owned server/Worker, and a managed
parent ModelRef.  It does not start COMSOL or run Phase Initialization,
Study.run, a solver, or an export.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from tools.run_native_w24_cure_science import (
    CampaignError,
    ManagedModelBinding,
    _model_ref_matches_worker_epoch,
    _project_path,
)


REPO = Path(__file__).resolve().parents[1]
STATIC_SHAPE_FIXTURE = REPO / "tools/java/W24StaticShapeFixture.java"
STATIC_SHAPE_READBACK = REPO / "tools/java/W24StaticShapeReadback.java"
STATIC_SHAPE_CASES = frozenset({"flat", "step"})
EXPECTED_WET_SELECTIONS = {
    "flat": {"sel_wet_flat_base"},
    "step": {"sel_wet_mesa_top", "sel_wet_mesa_side", "sel_wet_lower_base"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_fsynced(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _append_jsonl_fsynced(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str) + "\n").encode("utf-8")
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_project_sources(project_workspace: Path, *, fixture_source: Path = STATIC_SHAPE_FIXTURE,
                            readback_source: Path = STATIC_SHAPE_READBACK) -> dict[str, Any]:
    """Copy the two reviewed Java sources into the registered project without overwrite."""
    workspace = project_workspace.resolve(strict=True)
    if not workspace.is_dir() or project_workspace.is_symlink():
        raise CampaignError("registered static-shape workspace must be a real directory")
    sources = {"fixture": fixture_source, "readback": readback_source}
    rows: dict[str, Any] = {}
    for role, supplied in sources.items():
        source = supplied.resolve(strict=True)
        if supplied.is_symlink() or not source.is_file():
            raise CampaignError(f"{role} Java source must be a regular non-symlink file")
        supplied_destination = workspace / source.name
        destination = _project_path(workspace, supplied_destination,
                                   must_exist=supplied_destination.exists())
        source_hash = _sha256(source)
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or _sha256(destination) != source_hash:
                raise CampaignError(f"existing project Java source differs from reviewed {role} source")
            copied = False
        else:
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            copied = True
        if _sha256(destination) != source_hash:
            raise CampaignError(f"project copy of {role} Java source failed SHA-256 verification")
        rows[role] = {"source_path": str(source), "project_path": str(destination),
                      "sha256": source_hash, "size_bytes": destination.stat().st_size,
                      "copied_this_call": copied}
    outputs = workspace / "outputs"
    if outputs.exists():
        if outputs.is_symlink() or not outputs.is_dir() or outputs.resolve(strict=True) != outputs:
            raise CampaignError("registered outputs directory is aliased or not a directory")
    else:
        outputs.mkdir(mode=0o700)
    return {"status": "PROJECT_JAVA_SOURCES_VERIFIED", "project_workspace": str(workspace),
            "sources": rows, "outputs_directory": str(outputs)}


class StaticShapeManagedRunner:
    """One no-solve static-shape build, native readback, save, and managed reopen."""

    def __init__(self, *, daemon: Any, project_id: str, project_workspace: Path,
                 project_create_response: Mapping[str, Any],
                 parent_binding: ManagedModelBinding, source_manifest: Mapping[str, Any],
                 setup_runner: Any, evidence_dir: Path, timeout_s: float = 300.0):
        if not isinstance(project_id, str) or not project_id:
            raise CampaignError("static-shape runner requires the authoritative registered project id")
        self.daemon = daemon
        self.project_id = project_id
        self.workspace = project_workspace.resolve(strict=True)
        if not self.workspace.is_dir() or project_workspace.is_symlink():
            raise CampaignError("static-shape workspace must be the exact registered real directory")
        project_data = project_create_response.get("data") if isinstance(project_create_response, Mapping) else None
        project_record = project_data.get("project") if isinstance(project_data, Mapping) else None
        recorded_workspace = project_record.get("workspace") if isinstance(project_record, Mapping) else None
        if (not isinstance(project_record, Mapping) or project_record.get("project_id") != project_id or
            not isinstance(recorded_workspace, str) or not Path(recorded_workspace).is_absolute() or
            Path(recorded_workspace) != self.workspace or
            Path(recorded_workspace).resolve(strict=True) != self.workspace):
            raise CampaignError("runner project/workspace must exactly match the production project.create response")
        if parent_binding.project_id != project_id:
            raise CampaignError("parent ModelRef is not bound to the registered static-shape project")
        if not math_is_positive_finite(timeout_s):
            raise CampaignError("static-shape native RPC timeout must be positive and finite")
        self.parent_binding = parent_binding
        self.setup_runner = setup_runner
        self.timeout_s = float(timeout_s)
        self.source_paths = self._verify_sources(source_manifest)
        self.evidence_dir = evidence_dir.resolve(strict=True)
        if not self.evidence_dir.is_dir() or evidence_dir.is_symlink():
            raise CampaignError("static-shape evidence directory must already exist as a real directory")
        self.response_dir = self.evidence_dir / "managed_responses"
        self.response_dir.mkdir(mode=0o700)
        self.rpc_journal = self.evidence_dir / "managed_action_journal.jsonl"
        self._model_tag = parent_binding.model_tag
        self._binding = parent_binding
        self._verify_persisted_binding(parent_binding)

    def _verify_sources(self, source_manifest: Mapping[str, Any]) -> dict[str, Path]:
        declared = source_manifest.get("sources") if isinstance(source_manifest, Mapping) else None
        if not isinstance(declared, Mapping):
            raise CampaignError("static-shape source manifest is missing its fixture/readback records")
        paths: dict[str, Path] = {}
        for role, expected_name in (("fixture", STATIC_SHAPE_FIXTURE.name),
                                    ("readback", STATIC_SHAPE_READBACK.name)):
            record = declared.get(role)
            if not isinstance(record, Mapping) or record.get("project_path") is None:
                raise CampaignError(f"static-shape source manifest omitted {role} project path")
            path = _project_path(self.workspace, Path(str(record["project_path"])), must_exist=True)
            if path.name != expected_name or _sha256(path) != record.get("sha256"):
                raise CampaignError(f"project {role} source does not match the declared reviewed source hash")
            paths[role] = path
        return paths

    def _verify_persisted_binding(self, binding: ManagedModelBinding) -> Mapping[str, Any]:
        registry = getattr(self.daemon, "session_registry", None)
        get_context = getattr(registry, "get", None)
        if not callable(get_context):
            raise CampaignError("ControlDaemon cannot resolve the exact registered project/session context")
        try:
            context = get_context(self.project_id, binding.session_id)
        except Exception as exc:
            raise CampaignError("the bound ModelRef session has no exact live registered runtime context") from exc
        if (getattr(context, "project_id", None) != self.project_id or
                getattr(context, "session_id", None) != binding.session_id):
            raise CampaignError("registered runtime context differs from the exact ModelRef project/session")
        backend = getattr(context, "backend", None)
        if backend is None:
            raise CampaignError("registered runtime context has no session-bound backend")
        reader = getattr(backend, "model_project_binding", None)
        if not callable(reader):
            raise CampaignError("ControlDaemon cannot read the persisted ModelRef/project association")
        worker_identity = getattr(backend, "worker_identity", None)
        if not _model_ref_matches_worker_epoch(binding, worker_identity):
            raise CampaignError("ModelRef does not belong to the exact active Worker server instance")
        persisted = reader(dict(binding.model_ref))
        if (not isinstance(persisted, Mapping) or persisted.get("attribution") != "PROJECT_BOUND" or
                persisted.get("project_id") != self.project_id):
            raise CampaignError("ModelRef is not persistently associated with the exact registered project")
        return dict(persisted)

    def _study_run_ledger(self) -> list[dict[str, Any]]:
        from tools.run_native_w24_cure_preflight import _actual_study_run_submissions

        store = getattr(self.daemon, "store", None)
        if store is None:
            raise CampaignError("ControlDaemon durable store is unavailable for the zero-solve gate")
        try:
            return list(_actual_study_run_submissions(self.daemon, self.project_id))
        except Exception as exc:
            raise CampaignError("complete project ledger could not prove the static-shape zero-solve gate") from exc

    def _record_response(self, call_id: str, response: Any) -> None:
        if isinstance(response, Mapping):
            payload: Mapping[str, Any] = dict(response)
        else:
            payload = {"unstructured_response": repr(response)}
        _write_json_fsynced(self.response_dir / f"{call_id}.json", payload)

    def _dispatch(self, operation: str, arguments: Mapping[str, Any], *,
                  binding: ManagedModelBinding | None = None,
                  session_id: str | None = None,
                  worker_required: bool, timeout_s: float | None = None) -> dict[str, Any]:
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        if not math_is_positive_finite(timeout):
            raise CampaignError("static-shape RPC timeout must be positive and finite")
        if binding is not None:
            self._verify_persisted_binding(binding)
            if session_id is not None and session_id != binding.session_id:
                raise CampaignError("explicit session id conflicts with the bound ModelRef")
            session_id = binding.session_id
        elif session_id is not None and session_id != self.parent_binding.session_id:
            raise CampaignError("unbound project action must use the registered parent Worker session")
        call_id = uuid4().hex
        event = {"call_id": call_id, "operation": operation, "project_id": self.project_id,
                 "model_tag": binding.model_tag if binding else None,
                 "started": True}
        _append_jsonl_fsynced(self.rpc_journal, {"event": "dispatch_started", **event})
        try:
            execution: dict[str, Any] = {
                "project_id": self.project_id, "rpc_timeout_s": timeout,
                "queue_timeout_s": 60.0, "execution_timeout_s": None,
                "idempotency_key": f"w24-shape-{uuid4()}",
                "request_id": f"w24-shape-request-{uuid4()}",
            }
            if session_id is not None:
                execution["session_id"] = session_id
            if binding is not None:
                execution["model_ref"] = dict(binding.model_ref)
                execution["expected_revision"] = binding.revision
            response = self.daemon.dispatch({"operation": operation,
                                             "arguments": dict(arguments),
                                             "execution": execution})
        except BaseException as exc:
            _append_jsonl_fsynced(self.rpc_journal, {
                "event": "dispatch_exception", **event, "terminal_observed": False,
                "error": f"{type(exc).__name__}: {exc}"})
            raise
        if not isinstance(response, dict):
            _append_jsonl_fsynced(self.rpc_journal, {
                "event": "dispatch_returned", **event, "terminal_observed": False,
                "response_type": type(response).__name__})
            raise CampaignError(f"{operation} returned a non-object managed response; no retry")
        self._record_response(call_id, response)
        worker_data = response.get("data", {}).get("worker") if isinstance(response.get("data"), Mapping) else None
        terminal = (self.setup_runner._worker_request_terminal(response)
                    if worker_required else response.get("success") is True)
        _append_jsonl_fsynced(self.rpc_journal, {
            "event": "dispatch_returned", **event, "terminal_observed": bool(terminal),
            "success": response.get("success") is True,
            "worker_status": worker_data.get("status") if isinstance(worker_data, Mapping) else None,
            "request_id": response.get("execution", {}).get("request_id")
                          if isinstance(response.get("execution"), Mapping) else None})
        if not terminal:
            raise CampaignError(f"{operation} lacks an observed terminal result; no retry or follow-on dispatch")
        if response.get("success") is not True:
            raise CampaignError(f"{operation} failed: {json.dumps(response, ensure_ascii=False, default=str)[:3000]}")
        return response

    def _java_action(self, binding: ManagedModelBinding, *, source_role: str,
                     entrypoint: str, arguments: Mapping[str, Any]) -> tuple[ManagedModelBinding, dict[str, Any], dict[str, Any]]:
        source = self.source_paths[source_role]
        response = self._dispatch("operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": source.name, "entrypoint": entrypoint,
                          "arguments": dict(arguments), "mode": "trusted"},
        }, binding=binding, worker_required=True)
        result = self.setup_runner._java_action_readback(response, f"W24 static-shape {entrypoint}")
        updated = self._updated_binding(binding, response)
        return updated, response, result

    def _updated_binding(self, prior: ManagedModelBinding,
                         response: Mapping[str, Any]) -> ManagedModelBinding:
        execution = response.get("execution")
        if not isinstance(execution, Mapping):
            raise CampaignError("managed Worker response omitted execution identity")
        model_ref = execution.get("model_ref")
        project = execution.get("project_id")
        session = execution.get("session_id", prior.session_id)
        revision = execution.get("revision")
        if ((project is not None and project != self.project_id) or session != prior.session_id or
            not isinstance(model_ref, Mapping) or dict(model_ref) != dict(prior.model_ref) or
            isinstance(revision, bool) or not isinstance(revision, int) or revision < prior.revision):
            raise CampaignError("managed Worker response changed or omitted the exact project ModelRef/revision")
        current = ManagedModelBinding(self.project_id, prior.session_id, dict(model_ref), revision)
        self._verify_persisted_binding(current)
        if prior.model_tag == self.parent_binding.model_tag:
            self.parent_binding = current
        return current

    def _adopt(self, model_tag: str) -> ManagedModelBinding:
        self._verify_persisted_binding(self.parent_binding)
        call_id = uuid4().hex
        _append_jsonl_fsynced(self.rpc_journal, {
            "event": "dispatch_started", "call_id": call_id, "operation": "model.adopt",
            "project_id": self.project_id, "native_model_tag": model_tag})
        try:
            response = self.daemon.dispatch({
                "operation": "model.adopt", "arguments": {"server_model_tag": model_tag},
                "execution": {"project_id": self.project_id,
                              "session_id": self.parent_binding.session_id,
                              "idempotency_key": f"w24-shape-adopt-{uuid4()}",
                              "request_id": f"w24-shape-adopt-request-{uuid4()}",
                              "rpc_timeout_s": self.timeout_s, "queue_timeout_s": 60.0,
                              "execution_timeout_s": None},
            })
        except BaseException as exc:
            _append_jsonl_fsynced(self.rpc_journal, {
                "event": "dispatch_exception", "call_id": call_id, "operation": "model.adopt",
                "project_id": self.project_id, "terminal_observed": False,
                "error": f"{type(exc).__name__}: {exc}"})
            raise
        self._record_response(call_id, response)
        if not isinstance(response, Mapping) or response.get("success") is not True:
            _append_jsonl_fsynced(self.rpc_journal, {
                "event": "dispatch_returned", "call_id": call_id, "operation": "model.adopt",
                "project_id": self.project_id, "success": False, "terminal_observed": False})
            raise CampaignError("canonical project-scoped model.adopt failed: " +
                                json.dumps(response, ensure_ascii=False, default=str)[:3000])
        _append_jsonl_fsynced(self.rpc_journal, {
            "event": "dispatch_returned", "call_id": call_id, "operation": "model.adopt",
            "project_id": self.project_id, "success": True, "terminal_observed": True})
        binding = ManagedModelBinding.from_adopt_response(
            response, project_id=self.project_id, expected_model_tag=model_tag)
        if binding.session_id != self.parent_binding.session_id:
            raise CampaignError("model.adopt returned a different live Worker session")
        self._verify_persisted_binding(binding)
        return binding

    def _inspect(self, binding: ManagedModelBinding) -> tuple[ManagedModelBinding, dict[str, Any]]:
        response = self._dispatch("model.inspect", {"detail": "summary"}, binding=binding,
                                  worker_required=True)
        data = response.get("data")
        execution = response.get("execution")
        identity = data.get("model_identity") if isinstance(data, Mapping) else None
        echoed = execution.get("model_ref") if isinstance(execution, Mapping) else None
        project_echo = execution.get("project_id") if isinstance(execution, Mapping) else None
        if (not isinstance(identity, Mapping) or dict(identity) != dict(binding.model_ref) or
            not isinstance(echoed, Mapping) or dict(echoed) != dict(binding.model_ref) or
            execution.get("session_id") != binding.session_id or
            (project_echo is not None and project_echo != self.project_id)):
            raise CampaignError("model.inspect failed exact native tag/session/project identity readback")
        current = self._updated_binding(binding, response)
        return current, {"data": dict(data), "response": response,
                         "persisted_project_binding": self._verify_persisted_binding(current),
                         "model_binding": current.as_record()}

    @staticmethod
    def _validate_shape_readback(result: Mapping[str, Any], expected_case: str) -> dict[str, Any]:
        if result.get("status") != "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK" or result.get("case_id") != expected_case:
            raise CampaignError("native readback did not identify the requested static-shape model")
        if (result.get("native_acceptance") != "NOT_RUN" or
            result.get("geometry_dimension") != 2 or result.get("geometry_axisymmetric") is not True or
            result.get("geometry_domain_count") != 2):
            raise CampaignError("native static-shape readback failed the two-domain axisymmetric gate")
        wetting = result.get("substrate_wetting_selections")
        if not isinstance(wetting, Mapping) or set(wetting) != EXPECTED_WET_SELECTIONS[expected_case]:
            raise CampaignError("native static-shape readback has incomplete or unexpected wetting selections")
        if any(not isinstance(ids, list) or not ids for ids in wetting.values()):
            raise CampaignError("native static-shape readback contains an empty substrate boundary selection")
        observed_wet_ids: set[int] = set()
        for ids in wetting.values():
            for value in ids:
                if isinstance(value, bool) or not isinstance(value, int) or value in observed_wet_ids:
                    raise CampaignError("native static-shape substrate boundary selections overlap or contain invalid IDs")
                observed_wet_ids.add(value)
        axis_ids = result.get("axis_boundary_ids")
        if not isinstance(axis_ids, list) or observed_wet_ids.intersection(axis_ids):
            raise CampaignError("native static-shape wetting selections overlap the symmetry axis")
        glue_ids = result.get("glue_domain_ids")
        gas_ids = result.get("gas_domain_ids")
        multiphase_ids = result.get("multiphase_domain_ids")
        if (not isinstance(glue_ids, list) or len(glue_ids) != 1 or
            not isinstance(gas_ids, list) or len(gas_ids) != 1 or glue_ids == gas_ids or
            not isinstance(multiphase_ids, list) or set(multiphase_ids) != set(glue_ids + gas_ids)):
            raise CampaignError("native static-shape glue/gas/material domain mappings are not exact and distinct")
        if len(result.get("wetted_wall_features", [])) != len(EXPECTED_WET_SELECTIONS[expected_case]):
            raise CampaignError("native static-shape readback has the wrong number of Wetted Wall features")
        wall_features = result.get("wetted_wall_features")
        if (not isinstance(wall_features, list) or
            {row.get("surface_selection") for row in wall_features if isinstance(row, Mapping)} != set(wetting) or
            any(row.get("contact_angle_expression") != "thetaSubstrate" for row in wall_features
                if isinstance(row, Mapping))):
            raise CampaignError("native Wetted Wall feature mapping does not cover the named physical surfaces")
        wall_by_surface = {row["surface_selection"]: row for row in wall_features
                           if isinstance(row, Mapping) and isinstance(row.get("surface_selection"), str)}
        if any(wall_by_surface[name].get("boundary_ids") != ids for name, ids in wetting.items()):
            raise CampaignError("native Wetted Wall feature boundary IDs differ from their named substrate selections")
        if result.get("flow_compressibility") != "incompressible" or result.get("pressure_reference_value") != "0[Pa]":
            raise CampaignError("native closed-cavity flow pressure/compressibility configuration is incomplete")
        if (result.get("multiphase_volume_fraction_definition") != "pf" or
            result.get("phase1_material_link") != "matGlue" or result.get("phase2_material_link") != "matGas" or
            result.get("surface_tension_mode") != "userdef" or
            result.get("surface_tension_expression") != "sigma0"):
            raise CampaignError("native two-phase material/surface-tension configuration is incomplete")
        if (result.get("phase1_initial_value") != "Fluid1phipf" or
            result.get("phase2_initial_value") != "Fluid2phipf" or
            result.get("phase1_initial_selection") != glue_ids or
            result.get("phase2_initial_selection") != gas_ids):
            raise CampaignError("native phase initialization does not map one distinct fluid domain per phase")
        if (result.get("phase_initialization_step_type") != "PhaseInitialization" or
            result.get("transient_step_type") != "Transient" or
            result.get("bdf_output_time_policy") != "strict" or
            result.get("output_mode") != "tsteps" or
            result.get("stored_time_steps_policy") != 1):
            raise CampaignError("native phase-init/transient/stored-time setup readback is incomplete")
        if not (str(result.get("surface_tension_enabled", "")).lower() in {"on", "1", "true"}):
            raise CampaignError("native two-phase surface-tension force is not enabled")
        parameter_rows = result.get("parameter_values_and_units")
        expected_parameters = {
            "rhoGlue": (1200.0, "kg/m^3"), "muGlue": (1.0, "Pa*s"),
            "rhoGas": (1.2, "kg/m^3"), "muGas": (0.018, "Pa*s"),
            "sigma0": (0.03, "N/m"), "epsPF": (8e-6, "m"), "Rdrop": (500e-6, "m"),
        }
        if not isinstance(parameter_rows, Mapping):
            raise CampaignError("native static-shape readback omitted evaluated SI parameters and units")
        for name, (expected_value, expected_unit) in expected_parameters.items():
            row = parameter_rows.get(name)
            value = row.get("value_si") if isinstance(row, Mapping) else None
            if (not isinstance(row, Mapping) or row.get("unit") != expected_unit or
                isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(float(value)) or
                abs(float(value) - expected_value) > max(1e-14, abs(expected_value) * 1e-12)):
                raise CampaignError(f"native static-shape baseline parameter {name} differs from its frozen SI value")
        if result.get("study_run_calls_this_action") != 0:
            raise CampaignError("static-shape readback unexpectedly invoked study.run")
        StaticShapeManagedRunner._validate_no_stored_solution_data(
            result.get("solution_state_readback"))
        return dict(result)

    @staticmethod
    def _validate_no_stored_solution_data(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("status") != "NO_STORED_SOLUTION_DATA":
            raise CampaignError("native solver-sequence readback did not establish an empty solution state")
        if value.get("readback_method") != "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]":
            raise CampaignError("native solution-state evidence omitted its exact documented readback method")
        rows = value.get("solver_sequences")
        if not isinstance(rows, list):
            raise CampaignError("native solution-state evidence omitted the solver-sequence inventory")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise CampaignError("native solver-sequence inventory contains an invalid row")
            tag = row.get("solver_sequence_tag")
            dofs, stored = row.get("degrees_of_freedom"), row.get("stored_solution_count")
            if (not isinstance(tag, str) or not tag or tag in seen or
                    isinstance(dofs, bool) or type(dofs) is not int or dofs != 0 or
                    isinstance(stored, bool) or type(stored) is not int or stored != 0):
                raise CampaignError("native solver-sequence readback is nonempty, inconsistent, or ambiguous")
            seen.add(tag)
        return dict(value)

    def build_save_reopen(self, case_id: str) -> dict[str, Any]:
        if case_id not in STATIC_SHAPE_CASES:
            raise CampaignError("case_id must be exactly flat or step")
        before_study_runs = self._study_run_ledger()
        if before_study_runs:
            raise CampaignError("registered project already contains a study.run submission; setup-only route refuses it")
        target = self.workspace / "outputs" / f"static_shape_{case_id}.mph"
        target = _project_path(self.workspace, target, must_exist=False)

        parent = self.parent_binding
        parent, build_response, build = self._java_action(
            parent, source_role="fixture", entrypoint="W24StaticShapeFixture#run",
            arguments={"action": "build", "case_id": case_id})
        if (build.get("status") != "BUILT_NOT_SOLVED" or build.get("case_id") != case_id or
            build.get("study_run_calls") != 0 or build.get("phase_initialization_executed") is not False or
            build.get("native_acceptance") != "NOT_RUN"):
            raise CampaignError("static-shape Java builder did not return the exact unsolved/no-solve status")
        native_tag = build.get("model_tag")
        if not isinstance(native_tag, str) or not native_tag:
            raise CampaignError("static-shape builder did not return its actual new native model tag")

        binding = self._adopt(native_tag)
        binding, identity = self._inspect(binding)
        binding, readback_response, native_readback = self._java_action(
            binding, source_role="readback", entrypoint="W24StaticShapeReadback#run",
            arguments={"action": "readback"})
        native_readback = self._validate_shape_readback(native_readback, case_id)

        binding, save_response, save = self._java_action(
            binding, source_role="readback", entrypoint="W24StaticShapeReadback#run",
            arguments={"action": "save", "workspace_path": str(self.workspace),
                       "path": str(target)})
        if (save.get("status") != "SAVED_UNSOLVED_STATIC_SHAPE_MODEL" or
            save.get("model_tag") != native_tag or save.get("solution_data_present") is not False or
            save.get("study_run_calls_this_action") != 0):
            raise CampaignError("native static-shape save did not report an unsolved, no-run artifact")
        self._validate_no_stored_solution_data(save.get("solution_state_readback"))
        target = _project_path(self.workspace, target, must_exist=True)
        artifact_hash = _sha256(target)
        if (target.stat().st_size <= 0 or save.get("size_bytes") != target.stat().st_size or
            save.get("sha256") != artifact_hash):
            raise CampaignError("saved project MPH size/hash does not match native Java save readback")

        # Reopen only through the path-authorized production model_load route.
        loaded_response = self._dispatch("model_load", {"path": str(target)},
                                         session_id=self.parent_binding.session_id,
                                         worker_required=True)
        loaded = ManagedModelBinding.from_load_response(loaded_response, project_id=self.project_id)
        if loaded.session_id != self.parent_binding.session_id:
            raise CampaignError("managed shape model_load returned a different Worker session")
        persisted = self._verify_persisted_binding(loaded)
        loaded, reopened_identity = self._inspect(loaded)
        # The trusted Java readback returns a new managed revision even when
        # its Java body only inspects the model. Preserve the revision to which
        # the full configuration/getSize evidence actually applied instead of
        # later pretending that evidence was produced at the returned revision.
        reopened_readback_binding = loaded.as_record()
        loaded, reopened_response, reopened_raw = self._java_action(
            loaded, source_role="readback", entrypoint="W24StaticShapeReadback#run",
            arguments={"action": "readback"})
        reopened = self._validate_shape_readback(reopened_raw, case_id)
        comparable_before = {key: value for key, value in native_readback.items() if key != "model_tag"}
        comparable_after = {key: value for key, value in reopened.items() if key != "model_tag"}
        if comparable_before != comparable_after:
            raise CampaignError("native configuration readback changed after managed save/reopen")
        study_runs = self._study_run_ledger()
        if study_runs:
            raise CampaignError("static-shape setup unexpectedly submitted Study.run; preserve the campaign as failed")

        result = {
            "schema": "W24_STATIC_SHAPE_MANAGED_SETUP_V1",
            "status": "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE",
            "native_acceptance": "NOT_RUN",
            "project_id": self.project_id,
            "project_workspace": str(self.workspace),
            "case_id": case_id,
            "source_sha256": {
                "fixture": _sha256(self.source_paths["fixture"]),
                "readback": _sha256(self.source_paths["readback"]),
            },
            "parent_model_binding": parent.as_record(),
            "new_model_binding": binding.as_record(),
            "native_build_readback": dict(build),
            "pre_save_model_identity": identity,
            "pre_save_configuration_readback": native_readback,
            "save_readback": dict(save),
            "project_artifact": {"path": str(target), "size_bytes": target.stat().st_size,
                                 "sha256": artifact_hash},
            "reopened_configuration_readback_binding": reopened_readback_binding,
            "reopened_model_binding": loaded.as_record(),
            "persisted_reopened_project_binding": dict(persisted),
            "reopened_model_identity": reopened_identity,
            "reopened_configuration_readback": reopened,
            "readback_comparison": {"matches": True, "interpolation_used": False,
                                    "comparison": "exact JSON-native configuration values"},
            "study_run_submissions": study_runs,
            "study_run_submission_count": len(study_runs),
            "phase_initialization_executed": False,
            "raw_response_files": sorted(path.name for path in self.response_dir.glob("*.json")),
        }
        return result


def math_is_positive_finite(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number > 0.0 and number < float("inf")
