"""T21 P0: generic Pair source()/destination() selection binding and real readback.

Audit basis: ``docs/universal_mcp_execution/evidence/full/T21-route-audit-qwen-20261108.md``
(section 3 item 1).  ``Pair.source()``/``Pair.destination()`` returning
``LocalSelection`` were re-confirmed by ``javap`` of the installed
``apiplugins/com.comsol.api_1.0.0.jar`` (2026-11-08):

    public interface com.comsol.model.Pair ...
      public abstract com.comsol.model.LocalSelection source();
      public abstract com.comsol.model.LocalSelection destination();
    public interface com.comsol.model.LocalSelection extends com.comsol.model.Selection
      public abstract java.lang.String named();
      public abstract com.comsol.model.LocalSelection named(java.lang.String);
    public interface com.comsol.model.Selection extends com.comsol.model.AbstractSelection
      public abstract int[] entities();
      public abstract com.comsol.model.Selection geom(java.lang.String, int);
      public abstract com.comsol.model.Selection geom(int);
      public abstract com.comsol.model.Selection set(int...);

The tests drive the real ``definition.pair_manage`` adapter against the
test_g3_w14 fake engine, extended locally with a ``Pair`` node whose
``source()``/``destination()`` accessors mirror that javap surface (the shared
W14 fake is deliberately not edited).  Coverage required by the task:

* named/destination binding succeeds and is read back from the endpoint
  (``named()``/``entities()``/``dim()`` probes), never from the request echo;
* pre-existing pairs without selections stay compatible;
* other pairs, the other component and another model are left untouched;
* a same-tag named selection that lives only in another component is refused
  before ``pair().create()``; a tag present in two components is recorded as a
  shadow limitation, not silently claimed unique;
* statically invalid definition/component/geometry/entity-dimension parameters
  are refused with zero mutation calls;
* a post-write readback mismatch returns ``EXECUTION_STATE_UNKNOWN`` data with
  the pair node kept (no blind rollback), and an engine exception during one
  side distinguishes that the pair create was already issued.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _g3_w14 as w14

try:  # pragma: no cover - import shim
    from tests.test_g3_w14 import (  # type: ignore[no-redef]
        FClient, FComponent, FList, FModel, FNode, FSelection, FWorker,
        _rejected, component, expect_error, geometry, model_with,
    )
except ImportError:  # pragma: no cover
    from test_g3_w14 import (  # type: ignore[no-redef]
        FClient, FComponent, FList, FModel, FNode, FSelection, FWorker,
        _rejected, component, expect_error, geometry, model_with,
    )

# ---------------------------------------------------------------------------
# fake extension: a Pair node with the javap-verified LocalSelection surface
# (kept local so tests/test_g3_w14.py's shared fake stays byte-stable)
# ---------------------------------------------------------------------------


class FLocalSel(FSelection):
    """``LocalSelection`` mirror: named()/named(String), geom(int) /
    geom(String,int), set(int...), entities(), dim(), inputDimension().

    ``inputDimension()`` is part of the real ABI - javap of the installed
    ``apiplugins/com.comsol.api_1.0.0.jar`` (2026-11-08) shows
    ``com.comsol.model.Selection ... public abstract int inputDimension();``
    and ``LocalSelection extends Selection`` - so the fake must carry it.  It
    reports the dimension the endpoint itself recorded in ``geom(...)``, i.e.
    independently of what a request asked for; subclasses that model a
    swallowing or lying endpoint override it.
    """

    def __init__(self, owner: Any = None) -> None:
        super().__init__(owner)
        self.geometry_ref: str | None = None

    def geom(self, *args: Any) -> Any:
        self.calls.append(("geom", args))
        if not args:
            return self.geometry_ref if self.geometry_ref is not None else self.dimension
        if isinstance(args[0], str):
            self.geometry_ref = str(args[0])
            self.dimension = int(args[1]) if len(args) > 1 else None
        else:
            self.dimension = int(args[0])
        return None

    def dim(self) -> Any:
        return self.dimension if self.dimension is not None else 0

    def inputDimension(self) -> int:  # noqa: N802 - Java spelling
        return self.dimension if self.dimension is not None else -1


class LyingNamedSel(FLocalSel):
    """named(tag) is accepted but the getter readback reports a different tag."""

    def named(self, *args: Any) -> Any:
        self.calls.append(("named", args))
        if args:
            self.named_ref = str(args[0])
            return None
        return "selLiar" if self.named_ref else None


class DeafSetSel(FLocalSel):
    """set() is recorded but silently ignored: entities() cannot confirm it."""

    def set(self, *args: Any) -> None:
        self.calls.append(("set", args))


class FPairNode(FNode):
    """Pair node exposing ``source()``/``destination()`` as zero-arg getters
    (javap ``Pair.source() -> LocalSelection``); listed in ``unavailable`` the
    worker allow-list refusal (METHOD_REJECTED) surfaces instead."""

    def __init__(self, *args: Any, source_selection: Any = None,
                 destination_selection: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.side_selections: dict[str, FLocalSel] = {
            "source": source_selection or FLocalSel(self),
            "destination": destination_selection or FLocalSel(self),
        }

    def source(self) -> Any:
        self.calls.append(("source", ()))
        if "source" in self.unavailable:
            raise _rejected("source")
        return self.side_selections["source"]

    def destination(self) -> Any:
        self.calls.append(("destination", ()))
        if "destination" in self.unavailable:
            raise _rejected("destination")
        return self.side_selections["destination"]


def pair_factory(tag: str, *args: Any) -> FPairNode:
    return FPairNode(tag=tag, type_id=str(args[0]) if args else "Identity")


def named_selection(node_container: FList, *tags: str) -> None:
    for tag in tags:
        node_container.items[tag] = FNode(tag=tag, type_id="NamedSelection")


def pair_world() -> tuple[FWorker, FModel, FComponent, FComponent, FModel]:
    """comp1 (geom1 sdim=2, named sel1/sel2, pair factory), comp2 (named selX
    plus a same-tag pair "pair1"), and a second model "Other" that must never
    be touched by operations against model "Model"."""
    comp1 = component(geometries={"geom1": geometry("geom1", sdim=2)})
    named_selection(comp1.collections["selection"], "sel1", "sel2")
    comp1.collections["pair"].factory = pair_factory

    comp2 = component(tag="comp2")
    named_selection(comp2.collections["selection"], "selX")
    comp2.collections["pair"].factory = pair_factory
    comp2.collections["pair"].items["pair1"] = FPairNode(tag="pair1", type_id="Identity")

    model = model_with(components={"comp1": comp1, "comp2": comp2})
    other_comp = component(tag="comp1")
    other_comp.collections["pair"].factory = pair_factory
    other_comp.collections["pair"].items["pair1"] = FPairNode(tag="pair1", type_id="Identity")
    other_model = model_with(components={"comp1": other_comp})

    worker = FWorker(model)
    worker._client = FClient({"Model": model, "Other": other_model})
    return worker, model, comp1, comp2, other_model


def create_pair(worker: FWorker, arguments: Mapping[str, Any], model_tag: str = "Model") -> dict:
    return w14.OPERATIONS["definition.pair_manage"](worker, model_tag, dict(arguments))


def inspect_pair(worker: FWorker, path: Mapping[str, Any], model_tag: str = "Model") -> dict:
    return w14.OPERATIONS["definition.pair_manage"](
        worker, model_tag, {"action": "inspect", "path": dict(path)})


def pair_path(tag: str = "pair1", component_tag: str = "comp1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component_tag},
                         {"collection": "pair", "tag": tag}]}


# ---------------------------------------------------------------------------
# positive binding + endpoint readback
# ---------------------------------------------------------------------------


class TestPairSelectionBinding:
    def test_named_source_and_destination_are_bound_and_read_back(self):
        worker, _, comp1, _, _ = pair_world()
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Contact", "geometry": "geom1",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
            },
        })
        assert result["ok"] is True and result["status"] == "APPLIED"
        assert result["created"] is True and result["type_readback"] == "Contact"
        # binding went through Pair.source()/destination() and named()
        node = comp1.collections["pair"].items["pair1"]
        assert node.side_selections["source"].named_ref == "sel1"
        assert node.side_selections["destination"].named_ref == "sel2"
        # readback comes from the endpoint, not the request echo
        assert result["selection"]["source"]["engine_state"]["named"] == "sel1"
        assert result["selection"]["destination"]["engine_state"]["named"] == "sel2"
        inspected = inspect_pair(worker, pair_path("pair1"))
        assert inspected["source"]["available"] is True
        assert inspected["source"]["named"] == "sel1"
        assert inspected["destination"]["named"] == "sel2"
        # source/destination are no longer reported as unavailable worker methods
        assert set(result["unavailable"]) == {"pair_type", "pairName", "swap"}
        assert set(inspected["unavailable"]) == {"pair_type", "pairName", "hasAutoSelection",
                                                "manualSelection", "searchMethod", "searchDist"}
        # pre-write checks ran and say so
        assert result["pre_checks"]["pair_geometry_existence"] == "validated"
        assert result["pre_checks"]["source_named_existence"] == "validated"
        assert result["pre_checks"]["destination_named_existence"] == "validated"

    def test_entity_destination_binds_dimension_and_entities_from_the_endpoint(self):
        worker, _, comp1, _, _ = pair_world()
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "GeneralContact", "geometry": "geom1",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                "destination": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                                "entity_dimension": 2, "entities": [2, 5]},
            },
        })
        assert result["ok"] is True
        assert result["selection"]["destination"]["engine_state"]["entities"] == [2, 5]
        assert result["selection"]["destination"]["engine_state"]["dimension"] == 2
        node = comp1.collections["pair"].items["pair1"]
        assert node.side_selections["destination"].geometry_ref == "geom1"
        assert node.side_selections["destination"].dimension == 2
        inspected = inspect_pair(worker, pair_path("pair1"))
        assert sorted(inspected["destination"]["entities"]) == [2, 5]
        assert inspected["source"]["named"] == "sel1"

    def test_preexisting_pair_without_selections_is_reported_unset(self):
        worker, _, comp1, _, _ = pair_world()
        comp1.collections["pair"].items["pair0"] = FPairNode(tag="pair0", type_id="Identity")
        inspected = inspect_pair(worker, pair_path("pair0"))
        assert inspected["source"]["available"] is True
        assert inspected["source"]["named"] is None
        assert inspected["destination"]["named"] is None
        node = comp1.collections["pair"].items["pair0"]
        src = node.side_selections["source"]
        dst = node.side_selections["destination"]
        # inspect only probes; no setter-shaped call was recorded on either
        # side (zero-arg geom()/named() probes are reads, not mutations)
        def setter_calls(sel: Any) -> list[Any]:
            return [c for c in sel.calls
                    if c[0] in {"set", "all", "init"} or (c[0] in {"named", "geom"} and c[1])]

        assert setter_calls(src) == []
        assert setter_calls(dst) == []
        listed = w14.OPERATIONS["definition.pair_manage"](
            worker, "Model", {"action": "list", "component": "comp1"})
        rows = {row["tag"]: row for row in listed["pairs"]}
        assert rows["pair0"]["source"]["named"] is None
        assert rows["pair0"]["destination"]["available"] is True

    def test_pair_list_reports_side_state_from_endpoint_probes(self):
        worker, _, comp1, _, _ = pair_world()
        node = FPairNode(tag="pair2", type_id="Identity")
        node.side_selections["source"].named("sel1")
        node.side_selections["destination"].geom(2)
        node.side_selections["destination"].set([3])
        comp1.collections["pair"].items["pair2"] = node
        listed = w14.OPERATIONS["definition.pair_manage"](
            worker, "Model", {"action": "list", "component": "comp1"})
        row = {r["tag"]: r for r in listed["pairs"]}["pair2"]
        assert row["source"]["available"] is True and row["source"]["named"] == "sel1"
        assert row["destination"]["entities"] == [3]
        assert row["destination"]["dimension"] == 2

    def test_binding_touches_only_the_target_pair_component_and_model(self):
        worker, _, comp1, comp2, other_model = pair_world()
        other_pair = other_model.collections["component"].items["comp1"].collections["pair"].items["pair1"]
        untouched = comp2.collections["pair"].items["pair1"]
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Contact", "geometry": "geom1",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                "destination": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                                "entity_dimension": 2, "entities": [4]},
            },
        })
        assert result["ok"] is True
        # the same-tag pair inside comp2 and inside model "Other" was never called
        assert untouched.calls == []
        assert untouched.side_selections["source"].calls == []
        assert untouched.side_selections["destination"].calls == []
        assert other_pair.calls == []
        assert other_pair.side_selections["source"].calls == []
        # comp2's named selections stayed exactly as they were
        assert sorted(comp2.collections["selection"].items) == ["selX"]
        assert comp2.collections["selection"].items["selX"].calls == []

    def test_named_readback_mismatch_is_execution_state_unknown_not_success(self):
        worker, _, comp1, _, _ = pair_world()
        comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
            tag=tag, type_id=str(args[0]) if args else "Identity",
            source_selection=LyingNamedSel(None),
        )
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Contact", "geometry": "geom1",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
            },
        })
        assert result["ok"] is False
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["partial_change"] is True
        assert result["created"] is True  # the pair node stays: no blind rollback
        assert "pair1" in comp1.collections["pair"].items
        failed = result["failed"][0]
        assert failed["stage"] == "selection" and failed["side"] == "source"
        assert failed["code"] == "EXECUTION_STATE_UNKNOWN"
        assert failed["partial_change"] is True
        # the engine's own state (the lie) is reported, not the requested value
        assert failed["engine_state_after_attempt"]["named"] == "selLiar"

    def test_ignored_set_reports_endpoint_state_not_request_echo(self):
        worker, _, comp1, _, _ = pair_world()
        comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
            tag=tag, type_id=str(args[0]) if args else "Identity",
            destination_selection=DeafSetSel(None),
        )
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Contact", "geometry": "geom1",
                "destination": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                                "entity_dimension": 2, "entities": [3]},
            },
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["failed"][0]["engine_state_after_attempt"]["entities"] == []
        assert "pair1" in comp1.collections["pair"].items

    def test_binding_getter_failure_keeps_applied_source_and_reports_partial(self):
        worker, _, comp1, _, _ = pair_world()

        class NoDestination(FPairNode):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.unavailable.add("destination")

        comp1.collections["pair"].factory = lambda tag, *args: NoDestination(
            tag=tag, type_id=str(args[0]) if args else "Identity")
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Contact", "geometry": "geom1",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                "destination": {"kind": "named", "component": "comp1", "tag": "sel2"},
            },
        })
        assert result["ok"] is False
        assert result["status"] == "PARTIAL_FAILURE"
        assert result["execution_state_unknown"] is False
        assert result["partial_change"] is True
        assert [row["side"] for row in result["applied"]] == ["source"]
        failed = result["failed"][0]
        assert failed["side"] == "destination"
        # the failure happened after pair().create() had already been issued
        assert failed["mutation_issued"] is True
        assert result["created"] is True
        node = comp1.collections["pair"].items["pair1"]
        assert node.side_selections["source"].named_ref == "sel1"  # not rolled back
        assert node.side_selections["destination"].named_ref is None


# ---------------------------------------------------------------------------
# pre-write refusals: statically bad parameters never reach pair().create()
# ---------------------------------------------------------------------------


class TestPairSelectionPreWrite:
    def _refused_without_mutation(self, worker: FWorker, comp1: FComponent, definition: Mapping[str, Any],
                                  code: str) -> ExecutionContractError:
        before = list(comp1.collections["pair"].mutation_calls())
        error = expect_error(code, create_pair, worker, {
            "action": "create", "component": "comp1", "tag": "pair1", "definition": dict(definition),
        })
        assert comp1.collections["pair"].mutation_calls() == before
        return error

    def test_named_reference_existing_only_in_other_component_refused_pre_write(self):
        worker, _, comp1, comp2, _ = pair_world()
        error = self._refused_without_mutation(worker, comp1, {
            "type": "Contact", "geometry": "geom1",
            "source": {"kind": "named", "component": "comp1", "tag": "selX"},
        }, "NODE_NOT_FOUND")
        assert "does not exist in component 'comp1'" in str(error)
        assert comp2.collections["pair"].items["pair1"].calls == []

    def test_selection_spec_naming_another_component_refused_pre_write(self):
        worker, _, comp1, _, _ = pair_world()
        error = self._refused_without_mutation(worker, comp1, {
            "type": "Contact", "geometry": "geom1",
            "source": {"kind": "named", "component": "comp2", "tag": "selX"},
        }, "INVALID_REQUEST")
        assert "differs from its actual owner path" in str(error)

    def test_static_invalid_selection_params_never_reach_pair_create(self):
        worker, _, comp1, _, _ = pair_world()
        cases = [
            # unsupported selection kinds in this operation
            ({"kind": "spatial", "component": "comp1", "geometry": "geom1",
              "entity_dimension": 2, "query": {"mode": "box"}}, "API_UNSUPPORTED"),
            ({"kind": "all", "component": "comp1", "geometry": "geom1",
              "entity_dimension": 2}, "API_UNSUPPORTED"),
            ({"kind": "inherited", "component": "comp1"}, "API_UNSUPPORTED"),
            # malformed specs
            ({"kind": "named", "tag": "sel1"}, "INVALID_REQUEST"),
            ({"kind": "explicit", "geometry": "geom1", "entity_dimension": 2}, "INVALID_REQUEST"),
            ({"kind": "explicit", "geometry": "geom1", "entities": [1, 2]}, "INVALID_REQUEST"),
            # entity_dimension 3 is statically impossible inside sdim=2 geom1
            ({"kind": "explicit", "geometry": "geom1", "entity_dimension": 3,
              "entities": [1]}, "INVALID_REQUEST"),
            # geometry target does not exist
            ({"kind": "explicit", "geometry": "geomZ", "entity_dimension": 2,
              "entities": [1]}, "NODE_NOT_FOUND"),
            ({"kind": "named", "component": "comp1", "tag": "selMissing"}, "NODE_NOT_FOUND"),
        ]
        for source_spec, code in cases:
            self._refused_without_mutation(worker, comp1, {
                "type": "Contact", "geometry": "geom1", "source": source_spec,
            }, code)
        # a missing pair-level geometry target is refused before create as well
        self._refused_without_mutation(worker, comp1, {
            "type": "Contact", "geometry": "geomZ",
            "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
        }, "NODE_NOT_FOUND")
        # a non-mapping selection is a shape error, not an engine call
        self._refused_without_mutation(worker, comp1, {
            "type": "Contact", "source": "sel1",
        }, "INVALID_REQUEST")

    def test_source_destination_rejected_for_non_create_actions(self):
        worker, _, comp1, _, _ = pair_world()
        comp1.collections["pair"].items["pair1"] = FPairNode(tag="pair1", type_id="Identity")
        expect_error("INVALID_REQUEST", w14.OPERATIONS["definition.pair_manage"], worker, "Model", {
            "action": "inspect", "path": pair_path("pair1"),
            "definition": {"source": {"kind": "named", "component": "comp1", "tag": "sel1"}},
        })


# ---------------------------------------------------------------------------
# cross-component same-tag shadow: verified presence, unprovable resolution
# ---------------------------------------------------------------------------


class TestPairSelectionShadowing:
    def test_same_named_tag_in_two_components_is_recorded_as_shadowed_limitation(self):
        worker, _, comp1, comp2, _ = pair_world()
        named_selection(comp2.collections["selection"], "sel1")
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Identity",
                "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
            },
        })
        assert result["ok"] is True
        scope = result["pre_checks"]["source_named_tag_scope"]
        assert scope.startswith("shadowed_in_comp2")
        assert "cannot prove" in scope
        assert any("same-tag" in note or "shadow" in note for note in result["notes"])

    def test_unique_tag_across_components_is_recorded_as_unique(self):
        worker, _, comp1, _, _ = pair_world()
        result = create_pair(worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {
                "type": "Identity",
                "source": {"kind": "named", "component": "comp1", "tag": "sel2"},
            },
        })
        assert result["pre_checks"]["source_named_tag_scope"] == "unique_in_model_components"


# ---------------------------------------------------------------------------
# worker allow-list reality: the refusal table must match the Java METHODS
# ---------------------------------------------------------------------------


class TestPairSelectionWorkerContract:
    @staticmethod
    def _worker_methods() -> set[str]:
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "comsol_mcp", "worker_java",
                         "PersistentComsolWorker.java"),
            encoding="utf-8",
        ).read()
        block = source.split("private static final Set<String> METHODS", 1)[1]
        block = block.split("private static final Set<String> MODEL_UTIL", 1)[0]
        return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', block))

    def test_source_and_destination_are_allow_listed(self):
        methods = self._worker_methods()
        assert {"source", "destination"} <= methods, (
            "the Java worker METHODS allow-list must carry the javap-verified "
            "Pair.source()/destination() getters for the T21 pair binding route"
        )

    def test_worker_unavailable_table_no_longer_claims_them_absent(self):
        methods = self._worker_methods()
        assert not (w14.WORKER_UNAVAILABLE_METHODS & methods), (
            "WORKER_UNAVAILABLE_METHODS must keep meaning 'still absent from the worker'"
        )
