"""Offline protocol tests for the bounded W21 stdio runner.

The transport double below models public MCP envelopes only. It never starts
the native server, Java Worker, COMSOL engine, or solver.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "w21_stage_native_runner", REPOSITORY / "tools/run_w21_stage_native.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def _plan(tmp_path: Path, *, worker_epoch: int = 7) -> dict:
    root = REPOSITORY
    run_root = tmp_path / "run"
    workspace_root = run_root / "workspaces"
    workspace_root.mkdir(parents=True)
    plan = {
        "schema": runner.SCHEMA,
        "run_id": "w21-test-run",
        "run_root": str(run_root),
        "requested_version": "6.4",
        "selected_comsol": {
            "runtime_id": "comsol64", "version": "6.4.0.293", "build": "293",
        },
        "selected_jdk": {"home": "offline-test-jdk"},
        "source_root": str(root),
        "source_manifest": runner.source_manifest(root),
        "fixture_sha256": runner.sha256_file(runner.FIXTURE),
        "probe_sha256": runner.sha256_file(runner.PROBE),
        "project_workspace": str(workspace_root / "field-identity-probe"),
        "isolation_receipt": str(run_root / "owned_server_isolation.json"),
        "published_tool_schemas": {
            "operation_call": {"type": "object", "properties": {}},
            "operation_describe": {"type": "object", "properties": {}},
            "model_create": {"type": "object", "properties": {}},
        },
        "logical_operation_schemas": {"job.list": {"type": "object"}, "job.status": {"type": "object"}},
        "request_ids": {
            name: f"req-{name}" for name in (
                "project_create", "session_start", "session_connect", "model_create",
                "model_inspect_before_fixture", "fixture_register", "fixture_execute",
                "model_inspect_after_fixture", "probe_register", "probe_execute",
                "session_disconnect", "session_stop", "unknown_query",
                "failure_session_disconnect", "failure_session_stop",
            )
        },
        "idempotency_keys": {
            name: f"idem-{name}" for name in (
                "project_create", "session_start", "session_connect", "fixture_register",
                "fixture_execute", "probe_register", "probe_execute",
                "session_disconnect", "session_stop", "failure_session_disconnect",
                "failure_session_stop",
            )
        },
        "budgets": {
            "server_births_max": 1, "worker_births_max": 1,
            "seconds_from_session_start_dispatch": 900,
            "ordinary_rpc_wait_seconds": 45,
            "geometry_run": 1, "mesh_run": 1, "study_dispatch": 0, "solver_dispatch": 0,
        },
        "_worker_epoch": worker_epoch,
    }
    plan["freeze_sha256"] = runner.sha256_value(plan)
    return plan


def _state(tmp_path: Path) -> runner._RunState:
    path = tmp_path / "run" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": runner.SCHEMA, "run_id": "w21-test-run",
        "freeze_sha256": "frozen-test-hash", "status": "PREPARED",
        "action_history": [], "job_ids": [],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return runner._RunState(path, value)


class _FakeStdioSession:
    """A protocol double that verifies durable intent before each tool call."""

    def __init__(self, plan: dict, state: runner._RunState, *,
                 wrong_model_field: str | None = None,
                 inspect_drift: bool = False,
                 probe_revision_drift: bool = False,
                 unknown_on: str | None = None,
                 timeout_on: str | None = None):
        self.plan = plan
        self.state = state
        self.wrong_model_field = wrong_model_field
        self.inspect_drift = inspect_drift
        self.probe_revision_drift = probe_revision_drift
        self.unknown_on = unknown_on
        self.timeout_on = timeout_on
        self.calls: list[tuple[str, dict]] = []
        self.project_id = "project-test"
        self.session_id = "session-test"
        self.server_id = "server-test"
        self.worker_epoch = plan.get("_worker_epoch", 7)
        self.model_ref = {
            "model_tag": "w21model", "session_id": self.session_id,
            "server_instance_id": self.server_id, "generation": 1,
        }

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(name=name, inputSchema=schema)
            for name, schema in self.plan["published_tool_schemas"].items()
        ])

    def _identity_in_params(self, params: dict):
        outer = params.get("execution") if isinstance(params.get("execution"), dict) else {}
        inner = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        return outer.get("request_id") or inner.get("request_id"), outer.get("idempotency_key") or inner.get("idempotency_key")

    def _assert_pre_dispatch_intent(self, params: dict):
        request_id, idempotency_key = self._identity_in_params(params)
        state = json.loads(self.state.path.read_text(encoding="utf-8"))
        intents = list(state.get("actions", {}).values())
        if request_id:
            assert any(row.get("request_id") == request_id for row in intents), request_id
        if idempotency_key:
            assert any(row.get("idempotency_key") == idempotency_key for row in intents), idempotency_key

    def _ref(self):
        ref = dict(self.model_ref)
        if self.wrong_model_field == "project":
            pass  # project identity is checked in execution, not embedded ModelRef.
        if self.wrong_model_field == "session":
            ref["session_id"] = "foreign-session"
        elif self.wrong_model_field == "server":
            ref["server_instance_id"] = "foreign-server"
        elif self.wrong_model_field == "generation":
            ref["generation"] = 0
        return ref

    @staticmethod
    def _execution_reply(params: dict) -> dict:
        execution = dict(params["execution"])
        execution["revision"] = execution.pop("expected_revision")
        return execution

    async def call_tool(self, name: str, params: dict):
        operation = params.get("operation_id")
        inner = params.get("arguments", {})
        execution = params.get("execution", {})
        if operation == "model.inspect":
            action = ("model.inspect.after_fixture"
                      if execution.get("request_id") == self.plan["request_ids"]["model_inspect_after_fixture"]
                      else "model.inspect")
        elif operation == "artifact.register":
            action = "fixture.register" if Path(inner["path"]).name == "W21Fixture.java" else "probe.register"
        elif operation == "code.execute_java":
            action = "fixture.execute" if inner.get("entrypoint") == "W21Fixture" else "probe.execute"
        else:
            action = operation or ("model_create" if name == "model_create" else None)
        if action in {"job.list", "job.status"}:
            query = self.state.value.get("recovery", {}).get("read_only_query", {})
            assert query.get("status") == "READ_ONLY_QUERY_INTENT"
            assert query.get("params_sha256") == runner.sha256_value(params)
        if action not in {"job.list", "job.status"}:
            self._assert_pre_dispatch_intent(params)
        self.calls.append((action or name, params))

        if self.timeout_on == action:
            raise asyncio.TimeoutError("injected transport timeout")
        if self.unknown_on == action:
            if action == "fixture.execute":
                return {"success": False, "execution_state_unknown": True,
                        "data": {"job_id": "job-test", "status": "UNKNOWN",
                                 "project_id": self.project_id}}
            return {"success": False, "execution_state_unknown": True,
                    "data": {"status": "UNKNOWN"}}

        if action == "project.create":
            Path(self.plan["project_workspace"]).mkdir(parents=True)
            return {"success": True, "data": {"project_id": self.project_id}}
        if action == "session.start":
            return {"success": True, "data": {
                "project_id": self.project_id, "session_id": self.session_id,
                "runtime_id": "comsol64", "endpoint": {"host": "127.0.0.1", "port": 2036},
                "server_ownership": "mcp_managed", "loopback_only_verified": True,
                "server_process_identity": {"pid": 9876, "birth": "start_epoch_ms:1700000000000"},
            }}
        if action == "session.connect":
            return {"success": True, "data": {
                "project_id": self.project_id, "session_id": self.session_id,
                "runtime_id": "comsol64", "endpoint": {"host": "127.0.0.1", "port": 2036},
                "server_ownership": "mcp_managed", "server_instance_id": self.server_id,
                "worker_instance_id": "worker-test", "worker_epoch": self.worker_epoch,
                "remote_engine_version": "6.4.0.293", "remote_engine_build": "293",
            }}
        if action == "model_create":
            ref = self._ref()
            execution = {
                "project_id": "foreign-project" if self.wrong_model_field == "project" else self.project_id,
                "session_id": self.session_id, "model_ref": ref, "revision": 0,
            }
            return {"success": True, "data": {"model_tag": "w21model"}, "execution": execution}
        if action in {"model.inspect", "model.inspect.after_fixture"}:
            execution = self._execution_reply(params)
            if self.inspect_drift:
                execution["revision"] += 1
            return {"success": True, "execution": execution, "data": {"model_tag": "w21model"}}
        if action in {"fixture.register", "probe.register"}:
            path = Path(inner["path"])
            return {"success": True, "data": {"sha256": runner.sha256_file(path)}}
        if action == "fixture.execute":
            execution = self._execution_reply(params)
            execution["revision"] += 1
            return {"success": True, "data": {
                "execution_success": True, "readback": {"status": "BUILT_NOT_SOLVED"},
            }, "execution": execution}
        if action == "probe.execute":
            execution = self._execution_reply(params)
            if self.probe_revision_drift:
                execution["revision"] += 1
            return {"success": True, "data": {
                "execution_success": True,
                "readback": {
                    "probe": "W21FieldIdentityProbe", "status": "STRUCTURE_CAPTURED_ONLY",
                    "native_admission": "UNVERIFIED",
                    "identity": {"model_tag": "w21model"},
                },
            }, "execution": execution}
        if action == "job.status":
            return {"success": True, "data": {"job_id": "job-test", "status": "RUNNING",
                                                 "project_id": self.project_id}}
        if action == "job.list":
            original_id = self.plan["request_ids"]["fixture_execute"]
            return {"success": True, "data": {"jobs": [{
                "job_id": "job-test", "status": "RUNNING", "request_id": original_id,
                "project_id": self.project_id,
            }]}}
        if action == "session.disconnect":
            return {"success": True, "data": {"project_id": self.project_id,
                "session_id": self.session_id, "worker_handle_preserved": False}}
        if action == "session.stop":
            return {"success": True, "data": {"project_id": self.project_id,
                "session_id": self.session_id, "state": "STOPPED", "server_stopped": True,
                "stop_evidence": {"owned_process": True}}}
        raise AssertionError(f"unhandled fake public operation: {action or name}; params={params}")


def _patch_isolation(monkeypatch):
    monkeypatch.setattr(runner, "_write_isolation_receipt", lambda *_args: None)
    monkeypatch.setattr(runner, "_mark_isolation_receipt_stopped", lambda *_args: None)


def test_prepare_freezes_full_source_identity_without_starting_services(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows")
    monkeypatch.setenv("COMSOL_MCP_TOOL_PROFILE", "full")
    monkeypatch.setattr(runner, "_comsol_identity", lambda root, version: {
        "runtime_id": "comsol64", "root": str(root), "version": version + ".0.293",
        "build": "293", "server_executable_sha256": "a" * 64,
    })
    monkeypatch.setattr(runner, "_jdk_identity", lambda home: {
        "home": str(home), "java_sha256": "b" * 64, "javac_sha256": "c" * 64,
        "release_sha256": "d" * 64, "java_version": "11",
    })
    monkeypatch.setattr(runner, "_python_identity", lambda: {
        "executable_sha256": "e" * 64, "python_version": "3.12.0",
        "implementation": "CPython", "mcp_distribution_version": "1.30.0",
    })
    monkeypatch.setattr(runner, "_published_tool_schemas", lambda _root: {
        name: {"type": "object"} for name in runner.REQUIRED_TOOLS
    })
    monkeypatch.setattr(runner, "_logical_schemas", lambda _root: {
        name: {"type": "object"} for name in runner.LOGICAL_OPERATIONS
    })
    evidence = tmp_path / "external-evidence"
    evidence.mkdir()

    result = runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                            jdk_home=tmp_path / "JDK11", evidence_root=evidence)
    plan = result["plan"]
    expected_files = {
        "tools/run_w21_stage_native.py", "tools/java/W21Fixture.java",
        "tools/java/W21FieldIdentityProbe.java", "tools/run_function_evaluate_probe.py",
        "comsol_mcp/worker_java/PersistentComsolWorker.java",
        "comsol_mcp/_control_client.py", "comsol_mcp/_g2_registry.py",
        "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
    }
    assert expected_files <= plan["source_manifest"].keys()
    assert not any(Path(name).name.startswith("._") for name in plan["source_manifest"])
    assert plan["source_manifest_sha256"] == runner.sha256_value(plan["source_manifest"])
    assert plan["fixture_sha256"] == plan["source_manifest"]["tools/java/W21Fixture.java"]
    assert plan["probe_sha256"] == plan["source_manifest"]["tools/java/W21FieldIdentityProbe.java"]
    assert plan["budgets"]["study_dispatch"] == plan["budgets"]["solver_dispatch"] == 0
    assert result["status"] == "PREPARED_ONLY"
    assert not Path(plan["project_workspace"]).exists()
    assert not Path(plan["server_home"]).exists()
    assert json.loads((Path(result["run_root"]) / "state.json").read_text())["status"] == "PREPARED"


def test_python_identity_freezes_interpreter_import_origin_and_dependency_inventory(monkeypatch):
    inventory = [
        SimpleNamespace(metadata={"Name": "mcp"}, version="1.30.0"),
        SimpleNamespace(metadata={"Name": "anyio"}, version="4.9.0"),
    ]
    monkeypatch.setattr(runner.importlib.metadata, "distributions", lambda: inventory)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda _name: "1.30.0")
    identity = runner._python_identity()
    assert len(identity["executable_sha256"]) == 64
    assert Path(identity["mcp_import_origin"]).is_absolute()
    assert identity["distribution_count"] == 2
    assert len(identity["distribution_inventory_sha256"]) == 64


def test_python_identity_fails_closed_on_unreadable_distribution_metadata(monkeypatch):
    class UnreadableDistribution:
        @property
        def metadata(self):
            raise UnicodeDecodeError("utf-8", b"bad", 0, 1, "invalid metadata")

    monkeypatch.setattr(runner.importlib.metadata, "distributions", lambda: [UnreadableDistribution()])
    with pytest.raises(runner.RunnerError, match="dependency inventory is unreadable"):
        runner._python_identity()


def test_freeze_reads_current_public_stdio_and_logical_query_schemas(monkeypatch):
    monkeypatch.setenv("COMSOL_MCP_TOOL_PROFILE", "full")
    tools = runner._published_tool_schemas(REPOSITORY)
    logical = runner._logical_schemas(REPOSITORY)
    assert set(tools) == set(runner.REQUIRED_TOOLS)
    assert {"job.list", "job.status"} <= set(logical)
    assert all(isinstance(schema, dict) and schema.get("type") == "object"
               for schema in tools.values())


def test_verify_plan_rejects_hash_mismatch_and_source_manifest_drift(monkeypatch):
    plan = {
        "source_manifest": runner.source_manifest(REPOSITORY),
        "source_manifest_sha256": None,
        "python": {"frozen": True}, "published_tool_schemas": {},
        "logical_operation_schemas": {},
        "selected_comsol": {"root": "frozen-root"}, "requested_version": "6.4",
        "selected_jdk": {"home": "frozen-jdk"},
    }
    plan["source_manifest_sha256"] = runner.sha256_value(plan["source_manifest"])
    monkeypatch.setattr(runner, "_python_identity", lambda: plan["python"])
    monkeypatch.setattr(runner, "_published_tool_schemas", lambda _root: plan["published_tool_schemas"])
    monkeypatch.setattr(runner, "_logical_schemas", lambda _root: plan["logical_operation_schemas"])
    monkeypatch.setattr(runner, "_comsol_identity", lambda *_args: plan["selected_comsol"])
    monkeypatch.setattr(runner, "_jdk_identity", lambda _home: plan["selected_jdk"])
    candidate = dict(plan)
    candidate["freeze_sha256"] = runner.sha256_value(candidate)
    with pytest.raises(runner.RunnerError, match="freeze hash mismatch"):
        runner.verify_plan(candidate, expected_sha256="0" * 64)
    runner.verify_plan(candidate, expected_sha256=candidate["freeze_sha256"])

    drifted = dict(candidate)
    drifted["source_manifest"] = dict(candidate["source_manifest"])
    drifted["source_manifest"]["tools/java/W21Fixture.java"] = "0" * 64
    drifted["source_manifest_sha256"] = runner.sha256_value(drifted["source_manifest"])
    drifted.pop("freeze_sha256")
    drifted["freeze_sha256"] = runner.sha256_value(drifted)
    with pytest.raises(runner.RunnerError, match="source manifest drifted"):
        runner.verify_plan(drifted, expected_sha256=drifted["freeze_sha256"])


@pytest.mark.parametrize("field", ["project", "session", "server", "generation"])
def test_model_create_foreign_or_invalid_binding_refuses_before_java(field, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, wrong_model_field=field)
    with pytest.raises(runner.RunnerError, match="model_create returned"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert "model_create" in operations
    assert "model.inspect" not in operations
    assert "fixture.execute" not in operations
    assert "session.disconnect" in operations
    assert "session.stop" in operations
    assert state.value["status"] == "FAILED"
    assert state.value["failure_cleanup"]["status"] == "CLEANUP_COMPLETE"


def test_metadata_only_protocol_binds_model_generation_separately_from_worker_epoch(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, worker_epoch=7)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state)
    report = asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
        clock=lambda: 100.0, preflight=lambda: [{"process_id": 1, "name": "python.exe",
            "path_missing": True, "command_line_missing": True}]))

    model_create = next(params for action, params in fake.calls if action == "model_create")
    assert model_create["execution"]["project_id"] == fake.project_id
    assert model_create["execution"]["session_id"] == fake.session_id
    binding = report["model_binding"]
    assert binding["worker_epoch"] == 7
    assert binding["model_ref"]["generation"] == 1
    assert binding["revision"] == 1
    assert report["owned_server"] == {
        "pid": 9876, "birth": "start_epoch_ms:1700000000000",
        "host": "127.0.0.1", "port": 2036,
    }
    assert report["remote_engine_version"] == "6.4.0.293"
    assert report["native_admission"] == report["physical_validation"] == "UNVERIFIED"
    assert report["study_dispatch"] == report["solver_dispatch"] == 0
    assert report["cleanup"]["status"] == "CLEANUP_COMPLETE"
    assert state.value["status"] == "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION"
    assert not any(action in {"study.run", "solver.run"} for action, _ in fake.calls)
    assert any(action == "session.disconnect" for action, _ in fake.calls)
    assert any(action == "session.stop" for action, _ in fake.calls)


def test_pure_inspect_revision_drift_refuses_before_fixture(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, inspect_drift=True)
    with pytest.raises(runner.RunnerError, match="exact project/session/ModelRef/revision"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert "fixture.execute" not in [action for action, _ in fake.calls]


def test_probe_result_must_echo_exact_model_binding_and_revision(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, probe_revision_drift=True)
    with pytest.raises(runner.RunnerError, match="probe reply omitted the exact"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert "session.disconnect" in [action for action, _ in fake.calls]
    assert "session.stop" in [action for action, _ in fake.calls]
    assert state.value["status"] == "FAILED"


def test_unknown_fixture_job_is_queried_once_read_only_and_never_cleaned_or_replayed(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, unknown_on="fixture.execute")
    with pytest.raises(runner.RunnerError, match="returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("fixture.execute") == 1
    assert operations.count("job.status") == 1
    assert state.value["recovery"]["read_only_query"]["exact_job_identity_confirmed"] is True
    assert "probe.execute" not in operations
    assert "session.disconnect" not in operations
    assert "session.stop" not in operations
    assert state.value["status"] == "UNKNOWN"
    assert state.value["recovery"]["request_id"] == plan["request_ids"]["fixture_execute"]
    assert state.value["recovery"]["idempotency_key"] == plan["idempotency_keys"]["fixture_execute"]
    assert state.value["recovery"]["replay_permitted"] is False
    assert state.value["recovery"]["cleanup_permitted"] is False


def test_transport_timeout_queries_project_jobs_for_exact_request_without_retry(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, timeout_on="fixture.execute")
    with pytest.raises(runner.RunnerError, match="transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("fixture.execute") == 1
    assert operations.count("job.list") == 1
    assert "session.disconnect" not in operations
    query = state.value["recovery"]["read_only_query"]
    assert query["status"] == "QUERY_RESPONSE_RECORDED"
    assert query["matching_job_rows"] == [{
        "job_id": "job-test", "status": "RUNNING",
        "request_id": plan["request_ids"]["fixture_execute"], "project_id": "project-test",
    }]
    assert state.value["job_ids"] == ["job-test"]
    assert state.value["recovery"]["job_ids"] == ["job-test"]
    assert state.value["status"] == "UNKNOWN"


def test_unknown_disconnect_never_dispatches_server_stop(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, unknown_on="session.disconnect")
    with pytest.raises(runner.RunnerError, match="returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("session.disconnect") == 1
    assert "session.stop" not in operations
    assert state.value["status"] == "UNKNOWN"
    assert state.value["recovery"]["cleanup_permitted"] is False
