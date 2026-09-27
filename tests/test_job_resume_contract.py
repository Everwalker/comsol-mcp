from __future__ import annotations

import sqlite3
import threading
import hashlib
from contextlib import contextmanager

import pytest

from comsol_mcp._operation_store import IdempotencyConflict, OperationStore, SCHEMA_VERSION
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService


def _failed_source(store, key="source-key"):
    source, _ = store.begin(request_id="source-request", idempotency_key=key, request_hash="source-hash",
                            operation="study.run", metadata={"operation": "study.run", "arguments": {"study": {"tag": "std1"}}})
    store.finish(source["operation_id"], status="FAILED", result={"success": False, "error": {"code": "SOLVER_ERROR"}})
    return source


def _begin_resume(store, source_job_id, *, key="resume-key", digest="resume-hash"):
    return store.begin_resume(
        source_job_id=source_job_id,
        request_id="resume-request",
        idempotency_key=key,
        request_hash=digest,
        metadata={"operation": "study.run", "arguments": {"study": {"tag": "std1"}}},
        timeouts={"rpc_timeout_s": 0.01},
    )


def test_resume_claim_is_atomic_idempotent_and_preserves_parent_result(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        source = _failed_source(store)
        parent_before = store.job(source["job_id"])
        child, reused = _begin_resume(store, source["job_id"])
        assert reused is False
        assert child["operation"] == "study.run"
        assert child["job_id"] != source["job_id"]
        assert store.job(source["job_id"])["status"] == "FAILED"
        assert store.job(source["job_id"])["result"] == parent_before["result"]
        assert store.job(source["job_id"])["metadata"]["resumed_by_job_id"] == child["job_id"]
        assert store.job(child["job_id"])["metadata"]["resume_of_job_id"] == source["job_id"]
        assert [event["event"] for event in store.events(source["job_id"])] == ["ResumeClaimed"]

        exact_retry, reused = _begin_resume(store, source["job_id"])
        assert reused is True
        assert exact_retry["operation_id"] == child["operation_id"]
        assert exact_retry["job_id"] == child["job_id"]
        with pytest.raises(IdempotencyConflict):
            _begin_resume(store, source["job_id"], key="another-key")
        with pytest.raises(IdempotencyConflict):
            _begin_resume(store, source["job_id"], digest="different-hash")

        # Cleanup sees the explicit parent/child dependency in child metadata.
        with pytest.raises(Exception):
            store.compact_terminal_job_metadata([source["job_id"]])
    finally:
        store.close()


def test_parallel_resume_claims_create_one_child(tmp_path):
    path = tmp_path / "operations.sqlite3"
    first, second = OperationStore(path), OperationStore(path)
    try:
        source = _failed_source(first)
        rows, errors = [], []

        def call(store):
            try:
                rows.append(_begin_resume(store, source["job_id"]))
            except Exception as exc:  # pragma: no cover - evidence retained for a failing race
                errors.append(exc)

        a = threading.Thread(target=call, args=(first,))
        b = threading.Thread(target=call, args=(second,))
        a.start(); b.start(); a.join(); b.join()
        assert not errors
        assert len(rows) == 2
        assert len({row[0]["operation_id"] for row in rows}) == 1
        assert len({row[0]["job_id"] for row in rows}) == 1
        assert sum(not reused for _record, reused in rows) == 1
        assert first.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 1
        assert first.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    finally:
        first.close(); second.close()


def test_additive_v1_resume_table_preserves_legacy_records_and_claims(tmp_path):
    path = tmp_path / "legacy-v1.sqlite3"
    store = OperationStore(path)
    try:
        source = _failed_source(store)
        child, _ = _begin_resume(store, source["job_id"])
        assert store.db.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION == 1
        parent_result = store.job(source["job_id"])["result"]
        child_result = store.job(child["job_id"])["result"]
    finally:
        store.close()

    # Simulate a v1 reader opening the SQLite file.  The extension uses a new
    # table only; standard legacy reads/writes neither need nor alter it.
    legacy = sqlite3.connect(path)
    assert legacy.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    assert legacy.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    assert legacy.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 1
    legacy.execute("CREATE TABLE IF NOT EXISTS legacy_reader_probe(value TEXT)")
    legacy.execute("INSERT INTO legacy_reader_probe VALUES('opened')")
    legacy.commit()
    legacy.close()

    reopened = OperationStore(path)
    try:
        assert reopened.job(source["job_id"])["result"] == parent_result
        assert reopened.job(child["job_id"])["result"] == child_result
        assert reopened.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 1
        assert reopened.db.execute("SELECT COUNT(*) FROM legacy_reader_probe").fetchone()[0] == 1
    finally:
        reopened.close()


class _Snapshot:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp",
                "external_event_counter": 0}


