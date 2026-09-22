"""Shared G3 infrastructure: node create/remove semantics, selection specs, values.

These tests exercise ``comsol_mcp/_g3_common.py`` and the aggregator surface of
``comsol_mcp/_g3_ops.py`` against the fake COMSOL node tree defined in
``tests/test_g3_w13.py`` (kept in one place so both W13 test modules drive the
same fakes).
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_common as common
from comsol_mcp import _g3_ops as ops

try:  # pragma: no cover - import shim for direct execution
    from tests.test_g3_w13 import (
        FList, FNode, FParamCollection, FakeEngineError, INTERPOLATION_META, TestSelectionQuerySpatial,
        component_with, function_model, function_node, param_model, rows, selection_node,
        variable_model, worker_for,
    )
except ImportError:  # pragma: no cover
    from test_g3_w13 import (  # type: ignore[no-redef]
        FList, FNode, FParamCollection, FakeEngineError, INTERPOLATION_META, TestSelectionQuerySpatial,
        component_with, function_model, function_node, param_model, rows, selection_node,
        variable_model, worker_for,
    )


def expect_error(code: str, function: Any, *args: Any, **kwargs: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def function_worker(**kwargs: Any) -> tuple[Any, FNode]:
    """Model + worker whose ``func`` list creates metadata-bearing nodes."""
    model, component = function_model()
    model.collections["func"].node_factory = kwargs.pop("factory", None)
    return worker_for(model), component


class FObjectScopedSelection(FNode):
    """``GeomObjectSelection``: entities are read per object tag.

    ``FNode.entities`` only understands the dimension-int form used by
    ``MeshSelection`` (``entities(2)``); a geometry-scoped named selection
    exposes ``entities(String)`` instead, so it needs its own fake.
    """

    def __init__(self, *, objects: Mapping[str, list[int]], dim: int, **kwargs: Any) -> None:
        kwargs.setdefault("type_id", "Explicit")
        kwargs.setdefault("dim_", dim)
        super().__init__(**kwargs)
        self.objects_ = list(objects)
        self.object_entities = {tag: list(items) for tag, items in objects.items()}

    def entities(self, *args: Any) -> list[int]:
        self.calls.append(("entities", args))
        if args and isinstance(args[0], str):
            return list(self.object_entities.get(args[0], []))
        return list(self.entities_ or [])


# ---------------------------------------------------------------------------
# node_create / node_remove
# ---------------------------------------------------------------------------


class TestNodeCreate:
    def function_worker(self, **kwargs: Any) -> tuple[Any, Any]:
        model, component = function_model(**kwargs)
        return worker_for(model), component

    def test_creates_a_wrapped_node_with_type_readback(self):
        worker, component = function_model()[0], None
        worker, component = self.function_worker()
        created = common.node_create(worker, "Model", {"segments": []}, "func", "int1", "Interpolation")
        assert created["tag"] == "int1" and created["type_id"] == "Interpolation"
        assert created["created"] is True
        assert created["path"]["segments"][-1] == {"collection": "func", "tag": "int1"}
        assert created["readback"]["tags"] == ["int1"]

    def test_creates_an_untagged_collection_without_a_type(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        created = common.node_create(worker_for(model), "Model", {"segments": []}, "variable", "var2", None)
        assert created["tag"] == "var2"

    def test_same_tag_same_type_is_a_tag_conflict(self):
        worker, _ = self.function_worker(functions={"int1": function_node()})
        expect_error("TAG_CONFLICT", common.node_create, worker, "Model", {"segments": []},
                     "func", "int1", "Interpolation")

    def test_same_tag_different_type_is_a_type_conflict(self):
        worker, _ = self.function_worker(functions={"an1": function_node("an1", type_id="Analytic")})
        expect_error("TYPE_CONFLICT", common.node_create, worker, "Model", {"segments": []},
                     "func", "an1", "Interpolation")

    def test_type_is_required_for_a_typed_collection(self):
        worker, _ = self.function_worker()
        expect_error("INVALID_REQUEST", common.node_create, worker, "Model", {"segments": []},
                     "func", "int1", None)

    def test_type_is_rejected_for_an_untyped_collection(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        expect_error("INVALID_REQUEST", common.node_create, worker_for(model), "Model", {"segments": []},
                     "variable", "var2", "Explicit")

    def test_type_outside_the_verified_vocabulary_is_refused(self):
        worker, _ = self.function_worker()
        error = expect_error("API_UNSUPPORTED", common.node_create, worker, "Model", {"segments": []},
                             "func", "int1", "Imaginary")
        assert "Interpolation" in str(error)

    def test_invalid_tag_is_refused(self):
        worker, _ = self.function_worker()
        expect_error("INVALID_REQUEST", common.node_create, worker, "Model", {"segments": []},
                     "func", "1 bad", "Interpolation")

    def test_unknown_collection_is_refused(self):
        worker, _ = self.function_worker()
        expect_error("INVALID_REQUEST", common.node_create, worker, "Model", {"segments": []},
                     "nope", "x1", None)

    def test_missing_parent_node_is_reported(self):
        worker, _ = self.function_worker()
        expect_error("NODE_NOT_FOUND", common.node_create, worker, "Model",
                     {"segments": [{"collection": "component", "tag": "nope"}]}, "func", "int1", "Interpolation")

    def test_create_that_the_tag_readback_does_not_show_is_unknown_state(self):
        model, _ = function_model()
        worker = worker_for(model)
        container = model.collections["func"]

        def create(tag: str, *type_id: Any) -> FNode:
            # The engine accepted create() but tags() never shows the node, so
            # the post-create state cannot be proven.
            return FNode(tag=tag, type_id=str(type_id[0]) if type_id else "Fake")

        container.create = create  # type: ignore[assignment]
        expect_error("EXECUTION_STATE_UNKNOWN", common.node_create, worker, "Model", {"segments": []},
                     "func", "int1", "Interpolation")

    def test_type_readback_mismatch_is_unknown_state(self):
        model, _ = function_model()
        worker = worker_for(model)
        container = model.collections["func"]
        real_create = container.create

        def create(tag: str, *type_id: Any) -> FNode:
            node = real_create(tag, "Analytic")  # engine reports a different type
            return node

        container.create = create  # type: ignore[assignment]
        expect_error("EXECUTION_STATE_UNKNOWN", common.node_create, worker, "Model", {"segments": []},
                     "func", "int1", "Interpolation")


class TestNodeRemove:
    def test_removes_and_reads_back_the_tag_list(self):
        worker, _ = function_model(functions={"int1": function_node()})[0], None
        worker = worker_for(function_model(functions={"int1": function_node()})[0])
        removed = common.node_remove(worker, "Model", {"segments": []}, "func", "int1")
        assert removed["removed"] is True and removed["readback"]["tags"] == []

    def test_missing_node(self):
        worker = worker_for(function_model()[0])
        expect_error("NODE_NOT_FOUND", common.node_remove, worker, "Model", {"segments": []}, "func", "nope")

    def test_reserved_tag_cannot_be_removed(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", common.node_remove, worker_for(model), "Model", {"segments": []},
                     "param", "default")

    def test_parameter_group_removal_uses_the_group_list(self):
        model, collection = param_model(groups={"phys": rows(T0="1")})
        removed = common.node_remove(worker_for(model), "Model", {"segments": []}, "param", "phys")
        assert removed["removed"] is True
        assert [call[0] for call in collection.groups.calls].count("remove") == 1
        assert collection.expressions == {}  # ModelParam.remove(String) would have removed a parameter

    def test_residual_tag_after_removal_is_unknown_state(self):
        model, _ = function_model(functions={"int1": function_node()})
        worker = worker_for(model)
        container = model.collections["func"]
        container.remove = lambda tag: None  # type: ignore[assignment]
        expect_error("EXECUTION_STATE_UNKNOWN", common.node_remove, worker, "Model", {"segments": []},
                     "func", "int1")


# ---------------------------------------------------------------------------
# accessors and paths
# ---------------------------------------------------------------------------


class TestAccessors:
    def test_child_node_uses_the_parent_accessor(self):
        node = function_node()
        model, component = function_model(functions={"int1": node})
        worker = worker_for(model)
        assert common.child_node(worker, "Model", {"segments": []}, "func", "int1") is node
        # A component-scoped collection is reached through the node the parent
        # path names (component.func(<tag>)), not through the model's own list.
        scoped = function_node(tag="compf")
        component.collections["func"].items["compf"] = scoped
        assert common.child_node(
            worker, "Model", {"segments": [{"collection": "component", "tag": "comp1"}]},
            "func", "compf") is scoped

    def test_parameter_group_accessor(self):
        model, collection = param_model(groups={"phys": rows(T0="1")})
        node = common.child_node(worker_for(model), "Model", {"segments": []}, "param", "phys")
        assert node is collection.groups.items["phys"]

    def test_tags_of_a_component_collection(self):
        node = selection_node(tag="sel1")
        model, _ = component_with(selections={"sel1": node})
        tags = common.node_tags(worker_for(model), "Model",
                                {"segments": [{"collection": "component", "tag": "comp1"}]}, "selection")
        assert tags == ["sel1"]

    def test_split_parent_path(self):
        parent, collection, tag = common.split_parent_path(
            {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "selection", "tag": "sel1"}]})
        assert (collection, tag) == ("selection", "sel1")
        assert parent["segments"] == [{"collection": "component", "tag": "comp1"}]

    def test_split_parent_path_rejects_an_accessor_only_path(self):
        # Path problems carry the path vocabulary's own code (INVALID_NODE_PATH),
        # the same code NodePath.from_wire uses for a malformed segment.
        error = expect_error("INVALID_NODE_PATH", common.split_parent_path,
                             {"segments": [{"accessor": "func"}]})
        assert "collection+tag" in str(error)
        expect_error("INVALID_NODE_PATH", common.split_parent_path, {"segments": []})

    def test_path_with_segment_round_trips(self):
        path = common.path_with_segment(
            {"segments": [{"collection": "component", "tag": "comp1"}]}, "selection", "sel1")
        assert path == {"segments": [{"collection": "component", "tag": "comp1"},
                                     {"collection": "selection", "tag": "sel1"}]}


# ---------------------------------------------------------------------------
# selection specs
# ---------------------------------------------------------------------------


class TestSelectionSpec:
    def test_named_requires_component_and_tag(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {"kind": "named"})

    def test_explicit_requires_entities(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {"kind": "explicit"})

    def test_entity_ids_are_positive_ints(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec,
                     {"kind": "explicit", "entities": [0], "entity_dimension": 2})
        expect_error("INVALID_REQUEST", common.validate_selection_spec,
                     {"kind": "explicit", "entities": [1, 1], "entity_dimension": 2})
        expect_error("INVALID_REQUEST", common.validate_selection_spec,
                     {"kind": "explicit", "entities": [True], "entity_dimension": 2})

    def test_explicit_entity_dimension_is_required_only_to_bind(self):
        """``explicit`` is a caller-supplied list: its dimension may be unknown."""
        spec = common.validate_selection_spec({"kind": "explicit", "entities": [1]})
        assert spec == {"kind": "explicit", "entities": [1]}
        model, _ = component_with()
        resolved = common.resolve_selection_entities(worker_for(model), "Model",
                                                    {"kind": "explicit", "entities": [1]})
        assert resolved["entities"] == [1] and resolved["entity_dimension"] is None
        # Binding the same spec to a local selection does need the dimension.
        node = selection_node(tag="sel1")
        expect_error("INVALID_REQUEST", common.apply_local_selection, node, None, "Model", None,
                     {"kind": "explicit", "entities": [1]})

    def test_unknown_kind(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {"kind": "magic"})

    def test_missing_kind(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {})

    def test_unknown_field(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec,
                     {"kind": "explicit", "entities": [1], "entity_dimension": 2, "colour": "red"})

    def test_entity_dimension_bounds(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec,
                     {"kind": "explicit", "entities": [1], "entity_dimension": 4})

    def test_spatial_requires_a_query(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {
            "kind": "spatial", "component": "comp1", "geometry": "geom1", "entity_dimension": 2})

    def test_geometry_revision_must_be_an_int(self):
        expect_error("INVALID_REQUEST", common.validate_selection_spec, {
            "kind": "explicit", "entities": [1], "entity_dimension": 2, "geometry_revision": "7"})


class TestResolveSelectionEntities:
    def test_named_component_selection(self):
        node = selection_node(tag="sel1", entities_=[4, 2], dim_=2)
        model, _ = component_with(selections={"sel1": node})
        resolved = common.resolve_selection_entities(worker_for(model), "Model",
                                                    {"kind": "named", "component": "comp1", "tag": "sel1"})
        assert resolved["entities"] == [2, 4]
        assert resolved["source"] == "component_named_selection"
        assert resolved["entity_dimension"] == 2

    def test_named_geometry_selection_reads_entities_per_object(self):
        selection = FObjectScopedSelection(tag="csel1", dim=2, objects={"blk1": [7, 8], "blk2": [9]})
        container = FList(arity=2)
        container.items["csel1"] = selection
        geom = FNode(tag="geom1", collections={"selection": container})
        model, component = component_with(geometry=geom)
        resolved = common.resolve_selection_entities(worker_for(model), "Model", {
            "kind": "named", "component": "comp1", "geometry": "geom1", "tag": "csel1"})
        assert resolved["objects"] == ["blk1", "blk2"]
        assert resolved["entities"] == [7, 8, 9]
        assert resolved["source"] == "geom_named_selection"
        assert resolved["entity_dimension"] == 2
        # GeomObjectSelection exposes entities(String): one read per object tag.
        assert ("entities", ("blk1",)) in selection.calls
        assert ("entities", ("blk2",)) in selection.calls

    def test_all_kind_uses_the_measure_tool(self):
        model, component = component_with()
        component.measure_entities = {2: [1, 2, 3]}
        resolved = common.resolve_selection_entities(worker_for(model), "Model", {
            "kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 2})
        assert resolved["entities"] == [1, 2, 3]
        assert resolved["source"] == "measure_tool_all"

    def test_explicit_is_normalised(self):
        model, _ = component_with()
        resolved = common.resolve_selection_entities(worker_for(model), "Model", {
            "kind": "explicit", "entities": [3, 1], "entity_dimension": 2})
        assert resolved["entities"] == [1, 3]

    def test_spatial_is_refused_for_reads(self):
        model, _ = component_with()
        error = expect_error("API_UNSUPPORTED", common.resolve_selection_entities, worker_for(model), "Model", {
            "kind": "spatial", "component": "comp1", "geometry": "geom1", "entity_dimension": 2,
            "query": {"kind": "box"}})
        # The refusal names the refused kind and the kinds this read path can
        # resolve, so the caller is not left guessing.
        assert "spatial" in str(error)
        for kind in common.RESOLVABLE_SELECTION_KINDS:
            assert kind in str(error)

    def test_objects_is_refused(self):
        model, _ = component_with()
        error = expect_error("API_UNSUPPORTED", common.resolve_selection_entities, worker_for(model), "Model", {
            "kind": "objects", "component": "comp1", "geometry": "geom1", "object_tags": {"blk1": [1]}})
        assert "object" in str(error)

    def test_inherited_is_refused_for_reads(self):
        model, _ = component_with()
        expect_error("API_UNSUPPORTED", common.resolve_selection_entities, worker_for(model), "Model",
                     {"kind": "inherited", "component": "comp1"})


class TestApplyLocalSelection:
    def test_named_binding_with_readback(self):
        node = selection_node(tag="sel1")
        applied = common.apply_local_selection(node, None, "Model", None,
                                               {"kind": "named", "component": "comp1", "tag": "sel1"})
        assert applied["applied"][0]["readback"] == "sel1"
        assert node.named_ref == "sel1"
        assert applied["model_binding"] is None  # no component argument, no model() call

    def test_explicit_requires_a_dimension(self):
        node = selection_node(tag="sel1")
        expect_error("INVALID_REQUEST", common.apply_local_selection, node, None, "Model", None,
                     {"kind": "explicit", "entities": [1]})

    def test_explicit_readback_mismatch_is_unknown_state(self):
        node = selection_node(tag="sel1")
        node.__dict__["set"] = lambda *args: None  # the engine ignores the write
        expect_error("EXECUTION_STATE_UNKNOWN", common.apply_local_selection, node, None, "Model", None,
                     {"kind": "explicit", "entities": [1, 2], "entity_dimension": 2})

    def test_model_binding_happens_on_the_owner(self):
        node = selection_node(tag="sel1")
        owner = FNode(tag="var1", expressions={})
        applied = common.apply_local_selection(node, None, "Model", "comp1",
                                               {"kind": "named", "component": "comp1", "tag": "sel1"},
                                               owner=owner)
        assert owner.model_ref == "comp1"
        assert applied["model_binding"]["readback"] == "comp1"
        assert node.named_ref == "sel1"

    def test_model_binding_is_skipped_when_already_set(self):
        node = selection_node(tag="sel1", named_ref=None)
        owner = FNode(tag="var1", expressions={})
        owner.model_ref = "comp1"
        applied = common.apply_local_selection(node, None, "Model", "comp1",
                                               {"kind": "named", "component": "comp1", "tag": "sel1"},
                                               owner=owner)
        assert applied["model_binding"] is None

    def test_inherited_flag(self):
        node = selection_node(tag="sel1")
        applied = common.apply_local_selection(node, None, "Model", None,
                                               {"kind": "inherited", "component": "comp1"})
        assert node.inheriting_ is True
        assert applied["applied"][0]["readback"] is True

    def test_inherited_false_readback_is_unknown_state(self):
        node = selection_node(tag="sel1")
        node.inherit = lambda *args: None  # type: ignore[assignment]
        expect_error("EXECUTION_STATE_UNKNOWN", common.apply_local_selection, node, None, "Model", None,
                     {"kind": "inherited", "component": "comp1"})

    def test_spatial_is_refused(self):
        node = selection_node(tag="sel1")
        expect_error("API_UNSUPPORTED", common.apply_local_selection, node, None, "Model", None,
                     {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                      "entity_dimension": 2, "query": {"kind": "box"}})


# ---------------------------------------------------------------------------
# values, arguments and metadata
# ---------------------------------------------------------------------------


class TestArguments:
    def test_envelope_fields_pass_through_without_being_interpreted(self):
        args = common.operation_arguments({"names": ["a"], "idempotency_key": "k", "expected_revision": 3},
                                          ("names",), ("names",))
        # The control plane owns these fields: they are accepted untouched (a
        # dispatcher may forward the whole request body) and never count as
        # operation arguments.
        assert set(args) - common.ENVELOPE_FIELDS == {"names"}
        assert args["names"] == ["a"]
        assert args["idempotency_key"] == "k" and args["expected_revision"] == 3

    def test_unknown_field_is_rejected(self):
        expect_error("INVALID_REQUEST", common.operation_arguments, {"nope": 1}, (), ())

    def test_missing_required_field_is_rejected(self):
        expect_error("INVALID_REQUEST", common.operation_arguments, {}, ("names",), ("names",))

    def test_non_mapping_is_rejected(self):
        expect_error("INVALID_REQUEST", common.operation_arguments, ["names"], ("names",), ("names",))


class TestTypedValues:
    def test_scalar_conversions_use_the_metadata_kind(self):
        assert common.typed_value_from_json("on", {"kind": "boolean", "shape_rank": 0}, label="p")["data"] is True
        assert common.typed_value_from_json(2, {"kind": "int32", "shape_rank": 0}, label="p")["data"] == 2
        value = common.typed_value_from_json(2, {"kind": "float64", "shape_rank": 0}, label="p")
        assert value["data"] == 2.0 and value["shape"] == []
        assert common.typed_value_from_json("x", {"kind": "string", "shape_rank": 0}, label="p")["data"] == "x"

    def test_rank_two_string_matrix(self):
        value = common.typed_value_from_json([["a", "b"], ["c", "d"]],
                                             {"kind": "string", "shape_rank": 2}, label="table")
        assert value["shape"] == [2, 2] and value["data"][1] == ["c", "d"]

    def test_ragged_matrix_is_rejected(self):
        expect_error("PROPERTY_TYPE_MISMATCH", common.typed_value_from_json, [["a"], ["b", "c"]],
                     {"kind": "string", "shape_rank": 2}, label="table")

    def test_wrong_kind_is_rejected_without_coercion(self):
        expect_error("PROPERTY_TYPE_MISMATCH", common.typed_value_from_json, "2",
                     {"kind": "int32", "shape_rank": 0}, label="p")
        expect_error("PROPERTY_TYPE_MISMATCH", common.typed_value_from_json, 1,
                     {"kind": "boolean", "shape_rank": 0}, label="p")
        expect_error("PROPERTY_TYPE_MISMATCH", common.typed_value_from_json, float("nan"),
                     {"kind": "float64", "shape_rank": 0}, label="p")

    def test_unknown_kind_is_refused(self):
        expect_error("API_UNSUPPORTED", common.typed_value_from_json, 1,
                     {"kind": "quaternion", "shape_rank": 0}, label="p")

    def test_missing_metadata_is_refused(self):
        expect_error("API_UNSUPPORTED", common.typed_value_from_json, 1, {}, label="p")


class TestDefinitionProperties:
    def test_allowed_none_with_names_is_refused(self):
        node = FNode(tag="dnn1", type_id="DNN")
        expect_error("API_UNSUPPORTED", common.definition_properties, node, {"filename": "x"}, None,
                     label="definition")

    def test_allowed_none_without_names_is_empty(self):
        node = FNode(tag="dnn1", type_id="DNN")
        assert common.definition_properties(node, {}, None, label="definition") == []

    def test_unknown_property_is_refused(self):
        node = FNode(tag="int1", type_id="Interpolation", value_types=dict(INTERPOLATION_META))
        expect_error("INVALID_REQUEST", common.definition_properties, node, {"posx": 1.0},
                     INTERPOLATION_META.keys(), label="definition")

    def test_metadata_unknown_is_refused(self):
        node = FNode(tag="int1", type_id="Interpolation", value_types={"nargs": "Int"})
        expect_error("API_UNSUPPORTED", common.definition_properties, node, {"adaptol": 1e-3},
                     ("adaptol",), label="definition")

    def test_payload_is_built_from_engine_metadata(self):
        node = FNode(tag="int1", type_id="Interpolation", value_types=dict(INTERPOLATION_META),
                     allowed={"struct": ["grid", "sectionwise", "spreadsheet"]})
        payload = common.definition_properties(node, {"struct": "grid", "nargs": 2},
                                               INTERPOLATION_META.keys(), label="definition")
        assert [item["name"] for item in payload] == ["struct", "nargs"]
        assert payload[1]["value"]["kind"] == "int32"

    def test_allowed_value_violation_is_refused(self):
        node = FNode(tag="int1", type_id="Interpolation", value_types=dict(INTERPOLATION_META),
                     allowed={"struct": ["grid", "sectionwise"]})
        # The engine's own enumeration is authoritative (getAllowedPropertyValues),
        # and an out-of-vocabulary value is refused with the property-value code
        # instead of being handed to the setter.
        expect_error("INVALID_PROPERTY_VALUE", common.definition_properties, node,
                     {"struct": "spreadsheet"}, INTERPOLATION_META.keys(), label="definition")
        payload = common.definition_properties(node, {"struct": "grid"},
                                               INTERPOLATION_META.keys(), label="definition")
        assert payload[0]["value"]["data"] == "grid"  # an allowed value still converts


class TestSmallValidators:
    def test_quantity(self):
        assert common.quantity({"value": 1, "unit": "mm"}, "tol") == {"value": 1.0, "unit": "mm"}
        expect_error("INVALID_REQUEST", common.quantity, {"value": 1}, "tol")
        expect_error("INVALID_REQUEST", common.quantity, {"value": 1, "unit": "mm", "extra": 1}, "tol")

    def test_entity_id_array(self):
        assert common.require_entity_id_array([3, 1], "entities") == [1, 3]
        expect_error("INVALID_REQUEST", common.require_entity_id_array, [], "entities")
        assert common.require_entity_id_array([], "entities", allow_empty=True) == []

    def test_tag_and_name_validation(self):
        assert common.validate_tag("sel_1") == "sel_1"
        expect_error("INVALID_REQUEST", common.validate_tag, "_bad")
        expect_error("INVALID_REQUEST", common.validate_tag, "")

    def test_allowlist_rejection_is_detected(self):
        exc = FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        assert common.allowlist_rejected(exc) is True
        other = FakeEngineError("boom")
        assert common.allowlist_rejected(other) is False

    def test_describe_engine_failure_reports_the_allowlist_entry(self):
        exc = FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        described = common.describe_engine_failure(exc, "group")
        assert described["code"] == "METHOD_REJECTED"
        assert described["allowlist_entry_required"] == "group"
        assert "group" in described["message"]
        assert described["execution_state_unknown"] is False

    def test_describe_engine_failure_preserves_structured_unknown_from_worker_reply(self):
        exc = FakeEngineError("FlException: solve state unresolved",
                              code="ENGINE_CALL_FAILED")
        exc.reply["execution_state_unknown"] = True
        exc.failure["execution_state_unknown"] = True
        described = common.describe_engine_failure(exc, "run")
        assert described["code"] == "ENGINE_CALL_FAILED"
        assert described["execution_state_unknown"] is True

    def test_entity_list_hash_is_a_stable_digest_of_the_normalised_list(self):
        normalised = common.require_entity_id_array([3, 2, 1], "entities")
        assert normalised == [1, 2, 3]
        assert common.entity_list_hash(normalised) == common.entity_list_hash([1, 2, 3])
        assert common.entity_list_hash([1, 2, 3]) == common.entity_list_hash([1, 2, 3])
        assert common.entity_list_hash([1, 2, 3]) != common.entity_list_hash([1, 2, 4])

    def test_expression_write_readback(self):
        group = FNode(tag="var1", expressions={})
        recorded = common.expression_write(group, "X", "x+1", "shift")
        assert recorded["expression"] == "x+1" and recorded["description"] == "shift"
        assert group.expressions["X"]["expression"] == "x+1"

    def test_expression_write_mismatch_is_unknown_state(self):
        group = FNode(tag="var1", expressions={}, expression_overrides={"X": "other"})
        expect_error("EXECUTION_STATE_UNKNOWN", common.expression_write, group, "X", "x+1")

    def test_expression_remove_missing_name(self):
        group = FNode(tag="var1", expressions={})
        expect_error("NAME_NOT_FOUND", common.expression_remove, group, "X")


# ---------------------------------------------------------------------------
# aggregator surface
# ---------------------------------------------------------------------------


class TestOpsAggregator:
    W13_OPERATIONS = (
        "parameter.list", "parameter.get", "parameter.set", "parameter.remove", "parameter.group_manage",
        "variable.list", "variable.get", "variable.set", "variable.remove", "variable.group_create",
        "variable.selection_set",
        "function.list", "function.create", "function.inspect", "function.update", "function.remove",
        "function.data_import", "function.data_reload", "function.evaluate",
        "selection.list", "selection.create", "selection.inspect", "selection.update", "selection.remove",
        "selection.entities", "selection.measure", "selection.query_spatial", "selection.adjacency",
        "selection.validate",
    )

    def test_module_tuple_names_the_scheduled_workstreams(self):
        # The four W13-W16 workstream modules are always named, in workstream
        # order; a later module (for example a shared runtime surface) may be
        # appended, so this pins the scheduled set rather than the whole tuple.
        assert set(("_g3_w13", "_g3_w14", "_g3_w15", "_g3_w16")) <= set(ops._MODULES)
        assert ops._MODULES[0] == "_g3_w13"

    def test_unimplemented_workstreams_are_skipped_with_a_reason(self):
        skipped = {row["module"]: row["reason"] for row in ops.SKIPPED_MODULES}
        # A skipped module is a named module that published nothing: every skip
        # carries a non-empty reason, no skip may be a module that published
        # operations, and every skip is a module this aggregator names.
        assert set(skipped) <= set(ops._MODULES)
        for module, reason in skipped.items():
            assert reason, f"{module} must be skipped with a non-empty reason"
        assert ops.OPERATION_ORIGINS
        assert set(skipped) & set(ops.OPERATION_ORIGINS.values()) == set()

    def test_w13_operations_are_published_and_callable(self):
        assert set(self.W13_OPERATIONS) <= set(ops.IMPLEMENTED_OPERATIONS)
        assert set(self.W13_OPERATIONS) <= set(ops.DISPATCH)
        assert all(callable(ops.DISPATCH[name]) for name in self.W13_OPERATIONS)

    def test_effects_are_the_catalogue_effects(self):
        assert ops.effect_of("parameter.list") == "READ"
        assert ops.effect_of("variable.set") == "WRITE"
        assert ops.effect_of("function.evaluate") == "EVALUATE"
        assert ops.effect_of("selection.query_spatial") == "EVALUATE"
        assert ops.effect_of("no.such") is None
        assert set(ops.EFFECT_SOURCES.values()) == {"catalog"}

    def test_isolation_requirement_is_all_non_read_operations(self):
        # Each published operation is isolated exactly when its effect is not a
        # pure read; the W13 surface is asserted operation by operation so the
        # check survives a later workstream publishing its own operations.
        for name in self.W13_OPERATIONS:
            assert (name in ops.REQUIRES_ISOLATION) == (ops.EFFECTS[name] != "READ")
        assert "parameter.list" not in ops.REQUIRES_ISOLATION
        assert "selection.query_spatial" in ops.REQUIRES_ISOLATION

    def test_dispatch_calls_the_operation_function(self):
        seen: dict[str, Any] = {}
        original = ops.DISPATCH["parameter.list"]

        def stub(worker: Any, model_tag: str, arguments: dict[str, Any]) -> dict[str, Any]:
            seen.update({"worker": worker, "model_tag": model_tag, "arguments": arguments})
            return {"ok": True}

        ops.DISPATCH["parameter.list"] = stub  # type: ignore[assignment]
        try:
            assert ops.dispatch("parameter.list", "W", "Model", {"group": "phys"}) == {"ok": True}
        finally:
            ops.DISPATCH["parameter.list"] = original  # type: ignore[assignment]
        assert seen["arguments"] == {"group": "phys"}

    def test_dispatch_refuses_an_unimplemented_operation(self):
        expect_error("UNSUPPORTED_OPERATION", ops.dispatch, "physic.list", "W", "Model", {})

    def test_inventory_describes_the_surface(self):
        described = ops.describe()
        assert described["operation_count"] == len(ops.IMPLEMENTED_OPERATIONS)
        assert set(self.W13_OPERATIONS) <= set(described["operations"])
        assert set(described["requires_isolation"]) == set(ops.REQUIRES_ISOLATION)
        assert described["operations"] == sorted(described["operations"])


class TestPropertySetWireShapes:
    """The catalogue's ``PropertySet`` row array and the module mapping are one contract.

    ``docs/comsol_mcp_design_v1/common.schema.json#/$defs/PropertySet`` is an array of
    ``{name, value}`` rows (the production driver's wire form); the domain modules spell the
    same argument as a mapping.  ``property_definition`` must accept both and refuse every
    structural defect before the engine is touched.
    """

    def _rows(self, *rows: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def test_mapping_passes_through_byte_for_byte(self):
        requested = {"expr": "sin(x)", "args": ["x"]}
        assert common.property_definition(requested) == requested
        assert common.property_definition(requested) is not requested, "the result must not alias the argument"

    def test_row_array_becomes_the_mapping_of_typed_values(self):
        definition = common.property_definition(self._rows(
            {"name": "T0", "value": {"kind": "expression", "shape": [], "data": "300[K]"}},
            {"name": "Q0", "value": {"kind": "expression", "shape": [], "data": "1e5[W/m^3]", "unit": "W/m^3"}},
        ))
        assert sorted(definition) == ["Q0", "T0"]
        assert definition["T0"] == {"kind": "expression", "shape": [], "data": "300[K]"}
        assert definition["Q0"]["data"] == "1e5[W/m^3]"

    def test_empty_row_array_is_an_empty_definition(self):
        assert common.property_definition([]) == {}

    def test_a_column_vector_row_is_the_mapping_form(self):
        definition = common.property_definition(self._rows(
            {"name": "tlist", "value": {"kind": "float64", "shape": [3], "data": [1.0, 5.0, 20.0]}}))
        assert definition == {"tlist": {"kind": "float64", "shape": [3], "data": [1.0, 5.0, 20.0]}}

    @pytest.mark.parametrize("value", ["T0=300[K]", 3, 3.5, None, True])
    def test_a_scalar_container_is_refused(self, value: Any):
        expect_error("INVALID_REQUEST", common.property_definition, value, "properties")

    def test_a_row_that_is_not_an_object_is_refused(self):
        failure = expect_error("INVALID_REQUEST", common.property_definition, ["T0"], "properties")
        assert "properties[0]" in str(failure)

    def test_a_row_with_an_unknown_field_is_refused(self):
        failure = expect_error("INVALID_REQUEST", common.property_definition,
                               self._rows({"name": "T0", "value": {"kind": "expression", "shape": [], "data": "1"},
                                           "unit": "K"}), "properties")
        assert "unit" in str(failure)

    def test_a_row_without_a_name_is_refused(self):
        expect_error("INVALID_REQUEST", common.property_definition,
                     self._rows({"value": {"kind": "expression", "shape": [], "data": "1"}}), "properties")

    @pytest.mark.parametrize("name", [None, "", "   ", 3, ["T0"]])
    def test_a_non_string_or_empty_name_is_refused(self, name: Any):
        expect_error("INVALID_REQUEST", common.property_definition,
                     self._rows({"name": name, "value": {"kind": "expression", "shape": [], "data": "1"}}),
                     "properties")

    def test_a_row_without_a_value_is_refused(self):
        failure = expect_error("INVALID_REQUEST", common.property_definition, self._rows({"name": "T0"}), "properties")
        assert "value" in str(failure)

    def test_a_duplicated_name_is_refused(self):
        failure = expect_error("INVALID_REQUEST", common.property_definition, self._rows(
            {"name": "T0", "value": {"kind": "expression", "shape": [], "data": "300[K]"}},
            {"name": "T0", "value": {"kind": "expression", "shape": [], "data": "310[K]"}},
        ), "properties")
        assert "more than once" in str(failure)

    @pytest.mark.parametrize("bad", ["300[K]", 3, [], {}, {"kind": "expression"}, {"kind": "expression", "data": "3"},
                                    {"data": "3", "shape": []}, None])
    def test_a_value_that_is_not_a_wire_typed_value_is_refused(self, bad: Any):
        expect_error("INVALID_REQUEST", common.property_definition,
                     self._rows({"name": "T0", "value": bad}), "properties")

    def test_the_label_names_the_offending_row(self):
        failure = expect_error("INVALID_REQUEST", common.property_definition, self._rows(
            {"name": "a", "value": {"kind": "expression", "shape": [], "data": "1"}},
            {"name": "a", "value": {"kind": "expression", "shape": [], "data": "2"}},
        ), "definition.properties")
        assert failure.code == "INVALID_REQUEST"
        assert "definition.properties" in str(failure)
