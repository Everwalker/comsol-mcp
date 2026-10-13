from __future__ import annotations

import base64
import hashlib
import json

import pytest

from comsol_mcp import _g3_solver_ops
from comsol_mcp._comsol_progress_capture import CHUNK_BYTES, MAX_STORED_BYTES, ComsolProgressCapture
from comsol_mcp import _g3_ops
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._execution_contract import SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import RemoteClient
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


MODEL_REF = {
    "session_id": "session-1", "model_tag": "model-1", "server_instance_id": "server-1",
    "worker_generation": 3,
}


class _Client:
    def __init__(self, payload=b"solve started\xff\nsolve finished\n", *, stop_failure=False):
        self.payload = payload
        self.stop_failure = stop_failure
        self.calls = []

    def set_progress_log(self, path):
        self.calls.append(path)
        if path is not None:
            with open(path, "wb") as stream:
                stream.write(self.payload)
            return {"active": True, "disabled": False}
        if self.stop_failure:
            raise RuntimeError("synthetic Worker stop uncertainty")
        return {"active": False, "disabled": True}


class _Worker:
    def __init__(self, client):
        self._client = client

    def client(self):
        return self._client


class _RoutingWorker:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def client(self):
        return RemoteClient(self)

    def submit(self, kind, payload, **_kwargs):
        self.calls.append((kind, dict(payload)))
        path = payload.get("path")
        if path is None:
            return {"ok": True, "result": {"active": False, "disabled": True}}
        with open(path, "wb") as stream:
            stream.write(self.payload)
        return {"ok": True, "result": {"active": True, "disabled": False}}


class _SnapshotAdapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server-1",
                "external_event_counter": 0, "fingerprint": "fixed"}


def _bound_job(store: OperationStore, tmp_path, *, operation="study.run"):
    execution = {
        "project_id": "project-1", "session_id": "session-1", "model_ref": MODEL_REF,
        "expected_revision": 7, "request_id": "request-1",
    }
    metadata = {"operation": operation, "arguments": {}, "execution": execution}
    record, reused = store.begin(
        request_id="request-1", idempotency_key="idempotency-1", request_hash="a" * 64,
        operation=operation, metadata=metadata,
    )
    assert reused is False
    store.update_job(record["job_id"], "RUNNING")
    return record, execution


def _capture(store, tmp_path, client, *, operation="study.run"):
    record, execution = _bound_job(store, tmp_path, operation=operation)
    capture = ComsolProgressCapture(
        _Worker(client), store, tmp_path / "project", operation_id=record["operation_id"],
        operation=operation, execution=execution, model_ref=MODEL_REF, model_revision=7,
    )
    return record, execution, capture


def _read_all(store, job_id):
    first = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": job_id, "limit": 2})
    events = list(first["events"])
    cursor = first["next_cursor"]
    while cursor:
        page = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": job_id, "cursor": cursor})
        events.extend(page["events"])
        cursor = page["next_cursor"]
    final = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": job_id, "limit": 1000})
    return final, events


