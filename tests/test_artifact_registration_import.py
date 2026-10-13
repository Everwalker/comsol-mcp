from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from threading import Barrier

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
    resolve_registered_artifact,
)
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore


class _SnapshotAdapter:
    def __init__(self, server_id="server-test"):
        self.server_id = server_id

    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": self.server_id,
            "external_event_counter": 0,
            "fingerprint": "fingerprint-test",
        }


class _Worker:
    generation = 1

    class _Model:
        class java:
            @staticmethod
            def tag():
                return "m1"

            @staticmethod
            def getFilePath():
                return ""

    class _Client:
        def model(self, tag):
            assert tag == "m1"
            return _Worker._Model()

    def client(self):
        return self._Client()

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


def _make_daemon(tmp_path, *, with_model=False):
    project_container = tmp_path / "projects"
    project_container.mkdir(parents=True)
    ledger = SessionLedger("session-test", "server-test")
    service = ExecutionService(ledger, _SnapshotAdapter(), project_root=project_container)
    worker = _Worker() if with_model else None
    daemon = ControlDaemon(
        tmp_path / "control",
        service=service,
        registry={},
        worker=worker,
        project_root=project_container,
    )
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    if with_model:
        daemon.backend.worker_identity = {
            "runtime_id": "artifact-test-runtime",
            "worker_instance_id": "artifact-test-worker",
            "connection_epoch": 1,
            "server_instance_id": "server-test",
            "endpoint": "127.0.0.1:2036",
        }
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "artifact-test-project",
            "workspace": "artifact-test-project",
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": "create-artifact-test-project", "idempotency_key": "create-artifact-test-project"},
    })
    assert created["success"] is True
    project = created["data"]["project"]
    project_root = Path(project["workspace"])
    (project_root / "inputs").mkdir(parents=True)
    daemon._test_project_id = project["project_id"]
    daemon._test_project_container = project_container
    execution = None
    if with_model:
        adopted = daemon.dispatch({
            "operation": "model.adopt",
            "arguments": {"server_model_tag": "m1"},
            "execution": {
                "project_id": daemon._test_project_id,
                "session_id": "session-test",
                "request_id": "adopt-artifact-test-model",
                "idempotency_key": "adopt-artifact-test-model",
            },
        })
        assert adopted["success"] is True
        execution = adopted["execution"]
    return daemon, project_root, execution


def _register(daemon, *, path="inputs/shape.step", key="artifact-register-1", request_id="register-request-1"):
    return daemon.dispatch({
        "operation": "artifact.register",
        "arguments": {
            "project_id": daemon._test_project_id,
            "idempotency_key": key,
            "request_id": request_id,
            "path": path,
            "role": "geometry_source",
            "classification": "internal",
        },
        "execution": {"project_id": daemon._test_project_id, "idempotency_key": key, "request_id": request_id},
    })


def _error_code(envelope):
    return envelope.get("error", {}).get("code") or envelope.get("data", {}).get("error", {}).get("code")


def _register_for_project(daemon, project_id, *, path, role="geometry_source", classification="internal", key="register-extra"):
    return daemon.dispatch({
        "operation": "artifact.register",
        "arguments": {
            "project_id": project_id,
            "idempotency_key": key,
            "request_id": f"{key}-request",
            "path": path,
            "role": role,
            "classification": classification,
        },
        "execution": {"project_id": project_id, "idempotency_key": key, "request_id": f"{key}-request"},
    })


def _create_project(daemon, *, workspace, label):
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": workspace,
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"create-{workspace}", "idempotency_key": f"create-{workspace}"},
    })
    assert response["success"] is True
    project = response["data"]["project"]
    (Path(project["workspace"]) / "inputs").mkdir(parents=True)
    return project


