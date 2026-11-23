from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._tools_w18 import register as register_w18


INVALID = [
    ("solution-null", {"solution": None}),
    ("solution-empty", {"solution": {}}),
    ("dataset-null", {"solution": {"dataset": None}}),
    ("dataset-empty", {"solution": {"dataset": ""}}),
    ("dataset-whitespace", {"solution": {"dataset": "   "}}),
    ("solution-empty-string", {"solution": {"dataset": "d1", "solution": ""}}),
    ("unknown-solution-key", {"solution": {"dataset": "d1", "inner": "first"}}),
    ("dataset-conflict", {"solution": {"dataset": "d1"}, "options": {"dataset": "d0"}}),
    ("data-alias-conflict", {"solution": {"dataset": "d1"}, "options": {"dataset": "d1", "data": "d0"}}),
    ("unmapped-options-solution", {"solution": {"dataset": "d1"}, "options": {"solution": "sol1"}}),
    ("options-null", {"options": None}),
]
VALID = [
    ("omitted", {"path": "pg"}),
    ("legacy-empty", {"path": "pg", "options": {}}),
    ("legacy-dataset", {"path": "pg", "options": {"dataset": "dlegacy"}}),
    ("legacy-data", {"path": "pg", "options": {"data": "dlegacy"}}),
    ("explicit", {"path": "pg", "solution": {"dataset": "dnew"}}),
    ("explicit-matching-legacy", {"path": "pg", "solution": {"dataset": "dnew"}, "options": {"dataset": "dnew"}}),
]


class FastMCPCombinedAdmission(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_parent = Path(tempfile.gettempdir()).resolve()
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_parent)
        root = Path(self.temp.name)
        projects = root / "projects"
        projects.mkdir()
        self.daemon = ControlDaemon(root / "control", project_root=projects, registry={})
        created = self.daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "combined-test", "workspace": "combined-test",
                          "policy": {"permissions": ["inspect", "project_write"]}},
            "execution": {"request_id": "create", "idempotency_key": "create"},
        })
        self.assertTrue(created["success"], created)
        self.project_id = created["data"]["project"]["project_id"]
        self.dispatches = []
        self.authorizations = []
        self.begins = []
        self.executions = []
        original_authorize = self.daemon._authorize_project_execution
        def authorize(*args, **kwargs):
            self.authorizations.append(args[0])
            return original_authorize(*args, **kwargs)
        self.daemon._authorize_project_execution = authorize
        original_begin = self.daemon.store.begin
        def begin(*args, **kwargs):
            self.begins.append(kwargs.get("operation"))
            return original_begin(*args, **kwargs)
        self.daemon.store.begin = begin
        def fake_execute(record, operation, arguments, execution, timeouts, submitted, *, resume_context=None):
            self.executions.append((operation, dict(arguments), dict(execution)))
            return self.daemon._finish(record, {"success": True, "data": {"fake": True}}, "SUCCEEDED")
        self.daemon._execute = fake_execute
        def dispatcher(operation, arguments, execution):
            self.dispatches.append((operation, dict(arguments), dict(execution)))
            return self.daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})
        self.server = FastMCP("t29-combined")
        register_w18(GatewayRegistry(self.server, dispatcher=dispatcher))

    async def asyncTearDown(self):
        self.daemon.close()
        self.temp.cleanup()

    def request(self, request_id, fields):
        return {**fields, "execution": {"project_id": self.project_id,
                "request_id": request_id, "idempotency_key": request_id}}

    async def test_invalid_requests_reach_auth_then_fail_before_begin(self):
        async with create_connected_server_and_client_session(self.server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertTrue({"plot.render", "plot_render"} <= names)
            before_jobs = self.daemon.store.list_jobs().total
            for public_name in ("plot.render", "plot_render"):
                for label, fields in INVALID:
                    request_id = f"bad-{public_name}-{label}"
                    result = await client.call_tool(public_name, arguments=self.request(request_id, {"path": "pg", **fields}))
                    payload = result.structuredContent
                    self.assertEqual(payload["error"]["code"], "INVALID_REQUEST", (public_name, label, payload))
            self.assertEqual(len(self.dispatches), len(INVALID) * 2)
            self.assertEqual(len(self.authorizations), len(INVALID) * 2)
            self.assertEqual(self.begins, [])
            self.assertEqual(self.daemon.store.list_jobs().total, before_jobs)
            self.assertEqual(self.executions, [])
            self.assertTrue(all(row[0] == "plot.render" for row in self.dispatches))

    async def test_valid_omitted_and_legacy_forms_keep_outer_identity(self):
        async with create_connected_server_and_client_session(self.server) as client:
            for public_name in ("plot.render", "plot_render"):
                for label, fields in VALID:
                    request_id = f"good-{public_name}-{label}"
                    result = await client.call_tool(public_name, arguments=self.request(request_id, fields))
                    self.assertIs(result.structuredContent["success"], True, (public_name, label, result.structuredContent))
            self.assertEqual(len(self.begins), len(VALID) * 2)
            self.assertEqual(len(self.executions), len(VALID) * 2)
            for operation, arguments, execution in self.dispatches:
                self.assertEqual(operation, "plot.render")
                self.assertEqual(execution["project_id"], self.project_id)
                self.assertEqual(execution["request_id"], execution["idempotency_key"])
                self.assertFalse({"project_id", "request_id", "idempotency_key"} & set(arguments))

    async def test_fastmcp_drops_unknown_root_fields(self):
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("plot.render", arguments=self.request(
                "root-extra", {"path": "pg", "unknown_root_field": "observed"}))
            self.assertIs(result.structuredContent["success"], True)
            self.assertNotIn("unknown_root_field", self.dispatches[-1][1])
            self.assertEqual(self.dispatches[-1][1], {"path": "pg"})

    async def test_unauthorized_invalid_input_returns_permission_denied_without_begin(self):
        root = Path(self.temp.name) / "inspect-only"
        root.mkdir()
        projects = root / "projects"
        projects.mkdir()
        daemon = ControlDaemon(root / "control", project_root=projects, registry={})
        created = daemon.dispatch({"operation": "project.create", "arguments": {
            "label": "inspect-only", "workspace": "inspect-only", "policy": {"permissions": ["inspect"]}},
            "execution": {"request_id": "create-readonly", "idempotency_key": "create-readonly"}})
        self.assertTrue(created["success"], created)
        project_id = created["data"]["project"]["project_id"]
        begins = []
        original_begin = daemon.store.begin
        def begin(*args, **kwargs):
            begins.append(kwargs.get("operation"))
            return original_begin(*args, **kwargs)
        daemon.store.begin = begin
        daemon._execute = lambda *args, **kwargs: self.fail("engine route reached")
        server = FastMCP("t29-readonly")
        def dispatcher(operation, arguments, execution):
            return daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})
        register_w18(GatewayRegistry(server, dispatcher=dispatcher))
        try:
            async with create_connected_server_and_client_session(server) as client:
                before_jobs = daemon.store.list_jobs().total
                result = await client.call_tool("plot.render", arguments={"path": "pg", "solution": None,
                    "execution": {"project_id": project_id, "request_id": "denied-invalid",
                                  "idempotency_key": "denied-invalid"}})
                self.assertEqual(result.structuredContent["error"]["code"], "PERMISSION_DENIED")
                self.assertEqual(begins, [])
                self.assertEqual(daemon.store.list_jobs().total, before_jobs)
        finally:
            daemon.close()


if __name__ == "__main__":
    unittest.main()
