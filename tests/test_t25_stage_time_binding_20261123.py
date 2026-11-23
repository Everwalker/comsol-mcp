"""Synthetic regressions for exact source-time tuple binding (T25)."""

import pytest

from comsol_mcp import _w21_execution as execution
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._solution_binding import SolutionBinding


def _indices_with_unknown_candidate_time(*, include_temperature=False):
    """Build partial per-tuple metadata through the production SolutionInfo mapper."""
    class _SolutionInfo:
        def getOuterSolnum(self):
            return [4]

        def getSolnum(self, outer, strict):
            assert (outer, strict) == (4, True)
            return [2, 5]

        def getPNames(self, pairs):
            assert pairs == [[4, 2], [4, 5]]
            if include_temperature:
                return [["t", "T"], ["t", "T"]]
            return [["t"], []]

        def getPvals(self, pairs):
            assert pairs == [[4, 2], [4, 5]]
            if include_temperature:
                return [[0.25, 310.0], [0.75, 315.0]]
            return [[0.25], [0.75]]

        def getUnits(self, pairs):
            assert pairs == [[4, 2], [4, 5]]
            if include_temperature:
                return [["s", "K"], ["s", "K"]]
            return [["s"], ["s"]]

    binding = SolutionBinding.resolve_solution_info(_SolutionInfo())
    by_pair = {}
    for pair in binding["solnum_pairs"]:
        key = (pair["outer"], pair["inner"])
        by_pair[f"{pair['outer']}:{pair['inner']}"] = {
            "names": binding["parameter_names_by_pair"][key],
            "values": binding["parameter_values_by_pair"][key],
            "units": binding["parameter_units_by_pair"][key],
            "solnum": pair["solnum"],
        }
    return {
        "dataset": "dset_synthetic_source",
        "solution": "sol_synthetic_source",
        "binding_complete": True,
        "pair_mapping_complete": binding["pair_mapping_complete"],
        # Mirrors dataset_solution_indices: this is true when any values exist.
        "parameters_complete": bool(binding["parameter_values_by_pair"]),
        "parameters": {"by_pair": by_pair},
        "solnum_pairs": binding["solnum_pairs"],
    }


def _time_source():
    return {
        "dataset": "dset_synthetic_source",
        "solution": "sol_synthetic_source",
        "outer": 4,
        "time": [{"value": 0.25, "unit": "s"}],
    }


def _indices_with_time_and_temperature_names():
    """Build complete per-tuple metadata with distinct time and temperature names."""
    return _indices_with_unknown_candidate_time(include_temperature=True)


def test_state_map_time_requires_metadata_for_every_candidate_tuple():
    with pytest.raises(ExecutionContractError, match="time metadata"):
        execution._resolve_state_map_source_pair(
            _indices_with_unknown_candidate_time(), _time_source()
        )


def test_state_map_time_resolves_when_all_candidate_tuple_metadata_is_complete():
    indices = _indices_with_unknown_candidate_time()
    indices["parameters"]["by_pair"]["4:5"] = {
        "names": ["time"], "values": [0.75], "units": ["s"], "solnum": 5,
    }

    assert execution._resolve_state_map_source_pair(indices, _time_source()) == {
        "dataset": "dset_synthetic_source", "solution": "sol_synthetic_source",
        "outer": 4, "inner": 2, "solnum": 2,
    }


def test_state_map_time_resolves_alongside_uppercase_temperature_parameter():
    assert execution._resolve_state_map_source_pair(
        _indices_with_time_and_temperature_names(), _time_source()
    ) == {
        "dataset": "dset_synthetic_source", "solution": "sol_synthetic_source",
        "outer": 4, "inner": 2, "solnum": 2,
    }


