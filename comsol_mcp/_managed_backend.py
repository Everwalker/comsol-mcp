"""Bound legacy services running exclusively inside the serialized daemon."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any

from ._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping, canonical_project_path
from ._execution_service import ExecutionService
from ._runtime_state import save_runtime_state, restore_runtime_state

# This is deliberately capability metadata rather than a promise of engine CAS.
# The only live external mutation probe so far changed a scalar parameter.  The
# documented ModelChangedHandler callback was registered, but did not advance
# during that probe; property-tree edits therefore remain outside verified
# detection coverage.  Keep this scope visible to callers instead of silently
# treating a stable fingerprint as proof that a whole model is unchanged.
EXTERNAL_CHANGE_DETECTION_CAPABILITY = {
    "coverage": "parameters+shallow_tree_identity",
    "status": "SCOPED_EVIDENCE_ONLY",
    "event_notifications": "UNVERIFIED",
    "full_model_property_coverage": "UNVERIFIED",
    "cas_guarantee": "NONE",
    "evidence_source": "evidence/phase2/followup/20260918T142300Z-external-api",
    "evidence_environment": "macOS arm64; COMSOL 6.4.0.293",
    "other_platforms_or_versions": "UNVERIFIED",
}


def _external_change_detection_metadata() -> dict[str, str]:
    """Return a fresh, scope-limited external-change capability declaration."""
    return dict(EXTERNAL_CHANGE_DETECTION_CAPABILITY)


class ProcessLock:
    """Fail closed when another local process owns the same execution scope."""
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or path.parent.is_symlink():
            raise ExecutionContractError("PERMISSION_DENIED", "lock path must not be a symlink")
        self.handle = path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write("0"); self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError) as exc:
            self.handle.close()
            raise ExecutionContractError("ENGINE_BUSY", "execution scope is already owned or cannot be locked") from exc

    def close(self):
        self.handle.close()


def collect_legacy_registry():
    from . import _tools_connection, _tools_workflow, _tools_model, _tools_params, _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver, _tools_phase1
    class Collector:
        def __init__(self): self.functions = {}
        def add_tool(self, fn, **_): self.functions[fn.__name__] = fn
    collector = Collector()
    for module in (_tools_connection, _tools_workflow, _tools_model, _tools_params, _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver, _tools_phase1):
        module.register(collector)
    return {name: (lambda args, fn=fn: fn(**args)) for name, fn in collector.functions.items()}


class ManagedBackend:
    def __init__(self, home, store, *, service=None, registry=None, worker=None):
        self.home, self.store = Path(home), store
        self.service, self.worker = service, worker
        self.registry = dict(registry) if registry is not None else collect_legacy_registry()
        self.server_lock = None
        self.worker_identity = None
        self.endpoint_key = None
        self.project_root = Path(__file__).resolve().parents[1]
        self.cached = {"connected": False, "server_ownership": "shared",
                       "external_change_detection": _external_change_detection_metadata()}
        if service is not None:
            self.cached.update(connected=True, session_id=service.ledger.session_id)

    def context(self, operation_id, callback):
        if self.worker is not None:
            return self.worker.operation_context(operation_id, on_request_event=callback)
        return nullcontext()

    def connect(self, arguments, operation_id, event_callback):
        from ._java_worker import PersistentJavaWorker, JavaWorkerPaths
        import comsol_mcp._server as srv
        host = str(arguments.get("host") or "localhost")
        port = arguments.get("port", srv.DEFAULT_PORT)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ExecutionContractError("INVALID_REQUEST", "server port must be an integer in range")
        try:
            canonical_host = socket.gethostbyname(host)
        except OSError as exc:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "server hostname could not be resolved") from exc
        key = f"{canonical_host}:{port}"
        if self.endpoint_key and key != self.endpoint_key:
            raise ExecutionContractError("PERMISSION_DENIED", "a control service is bound to one server endpoint")
        if self.worker is None:
            root = os.environ.get("COMSOL_ROOT")
            java = os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME")
            if not root or not java:
                raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "COMSOL_ROOT and COMSOL_JAVA_HOME must be configured")
            digest = hashlib.sha256(key.encode()).hexdigest()
            lock_root = Path(tempfile.gettempdir()) / f"comsol-mcp-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
            self.server_lock = ProcessLock(lock_root / f"server-{digest}.lock")
            prefs = os.environ.get("COMSOL_PREFS_DIR")
            paths = JavaWorkerPaths(Path(root), Path(java), Path(prefs) if prefs else None,
                                    project_root=self.project_root)
            self.worker = PersistentJavaWorker(paths, state_dir=self.home / "worker")
            self.endpoint_key = key
        # Explicit reconnect can replace a confirmed-dead Worker. start() refuses
        # an alive but unreachable endpoint and never replays an engine request.
        self.worker.start()
        with self.context(operation_id, event_callback):
            health = self.worker.health()
            if health.get("connected"):
                if health.get("server") != key:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "worker is connected to a different endpoint")
            else:
                self.worker.client().connect(port, canonical_host)
            runtime = self.worker.runtime_metadata()
            instance = runtime.get("instance_id")
            generation = runtime.get("generation")
            if not instance or type(generation) is not int:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker connection identity unavailable")
            # ``server_instance_id`` is a compatibility wire field for the
            # bound execution epoch, not a durable OS-process identity.  It
            # deliberately changes when the Worker reconnects so old handles
            # fail closed; evidence must retain the separately verified server
            # PID/endpoint when it needs to identify the persistent server.
            server_id = hashlib.sha256(f"{key}:{instance}:{generation}".encode()).hexdigest()
            session_id = "session-" + hashlib.sha256(key.encode()).hexdigest()[:24]
            identity = {"runtime_id": "runtime-" + instance, "worker_instance_id": instance,
                        "connection_epoch": generation, "server_instance_id": server_id, "endpoint": key}
            if self.service is None or self.worker_identity != identity:
                saved = self.store.get_metadata("sessions", session_id)
                if saved:
                    ledger, unknown = restore_runtime_state(self.store, session_id, identity)
                else:
                    ledger = SessionLedger(session_id, server_id)
                backend = self
                class Adapter:
                    def model_snapshot(self, tag):
                        raw = backend.worker.backend_snapshot(tag)
                        if raw.get("server_instance_id") != key or raw.get("generation") != generation or raw.get("instance_id") != instance:
                            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "worker connection epoch changed")
                        return {**raw, "server_instance_id": server_id}
                self.worker_identity = identity
                self.service = ExecutionService(ledger, Adapter(), project_root=self.project_root,
                                                on_state_change=lambda event: self.persist())
            srv._remote_client_factory = self.worker.client
            srv._client = self.worker.client()
            srv._client_connected = True
            srv._connected_host, srv._connected_port = canonical_host, port
            srv._server_started_by_mcp = False
            self.cached = {"connected": True, "server_ownership": "shared", "endpoint": key,
                           "session_id": session_id, "server_instance_id": server_id,
                           "external_change_detection": _external_change_detection_metadata(),
                           "worker": {k: v for k, v in runtime.items() if k != "token"}}
            self.persist()
            model_name = str(arguments.get("model_name") or "").strip()
            if model_name:
                found = [m for m in srv._client.models() if m.name() == model_name or str(m.java.tag()) == model_name]
                if len(found) != 1:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_name is not unique on this server")
                return self.adopt(str(found[0].java.tag()))
            return {"success": True, "data": dict(self.cached),
                    "execution": {"session_id": session_id, "model_ref": None, "revision": None}}

    def persist(self):
        if self.service is None or self.worker_identity is None:
            return
        ledger = self.service.ledger
        save_runtime_state(self.store, ledger, self.worker_identity)
        for state in ledger._models.values():
            self.store.put_metadata("revisions", json.dumps(state.ref.as_dict(), sort_keys=True), {
                "model_ref": state.ref.as_dict(), "revision": state.revision, "dirty": state.dirty,
                "fingerprint": state.fingerprint, "active_operation_id": state.active_operation_id})

    def adopt(self, tag):
        import comsol_mcp._server as srv
        from ._model import _set_current_model
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connect to a server first")
        if "project_write" not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", "model adoption requires project_write")
        model = self.worker.client().model(tag)
        _set_current_model(model, origin="adopted-by-tag")
        metadata = self.service.bind_model(tag, ownership="mcp_owned" if tag in srv._mcp_owned_model_tags else "user_owned")
        self.persist()
        return {"success": True, "data": {"model_tag": tag}, **metadata}

    def invoke(self, operation, arguments, execution, operation_id, event_callback):
        if operation == "server_connect":
            return self.connect(arguments, operation_id, event_callback)
        local_operations = {"check_server_port", "workflow_info", "mcp_tool_audit", "configure_single_main_workflow"}
        if self.service is None and operation in local_operations:
            if execution.get("model_ref") or execution.get("session_id") or execution.get("expected_revision") is not None:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "no model session is connected")
            args = dict(arguments)
            for field in ("current_main_model_path", "snapshot_dir"):
                if args.get(field):
                    args[field] = str(canonical_project_path(self.project_root, args[field]))
            return ExecutionService._decode_callback(self.registry[operation](args))
        starter = operation in {"start_visible_main_workflow", "start_visible_main_workflow_async"}
        selections = {"model_create", "model_load", "load_visible_main_model", "load_current_main_model"}
        if (starter or operation in selections) and execution.get("model_ref"):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model selection requires an unbound request; use its returned model_ref")
        managed_connection = None
        if starter:
            # Keep lifecycle changes inside the managed connection path. Calling
            # legacy server_connect here would disconnect the persistent Worker
            # and invalidate its epoch behind the execution service's back.
            if arguments.get("path"):
                canonical_project_path(self.project_root, arguments["path"])
            managed_connection = self.connect(arguments, operation_id, event_callback)
        if self.service is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connect to a server first")
        session = execution.get("session_id")
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        ref = model_ref_from_mapping(execution["model_ref"]) if execution.get("model_ref") else None
        if ref:
            self.service.ledger._state_for(ref)  # reject stale refs before any engine call
        with self.context(operation_id, event_callback):
            if operation == "model_adopt":
                return self.adopt(arguments["model_tag"])
            if operation == "model_inspect":
                if ref is None:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref is required")
                if arguments.get("refresh", False):
                    return {"success": True, "data": {}, **self.service.reconcile(ref)}
                return {"success": True, "data": {}, **self.service.inspect(ref)}
            if operation in {"server_start", "server_disconnect", "prune_loaded_models"}:
                raise ExecutionContractError("PERMISSION_DENIED", "shared-server lifecycle changes are not enabled by this backend")
            import comsol_mcp._server as srv
            from ._model import _set_current_model
            if ref and self.worker is not None:
                _set_current_model(self.worker.client().model(ref.model_tag), origin="bound-request")
            # Validate configured paths too, not only paths present in this request.
            if self.worker is not None:
                from ._state import _read_workflow_state
                workflow = _read_workflow_state()
                for field in ("current_main_model_path", "snapshot_dir"):
                    if workflow.get(field):
                        canonical_project_path(self.project_root, workflow[field])
            actual = {"run_study_async": "run_study", "start_visible_main_workflow_async": "start_visible_main_workflow"}.get(operation, operation)
            callback = self.registry.get(actual)
            if starter:
                from ._tools_workflow import _start_visible_main_workflow_payload
                callback = lambda args: {"success": True, "data": _start_visible_main_workflow_payload(
                    **args, action=actual, managed_connection=managed_connection)}
            if callback is None:
                raise ExecutionContractError("UNSUPPORTED_OPERATION", "operation has no managed implementation")
            result = self.service.execute_legacy(actual, callback, arguments, model_ref=ref,
                expected_revision=execution.get("expected_revision"), request_id=execution.get("request_id"),
                session_id=session, path_parameters=("path", "current_main_model_path", "snapshot_dir"))
            if not ref and (actual in selections or starter) and result.get("success") and self.worker is not None:
                tag = str(srv._current_model.java.tag())
                result["execution"] = self.service.bind_model(tag, ownership="mcp_owned" if tag in srv._mcp_owned_model_tags else "user_owned")["execution"]
                result["data"]["model_tag"] = tag
            self.persist()
            if result.get("success") and actual in {"save_model", "save_main_model_snapshot", "commit_current_main_model"}:
                self._artifacts(result, actual)
            return result

    def _artifacts(self, result, operation):
        data = result.get("data", {})
        for key in ("path", "saved_path", "snapshot_path", "file_path", "current_main_model_path"):
            value = data.get(key)
            if not value:
                continue
            path = canonical_project_path(self.project_root, value)
            if path.is_file() and path.suffix.lower() == ".mph":
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                artifact = {"path": str(path), "sha256": digest, "size": path.stat().st_size,
                            "model_ref": result.get("execution", {}).get("model_ref")}
                self.store.persist_artifact(digest, artifact)
                if operation != "save_model":
                    self.store.persist_checkpoint(digest, artifact)
