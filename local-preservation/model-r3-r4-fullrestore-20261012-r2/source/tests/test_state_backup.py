from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
import subprocess
import sys
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_state import restore_runtime_state, save_runtime_state
from comsol_mcp._state_backup import (
    ACTIVE_FULL_RESTORE_JOURNAL,
    ACTIVE_RESTORE_JOURNAL,
    DATABASE_NAME,
    ProcessLock,
    StateBackupError,
    assert_no_pending_restore,
    create_backup,
    create_full_backup,
    main,
    recover_restore,
    recover_full_restore,
    restore_backup,
    restore_full_backup,
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


def _seed_full_state(home: Path, workspace_root: Path) -> tuple[str, str, str, dict]:
    """Create real ProjectAuthority and ArtifactStore records for full restore."""
    from comsol_mcp._artifact_store import (
        local_artifact_host_identity, local_engine_host_identity,
        register_project_artifact,
    )
    from comsol_mcp._project_authority import ProjectAuthority

    home.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    store = OperationStore(home / DATABASE_NAME)
    authority = ProjectAuthority(store, workspace_root=workspace_root,
                                 permission_provider=lambda: {"inspect", "project_write", "compute"},
                                 grant_ceiling={"inspect", "project_write", "compute"})
    project_result = authority.dispatch("project.create", {
        "label": "Full backup fixture", "workspace": "science/full-backup",
        "policy": {"permissions": ["inspect", "project_write"]},
    })
    project = project_result["data"]["project"]
    project_root = Path(project["workspace"])
    source = project_root / "inputs" / "model.mph"
    source.parent.mkdir(parents=True)
    with zipfile.ZipFile(source, "w") as mph:
        mph.writestr("model/fixture.txt", "registered full backup fixture")
    host_identity = local_artifact_host_identity()
    engine_identity = local_engine_host_identity("127.0.0.1:2040")
    artifact = register_project_artifact(
        project_root, store, "inputs/model.mph", project_id=project["project_id"],
        role="saved_model", classification="project_local", host_identity=host_identity,
        engine_host_identity=engine_identity, request_id="full-backup-artifact",
        registering_operation_id="artifact.register", artifact_type="application/vnd.comsol.mph",
        format_version="comsol-mph-savecopy/1",
    )
    unknown, _ = store.begin(
        request_id="full-backup-unknown", idempotency_key="full-backup-unknown",
        request_hash="f" * 64, operation="study.run",
        metadata={"operation": "study.run", "arguments": {"study": "std1"},
                  "execution": {"project_id": project["project_id"],
                                "model_ref": {"schema_version": 1, "session_id": "old-session",
                                              "server_instance_id": "old-server", "model_tag": "mcp_saved",
                                              "generation": 8}, "expected_revision": 5}},
    )
    store.update_job(unknown["job_id"], "UNKNOWN", {"safe_retry": False, "replay_performed": False})
    store.finish(unknown["operation_id"], status="UNKNOWN",
                 result={"success": False, "error": {"code": "EXECUTION_STATE_UNKNOWN"},
                         "data": {"safe_retry": False, "replay_performed": False}})
    store.close()
    return project["project_id"], artifact["artifact_id"], unknown["operation_id"], project


def test_full_backup_restore_rebinds_project_and_artifact_without_replaying_unknown(tmp_path):
    from comsol_mcp._artifact_store import (
        local_artifact_host_identity, local_engine_host_identity,
        resolve_registered_artifact,
    )
    from comsol_mcp._project_authority import ProjectAuthority

    source_home = tmp_path / "source-control"
    source_workspaces = tmp_path / "source-workspaces"
    project_id, artifact_id, operation_id, _project = _seed_full_state(source_home, source_workspaces)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backed = create_full_backup(source_home, backup_dir / "full-state.tar")
    assert backed["success"] is True
    assert backed["project_count"] == 1
    assert backed["file_count"] == 1
    archive_bytes = Path(backed["archive_path"]).read_bytes()
    with pytest.raises(StateBackupError) as no_clobber:
        create_full_backup(source_home, backed["archive_path"])
    assert no_clobber.value.code == "DESTINATION_EXISTS"
    assert Path(backed["archive_path"]).read_bytes() == archive_bytes
    sidecar_collision = backup_dir / "sidecar-collision.tar"
    Path(str(sidecar_collision) + ".manifest.json").write_bytes(b"keep sidecar")
    with pytest.raises(StateBackupError) as sidecar_refusal:
        create_full_backup(source_home, sidecar_collision)
    assert sidecar_refusal.value.code == "DESTINATION_EXISTS"
    assert not sidecar_collision.exists()
    assert Path(str(sidecar_collision) + ".manifest.json").read_bytes() == b"keep sidecar"

    restored_home = tmp_path / "restored-control"
    restored_workspace = tmp_path / "restored-workspaces"
    restored_home.mkdir()
    restored_workspace.mkdir()
    restored = restore_full_backup(restored_home, restored_workspace, backed["archive_path"],
                                   backed["manifest_path"], confirm=True)
    assert restored["success"] is True
    assert restored["worker_or_model_bindings_revived"] is False
    assert restored["operation_replay_performed"] is False
    assert not (restored_home / ACTIVE_FULL_RESTORE_JOURNAL).exists()

    reopened = OperationStore(restored_home / DATABASE_NAME)
    try:
        saved_operation = reopened.get_operation(operation_id)
        assert saved_operation["status"] == "UNKNOWN"
        assert saved_operation["result"]["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert saved_operation["result"]["data"] == {"safe_retry": False, "replay_performed": False}
        assert reopened.operation_job(operation_id)["status"] == "UNKNOWN"
        authority = ProjectAuthority(reopened, workspace_root=restored_workspace,
                                     permission_provider=lambda: set(), grant_ceiling=set())
        project = authority.get_project(project_id)
        restored_project_root = Path(project["workspace"])
        assert restored_project_root.is_relative_to(restored_workspace)
        resolved = resolve_registered_artifact(
            restored_project_root, reopened, artifact_id, project_id=project_id,
            current_host_identity=local_artifact_host_identity(),
            current_engine_host_identity=local_engine_host_identity("127.0.0.1:2040"),
            current_server_instance_id="new-server-after-rebind",
        )
        assert resolved["sha256"] == artifact_id
        assert resolved["size"] > 0
        assert resolved["path"].read_bytes()[:2] == b"PK"
        audit = json.loads(Path(restored["rebind_audit_path"]).read_text())
        assert audit["projects"][0]["project_id"] == project_id
        assert audit["projects"][0]["restored_revision"] > audit["projects"][0]["source_revision"]
    finally:
        reopened.close()


@pytest.mark.parametrize("corruption,expected_code", [
    ("changed", "ARTIFACT_HASH_MISMATCH"),
    ("missing", "ARTIFACT_UNVERIFIED"),
    ("outside", "ARTIFACT_SCOPE_MISMATCH"),
])
def test_full_backup_refuses_changed_missing_or_outside_registered_file(tmp_path, corruption, expected_code):
    source_home = tmp_path / "source-control"
    source_workspaces = tmp_path / "source-workspaces"
    project_id, artifact_id, _operation_id, project = _seed_full_state(source_home, source_workspaces)
    store = OperationStore(source_home / DATABASE_NAME)
    record = store.get_metadata("artifacts", artifact_id)
    registered = Path(project["workspace"]) / record["path"]
    if corruption == "changed":
        registered.write_bytes(registered.read_bytes() + b"tamper")
    elif corruption == "missing":
        registered.unlink()
    else:
        outside = tmp_path / "outside.mph"
        outside.write_bytes(registered.read_bytes())
        record["path"] = str(outside)
        with store.lock:
            store.db.execute("UPDATE artifacts SET metadata=? WHERE artifact_id=?",
                             (json.dumps(record, sort_keys=True), artifact_id))
    store.close()
    with pytest.raises(StateBackupError) as rejected:
        create_full_backup(source_home, tmp_path / "must-not-publish.tar")
    assert rejected.value.code == expected_code
    assert not (tmp_path / "must-not-publish.tar").exists()
    assert project_id


def test_full_restore_tampered_archive_nonempty_target_and_post_install_failure_rollback(tmp_path, monkeypatch):
    import comsol_mcp._state_backup as state_backup

    source_home = tmp_path / "source-control"
    source_workspaces = tmp_path / "source-workspaces"
    _seed_full_state(source_home, source_workspaces)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backed = create_full_backup(source_home, backup_dir / "full-state.tar")

    occupied_home = tmp_path / "occupied-control"
    occupied_workspace = tmp_path / "occupied-workspaces"
    occupied_home.mkdir()
    occupied_workspace.mkdir()
    sentinel = occupied_workspace / "keep.txt"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(StateBackupError) as occupied:
        restore_full_backup(occupied_home, occupied_workspace, backed["archive_path"],
                            backed["manifest_path"], confirm=True)
    assert occupied.value.code == "RESTORE_TARGET_NOT_EMPTY"
    assert sentinel.read_bytes() == b"preserve"

    tampered = backup_dir / "tampered.tar"
    tampered.write_bytes(Path(backed["archive_path"]).read_bytes() + b"tamper")
    tampered_manifest = backup_dir / "tampered.tar.manifest.json"
    tampered_manifest_data = json.loads(Path(backed["manifest_path"]).read_text())
    tampered_manifest_data["archive_name"] = tampered.name
    tampered_manifest.write_text(json.dumps(tampered_manifest_data, sort_keys=True), encoding="utf-8")
    tampered_home = tmp_path / "tampered-control"
    tampered_workspace = tmp_path / "tampered-workspaces"
    tampered_home.mkdir()
    tampered_workspace.mkdir()
    with pytest.raises(StateBackupError) as changed:
        restore_full_backup(tampered_home, tampered_workspace, tampered, tampered_manifest, confirm=True)
    assert changed.value.code == "FULL_BACKUP_HASH_MISMATCH"
    assert not (tampered_home / DATABASE_NAME).exists()
    assert not any(tampered_workspace.iterdir())
    assert not (tampered_home / ACTIVE_FULL_RESTORE_JOURNAL).exists()

    rollback_home = tmp_path / "rollback-control"
    rollback_workspace = tmp_path / "rollback-workspaces"
    rollback_home.mkdir()
    rollback_workspace.mkdir()
    original_validate = state_backup._validate_rebound_database
    monkeypatch.setattr(state_backup, "_validate_rebound_database",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected validation failure")))
    with pytest.raises(StateBackupError) as failed:
        restore_full_backup(rollback_home, rollback_workspace, backed["archive_path"],
                            backed["manifest_path"], confirm=True)
    assert failed.value.code == "FULL_RESTORE_FAILED", (failed.value.code, repr(failed.value.__cause__), failed.value.recovery_path)
    assert not (rollback_home / DATABASE_NAME).exists()
    assert not any(rollback_workspace.iterdir())
    assert not (rollback_home / ACTIVE_FULL_RESTORE_JOURNAL).exists()
    monkeypatch.setattr(state_backup, "_validate_rebound_database", original_validate)


def test_full_restore_refuses_filesystem_root_as_workspace_target(tmp_path):
    home = tmp_path / "restore-control"
    home.mkdir()
    with pytest.raises(StateBackupError) as rejected:
        restore_full_backup(home, Path(Path.home().anchor), tmp_path / "missing.tar",
                            tmp_path / "missing.manifest.json", confirm=True)
    assert rejected.value.code == "RESTORE_TARGET_INVALID"
    assert not (home / DATABASE_NAME).exists()
    assert not (home / ACTIVE_FULL_RESTORE_JOURNAL).exists()


def test_full_restore_incomplete_rollback_fences_startup_then_explicit_recovery_clears_targets(tmp_path, monkeypatch):
    import comsol_mcp._state_backup as state_backup

    source_home = tmp_path / "source-control"
    source_workspaces = tmp_path / "source-workspaces"
    _seed_full_state(source_home, source_workspaces)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backed = create_full_backup(source_home, backup_dir / "full-state.tar")

    home = tmp_path / "recovery-control"
    workspace = tmp_path / "recovery-workspaces"
    home.mkdir()
    workspace.mkdir()
    original_rollback = state_backup._rollback_full_restore
    monkeypatch.setattr(state_backup, "_validate_rebound_database",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected restore failure")))
    monkeypatch.setattr(state_backup, "_rollback_full_restore",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            StateBackupError("RESTORE_ROLLBACK_INCOMPLETE", "injected recovery interruption")))
    with pytest.raises(StateBackupError) as interrupted:
        restore_full_backup(home, workspace, backed["archive_path"], backed["manifest_path"], confirm=True)
    assert interrupted.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"

    monkeypatch.setattr(state_backup, "_rollback_full_restore", original_rollback)
    recovered = recover_full_restore(home, confirm=True)
    assert recovered["success"] is True
    assert recovered["daemon_started"] is False
    assert not (home / ACTIVE_FULL_RESTORE_JOURNAL).exists()
    assert not (home / DATABASE_NAME).exists()
    assert not any(workspace.iterdir())


def _full_backup_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_home = tmp_path / "source-control"
    source_workspaces = tmp_path / "source-workspaces"
    _seed_full_state(source_home, source_workspaces)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    receipt = create_full_backup(source_home, backup_dir / "full-state.tar")
    return Path(receipt["archive_path"]), Path(receipt["manifest_path"])


def test_full_restore_directory_publication_race_preserves_foreign_empty_projects_directory(tmp_path, monkeypatch):
    import os
    import comsol_mcp._state_backup as state_backup

    archive, manifest = _full_backup_fixture(tmp_path)
    home = tmp_path / "target-control"
    workspace = tmp_path / "target-workspace"
    home.mkdir()
    workspace.mkdir()
    real_rename = state_backup._renameat_directory_noreplace
    foreign = {}

    def race_create_destination(source_fd, source_leaf, destination_fd, destination_leaf):
        if destination_leaf == "projects" and not foreign:
            os.mkdir(destination_leaf, 0o700, dir_fd=destination_fd)
            info = os.stat(destination_leaf, dir_fd=destination_fd, follow_symlinks=False)
            foreign["identity"] = (int(info.st_dev), int(info.st_ino))
        return real_rename(source_fd, source_leaf, destination_fd, destination_leaf)

    monkeypatch.setattr(state_backup, "_renameat_directory_noreplace", race_create_destination)
    with pytest.raises(StateBackupError) as refused:
        restore_full_backup(home, workspace, archive, manifest, confirm=True)
    assert refused.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    current = (workspace / "projects").stat(follow_symlinks=False)
    assert (int(current.st_dev), int(current.st_ino)) == foreign["identity"]
    assert list((workspace / "projects").iterdir()) == []
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"


def test_full_restore_rejects_stage_parent_symlink_substitution_and_preserves_outside(tmp_path, monkeypatch):
    import comsol_mcp._state_backup as state_backup

    archive, manifest = _full_backup_fixture(tmp_path)
    home = tmp_path / "target-control"
    workspace = tmp_path / "target-workspace"
    outside = tmp_path / "outside"
    home.mkdir()
    workspace.mkdir()
    outside.mkdir()
    sentinel = outside / "model.mph"
    sentinel.write_bytes(b"outside must remain unchanged")
    original_writer = state_backup._write_tar_member_exclusive
    injected = {}

    def substitute_parent(archive_obj, info, target, **kwargs):
        if target.is_relative_to(workspace) and target.suffix == ".mph" and not injected:
            parent = target.parent
            parked = parent.with_name(parent.name + ".held-by-test")
            parent.rename(parked)
            parent.symlink_to(outside, target_is_directory=True)
            injected["link"] = parent
        return original_writer(archive_obj, info, target, **kwargs)

    monkeypatch.setattr(state_backup, "_write_tar_member_exclusive", substitute_parent)
    with pytest.raises(StateBackupError) as refused:
        restore_full_backup(home, workspace, archive, manifest, confirm=True)
    assert refused.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    assert injected["link"].is_symlink()
    assert sentinel.read_bytes() == b"outside must remain unchanged"
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"


def test_full_restore_rollback_preserves_same_hash_foreign_project_replacement(tmp_path, monkeypatch):
    import shutil
    import comsol_mcp._state_backup as state_backup

    archive, manifest = _full_backup_fixture(tmp_path)
    home = tmp_path / "target-control"
    workspace = tmp_path / "target-workspace"
    home.mkdir()
    workspace.mkdir()
    foreign = {}
    original_inventory = state_backup._verify_exact_tree

    def fail_after_projects_install(*_args, **_kwargs):
        raise RuntimeError("inject post-install failure to enter rollback")

    def replace_after_inventory(root, expected):
        result = original_inventory(root, expected)
        projects = workspace / "projects"
        if root == projects and not foreign:
            parked = workspace / "projects-owned-recovery-test"
            projects.rename(parked)
            shutil.copytree(parked, projects)
            old_identity = state_backup._directory_identity(parked)
            new_identity = state_backup._directory_identity(projects)
            assert old_identity != new_identity
            assert state_backup._tree_file_inventory(parked) == state_backup._tree_file_inventory(projects)
            foreign.update(parked=parked, projects=projects, identity=new_identity)
        return result

    monkeypatch.setattr(state_backup, "_validate_rebound_database", fail_after_projects_install)
    monkeypatch.setattr(state_backup, "_verify_exact_tree", replace_after_inventory)
    with pytest.raises(StateBackupError) as refused:
        restore_full_backup(home, workspace, archive, manifest, confirm=True)
    assert refused.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    assert state_backup._directory_identity(foreign["projects"]) == foreign["identity"]
    assert state_backup._tree_file_inventory(foreign["projects"]) == state_backup._tree_file_inventory(foreign["parked"])
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"


def test_full_restore_quarantine_cleanup_pins_workspace_after_root_path_swap(tmp_path, monkeypatch):
    import comsol_mcp._state_backup as state_backup

    archive, manifest = _full_backup_fixture(tmp_path)
    home = tmp_path / "target-control"
    workspace = tmp_path / "target-workspace"
    parked = tmp_path / "owned-workspace-parked"
    outside = tmp_path / "outside"
    home.mkdir()
    workspace.mkdir()
    outside.mkdir()
    sentinel = outside / "foreign.mph"
    sentinel.write_bytes(b"preserve the outside tree")

    def fail_after_projects_install(*_args, **_kwargs):
        raise RuntimeError("inject post-install failure to enter rollback")

    injected = {}

    def swap_workspace_before_rmdir(parent_fd, quarantine_leaf):
        if injected:
            return
        # The descriptor is already pinned to this workspace directory. Move
        # its pathname away and replace the old pathname with an outside link.
        workspace.rename(parked)
        workspace.symlink_to(outside, target_is_directory=True)
        injected["done"] = quarantine_leaf

    monkeypatch.setattr(state_backup, "_validate_rebound_database", fail_after_projects_install)
    monkeypatch.setattr(state_backup, "_before_restore_quarantine_rmdir", swap_workspace_before_rmdir)
    with pytest.raises(StateBackupError) as refused:
        restore_full_backup(home, workspace, archive, manifest, confirm=True)
    assert refused.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    assert injected["done"].startswith(".comsol-restore-cleanup-")
    assert workspace.is_symlink()
    assert sentinel.read_bytes() == b"preserve the outside tree"
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"


def test_full_restore_quarantine_cleanup_preserves_same_content_foreign_leaf(tmp_path, monkeypatch):
    import shutil
    import comsol_mcp._state_backup as state_backup

    archive, manifest = _full_backup_fixture(tmp_path)
    home = tmp_path / "target-control"
    workspace = tmp_path / "target-workspace"
    home.mkdir()
    workspace.mkdir()
    template = tmp_path / "foreign-projects-template"
    foreign_copy = tmp_path / "foreign-projects-copy"
    parked_owned = tmp_path / "owned-projects-parked"
    injected = {}

    def fail_after_projects_install(*_args, **_kwargs):
        raise RuntimeError("inject post-install failure to enter rollback")

    def preserve_template(root, relative, quarantine_leaf):
        if relative == "projects" and not template.exists():
            shutil.copytree(root / quarantine_leaf, template)

    def replace_quarantine_with_same_content(parent_fd, quarantine_leaf):
        if injected or not template.exists():
            return
        quarantine = workspace / quarantine_leaf
        quarantine.rename(parked_owned)
        shutil.copytree(template, quarantine)
        shutil.copytree(template, foreign_copy)
        injected.update(leaf=quarantine_leaf, identity=state_backup._directory_identity(quarantine))

    monkeypatch.setattr(state_backup, "_validate_rebound_database", fail_after_projects_install)
    monkeypatch.setattr(state_backup, "_before_restore_quarantine_delete", preserve_template)
    monkeypatch.setattr(state_backup, "_before_restore_quarantine_rmdir", replace_quarantine_with_same_content)
    with pytest.raises(StateBackupError) as refused:
        restore_full_backup(home, workspace, archive, manifest, confirm=True)
    assert refused.value.code == "RESTORE_ROLLBACK_INCOMPLETE"
    foreign_leaf = workspace / injected["leaf"]
    assert foreign_leaf.is_dir() and not foreign_leaf.is_symlink()
    assert state_backup._directory_identity(foreign_leaf) == injected["identity"]
    assert state_backup._tree_file_inventory(foreign_leaf) == state_backup._tree_file_inventory(foreign_copy)
    assert (home / ACTIVE_FULL_RESTORE_JOURNAL).is_file()
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "RESTORE_RECOVERY_REQUIRED"


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
