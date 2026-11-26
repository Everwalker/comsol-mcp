from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import comsol_mcp._control_daemon as control_daemon_module
from comsol_mcp._control_daemon import (
    ControlDaemon,
    _JOB_STATUS_WAIT_INLINE_RESULT_MAX_BYTES,
)
from comsol_mcp._project_authority import ProjectAuthority


def _create_project(daemon: ControlDaemon, label: str) -> dict:
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": label,
            "policy": {"permissions": ["inspect", "project_write"]},
        },
        "execution": {
            "request_id": f"create-{label}",
            "idempotency_key": f"create-{label}",
        },
    })
    assert result["success"] is True, result
    return result["data"]["project"]


@pytest.fixture
def daemon_projects(tmp_path: Path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(
        tmp_path / "control",
        project_root=project_root,
        registry={},
    )
    try:
        assert isinstance(daemon.project_authority, ProjectAuthority)
        project_a = _create_project(daemon, "project-a")
        project_b = _create_project(daemon, "project-b")
        database_path = Path(daemon.store.path)
        assert database_path.is_file()
        assert daemon.store.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        yield daemon, project_a["project_id"], project_b["project_id"], database_path
    finally:
        daemon.close()


def _begin_job(daemon: ControlDaemon, project_id: str, label: str) -> dict:
    record, reused = daemon.store.begin(
        request_id=f"request-{label}",
        idempotency_key=f"idempotency-{label}",
        request_hash=f"hash-{label}",
        operation="synthetic.t35_status_wait",
        metadata={"project_id": project_id, "execution": {"project_id": project_id}},
    )
    assert reused is False
    return record


def _finish_job(daemon: ControlDaemon, record: dict, result: dict) -> None:
    accepted, status = daemon.store.finish(
        record["operation_id"], status="SUCCEEDED", result=result,
    )
    assert accepted is True
    assert status == "SUCCEEDED"


def _call_job(
    daemon: ControlDaemon,
    action: str,
    job_id: str,
    project_id: str,
    **arguments,
) -> dict:
    return daemon.dispatch({
        "operation": "operation_call",
        "arguments": {
            "operation_id": action,
            "arguments": {"job_id": job_id, **arguments},
        },
        "execution": {"project_id": project_id},
    })


def test_reconcile_requires_matching_project_and_retains_unknown(daemon_projects) -> None:
    daemon, project_a, project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "reconcile-scope")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN", {"private_marker": "project-a-only"})
    for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "synthetic-old-request", "phase": phase, "status": status,
        })

    denied = _call_job(daemon, "job.reconcile", job_id, project_b)
    assert denied["success"] is False
    assert denied["error"]["code"] == "NODE_NOT_FOUND"
    assert "project-a-only" not in json.dumps(denied)
    assert daemon.store.job(job_id)["status"] == "UNKNOWN"

    allowed = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert allowed["success"] is True, allowed
    assert allowed["data"]["status"] == "UNKNOWN"
    assert allowed["data"]["metadata"]["reconciled_quiescent"] is True
    assert allowed["data"]["metadata"]["replay_performed"] is False


def test_reconcile_streams_logs_without_collecting_unrelated_events(daemon_projects, monkeypatch) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "reconcile-many-logs")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN")
    for _ in range(20):
        daemon.store.add_event(job_id, "solver_progress", {"message": "x" * 16384})
    for _ in range(5):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "old-request", "phase": "observed", "status": "SUCCEEDED",
            "diagnostic": "x" * (1024 * 1024),
        })
    for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "old-request", "phase": phase, "status": status,
        })

    def no_bulk_event_read(*_args, **_kwargs):
        raise AssertionError("reconciliation must not collect all event pages")

    monkeypatch.setattr(daemon.store, "events", no_bulk_event_read)
    result = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert result["success"] is True, result
    assert result["data"]["status"] == "UNKNOWN"
    assert result["data"]["metadata"]["reconciled_quiescent"] is True


