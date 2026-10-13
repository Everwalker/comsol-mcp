from __future__ import annotations

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore


class Collection:
    def __init__(self, rows):
        self.rows = dict(rows)

    def tags(self):
        return list(self.rows)

    def get(self, tag):
        return self.rows[tag]


class PropertyNode:
    def __init__(self, type_id, properties, *, allowed=None, initial=None):
        self.type_id = type_id
        self.values = dict(initial or {})
        self.value_types = dict(properties)
        self.allowed = dict(allowed or {})
        self.set_calls = []

    def getType(self):
        return self.type_id

    def type(self):
        return self.type_id

    def properties(self):
        return list(self.value_types)

    def getValueType(self, name):
        return self.value_types[name]

    def getAllowedPropertyValues(self, name):
        return self.allowed.get(name)

    def set(self, name, value):
        self.set_calls.append(name)
        self.values[name] = value["data"]

    def getString(self, name):
        value = self.values.get(name)
        return value if isinstance(value, str) else ""

    def getDouble(self, name):
        return float(self.values.get(name, 0.0))

    def getIntArray(self, name):
        return list(self.values.get(name, []))


class SolverSequence:
    def __init__(self, study="source", *, empty=False, initialized=True, default_solnum=1, features=None):
        self.study_tag = study
        self.empty = empty
        self.initialized = initialized
        self.default_solnum = default_solnum
        self.features = Collection(features or {})
        self.clear_calls = 0

    def feature(self, tag=None):
        return self.features if tag is None else self.features.get(tag)

    def isEmpty(self):
        return self.empty

    def isInitialized(self):
        return self.initialized

    def getDefaultSolnum(self):
        return self.default_solnum

    def study(self):
        return self.study_tag

    def clearSolutionData(self):
        self.clear_calls += 1
        self.empty = True
        self.default_solnum = 0


class Study:
    def __init__(self, features):
        self.features = Collection(features)

    def feature(self, tag=None):
        return self.features if tag is None else self.features.get(tag)


class Model:
    def __init__(self, solvers=None, studies=None):
        self.solvers = Collection(solvers or {})
        self.studies = dict(studies or {})

    def sol(self, tag=None):
        return self.solvers if tag is None else self.solvers.get(tag)

    def study(self, tag=None):
        return Collection(self.studies) if tag is None else self.studies[tag]


class Client:
    def __init__(self, model):
        self._model = model

    def model(self, _tag):
        return self._model


class Worker:
    def __init__(self, model):
        self._client = Client(model)

    def client(self):
        return self._client


def _indices(*, outers=(1,), params=None, solution="solsrc"):
    pairs = []
    by_pair = {}
    for outer in outers:
        for inner, time_value in ((1, 0.0), (2, 1.0)):
            solnum = len(pairs) + 1
            pairs.append({"outer": outer, "inner": inner, "solnum": solnum})
            by_pair[f"{outer}:{inner}"] = {
                "names": ["t", "alpha"], "values": [time_value, float(outer * 10 + inner)],
                "units": ["s", "K"], "solnum": solnum,
            }
    if params is not None:
        by_pair = params
    return {
        "dataset": "dset1", "solution": solution, "study": "source",
        "binding_complete": True, "axis_metadata_complete": True, "pair_mapping_complete": True,
        "inner_indices": [1, 2], "outer_indices": list(outers),
        "solnum_pairs": pairs, "solution_count": len(pairs), "time_values": [0.0, 1.0],
        "parameters": {"by_pair": by_pair}, "parameters_complete": True,
        "dataset_binding": {"dataset": "dset1", "solution": solution, "binding_complete": True},
        "read_errors": [],
    }


def _direct_node(linsolver="mumps"):
    return PropertyNode(
        "Direct",
        {"linsolver": "String", "ooc": "String", "oocmemory": "Double",
         "memfracooc": "Double", "mumpsalloc": "Double"},
        allowed={"ooc": ["automatic", "on", "off"]},
        initial={"linsolver": linsolver, "ooc": "automatic", "oocmemory": 512.0,
                 "memfracooc": 0.99, "mumpsalloc": 1.2},
    )


