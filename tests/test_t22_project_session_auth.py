"""In-memory route-to-handler controls for T22 dynamic project/session effects."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
sys.path.insert(0, str(SOURCE))

from comsol_mcp._control_daemon import ControlDaemon  # noqa: E402
from comsol_mcp._execution_contract import (  # noqa: E402
    ExecutionContractError,
    SessionLedger,
)
from comsol_mcp._execution_service import ExecutionService  # noqa: E402
from comsol_mcp._g3_ops import DISPATCH, execution_effect  # noqa: E402
from comsol_mcp._managed_backend import ManagedBackend  # noqa: E402


MATERIAL_PATH = {
    "segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "material", "tag": "mat1"},
    ]
}
PROJECT_ID = "memory-project"
OPERATIONS = ("material.group_manage", "physics.multiphysics_manage")


class _Snapshot:
    def model_snapshot(self, model_tag):
        return {
            "model_tag": model_tag,
            "fingerprint": "memory-only-fingerprint",
            "external_event_counter": 0,
        }


class _ProjectAuthority:
    def __init__(self, permissions):
        self.permissions = set(permissions)
        self.calls = []

    def authorize_operation(self, project_id, permission):
        self.calls.append((project_id, permission))
        if project_id != PROJECT_ID or permission not in self.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", f"memory project lacks {permission}")
        return {"project_id": project_id}


class _BoundIdentity:
    @staticmethod
    def model_project_binding(_ref):
        return {"attribution": "PROJECT_BOUND", "project_id": PROJECT_ID}


class _FakeWorker:
    def operation_context(self, _operation_id, on_request_event=None):
        return nullcontext()


class _FakeStore:
    def list_metadata(self, _table):
        return []


class _NoneContext:
    @staticmethod
    def get(_default=None):
        return None


class _TestBackend(ManagedBackend):
    def _require_external_execution_authority(self, *_args, **_kwargs):
        return None

    def _require_g2_isolation(self):
        return {"memory_isolation": True}


class ProjectSessionAuthTests(unittest.TestCase):
    def _harness(self, project_permissions, session_permissions):
        ledger = SessionLedger("memory-session", "memory-server")
        ledger.permissions = set(session_permissions)
        ref = ledger.bind_model("memory-model", fingerprint="memory-only-fingerprint")
        service = ExecutionService(ledger, _Snapshot(), project_root=ROOT)

        backend = _TestBackend.__new__(_TestBackend)
        backend.service = service
        backend.worker = _FakeWorker()
        backend.store = _FakeStore()
        backend.project_root = ROOT
        backend._stage_native_context = _NoneContext()
        backend._stage_output_mode_context = _NoneContext()
        backend._stage_mesh_snapshot_context = _NoneContext()

        daemon = ControlDaemon.__new__(ControlDaemon)
        daemon.backend = _BoundIdentity()
        daemon.project_authority = _ProjectAuthority(project_permissions)
        execution = {
            "project_id": PROJECT_ID,
            "session_id": ledger.session_id,
            "model_ref": ref.as_dict(),
            "expected_revision": 0,
            "idempotency_key": "memory-idempotency",
            "request_id": "memory-request",
        }
        return daemon, backend, ledger, ref, execution

    @staticmethod
    def _body(operation, action, **extra):
        body = {"action": action, **extra}
        if operation == "material.group_manage":
            body.update({"path": MATERIAL_PATH, "group": "def"})
        return body

    @staticmethod
    def _route(route, operation, body):
        if route == "direct":
            return operation, body
        wrapper = "registry_call" if route == "registry" else "operation_call"
        return wrapper, {"operation_id": operation, "arguments": body}

    def _dispatch(self, daemon, backend, route, operation, body, execution):
        outer_operation, outer_arguments = self._route(route, operation, body)
        daemon._authorize_project_execution(outer_operation, outer_arguments, execution)
        with patch.dict(DISPATCH, {operation: self._handler}):
            return backend.invoke(
                outer_operation,
                outer_arguments,
                execution,
                "memory-request",
                lambda _event: None,
            )

    def setUp(self):
        self.handler_calls = []

    def _handler(self, worker, model_tag, arguments):
        self.handler_calls.append((model_tag, dict(arguments)))
        return {"status": "APPLIED", "action": arguments.get("action"), "memory_fake": True}

    def test_direct_and_registry_inspect_reach_handler_for_both_operations(self):
        for operation in OPERATIONS:
            for route in ("direct", "registry", "operation_call"):
                with self.subTest(operation=operation, route=route):
                    daemon, backend, _ledger, _ref, execution = self._harness(
                        {"inspect"}, {"inspect"},
                    )
                    body = self._body(operation, "inspect")
                    result = self._dispatch(daemon, backend, route, operation, body, execution)
                    self.assertTrue(result["success"])
                    self.assertEqual(len(self.handler_calls), 1)
                    self.assertEqual(self.handler_calls[-1][1]["action"], "inspect")
                    self.assertEqual(daemon.project_authority.calls, [(PROJECT_ID, "inspect")])
                    self.handler_calls.clear()

    def test_project_only_and_session_only_inspect_grants_do_not_bypass_either_gate(self):
        body = self._body("material.group_manage", "inspect")

        # Project-only inspect passes the project gate, then the real session
        # service refuses before the fake handler is entered.
        daemon, backend, _ledger, _ref, execution = self._harness({"inspect"}, set())
        with self.assertRaises(ExecutionContractError) as raised:
            self._dispatch(daemon, backend, "direct", "material.group_manage", body, execution)
        self.assertEqual(raised.exception.code, "PERMISSION_DENIED")
        self.assertEqual(daemon.project_authority.calls, [(PROJECT_ID, "inspect")])
        self.assertEqual(self.handler_calls, [])

        # Session-only inspect is refused by the project gate on the nested
        # registry route; the managed backend and handler are never called.
        daemon, backend, _ledger, _ref, execution = self._harness(set(), {"inspect"})
        with self.assertRaises(ExecutionContractError) as raised:
            self._dispatch(daemon, backend, "registry", "material.group_manage", body, execution)
        self.assertEqual(raised.exception.code, "PERMISSION_DENIED")
        self.assertEqual(daemon.project_authority.calls, [(PROJECT_ID, "inspect")])
        self.assertEqual(self.handler_calls, [])

    def test_valid_write_reaches_handler_only_with_project_and_session_write(self):
        operation = "material.group_manage"
        body = self._body(operation, "update", definition={"description": "memory-only"})
        daemon, backend, _ledger, _ref, execution = self._harness(
            {"project_write"}, {"project_write"},
        )
        result = self._dispatch(daemon, backend, "direct", operation, body, execution)
        self.assertTrue(result["success"])
        self.assertEqual(daemon.project_authority.calls, [(PROJECT_ID, "project_write")])
        self.assertEqual(self.handler_calls[0][1]["action"], "update")

    def test_default_invalid_and_forged_effect_actions_remain_project_write(self):
        operation = "material.group_manage"
        bodies = (
            self._body(operation, None),
            self._body(operation, "Inspect"),
            self._body(operation, {"nested": "inspect"}),
            self._body(operation, "update", effect="inspect"),
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(execution_effect(operation, body), "DYNAMIC")
                daemon, backend, _ledger, _ref, execution = self._harness({"inspect"}, {"inspect"})
                with self.assertRaises(ExecutionContractError) as raised:
                    self._dispatch(daemon, backend, "direct", operation, body, execution)
                self.assertEqual(raised.exception.code, "PERMISSION_DENIED")
                self.assertEqual(daemon.project_authority.calls, [(PROJECT_ID, "project_write")])
                self.assertEqual(self.handler_calls, [])

                # A request-body claim and even an internal effect keyword
                # cannot turn an update into an inspect-authorized operation.
                service = backend.service
                callback_calls = []
                with self.assertRaises(ExecutionContractError) as service_error:
                    service.execute_legacy(
                        "material_group_manage",
                        lambda args: callback_calls.append(args) or {"success": True, "data": {}},
                        body,
                        session_id=execution["session_id"],
                        effect="inspect",
                    )
                self.assertEqual(service_error.exception.code, "PERMISSION_DENIED")
                self.assertEqual(callback_calls, [])


if __name__ == "__main__":
    unittest.main()
