from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
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

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


def _make_daemon(tmp_path, *, with_model=False):
    project_root = tmp_path / "project"
    (project_root / "inputs").mkdir(parents=True)
    ledger = SessionLedger("session-test", "server-test")
    service = ExecutionService(ledger, _SnapshotAdapter(), project_root=project_root)
    worker = _Worker() if with_model else None
    daemon = ControlDaemon(
        tmp_path / "control",
        service=service,
        registry={},
        worker=worker,
        project_root=project_root,
    )
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    execution = None
    if with_model:
        execution = service.bind_model("m1")["execution"]
    return daemon, project_root, execution


def _register(daemon, *, path="inputs/shape.step", key="artifact-register-1", request_id="register-request-1"):
    return daemon.dispatch({
        "operation": "artifact.register",
        "arguments": {
            "project_id": "project-test",
            "idempotency_key": key,
            "request_id": request_id,
            "path": path,
            "role": "geometry_source",
            "classification": "internal",
        },
        "execution": {"project_id": "project-test", "idempotency_key": key, "request_id": request_id},
    })


def _error_code(envelope):
    return envelope.get("error", {}).get("code") or envelope.get("data", {}).get("error", {}).get("code")


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
        assert record["project_id"] == "project-test"
        assert record["path"] == f"g2_artifacts/registered/{artifact_id}.step"
        assert record["provenance"]["source_project_relative_path"] == "inputs/shape.step"
        resolved = resolve_registered_artifact(
            project_root,
            reopened,
            artifact_id,
            project_id="project-test",
            current_host_identity=local_artifact_host_identity(),
            current_engine_host_identity=local_engine_host_identity("127.0.0.1:2036"),
            current_server_instance_id="worker-restarted-server-instance",
        )
        assert resolved["path"].read_bytes() == source.read_bytes()
        assert resolved["relative_path"] == record["path"]
    finally:
        reopened.close()


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
            "execution": {"project_id": "project-test", "idempotency_key": key, "request_id": request_id},
        })
        assert response["success"] is True
        assert response["data"]["artifact_id"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert daemon.store.get_metadata("artifacts", response["data"]["artifact_id"])["project_id"] == "project-test"

        malformed = daemon.dispatch({
            "operation": outer_operation,
            "arguments": {
                "operation_id": "artifact.register",
                "arguments": {"path": "inputs/nested.step", "classification": "internal"},
            },
            "execution": {"project_id": "project-test", "idempotency_key": key + "-invalid",
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
                "project_id": "project-test",
                "current_engine_host_identity": "another-engine-host",
            })
        assert wrong_host.value.code == "ARTIFACT_SCOPE_MISMATCH"

        record = daemon.store.get_metadata("artifacts", artifact_id)
        tampered = dict(record)
        tampered["path"] = "inputs/shape.step"
        _inject_artifact_metadata_for_test(daemon.store, artifact_id, tampered)
        with pytest.raises(ExecutionContractError):
            resolve_registered_artifact(**base, project_id="project-test")

        _inject_artifact_metadata_for_test(daemon.store, artifact_id, record)
        managed = project_root / record["path"]
        managed.write_bytes(b"changed content")
        with pytest.raises(ExecutionContractError) as changed:
            resolve_registered_artifact(**base, project_id="project-test")
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
                project_id="project-test",
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
        daemon.close()
        daemon = None

        # Reopen the same project ledger and bind a fresh Worker epoch. The
        # artifact registration is project/host scoped, so it survives this
        # process restart without inheriting the previous server_instance_id.
        service = ExecutionService(SessionLedger("session-restarted", "server-restarted"),
                                   _SnapshotAdapter("server-restarted"), project_root=project_root)
        daemon = ControlDaemon(home, service=service, registry={}, worker=_Worker(), project_root=project_root)
        daemon.backend.endpoint_key = "127.0.0.1:2036"
        model_execution = service.bind_model("m1")["execution"]
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
            "project_id": "project-test",
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
            "project_id": "project-test",
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
                "project_id": "project-test",
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
