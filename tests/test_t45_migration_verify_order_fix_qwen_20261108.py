"""T45 verify-order fixture correction evidence (Qwen subagent, 20261108).

Software-only: reuses the phase2b Harness (FixtureDesktopAdapter +
SoftwareWorker fakes). No Desktop, COMSOL, Worker process, Java, SSH or
native platform call is executed here.

These tests pin the REAL two-stage order of
``comsol_mcp._desktop_service._migrate``:

- verify #1 runs before ArtifactStore publish and the queued ``model.load``;
  when it refuses, the contract is bounded pre-admission: nothing published,
  nothing loaded, no attempt row is written at all, parent UNKNOWN, and a
  same-key replay performs no duplicated effect.  This test must NOT claim
  the save-copy preserved the source and must NOT pretend a target loaded.
- verify #2 runs only after the queued load admitted the PROVISIONAL
  attempt row (covered by phase2b's NON_READY and foreign-parent tests).
"""
from pathlib import Path

import pytest

from test_desktop_managed_load_phase2b import Harness


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.close()


def test_first_verify_loss_is_bounded_pre_admission(harness, monkeypatch):
    """preserve_source=False from the start: verify #1 refuses before publish."""
    harness.adapter.preserve_source = False
    save_calls = []
    original_save = harness.adapter.save_copy
    def save(identity, destination, source=None):
        save_calls.append(str(destination))
        return original_save(identity, destination, source)
    monkeypatch.setattr(harness.adapter, "save_copy", save)

    request = harness.request()
    destination = Path(request["arguments"]["save_policy"]["destination_path"])
    result = harness.migrate(request)
    assert not result["success"], result
    assert result["error"]["code"] == "SOURCE_MODEL_NOT_PRESERVED", result
    assert result["execution"]["status"] == "UNKNOWN"
    # Honest refusal shape: nothing in this result may claim the source
    # survived or that a target model exists.
    assert result["data"].get("source_window_preserved") is not True
    assert "target_model_ref" not in result["data"]

    # Bounded pre-admission contract, verified against the real store:
    assert not destination.exists()
    assert harness.snapshot()["copy_files"] == []
    assert harness.worker.trace == []  # no model.load, no Worker RPC at all
    assert harness.service.ledger._models == {}
    # The current product writes NO durable attempt for a pre-admission
    # failure (the synthetic pre-admission attempt plan was not authorized).
    assert list(harness.daemon._desktop_attempt_rows("p")) == []
    assert harness.daemon._desktop_unpersisted_attempts == {}
    jobs = [job for job in harness.snapshot()["jobs"] if job["metadata"].get("project_id") == "p"]
    assert len(jobs) == 1 and jobs[0]["status"] == "UNKNOWN"
    assert harness.context.current_model is harness.pointer
    assert harness.foreign.current_model is harness.foreign_pointer

    # Same-key replay must not duplicate the stage save effect or anything else.
    replay = harness.migrate(request)
    assert not replay["success"], replay
    assert replay == result
    assert len(save_calls) == 1
    assert harness.worker.trace == []
    assert not destination.exists()
    assert list(harness.daemon._desktop_attempt_rows("p")) == []
    assert harness.service.ledger._models == {}


def test_success_verifies_source_twice_second_is_post_load(harness, monkeypatch):
    """Order witness for both corrected fixtures: verify #1 pre-load, #2 post-load."""
    original = harness.adapter.verify_source_preserved
    stages = []
    def verify(identity, source):
        rows = [a for _job, a in harness.daemon._desktop_attempt_rows("p")]
        loaded = any(entry["method"] == "load" for entry in harness.worker.trace)
        if rows:
            assert loaded and rows[0]["state"] == "PROVISIONAL" and rows[0]["model_ref"]
            stages.append("POST_LOAD_PROVISIONAL")
        else:
            assert not loaded
            stages.append("PRE_PUBLISH_NO_ATTEMPT")
        return original(identity, source)
    monkeypatch.setattr(harness.adapter, "verify_source_preserved", verify)
    result = harness.migrate()
    assert result["success"], result
    assert stages == ["PRE_PUBLISH_NO_ATTEMPT", "POST_LOAD_PROVISIONAL"]
    job, attempt = harness.attempt()
    assert attempt["state"] == "READY"
