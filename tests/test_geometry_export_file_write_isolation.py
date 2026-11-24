from __future__ import annotations

import unittest
from pathlib import Path

from comsol_mcp._execution_contract import (
    ExecutionContractError, SessionLedger, permission_for_effect, permission_for_legacy_tool,
)
from comsol_mcp._g2_registry import BY_ID, validate_call
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, REQUIRES_ISOLATION
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend, _G3_EFFECT_MAP


class _ContextVarStub:
    def get(self, default=None):
        return default


class _FakeService:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = []
        self.callback_invoked = False

    def execute_legacy(self, tool_name, callback, arguments, **kwargs):
        # Route decision only: retain but never invoke the domain callback.
        self.calls.append({"tool_name": tool_name, "callback": callback,
                           "arguments": arguments, **kwargs})
        return {"success": False, "data": {}, "execution": {}}


class _SyntheticBackend(ManagedBackend):
    def __init__(self, service):
        self.service = service
        self.worker = object()
        self._stage_native_context = _ContextVarStub()
        self.isolation_calls = 0

    def _require_g2_isolation(self):
        self.isolation_calls += 1
        return {"synthetic_isolation": True}

    def _require_external_execution_authority(self, *args, **kwargs):
        return None


class GeometryExportIsolationTests(unittest.TestCase):
    def _invoke(self, operation):
        ledger = SessionLedger("synthetic-session", "synthetic-server")
        ref = ledger.bind_model("synthetic-model", ownership="mcp_owned")
        service = _FakeService(ledger)
        backend = _SyntheticBackend(service)
        execution = {
            "project_id": "synthetic-project",
            "session_id": ledger.session_id,
            "request_id": "synthetic-request",
            "expected_revision": 0,
        }
        result = backend._invoke_g3_model(
            operation, ref, {}, execution, "synthetic-operation", ledger.session_id
        )
        self.assertEqual(len(service.calls), 1)
        self.assertFalse(service.callback_invoked)
        return backend, service.calls[0], result

    def test_geometry_export_is_registered_file_write_and_isolation_required(self):
        entry = BY_ID["geometry.export"]
        self.assertEqual(entry.gate, "G5")
        self.assertEqual(entry.effect, "FILE_WRITE")
        self.assertTrue(entry.as_dict()["executable"])
        call_arguments = {
            "project_id": "synthetic-project",
            "session_id": "synthetic-session",
            "model_ref": {"schema_version": 1, "session_id": "synthetic-session",
                          "server_instance_id": "synthetic-server", "model_tag": "synthetic-model",
                          "generation": 1},
            "expected_revision": 0, "idempotency_key": "synthetic-idem",
            "geometry": {}, "format": "mphtxt", "destination": "geometry.mphtxt",
        }
        self.assertEqual(validate_call("geometry.export", call_arguments).operation_id, "geometry.export")
        self.assertIn("geometry.export", DISPATCH)
        self.assertIn("geometry.export", REQUIRES_ISOLATION)
        self.assertEqual(EFFECTS["geometry.export"], "FILE_WRITE")

        backend, call, result = self._invoke("geometry.export")
        self.assertEqual(backend.isolation_calls, 1)
        self.assertEqual(call["effect"], "file_write")
        self.assertEqual(result["data"]["isolation_proof"], {"synthetic_isolation": True})

    def test_read_keeps_read_route_without_isolation(self):
        self.assertEqual(EFFECTS["selection.list"], "READ")
        backend, call, result = self._invoke("selection.list")
        self.assertEqual(backend.isolation_calls, 0)
        self.assertEqual(call["effect"], "inspect")
        self.assertNotIn("isolation_proof", result.get("data", {}))

    def test_other_non_read_effects_still_require_isolation(self):
        representatives = {
            "geometry.sequence_create": "WRITE",
            "result.evaluate": "EVALUATE",
            "study.run": "COMPUTE",
            "result.numerical_manage": "DYNAMIC",
            "code.recipe_register": "STATE_WRITE",
            "code.recipe_run": "TRUSTED_CODE",
        }
        for operation, catalog_effect in representatives.items():
            with self.subTest(operation=operation):
                self.assertIn(operation, DISPATCH)
                self.assertEqual(EFFECTS[operation], catalog_effect)
                backend, call, result = self._invoke(operation)
                self.assertEqual(backend.isolation_calls, 1)
                self.assertEqual(call["effect"], _G3_EFFECT_MAP[catalog_effect])
                self.assertEqual(result["data"]["isolation_proof"], {"synthetic_isolation": True})

    def test_geometry_export_permission_remains_project_write(self):
        self.assertEqual(permission_for_effect("file_write"), "project_write")
        self.assertEqual(permission_for_legacy_tool("geometry_export"), "project_write")

    def test_execution_service_denies_file_write_without_project_write(self):
        ledger = SessionLedger("synthetic-session", "synthetic-server")
        ref = ledger.bind_model("synthetic-model", ownership="mcp_owned")
        ledger.permissions = {"inspect"}
        service = ExecutionService(ledger, object(), project_root=Path(__file__).parent)
        callback_called = False

        def callback(_arguments):
            nonlocal callback_called
            callback_called = True
            return {"success": True}

        with self.assertRaises(ExecutionContractError) as raised:
            service.execute_legacy(
                "geometry_export", callback, {}, model_ref=ref, expected_revision=0,
                session_id=ledger.session_id, effect="file_write",
            )
        self.assertEqual(raised.exception.code, "PERMISSION_DENIED")
        self.assertFalse(callback_called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
