#!/usr/bin/env python3
"""Run a no-engine stdio MCP probe and preserve protocol evidence."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from jsonschema.validators import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUN_ID = os.environ.get("W01_RUN_ID", "manual")
CASE = ROOT / "evidence" / "w01" / "runs" / RUN_ID / "cases" / "T038_stdio_registry"


def dump(name: str, value: object) -> None:
    (CASE / name).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


async def probe() -> int:
    CASE.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["COMSOL_SERVER_MCP_HOME"] = str(CASE / "mcp_home")
    params = StdioServerParameters(command=sys.executable, args=["mcp_server.py"], env=env)
    request = {"transport": "stdio", "calls": ["initialize", "tools/list", "tools/call:server_info", "tools/call:get_parameters (unconnected)", "tools/call:does_not_exist"]}
    dump("environment.json", {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version, "cwd": str(ROOT), "engine": "NOT_RUN: no COMSOL action is authorized in W01"})
    dump("request.json", request)
    try:
        async with stdio_client(params, errlog=sys.stderr) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                listed = await session.list_tools()
                good = await session.call_tool("server_info", {})
                business_failure = await session.call_tool("get_parameters", {})
                missing = await session.call_tool("does_not_exist", {})
        schemas = [tool.inputSchema for tool in listed.tools]
        schema_errors = []
        for tool in listed.tools:
            try:
                Draft202012Validator.check_schema(tool.inputSchema)
            except Exception as exc:
                schema_errors.append({"tool": tool.name, "error": repr(exc)})
        assertions = {
            "initialize_completed": True,
            "protocol_version": init.protocolVersion,
            "tool_count_is_50": len(listed.tools) == 50,
            "all_tools_have_object_schema": all(schema.get("type") == "object" for schema in schemas),
            "draft_2020_12_schema_errors": schema_errors,
            "server_info_is_not_error": good.isError is False,
            "server_info_has_structured_content": good.structuredContent is not None,
            "unknown_tool_is_error": missing.isError is True,
            "legacy_business_failure_outer_is_error": business_failure.isError,
            "legacy_business_failure_isError_defect": business_failure.isError is False,
            "stdout_protocol_contamination": "NOT_DIRECTLY_OBSERVABLE_BY_CLIENT; server logs were retained on stderr, no decode failure occurred",
        }
        result = {"initialize": init.model_dump(mode="json"), "tools": [tool.model_dump(mode="json") for tool in listed.tools], "server_info": good.model_dump(mode="json"), "get_parameters_unconnected": business_failure.model_dump(mode="json"), "unknown_tool": missing.model_dump(mode="json")}
        dump("result.json", result)
        dump("assertions.json", assertions)
        (CASE / "engine.log").write_text("NOT_RUN: W01 validates MCP protocol only; it did not connect to or start COMSOL.\n", encoding="utf-8")
        required = (assertions["initialize_completed"], assertions["tool_count_is_50"], assertions["all_tools_have_object_schema"], not schema_errors, assertions["server_info_is_not_error"], assertions["server_info_has_structured_content"], assertions["unknown_tool_is_error"], assertions["legacy_business_failure_isError_defect"])
        return 0 if all(required) else 1
    except Exception as exc:
        dump("result.json", {"exception": repr(exc)})
        dump("assertions.json", {"probe_completed": False})
        (CASE / "engine.log").write_text("NOT_RUN: protocol probe failed before any COMSOL operation.\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
