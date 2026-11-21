"""T21 read-only Pair fix: bounded controls beyond the 6 parent counterexamples.

Complements ``tests/test_t21_readonly_unknown_parent_20261109.py`` (which
injects getType/label/named) with the read-route gaps this fix must also close:
a side getter (``source()``/``destination()``) itself UNKNOWN, ``isActive()``
UNKNOWN, the next-pair fetch ban in ``list``, the pure happy-path control, and
the *real* domain classification (``_domain_outcome.domain_envelope`` and
``_managed_backend.ManagedBackend._dispatch_with_witness``) of the new
top-level UNKNOWN flags.  Fakes only: no COMSOL, Worker, Desktop or network is
started, so this fault model does not prove a real getter times out.  The full
public route (managed-backend ticket + real worker) is NOT exercised here.
"""
from __future__ import annotations

import pytest

from comsol_mcp._java_worker import JavaWorkerTimeout
from comsol_mcp import _g3_w14 as w14

try:  # pragma: no cover - import shim
    from tests.test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )
except ImportError:  # pragma: no cover
    from test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, create_pair, inspect_pair, pair_path, pair_world,
    )


def _timeout() -> JavaWorkerTimeout:
    return JavaWorkerTimeout(
        "synthetic RPC timeout; worker request may still be executing",
        reply={"status": "UNKNOWN", "execution_state_unknown": True,
               "request_id": "synthetic-pair-read-timeout-fix"},
    )


def _unknown_pair(tag: str, fault_side: str, trace: list[str]) -> FPairNode:
    class Pair(FPairNode):
        def source(self):
            trace.append("source")
            if fault_side == "source":
                trace.append("UNKNOWN")
                raise _timeout()
            return super().source()

        def destination(self):
            trace.append("destination")
            if fault_side == "destination":
                trace.append("UNKNOWN")
                raise _timeout()
            return super().destination()

    return Pair(tag=tag, type_id="Contact")


def test_side_getter_itself_unknown_stops_later_reads_inspect():
    """``source()`` UNKNOWN: destination is never fetched; observed data stays."""
    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []
    comp.collections["pair"].items["pairA"] = _unknown_pair("pairA", "source", trace)
    outcome = inspect_pair(worker, pair_path("pairA"))
    assert trace == ["source", "UNKNOWN"]
    assert outcome["execution_state_unknown"] is True
    assert outcome["status"] == "EXECUTION_STATE_UNKNOWN"
    # a read-only route issues no model write
    assert outcome["partial_change"] is False
    # observations made before the UNKNOWN stay reported
    assert outcome["type_id"] == "Contact"
    assert outcome["label"] == "pairA label"
    row = outcome["source"]
    assert row["available"] is False
    assert row["probe_error"]["execution_state_unknown"] is True
    assert outcome["destination"]["probe_skipped"] is True
    assert any(rec["field"] == "destination" for rec in outcome["not_executed"])
    assert outcome["failed"][0]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert outcome["failed"][0]["mutation_issued"] is False


def test_is_active_unknown_stops_selection_reads_inspect():
    """``isActive()`` UNKNOWN: neither selection getter is dispatched after it."""
    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []

    class Pair(FPairNode):
        def isActive(self):  # noqa: N802 - Java spelling
            trace.append("isActive")
            trace.append("UNKNOWN")
            raise _timeout()

        def source(self):
            trace.append("source")
            return super().source()

        def destination(self):
            trace.append("destination")
            return super().destination()

    comp.collections["pair"].items["pairA"] = Pair(tag="pairA", type_id="Contact")
    outcome = inspect_pair(worker, pair_path("pairA"))
    assert trace == ["isActive", "UNKNOWN"]
    assert outcome["execution_state_unknown"] is True and outcome["status"] == "EXECUTION_STATE_UNKNOWN"
    assert outcome["type_id"] == "Contact" and outcome["label"] == "pairA label"
    assert outcome["source"]["probe_skipped"] is True
    assert outcome["destination"]["probe_skipped"] is True
    # ``active`` itself was the UNKNOWN probe (dispatched, so not skipped);
    # only the fields behind it are recorded as unobserved.
    assert [rec["field"] for rec in outcome["not_executed"]] == ["source", "destination"]


def test_list_unknown_side_stops_before_next_pair(monkeypatch):
    """pairA's ``destination()`` UNKNOWN: pairB is never fetched or read."""
    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []
    pairs = comp.collections["pair"]
    pairs.items["pairA"] = _unknown_pair("pairA", "destination", trace)
    pairs.items["pairB"] = FPairNode(tag="pairB", type_id="Contact")
    original_get = pairs.get

    def traced_get(tag):
        if tag == "pairB":
            trace.append("get_pairB")
        return original_get(tag)

    monkeypatch.setattr(pairs, "get", traced_get)
    outcome = create_pair(worker, {"action": "list", "component": "comp1"})
    assert trace == ["source", "destination", "UNKNOWN"]
    assert "get_pairB" not in trace
    assert outcome["execution_state_unknown"] is True
    assert outcome["status"] == "EXECUTION_STATE_UNKNOWN"
    assert outcome["partial_change"] is False
    assert outcome["traversal_completed"] is False
    assert outcome["not_read"] == ["pairB"]
    # the counting rule counts only what was read; no false full traversal
    assert outcome["pair_count"] == 1
    assert [row["tag"] for row in outcome["pairs"]] == ["pairA"]
    assert outcome["readback"]["tags"] == ["pairA"]
    row_a = outcome["pairs"][0]
    # pairA's completed source observation is preserved; only the UNKNOWN's
    # tail is unobserved
    assert row_a["source"]["available"] is True
    assert row_a["destination"]["available"] is False
    assert row_a["destination"]["probe_error"]["execution_state_unknown"] is True