def test_reconcile_refuses_oversized_worker_evidence_without_partial_result(daemon_projects) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "reconcile-large-evidence")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN")
    request_ids = [str(index) + "x" * (1024 * 1024) for index in range(5)]
    for request_id in request_ids:
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": request_id, "phase": "observed", "status": "SUCCEEDED",
        })
    for request_id in request_ids:
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": request_id, "phase": "submitted",
        })

    result = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    current = daemon.store.job(job_id)
    assert current["status"] == "UNKNOWN"
    assert "reconciled_quiescent" not in current["metadata"]


def test_reconcile_refuses_incomplete_event_snapshot(daemon_projects) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "reconcile-corrupt-event")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN")
    for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "old-request", "phase": phase, "status": status,
        })
    daemon.store.db.execute(
        "UPDATE job_events SET metadata=? WHERE job_id=? AND id=(SELECT MAX(id) FROM job_events WHERE job_id=?)",
        ("{", job_id, job_id),
    )
    daemon.store.db.commit()

    result = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert daemon.store.job(job_id)["status"] == "UNKNOWN"
    assert "reconciled_quiescent" not in daemon.store.job(job_id)["metadata"]


@pytest.mark.parametrize("status", ["UNKNOWN", "SUCCEEDED"])
def test_reconcile_omits_oversized_result_without_decoding(daemon_projects, monkeypatch, status) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, f"reconcile-large-result-{status}")
    job_id = record["job_id"]
    stored = {"success": True, "payload": "x" * (1024 * 1024)}
    accepted, actual_status = daemon.store.finish(record["operation_id"], status=status, result=stored)
    assert accepted and actual_status == status
    if status == "UNKNOWN":
        for phase, worker_status in (("submitted", None), ("observed", "SUCCEEDED")):
            daemon.store.add_event(job_id, "worker_request", {
                "request_id": "old-request", "phase": phase, "status": worker_status,
            })

    def no_full_result_decode(*_args, **_kwargs):
        raise AssertionError("reconcile decoded an oversized result")

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(daemon.store, "_job", no_full_result_decode)
        result = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert result["success"] is True, result
    assert result["data"]["status"] == status
    assert result["data"]["result_omitted"] is True
    assert result["data"]["result_available_via"] == "job.result.read"
    assert result["data"]["result_size_bytes"] > _JOB_STATUS_WAIT_INLINE_RESULT_MAX_BYTES
    assert "payload" not in json.dumps(result)


@pytest.mark.parametrize("change", ["binding", "event"])
def test_reconcile_refuses_drift_after_snapshot(daemon_projects, monkeypatch, change) -> None:
    daemon, project_a, project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, f"reconcile-drift-{change}")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN")
    for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "synthetic-old-request", "phase": phase, "status": status,
        })
    original = daemon.store.update_reconciliation_scoped

    def change_before_write(*args, **kwargs):
        if change == "event":
            daemon.store.add_event(job_id, "worker_request", {
                "request_id": "synthetic-new-request", "phase": "submitted", "status": None,
            })
        else:
            metadata = json.dumps({"project_id": project_b, "execution": {"project_id": project_b}})
            daemon.store.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?", (metadata, job_id))
            daemon.store.db.execute("UPDATE operations SET metadata=? WHERE operation_id=?", (
                metadata, record["operation_id"],
            ))
            daemon.store.db.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon.store, "update_reconciliation_scoped", change_before_write)
    response = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert response["success"] is False
    assert response["error"]["code"] == (
        "NODE_NOT_FOUND" if change == "binding" else "EXECUTION_STATE_UNKNOWN"
    )
    current = daemon.store.job(job_id)
    assert current["status"] == "UNKNOWN"
    assert "reconciled_quiescent" not in current["metadata"]


