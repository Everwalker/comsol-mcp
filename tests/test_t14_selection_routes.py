"""T14: named/explicit/all/spatial selection routes across components, dimensions,
renames, and geometry rebuilds, pinned against the fake engine from
tests/test_g3_w13.py.

The acceptance scenarios pinned here follow the route implementations in
comsol_mcp/_g3_common.py (resolve_selection_entities and friends) and the
selection.* operations in comsol_mcp/_g3_w13.py: read-back for two components
that share a selection tag, mixed entity dimensions, a named selection whose
tag was replaced, a geometry-scoped selection read after a rebuild, and the
separation of entities, geometry object tags, and selection properties. No
entity number, geometry tag, or space dimension is ever guessed by the layer.
"""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp import _g3_common as common
from comsol_mcp import _g3_w13 as w13
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import ManagedBackend

try:  # pragma: no cover - import shim for direct execution
    from tests.test_g3_w13 import (
        FList, FNode, component_with, selection_node, variable_model, worker_for, expect_error,
    )
    from tests.test_g3_common import FObjectScopedSelection
except ImportError:  # pragma: no cover
    from test_g3_w13 import (  # type: ignore[no-redef]
        FList, FNode, component_with, selection_node, variable_model, worker_for, expect_error,
    )
    from test_g3_common import FObjectScopedSelection  # type: ignore[no-redef]


BOX_META = {
    "entitydim": "Int", "condition": "String", "xmin": "Double", "xmax": "Double",
    "ymin": "Double", "ymax": "Double",
}
BOX_ALLOWED = {"condition": ["intersects", "inside", "somevertex", "allvertices"]}


def two_component_model():
    """comp1 and comp2 each own a named selection 'faces' with different dimensions."""
    model, comp1 = component_with(
        selections={
            "faces": selection_node(tag="faces", entities_=[1, 2], dim_=2),
            "tips": selection_node(tag="tips", entities_=[3], dim_=0),
        },
        geometry=FNode(tag="geom1", integers={"sdim": 3}),
    )
    comp1.measure_entities = {2: [1, 2, 3], 1: [1, 2, 3, 4], 0: [1, 2, 3, 4, 5]}
    comp2 = FNode(tag="comp2", type_id="Component")
    comp2.collections["geom"] = FList(arity=2, node_type="GeomSequence")
    comp2.collections["geom"].items["g2"] = FNode(tag="g2", integers={"sdim": 2})
    comp2.collections["selection"] = FList(arity=2, node_type="SelectionFeature")
    comp2.collections["selection"].items["faces"] = selection_node(tag="faces", entities_=[9], dim_=0)
    comp2.measure_entities = {0: [1, 2], 2: [1]}
    model.collections["component"].items["comp2"] = comp2
    return model, comp1, comp2


class TestMultiComponentNamedReadback:
    def test_same_named_tag_resolves_per_component(self):
        model, _, _ = two_component_model()
        worker = worker_for(model)
        first = common.resolve_selection_entities(worker, "Model",
                                                  {"kind": "named", "component": "comp1", "tag": "faces"})
        second = common.resolve_selection_entities(worker, "Model",
                                                   {"kind": "named", "component": "comp2", "tag": "faces"})
        assert first["entities"] == [1, 2] and first["entity_dimension"] == 2
        assert second["entities"] == [9] and second["entity_dimension"] == 0
        assert first["component"] == "comp1" and second["component"] == "comp2"

    def test_selection_entities_page_is_component_scoped(self):
        model, _, _ = two_component_model()
        worker = worker_for(model)
        page = w13.selection_entities(worker, "Model",
                                      {"selection": {"kind": "named", "component": "comp2", "tag": "faces"}})
        assert page["entities"] == [9] and page["total_count"] == 1 and page["has_more"] is False
        assert page["entity_dimension"] == 0 and page["component"] == "comp2"
        rows = w13.selection_list(worker, "Model", {"component": "comp2"})
        assert rows["count"] == 1
        assert rows["selections"][0]["tag"] == "faces" and rows["selections"][0]["entity_dimension"] == 0

    def test_all_kind_enumerates_per_component_and_dimension(self):
        model, comp1, comp2 = two_component_model()
        worker = worker_for(model)
        deep = common.resolve_selection_entities(worker, "Model",
                                                 {"kind": "all", "component": "comp1", "geometry": "geom1",
                                                  "entity_dimension": 1})
        points = common.resolve_selection_entities(worker, "Model",
                                                   {"kind": "all", "component": "comp2", "geometry": "g2",
                                                    "entity_dimension": 0})
        assert deep["entities"] == [1, 2, 3, 4] and deep["entity_dimension"] == 1
        assert points["entities"] == [1, 2] and points["entity_dimension"] == 0
        assert deep["source"] == points["source"] == "measure_tool_all"
        # The all route never counts entities itself: both lists come from the
        # per-component measurement tool enumeration.
        assert comp2.calls.count(("measure", ())) >= 1


