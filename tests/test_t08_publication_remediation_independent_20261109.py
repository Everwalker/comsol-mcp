"""T08 remediation regressions: three-state temporary evidence, honest
publish-instant labels, and real publication state in the checkpoint wrapper.

Synthetic fault models only: no Worker, daemon listener, process, COMSOL,
existing model, or external path is started or touched.  Permission-revoke
(chmod 000) races and post-syscall raises are deliberate fault models used
to pin the epistemic contract; they are not claims about normal OS behavior.
The in-process identity guard pins all four product modules under the
expected tree before and after the run (T08R_* environment pins).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import zipfile

import pytest

from comsol_mcp import _atomic_save as atomic
from comsol_mcp import _control_daemon as daemon
from comsol_mcp import _g2_engine as engine
from comsol_mcp import _java_worker as worker
from comsol_mcp._execution_contract import ExecutionContractError


GUARDED_MODULES = (
    (atomic, "T08R_ATOMIC_SHA"),
    (worker, "T08R_WORKER_SHA"),
    (engine, "T08R_ENGINE_SHA"),
    (daemon, "T08R_DAEMON_SHA"),
)


@pytest.fixture(scope="module", autouse=True)
def _bind_source_identity():
    default_root = str(Path(__file__).resolve().parents[1])
    expected_root = Path(os.environ.get("T08R_EXPECT_ROOT", default_root)).resolve()
    expected = {}
    for module, env_name in GUARDED_MODULES:
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        assert path.parent == expected_root / "comsol_mcp", path
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert os.environ.get(env_name, sha) == sha, path
        expected[module] = sha
        print(f"T08R_IDENTITY BEFORE {module.__name__} {sha} {path}")
    yield
    for module, sha in expected.items():
        module_file = module.__file__
        assert module_file is not None
        path = Path(module_file).resolve()
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"T08R_IDENTITY AFTER {module.__name__} {observed} {path}")
        assert observed == sha


def _write_mph(path: Path, body: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/synthetic.bin", body)


def _read_mph(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("model/synthetic.bin")


# ---------------------------------------------------------------------------
# D1: three-state temporary-evidence observation (MPH + worker sibling)
# ---------------------------------------------------------------------------

def test_callback_stage_positive_and_negative_retention_controls(tmp_path):
    """Positive/negative controls: observed retention vs proven absence."""
    target = tmp_path / "project.mph"
    _write_mph(target, b"old synthetic checkpoint")

    def write_then_fail(candidate: Path) -> None:
        _write_mph(candidate, b"partial synthetic candidate")
        raise OSError("synthetic callback failure")

    with pytest.raises(atomic.AtomicSaveError) as positive:
        atomic.atomic_save(target, write_then_fail, tmp_path, overwrite=True)
    assert positive.value.temporary_retained is True
    assert positive.value.partial is True

    def fail_without_writing(candidate: Path) -> None:
        raise OSError("synthetic callback failure without candidate")

    with pytest.raises(atomic.AtomicSaveError) as negative:
        atomic.atomic_save(target, fail_without_writing, tmp_path, overwrite=True)
    # ENOENT is a successful observation of absence, so False is earned here.
    assert negative.value.temporary_retained is False
    assert negative.value.partial is False


def test_mph_temporary_retained_none_when_observation_refused(tmp_path):
    """A refused observation must be reported as None, never as absence."""
    target = tmp_path / "project.mph"

    def write_then_revoke(candidate: Path) -> None:
        _write_mph(candidate, b"staged synthetic candidate")
        os.chmod(tmp_path, 0o000)

    try:
        with pytest.raises(atomic.AtomicSaveError) as caught:
            atomic.atomic_save(target, write_then_revoke, tmp_path, overwrite=True)
    finally:
        os.chmod(tmp_path, 0o700)
    error = caught.value
    assert error.temporary_retained is None, error.as_dict()
    # Generation observably occurred before the revoke; partial and retention
    # are orthogonal and must not contradict each other in one as_dict().
    assert error.partial is True, error.as_dict()
    assert error.as_dict()["temporary_retained"] is None
    # Positive control: the staging evidence really is still there; the
    # library only refused to claim anything about it.
    assert Path(error.temporary_path).is_file()


def test_export_error_message_does_not_claim_absence_when_observation_refused(tmp_path):
    target = tmp_path / "Example.java"

    def write_then_revoke(candidate: Path) -> None:
        candidate.write_text("staged synthetic source", encoding="utf-8")
        os.chmod(tmp_path, 0o000)

    try:
        with pytest.raises(worker.JavaWorkerError) as caught:
            worker._atomic_export_file(target, write_then_revoke, tmp_path, overwrite=True)
    finally:
        os.chmod(tmp_path, 0o700)
    message = str(caught.value)
    assert getattr(caught.value, "temporary_retained", True) is None, message
    assert "no temporary evidence remained" not in message, message
    assert "unobserved" in message, message
    # Positive control: the staging directory and candidate bytes survive.
    staging = [p for p in tmp_path.iterdir() if p.name.startswith(".comsol-export-")]
    assert staging and (staging[0] / "Example.java").read_text() == "staged synthetic source"


# ---------------------------------------------------------------------------
# D2: publish-instant labels — replace cannot infer from preflight; link
#     proves only absence at the publish instant
# ---------------------------------------------------------------------------

def test_overwrite_publish_return_prior_state_unverified(tmp_path, monkeypatch):
    """Single-writer replace: the return proves publication, not the prior state."""
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
    assert reached == [True]
    assert error.publication_status == "published", error.as_dict()
    assert error.original_preservation == "replace_published_prior_state_unverified", error.as_dict()
    assert error.original_preserved is False, error.as_dict()
    assert error.temporary_retained is False, error.as_dict()
    assert _read_mph(target) == b"new synthetic checkpoint"


def test_overwrite_concurrent_creator_prior_state_unverified(tmp_path, monkeypatch):
    """Reviewer R4 fault model: destination created during the save window.

    The publisher's preflight saw no destination; an external writer landed
    one; os.replace clobbered it; a later step failed.  The label must not
    claim 'candidate_created'.
    """
    target = tmp_path / "project.mph"
    reached = []

    def racing_callback(candidate: Path) -> None:
        _write_mph(target, b"external racing writer")  # external writer lands
        _write_mph(candidate, b"new synthetic checkpoint")

    def fail_after_replace(path):
        assert path == target and _read_mph(target) == b"new synthetic checkpoint"
        reached.append(True)
        raise OSError("synthetic directory close failure after replace")

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", fail_after_replace)
        with pytest.raises(atomic.AtomicSaveError) as caught:
            atomic.atomic_save(target, racing_callback, tmp_path, overwrite=True)
    error = caught.value
    assert reached == [True]
    assert error.publication_status == "published", error.as_dict()
    assert error.original_preservation == "replace_published_prior_state_unverified", error.as_dict()
    assert error.original_preserved is False, error.as_dict()


def test_no_clobber_link_return_proves_only_publish_instant_absence(tmp_path, monkeypatch):
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
    assert error.publication_status == "published", error.as_dict()
    # Link success proves the name was absent at that instant; it is not a
    # lifetime 'created' claim.
    assert error.original_preservation == "no_destination_at_publish_instant", error.as_dict()
    assert error.temporary_retained is True, error.as_dict()


# ---------------------------------------------------------------------------
# Real create_checkpoint wrapper: published/unknown must not be relabelled
# ARTIFACT_MISSING; not_attempted keeps its code but keeps the cause.
# ---------------------------------------------------------------------------

class _FakeSavedModel:
    def __init__(self, body: bytes):
        self._body = body

    def save(self, path: str) -> None:
        # Real disposable zip written by the fake engine save callback.
        _write_mph(Path(path), self._body)


class _FakeClient:
    def __init__(self, model: _FakeSavedModel):
        self._model = model

    def model(self, tag: str):
        return self._model


class _FakeWorker:
    def __init__(self, model: _FakeSavedModel):
        self._client = _FakeClient(model)

    def client(self):
        return self._client


def _checkpoint_args(tmp_path, model: _FakeSavedModel):
    destination = tmp_path / "checkpoints" / "ckpt.mph"
    return (_FakeWorker(model), "tag-synthetic", destination, "synthetic-label")


def test_checkpoint_publish_return_then_post_step_failure_reports_publish_unknown(tmp_path, monkeypatch):
    reached = []

    def fail_after_link(path):
        # Prior-state log: publication really returned before this step failed.
        assert path.parent == tmp_path / "checkpoints"
        reached.append(True)
        raise OSError("synthetic directory close failure after checkpoint link")

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", fail_after_link)
        with pytest.raises(ExecutionContractError) as caught:
            engine.create_checkpoint(*_checkpoint_args(tmp_path, _FakeSavedModel(b"published checkpoint")))
    error = caught.value
    assert reached == [True]
    assert error.code == "ARTIFACT_PUBLISH_UNKNOWN", error.as_dict()
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert error.details["publication_status"] == "published", error.details
    assert error.details["destination_presence"] == "observed_present", error.details
    assert error.details["cause_type"] == "AtomicSaveError"
    assert isinstance(error.__cause__, atomic.AtomicSaveError)
    assert error.__cause__.publication_status == "published"


def test_checkpoint_publish_call_failure_reports_publish_unknown(tmp_path, monkeypatch):
    real_link = os.link

    def refusing_link(source, destination, **kwargs):
        # No-effect stub: the library must still answer 'unknown', which is
        # conservative, not a physical-state claim.
        raise OSError("synthetic publish refusal before any effect")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refusing_link)
        with pytest.raises(ExecutionContractError) as caught:
            engine.create_checkpoint(*_checkpoint_args(tmp_path, _FakeSavedModel(b"never published")))
    error = caught.value
    assert error.code == "ARTIFACT_PUBLISH_UNKNOWN", error.as_dict()
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert error.details["publication_status"] == "unknown", error.details
    assert error.details["cause_type"] == "AtomicSaveError"
    assert "synthetic publish refusal" in error.details["cause_message"]


def test_checkpoint_not_attempted_keeps_artifact_missing_with_cause(tmp_path):
    class _FailingModel(_FakeSavedModel):
        def save(self, path: str) -> None:
            raise RuntimeError("synthetic engine save failure")

    with pytest.raises(ExecutionContractError) as caught:
        engine.create_checkpoint(*_checkpoint_args(tmp_path, _FailingModel(b"unused")))
    error = caught.value
    # Positive control: the publish syscall never dispatched, so the
    # checkpoint record really is missing for the caller; the code stays.
    assert error.code == "ARTIFACT_MISSING"
    assert error.safe_retry is False
    assert error.details, error.as_dict()
    assert error.details["cause_type"] == "AtomicSaveError"
    assert error.details["publication_status"] == "not_attempted"
    assert error.details["temporary_retention_observed"] == "observed_absent"


def test_checkpoint_external_removal_after_publish_keeps_publication_fact(tmp_path, monkeypatch):
    def remove_destination_after_publish(path):
        # Fault model: an external actor deletes the just-published name
        # during the save window; the publisher itself never errors.  The
        # checkpoint record is then missing for the caller, so the code may
        # stay ARTIFACT_MISSING, but the honest wrapper must keep the
        # returned-publication fact and the observed absence in details
        # instead of discarding them.
        entries = sorted(p for p in path.parent.glob("ckpt-*.mph")) + sorted(path.parent.glob("ckpt.mph"))
        assert entries, "published checkpoint name expected before removal"
        for entry in entries:
            entry.unlink()
        return False

    with monkeypatch.context() as patch:
        patch.setattr(atomic, "_fsync_parent", remove_destination_after_publish)
        with pytest.raises(ExecutionContractError) as caught:
            engine.create_checkpoint(*_checkpoint_args(tmp_path, _FakeSavedModel(b"published then removed")))
    error = caught.value
    assert error.code == "ARTIFACT_MISSING", error.as_dict()
    assert error.details["publication_status"] == "published", error.details
    assert error.details["destination_presence"] == "observed_absent", error.details


def test_checkpoint_readback_observation_denied_is_not_missing(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"

    def revoke_after_publish(path):
        os.chmod(checkpoint_dir, 0o000)
        return False

    try:
        with monkeypatch.context() as patch:
            patch.setattr(atomic, "_fsync_parent", revoke_after_publish)
            with pytest.raises(ExecutionContractError) as caught:
                engine.create_checkpoint(*_checkpoint_args(tmp_path, _FakeSavedModel(b"published checkpoint")))
    finally:
        os.chmod(checkpoint_dir, 0o700)
    error = caught.value
    # The old code answered ARTIFACT_MISSING ("no complete file") here even
    # though the file was physically present; only the observation failed.
    assert error.code == "ARTIFACT_PUBLISH_UNKNOWN", error.as_dict()
    assert error.details["publication_status"] == "published"
    assert error.details["destination_presence"] == "observation_denied"
    # Positive control: the file really exists; the wrapper only declined
    # to claim anything, it never denied existence.
    assert any(p.is_file() and p.name.startswith("ckpt") and stat.S_ISREG(p.stat().st_mode)
               for p in checkpoint_dir.iterdir())


# ---------------------------------------------------------------------------
# ControlDaemon structured cause channel (durable stage evidence contract)
# ---------------------------------------------------------------------------

def test_publisher_errors_expose_daemon_cause_details(tmp_path, monkeypatch):
    target = tmp_path / "Example.java"

    def refusing_replace(source, destination):
        raise OSError("synthetic publish refusal before any effect")

    with monkeypatch.context() as patch:
        patch.setattr(worker.os, "replace", refusing_replace)
        with pytest.raises(worker.JavaWorkerError) as caught:
            worker._atomic_export_file(target, lambda candidate: candidate.write_text("new synthetic source"),
                                       tmp_path, overwrite=True)
    error = caught.value
    details = error.details
    assert details["publication_status"] == "unknown"
    assert details["temporary_retention_observed"] == "observed_retained"
    assert details["cause_type"] == "OSError"
    assert details["cause_stage"] == "publication"


def test_daemon_cause_projection_preserves_publication_fields(tmp_path, monkeypatch):
    projection = getattr(daemon, "_stage_failure_cause_details", None)
    assert projection is not None, "daemon exposes no durable cause projection for publication fields"

    real_link = os.link

    def refusing_link(source, destination, **kwargs):
        raise OSError("synthetic publish refusal before any effect")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refusing_link)
        with pytest.raises(ExecutionContractError) as caught:
            engine.create_checkpoint(*_checkpoint_args(tmp_path, _FakeSavedModel(b"never published")))
    projected = projection(caught.value)
    assert projected["publication_status"] == "unknown"
    assert projected["cause_type"] == "AtomicSaveError"
    assert set(projected["cause_message"]) and len(projected["cause_message"]) <= 300

    # Negative control: an exception without structured details still yields
    # an empty record — the projection never invents state.
    assert projection(RuntimeError("plain failure")) == {}
    control = ExecutionContractError("X", "carries no details")
    assert projection(control) == {}


def test_unknown_result_wires_cause_projection_static():
    """Static pin: the durable stage path uses the shared projection.

    End-to-end execution of ``_execute_admitted_experiment_stage`` needs the
    W21 stage-plan/Worker-dispatch-gate machinery (and would require fabric
    ing bound Worker events), which is outside this task's allowed surface;
    the call-site wiring is pinned against the real source text instead.
    """
    source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "details = _stage_failure_cause_details(exc)" in source
    assert '"temporary_retention_observed",' in source
    assert 'for key in ("cause_type", "cause_code", "cause_stage", "cause_message")' not in source


def test_docstring_scopes_proofs_to_this_publisher():
    doc = atomic.atomic_save.__doc__ or ""
    assert "this publisher" in doc
    assert "outside the proving" in doc
