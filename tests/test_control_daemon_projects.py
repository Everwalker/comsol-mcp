from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Snapshot:
    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "test-server",
            "fingerprint": "test-fingerprint",
            "external_event_counter": 0,
        }


class _JavaCreatedModel:
    def __init__(self, model_tag="created-model"):
        self.model_tag = model_tag

    def tag(self):
        return self.model_tag


class _CreatedModel:
    def __init__(self, model_tag="created-model"):
        self.java = _JavaCreatedModel(model_tag)


class _WorkerClient:
    def model(self, tag):
        assert tag in {"created-model", "loaded-model"}
        return _CreatedModel(tag)


class _Worker:
    def client(self):
        return _WorkerClient()

    def operation_context(self, *_args, **_kwargs):
        from contextlib import nullcontext

        return nullcontext()


def _create_project(daemon: ControlDaemon, name: str) -> dict:
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": name,
            "workspace": name,
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"create-{name}", "idempotency_key": f"create-{name}"},
    })
    assert result["success"] is True
    return result["data"]["project"]


def test_project_create_is_offline_and_uses_the_registered_workspace(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    try:
        result = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "offline-project",
                "workspace": "science/offline-project",
                "policy": {"permissions": ["inspect", "project_write"]},
            },
            "execution": {"request_id": "offline-create", "idempotency_key": "offline-create"},
        })
        project = result["data"]["project"]
        assert result["success"] is True
        assert project["workspace"] == str(project_root / "science" / "offline-project")
        assert Path(project["workspace"]).is_dir()
        assert daemon.backend.worker is None
        assert daemon.service is None
        job = daemon.store.job(result["execution"]["job_id"])
        assert job["metadata"]["engine_dispatched"] is False
    finally:
        daemon.close()


def test_project_inspect_keeps_execution_idempotency_out_of_domain_arguments(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project = _create_project(daemon, "inspect-envelope")
    try:
        inspected = daemon.dispatch({
            "operation": "project.inspect",
            "arguments": {"project_id": project["project_id"]},
            "execution": {
                "request_id": "inspect-envelope-read",
                "idempotency_key": "inspect-envelope-key",
                "project_id": project["project_id"],
            },
        })
        assert inspected["success"] is True
        assert inspected["data"]["project"]["project_id"] == project["project_id"]
        record = daemon.store.get_operation(inspected["execution"]["operation_id"])
        assert record["metadata"]["execution"]["idempotency_key"] == "inspect-envelope-key"
        assert "idempotency_key" not in record["metadata"]["arguments"]

        refused = daemon.dispatch({
            "operation": "project.inspect",
            "arguments": {"project_id": project["project_id"], "unexpected": True},
            "execution": {"request_id": "inspect-envelope-unknown", "idempotency_key": "inspect-envelope-unknown"},
        })
        assert refused["success"] is False
        assert refused["error"]["code"] == "INVALID_REQUEST"
    finally:
        daemon.close()


def test_quiescence_readback_covers_shared_worker_across_projects_and_rpc_evidence(tmp_path):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    service = ExecutionService(
        SessionLedger("shared-session", "shared-epoch"), Snapshot(), project_root=workspace_root,
    )
    worker = _Worker()
    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, service=service,
        registry={}, worker=worker,
    )
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime",
        "worker_instance_id": "shared-worker-instance",
        "connection_epoch": 4,
        "server_instance_id": "shared-connection-epoch",
    }
    first = _create_project(daemon, "quiescence-first")
    second = _create_project(daemon, "quiescence-second")
    try:
        record, reused = daemon.store.begin(
            request_id="cross-project-worker-request",
            idempotency_key="cross-project-worker-request",
            request_hash="cross-project-worker-hash",
            operation="model.inspect",
            metadata={"operation": "model.inspect", "arguments": {}, "execution": {
                "project_id": second["project_id"], "session_id": "shared-session",
            }},
            timeouts={"rpc_timeout_s": 30},
        )
        assert reused is False
        daemon.store.update_job(record["job_id"], "RUNNING")
        daemon.store.add_event(record["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "direct-rpc-1", "kind": "call",
        })

        busy = daemon.quiescence_readback(first["project_id"], "shared-session")
        data = busy["data"]
        assert data["status"] == data["worker_status"] == "BUSY"
        assert data["scope"] == "exact-daemon-worker+all-projects+durable-direct-rpc"
        assert data["counts"]["requested_project_session_jobs"]["RUNNING"] == 0
        assert data["counts"]["worker_jobs"]["RUNNING"] == 1
        assert data["counts"]["direct_rpc"]["pending"] == 1
        assert data["server_lane_status"] == "BUSY"
        assert data["safe_to_stop_server"] is False

        daemon.store.add_event(record["job_id"], "worker_request", {
            "phase": "observed", "request_id": "direct-rpc-1", "kind": "call",
            "reply": {"status": "SUCCEEDED"},
        })
        daemon.store.update_job(record["job_id"], "SUCCEEDED")
        quiet = daemon.quiescence_readback(first["project_id"], "shared-session")
        assert quiet["data"]["worker_status"] == "QUIESCENT"
        assert quiet["data"]["counts"]["direct_rpc"]["pending"] == 0
        assert quiet["data"]["checks"]["all_projects_in_daemon_ledger_scanned"] is True
        assert quiet["data"]["server_lane_status"] == "UNOWNED_OR_UNKNOWN"
        assert quiet["data"]["safe_to_retire_worker"] is True
        assert quiet["data"]["safe_to_stop_server"] is False

        unknown_record, unknown_reused = daemon.store.begin(
            request_id="cross-project-unknown-request",
            idempotency_key="cross-project-unknown-request",
            request_hash="cross-project-unknown-hash",
            operation="model.inspect",
            metadata={"operation": "model.inspect", "arguments": {}, "execution": {
                "project_id": second["project_id"], "session_id": "shared-session",
            }},
            timeouts={"rpc_timeout_s": 30},
        )
        assert unknown_reused is False
        daemon.store.update_job(unknown_record["job_id"], "RUNNING")
        daemon.store.update_job(unknown_record["job_id"], "UNKNOWN")
        unknown = daemon.quiescence_readback(first["project_id"], "shared-session")
        assert unknown["data"]["worker_status"] == "UNKNOWN"
        assert unknown["data"]["counts"]["worker_jobs"]["UNKNOWN"] == 1
        assert unknown["data"]["safe_to_retire_worker"] is False
    finally:
        daemon.close()