class TestDimensionAndGeometryProvenance:
    def test_named_dimension_and_geometry_are_the_engine_values(self):
        node = selection_node(tag="tips", entities_=[5], dim_=0, geometry_tag="outerGeom")
        model, _ = component_with(selections={"tips": node})
        resolved = common.resolve_selection_entities(worker_for(model), "Model",
                                                     {"kind": "named", "component": "comp1", "tag": "tips"})
        # The spec names neither a dimension nor a geometry: both values in the
        # read-back are the engine's own dim()/geom() probes.
        assert resolved["entity_dimension"] == 0
        assert resolved["geometry"] == "outerGeom"
        assert "objects" not in resolved

    def test_explicit_entities_are_echoed_not_treated_as_geometry_facts(self):
        model, comp1 = component_with(selections={})
        resolved = common.resolve_selection_entities(worker_for(model), "Model",
                                                     {"kind": "explicit", "component": "comp1",
                                                      "geometry": "geom1", "entity_dimension": 2,
                                                      "entities": [7, 3]})
        assert resolved["entities"] == [3, 7] and resolved["entity_dimension"] == 2
        assert resolved["source"] == "caller_explicit"
        assert any("selection.validate" in note for note in resolved["notes"])
        # Reading an explicit spec does not fabricate a geometry enumeration.
        assert not any(method == "measure" for method, _ in comp1.calls)


class TestRenamedSelectionReadback:
    def test_replaced_named_tag_refuses_with_engine_members_and_new_tag_reads_back(self):
        model, comp1, _ = two_component_model()
        worker = worker_for(model)
        removed = w13.selection_remove(worker, "Model", {"component": "comp1", "tag": "faces"})
        assert removed["removed"] is True
        created = w13.selection_create(worker, "Model", {
            "component": "comp1", "tag": "faces2", "type_id": "Explicit",
            "definition": {"entities": [1, 2], "entity_dimension": 2}})
        assert created["ok"] is True
        stale = {"kind": "named", "component": "comp1", "tag": "faces"}
        error = expect_error("NODE_NOT_FOUND", w13.selection_entities, worker, "Model",
                             {"selection": dict(stale)})
        assert "'faces'" in str(error) and "faces2" in str(error)
        expect_error("NODE_NOT_FOUND", w13.selection_measure, worker, "Model",
                     {"selection": dict(stale), "metrics": ["area"]})
        fresh = common.resolve_selection_entities(worker, "Model",
                                                  {"kind": "named", "component": "comp1", "tag": "faces2"})
        assert fresh["entities"] == [1, 2] and fresh["entity_dimension"] == 2


