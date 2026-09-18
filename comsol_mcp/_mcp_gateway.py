"""MCP transport adapter; engine execution belongs to the control service.

Legacy Python functions remain available for internal composition. Public MCP
calls always pass through this adapter, including the legacy tool names.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, get_type_hints

from mcp.types import CallToolResult, TextContent


def mcp_result(value: str | dict[str, Any]) -> CallToolResult:
    """Preserve the business envelope and expose failures to MCP clients."""
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except (ValueError, TypeError):
            payload = {"success": False, "error": "Invalid backend result", "data": {}}
    else:
        payload = value
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        payload = {"success": False, "error": "Missing backend success status", "data": {}}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))],
        structuredContent=payload,
        isError=not payload["success"],
    )


def dispatch(operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    # A lazy import keeps discovery independent of engine installation/startup.
    from comsol_mcp._control_client import dispatch as control_dispatch
    return control_dispatch(operation, arguments, execution)


class GatewayRegistry:
    """Register legacy schemas with an additive execution contract.

    No JVM is started, nor any model inspected, by the transport process.
    The injected dispatcher is also useful for protocol-only tests.
    """

    def __init__(self, mcp: Any, dispatcher: Callable = dispatch):
        self.mcp = mcp
        self.dispatcher = dispatcher
        self.functions: dict[str, Callable] = {}

    def add_tool(self, function: Callable, **options: Any) -> None:
        operation = options.get("name") or function.__name__
        self.functions[operation] = function
        original = inspect.signature(function)
        hints = get_type_hints(function)
        parameters = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in original.parameters.values()]
        if "execution" in original.parameters:
            raise ValueError("Legacy function conflicts with execution contract")
        parameters.append(inspect.Parameter(
            "execution", inspect.Parameter.KEYWORD_ONLY,
            default=None, annotation=dict[str, Any] | None,
        ))

        async def routed(**kwargs: Any) -> CallToolResult:
            execution = kwargs.pop("execution", None) or {}
            bound = original.bind(**kwargs)
            bound.apply_defaults()
            try:
                result = await asyncio.to_thread(self.dispatcher, operation, dict(bound.arguments), execution)
            except Exception as exc:
                # Do not echo connection secrets, backend configuration or raw
                # transport exceptions. Details belong in protected service logs.
                result = {"success": False, "error": {
                    "code": "CONTROL_SERVICE_UNAVAILABLE", "type": type(exc).__name__,
                    "message": "Control service unavailable; reconcile before retrying writes.",
                    "safe_retry": False,
                }, "data": {}}
            return mcp_result(result)

        routed.__name__ = operation
        routed.__doc__ = (function.__doc__ or "") + (
            "\nExecution metadata supplies session_id, model_ref, expected_revision, "
            "idempotency_key and timeout policy. Writes require a current revision."
        )
        routed.__signature__ = inspect.Signature(parameters, return_annotation=CallToolResult)
        routed.__annotations__ = {p.name: p.annotation for p in parameters}
        routed.__annotations__["return"] = CallToolResult
        self.mcp.add_tool(routed, **options)