class _Worker:
    def __init__(self, status="FAILED"):
        self.status_value = status
        self.status_calls = []

    def status(self, request_id):
        self.status_calls.append(request_id)
        return {"status": self.status_value}


def _resume_fixture(tmp_path, *, worker_status="FAILED", source_operation="study.run", outer_arguments=None):
    service = ExecutionService(SessionLedger("session", "server"), _Snapshot(), project_root=tmp_path)
    model_ref = service.bind_model("source")["execution"]["model_ref"]
    worker = _Worker(worker_status)
    daemon = ControlDaemon(tmp_path, service=service, registry={}, worker=worker, project_root=tmp_path)
    run_arguments = {"study": {"tag": "std1"}, "recovery_policy": {"mode": "restart_from_checkpoint"}}
    source_arguments = outer_arguments or run_arguments
    if source_operation in {"registry_call", "operation_call"} and outer_arguments is None:
        source_arguments = {"operation_id": "study.run", "arguments": run_arguments}
    source_record, reused = daemon.store.begin(
        request_id="source-request", idempotency_key="source-idempotency", request_hash="source-hash",
        operation=source_operation,
        metadata={"operation": source_operation, "arguments": source_arguments,
                  "execution": {"session_id": "session", "model_ref": model_ref, "expected_revision": 0}},
    )
    assert reused is False
    daemon.store.finish(source_record["operation_id"], status="FAILED",
                        result={"success": False, "error": {"code": "SOLVER_ERROR"}})
    source_job_id = source_record["job_id"]
    daemon.store.add_event(source_job_id, "worker_request", {"phase": "submitted", "request_id": "engine-run-1"})
    if worker_status == "FAILED":
        daemon.store.add_event(source_job_id, "worker_request", {
            "phase": "observed", "request_id": "engine-run-1", "status": "FAILED"})

    path = tmp_path / "g2_artifacts" / "checkpoints" / "source.mph"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"native mph bytes")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    state = service.ledger._state_for(service.ledger._models["source"].ref)
    restore_scope = {"model_settings": True, "solution": "included_by_COMSOL_save_unverified",
                     "external_dependencies": False, "gui_state": "unknown"}
    readback = {"signature": hashlib.sha256(b"structural-signature").hexdigest(),
                "scope": "structure+global_parameters+solutions+Model.FileResourceList",
                "state": {"solutions": [], "file_resource_tags": []}}
    checkpoint = {
        "checkpoint_id": "checkpoint-source", "path": str(path), "sha256": digest,
        "restore_scope": restore_scope,
        "source_binding": {"model_ref": model_ref, "revision": 0, "fingerprint": "fp",
                           "external_event_counter": 0},
        "resume_source_job_id": source_job_id,
        "resume_source_operation_id": source_record["operation_id"],
        "resume_source_readback_signature": readback["signature"],
    }
    daemon.store.persist_checkpoint(checkpoint["checkpoint_id"], checkpoint)
    original_arguments = (source_arguments.get("arguments") if source_operation in {"registry_call", "operation_call"}
                          else source_arguments)
    signature = hashlib.sha256(b"same").hexdigest()
    contract = {
        "schema_version": 1,
        "source_job_id": source_job_id,
        "source_operation_id": source_record["operation_id"],
        "source_outer_operation": source_operation,
        "source_outer_request_hash": "source-hash",
        "source_model_ref": model_ref,
        "source_revision": 0,
        "source_fingerprint": "fp",
        "source_external_event_counter": 0,
        "source_model_readback": readback,
        "original_arguments": original_arguments,
        "run_arguments": {"study": {"tag": "std1"}},
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_sha256": digest,
        "checkpoint_path": str(path),
        "checkpoint_restore_scope": restore_scope,
        "source_solution_tags": [],
        "file_resource_tags": [],
    }
    daemon.store.update_job(source_job_id, "FAILED", {"resume_contract": contract})
    return daemon, worker, service, source_job_id, source_record, model_ref, run_arguments, checkpoint


def _resume_request(source_job_id, model_ref, *, key="resume-key", arguments=None, revision=0):
    return {"operation": "job.resume", "arguments": {"job_id": source_job_id, **(arguments or {})},
            "execution": {"session_id": "session", "model_ref": model_ref,
                          "expected_revision": revision, "idempotency_key": key}}