def test_reconcile_refuses_in_place_event_edit_after_snapshot(daemon_projects, monkeypatch) -> None:
    """An in-place rewrite of a committed worker_request observation keeps
    COUNT(*)/MAX(id) identical, so the append-only count/max guard cannot
    see it; reconciliation must still refuse via the frozen prefix digest."""
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "reconcile-inplace-event-edit")
    job_id = record["job_id"]
    daemon.store.update_job(job_id, "UNKNOWN")
    for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
        daemon.store.add_event(job_id, "worker_request", {
            "request_id": "synthetic-old-request", "phase": phase, "status": status,
        })
    original = daemon.store.update_reconciliation_scoped

    def edit_before_write(*args, **kwargs):
        # Rewrite the durable completion observation in place (observed
        # SUCCEEDED becomes RUNNING); no append and no delete, so the
        # event count and last event id are unchanged.
        daemon.store.db.execute(
            "UPDATE job_events SET metadata=? WHERE job_id=? AND id=("
            "SELECT MAX(id) FROM job_events WHERE job_id=?)",
            (json.dumps({
                "request_id": "synthetic-old-request", "phase": "observed",
                "status": "RUNNING",
            }), job_id, job_id),
        )
        daemon.store.db.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon.store, "update_reconciliation_scoped", edit_before_write)
    response = _call_job(daemon, "job.reconcile", job_id, project_a)
    assert response["success"] is False
    assert response["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert response["error"]["safe_retry"] is False
    current = daemon.store.job(job_id)
    assert current["status"] == "UNKNOWN"
    assert "reconciled_quiescent" not in current["metadata"]


def test_status_and_terminal_wait_preserve_small_result_shape(daemon_projects) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "small")
    _finish_job(daemon, record, {"success": True, "marker": "small-result"})
    expected = daemon.store.job(record["job_id"])

    status = _call_job(daemon, "job.status", record["job_id"], project_a)
    waited = _call_job(
        daemon, "job.wait", record["job_id"], project_a,
        timeout_s=0, poll_interval_s=0.01,
    )

    assert status["success"] is True
    assert waited["success"] is True
    assert status["data"] == expected
    assert waited["data"] == expected


def test_oversized_status_and_wait_skip_result_decode_and_result_is_chunked(
    daemon_projects, monkeypatch,
) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "large")
    result = {"success": True, "payload": "large-result-" + "x" * (4 * 1024 * 1024)}
    _finish_job(daemon, record, result)
    stored_bytes = daemon.store.db.execute(
        "SELECT length(CAST(result AS BLOB)) FROM operations WHERE operation_id=?",
        (record["operation_id"],),
    ).fetchone()[0]
    assert stored_bytes > _JOB_STATUS_WAIT_INLINE_RESULT_MAX_BYTES

    def unexpected_full_job_read(*_args, **_kwargs):
        raise AssertionError("oversized status/wait decoded the full operation result")

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(daemon.store, "_job", unexpected_full_job_read)
        scoped_patch.setattr(daemon.store, "_op", unexpected_full_job_read)
        status = _call_job(daemon, "job.status", record["job_id"], project_a)
        waited = _call_job(
            daemon, "job.wait", record["job_id"], project_a,
            timeout_s=0, poll_interval_s=0.01,
        )

    for response in (status, waited):
        assert response["success"] is True
        data = response["data"]
        assert data["status"] == "SUCCEEDED"
        assert data["operation"] == "synthetic.t35_status_wait"
        assert data["result_omitted"] is True
        assert data["result_size_bytes"] == stored_bytes
        assert data["result_available_via"] == "job.result.read"
        assert "result" not in data

    inline = _call_job(daemon, "job.result", record["job_id"], project_a)
    assert inline["success"] is False
    assert inline["error"]["code"] == "RESULT_TOO_LARGE"
    assert inline["data"]["result_size_bytes"] == stored_bytes
    assert inline["data"]["result_available_via"] == "job.result.read"

    pieces = []
    offset = 0
    result_sha256 = None
    while True:
        arguments = {"offset": offset, "limit": 65536}
        if result_sha256 is not None:
            arguments["result_sha256"] = result_sha256
        page = _call_job(
            daemon, "job.result.read", record["job_id"], project_a, **arguments,
        )
        assert page["success"] is True, page
        data = page["data"]
        assert data["offset"] == offset
        assert data["total_bytes"] == stored_bytes
        if result_sha256 is None:
            result_sha256 = data["result_sha256"]
        else:
            assert data["result_sha256"] == result_sha256
        pieces.append(base64.b64decode(data["chunk_base64"], validate=True))
        offset = data["next_offset"]
        if data["complete"]:
            break

    raw_result = b"".join(pieces)
    assert len(raw_result) == stored_bytes
    assert hashlib.sha256(raw_result).hexdigest() == result_sha256
    assert json.loads(raw_result) == result

    end_page = _call_job(
        daemon, "job.result.read", record["job_id"], project_a,
        offset=stored_bytes, limit=1, result_sha256=result_sha256,
    )
    assert end_page["success"] is True
    assert base64.b64decode(end_page["data"]["chunk_base64"], validate=True) == b""
    assert end_page["data"]["next_offset"] == stored_bytes
    assert end_page["data"]["complete"] is True


