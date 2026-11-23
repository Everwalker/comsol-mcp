"""Task-owned T19 fake/SQLite stack; no T20 helpers or model.adopt route."""
from contextlib import contextmanager
from pathlib import Path
import sys
from types import ModuleType
import pytest
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._desktop_service import MetadataOnlyDesktopAdapter
from tests.test_unit_a_node_public_api import ROOT, Worker, tv

FUNCTION_TYPES = (
    "Import", "Interpolation", "Elevation", "Image", "CGNS", "LeastSquares",
    "GaussianProcess", "PolynomialChaosExpansion",
)
NEW_FUNCTION_TYPES = ("GaussianProcess", "PolynomialChaosExpansion")


def node_path(tag="a"):
    return {"segments": [{"collection": "feature", "tag": tag}]}


def routed_call(daemon, operation, body, execution, key):
    request_execution = dict(execution)
    request_execution.update(request_id=key, idempotency_key=key)
    return daemon.dispatch({
        "operation": "registry_call",
        "arguments": {"operation_id": operation, "arguments": body},
        "execution": request_execution,
    })


@pytest.fixture
def t19_stack(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    worker = Worker()
    ledger = SessionLedger("t19-session", "server")
    service = ExecutionService(ledger, worker, project_root=project_root)
    ref = service.bind_model("m")["execution"]["model_ref"]
    state_stub = ModuleType("comsol_mcp._state")
    @contextmanager
    def no_workflow_file_scope(_root):
        yield None
    state_stub.project_workflow_state_scope = no_workflow_file_scope
    monkeypatch.setitem(sys.modules, "comsol_mcp._state", state_stub)

    daemon = ControlDaemon(
        tmp_path / "control", project_root=project_root,
        registry={}, worker=worker, service=service,
        desktop_adapter=MetadataOnlyDesktopAdapter(),
    )
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "T19 isolated review", "workspace": "t19-isolated-review",
            "policy": {"permissions": ["inspect", "project_write"]},
        },
        "execution": {"request_id": "project", "idempotency_key": "project"},
    })
    assert created["success"], created
    project = created["data"]["project"]
    daemon.backend._bind_model_project(ref, project["project_id"])
    daemon.store.put_metadata(
        "revisions", daemon.backend._model_project_key(ref),
        {"model_ref": ref, "project_id": project["project_id"],
         "revision": 0, "dirty": False},
    )
    daemon.backend.endpoint_key = "127.0.0.1:2036"  # identity string only; no socket
    monkeypatch.setattr(
        daemon.backend, "_require_g2_isolation", lambda: {"synthetic": True},
    )

    tickets = []
    original_begin_write = SessionLedger.begin_write
    def record_ticket(self, *args, **kwargs):
        tickets.append((args, kwargs))
        return original_begin_write(self, *args, **kwargs)
    monkeypatch.setattr(SessionLedger, "begin_write", record_ticket)

    execution = {
        "project_id": project["project_id"], "session_id": "t19-session",
        "model_ref": ref, "expected_revision": 0,
        "idempotency_key": "initial", "request_id": "initial",
    }
    try:
        yield {
            "daemon": daemon, "worker": worker, "service": service,
            "ledger": ledger, "execution": execution,
            "workspace": Path(project["workspace"]), "tickets": tickets,
        }
    finally:
        daemon.close()


def function_node(stack, type_id, tag="a"):
    node = stack["worker"].model_node.list.items[tag]
    node.type_id = type_id
    node.values["filename"] = "seed.dat"
    original_get_value_type = node.getValueType
    node.getValueType = lambda name: (
        "String" if name == "filename" else original_get_value_type(name)
    )
    return node


def assert_ledger_clean(stack):
    state = stack["ledger"]._models["m"]
    assert state.revision == 0
    assert state.dirty is False
    assert state.active_operation_id is None
