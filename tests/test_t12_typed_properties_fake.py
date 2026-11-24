"""T12 focused fake coverage against the fixed production implementation.

The fake only implements the PropFeature calls consumed by the production
property schema/get/set functions. It does not model Java or COMSOL behavior.
"""
from __future__ import annotations

from copy import deepcopy
import sys
import unittest

sys.dont_write_bytecode = True

from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._g2_engine import property_get, property_schema, property_set  # noqa: E402


PATH = {"segments": []}


class MemoryPropFeature:
    def __init__(self) -> None:
        self.types = {
            "flag": "Boolean",
            "count": "Int",
            "gain": "Double",
            "label": "String",
            "flag_empty": "BooleanArray",
            "flag_one": "BooleanArray",
            "count_empty": "IntArray",
            "count_one": "IntArray",
            "gain_empty": "DoubleArray",
            "gain_one": "DoubleArray",
            "label_empty": "StringArray",
            "label_one": "StringArray",
            "flag_matrix": "BooleanMatrix",
            "count_matrix": "IntMatrix",
            "gain_matrix": "DoubleMatrix",
            "label_matrix": "StringMatrix",
            "row_matrix": "DoubleRowMatrix",
            "expr": "String",
            "choice": "String",
            "enabled": "Boolean",
        }
        self.values = {
            "flag": True,
            "count": 7,
            "gain": 1.25,
            "label": "literal",
            "flag_empty": [],
            "flag_one": [True],
            "count_empty": [],
            "count_one": [3],
            "gain_empty": [],
            "gain_one": [2.5],
            "label_empty": [],
            "label_one": ["single"],
            "flag_matrix": [[True, False], [False, True]],
            "count_matrix": [[1, 2], [3, 4]],
            "gain_matrix": [[1.5, 2.5], [3.5, 4.5]],
            "label_matrix": [["a", "b"], ["c", "d"]],
            "row_matrix": [[5.0, 6.0], [7.0, 8.0]],
            "expr": "2[mm]",
            "choice": "fast",
            "enabled": False,
        }
        self.allowed = {"choice": ["fast", "safe"], "enabled": ["on", "off"]}
        self.set_calls: list[tuple[str, dict]] = []
        self._getter_types = {
            "Boolean": "getBoolean",
            "BooleanArray": "getBooleanArray",
            "BooleanMatrix": "getBooleanMatrix",
            "Int": "getInt",
            "IntArray": "getIntArray",
            "IntMatrix": "getIntMatrix",
            "Double": "getDouble",
            "DoubleArray": "getDoubleArray",
            "DoubleMatrix": "getDoubleMatrix",
            "DoubleRowMatrix": "getDoubleMatrix",
            "String": "getString",
            "StringArray": "getStringArray",
            "StringMatrix": "getStringMatrix",
        }

    def properties(self) -> list[str]:
        return list(self.types)

    def getValueType(self, name: str) -> str:
        return self.types[name]

    def hasProperty(self, name: str) -> bool:
        return name in self.types

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        values = self.allowed.get(name)
        return None if values is None else list(values)

    def getType(self) -> str:
        return "MemoryPropFeature"

    def set(self, name: str, typed: dict) -> None:
        self.set_calls.append((name, deepcopy(typed)))
        self.values[name] = deepcopy(typed["data"])

    def __getattr__(self, method: str):
        if method in set(self._getter_types.values()):
            def get(name: str):
                expected = self._getter_types[self.types[name]]
                if method != expected:
                    raise TypeError(f"{method} does not match {self.types[name]}")
                return deepcopy(self.values[name])
            return get
        raise AttributeError(method)


class MemoryClient:
    def __init__(self, node: MemoryPropFeature) -> None:
        self.node = node

    def model(self, tag: str) -> MemoryPropFeature:
        if tag != "fake-model":
            raise KeyError(tag)
        return self.node


class MemoryWorker:
    def __init__(self, node: MemoryPropFeature) -> None:
        self._client = MemoryClient(node)

    def client(self) -> MemoryClient:
        return self._client


class TypedPropertyProductionFakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node = MemoryPropFeature()
        self.worker = MemoryWorker(self.node)

    def test_get_preserves_scalar_array_and_matrix_type_and_shape(self) -> None:
        expected = {
            "flag": ("boolean", [], True, "boolean"),
            "count": ("int32", [], 7, "int"),
            "gain": ("float64", [], 1.25, "double"),
            "label": ("string", [], "literal", "java.lang.String"),
            "flag_empty": ("boolean", [0], [], "boolean[]"),
            "flag_one": ("boolean", [1], [True], "boolean[]"),
            "count_empty": ("int32", [0], [], "int[]"),
            "count_one": ("int32", [1], [3], "int[]"),
            "gain_empty": ("float64", [0], [], "double[]"),
            "gain_one": ("float64", [1], [2.5], "double[]"),
            "label_empty": ("string", [0], [], "java.lang.String[]"),
            "label_one": ("string", [1], ["single"], "java.lang.String[]"),
            "flag_matrix": ("boolean", [2, 2], [[True, False], [False, True]], "boolean[][]"),
            "count_matrix": ("int32", [2, 2], [[1, 2], [3, 4]], "int[][]"),
            "gain_matrix": ("float64", [2, 2], [[1.5, 2.5], [3.5, 4.5]], "double[][]"),
            "label_matrix": ("string", [2, 2], [["a", "b"], ["c", "d"]], "java.lang.String[][]"),
            "row_matrix": ("float64", [2, 2], [[5.0, 6.0], [7.0, 8.0]], "double[][]"),
        }
        result = property_get(self.worker, "fake-model", PATH, list(expected))
        observed = {item["name"]: item["value"] for item in result["properties"]}
        for name, (kind, shape, data, signature) in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    observed[name],
                    {"kind": kind, "shape": shape, "data": data, "java_signature": signature},
                )

    def test_set_preserves_typed_shapes_and_expression_unit_text(self) -> None:
        assignments = [
            {"name": "flag", "value": {"kind": "boolean", "shape": [], "data": False}},
            {"name": "count", "value": {"kind": "int32", "shape": [], "data": 9}},
            {"name": "gain", "value": {"kind": "float64", "shape": [], "data": 9.5}},
            {"name": "label", "value": {"kind": "string", "shape": [], "data": "new"}},
            {"name": "flag_empty", "value": {"kind": "boolean", "shape": [0], "data": []}},
            {"name": "flag_one", "value": {"kind": "boolean", "shape": [1], "data": [False]}},
            {"name": "count_empty", "value": {"kind": "int32", "shape": [0], "data": []}},
            {"name": "count_one", "value": {"kind": "int32", "shape": [1], "data": [8]}},
            {"name": "gain_empty", "value": {"kind": "float64", "shape": [0], "data": []}},
            {"name": "gain_one", "value": {"kind": "float64", "shape": [1], "data": [4.5]}},
            {"name": "label_empty", "value": {"kind": "string", "shape": [0], "data": []}},
            {"name": "label_one", "value": {"kind": "string", "shape": [1], "data": ["one"]}},
            {"name": "flag_matrix", "value": {"kind": "boolean", "shape": [2, 2], "data": [[False, True], [True, False]]}},
            {"name": "count_matrix", "value": {"kind": "int32", "shape": [2, 2], "data": [[5, 6], [7, 8]]}},
            {"name": "gain_matrix", "value": {"kind": "float64", "shape": [2, 2], "data": [[5.5, 6.5], [7.5, 8.5]]}},
            {"name": "label_matrix", "value": {"kind": "string", "shape": [2, 2], "data": [["e", "f"], ["g", "h"]]}},
            {"name": "row_matrix", "value": {"kind": "float64", "shape": [2, 2], "data": [[9.5, 10.5], [11.5, 12.5]]}},
            {"name": "expr", "value": {"kind": "expression", "shape": [], "data": "3[mm]", "unit": "mm"}},
        ]
        result = property_set(self.worker, "fake-model", PATH, assignments)
        self.assertTrue(result["success"], result)
        applied = {item["name"]: item for item in result["data"]["applied"]}
        self.assertEqual(len(applied), len(assignments))
        for assignment in assignments:
            name = assignment["name"]
            with self.subTest(name=name):
                self.assertTrue(applied[name]["readback_match"])
                self.assertEqual(applied[name]["readback"]["data"], assignment["value"]["data"])
                self.assertEqual(applied[name]["readback"]["shape"], assignment["value"]["shape"])
        self.assertEqual(applied["expr"]["value"]["unit"], "mm")
        self.assertEqual(applied["expr"]["readback"]["kind"], "string")
        self.assertEqual(applied["expr"]["readback"]["data"], "3[mm]")
        self.assertEqual(
            applied["expr"]["comparison"]["rule"],
            "exact_text_expression_string_mapping",
        )

    def test_allowed_values_are_exposed_and_checked_before_set(self) -> None:
        choice_schema = property_schema(self.worker, "fake-model", PATH, "choice")
        self.assertEqual(choice_schema["allowed_values"], ["fast", "safe"])
        boolean_schema = property_schema(self.worker, "fake-model", PATH, "enabled")
        self.assertEqual(boolean_schema["allowed_values"], ["on", "off"])

        valid = property_set(self.worker, "fake-model", PATH, [
            {"name": "choice", "value": {"kind": "string", "shape": [], "data": "safe"}},
            {"name": "enabled", "value": {"kind": "boolean", "shape": [], "data": True}},
        ])
        self.assertTrue(valid["success"], valid)

        rejected_node = MemoryPropFeature()
        rejected_worker = MemoryWorker(rejected_node)
        with self.assertRaises(ExecutionContractError) as caught:
            property_set(rejected_worker, "fake-model", PATH, [
                {"name": "choice", "value": {"kind": "string", "shape": [], "data": "outside"}},
            ])
        self.assertEqual(caught.exception.code, "INVALID_PROPERTY_VALUE")
        self.assertEqual(rejected_node.set_calls, [])


if __name__ == "__main__":
    unittest.main()