def _study_step():
    return PropertyNode(
        "Stationary",
        {"useinitsol": "String", "initmethod": "String", "initstudy": "String",
         "solnum": "String", "manualsolnum": "IntArray"},
        allowed={"useinitsol": ["on", "off"], "initmethod": ["init", "sol"],
                 "solnum": ["auto", "all", "first", "last", "interp", "manual"]},
        initial={"useinitsol": "off", "initmethod": "init", "initstudy": "",
                 "solnum": "auto", "manualsolnum": [1]},
    )


def _state_model(*, direct=None, target=None):
    source = SolverSequence("source", features={"d1": direct} if direct else {})
    solvers = {"solsrc": source}
    studies = {"source": Study({})}
    if target:
        studies["target"] = Study({"step1": target})
    return Model(solvers, studies), source


def test_solution_inspect_filters_exact_time_pair_and_reports_state(monkeypatch):
    sequence = SolverSequence("source", empty=False, initialized=True, default_solnum=1)
    model = Model({"solsrc": sequence})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices())

    result = ops.solver_solution_inspect(Worker(model), "m1", {
        "solution": {"dataset": "dset1", "solution": "solsrc", "time": [{"value": 1, "unit": "s"}]},
    })

    assert result["status"] == "AVAILABLE"
    assert result["selection_status"] == "MATCHED"
    assert result["selected_pairs"] == [{"outer": 1, "inner": 2, "solnum": 2}]
    assert result["solution_state"]["values"]["getDefaultSolnum"] == 1


def test_solution_inspect_intersects_exact_outer_inner_time_and_parameter_axes(monkeypatch):
    model = Model({"solsrc": SolverSequence("source")})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices(outers=(1, 2)))

    result = ops.solver_solution_inspect(Worker(model), "m1", {
        "solution": {
            "dataset": "dset1", "outer": [2], "inner": [1],
            "time": [{"value": 0, "unit": "s"}],
            "parameters": {"alpha": {"value": 21, "unit": "K"}},
        },
    })

    assert result["selection_status"] == "MATCHED"
    assert result["selected_pairs"] == [{"outer": 2, "inner": 1, "solnum": 3}]


def test_solution_inspect_never_deduplicates_duplicate_solution_info_pairs(monkeypatch):
    model = Model({"solsrc": SolverSequence("source")})
    indices = _indices()
    indices["solnum_pairs"] = indices["solnum_pairs"] + [dict(indices["solnum_pairs"][0])]
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: indices)

    result = ops.solver_solution_inspect(Worker(model), "m1", {
        "solution": {"dataset": "dset1"},
    })

    assert result["selection_status"] == "UNVERIFIED"
    assert result["selected_pairs"] == []
    assert any("duplicate outer/inner pair" in reason for reason in result["selection_unresolved"])


def test_solution_inspect_does_not_project_default_outer_pvals_onto_pair_time(monkeypatch):
    model = Model({"solsrc": SolverSequence("source")})
    indices = _indices()
    indices["time_values"] = [0.0, 1.0]
    indices["parameters"]["by_pair"] = {
        key: {"names": ["alpha"], "values": [row["values"][1]],
              "units": ["K"], "solnum": row["solnum"]}
        for key, row in indices["parameters"]["by_pair"].items()
    }
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: indices)

    result = ops.solver_solution_inspect(Worker(model), "m1", {
        "solution": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}]},
    })

    assert result["time_values"] == [0.0, 1.0]
    assert result["selection_status"] == "UNVERIFIED"
    assert result["selected_pairs"] == []
    assert any("exact named, unit-bearing SolutionInfo value" in reason
               for reason in result["selection_unresolved"])


