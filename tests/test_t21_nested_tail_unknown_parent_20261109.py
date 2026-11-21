"""T21 Pair create: UNKNOWN inside the unrequested-side report is not read-only.

The Pair creation is a mutation even when no side bindings were requested.
The real Python adapter is exercised with fake engine endpoints only; no
native COMSOL, Worker, Desktop, or SSH operation is performed.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world


@pytest.mark.parametrize("requested", ["none", "destination", "source"])
def test_nested_unknown_in_unrequested_side_report_fences_pair_create(requested):
    class UnknownNestedSelection(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.named_reads = 0

        def named(self, *args):
            if args:
                return super().named(*args)
            self.named_reads += 1
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic nested report getter UNKNOWN")

    worker, _, comp, _, _ = pair_world()
    faulty = UnknownNestedSelection()
    faulty_side = "destination" if requested == "source" else "source"
    comp.collections["pair"].factory = lambda tag, *a: FPairNode(
        tag=tag, type_id=str(a[0]), **{f"{faulty_side}_selection": faulty})
    definition: dict[str, object] = {"type": "Contact"}
    if requested != "none":
        definition[requested] = {"kind": "named", "component": "comp1", "tag": "sel1"}
    result = create_pair(worker, {"action": "create", "component": "comp1",
                                  "tag": "pair1", "definition": definition})
    pair = comp.collections["pair"].items["pair1"]
    assert faulty.named_reads == 1  # the nested getter, not just the outer side getter, failed
    assert result["created"] is True
    if requested == "none":
        # No destination probe may be sent after the source's nested UNKNOWN.
        assert [c[0] for c in pair.calls if c[0] in {"source", "destination"}] == ["source"]
    else:
        assert pair.side_selections[requested].named_ref == "sel1"  # already applied, not rolled back
    assert result["status"] == "EXECUTION_STATE_UNKNOWN", {
        "status": result["status"], "nested_state_unknown": result["selection"][faulty_side].get("state_unknown"),
        "sides_dispatched": [c[0] for c in pair.calls if c[0] in {"source", "destination"}],
    }
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert result["selection"][faulty_side]["state_unknown"] is True
