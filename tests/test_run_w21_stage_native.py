"""Offline protocol tests for the bounded W21 stdio runner.

The transport double below models public MCP envelopes only. It never starts
the native server, Java Worker, COMSOL engine, or solver.
Solve-readback values are synthetic transport payloads and prove parser/binding
behavior only; they are not native numerical or scientific evidence.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_code import describe_source, execution_result


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "w21_stage_native_runner", REPOSITORY / "tools/run_w21_stage_native.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def _plan(tmp_path: Path, *, worker_epoch: int = 7,
          mode: str = runner.METADATA_MODE) -> dict:
    root = REPOSITORY
    run_root = tmp_path / "run"
    workspace_root = run_root / "workspaces"
    workspace_root.mkdir(parents=True)
    server_home_root = tmp_path / "h"
    server_home_root.mkdir()
    server_home_id = "0123456789abcdef"
    server_home = server_home_root / server_home_id
    plan = {
        "schema": runner._mode_schema(mode), "mode": mode, "kind": runner._mode_kind(mode),
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
        "budgets": runner._mode_budgets(mode),
        "_worker_epoch": worker_epoch,
    }
    if mode == runner.SOLVE_READBACK_MODE:
        plan["published_tool_schemas"]["run_study"] = {"type": "object", "properties": {}}
        plan["logical_operation_schemas"]["dataset.solution_indices"] = {"type": "object"}
        plan["logical_operation_schemas"]["result.evaluate"] = {"type": "object"}
        plan["request_ids"].update({
            name: f"req-{name}" for name in ("study_solve", "solution_indices", "result_evaluate")
        })
        plan["idempotency_keys"].update({
            name: f"idem-{name}" for name in ("study_solve", "solution_indices", "result_evaluate")
        })
    plan["freeze_sha256"] = runner.sha256_value(plan)
    return plan


def _state(tmp_path: Path, *, schema: str = runner.SCHEMA) -> runner._RunState:
    path = tmp_path / "run" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": schema, "run_id": "w21-test-run",
        "freeze_sha256": "frozen-test-hash", "status": "PREPARED",
        "action_history": [], "job_ids": [],
        "study_dispatch": 0, "study_dispatch_possible": 0,
        "study_dispatch_status": "NOT_DISPATCHED",
        "solver_dispatch": 0, "solver_dispatch_possible": 0,
        "solver_dispatch_status": "NOT_DISPATCHED",
        "solution_tuple_reads": 0, "solution_tuple_reads_possible": 0,
        "solution_tuple_reads_status": "NOT_DISPATCHED",
        "field_reads": 0, "field_reads_possible": 0,
        "field_reads_status": "NOT_DISPATCHED",
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
    monkeypatch.setattr(runner, "_published_tool_schemas", lambda _root, names=None: {
        name: {"type": "object"} for name in (names or runner.REQUIRED_TOOLS)
    })
    monkeypatch.setattr(runner, "_logical_schemas", lambda _root, operation_ids=None: {
        name: {"type": "object"} for name in (operation_ids or runner.LOGICAL_OPERATIONS)
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
                 job_status_fault: str | None = None,
                 solve_fault: str | None = None,
                 probe_payload_override: Any | None = None,
                 connect_identity: dict[str, Any] | None = None,
                 model_create_unknown_code_only: bool = False):
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
        self.solve_fault = solve_fault
        self.probe_payload_override = probe_payload_override
        self.model_create_unknown_code_only = model_create_unknown_code_only
        self.connect_identity = {
            "remote_engine_version": "6.4.0.293",
            "remote_engine_build": "293",
            "remote_engine_build_source": "remote-connect-reply",
        }
        if connect_identity is not None:
            self.connect_identity.update(connect_identity)
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

    def _ticket_reply(self, params: dict, *, revision: int, operation_id: str,
                      job_id: str) -> dict:
        execution = dict(params["execution"])
        execution.pop("expected_revision", None)
        execution.update({
            "project_id": self.project_id, "session_id": self.session_id,
            "model_ref": dict(self.model_ref), "revision": revision,
            "operation_id": operation_id, "request_hash": "a" * 64,
            "job_id": job_id,
        })
        return execution

    def _java_execution_response(self, params: dict, payload: Any) -> dict:
        arguments = params["arguments"]
        description = describe_source(
            self.plan["project_workspace"], arguments["source_artifact"], arguments["entrypoint"],
        )
        worker_result = {
            "executed": True,
            "model_tag": "w21model",
            "source_sha256": description["source_sha256"],
            "entrypoint": description["entrypoint"],
            "readback": payload,
        }
        produced = execution_result(
            description=description,
            worker_reply={"ok": True, "result": worker_result},
            before={"model_tag": "w21model", "revision": params["execution"]["expected_revision"]},
            after={"model_tag": "w21model", "revision": params["execution"]["expected_revision"]},
        )
        # ExecutionService preserves this non-business success marker inside
        # the public ActionResult data mapping.
        public_data = {**produced["data"], "execution_success": produced["execution_success"]}
        execution = self._execution_reply(params)
        execution["revision"] += 1
        return {"success": produced["success"], "data": public_data,
                "error": produced["error"], "execution": execution}

    async def call_tool(self, name: str, params: dict):
        operation = params.get("operation_id")
        inner = params.get("arguments", {})
        execution = params.get("execution", {})
        if operation == "model.inspect":
            action = ("model.inspect.after_fixture"
                      if execution.get("request_id") == self.plan["request_ids"]["model_inspect_after_fixture"]
                      else "model.inspect")
        elif operation == "artifact.register":
            assert execution.get("project_id") == self.project_id
            assert execution.get("session_id") == self.session_id
            assert not Path(inner["path"]).is_absolute()
            assert ".." not in Path(inner["path"]).parts
            action = "fixture.register" if Path(inner["path"]).name == "W21Fixture.java" else "probe.register"
        elif operation == "code.execute_java":
            assert not Path(inner["source_artifact"]).is_absolute()
            assert ".." not in Path(inner["source_artifact"]).parts
            action = "fixture.execute" if inner.get("entrypoint") == "W21Fixture" else "probe.execute"
        elif operation == "job.wait":
            action = "project.create.wait"
        elif name == "run_study":
            action = "study.solve"
        else:
            action = operation or ("model_create" if name == "model_create" else None)
        if action in {"job.list", "job.status"}:
            query = self.state.value.get("recovery", {}).get("read_only_query", {})
            assert query.get("status") == "READ_ONLY_QUERY_INTENT"
            assert query.get("params_sha256") == runner.sha256_value(params)
        if action not in {"job.list", "job.status"}:
            self._assert_pre_dispatch_intent(params)
        if action == "study.solve":
            durable = json.loads(self.state.path.read_text(encoding="utf-8"))
            capture = durable.get("probe_capture")
            assert isinstance(capture, dict)
            assert capture.get("status") == "RAW_UNINTERPRETED"
            assert capture.get("project_id") == self.project_id
            assert capture.get("session_id") == self.session_id
            assert capture.get("model_ref") == params["execution"]["model_ref"]
            assert capture.get("revision") == params["execution"]["expected_revision"]
            assert durable.get("study_dispatch") == durable.get("solver_dispatch") == 0
        self.calls.append((action or name, params))

        if action == "model_create" and self.model_create_unknown_code_only:
            request_id, _ = self._identity_in_params(params)
            return {
                "success": False,
                "error": {
                    "code": "EXECUTION_STATE_UNKNOWN",
                    "message": "backend execution failed; inspect worker evidence",
                    "safe_retry": False,
                    "type": "AttributeError",
                },
                "data": {},
                "execution": {
                    "request_id": request_id,
                    "job_id": "model-create-unknown-job",
                },
            }

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
                **self.connect_identity,
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
            path = Path(self.plan["project_workspace"]) / inner["path"]
            return {"success": True, "data": {"sha256": runner.sha256_file(path)}}
        if action == "fixture.execute":
            return self._java_execution_response(
                params, {"status": "BUILT_NOT_SOLVED"},
            )
        if action == "probe.execute":
            payload = self.probe_payload_override
            if payload is None:
                payload = json.dumps({
                    "probe": "W21FieldIdentityProbe", "status": "STRUCTURE_CAPTURED_ONLY",
                    "native_admission": "UNVERIFIED",
                    "identity": {"model_tag": "w21model"},
                }, ensure_ascii=False, separators=(",", ":"))
            response = self._java_execution_response(params, payload)
            if self.probe_revision_drift:
                response["execution"]["revision"] += 1
            return response
        if action == "study.solve":
            execution = self._ticket_reply(
                params, revision=params["execution"]["expected_revision"] + 1,
                operation_id="op-study-solve", job_id="job-study-solve",
            )
            data = {"study_tag": inner.get("study_tag", params.get("study_tag"))}
            if self.solve_fault == "wrong_request":
                execution["request_id"] = "foreign-request"
            elif self.solve_fault == "wrong_project":
                execution["project_id"] = "foreign-project"
            elif self.solve_fault == "wrong_revision":
                execution["revision"] += 1
            elif self.solve_fault == "solve_bad_hash":
                execution["request_hash"] = "not-a-sha256"
            if self.solve_fault == "wrong_study_tag":
                data["study_tag"] = "std2"
            return {"success": True, "data": data, "execution": execution}
        if action == "dataset.solution_indices":
            execution = self._ticket_reply(
                params, revision=params["execution"]["expected_revision"],
                operation_id="op-solution-indices", job_id="job-solution-indices",
            )
            data = {
                "dataset": "dset1", "solution": "sol1", "binding_complete": True,
                "pair_mapping_complete": True,
                "binding_source": "SolutionInfo.getSolnum(outer, strict)",
                "outer_indices": [1], "inner_indices": [1, 2],
                "solnum_pairs": [
                    {"outer": 1, "inner": 1, "solnum": 1},
                    {"outer": 1, "inner": 2, "solnum": 2},
                ],
            }
            if self.solve_fault == "tuple_wrong_request":
                execution["request_id"] = "foreign-request"
            elif self.solve_fault == "tuple_wrong_project":
                execution["project_id"] = "foreign-project"
            elif self.solve_fault == "tuple_wrong_revision":
                execution["revision"] += 1
            return {"success": True, "data": data, "execution": execution}
        if action == "result.evaluate":
            revision = params["execution"]["expected_revision"]
            if self.solve_fault in {"wrong_result_revision", "result_wrong_revision"}:
                revision += 2
            elif self.solve_fault == "result_revision_advance":
                revision += 1
            execution = self._ticket_reply(
                params, revision=revision,
                operation_id="op-result-evaluate", job_id="job-result-evaluate",
            )
            if self.solve_fault in {"wrong_request", "result_wrong_request"}:
                execution["request_id"] = "foreign-request"
            elif self.solve_fault == "result_wrong_project":
                execution["project_id"] = "foreign-project"
            elif self.solve_fault == "result_wrong_key":
                execution["idempotency_key"] = "foreign-idempotency"
            elif self.solve_fault == "result_bad_hash":
                execution["request_hash"] = "not-a-sha256"
            point_count = 32769 if self.solve_fault == "numeric_cap" else 2
            if point_count == 2:
                values = [[[[300.0, 301.0], [302.0, 303.0]]]]
            else:
                values = [[[[300.0] * point_count, [302.0] * point_count]]]
            if self.solve_fault == "nonfinite":
                values[0][0][1][0] = float("nan")
            elif self.solve_fault == "overflow":
                values[0][0][1][0] = 10 ** 400
            pairs = [
                {"outer": 1, "inner": 1, "solnum": 1, "parameters": {"t": 0.0}},
                {"outer": 1, "inner": 2, "solnum": 2, "parameters": {"t": 0.5}},
            ]
            if self.solve_fault == "wrong_tuple":
                pairs[1]["solnum"] = 7
            field_array = {
                "values": values, "axes": ["expression", "outer", "inner", "point"],
                "shape": [1, 1, 2, point_count], "coords": {
                    "expression": ["T"], "outer": [1], "inner": [1, 2],
                    "point": list(range(1, point_count + 1)),
                    "spatial": ([[0.0, 0.0, 0.0], [0.05, 0.0, 0.0]]
                                if point_count == 2 else None),
                }, "units": {"expression": {"T": "K"}, "point": "m"},
                "metadata": {"pair_mapping_complete": True, "solution_pairs": pairs,
                              "unit_readback_status": "VERIFIED"},
                "is_complex": False,
            }
            budget_record = {"status": "PASS", "allowed": True, "publish_allowed": True}
            budget_status = "PASS"
            budget_publish = True
            if self.solve_fault == "truncated":
                budget_record["publish_allowed"] = False
                budget_status = "BLOCKED"
                budget_publish = False
            data = {
                "status": {"ok": True}, "dataset": "dset1", "solution": "sol1",
                "expressions": ["T"],
                "storage": "inline", "values": values, "field_array": field_array,
                "solution_axes": {"outer": [1], "inner": [1, 2], "pair_mapping_complete": True},
                "expression_units": {"T": "K"},
                "result_budget": {"status": budget_status, "publish_allowed": budget_publish,
                                  "records": [budget_record]},
                "cleanup": {"cleanup_failed": self.solve_fault in {"cleanup_unknown", "cleanup_failed_only"}},
                "execution_state_unknown": self.solve_fault == "cleanup_unknown",
                "observation_ref": {"observation_id": "obs-test", "sha256": "b" * 64},
            }
            if self.solve_fault in {"cleanup_unknown", "cleanup_failed_only"}:
                data["status"] = {"ok": False, "cleanup_failed": True,
                                  "execution_state_unknown": self.solve_fault == "cleanup_unknown"}
            if self.solve_fault == "result_wrong_outer_axis":
                field_array["coords"]["outer"] = [2]
            elif self.solve_fault == "result_wrong_shape":
                field_array["shape"][3] -= 1
            elif self.solve_fault == "result_missing_pair":
                field_array["metadata"]["solution_pairs"].pop()
            elif self.solve_fault == "result_wrong_point_axis":
                field_array["coords"]["point"] = [0] * point_count
            elif self.solve_fault == "result_wrong_spatial_shape":
                field_array["coords"]["spatial"] = [[0.0, 0.0, 0.0]]
            elif self.solve_fault == "result_wrong_expression":
                field_array["coords"]["expression"] = ["u"]
            elif self.solve_fault == "result_oversized_json":
                field_array["metadata"]["padding"] = "x" * (8 * 1024 * 1024)
            elif self.solve_fault == "result_unit_metadata_conflict":
                field_array["units"]["expression"]["T"] = "degC"
            if self.solve_fault == "missing_observation":
                data.pop("observation_ref")
            return {"success": True, "data": data, "execution": execution}
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


def _run_solve_readback(tmp_path, monkeypatch, *, solve_fault=None, unknown_on=None):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, solve_fault=solve_fault, unknown_on=unknown_on)
    report = asyncio.run(runner.run_metadata_protocol(
        runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
    ))
    return plan, state, fake, report


def test_srb_ok(tmp_path, monkeypatch):
    plan, state, fake, report = _run_solve_readback(
        tmp_path, monkeypatch, solve_fault="result_unit_metadata_conflict",
    )
    actions = [action for action, _ in fake.calls]
    start_params = next(params for action, params in fake.calls if action == "session.start")
    assert start_params["execution"]["rpc_timeout_s"] == runner.RPC_WAIT_S == 45
    assert actions.count("study.solve") == 1
    assert actions.count("dataset.solution_indices") == 1
    assert actions.count("result.evaluate") == 1
    assert actions.count("probe.register") == actions.count("probe.execute") == 1
    assert actions.index("probe.execute") < actions.index("study.solve")
    assert report["mode"] == runner.SOLVE_READBACK_MODE
    assert report["kind"] == runner.SOLVE_READBACK_KIND
    assert report["study_dispatch"] == report["solver_dispatch"] == 1
    assert report["native_admission"] == report["physical_validation"] == "UNVERIFIED"
    assert report["status"] == "SOLVE_READBACK_CAPTURED_ONLY_NOT_ADMISSION"
    observation = report["probe_observation"]
    assert observation["status"] == "RAW_UNINTERPRETED"
    assert observation["source_sha256"] == plan["probe_sha256"]
    assert observation["project_id"] == report["project_id"]
    assert observation["session_id"] == report["session_id"]
    assert observation["model_ref"] == report["model_binding"]["model_ref"]
    assert observation["revision"] == state.value["study_solve_progress"]["revision_before"]
    assert observation["request_id"] == plan["request_ids"]["probe_execute"]
    assert observation["idempotency_key"] == plan["idempotency_keys"]["probe_execute"]
    raw_probe = observation["raw_payload_json"]
    assert len(raw_probe.encode("utf-8")) == observation["raw_payload_bytes"]
    assert hashlib.sha256(raw_probe.encode("utf-8")).hexdigest() == observation["raw_payload_sha256"]
    assert json.loads(raw_probe)["native_admission"] == "UNVERIFIED"
    persisted = json.loads((Path(plan["run_root"]) / "solve_readback_receipt.json").read_text())
    assert persisted["probe_observation"] == observation
    captured = report["solve_readback"]
    assert captured["tuple"] == {"outer": 1, "inner": 2, "solnum": 2}
    assert captured["field_values"] == [302.0, 303.0]
    assert captured["field_units"]["expression"]["T"] == "degC"
    assert captured["unit_acceptance"] == "UNVERIFIED_CONFIGURED_OR_MODEL_DEPENDENT"
    assert captured["coordinate_frame_acceptance"] == "UNVERIFIED"
    assert captured["mesh_intrinsic_identity"] == "UNVERIFIED"
    assert captured["read_scope"].startswith("complete returned dataset FieldArray")
    assert captured["tuple_readback"]["operation"]["request_id"] == plan["request_ids"]["solution_indices"]
    assert captured["field_operation"]["request_id"] == plan["request_ids"]["result_evaluate"]
    assert captured["field_operation"]["revision_after"] in {
        captured["field_operation"]["revision_before"],
        captured["field_operation"]["revision_before"] + 1,
    }
    assert captured["cleanup"]["cleanup_failed"] is False
    assert report["cleanup"]["status"] == "CLEANUP_COMPLETE"
    assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 1
    solve_params = next(params for action, params in fake.calls if action == "study.solve")
    probe_params = next(params for action, params in fake.calls if action == "probe.execute")
    tuple_params = next(params for action, params in fake.calls
                        if action == "dataset.solution_indices")
    result_params = next(params for action, params in fake.calls if action == "result.evaluate")
    assert solve_params["study_tag"] == "std1"
    assert probe_params["execution"]["request_id"] == plan["request_ids"]["probe_execute"]
    assert probe_params["execution"]["idempotency_key"] == plan["idempotency_keys"]["probe_execute"]
    assert probe_params["execution"]["model_ref"] == solve_params["execution"]["model_ref"]
    assert probe_params["execution"]["expected_revision"] + 1 == solve_params["execution"]["expected_revision"]
    assert report["probe_observation"]["revision"] == solve_params["execution"]["expected_revision"]
    assert report["probe_observation"]["worker_execution"]["entrypoint"] == "W21FieldIdentityProbe"
    assert set(report["probe_observation"]["worker_execution"]["before"]) == {"model_tag", "revision"}
    assert report["fixture_worker_execution"]["worker"]["source_sha256"] == plan["fixture_sha256"]
    for params in (solve_params, tuple_params, result_params):
        assert params["execution"]["queue_timeout_s"] == 30
        assert params["execution"]["execution_timeout_s"] == 240
    assert result_params["arguments"]["spec"] == {
        "expressions": ["T"],
        "solution": {"dataset": "dset1", "solution": "sol1"},
        "aggregate": "none", "complex_mode": "preserve", "storage": "inline",
    }


def test_srb_preserves_bounded_table_limit_probe_and_continues_readback(tmp_path, monkeypatch):
    limited_probe = (
        '{"probe":"W21FieldIdentityProbe","status":"OUTPUT_LIMIT_EXCEEDED",'
        '"code":"TABLE_ROW_LIMIT_EXCEEDED","native_admission":"UNVERIFIED",'
        '"payload_complete":false}'
    )
    assert len(limited_probe.encode("utf-8")) == 157
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, probe_payload_override=limited_probe)

    report = asyncio.run(runner.run_metadata_protocol(
        runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
    ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("probe.execute") == 1
    assert actions.count("study.solve") == 1
    assert actions.count("dataset.solution_indices") == 1
    assert actions.count("result.evaluate") == 1
    observation = report["probe_observation"]
    assert observation["status"] == "RAW_UNINTERPRETED"
    assert observation["capture_status"] == "OUTPUT_LIMIT_EXCEEDED"
    assert observation["capture_code"] == "TABLE_ROW_LIMIT_EXCEEDED"
    assert observation["capture_completeness"] == "PARTIAL"
    assert observation["metadata_complete"] is False
    assert observation["raw_payload_json"] == limited_probe
    assert observation["raw_payload_bytes"] == 157
    assert hashlib.sha256(limited_probe.encode("utf-8")).hexdigest() == observation["raw_payload_sha256"]
    durable = json.loads(state.path.read_text(encoding="utf-8"))["probe_capture"]
    assert durable["capture_completeness"] == "PARTIAL"
    assert durable["metadata_complete"] is False
    assert durable["raw_payload_json"] == limited_probe
    assert report["native_admission"] == report["physical_validation"] == "UNVERIFIED"


def test_metadata_only_rejects_bounded_table_limit_probe(tmp_path, monkeypatch):
    limited_probe = (
        '{"probe":"W21FieldIdentityProbe","status":"OUTPUT_LIMIT_EXCEEDED",'
        '"code":"TABLE_ROW_LIMIT_EXCEEDED","native_admission":"UNVERIFIED",'
        '"payload_complete":false}'
    )
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.METADATA_MODE)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, probe_payload_override=limited_probe)

    with pytest.raises(runner.RunnerError, match="probe output is incomplete"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("probe.execute") == 1
    assert "study.solve" not in actions
    assert "dataset.solution_indices" not in actions
    assert "result.evaluate" not in actions
    assert state.value["status"] == "FAILED"


@pytest.mark.parametrize("change", ["unknown_code", "claimed_admission", "complete_flag", "extra_identity"])
def test_srb_rejects_unrecognized_or_claimed_partial_probe(tmp_path, monkeypatch, change):
    payload = {
        "probe": "W21FieldIdentityProbe",
        "status": "OUTPUT_LIMIT_EXCEEDED",
        "code": "TABLE_ROW_LIMIT_EXCEEDED",
        "native_admission": "UNVERIFIED",
        "payload_complete": False,
    }
    if change == "unknown_code":
        payload["code"] = "UNIT_ROW_LIMIT_EXCEEDED"
    elif change == "claimed_admission":
        payload["native_admission"] = "VERIFIED"
    elif change == "complete_flag":
        payload["payload_complete"] = True
    elif change == "extra_identity":
        payload["identity"] = {"model_tag": "w21model"}
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, probe_payload_override=json.dumps(payload))

    with pytest.raises(runner.RunnerError, match="probe output is incomplete"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("probe.execute") == 1
    assert "study.solve" not in actions
    assert "dataset.solution_indices" not in actions
    assert "result.evaluate" not in actions
    assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 0


def test_trusted_code_ticket_advances_real_execution_service_revision_and_unwraps_worker_result(tmp_path):
    """Exercise the actual trusted-code ledger and G2 result constructors offline."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "W21FieldIdentityProbe.java"
    source.write_text("public final class W21FieldIdentityProbe {}\n", encoding="utf-8")
    description = describe_source(project_root, source.name, "W21FieldIdentityProbe")

    class SnapshotAdapter:
        def model_snapshot(self, model_tag):
            return {"model_tag": model_tag, "server_instance_id": "server-test",
                    "external_event_counter": 0, "fingerprint": "snapshot-test"}

    ledger = SessionLedger(
        "session-test", "server-test", server_ownership="mcp_managed",
        permissions={"inspect", "project_write", "compute", "trusted_code"},
    )
    service = ExecutionService(ledger, SnapshotAdapter(), project_root=project_root)
    bound = service.bind_model("w21model", ownership="mcp_owned")
    model_ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    revision_before = bound["execution"]["revision"]
    raw_probe = json.dumps({"probe": "W21FieldIdentityProbe", "status": "STRUCTURE_CAPTURED_ONLY",
                            "native_admission": "UNVERIFIED",
                            "identity": {"model_tag": "w21model"}}, separators=(",", ":"))

    def callback(_arguments):
        return execution_result(
            description=description,
            worker_reply={"ok": True, "result": {
                "executed": True, "model_tag": "w21model",
                "source_sha256": description["source_sha256"],
                "entrypoint": "W21FieldIdentityProbe", "readback": raw_probe,
            }},
            before={"model_tag": "w21model", "revision": revision_before},
            after={"model_tag": "w21model", "revision": revision_before},
        )

    response = service.execute_legacy(
        "code_execute_java", callback, {"source_artifact": source.name},
        model_ref=model_ref, expected_revision=revision_before,
        request_id="probe-ticket-test", session_id="session-test", effect="trusted_code",
    )
    assert response["success"] is True
    assert response["execution"]["revision"] == revision_before + 1
    payload, evidence = runner._validate_java_execution_data(
        response["data"], expected_source_sha256=description["source_sha256"],
        expected_entrypoint="W21FieldIdentityProbe", expected_model_tag="w21model",
        label="field identity probe",
    )
    assert payload == raw_probe
    assert evidence["worker"]["source_sha256"] == description["source_sha256"]
    assert evidence["before"]["revision"] == evidence["after"]["revision"] == revision_before


