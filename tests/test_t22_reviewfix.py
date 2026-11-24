"""Fresh pure-fake and AST controls for the isolated T22 review fixes."""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
sys.path.insert(0, str(SOURCE))

from comsol_mcp import _g3_w15  # noqa: E402
from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._execution_service import ExecutionService  # noqa: E402
from comsol_mcp._g3_ops import EFFECTS, execution_effect  # noqa: E402


_UNSET = object()
MATERIAL_PATH = {
    "segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "material", "tag": "mat1"},
    ]
}


class FakeGroup:
    def __init__(self, inputs=(), *, first_value=_UNSET, first_error=None):
        self.inputs = list(inputs)
        self.first_value = first_value
        self.first_error = first_error
        self.input_reads = 0
        self.description = "old"
        self.writes: list[tuple[str, object]] = []

    def input(self):
        self.input_reads += 1
        if self.input_reads == 1:
            if self.first_error is not None:
                raise self.first_error
            if self.first_value is not _UNSET:
                return self.first_value
        return list(self.inputs)

    def addInput(self, name):
        self.writes.append(("addInput", name))
        self.inputs.append(name)

    def removeInput(self, name):
        self.writes.append(("removeInput", name))
        self.inputs.remove(name)

    def descr(self, *args):
        if args:
            self.writes.append(("descr", args[0]))
            self.description = args[0]
        return self.description

    def getType(self):
        return "Common"

    def properties(self):
        return []


class FakeGroupList:
    def __init__(self, group):
        self.group = group

    def tags(self):
        return []


class FakeMaterial:
    def __init__(self, inputs=(), *, first_value=_UNSET, first_error=None):
        self.group = FakeGroup(inputs, first_value=first_value, first_error=first_error)
        self.groups = FakeGroupList(self.group)

    def getType(self):
        return "Material"

    def materialType(self):
        return "Common"

    def propertyGroup(self, *args):
        return self.groups if not args else self.group


