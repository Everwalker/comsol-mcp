from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionContextMissing,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    current_session_context,
)
from comsol_mcp._session_lifecycle import SessionLifecycleProjectConflict, new_lifecycle_record


def _daemon(tmp_path: Path, *, registry=None) -> ControlDaemon:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True)
    return ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        registry=registry if registry is not None else {},
    )


def _create_project(daemon: ControlDaemon, label: str) -> dict:
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": label,
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
    })
    assert response["success"] is True, response
    return response["data"]["project"]


def _record(daemon: ControlDaemon, project_id: str, session_id: str, *, state="CONNECTED") -> dict:
    return daemon.session_lifecycle.save(new_lifecycle_record(
        project_id=project_id,
        session_id=session_id,
        state=state,
        runtime_id="comsol-install:%2Fopt%2Fcomsol64",
        endpoint={"host": "127.0.0.1", "port": 2046},
        client_state="CONNECTED" if state == "CONNECTED" else "DISCONNECTED",
        server_state="SHARED",
        server_ownership="shared",
        worker_instance_id="worker-fixture",
        worker_epoch=3,
        server_instance_id="server-connection-epoch-only",
        health={"status": "HEALTHY", "observed_at": "2026-09-27T00:00:00Z", "source": "cached-worker-health"},
    ))


def test_session_lifecycle_reads_are_project_scoped_and_wrapper_equivalent(tmp_path):
    daemon = _daemon(tmp_path)
    try:
        project = _create_project(daemon, "session-reads")
        _record(daemon, project["project_id"], "session-a")

        direct = daemon.dispatch({
            "operation": "session.list",
            "arguments": {"project_id": project["project_id"], "filter": {}},
            "execution": {},
        })
        wrapped = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "session.list", "arguments": {"filter": {}}},
            "execution": {"project_id": project["project_id"]},
        })
        assert direct["success"] is True
        assert wrapped["success"] is True
        assert direct["data"] == wrapped["data"]
        assert direct["data"]["count"] == 1
        assert direct["data"]["sessions"][0]["lifecycle"]["session_id"] == "session-a"
        assert direct["data"]["sessions"][0]["runtime_live"] is False

        inspected = daemon.dispatch({
            "operation": "session.inspect",
            "arguments": {"project_id": project["project_id"], "session_id": "session-a"},
            "execution": {},
        })
        assert inspected["success"] is True
        assert inspected["data"]["lifecycle"]["server_instance_id"] == "server-connection-epoch-only"
        assert inspected["data"]["runtime_live"] is False

        health = daemon.dispatch({
            "operation": "session.health",
            "arguments": {"project_id": project["project_id"], "session_id": "session-a"},
            "execution": {},
        })
        assert health["success"] is True
        assert health["data"]["freshness"] == "CACHED"
        assert health["data"]["worker_rpc_performed"] is False
    finally:
        daemon.close()


def test_session_inspect_rejects_foreign_project_and_unknown_fields(tmp_path):
    daemon = _daemon(tmp_path)
    try:
        owner = _create_project(daemon, "session-owner")
        foreign = _create_project(daemon, "session-foreign")
        _record(daemon, owner["project_id"], "private-session")

        foreign_response = daemon.dispatch({
            "operation": "session.inspect",
            "arguments": {"project_id": foreign["project_id"], "session_id": "private-session"},
            "execution": {},
        })
        assert foreign_response["success"] is False
        assert foreign_response["error"]["code"] == "PROJECT_IDENTITY_MISMATCH"

        malformed = daemon.dispatch({
            "operation": "session.list",
            "arguments": {"project_id": owner["project_id"], "secret": "must-not-be-ignored"},
            "execution": {},
        })
        assert malformed["success"] is False
        assert malformed["error"]["code"] == "INVALID_REQUEST"

        not_implemented = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {
                "project_id": owner["project_id"],
                "idempotency_key": "connect-1",
                "runtime_id": "comsol-install:%2Fopt%2Fcomsol64",
                "endpoint": {"host": "127.0.0.1", "port": 2046},
            },
            "execution": {},
        })
        assert not_implemented["success"] is False
        assert not_implemented["error"]["code"] == "UNSUPPORTED_OPERATION"
    finally:
        daemon.close()


