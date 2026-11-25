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


def test_stdio_job_readers_use_file_store_and_project_scope(tmp_path):
    """Real stdio frames and SQLite; the control connection is in-process and synthetic."""
    import base64
    import hashlib
    import textwrap
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    child = textwrap.dedent("""
        import json, os
        from pathlib import Path
        from comsol_mcp._control_daemon import ControlDaemon
        from comsol_mcp import _control_client

        root = Path(os.environ['T35_FIXTURE_ROOT'])
        projects = root / 'projects'
        projects.mkdir()
        daemon = ControlDaemon(root / 'control', project_root=projects, registry={})
        def project(label):
            reply = daemon.dispatch({'operation': 'project.create', 'arguments': {
                'label': label, 'workspace': label,
                'policy': {'permissions': ['inspect', 'project_write']}},
                'execution': {'request_id': label, 'idempotency_key': label}})
            assert reply['success'], reply
            return reply['data']['project']['project_id']
        owner, other = project('owner'), project('other')
        job, reused = daemon.store.begin(request_id='fixture-request', idempotency_key='fixture-key',
            request_hash='fixture-hash', operation='synthetic.fixture',
            metadata={'project_id': owner, 'execution': {'project_id': owner}})
        assert not reused
        daemon.store.add_event(job['job_id'], 'FIXTURE', {'message': 'synthetic-event-界'})
        event = daemon.store.db.execute("SELECT id,metadata FROM job_events WHERE job_id=? AND event='FIXTURE'",
                                        (job['job_id'],)).fetchone()
        result = {'success': True, 'payload': 'synthetic-result'}
        assert daemon.store.finish(job['operation_id'], status='SUCCEEDED', result=result)[0]
        (root / 'fixture.json').write_text(json.dumps({'owner': owner, 'other': other,
            'job': job['job_id'], 'event': event[0], 'metadata': event[1], 'result': result}))
        def dispatch(operation, arguments, execution):
            return daemon.dispatch({'operation': operation, 'arguments': arguments, 'execution': execution})
        _control_client.dispatch = dispatch
        from comsol_mcp.mcp_server import main
        try:
            main()
        finally:
            daemon.close()
    """)

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-c", child], env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "COMSOL_SERVER_MCP_HOME": str(tmp_path / "mcp-home"),
            "COMSOL_MCP_TOOL_PROFILE": "full", "T35_FIXTURE_ROOT": str(tmp_path),
        })
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                fixture = json.loads((tmp_path / "fixture.json").read_text())
                owner = {"project_id": fixture["owner"]}
                event_args = {"job_id": fixture["job"], "event_id": fixture["event"],
                              "field": "metadata", "limit": 8, "execution": owner}
                first = await session.call_tool("job_log_event_read", arguments=event_args)
                assert first.isError is False, first
                page = first.structuredContent["data"]
                second = await session.call_tool("job_log_event_read", arguments={
                    **event_args, "offset": page["next_offset"], "field_sha256": page["sha256"], "limit": 1024})
                assert second.isError is False, second
                raw = base64.b64decode(page["chunk_base64"]) + base64.b64decode(second.structuredContent["data"]["chunk_base64"])
                assert raw == fixture["metadata"].encode()
                assert hashlib.sha256(raw).hexdigest() == page["sha256"]
                assert page["trusted_terminal_status"] == "SUCCEEDED"

                chunks, offset, digest = [], 0, None
                while True:
                    args = {"job_id": fixture["job"], "offset": offset, "limit": 8, "execution": owner}
                    if digest is not None:
                        args["result_sha256"] = digest
                    reply = await session.call_tool("job_result_read", arguments=args)
                    assert reply.isError is False, reply
                    data = reply.structuredContent["data"]
                    digest = data["result_sha256"]
                    chunks.append(base64.b64decode(data["chunk_base64"]))
                    offset = data["next_offset"]
                    if data["complete"]:
                        break
                result_raw = b"".join(chunks)
                assert hashlib.sha256(result_raw).hexdigest() == digest
                assert json.loads(result_raw) == fixture["result"]

                log = await session.call_tool("operation_call", arguments={
                    "operation_id": "solver.log_read", "arguments": {
                        "project_id": fixture["owner"], "job_id": fixture["job"], "limit": 10},
                    "execution": owner})
                assert log.isError is False, log
                assert log.structuredContent["data"]["job_status"] == "SUCCEEDED"
                assert any(row["event"] == "FIXTURE" for row in log.structuredContent["data"]["events"])

                foreign = await session.call_tool("job_log_event_read", arguments={
                    **event_args, "execution": {"project_id": fixture["other"]}})
                assert foreign.isError is True
                assert foreign.structuredContent["error"]["code"] == "NODE_NOT_FOUND"
                assert "synthetic-event" not in str(foreign.structuredContent)
                invalid = await session.call_tool("job_log_event_read", arguments={**event_args, "field": "private_column"})
                assert invalid.isError is True

    asyncio.run(exercise())


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