def test_solution_inspect_does_not_invent_availability_when_binding_is_missing(monkeypatch):
    model = Model({"solsrc": SolverSequence(empty=False, initialized=True)})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    incomplete = {**_indices(), "binding_complete": False, "solnum_pairs": [], "solution_count": 0}
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: incomplete)

    result = ops.solver_solution_inspect(Worker(model), "m1", {"solution": {"dataset": "dset1"}})

    assert result["status"] == "UNVERIFIED"
    assert result["selection_status"] == "UNVERIFIED"
    assert result["selected_pairs"] == []


def test_solution_clear_changes_only_exact_requested_solver_sequence(monkeypatch):
    sequence = SolverSequence(empty=False, initialized=True)
    other = SolverSequence(empty=False, initialized=True)
    model = Model({"solsrc": sequence, "solother": other})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)

    result = ops.solver_solution_clear(Worker(model), "m1", {
        "path": {"segments": [{"collection": "sol", "tag": "solsrc"}]},
        "scope": "solution_data",
    })

    assert result["status"] == "CLEARED"
    assert result["after"]["values"]["isInitialized"] is True
    assert sequence.clear_calls == 1
    assert other.clear_calls == 0
    assert result["cache_state"] == "NOT_CLEARED_OR_OBSERVED"
    assert result["result_features"] == "documented_unaffected"


@pytest.mark.parametrize("path,scope", [
    ({"segments": [{"collection": "sol", "tag": "solsrc"}, {"collection": "feature", "tag": "s1"}]}, "solution_data"),
    ({"segments": [{"collection": "sol", "tag": "solsrc"}]}, "cache"),
])
def test_solution_clear_rejects_effect_outside_exact_documented_scope(monkeypatch, path, scope):
    sequence = SolverSequence()
    model = Model({"solsrc": sequence})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)

    with pytest.raises(ExecutionContractError) as exc:
        ops.solver_solution_clear(Worker(model), "m1", {"path": path, "scope": scope})

    assert exc.value.code == "API_UNSUPPORTED"
    assert sequence.clear_calls == 0


def test_solution_clear_preserves_unknown_receipt_if_remote_dispatch_fails(monkeypatch):
    class FailedClear(SolverSequence):
        def clearSolutionData(self):
            self.clear_calls += 1
            raise RuntimeError("worker reply lost after dispatch")

    sequence = FailedClear(empty=False, initialized=True)
    model = Model({"solsrc": sequence})
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)

    result = ops.solver_solution_clear(Worker(model), "m1", {
        "path": {"segments": [{"collection": "sol", "tag": "solsrc"}]},
        "scope": "solution_data",
    })

    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["clear_dispatched"] is True
    assert result["execution_state_unknown"] is True
    assert result["cache_state"] == "NOT_CLEARED_OR_OBSERVED"
    assert sequence.clear_calls == 1


def test_resource_configure_sets_direct_solver_properties_and_reads_back(monkeypatch):
    direct = _direct_node()
    model, _ = _state_model(direct=direct)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)

    result = ops.solver_resource_configure(Worker(model), "m1", {
        "scope": "direct_solver",
        "resources": {"path": {"segments": [
            {"collection": "sol", "tag": "solsrc"}, {"collection": "feature", "tag": "d1"},
        ]}, "settings": {"ooc": "on", "oocmemory": 2048, "mumpsalloc": 1.4}},
    })

    assert result["status"] == "APPLIED"
    assert result["linear_solver"] == "mumps"
    assert [row["name"] for row in result["applied"]] == ["mumpsalloc", "ooc", "oocmemory"]
    assert all(row["readback_match"] is True for row in result["applied"])
    assert direct.values["ooc"] == "on"
    assert direct.values["oocmemory"] == 2048.0
    assert direct.values["mumpsalloc"] == 1.4
    assert result["process_resources"] == "NOT_CHANGED"


