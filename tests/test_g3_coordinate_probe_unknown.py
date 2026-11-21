"""T21 W14 live: portable non-list coordinate-create probe-UNKNOWN regression.

Permanent regression for the two integrated non-list UNKNOWN stops:

1. ``comsol_mcp._g3_w14._require_component`` stops on a structured
   ``EXECUTION_STATE_UNKNOWN`` from the shared ``model.component()``
   collection probe, before the ``component(tag)`` lookup and every later
   dependent call made through that helper's return path;
2. ``definition_coordinate_manage(action='create')`` stops on a structured
   ``EXECUTION_STATE_UNKNOWN`` from the optional ``comp.geom()`` probe,
   before the geometry existence check, the coordSystem create call and
   every later dependent call of this request.

Both routes run the REAL ``definition_coordinate_manage`` (action='create')
and the REAL ``_definition_create`` (no stub or monkeypatch on either)
against purely in-memory fake worker/client/model/component/geometry/
coordinate-container objects.  The fake coordinate container records every
create invocation and mutates only its own in-memory tag map.  Preserved
controls: healthy create, ordinary (non-structured) ``component()``/``geom()``
getter misses that keep the pre-existing optional-miss fallback, the
deterministic missing-geometry ``NODE_NOT_FOUND`` stop, and the nested
``tags()`` UNKNOWN stop from ``tag_list``.

Portability: ordinary pytest collection and execution needs no task-specific
environment; byte/identity binding of the five imported comsol_mcp modules
and of this file is supplied by an explicitly configured task-owned pytest
plugin (``-p``), never baked into this module.  The internal fake
``model.component()`` collection probe reached via ``_require_component`` is
part of this explicitly non-list coordinate-create route, not a public
component-list validation, and no component-list action is exercised.  No
real Worker, model, daemon, JVM, network or file target is used.
"""
from __future__ import annotations

import comsol_mcp._execution_contract as contract
from comsol_mcp import _g3_w14 as w14


# ---------------------------------------------------------------------------
# in-memory fakes (all calls recorded in dispatch order)
# ---------------------------------------------------------------------------

class FakeCoordNode:
    def __init__(self, trace, type_id):
        self.trace = trace
        self._type_id = type_id

    def getType(self):
        self.trace.append("coord_get_type")
        return self._type_id


class FakeCoordinateSystemList:
    """Records every create invocation; mutates only its own tag map."""

    def __init__(self, trace):
        self.trace = trace
        self.tags_map: list[str] = []
        self.create_calls: list[tuple] = []
        self._created_types: dict[str, str] = {}

    def tags(self):
        self.trace.append("coord_tags")
        return list(self.tags_map)

    def create(self, tag, geometry, type_id):
        record = ("coord_create", tag, geometry, type_id)
        self.trace.append(record)
        self.create_calls.append(record)
        self.tags_map.append(tag)
        self._created_types[tag] = type_id

    def get(self, tag):
        self.trace.append(("coord_get", tag))
        return FakeCoordNode(self.trace, self._created_types.get(tag, "Unknown"))


class FakeGeomList:
    def __init__(self, trace, tag_names, *, tags_fault=None):
        self.trace = trace
        self.tag_names = list(tag_names)
        self.tags_fault = tags_fault

    def tags(self):
        self.trace.append("geom_tags")
        if self.tags_fault is not None:
            raise self.tags_fault
        return list(self.tag_names)


class FakeComponent:
    def __init__(self, trace, geom_list, coord_list, *, geom_fault=None):
        self.trace = trace
        self.geom_list = geom_list
        self.coord_list = coord_list
        self.geom_fault = geom_fault

    def geom(self, tag=None):
        if tag is None:
            self.trace.append("geom_probe")
            if self.geom_fault is not None:
                raise self.geom_fault
            return self.geom_list
        self.trace.append(("geom_by_tag", tag))
        return self.geom_list

    def coordSystem(self):
        self.trace.append("coordSystem")
        return self.coord_list


class FakeComponents:
    def __init__(self, trace):
        self.trace = trace

    def tags(self):
        self.trace.append("component_tags")
        return ["comp1"]


class FakeModel:
    def __init__(self, trace, component, *, component_fault=None):
        self.trace = trace
        self._component = component
        self.component_fault = component_fault

    def component(self, tag=None):
        if tag is None:
            self.trace.append("component_collection")
            if self.component_fault is not None:
                raise self.component_fault
            return FakeComponents(self.trace)
        self.trace.append(("component_by_tag", tag))
        assert tag == "comp1"
        return self._component