class TestGeometryRebuild:
    def test_object_keyed_named_selection_reads_fresh_entities_after_rebuild(self):
        selection = FObjectScopedSelection(tag="csel1", dim=2, objects={"blk1": [7, 8], "blk2": [9]})
        container = FList(arity=2)
        container.items["csel1"] = selection
        geom = FNode(tag="geom1", collections={"selection": container})
        model, _ = component_with(geometry=geom)
        worker = worker_for(model)
        spec = {"kind": "named", "component": "comp1", "geometry": "geom1", "tag": "csel1"}
        before = common.resolve_selection_entities(worker, "Model", dict(spec))
        assert before["objects"] == ["blk1", "blk2"] and before["entities"] == [7, 8, 9]
        # A rebuild renumbers final geometry entities but keeps the geometry
        # object tags; every entity id comes from the live engine, never a cache.
        selection.objects_ = ["blk1"]
        selection.object_entities = {"blk1": [11, 12, 13]}
        after = common.resolve_selection_entities(worker, "Model", dict(spec))
        assert after["entities"] == [11, 12, 13] and after["objects"] == ["blk1"]
        assert after["entity_dimension"] == 2  # from the engine dim(), not the request
        per_object_reads = [call for call in selection.calls if call[0] == "entities" and call[1] == ("blk1",)]
        assert len(per_object_reads) == 2

    def test_stale_geometry_selection_tag_refuses_with_actual_members(self):
        container = FList(arity=2)
        container.items["csel1"] = FObjectScopedSelection(tag="csel1", dim=2, objects={"blk1": [7]})
        geom = FNode(tag="geom1", collections={"selection": container})
        model, _ = component_with(geometry=geom)
        error = expect_error("NODE_NOT_FOUND", common.resolve_selection_entities, worker_for(model), "Model",
                             {"kind": "named", "component": "comp1", "geometry": "geom1", "tag": "csel0"})
        assert "'csel0'" in str(error) and "csel1" in str(error)

    def test_entity_ids_in_range_follows_the_current_geometry_enumeration(self):
        model, comp1, _ = two_component_model()
        worker = worker_for(model)
        request = {"selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                                 "entity_dimension": 2, "entities": [1, 2, 9]},
                   "expectations": {"entity_ids_in_range": True}}
        before = w13.selection_validate(worker, "Model", dict(request))
        check = [row for row in before["checks"] if row["check"] == "entity_ids_in_range"][0]
        assert check["status"] == "FAIL" and check["out_of_range"] == [9]
        assert before["verdict"] == "FAIL" and before["partial_change"] is False
        # The rebuild adds entity 9: the same request now passes without any
        # layer-side guessing about which ids exist.
        comp1.measure_entities = {**comp1.measure_entities, 2: [1, 2, 3, 9]}
        after = w13.selection_validate(worker, "Model", dict(request))
        assert after["verdict"] == "PASS"


