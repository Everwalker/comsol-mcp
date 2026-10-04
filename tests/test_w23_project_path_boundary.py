"""Production-class, no-process regressions for project-scoped legacy paths.

The ControlDaemon, ProjectAuthority, ManagedBackend, ExecutionService,
OperationStore, session registry and project/session scopes are real.  Only the
remote Worker/COMSOL boundary is represented by a local in-process protocol
adapter; it has no process, socket, JVM, native runtime or network behavior.
"""
from contextlib import nullcontext
from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import collect_legacy_registry
from comsol_mcp._session_context import (
    CanonicalSocket,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    use_session_context,
)
from comsol_mcp._state import _read_workflow_state, _write_workflow_state, project_workflow_state_scope


class _NoProcessWorker:
    """Tiny Worker protocol double; every command stays in this Python object."""

    generation = 7

    def __init__(self):
        self.calls = []
        self.labels = {}
        self._next_tag = 0

    def client(self):
        from comsol_mcp._java_worker import RemoteClient

        return RemoteClient(self)

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "server-a",
            "fingerprint": f"fingerprint:{tag}",
            "external_event_counter": 0,
        }

    def submit(self, kind, payload, **_kwargs):
        body = dict(payload)
        self.calls.append((kind, body))
        if kind == "modelutil":
            method, args = body["method"], body.get("args", [])
            if method == "uniquetag":
                self._next_tag += 1
                result = f"mcp{self._next_tag}"
            elif method in {"create", "load"}:
                tag = args[0]
                if method == "load":
                    self.labels[tag] = Path(args[1]).stem
                result = {
                    "$worker_handle": f"model:{tag}",
                    "generation": self.generation,
                    "java_type": "com.comsol.model.Model",
                }
            else:
                raise AssertionError(f"unexpected modelutil command: {method}")
        elif kind == "model":
            tag = body["tag"]
            result = {
                "$worker_handle": f"model:{tag}",
                "generation": self.generation,
                "java_type": "com.comsol.model.Model",
            }
        elif kind == "call":
            tag = body["handle"].removeprefix("model:")
            method, args = body["method"], body.get("args", [])
            if method == "label":
                if args:
                    self.labels[tag] = args[0]
                    result = None
                else:
                    result = self.labels.get(tag, tag)
            elif method == "tag":
                result = tag
            elif method == "getFilePath":
                result = ""
            else:
                raise AssertionError(f"unexpected model command: {method}")
        else:
            raise AssertionError(f"unexpected no-process Worker command: {kind}")
        return {"ok": True, "status": "SUCCEEDED", "result": result}


def _create_project(daemon, *, label, workspace, permissions=None):
    key = f"project-create-{label}"
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": workspace,
            "policy": {"permissions": permissions or ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": key, "idempotency_key": key},
    })
    assert result["success"] is True, result
    return result["data"]["project"]


