"""T21 unknown-tail fix controls (qwen, 2026-11-08).

Companion controls for the parent adversarial file
``tests/test_t21_unknown_tail_dispatch_parent_20261108.py`` after the
first-structured-UNKNOWN stop fix in ``_g3_w14.definition_pair_manage`` and
the controlled ``selection_state(strict_unknown=...)`` mode in ``_g3_common``.

These fake-endpoint tests pin the repaired contract on the real Pair adapter
chain, and guard the boundaries the fix must NOT widen:

* after the first structured UNKNOWN on the Pair creation chain, zero further
  engine reads or writes are dispatched - not the failed side's getter, not
  the other side's getter, not the post-loop report probes; the remaining
  side is recorded as data (``state_unknown``/``probe_skipped``) and stays in
  ``not_executed``;
* a Worker transport UNKNOWN under a non-UNKNOWN code (``__cause__`` carries
  the structured flag) keeps the wrapper code, marks the envelope unknown,
  and never triggers a post-attempt re-probe;
* a structured UNKNOWN surfacing only in the closing ``entity_state`` /
  ``engine_state`` readbacks is no longer swallowed into ``*_error`` fields:
  the side fails with ``mutation_issued=true``, the applied mutation stays
  (no blind rollback), and the destination write never dispatches;
* honest distinctions survive: a *logical* readback mismatch (probe
  completed, value contradicts) keeps reporting the engine state via a
  post-attempt probe and is labelled differently from a transport UNKNOWN;
  ordinary optional probe misses (METHOD_REJECTED, method not exposed) stay
  optional and still reach ``APPLIED``; read-only ``inspect``/default
  ``selection_state`` keep the lenient data shape (no raising, no false
  isolation claim);
* a clean create still probes both sides and reports ``APPLIED``.
"""
from __future__ import annotations

from typing import Any

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_common import selection_state
from comsol_mcp._g3_ops import dispatch as public_dispatch
from comsol_mcp import _g3_w14 as w14

try:  # pragma: no cover - import shim
    from tests.test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )
    from tests.test_t21_probe_unknown_fix_qwen_20261108 import (  # type: ignore[no-redef]
        _explicit_source_without_geometry, _flagged_worker_failure, _unknown_geom_getter,
    )
except ImportError:  # pragma: no cover
    from test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )
    from test_t21_probe_unknown_fix_qwen_20261108 import (  # type: ignore[no-redef]
        _explicit_source_without_geometry, _flagged_worker_failure, _unknown_geom_getter,
    )


def _install_pair(comp: Any, source: Any) -> None:
    comp.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=source
    )


def _pair_args(definition: Any) -> dict[str, Any]:
    return {"action": "create", "component": "comp1", "tag": "pair1",
            "definition": definition}


def _side_calls(pair: Any) -> list[str]:
    return [call[0] for call in pair.calls if call[0] in {"source", "destination"}]


_NAMED_BOTH = {
    "type": "Contact",
    "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
    "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
}


class _LateUnknownNamed(FLocalSel):
    """Zero-argument named() raises a structured UNKNOWN on the Nth read."""

    def __init__(self, fail_on_get: int) -> None:
        super().__init__(None)
        self.fail_on_get = fail_on_get
        self.getter_count = 0

    def named(self, *args: Any) -> Any:
        if not args:
            self.getter_count += 1
            if self.getter_count == self.fail_on_get:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN",
                                             "synthetic source state timeout")
        return super().named(*args)


# ---------------------------------------------------------------------------
# 1) post-loop tail: no engine read may run behind the first UNKNOWN
# ---------------------------------------------------------------------------


