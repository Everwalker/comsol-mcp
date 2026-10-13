"""Defect tests for G3.6: D01-D06 fixes (Isolation, Cancel State Machine, CAS, Lease, Cache, DACL)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest

from comsol_mcp import _g2_isolation as isolation
from comsol_mcp import _java_worker as java_worker
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
)
from comsol_mcp._platform_process import (
    validate_windows_path_security,
    terminate_process_tree,
    is_process_in_job,
)
from comsol_mcp._security_os import (
    set_private_directory_permissions,
    validate_path_boundaries,
)


# ===========================================================================
# D01: Windows Isolation & Observation Tests
# ===========================================================================

def test_windows_socket_rows_parsing(monkeypatch):
    sample_netstat_output = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       888
  TCP    127.0.0.1:56389        0.0.0.0:0              LISTENING       12345
  TCP    127.0.0.1:56389        127.0.0.1:58412        ESTABLISHED     12345
  TCP    127.0.0.1:58412        127.0.0.1:56389        ESTABLISHED     777
  TCP    192.168.100.2:139      0.0.0.0:0              LISTENING       4
"""
    def mock_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=sample_netstat_output, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(isolation, "_require_isolation_adapter", lambda: None)

    rows = isolation._windows_socket_rows(56389)
    assert len(rows) == 3
    # Check listener
    listener = [r for r in rows if r["state"] == "LISTEN"]
    assert len(listener) == 1
    assert listener[0]["pid"] == 12345
    assert listener[0]["endpoint"] == "127.0.0.1:56389"

    # Check established
    established = [r for r in rows if r["state"] == "ESTABLISHED"]
    assert len(established) == 2
    pids = {r["pid"] for r in established}
    assert pids == {12345, 777}


def test_isolation_probe_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd")
    monkeypatch.setattr(isolation.os, "name", "posix")
    with pytest.raises(ExecutionContractError) as exc_info:
        isolation._require_isolation_adapter()
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"


# ===========================================================================
# D02: UNKNOWN/RECONCILING Cancel and Parameter Validation
# ===========================================================================

def test_cancel_job_unknown_status_preserved(tmp_path):
    daemon = ControlDaemon(tmp_path)
    store = daemon.store
    try:
        rec, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="run_study")
        job_id = rec["job_id"]

        # Transition job to UNKNOWN
        store.update_job(job_id, "UNKNOWN")
        assert store.job(job_id)["status"] == "UNKNOWN"

        # Request cancellation
        res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "reason": "cancel unknown job"},
        })
        assert res["success"] is True
        assert res["data"]["status"] == "UNKNOWN"
        assert res["data"]["cancel_accepted"] is True
        assert res["data"]["engine_stopped"] is False
        assert res["data"]["cancel_requested"] is True

        # Ensure the job status in the store remains UNKNOWN, NOT RUNNING!
        assert store.job(job_id)["status"] == "UNKNOWN"
    finally:
        daemon.close()


def test_cancel_job_reconciling_status_preserved(tmp_path):
    daemon = ControlDaemon(tmp_path)
    store = daemon.store
    try:
        rec, _ = store.begin(request_id="r2", idempotency_key="k2", request_hash="h2", operation="run_study")
        job_id = rec["job_id"]

        # Transition job to RECONCILING
        store.update_job(job_id, "RECONCILING")
        assert store.job(job_id)["status"] == "RECONCILING"

        # Request cancellation
        res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "reason": "cancel reconciling job"},
        })
        assert res["success"] is True
        assert res["data"]["status"] == "RECONCILING"
        assert res["data"]["cancel_requested"] is True

        # Ensure the job status in the store remains RECONCILING, NOT RUNNING!
        assert store.job(job_id)["status"] == "RECONCILING"
    finally:
        daemon.close()


