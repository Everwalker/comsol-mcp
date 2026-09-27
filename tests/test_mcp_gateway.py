import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

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


def test_w20_tool_schema_no_kwargs():
    from comsol_mcp._tools_w20 import validate_expressions, validate_solution, validate_report
    server = FastMCP("gateway-test")
    registry = GatewayRegistry(server, lambda op, args, exec: {"success": True, "data": {}})
    for name, fn in [
        ("validate.expressions", validate_expressions),
        ("validate.solution", validate_solution),
        ("validate.report", validate_report),
    ]:
        registry.add_tool(fn, name=name)
        tool = server._tool_manager._tools[name]
        assert "kwargs" not in tool.parameters.get("properties", {}), f"'kwargs' found in {name} properties"
        assert "kwargs" not in tool.parameters.get("required", []), f"'kwargs' found in {name} required"


def test_real_stdio_call_validate_expressions(tmp_path):
    import sys
    import textwrap
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _stdio_test():
        # Keep this protocol test independent of loopback permissions and any
        # COMSOL/control process. The production MCP server, schemas, registry
        # pagination implementation, stdio framing and file-only logging remain
        # real; only the control-client boundary is replaced in the child.
        child_server = textwrap.dedent("""
            from comsol_mcp import _control_client
            from comsol_mcp._execution_contract import ExecutionContractError
            from comsol_mcp._g2_registry import registry_list

            def dispatch(operation, arguments, execution):
                if operation == "registry_list":
                    try:
                        return {"success": True, "data": registry_list(
                            domain=arguments.get("domain") or None,
                            cursor=arguments.get("cursor") or None,
                            limit=arguments.get("limit", 100),
                        )}
                    except ExecutionContractError as exc:
                        return {"success": False, "error": exc.as_dict(), "data": {}}
                return {
                    "success": False,
                    "data": {"status": "PARTIAL", "applied": ["one"], "not_executed": ["two"]},
                    "error": {"code": "PARTIAL_FAILURE", "message": "fixture failure"},
                    "execution": {"operation": operation},
                }

            _control_client.dispatch = dispatch
            from comsol_mcp.mcp_server import main
            main()
        """)
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-c", child_server],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "COMSOL_SERVER_MCP_HOME": str(tmp_path),
                 "COMSOL_MCP_TOOL_PROFILE": "full"},
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                assert init_result is not None
                tools = await session.list_tools()
                expr_tool = next((t for t in tools.tools if t.name == "validate.expressions"), None)
                assert expr_tool is not None
                registry_tool = next((t for t in tools.tools if t.name == "registry_list"), None)
                assert registry_tool is not None
                assert "kwargs" not in expr_tool.inputSchema.get("properties", {})
                assert "kwargs" not in expr_tool.inputSchema.get("required", [])

                # Registry listing is a no-engine MCP call and proves the cursor
                # survives tools/call serialization across real stdio pages.
                first_page = await session.call_tool("registry_list", arguments={"limit": 1})
                assert first_page.isError is False
                assert isinstance(first_page.structuredContent, dict)
                first_data = first_page.structuredContent["data"]
                assert len(first_data["operations"]) == 1
                assert first_data["next_cursor"] == "1"
                second_page = await session.call_tool(
                    "registry_list", arguments={"limit": 1, "cursor": first_data["next_cursor"]}
                )
                second_data = second_page.structuredContent["data"]
                assert second_page.isError is False
                assert len(second_data["operations"]) == 1
                assert second_data["operations"][0]["operation_id"] != first_data["operations"][0]["operation_id"]
                assert second_data["next_cursor"] == "2"

                invalid_page = await session.call_tool("registry_list", arguments={"limit": 0})
                assert invalid_page.isError is True
                assert invalid_page.structuredContent["success"] is False

                call_res = await session.call_tool("validate.expressions", arguments={"expressions": ["1+1"]})
                # A partial business failure remains an error over stdio and
                # keeps its structured execution evidence.
                assert call_res is not None
                assert call_res.isError is True
                assert isinstance(call_res.structuredContent, dict)
                assert call_res.structuredContent["data"]["status"] == "PARTIAL"
                assert call_res.structuredContent["data"]["not_executed"] == ["two"]
                assert call_res.structuredContent["execution"]["operation"] == "validate.expressions"

    asyncio.run(_stdio_test())


