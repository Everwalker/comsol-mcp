from __future__ import annotations

from pathlib import Path
import sys
import unittest
from collections.abc import Mapping


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT))
import comsol_mcp  # noqa: E402

from jsonschema import Draft202012Validator  # noqa: E402
from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._g2_contract import validate_typed_value  # noqa: E402
from comsol_mcp._g2_public_api import Capability, _typed_result  # noqa: E402
from comsol_mcp._g2_registry import validate_call  # noqa: E402
from comsol_mcp._g2_tools import registry_describe  # noqa: E402
from comsol_mcp._g3_w18 import plot_feature_create, plot_group_create, plot_update  # noqa: E402


CREATE_ROUTES = ("plot.group_create", "plot.feature_create")
BASE_ENVELOPE = {
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
}
NODE_PATH = {"segments": [{"collection": "plotGroup", "tag": "pg"}]}


def _wire_arguments(operation_id: str, properties):
    result = dict(BASE_ENVELOPE)
    result["properties"] = properties
    if operation_id == "plot.group_create":
        result.update({"tag": "newpg", "dimension": 2, "dataset": {"segments": [{"accessor": "dataset"}]}})
    elif operation_id == "plot.feature_create":
        result.update({"group": NODE_PATH, "tag": "surf", "type_id": "Surface"})
    elif operation_id == "plot.update":
        result["path"] = NODE_PATH
    return result


class _FakeNode:
    def __init__(self, tag, events):
        self.tag = tag
        self.events = events
        self.features = _FakeFeatureList(events)

    def set(self, key, value):
        self.events.append(("set", self.tag, key, value))

    def setEntry(self, key, entry, value):
        self.events.append(("setEntry", self.tag, key, entry, value))

    def feature(self):
        return self.features


class _FakeFeatureList:
    def __init__(self, events):
        self.events = events
        self.nodes = {}

    def create(self, tag, type_id):
        self.events.append(("create_feature", tag, type_id))
        node = _FakeNode(tag, self.events)
        self.nodes[tag] = node
        return node

    def get(self, tag):
        return self.nodes[tag]


class _FakeResults:
    def __init__(self, events):
        self.events = events
        self.nodes = {"pg": _FakeNode("pg", events)}

    def create(self, tag, type_id):
        self.events.append(("create_group", tag, type_id))
        node = _FakeNode(tag, self.events)
        self.nodes[tag] = node
        return node

    def get(self, tag):
        return self.nodes[tag]


class _FakeModel:
    def __init__(self, events):
        self.results = _FakeResults(events)

    def result(self):
        return self.results


class _FakeClient:
    def __init__(self, model):
        self._model = model

    def model(self, model_tag):
        return self._model


class _FakeWorker:
    def __init__(self):
        self.events = []
        self._model = _FakeModel(self.events)
        self._client = _FakeClient(self._model)

    def client(self):
        return self._client


def _run_create_handler(operation_id, properties):
    wire = _wire_arguments(operation_id, properties)
    validate_call(operation_id, wire)
    worker = _FakeWorker()
    if operation_id == "plot.group_create":
        handler_args = {"tag": "newpg", "dimension": 2, "properties": properties}
        result = plot_group_create(worker, "m", handler_args)
    else:
        handler_args = {"group": "pg", "tag": "surf", "type_id": "Surface", "properties": properties}
        result = plot_feature_create(worker, "m", handler_args)
    return worker, result


def _schema(operation_id):
    result = registry_describe(operation_id)
    if not result["success"]:
        raise AssertionError(result)
    return result["data"]["input_schema"]


