"""Additional T21 first-UNKNOWN counterexamples in already-reached Pair paths.

These tests drive the actual Python adapter with fake endpoints, never native
COMSOL. They target documented safety invariants, not new product capabilities.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from test_g3_w14 import _rejected
from test_t21_pair_selection_20261108 import FLocalSel, FPairNode, create_pair, pair_world
from test_t21_probe_unknown_fix_qwen_20261108 import _explicit_source_without_geometry


def _args(definition):
    return {"action": "create", "component": "comp1", "tag": "pair1",
            "definition": definition}


def _named_both():
    return {"type": "Contact",
            "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
            "destination": {"kind": "named", "component": "comp1", "tag": "sel2"}}


@pytest.mark.parametrize("fault_method", ["selection", "tags"])
def test_repeated_named_preflight_unknown_cannot_reach_pair_create(fault_method):
    worker, _, comp, _, _ = pair_world()
    endpoint = comp if fault_method == "selection" else comp.collections["selection"]
    original = getattr(endpoint, fault_method)
    count = []

    def guarded(*args):
        count.append(args)
        if len(count) == 2:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic second preflight probe UNKNOWN")
        return original(*args)

    setattr(endpoint, fault_method, guarded)
    try:
        result = create_pair(worker, _args({
            "type": "Contact",
            "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
        }))
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
    else:
        # Eliminating redundant probes is also valid, but the injected UNKNOWN
        # may not be followed by a successful create.
        if len(count) == 1:
            assert result["status"] == "APPLIED"
            return
        assert result.get("status") == "EXECUTION_STATE_UNKNOWN", (
            f"UNKNOWN reached with calls={len(count)} but result={result.get('status')} "
            f"and pair_exists={'pair1' in comp.collections['pair'].items}"
        )
    assert len(count) == 2
    assert not comp.collections["pair"].mutation_calls()
    assert "pair1" not in comp.collections["pair"].items


def test_unknown_dim_diagnostic_after_unavailable_input_dimension_stops_all_reads():
    class UnknownDim(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.dim_reads = 0

        def inputDimension(self):
            raise _rejected("inputDimension")

        def dim(self):
            self.dim_reads += 1
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic dim diagnostic UNKNOWN")

    worker, _, comp, _, _ = pair_world()
    source = UnknownDim()
    comp.collections["pair"].factory = lambda tag, *a: FPairNode(
        tag=tag, type_id=str(a[0]), source_selection=source)
    result = create_pair(worker, _args(_explicit_source_without_geometry()))
    pair = comp.collections["pair"].items["pair1"]
    assert source.dim_reads >= 1  # diagnostic reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["partial_change"] is True
    assert source.dim_reads == 1  # no repeated state reads after the diagnostic UNKNOWN
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ())]


def test_unrequested_side_tail_probe_unknown_never_reports_applied():
    class UnknownSource(FPairNode):
        def source(self):
            self.calls.append(("source", ()))
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic source read UNKNOWN")

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *a: UnknownSource(tag=tag, type_id=str(a[0]))
    result = create_pair(worker, _args({
        "type": "Contact",
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }))
    pair = comp.collections["pair"].items["pair1"]
    assert pair.side_selections["destination"].named_ref == "sel2"  # earlier write really occurred
    assert [c[0] for c in pair.calls].count("source") == 1  # UNKNOWN reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN", f"status={result['status']} despite tail UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True


def test_unknown_in_post_mismatch_diagnostic_stops_subsequent_side_probes():
    class LyingThenUnknown(FLocalSel):
        def __init__(self):
            super().__init__(None)
            self.getter_count = 0

        def named(self, *args):
            if args:
                self.named_ref = str(args[0])
                return None
            self.getter_count += 1
            if self.getter_count == 1:
                return "selLiar"  # completed logical mismatch
            if self.getter_count == 2:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic diagnostic UNKNOWN")
            return self.named_ref

    worker, _, comp, _, _ = pair_world()
    source = LyingThenUnknown()
    comp.collections["pair"].factory = lambda tag, *a: FPairNode(
        tag=tag, type_id=str(a[0]), source_selection=source)
    result = create_pair(worker, _args(_named_both()))
    pair = comp.collections["pair"].items["pair1"]
    assert source.getter_count >= 2  # post-mismatch UNKNOWN reached
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["partial_change"] is True
    assert source.getter_count == 2
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ()), ("source", ())]
    assert pair.side_selections["destination"].named_ref is None
