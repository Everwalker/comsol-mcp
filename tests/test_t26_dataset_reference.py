"""Focused T26 reproduction: an upstream dataset tag is not a solution tag."""
from __future__ import annotations

import pytest

from comsol_mcp import _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import ManagedBackend
from tests.test_g3_w17 import FNode, FWiredTree


def test_result_evaluate_does_not_resolve_upstream_dataset_as_solution(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = FWiredTree()
    # A derived dataset points to dset1. Make its real solution binding
    # incomplete, while preserving the upstream reference that must not be
    # reinterpreted as a model.sol() tag.
    tree.dset1.props["comp"] = "missing_component"
    tree.dataset_list.items["cut1"] = FNode(
        tag="cut1", type_id="CutPoint3D",
        props={"data": "dset1", "pointx": 0.0, "pointy": 0.0, "pointz": 0.0},
    )
    binding = results._resolve_dataset_binding(tree.model, "cut1")
    assert binding["binding_complete"] is False
    assert binding["dataset_edges"] == [{"property": "data", "from": "cut1", "to": "dset1"}]

    solution_lookups: list[str | None] = []

    def record_solution_lookup(_model, solution_tag, **_kwargs):
        solution_lookups.append(solution_tag)
        return {"pair_mapping_complete": False, "solnum_pairs": []}

    monkeypatch.setattr(results, "_result_solution_binding", record_solution_lookup)
    with pytest.raises(ExecutionContractError) as exc_info:
        results.result_evaluate(tree.worker, "Model", {
            "spec": {"expressions": ["T"], "solution": {"dataset": "cut1"},
                     "aggregate": "global", "complex_mode": "real"}
        })

    assert solution_lookups == []
    assert exc_info.value.code == "DATASET_BINDING_INCOMPLETE"
    assert tree.numerical_list.tags() == []


def test_incomplete_dataset_is_a_managed_prewrite_refusal() -> None:
    tree = FWiredTree()
    tree.dset1.props["comp"] = "missing_component"
    tree.dataset_list.items["cut1"] = FNode(tag="cut1", type_id="CutPoint3D", props={"data": "dset1"})

    response = ManagedBackend._dispatch_with_witness(
        "result.evaluate",
        lambda: results.result_evaluate(tree.worker, "Model", {
            "spec": {"expressions": ["T"], "solution": {"dataset": "cut1"},
                     "aggregate": "global", "complex_mode": "real"}
        }),
        effect="project_write",
    )

    assert response["success"] is False
    assert response["error"]["code"] == "DATASET_BINDING_INCOMPLETE"
    assert response["error"]["stage"] == "validation"
    assert response["data"]["witness"]["mutation_issued"] is False
    assert tree.numerical_list.tags() == []


def test_dataset_worker_unknown_is_not_demoted_or_followed_by_more_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = FWiredTree()
    original = tree.dset1.getString
    reads = []

    def unknown_on_component(name):
        reads.append(name)
        if name == "comp":
            raise ExecutionContractError(
                "ENGINE_CALL_FAILED", "synthetic unresolved worker reply",
                stage="post_dispatch", details={"execution_state_unknown": True},
            )
        return original(name)

    monkeypatch.setattr(tree.dset1, "getString", unknown_on_component)
    with pytest.raises(ExecutionContractError) as exc_info:
        ManagedBackend._dispatch_with_witness(
            "result.evaluate",
            lambda: results.result_evaluate(tree.worker, "Model", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                         "aggregate": "global", "complex_mode": "real"}
            }),
            effect="project_write",
        )

    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert exc_info.value.safe_retry is False
    assert exc_info.value.details["witness"]["mutation_issued"] is False
    assert reads[-1] == "comp"
    assert tree.numerical_list.tags() == []


def test_valid_cut_dataset_read_keeps_solution_and_existing_numerical_table_nodes() -> None:
    tree = FWiredTree(numerical_real=[300.0, 350.0])
    tree.dataset_list.items["cut1"] = FNode(
        tag="cut1", type_id="CutPoint3D",
        props={"data": "dset1", "pointx": 0.0, "pointy": 0.0, "pointz": 0.0},
    )
    numerical = results.result_numerical_manage(tree.worker, "Model", {
        "action": "create", "path": {"segments": [
            {"accessor": "result"}, {"collection": "numerical", "tag": "user_num"}]},
        "definition": {"type_id": "EvalGlobal", "expr": ["T"], "data": "dset1"},
    })
    table = results.result_table_manage(tree.worker, "Model", {
        "action": "create", "path": {"segments": [
            {"accessor": "result"}, {"collection": "table", "tag": "user_table"}]},
        "definition": {"data": [[17.0]]},
    })
    assert numerical["created"] is True and table["created"] is True
    numerical_node = tree.numerical_list.items["user_num"]
    table_node = tree.table_list.items["user_table"]

    result = results.result_at_points(tree.worker, "Model", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "cut1"}, "complex_mode": "real"},
        "points": [[0.0, 0.0, 0.0], [0.01, 0.01, 0.01]],
        "coordinate_unit": "m", "frame": "spatial",
    })
    aggregate_result = results.result_evaluate(tree.worker, "Model", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "cut1"},
                 "aggregate": "global", "complex_mode": "real"}
    })

    assert result["dataset"] == "cut1"
    assert result["solution"] == "sol1"
    assert result["dataset_binding"]["dataset_chain"] == ["dset1", "cut1"]
    assert result["feature_readback"]["data"] == "cut1"
    assert aggregate_result["dataset"] == "cut1"
    assert aggregate_result["solution"] == "sol1"
    assert aggregate_result["dataset_binding"]["dataset_chain"] == ["dset1", "cut1"]
    assert tree.numerical_list.tags() == ["user_num"]
    assert tree.numerical_list.items["user_num"] is numerical_node
    assert tree.table_list.tags() == ["user_table"]
    assert tree.table_list.items["user_table"] is table_node
    assert table_node.getTableData() == [[17.0]]