def _runtime_context(project_id: str, session_id: str, *, pid: int, port: int, worker: object, backend=None):
    socket = CanonicalSocket("127.0.0.1", port)
    process = OwnedServerProcessIdentity(
        pid=pid,
        birth=f"test-birth-{pid}",
        executable="/opt/comsol/server",
        listener_sockets=(socket,),
        start_epoch_ms=123456,
    )
    runtime = SessionRuntimeConfig(
        runtime_id=f"runtime-{pid}",
        comsol_version="6.4.0.293",
        installation_root=Path("/opt/comsol64"),
        java_executable=Path("/opt/comsol64/java/bin/java"),
        classpath=(Path("/opt/comsol64/plugins/client.jar"),),
        preferences_dir=Path(f"/private/tmp/prefs-{pid}"),
        session_state_root=Path(f"/private/tmp/session-state-{pid}"),
    )
    if backend is None:
        backend = SimpleNamespace(
            name=session_id,
            service=SimpleNamespace(ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"})),
            host_permission_ceiling={"inspect", "project_write", "compute"},
            worker=worker,
        )
    return SessionRuntimeContext(
        project_id=project_id,
        session_id=session_id,
        project_root=Path(f"/private/tmp/workspace-{project_id}"),
        runtime=runtime,
        endpoint=SessionEndpointIdentity(
            host="127.0.0.1", port=port, worker_epoch=1,
            observed_peer=socket, owned_process=process,
        ),
        backend=backend,
        worker_instance_id=f"worker-{pid}",
        worker=worker,
        server_ownership="mcp_managed",
        process_identity=process,
    )


def _request(project_id: str, session_id: str, key: str):
    return {
        "operation": "test_session_engine_action",
        "arguments": {},
        "execution": {
            "project_id": project_id,
            "session_id": session_id,
            "request_id": f"request-{key}",
            "idempotency_key": key,
        },
    }


def _engine_daemon(tmp_path: Path):
    daemon = _daemon(tmp_path, registry={"test_session_engine_action": lambda _args: None})
    project = _create_project(daemon, "session-engine")
    return daemon, project["project_id"]


class _InvocationBackend:
    """Narrow backend double; ControlDaemon._execute remains production code."""

    def __init__(self, name: str, *, worker=None, service=None):
        self.name = name
        self.worker = worker
        self.service = service or SimpleNamespace(
            ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"}),
        )
        self.host_permission_ceiling = {"inspect", "project_write", "compute"}
        self.registry = {"test_session_engine_action": lambda _arguments: None}
        self.project_root_explicit = True
        self.project_root = Path("/private/tmp") / f"{name}-project-root"
        self.worker_identity = None
        self.invoke_hook = None
        self.calls = []

    def project_root_scope(self, _root):
        return nullcontext()

    def model_project_binding(self, _model_ref):
        return None

    def apply_timeout_caps(self, _project_id, timeouts):
        return timeouts

    def invoke(self, operation, arguments, _execution, _operation_id, _worker_event):
        try:
            context = current_session_context()
        except SessionContextMissing:
            context = None
        self.calls.append((operation, context))
        if self.invoke_hook is not None:
            return self.invoke_hook(operation, arguments, context)
        return {"success": True, "data": {"backend": self.name,
                                            "session_id": context.session_id if context else None}}

    def close(self):
        pass


def test_control_daemon_routes_distinct_owned_servers_concurrently(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    barrier = threading.Barrier(2)
    try:
        first = _runtime_context(project_id, "session-6.3", pid=6103, port=2603, worker=object())
        second = _runtime_context(project_id, "session-6.4", pid=6104, port=2604, worker=object())
        daemon.session_registry.register(first)
        daemon.session_registry.register(second)
        monkeypatch.setitem(
            __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
            "test_session_engine_action", "inspect",
        )
        original_execute = daemon._execute

        def execute(record, operation, arguments, execution, timeouts, submitted, **kwargs):
            context = current_session_context()
            barrier.wait(timeout=2)
            return daemon._finish(record, {
                "success": True,
                "data": {"session_id": context.session_id, "backend": context.backend.name},
            }, "SUCCEEDED")

        daemon._execute = execute
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "distinct-one"))
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "distinct-two"))
            result_one, result_two = one.result(timeout=4), two.result(timeout=4)
        assert result_one["success"] is True
        assert result_two["success"] is True
        assert {result_one["data"]["session_id"], result_two["data"]["session_id"]} == {first.session_id, second.session_id}
    finally:
        daemon.close()


