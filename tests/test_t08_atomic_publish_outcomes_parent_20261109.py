"""T08 synthetic save/export post-publication error honesty regressions.

No Worker, daemon, COMSOL, existing model or external path is used.  A lost
return after a real replace is a fault model, not a claim about OS behavior.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _atomic_save as atomic
from comsol_mcp import _java_worker as worker


@pytest.fixture(scope="module", autouse=True)
def _bind_source_identity():
    default_root = str(Path(__file__).resolve().parents[1])
    expected_root = Path(os.environ.get("T08_EXPECT_ROOT", default_root)).resolve()
    expected = {}
    for module, env_name in ((atomic, "T08_ATOMIC_SHA"), (worker, "T08_WORKER_SHA")):
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        assert path.parent == expected_root / "comsol_mcp", path
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert os.environ.get(env_name, sha) == sha, path
        expected[module] = sha
        print(f"T08_IDENTITY BEFORE {module.__name__} {sha} {path}")
    yield
    for module, sha in expected.items():
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"T08_IDENTITY AFTER {module.__name__} {observed} {path}")
        assert observed == sha


def _write_mph(path: Path, body: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/synthetic.bin", body)


def _read_mph(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("model/synthetic.bin")


def test_mph_overwrite_then_parent_sync_error_does_not_claim_old_preserved(tmp_path, monkeypatch):
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")
    reached = []

    def fail_after_replace(path):
        assert path == target and _read_mph(target) == b"new synthetic checkpoint"
        reached.append(True)
        raise OSError("synthetic directory close failure after replace")

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", fail_after_replace)
        with pytest.raises(atomic.AtomicSaveError) as caught:
            atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"new synthetic checkpoint"),
                               tmp_path, overwrite=True)
    assert reached == [True] and _read_mph(target) == b"new synthetic checkpoint"
    assert caught.value.stage == "publish"
    assert caught.value.original_preserved is False, caught.value.as_dict()


def test_mph_replace_then_lost_acknowledgement_does_not_claim_old_preserved(tmp_path, monkeypatch):
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")
    real_replace = os.replace
    reached = []

    def replace_then_raise(source, destination):
        real_replace(source, destination)
        reached.append(True)
        raise OSError("synthetic post-replace lost acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(atomic.os, "replace", replace_then_raise)
        with pytest.raises(atomic.AtomicSaveError) as caught:
            atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"new synthetic checkpoint"),
                               tmp_path, overwrite=True)
    assert reached == [True] and _read_mph(target) == b"new synthetic checkpoint"
    assert caught.value.stage == "publish"
    assert caught.value.original_preserved is False, caught.value.as_dict()


def test_mph_no_clobber_publish_then_sync_error_does_not_claim_old_preserved(tmp_path, monkeypatch):
    target = tmp_path / "new.mph"
    reached = []

    def fail_after_link(path):
        assert path == target and _read_mph(target) == b"new synthetic checkpoint"
        reached.append(True)
        raise OSError("synthetic directory close failure after link")

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", fail_after_link)
        with pytest.raises(atomic.AtomicSaveError) as caught:
            atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"new synthetic checkpoint"),
                               tmp_path, overwrite=False)
    assert reached == [True] and _read_mph(target) == b"new synthetic checkpoint"
    assert caught.value.stage == "publish"
    assert caught.value.original_preserved is False, caught.value.as_dict()


def test_export_replace_then_sync_error_does_not_claim_unpublished(tmp_path, monkeypatch):
    target = tmp_path / "Example.java"
    target.write_text("old synthetic source", encoding="utf-8")
    reached = []

    def fail_on_post_publish_fsync(path):
        reached.append(path)
        if len(reached) == 1:
            return False  # temporary staging-directory sync, before publication
        assert path == target and target.read_text() == "new synthetic source"
        raise OSError("synthetic directory close failure after source replace")

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", fail_on_post_publish_fsync)
        with pytest.raises(worker.JavaWorkerError) as caught:
            worker._atomic_export_file(target, lambda candidate: candidate.write_text("new synthetic source"),
                                       tmp_path, overwrite=True)
    assert len(reached) == 2 and target.read_text() == "new synthetic source"
    assert "was not published" not in str(caught.value), str(caught.value)


def test_mph_callback_error_keeps_existing_checkpoint(tmp_path):
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")

    def fail_before_publish(candidate):
        _write_mph(candidate, b"partial synthetic candidate")
        raise OSError("synthetic save callback failure")

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, fail_before_publish, tmp_path, overwrite=True)
    assert caught.value.stage == "callback" and caught.value.original_preserved is True
    assert _read_mph(target) == b"old synthetic checkpoint"
