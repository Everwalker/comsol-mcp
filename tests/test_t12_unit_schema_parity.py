from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from collections.abc import Mapping


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT))
import comsol_mcp  # noqa: E402

from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._g2_contract import validate_typed_value  # noqa: E402
from comsol_mcp._g2_tools import registry_describe, registry_list  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402


INPUT_ROUTES = (
    "node.property_set",
    "node.property_index_set",
    "node.property_entry_set",
    "api.invoke",
)
RAW_PLOT_ROUTES = ("plot.group_create", "plot.feature_create", "plot.update")


def _route_example(operation_id: str, value: dict) -> dict:
    common = {
        "project_id": "p",
        "session_id": "s",
        "model_ref": {
            "schema_version": 1,
            "session_id": "s",
            "server_instance_id": "srv",
            "model_tag": "m",
            "generation": 1,
        },
        "expected_revision": 0,
        "idempotency_key": "k",
        "path": {"segments": []},
    }
    if operation_id == "node.property_set":
        return {**common, "properties": [{"name": "p0", "value": value}]}
    if operation_id == "node.property_index_set":
        return {**common, "name": "p0", "indices": [0], "value": value}
    if operation_id == "node.property_entry_set":
        return {**common, "name": "p0", "key": "k0", "value": value}
    if operation_id == "api.invoke":
        return {
            **common,
            "method": "getSomething",
            "arguments": [value],
            "declared_effect": "READ",
        }
    raise AssertionError(operation_id)


def _references(value):
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


