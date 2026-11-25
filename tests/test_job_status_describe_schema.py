import json
import unittest
from pathlib import Path

import comsol_mcp._g2_registry as registry
from comsol_mcp._g2_registry import (
    BY_ID,
    CATALOG_PATH,
    _ACTION_RESULT_SCHEMA,
    operation_describe,
)


def assert_known_properties_match(testcase, schema, value):
    testcase.assertIsInstance(value, dict)
    properties = schema["properties"]
    for name in schema.get("required", []):
        testcase.assertIn(name, value)
    for name, actual in value.items():
        rule = properties.get(name)
        if rule is None:  # additionalProperties defaults to true in this catalog schema
            continue
        expected_types = rule.get("type")
        if expected_types is not None:
            if isinstance(expected_types, str):
                expected_types = [expected_types]
            type_checks = {
                "object": lambda item: isinstance(item, dict),
                "string": lambda item: isinstance(item, str),
                "boolean": lambda item: type(item) is bool,
                "integer": lambda item: type(item) is int,
                "null": lambda item: item is None,
            }
            testcase.assertTrue(any(type_checks[kind](actual) for kind in expected_types))
        if "enum" in rule:
            testcase.assertIn(actual, rule["enum"])
        if "const" in rule:
            testcase.assertEqual(actual, rule["const"])
        if "minimum" in rule:
            testcase.assertGreaterEqual(actual, rule["minimum"])


class JobStatusDescribeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = json.loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
        entries = [item for item in catalog["operations"] if item["operation_id"] == "job.status"]
        if len(entries) != 1:
            raise AssertionError(f"expected one job.status catalog entry, found {len(entries)}")
        cls.catalog_schema = entries[0]["output_schema"]

    def test_catalog_schema_is_exposed_separately_from_action_result(self):
        described = operation_describe("job.status")
        self.assertEqual(described["catalog_output_schema"], self.catalog_schema)
        self.assertEqual(described["output_schema"], _ACTION_RESULT_SCHEMA)
        self.assertEqual(set(self.catalog_schema["required"]), {"job_id", "status"})
        self.assertNotIn("success", self.catalog_schema["properties"])
        self.assertNotIn("data", self.catalog_schema["properties"])

    def test_catalog_status_fields_and_optional_values(self):
        properties = self.catalog_schema["properties"]
        self.assertEqual(
            properties["status"]["enum"],
            ["QUEUED", "STARTING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST", "RECONCILING", "UNKNOWN"],
        )
        self.assertEqual(properties["operation"]["type"], ["string", "object"])
        self.assertEqual(properties["started_at"]["type"], ["string", "null"])
        self.assertEqual(properties["finished_at"]["type"], ["string", "null"])
        self.assertEqual(properties["result_available_via"]["const"], "job.result.read")

    def test_synthetic_inline_and_omitted_result_payloads(self):
        schema = operation_describe("job.status")["catalog_output_schema"]
        synthetic_responses = [
            {
                "success": True,
                "data": {
                    "job_id": "synthetic-inline",
                    "status": "SUCCEEDED",
                    "operation": {"operation": "study.solve", "status": "SUCCEEDED"},
                    "created_at": "2030-01-01T00:00:00Z",
                    "started_at": None,
                    "finished_at": "2030-01-01T00:00:01Z",
                    "metadata": {},
                    "result": {"success": True, "data": {}},
                },
            },
            {
                "success": True,
                "data": {
                    "job_id": "synthetic-omitted",
                    "status": "RUNNING",
                    "operation": "study.solve",
                    "result_omitted": True,
                    "result_size_bytes": 4096,
                    "result_available_via": "job.result.read",
                },
            },
        ]
        for response in synthetic_responses:
            self.assertIs(type(response["success"]), bool)
            assert_known_properties_match(self, schema, response["data"])

    def test_other_operations_keep_generic_schema_only(self):
        described = operation_describe("job.list")
        self.assertNotIn("catalog_output_schema", described)
        self.assertEqual(described["output_schema"], _ACTION_RESULT_SCHEMA)


    def test_catalog_loader_is_status_scoped_and_preserves_other_data_schemas(self):
        self.assertEqual(BY_ID["job.status"].catalog_output_schema, self.catalog_schema)
        catalog = json.loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
        result_rows = [row for row in catalog["operations"] if row["operation_id"] == "job.result"]
        self.assertEqual(len(result_rows), 1)
        self.assertIn("output_schema", result_rows[0])
        self.assertIsNone(BY_ID["job.result"].catalog_output_schema)

        entries_by_id = {row["operation_id"]: row for row in catalog["operations"]}
        for operation_id in ("result.evaluate", "result.at_points"):
            with self.subTest(operation_id=operation_id):
                self.assertEqual(
                    operation_describe(operation_id).get("data_schema"),
                    entries_by_id[operation_id].get("data_schema"),
                )


if __name__ == "__main__":
    unittest.main()