def test_runner_relative_artifact_registration_reaches_real_public_route(tmp_path):
    """Use runner params against ControlDaemon's actual artifact.register route."""
    project_container = tmp_path / "projects"
    project_container.mkdir()

    class SnapshotAdapter:
        def model_snapshot(self, model_tag):
            return {"model_tag": model_tag, "server_instance_id": "server-test",
                    "external_event_counter": 0, "fingerprint": "snapshot-test"}

    ledger = SessionLedger("session-test", "server-test",
                           permissions={"inspect", "project_write", "compute"})
    service = ExecutionService(ledger, SnapshotAdapter(), project_root=project_container)
    daemon = ControlDaemon(
        tmp_path / "control", service=service, registry={}, worker=None,
        project_root=project_container,
    )
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "runner-registration-test", "workspace": "runner-registration-test",
                          "policy": {"permissions": ["project_write", "compute"]}},
            "execution": {"request_id": "create-runner-registration-test",
                          "idempotency_key": "create-runner-registration-test"},
        })
        assert created["success"] is True
        project = created["data"]["project"]
        project_id = project["project_id"]
        workspace = Path(project["workspace"])
        fixture_dir = workspace / "fixtures"
        fixture_dir.mkdir()
        source = fixture_dir / "W21Fixture.java"
        source.write_text("public final class W21Fixture {}\n", encoding="utf-8")
        relative = runner._project_relative_file(workspace, source)
        assert relative == "fixtures/W21Fixture.java"

        params = runner._operation_params("artifact.register", {
            "project_id": project_id, "path": relative, "role": "w21_probe_source",
            "classification": "task_owned_frozen_java_source",
            "idempotency_key": "fixture-register-runner-test",
            "request_id": "fixture-register-runner-test",
        }, {
            "project_id": project_id, "session_id": "session-test",
            "idempotency_key": "fixture-register-runner-test",
            "request_id": "fixture-register-runner-test", "rpc_timeout_s": 45,
        })
        response = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": params["operation_id"],
                           "arguments": params["arguments"]},
            "execution": params["execution"],
        })
        assert response["success"] is True
        assert response["data"]["sha256"] == runner.sha256_file(source)
        record = daemon.store.get_metadata("artifacts", response["data"]["artifact_id"])
        assert record["provenance"]["source_project_relative_path"] == relative
        assert record["project_id"] == project_id
    finally:
        daemon.close()


