"""Focused software coverage for managed model lifecycle and code recipes.

These tests use the production service ledger, OperationStore, ArtifactStore
resolver and Worker-shaped call boundary.  The synthetic Worker is software
evidence only and never starts COMSOL.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _g3_managed_models as models
from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
)
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_installation import runtime_id_for_root


class _Java:
    def __init__(self, tag, path=""):
        self._tag = tag
        self._path = path
        self._label = tag
        self._save_payload = b"synthetic saved model"
        self.calls = []
        self.java = self

    def tag(self):
        return self._tag

    def label(self, *args):
        if args:
            self._label = str(args[0])
            return self
        return self._label

    def component(self, *_args):
        return self

    def geom(self, *_args):
        return self

    def create(self, *_args):
        self.calls.append(("create", _args))
        return self

    def _call(self, method, *args):
        self.calls.append((method, args))
        if method == "getFilePath":
            return self._path
        if method == "saveModelPackage":
            target, _include_solution = args
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("model/fixture.txt", self._save_payload)
            return {"saved": True, "live_filepath_preserved": True}
        raise AssertionError(f"unexpected model call: {method}")


class _Client:
    def __init__(self, worker):
        self.worker = worker
        self.progress_log_path = None

    def tags(self):
        return list(self.worker.models)

    def model(self, tag):
        return self.worker.models[tag]

    def uniquetag(self, prefix):
        self.worker.counter += 1
        return f"{prefix}{self.worker.counter}"

    def create(self, label):
        tag = self.uniquetag("mcp")
        value = _Java(tag)
        value.label(label)
        self.worker.models[tag] = value
        return value

    def load(self, path):
        tag = self.uniquetag("mcp_load")
        self.worker.models[tag] = _Java(tag, path)
        return self.worker.models[tag]

    def remove(self, tag):
        self.worker.removed.append(tag)
        del self.worker.models[tag]

    def modelsUsedByOtherClients(self):
        return list(getattr(self.worker, "shared_model_tags", []))

    def set_progress_log(self, path):
        """Synthetic typed Worker receipt; it intentionally emits no COMSOL log bytes."""
        if path is None:
            self.progress_log_path = None
            return {"active": False, "disabled": True}
        candidate = Path(path)
        if not candidate.is_file() or candidate.is_symlink():
            raise AssertionError("progress capture must provide a host-owned private regular file")
        self.progress_log_path = str(candidate)
        return {"active": True, "sink_path_persisted": False}


class _Worker:
    generation = 1

    def __init__(self, install_root, endpoint):
        self.paths = type("Paths", (), {"comsol_root": install_root, "project_root": None})()
        self.endpoint = endpoint
        self.models = {"m": _Java("m", "/server/source.mph"), "foreign": _Java("foreign")}
        self.counter = 0
        self.context_calls = []
        self.executions = []
        self.shared_model_tags = []
        self.removed = []
        self._active_operation_id = ""
        self._active_event_callback = None
        self.instance_id = "worker-1"
        self.worker_generation = 1
        self._client = _Client(self)

    def client(self):
        return self._client

    @contextmanager
    def operation_context(self, operation_id, on_request_event=None):
        self.context_calls.append(operation_id)
        previous = (self._active_operation_id, self._active_event_callback)
        self._active_operation_id, self._active_event_callback = operation_id, on_request_event
        try:
            yield
        finally:
            self._active_operation_id, self._active_event_callback = previous

    def runtime_metadata(self):
        return {"connected": True, "server": self.endpoint, "instance_id": self.instance_id,
                "generation": self.worker_generation}

    def model_snapshot(self, tag):
        if tag not in self.models:
            raise KeyError(tag)
        value = self.models[tag]
        return {"model_tag": tag, "fingerprint": f"fp:{tag}:{value._path}:{value._label}",
                "external_event_counter": 0}

    backend_snapshot = model_snapshot

    def getComsolVersion(self):
        return "COMSOL Multiphysics 6.4 (开发版本: 293)"

    def submit(self, command, arguments, **_kwargs):
        assert command == "modelutil"
        assert arguments["method"] == "loadCopy"
        tag, path = arguments["args"]
        assert tag not in self.models
        self.models[tag] = _Java(tag, path)
        return {"ok": True, "result": {"tag": tag}}

    def execute_java(self, tag, path, entrypoint, arguments, **kwargs):
        self.executions.append((tag, path, entrypoint, dict(arguments), dict(kwargs)))
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        request_id = kwargs.get("request_id") or f"worker-execute-{len(self.executions)}"
        payload = {"tag": tag, "source_artifact": path, "entrypoint": entrypoint,
                   "arguments": dict(arguments or {})}
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        event = {"request_id": request_id, "kind": "code_execute",
                 "operation_id": self._active_operation_id, "request_hash": request_hash,
                 "metadata": payload}
        if callable(self._active_event_callback):
            self._active_event_callback({"phase": "submitted", **event})
        reply = {"ok": True, "status": "SUCCEEDED", "result": {"executed": True, "model_tag": tag,
                "source_sha256": digest, "entrypoint": entrypoint, "answer": 42}}
        if callable(self._active_event_callback):
            self._active_event_callback({"phase": "observed", "status": "SUCCEEDED", **event})
        return reply


class _Backend:
    def __init__(self, store, endpoint):
        self.store = store
        self.endpoint_key = endpoint
        self.bindings = {}
        self.persisted = 0

    def _model_project_key(self, ref):
        return json.dumps(dict(ref), sort_keys=True, separators=(",", ":"))

    def _bind_model_project(self, ref, project_id):
        self.bindings[self._model_project_key(ref)] = project_id

    def model_project_binding(self, ref):
        value = self.bindings.get(self._model_project_key(ref))
        return {"attribution": "PROJECT_BOUND", "project_id": value} if value else {
            "attribution": "UNATTRIBUTED", "project_id": None}

    def persist(self):
        self.persisted += 1


class _ModelPublicationHandle:
    def __init__(self, path: Path):
        self.path = path


class _ModelPublicationApi:
    """POSIX decision adapter for the Win32 route; not kernel evidence."""

    @staticmethod
    def _stat(handle):
        return handle.path.stat(follow_symlinks=False)

    def open_existing_for_delete(self, parent, leaf):
        path = Path(parent) / leaf
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        return _ModelPublicationHandle(path)

    def source_fingerprint(self, handle):
        value = self._stat(handle)
        return ((int(value.st_dev), int(value.st_ino)), int(value.st_size), 0,
                int(value.st_mtime_ns), int(value.st_nlink))

    @staticmethod
    def readback(handle):
        digest = hashlib.sha256()
        size = 0
        with handle.path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def child_identity(self, parent, leaf):
        path = Path(parent) / leaf
        value = path.stat(follow_symlinks=False)
        if path.is_symlink() or not path.is_file():
            raise OSError("model target is not a regular file")
        return int(value.st_dev), int(value.st_ino)

    @staticmethod
    def exists_child(parent, leaf):
        path = Path(parent) / leaf
        return path.exists() or path.is_symlink()

    def rename(self, handle, parent, leaf):
        if self.child_identity(handle.path.parent, handle.path.name) != self.source_fingerprint(handle)[0]:
            raise OSError("opened model identity no longer matches its name")
        target = Path(parent) / leaf
        # link+unlink is an exclusive same-volume rename analogue for this
        # software-only decision test. Native SetFileInformationByHandle is
        # explicitly NOT_RUN on this host.
        os.link(handle.path, target, follow_symlinks=False)
        handle.path.unlink()
        handle.path = target

    @staticmethod
    def verify_named_binding(binding):
        for path, identity in binding:
            current = path.stat(follow_symlinks=False)
            if path.is_symlink() or (int(current.st_dev), int(current.st_ino)) != identity:
                raise OSError("pinned model parent identity changed")

    @staticmethod
    def close(_handle):
        return None

    def delete_owned(self, handle):
        if self.child_identity(handle.path.parent, handle.path.name) != self.source_fingerprint(handle)[0]:
            raise OSError("recovery MPH identity changed")
        handle.path.unlink()


@contextmanager
def _model_publication_test_parent(root, destination, *, add_file, _api):
    parts = tuple(destination.split("/"))
    parent = Path(root).joinpath(*parts[:-1])
    value = parent.stat(follow_symlinks=False)
    if parent.is_symlink() or not parent.is_dir():
        raise OSError("unsafe test parent")
    yield _api, parent, parts, [(parent, (int(value.st_dev), int(value.st_ino)))]


class _WindowsModelModuleOS:
    name = "nt"

    def __getattr__(self, name):
        return getattr(os, name)


def _runtime_root(path: Path) -> Path:
    path.mkdir()
    (path / "readme.txt").write_text("COMSOL 6.4.0.293 README\n", encoding="utf-8")
    launcher = path / "bin" / "macarm64" / "comsollauncher"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"synthetic installation evidence")
    return path


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    install_root = _runtime_root(tmp_path / "comsol")
    endpoint = "127.0.0.1:25432"
    worker = _Worker(install_root, endpoint)
    store = OperationStore(tmp_path / "operations.sqlite3")
    service = ExecutionService(SessionLedger("session-1", "server-1", permissions={
        "inspect", "project_write", "compute", "trusted_code", "host_control"}), worker,
        project_root=root)
    backend = _Backend(store, endpoint)
    ref = service.bind_model("m", ownership="mcp_owned")["execution"]["model_ref"]
    backend._bind_model_project(ref, "project-1")
    backend._model_project_key(ref)
    context = {
        "project_root": str(root), "project_id": "project-1", "operation_store": store,
        "host_identity": local_artifact_host_identity(),
        "engine_host_identity": local_engine_host_identity(endpoint), "backend": backend,
        "execution_service": service, "model_ref": ref, "_model_ref_object": service.ledger._models["m"].ref,
        "source_revision": 0, "operation_id": "op-current", "request_id": "request-current",
        "idempotency_key": "key-current", "session_context": type("Runtime", (), {"owned_model_tags": set()})(),
        "project_authorizer": lambda _project, _permission: None,
    }
    yield {"root": root, "install_root": install_root, "endpoint": endpoint, "worker": worker,
           "store": store, "service": service, "backend": backend, "ref": ref, "context": context}
    store.close()


def _args(context, **values):
    return {**values, "_managed_context": context}


def _seed_durable_create_baseline(env):
    context = {**env["context"], "operation_id": "model-create-baseline"}
    operation, _ = env["store"].begin(
        request_id="model-create-baseline-request", idempotency_key="model-create-baseline-key",
        request_hash="c" * 64, operation="model.create",
        metadata={"operation": "model.create", "arguments": {}, "execution": {}},
    )
    env["store"].finish(operation["operation_id"], status="SUCCEEDED", result={
        "success": True,
        "execution": {"operation_id": operation["operation_id"]},
        "data": {"model_ref": env["ref"]},
    })
    context["operation_id"] = operation["operation_id"]
    models._record_saved_baseline(context, model_ref=env["ref"], state="UNSAVED", saved_revision=0,
                                  operation="model.create")
    return context


def test_list_reports_bound_and_foreign_without_adopting(env):
    result = models.list_models(env["worker"], "", _args(env["context"]))
    rows = {row["model_tag"]: row for row in result["models"]}
    assert rows["m"]["managed"] is True
    assert rows["m"]["ownership"] == "mcp_owned"
    assert rows["m"]["model_ref"] == env["ref"]
    assert rows["foreign"]["managed"] is False
    assert rows["foreign"]["model_ref"] is None
    assert result["adopted"] is False
    assert "foreign" not in env["service"].ledger._models


def test_create_returns_persisted_exact_ref_and_only_owns_new_tag(env):
    result = models.create_model(env["worker"], "", _args(env["context"], label="New model", dimension=2))
    assert result["status"] == "CREATED"
    assert result["model_tag"] == "mcp1"
    assert result["model_ref"] == env["service"].ledger._models["mcp1"].ref.as_dict()
    assert env["backend"].model_project_binding(result["model_ref"]) == {
        "attribution": "PROJECT_BOUND", "project_id": "project-1"}
    assert "mcp1" in env["context"]["session_context"].owned_model_tags
    assert env["worker"].models["mcp1"].calls[0][0] == "create"


def test_load_requires_schema_v2_registered_saved_model_and_binds_new_model(env):
    saved = env["root"] / "saved.mph"
    with zipfile.ZipFile(saved, "w") as archive:
        archive.writestr("model/fixture.txt", "saved bytes")
    artifact = register_project_artifact(
        env["root"], env["store"], "saved.mph", project_id="project-1", role=models.MODEL_SAVE_ROLE,
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="register",
        registering_operation_id="model.save", artifact_type=models.MODEL_SAVE_FORMAT,
        format_version="comsol-mph-savecopy/1")
    result = models.load_model(env["worker"], "", _args(env["context"], artifact_id=artifact["artifact_id"]))
    assert result["status"] == "LOADED"
    assert result["artifact_id"] == artifact["artifact_id"]
    assert result["model_tag"] == "mcp_load1"
    assert Path(env["worker"].models["mcp_load1"]._path) == Path(artifact["resolved_path"])
    assert env["backend"].model_project_binding(result["model_ref"]) == {
        "attribution": "PROJECT_BOUND", "project_id": "project-1"}


def test_load_accepts_registered_restored_mph_without_typed_metadata(env):
    restored = env["root"] / "restore" / "recovered.mph"
    restored.parent.mkdir()
    with zipfile.ZipFile(restored, "w") as archive:
        archive.writestr("model/fixture.txt", "restored model bytes")
    artifact = register_project_artifact(
        env["root"], env["store"], "restore/recovered.mph", project_id="project-1", role="restored_model",
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="restore-register",
        registering_operation_id="artifact.register")
    before = set(env["worker"].models)
    result = models.load_model(env["worker"], "", _args(env["context"], artifact_id=artifact["artifact_id"]))
    assert result["status"] == "LOADED"
    assert result["model_tag"] == "mcp_load1"
    assert set(env["worker"].models) == before | {"mcp_load1"}
    assert Path(env["worker"].models["mcp_load1"]._path) == Path(artifact["resolved_path"])


def test_load_rejects_registered_non_mph_metadata_before_engine(env):
    saved = env["root"] / "wrong.mph"
    with zipfile.ZipFile(saved, "w") as archive:
        archive.writestr("model/fixture.txt", "registered archive")
    artifact = register_project_artifact(
        env["root"], env["store"], "wrong.mph", project_id="project-1", role="restored_model",
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="wrong-register",
        registering_operation_id="artifact.register", artifact_type="application/zip", format_version="zip/1")
    with pytest.raises(Exception) as error:
        models.load_model(env["worker"], "", _args(env["context"], artifact_id=artifact["artifact_id"]))
    assert getattr(error.value, "code", None) == "ARTIFACT_ROLE_MISMATCH"
    assert set(env["worker"].models) == {"m", "foreign"}


def test_load_rejects_unreadable_registered_mph_before_engine(env):
    malformed = env["root"] / "malformed.mph"
    malformed.write_bytes(b"not a COMSOL MPH archive")
    artifact = register_project_artifact(
        env["root"], env["store"], "malformed.mph", project_id="project-1", role="restored_model",
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="malformed-register",
        registering_operation_id="artifact.register")
    with pytest.raises(Exception) as error:
        models.load_model(env["worker"], "", _args(env["context"], artifact_id=artifact["artifact_id"]))
    assert getattr(error.value, "code", None) == "MODEL_FORMAT_UNVERIFIED"
    assert set(env["worker"].models) == {"m", "foreign"}


def test_load_rejects_unregistered_or_wrong_role_artifact_before_engine(env):
    with pytest.raises(Exception) as error:
        models.load_model(env["worker"], "", _args(env["context"], artifact_id="a" * 64))
    assert getattr(error.value, "code", None) == "ARTIFACT_NOT_FOUND"
    assert set(env["worker"].models) == {"m", "foreign"}


def test_tree_uses_existing_cursor_bound_to_revision_and_depth(monkeypatch, env):
    observed = {}
    def walk(_worker, tag, query, **kwargs):
        observed.update(tag=tag, query=query, **kwargs)
        return {"status": "SUCCEEDED", "results": [
            {"path": {"segments": [{"collection": "component", "tag": "c1"}]}, "tag": "c1"},
            {"path": {"segments": [{"collection": "component", "tag": "c1"}, {"collection": "geom", "tag": "g1"}]}, "tag": "g1"},
            {"path": {"segments": [{"collection": "component", "tag": "c1"}, {"collection": "geom", "tag": "g1"}, {"collection": "feature", "tag": "f1"}]}, "tag": "f1"},
        ], "complete": True, "truncated": False, "next_cursor": None, "errors": []}
    monkeypatch.setattr("comsol_mcp._g2_engine.find_nodes", walk)
    result = models.tree(env["worker"], "m", _args(env["context"], depth=1, limit=10))
    assert [row["tag"] for row in result["nodes"]] == ["c1"]
    assert observed["model_revision"] == 0
    assert observed["limit"] == 10
    assert observed["query"]["_managed_tree_depth"] == 1


def test_save_uses_savecopy_no_clobber_register_and_preserves_live_path(env):
    (env["root"] / "exports").mkdir()
    result = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph",
                                                              overwrite=False))
    target = env["root"] / "exports" / "model.mph"
    assert result["status"] == "SAVED"
    assert result["include_solution"] is True
    assert result["live_filepath_preserved"] is True
    assert target.is_file()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == result["sha256"]
    record = env["store"].get_metadata("artifacts", result["artifact_id"])
    assert record["role"] == models.MODEL_SAVE_ROLE
    assert record["format_version"] == "comsol-mph-savecopy/1"
    assert env["worker"].models["m"]._path == "/server/source.mph"
    assert env["worker"].models["m"].calls[0][0] == "getFilePath"


def test_save_no_clobber_and_atomic_overwrite_of_owned_registered_target(env):
    (env["root"] / "exports").mkdir()
    first = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    with pytest.raises(Exception) as error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    assert getattr(error.value, "code", None) == "DESTINATION_EXISTS"
    old_bytes = (env["root"] / "exports" / "model.mph").read_bytes()
    env["worker"].models["m"]._save_payload = b"updated synthetic model bytes"
    second = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=True))
    assert second["sha256"] != first["sha256"]
    assert second["publication"]["reused_existing"] is False
    assert second["overwrite_policy"] == "OWNED_REGISTERED_TARGET_ATOMIC_EXCHANGE"
    assert hashlib.sha256((env["root"] / "exports" / "model.mph").read_bytes()).hexdigest() == second["sha256"]
    old_record = env["store"].get_metadata("artifacts", first["artifact_id"])
    assert (env["root"] / old_record["path"]).read_bytes() == old_bytes
    assert not list((env["root"] / "exports").glob(".model-save-*.partial"))


def test_save_overwrite_refuses_unregistered_or_tampered_target_before_engine(env):
    exports = env["root"] / "exports"
    exports.mkdir()
    unregistered = exports / "unregistered.mph"
    unregistered.write_bytes(b"foreign bytes")
    before_calls = list(env["worker"].models["m"].calls)
    with pytest.raises(Exception) as unregistered_error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/unregistered.mph", overwrite=True))
    assert getattr(unregistered_error.value, "code", None) == "OVERWRITE_UNSAFE"
    assert env["worker"].models["m"].calls == before_calls

    result = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/tampered.mph", overwrite=False))
    tampered = exports / "tampered.mph"
    tampered.write_bytes(b"changed after registration")
    before_calls = list(env["worker"].models["m"].calls)
    with pytest.raises(Exception) as tampered_error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/tampered.mph", overwrite=True))
    assert getattr(tampered_error.value, "code", None) == "OVERWRITE_UNSAFE"
    assert env["worker"].models["m"].calls == before_calls
    assert result["status"] == "SAVED"

    foreign_path = exports / "foreign.mph"
    with zipfile.ZipFile(foreign_path, "w") as archive:
        archive.writestr("model/fixture.txt", "another project")
    register_project_artifact(
        env["root"], env["store"], "exports/foreign.mph", project_id="project-2", role=models.MODEL_SAVE_ROLE,
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="foreign-register",
        registering_operation_id="model.save", artifact_type=models.MODEL_SAVE_FORMAT,
        format_version="comsol-mph-savecopy/1")
    before_calls = list(env["worker"].models["m"].calls)
    with pytest.raises(Exception) as foreign_error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/foreign.mph", overwrite=True))
    assert getattr(foreign_error.value, "code", None) == "OVERWRITE_UNSAFE"
    assert env["worker"].models["m"].calls == before_calls


def test_save_overwrite_refuses_symlink_and_lexical_alias_before_engine(env):
    exports = env["root"] / "exports"
    exports.mkdir()
    models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    symlink = exports / "linked.mph"
    symlink.symlink_to(exports / "model.mph")
    before_calls = list(env["worker"].models["m"].calls)
    with pytest.raises(Exception) as link_error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/linked.mph", overwrite=True))
    assert getattr(link_error.value, "code", None) == "ACCESS_VIOLATION"
    with pytest.raises(Exception) as alias_error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/../exports/model.mph", overwrite=True))
    assert getattr(alias_error.value, "code", None) == "INVALID_DESTINATION"
    assert env["worker"].models["m"].calls == before_calls


def test_save_overwrite_refuses_hardlink_alias_of_registered_blob_before_engine(env):
    exports = env["root"] / "exports"
    exports.mkdir()
    alias = exports / "alias.mph"
    with zipfile.ZipFile(alias, "w") as archive:
        archive.writestr("model/fixture.txt", "hardlink alias fixture")
    artifact = register_project_artifact(
        env["root"], env["store"], "exports/alias.mph", project_id="project-1", role=models.MODEL_SAVE_ROLE,
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="alias-register",
        registering_operation_id="model.save", artifact_type=models.MODEL_SAVE_FORMAT,
        format_version="comsol-mph-savecopy/1")
    registered_blob = env["root"] / artifact["path"]
    alias.unlink()
    alias.hardlink_to(registered_blob)
    before_calls = list(env["worker"].models["m"].calls)
    with pytest.raises(Exception) as error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/alias.mph", overwrite=True))
    assert getattr(error.value, "code", None) == "OVERWRITE_UNSAFE"
    assert env["worker"].models["m"].calls == before_calls


def test_save_overwrite_restores_original_when_registration_fails(monkeypatch, env):
    (env["root"] / "exports").mkdir()
    first = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    target = env["root"] / "exports" / "model.mph"
    original = target.read_bytes()
    env["worker"].models["m"]._save_payload = b"replacement whose registration will fail"
    monkeypatch.setattr(models, "_register_saved_model", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected registration failure")))
    with pytest.raises(Exception) as error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=True))
    assert getattr(error.value, "code", None) == "EXECUTION_STATE_UNKNOWN"
    assert target.read_bytes() == original
    assert env["store"].get_metadata("artifacts", first["artifact_id"]) is not None
    receipts = list(env["root"].glob("model-save-*/failure_receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["stage"] == "register"


def test_save_overwrite_readback_mismatch_atomically_restores_original(monkeypatch, env):
    (env["root"] / "exports").mkdir()
    first = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    target = env["root"] / "exports" / "model.mph"
    old_bytes = target.read_bytes()
    env["worker"].models["m"]._save_payload = b"new saved bytes with injected readback mismatch"

    import comsol_mcp._artifact_store as artifact_store

    real_hash = artifact_store._hash_regular_artifact
    target_reads = 0

    def inject_one_published_readback_mismatch(path):
        nonlocal target_reads
        digest, identity = real_hash(Path(path))
        if Path(path) == target:
            target_reads += 1
            # First read is the registered-target preflight; second is the
            # post-exchange readback that must trigger a rollback.
            if target_reads == 2:
                return digest, {**identity, "size": identity["size"] + 1}
        return digest, identity

    monkeypatch.setattr(artifact_store, "_hash_regular_artifact", inject_one_published_readback_mismatch)
    with pytest.raises(Exception) as error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=True))
    assert getattr(error.value, "code", None) == "OVERWRITE_ROLLED_BACK"
    assert target.read_bytes() == old_bytes
    assert hashlib.sha256(target.read_bytes()).hexdigest() == first["sha256"]
    assert target_reads >= 3  # the rollback readback saw the restored file
    assert not list((env["root"] / "exports").glob(".model-save-*.partial"))
    assert env["store"].get_metadata("artifacts", first["artifact_id"]) is not None


def test_save_overwrite_failed_atomic_rollback_retains_exact_backup_and_unknown_receipt(monkeypatch, env):
    (env["root"] / "exports").mkdir()
    first = models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=False))
    target = env["root"] / "exports" / "model.mph"
    old_bytes = target.read_bytes()
    env["worker"].models["m"]._save_payload = b"new bytes with rollback interruption"

    import comsol_mcp._artifact_store as artifact_store

    real_hash = artifact_store._hash_regular_artifact
    target_reads = 0

    def inject_post_exchange_readback_mismatch(path):
        nonlocal target_reads
        digest, identity = real_hash(Path(path))
        if Path(path) == target:
            target_reads += 1
            if target_reads == 2:
                return digest, {**identity, "size": identity["size"] + 1}
        return digest, identity

    real_exchange = models._exchange_names
    exchange_calls = 0

    def interrupt_rollback(directory_fd, first_name, second_name):
        nonlocal exchange_calls
        exchange_calls += 1
        if exchange_calls == 1:
            return real_exchange(directory_fd, first_name, second_name)
        raise OSError("injected rollback exchange interruption")

    monkeypatch.setattr(artifact_store, "_hash_regular_artifact", inject_post_exchange_readback_mismatch)
    monkeypatch.setattr(models, "_exchange_names", interrupt_rollback)
    with pytest.raises(Exception) as error:
        models.save_model(env["worker"], "m", _args(env["context"], destination="exports/model.mph", overwrite=True))

    assert getattr(error.value, "code", None) == "OVERWRITE_STATE_UNKNOWN"
    assert error.value.details["safe_retry"] is False
    recovery_relative = error.value.details["recovery_path"]
    recovery = env["root"] / recovery_relative
    assert recovery.is_file()
    assert recovery.read_bytes() == old_bytes
    assert hashlib.sha256(recovery.read_bytes()).hexdigest() == first["sha256"]
    assert hashlib.sha256(target.read_bytes()).hexdigest() != first["sha256"]
    assert exchange_calls >= 2
    receipt_path = env["root"] / error.value.details["failure_receipt"]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["safe_retry"] is False
    assert receipt["recovery_details"]["recovery_path"] == recovery_relative
    assert receipt["destination_readback"]["state"] == "VERIFIED"


def test_clone_uses_typed_loadcopy_and_leaves_source_unchanged(env):
    before = env["worker"].backend_snapshot("m")
    result = models.clone_model(env["worker"], "m", _args(env["context"], label="Clone", include_solution=True))
    assert result["status"] == "CLONED"
    assert result["copy_method"] == "ModelUtil.loadCopy"
    assert result["model_tag"] == "mcp_clone1"
    assert env["worker"].backend_snapshot("m") == before
    assert env["worker"].models["m"]._path == "/server/source.mph"
    assert env["worker"].models[result["model_tag"]]._label == "Clone"


def test_close_requires_project_owned_ref_and_lifecycle_finalizer(env):
    callback_calls = []
    env["context"]["close_bound_model"] = lambda **kwargs: callback_calls.append(kwargs) or {"status": "CLOSED"}
    with pytest.raises(Exception) as error:
        models.close_model(env["worker"], "m", _args(env["context"], discard_changes=False))
    assert getattr(error.value, "code", None) == "SAVED_STATE_UNKNOWN"
    assert callback_calls == []
    assert set(env["worker"].models) == {"m", "foreign"}


def test_dirty_or_foreign_close_refuses_without_scoped_authorizer(env):
    context = _seed_durable_create_baseline(env)
    state = env["service"].ledger._models["m"]
    state.dirty = True
    context["authorization_scope_verifier"] = None
    with pytest.raises(Exception) as error:
        models.close_model(env["worker"], "m", _args(context, discard_changes=True,
                                                        authorization_ref="nonempty-is-not-proof"))
    assert getattr(error.value, "code", None) == "AUTHORIZATION_STATE_UNKNOWN"
    assert "m" in env["worker"].models
    state.dirty = False
    env["service"].ledger.model_ownership["m"] = "user_owned"
    with pytest.raises(Exception) as owner_error:
        models.close_model(env["worker"], "m", _args(env["context"], discard_changes=False))
    assert getattr(owner_error.value, "code", None) == "MODEL_OWNERSHIP_UNKNOWN"
    assert "m" in env["worker"].models


def test_discard_close_requires_exact_scope_verifier_and_accepts_verified_authorization(env):
    context = _seed_durable_create_baseline(env)
    state = env["service"].ledger._models["m"]
    state.dirty = True
    calls = []
    context["authorization_scope_verifier"] = lambda *args: calls.append(args) or True
    close_calls = []
    context["close_bound_model"] = lambda **kwargs: close_calls.append(kwargs) or {
        "status": "CLOSED", "terminal_close_verified": True,
    }
    with pytest.raises(Exception) as uncertain_without_discard:
        models.close_model(env["worker"], "m", _args(context, discard_changes=False,
                                                        authorization_ref="scoped-test-reference"))
    assert getattr(uncertain_without_discard.value, "code", None) == "MODEL_STATE_UNKNOWN"
    assert close_calls == []
    result = models.close_model(env["worker"], "m", _args(context, discard_changes=True,
                                                            authorization_ref="scoped-test-reference"))
    assert result["terminal_close_verified"] is True
    assert calls == [("project-1", "model.close", env["ref"], 0, None, "scoped-test-reference")]
    assert close_calls[0]["model_ref"] == env["ref"]

    state.dirty = True
    context["authorization_scope_verifier"] = lambda *_args: False
    with pytest.raises(Exception) as wrong_scope:
        models.close_model(env["worker"], "m", _args(context, discard_changes=True,
                                                        authorization_ref="wrong-action-or-revision"))
    assert getattr(wrong_scope.value, "code", None) == "AUTHORIZATION_REQUIRED"


def test_historical_recipe_worker_path_survives_project_relocation_but_stays_content_addressed():
    digest = "a" * 64
    rel = f"g2_artifacts/registered/{digest}.java"
    assert models._worker_source_path_matches(f"/old/project/{rel}", rel)
    assert models._worker_source_path_matches(f"/new/moved/project/{rel}", rel)
    assert not models._worker_source_path_matches(f"/new/moved/project/src/Recipe.java", rel)
    assert not models._worker_source_path_matches(f"/new/moved/project/../{rel}", rel)


def _register_source(env, payload: bytes, rel="src/Recipe.java"):
    path = env["root"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return register_project_artifact(
        env["root"], env["store"], rel, project_id="project-1", role="java-source",
        classification="project_local", host_identity=env["context"]["host_identity"],
        engine_host_identity=env["context"]["engine_host_identity"], request_id="source-register",
        registering_operation_id="artifact.register", artifact_type="text/x-java-source")


def _forged_durable_test(env, source_sha, *, test_ref="forged-test-1", project="project-1"):
    """A durable row with caller-authored claims, used only as a rejection control."""
    operation, _ = env["store"].begin(request_id=test_ref, idempotency_key=test_ref, request_hash="d" * 64,
                                       operation="code.execute_java", metadata={
        "execution": {"project_id": project},
        "arguments": {"source_artifact": source_sha, "entrypoint": "Recipe", "mode": "trusted"}})
    env["store"].finish(operation["operation_id"], status="SUCCEEDED", result={
        "success": True, "execution_success": True,
        "data": {"source_sha256": source_sha, "entrypoint": "Recipe",
                 "test_evidence": {"status": "PASS", "accepted": True}},
    })
    return operation["operation_id"]


def test_recipe_register_rejects_forged_durable_test_row_and_invalid_schema(env):
    source = _register_source(env, b"public class Recipe { public static Object run() { return 1; } }\n")
    fake_ref = _forged_durable_test(env, source["sha256"])
    with pytest.raises(Exception) as forged:
        models.recipe_register(env["worker"], "m", _args(env["context"], source_artifact=source["artifact_id"],
            input_schema={"type": "object"}, test_refs=[fake_ref]))
    assert getattr(forged.value, "code", None) == "RECIPE_TEST_UNVERIFIED"
    with pytest.raises(Exception) as schema:
        models.recipe_register(env["worker"], "m", _args(env["context"], source_artifact=source["artifact_id"],
            input_schema={"type": "not-a-schema"}, test_refs=[fake_ref]))
    assert getattr(schema.value, "code", None) == "RECIPE_INVALID"
    assert not env["worker"].executions


def _dispatch_managed(daemon, operation, arguments, *, project_id, session_id,
                      request_id, idempotency_key=None, model_ref=None, revision=None):
    execution = {"project_id": project_id, "session_id": session_id, "request_id": request_id}
    if idempotency_key is not None:
        execution["idempotency_key"] = idempotency_key
    if model_ref is not None:
        execution["model_ref"] = model_ref
    if revision is not None:
        execution["expected_revision"] = revision
    return daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})


def test_control_daemon_dirty_close_requires_configured_exact_scope_provider(tmp_path, monkeypatch):
    """Successful worker mutation requires a trusted, revision-bound close grant."""
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    install_root = _runtime_root(tmp_path / "comsol")
    endpoint = "127.0.0.1:25439"
    worker = _Worker(install_root, endpoint)

    class Provider:
        def __init__(self):
            self.scopes = []

        def __call__(self, _project_id, _authorization_ref):
            return False

        def verify_scope(self, project_id, action, scope, authorization_ref):
            self.scopes.append((project_id, action, dict(scope), authorization_ref))
            return (project_id == expected_project[0] and action == "model.close"
                    and scope == {"model_ref": target_ref[0], "expected_revision": scope.get("expected_revision"),
                                  "destination_ref": None}
                    and type(scope.get("expected_revision")) is int
                    and authorization_ref == "valid-close-ref")

    expected_project = [None]
    target_ref = [None]
    provider = Provider()
    service = ExecutionService(SessionLedger("session-close-scope", "server-close-scope", permissions={
        "inspect", "project_write", "trusted_code", "host_control"}), worker, project_root=projects)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker,
                           project_root=projects, project_authorization_verifier=provider)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {
        "runtime_id": runtime_id_for_root(install_root), "worker_instance_id": "worker-1",
        "connection_epoch": 1, "server_instance_id": "server-close-scope", "endpoint": endpoint,
    }
    daemon.backend.persist()
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"software_fixture": True})
    try:
        created_project = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "close-scope", "workspace": "close-scope",
                          "policy": {"permissions": ["inspect", "project_write", "trusted_code", "host_control"]}},
            "execution": {"request_id": "close-scope-project", "idempotency_key": "close-scope-project"},
        })
        assert created_project["success"] is True, created_project
        project_id = created_project["data"]["project"]["project_id"]
        expected_project[0] = project_id
        session_id = service.ledger.session_id
        created = _dispatch_managed(daemon, "model.create", {"label": "Scoped close target", "dimension": 2},
            project_id=project_id, session_id=session_id, request_id="close-scope-model-create",
            idempotency_key="close-scope-model-create")
        assert created["success"] is True, created
        ref = created["execution"]["model_ref"]
        target_ref[0] = ref
        tag = ref["model_tag"]

        project_root = projects / "close-scope"
        source = project_root / "src" / "Recipe.java"
        source.parent.mkdir(parents=True)
        source.write_text("public final class Recipe { public static Object run() { return 42; } }\n",
                          encoding="utf-8")
        source_record = register_project_artifact(
            project_root, daemon.store, "src/Recipe.java", project_id=project_id,
            role="java-source", classification="project_local",
            host_identity=local_artifact_host_identity(),
            engine_host_identity=local_engine_host_identity(endpoint),
            request_id="close-scope-source-register", registering_operation_id="artifact.register",
            artifact_type="text/x-java-source", format_version="java-source/1",
        )
        original_execute = worker.execute_java

        def execute_and_mutate(model_tag, *args, **kwargs):
            reply = original_execute(model_tag, *args, **kwargs)
            worker.models[model_tag].label("unsaved-java-change")
            return reply

        worker.execute_java = execute_and_mutate
        executed = _dispatch_managed(daemon, "code.execute_java", {
            "source_artifact": source_record["path"], "entrypoint": "Recipe",
            "arguments": {}, "mode": "trusted",
        }, project_id=project_id, session_id=session_id, model_ref=ref, revision=0,
            request_id="close-scope-java-execute", idempotency_key="close-scope-java-execute")
        assert executed["success"] is True, executed
        revision = service.ledger._models[tag].revision
        assert revision == 1

        ordinary_close = _dispatch_managed(daemon, "model.close", {"discard_changes": False},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
            request_id="close-scope-ordinary", idempotency_key="close-scope-ordinary")
        assert ordinary_close["success"] is False
        assert ordinary_close["error"]["code"] == "UNSAVED_CHANGES", ordinary_close
        assert tag in worker.models
        assert provider.scopes == []

        wrong_scope = _dispatch_managed(daemon, "model.close", {
            "discard_changes": True, "authorization_ref": "wrong-action-or-expired"},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
            request_id="close-scope-wrong", idempotency_key="close-scope-wrong")
        assert wrong_scope["success"] is False
        assert wrong_scope["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert tag in worker.models
        assert provider.scopes[-1][1] == "model.close"
        assert provider.scopes[-1][2] == {"model_ref": ref, "expected_revision": revision,
                                          "destination_ref": None}

        authorized = _dispatch_managed(daemon, "model.close", {
            "discard_changes": True, "authorization_ref": "valid-close-ref"},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
            request_id="close-scope-authorized", idempotency_key="close-scope-authorized")
        assert authorized["success"] is True, authorized
        assert authorized["data"]["terminal_close_verified"] is True
        assert tag not in worker.models
        assert set(worker.models) == {"m", "foreign"}
        record = daemon.store.get_operation(authorized["execution"]["operation_id"])
        assert "authorization_ref" not in record["metadata"]["arguments"]
        assert record["metadata"]["arguments"]["authorization_ref_sha256"] == hashlib.sha256(
            b"valid-close-ref").hexdigest()
    finally:
        daemon.close()


def test_control_daemon_model_routes_and_recipe_use_durable_worker_execution(tmp_path, monkeypatch):
    """Exercise the real public dispatcher, serial queue and OperationStore.

    The Worker is synthetic software evidence only; this test never launches
    COMSOL or claims native solver behavior.
    """
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    projects = tmp_path / "projects"
    projects.mkdir()
    install_root = _runtime_root(tmp_path / "comsol")
    endpoint = "127.0.0.1:25432"
    worker = _Worker(install_root, endpoint)
    service = ExecutionService(SessionLedger("session-daemon", "server-daemon", permissions={
        "inspect", "project_write", "compute", "trusted_code", "host_control"}), worker,
        project_root=projects)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker, project_root=projects)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {
        "runtime_id": runtime_id_for_root(install_root), "worker_instance_id": "worker-1",
        "connection_epoch": 1, "server_instance_id": "server-daemon", "endpoint": endpoint,
    }
    daemon.backend.persist()
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"software_fixture": True})
    from comsol_mcp import _g2_engine

    monkeypatch.setattr(_g2_engine, "find_nodes", lambda *_args, **_kwargs: {
        "status": "SUCCEEDED", "results": [{"path": {"segments": []}, "tag": "root"}],
        "complete": True, "truncated": False, "next_cursor": None, "errors": [],
    })
    try:
        created_project = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "managed-models", "workspace": "managed-models",
                          "policy": {"permissions": ["inspect", "project_write", "compute",
                                                        "trusted_code", "host_control"]}},
            "execution": {"request_id": "create-project", "idempotency_key": "create-project"},
        })
        assert created_project["success"] is True, created_project
        project_id = created_project["data"]["project"]["project_id"]
        session_id = service.ledger.session_id
        project_root = projects / "managed-models"

        created = _dispatch_managed(daemon, "model.create", {"label": "Lifecycle Fixture", "dimension": 2},
            project_id=project_id, session_id=session_id, request_id="model-create", idempotency_key="model-create")
        assert created["success"] is True, created
        ref = created["execution"]["model_ref"]
        assert ref["model_tag"] == "mcp1"
        revision = service.ledger._models["mcp1"].revision
        worker_call_count = len(worker.models["mcp1"].calls)
        stale = _dispatch_managed(daemon, "model.clone", {"label": "Must Not Clone"},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision + 1,
            request_id="model-clone-stale", idempotency_key="model-clone-stale")
        assert stale["success"] is False
        assert stale["error"]["code"] == "REVISION_CONFLICT"
        assert set(worker.models) == {"mcp1", "foreign", "m"}
        assert len(worker.models["mcp1"].calls) == worker_call_count

        listed = _dispatch_managed(daemon, "model.list", {}, project_id=project_id, session_id=session_id,
                                   request_id="model-list")
        assert listed["success"] is True, listed
        assert listed["data"]["adopted"] is False
        assert {row["model_tag"] for row in listed["data"]["models"]} >= {"mcp1", "foreign"}
        assert next(row for row in listed["data"]["models"] if row["model_tag"] == "foreign")["model_ref"] is None

        tree = _dispatch_managed(daemon, "model.tree", {"path": {"segments": []}, "depth": 0, "limit": 10},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
            request_id="model-tree")
        assert tree["success"] is True, tree
        assert tree["data"]["nodes"][0]["tag"] == "root"

        (project_root / "exports").mkdir()
        saved = _dispatch_managed(daemon, "model.save", {"destination": "exports/model.mph", "overwrite": False},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
            request_id="model-save", idempotency_key="model-save")
        assert saved["success"] is True, saved
        assert saved["data"]["include_solution"] is True
        assert saved["data"]["live_filepath_preserved"] is True
        revision = service.ledger._models["mcp1"].revision

        original_sha = saved["data"]["sha256"]
        worker.models["mcp1"]._save_payload = b"ControlDaemon overwrite software fixture"
        # Route through the production Windows branch while substituting only
        # the kernel adapter. The no-clobber ArtifactStore publisher, daemon
        # dispatch, authorization, queue, operation journal, and registration
        # remain the real software implementations.
        from types import SimpleNamespace
        from comsol_mcp import _windows_model_publish, _windows_parameter_publish
        publication_api = _ModelPublicationApi()
        with monkeypatch.context() as windows_route:
            windows_route.setattr(models, "sys", SimpleNamespace(platform="win32"))
            windows_route.setattr(_windows_model_publish, "os", _WindowsModelModuleOS())
            windows_route.setattr(_windows_parameter_publish, "_API", lambda: publication_api)
            windows_route.setattr(_windows_parameter_publish, "pinned_parent", _model_publication_test_parent)
            overwritten = _dispatch_managed(daemon, "model.save", {
                "destination": "exports/model.mph", "overwrite": True,
            }, project_id=project_id, session_id=session_id, model_ref=ref, revision=revision,
               request_id="model-save-overwrite", idempotency_key="model-save-overwrite")
        assert overwritten["success"] is True, overwritten
        assert overwritten["data"]["sha256"] != original_sha
        assert overwritten["data"]["overwrite_policy"] == "OWNED_REGISTERED_TARGET_JOURNALLED_NO_CLOBBER"
        assert overwritten["data"]["atomic_new_file_publication"] is True
        assert overwritten["data"]["atomic_path_replacement"] is False
        assert overwritten["data"]["visibility_gap_possible"] is True
        assert overwritten["data"]["overwrite_recovery"]["status"] == "COMMITTED"
        assert hashlib.sha256((project_root / "exports" / "model.mph").read_bytes()).hexdigest() == overwritten["data"]["sha256"]
        saved = overwritten

        loaded = _dispatch_managed(daemon, "model.load", {"artifact_id": saved["data"]["artifact_id"]},
            project_id=project_id, session_id=session_id, request_id="model-load", idempotency_key="model-load")
        assert loaded["success"] is True, loaded
        loaded_ref = loaded["execution"]["model_ref"]
        assert loaded_ref["model_tag"] == "mcp_load2"
        assert loaded["data"]["artifact_id"] == saved["data"]["artifact_id"]

        restored_dir = project_root / "restore"
        restored_dir.mkdir()
        with zipfile.ZipFile(restored_dir / "recovered.mph", "w") as archive:
            archive.writestr("model/fixture.txt", "restored model bytes")
        registered = _dispatch_managed(daemon, "artifact.register", {
            "project_id": project_id, "idempotency_key": "register-restored-mph",
            "path": "restore/recovered.mph", "role": "restored_model", "classification": "project_local",
        }, project_id=project_id, session_id=session_id, request_id="register-restored-mph",
           idempotency_key="register-restored-mph")
        assert registered["success"] is True, registered
        restored = _dispatch_managed(daemon, "model.load", {"artifact_id": registered["data"]["artifact_id"]},
            project_id=project_id, session_id=session_id, request_id="load-restored-mph",
            idempotency_key="load-restored-mph")
        assert restored["success"] is True, restored
        assert restored["execution"]["model_ref"]["model_tag"] == "mcp_load3"
        assert "mcp1" in worker.models and "foreign" in worker.models

        revision = service.ledger._models["mcp1"].revision
        cloned = _dispatch_managed(daemon, "model.clone", {"label": "Clone"}, project_id=project_id,
            session_id=session_id, model_ref=ref, revision=revision, request_id="model-clone",
            idempotency_key="model-clone")
        assert cloned["success"] is True, cloned
        clone_ref = cloned["data"]["model_ref"]
        assert clone_ref["model_tag"] == "mcp_clone4"
        assert cloned["data"]["source_preserved"] is True
        assert "mcp1" in worker.models and clone_ref["model_tag"] in worker.models

        close_revision = service.ledger._models[clone_ref["model_tag"]].revision
        close_args = {"discard_changes": False}
        closed = _dispatch_managed(daemon, "model.close", close_args, project_id=project_id,
            session_id=session_id, model_ref=clone_ref, revision=close_revision, request_id="model-close",
            idempotency_key="model-close")
        assert closed["success"] is True, closed
        assert closed["data"]["terminal_close_verified"] is True
        assert clone_ref["model_tag"] not in worker.models
        remove_count = len(worker.removed)
        replay = _dispatch_managed(daemon, "model.close", close_args, project_id=project_id,
            session_id=session_id, model_ref=clone_ref, revision=close_revision, request_id="model-close",
            idempotency_key="model-close")
        assert replay == closed
        assert len(worker.removed) == remove_count
        conflict = _dispatch_managed(daemon, "model.close", {"discard_changes": True}, project_id=project_id,
            session_id=session_id, model_ref=clone_ref, revision=close_revision, request_id="model-close",
            idempotency_key="model-close")
        assert conflict["success"] is False
        assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(worker.removed) == remove_count

        source = project_root / "src" / "Recipe.java"
        source.parent.mkdir(parents=True)
        source.write_text("public final class Recipe { public static Object run() { return 42; } }\n",
                          encoding="utf-8")
        source_record = register_project_artifact(
            project_root, daemon.store, "src/Recipe.java", project_id=project_id, role="java-source",
            classification="project_local", host_identity=local_artifact_host_identity(),
            engine_host_identity=local_engine_host_identity(endpoint), request_id="recipe-source-register",
            registering_operation_id="artifact.register", artifact_type="text/x-java-source")

        execute = _dispatch_managed(daemon, "code.execute_java", {
            "source_artifact": source_record["path"], "entrypoint": "Recipe",
            "arguments": {"count": 2}, "mode": "trusted",
        }, project_id=project_id, session_id=session_id, model_ref=ref,
            revision=service.ledger._models["mcp1"].revision, request_id="recipe-smoke-execute",
            idempotency_key="recipe-smoke-execute")
        assert execute["success"] is True, execute
        execution_record = daemon.store.get_operation(execute["execution"]["operation_id"])
        assert execution_record["status"] == "SUCCEEDED"
        assert execution_record["result"]["data"]["execution_success"] is True
        assert execution_record["result"]["execution"]["runtime_observation"]["status"] == "OBSERVED"
        assert execution_record["result"]["data"]["source_sha256"] == source_record["sha256"]
        event_rows = daemon.store.events(daemon.store.operation_job(execute["execution"]["operation_id"])["job_id"], limit=1000)
        assert {row["metadata"].get("phase") for row in event_rows
                if row["event"] == "worker_request" and row["metadata"].get("kind") == "code_execute"} == {
                    "submitted", "observed"}

        current_revision = service.ledger._models["mcp1"].revision
        recipe = _dispatch_managed(daemon, "code.recipe_register", {
            "source_artifact": source_record["artifact_id"],
            "input_schema": {"type": "object", "properties": {"count": {"type": "integer"}},
                             "required": ["count"], "additionalProperties": False},
            "test_refs": [execute["execution"]["operation_id"]],
        }, project_id=project_id, session_id=session_id, model_ref=ref, revision=current_revision,
            request_id="recipe-register", idempotency_key="recipe-register")
        assert recipe["success"] is True, (recipe.get("error") or {}).get("details")
        assert recipe["data"]["test_refs"][0]["scope"] == "EXECUTION_SMOKE"
        recipe_id = recipe["data"]["recipe_id"]
        recipe_record = daemon.store.get_metadata("artifacts", recipe_id)
        assert recipe_record["role"] == "code_recipe"
        assert recipe_record["provenance"]["source_artifact_sha256"] == source_record["sha256"]

        service.ledger.permissions.discard("trusted_code")
        denied = _dispatch_managed(daemon, "code.recipe_run", {"recipe_id": recipe_id, "arguments": {"count": 3}},
            project_id=project_id, session_id=session_id, model_ref=ref,
            revision=service.ledger._models["mcp1"].revision, request_id="recipe-run-denied",
            idempotency_key="recipe-run-denied")
        service.ledger.permissions.add("trusted_code")
        assert denied["success"] is False
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert len(worker.executions) == 1

        run = _dispatch_managed(daemon, "code.recipe_run", {"recipe_id": recipe_id, "arguments": {"count": 3}},
            project_id=project_id, session_id=session_id, model_ref=ref,
            revision=service.ledger._models["mcp1"].revision, request_id="recipe-run",
            idempotency_key="recipe-run")
        assert run["success"] is True, run
        assert run["data"]["status"] == "EXECUTED"
        assert worker.executions[-1][3] == {"count": 3}
        assert worker.executions[-1][1] == str(project_root / source_record["path"])

        invalid = _dispatch_managed(daemon, "code.recipe_run", {"recipe_id": recipe_id, "arguments": {"count": "bad"}},
            project_id=project_id, session_id=session_id, model_ref=ref,
            revision=service.ledger._models["mcp1"].revision, request_id="recipe-run-invalid",
            idempotency_key="recipe-run-invalid")
        assert invalid["success"] is False
        assert invalid["error"]["code"] == "RECIPE_ARGUMENTS_INVALID"
        assert len(worker.executions) == 2

        # The execution-smoke record is historical provenance. Closing the
        # tested ModelRef, making a fresh target, and reconnecting a new
        # Worker epoch must not invalidate the registered recipe.
        source_revision = service.ledger._models["mcp1"].revision
        removed_before_unsaved_close = len(worker.removed)
        unsaved_close = _dispatch_managed(daemon, "model.close", {"discard_changes": False},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=source_revision,
            request_id="recipe-smoke-model-close-unsaved", idempotency_key="recipe-smoke-model-close-unsaved")
        assert unsaved_close["success"] is False
        assert unsaved_close["error"]["code"] == "UNSAVED_CHANGES"
        assert len(worker.removed) == removed_before_unsaved_close
        assert ref["model_tag"] in worker.models
        # A successful save records the post-ticket terminal revision as the
        # durable saved baseline; only then can ordinary close proceed.
        final_saved = _dispatch_managed(daemon, "model.save", {
            "destination": "exports/final-source.mph", "overwrite": False,
        }, project_id=project_id, session_id=session_id, model_ref=ref, revision=source_revision,
           request_id="recipe-smoke-model-save", idempotency_key="recipe-smoke-model-save")
        assert final_saved["success"] is True, final_saved
        source_revision = service.ledger._models["mcp1"].revision
        closed_source = _dispatch_managed(daemon, "model.close", {"discard_changes": False},
            project_id=project_id, session_id=session_id, model_ref=ref, revision=source_revision,
            request_id="recipe-smoke-model-close", idempotency_key="recipe-smoke-model-close")
        assert closed_source["success"] is True, closed_source
        replacement = _dispatch_managed(daemon, "model.create", {"label": "Recipe target", "dimension": 2},
            project_id=project_id, session_id=session_id, request_id="recipe-target-create",
            idempotency_key="recipe-target-create")
        assert replacement["success"] is True, replacement
        target_ref = replacement["execution"]["model_ref"]
        endpoint = "127.0.0.1:25433"
        worker.endpoint = endpoint
        daemon.backend.endpoint_key = endpoint
        worker.instance_id = "worker-reconnected"
        worker.worker_generation = 2
        daemon.backend.worker_identity = {
            "runtime_id": runtime_id_for_root(install_root),
            "worker_instance_id": "worker-reconnected", "connection_epoch": 2,
            "server_instance_id": "server-daemon", "endpoint": endpoint,
        }
        replay_on_new_target = _dispatch_managed(daemon, "code.recipe_run", {
            "recipe_id": recipe_id, "arguments": {"count": 4}},
            project_id=project_id, session_id=session_id, model_ref=target_ref,
            revision=service.ledger._models[target_ref["model_tag"]].revision,
            request_id="recipe-run-new-target", idempotency_key="recipe-run-new-target")
        assert replay_on_new_target["success"] is True, replay_on_new_target
        assert replay_on_new_target["data"]["status"] == "EXECUTED"
        assert [row["test_ref"] for row in replay_on_new_target["data"]["test_refs"]] == [
            execute["execution"]["operation_id"]]
        assert worker.executions[-1][0] == target_ref["model_tag"]
        assert worker.executions[-1][3] == {"count": 4}

        # A later duplicate/tampered durable Worker event must not make the
        # already accepted test reference reusable as trusted evidence.
        job_id = daemon.store.operation_job(execute["execution"]["operation_id"])["job_id"]
        observed_event = next(row for row in daemon.store.events(job_id, limit=1000)
                              if row["event"] == "worker_request"
                              and row["metadata"].get("kind") == "code_execute"
                              and row["metadata"].get("phase") == "observed")
        forged_event = dict(observed_event["metadata"])
        forged_event["request_hash"] = "0" * 64
        daemon.store.add_event(job_id, "worker_request", forged_event)
        tampered = _dispatch_managed(daemon, "code.recipe_register", {
            "source_artifact": source_record["artifact_id"],
            "input_schema": {"type": "object", "properties": {"count": {"type": "integer"}},
                             "required": ["count"], "additionalProperties": False},
            "test_refs": [execute["execution"]["operation_id"]],
        }, project_id=project_id, session_id=session_id, model_ref=target_ref,
            revision=service.ledger._models[target_ref["model_tag"]].revision, request_id="recipe-register-tampered",
            idempotency_key="recipe-register-tampered")
        assert tampered["success"] is False
        assert tampered["error"]["code"] == "RECIPE_TEST_UNVERIFIED"
        assert len(worker.executions) == 3

        # The same post-exchange rollback interruption must cross the real
        # ControlDaemon/OperationStore boundary as UNKNOWN, keep retry fenced,
        # and retain the exact old registered bytes for recovery.
        target_tag = target_ref["model_tag"]
        worker.models[target_tag]._save_payload = b"unique registered rollback base MPH"
        target_revision = service.ledger._models[target_tag].revision
        target_saved = _dispatch_managed(daemon, "model.save", {
            "destination": "exports/rollback-target.mph", "overwrite": False,
        }, project_id=project_id, session_id=session_id, model_ref=target_ref,
           revision=target_revision, request_id="model-save-rollback-base",
           idempotency_key="model-save-rollback-base")
        assert target_saved["success"] is True, target_saved
        target_revision = service.ledger._models[target_tag].revision
        target_path = project_root / "exports" / "rollback-target.mph"
        old_target_bytes = target_path.read_bytes()
        worker.models[target_tag]._save_payload = b"new bytes with durable rollback interruption"

        import comsol_mcp._artifact_store as artifact_store

        real_hash = artifact_store._hash_regular_artifact
        target_reads = 0

        def inject_exchange_readback_mismatch(path):
            nonlocal target_reads
            digest, identity = real_hash(Path(path))
            if Path(path) == target_path:
                target_reads += 1
                if target_reads == 2:
                    return digest, {**identity, "size": identity["size"] + 1}
            return digest, identity

        real_exchange = models._exchange_names
        exchange_calls = 0

        def interrupt_exchange_rollback(directory_fd, first_name, second_name):
            nonlocal exchange_calls
            exchange_calls += 1
            if exchange_calls == 1:
                return real_exchange(directory_fd, first_name, second_name)
            raise OSError("injected durable rollback interruption")

        monkeypatch.setattr(artifact_store, "_hash_regular_artifact", inject_exchange_readback_mismatch)
        monkeypatch.setattr(models, "_exchange_names", interrupt_exchange_rollback)
        failed_save = _dispatch_managed(daemon, "model.save", {
            "destination": "exports/rollback-target.mph", "overwrite": True,
        }, project_id=project_id, session_id=session_id, model_ref=target_ref,
           revision=target_revision, request_id="model-save-rollback-unknown",
           idempotency_key="model-save-rollback-unknown")
        assert failed_save["success"] is False, failed_save
        assert failed_save["error"]["code"] == "EXECUTION_STATE_UNKNOWN", failed_save
        failure_details = failed_save["error"]["details"]
        assert failure_details["cause_code"] == "OVERWRITE_STATE_UNKNOWN"
        assert failure_details["safe_retry"] is False
        failed_operation_row = daemon.store.db.execute(
            "SELECT operation_id FROM operations WHERE request_id=?", ("model-save-rollback-unknown",)
        ).fetchone()
        assert failed_operation_row is not None
        failed_operation = daemon.store.get_operation(failed_operation_row[0])
        failed_job = daemon.store.operation_job(failed_operation_row[0])
        assert failed_operation["status"] == "UNKNOWN"
        assert failed_job["status"] == "UNKNOWN"
        assert failed_operation["result"]["error"]["safe_retry"] is False
        assert failed_job["result"]["error"]["safe_retry"] is False
        recovery_relative = failure_details["recovery_path"]
        recovery_path = project_root / recovery_relative
        assert recovery_path.read_bytes() == old_target_bytes
        durable_receipt = project_root / failure_details["failure_receipt"]
        assert json.loads(durable_receipt.read_text())["recovery_details"]["recovery_path"] == recovery_relative
    finally:
        daemon.close()