class TestEntitiesObjectsAndAttributes:
    def test_box_selection_reports_attributes_separately_from_entities(self):
        model, component = component_with(geometry=FNode(tag="geom1"))

        def create(tag: str, type_id: str) -> FNode:
            node = selection_node(tag=tag, type_id=type_id, entities_=[4, 5], dim_=2,
                                  value_types=dict(BOX_META), allowed=dict(BOX_ALLOWED))
            component.collections["selection"].items[tag] = node
            return node

        component.collections["selection"].create = create
        created = w13.selection_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "box1", "type_id": "Box",
            "definition": {"entity_dimension": 2, "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0}})
        assert created["ok"] is True
        inspected = w13.selection_inspect(worker_for(model), "Model", {"component": "comp1", "tag": "box1"})
        assert inspected["type_id"] == "Box"
        assert inspected["entities"] == [4, 5]
        assert inspected["properties"]["xmax"]["value"] == 1.0
        assert inspected["properties"]["xmin"]["value"] == 0.0

    def test_geom_scoped_resolve_separates_objects_from_aggregated_entities(self):
        selection = FObjectScopedSelection(tag="csel1", dim=2, objects={"blk1": [7, 8], "blk2": [9]})
        container = FList(arity=2)
        container.items["csel1"] = selection
        geom = FNode(tag="geom1", collections={"selection": container})
        model, _ = component_with(geometry=geom)
        resolved = common.resolve_selection_entities(worker_for(model), "Model",
                                                     {"kind": "named", "component": "comp1",
                                                      "geometry": "geom1", "tag": "csel1"})
        assert resolved["objects"] == ["blk1", "blk2"]
        assert resolved["entities"] == [7, 8, 9]
        assert all(isinstance(item, str) for item in resolved["objects"])
        assert all(isinstance(item, int) for item in resolved["entities"])


class TestSpatialAcrossComponents:
    def test_spatial_query_uses_each_component_geometry_and_leaves_other_components_untouched(self):
        model, comp1, comp2 = two_component_model()
        worker = worker_for(model)
        box = {"kind": "box", "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0}
        error = expect_error("INVALID_REQUEST", w13.selection_query_spatial, worker, "Model",
                             {"component": "comp2", "geometry": "g2", "dimension": 3,
                              "query": dict(box), "tolerance": {"value": 0.0, "unit": "m"}})
        assert "space dimension 2" in str(error)  # comp2's own getSDim(), not a global guess

        def create(tag: str, type_id: str) -> FNode:
            node = selection_node(tag=tag, type_id=type_id, entities_=[4, 7], dim_=2,
                                  value_types=dict(BOX_META), allowed=dict(BOX_ALLOWED))
            comp2.collections["selection"].items[tag] = node
            return node

        comp2.collections["selection"].create = create
        result = w13.selection_query_spatial(worker, "Model",
                                             {"component": "comp2", "geometry": "g2", "dimension": 2,
                                              "query": dict(box), "tolerance": {"value": 0.0, "unit": "m"}})
        assert result["entities"] == [4, 7]
        assert list(comp2.collections["selection"].items) == ["faces"]
        assert sorted(comp1.collections["selection"].items) == ["faces", "tips"]


class TestBindingDependencyGates:
    def test_variable_group_create_stale_named_dependency_creates_no_node(self):
        model, component = variable_model(selections={"faces": selection_node(tag="faces")})
        error = expect_error("NODE_NOT_FOUND", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "varX", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1", "tag": "gone"}})
        assert "'gone'" in str(error) and "faces" in str(error)
        # The pre-create gate: the refusal leaves no half-configured group.
        assert "varX" not in component.collections["variable"].items

    def test_variable_group_create_unbindable_kind_creates_no_node(self):
        model, _ = variable_model()
        expect_error("API_UNSUPPORTED", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "varBad",
            "selection": {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2, "query": {"kind": "box"}}})
        assert "varBad" not in model.collections["variable"].items

    def test_variable_selection_set_stale_named_dependency_leaves_group_unchanged(self):
        model, _ = variable_model(selections={"faces": selection_node(tag="faces")})
        group = FNode(tag="g1", expressions={})
        model.collections["variable"].items["g1"] = group
        worker = worker_for(model)
        expect_error("NODE_NOT_FOUND", w13.variable_selection_set, worker, "Model", {
            "group": {"segments": [{"collection": "variable", "tag": "g1"}]},
            "selection": {"kind": "named", "component": "comp1", "tag": "gone"}})
        assert group.named_ref is None
        applied = w13.variable_selection_set(worker, "Model", {
            "group": {"segments": [{"collection": "variable", "tag": "g1"}]},
            "selection": {"kind": "named", "component": "comp1", "tag": "faces"}})
        assert applied["ok"] is True and group.named_ref == "faces"


class TestComponentMismatchGate:
    """A selection spec naming a component must match the group's own component.

    Selection.named(tag) and model(component) resolve inside the component that
    owns the variable group, so validating the tag in a different component
    accepts a reference the binding then resolves to the group component's
    same-tag selection (probe t14b, pre-fix: a comp1 group silently bound
    comp1's 'faces' while the caller named comp2's).  The accepted
    node.selection_set contract refuses this disagreement before the first
    write; a global group may still take its component from the spec.
    """

    def group_path(self, group_tag: str) -> dict[str, Any]:
        return {"segments": [{"collection": "component", "tag": "comp1"},
                             {"collection": "variable", "tag": group_tag}]}

    def test_group_create_named_component_mismatch_refuses_before_create(self):
        model, comp1, _ = two_component_model()
        error = expect_error("INVALID_REQUEST", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "varX", "component": "comp1",
            "selection": {"kind": "named", "component": "comp2", "tag": "faces"}})
        assert "comp2" in str(error) and "comp1" in str(error)
        assert "varX" not in comp1.collections["variable"].items

    def test_group_create_named_same_component_binds(self):
        model, comp1, _ = two_component_model()
        result = w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varOk", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1", "tag": "faces"}})
        node = comp1.collections["variable"].items["varOk"]
        assert result["created"] is True and node.named_ref == "faces"
        # The gate never fires for the effective same component.
        assert node.model_ref == "comp1"

    def test_selection_set_named_mismatch_then_same_component_binds(self):
        model, comp1, _ = two_component_model()
        group = FNode(tag="gv1", expressions={})
        comp1.collections["variable"].items["gv1"] = group
        worker = worker_for(model)
        expect_error("INVALID_REQUEST", w13.variable_selection_set, worker, "Model", {
            "group": self.group_path("gv1"),
            "selection": {"kind": "named", "component": "comp2", "tag": "faces"}})
        assert group.named_ref is None and group.model_ref is None
        applied = w13.variable_selection_set(worker, "Model", {
            "group": self.group_path("gv1"),
            "selection": {"kind": "named", "component": "comp1", "tag": "faces"}})
        assert applied["ok"] is True and group.named_ref == "faces"

    def test_group_create_explicit_component_mismatch_refuses(self):
        model, comp1, _ = two_component_model()
        expect_error("INVALID_REQUEST", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "varE", "component": "comp1",
            "selection": {"kind": "explicit", "component": "comp2", "geometry": "g2",
                          "entity_dimension": 2, "entities": [1]}})
        # Pre-fix, the entities were written onto the comp1 group with comp2's
        # geometry tag; now nothing is created.
        assert "varE" not in comp1.collections["variable"].items

    def test_group_create_inherited_component_mismatch_refuses(self):
        model, comp1, _ = two_component_model()
        expect_error("INVALID_REQUEST", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "varI", "component": "comp1",
            "selection": {"kind": "inherited", "component": "comp2"}})
        assert "varI" not in comp1.collections["variable"].items

    def test_selection_set_inherited_mismatch_leaves_group_unchanged(self):
        model, comp1, _ = two_component_model()
        group = FNode(tag="gv2", expressions={})
        comp1.collections["variable"].items["gv2"] = group
        expect_error("INVALID_REQUEST", w13.variable_selection_set, worker_for(model), "Model", {
            "group": self.group_path("gv2"),
            "selection": {"kind": "inherited", "component": "comp2"}})
        assert group.inheriting_ is False and group.model_ref is None


