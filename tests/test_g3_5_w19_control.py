"""Unit tests for W19 job control: list, wait, cancel, and concurrency safeguards."""
from __future__ import annotations

import time
import pytest
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore


class MockAdapter:
    def model_snapshot(self, tag: str) -> dict:
        return {"model_tag": tag, "server_instance_id": "srv1", "fingerprint": "fp1", "external_event_counter": 0}


def test_operation_store_list_jobs_and_filtering(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    try:
        # Create jobs across different statuses and projects
        rec1, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="run_study", metadata={"project_id": "proj-A"})
        rec2, _ = store.begin(request_id="r2", idempotency_key="k2", request_hash="h2", operation="plot_render", metadata={"project_id": "proj-B"})
        rec3, _ = store.begin(request_id="r3", idempotency_key="k3", request_hash="h3", operation="model_save", metadata={"project_id": "proj-A"})
        
        store.update_job(rec1["job_id"], "RUNNING")
        store.finish(rec2["operation_id"], status="SUCCEEDED", result={"success": True, "data": {}})
        store.cancel_queued(rec3["job_id"], reason="test cancel")

        # Test list_jobs all
        all_jobs = store.list_jobs(offset=0, limit=10)
        assert len(all_jobs) == 3
        assert all_jobs.total == 3
        assert all_jobs.has_more is False

        # Test status filtering
        running_jobs = store.list_jobs(status="RUNNING")
        assert len(running_jobs) == 1
        assert running_jobs[0]["job_id"] == rec1["job_id"]

        succeeded_jobs = store.list_jobs(status="SUCCEEDED")
        assert len(succeeded_jobs) == 1
        assert succeeded_jobs[0]["job_id"] == rec2["job_id"]

        cancelled_jobs = store.list_jobs(status="CANCELLED")
        assert len(cancelled_jobs) == 1
        assert cancelled_jobs[0]["job_id"] == rec3["job_id"]

        # Test project_id filtering
        proj_a_jobs = store.list_jobs(project_id="proj-A")
        assert len(proj_a_jobs) == 2
        proj_b_jobs = store.list_jobs(project_id="proj-B")
        assert len(proj_b_jobs) == 1
        assert proj_b_jobs[0]["job_id"] == rec2["job_id"]

        # Test pagination
        page1 = store.list_jobs(offset=0, limit=2)
        assert len(page1) == 2
        assert page1.has_more is True
        page2 = store.list_jobs(offset=2, limit=2)
        assert len(page2) == 1
        assert page2.has_more is False
    finally:
        store.close()


def test_operation_store_cancel_queued_atomic_and_idempotent(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    try:
        rec, _ = store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="run_study")
        job_id = rec["job_id"]
        assert store.job(job_id)["status"] == "QUEUED"

        # Cancel queued job
        success, code, job = store.cancel_queued(job_id, reason="user abort")
        assert success is True
        assert code == "CANCELLED"
        assert job["status"] == "CANCELLED"
        assert job["result"]["error"]["code"] == "OPERATION_CANCELLED"
        assert job["result"]["data"]["engine_dispatched"] is False

        # Cancel again (idempotent)
        success2, code2, job2 = store.cancel_queued(job_id, reason="second try")
        assert success2 is True
        assert code2 == "ALREADY_CANCELLED"
        assert job2["status"] == "CANCELLED"

        # Late update to RUNNING is rejected
        store.update_job(job_id, "RUNNING")
        assert store.job(job_id)["status"] == "CANCELLED"
    finally:
        store.close()


