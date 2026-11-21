"""Unit-A local-selection fake ABI alignment; no native COMSOL is executed."""
from __future__ import annotations

from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_unit_a_node_public_api import Selection, backend, execution, path


def test_unit_a_explicit_selection_positive_uses_input_dimension(backend):
    b, worker, ref, tickets = backend
    body = {"path": path("a"), "selection": {"kind": "explicit", "entities": [1, 3], "entity_dimension": 2}}
    out = b.invoke("node_selection_set", body, execution(ref), "unit-a-input-dimension", None)
    assert out["success"] is True, out
    selected = worker.model_node.list.items["a"].sel
    assert selected.inputDimension() == 2
    assert selected.entities() == [1, 3]
    assert out["data"]["readback"]["entities"] == [1, 3]
    assert len(tickets) == 1


def test_unit_a_missing_input_dimension_does_not_fake_applied(backend, monkeypatch):
    b, worker, ref, tickets = backend

    def unavailable(_selection):
        raise ExecutionContractError("METHOD_REJECTED", "synthetic missing inputDimension")

    monkeypatch.setattr(Selection, "inputDimension", unavailable, raising=False)
    body = {"path": path("a"), "selection": {"kind": "explicit", "entities": [1, 3], "entity_dimension": 2}}
    out = b.invoke("node_selection_set", body, execution(ref), "unit-a-missing-dimension", None)
    assert out["success"] is False, out
    assert out["error"]["code"] == "EXECUTION_STATE_UNKNOWN", out
    assert worker.model_node.list.items["a"].sel.entities() == [1, 3]
    assert len(tickets) == 1