def test_resume_dispatch_queues_one_idempotent_child_from_verified_failed_source(tmp_path):
    daemon, _worker, _service, source_job_id, _source, model_ref, run_arguments, _checkpoint = _resume_fixture(tmp_path)
    calls = []

    def resume_backend(context, arguments, execution, operation_id, event_callback):
        calls.append((context, arguments, execution, operation_id))
        return {"success": True, "data": {"restart_mode": "restart_from_checkpoint"}, "execution": {}}

    daemon.backend.resume_study_run = resume_backend
    request = _resume_request(source_job_id, model_ref)
    try:
        result = daemon.dispatch(request)
        assert result["success"] is True, result
        child_id = result["execution"]["job_id"]
        assert child_id != source_job_id
        assert len(calls) == 1
        assert calls[0][1] == {"study": {"tag": "std1"}}
        assert calls[0][0]["source_job_id"] == source_job_id
        assert calls[0][2]["model_ref"] == model_ref
        assert daemon.store.job(source_job_id)["status"] == "FAILED"
        assert daemon.store.job(source_job_id)["result"]["error"]["code"] == "SOLVER_ERROR"
        assert daemon.store.job(child_id)["metadata"]["resume_of_job_id"] == source_job_id

        retry = daemon.dispatch(request)
        assert retry == result
        assert len(calls) == 1
        assert daemon.store.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 1
    finally:
        daemon.close()


def test_resume_refuses_unknown_worker_state_schema_spoof_and_permission_failures(tmp_path):
    daemon, _worker, service, source_job_id, _source, model_ref, _args, _checkpoint = _resume_fixture(
        tmp_path, worker_status="RUNNING")
    calls = []
    daemon.backend.resume_study_run = lambda *args: calls.append(args) or {"success": True, "data": {}, "execution": {}}
    try:
        unknown = daemon.dispatch(_resume_request(source_job_id, model_ref))
        assert unknown["success"] is False
        assert unknown["error"]["code"] == "JOB_NOT_QUIESCENT"
        assert calls == []
        assert daemon.store.db.execute("SELECT COUNT(*) FROM resume_claims").fetchone()[0] == 0

        spoof = daemon.dispatch(_resume_request(source_job_id, model_ref, key="spoof",
                                                arguments={"reentrant": True}))
        assert spoof["success"] is False
        assert spoof["error"]["code"] == "INVALID_REQUEST"
        assert calls == []

        _worker.status_value = "FAILED"
        service.ledger.permissions.remove("compute")
        denied = daemon.dispatch(_resume_request(source_job_id, model_ref, key="permission"))
        assert denied["success"] is False
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert calls == []
    finally:
        daemon.close()


