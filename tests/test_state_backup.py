from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_state import restore_runtime_state, save_runtime_state
from comsol_mcp._state_backup import (
    ACTIVE_RESTORE_JOURNAL,
    DATABASE_NAME,
    ProcessLock,
    StateBackupError,
    assert_no_pending_restore,
    create_backup,
    main,
    recover_restore,
    restore_backup,
)


def _seed_home(home: Path, *, live_unknown: bool = True) -> tuple[str, str, object]:
    home.mkdir(parents=True)
    store = OperationStore(home / DATABASE_NAME)
    successful, _ = store.begin(
        request_id="request-success", idempotency_key="idem-success", request_hash="hash-success",
        operation="study.run",
    )
    store.finish(successful["operation_id"], status="SUCCEEDED", result={"success": True, "value": 3})
    active, _ = store.begin(
        request_id="request-active", idempotency_key="idem-active", request_hash="hash-active",
        operation="study.run",
    )
    if live_unknown:
        store.update_job(active["job_id"], "UNKNOWN", {"safe_retry": False, "replay_performed": False})

    ledger = SessionLedger("session-old", "server-old")
    old_ref = ledger.bind_model("model-A", fingerprint="old-model-fingerprint")
    ticket = ledger.begin_write("study.run", {}, old_ref, 0, fingerprint="old-model-fingerprint")
    old_identity = {
        "runtime_id": "runtime-old", "worker_instance_id": "worker-old",
        "connection_epoch": 4, "server_instance_id": "server-old",
    }
    save_runtime_state(store, ledger, old_identity, [ticket])
    store.close()
    return successful["job_id"], active["job_id"], old_ref


def _logical_snapshot(path: Path) -> dict[str, list[tuple]]:
    db = sqlite3.connect(path)
    try:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {
            table: [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    finally:
        db.close()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup(home: Path, root: Path) -> tuple[Path, Path]:
    output = root / "backups" / "ledger.sqlite3"
    output.parent.mkdir(parents=True)
    result = create_backup(home, output)
    assert result["success"] is True
    return output, Path(result["manifest_path"])


def _make_symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable on this host: {type(exc).__name__}")


def test_online_backup_includes_committed_wal_pages_without_migrating_source(tmp_path):
    home = tmp_path / "control"
    home.mkdir()
    db_path = home / DATABASE_NAME
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
    db.execute("INSERT INTO schema_meta VALUES(1)")
    db.executescript(
        "CREATE TABLE operations(operation_id TEXT PRIMARY KEY,request_id TEXT,idempotency_key TEXT,"
        "request_hash TEXT,operation TEXT,status TEXT,result TEXT,metadata TEXT,created_at TEXT,"
        "started_at TEXT,finished_at TEXT,effective_timeouts TEXT);"
        "CREATE TABLE jobs(job_id TEXT PRIMARY KEY,operation_id TEXT,status TEXT,metadata TEXT,"
        "created_at TEXT,started_at TEXT,finished_at TEXT,effective_timeouts TEXT);"
        "CREATE TABLE job_events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,event TEXT,metadata TEXT,created_at TEXT);"
        "CREATE TABLE sessions(session_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);"
        "CREATE TABLE runtimes(runtime_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);"
        "CREATE TABLE revisions(model_key TEXT PRIMARY KEY,metadata TEXT NOT NULL,revision INTEGER NOT NULL);"
        "CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);"
        "CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY,metadata TEXT NOT NULL);"
    )
    db.execute(
        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
        ("wal-job", "CommittedInWAL", '{"retained":true}'),
    )
    db.commit()
    assert Path(str(db_path) + "-wal").exists()
    before_schema = [tuple(row) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]

    output, manifest_path = _backup(home, tmp_path)
    source_schema = [tuple(row) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    assert source_schema == before_schema
    assert "resume_claims" not in {row[1] for row in source_schema}
    db.close()

    copied = sqlite3.connect(output)
    try:
        assert copied.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert copied.execute("SELECT event FROM job_events WHERE event='CommittedInWAL'").fetchone()[0] == "CommittedInWAL"
    finally:
        copied.close()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["database_sha256"] == _hash(output)
    assert manifest["database_size_bytes"] == output.stat().st_size
    assert "model/artifact files are not included" in manifest["backup_scope"]


def test_backup_uses_kernel_lock_not_lockfile_existence(tmp_path):
    home = tmp_path / "control"
    _seed_home(home)
    lock_path = home / "control.lock"
    lock_path.write_text("stale lockfile contents do not prove a live owner\n")
    result = create_backup(home, tmp_path / "ledger.sqlite3")
    assert result["success"] is True

    lock = ProcessLock(lock_path)
    try:
        with pytest.raises(StateBackupError) as exc:
            create_backup(home, tmp_path / "busy.sqlite3")
        assert exc.value.code in {"ENGINE_BUSY", "CONTROL_HOME_BUSY"}
    finally:
        lock.close()


def test_paths_reject_home_symlink_backup_inside_home_and_existing_target(tmp_path):
    home = tmp_path / "control"
    _seed_home(home)
    with pytest.raises(StateBackupError, match="outside"):
        create_backup(home, home / "inside.sqlite3")
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"preserve")
    with pytest.raises(StateBackupError) as exc:
        create_backup(home, existing)
    assert exc.value.code == "DESTINATION_EXISTS"

    alias = tmp_path / "control-alias"
    _make_symlink_or_skip(alias, home, target_is_directory=True)
    with pytest.raises(StateBackupError) as exc:
        create_backup(alias, tmp_path / "alias-backup.sqlite3")
    assert exc.value.code == "SYMLINK_PATH"

    target = tmp_path / "real-target.sqlite3"
    target.write_bytes(b"target")
    destination_alias = tmp_path / "destination-alias.sqlite3"
    _make_symlink_or_skip(destination_alias, target)
    with pytest.raises(StateBackupError) as exc:
        create_backup(home, destination_alias)
    assert exc.value.code == "SYMLINK_PATH"
    with pytest.raises(StateBackupError) as exc:
        create_backup(home, tmp_path / "control" / ".." / "escaped.sqlite3")
    assert exc.value.code == "PATH_TRAVERSAL"


def test_backup_rejects_symlinked_database_and_restore_rejects_tampered_backup(tmp_path):
    home = tmp_path / "control"
    _seed_home(home)
    actual = tmp_path / "actual.sqlite3"
    (home / DATABASE_NAME).replace(actual)
    (home / DATABASE_NAME).symlink_to(actual)
    with pytest.raises(StateBackupError) as exc:
        create_backup(home, tmp_path / "should-not-exist.sqlite3")
    assert exc.value.code == "SYMLINK_PATH"

    (home / DATABASE_NAME).unlink()
    actual.replace(home / DATABASE_NAME)
    backup, manifest = _backup(home, tmp_path)
    backup_alias = tmp_path / "backup-alias.sqlite3"
    _make_symlink_or_skip(backup_alias, backup)
    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup_alias, manifest, confirm=True)
    assert exc.value.code == "SYMLINK_PATH"
    with backup.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup, manifest, confirm=True)
    assert exc.value.code == "BACKUP_HASH_MISMATCH"
    assert not (home / ACTIVE_RESTORE_JOURNAL).exists()


