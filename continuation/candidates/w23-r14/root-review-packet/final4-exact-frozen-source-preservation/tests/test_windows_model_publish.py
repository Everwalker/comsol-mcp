"""Software state-machine controls for the Win32 registered-MPH publisher.

The injected filesystem adapter verifies transaction decisions only.  It is
not evidence for Windows kernel handle/rename behavior.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

import pytest

from comsol_mcp._artifact_store import _hash_regular_artifact
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._state_backup import StateBackupError, assert_no_pending_restore
from comsol_mcp._windows_model_publish import (
    MODEL_PUBLICATION_KEY_PREFIX,
    begin_registered_overwrite,
    recover_model_publication,
)


class _Handle:
    def __init__(self, path: Path):
        self.path = path
        self.closed = False


class _FilesystemAdapter:
    """POSIX test adapter for state-machine coverage, not Win32 semantics."""

    def open_existing_for_delete(self, parent: Path, leaf: str) -> _Handle:
        path = parent / leaf
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        return _Handle(path)

    @staticmethod
    def _stat(handle: _Handle):
        return handle.path.stat(follow_symlinks=False)

    def source_fingerprint(self, handle: _Handle):
        value = self._stat(handle)
        return ((int(value.st_dev), int(value.st_ino)), int(value.st_size), 0,
                int(value.st_mtime_ns), int(value.st_nlink))

    def readback(self, handle: _Handle):
        digest = hashlib.sha256()
        size = 0
        with handle.path.open("rb") as stream:
            for block in iter(lambda: stream.read(4096), b""):
                digest.update(block)
                size += len(block)
        return digest.hexdigest(), size

    def info(self, handle: _Handle):
        value = self._stat(handle)
        return (int(value.st_dev), int(value.st_ino)), int(value.st_size)

    def child_identity(self, parent: Path, leaf: str):
        path = parent / leaf
        value = path.stat(follow_symlinks=False)
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        return (int(value.st_dev), int(value.st_ino))

    def exists_child(self, parent: Path, leaf: str) -> bool:
        path = parent / leaf
        return path.exists() or path.is_symlink()

    def rename(self, handle: _Handle, parent: Path, leaf: str):
        current = self.child_identity(handle.path.parent, handle.path.name)
        expected = self.source_fingerprint(handle)[0]
        if current != expected:
            raise OSError("handle/path identity changed")
        target = parent / leaf
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        os.rename(handle.path, target)
        handle.path = target

    def verify_named_binding(self, binding):
        for path, identity in binding:
            current = path.stat(follow_symlinks=False)
            if path.is_symlink() or (int(current.st_dev), int(current.st_ino)) != identity:
                raise OSError("directory identity changed")

    def close(self, handle: _Handle):
        handle.closed = True

    def delete_owned(self, handle: _Handle):
        current = self.source_fingerprint(handle)[0]
        if self.child_identity(handle.path.parent, handle.path.name) != current:
            raise OSError("owned backup identity changed")
        handle.path.unlink()


@contextmanager
def _pin_parent(root, destination, *, add_file, _api):
    parts = tuple(destination.split("/"))
    parent = Path(root).joinpath(*parts[:-1])
    identity = parent.stat(follow_symlinks=False)
    if parent.is_symlink() or not parent.is_dir():
        raise OSError("unsafe parent")
    yield _api, parent, parts, [(parent, (int(identity.st_dev), int(identity.st_ino)))]


def _publisher(fault=None):
    def publish(root, destination, source, *, expected_sha256, expected_size):
        target = Path(root) / destination
        if fault == "foreign_collision":
            target.write_bytes(b"foreign race")
            raise OSError("destination raced into the visibility gap")
        if fault == "before_publish":
            raise OSError("injected publisher failure")
        temp = target.parent / ".test-new-publish"
        with source.open("rb") as inp, temp.open("xb") as out:
            while True:
                block = inp.read(4096)
                if not block:
                    break
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        digest, identity = _hash_regular_artifact(temp)
        assert digest == expected_sha256 and identity["size"] == expected_size
        os.link(temp, target)
        temp.unlink()
        digest, identity = _hash_regular_artifact(target)
        return {"sha256": digest, "size_bytes": identity["size"],
                "file_identity": {"device": identity["device"], "inode": identity["inode"]}}
    return publish


def _setup(tmp_path):
    root = tmp_path / "project"
    parent = root / "exports"
    parent.mkdir(parents=True)
    old = parent / "model.mph"
    old.write_bytes(b"old owned model bytes")
    source = root / "scratch.mph"
    source.write_bytes(b"new saved model bytes")
    home = tmp_path / "control"
    home.mkdir()
    operation_store = OperationStore(home / "operations.sqlite3")
    old_sha, old_identity = _hash_regular_artifact(old)
    new_sha, new_identity = _hash_regular_artifact(source)
    api = _FilesystemAdapter()
    common = dict(
        operation_store=operation_store, project_id="project-one",
        operation_id="op-save-1", request_id="req-save-1",
        model_ref={"project_id": "project-one", "session_id": "s1", "server_instance_id": "server1",
                   "worker_instance_id": "worker1", "worker_generation": 1, "model_tag": "m1"},
        source_revision=4, old_sha256=old_sha, old_identity=old_identity,
        old_artifact_relative_path="g2_artifacts/registered/old.mph",
        new_sha256=new_sha, new_size_bytes=new_identity["size"], source_identity=new_identity,
        _api=api, _pin_parent=_pin_parent,
    )
    return root, home, operation_store, old, source, api, common


def test_windows_publication_success_reports_two_step_visibility_and_commits(tmp_path):
    root, _home, store, destination, source, api, common = _setup(tmp_path)
    tx = begin_registered_overwrite(root, "exports/model.mph", source,
                                    _publisher=_publisher(), **common)
    assert destination.read_bytes() == b"new saved model bytes"
    receipt = tx.commit()
    assert receipt["atomic_new_file_publication"] is True
    assert receipt["atomic_path_replacement"] is False
    assert receipt["visibility_gap_possible"] is True
    assert receipt["backup_removed"] is True
    assert destination.read_bytes() == b"new saved model bytes"
    journal = store.get_metadata("revisions", MODEL_PUBLICATION_KEY_PREFIX + receipt["journal_id"])
    assert journal["status"] == "COMMITTED"
    assert not list(destination.parent.glob(".model-overwrite-old-*.mph"))


def test_windows_publication_failure_restores_old_exactly(tmp_path):
    root, _home, store, destination, source, _api, common = _setup(tmp_path)
    with pytest.raises(ExecutionContractError) as exc:
        begin_registered_overwrite(root, "exports/model.mph", source,
                                   _publisher=_publisher("before_publish"), **common)
    assert exc.value.code == "OVERWRITE_ROLLED_BACK"
    assert destination.read_bytes() == b"old owned model bytes"
    rows = [row for row in store.list_metadata("revisions")
            if str(row.get("journal_id", "")).strip()]
    assert len(rows) == 1 and rows[0]["status"] == "RESTORED"


def test_foreign_leaf_race_is_preserved_and_startup_is_fenced(tmp_path):
    root, home, store, destination, source, _api, common = _setup(tmp_path)
    with pytest.raises(ExecutionContractError) as exc:
        begin_registered_overwrite(root, "exports/model.mph", source,
                                   _publisher=_publisher("foreign_collision"), **common)
    assert exc.value.code == "OVERWRITE_STATE_UNKNOWN"
    assert destination.read_bytes() == b"foreign race"
    recovery = list(destination.parent.glob(".model-overwrite-old-*.mph"))
    assert len(recovery) == 1 and recovery[0].read_bytes() == b"old owned model bytes"
    with pytest.raises(StateBackupError) as startup:
        assert_no_pending_restore(home)
    assert startup.value.code == "MODEL_PUBLICATION_RECOVERY_REQUIRED"


def test_pending_journal_explicit_recovery_restores_old_and_unfences_startup(tmp_path):
    root, home, store, destination, source, api, common = _setup(tmp_path)
    tx = begin_registered_overwrite(root, "exports/model.mph", source,
                                    _publisher=_publisher(), **common)
    journal_id = tx.journal["journal_id"]
    tx._close()  # Simulate process loss: journal and both names remain durable.
    with pytest.raises(StateBackupError):
        assert_no_pending_restore(home)
    recovered = recover_model_publication(store, journal_id, _api=api,
                                           _pin_parent=_pin_parent,
                                           _publisher=_publisher())
    assert recovered["status"] == "RESTORED"
    assert destination.read_bytes() == b"old owned model bytes"
    assert_no_pending_restore(home)


def test_recovery_refuses_a_foreign_destination_without_touching_it(tmp_path):
    root, home, store, destination, source, api, common = _setup(tmp_path)
    tx = begin_registered_overwrite(root, "exports/model.mph", source,
                                    _publisher=_publisher(), **common)
    journal_id = tx.journal["journal_id"]
    tx._close()
    destination.unlink()
    destination.write_bytes(b"foreign replacement")
    with pytest.raises(ExecutionContractError) as exc:
        recover_model_publication(store, journal_id, _api=api,
                                  _pin_parent=_pin_parent, _publisher=_publisher())
    assert exc.value.code == "MODEL_PUBLICATION_RECOVERY_BLOCKED"
    assert destination.read_bytes() == b"foreign replacement"
    with pytest.raises(StateBackupError) as fenced:
        assert_no_pending_restore(home)
    assert fenced.value.code == "MODEL_PUBLICATION_RECOVERY_REQUIRED"