def test_srb_probe_unknown_is_durable_and_never_reaches_solve(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, unknown_on="probe.execute")

    with pytest.raises(runner.RunnerError, match="probe.execute returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("probe.execute") == 1
    assert actions.count("job.list") == 1
    assert "study.solve" not in actions
    assert "dataset.solution_indices" not in actions
    assert "result.evaluate" not in actions
    assert "session.disconnect" not in actions and "session.stop" not in actions
    assert state.value["status"] == "UNKNOWN"
    assert state.value["unknown_action"] == "probe.execute"
    assert state.value["recovery"]["request_id"] == plan["request_ids"]["probe_execute"]
    assert state.value["recovery"]["idempotency_key"] == plan["idempotency_keys"]["probe_execute"]
    assert state.value["recovery"]["replay_permitted"] is False
    assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 0


def test_srb_probe_payload_limit_refuses_before_solve(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(
        plan, state,
        probe_payload_override="x" * (runner.W21_FIELD_IDENTITY_PROBE_MAX_JSON_UTF8_BYTES + 1),
    )

    with pytest.raises(runner.RunnerError, match="probe payload exceeds"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("probe.execute") == 1
    assert "study.solve" not in actions
    assert "dataset.solution_indices" not in actions
    assert "result.evaluate" not in actions
    assert state.value["status"] == "FAILED"


@pytest.mark.parametrize(
    ("mode", "compute_allowed"),
    [(runner.METADATA_MODE, False), (runner.SOLVE_READBACK_MODE, True)],
    ids=["meta", "solve"],
)
def test_compute_policy(mode, compute_allowed, tmp_path, monkeypatch):
    """Only solve-readback grants the permission enforced by the public run_study route."""
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    _patch_isolation(monkeypatch)
    runner_root = tmp_path / "runner"
    runner_root.mkdir()
    plan = _plan(runner_root, mode=mode)
    state = _state(runner_root, schema=runner._mode_schema(mode))
    fake = _FakeStdioSession(plan, state)
    asyncio.run(runner.run_metadata_protocol(
        runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
    ))

    create_params = next(params for action, params in fake.calls if action == "project.create")
    permissions = create_params["arguments"]["policy"]["permissions"]
    expected = ["inspect", "project_write", "trusted_code", "host_control"]
    if compute_allowed:
        expected.append("compute")
    assert permissions == expected

    # Exercise the production project policy/operation gate that guards the
    # runner's run_study action, without creating a session or starting a worker.
    project_root = runner_root / "authorized-projects"
    project_root.mkdir()
    daemon = ControlDaemon(runner_root / "control", project_root=project_root)
    request_id = f"policy-{mode}-create"
    response = daemon.dispatch({
        "operation": "operation_call",
        "arguments": {"operation_id": "project.create", "arguments": {
            "label": f"W21 {mode} permission test", "workspace": f"workspace-{mode}",
            "policy": {"permissions": permissions}, "request_id": request_id,
            "idempotency_key": f"idem-{mode}-create",
        }},
        "execution": {"request_id": request_id, "idempotency_key": f"idem-{mode}-create"},
    })
    try:
        assert response["success"] is True
        project_id = response["data"]["project"]["project_id"]
        execution = {"project_id": project_id}
        if compute_allowed:
            daemon._authorize_project_execution("run_study", {"study_tag": "std1"}, execution)
        else:
            with pytest.raises(ExecutionContractError, match="project-scoped action requires compute"):
                daemon._authorize_project_execution("run_study", {"study_tag": "std1"}, execution)
    finally:
        daemon.close()


def test_srb_eval_revision_advance_is_bound_into_the_report(tmp_path, monkeypatch):
    _plan, _state, _fake, report = _run_solve_readback(
        tmp_path, monkeypatch, solve_fault="result_revision_advance",
    )
    capture = report["solve_readback"]
    operation = capture["field_operation"]
    assert operation["revision_after"] == operation["revision_before"] + 1
    assert report["model_binding"]["revision"] == operation["revision_after"]


def test_srb_freeze(tmp_path, monkeypatch):
    _patch_prepare_environment(monkeypatch)
    evidence = tmp_path / "e"
    evidence.mkdir()
    result = runner.prepare(
        version="6.4", mode=runner.SOLVE_READBACK_MODE,
        comsol_root=tmp_path / "COMSOL64", jdk_home=tmp_path / "JDK11",
        evidence_root=evidence, server_home_root=evidence / "h",
    )
    plan = result["plan"]
    assert plan["schema"] == runner.SOLVE_READBACK_SCHEMA
    assert plan["kind"] == runner.SOLVE_READBACK_KIND
    assert plan["mode"] == runner.SOLVE_READBACK_MODE
    assert plan["budgets"] == runner._mode_budgets(runner.SOLVE_READBACK_MODE)
    assert plan["budgets"]["study_dispatch"] == plan["budgets"]["solver_dispatch"] == 1
    assert plan["budgets"]["solution_tuple_reads"] == plan["budgets"]["field_reads"] == 1
    assert plan["budgets"]["queue_timeout_seconds"] == 30
    assert plan["budgets"]["execution_timeout_seconds"] == 240
    assert plan["budgets"]["ordinary_rpc_wait_seconds"] == 45
    assert plan["budgets"]["cleanup_reserve_seconds"] == 60
    assert "run_study" in plan["published_tool_schemas"]
    assert {"dataset.solution_indices", "result.evaluate"} <= plan["logical_operation_schemas"].keys()
    assert {"study_solve", "solution_indices", "result_evaluate"} <= plan["request_ids"].keys()
    assert {"study_solve", "solution_indices", "result_evaluate"} <= plan["idempotency_keys"].keys()
    assert result["status"] == "PREPARED_ONLY"

    metadata_receipt = evidence / "metadata-only-receipt.json"
    metadata_receipt.write_text(json.dumps({
        "schema": runner.SCHEMA,
        "status": "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION",
        "selected_comsol": {"version": "6.4.0.293"},
        "cleanup": {"status": "CLEANUP_COMPLETE"},
    }), encoding="utf-8")
    metadata_step = runner.prepare(
        version="6.3", comsol_root=tmp_path / "COMSOL63", jdk_home=tmp_path / "JDK11",
        evidence_root=evidence, server_home_root=evidence / "h63",
        prerequisite_64_receipt=metadata_receipt,
    )
    assert metadata_step["plan"]["mode"] == runner.METADATA_MODE
    assert metadata_step["plan"]["budgets"]["study_dispatch"] == 0
    assert metadata_step["plan"]["budgets"]["solver_dispatch"] == 0
    with pytest.raises(runner.RunnerError, match="solve-readback receipt"):
        runner.prepare(
            version="6.3", mode=runner.SOLVE_READBACK_MODE,
            comsol_root=tmp_path / "COMSOL63", jdk_home=tmp_path / "JDK11",
            evidence_root=evidence, server_home_root=evidence / "h63-solve",
            prerequisite_64_receipt=metadata_receipt,
        )


@pytest.mark.parametrize(("fault", "message"), [
    ("wrong_tuple", "pair mapping"),
    ("tuple_wrong_request", "managed request/ticket/model binding"),
    ("tuple_wrong_project", "managed request/ticket/model binding"),
    ("tuple_wrong_revision", "revision outside"),
    ("result_wrong_request", "managed request/ticket/model binding"),
    ("result_wrong_project", "managed request/ticket/model binding"),
    ("result_wrong_key", "managed request/ticket/model binding"),
    ("result_bad_hash", "managed request/ticket/model binding"),
    ("result_wrong_revision", "revision outside"),
    ("result_wrong_outer_axis", "coordinates do not contain"),
    ("result_wrong_shape", "axes, shape"),
    ("result_missing_pair", "pair mapping"),
    ("result_wrong_point_axis", "point coordinate axis"),
    ("result_wrong_spatial_shape", "spatial coordinates"),
    ("result_wrong_expression", "coordinates do not contain"),
    ("nonfinite", "nonfinite"),
    ("overflow", "overflowing"),
    ("numeric_cap", "exceeds the W21 output cap"),
    ("truncated", "budget witness"),
    ("missing_observation", "persisted observation reference"),
    ("result_oversized_json", "8 MiB output cap"),
], ids=[
    "pair", "tuple-req", "tuple-project", "tuple-rev", "field-req", "field-project",
    "field-key", "field-hash", "field-rev", "outer-axis", "shape", "missing-pair",
    "point-axis", "spatial", "expression", "nan", "overflow", "scalar-cap", "truncated",
    "observation", "json-cap",
])
def test_srb_rejects_incomplete_or_misbound_readback(fault, message, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, solve_fault=fault)
    with pytest.raises(runner.RunnerError, match=message):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))
    assert state.value["status"] == "FAILED"
    assert "session.stop" in [action for action, _ in fake.calls]


@pytest.mark.parametrize(("unknown_on", "expected_action", "state_action"), [
    ("study.solve", "study.solve", "study.solve"),
    ("dataset.solution_indices", "dataset.solution_indices", "solution_indices"),
    ("result.evaluate", "result.evaluate", "result.evaluate"),
], ids=["solve", "tuple", "field"])
def test_srb_unknown_stops_after_one_readonly_query(
        unknown_on, expected_action, state_action, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, unknown_on=unknown_on)
    with pytest.raises(runner.RunnerError, match="UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))
    actions = [action for action, _ in fake.calls]
    unknown_index = actions.index(expected_action)
    assert actions[unknown_index + 1:] == ["job.list"]
    assert actions.count("job.list") == 1
    assert "session.disconnect" not in actions and "session.stop" not in actions
    assert state.value["status"] == "UNKNOWN"
    assert state.value["recovery"]["replay_permitted"] is False
    assert state.value["recovery"]["cleanup_permitted"] is False
    assert state.value["actions"][state_action]["status"] == "UNKNOWN"
    if expected_action == "study.solve":
        assert state.value["study_dispatch_possible"] == 1
        assert state.value["solver_dispatch_possible"] == 1
        assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 0
        assert state.value["study_dispatch_status"] == state.value["solver_dispatch_status"] == "UNKNOWN"
    elif expected_action == "dataset.solution_indices":
        assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 1
        assert state.value["solution_tuple_reads_possible"] == 1
        assert state.value["solution_tuple_reads"] == 0
        assert state.value["solution_tuple_reads_status"] == "UNKNOWN"
        assert state.value["field_reads_possible"] == 0
    else:
        assert state.value["study_dispatch"] == state.value["solver_dispatch"] == 1
        assert state.value["solution_tuple_reads"] == 1
        assert state.value["field_reads_possible"] == 1
        assert state.value["field_reads"] == 0
        assert state.value["field_reads_status"] == "UNKNOWN"


@pytest.mark.parametrize(("blocked_action", "state_action", "boundary_action", "counter"), [
    ("study.solve", "study.solve", "probe.execute", "study_dispatch"),
    ("dataset.solution_indices", "solution_indices", "study.solve", "solution_tuple_reads"),
    ("result.evaluate", "result.evaluate", "dataset.solution_indices", "field_reads"),
], ids=["solve", "tuple", "field"])
def test_srb_late_server_budget_refuses_before_stage_dispatch(
        blocked_action, state_action, boundary_action, counter, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state)

    def near_work_boundary():
        # Bind the synthetic time shift to the last successful operation before
        # each guarded stage dispatch, so extra metadata calls cannot retarget
        # this acceptance boundary by changing a positional call count.
        completed = {action for action, _ in fake.calls}
        return 730.0 if boundary_action in completed else 100.0

    with pytest.raises(runner.RunnerError, match=r"server queue\+execution budget exceeds"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=near_work_boundary, preflight=lambda: [],
        ))
    actions = [action for action, _ in fake.calls]
    assert blocked_action not in actions
    assert "session.disconnect" in actions and "session.stop" in actions
    action_state = state.value["actions"][state_action]
    assert action_state["status"] == "NOT_DISPATCHED_SERVER_BUDGET"
    assert action_state["server_queue_timeout_s"] == 30
    assert action_state["server_execution_timeout_s"] == 240
    assert action_state["remaining_work_window_s"] < 270
    assert state.value[f"{counter}_possible"] == 0
    assert state.value[counter] == 0
    assert state.value[f"{counter}_status"] == "NOT_DISPATCHED_BUDGET"


def test_srb_cleanup_unknown_has_no_followup_cleanup(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    fake = _FakeStdioSession(plan, state, solve_fault="cleanup_failed_only")
    with pytest.raises(runner.RunnerError, match="result.evaluate returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state, clock=lambda: 100.0, preflight=lambda: [],
        ))
    actions = [action for action, _ in fake.calls]
    result_index = actions.index("result.evaluate")
    assert actions[result_index + 1:] == ["job.status"]
    assert "session.disconnect" not in actions and "session.stop" not in actions
    assert state.value["field_reads_possible"] == 1
    assert state.value["field_reads"] == 0
    assert state.value["field_reads_status"] == "UNKNOWN"


def test_srb_receipt_gate_requires_durable_solve_capture_and_cleanup(tmp_path):
    run_root = tmp_path / "v6.4"
    run_root.mkdir()
    receipt_path = run_root / "solve_readback_receipt.json"
    state_path = run_root / "state.json"
    isolation_path = run_root / "owned_server_isolation.json"
    receipt = {
        "schema": runner.SOLVE_READBACK_SCHEMA, "kind": runner.SOLVE_READBACK_KIND,
        "mode": runner.SOLVE_READBACK_MODE,
        "status": "SOLVE_READBACK_CAPTURED_ONLY_NOT_ADMISSION",
        "selected_comsol": {"version": "6.4.0.293"},
        "study_dispatch": 1, "solver_dispatch": 1,
        "solve_readback": {"status": "CAPTURED", "field_sha256": "a" * 64},
        "solution_tuple_readback": {
            "status": "VERIFIED", "target_tuple": {"outer": 1, "inner": 2, "solnum": 2},
        },
        "cleanup": {"status": "CLEANUP_COMPLETE", "worker_retired": True,
                     "owned_server_stopped": True},
    }
    durable_state = {
        "schema": runner.SOLVE_READBACK_SCHEMA,
        "status": receipt["status"], "receipt_path": str(receipt_path.resolve()),
        "study_dispatch": 1, "study_dispatch_possible": 1,
        "study_dispatch_status": "CONFIRMED",
        "solver_dispatch": 1, "solver_dispatch_possible": 1,
        "solver_dispatch_status": "CONFIRMED",
        "solution_tuple_reads": 1, "solution_tuple_reads_possible": 1,
        "solution_tuple_reads_status": "CONFIRMED",
        "field_reads": 1, "field_reads_possible": 1,
        "field_reads_status": "CONFIRMED",
        "solution_tuple_readback_progress": {
            "status": "VERIFIED", "tuple": {"outer": 1, "inner": 2, "solnum": 2},
        },
        "field_readback_progress": {"status": "VERIFIED", "field_sha256": "a" * 64},
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    durable_state["receipt_sha256"] = runner.sha256_file(receipt_path)
    state_path.write_text(json.dumps({
        **durable_state,
    }), encoding="utf-8")
    isolation_path.write_text(json.dumps({"status": "STOPPED"}), encoding="utf-8")
    checked = runner._check_64_receipt(receipt_path, mode=runner.SOLVE_READBACK_MODE)
    assert checked["mode"] == runner.SOLVE_READBACK_MODE
    assert checked["sha256"] == runner.sha256_file(receipt_path)

    state_path.write_text(json.dumps({
        **durable_state, "receipt_sha256": "0" * 64,
    }), encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="durable completion state"):
        runner._check_64_receipt(receipt_path, mode=runner.SOLVE_READBACK_MODE)

    state_path.write_text(json.dumps({
        **durable_state,
    }), encoding="utf-8")
    isolation_path.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="not safely stopped"):
        runner._check_64_receipt(receipt_path, mode=runner.SOLVE_READBACK_MODE)

    metadata_receipt = dict(receipt)
    metadata_receipt.update({"schema": runner.SCHEMA,
                             "kind": "W21_FIELD_IDENTITY_METADATA_PROBE",
                             "mode": runner.METADATA_MODE,
                             "status": "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION",
                             "study_dispatch": 0, "solver_dispatch": 0})
    metadata_path = run_root / "metadata_receipt.json"
    metadata_path.write_text(json.dumps(metadata_receipt), encoding="utf-8")
    metadata_checked = runner._check_64_receipt(metadata_path, mode=runner.METADATA_MODE)
    assert metadata_checked["sha256"] == runner.sha256_file(metadata_path)
    with pytest.raises(runner.RunnerError, match="cleaned-up 6.4 metadata-only"):
        runner._check_64_receipt(receipt_path, mode=runner.METADATA_MODE)
    with pytest.raises(runner.RunnerError, match="complete actual 6.4 solve-readback"):
        runner._check_64_receipt(metadata_path, mode=runner.SOLVE_READBACK_MODE)


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


def test_bind_candidate_source_refuses_a_cached_foreign_package(tmp_path, monkeypatch):
    stale = ModuleType("comsol_mcp")
    stale.__file__ = str(tmp_path / "old-wheel" / "comsol_mcp" / "__init__.py")
    monkeypatch.setitem(sys.modules, "comsol_mcp", stale)
    with pytest.raises(runner.RunnerError, match="loaded comsol_mcp module came from outside"):
        runner._bind_candidate_source(REPOSITORY)


def test_prepare_bind_root(tmp_path, monkeypatch):
    _patch_prepare_environment(monkeypatch)
    monkeypatch.setattr(runner.sys, "path", [str(tmp_path / "unrelated-cwd"), *runner.sys.path])
    observed = []
    original = runner._bind_candidate_source

    def record_binding(root):
        resolved = original(root)
        observed.append(runner.sys.path[0])
        return resolved

    monkeypatch.setattr(runner, "_bind_candidate_source", record_binding)
    evidence = tmp_path / "e"
    evidence.mkdir()
    prepared = runner.prepare(
        version="6.4", comsol_root=tmp_path / "COMSOL64",
        jdk_home=tmp_path / "jdk", evidence_root=evidence,
        source_root=REPOSITORY,
    )
    assert observed == [str(REPOSITORY.resolve())]
    assert prepared["status"] == "PREPARED_ONLY"


def test_external_cwd_load_plan_candidate(tmp_path):
    plan = _plan(tmp_path)
    plan["python"] = {"offline-test": True}
    plan["source_manifest_sha256"] = runner.sha256_value(plan["source_manifest"])
    plan.pop("freeze_sha256")
    plan["freeze_sha256"] = runner.sha256_value(plan)
    run_root = Path(plan["run_root"])
    (run_root / "freeze.json").write_text(json.dumps(plan), encoding="utf-8")
    state = {
        "schema": plan["schema"], "run_id": plan["run_id"],
        "freeze_sha256": plan["freeze_sha256"], "status": "PREPARED",
        "action_history": [],
    }
    (run_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    child = textwrap.dedent("""\
        import importlib.util, json, pathlib, sys
        plan_path = pathlib.Path(sys.argv[1])
        expected = sys.argv[2]
        runner_path = pathlib.Path(sys.argv[3])
        spec = importlib.util.spec_from_file_location("w21_cli_runner", runner_path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        runner._python_identity = lambda: plan["python"]
        runner._published_tool_schemas = lambda _root, _names=None: plan["published_tool_schemas"]
        runner._logical_schemas = lambda _root, _operations=None: plan["logical_operation_schemas"]
        runner._comsol_identity = lambda *_args: plan["selected_comsol"]
        runner._jdk_identity = lambda _home: plan["selected_jdk"]
        runner.platform.system = lambda: "Linux"
        runner_exit = runner.main(["execute", "--plan", str(plan_path),
                                   "--freeze-sha256", expected])
        import comsol_mcp
        origin = pathlib.Path(comsol_mcp.__file__).resolve()
        expected_origin = pathlib.Path(plan["source_root"], "comsol_mcp", "__init__.py").resolve()
        if origin != expected_origin:
            raise SystemExit("wrong candidate import origin")
        print(json.dumps({"runner_exit": runner_exit, "origin": str(origin)}))
    """)
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", child,
         str(run_root / "freeze.json"), plan["freeze_sha256"],
         str(REPOSITORY / "tools/run_w21_stage_native.py")],
        cwd=outside, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    output_lines = completed.stdout.splitlines()
    cli_result = json.loads(output_lines[-2])
    result = json.loads(output_lines[-1])
    assert cli_result["status"] == "REFUSED_OR_FAILED"
    assert cli_result["message"] == "execute refuses non-Windows hosts before any MCP process is started"
    assert result == {
        "runner_exit": 2,
        "origin": str(REPOSITORY / "comsol_mcp/__init__.py"),
    }
    unchanged = json.loads((run_root / "state.json").read_text(encoding="utf-8"))
    assert unchanged["status"] == "PREPARED"
    assert unchanged["action_history"] == []


def test_missing_module_cause_summary_keeps_only_safe_module_name():
    exc = ModuleNotFoundError("missing private module path", name="comsol_mcp._session_server")
    assert runner._safe_runner_error_causes(exc) == [{
        "type": "ModuleNotFoundError", "module": "comsol_mcp._session_server",
    }]


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
        "schema": runner.SCHEMA, "mode": runner.METADATA_MODE,
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
        "budgets": runner._mode_budgets(runner.METADATA_MODE),
        "selected_comsol": {"root": "frozen-root"}, "requested_version": "6.4",
        "selected_jdk": {"home": "frozen-jdk"},
    }
    plan["source_manifest_sha256"] = runner.sha256_value(plan["source_manifest"])
    monkeypatch.setattr(runner, "_python_identity", lambda: plan["python"])
    monkeypatch.setattr(runner, "_published_tool_schemas", lambda _root, _names=None: plan["published_tool_schemas"])
    monkeypatch.setattr(runner, "_logical_schemas", lambda _root, _operations=None: plan["logical_operation_schemas"])
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