def test_unknown_source_readback_tail_reports_side_without_any_probe():
    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, _unknown_geom_getter())
    result = create_pair(worker, _pair_args(_explicit_source_without_geometry()))
    pair = comp.collections["pair"].items["pair1"]

    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert result["created"] is True  # no blind rollback
    # Pair.source() once; Pair.destination() never - neither in the loop, the
    # post-failure handling, nor the closing per-side report probes.
    assert _side_calls(pair) == ["source"]
    assert any(row.get("side") == "destination" for row in result["not_executed"])
    tail = result["selection"]["destination"]
    assert tail["engine_state"] is None and tail["state_unknown"] is True
    assert tail["probe_skipped"] is True
    assert tail["kind"] == "named"
    assert pair.side_selections["destination"].calls == []
    assert comp.collections["selection"].items["sel2"].calls == []
    assert any("no automatic rollback" in note for note in result["notes"])


def test_flagged_mutation_unknown_never_reprobes_failed_or_untouched_side():
    class UnknownSet(FLocalSel):
        def set(self, *args: Any) -> None:
            self.calls.append(("set", args))
            raise _flagged_worker_failure("synthetic dispatched setter may still be running")

    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, UnknownSet(None))
    result = create_pair(worker, _pair_args(_explicit_source_without_geometry()))
    pair = comp.collections["pair"].items["pair1"]

    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["partial_change"] is True
    # no re-probe of the UNKNOWN endpoint, no destination getter at all
    assert _side_calls(pair) == ["source"]
    failed = result["failed"][0]
    # the _call wrapper keeps the engine-call code; the structured flag rides
    # through __cause__ into the unknown markers
    assert failed["code"] == "ENGINE_CALL_FAILED"
    assert failed["execution_state_unknown"] is True
    assert failed["engine_state_after_attempt"]["state_unknown"] is True
    assert failed["engine_state_after_attempt"]["probe"] == "mutation"
    assert result["selection"]["source"]["state_unknown"] is True
    assert result["selection"]["destination"]["probe_skipped"] is True
    assert pair.side_selections["destination"].named_ref is None


# ---------------------------------------------------------------------------
# 2) late UNKNOWN only inside the closing state readbacks (was swallowed)
# ---------------------------------------------------------------------------


def test_late_unknown_in_closing_entity_state_readback_blocks_destination_write():
    worker, _, comp, _, _ = pair_world()
    source = _LateUnknownNamed(fail_on_get=2)  # 2nd read = apply_local_selection entity_state
    _install_pair(comp, source)
    result = create_pair(worker, _pair_args(_NAMED_BOTH))
    pair = comp.collections["pair"].items["pair1"]

    assert source.getter_count == 2  # raised on the closing read, nothing behind it
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    failed = result["failed"][0]
    assert failed["code"] == "EXECUTION_STATE_UNKNOWN"
    assert failed["mutation_issued"] is True
    assert failed["engine_state_after_attempt"]["state_unknown"] is True
    assert failed["engine_state_after_attempt"]["probe"] == "named"
    # the write that had already landed stays (no blind rollback), and the
    # destination side was never touched
    assert pair.side_selections["source"].named_ref == "sel1"
    assert pair.side_selections["destination"].named_ref is None
    assert _side_calls(pair) == ["source"]
    assert any(row.get("side") == "destination" for row in result["not_executed"])


def test_late_unknown_in_outer_engine_state_probe_blocks_destination_write():
    worker, _, comp, _, _ = pair_world()
    # reads: 1 = named() readback, 2 = entity_state closing probe,
    # 3 = the outer per-side engine_state probe (previously lenient)
    source = _LateUnknownNamed(fail_on_get=3)
    _install_pair(comp, source)
    result = create_pair(worker, _pair_args(_NAMED_BOTH))
    pair = comp.collections["pair"].items["pair1"]

    assert source.getter_count == 3
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    failed = result["failed"][0]
    assert failed["engine_state_after_attempt"]["probe"] == "named"
    assert failed["mutation_issued"] is True
    assert pair.side_selections["source"].named_ref == "sel1"  # kept, not rolled back
    assert pair.side_selections["destination"].named_ref is None
    assert _side_calls(pair) == ["source"]


# ---------------------------------------------------------------------------
# 3) boundaries the fix must not widen
# ---------------------------------------------------------------------------


