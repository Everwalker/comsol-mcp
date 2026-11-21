"""T21 fix controls: a structured UNKNOWN probe stops the Pair write chain.

Companion controls for ``test_t21_probe_unknown_propagation_20261108.py`` after
the shared-``call_probe``/Pair fix (evidence
``docs/universal_mcp_execution/evidence/full/T21-probe-unknown-fix-qwen-20261108.md``).

These fake-endpoint tests assert the repair's precise contract on BOTH chains -
the direct ``definition.pair_manage`` adapter call and the public
``_g3_ops.dispatch`` route:

* a zero-argument getter that fails with an explicit ``EXECUTION_STATE_UNKNOWN``
  code, or with a different code but the structured
  ``failure/reply.execution_state_unknown`` flag, is normalized by
  ``call_probe`` and never demoted into ``not_verifiable``;
* the first structured UNKNOWN inside one operation survives: no later
  readable probe rewrites it, and no further engine call is dispatched on the
  UNKNOWN endpoint afterwards (the failing side's getter is hit exactly once;
  the Pair post-failure read-back reports ``state_unknown`` instead of
  re-probing);
* the created/partial/not_executed envelope is preserved after a post-create
  UNKNOWN (no blind rollback, no destination-side mutation), while a pre-write
  UNKNOWN raises before ``pair().create()`` with zero mutation calls;
* a mutation failure whose worker code is not UNKNOWN but which carries the
  structured unknown flag still sets ``execution_state_unknown`` in the
  envelope;
* positive controls keep ordinary optional getters compatible: a
  METHOD_REJECTED zero-argument ``geom()`` probe still verifies through
  ``inputDimension()`` and creates, and a rejected geometry-list probe still
  records ``not_verifiable_engine_tags_unreadable`` while the create proceeds;
  read-only ``inspect`` reports an UNKNOWN getter as data, it does not raise.
"""
from __future__ import annotations

from typing import Any

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_common import call_probe, probe_state_unknown
from comsol_mcp._g3_ops import dispatch as public_dispatch
from comsol_mcp import _g3_w14 as w14

try:  # pragma: no cover - import shim
    from tests.test_g3_w14 import FakeEngineError, _rejected  # type: ignore[no-redef]
    from tests.test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )
except ImportError:  # pragma: no cover
    from test_g3_w14 import FakeEngineError, _rejected  # type: ignore[no-redef]
    from test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )


def _flagged_worker_failure(message: str, code: str = "ENGINE_TIMEOUT") -> FakeEngineError:
    """Worker failure with a non-UNKNOWN code but the structured unknown flag."""
    exc = FakeEngineError(message, code=code)
    exc.failure = {**exc.failure, "execution_state_unknown": True}  # type: ignore[assignment]
    exc.reply = {**exc.reply, "execution_state_unknown": True}  # type: ignore[assignment]
    return exc


def _unknown_geom_getter(base: type = FLocalSel) -> Any:
    class UnknownGeom(base):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            super().__init__(None)
            self.getter_failures = 0

        def geom(self, *args: Any) -> Any:
            if not args:
                self.getter_failures += 1
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic geom getter timeout")
            return super().geom(*args)

    return UnknownGeom()


def _explicit_source_without_geometry() -> dict[str, Any]:
    return {
        "type": "Contact",
        "source": {"kind": "explicit", "component": "comp1", "entity_dimension": 2, "entities": [3]},
        "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
    }


def _setter_call_count(sel: Any) -> list[Any]:
    return [c for c in sel.calls
            if c[0] in {"set", "all", "init"} or (c[0] in {"named", "geom"} and c[1])]


# ---------------------------------------------------------------------------
# shared call_probe contract (unit level, no operation involved)
# ---------------------------------------------------------------------------


class _FailingGetter:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def probe(self) -> None:
        raise self._exc