def test_control_daemon_serializes_same_server_across_sessions(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    try:
        first = _runtime_context(project_id, "session-a", pid=6110, port=2610, worker=object())
        second = _runtime_context(project_id, "session-b", pid=6110, port=2610, worker=object())
        daemon.session_registry.register(first)
        daemon.session_registry.register(second)
        monkeypatch.setitem(
            __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
            "test_session_engine_action", "inspect",
        )

        def execute(record, _operation, _arguments, execution, _timeouts, _submitted, **_kwargs):
            context = current_session_context()
            if context.session_id == first.session_id:
                entered.set()
                assert release.wait(timeout=3)
            else:
                second_entered.set()
            return daemon._finish(record, {"success": True, "data": {"session_id": context.session_id}}, "SUCCEEDED")

        daemon._execute = execute
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "same-one"))
            assert entered.wait(timeout=2)
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "same-two"))
            assert not second_entered.wait(timeout=0.15)
            release.set()
            assert one.result(timeout=2)["success"] is True
            assert two.result(timeout=2)["success"] is True
            assert second_entered.is_set()
    finally:
        release.set()
        daemon.close()


def test_control_daemon_production_execute_routes_to_each_session_backend(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    barrier = threading.Barrier(2)
    first_backend = _InvocationBackend("session-6.3")
    second_backend = _InvocationBackend("session-6.4")
    default_backend = _InvocationBackend("default")
    daemon._default_backend = default_backend
    first = _runtime_context(project_id, "session-6.3", pid=6203, port=2703,
                             worker=object(), backend=first_backend)
    second = _runtime_context(project_id, "session-6.4", pid=6204, port=2704,
                              worker=object(), backend=second_backend)
    daemon.session_registry.register(first)
    daemon.session_registry.register(second)
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )

    def overlapping(operation, _arguments, context):
        assert operation == "test_session_engine_action"
        assert context is not None
        barrier.wait(timeout=3)
        return {"success": True, "data": {"backend": current_session_context().backend.name}}

    first_backend.invoke_hook = overlapping
    second_backend.invoke_hook = overlapping
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            one = callers.submit(daemon.dispatch, _request(project_id, first.session_id, "invoke-one"))
            two = callers.submit(daemon.dispatch, _request(project_id, second.session_id, "invoke-two"))
            result_one, result_two = one.result(timeout=5), two.result(timeout=5)
        assert result_one["success"] is True
        assert result_two["success"] is True
        assert {result_one["data"]["backend"], result_two["data"]["backend"]} == {"session-6.3", "session-6.4"}
        assert default_backend.calls == []
        assert first_backend.calls[0][1] is first
        assert second_backend.calls[0][1] is second
    finally:
        daemon.close()


def test_default_dispatch_cannot_overlap_registered_unknown_endpoint(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    default_entered = threading.Event()
    release_default = threading.Event()
    registered_entered = threading.Event()
    default_backend = _InvocationBackend("default")
    registered_backend = _InvocationBackend("registered-unknown")
    daemon._default_backend = default_backend
    context = _runtime_context(project_id, "session-unknown", pid=6301, port=2801,
                               worker=object(), backend=registered_backend)
    context = replace(
        context,
        endpoint=SessionEndpointIdentity("127.0.0.1", 2801, 1, observed_peer=None),
        server_ownership="shared",
        process_identity=None,
    )
    daemon.session_registry.register(context)
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )

    def hold_default(_operation, _arguments, _context):
        default_entered.set()
        assert release_default.wait(timeout=3)
        return {"success": True, "data": {"lane": "default"}}

    def registered_call(_operation, _arguments, _context):
        registered_entered.set()
        return {"success": True, "data": {"lane": "registered"}}

    default_backend.invoke_hook = hold_default
    registered_backend.invoke_hook = registered_call
    try:
        default_request = {
            "operation": "test_session_engine_action", "arguments": {},
            "execution": {"project_id": project_id, "request_id": "default-gated", "idempotency_key": "default-gated"},
        }
        with ThreadPoolExecutor(max_workers=2) as callers:
            first = callers.submit(daemon.dispatch, default_request)
            assert default_entered.wait(timeout=2)
            second = callers.submit(daemon.dispatch, _request(project_id, context.session_id, "registered-gated"))
            assert not registered_entered.wait(timeout=0.2)
            release_default.set()
            assert first.result(timeout=3)["success"] is True
            assert second.result(timeout=3)["success"] is True
        assert registered_entered.is_set()
        assert default_backend.calls[0][1] is None
        assert registered_backend.calls[0][1] is context
    finally:
        release_default.set()
        daemon.close()