class _OwnedChild:
    def __init__(self, pid=123456):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


def test_worker_retirement_fence_rejects_new_submission_until_exact_child_exit(tmp_path):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    service = ExecutionService(
        SessionLedger("retire-session", "retire-epoch"), Snapshot(), project_root=workspace_root,
    )
    worker = _Worker()
    worker._process = _OwnedChild()
    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, service=service,
        registry={"configure_single_main_workflow": lambda args: {"success": True, "data": {"reached": True}}},
        worker=worker,
    )
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime",
        "worker_instance_id": "retiring-worker",
        "connection_epoch": 2,
        "server_instance_id": "retiring-epoch",
    }
    project = _create_project(daemon, "retirement-fence")
    try:
        with daemon.worker_retirement_guard(project["project_id"], "retire-session") as lease:
            assert lease.readback["data"]["worker_status"] == "QUIESCENT"
            refused = daemon.dispatch({
                "operation": "configure_single_main_workflow",
                "arguments": {"current_main_model_path": "workflow.mph"},
                "execution": {"project_id": project["project_id"], "request_id": "during-retirement", "idempotency_key": "during-retirement"},
            })
            assert refused["success"] is False
            assert refused["error"]["code"] == "WORKER_RETIREMENT_IN_PROGRESS"
            assert refused["data"]["engine_dispatched"] is False
            lease.mark_cleanup_started()
            worker._process.returncode = 0
            lease.confirm_worker_stopped()

        after = daemon.dispatch({
            "operation": "configure_single_main_workflow",
            "arguments": {"current_main_model_path": "workflow.mph"},
            "execution": {"project_id": project["project_id"], "request_id": "after-retirement", "idempotency_key": "after-retirement"},
        })
        assert after["success"] is False
        assert after["error"]["code"] == "WORKER_RETIRED_OR_UNKNOWN"
        assert after["data"]["worker_retirement_state"] == "RETIRED"
    finally:
        daemon.close()


