"""T20 node.create through actual ControlDaemon admission, fake Worker only.

The local daemon and project authority are real in-process software. The engine
and the owned-server isolation proof are test doubles; there is no socket or
COMSOL server. This test does not cover general filesystem confinement.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from tests.test_unit_a_node_public_api import ROOT, Worker, tv


@pytest.fixture
def public_stack(tmp_path, monkeypatch, request):
    root = tmp_path / "projects"
    root.mkdir()
    worker = Worker()
    ledger = SessionLedger("session", "server")
    service = ExecutionService(ledger, worker, project_root=root)
    ref = service.bind_model("m")["execution"]["model_ref"]
    daemon = ControlDaemon(tmp_path / "control", project_root=root,
                           registry={}, worker=worker, service=service)
    permissions = getattr(request, "param", ["inspect", "project_write"])
    project_result = daemon.dispatch({"operation": "project.create",
        "arguments": {"label": "t20-route", "workspace": "t20-route",
                      "policy": {"permissions": permissions}},
        "execution": {"request_id": "project", "idempotency_key": "project"}})
    assert project_result["success"], project_result
    project = project_result["data"]["project"]
    daemon.backend._bind_model_project(ref, project["project_id"])
    daemon.store.put_metadata("revisions", daemon.backend._model_project_key(ref),
                              {"model_ref": ref, "project_id": project["project_id"], "revision": 0})
    daemon.backend.endpoint_key = "127.0.0.1:2036"  # identity string, no socket
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True})
    tickets = []
    original = SessionLedger.begin_write

    def record_ticket(self, *args, **kwargs):
        tickets.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionLedger, "begin_write", record_ticket)
    execution = {"project_id": project["project_id"], "session_id": "session", "model_ref": ref,
                 "expected_revision": 0, "idempotency_key": "create", "request_id": "create"}
    yield daemon, worker, Path(project["workspace"]), execution, tickets
    daemon.close()


def _dispatch(daemon, route, body, execution):
    arguments = {"operation_id": "node.create", "arguments": body} if route in {"registry_call", "operation_call"} else body
    return daemon.dispatch({"operation": route, "arguments": arguments, "execution": execution})


@pytest.mark.parametrize("route", ["node.create", "node_create", "registry_call", "operation_call"])
@pytest.mark.parametrize("external", [True, False])
def test_public_daemon_create_screens_or_applies_before_any_mutation(public_stack, tmp_path, route, external):
    daemon, worker, workspace, execution, tickets = public_stack
    target = tmp_path / "outside.dat" if external else workspace / "inside.dat"
    target.write_text("synthetic\n")
    body = {"parent": ROOT, "collection": "feature", "tag": "new1", "type_id": "Rectangle",
            "properties": [{"name": "string", "value": tv(str(target))}]}
    result = _dispatch(daemon, route, body, execution)
    creates = [row for row in worker.model_node.list.calls if row[0] == "create"]
    if external:
        assert result["success"] is False and result["error"]["code"] == "ACCESS_VIOLATION", result
        assert not creates and not tickets and "new1" not in worker.model_node.list.items
    else:
        assert result["success"] is True, result
        assert len(creates) == 1 and len(tickets) == 1
        assert worker.model_node.list.items["new1"].values["string"] == str(target)


@pytest.mark.parametrize("public_stack", [["inspect"]], indirect=True)
def test_public_project_without_write_permission_is_refused_before_worker(public_stack):
    daemon, worker, _, execution, tickets = public_stack
    body = {"parent": ROOT, "collection": "feature", "tag": "new1", "type_id": "Rectangle",
            "properties": [{"name": "string", "value": tv("linear")}]}
    result = _dispatch(daemon, "registry_call", body, execution)
    assert result["success"] is False and result["error"]["code"] == "PERMISSION_DENIED", result
    assert not tickets and not worker.model_node.list.calls and "new1" not in worker.model_node.list.items


def test_public_cross_project_model_binding_refused_before_worker(public_stack):
    daemon, worker, _, execution, tickets = public_stack
    other = daemon.dispatch({"operation": "project.create",
        "arguments": {"label": "other", "workspace": "other",
                      "policy": {"permissions": ["inspect", "project_write"]}},
        "execution": {"request_id": "other-project", "idempotency_key": "other-project"}})
    assert other["success"], other
    foreign = {**execution, "project_id": other["data"]["project"]["project_id"]}
    body = {"parent": ROOT, "collection": "feature", "tag": "new1", "type_id": "Rectangle",
            "properties": [{"name": "string", "value": tv("linear")}]}
    result = _dispatch(daemon, "node.create", body, foreign)
    assert result["success"] is False and result["error"]["code"] == "PROJECT_IDENTITY_MISMATCH", result
    assert not tickets and not worker.model_node.list.calls and "new1" not in worker.model_node.list.items