def test_job_reconcile_uses_durable_registered_worker_epoch(tmp_path):
    class Worker:
        def __init__(self, name):
            self.name = name
            self.calls = []

        def status(self, identifier):
            self.calls.append(identifier)
            return {"request_id": identifier, "status": "SUCCEEDED"}

    daemon, project_id = _engine_daemon(tmp_path)
    default_worker = Worker("default")
    target_worker = Worker("target")
    daemon._default_backend = _InvocationBackend("default", worker=default_worker)
    target_backend = _InvocationBackend("target", worker=target_worker)
    context = _runtime_context(project_id, "session-target", pid=6401, port=2901,
                               worker=target_worker, backend=target_backend)
    daemon.session_registry.register(context)
    try:
        binding = {
            "kind": "registered_session", "project_id": project_id,
            "session_id": context.session_id, "worker_epoch": context.worker_epoch,
            "worker_instance_id": context.worker_instance_id,
        }
        record, _ = daemon.store.begin(
            request_id="bound-job-request", idempotency_key="bound-job-key",
            request_hash="bound-job-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": binding},
        )
        job_id = record["job_id"]
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "bound-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(job_id, "UNKNOWN")

        result = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": job_id}, "execution": {},
        })
        assert result["success"] is True
        assert result["data"]["metadata"]["reconciled_quiescent"] is True
        assert target_worker.calls == ["bound-worker-call"]
        assert default_worker.calls == []

        mismatch_binding = {**binding, "worker_instance_id": "different-instance-same-epoch"}
        mismatch, _ = daemon.store.begin(
            request_id="mismatch-request", idempotency_key="mismatch-key",
            request_hash="mismatch-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": mismatch_binding},
        )
        daemon.store.add_event(mismatch["job_id"], "worker_request", {
            "request_id": "mismatch-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(mismatch["job_id"], "UNKNOWN")
        mismatch_result = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": mismatch["job_id"]}, "execution": {},
        })
        assert mismatch_result["success"] is False
        assert mismatch_result["error"]["code"] == "WORKER_BINDING_MISMATCH"
        assert target_worker.calls == ["bound-worker-call"]
    finally:
        daemon.close()


def test_unbound_job_worker_access_fails_closed_and_force_stop_uses_bound_backend(tmp_path, monkeypatch):
    class Worker:
        def __init__(self):
            self.calls = []

        def status(self, identifier):
            self.calls.append(identifier)
            return {"request_id": identifier, "status": "RUNNING"}

    daemon, project_id = _engine_daemon(tmp_path)
    default_worker = Worker()
    default_service = SimpleNamespace(is_shared=False, server_pid=6500, ledger=SimpleNamespace(session_id="default-session"))
    daemon._default_backend = _InvocationBackend("default", worker=default_worker, service=default_service)
    target_worker = Worker()
    shared_service = SimpleNamespace(is_shared=True, server_pid=6501, ledger=SimpleNamespace(session_id="target-session"))
    target_backend = _InvocationBackend("target", worker=target_worker, service=shared_service)
    context = _runtime_context(project_id, "target-session", pid=6501, port=3001,
                               worker=target_worker, backend=target_backend)
    daemon.session_registry.register(context)
    termination_calls = []
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda pid, timeout_s=5.0: termination_calls.append(pid) or True)
    try:
        unbound, _ = daemon.store.begin(
            request_id="unbound-request", idempotency_key="unbound-key",
            request_hash="unbound-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {}, "execution": {}},
        )
        daemon.store.add_event(unbound["job_id"], "worker_request", {
            "request_id": "unbound-worker-call", "phase": "submitted",
        })
        daemon.store.update_job(unbound["job_id"], "UNKNOWN")
        refused = daemon.dispatch({
            "operation": "job.reconcile", "arguments": {"job_id": unbound["job_id"]}, "execution": {},
        })
        assert refused["success"] is False
        assert refused["error"]["code"] == "WORKER_BINDING_UNKNOWN"
        assert default_worker.calls == [] and target_worker.calls == []

        binding = {
            "kind": "registered_session", "project_id": project_id,
            "session_id": context.session_id, "worker_epoch": context.worker_epoch,
            "worker_instance_id": context.worker_instance_id,
        }
        active, _ = daemon.store.begin(
            request_id="force-stop-request", idempotency_key="force-stop-key",
            request_hash="force-stop-hash", operation="study.run",
            metadata={"operation": "study.run", "arguments": {},
                      "execution": {"project_id": project_id, "session_id": context.session_id},
                      "runtime_binding": binding},
        )
        daemon.store.update_job(active["job_id"], "RUNNING")
        force_result = daemon.dispatch({
            "operation": "job.cancel",
            "arguments": {"job_id": active["job_id"], "force_stop": True,
                          "server_scope": {"authorized": True, "pid": 6500}},
            "execution": {},
        })
        assert force_result["success"] is False
        assert force_result["error"]["code"] == "CANNOT_TERMINATE_SHARED_SERVER"
        assert termination_calls == []
    finally:
        daemon.close()


