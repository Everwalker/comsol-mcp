"""T19/T18 evidence: the empty source_artifact refusal is a validation-stage host refusal.

Software scope only; no COMSOL/Worker/SSH is started and no prior UNKNOWN resource
is touched.  The public code.execute_java schema constrains source_artifact to a
string, so empty/whitespace input reaches read_source_snapshot.  Before the fix
the first empty-input check raised a stage-less ExecutionContractError, and the
main Agent's witness probe showed the durable result could only be
EXECUTION_STATE_UNKNOWN/post_dispatch even though engine_calls=0 and
mutation_issued=False.  These cases pin the minimal fix: the pure host-side input
decision joins the T18 proof class (explicit validation stage), while a raise that
cannot pair with the no-mutation witness keeps its fail-closed UNKNOWN shape.
"""
from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_code import read_source_snapshot
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._runtime_installation import runtime_id_for_root
from tests.test_g3_managed_models import _Worker, _runtime_root


def test_empty_blank_or_non_string_source_artifact_declares_validation_stage(tmp_path):
    for bad in ("", "   ", 5, None):
        with pytest.raises(PreWriteRefusal) as raised:
            read_source_snapshot(tmp_path, bad)
        assert raised.value.code == "INVALID_REQUEST"
        assert raised.value.stage == "validation"


def test_empty_source_is_refused_before_opening_project_root(tmp_path):
    with pytest.raises(PreWriteRefusal) as raised:
        read_source_snapshot(tmp_path / "missing-root", "")
    assert raised.value.code == "INVALID_REQUEST"
    assert raised.value.stage == "validation"


def test_witness_pairs_the_empty_input_refusal_with_zero_dispatch(tmp_path):
    # Same boundary the main Agent reproduced: the witness still decides; the
    # declared stage alone is never the proof.  With zero engine calls the
    # refusal is published definite instead of EXECUTION_STATE_UNKNOWN.
    envelope = ManagedBackend._dispatch_with_witness(
        "code.execute_java", lambda: read_source_snapshot(tmp_path, ""), effect="trusted_code")
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "INVALID_REQUEST"
    assert envelope["error"]["stage"] == "validation"
    assert envelope["data"]["status"] == "REFUSED" and envelope["data"]["refused"] is True
    witness = envelope["data"]["witness"]
    assert witness["mutation_issued"] is False and witness["engine_calls"] == 0
    assert envelope["data"]["dispatch_stage"] == "validation"


def test_stageless_raise_keeps_fail_closed_unknown_shape():
    def raiser():
        raise ExecutionContractError("INVALID_REQUEST", "cannot prove nothing dispatched")

    with pytest.raises(ExecutionContractError) as raised:
        ManagedBackend._dispatch_with_witness("code.execute_java", raiser, effect="trusted_code")
    assert raised.value.code == "EXECUTION_STATE_UNKNOWN"
    assert raised.value.stage == "post_dispatch"
    assert raised.value.details["cause_code"] == "INVALID_REQUEST"
    assert raised.value.details["unproven_pre_dispatch"] is False


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    install = _runtime_root(tmp_path / "install")
    endpoint = "127.0.0.1:25448"
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
        "label": "empty-source", "workspace": "empty-source",
        "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code", "host_control"]}},
        "execution": {"request_id": "project", "idempotency_key": "project"}})
    assert created["success"], created
    project = created["data"]["project"]
    model = daemon.dispatch({"operation": "model.create", "arguments": {"label": "empty-source"},
                             "execution": {"project_id": project["project_id"],
                                           "session_id": service.ledger.session_id,
                                           "request_id": "model", "idempotency_key": "model"}})
    assert model["success"], model
    ref = model["execution"]["model_ref"]
    yield daemon, worker, project, ref, Path(project["workspace"]), service
    daemon.close()


def test_public_execute_java_empty_source_is_definite_refusal(environment):
    daemon, worker, project, ref, _workspace, service = environment
    body = {"project_id": project["project_id"], "session_id": ref["session_id"], "model_ref": ref,
            "source_artifact": "", "entrypoint": "Task", "mode": "trusted", "arguments": {}}
    execution = {"project_id": project["project_id"], "session_id": ref["session_id"], "model_ref": ref,
                 "expected_revision": service.ledger._models[ref["model_tag"]].revision,
                 "request_id": "empty-execute", "idempotency_key": "empty-execute"}
    result = daemon.dispatch({"operation": "operation_call",
                              "arguments": {"operation_id": "code.execute_java", "arguments": body},
                              "execution": execution})
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_REQUEST", result["error"]
    assert result["error"]["stage"] == "validation", result["error"]
    assert result["data"]["status"] == "REFUSED" and result["data"]["refused"] is True
    witness = result["data"]["witness"]
    assert witness["mutation_issued"] is False and witness["engine_calls"] == 0
    assert not worker.executions
    # The never-dispatched refusal must not poison the model gate.
    state = service.ledger._models[ref["model_tag"]]
    assert state.dirty is False and state.revision == 0
    retry_execution = dict(execution, request_id="empty-execute-retry", idempotency_key="empty-execute-retry",
                           expected_revision=state.revision)
    retry = daemon.dispatch({"operation": "operation_call",
                             "arguments": {"operation_id": "code.execute_java", "arguments": body},
                             "execution": retry_execution})
    assert retry["success"] is False and retry["error"]["code"] == "INVALID_REQUEST"
