from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from ._mcp_gateway import dispatch_mcp_call


_PLOT_TOOLS = {"plot.render", "plot_render"}


def create_public_server(
    fastmcp: FastMCP,
    dispatcher: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> Server:
    """Expose the registered FastMCP tools through MCP's public raw-call API."""
    server = Server("comsol-mcp-server")
    plot_properties: dict[str, set[str]] = {}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        registered = await fastmcp.list_tools()
        result = []
        for tool in registered:
            if tool.name in _PLOT_TOOLS:
                plot_properties[tool.name] = set(tool.inputSchema["properties"])
                schema = {**tool.inputSchema, "additionalProperties": False}
                tool = tool.model_copy(update={"inputSchema": schema})
            result.append(tool)
        return result

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        payload = arguments or {}
        if name in _PLOT_TOOLS and set(payload) - plot_properties[name]:
            raw_arguments = dict(payload)
            execution = raw_arguments.pop("execution", None) or {}
            return await dispatch_mcp_call(dispatcher, "plot.render", raw_arguments, execution)
        return await fastmcp.call_tool(name, payload)

    @server.list_resources()
    async def list_resources():
        return await fastmcp.list_resources()

    @server.read_resource()
    async def read_resource(uri: Any):
        return await fastmcp.read_resource(uri)

    @server.list_resource_templates()
    async def list_resource_templates():
        return await fastmcp.list_resource_templates()

    @server.list_prompts()
    async def list_prompts():
        return await fastmcp.list_prompts()

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None):
        return await fastmcp.get_prompt(name, arguments)

    return server


async def run_stdio(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
