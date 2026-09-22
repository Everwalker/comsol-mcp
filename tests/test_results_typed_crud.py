"""Focused typed CRUD checks for W17 Dataset/Numerical/Table adapters."""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._g3_results import (
    dataset_create,
    dataset_update,
    result_numerical_manage,
    result_table_manage,
)

from tests.test_g3_w17 import FWiredTree


def _path(collection: str, tag: str) -> dict[str, list[dict[str, str]]]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


def test_dataset_definition_is_prevalidated_and_typed_path_rejects_geometry() -> None:
    tree = FWiredTree()
    with pytest.raises(ExecutionContractError) as unknown:
        dataset_create(tree.worker, "Model", {
            "tag": "bad1", "type_id": "CutPoint3D", "definition": {"typo_property": 1},
        })
    assert unknown.value.code == "INVALID_REQUEST"
    assert isinstance(unknown.value, PreWriteRefusal)
    assert unknown.value.stage == "validation"
    assert "bad1" not in tree.dataset_list.tags()

    with pytest.raises(ExecutionContractError) as geometry:
        dataset_update(tree.worker, "Model", {
            "path": {"segments": [{"accessor": "result"}, {"collection": "geometry", "tag": "geom1"}]},
            "definition": {"pointx": 1.0},
        })
    assert geometry.value.code == "INVALID_NODE_PATH"
    assert isinstance(geometry.value, PreWriteRefusal)
    assert geometry.value.stage == "validation"


def test_dataset_binding_refs_and_cycles_refuse_before_mutation() -> None:
    tree = FWiredTree()

    with pytest.raises(PreWriteRefusal) as bad_component:
        dataset_create(tree.worker, "Model", {
            "tag": "bad_component", "type_id": "CutPoint3D",
            "definition": {"data": "dset1", "comp": "missing_comp",
                            "pointx": 0.01, "pointy": 0.01, "pointz": 0.01},
        })
    assert bad_component.value.code == "NODE_NOT_FOUND"
    assert bad_component.value.stage == "validation"
    assert "bad_component" not in tree.dataset_list.tags()

    for tag in ("cycle_a", "cycle_b"):
        created = dataset_create(tree.worker, "Model", {
            "tag": tag, "type_id": "Join", "definition": {"data": "dset1", "data2": "dset1"},
        })
        assert created["created"] is True
    updated = dataset_update(tree.worker, "Model", {
        "path": _path("dataset", "cycle_a"), "definition": {"data": "cycle_b"},
    })
    assert updated["updated"] is True
    cycle_node = tree.dataset_list.items["cycle_b"]
    set_count = len([name for name, _args in cycle_node.calls if name == "set"])
    with pytest.raises(PreWriteRefusal) as cycle:
        dataset_update(tree.worker, "Model", {
            "path": _path("dataset", "cycle_b"), "definition": {"data": "cycle_a"},
        })
    assert cycle.value.code == "DATASET_CYCLE_DETECTED"
    assert cycle.value.stage == "validation"
    assert len([name for name, _args in cycle_node.calls if name == "set"]) == set_count


def test_cutplane_rejects_component_geometry_setters_before_create() -> None:
    tree = FWiredTree()
    with pytest.raises(PreWriteRefusal) as refused:
        dataset_create(tree.worker, "Model", {
            "tag": "bad_cutplane", "type_id": "CutPlane",
            "definition": {"data": "dset1", "comp": "comp1", "geom": "geom1",
                            "planetype": "quick", "quickplane": "xy"},
        })
    assert refused.value.code == "INVALID_REQUEST"
    assert refused.value.stage == "validation"
    assert "bad_cutplane" not in tree.dataset_list.tags()


def test_numerical_update_stops_on_first_engine_set_failure() -> None:
    tree = FWiredTree()
    node_path = _path("numerical", "n1")
    created = result_numerical_manage(tree.worker, "Model", {
        "action": "create", "path": node_path,
        "definition": {"type_id": "EvalGlobal", "expr": ["T"]},
    })
    assert created["status"] == "APPLIED"
    node = tree.numerical_list.items["n1"]
    before_set_calls = len([name for name, _args in node.calls if name == "set"])
    node.unavailable.add("set")
    result = result_numerical_manage(tree.worker, "Model", {
        "action": "update", "path": node_path,
        "definition": {"expr": ["U"], "data": "dset1"},
    })
    assert result["failed_count"] == 1
    assert result["not_executed"] == [{"step": "property", "property": "data"}]
    assert len([name for name, _args in node.calls if name == "set"]) == before_set_calls + 1


def test_table_set_does_not_fallback_after_set_error() -> None:
    tree = FWiredTree()
    table_path = _path("table", "tbl1")
    created = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": table_path, "definition": {},
    })
    assert created["status"] == "APPLIED"
    node = tree.table_list.items["tbl1"]
    node.unavailable.add("setTableData")
    result = result_table_manage(tree.worker, "Model", {
        "action": "set", "path": table_path, "definition": {"data": [[1.0, 2.0]]},
    })
    assert result["failed_count"] == 1
    assert [name for name, _args in node.calls if name == "setTableData"] == ["setTableData"]