def test_resource_configure_returns_unknown_partial_receipt_on_readback_mismatch(monkeypatch):
    class MismatchDirect(PropertyNode):
        def getDouble(self, name):
            value = super().getDouble(name)
            return value + 1.0 if name == "mumpsalloc" else value

    direct = MismatchDirect(
        "Direct",
        {"linsolver": "String", "ooc": "String", "oocmemory": "Double",
         "memfracooc": "Double", "mumpsalloc": "Double"},
        allowed={"ooc": ["automatic", "on", "off"]},
        initial={"linsolver": "mumps", "ooc": "automatic", "oocmemory": 512.0,
                 "memfracooc": 0.99, "mumpsalloc": 1.2},
    )
    model, _ = _state_model(direct=direct)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)

    result = ops.solver_resource_configure(Worker(model), "m1", {
        "scope": "direct_solver",
        "resources": {"path": {"segments": [
            {"collection": "sol", "tag": "solsrc"}, {"collection": "feature", "tag": "d1"},
        ]}, "settings": {"ooc": "on", "mumpsalloc": 1.4}},
    })

    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert [row["name"] for row in result["failed"]] == ["mumpsalloc"]
    assert [row["name"] for row in result["not_executed"]] == ["ooc"]
    assert direct.set_calls == ["mumpsalloc"]


def test_resource_configure_refuses_process_fields_and_wrong_solver_without_setters(monkeypatch):
    direct = _direct_node(linsolver="cudss")
    model, _ = _state_model(direct=direct)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    path = {"segments": [{"collection": "sol", "tag": "solsrc"}, {"collection": "feature", "tag": "d1"}]}

    with pytest.raises(ExecutionContractError) as process:
        ops.solver_resource_configure(Worker(model), "m1", {"scope": "process", "resources": {"cores": 8}})
    assert process.value.code == "API_UNSUPPORTED"
    with pytest.raises(ExecutionContractError) as wrong_solver:
        ops.solver_resource_configure(Worker(model), "m1", {
            "scope": "direct_solver", "resources": {"path": path, "settings": {"oocmemory": 2048}},
        })
    assert wrong_solver.value.code == "API_UNSUPPORTED"
    assert direct.set_calls == []


@pytest.mark.parametrize("settings", [
    {"ooc": "sometimes"},
    {"oocmemory": 0},
    {"memfracooc": 1.01},
    {"mumpsalloc": 0.99},
])
def test_resource_configure_rejects_invalid_values_before_any_setter(monkeypatch, settings):
    direct = _direct_node()
    model, _ = _state_model(direct=direct)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    path = {"segments": [{"collection": "sol", "tag": "solsrc"},
                         {"collection": "feature", "tag": "d1"}]}

    with pytest.raises(ExecutionContractError):
        ops.solver_resource_configure(Worker(model), "m1", {
            "scope": "direct_solver", "resources": {"path": path, "settings": settings},
        })

    assert direct.set_calls == []


def test_initial_solution_set_keeps_source_pair_identity_and_reads_back(monkeypatch):
    target = _study_step()
    model, source_solver = _state_model(target=target)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices())

    result = ops.study_initial_solution_set(Worker(model), "m1", {
        "step": {"segments": [{"collection": "study", "tag": "target"},
                              {"collection": "feature", "tag": "step1"}]},
        "source": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}]},
        "mapping": {},
    })

    assert result["status"] == "APPLIED"
    assert result["source"]["study"] == "source"
    assert result["source"]["inner"] == 2
    assert result["source"]["solnum"] == 2
    assert target.values["initstudy"] == "source"
    assert target.values["manualsolnum"] == [2]
    assert target.values["useinitsol"] == "on"
    assert result["solve_dispatched"] is False
    assert source_solver.clear_calls == 0


