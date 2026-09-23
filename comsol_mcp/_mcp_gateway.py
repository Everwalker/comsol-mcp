"""MCP transport adapter; engine execution belongs to the control service.

Legacy Python functions remain available for internal composition. Public MCP
calls always pass through this adapter, including the legacy tool names.
"""
from __future__ import annotations

import asyncio
import base64
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

    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        payload = {"success": False, "error": "Missing backend success status", "data": {}}

    def _delivery_error(code: str, message: str) -> CallToolResult:
        sc: dict[str, Any] = {"success": False, "error": code, "message": message}
        for k in ("execution", "job_id", "operation_id", "request_id", "model_ref"):
            if k in payload:
                sc[k] = payload[k]
        if "execution" in payload and isinstance(payload["execution"], dict):
            for k in ("job_id", "operation_id", "request_id", "model_ref"):
                if k in payload["execution"] and k not in sc:
                    sc[k] = payload["execution"][k]
        if isinstance(payload.get("data"), dict):
            for k in ("job_id", "operation_id", "artifact_ref", "file_path", "sha256", "solution", "solution_binding"):
                if k in payload["data"] and k not in sc:
                    sc[k] = payload["data"][k]
        return CallToolResult(
            content=[TextContent(type="text", text=f"{code}: {message}")],
            structuredContent=sc,
            isError=True,
        )

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

    has_unknown_or_cleanup_fail = bool(
        payload.get("execution_state_unknown")
        or payload.get("cleanup_failed")
        or (isinstance(data, dict) and (data.get("execution_state_unknown") or data.get("cleanup_failed")))
    )

    if image_b64 is not None:
        if mime_type != "image/png":
            return _delivery_error("UNSUPPORTED_IMAGE_FORMAT", f"Only PNG is supported, got {mime_type}")
        if len(image_b64) > 10 * 1024 * 1024:
            return _delivery_error("IMAGE_TOO_LARGE", "Base64 payload exceeds 10MB limit")
        try:
            raw_bytes = base64.b64decode(image_b64, validate=True)
        except Exception:
            return _delivery_error("IMAGE_CORRUPTED", "Failed to decode base64 image: invalid base64 characters or padding")

        # Full PNG verification (signature + strict chunk CRC + IDAT check)
        if not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(raw_bytes) < 33:
            return _delivery_error("IMAGE_CORRUPTED", "Invalid or truncated PNG header signature")

        import struct
        import zlib
        offset = 8
        n_bytes = len(raw_bytes)
        has_ihdr = False
        has_idat = False
        has_iend = False
        w, h = 0, 0
        while offset + 12 <= n_bytes:
            chunk_len = struct.unpack(">I", raw_bytes[offset:offset+4])[0]
            chunk_type = raw_bytes[offset+4:offset+8]
            if offset + 12 + chunk_len > n_bytes:
                return _delivery_error("IMAGE_CORRUPTED", f"Truncated PNG chunk {chunk_type.decode('latin1', 'replace')}")
            chunk_data = raw_bytes[offset+8:offset+8+chunk_len]
            crc = struct.unpack(">I", raw_bytes[offset+8+chunk_len:offset+12+chunk_len])[0]
            calc_crc = zlib.crc32(raw_bytes[offset+4:offset+8+chunk_len])
            if crc != calc_crc:
                return _delivery_error("IMAGE_CORRUPTED", f"CRC mismatch in chunk {chunk_type.decode('latin1', 'replace')}")

            if chunk_type == b"IHDR":
                if has_ihdr:
                    return _delivery_error("IMAGE_CORRUPTED", "Multiple IHDR chunks in PNG")
                if offset != 8 or chunk_len != 13:
                    return _delivery_error("IMAGE_CORRUPTED", "Corrupted or misplaced IHDR chunk")
                w, h = struct.unpack(">II", chunk_data[:8])
                if w <= 0 or h <= 0:
                    return _delivery_error("INVALID_IMAGE_DIMENSIONS", f"Invalid dimensions {w}x{h}: must be positive integers")
                has_ihdr = True
            elif chunk_type == b"IDAT":
                if not has_ihdr:
                    return _delivery_error("IMAGE_CORRUPTED", "IDAT chunk appeared before IHDR")
                if chunk_len > 0:
                    has_idat = True
            elif chunk_type == b"IEND":
                if not has_ihdr:
                    return _delivery_error("IMAGE_CORRUPTED", "IEND chunk appeared before IHDR")
                if not has_idat:
                    return _delivery_error("IMAGE_CORRUPTED", "Incomplete PNG: missing IDAT image data before IEND")
                if offset + 12 != n_bytes:
                    return _delivery_error("IMAGE_CORRUPTED", "Trailing data found after IEND chunk")
                has_iend = True
                break
            offset += 12 + chunk_len

        if not (has_ihdr and has_idat and has_iend):
            return _delivery_error("IMAGE_CORRUPTED", "Incomplete PNG missing IHDR, IDAT, or IEND chunk")

        if w * h > 16 * 1024 * 1024:
            return _delivery_error("EXCESSIVE_PIXELS", f"Image dimensions {w}x{h} ({w*h} px) exceed 16M pixel limit")

        # Only return ImageContent for clean successful results (or explicit diagnostic mode)
        is_clean_success = bool(payload.get("success")) and not has_unknown_or_cleanup_fail
        is_diagnostic = bool(isinstance(data, dict) and data.get("diagnostic"))
        if is_clean_success or is_diagnostic:
            contents.append(ImageContent(type="image", data=image_b64, mimeType="image/png"))

    contents.append(TextContent(type="text", text=json.dumps(text_payload, ensure_ascii=False, default=str)))

    return CallToolResult(
        content=contents,
        structuredContent=text_payload,
        isError=not payload["success"] or has_unknown_or_cleanup_fail,
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
