from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._desktop_service import (
    AVAILABLE,
    UNSUPPORTED_CONTROL,
    MetadataOnlyDesktopAdapter,
    WindowIdentity,
    WindowObservation,
)
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._tools_desktop import register as register_desktop


class AvailableWindowAdapter(MetadataOnlyDesktopAdapter):
    def __init__(self):
        self.window = WindowObservation(
            WindowIdentity(
                platform="fixture",
                native_window_id="WINDOW:1",
                process_id=4321,
                process_birth="birth:4321",
                login_id="uid:test",
                desktop_session_id="session:1",
                comsol_version="6.4.0.293",
            ),
            title="COMSOL fixture window",
            control_status=AVAILABLE,
        )

    def status(self, runtime_id=None):
        return {"platform": "fixture", "permission_status": AVAILABLE,
                "control_status": AVAILABLE, "metadata_status": AVAILABLE}

    def enumerate_windows(self, runtime_id=None):
        return [self.window]

    def revalidate_window(self, identity):
        return self.window if self.window.identity == identity else None


def _service(root):
    class SnapshotAdapter:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "f" * 64,
                    "external_event_counter": 0}

    return ExecutionService(SessionLedger("session-a", "server"), SnapshotAdapter(), project_root=root)


def _operation_count(daemon):
    return daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0]


def test_all_public_desktop_tool_names_route_to_canonical_catalog_ids():
    calls = []
    server = FastMCP("desktop-gateway-test")
    registry = GatewayRegistry(server, lambda op, args, execution: (
        calls.append((op, args, execution)) or {"success": True, "data": {}}
    ))
    register_desktop(registry)

    names = {
        "desktop_status": "desktop.status",
        "desktop_bind": "desktop.bind",
        "desktop_show_model": "desktop.show_model",
        "desktop_select_node": "desktop.select_node",
        "desktop_capture": "desktop.capture",
        "desktop_action": "desktop.action",
        "desktop_shell_execute": "desktop.shell_execute",
        "desktop_migrate_standalone": "desktop.migrate_standalone",
    }
    assert set(names).issubset(server._tool_manager._tools)
    assert set(names.values()).issubset(registry.functions)

    tool = server._tool_manager._tools["desktop_status"]
    assert tool.parameters["required"] == ["project_id"]
    response = asyncio.run(tool.fn(project_id="project-a"))
    assert response.structuredContent["success"] is True
    assert calls == [("desktop.status", {"project_id": "project-a", "request_id": "", "runtime_id": ""}, {})]

    bind = server._tool_manager._tools["desktop_bind"]
    asyncio.run(bind.fn(project_id="p", idempotency_key="k", window_ref="w", model_ref="m"))
    assert calls[-1][0] == "desktop.bind"
    assert calls[-1][1]["verification"] == {}
    asyncio.run(bind.fn(project_id="p", idempotency_key="k2", window_ref="w", model_ref="m", verification=None))
    assert "verification" in calls[-1][1] and calls[-1][1]["verification"] is None


def test_real_server_registration_exposes_catalog_desktop_tool_names():
    import comsol_mcp.mcp_server as server_module

    expected = {
        "desktop_status", "desktop_bind", "desktop_show_model", "desktop_select_node",
        "desktop_capture", "desktop_action", "desktop_shell_execute", "desktop_migrate_standalone",
    }
    tools = server_module.mcp._tool_manager._tools
    assert expected.issubset(tools)
    assert server_module._gateway.functions["desktop.status"].__name__ == "desktop_status"
    assert tools["desktop_status"].parameters["required"] == ["project_id"]


def test_status_and_nested_status_use_read_only_control_route(tmp_path):
    for outer_operation, arguments in (
        ("desktop.status", {"project_id": "project-a"}),
        ("registry_call", {"operation_id": "desktop.status", "arguments": {}}),
        ("operation_call", {"operation_id": "desktop.status", "arguments": {}}),
    ):
        daemon = ControlDaemon(tmp_path / outer_operation, desktop_adapter=AvailableWindowAdapter())
        try:
            execution = {"project_id": "project-a"} if outer_operation != "desktop.status" else {}
            result = daemon.dispatch({"operation": outer_operation, "arguments": arguments, "execution": execution})
            assert result["success"] is True
            assert result["data"]["adapter"]["control_status"] == AVAILABLE
            assert result["data"]["windows"][0]["window_ref"] is not None
            assert _operation_count(daemon) == 0
        finally:
            daemon.close()


