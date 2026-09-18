from __future__ import annotations

import os
from pathlib import Path
import zipfile

import pytest

from comsol_mcp._atomic_save import AtomicSaveError, atomic_save


def write_mph(path: Path, payload: bytes = b"complete model") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/model.mphbin", payload)


def test_atomic_save_replaces_only_after_complete_zip_verification(tmp_path: Path):
    destination = tmp_path / "checkpoints" / "model.mph"
    destination.parent.mkdir()
    write_mph(destination, b"old")

    result = atomic_save(destination, lambda temp: write_mph(temp, b"new"), tmp_path)

    assert result["path"] == str(destination.resolve())
    assert result["size"] == destination.stat().st_size
    assert len(result["sha256"]) == 64
    assert result["checkpoint"]["verified"] is True
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("model/model.mphbin") == b"new"
    assert not list(destination.parent.glob(".model.mph.*.tmp.mph"))


def test_valid_mph_zip_may_contain_an_empty_optional_member(tmp_path: Path):
    destination = tmp_path / "model.mph"
    def callback(temp):
        with zipfile.ZipFile(temp, "w") as archive:
            archive.writestr("model/model.mphbin", b"complete")
            archive.writestr("optional-empty-member", b"")
    result = atomic_save(destination, callback, tmp_path)
    assert result["checkpoint"]["verified"] is True


def test_callback_failure_preserves_existing_checkpoint_and_keeps_partial_evidence(tmp_path: Path):
    destination = tmp_path / "model.mph"
    write_mph(destination, b"old")

    def broken(temp: Path):
        temp.write_bytes(b"incomplete")
        raise RuntimeError("disk full")

    with pytest.raises(AtomicSaveError) as raised:
        atomic_save(destination, broken, tmp_path)
    error = raised.value
    assert error.stage == "callback" and error.original_preserved and error.partial
    assert error.temporary_path and error.temporary_path.read_bytes() == b"incomplete"
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("model/model.mphbin") == b"old"


def test_verification_and_publish_failures_do_not_replace_existing_checkpoint(tmp_path: Path, monkeypatch):
    destination = tmp_path / "model.mph"
    write_mph(destination, b"old")
    with pytest.raises(AtomicSaveError) as invalid:
        atomic_save(destination, lambda temp: temp.write_bytes(b"not a zip"), tmp_path)
    assert invalid.value.stage == "verification" and invalid.value.original_preserved
    assert invalid.value.temporary_path and invalid.value.temporary_path.exists()

    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("rename denied")))
    with pytest.raises(AtomicSaveError) as publish:
        atomic_save(destination, lambda temp: write_mph(temp, b"new"), tmp_path)
    assert publish.value.stage == "publish" and publish.value.original_preserved
    assert publish.value.temporary_path and publish.value.temporary_path.exists()
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("model/model.mphbin") == b"old"


def test_path_escape_and_overwrite_refusal_do_not_create_candidate(tmp_path: Path):
    outside = tmp_path.parent / "outside.mph"
    with pytest.raises(Exception):
        atomic_save(outside, lambda temp: write_mph(temp), tmp_path)
    destination = tmp_path / "model.mph"
    write_mph(destination)
    with pytest.raises(AtomicSaveError, match="overwrite") as raised:
        atomic_save(destination, lambda temp: write_mph(temp, b"new"), tmp_path, overwrite=False)
    assert raised.value.temporary_path is None


def test_no_clobber_publish_refuses_a_destination_created_after_preflight(tmp_path: Path, monkeypatch):
    destination = tmp_path / "model.mph"
    real_link = os.link
    def race(temp, target):
        write_mph(destination, b"racing writer")
        return real_link(temp, target)
    monkeypatch.setattr(os, "link", race)
    with pytest.raises(AtomicSaveError) as raised:
        atomic_save(destination, lambda temp: write_mph(temp, b"candidate"), tmp_path, overwrite=False)
    assert raised.value.stage == "publish" and raised.value.original_preserved is True
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("model/model.mphbin") == b"racing writer"


def test_symlink_parent_escape_is_rejected_before_callback(tmp_path: Path):
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    called = []
    with pytest.raises(Exception):
        atomic_save("escape/model.mph", lambda temp: called.append(temp), tmp_path)
    assert called == []
