"""Bound legacy services running exclusively inside the serialized daemon."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import (
    ExecutionContractError,
    LEGACY_TOOL_EFFECTS,
    PreWriteRefusal,
    SessionLedger,
    model_ref_from_mapping,
    canonical_project_path,
    permission_for_legacy_tool,
)
from ._execution_service import ExecutionService
from ._runtime_state import save_runtime_state, restore_runtime_state
from ._g2_docs import OfflineDocsIndex
from ._g2_registry import CONTROL_IMPLEMENTED_OPERATIONS, IMPLEMENTED_OPERATIONS, LEGACY_FALLBACK_NAMES, is_implemented, validate_call
from ._g2_contract import NodePath
from ._g2_engine import (
    children_node, create_checkpoint, execute_transaction, find_nodes, inspect_node,
    property_entry_set, property_get, property_index_set, property_schema, property_set,
)
from ._g2_code import compile_result, describe_source, execution_result, read_source
from ._g2_transactions import TransactionStore, preview_transaction, validate_invariants
from ._g2_isolation import configured_receipt, verify_owned_server
from ._platform_paths import default_comsol_help_roots
from ._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionEndpointIdentity,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    session_state_directory,
)


class SessionConnectFailure(RuntimeError):
    """A scoped attach attempt with an explicit retry/uncertainty boundary."""

    def __init__(self, code: str, message: str, *, safe_retry: bool, uncertain: bool,
                 dispatched: bool, worker=None, reply=None, runtime_metadata=None):
        super().__init__(message)
        self.code = code
        self.safe_retry = bool(safe_retry)
        self.uncertain = bool(uncertain)
        self.dispatched = bool(dispatched)
        self.worker = worker
        self.reply = dict(reply) if isinstance(reply, Mapping) else None
        self.runtime_metadata = dict(runtime_metadata) if isinstance(runtime_metadata, Mapping) else None

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
    "HOST_CONTROL": "host_control",
    "DYNAMIC": "project_write",
}


#: Operations whose subject is the live runtime, not a model.  They are routed
#: without a model_ref and without an expected_revision (C05).
RUNTIME_STATIC_OPERATIONS = frozenset({
    "runtime.discover", "runtime.inspect", "runtime.doctor", "runtime.compatibility_report",
})
RUNTIME_SCOPED_OPERATIONS = frozenset({
    *RUNTIME_STATIC_OPERATIONS,
    "runtime.capabilities", "runtime.license_inspect",
    "runtime.license_checkout", "runtime.render_probe",
})


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
    def __init__(self, home, store, *, service=None, registry=None, worker=None, project_root=None,
                 session_worker_factory=None, session_peer_observer=None,
                 host_permission_ceiling=None):
        self.home, self.store = Path(home), store
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.package_resource_root = Path(__file__).resolve().parent
        self.private_control_root = self.home

        if project_root is not None:
            self.project_root = Path(project_root).resolve()
            self.project_root_explicit = True
        else:
            env_project = os.environ.get("COMSOL_PROJECT_ROOT")
            if env_project:
                self.project_root = Path(env_project).resolve()
                self.project_root_explicit = True
            else:
                fallback = Path(__file__).resolve().parents[1]
                if any(p in fallback.parts for p in ("site-packages", "dist-packages")):
                    raise ExecutionContractError(
                        "RUNTIME_CONFIGURATION_REQUIRED",
                        "COMSOL_PROJECT_ROOT or explicit project_root is required when running from site-packages; site-packages is not an authorized project root",
                    )
                self.project_root = fallback
                self.project_root_explicit = False

        # Deployment grants are captured once, before the first COMSOL
        # connection. A later Worker/session can narrow these grants but the
        # environment or persisted session cannot expand the startup ceiling.
        if host_permission_ceiling is None:
            self.host_permission_ceiling = {"inspect", "project_write", "compute"}
            if os.environ.get("COMSOL_MCP_TRUSTED_CODE", "").strip().lower() in {"1", "true", "yes"}:
                self.host_permission_ceiling.add("trusted_code")
            if os.environ.get("COMSOL_MCP_HOST_CONTROL", "").strip().lower() in {"1", "true", "yes"}:
                self.host_permission_ceiling.add("host_control")
        else:
            allowed = {"inspect", "project_write", "compute", "trusted_code", "host_control"}
            if not isinstance(host_permission_ceiling, (set, frozenset)) or not set(host_permission_ceiling) <= allowed:
                raise ValueError("host_permission_ceiling must be a trusted permission set")
            self.host_permission_ceiling = set(host_permission_ceiling)
        self._project_root_context = ContextVar(f"comsol_project_root_{id(self)}", default=None)
        # Strict field/integral modes are available only to the backend-owned
        # W21 output reader. They are kept in a context variable instead of a
        # request flag, so public result.evaluate callers cannot enable them.
        self._stage_output_mode_context = ContextVar(f"comsol_w21_output_mode_{id(self)}", default=None)
        # Initial-stage admission/output metadata can only be requested by
        # backend-owned readers. This context never comes from an operation
        # argument and preserves the public G3 schemas.
        self._stage_native_context = ContextVar(f"comsol_w21_initial_stage_{id(self)}", default=None)
        # Full current-mesh snapshots are similarly restricted to the
        # backend-owned stage evidence reader. The public mesh.inspect schema
        # remains unchanged and cannot select this branch.
        self._stage_mesh_snapshot_context = ContextVar(f"comsol_w21_mesh_snapshot_{id(self)}", default=None)
        self._model_project_bindings: dict[str, str | None] = {}

        help_roots = default_comsol_help_roots(self.project_root)
        self.docs_index = OfflineDocsIndex(self.home / "docs_index.sqlite3", allowed_roots=help_roots)
        self.transactions = TransactionStore(self.home / "transactions.json")
        self.service, self.worker = service, worker
        self.session_worker_factory = session_worker_factory
        self.session_peer_observer = session_peer_observer
        self.registry = dict(registry) if registry is not None else collect_legacy_registry()
        self.server_lock = None
        self.worker_identity = None
        self.endpoint_key = None
        self.cached = {"connected": False, "server_ownership": "shared",
                       "external_change_detection": _external_change_detection_metadata()}
        if service is not None:
            self.cached.update(connected=True, session_id=service.ledger.session_id)

    def close(self):
        if hasattr(self, "docs_index") and hasattr(self.docs_index, "close"):
            try:
                self.docs_index.close()
            except Exception:
                pass

    @property
    def project_root(self):
        scoped = self._project_root_context.get(None)
        return scoped if scoped is not None else self._base_project_root

    @project_root.setter
    def project_root(self, value):
        self._base_project_root = Path(value).resolve()

    def stage_native_admission(self, *, binding, plan, stage, event_callback=None,
                               authorize_callback=None):
        """Return the current fail-closed native admission state for one stage.

        A caller declaration cannot populate these facts.  This build has no
        verified target field/unit/frame identity reader, so the backend does
        not authorize a stage solve.  Keeping the response here makes the
        missing native capability explicit at the dispatch boundary.
        """
        from ._w21_stage_backend import ADMISSION_CONTRACT, is_registered_initial_state_stage

        if is_registered_initial_state_stage(plan, stage):
            from ._w21_initial_stage import produce_initial_stage_preflight
            return produce_initial_stage_preflight(
                self, binding=binding, plan=plan, stage=stage,
                event_callback=event_callback, authorize_callback=authorize_callback,
            )
        return {
            "contract": ADMISSION_CONTRACT,
            "producer": "managed-backend-unverified",
            "binding": dict(binding),
            "facts": {
                "source_attempt_binding": "UNVERIFIED",
                "target_field_identity": "UNVERIFIED",
                "source_target_units": "UNVERIFIED",
                "source_target_mesh": "UNVERIFIED",
                "frame_identity": "UNVERIFIED",
                "history_identity": "UNVERIFIED",
            },
            "evidence_refs": [],
            "status": "UNVERIFIED",
        }

    def stage_output_readback(self, *, binding, stage, plan, stage_run_operation_id,
                              model_revision, solve_result, event_callback=None,
                              authorize_callback=None):
        """Read stage outputs through managed G3 result operations.

        The producer uses ordinary service tickets and the current Worker. Its
        private strict modes are scoped to this call and cannot be requested in
        an MCP argument or execution envelope.
        """
        from ._w21_native_output import produce_stage_output_readback

        return produce_stage_output_readback(
            self, binding=binding, stage=stage, plan=plan,
            stage_run_operation_id=stage_run_operation_id,
            model_revision=model_revision, solve_result=solve_result,
            event_callback=event_callback, authorize_callback=authorize_callback,
        )

    def stage_mesh_snapshot_readback(self, *, project_id, model_ref, attempt_id, phase,
                                     model_revision, component, mesh, geometry,
                                     event_callback=None):
        """Read and persist a bounded current mesh snapshot for one exact attempt."""
        from ._w21_mesh_readback import produce_stage_mesh_snapshot_readback

        return produce_stage_mesh_snapshot_readback(
            self, project_id=project_id, model_ref=model_ref, attempt_id=attempt_id,
            phase=phase, model_revision=model_revision, component=component,
            mesh=mesh, geometry=geometry, event_callback=event_callback,
        )

    @contextmanager
    def project_root_scope(self, root):
        """Apply one registered project path/workflow scope in this context."""
        candidate = Path(root).resolve(strict=True)
        token = self._project_root_context.set(candidate)
        service = self.service
        service_scope = getattr(service, "project_root_scope", None)
        try:
            from ._state import project_workflow_state_scope

            with project_workflow_state_scope(candidate):
                if callable(service_scope):
                    with service_scope(candidate):
                        yield candidate
                else:
                    yield candidate
        finally:
            self._project_root_context.reset(token)

    @staticmethod
    def _model_project_key(model_ref):
        from ._execution_contract import model_ref_from_mapping
        ref = model_ref_from_mapping(dict(model_ref)).as_dict()
        return json.dumps(ref, sort_keys=True, separators=(",", ":"))

    def model_project_binding(self, model_ref):
        """Return a persisted exact-ref project binding or UNATTRIBUTED."""
        key = self._model_project_key(model_ref)
        metadata = self.store.get_metadata("revisions", key)
        if not isinstance(metadata, Mapping) or not isinstance(metadata.get("project_id"), str):
            return {"attribution": "UNATTRIBUTED", "project_id": None}
        return {"attribution": "PROJECT_BOUND", "project_id": metadata["project_id"]}

    def _bind_model_project(self, model_ref, project_id):
        key = self._model_project_key(model_ref)
        existing = self.store.get_metadata("revisions", key)
        existing_project = existing.get("project_id") if isinstance(existing, Mapping) else None
        if existing_project is not None and existing_project != project_id:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "ModelRef is already bound to a different project")
        if project_id is not None and (not isinstance(project_id, str) or not project_id):
            raise ExecutionContractError("INVALID_REQUEST", "execution.project_id must be a nonempty string")
        if project_id is not None:
            self._model_project_bindings[key] = project_id
        elif existing_project is not None:
            self._model_project_bindings[key] = existing_project
        else:
            self._model_project_bindings[key] = None

    def context(self, operation_id, callback):
        if self.worker is not None:
            return self.worker.operation_context(operation_id, on_request_event=callback)
        return nullcontext()

    def _bind_confirmed_worker_endpoint(self, endpoint, port, canonical_host):
        """Bind only after Worker health reports the exact connected endpoint.

        This also handles a Worker injected by the owning application. Its
        being connected is not itself proof that it is connected to the
        endpoint named by a new control session.
        """
        if self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "a persistent Worker is required")
        health = self.worker.health()
        if not isinstance(health, dict):
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "Worker health did not return endpoint identity")
        if health.get("connected") is True:
            if health.get("server") != endpoint:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "worker is connected to a different endpoint")
        else:
            self.worker.client().connect(port, canonical_host)
        verified = self.worker.health()
        if not isinstance(verified, dict):
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "Worker health did not confirm endpoint identity")
        if verified.get("connected") is not True or verified.get("server") != endpoint:
            raise ExecutionContractError(
                "MODEL_IDENTITY_MISMATCH",
                "Worker health did not confirm the exact connected COMSOL endpoint",
            )
        self.endpoint_key = endpoint
        return verified

    @staticmethod
    def _parse_socket_endpoint(value: Any) -> tuple[str, int] | None:
        if not isinstance(value, str) or not value:
            return None
        text = value.strip()
        if text.startswith("[") and "]" in text:
            end = text.find("]")
            host, suffix = text[1:end], text[end + 1:]
            if not suffix.startswith(":"):
                return None
            port_text = suffix[1:]
        else:
            if ":" not in text:
                return None
            host, port_text = text.rsplit(":", 1)
        try:
            address = ipaddress.ip_address(host.strip("[]")).compressed
            port = int(port_text)
        except (TypeError, ValueError):
            return None
        if not 1 <= port <= 65535:
            return None
        return address, port

    @classmethod
    def _observe_session_peer(cls, worker, port: int, runtime_metadata: Mapping[str, Any]) -> CanonicalSocket | None:
        """Return one OS-observed remote peer for this exact Worker process."""
        pid = runtime_metadata.get("pid")
        if type(pid) is not int or pid <= 1:
            return None
        try:
            from ._g2_isolation import _row_connection, _socket_rows
            rows = _socket_rows(port)
        except Exception:
            return None
        peers: set[CanonicalSocket] = set()
        for row in rows:
            if not isinstance(row, Mapping) or row.get("pid") != pid or row.get("state") != "ESTABLISHED":
                continue
            _local, remote = _row_connection(row)
            parsed = cls._parse_socket_endpoint(remote)
            if parsed is not None and parsed[1] == port:
                peers.add(CanonicalSocket(*parsed))
        return next(iter(peers)) if len(peers) == 1 else None

    @staticmethod
    def _session_server_instance_id(peer: CanonicalSocket, reply: Mapping[str, Any]) -> str:
        material = "\0".join((peer.address, str(peer.port), str(reply["instance_id"]),
                              str(reply["generation"]), str(reply["engine_version"])))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def connect_session(self, *, runtime: SessionRuntimeConfig, project_id: str, session_id: str,
                        host: str, port: int, operation_id: str, request_id: str,
                        event_callback, credentials: Mapping[str, Any] | None = None,
                        rpc_timeout_s: float = 30.0, project_permissions=None,
                        existing_worker=None, server_ownership: str = "shared",
                        owned_process: OwnedServerProcessIdentity | None = None):
        """Attach one private Worker to an already-listening endpoint.

        This route never touches ``_server`` globals, mutates process
        environment, or assumes ownership of the COMSOL server.
        """
        from ._java_worker import JavaWorkerError, JavaWorkerPaths, JavaWorkerTimeout, PersistentJavaWorker

        if not isinstance(runtime, SessionRuntimeConfig):
            raise SessionConnectFailure("RUNTIME_CONFIGURATION_REQUIRED", "local runtime configuration is unavailable",
                                        safe_retry=True, uncertain=False, dispatched=False)
        if server_ownership not in {"shared", "mcp_managed", "user_owned"}:
            raise SessionConnectFailure("SERVER_OWNERSHIP_UNKNOWN", "Server ownership classification is invalid",
                                        safe_retry=False, uncertain=False, dispatched=False)
        if owned_process is not None and not isinstance(owned_process, OwnedServerProcessIdentity):
            raise SessionConnectFailure("SERVER_OWNERSHIP_UNKNOWN", "Server process identity is malformed",
                                        safe_retry=False, uncertain=False, dispatched=False)
        if (server_ownership == "mcp_managed") != (owned_process is not None):
            raise SessionConnectFailure("SERVER_OWNERSHIP_UNKNOWN", "MCP-managed Server requires its exact live process identity",
                                        safe_retry=False, uncertain=False, dispatched=False)
        try:
            runtime_for_worker = runtime
            session_home = session_state_directory(runtime.session_state_root, project_id, session_id)
            session_home.mkdir(mode=0o700, parents=True, exist_ok=True)
            if session_home.is_symlink() or not session_home.resolve().is_relative_to(runtime.session_state_root.resolve()):
                raise ValueError("session state directory is not private")
            worker_state = session_home / "worker"
            if server_ownership == "mcp_managed":
                backend_home = self.home.resolve(strict=True)
                session_home_real = session_home.resolve(strict=True)
                if self.home.is_symlink() or backend_home.parent != session_home_real:
                    raise ValueError("managed Worker backend is not bound to this session")
                from ._session_server import owned_server_preferences_directory

                preferences = owned_server_preferences_directory(
                    runtime, project_id, session_id, owned_process,
                )
                runtime_for_worker = replace(runtime, preferences_dir=preferences)
            else:
                preferences = runtime.preferences_dir
            if preferences.is_symlink():
                raise ValueError("COMSOL preferences directory cannot be a symlink")
            if server_ownership == "mcp_managed":
                if not preferences.is_dir():
                    raise ValueError("owned Server preferences directory is unavailable")
            else:
                preferences.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not isinstance(host, str) or not host.strip() or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("endpoint is invalid")
            if existing_worker is not None:
                # Lifecycle reconnect must use the exact retained Worker and
                # prove it is already alive before issuing another connect.
                # Never call start() here: it may create a replacement child
                # after the original handle has become unreachable.
                worker = existing_worker
            elif self.session_worker_factory is not None:
                # This injection is reserved for deterministic tests/host
                # adapters. The default production path below always validates
                # the inspected installation, JDK and exact classpath.
                worker = self.session_worker_factory(runtime_for_worker, worker_state)
            else:
                paths = JavaWorkerPaths(
                    runtime_for_worker.installation_root,
                    runtime_for_worker.java_executable.parent.parent,
                    runtime_for_worker.preferences_dir,
                    project_root=self.project_root,
                )
                paths.validate()
                classpath, _manifest_hash, _jar_count, _jar_hash = paths.classpath()
                observed_classpath = tuple(Path(item) for item in classpath.split(paths.classpath_separator) if item)
                if observed_classpath != runtime.classpath:
                    raise ValueError("runtime classpath changed after trusted inspection")
                worker = PersistentJavaWorker(paths, state_dir=worker_state)
        except Exception as exc:
            raise SessionConnectFailure(
                "RUNTIME_CONFIGURATION_REQUIRED", "local Worker runtime configuration failed validation",
                safe_retry=True, uncertain=False, dispatched=False,
            ) from exc

        runtime_metadata = None
        reply = None
        dispatched = False
        worker_started = False
        try:
            if existing_worker is None:
                worker.start()
                worker_started = True
            else:
                # The handle is known to have existed already.  A health
                # failure leaves its process/request state unknown and must
                # not trigger a Worker replacement.
                worker_started = True
            runtime_metadata = worker.runtime_metadata()
            if not isinstance(runtime_metadata, Mapping):
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "Worker identity is unavailable",
                                            safe_retry=False, uncertain=True, dispatched=False, worker=worker)
            if runtime_metadata.get("connected") is True:
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "private Worker already has a server binding",
                                            safe_retry=False, uncertain=True, dispatched=False, worker=worker,
                                            runtime_metadata=runtime_metadata)
            resolved = dict(credentials or {})
            user, password = resolved.get("user", ""), resolved.get("password", "")
            if not isinstance(user, str) or not isinstance(password, str):
                raise SessionConnectFailure("AUTHORIZATION_REQUIRED", "resolved connection credentials are malformed",
                                            safe_retry=True, uncertain=False, dispatched=False, worker=worker,
                                            runtime_metadata=runtime_metadata)
            operation_context = getattr(worker, "operation_context", None)
            scope = operation_context(operation_id, on_request_event=event_callback) if callable(operation_context) else nullcontext()
            with scope:
                dispatched = True
                reply = worker.client().connect(
                    port, host, request_id=request_id, rpc_timeout_s=rpc_timeout_s,
                    user=user, password=password,
                )
            if not isinstance(reply, Mapping):
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "Worker connect reply is malformed",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
            reply = dict(reply)
            generation, instance = reply.get("generation"), reply.get("instance_id")
            if (reply.get("connected") is not True or not isinstance(reply.get("server"), str)
                    or type(generation) is not int or generation < 1
                    or not isinstance(instance, str) or not instance
                    or not isinstance(reply.get("engine_version"), str) or not reply["engine_version"]):
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "Worker did not return an exact remote connect identity",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
            if reply["server"] != f"{host}:{port}":
                raise SessionConnectFailure("MODEL_IDENTITY_MISMATCH", "remote connect reply differs from the requested endpoint",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
            try:
                runtime_metadata = worker.runtime_metadata()
            except Exception:
                pass
            if (not isinstance(runtime_metadata, Mapping)
                    or runtime_metadata.get("instance_id") != instance
                    or runtime_metadata.get("generation") != generation
                    or runtime_metadata.get("connected") is not True
                    or runtime_metadata.get("server") != reply["server"]):
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "Worker health does not agree with its connect reply",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
            observer = self.session_peer_observer or self._observe_session_peer
            try:
                peer = observer(worker, port, runtime_metadata)
            except Exception:
                peer = None
            if not isinstance(peer, CanonicalSocket) or peer.port != port:
                raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "connected remote peer could not be independently observed",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
            if owned_process is not None and not owned_process.attests_peer(peer):
                raise SessionConnectFailure("SERVER_OWNERSHIP_UNKNOWN", "observed remote peer is not attested by the retained Server listener",
                                            safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                                            reply=reply, runtime_metadata=runtime_metadata)
        except SessionConnectFailure:
            raise
        except JavaWorkerTimeout as exc:
            try:
                runtime_metadata = worker.runtime_metadata()
            except Exception:
                pass
            message = ("Worker connect timed out; preserve the original Worker handle" if dispatched
                       else "Worker startup timed out; preserve the original Worker handle")
            raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", message,
                                        safe_retry=False, uncertain=True, dispatched=dispatched, worker=worker,
                                        runtime_metadata=runtime_metadata) from exc
        except JavaWorkerError as exc:
            try:
                runtime_metadata = worker.runtime_metadata()
            except Exception:
                pass
            detail = str(exc).casefold()
            unsafe_start = not worker_started and any(token in detail for token in (
                "alive but unreachable", "do not start a replacement", "identity cannot be verified",
                "manual reconciliation is required",
            ))
            unknown = bool(getattr(exc, "execution_state_unknown", False)) or unsafe_start
            raise SessionConnectFailure(
                "EXECUTION_STATE_UNKNOWN" if unknown else "ENGINE_UNRESPONSIVE",
                "Worker connect failed; inspect the original Worker before retrying" if unknown else "Worker refused the connection before an uncertain remote state",
                safe_retry=not unknown, uncertain=unknown, dispatched=dispatched, worker=worker,
                reply=getattr(exc, "reply", None), runtime_metadata=runtime_metadata,
            ) from exc
        except Exception as exc:
            raise SessionConnectFailure("EXECUTION_STATE_UNKNOWN", "Worker connect ended without a verified result",
                                        safe_retry=False, uncertain=dispatched or worker_started, dispatched=dispatched,
                                        worker=worker, reply=reply, runtime_metadata=runtime_metadata) from exc

        # Once the Worker has attached, even local ledger/service/cache writes
        # are part of the uncertain lifecycle.  A disk or constructor failure
        # here must return the exact live handle to the daemon for UNKNOWN
        # reconciliation; it must never look like a pre-dispatch refusal.
        reply_with_peer = {**reply, "observed_peer": {"address": peer.address, "port": peer.port}}
        try:
            server_id = self._session_server_instance_id(peer, reply)
            remote_build = reply.get("engine_build")
            if not isinstance(remote_build, str) or not remote_build.strip():
                remote_build = None
            worker_identity = {
                "runtime_id": runtime.runtime_id,
                "worker_instance_id": instance,
                "connection_epoch": generation,
                "server_instance_id": server_id,
                "server_ownership": server_ownership,
                "endpoint": reply["server"],
                "observed_peer": {"address": peer.address, "port": peer.port},
                "remote_engine_version": reply["engine_version"],
                "remote_engine_build": remote_build,
                "remote_engine_build_source": "remote-connect-reply" if remote_build is not None else "NOT_REPORTED",
            }
            permissions = set(project_permissions or ()) & self.host_permission_ceiling
            ledger = SessionLedger(session_id, server_id, server_ownership=server_ownership, permissions=permissions)
            self.worker = worker
            self.worker_identity = worker_identity
            backend = self

            class Adapter:
                def model_snapshot(self, tag):
                    raw = backend.worker.backend_snapshot(tag)
                    if (raw.get("generation") != generation or raw.get("instance_id") != instance
                            or raw.get("server_instance_id") != reply["server"]):
                        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "Worker connection epoch changed")
                    return {**raw, "server_instance_id": server_id}

            self.service = ExecutionService(ledger, Adapter(), project_root=self.project_root,
                                            on_state_change=lambda event: self.persist())
            self.endpoint_key = reply["server"]
            self.cached = {
                "connected": True,
                "session_id": session_id,
                "runtime_id": runtime.runtime_id,
                "server_ownership": server_ownership,
                "endpoint": reply["server"],
                "observed_peer": {"address": peer.address, "port": peer.port},
                "worker": {k: v for k, v in runtime_metadata.items() if k != "token"},
                "remote_engine_version": reply["engine_version"],
                "remote_engine_build": worker_identity["remote_engine_build"],
                "remote_engine_build_source": worker_identity["remote_engine_build_source"],
            }
            self.persist()
            return {"worker": worker, "reply": reply_with_peer, "runtime_metadata": dict(runtime_metadata),
                    "peer": peer, "worker_identity": dict(worker_identity), "server_instance_id": server_id,
                    "server_ownership": server_ownership, "owned_process": owned_process}
        except SessionConnectFailure:
            raise
        except Exception as exc:
            self.worker = worker
            raise SessionConnectFailure(
                "EXECUTION_STATE_UNKNOWN",
                "Worker attached, but managed session initialization or durable persistence failed",
                safe_retry=False, uncertain=True, dispatched=True, worker=worker,
                reply=reply_with_peer, runtime_metadata=runtime_metadata,
            ) from exc

    def connect(self, arguments, operation_id, event_callback, *, project_id=None):
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
        # Explicit reconnect can replace a confirmed-dead Worker. start() refuses
        # an alive but unreachable endpoint and never replays an engine request.
        self.worker.start()
        with self.context(operation_id, event_callback):
            self._bind_confirmed_worker_endpoint(key, port, canonical_host)
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
                ledger.permissions.discard("trusted_code")
                if "trusted_code" in self.host_permission_ceiling:
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
            # Host-level actions are a separate opt-in. In particular, a
            # successful Worker connection never grants license checkout.
            # Reapply only the deployment ceiling captured when this backend
            # was constructed. This strips stale persisted grants when a new
            # backend starts with the capability disabled, while environment
            # changes during this backend's lifetime cannot expand or revoke
            # its startup ceiling; restart the backend to apply a deployment
            # configuration change.
            ledger = self.service.ledger
            ledger.permissions.discard("host_control")
            ledger.permissions.discard("trusted_code")
            if "trusted_code" in self.host_permission_ceiling:
                ledger.permissions.add("trusted_code")
            if "host_control" in self.host_permission_ceiling:
                ledger.permissions.add("host_control")
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
                           "host_control": {"enabled": "host_control" in self.service.ledger.permissions,
                                             "scope": "current managed session", "config": "COMSOL_MCP_HOST_CONTROL"},
                           "worker": {k: v for k, v in runtime.items() if k != "token"}}
            self.persist()
            model_name = str(arguments.get("model_name") or "").strip()
            if model_name:
                found = [m for m in srv._client.models() if m.name() == model_name or str(m.java.tag()) == model_name]
                if len(found) != 1:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_name is not unique on this server")
                return self.adopt(str(found[0].java.tag()), project_id=project_id)
            return {"success": True, "data": dict(self.cached),
                    "execution": {"session_id": session_id, "model_ref": None, "revision": None}}

    def persist(self):
        if self.service is None or self.worker_identity is None:
            return
        ledger = self.service.ledger
        save_runtime_state(self.store, ledger, self.worker_identity)
        for state in ledger._models.values():
            key = self._model_project_key(state.ref.as_dict())
            previous = self.store.get_metadata("revisions", key) or {}
            project_id = self._model_project_bindings.get(key, previous.get("project_id"))
            metadata = {
                "model_ref": state.ref.as_dict(), "revision": state.revision, "dirty": state.dirty,
                "fingerprint": state.fingerprint, "active_operation_id": state.active_operation_id,
                "attribution": "PROJECT_BOUND" if isinstance(project_id, str) else "UNATTRIBUTED",
            }
            if isinstance(project_id, str):
                metadata["project_id"] = project_id
            self.store.put_metadata("revisions", key, metadata)

    def adopt(self, tag, *, project_id=None):
        from ._server import session_server as srv
        from ._model import _set_current_model
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connect to a server first")
        if "project_write" not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", "model adoption requires project_write")
        model = self.worker.client().model(tag)
        _set_current_model(model, origin="adopted-by-tag")
        metadata = self.service.bind_model(tag, ownership="mcp_owned" if tag in srv._mcp_owned_model_tags else "user_owned")
        ref = metadata.get("execution", {}).get("model_ref")
        if isinstance(ref, Mapping):
            self._bind_model_project(ref, project_id)
        self.persist()
        return {"success": True, "data": {"model_tag": tag}, **metadata}

    def invoke(self, operation, arguments, execution, operation_id, event_callback):
        from ._g2_registry import NODE_ACTIONS
        unit_a = NODE_ACTIONS | {"api.describe", "api.invoke", "checkpoint.branch", "api.probe", "checkpoint.diff"}
        aliases = {name.replace(".", "_"): name for name in unit_a}
        operation = aliases.get(operation, operation)
        if operation in {"registry_call", "operation_call"} and arguments.get("operation_id") == "api.invoke":
            from ._g2_public_api import routed_arguments
            routed_arguments(operation, arguments)
        if operation == "server_connect":
            return self.connect(arguments, operation_id, event_callback, project_id=execution.get("project_id"))
        if operation == "artifact.register":
            return self._register_project_artifact(arguments, execution, operation_id)
        if operation == "job.resume":
            raise ExecutionContractError(
                "CONTROL_PLANE_ROUTE_REQUIRED",
                "job.resume must be admitted and queued by ControlDaemon",
            )
        if operation in CONTROL_IMPLEMENTED_OPERATIONS:
            raise ExecutionContractError(
                "CONTROL_PLANE_ROUTE_REQUIRED",
                f"{operation} must be dispatched by ControlDaemon to the cached operation store",
            )
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
            if inner in {"metric.define", "metric.list", "metric.evaluate", "metric.remove", "metric.compare"}:
                nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
                present = sorted(nested_identity.intersection(supplied_args))
                if present:
                    raise ExecutionContractError(
                        "INVALID_REQUEST",
                        f"{inner} identity belongs in the outer execution envelope: {', '.join(present)}",
                    )
            if inner in {"model.adopt", "model.inspect"}:
                nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
                present = sorted(nested_identity.intersection(supplied_args))
                if present:
                    raise ExecutionContractError(
                        "INVALID_REQUEST",
                        f"{inner} identity belongs in the outer execution envelope: {', '.join(present)}",
                    )
            if inner == "study.run":
                nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
                present = sorted(nested_identity.intersection(supplied_args))
                if present:
                    raise ExecutionContractError(
                        "INVALID_REQUEST",
                        f"study.run identity belongs in the outer execution envelope: {', '.join(present)}",
                    )
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
                call_args = self._g2_body(call_args, entry.operation_id)
            return self.invoke(entry.operation_id, call_args, nested_execution, operation_id, event_callback)
        if operation in {"docs_index", "docs.index", "docs.search", "docs.get", "docs.examples", "docs.error_search",
                         "code_describe_java", "code.describe_java", "code_compile_java", "code.compile_java",
                         "code.inspect_run", "code_inspect_run",
                         "transaction_preview", "transaction.preview", "checkpoint.list", "checkpoint.inspect"}:
            return self._invoke_g2_control(operation, arguments, execution, operation_id)
        if operation in RUNTIME_SCOPED_OPERATIONS:
            # C05: a runtime capability/licence question is a property of the
            # runtime, not of a model.  It is routed without a model_ref and
            # without an expected_revision (an unbound call must succeed), and a
            # model_ref that *is* supplied is used for the inventory read only.
            return self._invoke_runtime_scoped(operation, arguments, execution, operation_id, event_callback)
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
        stage_marker = execution.get("_w21_stage_marker")
        stage_save_target = None
        if stage_marker is not None:
            expected_phase = {"run_study": "solve", "save_model": "save"}.get(operation)
            if (not isinstance(stage_marker, Mapping) or expected_phase is None
                    or stage_marker.get("phase") != expected_phase
                    or not isinstance(stage_marker.get("attempt_id"), str)
                    or not stage_marker.get("attempt_id")
                    or stage_marker.get("project_id") != execution.get("project_id")
                    or ref is None
                    or stage_marker.get("model_ref") != ref.as_dict()
                    or stage_marker.get("expected_revision") != execution.get("expected_revision")
                    or stage_marker.get("request_id") != execution.get("request_id")
                    or stage_marker.get("operation_id") != operation_id
                    or not isinstance(stage_marker.get("binding_sha256"), str)
                    or len(stage_marker.get("binding_sha256", "")) != 64):
                raise ExecutionContractError(
                    "MODEL_IDENTITY_MISMATCH",
                    "private W21 stage dispatch marker does not match its exact operation identity",
                )
            if expected_phase == "solve":
                if (not isinstance(arguments.get("study_tag"), str)
                        or not arguments.get("study_tag")
                        or stage_marker.get("study_tag") != arguments.get("study_tag")):
                    raise ExecutionContractError(
                        "MODEL_IDENTITY_MISMATCH",
                        "private W21 solve marker does not match the requested study tag",
                    )
            else:
                relative_path = arguments.get("path")
                active_project_root = self._project_root_context.get() or self.project_root
                if (not isinstance(relative_path, str) or not relative_path
                        or stage_marker.get("save_path") != relative_path):
                    raise ExecutionContractError(
                        "PROJECT_IDENTITY_MISMATCH",
                        "private W21 save marker does not match the requested stage output path",
                    )
                stage_save_target = canonical_project_path(active_project_root, relative_path)
                if stage_marker.get("save_target_path") != str(stage_save_target):
                    raise ExecutionContractError(
                        "PROJECT_IDENTITY_MISMATCH",
                        "private W21 save marker does not match its canonical project output path",
                    )
            marker_binding = self.model_project_binding(ref.as_dict())
            if (marker_binding.get("attribution") != "PROJECT_BOUND"
                    or marker_binding.get("project_id") != execution.get("project_id")):
                raise ExecutionContractError(
                    "PROJECT_IDENTITY_MISMATCH",
                    "private W21 stage dispatch has no exact registered project/ModelRef binding",
                )
        callback_target = [event_callback]

        def dynamic_worker_event(event):
            callback = callback_target[0]
            if callable(callback):
                callback(event)

        with self.context(operation_id, dynamic_worker_event):
            if operation == "model_adopt":
                return self.adopt(arguments["model_tag"], project_id=execution.get("project_id"))
            if operation == "model_inspect":
                if ref is None:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref is required")
                if arguments.get("refresh", False):
                    return {"success": True, "data": {}, **self.service.reconcile(ref)}
                return {"success": True, "data": {}, **self.service.inspect(ref)}
            if operation in {"server_start", "server_disconnect", "prune_loaded_models"}:
                raise ExecutionContractError("PERMISSION_DENIED", "shared-server lifecycle changes are not enabled by this backend")
            from ._server import session_server as srv
            from ._model import _set_current_model
            bound_model = None
            if stage_marker is not None:
                if (ref is None or self.worker is None or not callable(event_callback)):
                    raise ExecutionContractError(
                        "WORKER_IDENTITY_UNAVAILABLE",
                        "W21 stage dispatch requires a bound ModelRef, Worker, and event callback",
                    )
                backend_binding = {
                    "phase": stage_marker["phase"],
                    "model_tag": ref.model_tag,
                    "model_handle": None,
                    "worker_generation": getattr(self.worker, "generation", None),
                    "save_target_path": str(stage_save_target) if stage_save_target is not None else None,
                    "model_ref": ref.as_dict(),
                    "project_id": execution.get("project_id"),
                    "attempt_id": stage_marker["attempt_id"],
                    "expected_revision": stage_marker["expected_revision"],
                    "request_id": stage_marker["request_id"],
                    "operation_id": operation_id,
                    "binding_sha256": stage_marker["binding_sha256"],
                }
                if (type(backend_binding["worker_generation"]) is not int
                        or backend_binding["worker_generation"] < 1):
                    raise ExecutionContractError(
                        "WORKER_IDENTITY_UNAVAILABLE",
                        "W21 stage dispatch has no current Worker generation",
                    )
                original_event_callback = event_callback

                def bound_stage_event(event):
                    enriched = dict(event) if isinstance(event, Mapping) else {"event": event}
                    enriched["w21_backend_binding"] = dict(backend_binding)
                    original_event_callback(enriched)

                # Model resolution and _set_current_model can perform genuine
                # Worker reads (including getFilePath) before the solve/save
                # callback is entered. Bind those requests to the stage before
                # issuing them; the first model command has no handle yet, so
                # its exact tag/ModelRef/attempt binding deliberately carries
                # model_handle=None until the returned RemoteModel is known.
                callback_target[0] = bound_stage_event
                bound_model = self.worker.client().model(ref.model_tag)
                if (not isinstance(getattr(bound_model, "_handle", None), str)
                        or not bound_model._handle
                        or type(getattr(bound_model, "_generation", None)) is not int
                        or bound_model._generation != backend_binding["worker_generation"]):
                    raise ExecutionContractError(
                        "WORKER_IDENTITY_UNAVAILABLE",
                        "W21 stage model resolution returned a different Worker handle or generation",
                    )
                backend_binding["model_handle"] = bound_model._handle
                _set_current_model(bound_model, origin="bound-request")
            elif ref and self.worker is not None:
                bound_model = self.worker.client().model(ref.model_tag)
                _set_current_model(bound_model, origin="bound-request")
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
            if (actual in {"run_study", "save_model"} and result.get("success") is True
                    and ref is not None and stage_marker is not None):
                requested_project = execution.get("project_id")
                project_binding = self.model_project_binding(ref.as_dict())
                reply_execution = result.get("execution")
                if (not isinstance(requested_project, str) or not requested_project
                        or not isinstance(reply_execution, Mapping)
                        or project_binding.get("attribution") != "PROJECT_BOUND"
                        or project_binding.get("project_id") != requested_project):
                    raise ExecutionContractError(
                        "PROJECT_IDENTITY_MISMATCH",
                        f"{actual} reply cannot be bound to the exact registered project and ModelRef",
                    )
                result["execution"] = {**reply_execution, "project_id": requested_project}
            if not ref and (actual in selections or starter) and result.get("success") and self.worker is not None:
                tag = str(srv._current_model.java.tag())
                bound = self.service.bind_model(tag, ownership="mcp_owned" if tag in srv._mcp_owned_model_tags else "user_owned")
                ref_mapping = bound.get("execution", {}).get("model_ref")
                if isinstance(ref_mapping, Mapping):
                    self._bind_model_project(ref_mapping, execution.get("project_id"))
                result["execution"] = bound["execution"]
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
            "TRUSTED_CODE": "trusted_code", "HOST_CONTROL": "host_control",
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
    def _g2_body(arguments: dict[str, Any], operation: str | None = None) -> dict[str, Any]:
        body = {key: value for key, value in arguments.items() if key not in {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}}
        is_w20 = operation is not None and (operation.startswith("validate.") or operation.startswith("validate_")
                    or operation in {"parameter.case_manage", "study.sweep_manage", "experiment.design",
                                     "experiment.run", "optimization.bounded_run", "solver.solution_transfer",
                                     "stage.state_transfer", "stage.checkpoint_create"})
        if is_w20 and "arguments" in body and isinstance(body["arguments"], Mapping):
            inner = dict(body.pop("arguments"))
            for k, v in inner.items():
                if k in body and body[k] is not None and body[k] != v:
                    raise ExecutionContractError("INVALID_REQUEST", f"Conflicting values for argument '{k}'")
                if k not in body or body[k] is None:
                    body[k] = v
        return body

    def _invoke_g2_control(self, operation, arguments, execution, operation_id):
        if operation in {"docs_index", "docs.index"}:
            body = self._g2_body(arguments, operation)
            result = self.docs_index.index(runtime_id=body.get("runtime_id", ""), sources=body.get("sources", []), version=body.get("version"), product=body.get("product", "COMSOL"))
            return {"success": True, "data": result, "execution": {"operation_id": operation_id}}
        if operation in {"docs.search", "docs.get", "docs.examples", "docs.error_search"}:
            body = self._g2_body(arguments, operation)
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
        if operation in {"checkpoint.list", "checkpoint.inspect"}:
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
        prefs = os.environ.get("COMSOL_PREFS_DIR")
        self.worker = PersistentJavaWorker(JavaWorkerPaths(Path(root), Path(java), Path(prefs) if prefs else None,
                                                           project_root=self.project_root),
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

    def _invoke_runtime_scoped(self, operation, arguments, execution, operation_id, event_callback=None):
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

        function = DISPATCH.get(operation)
        if function is None:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"G3 operation is not executable: {operation}")
        if operation in RUNTIME_STATIC_OPERATIONS:
            return self._invoke_runtime_static(operation, function, arguments, execution, operation_id)
        if operation in {"runtime.license_checkout", "runtime.render_probe"}:
            return self._invoke_runtime_control(
                operation, function, arguments, execution, operation_id,
                catalog_effect=str(EFFECTS.get(operation, "")).upper(),
                event_callback=event_callback,
            )

        if self.service is None or self.worker is None:
            raise ExecutionContractError(
                "ENGINE_UNRESPONSIVE", "a connected persistent Worker is required for a runtime probe"
            )
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

    def _invoke_runtime_static(self, operation, function, arguments, execution, operation_id):
        """Serve install metadata reads without requiring or starting COMSOL."""
        if execution.get("model_ref") is not None or arguments.get("model_ref") is not None:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "runtime metadata reads cannot carry model_ref")
        if execution.get("expected_revision") is not None or arguments.get("expected_revision") is not None:
            raise ExecutionContractError("INVALID_REQUEST", "runtime metadata reads cannot carry expected_revision")
        session = execution.get("session_id") or arguments.get("session_id")
        if self.service is None:
            if session is not None:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "no connected session matches the request")
            body = self._g2_body(arguments)
            data = function(None, "", body)
            return {"success": True, "data": data,
                    "execution": {"operation_id": operation_id, "session_id": None,
                                  "model_ref": None, "revision": None}}
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        body = self._g2_body(arguments)
        callback = lambda _args: {"success": True, "data": function(None, "", dict(body))}
        result = self.service.execute_legacy(
            self._g2_alias(operation), callback, body, model_ref=None,
            request_id=execution.get("request_id"), session_id=session, effect="inspect",
        )
        result.setdefault("execution", {}).update({
            "operation_id": operation_id,
            "model_ref": None,
            "revision": None,
        })
        return result

    def _invoke_runtime_control(self, operation, function, arguments, execution, operation_id, *, catalog_effect,
                                event_callback=None):
        """Seat-affecting and render operations use distinct permission/budget gates."""
        # Control operations cannot be nested in a model revision. The runtime
        # itself is the subject and a real bounded execution budget is required
        # in the outer daemon envelope before any Worker action is considered.
        if execution.get("model_ref") is not None or arguments.get("model_ref") is not None:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "runtime controls cannot carry model_ref")
        if execution.get("expected_revision") is not None or arguments.get("expected_revision") is not None:
            raise ExecutionContractError("INVALID_REQUEST", "runtime controls cannot carry expected_revision")
        from ._control_daemon import ControlDaemon
        timeouts = ControlDaemon._timeouts(execution)
        required_timeout_fields = ("queue_timeout_s", "execution_timeout_s", "rpc_timeout_s")
        if any(name not in execution for name in required_timeout_fields):
            raise ExecutionContractError(
                "INVALID_REQUEST", "runtime controls require explicit queue, execution and RPC deadlines"
            )
        queue_duration = timeouts.get("queue_timeout_s")
        duration = timeouts.get("execution_timeout_s")
        rpc_duration = timeouts.get("rpc_timeout_s")
        if (queue_duration is None or queue_duration <= 0 or queue_duration > 60
                or duration is None or duration <= 0 or duration > 300
                or rpc_duration is None or rpc_duration < queue_duration + duration
                or rpc_duration > 360):
            raise ExecutionContractError(
                "INVALID_REQUEST", "runtime control deadlines must satisfy queue (0,60], execution (0,300], and RPC >= their sum"
            )
        if self.service is None or self.worker is None:
            raise ExecutionContractError("NOT_CONNECTED", "runtime controls require a connected managed Worker")
        session = execution.get("session_id") or arguments.get("session_id")
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        effect = catalog_effect
        required_permission = {"HOST_CONTROL": "host_control", "COMPUTE": "compute"}.get(effect)
        if required_permission is None:
            raise ExecutionContractError("PERMISSION_DENIED", f"unsupported runtime-control effect: {effect or 'unknown'}")
        if required_permission not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {required_permission}")
        body = dict(arguments)
        if operation == "runtime.license_checkout" and execution.get("authorization_ref") is not None:
            raise ExecutionContractError(
                "INVALID_REQUEST", "license checkout authorization_ref must be supplied in arguments"
            )
        if operation == "runtime.render_probe" and body.get("authorization_ref") is not None:
            raise ExecutionContractError(
                "INVALID_REQUEST", "render authorization_ref must be supplied in the outer execution envelope"
            )
        if not isinstance(body.get("idempotency_key"), str) or not body["idempotency_key"]:
            raise ExecutionContractError("INVALID_REQUEST", f"{operation} requires arguments.idempotency_key")
        if body["idempotency_key"] != execution.get("idempotency_key"):
            raise ExecutionContractError("IDEMPOTENCY_CONFLICT", f"{operation} idempotency key differs from the outer execution envelope")
        body_request_id = body.get("request_id")
        if body_request_id is not None and body_request_id != execution.get("request_id"):
            raise ExecutionContractError("INVALID_REQUEST", f"{operation} request_id differs from the outer execution envelope")
        if operation == "runtime.license_checkout":
            authorization_ref = body.get("authorization_ref")
        else:
            authorization_ref = execution.get("authorization_ref")
        if not isinstance(authorization_ref, str) or not authorization_ref.strip() or len(authorization_ref) > 512:
            raise ExecutionContractError("PERMISSION_DENIED", f"{operation} requires a nonempty authorization reference")
        if any(ord(char) < 32 for char in authorization_ref):
            raise ExecutionContractError("INVALID_REQUEST", "authorization reference contains control characters")
        isolation_proof = self._require_g2_isolation() if effect == "COMPUTE" else None
        # Keep the raw reference in memory only. It is not passed to a Worker
        # command or emitted to the request-event callback.
        authorization_sha256 = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
        body.pop("authorization_ref", None)
        body["authorization_ref_sha256"] = authorization_sha256

        def invoke_control(_args):
            operation_context = {
                "execution": dict(execution), "backend": self,
                "event_callback": event_callback,
            }
            if isolation_proof is not None:
                operation_context["isolation_proof"] = isolation_proof
            data = function(self.worker, "", dict(arguments), **operation_context)
            return {"success": True, "data": data}

        with self.context(operation_id, event_callback):
            result = self.service.execute_legacy(
                self._g2_alias(operation), invoke_control, body, model_ref=None,
                request_id=execution.get("request_id"), session_id=session,
                effect="host_control" if effect == "HOST_CONTROL" else "compute",
            )
        result.setdefault("execution", {}).update({
            "operation_id": operation_id,
            "model_ref": None,
            "revision": None,
        })
        self.persist()
        return result

    def _register_project_artifact(self, arguments, execution, operation_id):
        """Register a project-local file in the existing durable artifact table."""
        from ._g2_registry import validate_call
        from ._artifact_store import (
            local_artifact_host_identity,
            local_engine_host_identity,
            register_project_artifact,
        )

        if not isinstance(arguments, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "artifact.register arguments must be an object")
        validate_call("artifact.register", arguments)
        supplied_project = arguments.get("project_id")
        current_project = execution.get("project_id")
        if not isinstance(current_project, str) or not current_project:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "artifact.register requires execution.project_id")
        if supplied_project != current_project:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "artifact.register project_id differs from its execution envelope")
        body_key = arguments.get("idempotency_key")
        if not isinstance(body_key, str) or not body_key or body_key != execution.get("idempotency_key"):
            raise ExecutionContractError("INVALID_REQUEST", "artifact.register idempotency_key must match the outer execution envelope")
        request_id = execution.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "artifact.register requires execution.request_id")
        if self.service is None:
            raise ExecutionContractError(
                "ENGINE_UNRESPONSIVE",
                "connect to the managed local COMSOL runtime before registering project artifacts",
                safe_retry=True,
                stage="validation",
            )
        if "project_write" not in self.service.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", "artifact.register requires project_write permission")
        host_identity = local_artifact_host_identity()
        engine_host_identity = local_engine_host_identity(self.endpoint_key)
        record = register_project_artifact(
            self.project_root,
            self.store,
            arguments.get("path"),
            project_id=current_project,
            role=arguments.get("role"),
            classification=arguments.get("classification"),
            host_identity=host_identity,
            engine_host_identity=engine_host_identity,
            request_id=request_id,
            registering_operation_id=operation_id,
        )
        return {
            "success": True,
            "data": {
                "artifact_id": record["artifact_id"],
                "sha256": record["sha256"],
                "path": record["path"],
                "size": record["size"],
                "role": record["role"],
                "classification": record["classification"],
                "provenance": record["provenance"],
                "reused": record.get("reused", False),
                "host_scope": "local_loopback_engine_and_current_project",
            },
            "execution": {
                "operation_id": operation_id,
                "project_id": current_project,
                "request_id": request_id,
                "idempotency_key": body_key,
            },
        }

    def _invoke_g2_model(self, operation, arguments, execution, operation_id, event_callback):
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "a connected persistent Worker is required")
        if operation == "checkpoint.diff":
            from ._g2_checkpoint_diff import invoke_diff
            return invoke_diff(self, arguments, execution, operation_id)
        if operation in {"checkpoint.branch", "api.probe"}:
            from ._g2_checkpoint_ops import invoke_owned
            return invoke_owned(self, operation, arguments, execution, operation_id)
        if operation == "geometry.import":
            # The design-catalog body may carry these identity fields for
            # compatibility, but the managed envelope is authoritative. Never
            # let a nested/direct body silently select a different project,
            # session, revision or ModelRef.
            required = ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key")
            missing = [name for name in required if execution.get(name) is None]
            if missing:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "geometry.import requires outer execution identity: " + ", ".join(missing))
            if not isinstance(execution.get("project_id"), str) or not execution["project_id"]:
                raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "geometry.import requires execution.project_id")
            if not isinstance(execution.get("session_id"), str) or not execution["session_id"]:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "geometry.import requires execution.session_id")
            if not isinstance(execution.get("model_ref"), dict):
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "geometry.import requires an outer execution.model_ref object")
            revision = execution.get("expected_revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                raise ExecutionContractError("REVISION_CONFLICT", "geometry.import requires a non-negative execution.expected_revision")
            if not isinstance(execution.get("idempotency_key"), str) or not execution["idempotency_key"]:
                raise ExecutionContractError("INVALID_REQUEST", "geometry.import requires execution.idempotency_key")
            for field in ("project_id", "session_id", "expected_revision", "idempotency_key", "request_id"):
                if field in arguments and field in execution and arguments[field] != execution[field]:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", f"geometry.import {field} differs from the execution envelope")
            supplied_ref = arguments.get("model_ref")
            if supplied_ref is not None:
                outer_ref = execution["model_ref"]
                matches = (dict(supplied_ref) == outer_ref if isinstance(supplied_ref, Mapping)
                           else supplied_ref == outer_ref.get("model_tag") if isinstance(supplied_ref, str)
                           else False)
                if not matches:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "geometry.import model_ref differs from the execution envelope")
        from ._g2_registry import NODE_ACTIONS
        unit_a = operation in NODE_ACTIONS or operation in {"api.describe", "api.invoke"}
        if unit_a:
            # These new capabilities never select a hidden current model or
            # replace the managed identity from a nested/direct body.
            for field in ("project_id", "session_id", "model_ref"):
                if execution.get(field) is None:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", f"{operation} requires outer {field}")
            if not isinstance(execution["model_ref"], dict):
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "outer model_ref must be an object")
            for field in ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"):
                if field in arguments and arguments[field] != execution.get(field):
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", f"{field} differs from the execution envelope")
        session = execution.get("session_id") or arguments.get("session_id")
        if session is not None and session != self.service.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "session mismatch")
        if operation == "model.adopt":
            # The design-catalog spelling is intentionally adapted at this
            # boundary; the legacy adoption function continues to receive its
            # historical ``model_tag`` name and keeps its project_write gate.
            tag = arguments.get("server_model_tag")
            if not isinstance(tag, str) or not tag.strip():
                raise ExecutionContractError("INVALID_REQUEST", "server_model_tag must be a non-empty string")
            return self.adopt(tag, project_id=execution.get("project_id"))
        ref_mapping = execution.get("model_ref") or arguments.get("model_ref")
        if not isinstance(ref_mapping, dict):
            from ._server import session_server as srv
            cur = getattr(srv, "_current_model", None)
            cur_tag = None
            if cur is not None:
                tag_attr = getattr(cur, "tag", None)
                if isinstance(tag_attr, str):
                    cur_tag = tag_attr
                elif callable(tag_attr):
                    try:
                        cur_tag = tag_attr()
                    except Exception:
                        pass
                if not cur_tag and hasattr(cur, "java"):
                    j_tag = getattr(cur.java, "tag", None)
                    if isinstance(j_tag, str):
                        cur_tag = j_tag
                    elif callable(j_tag):
                        try:
                            cur_tag = j_tag()
                        except Exception:
                            pass

            active_models = [m for m in self.service.ledger._models.values() if not m.retired]
            client_models = []
            if self.worker is not None and hasattr(self.worker, "client"):
                try:
                    client_models = self.worker.client().models()
                except Exception:
                    client_models = []

            # 1. Check cur_tag first! If cur_tag is a valid string corresponding to an active model, use it.
            if isinstance(cur_tag, str) and cur_tag:
                if cur_tag not in self.service.ledger._models:
                    self.service.ledger.bind_model(cur_tag)
                ref_mapping = self.service.ledger._models[cur_tag].ref.as_dict()
            # 2. validate.report does not strictly require a model tag. Allow it to execute with ref_mapping=None.
            elif operation in {"validate.report", "validate_report"}:
                if len(active_models) == 1:
                    ref_mapping = active_models[0].ref.as_dict()
                elif len(client_models) == 1:
                    tag = str(client_models[0].tag()) if callable(getattr(client_models[0], "tag", None)) else str(getattr(client_models[0], "name", lambda: "m1")())
                    if tag not in self.service.ledger._models:
                        self.service.ledger.bind_model(tag)
                    ref_mapping = self.service.ledger._models[tag].ref.as_dict()
                else:
                    ref_mapping = None
            # 3. Multiple models without binding or unambiguous cur_tag must fail with AMBIGUOUS_TARGET
            elif len(active_models) > 1 or len(client_models) > 1:
                raise ExecutionContractError(
                    "AMBIGUOUS_TARGET",
                    "Multiple models loaded on server without explicit model_ref; target is ambiguous"
                )
            # 4. Single model fallbacks
            elif len(active_models) == 1:
                ref_mapping = active_models[0].ref.as_dict()
            elif len(client_models) == 1:
                tag = str(client_models[0].tag()) if callable(getattr(client_models[0], "tag", None)) else str(getattr(client_models[0], "name", lambda: "m1")())
                if tag not in self.service.ledger._models:
                    self.service.ledger.bind_model(tag)
                ref_mapping = self.service.ledger._models[tag].ref.as_dict()
            else:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model_ref is required for this operation")
        ref = model_ref_from_mapping(ref_mapping) if isinstance(ref_mapping, dict) else None
        bound_revision = self.service.ledger._state_for(ref).revision if ref is not None else None
        body = self._g2_body(arguments, operation)
        alias = self._g2_alias(operation)
        prepared_a = None
        resolved_a_effect = None
        if unit_a:
            from ._execution_contract import permission_for_effect
            from ._g2_public_api import legacy_effect, provisional_effect, prepare_invoke
            from ._g2_engine import prepare_node_action
            resolved_a_effect = (legacy_effect(provisional_effect(body)) if operation == "api.invoke"
                                 else "inspect" if operation in {"api.describe", "node.selection_get"} else "project_write")
            required_permission = permission_for_effect(resolved_a_effect)
            if required_permission not in self.service.ledger.permissions:
                raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {required_permission}")
            revision = execution.get("expected_revision")
            if revision is not None and (type(revision) is not int or revision != bound_revision):
                raise ExecutionContractError("REVISION_CONFLICT", "Unit A expected_revision differs from the bound source")
            if resolved_a_effect != "inspect" and (revision is None or not isinstance(execution.get("idempotency_key"), str) or not execution["idempotency_key"]):
                raise ExecutionContractError("INVALID_REQUEST", "Unit A write requires revision and idempotency key")
            if resolved_a_effect != "inspect" and self.service.ledger._state_for(ref).dirty:
                # A retained owned-copy UNKNOWN epoch is evidence, not a
                # writable capability. Refuse before receiver/Worker access.
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "bound model is dirty; write requires verified reconciliation")
            if operation == "api.invoke":
                prepared_a = prepare_invoke(self.worker, ref.model_tag, body)
                prepared_a["model_ref"] = ref.as_dict()
                if prepared_a["effect"] != resolved_a_effect:
                    raise ExecutionContractError("PERMISSION_DENIED", "actual capability effect differs from provisional policy")
            elif operation in NODE_ACTIONS:
                prepared_a = prepare_node_action(self.worker, ref.model_tag, operation, body, model_revision=bound_revision)

        if operation == "model.inspect":
            if "inspect" not in self.service.ledger.permissions:
                raise ExecutionContractError("PERMISSION_DENIED", "permission required: inspect")
            supplied_revision = execution.get("expected_revision")
            if supplied_revision is not None and supplied_revision != bound_revision:
                raise ExecutionContractError("REVISION_CONFLICT", "model.inspect expected_revision does not match the bound model")
            identity = self.service.inspect(ref)
            model = self.worker.client().model(ref.model_tag)
            java = model.java
            detail = arguments.get("detail", "summary")
            from ._model_ops import _model_tree_data
            try:
                tree = _model_tree_data(model)
            except Exception as exc:
                raise ExecutionContractError(
                    "MODEL_INSPECTION_UNAVAILABLE",
                    "COMSOL model structure readback failed",
                    safe_retry=True,
                    details={"cause_type": type(exc).__name__},
                ) from exc
            structure = {
                "components": tree.get("components", []),
                "component_details": tree.get("component_details", []),
                "parameters": tree.get("parameters", []),
                "studies": tree.get("studies", []),
                "solutions": tree.get("solutions", []),
                "datasets": tree.get("datasets", []),
                "results": tree.get("results", []),
            }
            dependency_inventory = {"status": "UNAVAILABLE", "file_resource_count": None, "file_resource_tags": [],
                                    "external_dependencies": "NOT_EXHAUSTIVELY_ENUMERATED"}
            try:
                file_tags = [str(tag) for tag in java.getFileResourceTags()]
                dependency_inventory = {
                    "status": "SCOPED_PUBLIC_API_INVENTORY",
                    "file_resource_count": len(file_tags),
                    "file_resource_tags": file_tags,
                    "scope": "Model.FileResourceList entries referenced by model features; COMSOL documents these file resources as stored in the MPH archive.",
                    "external_dependencies": "NOT_EXHAUSTIVELY_ENUMERATED",
                    "limitations": ["linked runtime libraries, external services and dependency paths outside Model.FileResourceList were not enumerated by this action"],
                }
            except Exception as exc:
                dependency_inventory = {"status": "UNAVAILABLE", "file_resource_count": None, "file_resource_tags": [],
                                        "external_dependencies": "NOT_EXHAUSTIVELY_ENUMERATED",
                                        "error_type": type(exc).__name__}
            try:
                version = str(java.getComsolVersion())
            except Exception:
                version = None
            computation = {}
            for key, method_name in (("last_computation_time", "getLastComputationTime"),
                                     ("last_computation_date", "getLastComputationDate"),
                                     ("last_computation_version", "getLastComputationVersion")):
                try:
                    value = getattr(java, method_name)()
                    computation[key] = None if value is None else str(value)
                except Exception:
                    computation[key] = None
            data = {
                "model_identity": identity.get("execution", {}).get("model_ref", ref.as_dict()),
                "revision": identity.get("execution", {}).get("revision", bound_revision),
                "dirty": identity.get("execution", {}).get("dirty", False),
                "structure": structure if detail in {"summary", "structure"} else None,
                "dependency_inventory": dependency_inventory if detail in {"summary", "dependencies"} else None,
                "comsol_version": version,
                "last_computation": computation,
                "detail": detail,
                "file_path": "OMITTED",
            }
            if detail == "summary":
                data["structure_counts"] = {name: len(structure[name]) for name in
                                             ("components", "parameters", "studies", "solutions", "datasets", "results")}
            return {"success": True, "data": data, "execution": identity.get("execution", {})}

        if operation not in _g3_operations():
            permission = permission_for_effect(resolved_a_effect) if unit_a else permission_for_legacy_tool(alias)
            if permission != "inspect" and ref is not None:
                supplied_rev = execution.get("expected_revision")
                if supplied_rev is None:
                    supplied_rev = execution.get("revision")
                if supplied_rev is None:
                    supplied_rev = arguments.get("expected_revision")
                if supplied_rev is None:
                    supplied_rev = arguments.get("revision")
                if supplied_rev is not None and supplied_rev != bound_revision:
                    raise ExecutionContractError(
                        "REVISION_CONFLICT",
                        f"requested revision {supplied_rev} does not match managed revision {bound_revision}"
                    )
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
            result = self._invoke_g3_model(operation, ref, body, execution, operation_id, session)
            mesh_snapshot_mode = self._stage_mesh_snapshot_context.get(None)
            if (operation == "mesh.inspect" and isinstance(mesh_snapshot_mode, dict)
                    and isinstance(result, Mapping) and result.get("success") is True):
                # The legacy inspect path intentionally has no write ticket or
                # ticket request hash.  Bind the private producer's exact
                # request/operation correlation to the actual outer request
                # after that same request ID has been passed through
                # ExecutionService.execute_legacy.  Never synthesize a hash.
                execution_result = result.get("execution") if isinstance(result, Mapping) else None
                request_id = execution.get("request_id")
                if (not isinstance(execution_result, Mapping)
                        or not isinstance(request_id, str) or not request_id
                        or execution_result.get("model_ref") != ref.as_dict()
                        or execution_result.get("session_id") != session
                        or execution_result.get("revision") != execution.get("expected_revision")
                        or execution_result.get("dirty") is not False
                        or execution_result.get("project_id") not in (None, execution.get("project_id"))
                        or execution_result.get("request_id") not in (None, request_id)
                        or execution_result.get("operation_id") not in (None, operation_id)
                        or "request_hash" in execution_result):
                    raise ExecutionContractError(
                        "MODEL_IDENTITY_MISMATCH",
                        "private mesh READ response does not preserve its exact managed identity",
                        stage="post_dispatch",
                    )
                enriched = dict(execution_result)
                enriched.update({
                    "project_id": execution.get("project_id"),
                    "request_id": request_id,
                    "operation_id": operation_id,
                    "request_hash_status": "NOT_APPLICABLE_READ_NO_WRITE_TICKET",
                })
                result = {**dict(result), "execution": enriched}
            return result
        if (unit_a and resolved_a_effect != "inspect") or operation in {"node.property_set", "node.property_index_set", "node.property_entry_set",
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
        if unit_a:
            from ._g2_public_api import execute_prepared, describe_api
            from ._g2_engine import execute_node_action
            def callback(_args):
                if operation == "api.invoke":
                    call = lambda: execute_prepared(self.worker, prepared_a)
                elif operation == "api.describe":
                    call = lambda: describe_api(self.worker, ref.model_tag, body)
                else:
                    call = lambda: execute_node_action(self.worker, ref.model_tag, operation, prepared_a, model_ref=ref.as_dict())
                return self._dispatch_with_witness(operation, call, effect=resolved_a_effect)
        else:
            callback = self._g2_callback(operation, ref, body, operation_id, bound_revision)
        result = self.service.execute_legacy(alias, callback, body, model_ref=ref,
                expected_revision=execution.get("expected_revision", arguments.get("expected_revision")),
                request_id=execution.get("request_id"), session_id=session, effect=resolved_a_effect if unit_a else {
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

    @staticmethod
    def _resume_model_readback(model: Any) -> dict[str, Any]:
        """Read the stable, public model subset used to verify a saved restart.

        This is deliberately not described as a complete COMSOL model hash:
        it binds the structural tags and global parameter expressions which
        the offline API adapter can reliably enumerate, plus solution and
        file-resource inventories which gate the initial allowlist.
        """
        from ._model_ops import _model_tree_data, _parameter_rows

        try:
            tree = _model_tree_data(model)
            java = model.java
            file_tags = [str(tag) for tag in java.getFileResourceTags()]
            parameters = sorted(_parameter_rows(model), key=lambda row: row["name"])
        except Exception as exc:
            raise ExecutionContractError(
                "RESUME_READBACK_UNAVAILABLE",
                "COMSOL could not read the structural, parameter, solution and file-resource state needed for restart",
                safe_retry=True,
                details={"cause_type": type(exc).__name__},
            ) from exc

        component_details = []
        for component in tree.get("component_details", []):
            normalized = {"tag": str(component.get("tag", ""))}
            for field in ("geometries", "meshes", "physics", "materials"):
                normalized[field] = sorted(str(item) for item in component.get(field, []))
            component_details.append(normalized)
        component_details.sort(key=lambda row: row["tag"])
        stable = {
            "components": sorted(str(item) for item in tree.get("components", [])),
            "component_details": component_details,
            "parameters": parameters,
            "studies": sorted(str(item) for item in tree.get("studies", [])),
            "solutions": sorted(str(item) for item in tree.get("solutions", [])),
            "datasets": sorted(str(item) for item in tree.get("datasets", [])),
            "results": sorted(str(item) for item in tree.get("results", [])),
            "file_resource_tags": sorted(file_tags),
        }
        signature = hashlib.sha256(
            json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {"signature": signature, "scope": "structure+global_parameters+solutions+Model.FileResourceList",
                "state": stable}

    def _preflight_study_resume(self, ref, body, execution, operation_id):
        """Freeze a trustworthy source contract before the opted-in study runs."""
        if ref is None or body.get("recovery_policy") != {"mode": "restart_from_checkpoint"}:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "restart policy requires a bound study.run request")
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "a connected persistent Worker is required")
        if not {"compute", "project_write"}.issubset(self.service.ledger.permissions):
            raise ExecutionContractError("PERMISSION_DENIED", "restart snapshots require compute and project_write")
        supplied_revision = execution.get("expected_revision")
        state = self.service.ledger._state_for(ref)
        if isinstance(supplied_revision, bool) or supplied_revision != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "study.run recovery policy requires the current managed revision")

        # Observe through the existing fingerprint/event adapter before taking
        # the save.  A dirty or externally changed source has no unambiguous
        # pre-run boundary for this first allowlist entry.
        self.service.inspect(ref)
        state = self.service.ledger._state_for(ref)
        if state.dirty or state.external_event_counter != state.observed_external_event_counter:
            raise ExecutionContractError("REVISION_CONFLICT", "study.run restart snapshot requires a clean, reconciled source model")
        source_readback = self._resume_model_readback(self.worker.client().model(ref.model_tag))
        model_state = source_readback["state"]
        if model_state["solutions"]:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "restart allowlist excludes models with pre-existing solution tags")
        if model_state["file_resource_tags"]:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "restart allowlist excludes Model.FileResourceList dependencies")

        operation = self.store.get_operation(operation_id)
        job = self.store.operation_job(operation_id)
        if not operation or not job:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "study.run durable operation/job binding is unavailable")
        outer_arguments = operation.get("metadata", {}).get("arguments")
        if operation.get("operation") in {"registry_call", "operation_call"}:
            if (not isinstance(outer_arguments, dict) or outer_arguments.get("operation_id") != "study.run"
                    or not isinstance(outer_arguments.get("arguments"), dict)):
                raise ExecutionContractError("RESUME_NOT_SUPPORTED", "only canonical study.run fallback requests can be restarted")
            original_arguments = dict(outer_arguments["arguments"])
        elif operation.get("operation") == "study.run" and isinstance(outer_arguments, dict):
            original_arguments = dict(outer_arguments)
        else:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "only direct or canonical registry study.run requests can be restarted")
        if original_arguments != body:
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "durable study.run arguments differ from dispatched arguments")

        return {
            "schema_version": 1,
            "source_job_id": job["job_id"],
            "source_operation_id": operation_id,
            "source_outer_operation": operation["operation"],
            "source_outer_request_hash": operation["request_hash"],
            "source_model_ref": ref.as_dict(),
            "source_revision": state.revision,
            "source_fingerprint": state.fingerprint,
            "source_external_event_counter": state.external_event_counter,
            "source_model_readback": source_readback,
            "source_solution_tags": [],
            "file_resource_tags": [],
            "original_arguments": original_arguments,
            "run_arguments": {name: value for name, value in original_arguments.items() if name != "recovery_policy"},
        }

    def _commit_study_resume_snapshot(self, ref, body, operation_id, preflight):
        """Save and durably bind the opt-in pre-run checkpoint before Compute."""
        from ._g2_engine import create_checkpoint

        source_job_id = preflight["source_job_id"]
        root = self.project_root / "g2_artifacts" / "checkpoints"
        destination = root / f"resume-before-study-{source_job_id}.mph"
        checkpoint = create_checkpoint(
            self.worker, ref.model_tag, destination,
            f"resume-before-study-{source_job_id}", include_solution=False,
        )
        # The save must not have changed the state subset that will later be
        # read back from a separately loaded native model.
        saved_readback = self._resume_model_readback(self.worker.client().model(ref.model_tag))
        if saved_readback["signature"] != preflight["source_model_readback"]["signature"]:
            raise ExecutionContractError("RESUME_SNAPSHOT_CHANGED_SOURCE", "pre-run checkpoint save changed the allowlisted model readback")

        state = self.service.ledger._state_for(ref)
        checkpoint = self._bind_checkpoint_metadata(ref, checkpoint, state=state)
        checkpoint.update({
            "resume_source_job_id": source_job_id,
            "resume_source_operation_id": operation_id,
            "resume_source_readback_signature": preflight["source_model_readback"]["signature"],
            "resume_source_readback_scope": preflight["source_model_readback"]["scope"],
            "resume_restart_mode": "restart_from_checkpoint",
        })
        self.store.persist_checkpoint(checkpoint["checkpoint_id"], checkpoint)
        contract = {
            **preflight,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_sha256": checkpoint["sha256"],
            "checkpoint_path": checkpoint["path"],
            "checkpoint_restore_scope": checkpoint["restore_scope"],
        }
        self.store.update_job(source_job_id, "RUNNING", {"resume_contract": contract})
        self.store.add_event(source_job_id, "ResumeCheckpointCreated", {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "sha256": checkpoint["sha256"],
            "path": checkpoint["path"],
            "source_readback_signature": preflight["source_model_readback"]["signature"],
            "source_readback_scope": preflight["source_model_readback"]["scope"],
            "restart_mode": "restart_from_checkpoint",
        })
        return checkpoint

    def resume_study_run(self, context, arguments, execution, operation_id, event_callback):
        """Load a bound pre-run snapshot into a fresh tag, then rerun study.run.

        The original ModelRef, current model pointer and source failure record
        stay intact.  This path restarts the full study operation from the
        snapshot; COMSOL solver-internal iteration continuation is unsupported.
        """
        if self.service is None or self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connect to the existing Worker before resume")
        contract = context.get("resume_contract") if isinstance(context, Mapping) else None
        if not isinstance(contract, Mapping):
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "durable resume contract is unavailable")
        source_ref = model_ref_from_mapping(dict(contract["source_model_ref"]))
        if execution.get("model_ref") != source_ref.as_dict():
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "resume source ModelRef changed after admission")
        if execution.get("session_id") != source_ref.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "resume session changed after admission")
        with self.context(operation_id, event_callback):
            self.service.inspect(source_ref)
        source_state = self.service.ledger._state_for(source_ref)
        requested_revision = execution.get("expected_revision")
        if isinstance(requested_revision, bool) or requested_revision != source_state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "source model revision changed while resume was queued")
        if (source_state.dirty or source_state.external_event_counter != source_state.observed_external_event_counter) and not context.get("authorization_ref_present"):
            raise ExecutionContractError("REVISION_CONFLICT", "dirty source state requires the admitted explicit authorization_ref")

        checkpoint_id = contract.get("checkpoint_id")
        expected_readback = contract.get("source_model_readback")
        if (not isinstance(expected_readback, Mapping) or not isinstance(expected_readback.get("signature"), str)
                or len(expected_readback["signature"]) != 64):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "source snapshot readback signature is malformed")
        checkpoint = next((row for row in self.store.list_metadata("checkpoints")
                           if row.get("checkpoint_id") == checkpoint_id), None)
        binding = checkpoint.get("source_binding") if isinstance(checkpoint, Mapping) else None
        if (not isinstance(checkpoint, Mapping) or not isinstance(binding, Mapping)
                or checkpoint.get("sha256") != contract.get("checkpoint_sha256")
                or checkpoint.get("path") != contract.get("checkpoint_path")
                or checkpoint.get("resume_source_job_id") != context.get("source_job_id")
                or checkpoint.get("resume_source_operation_id") != contract.get("source_operation_id")
                or checkpoint.get("resume_source_readback_signature") != expected_readback.get("signature")
                or binding.get("model_ref") != source_ref.as_dict()
                or binding.get("revision") != contract.get("source_revision")
                or binding.get("fingerprint") != contract.get("source_fingerprint")
                or binding.get("external_event_counter") != contract.get("source_external_event_counter")
                or checkpoint.get("restore_scope") != contract.get("checkpoint_restore_scope")):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "durable restart checkpoint changed after admission")
        path = canonical_project_path(self.project_root, checkpoint.get("path", ""))
        if not path.is_file():
            raise ExecutionContractError("ARTIFACT_MISSING", "restart checkpoint file is unavailable")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != contract.get("checkpoint_sha256"):
            raise ExecutionContractError("ARTIFACT_MISSING", "restart checkpoint SHA-256 changed before load")

        isolation = self._require_g2_isolation()
        client = self.worker.client()
        new_tag = "mcp_resume_" + uuid4().hex[:20]
        try:
            existing_tags = {str(tag) for tag in client.tags()}
        except Exception as exc:
            raise ExecutionContractError("RESUME_READBACK_UNAVAILABLE", "COMSOL could not verify a fresh target model tag",
                                         safe_retry=True, details={"cause_type": type(exc).__name__}) from exc
        if new_tag in existing_tags:
            raise ExecutionContractError("ENGINE_BUSY", "fresh resume model tag collided; no model was loaded")
        try:
            self.store.add_event(context.get("source_job_id", ""), "ResumeSnapshotLoadStarted", {
                "continuation_operation_id": operation_id, "checkpoint_id": checkpoint_id,
                "target_tag": new_tag,
            })
            with self.context(operation_id, event_callback):
                loaded = client.load(path, tag=new_tag)
                readback = self._resume_model_readback(loaded)
            if (not isinstance(expected_readback, Mapping)
                    or readback.get("signature") != expected_readback.get("signature")
                    or readback.get("state", {}).get("solutions") != []
                    or readback.get("state", {}).get("file_resource_tags") != []):
                raise ExecutionContractError("RESUME_SNAPSHOT_READBACK_MISMATCH", "loaded checkpoint did not reproduce the pre-run allowlisted model state")
            with self.context(operation_id, event_callback):
                binding = self.service.bind_model(new_tag, ownership="mcp_owned")
            new_ref = model_ref_from_mapping(binding["execution"]["model_ref"])
        except Exception as exc:
            cleanup_error = None
            try:
                with self.context(operation_id, event_callback):
                    if new_tag in {str(tag) for tag in client.tags()}:
                        client.remove(new_tag)
            except Exception as cleanup_exc:  # preserve the original failure and flag uncertain cleanup
                cleanup_error = cleanup_exc
            if cleanup_error is not None:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "restart snapshot load failed and the owned fresh model could not be removed",
                    details={"load_cause_type": type(exc).__name__, "cleanup_cause_type": type(cleanup_error).__name__,
                             "target_tag": new_tag},
                ) from exc
            if isinstance(exc, ExecutionContractError):
                raise
            raise ExecutionContractError("RESUME_SNAPSHOT_LOAD_FAILED", "COMSOL could not load and verify the restart snapshot",
                                        safe_retry=True, details={"cause_type": type(exc).__name__}) from exc

        new_execution = dict(execution)
        new_execution.update({"model_ref": new_ref.as_dict(), "expected_revision": 0,
                              "session_id": new_ref.session_id})
        child_job = self.store.operation_job(operation_id)
        if child_job:
            self.store.update_job(child_job["job_id"], child_job["status"], {
                "source_model_ref": source_ref.as_dict(),
                "resume_target_model_ref": new_ref.as_dict(),
                "resume_snapshot_readback_signature": readback["signature"],
                "resume_snapshot_readback_scope": readback["scope"],
            })
        self.store.add_event(context.get("source_job_id", ""), "ResumeSnapshotLoaded", {
            "continuation_operation_id": operation_id,
            "checkpoint_id": checkpoint_id,
            "target_model_ref": new_ref.as_dict(),
            "readback_signature": readback["signature"],
            "readback_scope": readback["scope"],
            "solution_continuation": False,
        })
        # Keep the resumed Study operation's Worker requests on the durable
        # continuation job.  The snapshot load/readback above use the same
        # context; omitting it here loses run() submit/observe events and makes
        # a later UNKNOWN continuation impossible to reconcile safely.
        with self.context(operation_id, event_callback):
            result = self._invoke_g3_model("study.run", new_ref, dict(arguments), new_execution,
                                           operation_id, new_ref.session_id)
        result.setdefault("data", {}).update({
            "resume": {
                "source_job_id": context.get("source_job_id"),
                "checkpoint_id": checkpoint_id,
                "old_model_ref": source_ref.as_dict(),
                "new_model_ref": new_ref.as_dict(),
                "new_model_selected": False,
                "restart_mode": "restart_from_checkpoint",
                "solution_continuation": False,
                "snapshot_readback": {"status": "VERIFIED", "signature": readback["signature"],
                                      "scope": readback["scope"]},
            },
            "isolation_proof": isolation,
        })
        return result

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
        native_ticket_context = self._stage_native_context.get(None)
        catalog_effect = str(EFFECTS.get(operation, "")).upper()
        private_xmesh_ticket = False
        if (isinstance(native_ticket_context, Mapping)
                and native_ticket_context.get("variables_xmesh_ticket") is True):
            exact_path = native_ticket_context.get("variables_feature_path")
            if (operation != "solver.inspect"
                    or native_ticket_context.get("purpose") != "initial_stage_output"
                    or native_ticket_context.get("readphase") != "initial-stage-active-variables-xmesh"
                    or not isinstance(exact_path, Mapping)
                    or body.get("path") != exact_path
                    or execution.get("expected_revision") != native_ticket_context.get("expected_revision")
                    or execution.get("expected_revision") != native_ticket_context.get("solve_revision")):
                raise ExecutionContractError(
                    "PERMISSION_DENIED",
                    "private Variables.xmeshInfo ticket requires its exact path and current solve revision",
                    stage="pre_dispatch",
                )
            # This is the server-owned initial-stage ephemeral xmesh lifecycle.
            # It reuses the registered solver.inspect implementation while
            # requiring project_write authority and a real EVALUATE ticket.
            catalog_effect = "EVALUATE"
            private_xmesh_ticket = True
        effect = _G3_EFFECT_MAP.get(catalog_effect)
        if effect is None:
            raise ExecutionContractError("PERMISSION_DENIED", f"unclassified G3 effect for operation {operation}")
        isolation = (
            self._require_g2_isolation()
            if (operation in REQUIRES_ISOLATION and effect in {"evaluate", "project_write", "compute", "state_write", "trusted_code"})
            else None
        )
        alias = ("w21_initial_variables_xmesh" if private_xmesh_ticket
                 else self._g2_alias(operation))

        registered_import = None
        import_body = body
        if operation == "geometry.import":
            from ._artifact_store import (
                local_artifact_host_identity,
                local_engine_host_identity,
                resolve_registered_artifact,
            )

            host_identity = local_artifact_host_identity()
            engine_host_identity = local_engine_host_identity(self.endpoint_key)
            registered_import = resolve_registered_artifact(
                self.project_root,
                self.store,
                body.get("artifact_id"),
                project_id=execution.get("project_id"),
                current_host_identity=host_identity,
                current_engine_host_identity=engine_host_identity,
                current_server_instance_id=ref.server_instance_id if ref is not None else "",
            )
            if registered_import.get("role") not in {"geometry_source", "cad_geometry"}:
                raise ExecutionContractError(
                    "ARTIFACT_ROLE_MISMATCH",
                    "geometry.import requires a registered geometry_source or cad_geometry artifact",
                )
            import_body = dict(body)
            options = import_body.get("options") or {}
            if not isinstance(options, Mapping):
                raise ExecutionContractError("INVALID_REQUEST", "geometry.import options must be an object")
            import_options = dict(options)
            # A caller-supplied local_path must never turn this adapter into an
            # arbitrary path probe.  The only engine filename and local probe
            # are the digest-verified project-managed copy.
            import_options["path_check"] = "local"
            import_options["local_path"] = str(registered_import["path"])
            import_body["artifact_id"] = str(registered_import["path"])
            import_body["options"] = import_options

        resume_preflight = None
        if operation == "study.run" and "recovery_policy" in body:
            resume_preflight = self._preflight_study_resume(ref, body, execution, operation_id)

        model_tag = ref.model_tag if ref is not None else ""
        def callback(_args: dict[str, Any]) -> dict[str, Any]:
            from ._observation_store import observation_context, register_observation
            def invoke_domain():
                domain_body = dict(body)
                checkpoint_info = None
                if resume_preflight is not None:
                    checkpoint_info = self._commit_study_resume_snapshot(
                        ref, domain_body, operation_id, resume_preflight,
                    )
                    domain_body.pop("recovery_policy", None)
                if registered_import is not None:
                    domain_body = dict(import_body)
                output_mode = self._stage_output_mode_context.get(None)
                native_context = self._stage_native_context.get(None)
                mesh_snapshot_mode = self._stage_mesh_snapshot_context.get(None)
                if operation == "result.evaluate" and output_mode == "strict_field_readback":
                    if (isinstance(native_context, Mapping)
                            and native_context.get("purpose") == "initial_stage_output"):
                        data = function(
                            self.worker, model_tag, domain_body,
                            strict_field_readback=True,
                            _w21_initial_output_context=dict(native_context),
                        )
                    else:
                        data = function(self.worker, model_tag, domain_body, strict_field_readback=True)
                elif operation == "result.evaluate" and output_mode == "strict_metric_evidence":
                    data = function(self.worker, model_tag, domain_body, strict_metric_evidence=True)
                elif (operation in {"study.inspect", "solver.inspect"}
                      and isinstance(native_context, Mapping)
                      and native_context.get("purpose") in {
                          "initial_stage_admission", "initial_stage_output"}):
                    data = function(
                        self.worker, model_tag, domain_body,
                        _w21_initial_stage_context=dict(native_context),
                    )
                elif (operation == "dataset.solution_indices"
                      and isinstance(native_context, Mapping)
                      and native_context.get("purpose") == "initial_stage_output"):
                    data = function(
                        self.worker, model_tag, domain_body,
                        _w21_initial_stage_context=dict(native_context),
                    )
                elif operation == "mesh.inspect" and isinstance(mesh_snapshot_mode, dict):
                    data = function(self.worker, model_tag, domain_body,
                                    _strict_snapshot_mode=mesh_snapshot_mode)
                else:
                    data = function(self.worker, model_tag, domain_body)
                if registered_import is not None and isinstance(data, dict):
                    # The public identity remains the registered digest.  Do
                    # not return the absolute Worker-visible filename from the
                    # legacy geometry callback.
                    data["artifact_id"] = registered_import["artifact_id"]
                    data["artifact_path_verbatim"] = False
                    data["artifact_registration"] = {
                        "sha256": registered_import["sha256"],
                        "path": registered_import["relative_path"],
                        "size": registered_import["size"],
                        "role": registered_import["role"],
                        "classification": registered_import["classification"],
                        "record_schema_version": registered_import["record_schema_version"],
                        "engine_host_scope": "verified_local_loopback",
                    }
                    if isinstance(data.get("local_probe"), Mapping):
                        data["local_probe"] = {"is_file": bool(data["local_probe"].get("is_file")),
                                                "registered_artifact": True}
                if checkpoint_info is not None and isinstance(data, dict):
                    data["resume_checkpoint"] = {
                        "checkpoint_id": checkpoint_info["checkpoint_id"],
                        "sha256": checkpoint_info["sha256"],
                        "restart_mode": "restart_from_checkpoint",
                        "solution_continuation": False,
                    }
                if operation in {"result.at_points", "result.evaluate"} and data.get("field_array"):
                    data["observation_ref"] = register_observation(self.worker, model_tag, data)
                return data
            # EVALUATE calls do not advance the model revision; mutations do.
            revision = self.service.ledger._state_for(ref).revision if ref else None
            with observation_context(self.store, ref.as_dict() if ref else None,
                                     revision, operation_id, project_id=execution.get("project_id")):
                return self._dispatch_with_witness(operation, invoke_domain, effect=effect)

        supplied_rev = execution.get("expected_revision")
        if supplied_rev is None:
            supplied_rev = execution.get("revision")
        if supplied_rev is None:
            supplied_rev = body.get("expected_revision")
        if supplied_rev is None:
            supplied_rev = body.get("revision")

        if ref is not None:
            current_rev = self.service.ledger._state_for(ref).revision
            if supplied_rev is not None:
                if supplied_rev != current_rev:
                    raise ExecutionContractError(
                        "REVISION_CONFLICT",
                        f"requested revision {supplied_rev} does not match managed revision {current_rev}"
                    )
                expected_rev = supplied_rev
            else:
                if effect == "inspect":
                    expected_rev = None
                else:
                    state = self.service.ledger._state_for(ref)
                    if state.dirty or state.external_event_counter != state.observed_external_event_counter:
                        raise ExecutionContractError("REVISION_CONFLICT", "model has external changes or is dirty; reconcile before evaluating")
                    expected_rev = current_rev
        else:
            if supplied_rev is not None:
                raise ExecutionContractError("INVALID_REQUEST", "unbound operation cannot carry expected_revision")
            expected_rev = None
        result = self.service.execute_legacy(alias, callback, body, model_ref=ref,
                expected_revision=expected_rev,
                request_id=execution.get("request_id"), session_id=session, effect=effect)
        if result.get("success") and ref is not None:
            # Bind observations minted inside this serial operation to its
            # final revision, after the service has accounted for mutations.
            final_revision = self.service.ledger._state_for(ref).revision
            for record in self.store.list_metadata("artifacts"):
                if record.get("kind") == "w17_observation" and record.get("producer") == operation_id:
                    record["sample_revision"] = record["revision"]
                    record["revision"] = final_revision
                    self.store.persist_artifact(record["observation_id"], record)
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
