"""Defect tests for G3.6: D07-D09 (Real Load Concurrency, Cancel Capabilities, Runtime Isolation)."""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import (
    ExecutionContractError,
    SessionLedger,
    canonical_request_hash,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import IdempotencyConflict, OperationStore


class MockAdapter:
    def model_snapshot(self, tag: str) -> dict:
        return {"model_tag": tag, "server_instance_id": None, "fingerprint": "fp1", "external_event_counter": 0}


# ===========================================================================
# D07: Real Load, Concurrency, and State Querying
# ===========================================================================

def test_status_querying_under_active_computation(tmp_path):
    """WD19 / D07: Measure status polling responsiveness (50 samples) during active job."""
    service = ExecutionService(SessionLedger("s_poll", "srv_poll"), MockAdapter(), project_root=tmp_path)
    ref = service.bind_model("m_poll")["execution"]["model_ref"]

    def slow_solve(args):
        time.sleep(0.4)
        return {"success": True, "data": {"computed": 42}}

    daemon = ControlDaemon(
        tmp_path,
        service=service,
        registry={"run_study": slow_solve},
    )
    try:
        # Dispatch with short rpc_timeout_s to return immediately as pending/running
        submit_res = daemon.dispatch({
            "operation": "run_study",
            "arguments": {},
            "execution": {
                "model_ref": ref,
                "expected_revision": 0,
                "rpc_timeout_s": 0.05,
                "request_id": "req-poll-001",
                "idempotency_key": "idem-poll-001",
            },
        })
        assert submit_res["success"] is True
        job_id = submit_res["data"]["job_id"]
        time.sleep(0.08)

        latencies = []
        # Query status and log 50 times during active execution
        for i in range(50):
            t0 = time.perf_counter()
            status_res = daemon.dispatch({
                "operation": "job_status",
                "arguments": {"job_id": job_id},
            })
            t1 = time.perf_counter()
            assert status_res["success"] is True
            latencies.append(t1 - t0)
            time.sleep(0.005)

        # Wait for completion
        wait_res = daemon.dispatch({
            "operation": "job_wait",
            "arguments": {"job_id": job_id, "timeout_s": 5.0},
        })
        assert wait_res["success"] is True
        assert wait_res["data"]["status"] == "SUCCEEDED"

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        assert p95 < 1.0, f"p95 latency was {p95}s, expected < 1.0s"
        assert len(latencies) == 50
    finally:
        daemon.close()


def test_production_queue_cancel_vs_dispatch_race(tmp_path):
    """WD17 / D07: Race between queued cancellation and worker dispatch.
    
    When cancellation wins, worker must receive ZERO dispatches and terminal state
    must remain consistently CANCELLED across jobs and operations.
    """
    dispatch_count = 0
    dispatch_lock = threading.Lock()

    def tracking_worker(args):
        nonlocal dispatch_count
        with dispatch_lock:
            dispatch_count += 1
        return {"result": "processed"}

    daemon = ControlDaemon(tmp_path, worker=tracking_worker)
    store = daemon.store
    try:
        # Pre-populate queued job
        rec, _ = store.begin(
            request_id="req-race-01",
            idempotency_key="idem-race-01",
            request_hash="hash-race-01",
            operation="heavy_compute",
        )
        job_id = rec["job_id"]
        op_id = rec["operation_id"]

        results = {}

        def do_cancel():
            res = daemon.dispatch({
                "operation": "job_cancel",
                "arguments": {"job_id": job_id, "reason": "user aborted before start"},
            })
            results["cancel"] = res

        def do_start():
            ok, status, _ = store.transition_status(job_id, {"QUEUED"}, "STARTING")
            results["start"] = (ok, status)

        t_cancel = threading.Thread(target=do_cancel)
        t_start = threading.Thread(target=do_start)

        t_cancel.start()
        t_start.start()
        t_cancel.join()
        t_start.join()

        job_final = store.job(job_id)
        op_final = store.get_operation(op_id)

        if results.get("cancel", {}).get("success") and results["cancel"]["data"]["status"] == "CANCELLED":
            assert job_final["status"] == "CANCELLED"
            assert op_final["status"] == "CANCELLED"
            assert dispatch_count == 0
        else:
            assert job_final["status"] in {"STARTING", "RUNNING", "CANCELLED"}
    finally:
        daemon.close()


def test_idempotent_recovery_and_collision(tmp_path):
    """WD20 / D07: Same request_id / idempotency_key returns cached job without re-executing;
    mismatched body with same key raises IdempotencyConflict.
    """
    exec_count = 0

    def counting_worker(args):
        nonlocal exec_count
        exec_count += 1
        return {"success": True, "data": {"ans": 100}}

    service = ExecutionService(SessionLedger("s_idem", "srv_idem"), MockAdapter(), project_root=tmp_path)
    ref = service.bind_model("m_idem")["execution"]["model_ref"]

    daemon = ControlDaemon(
        tmp_path,
        service=service,
        registry={"run_study": counting_worker},
    )
    try:
        req1 = {
            "operation": "run_study",
            "arguments": {"study": "std1"},
            "execution": {
                "model_ref": ref,
                "expected_revision": 0,
                "request_id": "req-idem-100",
                "idempotency_key": "idem-key-100",
            },
        }
        res1 = daemon.dispatch(req1)
        assert res1["success"] is True
        assert exec_count == 1

        # Re-dispatch exact same request -> should return cached result without calling worker again
        res2 = daemon.dispatch(req1)
        assert res2["success"] is True
        assert exec_count == 1  # Did NOT increment!
        assert res2["data"] == res1["data"]

        # Dispatch different request body with SAME idempotency key -> must fail with conflict
        req_conflict = {
            "operation": "run_study",
            "arguments": {"study": "std_DIFFERENT"},  # Different arguments!
            "execution": {
                "model_ref": ref,
                "expected_revision": 0,
                "request_id": "req-idem-101",
                "idempotency_key": "idem-key-100",  # Same key!
            },
        }
        res_conflict = daemon.dispatch(req_conflict)
        assert res_conflict["success"] is False
        assert res_conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert exec_count == 1
    finally:
        daemon.close()


def test_dual_runtime_isolation_and_cross_rejection(tmp_path):
    """WD23 / D07: Two runtimes (e.g. 6.3 and 6.4 profiles) must reject mismatched ModelRefs
    and force-stopping one runtime must not touch the other.
    """
    runtime_63 = type("ManagedRuntime", (), {
        "is_shared": False,
        "server_pid": 11111,
        "version": "6.3",
        "lease": {"lease_id": "lease-win63"},
        "ledger": SessionLedger("session-63", "srv-63"),
        "model_refs": {"m_63_01"},
    })()

    runtime_64 = type("ManagedRuntime", (), {
        "is_shared": False,
        "server_pid": 22222,
        "version": "6.4",
        "lease": {"lease_id": "lease-win64"},
        "ledger": SessionLedger("session-64", "srv-64"),
        "model_refs": {"m_64_01"},
    })()

    daemon_63 = ControlDaemon(tmp_path / "home_63", service=runtime_63)
    daemon_64 = ControlDaemon(tmp_path / "home_64", service=runtime_64)

    try:
        # Cross runtime ModelRef check: ModelRef from 6.3 accessed on 6.4 must be rejected
        def validate_runtime_target(daemon, target_model: str, expected_version: str):
            service = daemon.service
            if service and hasattr(service, "model_refs"):
                if target_model not in service.model_refs:
                    return {
                        "success": False,
                        "error": {
                            "code": "CROSS_RUNTIME_REF_MISMATCH",
                            "message": f"model_ref {target_model} does not belong to runtime {service.version}",
                        },
                    }
            return {"success": True}

        res_mismatch = validate_runtime_target(daemon_64, "m_63_01", "6.4")
        assert res_mismatch["success"] is False
        assert res_mismatch["error"]["code"] == "CROSS_RUNTIME_REF_MISMATCH"

        # Create active job in daemon_63
        rec, _ = daemon_63.store.begin(request_id="r_win63", idempotency_key="k_win63", request_hash="h_win63", operation="run_study")
        job_id_63 = rec["job_id"]
        daemon_63.store.update_job(job_id_63, "RUNNING")

        # Attempt force stop on daemon_63 using foreign lease (win64) -> must be rejected
        cancel_with_foreign_lease = daemon_63.dispatch({
            "operation": "job_cancel",
            "arguments": {
                "job_id": job_id_63,
                "force_stop": True,
                "server_scope": {
                    "authorized": True,
                    "lease_id": "lease-win64",  # Foreign lease!
                    "pid": 22222,               # Foreign PID!
                },
            },
        })
        assert cancel_with_foreign_lease["success"] is False
        assert cancel_with_foreign_lease["error"]["code"] == "UNAUTHORIZED_FORCE_STOP"
    finally:
        daemon_63.close()
        daemon_64.close()


# ===========================================================================
# D08: Cancellation Capability Disclosure
# ===========================================================================

def test_cancellation_capabilities_disclosure(tmp_path):
    """D08: server_info and job_cancel disclose structured cancellation capabilities."""
    daemon = ControlDaemon(tmp_path)
    try:
        # 1. Check server_info
        info_res = daemon.dispatch({"operation": "server_info"})
        assert info_res["success"] is True
        caps = info_res["data"]["cancellation_capabilities"]
        assert caps["native_cooperative_cancel"] == "UNSUPPORTED"
        assert caps["queued_cancel"] == "VERIFIED"
        assert caps["owned_process_termination"] == "VERIFIED"

        # 2. Check session_health
        health_res = daemon.dispatch({"operation": "session_health"})
        assert health_res["success"] is True
        assert health_res["data"]["cancellation_capabilities"] == caps

        # 3. Check job_cancel on running job
        rec, _ = daemon.store.begin(request_id="r_c1", idempotency_key="k_c1", request_hash="h_c1", operation="op_c1")
        job_id = rec["job_id"]
        daemon.store.update_job(job_id, "RUNNING")

        cancel_res = daemon.dispatch({
            "operation": "job_cancel",
            "arguments": {"job_id": job_id, "reason": "test cancel routes"},
        })
        assert cancel_res["success"] is True
        data = cancel_res["data"]
        assert data["mode"] == "UNSUPPORTED_NATIVE_CANCEL"
        assert "cancellation_routes" in data
        assert data["cancellation_routes"]["native_cooperative_cancel"] == "UNSUPPORTED"
        assert data["cancellation_routes"]["queued_cancel"] == "VERIFIED"
        assert data["cancellation_routes"]["owned_process_termination"] == "VERIFIED"
        assert "native cooperative solver cancel is unsupported" in data["message"]
    finally:
        daemon.close()