@pytest.mark.parametrize("selected_solution", ["solsrc", "solother"])
def test_initial_solution_set_refuses_ambiguous_same_study_solver_binding_before_write(
    monkeypatch, selected_solution,
):
    target = _study_step()
    model, _ = _state_model(target=target)
    model.solvers.rows["solother"] = SolverSequence("source")
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices(solution=selected_solution))

    with pytest.raises(ExecutionContractError) as exc:
        ops.study_initial_solution_set(Worker(model), "m1", {
            "step": {"segments": [{"collection": "study", "tag": "target"},
                                  {"collection": "feature", "tag": "step1"}]},
            "source": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}]},
        })

    assert exc.value.code == "API_UNSUPPORTED"
    assert len(exc.value.details["study_solver_associations"]) == 2
    assert target.set_calls == []


def test_initial_solution_set_refuses_incomplete_solver_study_metadata_before_write(monkeypatch):
    class UnreadableStudy(SolverSequence):
        def study(self):
            raise RuntimeError("study association unavailable")

    target = _study_step()
    model, _ = _state_model(target=target)
    model.solvers.rows["solunknown"] = UnreadableStudy("source")
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices())

    with pytest.raises(ExecutionContractError) as exc:
        ops.study_initial_solution_set(Worker(model), "m1", {
            "step": {"segments": [{"collection": "study", "tag": "target"},
                                  {"collection": "feature", "tag": "step1"}]},
            "source": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}]},
        })

    assert exc.value.code == "SOLUTION_AXIS_METADATA_UNAVAILABLE"
    assert exc.value.details["mutation_issued"] is False
    assert target.set_calls == []


def test_initial_solution_set_rejects_unrepresentable_outer_parameter_before_write(monkeypatch):
    target = _study_step()
    model, _ = _state_model(target=target)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices(outers=(1, 2)))

    with pytest.raises(ExecutionContractError) as exc:
        ops.study_initial_solution_set(Worker(model), "m1", {
            "step": {"segments": [{"collection": "study", "tag": "target"},
                                  {"collection": "feature", "tag": "step1"}]},
            "source": {"dataset": "dset1", "outer": [2], "inner": [1]},
        })
    assert exc.value.code == "API_UNSUPPORTED"
    assert target.set_calls == []


def test_initial_solution_set_requires_one_pair_and_refuses_variable_mapping(monkeypatch):
    target = _study_step()
    model, _ = _state_model(target=target)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices())
    request = {
        "step": {"segments": [{"collection": "study", "tag": "target"},
                              {"collection": "feature", "tag": "step1"}]},
        "source": {"dataset": "dset1"},
    }
    with pytest.raises(ExecutionContractError) as ambiguous:
        ops.study_initial_solution_set(Worker(model), "m1", request)
    assert ambiguous.value.code == "SOLUTION_SELECTION_AMBIGUOUS"
    with pytest.raises(ExecutionContractError) as mapping:
        ops.study_initial_solution_set(Worker(model), "m1", {**request, "mapping": {"u": "v"}})
    assert mapping.value.code == "API_UNSUPPORTED"
    assert target.set_calls == []


def test_initial_solution_set_distinguishes_no_matching_source_from_unreadable_axes(monkeypatch):
    target = _study_step()
    model, _ = _state_model(target=target)
    monkeypatch.setattr(ops, "bound_model", lambda *_: model)
    monkeypatch.setattr(ops, "dataset_solution_indices", lambda *_: _indices())

    with pytest.raises(ExecutionContractError) as no_match:
        ops.study_initial_solution_set(Worker(model), "m1", {
            "step": {"segments": [{"collection": "study", "tag": "target"},
                                  {"collection": "feature", "tag": "step1"}]},
            "source": {"dataset": "dset1", "time": [{"value": 90, "unit": "s"}]},
        })

    assert no_match.value.code == "SOLUTION_NOT_FOUND"
    assert no_match.value.details["mutation_issued"] is False
    assert target.set_calls == []


