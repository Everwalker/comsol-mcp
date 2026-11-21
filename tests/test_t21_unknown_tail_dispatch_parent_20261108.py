"""Parent adversarial controls: Pair writes stop at the first structured UNKNOWN.

These exercise the actual pair adapter with a fake Worker. They do not establish
native COMSOL behaviour or change the existing UNKNOWN resource fences.
"""
from __future__ import annotations

from comsol_mcp._execution_contract import ExecutionContractError
from test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world
from test_t21_probe_unknown_fix_qwen_20261108 import (
    _explicit_source_without_geometry,
    _flagged_worker_failure,
    _unknown_geom_getter,
)


def _install_pair(comp, source):
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=source
    )


def _pair_args(definition):
    return {"action": "create", "component": "comp1", "tag": "pair1",
            "definition": definition}


def test_unknown_source_readback_does_not_probe_destination_afterward():
    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, _unknown_geom_getter())
    result = create_pair(worker, _pair_args(_explicit_source_without_geometry()))
    pair = comp.collections["pair"].items["pair1"]
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["partial_change"] is True
    assert [call for call in pair.calls if call[0] in {"source", "destination"}] == [("source", ())]
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_unknown_source_mutation_does_not_reprobe_either_pair_side():
    class UnknownSet(FLocalSel):
        def set(self, *args):
            self.calls.append(("set", args))
            raise _flagged_worker_failure("synthetic dispatched setter may still be running")

    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, UnknownSet(None))
    result = create_pair(worker, _pair_args(_explicit_source_without_geometry()))
    pair = comp.collections["pair"].items["pair1"]
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["partial_change"] is True
    assert [call for call in pair.calls if call[0] in {"source", "destination"}] == [("source", ())]
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_unknown_in_successful_source_state_probe_prevents_destination_write():
    class LateUnknownNamed(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.getter_count = 0

        def named(self, *args):
            if not args:
                self.getter_count += 1
                if self.getter_count == 2:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic source state timeout")
            return super().named(*args)

    worker, _, comp, _, _ = pair_world()
    source = LateUnknownNamed()
    _install_pair(comp, source)
    result = create_pair(worker, _pair_args({
        "type": "Contact",
        "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }))
    pair = comp.collections["pair"].items["pair1"]
    assert source.getter_count >= 2  # ensure the late fault was reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert pair.side_selections["destination"].named_ref is None
    assert [call for call in pair.calls if call[0] == "destination"] == []
