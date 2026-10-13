"""Canonical W21 routes and durable scheduler safety; software evidence only."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_registry import validate_call
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, REQUIRES_ISOLATION
from comsol_mcp._g3_w21 import ALIASES
from comsol_mcp._observation_store import observation_context
from comsol_mcp._operation_store import OperationStore
from comsol_mcp import _w21_execution as execution
from comsol_mcp import _g3_results as results


def _identity(project="project-a", model="model-a"):
    return {"model_tag": model, "session_id": "session-a", "server_instance_id": "server-a", "generation": 1}


def _definition():
    return {
        "study": "std1",
        "sample": {"spec": {"solution": {"dataset": "dset1"}, "expressions": ["T"]},
                   "points": [[0.0, 0.0]], "coordinate_unit": "m"},
        "metrics": {"peak": {"expression": "T", "unit": "K", "indices": [0]}},
        "times": [0.0],
        "validation": {"range": [250.0, 400.0]},
        "parameters": {"heat": [1.0, 2.0, 3.0]},
        "units": {"heat": "W"},
        "sampling": {"kind": "cartesian_grid"},
        "budget": {"max_cases": 3, "max_wall_time_s": 120.0},
    }


def test_canonical_operation_ids_have_distinct_handlers_and_catalog_effects():
    assert DISPATCH["solver.solution_transfer"] is execution.op_solver_solution_transfer
    assert DISPATCH["stage.state_transfer"] is execution.op_stage_state_transfer
    assert DISPATCH["experiment.run"] is execution.op_experiment_run
    assert DISPATCH["optimization.bounded_run"] is execution.op_optimization_bounded_run
    assert DISPATCH["experiment.design"] is execution.op_experiment_design
    assert DISPATCH["experiment.state_map"] is execution.op_experiment_state_map
    assert EFFECTS["solver.solution_transfer"] == "WRITE"
    assert EFFECTS["stage.state_transfer"] == "COMPUTE"
    assert EFFECTS["optimization.bounded_run"] == "COMPUTE"
    assert EFFECTS["experiment.design"] == "STATE_WRITE"
    assert EFFECTS["experiment.run"] == "COMPUTE"
    assert EFFECTS["experiment.state_map"] == "WRITE"
    assert {"solver.solution_transfer", "stage.state_transfer", "experiment.design",
            "experiment.run", "optimization.bounded_run"}.issubset(REQUIRES_ISOLATION)
    assert ALIASES["stage_state_transfer"] == "stage.state_transfer"
    assert ALIASES["optimization_bounded_run"] == "optimization.bounded_run"
    assert ALIASES["solver_solution_transfer"] == "solver.solution_transfer"
    assert ALIASES["experiment_design"] == "experiment.design"
    assert ALIASES["experiment_run"] == "experiment.run"
    assert ALIASES["experiment_state_map"] == "experiment.state_map"


def test_canonical_catalog_schema_accepts_the_declared_contracts():
    envelope = {"project_id": "project-a", "session_id": "session-a", "model_ref": _identity(),
                "expected_revision": 4, "idempotency_key": "key-a"}
    transfer = dict(envelope, source={"dataset": "dset1", "solution": "sol1", "inner": 1, "outer": 1},
                    target={"segments": [{"collection": "sol", "tag": "sol2"},
                                         {"collection": "feature", "tag": "v1"}]},
                    mapping={})
    assert validate_call("solver.solution_transfer", transfer).operation_id == "solver.solution_transfer"
    time_transfer = dict(transfer, source={"dataset": "dset1", "outer": 1,
                                           "time": [{"value": 0.25, "unit": "s"}]})
    assert validate_call("solver.solution_transfer", time_transfer).operation_id == "solver.solution_transfer"
    state_map = dict(
        envelope,
        source={"dataset": "dset1", "solution": "sol2", "outer": 2, "inner": 3},
        target={"segments": [{"collection": "sol", "tag": "sol3"},
                             {"collection": "feature", "tag": "v1"}]},
        mapping={"profile": "same_name_same_mesh_initialization", "variables": [{
            "source_variable": "T", "target_variable": "T", "source_unit": "K",
            "target_unit": "K", "mapping_method": "identity",
        }]},
    )
    assert validate_call("experiment.state_map", state_map).operation_id == "experiment.state_map"
    with pytest.raises(ExecutionContractError):
        validate_call("experiment.state_map", {**state_map, "mapping": {**state_map["mapping"], "extra": 1}})
    with pytest.raises(ExecutionContractError):
        validate_call("experiment.state_map", {**state_map, "mapping": {
            "profile": "same_name_same_mesh_initialization", "variables": [{
                "source_variable": "T", "target_variable": "T", "source_unit": "K",
                "target_unit": "s", "mapping_method": "identity",
            }],
        }})
    assert validate_call("experiment.design", dict(envelope, definition={"sampling": "declared"})).operation_id == "experiment.design"
    assert validate_call("experiment.run", dict(envelope, experiment_id="exp_1", resources={"max_cases": 1}, timeout_s=1.0)).operation_id == "experiment.run"
    with pytest.raises(ExecutionContractError):
        validate_call("experiment.run", dict(envelope, experiment_id="exp_1", resources=None))


def test_legacy_bounded_optimizer_stops_after_unknown_engine_case():
    from comsol_mcp._g3_w21 import BoundedOptimizer, ComputationBudget

    calls = []
    optimizer = BoundedOptimizer("objective", parameter_bounds={"x": (0.0, 1.0)},
                                budget=ComputationBudget(max_cases=10))

    def evaluate(_params):
        calls.append(1)
        return {"status": "EXECUTION_STATE_UNKNOWN", "execution_state_unknown": True}

    result = optimizer.run_bounded_search(grid_points_per_dim=4, eval_fn=evaluate)
    assert len(calls) == 1
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"


class _Collection:
    def __init__(self, values):
        self.values = values

    def tags(self):
        return list(self.values)

    def get(self, tag):
        return self.values[tag]


class _Variables:
    def __init__(self, type_name="Variables", fail_readback=False):
        self.type_name = type_name
        self.values = {"useinitsol": "off", "initmethod": "init", "initsol": "zero",
                       "initsoluse": "current", "initsolusesolnum": 1,
                       "solnum": "last", "manualsolnum": 1}
        self.set_calls = []
        self.fail_readback = fail_readback

    def getType(self):
        return self.type_name

    def set(self, name, value):
        self.set_calls.append((name, value))
        self.values[name] = value

    def getString(self, name):
        if self.fail_readback and name == "initsol":
            return "sol1"
        return self.values[name]

    def getInt(self, name):
        return self.values[name]


class _Solver:
    def __init__(self, study, features=None, attached=True):
        self.study_tag = study
        self.attached = attached
        self.features = _Collection(features or {})
        self.run_calls = 0

    def study(self):
        return self.study_tag

    def isAttached(self):
        return self.attached

    def feature(self):
        return self.features

    def run(self):
        self.run_calls += 1


class _Model:
    def __init__(self, solvers):
        self.solvers = _Collection(solvers)
        study_tags = {solver.study_tag for solver in solvers.values() if solver.study_tag}
        self.studies = _Collection({tag: object() for tag in sorted(study_tags)})

    def sol(self, tag=None):
        return self.solvers if tag is None else self.solvers.get(tag)

    def study(self, tag=None):
        return self.studies if tag is None else self.studies.get(tag)


def _transfer_arguments(inner=3, outer=2, mapping=None, time=None, solution="sol2"):
    source = {"dataset": "dset1", "outer": outer}
    if solution is not None:
        source["solution"] = solution
    if inner is not None:
        source["inner"] = inner
    if time is not None:
        source["time"] = time
    return {
        "source": source,
        "target": {"segments": [{"collection": "sol", "tag": "sol3"},
                                {"collection": "feature", "tag": "v1"}]},
        "mapping": {} if mapping is None else mapping,
    }


def _state_map_arguments():
    return {
        "source": {"dataset": "dset1", "solution": "sol2", "outer": 2, "inner": 3},
        "target": {"segments": [{"collection": "sol", "tag": "sol3"},
                                {"collection": "feature", "tag": "v1"}]},
        "mapping": {"profile": "same_name_same_mesh_initialization", "variables": [{
            "source_variable": "T", "target_variable": "T", "source_unit": "K",
            "target_unit": "K", "mapping_method": "identity",
        }]},
    }


def _bound_indices():
    return {
        "dataset": "dset1", "solution": "sol2", "binding_complete": True,
        "pair_mapping_complete": True,
        "binding_source": "typed dataset + SolutionInfo.getSolnum(outer, strict)",
        "parameters_complete": True,
        "parameters": {"by_pair": {"2:3": {
            "names": ["t"], "values": [0.2], "units": ["s"], "solnum": 3,
        }}},
        "solnum_pairs": [{"outer": 2, "inner": 3, "solnum": 3}],
    }


def test_canonical_solution_transfer_selects_exact_solution_indices_without_solving(monkeypatch):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    result = execution.op_solver_solution_transfer(object(), "model-a", _transfer_arguments())
    assert result["status"] == "APPLIED"
    assert result["coverage_status"] == "PARTIAL"
    assert result["source"]["manualsolnum"] == 3
    assert result["source"]["selection_mode"] == "solution_index"
    assert result["source"]["time_binding"] == {"name": "t", "value": 0.2, "unit": "s"}
    assert result["mapping"] == {}
    assert result["variable_mapping_applied"] is False
    assert result["initialization_readback"] == {
        "useinitsol": "on", "initmethod": "sol", "initsol": "sol2", "initsoluse": "manual",
        "solnum": "manual", "initsolusesolnum": 2, "manualsolnum": 3,
    }
    assert result["solve_dispatched"] is False
    assert result["verification"]["status"] == "CONFIGURED_ONLY"
    assert result["verification"]["mesh_compatibility"] == "UNVERIFIED"
    assert result["verification"]["history_preserved"] is False
    assert model.solvers.get("sol3").run_calls == 0


@pytest.mark.parametrize("patch", [
    {"source": {"dataset": "dset1", "solution": "sol2", "inner": "last", "outer": 2}},
    {"mapping": {"T": "T_init"}},
    {"mapping": {"T": "T"}},
    {"target": {"segments": [{"collection": "sol", "tag": "sol3"},
                                {"collection": "feature", "tag": "step1"}]}},
])
def test_solution_transfer_rejects_unbounded_selectors_before_writes(monkeypatch, patch):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables, "step1": _Variables("Time")})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    arguments = _transfer_arguments()
    arguments.update(patch)
    with pytest.raises(ExecutionContractError):
        execution.op_solver_solution_transfer(object(), "model-a", arguments)
    assert variables.set_calls == []


def test_solution_transfer_refuses_ambiguous_dataset_pair_and_readback_mismatch(monkeypatch):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: dict(
        _bound_indices(), solnum_pairs=[{"outer": 2, "inner": 3, "solnum": 3},
                                       {"outer": 2, "inner": 3, "solnum": 4}]))
    with pytest.raises(ExecutionContractError, match="uniquely resolve"):
        execution.op_solver_solution_transfer(object(), "model-a", _transfer_arguments())
    assert variables.set_calls == []
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    variables.fail_readback = True
    with pytest.raises(ExecutionContractError, match="did not read back exactly"):
        execution.op_solver_solution_transfer(object(), "model-a", _transfer_arguments())


@pytest.mark.parametrize("time", [
    [{"value": 0.3, "unit": "s"}],
    [{"value": 0.2, "unit": "ms"}],
    [{"value": True, "unit": "s"}],
    [{"value": 0.2, "unit": "s"}, {"value": 0.2, "unit": "s"}],
])
def test_solution_transfer_time_quantity_requires_exact_pair_value_and_unit(monkeypatch, time):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    with pytest.raises(ExecutionContractError):
        execution.op_solver_solution_transfer(object(), "model-a",
                                               _transfer_arguments(inner=None, time=time))
    assert variables.set_calls == []


def test_solution_transfer_accepts_unique_time_quantity_and_rejects_mesh_history_requests(monkeypatch):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    result = execution.op_solver_solution_transfer(
        object(), "model-a", _transfer_arguments(inner=None, time=[{"value": 0.2, "unit": "s"}], solution=None))
    assert result["source"]["selection_mode"] == "time_quantity"
    assert result["source"]["inner"] == 3
    assert result["source"]["time_binding"] == {"name": "t", "value": 0.2, "unit": "s"}
    assert model.solvers.get("sol3").run_calls == 0

    variables.set_calls.clear()
    request = _transfer_arguments()
    request["require_same_mesh"] = True
    with pytest.raises(ExecutionContractError, match="mesh/history verification"):
        execution.op_solver_solution_transfer(object(), "model-a", request)
    assert variables.set_calls == []


def test_experiment_state_map_configures_selector_and_reports_partial_native_evidence(monkeypatch):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())
    field = {
        "status": "VERIFIED",
        "scope": "selected_source_field_values_and_native_coordinate_payload_only",
        "solution_tuple": {"outer": 2, "inner": 3, "solnum": 3,
                           "source": "SolutionInfo.getSolnum(outer, strict)"},
        "selector_readback": {
            "dataset": "dset1", "outerinput": "manual", "outersolnum": 2,
            "innerinput": "manual", "solnum": 3,
        },
        "selection_readback": {"geometry": "geom1", "entities": [1, 2]},
        "expression_unit_readback": {"values": {"T": "K"}, "field_dimensionality": "UNVERIFIED"},
        "coordinates": {"values": [[0.0, 1.0]], "shape": [1, 2], "coordinate_frame": "UNVERIFIED"},
        "field_array": {"values": [[[[300.0, 301.0]]]], "shape": [1, 1, 1, 2]},
        "numeric_scalar_count_including_real_imag_and_coordinates": 4,
        "json_response_bytes": 512,
    }
    mesh = {"status": "VERIFIED", "mesh_tag": "mesh1", "topology": "UNVERIFIED",
            "dof_equivalence": "UNVERIFIED", "coordinate_frame": "UNVERIFIED"}
    monkeypatch.setattr(execution, "_strict_source_field_readback", lambda *args: field)
    monkeypatch.setattr(execution, "_read_source_solution_mesh_association", lambda *args: mesh)

    result = execution.op_experiment_state_map(object(), "model-a", _state_map_arguments())

    assert result["status"] == "APPLIED"
    assert result["contract"] == "experiment.state_map/v1"
    assert result["coverage_status"] == "PARTIAL"
    assert result["source"]["manualsolnum"] == 3
    assert result["target_solver_attachment_readback"] == {
        "status": "VERIFIED", "study": "std2", "solver": "sol3", "is_attached": True,
        "unique_attached_solver_tags": ["sol3"],
        "readback_methods": ["SolverSequence.study()", "SolverSequence.isAttached()"],
    }
    assert result["initialization_readback"]["initsol"] == "sol2"
    assert result["declared_variable_mappings"] == _state_map_arguments()["mapping"]["variables"]
    assert result["variable_mapping_applied"] is False
    assert result["mapping_evidence"] == {
        "status": "PARTIAL",
        "reason": "source field values and source solution-to-mesh association were read back; the per-variable target mapping remains unapplied and unverified",
        "source_field_identity": "VERIFIED_FOR_REQUESTED_EXPRESSIONS_AND_SELECTION", "target_field_identity": "UNVERIFIED",
        "source_target_units": "UNVERIFIED", "source_target_mesh_identity": "UNVERIFIED",
        "source_mesh_association": "VERIFIED", "source_mesh_tag": "mesh1",
        "target_mesh_association": "UNVERIFIED", "mesh_topology": "UNVERIFIED",
        "dof_equivalence": "UNVERIFIED",
        "frame_equivalence": "UNVERIFIED", "hidden_solver_history": "NOT_VERIFIED",
    }
    assert result["source_field_readback"] == field
    assert result["source_solution_mesh_association"] == mesh
    assert result["declared_units"] == [{
        "source_variable": "T", "source_unit": "K", "target_variable": "T",
        "target_unit": "K", "status": "DECLARATION_ONLY_UNITS_NOT_VERIFIED",
    }]
    assert result["solve_dispatched"] is False
    assert variables.set_calls
    assert model.solvers.get("sol3").run_calls == 0


@pytest.mark.parametrize("failed_stage", ["field", "mesh"])
def test_experiment_state_map_source_readback_failure_does_not_write_target(monkeypatch, failed_stage):
    variables = _Variables()
    model = _Model({"sol2": _Solver("std1"), "sol3": _Solver("std2", {"v1": variables})})
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())

    def field_readback(*args):
        if failed_stage == "field":
            raise ExecutionContractError("FIELD_READBACK_INCOMPLETE", "injected field failure")
        return {"selection_readback": {"geometry": "geom1"}}

    def mesh_readback(*args):
        if failed_stage == "mesh":
            raise ExecutionContractError("SOLUTION_MESH_ASSOCIATION_UNAVAILABLE", "injected mesh failure")
        return {"status": "VERIFIED", "mesh_tag": "mesh1"}

    monkeypatch.setattr(execution, "_strict_source_field_readback", field_readback)
    monkeypatch.setattr(execution, "_read_source_solution_mesh_association", mesh_readback)
    with pytest.raises(ExecutionContractError):
        execution.op_experiment_state_map(object(), "model-a", _state_map_arguments())
    assert variables.set_calls == []
    assert model.solvers.get("sol3").run_calls == 0


def test_state_map_source_tuple_resolution_is_exact_and_time_quantity_bound():
    assert execution._resolve_state_map_source_pair(_bound_indices(), _state_map_arguments()["source"]) == {
        "dataset": "dset1", "solution": "sol2", "outer": 2, "inner": 3, "solnum": 3,
    }
    by_time = {"dataset": "dset1", "solution": "sol2", "outer": 2,
               "time": [{"value": 0.2, "unit": "s"}]}
    assert execution._resolve_state_map_source_pair(_bound_indices(), by_time)["inner"] == 3
    with pytest.raises(ExecutionContractError):
        execution._resolve_state_map_source_pair(_bound_indices(), {**by_time, "dataset": "other"})
    duplicate = dict(_bound_indices(), solnum_pairs=[
        {"outer": 2, "inner": 3, "solnum": 3}, {"outer": 2, "inner": 3, "solnum": 4},
    ])
    with pytest.raises(ExecutionContractError):
        execution._resolve_state_map_source_pair(duplicate, _state_map_arguments()["source"])


def test_source_mesh_association_uses_zero_based_solution_object_index_and_exact_geometry():
    calls = []

    class SolutionInfo:
        def getISol(self, outer, inner):
            calls.append(("getISol", outer, inner))
            return [4, 7]

    class Solver:
        def getSolutioninfo(self):
            return SolutionInfo()

        def getMesh(self, geometry, i_multi):
            calls.append(("getMesh", geometry, i_multi))
            return "mesh_hist_4"

    class Model:
        def sol(self, tag):
            assert tag == "sol2"
            return Solver()

    result = execution._read_source_solution_mesh_association(Model(), "sol2", "geom1", 2, 3)
    assert calls == [("getISol", 2, 3), ("getMesh", "geom1", 4)]
    assert result["status"] == "VERIFIED"
    assert result["solution_object_index_zero_based"] == 4
    assert result["solution_index_within_object_zero_based"] == 7
    assert result["mesh_tag"] == "mesh_hist_4"
    assert result["topology"] == "UNVERIFIED"


def test_strict_eval_selector_uses_and_reads_manual_modes_and_exact_tuple():
    class Eval:
        def __init__(self, wrong_inner_mode=False):
            self.properties = {
                "data": "dset1", "outerinput": "all", "outersolnum": 1,
                "innerinput": "all", "solnum": 1,
            }
            self.set_calls = []
            self.wrong_inner_mode = wrong_inner_mode

        def set(self, name, value):
            self.set_calls.append((name, value))
            if not (self.wrong_inner_mode and name == "innerinput"):
                self.properties[name] = value

        def getString(self, name):
            return self.properties[name]

        def getInt(self, name):
            return self.properties[name]

    feature = Eval()
    assert results._set_strict_eval_selectors(feature, "dset1", 2, 3) == {
        "dataset": "dset1", "outerinput": "manual", "outersolnum": 2,
        "innerinput": "manual", "solnum": 3,
    }
    assert feature.set_calls == [
        ("outerinput", "manual"), ("outersolnum", 2),
        ("innerinput", "manual"), ("solnum", 3),
    ]

    wrong_mode = Eval(wrong_inner_mode=True)
    with pytest.raises(ExecutionContractError) as excinfo:
        results._set_strict_eval_selectors(wrong_mode, "dset1", 2, 3)
    assert excinfo.value.code == "SOLUTION_SELECTION_MISMATCH"

    wrong_dataset = Eval()
    with pytest.raises(ExecutionContractError) as excinfo:
        results._set_strict_eval_selectors(wrong_dataset, "dset2", 2, 3)
    assert excinfo.value.code == "DATASET_SELECTION_MISMATCH"
    assert wrong_dataset.set_calls == []


@pytest.mark.parametrize("indices", [[4], [4, -1], [True, 1], [0, 2.5]])
def test_source_mesh_association_rejects_malformed_native_tuple_indices(indices):
    class SolutionInfo:
        def getISol(self, outer, inner):
            return indices

    class Solver:
        def getSolutioninfo(self):
            return SolutionInfo()

        def getMesh(self, geometry, i_multi):
            pytest.fail("getMesh must not run after malformed getISol indices")

    class Model:
        def sol(self, tag):
            return Solver()

    with pytest.raises(ExecutionContractError, match="zero-based"):
        execution._read_source_solution_mesh_association(Model(), "sol2", "geom1", 2, 3)


def test_strict_source_field_readback_checks_selectors_and_cleanup_before_accepting(monkeypatch):
    dataset_node = object()
    dataset_list = _Collection({"dset1": dataset_node})
    model = SimpleNamespace(result=lambda: SimpleNamespace(dataset=lambda: dataset_list))
    monkeypatch.setattr(execution, "_resolve_dataset_binding", lambda *args, **kwargs: {"binding_complete": True})
    monkeypatch.setattr(execution, "_coordinate_context", lambda *args, **kwargs: {
        "component": "comp1", "geometry": "geom1", "space_dimension": 2,
    })
    pair = {"dataset": "dset1", "solution": "sol2", "outer": 2, "inner": 3, "solnum": 3}
    evidence = {
        "status": "VERIFIED", "solution_tuple": {"outer": 2, "inner": 3, "solnum": 3},
        "selector_readback": {
            "dataset": "dset1", "outerinput": "manual", "outersolnum": 2,
            "innerinput": "manual", "solnum": 3,
        },
        "selection_readback": {"geometry": "geom1"},
    }
    response = {"strict_field_readback": evidence, "cleanup": {"created": True, "removed": True, "cleanup_failed": False},
                "status": {"ok": True}}
    calls = []

    def evaluate(worker, model_tag, arguments, *, strict_field_readback=False):
        calls.append((arguments["spec"], strict_field_readback))
        return response

    monkeypatch.setattr(execution, "result_evaluate", evaluate)
    returned = execution._strict_source_field_readback(
        object(), "model-a", model, {"dataset": "dset1"}, _state_map_arguments()["source"], pair, ["T"]
    )
    assert returned == evidence
    spec, strict = calls[0]
    assert strict is True
    assert spec["expressions"] == ["T"]
    assert spec["solution"] == {"dataset": "dset1", "solution": "sol2", "outer": 2, "inner": 3}
    assert spec["selection"] == {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 2}

    response["cleanup"]["cleanup_failed"] = True
    with pytest.raises(ExecutionContractError, match="clean transient-node removal"):
        execution._strict_source_field_readback(
            object(), "model-a", model, {"dataset": "dset1"}, _state_map_arguments()["source"], pair, ["T"]
        )


@pytest.mark.parametrize("values,coordinates,expected", [
    ([0.0] * 65_536, [], 65_536),
    ([0.0] * 65_537, [], None),
    ([{"real": 1.0, "imag": 0.0}], [[0.0, 1.0]], 4),
])
def test_w21_strict_field_scalar_limit_includes_real_imag_and_coordinates(values, coordinates, expected):
    response = {"strict_field_readback": {
        "field_array": {"values": values}, "coordinates": {"values": coordinates},
    }}
    if expected is None:
        with pytest.raises(ExecutionContractError) as excinfo:
            results._enforce_w21_field_response_limits(response)
        assert excinfo.value.code == "FIELD_READBACK_LIMIT_EXCEEDED"
    else:
        scalar_count, byte_count = results._enforce_w21_field_response_limits(response)
        assert scalar_count == expected
        assert byte_count <= results.W21_FIELD_READBACK_MAX_JSON_BYTES


@pytest.mark.parametrize(("payload", "reported", "expected"), [
    ({"real": [[[300.0, 301.0]]], "imag": None,
     "coordinates": [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]}, 8, 8),
    ({"real": [[[300.0, 301.0]]], "imag": [[[0.25, 0.5]]],
     "coordinates": [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]}, 10, 10),
])
def test_strict_worker_count_matches_raw_real_imag_and_coordinates(payload, reported, expected):
    assert results._validate_strict_worker_field_payload_count(payload, reported) == expected


def test_strict_worker_count_rejects_fabricated_or_missing_raw_numeric_leaves():
    payload = {"real": [[[300.0, 301.0]]], "imag": None,
               "coordinates": [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]}
    with pytest.raises(ExecutionContractError) as excinfo:
        results._validate_strict_worker_field_payload_count(payload, 10)
    assert excinfo.value.code == "FIELD_READBACK_COUNT_MISMATCH"
    assert excinfo.value.details == {
        "reported_numeric_scalar_count": 10,
        "raw_payload_numeric_scalar_count": 8,
    }


def test_w21_normalized_preserve_response_counts_derived_imaginary_components_for_cap():
    values = [{"real": 1.0, "imag": 0.0}] * (results.W21_FIELD_READBACK_MAX_NUMERIC_SCALARS // 2 + 1)
    response = {"strict_field_readback": {
        "field_array": {"values": values}, "coordinates": {"values": []},
    }}
    with pytest.raises(ExecutionContractError) as excinfo:
        results._enforce_w21_field_response_limits(response)
    assert excinfo.value.code == "FIELD_READBACK_LIMIT_EXCEEDED"


def test_w21_strict_field_json_limit_fails_closed_without_truncation():
    response = {"strict_field_readback": {
        "field_array": {"values": []}, "coordinates": {"values": []},
    }, "padding": "x" * (results.W21_FIELD_READBACK_MAX_JSON_BYTES + 1)}
    with pytest.raises(ExecutionContractError) as excinfo:
        results._enforce_w21_field_response_limits(response)
    assert excinfo.value.code == "FIELD_READBACK_LIMIT_EXCEEDED"


def test_w21_strict_field_json_exact_limit_is_admitted():
    import json

    base = {"strict_field_readback": {
        "field_array": {"values": []}, "coordinates": {"values": []},
        "numeric_scalar_count_including_real_imag_and_coordinates": 0, "json_response_bytes": 0,
    }, "padding": ""}
    baseline = len(json.dumps(base, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    approximate_padding = results.W21_FIELD_READBACK_MAX_JSON_BYTES - baseline
    candidate = {**base, "strict_field_readback": dict(base["strict_field_readback"]),
                 "padding": "x" * (approximate_padding - 6)}
    _scalar_count, measured = results._enforce_w21_field_response_limits(candidate)
    assert measured == results.W21_FIELD_READBACK_MAX_JSON_BYTES


def test_strict_worker_field_error_preserves_structured_limit_code_and_safe_details(monkeypatch):
    class WorkerFailure(RuntimeError):
        failure = {
            "code": "FIELD_READBACK_LIMIT_EXCEEDED",
            "numeric_scalar_count": 65_537,
            "max_numeric_scalars": 65_536,
            "raw_array": [1.0, 2.0],
        }

    wrapped = ExecutionContractError("ENGINE_CALL_FAILED", "transport wrapper")
    wrapped.__cause__ = WorkerFailure("limit exceeded")
    monkeypatch.setattr(results, "_call", lambda *_args: (_ for _ in ()).throw(wrapped))
    with pytest.raises(ExecutionContractError) as excinfo:
        results._call_strict_worker_field_readback(object())
    assert excinfo.value.code == "FIELD_READBACK_LIMIT_EXCEEDED"
    assert excinfo.value.details == {
        "source": "structured_worker_failure",
        "numeric_scalar_count": 65_537,
        "max_numeric_scalars": 65_536,
    }
    assert "raw_array" not in excinfo.value.details


def test_strict_worker_unknown_failure_uses_unavailable_error():
    error = results._strict_worker_readback_failure(RuntimeError("unknown local exception"))
    assert error.code == "FIELD_READBACK_UNAVAILABLE"


@pytest.mark.parametrize("target_solver", [
    _Solver("std2", {"v1": _Variables()}, attached=False),
    _Solver("std2", {"v1": _Variables()}),
])
def test_experiment_state_map_refuses_detached_or_ambiguous_target_before_selector_write(monkeypatch, target_solver):
    variables = target_solver.features.values["v1"]
    solvers = {"sol2": _Solver("std1"), "sol3": target_solver}
    if target_solver.attached:
        solvers["sol4"] = _Solver("std2")
    model = _Model(solvers)
    monkeypatch.setattr(execution, "bound_model", lambda worker, tag: model)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *args: _bound_indices())

    with pytest.raises(ExecutionContractError) as excinfo:
        execution.op_experiment_state_map(object(), "model-a", _state_map_arguments())
    assert excinfo.value.code in {"TARGET_SOLVER_NOT_ATTACHED", "TARGET_STUDY_SOLVER_AMBIGUOUS"}
    assert variables.set_calls == []
    assert model.solvers.get("sol3").run_calls == 0


def test_experiment_design_persists_binding_and_unknown_run_stops_dispatch(tmp_path, monkeypatch):
    db = tmp_path / "operations.sqlite3"
    store = OperationStore(db)
    model_ref = _identity()
    monkeypatch.setattr(execution, "validate_definition", lambda *args: None)
    monkeypatch.setattr(execution, "validate_parameters", lambda *args: None)
    with observation_context(store, model_ref, 7, "design-op", project_id="project-a"):
        design = execution.op_experiment_design(object(), "model-a", {"definition": _definition()})
    assert design["kind"] == "w21experiment"
    assert design["model_revision"] == 8
    assert design["project_id"] == "project-a"
    assert [row["case_id"] for row in design["cases"]] == ["case-0001", "case-0002", "case-0003"]
    store.close()

    store = OperationStore(db)  # durable record remains resolvable after restart
    calls = []

    def fake_case(worker, tag, study, definition, values, budget, case_id, *,
                  metric_definitions=None, experiment_binding=None):
        assert isinstance(experiment_binding, dict)
        assert isinstance(experiment_binding.get("attempt_id"), str)
        attempt = store.get_metadata(
            "artifacts",
            "w21experimentattempt:" + experiment_binding["experiment_id"] + ":" + experiment_binding["case_id"],
        )
        assert attempt["attempt_id"] == experiment_binding["attempt_id"]
        calls.append(case_id)
        budget.cases_evaluated += 1
        if len(calls) == 2:
            budget.cases_failed += 1
            budget.total_failures += 1
            return {"status": "UNKNOWN", "execution_state_unknown": True, "case_id": case_id,
                    "case_attempt_id": experiment_binding["attempt_id"]}
        return {"status": "COMPLETED", "case_id": case_id, "peak": 301.0,
                "case_attempt_id": experiment_binding["attempt_id"]}

    monkeypatch.setattr(execution, "execute_case", fake_case)
    with observation_context(store, model_ref, 8, "run-op", project_id="project-a"):
        result = execution.op_experiment_run(object(), "model-a", {"experiment_id": design["experiment_id"]})
        assert result["completion_status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        with pytest.raises(ExecutionContractError, match="already has a run claim"):
            execution.op_experiment_run(object(), "model-a", {"experiment_id": design["experiment_id"]})
    assert len(calls) == 2
    run = store.get_metadata("artifacts", "w21experimentrun:" + design["experiment_id"])
    assert run["status"] == "EXECUTION_STATE_UNKNOWN"
    assert store.get_metadata("artifacts", "w21experimentcase:" + design["experiment_id"] + ":case-0003") is None
    store.close()


@pytest.mark.parametrize("ctx_patch", [
    {"project_id": "project-b"},
    {"model_ref": _identity(model="model-b")},
    {"revision": 9},
])
def test_experiment_run_refuses_wrong_project_model_or_revision(tmp_path, monkeypatch, ctx_patch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    monkeypatch.setattr(execution, "validate_definition", lambda *args: None)
    monkeypatch.setattr(execution, "validate_parameters", lambda *args: None)
    with observation_context(store, _identity(), 7, "design-op", project_id="project-a"):
        design = execution.op_experiment_design(object(), "model-a", {"definition": _definition()})
    base = {"project_id": "project-a", "model_ref": _identity(), "revision": 8}
    base.update(ctx_patch)
    with observation_context(store, base["model_ref"], base["revision"], "run-op", project_id=base["project_id"]):
        with pytest.raises(ExecutionContractError):
            execution.op_experiment_run(object(), "model-a", {"experiment_id": design["experiment_id"]})
    assert store.get_metadata("artifacts", "w21experimentrun:" + design["experiment_id"]) is None
    store.close()


@pytest.mark.parametrize("resources,timeout", [
    ({"max_cases": 4}, None),
    ({"max_wall_time_s": 121}, None),
    ({"max_cases": True}, None),
    ({"max_wall_time_s": 999}, 1),
    ({"max_wall_time_s": True}, 1),
    ({}, 121),
    ({}, True),
])
def test_experiment_overrides_cannot_exceed_frozen_budget(tmp_path, monkeypatch, resources, timeout):
    store = OperationStore(tmp_path / "operations.sqlite3")
    monkeypatch.setattr(execution, "validate_definition", lambda *args: None)
    monkeypatch.setattr(execution, "validate_parameters", lambda *args: None)
    with observation_context(store, _identity(), 1, "design-op", project_id="project-a"):
        design = execution.op_experiment_design(object(), "model-a", {"definition": _definition()})
    with observation_context(store, _identity(), 2, "run-op", project_id="project-a"):
        with pytest.raises(ExecutionContractError):
            execution.op_experiment_run(object(), "model-a", {
                "experiment_id": design["experiment_id"], "resources": resources, "timeout_s": timeout,
            })
    assert store.get_metadata("artifacts", "w21experimentrun:" + design["experiment_id"]) is None
    store.close()


def test_experiment_run_budget_exhaustion_is_partial_not_complete(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    monkeypatch.setattr(execution, "validate_definition", lambda *args: None)
    monkeypatch.setattr(execution, "validate_parameters", lambda *args: None)
    with observation_context(store, _identity(), 1, "design-op", project_id="project-a"):
        design = execution.op_experiment_design(object(), "model-a", {"definition": _definition()})

    calls = []

    def fake_case(worker, tag, study, definition, values, budget, case_id, *,
                  metric_definitions=None, experiment_binding=None):
        assert isinstance(experiment_binding, dict)
        calls.append(case_id)
        budget.cases_evaluated += 1
        return {"status": "COMPLETED", "case_id": case_id, "peak": 301.0,
                "case_attempt_id": experiment_binding["attempt_id"]}

    monkeypatch.setattr(execution, "execute_case", fake_case)
    with observation_context(store, _identity(), 2, "run-op", project_id="project-a"):
        result = execution.op_experiment_run(object(), "model-a", {
            "experiment_id": design["experiment_id"], "resources": {"max_cases": 1},
        })
    assert calls == [design["experiment_id"] + ":case-0001"]
    assert result["completion_status"] == "BUDGET_EXHAUSTED"
    assert result["status"] == "PARTIAL"
    assert len(result["cases"]) == 1
    from comsol_mcp._domain_outcome import classify
    assert classify("experiment.run", result).state == "partial"
    assert store.get_metadata("artifacts", "w21experimentrun:" + design["experiment_id"])["status"] == "PARTIAL"
    store.close()