def test_state_map_time_rejects_parameter_row_bound_to_another_solnum():
    indices = _indices_with_unknown_candidate_time()
    indices["parameters"]["by_pair"]["4:2"]["solnum"] = 99
    indices["parameters"]["by_pair"]["4:5"] = {
        "names": ["time"], "values": [0.75], "units": ["s"], "solnum": 5,
    }

    with pytest.raises(ExecutionContractError, match="time metadata"):
        execution._resolve_state_map_source_pair(indices, _time_source())


class _Variables:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def getType(self):
        return "Variables"

    def set(self, name, value):
        self.set_calls.append((name, value))
        self.values[name] = value

    def getString(self, name):
        return self.values[name]

    def getInt(self, name):
        return self.values[name]


class _Collection:
    def __init__(self, nodes):
        self.nodes = nodes

    def tags(self):
        return list(self.nodes)

    def get(self, tag):
        return self.nodes[tag]


class _Solver:
    def __init__(self, study, features=None):
        self._study = study
        self._features = _Collection(features or {})

    def study(self):
        return self._study

    def feature(self):
        return self._features


class _Model:
    def __init__(self, variables):
        self.solvers = _Collection({
            "sol_synthetic_source": _Solver("study_source"),
            "sol_synthetic_target": _Solver("study_target", {"v_synthetic": variables}),
        })

    def sol(self, tag=None):
        return self.solvers if tag is None else self.solvers.get(tag)


def test_selector_write_refuses_time_match_when_another_tuple_is_unreadable(monkeypatch):
    variables = _Variables()
    model = _Model(variables)
    monkeypatch.setattr(execution, "dataset_solution_indices",
                        lambda *_args: _indices_with_unknown_candidate_time())
    monkeypatch.setattr(execution, "bound_model", lambda *_args: model)
    arguments = {
        "source": _time_source(),
        "target": {"segments": [
            {"collection": "sol", "tag": "sol_synthetic_target"},
            {"collection": "feature", "tag": "v_synthetic"},
        ]},
        "mapping": {},
    }

    with pytest.raises(ExecutionContractError, match="every candidate tuple"):
        execution.op_solver_solution_transfer(object(), "model_synthetic", arguments)

    assert variables.set_calls == []


def test_selector_write_refuses_parameter_row_for_a_different_solnum(monkeypatch):
    indices = _indices_with_unknown_candidate_time()
    indices["parameters"]["by_pair"]["4:2"]["solnum"] = 99
    indices["parameters"]["by_pair"]["4:5"] = {
        "names": ["time"], "values": [0.75], "units": ["s"], "solnum": 5,
    }
    variables = _Variables()
    model = _Model(variables)
    monkeypatch.setattr(execution, "dataset_solution_indices", lambda *_args: indices)
    monkeypatch.setattr(execution, "bound_model", lambda *_args: model)
    arguments = {
        "source": _time_source(),
        "target": {"segments": [
            {"collection": "sol", "tag": "sol_synthetic_target"},
            {"collection": "feature", "tag": "v_synthetic"},
        ]},
        "mapping": {},
    }

    with pytest.raises(ExecutionContractError, match="every candidate tuple"):
        execution.op_solver_solution_transfer(object(), "model_synthetic", arguments)

    assert variables.set_calls == []


def test_selector_write_resolves_time_alongside_uppercase_temperature_parameter(monkeypatch):
    variables = _Variables()
    model = _Model(variables)
    monkeypatch.setattr(execution, "dataset_solution_indices",
                        lambda *_args: _indices_with_time_and_temperature_names())
    monkeypatch.setattr(execution, "bound_model", lambda *_args: model)
    arguments = {
        "source": _time_source(),
        "target": {"segments": [
            {"collection": "sol", "tag": "sol_synthetic_target"},
            {"collection": "feature", "tag": "v_synthetic"},
        ]},
        "mapping": {},
    }

    result = execution.op_solver_solution_transfer(object(), "model_synthetic", arguments)

    assert result["source"]["inner"] == 2
    assert result["source"]["manualsolnum"] == 2
    assert result["initialization_readback"]["manualsolnum"] == 2