def test_call_probe_honestly_marks_explicit_unknown_instead_of_flattening_it():
    probe = call_probe(_FailingGetter(
        ExecutionContractError("EXECUTION_STATE_UNKNOWN", "getter timeout")), "probe")
    assert probe["ok"] is False and probe["value"] is None
    assert probe["execution_state_unknown"] is True
    assert probe["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert probe["error"]["execution_state_unknown"] is True
    assert probe_state_unknown(probe) is True


def test_call_probe_keeps_structured_unknown_under_a_non_unknown_code():
    probe = call_probe(_FailingGetter(_flagged_worker_failure("engine may still be running")), "probe")
    assert probe["ok"] is False
    # the worker's own code is preserved, not rewritten, but the unknown bit
    # rides along instead of being lost to "unreadable"
    assert probe["error"]["code"] == "ENGINE_TIMEOUT"
    assert probe["error"]["execution_state_unknown"] is True
    assert probe["execution_state_unknown"] is True
    assert probe_state_unknown(probe) is True


def test_call_probe_keeps_ordinary_rejected_probe_optional():
    probe = call_probe(_FailingGetter(_rejected("geom")), "probe")
    assert probe["ok"] is False
    assert probe["execution_state_unknown"] is False
    assert probe["error"]["execution_state_unknown"] is False
    assert probe["error"]["code"] == "METHOD_REJECTED"
    assert probe["error"]["allowlist_entry_required"] == "probe"
    assert probe_state_unknown(probe) is False


# ---------------------------------------------------------------------------
# case A: post-create binding probe UNKNOWN (direct adapter chain)
# ---------------------------------------------------------------------------


def test_pair_source_probe_unknown_stops_before_destination_mutation_direct():
    worker, _, comp, _, _ = pair_world()
    fault = _unknown_geom_getter()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=fault)
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": _explicit_source_without_geometry()})
    pair = comp.collections["pair"].items["pair1"]
    source, destination = pair.side_selections["source"], pair.side_selections["destination"]

    # first structured UNKNOWN protected: the operation ends UNKNOWN even
    # though the entity round trip and a later inputDimension() read were fine
    assert result["ok"] is False and result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True and result["partial_change"] is True
    assert result["created"] is True  # pair().create() already ran: no blind rollback

    # no re-probe of the UNKNOWN endpoint: the failing zero-arg geom() getter
    # was hit exactly once, and Pair.source() was called exactly once
    assert fault.getter_failures == 1, "no additional engine call after the structured UNKNOWN"
    assert [c for c in pair.calls if c[0] == "source"] == [("source", ())]

    failed = result["failed"][0]
    assert failed["stage"] == "selection" and failed["side"] == "source"
    assert failed["code"] == "EXECUTION_STATE_UNKNOWN"
    assert failed["execution_state_unknown"] is True and failed["mutation_issued"] is True
    # the post-failure read-back reports the unknown state instead of probing again
    assert failed["engine_state_after_attempt"]["state_unknown"] is True
    assert failed["engine_state_after_attempt"]["probe"] == "geom"

    # destination mutation never dispatched, and it is recorded as not_executed
    assert destination.named_ref is None
    assert _setter_call_count(destination) == []
    assert any(row.get("side") == "destination" for row in result["not_executed"])
    assert comp.collections["selection"].items["sel2"].calls == []


def test_pair_source_probe_flagged_worker_failure_stops_before_destination_mutation_direct():
    worker, _, comp, _, _ = pair_world()

    class FlaggedGeom(FLocalSel):
        def __init__(self) -> None:
            super().__init__(None)
            self.getter_failures = 0

        def geom(self, *args: Any) -> Any:
            if not args:
                self.getter_failures += 1
                raise _flagged_worker_failure("engine may still be running")
            return super().geom(*args)

    fault = FlaggedGeom()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=fault)
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": _explicit_source_without_geometry()})
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    # a non-UNKNOWN worker code with the structured flag normalizes to the
    # operation's EXECUTION_STATE_UNKNOWN outcome, never to APPLIED
    assert result["failed"][0]["execution_state_unknown"] is True
    assert fault.getter_failures == 1
    pair = comp.collections["pair"].items["pair1"]
    assert pair.side_selections["destination"].named_ref is None


def test_pair_source_mutation_failure_with_flagged_unknown_keeps_unknown_in_envelope():
    worker, _, comp, _, _ = pair_world()

    class FlaggedSet(FLocalSel):
        def set(self, *args: Any) -> None:
            self.calls.append(("set", args))
            raise _flagged_worker_failure("set dispatched; engine state unknown")

    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=FlaggedSet(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": _explicit_source_without_geometry()})
    failed = result["failed"][0]
    # the wrapped mutation keeps the engine-call code but the structured
    # unknown flag rides through __cause__ into the envelope
    assert failed["code"] == "ENGINE_CALL_FAILED"
    assert failed["execution_state_unknown"] is True
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["created"] is True
    pair = comp.collections["pair"].items["pair1"]
    assert pair.side_selections["destination"].named_ref is None
    assert any(row.get("side") == "destination" for row in result["not_executed"])


# ---------------------------------------------------------------------------
# case B: pre-write geometry precheck probe UNKNOWN (direct adapter chain)
# ---------------------------------------------------------------------------


def _install_flaky_component_geom(comp: Any, exc_factory: Any) -> list[bool]:
    original_geom = comp.geom
    hits: list[bool] = []

    def geom_with_first_probe_unknown(*args: Any) -> Any:
        if not args and not hits:
            hits.append(True)
            raise exc_factory()
        return original_geom(*args)

    setattr(comp, "geom", geom_with_first_probe_unknown)
    return hits


def test_pair_geometry_precheck_unknown_refuses_create_direct():
    worker, _, comp, _, _ = pair_world()
    hits = _install_flaky_component_geom(
        comp, lambda: ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic precheck timeout"))
    try:
        create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                             "definition": {"type": "Contact", "geometry": "geom1",
                                            "destination": {"kind": "named", "component": "comp1",
                                                            "tag": "sel2"}}})
        raise AssertionError("pre-write UNKNOWN must not be demoted")
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
        assert exc.details.get("probe_state_unknown") is True
        assert exc.details.get("execution_state_unknown") is True
    # exactly one probe hit; nothing ran behind the UNKNOWN call
    assert hits == [True]
    assert "pair1" not in comp.collections["pair"].items
    assert comp.collections["pair"].mutation_calls() == []
    # the named-selection presence probes were never reached either
    assert comp.collections["selection"].calls == []


