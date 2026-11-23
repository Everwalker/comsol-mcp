from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from comsol_mcp._mcp_public_validation import create_public_server, run_stdio
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._tools_w18 import register as register_w18
import comsol_mcp.mcp_server as entry


class FastMCPGatewayParityTests(unittest.IsolatedAsyncioTestCase):
    def make_servers(self, dispatcher):
        fastmcp = FastMCP("t29-parity")
        gateway = GatewayRegistry(fastmcp, dispatcher=dispatcher)
        register_w18(gateway)
        return fastmcp, create_public_server(fastmcp, gateway.dispatcher)

    async def call(self, server, name, arguments):
        async with create_connected_server_and_client_session(server) as client:
            return await client.call_tool(name, arguments=arguments)

    async def test_raw_business_failure_matches_original_gateway_is_error(self):
        failure = {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": "rejected by fake dispatcher"},
            "data": {},
        }
        fastmcp, protocol = self.make_servers(lambda *_: failure)

        original = await self.call(fastmcp, "plot.render", {"path": "pg"})
        raw = await self.call(protocol, "plot.render", {
            "path": "pg", "unknown_root_field": "observed",
        })

        self.assertEqual(raw.structuredContent, original.structuredContent)
        self.assertEqual(raw.isError, original.isError)
        self.assertIs(raw.isError, True)

    async def test_raw_dispatch_exception_matches_gateway_sanitization(self):
        secret = "PRIVATE_ENDPOINT_TOKEN=t29-secret"

        def fail(*_args):
            raise RuntimeError(secret)

        fastmcp, protocol = self.make_servers(fail)
        original = await self.call(fastmcp, "plot.render", {"path": "pg"})
        raw = await self.call(protocol, "plot.render", {
            "path": "pg", "unknown_root_field": "observed",
        })

        self.assertEqual(raw.structuredContent, original.structuredContent)
        self.assertIs(raw.isError, True)
        self.assertEqual(raw.structuredContent["error"]["code"], "CONTROL_SERVICE_UNAVAILABLE")
        self.assertNotIn(secret, str(raw.structuredContent))
        self.assertNotIn(secret, "".join(part.text for part in raw.content if hasattr(part, "text")))

    async def test_dispatch_keeps_heartbeat_live_and_blocking_negative_control_detects_stall(self):
        def slow_dispatcher(*_args):
            time.sleep(0.2)
            return {"success": True, "data": {"done": True}}

        fastmcp, protocol = self.make_servers(slow_dispatcher)

        async def max_heartbeat_gap(action):
            stop = asyncio.Event()
            gaps = []

            async def heartbeat():
                previous = time.monotonic()
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                    current = time.monotonic()
                    gaps.append(current - previous)
                    previous = current

            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            try:
                await action()
            finally:
                stop.set()
                await beat
            return max(gaps, default=0.0)

        normal_gap = await max_heartbeat_gap(
            lambda: self.call(fastmcp, "plot.render", {"path": "pg"}),
        )
        raw_gap = await max_heartbeat_gap(
            lambda: self.call(protocol, "plot.render", {
                "path": "pg", "unknown_root_field": "observed",
            }),
        )

        async def blocking_negative_control():
            time.sleep(0.2)

        blocked_gap = await max_heartbeat_gap(blocking_negative_control)
        self.assertLess(normal_gap, 0.12, normal_gap)
        self.assertLess(raw_gap, 0.12, raw_gap)
        self.assertGreater(blocked_gap, 0.16, blocked_gap)

    async def test_both_aliases_call_before_tools_list(self):
        calls = []

        def dispatcher(operation, arguments, execution):
            calls.append((operation, dict(arguments), dict(execution)))
            return {"success": True, "data": {"routed": operation}}

        _fastmcp, protocol = self.make_servers(dispatcher)
        async with create_connected_server_and_client_session(protocol) as client:
            for name in ("plot.render", "plot_render"):
                result = await client.call_tool(name, arguments={
                    "path": "pg", "unknown_root_field": "observed",
                })
                self.assertIs(result.structuredContent["success"], True)

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0] == "plot.render" for call in calls))
        self.assertTrue(all(call[1]["unknown_root_field"] == "observed" for call in calls))

    async def test_resources_templates_and_prompts_still_use_fastmcp_routes(self):
        fastmcp = FastMCP("t29-resources")

        @fastmcp.resource("test://status")
        def status_resource() -> str:
            return "resource-value"

        @fastmcp.resource("test://items/{name}")
        def item_resource(name: str) -> str:
            return f"item:{name}"

        @fastmcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        protocol = create_public_server(fastmcp, lambda *_: {"success": True, "data": {}})

        async def snapshot(server):
            async with create_connected_server_and_client_session(server) as client:
                resources = await client.list_resources()
                templates = await client.list_resource_templates()
                prompts = await client.list_prompts()
                content = await client.read_resource("test://status")
                prompt = await client.get_prompt("greeting", {"name": "Ada"})
                return resources, templates, prompts, content, prompt

        self.assertEqual(await snapshot(protocol), await snapshot(fastmcp))

    async def test_non_plot_tool_still_uses_gateway_dispatch(self):
        calls = []

        def dispatcher(operation, arguments, execution):
            calls.append((operation, dict(arguments), dict(execution)))
            return {"success": True, "data": {"routed": operation}}

        with patch.object(entry._gateway, "dispatcher", dispatcher):
            protocol = entry.build_mcp_protocol_server()
            result = await self.call(protocol, "server_info", {"execution": {}})

        self.assertIs(result.structuredContent["success"], True)
        self.assertEqual(calls, [("server_info", {}, {})])

    async def test_run_stdio_uses_mcp_130_public_server_api(self):
        fastmcp = FastMCP("t29-stdio")
        protocol = create_public_server(fastmcp, lambda *_: {"success": True, "data": {}})
        self.assertIsInstance(protocol, Server)
        self.assertEqual(
            protocol.create_initialization_options().capabilities,
            fastmcp._mcp_server.create_initialization_options().capabilities,
        )
        actual_protocol = entry.build_mcp_protocol_server()
        self.assertEqual(
            actual_protocol.create_initialization_options().capabilities,
            entry.mcp._mcp_server.create_initialization_options().capabilities,
        )

        read_stream = object()
        write_stream = object()

        @asynccontextmanager
        async def fake_stdio_server():
            yield read_stream, write_stream

        run = AsyncMock()
        with patch("comsol_mcp._mcp_public_validation.stdio_server", fake_stdio_server):
            with patch.object(protocol, "run", run):
                await run_stdio(protocol)

        run.assert_awaited_once_with(
            read_stream,
            write_stream,
            protocol.create_initialization_options(),
        )

        with patch("comsol_mcp.mcp_server._setup_logging"), \
                patch("comsol_mcp.mcp_server._write_status"), \
                patch("comsol_mcp.mcp_server.anyio.run") as anyio_run:
            entry.main()
        args = anyio_run.call_args.args
        self.assertIs(args[0], run_stdio)
        self.assertIsInstance(args[1], Server)


if __name__ == "__main__":
    unittest.main()
