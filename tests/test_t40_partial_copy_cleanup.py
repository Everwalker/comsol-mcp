"""T40 focused fake: a verified diff copy whose cleanup cannot be confirmed."""

import os
from pathlib import Path
from types import SimpleNamespace

from comsol_mcp import _g2_checkpoint_diff as diff
from comsol_mcp._execution_contract import model_ref_from_mapping
from test_unit_c_checkpoint_diff import env, invoke


def _inject_copy_fsync_failure(monkeypatch, target, *, fail_cleanup):
    real_fsync = os.fsync
    real_unlink = Path.unlink

    def fail_target_fsync(fd):
        current = os.fstat(fd)
        if target.exists():
            owned = target.stat()
            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                raise OSError("simulated checkpoint fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_target_fsync)
    if fail_cleanup:
        def fail_target_unlink(path, *args, **kwargs):
            if path == target:
                raise PermissionError("simulated owned-copy cleanup failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_target_unlink)


def test_diff_copy_cleanup_unknown_is_not_reported_as_complete(env, monkeypatch):
    backend, worker, ref, row, tickets = env
    monkeypatch.setattr(diff, "uuid4", lambda: SimpleNamespace(hex="t40partial"))
    target = backend.project_root / "g2_artifacts/diffs/mcp_diff_t40partial.mph"
    _inject_copy_fsync_failure(monkeypatch, target, fail_cleanup=True)

    out = invoke(env, "exact-old", "current")

    assert target.exists()
    assert out["success"] is False
    assert out["data"]["status"] == "UNKNOWN"
    assert out["data"]["complete"] is False
    assert out["data"]["execution_state_unknown"] is True
    assert out["data"]["cleanup"]["complete"] is False
    assert out["data"]["cleanup"]["errors"]
    assert out["error"]["safe_retry"] is False
    assert not tickets
    assert backend.service.ledger._state_for(model_ref_from_mapping(ref)).dirty is True


def test_verified_copy_cleanup_failure_allows_a_later_read(env, monkeypatch):
    backend, worker, ref, row, tickets = env
    monkeypatch.setattr(diff, "uuid4", lambda: SimpleNamespace(hex="t40cleaned"))
    target = backend.project_root / "g2_artifacts/diffs/mcp_diff_t40cleaned.mph"
    _inject_copy_fsync_failure(monkeypatch, target, fail_cleanup=False)

    failed = invoke(env, "exact-old", "current")
    assert failed["success"] is False
    assert failed["data"]["status"] == "INCOMPLETE"
    assert failed["data"]["cleanup"]["complete"] is True
    assert target.exists() is False
    assert backend.service.ledger._state_for(model_ref_from_mapping(ref)).dirty is False

    followup = backend.invoke(
        "checkpoint.diff",
        {"left": "current", "right": "current"},
        {
            "project_id": "p",
            "session_id": "session",
            "model_ref": ref,
            "request_id": "t40-after-failure",
            "idempotency_key": "t40-after-failure",
        },
        "t40-after-failure",
        None,
    )
    state = backend.service.ledger._state_for(model_ref_from_mapping(ref))
    assert followup["success"] is True
    assert followup["data"]["comparison"] == {
        "status": "EQUAL",
        "equal": True,
        "complete": True,
    }
    assert state.dirty is False
    assert not tickets

