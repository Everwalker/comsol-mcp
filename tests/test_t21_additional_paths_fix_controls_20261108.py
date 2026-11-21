"""T21 additional-paths fix controls (2026-11-08).

Companion controls for the parent counterexample file
``tests/test_t21_unknown_additional_paths_parent_20261108.py``, the re-review
file ``tests/test_t21_independent_rereview_20261108.py`` and the repeat-probe
file ``tests/test_t21_unknown_repeat_probe_parent_20261108.py``.  They pin the
repaired contract of the Pair write path on the real adapter, with fake
endpoints only - no COMSOL, Worker, SSH or Desktop was started, and nothing
here is a native-behaviour claim.

What is pinned:

* preflight reads the named-selection gate exactly once per pair side: no
  second ``component.selection()``/``tags()`` dispatch exists for an UNKNOWN to
  be swallowed into, and a structured UNKNOWN on the one read still refuses
  ``pair().create()`` with zero mutations;
* probe unification: a clean named binding reads ``dim()``/``geom()`` a bounded
  number of times per side (the apply-time state read plus the outer per-side
  state read), so the strict fence covers every dispatch;
* a completed readback mismatch (``selLiar``) keeps its distinct class and its
  one honest post-attempt engine read, including the untouched-side report
  probes;
* a structured UNKNOWN inside that post-attempt diagnostic stops every later
  read, and the partially observed engine state stays reported with an explicit
  unknown marker;
* the closing per-side report probe fences when the dispatched side getter
  itself returns UNKNOWN: an applied side is not reported as ``APPLIED``, the
  probe failure is recorded as data, and no further probe runs;
* the completed-readback distinction is local provenance: a foreign exception
  carrying a ``selection_readback_completed`` detail is NOT believed and is
  fenced.
"""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_common import (
    mark_selection_readback_completed,
    selection_readback_completed,
)
from test_g3_w14 import FakeEngineError, _rejected
from test_t21_pair_selection_20261108 import (
    FLocalSel,
    FPairNode,
    create_pair,
    pair_world,
)
from test_t21_probe_unknown_fix_qwen_20261108 import _explicit_source_without_geometry

_NAMED_BOTH = {
    "type": "Contact",
    "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
    "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
}


def _args(definition: dict[str, Any]) -> dict[str, Any]:
    return {"action": "create", "component": "comp1", "tag": "pair1", "definition": definition}


def _create(source: Any, definition: dict[str, Any]):
    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=source)
    result = create_pair(worker, _args(definition))
    return worker, comp, result, comp.collections["pair"].items["pair1"]


# ---------------------------------------------------------------------------
# 1) preflight: one read per gate, and an UNKNOWN on that read refuses create
# ---------------------------------------------------------------------------


def test_preflight_reads_named_gate_once_per_side_positive():
    """The duplicate selection()/tags() read is gone; the single pass decides."""
    worker, _, comp, _, _ = pair_world()
    result = create_pair(worker, _args(_NAMED_BOTH))
    assert result["status"] == "APPLIED"
    assert result["pre_checks"]["source_named_existence"] == "validated"
    assert result["pre_checks"]["destination_named_existence"] == "validated"
    # component.selection() once per side, and the selection container's tags()
    # once per side (comp1 is read twice: source then destination), never twice
    # per side as the old redundant require_named_selection pass did.
    assert [c for c in comp.calls if c[0] == "selection" and c[1] in {(), (None,)}] == [
        ("selection", (None,)), ("selection", (None,))]
    tags_calls = comp.collections["selection"].calls.count(("tags", ()))
    assert tags_calls == 2, comp.collections["selection"].calls


@pytest.mark.parametrize("fault_method", ["selection", "tags"])
def test_preflight_unknown_on_the_single_read_refuses_create(fault_method: str):
    worker, _, comp, _, _ = pair_world()
    endpoint = comp if fault_method == "selection" else comp.collections["selection"]
    original = getattr(endpoint, fault_method)
    count: list[Any] = []

    def guarded(*args: Any) -> Any:
        count.append(args)
        if len(count) == 1:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic first preflight UNKNOWN")
        return original(*args)

    setattr(endpoint, fault_method, guarded)
    with pytest.raises(ExecutionContractError) as caught:
        create_pair(worker, _args({"type": "Contact",
                                   "source": {"kind": "named", "component": "comp1", "tag": "sel1"}}))
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert len(count) == 1, "no second read behind the UNKNOWN"
    assert comp.collections["pair"].mutation_calls() == []
    assert "pair1" not in comp.collections["pair"].items


