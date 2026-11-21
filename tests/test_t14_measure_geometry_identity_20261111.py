"""Selection-measure geometry binding on the real W13 Python operation.

Only in-memory fake receivers are used. This is not a COMSOL/native test and
does not exercise the spatial query, public component list or blocked paths.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_w13 import selection_measure
from test_t14_all_geometry_identity_20261111 import build_world


def prepare_measure():
    worker, client, component, selection = build_world()
    measure = component.measure()

    def area():
        selection.events.append(("getArea", (selection.geometry,)))
        assert isinstance(selection.geometry, str)
        return {"first": 1.0, "second": 2.0}[selection.geometry]

    setattr(measure, "getArea", area)
    return worker, client, selection


def all_spec():
    return {
        "kind": "all", "component": "componentA", "geometry": "second", "entity_dimension": 2,
    }


def test_measure_all_uses_the_same_geometry_after_resolving_entities():
    worker, client, selection = prepare_measure()
    result = selection_measure(worker, "modelA", {"selection": all_spec(), "metrics": ["area"]})
    assert client.lookups
    assert result["selection"]["entities"] == [21, 22]
    assert result["geometry"] == "second"
    assert result["metrics"]["area"]["value"]["data"] == 2.0
    assert ("getArea", ("second",)) in selection.events
    assert selection.events.count(("geom", ("second", 2))) == 2


@pytest.mark.parametrize("fault", ["wrong_second_readback", "unknown_second_readback"])
def test_measure_refuses_bad_second_geometry_readback_before_metrics(fault):
    worker, client, selection = prepare_measure()
    original_geom = selection.geom
    reads = 0

    def geom(*args):
        nonlocal reads
        if not args:
            reads += 1
            if reads == 2:
                selection.events.append(("geom", ()))
                if fault == "unknown_second_readback":
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "fake second readback unknown")
                return "first"
        return original_geom(*args)

    selection.geom = geom
    with pytest.raises(ExecutionContractError) as caught:
        selection_measure(worker, "modelA", {"selection": all_spec(), "metrics": ["area"]})
    assert client.lookups
    assert reads == 2
    assert caught.value.code in {"EXECUTION_STATE_UNKNOWN", "SELECTION_READBACK_MISMATCH"}
    assert selection.events[-1] == ("geom", ())
    assert not any(name == "getArea" for name, _ in selection.events)


def test_measure_explicit_without_geometry_keeps_dimension_only_route():
    worker, client, selection = prepare_measure()
    spec = {"kind": "explicit", "component": "componentA", "entity_dimension": 2, "entities": [1]}
    result = selection_measure(worker, "modelA", {"selection": spec, "metrics": ["area"]})
    assert client.lookups
    assert result["metrics"]["area"]["value"]["data"] == 1.0
    assert ("geom", (2,)) in selection.events
    assert not any(name == "inputDimension" for name, _ in selection.events)
