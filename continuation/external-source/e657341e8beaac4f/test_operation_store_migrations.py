"""Migration and backup tests against the actual historical v1 ledger layout."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from comsol_mcp._operation_store import OperationStore


_REAL_CONNECT = sqlite3.connect
_HISTORICAL_SCHEMA_SOURCE = {
    "commit": "a3418bd4546ecc33e26448e7f2a1982727a37153",
    "path": "comsol_mcp/_operation_store.py",
    "blob": "525c8897bd626d1e1d30255fcbaa0473e154f727",
}


def _make_head_v1_database(path: Path, *, indexes: bool = True) -> None:
    """Create v1 as recorded at the pinned historical commit/blob above."""
    db = _REAL_CONNECT(path)
    db.executescript(
        """
        CREATE TABLE schema_meta(version INTEGER NOT NULL);
        INSERT INTO schema_meta VALUES(1);
        CREATE TABLE operations(
            operation_id TEXT PRIMARY KEY,request_id TEXT,idempotency_key TEXT UNIQUE,
            request_hash TEXT,operation TEXT,status TEXT,result TEXT,metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,started_at TEXT,finished_at TEXT,
            effective_timeouts TEXT
        );
        CREATE TABLE jobs(
            job_id TEXT PRIMARY KEY,operation_id TEXT UNIQUE,status TEXT,metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,started_at TEXT,finished_at TEXT,
            effective_timeouts TEXT
        );
        CREATE TABLE job_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,event TEXT,metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE sessions(session_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);
        CREATE TABLE runtimes(runtime_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);
        CREATE TABLE revisions(model_key TEXT PRIMARY KEY,metadata TEXT NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);
        CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);
        INSERT INTO operations VALUES(
            'op-v1','request-v1','idem-v1','hash-v1','study.run','SUCCEEDED',
            '{"success":true,"value":3}', '{"project_id":"p-v1"}',
            '2026-01-02T03:04:05Z','2026-01-02T03:04:06Z','2026-01-02T03:04:07Z','{}'
        );
        INSERT INTO jobs VALUES(
            'job-v1','op-v1','SUCCEEDED','{"project_id":"p-v1"}',
            '2026-01-02T03:04:05Z','2026-01-02T03:04:06Z','2026-01-02T03:04:07Z','{}'
        );
        INSERT INTO job_events(id,job_id,event,metadata) VALUES
            (11,'job-v1','Started','{"request_id":"request-v1"}'),
            (12,'job-v1','Finished','{"status":"SUCCEEDED"}');
        INSERT INTO sessions VALUES('session-v1','{"schema_version":1,"session_id":"session-v1"}');
        INSERT INTO runtimes VALUES('runtime-v1','{"runtime_id":"runtime-v1"}');
        INSERT INTO revisions VALUES('model-v1','{"fingerprint":"rev-v1"}',7);
        INSERT INTO artifacts VALUES('artifact-v1','{"schema_version":1,"path":"legacy.mph"}');
        INSERT INTO checkpoints VALUES('checkpoint-v1','{"sha256":"abc"}');
        """
    )
    if indexes:
        db.executescript(
            "CREATE INDEX idx_jobs_status ON jobs(status);"
            "CREATE INDEX idx_jobs_created_at ON jobs(created_at);"
        )
    db.commit()
    db.close()


def _logical_snapshot(path: Path) -> tuple[list[tuple], dict[str, list[tuple]]]:
    db = _REAL_CONNECT(path)
    objects = [tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    tables = [row[1] for row in objects if row[0] == "table"]
    contents = {
        table: [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        for table in tables
    }
    db.close()
    return objects, contents


def test_historical_v1_extension_preserves_rows_idempotency_and_reopen(tmp_path):
    path = tmp_path / "historical-v1.sqlite"
    _make_head_v1_database(path)
    before_objects, before_rows = _logical_snapshot(path)
    assert {"resume_claims", "projects", "stage_attempts", "idx_stage_attempt_scope"}.isdisjoint(
        {row[1] for row in before_objects}
    )

    store = OperationStore(path)
    assert store.db.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    assert store.db.execute("PRAGMA table_info(resume_claims)").fetchall()
    after_objects, after_rows = _logical_snapshot(path)
    after_names = {row[1] for row in after_objects}
    assert after_names - {row[1] for row in before_objects} == {
        "resume_claims", "projects", "stage_attempts", "idx_stage_attempt_scope",
    }
    assert [row[1] for row in store.db.execute("PRAGMA table_info(stage_attempts)")] == [
        "attempt_id", "scope_digest", "stage_id", "idempotency_key", "request_hash",
        "status", "version", "record_json", "created_at", "updated_at",
    ]
    assert after_rows["operations"] == before_rows["operations"]
    assert after_rows["jobs"] == before_rows["jobs"]
    assert after_rows["job_events"] == before_rows["job_events"]
    for table in ("sessions", "runtimes", "revisions", "artifacts", "checkpoints"):
        assert after_rows[table] == before_rows[table]

    existing, reused = store.begin(
        request_id="new-request", idempotency_key="idem-v1", request_hash="hash-v1",
        operation="study.run",
    )
    assert reused is True
    assert existing["operation_id"] == "op-v1" and existing["job_id"] == "job-v1"
    assert existing["result"] == {"success": True, "value": 3}
    assert store.get_metadata("sessions", "session-v1")["session_id"] == "session-v1"
    assert store.get_metadata("revisions", "model-v1")["fingerprint"] == "rev-v1"
    assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM stage_attempts").fetchone()[0] == 0
    store.close()

    reopened = OperationStore(path)
    assert reopened.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 0
    assert reopened.db.execute("SELECT COUNT(*) FROM stage_attempts").fetchone()[0] == 0
    again, reused_again = reopened.begin(
        request_id="after-reopen", idempotency_key="idem-v1", request_hash="hash-v1",
        operation="study.run",
    )
    assert reused_again is True and again["operation_id"] == "op-v1"
    reopened.close()


@pytest.mark.parametrize("metadata_rows", [[99], [1, 1]])
def test_unknown_or_ambiguous_schema_metadata_rejects_without_logical_change(tmp_path, metadata_rows):
    path = tmp_path / "invalid-version.sqlite"
    db = _REAL_CONNECT(path)
    db.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
    db.executemany("INSERT INTO schema_meta(version) VALUES(?)", [(value,) for value in metadata_rows])
    db.execute("CREATE TABLE sentinel(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    db.execute("INSERT INTO sentinel VALUES('keep','unchanged')")
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    message = "ambiguous operation database schema metadata" if len(metadata_rows) > 1 else "unsupported operation database schema"
    with pytest.raises(RuntimeError, match=message):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_unversioned_nonempty_database_is_not_adopted_as_current(tmp_path):
    path = tmp_path / "unversioned.sqlite"
    db = _REAL_CONNECT(path)
    db.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
    db.execute("CREATE TABLE legacy(payload TEXT)")
    db.execute("INSERT INTO legacy VALUES('preserve')")
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    with pytest.raises(RuntimeError, match="unversioned operation database"):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_malformed_additive_table_is_rejected_without_changing_legacy_data(tmp_path):
    path = tmp_path / "malformed-extension.sqlite"
    _make_head_v1_database(path)
    db = _REAL_CONNECT(path)
    db.execute("CREATE TABLE resume_claims(source_job_id TEXT PRIMARY KEY)")
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    with pytest.raises(RuntimeError, match="invalid resume_claims schema"):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_malformed_stage_attempt_table_is_rejected_without_changing_legacy_data(tmp_path):
    path = tmp_path / "malformed-stage-attempt-extension.sqlite"
    _make_head_v1_database(path)
    db = _REAL_CONNECT(path)
    # Include columns needed for the additive index so the migration reaches
    # its exact schema validation instead of failing incidentally at DDL.
    db.execute(
        "CREATE TABLE stage_attempts("
        "attempt_id TEXT,scope_digest TEXT,stage_id TEXT,idempotency_key TEXT,"
        "request_hash TEXT,status TEXT,version INTEGER,record_json TEXT,"
        "created_at TEXT,updated_at TEXT)"
    )
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    with pytest.raises(RuntimeError, match="invalid stage_attempts schema"):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_composite_expression_index_does_not_satisfy_single_column_uniqueness(tmp_path):
    path = tmp_path / "composite-expression-index.sqlite"
    _make_head_v1_database(path)
    db = _REAL_CONNECT(path)
    db.execute(
        "CREATE TABLE resume_claims("
        "source_job_id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL,"
        "request_hash TEXT NOT NULL,child_operation_id TEXT UNIQUE NOT NULL,"
        "child_job_id TEXT UNIQUE NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    db.execute(
        "CREATE UNIQUE INDEX resume_claims_idem_expr "
        "ON resume_claims(idempotency_key, lower(request_hash))"
    )
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    with pytest.raises(RuntimeError, match="invalid resume_claims schema"):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_partial_unique_index_does_not_satisfy_single_column_uniqueness(tmp_path):
    path = tmp_path / "partial-unique-index.sqlite"
    _make_head_v1_database(path)
    db = _REAL_CONNECT(path)
    db.execute(
        "CREATE TABLE resume_claims("
        "source_job_id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL,"
        "request_hash TEXT NOT NULL,child_operation_id TEXT UNIQUE NOT NULL,"
        "child_job_id TEXT UNIQUE NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    db.execute(
        "CREATE UNIQUE INDEX resume_claims_idem_partial "
        "ON resume_claims(idempotency_key) WHERE request_hash != ''"
    )
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    with pytest.raises(RuntimeError, match="invalid resume_claims schema"):
        OperationStore(path)

    assert _logical_snapshot(path) == before


def test_migration_failure_closes_connection_and_preserves_original_error(tmp_path, monkeypatch):
    path = tmp_path / "close-on-migration-error.sqlite"
    _make_head_v1_database(path)
    db = _REAL_CONNECT(path)
    db.execute("CREATE TABLE resume_claims(source_job_id TEXT PRIMARY KEY)")
    db.commit()
    db.close()
    before = _logical_snapshot(path)

    import comsol_mcp._operation_store as operation_store

    class CloseRaisesConnection(sqlite3.Connection):
        close_calls = 0

        def close(self):
            type(self).close_calls += 1
            super().close()
            raise OSError("injected connection close failure")

    def connect_with_close_failure(*args, **kwargs):
        kwargs["factory"] = CloseRaisesConnection
        return _REAL_CONNECT(*args, **kwargs)

    monkeypatch.setattr(operation_store.sqlite3, "connect", connect_with_close_failure)
    with pytest.raises(RuntimeError, match="invalid resume_claims schema"):
        OperationStore(path)

    assert CloseRaisesConnection.close_calls == 1
    assert _logical_snapshot(path) == before


def test_historical_fixture_commit_and_blob_identity_resolve():
    commit = _HISTORICAL_SCHEMA_SOURCE["commit"]
    path = _HISTORICAL_SCHEMA_SOURCE["path"]
    expected_blob = _HISTORICAL_SCHEMA_SOURCE["blob"]
    resolved_blob = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert resolved_blob == expected_blob


def test_migration_ddl_failure_rolls_back_prior_schema_changes(tmp_path, monkeypatch):
    path = tmp_path / "injected-ddl-failure.sqlite"
    _make_head_v1_database(path, indexes=False)
    before = _logical_snapshot(path)

    import comsol_mcp._operation_store as operation_store

    real_connect = _REAL_CONNECT

    def connect_with_migration_failure(*args, **kwargs):
        connection = real_connect(*args, **kwargs)

        def deny_stage_attempt_table(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "stage_attempts":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_stage_attempt_table)
        return connection

    monkeypatch.setattr(operation_store.sqlite3, "connect", connect_with_migration_failure)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        OperationStore(path)
    monkeypatch.undo()

    assert _logical_snapshot(path) == before


def test_online_backup_and_restore_preserve_wal_data_and_reopen(tmp_path):
    source = tmp_path / "historical-v1.sqlite"
    backup = tmp_path / "before-migration.sqlite"
    restored = tmp_path / "restored.sqlite"
    _make_head_v1_database(source)

    source_db = _REAL_CONNECT(source)
    assert source_db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    source_db.execute(
        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
        ("job-v1", "CommittedWhileWalOpen", '{"preserve":true}'),
    )
    source_db.commit()
    backup_db = _REAL_CONNECT(backup)
    source_db.backup(backup_db)
    backup_db.close()
    source_db.close()
    before_migration = _logical_snapshot(backup)
    assert any(row[2] == "CommittedWhileWalOpen" for row in before_migration[1]["job_events"])
    assert "resume_claims" not in {row[1] for row in before_migration[0]}
    assert "stage_attempts" not in {row[1] for row in before_migration[0]}

    migrated = OperationStore(source)
    assert migrated.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 0
    assert migrated.db.execute("SELECT COUNT(*) FROM stage_attempts").fetchone()[0] == 0
    migrated.close()

    backup_db = _REAL_CONNECT(backup)
    restored_db = _REAL_CONNECT(restored)
    backup_db.backup(restored_db)
    restored_db.close()
    backup_db.close()
    assert _logical_snapshot(restored) == before_migration

    reopened = OperationStore(restored)
    assert reopened.db.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    assert reopened.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 0
    assert reopened.db.execute("SELECT COUNT(*) FROM stage_attempts").fetchone()[0] == 0
    assert reopened.db.execute(
        "SELECT event FROM job_events WHERE event='CommittedWhileWalOpen'"
    ).fetchone()[0] == "CommittedWhileWalOpen"
    record, reused = reopened.begin(
        request_id="rollback-request", idempotency_key="idem-v1", request_hash="hash-v1",
        operation="study.run",
    )
    assert reused is True and record["operation_id"] == "op-v1"
    reopened.close()
