"""Single-selection inspect: label UNKNOWN stops dependent fake probes.

The product function and its fake model-acquisition path are not replaced. This
is in-memory evidence only; the separately fenced list/cursor/first-model and
T20 sibling outcomes are outside this test. Source identities belong to the
external evidence runner, not a hardcoded checkout path in this test.
"""
from __future__ import annotations

import pytest

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w13 import FakeEngineError, FClient, FNode, FWorker, component_with


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


class RecordingSelection(FNode):
    def __init__(self, events, fault=None):
        super().__init__(tag="sel1", type_id="Explicit", entities_=[1, 2], dim_=2, geometry_tag="geom1")
        self.events = events
        self.fault = fault
        self.fired = False

    def getType(self):
        self.events.append(("getType",))
        return super().getType()

    def label(self, *args):
        if args:
            return super().label(*args)
        self.events.append(("label",))
        if self.fault is not None:
            self.fired = True
            raise self.fault
        return super().label()

    def entities(self, *args):
        self.events.append(("entities",))
        return super().entities(*args)


class WorkerShapedUnknown(RuntimeError):
    def __init__(self):
        super().__init__("synthetic label failure")
        self.failure = {"code": "ENGINE_CALL_FAILED", "execution_state_unknown": True}


def _tree(fault=None):
    events = []
    node = RecordingSelection(events, fault)
    model, component = component_with(selections={"sel1": node})
    worker = RecordingWorker(model, events)
    return events, node, model, component, worker


def _inspect(worker):
    try:
        result = w13.selection_inspect(worker, "Model", {"component": "comp1", "tag": "sel1"})
    except ExecutionContractError as exc:
        return None, exc
    return result, None


def _assert_resolved(events, node, model, component):
    # These are observations made during the real function call; this helper
    # deliberately does not call the fake client/model again after a fault.
    label_index = events.index(("label",))
    assert events.index(("client",)) < events.index(("model", "Model")) < label_index, events
    assert events.index(("getType",)) < label_index, events
    assert ("component", (None,)) in model.calls
    assert ("component", ("comp1",)) in model.calls
    assert ("selection", ()) in component.calls
    assert ("selection", ("sel1",)) in component.calls
    assert ("get", ("sel1",)) in component.collections["selection"].calls
    assert node is component.collections["selection"].items["sel1"]
    return label_index


def _unknown(kind):
    if kind == "direct":
        return ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic label unknown")
    if kind == "details":
        return ExecutionContractError(
            "ENGINE_CALL_FAILED", "synthetic label failure",
            stage="validation", safe_retry=True, details={"execution_state_unknown": True},
        )
    if kind == "failure":
        return WorkerShapedUnknown()
    raise AssertionError("unexpected synthetic fault kind")


@pytest.mark.parametrize("kind", ["direct", "details", "failure"])
def test_label_unknown_stops_before_entities(kind):
    events, node, model, component, worker = _tree(_unknown(kind))
    result, error = _inspect(worker)
    label_index = _assert_resolved(events, node, model, component)
    assert node.fired, events
    assert ("entities",) not in events[label_index + 1:], (events, result, error)
    assert error is not None, (events, result)
    assert error.code == "EXECUTION_STATE_UNKNOWN"
    assert error.safe_retry is False


def test_healthy_label_and_entities_remain_data_shaped():
    events, node, model, component, worker = _tree()
    result, error = _inspect(worker)
    label_index = _assert_resolved(events, node, model, component)
    assert error is None
    assert result is not None
    assert ("entities",) in events[label_index + 1:]
    assert result["label"] == "sel1 label"
    assert result["label_error"] is None
    assert result["entities"] == [1, 2]
    assert result["entity_dimension"] == 2


def test_ordinary_optional_label_failure_still_allows_entities():
    fault = FakeEngineError("synthetic optional-method rejection", code="METHOD_REJECTED")
    events, node, model, component, worker = _tree(fault)
    result, error = _inspect(worker)
    label_index = _assert_resolved(events, node, model, component)
    assert node.fired, events
    assert error is None
    assert result is not None
    assert ("entities",) in events[label_index + 1:]
    assert result["label"] is None
    assert result["label_error"]["execution_state_unknown"] is False
    assert result["entities"] == [1, 2]
