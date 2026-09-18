import asyncio
import json

from mcp.server.fastmcp import FastMCP

from comsol_mcp._mcp_gateway import GatewayRegistry, mcp_result


def test_business_failure_has_outer_error_and_preserves_partial_evidence():
    payload = {"success": False, "data": {"applied": ["a"], "not_executed": ["c"]}, "error": "b failed"}
    result = mcp_result(json.dumps(payload))
    assert result.isError is True
    assert result.structuredContent == payload
    assert json.loads(result.content[0].text) == payload


def test_invalid_backend_result_fails_closed():
    for payload in ("not json", "[]", {"data": "no success"}, {"success": "false"}):
        assert mcp_result(payload).isError is True


def test_legacy_tool_schema_and_real_dispatch_route():
    calls = []
    def legacy_write(name: str, value: str = "1") -> str:
        raise AssertionError("MCP transport must not execute engine code")
    def backend(operation, arguments, execution):
        calls.append((operation, arguments, execution))
        return {"success": True, "data": {"revision": 8}}
    server = FastMCP("gateway-test")
    registry = GatewayRegistry(server, backend)
    registry.add_tool(legacy_write)
    tool = server._tool_manager._tools["legacy_write"]
    assert tool.parameters["required"] == ["name"]
    assert set(tool.parameters["properties"]) == {"name", "value", "execution"}
    result = asyncio.run(tool.fn(name="a", execution={"expected_revision": 7}))
    assert not result.isError
    assert calls == [("legacy_write", {"name": "a", "value": "1"}, {"expected_revision": 7})]


def test_transport_failure_does_not_leak_exception_secrets():
    def function() -> str:
        return "unused"
    def broken(*args):
        raise RuntimeError("password=private-secret")
    server = FastMCP("gateway-test")
    GatewayRegistry(server, broken).add_tool(function)
    result = asyncio.run(server._tool_manager._tools["function"].fn())
    assert result.isError
    assert "private-secret" not in result.model_dump_json()
    assert result.structuredContent["error"]["safe_retry"] is False
