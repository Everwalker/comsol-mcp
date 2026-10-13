import json
import threading
import time

import pytest

from comsol_mcp._model_ops import _coerce_eval_value, _evaluate_aggregate, _evaluate_expression_safely, _temporary_numerical_feature
from comsol_mcp._physics_ops import _create_variable, _list_variables, _set_physics_selection


class Selection:
    def __init__(self, inherited=False):
        self.inherited, self.mode, self.values = inherited, "", []

    def isInheriting(self): return self.inherited
    def inherit(self, value): self.inherited = value
    def set(self, values): self.mode, self.values = "explicit", list(values)
    def all(self): self.mode = "all"
    def named(self, value=None):
        if value is not None: self.mode, self.values = "named", value
        return self.values
    def entities(self): return self.values
    def geom(self, *_): pass


class Feature:
    def __init__(self, values=((3.0,),)):
        self.values, self.selection_obj = values, Selection()
    def set(self, *_): pass
    def setIndex(self, *_): pass
    def run(self): pass
    def getReal(self): return self.values
    def getData(self): return self.values
    def isComplex(self): return False
    def selection(self): return self.selection_obj


class Numerical:
    def __init__(self, remove_error=False):
        self.features = {"agg1": Feature(), "gev1": Feature(), "user_table_bound": Feature()}
        self.remove_error = remove_error
    def create(self, tag, _): self.features[tag] = Feature()
    def remove(self, tag):
        if self.remove_error: raise RuntimeError("remove denied")
        del self.features[tag]
    def __call__(self, tag): return self.features[tag]
    def tags(self): return list(self.features)


class Result:
    def __init__(self, numerical): self.numerical_obj = numerical
    def numerical(self, tag=None): return self.numerical_obj if tag is None else self.numerical_obj(tag)


class Model:
    def __init__(self, numerical):
        self.java = type("Java", (), {"result": lambda _self: Result(numerical)})()


def test_t005_owned_temp_nodes_preserve_user_nodes_and_cleanup_on_failure():
    numerical = Numerical()
    model = Model(numerical)
    assert _evaluate_expression_safely(model, "T") == [[3.0]]
    assert set(numerical.tags()) == {"agg1", "gev1", "user_table_bound"}
    with pytest.raises(RuntimeError, match="bad expression"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Feature, "run", lambda self: (_ for _ in ()).throw(RuntimeError("bad expression")))
            _evaluate_expression_safely(model, "invalid")
    assert set(numerical.tags()) == {"agg1", "gev1", "user_table_bound"}


def test_t033_preserves_primary_and_cleanup_errors():
    numerical = Numerical(remove_error=True)
    with pytest.raises(RuntimeError, match="Temporary numerical cleanup failed"):
        _evaluate_expression_safely(Model(numerical), "T")


class JavaArray:
    def __init__(self, values): self.values = values
    def __iter__(self): return iter(self.values)


def test_java_arrays_become_nested_lists_instead_of_strings():
    assert _coerce_eval_value(JavaArray([JavaArray([1.0, 2.0])])) == [[1.0, 2.0]]


def test_temp_tag_collision_never_removes_existing_user_node(monkeypatch):
    numerical = Numerical()
    collision = "mcp_eval_collision"
    numerical.features[collision] = Feature()
    monkeypatch.setattr("comsol_mcp._model_ops.uuid.uuid4", lambda: type("Id", (), {"hex": "collision"})())
    with pytest.raises(RuntimeError, match="allocate"):
        with _temporary_numerical_feature(Model(numerical), "EvalGlobal"):
            pass
    assert collision in numerical.features


class BadGlobalFeature(Feature):
    def run(self): raise RuntimeError("global cannot evaluate field")


class FallbackNumerical(Numerical):
    def create(self, tag, feature_type):
        self.features[tag] = BadGlobalFeature() if feature_type == "EvalGlobal" else Feature()


def test_spatial_eval_uses_owned_eval_getdata_after_evalglobal_failure():
    numerical = FallbackNumerical()
    assert _evaluate_expression_safely(Model(numerical), "u") == [[3.0]]
    assert set(numerical.tags()) == {"agg1", "gev1", "user_table_bound"}


def test_eval_diagnostics_retain_global_and_fallback_failures():
    class BothBad(FallbackNumerical):
        def create(self, tag, _): self.features[tag] = BadGlobalFeature()
    with pytest.raises(RuntimeError, match="EvalGlobal failed: global cannot evaluate field; Eval/getData fallback failed: global cannot evaluate field"):
        _evaluate_expression_safely(Model(BothBad()), "u")


