from types import SimpleNamespace

import pytest

from comsol_mcp import _g3_results as results
from comsol_mcp import _w21_execution as execution
from comsol_mcp._execution_contract import ExecutionContractError


class Collection:
    def __init__(self, nodes): self.nodes = nodes
    def tags(self): return list(self.nodes)
    def get(self, tag): return self.nodes[tag]


class SolutionInfo:
    def __init__(self, rows): self.rows = rows
    def getOuterSolnum(self): return [4]
    def getSolnum(self, outer, strict):
        assert (outer, strict) == (4, True)
        return [row["inner"] for row in self.rows]
    def getPNames(self, pairs): return [self._row(pair)["names"] for pair in pairs]
    def getPvals(self, pairs): return [self._row(pair)["values"] for pair in pairs]
    def getUnits(self, pairs): return [self._row(pair)["units"] for pair in pairs]
    def _row(self, pair): return next(row for row in self.rows if pair == [4, row["inner"]])


class Variables:
    def __init__(self): self.values = {}; self.set_calls = []
    def getType(self): return "Variables"
    def set(self, name, value): self.set_calls.append((name, value)); self.values[name] = value
    def getString(self, name): return self.values[name]
    def getInt(self, name): return self.values[name]


class Solver:
    def __init__(self, study, info=None, variables=None):
        self._study, self._info = study, info
        self._features = Collection({"v1": variables} if variables else {})
    def study(self): return self._study
    def getPVals(self): return [0.25, 0.75]
    def getSolutioninfo(self): return self._info
    def feature(self): return self._features


class Model:
    def __init__(self, info):
        self.variables = Variables()
        self.solvers = Collection({
            "sol_src": Solver("std_src", info),
            "sol_dst": Solver("std_dst", variables=self.variables),
        })
        self.datasets = Collection({"dset_src": object()})
    def sol(self, tag=None): return self.solvers if tag is None else self.solvers.get(tag)
    def result(self): return SimpleNamespace(dataset=lambda: self.datasets)


class EvalFeature:
    def __init__(self, dataset):
        self.values = {"data": dataset, "outerinput": "all", "outersolnum": 1,
                       "innerinput": "all", "solnum": 1}
        self.set_calls = []
    def set(self, name, value): self.set_calls.append((name, value)); self.values[name] = value
    def getString(self, name): return self.values[name]
    def getInt(self, name): return self.values[name]


def install_mapper_fakes(monkeypatch, rows):
    info = SolutionInfo(rows)
    model = Model(info)
    binding = {"binding_complete": True, "solution": "sol_src", "component": "comp1",
               "geometry": "geom1", "dataset_type": "Solution"}
    monkeypatch.setattr(results, "bound_model", lambda *_args: model)
    monkeypatch.setattr(execution, "bound_model", lambda *_args: model)
    monkeypatch.setattr(results, "_resolve_dataset_binding", lambda *_args, **_kw: binding)
    monkeypatch.setattr(execution, "_resolve_dataset_binding", lambda *_args, **_kw: binding)
    monkeypatch.setattr(results, "_study_steps", lambda *_args: ([], None))
    monkeypatch.setattr(results, "_solver_sequence_parameter_observation",
                        lambda *_args, **_kw: {"read_errors": []})
    return model


def prepare_state_map_operation(monkeypatch, rows):
    # The production dataset_solution_indices mapper reads this fake SolutionInfo.
    model = install_mapper_fakes(monkeypatch, rows)
    assert execution.dataset_solution_indices is results.dataset_solution_indices
    temporary_features = []

    def fake_result_evaluate(_worker, _model_tag, arguments, *, strict_field_readback=False):
        assert strict_field_readback is True
        spec = arguments["spec"]
        feature = EvalFeature(spec["solution"]["dataset"])
        temporary_features.append(feature)
        feature.set("data", spec["solution"]["dataset"])
        feature.set("expr", spec["expressions"])
        selectors = results._set_strict_eval_selectors(feature, "dset_src", 4, 2)
        return {
            "strict_field_readback": {
                "status": "VERIFIED", "solution_tuple": {"outer": 4, "inner": 2, "solnum": 2},
                "selector_readback": selectors, "selection_readback": {"geometry": "geom1"},
            },
            "status": {"ok": True},
            "cleanup": {"created": True, "removed": True, "cleanup_failed": False},
        }

    monkeypatch.setattr(execution, "result_evaluate", fake_result_evaluate)
    monkeypatch.setattr(execution, "_coordinate_context", lambda *_a, **_k: {
        "component": "comp1", "geometry": "geom1", "space_dimension": 2,
    })
    monkeypatch.setattr(execution, "_attached_solver_sequence_evidence", lambda *_a: {
        "status": "VERIFIED", "study": "std_dst", "solver": "sol_dst", "is_attached": True,
        "unique_attached_solver_tags": ["sol_dst"], "readback_methods": [],
    })
    monkeypatch.setattr(execution, "_read_source_solution_mesh_association", lambda *_a: {
        "status": "VERIFIED", "mesh_tag": "mesh_src",
    })
    args = {
        "source": {"dataset": "dset_src", "solution": "sol_src", "outer": 4,
                   "time": [{"value": 0.25, "unit": "s"}]},
        "target": {"segments": [{"collection": "sol", "tag": "sol_dst"},
                                {"collection": "feature", "tag": "v1"}]},
        "mapping": {"profile": "same_name_same_mesh_initialization", "variables": [{
            "source_variable": "T", "target_variable": "T", "source_unit": "K",
            "target_unit": "K", "mapping_method": "identity",
        }]},
    }
    return model, temporary_features, args


@pytest.mark.parametrize("bad_value,bad_unit", [
    (0.25, None),
    (0.25, ""),
    (True, "s"),
    (float("nan"), "s"),
])
def test_state_map_rejects_invalid_time_alias_before_temporary_eval_write(monkeypatch, bad_value, bad_unit):
    rows = [
        {"inner": 2, "names": ["t", "time"], "values": [0.25, bad_value], "units": ["s", bad_unit]},
        {"inner": 5, "names": ["time"], "values": [0.75], "units": ["s"]},
    ]
    model, temporary_features, args = prepare_state_map_operation(monkeypatch, rows)

    with pytest.raises(ExecutionContractError):
        execution.op_experiment_state_map(object(), "model-a", args)

    assert temporary_features == []
    assert model.variables.set_calls == []


def test_state_map_keeps_t_distinct_from_temperature_and_accepts_multiple_parameters(monkeypatch):
    rows = [
        {"inner": 2, "names": ["t", "T", "heat"], "values": [0.25, 310.0, 3.0],
         "units": ["s", "K", "W"]},
        {"inner": 5, "names": ["time", "T", "heat"], "values": [0.75, 315.0, 4.0],
         "units": ["s", "K", "W"]},
    ]
    model, temporary_features, args = prepare_state_map_operation(monkeypatch, rows)

    result = execution.op_experiment_state_map(object(), "model-a", args)

    assert result["status"] == "APPLIED"
    assert result["source"]["inner"] == 2
    assert result["source"]["manualsolnum"] == 2
    assert len(temporary_features) == 1
    assert ("outerinput", "manual") in temporary_features[0].set_calls
    assert ("solnum", 2) in temporary_features[0].set_calls
    assert model.variables.set_calls
