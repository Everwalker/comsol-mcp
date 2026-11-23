"""T26 component-free evaluation regression coverage."""
from __future__ import annotations

from typing import Any, Mapping

import pytest

import comsol_mcp
from comsol_mcp import _execution_contract, _g3_ops, _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w17 import FList, FNode, FWiredTree

def _componentless_solution_tree() -> FWiredTree:
    tree = FWiredTree(numerical_real=42.0)
    tree.model.collections["component"].items.clear()
    tree.model.collections["modelNode"].items.clear()
    tree.model.collections["geom"] = FList(node_type="GeomSequence")
    tree.model.geom = lambda *args: tree.model._collection("geom", args)
    tree.dset1.props["comp"] = None
    tree.dset1.props["geom"] = None
    return tree


def _dispatch_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tree: FWiredTree,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    assert _g3_ops.effect_of("result.evaluate") == "EVALUATE"
    monkeypatch.setitem(_g3_ops.DISPATCH, "result.evaluate", results.result_evaluate)
    return _g3_ops.dispatch(
        "result.evaluate", tree.worker, "Model", {"spec": dict(spec)},
    )


def _global_spec() -> dict[str, Any]:
    return {
        "expressions": ["1"],  # synthetic component-independent expression
        "solution": {"dataset": "dset1"},
        "aggregate": "global",
        "complex_mode": "real",
    }


def test_candidate_global_eval_binds_dataset_and_solution_without_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()
    created: list[Any] = []
    factory = tree.numerical_list.factory

    def capture_factory(tag: str, *args: Any) -> Any:
        node = factory(tag, *args)
        created.append(node)
        return node

    tree.numerical_list.factory = capture_factory
    result = _dispatch_candidate(monkeypatch, tree, _global_spec())

    assert result["status"]["ok"] is True
    assert result["dataset"] == "dset1"
    assert result["solution"] == "sol1"
    assert result["dataset_binding"]["solution"] == "sol1"
    assert result["dataset_binding"]["component"] is None
    assert result["dataset_binding"]["geometry"] is None
    assert result["values"] is not None
    assert len(created) == 1
    assert created[0].props["data"] == "dset1"
    assert created[0].props["expr"] == result["evaluated_expressions"]
    assert tree.numerical_list.tags() == []


def test_component_bound_global_eval_retains_existing_binding_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = FWiredTree(numerical_real=42.0)
    result = _dispatch_candidate(monkeypatch, tree, _global_spec())

    assert result["status"]["ok"] is True
    assert result["dataset"] == "dset1"
    assert result["solution"] == "sol1"
    assert result["dataset_binding"]["component"] == "comp1"
    assert result["dataset_binding"]["geometry"] == "geom1"
    assert tree.numerical_list.tags() == []


def test_component_free_global_binding_does_not_downgrade_worker_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()
    original_get_string = tree.dset1.getString
    unknown_seen = False
    reads_after_unknown: list[str] = []

    def unknown_on_solution(name: str) -> Any:
        nonlocal unknown_seen
        if unknown_seen:
            reads_after_unknown.append(name)
        if name == "solution":
            unknown_seen = True
            raise ExecutionContractError(
                "ENGINE_CALL_FAILED", "synthetic unresolved Worker read",
                stage="post_dispatch", details={"execution_state_unknown": True},
            )
        return original_get_string(name)

    monkeypatch.setattr(tree.dset1, "getString", unknown_on_solution)
    with pytest.raises(ExecutionContractError) as exc_info:
        _dispatch_candidate(monkeypatch, tree, _global_spec())

    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert unknown_seen
    assert reads_after_unknown == []
    assert tree.numerical_list.tags() == []


def test_component_free_non_global_request_keeps_binding_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()
    spec = _global_spec()
    spec["aggregate"] = "none"

    with pytest.raises(ExecutionContractError) as exc_info:
        _dispatch_candidate(monkeypatch, tree, spec)

    assert exc_info.value.code == "DATASET_BINDING_INCOMPLETE"
    assert tree.numerical_list.tags() == []


def _assert_candidate_refuses(
    monkeypatch: pytest.MonkeyPatch,
    tree: FWiredTree,
    spec: Mapping[str, Any] | None = None,
    *,
    code: str = "DATASET_BINDING_INCOMPLETE",
) -> None:
    with pytest.raises(ExecutionContractError) as exc_info:
        _dispatch_candidate(monkeypatch, tree, spec or _global_spec())
    assert exc_info.value.code == code
    assert tree.numerical_list.tags() == []


def test_missing_comp_or_geom_property_inventory_never_proves_zero_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for missing_names in (("comp",), ("geom",), ("comp", "geom")):
        tree = _componentless_solution_tree()
        for name in missing_names:
            tree.dset1.props.pop(name)
        _assert_candidate_refuses(monkeypatch, tree)