@pytest.fixture
def runtime(tmp_path):
    authorized_root = tmp_path / "authorized"
    authorized_root.mkdir()
    worker = _NoProcessWorker()
    service = ExecutionService(
        SessionLedger("session-a", "server-a"), worker, project_root=authorized_root,
    )
    registry = collect_legacy_registry()
    callbacks = {"save_model": [], "model_load": [], "run_study": [], "workflow_info": []}
    registry["save_model"] = lambda args: callbacks["save_model"].append(dict(args)) or {
        "success": True, "data": {"saved": True},
    }
    registry["model_load"] = lambda args: callbacks["model_load"].append(dict(args)) or {
        "success": True, "data": {"model_tag": "unexpected-load"},
    }
    registry["run_study"] = lambda args: callbacks["run_study"].append(dict(args)) or {
        "success": True, "data": {"completed": True},
    }
    registry["workflow_info"] = lambda args: callbacks["workflow_info"].append(dict(args)) or {
        "success": True, "data": {"status": "ready"},
    }
    daemon = ControlDaemon(
        tmp_path / "control", project_root=authorized_root, service=service,
        worker=worker, registry=registry,
    )
    project = _create_project(daemon, label="science", workspace="science")
    project_root = Path(project["workspace"])
    runtime_config = SessionRuntimeConfig(
        runtime_id="no-process-runtime", comsol_version="test-only",
        installation_root=tmp_path / "unused-comsol",
        java_executable=tmp_path / "unused-java" / "bin" / "java",
        classpath=(tmp_path / "unused-client.jar",),
        preferences_dir=tmp_path / "unused-preferences",
        session_state_root=tmp_path / "session-state",
    )
    peer = CanonicalSocket("127.0.0.1", 49321)  # identity value only; no socket is opened
    context = SessionRuntimeContext(
        project_id=project["project_id"], session_id="session-a", project_root=project_root,
        runtime=runtime_config,
        endpoint=SessionEndpointIdentity(
            host="127.0.0.1", port=peer.port, worker_epoch=1, observed_peer=peer,
        ),
        backend=daemon.backend, worker_instance_id="no-process-worker", worker=worker,
        service=service, client=worker.client(), client_connected=True,
    )
    daemon.session_registry.register(context)
    value = {
        "authorized_root": authorized_root,
        "callbacks": callbacks,
        "context": context,
        "daemon": daemon,
        "project": project,
        "project_root": project_root,
        "sequence": 0,
        "service": service,
        "worker": worker,
    }
    try:
        yield value
    finally:
        daemon.close()


def _write_project_workflow(runtime, updates):
    context = runtime["context"]
    with use_session_context(context), project_workflow_state_scope(context.project_root):
        return _write_workflow_state(dict(updates))


def _project_request(runtime, operation, arguments, *, project=None, ref=None,
                     revision=None, session_id="session-a"):
    runtime["sequence"] += 1
    serial = runtime["sequence"]
    project = project or runtime["project"]
    execution = {
        "project_id": project["project_id"],
        "request_id": f"path-boundary-{serial}",
        "idempotency_key": f"path-boundary-{serial}",
    }
    if session_id is not None:
        execution["session_id"] = session_id
    if ref is not None:
        execution["model_ref"] = dict(ref)
        execution["expected_revision"] = revision
    return runtime["daemon"].dispatch({
        "operation": operation,
        "arguments": dict(arguments),
        "execution": execution,
    })


def _create_model(runtime, *, name="path boundary model"):
    return _project_request(runtime, "model_create", {"name": name})


def test_pathless_model_create_ignores_each_unrelated_out_of_root_workflow_path(runtime):
    outside_parent = runtime["authorized_root"]
    bad_values = {
        "current_main_model_path": str(outside_parent / "parent-main.mph"),
        "snapshot_dir": str(outside_parent / "parent-snapshots"),
    }

    for field, outside_path in bad_values.items():
        _write_project_workflow(runtime, {"current_main_model_path": "", "snapshot_dir": "", field: outside_path})
        calls_before = len(runtime["worker"].calls)
        result = _create_model(runtime, name=f"pathless-{field}")
        assert result["success"] is True, result
        assert any(
            kind == "modelutil" and payload.get("method") == "create"
            for kind, payload in runtime["worker"].calls[calls_before:]
        ), "real model_create callback did not reach the no-process Worker boundary"
        with use_session_context(runtime["context"]), project_workflow_state_scope(runtime["project_root"]):
            observed = _read_workflow_state()
        assert observed[field] == outside_path, "model_create must not clear persisted workflow state"


def test_non_create_calls_keep_configured_workflow_path_guard(runtime):
    outside_parent = runtime["authorized_root"]

    cases = (
        ("current_main_model_path", str(outside_parent / "parent-main.mph")),
        ("snapshot_dir", str(outside_parent / "parent-snapshots")),
    )
    for field, value in cases:
        _write_project_workflow(runtime, {"current_main_model_path": "", "snapshot_dir": "", field: value})
        for operation, args in (
            ("model_load", {"path": "inside/input.mph"}),
            ("workflow_info", {}),
        ):
            before = len(runtime["callbacks"][operation])
            result = _project_request(runtime, operation, args)
            assert result["success"] is False, (field, operation, result)
            assert result["error"]["code"] == "PERMISSION_DENIED", (field, operation, result)
            assert len(runtime["callbacks"][operation]) == before, (field, operation, result)