def test_progress_capture_persists_job_bound_binary_chunks_and_reader_verifies_them(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    client = _Client(b"solve started\xff\n" + b"z" * (2 * CHUNK_BYTES + 13))
    record, execution, capture = _capture(store, tmp_path, client)

    with capture:
        assert client.calls and client.calls[0].startswith(str(project_root))

    result, events = _read_all(store, record["job_id"])
    output = result["solver_output_log"]
    chunks = [row["metadata"] for row in events if row["event"] == "COMSOLProgressChunk"]
    assert output["status"] == "CAPTURED", output
    assert output["cleanup_verified"] is True
    assert output["byte_length"] == len(client.payload)
    assert output["sha256"] == hashlib.sha256(client.payload).hexdigest()
    assert output["chunk_count"] == 3
    assert b"".join(base64.b64decode(item["content_base64"], validate=True) for item in chunks) == client.payload
    assert all(item["job_id"] == record["job_id"] for item in chunks)
    assert all(item["project_id"] == execution["project_id"] for item in chunks)
    assert all(item["model_ref"] == MODEL_REF and item["model_revision"] == 7 for item in chunks)
    assert all(item["request_id"] == "request-1" and item["request_hash"] == "a" * 64 for item in chunks)
    assert all(not any(str(project_root) in json.dumps(row) for row in events) for row in events)
    assert client.calls[-1] is None
    store.close()


def test_progress_capture_marks_truncation_and_hashes_only_the_bounded_prefix(tmp_path):
    (tmp_path / "project").mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    payload = b"p" * (MAX_STORED_BYTES + 17)
    client = _Client(payload)
    record, _execution, capture = _capture(store, tmp_path, client)
    with capture:
        pass

    response = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": record["job_id"]})
    output = response["solver_output_log"]
    assert output["status"] == "CAPTURED_TRUNCATED", output
    assert output["file_size_bytes"] == len(payload)
    assert output["byte_length"] == MAX_STORED_BYTES
    assert output["sha256_scope"] == "prefix"
    assert output["sha256"] == hashlib.sha256(payload[:MAX_STORED_BYTES]).hexdigest()
    store.close()


def test_progress_capture_prewrite_binding_refusal_never_calls_worker(tmp_path):
    (tmp_path / "project").mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    record, execution = _bound_job(store, tmp_path)
    client = _Client()
    with pytest.raises(PreWriteRefusal) as mismatch:
        ComsolProgressCapture(
            _Worker(client), store, tmp_path / "project", operation_id=record["operation_id"],
            operation="mesh.convergence_study", execution=execution,
            model_ref=MODEL_REF, model_revision=7,
        )
    assert mismatch.value.code == "OPERATION_IDENTITY_MISMATCH"
    assert client.calls == []
    store.close()


def test_progress_capture_stop_uncertainty_retains_scratch_and_marks_unknown(tmp_path):
    (tmp_path / "project").mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    client = _Client(stop_failure=True)
    record, _execution, capture = _capture(store, tmp_path, client)
    with pytest.raises(ExecutionContractError) as failure:
        with capture:
            pass
    assert failure.value.code == "EXECUTION_STATE_UNKNOWN"
    assert failure.value.stage == "post_dispatch"
    assert failure.value.details["safe_retry"] is False
    assert capture.scratch is not None and capture.scratch.is_dir()
    result = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": record["job_id"]})
    assert result["solver_output_log"]["status"] == "UNKNOWN"
    store.close()


def test_progress_cleanup_failure_preserves_prior_operation_failure(tmp_path, monkeypatch):
    (tmp_path / "project").mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    client = _Client()
    record, _execution, capture = _capture(store, tmp_path, client)
    monkeypatch.setattr(capture, "_cleanup_scratch", lambda: False)
    with pytest.raises(ExecutionContractError) as failure:
        with capture:
            raise RuntimeError("prior solve callback failed")
    assert failure.value.code == "EXECUTION_STATE_UNKNOWN"
    assert failure.value.details["prior_error"]["type"] == "RuntimeError"
    assert failure.value.details["safe_retry"] is False
    result = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": record["job_id"]})
    assert result["solver_output_log"]["status"] == "UNKNOWN"
    store.close()


def test_solver_log_read_reports_only_structured_worker_causes_or_summary(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    record, _execution = _bound_job(store, tmp_path)
    store.add_event(record["job_id"], "worker_request", {
        "request_id": "worker-req-1", "kind": "call", "phase": "observed",
        "reply": {"status": "FAILED", "failure": {
            "code": "ENGINE_CALL_FAILED", "message": "failed",
            "cause_chain": [{"type": "com.comsol.util.exceptions.FlException", "message": "native cause"}],
        }},
    })
    response = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": record["job_id"]})
    assert response["exception_chain"]["status"] == "CAPTURED"
    assert response["exception_chain"]["items"][0]["type"].endswith("FlException")
    assert response["solver_output_log"]["status"] == "NOT_CAPTURED"
    store.close()


def test_managed_study_run_uses_the_private_capture_inside_its_real_write_ticket(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    service = ExecutionService(SessionLedger("session-1", "server-1"), _SnapshotAdapter(),
                               project_root=project_root)
    bound = service.bind_model("model-1")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    execution = {
        "project_id": "project-1", "session_id": "session-1", "model_ref": ref.as_dict(),
        "expected_revision": 0, "request_id": "request-managed", "idempotency_key": "managed-key",
    }
    metadata = {"operation": "study.run", "arguments": {}, "execution": execution}
    record, reused = store.begin(
        request_id="request-managed", idempotency_key="managed-key", request_hash="b" * 64,
        operation="study.run", metadata=metadata,
    )
    assert not reused
    store.update_job(record["job_id"], "RUNNING")
    worker = _RoutingWorker(b"a real worker reply would contain native progress\n")
    backend = ManagedBackend(tmp_path / "home", store, service=service, worker=worker,
                             registry={}, project_root=project_root)
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    monkeypatch.setitem(_g3_ops.DISPATCH, "study.run", lambda *_: {
        "status": {"ok": True, "status": "COMPLETED"}, "applied": [], "failed": [], "not_executed": [],
    })

    result = backend._invoke_g3_model("study.run", ref, {}, execution,
                                     record["operation_id"], "session-1")

    assert result["success"] is True, result
    assert [item[0] for item in worker.calls] == ["progress_log", "progress_log"]
    store.finish(record["operation_id"], status="SUCCEEDED", result=result)
    page = _g3_solver_ops.solver_log_read(store, "project-1", {"job_id": record["job_id"]})
    assert page["solver_output_log"]["status"] == "CAPTURED"
    assert page["solver_output_log"]["byte_length"] == len(worker.payload)
    assert any(event["event"] == "COMSOLProgressChunk" for event in page["events"])
    backend.close()
    store.close()