def test_cancel_job_force_stop_validation_precedes_state_change(tmp_path):
    daemon = ControlDaemon(tmp_path)
    store = daemon.store
    try:
        rec, _ = store.begin(request_id="r3", idempotency_key="k3", request_hash="h3", operation="run_study")
        job_id = rec["job_id"]
        assert store.job(job_id)["status"] == "QUEUED"

        # Pass invalid force_stop (non-boolean string)
        bad_fs_res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "force_stop": "yes"},
        })
        assert bad_fs_res["success"] is False
        assert bad_fs_res["error"]["code"] == "INVALID_REQUEST"
        # Queued job must NOT have been cancelled!
        assert store.job(job_id)["status"] == "QUEUED"

        # Pass force_stop with unauthorized server_scope
        bad_scope_res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "force_stop": True, "server_scope": {"authorized": False}},
        })
        assert bad_scope_res["success"] is False
        assert bad_scope_res["error"]["code"] == "UNAUTHORIZED_FORCE_STOP"
        # Queued job still untouched!
        assert store.job(job_id)["status"] == "QUEUED"
    finally:
        daemon.close()


# ===========================================================================
# D03: Terminal State Immunity & CAS Arbitration
# ===========================================================================

def test_operation_store_terminal_state_immunity_finish(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    try:
        rec, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="run_study")
        job_id = rec["job_id"]
        op_id = rec["operation_id"]

        # Cancel while queued
        success, code, job = store.cancel_queued(job_id, reason="aborted early")
        assert success is True
        assert job["status"] == "CANCELLED"

        # A late worker callback arrives with finish(status="SUCCEEDED")
        store.finish(op_id, status="SUCCEEDED", result={"success": True, "data": {"answer": 42}})

        # Status must remain CANCELLED!
        assert store.job(job_id)["status"] == "CANCELLED"
        assert store.get_operation(op_id)["status"] == "CANCELLED"

        # A late event 'LateResultRecorded' was recorded in job_events
        events = [e["event"] for e in store.events(job_id)]
        assert "LateResultRecorded" in events
    finally:
        store.close()


def test_operation_store_terminal_state_immunity_update_job(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    try:
        rec, _ = store.begin(request_id="r2", idempotency_key="k2", request_hash="h2", operation="run_study")
        job_id = rec["job_id"]

        store.update_job(job_id, "SUCCEEDED", metadata={"result": "done"})
        assert store.job(job_id)["status"] == "SUCCEEDED"

        # Attempt to overwrite with FAILED
        store.update_job(job_id, "FAILED", metadata={"error": "late crash"})

        # Must remain SUCCEEDED!
        assert store.job(job_id)["status"] == "SUCCEEDED"
        events = [e["event"] for e in store.events(job_id)]
        assert "LateTransitionRejected" in events
    finally:
        store.close()


def test_operation_store_transition_status_cas(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    try:
        rec, _ = store.begin(request_id="r3", idempotency_key="k3", request_hash="h3", operation="run_study")
        job_id = rec["job_id"]

        # CAS: Transition from QUEUED to STARTING -> should succeed
        ok, status, updated = store.transition_status(job_id, "QUEUED", "STARTING")
        assert ok is True
        assert status == "STARTING"
        assert updated["status"] == "STARTING"

        # CAS: Transition from QUEUED (mismatch) to RUNNING -> should fail
        ok, status, updated = store.transition_status(job_id, "QUEUED", "RUNNING")
        assert ok is False
        assert status == "STARTING"

        # CAS: Transition from STARTING to RUNNING -> should succeed
        ok, status, updated = store.transition_status(job_id, {"STARTING"}, "RUNNING")
        assert ok is True
        assert status == "RUNNING"
    finally:
        store.close()


# ===========================================================================
# D04: Force-Stop Backend Lease and Process Tree Termination
# ===========================================================================

def test_scoped_force_stop_rejects_unbound_job_before_selecting_backend(tmp_path):
    mock_service = type("ManagedService", (), {
        "is_shared": False,
        "server_pid": 99998,
        "lease": {"lease_id": "lease-authorized-123"},
        "ledger": SessionLedger("s_mock", "srv_mock"),
    })()

    daemon = ControlDaemon(tmp_path, service=mock_service)
    try:
        rec, _ = daemon.store.begin(request_id="rf1", idempotency_key="kf1", request_hash="hf1", operation="run_study")
        job_id = rec["job_id"]
        daemon.store.update_job(job_id, "RUNNING")

        # Force-stop with incorrect lease_id -> rejected
        res_bad_lease = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {
                "job_id": job_id,
                "force_stop": True,
                "server_scope": {
                    "authorized": True,
                    "lease_id": "lease-wrong",
                    "pid": 99998,
                },
            },
        })
        assert res_bad_lease["success"] is False
        assert res_bad_lease["error"]["code"] == "WORKER_BINDING_UNKNOWN"
    finally:
        daemon.close()