def test_control_reconcile_and_force_stop_do_not_wait_behind_busy_backend_invoke(tmp_path, monkeypatch):
    daemon, project_id = _engine_daemon(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class Worker:
        def __init__(self):
            self.status_calls = []

        def status(self, identifier):
            self.status_calls.append(identifier)
            return {"request_id": identifier, "status": "SUCCEEDED"}

    worker = Worker()
    service = SimpleNamespace(
        is_shared=False,
        server_pid=6601,
        ledger=SimpleNamespace(permissions={"inspect", "project_write", "compute"}, session_id="session-control"),
    )
    backend = _InvocationBackend("session-control", worker=worker, service=service)
    context = _runtime_context(project_id, "session-control", pid=6601, port=3101,
                               worker=worker, backend=backend)
    daemon.session_registry.register(context)
    daemon._default_backend = _InvocationBackend("default")
    monkeypatch.setitem(
        __import__("comsol_mcp._g2_registry", fromlist=["LEGACY_TOOL_EFFECTS"]).LEGACY_TOOL_EFFECTS,
        "test_session_engine_action", "inspect",
    )
    monkeypatch.setattr("comsol_mcp._control_daemon.process_identity",
                        lambda _pid: {"alive": True, "start_epoch_ms": 123456})
    monkeypatch.setattr("comsol_mcp._control_daemon.terminate_process_tree",
                        lambda _pid, timeout_s=5.0: True)
    backend.invoke_hook = lambda *_args: (
        entered.set(), release.wait(timeout=4), {"success": True, "data": {}}
    )[-1]

    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            engine_call = caller.submit(daemon.dispatch, _request(project_id, context.session_id, "busy-control"))
            assert entered.wait(timeout=2)
            running_job = None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with daemon.store.lock:
                    row = daemon.store.db.execute(
                        "SELECT j.job_id FROM jobs j JOIN operations o USING(operation_id) WHERE o.idempotency_key=?",
                        ("busy-control",),
                    ).fetchone()
                if row:
                    running_job = daemon.store.job(row["job_id"])
                    break
                time.sleep(0.01)
            assert running_job is not None
            job_id = running_job["job_id"]

            binding = running_job["metadata"]["runtime_binding"]
            control_job, _ = daemon.store.begin(
                request_id="parallel-status-request", idempotency_key="parallel-status-key",
                request_hash="parallel-status-hash", operation="study.run",
                metadata={"operation": "study.run", "arguments": {},
                          "execution": {"project_id": project_id, "session_id": context.session_id},
                          "runtime_binding": binding},
            )
            daemon.store.add_event(control_job["job_id"], "worker_request", {
                "request_id": "parallel-status-call", "phase": "submitted",
            })
            daemon.store.update_job(control_job["job_id"], "UNKNOWN")
            started = time.monotonic()
            reconciled = daemon.dispatch({
                "operation": "job.reconcile", "arguments": {"job_id": control_job["job_id"]}, "execution": {},
            })
            assert time.monotonic() - started < 0.5
            assert reconciled["success"] is True
            assert worker.status_calls == ["parallel-status-call"]

            started = time.monotonic()
            stopped = daemon.dispatch({
                "operation": "job.cancel",
                "arguments": {"job_id": job_id, "force_stop": True,
                              "server_scope": {"authorized": True, "pid": 6601,
                                               "process_start_epoch_ms": 123456}},
                "execution": {},
            })
            assert time.monotonic() - started < 0.5
            assert stopped["success"] is True
            assert stopped["data"]["engine_stopped"] is True
            assert entered.is_set() and not release.is_set()
            assert daemon.store.job(job_id)["status"] == "CANCELLED"
            after_fence = daemon.dispatch(_request(project_id, context.session_id, "after-force-stop"))
            assert after_fence["success"] is False
            assert after_fence["error"]["code"] == "WORKER_RETIRED_OR_UNKNOWN"
            assert len(backend.calls) == 1
            release.set()
            # The request was already accepted as a pending RPC; its durable
            # terminal cancellation must survive the late fake callback.
            engine_call.result(timeout=3)
            assert daemon.store.job(job_id)["status"] == "CANCELLED"
    finally:
        release.set()
        daemon.close()