def test_supplied_model_paths_keep_relative_absolute_traversal_and_symlink_rules(runtime, tmp_path):
    _write_project_workflow(runtime, {
        "current_main_model_path": "", "snapshot_dir": "",
        "visible_main_locked": False,
    })
    created = _create_model(runtime)
    assert created["success"] is True, created
    root = runtime["project_root"]
    outside = runtime["authorized_root"] / "outside.mph"
    escape_link = root / "outside-link.mph"
    escape_link.symlink_to(outside)

    allowed = (
        ("relative", "nested/input.mph", root / "nested" / "input.mph"),
        ("absolute", str(root / "absolute.mph"), root / "absolute.mph"),
        ("normalized-internal-dotdot", "nested/../normalized.mph", root / "normalized.mph"),
    )
    for label, requested, canonical in allowed:
        before = len(runtime["callbacks"]["model_load"])
        result = _project_request(
            runtime, "model_load", {"path": requested},
        )
        assert result["success"] is True, (label, result)
        assert len(runtime["callbacks"]["model_load"]) == before + 1
        assert runtime["callbacks"]["model_load"][-1]["path"] == str(canonical)

    rejected = (
        ("parent-traversal", "../escape.mph"),
        ("absolute-outside", str(outside)),
        ("symlink-outside", str(escape_link)),
    )
    for label, requested in rejected:
        before = len(runtime["callbacks"]["model_load"])
        result = _project_request(
            runtime, "model_load", {"path": requested},
        )
        assert result["success"] is False, (label, result)
        assert result["error"]["code"] == "PERMISSION_DENIED", (label, result)
        assert len(runtime["callbacks"]["model_load"]) == before


def test_project_permission_session_binding_visible_lock_and_cross_project_guards_remain(runtime):
    _write_project_workflow(runtime, {
        "current_main_model_path": "", "snapshot_dir": "",
        "visible_main_locked": False,
    })
    created = _create_model(runtime)
    assert created["success"] is True, created
    ref = created["execution"]["model_ref"]
    revision = created["execution"]["revision"]
    create_count = sum(
        kind == "modelutil" and payload.get("method") == "create"
        for kind, payload in runtime["worker"].calls
    )

    _write_project_workflow(runtime, {
        "current_main_model_path": str(runtime["project_root"] / "main.mph"),
        "snapshot_dir": str(runtime["project_root"] / "snapshots"),
        "visible_main_locked": True, "guard_level": "strict",
    })
    locked = _create_model(runtime, name="must remain locked")
    assert locked["success"] is False
    assert "restricted while visible_main_locked=true" in str(locked)
    assert sum(
        kind == "modelutil" and payload.get("method") == "create"
        for kind, payload in runtime["worker"].calls
    ) == create_count, "visible-main lock must prevent creating another model"

    restricted = _create_project(
        runtime["daemon"], label="inspect-only", workspace="inspect-only", permissions=["inspect"],
    )
    denied = _project_request(
        runtime, "model_create", {"name": "permission denied"}, project=restricted, session_id=None,
    )
    assert denied["success"] is False
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    assert sum(
        kind == "modelutil" and payload.get("method") == "create"
        for kind, payload in runtime["worker"].calls
    ) == create_count

    other = _create_project(runtime["daemon"], label="other-project", workspace="other-project")
    cross_project = _project_request(
        runtime, "model.inspect", {"detail": "summary"}, project=other,
        ref=ref, revision=revision, session_id=None,
    )
    assert cross_project["success"] is False
    assert cross_project["error"]["code"] in {
        "PROJECT_IDENTITY_MISMATCH", "MODEL_IDENTITY_MISMATCH",
    }

    wrong_session = _project_request(
        runtime, "model.inspect", {"detail": "summary"}, ref=ref, revision=revision,
        session_id="wrong-session",
    )
    assert wrong_session["success"] is False
    assert wrong_session["error"]["code"] == "SESSION_NOT_FOUND"
