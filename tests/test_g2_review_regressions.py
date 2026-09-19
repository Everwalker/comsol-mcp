"""Focused regressions for the W08-W12 review findings.

These tests use small in-process doubles only. They assert fail-closed results
and the absence of engine-side save/load before a transaction is allowed to
proceed.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _atomic_save
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_engine import create_checkpoint, execute_transaction, property_set
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


class _SnapshotAdapter:
    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "server",
            "external_event_counter": 0,
            "fingerprint": "fingerprint",
        }


def _write_valid_mph(path: Path, payload: bytes = b"fake mph") -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model/model.mphbin", payload)


class _TrialModel:
    def __init__(self, worker: "_TrialWorker"):
        self.worker = worker

    def save(self, path):
        self.worker.calls.append(("save", str(path)))
        _write_valid_mph(Path(path), b"trial")


class _TrialWorker:
    """Worker double that records every model-copy and cleanup operation."""

    def __init__(self, *, load_fails: bool = False):
        self.calls: list[tuple] = []
        self.load_fails = load_fails
        self._tags = {"main"}

    def operation_context(self, *_args, **_kwargs):
        self.calls.append(("operation_context",))
        return nullcontext()

    def backend_snapshot(self, tag):
        self.calls.append(("backend_snapshot", tag))
        return {
            "model_tag": tag,
            "server_instance_id": "server",
            "external_event_counter": 0,
            "fingerprint": "fingerprint",
        }

    def client(self):
        self.calls.append(("client",))
        return self

    def tags(self):
        self.calls.append(("tags",))
        return sorted(self._tags)

    def model(self, tag):
        self.calls.append(("model", tag))
        if tag not in self._tags:
            raise KeyError(tag)
        return _TrialModel(self)

    def load(self, path, tag=None):
        self.calls.append(("load", str(path), tag))
        if tag:
            # Simulate COMSOL publishing the tag before a transport/load error.
            self._tags.add(tag)
        if self.load_fails:
            raise RuntimeError("load failed after partial model registration")
        return object()

    def remove(self, tag):
        self.calls.append(("remove", tag))
        self._tags.discard(tag)


@pytest.fixture
def backend_context(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _TrialWorker()
    service = ExecutionService(
        SessionLedger("session", "server"),
        _SnapshotAdapter(),
        project_root=tmp_path,
    )
    ref = service.bind_model("main")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    try:
        yield backend, worker, service, ref
    finally:
        backend.docs_index.close()
        store.close()


def _execution(ref, *, expected_revision=0, **extra):
    return {
        "session_id": "session",
        "model_ref": ref,
        "expected_revision": expected_revision,
        **extra,
    }


def _nested_java_action():
    return {
        "operation_id": "code.execute_java",
        "arguments": {
            "source_artifact": "code.java",
            "entrypoint": "Code",
            "arguments": {},
            "mode": "trusted",
        },
    }


def test_nested_java_without_trusted_code_rejects_before_checkpoint_or_copy(
    backend_context, monkeypatch
):
    backend, worker, _service, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.apply",
            {"actions": [_nested_java_action()], "checkpoint_policy": "before"},
            _execution(ref, request_id="java-permission", idempotency_key="java-permission"),
            "java-permission",
            lambda _event: None,
        )

    assert exc.value.code == "PERMISSION_DENIED"
    assert not any(call[0] in {"save", "load"} for call in worker.calls)
    artifact_root = backend.project_root / "g2_artifacts"
    assert not artifact_root.exists() or not list(artifact_root.rglob("*"))


def test_trial_stale_revision_rejects_before_copy_or_load(backend_context, monkeypatch):
    backend, worker, _service, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.trial",
            {"actions": []},
            _execution(ref, expected_revision=99),
            "trial-stale-revision",
            lambda _event: None,
        )

    assert exc.value.code == "REVISION_CONFLICT"
    assert not any(call[0] in {"save", "load", "remove"} for call in worker.calls)
    assert not (backend.project_root / "g2_artifacts" / "trials").exists()


def test_trial_partial_load_failure_removes_registered_copy_and_freezes_main(
    backend_context, monkeypatch
):
    backend, _unused_worker, service, ref = backend_context
    worker = _TrialWorker(load_fails=True)
    backend.worker = worker
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "transaction.trial",
        {"actions": []},
        _execution(ref),
        "trial-partial-load",
        lambda _event: None,
    )

    assert result["success"] is False
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert any(call[0] == "save" for call in worker.calls)
    assert any(call[0] == "load" for call in worker.calls)
    assert any(call[0] == "remove" for call in worker.calls)
    assert worker._tags == {"main"}
    assert not list((backend.project_root / "g2_artifacts" / "trials").glob("*.mph"))
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.dirty is True
    assert state.fingerprint is None


class _CheckpointWorker:
    def __init__(self):
        self.save_calls: list[str] = []

    def client(self):
        return self

    def model(self, _tag):
        return self

    def save(self, path):
        self.save_calls.append(str(path))
        _write_valid_mph(Path(path), b"checkpoint candidate")


def test_checkpoint_collision_publishes_new_valid_file_and_preserves_original(tmp_path):
    destination = tmp_path / "checkpoint.mph"
    _write_valid_mph(destination, b"original")
    original = destination.read_bytes()
    worker = _CheckpointWorker()

    result = create_checkpoint(worker, "main", destination, "checkpoint")

    assert destination.read_bytes() == original
    assert Path(result["path"]) != destination
    assert Path(result["path"]).is_file()
    with zipfile.ZipFile(result["path"]) as archive:
        assert archive.testzip() is None
    assert len(worker.save_calls) == 1


def test_checkpoint_publish_failure_with_valid_candidate_preserves_original(tmp_path, monkeypatch):
    destination = tmp_path / "checkpoint.mph"
    _write_valid_mph(destination, b"original")
    original = destination.read_bytes()
    worker = _CheckpointWorker()

    def fail_link(_source, _target):
        raise OSError("simulated no-clobber publish failure")

    monkeypatch.setattr(_atomic_save.os, "link", fail_link)
    with pytest.raises(ExecutionContractError) as exc:
        create_checkpoint(worker, "main", destination, "checkpoint")

    assert exc.value.code == "ARTIFACT_MISSING"
    assert destination.read_bytes() == original
    candidates = list(tmp_path.glob(".*.tmp.mph"))
    assert candidates, "failed publication must retain a candidate for evidence"
    with zipfile.ZipFile(candidates[0]) as archive:
        assert archive.testzip() is None


class _GetterFailureNode:
    def __init__(self):
        self.calls: list[tuple] = []

    def properties(self):
        return ["flag"]

    def getValueType(self, _name):
        return "Boolean"

    def getAllowedPropertyValues(self, _name):
        return None

    def getType(self):
        return "FakeFeature"

    def set(self, name, value):
        self.calls.append(("set", name, value))

    def getBoolean(self, name):
        self.calls.append(("getBoolean", name))
        raise RuntimeError("authoritative getter failed after setter")


class _NodeWorker:
    def __init__(self, node):
        self.node = node

    def client(self):
        return self

    def model(self, _tag):
        return self.node


def test_successful_setter_with_failed_authoritative_getter_is_unknown():
    node = _GetterFailureNode()

    result = property_set(
        _NodeWorker(node),
        "main",
        {"segments": []},
        [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": False}}],
    )

    assert result["success"] is False
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["data"]["failed"][0]["partial_change"] is True
    assert [call[0] for call in node.calls] == ["set", "getBoolean"]


def test_transaction_internal_unknown_propagates_to_durable_control_job(tmp_path, monkeypatch):
    inner = execute_transaction(
        object(),
        "main",
        [{"operation_id": "node.inspect", "arguments": {"path": {"segments": []}}}],
        runner=lambda _operation, _arguments, _index: {
            "success": False,
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "worker state lost", "safe_retry": False},
        },
    )
    assert inner["success"] is False
    assert inner["execution_state_unknown"] is True
    assert inner["data"]["status"] == "UNKNOWN"

    daemon = ControlDaemon(tmp_path, registry={"transaction.apply": object()})
    monkeypatch.setattr(
        daemon.backend,
        "invoke",
        lambda _operation, _arguments, _execution, _operation_id, _event: inner,
    )
    try:
        result = daemon.dispatch(
            {
                "operation": "transaction.apply",
                "arguments": {"actions": []},
                "execution": {"request_id": "txn-unknown", "idempotency_key": "txn-unknown"},
            }
        )
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        assert daemon.store.get_operation(result["execution"]["operation_id"])["status"] == "UNKNOWN"
    finally:
        daemon.close()