def test_scoped_force_stop_unbound_job_does_not_terminate_default_process(monkeypatch, tmp_path):
    mock_service = type("ManagedService", (), {
        "is_shared": False,
        "server_pid": 99997,
        "ledger": SessionLedger("s_mock", "srv_mock"),
    })()

    daemon = ControlDaemon(tmp_path, service=mock_service)
    try:
        rec, _ = daemon.store.begin(request_id="rf2", idempotency_key="kf2", request_hash="hf2", operation="run_study")
        job_id = rec["job_id"]
        daemon.store.update_job(job_id, "RUNNING")

        terminated = []
        # A legacy job without a durable session/Worker binding must not
        # select this process even when the caller presents its PID.
        monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda pid: {"alive": True, "start_epoch_ms": 1000})
        monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree", lambda pid, timeout_s=5.0: terminated.append(pid) or False)

        res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {
                "job_id": job_id,
                "force_stop": True,
                "server_scope": {"authorized": True, "pid": 99997, "process_start_epoch_ms": 1000},
            },
        })
        assert res["success"] is False
        assert res["error"]["code"] == "WORKER_BINDING_UNKNOWN"
        assert terminated == []
        assert daemon.store.job(job_id)["status"] == "RUNNING"
    finally:
        daemon.close()


def _registered_force_stop_fixture(tmp_path, *, lease_id="lease-authorized-123", process_start_epoch_ms=1000):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    socket = CanonicalSocket("127.0.0.1", 24567)
    pid = 99991
    process = OwnedServerProcessIdentity(
        pid=pid,
        birth=f"synthetic-start-epoch-ms-{process_start_epoch_ms}",
        executable="/synthetic/comsol/server",
        listener_sockets=(socket,),
        start_epoch_ms=process_start_epoch_ms,
    )
    service = type("ManagedService", (), {
        "is_shared": False,
        "server_pid": pid,
        "lease": {"lease_id": lease_id},
        "ledger": SessionLedger("force-stop-session", "force-stop-server"),
    })()
    worker = SimpleNamespace()
    backend = SimpleNamespace(
        service=service,
        worker=worker,
        worker_identity={"connection_epoch": 1, "worker_instance_id": "force-stop-worker",
                         "endpoint": "127.0.0.1:24567"},
        host_permission_ceiling={"inspect", "project_write", "compute", "host_control"},
    )
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, service=service, worker=worker)
    project_response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "force-stop-project", "workspace": "force-stop-project",
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": "force-stop-project-create",
                      "idempotency_key": "force-stop-project-create"},
    })
    assert project_response["success"] is True, project_response
    project = project_response["data"]["project"]["project_id"]
    workspace = Path(project_response["data"]["project"]["workspace"])
    context = SessionRuntimeContext(
        project_id=project,
        session_id="force-stop-session",
        project_root=workspace,
        runtime=SessionRuntimeConfig(
            runtime_id="force-stop-runtime", comsol_version="6.4.0.293",
            installation_root=Path("/synthetic/comsol64"),
            java_executable=Path("/synthetic/comsol64/java/bin/java"),
            classpath=(Path("/synthetic/comsol64/plugins/client.jar"),),
            preferences_dir=tmp_path / "prefs",
            session_state_root=tmp_path / "session-state",
        ),
        endpoint=SessionEndpointIdentity(
            host="127.0.0.1", port=socket.port, worker_epoch=1,
            observed_peer=socket, owned_process=process,
        ),
        backend=backend,
        worker_instance_id="force-stop-worker",
        worker=worker,
        server_ownership="mcp_managed",
        process_identity=process,
    )
    daemon.session_registry.register(context)
    job, reused = daemon.store.begin(
        request_id="force-stop-request", idempotency_key="force-stop-key",
        request_hash="force-stop-hash", operation="study.run",
        metadata={
            "operation": "study.run", "arguments": {},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
            "runtime_binding": {"kind": "registered_session", "project_id": project,
                                "session_id": "force-stop-session", "worker_epoch": 1,
                                "worker_instance_id": "force-stop-worker"},
        },
    )
    assert reused is False
    daemon.store.update_job(job["job_id"], "RUNNING")
    return daemon, service, process, job["job_id"], project