# ---------------------------------------------------------------------------
# 2) probe unification: every dim()/geom() dispatch is inside the strict fence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("getter", ["dim", "geom"])
def test_state_getters_are_read_a_bounded_number_of_times(getter: str):
    """4 dispatches per side (2 per state read) collapse to 2 (1 per state read)."""
    class Counting(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.dim_reads = 0
            self.geom_reads = 0

        def dim(self) -> Any:
            self.dim_reads += 1
            return super().dim()

        def geom(self, *args: Any) -> Any:
            if not args:
                self.geom_reads += 1
            return super().geom(*args)

    source = Counting()
    _, _, result, _ = _create(source, _NAMED_BOTH)
    assert result["status"] == "APPLIED"
    reads = getattr(source, f"{getter}_reads")
    # apply-time closing state read + the outer per-side state read; the
    # redundant selection_dimension()/probe_geometry_tag() re-dispatch is gone.
    assert reads == 2, f"{getter}() dispatched {reads} times for one side"


# ---------------------------------------------------------------------------
# 3) completed logical mismatch keeps its diagnostic; a diagnostic that goes
#    UNKNOWN stops everything behind it
# ---------------------------------------------------------------------------


def test_completed_mismatch_still_reports_engine_state_and_other_side():
    class Liar(FLocalSel):
        def named(self, *args: Any) -> Any:
            self.calls.append(("named", args))
            if args:
                self.named_ref = str(args[0])
                return None
            return "selLiar" if self.named_ref else None

    _, _, result, pair = _create(Liar(None), _NAMED_BOTH)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    failed = result["failed"][0]
    assert failed["engine_state_after_attempt"]["named"] == "selLiar"
    assert "state_unknown" not in failed["engine_state_after_attempt"]
    assert [c[0] for c in pair.calls if c[0] in {"source", "destination"}] == [
        "source", "source", "source", "destination"]


def test_post_attempt_diagnostic_unknown_marks_state_and_stops_further_reads():
    class LiarThenUnknown(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.getter_count = 0

        def named(self, *args: Any) -> Any:
            if args:
                self.named_ref = str(args[0])
                return None
            self.getter_count += 1
            if self.getter_count == 1:
                return "selLiar"
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic diagnostic UNKNOWN")

    _, _, result, pair = _create(LiarThenUnknown(), _NAMED_BOTH)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert pair.side_selections["destination"].named_ref is None
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ()), ("source", ())]
    failed = result["failed"][0]
    state = failed["engine_state_after_attempt"]
    assert state["state_unknown"] is True
    assert state["state_unknown_probe"] == "named"
    assert state["named_error"]["code"] == "EXECUTION_STATE_UNKNOWN"


# ---------------------------------------------------------------------------
# 4) the closing report probe fences when the dispatched getter went UNKNOWN
# ---------------------------------------------------------------------------


def test_tail_probe_unknown_reports_probe_failure_and_keeps_applied_side():
    class UnknownSource(FPairNode):
        def source(self) -> Any:
            self.calls.append(("source", ()))
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic source read UNKNOWN")

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: UnknownSource(tag=tag, type_id=str(args[0]))
    result = create_pair(worker, _args({
        "type": "Contact",
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }))
    pair = comp.collections["pair"].items["pair1"]
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["ok"] is False
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert [c[0] for c in pair.calls].count("source") == 1
    assert pair.side_selections["destination"].named_ref == "sel2"  # no blind rollback
    assert result["selection"]["source"]["state_unknown"] is True
    assert result["selection"]["source"]["probe_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    probes = [row for row in result["failed"] if row["stage"] == "selection_state_probe"]
    assert len(probes) == 1 and probes[0]["side"] == "source"
    assert probes[0]["mutation_issued"] is False
    assert any("closing engine-state probe" in note for note in result["notes"])


def test_tail_probe_unknown_skips_the_remaining_side_probe():
    """Neither side was requested: the first unknown report probe ends the loop."""
    class UnknownSource(FPairNode):
        def source(self) -> Any:
            self.calls.append(("source", ()))
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic source read UNKNOWN")

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: UnknownSource(tag=tag, type_id=str(args[0]))
    result = create_pair(worker, _args({"type": "Contact"}))
    pair = comp.collections["pair"].items["pair1"]
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert [c[0] for c in pair.calls if c[0] in {"source", "destination"}] == ["source"]
    assert result["selection"]["destination"]["probe_skipped"] is True


# ---------------------------------------------------------------------------
# 5) provenance: the completed-readback marker is local, not a wire detail
# ---------------------------------------------------------------------------


class _ForeignReadbackClaim(ExecutionContractError):
    """Endpoint-side raise that *claims* a completed readback in its details."""

    def __init__(self) -> None:
        super().__init__(
            "EXECUTION_STATE_UNKNOWN", "foreign endpoint claims its readback completed",
            details={"selection_readback_completed": True},
        )


def test_foreign_details_marker_is_not_trusted():
    """A worker-supplied selection_readback_completed detail must not unlock probing."""
    assert selection_readback_completed(_ForeignReadbackClaim()) is False
    assert selection_readback_completed(
        ExecutionContractError("EXECUTION_STATE_UNKNOWN", "bare unknown")) is False
    assert selection_readback_completed(
        mark_selection_readback_completed(
            ExecutionContractError("EXECUTION_STATE_UNKNOWN", "our completed readback"))) is True


def test_marker_does_not_travel_through_implicit_context():
    """Only an explicit raise-from chain carries our own provenance."""
    marked = mark_selection_readback_completed(
        ExecutionContractError("EXECUTION_STATE_UNKNOWN", "our completed readback"))
    outer = ExecutionContractError("ENGINE_CALL_FAILED", "unrelated later failure")
    try:
        raise marked
    except ExecutionContractError:
        try:
            raise outer
        except ExecutionContractError as caught:
            assert caught.__context__ is marked
            assert selection_readback_completed(caught) is False
    chained = ExecutionContractError("ENGINE_CALL_FAILED", "wrapped our completed readback")
    chained.__cause__ = marked
    assert selection_readback_completed(chained) is True


def test_bare_unknown_named_setter_fences_like_a_bare_unknown_entity_setter():
    """The fail-closed default covers the named() mutation shape too."""
    class UnknownNamed(FLocalSel):
        def named(self, *args: Any) -> Any:
            self.calls.append(("named", args))
            if args:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN",
                                             "synthetic named() binding outcome unconfirmed")
            return self.named_ref

    _, _, result, pair = _create(UnknownNamed(None), _NAMED_BOTH)
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ())]
    assert pair.side_selections["destination"].named_ref is None
    assert result["failed"][0]["engine_state_after_attempt"]["state_unknown"] is True
    assert result["failed"][0]["engine_state_after_attempt"]["probe"] == "mutation"


