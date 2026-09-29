"""Offline protocol tests for the bounded W21 stdio runner.

The transport double below models public MCP envelopes only. It never starts
the native server, Java Worker, COMSOL engine, or solver.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from comsol_mcp._control_daemon import ControlDaemon


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
    server_home_root = tmp_path / "h"
    server_home_root.mkdir()
    server_home_id = "0123456789abcdef"
    server_home = server_home_root / server_home_id
    plan = {
        "schema": runner.SCHEMA,
        "run_id": "w21-test-run",
        "run_root": str(run_root),
        "task_root": str(tmp_path),
        "server_home_root": str(server_home_root),
        "server_home_id": server_home_id,
        "server_home_path_budget": runner._server_home_budget(server_home),
        "server_home": str(server_home),
        "requested_version": "6.4",
        "selected_comsol": {
            "runtime_id": "comsol64", "root": str(tmp_path / "COMSOL64"),
            "version": "6.4.0.293", "build": "293",
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
        "logical_operation_schemas": {
            "job.list": {"type": "object"}, "job.status": {"type": "object"},
            "job.wait": {"type": "object"},
        },
        "request_ids": {
            name: f"req-{name}" for name in (
                "project_create", "project_create_wait", "session_start", "session_connect", "model_create",
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
            "project_create_wait_calls_max": 1,
            "project_create_wait_timeout_seconds": 30,
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


def _patch_prepare_environment(monkeypatch):
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


class _FakeStdioSession:
    """A protocol double that verifies durable intent before each tool call."""

    def __init__(self, plan: dict, state: runner._RunState, *,
                 wrong_model_field: str | None = None,
                 inspect_drift: bool = False,
                 probe_revision_drift: bool = False,
                 project_create_pending: bool = False,
                 project_create_fault: str | None = None,
                 project_wait_fault: str | None = None,
                 unknown_on: str | None = None,
                 timeout_on: str | None = None,
                 job_list_rows: list[dict] | None = None,
                 job_list_has_more: bool = False,
                 job_list_timeout: bool = False,
                 session_start_unknown_job_id: str | None = None,
                 job_status_fault: str | None = None):
        self.plan = plan
        self.state = state
        self.wrong_model_field = wrong_model_field
        self.inspect_drift = inspect_drift
        self.probe_revision_drift = probe_revision_drift
        self.project_create_pending = project_create_pending
        self.project_create_fault = project_create_fault
        self.project_wait_fault = project_wait_fault
        self.unknown_on = unknown_on
        self.timeout_on = timeout_on
        self.job_list_rows = job_list_rows
        self.job_list_has_more = job_list_has_more
        self.job_list_timeout = job_list_timeout
        self.session_start_unknown_job_id = session_start_unknown_job_id
        self.job_status_fault = job_status_fault
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
        elif operation == "job.wait":
            action = "project.create.wait"
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
        if action == "job.list" and self.job_list_timeout:
            raise asyncio.TimeoutError("injected read-only job.list timeout")
        if self.unknown_on == action:
            if action == "fixture.execute":
                return {"success": False, "execution_state_unknown": True,
                        "data": {"job_id": "job-test", "status": "UNKNOWN",
                                 "project_id": self.project_id}}
            if action == "session.start":
                data = {"status": "UNKNOWN"}
                if self.session_start_unknown_job_id is not None:
                    data.update({"job_id": self.session_start_unknown_job_id,
                                 "project_id": self.project_id})
                return {"success": False, "execution_state_unknown": True, "data": data}
            return {"success": False, "execution_state_unknown": True,
                    "data": {"status": "UNKNOWN"}}

        if action == "project.create":
            Path(self.plan["project_workspace"]).mkdir(parents=True)
            project = {"project_id": self.project_id,
                       "workspace": str(Path(self.plan["project_workspace"]).resolve()),
                       "revision": 1}
            if self.project_create_pending:
                request_id, idempotency_key = self._identity_in_params(params)
                job_id = "project-create-job"
                execution = {"request_id": request_id,
                             "idempotency_key": idempotency_key,
                             "operation_id": "op-project-create",
                             "job_id": job_id}
                if self.project_create_fault == "wrong_request":
                    execution["request_id"] = "different-request"
                elif self.project_create_fault == "wrong_idempotency":
                    execution["idempotency_key"] = "different-idem"
                elif self.project_create_fault == "wrong_job_binding":
                    execution["job_id"] = "different-job"
                return {"success": True,
                        "data": {"job_id": job_id, "status": "RUNNING"},
                        "execution": execution}
            request_id, idempotency_key = self._identity_in_params(params)
            if self.project_create_fault == "wrong_request":
                request_id = "different-request"
            elif self.project_create_fault == "wrong_idempotency":
                idempotency_key = "different-idem"
            elif self.project_create_fault == "flat_project":
                return {"success": True, "data": {"project_id": self.project_id},
                        "execution": {"request_id": request_id,
                                      "idempotency_key": idempotency_key,
                                      "operation_id": "op-project-create"}}
            return {"success": True, "data": {"project": project},
                    "execution": {"request_id": request_id,
                                  "idempotency_key": idempotency_key,
                                  "operation_id": "op-project-create"}}
        if action == "project.create.wait":
            original_request = self.plan["request_ids"]["project_create"]
            original_key = self.plan["idempotency_keys"]["project_create"]
            job_id = inner.get("job_id")
            if self.project_wait_fault == "wrong_job":
                job_id = "other-job"
            operation_id = "op-project-create"
            if self.project_wait_fault == "wrong_operation_id":
                operation_id = "other-operation"
            result_data = {"project": {
                "project_id": self.project_id,
                "workspace": str(Path(self.plan["project_workspace"]).resolve()),
                "revision": 1,
            }}
            result_execution = {"request_id": original_request,
                                "idempotency_key": original_key,
                                "job_id": "project-create-job",
                                "operation_id": "op-project-create"}
            operation = {"operation": "project.create", "status": "SUCCEEDED",
                         "request_id": original_request, "idempotency_key": original_key,
                         "operation_id": "op-project-create"}
            if self.project_wait_fault == "wrong_request":
                operation["request_id"] = "different-request"
            if self.project_wait_fault == "failed":
                operation["status"] = "FAILED"
            return {"success": True, "data": {
                "job_id": job_id, "operation_id": operation_id, "status": "SUCCEEDED",
                "operation": operation,
                "result": {"success": True, "data": result_data,
                           "execution": result_execution},
            }}
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
            query = self.state.value["recovery"]["read_only_query"]
            if query.get("action") == "project.create.wait":
                original_request = self.plan["request_ids"]["project_create"]
                original_key = self.plan["idempotency_keys"]["project_create"]
                job_id, project_id = "project-create-job", None
                operation_name = "project.create"
            elif query.get("action") == "session.start":
                original_request = self.plan["request_ids"]["session_start"]
                original_key = self.plan["idempotency_keys"]["session_start"]
                job_id, project_id = query.get("query_job_id"), self.project_id
                operation_name = "session.start"
            else:
                original_request = self.plan["request_ids"]["fixture_execute"]
                original_key = self.plan["idempotency_keys"]["fixture_execute"]
                job_id, project_id = "job-test", self.project_id
                operation_name = "fixture.execute"
            if self.job_status_fault == "wrong_request":
                original_request = "foreign-request"
            elif self.job_status_fault == "wrong_idempotency":
                original_key = "foreign-idempotency"
            elif self.job_status_fault == "wrong_project":
                project_id = "foreign-project"
            elif self.job_status_fault == "wrong_operation":
                operation_name = "session.connect"
            operation_record = {"request_id": original_request,
                                "idempotency_key": original_key}
            if query.get("action") == "session.start":
                operation_record["operation"] = operation_name
                if self.job_status_fault == "missing_operation":
                    operation_record.pop("operation")
                elif self.job_status_fault == "missing_request":
                    operation_record.pop("request_id")
                elif self.job_status_fault == "missing_idempotency":
                    operation_record.pop("idempotency_key")
            return_data = {"job_id": job_id, "status": "SUCCEEDED", "project_id": project_id,
                           "operation": operation_record}
            if query.get("action") == "session.start":
                return_data["metadata"] = {"project_id": project_id}
            return {"success": True, "data": {
                **return_data,
            }}
        if action == "job.list":
            query = self.state.value["recovery"]["read_only_query"]
            original_id = query["original_request_id"]
            if original_id == self.plan["request_ids"]["project_create"]:
                job = {"job_id": "job-created-before-timeout", "status": "RUNNING",
                       "request_id": original_id,
                       "idempotency_key": self.plan["idempotency_keys"]["project_create"],
                       "project_id": None}
            elif original_id == self.plan["request_ids"]["session_start"]:
                if self.job_list_rows is None:
                    job = {
                        "job_id": "job-session-start", "status": "SUCCEEDED",
                        "metadata": {"project_id": self.project_id},
                        "operation": {
                            "operation": "session.start",
                            "request_id": original_id,
                            "idempotency_key": self.plan["idempotency_keys"]["session_start"],
                        },
                    }
                    jobs = [job]
                else:
                    jobs = [dict(row) for row in self.job_list_rows]
                return {"success": True, "data": {
                    "jobs": jobs, "total": len(jobs), "has_more": self.job_list_has_more,
                }}
            else:
                job = {"job_id": "job-test", "status": "RUNNING", "request_id": original_id,
                       "idempotency_key": self.plan["idempotency_keys"]["fixture_execute"],
                       "project_id": self.project_id}
            return {"success": True, "data": {"jobs": [job]}}
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
    _patch_prepare_environment(monkeypatch)
    # Keep the test-owned path compact enough for the frozen Windows DLL
    # MAX_PATH preflight, independent of pytest's test-function temp prefix.
    evidence = tmp_path / "e"
    evidence.mkdir()

    result = runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                            jdk_home=tmp_path / "JDK11", evidence_root=evidence,
                            server_home_root=evidence / "h")
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
    assert Path(plan["server_home"]).parent == Path(plan["server_home_root"])
    assert Path(plan["server_home_root"]).name == "h"
    assert len(plan["server_home_id"]) == 16
    assert plan["server_home_path_budget"]["command_line_utf16_units_including_nul"] <= 32767
    assert max(plan["server_home_path_budget"]["path_utf16_units_including_nul"].values()) <= 260
    assert result["status"] == "PREPARED_ONLY"
    assert not Path(plan["project_workspace"]).exists()
    assert not Path(plan["server_home"]).exists()
    assert json.loads((Path(result["run_root"]) / "state.json").read_text())["status"] == "PREPARED"


def test_prepare_accepts_explicit_short_runtime_root_inside_task(tmp_path, monkeypatch):
    _patch_prepare_environment(monkeypatch)
    # Exercise a genuinely short requested root without inheriting long labels.
    evidence = tmp_path / "t"
    evidence.mkdir()
    requested_root = evidence / "r"
    result = runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                            jdk_home=tmp_path / "JDK11", evidence_root=evidence,
                            server_home_root=requested_root)
    plan = result["plan"]
    assert Path(plan["server_home_root"]) == requested_root
    assert Path(plan["server_home"]).parent == requested_root
    assert not Path(plan["server_home"]).exists()


def test_prepare_assigns_distinct_uncreated_runtime_homes_per_run(tmp_path, monkeypatch):
    _patch_prepare_environment(monkeypatch)
    evidence = tmp_path / "task"
    evidence.mkdir()
    first = runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                           jdk_home=tmp_path / "JDK11", evidence_root=evidence)
    second = runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                            jdk_home=tmp_path / "JDK11", evidence_root=evidence)
    first_home = Path(first["plan"]["server_home"])
    second_home = Path(second["plan"]["server_home"])
    assert first_home != second_home
    assert first_home.parent == second_home.parent == evidence / "h"
    assert not first_home.exists() and not second_home.exists()


@pytest.mark.parametrize("server_home_root_kind", ["outside", "symlink", "junction_alias"])
def test_prepare_refuses_server_home_root_outside_task_or_aliased(tmp_path, monkeypatch, server_home_root_kind):
    _patch_prepare_environment(monkeypatch)
    evidence = tmp_path / "task"
    evidence.mkdir()
    if server_home_root_kind == "outside":
        requested = tmp_path / "outside"
    elif server_home_root_kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        requested = evidence / "h"
        requested.symlink_to(target, target_is_directory=True)
    else:
        requested = evidence / "h"
        requested.mkdir()
        target = tmp_path / "junction-target"
        target.mkdir()
        original_resolve = Path.resolve

        def resolve_junction(path, *args, **kwargs):
            if path == requested:
                return target
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve_junction)

    with pytest.raises(runner.RunnerError, match="inside the authorized task root|symlink|junction|alias"):
        runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                       jdk_home=tmp_path / "JDK11", evidence_root=evidence,
                       server_home_root=requested)
    assert not (evidence / "6.4").exists()


def test_prepare_refuses_deep_server_home_before_freezing_or_native_birth(tmp_path, monkeypatch):
    _patch_prepare_environment(monkeypatch)
    evidence = tmp_path / ("e" * 120) / ("d" * 120)
    evidence.mkdir(parents=True)

    with pytest.raises(runner.RunnerError, match="path budget exceeded"):
        runner.prepare(version="6.4", comsol_root=tmp_path / "COMSOL64",
                       jdk_home=tmp_path / "JDK11", evidence_root=evidence)

    assert not (evidence / "6.4").exists()
    assert not (evidence / "h").exists()


def test_execute_stdio_uses_the_frozen_server_home(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows")
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    captured = {}

    class FakeStdioServerParameters:
        def __init__(self, **values):
            self.__dict__.update(values)

    class FakeClientSession:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

    @asynccontextmanager
    async def fake_stdio_client(server):
        captured["server"] = server
        yield object(), object()

    async def fake_protocol(_client, _plan, _state, *, preflight=None):
        return {"status": "OFFLINE_TEST_DOUBLE"}

    mcp_module = ModuleType("mcp")
    mcp_module.__path__ = []
    mcp_module.ClientSession = FakeClientSession
    mcp_module.StdioServerParameters = FakeStdioServerParameters
    mcp_client_module = ModuleType("mcp.client")
    mcp_client_module.__path__ = []
    mcp_stdio_module = ModuleType("mcp.client.stdio")
    mcp_stdio_module.stdio_client = fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio_module)
    monkeypatch.setattr(runner, "run_metadata_protocol", fake_protocol)

    result = asyncio.run(runner._execute_stdio(plan, state))

    assert result == {"status": "OFFLINE_TEST_DOUBLE"}
    assert captured["server"].env["COMSOL_SERVER_MCP_HOME"] == plan["server_home"]
    assert Path(plan["server_home"]).is_dir()
    assert not (Path(plan["run_root"]) / "server-home").exists()


def test_project_create_actionresult_contract_and_pending_job_match_public_routes(tmp_path):
    project_root = tmp_path / "authorized-projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root)
    entered = threading.Event()
    release = threading.Event()
    original_dispatch = daemon.project_authority.dispatch

    def gated_dispatch(operation, arguments):
        if operation == "project.create":
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test gate timed out")
        return original_dispatch(operation, arguments)

    daemon.project_authority.dispatch = gated_dispatch
    request_id, idempotency_key = "project-create-request", "project-create-idem"
    create_args = {
        "label": "W21 response contract",
        "workspace": "field-identity-probe",
        "policy": {"permissions": ["inspect", "project_write"]},
        "request_id": request_id,
        "idempotency_key": idempotency_key,
    }
    create_request = {
        "operation": "operation_call",
        "arguments": {"operation_id": "project.create", "arguments": create_args},
        "execution": {"request_id": request_id, "idempotency_key": idempotency_key},
    }
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_call = pool.submit(daemon.dispatch, create_request)
            assert entered.wait(timeout=5)
            pending = daemon.dispatch(create_request)
            assert pending["success"] is True
            assert pending["data"]["status"] == "RUNNING"
            pending_job_id = pending["data"]["job_id"]
            assert pending["execution"]["job_id"] == pending_job_id
            assert pending["execution"]["request_id"] == request_id
            assert pending["execution"]["idempotency_key"] == idempotency_key

            release.set()
            terminal = first_call.result(timeout=5)
            project = terminal["data"]["project"]
            assert terminal["execution"]["request_id"] == request_id
            assert terminal["execution"]["idempotency_key"] == idempotency_key
            assert terminal["execution"]["operation_id"]
            assert project["workspace"] == str((project_root / "field-identity-probe").resolve())

        wait_response = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "job.wait", "arguments": {
                "job_id": pending_job_id, "timeout_s": 2, "poll_interval_s": 0.01,
            }},
            "execution": {"request_id": "project-create-readonly-wait"},
        })
        assert wait_response["success"] is True
        assert wait_response["data"]["job_id"] == pending_job_id
        assert wait_response["data"]["operation"]["operation"] == "project.create"
        assert wait_response["data"]["result"]["data"]["project"] == project

        async def read_exact_job(job_id):
            assert job_id == pending_job_id
            return wait_response

        resolved = asyncio.run(runner._resolve_project_create_response(
            pending, request_id=request_id, idempotency_key=idempotency_key,
            wait_for_job=read_exact_job))
        assert resolved == project
        direct = asyncio.run(runner._resolve_project_create_response(
            terminal, request_id=request_id, idempotency_key=idempotency_key,
            wait_for_job=lambda _job_id: pytest.fail("terminal project response must not poll")))
        assert direct == project
        assert daemon.backend.worker is None
    finally:
        release.set()
        daemon.close()


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
    assert {"job.list", "job.status", "job.wait"} <= set(logical)
    assert all(isinstance(schema, dict) and schema.get("type") == "object"
               for schema in tools.values())


def test_verify_plan_rejects_hash_mismatch_and_source_manifest_drift(tmp_path, monkeypatch):
    task_root = tmp_path / "task"
    task_root.mkdir()
    run_root = task_root / "run"
    run_root.mkdir()
    server_home_root = task_root / "h"
    server_home_root.mkdir()
    server_home_id = "0123456789abcdef"
    server_home = server_home_root / server_home_id
    plan = {
        "schema": runner.SCHEMA,
        "run_root": str(run_root),
        "task_root": str(task_root),
        "server_home_root": str(server_home_root),
        "server_home_id": server_home_id,
        "server_home": str(server_home),
        "server_home_path_budget": runner._server_home_budget(server_home),
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

    relocated = dict(candidate)
    relocated["server_home"] = str(server_home_root / "fedcba9876543210")
    relocated.pop("freeze_sha256")
    relocated["freeze_sha256"] = runner.sha256_value(relocated)
    with pytest.raises(runner.RunnerError, match="does not match its task-owned root"):
        runner.verify_plan(relocated, expected_sha256=relocated["freeze_sha256"])


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
    assert "project.create.wait" not in [action for action, _ in fake.calls]
    assert any(action == "session.disconnect" for action, _ in fake.calls)
    assert any(action == "session.stop" for action, _ in fake.calls)


def test_project_create_pending_response_uses_one_frozen_readonly_wait(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_pending=True)
    report = asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
        clock=lambda: 100.0, preflight=lambda: []))
    calls = [(action, params) for action, params in fake.calls if action == "project.create.wait"]
    assert len(calls) == 1
    action, params = calls[0]
    assert params["arguments"]["job_id"] == "project-create-job"
    assert params["arguments"]["timeout_s"] == plan["budgets"]["project_create_wait_timeout_seconds"]
    assert params["execution"]["request_id"] == plan["request_ids"]["project_create_wait"]
    intent = state.value["actions"]["project.create.wait"]
    assert intent["request_id"] == plan["request_ids"]["project_create_wait"]
    assert intent["status"] == "RESPONSE_RECORDED"
    assert report["project_id"] == fake.project_id


@pytest.mark.parametrize("fault", ["wrong_request", "wrong_idempotency", "wrong_job_binding"])
def test_project_create_pending_envelope_binding_mismatch_refuses_before_session_start(fault, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_pending=True,
                             project_create_fault=fault)
    with pytest.raises(runner.RunnerError, match="exact request/job binding"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert "session.start" not in [action for action, _ in fake.calls]


@pytest.mark.parametrize("fault", ["wrong_job", "wrong_request", "wrong_operation_id", "failed"])
def test_project_create_wait_mismatch_refuses_without_starting_session(fault, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_pending=True,
                             project_wait_fault=fault)
    with pytest.raises(runner.RunnerError):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert [action for action, _ in fake.calls].count("project.create.wait") == 1
    assert "session.start" not in [action for action, _ in fake.calls]
    assert state.value["failure_cleanup"]["status"] == "NOT_POSSIBLE_UNVERIFIED_SESSION_BINDING"


def test_project_create_legacy_flat_response_is_not_recursively_unwrapped(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_fault="flat_project")
    with pytest.raises(runner.RunnerError, match="neither data.project nor a pending job envelope"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert "session.start" not in [action for action, _ in fake.calls]


def test_project_create_wait_budget_is_frozen_and_bounded(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    plan["budgets"]["project_create_wait_timeout_seconds"] = runner.RPC_WAIT_S + 1
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_pending=True)
    with pytest.raises(runner.RunnerError, match="wait budget is invalid"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    assert "project.create.wait" not in [action for action, _ in fake.calls]
    assert "session.start" not in [action for action, _ in fake.calls]


def test_exception_group_exposes_only_runner_owned_cause_summary():
    error = ExceptionGroup("task failure", [
        runner.RunnerError("project.create omitted its durable operation identity"),
        ValueError("secret C:\\Users\\person\\private value"),
    ])
    assert runner._safe_runner_error_causes(error) == [{
        "type": "RunnerError",
        "message": "project.create omitted its durable operation identity",
    }]


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
        "request_id": plan["request_ids"]["fixture_execute"],
        "idempotency_key": plan["idempotency_keys"]["fixture_execute"],
        "project_id": "project-test",
    }]
    assert state.value["job_ids"] == ["job-test"]
    assert state.value["recovery"]["job_ids"] == ["job-test"]
    assert state.value["status"] == "UNKNOWN"


def test_project_create_timeout_queries_once_by_frozen_request_without_none_project_match(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, timeout_on="project.create")
    with pytest.raises(runner.RunnerError, match="transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("project.create") == 1
    assert operations.count("job.list") == 1
    assert "session.start" not in operations
    query = state.value["recovery"]["read_only_query"]
    assert query["original_request_id"] == plan["request_ids"]["project_create"]
    assert query["original_idempotency_key"] == plan["idempotency_keys"]["project_create"]
    assert query["matching_job_rows"] == [{
        "job_id": "job-created-before-timeout", "status": "RUNNING",
        "request_id": plan["request_ids"]["project_create"],
        "idempotency_key": plan["idempotency_keys"]["project_create"],
        "project_id": None,
    }]


def test_project_create_wait_timeout_queries_original_job_once_without_replaying_wait(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, project_create_pending=True,
                             timeout_on="project.create.wait")
    with pytest.raises(runner.RunnerError, match="transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("project.create") == 1
    assert operations.count("project.create.wait") == 1
    assert operations.count("job.status") == 1
    assert "session.start" not in operations
    query = state.value["recovery"]["read_only_query"]
    assert query["action"] == "project.create.wait"
    assert query["job_id"] == "project-create-job"
    assert query["original_request_id"] == plan["request_ids"]["project_create"]
    assert query["original_idempotency_key"] == plan["idempotency_keys"]["project_create"]
    assert query["exact_job_identity_confirmed"] is True
    assert query["observed_request_id"] == plan["request_ids"]["project_create"]
    assert state.value["status"] == "UNKNOWN"


def _session_start_job_row(plan, *, project_id="project-test", job_id="job-session-start",
                           status="SUCCEEDED"):
    return {
        "job_id": job_id,
        "status": status,
        "metadata": {"project_id": project_id},
        "operation": {
            "operation": "session.start",
            "request_id": plan["request_ids"]["session_start"],
            "idempotency_key": plan["idempotency_keys"]["session_start"],
        },
    }


@pytest.mark.parametrize("start_failure", ["timeout", "unknown_no_job_id"])
def test_unknown_session_start_queries_one_project_scoped_job_list_and_never_continues(
        start_failure, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(
        plan, state,
        timeout_on="session.start" if start_failure == "timeout" else None,
        unknown_on="session.start" if start_failure == "unknown_no_job_id" else None,
        job_list_rows=[_session_start_job_row(plan, status="SUCCEEDED")],
    )
    with pytest.raises(runner.RunnerError, match="session.start.*UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))

    operations = [action for action, _ in fake.calls]
    start_index = operations.index("session.start")
    assert operations.count("session.start") == 1
    assert operations.count("job.list") == 1
    assert operations[start_index + 1:] == ["job.list"]
    query_call = next(params for action, params in fake.calls if action == "job.list")
    assert query_call["operation_id"] == "job.list"
    assert query_call["arguments"] == {"limit": 100, "project_id": fake.project_id}
    assert query_call["execution"]["project_id"] == fake.project_id
    query = state.value["recovery"]["read_only_query"]
    assert query["status"] == "QUERY_RESPONSE_RECORDED"
    assert query["action"] == "session.start"
    assert query["original_request_id"] == plan["request_ids"]["session_start"]
    assert query["original_idempotency_key"] == plan["idempotency_keys"]["session_start"]
    assert query["original_project_id"] == fake.project_id
    assert query["original_operation"] == "session.start"
    assert query["query_complete"] is True
    assert query["match_resolution"] == "UNIQUE_EXACT_MATCH"
    assert query["exact_job_identity_confirmed"] is True
    assert query["matching_job_rows"] == [{
        "job_id": "job-session-start", "status": "SUCCEEDED",
        "request_id": plan["request_ids"]["session_start"],
        "idempotency_key": plan["idempotency_keys"]["session_start"],
        "project_id": fake.project_id, "operation": "session.start",
    }]
    assert state.value["job_ids"] == ["job-session-start"]
    assert state.value["recovery"]["job_ids"] == ["job-session-start"]
    assert state.value["status"] == "UNKNOWN"
    assert state.value["recovery"]["replay_permitted"] is False
    assert state.value["recovery"]["cleanup_permitted"] is False


@pytest.mark.parametrize("fault", [
    "wrong_request", "wrong_idempotency", "wrong_project", "wrong_operation",
    "missing_request", "missing_idempotency", "missing_project", "missing_operation",
    "missing_job_id", "duplicate",
])
def test_session_start_job_list_rejects_nonunique_or_incomplete_bindings(fault, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    row = _session_start_job_row(plan)
    if fault == "wrong_request":
        row["operation"]["request_id"] = "foreign-request"
    elif fault == "wrong_idempotency":
        row["operation"]["idempotency_key"] = "foreign-idempotency"
    elif fault == "wrong_project":
        row["metadata"]["project_id"] = "foreign-project"
    elif fault == "wrong_operation":
        row["operation"]["operation"] = "session.connect"
    elif fault == "missing_request":
        row["operation"].pop("request_id")
    elif fault == "missing_idempotency":
        row["operation"].pop("idempotency_key")
    elif fault == "missing_project":
        row["metadata"].pop("project_id")
    elif fault == "missing_operation":
        row["operation"].pop("operation")
    elif fault == "missing_job_id":
        row.pop("job_id")
    elif fault == "duplicate":
        row = [row, dict(row)]
    fake = _FakeStdioSession(plan, state, timeout_on="session.start",
                             job_list_rows=row if isinstance(row, list) else [row])
    with pytest.raises(runner.RunnerError, match="session.start transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))

    operations = [action for action, _ in fake.calls]
    assert operations.count("session.start") == 1
    assert operations.count("job.list") == 1
    assert operations[operations.index("session.start") + 1:] == ["job.list"]
    query = state.value["recovery"]["read_only_query"]
    assert query["exact_job_identity_confirmed"] is False
    assert state.value["job_ids"] == []
    if fault == "duplicate":
        assert query["match_resolution"] == "AMBIGUOUS_EXACT_MATCH"
        assert len(query["matching_job_rows"]) == 2
    elif fault == "missing_job_id":
        assert query["match_resolution"] == "MATCH_MISSING_JOB_ID"
        assert len(query["matching_job_rows"]) == 1
    else:
        assert query["match_resolution"] == "NO_EXACT_MATCH"
        assert query["matching_job_rows"] == []
    assert state.value["status"] == "UNKNOWN"


def test_session_start_job_list_does_not_treat_truncated_page_as_complete(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, timeout_on="session.start",
                             job_list_rows=[_session_start_job_row(plan)],
                             job_list_has_more=True)
    with pytest.raises(runner.RunnerError, match="session.start transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("job.list") == 1
    query = state.value["recovery"]["read_only_query"]
    assert query["query_complete"] is False
    assert query["match_resolution"] == "INCOMPLETE_QUERY"
    assert query["exact_job_identity_confirmed"] is False
    assert state.value["job_ids"] == []
    assert operations[operations.index("session.start") + 1:] == ["job.list"]


def test_session_start_job_list_timeout_keeps_unknown_and_does_not_retry(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, timeout_on="session.start", job_list_timeout=True)
    with pytest.raises(runner.RunnerError, match="session.start transport outcome is UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("session.start") == 1
    assert operations.count("job.list") == 1
    assert operations[operations.index("session.start") + 1:] == ["job.list"]
    query = state.value["recovery"]["read_only_query"]
    assert query["status"] == "QUERY_UNKNOWN"
    assert query["exact_job_identity_confirmed"] is not True
    assert state.value["job_ids"] == []
    assert state.value["status"] == "UNKNOWN"


@pytest.mark.parametrize("fault", [None, "wrong_request", "wrong_idempotency", "wrong_project",
                                   "wrong_operation", "missing_request", "missing_idempotency",
                                   "missing_operation"])
def test_session_start_reported_job_id_requires_exact_job_status_binding(fault, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(
        plan, state, unknown_on="session.start",
        session_start_unknown_job_id="job-reported-by-start", job_status_fault=fault,
    )
    with pytest.raises(runner.RunnerError, match="session.start returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    operations = [action for action, _ in fake.calls]
    assert operations.count("session.start") == 1
    assert operations.count("job.status") == 1
    assert "job.list" not in operations
    assert operations[operations.index("session.start") + 1:] == ["job.status"]
    query = state.value["recovery"]["read_only_query"]
    assert query["query_job_id"] == "job-reported-by-start"
    assert query["exact_job_identity_confirmed"] is (fault is None)
    assert query.get("job_id") == ("job-reported-by-start" if fault is None else None)
    assert state.value["job_ids"] == (["job-reported-by-start"] if fault is None else [])
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
