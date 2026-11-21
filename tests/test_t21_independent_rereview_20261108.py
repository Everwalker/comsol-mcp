"""Independent re-review counterexamples for the T21 Pair UNKNOWN tail (2026-11-08).

Self-contained RED/green requirement tests written by the independent
re-reviewer; they do NOT reuse tests/test_t21_unknown_repeat_probe_parent_20261108.py
and instead pin the requirements from the review brief:

1. no dependent engine call may be dispatched behind a structured UNKNOWN
   inside one operation (including duplicate selection_state probes);
2. the post-write envelope must report partial/not_executed correctly and the
   untouched side must not be probed behind the UNKNOWN;
3. transport UNKNOWN (dispatched call, outcome unconfirmed) must be kept
   distinct from a completed adapter readback mismatch (which may still be
   followed by one honest engine-state read);
4. ordinary optional-getter misses and read-only inspect/list paths must not
   regress.

Fake Worker endpoints only - this is software-level contract evidence, never
a native COMSOL claim.  Fakes extend the T21 pair harness without editing it.
"""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from test_t21_pair_selection_20261108 import (
    FLocalSel,
    FPairNode,
    LyingNamedSel,
    create_pair,
    inspect_pair,
    pair_path,
    pair_world,
)

_NAMED_BOTH = {
    "type": "Contact",
    "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
    "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
}

_EXPLICIT_SOURCE_NO_GEOM = {
    "type": "Contact",
    "source": {"kind": "explicit", "component": "comp1", "entity_dimension": 2,
               "entities": [3]},
    "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
}


