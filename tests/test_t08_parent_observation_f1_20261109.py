"""T08 F-1 targeted regressions: one real lstat drives the parent check.

The independent reviewer's finding F-1: in the remediation candidate
``atomic_save`` first observed the parent with ``_observed_presence(parent)``
(one ``os.lstat``) and then re-``lstat``ed the parent for the directory-type
check.  If that second observation raised (injected EACCES, or the parent
removed between the two calls), the error claimed "Destination directory is
not a directory." — a physical fact asserted from a refused/failed
observation, violating the module's own three-state standard.  Fail-closed
behaviour was unaffected; the defect is message-level honesty.

The F-1 fix drives all three parent branches (confirmed absent / observation
refused / observed present but not a directory) from a single actual
``os.lstat(parent)``, with the three existing messages unchanged.

These tests pin the fixed contract explicitly:
- the reviewer's two counterexamples (EACCES at the second parent
  observation; parent vanishing between observations): under the fix no
  second parent observation can exist (count pin) and no message may claim
  a not-directory physical fact that no observation supported;
- positive control: a legitimate directory parent publishes normally with
  exactly one path-typed parent lstat;
- negative control: a real regular-file parent is still honestly claimed
  "is not a directory" (the claim is backed by the returned observation);
- the three single-observation branches keep their exact messages and stay
  fail-closed: no candidate file is created and nothing is published.

Fault injections are Python-level ``os.lstat`` injections (labelled as
such, not native OS races), mirroring the reviewer's characterization
technique.  Absence/refusal branches also run natively where the filesystem
can provide them.  No Worker, daemon listener, COMSOL, model or SSH is
touched.  The in-process identity guard pins all four product modules
(``__file__`` + SHA) under the expected tree BEFORE and AFTER (T08F_*
environment pins).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _atomic_save as atomic
from comsol_mcp import _control_daemon as daemon
from comsol_mcp import _g2_engine as engine
from comsol_mcp import _java_worker as worker

GUARDED_MODULES = (
    (atomic, "T08F_ATOMIC_SHA"),
    (worker, "T08F_WORKER_SHA"),
    (engine, "T08F_ENGINE_SHA"),
    (daemon, "T08F_DAEMON_SHA"),
)


@pytest.fixture(scope="module", autouse=True)
def _bind_source_identity():
    default_root = str(Path(__file__).resolve().parents[1])
    expected_root = Path(os.environ.get("T08F_EXPECT_ROOT", default_root)).resolve()
    expected = {}
    for module, env_name in GUARDED_MODULES:
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        assert path.parent == expected_root / "comsol_mcp", path
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert os.environ.get(env_name, sha) == sha, path
        expected[module] = sha
        print(f"T08F_IDENTITY BEFORE {module.__name__} {sha} {path}")
    yield
    for module, sha in expected.items():
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"T08F_IDENTITY AFTER {module.__name__} {observed} {path}")
        assert observed == sha


def _write_mph(path: Path, body: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/synthetic.bin", body)


def _read_mph(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("model/synthetic.bin")


def _sentinel_callback(candidate: Path) -> None:
    # Stops the run deterministically after preflight; never writes a
    # candidate, so any failure branch can assert no-create/no-publish.
    raise RuntimeError("F1 sentinel: stop after preflight; never write a candidate")


def _instrument_parent_lstats(monkeypatch, parent: Path, *, fail_from_call: int | None = None,
                              error: OSError | None = None):
    """Count/patch ``os.lstat`` calls targeting ``parent`` with a Path-typed
    argument (path-resolution internals pass str components and are not
    this publisher's observation of the parent).  When ``fail_from_call``
    is set, the N-th (and later) Path-typed targeted call raises ``error``
    instead of observing — the reviewer's Python-level fault injection."""
    stats = {"path_typed_calls": 0, "injected_raises": 0}
    real_lstat = os.lstat

    def instrumented_lstat(path, *args, **kwargs):
        if isinstance(path, Path) and path == parent:
            stats["path_typed_calls"] += 1
            if fail_from_call is not None and stats["path_typed_calls"] >= fail_from_call:
                stats["injected_raises"] += 1
                raise error
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", instrumented_lstat)
    return stats


# --- positive control: legitimate directory parent -------------------------

def test_legitimate_directory_parent_publishes_from_single_parent_observation(tmp_path, monkeypatch):
    """Normal publication must keep working, and the fixed preflight may
    observe the parent with the filesystem exactly once (path-typed)."""
    target = tmp_path / "project.mph"
    stats = _instrument_parent_lstats(monkeypatch, tmp_path)

    result = atomic.atomic_save(target, lambda candidate: _write_mph(candidate, b"published candidate"),
                                tmp_path, overwrite=True)

    assert stats["path_typed_calls"] == 1, stats
    assert result["checkpoint"]["verified"] is True
    assert _read_mph(target) == b"published candidate"
    assert not any(p.name.startswith(".project.mph.") for p in tmp_path.iterdir())


# --- negative control: real regular-file parent, honest physical claim -----

def test_regular_file_parent_is_honestly_claimed_not_a_directory(tmp_path):
    """A parent observed (successfully, via the real filesystem) to be a
    regular file may still be reported as 'not a directory'; this honest
    branch must be unchanged by the fix."""
    parent_file = tmp_path / "regular-file-parent"
    parent_file.write_bytes(b"not a directory")
    target = parent_file / "model.mph"

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, _sentinel_callback, tmp_path, overwrite=True)
    error = caught.value
    assert str(error) == "Destination directory is not a directory.", str(error)
    assert error.stage == "preflight"
    assert error.publication_status == "not_attempted"
    assert error.original_preserved is False
    assert error.temporary_retained is False
    # Fail-closed: the regular file is untouched; no candidate, no publish.
    assert parent_file.read_bytes() == b"not a directory"