def test_daemon_queued_cancel_prevents_engine_dispatch(tmp_path):
    dispatched = []

    def long_study_worker(args):
        time.sleep(0.3)
        return {"success": True, "data": {"step": 1}}

    def queued_worker(args):
        dispatched.append("queued_executed")
        return {"success": True, "data": {"step": 2}}

    service = ExecutionService(SessionLedger("s1", "srv1"), MockAdapter(), project_root=tmp_path)
    ref = service.bind_model("m1")["execution"]["model_ref"]

    daemon = ControlDaemon(
        tmp_path,
        service=service,
        registry={"run_study": long_study_worker, "set_parameters": queued_worker},
    )
    try:
        # Submit first long-running job asynchronously
        req1 = {
            "operation": "run_study",
            "arguments": {},
            "execution": {"model_ref": ref, "expected_revision": 0, "rpc_timeout_s": 0.05, "idempotency_key": "k1"},
        }
        res1 = daemon.dispatch(req1)
        assert res1["success"] is True
        job1_id = res1["data"]["job_id"]
        assert daemon.store.job(job1_id)["status"] in {"QUEUED", "RUNNING"}

        # Submit second job that gets queued behind job1
        req2 = {
            "operation": "set_parameters",
            "arguments": {"a": 1},
            "execution": {"model_ref": ref, "expected_revision": 0, "rpc_timeout_s": 0.05, "idempotency_key": "k2"},
        }
        res2 = daemon.dispatch(req2)
        job2_id = res2["data"]["job_id"]

        # Cancel job2 while it is still queued!
        cancel_res = daemon.dispatch({"operation": "job_cancel", "arguments": {"job_id": job2_id, "reason": "skip"}})
        assert cancel_res["success"] is True
        assert cancel_res["data"]["cancel_accepted"] is True
        assert cancel_res["data"]["engine_dispatched"] is False

        # Wait for queue to drain
        time.sleep(0.5)

        # Confirm job2 was NEVER dispatched to engine
        assert "queued_executed" not in dispatched
        assert daemon.store.job(job2_id)["status"] == "CANCELLED"
    finally:
        daemon.close()


def test_daemon_running_cancel_unsupported_native(tmp_path):
    service = ExecutionService(SessionLedger("s1", "srv1"), MockAdapter(), project_root=tmp_path)
    ref = service.bind_model("m1")["execution"]["model_ref"]

    def hanging_solve(args):
        time.sleep(0.4)
        return {"success": True, "data": {}}

    daemon = ControlDaemon(
        tmp_path,
        service=service,
        registry={"run_study": hanging_solve},
    )
    try:
        res = daemon.dispatch({
            "operation": "run_study",
            "arguments": {},
            "execution": {"model_ref": ref, "expected_revision": 0, "rpc_timeout_s": 0.05, "idempotency_key": "hk"},
        })
        job_id = res["data"]["job_id"]
        time.sleep(0.08)  # let it enter RUNNING state
        assert daemon.store.job(job_id)["status"] == "RUNNING"

        # Request cancellation for running job without force_stop
        cancel_res = daemon.dispatch({"operation": "job_cancel", "arguments": {"job_id": job_id, "reason": "cancel running"}})
        assert cancel_res["success"] is True
        assert cancel_res["data"]["status"] == "RUNNING"
        assert cancel_res["data"]["cancel_accepted"] is True
        assert cancel_res["data"]["engine_stopped"] is False
        assert cancel_res["data"]["mode"] == "UNSUPPORTED_NATIVE_CANCEL"

        # Force-stop on shared server must be rejected
        force_res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "force_stop": True, "server_scope": {"authorized": True}},
        })
        assert force_res["success"] is False
        assert force_res["error"]["code"] == "CANNOT_TERMINATE_SHARED_SERVER"

        time.sleep(0.4)
    finally:
        daemon.close()


def test_daemon_job_wait_and_alias_dispatch(tmp_path):
    daemon = ControlDaemon(tmp_path)
    try:
        record, _ = daemon.store.begin(request_id="r1", idempotency_key="k1", request_hash="h1", operation="model_save")
        job_id = record["job_id"]

        # Wait with expired timeout
        wait_res = daemon.dispatch({
            "operation": "job.wait",
            "arguments": {"job_id": job_id, "timeout_s": 0.05, "poll_interval_s": 0.01},
        })
        assert wait_res["success"] is True
        assert wait_res["data"].get("wait_expired") is True

        # Finish job
        daemon.store.finish(record["operation_id"], status="SUCCEEDED", result={"success": True, "data": {"saved": True}})

        # Wait should now return immediately with SUCCEEDED
        wait_res2 = daemon.dispatch({
            "operation": "job_wait",
            "arguments": {"job_id": job_id, "timeout_s": 1.0},
        })
        assert wait_res2["success"] is True
        assert wait_res2["data"]["status"] == "SUCCEEDED"
        assert wait_res2["data"].get("wait_expired") is not True

        # Test job.list alias
        list_res = daemon.dispatch({"operation": "job.list", "arguments": {"limit": 10}})
        assert list_res["success"] is True
        assert len(list_res["data"]["jobs"]) == 1
    finally:
        daemon.close()