def test_complex_value_has_real_and_imaginary_parts():
    class ComplexFeature(Feature):
        def isComplex(self): return True
        def getImag(self): return ((-2.0,),)
    class ComplexNumerical(Numerical):
        def create(self, tag, _): self.features[tag] = ComplexFeature()
    assert _evaluate_expression_safely(Model(ComplexNumerical()), "emw.Ex") == {"real": [[3.0]], "imag": [[-2.0]]}


def test_t005_aggregate_uses_unique_temp_tag_not_agg1(monkeypatch):
    numerical = Numerical()
    model = Model(numerical)
    monkeypatch.setattr("comsol_mcp._model_ops._get_model_dimension", lambda _: 2)
    assert _evaluate_aggregate(model, "T", "max") == 3.0
    assert set(numerical.tags()) == {"agg1", "gev1", "user_table_bound"}


class VariableNode:
    def __init__(self): self.values = {}
    def set(self, name, expression): self.values[name] = expression
    def varnames(self): return list(self.values)
    def get(self, name): return self.values[name]


class Variables:
    def __init__(self): self.nodes = {}
    def tags(self): return list(self.nodes)
    def create(self, tag): self.nodes[tag] = VariableNode()
    def __call__(self, tag): return self.nodes[tag]


class Component:
    def __init__(self): self.variables = Variables()
    def variable(self, tag=None): return self.variables if tag is None else self.variables(tag)


class VariableModel:
    def __init__(self):
        self.global_variables, self.component = Variables(), Component()
        self.java = type("Java", (), {"variable": lambda s, tag=None: self.global_variables if tag is None else self.global_variables(tag), "component": lambda s, _: self.component})()


def test_t006_global_and_component_groups_append_update_and_readback():
    model = VariableModel()
    _create_variable(model, "", "var1", "q_abs", "2")
    result = _create_variable(model, "", "var1", "q_heat", "q_abs*3")
    _create_variable(model, "", "var1", "q_abs", "4")
    _create_variable(model, "comp1", "varc", "qc", "5")
    assert result["created"] is False
    assert model.global_variables("var1").values == {"q_abs": "4", "q_heat": "q_abs*3"}
    assert "name" not in model.global_variables("var1").values
    assert _list_variables(model)[0]["variables"][0]["name"] == "q_abs"
    assert _list_variables(model, "comp1")[0]["variables"] == [{"name": "qc", "expression": "5"}]


def test_t007_parent_feature_and_inherited_selection():
    parent, child = Feature(), Feature()
    child.selection_obj = Selection()
    physics = type("Physics", (), {"selection": lambda s: parent.selection(), "feature": lambda s, tag=None: child if tag == "bc1" else None})()
    component = type("Component", (), {"physics": lambda s, _: physics})()
    model = type("Model", (), {"java": type("Java", (), {"component": lambda s, _: component})()})()
    parent_result = _set_physics_selection(model, "comp1", "ht", "ht", [1, 2])
    assert parent_result["target"] == "physics" and parent.selection_obj.values == [1, 2]
    _set_physics_selection(model, "comp1", "ht", "bc1", [], "named", "sel_hot")
    child.selection_obj.inherited = True
    with pytest.raises(ValueError, match="inherited"):
        _set_physics_selection(model, "comp1", "ht", "bc1", [3])


def test_t007_all_and_named_are_read_back_not_echoed():
    parent, child = Feature(), Feature()
    physics = type("Physics", (), {"selection": lambda s: parent.selection(), "feature": lambda s, tag=None: child})()
    component = type("Component", (), {"physics": lambda s, _: physics})()
    model = type("Model", (), {"java": type("Java", (), {"component": lambda s, _: component})()})()
    all_result = _set_physics_selection(model, "comp1", "ht", "bc1", [], "all")
    named_result = _set_physics_selection(model, "comp1", "ht", "bc1", [], "named", "actual_sel")
    assert all_result["selection"]["entities"] == []
    assert named_result["selection"]["named"] == "actual_sel"


def test_pure_read_rejects_before_model_evaluation(monkeypatch):
    from comsol_mcp import _tools_params
    monkeypatch.setattr(_tools_params, "_run_tool", lambda _name, callback: callback())
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: (_ for _ in ()).throw(AssertionError("model access")))
    # Policy is checked after visible-model guard, but before creating a result node.
    class EmptyModel: pass
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: EmptyModel())
    with pytest.raises(ValueError, match="pure_read"):
        _tools_params.evaluate_expressions('[{"name":"x","expression":"T"}]', evaluation_policy="pure_read")


def test_metrics_without_definitions_never_calls_evaluator(monkeypatch):
    from comsol_mcp import _tools_params
    monkeypatch.setattr(_tools_params, "_run_tool", lambda _name, callback: callback())
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: object())
    monkeypatch.setattr(_tools_params, "_evaluate_aggregate", lambda *_: (_ for _ in ()).throw(AssertionError("evaluated")))
    with pytest.raises(ValueError, match="Explicit non-empty metric definitions"):
        _tools_params.get_core_metrics()


