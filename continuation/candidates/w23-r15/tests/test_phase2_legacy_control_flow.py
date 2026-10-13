"""Regression tests for legacy tool control flow fixed before the W05 gateway."""

from __future__ import annotations

import json

import pytest


def test_solver_batch_preflights_all_steps_before_creating_any(monkeypatch):
    from comsol_mcp import _tools_solver
    changes = []
    monkeypatch.setattr(_tools_solver, "_run_tool", lambda name, callback: callback())
    monkeypatch.setattr(_tools_solver, "_require_visible_main", lambda name: object())
    monkeypatch.setattr(_tools_solver, "_add_segregated_step", lambda *args: changes.append(args) or {})
    body = json.dumps([
        {"name": "add_segregated_step", "value": json.dumps({"step_tag": "s1", "variables": ["u"]})},
        {"name": "add_segregated_step", "value": "invalid JSON"},
    ])
    with pytest.raises(ValueError):
        _tools_solver.configure_solver("sol1", "seg1", body)
    assert changes == []


class _Params:
    def __init__(self, fail_name: str = ""):
        self.fail_name = fail_name
        self.calls: list[tuple[str, str]] = []

    def set(self, name: str, expression: str) -> None:
        self.calls.append((name, expression))
        if name == self.fail_name:
            raise RuntimeError(f"engine rejected {name}")


class _Model:
    def __init__(self, fail_name: str = ""):
        self.params = _Params(fail_name)
        self.java = type("Java", (), {"param": lambda _self: self.params})()


def _direct_runner(_name, callback):
    return callback()


def test_parameter_static_preflight_happens_before_any_write(monkeypatch):
    from comsol_mcp import _tools_params

    model = _Model()
    monkeypatch.setattr(_tools_params, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: model)

    with pytest.raises(ValueError, match="object"):
        _tools_params.set_parameters('[{"name":"a","expression":"1"}, 42]')
    assert model.params.calls == []


def test_parameter_runtime_failure_reports_applied_failed_and_not_executed(monkeypatch):
    from comsol_mcp import _tools_params
    from comsol_mcp._state import ToolExecutionError

    model = _Model(fail_name="b")
    monkeypatch.setattr(_tools_params, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: model)

    with pytest.raises(ToolExecutionError) as raised:
        _tools_params.set_parameters(
            '[{"name":"a","expression":"1"}, {"name":"b","expression":"2"}, {"name":"c","expression":"3"}]'
        )
    assert raised.value.data == {
        "applied": [{"name": "a", "expression": "1"}],
        "failed": {"name": "b", "expression": "2", "error": "engine rejected b"},
        "not_executed": [{"name": "c", "expression": "3"}],
        "atomic": False,
        "partial_change": True,
        "failed_item_may_have_changed": True,
        "safe_retry": False,
    }


def test_legacy_runner_preserves_structured_business_failure_data(monkeypatch):
    from comsol_mcp import _state
    from comsol_mcp._state import ToolExecutionError
    captured = {}
    monkeypatch.setattr(_state, "_setup_logging", lambda: None)
    monkeypatch.setattr(
        _state,
        "_tool_result",
        lambda tool, success, data=None, error="": captured.update(
            tool=tool, success=success, data=data, error=error
        ) or "envelope",
    )

    result = _state._run_tool(
        "batch",
        lambda: (_ for _ in ()).throw(ToolExecutionError("partial", data={"atomic": False})),
    )
    assert result == "envelope"
    assert captured == {"tool": "batch", "success": False, "data": {"atomic": False}, "error": "partial"}


def test_study_solve_failure_is_not_retried_with_a_second_target(monkeypatch):
    from comsol_mcp import _tools_workflow

    class Study:
        def get(self, tag):
            assert tag == "std1"
            return type("StudyNode", (), {"label": lambda _self: "Study 1"})()

    class SolveModel:
        java = type("Java", (), {"study": lambda _self: Study(), "label": lambda _self: "fixture"})()

        def __init__(self):
            self.solve_targets = []

        def solve(self, target):
            self.solve_targets.append(target)
            raise RuntimeError("solver diverged")

    model = SolveModel()
    monkeypatch.setattr(_tools_workflow, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_workflow, "_require_visible_main", lambda _: model)
    with pytest.raises(RuntimeError, match="solver diverged"):
        _tools_workflow.run_study("std1")
    assert model.solve_targets == ["Study 1"]


