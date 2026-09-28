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
    assert EFFECTS["solver.solution_transfer"] == "WRITE"
    assert EFFECTS["stage.state_transfer"] == "COMPUTE"
    assert EFFECTS["optimization.bounded_run"] == "COMPUTE"
    assert EFFECTS["experiment.design"] == "STATE_WRITE"
    assert EFFECTS["experiment.run"] == "COMPUTE"
    assert {"solver.solution_transfer", "stage.state_transfer", "experiment.design",
            "experiment.run", "optimization.bounded_run"}.issubset(REQUIRES_ISOLATION)
    assert ALIASES["stage_state_transfer"] == "stage.state_transfer"
    assert ALIASES["optimization_bounded_run"] == "optimization.bounded_run"
    assert ALIASES["solver_solution_transfer"] == "solver.solution_transfer"
    assert ALIASES["experiment_design"] == "experiment.design"
    assert ALIASES["experiment_run"] == "experiment.run"


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
    def __init__(self, study, features=None):
        self.study_tag = study
        self.features = _Collection(features or {})
        self.run_calls = 0

    def study(self):
        return self.study_tag

    def feature(self):
        return self.features

    def run(self):
        self.run_calls += 1


class _Model:
    def __init__(self, solvers):
        self.solvers = _Collection(solvers)

    def sol(self, tag=None):
        return self.solvers if tag is None else self.solvers.get(tag)


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


def _bound_indices():
    return {
        "dataset": "dset1", "solution": "sol2", "binding_complete": True,
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