def test_scoped_force_stop_rejects_wrong_registered_backend_lease(tmp_path, monkeypatch):
    daemon, _service, process, job_id, project = _registered_force_stop_fixture(tmp_path)
    terminated = []
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda _pid: {
        "alive": True, "start_epoch_ms": 1000,
    })
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: terminated.append((pid, timeout_s)) or True)
    try:
        result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": job_id, "force_stop": True,
                          "server_scope": {"authorized": True, "lease_id": "lease-wrong",
                                           "pid": process.pid, "process_start_epoch_ms": 1000}},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "UNAUTHORIZED_FORCE_STOP"
        assert terminated == []
        assert daemon.store.job(job_id)["status"] == "RUNNING"
    finally:
        daemon.close()


def test_scoped_force_stop_retains_unknown_when_owned_termination_unconfirmed(tmp_path, monkeypatch):
    daemon, _service, process, job_id, project = _registered_force_stop_fixture(tmp_path)
    calls = []
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda pid: {
        "alive": True, "start_epoch_ms": 1000,
    })
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: calls.append((pid, timeout_s)) or False)
    try:
        result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": job_id, "force_stop": True,
                          "server_scope": {"authorized": True, "lease_id": "lease-authorized-123",
                                           "pid": process.pid, "process_start_epoch_ms": 1000}},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "TERMINATION_FAILED"
        assert calls == [(process.pid, 5.0)]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        assert daemon.store.job(job_id)["metadata"]["mode"] == "TERMINATION_UNCONFIRMED"
    finally:
        daemon.close()


def test_scoped_force_stop_rejects_pid_reuse_even_when_caller_matches_new_birth(tmp_path, monkeypatch):
    daemon, _service, process, job_id, project = _registered_force_stop_fixture(
        tmp_path, process_start_epoch_ms=900,
    )
    terminated = []
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda _pid: {
        "alive": True, "start_epoch_ms": 1000,
    })
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: terminated.append(pid) or True)
    try:
        result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": job_id, "force_stop": True,
                          "server_scope": {"authorized": True, "lease_id": "lease-authorized-123",
                                           "pid": process.pid, "process_start_epoch_ms": 1000}},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "PROCESS_IDENTITY_MISMATCH"
        assert terminated == []
        assert daemon.store.job(job_id)["status"] == "RUNNING"
    finally:
        daemon.close()


def test_scoped_force_stop_refuses_missing_backend_owned_birth(tmp_path, monkeypatch):
    daemon, _service, process, job_id, project = _registered_force_stop_fixture(
        tmp_path, process_start_epoch_ms=None,
    )
    terminated = []
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda _pid: {
        "alive": True, "start_epoch_ms": 1000,
    })
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: terminated.append(pid) or True)
    try:
        result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": job_id, "force_stop": True,
                          "server_scope": {"authorized": True, "lease_id": "lease-authorized-123",
                                           "pid": process.pid, "process_start_epoch_ms": 1000}},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "PROCESS_IDENTITY_UNKNOWN"
        assert terminated == []
        assert daemon.store.job(job_id)["status"] == "RUNNING"
    finally:
        daemon.close()


