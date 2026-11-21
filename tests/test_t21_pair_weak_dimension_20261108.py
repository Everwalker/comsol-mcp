"""T21: max output dimension cannot substitute for unknown input dimension.

These are fault-injection controls on the real Pair adapter and fake endpoint;
they are not evidence that native COMSOL actually has this behavior.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world


@pytest.mark.parametrize("probe_code", ["API_UNSUPPORTED", "EXECUTION_STATE_UNKNOWN"])
def test_matching_output_dimension_does_not_prove_unreadable_input_or_continue(probe_code):
    class UnconfirmedInputDimension(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.actual_input_dimension = 0
            self.input_probe_calls = 0

        def inputDimension(self):
            self.input_probe_calls += 1
            raise ExecutionContractError(probe_code, "synthetic input-dimension probe failure")

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=UnconfirmedInputDimension())
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
        "type": "Contact", "geometry": "geom1",
        "source": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                   "entity_dimension": 2, "entities": [3]},
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }})
    pair = comp1.collections["pair"].items["pair1"]
    source = pair.side_selections["source"]
    destination = pair.side_selections["destination"]
    # Prove the exact fault model was reached: geometry and entity/output
    # readbacks happen to match while the fake's underlying input is different.
    assert source.input_probe_calls == 1
    assert source.geom() == "geom1" and source.dim() == 2 and source.entities() == [3]
    assert source.actual_input_dimension == 0
    assert result["ok"] is False and result["status"] == "EXECUTION_STATE_UNKNOWN", {
        "status": result["status"],
        "actual_input_dimension": source.actual_input_dimension,
        "observed_max_output_dimension": source.dim(),
        "destination_mutated": destination.named_ref is not None,
    }
    assert destination.named_ref is None
    assert not any(method == "named" and args for method, args in destination.calls)
    assert any(row.get("side") == "destination" for row in result["not_executed"])
    assert result["partial_change"] is True
