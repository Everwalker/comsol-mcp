from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from comsol_mcp import _state_backup
from comsol_mcp._operation_store import OperationStore


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_manifest_publish_is_atomic_and_never_overwrites_a_competing_destination(tmp_path, monkeypatch):
    destination = tmp_path / "receipt.json"
    original_link = os.link

    def competing_link(source, target):
        assert Path(source).parent == Path(target).parent
        Path(target).write_bytes(b"competing writer")
        return original_link(source, target)

    monkeypatch.setattr(_state_backup.os, "link", competing_link)
    with pytest.raises(_state_backup.StateBackupError) as caught:
        _state_backup._publish_new_manifest_atomic(destination, {"winner": "ours"})

    assert caught.value.code == "DESTINATION_EXISTS"
    assert destination.read_bytes() == b"competing writer"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_backup_manifest_file_is_fsynced_before_atomic_publish(tmp_path, monkeypatch):
    destination = tmp_path / "receipt.json"
    events = []
    original_fsync = os.fsync
    original_link = os.link

    def observed_fsync(descriptor):
        events.append("fsync")
        return original_fsync(descriptor)

    def observed_link(source, target):
        assert events == ["fsync"], "manifest content must be flushed before publication"
        assert Path(source).parent == Path(target).parent
        events.append("link")
        return original_link(source, target)

    monkeypatch.setattr(_state_backup.os, "fsync", observed_fsync)
    monkeypatch.setattr(_state_backup.os, "link", observed_link)
    _state_backup._publish_new_manifest_atomic(destination, {"ready": True})

    assert events == ["fsync", "link", "fsync"]
    assert json.loads(destination.read_text(encoding="utf-8")) == {"ready": True}


def test_backup_manifest_publish_fails_closed_when_no_clobber_link_is_unavailable(tmp_path, monkeypatch):
    destination = tmp_path / "receipt.json"

    def unsupported_link(_source, _target):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(_state_backup.os, "link", unsupported_link)
    with pytest.raises(_state_backup.StateBackupError) as caught:
        _state_backup._publish_new_manifest_atomic(destination, {"winner": "ours"})

    assert caught.value.code == "ATOMIC_PUBLISH_FAILED"
    assert destination.exists() is False
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_published_manifest_reports_uncertain_directory_fsync_without_losing_artifact(tmp_path, monkeypatch):
    destination = tmp_path / "receipt.json"

    def failed_directory_fsync(_path):
        raise _state_backup.StateBackupError("DURABILITY_FAILED", "injected directory fsync failure")

    monkeypatch.setattr(_state_backup, "_fsync_directory", failed_directory_fsync)
    with pytest.raises(_state_backup.StateBackupError) as caught:
        _state_backup._publish_new_manifest_atomic(destination, {"complete": True})

    assert caught.value.code == "DURABILITY_FAILED"
    assert isinstance(caught.value, _state_backup._PublishedManifestError)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"complete": True}


def test_backup_manifest_binds_component_hashes_and_publication_policy(tmp_path, monkeypatch):
    home = tmp_path / "control"
    home.mkdir()
    store = OperationStore(home / _state_backup.DATABASE_NAME)
    store.close()
    monkeypatch.setattr(_state_backup, "_package_version", lambda: "0.1.9-test")

    output = tmp_path / "backups" / "ledger.sqlite3"
    output.parent.mkdir()
    result = _state_backup.create_backup(home, output)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    identity = manifest["build_identity"]

    assert identity["scope"] == "component_fingerprints_not_full_wheel_identity"
    assert identity["installation_version"] == "0.1.9-test"
    package_dir = Path(_state_backup.__file__).resolve().parent
    assert identity["components"]["comsol_mcp._state_backup.py"]["sha256"] == _digest(package_dir / "_state_backup.py")
    assert identity["components"]["comsol_mcp._operation_store.py"]["sha256"] == _digest(package_dir / "_operation_store.py")
    assert manifest["manifest_publication"] == {
        "method": "same_directory_hard_link_no_clobber",
        "file_fsync": "required_before_publish",
        "directory_fsync_policy": _state_backup._directory_fsync_policy(),
    }
    assert _state_backup._directory_fsync_policy("nt") == "skipped_windows_directory_fsync_unsupported_by_implementation"


def test_restore_journal_json_writer_retains_existing_behavior(tmp_path, monkeypatch):
    destination = tmp_path / "journal.json"

    def link_not_used(_source, _target):
        raise AssertionError("manifest-only publisher must not replace journal writer")

    monkeypatch.setattr(_state_backup.os, "link", link_not_used)
    _state_backup._write_new_json(destination, {"journal": "preserved"})
    assert json.loads(destination.read_text(encoding="utf-8")) == {"journal": "preserved"}


def test_backup_preserves_database_if_manifest_was_published_but_durability_is_unknown(tmp_path, monkeypatch):
    home = tmp_path / "control"
    home.mkdir()
    store = OperationStore(home / _state_backup.DATABASE_NAME)
    store.close()

    def failed_directory_fsync(_path):
        raise _state_backup.StateBackupError("DURABILITY_FAILED", "injected directory fsync failure")

    monkeypatch.setattr(_state_backup, "_fsync_directory", failed_directory_fsync)
    output = tmp_path / "backups" / "ledger.sqlite3"
    output.parent.mkdir()
    with pytest.raises(_state_backup.StateBackupError) as caught:
        _state_backup.create_backup(home, output)

    manifest_path = Path(str(output) + ".manifest.json")
    assert caught.value.code == "DURABILITY_FAILED"
    assert isinstance(caught.value, _state_backup._PublishedManifestError)
    assert output.is_file() and manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["database_sha256"] == _digest(output)
