"""Bound legacy services running exclusively inside the serialized daemon."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from typing import Any, Mapping

from ._execution_contract import (
    ExecutionContractError,
    LEGACY_TOOL_EFFECTS,
    PreWriteRefusal,
    SessionLedger,
    model_ref_from_mapping,
    canonical_project_path,
)
from ._execution_service import ExecutionService
from ._runtime_state import save_runtime_state, restore_runtime_state
from ._g2_docs import OfflineDocsIndex
from ._g2_registry import IMPLEMENTED_OPERATIONS, LEGACY_FALLBACK_NAMES, is_implemented, validate_call
from ._g2_contract import NodePath
from ._g2_engine import (
    children_node, create_checkpoint, execute_transaction, find_nodes, inspect_node,
    property_entry_set, property_get, property_index_set, property_schema, property_set,
)
from ._g2_code import compile_result, describe_source, execution_result, read_source
from ._g2_transactions import TransactionStore, preview_transaction, validate_invariants
from ._g2_isolation import configured_receipt, verify_owned_server
from ._platform_paths import default_comsol_help_roots

#: G3 (W13-W16) catalogue effect -> write-ticket effect classification.  The
#: catalogue remains the single source of truth (``_g3_ops.EFFECTS``); this
#: table only translates its effect names.  An unrecognised effect fails
#: closed instead of defaulting to an inspect permission.  ``DYNAMIC`` is
#: resolved server-side to the write class: every dynamic G3 sub-action in
#: this round mutates the model, so the strictest path is the honest default.
_G3_EFFECT_MAP: dict[str, str] = {
    "READ": "inspect",
    "WRITE": "project_write",
    "STATE_WRITE": "state_write",
    "FILE_WRITE": "file_write",
    "EVALUATE": "evaluate",
    "COMPUTE": "compute",
    "TRUSTED_CODE": "trusted_code",
    "DYNAMIC": "project_write",
}


#: Operations whose subject is the live runtime, not a model.  They are routed
#: without a model_ref and without an expected_revision (C05).
RUNTIME_SCOPED_OPERATIONS = frozenset({"runtime.capabilities", "runtime.license_inspect"})


def _g3_operations() -> frozenset[str]:
    """G3 operation ids published by the domain modules (empty when absent)."""
    try:
        from ._g3_ops import IMPLEMENTED_OPERATIONS as g3_operations
    except Exception:
        return frozenset()
    return g3_operations


def _refusal_envelope(operation: str, exc: ExecutionContractError, witness: Any, *, effect: str) -> dict[str, Any]:
    """Envelope for a refusal that provably happened before any mutation.

    ``exc.stage`` is the explicit stage the raise site declared; the witness
    proves no mutation-class engine method was issued during the callback.  Both
    facts are published so a later reader never has to trust the exception class
    name: the evidence is in the envelope.
    """
    from ._domain_outcome import STAGE_VALIDATION, domain_envelope
    refusal = {
        "code": exc.code,
        "message": str(exc),
        "safe_retry": exc.safe_retry,
        "stage": exc.stage or STAGE_VALIDATION,
    }
    if exc.details:
        refusal["details"] = dict(exc.details)
    data = {
        "status": "REFUSED",
        "refused": True,
        "operation": operation,
        "refusal": refusal,
        "dispatch_stage": exc.stage or STAGE_VALIDATION,
        "witness": witness.as_dict(),
    }
    envelope = domain_envelope(operation, data, dispatch_stage=STAGE_VALIDATION, witness=witness)
    envelope["error"] = refusal
    envelope["effect"] = effect
    return envelope


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
    from . import _tools_connection, _tools_workflow, _tools_model, _tools_params, _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver, _tools_phase1, _g2_tools
    class Collector:
        def __init__(self): self.functions = {}
        def add_tool(self, fn, **_): self.functions[fn.__name__] = fn
    collector = Collector()
    for module in (_tools_connection, _tools_workflow, _tools_model, _tools_params, _tools_geometry, _tools_snapshot, _tools_physics, _tools_solver, _tools_phase1, _g2_tools):
        module.register(collector)
    return {name: (lambda args, fn=fn: fn(**args)) for name, fn in collector.functions.items()}


class ManagedBackend:
    def __init__(self, home, store, *, service=None, registry=None, worker=None, project_root=None):
        self.home, self.store = Path(home), store
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.package_resource_root = Path(__file__).resolve().parent
        self.private_control_root = self.home

        if project_root is not None:
            self.project_root = Path(project_root).resolve()
        else:
            env_project = os.environ.get("COMSOL_PROJECT_ROOT")
            if env_project:
                self.project_root = Path(env_project).resolve()
            else:
                fallback = Path(__file__).resolve().parents[1]
                if any(p in fallback.parts for p in ("site-packages", "dist-packages")):
                    raise ExecutionContractError(
                        "RUNTIME_CONFIGURATION_REQUIRED",
                        "COMSOL_PROJECT_ROOT or explicit project_root is required when running from site-packages; site-packages is not an authorized project root",
                    )
                self.project_root = fallback

        help_roots = default_comsol_help_roots(self.project_root)
        self.docs_index = OfflineDocsIndex(self.home / "docs_index.sqlite3", allowed_roots=help_roots)
        self.transactions = TransactionStore(self.home / "transactions.json")
        self.service, self.worker = service, worker
        self.registry = dict(registry) if registry is not None else collect_legacy_registry()
        self.server_lock = None
        self.worker_identity = None
        self.endpoint_key = None
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
                # Trusted Java is an explicit deployment capability.  It is
                # never implied by a successful connection and is not an OS
                # sandbox; operators must opt in before the daemon starts.
                if os.environ.get("COMSOL_MCP_TRUSTED_CODE", "").strip().lower() in {"1", "true", "yes"}:
                    ledger.permissions.add("trusted_code")
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
                           "trusted_code": {"enabled": "trusted_code" in self.service.ledger.permissions,
                                            "os_sandbox": False, "config": "COMSOL_MCP_TRUSTED_CODE"},
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
        # Registry fallback calls are revalidated against the server-side
        # catalog before nested dispatch.  The nested operation then follows
        # the same service/Worker route as a directly published action.
        if operation in {"registry_call", "operation_call"}:
            inner = arguments.get("operation_id")
            supplied_args = arguments.get("arguments", {})
            if not isinstance(supplied_args, Mapping):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            # The public fallback keeps the outer execution envelope separate
            # from the logical operation body.  Materialise its identity in a
            # detached copy before strict catalog validation; this preserves
            # one idempotency/revision gate across both dispatch layers.
            inner_args = dict(supplied_args)
            for name in ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"):
                if name not in inner_args and name in execution:
                    inner_args[name] = execution[name]
            entry = validate_call(inner, inner_args)
            nested_execution = dict(execution)
            for name in ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key"):
                if name not in nested_execution and name in inner_args:
                    nested_execution[name] = inner_args[name]
            if entry.operation_id.startswith("registry."):
                from . import _g2_registry
                if entry.operation_id == "registry.list":
                    data = _g2_registry.registry_list(domain=inner_args.get("domain"), cursor=inner_args.get("cursor"), limit=inner_args.get("limit", 100))
                elif entry.operation_id == "registry.describe":
                    data = _g2_registry.registry_describe(inner_args.get("operation_id", ""))
                elif entry.operation_id == "registry.search":
                    data = _g2_registry.registry_search(inner_args.get("query", ""), domain=inner_args.get("domain"))
                elif entry.operation_id == "registry.manifest":
                    data = _g2_registry.registry_manifest(inner_args.get("profile"))
                else:
                    data = {"operation_id": entry.operation_id}
                return {"success": True, "data": data, "execution": {"operation_id": operation_id}}
            call_args = dict(inner_args)
            if entry.operation_id in LEGACY_FALLBACK_NAMES:
                # Legacy Python callables retain their historical signatures;
                # the envelope identity belongs to the service ticket and
                # must not be injected into ``fn(**args)``.
                call_args = self._g2_body(call_args)
            return self.invoke(entry.operation_id, call_args, nested_execution, operation_id, event_callback)
        if operation in {"docs_index", "docs.index", "docs.search", "docs.get", "docs.examples", "docs.error_search",
                         "code_describe_java", "code.describe_java", "code_compile_java", "code.compile_java",
                         "code.inspect_run", "code_inspect_run",
                         "transaction_preview", "transaction.preview", "checkpoint.list", "checkpoint.inspect", "checkpoint.diff"}:
            return self._invoke_g2_control(operation, arguments, execution, operation_id)
        if operation in RUNTIME_SCOPED_OPERATIONS:
            # C05: a runtime capability/licence question is a property of the
            # runtime, not of a model.  It is routed without a model_ref and
            # without an expected_revision (an unbound call must succeed), and a
            # model_ref that *is* supplied is used for the inventory read only.
            return self._invoke_runtime_scoped(operation, arguments, execution, operation_id)
        if is_implemented(operation) and operation not in {"registry.list", "registry.describe", "registry.search", "registry.manifest", "registry.call"}:
            # G2 actions may be reached directly or through the public
            # operation_call/registry_call fallback.  Bind the persistent
            # Worker's request-event context around both routes so the
            # daemon's original job records submitted and observed request
            # IDs for later reconciliation.  Keep this scope local to the
            # current operation and let operation_context restore any outer
            # context on every exit, including exceptions.
            with self.context(operation_id, event_callback):
                return self._invoke_g2_model(operation, arguments, execution, operation_id, event_callback)
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

    @staticmethod
    def _g2_alias(operation: str) -> str:
        return operation.replace(".", "_")

    @staticmethod
    def _g2_nested_permission(effect: str) -> str:
        permissions = {
            "READ": "inspect", "WRITE": "project_write", "STATE_WRITE": "project_write",
            "FILE_WRITE": "project_write", "COMPUTE": "compute", "EVALUATE": "project_write",
            "TRUSTED_CODE": "trusted_code",
        }
        try:
            return permissions[str(effect).upper()]
        except KeyError as exc:
            raise ExecutionContractError("PERMISSION_DENIED", f"unclassified nested G2 effect: {effect!r}") from exc

    @staticmethod
    def _validate_java_mode(operation: str, arguments: Mapping[str, Any]) -> None:
        """Reject an untrusted Java mode before any write/copy ticket."""
        if operation == "code.execute_java" and arguments.get("mode") not in {"trusted", "execute"}:
            raise ExecutionContractError("PERMISSION_DENIED", "code execution requires mode=trusted or mode=execute")

    def _preflight_nested_actions(self, actions: Any) -> list[tuple[str, dict[str, Any], str]]:
        """Validate every nested action before a transaction checkpoint/copy.

        The outer service ticket gates the transaction as a whole, but it must
        not be possible for a later nested action to discover a missing
        ``trusted_code`` permission after an earlier checkpoint or copy was
        already created.  Only operations implemented by the transaction
        runner are accepted here; legacy fallback names never become a second
        engine route.
        """
        if not isinstance(actions, list):
            raise ExecutionContractError("INVALID_REQUEST", "actions must be an array")
        supported = {
            "node.inspect", "node.children", "node.find", "node.property_schema", "node.property_get",
            "node.property_set", "node.property_index_set", "node.property_entry_set", "code.execute_java",
        }
        rows: list[tuple[str, dict[str, Any], str]] = []
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                raise ExecutionContractError("INVALID_REQUEST", f"action {index} must be an object")
            operation = action.get("operation_id", action.get("operation"))
            arguments = action.get("arguments", {})
            if not isinstance(operation, str) or not operation:
                raise ExecutionContractError("INVALID_REQUEST", f"action {index} requires operation_id")
            if not isinstance(arguments, Mapping):
                raise ExecutionContractError("INVALID_REQUEST", f"action {index} arguments must be an object")
            if operation not in supported:
                raise ExecutionContractError("UNSUPPORTED_OPERATION", f"nested transaction operation is not supported: {operation}")
            self._validate_java_mode(operation, arguments)
            entry = validate_call(operation, arguments, allow_unbound_identity=True)
            permission = self._g2_nested_permission(entry.effect)
            if permission not in self.service.ledger.permissions:
                raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {permission}")
            rows.append((operation, dict(arguments), permission))
        return rows

    def _freeze_g2_model(self, ref, reason: str) -> None:
        """Freeze a bound model after an unproven copy/cleanup outcome."""
        try:
            state = self.service.ledger._state_for(ref)
            state.dirty = True
            state.fingerprint = None
            state.active_operation_id = None
            self.persist()
        except Exception:
            # The caller is already returning UNKNOWN.  Preserve that result
            # rather than replacing it with a secondary bookkeeping error.
            pass

    @staticmethod
    def _trial_scope_plan(actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Build the bounded main-model before/after readback scope."""
        requests: list[dict[str, Any]] = []
        limitations: list[str] = []
        for action in actions:
            operation = action.get("operation_id", action.get("operation"))
            args = action.get("arguments", {})
            if operation == "node.property_set":
                names = [item.get("name") for item in args.get("properties", []) if isinstance(item, Mapping)]
                requests.append({"operation": operation, "path": args.get("path", {}), "names": names})
            elif operation == "node.property_index_set":
                requests.append({"operation": operation, "path": args.get("path", {}), "names": [args.get("name")]})
            elif operation == "node.property_entry_set":
                limitations.append("property_entry_set keyed readback is not covered")
            elif operation == "code.execute_java":
                limitations.append("trusted Java may touch arbitrary model state")
        return requests, limitations

    def _capture_trial_scope(self, model_tag: str, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []
        for request in requests:
            names = [name for name in request.get("names", []) if isinstance(name, str) and name]
            if not names:
                continue
            captured.append({"operation": request["operation"], "path": request["path"],
                             "properties": property_get(self.worker, model_tag, request["path"], names)["properties"]})
        return captured

    @staticmethod
    def _g2_body(arguments: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in arguments.items() if key not in {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}}

    def _invoke_g2_control(self, operation, arguments, execution, operation_id):
        if operation in {"docs_index", "docs.index"}:
            body = self._g2_body(arguments)
            result = self.docs_index.index(runtime_id=body.get("runtime_id", ""), sources=body.get("sources", []), version=body.get("version"), product=body.get("product", "COMSOL"))
            return {"success": True, "data": result, "execution": {"operation_id": operation_id}}
        if operation in {"docs.search", "docs.get", "docs.examples", "docs.error_search"}:
            body = self._g2_body(arguments)
            if operation in {"docs.search", "docs.examples", "docs.error_search"}:
                # The declared input schemas decide the dimensions:
                # docs.search carries ``product``, docs.error_search carries
                # ``node_type`` (a node/feature type filter, never a product),
                # and docs.examples carries neither - so a node_type is never
                # aliased onto the product dimension here.
                if operation == "docs.error_search":
                    query, product, node_type = body.get("error", ""), None, body.get("node_type")
                elif operation == "docs.examples":
                    query = (body.get("query", "") + " example tutorial").strip()
                    product = node_type = None
                else:
                    query, product, node_type = body.get("query", ""), body.get("product"), body.get("node_type")
                result = self.docs_index.search(query=query, version=body.get("version", ""),
                                                product=product, node_type=node_type,
                                                limit=body.get("limit", 10))
            else:
                result = self.docs_index.get(document_ref=body.get("document_ref", ""), section=body.get("section"), offset=body.get("offset", 0), length=body.get("length", 6000))
            return {"success": True, "data": result, "execution": {"operation_id": operation_id}}
        if operation in {"code_describe_java", "code.describe_java"}:
            description = describe_source(self.project_root, arguments.get("source_artifact", ""), arguments.get("entrypoint", ""))
            return {"success": True, "data": description, "execution": {"operation_id": operation_id}}
        if operation in {"code_compile_java", "code.compile_java"}:
            self._ensure_compile_worker()
            description = describe_source(self.project_root, arguments.get("source_artifact", ""), arguments.get("entrypoint", ""))
            try:
                reply = self.worker.compile_java(description["source_artifact"], description["entrypoint"], request_id=operation_id)
                return compile_result(description=description, worker_reply=reply)
            except Exception as exc:
                preserved = getattr(exc, "reply", None)
                failure = preserved.get("failure") if isinstance(preserved, dict) else None
                reply = dict(failure) if isinstance(failure, dict) else {}
                reply.update({"ok": False, "code": reply.get("code", "COMPILE_ERROR"), "message": str(exc)})
                return compile_result(description=description, worker_reply=reply)
        if operation in {"code.inspect_run", "code_inspect_run"}:
            job_id = self._g2_body(arguments).get("job_id", "")
            job = self.store.job(job_id) if isinstance(job_id, str) and job_id else None
            if job is None:
                return {"success": False, "data": {}, "error": {"code": "NODE_NOT_FOUND", "message": "code run job not found", "safe_retry": False}}
            return {"success": True, "data": {"job": job, "events": self.store.events(job_id)}, "execution": {"operation_id": operation_id}}
        if operation in {"transaction_preview", "transaction.preview"}:
            data = preview_transaction(arguments.get("actions", []), arguments.get("invariants"), model_ref=execution.get("model_ref"))
            return {"success": True, "data": data, "execution": {"operation_id": operation_id}}
        if operation in {"checkpoint.list", "checkpoint.inspect", "checkpoint.diff"}:
            rows = self.store.list_metadata("checkpoints")
            if operation == "checkpoint.list":
                return {"success": True, "data": {"status": "SUCCEEDED", "checkpoints": rows}, "execution": {"operation_id": operation_id}}
            if operation == "checkpoint.inspect":
                checkpoint_id = arguments.get("checkpoint_id", "")
                row = next((item for item in rows if item.get("checkpoint_id") == checkpoint_id or item.get("sha256") == checkpoint_id), None)
                if row is None:
                    return {"success": False, "data": {}, "error": {"code": "NODE_NOT_FOUND", "message": "checkpoint not found", "safe_retry": False}}
                path = Path(row.get("path", ""))
                verified = path.is_file() and (not row.get("sha256") or hashlib.sha256(path.read_bytes()).hexdigest() == row.get("sha256"))
                return {"success": True, "data": {"status": "SUCCEEDED" if verified else "FAILED", "checkpoint": row, "hash_verified": verified}, "execution": {"operation_id": operation_id}}
            return {"success": False, "data": {"status": "NOT_RUN", "reason": "checkpoint diff requires a versioned COMSOL comparison adapter"},
                    "error": {"code": "UNSUPPORTED_OPERATION", "message": "checkpoint.diff is not available in this G2 adapter", "safe_retry": False}}
        raise ExecutionContractError("UNSUPPORTED_OPERATION", f"unsupported G2 control operation {operation}")

    def _ensure_compile_worker(self) -> None:
        """Start only the private Java Worker when compilation has no server.

        The worker JVM owns javac/classpath resolution but stays disconnected
        from COMSOL.  This keeps source diagnostics available when model
        isolation is unavailable and never implies a bound Model identity.
        """
        if self.worker is not None:
            return
        from ._java_worker import JavaWorkerPaths, PersistentJavaWorker
        root = os.environ.get("COMSOL_ROOT")
        java = os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME")
        if not root or not java:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "COMSOL_ROOT and COMSOL_JAVA_HOME must be configured for Java compilation")
        self.worker = PersistentJavaWorker(JavaWorkerPaths(Path(root), Path(java), project_root=self.project_root),
                                           state_dir=self.home / "worker")
        self.worker.start()

    def _require_g2_isolation(self) -> dict[str, Any]:
        receipt = configured_receipt()
        if receipt is None:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "this G2 model mutation requires a configured owned-server isolation receipt")
        if not self.endpoint_key:
            raise ExecutionContractError("ISOLATION_PROOF_REQUIRED", "current COMSOL endpoint is not bound")
        runtime = self.worker.runtime_metadata() if self.worker is not None else {}
        return verify_owned_server(receipt, endpoint=self.endpoint_key, worker_pid=runtime.get("pid"))

    def _invoke_runtime_scoped(self, operation, arguments, execution, operation_id):
        """Route a runtime-scoped G3 operation without a model or a revision (C05).

        ``runtime.capabilities`` and ``runtime.license_inspect`` answer a question
        about the live runtime, so requiring a bound model (and a revision for it)
        would refuse a request the product can serve: an unbound call must reach
        the probe.  When the caller *does* bind a model, the model feeds the
        inventory read only - the licence answer never depends on it, and no write
        ticket, revision or dirty flag is involved because the catalogue effect of
        both operations is a read.
        """
        from ._g3_ops import DISPATCH, EFFECTS

        if self.service is None or self.worker is None:
            raise ExecutionContractError(
                "ENGINE_UNRESPONSIVE", "a connected persistent Worker is required for a runtime probe"
            )
        function = DISPATCH.get(operation)
        if function is None:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"G3 operation is not executable: {operation}")
        effect = str(EFFECTS.get(operation, "")).upper()
        if effect not in {"READ", ""}:
            raise ExecutionContractError(
                "PERMISSION_DENIED",
                f"{operation} is published as a {effect or 'unknown'} effect and is not runtime-scoped",
            )
        session = execution.get("session_id") or arguments.get("session_id")
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        if execution.get("expected_revision") is not None or arguments.get("expected_revision") is not None:
            # A runtime probe must not participate in the model revision protocol:
            # accepting a revision here would imply the answer depends on it.
            raise ExecutionContractError(
                "INVALID_REQUEST", "a runtime-scoped operation must not carry expected_revision"
            )
        ref = None
        ref_mapping = execution.get("model_ref") or arguments.get("model_ref")
        if isinstance(ref_mapping, Mapping):
            ref = model_ref_from_mapping(dict(ref_mapping))
            self.service.ledger._state_for(ref)  # reject a stale ref before any engine call
        body = self._g2_body(arguments)
        if ref is not None:
            body["model_ref"] = ref.as_dict()
        model_tag = ref.model_tag if ref is not None else ""
        callback = lambda _args: self._dispatch_with_witness(
            operation, lambda: function(self.worker, model_tag, dict(body)), effect="inspect",
        )
        result = self.service.execute_legacy(
            self._g2_alias(operation), callback, body, model_ref=ref,
            request_id=execution.get("request_id"), session_id=session, effect="inspect",
        )
        result.setdefault("data", {})["model_binding"] = {
            "bound": ref is not None,
            "model_tag": model_tag or None,
            "revision_required": False,
            "reason": "a runtime capability answer does not depend on a model revision",
        }
        self.persist()
        return result

    def _invoke_g2_model(self, operation, arguments, execution, operation_id, event_callback):
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "a connected persistent Worker is required")
        session = execution.get("session_id") or arguments.get("session_id")
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        ref_mapping = execution.get("model_ref") or arguments.get("model_ref")
        if not isinstance(ref_mapping, dict):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref is required for this operation")
        ref = model_ref_from_mapping(ref_mapping)
        bound_revision = self.service.ledger._state_for(ref).revision
        body = self._g2_body(arguments)
        alias = self._g2_alias(operation)
        # Reject an untrusted Java mode before entering the write-ticket
        # service.  If this check were left inside the engine callback,
        # ExecutionService would conservatively classify the callback
        # exception as UNKNOWN after dispatch, even though no Java request or
        # model mutation was authorized.
        self._validate_java_mode(operation, body)

        if operation in _g3_operations():
            # G3 (W13-W16) domain operations use the same bound-model,
            # revision and write-ticket path as the G2 model surface; the
            # catalogue effect decides permission and isolation inside.
            return self._invoke_g3_model(operation, ref, body, execution, operation_id, session)
        if operation in {"node.property_set", "node.property_index_set", "node.property_entry_set",
                         "code.execute_java", "checkpoint.create", "checkpoint.restore",
                         "transaction.trial", "transaction.apply", "transaction.recover"}:
            isolation = self._require_g2_isolation()
        else:
            isolation = None
        # Static trial deliberately does not touch the main model or its
        # revision.  It first observes the bound model through the same gate,
        # then creates and removes a private copy in the Worker queue.
        if operation == "transaction.trial":
            result = self._run_trial(ref, body, operation_id, execution)
            if isolation is not None:
                result.setdefault("data", {})["isolation_proof"] = isolation
            return result
        if operation == "transaction.recover":
            record = self.transactions.get(body.get("transaction_id", ""))
            if not record or not record.get("checkpoint_id"):
                raise ExecutionContractError("CHECKPOINT_RESTORE_REQUIRES_REBIND", "transaction has no restorable checkpoint")
            restore_args = {"checkpoint_id": record["checkpoint_id"], "authorization_ref": body.get("authorization_ref")}
            return self._invoke_g2_model("checkpoint.restore", {**arguments, **restore_args}, execution, operation_id, event_callback)
        if operation == "checkpoint.restore":
            result = self._restore_checkpoint(ref, body, operation_id, event_callback, execution)
            result.setdefault("data", {})["isolation_proof"] = isolation
            return result
        if operation == "checkpoint.create":
            result = self._checkpoint_via_service(ref, body, alias, operation_id, execution)
            if isolation is not None:
                result.setdefault("data", {})["isolation_proof"] = isolation
            return result
        if operation == "transaction.apply":
            result = self._transaction_via_service(ref, body, alias, operation_id, execution)
            if isolation is not None:
                result.setdefault("data", {})["isolation_proof"] = isolation
            return result
        if operation == "transaction.verify":
            # R04: the durable record is only accepted for the model the caller
            # is bound to; cross-model verification is refused.
            callback = lambda _args: self._verify_transaction_record(body.get("transaction_id", ""), body.get("checks", []), requested_ref=ref.as_dict())
            return self.service.execute_legacy(alias, callback, body, model_ref=ref,
                expected_revision=execution.get("expected_revision", arguments.get("expected_revision")),
                request_id=execution.get("request_id"), session_id=session, effect="evaluate")
        callback = self._g2_callback(operation, ref, body, operation_id, bound_revision)
        result = self.service.execute_legacy(alias, callback, body, model_ref=ref,
                expected_revision=execution.get("expected_revision", arguments.get("expected_revision")),
                request_id=execution.get("request_id"), session_id=session, effect={
                    "node.property_set": "project_write", "node.property_index_set": "project_write", "node.property_entry_set": "project_write",
                    "node.property_schema": "inspect", "node.property_get": "inspect", "node.inspect": "inspect", "node.children": "inspect", "node.find": "inspect",
                    "code.execute_java": "trusted_code",
                }.get(operation, "compute" if operation.startswith("transaction.") else "inspect"))
        if isolation is not None:
            result.setdefault("data", {})["isolation_proof"] = isolation
        return result

    def _g2_callback(self, operation: str, ref, body: dict[str, Any], operation_id: str, bound_revision: int):
        """Wrap one G2 action so a *proven* pre-write refusal keeps its own code.

        The property/index/entry setters raise ``PreWriteRefusal`` only before
        their first engine mutation; they convert every post-dispatch failure
        into their own result data.  Reporting that refusal with its published
        code is what keeps a correct rejection from being flattened into
        ``EXECUTION_STATE_UNKNOWN`` — an unknown job poisons the control gate for
        every later operation.

        The proof is never the exception class name alone: the refusal must
        declare the explicit ``validation`` stage *and* the dispatch witness must
        have seen no mutation-class engine method during this callback.  Any
        other exception keeps the conservative fail-closed classification.
        """

        def callback(_args: dict[str, Any]) -> dict[str, Any]:
            return self._dispatch_with_witness(
                operation,
                lambda: self._run_g2_action(operation, ref.model_tag, body, operation_id,
                                            model_revision=bound_revision),
                effect=LEGACY_TOOL_EFFECTS.get(operation, "project_write"),
            )

        return callback

    @staticmethod
    def _dispatch_with_witness(operation: str, call, *, effect: str) -> dict[str, Any]:
        """Run one domain/G2 action inside the dispatch witness and classify it.

        A domain function either returns its ``data`` mapping (success or a
        post-dispatch outcome the mapping itself declares) or raises.  The raise
        is only treated as a pre-write refusal when both proofs hold:

        * the raise declared the explicit validation stage
          (``ExecutionContractError.stage == "validation"``), and
        * the witness saw no mutation-class engine call during the callback.

        Everything else is re-raised so the shared write-ticket path records the
        fail-closed ``EXECUTION_STATE_UNKNOWN`` (the callback may have changed the
        engine before it died).
        """
        from ._domain_outcome import classify_envelope, domain_envelope, witness_scope
        with witness_scope() as witness:
            try:
                data = call()
            except (PreWriteRefusal, ExecutionContractError) as exc:
                if exc.stage == "validation" and not witness.mutation_issued:
                    # Both proofs hold: an explicit validation stage and a
                    # witness that saw no mutation-class engine call.
                    return _refusal_envelope(operation, exc, witness, effect=effect)
                # A raise that cannot prove "nothing was dispatched" must keep
                # the fail-closed state, with the cause preserved for evidence.
                unproven = exc.stage == "validation"
                details = {
                    **exc.details,
                    "dispatch_stage": "post_dispatch",
                    "witness": witness.as_dict(),
                    "unproven_pre_dispatch": unproven,
                    "cause_code": exc.code,
                    "cause_message": str(exc),
                }
                if exc.code == "EXECUTION_STATE_UNKNOWN":
                    exc.details = details
                    raise
                reason = ("the callback declared the validation stage but had already issued an "
                          "engine mutation" if unproven else "the callback did not declare a "
                          "pre-dispatch stage")
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"{operation} failed with {exc.code} ({exc}); {reason}, so the engine state "
                    "cannot be shown to be unchanged",
                    stage="post_dispatch", details=details,
                ) from exc
            if not isinstance(data, Mapping):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"{operation} returned {type(data).__name__} instead of a data mapping",
                    stage="post_dispatch",
                )
            if isinstance(data.get("success"), bool):
                # The G2 property/transaction actions own their own envelope
                # (a boolean ``success`` is the wire contract of that layer), so
                # it is classified and normalised, never re-wrapped.
                envelope = dict(data)
                outcome = classify_envelope(envelope)
                envelope.update(outcome.envelope_fields())
                envelope.setdefault("data", {})
                envelope["operation"] = operation
                envelope["effect"] = effect
                envelope["domain_outcome"] = outcome.as_dict()
                return envelope
            envelope = domain_envelope(operation, data, witness=witness)
            envelope["effect"] = effect
            return envelope

        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "the dispatch witness scope did not close")

    def _invoke_g3_model(self, operation, ref, body, execution, operation_id, session):
        """Dispatch a G3 (W13-W16) domain operation through the shared write-ticket service.

        The catalogue effect recorded in ``_g3_ops`` — never the request body —
        decides the permission and whether the isolated (owned-server) path is
        required, mirroring the G2 mutation gate.  A G3 operation raises
        ``ExecutionContractError`` only *before* its first engine mutation;
        post-dispatch outcomes are reported as data (``status``/
        ``partial_change``/``execution_state_unknown``).  A raise is therefore
        mapped back to its own error code here instead of being flattened into
        ``EXECUTION_STATE_UNKNOWN`` by the conservative legacy-callback path.
        """
        from ._g3_ops import DISPATCH, EFFECTS, REQUIRES_ISOLATION
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "a connected persistent Worker is required")
        function = DISPATCH.get(operation)
        if function is None:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"G3 operation is not executable: {operation}")
        effect = _G3_EFFECT_MAP.get(str(EFFECTS.get(operation, "")).upper())
        if effect is None:
            raise ExecutionContractError("PERMISSION_DENIED", f"unclassified G3 effect for operation {operation}")
        isolation = (
            self._require_g2_isolation()
            if (operation in REQUIRES_ISOLATION and operation not in {"plot.render", "plot.geometry_render", "plot_render", "plot_geometry_render"})
            else None
        )
        alias = self._g2_alias(operation)

        def callback(_args: dict[str, Any]) -> dict[str, Any]:
            return self._dispatch_with_witness(
                operation, lambda: function(self.worker, ref.model_tag, dict(body)), effect=effect,
            )

        result = self.service.execute_legacy(alias, callback, body, model_ref=ref,
                expected_revision=execution.get("expected_revision", body.get("expected_revision")),
                request_id=execution.get("request_id"), session_id=session, effect=effect)
        if isolation is not None:
            result.setdefault("data", {})["isolation_proof"] = isolation
        return result

    def _run_g2_action(self, operation: str, model_tag: str, body: dict[str, Any], operation_id: str, *, model_revision: int | None = None) -> dict[str, Any]:
        if operation == "node.inspect": data = inspect_node(self.worker, model_tag, body.get("path", {}), include_values=bool(body.get("include_values", False)))
        elif operation == "node.children": data = children_node(self.worker, model_tag, body.get("path", {}), cursor=body.get("cursor"), limit=body.get("limit", 100), model_revision=model_revision)
        elif operation == "node.find": data = find_nodes(self.worker, model_tag, body.get("query", {}), root=body.get("root"), limit=body.get("limit", 100), cursor=body.get("cursor"), model_revision=model_revision, budget=body.get("budget"))
        elif operation == "node.property_schema": data = property_schema(self.worker, model_tag, body.get("path", {}), body.get("name"))
        elif operation == "node.property_get": data = property_get(self.worker, model_tag, body.get("path", {}), body.get("names", []))
        elif operation == "node.property_set": data = property_set(self.worker, model_tag, body.get("path", {}), body.get("properties", []))
        elif operation == "node.property_index_set": data = property_index_set(self.worker, model_tag, body.get("path", {}), body.get("name", ""), body.get("indices", []), body.get("value", {}))
        elif operation == "node.property_entry_set": data = property_entry_set(self.worker, model_tag, body.get("path", {}), body.get("name", ""), body.get("key", ""), body.get("value", {}))
        elif operation == "code.execute_java":
            description = describe_source(self.project_root, body.get("source_artifact", ""), body.get("entrypoint", ""))
            if body.get("mode") not in {"trusted", "execute"}:
                raise ExecutionContractError("PERMISSION_DENIED", "code execution requires mode=trusted or mode=execute")
            before = self.worker.backend_snapshot(model_tag)
            try:
                reply = self.worker.execute_java(model_tag, description["source_artifact"], description["entrypoint"], body.get("arguments", {}), request_id=operation_id)
            except Exception as exc:
                # Worker compilation is completed before ModelUtil.model(tag)
                # and therefore proves no model write on a syntax failure.
                preserved = getattr(exc, "reply", None)
                failure = preserved.get("failure") if isinstance(preserved, Mapping) else None
                code = failure.get("code") if isinstance(failure, Mapping) else None
                if code != "COMPILE_ERROR":
                    raise
                reply = {"ok": False, "code": code, "message": str(exc),
                         "diagnostics": failure.get("diagnostics", []) if isinstance(failure, Mapping) else [],
                         "execution_state_unknown": False}
            after = self.worker.backend_snapshot(model_tag)
            data = execution_result(description=description, worker_reply=reply, before=before, after=after)
            if not reply.get("ok") and reply.get("code") == "COMPILE_ERROR":
                data.setdefault("error", {})["code"] = "COMPILE_ERROR"
                data["error"]["safe_retry"] = True
        elif operation == "transaction.verify":
            # A nested/legacy verify has no bound model identity, so it cannot
            # confirm that the durable record belongs to this model.  Refuse
            # instead of reporting another model's verification as this one's.
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH",
                                         "transaction.verify requires the bound managed model_ref")
        else:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"G2 model operation is not implemented: {operation}")
        if isinstance(data, dict) and "success" in data:
            return data
        return {"success": True, "data": data}

    def _verify_transaction_record(self, transaction_id: str, checks: Any, *, requested_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate the small durable-record check vocabulary explicitly.

        R04 scope annotation: every check below re-reads the *durable
        transaction record*, so its scope is ``durable_record``.  Live model
        state is not re-read here - that evidence belongs to the apply-time
        ``invariant_results``, which are bound to the model_ref/revision of the
        observation they were evaluated against and are reported alongside.
        A check that cannot be evaluated from the durable record is NOT_RUN; it
        never becomes a passing boolean by echoing the request or the
        transaction's terminal status.

        When the caller supplies the requested model binding (the public
        transaction.verify path always does), a record bound to a different
        model - or to no model at all - is refused instead of being reported as
        this model's verification.
        """
        record = self.transactions.get(transaction_id)
        if not record:
            return {"success": False, "data": {"transaction_id": transaction_id, "status": "NOT_FOUND", "verified": False},
                    "error": {"code": "NODE_NOT_FOUND", "message": "transaction not found", "safe_retry": False}}
        if not isinstance(checks, list):
            raise ExecutionContractError("INVALID_REQUEST", "checks must be an array")
        record_ref = record.get("model_ref")
        if not isinstance(record_ref, Mapping):
            metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
            record_ref = metadata.get("source_model_ref") or (metadata.get("source_binding") or {}).get("model_ref")
        if requested_ref is not None:
            if not isinstance(record_ref, Mapping) or dict(record_ref) != dict(requested_ref):
                raise ExecutionContractError(
                    "MODEL_IDENTITY_MISMATCH",
                    "durable transaction record is not bound to the requested model",
                )
        rows: list[dict[str, Any]] = []
        unsupported = False
        failed = False

        def lookup(value: Any, path: Any) -> tuple[bool, Any]:
            parts = path.split(".") if isinstance(path, str) else path if isinstance(path, list) else None
            if not parts:
                return False, None
            current = value
            for part in parts:
                if isinstance(current, Mapping) and part in current:
                    current = current[part]
                elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
                    current = current[part]
                else:
                    return False, None
            return True, current

        for index, check in enumerate(checks):
            if not isinstance(check, Mapping):
                unsupported = True; rows.append({"index": index, "scope": "durable_record",
                                                 "status": "NOT_RUN", "reason": "check is not an object"}); continue
            field = check.get("field", check.get("path"))
            expected_present = "equals" in check or "expected" in check
            expected = check.get("equals", check.get("expected"))
            if not isinstance(field, (str, list)) or not expected_present:
                unsupported = True
                rows.append({"index": index, "scope": "durable_record", "status": "NOT_RUN",
                             "reason": "supported checks require field/path and equals/expected"}); continue
            present, actual = lookup(record, field)
            if not present:
                failed = True
                rows.append({"index": index, "scope": "durable_record", "status": "FAILED",
                             "field": field, "reason": "field unavailable"}); continue
            passed = actual == expected
            failed |= not passed
            rows.append({"index": index, "scope": "durable_record", "status": "PASSED" if passed else "FAILED",
                         "field": field, "actual": actual, "expected": expected})
        status = "NOT_RUN" if unsupported or not checks else ("FAILED" if failed else "VERIFIED")
        raw_invariants = record.get("invariant_results")
        invariant_results = dict(raw_invariants) if isinstance(raw_invariants, Mapping) else {}
        data = {"transaction_id": transaction_id, "transaction": record, "checks": rows,
                "status": status, "verified": status == "VERIFIED",
                "scope": "durable_record",
                "model_ref": dict(record_ref) if isinstance(record_ref, Mapping) else None,
                "revision": record.get("revision"),
                "execution_status": record.get("execution_status", record.get("status")),
                "verification_status": record.get("verification_status", "NOT_RUN"),
                "invariant_results": invariant_results,
                "live_model_state": {
                    "scope": "model_state",
                    "source": "transaction.apply invariant_results",
                    "status": invariant_results.get("status", "NOT_RUN"),
                    "model_ref": invariant_results.get("model_ref"),
                    "revision": invariant_results.get("revision"),
                    "checks": invariant_results.get("checks", []),
                    "note": ("Apply-time evidence bound to the recorded model_ref/revision; this call re-reads the durable record only and is not a live re-read of the current model."),
                }}
        return {"success": True, "data": data, "error": None}

    def _checkpoint_via_service(self, ref, body, alias, operation_id, execution):
        label = str(body.get("label") or "checkpoint")
        destination = body.get("destination")
        if destination:
            destination = canonical_project_path(self.project_root, destination)
        else:
            destination = self.project_root / "g2_artifacts" / "checkpoints" / (label.replace("/", "_").replace("\\", "_") + ".mph")
        callback = lambda _args: {"success": True, "data": create_checkpoint(self.worker, ref.model_tag, Path(destination), label, include_solution=bool(body.get("include_solution", False)))}
        result = self.service.execute_legacy(alias, callback, body, model_ref=ref, expected_revision=execution.get("expected_revision", body.get("expected_revision")), request_id=execution.get("request_id"), session_id=execution.get("session_id"), effect="project_write")
        if result.get("success"):
            info = result.get("data", {}).get("checkpoint_id")
            metadata = result.get("data", {})
            state = self.service.ledger._state_for(ref)
            metadata = self._bind_checkpoint_metadata(ref, metadata, state=state)
            result.setdefault("data", {}).update(metadata)
            if metadata.get("sha256"):
                self.store.persist_checkpoint(metadata.get("sha256"), metadata)
            self.transactions.put({"transaction_id": info or "checkpoint-" + metadata.get("sha256", "")[:12], "checkpoint_id": info, "status": "CHECKPOINT", "metadata": metadata})
        return result

    @staticmethod
    def _bind_checkpoint_metadata(ref, metadata: Mapping[str, Any], *, state) -> dict[str, Any]:
        """Bind a published checkpoint to the exact managed model observation.

        The binding is created by the backend after the ticketed save has
        completed.  A caller can provide the checkpoint id and artifact hash,
        but cannot choose the model epoch, revision, or fingerprint used by a
        later isolated trial.
        """
        value = dict(metadata)
        if not isinstance(value.get("checkpoint_id"), str) or not value["checkpoint_id"]:
            raise ExecutionContractError("ARTIFACT_MISSING", "checkpoint save returned no checkpoint_id")
        if not isinstance(value.get("sha256"), str) or len(value["sha256"]) != 64:
            raise ExecutionContractError("ARTIFACT_MISSING", "checkpoint save returned no complete sha256")
        source_revision = state.revision
        source_fingerprint = state.fingerprint
        source_external_event_counter = state.external_event_counter
        if not isinstance(source_fingerprint, str) or not source_fingerprint:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "checkpoint source fingerprint is unavailable")
        binding = {
            "model_ref": ref.as_dict(),
            "revision": source_revision,
            "fingerprint": source_fingerprint,
            "external_event_counter": source_external_event_counter,
        }
        value["source_binding"] = binding
        value["source_model_ref"] = ref.as_dict()
        value["source_revision"] = source_revision
        value["source_fingerprint"] = source_fingerprint
        value["source_external_event_counter"] = source_external_event_counter
        value["source_sha256"] = value["sha256"]
        return value

    def _trial_checkpoint(self, ref, checkpoint_id: Any, state, expected_revision: Any) -> tuple[dict[str, Any], Path]:
        """Validate an immutable checkpoint before any trial copy/load side effect."""
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise ExecutionContractError("CHECKPOINT_REQUIRED", "transaction.trial requires an explicit checkpoint_id")
        rows = self.store.list_metadata("checkpoints")
        metadata = next((row for row in rows if row.get("checkpoint_id") == checkpoint_id), None)
        if not isinstance(metadata, Mapping):
            raise ExecutionContractError("NODE_NOT_FOUND", "trial checkpoint was not found")
        binding = metadata.get("source_binding")
        if not isinstance(binding, Mapping):
            raise ExecutionContractError("CHECKPOINT_REQUIRED", "checkpoint is not bound to a managed model observation")
        source_ref = binding.get("model_ref")
        if not isinstance(source_ref, Mapping) or dict(source_ref) != ref.as_dict():
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "trial checkpoint belongs to another model generation")
        if binding.get("revision") != state.revision or metadata.get("source_revision") != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "trial checkpoint revision is stale")
        if expected_revision != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "trial checkpoint requires the current expected_revision")
        current_fingerprint = state.fingerprint
        if binding.get("fingerprint") != current_fingerprint or metadata.get("source_fingerprint") != current_fingerprint:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "trial checkpoint fingerprint is stale")
        if binding.get("external_event_counter") != state.external_event_counter or metadata.get("source_external_event_counter") != state.external_event_counter:
            raise ExecutionContractError("REVISION_CONFLICT", "trial checkpoint external event counter is stale")
        recorded_sha = metadata.get("sha256")
        if not isinstance(recorded_sha, str) or len(recorded_sha) != 64 or metadata.get("source_sha256", recorded_sha) != recorded_sha:
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint has no authoritative sha256")
        raw_path = metadata.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint has no artifact path")
        path = canonical_project_path(self.project_root, raw_path)
        if path.suffix.lower() != ".mph" or not path.is_file():
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint artifact is unavailable")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != recorded_sha:
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint hash does not match durable metadata")
        return dict(metadata), path

    @staticmethod
    def _copy_trial_checkpoint(source: Path, destination: Path, expected_sha256: str) -> dict[str, Any]:
        """Copy a verified checkpoint without overwriting an existing artifact."""
        started_ns = time.monotonic_ns()
        copied = 0
        source_digest = hashlib.sha256()
        created = False

        def cleanup_owned() -> None:
            if not created:
                return
            try:
                destination.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"owned trial checkpoint cleanup failed: {destination}",
                ) from cleanup_exc
            if destination.exists():
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"owned trial checkpoint remained after cleanup: {destination}",
                )

        try:
            with source.open("rb") as reader, destination.open("xb") as writer:
                created = True
                while True:
                    block = reader.read(1024 * 1024)
                    if not block:
                        break
                    source_digest.update(block)
                    writer.write(block)
                    copied += len(block)
                writer.flush()
                os.fsync(writer.fileno())
        except Exception as exc:
            cleanup_owned()
            if isinstance(exc, ExecutionContractError):
                raise
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint copy failed") from exc
        source_sha = source_digest.hexdigest()
        copied_digest = hashlib.sha256()
        try:
            with destination.open("rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    copied_digest.update(block)
        except OSError as exc:
            cleanup_owned()
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint copy readback failed") from exc
        copied_sha = copied_digest.hexdigest()
        if source_sha != expected_sha256 or copied_sha != expected_sha256:
            cleanup_owned()
            raise ExecutionContractError("ARTIFACT_MISSING", "trial checkpoint copy hash verification failed")
        return {
            "path": str(destination),
            "bytes": copied,
            "sha256": copied_sha,
            "source_sha256": source_sha,
            "copy_elapsed_ms": round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
            "verified": True,
        }

    def _transaction_via_service(self, ref, body, alias, operation_id, execution):
        actions = body.get("actions", [])
        # Validate all nested schemas/effects before the ticket callback can
        # create its before-checkpoint.  The callback repeats the permission
        # check as defense in depth for any future runner change.
        self._preflight_nested_actions(actions)
        # R04: reject an unsupported/malformed required invariant here, before
        # the checkpoint file and before any action is dispatched.
        invariants = body.get("invariants")
        validate_invariants(invariants)
        checkpoint_id_holder: dict[str, str | None] = {"value": None}
        metadata_holder: dict[str, dict[str, Any]] = {}
        def runner(operation, args, _index):
            entry = validate_call(operation, args, allow_unbound_identity=True)
            permission = self._g2_nested_permission(entry.effect)
            if permission not in self.service.ledger.permissions:
                return {"success": False, "error": {"code": "PERMISSION_DENIED", "message": f"permission required: {permission}", "safe_retry": False}}
            # Each nested Worker request needs its own deterministic request
            # identity.  The outer operation_id remains the durable job and
            # operation-context key; reusing it for two Java actions would
            # make PersistentJavaWorker treat the second body as an
            # idempotency conflict (or replay the first body).
            step_operation_id = f"{operation_id}:step:{_index}"
            return self._run_g2_action(operation, ref.model_tag, args, step_operation_id)
        def callback(_args):
            # The checkpoint is part of the ticketed callback.  Permission,
            # current revision, and external-change checks therefore happen
            # before any file or model side effect.
            if body.get("checkpoint_policy", "on_failure") in {"always", "on_failure", "before"}:
                checkpoint_root = self.project_root / "g2_artifacts" / "checkpoints"
                metadata = create_checkpoint(self.worker, ref.model_tag, checkpoint_root / ("txn-" + operation_id + ".mph"), "transaction-before")
                # This checkpoint protects recovery of a partial transaction;
                # unlike checkpoint.create, it is intentionally not a trial
                # source because its exact pre-action revision is not a
                # committed post-save ledger observation.
                metadata["recovery_only"] = True
                checkpoint_id_holder["value"] = metadata["checkpoint_id"]
                metadata_holder.update(metadata)
                self.store.persist_checkpoint(metadata["sha256"], metadata)
            # The record is bound to the exact managed model observation: the
            # pre-apply revision is recorded here and the post-apply revision is
            # bound below, once the ticketed callback has finished.
            return execute_transaction(self.worker, ref.model_tag, actions, runner=runner,
                                       invariants=invariants, checkpoint_id=checkpoint_id_holder["value"],
                                       model_ref=ref.as_dict(),
                                       pre_revision=self.service.ledger._state_for(ref).revision)
        result = self.service.execute_legacy(alias, callback, body, model_ref=ref, expected_revision=execution.get("expected_revision", body.get("expected_revision")), request_id=execution.get("request_id"), session_id=execution.get("session_id"), effect="project_write")
        txn = result.get("data", {})
        if metadata_holder:
            txn["checkpoint_id"] = checkpoint_id_holder["value"]
            txn["checkpoint_metadata"] = dict(metadata_holder)
        if txn.get("transaction_id"):
            # Bind the durable record to the model identity and to the revision
            # the transaction left behind, so a later verify can refuse a record
            # that belongs to another model generation.
            state = self.service.ledger._state_for(ref)
            txn["model_ref"] = ref.as_dict()
            txn["revision"] = state.revision
            invariant_results = txn.get("invariant_results")
            if isinstance(invariant_results, dict):
                invariant_results["model_ref"] = ref.as_dict()
                invariant_results["revision"] = state.revision
            self.transactions.put(txn)
        return result

    def _run_trial(self, ref, body, operation_id, execution):
        # Trial is a compute action on an isolated copy.  It still observes
        # the selected main model through the ordinary ledger gate so an
        # externally changed/dirty main model cannot be copied accidentally.
        if "compute" not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", "permission required: compute")
        self.service.inspect(ref)
        state = self.service.ledger._state_for(ref)
        expected_revision = execution.get("expected_revision", body.get("expected_revision"))
        if expected_revision is None or expected_revision != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "trial requires the current expected_revision")
        if state.dirty or state.external_event_counter != state.observed_external_event_counter:
            raise ExecutionContractError("REVISION_CONFLICT", "external model change requires reconciliation before trial")
        actions = body.get("actions", [])
        self._preflight_nested_actions(actions)
        checkpoint_metadata, checkpoint_path = self._trial_checkpoint(ref, body.get("checkpoint_id"), state, expected_revision)
        scope_requests, scope_limitations = self._trial_scope_plan(actions)
        before_main = self.worker.backend_snapshot(ref.model_tag)
        trial_tag = "mcp_trial_" + operation_id.replace("-", "")[:16]
        trial_path = canonical_project_path(self.project_root, self.project_root / "g2_artifacts" / "trials" / (trial_tag + ".mph"))
        if trial_tag in {str(tag) for tag in self.worker.client().tags()}:
            raise ExecutionContractError("ENGINE_BUSY", "trial model tag is already present; refusing to reuse it")
        scope_before: list[dict[str, Any]] = []
        if scope_requests:
            scope_before = self._capture_trial_scope(ref.model_tag, scope_requests)
        trial_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        copy_info = self._copy_trial_checkpoint(source=checkpoint_path, destination=trial_path, expected_sha256=checkpoint_metadata["sha256"])
        trial = None
        result = None
        cleanup_errors: list[str] = []
        cleanup = {"model_removed": False, "artifact_deleted": False, "errors": cleanup_errors}
        try:
            trial = self.worker.client().load(trial_path, tag=trial_tag)

            def runner(operation, args, _index):
                # The outer trial owns the model identity and permission gate;
                # nested actions remain shape-validated without inventing a
                # second identity or idempotency scope.
                entry = validate_call(operation, args, allow_unbound_identity=True)
                permission = self._g2_nested_permission(entry.effect)
                if permission not in self.service.ledger.permissions:
                    return {"success": False, "error": {"code": "PERMISSION_DENIED", "message": f"permission required: {permission or entry.effect}", "safe_retry": False}}
                step_operation_id = f"{operation_id}:step:{_index}"
                return self._run_g2_action(operation, trial_tag, args, step_operation_id)
            result = execute_transaction(self.worker, trial_tag, actions, runner=runner, invariants=body.get("invariants"))
        except Exception as exc:
            result = {"success": False, "data": {"status": "UNKNOWN"},
                      "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": str(exc), "safe_retry": False},
                      "execution_state_unknown": True}
        finally:
            try:
                if trial is not None or trial_tag in {str(tag) for tag in self.worker.client().tags()}:
                    self.worker.client().remove(trial_tag)
                cleanup["model_removed"] = trial_tag not in {str(tag) for tag in self.worker.client().tags()}
            except Exception as exc:
                cleanup_errors.append(f"remove_trial_model {trial_tag}: {type(exc).__name__}: {exc}")
            try:
                trial_path.unlink(missing_ok=True)
                cleanup["artifact_deleted"] = not trial_path.exists()
                if not cleanup["artifact_deleted"]:
                    cleanup_errors.append("remove_trial_artifact: file remained after unlink")
            except OSError as exc:
                cleanup_errors.append(f"remove_trial_artifact {trial_path}: {type(exc).__name__}: {exc}")
        try:
            after_main = self.worker.backend_snapshot(ref.model_tag)
        except Exception as exc:
            self._freeze_g2_model(ref, "main model post-trial observation failed")
            data = (result or {}).setdefault("data", {})
            data.update({"isolated_trial": True, "trial_model_tag": trial_tag,
                         "main_model_untouched": None,
                         "main_model_fingerprint_scope": "unavailable",
                         "main_before": before_main, "main_after": None,
                         "source_checkpoint_id": checkpoint_metadata.get("checkpoint_id"),
                         "source_checkpoint": checkpoint_metadata,
                         "trial_copy": copy_info, "cleanup": cleanup})
            (result or {}).update({"success": False, "execution_state_unknown": True,
                                   "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": f"main model post-trial observation failed: {type(exc).__name__}", "safe_retry": False}})
            return result or {"success": False, "data": data,
                              "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "main model post-trial observation failed", "safe_retry": False},
                              "execution_state_unknown": True}
        observed_unchanged = (before_main.get("fingerprint") == after_main.get("fingerprint") and
                              before_main.get("external_event_counter") == after_main.get("external_event_counter"))
        scope_after: list[dict[str, Any]] = []
        scope_error: str | None = None
        if scope_requests:
            try:
                scope_after = self._capture_trial_scope(ref.model_tag, scope_requests)
            except Exception as exc:
                scope_error = f"scoped main-model readback failed: {type(exc).__name__}"
        scope_equal = (scope_before == scope_after) if scope_requests else not scope_limitations
        scope_verified = scope_error is None and not scope_limitations and scope_equal
        if result is None:
            result = {"success": False, "data": {"status": "UNKNOWN"},
                      "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "trial execution did not return a result", "safe_retry": False},
                      "execution_state_unknown": True}
        data = result.setdefault("data", {})
        # The Worker snapshot covers only the declared shallow scope; equality
        # cannot prove that an arbitrary COMSOL property was untouched.  Keep
        # the claim explicitly unknown and freeze the bound model until a
        # caller performs a reconciliation.
        data.update({"isolated_trial": True, "trial_model_tag": trial_tag,
                     "main_model_untouched": None,
                     "main_model_unchanged_within_scope": scope_equal if scope_error is None else None,
                     "main_model_change_observation": "NO_CHANGE_WITHIN_SCOPED_FINGERPRINT" if observed_unchanged else "CHANGED_WITHIN_SCOPED_FINGERPRINT",
                     "main_model_fingerprint_scope": before_main.get("fingerprint_scope", "parameters+shallow_tree_identity; full property coverage unverified"),
                     "main_before": before_main,
                     "main_after": after_main,
                     "main_external_event_counter_before": before_main.get("external_event_counter"),
                     "main_external_event_counter_after": after_main.get("external_event_counter"),
                     "source_checkpoint_id": checkpoint_metadata.get("checkpoint_id"),
                     "source_checkpoint": checkpoint_metadata,
                     "trial_copy": copy_info,
                     "cleanup": cleanup,
                     "main_model_scope": {"status": "VERIFIED" if scope_verified else "UNKNOWN",
                                          "operations": [row.get("operation") for row in scope_requests],
                                          "requests": scope_requests,
                                          "before": scope_before,
                                          "after": scope_after,
                                          "limitations": scope_limitations + ([scope_error] if scope_error else [])}})
        unknown_reason = None
        if result.get("execution_state_unknown") or result.get("cleanup_failed"):
            unknown_reason = "nested trial execution returned an unknown state"
        elif not observed_unchanged:
            unknown_reason = "main model fingerprint changed during isolated trial"
        elif not scope_verified:
            unknown_reason = scope_error or "; ".join(scope_limitations) or "main model writable scope was not observed"
        if unknown_reason:
            self._freeze_g2_model(ref, unknown_reason)
            result.update({"success": False, "execution_state_unknown": True,
                           "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": unknown_reason, "safe_retry": False}})
        if cleanup_errors:
            data["cleanup_errors"] = cleanup_errors
            self._freeze_g2_model(ref, "isolated trial cleanup failed")
            result.update({"success": False, "cleanup_failed": True, "execution_state_unknown": True,
                           "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "isolated trial cleanup failed", "safe_retry": False}})
        return result

    def _restore_checkpoint(self, ref, body, operation_id, event_callback, execution):
        if "project_write" not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", "permission required: project_write")
        self.service.inspect(ref)
        state = self.service.ledger._state_for(ref)
        expected_revision = execution.get("expected_revision", body.get("expected_revision"))
        if expected_revision is None or expected_revision != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "checkpoint restore requires the current expected_revision")
        dirty_override = state.dirty or state.external_event_counter != state.observed_external_event_counter
        if dirty_override and (not isinstance(body.get("authorization_ref"), str) or not body.get("authorization_ref").strip()):
            raise ExecutionContractError("REVISION_CONFLICT", "dirty restore requires an explicit authorization_ref")
        checkpoint_id = body.get("checkpoint_id")
        rows = self.store.list_metadata("checkpoints")
        metadata = next((row for row in rows if row.get("checkpoint_id") == checkpoint_id or row.get("sha256") == checkpoint_id), None)
        if not metadata or not metadata.get("path"):
            raise ExecutionContractError("NODE_NOT_FOUND", "checkpoint not found")
        path = canonical_project_path(self.project_root, metadata["path"])
        if not path.is_file(): raise ExecutionContractError("ARTIFACT_MISSING", "checkpoint file is unavailable")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if metadata.get("sha256") and digest != metadata["sha256"]:
            raise ExecutionContractError("ARTIFACT_MISSING", "checkpoint hash does not match durable metadata")
        old_tag = ref.model_tag
        new_tag = "mcp_restore_" + str(metadata.get("sha256", operation_id))[:16]
        if new_tag in {str(tag) for tag in self.worker.client().tags()}:
            raise ExecutionContractError("ENGINE_BUSY", "restore target tag is already present; refusing to replace it")
        try:
            new_model = self.worker.client().load(path, tag=new_tag)
            import comsol_mcp._server as srv
            from ._model import _set_current_model
            _set_current_model(new_model, origin="checkpoint-restore", requested_path=str(path))
            old_ref = ref.as_dict()
            bound = self.service.bind_model(new_tag, ownership="mcp_owned")
        except Exception:
            try:
                self.worker.client().remove(new_tag)
            except Exception as cleanup_exc:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "restore target cleanup failed after checkpoint bind failure") from cleanup_exc
            raise
        self.service.ledger.retire_model(ref)
        self.persist()
        return {"success": True, "data": {"checkpoint_id": checkpoint_id, "restored_path": str(path), "old_model_ref": old_ref, "model_ref": bound["execution"]["model_ref"], "generation_advanced": True,
            "dirty_override": dirty_override,
            "restore_scope": metadata.get("restore_scope", {"model_settings": True, "solution": False, "external_dependencies": False, "gui_state": "unknown"})}, "execution": bound["execution"]}

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
