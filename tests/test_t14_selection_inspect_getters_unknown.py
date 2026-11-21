"""T14 sibling fault matrix: post-resolution getter UNKNOWNs in one inspect.

One named ``selection_inspect`` session per case, same in-memory shape as
``test_selection_inspect_unknown_v2``: the real product function runs against
fake client/model/component/selection nodes from ``tests.test_g3_w13``.  After
the fake client/model calls and actual node resolution complete, exactly one
structured UNKNOWN is injected at one *counted occurrence* of one node getter.
Assertions (made only from the ordered event log, never by re-dispatching
fakes) require: the injected fault actually fired during the real call, the
product dispatched no further node getter behind it, and an
``ExecutionContractError`` with ``code=EXECUTION_STATE_UNKNOWN`` and
``safe_retry=False`` escaped.  Direct-code and structured-bit unknown varieties
alternate per site.  One healthy and one ordinary optional-METHOD_REJECTED
positive control keep the data-shaped contract pinned.

Portable regression: the original isolated test was authored without execution.  The fenced
list/cursor/first-model routes, the coordinate-management ``action=list``
outcome, and the T20 sibling-validation guarded outcome stay out of scope;
no real model, Worker, SSH, or external state is touched.
"""
from __future__ import annotations

import pytest

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w13 import FakeEngineError, FClient, FNode, FWorker, component_with

GETTER_METHODS = frozenset(
    {
        "getType",
        "label",
        "entities",
        "dim",
        "dimension",
        "named",
        "isInheriting",
        "geom",
        "getString",
        "getDouble",
        "getValueType",
        "getAllowedPropertyValues",
    }
)


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


class WorkerShapedUnknown(RuntimeError):
    """Structured worker-shaped unknown: ``failure`` carries the unknown bit."""

    def __init__(self, method):
        super().__init__(f"synthetic {method} failure")
        self.failure = {"code": "ENGINE_CALL_FAILED", "execution_state_unknown": True}


def _unknown(kind, method):
    if kind == "direct":
        return ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"synthetic {method} unknown")
    if kind == "details":
        return ExecutionContractError(
            "ENGINE_CALL_FAILED", f"synthetic {method} failure",
            stage="validation", safe_retry=True, details={"execution_state_unknown": True},
        )
    if kind == "failure":
        return WorkerShapedUnknown(method)
    raise AssertionError(f"unexpected synthetic fault kind {kind!r}")


class RecordingSelection(FNode):
    """Every targeted node getter logs (method, actual-call-ordinal, args).

    ``fault_at`` names one (method, ordinal) pair: only that real occurrence
    raises the injected unknown and marks ``fault_fired``; ``optional_rejects``
    makes every call of a method report an ordinary METHOD_REJECTED miss (used
    to reach the property_read_rows getString -> getDouble fallback).
    """

    def __init__(self, events, fault=None, *, fault_at=None, optional_rejects=()):
        super().__init__(tag="sel1", type_id="Explicit", entities_=[1, 2], dim_=2,
                         geometry_tag="geom1")
        self.events = events
        self.fault = fault
        self.fault_at = fault_at
        self.optional_rejects = frozenset(optional_rejects)
        self._counts = {}
        self.fault_fired = False
        self.fault_event = None

    def _attempt(self, method, *args):
        occurrence = self._counts.get(method, 0) + 1
        self._counts[method] = occurrence
        event = (method, occurrence) + args
        self.events.append(event)
        if method in self.optional_rejects:
            raise FakeEngineError(f"SecurityException: METHOD_REJECTED ({method})",
                                  code="METHOD_REJECTED")
        if self.fault is not None and self.fault_at == (method, occurrence) and not self.fault_fired:
            self.fault_fired = True
            self.fault_event = event
            raise self.fault
        return None

    # -- identity / definition getters ------------------------------------
    def getType(self):
        self._attempt("getType")
        return super().getType()

    def label(self, *args):
        if args:
            return super().label(*args)
        self._attempt("label")
        return super().label()

    def entities(self, *args):
        self._attempt("entities", *args)
        return super().entities(*args)

    def dim(self):
        self._attempt("dim")
        return super().dim()

    def dimension(self):
        self._attempt("dimension")
        return list(super().dimension())

    # -- selection-state getters ------------------------------------------
    def named(self, *args):
        if args:
            return super().named(*args)
        self._attempt("named")
        return super().named()

    def isInheriting(self):
        self._attempt("isInheriting")
        return super().isInheriting()

    def geom(self, *args):
        if args:
            return super().geom(*args)
        self._attempt("geom")
        return super().geom()

    # -- property-value and metadata getters ------------------------------
    def getString(self, name):
        self._attempt("getString", name)
        return super().getString(name)

    def getDouble(self, name):
        self._attempt("getDouble", name)
        return super().getDouble(name)

    def getValueType(self, name):
        self._attempt("getValueType", name)
        return super().getValueType(name)

    def getAllowedPropertyValues(self, name):
        self._attempt("getAllowedPropertyValues", name)
        return super().getAllowedPropertyValues(name)


def _tree(fault=None, *, fault_at=None, optional_rejects=()):
    events = []
    node = RecordingSelection(events, fault, fault_at=fault_at,
                              optional_rejects=optional_rejects)
    model, component = component_with(selections={"sel1": node})
    worker = RecordingWorker(model, events)
    return events, node, model, component, worker


def _inspect(worker):
    try:
        result = w13.selection_inspect(worker, "Model", {"component": "comp1", "tag": "sel1"})
    except ExecutionContractError as exc:
        return None, exc
    return result, None