class FakeWorker:
    """Fake client/worker; every model access is traced."""

    def __init__(self, *, component_fault=None, geom_tags=("geom1",),
                 geom_fault=None, tags_fault=None):
        self.trace: list = []
        self.coord = FakeCoordinateSystemList(self.trace)
        geom = FakeGeomList(self.trace, geom_tags, tags_fault=tags_fault)
        comp = FakeComponent(self.trace, geom, self.coord, geom_fault=geom_fault)
        self.model_entity = FakeModel(self.trace, comp, component_fault=component_fault)

    def client(self):
        self.trace.append("client")
        return self

    def model(self, tag):
        assert tag == "synthetic-model"
        self.trace.append("model")
        return self.model_entity

    @property
    def names(self) -> list[str]:
        return [c[0] if isinstance(c, tuple) else c for c in self.trace]


def _coordinate_create(worker: FakeWorker, *, geometry: str = "geom1"):
    result = None
    caught = None
    try:
        result = w14.definition_coordinate_manage(worker, "synthetic-model", {
            "action": "create", "component": "comp1", "tag": "sys1",
            "definition": {"type": "Cylindrical", "geometry": geometry},
        })
    except contract.ExecutionContractError as exc:
        caught = exc
    return result, caught


# Exact dispatch order for a healthy create through the real dispatcher chain
# (definition_coordinate_manage create branch -> membership probe ->
# _definition_create -> _definition_list -> _require_component -> coordSystem
# container -> tags/create/tags/get/getType).
FULL_CREATE_ORDER = [
    "client", "model", "component_collection", "component_tags", "component_by_tag",
    "geom_probe", "geom_tags",
    "client", "model", "component_collection", "component_tags", "component_by_tag",
    "coordSystem", "coord_tags", "coord_create", "coord_tags", "coord_get", "coord_get_type",
]
PRE_GEOM_ORDER = FULL_CREATE_ORDER[:FULL_CREATE_ORDER.index("geom_probe") + 1]
# The integrated _require_component guard must stop at the collection probe:
# nothing after call_probe(model, "component") may dispatch.
COMPONENT_PROBE_STOP_ORDER = ["client", "model", "component_collection"]
# Source-bound observation (live _g3_w14.py:2693/2696): the missing-tag branch
# evaluates ``tag_list`` twice - once in the membership test and again inside
# the node_not_found message - so the deterministic stop ends with two
# geom_tags reads.
MISSING_GEOMETRY_STOP_ORDER = PRE_GEOM_ORDER + ["geom_tags", "geom_tags"]
# An ordinary (non-structured) component() probe miss keeps the pre-existing
# optional-miss lookup: every component(tag) lookup and the create continue.
# Source-bound: the faulting getter misses at BOTH _require_component probe
# sites, so tag_list never dispatches and no component_tags read appears.
ORDINARY_COMPONENT_MISS_ORDER = [
    "client", "model", "component_collection", "component_by_tag",
    "geom_probe", "geom_tags",
    "client", "model", "component_collection", "component_by_tag",
    "coordSystem", "coord_tags", "coord_create", "coord_tags", "coord_get", "coord_get_type",
]


# ---------------------------------------------------------------------------
# primary faults: structured UNKNOWN at component collection / comp.geom()
# ---------------------------------------------------------------------------