# --- reviewer counterexample 1: second parent observation refused ----------

def test_reviewer_counterexample_eacces_second_parent_lstat_is_never_a_directory_claim(tmp_path, monkeypatch):
    """Injected EACCES at the position of the old second parent lstat: the
    fixed publisher observes the parent once, so the injected refusal can
    never fire and no 'is not a directory' claim is produced; the run is
    stopped by the sentinel callback before any candidate exists."""
    target = tmp_path / "project.mph"
    stats = _instrument_parent_lstats(
        monkeypatch, tmp_path, fail_from_call=2,
        error=PermissionError(13, "F1 injected observation refusal"))

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, _sentinel_callback, tmp_path, overwrite=True)
    error = caught.value
    # F-1 core: no physical not-directory claim from a refused observation.
    assert "is not a directory" not in str(error), str(error)
    # Target branch: a single Path-typed parent observation drove preflight;
    # the injected second-observation fault never fired (window is gone).
    assert stats["path_typed_calls"] == 1, stats
    assert stats["injected_raises"] == 0, stats
    assert error.stage == "callback", error.as_dict()
    assert str(error).startswith("Candidate save callback failed"), str(error)
    # Fail-closed control: no candidate, no publish.
    assert error.publication_status == "not_attempted"
    assert not target.exists()
    assert not any(p.name.startswith(".project.mph.") for p in tmp_path.iterdir())


# --- reviewer counterexample 2: parent vanishes between observations -------

def test_reviewer_counterexample_parent_vanishing_second_lstat_is_never_a_directory_claim(tmp_path, monkeypatch):
    """Injected ENOENT at the position of the old second parent lstat (the
    parent "disappears between the two calls"): the fixed single-observation
    preflight never re-observes, so the old dishonest 'is not a directory'
    message cannot appear."""
    target = tmp_path / "project.mph"
    stats = _instrument_parent_lstats(
        monkeypatch, tmp_path, fail_from_call=2,
        error=FileNotFoundError(2, "F1 injected parent disappearance"))

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, _sentinel_callback, tmp_path, overwrite=True)
    error = caught.value
    assert "is not a directory" not in str(error), str(error)
    assert stats["path_typed_calls"] == 1, stats
    assert stats["injected_raises"] == 0, stats
    assert error.stage == "callback", error.as_dict()
    assert error.publication_status == "not_attempted"
    assert not target.exists()
    assert not any(p.name.startswith(".project.mph.") for p in tmp_path.iterdir())


# --- three single-observation branches keep exact honest messages ----------

def test_refused_single_parent_observation_refuses_without_physical_claim(tmp_path, monkeypatch):
    """When the one parent observation itself is refused (injected EACCES),
    the message must be the refused-observation refusal — never a physical
    absence or not-directory fact — and the run must stay fail-closed."""
    target = tmp_path / "project.mph"
    stats = _instrument_parent_lstats(
        monkeypatch, tmp_path, fail_from_call=1,
        error=PermissionError(13, "F1 injected observation refusal"))

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, _sentinel_callback, tmp_path, overwrite=True)
    error = caught.value
    assert str(error) == ("Destination directory could not be observed; "
                          "refusing without treating it as present or absent."), str(error)
    assert stats["injected_raises"] == 1, stats  # the refused branch really executed
    assert error.stage == "preflight"
    assert error.publication_status == "not_attempted"
    assert error.original_preserved is False
    # Fail-closed: the injected refusal created nothing and published nothing.
    assert not target.exists()
    assert not any(p.name.startswith(".project.mph.") for p in tmp_path.iterdir())


def test_confirmed_absent_parent_still_reports_does_not_exist(tmp_path):
    """Native ENOENT (no injection): confirmed absence keeps its exact
    message and stays fail-closed."""
    target = tmp_path / "absent-dir" / "model.mph"

    with pytest.raises(atomic.AtomicSaveError) as caught:
        atomic.atomic_save(target, _sentinel_callback, tmp_path, overwrite=True)
    error = caught.value
    assert str(error) == "Destination directory does not exist.", str(error)
    assert error.stage == "preflight"
    assert error.publication_status == "not_attempted"
    assert not target.exists()