def test_scoped_force_stop_accepts_exact_backend_owned_birth_match(tmp_path, monkeypatch):
    daemon, _service, process, job_id, project = _registered_force_stop_fixture(tmp_path)
    original_identity = daemon.session_registry.get(project, "force-stop-session")
    process_context = original_identity.endpoint.owned_process
    assert process_context.start_epoch_ms == 1000
    calls = []
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity", lambda _pid: {
        "alive": True, "start_epoch_ms": 1000,
    })
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: calls.append(pid) or True)
    try:
        result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": job_id, "force_stop": True,
                          "server_scope": {"authorized": True, "lease_id": "lease-authorized-123",
                                           "pid": process.pid, "process_start_epoch_ms": 1000}},
            "execution": {"project_id": project, "session_id": "force-stop-session"},
        })
        assert result["success"] is True, result
        assert calls == [process.pid]
        assert daemon.store.job(job_id)["status"] == "CANCELLED"
    finally:
        daemon.close()


# ===========================================================================
# D05: Version and Compilation Cache Fingerprinting
# ===========================================================================

def test_compilation_cache_fingerprint_isolation(tmp_path):
    root63 = tmp_path / "COMSOL63" / "Multiphysics"
    root64 = tmp_path / "COMSOL64" / "Multiphysics"
    (root63 / "bin").mkdir(parents=True)
    (root64 / "bin").mkdir(parents=True)
    (root63 / "bin" / "comsolclientpath.txt").write_text("v63.jar\n")
    (root64 / "bin" / "comsolclientpath.txt").write_text("v64.jar\n")
    jdk = tmp_path / "jdk11"
    (jdk / "bin").mkdir(parents=True)
    (jdk / "bin" / "javac").write_bytes(b"")
    (jdk / "bin" / "java").write_bytes(b"")

    paths63 = java_worker.JavaWorkerPaths(root63, jdk, project_root=tmp_path)
    paths64 = java_worker.JavaWorkerPaths(root64, jdk, project_root=tmp_path)

    source = b"class PersistentComsolWorker {}"
    key63, receipt63 = paths63.compilation_cache_fingerprint(source, "sha_manifest_63")
    key64, receipt64 = paths64.compilation_cache_fingerprint(source, "sha_manifest_64")

    # Cache keys must be completely different!
    assert key63 != key64
    assert receipt63["classpath_manifest_sha256"] != receipt64["classpath_manifest_sha256"]
    assert "-encoding" in receipt63["javac_flags"]
    assert "UTF-8" in receipt63["javac_flags"]


# ===========================================================================
# D06: Windows Lifecycle & Filesystem Boundaries
# ===========================================================================

def test_validate_windows_path_security_ads_rejected():
    with pytest.raises(ValueError, match="Alternate Data Stream"):
        validate_windows_path_security("data/model.mph:hidden_stream")


def test_validate_windows_path_security_reserved_devices():
    for dev in ("CON", "prn", "AUX", "nul", "COM1", "lpt3", "con.txt", "aux.dat"):
        with pytest.raises(ValueError, match="reserved device"):
            validate_windows_path_security(f"projects/{dev}")


def test_validate_windows_path_security_trailing_dots_spaces():
    with pytest.raises(ValueError, match="Trailing dot or space"):
        validate_windows_path_security("projects/folder. /file.txt")
    with pytest.raises(ValueError, match="Trailing dot or space"):
        validate_windows_path_security("projects/folder./file.txt")


def test_validate_windows_path_security_unc_rejected():
    with pytest.raises(ValueError, match="UNC network paths are rejected"):
        validate_windows_path_security(r"\\remote-server\share\model.mph")


def test_set_private_directory_permissions(tmp_path):
    priv_dir = tmp_path / "private_run"
    set_private_directory_permissions(priv_dir)
    assert priv_dir.is_dir()
