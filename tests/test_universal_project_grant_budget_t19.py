"""T19 unified project grant / timeout-budget assertions across routes.

Synthetic engine doubles only; this file is software evidence and never native
COMSOL acceptance. Reuse boundaries: cross-project ModelRef refusal, api.invoke
setter ACL, and queued-work re-authorization after revocation already live in
test_control_daemon_projects.py / test_unit_a_node_public_api.py /
test_public_java_recipe_witness.py and are not re-litigated here.
"""
from __future__ import annotations

import json

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority


def _project(daemon: ControlDaemon, name: str, policy: dict) -> dict:
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": name, "workspace": name, "policy": policy},
        "execution": {"request_id": f"create-{name}", "idempotency_key": f"create-{name}"},
    })
    assert result["success"] is True, result
    return result["data"]["project"]


def test_single_grant_sustains_domain_setters_and_timeout_caps_are_effective(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    calls: list[tuple] = []
    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=root,
        registry={"set_parameters": lambda args: {"success": True, "data": dict(args)}},
    )
    # The real offline backend refuses engine work before a connection; the
    # stub keeps the claim at the control-plane boundary that T19 covers.
    monkeypatch.setattr(
        daemon.backend, "invoke",
        lambda operation, arguments, *_rest: calls.append((operation, dict(arguments))) or {"success": True, "data": {}},
    )
    try:
        project = _project(daemon, "budget", {
            "permissions": ["inspect", "project_write"],
            "timeouts": {"queue_timeout_s": 10, "execution_timeout_s": 30},
        })
        first_job = None
        for index in range(3):
            result = daemon.dispatch({
                "operation": "set_parameters",
                "arguments": {"parameter": f"x{index}"},
                "execution": {
                    "project_id": project["project_id"],
                    "request_id": f"set-{index}", "idempotency_key": f"set-{index}",
                    "rpc_timeout_s": 5, "queue_timeout_s": 600, "execution_timeout_s": 600,
                },
            })
            assert result["success"] is True, result
            if index == 0:
                first_job = result["execution"]["job_id"]
        assert [call[1]["parameter"] for call in calls] == ["x0", "x1", "x2"]
        for index in range(3):
            row = daemon.store.db.execute(
                "SELECT effective_timeouts FROM operations WHERE idempotency_key=?", (f"set-{index}",)
            ).fetchone()
            effective = json.loads(row["effective_timeouts"])
            assert effective["queue_timeout_s"] == 10
            assert effective["execution_timeout_s"] == 30
            assert effective["rpc_timeout_s"] == 5

        # A null requested deadline is still clamped to the project budget.
        null_deadline = daemon.dispatch({
            "operation": "set_parameters",
            "arguments": {"parameter": "x-null"},
            "execution": {
                "project_id": project["project_id"],
                "request_id": "set-null", "idempotency_key": "set-null",
                "execution_timeout_s": None,
            },
        })
        assert null_deadline["success"] is True, null_deadline
        row = daemon.store.db.execute(
            "SELECT effective_timeouts FROM operations WHERE idempotency_key=?", ("set-null",)
        ).fetchone()
        assert json.loads(row["effective_timeouts"])["execution_timeout_s"] == 30
        assert len(calls) == 4

        # Same durable key replayed with a different over-cap deadline collapses
        # to the capped identity instead of an idempotency conflict or a re-run.
        replay = daemon.dispatch({
            "operation": "set_parameters",
            "arguments": {"parameter": "x0"},
            "execution": {
                "project_id": project["project_id"],
                "request_id": "set-0", "idempotency_key": "set-0",
                "rpc_timeout_s": 5, "queue_timeout_s": 600, "execution_timeout_s": 1200,
            },
        })
        assert replay["success"] is True, replay
        assert replay["execution"]["job_id"] == first_job
        assert len(calls) == 4

        # A sibling project without the project_write grant cannot run the same
        # setter even though the host grant is unchanged.
        sibling = _project(daemon, "no-write", {"permissions": ["inspect", "compute"]})
        refused = daemon.dispatch({
            "operation": "set_parameters",
            "arguments": {"parameter": "x-foreign"},
            "execution": {
                "project_id": sibling["project_id"],
                "request_id": "set-refused", "idempotency_key": "set-refused",
            },
        })
        assert refused["success"] is False
        assert refused["error"]["code"] == "PERMISSION_DENIED"
        assert len(calls) == 4
    finally:
        daemon.close()


