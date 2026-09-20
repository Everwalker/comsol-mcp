"""Focused regressions for the W08-W12 review findings.

These tests use small in-process doubles only. They assert fail-closed results
and the absence of engine-side save/load before a transaction is allowed to
proceed.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import os
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


class _ContextWorker:
    """Small Worker double for the managed G2 request-event boundary."""

    def __init__(self):
        self.current = None
        self.context_history: list[tuple[str, str | None]] = []

    @contextmanager
    def operation_context(self, operation_id, *, on_request_event=None):
        previous = self.current
        self.current = (operation_id, on_request_event)
        self.context_history.append(("enter", operation_id))
        try:
            yield
        finally:
            self.context_history.append(("exit", operation_id))
            self.current = previous

    def submit(self, kind, payload, *, request_id=None, **_kwargs):
        assert self.current is not None
        operation_id, callback = self.current
        for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
            if callback is not None:
                callback({"phase": phase, "operation_id": operation_id,
                          "request_id": request_id, "kind": kind, "status": status})
        return {"status": "SUCCEEDED", "request_id": request_id}


class _ContextTrialWorker(_TrialWorker, _ContextWorker):
    """Trial-capable Worker double that keeps one outer request context."""

    def __init__(self):
        _TrialWorker.__init__(self)
        self.current = None
        self.context_history: list[tuple[str, str | None]] = []

    @contextmanager
    def operation_context(self, operation_id, *, on_request_event=None):
        previous = self.current
        self.current = (operation_id, on_request_event)
        self.context_history.append(("enter", operation_id))
        try:
            yield
        finally:
            self.context_history.append(("exit", operation_id))
            self.current = previous

    def submit(self, kind, payload, *, request_id=None, **_kwargs):
        assert self.current is not None
        operation_id, callback = self.current
        for phase, status in (("submitted", None), ("observed", "SUCCEEDED")):
            if callback is not None:
                callback({"phase": phase, "operation_id": operation_id,
                          "request_id": request_id, "kind": kind, "status": status})
        return {"status": "SUCCEEDED", "request_id": request_id}


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


def _install_trial_checkpoint(backend, ref, *, name="trial-source", payload=b"checkpoint source"):
    path = backend.project_root / "g2_artifacts" / "checkpoints" / f"{name}.mph"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    metadata = {
        "checkpoint_id": f"checkpoint-{name}",
        "path": str(path),
        "sha256": digest,
        "source_sha256": digest,
        "source_binding": {
            "model_ref": dict(ref),
            "revision": 0,
            "fingerprint": "fingerprint",
            "external_event_counter": 0,
        },
        "source_model_ref": dict(ref),
        "source_revision": 0,
        "source_fingerprint": "fingerprint",
        "source_external_event_counter": 0,
    }
    backend.store.persist_checkpoint(digest, metadata)
    return metadata


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


def test_direct_untrusted_java_mode_rejects_before_write_ticket(backend_context, monkeypatch):
    backend, worker, service, ref = backend_context
    service.ledger.permissions.add("trusted_code")
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "code.execute_java",
            {
                "source_artifact": "tools/java/Phase3NoWrapper.java",
                "entrypoint": "Phase3NoWrapper#run",
                "arguments": {},
                "mode": "untrusted",
            },
            _execution(ref),
            "direct-untrusted-mode",
            lambda _event: None,
        )

    assert exc.value.code == "PERMISSION_DENIED"
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 0
    assert state.fingerprint == "fingerprint"
    assert not any(call[0] in {"save", "load", "execute_java"} for call in worker.calls)


def test_nested_untrusted_java_mode_rejects_before_checkpoint_or_copy(backend_context, monkeypatch):
    backend, worker, service, ref = backend_context
    service.ledger.permissions.add("trusted_code")
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    action = _nested_java_action()
    action["arguments"]["mode"] = "untrusted"
    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.apply",
            {"actions": [action], "checkpoint_policy": "before"},
            _execution(ref),
            "nested-untrusted-mode",
            lambda _event: None,
        )

    assert exc.value.code == "PERMISSION_DENIED"
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 0
    assert state.fingerprint == "fingerprint"
    assert not any(call[0] in {"save", "load", "execute_java"} for call in worker.calls)
    artifact_root = backend.project_root / "g2_artifacts"
    assert not artifact_root.exists() or not list(artifact_root.rglob("*"))


def test_g2_direct_and_operation_fallback_bind_worker_events_to_original_job(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _ContextWorker()
    backend = ManagedBackend(tmp_path, store, worker=worker, registry={})
    events: list[dict] = []
    ref = {
        "session_id": "session",
        "server_instance_id": "server",
        "model_tag": "main",
        "generation": 1,
        "schema_version": 1,
    }

    def invoke_model(operation, arguments, execution, operation_id, event_callback):
        return {"success": True, "data": worker.submit("model", {"operation": operation}, request_id=operation_id)}

    monkeypatch.setattr(backend, "_invoke_g2_model", invoke_model)
    try:
        direct = backend.invoke(
            "node.inspect",
            {"path": {"segments": []}},
            {"model_ref": ref, "session_id": "session", "expected_revision": 0},
            "job-direct",
            events.append,
        )
        fallback = backend.invoke(
            "operation_call",
            {"operation_id": "node.inspect", "arguments": {
                "project_id": "project",
                "session_id": "session",
                "model_ref": ref,
                "expected_revision": 0,
                "path": {"segments": []},
            }},
            {"model_ref": ref, "session_id": "session", "expected_revision": 0},
            "job-fallback",
            events.append,
        )
    finally:
        backend.docs_index.close()
        store.close()

    assert direct["success"] and fallback["success"]
    assert [(row["operation_id"], row["phase"], row["status"]) for row in events] == [
        ("job-direct", "submitted", None), ("job-direct", "observed", "SUCCEEDED"),
        ("job-fallback", "submitted", None), ("job-fallback", "observed", "SUCCEEDED"),
    ]
    assert worker.context_history == [
        ("enter", "job-direct"), ("exit", "job-direct"),
        ("enter", "job-fallback"), ("exit", "job-fallback"),
    ]


def test_g2_event_context_restores_outer_context_when_action_raises(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _ContextWorker()
    backend = ManagedBackend(tmp_path, store, worker=worker, registry={})
    ref = {
        "session_id": "session",
        "server_instance_id": "server",
        "model_tag": "main",
        "generation": 1,
        "schema_version": 1,
    }

    def failing_model(*_args):
        raise RuntimeError("deliberate G2 callback failure")

    monkeypatch.setattr(backend, "_invoke_g2_model", failing_model)
    outer_callback = lambda _event: None
    try:
        with worker.operation_context("outer-job", on_request_event=outer_callback):
            with pytest.raises(RuntimeError, match="deliberate G2 callback failure"):
                backend.invoke(
                    "node.inspect",
                    {"path": {"segments": []}},
                    {"model_ref": ref, "session_id": "session", "expected_revision": 0},
                    "inner-job",
                    lambda _event: None,
                )
            assert worker.current == ("outer-job", outer_callback)
    finally:
        backend.docs_index.close()
        store.close()
    assert worker.current is None
    assert worker.context_history == [
        ("enter", "outer-job"), ("enter", "inner-job"),
        ("exit", "inner-job"), ("exit", "outer-job"),
    ]


def _nested_code_step(marker: str) -> dict:
    return {
        "operation_id": "code.execute_java",
        "arguments": {
            "source_artifact": "tools/java/Phase3NoWrapper.java",
            "entrypoint": "Phase3NoWrapper#run",
            "arguments": {"marker": marker},
            "mode": "trusted",
        },
    }


def test_transaction_nested_code_steps_use_distinct_worker_ids_and_one_outer_job(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _ContextWorker()
    service = ExecutionService(
        SessionLedger("session", "server"),
        _SnapshotAdapter(),
        project_root=tmp_path,
    )
    ref = service.bind_model("main")["execution"]["model_ref"]
    service.ledger.permissions.add("trusted_code")
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    seen: list[tuple[str, str, str]] = []
    events: list[dict] = []

    def run_action(operation, model_tag, arguments, request_id):
        assert worker.current is not None
        seen.append((request_id, worker.current[0], arguments["arguments"]["marker"]))
        reply = worker.submit("java", arguments, request_id=request_id)
        return {"success": True, "data": reply}

    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    monkeypatch.setattr(backend, "_run_g2_action", run_action)
    try:
        result = backend.invoke(
            "transaction.apply",
            {"actions": [_nested_code_step("one"), _nested_code_step("two")], "checkpoint_policy": "never"},
            {"model_ref": ref, "session_id": "session", "expected_revision": 0,
             "request_id": "outer-request", "idempotency_key": "outer-request"},
            "outer-job",
            events.append,
        )
    finally:
        backend.docs_index.close()
        store.close()

    assert result["success"] is True, result
    assert seen == [
        ("outer-job:step:0", "outer-job", "one"),
        ("outer-job:step:1", "outer-job", "two"),
    ]
    assert [(row["operation_id"], row["request_id"], row["phase"]) for row in events] == [
        ("outer-job", "outer-job:step:0", "submitted"),
        ("outer-job", "outer-job:step:0", "observed"),
        ("outer-job", "outer-job:step:1", "submitted"),
        ("outer-job", "outer-job:step:1", "observed"),
    ]


def test_trial_nested_code_steps_use_distinct_worker_ids_and_one_outer_job(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _ContextTrialWorker()
    service = ExecutionService(
        SessionLedger("session", "server"),
        _SnapshotAdapter(),
        project_root=tmp_path,
    )
    ref = service.bind_model("main")["execution"]["model_ref"]
    service.ledger.permissions.add("trusted_code")
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    checkpoint = _install_trial_checkpoint(backend, ref, name="nested-code-trial")
    seen: list[tuple[str, str, str]] = []
    events: list[dict] = []

    def run_action(operation, model_tag, arguments, request_id):
        assert worker.current is not None
        seen.append((request_id, worker.current[0], arguments["arguments"]["marker"]))
        reply = worker.submit("java", arguments, request_id=request_id)
        return {"success": True, "data": reply}

    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    monkeypatch.setattr(backend, "_run_g2_action", run_action)
    try:
        result = backend.invoke(
            "transaction.trial",
            {"actions": [_nested_code_step("one"), _nested_code_step("two")],
             "invariants": [], "checkpoint_id": checkpoint["checkpoint_id"]},
            {"model_ref": ref, "session_id": "session", "expected_revision": 0,
             "request_id": "outer-request", "idempotency_key": "outer-request"},
            "outer-job",
            events.append,
        )
    finally:
        backend.docs_index.close()
        store.close()

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["data"]["main_model_scope"]["status"] == "UNKNOWN"
    assert seen == [
        ("outer-job:step:0", "outer-job", "one"),
        ("outer-job:step:1", "outer-job", "two"),
    ]
    assert [(row["operation_id"], row["request_id"], row["phase"]) for row in events] == [
        ("outer-job", "outer-job:step:0", "submitted"),
        ("outer-job", "outer-job:step:0", "observed"),
        ("outer-job", "outer-job:step:1", "submitted"),
        ("outer-job", "outer-job:step:1", "observed"),
    ]


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


def test_trial_requires_explicit_checkpoint_before_copy_or_load(backend_context, monkeypatch):
    backend, worker, _service, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.trial",
            {"actions": []},
            _execution(ref),
            "trial-no-checkpoint",
            lambda _event: None,
        )

    assert exc.value.code == "CHECKPOINT_REQUIRED"
    assert not any(call[0] in {"save", "load", "remove"} for call in worker.calls)


def test_trial_copies_bound_checkpoint_without_saving_main(backend_context, monkeypatch):
    backend, worker, _service, ref = backend_context
    checkpoint = _install_trial_checkpoint(backend, ref)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "transaction.trial",
        {"actions": [], "checkpoint_id": checkpoint["checkpoint_id"]},
        _execution(ref),
        "trial-copy-only",
        lambda _event: None,
    )

    assert result["success"] is True
    assert not any(call[0] == "save" for call in worker.calls)
    assert any(call[0] == "load" for call in worker.calls)
    data = result["data"]
    assert data["source_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert data["source_checkpoint"]["source_binding"]["model_ref"] == dict(ref)
    assert data["source_checkpoint"]["sha256"] == checkpoint["sha256"]
    assert data["trial_copy"]["verified"] is True
    assert data["trial_copy"]["bytes"] == len(b"checkpoint source")
    assert data["cleanup"] == {"model_removed": True, "artifact_deleted": True, "errors": []}
    assert Path(checkpoint["path"]).read_bytes() == b"checkpoint source"


def test_trial_existing_copy_destination_is_never_deleted(backend_context, monkeypatch):
    backend, worker, _service, ref = backend_context
    checkpoint = _install_trial_checkpoint(backend, ref, name="trial-existing")
    operation_id = "existing-destination"
    destination = backend.project_root / "g2_artifacts" / "trials" / (
        "mcp_trial_" + operation_id.replace("-", "")[:16] + ".mph"
    )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sentinel = b"caller-owned sentinel"
    destination.write_bytes(sentinel)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.trial",
            {"actions": [], "checkpoint_id": checkpoint["checkpoint_id"]},
            _execution(ref), operation_id, lambda _event: None,
        )

    assert exc.value.code == "ARTIFACT_MISSING"
    assert destination.read_bytes() == sentinel
    assert not any(call[0] == "load" for call in worker.calls)


def test_trial_copy_failure_removes_only_owned_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.mph"
    destination = tmp_path / "copy.mph"
    source.write_bytes(b"source bytes")
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync failure")))

    with pytest.raises(ExecutionContractError) as exc:
        ManagedBackend._copy_trial_checkpoint(source, destination, hashlib.sha256(source.read_bytes()).hexdigest())

    assert exc.value.code == "ARTIFACT_MISSING"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("revision", "REVISION_CONFLICT"),
        ("ref", "MODEL_IDENTITY_MISMATCH"),
        ("fingerprint", "MODEL_IDENTITY_MISMATCH"),
        ("hash", "ARTIFACT_MISSING"),
    ],
)
def test_trial_rejects_stale_or_tampered_checkpoint_before_copy_or_load(
    backend_context, monkeypatch, mutation, expected_code
):
    backend, worker, _service, ref = backend_context
    checkpoint = _install_trial_checkpoint(backend, ref, name=f"trial-{mutation}")
    if mutation == "revision":
        checkpoint["source_binding"]["revision"] = 99
        checkpoint["source_revision"] = 99
    elif mutation == "ref":
        checkpoint["source_binding"]["model_ref"] = {**dict(ref), "model_tag": "another"}
        checkpoint["source_model_ref"] = {**dict(ref), "model_tag": "another"}
    elif mutation == "fingerprint":
        checkpoint["source_binding"]["fingerprint"] = "stale-fingerprint"
        checkpoint["source_fingerprint"] = "stale-fingerprint"
    else:
        Path(checkpoint["path"]).write_bytes(b"tampered checkpoint")
    backend.store.persist_checkpoint(checkpoint["sha256"], checkpoint)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.trial",
            {"actions": [], "checkpoint_id": checkpoint["checkpoint_id"]},
            _execution(ref),
            f"trial-reject-{mutation}",
            lambda _event: None,
        )

    assert exc.value.code == expected_code
    assert not any(call[0] in {"save", "load", "remove"} for call in worker.calls)


def test_trial_partial_load_failure_removes_registered_copy_and_freezes_main(
    backend_context, monkeypatch
):
    backend, _unused_worker, service, ref = backend_context
    worker = _TrialWorker(load_fails=True)
    backend.worker = worker
    checkpoint = _install_trial_checkpoint(backend, ref)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "transaction.trial",
        {"actions": [], "checkpoint_id": checkpoint["checkpoint_id"]},
        _execution(ref),
        "trial-partial-load",
        lambda _event: None,
    )

    assert result["success"] is False
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert not any(call[0] == "save" for call in worker.calls)
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