def test_model_create_unknown_error_code_halts_without_cleanup_and_preserves_ids(tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, model_create_unknown_code_only=True)

    with pytest.raises(runner.RunnerError, match="model_create returned UNKNOWN"):
        asyncio.run(runner.run_metadata_protocol(
            runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: [],
        ))

    actions = [action for action, _ in fake.calls]
    assert actions.count("model_create") == 1
    assert "model.inspect" not in actions
    assert "fixture.register" not in actions and "fixture.execute" not in actions
    assert "session.disconnect" not in actions and "session.stop" not in actions
    assert "failure_cleanup.session_disconnect" not in actions
    model_action = state.value["actions"]["model_create"]
    assert model_action["request_id"] == plan["request_ids"]["model_create"]
    assert model_action["response"]["error_code"] == "EXECUTION_STATE_UNKNOWN"
    assert model_action["response"]["execution_state_unknown"] is True
    assert model_action["response"]["job_id"] == "model-create-unknown-job"
    assert state.value["status"] == "UNKNOWN"
    assert state.value["unknown_action"] == "model_create"
    assert state.value["recovery"]["request_id"] == plan["request_ids"]["model_create"]
    assert state.value["recovery"]["job_ids"] == ["model-create-unknown-job"]
    assert state.value["recovery"]["replay_permitted"] is False
    assert state.value["recovery"]["cleanup_permitted"] is False


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
    # Fixture and probe each use a trusted_code write ticket, so both advance
    # the ledger revision once even though the probe's Java body is read-only.
    assert binding["revision"] == 2
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


