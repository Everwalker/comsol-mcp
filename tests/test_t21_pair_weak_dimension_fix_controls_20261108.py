"""T21 weak-dimension fix controls: ``inputDimension()`` is authoritative.

Companion controls for the parent fault tests
``tests/test_t21_pair_weak_dimension_20261108.py`` (which stay untouched).
These lock the two directions of the fix in ``_g3_common._verify_local_selection_endpoint``:

* no false rejection: ``dim()`` is the maximum selected entity dimension and
  may legitimately differ from, or be -1 next to, a correctly applied input
  level (``com.comsol.model.AbstractSelection.dim()``, javap of the installed
  ``apiplugins/com.comsol.api_1.0.0.jar`` 2026-11-08; KB doc 7494/chunk 22758).
  When ``inputDimension()`` reports the requested value, an empty/other
  ``dim()`` must not veto the binding.
* no false claim: an ``inputDimension()`` that is readable but not an ``int``
  (transport mangling) is still an unproven input dimension and fails closed
  as ``EXECUTION_STATE_UNKNOWN`` - and the loop must not then mutate the
  other Pair side.

Synthetic fake-engine controls only; no native COMSOL behavior is claimed.
"""
from __future__ import annotations

import pytest

from tests.test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world


class EmptyDimButCorrectInput(FLocalSel):
    """inputDimension() proves the applied level while dim() reads -1 (empty)."""

    def dim(self):
        return -1


@pytest.mark.parametrize("actual_dim", [-1, 3])
def test_matching_input_dimension_is_not_vetoed_by_other_max_output_dim(actual_dim):
    class OtherMaxDim(FLocalSel):
        def dim(self):
            return actual_dim

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), destination_selection=OtherMaxDim(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "geometry": "geom1", "entity_dimension": 2,
                                                                 "entities": [3]}}})
    assert result["ok"] is True and result["status"] == "APPLIED", result
    geom_set = [row for row in result["selection"]["destination"]["applied"] if row["method"] == "geom/set"]
    assert geom_set and geom_set[0]["dimension_verified_by"] == "inputDimension"
    assert geom_set[0]["endpoint_input_dimension"] == 2
    # dim() is still reported as raw endpoint state on the side, never used
    # to veto or to verify.
    assert result["selection"]["destination"]["engine_state"]["dimension"] == actual_dim


def test_non_int_input_dimension_readback_fails_closed_without_other_side_mutation():
    class ManglingInputDimension(FLocalSel):
        def inputDimension(self):  # type: ignore[override]  # deliberately non-int transport readback
            return "2"  # transport mangling: readable but not an int

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=ManglingInputDimension(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "explicit", "component": "comp1",
                                                            "geometry": "geom1", "entity_dimension": 2,
                                                            "entities": [3]},
                                                 "destination": {"kind": "named", "component": "comp1",
                                                                 "tag": "sel2"}}})
    assert result["ok"] is False and result["status"] == "EXECUTION_STATE_UNKNOWN", result
    assert result["created"] is True and result["partial_change"] is True
    destination = comp1.collections["pair"].items["pair1"].side_selections["destination"]
    assert destination.named_ref is None
    assert not any(method == "named" and args for method, args in destination.calls)
    assert any(row.get("side") == "destination" for row in result["not_executed"])