def test_trusted_java_requires_host_optin_then_the_same_project_grant(tmp_path, monkeypatch):
    body = {"mode": "trusted", "source_artifact": "code.java", "entrypoint": "Code"}

    default_root = tmp_path / "default-projects"
    default_root.mkdir()
    default = ControlDaemon(tmp_path / "default-control", project_root=default_root, registry={})
    try:
        assert "trusted_code" not in default.backend.host_permission_ceiling
        refused_policy = default.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "self-grant", "workspace": "self-grant",
                "policy": {"permissions": ["inspect", "project_write", "trusted_code"]},
            },
            "execution": {"request_id": "create-self-grant", "idempotency_key": "create-self-grant"},
        })
        assert refused_policy["success"] is False
        assert refused_policy["error"]["code"] == "POLICY_ESCALATION_REFUSED"
        assert list(default_root.iterdir()) == []

        plain = _project(default, "java-plain", {"permissions": ["inspect", "project_write", "compute"]})
        calls: list[str] = []
        monkeypatch.setattr(
            default.backend, "invoke",
            lambda operation, *_args: calls.append(operation) or {"success": True, "data": {}},
        )
        denied = default.dispatch({
            "operation": "code.execute_java", "arguments": dict(body),
            "execution": {"project_id": plain["project_id"], "request_id": "java-default", "idempotency_key": "java-default"},
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert calls == []
    finally:
        default.close()

    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    opted_root = tmp_path / "opted-projects"
    opted_root.mkdir()
    opted = ControlDaemon(tmp_path / "opted-control", project_root=opted_root, registry={})
    try:
        assert "trusted_code" in opted.backend.host_permission_ceiling
        ungated = _project(opted, "java-ungated", {"permissions": ["inspect", "project_write", "compute"]})
        granted = _project(opted, "java-granted", {"permissions": ["inspect", "project_write", "trusted_code"]})
        calls: list[str] = []
        monkeypatch.setattr(
            opted.backend, "invoke",
            lambda operation, *_args: calls.append(operation) or {"success": True, "data": {}},
        )
        denied = opted.dispatch({
            "operation": "code.execute_java", "arguments": dict(body),
            "execution": {"project_id": ungated["project_id"], "request_id": "java-ungated", "idempotency_key": "java-ungated"},
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        admitted = opted.dispatch({
            "operation": "code.execute_java", "arguments": dict(body),
            "execution": {"project_id": granted["project_id"], "request_id": "java-granted", "idempotency_key": "java-granted"},
        })
        assert admitted["success"] is True, admitted
        assert calls == ["code.execute_java"]
    finally:
        opted.close()


def test_timeout_caps_clamp_persisted_policy_and_fail_closed(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    ceiling = frozenset({"inspect", "project_write", "compute"})
    authority = ProjectAuthority(
        store,
        workspace_root=root,
        permission_provider=lambda: {"inspect", "project_write", "compute"},
        grant_ceiling=ceiling,
    )
    project = authority.dispatch("project.create", {
        "label": "caps", "workspace": "caps",
        "policy": {"permissions": ["inspect", "project_write"], "timeouts": {"queue_timeout_s": 10, "execution_timeout_s": 30}},
    })["data"]["project"]
    capped = authority.apply_timeout_caps(project["project_id"], {
        "rpc_timeout_s": 5.0, "queue_timeout_s": 600.0, "execution_timeout_s": None, "no_progress_warning_s": 3.0,
    })
    assert capped == {"rpc_timeout_s": 5.0, "queue_timeout_s": 10, "execution_timeout_s": 30, "no_progress_warning_s": 3.0}
    with pytest.raises(ExecutionContractError) as missing:
        authority.apply_timeout_caps("no-such-project", {})
    assert missing.value.code == "PROJECT_NOT_FOUND"
    store.close()