def test_logical_readback_mismatch_stays_distinct_and_reports_engine_state():
    class LyingNamed(FLocalSel):
        def named(self, *args: Any) -> Any:
            self.calls.append(("named", args))
            if args:
                self.named_ref = str(args[0])
                return None
            return "selLiar" if self.named_ref else None

    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, LyingNamed(None))
    result = create_pair(worker, _pair_args({
        "type": "Contact",
        "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
    }))
    pair = comp.collections["pair"].items["pair1"]

    # logical mismatch (read completed, value contradicts) is still reported
    # as the engine's own state via a post-attempt probe - NOT relabelled as a
    # transport state_unknown record.
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["execution_state_unknown"] is True
    failed = result["failed"][0]
    assert failed["engine_state_after_attempt"]["named"] == "selLiar"
    assert "state_unknown" not in failed["engine_state_after_attempt"]
    # no destination plan: the loop's post-attempt probe plus the unchanged
    # tail report probes may still read the endpoint (logical mismatch, not a
    # transport UNKNOWN)
    assert result["selection"]["destination"]["available"] is True
    assert _side_calls(pair) == ["source", "source", "source", "destination"]


def test_optional_rejected_probes_still_apply_positive():
    class RejectedExtras(FLocalSel):
        def geom(self, *args: Any) -> Any:
            if not args:
                self.calls.append(("geom", args))
                raise _rejected_getter("geom")
            return super().geom(*args)

    worker, _, comp, _, _ = pair_world()
    _install_pair(comp, RejectedExtras(None))
    result = create_pair(worker, _pair_args(_explicit_source_without_geometry()))
    assert result["ok"] is True and result["status"] == "APPLIED"
    assert result["selection"]["source"]["engine_state"]["geometry_error"]["code"] == "METHOD_REJECTED"
    assert result["selection"]["source"]["engine_state"]["geometry_error"]["execution_state_unknown"] is False
    assert comp.collections["pair"].items["pair1"].side_selections["destination"].named_ref == "sel2"


def _rejected_getter(method: str) -> Any:
    from test_g3_w14 import FakeEngineError  # local: positive control only
    return FakeEngineError(f"SecurityException: METHOD_REJECTED ({method})", code="METHOD_REJECTED")


def test_default_selection_state_stays_lenient_strict_mode_is_opt_in():
    lenient = selection_state(_LateUnknownNamed(fail_on_get=1))  # read-only: no raise
    assert lenient["named"] is None
    assert lenient["named_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert lenient["named_error"]["execution_state_unknown"] is True

    try:
        selection_state(_LateUnknownNamed(fail_on_get=1), strict_unknown=True)
        raise AssertionError("strict mode must surface the structured UNKNOWN")
    except ExecutionContractError as exc:
        assert exc.code == "EXECUTION_STATE_UNKNOWN"
        assert exc.details.get("probe_state_unknown") is True
        assert exc.details.get("probe") == "named"


def test_readonly_inspect_reports_unknown_getter_as_data_without_raising():
    worker, _, comp, _, _ = pair_world()
    node = FPairNode(tag="pair9", type_id="Identity", source_selection=_unknown_geom_getter())
    comp.collections["pair"].items["pair9"] = node
    inspected = inspect_pair(worker, pair_path("pair9"))
    assert inspected["source"]["available"] is True
    assert inspected["source"]["geometry_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert inspected["source"]["geometry_error"]["execution_state_unknown"] is True


def test_clean_create_still_probes_both_sides_positive():
    worker, _, comp, _, _ = pair_world()
    result = public_dispatch("definition.pair_manage", worker, "Model",
                             _pair_args(_NAMED_BOTH))
    pair = comp.collections["pair"].items["pair1"]
    assert result["status"] == "APPLIED" and result["ok"] is True
    assert _side_calls(pair) == ["source", "destination"]
    assert result["selection"]["source"]["engine_state"]["named"] == "sel1"
    assert result["selection"]["destination"]["engine_state"]["named"] == "sel2"