def test_component_inventory_must_be_explicitly_empty_and_unambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_component = _componentless_solution_tree()
    one_component.component.collections["geom"].items.clear()
    one_component.model.collections["component"].items["comp1"] = one_component.component
    one_component.model.collections["modelNode"].items["comp1"] = one_component.component
    _assert_candidate_refuses(monkeypatch, one_component)

    multiple_components = _componentless_solution_tree()
    comp2 = FNode(tag="comp2", type_id="Component", collections={"geom": FList(node_type="GeomSequence")})
    comp2.geom = lambda *args: comp2._collection("geom", args)
    multiple_components.model.collections["component"].items["comp2"] = comp2
    multiple_components.model.collections["modelNode"].items["comp2"] = comp2
    _assert_candidate_refuses(monkeypatch, multiple_components)


def test_model_geometry_sequence_prevents_the_component_free_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()
    tree.model.collections["geom"].items["partgeom1"] = FNode(tag="partgeom1", type_id="GeomSequence")
    _assert_candidate_refuses(monkeypatch, tree)


def test_prior_seven_rejection_controls_remain_prewrite_refusals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_selection = _global_spec()
    explicit_selection["selection"] = {
        "kind": "explicit", "component": "comp1", "geometry": "geom1",
        "entity_dimension": 3, "entities": [1],
    }
    spatial_selection = _global_spec()
    spatial_selection["selection"] = {
        "kind": "spatial", "component": "comp1", "geometry": "geom1",
        "entity_dimension": 3, "query": {},
    }
    weighted = _global_spec()
    weighted["weight_expression"] = "1"
    entity_dimension = _global_spec()
    entity_dimension["entity_dim"] = 3

    derived = _componentless_solution_tree()
    derived.dataset_list.items["cut1"] = FNode(
        tag="cut1", type_id="CutPoint3D", props={"data": "dset1"},
    )
    derived_spec = _global_spec()
    derived_spec["solution"] = {"dataset": "cut1"}

    getter_failure = _componentless_solution_tree()
    def fail_component_getter(name: str) -> Any:
        if name == "comp":
            raise RuntimeError("synthetic component property read failure")
        return None
    monkeypatch.setattr(getter_failure.dset1, "getString", fail_component_getter)

    mismatch = _global_spec()
    mismatch["solution"] = {"dataset": "dset1", "solution": "sol2"}

    controls = (
        (_componentless_solution_tree(), explicit_selection, "DATASET_BINDING_INCOMPLETE"),
        (_componentless_solution_tree(), spatial_selection, "DATASET_BINDING_INCOMPLETE"),
        (_componentless_solution_tree(), weighted, "DATASET_BINDING_INCOMPLETE"),
        (_componentless_solution_tree(), entity_dimension, "DATASET_BINDING_INCOMPLETE"),
        (derived, derived_spec, "DATASET_BINDING_INCOMPLETE"),
        (getter_failure, _global_spec(), "DATASET_BINDING_INCOMPLETE"),
        (_componentless_solution_tree(), mismatch, "SOLUTION_MISMATCH"),
    )
    for tree, spec, expected_code in controls:
        _assert_candidate_refuses(monkeypatch, tree, spec, code=expected_code)


def test_unavailable_property_inventory_keeps_the_special_case_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()
    def fail_properties() -> Any:
        raise RuntimeError("synthetic property inventory failure")
    monkeypatch.setattr(tree.dset1, "properties", fail_properties)
    _assert_candidate_refuses(monkeypatch, tree)


def test_unknown_during_new_proof_is_not_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _componentless_solution_tree()

    def resolved_candidate(_model: Any, dataset_tag: str, *, requested_solution: str | None = None) -> dict[str, Any]:
        return {
            "dataset": dataset_tag, "dataset_type": "Solution", "solution": "sol1",
            "component": None, "geometry": None, "binding_complete": False,
            "read_errors": [], "error": {"code": "DATASET_BINDING_INCOMPLETE"},
        }

    def unknown_properties() -> Any:
        raise ExecutionContractError(
            "ENGINE_CALL_FAILED", "synthetic unresolved property inventory read",
            stage="post_dispatch", details={"execution_state_unknown": True},
        )

    monkeypatch.setattr(results, "_resolve_dataset_binding", resolved_candidate)
    monkeypatch.setattr(tree.dset1, "properties", unknown_properties)
    with pytest.raises(ExecutionContractError) as exc_info:
        _dispatch_candidate(monkeypatch, tree, _global_spec())
    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert tree.numerical_list.tags() == []