def test_iteration_rejects_partial_metrics_and_does_not_save(monkeypatch):
    from comsol_mcp import _tools_snapshot

    model = _Model()
    model.java.tag = lambda: "mod1"
    model.java.label = lambda: "fixture"
    model.java.getFilePath = lambda: "/tmp/fixture.mph"
    model.solve = lambda *_: None
    monkeypatch.setattr(_tools_snapshot, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_snapshot, "_require_visible_main", lambda _: model)
    monkeypatch.setattr(_tools_snapshot, "_visible_main_identity", lambda _: {"tag": "mod1", "path": "/tmp/fixture.mph"})
    monkeypatch.setattr(_tools_snapshot, "_visible_main_mismatch", lambda *_: [])
    monkeypatch.setattr(
        "comsol_mcp._tools_params.get_core_metrics",
        lambda *_args, **_kwargs: json.dumps({"success": True, "data": {"solve_status": "partial", "results": [{"ok": False, "error": "bad metric"}]}}),
    )
    monkeypatch.setattr(_tools_snapshot, "save_main_model_snapshot", lambda *_: pytest.fail("partial metrics must not save"))

    with pytest.raises(RuntimeError, match="metrics"):
        _tools_snapshot.run_visible_main_iteration(
            "case",
            metrics_json='[{"name":"u","expression":"u"}]',
        )


def test_iteration_success_separates_execution_metrics_and_acceptance(monkeypatch):
    from comsol_mcp import _tools_snapshot

    model = _Model()
    model.java.tag = lambda: "mod1"
    model.java.label = lambda: "fixture"
    model.java.getFilePath = lambda: "/tmp/fixture.mph"
    model.solve = lambda *_: None
    monkeypatch.setattr(_tools_snapshot, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_snapshot, "_require_visible_main", lambda _: model)
    monkeypatch.setattr(_tools_snapshot, "_visible_main_identity", lambda _: {"tag": "mod1", "path": "/tmp/fixture.mph"})
    monkeypatch.setattr(_tools_snapshot, "_visible_main_mismatch", lambda *_: [])
    monkeypatch.setattr(
        "comsol_mcp._tools_params.get_core_metrics",
        lambda *_args, **_kwargs: json.dumps({"success": True, "data": {"solve_status": "success", "results": [{"ok": True, "value": 1}]}}),
    )
    monkeypatch.setattr(_tools_snapshot, "save_main_model_snapshot", lambda *_: json.dumps({"success": True, "data": {"snapshot_path": "/tmp/case.mph"}}))
    monkeypatch.setattr(_tools_snapshot, "_append_operation", lambda *_: None)
    result = _tools_snapshot.run_visible_main_iteration("case", metrics_json='[{"name":"u","expression":"u"}]')
    assert result["execution_success"] is True
    assert result["metric_evaluation_success"] is True
    assert result["acceptance_status"] == "not_evaluated"


def test_property_batch_is_prevalidated_and_setter_failure_is_not_atomic():
    from comsol_mcp._model_ops import _apply_feature_properties, _normalize_properties
    from comsol_mcp._state import ToolExecutionError

    with pytest.raises(ValueError, match="Property name"):
        _normalize_properties('[{"name":"a","value":"1"}, {"value":"2"}]')

    class Feature:
        def __init__(self): self.calls = []
        def set(self, name, value):
            self.calls.append((name, value))
            if name == "b": raise RuntimeError("engine rejected b")

    feature = Feature()
    with pytest.raises(ToolExecutionError) as raised:
        _apply_feature_properties(feature, [("a", ["1"]), ("b", ["2"]), ("c", ["3"])])
    assert feature.calls == [("a", "1"), ("b", "2")]
    assert raised.value.data["applied"] == [{"name": "a", "value": "1"}]
    assert raised.value.data["failed_item_may_have_changed"] is True
    assert raised.value.data["not_executed"] == [{"name": "c", "value": "3"}]


def test_expression_row_failure_is_outer_business_failure_with_preserved_rows(monkeypatch):
    from comsol_mcp import _tools_params
    from comsol_mcp._state import ToolExecutionError

    monkeypatch.setattr(_tools_params, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: object())
    monkeypatch.setattr(
        _tools_params,
        "_evaluate_expression_safely",
        lambda _model, expression, *_args: [[2.0]] if expression == "u" else (_ for _ in ()).throw(RuntimeError("Undefined variable missing_symbol")),
    )
    with pytest.raises(ToolExecutionError, match="expressions could not") as raised:
        _tools_params.evaluate_expressions('[{"name":"good","expression":"u"},{"name":"bad","expression":"missing_symbol"}]')
    data = raised.value.data
    assert data["status"] == "partial"
    # C04 extends the evaluated row with the shape of the published value; the dataset/solution/route
    # fields are published by the one evaluation path, which this test replaces with a double, so
    # exactly the value-derived field is added here.
    assert data["results"] == [
        {"name": "good", "expression": "u", "value": [[2.0]], "last_value": 2.0, "shape": [1, 1], "ok": True},
        {"name": "bad", "expression": "missing_symbol", "ok": False, "error": "Undefined variable missing_symbol"},
    ]
    assert data["safe_retry"] is True
    assert data["partial_change"] is False
    assert data["failed_item_may_have_changed"] is False


def test_cleanup_failure_is_engine_unknown_not_a_row_level_success(monkeypatch):
    from comsol_mcp import _tools_params
    from comsol_mcp._model_ops import NumericalCleanupError
    from comsol_mcp._state import ToolExecutionError

    monkeypatch.setattr(_tools_params, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: object())
    monkeypatch.setattr(
        _tools_params,
        "_evaluate_expression_safely",
        lambda *_args: (_ for _ in ()).throw(NumericalCleanupError("remove denied")),
    )
    with pytest.raises(ToolExecutionError) as raised:
        _tools_params.evaluate_expressions('[{"name":"u","expression":"u"}]')
    assert raised.value.data["cleanup_failed"] is True
    assert raised.value.data["engine_state_unknown"] is True