def test_structured_component_probe_unknown_stops_before_coordinate_create():
    worker = FakeWorker(component_fault=contract.ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN", "synthetic component collection uncertainty"))
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = (f"fake_call_order={names} "
           f"synthetic_coord_create_count={len(worker.coord.create_calls)}")
    # The injected fault was actually reached (collection probe attempted).
    assert names.count("component_collection") >= 1, f"fault seam not reached: {ctx}"
    # Desired outcome: typed UNKNOWN surfaces, not a completed create.
    assert caught is not None, (
        f"expected ExecutionContractError EXECUTION_STATE_UNKNOWN but the call "
        f"returned a result object instead (created={result and result.get('created')!r}): {ctx}")
    assert caught.code == "EXECUTION_STATE_UNKNOWN", f"wrong code: {caught.code} {ctx}"
    # Nothing after the collection probe dispatched, and no coordinate create.
    assert "component_by_tag" not in names, ctx
    assert "geom_probe" not in names, ctx
    assert "coordSystem" not in names, ctx
    assert "coord_create" not in names, ctx
    assert len(worker.coord.create_calls) == 0, ctx
    assert names == COMPONENT_PROBE_STOP_ORDER, (
        f"calls continued past the UNKNOWN component collection probe: {ctx}")
    print(f"[control:component-probe-unknown-stop] first_assertions=passed {ctx}", flush=True)


def test_structured_geom_probe_unknown_stops_before_coordinate_create():
    worker = FakeWorker(geom_fault=contract.ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN", "synthetic geom collection uncertainty"))
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = (f"fake_call_order={names} "
           f"synthetic_coord_create_count={len(worker.coord.create_calls)}")
    # The injected fault was actually reached (probe attempted exactly once).
    assert names.count("geom_probe") == 1, f"fault seam not reached: {ctx}"
    # Desired outcome: typed UNKNOWN surfaces, not a completed create.
    assert caught is not None, (
        f"expected ExecutionContractError EXECUTION_STATE_UNKNOWN but the call "
        f"returned a result object instead (created={result and result.get('created')!r}): {ctx}")
    assert caught.code == "EXECUTION_STATE_UNKNOWN", f"wrong code: {caught.code} {ctx}"
    # No subsequent fake engine call and no coordinate create.
    assert "coordSystem" not in names, ctx
    assert "coord_create" not in names, ctx
    assert len(worker.coord.create_calls) == 0, ctx
    assert names == PRE_GEOM_ORDER, f"calls continued past the UNKNOWN geom probe: {ctx}"
    print(f"[control:geom-probe-unknown-stop] first_assertions=passed {ctx}", flush=True)


# ---------------------------------------------------------------------------
# source-bound positive/stop controls
# ---------------------------------------------------------------------------

def test_healthy_geometry_performs_in_memory_coordinate_create():
    worker = FakeWorker()
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = f"fake_call_order={names} synthetic_coord_create_count={len(worker.coord.create_calls)}"
    assert caught is None, f"healthy route raised {caught!r}: {ctx}"
    assert names == FULL_CREATE_ORDER, ctx
    assert len(worker.coord.create_calls) == 1, ctx
    assert worker.coord.create_calls[0] == ("coord_create", "sys1", "geom1", "Cylindrical"), ctx
    assert result["created"] is True and result["type_readback"] == "Cylindrical", ctx
    assert result["readback"]["tags"] == ["sys1"], ctx
    assert names.count("geom_probe") == 1, ctx  # single geom probe on this route
    print(f"[control:healthy-create] first_assertions=passed {ctx}", flush=True)


def test_ordinary_optional_geom_getter_miss_keeps_existing_create():
    worker = FakeWorker(geom_fault=RuntimeError("synthetic optional miss"))
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = f"fake_call_order={names} synthetic_coord_create_count={len(worker.coord.create_calls)}"
    assert caught is None, f"ordinary miss must keep the fallback: {ctx}"
    assert "geom_probe" in names and "geom_tags" not in names, ctx
    assert names.count("geom_probe") == 1, ctx
    # pre-existing optional-miss fallback: the in-memory coordinate create occurs
    assert len(worker.coord.create_calls) == 1, ctx
    assert result["created"] is True, ctx
    print(f"[control:ordinary-getter-miss-fallback] first_assertions=passed {ctx}", flush=True)


def test_ordinary_component_probe_miss_keeps_existing_lookup_and_create():
    worker = FakeWorker(component_fault=RuntimeError("synthetic optional component miss"))
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = f"fake_call_order={names} synthetic_coord_create_count={len(worker.coord.create_calls)}"
    assert caught is None, f"ordinary component probe miss must keep the fallback: {ctx}"
    # the structured-UNKNOWN guard does not fire; the pre-existing optional-miss
    # component(tag) lookup and the create continue
    assert names == ORDINARY_COMPONENT_MISS_ORDER, ctx
    assert len(worker.coord.create_calls) == 1, ctx
    assert result["created"] is True, ctx
    print(f"[control:ordinary-component-miss-fallback] first_assertions=passed {ctx}", flush=True)


def test_deterministic_missing_geometry_stops_before_coordinate_create():
    worker = FakeWorker(geom_tags=("geom1",))
    result, caught = _coordinate_create(worker, geometry="geom2")
    names = worker.names
    ctx = f"fake_call_order={names} synthetic_coord_create_count={len(worker.coord.create_calls)}"
    assert caught is not None and caught.code == "NODE_NOT_FOUND", ctx
    assert names == MISSING_GEOMETRY_STOP_ORDER, ctx
    assert len(worker.coord.create_calls) == 0, ctx
    print(f"[control:missing-geometry-stop] first_assertions=passed {ctx}", flush=True)


def test_nested_tags_structured_unknown_stops_before_coordinate_create():
    worker = FakeWorker(tags_fault=contract.ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN", "synthetic geom tags uncertainty"))
    result, caught = _coordinate_create(worker)
    names = worker.names
    ctx = f"fake_call_order={names} synthetic_coord_create_count={len(worker.coord.create_calls)}"
    assert caught is not None and caught.code == "EXECUTION_STATE_UNKNOWN", ctx
    assert "coordSystem" not in names and "coord_create" not in names, ctx
    assert len(worker.coord.create_calls) == 0, ctx
    print(f"[control:nested-tags-unknown-stop] first_assertions=passed {ctx}", flush=True)