class TypedValueUnitParityTests(unittest.TestCase):
    def test_draft_2020_12_input_schemas_match_unit_acceptance_in_all_routes(self):
        cases = (
            ("numeric_unit_omitted", {"kind": "float64", "shape": [], "data": 3.0}, True),
            ("numeric_unit_null", {"kind": "float64", "shape": [], "data": 3.0, "unit": None}, True),
            ("numeric_unit_text", {"kind": "float64", "shape": [], "data": 3.0, "unit": "mm"}, False),
            ("numeric_singleton_null", {
                "kind": "float64", "shape": [1], "data": [3.0], "unit": None,
            }, True),
            ("numeric_singleton_text", {
                "kind": "float64", "shape": [1], "data": [3.0], "unit": "mm",
            }, False),
            ("numeric_matrix_null", {
                "kind": "float64", "shape": [2, 2], "data": [[1.0, 2.0], [3.0, 4.0]], "unit": None,
            }, True),
            ("numeric_matrix_text", {
                "kind": "float64", "shape": [2, 2], "data": [[1.0, 2.0], [3.0, 4.0]], "unit": "mm",
            }, False),
            ("complex_unit_null", {
                "kind": "complex128", "shape": [], "data": {"real": 1.0, "imag": -0.5}, "unit": None,
            }, True),
            ("complex_unit_text", {
                "kind": "complex128", "shape": [], "data": {"real": 1.0, "imag": -0.5}, "unit": "mm",
            }, False),
            ("expression_text_unit", {
                "kind": "expression", "shape": [], "data": "3", "unit": "mm",
            }, True),
            ("expression_array_text_unit", {
                "kind": "expression", "shape": [1], "data": ["3[mm]"], "unit": "mm",
            }, True),
            ("expression_unit_null", {
                "kind": "expression", "shape": [], "data": "3", "unit": None,
            }, True),
            ("string_text_unit", {
                "kind": "string", "shape": [], "data": "length", "unit": "mm",
            }, True),
            ("string_array_text_unit", {
                "kind": "string", "shape": [1], "data": ["length"], "unit": "mm",
            }, True),
            ("string_unit_null", {
                "kind": "string", "shape": [], "data": "length", "unit": None,
            }, True),
            ("expression_explicit_literal_without_unit_field", {
                "kind": "expression", "shape": [], "data": "3[mm]",
            }, True),
        )

        listed = registry_list(limit=500)
        self.assertTrue(listed["success"])
        operation_ids = [row["operation_id"] for row in listed["data"]["operations"]]
        source_catalog = json.loads(
            (CANDIDATE_ROOT / "comsol_mcp" / "data" / "g2" / "02_ACTION_CATALOG.json").read_text(encoding="utf-8")
        )
        catalog_target_suffixes = (
            "common.schema.json#/$defs/TypedValue",
            "common.schema.json#/$defs/TypedValues",
            "common.schema.json#/$defs/PropertySet",
        )
        catalog_typed_routes = {
            row["operation_id"]
            for row in source_catalog["operations"]
            if any(reference.endswith(catalog_target_suffixes) for reference in _references(row.get("input_schema", {})))
        }
        typed_routes = {}
        plot_schemas = {}
        for operation_id in operation_ids:
            response = registry_describe(operation_id)
            self.assertTrue(response["success"], operation_id)
            schema = response["data"]["input_schema"]
            Draft202012Validator.check_schema(schema)
            common_defs = schema.get("$defs", {}).get("comsol_common", {})
            if any(name in common_defs for name in ("TypedValue", "TypedValues", "PropertySet")):
                typed_routes[operation_id] = schema
            if operation_id in RAW_PLOT_ROUTES:
                plot_schemas[operation_id] = schema

        self.assertEqual(set(plot_schemas), set(RAW_PLOT_ROUTES))
        self.assertEqual(set(typed_routes), catalog_typed_routes - set(RAW_PLOT_ROUTES))

        for operation_id, schema in plot_schemas.items():
            properties_schema = schema["properties"]["properties"]
            self.assertNotIn("TypedValue", schema.get("$defs", {}).get("comsol_common", {}))
            property_validator = Draft202012Validator(properties_schema)
            self.assertTrue(property_validator.is_valid({"expr": "T", "linecolor": "blue"}))
            if operation_id in {"plot.group_create", "plot.feature_create"}:
                self.assertEqual(len(properties_schema["oneOf"]), 3)
                self.assertTrue(property_validator.is_valid([]))
                self.assertTrue(property_validator.is_valid(None))
                self.assertFalse(property_validator.is_valid([{"name": "expr", "value": {
                    "kind": "expression", "shape": [], "data": "T", "unit": "mm",
                }}]))
            else:
                self.assertEqual(properties_schema["type"], "object")
                self.assertFalse(property_validator.is_valid([]))
                self.assertFalse(property_validator.is_valid(None))

        for operation_id, schema in typed_routes.items():
            with self.subTest(operation=operation_id, case="full_schema_well_formed"):
                Draft202012Validator.check_schema(schema)
            common_defs = schema["$defs"]["comsol_common"]
            self.assertIn("TypedValue", common_defs, operation_id)
            typed_probe = {
                "$schema": schema["$schema"],
                "$defs": schema["$defs"],
                "$ref": "#/$defs/comsol_common/TypedValue",
            }
            validator = Draft202012Validator(typed_probe)
            for label, value, accepted in cases:
                with self.subTest(operation=operation_id, case=label):
                    errors = list(validator.iter_errors(value))
                    self.assertEqual(not errors, accepted, [error.message for error in errors])

        # Validate complete public inputs for the four direct schema shapes;
        # the remaining operations above reuse PropertySet/TypedValues.
        direct_inputs = {
            "node.property_set": (
                {"kind": "float64", "shape": [], "data": 3.0, "unit": None},
                {"kind": "expression", "shape": [], "data": "3", "unit": "mm"},
            ),
            "node.property_index_set": (
                {"kind": "float64", "shape": [], "data": 3.0, "unit": None},
                {"kind": "expression", "shape": [], "data": "3", "unit": "mm"},
            ),
            "node.property_entry_set": (
                {"kind": "float64", "shape": [], "data": 3.0, "unit": None},
                {"kind": "expression", "shape": [], "data": "3", "unit": "mm"},
            ),
            "api.invoke": (
                {"kind": "float64", "shape": [], "data": 3.0, "unit": None},
                {"kind": "expression", "shape": [], "data": "3", "unit": "mm"},
            ),
        }
        for operation_id, values in direct_inputs.items():
            schema = typed_routes[operation_id]
            validator = Draft202012Validator(schema)
            for value in values:
                with self.subTest(operation=operation_id, full_input=value):
                    validator.validate(_route_example(operation_id, value))

    def test_production_validator_preserves_null_and_text_units_and_rejects_numeric_text(self):
        valid = (
            {"kind": "boolean", "shape": [], "data": True, "unit": None},
            {"kind": "int32", "shape": [], "data": 3, "unit": None},
            {"kind": "int64", "shape": [], "data": 3, "unit": None},
            {"kind": "float64", "shape": [], "data": 3.0, "unit": None},
            {"kind": "float64", "shape": [1], "data": [3.0], "unit": None},
            {"kind": "float64", "shape": [2, 2], "data": [[1.0, 2.0], [3.0, 4.0]], "unit": None},
            {"kind": "complex128", "shape": [], "data": {"real": 1.0, "imag": -0.5}, "unit": None},
            {"kind": "expression", "shape": [], "data": "3", "unit": None},
            {"kind": "string", "shape": [], "data": "length", "unit": None},
            {"kind": "expression", "shape": [], "data": "3", "unit": "mm"},
            {"kind": "string", "shape": [], "data": "length", "unit": "mm"},
            {"kind": "expression", "shape": [1], "data": ["3[mm]"], "unit": "mm"},
            {"kind": "string", "shape": [1], "data": ["length"], "unit": "mm"},
            {"kind": "expression", "shape": [], "data": "3[mm]"},
        )
        for value in valid:
            with self.subTest(value=value):
                result = validate_typed_value(value)
                self.assertEqual(result["data"], value["data"])
                self.assertEqual(result["shape"], value["shape"])
                if "unit" in value:
                    self.assertIn("unit", result)
                    self.assertIs(result["unit"], value["unit"])
                else:
                    self.assertNotIn("unit", result)

        invalid = (
            {"kind": "float64", "shape": [], "data": 3.0, "unit": "mm"},
            {"kind": "float64", "shape": [1], "data": [3.0], "unit": "mm"},
            {"kind": "float64", "shape": [2, 2], "data": [[1.0, 2.0], [3.0, 4.0]], "unit": "mm"},
            {"kind": "complex128", "shape": [], "data": {"real": 1.0, "imag": -0.5}, "unit": "mm"},
            {"kind": "int32", "shape": [1], "data": [3], "unit": "mm"},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ExecutionContractError) as caught:
                    validate_typed_value(value)
                self.assertEqual(caught.exception.code, "PROPERTY_TYPE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