class PlotPropertiesCompatibilityTests(unittest.TestCase):
    def test_public_draft_schema_and_registry_admission_match_create_contract(self):
        accepted = ([], {}, {"expr": "T", "linecolor": "blue"}, None)
        rejected = ([{"name": "expr", "value": "T"}], "invalid", False)
        for operation_id in CREATE_ROUTES:
            schema = _schema(operation_id)
            Draft202012Validator.check_schema(schema)
            self.assertEqual(registry_describe(operation_id)["data"]["effect"], "WRITE")
            self.assertEqual(registry_describe(operation_id)["data"]["scope"], "model")
            validator = Draft202012Validator(schema)
            for value in accepted:
                with self.subTest(operation=operation_id, value=value):
                    self.assertTrue(validator.is_valid(_wire_arguments(operation_id, value)))
                    validate_call(operation_id, _wire_arguments(operation_id, value))
            for value in rejected:
                with self.subTest(operation=operation_id, rejected=value):
                    self.assertFalse(validator.is_valid(_wire_arguments(operation_id, value)))
                    with self.assertRaises(ExecutionContractError) as caught:
                        validate_call(operation_id, _wire_arguments(operation_id, value))
                    self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_create_handlers_keep_empty_array_null_and_mapping_on_fresh_fake_models(self):
        for operation_id in CREATE_ROUTES:
            for properties in ([], None, {}, {"expr": "T"}):
                with self.subTest(operation=operation_id, properties=properties):
                    worker, result = _run_create_handler(operation_id, properties)
                    self.assertTrue(any(event[0].startswith("create_") for event in worker.events))
                    property_writes = [event for event in worker.events if event[0] in {"set", "setEntry"}]
                    if isinstance(properties, Mapping) and properties:
                        self.assertTrue(property_writes)
                    else:
                        self.assertEqual(property_writes, [])
                    self.assertEqual(result["properties"], dict(properties) if isinstance(properties, Mapping) else {})

    def test_nonempty_create_array_is_refused_before_fake_handler_can_change_model(self):
        bad = [{"name": "expr", "value": "T"}]
        for operation_id in CREATE_ROUTES:
            with self.subTest(operation=operation_id):
                worker = _FakeWorker()
                with self.assertRaises(ExecutionContractError) as caught:
                    validate_call(operation_id, _wire_arguments(operation_id, bad))
                self.assertEqual(caught.exception.code, "INVALID_REQUEST")
                self.assertEqual(worker.events, [])

    def test_plot_update_stays_mapping_only_and_matches_require_mapping(self):
        schema = _schema("plot.update")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for properties in ({}, {"expr": "T"}):
            args = _wire_arguments("plot.update", properties)
            self.assertTrue(validator.is_valid(args))
            validate_call("plot.update", args)
        for properties in ([], None, [{"name": "expr", "value": "T"}]):
            args = _wire_arguments("plot.update", properties)
            self.assertFalse(validator.is_valid(args))
            with self.assertRaises(ExecutionContractError) as caught:
                validate_call("plot.update", args)
            self.assertEqual(caught.exception.code, "INVALID_REQUEST")
            worker = _FakeWorker()
            with self.assertRaises(ExecutionContractError) as handler_error:
                plot_update(worker, "m", {"path": "pg", "properties": properties})
            self.assertEqual(handler_error.exception.code, "INVALID_REQUEST")
            self.assertEqual(worker.events, [])
        worker = _FakeWorker()
        plot_update(worker, "m", {"path": "pg", "properties": {"expr": "T"}})
        self.assertIn(("set", "pg", "expr", "T"), worker.events)

    def test_typed_unit_positive_negative_controls_and_api_numeric_output(self):
        numeric_null = {"kind": "float64", "shape": [], "data": 3.0, "unit": None}
        numeric_text = {"kind": "float64", "shape": [], "data": 3.0, "unit": "mm"}
        expression_text = {"kind": "expression", "shape": [], "data": "3", "unit": "mm"}
        string_text = {"kind": "string", "shape": [], "data": "length", "unit": "mm"}
        literal_without_field = {"kind": "expression", "shape": [], "data": "3[mm]"}
        typed_schema = _schema("node.property_set")
        Draft202012Validator.check_schema(typed_schema)
        common_defs = typed_schema["$defs"]["comsol_common"]
        probe = {"$schema": typed_schema["$schema"], "$defs": typed_schema["$defs"],
                 "$ref": "#/$defs/comsol_common/TypedValue"}
        validator = Draft202012Validator(probe)
        for value in (numeric_null, expression_text, string_text, literal_without_field):
            self.assertTrue(validator.is_valid(value), value)
            self.assertEqual(validate_typed_value(value)["data"], value["data"])
        self.assertIn("TypedValue", common_defs)
        self.assertFalse(validator.is_valid(numeric_text))
        with self.assertRaises(ExecutionContractError) as caught:
            validate_typed_value(numeric_text)
        self.assertEqual(caught.exception.code, "PROPERTY_TYPE_MISMATCH")

        api_schema = _schema("api.invoke")
        Draft202012Validator.check_schema(api_schema)
        api_args = dict(BASE_ENVELOPE)
        api_args.update({"path": NODE_PATH, "method": "getValue", "arguments": [numeric_null],
                         "declared_effect": "READ"})
        self.assertTrue(Draft202012Validator(api_schema).is_valid(api_args))

        capability = Capability("com.example.Model", "getValue", (), "double", "READ",
                                return_kind="float64", return_rank=0)
        output = _typed_result(3.0, capability)
        self.assertEqual(output["kind"], "value")
        self.assertEqual(output["value"]["data"], 3.0)
        self.assertNotIn("unit", output["value"])


if __name__ == "__main__":
    unittest.main()