def test_real_stdio_public_docs_route_through_registry_call(tmp_path):
    """Public MCP stdio -> registry_call -> catalog validation -> docs actions.

    The MCP transport, published tool schemas, catalog input validation and
    the OfflineDocsIndex are real; only the control-client boundary is
    replaced in the child (same shape as the existing registry stdio test).
    The corpus is a synthetic fixture, not copied commercial documentation.
    """
    import sys
    import textwrap
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    corpus = tmp_path / 'Applications' / 'COMSOL64' / 'Multiphysics' / 'doc'
    corpus.mkdir(parents=True)
    (corpus / 'guide.md').write_text(
        '# PropFeature\nIllegal temperature value example tutorial\n', encoding='utf-8')

    async def _stdio_test():
        child_server = textwrap.dedent(f'''
            from pathlib import Path
            from comsol_mcp import _control_client
            from comsol_mcp._execution_contract import ExecutionContractError
            from comsol_mcp._g2_registry import registry_describe, validate_call
            from comsol_mcp._managed_backend import ManagedBackend

            backend = ManagedBackend(Path({str(tmp_path)!r}), object(), registry={{}},
                                     project_root=Path({str(tmp_path)!r}))

            def dispatch(operation, arguments, execution):
                try:
                    if operation in {{"registry_call", "registry.call"}}:
                        operation_id = arguments.get("operation_id", "")
                        inner = dict(arguments.get("arguments") or {{}})
                        validate_call(operation_id, inner)
                        return backend._invoke_g2_control(operation_id, inner, {{}}, "stdio-docs")
                    if operation in {{"registry_describe", "registry.describe"}}:
                        return {{"success": True, "data": registry_describe(arguments.get("operation_id", ""))}}
                except ExecutionContractError as exc:
                    return {{"success": False, "error": exc.as_dict(), "data": {{}}}}
                return {{"success": True, "data": {{}}}}

            _control_client.dispatch = dispatch
            from comsol_mcp.mcp_server import main
            main()
        ''')
        server_params = StdioServerParameters(
            command=sys.executable, args=['-c', child_server],
            env={'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
                 'COMSOL_SERVER_MCP_HOME': str(tmp_path),
                 'COMSOL_MCP_TOOL_PROFILE': 'full'},
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                assert (await session.initialize()) is not None
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {'registry_call', 'registry_describe'}.issubset(names)

                # Public introspection of the docs contract: the published
                # input schema keeps product and node_type dimensions apart.
                described = await session.call_tool('registry_describe',
                                                    arguments={'operation_id': 'docs.search'})
                assert described.isError is False
                props = described.structuredContent['data']['input_schema']['properties']
                assert 'product' in props and 'node_type' not in props

                def call(operation_id, inner):
                    return session.call_tool('registry_call',
                                             arguments={'operation_id': operation_id, 'arguments': inner})

                indexed = await call('docs.index', {'project_id': 'proj', 'idempotency_key': 't16-public',
                                                    'runtime_id': 'COMSOL64', 'sources': [str(corpus)]})
                assert indexed.isError is False
                index_data = indexed.structuredContent['data']
                assert index_data['version'] == '6.4' and index_data['indexed_count'] >= 1

                found = await call('docs.search', {'project_id': 'proj',
                                                   'query': 'Illegal temperature tutorial', 'version': '6.4'})
                assert found.isError is False
                results = found.structuredContent['data']['results']
                assert found.structuredContent['data']['status'] == 'SUCCEEDED'
                assert 'illegal temperature' in results[0]['snippet'].lower()

                absent = await call('docs.search', {'project_id': 'proj', 'query': 'phantom zzz term',
                                                    'version': '6.4'})
                assert absent.isError is False
                assert absent.structuredContent['data']['status'] == 'NOT_FOUND'
                unindexed = await call('docs.search', {'project_id': 'proj', 'query': 'Illegal',
                                                       'version': '6.3'})
                assert unindexed.structuredContent['data']['status'] == 'UNAVAILABLE'

                # A node_type is not a legal docs.search dimension; the public
                # envelope must refuse it with the catalog INVALID_REQUEST.
                wrong = await call('docs.search', {'project_id': 'proj', 'query': 'Illegal',
                                                   'version': '6.4', 'node_type': 'PropFeature'})
                assert wrong.isError is True
                assert 'INVALID_REQUEST' in json.dumps(wrong.structuredContent)

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
