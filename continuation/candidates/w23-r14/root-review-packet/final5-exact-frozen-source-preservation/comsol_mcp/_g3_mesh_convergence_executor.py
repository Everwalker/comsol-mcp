"""Private in-lane executor for bounded, checkpoint-isolated mesh comparisons.

This is not a second public route.  ``_managed_backend`` installs its closures
into the per-call private G3 context, after the ordinary COMPUTE admission has
started on the existing serialized Worker lane.  Source-model preflight is
read-only; every mesh feature, study binding, mesh build, and Study.run happens
on a distinct durable branch copied from the pinned checkpoint.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal, model_ref_from_mapping
from ._g2_contract import NodePath
from ._g2_engine import _call, property_get


# COMSOL 6.4.0.293 API evidence for the exact data-binding calls below.
API_SOURCES = {
    "Refine.numrefine": {"doc": 3626, "chunk": 17148,
                         "sha256": "c91b23ba34df87758116d116af4e7be4c2425c4aa44180cb456bc3c89f880303"},
    "StudyFeature.mesh": {"doc": 7717, "chunk": 23092,
                          "sha256": "a9ffe9c3ac8a45d945eb51d23ab78277cc8970b14f5d5bddfd504147867ca674"},
    "Study.getSolverSequences": {"doc": 7716, "chunk": 23089,
                                 "sha256": "7ab71c87a119f01583dbf0441ec9b032e257c4125aa9994b9d83f72571236546"},
    "SolutionInfo.getSolverSequence": {"doc": 7709, "chunk": 23067,
                                       "sha256": "94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb"},
    "SolutionInfo.getISol": {"doc": 7709, "chunk": 23065,
                             "sha256": "94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb"},
    "SolverSequence.getMesh": {"doc": 7714, "chunk": 23081,
                               "sha256": "8fcefe8e2f2171858f49fe53ad6210d4231a6a28651766db67effc39c327a2c5"},
}


class MeshConvergenceExecutor:
    """Private callback pair installed for one outer mesh-convergence request."""

    def __init__(self, backend: Any, worker: Any, source_ref: Any, expected_revision: int,
                 execution: Mapping[str, Any], operation_id: str) -> None:
        self.backend = backend
        self.worker = worker
        self.source_ref = source_ref
        self.expected_revision = expected_revision
        self.execution = dict(execution)
        self.operation_id = str(operation_id)
        self.plan: dict[str, Any] | None = None
        self.budget: Any = None
        self.run_index = 0

    def preflight(self, *, worker: Any, model_tag: str, definition: Mapping[str, Any],
                  metric_refs: Sequence[Mapping[str, Any]], source_checkpoint_id: str,
                  level_count: int) -> dict[str, Any]:
        try:
            return self._preflight_checked(worker=worker, model_tag=model_tag, definition=definition,
                                           metric_refs=metric_refs, source_checkpoint_id=source_checkpoint_id,
                                           level_count=level_count)
        except PreWriteRefusal:
            raise
        except ExecutionContractError as exc:
            if exc.code == "EXECUTION_STATE_UNKNOWN":
                raise
            raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc

    def _preflight_checked(self, *, worker: Any, model_tag: str, definition: Mapping[str, Any],
                           metric_refs: Sequence[Mapping[str, Any]], source_checkpoint_id: str,
                           level_count: int) -> dict[str, Any]:
        if worker is not self.worker or model_tag != self.source_ref.model_tag:
            raise PreWriteRefusal("WORKER_BINDING_MISMATCH", "mesh convergence preflight is bound to another Worker or ModelRef")
        if self.plan is not None:
            raise PreWriteRefusal("INVALID_REQUEST", "mesh convergence preflight may run only once per outer operation")

        from . import _g2_checkpoint_ops as checkpoint_ops
        from . import _g3_results, _g3_w16
        from ._g3_w21 import ComputationBudget

        if ("Refine" not in _g3_w16.MESH_FEATURE_TYPE_SOURCES
                or "numrefine" not in _g3_w16.MESH_DOCUMENTED_PROPERTIES.get("Refine", frozenset())):
            raise PreWriteRefusal("API_UNSUPPORTED", "verified Refine.numrefine integer feature contract is unavailable")

        project_id = self.execution.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise PreWriteRefusal("PROJECT_IDENTITY_REQUIRED", "mesh convergence requires the admitted project id")
        state = self.backend.service.ledger._state_for(self.source_ref)
        if (state.revision != self.expected_revision or not state.active_operation_id
                or self.backend.model_project_binding(self.source_ref.as_dict()) != {
                    "attribution": "PROJECT_BOUND", "project_id": project_id}):
            raise PreWriteRefusal("REVISION_CONFLICT", "source ModelRef, revision, active ticket, or project binding changed before preflight")

        checkpoint_record, checkpoint_path = checkpoint_ops.checkpoint_input(
            self.backend, self.source_ref, source_checkpoint_id, project_id)
        canonical_mesh, mesh_node, component_tag, mesh_tag = _g3_w16._mesh_sequence_context(
            worker, model_tag, definition["mesh_path"], label="definition.mesh_path")
        geometry_tag = _call(mesh_node, "geom")
        if not isinstance(geometry_tag, str) or not geometry_tag:
            raise PreWriteRefusal("API_UNSUPPORTED", "selected mesh has no typed geometry association")
        component = _g3_w16._require_component(worker, model_tag, component_tag)
        geometry_tags = _g3_w16.tag_list(_call(component, "geom"))
        if geometry_tag not in geometry_tags:
            raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "selected mesh geometry is not present in its owning component")

        canonical_study, study_node, study_tag = _g3_w16._study_context(
            worker, model_tag, definition["study_path"], label="definition.study_path")
        step_rows = _g3_w16._feature_rows(study_node)
        if not step_rows:
            raise PreWriteRefusal("INVALID_REQUEST", "selected Study has no steps to compute")
        unknown_types = [row.get("type_id") for row in step_rows
                         if not isinstance(row.get("type_id"), str)
                         or row.get("type_id") not in _g3_w16.STUDY_STEP_TYPE_IDS]
        if unknown_types:
            raise PreWriteRefusal("API_UNSUPPORTED", "selected Study contains an unclassified StudyFeature type",
                                  details={"unclassified_types": unknown_types})
        defining = [row for row in step_rows if row.get("type_id") in _g3_w16.STUDY_DEFINING_STEPS]
        if not defining:
            raise PreWriteRefusal("API_UNSUPPORTED", "selected Study has no verified equation-defining step")
        step_meshes = []
        for row in defining:
            node = _call(_call(study_node, "feature"), "get", row["tag"])
            current = _call(node, "mesh", geometry_tag)
            if not isinstance(current, str) or not current:
                raise PreWriteRefusal("API_UNSUPPORTED", "StudyFeature.mesh(geometry) did not return a typed mesh tag",
                                      details={"feature": row["tag"], "geometry": geometry_tag})
            step_meshes.append({"feature": row["tag"], "type_id": row["type_id"],
                                "geometry": geometry_tag, "source_mesh_readback": current})

        source_feature_tags = _g3_w16.tag_list(_call(mesh_node, "feature"))
        refine_tags: dict[str, str] = {}
        for row in definition["levels"]:
            candidate = "mc_" + hashlib.sha256(
                (self.operation_id + "\0" + row["case_id"]).encode("utf-8")).hexdigest()[:20]
            if candidate in source_feature_tags or candidate in refine_tags.values():
                raise PreWriteRefusal("TYPE_CONFLICT", "deterministic Refine tag collides with an existing mesh feature")
            refine_tags[row["case_id"]] = candidate

        dataset_refs: dict[str, dict[str, Any]] = {}
        metric_hashes: list[str] = []
        for metric in metric_refs:
            metric_sha = metric.get("definition_sha256")
            if (not isinstance(metric_sha, str) or len(metric_sha) != 64
                    or any(char not in "0123456789abcdef" for char in metric_sha)
                    or metric_sha in dataset_refs):
                raise PreWriteRefusal("METRIC_REGISTRY_UNAVAILABLE", "convergence metric refs require unique lowercase SHA-256 identities")
            metric_definition = metric.get("definition")
            solution = metric_definition.get("solution") if isinstance(metric_definition, Mapping) else None
            dataset_tag = solution.get("dataset") if isinstance(solution, Mapping) else None
            if not isinstance(dataset_tag, str) or not dataset_tag:
                raise PreWriteRefusal("SOLUTION_AXIS_METADATA_UNAVAILABLE", "registered metric has no actual Solution dataset selector")
            dataset_path = {"segments": [{"collection": "dataset", "tag": dataset_tag}]}
            _canonical, dataset_node = _g3_w16.resolve_path(worker, model_tag, dataset_path,
                                                              label="registered metric Solution dataset")
            dataset_type = _call(dataset_node, "getType")
            if dataset_type != "Solution":
                raise PreWriteRefusal("SOLUTION_AXIS_METADATA_UNAVAILABLE", "registered metric dataset is not a Solution dataset",
                                      details={"dataset": dataset_tag, "actual_type": dataset_type})
            dataset_refs[metric_sha] = {
                "metric_id": metric.get("metric_id"), "definition_version": metric.get("version"),
                "dataset": dataset_tag, "path": NodePath.from_wire(dataset_path).as_dict(),
                "requested_solution": solution.get("solution"),
                "requested_outer": solution.get("outer"), "requested_inner": solution.get("inner"),
            }
            metric_hashes.append(metric_sha)

        self.plan = {
            "checkpoint_id": source_checkpoint_id, "checkpoint_record": checkpoint_record,
            "checkpoint_path": checkpoint_path, "mesh_path": canonical_mesh,
            "mesh_component": component_tag, "mesh_tag": mesh_tag,
            "geometry_tag": geometry_tag, "study_path": canonical_study,
            "study_tag": study_tag, "step_bindings": step_meshes,
            "refine_tags": refine_tags, "datasets": dataset_refs, "metric_hashes": metric_hashes,
            "source_model_ref": self.source_ref.as_dict(),
            "source_revision": self.expected_revision,
        }
        self.budget = ComputationBudget(max_cases=level_count, max_wall_time_s=300.0,
                                       max_consecutive_failures=1)
        return {
            "status": "PREFLIGHTED", "checkpoint_id": source_checkpoint_id,
            "mesh": {"path": canonical_mesh, "component": component_tag, "tag": mesh_tag,
                     "geometry": geometry_tag},
            "study": {"path": canonical_study, "tag": study_tag,
                      "defining_step_count": len(step_meshes),
                      "source_step_meshes": step_meshes},
            "metric_datasets": dataset_refs,
            "metric_hashes": metric_hashes,
            "api_sources": API_SOURCES,
            "budget": {"max_study_runs": level_count, "max_wall_time_s": 300.0,
                       "scope": "one budget shared by every case in this outer operation"},
            "source_model_ref": self.source_ref.as_dict(),
            "source_revision": self.expected_revision,
        }

    def status(self) -> dict[str, Any]:
        return self.budget.status() if self.budget is not None else {
            "cases_evaluated": 0, "max_cases": 0, "exhausted": False,
            "status": "NOT_STARTED"}

    def execute_case(self, *, worker: Any, source_model_ref: Mapping[str, Any],
                     source_revision: int, source_checkpoint_id: str, mesh_path: Mapping[str, Any],
                     study_path: Mapping[str, Any], case_id: str, refinement: Mapping[str, Any],
                     metric_refs: Sequence[Mapping[str, Any]], metric_sha256: Sequence[str],
                     operation_id: str) -> dict[str, Any]:
        if self.plan is None or self.budget is None:
            raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "mesh convergence must pass native preflight before executing cases")
        if (worker is not self.worker or dict(source_model_ref) != self.plan["source_model_ref"]
                or source_revision != self.expected_revision or source_checkpoint_id != self.plan["checkpoint_id"]
                or NodePath.from_wire(mesh_path).as_dict() != self.plan["mesh_path"]
                or NodePath.from_wire(study_path).as_dict() != self.plan["study_path"]
                or operation_id != self.operation_id
                or list(metric_sha256) != self.plan["metric_hashes"]
                or [metric.get("definition_sha256") for metric in metric_refs] != self.plan["metric_hashes"]):
            if not self.plan.get("branch_started"):
                raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "case request differs from its preflight plan",
                                      details={"case_id": case_id, "phase": "case_admission"})
            raise self._typed_case_error("MODEL_IDENTITY_MISMATCH", "case request differs from its preflight plan", case_id,
                                         phase="case_admission")
        if not self.budget.can_evaluate():
            if not self.plan.get("branch_started"):
                raise PreWriteRefusal("COMPUTATION_BUDGET_EXCEEDED", "shared convergence budget is exhausted before the first owned branch",
                                      details={"budget": self.status(), "case_id": case_id})
            raise self._typed_case_error("COMPUTATION_BUDGET_EXCEEDED", "shared convergence budget is exhausted before this level",
                                         case_id, phase="budget", safe_retry=False)
        from . import _g2_checkpoint_ops as checkpoint_ops, _g3_results
        from ._g3_ops import DISPATCH
        from ._observation_store import observation_context

        branch = None
        branch_ref = None
        branch_revision = None
        step_evidence: list[dict[str, Any]] = []
        raw: dict[str, Any] = {}
        run_attempted = False
        current_phase = "checkpoint_branch"
        case_evidence: dict[str, Any] = {
            "case_id": case_id, "status": "UNKNOWN", "source_model_ref": self.plan["source_model_ref"],
            "source_revision": self.expected_revision, "source_checkpoint_id": self.plan["checkpoint_id"],
            "requested_refinement": dict(refinement), "run_budget": self.status(),
            "raw": raw,
        }
        try:
            try:
                self._assert_source_identity()
            except ExecutionContractError as exc:
                if self.plan.get("branch_started"):
                    raise
                # The exact source identity is rechecked immediately before the
                # first branch write.  A mismatch here is a clean revision
                # refusal: no owned branch or Study.run was dispatched, so do
                # not poison the lane with a post-dispatch UNKNOWN code.
                raise PreWriteRefusal("REVISION_CONFLICT",
                                      "source ModelRef/revision/project binding changed before branch creation",
                                      details={"phase": "source_identity", **dict(exc.details)}) from exc
            self.plan["branch_started"] = True
            proof: dict[str, Any] = {}
            branch_reply = checkpoint_ops.run_owned(
                self.backend, "checkpoint.branch", self.source_ref,
                {"checkpoint_id": self.plan["checkpoint_id"], "label": f"mesh-convergence-{case_id}"},
                self.execution, self.operation_id, self.plan["checkpoint_record"],
                self.plan["checkpoint_path"], proof)
            branch = branch_reply.get("data") if isinstance(branch_reply, Mapping) else None
            if not isinstance(branch_reply, Mapping) or branch_reply.get("success") is not True or not isinstance(branch, Mapping):
                case_evidence["branch_creation"] = self._safe_branch_receipt(branch)
                raise self._typed_case_error("EXECUTION_STATE_UNKNOWN", "checkpoint branch creation did not verify",
                                             case_id, phase=current_phase, evidence=case_evidence)
            branch_ref_mapping = branch.get("branch_model_ref")
            if not isinstance(branch_ref_mapping, Mapping):
                case_evidence["branch_creation"] = self._safe_branch_receipt(branch)
                raise self._typed_case_error("EXECUTION_STATE_UNKNOWN", "owned branch has no exact ModelRef",
                                             case_id, phase=current_phase, evidence=case_evidence)
            branch_ref = model_ref_from_mapping(dict(branch_ref_mapping))
            branch_state = self.backend.service.ledger._state_for(branch_ref)
            branch_revision = branch_state.revision
            case_evidence.update({"branch_model_ref": branch_ref.as_dict(), "branch_revision": branch_revision,
                                  "checkpoint_id": branch.get("checkpoint_id"),
                                  "branch_creation": self._safe_branch_receipt(branch)})
            self._verify_branch_receipt(branch, case_id)

            current_phase = "create_refine"
            refine_tag = self.plan["refine_tags"][case_id]
            create_result = self._ticketed_call(
                DISPATCH["mesh.feature_create"], branch_ref, "mesh.feature_create",
                {"parent": self.plan["mesh_path"], "tag": refine_tag, "type_id": "Refine",
                 "properties": {"numrefine": refinement["nrefine"]}},
                effect="project_write", request_suffix=f"{case_id}:refine")
            raw["refine_create"] = create_result
            self._require_native_applied(create_result, "Refine feature creation")
            feature_path = {"segments": self.plan["mesh_path"]["segments"] + [
                {"collection": "feature", "tag": refine_tag}]}
            property_result = property_get(self.worker, branch_ref.model_tag, feature_path, ["numrefine"])
            property_row = property_result.get("properties", [{}])[0]
            typed_value = property_row.get("value") if isinstance(property_row, Mapping) else None
            if (not isinstance(typed_value, Mapping) or typed_value.get("kind") != "integer"
                    or typed_value.get("shape") != [] or typed_value.get("data") != refinement["nrefine"]):
                raise self._typed_case_error("READBACK_MISMATCH", "Refine.numrefine did not read back the requested integer",
                                             case_id, phase=current_phase,
                                             evidence={"requested": refinement["nrefine"], "readback": property_result})
            raw["refine_property_readback"] = property_result

            current_phase = "bind_study_mesh"
            step_evidence = self._bind_study_mesh(branch_ref, case_id)
            case_evidence["study_mesh_bindings"] = step_evidence

            current_phase = "build_mesh"
            mesh_build = self._ticketed_call(DISPATCH["mesh.build"], branch_ref, "mesh.build",
                                             {"path": self.plan["mesh_path"]}, effect="compute",
                                             request_suffix=f"{case_id}:mesh-build")
            raw["mesh_build"] = mesh_build
            self._require_native_applied(mesh_build, "mesh build")
            if refine_tag not in mesh_build.get("built_feature_range", []):
                raise self._typed_case_error("READBACK_MISMATCH", "mesh build did not report the selected Refine feature in its built range",
                                             case_id, phase=current_phase,
                                             evidence={"refine_tag": refine_tag, "mesh_build": mesh_build})
            mesh_stats = DISPATCH["mesh.statistics"](
                self.worker, branch_ref.model_tag, {"path": self.plan["mesh_path"]})
            if (mesh_stats.get("is_complete") is not True or mesh_stats.get("is_empty") is not False
                    or type(mesh_stats.get("element_count")) is not int or mesh_stats["element_count"] <= 0):
                raise self._typed_case_error("EXECUTION_STATE_UNKNOWN", "built mesh statistics are incomplete or empty",
                                             case_id, phase=current_phase, evidence={"mesh_statistics": mesh_stats})
            raw["mesh_statistics"] = mesh_stats

            current_phase = "study_run"
            def mark_study_run_attempted() -> None:
                nonlocal run_attempted
                run_attempted = True

            study_run = self._ticketed_call(DISPATCH["study.run"], branch_ref, "study.run",
                                            {"study": self.plan["study_path"]}, effect="compute",
                                            request_suffix=f"{case_id}:study-run",
                                            on_dispatch=mark_study_run_attempted)
            raw["study_run"] = study_run
            self._require_native_applied(study_run, "Study.run")

            current_phase = "solution_binding"
            observations: dict[str, Any] = {}
            tuple_evidence: dict[str, Any] = {}
            branch_revision = self.backend.service.ledger._state_for(branch_ref).revision
            for metric_ref in metric_refs:
                metric_sha = str(metric_ref["definition_sha256"])
                dataset = self.plan["datasets"][metric_sha]
                axes = _g3_results.dataset_solution_indices(
                    self.worker, branch_ref.model_tag, {"path": dataset["path"]})
                solution_tuple = self._select_solution_tuple(metric_ref, dataset, axes, branch_ref,
                                                             case_id=case_id)
                actual_binding = self._verify_solver_and_mesh_binding(branch_ref, solution_tuple, axes)
                tuple_evidence[metric_sha] = {**solution_tuple, "actual_binding": actual_binding}

                current_phase = "metric_evaluation"
                before_revision = self.backend.service.ledger._state_for(branch_ref).revision
                if before_revision != branch_revision:
                    raise self._typed_case_error("REVISION_CONFLICT", "branch revision changed before metric evaluation",
                                                 case_id, phase=current_phase,
                                                 evidence={"expected": branch_revision, "actual": before_revision})
                # Metric evaluation is a bounded ephemeral result read with its
                # own verified cleanup.  Calling it directly reuses this outer
                # Worker lane and pins ObservationRef to the unchanged branch
                # revision; it does not create a nested queue/ticket.
                with observation_context(self.backend.store, branch_ref.as_dict(), branch_revision,
                                         self.operation_id, project_id=self.execution["project_id"]):
                    metric_result = DISPATCH["metric.evaluate"](
                        self.worker, branch_ref.model_tag,
                    {"metric_ids": [metric_ref["metric_id"]],
                         "solution": {"dataset": dataset["dataset"],
                                      "solution": solution_tuple["solution"],
                                      "outer": solution_tuple["outer"],
                                      "inner": solution_tuple["inner"]}})
                raw.setdefault("metric_evaluations", {})[metric_sha] = metric_result
                item = next((row for row in metric_result.get("items", [])
                             if isinstance(row, Mapping) and row.get("definition_sha256") == metric_sha), None)
                if item is None or len(metric_result.get("items", [])) != 1:
                    raise self._typed_case_error("UNVERIFIED_OBSERVATION", "metric evaluator returned no single SHA-bound item",
                                                 case_id, phase=current_phase, evidence={"metric_result": metric_result})
                value_row = next((row for row in item.get("values", [])
                                  if isinstance(row, Mapping)
                                  and row.get("outer") == solution_tuple["outer"]
                                  and row.get("inner") == solution_tuple["inner"]
                                  and row.get("solnum") == solution_tuple["solnum"]), None)
                if (value_row is None or len(item.get("values", [])) != 1
                        or not isinstance(item.get("observation_ref"), Mapping)):
                    raise self._typed_case_error("UNVERIFIED_OBSERVATION", "metric result did not preserve exactly the selected real solution tuple",
                                                 case_id, phase=current_phase, evidence={"metric_item": item})
                value = value_row.get("value")
                if isinstance(value, Mapping) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise self._typed_case_error("UNVERIFIED_OBSERVATION", "convergence metric must return one finite scalar for the selected tuple",
                                                 case_id, phase=current_phase, evidence={"metric_value": value})
                if self.backend.service.ledger._state_for(branch_ref).revision != branch_revision:
                    raise self._typed_case_error("REVISION_CONFLICT", "metric evaluation changed the branch revision",
                                                 case_id, phase=current_phase)
                observations[metric_sha] = {
                    "metric_definition_sha256": metric_sha,
                    "observation_ref": dict(item["observation_ref"]),
                    "model_ref": branch_ref.as_dict(), "model_revision": branch_revision,
                    "producer_operation_id": self.operation_id,
                    "value": {"value": float(value), "unit": item.get("unit")},
                    "selected_solution": dict(solution_tuple),
                }

            self._assert_source_identity()
            self.budget.record_case(True)
            case_evidence.update({"status": "SUCCEEDED", "branch_model_ref": branch_ref.as_dict(),
                                  "branch_revision": branch_revision,
                                  "mesh_readback": {"is_complete": True, "is_empty": False,
                                                     "element_count": mesh_stats["element_count"],
                                                     "geometry": self.plan["geometry_tag"],
                                                     "mesh_tag": self.plan["mesh_tag"],
                                                     "refine_tag": refine_tag,
                                                     "numrefine": refinement["nrefine"],
                                                     "built_feature_range": mesh_build.get("built_feature_range")},
                                  "study_readback": {"status": "SUCCEEDED", "solution": next(iter(tuple_evidence.values()))["solution"],
                                                     "study": self.plan["study_tag"],
                                                     "solver_sequences": study_run.get("solver_sequences"),
                                                     "selected_solution_tuples": tuple_evidence,
                                                     "step_mesh_bindings": step_evidence},
                                  "observations": observations, "raw": raw,
                                  "run_budget": self.status()})
            return case_evidence
        except Exception as exc:
            if isinstance(exc, PreWriteRefusal) and not self.plan.get("branch_started"):
                raise
            if run_attempted:
                self.budget.record_case(False)
            error_details = getattr(exc, "details", {})
            if isinstance(error_details, Mapping) and isinstance(error_details.get("result"), Mapping):
                failed_result = error_details["result"]
                failed_operation = error_details.get("operation")
                if isinstance(failed_operation, str):
                    raw.setdefault("failed_operation", {"operation": failed_operation,
                                                         "result": dict(failed_result)})
                    if failed_operation == "study.run" and isinstance(failed_result.get("data"), Mapping):
                        raw.setdefault("study_run", dict(failed_result["data"]))
            if branch_ref is not None:
                try:
                    self.backend._freeze_g2_model(branch_ref, "mesh convergence case did not fully verify")
                except Exception as freeze_exc:
                    case_evidence["freeze_error"] = {"type": type(freeze_exc).__name__,
                                                      "message": str(freeze_exc)[:400]}
                try:
                    branch_revision = self.backend.service.ledger._state_for(branch_ref).revision
                except Exception:
                    pass
            case_evidence.update({"status": "UNKNOWN", "phase": current_phase,
                                  "branch_model_ref": branch_ref.as_dict() if branch_ref is not None else None,
                                  "branch_revision": branch_revision, "raw": raw,
                                  "study_mesh_bindings": step_evidence,
                                  "run_budget": self.status(),
                                  "failure": {"type": type(exc).__name__, "code": getattr(exc, "code", None),
                                              "message": str(exc)[:500],
                                              "details": dict(getattr(exc, "details", {}))
                                              if isinstance(getattr(exc, "details", {}), Mapping) else {}}})
            raise self._typed_case_error(getattr(exc, "code", "EXECUTION_STATE_UNKNOWN"),
                                         "mesh convergence case stopped; durable owned branch retained",
                                         case_id, phase=current_phase, evidence=case_evidence,
                                         cause=exc, safe_retry=False)

    def _ticketed_call(self, function: Any, branch_ref: Any, operation: str,
                       arguments: Mapping[str, Any], *, effect: str,
                       request_suffix: str, on_dispatch: Any = None) -> dict[str, Any]:
        state = self.backend.service.ledger._state_for(branch_ref)
        if state.dirty or state.active_operation_id:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "owned convergence branch is dirty or already active",
                                         stage="post_dispatch")
        request_id = f"{self.operation_id}:mesh-convergence:{request_suffix}"

        def callback(_args: Mapping[str, Any]) -> dict[str, Any]:
            if callable(on_dispatch):
                on_dispatch()
            data = function(self.worker, branch_ref.model_tag, dict(arguments))
            success = self._native_applied(data)
            return {"success": success, "data": data,
                    "error": None if success else {"code": "EXECUTION_STATE_UNKNOWN",
                                                   "message": f"{operation} did not verify",
                                                   "safe_retry": False}}

        result = self.backend.service.execute_legacy(
            operation.replace(".", "_"), callback, arguments,
            model_ref=branch_ref, expected_revision=state.revision,
            request_id=request_id, session_id=branch_ref.session_id, effect=effect)
        data = result.get("data") if isinstance(result, Mapping) else None
        if (result.get("success") is not True or not isinstance(data, Mapping)
                or data.get("execution_state_unknown") is True):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{operation} ticket did not reach a verified terminal state",
                                         stage="post_dispatch", details={"operation": operation,
                                                                          "result": dict(result) if isinstance(result, Mapping) else None})
        return dict(data)

    @staticmethod
    def _native_applied(data: Any) -> bool:
        if not isinstance(data, Mapping) or data.get("execution_state_unknown") is True:
            return False
        status = data.get("status")
        if isinstance(status, Mapping):
            return status.get("ok") is True and status.get("execution_state_unknown") is not True
        return status == "APPLIED" and data.get("ok", True) is True

    def _require_native_applied(self, data: Mapping[str, Any], label: str) -> None:
        if not self._native_applied(data):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{label} failed or lacks complete native readback",
                                         stage="post_dispatch", details={"result": dict(data)})

    def _bind_study_mesh(self, branch_ref: Any, case_id: str) -> list[dict[str, Any]]:
        from . import _g3_w16

        model = _g3_w16.bound_model(self.worker, branch_ref.model_tag)
        study = _call(model, "study", self.plan["study_tag"])
        features = _call(study, "feature")
        rows: list[dict[str, Any]] = []
        for feature in self.plan["step_bindings"]:
            step = _call(features, "get", feature["feature"])
            request_suffix = f"{case_id}:bind:{feature['feature']}"

            def bind_and_readback(_worker: Any, _model_tag: str, _arguments: Mapping[str, Any]) -> dict[str, Any]:
                before = _call(step, "mesh", self.plan["geometry_tag"])
                _call(step, "mesh", self.plan["geometry_tag"], self.plan["mesh_tag"])
                after = _call(step, "mesh", self.plan["geometry_tag"])
                return {"status": "APPLIED", "ok": after == self.plan["mesh_tag"],
                        "feature": feature["feature"], "geometry": self.plan["geometry_tag"],
                        "source_mesh": before, "requested_mesh": self.plan["mesh_tag"],
                        "mesh_readback": after}

            data = self._ticketed_call(bind_and_readback, branch_ref, "api.invoke",
                                       {"feature": feature["feature"], "geometry": self.plan["geometry_tag"],
                                        "mesh": self.plan["mesh_tag"]}, effect="project_write",
                                       request_suffix=request_suffix)
            if data.get("mesh_readback") != self.plan["mesh_tag"]:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "StudyFeature.mesh(geometry) did not read back the selected mesh",
                                             stage="post_dispatch", details={"binding": data})
            rows.append(data)
        return rows

    def _select_solution_tuple(self, metric_ref: Mapping[str, Any], dataset: Mapping[str, Any],
                               axes: Mapping[str, Any], branch_ref: Any, *, case_id: str) -> dict[str, Any]:
        if axes.get("binding_complete") is not True:
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "SolutionInfo did not bind complete actual solution tuples",
                                         case_id, phase="solution_binding", evidence={"axes": dict(axes)})
        solution = axes.get("solution")
        study = axes.get("study")
        if not isinstance(solution, str) or not solution or study != self.plan["study_tag"]:
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "metric dataset solution is not bound to the selected Study",
                                         case_id, phase="solution_binding",
                                         evidence={"solution": solution, "study": study,
                                                   "expected_study": self.plan["study_tag"]})
        requested_solution = dataset.get("requested_solution")
        if requested_solution is not None and requested_solution != solution:
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "metric's explicit solver selector differs from the Study result",
                                         case_id, phase="solution_binding",
                                         evidence={"requested_solution": requested_solution, "actual_solution": solution})
        binding = axes.get("dataset_binding")
        if not isinstance(binding, Mapping) or binding.get("solution") != solution:
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "dataset binding readback does not agree with SolutionInfo solution tag",
                                         case_id, phase="solution_binding", evidence={"dataset_binding": binding})
        pairs = axes.get("solnum_pairs")
        if not isinstance(pairs, list) or not pairs or any(not isinstance(pair, Mapping) for pair in pairs):
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "SolutionInfo tuple mapping is missing",
                                         case_id, phase="solution_binding", evidence={"pairs": pairs})
        selected = list(pairs)
        for selector_name in ("outer", "inner"):
            selector = dataset.get("requested_" + selector_name)
            if selector == "all" or selector is None:
                continue
            allowed = set(selector if isinstance(selector, list) else [selector])
            selected = [row for row in selected if row.get(selector_name) in allowed]
        if len(selected) != 1:
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE",
                                         "registered metric selectors do not identify one actual stored (outer, inner, solnum) tuple",
                                         case_id, phase="solution_binding",
                                         evidence={"requested_outer": dataset.get("requested_outer"),
                                                   "requested_inner": dataset.get("requested_inner"),
                                                   "available_pairs": pairs,
                                                   "selected_pairs": selected})
        pair = selected[0]
        if (type(pair.get("outer")) is not int or pair["outer"] < 1
                or type(pair.get("inner")) is not int or pair["inner"] < 1
                or type(pair.get("solnum")) is not int or pair["solnum"] < 1):
            raise self._typed_case_error("SOLUTION_AXIS_METADATA_UNAVAILABLE", "selected SolutionInfo tuple has invalid one-based indices",
                                         case_id, phase="solution_binding", evidence={"selected_pair": pair})
        return {"dataset": dataset["dataset"], "solution": solution,
                "study": study, "outer": pair["outer"], "inner": pair["inner"],
                "solnum": pair["solnum"]}

    def _verify_solver_and_mesh_binding(self, branch_ref: Any, selected: Mapping[str, Any],
                                        axes: Mapping[str, Any]) -> dict[str, Any]:
        from . import _g3_w16

        model = _g3_w16.bound_model(self.worker, branch_ref.model_tag)
        study = _call(model, "study", self.plan["study_tag"])
        solver_tags = _call(study, "getSolverSequences", "SolverSequence")
        if not isinstance(solver_tags, (list, tuple)) or not solver_tags or any(not isinstance(tag, str) or not tag for tag in solver_tags):
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "Study.getSolverSequences returned no typed solver tags",
                                         stage="post_dispatch")
        solver_tags = list(solver_tags)
        sol_node = _call(_call(model, "sol"), "get", selected["solution"])
        actual_study = _call(sol_node, "study")
        info = _call(sol_node, "getSolutioninfo")
        solver_sequence = _call(info, "getSolverSequence", selected["outer"])
        if actual_study != self.plan["study_tag"] or not isinstance(solver_sequence, str) or solver_sequence not in solver_tags:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "solution is not associated with a solver sequence of the selected Study",
                                         stage="post_dispatch", details={"solution": selected["solution"],
                                                                          "actual_study": actual_study,
                                                                          "solver_sequence": solver_sequence,
                                                                          "study_solver_sequences": solver_tags})
        indices = _call(info, "getISol", selected["outer"], selected["inner"])
        if (not isinstance(indices, (list, tuple)) or len(indices) != 2
                or type(indices[0]) is not int or indices[0] < 0
                or type(indices[1]) is not int or indices[1] < 0):
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "SolutionInfo.getISol did not return the exact stored solution indices",
                                         stage="post_dispatch", details={"indices": indices})
        actual_mesh = _call(sol_node, "getMesh", self.plan["geometry_tag"], indices[0])
        if actual_mesh != self.plan["mesh_tag"]:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "stored solution tuple is bound to a different mesh",
                                         stage="post_dispatch", details={"expected_mesh": self.plan["mesh_tag"],
                                                                          "actual_mesh": actual_mesh,
                                                                          "geometry": self.plan["geometry_tag"],
                                                                          "solution_indices": list(indices)})
        if axes.get("study") != self.plan["study_tag"]:
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "dataset solution-index readback changed Study association",
                                         stage="post_dispatch")
        return {"study_solver_sequences": solver_tags, "solution_study": actual_study,
                "solution_info_solver_sequence": solver_sequence,
                "solution_indices": list(indices), "solution_mesh": actual_mesh,
                "api_sources": API_SOURCES,
                "evidence": ["Study.getSolverSequences('SolverSequence')",
                             "SolverSequence.study()", "SolutionInfo.getSolverSequence(outer)",
                             "SolutionInfo.getISol(outer, inner)",
                             "SolverSequence.getMesh(geometry, objectIndex)"]}

    def _assert_source_identity(self) -> None:
        state = self.backend.service.ledger._state_for(self.source_ref)
        project_id = self.execution.get("project_id")
        binding = self.backend.model_project_binding(self.source_ref.as_dict())
        if (state.ref != self.source_ref or state.revision != self.expected_revision
                or not state.active_operation_id or binding != {"attribution": "PROJECT_BOUND",
                                                                 "project_id": project_id}):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "source ModelRef/revision/project binding changed during convergence",
                                         stage="post_dispatch", details={"expected_model_ref": self.source_ref.as_dict(),
                                                                          "expected_revision": self.expected_revision,
                                                                          "actual_revision": state.revision,
                                                                          "project_binding": binding,
                                                                          "safe_retry": False})

    @staticmethod
    def _safe_branch_receipt(branch: Any) -> dict[str, Any] | None:
        if not isinstance(branch, Mapping):
            return None
        safe_keys = ("status", "complete", "checkpoint_id", "branch_model_ref", "branch_revision",
                     "source_pointer_restored", "source_unchanged_within_scope", "cleanup", "evidence", "persistence")
        result = {key: branch[key] for key in safe_keys if key in branch}
        # The private model file path is intentionally omitted from outward receipts.
        if isinstance(result.get("evidence"), Mapping):
            result["evidence"] = {key: value for key, value in result["evidence"].items()
                                  if key not in {"owned_path"}}
        if isinstance(result.get("cleanup"), Mapping):
            result["cleanup"] = {key: value for key, value in result["cleanup"].items()
                                 if key not in {"retained_path"}}
        return result

    def _verify_branch_receipt(self, branch: Mapping[str, Any], case_id: str) -> None:
        expected_sha = self.plan["checkpoint_record"].get("sha256")
        binding = branch.get("admission_source_identity")
        if (branch.get("status") != "SUCCEEDED" or branch.get("complete") is not True
                or branch.get("checkpoint_id") != self.plan["checkpoint_id"]
                or branch.get("source_pointer_restored") is not True
                or branch.get("source_unchanged_within_scope") is not True
                or not isinstance(expected_sha, str) or len(expected_sha) != 64
                or not isinstance(binding, Mapping)
                or binding.get("model_ref") != self.plan["source_model_ref"]
                or binding.get("revision") != self.expected_revision
                or not isinstance(branch.get("persistence"), Mapping)
                or branch["persistence"].get("verified") is not True):
            raise self._typed_case_error("EXECUTION_STATE_UNKNOWN", "owned checkpoint branch receipt lacks exact source/persistence proof",
                                         case_id, phase="checkpoint_branch",
                                         evidence={"branch_creation": self._safe_branch_receipt(branch)})
        evidence = branch.get("evidence")
        if (not isinstance(evidence, Mapping)
                or evidence.get("checkpoint_after_sha256") != expected_sha
                or evidence.get("clone_after_sha256") != expected_sha):
            raise self._typed_case_error("EXECUTION_STATE_UNKNOWN", "owned branch or source checkpoint SHA readback differs",
                                         case_id, phase="checkpoint_branch",
                                         evidence={"branch_creation": self._safe_branch_receipt(branch),
                                                   "expected_checkpoint_sha256": expected_sha})

    def _typed_case_error(self, code: str, message: str, case_id: str, *, phase: str,
                          evidence: Mapping[str, Any] | None = None,
                          cause: BaseException | None = None, safe_retry: bool = False) -> ExecutionContractError:
        current = dict(evidence or {})
        current.setdefault("case_id", case_id)
        current.setdefault("phase", phase)
        current.setdefault("run_budget", self.status())
        error = ExecutionContractError(code, message, safe_retry=safe_retry,
                                       stage="post_dispatch" if self.plan is not None and self.plan.get("branch_started") else "validation",
                                       details={"case_evidence": current, "safe_retry": safe_retry})
        if cause is not None:
            error.with_cause(cause)
        return error


def build_mesh_convergence_callbacks(backend: Any, worker: Any, source_ref: Any,
                                     expected_revision: int, execution: Mapping[str, Any],
                                     operation_id: str) -> MeshConvergenceExecutor:
    """Create request-private preflight/execution callbacks for one G3 ticket."""
    return MeshConvergenceExecutor(backend, worker, source_ref, expected_revision,
                                   execution, operation_id)
