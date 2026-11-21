"""In-memory managed-route coverage for consented ordinary node removal.

The model, COMSOL receiver and isolation authority are fakes: this file does
not validate real project binding, a daemon, or any native engine behavior.
"""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from comsol_mcp import _managed_backend as managed
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from tests.test_t13_consented_remove_portable_20261110 import body_for, consent, make_worker


@pytest.mark.parametrize("route", ["node.remove", "registry_call"])
def test_managed_removal_permissions_consent_and_dispatch(route, tmp_path, monkeypatch):
    """Both gateways must refuse before ticket/receiver or dispatch exactly once."""
    fake_worker, root, geom_list, _f1, _f2 = make_worker()
    worker: Any = fake_worker
    worker.model_snapshot = lambda tag: {
        "model_tag": tag,
        "server_instance_id": "fake-server",
        "external_event_counter": 0,
        "fingerprint": "fake-fingerprint",
    }
    worker.operation_context = lambda *_args, **_kwargs: nullcontext()
    ledger = SessionLedger(session_id="fake-session", server_instance_id="fake-server")
    service = ExecutionService(ledger, worker, project_root=tmp_path)
    model_ref = service.bind_model("m")["execution"]["model_ref"]

    # These construction-time services and the isolation decision are outside
    # this test's scope; do not create SQLite databases or use any real project.
    monkeypatch.setattr(managed, "default_comsol_help_roots", lambda _home: [])
    monkeypatch.setattr(managed, "OfflineDocsIndex", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(managed, "TransactionStore", lambda *_args, **_kwargs: SimpleNamespace())
    backend = managed.ManagedBackend(home=tmp_path, store=None, service=service, worker=worker, registry={})
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"kind": "in-memory-fake"})
    ticket_calls = []
    original_begin_write = SessionLedger.begin_write

    def observed_begin_write(self, *args, **kwargs):
        ticket_calls.append(1)
        return original_begin_write(self, *args, **kwargs)

    monkeypatch.setattr(SessionLedger, "begin_write", observed_begin_write)
    execution = {
        "project_id": "fake-project",
        "session_id": "fake-session",
        "model_ref": model_ref,
        "expected_revision": 0,
        "idempotency_key": "fake-key-" + route,
    }

    def invoke(body):
        if route == "registry_call":
            arguments = {"operation_id": "node.remove", "arguments": body}
        else:
            arguments = body
        return backend.invoke(route, arguments, execution, "fake-operation", None)

    def authorized_body():
        body = body_for()
        body["authorization"] = consent()
        return body

    ledger.permissions = {"inspect"}
    with pytest.raises(ExecutionContractError) as denied:
        invoke(authorized_body())
    assert denied.value.code == "PERMISSION_DENIED"
    assert not ticket_calls and worker.model_calls == 0 and geom_list.calls == []

    ledger.permissions.add("project_write")
    with pytest.raises(ExecutionContractError) as no_consent:
        invoke(body_for())
    assert no_consent.value.code == "API_UNSUPPORTED"
    assert not ticket_calls and worker.model_calls == 0 and geom_list.calls == []

    result = invoke(authorized_body())
    assert result["success"] is True
    assert result["data"]["status"] == "INCOMPLETE"
    assert result["data"]["reference_risk"]["level"] == "UNSCANNED"
    assert result["data"]["retention"]["target_absent"] is True
    assert result["execution"]["revision"] == 1
    assert ticket_calls == [1]
    assert geom_list.calls == [("remove", "f1")]
    assert geom_list.order == ["f2"]
    assert getattr(root, "feature")().order == ["a", "b"]
    assert getattr(root, "component")().order == ["c1"]
