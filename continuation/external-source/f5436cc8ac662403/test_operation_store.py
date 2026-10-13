from comsol_mcp._operation_store import IdempotencyConflict, OperationStore, SCHEMA_VERSION
import pytest
import sqlite3
import threading


def test_store_idempotency_result_and_restart_reconciliation(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    assert store.db.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
    assert [row[1] for row in store.db.execute("PRAGMA table_info(stage_attempts)")] == [
        "attempt_id", "scope_digest", "stage_id", "idempotency_key", "request_hash",
        "status", "version", "record_json", "created_at", "updated_at",
    ]
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
    assert store.db.execute(
        "SELECT type FROM sqlite_master WHERE name='stage_attempts'"
    ).fetchone()[0] == "table"
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


def test_job_event_snapshot_collects_large_exact_job_in_bounded_pages(tmp_path):
    """A full job audit must not depend on the legacy 1000-row event page."""
    import hashlib
    import json

    store = OperationStore(tmp_path / "large-event-snapshot.sqlite")
    try:
        record, _ = store.begin(
            request_id="large-event-snapshot", idempotency_key="large-event-snapshot",
            request_hash="a" * 64, operation="experiment.stage_run",
        )
        job_id = record["job_id"]
        for index in range(4923):
            store.add_event(job_id, "worker_request" if index % 2 else "progress", {"index": index})

        snapshot = store.collect_job_event_snapshot(job_id, page_size=1000)
        assert snapshot["job_id"] == job_id
        assert snapshot["complete"] is True
        assert snapshot["expected_event_count"] == 4923
        assert snapshot["fetched_event_count"] == 4923
        assert len(snapshot["events"]) == 4923
        event_ids = [row["id"] for row in snapshot["events"]]
        assert event_ids == sorted(set(event_ids))
        assert snapshot["id_sha256"] == hashlib.sha256(
            ",".join(str(event_id) for event_id in event_ids).encode("ascii")
        ).hexdigest()
        assert snapshot["events_sha256"] == hashlib.sha256(
            json.dumps(
                snapshot["events"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        assert [page["count"] for page in snapshot["pages"]] == [1000, 1000, 1000, 1000, 923]
        assert [page["offset"] for page in snapshot["pages"]] == [0, 1000, 2000, 3000, 4000]
        for page, offset in zip(snapshot["pages"], (0, 1000, 2000, 3000, 4000), strict=True):
            page_rows = snapshot["events"][offset:offset + page["count"]]
            assert page["events_sha256"] == hashlib.sha256(
                json.dumps(
                    page_rows, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        assert snapshot["terminal_sentinel_checked"] is True
        assert snapshot["terminal_sentinel_count"] == 0
    finally:
        store.close()


def test_job_event_snapshot_excludes_append_after_frozen_read_snapshot(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "snapshot-reader.sqlite")
    writer = OperationStore(tmp_path / "snapshot-reader.sqlite")
    try:
        record, _ = store.begin(
            request_id="snapshot-late-append", idempotency_key="snapshot-late-append",
            request_hash="b" * 64, operation="experiment.stage_run",
        )
        job_id = record["job_id"]
        for index in range(3):
            store.add_event(job_id, "progress", {"index": index})
        original_page = store._job_event_snapshot_page
        appended = False

        def append_after_snapshot_page(selected_job_id, snapshot_last_id, *, limit, offset):
            nonlocal appended
            if not appended:
                writer.add_event(selected_job_id, "late-append", {"outside_snapshot": True})
                appended = True
            return original_page(
                selected_job_id, snapshot_last_id, limit=limit, offset=offset,
            )

        monkeypatch.setattr(store, "_job_event_snapshot_page", append_after_snapshot_page)
        snapshot = store.collect_job_event_snapshot(job_id, page_size=2)
        assert appended is True
        assert snapshot["complete"] is True
        assert snapshot["expected_event_count"] == 3
        assert snapshot["fetched_event_count"] == 3
        assert snapshot["snapshot_last_id"] == snapshot["events"][-1]["id"]
        assert all(row["event"] != "late-append" for row in snapshot["events"])
        assert [row["event"] for row in store.events(job_id)] == [
            "progress", "progress", "progress", "late-append",
        ]
    finally:
        writer.close()
        store.close()


@pytest.mark.parametrize(
    "raw_metadata,expected_metadata",
    [
        (None, None),
        ("", None),
        ("not-json", None),
        ("null", None),
        ("[]", []),
        ("NaN", None),
    ],
    ids=["sql-null", "empty-text", "invalid-json", "json-null", "json-array", "nonstandard-nan"],
)
def test_job_event_snapshot_marks_malformed_metadata_incomplete(
    tmp_path, raw_metadata, expected_metadata,
):
    store = OperationStore(tmp_path / "snapshot-malformed.sqlite")
    try:
        record, _ = store.begin(
            request_id="snapshot-malformed", idempotency_key="snapshot-malformed",
            request_hash="c" * 64, operation="experiment.stage_run",
        )
        job_id = record["job_id"]
        store.add_event(job_id, "progress", {"ok": True})
        store.db.execute(
            "UPDATE job_events SET metadata=? WHERE job_id=?", (raw_metadata, job_id),
        )
        row_count_before = store.db.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        snapshot = store.collect_job_event_snapshot(job_id)
        row_count_after = store.db.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        assert snapshot["complete"] is False
        assert snapshot["status"] == "INCOMPLETE"
        assert snapshot["events"][0]["metadata"] == expected_metadata
        assert row_count_after == row_count_before == 1
        assert store.db.execute(
            "SELECT metadata FROM job_events WHERE job_id=?", (job_id,),
        ).fetchone()[0] == raw_metadata
        assert any("malformed" in issue for issue in snapshot["issues"])
    finally:
        store.close()


@pytest.mark.parametrize("metadata", [None, {}], ids=["public-none", "legal-empty-object"])
def test_job_event_snapshot_accepts_valid_empty_object_metadata(tmp_path, metadata):
    store = OperationStore(tmp_path / "snapshot-empty-object.sqlite")
    try:
        record, _ = store.begin(
            request_id="snapshot-empty-object", idempotency_key="snapshot-empty-object",
            request_hash="e" * 64, operation="experiment.stage_run",
        )
        job_id = record["job_id"]
        store.add_event(job_id, "progress", metadata)

        raw_metadata = store.db.execute(
            "SELECT metadata FROM job_events WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        snapshot = store.collect_job_event_snapshot(job_id)

        assert raw_metadata == "{}"
        assert snapshot["complete"] is True
        assert snapshot["status"] == "COMPLETE"
        assert snapshot["expected_event_count"] == snapshot["fetched_event_count"] == 1
        assert snapshot["events"][0]["metadata"] == {}
        assert snapshot["issues"] == []
    finally:
        store.close()


def test_job_event_snapshot_stops_at_finite_page_cap(tmp_path, monkeypatch):
    import comsol_mcp._operation_store as operation_store

    store = OperationStore(tmp_path / "snapshot-page-cap.sqlite")
    try:
        record, _ = store.begin(
            request_id="snapshot-page-cap", idempotency_key="snapshot-page-cap",
            request_hash="d" * 64, operation="experiment.stage_run",
        )
        job_id = record["job_id"]
        for index in range(4):
            store.add_event(job_id, "progress", {"index": index})
        monkeypatch.setattr(operation_store, "JOB_EVENT_SNAPSHOT_MAX_PAGES", 2)
        at_limit = store.collect_job_event_snapshot(job_id, page_size=2)
        assert at_limit["complete"] is True
        assert at_limit["expected_page_count"] == 2
        assert at_limit["fetched_event_count"] == 4

        store.add_event(job_id, "progress", {"index": 4})
        over_limit = store.collect_job_event_snapshot(job_id, page_size=2)
        assert over_limit["complete"] is False
        assert over_limit["expected_page_count"] == 3
        assert over_limit["fetched_event_count"] == 0
        assert over_limit["pages"] == []
        assert over_limit["terminal_sentinel_checked"] is False
        assert any("bounded page limit" in issue for issue in over_limit["issues"])
    finally:
        store.close()


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
