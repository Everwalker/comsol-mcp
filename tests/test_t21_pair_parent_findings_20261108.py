"""T21 Pair regressions reproduced by main (synthetic, no COMSOL).

The endpoint may fail or report values different from the requested geometry,
entity dimension, or named selection.  These tests execute the real Pair
adapter against the existing bounded fake worker, with no native claims.
"""
import pytest

from tests.test_t21_pair_selection_20261108 import (
    FLocalSel, FPairNode, LyingNamedSel, create_pair, pair_world,
)


@pytest.mark.parametrize("actual_geometry,actual_dimension", [
    ("other_geom", 2),
    ("geom1", 0),
    (None, None),
])
def test_explicit_selection_requires_geometry_and_dimension_readback(actual_geometry, actual_dimension):
    class UnconfirmedGeometry(FLocalSel):
        def geom(self, *args):
            if args:
                self.calls.append(("geom", args))
                self.geometry_ref = actual_geometry
                self.dimension = actual_dimension
                return None
            return actual_geometry

        def dim(self):
            return actual_dimension

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), destination_selection=UnconfirmedGeometry(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
        "type": "Contact", "geometry": "geom1", "destination": {"kind": "explicit", "component": "comp1",
            "geometry": "geom1", "entity_dimension": 2, "entities": [3]}}})
    # Deliberately prove the entity setter/readback was reached; the target
    # failure must be wrong or missing geometry/dimension, not an earlier gate.
    pair = comp1.collections["pair"].items["pair1"]
    assert pair.side_selections["destination"].entities() == [3]
    assert result["ok"] is False
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["created"] is True
    assert result["partial_change"] is True


def test_source_unknown_stops_destination_mutation():
    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=LyingNamedSel(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
        "type": "Contact", "geometry": "geom1",
        "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"}}})
    pair = comp1.collections["pair"].items["pair1"]
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert pair.side_selections["source"].named_ref == "sel1"
    assert pair.side_selections["destination"].named_ref is None
    assert not any(name == "named" and args for name, args in pair.side_selections["destination"].calls)
    assert any(item.get("side") == "destination" for item in result["not_executed"])


def test_first_side_failure_reports_created_pair_as_partial_change():
    class NoSource(FPairNode):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.unavailable.add("source")

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: NoSource(tag=tag, type_id=str(args[0]))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1", "definition": {
        "type": "Contact", "geometry": "geom1",
        "source": {"kind": "named", "component": "comp1", "tag": "sel1"}}})
    assert "pair1" in comp1.collections["pair"].items
    assert result["created"] is True
    assert result["ok"] is False
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["partial_change"] is True
