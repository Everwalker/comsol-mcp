"""T19 file-budget cross-route evidence: synthetic control plane only.

No COMSOL/Worker/SSH/native connection is made.  These cases pin the two file
byte-budget semantics that were previously only asserted at helper level or not
at all:

1. The public code.execute_java route refuses a registered Java source whose
   bytes exceed the fixed 2 MiB host-side limit before any Worker contact, under
   a granted trusted-code project.
2. resolve_registered_artifact binds identity (project/host) first and then
   honours the caller's bounded read limit with ARTIFACT_TOO_LARGE, and serves
   within-cap bytes unchanged.
"""
from pathlib import Path

import pytest

from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    resolve_registered_artifact,
)
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_code import MAX_JAVA_SOURCE_BYTES
from comsol_mcp._runtime_installation import runtime_id_for_root
from tests.test_g3_managed_models import _Worker, _runtime_root


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    install = _runtime_root(tmp_path / "install")
    endpoint = "127.0.0.1:25447"
    worker = _Worker(install, endpoint)
    service = ExecutionService(
        SessionLedger("software-session", "software-server",
                      permissions={"inspect", "project_write", "compute", "trusted_code", "host_control"}),
        worker, project_root=projects)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker, project_root=projects)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {
        "runtime_id": runtime_id_for_root(install), "worker_instance_id": "worker-1",
        "connection_epoch": 1, "server_instance_id": "software-server", "endpoint": endpoint}
    daemon.backend._bind_runtime_observation_context(
        installation_root=install, runtime_id=runtime_id_for_root(install), worker_instance_id="worker-1",
        connection_epoch=1, server_instance_id="software-server", endpoint=endpoint, legacy=False)
    daemon.backend.persist()
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"software_boundary_not_native": True})
    created = daemon.dispatch({"operation": "project.create", "arguments": {
        "label": "file-budget", "workspace": "file-budget",
        "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code", "host_control"]}},
        "execution": {"request_id": "project", "idempotency_key": "project"}})
    assert created["success"], created
    project = created["data"]["project"]
    model = daemon.dispatch({"operation": "model.create", "arguments": {"label": "budget"},
                             "execution": {"project_id": project["project_id"],
                                           "session_id": service.ledger.session_id,
                                           "request_id": "model", "idempotency_key": "model"}})
    assert model["success"], model
    ref = model["execution"]["model_ref"]
    yield daemon, worker, project, ref, Path(project["workspace"]), service
    daemon.close()


def _public(daemon, project, ref, service, operation, body, key):
    body = {"project_id": project["project_id"], "session_id": ref["session_id"], "model_ref": ref, **body}
    execution = {"project_id": project["project_id"], "session_id": ref["session_id"], "model_ref": ref,
                 "expected_revision": service.ledger._models[ref["model_tag"]].revision,
                 "request_id": key, "idempotency_key": key}
    return daemon.dispatch({"operation": "operation_call",
                            "arguments": {"operation_id": operation, "arguments": body},
                            "execution": execution})


def test_java_route_refuses_registered_source_over_the_two_meb_cap_before_worker(environment):
    daemon, worker, project, ref, workspace, service = environment
    prefix = b"public class Oversize { public static Object run() { return 1; } } //"
    (workspace / "Oversize.java").write_bytes(prefix + b"x" * (MAX_JAVA_SOURCE_BYTES + 1 - len(prefix)))
    assert (workspace / "Oversize.java").stat().st_size == MAX_JAVA_SOURCE_BYTES + 1
    registered = _public(daemon, project, ref, service, "artifact.register",
                         {"path": "Oversize.java", "role": "java-source", "classification": "project_local"},
                         "oversize-register")
    assert registered["success"], registered
    result = _public(daemon, project, ref, service, "code.execute_java",
                     {"source_artifact": registered["data"]["path"], "entrypoint": "Oversize",
                      "mode": "trusted", "arguments": {}}, "oversize-execute")
    assert result["success"] is False
    # The project root binding refuses the over-cap bytes with INVALID_REQUEST
    # before any Worker call.  Because the write ticket already began, the
    # durable envelope stays fail-closed EXECUTION_STATE_UNKNOWN while keeping
    # the structured cause code; the Worker was never contacted.
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result["error"]
    assert result["error"]["details"]["cause_code"] == "INVALID_REQUEST", result["error"]
    assert not worker.executions


def test_registered_artifact_read_is_identity_bound_then_size_bounded(environment):
    daemon, _worker, project, _ref, workspace, _service = environment
    source = workspace / "Small.java"
    source.write_text("public class Small { public static Object run() { return 1; } }\n", encoding="utf-8")
    registered = _public(daemon, project, _ref, _service, "artifact.register",
                         {"path": "Small.java", "role": "java-source", "classification": "project_local"},
                         "small-register")
    assert registered["success"], registered
    artifact_id = registered["data"]["artifact_id"]
    stored = daemon.store.get_metadata("artifacts", artifact_id)
    assert stored["size"] == source.stat().st_size
    base = {"project_id": project["project_id"],
            "current_host_identity": local_artifact_host_identity(),
            "current_engine_host_identity": local_engine_host_identity("127.0.0.1:25447"),
            "current_server_instance_id": "software-server"}
    with pytest.raises(ExecutionContractError) as wrong_project:
        resolve_registered_artifact(workspace, daemon.store, artifact_id, max_size_bytes=1,
                                    project_id="other-project",
                                    current_host_identity=base["current_host_identity"],
                                    current_engine_host_identity=base["current_engine_host_identity"],
                                    current_server_instance_id=base["current_server_instance_id"])
    assert wrong_project.value.code == "ARTIFACT_SCOPE_MISMATCH"
    with pytest.raises(ExecutionContractError) as too_large:
        resolve_registered_artifact(workspace, daemon.store, artifact_id,
                                    max_size_bytes=stored["size"] - 1, **base)
    assert too_large.value.code == "ARTIFACT_TOO_LARGE"
    resolved = resolve_registered_artifact(workspace, daemon.store, artifact_id,
                                           max_size_bytes=stored["size"], **base)
    assert resolved["relative_path"] == f"g2_artifacts/registered/{artifact_id}.java"
    assert resolved["path"].read_bytes() == source.read_bytes()
