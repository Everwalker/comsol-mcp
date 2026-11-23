"""Independent source-artifact UNKNOWN control for the T40 diff candidate."""

from pathlib import Path
from types import SimpleNamespace

from comsol_mcp import _g2_checkpoint_diff as diff
from comsol_mcp._execution_contract import model_ref_from_mapping
from test_unit_c_checkpoint_diff import env, invoke
from test_t40_partial_copy_cleanup import _inject_copy_fsync_failure


def test_unlink_failure_keeps_copy_error_dispatch_and_source_dirty(env, monkeypatch):
    backend, worker, ref, row, tickets = env
    monkeypatch.setattr(diff, "uuid4", lambda: SimpleNamespace(hex="t40review"))
    target = backend.project_root / "g2_artifacts/diffs/mcp_diff_t40review.mph"
    _inject_copy_fsync_failure(monkeypatch, target, fail_cleanup=True)

    out = invoke(env, "exact-old", "current")

    assert out["success"] is False
    assert out["data"]["status"] == "UNKNOWN"
    assert out["data"]["complete"] is False
    assert out["data"]["cleanup"]["complete"] is False
    [cleanup_error] = out["data"]["cleanup"]["errors"]
    assert cleanup_error["code"] == "EXECUTION_STATE_UNKNOWN"
    assert cleanup_error["path"] == str(target)
    assert "cleanup failed" in cleanup_error["message"]
    assert target.exists()

    [cause] = [error for error in out["data"]["errors"] if error.get("stage") == "owned_copy"]
    assert cause["code"] == "EXECUTION_STATE_UNKNOWN"
    assert cause["dispatched"] is False
    assert cause["mutation_possible"] is False
    assert out["error"]["safe_retry"] is False
    assert not any(call[0] in {"load", "remove"} for call in worker.calls)

    state = backend.service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.dirty is True
    assert not tickets


def test_changed_original_checkpoint_keeps_cleanup_complete_and_source_unknown(env, monkeypatch):
    backend, worker, ref, row, tickets = env
    source = Path(row["path"])
    real_load = worker.load

    def change_source_after_load(*args, **kwargs):
        result = real_load(*args, **kwargs)
        source.write_bytes(b"changed-original-checkpoint")
        return result

    monkeypatch.setattr(worker, "load", change_source_after_load)
    out = invoke(env, "exact-old", "current")

    assert out["success"] is False
    assert out["data"]["status"] == "UNKNOWN"
    assert out["data"]["complete"] is False
    assert out["data"]["comparison"] == {
        "status": "INCOMPLETE",
        "equal": None,
        "complete": False,
    }
    assert out["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert out["error"]["safe_retry"] is False

    assert out["data"]["cleanup"] == {"complete": True, "errors": []}
    [owned] = out["data"]["owned_copies"]
    assert owned["loaded"] is True
    assert owned["removed"] is True
    assert owned["input_deleted"] is True
    assert not Path(owned["path"]).exists()

    [cause] = [
        error
        for error in out["data"]["errors"]
        if error.get("message") == "original checkpoint bytes changed during projection"
    ]
    assert cause["code"] == "EXECUTION_STATE_UNKNOWN"
    assert cause["dispatched"] is True
    assert cause["mutation_possible"] is True
    [observation] = out["data"]["provenance"]["checkpoint_after"]
    assert observation["identity_unchanged"] is False
    assert observation["sha256"] != row["sha256"]

    state = backend.service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.dirty is True
    assert not tickets