def _store_rows_for_read_assertion(store):
    tables = ("operations", "jobs", "job_events", "artifacts", "revisions", "projects")
    snapshot = {}
    with store.lock:
        for table in tables:
            columns = [row[1] for row in store.db.execute(f"PRAGMA table_info({table})").fetchall()]
            snapshot[table] = [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
            snapshot[f"{table}_columns"] = columns
    return snapshot


def _read_action(daemon, operation, project_id, arguments, *, entrypoint="direct", request_id=None):
    body = {"project_id": project_id, **arguments}
    execution = {"project_id": project_id}
    if request_id:
        body["request_id"] = request_id
        execution["request_id"] = request_id
    if entrypoint == "direct":
        request = {"operation": operation, "arguments": body, "execution": execution}
    else:
        request = {
            "operation": entrypoint,
            "arguments": {"operation_id": operation, "arguments": body},
            "execution": execution,
        }
    return daemon.dispatch(request)


def _inject_artifact_metadata_for_test(store, key, metadata):
    """Bypass the protected public write path to simulate on-disk corruption."""
    with store.lock:
        store.db.execute(
            "UPDATE artifacts SET metadata=? WHERE artifact_id=?",
            (json.dumps(metadata, sort_keys=True), key),
        )


def test_artifact_register_is_durable_and_idempotent_across_store_reopen(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "shape.step"
    source.write_bytes(b"ISO-10303-21; bounded fixture\n")
    try:
        first = _register(daemon)
        second = _register(daemon, key="artifact-register-2", request_id="register-request-2")
        replay = _register(daemon)
        assert first["success"] is True
        assert second["success"] is True
        assert replay["success"] is True
        assert first["data"]["artifact_id"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert second["data"]["reused"] is True
        assert replay["data"] == first["data"]
        artifact_id = first["data"]["artifact_id"]
        saved_path = daemon.home / "operations.sqlite3"
    finally:
        daemon.close()

    reopened = OperationStore(saved_path)
    try:
        record = reopened.get_metadata("artifacts", artifact_id)
        assert record["schema_version"] == 2
        assert record["project_id"] == daemon._test_project_id
        assert record["path"] == f"g2_artifacts/registered/{artifact_id}.step"
        assert record["provenance"]["source_project_relative_path"] == "inputs/shape.step"
        resolved = resolve_registered_artifact(
            project_root,
            reopened,
            artifact_id,
            project_id=daemon._test_project_id,
            current_host_identity=local_artifact_host_identity(),
            current_engine_host_identity=local_engine_host_identity("127.0.0.1:2036"),
            current_server_instance_id="worker-restarted-server-instance",
        )
        assert resolved["path"].read_bytes() == source.read_bytes()
        assert resolved["relative_path"] == record["path"]
    finally:
        reopened.close()


def test_artifact_register_without_managed_service_is_retryable_failed_not_unknown(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    (project_root / "inputs" / "not-registered.step").write_bytes(b"offline refusal fixture")
    daemon.backend.service = None
    try:
        response = _register(daemon, path="inputs/not-registered.step",
                             key="missing-service-key", request_id="missing-service-request")
        assert response["success"] is False
        assert response["error"]["code"] == "ENGINE_UNRESPONSIVE"
        assert response["error"]["safe_retry"] is True
        assert response["error"]["stage"] == "validation"
        job_id = response["execution"]["job_id"]
        job = daemon.store.job(job_id)
        assert job["status"] == "FAILED"
        assert job["result"]["error"]["code"] == "ENGINE_UNRESPONSIVE"
        assert job["result"]["error"]["safe_retry"] is True
        assert daemon.store.list_metadata("artifacts") == []
        assert not any(event["event"] == "worker_request"
                       for event in daemon.store.events(job_id, offset=0, limit=100))
    finally:
        daemon.close()


def test_legacy_artifact_writers_cannot_overwrite_registered_schema_v2(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "protected.mph"
    source.write_bytes(b"immutable registered model bytes")
    try:
        registered = _register(daemon, path="inputs/protected.mph")
        artifact_id = registered["data"]["artifact_id"]
        record = daemon.store.get_metadata("artifacts", artifact_id)
        assert record["schema_version"] == 2

        legacy_output = {
            "path": str(project_root / "g2_artifacts" / "saved.mph"),
            "sha256": artifact_id,
            "size": source.stat().st_size,
            "model_ref": {"model_tag": "saved-model"},
        }
        with pytest.raises(ValueError, match="registered artifact metadata is immutable"):
            daemon.store.persist_artifact(artifact_id, legacy_output)
        with pytest.raises(ValueError, match="registered artifact metadata is immutable"):
            daemon.store.put_metadata("artifacts", artifact_id, legacy_output)

        assert daemon.store.get_metadata("artifacts", artifact_id) == record
        # Identical compatibility reuse is a no-op and leaves provenance intact.
        daemon.store.persist_artifact(artifact_id, record)
        assert daemon.store.get_metadata("artifacts", artifact_id) == record
    finally:
        daemon.close()


def test_artifact_register_rejects_idempotency_replay_with_different_body(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        (project_root / "inputs" / "one.step").write_bytes(b"one")
        (project_root / "inputs" / "two.step").write_bytes(b"two")
        first = _register(daemon, path="inputs/one.step", key="same-key", request_id="same-request")
        conflict = _register(daemon, path="inputs/two.step", key="same-key", request_id="same-request")
        assert first["success"] is True
        assert conflict["success"] is False
        assert _error_code(conflict) in {"IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_KEY_REUSED"}
        assert len(daemon.store.list_metadata("artifacts")) == 1
    finally:
        daemon.close()


@pytest.mark.parametrize("outer_operation", ["registry_call", "operation_call"])
def test_artifact_register_uses_canonical_nested_schema_and_outer_idempotency(tmp_path, outer_operation):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "nested.step"
    source.write_bytes(b"nested route geometry")
    try:
        key = f"{outer_operation}-idempotency"
        request_id = f"{outer_operation}-request"
        response = daemon.dispatch({
            "operation": outer_operation,
            "arguments": {
                "operation_id": "artifact.register",
                "arguments": {
                    "path": "inputs/nested.step",
                    "role": "geometry_source",
                    "classification": "internal",
                },
            },
            "execution": {"project_id": daemon._test_project_id, "idempotency_key": key, "request_id": request_id},
        })
        assert response["success"] is True
        assert response["data"]["artifact_id"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert daemon.store.get_metadata("artifacts", response["data"]["artifact_id"])["project_id"] == daemon._test_project_id

        malformed = daemon.dispatch({
            "operation": outer_operation,
            "arguments": {
                "operation_id": "artifact.register",
                "arguments": {"path": "inputs/nested.step", "classification": "internal"},
            },
            "execution": {"project_id": daemon._test_project_id, "idempotency_key": key + "-invalid",
                          "request_id": request_id + "-invalid"},
        })
        assert malformed["success"] is False
        assert _error_code(malformed) == "INVALID_REQUEST"
        assert len(daemon.store.list_metadata("artifacts")) == 1
    finally:
        daemon.close()


def test_concurrent_same_digest_registration_is_compare_and_insert(tmp_path):
    project_root = tmp_path / "project"
    (project_root / "inputs").mkdir(parents=True)
    source = project_root / "inputs" / "shared.step"
    source.write_bytes(b"one immutable shared geometry file")
    database = tmp_path / "operation-store.sqlite3"
    stores = [OperationStore(database), OperationStore(database)]
    initial_lookup_barrier = Barrier(2)

    class _BarrierStore:
        def __init__(self, store):
            self.store = store

        def get_metadata(self, table, key):
            value = self.store.get_metadata(table, key)
            if table == "artifacts" and value is None:
                initial_lookup_barrier.wait(timeout=3)
            return value

        def register_artifact_if_absent(self, key, metadata):
            return self.store.register_artifact_if_absent(key, metadata)

    host = local_artifact_host_identity()
    engine_host = local_engine_host_identity("127.0.0.1:2036")

    def register(index, role):
        try:
            return register_project_artifact(
                project_root,
                _BarrierStore(stores[index]),
                "inputs/shared.step",
                project_id="project-test",
                role=role,
                classification="internal",
                host_identity=host,
                engine_host_identity=engine_host,
                request_id=f"concurrent-request-{index}",
                registering_operation_id=f"concurrent-operation-{index}",
            )
        except ExecutionContractError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(register, 0, "geometry_source"),
                       pool.submit(register, 1, "cad_geometry")]
            results = [future.result(timeout=10) for future in futures]
        successes = [value for value in results if isinstance(value, dict)]
        conflicts = [value for value in results if isinstance(value, str)]
        assert len(successes) == 1
        assert conflicts == ["ARTIFACT_REGISTRATION_CONFLICT"]
        artifact_id = hashlib.sha256(source.read_bytes()).hexdigest()
        row = stores[0].get_metadata("artifacts", artifact_id)
        assert row["role"] == successes[0]["role"]
        managed = project_root / row["path"]
        assert managed.read_bytes() == source.read_bytes()
        assert stores[0].get_metadata("artifacts", artifact_id) == row
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("path", ["../outside.step", "/etc/hosts"])
def test_artifact_register_rejects_paths_outside_project(tmp_path, path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        source = project_root / "inputs" / "shape.step"
        source.write_bytes(b"approved source")
        result = _register(daemon, path=path, key="outside-key", request_id="outside-request")
        assert result["success"] is False
        assert _error_code(result) in {"ACCESS_VIOLATION", "INVALID_REQUEST"}
        assert daemon.store.list_metadata("artifacts") == []
    finally:
        daemon.close()


def test_artifact_register_rejects_symlink_escape_without_record(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    outside = tmp_path / "outside.step"
    outside.write_bytes(b"outside content")
    link = project_root / "inputs" / "escape.step"
    link.symlink_to(outside)
    try:
        result = _register(daemon, path="inputs/escape.step", key="symlink-key", request_id="symlink-request")
        assert result["success"] is False
        assert _error_code(result) in {"ACCESS_VIOLATION", "ARTIFACT_NOT_FOUND"}
        assert daemon.store.list_metadata("artifacts") == []
    finally:
        daemon.close()


def test_artifact_register_rejects_symlinked_managed_directory_without_external_write(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    (project_root / "inputs" / "shape.step").write_bytes(b"approved source")
    outside_dir = tmp_path / "outside-managed"
    outside_dir.mkdir()
    (project_root / "g2_artifacts").symlink_to(outside_dir, target_is_directory=True)
    try:
        result = _register(daemon)
        assert result["success"] is False
        assert _error_code(result) == "ACCESS_VIOLATION"
        assert list(outside_dir.iterdir()) == []
        assert daemon.store.list_metadata("artifacts") == []
    finally:
        daemon.close()


def test_artifact_resolver_rejects_project_host_path_and_content_tampering(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "shape.step"
    source.write_bytes(b"registered bytes")
    try:
        result = _register(daemon)
        artifact_id = result["data"]["artifact_id"]
        base = {
            "project_root": project_root,
            "operation_store": daemon.store,
            "artifact_id": artifact_id,
            "current_host_identity": local_artifact_host_identity(),
            "current_engine_host_identity": local_engine_host_identity("127.0.0.1:2036"),
            "current_server_instance_id": "new-worker-epoch",
        }
        with pytest.raises(ExecutionContractError) as wrong_project:
            resolve_registered_artifact(**base, project_id="other-project")
        assert wrong_project.value.code == "ARTIFACT_SCOPE_MISMATCH"
        with pytest.raises(ExecutionContractError) as wrong_host:
            resolve_registered_artifact(**{
                **base,
                "project_id": daemon._test_project_id,
                "current_engine_host_identity": "another-engine-host",
            })
        assert wrong_host.value.code == "ARTIFACT_SCOPE_MISMATCH"

        record = daemon.store.get_metadata("artifacts", artifact_id)
        tampered = dict(record)
        tampered["path"] = "inputs/shape.step"
        _inject_artifact_metadata_for_test(daemon.store, artifact_id, tampered)
        with pytest.raises(ExecutionContractError):
            resolve_registered_artifact(**base, project_id=daemon._test_project_id)

        _inject_artifact_metadata_for_test(daemon.store, artifact_id, record)
        managed = project_root / record["path"]
        managed.write_bytes(b"changed content")
        with pytest.raises(ExecutionContractError) as changed:
            resolve_registered_artifact(**base, project_id=daemon._test_project_id)
        assert changed.value.code in {"ARTIFACT_HASH_MISMATCH", "ARTIFACT_IDENTITY_MISMATCH"}
    finally:
        daemon.close()


def test_artifact_resolver_rejects_managed_path_symlink_escape(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "shape.step"
    source.write_bytes(b"registered bytes")
    outside = tmp_path / "outside.step"
    outside.write_bytes(source.read_bytes())
    try:
        result = _register(daemon)
        record = daemon.store.get_metadata("artifacts", result["data"]["artifact_id"])
        managed = project_root / record["path"]
        managed.unlink()
        managed.symlink_to(outside)
        with pytest.raises(ExecutionContractError) as escaped:
            resolve_registered_artifact(
                project_root,
                daemon.store,
                record["artifact_id"],
                project_id=daemon._test_project_id,
                current_host_identity=local_artifact_host_identity(),
                current_engine_host_identity=local_engine_host_identity("127.0.0.1:2036"),
                current_server_instance_id="new-worker-epoch",
            )
        assert escaped.value.code == "ACCESS_VIOLATION"
    finally:
        daemon.close()


def test_geometry_import_receives_only_resolved_managed_path_and_returns_digest(tmp_path, monkeypatch):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "shape.step"
    source.write_bytes(b"ISO-10303-21; managed geometry fixture\n")
    outside = tmp_path / "caller-selected.step"
    outside.write_bytes(b"must not be probed")
    try:
        registered = _register(daemon)
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        home = daemon.home
        project_id = daemon._test_project_id
        project_container = daemon._test_project_container
        daemon.close()
        daemon = None

        # Reopen the same project ledger and bind a fresh Worker epoch. The
        # artifact registration is project/host scoped, so it survives this
        # process restart without inheriting the previous server_instance_id.
        service = ExecutionService(SessionLedger("session-restarted", "server-restarted"),
                                   _SnapshotAdapter("server-restarted"), project_root=project_container)
        daemon = ControlDaemon(home, service=service, registry={}, worker=_Worker(), project_root=project_container)
        daemon._test_project_id = project_id
        daemon._test_project_container = project_container
        daemon.backend.endpoint_key = "127.0.0.1:2036"
        daemon.backend.worker_identity = {
            "runtime_id": "artifact-test-runtime-restarted",
            "worker_instance_id": "artifact-test-worker-restarted",
            "connection_epoch": 2,
            "server_instance_id": "server-restarted",
            "endpoint": "127.0.0.1:2036",
        }
        adopted = daemon.dispatch({
            "operation": "model.adopt",
            "arguments": {"server_model_tag": "m1"},
            "execution": {
                "project_id": project_id,
                "session_id": "session-restarted",
                "request_id": "adopt-restarted-artifact-model",
                "idempotency_key": "adopt-restarted-artifact-model",
            },
        })
        assert adopted["success"] is True
        model_execution = adopted["execution"]
        observed = []

        def fake_geometry_import(_worker, _model_tag, args):
            observed.append(dict(args))
            return {
                "success": True,
                "data": {"properties": {"type": "Import"}},
                "artifact_id": args["artifact_id"],
                "artifact_path_verbatim": True,
                "local_probe": {"path": args["options"]["local_path"], "is_file": True},
                "status": {"ok": True, "failed": False, "partial": False},
            }

        monkeypatch.setitem(_g3_ops.DISPATCH, "geometry.import", fake_geometry_import)
        monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
        bound = model_execution["model_ref"]
        nested_execution = {
            "project_id": daemon._test_project_id,
            "session_id": model_execution["session_id"],
            "model_ref": bound,
            "expected_revision": model_execution["revision"],
            "idempotency_key": "geometry-import-key",
            "request_id": "geometry-import-request",
        }
        operation_args = {
            "geometry": {"component": "comp1", "geometry": "geom1"},
            "tag": "import1",
            "artifact_id": artifact_id,
            "options": {"local_path": str(outside), "build": False},
        }
        envelope = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "geometry.import", "arguments": operation_args},
            "execution": nested_execution,
        })
        managed_path = project_root / registered["data"]["path"]
        assert envelope["success"] is True
        assert observed[0]["artifact_id"] == str(managed_path)
        assert observed[0]["options"]["local_path"] == str(managed_path)
        assert observed[0]["options"]["path_check"] == "local"
        assert envelope["data"]["artifact_id"] == artifact_id
        assert envelope["data"]["artifact_path_verbatim"] is False
        assert envelope["data"]["artifact_registration"]["path"] == registered["data"]["path"]
        assert "path" not in envelope["data"]["local_probe"]
        assert str(managed_path) not in json.dumps(envelope, sort_keys=True)

        # The direct canonical action passes the same strict catalog schema
        # and resolver, with model/project identity pinned in the envelope.
        current_revision = envelope["execution"]["revision"]
        direct_execution = {**nested_execution, "expected_revision": current_revision,
                            "idempotency_key": "geometry-import-direct-key",
                            "request_id": "geometry-import-direct-request"}
        direct_arguments = {
            **operation_args,
            "project_id": daemon._test_project_id,
            "session_id": direct_execution["session_id"],
            "model_ref": direct_execution["model_ref"],
            "expected_revision": current_revision,
            "idempotency_key": direct_execution["idempotency_key"],
            "request_id": direct_execution["request_id"],
        }
        direct = daemon.dispatch({"operation": "geometry.import", "arguments": direct_arguments,
                                  "execution": direct_execution})
        assert direct["success"] is True
        assert observed[1]["artifact_id"] == str(managed_path)
        assert direct["data"]["artifact_id"] == artifact_id
    finally:
        if daemon is not None:
            daemon.close()


def test_geometry_import_refuses_nonlocal_engine_before_domain_dispatch(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    (project_root / "inputs" / "shape.step").write_bytes(b"managed geometry")
    try:
        registered = _register(daemon)
        calls = []
        monkeypatch.setitem(_g3_ops.DISPATCH, "geometry.import", lambda *_args: calls.append(1) or {"success": True})
        monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
        daemon.backend.endpoint_key = "192.0.2.9:2036"
        response = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "geometry.import", "arguments": {
                "geometry": {"component": "comp1", "geometry": "geom1"},
                "tag": "import1", "artifact_id": registered["data"]["artifact_id"],
            }},
            "execution": {
                "project_id": daemon._test_project_id,
                "session_id": model_execution["session_id"],
                "model_ref": model_execution["model_ref"],
                "expected_revision": model_execution["revision"],
                "idempotency_key": "remote-import-key",
                "request_id": "remote-import-request",
            },
        })
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_HOST_UNVERIFIED"
        assert calls == []
    finally:
        daemon.close()


def test_artifact_list_uses_scoped_keyset_pages_without_file_reads_or_row_writes(tmp_path, monkeypatch):
    from comsol_mcp import _artifact_store

    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        expected = []
        for index, (role, classification) in enumerate((
            ("geometry_source", "internal"),
            ("geometry_source", "internal"),
            ("mesh_source", "internal"),
            ("geometry_source", "restricted"),
        )):
            relative = f"inputs/list-{index}.step"
            (project_root / relative).write_bytes(f"list fixture {index}".encode())
            registered = _register_for_project(
                daemon, daemon._test_project_id, path=relative, role=role,
                classification=classification, key=f"list-register-{index}",
            )
            assert registered["success"] is True
            if classification == "internal":
                expected.append(registered["data"]["artifact_id"])

        with daemon.store.lock:
            daemon.store.db.execute(
                "INSERT INTO artifacts(artifact_id,metadata) VALUES(?,?)",
                ("f" * 64, "{malformed-json"),
            )
        daemon.backend.service = None
        daemon.backend.worker = None
        assert daemon.backend.service is None and daemon.backend.worker is None
        other_project = _create_project(daemon, workspace="other-artifact-project", label="other-artifact-project")

        with monkeypatch.context() as patcher:
            patcher.setattr(
                _artifact_store, "resolve_registered_artifact",
                lambda *_args, **_kwargs: pytest.fail("artifact.list must not open or hash artifact files"),
            )
            patcher.setattr(
                daemon.store, "list_metadata",
                lambda *_args, **_kwargs: pytest.fail("artifact.list must not fall back to a table scan"),
            )
            baseline = _store_rows_for_read_assertion(daemon.store)
            filters = {"classification": "internal"}
            first = _read_action(
                daemon, "artifact.list", daemon._test_project_id,
                {"filter": filters, "limit": 1}, request_id="list-page-1",
            )
            assert first["success"] is True
            first_data = first["data"]
            assert first_data["verification_scope"] == "METADATA_ONLY"
            assert first_data["request_id"] == "list-page-1"
            assert first_data["has_more"] is True
            assert first_data["items"][0]["verification_status"] == "NOT_CHECKED"
            assert first_data["items"][0]["producer_job_status"] == "NOT_LOOKED_UP"
            assert first_data["items"][0]["version_status"] == "NOT_RECORDED"
            cursor = first_data["next_cursor"]
            raw_cursor = __import__("base64").urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            cursor_payload = json.loads(raw_cursor)
            assert set(cursor_payload) == {
                "schema", "project_id", "scope_sha256", "filter_sha256", "after_artifact_id",
            }
            assert "project_root_identity" not in cursor_payload
            assert "host_identity" not in cursor_payload
            assert "engine_host_identity" not in cursor_payload
            assert str(project_root).encode() not in raw_cursor
            assert b"127.0.0.1:2036" not in raw_cursor

            second = _read_action(
                daemon, "artifact.list", daemon._test_project_id,
                {"filter": filters, "limit": 1, "cursor": cursor}, entrypoint="registry_call",
            )
            assert second["success"] is True
            assert second["data"]["has_more"] is True
            third = _read_action(
                daemon, "artifact.list", daemon._test_project_id,
                {"filter": filters, "limit": 1, "cursor": second["data"]["next_cursor"]},
                entrypoint="operation_call",
            )
            assert third["success"] is True
            assert third["data"]["has_more"] is False
            observed = [item["artifact_id"] for response in (first, second, third) for item in response["data"]["items"]]
            assert observed == sorted(expected)
            assert "f" * 64 not in observed
            assert str(project_root) not in json.dumps([first, second, third], sort_keys=True)

            changed_filter = _read_action(
                daemon, "artifact.list", daemon._test_project_id,
                {"filter": {"classification": "restricted"}, "limit": 1, "cursor": cursor},
            )
            assert changed_filter["success"] is False
            assert _error_code(changed_filter) == "INVALID_CURSOR"

            foreign_cursor = _read_action(
                daemon, "artifact.list", other_project["project_id"],
                {"filter": filters, "limit": 1, "cursor": cursor}, entrypoint="operation_call",
            )
            assert foreign_cursor["success"] is False
            assert _error_code(foreign_cursor) == "INVALID_CURSOR"

            with monkeypatch.context() as changed_root:
                changed_root.setattr(_artifact_store, "project_root_identity", lambda _path: "changed-root-scope")
                stale_root = _read_action(
                    daemon, "artifact.list", daemon._test_project_id,
                    {"filter": filters, "limit": 1, "cursor": cursor},
                )
            assert stale_root["success"] is False
            assert _error_code(stale_root) == "INVALID_CURSOR"

            with monkeypatch.context() as changed_host:
                changed_host.setattr(_artifact_store, "local_artifact_host_identity", lambda: "changed-host-scope")
                stale_host = _read_action(
                    daemon, "artifact.list", daemon._test_project_id,
                    {"filter": filters, "limit": 1, "cursor": cursor},
                )
            assert stale_host["success"] is False
            assert _error_code(stale_host) == "INVALID_CURSOR"
            assert _store_rows_for_read_assertion(daemon.store) == baseline
    finally:
        daemon.close()


def test_artifact_inspect_is_host_only_scoped_and_reports_registration_provenance(tmp_path, monkeypatch):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "inspect.step"
    source.write_bytes(b"synthetic inspect content")
    try:
        registered = _register(daemon, path="inputs/inspect.step")
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        daemon.backend.service = None
        daemon.backend.worker = None
        with monkeypatch.context() as patcher:
            patcher.setattr(
                daemon.store, "list_metadata",
                lambda *_args, **_kwargs: pytest.fail("artifact.inspect must use the exact scoped lookup"),
            )
            baseline = _store_rows_for_read_assertion(daemon.store)
            direct = _read_action(
                daemon, "artifact.inspect", daemon._test_project_id,
                {"artifact_id": artifact_id}, request_id="inspect-direct",
            )
            nested = _read_action(
                daemon, "artifact.inspect", daemon._test_project_id,
                {"artifact_id": artifact_id}, entrypoint="registry_call",
            )
            operation = _read_action(
                daemon, "artifact.inspect", daemon._test_project_id,
                {"artifact_id": artifact_id}, entrypoint="operation_call",
            )
            for response in (direct, nested, operation):
                assert response["success"] is True
                assert response["data"]["sha256"] == artifact_id
                assert response["data"]["size_bytes"] == len(source.read_bytes())
                assert response["data"]["relative_path"] == f"g2_artifacts/registered/{artifact_id}.step"
                assert response["data"]["file_integrity"]["status"] == "VERIFIED_CONTENT_AND_PINNED_IDENTITY"
                assert response["data"]["producer_job"]["status"] == "SUCCEEDED"
                assert isinstance(response["data"]["producer_job"]["job_id"], str)
                assert response["data"]["package_status"] == "NOT_VERIFIED"
                assert response["data"]["format_version_status"] == "NOT_RECORDED"
                assert str(project_root) not in json.dumps(response, sort_keys=True)
            assert direct["data"]["request_id"] == "inspect-direct"
            assert _store_rows_for_read_assertion(daemon.store) == baseline

        # A listed metadata row with a scoped but malformed payload fails closed;
        # the read must not repair it or fall back to a global metadata scan.
        record = daemon.store.get_metadata("artifacts", artifact_id)
        corrupted = dict(record)
        corrupted.pop("size")
        _inject_artifact_metadata_for_test(daemon.store, artifact_id, corrupted)
        corrupted_rows = _store_rows_for_read_assertion(daemon.store)
        broken = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": artifact_id},
        )
        assert broken["success"] is False
        assert _error_code(broken) in {"ARTIFACT_STATE_UNKNOWN", "ARTIFACT_IDENTITY_MISMATCH"}
        assert _store_rows_for_read_assertion(daemon.store) == corrupted_rows
    finally:
        daemon.close()


def test_artifact_inspect_checks_permission_before_scoped_metadata_lookup(tmp_path, monkeypatch):
    daemon, project_root, _ = _make_daemon(tmp_path)
    (project_root / "inputs" / "permission.step").write_bytes(b"permission fixture")
    try:
        registered = _register(daemon, path="inputs/permission.step")
        assert registered["success"] is True
        daemon.project_authority.permission_provider = lambda: set()
        monkeypatch.setattr(
            daemon.store, "get_project_artifact_metadata",
            lambda *_args, **_kwargs: pytest.fail("artifact metadata lookup occurred before permission denial"),
        )
        response = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": registered["data"]["artifact_id"]},
        )
        assert response["success"] is False
        assert _error_code(response) == "PERMISSION_DENIED"
    finally:
        daemon.close()


def test_artifact_inspect_rejects_same_hash_registered_to_another_project(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "same-bytes.step"
    source.write_bytes(b"same immutable bytes")
    try:
        first = _register(daemon, path="inputs/same-bytes.step")
        assert first["success"] is True
        other = _create_project(daemon, workspace="artifact-owner-two", label="artifact-owner-two")
        (Path(other["workspace"]) / "inputs" / "same-bytes.step").write_bytes(source.read_bytes())
        second = _register_for_project(
            daemon, other["project_id"], path="inputs/same-bytes.step", key="same-bytes-other-project",
        )
        assert second["success"] is False
        assert _error_code(second) == "ARTIFACT_REGISTRATION_CONFLICT"
        foreign = _read_action(
            daemon, "artifact.inspect", other["project_id"],
            {"artifact_id": first["data"]["artifact_id"]},
        )
        forged = _read_action(
            daemon, "artifact.inspect", other["project_id"],
            {"artifact_id": "a" * 64},
        )
        assert foreign["success"] is False and forged["success"] is False
        assert _error_code(foreign) == _error_code(forged) == "ARTIFACT_NOT_FOUND"
        assert foreign["error"]["message"] == forged["error"]["message"]
    finally:
        daemon.close()


def test_artifact_read_preserves_safe_mime_namespaced_and_freeform_metadata(tmp_path):
    from jsonschema import validate
    from comsol_mcp._g2_registry import registry_describe

    daemon, project_root, _ = _make_daemon(tmp_path)
    (project_root / "inputs" / "metadata.step").write_bytes(b"metadata fixture")
    try:
        registered = _register_for_project(
            daemon, daemon._test_project_id, path="inputs/metadata.step",
            role="geometry/source v2", classification="internal review",
            key="metadata-safe-values",
        )
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        record = daemon.store.get_metadata("artifacts", artifact_id)
        _inject_artifact_metadata_for_test(daemon.store, artifact_id, {
            **record,
            "artifact_type": "application/octet-stream",
            "format_version": "comsol-mcp-full-release/1",
        })
        listed = _read_action(daemon, "artifact.list", daemon._test_project_id, {"limit": 1})
        inspected = _read_action(daemon, "artifact.inspect", daemon._test_project_id, {"artifact_id": artifact_id})
        assert listed["success"] is True
        assert inspected["success"] is True
        item = listed["data"]["items"][0]
        assert item["role"] == "geometry/source v2"
        assert item["classification"] == "internal review"
        assert item["version_status"] == "DECLARED"
        assert "format_version_status" not in item
        assert inspected["data"]["type_hint"] == {"value": "application/octet-stream", "status": "DECLARED"}
        assert inspected["data"]["format_version"] == "comsol-mcp-full-release/1"
        assert inspected["data"]["format_version_status"] == "DECLARED"
        validate(instance=listed["data"], schema=registry_describe("artifact.list")["data_schema"])
        validate(instance=inspected["data"], schema=registry_describe("artifact.inspect")["data_schema"])
    finally:
        daemon.close()


@pytest.mark.parametrize("field", ["artifact_type", "format_version", "role", "classification"])
def test_artifact_reads_reject_absolute_pathlike_metadata_without_disclosure(tmp_path, field):
    daemon, project_root, _ = _make_daemon(tmp_path)
    (project_root / "inputs" / "private-metadata.step").write_bytes(b"private metadata fixture")
    try:
        registered = _register(daemon, path="inputs/private-metadata.step", key=f"bad-{field}")
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        record = daemon.store.get_metadata("artifacts", artifact_id)
        absolute_value = str(tmp_path / "private-source-path")
        _inject_artifact_metadata_for_test(daemon.store, artifact_id, {**record, field: absolute_value})
        for operation, arguments in (
            ("artifact.inspect", {"artifact_id": artifact_id}),
            ("artifact.list", {"limit": 200}),
        ):
            response = _read_action(daemon, operation, daemon._test_project_id, arguments)
            assert response["success"] is False
            assert _error_code(response) == "ARTIFACT_STATE_UNKNOWN"
            assert absolute_value not in json.dumps(response, sort_keys=True)
            assert str(project_root) not in json.dumps(response, sort_keys=True)
    finally:
        daemon.close()


class _ParameterNode:
    def __init__(self, values):
        self.values = {name: dict(row) for name, row in values.items()}
        self.setter_calls = 0
        self.fail_after_set_name = None
        self.unit_error_name = None
        self.description_error_name = None

    def varnames(self):
        return list(self.values)

    def get(self, name):
        return self.values[name]["expression"]

    def descr(self, name):
        if self.description_error_name == name:
            raise RuntimeError("synthetic description getter failure")
        return self.values[name]["description"]

    def set(self, name, expression, description=""):
        self.setter_calls += 1
        self.values[name] = {"expression": expression, "description": description}
        if self.fail_after_set_name == name:
            raise RuntimeError("synthetic setter failure after mutation")

    def _unit_readback(self, name):
        if self.unit_error_name == name:
            raise RuntimeError("synthetic unit readback failure")
        expression = self.values[name]["expression"]
        if expression == "3[m]":
            return "m"
        if expression == "B+1":
            return self._unit_readback("B")
        return None


class _ParameterGroupList:
    def __init__(self, groups):
        self.groups = groups

    def tags(self):
        return list(self.groups)


class _ParameterCollection:
    def __init__(self):
        # A precedes B in the exported rows and references B. Unit verification
        # must therefore happen after the complete import setter batch.
        self.groups = {
            "default": _ParameterNode({"base": {"expression": "2", "description": ""}}),
            "phys": _ParameterNode({
                "A": {"expression": "B+1", "description": "温度 🚀"},
                "B": {"expression": "3[m]", "description": ""},
            }),
        }

    def evaluateUnit(self, name):
        # The public COMSOL 6.4 ParamBase evaluator belongs to ModelParam;
        # group setters/getters implement ExpressionBase only.
        for group in self.groups.values():
            if name in group.values:
                return group._unit_readback(name)
        raise KeyError(name)

    def group(self, tag=None):
        return _ParameterGroupList(self.groups) if tag is None else self.groups[tag]


class _ParameterModel:
    def __init__(self):
        self._parameters = _ParameterCollection()
        self.param_calls = 0

        class _Java:
            @staticmethod
            def tag():
                return "m1"

            @staticmethod
            def getFilePath():
                return ""

        self.java = _Java()

    def param(self):
        self.param_calls += 1
        return self._parameters

    def group(self, tag):
        return self._parameters.group_node(tag)


class _ParameterWorker:
    generation = 1

    def __init__(self):
        self.model = _ParameterModel()

    class _Client:
        def __init__(self, model):
            self._model = model

        def model(self, tag):
            assert tag == "m1"
            return self._model

    def client(self):
        return self._Client(self.model)

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def runtime_metadata(self):
        return {"pid": 10101}


def _parameter_action(daemon, operation, model_execution, arguments, *, key, request_id, entrypoint="operation_call"):
    execution = {
        "project_id": daemon._test_project_id,
        "session_id": model_execution["session_id"],
        "model_ref": model_execution["model_ref"],
        "expected_revision": model_execution["revision"],
        "idempotency_key": key,
        "request_id": request_id,
    }
    if entrypoint == "direct":
        body = {
            "project_id": daemon._test_project_id,
            "session_id": model_execution["session_id"],
            "model_ref": model_execution["model_ref"],
            "expected_revision": model_execution["revision"],
            "idempotency_key": key,
            "request_id": request_id,
            **arguments,
        }
        return daemon.dispatch({"operation": operation, "arguments": body, "execution": execution})
    return daemon.dispatch({
        "operation": entrypoint,
        "arguments": {"operation_id": operation, "arguments": arguments},
        "execution": execution,
    })


def _register_parameter_table(daemon, project_root, document, *, role="parameter_source", key="parameter-source"):
    source = project_root / "inputs" / f"{key}.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return _register_for_project(
        daemon, daemon._test_project_id, path=source.relative_to(project_root).as_posix(),
        role=role, classification="project_private", key=key,
    )


def _enable_synthetic_parameter_isolation(monkeypatch):
    from comsol_mcp import _managed_backend

    monkeypatch.setattr(_managed_backend, "configured_receipt", lambda: "TEST_ONLY_RECEIPT")
    monkeypatch.setattr(
        _managed_backend, "verify_owned_server",
        lambda receipt, *, endpoint, worker_pid: {
            "status": "VERIFIED_TEST_ONLY", "endpoint": endpoint, "worker_pid": worker_pid,
        },
    )


def test_parameter_io_connection_epoch_two_accepts_model_incarnation_one(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    worker.generation = 2
    daemon.backend.worker = worker
    daemon.backend.worker_identity["connection_epoch"] = 2
    _enable_synthetic_parameter_isolation(monkeypatch)
    try:
        assert model_execution["model_ref"]["generation"] == 1
        result = _parameter_action(daemon, "parameter.export", model_execution,
                                  {"format": "json", "destination": "epoch-two.json"},
                                  key="export-epoch-two", request_id="export-epoch-two")
        assert result["success"] is True, result
        assert result["data"]["artifact_id"] == hashlib.sha256((project_root / "epoch-two.json").read_bytes()).hexdigest()
    finally:
        daemon.close()


def test_parameter_io_epoch_change_during_native_getters_refuses_publication(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    worker.generation = 2
    daemon.backend.worker = worker
    daemon.backend.worker_identity["connection_epoch"] = 2
    _enable_synthetic_parameter_isolation(monkeypatch)
    original = worker.model._parameters.evaluateUnit

    def epoch_changed(name):
        value = original(name)
        worker.generation = 3
        # Coordinated changes to the mutable dictionary cannot change the
        # private epoch already captured for this production callback.
        daemon.backend.worker_identity["connection_epoch"] = 3
        return value

    monkeypatch.setattr(worker.model._parameters, "evaluateUnit", epoch_changed)
    try:
        result = _parameter_action(daemon, "parameter.export", model_execution,
                                  {"format": "json", "destination": "epoch-changed.json"},
                                  key="export-epoch-change", request_id="export-epoch-change")
        assert result["success"] is False and _error_code(result) == "EXECUTION_STATE_UNKNOWN"
        assert not (project_root / "epoch-changed.json").exists()
    finally:
        daemon.close()


def test_parameter_io_uses_managed_roundtrip_for_all_groups_json_csv_and_units(tmp_path, monkeypatch):
    from jsonschema import validate
    from comsol_mcp import _managed_backend
    from comsol_mcp._g2_registry import registry_describe

    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    # The managed server-owned isolation check remains in the production route;
    # this fixture supplies explicit synthetic owned-server evidence only.
    _enable_synthetic_parameter_isolation(monkeypatch)
    try:
        (project_root / "exports").mkdir()
        exported_json = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": "exports/parameters.json"},
            key="parameter-export-json", request_id="parameter-export-json-request",
        )
        assert exported_json["success"] is True
        data = exported_json["data"]
        validate(instance=data, schema=registry_describe("parameter.export")["data_schema"])
        assert data["format_version"] == "comsol-mcp-parameter-table/1"
        assert data["groups"] == ["default", "phys"]
        assert data["row_count"] == 3
        assert exported_json["execution"]["revision"] == model_execution["revision"] + 1
        assert data["artifact_id"] == hashlib.sha256(
            (project_root / "exports" / "parameters.json").read_bytes()
        ).hexdigest()
        source_artifact = data["artifact_id"]
        source_document = json.loads((project_root / "exports" / "parameters.json").read_text("utf-8"))
        assert source_document["schema"] == "comsol-mcp-parameter-table/1"
        assert source_document["rows"][1]["description"] == "温度 🚀"
        assert source_document["rows"][0]["unit"] is None

        # A later CSV export exercises the same bounded projection and
        # unambiguous JSON-scalar null encoding for CSV group/unit cells.
        next_execution = exported_json["execution"]
        exported_csv = _parameter_action(
            daemon, "parameter.export", next_execution,
            {"format": "csv", "destination": "exports/parameters.csv"},
            key="parameter-export-csv", request_id="parameter-export-csv-request",
            entrypoint="registry_call",
        )
        assert exported_csv["success"] is True
        csv_text = (project_root / "exports" / "parameters.csv").read_text("utf-8")
        assert '"group","name","expression","description","unit","unit_status"' in csv_text
        assert '"null"' in csv_text
        csv_artifact = exported_csv["data"]["artifact_id"]

        # Import the JSON artifact after clearing the synthetic target. This
        # travels through ControlDaemon -> ManagedBackend -> ExecutionService
        # -> G3 -> OperationStore and proves the forward reference's unit after
        # the entire setter batch has completed.
        for node in worker.model._parameters.groups.values():
            node.values.clear()
        current_execution = exported_csv["execution"]
        imported = _parameter_action(
            daemon, "parameter.import", current_execution,
            {"artifact_id": source_artifact, "format": "json"},
            key="parameter-import-json", request_id="parameter-import-json-request",
            entrypoint="direct",
        )
        assert imported["success"] is True
        validate(instance=imported["data"], schema=registry_describe("parameter.import")["data_schema"])
        assert imported["data"]["status"] == "APPLIED"
        assert imported["data"]["source_artifact_id"] == source_artifact
        assert len(imported["data"]["applied"]) == 3
        assert [row["unit_status"] for row in imported["data"]["applied"]] == [
            "NO_UNIT_RETURNED", "VERIFIED", "VERIFIED",
        ]
        assert worker.model._parameters.groups["phys"].values["A"] == {
            "expression": "B+1", "description": "温度 🚀",
        }
        assert csv_artifact != source_artifact
        for node in worker.model._parameters.groups.values():
            node.values.clear()
        imported_csv = _parameter_action(
            daemon, "parameter.import", imported["execution"],
            {"artifact_id": csv_artifact, "format": "csv"},
            key="parameter-import-csv", request_id="parameter-import-csv-request",
        )
        assert imported_csv["success"] is True
        validate(instance=imported_csv["data"], schema=registry_describe("parameter.import")["data_schema"])
        assert imported_csv["data"]["status"] == "APPLIED"
        assert len(imported_csv["data"]["applied"]) == 3
        assert worker.model._parameters.groups["phys"].values["B"] == {
            "expression": "3[m]", "description": "",
        }
        metadata = daemon.store.get_metadata("artifacts", source_artifact)
        assert metadata["role"] == "parameter_source"
        assert metadata["format_version"] == "comsol-mcp-parameter-table/1"
        assert metadata["provenance"]["source_model_ref"] == model_execution["model_ref"]
        assert metadata["provenance"]["source_revision"] == 0
        inspected = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": source_artifact},
        )
        assert inspected["success"] is True
        assert inspected["data"]["sha256"] == source_artifact
        assert inspected["data"]["format_version"] == "comsol-mcp-parameter-table/1"
    finally:
        daemon.close()


def test_parameter_import_rejects_bad_rows_group_collapse_and_wrong_role_before_setters(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    duplicate = {
        "schema": "comsol-mcp-parameter-table/1",
        "groups": ["phys"],
        "rows": [
            {"group": "phys", "name": "A", "expression": "1", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
            {"group": "phys", "name": "A", "expression": "2", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
        ],
    }
    multigroup = {
        "schema": "comsol-mcp-parameter-table/1",
        "groups": ["default", "phys"],
        "rows": [
            {"group": "default", "name": "base", "expression": "1", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
            {"group": "phys", "name": "A", "expression": "2", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
        ],
    }
    try:
        duplicate_source = _register_parameter_table(
            daemon, project_root, duplicate, key="duplicate-parameter-rows",
        )
        assert duplicate_source["success"] is True
        bad_rows = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": duplicate_source["data"]["artifact_id"], "format": "json"},
            key="duplicate-parameter-rows-import", request_id="duplicate-parameter-rows-import-request",
            entrypoint="direct",
        )
        assert bad_rows["success"] is False
        assert _error_code(bad_rows) == "INVALID_REQUEST"
        assert sum(node.setter_calls for node in worker.model._parameters.groups.values()) == 0
        assert worker.model.param_calls == 0

        multi_source = _register_parameter_table(
            daemon, project_root, multigroup, key="multigroup-parameter-rows",
        )
        assert multi_source["success"] is True
        collapsed = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": multi_source["data"]["artifact_id"], "format": "json", "group": "phys"},
            key="multigroup-parameter-collapse", request_id="multigroup-parameter-collapse-request",
        )
        assert collapsed["success"] is False
        assert _error_code(collapsed) == "INVALID_REQUEST"
        assert worker.model.param_calls == 0

        wrong_role_document = dict(multigroup)
        wrong_role_document["rows"] = [dict(row) for row in multigroup["rows"]]
        wrong_role_document["rows"][0]["expression"] = "9"
        wrong_role = _register_parameter_table(
            daemon, project_root, wrong_role_document, role="geometry_source", key="wrong-parameter-role",
        )
        assert wrong_role["success"] is True
        refused = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": wrong_role["data"]["artifact_id"], "format": "json"},
            key="wrong-parameter-role-import", request_id="wrong-parameter-role-import-request",
        )
        assert refused["success"] is False
        assert _error_code(refused) == "ARTIFACT_ROLE_MISMATCH"
        assert worker.model.param_calls == 0
        assert sum(node.setter_calls for node in worker.model._parameters.groups.values()) == 0
    finally:
        daemon.close()


def test_parameter_export_refuses_existing_or_protected_destination_before_engine_getters(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    try:
        (project_root / "exports").mkdir()
        existing = project_root / "exports" / "keep.json"
        existing.write_bytes(b"keep these exact bytes")
        refused_existing = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": "exports/keep.json"},
            key="parameter-export-existing", request_id="parameter-export-existing-request",
            entrypoint="direct",
        )
        assert refused_existing["success"] is False
        assert _error_code(refused_existing) == "DESTINATION_EXISTS"
        assert existing.read_bytes() == b"keep these exact bytes"
        assert worker.model.param_calls == 0

        refused_private = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": ".private/parameters.json"},
            key="parameter-export-private", request_id="parameter-export-private-request",
        )
        assert refused_private["success"] is False
        assert _error_code(refused_private) == "ACCESS_VIOLATION"
        assert worker.model.param_calls == 0
    finally:
        daemon.close()


def test_parameter_export_description_getter_failure_does_not_publish_incomplete_table(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    worker.model._parameters.groups["phys"].description_error_name = "A"
    _enable_synthetic_parameter_isolation(monkeypatch)
    try:
        (project_root / "exports").mkdir()
        response = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": "exports/incomplete.json"},
            key="parameter-export-description-error", request_id="parameter-export-description-error-request",
        )
        assert response["success"] is False
        assert _error_code(response) in {"UNVERIFIED", "ENGINE_CALL_FAILED"}
        assert not (project_root / "exports" / "incomplete.json").exists()
        assert list((project_root / "exports").iterdir()) == []
    finally:
        daemon.close()


def test_parameter_export_race_refuses_to_replace_competing_destination(tmp_path, monkeypatch):
    from comsol_mcp._artifact_store import publish_parameter_table_bytes_no_clobber

    root = tmp_path / "project"
    exports = root / "exports"
    exports.mkdir(parents=True)
    target = exports / "parameters.json"
    competing_bytes = b"concurrent owner bytes"
    real_link = os.link

    def race_create_destination(source, destination, *args, **kwargs):
        Path(exports / destination).write_bytes(competing_bytes)
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", race_create_destination)
    with pytest.raises(ExecutionContractError) as caught:
        publish_parameter_table_bytes_no_clobber(root, "exports/parameters.json", b"our complete table")
    assert caught.value.code == "DESTINATION_EXISTS"
    assert target.read_bytes() == competing_bytes
    assert list(exports.iterdir()) == [target]


def test_parameter_export_exclusive_rename_fallback_is_atomic_and_no_clobber(tmp_path, monkeypatch):
    import errno
    from comsol_mcp._artifact_store import publish_parameter_table_bytes_no_clobber

    root = tmp_path / "project"
    exports = root / "exports"
    exports.mkdir(parents=True)
    target = exports / "parameters.json"
    competing_bytes = b"concurrent owner bytes"

    def race_before_fallback(_source, destination, *_args, **_kwargs):
        (exports / destination).write_bytes(competing_bytes)
        raise OSError(errno.EOPNOTSUPP, "synthetic filesystem without hard-link support")

    monkeypatch.setattr(os, "link", race_before_fallback)
    with pytest.raises(ExecutionContractError) as caught:
        publish_parameter_table_bytes_no_clobber(root, "exports/parameters.json", b"our complete table")
    assert caught.value.code == "DESTINATION_EXISTS"
    assert target.read_bytes() == competing_bytes
    assert list(exports.iterdir()) == [target]


def test_parameter_export_exclusive_rename_fallback_publishes_complete_bytes(tmp_path, monkeypatch):
    import errno
    from comsol_mcp._artifact_store import publish_parameter_table_bytes_no_clobber

    root = tmp_path / "project"
    exports = root / "exports"
    exports.mkdir(parents=True)

    def no_hard_links(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "synthetic filesystem without hard-link support")

    monkeypatch.setattr(os, "link", no_hard_links)
    payload = b"complete fallback payload"
    published = publish_parameter_table_bytes_no_clobber(root, "exports/parameters.json", payload)
    assert (exports / "parameters.json").read_bytes() == payload
    assert published["sha256"] == hashlib.sha256(payload).hexdigest()
    assert published["size_bytes"] == len(payload)
    assert list(exports.iterdir()) == [exports / "parameters.json"]


def test_parameter_export_parent_move_during_descriptor_commit_cleans_escaped_output(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    exports = project_root / "exports"
    exports.mkdir()
    moved_directory = tmp_path / "moved-exports-outside-project"
    replacement_target = tmp_path / "replacement-exports-target"
    replacement_target.mkdir()
    real_link = os.link
    swapped = False

    def move_parent_at_commit(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and kwargs.get("dst_dir_fd") is not None:
            swapped = True
            os.rename(exports, moved_directory)
            os.symlink(replacement_target, exports)
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", move_parent_at_commit)
    try:
        response = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": "exports/escaped.json"},
            key="parameter-export-parent-move", request_id="parameter-export-parent-move-request",
            entrypoint="direct",
        )
        assert swapped is True
        assert response["success"] is False
        assert _error_code(response) == "ACCESS_VIOLATION"
        assert response["error"]["details"]["publication_committed"] is True
        assert response["error"]["details"]["cleanup_verified"] is True
        assert response["error"]["details"]["partial_change"] is True
        assert response["error"]["details"]["final_state"] == "ROLLED_BACK"
        assert response["error"]["details"]["residual_status"] == "ABSENT"
        assert not (moved_directory / "escaped.json").exists()
        assert list(moved_directory.iterdir()) == []
        assert not (replacement_target / "escaped.json").exists()
        assert worker.model.param_calls > 0
        assert response["execution"]["revision"] == model_execution["revision"]
        assert daemon.store.job(response["execution"]["job_id"])["status"] == "FAILED"
        assert daemon.store.list_metadata("artifacts") == []
    finally:
        daemon.close()


def test_parameter_export_parent_move_with_unprovable_cleanup_reports_commit_receipt(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    exports = project_root / "exports"
    exports.mkdir()
    moved_directory = tmp_path / "moved-exports-outside-project"
    replacement_target = tmp_path / "replacement-exports-target"
    replacement_target.mkdir()
    real_link = os.link
    real_unlink = os.unlink
    swapped = False

    def move_parent_at_commit(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and kwargs.get("dst_dir_fd") is not None:
            swapped = True
            os.rename(exports, moved_directory)
            os.symlink(replacement_target, exports)
        return real_link(source, destination, *args, **kwargs)

    def refuse_owned_cleanup(path, *args, **kwargs):
        if path == "escaped.json" and kwargs.get("dir_fd") is not None:
            raise PermissionError("synthetic cleanup refusal")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "link", move_parent_at_commit)
    monkeypatch.setattr(os, "unlink", refuse_owned_cleanup)
    try:
        response = _parameter_action(
            daemon, "parameter.export", model_execution,
            {"format": "json", "destination": "exports/escaped.json"},
            key="parameter-export-parent-move-cleanup-failure",
            request_id="parameter-export-parent-move-cleanup-failure-request",
        )
        assert swapped is True
        assert response["success"] is False
        assert response["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert response["error"]["safe_retry"] is False
        receipt = response["error"]["details"]
        assert receipt["cause_code"] == "ARTIFACT_PUBLISH_UNKNOWN"
        assert receipt["publication_committed"] is True
        assert receipt["cleanup_verified"] is False
        assert receipt["partial_change"] is True
        assert receipt["final_state"] == "UNKNOWN"
        assert receipt["residual_status"] == "UNKNOWN"
        escaped = moved_directory / "escaped.json"
        assert escaped.is_file()
        assert hashlib.sha256(escaped.read_bytes()).hexdigest() == receipt["sha256"]
        assert escaped.stat().st_size == receipt["size_bytes"]
        assert daemon.store.job(response["execution"]["job_id"])["status"] == "UNKNOWN"
        assert daemon.store.list_metadata("artifacts") == []
    finally:
        daemon.close()


def test_parameter_import_deep_json_and_csv_cells_are_terminal_prewrite_refusals(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    nested = "[" * 5000 + "0" + "]" * 5000
    json_source = project_root / "inputs" / "deep-nesting.json"
    json_source.write_text(
        '{"schema":"comsol-mcp-parameter-table/1","groups":["phys"],"rows":[{'
        '"group":"phys","name":"A","expression":"1","description":' + nested +
        ',"unit":null,"unit_status":"NO_UNIT_RETURNED"}]}',
        encoding="utf-8",
    )
    csv_source = project_root / "inputs" / "deep-nesting.csv"
    csv_source.write_text(
        'group,name,expression,description,unit,unit_status\n'
        '"""phys""",A,1,,"' + nested + '",VERIFIED\n',
        encoding="utf-8",
    )
    try:
        for source, fmt in ((json_source, "json"), (csv_source, "csv")):
            registered = _register_for_project(
                daemon, daemon._test_project_id,
                path=source.relative_to(project_root).as_posix(),
                role="parameter_source", classification="project_private",
                key=f"deep-parameter-source-{fmt}",
            )
            assert registered["success"] is True
            request = {"artifact_id": registered["data"]["artifact_id"], "format": fmt}
            key = f"deep-parameter-import-{fmt}"
            first = _parameter_action(
                daemon, "parameter.import", model_execution, request,
                key=key, request_id=f"{key}-request",
            )
            assert first["success"] is False
            assert _error_code(first) == "INVALID_REQUEST"
            job = daemon.store.job(first["execution"]["job_id"])
            assert job["status"] == "FAILED"
            assert job["result"]["error"]["safe_retry"] is False
            assert worker.model.param_calls == 0
            assert sum(node.setter_calls for node in worker.model._parameters.groups.values()) == 0
            replay = _parameter_action(
                daemon, "parameter.import", model_execution, request,
                key=key, request_id=f"{key}-request",
            )
            assert replay == first
            assert worker.model.param_calls == 0
            assert sum(node.setter_calls for node in worker.model._parameters.groups.values()) == 0
    finally:
        daemon.close()


def test_parameter_import_rejects_unknown_schema_fields_and_oversized_cells_before_model_reads(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    _enable_synthetic_parameter_isolation(monkeypatch)
    base_row = {
        "group": "phys", "name": "A", "expression": "1", "description": "",
        "unit": None, "unit_status": "NO_UNIT_RETURNED",
    }
    variants = [
        {"schema": "future-schema", "groups": ["phys"], "rows": [dict(base_row)]},
        {
            "schema": "comsol-mcp-parameter-table/1", "groups": ["phys"], "rows": [
                {**base_row, "unexpected": True},
            ],
        },
        {
            "schema": "comsol-mcp-parameter-table/1", "groups": ["phys"], "rows": [
                {**base_row, "description": "x" * 8193},
            ],
        },
    ]
    try:
        for index, document in enumerate(variants):
            source = _register_parameter_table(
                daemon, project_root, document, key=f"invalid-parameter-table-{index}",
            )
            assert source["success"] is True
            response = _parameter_action(
                daemon, "parameter.import", model_execution,
                {"artifact_id": source["data"]["artifact_id"], "format": "json"},
                key=f"invalid-parameter-import-{index}", request_id=f"invalid-parameter-import-{index}-request",
            )
            assert response["success"] is False
            assert _error_code(response) == "INVALID_REQUEST"
        invalid_format = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": source["data"]["artifact_id"], "format": "yaml"},
            key="invalid-parameter-import-format", request_id="invalid-parameter-import-format-request",
        )
        assert invalid_format["success"] is False
        assert _error_code(invalid_format) == "INVALID_REQUEST"
        missing_source = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": "0" * 64, "format": "json"},
            key="missing-parameter-source", request_id="missing-parameter-source-request",
        )
        assert missing_source["success"] is False
        assert _error_code(missing_source) == "ARTIFACT_NOT_FOUND"
        assert worker.model.param_calls == 0
        assert sum(node.setter_calls for node in worker.model._parameters.groups.values()) == 0
    finally:
        daemon.close()


@pytest.mark.parametrize(
    ("unit", "unit_error", "expected_code", "expected_status"),
    [
        ("cm", False, "UNIT_MISMATCH", "MISMATCH"),
        ("m", True, "UNIT_READBACK_UNVERIFIED", "UNVERIFIED"),
    ],
)
def test_parameter_import_unit_mismatch_and_getter_failure_are_not_success(
    tmp_path, monkeypatch, unit, unit_error, expected_code, expected_status,
):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    worker.model._parameters.groups["phys"].values.clear()
    worker.model._parameters.groups["phys"].unit_error_name = "A" if unit_error else None
    _enable_synthetic_parameter_isolation(monkeypatch)
    document = {
        "schema": "comsol-mcp-parameter-table/1",
        "groups": ["phys"],
        "rows": [{
            "group": "phys", "name": "A", "expression": "3[m]",
            "description": "unit check", "unit": unit, "unit_status": "VERIFIED",
        }],
    }
    try:
        source = _register_parameter_table(daemon, project_root, document, key=f"unit-source-{expected_code}")
        response = _parameter_action(
            daemon, "parameter.import", model_execution,
            {"artifact_id": source["data"]["artifact_id"], "format": "json"},
            key=f"unit-import-{expected_code}", request_id=f"unit-import-{expected_code}-request",
        )
        assert response["success"] is False
        assert response["data"]["status"] == "PARTIAL_FAILURE"
        assert response["data"]["execution_state_unknown"] is False
        assert response["data"]["failed"][0]["code"] == expected_code
        assert response["data"]["applied"][0]["unit_status"] == expected_status
        assert _error_code(response) == expected_code
    finally:
        daemon.close()


def test_parameter_import_setter_failure_is_unknown_durable_and_same_key_does_not_retry(tmp_path, monkeypatch):
    daemon, project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    worker = _ParameterWorker()
    daemon.backend.worker = worker
    for node in worker.model._parameters.groups.values():
        node.values.clear()
    worker.model._parameters.groups["phys"].fail_after_set_name = "B"
    _enable_synthetic_parameter_isolation(monkeypatch)
    document = {
        "schema": "comsol-mcp-parameter-table/1",
        "groups": ["phys"],
        "rows": [
            {"group": "phys", "name": "A", "expression": "1", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
            {"group": "phys", "name": "B", "expression": "2", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
            {"group": "phys", "name": "C", "expression": "3", "description": "", "unit": None, "unit_status": "NO_UNIT_RETURNED"},
        ],
    }
    try:
        source = _register_parameter_table(daemon, project_root, document, key="partial-parameter-source")
        assert source["success"] is True
        request = {
            "artifact_id": source["data"]["artifact_id"], "format": "json",
        }
        first = _parameter_action(
            daemon, "parameter.import", model_execution, request,
            key="partial-parameter-import", request_id="partial-parameter-import-request",
        )
        assert first["success"] is False
        assert first["data"]["status"] == "EXECUTION_STATE_UNKNOWN"
        assert first["data"]["execution_state_unknown"] is True
        assert [row["name"] for row in first["data"]["applied"]] == ["A"]
        assert [row["name"] for row in first["data"]["failed"]] == ["B"]
        assert [row["name"] for row in first["data"]["not_executed"]] == ["C"]
        assert first["error"]["code"] == "ENGINE_CALL_FAILED"
        # The shared command wrapper retains ENGINE_CALL_FAILED for the
        # transport/error class; the domain outcome carries the stronger
        # EXECUTION_STATE_UNKNOWN state and forbids a blind retry.
        assert first["data"]["failed"][0]["error"]["code"] == "ENGINE_CALL_FAILED"
        assert first["data"]["failed"][0]["error"]["safe_retry"] is False
        job_id = first["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        setter_count = worker.model._parameters.groups["phys"].setter_calls
        replay = _parameter_action(
            daemon, "parameter.import", model_execution, request,
            key="partial-parameter-import", request_id="partial-parameter-import-request",
        )
        assert replay == first
        assert worker.model._parameters.groups["phys"].setter_calls == setter_count
    finally:
        daemon.close()


def test_artifact_inspect_sanitizes_symlink_resolver_error_without_losing_code(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "symlink-boundary.step"
    source.write_bytes(b"symlink boundary fixture")
    try:
        registered = _register(daemon, path="inputs/symlink-boundary.step", key="symlink-boundary")
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        managed = project_root / daemon.store.get_metadata("artifacts", artifact_id)["path"]
        outside = tmp_path / "outside-symlink-target.step"
        outside.write_bytes(source.read_bytes())
        managed.unlink()
        managed.symlink_to(outside)
        daemon.backend.service = None
        daemon.backend.worker = None
        response = _read_action(daemon, "artifact.inspect", daemon._test_project_id, {"artifact_id": artifact_id})
        assert response["success"] is False
        assert _error_code(response) == "ACCESS_VIOLATION"
        assert str(project_root) not in json.dumps(response, sort_keys=True)
        assert str(outside) not in json.dumps(response, sort_keys=True)
    finally:
        daemon.close()


def test_artifact_list_rejects_non_sha256_scoped_metadata_key(tmp_path):
    from comsol_mcp import _artifact_store

    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        malformed_id = "g" * 64
        record = {
            "schema_version": 2,
            "artifact_id": malformed_id,
            "sha256": malformed_id,
            "path": f"g2_artifacts/registered/{malformed_id}.step",
            "size": 0,
            "file_identity": {"device": 1, "inode": 1, "size": 0, "mtime_ns": 1},
            "project_id": daemon._test_project_id,
            "project_root_identity": _artifact_store.project_root_identity(project_root),
            "host_identity": local_artifact_host_identity(),
            "engine_host_identity": local_engine_host_identity("127.0.0.1:2036"),
            "role": "geometry_source",
            "classification": "internal",
        }
        with daemon.store.lock:
            daemon.store.db.execute(
                "INSERT INTO artifacts(artifact_id,metadata) VALUES(?,?)",
                (malformed_id, json.dumps(record, sort_keys=True)),
            )
        response = _read_action(daemon, "artifact.list", daemon._test_project_id, {"limit": 1})
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_STATE_UNKNOWN"
        assert "g" * 64 not in json.dumps(response, sort_keys=True)
    finally:
        daemon.close()


def test_artifact_inspect_fails_closed_on_scope_and_pinned_metadata_tampering(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "tamper.step"
    source.write_bytes(b"pinned metadata fixture")
    try:
        registered = _register(daemon, path="inputs/tamper.step")
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        original = daemon.store.get_metadata("artifacts", artifact_id)
        variants = []
        for field, value in (
            ("project_id", "foreign-project"),
            ("project_root_identity", "foreign-root"),
            ("host_identity", "foreign-host"),
            ("engine_host_identity", "foreign-engine"),
        ):
            altered = json.loads(json.dumps(original))
            altered[field] = value
            variants.append((altered, "ARTIFACT_NOT_FOUND"))
        altered_path = json.loads(json.dumps(original))
        altered_path["path"] = f"g2_artifacts/registered/{'0' * 64}.step"
        variants.append((altered_path, "ARTIFACT_STATE_UNKNOWN"))
        altered_size = json.loads(json.dumps(original))
        altered_size.pop("size")
        variants.append((altered_size, "ARTIFACT_STATE_UNKNOWN"))
        altered_inode = json.loads(json.dumps(original))
        altered_inode["file_identity"]["inode"] += 1
        variants.append((altered_inode, "ARTIFACT_IDENTITY_MISMATCH"))

        for altered, expected_code in variants:
            _inject_artifact_metadata_for_test(daemon.store, artifact_id, altered)
            response = _read_action(
                daemon, "artifact.inspect", daemon._test_project_id,
                {"artifact_id": artifact_id},
            )
            assert response["success"] is False
            assert _error_code(response) == expected_code
            assert daemon.store.get_metadata("artifacts", artifact_id) == altered
            _inject_artifact_metadata_for_test(daemon.store, artifact_id, original)

        managed = project_root / original["path"]
        managed.write_bytes(b"changed bytes after registration")
        changed_bytes = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": artifact_id},
        )
        assert changed_bytes["success"] is False
        assert _error_code(changed_bytes) == "ARTIFACT_HASH_MISMATCH"
        assert daemon.store.get_metadata("artifacts", artifact_id) == original
    finally:
        daemon.close()


def test_artifact_inspect_rejects_symlink_and_same_byte_hardlink_replacement(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    source = project_root / "inputs" / "filesystem-identity.step"
    source.write_bytes(b"same bytes, different file identity")
    try:
        registered = _register(daemon, path="inputs/filesystem-identity.step")
        assert registered["success"] is True
        artifact_id = registered["data"]["artifact_id"]
        record = daemon.store.get_metadata("artifacts", artifact_id)
        managed = project_root / record["path"]

        outside = tmp_path / "outside.step"
        outside.write_bytes(source.read_bytes())
        managed.unlink()
        managed.symlink_to(outside)
        symlink = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": artifact_id},
        )
        assert symlink["success"] is False
        assert _error_code(symlink) == "ACCESS_VIOLATION"

        managed.unlink()
        os.link(source, managed)
        hardlink = _read_action(
            daemon, "artifact.inspect", daemon._test_project_id,
            {"artifact_id": artifact_id},
        )
        assert hardlink["success"] is False
        assert _error_code(hardlink) == "ARTIFACT_IDENTITY_MISMATCH"
        assert hashlib.sha256(managed.read_bytes()).hexdigest() == artifact_id
    finally:
        daemon.close()


def test_artifact_scope_index_migration_preserves_malformed_rows_and_schema_version(tmp_path):
    from comsol_mcp._operation_store import ARTIFACT_SCOPE_INDEX

    database = tmp_path / "legacy-artifacts.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_meta VALUES(1)")
    connection.execute("CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
    good = {"schema_version": 2, "project_id": "p1", "artifact_id": "a" * 64}
    original_rows = [("a" * 64, json.dumps(good, sort_keys=True)), ("b" * 64, "{malformed-json")]
    connection.executemany("INSERT INTO artifacts VALUES(?,?)", original_rows)
    connection.commit()
    connection.close()

    store = OperationStore(database)
    try:
        with store.lock:
            observed = [tuple(row) for row in store.db.execute("SELECT artifact_id,metadata FROM artifacts ORDER BY artifact_id")]
            version = store.db.execute("SELECT version FROM schema_meta").fetchone()[0]
            index = store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (ARTIFACT_SCOPE_INDEX,),
            ).fetchone()
        assert observed == original_rows
        assert version == 1
        assert index is not None
    finally:
        store.close()


def test_artifact_verify_is_registered_with_the_frozen_input_and_output_contract():
    from jsonschema import validate
    from comsol_mcp._g2_registry import CONTROL_IMPLEMENTED_OPERATIONS, registry_describe, validate_call

    assert "artifact.verify" in CONTROL_IMPLEMENTED_OPERATIONS
    entry = validate_call("artifact.verify", {"project_id": "p1", "artifact_id": "a" * 64})
    assert entry.operation_id == "artifact.verify"
    description = registry_describe("artifact.verify")
    assert description["implementation_status"] == "SUPPORTED_UNVERIFIED"
    assert set(description["input_schema"]["properties"]) == {"project_id", "artifact_id", "request_id"}
    assert description["data_schema"]["properties"]["format_verdict"]["enum"] == [
        "VERIFIED_DECLARED_PACKAGE_CONTENT", "INVALID", "INCOMPLETE", "UNSUPPORTED",
    ]
    with pytest.raises(Exception):
        validate(instance={"format_verdict": "PASS"}, schema=description["data_schema"])