def test_other_inspect_granted_project_cannot_probe_or_read_a_job(
    daemon_projects, monkeypatch,
) -> None:
    daemon, project_a, project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "cross-project")
    _finish_job(daemon, record, {"success": True, "marker": "project-a-secret"})
    daemon.project_authority.authorize_operation(project_b, "inspect")
    calls = []
    original = daemon.store.job_for_status_wait

    def tracked_read(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon.store, "job_for_status_wait", tracked_read)
    response = _call_job(daemon, "job.status", record["job_id"], project_b)

    assert response["success"] is False
    assert response["error"]["code"] == "NODE_NOT_FOUND"
    assert calls == []
    assert "project-a-secret" not in json.dumps(response, ensure_ascii=False)


def test_status_and_result_size_use_one_real_wal_snapshot(
    daemon_projects, monkeypatch,
) -> None:
    daemon, project_a, _project_b, database_path = daemon_projects
    record = _begin_job(daemon, project_a, "snapshot")
    original_result = {"success": True, "payload": "snapshot-old-" + "y" * 300_000}
    _finish_job(daemon, record, original_result)
    old_result_bytes = daemon.store.db.execute(
        "SELECT length(CAST(result AS BLOB)) FROM operations WHERE operation_id=?",
        (record["operation_id"],),
    ).fetchone()[0]
    assert old_result_bytes > _JOB_STATUS_WAIT_INLINE_RESULT_MAX_BYTES
    original_read = daemon.store.job_for_status_wait

    def commit_new_state_while_snapshot_is_open(job_id: str, **kwargs):
        # _control_read has already opened and pinned project_job_read_snapshot.
        with sqlite3.connect(database_path, timeout=2) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE jobs SET status='FAILED' WHERE job_id=?", (job_id,))
            writer.execute(
                "UPDATE operations SET status='FAILED',result=? WHERE operation_id=?",
                (json.dumps({"success": False, "marker": "new-small-result"}, separators=(",", ":")),
                 record["operation_id"]),
            )
            writer.commit()
        return original_read(job_id, **kwargs)

    monkeypatch.setattr(
        daemon.store, "job_for_status_wait", commit_new_state_while_snapshot_is_open,
    )
    response = _call_job(daemon, "job.status", record["job_id"], project_a)

    assert response["success"] is True
    assert response["data"]["status"] == "SUCCEEDED"
    assert response["data"]["result_size_bytes"] == old_result_bytes
    assert response["data"]["result_omitted"] is True
    current = daemon.store.job(record["job_id"])
    assert current["status"] == "FAILED"
    assert current["result"] == {"success": False, "marker": "new-small-result"}


def test_wait_polls_real_running_store_to_terminal_without_omission(
    daemon_projects, monkeypatch,
) -> None:
    daemon, project_a, _project_b, _database_path = daemon_projects
    record = _begin_job(daemon, project_a, "poll")
    real_time = control_daemon_module.time
    sleeps = []

    def finish_on_poll_sleep(_seconds: float) -> None:
        sleeps.append(_seconds)
        if len(sleeps) == 1:
            _finish_job(
                daemon, record,
                {"success": True, "marker": "completed-during-poll"},
            )

    monkeypatch.setattr(
        control_daemon_module,
        "time",
        SimpleNamespace(monotonic=real_time.monotonic, sleep=finish_on_poll_sleep),
    )
    response = _call_job(
        daemon, "job.wait", record["job_id"], project_a,
        timeout_s=2, poll_interval_s=0.01,
    )

    assert response["success"] is True
    assert response["data"]["status"] == "SUCCEEDED"
    assert response["data"]["result"] == {
        "success": True, "marker": "completed-during-poll",
    }
    assert "result_omitted" not in response["data"]
    assert len(sleeps) == 1
