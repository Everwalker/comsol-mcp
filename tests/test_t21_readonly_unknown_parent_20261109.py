"""T21 read-only Pair reports must not hide or dispatch behind Worker UNKNOWN.

The adapter is real, the endpoints are disposable fakes. JavaWorkerTimeout is
constructed locally; no sockets, Worker, or COMSOL process are started. This
fault model does not prove that an actual COMSOL getter times out.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerTimeout
from tests.test_t21_pair_selection_20261108 import (
    FLocalSel,
    FPairNode,
    create_pair,
    inspect_pair,
    pair_path,
    pair_world,
)


def _timeout() -> JavaWorkerTimeout:
    return JavaWorkerTimeout(
        "synthetic RPC timeout; worker request may still be executing",
        reply={"status": "UNKNOWN", "execution_state_unknown": True,
               "request_id": "synthetic-pair-read-timeout"},
    )


def _is_unknown(outcome: object) -> bool:
    if isinstance(outcome, ExecutionContractError):
        return outcome.code == "EXECUTION_STATE_UNKNOWN"
    if isinstance(outcome, JavaWorkerError):
        return outcome.execution_state_unknown
    if isinstance(outcome, dict):
        return outcome.get("execution_state_unknown") is True or outcome.get("status") == "EXECUTION_STATE_UNKNOWN"
    return False


@pytest.mark.parametrize("action", ["inspect", "list"])
@pytest.mark.parametrize("fault", ["getType", "label", "named"])
def test_pair_read_worker_unknown_stops_later_dispatch_and_surfaces(action, fault, monkeypatch):
    worker, _, comp, _, _ = pair_world()
    trace: list[str] = []

    class Selection(FLocalSel):
        def named(self, *args):
            trace.append("named")
            if fault == "named" and not args:
                trace.append("UNKNOWN")
                raise _timeout()
            return super().named(*args)

    class Pair(FPairNode):
        def getType(self):
            trace.append("getType")
            if fault == "getType":
                trace.append("UNKNOWN")
                raise _timeout()
            return super().getType()

        def label(self, *args):
            trace.append("label")
            if fault == "label" and not args:
                trace.append("UNKNOWN")
                raise _timeout()
            return super().label(*args)

        def source(self):
            trace.append("source")
            return super().source()

        def destination(self):
            trace.append("destination")
            return super().destination()

    pairs = comp.collections["pair"]
    pairs.items["pairA"] = Pair(tag="pairA", type_id="Contact", source_selection=Selection(None))
    pairs.items["pairB"] = Pair(tag="pairB", type_id="Contact")
    original_get = pairs.get

    def traced_get(tag):
        if tag == "pairB":
            trace.append("get_pairB")
        return original_get(tag)

    monkeypatch.setattr(pairs, "get", traced_get)
    try:
        outcome = (inspect_pair(worker, pair_path("pairA")) if action == "inspect" else
                   create_pair(worker, {"action": "list", "component": "comp1"}))
    except (ExecutionContractError, JavaWorkerError) as exc:
        outcome = exc

    assert "UNKNOWN" in trace, (action, fault, trace)
    assert trace[trace.index("UNKNOWN") + 1:] == [], (action, fault, trace)
    assert _is_unknown(outcome), (action, fault, outcome)


@pytest.mark.parametrize("action", ["inspect", "list"])
def test_pair_read_optional_method_rejected_remains_lenient(action):
    worker, _, comp, _, _ = pair_world()

    class RejectedLabel(FPairNode):
        def label(self, *args):
            raise ExecutionContractError("METHOD_REJECTED", "synthetic optional getter rejection")

    comp.collections["pair"].items["pairA"] = RejectedLabel(tag="pairA", type_id="Contact")
    result = (inspect_pair(worker, pair_path("pairA")) if action == "inspect" else
              create_pair(worker, {"action": "list", "component": "comp1"}))
    row = result if action == "inspect" else result["pairs"][0]
    assert row["label"] is None
    assert row["source"]["available"] is True
    assert row["destination"]["available"] is True