def test_resume_backend_loads_fresh_tag_and_keeps_failed_source_model_bound(tmp_path, monkeypatch):
    from comsol_mcp._execution_contract import model_ref_from_mapping

    class Client:
        def __init__(self):
            self.models = {"source"}
            self.loads = []
            self.removes = []

        def tags(self):
            return sorted(self.models)

        def load(self, path, *, tag):
            self.loads.append((str(path), tag))
            self.models.add(tag)
            return object()

        def remove(self, tag):
            self.removes.append(tag)
            self.models.discard(tag)

    class WorkerWithClient(_Worker):
        def __init__(self, client):
            super().__init__()
            self._client = client
            self.active_operation_context = None

        def client(self):
            return self._client

        @contextmanager
        def operation_context(self, operation_id, *, on_request_event=None):
            previous = self.active_operation_context
            self.active_operation_context = (operation_id, on_request_event)
            try:
                yield
            finally:
                self.active_operation_context = previous

    service = ExecutionService(SessionLedger("session", "server"), _Snapshot(), project_root=tmp_path)
    source_ref_map = service.bind_model("source")["execution"]["model_ref"]
    client = Client()
    worker = WorkerWithClient(client)
    daemon = ControlDaemon(tmp_path, service=service, registry={}, worker=worker, project_root=tmp_path)
    source, _ = daemon.store.begin(request_id="failed-request", idempotency_key="failed-key",
        request_hash="failed-hash", operation="study.run", metadata={"operation": "study.run", "arguments": {"study": {"tag": "std1"}}})
    daemon.store.finish(source["operation_id"], status="FAILED", result={"success": False})
    checkpoint_path = tmp_path / "checkpoint.mph"
    checkpoint_path.write_bytes(b"checkpoint")
    checkpoint_hash = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    checkpoint_id = "checkpoint-1"
    restore_scope = {"model_settings": True, "solution": "included_by_COMSOL_save_unverified",
                     "external_dependencies": False, "gui_state": "unknown"}
    signature = hashlib.sha256(b"same").hexdigest()
    contract = {
        "source_job_id": source["job_id"],
        "source_operation_id": source["operation_id"],
        "source_model_ref": source_ref_map,
        "source_revision": 0,
        "source_fingerprint": "fp",
        "source_external_event_counter": 0,
        "checkpoint_id": checkpoint_id,
        "checkpoint_sha256": checkpoint_hash,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_restore_scope": restore_scope,
        "source_model_readback": {"signature": signature, "scope": "test-scope",
                                   "state": {"solutions": [], "file_resource_tags": []}},
    }
    daemon.store.persist_checkpoint(checkpoint_id, {
        "checkpoint_id": checkpoint_id, "path": str(checkpoint_path), "sha256": checkpoint_hash,
        "resume_source_job_id": source["job_id"], "resume_source_operation_id": source["operation_id"],
        "resume_source_readback_signature": signature,
        "source_binding": {"model_ref": source_ref_map, "revision": 0, "fingerprint": "fp",
                           "external_event_counter": 0},
        "restore_scope": restore_scope,
    })
    backend = daemon.backend
    backend._require_g2_isolation = lambda: {"verified": True, "receipt": "test"}
    backend._resume_model_readback = lambda _model: {"signature": signature, "scope": "test-scope",
                                                       "state": {"solutions": [], "file_resource_tags": []}}
    invoked = []
    event_callback = lambda _event: None

    def invoke_study(operation, ref, arguments, execution, operation_id, session):
        assert worker.active_operation_context == (operation_id, event_callback)
        invoked.append((operation, ref.as_dict(), arguments, execution, session))
        return {"success": True, "data": {"status": "SUCCEEDED"}, "execution": {}}

    backend._invoke_g3_model = invoke_study
    context = {"source_job_id": source["job_id"], "resume_contract": contract,
               "authorization_ref_present": False}
    execution = {"session_id": "session", "model_ref": source_ref_map, "expected_revision": 0,
                 "request_id": "resume-request", "idempotency_key": "resume-key"}
    try:
        result = backend.resume_study_run(context, {"study": {"tag": "std1"}}, execution,
                                          "resume-operation-id", event_callback)
        assert result["success"] is True
        data = result["data"]["resume"]
        new_ref = data["new_model_ref"]
        assert new_ref["model_tag"].startswith("mcp_resume_")
        assert new_ref != source_ref_map
        assert data["new_model_selected"] is False
        assert data["solution_continuation"] is False
        assert client.loads == [(str(checkpoint_path), new_ref["model_tag"])]
        assert client.removes == []
        assert source_ref_map["model_tag"] in client.tags()
        assert service.ledger._state_for(model_ref_from_mapping(source_ref_map)).retired is False
        assert service.ledger._state_for(model_ref_from_mapping(new_ref)).retired is False
        assert invoked[0][0] == "study.run"
        assert invoked[0][1] == new_ref
        assert invoked[0][3]["expected_revision"] == 0
        assert invoked[0][2] == {"study": {"tag": "std1"}}
    finally:
        daemon.close()


def test_resume_study_callback_uses_checkpoint_sha256_returned_by_creator(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    daemon, _worker, _service, _source_job_id, _source, model_ref, _args, _checkpoint = _resume_fixture(tmp_path)
    backend = daemon.backend
    backend._require_g2_isolation = lambda: {"verified": True}
    backend._preflight_study_resume = lambda *args: {"preflight": "ok"}
    created = {"checkpoint_id": "checkpoint-callback", "path": "/tmp/checkpoint.mph",
               "sha256": "a" * 64, "restore_scope": {}}
    backend._commit_study_resume_snapshot = lambda *args: dict(created)
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION",
                        frozenset(_g3_ops.REQUIRES_ISOLATION - {"study.run"}))
    monkeypatch.setitem(_g3_ops.DISPATCH, "study.run",
                        lambda _worker, _tag, _arguments: {
                            "ok": True, "status": "APPLIED", "applied": [{"method": "run"}],
                            "failed": [], "not_executed": [], "execution_state_unknown": False,
                            "readback": {"readable": True},
                        })
    ref = daemon.service.ledger._state_for(model_ref_from_mapping(model_ref)).ref
    try:
        result = backend._invoke_g3_model(
            "study.run", ref,
            {"study": {"tag": "std1"}, "recovery_policy": {"mode": "restart_from_checkpoint"}},
            {"session_id": "session", "model_ref": model_ref, "expected_revision": 0,
             "request_id": "resume-callback-request"},
            "resume-callback-operation", "session",
        )
        assert result["success"] is True, result
        assert result["data"]["resume_checkpoint"] == {
            "checkpoint_id": "checkpoint-callback",
            "sha256": "a" * 64,
            "restart_mode": "restart_from_checkpoint",
            "solution_continuation": False,
        }
    finally:
        daemon.close()