def test_t003_explicit_prune_preserves_unknown_server_models(monkeypatch):
    import comsol_mcp._model as model_ops
    import comsol_mcp._server as server

    class Loaded:
        def __init__(self, tag): self.tag = tag
        def name(self): return self.tag
    class Client:
        def __init__(self): self.models_list, self.removed = [Loaded("keep"), Loaded("user")], []
        def models(self): return self.models_list
        def remove(self, model): self.removed.append(model.tag); self.models_list.remove(model)
    client = Client()
    monkeypatch.setattr(model_ops, "_require_client", lambda: client)
    monkeypatch.setattr(model_ops, "_safe_model_tag", lambda model: model.tag)
    monkeypatch.setattr(model_ops, "_safe_model_label", lambda model: model.tag)
    monkeypatch.setattr(model_ops, "_resolved_model_file_path", lambda model: "")
    server._mcp_owned_model_tags.clear()
    removed, _ = model_ops._prune_loaded_models_locked(client.models_list[0])
    assert removed == [] and client.removed == [] and [m.tag for m in client.models_list] == ["keep", "user"]


def test_disconnect_clears_model_ownership():
    import comsol_mcp._server as server
    from comsol_mcp._connection import _disconnect_locked
    server._client = None
    server._mcp_owned_model_tags.update({"model1", "model2"})
    _disconnect_locked()
    assert server._mcp_owned_model_tags == set()


def test_ephemeral_runner_serializes_concurrent_callbacks(monkeypatch):
    from comsol_mcp import _state, _server
    _server._runtime_lock = threading.RLock()
    monkeypatch.setattr(_state, "_setup_logging", lambda: None)
    monkeypatch.setattr(_state, "_tool_result", lambda *_args, **_kwargs: "ok")
    active, maximum, guard = [0], [0], threading.Lock()
    def callback():
        with guard:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        time.sleep(0.02)
        with guard: active[0] -= 1
        return {}
    workers = [threading.Thread(target=lambda: _state._run_tool("ephemeral", callback)) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert maximum == [1]


def test_iteration_rejects_missing_metrics_before_model_mutation(monkeypatch):
    from comsol_mcp import _tools_snapshot
    monkeypatch.setattr(_tools_snapshot, "_run_tool", lambda _name, callback: callback())
    monkeypatch.setattr(_tools_snapshot, "_require_visible_main", lambda _: (_ for _ in ()).throw(AssertionError("model touched")))
    with pytest.raises(ValueError, match="Explicit non-empty metric definitions"):
        _tools_snapshot.run_visible_main_iteration("case")


def test_cleanup_failure_does_not_try_field_fallback():
    numerical = Numerical(remove_error=True)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        _evaluate_expression_safely(Model(numerical), "u")
    assert len([t for t in numerical.tags() if t.startswith('mcp_eval_')]) == 1


def test_complex_structure_survives_coercion():
    assert _coerce_eval_value({'real': JavaArray([1.0]), 'imag': JavaArray([2.0])}) == {'real':[1.0], 'imag':[2.0]}


@pytest.mark.parametrize('definitions', ['[]','[{}]','[{"name":"x","expression":"u","aggregate":"wrong"}]','[{"name":"x","expression":"u","domains":[true]}]'])
def test_metric_preflight_rejects_invalid_definitions(definitions):
    from comsol_mcp._tools_params import _validate_metric_definitions
    with pytest.raises(ValueError):_validate_metric_definitions(definitions)


def test_solution_selection_preserves_field_axis_and_complex_parts():
    from comsol_mcp._model_ops import _select_inner
    values={'real':[[[1,2],[3,4]]], 'imag':[[[5,6],[7,8]]]}
    assert _select_inner(values,'last')=={'real':[[[3,4]]], 'imag':[[[7,8]]]}
    assert _select_inner(values,'all')==values
    assert _select_inner(values,'1')=={'real':[[[1,2]]], 'imag':[[[5,6]]]}
    with pytest.raises(ValueError):_select_inner(values,'0')


def test_metric_preflight_rejects_invalid_time_before_iteration():
    from comsol_mcp import _tools_snapshot
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_tools_snapshot,'_run_tool',lambda _,fn:fn())
        mp.setattr(_tools_snapshot,'_require_visible_main',lambda _:pytest.fail('model accessed'))
        with pytest.raises(ValueError,match='time_point'):
            _tools_snapshot.run_visible_main_iteration('bad',metrics_json='[{"name":"u","expression":"u","time_point":"banana"}]')
