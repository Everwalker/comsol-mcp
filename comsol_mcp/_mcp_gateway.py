"""MCP transport adapter; engine execution belongs to the control service.

Legacy Python functions remain available for internal composition. Public MCP
calls always pass through this adapter, including the legacy tool names.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, get_type_hints

from mcp.types import CallToolResult, ImageContent, TextContent

from ._g2_registry import current_tool_profile, is_tool_published


def mcp_result(value: str | dict[str, Any]) -> CallToolResult:
    """Preserve the business envelope and expose failures to MCP clients."""
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except (ValueError, TypeError):
            payload = {"success": False, "error": "Invalid backend result", "data": {}}
    else:
        payload = value

    if isinstance(payload, dict) and "success" not in payload:
        payload = {"success": True, "data": payload}
    elif not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        payload = {"success": False, "error": "Missing backend success status", "data": {}}

    contents: list[TextContent | ImageContent] = []
    data = payload.get("data")
    image_b64 = None
    mime_type = "image/png"
    if isinstance(data, dict):
        if "image_base64" in data and isinstance(data["image_base64"], str) and data["image_base64"]:
            image_b64 = data["image_base64"]
            mime_type = str(data.get("image_mime_type") or "image/png")
            text_payload = json.loads(json.dumps(payload))
            text_payload["data"]["image_base64"] = f"<embedded base64 image ({len(image_b64)} chars)>"
        else:
            text_payload = payload
    else:
        text_payload = payload

    if image_b64 is not None:
        contents.append(ImageContent(type="image", data=image_b64, mimeType=mime_type))

    contents.append(TextContent(type="text", text=json.dumps(text_payload, ensure_ascii=False, default=str)))

    return CallToolResult(
        content=contents,
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
        # Validate the deployment choice once at host startup.  Narrow modes
        # affect only tools/list publication; the daemon keeps the complete
        # registry and explicit operation fallback available.
        self.profile = current_tool_profile()
        self.functions: dict[str, Callable] = {}

    def add_tool(self, function: Callable, **options: Any) -> None:
        operation = options.get("name") or function.__name__
        if not is_tool_published(operation, profile=self.profile):
            return
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
