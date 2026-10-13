"""Bounded G2 security and recovery contracts.

These tests deliberately use a recording Worker and temporary project roots.
They exercise control-plane rejection and artifact guards without starting or
connecting to COMSOL.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path

import pytest

from comsol_mcp import _g2_isolation as isolation
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_docs import OfflineDocsIndex
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


class _FakeModel:
    def __init__(self, worker):
        self.worker = worker

    def save(self, path):
        self.worker.calls.append(("save", str(path)))
        target = Path(path)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(b"fake trial artifact")


class _RecordingWorker:
    """Small Worker double that makes every possible engine-side call visible."""

    def __init__(self, *, remove_fails: bool = False):
        self.calls: list[tuple] = []
        self.remove_fails = remove_fails

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

    def runtime_metadata(self):
        self.calls.append(("runtime_metadata",))
        return {"pid": 99999, "instance_id": "worker", "generation": 1}

    def client(self):
        self.calls.append(("client",))
        return self

    def tags(self):
        self.calls.append(("tags",))
        return []

    def model(self, tag):
        self.calls.append(("model", tag))
        return _FakeModel(self)

    def load(self, path, tag=None):
        self.calls.append(("load", str(path), tag))
        return object()

    def remove(self, tag):
        self.calls.append(("remove", tag))
        if self.remove_fails:
            raise RuntimeError("simulated trial cleanup failure")


@pytest.fixture
def backend_context(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _RecordingWorker()
    service = ExecutionService(
        SessionLedger("session", "server"),
        _SnapshotAdapter(),
        project_root=tmp_path,
    )
    ref = service.bind_model("main")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    # ManagedBackend normally uses the repository as the approved project root;
    # tests use an isolated temporary root for checkpoint/trial artifacts.
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


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("transaction.trial", {"actions": []}),
        ("transaction.apply", {"actions": []}),
        ("code.execute_java", {"mode": "trusted", "source_artifact": "code.java", "entrypoint": "Code"}),
        ("checkpoint.restore", {"checkpoint_id": "checkpoint-1"}),
    ],
)
def test_sensitive_g2_operations_require_owned_receipt_before_engine_calls(
    backend_context, operation, arguments
):
    backend, worker, _service, ref = backend_context

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(operation, arguments, _execution(ref), "guard-op", lambda _event: None)

    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"
    assert worker.calls == []


def test_request_isolation_boolean_cannot_substitute_a_live_receipt(backend_context):
    backend, worker, _service, ref = backend_context

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.trial",
            {"actions": []},
            _execution(ref, isolation=True),
            "boolean-isolation",
            lambda _event: None,
        )

    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"
    assert worker.calls == []


@pytest.mark.parametrize(
    ("operation", "arguments", "permission"),
    [
        ("transaction.trial", {"actions": []}, "compute"),
        ("transaction.apply", {"actions": []}, "project_write"),
        ("code.execute_java", {"mode": "trusted", "source_artifact": "code.java", "entrypoint": "Code"}, "trusted_code"),
        ("checkpoint.restore", {"checkpoint_id": "checkpoint-1"}, "project_write"),
    ],
)
def test_sensitive_g2_permissions_fail_before_engine_calls(
    backend_context, monkeypatch, operation, arguments, permission
):
    backend, worker, service, ref = backend_context
    service.ledger.permissions.discard(permission)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(operation, arguments, _execution(ref), "permission-op", lambda _event: None)

    assert exc.value.code == "PERMISSION_DENIED"
    assert worker.calls == []


@pytest.mark.parametrize(
    ("operation", "arguments", "code"),
    [
        (
            "node.property_set",
            {
                "path": {"segments": []},
                "properties": [{"name": "x", "value": {"kind": "float64", "shape": [2], "data": [1.0]}}],
            },
            "PROPERTY_TYPE_MISMATCH",
        ),
        ("node.property_get", {"path": {"segments": []}, "names": []}, "INVALID_REQUEST"),
        ("node.inspect", {"path": {"segments": [{"accessor": "arbitrary_method"}]}}, "INVALID_NODE_PATH"),
    ],
)
def test_malformed_registry_shape_is_rejected_before_worker_dispatch(
    backend_context, operation, arguments, code
):
    backend, worker, _service, ref = backend_context

    with pytest.raises(ExecutionContractError) as exc:
        backend.invoke(
            "registry_call",
            {"operation_id": operation, "arguments": arguments},
            _execution(ref, project_id="test-project", idempotency_key="invalid-shape"),
            "invalid-shape",
            lambda _event: None,
        )

    assert exc.value.code == code
    assert worker.calls == []


def test_stale_revision_rejects_transaction_before_checkpoint_save(backend_context, monkeypatch):
    backend, worker, _service, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.apply",
            {"actions": [], "checkpoint_policy": "before"},
            _execution(ref, expected_revision=99),
            "stale-revision",
            lambda _event: None,
        )

    assert exc.value.code == "REVISION_CONFLICT"
    assert worker.calls == []
    assert backend.store.list_metadata("checkpoints") == []


def test_altered_checkpoint_hash_is_rejected_without_replacing_model(backend_context, monkeypatch, tmp_path):
    backend, worker, service, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    checkpoint = tmp_path / "checkpoint.mph"
    checkpoint.write_bytes(b"original checkpoint")
    original_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    backend.store.persist_checkpoint(
        original_sha,
        {"checkpoint_id": "checkpoint-1", "path": str(checkpoint), "sha256": original_sha},
    )
    checkpoint.write_bytes(b"altered checkpoint")

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "checkpoint.restore",
            {"checkpoint_id": "checkpoint-1"},
            _execution(ref),
            "altered-checkpoint",
            lambda _event: None,
        )

    assert exc.value.code == "ARTIFACT_MISSING"
    assert not any(call[0] in {"load", "remove", "model", "save"} for call in worker.calls)
    assert service.ledger.revision(model_ref_from_mapping(ref)) == 0


def test_trial_cleanup_failure_is_unknown_and_not_reported_as_success(backend_context, monkeypatch):
    backend, _unused_worker, service, ref = backend_context
    worker = _RecordingWorker(remove_fails=True)
    backend.worker = worker
    checkpoint = _install_trial_checkpoint(backend, ref)
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "transaction.trial",
        {"actions": [], "checkpoint_id": checkpoint["checkpoint_id"]},
        _execution(ref),
        "cleanup-failure",
        lambda _event: None,
    )

    assert result["success"] is False
    assert result["cleanup_failed"] is True
    assert result["execution_state_unknown"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert any(call[0] == "remove" for call in worker.calls)


def test_recursive_docs_index_skips_private_symlink_and_rejects_outside_root(tmp_path):
    approved = tmp_path / "approved-docs"
    approved.mkdir()
    public = approved / "COMSOL-6.4-guide.md"
    public.write_text("# Public API\ngetDoubleArray", encoding="utf-8")
    private = approved / ".phase1-private"
    private.mkdir()
    (private / "COMSOL-6.4-secret.md").write_text("private credential text", encoding="utf-8")
    outside = tmp_path / "outside-6.4.md"
    outside.write_text("outside approved root", encoding="utf-8")
    symlink = approved / "linked-outside-6.4.md"
    symlink.symlink_to(outside)

    index = OfflineDocsIndex(tmp_path / "docs.sqlite3", allowed_roots=[approved])
    try:
        result = index.index(runtime_id="COMSOL 6.4", sources=[str(approved)])
        assert result["indexed_count"] == 1
        assert index.search(query="getDoubleArray", version="6.4")["status"] == "SUCCEEDED"
        for query, forbidden in (("private credential", "private credential"), ("outside approved", "outside approved")):
            search = index.search(query=query, version="6.4")
            rows = search["results"]
            assert search["status"] == "NOT_FOUND"
            assert rows == []
            assert all(forbidden not in row["snippet"] for row in rows)

        with pytest.raises(ExecutionContractError) as exc:
            index.index(runtime_id="COMSOL 6.4", sources=[str(outside)])
        assert exc.value.code == "PERMISSION_DENIED"
    finally:
        index.close()


def _write_receipt(path: Path, *, command: str = "/Applications/COMSOL64/Multiphysics/bin/mphserver") -> dict:
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    receipt = {
        "status": "RUNNING",
        "process": {"pid": 12345, "birth": "birth-1", "command_sha256": command_hash, "port": 56389},
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt


@pytest.mark.parametrize("failure", ["stale", "wildcard"])
def test_stale_or_wildcard_isolation_receipt_is_rejected(monkeypatch, tmp_path, failure):
    receipt_path = tmp_path / "receipt.json"
    receipt = _write_receipt(receipt_path)
    command = "/Applications/COMSOL64/Multiphysics/bin/mphserver"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()

    if failure == "stale":
        monkeypatch.setattr(
            isolation,
            "_process_snapshot",
            lambda _pid: {
                "pid": 12345,
                "birth": "different-birth",
                "command": command,
                "command_sha256": command_hash,
            },
        )
        monkeypatch.setattr(isolation, "_socket_rows", lambda _port: [])
    else:
        monkeypatch.setattr(
            isolation,
            "_process_snapshot",
            lambda _pid: {
                "pid": 12345,
                "birth": receipt["process"]["birth"],
                "command": command,
                "command_sha256": command_hash,
            },
        )
        monkeypatch.setattr(
            isolation,
            "_socket_rows",
            lambda _port: [{"pid": 12345, "endpoint": "*:56389", "state": "LISTEN"}],
        )

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint="127.0.0.1:56389")

    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


def test_isolation_receipt_accepts_exact_bidirectional_server_worker_pair(monkeypatch, tmp_path):
    receipt_path = tmp_path / "receipt.json"
    receipt = _write_receipt(receipt_path)
    command = "/Applications/COMSOL64/Multiphysics/bin/mphserver"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    worker_pid = 777
    worker_port = 58412
    monkeypatch.setattr(
        isolation,
        "_process_snapshot",
        lambda _pid: {
            "pid": 12345,
            "birth": receipt["process"]["birth"],
            "command": command,
            "command_sha256": command_hash,
        },
    )
    monkeypatch.setattr(
        isolation,
        "_socket_rows",
        lambda _port: [
            {"pid": 12345, "endpoint": "127.0.0.1:56389", "state": "LISTEN"},
            {"pid": 12345, "endpoint": f"127.0.0.1:56389->127.0.0.1:{worker_port}", "state": "ESTABLISHED"},
            {"pid": worker_pid, "endpoint": f"127.0.0.1:{worker_port}->127.0.0.1:56389", "state": "ESTABLISHED"},
        ],
    )

    result = isolation.verify_owned_server(receipt_path, endpoint="127.0.0.1:56389", worker_pid=worker_pid)

    assert result["verified"] is True
    assert result["established_client_pids"] == sorted([12345, worker_pid])


def test_isolation_receipt_rejects_mismatched_server_worker_peer_ports(monkeypatch, tmp_path):
    receipt_path = tmp_path / "receipt.json"
    receipt = _write_receipt(receipt_path)
    command = "/Applications/COMSOL64/Multiphysics/bin/mphserver"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    monkeypatch.setattr(
        isolation,
        "_process_snapshot",
        lambda _pid: {
            "pid": 12345,
            "birth": receipt["process"]["birth"],
            "command": command,
            "command_sha256": command_hash,
        },
    )
    monkeypatch.setattr(
        isolation,
        "_socket_rows",
        lambda _port: [
            {"pid": 12345, "endpoint": "127.0.0.1:56389", "state": "LISTEN"},
            {"pid": 12345, "endpoint": "127.0.0.1:56389->127.0.0.1:58412", "state": "ESTABLISHED"},
            {"pid": 777, "endpoint": "127.0.0.1:58413->127.0.0.1:56389", "state": "ESTABLISHED"},
        ],
    )

    with pytest.raises(ExecutionContractError) as exc:
        isolation.verify_owned_server(receipt_path, endpoint="127.0.0.1:56389", worker_pid=777)

    assert exc.value.code == "ISOLATION_PROOF_REQUIRED"
