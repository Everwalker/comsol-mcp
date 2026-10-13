"""Public G2 registry/fallback MCP tools.

The model-scoped actions are intentionally available through the explicit
``registry_call`` fallback until each action has a stable host-facing schema.
This avoids advertising catalog placeholders as executable tools while still
giving static hosts a discoverable describe/call route.
"""
from __future__ import annotations

from typing import Any

from ._g2_registry import operation_describe as _operation_describe
from ._g2_registry import registry_describe as _describe, registry_list as _list, registry_manifest as _manifest, registry_search as _search


def registry_list(domain: str = "", cursor: str = "", limit: int = 100) -> dict[str, Any]:
    """List catalog actions with a continuation cursor and executable status."""
    return {"success": True, "data": _list(domain=domain or None, cursor=cursor or None, limit=limit)}


def registry_describe(operation_id: str) -> dict[str, Any]:
    """Describe one logical operation, including its JSON input schema."""
    return {"success": True, "data": _describe(operation_id)}


def registry_search(query: str, domain: str = "") -> dict[str, Any]:
    """Search catalog action purposes and names."""
    return {"success": True, "data": _search(query, domain=domain or None)}


def registry_manifest(profile: str = "full") -> dict[str, Any]:
    """Return the current profile's executable operation manifest."""
    return {"success": True, "data": _manifest(profile)}


def registry_call(operation_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one registry operation through the managed control service."""
    # The actual nested dispatch is performed by ManagedBackend.  Returning a
    # marker here keeps this function importable for registry collection and
    # prevents accidental direct engine access in the stdio process.
    return {"success": True, "data": {"operation_id": operation_id, "arguments": arguments}}


def operation_describe(operation_id: str) -> dict[str, Any]:
    """Compatibility alias for hosts that call the fallback operation API."""
    return {"success": True, "data": _operation_describe(operation_id)}


def operation_call(operation_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for registry_call."""
    return registry_call(operation_id, arguments)


def register(registry) -> None:
    for function in (registry_list, registry_describe, registry_search, registry_manifest, registry_call, operation_describe, operation_call):
        registry.add_tool(function)