class TestSelectionLabelReadback:
    def test_selection_inspect_reads_back_a_renamed_label(self):
        model, _ = component_with(geometry=FNode(tag="geom1"))
        worker = worker_for(model)
        created = w13.selection_create(worker, "Model", {
            "component": "comp1", "tag": "faces", "type_id": "Explicit",
            "definition": {"geometry": "geom1", "entities": [1, 2], "entity_dimension": 2}})
        assert created["ok"] is True
        updated = w13.selection_update(worker, "Model", {
            "component": "comp1", "tag": "faces", "definition": {"label": "Outer boundary"}})
        assert updated["ok"] is True
        inspected = w13.selection_inspect(worker, "Model", {"component": "comp1", "tag": "faces"})
        assert inspected["label"] == "Outer boundary" and inspected["label_error"] is None
        assert inspected["entities"] == [1, 2] and inspected["type_id"] == "Explicit"


class TestManagedRouteRefusalPublication:
    """The gates above must survive the public managed write route as refusals.

    ManagedBackend._dispatch_with_witness only publishes a pre-write refusal
    when the raise declares the validation stage *and* the witness saw no
    mutation-class engine call; anything else becomes fail-closed
    EXECUTION_STATE_UNKNOWN (W13_T006 mechanism, tests/test_m1_product_repairs.py).
    With the fake engine the witness is clean by construction, so these pins
    also assert the engine-side proof: no node was ever created.
    """

    def test_component_mismatch_publishes_refused_not_unknown(self):
        model, comp1, _ = two_component_model()
        envelope = ManagedBackend._dispatch_with_witness(
            "variable.group_create",
            lambda: w13.variable_group_create(worker_for(model), "Model", {
                "tag": "varX", "component": "comp1",
                "selection": {"kind": "named", "component": "comp2", "tag": "faces"}}),
            effect="project_write")
        assert envelope["success"] is False
        assert envelope["error"]["code"] == "INVALID_REQUEST"
        assert envelope["data"]["status"] == "REFUSED"
        assert envelope["data"]["refused"] is True
        assert envelope["data"]["dispatch_stage"] == "validation"
        assert envelope["data"]["witness"]["mutation_issued"] is False
        assert envelope["execution_state_unknown"] is False
        assert "varX" not in comp1.collections["variable"].items

    def test_unbindable_kind_publishes_refused_not_unknown(self):
        model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
        envelope = ManagedBackend._dispatch_with_witness(
            "variable.group_create",
            lambda: w13.variable_group_create(worker_for(model), "Model", {
                "tag": "varBad",
                "selection": {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                              "entity_dimension": 2, "query": {"kind": "box"}}}),
            effect="project_write")
        assert envelope["success"] is False
        assert envelope["error"]["code"] == "API_UNSUPPORTED"
        assert envelope["data"]["status"] == "REFUSED"
        assert envelope["data"]["dispatch_stage"] == "validation"
        assert envelope["execution_state_unknown"] is False
        assert "varBad" not in model.collections["variable"].items

    def test_global_group_explicit_gate_publishes_refused_not_unknown(self):
        """T14d: the global-scope gate runs before node_create, so it must be
        published as a refusal.  Before the raise declared the validation stage
        the shared write route flattened it into EXECUTION_STATE_UNKNOWN
        ("did not declare a pre-dispatch stage", T14d probe pre-fix run).
        """
        model, _comp1 = variable_model()
        envelope = ManagedBackend._dispatch_with_witness(
            "variable.group_create",
            lambda: w13.variable_group_create(worker_for(model), "Model", {
                "tag": "varGate",
                "selection": {"kind": "explicit", "geometry": "geom1",
                              "entity_dimension": 2, "entities": [1, 2]}}),
            effect="project_write")
        assert envelope["success"] is False
        assert envelope["error"]["code"] == "INVALID_REQUEST"
        assert envelope["data"]["status"] == "REFUSED"
        assert envelope["data"]["dispatch_stage"] == "validation"
        assert envelope["data"]["witness"]["mutation_issued"] is False
        assert envelope["execution_state_unknown"] is False
        assert "varGate" not in model.collections["variable"].items

    def test_selection_set_global_group_gate_refuses_and_leaves_group_unchanged(self):
        model, _comp1 = variable_model()
        group = FNode(tag="gvGate", expressions={})
        model.collections["variable"].items["gvGate"] = group
        envelope = ManagedBackend._dispatch_with_witness(
            "variable.selection_set",
            lambda: w13.variable_selection_set(worker_for(model), "Model", {
                "group": {"segments": [{"collection": "variable", "tag": "gvGate"}]},
                "selection": {"kind": "explicit", "geometry": "geom1",
                              "entity_dimension": 2, "entities": [1, 2]}}),
            effect="project_write")
        assert envelope["success"] is False
        assert envelope["error"]["code"] == "INVALID_REQUEST"
        assert envelope["data"]["status"] == "REFUSED"
        assert envelope["data"]["dispatch_stage"] == "validation"
        assert envelope["data"]["witness"]["mutation_issued"] is False
        assert envelope["execution_state_unknown"] is False
        assert (group.named_ref, group.model_ref, group.inheriting_, group.entities_) == (
            None, None, False, None)

    def test_global_group_gate_with_spec_component_binds(self):
        """The gate stays out of the way when the spec names the component."""
        model, _comp1 = variable_model()
        w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varOk",
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2, "entities": [1, 2]}})
        node = model.collections["variable"].items["varOk"]
        assert node.entities_ == [1, 2] and node.model_ref == "comp1"

    def test_global_group_gate_behind_a_recorded_mutation_stays_unknown(self):
        """A declared stage is never the proof: with a recorded mutation-class
        engine call the same gate still fails closed to UNKNOWN.
        """
        from comsol_mcp._domain_outcome import record_engine_method

        model, _comp1 = variable_model()
        worker = worker_for(model)

        def call() -> dict[str, Any]:
            record_engine_method("create")  # what a mutation-class engine call looks like
            return w13.variable_group_create(worker, "Model", {
                "tag": "varGate",
                "selection": {"kind": "explicit", "geometry": "geom1",
                              "entity_dimension": 2, "entities": [1, 2]}})

        with pytest.raises(ExecutionContractError) as info:
            ManagedBackend._dispatch_with_witness("variable.group_create", call,
                                                  effect="project_write")
        assert info.value.code == "EXECUTION_STATE_UNKNOWN"
        assert info.value.stage == "post_dispatch"
        assert info.value.details["unproven_pre_dispatch"] is True
        assert info.value.details["cause_code"] == "INVALID_REQUEST"