def test_clean_read_routes_keep_lenient_shape():
    """Pure happy-path control: no UNKNOWN flags, original shape and counts."""
    worker, _, comp, _, _ = pair_world()
    comp.collections["pair"].items["pairA"] = FPairNode(tag="pairA", type_id="Contact")
    comp.collections["pair"].items["pairB"] = FPairNode(tag="pairB", type_id="Identity")
    listed = create_pair(worker, {"action": "list", "component": "comp1"})
    assert listed["pair_count"] == 2
    assert [row["tag"] for row in listed["pairs"]] == ["pairA", "pairB"]
    assert listed["readback"]["tags"] == ["pairA", "pairB"]
    assert "status" not in listed and "not_executed" not in listed
    assert "traversal_completed" not in listed
    inspected = inspect_pair(worker, pair_path("pairA"))
    assert "status" not in inspected and "not_executed" not in inspected
    assert inspected["source"]["available"] is True
    assert inspected["destination"]["available"] is True


def test_rejected_nested_getter_keeps_lenient_continuation():
    """A deterministic METHOD_REJECTED nested getter is not an UNKNOWN stop."""
    worker, _, comp, _, _ = pair_world()
    try:
        from tests.test_g3_w14 import _rejected  # type: ignore[no-redef]
    except ImportError:
        from test_g3_w14 import _rejected  # type: ignore[no-redef]

    class RejectedNamed(FLocalSel):
        def named(self, *args):
            if not args:
                raise _rejected("named")
            return super().named(*args)

    comp.collections["pair"].items["pairA"] = FPairNode(
        tag="pairA", type_id="Contact", source_selection=RejectedNamed(None))
    inspected = inspect_pair(worker, pair_path("pairA"))
    assert inspected["source"]["available"] is True
    assert inspected["source"]["named_error"]["code"] == "METHOD_REJECTED"
    assert inspected["destination"]["available"] is True
    assert "status" not in inspected


def test_domain_envelope_classifies_stopped_read_as_unknown_not_success():
    """The real ``_domain_outcome`` classifier must see the new top-level flags."""
    from comsol_mcp._domain_outcome import STATE_SUCCEEDED, STATE_UNKNOWN, domain_envelope

    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []
    comp.collections["pair"].items["pairA"] = _unknown_pair("pairA", "source", trace)
    inspected = inspect_pair(worker, pair_path("pairA"))
    envelope = domain_envelope("definition.pair_manage", inspected)
    assert envelope["domain_outcome"]["state"] == STATE_UNKNOWN
    assert envelope["execution_state_unknown"] is True
    assert envelope["error"] is not None and envelope["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    # a read-only stop is never mutated-flagged into partial_change
    assert envelope["partial_change"] is False

    clean_worker, _, clean_comp, _, _ = pair_world()
    clean_comp.collections["pair"].items["pairC"] = FPairNode(tag="pairC", type_id="Identity")
    clean = inspect_pair(clean_worker, pair_path("pairC"))
    clean_envelope = domain_envelope("definition.pair_manage", clean)
    assert clean_envelope["domain_outcome"]["state"] == STATE_SUCCEEDED
    assert clean_envelope["execution_state_unknown"] is False


def test_managed_dispatch_with_witness_surfaces_stopped_read_unknown():
    """``ManagedBackend._dispatch_with_witness`` (real static path) on the data.

    This is a classification simulation only: the callback hands over the data
    mapping the fake operation already produced; no ticket, worker or engine is
    started, so the full public route stays untested here.
    """
    from comsol_mcp._managed_backend import ManagedBackend

    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []
    comp.collections["pair"].items["pairA"] = _unknown_pair("pairA", "source", trace)
    data = inspect_pair(worker, pair_path("pairA"))
    envelope = ManagedBackend._dispatch_with_witness(
        "definition.pair_manage", lambda: data, effect="read")
    assert envelope["success"] is False
    assert envelope["domain_state"] == "unknown"
    assert envelope["execution_state_unknown"] is True
    assert envelope["error"]["code"] == "EXECUTION_STATE_UNKNOWN"

    clean_worker, _, clean_comp, _, _ = pair_world()
    clean_comp.collections["pair"].items["pairC"] = FPairNode(tag="pairC", type_id="Identity")
    clean_data = create_pair(clean_worker, {"action": "list", "component": "comp1"})
    clean_envelope = ManagedBackend._dispatch_with_witness(
        "definition.pair_manage", lambda: clean_data, effect="read")
    assert clean_envelope["success"] is True
    assert clean_envelope["domain_state"] == "succeeded"
