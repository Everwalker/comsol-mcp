from __future__ import annotations

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


def test_oversized_status_and_wait_skip_result_decode_but_job_result_still_returns_it(
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
        assert data["result_available_via"] == "job.result"
        assert "result" not in data

    # The explicit result route remains the supported full-result access path.
    full_result = _call_job(daemon, "job.result", record["job_id"], project_a)
    assert full_result["success"] is True
    assert full_result["data"]["result"] == result


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
