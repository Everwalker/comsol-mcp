#!/usr/bin/env python3
"""Connection lifecycle: disconnect, client shell management, require_client."""

from __future__ import annotations

import logging
from typing import Any

from comsol_mcp._server import _get_mph
from comsol_mcp._server import session_server as _srv


def _timed_call(func, *args, timeout: float = 30.0, error_msg=""):
    """Execute in the daemon queue; waiting is not engine cancellation."""
    try:
        return func(*args)
    except Exception:
        raise


def _disconnect_locked(*, shutdown_server: bool = False) -> None:
    context = _srv.active_runtime_context()
    client = _srv._client
    server = _srv._server
    if client is not None:
        try:
            _timed_call(client.disconnect, timeout=15.0)
        except Exception as exc:
            if "not connected" not in str(exc).lower():
                _srv._last_error = f"Disconnect failed: {exc}"
        finally:
            _srv._client_connected = False
            _srv._connected_host = ""
            _srv._connected_port = None
            if context is None:
                _srv._client = None
            else:
                # A context's Worker/client binding is immutable. Reconnection
                # must create a new epoch/context rather than reuse a stale
                # socket through an old scheduler lane.
                context.client_retired = True

    _srv._current_model = None
    _srv._current_model_origin = ""
    _srv._current_model_path = ""
    _srv._mcp_owned_model_tags.clear()

    if shutdown_server and server is not None and _srv._server_started_by_mcp:
        try:
            server.stop()
        except Exception as exc:
            _srv._last_error = f"Server stop failed: {exc}"
        finally:
            if context is None:
                _srv._server = None
            _srv._server_started_by_mcp = False
    elif shutdown_server:
        if context is None:
            _srv._server = None
        _srv._server_started_by_mcp = False


def _ensure_client_shell() -> Any:
    context = _srv.active_runtime_context()
    if context is not None and context.client_retired:
        raise RuntimeError("session client binding was retired; reconnect with a new Worker epoch/context")
    if _srv._client is None:
        factory = _srv._remote_client_factory
        if context is not None and factory is None:
            # Managed requests may not fall back to another session's global
            # Worker factory. Prefer only a factory carried by this context.
            worker = context.worker
            factory = getattr(worker, "client", None) if worker is not None else None
            if factory is None:
                raise RuntimeError("managed session has no Worker-owned client factory")
        if factory is not None:
            _srv._client = factory()
            return _srv._client
        if context is not None:
            raise RuntimeError("managed session cannot use the process-global MPh client factory")
        mph = _get_mph()
        if mph is None:
            raise RuntimeError("MPh is not available; cannot create client shell.")
        client_kwargs: dict[str, Any] = {"host": None}
        if _srv.DEFAULT_VERSION:
            client_kwargs["version"] = _srv.DEFAULT_VERSION
        _srv._client = mph.Client(**client_kwargs)
    return _srv._client


def _retire_client_binding() -> None:
    """Discard a failed shell globally or retire the immutable session binding."""
    context = _srv.active_runtime_context()
    if context is None:
        _srv._client = None
    else:
        context.client_connected = False
        context.connected_host = ""
        context.connected_port = None
        context.client_retired = True


def _require_client() -> Any:
    if _srv._client is None or not _srv._client_connected:
        raise RuntimeError("Not connected to a COMSOL Multiphysics Server.")
    return _srv._client
