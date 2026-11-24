from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from collections.abc import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import comsol_mcp  # noqa: E402

from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._g2_contract import validate_typed_value  # noqa: E402
from comsol_mcp._g2_registry import (  # noqa: E402
    _DESCRIBE_COMMON_NAMESPACE,
    _describe_common_definitions,
    _describe_schema_with_common_definitions,
    _effective_input_schema,
)
from comsol_mcp._g2_tools import (  # noqa: E402
    operation_describe,
    registry_describe,
    registry_list,
)

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


COMMON_REF_PREFIX = "common.schema.json#/$defs/"


def _walk(value):
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _refs(value):
    return [node["$ref"] for node in _walk(value) if isinstance(node.get("$ref"), str)]


def _common_names(value):
    return {
        reference[len(COMMON_REF_PREFIX):]
        for reference in _refs(value)
        if reference.startswith(COMMON_REF_PREFIX)
        and "/" not in reference[len(COMMON_REF_PREFIX):]
    }


def _common_closure(names, definitions):
    closure = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in closure or name not in definitions:
            continue
        closure.add(name)
        for reference in _refs(definitions[name]):
            if reference.startswith(COMMON_REF_PREFIX):
                child = reference[len(COMMON_REF_PREFIX):]
            elif reference.startswith("#/$defs/"):
                child = reference[len("#/$defs/"):]
            else:
                continue
            if "/" not in child and child in definitions and child not in closure:
                pending.append(child)
    return closure


def _resolve_local_reference(schema, reference):
    if not reference.startswith("#/"):
        raise AssertionError(f"non-local $ref in effective schema: {reference}")
    value = schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _references_with_resource_root(schema, resource_root=None):
    if isinstance(schema, Mapping):
        if isinstance(schema.get("$id"), str):
            resource_root = schema
        reference = schema.get("$ref")
        if isinstance(reference, str):
            yield reference, resource_root
        for item in schema.values():
            yield from _references_with_resource_root(item, resource_root)
    elif isinstance(schema, list):
        for item in schema:
            yield from _references_with_resource_root(item, resource_root)


class PublicUnitSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common_definitions = _describe_common_definitions()
        cls.catalog_path = PROJECT_ROOT / "comsol_mcp" / "data" / "g2" / "02_ACTION_CATALOG.json"
        cls.catalog = json.loads(cls.catalog_path.read_text(encoding="utf-8"))
        cls.catalog_by_id = {row["operation_id"]: row for row in cls.catalog["operations"]}

    def test_actual_describe_wrappers_close_every_catalog_common_reference(self):
        rows = registry_list(limit=500)["data"]["operations"]
        operation_ids = [row["operation_id"] for row in rows]
        self.assertTrue(set(self.catalog_by_id).issubset(operation_ids))

        referenced_catalog_names = set()
        for operation_id in operation_ids:
            for describe in (registry_describe, operation_describe):
                with self.subTest(operation=operation_id, describe=describe.__name__):
                    response = describe(operation_id)
                    self.assertTrue(response["success"])
                    description = response["data"]
                    wire_schema = description["input_schema"]
                    original_schema = description["catalog_input_schema"]

                    if operation_id in self.catalog_by_id:
                        catalog_entry = self.catalog_by_id[operation_id]
                        expected_catalog_schema = catalog_entry["input_schema"]
                        self.assertEqual(original_schema, expected_catalog_schema)
                        self.assertEqual(description["effect"], catalog_entry["effect"])
                        referenced_catalog_names.update(_common_names(original_schema))

                    effective_before_repair = _effective_input_schema(original_schema, operation_id)
                    expected_defs = _common_closure(
                        _common_names(effective_before_repair), self.common_definitions
                    )
                    common_defs = wire_schema.get("$defs", {}).get(
                        _DESCRIBE_COMMON_NAMESPACE, {}
                    )
                    self.assertEqual(set(common_defs), expected_defs)
                    self.assertFalse(
                        any(reference.startswith(COMMON_REF_PREFIX) for reference in _refs(wire_schema))
                    )
                    for reference, resource_root in _references_with_resource_root(wire_schema):
                        _resolve_local_reference(resource_root or wire_schema, reference)

        self.assertEqual(referenced_catalog_names, set(self.common_definitions))

    def test_direct_and_nested_common_schemas_are_available_from_the_wire_subobject(self):
        expected = {
            "node.property_set": {"NodePath", "PropertySet", "TypedValue"},
            "node.selection_set": {"NodePath", "SelectionSpec"},
            "api.invoke": {"NodePath", "TypedValues", "TypedValue"},
            "study.initial_solution_set": {"NodePath", "SolutionSpec", "Quantity"},
            "result.evaluate": {
                "EvaluationSpec", "SolutionSpec", "SelectionSpec", "Quantity"
            },
            "session.connect": {"Endpoint"},
        }
        for operation_id, names in expected.items():
            schema = registry_describe(operation_id)["data"]["input_schema"]
            local_defs = schema["$defs"][_DESCRIBE_COMMON_NAMESPACE]
            self.assertTrue(names.issubset(local_defs), operation_id)

        typed_value = registry_describe("node.property_set")["data"]["input_schema"][
            "$defs"
        ][_DESCRIBE_COMMON_NAMESPACE]["TypedValue"]
        unit_description = typed_value["properties"]["unit"]["description"]
        self.assertNotIn("unit", typed_value["required"])
        self.assertIn("expression/string TypedValue only", unit_description)
        self.assertIn("not appended to data", unit_description)
        self.assertIn("does not cause unit conversion", unit_description)
        self.assertIn("3[mm]", unit_description)
        self.assertIn("need not contain a unit literal", unit_description)
        self.assertIn("does not identify units of variables or property defaults", unit_description)
        self.assertIn("does not establish COMSOL physical interpretation", unit_description)

        no_common = registry_describe("code.describe_java")["data"]
        self.assertEqual(no_common["input_schema"], no_common["catalog_input_schema"])
        self.assertNotIn("$defs", no_common["input_schema"])

    def test_empty_and_unrelated_references_are_unchanged(self):
        for schema in (
            {},
            {"type": "object"},
            {"$ref": ""},
            {"$ref": "other.schema.json#/$defs/External"},
        ):
            self.assertEqual(_describe_schema_with_common_definitions(schema), schema)

    @unittest.skipUnless(
        Draft202012Validator is not None,
        "jsonschema Draft 2020-12 client is not installed in this Python environment",
    )
    def test_jsonschema_client_parses_the_input_schema_subobjects(self):
        model_ref = {
            "schema_version": 1,
            "session_id": "s",
            "server_instance_id": "srv",
            "model_tag": "m",
            "generation": 1,
        }
        common = {
            "project_id": "p",
            "session_id": "s",
            "model_ref": model_ref,
            "expected_revision": 0,
            "idempotency_key": "k",
        }
        examples = {
            "node.property_set": {
                **common,
                "path": {"segments": [{"collection": "component", "tag": "c1"}]},
                "properties": [
                    {
                        "name": "expr",
                        "value": {
                            "kind": "expression",
                            "shape": [],
                            "data": "3",
                            "unit": "mm",
                        },
                    }
                ],
            },
            "node.selection_set": {
                **common,
                "path": {"segments": []},
                "selection": {"kind": "all"},
            },
            "api.invoke": {
                **common,
                "path": {"segments": []},
                "method": "getSomething",
                "arguments": [
                    {"kind": "expression", "shape": [], "data": "2*pi*f"}
                ],
                "declared_effect": "READ",
            },
            "study.initial_solution_set": {
                **common,
                "step": {"segments": []},
                "source": {
                    "dataset": "dset1",
                    "time": [{"value": 1.0, "unit": "s"}],
                    "frequency": [{"value": 1.0, "unit": "Hz"}],
                },
            },
            "result.evaluate": {
                "project_id": "p",
                "session_id": "s",
                "model_ref": model_ref,
                "idempotency_key": "k",
                "spec": {
                    "expressions": ["1"],
                    "solution": {"dataset": "dset1", "time": [{"value": 1.0, "unit": "s"}]},
                    "selection": {"kind": "all"},
                },
            },
            "session.connect": {
                "project_id": "p",
                "idempotency_key": "k",
                "runtime_id": "r",
                "endpoint": {"host": "localhost", "port": 2036},
            },
        }
        for operation_id, example in examples.items():
            with self.subTest(operation=operation_id):
                # Pass exactly the returned input_schema subobject to the client.
                schema = registry_describe(operation_id)["data"]["input_schema"]
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(example)

    def test_expression_unit_annotation_does_not_change_data_or_validation_contract(self):
        annotated = validate_typed_value(
            {"kind": "expression", "shape": [], "data": "3", "unit": "mm"}
        )
        self.assertEqual(annotated["data"], "3")
        self.assertEqual(annotated["unit"], "mm")

        string_annotation = validate_typed_value(
            {"kind": "string", "shape": [], "data": "length", "unit": "mm"}
        )
        self.assertEqual(string_annotation["data"], "length")
        self.assertEqual(string_annotation["unit"], "mm")

        explicit_literal = validate_typed_value(
            {"kind": "expression", "shape": [], "data": "3[mm]"}
        )
        self.assertEqual(explicit_literal["data"], "3[mm]")
        no_literal = validate_typed_value(
            {"kind": "expression", "shape": [], "data": "2*pi*f"}
        )
        self.assertEqual(no_literal["data"], "2*pi*f")

        with self.assertRaises(ExecutionContractError) as caught:
            validate_typed_value(
                {"kind": "float64", "shape": [], "data": 3.0, "unit": "mm"}
            )
        self.assertEqual(caught.exception.code, "PROPERTY_TYPE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