class _FlaggedWorkerFailure(Exception):
    """Worker transport failure shape: structured unknown flag, not a
    contract exception (mirrors the Java reply carrying
    execution_state_unknown=true)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.failure = {"code": "ENGINE_CALL_FAILED", "execution_state_unknown": True}


def _create(source: FLocalSel, definition: dict[str, Any]):
    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=source)
    result = create_pair(worker, {"action": "create", "component": "comp1",
                                  "tag": "pair1", "definition": definition})
    return result, comp.collections["pair"].items["pair1"]


# ---------------------------------------------------------------------------
# 1) repeated selection_state getters: the fence must survive probe unification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("getter", ["dim", "geom"])
def test_duplicate_state_getter_unknown_never_ends_applied(getter):
    """A dim()/geom() read that goes structured-UNKNOWN must stop the
    operation regardless of how many times the adapter reads it.

    Two honest fix shapes exist: (a) the duplicate probe is eliminated, so a
    single trustworthy read answers once; (b) the fence catches the Nth read.
    A clean APPLIED with the destination bound is NOT one of them when the
    injected read was actually reached.
    """
    class LateUnknown(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.dim_reads = 0
            self.geom_reads = 0

        def dim(self):
            self.dim_reads += 1
            if getter == "dim" and self.dim_reads == 2:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "synthetic second dim read UNKNOWN")
            return super().dim()

        def geom(self, *args):
            if not args:
                self.geom_reads += 1
                if getter == "geom" and self.geom_reads == 2:
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN", "synthetic second geom read UNKNOWN")
            return super().geom(*args)

    source = LateUnknown()
    result, pair = _create(source, _NAMED_BOTH)
    reads = getattr(source, f"{getter}_reads")
    other_reads = getattr(source, "geom_reads" if getter == "dim" else "dim_reads")
    side_calls = [c[0] for c in pair.calls if c[0] in {"source", "destination"}]
    if reads == 1:
        # single-read shape: reachable only if the duplicate probe was
        # eliminated for BOTH getters (no 4x probe chain left to fault on)
        assert other_reads == 1, (
            f"{getter}: injected fault avoided but {other_reads} reads remain "
            "— probe chain not unified, fence still bypassable")
        assert result["status"] == "APPLIED"
        assert pair.side_selections["destination"].named_ref == "sel2"
        return
    assert reads >= 2  # reachability: the injected fault was actually hit
    assert result["status"] == "EXECUTION_STATE_UNKNOWN", (
        f"status={result['status']}, destination_named="
        f"{pair.side_selections['destination'].named_ref!r}, reads={reads}")
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert pair.side_selections["destination"].named_ref is None
    assert side_calls == ["source"], f"engine calls after UNKNOWN: {side_calls}"


# ---------------------------------------------------------------------------
# 2) mutation carrying a bare EXECUTION_STATE_UNKNOWN code: outcome is NOT
#    confirmed, so it is the transport kind - no re-probe, no other-side read
# ---------------------------------------------------------------------------


def test_bare_unknown_on_dispatched_setter_fences_without_reprobe():
    class DirectUnknown(FLocalSel):
        def set(self, *args):
            self.calls.append(("set", args))
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "synthetic setter outcome unconfirmed")

    source = DirectUnknown(None)
    result, pair = _create(source, _EXPLICIT_SOURCE_NO_GEOM)
    assert [c[0] for c in source.calls].count("set") == 1
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    # Pair.source() exactly once (the binding itself); no post-failure probe
    # of the failed side, no tail probe of the untouched side.
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ())]
    assert pair.side_selections["destination"].named_ref is None
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_flagged_transport_unknown_on_setter_fences_same_way():
    """Positive control (already true today, must not regress): a worker
    failure with the structured flag fences identically."""

    class FlaggedUnknown(FLocalSel):
        def set(self, *args):
            self.calls.append(("set", args))
            raise _FlaggedWorkerFailure("synthetic dispatched setter may still run")

    source = FlaggedUnknown(None)
    result, pair = _create(source, _EXPLICIT_SOURCE_NO_GEOM)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ())]
    assert pair.side_selections["destination"].named_ref is None


# ---------------------------------------------------------------------------
# 3) completed logical mismatch stays a DISTINCT class: one honest
#    post-attempt engine-state read is allowed and must report the engine's
#    own value, never the request echo.
# ---------------------------------------------------------------------------


def test_completed_named_mismatch_keeps_distinct_post_attempt_read():
    source = LyingNamedSel(None)
    result, pair = _create(source, _NAMED_BOTH)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    failed = result["failed"][0]
    assert failed["code"] == "EXECUTION_STATE_UNKNOWN"
    # the endpoint demonstrably answered: an engine-state read after the
    # attempt is honest here and must show the endpoint's own value
    state = failed.get("engine_state_after_attempt")
    assert state is not None and state["named"] == "selLiar"
    assert pair.side_selections["destination"].named_ref is None
    assert any(row.get("side") == "destination" for row in result["not_executed"])


# ---------------------------------------------------------------------------
# 4) non-regression: clean bind and read-only/optional-miss shapes
# ---------------------------------------------------------------------------


def test_clean_named_binding_still_applied_with_endpoint_readback():
    source = FLocalSel(None)
    result, pair = _create(source, _NAMED_BOTH)
    assert result["status"] == "APPLIED"
    # pre-existing W14 _status convention (unchanged since HEAD):
    # partial_change=true means "the engine was mutated", even for a fully
    # applied operation; ok=True/status=APPLIED carry completeness.
    assert result["partial_change"] is True
    assert result["ok"] is True
    assert result["not_executed"] == []
    assert pair.side_selections["source"].named_ref == "sel1"
    assert pair.side_selections["destination"].named_ref == "sel2"
    engine_state = result["selection"]["source"]["engine_state"]
    assert engine_state["named"] == "sel1"
    # probe unification must not change observable success fields
    for field in ("named", "named_error", "entities", "dimension", "geometry"):
        assert field in engine_state


def test_create_tail_nested_unknown_does_not_report_applied():
    """Creating the pair is a write, even when no selection was requested."""
    worker, _, comp, _, _ = pair_world()

    class UnknownDim(FLocalSel):
        def dim(self):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "synthetic inspect dim UNKNOWN")

    sel = UnknownDim(None)
    sel.named_ref = "sel1"
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=sel)
    created = create_pair(worker, {
        "action": "create", "component": "comp1", "tag": "pair2",
        "definition": {"type": "Contact"}})
    assert created["created"] is True
    assert created["status"] == "EXECUTION_STATE_UNKNOWN"
    assert created["execution_state_unknown"] is True
    assert created["partial_change"] is True
    assert created["selection"]["source"]["state_unknown"] is True
    assert created["selection"]["source"]["dimension_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert created["selection"]["destination"]["probe_skipped"] is True


def test_inspect_read_path_stays_lenient_data_shaped():
    """A separate read-only inspect keeps its lenient *_error data shape."""
    worker, _, comp, _, _ = pair_world()

    class UnknownDim(FLocalSel):
        def dim(self):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "synthetic inspect dim UNKNOWN")

    sel = UnknownDim(None)
    sel.named_ref = "sel1"
    # Install a pre-existing fake Pair directly so this independent read test
    # does not issue an inspect after a create reported UNKNOWN.
    comp.collections["pair"].items["pair2"] = FPairNode(
        tag="pair2", type_id="Contact", source_selection=sel)
    inspected = inspect_pair(worker, pair_path("pair2"))
    side = inspected["source"]
    assert side["available"] is True
    assert side["dimension"] is None
    assert side["dimension_error"] is not None
    assert side["dimension_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
