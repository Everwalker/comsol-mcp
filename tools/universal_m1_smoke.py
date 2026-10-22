#!/usr/bin/env python3
"""Build and smoke-test the installed public MCP entrypoint for Unit A M1.

All generated files are written below the required, previously absent
``--run-dir``. The test starts the installed wheel's ``comsol-mcp`` console
script, performs MCP initialize/tools-list/schema calls, and routes one
``api.describe`` plus one read-only ``api.invoke`` through the real gateway
and ManagedBackend with a software-only Worker double. It never starts a JVM
or connects to COMSOL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import traceback


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    return {"command": command, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def _sitecustomize_source() -> str:
    return textwrap.dedent(r'''
        import atexit
        import os
        import runpy
        from pathlib import Path

        from comsol_mcp._control_client import __name__ as _control_client_module_name
        from comsol_mcp import _control_client
        from comsol_mcp._execution_contract import SessionLedger
        from comsol_mcp._execution_service import ExecutionService
        from comsol_mcp._g2_registry import operation_describe
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore

        _root = Path(os.environ["COMSOL_MCP_M1_RUN_DIR"])
        _fixture = runpy.run_path(os.environ["COMSOL_MCP_M1_FIXTURE"])
        _worker = _fixture["Worker"]()
        _worker.include_model_root_interface = True
        _store = OperationStore(_root / "software-worker.sqlite")
        _service = ExecutionService(SessionLedger("mcp-smoke-session", "server"),
                                    _worker, project_root=_root)
        _model_ref = _service.bind_model("m")['execution']['model_ref']
        _backend = ManagedBackend(_root, _store, service=_service, worker=_worker,
                                  project_root=_root, registry={})

        def _dispatch(operation, arguments, execution):
            if operation == "operation_describe":
                return {"success": True, "data": operation_describe(arguments.get("operation_id", ""))}
            return _backend.invoke(operation, arguments, execution, "mcp-wheel-smoke", None)

        _control_client.dispatch = _dispatch
        atexit.register(_store.close)
        atexit.register(_backend.docs_index.close)
    ''')


async def protocol_smoke(entrypoint: Path, run_dir: Path, package_site: Path,
                         dependency_site: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sitecustomize = run_dir / "sitecustomize.py"
    sitecustomize.write_text(_sitecustomize_source(), encoding="utf-8")
    server_env = {key: value for key, value in os.environ.items()
                  if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    server_env.update({
        "PYTHONPATH": os.pathsep.join((str(package_site), str(run_dir), str(dependency_site))),
        "COMSOL_MCP_M1_FIXTURE": str(ROOT / "tests" / "test_unit_a_node_public_api.py"),
        "COMSOL_MCP_M1_RUN_DIR": str(run_dir),
        "COMSOL_SERVER_MCP_HOME": str(run_dir / "mcp-home"),
        "COMSOL_MCP_TOOL_PROFILE": "full",
    })
    params = StdioServerParameters(command=str(entrypoint), args=[], env=server_env)
    report: dict = {"status": "FAIL", "native_comsol": "NOT_RUN"}
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize = await session.initialize()
            report["initialized"] = initialize is not None
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            report["tool_count"] = len(tools)
            report["required_tools_present"] = all(name in tools for name in ("operation_call", "operation_describe"))
            if not report["required_tools_present"]:
                raise AssertionError("installed tools/list omitted operation_call or operation_describe")
            call_schema = tools["operation_call"].inputSchema
            if not {"operation_id", "arguments", "execution"} <= set(call_schema.get("properties", {})):
                raise AssertionError("installed operation_call schema does not expose the actual MCP wire")
            report["operation_call_schema"] = call_schema

            described = await session.call_tool("operation_describe", arguments={"operation_id": "api.describe"})
            if described.isError or not isinstance(described.structuredContent, dict):
                raise AssertionError(f"api.describe operation schema MCP call failed: {described.structuredContent}")
            operation_schema = described.structuredContent["data"]
            if "path" not in operation_schema["input_schema"].get("properties", {}):
                raise AssertionError("api.describe input schema omitted NodePath")
            report["api_describe_schema"] = operation_schema

            execution = {
                "project_id": "mcp-smoke-project", "session_id": "mcp-smoke-session",
                "model_ref": {"schema_version": 1, "session_id": "mcp-smoke-session",
                              "server_instance_id": "server", "model_tag": "m", "generation": 1},
                "expected_revision": 0, "idempotency_key": "mcp-smoke-read", "request_id": "mcp-smoke-read",
            }
            root_path = {"segments": []}
            api_description = await session.call_tool("operation_call", arguments={
                "operation_id": "api.describe", "arguments": {"path": root_path}, "execution": execution,
            })
            if api_description.isError or not isinstance(api_description.structuredContent, dict):
                raise AssertionError(f"api.describe MCP route failed: {api_description.structuredContent}")
            description_data = api_description.structuredContent["data"]
            if (description_data.get("path") != root_path
                    or "com.comsol.model.Model" not in description_data.get("receiver_interfaces", [])):
                raise AssertionError("api.describe MCP result did not describe the bound Model root")
            report["api_describe_status"] = description_data.get("status")
            report["api_describe_root_interfaces"] = description_data["receiver_interfaces"]

            invoke_body = {"path": root_path, "method": "comments", "arguments": [], "declared_effect": "READ"}
            api_invoke = await session.call_tool("operation_call", arguments={
                "operation_id": "api.invoke", "arguments": invoke_body, "execution": execution,
            })
            if api_invoke.isError or not isinstance(api_invoke.structuredContent, dict):
                raise AssertionError(f"api.invoke MCP route failed: {api_invoke.structuredContent}")
            invoke_data = api_invoke.structuredContent["data"]
            if (invoke_data.get("operation") != "api.invoke"
                    or invoke_data.get("typed_return", {}).get("kind") != "value"
                    or invoke_data["typed_return"]["value"].get("data") != ""):
                raise AssertionError("api.invoke MCP route returned an unexpected typed result")
            report["api_invoke_status"] = invoke_data.get("status")
            report["api_invoke_typed_return"] = invoke_data["typed_return"]
            report["status"] = "PASS"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="new evidence directory; must not already exist")
    parser.add_argument("--builder", default=sys.executable, help="Python 3.12+ interpreter with the project build tools")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).expanduser()
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    run_dir = run_dir.resolve()
    if run_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing smoke evidence: {run_dir}")
    run_dir.mkdir(parents=True)
    # Preserve a venv's interpreter symlink. Resolving it drops the venv
    # prefix, site-packages, and any local build-backend dependencies.
    builder = str(Path(args.builder).expanduser().absolute())
    report = {"scope": "installed-wheel public MCP cold start and read-only M1 route",
              "native_comsol": "NOT_RUN", "run_dir": str(run_dir), "steps": []}
    try:
        wheelhouse = run_dir / "wheelhouse"
        wheelhouse.mkdir()
        build = run([builder, "-m", "pip", "wheel", "--no-build-isolation", "--no-deps", "--no-index",
                     "--wheel-dir", str(wheelhouse), "."], cwd=ROOT)
        report["steps"].append({"step": "build_wheel", **build})
        if build["exit_code"] != 0:
            raise RuntimeError("wheel build failed")
        wheels = sorted(wheelhouse.glob("comsol_mcp-*.whl"))
        if not wheels:
            raise RuntimeError("wheel build produced no comsol-mcp wheel")
        wheel = wheels[-1]

        runtime_env = run_dir / "runtime-venv"
        create_env = run([builder, "-m", "venv", str(runtime_env)], cwd=run_dir)
        report["steps"].append({"step": "create_clean_runtime", **create_env})
        if create_env["exit_code"] != 0:
            raise RuntimeError("clean runtime creation failed")
        runtime_python = runtime_env / "bin" / "python"
        install_prefix = run_dir / "installed-prefix"
        install = run([str(runtime_python), "-m", "pip", "install", "--no-deps", "--no-index",
                       "--prefix", str(install_prefix), str(wheel)], cwd=run_dir)
        report["steps"].append({"step": "install_built_wheel", **install})
        if install["exit_code"] != 0:
            raise RuntimeError("wheel install failed")
        entrypoint = install_prefix / "bin" / "comsol-mcp"
        package_file = next(install_prefix.rglob("comsol_mcp/__init__.py"), None)
        if not entrypoint.is_file() or package_file is None:
            raise RuntimeError("installed wheel is missing its package or comsol-mcp console entrypoint")
        package_site = package_file.parent.parent
        report["installed_package"] = str(package_file.parent)
        report["console_entrypoint"] = str(entrypoint)

        dependency_site = Path(builder).parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if not dependency_site.is_dir():
            # On framework and Homebrew Python layouts, ask the builder for its
            # exact purelib instead of guessing a platform-specific prefix.
            purelib = run([builder, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], cwd=run_dir)
            report["steps"].append({"step": "locate_dependency_runtime", **purelib})
            if purelib["exit_code"] != 0:
                raise RuntimeError("could not locate the dependency runtime")
            dependency_site = Path(purelib["stdout"].strip())
        if not dependency_site.is_dir():
            raise RuntimeError(f"dependency site-packages is absent: {dependency_site}")
        report["dependency_site"] = str(dependency_site)

        smoke = asyncio.run(asyncio.wait_for(
            protocol_smoke(entrypoint, run_dir, package_site, dependency_site), timeout=60))
        report["mcp_protocol"] = smoke
        report["status"] = "PASS" if smoke["status"] == "PASS" else "FAIL"
    except Exception as exc:
        report["status"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    (run_dir / "run_status.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_dir": str(run_dir),
                      "failure": report.get("failure"), "mcp_protocol": report.get("mcp_protocol")},
                     indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