@pytest.mark.parametrize(("selected_version", "selected_build", "remote_version", "remote_build",
                          "remote_build_source", "canonical_version", "canonical_build_source"), [
    ("6.4.0.293", "293", "COMSOL Multiphysics 6.4 (开发版本: 293)", None,
     "NOT_REPORTED", "6.4.0", "remote_version_text"),
    ("6.4.0.293", "293", "COMSOL Multiphysics 6.4 (Build: 293)", None,
     "NOT_REPORTED", "6.4.0", "remote_version_text"),
    ("6.3.0.290", "290", "COMSOL Multiphysics 6.3 (Build: 290)", None,
     "NOT_REPORTED", "6.3.0", "remote_version_text"),
    ("6.4.0.293", "293", "6.4.0.293", "293",
     "remote-connect-reply", "6.4.0", "remote_explicit_build"),
], ids=["localized-64", "english-64", "english-63", "numeric-compat"])
def test_session_connect_version_identity_is_parsed_in_full_protocol(
        selected_version, selected_build, remote_version, remote_build,
        remote_build_source, canonical_version, canonical_build_source, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path, mode=runner.SOLVE_READBACK_MODE)
    plan["requested_version"] = selected_version[:3]
    plan["selected_comsol"]["version"] = selected_version
    plan["selected_comsol"]["build"] = selected_build
    plan["freeze_sha256"] = runner.sha256_value({
        key: value for key, value in plan.items() if key != "freeze_sha256"
    })
    state = _state(tmp_path, schema=runner.SOLVE_READBACK_SCHEMA)
    state.value["freeze_sha256"] = plan["freeze_sha256"]
    state.save()
    fake = _FakeStdioSession(plan, state, connect_identity={
        "remote_engine_version": remote_version,
        "remote_engine_build": remote_build,
        "remote_engine_build_source": remote_build_source,
    })

    report = asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
        clock=lambda: 100.0, preflight=lambda: []))
    identity = report["remote_engine_identity"]
    assert identity["normalized_version"] == canonical_version
    assert identity["normalized_build"] == selected_build
    assert identity["version_source"] == "remote_version_text"
    assert identity["build_source"] == canonical_build_source
    assert identity["remote_engine_version_raw"] == remote_version
    assert identity["remote_engine_build_raw"] == remote_build
    assert identity["remote_engine_build_source_raw"] == remote_build_source
    assert report["remote_engine_version"] == remote_version
    assert report["remote_engine_build"] == remote_build
    assert report["native_admission"] == report["physical_validation"] == "UNVERIFIED"
    assert report["status"] == "SOLVE_READBACK_CAPTURED_ONLY_NOT_ADMISSION"
    actions = [action for action, _ in fake.calls]
    assert actions.count("study.solve") == 1
    assert actions.index("session.connect") < actions.index("model_create")


