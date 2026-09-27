"""Conservative cached job-metadata compaction; all cases use a temporary DB."""
from __future__ import annotations

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}


def _cleanup_request(operation, job_ids, *, project_id="project-a", policy=None):
    payload = {"job_ids": job_ids}
    if policy is not None:
        payload["policy"] = policy
    return {
        "operation": operation,
        "arguments": {"operation_id": "job.cleanup", "arguments": payload},
        "execution": {"project_id": project_id},
    }


def test_cleanup_compacts_only_selected_terminal_job_view_and_preserves_audit_and_results(tmp_path):
    callback_calls = []
    service = ExecutionService(SessionLedger("session", "server"), Adapter(), project_root=tmp_path)
    model_ref = service.bind_model("m")["execution"]["model_ref"]

    def fail_once(args):
        callback_calls.append(dict(args))
        return {"success": False, "data": {"artifact_id": "official-output"},
                "error": {"code": "SYNTHETIC_FAILURE", "message": "retained failure evidence", "safe_retry": False}}

    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": fail_once})
    request = {
        "operation": "set_parameters",
        "arguments": {"x": 1},
        "execution": {"model_ref": model_ref, "expected_revision": 0, "idempotency_key": "same-operation", "request_id": "request-1", "project_id": "project-a"},
    }
    try:
        failed = daemon.dispatch(request)
        job_id = failed["execution"]["job_id"]
        assert failed["success"] is False
        assert daemon.store.job(job_id)["status"] == "FAILED"
        daemon.store.add_event(job_id, "OriginalFailureEvidence", {"detail": "preserve this audit record"})
        daemon.store.persist_artifact("official-output", {"sha256": "artifact-hash", "path": "results/final.mph"})
        daemon.store.persist_checkpoint("checkpoint-output", {"sha256": "checkpoint-hash", "path": "checkpoints/model.mph"})

        cleanup = daemon.dispatch(_cleanup_request("registry_call", [job_id], policy={"metadata_only": True}))
        assert cleanup["success"] is True
        assert cleanup["data"]["cleaned_job_ids"] == [job_id]
        assert cleanup["data"]["cleaned_count"] == 1

        job = daemon.store.job(job_id)
        assert job["status"] == "FAILED"
        assert job["metadata"]["project_id"] == "project-a"
        assert job["metadata"]["cleanup"]["metadata_compacted"] is True
        assert job["result"] == failed
        events = daemon.store.events(job_id, offset=0, limit=100)
        assert any(row["event"] == "OriginalFailureEvidence" for row in events)
        assert any(row["event"] == "JobCacheCompacted" for row in events)
        assert daemon.store.get_metadata("artifacts", "official-output")["sha256"] == "artifact-hash"
        assert daemon.store.get_metadata("checkpoints", "checkpoint-output")["sha256"] == "checkpoint-hash"

        # Exact idempotent retry returns the original failure without invoking
        # the backend a second time, even after job-view metadata compaction.
        retried = daemon.dispatch(request)
        assert retried == failed
        assert callback_calls == [{"x": 1}]
        assert len(daemon.store.list_jobs(offset=0, limit=100)) == 1

        # Cleanup is itself idempotent; it does not add a second audit event.
        event_count = len(events)
        again = daemon.dispatch(_cleanup_request("operation_call", [job_id]))
        assert again["success"] is True
        assert again["data"]["cleaned_job_ids"] == []
        assert again["data"]["cleaned_count"] == 0
        assert len(daemon.store.events(job_id, offset=0, limit=100)) == event_count
        direct_again = daemon.dispatch({
            "operation": "job.cleanup", "arguments": {"job_ids": [job_id]},
            "execution": {"project_id": "project-a"},
        })
        assert direct_again["success"] is True
        assert direct_again["data"]["cleaned_count"] == 0
    finally:
        daemon.close()