def _assert_resolved_before(events, model, component, node, fault_index):
    # Observations of dispatches that already happened inside the real call;
    # this helper never re-dispatches fake client/model/getter calls.
    assert events.index(("client",)) < events.index(("model", "Model")) < fault_index, events
    assert ("component", (None,)) in model.calls, model.calls
    assert ("component", ("comp1",)) in model.calls, model.calls
    assert ("selection", ()) in component.calls, component.calls
    assert ("selection", ("sel1",)) in component.calls, component.calls
    assert ("get", ("sel1",)) in component.collections["selection"].calls
    assert node is component.collections["selection"].items["sel1"]


# Explicit, exact case table: (case_id, method, fault-occurrence, unknown kind,
# methods reporting an ordinary METHOD_REJECTED before the injected unknown).
CASES = [
    ("C01-getType1-direct", "getType", 1, "direct", ()),
    ("C02-getType1-structured", "getType", 1, "failure", ()),
    ("C03-entities1-direct", "entities", 1, "direct", ()),
    ("C04-entities1-structured", "entities", 1, "details", ()),
    ("C05-dim1-direct", "dim", 1, "direct", ()),
    ("C06-dim1-structured", "dim", 1, "failure", ()),
    ("C07-dimension1-direct", "dimension", 1, "direct", ()),
    ("C08-dimension1-structured", "dimension", 1, "details", ()),
    ("C09-named1-direct", "named", 1, "direct", ()),
    ("C10-named1-structured", "named", 1, "failure", ()),
    ("C11-getString1-direct", "getString", 1, "direct", ()),
    ("C12-getString1-structured", "getString", 1, "details", ()),
    ("C13-getDouble1-direct", "getDouble", 1, "direct", ("getString",)),
    ("C14-getDouble1-structured", "getDouble", 1, "failure", ("getString",)),
    ("C15-getValueType1-direct", "getValueType", 1, "direct", ()),
    ("C16-getValueType1-structured", "getValueType", 1, "details", ()),
    ("C17-getAllowedValues1-direct", "getAllowedPropertyValues", 1, "direct", ()),
    ("C18-getAllowedValues1-structured", "getAllowedPropertyValues", 1, "failure", ()),
    ("C19-getType2-direct", "getType", 2, "direct", ()),
    ("C20-getType2-structured", "getType", 2, "details", ()),
]


@pytest.mark.parametrize(
    "case_id, method, occurrence, kind, optional_rejects",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_post_resolution_getter_unknown_stops_dependent_probes(
    case_id, method, occurrence, kind, optional_rejects
):
    fault = _unknown(kind, f"{method}#{occurrence}")
    events, node, model, component, worker = _tree(
        fault, fault_at=(method, occurrence), optional_rejects=optional_rejects
    )
    result, error = _inspect(worker)

    assert node.fault_fired is True, (case_id, events)
    fault_event = node.fault_event
    assert fault_event is not None, (case_id, events)
    assert fault_event[:2] == (method, occurrence), (case_id, fault_event, events)
    fault_index = events.index(fault_event)
    _assert_resolved_before(events, model, component, node, fault_index)

    later_getters = [event for event in events[fault_index + 1:] if event[0] in GETTER_METHODS]
    assert later_getters == [], (case_id, events)
    assert error is not None, (case_id, events, result)
    assert error.code == "EXECUTION_STATE_UNKNOWN", (case_id, error.code)
    assert error.safe_retry is False, (case_id, error.safe_retry)


def _getter_order(events):
    return [event for event in events if event[0] in GETTER_METHODS]


def _first(order, method):
    return min(index for index, event in enumerate(order) if event[0] == method)


def test_c21_healthy_control_all_probe_sites_complete_data_shaped():
    events, node, model, component, worker = _tree()
    result, error = _inspect(worker)

    assert node.fault_fired is False
    assert error is None, events
    assert result is not None, events

    order = _getter_order(events)
    assert order[0] == ("getType", 1)
    assert order[1] == ("label", 1)
    assert order[2] == ("entities", 1)
    assert ("getDouble", 1) not in order
    first_string = _first(order, "getString")
    assert first_string < _first(order, "getValueType")
    assert _first(order, "getValueType") < _first(order, "getAllowedPropertyValues")
    assert ("getType", 2) in order
    assert order.index(("getType", 2)) > _first(order, "getAllowedPropertyValues")
    dim1 = order.index(("dim", 1))
    assert order[dim1 + 1] == ("dimension", 1)
    named1 = order.index(("named", 1))
    assert _first(order, "getAllowedPropertyValues") < dim1 < named1
    assert order[named1:] == [
        ("named", 1), ("isInheriting", 1), ("entities", 2), ("dim", 2), ("geom", 1),
    ]

    assert result["type_id"] == "Explicit"
    assert result["label"] == "sel1 label"
    assert result["label_error"] is None
    assert result["entities"] == [1, 2]
    assert result["entity_dimension"] == 2
    assert result["state"]["geometry"] == "geom1"
    assert result["state"]["named"] is None


def test_c22_ordinary_optional_method_rejected_entities_still_completes():
    events, node, model, component, worker = _tree(optional_rejects=("entities",))
    result, error = _inspect(worker)

    assert node.fault_fired is False
    assert error is None, (events, result)
    assert result is not None, events

    order = _getter_order(events)
    entities1 = order.index(("entities", 1))
    for later in (("dim", 1), ("dimension", 1), ("named", 1), ("dim", 2), ("geom", 1)):
        assert later in order and order.index(later) > entities1, (order, later)

    assert result["label"] == "sel1 label"
    assert result["entities"] is None
    assert result["entities_error"]["code"] == "METHOD_REJECTED"
    assert result["entities_error"]["execution_state_unknown"] is False
    assert result["state"]["entities"] is None
    assert result["state"]["entities_error"]["code"] == "METHOD_REJECTED"