def test_opt_in_real_stdio_to_task_owned_control_listener(tmp_path):
    """Exercise real MCP stdio -> authenticated control RPC using a temporary fixture.

    This test is opt-in because some managed sandboxes prohibit loopback bind.
    It starts one task-owned control daemon with no COMSOL configuration, uses
    only registry reads, and terminates that exact child before returning.
    """
    if os.environ.get("COMSOL_MCP_RUN_LOOPBACK_TEST") != "1":
        import pytest
        pytest.skip("requires the explicitly enabled task-owned loopback fixture")

    import sys
    import textwrap
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "fixture-home"
    control_home = home / "control-private"
    control_home.mkdir(parents=True)
    daemon_log = tmp_path / "control-daemon.log"
    receipt_path = tmp_path / "loopback_receipt.json"
    endpoint_path = control_home / "control.json"
    command = [sys.executable, "-m", "comsol_mcp._control_daemon", "--home", str(control_home)]
    child_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "isolated-home"),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": str(root),
        "COMSOL_SERVER_MCP_HOME": str(home),
        "COMSOL_MCP_TOOL_PROFILE": "full",
    }
    Path(child_env["HOME"]).mkdir()
    process = None
    endpoint = None
    protocol_checks = {"initialized": False, "registry_pages": 0, "invalid_limit_is_error": False}
    cleanup = {"exact_child_exited": False, "loopback_listener_unreachable": False}
    status = "FAIL"

    try:
        with daemon_log.open("wb") as stream:
            process = subprocess.Popen(
                command, cwd=root, env=child_env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            )

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"task-owned control daemon exited early: {process.returncode}")
            try:
                endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
        assert endpoint is not None, "task-owned control endpoint did not appear"
        assert endpoint.get("pid") == process.pid
        assert type(endpoint.get("port")) is int and 1 <= endpoint["port"] <= 65535
        assert isinstance(endpoint.get("token"), str) and endpoint["token"]

        async def _stdio_test():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "comsol_mcp.mcp_server"],
                env=child_env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    protocol_checks["initialized"] = init is not None
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    assert "registry_list" in names

                    first = await session.call_tool("registry_list", arguments={"limit": 1})
                    assert first.isError is False
                    first_data = first.structuredContent["data"]
                    assert first_data["next_cursor"] == "1"
                    second = await session.call_tool(
                        "registry_list", arguments={"limit": 1, "cursor": first_data["next_cursor"]}
                    )
                    assert second.isError is False
                    second_data = second.structuredContent["data"]
                    assert len(second_data["operations"]) == 1
                    assert second_data["operations"][0]["operation_id"] != first_data["operations"][0]["operation_id"]
                    assert second_data["next_cursor"] == "2"
                    protocol_checks["registry_pages"] = 2

                    invalid = await session.call_tool("registry_list", arguments={"limit": 0})
                    assert invalid.isError is True
                    assert invalid.structuredContent["success"] is False
                    protocol_checks["invalid_limit_is_error"] = True

        asyncio.run(_stdio_test())
        status = "PASS"
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            cleanup["exact_child_exited"] = process.poll() is not None
        if endpoint is not None:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with socket.socket() as probe:
                    probe.settimeout(0.2)
                    if probe.connect_ex(("127.0.0.1", endpoint["port"])) != 0:
                        cleanup["loopback_listener_unreachable"] = True
                        break
                time.sleep(0.05)
        receipt = {
            "schema": "comsol-mcp-w26-loopback-protocol-fixture/1",
            "status": status if all(cleanup.values()) else "FAIL_CLEANUP",
            "scope": "one temporary control daemon; registry_list only; no COMSOL runtime configured",
            "daemon": {
                "pid": process.pid if process is not None else None,
                "argv0": "python -m comsol_mcp._control_daemon",
                "endpoint_host": "127.0.0.1" if endpoint else None,
                "port": endpoint.get("port") if endpoint else None,
                "token_recorded": False,
            },
            "protocol": protocol_checks,
            "cleanup": cleanup,
            "prohibited_actions": {
                "comsol_engine_started": False,
                "model_loaded_or_saved": False,
                "study_or_solver_run": False,
                "gui_used": False,
            },
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    assert all(cleanup.values()), f"temporary control cleanup did not verify: {cleanup}"
    assert status == "PASS"