def test_sample_grid_reuses_at_points_adapter_and_preserves_cartesian_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "result_at_points",
                        lambda worker, tag, args: calls.append(args) or {"status": "VERIFIED", "values": [[1, 2, 3, 4]]})
    result = ops.result_sample_grid(object(), "m1", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}, "complex_mode": "real"},
        "grid": {"axes": [[0, 1], [2, 4]], "coordinate_unit": "mm"},
    })

    assert result["grid"]["shape"] == [2, 2]
    assert result["grid"]["point_count"] == 4
    assert calls[0]["points"] == [[0.0, 2.0], [0.0, 4.0], [1.0, 2.0], [1.0, 4.0]]
    assert calls[0]["frame"] == "spatial"
    assert result["scientific_acceptance"] == "NOT_EVALUATED"


def test_sample_grid_does_not_promote_adapter_readback_to_scientific_acceptance(monkeypatch):
    monkeypatch.setattr(ops, "result_at_points", lambda *_: {
        "status": {"ok": True, "verification_status": "PASSED"},
        "verification_status": "PASSED", "values": [],
    })

    result = ops.result_sample_grid(object(), "m1", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
        "grid": {"axes": [[0, 1]]},
    })

    assert result["status"] == "SAMPLED"
    assert result["verification_status"] == "PASSED"
    assert result["scientific_acceptance"] == "NOT_EVALUATED"


def test_sample_grid_rejects_nonregular_or_unsupported_spec_before_adapter(monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "result_at_points", lambda *args: calls.append(args))
    base = {"spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
            "grid": {"axes": [[0, 1, 3]]}}
    with pytest.raises(ExecutionContractError):
        ops.result_sample_grid(object(), "m1", base)
    with pytest.raises(ExecutionContractError):
        ops.result_sample_grid(object(), "m1", {
            **base, "spec": {**base["spec"], "selection": {"kind": "all"}},
        })
    assert calls == []


def _new_project_job(store, project="p1", result=None):
    record, reused = store.begin(
        request_id="request-" + project, idempotency_key="key-" + project,
        request_hash="hash-" + project, operation="study.run",
        metadata={"operation": "study.run", "arguments": {},
                  "execution": {"project_id": project, "session_id": "s1"}},
    )
    assert not reused
    store.update_job(record["job_id"], "RUNNING")
    store.add_event(record["job_id"], "RUNNING", {"phase": "queued"})
    store.add_event(record["job_id"], "SolverStarted", {"study": "std1"})
    if result is not None:
        store.finish(record["operation_id"], status="FAILED", result=result)
    else:
        store.add_event(record["job_id"], "SolverProgress", {"percent": 20})
    return record


def test_solver_log_read_is_project_bound_and_cursor_freezes_event_prefix(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    record = _new_project_job(store)

    first = ops.solver_log_read(store, "p1", {"job_id": record["job_id"], "limit": 2})
    assert [event["event"] for event in first["events"]] == ["RUNNING", "SolverStarted"]
    assert first["next_cursor"]
    assert first["solver_output_log"]["status"] == "NOT_CAPTURED"

    store.add_event(record["job_id"], "LaterEvent", {"phase": "after-page"})
    second = ops.solver_log_read(store, "p1", {"job_id": record["job_id"], "cursor": first["next_cursor"]})
    assert [event["event"] for event in second["events"]] == ["SolverProgress"]
    assert second["next_cursor"] is None
    with pytest.raises(ExecutionContractError) as mismatch:
        ops.solver_log_read(store, "p2", {"job_id": record["job_id"]})
    assert mismatch.value.code == "PROJECT_IDENTITY_MISMATCH"
    store.close()


def test_solver_log_read_reports_stored_exception_chain_but_no_fabricated_solver_output(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    record = _new_project_job(store, result={"success": False, "error": {
        "code": "SOLVER_FAILED", "message": "engine reported a solve error",
        "cause_chain": [{"type": "SolverException", "message": "stored cause"}],
    }})

    response = ops.solver_log_read(store, "p1", {"job_id": record["job_id"]})
    assert response["exception_chain"]["status"] == "CAPTURED"
    assert response["exception_chain"]["items"][0]["type"] == "SolverException"
    assert response["solver_output_log"]["status"] == "NOT_CAPTURED"
    store.close()
