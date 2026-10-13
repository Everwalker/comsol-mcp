"""Registry job controls stay responsive while the serialized engine is busy."""
from __future__ import annotations

import threading
import time

import pytest

from comsol_mcp import _g2_registry
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}


def _busy_daemon(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    service = ExecutionService(SessionLedger("session", "server"), Adapter(), project_root=tmp_path)
    model_ref = service.bind_model("m")["execution"]["model_ref"]

    def blocking_solve(args):
        calls.append(args)
        entered.set()
        release.wait(3)
        return {"success": True, "data": {"done": True}}

    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": blocking_solve})
    original_submit = daemon.session_scheduler.submit
    submitted = []

    def count_submit(context, fn, *args, **kwargs):
        submitted.append(args[1] if len(args) > 1 else None)
        return original_submit(context, fn, *args, **kwargs)

    daemon.session_scheduler.submit = count_submit
    pending = daemon.dispatch({
        "operation": "set_parameters",
        "arguments": {"x": 1},
        "execution": {"model_ref": model_ref, "expected_revision": 0, "idempotency_key": "one-solve", "rpc_timeout_s": 0.01},
    })
    assert pending["success"] is True
    assert entered.wait(1)
    return daemon, release, calls, submitted, pending["data"]["job_id"], model_ref


def _nested(outer_name, action, action_args, *, model_ref=None):
    execution = {"rpc_timeout_s": 0.1}
    if model_ref is not None:
        # A normal client may bind a model at the outer request level. Cached
        # job controls must not merge that identity into the nested action.
        execution["model_ref"] = model_ref
        execution["expected_revision"] = 123
    return {
        "operation": outer_name,
        "arguments": {"operation_id": action, "arguments": action_args},
        "execution": execution,
    }


def test_job_status_log_cancel_are_responsive_through_all_three_entrypoints(tmp_path):
    daemon, release, calls, submitted, job_id, model_ref = _busy_daemon(tmp_path)
    try:
        control_calls = [
            ("job.status", {"job_id": job_id}),
            ("job.log", {"job_id": job_id, "cursor": "0", "limit": 20}),
            ("job.cancel", {"job_id": job_id, "reason": "test cancellation intent"}),
        ]
        observed = []
        for action, action_args in control_calls:
            direct_started = time.monotonic()
            direct = daemon.dispatch({"operation": action, "arguments": action_args, "execution": {}})
            observed.append(direct)
            assert time.monotonic() - direct_started < 0.5

            operation_started = time.monotonic()
            operation_call = daemon.dispatch(_nested("operation_call", action, action_args, model_ref=model_ref))
            observed.append(operation_call)
            assert time.monotonic() - operation_started < 0.5

            registry_started = time.monotonic()
            registry_call = daemon.dispatch(_nested("registry_call", action, action_args, model_ref=model_ref))
            observed.append(registry_call)
            assert time.monotonic() - registry_started < 0.5

        assert all(result["success"] for result in observed)
        assert observed[0]["data"]["job_id"] == job_id
        assert observed[1]["data"]["job_id"] == job_id
        assert observed[2]["data"]["job_id"] == job_id
        assert all(result["data"].get("job_id", job_id) == job_id for result in observed)
        assert daemon.store.job(job_id)["status"] == "RUNNING"
        assert daemon.store.job(job_id)["metadata"].get("cancel_requested") is True
        assert len(daemon.store.list_jobs(offset=0, limit=100)) == 1
        assert len(submitted) == 1
        assert calls == [{"x": 1}]
        assert _g2_registry.is_implemented("job.status")
        assert _g2_registry.is_implemented("job.cancel")
        assert _g2_registry.is_implemented("job.cleanup")
        assert _g2_registry.is_implemented("job.resume")
        described = _g2_registry.operation_describe("job.list")
        assert described["executable"] is True
        assert described["implementation_status"] == "SUPPORTED_UNVERIFIED"
        assert described["runtime_dispatch_contract"]["engine_queue"] == "bypassed"
        assert described["runtime_dispatch_contract"]["cursor"].startswith("Base-10")
        registry_described = _g2_registry.registry_describe("job.list")
        assert registry_described["runtime_dispatch_contract"]["cursor"] == described["runtime_dispatch_contract"]["cursor"]
        assert described["input_schema"]["properties"]["filter"]["additionalProperties"] is False
        assert described["input_schema"]["allOf"] == [{"not": {"required": ["cursor", "offset"]}}]

        # A caller that bypasses ControlDaemon cannot accidentally send these
        # cached control actions into model-scoped ManagedBackend dispatch.
        with pytest.raises(ExecutionContractError, match="ControlDaemon") as exc_info:
            daemon.backend.invoke("job.status", {"job_id": job_id}, {}, "operation", lambda _event: None)
        assert exc_info.value.code == "CONTROL_PLANE_ROUTE_REQUIRED"
    finally:
        release.set()
        daemon.close()


def test_registry_job_schema_and_identity_errors_do_not_queue_or_mutate(tmp_path):
    daemon, release, calls, submitted, job_id, model_ref = _busy_daemon(tmp_path)
    try:
        before_events = daemon.store.events(job_id, offset=0, limit=100)
        bad_calls = [
            _nested("registry_call", "job.status", {"job_id": job_id, "unexpected": True}),
            _nested("operation_call", "job.status", {"job_id": job_id, "model_ref": model_ref}),
            _nested("registry_call", "job.status", {"job_id": ""}),
            _nested("registry_call", "job.resume", {"job_id": job_id}),
            _nested("registry_call", "job.list", {"filter": {"not_a_filter": "x"}}),
            {
                "operation": "registry_call",
                "arguments": {"operation_id": "job.status", "arguments": {"job_id": job_id}, "ignored": 1},
                "execution": {},
            },
        ]
        for request in bad_calls:
            result = daemon.dispatch(request)
            assert result["success"] is False
            assert result["error"]["code"] in {
                "INVALID_REQUEST", "UNSUPPORTED_OPERATION", "RESUME_SOURCE_NOT_FAILED",
            }, (request, result)

        invalid_cursor_requests = [
            {"operation": "job.list", "arguments": {"cursor": "../../1"}, "execution": {}},
            _nested("operation_call", "job.list", {"cursor": "../../1"}),
            _nested("registry_call", "job.list", {"cursor": "../../1"}),
        ]
        for request in invalid_cursor_requests:
            invalid_cursor = daemon.dispatch(request)
            assert invalid_cursor["success"] is False
            assert invalid_cursor["error"]["code"] == "INVALID_REQUEST", (request, invalid_cursor)

        unauthorized = daemon.dispatch(_nested("registry_call", "job.cancel", {
            "job_id": job_id,
            "force_stop": True,
        }))
        assert unauthorized["success"] is False
        assert unauthorized["error"]["code"] == "UNAUTHORIZED_FORCE_STOP"
        assert daemon.store.job(job_id)["status"] == "RUNNING"
        assert daemon.store.job(job_id)["metadata"].get("cancel_requested") is None
        assert daemon.store.events(job_id, offset=0, limit=100) == before_events
        assert len(daemon.store.list_jobs(offset=0, limit=100)) == 1
        assert len(submitted) == 1
        assert calls == [{"x": 1}]
    finally:
        release.set()
        daemon.close()


def test_registry_job_list_decimal_cursor_and_filter_mapping(tmp_path):
    daemon = ControlDaemon(tmp_path)
    try:
        rows = []
        for index in range(2):
            record, _ = daemon.store.begin(
                request_id=f"r{index}", idempotency_key=f"k{index}", request_hash=f"h{index}",
                operation="test.operation", metadata={"project_id": "project-a"},
            )
            rows.append(record["job_id"])

        first = daemon.dispatch(_nested("registry_call", "job.list", {"limit": 1, "filter": {"project_id": "project-a"}}))
        assert first["success"] is True
        assert first["data"]["next_cursor"] == "1"
        second = daemon.dispatch(_nested("operation_call", "job.list", {
            "limit": 1, "cursor": first["data"]["next_cursor"], "project_id": "project-a",
        }))
        direct_second = daemon.dispatch({
            "operation": "job.list",
            "arguments": {"limit": 1, "cursor": first["data"]["next_cursor"], "project_id": "project-a"},
            "execution": {},
        })
        assert second["success"] is True
        assert direct_second["success"] is True
        assert second["data"]["next_cursor"] is None
        assert direct_second["data"]["next_cursor"] is None
        assert first["data"]["jobs"][0]["job_id"] != second["data"]["jobs"][0]["job_id"]
        assert direct_second["data"]["jobs"][0]["job_id"] == second["data"]["jobs"][0]["job_id"]
        assert {first["data"]["jobs"][0]["job_id"], second["data"]["jobs"][0]["job_id"]} == set(rows)

        # Direct plus filter duplication is rejected instead of silently
        # choosing one of the conflicting project/status selectors.
        duplicate_filter = daemon.dispatch(_nested("registry_call", "job.list", {
            "status": "QUEUED", "filter": {"status": "QUEUED"},
        }))
        assert duplicate_filter["success"] is False
        assert duplicate_filter["error"]["code"] == "INVALID_REQUEST"
        assert len(daemon.store.list_jobs(offset=0, limit=100)) == 2
    finally:
        daemon.close()
