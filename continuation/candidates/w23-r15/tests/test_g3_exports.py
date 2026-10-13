"""Software evidence for report, evidence bundle, and host publication handlers.

The tests use the real OperationStore, ArtifactStore, hash pinning, and no-clobber
publication paths with a synthetic Worker/provider. They do not start COMSOL,
make an external host connection, or imply numerical/physical acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _g3_exports as exports
from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
)
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._execution_contract import model_ref_from_mapping


ENDPOINT = "127.0.0.1:24567"


class _Model:
    def __init__(self, root: Path, tag: str = "m"):
        self.tag_value = tag
        self.live_path = str(root / "live-model.mph")
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.java = self

    def _call(self, method: str, *args):
        self.calls.append((method, args))
        if method == "getFilePath":
            return self.live_path
        if method == "saveModelPackage":
            path, _include_solution = args
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("model/fixture.txt", "synthetic MPH saved by software fixture")
            return {"saved": True, "live_filepath_preserved": True}
        raise AssertionError(f"unexpected typed model method: {method}")


class _Client:
    def __init__(self, worker):
        self.worker = worker

    def model(self, tag):
        return self.worker.models[tag]


class _Worker:
    def __init__(self, root: Path):
        self.models = {"m": _Model(root)}
        self._client = _Client(self)

    def client(self):
        return self._client

    def operation_context(self, _operation_id, *, on_request_event=None):
        return nullcontext()

    def backend_snapshot(self, tag):
        return {"tag": tag, "model_tag": tag, "fingerprint": "synthetic-fingerprint",
                "fingerprint_scope": ["tag", "parameters"], "external_event_counter": 0,
                "server_instance_id": "server-1", "worker_instance_id": "worker-1", "generation": 1}

    model_snapshot = backend_snapshot

    def runtime_metadata(self):
        return {"connected": True, "server": ENDPOINT, "instance_id": "worker-1", "generation": 1}

    def getComsolVersion(self):
        return "COMSOL Multiphysics 6.4 (开发版本: 293)"


class _Backend:
    endpoint_key = ENDPOINT

    def __init__(self, project_id):
        self.project_id = project_id

    def model_project_binding(self, _model_ref):
        return {"attribution": "PROJECT_BOUND", "project_id": self.project_id}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "reports").mkdir()
    (root / "bundles").mkdir()
    store = OperationStore(tmp_path / "operations.sqlite3")
    worker = _Worker(root)
    service = ExecutionService(SessionLedger("session-1", "server-1"), worker, project_root=root)
    ref = model_ref_from_mapping(service.bind_model("m")["execution"]["model_ref"])
    context = {
        "project_root": str(root), "project_id": "project-alpha", "operation_id": "export-op",
        "request_id": "export-request", "idempotency_key": "export-key",
        "model_ref": ref.as_dict(), "_model_ref_object": ref, "source_revision": 0,
        "host_identity": local_artifact_host_identity(),
        "engine_host_identity": local_engine_host_identity(ENDPOINT),
        "operation_store": store, "execution_service": service, "backend": _Backend("project-alpha"),
    }

    def fake_tree(_worker, tag, arguments):
        assert tag == "m"
        assert arguments["_managed_context"] is context
        return {"status": "SUCCEEDED", "complete": True, "truncated": False,
                "count": 2, "nodes": [{"path": {"segments": []}, "node_type": "Model"}],
                "errors": [], "next_cursor": None, "model_ref": ref.as_dict(), "revision": 0}

    monkeypatch.setattr("comsol_mcp._g3_managed_models.tree", fake_tree)
    return root, store, worker, service, ref, context


def _register(env, relative: str, payload: bytes, role: str, *, artifact_type="application/octet-stream"):
    root, store, _worker, _service, _ref, context = env
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    record = register_project_artifact(
        root, store, relative, project_id=context["project_id"], role=role,
        classification="project_local", host_identity=context["host_identity"],
        engine_host_identity=context["engine_host_identity"], request_id="source-register",
        registering_operation_id="artifact.register", artifact_type=artifact_type,
        format_version="test-artifact/1",
    )
    return record["artifact_id"]


def _seed_model_job(store: OperationStore, context: dict):
    record, replayed = store.begin(
        request_id="durable-model-operation", idempotency_key="durable-model-key",
        request_hash="a" * 64, operation="node.property_get",
        metadata={"project_id": context["project_id"], "model_ref": context["model_ref"]},
    )
    assert replayed is False
    job_id = record["job_id"]
    store.add_event(job_id, "OperationCompleted", {
        "status": "SUCCEEDED", "model_ref": context["model_ref"],
        "authorization_ref": "do-not-export-this", "path": "/Users/private/model.mph",
        "message": "private event message",
    })
    store.finish(record["operation_id"], status="SUCCEEDED", result={
        "success": True, "data": {"status": "APPLIED"},
        "execution": {"model_ref": context["model_ref"], "revision": 0},
    })
    return record["operation_id"], job_id


def _assert_data_schema(operation: str, result: dict):
    from jsonschema import validate
    validate(instance=result, schema=exports.DATA_SCHEMAS[operation])


def test_export_report_uses_bound_tree_durable_registry_and_no_clobber(env):
    root, store, worker, _service, ref, context = env
    artifact_id = _register(env, "results/curve.csv", b"x,y\n0,1\n", "result_data", artifact_type="text/csv")
    _seed_model_job(store, context)
    result = exports.export_report(worker, "m", {
        "definition": {"title": "Synthetic report", "artifact_ids": [artifact_id]},
        "destination": "reports/report.json", "_managed_context": context,
    })
    _assert_data_schema("export.report", result)
    assert result["status"] == "APPLIED"
    assert result["report"]["model"]["model_ref"] == ref.as_dict()
    assert result["report"]["model_tree"]["count"] == 2
    assert result["report"]["registered_artifacts"][0]["status"] == "PASS"
    assert result["report"]["execution_history"][0]["status"] == "SUCCEEDED"
    assert result["report"]["scientific_acceptance"] == "UNVERIFIED"
    path = root / "reports/report.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model"]["model_ref"] == ref.as_dict()
    assert store.get_metadata("artifacts", result["artifact_registration"]["artifact_id"])["role"] == "model_report"
    with pytest.raises(ExecutionContractError) as conflict:
        exports.export_report(worker, "m", {
            "definition": {}, "destination": "reports/report.json", "_managed_context": context,
        })
    assert conflict.value.code == "DESTINATION_EXISTS"


def test_bundle_contains_hash_bound_members_and_explicit_complete_scope(env):
    root, store, worker, _service, ref, context = env
    recipe_id = _register(env, "recipes/code.json", b'{"recipe":"tested"}\n', "code_recipe",
                          artifact_type="application/json")
    raw_id = _register(env, "observations/raw.csv", b"t,value\n0,300\n", "rawdata_observation",
                       artifact_type="text/csv")
    plot_id = _register(env, "plots/temperature.png", b"synthetic-plot-payload", "plot_export",
                        artifact_type="image/png")
    _seed_model_job(store, context)
    result = exports.export_evidence_bundle(worker, "m", {
        "definition": {"artifact_ids": [recipe_id, raw_id, plot_id], "include_solution": True},
        "destination": "bundles/evidence.zip", "_managed_context": context,
    })
    _assert_data_schema("export.evidence_bundle", result)
    assert result["status"] == "APPLIED"
    assert result["bundle_status"] == "COMPLETE"
    assert result["missing_required_categories"] == []
    bundle_path = root / "bundles/evidence.zip"
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == result["sha256"]
    with zipfile.ZipFile(bundle_path) as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "report.json", "provenance.json", "logs/operations.json"} <= names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["absolute_paths_in_manifest"] is False
        assert manifest["bundle_status"] == "COMPLETE"
        for member in manifest["members"]:
            content = archive.read(member["path"])
            assert hashlib.sha256(content).hexdigest() == member["sha256"]
            assert len(content) == member["size_bytes"]
        logs = json.loads(archive.read("logs/operations.json"))
        assert logs["events"]
        serialized_logs = json.dumps(logs, ensure_ascii=False)
        assert "do-not-export-this" not in serialized_logs
        assert "/Users/private/model.mph" not in serialized_logs
        assert "private event message" not in serialized_logs
        assert any("[REDACTED]" == row["metadata_projection"].get("authorization_ref")
                   for row in logs["events"])
        provenance = json.loads(archive.read("provenance.json"))
        assert provenance["model_ref"] == ref.as_dict()
        assert provenance["save_copy"]["live_filepath_unchanged"] is True
    model = worker.client().model("m")
    assert sum(method == "saveModelPackage" for method, _args in model.calls) == 1
    assert model.live_path.endswith("live-model.mph")
    assert store.get_metadata("artifacts", result["artifact_registration"]["artifact_id"])["role"] == "evidence_bundle"


def test_bundle_truthfully_marks_missing_categories_incomplete(env):
    _root, _store, worker, _service, _ref, context = env
    result = exports.export_evidence_bundle(worker, "m", {
        "definition": {"artifact_ids": []}, "destination": "bundles/incomplete.zip",
        "_managed_context": context,
    })
    assert result["status"] == "APPLIED"
    assert result["bundle_status"] == "INCOMPLETE"
    assert set(result["missing_required_categories"]) == {"logs", "plots", "rawdata", "recipe"}
    with zipfile.ZipFile(context["project_root"] + "/bundles/incomplete.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["bundle_status"] == "INCOMPLETE"
        assert manifest["portability_status"] == "UNVERIFIED"


def test_report_registration_failure_leaves_receipt_and_returns_unknown(env, monkeypatch):
    root, _store, worker, _service, _ref, context = env

    def fail_register(*_args, **_kwargs):
        raise RuntimeError("registration injected failure")

    monkeypatch.setattr("comsol_mcp._artifact_store.register_project_artifact", fail_register)
    with pytest.raises(ExecutionContractError) as failure:
        exports.export_report(worker, "m", {
            "definition": {}, "destination": "reports/orphan.json", "_managed_context": context,
        })
    assert failure.value.code == "EXECUTION_STATE_UNKNOWN"
    recovery = failure.value.details["recovery_artifact"]
    receipt = root / recovery["receipt"]
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["stage"] == "publish_report"
    assert (root / "reports/orphan.json").is_file()


def _publish_context(env, sink_root: Path, *, accept=True, fail_on=None):
    root, store, _worker, _service, _ref, context = env
    descriptor_identity = {"project": context["project_id"], "destination": "safe-sink"}
    registration_sha = hashlib.sha256(exports._json_bytes(descriptor_identity)).hexdigest()
    calls = []

    def publish_chunks(destination, name, chunks, *, expected_sha256, expected_size, artifact_id):
        calls.append(artifact_id)
        if fail_on == artifact_id:
            raise OSError("provider failure")
        assert name == artifact_id + Path(store.get_metadata("artifacts", artifact_id)["path"]).suffix
        target = sink_root / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as output:
            for block in chunks:
                output.write(block)
                digest.update(block)
                size += len(block)
            output.flush()
            os.fsync(output.fileno())
        assert digest.hexdigest() == expected_sha256 and size == expected_size
        readback = target.read_bytes()
        assert hashlib.sha256(readback).hexdigest() == expected_sha256
        return {"status": "PUBLISHED", "destination_ref": destination["destination_ref"],
                "artifact_id": artifact_id, "sha256": expected_sha256, "size_bytes": size,
                "no_clobber": True, "readback_verified": True, "receipt_id": "receipt-1",
                "private_absolute_path": str(target)}

    def resolver(project_id, destination_ref):
        assert project_id == context["project_id"] and destination_ref == "dest-1"
        return {"schema": exports.PUBLISH_DESTINATION_SCHEMA, "project_id": project_id,
                "destination_ref": destination_ref, "registration_sha256": registration_sha,
                "publish_chunks": publish_chunks}

    def verifier(project_id, action, scope, authorization_ref):
        assert project_id == context["project_id"] and action == "artifact.publish"
        assert scope["destination_registration_sha256"] == registration_sha
        assert scope["destination_ref"] == "dest-1"
        return accept and authorization_ref == "authorized-once"

    context.update({"host_publish_destination_resolver": resolver,
                    "host_authorization_scope_verifier": verifier,
                    "host_permission_check": lambda project, permission: permission == "host_control"})
    sink_root.mkdir(parents=True, exist_ok=True)
    return context, calls


def test_artifact_publish_requires_and_uses_scoped_registered_provider(env, tmp_path):
    _root, store, _worker, _service, _ref, context = env
    artifact_id = _register(env, "outputs/share.csv", b"a,b\n1,2\n", "result_data", artifact_type="text/csv")
    context, calls = _publish_context(env, tmp_path / "host-sink")
    result = exports.artifact_publish(None, "", {
        "artifact_ids": [artifact_id], "destination_ref": "dest-1", "authorization_ref": "authorized-once",
        "_managed_context": context,
    })
    _assert_data_schema("artifact.publish", result)
    assert result["status"] == "PUBLISHED"
    assert result["published"][0]["artifact_id"] == artifact_id
    assert calls == [artifact_id]
    external = tmp_path / "host-sink" / (artifact_id + ".csv")
    assert hashlib.sha256(external.read_bytes()).hexdigest() == artifact_id
    assert "private_absolute_path" not in result["published"][0]["provider_receipt"]
    assert store.get_metadata("artifacts", artifact_id)["sha256"] == artifact_id


def test_artifact_publish_denies_bad_scope_and_absent_provider_without_side_effect(env, tmp_path):
    artifact_id = _register(env, "outputs/deny.csv", b"deny", "result_data", artifact_type="text/csv")
    context, calls = _publish_context(env, tmp_path / "denied-sink", accept=False)
    request = {"artifact_ids": [artifact_id], "destination_ref": "dest-1",
               "authorization_ref": "wrong-scope", "_managed_context": context}
    with pytest.raises(ExecutionContractError) as denied:
        exports.artifact_publish(None, "", request)
    assert denied.value.code == "AUTHORIZATION_REQUIRED"
    assert calls == []
    assert list((tmp_path / "denied-sink").iterdir()) == []
    for key in ("host_publish_destination_resolver", "host_authorization_scope_verifier"):
        context.pop(key)
    with pytest.raises(ExecutionContractError) as unavailable:
        exports.artifact_publish(None, "", {**request, "authorization_ref": "nonempty-is-not-auth"})
    assert unavailable.value.code == "PUBLISH_UNAVAILABLE"
    assert list((tmp_path / "denied-sink").iterdir()) == []


def test_artifact_publish_reports_partial_unknown_and_does_not_replay(env, tmp_path):
    first = _register(env, "outputs/first.bin", b"first", "result_data")
    second = _register(env, "outputs/second.bin", b"second", "result_data")
    context, calls = _publish_context(env, tmp_path / "partial-sink", fail_on=second)
    with pytest.raises(ExecutionContractError) as failure:
        exports.artifact_publish(None, "", {
            "artifact_ids": [first, second], "destination_ref": "dest-1",
            "authorization_ref": "authorized-once", "_managed_context": context,
        })
    assert failure.value.code == "EXECUTION_STATE_UNKNOWN"
    assert failure.value.details["completed_count"] == 1
    assert failure.value.details["partial_change"] is True
    assert failure.value.details["safe_retry"] is False
    assert calls == [first, second]
    assert (tmp_path / "partial-sink" / (first + ".bin")).read_bytes() == b"first"
    assert not (tmp_path / "partial-sink" / (second + ".bin")).exists()


class _RegisteredLocalPublishProvider:
    """Configured local sink used only to exercise ControlDaemon wiring."""

    def __init__(self):
        self.registration_sha = hashlib.sha256(b"registered local sink v1").hexdigest()
        self.bytes_by_name = {}
        self.calls = []
        self.scopes = []

    def __call__(self, _project_id, _authorization_ref):
        # Legacy project.policy_set verifier compatibility only.
        return False

    def resolve_destination(self, project_id, destination_ref):
        if destination_ref != "registered-local-sink":
            raise KeyError(destination_ref)
        return {"schema": exports.PUBLISH_DESTINATION_SCHEMA,
                "project_id": project_id, "destination_ref": destination_ref,
                "registration_sha256": self.registration_sha,
                "publish_chunks": self.publish_chunks}

    def verify_scope(self, project_id, action, scope, authorization_ref):
        self.scopes.append((project_id, action, dict(scope), authorization_ref))
        return (authorization_ref == "valid-publish-ref" and action == "artifact.publish"
                and scope.get("project_id") == project_id
                and scope.get("destination_ref") == "registered-local-sink"
                and scope.get("destination_registration_sha256") == self.registration_sha
                and isinstance(scope.get("artifacts"), list)
                and len(scope["artifacts"]) == 1)

    def publish_chunks(self, destination, target_name, chunks, *, expected_sha256,
                       expected_size, artifact_id):
        key = (destination["project_id"], destination["destination_ref"], target_name)
        if key in self.bytes_by_name:
            raise FileExistsError(target_name)
        payload = b"".join(chunks)
        observed_sha = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_size or observed_sha != expected_sha256:
            raise ValueError("provider stream hash/size mismatch")
        self.bytes_by_name[key] = payload
        self.calls.append((key[0], key[1], artifact_id))
        return {"status": "PUBLISHED", "destination_ref": key[1],
                "artifact_id": artifact_id, "sha256": observed_sha,
                "size_bytes": len(payload), "no_clobber": True,
                "readback_verified": self.bytes_by_name[key] == payload,
                "receipt_id": f"local-receipt-{len(self.calls)}"}


def _create_project_for_dispatch(daemon, name):
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": name, "workspace": name,
                      "policy": {"permissions": ["inspect", "project_write", "host_control"]}},
        "execution": {"request_id": f"create-{name}", "idempotency_key": f"create-{name}"},
    })
    assert result["success"] is True, result
    project = result["data"]["project"]
    return project["project_id"], Path(project["workspace"])


def test_artifact_publish_uses_control_daemon_store_scope_and_registered_provider(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    provider = _RegisteredLocalPublishProvider()
    daemon = ControlDaemon(tmp_path / "control", project_root=projects,
                           project_authorization_verifier=provider)
    try:
        project_id, project_root = _create_project_for_dispatch(daemon, "publish-project")
        # Schema-v2 artifacts retain the local engine-host pin. This fixture
        # supplies a loopback identity without creating a COMSOL connection.
        daemon.backend.endpoint_key = ENDPOINT
        source = project_root / "reports" / "source.csv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x,y\n0,1\n")
        registered = register_project_artifact(
            project_root, daemon.store, "reports/source.csv", project_id=project_id,
            role="result_data", classification="project_local",
            host_identity=local_artifact_host_identity(),
            engine_host_identity=local_engine_host_identity(ENDPOINT),
            request_id="source-register", registering_operation_id="artifact.register",
            artifact_type="text/csv", format_version="test-csv/1",
        )
        denied = daemon.dispatch({
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": "registered-local-sink",
                          "authorization_ref": "wrong-scope"},
            "execution": {"project_id": project_id, "request_id": "publish-denied",
                          "idempotency_key": "publish-denied"},
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert provider.calls == []
        denied_record = daemon.store.get_operation(denied["execution"]["operation_id"])
        assert "authorization_ref" not in denied_record["metadata"]["arguments"]
        assert denied_record["metadata"]["arguments"]["authorization_ref_sha256"] == hashlib.sha256(
            b"wrong-scope").hexdigest()

        request = {
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": [registered["artifact_id"]],
                          "destination_ref": "registered-local-sink",
                          "authorization_ref": "valid-publish-ref"},
            "execution": {"project_id": project_id, "request_id": "publish-valid",
                          "idempotency_key": "publish-valid"},
        }
        published = daemon.dispatch(request)
        assert published["success"] is True, published
        _assert_data_schema("artifact.publish", published["data"])
        assert published["data"]["status"] == "PUBLISHED"
        assert published["data"]["published"][0]["provider_receipt"]["readback_verified"] is True
        assert provider.calls == [(project_id, "registered-local-sink", registered["artifact_id"])]
        scoped = provider.scopes[-1]
        assert scoped[0:2] == (project_id, "artifact.publish")
        assert scoped[2]["artifacts"] == [{"artifact_id": registered["artifact_id"],
                                          "sha256": registered["sha256"],
                                          "size_bytes": len(source.read_bytes())}]
        assert scoped[3] == "valid-publish-ref"
        replay = daemon.dispatch(request)
        assert replay == published
        assert len(provider.calls) == 1
    finally:
        daemon.close()


def test_artifact_publish_without_scoped_provider_is_typed_unavailable(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=projects)
    try:
        project_id, _project_root = _create_project_for_dispatch(daemon, "publish-no-provider")
        result = daemon.dispatch({
            "operation": "artifact.publish",
            "arguments": {"artifact_ids": ["a" * 64],
                          "destination_ref": "unregistered",
                          "authorization_ref": "nonempty-is-not-proof"},
            "execution": {"project_id": project_id, "request_id": "publish-unavailable",
                          "idempotency_key": "publish-unavailable"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "PUBLISH_UNAVAILABLE"
    finally:
        daemon.close()


def test_report_and_bundle_use_control_daemon_bound_model_context(tmp_path, monkeypatch):
    """Production G3 routes install context from the outer ModelRef, not caller data."""
    from comsol_mcp._control_daemon import ControlDaemon

    projects = tmp_path / "projects"
    projects.mkdir()
    endpoint = ENDPOINT
    worker = _Worker(tmp_path)
    service = ExecutionService(SessionLedger("session-export-route", "server-1",
        permissions={"inspect", "project_write", "compute"}), worker, project_root=projects)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker,
                           project_root=projects)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {"runtime_id": "runtime-software-fixture",
        "worker_instance_id": "worker-export", "connection_epoch": 1,
        "server_instance_id": "server-1", "endpoint": endpoint}
    daemon.backend.persist()
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"software_fixture": True})
    created_project = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "export-route", "workspace": "export-route",
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": "export-route-project", "idempotency_key": "export-route-project"},
    })
    assert created_project["success"] is True, created_project
    project_id = created_project["data"]["project"]["project_id"]
    project_root = projects / "export-route"
    (project_root / "reports").mkdir()
    (project_root / "bundles").mkdir()
    bound = service.bind_model("m")
    ref = bound["execution"]["model_ref"]
    daemon.backend._bind_model_project(ref, project_id)
    daemon.backend.persist()
    artifact_context = {"project_id": project_id,
                        "host_identity": local_artifact_host_identity(),
                        "engine_host_identity": local_engine_host_identity(endpoint)}
    artifact_id = _register((project_root, daemon.store, worker, service,
                             model_ref_from_mapping(ref), artifact_context),
                            "results/curve.csv", b"x,y\n0,1\n", "result_data", artifact_type="text/csv")

    observed_contexts = []

    def bound_tree(_worker, tag, arguments):
        context = arguments["_managed_context"]
        assert tag == "m"
        assert context["project_id"] == project_id
        assert context["model_ref"] == ref
        expected_source_revision = 0 if not observed_contexts else report["execution"]["revision"]
        assert context["source_revision"] == expected_source_revision
        observed_contexts.append(context)
        return {"status": "SUCCEEDED", "complete": True, "truncated": False,
                "count": 1, "nodes": [{"path": {"segments": []}, "node_type": "Model"}],
                "errors": [], "next_cursor": None, "model_ref": ref, "revision": 0}

    monkeypatch.setattr("comsol_mcp._g3_managed_models.tree", bound_tree)
    try:
        report = daemon.dispatch({
            "operation": "export.report",
            "arguments": {"definition": {"title": "Production route fixture",
                                          "artifact_ids": [artifact_id]},
                          "destination": "reports/production.json"},
            "execution": {"project_id": project_id, "session_id": service.ledger.session_id,
                          "model_ref": ref, "expected_revision": 0,
                          "request_id": "export-route-report", "idempotency_key": "export-route-report"},
        })
        assert report["success"] is True, report
        wrapper_fields = {"domain_state", "verification_status", "execution_state_unknown",
                          "partial_change", "cleanup_failed", "dispatch_stage",
                          "domain_outcome", "effect"}
        assert wrapper_fields.issubset(report["data"])
        assert report["data"]["domain_state"] == "succeeded"
        _assert_data_schema("export.report", {key: value for key, value in report["data"].items()
                                               if key not in wrapper_fields})
        assert report["data"]["report"]["model"]["model_ref"] == ref
        assert report["execution"]["model_ref"] == ref

        bundle = daemon.dispatch({
            "operation": "export.evidence_bundle",
            "arguments": {"definition": {"artifact_ids": [artifact_id], "include_solution": True},
                          "destination": "bundles/production.zip"},
            "execution": {"project_id": project_id, "session_id": service.ledger.session_id,
                          "model_ref": ref, "expected_revision": report["execution"]["revision"],
                          "request_id": "export-route-bundle", "idempotency_key": "export-route-bundle"},
        })
        assert bundle["success"] is True, bundle
        assert wrapper_fields.issubset(bundle["data"])
        assert bundle["data"]["domain_state"] == "succeeded"
        _assert_data_schema("export.evidence_bundle", {key: value for key, value in bundle["data"].items()
                                                        if key not in wrapper_fields})
        assert bundle["data"]["manifest"]["model"]["model_ref"] == ref
        assert bundle["data"]["manifest"]["model"]["include_solution"] is True
        assert len(observed_contexts) == 2
        assert (project_root / "reports" / "production.json").is_file()
        assert (project_root / "bundles" / "production.zip").is_file()
    finally:
        daemon.close()
