"""Single selection_inspect: selection-container tags() UNKNOWN stops before the named-selection lookup.

Scope (T14, prepared 2026-11-10): exactly one named ``selection_inspect`` call
against the product function with successful fake client/model acquisition,
injecting a synthetic structured EXECUTION_STATE_UNKNOWN into the fake
selection-container ``tags()`` getter after the model/component/container
acquisition but before the named selection is looked up.  Public list actions,
coordinate management, any cursor/fallback behaviour, first-model failures and
T20 validation/sibling outcomes are outside this test and remain prohibited.
This is in-memory evidence only; source identities belong to the external
evidence runner, not a hardcoded checkout path in this test.  This file only
encodes the required stop behaviour; no execution result is claimed here.
"""
from __future__ import annotations

import pytest

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w13 import FakeEngineError, FClient, FList, FNode, FWorker, component_with


class RecordingClient(FClient):
    def __init__(self, model, events):
        super().__init__({"Model": model})
        self.events = events

    def model(self, tag):
        model = super().model(tag)
        self.events.append(("model", tag))
        return model


class RecordingWorker(FWorker):
    def __init__(self, model, events):
        super().__init__(model)
        self.events = events
        self._client = RecordingClient(model, events)

    def client(self):
        self.events.append(("client",))
        return self._client


class RecordingSelectionList(FList):
    """Replacement fake selection container; only ``tags()`` differs from FList."""

    def __init__(self, items, events, fault=None):
        super().__init__(arity=2, node_type="SelectionFeature")
        self.items = dict(items)
        self.events = events
        self.fault = fault
        self.fired = False

    def tags(self):
        self.calls.append(("tags", ()))
        self.events.append(("container-tags",))
        if self.fault is not None:
            self.fired = True
            raise self.fault
        return list(self.items)


class WorkerShapedUnknown(RuntimeError):
    def __init__(self):
        super().__init__("synthetic selection-container tags failure")
        self.failure = {"code": "ENGINE_CALL_FAILED", "execution_state_unknown": True}


def _tree(fault=None):
    events = []
    node = FNode(tag="sel1", type_id="Explicit", entities_=[1, 2], dim_=2, geometry_tag="geom1")
    model, component = component_with()
    recording = RecordingSelectionList({"sel1": node}, events, fault)
    # The only swap in this freshly constructed in-memory tree is the fake
    # selection container itself.
    component.collections["selection"] = recording
    worker = RecordingWorker(model, events)
    return events, node, model, component, recording, worker


def _inspect(worker):
    try:
        result = w13.selection_inspect(worker, "Model", {"component": "comp1", "tag": "sel1"})
    except ExecutionContractError as exc:
        return None, exc
    return result, None


def _assert_acquired(events, model, component, recording):
    # Observations recorded during the real function call; no second client or
    # model traversal is performed after the invocation.
    tags_index = events.index(("container-tags",))
    assert events.index(("client",)) < events.index(("model", "Model")) < tags_index, events
    assert ("component", (None,)) in model.calls
    assert ("component", ("comp1",)) in model.calls
    assert ("selection", ()) in component.calls
    assert ("tags", ()) in recording.calls
    return tags_index


def _unknown(kind):
    if kind == "direct":
        return ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "synthetic selection-container tags unknown")
    if kind == "details":
        return ExecutionContractError(
            "ENGINE_CALL_FAILED", "synthetic selection-container tags failure",
            stage="validation", safe_retry=True, details={"execution_state_unknown": True},
        )
    if kind == "failure":
        return WorkerShapedUnknown()
    raise AssertionError("unexpected synthetic fault kind")


@pytest.mark.parametrize("kind", ["direct", "details", "failure"])
def test_container_tags_unknown_stops_before_named_selection_lookup(kind):
    events, node, model, component, recording, worker = _tree(_unknown(kind))
    result, error = _inspect(worker)
    # Fake acquisition happened first: client -> model -> component ->
    # container -> container tags() fault.
    _assert_acquired(events, model, component, recording)
    assert recording.fired, events
    # After the injected tags failure nothing dispatches a named-selection
    # lookup or any dependent getter on the named selection node.
    assert ("selection", ("sel1",)) not in component.calls, (events, component.calls, result, error)
    assert ("get", ("sel1",)) not in recording.calls, (events, recording.calls, result, error)
    assert node.calls == [], (events, node.calls, result, error)
    assert result is None, (events, result)
    assert error is not None, (events, result)
    assert error.code == "EXECUTION_STATE_UNKNOWN"
    assert error.safe_retry is False
    # The container tags() probe was already dispatched, so the declared stop
    # stage is post-dispatch (see _execution_contract stage fail-closed rule).
    assert error.stage == "post_dispatch"


def test_healthy_container_tags_still_resolves_named_selection():
    events, node, model, component, recording, worker = _tree()
    result, error = _inspect(worker)
    _assert_acquired(events, model, component, recording)
    assert not recording.fired
    assert error is None
    assert result is not None
    assert ("selection", ("sel1",)) in component.calls
    assert ("get", ("sel1",)) in recording.calls
    assert ("getType", ()) in node.calls
    assert result["component"] == "comp1"
    assert result["tag"] == "sel1"
    assert result["type_id"] == "Explicit"
    assert result["label"] == "sel1 label"
    assert result["label_error"] is None
    assert result["entities"] == [1, 2]
    assert result["entity_count"] == 2
    assert result["entity_dimension"] == 2


def test_ordinary_method_rejected_container_tags_is_optional_miss():
    fault = FakeEngineError("synthetic selection-container tags rejection", code="METHOD_REJECTED")
    events, node, model, component, recording, worker = _tree(fault)
    result, error = _inspect(worker)
    _assert_acquired(events, model, component, recording)
    assert recording.fired, events
    assert error is None
    assert result is not None
    assert ("selection", ("sel1",)) in component.calls
    assert ("get", ("sel1",)) in recording.calls
    assert ("getType", ()) in node.calls
    assert result["tag"] == "sel1"
    assert result["entities"] == [1, 2]
