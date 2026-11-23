"""Finite T19.a known filename checks through synthetic production routes.

All engines are in-process fakes. These tests do not establish native COMSOL,
Windows filesystem, or engine-side path resolution behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_engine import _actual_node_type_id_for_file_property
from comsol_mcp import _g3_w14
from comsol_mcp._g2_property_path_guard import known_file_value_refusal
from tests.test_t20_artifact_boundary import (
    _call,
    _envelope,
    _make_project,
    _node_path,
)
from tests.test_unit_a_node_public_api import ROOT, Worker, tv
from tests.test_g3_w14 import (
    FNode, geom_path, geometry as g3_geometry, workplane_feature, workplane_path,
    world as g3_world,
)


@pytest.fixture
def create_stack(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    worker = Worker()
    ledger = SessionLedger("session", "server")
    service = ExecutionService(ledger, worker, project_root=root)
    ref = service.bind_model("m")["execution"]["model_ref"]
    daemon = ControlDaemon(tmp_path / "control", project_root=root,
                           registry={}, worker=worker, service=service)
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "t19a", "workspace": "t19a",
                      "policy": {"permissions": ["inspect", "project_write"]}},
        "execution": {"request_id": "project", "idempotency_key": "project"},
    })
    assert created["success"], created
    project = created["data"]["project"]
    daemon.backend._bind_model_project(ref, project["project_id"])
    daemon.store.put_metadata(
        "revisions", daemon.backend._model_project_key(ref),
        {"model_ref": ref, "project_id": project["project_id"], "revision": 0},
    )
    daemon.backend.endpoint_key = "127.0.0.1:2036"  # identity string only; no socket
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True})

    tickets = []
    original_begin_write = SessionLedger.begin_write

    def record_ticket(self, *args, **kwargs):
        tickets.append((args, kwargs))
        return original_begin_write(self, *args, **kwargs)

    monkeypatch.setattr(SessionLedger, "begin_write", record_ticket)
    execution = {
        "project_id": project["project_id"], "session_id": "session",
        "model_ref": ref, "expected_revision": 0,
        "idempotency_key": "create", "request_id": "create",
    }
    try:
        yield daemon, worker, Path(project["workspace"]), execution, tickets
    finally:
        daemon.close()


def _create(daemon, body, execution):
    return daemon.dispatch({
        "operation": "registry_call",
        "arguments": {"operation_id": "node.create", "arguments": body},
        "execution": execution,
    })


def test_semantic_mapping_is_exact_and_requires_literal_absolute_filename():
    bare = {"kind": "string", "shape": [], "data": "f1"}
    absolute = {"kind": "string", "shape": [], "data": "/project/inputs/part.step"}
    expression = {"kind": "expression", "shape": [], "data": "f1"}

    assert known_file_value_refusal("Import", "filename", bare)
    assert known_file_value_refusal("Import", "filename", expression)
    assert known_file_value_refusal("Import", "filename", absolute) is None
    assert known_file_value_refusal("Interpolation", "filename", bare)
    assert known_file_value_refusal("Interpolation", "filename", absolute) is None
    assert known_file_value_refusal("Interpolation", "funcname", bare) is None
    assert known_file_value_refusal("Import", "funcname", bare) is None


@pytest.mark.parametrize("type_id", ["Import", "Interpolation"])
def test_create_import_relative_filename_is_refused_before_ticket_or_create(create_stack, type_id):
    daemon, worker, _workspace, execution, tickets = create_stack
    body = {
        "parent": ROOT, "collection": "feature", "tag": "relative_import",
        "type_id": type_id,
        "properties": [{"name": "filename", "value": tv("f1")}],
    }

    result = _create(daemon, body, execution)

    assert result["success"] is False, result
    assert result["error"]["code"] == "ACCESS_VIOLATION", result
    assert not tickets
    assert not [row for row in worker.model_node.list.calls if row[0] == "create"]
    assert "relative_import" not in worker.model_node.list.items


def test_create_import_absolute_project_file_remains_usable(create_stack, tmp_path, monkeypatch):
    daemon, worker, workspace, execution, tickets = create_stack
    target = workspace / "inputs" / "part.step"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic CAD bytes\n")

    original_create = worker.model_node.list.create

    def create_with_import_filename(tag, type_id):
        node = original_create(tag, type_id)
        if type_id == "Import":
            node.values["filename"] = ""
            original_get_value_type = node.getValueType
            node.getValueType = lambda name: (
                "String" if name == "filename" else original_get_value_type(name)
            )
        return node

    monkeypatch.setattr(worker.model_node.list, "create", create_with_import_filename)
    body = {
        "parent": ROOT, "collection": "feature", "tag": "absolute_import",
        "type_id": "Import",
        "properties": [{"name": "filename", "value": tv(str(target))}],
    }

    result = _create(daemon, body, execution)

    assert result["success"] is True, result
    assert worker.model_node.list.items["absolute_import"].values["filename"] == str(target)
    assert len(tickets) == 1


@pytest.mark.parametrize("type_id", ["Import", "Interpolation"])
def test_existing_import_property_and_index_entry_routes_refuse_relative_filename(tmp_path, monkeypatch, type_id):
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "set-routes", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True})
        node.type_id = type_id  # engine-side metadata on the resolved fake node
        path = _node_path(("func", "interpA"))
        set_calls = []
        original_set = node.set

        def record_set(*args):
            set_calls.append(args)
            return original_set(*args)

        monkeypatch.setattr(node, "set", record_set)
        requests = [
            ("node.property_set", {
                "path": path,
                "properties": [{"name": "filename", "value": tv("f1")}],
            }),
            ("node.property_index_set", {
                "path": path, "name": "filename", "indices": [0], "value": tv("f1"),
            }),
            ("node.property_entry_set", {
                "path": path, "name": "filename", "key": "row0", "value": tv("f1"),
            }),
        ]
        for index, (operation, body) in enumerate(requests):
            result = _call(daemon, operation, body, _envelope(project, execution, f"relative-{index}"))
            assert result["success"] is False, result
            assert result["error"]["code"] == "ACCESS_VIOLATION", result
        assert not set_calls
        assert not node.values.get("filename")
    finally:
        daemon.close()


def test_non_import_text_and_expression_writes_remain_available(tmp_path, monkeypatch):
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "plain-values", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True})
        node.type_id = "Interpolation"
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [
                {"name": "funcname", "value": tv("f1")},
                {"name": "source", "value": tv("x + 1", kind="expression")},
            ],
        }, _envelope(project, execution, "plain-and-expression"))

        assert result["success"] is True, result
        assert node.values["funcname"] == "f1"
        assert node.values["source"] == "x + 1"
    finally:
        daemon.close()


def test_geometry_feature_create_refuses_import_relative_filename_before_create():
    geom = g3_geometry("geom1")
    worker, _, _ = g3_world(geometries={"geom1": geom})

    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_feature_create(worker, "Model", {
            "parent": geom_path("geom1"), "tag": "rel_import", "type_id": "Import",
            "definition": {"filename": "f1"},
        })

    assert getattr(exc_info.value, "code", None) == "ACCESS_VIOLATION"
    assert not geom.feature_list.mutation_calls()
    assert "rel_import" not in geom.feature_list.items


def test_geometry_feature_update_uses_resolved_import_type_and_keeps_absolute_path():
    import_node = FNode(tag="imp1", type_id="Import", value_types={"filename": "String"},
                        values={"filename": "/project/inputs/part.step"})
    geom = g3_geometry("geom1", features={"imp1": import_node})
    worker, _, _ = g3_world(geometries={"geom1": geom})
    set_calls = []
    original_set = import_node.set

    def record_set(*args):
        set_calls.append(args)
        return original_set(*args)

    import_node.set = record_set
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_feature_update(worker, "Model", {
            "path": {"segments": [
                {"collection": "component", "tag": "comp1"},
                {"collection": "geom", "tag": "geom1"},
                {"collection": "feature", "tag": "imp1"},
            ]},
            "definition": {"filename": "f1"},
        })
    assert getattr(exc_info.value, "code", None) == "ACCESS_VIOLATION"
    assert not set_calls

    screened = []

    def accept_project_path(value, *, where, stage):
        screened.append((value["data"], where, stage))

    result = _g3_w14.geometry_feature_update(worker, "Model", {
        "path": {"segments": [
            {"collection": "component", "tag": "comp1"},
            {"collection": "geom", "tag": "geom1"},
            {"collection": "feature", "tag": "imp1"},
        ]},
        "definition": {"filename": "/project/inputs/part.step"},
    }, path_guard=accept_project_path)
    assert screened and screened[0][0] == "/project/inputs/part.step"
    assert result["properties"]["filename"]["data"] == "/project/inputs/part.step"


def test_workplane_plan_preflights_import_filename_before_any_action_mutates():
    inner = g3_geometry("wp_geom")
    workplane = workplane_feature("wp1", inner=inner)
    outer = g3_geometry("geom1", features={"wp1": workplane})
    worker, _, _ = g3_world(geometries={"geom1": outer})

    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_workplane_edit(worker, "Model", {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "create", "tag": "r1", "type_id": "Rectangle",
                 "definition": {"size": [1.0, 1.0]}},
                {"action": "create", "tag": "imp1", "type_id": "Import",
                 "definition": {"filename": "f1"}},
            ],
        }, path_guard=lambda *_args, **_kwargs: None)

    assert exc_info.value.code == "ACCESS_VIOLATION"
    assert not inner.feature_list.mutation_calls()


def test_get_type_unknown_survives_managed_witness_and_stops_before_setter(tmp_path, monkeypatch):
    daemon, project, _root, _model, node, execution = _make_project(
        tmp_path, "get-type-unknown", monkeypatch
    )
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True})
        set_calls = []
        get_type_calls = []
        original_set = node.set

        def record_set(*args):
            set_calls.append(args)
            return original_set(*args)

        def unresolved_type():
            get_type_calls.append(True)
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "synthetic getType Worker outcome is unresolved",
                stage="post_dispatch",
            )

        monkeypatch.setattr(node, "set", record_set)
        monkeypatch.setattr(node, "getType", unresolved_type)
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "filename", "value": tv("/project/inputs/part.step")}],
        }, _envelope(project, execution, "get-type-unknown"))

        assert result["success"] is False, result
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
        assert result["error"]["details"]["witness"]["mutation_issued"] is False, result
        assert result["error"]["safe_retry"] is False, result
        assert get_type_calls == [True]
        assert not set_calls
    finally:
        daemon.close()


def test_only_ordinary_missing_get_type_metadata_maps_to_api_unsupported():
    class MissingType:
        pass

    with pytest.raises(ExecutionContractError) as missing:
        _actual_node_type_id_for_file_property(MissingType())
    assert missing.value.code == "API_UNSUPPORTED"
    assert missing.value.stage == "validation"

    class FailedType:
        def getType(self):
            raise ExecutionContractError("ENGINE_CALL_FAILED", "synthetic engine failure")

    with pytest.raises(ExecutionContractError) as failed:
        _actual_node_type_id_for_file_property(FailedType())
    assert failed.value.code == "ENGINE_CALL_FAILED"


def test_workplane_existing_import_get_type_unknown_is_not_downgraded():
    import_node = FNode(tag="imp1", type_id="Import", value_types={"filename": "String"},
                        values={"filename": ""})
    inner = g3_geometry("wp_geom", features={"imp1": import_node})
    workplane = workplane_feature("wp1", inner=inner)
    outer = g3_geometry("geom1", features={"wp1": workplane})
    worker, _, _ = g3_world(geometries={"geom1": outer})
    set_calls = []
    get_type_calls = []
    original_set = import_node.set

    def record_set(*args):
        set_calls.append(args)
        return original_set(*args)

    def unresolved_type():
        get_type_calls.append(True)
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "synthetic getType Worker outcome is unresolved",
            stage="post_dispatch",
        )

    import_node.set = record_set
    import_node.getType = unresolved_type
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_workplane_edit(worker, "Model", {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "update", "tag": "imp1", "definition": {"filename": "f1"}},
            ],
        }, path_guard=lambda *_args, **_kwargs: None)

    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert get_type_calls == [True]
    assert not set_calls


def test_geometry_create_stops_after_post_create_get_type_unknown(monkeypatch):
    geom = g3_geometry("geom1")
    worker, _, _ = g3_world(geometries={"geom1": geom})
    calls = []
    node = FNode(tag="imp1", type_id="Import")

    def unresolved_type():
        calls.append("getType:UNKNOWN")
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic type read")

    node.getType = unresolved_type
    geom.feature_list.factory = lambda *_args: node
    monkeypatch.setattr(_g3_w14, "_feature_properties",
                        lambda *_args, **_kwargs: (calls.append("properties") or [], "synthetic"))
    monkeypatch.setattr(_g3_w14, "_property_write", lambda *_args, **_kwargs: calls.append("set"))
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_feature_create(worker, "Model", {
            "parent": geom_path("geom1"), "tag": "imp1", "type_id": "Import",
            "definition": {"filename": "/project/inputs/part.step"},
        }, path_guard=lambda *_args, **_kwargs: None)
    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert "imp1" in geom.feature_list.items  # create already dispatched
    assert calls == ["getType:UNKNOWN"]


def test_geometry_update_preserves_get_type_unknown_before_setter():
    node = FNode(tag="imp1", type_id="Import")
    geom = g3_geometry("geom1", features={"imp1": node})
    worker, _, _ = g3_world(geometries={"geom1": geom})
    calls = []

    def unresolved_type():
        calls.append("getType:UNKNOWN")
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic type read")

    node.getType = unresolved_type
    node.set = lambda *_args: calls.append("set")
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_feature_update(worker, "Model", {
            "path": {"segments": [
                {"collection": "component", "tag": "comp1"},
                {"collection": "geom", "tag": "geom1"},
                {"collection": "feature", "tag": "imp1"},
            ]},
            "definition": {"filename": "/project/inputs/part.step"},
        }, path_guard=lambda *_args, **_kwargs: None)
    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert calls == ["getType:UNKNOWN"]


def test_workplane_execution_stops_after_second_get_type_unknown():
    node = FNode(tag="imp1", type_id="Import")
    inner = g3_geometry("wp_geom", features={"imp1": node})
    outer = g3_geometry("geom1", features={"wp1": workplane_feature("wp1", inner=inner)})
    worker, _, _ = g3_world(geometries={"geom1": outer})
    calls = []
    tags_after_unknown = []
    original_tags = inner.feature_list.tags

    def tags():
        if calls and calls[-1] == "getType:UNKNOWN":
            tags_after_unknown.append(True)
        return original_tags()

    def type_readback():
        if not calls:
            calls.append("getType:Import")
            return "Import"
        calls.append("getType:UNKNOWN")
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic type read")

    inner.feature_list.tags = tags
    node.getType = type_readback
    node.set = lambda *_args: calls.append("set")
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_workplane_edit(worker, "Model", {
            "workplane": workplane_path("wp1"),
            "actions": [{"action": "update", "tag": "imp1",
                         "definition": {"filename": "/project/inputs/part.step"}}],
        }, path_guard=lambda *_args, **_kwargs: None)
    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert calls == ["getType:Import", "getType:UNKNOWN"]
    assert not tags_after_unknown


def test_workplane_stops_after_nested_action_returns_unknown(monkeypatch):
    later = FNode(tag="later", type_id="Rectangle")
    inner = g3_geometry("wp_geom", features={"later": later})
    outer = g3_geometry("geom1", features={"wp1": workplane_feature("wp1", inner=inner)})
    worker, _, _ = g3_world(geometries={"geom1": outer})
    active_calls = []
    later.active = lambda *_args: active_calls.append(True)
    monkeypatch.setattr(_g3_w14, "_feature_create_common", lambda *_args, **_kwargs: {
        "status": "EXECUTION_STATE_UNKNOWN", "execution_state_unknown": True,
        "partial_change": True,
    })
    with pytest.raises(ExecutionContractError) as exc_info:
        _g3_w14.geometry_workplane_edit(worker, "Model", {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "create", "tag": "imp1", "type_id": "Import",
                 "definition": {"filename": "/project/inputs/part.step"}},
                {"action": "set_active", "tag": "later", "enabled": False},
            ],
        }, path_guard=lambda *_args, **_kwargs: None)
    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert not active_calls