def test_cleanup_refuses_live_or_dependent_jobs_atomically(tmp_path):
    daemon = ControlDaemon(tmp_path)
    try:
        completed, _ = daemon.store.begin(
            request_id="complete-r", idempotency_key="complete-k", request_hash="complete-h",
            operation="test.complete", metadata={"arguments": {"project_id": "project-a"}},
        )
        live, _ = daemon.store.begin(
            request_id="live-r", idempotency_key="live-k", request_hash="live-h",
            operation="test.live", metadata={"arguments": {"project_id": "project-a"}},
        )
        daemon.store.finish(completed["operation_id"], status="SUCCEEDED", result={"success": True, "data": {}})
        daemon.store.update_job(live["job_id"], "RUNNING")
        daemon.store.add_event(completed["job_id"], "OriginalAudit", {"keep": True})
        before_metadata = daemon.store.job(completed["job_id"])["metadata"]
        before_events = daemon.store.events(completed["job_id"], offset=0, limit=100)

        live_result = daemon.dispatch(_cleanup_request("registry_call", [completed["job_id"], live["job_id"]]))
        assert live_result["success"] is False
        assert live_result["error"]["code"] == "JOB_NOT_TERMINAL"
        assert daemon.store.job(completed["job_id"])["metadata"] == before_metadata
        assert daemon.store.events(completed["job_id"], offset=0, limit=100) == before_events

        # A known dependency reference in another operation blocks compaction;
        # the audit/event history and cache view remain intact.
        daemon.store.update_job(live["job_id"], "FAILED")
        daemon.store.db.execute(
            "UPDATE operations SET metadata=? WHERE operation_id=?",
            ('{"arguments":{"depends_on_job_ids":["' + completed["job_id"] + '"]}}', live["operation_id"]),
        )
        dependency_result = daemon.dispatch(_cleanup_request("registry_call", [completed["job_id"]]))
        assert dependency_result["success"] is False
        assert dependency_result["error"]["code"] == "JOB_HAS_DEPENDENCIES"
        assert daemon.store.job(completed["job_id"])["metadata"] == before_metadata
        assert daemon.store.events(completed["job_id"], offset=0, limit=100) == before_events

        invalid_policy = daemon.dispatch(_cleanup_request("registry_call", [completed["job_id"]], policy={"metadata_only": False}))
        assert invalid_policy["success"] is False
        assert invalid_policy["error"]["code"] == "INVALID_REQUEST"
        unscoped = daemon.dispatch(_cleanup_request("registry_call", [completed["job_id"]], project_id="other-project"))
        assert unscoped["success"] is False
        assert unscoped["error"]["code"] == "PROJECT_SCOPE_MISMATCH"
        assert daemon.store.job(completed["job_id"])["metadata"] == before_metadata
        assert daemon.store.events(completed["job_id"], offset=0, limit=100) == before_events
    finally:
        daemon.close()


def test_cleanup_requires_explicit_job_ids_and_advertises_preservation_contract(tmp_path):
    daemon = ControlDaemon(tmp_path)
    try:
        missing_ids = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "job.cleanup", "arguments": {}},
            "execution": {},
        })
        assert missing_ids["success"] is False
        assert missing_ids["error"]["code"] == "INVALID_REQUEST"

        desc = daemon.dispatch({"operation": "operation_describe", "arguments": {"operation_id": "job.cleanup"}, "execution": {}})
        assert desc["success"] is True
        contract = desc["data"]["runtime_dispatch_contract"]
        assert contract["engine_queue"] == "bypassed"
        assert "explicit job_ids" in contract["scope"]
        assert "job event audit" in contract["preserved"]
        assert desc["data"]["input_schema"]["required"] == ["job_ids"]
        assert desc["data"]["input_schema"]["properties"]["policy"]["additionalProperties"] is False
        assert desc["data"]["data_schema"]["required"] == ["cleaned_job_ids", "cleaned_count"]
    finally:
        daemon.close()
