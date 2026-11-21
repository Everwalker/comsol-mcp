"""T08 publication-status controls for the atomic save/export siblings.

Task-owned negative/positive controls (2026-11-09).  Synthetic fault models
only: no Worker, daemon, COMSOL, existing model, or external path is touched.
A publish syscall that raises after performing its effect (lost return) is a
deliberate fault model, not a claim about normal OS behavior.  Run against
the pinned baseline these expose the dishonest reported outcomes; run against
the isolated candidate they pin the corrected contract.
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
    expected_root = Path(os.environ.get("T08C_EXPECT_ROOT", default_root)).resolve()
    expected = {}
    for module, env_name in ((atomic, "T08C_ATOMIC_SHA"), (worker, "T08C_WORKER_SHA")):
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        assert path.parent == expected_root / "comsol_mcp", path
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert os.environ.get(env_name, sha) == sha, path
        expected[module] = sha
        print(f"T08C_IDENTITY BEFORE {module.__name__} {sha} {path}")
    yield
    for module, sha in expected.items():
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"T08C_IDENTITY AFTER {module.__name__} {observed} {path}")
        assert observed == sha


def _write_mph(path: Path, body: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/synthetic.bin", body)


def _read_mph(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("model/synthetic.bin")


def test_healthy_overwrite_publishes_and_leaves_no_temporary(tmp_path):
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")
    result = atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"new synthetic checkpoint"),
                                tmp_path, overwrite=True)
    assert result["checkpoint"]["verified"] is True
    assert _read_mph(target) == b"new synthetic checkpoint"
    assert not list(tmp_path.glob(".*.tmp.mph"))


def test_callback_failure_is_not_attempted_and_provably_preserves_prior(tmp_path):
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")

    def fail(candidate):
        _write_mph(candidate, b"partial synthetic candidate")
        raise OSError("synthetic callback failure")

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, fail, tmp_path, overwrite=True)
    error = caught.value
    assert error.stage == "callback"
    assert getattr(error, "publication_status", "not_attempted") == "not_attempted"
    assert getattr(error, "original_preservation", "preserved") == "preserved"
    assert error.original_preserved is True
    assert getattr(error, "temporary_retained", True) is True
    assert _read_mph(target) == b"old synthetic checkpoint"


def test_replace_then_parent_sync_reports_published_not_old_preserved(tmp_path, monkeypatch):
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
    error = caught.value
    assert reached == [True] and _read_mph(target) == b"new synthetic checkpoint"
    assert error.stage == "publish"
    assert error.publication_status == "published", error.as_dict()
    # Contract sync (T08 remediation, D2): a returned os.replace proves
    # publication, not that a prior destination existed at the publish
    # instant.  The old preflight-inferred label is replaced by the honest
    # general label; the prior-state observation above stays as the log.
    assert error.original_preservation == "replace_published_prior_state_unverified", error.as_dict()
    assert error.original_preserved is False, error.as_dict()
    # The replace consumed the temporary; honesty includes saying it is gone.
    assert error.temporary_retained is False, error.as_dict()


def test_replace_lost_acknowledgement_reports_unknown(tmp_path, monkeypatch):
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
    error = caught.value
    assert reached == [True] and _read_mph(target) == b"new synthetic checkpoint"
    assert error.stage == "publish"
    assert error.publication_status == "unknown", error.as_dict()
    assert error.original_preservation == "unverified", error.as_dict()
    # False means preservation could not be confirmed here, not proven loss.
    assert error.original_preserved is False, error.as_dict()
    assert error.temporary_retained is False, error.as_dict()


def test_no_clobber_link_succeeded_but_unlink_failed(tmp_path, monkeypatch):
    target = tmp_path / "new.mph"
    real_unlink = Path.unlink

    def unlink_then_raise(self, *args, **kwargs):
        if str(self).endswith(".tmp.mph"):
            raise OSError("synthetic unlink failure after link")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_then_raise)
    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"new synthetic checkpoint"),
                           tmp_path, overwrite=False)
    error = caught.value
    assert _read_mph(target) == b"new synthetic checkpoint"
    assert error.stage == "publish"
    assert error.publication_status == "published", error.as_dict()
    # Contract sync (T08 remediation, D2): no-clobber link success proves
    # the name was absent at the publish instant only; it is not a
    # lifetime 'created' claim.  The destination-bytes observation above
    # remains the positive log of what the publisher actually did.
    assert error.original_preservation == "no_destination_at_publish_instant", error.as_dict()
    assert error.original_preserved is False, error.as_dict()
    # The retained temporary is evidence; it must be recorded as still present.
    assert error.temporary_retained is True, error.as_dict()
    assert Path(error.temporary_path).is_file()


def test_export_post_publish_sync_failure_reports_published(tmp_path, monkeypatch):
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
    message = str(caught.value)
    assert len(reached) == 2 and target.read_text() == "new synthetic source"
    assert "was not published" not in message, message
    assert "was published" in message, message
    assert getattr(caught.value, "publication_status", None) == "published", message


def test_export_publish_call_failure_reports_unknown(tmp_path, monkeypatch):
    target = tmp_path / "Example.java"
    target.write_text("old synthetic source", encoding="utf-8")

    def refusing_replace(source, destination):
        raise OSError("synthetic publish refusal before any effect")

    with monkeypatch.context() as patch:
        patch.setattr(worker.os, "replace", refusing_replace)
        with pytest.raises(worker.JavaWorkerError) as caught:
            worker._atomic_export_file(target, lambda candidate: candidate.write_text("new synthetic source"),
                                       tmp_path, overwrite=True)
    message = str(caught.value)
    # No-effect stub: observed reality (control), not the library's claim.
    assert target.read_text() == "old synthetic source"
    assert "publication outcome is unknown" in message, message
    assert "was not published" not in message, message
    assert getattr(caught.value, "publication_status", None) == "unknown", message
    assert getattr(caught.value, "temporary_retained", None) is True, message
