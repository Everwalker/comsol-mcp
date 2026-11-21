"""T21 nested-tail fix controls (qwen, 2026-11-09).

Companion controls for the parent counterexample file
``tests/test_t21_nested_tail_unknown_parent_20261109.py``.  They pin the
repaired contract of the Pair create *tail* report path on the real adapter,
with fake engine endpoints only - no COMSOL, Worker, SSH or Desktop was
started, and nothing here is a native-behaviour claim.

Gap being fixed: ``pair().create()`` is a mutation even when no side binding
was requested.  The closing per-side report probe reused ``_pair_side_probe``,
whose nested ``selection_state(..., stop_at_first_unknown=True)`` correctly
records ``state_unknown`` when a dispatched ``named()``/``isInheriting()``/
``entities()``/``dim()``/``geom()`` getter returns a structured
EXECUTION_STATE_UNKNOWN - but the create tail caller never observed that
flag: it still probed the next side and reported the whole operation as
``APPLIED`` (a false applied claim on a mutated model).

What is pinned:

* the nested UNKNOWN fences the envelope (EXECUTION_STATE_UNKNOWN,
  execution_state_unknown, partial_change, one read-only probe failure with
  mutation_issued false), keeps observed partial state as data, and never
  forces a redundant second probe;
* no further engine read behind the UNKNOWN: the remaining side is reported
  probe_skipped, already-applied bindings stay (no rollback);
* the note recorded by the pre-existing side-getter fence also covers the
  nested case;
* boundaries the fix must not widen: an ordinary METHOD_REJECTED optional-getter
  miss inside the same tail report stays data, not an error (status APPLIED),
  and the read-only inspect/list routes keep their lenient data shape with
  both sides still reported.
"""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from test_g3_w14 import _rejected
from test_t21_pair_selection_20261108 import (
    FLocalSel,
    FPairNode,
    create_pair,
    inspect_pair,
    pair_path,
    pair_world,
)


def _nested_unknown_selection() -> Any:
    class UnknownNestedSelection(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.named_reads = 0

        def named(self, *args: Any) -> Any:
            if args:
                return super().named(*args)
            self.named_reads += 1
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "synthetic nested report getter UNKNOWN")

    return UnknownNestedSelection()


def _create_with_faulty_side(faulty_side: str, requested: str):
    worker, _, comp, _, _ = pair_world()
    faulty = _nested_unknown_selection()
    comp.collections["pair"].factory = lambda tag, *a: FPairNode(
        tag=tag, type_id=str(a[0]), **{f"{faulty_side}_selection": faulty})
    definition: dict[str, object] = {"type": "Contact"}
    if requested != "none":
        definition[requested] = {"kind": "named", "component": "comp1", "tag": "sel1"}
    result = create_pair(worker, {"action": "create", "component": "comp1",
                                  "tag": "pair1", "definition": definition})
    return result, comp.collections["pair"].items["pair1"], faulty


# ---------------------------------------------------------------------------
# the fenced envelope: unknown status, read-only probe failure, partial state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requested", ["none", "destination", "source"])
def test_nested_tail_unknown_records_readonly_probe_failure_once(requested: str):
    faulty_side = "destination" if requested == "source" else "source"
    result, pair, faulty = _create_with_faulty_side(faulty_side, requested)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["ok"] is False
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert result["created"] is True  # pair().create() stands; no blind rollback
    # exactly one probe-stage failure, and it is a read-only observation
    probes = [row for row in result["failed"] if row["stage"] == "selection_state_probe"]
    assert len(probes) == 1, result["failed"]
    assert probes[0]["side"] == faulty_side
    assert probes[0]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert probes[0]["mutation_issued"] is False
    assert probes[0]["partial_change"] is True
    assert probes[0]["execution_state_unknown"] is True
    # the fix must not force a second probe: the failing nested getter was
    # read once, and the pair side getter was dispatched exactly once
    assert faulty.named_reads == 1
    assert [c[0] for c in pair.calls if c[0] == faulty_side].count(faulty_side) == 1


@pytest.mark.parametrize("requested", ["none", "destination", "source"])
def test_nested_tail_unknown_keeps_partial_observation_as_data(requested: str):
    faulty_side = "destination" if requested == "source" else "source"
    result, _, _ = _create_with_faulty_side(faulty_side, requested)
    row = result["selection"][faulty_side]
    assert row["available"] is True
    assert row["state_unknown"] is True
    assert row["state_unknown_probe"] == "named"
    assert row["state_unknown_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    # what *did* complete before the UNKNOWN stays reported; the requested
    # binding (when any) stays applied and is not rolled back
    assert row["named_error"] is not None
    if requested != "none":
        assert result["selection"][requested]["applied"]
    # the pre-existing closing-probe note covers the nested trigger too
    assert any("closing engine-state probe" in note for note in result["notes"])


def test_nested_tail_unknown_skips_remaining_side_without_probe():
    result, pair, _ = _create_with_faulty_side("source", "none")
    dispatched = [c[0] for c in pair.calls if c[0] in {"source", "destination"}]
    assert dispatched == ["source"]
    skipped = result["selection"]["destination"]
    assert skipped["probe_skipped"] is True
    assert skipped["state_unknown"] is True
    assert skipped["engine_state"] is None


# ---------------------------------------------------------------------------
# boundaries the fix must not widen
# ---------------------------------------------------------------------------


def test_non_unknown_optional_rejection_in_tail_report_stays_data():
    """A METHOD_REJECTED nested getter is an optional miss, not an error."""

    class RejectedNamed(FLocalSel):
        def named(self, *args: Any) -> Any:
            if args:
                return super().named(*args)
            raise _rejected("named")

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *a: FPairNode(
        tag=tag, type_id=str(a[0]), source_selection=RejectedNamed(None))
    result = create_pair(worker, {"action": "create", "component": "comp1",
                                  "tag": "pair1", "definition": {"type": "Contact"}})
    assert result["status"] == "APPLIED"
    assert result["failed"] == []
    assert result["execution_state_unknown"] is False
    row = result["selection"]["source"]
    assert "state_unknown" not in row
    assert row["named_error"]["code"] == "METHOD_REJECTED"
    # the untouched side is still reported normally
    assert result["selection"]["destination"]["available"] is True


def test_read_only_inspect_route_keeps_lenient_data_shape():
    """Preserve observed data but stop after a read-only Worker UNKNOWN."""
    worker, _, comp, _, _ = pair_world()
    faulty = _nested_unknown_selection()
    comp.collections["pair"].items["pair1"] = FPairNode(
        tag="pair1", type_id="Identity", source_selection=faulty)
    inspected = inspect_pair(worker, pair_path("pair1"))
    assert inspected["source"]["available"] is True
    assert inspected["source"]["state_unknown"] is True
    assert inspected["source"]["named_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    # A read-only getter can still be running after an RPC timeout. Its
    # observed error stays data-shaped, but later worker reads must not run.
    assert inspected["destination"]["available"] is None
    assert inspected["destination"]["probe_skipped"] is True
    assert inspected["status"] == "EXECUTION_STATE_UNKNOWN"
    assert inspected["execution_state_unknown"] is True
    assert inspected["partial_change"] is False
    assert any(row["field"] == "destination" for row in inspected["not_executed"])
    node = comp.collections["pair"].items["pair1"]
    assert ("destination", ()) not in node.calls
    assert faulty.named_reads == 1