def test_worker_retirement_fence_is_revoked_when_cleanup_never_starts(tmp_path):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    service = ExecutionService(
        SessionLedger("retire-abort-session", "retire-abort-epoch"), Snapshot(), project_root=workspace_root,
    )
    worker = _Worker()
    worker._process = _OwnedChild(pid=123457)
    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, service=service,
        registry={"configure_single_main_workflow": lambda _args: {"success": True, "data": {"reached": True}}},
        worker=worker,
    )
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime",
        "worker_instance_id": "retiring-worker",
        "connection_epoch": 2,
        "server_instance_id": "retiring-epoch",
    }
    project = _create_project(daemon, "retirement-revoked")
    try:
        with daemon.worker_retirement_guard(project["project_id"], "retire-abort-session") as lease:
            assert lease.readback["data"]["worker_status"] == "QUIESCENT"
            # An operator can abort before any cleanup action; the gate reopens.
        assert daemon._worker_retirement_state == "OPEN"
        result = daemon.dispatch({
            "operation": "configure_single_main_workflow",
            "arguments": {"current_main_model_path": "workflow.mph"},
            "execution": {"project_id": project["project_id"], "request_id": "after-abort", "idempotency_key": "after-abort"},
        })
        assert result["success"] is True
        assert result["data"]["reached"] is True
    finally:
        daemon.close()


def test_project_scoped_legacy_path_is_confined_to_the_registered_child_workspace(tmp_path):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    calls: list[dict] = []
    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        registry={"configure_single_main_workflow": lambda args: calls.append(args) or {"success": True, "data": args}},
    )
    first = _create_project(daemon, "first")
    second = _create_project(daemon, "second")
    try:
        result = daemon.dispatch({
            "operation": "configure_single_main_workflow",
            "arguments": {"current_main_model_path": "model.mph"},
            "execution": {"project_id": first["project_id"], "request_id": "path-in", "idempotency_key": "path-in"},
        })
        assert result["success"] is True
        assert calls == [{"current_main_model_path": str(Path(first["workspace"]) / "model.mph")}]

        refused = daemon.dispatch({
            "operation": "configure_single_main_workflow",
            "arguments": {"current_main_model_path": str(Path(second["workspace"]) / "other.mph")},
            "execution": {"project_id": first["project_id"], "request_id": "path-out", "idempotency_key": "path-out"},
        })
        assert refused["success"] is False
        assert refused["error"]["code"] == "PERMISSION_DENIED"
        assert len(calls) == 1
    finally:
        daemon.close()


