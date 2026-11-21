"""T21: synthetic probe UNKNOWN must not be demoted into later mutations.

Faults are injected at adapter getters on fake endpoints; they do not claim
that a real COMSOL transport has this behavior.
"""
from __future__ import annotations

from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world


def test_unknown_optional_geometry_probe_cannot_precede_other_side_mutation():
    class UnknownOptionalGeometry(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.getter_failures = 0

        def geom(self, *args):
            if not args:
                self.getter_failures += 1
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic geom getter timeout")
            return super().geom(*args)

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=UnknownOptionalGeometry())
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
        "type": "Contact",
        "source": {"kind": "explicit", "component": "comp1", "entity_dimension": 2, "entities": [3]},
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }})
    pair = comp.collections["pair"].items["pair1"]
    source, destination = pair.side_selections["source"], pair.side_selections["destination"]
    assert source.getter_failures >= 1, "fault must be reached after the source mutation"
    assert result["ok"] is False and result["status"] == "EXECUTION_STATE_UNKNOWN", {
        "actual_status": result["status"], "geom_probe_unknown_count": source.getter_failures,
        "destination_mutated": destination.named_ref is not None,
    }
    assert destination.named_ref is None
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_unknown_precheck_geometry_probe_cannot_be_followed_by_pair_create():
    worker, _, comp, _, _ = pair_world()
    original_geom = comp.geom
    probe_failures = []

    def geom_with_first_probe_unknown(*args):
        if not args and not probe_failures:
            probe_failures.append(True)
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic precheck timeout")
        return original_geom(*args)

    setattr(comp, "geom", geom_with_first_probe_unknown)
    try:
        result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
            "type": "Contact", "geometry": "geom1",
            "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
        }})
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
        result = {"ok": False, "status": exc.code}
    assert probe_failures == [True], "fault must hit the actual pair geometry precheck"
    assert "pair1" not in comp.collections["pair"].items, {
        "result_status": result.get("status"), "pair_created_after_precheck_unknown": True,
    }
    assert result["ok"] is False and result["status"] == "EXECUTION_STATE_UNKNOWN"