def test_restore_rejects_unmanifested_wal_even_when_main_hash_matches(tmp_path):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest_path = _backup(home, tmp_path)
    live_hash = _hash(home / DATABASE_NAME)

    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["database_sha256"] = _hash(backup)
        manifest["database_size_bytes"] = backup.stat().st_size
        connection.execute(
            "INSERT INTO operations(operation_id,request_id,idempotency_key,request_hash,operation,status,result,metadata) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("unmanifested-op", "unmanifested-request", "unmanifested-key", "unmanifested-hash",
             "study.run", "QUEUED", "{}", "{}"),
        )
        connection.execute(
            "INSERT INTO jobs(job_id,operation_id,status,metadata) VALUES(?,?,?,?)",
            ("unmanifested-job", "unmanifested-op", "QUEUED", "{}"),
        )
        connection.commit()
        assert Path(str(backup) + "-wal").is_file()
        assert _hash(backup) == manifest["database_sha256"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(StateBackupError) as exc:
            restore_backup(home, backup, manifest_path, confirm=True)
        assert exc.value.code == "BACKUP_SIDECAR_PRESENT"
        assert _hash(home / DATABASE_NAME) == live_hash
        assert not (home / ACTIVE_RESTORE_JOURNAL).exists()
    finally:
        connection.close()


def test_restore_stages_only_the_private_copy_if_source_changes_after_verification(tmp_path, monkeypatch):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest_path = _backup(home, tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = _logical_snapshot(backup)

    import comsol_mcp._state_backup as state_backup

    real_stage = state_backup._stage_backup
    observed = False

    def mutate_external_source(control_home, verified_copy):
        nonlocal observed
        observed = True
        assert verified_copy != backup
        assert _hash(verified_copy) == manifest["database_sha256"]
        backup.write_bytes(b"changed after verified private copy was made")
        return real_stage(control_home, verified_copy)

    monkeypatch.setattr(state_backup, "_stage_backup", mutate_external_source)
    result = restore_backup(home, backup, manifest_path, confirm=True)
    assert result["success"] is True
    assert observed
    assert _logical_snapshot(home / DATABASE_NAME) == expected


def test_restore_stages_migration_preserves_prior_files_and_startup_does_not_replay(tmp_path):
    home = tmp_path / "control"
    successful_job, unknown_job, old_ref = _seed_home(home)
    legacy = sqlite3.connect(home / DATABASE_NAME)
    legacy.execute("DROP TABLE resume_claims")
    legacy.commit()
    legacy.close()
    backup, manifest = _backup(home, tmp_path)
    store = OperationStore(home / DATABASE_NAME)
    try:
        changed, _ = store.begin(
            request_id="request-after-backup", idempotency_key="idem-after-backup",
            request_hash="hash-after-backup", operation="study.run",
        )
        store.update_job(changed["job_id"], "FAILED", {"message": "post-backup mutation"})
    finally:
        store.close()
    prior_live_hash = _hash(home / DATABASE_NAME)
    prior_live_snapshot = _logical_snapshot(home / DATABASE_NAME)

    result = restore_backup(home, backup, manifest, confirm=True)
    assert result["success"] is True
    assert result["daemon_started"] is False
    assert not (home / "control.json").exists()
    assert not (home / ACTIVE_RESTORE_JOURNAL).exists()
    assert _hash(Path(result["recovery_directory"]) / "original" / DATABASE_NAME) == prior_live_hash
    restored_snapshot = _logical_snapshot(home / DATABASE_NAME)
    backup_snapshot = _logical_snapshot(backup)
    assert {table: rows for table, rows in restored_snapshot.items() if table != "resume_claims"} == backup_snapshot
    assert restored_snapshot["resume_claims"] == []
    recovery_journal = Path(result["recovery_directory"]) / "restore-journal.json"
    assert json.loads(recovery_journal.read_text())["phase"] == "RESTORED_AND_VERIFIED"
    assert any(row[2] == "FAILED" and "post-backup mutation" in row[3] for row in prior_live_snapshot["jobs"])

    class SpyWorker:
        def __init__(self):
            self.health_calls = 0
            self.execution_calls = []

        def health(self, **_kwargs):
            self.health_calls += 1
            return {"connected": False}

        def client(self):
            self.execution_calls.append("client")
            raise AssertionError("startup must not reconnect or replay an operation")

        def start(self):
            self.execution_calls.append("start")
            raise AssertionError("startup must not start a Worker")

    worker = SpyWorker()
    from comsol_mcp._control_daemon import ControlDaemon

    daemon = ControlDaemon(home, worker=worker)
    try:
        assert daemon.store.job(successful_job)["status"] == "SUCCEEDED"
        assert daemon.store.job(unknown_job)["status"] == "RECONCILING"
        assert daemon.running == {}
        assert worker.execution_calls == []

        new_identity = {
            "runtime_id": "runtime-new", "worker_instance_id": "worker-new",
            "connection_epoch": 1, "server_instance_id": "server-new",
        }
        restored_ledger, unknown = restore_runtime_state(daemon.store, "session-old", new_identity)
        new_ref = restored_ledger._models["model-A"].ref
        assert new_ref.server_instance_id == "server-new"
        assert new_ref.generation > old_ref.generation
        assert restored_ledger._models["model-A"].dirty is True
        assert unknown and all(item["safe_retry"] is False for item in unknown)
        assert worker.execution_calls == []
    finally:
        daemon.close()


def test_restore_failure_rolls_back_exact_main_wal_shm_and_keeps_history(tmp_path, monkeypatch):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest = _backup(home, tmp_path)
    db_path = home / DATABASE_NAME
    store = OperationStore(db_path)
    try:
        changed, _ = store.begin(
            request_id="current", idempotency_key="current-key", request_hash="current-hash", operation="study.run",
        )
        store.update_job(changed["job_id"], "FAILED", {"current": True})
    finally:
        store.close()
    db_path.with_name(db_path.name + "-wal").write_bytes(b"saved-stale-wal-bytes")
    db_path.with_name(db_path.name + "-shm").write_bytes(b"saved-stale-shm-bytes")
    before = {path.name: path.read_bytes() for path in home.glob(f"{DATABASE_NAME}*")}

    import comsol_mcp._state_backup as state_backup

    real_replace = state_backup.os.replace
    real_rollback = state_backup._rollback_restore
    failed_once = False
    rollback_lock_observed = False

    def probe_lock_then_rollback(control_home, journal):
        nonlocal rollback_lock_observed
        source_root = Path(__file__).resolve().parents[1]
        probe_code = (
            "import sys; from pathlib import Path; "
            "from comsol_mcp._managed_backend import ProcessLock; "
            "lock_path=Path(sys.argv[1])/'control.lock'; "
            "\ntry: lock=ProcessLock(lock_path)"
            "\nexcept Exception: print('BUSY')"
            "\nelse: lock.close(); print('FREE')"
        )
        probe = subprocess.run(
            [sys.executable, "-c", probe_code, str(control_home)],
            cwd=source_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.strip() == "BUSY"
        rollback_lock_observed = True
        return real_rollback(control_home, journal)

    monkeypatch.setattr(state_backup, "_rollback_restore", probe_lock_then_rollback)

    def fail_candidate_install(src, dst):
        nonlocal failed_once
        if (not failed_once and Path(dst) == db_path and Path(src).name.startswith(f".{DATABASE_NAME}.stage-")):
            failed_once = True
            raise OSError("injected stage install failure")
        return real_replace(src, dst)

    monkeypatch.setattr(state_backup.os, "replace", fail_candidate_install)
    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup, manifest, confirm=True)
    assert exc.value.code == "RESTORE_FAILED"
    assert failed_once
    assert rollback_lock_observed
    assert not (home / ACTIVE_RESTORE_JOURNAL).exists()
    after = {path.name: path.read_bytes() for path in home.glob(f"{DATABASE_NAME}*")}
    assert after == before
    archived = list(home.glob("state-recovery-*/restore-journal.json"))
    assert archived and json.loads(archived[-1].read_text())["phase"] == "ROLLED_BACK_TO_PRE_RESTORE_FILE_SET"


def test_failed_rollback_keeps_recovery_journal_and_recover_restores_bytes(tmp_path, monkeypatch):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest = _backup(home, tmp_path)
    db_path = home / DATABASE_NAME
    db_path.with_name(db_path.name + "-wal").write_bytes(b"old-wal")
    before = {path.name: path.read_bytes() for path in home.glob(f"{DATABASE_NAME}*")}

    import comsol_mcp._state_backup as state_backup

    real_replace = state_backup.os.replace
    database_replace_attempts = 0

    def fail_install_and_first_rollback(src, dst):
        nonlocal database_replace_attempts
        if Path(dst) == db_path:
            database_replace_attempts += 1
            if database_replace_attempts <= 2:
                raise OSError("injected install/rollback failure")
        return real_replace(src, dst)

    monkeypatch.setattr(state_backup.os, "replace", fail_install_and_first_rollback)
    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup, manifest, confirm=True)
    assert exc.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    assert "keep the daemon stopped" in str(exc.value).lower()
    assert "daemon startup is blocked" not in str(exc.value).lower()
    assert (home / ACTIVE_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as gate:
        assert_no_pending_restore(home)
    assert gate.value.code == "RESTORE_RECOVERY_REQUIRED"

    monkeypatch.setattr(state_backup.os, "replace", real_replace)
    recovered = recover_restore(home, confirm=True)
    assert recovered["success"] is True
    assert recovered["daemon_started"] is False
    assert not (home / ACTIVE_RESTORE_JOURNAL).exists()
    after = {path.name: path.read_bytes() for path in home.glob(f"{DATABASE_NAME}*")}
    assert after == before


def test_restore_requires_confirmation_and_cli_entrypoint_is_registered(tmp_path, capsys):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest = _backup(home, tmp_path)
    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup, manifest, confirm=False)
    assert exc.value.code == "CONFIRM_REQUIRED"
    status = main(["backup", "--home", str(home), "--destination", str(tmp_path / "cli.sqlite3")])
    assert status == 0
    output = json.loads(capsys.readouterr().out)
    assert output["success"] is True and output["operation"] == "backup"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'comsol-mcp-state = "comsol_mcp._state_backup:main"' in pyproject.read_text(encoding="utf-8")


def test_restore_refuses_symlinked_live_database_before_staging(tmp_path):
    home = tmp_path / "control"
    _seed_home(home)
    backup, manifest = _backup(home, tmp_path)
    live = home / DATABASE_NAME
    external = tmp_path / "external-live.sqlite3"
    live.replace(external)
    _make_symlink_or_skip(live, external)
    before = external.read_bytes()

    with pytest.raises(StateBackupError) as exc:
        restore_backup(home, backup, manifest, confirm=True)
    assert exc.value.code == "SYMLINK_PATH"
    assert external.read_bytes() == before
    assert not (home / ACTIVE_RESTORE_JOURNAL).exists()


def test_startup_gate_refuses_any_pending_restore_journal(tmp_path):
    home = tmp_path / "control"
    home.mkdir()
    (home / ACTIVE_RESTORE_JOURNAL).write_text("malformed but present", encoding="utf-8")
    with pytest.raises(StateBackupError) as exc:
        assert_no_pending_restore(home)
    assert exc.value.code == "RESTORE_RECOVERY_REQUIRED"