def test_pair_geometry_precheck_flagged_unknown_also_refuses_create():
    worker, _, comp, _, _ = pair_world()
    hits = _install_flaky_component_geom(
        comp, lambda: _flagged_worker_failure("precheck may still be running"))
    try:
        create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                             "definition": {"type": "Contact", "geometry": "geom1",
                                            "destination": {"kind": "named", "component": "comp1",
                                                            "tag": "sel2"}}})
        raise AssertionError("flagged pre-write UNKNOWN must not be demoted")
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
    assert hits == [True]
    assert "pair1" not in comp.collections["pair"].items
    assert comp.collections["pair"].mutation_calls() == []


# ---------------------------------------------------------------------------
# the same contract through the public dispatch chain
# ---------------------------------------------------------------------------


def test_public_dispatch_reports_binding_probe_unknown_without_destination_mutation():
    worker, _, comp, _, _ = pair_world()
    fault = _unknown_geom_getter()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=fault)
    result = public_dispatch("definition.pair_manage", worker, "Model", {
        "action": "create", "component": "comp1", "tag": "pair1",
        "definition": _explicit_source_without_geometry()})
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True and result["ok"] is False
    assert fault.getter_failures == 1
    pair = comp.collections["pair"].items["pair1"]
    assert pair.side_selections["destination"].named_ref is None
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_public_dispatch_precheck_unknown_raises_before_any_create():
    worker, _, comp, _, _ = pair_world()
    hits = _install_flaky_component_geom(
        comp, lambda: ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic precheck timeout"))
    try:
        public_dispatch("definition.pair_manage", worker, "Model", {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {"type": "Contact", "geometry": "geom1",
                           "destination": {"kind": "named", "component": "comp1", "tag": "sel2"}}})
        raise AssertionError("pre-write UNKNOWN must not be demoted through dispatch")
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
    assert hits == [True]
    assert "pair1" not in comp.collections["pair"].items
    assert comp.collections["pair"].mutation_calls() == []


# ---------------------------------------------------------------------------
# positive controls: ordinary optional getters stay optional
# ---------------------------------------------------------------------------


def test_optional_rejected_geom_probe_still_verifies_and_applies_positive():
    class RejectedGeom(FLocalSel):
        def geom(self, *args: Any) -> Any:
            if not args:
                self.calls.append(("geom", args))
                raise _rejected("geom")
            return super().geom(*args)

    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=RejectedGeom(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": _explicit_source_without_geometry()})
    assert result["ok"] is True and result["status"] == "APPLIED"
    applied = result["selection"]["source"]["applied"][0]
    assert applied["geometry_verification"] == "not_verifiable_no_geometry_context"
    assert applied["dimension_verified_by"] == "inputDimension"
    state = result["selection"]["source"]["engine_state"]
    assert state["geometry_error"]["code"] == "METHOD_REJECTED"
    assert state["geometry_error"]["execution_state_unknown"] is False
    assert comp.collections["pair"].items["pair1"].side_selections["destination"].named_ref == "sel2"


def test_optional_rejected_geometry_list_precheck_still_creates_positive():
    worker, _, comp, _, _ = pair_world()
    comp.unavailable.add("geom")
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "named", "component": "comp1",
                                                            "tag": "sel1"}}})
    assert result["ok"] is True and result["status"] == "APPLIED"
    assert result["pre_checks"]["pair_geometry_existence"] == "not_verifiable_engine_tags_unreadable"
    assert "pair1" in comp.collections["pair"].items


def test_pair_inspect_reports_unknown_getter_as_data_without_raising():
    worker, _, comp, _, _ = pair_world()
    fault = _unknown_geom_getter()
    node = FPairNode(tag="pair9", type_id="Identity", source_selection=fault)
    comp.collections["pair"].items["pair9"] = node
    inspected = inspect_pair(worker, pair_path("pair9"))
    assert inspected["source"]["available"] is True
    # read-only reporting keeps data shape but carries the honest marker
    assert inspected["source"]["geometry"] is None
    assert inspected["source"]["geometry_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert inspected["source"]["geometry_error"]["execution_state_unknown"] is True
    assert fault.getter_failures >= 1