# ---------------------------------------------------------------------------
# 6) boundaries the fix must not widen: optional-getter semantics intact
# ---------------------------------------------------------------------------


def test_input_dimension_and_dim_both_rejected_still_fails_closed_with_diagnostic():
    """Ordinary METHOD_REJECTED readbacks keep the completed-readback path."""

    class NoInputDimNoDim(FLocalSel):
        def inputDimension(self) -> int:  # noqa: N802 - Java spelling
            raise _rejected("inputDimension")

        def dim(self) -> Any:
            raise _rejected("dim")

    _, _, result, pair = _create(NoInputDimNoDim(None), _explicit_source_without_geometry())
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    failed = result["failed"][0]
    assert "dim()=None is the maximum entity dimension" in failed["message"]
    # the endpoint answered both reads: the honest post-attempt engine-state
    # read stays (this is a completed observation, not a transport UNKNOWN)
    assert "state_unknown" not in failed["engine_state_after_attempt"]
    assert [c[0] for c in pair.calls if c[0] in {"source", "destination"}][0] == "source"


def test_unknown_dim_diagnostic_after_rejected_input_dimension_never_probes_other_side():
    """The parent counterexample, pinned with the diagnostic's own identity."""

    class UnknownDim(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.dim_reads = 0

        def inputDimension(self) -> int:  # noqa: N802 - Java spelling
            raise _rejected("inputDimension")

        def dim(self) -> Any:
            self.dim_reads += 1
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic dim diagnostic UNKNOWN")

    source = UnknownDim()
    _, _, result, pair = _create(source, _explicit_source_without_geometry())
    assert source.dim_reads == 1
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert [c for c in pair.calls if c[0] in {"source", "destination"}] == [("source", ())]
    failed = result["failed"][0]
    assert failed["engine_state_after_attempt"]["probe"] == "dim"
    assert not any("dim()=None is the maximum entity dimension" in row["message"]
                   for row in result["failed"])


def test_rejected_worker_transport_still_wraps_as_engine_call_failure():
    """The pre-existing transport shape (non-UNKNOWN code) is unchanged."""

    class RejectedSet(FLocalSel):
        def set(self, *args: Any) -> None:
            self.calls.append(("set", args))
            raise FakeEngineError("SecurityException: METHOD_REJECTED (set)", code="METHOD_REJECTED")

    _, _, result, pair = _create(RejectedSet(None), _explicit_source_without_geometry())
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["execution_state_unknown"] is False
    failed = result["failed"][0]
    assert failed["code"] == "ENGINE_CALL_FAILED"
    assert "state_unknown" not in failed["engine_state_after_attempt"]
    assert [c[0] for c in pair.calls if c[0] in {"source", "destination"}][0] == "source"