@pytest.mark.parametrize(("remote_version", "remote_build", "remote_build_source"), [
    ("6.40.0.293", "293", "remote-connect-reply"),
    ("6.4.0.293", None, "NOT_REPORTED"),
    ("COMSOL Multiphysics 6.4 (Build: 293)", "292", "remote-connect-reply"),
    ("COMSOL Multiphysics 6.3 (Build: 293)", None, "NOT_REPORTED"),
    ("COMSOL Multiphysics 6.4 (Build: 294)", None, "NOT_REPORTED"),
    ("COMSOL Multiphysics 6.4 (Build: 293) trailing", None, "NOT_REPORTED"),
    ("6.4.0.293", "293", "NOT_REPORTED"),
], ids=["false-prefix", "missing-build", "conflicting-build", "wrong-version",
       "wrong-build", "trailing-data", "contradictory-source"])
def test_session_connect_rejects_ambiguous_or_mismatched_version_evidence(
        remote_version, remote_build, remote_build_source, tmp_path, monkeypatch):
    _patch_isolation(monkeypatch)
    plan = _plan(tmp_path)
    state = _state(tmp_path)
    fake = _FakeStdioSession(plan, state, connect_identity={
        "remote_engine_version": remote_version,
        "remote_engine_build": remote_build,
        "remote_engine_build_source": remote_build_source,
    })
    with pytest.raises(runner.RunnerError, match="COMSOL|build|version"):
        asyncio.run(runner.run_metadata_protocol(runner._MCPCalls(fake), plan, state,
            clock=lambda: 100.0, preflight=lambda: []))
    actions = [action for action, _ in fake.calls]
    assert actions.count("session.connect") == 1
    assert "model_create" not in actions
    assert "probe.execute" not in actions
    assert "study.solve" not in actions
    assert "session.disconnect" in actions and "session.stop" in actions
    assert state.value["status"] == "FAILED"


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
    with pytest.raises(runner.RunnerError, match="prepared mode/schema/budget identity is invalid"):
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
