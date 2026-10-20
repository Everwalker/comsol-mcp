"""W24 R10 permission regression controls; all execution stops before native birth."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

import pytest

campaign = importlib.import_module("tools.run_native_w24_static_shape_setup_campaign")


def test_prepared_inputs_are_readonly_and_evidence_parent_stays_owner_private(tmp_path):
    evidence = tmp_path / "candidate-evidence"
    classes = evidence / "offline_compile" / "classes"
    classes.mkdir(parents=True)
    (evidence / "candidate_freeze.json").write_text("{}", encoding="utf-8")
    (evidence / "offline_compile.json").write_text("{}", encoding="utf-8")
    (classes / "Input.class").write_bytes(b"test class bytes")

    campaign._seal_prepared_candidate_inputs(evidence)

    assert stat.S_IMODE(evidence.stat().st_mode) == 0o700
    assert stat.S_IMODE((evidence / "offline_compile").stat().st_mode) == 0o700
    assert stat.S_IMODE(classes.stat().st_mode) == 0o700
    for path in (evidence / "candidate_freeze.json",
                 evidence / "offline_compile.json", classes / "Input.class"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        assert not path.stat().st_mode & 0o222

    run_dir = evidence / "native-setup-run"
    run_dir.mkdir(mode=0o700)
    assert run_dir.is_dir()


def test_preparation_refuses_symlinked_evidence_input(tmp_path):
    evidence = tmp_path / "candidate-evidence"
    evidence.mkdir(mode=0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (evidence / "aliased.json").symlink_to(outside)

    with pytest.raises(campaign.CandidateError, match="aliased or foreign-owned"):
        campaign._seal_prepared_candidate_inputs(evidence)


class _AuditSentinel(Exception):
    pass


def _execute_through_real_run_dir(monkeypatch, *, readonly_parent: bool):
    root = Path(os.path.realpath("/private/tmp")) / f"w24-r10-execute-control-{uuid.uuid4().hex}"
    root.mkdir(mode=0o700)
    evidence = root / "candidate-evidence"
    evidence.mkdir(mode=0o700)
    freeze = {
        "schema": campaign.FREEZE_SCHEMA,
        "status": "PREPARED_SETUP_ONLY_NOT_NATIVE",
        "execution_mode": campaign.EXECUTION_MODE_SETUP14,
        "candidate_sha256": "a" * 64,
        "source": {"checkout_kind": "git_archive_plus_explicit_overlay",
                   "source_closure_sha256": "b" * 64},
    }
    freeze_path = evidence / campaign.FREEZE_FILE
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    freeze_path.chmod(0o400)
    freeze_view = {**freeze}
    work = Path(campaign.WORK_PREFIX + uuid.uuid4().hex)
    audit_reached = []
    native_popen_calls = []

    monkeypatch.chdir(campaign.REPO)
    monkeypatch.setattr(campaign.sys, "platform", "darwin")
    monkeypatch.setattr(campaign, "configure_archive_python",
                        lambda *_args, **_kwargs: {"site_processing_disabled": True})
    monkeypatch.setattr(campaign, "verify_candidate",
                        lambda **_kwargs: freeze_view)
    monkeypatch.setattr(campaign, "_verify_frozen_runtime",
                        lambda *_args, **_kwargs: {"status": "SOFTWARE_FIXTURE"})
    monkeypatch.setattr(campaign, "_verify_execution_environment",
                        lambda *_args, **_kwargs: {"status": "SOFTWARE_FIXTURE"})
    monkeypatch.setattr(campaign.subprocess, "Popen",
                        lambda *args, **kwargs: native_popen_calls.append((args, kwargs)))

    def audit_sentinel(_repo):
        audit_reached.append(True)
        raise _AuditSentinel("stopped immediately before any server construction")

    monkeypatch.setattr(campaign, "audit_archive_imports", audit_sentinel)
    if readonly_parent:
        evidence.chmod(0o555)
    try:
        return root, evidence, work, audit_reached, native_popen_calls, freeze, \
            campaign.execute_candidate(repo=campaign.REPO, evidence=evidence,
                                       work=work, reviewed_sha256="a" * 64)
    except BaseException as exc:
        return root, evidence, work, audit_reached, native_popen_calls, freeze, exc


def test_actual_execute_candidate_creates_native_run_dir_then_stops_at_audit(monkeypatch):
    root, evidence, work, audit, popen, _freeze, result = _execute_through_real_run_dir(
        monkeypatch, readonly_parent=False)
    try:
        assert isinstance(result, _AuditSentinel)
        assert audit == [True]
        run_dir = evidence / "native-setup-run"
        assert run_dir.is_dir()
        assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
        assert (run_dir / "events.jsonl").is_file()
        assert (evidence / "candidate_freeze.json").is_file()
        assert work.is_dir()
        assert popen == []
    finally:
        evidence.chmod(0o700)
        shutil.rmtree(root)
        if work.exists():
            shutil.rmtree(work)


def test_actual_execute_candidate_rejects_readonly_evidence_parent_before_audit(monkeypatch):
    root, evidence, work, audit, popen, _freeze, result = _execute_through_real_run_dir(
        monkeypatch, readonly_parent=True)
    try:
        assert isinstance(result, PermissionError)
        assert audit == []
        assert not (evidence / "native-setup-run").exists()
        assert work.is_dir()
        assert popen == []
    finally:
        evidence.chmod(0o700)
        shutil.rmtree(root)
        if work.exists():
            shutil.rmtree(work)
