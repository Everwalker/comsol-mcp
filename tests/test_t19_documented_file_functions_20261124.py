"""Independent T19 checks of the v2 finite file-function rule."""
from pathlib import Path
import json
import pytest
from comsol_mcp._g2_property_path_guard import known_file_value_refusal
from tests.t19_file_function_fake_stack import (
    FUNCTION_TYPES, NEW_FUNCTION_TYPES, assert_ledger_clean, function_node,
    node_path, routed_call, t19_stack,
)
from tests.test_unit_a_node_public_api import ROOT, tv


@pytest.mark.parametrize("type_id", FUNCTION_TYPES)
def test_semantic_gate_is_exact(type_id):
    relative = tv("input.dat")
    absolute = tv("/project/input.dat")
    assert known_file_value_refusal(type_id, "filename", relative)
    assert known_file_value_refusal(type_id, "filename", absolute) is None
    assert known_file_value_refusal(type_id, "filename", tv("")) is None
    assert known_file_value_refusal(type_id, "funcname", relative) is None
assert known_file_value_refusal("Analytic", "filename", tv("input.dat")) is None


@pytest.mark.parametrize("type_id", FUNCTION_TYPES)
def test_relative_create_is_refused_before_ticket_or_engine_create(t19_stack, type_id):
    stack=t19_stack
    result=routed_call(stack["daemon"], "node.create", {
        "parent": ROOT, "collection": "feature", "tag": "relative_file_function",
        "type_id": type_id,
        "properties": [{"name": "filename", "value": tv("input.dat")}],
    }, stack["execution"], f"create-relative-{type_id}")
    assert result["success"] is False, result
    assert result["error"]["code"] == "ACCESS_VIOLATION", json.dumps(result, sort_keys=True, default=str)
    assert stack["tickets"] == []
    assert not [row for row in stack["worker"].model_node.list.calls if row[0] == "create"]
    assert "relative_file_function" not in stack["worker"].model_node.list.items
    assert_ledger_clean(stack)


@pytest.mark.parametrize("type_id", FUNCTION_TYPES)
@pytest.mark.parametrize("operation", [
    "node.property_set", "node.property_index_set", "node.property_entry_set",
])
def test_relative_filename_setters_refuse_before_engine_metadata_or_mutation(
    t19_stack, type_id, operation,
):
    stack=t19_stack
    node=function_node(stack, type_id)
    calls=[]
    for name in ("set", "setIndex", "setEntry", "getString", "getEntryKeys", "getEntryKeyIndex"):
        original=getattr(node, name, None)
        def record(*args, _name=name, _original=original, **kwargs):
            calls.append((_name, args))
            if _original is not None:
                return _original(*args, **kwargs)
            return []
        setattr(node, name, record)

    if operation == "node.property_set":
        body={"path": node_path(), "properties": [{"name": "filename", "value": tv("input.dat")}]}
    elif operation == "node.property_index_set":
        body={"path": node_path(), "name": "filename", "indices": [0], "value": tv("input.dat")}
    else:
        body={"path": node_path(), "name": "filename", "key": "row0", "value": tv("input.dat")}
    result=routed_call(stack["daemon"], operation, body, stack["execution"], f"{operation}-{type_id}")
    assert result["success"] is False, result
    assert result["error"]["code"] == "ACCESS_VIOLATION", json.dumps(result, sort_keys=True, default=str)
    assert calls == []
    assert node.metadata_calls == []
    assert node.values["filename"] == "seed.dat"
    assert len(stack["tickets"]) == 1  # managed dispatch ticket; no model mutation
    assert_ledger_clean(stack)


@pytest.mark.parametrize("type_id", FUNCTION_TYPES)
def test_absolute_owned_project_file_create_remains_available(t19_stack, monkeypatch, type_id):
    stack=t19_stack
    target=stack["workspace"] / "inputs" / f"{type_id}.dat"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("T19 fake input\n")
    original_create=stack["worker"].model_node.list.create
    def create_with_filename(tag, actual_type):
        node=original_create(tag, actual_type)
        node.values["filename"]=""
        original_get_value_type=node.getValueType
        node.getValueType=lambda name: "String" if name == "filename" else original_get_value_type(name)
        return node
    monkeypatch.setattr(stack["worker"].model_node.list, "create", create_with_filename)
    tag=f"absolute_{type_id}"
    result=routed_call(stack["daemon"], "node.create", {
        "parent": ROOT, "collection": "feature", "tag": tag,
        "type_id": type_id,
        "properties": [{"name": "filename", "value": tv(str(target))}],
    }, stack["execution"], f"create-absolute-{type_id}")
    assert result["success"] is True, result
    assert stack["worker"].model_node.list.items[tag].values["filename"] == str(target)
    assert len(stack["tickets"]) == 1


@pytest.mark.parametrize("type_id", NEW_FUNCTION_TYPES)
def test_absolute_owned_project_file_set_remains_available(t19_stack, type_id):
    stack=t19_stack
    node=function_node(stack, type_id)
    target=stack["workspace"] / f"{type_id}-existing.dat"
    target.write_text("T19 fake input\n")
    result=routed_call(stack["daemon"], "node.property_set", {
        "path": node_path(), "properties": [{"name": "filename", "value": tv(str(target))}],
    }, stack["execution"], f"set-absolute-{type_id}")
    assert result["success"] is True, result
    assert node.values["filename"] == str(target)
    assert len(stack["tickets"]) == 1


def test_ordinary_fields_and_non_file_text_remain_writable(t19_stack):
    stack=t19_stack
    node=function_node(stack, "Interpolation")
    node.values.update(funcname="old", source="old")
    original_get_value_type=node.getValueType
    node.getValueType=lambda name: (
        "String" if name in {"filename", "funcname", "source"}
        else original_get_value_type(name)
    )
    result=routed_call(stack["daemon"], "node.property_set", {
        "path": node_path(),
        "properties": [
            {"name": "funcname", "value": tv("f1")},
            {"name": "source", "value": tv("x + 1", kind="expression")},
        ],
    }, stack["execution"], "ordinary-interpolation-fields")
    assert result["success"] is True, result
    assert node.values["funcname"] == "f1"
    assert node.values["source"] == "x + 1"
    assert len(stack["tickets"]) == 1


def test_non_file_source_modes_are_not_consulted_by_the_finite_rule():
    # The API docs say these filename inputs apply when source=file. The guard
    # takes only type/property/value, so it conservatively refuses relative
    # filename values for the documented types regardless of source mode.
    for type_id in ("LeastSquares", "GaussianProcess", "PolynomialChaosExpansion"):
        assert known_file_value_refusal(type_id, "filename", tv("input.dat"))
