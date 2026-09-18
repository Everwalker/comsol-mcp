from comsol_mcp._operation_store import IdempotencyConflict, OperationStore, SCHEMA_VERSION
import sqlite3
import threading


def test_store_idempotency_result_and_restart_reconciliation(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    assert store.db.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
    record, reused = store.begin(request_id="r", idempotency_key="key", request_hash="hash", operation="run_study")
    assert reused is False
    store.finish(record["operation_id"], status="SUCCEEDED", result={"success": True})
    finished_job = store.job(record["job_id"])
    assert finished_job["status"] == "SUCCEEDED"
    assert finished_job["result"] == {"success": True}
    assert finished_job["finished_at"] is not None
    again, reused = store.begin(request_id="other", idempotency_key="key", request_hash="hash", operation="run_study")
    assert reused is True and again["result"] == {"success": True}
    try:
        store.begin(request_id="x", idempotency_key="key", request_hash="different", operation="run_study")
    except IdempotencyConflict:
        pass
    else: assert False
    job = store.start_job(record["operation_id"], {"model": "m"})
    assert store.reconcile_after_restart() == []
    assert store.job(job)["status"] == "SUCCEEDED"


def test_schema_zero_migrates_and_durable_metadata_round_trips(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path); db.execute("CREATE TABLE schema_meta (version INTEGER NOT NULL)"); db.execute("INSERT INTO schema_meta VALUES (0)"); db.commit(); db.close()
    store = OperationStore(path)
    store.save_metadata("sessions", "s", {"server": "x"})
    store.save_metadata("revisions", "m", {"revision": 4, "fingerprint": "f"})
    assert store.db.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute("SELECT revision FROM revisions WHERE model_key='m'").fetchone()[0] == 4


def test_concurrent_same_key_has_one_operation_and_job(tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    rows, errors = [], []
    def submit(digest="h"):
        try: rows.append(store.begin(request_id=str(len(rows)), idempotency_key="k", request_hash=digest, operation="solve"))
        except Exception as exc: errors.append(exc)
    workers = [threading.Thread(target=submit) for _ in range(10)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert not errors and len({row[0]["operation_id"] for row in rows}) == len({row[0]["job_id"] for row in rows}) == 1
    submit("other")
    assert len(errors) == 1 and isinstance(errors[0], IdempotencyConflict)


def test_two_connections_restart_events_and_terminal_reconcile(tmp_path):
    path = tmp_path / "ops.sqlite"; first, second = OperationStore(path), OperationStore(path)
    rows = []
    a = threading.Thread(target=lambda: rows.append(first.begin(request_id="a", idempotency_key="same", request_hash="h", operation="x")))
    b = threading.Thread(target=lambda: rows.append(second.begin(request_id="b", idempotency_key="same", request_hash="h", operation="x")))
    a.start(); b.start(); a.join(); b.join()
    record = rows[0][0]; job_id = record["job_id"]
    first.update_job(job_id, "RUNNING", {"phase": 1}); first.add_event(job_id, "one"); first.add_event(job_id, "two"); first.finish(record["operation_id"], status="SUCCEEDED", result={"ok": True}); first.update_job(job_id, "SUCCEEDED")
    first.put_metadata("sessions", "s", {"server": "x"}); first.close(); second.close()
    reopened = OperationStore(path)
    assert reopened.job(job_id)["result"] == {"ok": True}
    assert [event["event"] for event in reopened.events(job_id, 1, 1)] == ["two"]
    assert reopened.get_metadata("sessions", "s") == {"server": "x"}
    assert reopened.reconcile_after_restart() == []
    reopened.close()


def test_finish_commits_operation_and_job_terminal_state_before_reopen(tmp_path):
    path = tmp_path / "atomic.sqlite"
    store = OperationStore(path)
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="solve")
    store.update_job(record["job_id"], "RUNNING")
    store.finish(record["operation_id"], status="FAILED", result={"success": False, "error": "observed"})
    store.close()

    reopened = OperationStore(path)
    operation = reopened.get_operation(record["operation_id"])
    job = reopened.job(record["job_id"])
    assert operation["status"] == job["status"] == "FAILED"
    assert operation["result"] == job["result"] == {"success": False, "error": "observed"}
    assert operation["finished_at"] is not None and job["finished_at"] is not None
    assert reopened.reconcile_after_restart() == []
    reopened.close()


def test_running_update_preserves_original_started_timestamp(tmp_path):
    store = OperationStore(tmp_path / "timestamps.sqlite")
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="solve")
    store.update_job(record["job_id"], "RUNNING")
    store.db.execute("UPDATE jobs SET started_at=? WHERE job_id=?", ("2001-02-03 04:05:06", record["job_id"]))
    store.update_job(record["job_id"], "RUNNING", {"progress": 2})
    job = store.job(record["job_id"])
    assert job["started_at"] == "2001-02-03 04:05:06"
    assert job["metadata"]["progress"] == 2


def test_operation_and_job_share_running_and_restart_observations(tmp_path):
    store = OperationStore(tmp_path / "observations.sqlite")
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="solve")
    store.update_job(record["job_id"], "RUNNING")
    operation = store.get_operation(record["operation_id"])
    assert operation["status"] == "RUNNING"
    assert operation["started_at"] is not None
    store.reconcile_after_restart()
    assert store.get_operation(record["operation_id"])["status"] == "RECONCILING"
    assert store.job(record["job_id"])["status"] == "RECONCILING"
    store.close()


def test_list_metadata_rejects_unapproved_table_name(tmp_path):
    import pytest
    store = OperationStore(tmp_path / "metadata.sqlite")
    store.put_metadata("sessions", "s", {"server": "one"})
    assert store.list_metadata("sessions") == [{"server": "one"}]
    with pytest.raises(ValueError, match="unsupported metadata table"):
        store.list_metadata("sessions; DROP TABLE jobs")


def test_finish_persists_unknown_without_terminal_timestamp(tmp_path):
    store = OperationStore(tmp_path / "finish.sqlite")
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="solve")
    store.finish(record["operation_id"], status="UNKNOWN", result={"success": False, "error": {"safe_retry": False}})
    operation = store.get_operation(record["operation_id"])
    job = store.job(record["job_id"])
    assert operation["status"] == job["status"] == "UNKNOWN"
    assert operation["finished_at"] is None and job["finished_at"] is None
    assert job["result"]["error"]["safe_retry"] is False
    assert store.reconcile_after_restart() == [record["job_id"]]
    assert store.job(record["job_id"])["status"] == "RECONCILING"


def test_future_schema_rejected(tmp_path):
    path = tmp_path / "future.sqlite"; store = OperationStore(path); store.close()
    raw = sqlite3.connect(path); raw.execute("UPDATE schema_meta SET version=99"); raw.commit(); raw.close()
    import pytest
    with pytest.raises(RuntimeError, match="unsupported"):
        OperationStore(path)
