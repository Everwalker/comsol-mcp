"""T14 geometry identity on the existing ``kind=all`` operation.

The only engine-facing receivers below are in-memory fakes.  Their treatment of
multi-geometry selection is a fault model, not a claim about a COMSOL receiver.
The worker's model lookup succeeds; no public component-list, T20 sibling,
first-model-failure, spatial-query, or native/remote path is exercised.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_common import measure_selection, resolve_selection_entities


class FakeSelection:
    def __init__(self, *, fault=None):
        self.events = []
        self.geometry = None
        self.dimension = None
        self.fault = fault
        self.selected = []
        self.world = {"first": {2: [1, 2]}, "second": {2: [21, 22]}}

    def geom(self, *args):
        self.events.append(("geom", args))
        if not args:
            if self.fault == "unknown_geom_readback":
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "fake geom getter unknown")
            if self.fault == "wrong_geometry_readback":
                return "first"
            return self.geometry
        if len(args) == 1:
            self.geometry, self.dimension = "first", args[0]
        elif len(args) == 2:
            if self.fault == "unsupported_tagged_geom":
                raise ExecutionContractError("API_UNSUPPORTED", "fake tagged setter unsupported")
            tag, dimension = args
            if tag not in self.world:
                raise ExecutionContractError("NODE_NOT_FOUND", "fake geometry not found")
            self.geometry, self.dimension = tag, dimension
        else:
            raise AssertionError("unexpected fake geom arguments")

    def inputDimension(self):
        self.events.append(("inputDimension", ()))
        if self.fault == "unknown_dimension_readback":
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "fake input dimension unknown")
        if self.fault == "wrong_dimension_readback":
            return 1
        return self.dimension

    def all(self):
        self.events.append(("all", ()))
        assert self.geometry is not None and isinstance(self.dimension, int)
        self.selected = list(self.world[self.geometry][self.dimension])

    def set(self, ids):
        self.events.append(("set", (tuple(ids),)))
        self.selected = list(ids)

    def entities(self):
        self.events.append(("entities", ()))
        return list(self.selected)


class FakeMeasure:
    def __init__(self, selection):
        self._selection = selection

    def selection(self):
        return self._selection


class FakeComponent:
    def __init__(self, selection):
        self._measure = FakeMeasure(selection)

    def measure(self):
        return self._measure


class FakeModel:
    def __init__(self, component):
        self._component = component

    def component(self, tag):
        assert tag == "componentA"
        return self._component


class FakeClient:
    def __init__(self, model):
        self._model = model
        self.lookups = []

    def model(self, tag):
        self.lookups.append(tag)
        assert tag == "modelA"
        return self._model


class FakeWorker:
    def __init__(self, client):
        self._client = client

    def client(self):
        return self._client


def build_world(*, fault=None):
    selection = FakeSelection(fault=fault)
    component = FakeComponent(selection)
    client = FakeClient(FakeModel(component))
    return FakeWorker(client), client, component, selection


def resolve_all(worker):
    return resolve_selection_entities(worker, "modelA", {
        "kind": "all", "component": "componentA", "geometry": "second", "entity_dimension": 2,
    })


def test_all_success_is_bound_to_requested_geometry_and_dimension():
    worker, client, _, selection = build_world()
    result = resolve_all(worker)
    assert client.lookups == ["modelA"]
    assert result["geometry"] == "second"
    assert result["entity_dimension"] == 2
    assert result["entities"] == [21, 22]
    assert ("geom", ("second", 2)) in selection.events
    assert ("geom", ()) in selection.events
    assert ("inputDimension", ()) in selection.events


@pytest.mark.parametrize("fault", ["wrong_geometry_readback", "wrong_dimension_readback"])
def test_all_never_returns_success_when_endpoint_identity_differs(fault):
    worker, client, _, selection = build_world(fault=fault)
    with pytest.raises(ExecutionContractError) as caught:
        resolve_all(worker)
    assert client.lookups == ["modelA"]
    assert caught.value.code in {"EXECUTION_STATE_UNKNOWN", "SELECTION_READBACK_MISMATCH"}
    assert ("geom", ("second", 2)) in selection.events
    assert ("geom", ()) in selection.events


def test_all_tagged_setter_unsupported_does_not_fallback_to_default():
    worker, client, _, selection = build_world(fault="unsupported_tagged_geom")
    with pytest.raises(ExecutionContractError) as caught:
        resolve_all(worker)
    assert client.lookups == ["modelA"]
    assert caught.value.code == "API_UNSUPPORTED"
    assert selection.events == [("geom", ("second", 2))]


@pytest.mark.parametrize("fault,last", [
    ("unknown_geom_readback", ("geom", ())),
    ("unknown_dimension_readback", ("inputDimension", ())),
])
def test_all_unknown_readback_stops_further_engine_dispatch(fault, last):
    worker, client, _, selection = build_world(fault=fault)
    with pytest.raises(ExecutionContractError) as caught:
        resolve_all(worker)
    assert client.lookups == ["modelA"]
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert ("geom", ("second", 2)) in selection.events
    assert selection.events[-1] == last


def test_measure_without_requested_geometry_keeps_existing_dimension_only_route():
    _, _, component, selection = build_world()
    _, actual = measure_selection(component, 2, [1])
    assert actual is selection
    assert selection.events == [("geom", (2,)), ("set", ((1,),))]
    assert selection.geometry == "first"
