"""Software-only tests for the private owned-branch mesh convergence runner."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from comsol_mcp import _g2_checkpoint_ops, _g3_mesh_convergence_executor as convergence
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal, model_ref_from_mapping
from comsol_mcp._g3_w21 import ComputationBudget


SOURCE_REF = {"schema_version": 1, "session_id": "session", "server_instance_id": "server",
              "model_tag": "source", "generation": 1}
METRIC_SHA = "a" * 64
MESH_PATH = {"segments": [{"collection": "component", "tag": "comp1"},
                           {"collection": "mesh", "tag": "mesh1"}]}
STUDY_PATH = {"segments": [{"collection": "study", "tag": "std1"}]}
DATASET_PATH = {"segments": [{"collection": "dataset", "tag": "dset1"}]}


class _State:
    def __init__(self, ref, revision=0, active=None):
        self.ref = ref
        self.revision = revision
        self.dirty = False
        self.active_operation_id = active


class _Ledger:
    def __init__(self):
        self.states = {}

    def _state_for(self, ref):
        tag = ref.model_tag if hasattr(ref, "model_tag") else ref["model_tag"]
        state = self.states[tag]
        assert state.ref == ref
        return state


class _Service:
    def __init__(self):
        self.ledger = _Ledger()
        self.calls = []

    def execute_legacy(self, tool_name, callback, arguments, *, model_ref, expected_revision,
                       request_id, session_id, effect):
        state = self.ledger._state_for(model_ref)
        assert state.revision == expected_revision
        assert not state.dirty and not state.active_operation_id
        state.active_operation_id = request_id
        self.calls.append((tool_name, effect, request_id))
        envelope = callback(dict(arguments))
        state.active_operation_id = None
        if envelope.get("success") is True:
            state.revision += 1
        else:
            state.dirty = True
        return {"success": envelope.get("success") is True,
                "data": envelope.get("data"), "error": envelope.get("error")}


class _Step:
    def __init__(self):
        self.selected_mesh = "auto"
        self.calls = []

    def mesh(self, *args):
        self.calls.append(args)
        if len(args) == 1:
            return self.selected_mesh
        if len(args) == 2:
            self.selected_mesh = args[1]
            return None
        raise AssertionError(args)


class _FeatureList:
    def __init__(self, step):
        self.step = step

    def get(self, tag):
        assert tag == "time1"
        return self.step


class _Study:
    def __init__(self, step):
        self.step = step

    def feature(self):
        return _FeatureList(self.step)

    def getSolverSequences(self, kind):
        assert kind == "SolverSequence"
        return ["seq1"]


class _SolutionInfo:
    def getSolverSequence(self, outer):
        assert outer == 1
        return "seq1"

    def getISol(self, outer, inner):
        assert (outer, inner) == (1, 1)
        return [0, 0]


class _Solution:
    def study(self):
        return "std1"

    def getSolutioninfo(self):
        return _SolutionInfo()

    def getMesh(self, geometry, object_index):
        assert (geometry, object_index) == ("geom1", 0)
        return "mesh1"


class _SolutionList:
    def get(self, tag):
        assert tag == "sol1"
        return _Solution()


class _Model:
    def __init__(self):
        self.step = _Step()
        self.study_node = _Study(self.step)

    def study(self, tag):
        assert tag == "std1"
        return self.study_node

    def sol(self):
        return _SolutionList()


class _Backend:
    def __init__(self):
        self.service = _Service()
        self.store = object()
        self.frozen = []

    def model_project_binding(self, ref):
        return {"attribution": "PROJECT_BOUND", "project_id": "project"}

    def _freeze_g2_model(self, ref, reason):
        state = self.service.ledger._state_for(ref)
        state.dirty = True
        self.frozen.append((ref.as_dict(), reason))


def _configured_executor():
    backend = _Backend()
    source_ref = model_ref_from_mapping(SOURCE_REF)
    backend.service.ledger.states["source"] = _State(source_ref, revision=3, active="outer-op")
    executor = convergence.MeshConvergenceExecutor(
        backend, object(), source_ref, 3,
        {"project_id": "project", "session_id": "session", "request_id": "req",
         "idempotency_key": "idem"}, "outer-op")
    executor.plan = {
        "checkpoint_id": "cp1", "checkpoint_record": {"source_binding": {"model_ref": SOURCE_REF, "revision": 3},
                                                            "sha256": "b" * 64},
        "checkpoint_path": object(), "mesh_path": MESH_PATH, "mesh_component": "comp1",
        "mesh_tag": "mesh1", "geometry_tag": "geom1", "study_path": STUDY_PATH,
        "study_tag": "std1", "step_bindings": [{"feature": "time1", "type_id": "Transient",
                                                       "geometry": "geom1", "source_mesh_readback": "auto"}],
        "refine_tags": {"case1": "mc_case1"},
        "datasets": {METRIC_SHA: {"dataset": "dset1", "path": DATASET_PATH,
                                  "requested_solution": None, "requested_outer": None,
                                  "requested_inner": None}},
        "metric_hashes": [METRIC_SHA],
        "source_model_ref": SOURCE_REF, "source_revision": 3,
    }
    executor.budget = ComputationBudget(max_cases=2, max_wall_time_s=300.0, max_consecutive_failures=1)
    return backend, executor


def test_solution_tuple_selection_uses_metric_selectors_and_refuses_ambiguity():
    _backend, executor = _configured_executor()
    executor.plan["branch_started"] = True
    dataset = {"dataset": "dset1", "requested_solution": "sol1",
               "requested_outer": 2, "requested_inner": 1}
    axes = {"binding_complete": True, "solution": "sol1", "study": "std1",
            "dataset_binding": {"solution": "sol1"},
            "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1},
                             {"outer": 2, "inner": 1, "solnum": 4}]}
    selected = executor._select_solution_tuple({}, dataset, axes, None, case_id="case1")
    assert selected == {"dataset": "dset1", "solution": "sol1", "study": "std1",
                        "outer": 2, "inner": 1, "solnum": 4}

    dataset["requested_outer"] = None
    with pytest.raises(ExecutionContractError) as exc:
        executor._select_solution_tuple({}, dataset, axes, None, case_id="case1")
    assert exc.value.code == "SOLUTION_AXIS_METADATA_UNAVAILABLE"
    assert exc.value.details["case_evidence"]["available_pairs"] == axes["solnum_pairs"]


def test_solution_and_mesh_binding_uses_real_study_solver_and_tuple_mesh(monkeypatch):
    from comsol_mcp import _g3_w16

    backend, executor = _configured_executor()
    model = _Model()
    monkeypatch.setattr(_g3_w16, "bound_model", lambda _worker, _tag: model)
    monkeypatch.setattr(_g3_w16, "_call", lambda node, method, *args: getattr(node, method)(*args))
    evidence = executor._verify_solver_and_mesh_binding(
        executor.source_ref, {"solution": "sol1", "study": "std1", "outer": 1, "inner": 1},
        {"study": "std1"})
    assert evidence["study_solver_sequences"] == ["seq1"]
    assert evidence["solution_info_solver_sequence"] == "seq1"
    assert evidence["solution_mesh"] == "mesh1"
    assert "SolutionInfo.getISol(outer, inner)" in evidence["evidence"]

    class WrongMesh(_Solution):
        def getMesh(self, _geometry, _object_index):
            return "mesh-other"

    class WrongList:
        def get(self, _tag):
            return WrongMesh()

    model.sol = lambda: WrongList()
    with pytest.raises(ExecutionContractError, match="different mesh") as exc:
        executor._verify_solver_and_mesh_binding(
            executor.source_ref, {"solution": "sol1", "study": "std1", "outer": 1, "inner": 1},
            {"study": "std1"})
    assert exc.value.code == "SOLUTION_AXIS_METADATA_UNAVAILABLE"


def test_preflight_pins_checkpoint_and_validates_mesh_study_and_datasets_before_branch(monkeypatch, tmp_path):
    from comsol_mcp import _g3_w16
    from comsol_mcp import _g3_results

    backend, executor = _configured_executor()
    executor.plan = None
    executor.budget = None
    mesh_node = SimpleNamespace()
    study_node = SimpleNamespace()
    component = SimpleNamespace()
    dataset_node = SimpleNamespace()
    feature_list = SimpleNamespace()

    monkeypatch.setattr(_g2_checkpoint_ops, "checkpoint_input", lambda *_args: (
        {"checkpoint_id": "cp1", "source_binding": {"model_ref": SOURCE_REF, "revision": 3},
         "sha256": "b" * 64}, tmp_path / "cp1.mph"))
    monkeypatch.setattr(_g3_w16, "_mesh_sequence_context",
                        lambda *_args, **_kwargs: (MESH_PATH, mesh_node, "comp1", "mesh1"))
    monkeypatch.setattr(_g3_w16, "_require_component", lambda *_args: component)
    monkeypatch.setattr(_g3_w16, "_study_context",
                        lambda *_args, **_kwargs: (STUDY_PATH, study_node, "std1"))
    monkeypatch.setattr(_g3_w16, "_feature_rows",
                        lambda _study: [{"tag": "time1", "type_id": "Transient", "label": None}])
    monkeypatch.setattr(_g3_w16, "tag_list", lambda node: ["refine0"] if node is feature_list else ["geom1"])

    step = _Step()
    def call(node, method, *args):
        if node is mesh_node and method == "geom": return "geom1"
        if node is mesh_node and method == "feature": return feature_list
        if node is component and method == "geom": return object()
        if node is study_node and method == "feature": return feature_list
        if node is feature_list and method == "get": return step
        if node is step and method == "mesh": return step.mesh(*args)
        if node is dataset_node and method == "getType": return "Solution"
        raise AssertionError((node, method, args))

    monkeypatch.setattr(_g3_w16, "_call", call)
    monkeypatch.setattr(convergence, "_call", call)
    monkeypatch.setattr(_g3_w16, "resolve_path", lambda *_args, **_kwargs: (DATASET_PATH, dataset_node))
    monkeypatch.setattr(_g3_results, "dataset_solution_indices", lambda *_args, **_kwargs: {})
    definition = {"mesh_path": MESH_PATH, "study_path": STUDY_PATH,
                  "source_checkpoint_id": "cp1",
                  "levels": [{"case_id": "one", "refinement": {"nrefine": 1}},
                             {"case_id": "two", "refinement": {"nrefine": 2}}]}
    result = executor.preflight(worker=executor.worker, model_tag="source", definition=definition,
                                metric_refs=[{"metric_id": "m1", "version": 1,
                                              "definition_sha256": METRIC_SHA,
                                              "definition": {"solution": {"dataset": "dset1"}}}],
                                source_checkpoint_id="cp1", level_count=2)
    assert result["status"] == "PREFLIGHTED"
    assert result["study"]["source_step_meshes"][0]["source_mesh_readback"] == "auto"
    assert set(executor.plan["refine_tags"]) == {"one", "two"}
    assert executor.budget.status()["max_cases"] == 2
    assert backend.service.calls == []


def test_execute_case_stops_and_freezes_after_study_run_uncertainty(monkeypatch):
    from comsol_mcp import _g3_ops, _g3_results, _g3_w16

    backend, executor = _configured_executor()
    model = _Model()
    monkeypatch.setattr(_g3_w16, "bound_model", lambda _worker, _tag: model)
    monkeypatch.setattr(_g3_w16, "_call", lambda node, method, *args: getattr(node, method)(*args))
    monkeypatch.setattr(convergence, "property_get", lambda *_args: {
        "properties": [{"name": "numrefine", "value": {"kind": "integer", "shape": [], "data": 1}}]})

    def run_owned(_backend, _operation, source_ref, body, _execution, _op_id, metadata, _path, _proof):
        assert body["checkpoint_id"] == "cp1"
        branch_mapping = {**source_ref.as_dict(), "model_tag": "branch_case1"}
        branch_ref = model_ref_from_mapping(branch_mapping)
        backend.service.ledger.states[branch_ref.model_tag] = _State(branch_ref)
        return {"success": True, "data": {"status": "SUCCEEDED", "complete": True,
                "checkpoint_id": "cp1", "branch_model_ref": branch_mapping, "branch_revision": 0,
                "admission_source_identity": {"model_ref": SOURCE_REF, "revision": 3},
                "source_pointer_restored": True, "source_unchanged_within_scope": True,
                "cleanup": {"policy": "persistent owned branch retained"},
                "evidence": {"checkpoint_after_sha256": metadata["sha256"],
                             "clone_after_sha256": metadata["sha256"]},
                "persistence": {"verified": True}}}

    monkeypatch.setattr(_g2_checkpoint_ops, "run_owned", run_owned)
    calls = []
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.feature_create",
                        lambda *_args: {"status": "APPLIED", "ok": True})
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.build",
                        lambda *_args: calls.append("mesh.build") or {
                            "status": "APPLIED", "ok": True, "built_feature_range": ["mc_case1"]})
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.statistics",
                        lambda *_args: {"is_complete": True, "is_empty": False, "element_count": 100})
    monkeypatch.setitem(_g3_ops.DISPATCH, "study.run",
                        lambda *_args: calls.append("study.run") or {
                            "status": "DISPATCHED_UNVERIFIED", "ok": False,
                            "execution_state_unknown": True, "partial_change": True})
    monkeypatch.setattr(_g3_results, "dataset_solution_indices",
                        lambda *_args, **_kwargs: pytest.fail("metric binding must not continue after failed Study.run"))

    with pytest.raises(ExecutionContractError) as exc:
        executor.execute_case(worker=executor.worker, source_model_ref=SOURCE_REF,
                              source_revision=3, source_checkpoint_id="cp1", mesh_path=MESH_PATH,
                              study_path=STUDY_PATH, case_id="case1", refinement={"nrefine": 1},
                              metric_refs=[{"metric_id": "m1", "version": 1,
                                            "definition_sha256": METRIC_SHA,
                                            "definition": {"solution": {"dataset": "dset1"}}}],
                              metric_sha256=[METRIC_SHA], operation_id="outer-op")
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    evidence = exc.value.details["case_evidence"]
    assert evidence["phase"] == "study_run", evidence.get("failure")
    assert evidence["branch_model_ref"]["model_tag"] == "branch_case1"
    assert evidence["checkpoint_id"] == "cp1"
    assert evidence["raw"]["study_run"]["status"] == "DISPATCHED_UNVERIFIED"
    assert evidence["run_budget"]["cases_evaluated"] == 1
    assert calls == ["mesh.build", "study.run"]
    assert backend.frozen and backend.frozen[0][0]["model_tag"] == "branch_case1"


def test_source_identity_mismatch_before_first_branch_is_prewrite_refusal():
    backend, executor = _configured_executor()
    backend.service.ledger.states["source"].revision = 4

    with pytest.raises(PreWriteRefusal) as exc:
        executor.execute_case(worker=executor.worker, source_model_ref=SOURCE_REF,
                              source_revision=3, source_checkpoint_id="cp1", mesh_path=MESH_PATH,
                              study_path=STUDY_PATH, case_id="case1", refinement={"nrefine": 1},
                              metric_refs=[{"metric_id": "m1", "version": 1,
                                            "definition_sha256": METRIC_SHA,
                                            "definition": {"solution": {"dataset": "dset1"}}}],
                              metric_sha256=[METRIC_SHA], operation_id="outer-op")

    assert exc.value.code == "REVISION_CONFLICT"
    assert exc.value.stage == "validation"
    assert exc.value.details["phase"] == "source_identity"
    assert not executor.plan.get("branch_started")
    assert backend.frozen == []
    assert backend.service.calls == []


def test_successful_case_binds_selected_mesh_on_owned_branch_and_records_real_tuple(monkeypatch):
    from comsol_mcp import _g3_ops, _g3_results, _g3_w16

    backend, executor = _configured_executor()
    source_model = _Model()
    branch_model = _Model()
    models = {"source": source_model}
    calls = []
    axes = {"binding_complete": True, "solution": "sol1", "study": "std1",
            "dataset_binding": {"solution": "sol1"},
            "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}]}
    observation = {"observation_id": "obs_mesh_case1", "sha256": "c" * 64}

    def run_owned(_backend, _operation, source_ref, body, _execution, _op_id, metadata, _path, _proof):
        assert body["checkpoint_id"] == "cp1"
        branch_mapping = {**source_ref.as_dict(), "model_tag": "branch_case1"}
        branch_ref = model_ref_from_mapping(branch_mapping)
        models[branch_ref.model_tag] = branch_model
        backend.service.ledger.states[branch_ref.model_tag] = _State(branch_ref)
        return {"success": True, "data": {"status": "SUCCEEDED", "complete": True,
                "checkpoint_id": "cp1", "branch_model_ref": branch_mapping, "branch_revision": 0,
                "admission_source_identity": {"model_ref": SOURCE_REF, "revision": 3},
                "source_pointer_restored": True, "source_unchanged_within_scope": True,
                "cleanup": {"policy": "persistent owned branch retained"},
                "evidence": {"checkpoint_after_sha256": metadata["sha256"],
                             "clone_after_sha256": metadata["sha256"]},
                "persistence": {"verified": True}}}

    def study_run(_worker, model_tag, _arguments):
        assert model_tag == "branch_case1"
        calls.append("study.run")
        return {"status": "APPLIED", "ok": True, "solver_sequences": ["seq1"]}

    def evaluate(_worker, model_tag, args):
        assert model_tag == "branch_case1"
        assert args["solution"] == {"dataset": "dset1", "solution": "sol1", "outer": 1, "inner": 1}
        calls.append("metric.evaluate")
        return {"items": [{"definition_sha256": METRIC_SHA, "unit": "m",
                            "observation_ref": observation,
                            "values": [{"outer": 1, "inner": 1, "solnum": 1, "value": 2.5}]}]}

    monkeypatch.setattr(_g2_checkpoint_ops, "run_owned", run_owned)
    monkeypatch.setattr(_g3_w16, "bound_model", lambda _worker, tag: models[tag])
    monkeypatch.setattr(convergence, "property_get", lambda *_args: {
        "properties": [{"name": "numrefine", "value": {"kind": "integer", "shape": [], "data": 1}}]})
    monkeypatch.setattr(_g3_results, "dataset_solution_indices", lambda *_args, **_kwargs: axes)
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.feature_create",
                        lambda *_args: calls.append("mesh.feature_create") or {"status": "APPLIED", "ok": True})
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.build",
                        lambda *_args: calls.append("mesh.build") or {
                            "status": "APPLIED", "ok": True,
                            "built_feature_range": [executor.plan["refine_tags"]["case1"]]})
    monkeypatch.setitem(_g3_ops.DISPATCH, "mesh.statistics",
                        lambda *_args: {"is_complete": True, "is_empty": False, "element_count": 100})
    monkeypatch.setitem(_g3_ops.DISPATCH, "study.run", study_run)
    monkeypatch.setitem(_g3_ops.DISPATCH, "metric.evaluate", evaluate)

    try:
        result = executor.execute_case(worker=executor.worker, source_model_ref=SOURCE_REF,
                                       source_revision=3, source_checkpoint_id="cp1", mesh_path=MESH_PATH,
                                       study_path=STUDY_PATH, case_id="case1", refinement={"nrefine": 1},
                                       metric_refs=[{"metric_id": "m1", "version": 1,
                                                     "definition_sha256": METRIC_SHA,
                                                     "definition": {"solution": {"dataset": "dset1"}}}],
                                       metric_sha256=[METRIC_SHA], operation_id="outer-op")
    except ExecutionContractError as exc:
        pytest.fail(f"successful branch case failed: {exc.code} details={exc.details!r}; cause={exc.__cause__!r}")

    assert result["status"] == "SUCCEEDED"
    assert result["observations"][METRIC_SHA]["value"] == {"value": 2.5, "unit": "m"}
    assert result["study_readback"]["selected_solution_tuples"][METRIC_SHA]["actual_binding"]["solution_mesh"] == "mesh1"
    assert result["study_mesh_bindings"][0]["source_mesh"] == "auto"
    assert result["study_mesh_bindings"][0]["mesh_readback"] == "mesh1"
    assert source_model.step.selected_mesh == "auto"
    assert branch_model.step.selected_mesh == "mesh1"
    assert calls == ["mesh.feature_create", "mesh.build", "study.run", "metric.evaluate"]
    assert result["run_budget"]["cases_evaluated"] == 1