class T22ReviewfixTests(unittest.TestCase):
    def invoke_group_update(self, material, definition):
        with patch.object(_g3_w15, "_material_lookup", return_value=(material, MATERIAL_PATH)):
            return _g3_w15.material_group_manage(
                object(),
                "fake-bound-model",
                {"path": MATERIAL_PATH, "action": "update", "group": "def", "definition": definition},
            )

    def _write_spies(self):
        calls = []

        def payload(*args, **kwargs):
            calls.append("property-payload")
            return []

        def write(*args, **kwargs):
            calls.append("property-write")
            return {"applied": [], "failed": [], "not_executed": [],
                    "execution_state_unknown": False, "readback_values": {}, "engine_error": None}

        return calls, patch.object(_g3_w15, "_property_payload", side_effect=payload), \
            patch.object(_g3_w15, "_property_write", side_effect=write)

    def test_structured_unknown_pre_read_blocks_all_update_writes(self):
        material = FakeMaterial(
            inputs=["Cp"],
            first_error=ExecutionContractError("EXECUTION_STATE_UNKNOWN", "fake structured unknown"),
        )
        write_calls, payload_patch, write_patch = self._write_spies()
        with payload_patch, write_patch:
            result = self.invoke_group_update(
                material,
                {"description": "new", "inputs": {"add": ["T"]}, "properties": {"rho": "1"}},
            )
        self.assertEqual(result["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertFalse(result["partial_change"])
        self.assertTrue(result["execution_state_unknown"])
        self.assertFalse(result["failed"][0]["partial_change"])
        self.assertEqual(material.group.writes, [])
        self.assertEqual(material.group.inputs, ["Cp"])
        self.assertEqual(material.group.description, "old")
        self.assertEqual(write_calls, [])
        self.assertEqual(material.group.input_reads, 1)

    def test_unavailable_and_invalid_input_pre_reads_refuse_before_writes(self):
        cases = (
            (FakeMaterial(inputs=["Cp"], first_error=ExecutionContractError("METHOD_REJECTED", "no allowlist entry")),
             "METHOD_REJECTED"),
            (FakeMaterial(inputs=["Cp"], first_value="Cp"), "API_UNSUPPORTED"),
            (FakeMaterial(inputs=["Cp"], first_value=["Cp", None]), "API_UNSUPPORTED"),
            (FakeMaterial(inputs=["Cp"], first_value=["Cp", "Cp"]), "API_UNSUPPORTED"),
            (FakeMaterial(inputs=["Cp"], first_value=[""]), "API_UNSUPPORTED"),
        )
        for material, expected_code in cases:
            with self.subTest(expected_code=expected_code, first_value=material.group.first_value):
                write_calls, payload_patch, write_patch = self._write_spies()
                with payload_patch, write_patch:
                    result = self.invoke_group_update(
                        material,
                        {"description": "new", "inputs": {"add": ["T"]}, "properties": {"rho": "1"}},
                    )
                self.assertEqual(result["failed"][0]["error"]["code"], expected_code)
                self.assertFalse(result["partial_change"])
                self.assertEqual(material.group.writes, [])
                self.assertEqual(write_calls, [])
                self.assertEqual(material.group.input_reads, 1)

    def test_valid_pre_read_also_gates_description_only_updates(self):
        material = FakeMaterial(inputs=["Cp"], first_value=("Cp",))
        result = self.invoke_group_update(material, {"description": "updated"})
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(material.group.input_reads, 1)
        self.assertEqual(material.group.writes, [("descr", "updated")])

    @staticmethod
    def _service_error(tool_name, arguments, *, permissions=("inspect",),
                       effect=None, session_id="session"):
        service = ExecutionService.__new__(ExecutionService)
        service.ledger = SimpleNamespace(session_id="session", permissions=set(permissions))
        service.project_root = ROOT
        try:
            service.execute_legacy(
                tool_name,
                lambda args: {"success": True, "data": {}},
                arguments,
                session_id=session_id,
                effect=effect,
            )
        except ExecutionContractError as exc:
            return exc.code
        return "RETURNED"

    def test_only_server_resolved_exact_inspect_reaches_session_read_permission(self):
        for alias in ("material_group_manage", "physics_multiphysics_manage"):
            with self.subTest(alias=alias):
                self.assertEqual(
                    self._service_error(alias, {"action": "inspect"}, effect="inspect"),
                    "MODEL_IDENTITY_MISMATCH",
                )
                self.assertEqual(
                    self._service_error(alias, {"action": "inspect"}),
                    "MODEL_IDENTITY_MISMATCH",
                )
                self.assertEqual(
                    self._service_error(alias, {"action": "update"}),
                    "PERMISSION_DENIED",
                )

    def test_client_fields_or_invalid_actions_cannot_forge_inspect_permission(self):
        for arguments in (
            {},
            {"action": "update"},
            {"action": "Inspect"},
            {"action": {"nested": "inspect"}},
            {"action": "update", "effect": "inspect"},
            {"action": "update", "_server_resolved_g3_inspect": True},
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    self._service_error("material_group_manage", arguments),
                    "PERMISSION_DENIED",
                )
        self.assertEqual(
            self._service_error("material_group_manage", {"action": "update"}, effect="inspect"),
            "PERMISSION_DENIED",
        )
        self.assertEqual(
            self._service_error("some_other_tool", {"action": "inspect"}, effect="inspect"),
            "PERMISSION_DENIED",
        )
        self.assertEqual(
            self._service_error("material_group_manage", {"action": "inspect"},
                                effect="inspect", session_id="wrong-session"),
            "MODEL_IDENTITY_MISMATCH",
        )
        self.assertEqual(self._service_error("api_invoke", {}, effect="inspect"), "MODEL_IDENTITY_MISMATCH")
        self.assertEqual(self._service_error("node_label_set", {}, effect="inspect"), "PERMISSION_DENIED")

    def test_action_resolver_and_direct_registry_routes_are_fail_closed(self):
        for operation in ("material.group_manage", "physics.multiphysics_manage"):
            self.assertEqual(EFFECTS[operation], "DYNAMIC")
            self.assertEqual(execution_effect(operation, {"action": "inspect"}), "READ")
            for arguments in (
                {},
                {"action": "update"},
                {"action": "Inspect"},
                {"action": {"nested": "inspect"}},
                {"action": "update", "effect": "READ"},
            ):
                self.assertEqual(execution_effect(operation, arguments), "DYNAMIC")

        backend_path = SOURCE / "comsol_mcp/_managed_backend.py"
        backend_text = backend_path.read_text()
        backend_tree = ast.parse(backend_text)
        methods = {node.name: node for node in ast.walk(backend_tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        invoke_source = ast.get_source_segment(backend_text, methods["invoke"])
        g2_source = ast.get_source_segment(backend_text, methods["_invoke_g2_model"])
        g3_source = ast.get_source_segment(backend_text, methods["_invoke_g3_model"])
        self.assertIn("self.invoke(entry.operation_id, call_args", invoke_source)
        self.assertIn("self._invoke_g3_model(operation, ref, body", g2_source)
        self.assertIn("execution_effect(operation, body)", g3_source)
        self.assertIn("session_id=session, effect=effect", g3_source)
        self.assertIn("_require_external_execution_authority(", g3_source)
        self.assertIn('project_id=execution.get("project_id")', g3_source)
        self.assertIn("session_id=session", g3_source)

    def test_catalog_tensor_adapter_and_t21_close_writes_remain_present(self):
        package_catalog = SOURCE / "comsol_mcp/data/g2/02_ACTION_CATALOG.json"
        design_catalog = SOURCE / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json"
        self.assertEqual(package_catalog.read_bytes(), design_catalog.read_bytes())
        for path in (package_catalog, design_catalog):
            parsed = json.loads(path.read_text())
            entries = [item for item in parsed["operations"] if item.get("operation_id") in
                       {"material.group_manage", "physics.multiphysics_manage"}]
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(item.get("effect") == "DYNAMIC" for item in entries))

        w15 = (SOURCE / "comsol_mcp/_g3_w15.py").read_text()
        self.assertIn("records=tensor_records", w15)
        self.assertIn("_material_tensor_readback_checks", w15)
        self.assertIn('"tensor_adapter"', w15)

        backend = (SOURCE / "comsol_mcp/_managed_backend.py").read_text()
        self.assertIn('"FILE_WRITE": "file_write"', backend)
        self.assertIn('terminal_model_close=(operation == "model.close")', backend)
        self.assertIn('"model.close"', backend)


if __name__ == "__main__":
    unittest.main()
