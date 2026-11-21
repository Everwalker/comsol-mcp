"""T45 (20261108): Desktop public-route software evidence.

Drives the real public MCP tool names through the real GatewayRegistry, the
real ControlDaemon durable dispatch/validate path, and the real durable
OperationStore, with only the platform adapter and worker replaced by the
isolated software fixtures.  No Desktop, COMSOL, worker process, Java, socket,
or platform/native control call is executed; green here proves the software
route shape, never native acceptance.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._tools_desktop import register as register_desktop

from test_desktop_managed_load_phase2b import Harness


def _call(tools, tool_name, **kwargs):
    return asyncio.run(tools[tool_name].fn(**kwargs))


def _error_code(result):
    error = result.structuredContent.get("error")
    return error.get("code") if isinstance(error, dict) else error


@pytest.fixture
def route(tmp_path):
    harness = Harness(tmp_path)
    server = FastMCP("t45-public-route")
    registry = GatewayRegistry(
        server,
        lambda operation, arguments, execution: harness.daemon.dispatch({
            "operation": operation, "arguments": arguments, "execution": execution,
        }),
    )
    register_desktop(registry)
    try:
        yield harness, server._tool_manager._tools
    finally:
        harness.close()


def _fresh_window(harness, tools):
    status = _call(tools, "desktop_status", project_id="p")
    assert status.structuredContent["success"] is True
    windows = status.structuredContent["data"]["windows"]
    assert len(windows) == 1 and windows[0]["identity_status"] == "AVAILABLE"
    return windows[0]["window_ref"], status


def test_public_status_mints_lease_and_reports_unknown_binding(route):
    harness, tools = route
    window, status = _fresh_window(harness, tools)
    payload = status.structuredContent["data"]
    assert window.startswith("dw_")
    assert payload["windows"][0]["model_binding"] == "UNKNOWN"
    # Status remains a control-plane read: no durable Desktop operation row.
    assert harness.daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


def test_public_save_copy_migration_reaches_durable_ready_evidence(route):
    harness, tools = route
    window, _ = _fresh_window(harness, tools)
    destination = harness.root / "t45-public-copy.mph"
    result = _call(tools, "desktop_migrate_standalone", project_id="p",
                   idempotency_key="t45-mig", request_id="t45-request", window_ref=window,
                   target_session_id="target",
                   save_policy={"mode": "save_copy", "destination_path": str(destination),
                                "overwrite": False})
    assert result.structuredContent["success"] is True, result.structuredContent
    data = result.structuredContent["data"]
    assert data["migration_mode"] == "explicit_save_copy"
    assert data["source_window_preserved"] is True
    assert data["target_model_ref"]["session_id"] == "target"
    assert data["saved_copy"]["path"] == str(destination)
    assert destination.is_file() and not destination.is_symlink()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    assert data["saved_copy"]["sha256"] == digest
    rows = list(harness.daemon._desktop_attempt_rows("p"))
    assert len(rows) == 1 and rows[0][1]["state"] == "READY"
    assert rows[0][1]["model_ref"] == data["target_model_ref"]
    # The standalone source is copied, never "adopted": the managed target
    # carries its own ModelRef and the Desktop binding stays explicitly
    # unverified, so no claim exists that arbitrary standalone memory is taken.
    assert data["desktop_binding_verified"] is False


def test_public_migration_replay_never_repeats_effects(route):
    harness, tools = route
    window, _ = _fresh_window(harness, tools)
    destination = harness.root / "t45-replay.mph"
    arguments = dict(project_id="p", idempotency_key="t45-replay", request_id="t45-replay-req",
                     window_ref=window, target_session_id="target",
                     save_policy={"mode": "save_copy", "destination_path": str(destination),
                                  "overwrite": False})
    first = _call(tools, "desktop_migrate_standalone", **arguments)
    assert first.structuredContent["success"] is True, first.structuredContent
    worker_calls = list(harness.worker.trace)
    replay = _call(tools, "desktop_migrate_standalone", **arguments)
    assert replay.structuredContent["success"] is True
    assert (replay.structuredContent["data"]["target_model_ref"]
            == first.structuredContent["data"]["target_model_ref"])
    assert harness.worker.trace == worker_calls
    assert harness.daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1

    new_key = _call(tools, "desktop_migrate_standalone", **{**arguments, "idempotency_key": "t45-new-key"})
    assert not new_key.structuredContent["success"]
    assert _error_code(new_key) == "SAVE_COPY_PATH_REJECTED"
    assert harness.worker.trace == worker_calls


def test_public_host_control_actions_refuse_forged_or_stale_window_handles(route):
    harness, tools = route
    for name, kwargs, allowed in (
        ("desktop_bind", dict(model_ref="model-ref"),
         ("MODEL_BINDING_UNKNOWN", "UNSUPPORTED_CONTROL", "WINDOW_HANDLE_EXPIRED",
          "MODEL_IDENTITY_MISMATCH")),
        ("desktop_show_model", dict(model_ref="model-ref"), ("WINDOW_HANDLE_EXPIRED",)),
        ("desktop_select_node", dict(path={"segments": [{"collection": "geom", "tag": "1"}]}),
         ("WINDOW_HANDLE_EXPIRED",)),
        ("desktop_action", dict(action={"action_id": "unknown"}), ("WINDOW_HANDLE_EXPIRED",)),
    ):
        result = _call(tools, name, project_id="p", idempotency_key=f"t45-forged-{name}",
                       window_ref="dw_forged_handle", **kwargs)
        assert not result.structuredContent["success"], name
        assert _error_code(result) in allowed, (name, result.structuredContent)
        assert result.isError is True
    # Forged-handle attempts may open durable claims, but every such claim is
    # terminal FAILED with no adapter effect: never UNKNOWN, never SUCCEEDED.
    rows = harness.daemon.store.db.execute(
        "SELECT status FROM operations WHERE operation IN "
        "('desktop.bind','desktop.show_model','desktop.select_node','desktop.action')"
    ).fetchall()
    assert len(rows) == 4
    assert all(row[0] == "FAILED" for row in rows)


def test_public_permission_and_schema_gates_refuse_before_effects(route):
    harness, tools = route
    # capture (HOST_CONTROL+PROJECT_WRITE) and shell_execute (HOST_CONTROL+
    # TRUSTED_CODE) are refused by the ledger grants before any adapter work.
    capture = _call(tools, "desktop_capture", project_id="p", idempotency_key="t45-cap",
                    window_ref="dw_forged_handle", region="graphics")
    assert not capture.structuredContent["success"]
    assert _error_code(capture) == "PERMISSION_REQUIRED"
    shell = _call(tools, "desktop_shell_execute", project_id="p", idempotency_key="t45-shell",
                  window_ref="dw_forged_handle", source_artifact="artifact-1",
                  expected_model_ref="model-ref")
    assert not shell.structuredContent["success"]
    assert _error_code(shell) == "PERMISSION_REQUIRED"

    window, _ = _fresh_window(harness, tools)
    for index, region in enumerate(("screen", "desktop", "all_screens", "Entire-Desktop")):
        refused = _call(tools, "desktop_capture", project_id="p",
                        idempotency_key=f"t45-region-{index}", window_ref=window, region=region)
        assert not refused.structuredContent["success"]
        assert _error_code(refused) == "CAPTURE_SCOPE_REJECTED", region

    missing_key = _call(tools, "desktop_bind", project_id="p", window_ref="dw_forged_handle",
                        model_ref="model-ref")
    assert not missing_key.structuredContent["success"]
    assert _error_code(missing_key) == "INVALID_REQUEST"
    assert harness.daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
