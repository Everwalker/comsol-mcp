"""Durable job control with one serial engine queue and responsive cached reads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager, nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import threading
import time
import traceback
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError, canonical_request_hash
from ._managed_backend import ManagedBackend, ProcessLock, SessionConnectFailure, collect_legacy_registry, _g3_operations
from ._operation_store import (
    IdempotencyConflict, JobCleanupError, OperationStore,
    session_recovery_evidence_sha256,
)
from ._platform_process import process_identity, terminate_process_tree
from ._desktop_platforms import create_native_metadata_adapter
from ._desktop_service import DesktopCoordinator, DesktopOperationError
from ._project_authority import ProjectAuthority, PROJECT_OPERATIONS
from ._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionEndpointIdentity,
    SessionContextMissing,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    session_state_directory,
    SessionEndpointScheduler,
    SessionBindingBusy,
    SessionRuntimeRegistry,
    SessionSchedulerClosed,
    active_session_context,
    use_session_context,
)
from ._session_server import ManagedServerHandle, OwnedServerError, OwnedServerLauncher
from ._session_lifecycle import (
    SessionLifecycleStore,
    SessionLifecycleProjectConflict,
    new_lifecycle_record,
)

TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED", "LOST", "CANCELLED"}
CONTROL_READS = {
    "session_health", "server_info", "job_status", "job_log", "job_result", "job_reconcile",
    "job_list", "job_wait", "job_cancel",
    "job.status", "job.log", "job.result", "job.reconcile",
    "job.list", "job.wait", "job.cancel",
    "run_study_status", "visible_main_workflow_status",
    "registry_list", "registry_describe", "registry_search", "registry_manifest", "operation_describe",
    "docs_search", "docs_get", "docs_examples", "docs_error_search", "checkpoint_list", "checkpoint_inspect", "checkpoint_diff",
}
CACHED_JOB_ACTIONS = frozenset({
    "job.list", "job.status", "job.log", "job.result", "job.reconcile", "job.wait", "job.cancel", "job.cleanup",
})
DESKTOP_OPERATIONS = frozenset({
    "desktop.status", "desktop.bind", "desktop.show_model", "desktop.select_node",
    "desktop.capture", "desktop.action", "desktop.shell_execute", "desktop.migrate_standalone",
})
SESSION_OPERATIONS = frozenset({
    "session.list", "session.connect", "session.start", "session.inspect",
    "session.reconnect", "session.disconnect", "session.stop", "session.health",
    "session.recover",
})

_PRESERVE_SERVER_PROCESS_IDENTITY = object()
SESSION_ALIASES = {f"session_{name}": f"session.{name}" for name in (
    "list", "connect", "start", "inspect", "reconnect", "disconnect", "stop", "recover",
)}


class WorkerRetirementRefused(RuntimeError):
    """The exact daemon Worker cannot yet enter a retirement fence."""

    def __init__(self, message: str, *, readback: dict[str, Any] | None = None):
        super().__init__(message)
        self.readback = readback


class WorkerRetirementLease:
    """Admission fence held while the caller verifies/stops one exact Worker."""

    def __init__(self, daemon, worker, identity, process, readback):
        self._daemon = daemon
        self._worker = worker
        self._identity = dict(identity)
        self._process = process
        self._confirmed = False
        self.cleanup_started = False
        self.readback = readback
        self.worker_pid = getattr(process, "pid", None)

    def mark_cleanup_started(self) -> None:
        if self._confirmed:
            raise WorkerRetirementRefused("Worker retirement is already confirmed")
        self.cleanup_started = True

    def confirm_worker_stopped(self) -> None:
        """Confirm exit of the exact Popen child bound at lease acquisition.

        COMSOL-server ownership is deliberately outside this confirmation; the
        external lifecycle owner still needs its own PID/birth/listener proof.
        """
        if not self.cleanup_started:
            raise WorkerRetirementRefused("mark exact Worker cleanup started before confirming retirement")
        if self._process is None or type(self.worker_pid) is not int or self.worker_pid <= 0:
            raise WorkerRetirementRefused("lease has no exact owned Worker child process handle")
        try:
            exited = self._process.poll() is not None
        except Exception as exc:
            raise WorkerRetirementRefused("exact Worker child state could not be read") from exc
        if not exited:
            raise WorkerRetirementRefused("exact Worker child is still live")
        if self._daemon.backend.worker is not self._worker:
            raise WorkerRetirementRefused("daemon Worker binding changed during retirement")
        if self._daemon.backend.worker_identity != self._identity:
            raise WorkerRetirementRefused("daemon Worker epoch changed during retirement")
        current_process = getattr(self._worker, "_process", None)
        if current_process not in (None, self._process):
            raise WorkerRetirementRefused("Worker object now refers to a different child process")
        self._confirmed = True


def configure_remote_backend(worker):
    import comsol_mcp._server as server
    server._remote_client_factory = worker.client


class ControlDaemon:
    def __init__(self, home, *, service=None, registry=None, worker=None, project_root=None,
                 desktop_adapter=None, project_authorization_verifier=None,
                 session_runtime_resolver=None, session_worker_factory=None,
                 session_peer_observer=None, session_credentials_resolver=None,
                 session_server_launcher=None):
        self.home = Path(home)
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.store = OperationStore(self.home / "operations.sqlite3")
        self.backend = ManagedBackend(
            self.home,
            self.store,
            service=service,
            registry=registry,
            worker=worker,
            project_root=project_root,
            session_worker_factory=session_worker_factory,
            session_peer_observer=session_peer_observer,
        )
        self.project_authority = ProjectAuthority(
            self.store,
            workspace_root=self.backend.project_root,
            grant_ceiling=frozenset(self.backend.host_permission_ceiling),
            permission_provider=self._project_host_permissions,
            authorization_verifier=project_authorization_verifier,
        )
        self.queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="comsol-engine-queue")
        self.session_registry = SessionRuntimeRegistry()
        self.session_scheduler = SessionEndpointScheduler()
        self.session_lifecycle = SessionLifecycleStore(self.store)
        self.session_runtime_resolver = session_runtime_resolver
        self.session_worker_factory = session_worker_factory
        self.session_peer_observer = session_peer_observer
        self.session_credentials_resolver = session_credentials_resolver
        self.session_server_launcher = session_server_launcher or OwnedServerLauncher()
        self._session_connect_lock = threading.RLock()
        self._session_worker_handles: dict[tuple[str, str], Any] = {}
        self._session_backends: dict[tuple[str, str], ManagedBackend] = {}
        self._session_runtime_configs: dict[tuple[str, str], SessionRuntimeConfig] = {}
        # Only the opaque local credential reference is retained in memory.
        # Resolved user/password values are request-local and never persisted.
        self._session_credentials_refs: dict[tuple[str, str], str | None] = {}
        self._session_process_identities: dict[tuple[str, str], Any] = {}
        # Retirement requires both the exact Worker Popen object and its
        # platform-reported birth identity. This is ephemeral host evidence,
        # never caller supplied and never reconstructed from a PID alone.
        self._session_worker_child_identities: dict[tuple[str, str], dict[str, Any]] = {}
        # A Server is controllable only while this daemon retains the exact
        # launcher Popen handle and the host-observed process birth/listener.
        # Durable lifecycle rows deliberately do not recreate this handle.
        self._session_server_handles: dict[tuple[str, str], ManagedServerHandle] = {}
        self.desktop = DesktopCoordinator(
            store=self.store,
            adapter=desktop_adapter if desktop_adapter is not None else create_native_metadata_adapter(),
            authorize=self._authorize_desktop,
            engine_queue=self._desktop_engine_queue,
            resolve_action=self._resolve_desktop_action,
            resolve_artifact=self._resolve_desktop_artifact,
            authorize_save_copy=self._authorize_desktop_save_copy,
        )
        self.lock = threading.RLock()
        self._admission_lock = threading.RLock()
        self._worker_retirement_state = "OPEN"
        self._worker_admission_inflight = 0
        self._control_dispatch_inflight = 0
        self._dispatch_condition = threading.Condition(self._admission_lock)
        self._closing = False
        self._close_started = False
        self._close_complete = False
        self._shutdown_tasks_drained = False
        # Engine-queue admission advances this value while holding ``lock``;
        # quiescence snapshots compare it before and after durable readback.
        self._activity_generation = 0
        self.running = {}
        self.worker_health = {"status": "NOT_STARTED", "observed_at": None}
        self.store.reconcile_after_restart()
        self.closed = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, name="comsol-cached-control", daemon=True)
        self.monitor.start()

    @property
    def backend(self):
        context = active_session_context()
        if context is not None and context.backend is not None:
            return context.backend
        return self._default_backend

    @backend.setter
    def backend(self, value):
        self._default_backend = value

    @property
    def service(self): return self.backend.service

    @property
    def worker(self): return self.backend.worker

    def quiescence_readback(self, project_id: str, session_id: str) -> dict[str, Any]:
        """Trusted in-process readback before retiring this daemon's Worker."""
        from ._daemon_quiescence import control_daemon_quiescence_readback

        return control_daemon_quiescence_readback(self, project_id, session_id)

    @contextmanager
    def worker_retirement_guard(self, project_id: str, session_id: str):
        """Fence new Worker admissions through exact child-process retirement.

        A plain quiescence snapshot is observational only. This context manager
        closes the existing Worker admission gate before re-reading durable and
        direct-RPC state, then keeps admissions closed until the caller confirms
        exit of the exact Popen child. Exiting without cleanup revokes the fence;
        uncertain cleanup leaves it fail-closed.
        """
        with self._admission_lock:
            if self._worker_retirement_state != "OPEN":
                raise WorkerRetirementRefused(
                    f"Worker admission is {self._worker_retirement_state.lower()}"
                )
            if self._worker_admission_inflight:
                raise WorkerRetirementRefused("a direct Worker observation is still in progress")
            readback = self.quiescence_readback(project_id, session_id)
            data = readback.get("data") if isinstance(readback, dict) else None
            if (not isinstance(data, dict) or data.get("worker_status") != "QUIESCENT"
                    or data.get("safe_to_retire_worker") is not True):
                raise WorkerRetirementRefused("Worker has queued, running, unknown, or unobserved work",
                                              readback=readback)
            worker = self.backend.worker
            identity = dict(self.backend.worker_identity or {})
            process = getattr(worker, "_process", None)
            if (worker is None or not callable(getattr(process, "poll", None))
                    or type(getattr(process, "pid", None)) is not int
                    or process.pid <= 0):
                raise WorkerRetirementRefused(
                    "exact Worker Popen identity is unavailable; cannot fence retirement",
                    readback=readback,
                )
            self._worker_retirement_state = "RETIRING"
            lease = WorkerRetirementLease(self, worker, identity, process, readback)
        try:
            yield lease
        except BaseException:
            with self._admission_lock:
                self._worker_retirement_state = "UNKNOWN" if lease.cleanup_started else "OPEN"
            raise
        else:
            with self._admission_lock:
                self._worker_retirement_state = (
                    "RETIRED" if lease._confirmed else
                    ("UNKNOWN" if lease.cleanup_started else "OPEN")
                )

    def _worker_admission_refusal(self) -> dict[str, Any] | None:
        state = self._worker_retirement_state
        if state == "OPEN":
            return None
        code = "WORKER_RETIREMENT_IN_PROGRESS" if state == "RETIRING" else "WORKER_RETIRED_OR_UNKNOWN"
        return self._error(code, "managed Worker admission is closed by its retirement fence",
                           data={"engine_dispatched": False, "worker_retirement_state": state})

    @contextmanager
    def _worker_observation_admission(self):
        with self._admission_lock:
            refusal = self._worker_admission_refusal()
            if refusal is not None:
                raise ExecutionContractError(refusal["error"]["code"], refusal["error"]["message"])
            self._worker_admission_inflight += 1
        try:
            yield
        finally:
            with self._admission_lock:
                self._worker_admission_inflight -= 1
                self._dispatch_condition.notify_all()

    def _project_host_permissions(self):
        permissions = set(self.backend.host_permission_ceiling)
        service = self.backend.service
        if service is not None:
            current = getattr(getattr(service, "ledger", None), "permissions", None)
            if not isinstance(current, (set, frozenset)):
                raise RuntimeError("connected service permission state is unavailable")
            permissions &= set(current)
        return permissions

    def dispatch(self, request):
        # Linearize close against every public request before it can block on
        # a session lock, create a Worker, or enter an engine lane. Close first
        # closes this gate, then waits for admitted calls before retiring
        # children and draining accepted scheduler work.
        with self._dispatch_condition:
            if self._closing:
                return self._error(
                    "CONTROL_DAEMON_CLOSING",
                    "control daemon is closing and no longer accepts requests",
                    data={"engine_dispatched": False},
                    safe_retry=False,
                )
            self._control_dispatch_inflight += 1
        try:
            return self._dispatch_admitted(request)
        finally:
            with self._dispatch_condition:
                self._control_dispatch_inflight -= 1
                self._dispatch_condition.notify_all()

    def _dispatch_admitted(self, request):
        try:
            if not isinstance(request, dict):
                raise ExecutionContractError("INVALID_REQUEST", "request must be an object")
            operation = request.get("operation")
            arguments, execution = request.get("arguments", {}), request.get("execution", {})
            if not isinstance(operation, str) or not isinstance(arguments, dict) or not isinstance(execution, dict):
                raise ExecutionContractError("INVALID_REQUEST", "invalid operation/arguments/execution")
            operation = SESSION_ALIASES.get(operation, operation)
            timeouts = self._timeouts(execution)
            if operation in SESSION_OPERATIONS:
                return self._dispatch_session_control(operation, arguments, execution)
            if operation in DESKTOP_OPERATIONS:
                return self._dispatch_desktop_control(operation, arguments, execution)
            if operation == "job.resume":
                return self._dispatch_catalog_job_resume(arguments, execution=execution, timeouts=timeouts)
            if operation in CACHED_JOB_ACTIONS:
                return self._dispatch_catalog_job_control(operation, arguments, execution=execution)
            if operation in CONTROL_READS:
                if operation in {"job_reconcile", "job.reconcile"}:
                    return self._dispatch_job_reconcile(arguments, execution=execution)
                if operation in {"job_cancel", "job.cancel"} and arguments.get("force_stop") is True:
                    return self._dispatch_job_cancel(arguments, execution=execution)
                return self._control_read(operation, arguments)
            if operation in {"registry_call", "operation_call"}:
                cached = self._cached_registry_control(operation, arguments, execution=execution)
                if cached is not None:
                    return cached
            if operation == "runtime_poc_v64":
                raise ExecutionContractError("UNSUPPORTED_OPERATION", "historical one-shot probe is disabled in the managed backend")
            from ._g3_w21 import ALIASES
            operation = ALIASES.get(operation, operation)
            operation = {"artifact_register": "artifact.register", "geometry_import": "geometry.import"}.get(operation, operation)
            operation = {
                "project_create": "project.create", "project_inspect": "project.inspect",
                "project_contract_set": "project.contract_set", "project_policy_set": "project.policy_set",
                "project_permissions": "project.permissions", "project_state_export": "project.state_export",
            }.get(operation, operation)
            if operation == "plot_render":
                operation = "plot.render"
            if operation.startswith("validate_"):
                operation = operation.replace("validate_", "validate.", 1)
            if operation in PROJECT_OPERATIONS:
                return self._dispatch_project_control(operation, arguments, execution, timeouts)
            if operation == "artifact.register":
                from ._g2_registry import validate_call
                validate_call(operation, arguments)
                project_id = arguments.get("project_id")
                idempotency_key = arguments.get("idempotency_key")
                if execution.get("project_id") not in (None, project_id):
                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "artifact.register project_id differs from the execution envelope")
                if execution.get("idempotency_key") not in (None, idempotency_key):
                    raise ExecutionContractError("IDEMPOTENCY_CONFLICT", "artifact.register idempotency_key differs from the execution envelope")
                body_request_id = arguments.get("request_id")
                if execution.get("request_id") not in (None, body_request_id) and body_request_id is not None:
                    raise ExecutionContractError("INVALID_REQUEST", "artifact.register request_id differs from the execution envelope")
                execution = {
                    **execution,
                    "project_id": project_id,
                    "idempotency_key": idempotency_key,
                    **({"request_id": body_request_id} if body_request_id is not None else {}),
                }
            if operation == "geometry.import":
                from ._g2_registry import validate_call
                scoped = dict(arguments)
                for identity in ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"):
                    if identity not in scoped and identity in execution:
                        scoped[identity] = execution[identity]
                if scoped.get("project_id") != execution.get("project_id"):
                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "geometry.import project_id must match the execution envelope")
                validate_call(operation, scoped)
                for identity in ("session_id", "expected_revision", "idempotency_key"):
                    if identity in arguments and identity in execution and arguments[identity] != execution[identity]:
                        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", f"geometry.import {identity} differs from the execution envelope")
            if operation in {"model.adopt", "model.inspect"}:
                nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
                present = sorted(nested_identity.intersection(arguments))
                if present:
                    raise ExecutionContractError(
                        "INVALID_REQUEST",
                        f"{operation} identity belongs in the outer execution envelope: {', '.join(present)}",
                    )
                from ._g2_registry import validate_call
                validate_call(operation, arguments)
                if not isinstance(execution.get("session_id"), str) or not execution["session_id"]:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", f"{operation} requires execution.session_id")
                if operation == "model.inspect" and not isinstance(execution.get("model_ref"), dict):
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "model.inspect requires execution.model_ref")
            if operation == "study.run":
                nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
                present = sorted(nested_identity.intersection(arguments))
                if present:
                    raise ExecutionContractError("INVALID_REQUEST", "study.run identity belongs in the outer execution envelope: " + ", ".join(present))
                from ._g2_registry import validate_call
                validate_call(operation, arguments)
                if not isinstance(execution.get("session_id"), str) or not execution["session_id"]:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "study.run requires execution.session_id")
                if not isinstance(execution.get("model_ref"), dict):
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "study.run requires execution.model_ref")
                revision = execution.get("expected_revision")
                if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                    raise ExecutionContractError("REVISION_CONFLICT", "study.run requires a non-negative execution.expected_revision")
            if (
                operation not in self.backend.registry
                and operation not in {"registry_call", "operation_call", "model_adopt", "model_inspect", "model.adopt", "model.inspect", "artifact.register"}
                and operation not in _g3_operations()
            ):
                raise ExecutionContractError("UNSUPPORTED_OPERATION", f"operation is not registered: {operation}")
            session_context = self._execution_session_context(execution)
            from contextlib import nullcontext
            session_scope = use_session_context(session_context) if session_context is not None else nullcontext()
            with session_scope:
                self._authorize_project_execution(operation, arguments, execution)
                if isinstance(execution.get("project_id"), str) and execution["project_id"]:
                    timeouts = self.project_authority.apply_timeout_caps(execution["project_id"], timeouts)
                if operation == "server_connect" and self.service is None and not (os.environ.get("COMSOL_ROOT") and (os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME"))):
                    raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "COMSOL_ROOT and COMSOL_JAVA_HOME must be configured")
            runtime_binding = self._runtime_binding_for_request(session_context, execution)
            request_id = execution.get("request_id") or str(uuid4())
            key = execution.get("idempotency_key") or str(uuid4())
            if not isinstance(request_id, str) or not isinstance(key, str) or not key:
                raise ExecutionContractError("INVALID_REQUEST", "request and idempotency identifiers must be strings")
            execution = {**execution, "request_id": request_id, "idempotency_key": key}
            digest_operation, digest_arguments = operation, arguments
            if operation in {"registry_call", "operation_call"}:
                nested_operation = arguments.get("operation_id")
                nested_arguments = arguments.get("arguments", {})
                if isinstance(nested_operation, str) and isinstance(nested_arguments, dict):
                    # The wrapper is a transport adapter, not a distinct
                    # semantic operation. Bind direct and nested entrypoints
                    # to one idempotency identity.
                    digest_operation, digest_arguments = nested_operation, nested_arguments
            digest = canonical_request_hash(
                digest_operation, digest_arguments, execution.get("model_ref"), execution.get("expected_revision"),
                **({"project_id": execution["project_id"]} if isinstance(execution.get("project_id"), str) else {}),
                session_id=execution.get("session_id"), queue_timeout_s=timeouts["queue_timeout_s"],
                execution_timeout_s=timeouts["execution_timeout_s"], no_progress_warning_s=timeouts["no_progress_warning_s"],
            )
            # Authorization references are transient control inputs. Bind
            # their digests to the idempotency identity and persisted metadata,
            # but never store the raw references in operation or job records.
            persisted_arguments = dict(arguments)
            persisted_execution = dict(execution)
            if operation in {"runtime.license_checkout", "runtime.render_probe"}:
                persisted_arguments.pop("authorization_ref_sha256", None)
                persisted_execution.pop("authorization_ref_sha256", None)
                authorization_hashes = []
                for location, mapping in (("arguments", persisted_arguments), ("execution", persisted_execution)):
                    authorization_ref = mapping.pop("authorization_ref", None)
                    if authorization_ref is not None:
                        if isinstance(authorization_ref, str):
                            encoded_ref = authorization_ref.encode("utf-8")
                        else:
                            encoded_ref = json.dumps(
                                authorization_ref, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False,
                            ).encode("utf-8")
                        authorization_sha256 = hashlib.sha256(encoded_ref).hexdigest()
                        mapping["authorization_ref_sha256"] = authorization_sha256
                        authorization_hashes.append((location, authorization_sha256))
                if authorization_hashes:
                    # Keep the exact control location in the idempotency
                    # identity without persisting either raw authorization ref.
                    auth_identity = "\0".join(f"{where}:{value}" for where, value in authorization_hashes)
                    digest = hashlib.sha256(f"{digest}\0authorization_refs:{auth_identity}".encode("utf-8")).hexdigest()
            with self._admission_lock:
                refusal = self._worker_admission_refusal()
                if refusal is not None:
                    return refusal
                with self.lock:
                    record_metadata = {"operation": operation, "arguments": persisted_arguments,
                                       "execution": persisted_execution}
                    if runtime_binding is not None:
                        record_metadata["runtime_binding"] = runtime_binding
                    record, reused = self.store.begin(request_id=request_id, idempotency_key=key, request_hash=digest,
                        operation=operation, metadata=record_metadata, timeouts=timeouts)
                    if reused:
                        return record["result"] if record["result"] is not None else self._pending(record)
                    submitted = time.monotonic()
                    # The durable operation/job exists before it can enter the
                    # engine queue. Admission and generation advance are atomic
                    # with respect to the in-process quiescence snapshot.
                    self._activity_generation += 1
                    # Legacy/default work has no process-attested session
                    # context and therefore shares the scheduler's one
                    # global-exclusive unknown lane with registered unknown
                    # endpoints.  It must never bypass that gate via the
                    # historical single-worker queue.
                    try:
                        future = self.session_scheduler.submit(
                            session_context, self._execute, record, operation, arguments,
                            execution, timeouts, submitted,
                        )
                    except SessionSchedulerClosed:
                        refused = self._error(
                            "WORKER_RETIRED_OR_UNKNOWN",
                            "the selected Worker/server lane is fenced and cannot accept new work",
                            data={"engine_dispatched": False}, safe_retry=False,
                        )
                        return self._finish(record, refused, "FAILED")
            try:
                return future.result(timeout=timeouts["rpc_timeout_s"])
            except SessionSchedulerClosed:
                refused = self._error(
                    "WORKER_RETIRED_OR_UNKNOWN",
                    "the selected Worker/server lane was fenced before this request dispatched",
                    data={"engine_dispatched": False}, safe_retry=False,
                )
                return self._finish(record, refused, "FAILED")
            except FutureTimeout:
                return self._pending(record, rpc_wait_expired=True)
        except (ExecutionContractError, IdempotencyConflict) as exc:
            return self._exception(exc)
        except Exception as exc:
            self._log_exception()
            return self._error("EXECUTION_STATE_UNKNOWN", "control could not establish the request state", type=type(exc).__name__)

    def _cached_registry_control(self, outer_operation: str, outer_arguments: dict[str, Any], *, execution: dict[str, Any]) -> dict[str, Any] | None:
        """Route supported nested job controls to the cached store directly.

        Other registry actions retain the existing ManagedBackend path.  The
        execution envelope is intentionally not copied into nested arguments:
        job controls are project/control-plane operations, not model writes.
        """
        if not isinstance(outer_arguments, dict):
            raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} arguments must be an object")
        extra_outer = sorted(set(outer_arguments) - {"operation_id", "arguments"})
        if extra_outer and str(outer_arguments.get("operation_id", "")).startswith("job."):
            raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
        inner_operation = outer_arguments.get("operation_id")
        if isinstance(inner_operation, str):
            inner_operation = SESSION_ALIASES.get(inner_operation, inner_operation)
        if extra_outer and inner_operation in DESKTOP_OPERATIONS:
            raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
        if inner_operation in SESSION_OPERATIONS:
            if extra_outer:
                raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
            inner_arguments = outer_arguments.get("arguments", {})
            if not isinstance(inner_arguments, dict):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            return self._dispatch_session_control(inner_operation, inner_arguments, execution)
        if inner_operation in PROJECT_OPERATIONS:
            inner_arguments = outer_arguments.get("arguments", {})
            if not isinstance(inner_arguments, dict):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            return self._dispatch_project_control(
                inner_operation, inner_arguments, execution, self._timeouts(execution),
            )
        if not isinstance(inner_operation, str) or not inner_operation.startswith("job."):
            if inner_operation in DESKTOP_OPERATIONS:
                inner_arguments = outer_arguments.get("arguments", {})
                if not isinstance(inner_arguments, dict):
                    raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
                return self._dispatch_desktop_control(inner_operation, inner_arguments, execution)
            return None
        inner_arguments = outer_arguments.get("arguments", {})
        if not isinstance(inner_arguments, dict):
            raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
        if inner_operation == "job.resume":
            return self._dispatch_catalog_job_resume(
                inner_arguments, execution=execution, timeouts=self._timeouts(execution),
            )
        if inner_operation in {"model.adopt", "model.inspect"}:
            nested_identity = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
            present = sorted(nested_identity.intersection(inner_arguments))
            if present:
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    f"{inner_operation} identity belongs in the outer execution envelope: {', '.join(present)}",
                )

        return self._dispatch_catalog_job_control(inner_operation, inner_arguments, execution=execution)

    def _dispatch_desktop_control(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        """Validate catalog shape, then use the durable Desktop coordinator."""
        from . import _g2_registry

        routed = dict(arguments)
        for field in ("project_id", "idempotency_key", "request_id"):
            if field not in execution:
                continue
            if (field in routed and routed[field] != execution[field]
                    and not (field == "request_id" and routed[field] == "")):
                raise ExecutionContractError("INVALID_REQUEST", f"Desktop {field} differs from the execution envelope")
            if field not in routed or (field == "request_id" and routed[field] == ""):
                routed[field] = execution[field]
        _g2_registry.validate_call(
            operation,
            routed,
            allow_coordinator_only=True,
        )
        return self.desktop.dispatch(operation, routed)

    def _authorize_desktop(self, project_id: str, permission: str) -> bool:
        """Map catalog Desktop permissions to host-owned capability grants."""
        if not isinstance(project_id, str) or not project_id:
            return False
        service = self.backend.service
        if permission == "READ" and service is None:
            # The control endpoint is same-user, loopback-only, and bearer
            # authenticated. A read-only platform status query does not need a
            # COMSOL session; connected sessions still use the ledger grant.
            return True
        if service is None:
            return False
        permissions = getattr(getattr(service, "ledger", None), "permissions", None)
        if not isinstance(permissions, set):
            return False
        required = {"READ": "inspect", "HOST_CONTROL": "host_control", "TRUSTED_CODE": "trusted_code"}.get(permission)
        return required is not None and required in permissions

    def _desktop_engine_queue(self, operation, arguments, model_ref, context):
        """Serialize any future managed API substep and fail closed today."""
        future = self.queue.submit(self._reject_unavailable_desktop_engine_route, operation)
        return future.result()

    @staticmethod
    def _reject_unavailable_desktop_engine_route(operation: str) -> dict[str, Any]:
        # desktop.binding.resolve/inspect and Desktop Shell execution do not
        # yet have a trusted current-window binding API in ManagedBackend.
        # Keeping the rejection inside the existing queue ensures that a
        # future approved implementation cannot silently bypass serialization.
        return {
            "success": False,
            "data": {"engine_dispatched": False},
            "error": {
                "code": "UNSUPPORTED_CONTROL",
                "message": f"managed Desktop engine route is unavailable: {operation}",
                "stage": "validation",
                "safe_retry": True,
            },
        }

    @staticmethod
    def _resolve_desktop_action(project_id, action):
        raise DesktopOperationError(
            "UNSUPPORTED_CONTROL", "no versioned trusted Desktop action resolver is configured",
            safe_retry=True,
        )

    @staticmethod
    def _resolve_desktop_artifact(project_id, artifact_id):
        raise DesktopOperationError(
            "UNSUPPORTED_CONTROL", "no trusted Desktop Shell artifact resolver is configured",
            safe_retry=True,
        )

    @staticmethod
    def _authorize_desktop_save_copy(project_id, destination):
        return False

    def _dispatch_catalog_job_control(self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch_catalog_job_control_impl(
            inner_operation, inner_arguments, execution=execution,
        )

    @staticmethod
    def _session_jdk_tools(jdk_home: Path, *, platform_name: str | None = None) -> tuple[Path, Path]:
        """Return platform-correct Java Worker tools from JavaWorkerPaths."""
        from ._java_worker import JavaWorkerPaths
        paths = JavaWorkerPaths(Path("."), Path(jdk_home), platform_name=platform_name)
        return paths.executable("java"), paths.executable("javac")

    def _resolve_session_runtime(self, runtime_id: str, project_id: str, session_id: str,
                                 project_root: Path) -> SessionRuntimeConfig:
        """Resolve only this host's exact, locally inspected COMSOL install."""
        from ._java_worker import JavaWorkerPaths
        from ._runtime_installation import inspect_installation

        state_root = self.home / "session-runtime-state"
        if state_root.is_symlink():
            raise ExecutionContractError("PERMISSION_DENIED", "session runtime state root cannot be a symlink")
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.session_runtime_resolver is not None:
            try:
                runtime = self.session_runtime_resolver(runtime_id, project_id, session_id, project_root, state_root)
            except Exception as exc:
                raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "trusted session runtime resolution failed") from exc
            if not isinstance(runtime, SessionRuntimeConfig) or runtime.runtime_id != runtime_id:
                raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "runtime resolver returned a mismatched local runtime")
            return runtime

        try:
            installation = inspect_installation(runtime_id)["installation"]
        except Exception as exc:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "runtime_id does not identify an inspected local COMSOL installation") from exc
        if installation.get("runtime_id") != runtime_id or installation.get("metadata_only") is not True:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "local COMSOL installation identity is not exact")
        version = installation.get("version")
        if not isinstance(version, Mapping) or version.get("status") != "OBSERVED" or not isinstance(version.get("value"), str):
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "local COMSOL version metadata is unavailable")

        configured_java = os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME")
        candidates: list[Path] = []
        if configured_java:
            candidates.append(Path(configured_java).expanduser())
        else:
            target_arch = platform.machine().lower()
            target_arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(target_arch, target_arch)
            for entry in installation.get("bundled_java", []):
                if not isinstance(entry, Mapping):
                    continue
                arch = entry.get("architecture", {}).get("values", []) if isinstance(entry.get("architecture"), Mapping) else []
                if target_arch in arch and isinstance(entry.get("home"), str):
                    candidates.append(Path(entry["home"]))
            if len(candidates) != 1:
                raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "configure one trusted JDK with java and javac")
        valid_jdks = []
        for candidate in candidates:
            try:
                home = candidate.resolve(strict=True)
                java_executable, javac_executable = self._session_jdk_tools(home)
                if home.is_dir() and java_executable.is_file() and javac_executable.is_file():
                    valid_jdks.append(home)
            except OSError:
                continue
        if len(valid_jdks) != 1:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "trusted JDK home with java and javac is unavailable or ambiguous")

        session_home = session_state_directory(state_root, project_id, session_id)
        preferences = session_home / "preferences"
        if preferences.is_symlink():
            raise ExecutionContractError("PERMISSION_DENIED", "per-session preferences directory cannot be a symlink")
        preferences.mkdir(mode=0o700, parents=True, exist_ok=True)
        installation_root = Path(installation["root"]).resolve(strict=True)
        paths = JavaWorkerPaths(installation_root, valid_jdks[0], preferences, project_root=project_root)
        try:
            paths.validate()
            classpath, _manifest_hash, _jar_count, _jar_hash = paths.classpath()
        except Exception as exc:
            raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "COMSOL Worker classpath or JDK validation failed") from exc
        return SessionRuntimeConfig(
            runtime_id=runtime_id,
            comsol_version=version["value"],
            installation_root=installation_root,
            java_executable=paths.executable("java").resolve(strict=True),
            classpath=tuple(Path(item) for item in classpath.split(paths.classpath_separator) if item),
            preferences_dir=preferences.resolve(),
            session_state_root=state_root.resolve(),
        )

    def _resolve_session_credentials(self, credentials_ref: str | None) -> dict[str, str]:
        """Resolve an opaque trusted reference into request-local credentials."""
        if credentials_ref is None:
            return {}
        if not isinstance(credentials_ref, str) or not credentials_ref.strip():
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "session credential reference is unavailable")
        if self.session_credentials_resolver is None:
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "credentials_ref has no trusted local resolver")
        try:
            credentials = self.session_credentials_resolver(credentials_ref)
        except Exception as exc:
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "trusted credentials reference could not be resolved") from exc
        if (not isinstance(credentials, Mapping)
                or set(credentials) - {"user", "password"}
                or any(not isinstance(credentials.get(name, ""), str) for name in ("user", "password"))):
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "trusted credentials resolver returned an invalid secret record")
        return {name: credentials.get(name, "") for name in ("user", "password")}

    def _quarantine_session_context(self, project_id: str, session_id: str,
                                    context: SessionRuntimeContext | None) -> None:
        """Block future admission and remove one context without canceling accepted work."""
        if context is None:
            return
        try:
            self.session_scheduler.close_session_binding_admission(context)
        except SessionSchedulerClosed:
            # A closed scheduler already rejects every new task. Accepted work
            # remains owned by its Futures and is allowed to drain.
            pass
        self._remove_session_context(project_id, session_id, expected=context)

    @staticmethod
    def _redact_session_worker_event(value):
        secret_names = {"password", "user", "credentials_ref", "authorization_ref", "token"}
        if isinstance(value, Mapping):
            return {
                str(key): ("[REDACTED]" if str(key).casefold() in secret_names
                           else ControlDaemon._redact_session_worker_event(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ControlDaemon._redact_session_worker_event(item) for item in value]
        if isinstance(value, tuple):
            return [ControlDaemon._redact_session_worker_event(item) for item in value]
        return value

    @staticmethod
    def _server_process_lifecycle_record(handle: ManagedServerHandle) -> dict[str, Any]:
        identity = handle.process_identity
        endpoint = handle.endpoint
        if identity is None or endpoint is None:
            raise OwnedServerError("owned Server has no complete process/listener identity", handle=handle, uncertain=True)
        return {
            "pid": identity.pid,
            "birth": identity.birth,
            "executable": identity.executable,
            "runtime_id": handle.runtime_id,
            "endpoint": {"host": endpoint.address, "port": endpoint.port},
        }

    def _owned_server_handle(self, project_id: str, session_id: str,
                             lifecycle: Mapping[str, Any]) -> tuple[ManagedServerHandle, OwnedServerProcessIdentity]:
        key = (project_id, session_id)
        handle = self._session_server_handles.get(key)
        if not isinstance(handle, ManagedServerHandle):
            raise OwnedServerError("the daemon no longer retains the exact owned Server Popen handle", uncertain=True)
        if (lifecycle.get("server_ownership") != "mcp_managed"
                or lifecycle.get("server_state") != "MCP_MANAGED"
                or not isinstance(lifecycle.get("server_process_identity"), Mapping)):
            raise OwnedServerError("durable lifecycle does not prove MCP Server ownership", handle=handle, uncertain=True)
        durable_identity = dict(lifecycle["server_process_identity"])
        if durable_identity != self._server_process_lifecycle_record(handle):
            raise OwnedServerError("durable Server identity differs from the retained process handle", handle=handle, uncertain=True)
        try:
            identity = self.session_server_launcher.verify(handle)
        except OwnedServerError:
            raise
        except Exception as exc:
            raise OwnedServerError("owned Server process/listener readback failed", handle=handle, uncertain=True) from exc
        return handle, identity

    @staticmethod
    def _endpoint_may_alias_owned_listener(host: Any, port: Any,
                                           listener: CanonicalSocket) -> bool:
        """Conservatively match local aliases without resolving or probing DNS.

        Owned COMSOL Servers bind only to loopback. When a caller supplies a
        hostname for the same port, its local/remote resolution cannot be
        established without a network lookup, so treat it as a possible local
        alias and fail closed. Literal non-loopback addresses remain distinct.
        """
        if type(port) is not int or port != listener.port or not isinstance(host, str) or not host.strip():
            return False
        normalized = host.strip().strip("[]").rstrip(".").casefold()
        try:
            owned = ipaddress.ip_address(listener.address)
        except ValueError:
            return True
        try:
            requested = ipaddress.ip_address(normalized)
        except ValueError:
            return owned.is_loopback or owned.is_unspecified
        if requested == owned:
            return True
        requested_local = requested.is_loopback or requested.is_unspecified
        owned_local = owned.is_loopback or owned.is_unspecified
        if requested.version == 6 and requested.ipv4_mapped is not None:
            requested_local = requested_local or requested.ipv4_mapped.is_loopback
        return requested_local and owned_local

    def _owned_server_endpoint_conflict(self, project_id: str, session_id: str,
                                         host: str, port: int) -> bool | None:
        """Whether another live/durable MCP-owned endpoint may accept this attach.

        The owner identity is intentionally not returned to the caller: projects
        are independent authorization boundaries.
        """
        target_key = (project_id, session_id)
        for key, handle in self._session_server_handles.items():
            if key == target_key or not isinstance(handle, ManagedServerHandle) or handle.endpoint is None:
                continue
            if self._endpoint_may_alias_owned_listener(host, port, handle.endpoint):
                return True
        try:
            rows = self.store.list_metadata("sessions")
        except Exception:
            return None
        for metadata in rows:
            if not isinstance(metadata, Mapping):
                return None
            lifecycle = metadata.get("lifecycle")
            if not isinstance(lifecycle, Mapping):
                continue
            key = (lifecycle.get("project_id"), lifecycle.get("session_id"))
            if (key == target_key or lifecycle.get("server_ownership") != "mcp_managed"
                    or lifecycle.get("state") in {"STOPPED", "LOST"}):
                continue
            endpoint = lifecycle.get("endpoint")
            if not isinstance(endpoint, Mapping) or endpoint.get("port") != port:
                continue
            owner_host = endpoint.get("host")
            if not isinstance(owner_host, str):
                return None
            try:
                owner_socket = CanonicalSocket(owner_host, port)
            except (TypeError, ValueError):
                # A hostname in a persisted owned endpoint is not
                # independently trustworthy; the matching port is ambiguous.
                return True
            if self._endpoint_may_alias_owned_listener(host, port, owner_socket):
                return True
        return False

    def _other_session_binding_uses_owned_server(self, owner_key: tuple[str, str],
                                                  identity: OwnedServerProcessIdentity) -> bool | None:
        """Check all durable session rows and retained handles before Server stop."""
        try:
            rows = self.store.list_metadata("sessions")
        except Exception:
            return None
        lifecycle_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
        for metadata in rows:
            if not isinstance(metadata, Mapping):
                return None
            raw = metadata.get("lifecycle")
            if not isinstance(raw, Mapping):
                continue
            project_id, session_id = raw.get("project_id"), raw.get("session_id")
            if isinstance(project_id, str) and isinstance(session_id, str):
                lifecycle_rows[(project_id, session_id)] = raw
        candidate_keys = set(lifecycle_rows) | set(self._session_worker_handles)
        for key in candidate_keys:
            if key == owner_key:
                continue
            raw = lifecycle_rows.get(key)
            try:
                lifecycle = self.session_lifecycle.get(*key)
            except Exception:
                endpoint = raw.get("endpoint") if isinstance(raw, Mapping) else None
                if isinstance(endpoint, Mapping):
                    host, port = endpoint.get("host"), endpoint.get("port")
                    for listener in identity.listener_sockets:
                        if self._endpoint_may_alias_owned_listener(host, port, listener):
                            return True
                return None
            context = None
            try:
                context = self.session_registry.get(*key)
            except SessionContextMissing:
                pass
            except Exception:
                return None
            if lifecycle is None:
                if key in self._session_worker_handles or context is not None:
                    return None
                continue
            matched = False
            peer = context.endpoint.observed_peer if context is not None else None
            if isinstance(peer, CanonicalSocket):
                matched = identity.attests_peer(peer)
            else:
                endpoint = lifecycle.get("endpoint")
                if isinstance(endpoint, Mapping):
                    matched = any(
                        self._endpoint_may_alias_owned_listener(
                            endpoint.get("host"), endpoint.get("port"), listener,
                        ) for listener in identity.listener_sockets
                    )
            if not matched:
                continue
            has_retained_handle = key in self._session_worker_handles
            active_state = lifecycle.get("state") in {"CONNECTING", "CONNECTED", "UNKNOWN", "STOPPING"}
            active_client = lifecycle.get("client_state") in {"CONNECTED", "UNKNOWN"}
            another_owner = (lifecycle.get("server_ownership") == "mcp_managed"
                             and lifecycle.get("state") not in {"STOPPED", "LOST"})
            if has_retained_handle or context is not None or active_state or active_client or another_owner:
                return True
        return False

    def _mark_owned_server_unknown_for_close(self, lifecycle: Mapping[str, Any]) -> None:
        """Persist conservative state when close cannot prove the Server is retired."""
        try:
            unknown = self._updated_session_lifecycle(
                lifecycle, state="UNKNOWN",
                client_state=lifecycle.get("client_state", "UNKNOWN"),
                worker_instance_id=lifecycle.get("worker_instance_id"),
                worker_epoch=lifecycle.get("worker_epoch"),
                server_instance_id=lifecycle.get("server_instance_id"),
                server_state="MCP_MANAGED", server_ownership="mcp_managed",
                server_process_identity=lifecycle.get("server_process_identity"),
            )
            self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
        except Exception:
            pass

    def _authorize_server_control(self, project_id: str, authorization_ref: str | None = None) -> str | None:
        self.project_authority.authorize_operation(project_id, "host_control")
        if authorization_ref is None:
            return None
        if (not isinstance(authorization_ref, str) or not authorization_ref.strip()
                or len(authorization_ref) > 512 or any(ord(char) < 32 for char in authorization_ref)):
            raise ExecutionContractError("AUTHORIZATION_REQUIRED", "authorization_ref is malformed")
        verifier = self.project_authority.authorization_verifier
        if verifier is not None:
            try:
                accepted = verifier(project_id, authorization_ref)
            except Exception as exc:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "Server control authorization could not be verified") from exc
            if accepted is not True:
                raise ExecutionContractError("AUTHORIZATION_REQUIRED", "Server control authorization was not accepted")
        return hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()

    def _dispatch_session_start(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        project_id, runtime_id = routed["project_id"], routed["runtime_id"]
        self._authorize_server_control(project_id)
        options, resources = routed.get("options", {}), routed.get("resources", {})
        if not isinstance(options, Mapping) or not isinstance(resources, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "session.start options and resources must be objects")
        if options or resources:
            raise ExecutionContractError(
                "UNSUPPORTED_OPERATION",
                "session.start currently accepts only empty options/resources until versioned launch semantics are defined",
            )
        request_id = routed.get("request_id") or execution.get("request_id") or str(uuid4())
        idempotency_key = routed["idempotency_key"]
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "session.start request_id must be a non-empty string")
        session_id = "session-" + uuid4().hex
        from ._execution_contract import canonical_request_hash
        semantic_arguments = {"runtime_id": runtime_id, "options": {}, "resources": {}}
        request_hash = canonical_request_hash(
            "session.start", semantic_arguments, None, None, project_id=project_id,
        )
        try:
            record, reused = self.store.begin(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation="session.start",
                metadata={"project_id": project_id, "session_id": session_id,
                          "runtime_id": runtime_id, "arguments": semantic_arguments},
                timeouts=self._timeouts(execution),
            )
        except IdempotencyConflict as exc:
            raise ExecutionContractError("IDEMPOTENCY_CONFLICT", str(exc)) from exc
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(
                record, session_id=record.get("metadata", {}).get("session_id"),
            )
        session_id = record["metadata"]["session_id"]
        self._start_session_mutation_record(record, operation="session.start", session_id=session_id)
        key = (project_id, session_id)
        lifecycle = None
        handle = None
        with self._session_connect_lock:
            if self._closing:
                return self._finish(record, self._error(
                    "CONTROL_DAEMON_CLOSING", "control daemon is closing before Server birth",
                    data={"session_id": session_id, "server_birth_performed": False}, safe_retry=False,
                ), "FAILED")
            try:
                unresolved = [row for row in self.session_lifecycle.list_for_project(project_id)
                              if row.get("runtime_id") == runtime_id
                              and row.get("endpoint") is None
                              and row.get("state") in {"STARTING", "STOPPING", "UNKNOWN"}]
                if unresolved:
                    return self._finish(record, self._error(
                        "SERVER_OWNERSHIP_UNKNOWN",
                        "an earlier owned Server birth has no resolved endpoint; no replacement will be started",
                        data={"session_id": session_id, "unresolved_session_id": unresolved[0]["session_id"],
                              "server_birth_performed": False}, safe_retry=False,
                    ), "FAILED")
                project = self.project_authority.get_project(project_id)
                project_root = Path(project["workspace"]).resolve(strict=True)
                runtime = self._resolve_session_runtime(runtime_id, project_id, session_id, project_root)
                self._session_runtime_configs[key] = runtime
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="STARTING",
                    runtime_id=runtime.runtime_id, endpoint=None,
                    client_state="DISCONNECTED", server_state="UNKNOWN", server_ownership="unknown",
                ))
            except ExecutionContractError as exc:
                return self._finish(record, self._exception(exc), "FAILED")
            except Exception as exc:
                self._log_exception()
                return self._finish(record, self._error(
                    "RUNTIME_CONFIGURATION_REQUIRED", "local COMSOL Server runtime could not be resolved",
                    data={"session_id": session_id, "server_birth_performed": False},
                    cause_type=type(exc).__name__, safe_retry=True,
                ), "FAILED")

            try:
                handle = self.session_server_launcher.start(runtime, project_id, session_id)
                if not isinstance(handle, ManagedServerHandle):
                    raise OwnedServerError("Server launcher returned an untrusted handle")
                self._session_server_handles[key] = handle
                identity = self.session_server_launcher.verify(handle)
                if (handle.runtime_id != runtime.runtime_id or handle.endpoint is None
                        or identity.pid != handle.process.pid):
                    raise OwnedServerError("Server launcher identity differs from the inspected runtime", handle=handle, uncertain=True)
                endpoint = {"host": handle.endpoint.address, "port": handle.endpoint.port}
                self._session_process_identities[key] = identity
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="DISCONNECTED",
                    runtime_id=runtime.runtime_id, endpoint=endpoint,
                    client_state="DISCONNECTED", server_state="MCP_MANAGED", server_ownership="mcp_managed",
                    server_process_identity=self._server_process_lifecycle_record(handle),
                    health={"status": "HEALTHY", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "source": "exact-process-birth+loopback-listener"},
                ), expected_revision=lifecycle["revision"])
                return self._finish(record, {"success": True, "data": {
                    "project_id": project_id, "session_id": session_id,
                    "runtime_id": runtime.runtime_id, "comsol_version": runtime.comsol_version,
                    "endpoint": endpoint, "server_ownership": "mcp_managed",
                    "server_process_identity": lifecycle["server_process_identity"],
                    "loopback_only_verified": True,
                    "server_listener_sockets": [
                        {"address": item.address, "port": item.port} for item in identity.listener_sockets
                    ],
                    "client_state": "DISCONNECTED",
                }}, "SUCCEEDED")
            except Exception as exc:
                if isinstance(exc, OwnedServerError):
                    handle = exc.handle or handle
                if handle is not None:
                    self._session_server_handles[key] = handle
                    endpoint = ({"host": handle.endpoint.address, "port": handle.endpoint.port}
                                if handle.endpoint is not None else None)
                    durable_process = None
                    owner, server_state = "unknown", "UNKNOWN"
                    identity = handle.process_identity
                    if identity is not None and endpoint is not None:
                        try:
                            durable_process = self._server_process_lifecycle_record(handle)
                            owner, server_state = "mcp_managed", "MCP_MANAGED"
                        except Exception:
                            durable_process = None
                    try:
                        unknown = new_lifecycle_record(
                            project_id=project_id, session_id=session_id, state="UNKNOWN",
                            runtime_id=runtime.runtime_id, endpoint=endpoint,
                            client_state="UNKNOWN", server_state=server_state, server_ownership=owner,
                            server_process_identity=durable_process,
                        )
                        lifecycle = self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
                    except Exception:
                        pass
                    return self._finish(record, self._error(
                        "EXECUTION_STATE_UNKNOWN", "Server process was created but exact startup/registration proof is incomplete",
                        data={"session_id": session_id, "state": "UNKNOWN", "server_handle_preserved": True,
                              "server_pid": getattr(handle.process, "pid", None),
                              "server_birth_performed": True},
                        execution_state_unknown=True, safe_retry=False,
                    ), "UNKNOWN")
                try:
                    failed = new_lifecycle_record(
                        project_id=project_id, session_id=session_id, state="STOPPED",
                        runtime_id=runtime.runtime_id, endpoint=None,
                        client_state="DISCONNECTED", server_state="STOPPED", server_ownership="unknown",
                    )
                    self.session_lifecycle.save(failed, expected_revision=lifecycle["revision"])
                except Exception:
                    pass
                self._log_exception()
                return self._finish(record, self._error(
                    "SERVER_START_FAILED", "owned COMSOL Server did not start before any child handle was created",
                    data={"session_id": session_id, "server_birth_performed": False},
                    cause_type=type(exc).__name__, safe_retry=True,
                ), "FAILED")

    def _dispatch_session_stop(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        project_id, session_id = routed["project_id"], routed["session_id"]
        authorization_digest = self._authorize_server_control(project_id, routed.get("authorization_ref"))
        record, reused = self._begin_session_mutation("session.stop", routed, execution)
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(record, session_id=session_id)
        self._start_session_mutation_record(record, operation="session.stop", session_id=session_id)
        with self._session_connect_lock:
            try:
                lifecycle = self.session_lifecycle.get(project_id, session_id)
            except SessionLifecycleProjectConflict as exc:
                return self._finish(record, self._error("PROJECT_IDENTITY_MISMATCH", str(exc)), "FAILED")
            if lifecycle is None:
                return self._finish(record, self._error("SESSION_NOT_FOUND", "session lifecycle record not found"), "FAILED")
            if lifecycle["state"] == "STOPPED":
                return self._finish(record, {"success": True, "data": {
                    "project_id": project_id, "session_id": session_id,
                    "state": "STOPPED", "already_stopped": True, "server_stopped": True,
                }}, "SUCCEEDED")
            if lifecycle.get("server_ownership") != "mcp_managed":
                return self._finish(record, self._error(
                    "SERVER_OWNERSHIP_MISMATCH", "session.stop never terminates a shared or user-owned Server",
                    data={"session_id": session_id, "server_ownership": lifecycle.get("server_ownership"),
                          "server_stopped": False}, safe_retry=False,
                ), "FAILED")
            key = (project_id, session_id)
            if lifecycle["state"] in {"UNKNOWN", "STOPPING"}:
                return self._finish(record, self._error(
                    "EXECUTION_STATE_UNKNOWN", "session has an unresolved lifecycle state; Server remains running",
                    data={"session_id": session_id, "state": lifecycle.get("state"),
                          "client_state": lifecycle.get("client_state"),
                          "server_stopped": False}, execution_state_unknown=True,
                ), "UNKNOWN")
            if (lifecycle["state"] != "DISCONNECTED"
                    or lifecycle.get("client_state") not in {"DISCONNECTED", "RETIRED"}):
                return self._finish(record, self._error(
                    "SESSION_CLIENT_NOT_RETIRED", "disconnect and, when present, retire the Worker before session.stop",
                    data={"session_id": session_id, "state": lifecycle.get("state"),
                          "client_state": lifecycle.get("client_state"), "server_stopped": False},
                    safe_retry=True,
                ), "FAILED")
            if key in self._session_worker_handles:
                return self._finish(record, self._error(
                    "WORKER_BINDING_UNKNOWN", "session.stop requires the retained Worker handle to be retired first",
                    data={"session_id": session_id, "server_stopped": False,
                          "worker_handle_preserved": True}, safe_retry=False,
                ), "FAILED")
            pending = [job for job in self._session_jobs_for_project(project_id)
                       if job.get("operation_id") != record["operation_id"]
                       and self._job_belongs_to_session(job, project_id, session_id)
                       and job.get("status") not in TERMINAL]
            if pending:
                return self._finish(record, self._error(
                    "SESSION_BINDING_BUSY", "owned Server has unresolved accepted work and remains running",
                    data={"session_id": session_id, "unresolved_job_ids": [job["job_id"] for job in pending],
                          "server_stopped": False}, safe_retry=False,
                ), "FAILED")
            try:
                handle, identity = self._owned_server_handle(project_id, session_id, lifecycle)
            except OwnedServerError as exc:
                return self._finish(record, self._error(
                    "SERVER_OWNERSHIP_UNKNOWN", str(exc),
                    data={"session_id": session_id, "server_handle_preserved": True,
                          "server_stopped": False}, execution_state_unknown=True,
                ), "UNKNOWN")
            other_binding = self._other_session_binding_uses_owned_server(key, identity)
            if other_binding is not False:
                unknown = other_binding is None
                return self._finish(record, self._error(
                    "SERVER_ENDPOINT_BINDING_UNKNOWN" if unknown else "SERVER_ENDPOINT_IN_USE",
                    "other known session bindings could not be ruled out; owned Server remains running"
                    if unknown else "another known session may still use this owned Server",
                    data={"session_id": session_id, "server_stopped": False,
                          "engine_dispatched": False},
                    execution_state_unknown=unknown, safe_retry=not unknown,
                ), "UNKNOWN" if unknown else "FAILED")
            stopping = self._updated_session_lifecycle(
                lifecycle, state="STOPPING", client_state=lifecycle["client_state"],
                worker_instance_id=lifecycle["worker_instance_id"],
                worker_epoch=lifecycle["worker_epoch"],
                server_instance_id=lifecycle["server_instance_id"],
            )
            lifecycle = self.session_lifecycle.save(stopping, expected_revision=lifecycle["revision"])
            try:
                stop_evidence = self.session_server_launcher.stop(handle)
                if (not isinstance(stop_evidence, Mapping) or stop_evidence.get("server_stopped") is not True
                        or stop_evidence.get("child_reaped") is not True
                        or stop_evidence.get("listener_absent") is not True
                        or stop_evidence.get("pid") != identity.pid
                        or stop_evidence.get("birth") != identity.birth):
                    raise OwnedServerError("Server stop adapter returned incomplete exact-exit evidence",
                                           handle=handle, uncertain=True)
                stopped = self._updated_session_lifecycle(
                    lifecycle, state="STOPPED", client_state=lifecycle["client_state"],
                    worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                    server_instance_id=None, server_state="STOPPED", server_ownership="unknown",
                    server_process_identity=None,
                )
                lifecycle = self.session_lifecycle.save(stopped, expected_revision=lifecycle["revision"])
                self._session_server_handles.pop(key, None)
                self._session_runtime_configs.pop(key, None)
                self._session_process_identities.pop(key, None)
            except Exception as exc:
                try:
                    unknown = self._updated_session_lifecycle(
                        lifecycle, state="UNKNOWN", client_state=lifecycle["client_state"],
                        worker_instance_id=lifecycle["worker_instance_id"],
                        worker_epoch=lifecycle["worker_epoch"],
                        server_instance_id=lifecycle["server_instance_id"],
                    )
                    self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
                except Exception:
                    pass
                return self._finish(record, self._error(
                    "EXECUTION_STATE_UNKNOWN", "owned Server stop/reap could not be fully confirmed; exact handle retained",
                    data={"session_id": session_id, "server_handle_preserved": True,
                          "server_stopped": False, "cause_type": type(exc).__name__},
                    execution_state_unknown=True,
                ), "UNKNOWN")
            digest = authorization_digest
            return self._finish(record, {"success": True, "data": {
                "project_id": project_id, "session_id": session_id,
                "state": "STOPPED", "server_stopped": True,
                "stop_evidence": dict(stop_evidence),
                "authorization_ref_sha256": digest,
                "worker_client_state": lifecycle["client_state"],
            }}, "SUCCEEDED")

    def _dispatch_session_connect(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        from ._execution_contract import canonical_request_hash

        project_id = routed["project_id"]
        idempotency_key = routed["idempotency_key"]
        request_id = routed.get("request_id") or execution.get("request_id") or str(uuid4())
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "session.connect request_id must be a non-empty string")
        requested_session_id = routed.get("session_id")
        if requested_session_id is not None and (
            not isinstance(requested_session_id, str) or not requested_session_id.strip()
        ):
            raise ExecutionContractError("INVALID_REQUEST", "session.connect session_id must be a non-empty string")
        endpoint = routed["endpoint"]
        host, port = endpoint.get("host"), endpoint.get("port")
        if not isinstance(host, str) or not host.strip() or type(port) is not int or not 1 <= port <= 65535:
            raise ExecutionContractError("INVALID_REQUEST", "session.connect endpoint is malformed")
        credentials_ref = routed.get("credentials_ref")
        if credentials_ref is not None and (not isinstance(credentials_ref, str) or not credentials_ref.strip()):
            raise ExecutionContractError("INVALID_REQUEST", "credentials_ref must be a non-empty trusted reference")

        self.project_authority.authorize_operation(project_id, "project_write")
        timeouts = self._timeouts(execution)
        semantic_arguments = {key: value for key, value in routed.items()
                              if key not in {"idempotency_key", "request_id", "credentials_ref"}}
        if credentials_ref is not None:
            semantic_arguments["credentials_ref_sha256"] = hashlib.sha256(credentials_ref.encode("utf-8")).hexdigest()
        request_hash = canonical_request_hash(
            "session.connect", semantic_arguments, None, None,
            project_id=project_id,
        )
        session_id = requested_session_id or ("session-" + uuid4().hex)
        metadata = {
            "project_id": project_id,
            "session_id": session_id,
            "runtime_id": routed["runtime_id"],
            "endpoint": {"host": host, "port": port},
            "credentials_configured": credentials_ref is not None,
        }
        try:
            record, reused = self.store.begin(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation="session.connect",
                metadata=metadata,
                timeouts=timeouts,
            )
        except IdempotencyConflict as exc:
            raise ExecutionContractError("IDEMPOTENCY_CONFLICT", str(exc)) from exc
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(record, session_id=record.get("metadata", {}).get("session_id"))

        session_id = record["metadata"]["session_id"]
        project_record = self.project_authority.get_project(project_id)
        project_root = Path(project_record["workspace"]).resolve(strict=True)
        with self._session_connect_lock:
            if self._closing:
                return self._finish(record, self._error(
                    "CONTROL_DAEMON_CLOSING",
                    "session connect was admitted before shutdown but reached the birth gate after close began",
                    data={"project_id": project_id, "session_id": session_id,
                          "engine_dispatched": False, "worker_birth_performed": False},
                    safe_retry=False,
                ), "FAILED")
            key = (project_id, session_id)
            existing_lifecycle = None
            owned_server_handle = None
            owned_process = None
            server_ownership = "shared"
            server_state = "SHARED"
            server_process_record = None
            if requested_session_id is not None:
                try:
                    existing_lifecycle = self.session_lifecycle.get(project_id, session_id)
                except SessionLifecycleProjectConflict as exc:
                    return self._finish(record, self._error("PROJECT_IDENTITY_MISMATCH", str(exc)), "FAILED")
                if existing_lifecycle is None:
                    return self._finish(record, self._error(
                        "SESSION_NOT_FOUND", "explicit session_id must name a prior session.start lifecycle",
                        data={"session_id": session_id, "engine_dispatched": False}, safe_retry=False,
                    ), "FAILED")
                if (existing_lifecycle.get("server_ownership") != "mcp_managed"
                        or existing_lifecycle.get("server_state") != "MCP_MANAGED"
                        or existing_lifecycle.get("runtime_id") != routed["runtime_id"]
                        or existing_lifecycle.get("endpoint") != {"host": host, "port": port}
                        or existing_lifecycle.get("state") != "DISCONNECTED"
                        or existing_lifecycle.get("client_state") != "DISCONNECTED"):
                    return self._finish(record, self._error(
                        "SERVER_OWNERSHIP_MISMATCH",
                        "explicit session_id does not identify a disconnected, matching MCP-managed endpoint",
                        data={"session_id": session_id, "engine_dispatched": False}, safe_retry=False,
                    ), "FAILED")
                if key in self._session_worker_handles:
                    return self._finish(record, self._error(
                        "WORKER_BINDING_UNKNOWN", "prior Worker handle must be reconnected or retired before a new attach",
                        data={"session_id": session_id, "engine_dispatched": False,
                              "worker_handle_preserved": True}, safe_retry=False,
                    ), "FAILED")
                try:
                    owned_server_handle, owned_process = self._owned_server_handle(
                        project_id, session_id, existing_lifecycle,
                    )
                    server_ownership = "mcp_managed"
                    server_state = "MCP_MANAGED"
                    server_process_record = dict(existing_lifecycle["server_process_identity"])
                except OwnedServerError as exc:
                    return self._finish(record, self._error(
                        "SERVER_OWNERSHIP_UNKNOWN", str(exc),
                        data={"session_id": session_id, "engine_dispatched": False,
                              "server_handle_preserved": True}, execution_state_unknown=True,
                    ), "UNKNOWN")
            active = [row for row in self.session_lifecycle.list_for_project(project_id)
                      if row.get("endpoint") == {"host": host, "port": port}
                      and row.get("session_id") != session_id
                      and row.get("state") in {"CONNECTING", "CONNECTED", "UNKNOWN", "STOPPING"}]
            if requested_session_id is None:
                owned_targets = [row for row in self.session_lifecycle.list_for_project(project_id)
                                 if row.get("endpoint") == {"host": host, "port": port}
                                 and row.get("server_ownership") == "mcp_managed"
                                 and row.get("state") not in {"STOPPED", "LOST"}]
                if owned_targets:
                    return self._finish(record, self._error(
                        "SESSION_ID_REQUIRED", "attach to a session.start Server with its exact session_id",
                        data={"session_id": owned_targets[0]["session_id"], "engine_dispatched": False},
                        safe_retry=False,
                    ), "FAILED")
            owned_conflict = self._owned_server_endpoint_conflict(project_id, session_id, host, port)
            if owned_conflict is not False:
                unknown = owned_conflict is None
                return self._finish(record, self._error(
                    "SERVER_ENDPOINT_OWNERSHIP_UNKNOWN" if unknown else "SERVER_ENDPOINT_OWNERSHIP_CONFLICT",
                    "daemon could not prove that this endpoint is independent of an owned Server"
                    if unknown else "endpoint is reserved by another daemon-owned session",
                    data={"engine_dispatched": False, "worker_birth_performed": False},
                    execution_state_unknown=unknown, safe_retry=False,
                ), "UNKNOWN" if unknown else "FAILED")
            if active:
                result = self._error("SESSION_ALREADY_ACTIVE", "an active or uncertain session already targets this endpoint",
                                     data={"session_id": session_id, "existing_session_id": active[0]["session_id"],
                                           "engine_dispatched": False}, safe_retry=False)
                self.store.update_job(record["job_id"], "RUNNING")
                return self._finish(record, result, "FAILED")

            self.store.update_job(record["job_id"], "RUNNING", {"session_id": session_id, "engine_dispatched": False})
            self.store.add_event(record["job_id"], "RUNNING", {"operation_id": record["operation_id"], "session_id": session_id})
            if existing_lifecycle is None:
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="CONNECTING",
                    runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                    client_state="DISCONNECTED", server_state="SHARED", server_ownership="shared",
                ))
            else:
                lifecycle = self.session_lifecycle.save(self._updated_session_lifecycle(
                    existing_lifecycle, state="CONNECTING", client_state="DISCONNECTED",
                    worker_instance_id=existing_lifecycle.get("worker_instance_id"),
                    worker_epoch=None, server_instance_id=None,
                ), expected_revision=existing_lifecycle["revision"])

            def connection_lifecycle(state: str, client_state: str, *, worker_id=None,
                                     epoch=None, server_id=None, health=None):
                return new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state=state,
                    runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                    client_state=client_state, server_state=server_state,
                    server_ownership=server_ownership,
                    worker_instance_id=worker_id, worker_epoch=epoch,
                    server_instance_id=server_id,
                    server_process_identity=server_process_record,
                    health=health,
                )
            backend = None
            worker = None
            attached = None
            dispatched = False
            try:
                self.project_authority.authorize_operation(project_id, "project_write")
                runtime = self._session_runtime_configs.get(key) if owned_server_handle is not None else None
                if runtime is None:
                    if owned_server_handle is not None:
                        raise ExecutionContractError(
                            "RUNTIME_CONFIGURATION_REQUIRED",
                            "daemon no longer retains the exact runtime configuration for this owned Server",
                        )
                    runtime = self._resolve_session_runtime(routed["runtime_id"], project_id, session_id, project_root)
                self._session_runtime_configs[(project_id, session_id)] = runtime
                credentials = self._resolve_session_credentials(credentials_ref)
                self._session_credentials_refs[(project_id, session_id)] = credentials_ref
                private_home = session_state_directory(runtime.session_state_root, project_id, session_id)
                backend = ManagedBackend(
                    private_home / "backend", self.store,
                    registry=self._default_backend.registry,
                    project_root=project_root,
                    session_worker_factory=self.session_worker_factory,
                    session_peer_observer=self.session_peer_observer,
                    host_permission_ceiling=self._default_backend.host_permission_ceiling,
                )
                self._session_backends[(project_id, session_id)] = backend

                def worker_event(event):
                    self.store.add_event(record["job_id"], "worker_request", self._redact_session_worker_event(event))

                attached = backend.connect_session(
                    runtime=runtime, project_id=project_id, session_id=session_id,
                    host=host, port=port, operation_id=record["operation_id"],
                    request_id=request_id, event_callback=worker_event,
                    credentials=credentials, rpc_timeout_s=timeouts["rpc_timeout_s"],
                    project_permissions=project_record.get("policy", {}).get("permissions", []),
                    server_ownership=server_ownership, owned_process=owned_process,
                )
                dispatched = True
                worker = attached["worker"]
                self._session_worker_handles[(project_id, session_id)] = worker
                self._remember_session_worker_child((project_id, session_id), worker)
                peer = attached["peer"]
                reply = attached["reply"]
                identity = attached["worker_identity"]
                endpoint_identity = SessionEndpointIdentity(
                    host, port, reply["generation"], observed_peer=peer, owned_process=owned_process,
                )
                context = SessionRuntimeContext(
                    project_id=project_id, session_id=session_id, project_root=project_root,
                    runtime=runtime, endpoint=endpoint_identity, backend=backend,
                    worker_instance_id=reply["instance_id"], worker=worker,
                    service=backend.service, client=worker.client(),
                    server_handle=owned_server_handle.process if owned_server_handle is not None else None,
                    process_identity=owned_process,
                    remote_client_factory=worker.client, server_ownership=server_ownership,
                    client_connected=True, connected_host=host, connected_port=port,
                    server_started_by_mcp=(server_ownership == "mcp_managed"),
                    health_snapshot={"status": "HEALTHY", "source": "worker-connect-reply+observed-peer"},
                )
                self.session_registry.register(context)
                lifecycle = self.session_lifecycle.save(connection_lifecycle(
                    "CONNECTED", "CONNECTED", worker_id=reply["instance_id"],
                    epoch=reply["generation"], server_id=attached["server_instance_id"],
                    health={"status": "HEALTHY", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "source": "worker-connect-reply+observed-peer"},
                ), expected_revision=lifecycle["revision"])
                result = {"success": True, "data": {
                    "project_id": project_id, "session_id": session_id,
                    "runtime_id": runtime.runtime_id, "endpoint": {"host": host, "port": port},
                    "observed_peer": {"address": peer.address, "port": peer.port},
                    "worker_instance_id": reply["instance_id"], "worker_epoch": reply["generation"],
                    "server_instance_id": attached["server_instance_id"],
                    "remote_engine_version": reply["engine_version"],
                    "remote_engine_build": identity["remote_engine_build"],
                    "remote_engine_build_source": identity["remote_engine_build_source"],
                    "server_ownership": server_ownership, "credentials_configured": credentials_ref is not None,
                }}
                return self._finish(record, result, "SUCCEEDED")
            except SessionConnectFailure as exc:
                worker = exc.worker or worker
                dispatched = dispatched or exc.dispatched
                if worker is not None:
                    self._session_worker_handles[(project_id, session_id)] = worker
                runtime_meta = exc.runtime_metadata or {}
                remote_reply = exc.reply or {}
                self._bind_session_job_to_worker(
                    record, project_id=project_id, session_id=session_id,
                    worker_instance_id=remote_reply.get("instance_id") or runtime_meta.get("instance_id"),
                    worker_epoch=remote_reply.get("generation") or runtime_meta.get("generation"),
                )
                uncertain = exc.uncertain
                retirement_error = None
                worker_retired = worker is None
                if not uncertain and worker is not None:
                    try:
                        # PersistentJavaWorker.close() returns only after its
                        # exact child has exited. A raised close leaves the
                        # handle live/unknown and must block any replacement.
                        worker.close()
                        worker_retired = True
                    except Exception as close_exc:
                        retirement_error = close_exc
                        uncertain = True
                if uncertain:
                    state = "UNKNOWN"
                    client_state = "CONNECTED" if remote_reply.get("connected") is True or runtime_meta.get("connected") is True else "UNKNOWN"
                    worker_id = remote_reply.get("instance_id") or runtime_meta.get("instance_id")
                    epoch = remote_reply.get("generation") or runtime_meta.get("generation")
                    server_id = None
                    if remote_reply.get("connected") is True and isinstance(remote_reply.get("engine_version"), str):
                        peer = remote_reply.get("observed_peer")
                        if isinstance(peer, Mapping):
                            try:
                                server_id = ManagedBackend._session_server_instance_id(
                                    CanonicalSocket(peer["address"], peer["port"]), remote_reply,
                                )
                            except Exception:
                                server_id = None
                    lifecycle = self.session_lifecycle.save(connection_lifecycle(
                        state, client_state,
                        worker_id=worker_id if isinstance(worker_id, str) else None,
                        epoch=epoch if type(epoch) is int and epoch > 0 else None,
                        server_id=server_id,
                    ), expected_revision=lifecycle["revision"])
                    failure_code = exc.code if exc.uncertain else "EXECUTION_STATE_UNKNOWN"
                    failure_message = (str(exc) if exc.uncertain else
                                       "session Worker retirement could not be confirmed after a pre-connect failure")
                    result = self._error(failure_code, failure_message, data={
                        "project_id": project_id, "session_id": session_id,
                        "state": "UNKNOWN", "engine_dispatched": dispatched,
                        "safe_retry": False, "worker_handle_preserved": worker is not None,
                        **({"worker_close_failure_type": type(retirement_error).__name__}
                           if retirement_error is not None else {}),
                    }, safe_retry=False, execution_state_unknown=True)
                    return self._finish(record, result, "UNKNOWN")

                lifecycle = self.session_lifecycle.save(connection_lifecycle(
                    "DISCONNECTED", "DISCONNECTED",
                ), expected_revision=lifecycle["revision"])
                if worker is not None:
                    if worker_retired:
                        self._session_worker_handles.pop((project_id, session_id), None)
                result = self._error(exc.code, str(exc), data={
                    "project_id": project_id, "session_id": session_id,
                    "state": "DISCONNECTED", "engine_dispatched": dispatched,
                    "safe_retry": exc.safe_retry,
                }, safe_retry=exc.safe_retry)
                return self._finish(record, result, "FAILED")
            except ExecutionContractError as exc:
                if dispatched and attached is not None:
                    worker = attached["worker"]
                    self._session_worker_handles[(project_id, session_id)] = worker
                    reply = attached["reply"]
                    self._bind_session_job_to_worker(
                        record, project_id=project_id, session_id=session_id,
                        worker_instance_id=reply.get("instance_id"),
                        worker_epoch=reply.get("generation"),
                    )
                    lifecycle = self.session_lifecycle.save(connection_lifecycle(
                        "UNKNOWN", "CONNECTED", worker_id=reply.get("instance_id"),
                        epoch=reply.get("generation"), server_id=attached.get("server_instance_id"),
                    ), expected_revision=lifecycle["revision"])
                    result = self._error(
                        "EXECUTION_STATE_UNKNOWN",
                        "Worker attached, but durable session registration failed; preserve this Worker for reconciliation",
                        data={"project_id": project_id, "session_id": session_id,
                              "state": "UNKNOWN", "engine_dispatched": True,
                              "worker_handle_preserved": True, "cause_code": exc.code},
                        execution_state_unknown=True,
                    )
                    return self._finish(record, result, "UNKNOWN")
                lifecycle = self.session_lifecycle.save(connection_lifecycle(
                    "DISCONNECTED", "DISCONNECTED",
                ), expected_revision=lifecycle["revision"])
                result = self._exception(exc)
                return self._finish(record, result, "FAILED")
            except Exception as exc:
                if worker is not None:
                    self._session_worker_handles[(project_id, session_id)] = worker
                if dispatched:
                    try:
                        runtime_meta = worker.runtime_metadata() if worker is not None else {}
                    except Exception:
                        runtime_meta = {}
                    self._bind_session_job_to_worker(
                        record, project_id=project_id, session_id=session_id,
                        worker_instance_id=runtime_meta.get("instance_id"),
                        worker_epoch=runtime_meta.get("generation"),
                    )
                    lifecycle = self.session_lifecycle.save(connection_lifecycle(
                        "UNKNOWN", "UNKNOWN",
                    ), expected_revision=lifecycle["revision"])
                    result = self._error("EXECUTION_STATE_UNKNOWN", "session connect completed without durable identity confirmation",
                                         data={"session_id": session_id, "engine_dispatched": True,
                                               "worker_handle_preserved": worker is not None},
                                         execution_state_unknown=True)
                    return self._finish(record, result, "UNKNOWN")
                self._log_exception()
                lifecycle = self.session_lifecycle.save(connection_lifecycle(
                    "DISCONNECTED", "DISCONNECTED",
                ), expected_revision=lifecycle["revision"])
                result = self._error("RUNTIME_CONFIGURATION_REQUIRED", "session Worker configuration failed before connect dispatch",
                                     data={"session_id": session_id, "engine_dispatched": False},
                                     cause_type=type(exc).__name__, safe_retry=True)
                return self._finish(record, result, "FAILED")

    def _begin_session_mutation(self, operation: str, routed: dict[str, Any],
                                execution: dict[str, Any]):
        """Persist one project/session lifecycle mutation before Worker RPC."""
        project_id, session_id = routed["project_id"], routed["session_id"]
        idempotency_key = routed["idempotency_key"]
        request_id = routed.get("request_id") or execution.get("request_id") or str(uuid4())
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", f"{operation} request_id must be a non-empty string")
        persisted_arguments = {
            key: value for key, value in routed.items()
            if key not in {"idempotency_key", "request_id", "authorization_ref"}
        }
        semantic_arguments = dict(persisted_arguments)
        if "authorization_ref" in routed:
            reference = routed["authorization_ref"]
            if not isinstance(reference, str) or not reference.strip() or len(reference) > 512:
                raise ExecutionContractError("AUTHORIZATION_REQUIRED", "authorization_ref is malformed")
            persisted_arguments["authorization_ref_sha256"] = hashlib.sha256(reference.encode("utf-8")).hexdigest()
            semantic_arguments["authorization_ref_sha256"] = persisted_arguments["authorization_ref_sha256"]
        request_hash = canonical_request_hash(
            operation, semantic_arguments, None, None, project_id=project_id,
        )
        try:
            record, reused = self.store.begin(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                metadata={
                    "operation": operation,
                    "project_id": project_id,
                    "session_id": session_id,
                    "arguments": persisted_arguments,
                    "execution": {"project_id": project_id, "session_id": session_id},
                },
                timeouts=self._timeouts(execution),
            )
        except IdempotencyConflict as exc:
            raise ExecutionContractError("IDEMPOTENCY_CONFLICT", str(exc)) from exc
        return record, reused

    def _start_session_mutation_record(self, record, *, operation: str, session_id: str) -> None:
        self.store.update_job(record["job_id"], "RUNNING", {
            "control_plane": True,
            "operation": operation,
            "session_id": session_id,
            "engine_dispatched": False,
        })
        self.store.add_event(record["job_id"], "RUNNING", {
            "operation_id": record["operation_id"],
            "session_id": session_id,
            "engine_dispatched": False,
        })

    def _session_jobs_for_project(self, project_id: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.store.list_jobs(offset=offset, limit=1000, project_id=project_id)
            jobs.extend(page)
            if len(page) < 1000:
                return jobs
            offset += len(page)

    @staticmethod
    def _job_belongs_to_session(job: Mapping[str, Any], project_id: str, session_id: str) -> bool:
        metadata = job.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        binding = metadata.get("runtime_binding")
        if isinstance(binding, Mapping) and (
            binding.get("project_id") == project_id and binding.get("session_id") == session_id
        ):
            return True
        for value in (metadata, metadata.get("execution"), metadata.get("arguments")):
            if isinstance(value, Mapping) and (
                value.get("project_id") == project_id and value.get("session_id") == session_id
            ):
                return True
        return False

    def _session_has_unresolved_jobs(self, project_id: str, session_id: str,
                                     *, excluding_operation_id: str | None = None) -> list[dict[str, Any]]:
        return [
            job for job in self._session_jobs_for_project(project_id)
            if job.get("operation_id") != excluding_operation_id
            and self._job_belongs_to_session(job, project_id, session_id)
            and job.get("status") not in TERMINAL
            and not self._job_quiescence_proven(job)
        ]

    def _session_recovery_resolution_is_valid(self, job: Mapping[str, Any]) -> bool:
        """Verify the append-only proof used to release one session job gate."""
        job_id = job.get("job_id")
        operation_id = job.get("operation_id")
        metadata = job.get("metadata")
        if (not isinstance(job_id, str) or not isinstance(operation_id, str)
                or not isinstance(metadata, Mapping)
                or metadata.get("reconciled_quiescent") is not True):
            return False
        binding = metadata.get("runtime_binding")
        execution = metadata.get("execution")
        if not isinstance(binding, Mapping) or binding.get("kind") != "registered_session" or not isinstance(execution, Mapping):
            return False
        operation = job.get("operation")
        if (job.get("status") not in {"UNKNOWN", "RECONCILING"}
                or not isinstance(operation, Mapping)
                or operation.get("status") not in {"UNKNOWN", "RECONCILING"}):
            return False
        result = job.get("result")
        if not isinstance(result, Mapping):
            return False
        try:
            current_result_digest = session_recovery_evidence_sha256({
                "source_result": self._redact_session_worker_event(dict(result)),
            })
        except Exception:
            return False
        try:
            event = self.store.session_recovery_resolution(job_id)
        except Exception:
            return False
        if not isinstance(event, Mapping) or not isinstance(event.get("metadata"), Mapping):
            return False
        proof = dict(event["metadata"])
        supplied_digest = proof.pop("evidence_sha256", None)
        if (not isinstance(supplied_digest, str) or len(supplied_digest) != 64
                or session_recovery_evidence_sha256(proof) != supplied_digest):
            return False
        pointer = metadata.get("session_recovery_resolution")
        proof_binding = proof.get("worker_binding")
        source_ref = execution.get("model_ref")
        return bool(
            isinstance(pointer, Mapping)
            and pointer.get("event_id") == event.get("id")
            and pointer.get("evidence_sha256") == supplied_digest
            and proof.get("schema_version") == 1
            and proof.get("source_job_id") == job_id
            and proof.get("source_operation_id") == operation_id
            and proof.get("source_status") == job.get("status")
            and proof.get("source_operation_status") == operation.get("status")
            and proof.get("source_result_sha256") == current_result_digest
            and proof.get("source_status") in {"UNKNOWN", "RECONCILING"}
            and proof.get("resolution_scope") == "WORKER_REQUEST_QUIESCENCE_ONLY"
            and proof.get("replay_performed") is False
            and proof.get("new_worker_created") is False
            and proof.get("outcome_resolution") == "UNVERIFIED_HISTORICAL_UNKNOWN"
            and isinstance(proof_binding, Mapping)
            and all(proof_binding.get(key) == binding.get(key) for key in (
                "kind", "project_id", "session_id", "worker_instance_id", "worker_epoch",
            ))
            and proof.get("model_ref") == source_ref
            and isinstance(proof.get("model_revision"), Mapping)
            and proof.get("model_revision", {}).get("observed_revision") == pointer.get("model_revision")
            and pointer.get("worker_binding") == proof_binding
        )

    def _job_quiescence_proven(self, job: Mapping[str, Any]) -> bool:
        metadata = job.get("metadata") if isinstance(job, Mapping) else None
        binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
        if isinstance(binding, Mapping) and binding.get("kind") == "registered_session":
            # `job.reconcile` readback alone does not prove model revision or
            # original Worker epoch. Session-bound UNKNOWN jobs require the
            # stronger append-only `session.recover` event.
            return self._session_recovery_resolution_is_valid(job)
        return isinstance(metadata, Mapping) and metadata.get("reconciled_quiescent") is True

    def _session_job_events(self, job_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.store.events(job_id, offset=offset, limit=1000)
            events.extend(page)
            if len(page) < 1000:
                return events
            offset += len(page)

    def _session_job_worker_submissions(self, job: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        job_id, operation_id = job.get("job_id"), job.get("operation_id")
        if not isinstance(job_id, str) or not isinstance(operation_id, str):
            return [], "SOURCE_JOB_IDENTITY_MALFORMED"
        requests: dict[str, dict[str, Any]] = {}
        for event in self._session_job_events(job_id):
            if event.get("event") != "worker_request":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, Mapping) or metadata.get("phase") != "submitted":
                continue
            event_operation_id = metadata.get("operation_id")
            # A request id without its original daemon operation binding is
            # insufficient: otherwise a reused/corrupt event could make a
            # terminal status from another job look like this job's result.
            if event_operation_id != operation_id:
                return [], "WORKER_REQUEST_OPERATION_MISMATCH"
            request_id = metadata.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                return [], "WORKER_REQUEST_ID_MISSING"
            kind = metadata.get("kind")
            request_hash = metadata.get("request_hash")
            candidate = {"request_id": request_id, "kind": kind,
                         "request_hash": request_hash,
                         "source_operation_id": operation_id,
                         "operation_id_source": "source_job_event"}
            previous = requests.get(request_id)
            if previous is not None and previous != candidate:
                return [], "WORKER_REQUEST_DUPLICATE_CONFLICT"
            requests[request_id] = candidate
        if not requests:
            return [], "ORIGINAL_WORKER_REQUESTS_UNAVAILABLE"
        return list(requests.values()), None

    def _read_session_job_requests(self, worker: Any, job: Mapping[str, Any],
                                   recovery_record: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        submissions, reason = self._session_job_worker_submissions(job)
        if reason is not None:
            return {"requests": [], "terminal": False, "reason": reason}
        operation_context = getattr(worker, "operation_context", None)
        scope = (operation_context(recovery_record["operation_id"],
                                  on_request_event=self._session_worker_event_callback(recovery_record))
                 if callable(operation_context) else nullcontext())
        observations = []
        with scope:
            for submitted in submissions:
                request_id = submitted["request_id"]
                try:
                    reply = worker.status(request_id, timeout_s=timeout)
                except Exception as exc:
                    observations.append({"request_id": request_id, "kind": submitted.get("kind"),
                                         "status": "UNKNOWN", "terminal": False,
                                         "error_type": type(exc).__name__})
                    continue
                if not isinstance(reply, Mapping):
                    observations.append({"request_id": request_id, "kind": submitted.get("kind"),
                                         "status": "UNKNOWN", "terminal": False,
                                         "error_type": "MALFORMED_REPLY"})
                    continue
                status = reply.get("status")
                exact_id = reply.get("request_id") == request_id
                exact_kind = submitted.get("kind") in (None, "") or reply.get("type") == submitted.get("kind")
                terminal = status in {"SUCCEEDED", "FAILED"} and exact_id and exact_kind
                terminal = terminal and (
                    (status == "SUCCEEDED" and "result" in reply)
                    or (status == "FAILED" and isinstance(reply.get("failure"), Mapping))
                )
                safe_reply = self._redact_session_worker_event(dict(reply))
                observations.append({
                    "request_id": request_id,
                    "kind": submitted.get("kind"),
                    "source_operation_id": submitted.get("source_operation_id"),
                    "status": status if isinstance(status, str) else "UNKNOWN",
                    "terminal": bool(terminal),
                    "request_id_match": exact_id,
                    "request_type_match": exact_kind,
                    "reply_sha256": session_recovery_evidence_sha256({"worker_reply": safe_reply}),
                    "operation_id_source": "source_job_event",
                })
        return {"requests": observations,
                "terminal": bool(observations) and all(item.get("terminal") is True for item in observations),
                "reason": None}

    def _session_recovery_model_evidence(
        self, job: Mapping[str, Any], context: SessionRuntimeContext,
        lifecycle: Mapping[str, Any], worker: Any, worker_metadata: Mapping[str, Any],
        request_observations: Mapping[str, Any], recovery_record: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Build a quiescence-only proof for one exact ModelRef-bound UNKNOWN job."""
        from ._execution_contract import model_ref_from_mapping

        job_id, source_operation_id = job.get("job_id"), job.get("operation_id")
        metadata = job.get("metadata")
        binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
        execution = metadata.get("execution") if isinstance(metadata, Mapping) else None
        if not isinstance(binding, Mapping) or binding.get("kind") != "registered_session":
            return None, "WORKER_BINDING_UNKNOWN"
        if not isinstance(execution, Mapping):
            return None, "MODEL_REF_BINDING_MISSING"
        if (binding.get("project_id") != context.project_id
                or binding.get("session_id") != context.session_id
                or binding.get("worker_instance_id") != context.worker_instance_id
                or binding.get("worker_epoch") != context.worker_epoch
                or lifecycle.get("worker_instance_id") != binding.get("worker_instance_id")
                or lifecycle.get("worker_epoch") != binding.get("worker_epoch")
                or worker_metadata.get("instance_id") != binding.get("worker_instance_id")
                or worker_metadata.get("generation") != binding.get("worker_epoch")):
            return None, "ORIGINAL_WORKER_EPOCH_MISMATCH"
        if (context.worker is not worker or context.backend is None
                or context.service is None or context.backend.worker is not worker
                or context.backend.service is not context.service):
            return None, "LIVE_SESSION_RUNTIME_CONTEXT_MISMATCH"
        if lifecycle.get("state") != "CONNECTED" or lifecycle.get("client_state") != "CONNECTED":
            return None, "SESSION_LIFECYCLE_NOT_CONFIRMED_CONNECTED"
        if request_observations.get("terminal") is not True:
            return None, str(request_observations.get("reason") or "ORIGINAL_WORKER_REQUEST_NOT_TERMINAL")
        if not isinstance(job_id, str) or not isinstance(source_operation_id, str):
            return None, "SOURCE_JOB_IDENTITY_MALFORMED"
        if job.get("status") not in {"UNKNOWN", "RECONCILING"}:
            return None, "SOURCE_JOB_NO_LONGER_UNRESOLVED"
        source_operation = job.get("operation")
        if (not isinstance(source_operation, Mapping)
                or source_operation.get("operation_id") != source_operation_id
                or source_operation.get("status") not in {"UNKNOWN", "RECONCILING"}):
            return None, "SOURCE_OPERATION_NO_LONGER_UNRESOLVED"
        raw_ref = execution.get("model_ref")
        expected_revision = execution.get("expected_revision")
        if not isinstance(raw_ref, Mapping):
            return None, "MODEL_REF_BINDING_MISSING"
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            return None, "EXPECTED_MODEL_REVISION_MISSING"
        try:
            model_ref = model_ref_from_mapping(dict(raw_ref))
        except Exception:
            return None, "MODEL_REF_BINDING_MALFORMED"
        if (model_ref.session_id != context.session_id
                or model_ref.server_instance_id != context.service.ledger.server_instance_id):
            return None, "MODEL_REF_SESSION_OR_SERVER_MISMATCH"

        original_result = job.get("result")
        original_execution = original_result.get("execution") if isinstance(original_result, Mapping) else None
        if (not isinstance(original_result, Mapping) or not isinstance(original_execution, Mapping)
                or original_execution.get("model_ref") != model_ref.as_dict()):
            return None, "ORIGINAL_MODEL_EXECUTION_EVIDENCE_MISSING"
        recorded_revision = original_execution.get("revision")
        recorded_dirty = original_execution.get("dirty")
        if (isinstance(recorded_revision, bool) or not isinstance(recorded_revision, int)
                or type(recorded_dirty) is not bool):
            return None, "ORIGINAL_MODEL_REVISION_EVIDENCE_MALFORMED"
        revision_delta = recorded_revision - expected_revision
        if not ((revision_delta == 0 and recorded_dirty is False)
                or (revision_delta == 1 and recorded_dirty is True)):
            return None, "MODEL_REVISION_TRANSITION_NOT_PROVEN"

        service = context.service
        try:
            state = service.ledger._state_for(model_ref)
        except Exception:
            return None, "LIVE_MODEL_REF_NOT_BOUND"
        if state.active_operation_id is not None or state.retired:
            return None, "MODEL_HAS_ACTIVE_OR_RETIRED_OPERATION"
        try:
            saved_session = self.store.get_metadata("sessions", context.session_id)
        except Exception:
            saved_session = None
        saved_identity = saved_session.get("worker_identity") if isinstance(saved_session, Mapping) else None
        saved_models = saved_session.get("models") if isinstance(saved_session, Mapping) else None
        saved_model = saved_models.get(model_ref.model_tag) if isinstance(saved_models, Mapping) else None
        if (not isinstance(saved_identity, Mapping)
                or saved_identity.get("worker_instance_id") != binding.get("worker_instance_id")
                or saved_identity.get("connection_epoch") != binding.get("worker_epoch")
                or not isinstance(saved_model, Mapping)
                or saved_model.get("ref") != model_ref.as_dict()
                or saved_model.get("revision") != recorded_revision
                or saved_model.get("dirty") is not recorded_dirty
                or saved_model.get("active_operation_id") is not None
                or saved_model.get("fingerprint") is None
                or isinstance(saved_model.get("external_event_counter"), bool)
                or not isinstance(saved_model.get("external_event_counter"), int)):
            return None, "DURABLE_MODEL_REVISION_BINDING_MISSING_OR_STALE"
        if (state.ref.as_dict() != saved_model.get("ref")
                or state.revision != saved_model.get("revision")
                or state.dirty is not saved_model.get("dirty")
                or state.active_operation_id != saved_model.get("active_operation_id")
                or state.fingerprint != saved_model.get("fingerprint")
                or state.external_event_counter != saved_model.get("external_event_counter")
                or state.observed_external_event_counter != saved_model.get("observed_external_event_counter")):
            return None, "LIVE_AND_DURABLE_MODEL_REVISION_STATE_DISAGREE"

        operation_context = getattr(worker, "operation_context", None)
        scope = (operation_context(recovery_record["operation_id"],
                                  on_request_event=self._session_worker_event_callback(recovery_record))
                 if callable(operation_context) else nullcontext())
        try:
            with scope:
                observed = service.inspect_evidence(model_ref)
        except Exception as exc:
            return None, f"MODEL_SNAPSHOT_READBACK_FAILED:{type(exc).__name__}"
        current_execution = observed.get("execution") if isinstance(observed, Mapping) else None
        snapshot = observed.get("model_snapshot") if isinstance(observed, Mapping) else None
        try:
            state_after = service.ledger._state_for(model_ref)
        except Exception:
            return None, "MODEL_REF_RETIRED_DURING_READBACK"
        if (not isinstance(current_execution, Mapping)
                or current_execution.get("model_ref") != model_ref.as_dict()
                or current_execution.get("revision") != recorded_revision
                or type(current_execution.get("dirty")) is not bool
                or not isinstance(snapshot, Mapping)
                or snapshot.get("model_tag") != model_ref.model_tag
                or snapshot.get("server_instance_id") != model_ref.server_instance_id
                or isinstance(snapshot.get("external_event_counter"), bool)
                or not isinstance(snapshot.get("external_event_counter"), int)
                or not isinstance(snapshot.get("fingerprint"), str)
                or not snapshot.get("fingerprint")):
            return None, "MODEL_SNAPSHOT_IDENTITY_OR_REVISION_MISMATCH"
        if (snapshot.get("external_event_counter") != saved_model.get("external_event_counter")
                or snapshot.get("fingerprint") != saved_model.get("fingerprint")
                or state_after.revision != recorded_revision
                or state_after.dirty is not recorded_dirty
                or state_after.external_event_counter != saved_model.get("external_event_counter")
                or state_after.fingerprint != saved_model.get("fingerprint")
                or state_after.active_operation_id is not None):
            return None, "MODEL_REVISION_OR_SNAPSHOT_CHANGED_AFTER_ORIGINAL_RESULT"

        try:
            worker_after = worker.runtime_metadata()
        except Exception as exc:
            return None, f"WORKER_IDENTITY_READBACK_FAILED:{type(exc).__name__}"
        if (not isinstance(worker_after, Mapping)
                or worker_after.get("instance_id") != binding.get("worker_instance_id")
                or worker_after.get("generation") != binding.get("worker_epoch")):
            return None, "WORKER_EPOCH_CHANGED_DURING_RECOVERY"
        # Do not persist an observed drift over the durable baseline.  Only
        # after the snapshot, local ledger and original revision all agree do
        # we refresh the existing state row, then verify that exact writeback.
        try:
            context.backend.persist()
        except Exception as exc:
            return None, f"MODEL_SNAPSHOT_PERSIST_FAILED:{type(exc).__name__}"
        try:
            persisted_session = self.store.get_metadata("sessions", context.session_id)
        except Exception:
            persisted_session = None
        persisted_identity = (persisted_session.get("worker_identity")
                              if isinstance(persisted_session, Mapping) else None)
        persisted_models = (persisted_session.get("models")
                            if isinstance(persisted_session, Mapping) else None)
        persisted_model = persisted_models.get(model_ref.model_tag) if isinstance(persisted_models, Mapping) else None
        if (not isinstance(persisted_identity, Mapping)
                or persisted_identity.get("worker_instance_id") != binding.get("worker_instance_id")
                or persisted_identity.get("connection_epoch") != binding.get("worker_epoch")
                or not isinstance(persisted_model, Mapping)
                or persisted_model.get("ref") != model_ref.as_dict()
                or persisted_model.get("revision") != recorded_revision
                or persisted_model.get("dirty") is not recorded_dirty
                or persisted_model.get("active_operation_id") is not None
                or persisted_model.get("fingerprint") != snapshot.get("fingerprint")
                or persisted_model.get("external_event_counter") != snapshot.get("external_event_counter")):
            return None, "DURABLE_MODEL_REVISION_CHANGED_DURING_RECOVERY"
        try:
            result_digest = session_recovery_evidence_sha256({
                "source_result": self._redact_session_worker_event(dict(original_result)),
            })
            snapshot_digest = session_recovery_evidence_sha256({
                "fingerprint": snapshot["fingerprint"],
                "external_event_counter": snapshot["external_event_counter"],
            })
        except Exception:
            return None, "RECOVERY_EVIDENCE_HASH_FAILED"
        error = original_result.get("error") if isinstance(original_result.get("error"), Mapping) else {}
        evidence = {
            "schema_version": 1,
            "resolution_scope": "WORKER_REQUEST_QUIESCENCE_ONLY",
            "session_recovery_operation_id": recovery_record["operation_id"],
            "source_job_id": job_id,
            "source_operation_id": source_operation_id,
            "source_status": job.get("status"),
            "source_operation_status": source_operation.get("status"),
            "source_result_sha256": result_digest,
            "original_unknown_reason": {
                "code": error.get("code") if isinstance(error.get("code"), str) else "UNKNOWN",
                "safe_retry": error.get("safe_retry") if type(error.get("safe_retry")) is bool else False,
            },
            "worker_binding": {key: binding.get(key) for key in (
                "kind", "project_id", "session_id", "worker_instance_id", "worker_epoch",
            )},
            "request_observations": list(request_observations.get("requests", [])),
            "model_ref": model_ref.as_dict(),
            "model_revision": {
                "expected_revision": expected_revision,
                "recorded_result_revision": recorded_revision,
                "observed_revision": current_execution.get("revision"),
                "dirty": current_execution.get("dirty"),
                "external_event_counter": snapshot.get("external_event_counter"),
                "snapshot_sha256": snapshot_digest,
                "snapshot_coverage": snapshot.get("coverage"),
                "cas_guarantee": snapshot.get("cas_guarantee"),
            },
            "admission_fence": "HELD_WITH_ZERO_ACCEPTED_TASKS",
            "worker_identity_rechecked_after_snapshot": True,
            "replay_performed": False,
            "new_worker_created": False,
            "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
        }
        return evidence, None

    def _bind_session_job_to_worker(self, record, *, project_id: str, session_id: str,
                                    worker_instance_id: Any, worker_epoch: Any) -> None:
        if (not isinstance(worker_instance_id, str) or not worker_instance_id
                or type(worker_epoch) is not int or worker_epoch < 1):
            return
        self.store.update_job(record["job_id"], "RUNNING", {
            "runtime_binding": {
                "kind": "registered_session",
                "project_id": project_id,
                "session_id": session_id,
                "worker_instance_id": worker_instance_id,
                "worker_epoch": worker_epoch,
            },
        })

    @staticmethod
    def _updated_session_lifecycle(prior: Mapping[str, Any], *, state: str,
                                   client_state: str, worker_instance_id: str | None = None,
                                   worker_epoch: int | None = None,
                                   server_instance_id: str | None = None,
                                   health: Mapping[str, Any] | None = None,
                                   server_state: str | None = None,
                                   server_ownership: str | None = None,
                                   server_process_identity: Any = _PRESERVE_SERVER_PROCESS_IDENTITY):
        process_identity_value = (
            prior["server_process_identity"]
            if server_process_identity is _PRESERVE_SERVER_PROCESS_IDENTITY
            else server_process_identity
        )
        return new_lifecycle_record(
            project_id=prior["project_id"],
            session_id=prior["session_id"],
            state=state,
            runtime_id=prior["runtime_id"],
            endpoint=prior["endpoint"],
            client_state=client_state,
            server_state=server_state if server_state is not None else prior["server_state"],
            server_ownership=server_ownership if server_ownership is not None else prior["server_ownership"],
            worker_instance_id=worker_instance_id if worker_instance_id is not None else prior["worker_instance_id"],
            worker_epoch=worker_epoch,
            server_instance_id=server_instance_id,
            server_process_identity=process_identity_value,
            health=health or {"status": "UNKNOWN", "observed_at": None, "source": None},
        )

    def _session_worker_event_callback(self, record):
        return lambda event: self.store.add_event(
            record["job_id"], "worker_request", self._redact_session_worker_event(event),
        )

    @staticmethod
    def _session_server_state(record: Mapping[str, Any]) -> str:
        return str(record.get("server_state") or "UNKNOWN")

    def _remember_session_worker_child(self, key: tuple[str, str], worker: Any) -> None:
        """Remember a live, exact child handle and birth after a verified attach."""
        process = getattr(worker, "_process", None)
        pid = getattr(process, "pid", None)
        if (process is None or type(pid) is not int or pid <= 1
                or not callable(getattr(process, "poll", None))):
            return
        try:
            if process.poll() is not None:
                return
            identity = process_identity(pid)
        except Exception:
            return
        birth = identity.get("start_epoch_ms") if isinstance(identity, Mapping) else None
        if (not isinstance(identity, Mapping) or identity.get("alive") is not True
                or type(birth) is not int or birth <= 0):
            return
        self._session_worker_child_identities[key] = {
            "worker": worker,
            "process": process,
            "pid": pid,
            "start_epoch_ms": birth,
        }

    def _session_worker_retirement_proof(self, key: tuple[str, str], worker: Any,
                                         lifecycle: Mapping[str, Any], *, connected: bool
                                         ) -> dict[str, Any] | None:
        """Preflight one exact Worker Popen/birth and connected epoch."""
        saved = self._session_worker_child_identities.get(key)
        if not isinstance(saved, Mapping) or saved.get("worker") is not worker:
            return None
        process = saved.get("process")
        pid = saved.get("pid")
        birth = saved.get("start_epoch_ms")
        if (process is None or type(pid) is not int or pid <= 1
                or type(birth) is not int or birth <= 0
                or getattr(worker, "_process", None) is not process
                or getattr(process, "pid", None) != pid
                or not callable(getattr(process, "poll", None))):
            return None
        try:
            if process.poll() is not None:
                return None
            current = process_identity(pid)
            metadata = worker.runtime_metadata()
        except Exception:
            return None
        if (not isinstance(current, Mapping) or current.get("alive") is not True
                or current.get("start_epoch_ms") != birth
                or not isinstance(metadata, Mapping)
                or metadata.get("instance_id") != lifecycle.get("worker_instance_id")
                or metadata.get("generation") != lifecycle.get("worker_epoch")
                or metadata.get("connected") is not connected):
            return None
        return {"process": process, "pid": pid, "start_epoch_ms": birth,
                "worker_instance_id": lifecycle.get("worker_instance_id"),
                "worker_epoch": lifecycle.get("worker_epoch")}

    def _retire_disconnected_session_worker(self, record: Mapping[str, Any],
                                            lifecycle: Mapping[str, Any], worker: Any,
                                            proof: Mapping[str, Any], *,
                                            disconnect_rpc_dispatched: bool) -> dict[str, Any]:
        """Close/wait one proven disconnected Worker, retaining UNKNOWN on doubt."""
        project_id, session_id = lifecycle["project_id"], lifecycle["session_id"]
        key = (project_id, session_id)
        process = proof["process"]
        pid, birth = proof["pid"], proof["start_epoch_ms"]
        cleanup_started = False
        try:
            current = self._session_worker_retirement_proof(key, worker, lifecycle, connected=False)
            if current is None or current["process"] is not process or current["start_epoch_ms"] != birth:
                raise RuntimeError("exact disconnected Worker identity changed before retirement")
            cleanup_started = True
            worker.close()
            # close() owns graceful/forced termination policy; this adapter
            # independently waits on the saved Popen object and verifies that
            # exact child, rather than trusting a return from close().
            process.wait(timeout=5.0)
            if process.poll() is None:
                raise RuntimeError("exact Worker child remained live after close/wait")
            if getattr(worker, "_process", None) not in (None, process):
                raise RuntimeError("Worker handle changed to a different child during retirement")
        except Exception as exc:
            try:
                unknown = self._updated_session_lifecycle(
                    lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                    worker_instance_id=lifecycle.get("worker_instance_id"),
                    worker_epoch=lifecycle.get("worker_epoch"),
                    server_instance_id=lifecycle.get("server_instance_id"),
                )
                self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
            except Exception:
                pass
            return self._finish(record, self._error(
                "EXECUTION_STATE_UNKNOWN", "Worker retirement outcome is unknown; exact handle retained",
                data={"project_id": project_id, "session_id": session_id,
                      "state": "UNKNOWN",
                      "engine_dispatched": disconnect_rpc_dispatched,
                      "disconnect_rpc_dispatched": disconnect_rpc_dispatched,
                      "worker_handle_preserved": True,
                      "worker_close_started": cleanup_started,
                      "cause_type": type(exc).__name__},
                safe_retry=False, execution_state_unknown=True,
            ), "UNKNOWN")

        try:
            retired = self._updated_session_lifecycle(
                lifecycle, state="DISCONNECTED", client_state="RETIRED",
                worker_instance_id=lifecycle.get("worker_instance_id"),
                worker_epoch=lifecycle.get("worker_epoch"),
                server_instance_id=None,
                health={"status": "UNKNOWN", "observed_at": None, "source": None},
            )
            self.session_lifecycle.save(retired, expected_revision=lifecycle["revision"])
        except Exception as exc:
            # The child is gone, but without a durable RETIRED transition the
            # session cannot safely be reconnected or reported as complete.
            try:
                unknown = self._updated_session_lifecycle(
                    lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                    worker_instance_id=lifecycle.get("worker_instance_id"),
                    worker_epoch=lifecycle.get("worker_epoch"),
                    server_instance_id=None,
                )
                self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
            except Exception:
                pass
            return self._finish(record, self._error(
                "EXECUTION_STATE_UNKNOWN", "Worker exited but retirement could not be durably recorded",
                data={"project_id": project_id, "session_id": session_id,
                      "state": "UNKNOWN",
                      "engine_dispatched": disconnect_rpc_dispatched,
                      "disconnect_rpc_dispatched": disconnect_rpc_dispatched,
                      "worker_handle_preserved": True,
                      "worker_close_started": True,
                      "worker_exit_confirmed": True,
                      "cause_type": type(exc).__name__},
                safe_retry=False, execution_state_unknown=True,
            ), "UNKNOWN")

        self._session_worker_handles.pop(key, None)
        self._session_worker_child_identities.pop(key, None)
        return self._finish(record, {"success": True, "data": {
            "project_id": project_id, "session_id": session_id,
            "state": "DISCONNECTED", "client_state": "RETIRED",
            "server_stopped": False, "worker_handle_preserved": False,
            "worker_retirement": {
                "status": "RETIRED",
                "worker_instance_id": proof["worker_instance_id"],
                "worker_epoch": proof["worker_epoch"],
                "process_identity": {"pid": pid, "start_epoch_ms": birth},
                "exact_popen_handle": True,
                "birth_identity_matched_before_close": True,
                "child_exit_confirmed": True, "child_reaped": True,
                "admission_fence": "RETIRED",
                "disconnect_rpc_dispatched": disconnect_rpc_dispatched,
                "worker_close_started": True,
            },
            "engine_dispatched": disconnect_rpc_dispatched,
            "disconnect_rpc_dispatched": disconnect_rpc_dispatched,
        }}, "SUCCEEDED")

    def _remove_session_context(self, project_id: str, session_id: str,
                                expected: SessionRuntimeContext | None = None) -> None:
        try:
            current = self.session_registry.get(project_id, session_id)
        except SessionContextMissing:
            return
        if expected is None or current is expected:
            self.session_registry.remove(project_id, session_id, expected=current)

    def _dispatch_session_disconnect(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        project_id, session_id = routed["project_id"], routed["session_id"]
        self.project_authority.authorize_operation(project_id, "project_write")
        record, reused = self._begin_session_mutation("session.disconnect", routed, execution)
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(record, session_id=session_id)
        self._start_session_mutation_record(record, operation="session.disconnect", session_id=session_id)
        with self._session_connect_lock:
            try:
                lifecycle = self.session_lifecycle.get(project_id, session_id)
            except SessionLifecycleProjectConflict as exc:
                return self._finish(record, self._error("PROJECT_IDENTITY_MISMATCH", str(exc)), "FAILED")
            if lifecycle is None:
                return self._finish(record, self._error("SESSION_NOT_FOUND", "session lifecycle record not found"), "FAILED")
            retire_worker = routed.get("retire_worker", False) is True
            if lifecycle["state"] == "DISCONNECTED":
                if not retire_worker:
                    return self._finish(record, {"success": True, "data": {
                        "project_id": project_id, "session_id": session_id,
                        "state": "DISCONNECTED", "already_disconnected": True,
                        "server_stopped": False,
                    }}, "SUCCEEDED")
                if lifecycle.get("client_state") == "RETIRED":
                    return self._finish(record, {"success": True, "data": {
                        "project_id": project_id, "session_id": session_id,
                        "state": "DISCONNECTED", "client_state": "RETIRED",
                        "already_retired": True, "server_stopped": False,
                        "worker_handle_preserved": False,
                    }}, "SUCCEEDED")
                key = (project_id, session_id)
                worker = self._session_worker_handles.get(key)
                if worker is None:
                    unknown = self._updated_session_lifecycle(
                        lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                        worker_instance_id=lifecycle["worker_instance_id"],
                        worker_epoch=lifecycle["worker_epoch"],
                        server_instance_id=lifecycle["server_instance_id"],
                    )
                    try:
                        self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
                    except Exception:
                        pass
                    return self._finish(record, self._error(
                        "WORKER_BINDING_UNKNOWN", "disconnected lifecycle has no exact retained Worker handle",
                        data={"session_id": session_id, "state": "UNKNOWN",
                              "worker_handle_preserved": False},
                        safe_retry=False, execution_state_unknown=True,
                    ), "UNKNOWN")
                proof = self._session_worker_retirement_proof(
                    key, worker, lifecycle, connected=False,
                )
                if proof is None:
                    return self._finish(record, self._error(
                        "WORKER_RETIREMENT_UNAVAILABLE",
                        "exact Worker Popen, birth, detached epoch, or liveness could not be verified",
                        data={"project_id": project_id, "session_id": session_id,
                              "state": "DISCONNECTED", "engine_dispatched": False,
                              "worker_handle_preserved": True},
                        safe_retry=False,
                    ), "FAILED")
                self._bind_session_job_to_worker(
                    record, project_id=project_id, session_id=session_id,
                    worker_instance_id=lifecycle["worker_instance_id"],
                    worker_epoch=lifecycle["worker_epoch"],
                )
                unresolved = [job for job in self._session_jobs_for_project(project_id)
                              if job.get("operation_id") != record["operation_id"]
                              and self._job_belongs_to_session(job, project_id, session_id)
                              and job.get("status") not in TERMINAL]
                if unresolved:
                    return self._finish(record, self._error(
                        "SESSION_BUSY", "active or UNKNOWN operations block Worker retirement",
                        data={"session_id": session_id, "engine_dispatched": False,
                              "blocking_job_ids": [item["job_id"] for item in unresolved]},
                        safe_retry=True,
                    ), "FAILED")
                return self._retire_disconnected_session_worker(
                    record, lifecycle, worker, proof,
                    disconnect_rpc_dispatched=False,
                )
            if lifecycle["state"] == "UNKNOWN":
                result = self._error("EXECUTION_STATE_UNKNOWN", "session is UNKNOWN; recover the original Worker before disconnecting",
                                     data={"session_id": session_id, "state": "UNKNOWN", "safe_retry": False},
                                     safe_retry=False, execution_state_unknown=True)
                return self._finish(record, result, "UNKNOWN")
            if lifecycle["state"] != "CONNECTED":
                return self._finish(record, self._error(
                    "INVALID_STATE_TRANSITION", f"cannot disconnect session in state {lifecycle['state']}",
                    data={"session_id": session_id, "state": lifecycle["state"], "engine_dispatched": False},
                    safe_retry=False,
                ), "FAILED")
            try:
                context = self.session_registry.get(project_id, session_id)
            except SessionContextMissing:
                result = self._error("EXECUTION_STATE_UNKNOWN", "durable session is connected but its exact runtime context is missing",
                                     data={"session_id": session_id, "state": "UNKNOWN", "safe_retry": False},
                                     safe_retry=False, execution_state_unknown=True)
                self.session_lifecycle.save(self._updated_session_lifecycle(
                    lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                    worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                    server_instance_id=lifecycle["server_instance_id"],
                ), expected_revision=lifecycle["revision"])
                return self._finish(record, result, "UNKNOWN")
            worker = self._session_worker_handles.get((project_id, session_id))
            if worker is None or context.worker is not worker or context.worker_instance_id != lifecycle["worker_instance_id"]:
                self._quarantine_session_context(project_id, session_id, context)
                result = self._error("WORKER_BINDING_UNKNOWN", "disconnect requires the exact retained session Worker handle",
                                     data={"session_id": session_id, "state": "UNKNOWN", "safe_retry": False},
                                     safe_retry=False, execution_state_unknown=True)
                self.session_lifecycle.save(self._updated_session_lifecycle(
                    lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                    worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                    server_instance_id=lifecycle["server_instance_id"],
                ), expected_revision=lifecycle["revision"])
                return self._finish(record, result, "UNKNOWN")
            retirement_proof = None
            if retire_worker:
                retirement_proof = self._session_worker_retirement_proof(
                    (project_id, session_id), worker, lifecycle, connected=True,
                )
                if retirement_proof is None:
                    return self._finish(record, self._error(
                        "WORKER_RETIREMENT_UNAVAILABLE",
                        "exact Worker Popen, birth, connected epoch, or liveness could not be verified",
                        data={"project_id": project_id, "session_id": session_id,
                              "state": "CONNECTED", "engine_dispatched": False,
                              "worker_handle_preserved": True},
                        safe_retry=False,
                    ), "FAILED")
            self._bind_session_job_to_worker(
                record, project_id=project_id, session_id=session_id,
                worker_instance_id=lifecycle["worker_instance_id"],
                worker_epoch=lifecycle["worker_epoch"],
            )
            unresolved = self._session_has_unresolved_jobs(
                project_id, session_id, excluding_operation_id=record["operation_id"],
            )
            if unresolved:
                return self._finish(record, self._error(
                    "SESSION_BUSY", "active or UNKNOWN operations block client disconnect",
                    data={"session_id": session_id, "engine_dispatched": False,
                          "blocking_job_ids": [item["job_id"] for item in unresolved]},
                    safe_retry=True,
                ), "FAILED")
            try:
                self.session_scheduler.fence_session_binding(context)
            except SessionBindingBusy as exc:
                return self._finish(record, self._error(
                    "SESSION_BUSY", str(exc), data={"session_id": session_id, "engine_dispatched": False},
                    safe_retry=True,
                ), "FAILED")
            except SessionSchedulerClosed as exc:
                if not self._shutdown_tasks_drained:
                    return self._finish(record, self._error(
                        "WORKER_BINDING_UNKNOWN", "session scheduler closed before shutdown quiescence was confirmed",
                        data={"session_id": session_id, "engine_dispatched": False,
                              "cause_type": type(exc).__name__}, safe_retry=False,
                    ), "FAILED")
                # close() has globally fenced admission and waited every
                # accepted scheduler Future. This is a shutdown-only exact
                # quiescence proof; ordinary requests cannot use it.
            except Exception as exc:
                return self._finish(record, self._error(
                    "WORKER_BINDING_UNKNOWN", "session Worker admission could not be fenced",
                    data={"session_id": session_id, "engine_dispatched": False,
                          "cause_type": type(exc).__name__}, safe_retry=False,
                ), "FAILED")

            timeout = self._timeouts(execution)["rpc_timeout_s"]
            worker_request_id = f"{record['operation_id']}:disconnect"
            dispatched = False
            preflight_confirmed = False
            lifecycle_transition_started = False
            try:
                before = worker.runtime_metadata()
                if (not isinstance(before, Mapping)
                        or before.get("instance_id") != lifecycle["worker_instance_id"]
                        or before.get("generation") != lifecycle["worker_epoch"]):
                    raise RuntimeError("retained Worker metadata does not match the connected lifecycle epoch")
                if before.get("connected") is True:
                    preflight_confirmed = True
                    operation_context = getattr(worker, "operation_context", None)
                    scope = (operation_context(record["operation_id"], on_request_event=self._session_worker_event_callback(record))
                             if callable(operation_context) else nullcontext())
                    with scope:
                        dispatched = True
                        reply = worker.client().disconnect(request_id=worker_request_id, rpc_timeout_s=timeout)
                    if not isinstance(reply, Mapping):
                        raise RuntimeError("Worker disconnect reply is malformed")
                    if (reply.get("connected") is not False
                            or reply.get("instance_id") != lifecycle["worker_instance_id"]
                            or type(reply.get("generation")) is not int
                            or reply["generation"] <= lifecycle["worker_epoch"]):
                        raise RuntimeError("Worker disconnect reply does not prove a new detached epoch")
                    after = worker.runtime_metadata()
                    if (not isinstance(after, Mapping)
                            or after.get("instance_id") != lifecycle["worker_instance_id"]
                            or after.get("generation") != reply["generation"]
                            or after.get("connected") is not False):
                        raise RuntimeError("Worker health does not confirm the disconnect reply")
                else:
                    raise RuntimeError("Worker connection state differs from the exact connected lifecycle epoch")

                lifecycle_transition_started = True
                context.client_connected = False
                context.client_retired = True
                self._remove_session_context(project_id, session_id, expected=context)
                updated = self._updated_session_lifecycle(
                    lifecycle, state="DISCONNECTED", client_state="DISCONNECTED",
                    worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=reply["generation"],
                    server_instance_id=None,
                    health={"status": "UNKNOWN", "observed_at": None, "source": None},
                )
                lifecycle = self.session_lifecycle.save(updated, expected_revision=lifecycle["revision"])
                if retire_worker:
                    retirement_proof = self._session_worker_retirement_proof(
                        (project_id, session_id), worker, lifecycle, connected=False,
                    )
                    if retirement_proof is None:
                        unknown = self._updated_session_lifecycle(
                            lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                            worker_instance_id=lifecycle["worker_instance_id"],
                            worker_epoch=lifecycle["worker_epoch"],
                            server_instance_id=None,
                        )
                        try:
                            self.session_lifecycle.save(
                                unknown, expected_revision=lifecycle["revision"],
                            )
                        except Exception:
                            pass
                        return self._finish(record, self._error(
                            "EXECUTION_STATE_UNKNOWN",
                            "Worker detached but exact retirement identity no longer verifies",
                            data={"project_id": project_id, "session_id": session_id,
                                  "state": "UNKNOWN", "engine_dispatched": dispatched,
                                  "worker_handle_preserved": True},
                            safe_retry=False, execution_state_unknown=True,
                        ), "UNKNOWN")
                    return self._retire_disconnected_session_worker(
                        record, lifecycle, worker, retirement_proof,
                        disconnect_rpc_dispatched=dispatched,
                    )
                result = {"success": True, "data": {
                    "project_id": project_id, "session_id": session_id,
                    "state": "DISCONNECTED", "client_connected": False,
                    "server_stopped": False, "server_ownership": lifecycle["server_ownership"],
                    "worker_instance_id": reply["instance_id"], "worker_epoch": reply["generation"],
                    "worker_handle_preserved": True,
                    "engine_dispatched": dispatched,
                }}
                return self._finish(record, result, "SUCCEEDED")
            except Exception as exc:
                # Once a lifecycle fence is installed, retain it unless we can
                # prove no Worker request was dispatched. An unknown disconnect
                # never closes the Worker or drops its handle.
                if not dispatched and preflight_confirmed and not lifecycle_transition_started:
                    self.session_scheduler.release_session_binding(context)
                    result = self._error("ENGINE_UNRESPONSIVE", "Worker refused disconnect before an RPC was dispatched",
                                         data={"session_id": session_id, "engine_dispatched": False,
                                               "cause_type": type(exc).__name__}, safe_retry=True)
                    return self._finish(record, result, "FAILED")
                self._remove_session_context(project_id, session_id, expected=context)
                try:
                    unknown_record = self._updated_session_lifecycle(
                        lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                        worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                        server_instance_id=lifecycle["server_instance_id"],
                    )
                    self.session_lifecycle.save(unknown_record, expected_revision=lifecycle["revision"])
                except Exception:
                    pass
                result = self._error("EXECUTION_STATE_UNKNOWN", "Worker disconnect outcome is unknown; original handle retained",
                                     data={"project_id": project_id, "session_id": session_id,
                                           "state": "UNKNOWN", "engine_dispatched": dispatched,
                                           "worker_handle_preserved": True, "cause_type": type(exc).__name__},
                                     safe_retry=False, execution_state_unknown=True)
                return self._finish(record, result, "UNKNOWN")

    def _dispatch_session_reconnect(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        project_id, session_id = routed["project_id"], routed["session_id"]
        self.project_authority.authorize_operation(project_id, "project_write")
        record, reused = self._begin_session_mutation("session.reconnect", routed, execution)
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(record, session_id=session_id)
        self._start_session_mutation_record(record, operation="session.reconnect", session_id=session_id)
        with self._session_connect_lock:
            try:
                lifecycle = self.session_lifecycle.get(project_id, session_id)
            except SessionLifecycleProjectConflict as exc:
                return self._finish(record, self._error("PROJECT_IDENTITY_MISMATCH", str(exc)), "FAILED")
            if lifecycle is None:
                return self._finish(record, self._error("SESSION_NOT_FOUND", "session lifecycle record not found"), "FAILED")
            if lifecycle["state"] == "UNKNOWN":
                result = self._error("EXECUTION_STATE_UNKNOWN", "session is UNKNOWN; reconcile its original Worker before reconnecting",
                                     data={"session_id": session_id, "state": "UNKNOWN", "safe_retry": False},
                                     safe_retry=False, execution_state_unknown=True)
                return self._finish(record, result, "UNKNOWN")
            if lifecycle["state"] not in {"CONNECTED", "DISCONNECTED"}:
                return self._finish(record, self._error(
                    "INVALID_STATE_TRANSITION", f"cannot reconnect session in state {lifecycle['state']}",
                    data={"session_id": session_id, "state": lifecycle["state"], "engine_dispatched": False},
                    safe_retry=False,
                ), "FAILED")

            key = (project_id, session_id)
            worker = self._session_worker_handles.get(key)
            runtime = self._session_runtime_configs.get(key)
            backend = self._session_backends.get(key)
            owned_server_handle = None
            owned_process = None
            if lifecycle.get("server_ownership") == "mcp_managed":
                try:
                    owned_server_handle, owned_process = self._owned_server_handle(
                        project_id, session_id, lifecycle,
                    )
                except OwnedServerError as exc:
                    try:
                        unknown = self._updated_session_lifecycle(
                            lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                            worker_instance_id=lifecycle.get("worker_instance_id"),
                            worker_epoch=lifecycle.get("worker_epoch"),
                            server_instance_id=lifecycle.get("server_instance_id"),
                        )
                        self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
                    except Exception:
                        pass
                    return self._finish(record, self._error(
                        "SERVER_OWNERSHIP_UNKNOWN", str(exc),
                        data={"project_id": project_id, "session_id": session_id,
                              "state": "UNKNOWN", "worker_handle_preserved": worker is not None,
                              "engine_dispatched": False}, execution_state_unknown=True,
                    ), "UNKNOWN")
            if lifecycle.get("client_state") == "RETIRED":
                return self._finish(record, self._error(
                    "WORKER_RETIRED", "session Worker was explicitly retired; reconnect cannot create a replacement Worker",
                    data={"project_id": project_id, "session_id": session_id,
                          "state": "DISCONNECTED", "client_state": "RETIRED",
                          "engine_dispatched": False, "new_worker_created": False},
                    safe_retry=False,
                ), "FAILED")
            if worker is None or runtime is None or backend is None:
                try:
                    stale_context = self.session_registry.get(project_id, session_id)
                except SessionContextMissing:
                    stale_context = None
                self._quarantine_session_context(project_id, session_id, stale_context)
                result = self._error("WORKER_BINDING_UNKNOWN", "reconnect requires the exact retained Worker and trusted local runtime configuration",
                                     data={"session_id": session_id, "state": "UNKNOWN",
                                           "worker_handle_preserved": worker is not None},
                                     safe_retry=False, execution_state_unknown=True)
                self.session_lifecycle.save(self._updated_session_lifecycle(
                    lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                    worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                    server_instance_id=lifecycle["server_instance_id"],
                ), expected_revision=lifecycle["revision"])
                return self._finish(record, result, "UNKNOWN")
            self._bind_session_job_to_worker(
                record, project_id=project_id, session_id=session_id,
                worker_instance_id=lifecycle["worker_instance_id"],
                worker_epoch=lifecycle["worker_epoch"],
            )
            context = None
            try:
                context = self.session_registry.get(project_id, session_id)
            except SessionContextMissing:
                if lifecycle["state"] == "CONNECTED":
                    result = self._error("WORKER_BINDING_UNKNOWN", "connected lifecycle has no exact live session context",
                                         data={"session_id": session_id, "state": "UNKNOWN"},
                                         safe_retry=False, execution_state_unknown=True)
                    self.session_lifecycle.save(self._updated_session_lifecycle(
                        lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                        worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                        server_instance_id=lifecycle["server_instance_id"],
                    ), expected_revision=lifecycle["revision"])
                    return self._finish(record, result, "UNKNOWN")
            if context is not None and (
                context.worker is not worker or context.worker_epoch != lifecycle["worker_epoch"]
                or context.worker_instance_id != lifecycle["worker_instance_id"]
                or context.server_ownership != lifecycle["server_ownership"]
                or (lifecycle["server_ownership"] == "mcp_managed"
                    and context.process_identity != owned_process)
            ):
                self._quarantine_session_context(project_id, session_id, context)
                try:
                    self.session_lifecycle.save(self._updated_session_lifecycle(
                        lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                        worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                        server_instance_id=lifecycle["server_instance_id"],
                    ), expected_revision=lifecycle["revision"])
                except Exception:
                    pass
                return self._finish(record, self._error(
                    "WORKER_BINDING_MISMATCH", "live context differs from the durable Worker epoch",
                    data={"session_id": session_id, "state": "UNKNOWN", "worker_handle_preserved": True},
                    safe_retry=False, execution_state_unknown=True,
                ), "UNKNOWN")
            unresolved = self._session_has_unresolved_jobs(
                project_id, session_id, excluding_operation_id=record["operation_id"],
            )
            if unresolved:
                return self._finish(record, self._error(
                    "SESSION_BUSY", "active or UNKNOWN operations block reconnect",
                    data={"session_id": session_id, "engine_dispatched": False,
                          "blocking_job_ids": [item["job_id"] for item in unresolved]},
                    safe_retry=True,
                ), "FAILED")
            credentials_ref = self._session_credentials_refs.get(key)
            try:
                # Resolve before installing a retirement fence or sending the
                # old Worker a disconnect. A missing/expired local secret
                # reference must leave the current binding intact.
                credentials = self._resolve_session_credentials(credentials_ref)
            except ExecutionContractError as exc:
                return self._finish(record, self._error(
                    exc.code, str(exc),
                    data={"project_id": project_id, "session_id": session_id,
                          "state": lifecycle["state"], "engine_dispatched": False,
                          "worker_handle_preserved": True},
                    safe_retry=True,
                ), "FAILED")
            if context is not None:
                try:
                    self.session_scheduler.fence_session_binding(context)
                except SessionBindingBusy as exc:
                    return self._finish(record, self._error(
                        "SESSION_BUSY", str(exc), data={"session_id": session_id, "engine_dispatched": False},
                        safe_retry=True,
                    ), "FAILED")
                except Exception as exc:
                    return self._finish(record, self._error(
                        "WORKER_BINDING_UNKNOWN", "session Worker admission could not be fenced",
                        data={"session_id": session_id, "engine_dispatched": False,
                              "cause_type": type(exc).__name__}, safe_retry=False,
                    ), "FAILED")

            project_record = self.project_authority.get_project(project_id)
            project_root = Path(project_record["workspace"]).resolve(strict=True)
            endpoint = lifecycle["endpoint"]
            host, port = endpoint["host"], endpoint["port"]
            old_backend_identity = dict(getattr(backend, "worker_identity", {}) or {})
            old_cached = dict(getattr(backend, "cached", {}) or {})
            detached = False
            preflight_confirmed = False
            attached = None
            worker_request_id = f"{record['operation_id']}:disconnect"
            try:
                metadata = worker.runtime_metadata()
                if (not isinstance(metadata, Mapping)
                        or metadata.get("instance_id") != lifecycle["worker_instance_id"]):
                    raise RuntimeError("retained Worker instance identity cannot be confirmed")
                if context is not None:
                    if metadata.get("generation") != lifecycle["worker_epoch"]:
                        raise RuntimeError("retained Worker generation differs from the connected lifecycle")
                    if metadata.get("connected") is not True:
                        raise RuntimeError("connected lifecycle conflicts with retained Worker health")
                    preflight_confirmed = True
                    operation_context = getattr(worker, "operation_context", None)
                    scope = (operation_context(record["operation_id"], on_request_event=self._session_worker_event_callback(record))
                             if callable(operation_context) else nullcontext())
                    with scope:
                        detached = True
                        detach_reply = worker.client().disconnect(request_id=worker_request_id,
                                                                  rpc_timeout_s=self._timeouts(execution)["rpc_timeout_s"])
                    if (not isinstance(detach_reply, Mapping)
                            or detach_reply.get("connected") is not False
                            or detach_reply.get("instance_id") != lifecycle["worker_instance_id"]
                            or type(detach_reply.get("generation")) is not int
                            or detach_reply["generation"] <= lifecycle["worker_epoch"]):
                        raise RuntimeError("Worker disconnect reply is not an exact newer detached epoch")
                    metadata = worker.runtime_metadata()
                    if (not isinstance(metadata, Mapping)
                            or metadata.get("instance_id") != lifecycle["worker_instance_id"]
                            or metadata.get("generation") != detach_reply["generation"]
                            or metadata.get("connected") is not False):
                        raise RuntimeError("Worker health did not confirm detached state")
                    context.client_connected = False
                    context.client_retired = True
                    self._remove_session_context(project_id, session_id, expected=context)
                    detached = True
                    lifecycle = self.session_lifecycle.save(self._updated_session_lifecycle(
                        lifecycle, state="DISCONNECTED", client_state="DISCONNECTED",
                        worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=metadata["generation"],
                        server_instance_id=None,
                        health={"status": "UNKNOWN", "observed_at": None, "source": None},
                    ), expected_revision=lifecycle["revision"])
                else:
                    if (metadata.get("connected") is not False
                            or type(metadata.get("generation")) is not int
                            or metadata.get("generation") != lifecycle["worker_epoch"]):
                        raise RuntimeError("disconnected lifecycle conflicts with retained Worker epoch/health")
                    preflight_confirmed = True

                old_peer = (context.endpoint.observed_peer if context is not None else None)
                if old_peer is None:
                    raw_peer = old_cached.get("observed_peer")
                    if isinstance(raw_peer, Mapping):
                        old_peer = CanonicalSocket(raw_peer.get("address"), raw_peer.get("port"))
                old_version = old_cached.get("remote_engine_version")
                old_build = old_cached.get("remote_engine_build")
                attached = backend.connect_session(
                    runtime=runtime, project_id=project_id, session_id=session_id,
                    host=host, port=port, operation_id=record["operation_id"],
                    request_id=f"{record['operation_id']}:connect",
                    event_callback=self._session_worker_event_callback(record),
                    credentials=credentials, rpc_timeout_s=self._timeouts(execution)["rpc_timeout_s"],
                    project_permissions=project_record.get("policy", {}).get("permissions", []),
                    existing_worker=worker, server_ownership=lifecycle["server_ownership"],
                    owned_process=owned_process,
                )
                reply, peer = attached["reply"], attached["peer"]
                if (reply["instance_id"] != lifecycle["worker_instance_id"]
                        or reply["generation"] <= (lifecycle["worker_epoch"] or 0)
                        or (old_peer is not None and peer != old_peer)
                        or (isinstance(old_version, str) and reply.get("engine_version") != old_version)
                        or attached["worker_identity"].get("remote_engine_build") != old_build):
                    raise RuntimeError("reconnect attached to a different or unverified endpoint/runtime identity")
                process_identity = owned_process
                ownership = lifecycle["server_ownership"]
                if ownership == "mcp_managed" and process_identity is None:
                    raise RuntimeError("MCP-owned reconnect lacks the exact retained server process/listener identity")
                endpoint_identity = SessionEndpointIdentity(
                    host, port, reply["generation"], observed_peer=peer,
                    owned_process=process_identity,
                )
                context = SessionRuntimeContext(
                    project_id=project_id, session_id=session_id, project_root=project_root,
                    runtime=runtime, endpoint=endpoint_identity, backend=backend,
                    worker_instance_id=reply["instance_id"], worker=worker,
                    service=backend.service, client=worker.client(),
                    server_handle=owned_server_handle.process if owned_server_handle is not None else None,
                    remote_client_factory=worker.client,
                    server_ownership=ownership, process_identity=process_identity,
                    client_connected=True, connected_host=host, connected_port=port,
                    server_started_by_mcp=ownership == "mcp_managed",
                    health_snapshot={"status": "HEALTHY", "source": "worker-connect-reply+observed-peer"},
                )
                self.session_registry.register(context)
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                connected_record = self._updated_session_lifecycle(
                    lifecycle, state="CONNECTED", client_state="CONNECTED",
                    worker_instance_id=reply["instance_id"], worker_epoch=reply["generation"],
                    server_instance_id=attached["server_instance_id"],
                    health={"status": "HEALTHY", "observed_at": now,
                            "source": "worker-connect-reply+observed-peer"},
                )
                lifecycle = self.session_lifecycle.save(connected_record, expected_revision=lifecycle["revision"])
                self._remember_session_worker_child(key, worker)
                result = {"success": True, "data": {
                    "project_id": project_id, "session_id": session_id,
                    "state": "CONNECTED", "runtime_id": runtime.runtime_id,
                    "endpoint": {"host": host, "port": port},
                    "observed_peer": {"address": peer.address, "port": peer.port},
                    "worker_instance_id": reply["instance_id"], "worker_epoch": reply["generation"],
                    "server_instance_id": attached["server_instance_id"],
                    "remote_engine_version": reply["engine_version"],
                    "remote_engine_build": attached["worker_identity"]["remote_engine_build"],
                    "remote_engine_build_source": attached["worker_identity"]["remote_engine_build_source"],
                    "server_ownership": ownership, "old_handles_invalidated": True,
                    "worker_reused": True,
                }}
                return self._finish(record, result, "SUCCEEDED")
            except Exception as exc:
                if context is not None:
                    self._remove_session_context(project_id, session_id, expected=context)
                if (isinstance(exc, SessionConnectFailure) and not exc.uncertain
                        and not exc.dispatched and (detached or (preflight_confirmed and context is None))):
                    try:
                        disconnected = self._updated_session_lifecycle(
                            lifecycle, state="DISCONNECTED", client_state="DISCONNECTED",
                            worker_instance_id=lifecycle["worker_instance_id"], worker_epoch=lifecycle["worker_epoch"],
                            server_instance_id=None,
                            health={"status": "UNKNOWN", "observed_at": None, "source": None},
                        )
                        self.session_lifecycle.save(disconnected, expected_revision=lifecycle["revision"])
                    except Exception:
                        pass
                    result = self._error(exc.code, str(exc), data={
                        "project_id": project_id, "session_id": session_id,
                        "state": "DISCONNECTED", "engine_dispatched": False,
                        "worker_handle_preserved": True, "safe_retry": exc.safe_retry,
                    }, safe_retry=exc.safe_retry)
                    return self._finish(record, result, "FAILED")
                if (context is not None or detached or attached is not None or not preflight_confirmed
                        or (isinstance(exc, SessionConnectFailure) and exc.dispatched)):
                    try:
                        unknown = self._updated_session_lifecycle(
                            lifecycle, state="UNKNOWN", client_state="UNKNOWN",
                            worker_instance_id=lifecycle["worker_instance_id"],
                            worker_epoch=lifecycle["worker_epoch"],
                            server_instance_id=lifecycle["server_instance_id"],
                        )
                        self.session_lifecycle.save(unknown, expected_revision=lifecycle["revision"])
                    except Exception:
                        pass
                    result = self._error("EXECUTION_STATE_UNKNOWN", "reconnect outcome is uncertain; original Worker retained and no replacement was created",
                                         data={"project_id": project_id, "session_id": session_id,
                                               "state": "UNKNOWN", "worker_handle_preserved": True,
                                               "cause_type": type(exc).__name__},
                                         safe_retry=False, execution_state_unknown=True)
                    return self._finish(record, result, "UNKNOWN")
                result = self._error("ENGINE_UNRESPONSIVE", "reconnect health preflight failed before any connect dispatch",
                                     data={"project_id": project_id, "session_id": session_id,
                                           "engine_dispatched": False, "cause_type": type(exc).__name__},
                                     safe_retry=True)
                return self._finish(record, result, "FAILED")

    def _dispatch_session_recover(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        project_id, session_id = routed["project_id"], routed["session_id"]
        self.project_authority.authorize_operation(project_id, "project_write")
        record, reused = self._begin_session_mutation("session.recover", routed, execution)
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(record, session_id=session_id)
        self._start_session_mutation_record(record, operation="session.recover", session_id=session_id)
        try:
            with self._session_connect_lock:
                lifecycle = self.session_lifecycle.get(project_id, session_id)
                if lifecycle is None:
                    return self._finish(record, self._error(
                        "SESSION_NOT_FOUND", "session lifecycle record not found",
                    ), "FAILED")
                worker = self._session_worker_handles.get((project_id, session_id))
                try:
                    context = self.session_registry.get(project_id, session_id)
                except SessionContextMissing:
                    context = None
                # Recovery must report prior proofs for still-historical UNKNOWN
                # rows, even though the ordinary admission gate correctly treats
                # those rows as quiescent.  Include unresolved rows plus UNKNOWN
                # rows with an existing proof so repeated read-only recovery is
                # observable and idempotent without querying the Worker again.
                unresolved = [
                    job for job in self._session_jobs_for_project(project_id)
                    if job.get("operation_id") != record["operation_id"]
                    and self._job_belongs_to_session(job, project_id, session_id)
                    and (
                        job.get("status") not in TERMINAL
                        or (job.get("status") == "UNKNOWN"
                            and self._session_recovery_resolution_is_valid(job))
                    )
                ]
                unresolved_items: list[dict[str, Any]] = []
                resolved_jobs: list[dict[str, Any]] = []
                observations: list[dict[str, Any]] = []
                runtime_metadata = None
                runtime_error = None

                if worker is not None:
                    try:
                        runtime_metadata = worker.runtime_metadata()
                    except Exception as exc:
                        runtime_error = type(exc).__name__

                exact_instance = bool(
                    worker is not None and isinstance(runtime_metadata, Mapping)
                    and isinstance(lifecycle.get("worker_instance_id"), str)
                    and runtime_metadata.get("instance_id") == lifecycle.get("worker_instance_id")
                )
                if worker is None:
                    unresolved_items.append({
                        "kind": "SESSION_WORKER", "status": "UNKNOWN",
                        "reason": "ORIGINAL_WORKER_HANDLE_UNAVAILABLE",
                    })
                elif not exact_instance:
                    unresolved_items.append({
                        "kind": "SESSION_WORKER", "status": "UNKNOWN",
                        "reason": "ORIGINAL_WORKER_IDENTITY_UNCONFIRMED",
                        "runtime_readback_error": runtime_error,
                    })

                fence_epoch = None
                if context is not None and context.worker is worker:
                    fence_epoch = context.worker_epoch
                elif type(lifecycle.get("worker_epoch")) is int and lifecycle["worker_epoch"] > 0:
                    fence_epoch = lifecycle["worker_epoch"]

                guard_context = (
                    self.session_scheduler.worker_recovery_admission_guard(worker, fence_epoch)
                    if worker is not None and exact_instance and type(fence_epoch) is int
                    else nullcontext({"admission_fenced": False, "accepted_task_count": None})
                )
                admission_fenced = False
                if worker is not None and exact_instance and type(fence_epoch) is not int:
                    unresolved_items.append({
                        "kind": "SESSION_WORKER", "status": "UNKNOWN",
                        "reason": "ORIGINAL_WORKER_EPOCH_UNAVAILABLE",
                    })

                try:
                    with guard_context as admission:
                        admission_fenced = bool(
                            worker is not None and exact_instance
                            and isinstance(admission, Mapping)
                            and admission.get("admission_fenced") is True
                        )
                        if admission_fenced:
                            stable_generation = runtime_metadata.get("generation")
                            for job in unresolved:
                                job_id = job.get("job_id")
                                job_meta = job.get("metadata")
                                job_binding = job_meta.get("runtime_binding") if isinstance(job_meta, Mapping) else None
                                job_execution = job_meta.get("execution") if isinstance(job_meta, Mapping) else None
                                source_model_ref = job_execution.get("model_ref") if isinstance(job_execution, Mapping) else None
                                if self._session_recovery_resolution_is_valid(job):
                                    prior = self.store.session_recovery_resolution(job_id)
                                    prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                    resolved_jobs.append({
                                        "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                        "quiescence_resolution": "ALREADY_PROVEN",
                                        "evidence_sha256": prior_meta.get("evidence_sha256"),
                                        "replayed": False,
                                    })
                                    continue
                                if self.store.session_recovery_resolution(job_id) is not None:
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": "EXISTING_RECOVERY_PROOF_INVALID",
                                    })
                                    continue
                                if not isinstance(job_binding, Mapping):
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": "ORIGINAL_WORKER_BINDING_MISSING",
                                    })
                                    continue
                                if (job_binding.get("project_id") != project_id
                                        or job_binding.get("session_id") != session_id
                                        or job_binding.get("worker_instance_id") != runtime_metadata.get("instance_id")):
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": "ORIGINAL_WORKER_BINDING_MISMATCH",
                                    })
                                    continue
                                if isinstance(source_model_ref, Mapping) and (
                                    context is None or context.worker is not worker
                                    or context.worker_epoch != stable_generation
                                    or lifecycle.get("state") != "CONNECTED"
                                    or lifecycle.get("client_state") != "CONNECTED"
                                    or job_binding.get("worker_epoch") != stable_generation
                                ):
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": "EXACT_CONNECTED_SESSION_CONTEXT_AND_WORKER_EPOCH_REQUIRED",
                                    })
                                    continue
                                readback = self._read_session_job_requests(
                                    worker, job, record,
                                    timeout=self._timeouts(execution)["rpc_timeout_s"],
                                )
                                after_requests = worker.runtime_metadata()
                                if (not isinstance(after_requests, Mapping)
                                        or after_requests.get("instance_id") != runtime_metadata.get("instance_id")
                                        or after_requests.get("generation") != stable_generation):
                                    readback["terminal"] = False
                                    readback["reason"] = "WORKER_EPOCH_CHANGED_DURING_REQUEST_READBACK"
                                observation = {
                                    "job_id": job_id,
                                    "operation_id": job.get("operation_id"),
                                    "historical_status": job.get("status"),
                                    "worker_binding": {
                                        "instance_id": job_binding.get("worker_instance_id"),
                                        "epoch": job_binding.get("worker_epoch"),
                                        "current_epoch_matches": job_binding.get("worker_epoch") == stable_generation,
                                    },
                                    "worker_requests": readback.get("requests", []),
                                    "terminal_requests": readback.get("terminal") is True,
                                    "reason": readback.get("reason"),
                                }
                                observations.append(observation)

                                if isinstance(source_model_ref, Mapping):
                                    if (context is None or context.worker is not worker
                                            or context.worker_epoch != stable_generation
                                            or lifecycle.get("state") != "CONNECTED"
                                            or lifecycle.get("client_state") != "CONNECTED"):
                                        reason = "EXACT_CONNECTED_SESSION_CONTEXT_REQUIRED"
                                    elif job_binding.get("worker_epoch") != stable_generation:
                                        reason = "ORIGINAL_WORKER_EPOCH_MISMATCH"
                                    else:
                                        evidence, reason = self._session_recovery_model_evidence(
                                            job, context, lifecycle, worker, runtime_metadata,
                                            readback, record,
                                        )
                                        if evidence is not None:
                                            recorded = self.store.record_session_recovery_resolution(
                                                job_id, job["operation_id"], evidence,
                                            )
                                            if recorded.get("recorded") is True:
                                                resolved_jobs.append({
                                                    "job_id": job_id,
                                                    "historical_status": job.get("status"),
                                                    "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                                    "quiescence_resolution": "PROVEN_AND_AUDITED",
                                                    "evidence_sha256": recorded["resolution"]["metadata"]["evidence_sha256"],
                                                    "model_ref": dict(evidence["model_ref"]),
                                                    "model_revision": dict(evidence["model_revision"]),
                                                    "replayed": False,
                                                })
                                            elif recorded.get("reason") == "ALREADY_RESOLVED":
                                                source_now = self.store.job(job_id)
                                                if source_now and self._session_recovery_resolution_is_valid(source_now):
                                                    prior = recorded.get("resolution") or {}
                                                    prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                                    resolved_jobs.append({
                                                        "job_id": job_id,
                                                        "historical_status": job.get("status"),
                                                        "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                                        "quiescence_resolution": "ALREADY_PROVEN",
                                                        "evidence_sha256": prior_meta.get("evidence_sha256"),
                                                        "replayed": False,
                                                    })
                                                else:
                                                    reason = "EXISTING_RECOVERY_PROOF_INVALID"
                                            else:
                                                reason = recorded.get("reason") or "RECOVERY_RESOLUTION_NOT_RECORDED"
                                else:
                                    reason = (
                                        "NO_MODELREF_REVISION_PROOF_FOR_LIFECYCLE_OPERATION"
                                        if readback.get("terminal") is True
                                        else readback.get("reason") or "ORIGINAL_LIFECYCLE_REQUEST_NOT_TERMINAL"
                                    )
                                    # This readback is diagnostic only. Lifecycle
                                    # transitions without a bound ModelRef and
                                    # revision remain explicitly PARTIAL here.
                                    self.store.add_event(job_id, "SessionRecoveryReadback", {
                                        "session_recovery_operation_id": record["operation_id"],
                                        "worker_binding": observation["worker_binding"],
                                        "request_observations": observation["worker_requests"],
                                        "resolution": "UNRESOLVED_NO_MODELREV_SCOPE",
                                        "replay_performed": False,
                                    })
                                if not any(item.get("job_id") == job_id for item in resolved_jobs):
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": reason or "RECOVERY_EVIDENCE_INCOMPLETE",
                                    })
                        else:
                            for job in unresolved:
                                unresolved_items.append({
                                    "kind": "UNKNOWN_JOB", "job_id": job.get("job_id"),
                                    "historical_status": job.get("status"),
                                    "reason": "SESSION_WORKER_ADMISSION_NOT_FENCED",
                                })
                except SessionBindingBusy as exc:
                    unresolved_items.append({
                        "kind": "SESSION_ADMISSION", "status": "BUSY",
                        "reason": "ACCEPTED_WORK_BLOCKS_RECOVERY",
                        "detail": str(exc),
                    })
                except (SessionSchedulerClosed, SessionContextMissing) as exc:
                    unresolved_items.append({
                        "kind": "SESSION_ADMISSION", "status": "UNKNOWN",
                        "reason": "SESSION_ADMISSION_FENCE_UNAVAILABLE",
                        "cause_type": type(exc).__name__,
                    })

                if lifecycle.get("state") == "UNKNOWN":
                    unresolved_items.append({
                        "kind": "SESSION_LIFECYCLE", "historical_status": "UNKNOWN",
                        "reason": "NO_MODELREF_LIFECYCLE_RESOLUTION_PROOF",
                    })
                if (worker is not None and exact_instance
                        and isinstance(runtime_metadata, Mapping)
                        and runtime_metadata.get("generation") != lifecycle.get("worker_epoch")):
                    unresolved_items.append({
                        "kind": "SESSION_LIFECYCLE", "historical_status": lifecycle.get("state"),
                        "reason": "CURRENT_WORKER_EPOCH_DIFFERS_FROM_LIFECYCLE_EPOCH",
                    })

                # A job may already have been resolved on a previous call; do
                # not report it again as an unresolved blocker.
                unresolved_items = [item for item in unresolved_items
                                    if item.get("job_id") not in {row.get("job_id") for row in resolved_jobs}]
                status = (
                    "PARTIAL_HISTORICAL_UNKNOWN_RETAINED" if resolved_jobs
                    else "PARTIAL_UNRESOLVED" if unresolved_items
                    else "NO_UNRESOLVED_WORK"
                )
                data = {
                    "project_id": project_id,
                    "session_id": session_id,
                    "lifecycle_state": lifecycle["state"],
                    "worker_readback": ({key: runtime_metadata.get(key) for key in (
                        "instance_id", "generation", "connected", "server",
                    )} if isinstance(runtime_metadata, Mapping) else "UNAVAILABLE"),
                    "admission_fence": ("HELD_DURING_READBACK" if admission_fenced else "UNAVAILABLE"),
                    "request_observations": observations,
                    "resolved_jobs": resolved_jobs,
                    "unresolved_items": unresolved_items,
                    "replayed_requests": 0,
                    "new_worker_created": False,
                    "historical_unknown_preserved": True,
                    "recovery_status": status,
                }
                return self._finish(record, {"success": True, "data": data}, "SUCCEEDED")
        except SessionLifecycleProjectConflict as exc:
            return self._finish(record, self._error("PROJECT_IDENTITY_MISMATCH", str(exc)), "FAILED")
        except Exception as exc:
            return self._finish(record, self._error(
                "EXECUTION_STATE_UNKNOWN", "session recovery evidence could not be durably established",
                data={"project_id": project_id, "session_id": session_id,
                      "recovery_status": "PARTIAL_UNRESOLVED",
                      "cause_type": type(exc).__name__,
                      "replayed_requests": 0, "new_worker_created": False,
                      "historical_unknown_preserved": True},
                safe_retry=False, execution_state_unknown=True,
            ), "UNKNOWN")

    def _dispatch_session_control(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        """Handle session lifecycle routes and cached snapshots.

        Lifecycle reads are deliberately control-plane only: they cannot wait
        behind a solve or turn cached evidence into a live-health claim.
        """
        from . import _g2_registry

        if operation not in SESSION_OPERATIONS:
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"session operation is not supported: {operation}")
        routed = dict(arguments)
        schema = _g2_registry.BY_ID.get(operation)
        properties = set(schema.input_schema.get("properties", {})) if schema is not None else set()
        for field in ("project_id", "session_id", "request_id", "idempotency_key", "authorization_ref"):
            if field not in properties or field not in execution:
                continue
            if field in routed and routed[field] != execution[field]:
                code = "IDEMPOTENCY_CONFLICT" if field == "idempotency_key" else "INVALID_REQUEST"
                raise ExecutionContractError(code, f"session {field} differs from the execution envelope")
            routed.setdefault(field, execution[field])
        _g2_registry.validate_call(operation, routed)
        project_id = routed.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("INVALID_REQUEST", "session action requires project_id")
        if execution.get("project_id") not in (None, project_id):
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "session project_id differs from the execution envelope")
        if operation == "session.connect":
            return self._dispatch_session_connect(routed, execution)
        if operation == "session.start":
            return self._dispatch_session_start(routed, execution)
        if operation == "session.disconnect":
            return self._dispatch_session_disconnect(routed, execution)
        if operation == "session.reconnect":
            return self._dispatch_session_reconnect(routed, execution)
        if operation == "session.recover":
            return self._dispatch_session_recover(routed, execution)
        if operation == "session.stop":
            return self._dispatch_session_stop(routed, execution)
        if operation not in {"session.list", "session.inspect", "session.health"}:
            # validate_call above gives a truthful UNSUPPORTED_OPERATION for
            # cataloged lifecycle mutations that do not yet have process-safe
            # production adapters.
            raise ExecutionContractError("UNSUPPORTED_OPERATION", f"session lifecycle adapter is not available: {operation}")
        if operation == "session.list":
            filters = routed.get("filter", {})
            if not isinstance(filters, Mapping) or filters:
                raise ExecutionContractError("INVALID_REQUEST", "session.list currently accepts only an empty filter object")
        self.project_authority.authorize_operation(project_id, "inspect")
        if operation == "session.list":
            records = self.session_lifecycle.list_for_project(project_id)
            live = {context.session_id for context in self.session_registry.list_for_project(project_id)}
            return {"success": True, "data": {
                "project_id": project_id,
                "sessions": [
                    {"lifecycle": row, "runtime_live": row["session_id"] in live}
                    for row in records
                ],
                "count": len(records),
                "inventory_scope": "durable-project-lifecycle-records",
                "unattributed_legacy_rows": "EXCLUDED",
            }}

        session_id = routed["session_id"]
        try:
            record = self.session_lifecycle.get(project_id, session_id)
        except SessionLifecycleProjectConflict as exc:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "session belongs to another project") from exc
        if record is None:
            raise ExecutionContractError("SESSION_NOT_FOUND", "no project-attributed lifecycle record exists for this session")
        try:
            context = self.session_registry.get(project_id, session_id)
        except SessionContextMissing:
            context = None
        if operation == "session.inspect":
            return {"success": True, "data": {
                "lifecycle": record,
                "runtime_live": context is not None,
                "worker_binding": (
                    {"worker_instance_id": context.worker_instance_id,
                     "worker_epoch": context.worker_epoch,
                     "server_lane": "owned" if context.endpoint.owned_process is not None else "unknown_or_shared"}
                    if context is not None else None
                ),
            }}
        health = dict(record["health"])
        return {"success": True, "data": {
            "project_id": project_id,
            "session_id": session_id,
            "session_state": record["state"],
            "health": health,
            "freshness": "CACHED" if health.get("status") in {"HEALTHY", "UNHEALTHY"} else "UNKNOWN",
            "live_context": context is not None,
            "worker_rpc_performed": False,
        }}

    def _execution_session_context(self, execution: Mapping[str, Any]):
        """Resolve a frozen project/session binding, preserving legacy default."""
        project_id = execution.get("project_id")
        session_id = execution.get("session_id")
        if session_id is None:
            return None
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", "session-bound work requires execution.project_id")
        try:
            context = self.session_registry.get(project_id, session_id)
        except SessionContextMissing:
            service = self._default_backend.service
            legacy_session = getattr(getattr(service, "ledger", None), "session_id", None)
            if session_id == legacy_session:
                return None
            raise ExecutionContractError(
                "SESSION_NOT_FOUND", "execution.session_id has no live project-bound runtime context",
            ) from None
        if context.project_id != project_id or context.session_id != session_id:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "session runtime binding differs from the execution envelope")
        return context

    def _runtime_binding_for_request(self, context, execution: Mapping[str, Any]) -> dict[str, Any] | None:
        """Capture the exact Worker epoch that accepted a durable engine job."""
        if context is not None:
            return {
                "kind": "registered_session",
                "project_id": context.project_id,
                "session_id": context.session_id,
                "worker_epoch": context.worker_epoch,
                "worker_instance_id": context.worker_instance_id,
            }
        backend = self._default_backend
        service = getattr(backend, "service", None)
        ledger = getattr(service, "ledger", None)
        session_id = getattr(ledger, "session_id", None)
        identity = getattr(backend, "worker_identity", None)
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(identity, Mapping)
                or type(identity.get("connection_epoch")) is not int
                or identity["connection_epoch"] < 1
                or not isinstance(identity.get("worker_instance_id"), str)
                or not identity["worker_instance_id"]):
            return None
        requested_session = execution.get("session_id")
        if requested_session is not None and requested_session != session_id:
            raise ExecutionContractError("SESSION_IDENTITY_MISMATCH", "execution.session_id differs from the active default Worker")
        return {
            "kind": "default_backend",
            "project_id": execution.get("project_id") if isinstance(execution.get("project_id"), str) else None,
            "session_id": session_id,
            "worker_epoch": identity["connection_epoch"],
            "worker_instance_id": identity["worker_instance_id"],
            "endpoint": identity.get("endpoint"),
        }

    def _resolve_job_runtime_context(self, job: Mapping[str, Any], execution: Mapping[str, Any] | None = None):
        """Resolve only the durable Worker binding recorded when this job was accepted."""
        metadata = job.get("metadata") if isinstance(job, Mapping) else None
        binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
        if not isinstance(binding, Mapping):
            raise ExecutionContractError(
                "WORKER_BINDING_UNKNOWN",
                "job has no durable project/session/Worker-epoch binding; Worker access is refused",
            )
        kind = binding.get("kind")
        project_id, session_id = binding.get("project_id"), binding.get("session_id")
        epoch, instance_id = binding.get("worker_epoch"), binding.get("worker_instance_id")
        if (not isinstance(session_id, str) or not session_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(instance_id, str) or not instance_id):
            raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "durable Worker binding is incomplete")
        if project_id is not None and (not isinstance(project_id, str) or not project_id):
            raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "durable project binding is malformed")
        request_execution = execution if isinstance(execution, Mapping) else {}
        job_execution = metadata.get("execution") if isinstance(metadata, Mapping) else None
        if not isinstance(job_execution, Mapping):
            job_execution = {}
        for observed in (job_execution, request_execution):
            for field, expected in (("project_id", project_id), ("session_id", session_id)):
                supplied = observed.get(field)
                if supplied is not None and supplied != expected:
                    raise ExecutionContractError("WORKER_BINDING_MISMATCH", f"job {field} differs from its durable Worker binding")
        if kind == "registered_session":
            if not isinstance(project_id, str) or not project_id:
                raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "registered Worker binding lacks project identity")
            try:
                context = self.session_registry.get(project_id, session_id)
            except SessionContextMissing:
                raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "the job's registered Worker context is no longer live") from None
            if (context.worker_epoch != epoch or context.worker_instance_id != instance_id):
                raise ExecutionContractError("WORKER_BINDING_MISMATCH", "the job belongs to a different Worker epoch")
            return context
        if kind == "default_backend":
            backend = self._default_backend
            service = getattr(backend, "service", None)
            ledger = getattr(service, "ledger", None)
            identity = getattr(backend, "worker_identity", None)
            if (getattr(ledger, "session_id", None) != session_id
                    or not isinstance(identity, Mapping)
                    or identity.get("connection_epoch") != epoch
                    or identity.get("worker_instance_id") != instance_id):
                raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "the job's default Worker epoch is no longer live")
            return None
        raise ExecutionContractError("WORKER_BINDING_UNKNOWN", "job names an unsupported Worker binding kind")

    def _dispatch_job_reconcile(self, arguments: Mapping[str, Any], *, execution: Mapping[str, Any]) -> dict[str, Any]:
        job_id = arguments.get("job_id") if isinstance(arguments, Mapping) else None
        if not isinstance(job_id, str) or not job_id:
            return self._error("INVALID_REQUEST", "job_id must be a non-empty string")
        job = self.store.job(job_id)
        if not job:
            return self._error("NODE_NOT_FOUND", "job not found")
        if job.get("status") not in {"UNKNOWN", "RECONCILING"}:
            return {"success": True, "data": job}
        # A fully observed request set can be reconciled from durable event
        # evidence alone.  This path does not consult any Worker and remains
        # safe even for pre-binding historical records.
        events = []
        while True:
            page = self.store.events(job_id, offset=len(events), limit=1000)
            events.extend(page)
            if len(page) < 1000:
                break
        submitted_ids = {
            event.get("metadata", {}).get("request_id")
            for event in events
            if event.get("event") == "worker_request"
            and event.get("metadata", {}).get("phase") == "submitted"
            and isinstance(event.get("metadata", {}).get("request_id"), str)
            and event.get("metadata", {}).get("request_id")
        }
        observed_ids = {
            event.get("metadata", {}).get("request_id")
            for event in events
            if event.get("event") == "worker_request"
            and event.get("metadata", {}).get("phase") == "observed"
            and event.get("metadata", {}).get("status") in {"SUCCEEDED", "FAILED"}
        }
        if submitted_ids and submitted_ids.issubset(observed_ids):
            return self._reconcile(job)
        try:
            context = self._resolve_job_runtime_context(job, execution)
        except ExecutionContractError as exc:
            return self._exception(exc)
        try:
            with self._worker_observation_admission():
                # Worker status/reconciliation is an existing control-channel
                # RPC, not ordinary model API work. Bind it to the durable
                # Worker directly so a busy solve on that lane cannot hide
                # its own recovery/status path behind the solve queue.
                from contextlib import nullcontext
                scope = use_session_context(context) if context is not None else nullcontext()
                with scope:
                    return self._reconcile(job)
        except ExecutionContractError as exc:
            return self._exception(exc)
        except Exception as exc:
            self._log_exception()
            return self._error("EXECUTION_STATE_UNKNOWN", "job reconciliation could not establish Worker state", type=type(exc).__name__)

    def _dispatch_job_cancel(self, arguments: Mapping[str, Any], *, execution: Mapping[str, Any]) -> dict[str, Any]:
        """Route force-stop only inside the Worker context durably bound to its job."""
        if arguments.get("force_stop") is not True:
            # Ordinary cancellation records a durable intent and never touches
            # a Worker or service handle, so it does not select a backend.
            return self._control_read("job_cancel", dict(arguments))
        server_scope = arguments.get("server_scope")
        if not isinstance(server_scope, dict):
            return self._error(
                "UNAUTHORIZED_FORCE_STOP",
                "force_stop requires explicit server_scope authorization with verified ownership",
                data={"job_id": arguments.get("job_id"), "cancel_accepted": False,
                      "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
            )
        if "authorized" not in server_scope or type(server_scope["authorized"]) is not bool:
            return self._error("INVALID_REQUEST", "server_scope.authorized must be a boolean", safe_retry=False)
        if not server_scope["authorized"]:
            return self._error(
                "UNAUTHORIZED_FORCE_STOP",
                "force_stop requires explicit server_scope authorization with verified ownership",
                data={"job_id": arguments.get("job_id"), "cancel_accepted": False,
                      "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
            )
        job_id = arguments.get("job_id")
        job = self.store.job(job_id) if isinstance(job_id, str) else None
        if not job:
            return self._error("NODE_NOT_FOUND" if isinstance(job_id, str) else "INVALID_REQUEST",
                               "job not found" if isinstance(job_id, str) else "job_id must be a non-empty string")
        try:
            context = self._resolve_job_runtime_context(job, execution)
        except ExecutionContractError as exc:
            return self._exception(exc)
        try:
            with self._worker_observation_admission():
                if context is None:
                    raise ExecutionContractError(
                        "WORKER_BINDING_UNKNOWN",
                        "force-stop requires a registered session with exact owned-process evidence",
                    )
                with use_session_context(context):
                    return self._cancel_job(dict(arguments))
        except ExecutionContractError as exc:
            return self._exception(exc)
        except Exception as exc:
            self._log_exception()
            return self._error("EXECUTION_STATE_UNKNOWN", "force-stop could not establish the job's bound Worker state", type=type(exc).__name__)

    def _dispatch_catalog_job_control_impl(self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute a canonical job action using the cached store."""
        from . import _g2_registry
        entry = _g2_registry.validate_call(inner_operation, inner_arguments)
        forbidden_identity = {"session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
        present_identity = sorted(forbidden_identity.intersection(inner_arguments))
        if present_identity:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"{inner_operation} identity belongs in the outer execution envelope: {', '.join(present_identity)}",
            )
        if "project_id" in inner_arguments and inner_operation != "job.list":
            raise ExecutionContractError("INVALID_REQUEST", f"project_id is not an argument for {inner_operation}")

        mapped = dict(inner_arguments)
        if entry.operation_id == "job.list":
            if "cursor" in mapped and "offset" in mapped:
                raise ExecutionContractError("INVALID_REQUEST", "job.list accepts either cursor or offset, not both")
            cursor = mapped.pop("cursor", None)
            if cursor is not None:
                mapped["offset"] = self._page_cursor_offset(cursor, "job.list cursor")
            filters = mapped.pop("filter", None)
            if filters is not None:
                if not isinstance(filters, dict):
                    raise ExecutionContractError("INVALID_REQUEST", "job.list filter must be an object")
                unknown_filters = sorted(set(filters) - {"status", "project_id"})
                if unknown_filters:
                    raise ExecutionContractError("INVALID_REQUEST", f"job.list has unsupported filter fields: {', '.join(unknown_filters)}")
                for name, value in filters.items():
                    if name in mapped:
                        raise ExecutionContractError("INVALID_REQUEST", f"job.list {name} cannot be set both directly and in filter")
                    mapped[name] = value
            response = self._control_read("job_list", mapped)
            if response.get("success") and isinstance(response.get("data"), dict):
                data = response["data"]
                data["next_cursor"] = str(data["offset"] + len(data["jobs"])) if data.get("has_more") else None
            return response

        if entry.operation_id == "job.log":
            cursor = mapped.pop("cursor", None)
            offset = self._page_cursor_offset(cursor, "job.log cursor") if cursor is not None else 0
            mapped["offset"] = offset
            response = self._control_read("job_log", mapped)
            if response.get("success") and isinstance(response.get("data"), dict):
                response["data"]["next_cursor"] = str(response["data"].get("next_offset", offset))
            return response

        if entry.operation_id == "job.cleanup":
            job_ids = mapped.get("job_ids")
            if not isinstance(job_ids, list) or not job_ids or any(not isinstance(job_id, str) or not job_id for job_id in job_ids):
                raise ExecutionContractError("INVALID_REQUEST", "job.cleanup requires an explicit non-empty job_ids array")
            if len(set(job_ids)) != len(job_ids):
                raise ExecutionContractError("INVALID_REQUEST", "job.cleanup job_ids must not contain duplicates")
            policy = mapped.get("policy", {})
            if not isinstance(policy, dict):
                raise ExecutionContractError("INVALID_REQUEST", "job.cleanup policy must be an object")
            unknown_policy = sorted(set(policy) - {"metadata_only"})
            if unknown_policy:
                raise ExecutionContractError("INVALID_REQUEST", f"job.cleanup has unsupported policy fields: {', '.join(unknown_policy)}")
            metadata_only = policy.get("metadata_only", True)
            if type(metadata_only) is not bool or not metadata_only:
                raise ExecutionContractError("INVALID_REQUEST", "job.cleanup policy.metadata_only must be true")
            try:
                project_id = execution.get("project_id")
                if project_id is not None and (not isinstance(project_id, str) or not project_id):
                    raise ExecutionContractError("INVALID_REQUEST", "execution.project_id must be a non-empty string")
                cleaned_ids = self.store.compact_terminal_job_metadata(job_ids, project_id=project_id)
            except JobCleanupError as exc:
                return self._error(exc.code, str(exc), safe_retry=False)
            return {"success": True, "data": {"cleaned_job_ids": cleaned_ids, "cleaned_count": len(cleaned_ids)}}

        operation_map = {
            "job.status": "job_status",
            "job.result": "job_result",
            "job.reconcile": "job_reconcile",
            "job.wait": "job_wait",
            "job.cancel": "job_cancel",
        }
        if entry.operation_id == "job.reconcile":
            return self._dispatch_job_reconcile(mapped, execution=execution)
        if entry.operation_id == "job.cancel" and mapped.get("force_stop") is True:
            return self._dispatch_job_cancel(mapped, execution=execution)
        return self._control_read(operation_map[entry.operation_id], mapped)

    def _dispatch_project_control(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any], timeouts: dict[str, Any]) -> dict[str, Any]:
        """Run project CRUD on the durable store without entering the engine queue."""
        from . import _g2_registry

        if not self.backend.project_root_explicit:
            raise ExecutionContractError(
                "RUNTIME_CONFIGURATION_REQUIRED",
                "project actions require an explicit project_root or COMSOL_PROJECT_ROOT",
            )

        body = dict(arguments)
        outer = dict(execution)
        # Keep transport identity in the durable outer execution envelope.
        # Only project.create/contract_set/policy_set have a domain-level
        # idempotency_key in their accepted ProjectAuthority shapes; injecting
        # it into read-only project.inspect (or permissions/state_export)
        # turns a valid envelope into an unsupported domain argument.
        envelope_fields = ["project_id", "request_id"]
        if operation in {"project.create", "project.contract_set", "project.policy_set"}:
            envelope_fields.append("idempotency_key")
        if operation == "project.policy_set":
            envelope_fields.append("authorization_ref")
        for field in envelope_fields:
            if field not in outer:
                continue
            if field in body and body[field] != outer[field]:
                code = "IDEMPOTENCY_CONFLICT" if field == "idempotency_key" else "INVALID_REQUEST"
                raise ExecutionContractError(code, f"project {field} differs from the execution envelope")
            body.setdefault(field, outer[field])
        _g2_registry.validate_call(operation, body, allow_unbound_identity=True)
        request_id = outer.get("request_id") or body.get("request_id") or str(uuid4())
        key = outer.get("idempotency_key") or body.get("idempotency_key")
        if key is None:
            if operation in {"project.create", "project.contract_set", "project.policy_set"}:
                raise ExecutionContractError("INVALID_REQUEST", f"{operation} requires idempotency_key")
            key = str(uuid4())
        if not isinstance(request_id, str) or not request_id or not isinstance(key, str) or not key:
            raise ExecutionContractError("INVALID_REQUEST", "project request identifiers must be nonempty strings")
        if len(key) > 256:
            raise ExecutionContractError("INVALID_REQUEST", "idempotency_key exceeds the supported size")
        request_execution = {**outer, "request_id": request_id, "idempotency_key": key}
        digest = canonical_request_hash(
            operation, arguments, request_execution.get("model_ref"), request_execution.get("expected_revision"),
            session_id=request_execution.get("session_id"),
            **({"project_id": body["project_id"]} if isinstance(body.get("project_id"), str) else {}),
            queue_timeout_s=timeouts.get("queue_timeout_s"),
            execution_timeout_s=timeouts.get("execution_timeout_s"),
            no_progress_warning_s=timeouts.get("no_progress_warning_s"),
        )
        persisted_arguments = dict(arguments)
        persisted_execution = dict(request_execution)
        authorization_hashes = []
        for location, mapping in (("arguments", persisted_arguments), ("execution", persisted_execution)):
            raw_ref = mapping.pop("authorization_ref", None)
            mapping.pop("authorization_ref_sha256", None)
            if raw_ref is not None:
                if (not isinstance(raw_ref, str) or not raw_ref.strip() or len(raw_ref) > 512
                        or any(ord(char) < 32 for char in raw_ref)):
                    raise ExecutionContractError("AUTHORIZATION_REQUIRED", "project policy update requires a valid authorization reference")
                value = hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()
                mapping["authorization_ref_sha256"] = value
                authorization_hashes.append((location, value))
        if authorization_hashes:
            auth_identity = "\0".join(f"{where}:{value}" for where, value in authorization_hashes)
            digest = hashlib.sha256(f"{digest}\0authorization_refs:{auth_identity}".encode("utf-8")).hexdigest()
            persisted_execution["authorization_ref_sha256"] = hashlib.sha256(
                "\0".join(value for _where, value in authorization_hashes).encode("ascii")
            ).hexdigest()
        persisted_execution.pop("authorization_ref", None)
        record, reused = self.store.begin(
            request_id=request_id,
            idempotency_key=key,
            request_hash=digest,
            operation=operation,
            metadata={"operation": operation, "arguments": persisted_arguments, "execution": persisted_execution},
            timeouts=timeouts,
        )
        if reused:
            return record["result"] if record["result"] is not None else self._pending(record)
        job_id = record["job_id"]
        self.store.update_job(job_id, "RUNNING", {"control_plane": True, "engine_dispatched": False})
        self.store.add_event(job_id, "RUNNING", {"operation_id": record["operation_id"], "engine_dispatched": False})
        try:
            result = self.project_authority.dispatch(operation, body)
            status = "SUCCEEDED" if result.get("success") is True else "FAILED"
        except ExecutionContractError as exc:
            result = self._exception(exc)
            status = "FAILED"
        except Exception as exc:
            self._log_exception()
            result = self._error("EXECUTION_STATE_UNKNOWN", "project request state could not be established", type=type(exc).__name__)
            status = "UNKNOWN"
        return self._finish(record, result, status)

    @staticmethod
    def _permission_for_effect(effect: Any) -> str | None:
        return {
            "INSPECT": "inspect", "PROJECT_WRITE": "project_write",
            "READ": "inspect", "WRITE": "project_write", "STATE_WRITE": "project_write",
            "FILE_WRITE": "project_write", "COMPUTE": "compute", "EVALUATE": "project_write",
            "TRUSTED_CODE": "trusted_code", "HOST_CONTROL": "host_control",
        }.get(str(effect).upper())

    def _authorize_project_execution(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> None:
        """Apply project policy to project-tagged work and exact ModelRefs."""
        from . import _g2_registry

        scoped_operation = operation
        if operation in {"registry_call", "operation_call"}:
            candidate = arguments.get("operation_id")
            if isinstance(candidate, str):
                scoped_operation = candidate
        project_id = execution.get("project_id")
        ref = execution.get("model_ref")
        if ref is not None and not isinstance(ref, Mapping):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "execution.model_ref must be an object")
        binding = self.backend.model_project_binding(ref) if isinstance(ref, Mapping) else None
        if binding and binding["attribution"] == "PROJECT_BOUND":
            if project_id is None:
                raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", "project-bound ModelRef requires execution.project_id")
            if project_id != binding["project_id"]:
                raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "execution.project_id differs from the ModelRef project binding")
        elif binding and project_id is not None and scoped_operation not in {"model.adopt", "model_adopt"}:
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "UNATTRIBUTED ModelRef cannot be assigned by an operation")
        if project_id is None:
            return
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("INVALID_REQUEST", "execution.project_id must be a nonempty string")
        entry = _g2_registry.BY_ID.get(scoped_operation)
        effect = entry.effect if entry is not None else None
        if effect is None:
            try:
                from ._g3_ops import EFFECTS as g3_effects
                effect = g3_effects.get(scoped_operation)
            except Exception:
                effect = None
        if effect is None:
            effect = _g2_registry.LEGACY_TOOL_EFFECTS.get(scoped_operation)
        permission = self._permission_for_effect(effect)
        if permission is None:
            raise ExecutionContractError("PROJECT_SCOPE_UNSUPPORTED", "project-scoped operation has no enforced effect mapping")
        self.project_authority.authorize_operation(project_id, permission)

    def _dispatch_catalog_job_resume(self, inner_arguments: dict[str, Any], *, execution: dict[str, Any], timeouts: dict[str, Any]) -> dict[str, Any]:
        source_id = inner_arguments.get("job_id") if isinstance(inner_arguments, Mapping) else None
        source = self.store.job(source_id) if isinstance(source_id, str) else None
        if source is None:
            return self._dispatch_catalog_job_resume_impl(
                inner_arguments, execution=execution, timeouts=timeouts, session_context=None,
            )
        source_operation = self.store.get_operation(source.get("operation_id", ""))
        if source.get("status") != "FAILED" or not source_operation or source_operation.get("status") != "FAILED":
            # This rejection is fully decided by durable job state and must
            # remain responsive even if there is no Worker binding to resolve.
            return self._dispatch_catalog_job_resume_impl(
                inner_arguments, execution=execution, timeouts=timeouts, session_context=None,
            )
        try:
            session_context = self._resolve_job_runtime_context(source, execution)
            request_context = self._execution_session_context(execution)
            if request_context is not session_context:
                raise ExecutionContractError("WORKER_BINDING_MISMATCH", "resume execution does not select the source job's exact Worker context")
            from contextlib import nullcontext
            scope = use_session_context(session_context) if session_context is not None else nullcontext()
            with self._worker_observation_admission(), scope:
                return self._dispatch_catalog_job_resume_impl(
                    inner_arguments, execution=execution, timeouts=timeouts,
                    session_context=session_context,
                )
        except ExecutionContractError as exc:
            return self._exception(exc)

    def _dispatch_catalog_job_resume_impl(self, inner_arguments: dict[str, Any], *, execution: dict[str, Any], timeouts: dict[str, Any], session_context=None) -> dict[str, Any]:
        """Atomically claim and queue one trusted pre-run snapshot restart."""
        from . import _g2_registry
        from ._execution_contract import canonical_project_path, model_ref_from_mapping

        _g2_registry.validate_call("job.resume", inner_arguments)
        forbidden = {"session_id", "model_ref", "expected_revision", "idempotency_key", "request_id", "project_id"}
        present = sorted(forbidden.intersection(inner_arguments))
        if present:
            raise ExecutionContractError("INVALID_REQUEST", "job.resume identity belongs in execution: " + ", ".join(present))
        key = execution.get("idempotency_key")
        request_id = execution.get("request_id") or str(uuid4())
        if not isinstance(key, str) or not key:
            raise ExecutionContractError("INVALID_REQUEST", "job.resume requires execution.idempotency_key")

        source_id = inner_arguments["job_id"]
        source = self.store.job(source_id)
        if not source:
            raise ExecutionContractError("NODE_NOT_FOUND", "source job not found")
        source_operation = self.store.get_operation(source.get("operation_id", ""))
        if source.get("status") != "FAILED" or not source_operation or source_operation.get("status") != "FAILED":
            raise ExecutionContractError("RESUME_SOURCE_NOT_FAILED", "job.resume requires terminal FAILED job and operation states")
        contract = source.get("metadata", {}).get("resume_contract")
        required = {"schema_version", "source_job_id", "source_operation_id", "source_outer_operation",
                    "source_outer_request_hash", "source_model_ref", "source_revision", "source_fingerprint",
                    "source_external_event_counter", "source_model_readback", "original_arguments", "run_arguments", "checkpoint_id",
                    "checkpoint_sha256", "checkpoint_path", "checkpoint_restore_scope", "source_solution_tags", "file_resource_tags"}
        if not isinstance(contract, dict) or not required.issubset(contract):
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "source job has no complete backend-created reentry contract")
        source_op = source_operation or {}
        if (contract.get("schema_version") != 1 or contract.get("source_job_id") != source_id
                or contract.get("source_operation_id") != source.get("operation_id")
                or contract.get("source_outer_operation") != source_op.get("operation")
                or contract.get("source_outer_request_hash") != source_op.get("request_hash")):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "source job/operation does not match the trusted reentry contract")
        source_args = (source_op.get("metadata") or {}).get("arguments")
        if source_op.get("operation") in {"registry_call", "operation_call"} and isinstance(source_args, dict):
            if source_args.get("operation_id") != "study.run" or not isinstance(source_args.get("arguments"), dict):
                raise ExecutionContractError("RESUME_NOT_SUPPORTED", "source fallback request did not call study.run")
            source_args = source_args["arguments"]
        elif source_op.get("operation") != "study.run":
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "only direct or registry study.run jobs are reentrant")
        if source_args != contract.get("original_arguments"):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "source run arguments differ from the trusted contract")
        if source_args.get("recovery_policy") != {"mode": "restart_from_checkpoint"}:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "source study.run did not opt in to snapshot restart")
        replay_args = {name: value for name, value in source_args.items() if name != "recovery_policy"}
        if replay_args != contract.get("run_arguments"):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "replay arguments differ from the source request")
        source_readback = contract.get("source_model_readback")
        if (not isinstance(source_readback, dict) or not isinstance(source_readback.get("signature"), str)
                or len(source_readback["signature"]) != 64 or not isinstance(source_readback.get("state"), dict)):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "source model readback signature is malformed")
        if contract.get("source_solution_tags") != [] or contract.get("file_resource_tags") != []:
            raise ExecutionContractError("RESUME_NOT_SUPPORTED", "source model is outside the no-prior-solutions/no-file-resources allowlist")

        ref_value = execution.get("model_ref")
        if not isinstance(ref_value, dict):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "job.resume requires execution.model_ref")
        ref = model_ref_from_mapping(ref_value)
        if ref.as_dict() != contract.get("source_model_ref") or execution.get("session_id") != ref.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "resume session/ModelRef does not match the source job")
        if self.service is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "managed session is unavailable for resume")
        if not {"compute", "project_write"}.issubset(self.service.ledger.permissions):
            raise ExecutionContractError("PERMISSION_DENIED", "job.resume requires compute and project_write")
        state = self.service.ledger._state_for(ref)
        revision = execution.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision != state.revision:
            raise ExecutionContractError("REVISION_CONFLICT", "job.resume requires the current source model revision")
        authorization_ref = inner_arguments.get("authorization_ref")
        needs_auth = bool(state.dirty or state.revision != contract.get("source_revision")
                          or state.external_event_counter != state.observed_external_event_counter
                          or state.external_event_counter != contract.get("source_external_event_counter"))
        if needs_auth and (not isinstance(authorization_ref, str) or not authorization_ref.strip()):
            raise ExecutionContractError("REVISION_CONFLICT", "dirty or advanced source state requires authorization_ref")

        checkpoint_id = contract.get("checkpoint_id")
        if inner_arguments.get("checkpoint_id") not in (None, checkpoint_id):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "requested checkpoint is not bound to the source job")
        checkpoint = next((row for row in self.store.list_metadata("checkpoints")
                           if row.get("checkpoint_id") == checkpoint_id), None)
        binding = checkpoint.get("source_binding") if isinstance(checkpoint, dict) else None
        if (not isinstance(checkpoint, dict) or not isinstance(binding, dict)
                or checkpoint.get("sha256") != contract.get("checkpoint_sha256")
                or checkpoint.get("path") != contract.get("checkpoint_path")
                or checkpoint.get("resume_source_job_id") != source_id
                or checkpoint.get("resume_source_operation_id") != contract.get("source_operation_id")
                or checkpoint.get("resume_source_readback_signature") != source_readback.get("signature")
                or binding.get("model_ref") != ref.as_dict()
                or binding.get("revision") != contract.get("source_revision")
                or binding.get("fingerprint") != contract.get("source_fingerprint")
                or binding.get("external_event_counter") != contract.get("source_external_event_counter")
                or checkpoint.get("restore_scope") != contract.get("checkpoint_restore_scope")
                or checkpoint.get("restore_scope", {}).get("solution") != "included_by_COMSOL_save_unverified"):
            raise ExecutionContractError("CHECKPOINT_BINDING_MISMATCH", "checkpoint metadata is not the source job's trusted pre-run save")
        path = canonical_project_path(self.backend.project_root, checkpoint["path"])
        if not path.is_file():
            raise ExecutionContractError("ARTIFACT_MISSING", "trusted pre-run checkpoint file is unavailable")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != contract.get("checkpoint_sha256"):
            raise ExecutionContractError("ARTIFACT_MISSING", "trusted pre-run checkpoint SHA-256 mismatch")

        events = []
        while True:
            page = self.store.events(source_id, offset=len(events), limit=1000)
            events.extend(page)
            if len(page) < 1000:
                break
        request_ids = list(dict.fromkeys(row.get("metadata", {}).get("request_id") for row in events
            if row.get("event") == "worker_request" and row.get("metadata", {}).get("phase") == "submitted"
            and row.get("metadata", {}).get("request_id")))
        observed = {row.get("metadata", {}).get("request_id"): row.get("metadata", {}).get("status")
            for row in events if row.get("event") == "worker_request"
            and row.get("metadata", {}).get("phase") == "observed"
            and row.get("metadata", {}).get("status") in {"SUCCEEDED", "FAILED"}}
        if not request_ids:
            raise ExecutionContractError("JOB_NOT_QUIESCENT", "source job has no durable Worker request history")
        if self.worker is None:
            raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connect to the existing Worker before resume")
        observations = []
        for identifier in request_ids:
            status = observed.get(identifier)
            if status is None:
                try:
                    reply = self.session_scheduler.submit(
                        session_context, lambda request_id: self.worker.status(request_id), identifier,
                    ).result()
                    status = reply.get("status") if isinstance(reply, dict) else None
                except Exception:
                    status = None
                self.store.add_event(source_id, "ResumeQuiescenceObserved", {
                    "request_id": identifier, "status": status if isinstance(status, str) else "UNKNOWN",
                    "source": "worker_status_readback",
                })
            observations.append({"request_id": identifier, "status": status})
        if any(row["status"] not in {"SUCCEEDED", "FAILED"} for row in observations):
            raise ExecutionContractError("JOB_NOT_QUIESCENT", "source job has RUNNING, QUEUED or unobserved Worker requests")

        request_hash = canonical_request_hash("job.resume", inner_arguments, ref, revision,
            session_id=execution.get("session_id"), project_id=execution.get("project_id"))
        child_execution = {"session_id": ref.session_id, "model_ref": ref.as_dict(),
                           "expected_revision": revision, "idempotency_key": key, "request_id": request_id}
        if isinstance(execution.get("project_id"), str):
            child_execution["project_id"] = execution["project_id"]
        authorization_hash = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest() \
            if isinstance(authorization_ref, str) and authorization_ref else None
        metadata = {"operation": "study.run", "arguments": replay_args, "execution": child_execution,
                    "resume": {"source_job_id": source_id, "checkpoint_id": checkpoint_id,
                               "restart_mode": "restart_from_checkpoint",
                               "authorization_ref_present": authorization_hash is not None,
                               "authorization_ref_sha256": authorization_hash}}
        runtime_binding = self._runtime_binding_for_request(session_context, child_execution)
        if runtime_binding is not None:
            metadata["runtime_binding"] = runtime_binding
        resume_metadata = {"source_job_id": source_id, "source_operation_id": source.get("operation_id"),
                           "resume_contract": contract,
                           "authorization_ref_present": bool(isinstance(authorization_ref, str) and authorization_ref.strip()),
                           "authorization_ref_sha256": authorization_hash}
        record, reused = self.store.begin_resume(source_job_id=source_id, request_id=request_id,
            idempotency_key=key, request_hash=request_hash, metadata=metadata, timeouts=timeouts)
        if reused:
            return record["result"] if record.get("result") is not None else self._pending(
                record, source_job_id=source_id, restart_mode="restart_from_checkpoint")
        self.store.add_event(record["job_id"], "ResumeContinuationQueued", {
            "source_job_id": source_id, "checkpoint_id": checkpoint_id,
            "restart_mode": "restart_from_checkpoint"})
        submitted = time.monotonic()
        try:
            future = self.session_scheduler.submit(
                session_context, self._execute, record, "study.run", replay_args,
                child_execution, timeouts, submitted, resume_context=resume_metadata,
            )
        except Exception as exc:
            result = self._error("RESUME_QUEUE_FAILED", "resume continuation could not enter the serial Worker queue",
                data={"source_job_id": source_id, "engine_dispatched": False}, cause_type=type(exc).__name__)
            return self._finish(record, result, "FAILED")
        try:
            return future.result(timeout=timeouts["rpc_timeout_s"])
        except FutureTimeout:
            return self._pending(record, rpc_wait_expired=True, source_job_id=source_id,
                                 restart_mode="restart_from_checkpoint")

    @staticmethod
    def _page_cursor_offset(cursor: Any, label: str) -> int:
        """Decode the documented decimal, zero-based page cursor."""
        if not isinstance(cursor, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)", cursor) is None:
            raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a decimal non-negative offset string")
        return int(cursor)

    def _execute(self, record, operation, arguments, execution, timeouts, submitted, *, resume_context=None):
        job_id, operation_id = record["job_id"], record["operation_id"]
        current_job = self.store.job(job_id)
        if current_job and current_job["status"] == "CANCELLED":
            return current_job.get("result") or self._error(
                "OPERATION_CANCELLED",
                "request was cancelled while queued",
                data={"status": "CANCELLED", "engine_dispatched": False},
                safe_retry=True,
            )
        now = time.monotonic()
        if timeouts["queue_timeout_s"] is not None and now - submitted > timeouts["queue_timeout_s"]:
            return self._finish(record, self._error("QUEUE_TIMEOUT", "request expired before engine dispatch", data={"status": "NOT_EXECUTED"}, safe_retry=True), "EXPIRED")
        unresolved = [job for job in self.store.unresolved_jobs()
                      if job["job_id"] != job_id
                      and job["status"] in {"UNKNOWN", "RECONCILING"}
                      and not self._job_quiescence_proven(job)]
        if unresolved and operation not in {"server_connect", "model_inspect"}:
            return self._finish(record, self._error("EXECUTION_STATE_UNKNOWN", "reconcile unfinished engine work before new operations"), "FAILED")
        self.store.update_job(job_id, "RUNNING", {"queue_wait_s": now - submitted, "engine_started_at": time.time()})
        current_job = self.store.job(job_id)
        if current_job and current_job["status"] == "CANCELLED":
            return current_job.get("result") or self._error(
                "OPERATION_CANCELLED",
                "request was cancelled before engine execution",
                data={"status": "CANCELLED", "engine_dispatched": False},
                safe_retry=True,
            )
        self.store.add_event(job_id, "RUNNING", {"operation_id": operation_id, "at": time.time()})
        with self.lock:
            self.running[job_id] = {"started": now, "last_event": now, "timeouts": timeouts, "execution_warned": False, "progress_warned": False}
        def worker_event(event):
            # Synchronous submission events commit before a Java request is sent.
            self.store.add_event(job_id, "worker_request", event)
            with self.lock:
                if job_id in self.running:
                    self.running[job_id]["last_event"] = time.monotonic()
        try:
            project_id = execution.get("project_id")
            if project_id is not None:
                # Project policy is control-plane state and may be narrowed
                # while this job is waiting in the serial engine queue. Check
                # again at the dispatch boundary so queued work cannot rely on
                # an admission-time grant that has since been revoked.
                self._authorize_project_execution(operation, arguments, execution)
                timeouts = self.project_authority.apply_timeout_caps(project_id, timeouts)
                project_record = self.project_authority.get_project(project_id)
                project_scope = self.backend.project_root_scope(project_record["workspace"])
            else:
                from contextlib import nullcontext
                project_scope = nullcontext()
            with project_scope:
                if resume_context is None:
                    result = self.backend.invoke(operation, arguments, execution, operation_id, worker_event)
                else:
                    result = self.backend.resume_study_run(
                        resume_context, arguments, execution, operation_id, worker_event,
                    )
            if not isinstance(result, dict) or type(result.get("success")) is not bool:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "backend returned an invalid execution envelope")
            detail = result.get("data") if isinstance(result.get("data"), dict) else {}
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            unknown = bool(
                result.get("execution_state_unknown")
                or result.get("cleanup_failed")
                or detail.get("execution_state_unknown")
                or detail.get("cleanup_failed")
                or error.get("code") in {"EXECUTION_STATE_UNKNOWN", "UNKNOWN"}
            )
            status = "UNKNOWN" if unknown else ("SUCCEEDED" if result["success"] else "FAILED")
            if result.get("execution", {}).get("dirty"):
                # A completed callback with a dirty model is a failed operation;
                # explicit model reconciliation is still required for next writes.
                result.setdefault("data", {})["requires_model_reconciliation"] = True
        except ExecutionContractError as exc:
            result = self._exception(exc)
            if exc.code == "EXECUTION_STATE_UNKNOWN":
                status = "UNKNOWN"
            elif exc.code == "ENGINE_UNRESPONSIVE":
                # A transport failure before the callback was dispatched leaves no
                # engine work to reconcile: report it as a retryable failure
                # instead of an unknown job that blocks every later operation.
                status = "FAILED" if exc.safe_retry else "UNKNOWN"
            else:
                status = "FAILED"
        except Exception as exc:
            self._log_exception()
            result = self._error("EXECUTION_STATE_UNKNOWN", "backend execution failed; inspect worker evidence", type=type(exc).__name__)
            status = "UNKNOWN"
        finally:
            with self.lock:
                self.running.pop(job_id, None)
        return self._finish(record, result, status)

    def _finish(self, record, result, status):
        execution = result.setdefault("execution", {})
        execution.update({key: record[key] for key in ("request_id", "operation_id", "request_hash", "idempotency_key", "job_id")})
        accepted, authoritative_status = self.store.finish(record["operation_id"], status=status, result=result)
        if not accepted:
            # F02: The store rejected this write because the job is already in a
            # terminal state.  Return the authoritative persistent result instead
            # of the candidate that was just rejected, and emit the authoritative
            # status event so RPC/SQLite/events are all consistent.
            job = self.store.job(record["job_id"])
            authoritative_result = (job.get("result") if job else None) or result
            self.store.add_event(record["job_id"], "LateResultRecorded", {
                "operation_id": record["operation_id"],
                "rejected_status": status,
                "authoritative_status": authoritative_status,
                "at": time.time(),
            })
            return authoritative_result
        self.store.update_job(record["job_id"], authoritative_status, {"finished_observed_at": time.time()})
        self.store.add_event(record["job_id"], authoritative_status, {"operation_id": record["operation_id"], "at": time.time()})
        return result

    def _pending(self, record, **extra):
        job_id = record.get("job_id")
        if not job_id:
            job = self.store.operation_job(record["operation_id"])
            job_id = job["job_id"]
        job = self.store.job(job_id)
        if job and job.get("result") is not None:
            return job["result"]
        return {"success": True, "data": {"job_id": job_id, "status": job["status"] if job else "UNKNOWN", **extra},
                "execution": {**{k: record[k] for k in ("request_id", "operation_id", "request_hash", "idempotency_key")}, "job_id": job_id}}

    def _control_read(self, operation, arguments):
        if operation in {"registry_list", "registry_describe", "registry_search", "registry_manifest", "operation_describe"}:
            from . import _g2_registry
            try:
                if operation == "registry_list":
                    data = _g2_registry.registry_list(domain=arguments.get("domain"), cursor=arguments.get("cursor"), limit=arguments.get("limit", 100))
                elif operation == "registry_describe":
                    data = _g2_registry.registry_describe(arguments.get("operation_id", ""))
                elif operation == "operation_describe":
                    data = _g2_registry.operation_describe(arguments.get("operation_id", ""))
                elif operation == "registry_search":
                    data = _g2_registry.registry_search(arguments.get("query", ""), domain=arguments.get("domain"))
                else:
                    data = _g2_registry.registry_manifest(arguments.get("profile"))
                return {"success": True, "data": data}
            except ExecutionContractError as exc:
                return self._exception(exc)
        if operation in {"docs_search", "docs_get", "docs_examples", "docs_error_search"}:
            try:
                index = self.backend.docs_index
                if operation == "docs_search":
                    data = index.search(query=arguments.get("query", ""), version=arguments.get("version", ""), product=arguments.get("product"), limit=arguments.get("limit", 10))
                elif operation == "docs_get":
                    data = index.get(document_ref=arguments.get("document_ref", ""), section=arguments.get("section"), offset=arguments.get("offset", 0), length=arguments.get("length", 6000))
                else:
                    query = arguments.get("query", arguments.get("error", ""))
                    data = index.search(query=query, version=arguments.get("version", ""), product=arguments.get("node_type"), limit=arguments.get("limit", 10))
                return {"success": True, "data": data}
            except ExecutionContractError as exc:
                return self._exception(exc)
            except Exception as exc:
                return self._error("UNAVAILABLE", "offline documentation index is unavailable", type=type(exc).__name__)
        if operation in {"checkpoint_list", "checkpoint_inspect", "checkpoint_diff"}:
            try:
                rows = self.store.list_metadata("checkpoints")
                if operation == "checkpoint_list":
                    return {"success": True, "data": {"checkpoints": rows}}
                checkpoint_id = arguments.get("checkpoint_id") or arguments.get("left")
                if operation == "checkpoint_inspect":
                    value = next((row for row in rows if row.get("checkpoint_id") == checkpoint_id or row.get("sha256") == checkpoint_id), None)
                    return {"success": bool(value), "data": value or {}, "error": None if value else {"code": "NODE_NOT_FOUND", "message": "checkpoint not found", "safe_retry": False}}
                left = arguments.get("left"); right = arguments.get("right")
                lrow = next((row for row in rows if row.get("checkpoint_id") == left or row.get("sha256") == left), None)
                rrow = next((row for row in rows if row.get("checkpoint_id") == right or row.get("sha256") == right), None)
                return {"success": bool(lrow and rrow), "data": {"left": lrow, "right": rrow, "equal": bool(lrow and rrow and lrow.get("sha256") == rrow.get("sha256"))}}
            except Exception as exc:
                return self._error("UNAVAILABLE", "checkpoint metadata is unavailable", type=type(exc).__name__)
        if operation in {"session_health", "server_info"}:
            with self.lock:
                active = list(self.running)
            return {"success": True, "data": {"status": "READY", "control_pid": os.getpid(),
                "worker_connected": self.backend.cached.get("connected", False),
                "worker": dict(self.worker_health), "session": dict(self.backend.cached), "active_jobs": active,
                "cancellation_capabilities": {
                    "native_cooperative_cancel": "UNSUPPORTED",
                    "queued_cancel": "VERIFIED",
                    "owned_process_termination": "VERIFIED",
                }},
                "execution": {"session_id": self.backend.cached.get("session_id")}}
        if operation in {"job_list", "job.list"}:
            offset = arguments.get("offset", 0)
            limit = arguments.get("limit", 50)
            status = arguments.get("status")
            project_id = arguments.get("project_id")
            if type(offset) is not int or offset < 0:
                return self._error("INVALID_REQUEST", "offset must be a non-negative integer")
            if type(limit) is not int or not 1 <= limit <= 1000:
                return self._error("INVALID_REQUEST", "limit must be an integer between 1 and 1000")
            if status is not None and not isinstance(status, str):
                return self._error("INVALID_REQUEST", "status filter must be a string")
            if project_id is not None and not isinstance(project_id, str):
                return self._error("INVALID_REQUEST", "project_id filter must be a string")
            jobs = self.store.list_jobs(offset=offset, limit=limit, status=status, project_id=project_id)
            return {
                "success": True,
                "data": {
                    "jobs": list(jobs),
                    "total": getattr(jobs, "total", len(jobs)),
                    "offset": offset,
                    "limit": limit,
                    "has_more": getattr(jobs, "has_more", False),
                },
            }
        if operation in {"job_wait", "job.wait"}:
            job_id = arguments.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                return self._error("INVALID_REQUEST", "job_id must be a non-empty string")
            job = self.store.job(job_id)
            if not job:
                return self._error("NODE_NOT_FOUND", "job not found")
            timeout_s = arguments.get("timeout_s", 30.0)
            poll_interval_s = arguments.get("poll_interval_s", 0.05)
            if timeout_s is not None and (type(timeout_s) not in (int, float) or timeout_s < 0 or not math.isfinite(timeout_s)):
                return self._error("INVALID_REQUEST", "timeout_s must be a non-negative finite number or null")
            if type(poll_interval_s) not in (int, float) or poll_interval_s <= 0 or not math.isfinite(poll_interval_s):
                poll_interval_s = 0.05
            poll_interval_s = max(0.01, min(poll_interval_s, 1.0))

            start_wait = time.monotonic()
            while True:
                job = self.store.job(job_id)
                if not job:
                    return self._error("NODE_NOT_FOUND", "job not found")
                if job["status"] in TERMINAL:
                    return {"success": True, "data": job}
                if timeout_s is not None and time.monotonic() - start_wait >= timeout_s:
                    return {"success": True, "data": {**job, "wait_expired": True}}
                time.sleep(poll_interval_s)
        if operation in {"job_cancel", "job.cancel"}:
            if arguments.get("force_stop") is True:
                return self._error(
                    "WORKER_BINDING_UNKNOWN",
                    "force-stop requires routing through the durable job Worker binding",
                    safe_retry=False,
                )
            return self._cancel_job(arguments)

        norm_op = operation.replace(".", "_")
        job_id = arguments.get("job_id")
        job = self.store.job(job_id) if isinstance(job_id, str) else None
        if not job:
            return self._error("NODE_NOT_FOUND", "job not found")
        if norm_op == "job_log":
            offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
            if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 1000:
                return self._error("INVALID_REQUEST", "invalid log offset/limit")
            events = self.store.events(job_id, offset=offset, limit=limit)
            return {"success": True, "data": {"job_id": job_id, "events": events, "next_offset": offset + len(events)}}
        if norm_op == "job_reconcile" and job["status"] in {"UNKNOWN", "RECONCILING"}:
            return self._error(
                "WORKER_BINDING_UNKNOWN",
                "reconciliation requires routing through the durable job Worker binding",
                safe_retry=False,
            )
        success = not (norm_op == "job_result" and job.get("result") is not None and not job["result"].get("success"))
        return {"success": success, "data": job}

    def _cancel_job(self, arguments: dict[str, Any]) -> dict[str, Any]:
        job_id = arguments.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return self._error("INVALID_REQUEST", "job_id must be a non-empty string")
        job = self.store.job(job_id)
        if not job:
            return self._error("NODE_NOT_FOUND", "job not found")

        reason = str(arguments.get("reason", "cancelled by user"))
        status = job["status"]

        # D02: Parameter and authorization validation MUST happen BEFORE changing any state
        if "force_stop" in arguments:
            raw_fs = arguments["force_stop"]
            if type(raw_fs) is not bool:
                return self._error("INVALID_REQUEST", f"force_stop must be a boolean, got {type(raw_fs).__name__}", safe_retry=False)
            force_stop = raw_fs
        else:
            force_stop = False

        if force_stop:
            server_scope = arguments.get("server_scope")
            if not isinstance(server_scope, dict):
                return self._error(
                    "UNAUTHORIZED_FORCE_STOP",
                    "force_stop requires explicit server_scope authorization with verified ownership",
                    data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
                )
            if "authorized" not in server_scope or type(server_scope["authorized"]) is not bool:
                return self._error("INVALID_REQUEST", "server_scope.authorized must be a boolean", safe_retry=False)
            if not server_scope["authorized"]:
                return self._error(
                    "UNAUTHORIZED_FORCE_STOP",
                    "force_stop requires explicit server_scope authorization with verified ownership",
                    data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
                )

        if status in TERMINAL:
            return {
                "success": True,
                "data": {
                    "job_id": job_id,
                    "status": status,
                    "cancel_accepted": False,
                    "reason": f"job already in terminal state {status}",
                    "engine_stopped": False,
                    "mode": "TERMINAL_NOOP",
                },
            }

        if status == "QUEUED":
            success, code, updated_job = self.store.cancel_queued(job_id, reason=reason)
            if success:
                return {
                    "success": True,
                    "data": {
                        "job_id": job_id,
                        "status": "CANCELLED",
                        "cancel_accepted": True,
                        "engine_dispatched": False,
                        "engine_stopped": False,
                        "mode": "QUEUED_ABORT",
                    },
                }
            job = self.store.job(job_id) or job
            status = job["status"]
            if status in TERMINAL:
                return {
                    "success": True,
                    "data": {
                        "job_id": job_id,
                        "status": status,
                        "cancel_accepted": False,
                        "reason": f"job transitioned to {status} during cancel",
                        "engine_stopped": False,
                        "mode": "TERMINAL_NOOP",
                    },
                }

        if force_stop:
            server_scope = arguments.get("server_scope")
            return self._scoped_force_stop(job, reason=reason, server_scope=server_scope)

        # D02 FIX: For non-force_stop cancellation, record cancel intent
        self.store.add_event(job_id, "CancelRequested", {
            "reason": reason,
            "mode": "UNSUPPORTED_NATIVE_CANCEL",
            "cancel_accepted": True,
            "engine_stopped": False,
            "at": time.time(),
        })

        # D02: If status is UNKNOWN or RECONCILING, keep observed status! Never write back RUNNING!
        target_status = status
        self.store.update_job(job_id, target_status, {
            "cancel_requested": True,
            "cancel_reason": reason,
        })
        return {
            "success": True,
            "data": {
                "job_id": job_id,
                "status": target_status,
                "cancel_accepted": True,
                "engine_stopped": False,
                "cancel_requested": True,
                "mode": "UNSUPPORTED_NATIVE_CANCEL",
                "message": f"native cooperative solver cancel is unsupported by current live solver adapter; cancel request recorded (status={target_status})",
                "cancellation_routes": {
                    "native_cooperative_cancel": "UNSUPPORTED",
                    "queued_cancel": "VERIFIED",
                    "owned_process_termination": "VERIFIED",
                },
            },
        }

    def _scoped_force_stop(self, job: dict[str, Any], *, reason: str, server_scope: Any) -> dict[str, Any]:
        job_id = job["job_id"]
        if not isinstance(server_scope, dict):
            return self._error(
                "UNAUTHORIZED_FORCE_STOP",
                "force_stop requires explicit server_scope authorization with verified ownership",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
            )
        if "authorized" not in server_scope or type(server_scope["authorized"]) is not bool:
            return self._error("INVALID_REQUEST", "server_scope.authorized must be a boolean", safe_retry=False)
        if not server_scope["authorized"]:
            return self._error(
                "UNAUTHORIZED_FORCE_STOP",
                "force_stop requires explicit server_scope authorization with verified ownership",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
            )

        service = self.service
        if service is None or getattr(service, "is_shared", True):
            return self._error(
                "CANNOT_TERMINATE_SHARED_SERVER",
                "force-stop is strictly forbidden on shared, external, or unverified servers",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
            )

        context = active_session_context()
        owned_process = getattr(getattr(context, "endpoint", None), "owned_process", None)
        observed_peer = getattr(getattr(context, "endpoint", None), "observed_peer", None)
        if (context is None or context.server_ownership != "mcp_managed"
                or owned_process is None or observed_peer is None
                or not owned_process.attests_peer(observed_peer)
                or context.process_identity != owned_process):
            return self._error(
                "PROCESS_IDENTITY_UNKNOWN",
                "force-stop requires the job's registered, observed MCP-owned process identity",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "IDENTITY_REJECTED"},
            )

        # D04: Validate backend ServerLease / RuntimeOwnership if present
        backend_lease = getattr(service, "lease", None) or getattr(service, "server_lease", None) or getattr(service, "runtime_ownership", None)
        if backend_lease is not None:
            req_lease_id = server_scope.get("lease_id")
            expected_lease_id = getattr(backend_lease, "lease_id", None) or (backend_lease.get("lease_id") if isinstance(backend_lease, dict) else None)
            if expected_lease_id and req_lease_id != expected_lease_id:
                return self._error(
                    "UNAUTHORIZED_FORCE_STOP",
                    f"server_scope lease_id {req_lease_id} does not match backend authorized lease {expected_lease_id}",
                    data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "FORCE_STOP_REJECTED"},
                )

        managed_pid = getattr(service, "server_pid", None) or getattr(service, "pid", None)
        target_pid = server_scope.get("pid")
        if (not managed_pid or managed_pid != owned_process.pid
                or target_pid != owned_process.pid):
            return self._error(
                "PROCESS_IDENTITY_MISMATCH",
                "server_scope PID does not match the job's exact owned-process binding",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "IDENTITY_REJECTED"},
            )

        ident = process_identity(managed_pid)
        scope_start = server_scope.get("process_start_epoch_ms")
        live_start = ident.get("start_epoch_ms")
        stored_start = owned_process.start_epoch_ms
        if (ident.get("alive") is not True
                or type(live_start) is not int or live_start <= 0
                or type(scope_start) is not int or scope_start <= 0
                or type(stored_start) is not int or stored_start <= 0):
            return self._error(
                "PROCESS_IDENTITY_UNKNOWN",
                "stored, live, and authorized process birth identities must all be available",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "IDENTITY_REJECTED"},
            )
        if not (stored_start == live_start == scope_start):
            return self._error(
                "PROCESS_IDENTITY_MISMATCH",
                "live and authorized process birth do not match the backend-owned process identity",
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False, "mode": "IDENTITY_REJECTED"},
            )

        try:
            self.session_scheduler.fence_owned_server_lane(context)
        except Exception as exc:
            return self._error(
                "PROCESS_IDENTITY_UNKNOWN",
                "owned server lane could not be fenced before termination",
                safe_retry=False,
                data={"job_id": job_id, "cancel_accepted": False, "engine_stopped": False,
                      "mode": "IDENTITY_REJECTED", "cause_type": type(exc).__name__},
            )

        # D04: Replace raw os.kill(pid, 9) with platform terminate_process_tree and wait for exit
        stopped = terminate_process_tree(managed_pid, timeout_s=5.0)
        if not stopped:
            self.store.update_job(job_id, "UNKNOWN", {
                "cancel_requested": True,
                "engine_stopped": False,
                "mode": "TERMINATION_UNCONFIRMED",
                "reason": reason,
            })
            return self._error(
                "TERMINATION_FAILED",
                f"process {managed_pid} could not be confirmed dead within timeout; retaining UNKNOWN status",
                safe_retry=False,
                data={"job_id": job_id, "cancel_accepted": True, "engine_stopped": False, "status": "UNKNOWN"},
            )

        # Invalidate runtime / worker handles if service supports it
        if hasattr(service, "invalidate_runtime"):
            try: service.invalidate_runtime()
            except Exception: pass
        elif hasattr(service, "worker") and hasattr(service.worker, "close"):
            try: service.worker.close()
            except Exception: pass

        # D03 & D04: Update job and operations with synchronized CANCELLED result
        cancel_result = {
            "success": False,
            "data": {
                "status": "CANCELLED",
                "job_id": job_id,
                "engine_stopped": True,
                "mode": "SCOPED_PROCESS_TERMINATION",
            },
            "error": {
                "code": "OPERATION_CANCELLED",
                "message": f"job was force-stopped: {reason}",
                "safe_retry": False,
                "engine_stopped": True,
            },
            "execution": {"job_id": job_id, "operation_id": job.get("operation_id")},
        }
        self.store.update_job(
            job_id,
            "CANCELLED",
            {"engine_stopped": True, "mode": "SCOPED_PROCESS_TERMINATION", "reason": reason},
            result=cancel_result,
        )
        self.store.add_event(job_id, "CANCELLED", {
            "engine_stopped": True,
            "mode": "SCOPED_PROCESS_TERMINATION",
            "reason": reason,
        })
        return {
            "success": True,
            "data": {
                "job_id": job_id,
                "status": "CANCELLED",
                "cancel_accepted": True,
                "engine_stopped": True,
                "mode": "SCOPED_PROCESS_TERMINATION",
            },
        }

    def _reconcile(self, job):
        # Query existing Java request ids only. Never submit the lost callback.
        events = []
        while True:
            page = self.store.events(job["job_id"], offset=len(events), limit=1000)
            events.extend(page)
            if len(page) < 1000:
                break
        ids = list(dict.fromkeys(event["metadata"].get("request_id") for event in events if event["event"] == "worker_request" and event["metadata"].get("phase") == "submitted"))
        completed = {event["metadata"]["request_id"]: event["metadata"]["status"] for event in events
                     if event["event"] == "worker_request" and event["metadata"].get("phase") == "observed"
                     and event["metadata"].get("request_id") and event["metadata"].get("status") in {"SUCCEEDED", "FAILED"}}
        pending_ids = [identifier for identifier in ids if identifier not in completed]
        if pending_ids and self.worker is None:
            return {"success": False, "data": job, "error": {
                "code": "EXECUTION_STATE_UNKNOWN",
                "message": "the job's bound Worker is unavailable for reconciliation",
                "safe_retry": False,
            }}
        observed = []
        for identifier in ids:
            if identifier in completed:
                # A replacement Worker cannot know the old request IDs. The
                # committed completion observation remains valid evidence that
                # this particular API call returned before the old Worker died.
                observed.append({"request_id": identifier, "status": completed[identifier], "source": "durable_worker_observation"})
            else:
                try:
                    observed.append(self.worker.status(identifier))
                except Exception:
                    observed.append({"request_id": identifier, "status": "UNKNOWN", "message": "worker request status unavailable"})
        active = any(item.get("status") in {"QUEUED", "RUNNING"} for item in observed)
        quiescent = bool(ids) and len(observed) == len(ids) and all(item.get("status") in {"SUCCEEDED", "FAILED"} for item in observed)
        status = "RECONCILING" if active else "UNKNOWN"
        self.store.update_job(job["job_id"], status, {"reconciliation": observed, "reconciled_quiescent": quiescent,
            "replay_performed": False, "callback_completion_unknown": True})
        return {"success": True, "data": self.store.job(job["job_id"])}

    def _monitor(self):
        health_at = 0.0
        while not self.closed.wait(0.05):
            now = time.monotonic()
            with self.lock:
                for job_id, state in list(self.running.items()):
                    duration = state["timeouts"]["execution_timeout_s"]
                    warning = state["timeouts"]["no_progress_warning_s"]
                    if duration is not None and not state["execution_warned"] and now - state["started"] >= duration:
                        state["execution_warned"] = True
                        self.store.add_event(job_id, "ExecutionDeadlineExceeded", {"safe_retry": False, "engine_stopped": False})
                        self.store.update_job(job_id, "RUNNING", {"execution_deadline_exceeded": True, "safe_retry": False})
                    if warning is not None and not state["progress_warned"] and now - state["last_event"] >= warning:
                        state["progress_warned"] = True
                        self.store.add_event(job_id, "NoProgressWarning", {"progress_percentage": None, "engine_stopped": False})
            if (self.worker is not None and callable(getattr(self.worker, "health", None))
                    and now - health_at >= 0.5):
                health_at = now
                with self._admission_lock:
                    may_observe = self._worker_retirement_state == "OPEN" and not self.closed.is_set()
                    if may_observe:
                        self._worker_admission_inflight += 1
                if may_observe:
                    try:
                        self.worker_health = {**self.worker.health(timeout_s=0.2), "observed_at": time.time()}
                    except Exception:
                        self.worker_health = {"status": "UNKNOWN", "observed_at": time.time()}
                    finally:
                        with self._admission_lock:
                            self._worker_admission_inflight -= 1
                            self._dispatch_condition.notify_all()

    @staticmethod
    def _timeouts(execution):
        out = {}
        for name in ("rpc_timeout_s", "queue_timeout_s", "execution_timeout_s", "no_progress_warning_s"):
            value = execution.get(name, 30.0 if name == "rpc_timeout_s" else None)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ExecutionContractError("INVALID_REQUEST", f"{name} must be a non-negative finite number or null")
            out[name] = value
        if out["rpc_timeout_s"] is None:
            out["rpc_timeout_s"] = 30.0
        return out

    @staticmethod
    def _error(code, message, *, data=None, **details):
        return {"success": False, "data": data or {}, "error": {"code": code, "message": message, "safe_retry": False, **details}}

    @classmethod
    def _exception(cls, exc):
        if isinstance(exc, ExecutionContractError):
            return {"success": False, "data": {}, "error": exc.as_dict()}
        return cls._error("IDEMPOTENCY_CONFLICT", str(exc))

    def _log_exception(self):
        with (self.home / "control-errors.log").open("a") as stream:
            traceback.print_exc(file=stream)

    def _retire_owned_session_workers_for_close(self) -> None:
        """Run the public exact Worker retirement path before daemon teardown.

        Only children for which this daemon retained both a Popen object and
        exact process-birth readback are considered. Uncertain, busy, unknown,
        or remotely attached Workers remain untouched.
        """
        with self._session_connect_lock:
            for key in list(self._session_worker_handles):
                if key not in self._session_worker_child_identities:
                    continue
                project_id, session_id = key
                try:
                    lifecycle = self.session_lifecycle.get(project_id, session_id)
                    if (lifecycle is None or lifecycle.get("state") not in {"CONNECTED", "DISCONNECTED"}
                            or lifecycle.get("client_state") == "RETIRED"):
                        continue
                    operation_id = f"daemon-close:{uuid4().hex}"
                    arguments = {
                        "project_id": project_id,
                        "session_id": session_id,
                        "idempotency_key": operation_id,
                        "retire_worker": True,
                    }
                    execution = {
                        "project_id": project_id,
                        "request_id": operation_id,
                        "idempotency_key": operation_id,
                    }
                    # This enters the same catalog validation, authorization,
                    # quiescence fence, disconnect, exact close/wait, and
                    # durable lifecycle route exposed to callers.
                    self._dispatch_session_control("session.disconnect", arguments, execution)
                except Exception:
                    # Shutdown remains fail-closed. A cleanup refusal never
                    # falls back to PID signalling or drops an uncertain handle.
                    continue

    def _stop_owned_servers_for_close(self) -> None:
        """Stop only exact daemon-started Servers after Worker/scheduler drain."""
        with self._session_connect_lock:
            for key, handle in list(self._session_server_handles.items()):
                project_id, session_id = key
                if key in self._session_worker_handles:
                    continue
                lifecycle = None
                close_resolution_started = False
                try:
                    lifecycle = self.session_lifecycle.get(project_id, session_id)
                    if (lifecycle is None or lifecycle.get("state") != "DISCONNECTED"
                            or lifecycle.get("client_state") not in {"DISCONNECTED", "RETIRED"}
                            or lifecycle.get("server_ownership") != "mcp_managed"):
                        continue
                    if self._session_has_unresolved_jobs(project_id, session_id):
                        continue
                    close_resolution_started = True
                    exact_handle, identity = self._owned_server_handle(project_id, session_id, lifecycle)
                    if exact_handle is not handle:
                        continue
                    other_binding = self._other_session_binding_uses_owned_server(key, identity)
                    if other_binding is not False:
                        self._mark_owned_server_unknown_for_close(lifecycle)
                        continue
                    evidence = self.session_server_launcher.stop(handle)
                    if (not isinstance(evidence, Mapping) or evidence.get("server_stopped") is not True
                            or evidence.get("child_reaped") is not True
                            or evidence.get("listener_absent") is not True
                            or evidence.get("pid") != identity.pid
                            or evidence.get("birth") != identity.birth):
                        raise OwnedServerError(
                            "owned Server stop adapter returned incomplete exact-exit evidence",
                            handle=handle, uncertain=True,
                        )
                    stopped = self._updated_session_lifecycle(
                        lifecycle, state="STOPPED", client_state=lifecycle["client_state"],
                        worker_instance_id=lifecycle.get("worker_instance_id"),
                        worker_epoch=lifecycle.get("worker_epoch"), server_instance_id=None,
                        server_state="STOPPED", server_ownership="unknown",
                        server_process_identity=None,
                    )
                    self.session_lifecycle.save(stopped, expected_revision=lifecycle["revision"])
                    self._session_server_handles.pop(key, None)
                    self._session_runtime_configs.pop(key, None)
                    self._session_process_identities.pop(key, None)
                except Exception:
                    if lifecycle is not None and close_resolution_started:
                        # close() cannot report an uncertain stop to a caller.
                        # Persist UNKNOWN so a later daemon never reads the old
                        # DISCONNECTED row as proof that the Server is healthy.
                        self._mark_owned_server_unknown_for_close(lifecycle)
                    # Unknown/busy/missing-birth processes are deliberately
                    # retained. Daemon shutdown never falls back to PID kill.
                    continue

    def close(self):
        with self._dispatch_condition:
            if self._close_complete:
                return
            if self._close_started:
                while not self._close_complete:
                    self._dispatch_condition.wait()
                return
            self._close_started = True
            # This shares the public dispatch admission lock, so there is no
            # check-then-block window in which a request can create a child
            # after close observed and retired the existing handle set.
            self._closing = True
            self._worker_retirement_state = "CLOSED"
            while self._control_dispatch_inflight or self._worker_admission_inflight:
                self._dispatch_condition.wait()
        try:
            self._retire_owned_session_workers_for_close()
        except Exception:
            # Retain exact handles and proceed with daemon teardown only after
            # the scheduler has drained accepted work below.
            pass
        with self._admission_lock:
            self.closed.set()
        try:
            self.session_scheduler.close()
            self.queue.shutdown(wait=True)
            # The first exact cleanup pass refuses busy work. Once every
            # accepted lane has drained, retry only through the same public
            # disconnect/retirement path; durable UNKNOWN work still blocks.
            self._shutdown_tasks_drained = True
            try:
                self._retire_owned_session_workers_for_close()
            except Exception:
                pass
            try:
                self._stop_owned_servers_for_close()
            except Exception:
                pass
            self.monitor.join(timeout=2)
            if hasattr(self, "backend") and hasattr(self.backend, "close"):
                try:
                    self.backend.close()
                except Exception:
                    pass
        finally:
            try:
                self.store.close()
            finally:
                with self._dispatch_condition:
                    self._close_complete = True
                    self._dispatch_condition.notify_all()


def serve(home):
    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    singleton = ProcessLock(home / "control.lock")
    try:
        from ._state_backup import assert_no_pending_restore
        assert_no_pending_restore(home)
    except Exception:
        singleton.close()
        raise
    token = secrets.token_urlsafe(32)
    daemon = ControlDaemon(home)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/rpc" or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(403); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8 * 1024 * 1024:
                    self.send_error(413); return
                result = daemon.dispatch(json.loads(self.rfile.read(length)))
                body = json.dumps(result, allow_nan=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The durable job keeps running when its caller disappears.
            except Exception:
                self.send_error(500, "internal control error")
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    temporary = home / "control.json.tmp"
    endpoint = {"port": server.server_port, "token": token, "pid": os.getpid()}
    start_epoch_ms = process_identity(os.getpid())["start_epoch_ms"]
    if isinstance(start_epoch_ms, int):
        endpoint["process_start_epoch_ms"] = start_epoch_ms
    temporary.write_text(json.dumps(endpoint))
    os.chmod(temporary, 0o600)
    temporary.replace(home / "control.json")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    serve(parser.parse_args().home)
