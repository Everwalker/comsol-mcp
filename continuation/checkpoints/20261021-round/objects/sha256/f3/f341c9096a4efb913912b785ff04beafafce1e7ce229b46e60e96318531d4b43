"""Catalog-shaped Desktop wrappers routed through the durable control daemon."""
from __future__ import annotations

from typing import Any


def desktop_status(
    project_id: str,
    request_id: str = "",
    runtime_id: str = "",
) -> dict[str, Any]:
    """Observe current COMSOL Desktop window and platform capability state."""
    return {"project_id": project_id, "request_id": request_id, "runtime_id": runtime_id}


def desktop_bind(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    model_ref: str,
    request_id: str = "",
    verification: dict[str, Any] = {},  # The wrapper never mutates this catalog-empty default.
) -> dict[str, Any]:
    """Bind a freshly observed Desktop window to a managed ModelRef."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "model_ref": model_ref,
            "request_id": request_id, "verification": verification}


def desktop_show_model(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    model_ref: str,
    request_id: str = "",
) -> dict[str, Any]:
    """Ask a supported Desktop adapter to show the bound managed model."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "model_ref": model_ref, "request_id": request_id}


def desktop_select_node(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    path: dict[str, Any],
    request_id: str = "",
) -> dict[str, Any]:
    """Select a typed COMSOL model-tree path in the bound Desktop window."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "path": path, "request_id": request_id}


def desktop_capture(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    region: str,
    request_id: str = "",
) -> dict[str, Any]:
    """Capture an explicitly scoped region of a bound COMSOL window."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "region": region, "request_id": request_id}


def desktop_action(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    action: dict[str, Any],
    request_id: str = "",
) -> dict[str, Any]:
    """Perform one trusted, catalog-resolved COMSOL Desktop action."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "action": action, "request_id": request_id}


def desktop_shell_execute(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    source_artifact: str,
    expected_model_ref: str,
    request_id: str = "",
) -> dict[str, Any]:
    """Submit a trusted immutable artifact to the bound Desktop Java Shell."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "source_artifact": source_artifact,
            "expected_model_ref": expected_model_ref, "request_id": request_id}


def desktop_migrate_standalone(
    project_id: str,
    idempotency_key: str,
    window_ref: str,
    target_session_id: str,
    save_policy: dict[str, Any],
    request_id: str = "",
) -> dict[str, Any]:
    """Save-copy and load a standalone model while preserving its source window."""
    return {"project_id": project_id, "idempotency_key": idempotency_key,
            "window_ref": window_ref, "target_session_id": target_session_id,
            "save_policy": save_policy, "request_id": request_id}


def register(registry: Any) -> None:
    """Register catalog MCP names with their canonical dotted operation IDs."""
    for name, operation_id, function in (
        ("desktop_status", "desktop.status", desktop_status),
        ("desktop_bind", "desktop.bind", desktop_bind),
        ("desktop_show_model", "desktop.show_model", desktop_show_model),
        ("desktop_select_node", "desktop.select_node", desktop_select_node),
        ("desktop_capture", "desktop.capture", desktop_capture),
        ("desktop_action", "desktop.action", desktop_action),
        ("desktop_shell_execute", "desktop.shell_execute", desktop_shell_execute),
        ("desktop_migrate_standalone", "desktop.migrate_standalone", desktop_migrate_standalone),
    ):
        registry.add_tool(function, name=name, operation_id=operation_id)