def test_metric_cleanup_failure_is_engine_unknown_not_partial_metric_row(monkeypatch):
    from comsol_mcp import _tools_params
    from comsol_mcp._model_ops import NumericalCleanupError
    from comsol_mcp._state import ToolExecutionError

    monkeypatch.setattr(_tools_params, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _: object())
    monkeypatch.setattr(_tools_params, "_find_initialized_solution_tag", lambda _: "sol1")
    monkeypatch.setattr(
        _tools_params,
        "_evaluate_aggregate",
        lambda *_args: (_ for _ in ()).throw(NumericalCleanupError("remove denied")),
    )
    with pytest.raises(ToolExecutionError) as raised:
        _tools_params.get_core_metrics('[{"name":"u","expression":"u"}]')
    assert raised.value.data["cleanup_failed"] is True
    assert raised.value.data["engine_state_unknown"] is True


def test_existing_feature_type_must_match_before_idempotent_reuse():
    from comsol_mcp._model_ops import _assert_existing_feature_type

    feature = type("Feature", (), {"getType": lambda _self: "Block"})()
    _assert_existing_feature_type(feature, "Block", kind="geometry feature")
    with pytest.raises(ValueError, match="type conflict"):
        _assert_existing_feature_type(feature, "Sphere", kind="geometry feature")


def test_create_feature_reports_when_an_existing_matching_feature_is_reused(monkeypatch):
    from comsol_mcp import _tools_geometry

    class Feature:
        def getType(self): return "Rectangle"

    class Features:
        def tags(self): return ["r1"]
        def __call__(self, tag):
            assert tag == "r1"
            return Feature()

    class Geometry:
        def feature(self, tag=None):
            features = Features()
            return features if tag is None else features(tag)
        def create(self, *_args): pytest.fail("matching tag/type must not be recreated")

    class Geometries:
        def tags(self): return ["geom1"]
        def __call__(self, tag=None):
            if tag is None:
                return self
            assert tag == "geom1"
            return Geometry()

    class Components:
        def tags(self): return ["comp1"]
        def __call__(self, tag=None):
            if tag is None:
                return self
            assert tag == "comp1"
            return type("Component", (), {"geom": lambda _self, *args: geometries(*args)})()

    geometries = Geometries()
    components = Components()
    model = type("Model", (), {"java": type("Java", (), {"component": lambda _self, *args: components(*args)})()})()
    monkeypatch.setattr(_tools_geometry, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_geometry, "_require_visible_main", lambda _: model)
    result = _tools_geometry.create_feature("comp1", "geom1", "r1", "Rectangle")
    assert result["created"] is False


def test_commit_publishes_resolved_artifact_without_replacing_server_identity(monkeypatch, tmp_path):
    from comsol_mcp import _model, _server, _tools_snapshot

    class Java:
        def __init__(self):
            self.saved = []
            self._label = "loaded.mph"
        def save(self, *args): self.saved.append(args)
        def label(self, value=None):
            if value is not None: self._label = value
            return self._label
        def tag(self): return "mod1"
        def getFilePath(self): return str(tmp_path / "stale-loaded-path.mph")

    model = type("Model", (), {"java": Java(), "save": lambda self, path: self.java.save(str(path))})()
    main = (tmp_path / "committed.mph").resolve()
    snapshot = (tmp_path / "snapshot.mph").resolve()
    written = {}
    monkeypatch.setattr(_tools_snapshot, "_run_tool", _direct_runner)
    monkeypatch.setattr(_tools_snapshot, "_block_if_visible_main_locked", lambda _: None)
    monkeypatch.setattr(_tools_snapshot, "_require_visible_main", lambda _: model)
    monkeypatch.setattr(_tools_snapshot, "_require_client", lambda: object())
    monkeypatch.setattr(_tools_snapshot, "_read_workflow_state", lambda: {"current_main_model_path": str(main)})
    monkeypatch.setattr(_tools_snapshot, "_workflow_snapshot_path", lambda *_: snapshot)
    monkeypatch.setattr(_tools_snapshot, "_write_workflow_state", lambda value: written.update(value) or value)
    _server._current_model = None
    _server._current_model_path = ""

    result = _tools_snapshot.commit_current_main_model("checkpoint")

    assert model.java.saved == [(str(snapshot), True), (str(main),)]
    assert result["current_main_model_path"] == str(main)
    assert written["current_main_model_path"] == str(main)
    source = str((tmp_path / "stale-loaded-path.mph").resolve())
    assert result["current_main_identity"]["path"] == source
    assert written["main_model_path"] == source
    assert result["save_mode"] == "atomic_copy"
    assert _model._visible_main_mismatch(model, written) == []