def test_managed_model_creation_persists_project_binding_and_rejects_foreign_project(tmp_path, monkeypatch):
    import json

    from comsol_mcp import _server
    from comsol_mcp import _state

    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    legacy_home = tmp_path / "legacy-home"
    legacy_home.mkdir()
    legacy_workflow = legacy_home / "workflow_state.json"
    legacy_main_path = tmp_path / "unscoped-main.mph"
    original_legacy_state = {
        **_state._default_workflow_state(),
        "current_main_model_path": str(legacy_main_path),
        "main_model_path": str(legacy_main_path),
        "snapshot_dir": str(tmp_path / "unscoped-snapshots"),
    }
    legacy_workflow.write_text(json.dumps(original_legacy_state), encoding="utf-8")
    original_legacy_bytes = legacy_workflow.read_bytes()
    monkeypatch.setattr(_state, "WORKFLOW_FILE", legacy_workflow)
    service = ExecutionService(SessionLedger("session", "test-server"), Snapshot(), project_root=workspace_root)

    loaded_paths: list[str] = []
    created_names: list[str] = []

    def create_model(arguments):
        name = str(arguments.get("model_name") or "created-model")
        created_names.append(name)
        monkeypatch.setattr(_server, "_current_model", _CreatedModel(name), raising=False)
        return {"success": True, "data": {"model_tag": name}}

    def load_model(arguments):
        loaded_paths.append(arguments["path"])
        monkeypatch.setattr(_server, "_current_model", _CreatedModel("loaded-model"), raising=False)
        return {"success": True, "data": {"model_tag": "loaded-model", "requested_path": arguments["path"]}}

    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        service=service,
        registry={"model_create": create_model, "model_load": load_model},
        worker=_Worker(),
    )
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime",
        "worker_instance_id": "test-worker",
        "connection_epoch": 1,
        "server_instance_id": "test-server",
    }
    project = _create_project(daemon, "model-owner")
    foreign = _create_project(daemon, "foreign")
    try:
        # A project-local foreign path must still fail closed, while the
        # legacy global workflow's stale path must not be consulted.
        with daemon.backend.project_root_scope(project["workspace"]):
            _state._write_workflow_state({
                "current_main_model_path": str(Path(foreign["workspace"]) / "foreign.mph"),
            })
        foreign_path = daemon.dispatch({
            "operation": "model_create",
            "arguments": {"model_name": "must-not-create"},
            "execution": {
                "project_id": project["project_id"],
                "session_id": "session",
                "request_id": "project-model-create-foreign-workflow-path",
                "idempotency_key": "project-model-create-foreign-workflow-path",
            },
        })
        assert foreign_path["success"] is False
        assert foreign_path["error"]["code"] == "PERMISSION_DENIED"
        assert created_names == []

        with daemon.backend.project_root_scope(project["workspace"]):
            _state._write_workflow_state({"current_main_model_path": "", "main_model_path": ""})
        created = daemon.dispatch({
            "operation": "model_create",
            "arguments": {"model_name": "created-model"},
            "execution": {
                "project_id": project["project_id"],
                "session_id": "session",
                "request_id": "project-model-create",
                "idempotency_key": "project-model-create",
            },
        })
        assert created["success"] is True, created
        assert created_names == ["created-model"]
        ref = created["execution"]["model_ref"]
        assert daemon.backend.model_project_binding(ref) == {
            "attribution": "PROJECT_BOUND", "project_id": project["project_id"],
        }
        project_workflow = Path(project["workspace"]) / ".comsol_mcp" / "workflow_state.json"
        project_state = json.loads(project_workflow.read_text(encoding="utf-8"))
        assert project_state["current_main_model_path"] == ""
        assert project_state["snapshot_dir"] == ""
        assert legacy_workflow.read_bytes() == original_legacy_bytes

        loaded = daemon.dispatch({
            "operation": "model_load",
            "arguments": {"path": "input.mph"},
            "execution": {
                "project_id": project["project_id"],
                "session_id": "session",
                "request_id": "project-model-load",
                "idempotency_key": "project-model-load",
            },
        })
        assert loaded["success"] is True, loaded
        loaded_ref = loaded["execution"]["model_ref"]
        assert loaded_paths == [str(Path(project["workspace"]) / "input.mph")]
        assert daemon.backend.model_project_binding(loaded_ref) == {
            "attribution": "PROJECT_BOUND", "project_id": project["project_id"],
        }

        denied = daemon.dispatch({
            "operation": "model.inspect",
            "arguments": {"detail": "summary"},
            "execution": {
                "project_id": foreign["project_id"],
                "session_id": "session",
                "model_ref": loaded_ref,
                "idempotency_key": "foreign-model-read",
                "request_id": "foreign-model-read",
            },
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "PROJECT_IDENTITY_MISMATCH"
    finally:
        daemon.close()


def test_project_execution_scopes_backend_and_service_roots_and_restores_after_error(tmp_path, monkeypatch):
    import json

    from comsol_mcp import _state

    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    legacy_home = tmp_path / "legacy-home"
    legacy_home.mkdir()
    legacy_workflow = legacy_home / "workflow_state.json"
    legacy_state = {
        **_state._default_workflow_state(),
        "current_main_model_path": str(tmp_path / "outside.mph"),
        "notes": "legacy-global-state",
    }
    legacy_workflow.write_text(json.dumps(legacy_state), encoding="utf-8")
    legacy_bytes = legacy_workflow.read_bytes()
    monkeypatch.setattr(_state, "WORKFLOW_FILE", legacy_workflow)
    service = ExecutionService(
        SessionLedger("session", "test-server"), Snapshot(), project_root=workspace_root,
    )
    daemon = ControlDaemon(
        tmp_path / "control", project_root=workspace_root, service=service,
        registry={"set_parameters": lambda _args: {"success": True, "data": {}}},
    )
    first = _create_project(daemon, "first")
    second = _create_project(daemon, "second")
    base_root = daemon.backend.project_root
    observed: list[tuple[str, str, str, str]] = []

    def invoke(operation, arguments, execution, operation_id, event_callback):
        workflow = _state._read_workflow_state()
        workflow_path = _state._workflow_state_file()
        observed.append((str(daemon.backend.project_root), str(service.project_root),
                         str(workflow_path), str(workflow.get("notes", ""))))
        _state._write_workflow_state({
            "notes": f"owned:{daemon.backend.project_root.name}",
        })
        if arguments.get("raise"):
            raise ExecutionContractError("INJECTED_REFUSAL", "injected callback refusal")
        return {"success": True, "data": {}}

    monkeypatch.setattr(daemon.backend, "invoke", invoke)
    try:
        for project, should_raise in ((first, False), (second, True), (first, False), (second, False)):
            result = daemon.dispatch({
                "operation": "set_parameters",
                "arguments": {"raise": should_raise},
                "execution": {
                    "project_id": project["project_id"],
                    "request_id": f"scoped-{project['label']}-{len(observed)}",
                    "idempotency_key": f"scoped-{project['label']}-{len(observed)}",
                },
            })
            assert result["success"] is (not should_raise)
            if should_raise:
                assert result["error"]["code"] == "INJECTED_REFUSAL"
        expected = [
            (first["workspace"], first["workspace"],
             str(Path(first["workspace"]) / ".comsol_mcp" / "workflow_state.json"), ""),
            (second["workspace"], second["workspace"],
             str(Path(second["workspace"]) / ".comsol_mcp" / "workflow_state.json"), ""),
            (first["workspace"], first["workspace"],
             str(Path(first["workspace"]) / ".comsol_mcp" / "workflow_state.json"),
             f"owned:{Path(first['workspace']).name}"),
            (second["workspace"], second["workspace"],
             str(Path(second["workspace"]) / ".comsol_mcp" / "workflow_state.json"),
             f"owned:{Path(second['workspace']).name}"),
        ]
        assert observed == expected
        assert daemon.backend.project_root == base_root
        assert service.project_root.resolve() == workspace_root.resolve()
        assert _state._workflow_state_file() == legacy_workflow
        assert legacy_workflow.read_bytes() == legacy_bytes
    finally:
        daemon.close()


def test_project_workflow_state_rejects_symlink_escape_before_read_or_write(tmp_path):
    from comsol_mcp import _state

    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_state = outside / "workflow_state.json"
    outside_state.write_text('{"notes":"must remain untouched"}', encoding="utf-8")

    state_dir = project_root / ".comsol_mcp"
    state_dir.symlink_to(outside, target_is_directory=True)
    with _state.project_workflow_state_scope(project_root):
        try:
            _state._read_workflow_state()
        except ExecutionContractError as exc:
            assert exc.code == "PERMISSION_DENIED"
        else:
            raise AssertionError("symlinked project workflow directory must be refused")
    state_dir.unlink()

    state_dir.mkdir()
    state_file = state_dir / "workflow_state.json"
    state_file.symlink_to(outside_state)
    with _state.project_workflow_state_scope(project_root):
        try:
            _state._write_workflow_state({"notes": "must not escape"})
        except ExecutionContractError as exc:
            assert exc.code == "PERMISSION_DENIED"
        else:
            raise AssertionError("symlinked project workflow file must be refused")
    assert outside_state.read_text(encoding="utf-8") == '{"notes":"must remain untouched"}'


def test_queued_project_write_is_reauthorized_after_policy_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=workspace_root, registry={"set_parameters": lambda _: None})
    project = _create_project(daemon, "revoked")
    entered = threading.Event()
    release = threading.Event()
    invoked: list[str] = []

    def block_queue():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test queue blocker was not released")

    daemon.session_scheduler.submit(None, block_queue)
    assert entered.wait(2)
    monkeypatch.setattr(
        daemon.backend,
        "invoke",
        lambda *args: invoked.append(args[0]) or {"success": True, "data": {}},
    )
    request = {
        "operation": "set_parameters",
        "arguments": {"parameter": "x"},
        "execution": {
            "project_id": project["project_id"],
            "request_id": "queued-write",
            "idempotency_key": "queued-write",
            "rpc_timeout_s": 10,
        },
    }
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(daemon.dispatch, request)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with daemon.store.lock:
                    queued = daemon.store.db.execute(
                        "SELECT 1 FROM operations WHERE idempotency_key=?", ("queued-write",),
                    ).fetchone()
                if queued:
                    break
                time.sleep(0.01)
            assert queued is not None, "write was not durably admitted before policy update"

            revoked = daemon.dispatch({
                "operation": "project.policy_set",
                "arguments": {
                    "project_id": project["project_id"],
                    "policy": {"permissions": ["inspect"]},
                    "authorization_ref": "audit-ticket-queued-revoke",
                },
                "execution": {"request_id": "revoke-write", "idempotency_key": "revoke-write"},
            })
            assert revoked["success"] is True
            release.set()
            result = pending.result(timeout=10)

        assert result["success"] is False
        assert result["error"]["code"] == "PERMISSION_DENIED"
        assert invoked == []
        job = daemon.store.job(result["execution"]["job_id"])
        assert job["status"] == "FAILED"
        assert not any(event["event"] == "worker_request" for event in daemon.store.events(job["job_id"]))
    finally:
        release.set()
        daemon.close()


def test_project_identity_is_part_of_the_durable_idempotency_hash(tmp_path, monkeypatch):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        registry={"set_parameters": lambda _args: {"success": True, "data": {}}},
    )
    first = _create_project(daemon, "hash-first")
    second = _create_project(daemon, "hash-second")
    invoked: list[str] = []
    monkeypatch.setattr(
        daemon.backend,
        "invoke",
        lambda *args: invoked.append(args[2]["project_id"]) or {"success": True, "data": {}},
    )
    request = {
        "operation": "set_parameters",
        "arguments": {"parameter": "x"},
        "execution": {
            "project_id": first["project_id"],
            "request_id": "same-key-first",
            "idempotency_key": "same-key-cross-project",
        },
    }
    try:
        accepted = daemon.dispatch(request)
        assert accepted["success"] is True
        replayed_elsewhere = daemon.dispatch({
            **request,
            "execution": {
                **request["execution"],
                "project_id": second["project_id"],
                "request_id": "same-key-second",
            },
        })
        assert replayed_elsewhere["success"] is False
        assert replayed_elsewhere["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert invoked == [first["project_id"]]
    finally:
        daemon.close()


def test_same_project_pending_request_reuses_identity_but_other_project_conflicts(tmp_path, monkeypatch):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=workspace_root,
        registry={"set_parameters": lambda _args: {"success": True, "data": {}}},
    )
    first = _create_project(daemon, "pending-first")
    second = _create_project(daemon, "pending-second")
    queue_entered = threading.Event()
    release_queue = threading.Event()
    invoke_entered = threading.Event()
    release_invoke = threading.Event()
    invoked: list[str] = []

    def block_queue():
        queue_entered.set()
        if not release_queue.wait(10):
            raise TimeoutError("test queue blocker was not released")

    def unknown_invoke(_operation, _arguments, execution, _operation_id, _worker_event):
        invoked.append(execution["project_id"])
        invoke_entered.set()
        if not release_invoke.wait(10):
            raise TimeoutError("test backend blocker was not released")
        return {
            "success": False,
            "execution_state_unknown": True,
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "synthetic ambiguous outcome"},
        }

    daemon.session_scheduler.submit(None, block_queue)
    assert queue_entered.wait(2)
    monkeypatch.setattr(daemon.backend, "invoke", unknown_invoke)
    request = {
        "operation": "set_parameters",
        "arguments": {"parameter": "x"},
        "execution": {
            "project_id": first["project_id"],
            "request_id": "pending-first-attempt",
            "idempotency_key": "same-project-pending-key",
            "rpc_timeout_s": 0.02,
            "queue_timeout_s": 10,
        },
    }
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_response_future = pool.submit(daemon.dispatch, request)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with daemon.store.lock:
                    stored = daemon.store.db.execute(
                        "SELECT j.job_id FROM operations o JOIN jobs j USING(operation_id) WHERE o.idempotency_key=?",
                        ("same-project-pending-key",),
                    ).fetchone()
                if stored:
                    break
                time.sleep(0.01)
            assert stored is not None, "request was not durably admitted"
            first_response = first_response_future.result(timeout=2)
            assert first_response["success"] is True
            first_job_id = first_response["data"]["job_id"]
            assert first_job_id == stored["job_id"]

            same_project_replay = daemon.dispatch({
                **request,
                "execution": {
                    **request["execution"],
                    "request_id": "pending-same-project-replay",
                },
            })
            assert same_project_replay["success"] is True
            assert same_project_replay["data"]["job_id"] == first_job_id
            assert same_project_replay["data"]["status"] == "QUEUED"

            other_project_replay = daemon.dispatch({
                **request,
                "execution": {
                    **request["execution"],
                    "project_id": second["project_id"],
                    "request_id": "pending-other-project-replay",
                },
            })
            assert other_project_replay["success"] is False
            assert other_project_replay["error"]["code"] == "IDEMPOTENCY_CONFLICT"

            release_queue.set()
            assert invoke_entered.wait(2)
            assert invoked == [first["project_id"]]
            release_invoke.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = daemon.store.job(first_job_id)
                if job["status"] == "UNKNOWN":
                    break
                time.sleep(0.01)
            assert job["status"] == "UNKNOWN"

        # An ambiguous terminal outcome remains authoritative. Reusing its
        # same-project key returns the stored result without another invoke.
        unknown_replay = daemon.dispatch({
            **request,
            "execution": {
                **request["execution"],
                "request_id": "pending-unknown-replay",
            },
        })
        assert unknown_replay["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert unknown_replay["execution"]["job_id"] == first_job_id
        assert invoked == [first["project_id"]]
    finally:
        release_queue.set()
        release_invoke.set()
        daemon.close()
