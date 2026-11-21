"""Parent counterexamples for repeated selection probes and direct UNKNOWN.

Exercises the real Pair adapter against fake endpoints. No native COMSOL call
is made; a synthetic UNKNOWN models a call whose completion is not confirmed.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world
from test_t21_probe_unknown_fix_qwen_20261108 import _explicit_source_without_geometry


_NAMED_BOTH = {
    "type": "Contact",
    "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
    "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
}


def _create_with_source(source, definition):
    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=source
    )
    result = create_pair(worker, {"action": "create", "component": "comp1",
                                  "tag": "pair1", "definition": definition})
    return result, comp.collections["pair"].items["pair1"]


@pytest.mark.parametrize("getter", ["dim", "geom"])
def test_unknown_on_repeated_strict_state_getter_stops_destination(getter):
    class LateUnknown(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.dim_reads = 0
            self.geom_reads = 0

        def dim(self):
            self.dim_reads += 1
            if getter == "dim" and self.dim_reads == 2:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic second dim read UNKNOWN")
            return super().dim()

        def geom(self, *args):
            if not args:
                self.geom_reads += 1
                if getter == "geom" and self.geom_reads == 2:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic second geom read UNKNOWN")
            return super().geom(*args)

    source = LateUnknown()
    result, pair = _create_with_source(source, _NAMED_BOTH)
    reads = getattr(source, f"{getter}_reads")
    if reads == 1:
        # A single trustworthy observation also avoids the second-call fault.
        assert result["status"] == "APPLIED"
        assert pair.side_selections["destination"].named_ref == "sel2"
        return
    assert reads >= 2  # otherwise the injected fault was not reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN", (
        f"status={result['status']}, destination_named="
        f"{pair.side_selections['destination'].named_ref!r}, "
        f"pair_calls={pair.calls!r}"
    )
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert reads == 2  # no later probe of this endpoint after the UNKNOWN
    assert pair.side_selections["destination"].named_ref is None
    assert not [call for call in pair.calls if call[0] == "destination"]


def test_direct_unknown_setter_does_not_reprobe_or_touch_destination():
    class DirectUnknown(FLocalSel):
        def set(self, *args):
            self.calls.append(("set", args))
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic setter outcome unconfirmed")

    source = DirectUnknown(None)
    result, pair = _create_with_source(source, _explicit_source_without_geometry())
    assert [call[0] for call in source.calls].count("set") == 1  # fault was reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert [call for call in pair.calls if call[0] in {"source", "destination"}] == [("source", ())]
