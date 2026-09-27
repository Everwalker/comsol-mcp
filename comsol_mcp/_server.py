#!/usr/bin/env python3
"""FastMCP instance, global runtime state, and configuration constants."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Lazy mph import: defer until first use so pip installs mid-session work.
_mph: Any | None = None
_mph_import_error = ""


def _get_mph():
    """Return the mph module, importing it on first call if needed."""
    global _mph, _mph_import_error
    if _mph is not None:
        return _mph
    try:
        import mph as _m
        _mph = _m
        _mph_import_error = ""
    except Exception as exc:  # pragma: no cover - handled at runtime
        _mph_import_error = str(exc)
    return _mph


def _get_mph_error() -> str:
    """Trigger import if needed, then return any error string."""
    _get_mph()
    return _mph_import_error


# Runtime adapters used by the legacy tool modules.  Unscoped legacy calls
# retain the process-level state below; managed dispatch selects a
# SessionRuntimeContext and reads/writes the same names through that context.
_SESSION_STATE_FIELDS = {
    "_server": "server_handle",
    "_client": "client",
    "_remote_client_factory": "remote_client_factory",
    "_client_connected": "client_connected",
    "_connected_host": "connected_host",
    "_connected_port": "connected_port",
    "_current_model": "current_model",
    "_current_model_origin": "current_model_origin",
    "_current_model_path": "current_model_path",
    "_server_started_by_mcp": "server_started_by_mcp",
    "_last_command": "last_command",
    "_last_error": "last_error",
    "_mcp_owned_model_tags": "owned_model_tags",
    "_background_jobs": "background_jobs",
}


def active_runtime_context():
    """Return the ContextVar-selected session, or ``None`` for legacy calls."""
    from ._session_context import active_session_context

    return active_session_context()


def runtime_value(name: str):
    """Read a legacy runtime field from the active session or fallback globals."""
    context = active_runtime_context()
    field_name = _SESSION_STATE_FIELDS.get(name)
    if context is not None and field_name is not None:
        return getattr(context, field_name)
    return globals()[name]


def set_runtime_value(name: str, value):
    """Write a legacy runtime field in the active session or fallback globals."""
    context = active_runtime_context()
    field_name = _SESSION_STATE_FIELDS.get(name)
    if context is not None and field_name is not None:
        setattr(context, field_name, value)
    else:
        globals()[name] = value


def runtime_lock():
    """Return the engine lock for the selected session."""
    context = active_runtime_context()
    return context.lock if context is not None else _runtime_lock


def background_jobs_lock():
    """Return the job-state lock for the selected session."""
    context = active_runtime_context()
    return context.background_jobs_lock if context is not None else _background_jobs_lock


def runtime_setting(name: str):
    """Resolve host/runtime defaults without swapping process environment."""
    context = active_runtime_context()
    if context is None:
        return globals()[name]
    if name == "COMSOL_ROOT":
        return context.runtime.installation_root
    if name == "COMSOL_SERVER_MCP_HOME":
        return context.paths.root
    if name == "OUTPUTS_DIR":
        return context.project_root / "outputs"
    if name == "DEFAULT_HOST":
        return context.endpoint.host
    if name == "DEFAULT_PORT":
        return context.endpoint.port
    path_names = {
        "LOGS_DIR": "logs_dir",
        "OUTPUTS_DIR": "outputs_dir",
        "STATUS_FILE": "status_file",
        "WORKFLOW_FILE": "workflow_file",
        "OPERATIONS_FILE": "operations_file",
        "SERVER_LOG": "server_log",
    }
    if name in path_names:
        paths = context.paths
        if name == "LOGS_DIR":
            return paths.server_log.parent
        if name == "OUTPUTS_DIR":
            return paths.outputs_dir
        return getattr(paths, path_names[name])
    return globals()[name]


class _SessionServerProxy:
    """Compatibility facade routing legacy state attributes by ContextVar.

    Tool modules historically imported this module and accessed fields such as
    ``_client`` directly.  The facade preserves those call sites while making
    their state session-local whenever managed dispatch has selected a context.
    Constants that are runtime/session scoped are resolved dynamically too.
    """

    _DYNAMIC_SETTINGS = frozenset({
        "COMSOL_ROOT", "COMSOL_SERVER_MCP_HOME", "DEFAULT_HOST", "DEFAULT_PORT",
        "LOGS_DIR", "OUTPUTS_DIR", "STATUS_FILE", "WORKFLOW_FILE",
        "OPERATIONS_FILE", "SERVER_LOG",
    })

    def __getattr__(self, name: str):
        if name in _SESSION_STATE_FIELDS:
            return runtime_value(name)
        if name == "_runtime_lock":
            return runtime_lock()
        if name == "_background_jobs_lock":
            return background_jobs_lock()
        if name in self._DYNAMIC_SETTINGS:
            return runtime_setting(name)
        return globals()[name]

    def __setattr__(self, name: str, value):
        if name in _SESSION_STATE_FIELDS:
            set_runtime_value(name, value)
            return
        if name == "_runtime_lock":
            if active_runtime_context() is not None:
                raise RuntimeError("session runtime lock is owned by its context")
            globals()[name] = value
            return
        if name == "_background_jobs_lock":
            if active_runtime_context() is not None:
                raise RuntimeError("session job lock is owned by its context")
            globals()[name] = value
            return
        if name in self._DYNAMIC_SETTINGS:
            if active_runtime_context() is not None:
                raise RuntimeError(f"session-scoped setting {name} cannot be mutated in place")
        globals()[name] = value


session_server = _SessionServerProxy()

VERSION = "0.1.9"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMSOL_ROOT = Path(r"C:\Program Files\COMSOL\COMSOL63\Multiphysics")
DEFAULT_SERVER_HOME = WORKSPACE_ROOT / "comsol-server-home"

COMSOL_ROOT = Path(os.environ.get("COMSOL_ROOT", str(DEFAULT_COMSOL_ROOT))).expanduser()
COMSOL_SERVER_MCP_HOME = Path(
    os.environ.get("COMSOL_SERVER_MCP_HOME", str(DEFAULT_SERVER_HOME))
).expanduser()
DEFAULT_TIMEOUT = int(os.environ.get("COMSOL_SERVER_TIMEOUT_SECONDS", "120"))
DEFAULT_HOST = os.environ.get("COMSOL_SERVER_HOST", "localhost")
DEFAULT_PORT = int(os.environ.get("COMSOL_SERVER_PORT", "2036"))
DEFAULT_VERSION = os.environ.get("COMSOL_SERVER_VERSION", "").strip() or None

COMSOL_MPHSERVER = COMSOL_ROOT / "bin" / "win64" / "comsolmphserver.exe"
COMSOL_MPHCLIENT = COMSOL_ROOT / "bin" / "win64" / "comsolmphclient.exe"

LOGS_DIR = COMSOL_SERVER_MCP_HOME / "logs"
OUTPUTS_DIR = COMSOL_SERVER_MCP_HOME / "outputs"
STATUS_FILE = COMSOL_SERVER_MCP_HOME / "status.json"
WORKFLOW_FILE = COMSOL_SERVER_MCP_HOME / "workflow_state.json"
OPERATIONS_FILE = LOGS_DIR / "operations.jsonl"
SERVER_LOG = LOGS_DIR / "server.log"

# ---------------------------------------------------------------------------
# Global runtime state (all tool execution is guarded by _runtime_lock)
# ---------------------------------------------------------------------------
_runtime_lock = threading.RLock()
_server: Any | None = None
_client: Any | None = None
_remote_client_factory: Any | None = None
_client_connected = False
_connected_host = ""
_connected_port: int | None = None
_current_model: Any | None = None
_current_model_origin = ""
_current_model_path = ""
_server_started_by_mcp = False
_last_command = ""
_last_error = ""
_logger_ready = False
_context_file_handler: logging.Handler | None = None
_background_jobs: dict[str, dict[str, Any]] = {}
_background_jobs_lock = threading.RLock()
# Tags created or loaded by this MCP process.  Unknown loaded models are
# presumed user-owned and are never candidates for explicit pruning.
_mcp_owned_model_tags: set[str] = set()

# ---------------------------------------------------------------------------
# FastMCP instance (shared across all tool modules)
# ---------------------------------------------------------------------------
mcp = FastMCP("comsol-mcp-server")

RECOMMENDED_DESKTOP_FLOW = (
    "visible-main-first: manually start COMSOL Multiphysics Server, "
    "start a persistent MCP process, call start_visible_main_workflow(host, port, path); "
    "after that connect COMSOL Desktop to the same server and import the already loaded model."
)

# ---------------------------------------------------------------------------
# Tool classification sets (for visible-main guard)
# ---------------------------------------------------------------------------
SAFE_READ_TOOLS = {
    "server_info",
    "check_server_port",
    "workflow_info",
    "model_tree",
    "get_parameters",
    "evaluate_expressions",
    "get_core_metrics",
    "list_physics",
    "list_physics_features",
    "list_solver_config",
    "list_solver_features",
    "run_study_status",
}
SAFE_VISIBLE_MAIN_WRITE_TOOLS = {
    "set_parameters",
    "ensure_component",
    "ensure_geometry",
    "ensure_mesh",
    "create_feature",
    "update_feature",
    "delete_feature",
    "run_feature",
    "run_study",
    "run_visible_main_iteration",
    "save_main_model_snapshot",
    "save_model",
    "create_physics",
    "remove_physics",
    "create_physics_feature",
    "update_physics_feature",
    "remove_physics_feature",
    "set_physics_selection",
    "manage_variables",
    "create_solver_config",
    "configure_solver",
    "run_study_async",
}
RESTRICTED_TOOLS = {
    "commit_current_main_model",
    "model_create",
    "model_load",
    "prune_loaded_models",
}


# ---------------------------------------------------------------------------
# Basic infrastructure
# ---------------------------------------------------------------------------
class _ContextFileHandler(logging.Handler):
    """Write each log record to the active session's private log file."""

    def __init__(self) -> None:
        super().__init__()
        self._write_lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            path = Path(runtime_setting("SERVER_LOG"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise OSError("session log path cannot be a symlink")
            message = self.format(record)
            with self._write_lock, path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except Exception:
            self.handleError(record)


def _ensure_dirs() -> None:
    home = runtime_setting("COMSOL_SERVER_MCP_HOME")
    logs = runtime_setting("LOGS_DIR")
    outputs = runtime_setting("OUTPUTS_DIR")
    for directory in (home, logs, outputs):
        directory.mkdir(parents=True, exist_ok=True)


def _setup_logging() -> None:
    global _logger_ready, _context_file_handler
    if _logger_ready:
        return
    _ensure_dirs()
    root_logger = logging.getLogger()
    if _context_file_handler is None:
        _context_file_handler = _ContextFileHandler()
        _context_file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        )
    if _context_file_handler not in root_logger.handlers:
        root_logger.addHandler(_context_file_handler)
    if root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)
    _logger_ready = True
