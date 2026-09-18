"""Guardrails for the real recovery acceptance driver.

Live T027/T028/T035 evidence is intentionally not simulated in pytest.  These
tests keep its production transport and fault-target preconditions intact.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from comsol_mcp._operation_store import OperationStore


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phase2_recovery_mcp", ROOT / "tools" / "phase2_recovery_mcp.py")
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def test_driver_uses_stdio_client_sessions_and_never_imports_control_client():
    source = (ROOT / "tools" / "phase2_recovery_mcp.py").read_text(encoding="utf-8")
    assert "stdio_client" in source
    assert "ClientSession" in source
    assert "_control_client" not in source
    assert 'call("run_study"' in source
    assert "same_key_retry" in source
    assert 'call("model_inspect"' in source
    assert "control_plane_samples" in source
    assert "ExecutionDeadlineExceeded" in source


def test_control_home_matches_production_private_layout(tmp_path: Path):
    assert driver._control_home(tmp_path) == tmp_path.resolve() / "control-private"


def test_model_ref_accepts_saved_execution_envelope(tmp_path: Path):
    ref = {"schema_version": 1, "session_id": "s", "server_instance_id": "srv", "model_tag": "m", "generation": 2}
    path = tmp_path / "ref.json"
    path.write_text(__import__("json").dumps({"execution": {"model_ref": ref}}), encoding="utf-8")
    assert driver._model_ref(path) == ref


def test_model_ref_rejects_non_identity_input(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no model_ref"):
        driver._model_ref(path)


def test_execution_identity_requires_a_bound_ref_and_integer_revision():
    ref = {"session_id": "s", "model_tag": "m"}
    assert driver._execution_identity({"execution": {"model_ref": ref, "revision": 3}}) == (ref, 3)
    assert driver._execution_identity({"execution": {"model_ref": ref, "revision": True}}) == (ref, None)
    assert driver._execution_identity({"execution": {"model_ref": "m", "revision": 3}}) == (None, 3)


def test_evidence_redaction_removes_private_paths_and_tokens():
    value = driver._redact({
        "token": "secret", "argument": "/repo/.phase1-private/prefs",
        "private_credential_path_denied": True, "credential_content_not_logged": False,
        "authorization_attempt_count": 3,
    })
    assert value == {
        "token": "REDACTED", "argument": "REDACTED_PATH",
        "private_credential_path_denied": True, "credential_content_not_logged": False,
        "authorization_attempt_count": 3,
    }


def test_verified_pid_rejects_registered_comsol_pid_before_ps():
    with pytest.raises(RuntimeError, match="protected"):
        driver._verified_pid({"pid": 84749}, "daemon", Path("/private/control"), 84749)


def test_job_row_reads_result_from_operations_join_in_real_sqlite_fixture(tmp_path: Path):
    control_home = tmp_path / "control-private"
    store = OperationStore(control_home / "operations.sqlite3")
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="run_study")
    store.update_job(record["job_id"], "RUNNING", {"phase": "solve"})
    store.finish(record["operation_id"], status="SUCCEEDED", result={"success": True, "data": {"value": 2}})
    store.close()

    row = driver._job_row(control_home, record["job_id"])
    assert row["job_id"] == record["job_id"]
    assert row["operation_id"] == record["operation_id"]
    assert json.loads(row["result"]) == {"success": True, "data": {"value": 2}}
    assert row["operation_count"] == 1


def test_worker_request_requires_pending_solver_run_not_preflight_or_completed_call():
    observed = {
        "status": {"data": {"status": "RUNNING"}},
        "log": {"data": {"events": [
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "snapshot", "kind": "model_snapshot",
                "metadata": {"method": "snapshot"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "label", "kind": "call",
                "metadata": {"method": "label"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "run-complete", "kind": "call",
                "metadata": {"method": "run"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "observed", "request_id": "run-complete", "kind": "call",
                "metadata": {"method": "run"},
            }},
        ]}},
    }
    assert driver._status(observed["status"]) == "RUNNING"
    assert driver._worker_request_submitted(observed) is False
    observed["log"]["data"]["events"].append({"event": "worker_request", "metadata": {
        "phase": "submitted", "request_id": "run-pending", "kind": "call", "metadata": {"method": "run"},
    }})
    assert driver._worker_request_submitted(observed) is True
    observed["log"]["data"]["events"].append({"event": "worker_request", "metadata": {
        "phase": "observed", "request_id": "run-pending", "kind": "call", "metadata": {"method": "run"},
    }})
    assert driver._worker_request_submitted(observed) is False


def test_p95_uses_the_95th_percentile_rank_and_empty_samples_are_unknown():
    assert driver._p95([]) is None
    assert driver._p95([0.8] * 19 + [1.2]) == 0.8
    assert driver._p95([0.4] * 20) == 0.4


def test_inspect_submission_detection_only_matches_worker_submit_events():
    observed = {"log": {"data": {"events": [
        {"event": "worker_request", "metadata": {"phase": "observed"}},
        {"event": "other", "metadata": {"phase": "submitted"}},
    ]}}}
    assert driver._has_worker_submission(observed) is False
    observed["log"]["data"]["events"].append(
        {"event": "worker_request", "metadata": {"phase": "submitted"}})
    assert driver._has_worker_submission(observed) is True
