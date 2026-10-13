"""Real Coordinator/Store software controls; no native effect certification."""
from pathlib import Path
import json
import os
from copy import deepcopy

import pytest

from comsol_mcp._desktop_service import DesktopModelObservation, DesktopOperationError
from test_desktop_service import Harness


def migration_args(h, key):
    return {"project_id": "proj-1", "idempotency_key": key, "request_id": "request-" + key,
            "window_ref": h.windows()[0], "target_session_id": "session-target",
            "save_policy": {"mode": "save_copy", "destination_path": str(h.save_root / (key + ".mph")), "overwrite": False}}


def standalone(h):
    h.adapter.models["WINDOW:1"] = DesktopModelObservation("STANDALONE", standalone_model_id="source-1",
                    fingerprint="source-fingerprint", dirty=True)


def evidence(h, name, results, trace, finishes):
    out = os.environ.get("W25_PHASE2C_EVIDENCE_DIR")
    if out:
        p = Path(out) / (h.save_root.name + "-" + name + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        jobs = list(h.store.list_jobs(limit=100))
        p.write_text(json.dumps({"scope": "ACTUAL_COORDINATOR_TEMPORARY_STORE_INJECTED_SOFTWARE_PROVIDER",
            "results": results, "callbacks": trace, "finish_calls": finishes,
            "operation_count": h.store.db.execute("SELECT count(*) FROM operations").fetchone()[0],
            "jobs": jobs, "operations": [h.store.get_operation(j["operation_id"]) for j in jobs],
            "events": {j["job_id"]: h.store.events(j["job_id"], limit=1000) for j in jobs}}, indent=2) + "\n")


def spy_finish(h, monkeypatch):
    calls = []
    original = h.store.finish
    def finish(operation_id, **kwargs):
        calls.append({"operation_id": operation_id, "status": kwargs["status"]})
        return original(operation_id, **kwargs)
    monkeypatch.setattr(h.store, "finish", finish)
    return calls


def test_marked_save_copy_structured_error_cannot_be_retryable_failed(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    standalone(h)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    original = h.adapter.save_copy
    first = RuntimeError("original provider cause")
    raised = DesktopOperationError("SAVE_PROVIDER_FAILURE", "provider failed after actual software copy",
                    side_effect_started=False, safe_retry=True)
    def save(identity, destination, source=None):
        trace.append({"callback": "save_copy", "path": str(destination)})
        original(identity, destination, source)
        raise raised from first
    monkeypatch.setattr(h.adapter, "save_copy", save)
    try:
        args = migration_args(h, "marked-copy")
        result = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(result)
        assert Path(args["save_policy"]["destination_path"]).read_bytes() == b"fixture save-copy bytes"
        assert raised.__cause__ is first
        assert result["execution"]["status"] == "UNKNOWN"
        assert result["error"]["code"] == "SAVE_PROVIDER_FAILURE"
        assert result["error"]["message"] == str(raised)
        assert result["error"]["safe_retry"] is False
        assert len(finishes) == len(trace) == 1
        replay = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(replay)
        assert replay == result and len(finishes) == len(trace) == 1
        assert h.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        assert h.store.get_operation(result["execution"]["operation_id"])["status"] == "UNKNOWN"
    finally:
        evidence(h, "marked-copy", results, trace, finishes)
        h.close()


def test_real_early_source_refusal_keeps_failed_retry_hint(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    monkeypatch.setattr(h.adapter, "save_copy", lambda *a: trace.append("must-not-save"))
    try:
        result = h.coordinator.dispatch("desktop.migrate_standalone", migration_args(h, "not-standalone"))
        results.append(result)
        assert result["execution"]["status"] == "FAILED"
        assert result["error"]["code"] == "MODEL_BINDING_UNKNOWN"
        assert result["error"]["safe_retry"] is True
        assert trace == [] and finishes[0]["status"] == "FAILED" and len(finishes) == 1
    finally:
        evidence(h, "early", results, trace, finishes); h.close()


def test_mark_checks_actual_local_owner_before_sticky_state(tmp_path, monkeypatch):
    h = Harness(tmp_path); standalone(h)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    original = h.coordinator._mark_effect
    def no_owner(operation_id):
        with h.coordinator._operation_lock:
            h.coordinator._active_operations.discard(operation_id)
        return original(operation_id)
    monkeypatch.setattr(h.coordinator, "_mark_effect", no_owner)
    monkeypatch.setattr(h.adapter, "save_copy", lambda *a: trace.append("must-not-save"))
    try:
        result = h.coordinator.dispatch("desktop.migrate_standalone", migration_args(h, "no-owner"))
        results.append(result)
        assert result["execution"]["status"] == "FAILED"
        assert result["error"]["code"] == "OPERATION_OWNERSHIP_LOST"
        assert trace == [] and len(finishes) == 1 and finishes[0]["status"] == "FAILED"
    finally:
        evidence(h, "owner", results, trace, finishes); h.close()


@pytest.mark.parametrize("marked", [False, True])
def test_unsuccessful_mapping_copies_status_and_error_without_mutating_caller(tmp_path, monkeypatch, marked):
    h = Harness(tmp_path)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    caller = {"success": False, "data": {"first_cause": {"code": "ADAPTER_CAUSE", "message": "provider detail"}},
              "execution": {"status": "FAILED", "witness": "keep"},
              "error": {"code": "PROVIDER_RETURNED_FAILURE", "message": "keep original error", "safe_retry": True,
                        "secondary": {"message": "keep secondary"}}}
    before = deepcopy(caller)
    def execute(operation, args, record, mark_effect):
        trace.append({"parent": record["operation_id"], "marked": marked})
        if marked: mark_effect()
        return caller
    monkeypatch.setattr(h.coordinator, "_execute_operation", execute)
    try:
        args = migration_args(h, "returned-failure")
        result = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(result)
        expected = "UNKNOWN" if marked else "FAILED"
        assert result["execution"]["status"] == expected
        assert result["execution"]["witness"] == "keep"
        assert result["error"]["safe_retry"] is (not marked)
        assert result["error"]["code"] == caller["error"]["code"]
        assert result["error"]["message"] == caller["error"]["message"]
        assert result["data"] == caller["data"] and result["error"]["secondary"] == caller["error"]["secondary"]
        assert caller == before
        if marked:
            assert result is not caller and result["error"] is not caller["error"] and result["execution"] is not caller["execution"]
        assert len(finishes) == 1 and finishes[0]["status"] == expected
        replay = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(replay)
        assert replay == result and len(trace) == len(finishes) == 1
        assert h.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    finally:
        evidence(h, "returned", results, trace, finishes); h.close()


@pytest.mark.parametrize(("marked", "own_effect", "expected"), [
    (False, False, "FAILED"), (False, True, "UNKNOWN"), (True, False, "UNKNOWN"), (True, True, "UNKNOWN")])
def test_structured_exception_uses_local_or_exception_effect_and_preserves_cause(tmp_path, monkeypatch, marked, own_effect, expected):
    h = Harness(tmp_path)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    original_cause = ValueError("first original cause")
    raised = DesktopOperationError("TRUSTED_FAILURE", "unchanged message", category="BLOCKED_PERMISSION",
                                   side_effect_started=own_effect, safe_retry=True)
    def execute(operation, args, record, mark_effect):
        trace.append({"parent": record["operation_id"], "mark": marked, "exception_effect": own_effect})
        if marked: mark_effect()
        raise raised from original_cause
    monkeypatch.setattr(h.coordinator, "_execute_operation", execute)
    try:
        result = h.coordinator.dispatch("desktop.migrate_standalone", migration_args(h, "structured"))
        results.append(result)
        assert result["execution"]["status"] == expected
        assert result["error"] == {"code": "TRUSTED_FAILURE", "message": "unchanged message", "safe_retry": expected == "FAILED"}
        assert result["data"]["status"] == "BLOCKED_PERMISSION"
        assert raised.__cause__ is original_cause
        assert finishes == [{"operation_id": result["execution"]["operation_id"], "status": expected}]
    finally:
        evidence(h, "exception", results, trace, finishes); h.close()


def test_distinct_followup_claim_does_not_inherit_earlier_marker(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    trace, results = [], []
    finishes = spy_finish(h, monkeypatch)
    def execute(operation, args, record, mark_effect):
        marked = args["idempotency_key"] == "first-marked"
        trace.append({"parent": record["operation_id"], "mark": marked})
        if marked: mark_effect()
        raise DesktopOperationError("SAME_PROVIDER_FAILURE", "same source", side_effect_started=False, safe_retry=True)
    monkeypatch.setattr(h.coordinator, "_execute_operation", execute)
    try:
        first = h.coordinator.dispatch("desktop.migrate_standalone", migration_args(h, "first-marked"))
        second = h.coordinator.dispatch("desktop.migrate_standalone", migration_args(h, "next-unmarked"))
        results.extend([first, second])
        assert first["execution"]["status"] == "UNKNOWN" and first["error"]["safe_retry"] is False
        assert second["execution"]["status"] == "FAILED" and second["error"]["safe_retry"] is True
        assert first["execution"]["operation_id"] != second["execution"]["operation_id"]
        assert len(finishes) == len(trace) == 2
        assert h.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 2
    finally:
        evidence(h, "followup", results, trace, finishes); h.close()


def test_success_after_mark_stays_succeeded_and_same_key_replay_is_readonly(tmp_path, monkeypatch):
    h = Harness(tmp_path); standalone(h)
    finishes = spy_finish(h, monkeypatch)
    trace, results = [], []
    original = h.adapter.save_copy
    def save(*args):
        trace.append("save_copy")
        return original(*args)
    monkeypatch.setattr(h.adapter, "save_copy", save)
    try:
        args = migration_args(h, "success")
        result = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(result)
        assert result["success"] and result["execution"]["status"] == "SUCCEEDED"
        assert result["data"]["desktop_binding_verified"] is False
        assert len(finishes) == len(trace) == 1 and finishes[0]["status"] == "SUCCEEDED"
        queue_before = list(h.engine.calls)
        replay = h.coordinator.dispatch("desktop.migrate_standalone", args)
        results.append(replay)
        assert replay == result and h.engine.calls == queue_before and len(finishes) == len(trace) == 1
    finally:
        evidence(h, "success", results, trace, finishes); h.close()