@pytest.mark.parametrize(("operation", "arguments"), [
    ("desktop.bind", {"window_ref": "forged", "model_ref": "model-ref"}),
    ("desktop.show_model", {"window_ref": "forged", "model_ref": "model-ref"}),
    ("desktop.select_node", {"window_ref": "forged", "path": {"segments": [{"accessor": "geometry"}]}}),
    ("desktop.capture", {"window_ref": "forged", "region": "graphics"}),
    ("desktop.action", {"window_ref": "forged", "action": {"action_id": "unknown"}}),
    ("desktop.shell_execute", {"window_ref": "forged", "source_artifact": "artifact", "expected_model_ref": "model-ref"}),
    ("desktop.migrate_standalone", {"window_ref": "forged", "target_session_id": "target", "save_policy": {
        "mode": "save_copy", "destination_path": "/tmp/copy.mph", "overwrite": False,
    }}),
])
def test_seven_host_control_actions_require_exact_host_grant_without_claims(tmp_path, operation, arguments):
    daemon = ControlDaemon(tmp_path, desktop_adapter=AvailableWindowAdapter())
    try:
        response = daemon.dispatch({
            "operation": operation,
            "arguments": {"project_id": "project-a", "idempotency_key": "must-not-start", **arguments},
            "execution": {},
        })
        assert response["success"] is False
        assert response["error"]["code"] == "PERMISSION_REQUIRED"
        assert "HOST_CONTROL" in response["error"]["message"]
        assert _operation_count(daemon) == 0
    finally:
        daemon.close()


def test_durable_bind_refusal_is_a_replayed_failure_not_unknown(tmp_path):
    service = _service(tmp_path)
    service.ledger.permissions.add("host_control")
    daemon = ControlDaemon(tmp_path / "daemon", service=service, desktop_adapter=AvailableWindowAdapter())
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        token = status["data"]["windows"][0]["window_ref"]
        request = {
            "operation": "desktop.bind",
            "arguments": {"project_id": "p", "idempotency_key": "bind-1", "window_ref": token,
                          "model_ref": "catalog-model-ref"},
            "execution": {},
        }
        first = daemon.dispatch(request)
        replay = daemon.dispatch(request)
        assert first["success"] is False
        assert first["error"]["code"] == "UNSUPPORTED_CONTROL"
        assert first["execution"]["status"] == "FAILED"
        assert first == replay
        assert _operation_count(daemon) == 1

        conflict = {**request, "arguments": {**request["arguments"], "model_ref": "different-ref"}}
        rejected = daemon.dispatch(conflict)
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert _operation_count(daemon) == 1
    finally:
        daemon.close()


def test_desktop_registry_metadata_does_not_advertise_unimplemented_native_controls():
    from comsol_mcp import _g2_registry

    status = _g2_registry.registry_describe("desktop.status")
    assert status["executable"] is True
    assert status["runtime_dispatch_contract"]["verification_scope"].find("NOT_RUN") >= 0
    for operation in (
        "desktop.bind", "desktop.show_model", "desktop.select_node", "desktop.capture",
        "desktop.action", "desktop.shell_execute", "desktop.migrate_standalone",
    ):
        descriptor = _g2_registry.registry_describe(operation)
        assert descriptor["executable"] is False
        assert descriptor["implementation_status"] == "PROPOSED_NOT_IMPLEMENTED"
        entry = _g2_registry.validate_call(operation, {
            "project_id": "p", "idempotency_key": "key", "window_ref": "w",
            **({"path": {"segments": [{"accessor": "geometry"}]}} if operation == "desktop.select_node" else {}),
            **({"region": "graphics"} if operation == "desktop.capture" else {}),
            **({"action": {"id": "candidate"}} if operation == "desktop.action" else {}),
            **({"source_artifact": "a", "expected_model_ref": "m"} if operation == "desktop.shell_execute" else {}),
            **({"target_session_id": "target", "save_policy": {"mode": "save_copy", "destination_path": "/tmp/a", "overwrite": False}}
               if operation == "desktop.migrate_standalone" else {}),
            **({"model_ref": "m"} if operation in {"desktop.bind", "desktop.show_model"} else {}),
        }, allow_coordinator_only=True)
        assert entry.operation_id == operation

    with pytest.raises(Exception):
        _g2_registry.validate_call("desktop.bind", {
            "project_id": "p", "idempotency_key": "key", "window_ref": "w", "model_ref": "m",
        })
