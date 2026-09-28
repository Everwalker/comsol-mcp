"""Durable job control with one serial engine queue and responsive cached reads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import itertools
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
    IdempotencyConflict, JobCleanupError, OperationStore, StagePlanStoreConflict,
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
    SessionIdentityConflict,
    active_session_context,
    use_session_context,
)
from ._session_server import ManagedServerHandle, OwnedServerError, OwnedServerLauncher
from ._session_lifecycle import (
    SessionLifecycleStore,
    SessionLifecycleProjectConflict,
    new_lifecycle_record,
    validate_lifecycle_record,
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
EXPERIMENT_DURABLE_READS = frozenset({"experiment.inspect", "experiment.case_result"})

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
            if operation in {"experiment.stage_define", "experiment_stage_define"}:
                return self._dispatch_experiment_stage_define(arguments, execution)
            if operation in {"experiment.stage_run", "experiment_stage_run"}:
                return self._dispatch_experiment_stage_run(arguments, execution)
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
            if operation in EXPERIMENT_DURABLE_READS:
                return self._dispatch_experiment_durable_read(operation, arguments, execution)
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
            if operation in {"metric.define", "metric.list", "metric.evaluate", "metric.remove", "metric.compare"}:
                from ._g2_registry import validate_call
                identity_fields = ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id")
                scoped = dict(arguments)
                normalized_execution = dict(execution)
                for field in identity_fields:
                    body_value = scoped.get(field)
                    outer_value = normalized_execution.get(field)
                    if body_value is not None and outer_value is not None and body_value != outer_value:
                        code = "IDEMPOTENCY_CONFLICT" if field == "idempotency_key" else "MODEL_IDENTITY_MISMATCH"
                        raise ExecutionContractError(code, f"metric {field} differs between arguments and execution envelope")
                    value = outer_value if outer_value is not None else body_value
                    if value is not None:
                        scoped[field] = value
                        normalized_execution[field] = value
                validate_call(operation, scoped)
                if normalized_execution.get("project_id") != scoped.get("project_id"):
                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "metric project_id must match the execution envelope")
                arguments = {key: value for key, value in arguments.items() if key not in identity_fields}
                execution = normalized_execution
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
        if inner_operation in EXPERIMENT_DURABLE_READS:
            if extra_outer:
                raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
            inner_arguments = outer_arguments.get("arguments", {})
            if not isinstance(inner_arguments, dict):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            return self._dispatch_experiment_durable_read(inner_operation, inner_arguments, execution)
        if inner_operation in {"experiment.stage_define", "experiment_stage_define"}:
            if extra_outer:
                raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
            inner_arguments = outer_arguments.get("arguments", {})
            if not isinstance(inner_arguments, dict):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            return self._dispatch_experiment_stage_define(inner_arguments, execution)
        if inner_operation in {"experiment.stage_run", "experiment_stage_run"}:
            if extra_outer:
                raise ExecutionContractError("INVALID_REQUEST", f"{outer_operation} has unsupported arguments: {', '.join(extra_outer)}")
            inner_arguments = outer_arguments.get("arguments", {})
            if not isinstance(inner_arguments, dict):
                raise ExecutionContractError("INVALID_REQUEST", "registry call arguments must be an object")
            return self._dispatch_experiment_stage_run(inner_arguments, execution)
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

    @staticmethod
    def _experiment_record_sha256(record: Mapping[str, Any]) -> str:
        payload = {key: value for key, value in record.items() if key != "sha256"}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _experiment_operation_project(operation_record: Mapping[str, Any], project_id: str) -> bool:
        """Require a producer operation's durable metadata to prove project ownership."""
        metadata = operation_record.get("metadata")
        job_metadata = operation_record.get("job_metadata")
        if not isinstance(metadata, Mapping) or not isinstance(job_metadata, Mapping):
            return False
        claims: list[str] = []
        operation_claims: list[str] = []
        argument_sources = [metadata.get("arguments")]
        outer_arguments = metadata.get("arguments")
        if (operation_record.get("operation") in {"registry_call", "operation_call"}
                and isinstance(outer_arguments, Mapping)):
            argument_sources.append(outer_arguments.get("arguments"))
        for source in (metadata, metadata.get("execution"), *argument_sources):
            if isinstance(source, Mapping) and "project_id" in source:
                value = source.get("project_id")
                if not isinstance(value, str) or not value:
                    return False
                operation_claims.append(value)
                claims.append(value)
        for source in (job_metadata, job_metadata.get("execution"), job_metadata.get("arguments")):
            if isinstance(source, Mapping) and "project_id" in source:
                value = source.get("project_id")
                if not isinstance(value, str) or not value:
                    return False
                claims.append(value)
        # A job copy may corroborate an operation record, but cannot create
        # project ownership when the authoritative operation metadata omitted it.
        return bool(operation_claims) and all(value == project_id for value in claims)

    @staticmethod
    def _experiment_producer_arguments(operation_record: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any] | None]:
        """Resolve direct and registry-fallback producer metadata to one action."""
        metadata = operation_record.get("metadata")
        if not isinstance(metadata, Mapping):
            return None, None
        row_operation = operation_record.get("operation")
        recorded_operation = metadata.get("operation")
        if recorded_operation not in (None, row_operation):
            return None, None
        arguments = metadata.get("arguments")
        if not isinstance(arguments, Mapping):
            return None, None
        if row_operation in {"registry_call", "operation_call"}:
            nested_operation = arguments.get("operation_id")
            nested_arguments = arguments.get("arguments")
            if isinstance(nested_operation, str) and isinstance(nested_arguments, Mapping):
                return nested_operation, nested_arguments
            return None, None
        return row_operation if isinstance(row_operation, str) else None, arguments

    @staticmethod
    def _experiment_operation_status(operation_record: Mapping[str, Any] | None) -> str:
        if not isinstance(operation_record, Mapping):
            return "UNKNOWN"
        status = operation_record.get("status")
        job_status = operation_record.get("job_status")
        if not isinstance(status, str) or not isinstance(job_status, str) or status != job_status:
            return "UNKNOWN"
        if status in {"UNKNOWN", "RECONCILING", "RUNNING", "QUEUED"}:
            return "UNKNOWN" if status in {"UNKNOWN", "RECONCILING"} else status
        if status in TERMINAL:
            return status
        return "UNKNOWN"

    @staticmethod
    def _experiment_failure_summary(status: str, record: Mapping[str, Any] | None) -> dict[str, Any]:
        failure_states = {"FAILED", "UNKNOWN", "EXECUTION_STATE_UNKNOWN", "CANCELLED", "EXPIRED", "LOST"}
        if status not in failure_states:
            return {"state": "NOT_APPLICABLE", "code": None, "message": None}
        sources: list[Any] = []
        if isinstance(record, Mapping):
            sources.extend(record.get(key) for key in ("error", "failure_reason"))
            result = record.get("result")
            if isinstance(result, Mapping):
                sources.append(result.get("error"))
                job_metadata = record.get("job_metadata")
                if isinstance(job_metadata, Mapping):
                    sources.append(job_metadata.get("error"))
            sources.append(record.get("completion_status"))
        for value in sources:
            if isinstance(value, str) and value.strip():
                return {"state": "REPORTED", "code": None, "message": value.strip()}
            if isinstance(value, Mapping):
                code = value.get("code") if isinstance(value.get("code"), str) else None
                message = value.get("message") if isinstance(value.get("message"), str) else None
                if code or message:
                    return {"state": "REPORTED", "code": code, "message": message}
        return {"state": "NOT_RECORDED", "code": None, "message": None}

    @staticmethod
    def _experiment_cache_summary(case_data: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(case_data, Mapping):
            return {"state": "NOT_RECORDED", "hit": None, "policy": None}
        hit = case_data.get("cache_hit")
        if type(hit) is not bool:
            return {"state": "UNKNOWN", "hit": None, "policy": None}
        policy = case_data.get("cache_policy")
        return {
            "state": "HIT" if hit else "MISS",
            "hit": hit,
            "policy": policy if isinstance(policy, str) else None,
        }

    @staticmethod
    def _experiment_parameters_match(actual: Any, planned: Any) -> bool:
        """Match W21's flat finite-number grid values without Python bool coercion."""
        if not isinstance(actual, Mapping) or not isinstance(planned, Mapping) or not planned:
            return False
        if set(actual) != set(planned):
            return False
        for name, expected in planned.items():
            if not isinstance(name, str) or not name:
                return False
            observed = actual[name]
            if (isinstance(expected, bool) or not isinstance(expected, (int, float))
                    or isinstance(observed, bool) or not isinstance(observed, (int, float))):
                return False
            try:
                expected_number = float(expected)
                observed_number = float(observed)
            except (OverflowError, ValueError):
                return False
            if not math.isfinite(expected_number) or not math.isfinite(observed_number):
                return False
            if expected_number != observed_number:
                return False
        return True

    @staticmethod
    def _valid_experiment_case_data(
        value: Any,
        case_id: str,
        expected_ordinal: int,
        planned_parameters: Mapping[str, Any],
    ) -> bool:
        if (not isinstance(value, Mapping) or value.get("case_id") != case_id
                or not isinstance(value.get("status"), str) or not value["status"]):
            return False
        if ("case_ordinal" in value
                and (type(value["case_ordinal"]) is not int or value["case_ordinal"] != expected_ordinal)):
            return False
        if "parameters" not in value:
            # W21 may record a case as NOT_RUN after its budget is exhausted.
            # Such a marker has no execution parameters; all other result rows
            # must carry the exact frozen parameter point.
            return value.get("status") == "NOT_RUN"
        return ControlDaemon._experiment_parameters_match(value.get("parameters"), planned_parameters)

    @staticmethod
    def _normalized_experiment_case_data(value: Mapping[str, Any], expected_ordinal: int) -> dict[str, Any]:
        """Normalize only the allowed historical omission for read comparison."""
        normalized = dict(value)
        normalized["case_ordinal"] = expected_ordinal
        return normalized

    @staticmethod
    def _experiment_case_rows(
        record: Mapping[str, Any] | None,
        planned_cases: Mapping[str, tuple[int, Mapping[str, Any]]],
    ) -> dict[str, dict[str, Any]] | None:
        if not isinstance(record, Mapping):
            return None
        rows = record.get("cases")
        if not isinstance(rows, list):
            return None
        result: dict[str, dict[str, Any]] = {}
        seen_ordinals: set[int] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
                return None
            case_id = row["case_id"]
            plan = planned_cases.get(case_id)
            if plan is None:
                return None
            expected_ordinal, planned_parameters = plan
            if not ControlDaemon._valid_experiment_case_data(
                    row, case_id, expected_ordinal, planned_parameters):
                return None
            ordinal = row.get("case_ordinal")
            if ordinal is not None and ordinal in seen_ordinals:
                return None
            if ordinal is not None:
                seen_ordinals.add(ordinal)
            if case_id in result:
                return None
            result[case_id] = row
        return result

    @staticmethod
    def _metric_evaluation_sha256(record: Mapping[str, Any]) -> str:
        payload = {key: value for key, value in record.items() if key != "sha256"}
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _verify_experiment_observation_artifact(
        *,
        project_workspace: str,
        project_id: str,
        observation: Mapping[str, Any],
        reference: Mapping[str, Any],
        attempt_id: str,
        solution_identity_sha256: str | None = None,
    ) -> None:
        """Verify observation bytes under the authorized project workspace.

        OperationStore snapshots contain provenance metadata, not the content
        file itself.  A matching hash string in SQLite is therefore not enough:
        reopen the canonical per-project JSON path, reject symlinks, and hash
        the actual regular file through ArtifactStore's pinned reader.
        """
        def refuse() -> ExecutionContractError:
            return ExecutionContractError(
                "EXPERIMENT_STATE_UNKNOWN",
                "durable observation artifact failed project, type, content, or hash verification",
            )

        try:
            from ._artifact_store import ArtifactStore, MAX_CHUNK_BYTES, _read_pinned_chunk

            observation_id = observation.get("observation_id")
            record_sha = observation.get("sha256")
            artifact = observation.get("artifact")
            if (set(reference) != {"observation_id", "sha256"}
                    or not isinstance(observation_id, str)
                    or re.fullmatch(r"obs_[0-9a-f]{32}", observation_id) is None
                    or reference.get("observation_id") != observation_id
                    or not isinstance(record_sha, str)
                    or re.fullmatch(r"[0-9a-f]{64}", record_sha) is None
                    or reference.get("sha256") != record_sha
                    or not isinstance(artifact, Mapping)
                    or artifact.get("sha256") != record_sha
                    or artifact.get("format") != "json"
                    or type(artifact.get("byte_size")) is not int
                    or artifact.get("byte_size", 0) <= 0
                    or not isinstance(project_id, str) or not project_id
                    or not isinstance(attempt_id, str) or not attempt_id):
                raise refuse()

            summary = artifact.get("evaluation_summary")
            if (not isinstance(summary, Mapping)
                    or summary.get("dataset") != observation.get("dataset")
                    or summary.get("solution") != observation.get("solution")):
                raise refuse()

            store = ArtifactStore(Path(project_workspace))
            relative_path = f"observations/{observation_id}.json"
            expected_path = store.resolve_safe_path(relative_path, allow_overwrite=True)
            stored_path = artifact.get("file_path")
            if (not isinstance(stored_path, str) or not Path(stored_path).is_absolute()
                    or Path(stored_path) != expected_path):
                raise refuse()

            expected_size = artifact["byte_size"]
            chunk_length = min(MAX_CHUNK_BYTES, expected_size)
            chunk = _read_pinned_chunk(expected_path, offset=0, length=chunk_length)
            if (chunk.get("whole_file_sha256") != record_sha
                    or chunk.get("file_size") != expected_size
                    or len(chunk.get("data_bytes", b"")) != chunk_length):
                raise refuse()

            # Small observation payloads can also be semantically checked from
            # the exact bytes that were hashed.  For larger arrays the whole-file
            # digest and the hash-bound record already cover every serialized
            # byte without loading an unbounded duplicate into memory.
            if expected_size <= MAX_CHUNK_BYTES:
                payload = json.loads(chunk["data_bytes"])
                metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
                values = payload.get("values") if isinstance(payload, Mapping) else None
                if (not isinstance(payload, Mapping)
                        or not isinstance(payload.get("spec"), Mapping)
                        or not isinstance(metadata, Mapping)
                        or values is None
                        or metadata.get("dataset") != observation.get("dataset")
                        or metadata.get("solution") != observation.get("solution")
                        or metadata.get("case_attempt_id") != attempt_id
                        or metadata.get("case_solution_identity_sha256") != solution_identity_sha256
                        or summary.get("expressions") != metadata.get("expressions")
                        or summary.get("complex_mode") != metadata.get("complex_mode")
                        or summary.get("is_complex") != metadata.get("is_complex")):
                    raise refuse()
        except ExecutionContractError as exc:
            if exc.code == "EXPERIMENT_STATE_UNKNOWN":
                raise
            raise refuse() from exc
        except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise refuse() from exc

    @staticmethod
    def _experiment_payload_sha256(value: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(dict(value), sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _find_operation_result_payload(operation_record: Mapping[str, Any], run_id: str) -> Mapping[str, Any] | None:
        pending: list[Any] = [operation_record.get("result")]
        visited: set[int] = set()
        while pending:
            value = pending.pop()
            if not isinstance(value, Mapping) or id(value) in visited:
                continue
            visited.add(id(value))
            if value.get("run_id") == run_id and value.get("kind") == "w21experiment_run":
                return value
            for key in ("data", "result", "payload"):
                child = value.get(key)
                if isinstance(child, Mapping):
                    pending.append(child)
        return None

    def _verify_experiment_case_metric_association(
        self,
        *,
        snapshot: Mapping[str, Any],
        project_id: str,
        project_workspace: str,
        experiment_id: str,
        case_id: str,
        design: Mapping[str, Any],
        run: Mapping[str, Any],
        run_producer: Mapping[str, Any],
        case_record: Mapping[str, Any] | None,
        case_data: Mapping[str, Any],
        expected_ordinal: int,
        planned_parameters: Mapping[str, Any],
    ) -> None:
        """Cross-check a case evaluation through its attempt, producer, and W17 artifacts."""
        association = case_data.get("metric_evaluation")
        metric_refs = design.get("definition", {}).get("metric_evaluations", [])
        if not metric_refs:
            if association is not None:
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case contains an unplanned metric association")
            return
        if case_data.get("status") != "COMPLETED" or not isinstance(association, Mapping):
            if association is not None or case_data.get("status") == "COMPLETED":
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "completed metric case has no valid evaluation association")
            return
        if case_record is None:
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric case association has no per-case durable artifact")
        if not snapshot.get("metric_version_snapshot_valid"):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "frozen metric version record is unavailable or corrupt")
        metric_snapshots = design.get("metric_definition_snapshots")
        if (not isinstance(metric_snapshots, list)
                or metric_snapshots != snapshot.get("metric_definition_versions")):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "experiment metric snapshots differ from their immutable project versions")

        run_id = run.get("run_id")
        producer_id = run.get("producer")
        attempt_id = association.get("case_attempt_id")
        evaluation_id = association.get("evaluation_id")
        evaluation_hash = association.get("sha256")
        evaluation = snapshot.get("evaluations", {}).get(case_id)
        attempt = snapshot.get("attempts", {}).get(case_id)
        if (not isinstance(run_id, str) or not run_id
                or not isinstance(producer_id, str) or not producer_id
                or not isinstance(attempt_id, str) or not attempt_id.startswith("att_")
                or not isinstance(evaluation_id, str) or not evaluation_id.startswith("mev_")
                or not isinstance(evaluation_hash, str) or len(evaluation_hash) != 64
                or not isinstance(evaluation, Mapping)
                or not isinstance(attempt, Mapping)):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case metric association is missing its durable attempt or evaluation")

        attempt_hash = self._experiment_record_sha256(attempt)
        if (attempt.get("kind") != "w21experiment_case_attempt"
                or attempt.get("attempt_id") != attempt_id
                or attempt.get("experiment_id") != experiment_id
                or attempt.get("run_id") != run_id
                or attempt.get("case_id") != case_id
                or type(attempt.get("case_ordinal")) is not int
                or attempt.get("case_ordinal") != expected_ordinal
                or attempt.get("project_id") != project_id
                or attempt.get("session_id") != run.get("session_id")
                or attempt.get("model_ref") != run.get("model_ref")
                or attempt.get("model_revision") != run.get("model_revision")
                or attempt.get("producer") != producer_id
                or attempt.get("design_sha256") != design.get("sha256")
                or not self._experiment_parameters_match(attempt.get("parameters"), planned_parameters)
                or attempt.get("sha256") != attempt_hash
                or case_data.get("case_attempt_id") != attempt_id):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case attempt does not match the frozen case, run, or Worker binding")

        if (case_record.get("project_id") != project_id
                or case_record.get("session_id") != run.get("session_id")
                or case_record.get("model_revision") != run.get("model_revision")
                or case_record.get("model_ref") != run.get("model_ref")
                or case_record.get("producer") != producer_id):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case artifact does not match its project, session, revision, and producer")
        if (run.get("project_id") != project_id
                or run.get("session_id") != run.get("model_ref", {}).get("session_id")
                or type(run.get("model_revision")) is not int or run.get("model_revision") < 0):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "run artifact has no exact admitted session and revision binding")

        evaluation_binding = evaluation.get("case_binding")
        if not isinstance(evaluation_binding, Mapping):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric evaluation has no case binding")
        if (evaluation.get("kind") != "w17_metric_evaluation"
                or evaluation.get("evaluation_id") != evaluation_id
                or evaluation.get("project_id") != project_id
                or evaluation.get("created_model_ref") != run.get("model_ref")
                or evaluation.get("created_revision") != run.get("model_revision")
                or evaluation.get("producer") != producer_id
                or evaluation.get("evaluation_source") != "experiment.run_case_same_worker_callback"
                or evaluation.get("sha256") != evaluation_hash
                or evaluation.get("sha256") != self._metric_evaluation_sha256(evaluation)
                or evaluation.get("case_binding_sha256") != self._metric_evaluation_sha256(evaluation_binding)
                or evaluation_binding.get("attempt_id") != attempt_id
                or evaluation_binding.get("attempt_sha256") != attempt.get("sha256")
                or attempt.get("units") != evaluation_binding.get("units")
                or evaluation_binding.get("experiment_id") != experiment_id
                or evaluation_binding.get("run_id") != run_id
                or evaluation_binding.get("case_id") != case_id
                or type(evaluation_binding.get("case_ordinal")) is not int
                or evaluation_binding.get("case_ordinal") != expected_ordinal
                or evaluation_binding.get("project_id") != project_id
                or evaluation_binding.get("session_id") != run.get("session_id")
                or evaluation_binding.get("model_ref") != run.get("model_ref")
                or evaluation_binding.get("model_revision") != run.get("model_revision")
                or evaluation_binding.get("producer") != producer_id
                or evaluation_binding.get("design_sha256") != design.get("sha256")
                or not self._experiment_parameters_match(evaluation_binding.get("parameters"), planned_parameters)
                or not self._experiment_parameters_match(evaluation_binding.get("planned_parameters"), planned_parameters)
                or evaluation_binding.get("validation_status") != "PASS"):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric evaluation integrity or case binding is inconsistent")

        sample = case_data.get("sample")
        solve = case_data.get("solve")
        parameter_readback = case_data.get("parameter_readback")
        solution_indices = case_data.get("solution_indices")
        if (not isinstance(sample, Mapping) or not isinstance(solve, Mapping)
                or not isinstance(parameter_readback, Mapping)
                or not isinstance(solution_indices, Mapping)
                or sample.get("case_attempt_id") != attempt_id
                or solve.get("case_attempt_id") != attempt_id
                or parameter_readback.get("case_attempt_id") != attempt_id
                or parameter_readback.get("value_comparison") != "native_scalar_in_frozen_declared_unit_with_rel_tol_1e-12"
                or solution_indices != evaluation_binding.get("solution_indices")
                or solution_indices.get("binding_complete") is not True
                or solution_indices.get("solution") != evaluation_binding.get("solution")
                or solution_indices.get("time_values") != evaluation_binding.get("solution_identity", {}).get("times")
                or sample.get("dataset") != evaluation_binding.get("dataset")
                or sample.get("solution") != evaluation_binding.get("solution")
                or parameter_readback != evaluation_binding.get("parameter_readback")
                or case_data.get("units") != evaluation_binding.get("units")
                or case_data.get("solve") is None
                or evaluation_binding.get("solve_result_sha256") != self._experiment_payload_sha256(solve)
                or evaluation_binding.get("solve_status") != solve.get("status")
                or not isinstance(evaluation_binding.get("solution_identity"), Mapping)
                or evaluation_binding.get("solution_identity_sha256") != self._experiment_payload_sha256(
                    evaluation_binding.get("solution_identity")
                )):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric case evidence does not bind the recorded solve, parameters, and solution")

        readback_rows = parameter_readback.get("parameters")
        if not isinstance(readback_rows, list):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric case parameter readback is malformed")
        readback_by_name = {row.get("name"): row for row in readback_rows if isinstance(row, Mapping)}
        if (set(readback_by_name) != set(planned_parameters)
                or len(readback_by_name) != len(readback_rows)):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric case parameter readback names do not match the frozen grid")
        for name, expected in planned_parameters.items():
            row = readback_by_name[name]
            value = row.get("case_value_readback")
            observed = value.get("value") if isinstance(value, Mapping) else None
            expected_unit = attempt["units"].get(name)
            observed_unit = value.get("unit") if isinstance(value, Mapping) else None
            tolerance = value.get("relative_tolerance") if isinstance(value, Mapping) else None
            expression = repr(float(expected)) + "[" + expected_unit + "]" if isinstance(expected_unit, str) else None
            if (row.get("case_attempt_id") != attempt_id
                    or row.get("expression") != expression
                    or not isinstance(row.get("evaluated"), Mapping)
                    or isinstance(observed, bool) or not isinstance(observed, (int, float))
                    or not math.isfinite(float(observed))
                    or isinstance(expected, bool) or not isinstance(expected, (int, float))
                    or not math.isfinite(float(expected))
                    or isinstance(tolerance, bool) or tolerance != 1e-12
                    or observed_unit != ("1" if expected_unit in {"1", "dimensionless"} else expected_unit)
                    or not math.isclose(float(observed), float(expected), rel_tol=1e-12, abs_tol=0.0)
                    or not isinstance(value.get("source"), str) or not value.get("source")):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "native case parameter value differs from the frozen parameter attempt")

        source_ref = evaluation_binding.get("sample_observation_ref")
        source_observation = (
            snapshot.get("observations", {}).get(source_ref.get("observation_id"))
            if isinstance(source_ref, Mapping) else None
        )
        case_source_ref = case_data.get("observation_ref")
        if (not isinstance(source_ref, Mapping) or source_ref != case_source_ref
                or not isinstance(source_observation, Mapping)
                or source_observation.get("kind") != "w17_observation"
                or source_observation.get("project_id") != project_id
                or source_observation.get("producer") != producer_id
                or source_observation.get("model_ref") != run.get("model_ref")
                or source_observation.get("dataset") != evaluation_binding.get("dataset")
                or source_observation.get("solution") != evaluation_binding.get("solution")
                or source_observation.get("source_identity") != evaluation_binding.get("solution_identity")
                or source_observation.get("sha256") != source_ref.get("sha256")):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case source observation does not match its native solution identity")
        self._verify_experiment_observation_artifact(
            project_workspace=project_workspace,
            project_id=project_id,
            observation=source_observation,
            reference=source_ref,
            attempt_id=attempt_id,
        )

        metric_by_id = {row.get("metric_id"): row for row in metric_snapshots if isinstance(row, Mapping)}
        items = evaluation.get("items")
        if (not isinstance(items, list) or len(items) != len(metric_refs)
                or len({item.get("metric_id") for item in items if isinstance(item, Mapping)}) != len(items)):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric evaluation item set differs from the frozen experiment definition")
        observation_refs = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric evaluation item is malformed")
            definition_record = metric_by_id.get(item.get("metric_id"))
            observation_ref = item.get("observation_ref")
            observation = (
                snapshot.get("observations", {}).get(observation_ref.get("observation_id"))
                if isinstance(observation_ref, Mapping) else None
            )
            if (not isinstance(definition_record, Mapping)
                    or item.get("definition_version") != definition_record.get("version")
                    or item.get("definition_sha256") != definition_record.get("definition_sha256")
                    or item.get("definition") != definition_record.get("definition")
                    or item.get("case_attempt_id") != attempt_id
                    or item.get("case_attempt_sha256") != attempt.get("sha256")
                    or item.get("case_solution_identity_sha256") != evaluation_binding.get("solution_identity_sha256")
                    or item.get("dataset") != evaluation_binding.get("dataset")
                    or item.get("solution") != evaluation_binding.get("solution")
                    or not isinstance(observation, Mapping)
                    or observation.get("kind") != "w17_observation"
                    or observation.get("project_id") != project_id
                    or observation.get("producer") != producer_id
                    or observation.get("model_ref") != run.get("model_ref")
                    or observation.get("dataset") != evaluation_binding.get("dataset")
                    or observation.get("solution") != evaluation_binding.get("solution")
                    or observation.get("source_identity") != evaluation_binding.get("solution_identity")
                    or not isinstance(observation_ref.get("sha256"), str)
                    or observation.get("sha256") != observation_ref.get("sha256")):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric item or observation artifact is not bound to the frozen metric and case solution")
            self._verify_experiment_observation_artifact(
                project_workspace=project_workspace,
                project_id=project_id,
                observation=observation,
                reference=observation_ref,
                attempt_id=attempt_id,
                solution_identity_sha256=evaluation_binding.get("solution_identity_sha256"),
            )
            observation_refs.append({"metric_id": item["metric_id"], "observation_id": observation_ref["observation_id"],
                                     "sha256": observation_ref["sha256"]})

        expected_refs = [
            {key: row[key] for key in ("metric_id", "version", "definition_sha256")}
            for row in metric_snapshots
        ]
        if (association.get("evaluation_id") != evaluation_id
                or association.get("case_binding_sha256") != evaluation.get("case_binding_sha256")
                or association.get("case_attempt_id") != attempt_id
                or association.get("metric_definition_refs") != expected_refs
                or association.get("observation_artifact_refs") != observation_refs):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case-to-metric artifact reference differs from its immutable evaluation")

        run_result = self._find_operation_result_payload(run_producer, run_id)
        if (run_result is None
                or run_result.get("sha256") != run.get("sha256")
                or self._experiment_record_sha256(run_result) != run.get("sha256")):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "metric association is not present in the recorded run producer result")
        run_case_rows = run_result.get("cases")
        matching_rows = [row for row in run_case_rows if isinstance(row, Mapping) and row.get("case_id") == case_id] if isinstance(run_case_rows, list) else []
        if len(matching_rows) != 1 or matching_rows[0] != case_data:
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case artifact differs from the run producer's persisted case association")

    def _verify_experiment_case_model_artifact(
        self,
        *,
        snapshot: Mapping[str, Any],
        project_id: str,
        project_workspace: str,
        experiment_id: str,
        case_id: str,
        design: Mapping[str, Any],
        run: Mapping[str, Any] | None,
        run_producer: Mapping[str, Any] | None,
        case_record: Mapping[str, Any] | None,
        case_data: Mapping[str, Any],
        expected_ordinal: int,
        planned_parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Verify saved case MPH bytes and exact attempt binding on public reads."""
        artifact = case_data.get("case_model_artifact")
        if artifact is None:
            return {"status": "NOT_AVAILABLE", "reason_code": "NO_SAME_WORKER_MODEL_COPY",
                    "restore_status": "NOT_AVAILABLE"}

        def refuse() -> ExecutionContractError:
            return ExecutionContractError(
                "EXPERIMENT_STATE_UNKNOWN",
                "durable case model artifact failed project, attempt, solution, type, or byte hash verification",
            )

        try:
            from ._artifact_store import ArtifactStore, MAX_CHUNK_BYTES, _read_pinned_chunk

            if not isinstance(artifact, Mapping) or not isinstance(run, Mapping):
                raise refuse()
            attempt = snapshot.get("attempts", {}).get(case_id)
            if not isinstance(attempt, Mapping):
                raise refuse()
            attempt_id = attempt.get("attempt_id")
            attempt_sha = self._experiment_record_sha256(attempt)
            if (attempt.get("kind") != "w21experiment_case_attempt"
                    or not isinstance(attempt_id, str) or re.fullmatch(r"att_[0-9a-f]{32}", attempt_id) is None
                    or attempt.get("experiment_id") != experiment_id
                    or attempt.get("run_id") != run.get("run_id")
                    or attempt.get("case_id") != case_id
                    or type(attempt.get("case_ordinal")) is not int
                    or attempt.get("case_ordinal") != expected_ordinal
                    or attempt.get("project_id") != project_id
                    or attempt.get("session_id") != run.get("session_id")
                    or attempt.get("model_ref") != run.get("model_ref")
                    or attempt.get("model_revision") != run.get("model_revision")
                    or attempt.get("producer") != run.get("producer")
                    or attempt.get("design_sha256") != design.get("sha256")
                    or not self._experiment_parameters_match(attempt.get("parameters"), planned_parameters)
                    or attempt.get("sha256") != attempt_sha
                    or case_data.get("case_attempt_id") != attempt_id):
                raise refuse()

            expected_metric_refs = [
                {key: row[key] for key in ("metric_id", "version", "definition_sha256")}
                for row in design.get("metric_definition_snapshots", [])
                if isinstance(row, Mapping)
            ]
            if (attempt.get("metric_definition_refs") != expected_metric_refs
                    or (case_record is not None and (
                        case_record.get("project_id") != project_id
                        or case_record.get("session_id") != run.get("session_id")
                        or case_record.get("model_ref") != run.get("model_ref")
                        or case_record.get("model_revision") != run.get("model_revision")
                        or case_record.get("producer") != run.get("producer")))):
                raise refuse()

            producer_status = self._experiment_operation_status(run_producer)
            if producer_status == "SUCCEEDED":
                run_result = self._find_operation_result_payload(run_producer, run.get("run_id"))
                rows = run_result.get("cases") if isinstance(run_result, Mapping) else None
                matching = ([row for row in rows if isinstance(row, Mapping) and row.get("case_id") == case_id]
                            if isinstance(rows, list) else [])
                if (not isinstance(run_result, Mapping)
                        or run_result.get("sha256") != run.get("sha256")
                        or self._experiment_record_sha256(run_result) != run.get("sha256")
                        or len(matching) != 1 or dict(matching[0]) != dict(case_data)):
                    raise refuse()
            elif run.get("status") != "RUNNING":
                raise refuse()

            common = {
                "schema_version": 1,
                "kind": "w21_experiment_case_model_artifact",
                "project_id": project_id,
                "experiment_id": experiment_id,
                "run_id": run.get("run_id"),
                "case_id": case_id,
                "case_ordinal": expected_ordinal,
                "attempt_id": attempt_id,
                "attempt_sha256": attempt_sha,
                "producer": run.get("producer"),
                "session_id": run.get("session_id"),
                "model_ref": run.get("model_ref"),
                "model_revision": run.get("model_revision"),
                "design_sha256": design.get("sha256"),
                "study": design.get("study"),
                "parameters": planned_parameters,
                "units": attempt.get("units"),
                "revision_scope": "run_admission_revision; exact case solution is separately bound below",
                "format": "mph",
                "save_copy": True,
                "save_api": "RemoteModel.save(path, saveCopy=True)",
                "verification": "ATOMIC_MPH_ZIP_CRC_AND_SHA256",
            }
            if any(artifact.get(key) != value for key, value in common.items()):
                raise refuse()

            if artifact.get("status") == "SAVE_FAILED":
                reason_code = artifact.get("reason_code")
                stage = artifact.get("stage")
                if (not isinstance(reason_code, str) or not reason_code
                        or artifact.get("restore_status") != "NOT_AVAILABLE"
                        or stage is not None and not isinstance(stage, str)
                        or "relative_path" in artifact or "file_sha256" in artifact
                        or "file_size" in artifact):
                    raise refuse()
                return {"status": "SAVE_FAILED", "reason_code": reason_code,
                        "stage": stage, "restore_status": "NOT_AVAILABLE"}

            if artifact.get("status") != "SAVED_HASH_VERIFIED_RELOAD_UNVERIFIED":
                raise refuse()
            if artifact.get("restore_status") != "NOT_RELOAD_VERIFIED":
                raise refuse()
            sample = case_data.get("sample")
            solve = case_data.get("solve")
            parameter_readback = case_data.get("parameter_readback")
            solution_indices = case_data.get("solution_indices")
            identity = artifact.get("solution_identity")
            identity_sha = artifact.get("solution_identity_sha256")
            if (not isinstance(sample, Mapping) or not isinstance(solve, Mapping)
                    or not isinstance(parameter_readback, Mapping)
                    or not isinstance(solution_indices, Mapping)
                    or not isinstance(identity, Mapping)
                    or not isinstance(identity_sha, str)
                    or re.fullmatch(r"[0-9a-f]{64}", identity_sha) is None
                    or identity_sha != self._experiment_payload_sha256(identity)
                    or identity != case_data.get("case_solution_identity")
                    or identity_sha != case_data.get("case_solution_identity_sha256")
                    or identity.get("solution") != sample.get("solution")
                    or identity.get("study") != attempt.get("study")
                    or sample.get("case_attempt_id") != attempt_id
                    or sample.get("dataset") != artifact.get("dataset")
                    or sample.get("solution") != artifact.get("solution")
                    or artifact.get("solution_indices") != solution_indices
                    or solution_indices.get("binding_complete") is not True
                    or solution_indices.get("solution") != sample.get("solution")
                    or solve.get("case_attempt_id") != attempt_id
                    or parameter_readback.get("case_attempt_id") != attempt_id
                    or artifact.get("parameter_readback_sha256") != self._experiment_payload_sha256(parameter_readback)
                    or artifact.get("parameters") != attempt.get("parameters")
                    or artifact.get("units") != attempt.get("units")):
                raise refuse()

            source_ref = artifact.get("sample_observation_ref")
            case_ref = case_data.get("observation_ref")
            observation = (
                snapshot.get("observations", {}).get(source_ref.get("observation_id"))
                if isinstance(source_ref, Mapping) else None
            )
            if (not isinstance(source_ref, Mapping) or source_ref != case_ref
                    or not isinstance(observation, Mapping)
                    or observation.get("kind") != "w17_observation"
                    or observation.get("project_id") != project_id
                    or observation.get("producer") != run.get("producer")
                    or observation.get("model_ref") != run.get("model_ref")
                    or observation.get("dataset") != sample.get("dataset")
                    or observation.get("solution") != sample.get("solution")
                    or observation.get("source_identity") != identity):
                raise refuse()
            self._verify_experiment_observation_artifact(
                project_workspace=project_workspace,
                project_id=project_id,
                observation=observation,
                reference=source_ref,
                attempt_id=attempt_id,
                solution_identity_sha256=None,
            )

            run_id = run.get("run_id")
            relative_path = f"w21/experiment_cases/{experiment_id}/{run_id}/{case_id}/{attempt_id}.mph"
            file_sha = artifact.get("file_sha256")
            file_size = artifact.get("file_size")
            if (artifact.get("relative_path") != relative_path
                    or not isinstance(file_sha, str)
                    or re.fullmatch(r"[0-9a-f]{64}", file_sha) is None
                    or type(file_size) is not int or file_size <= 0):
                raise refuse()
            store = ArtifactStore(Path(project_workspace))
            expected_path = store.resolve_safe_path(relative_path, allow_overwrite=True)
            pinned = _read_pinned_chunk(expected_path, offset=0,
                                        length=min(MAX_CHUNK_BYTES, file_size))
            if (pinned.get("whole_file_sha256") != file_sha
                    or pinned.get("file_size") != file_size
                    or len(pinned.get("data_bytes", b"")) != min(MAX_CHUNK_BYTES, file_size)
                    or not pinned.get("data_bytes", b"").startswith(b"PK\x03\x04")):
                raise refuse()
            return {
                "status": "SAVED_HASH_VERIFIED_RELOAD_UNVERIFIED",
                "relative_path": relative_path,
                "file_sha256": file_sha,
                "file_size": file_size,
                "format": "mph",
                "restore_status": "NOT_RELOAD_VERIFIED",
                "solution_identity_sha256": identity_sha,
            }
        except ExecutionContractError as exc:
            if exc.code == "EXPERIMENT_STATE_UNKNOWN":
                raise
            raise refuse() from exc
        except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise refuse() from exc

    @staticmethod
    def _verified_experiment_metric_value(
        evaluation: Mapping[str, Any] | None,
        metric_record: Mapping[str, Any],
        metric_ref: Mapping[str, Any],
        tuple_ref: Mapping[str, Any],
        requested_unit: str,
    ) -> dict[str, Any] | None:
        """Resolve one exact real projected metric tuple from its durable evaluation."""
        if not isinstance(evaluation, Mapping):
            return None
        definition = metric_record.get("definition")
        expected_ref = {key: metric_record.get(key) for key in ("metric_id", "version", "definition_sha256")}
        if (metric_ref != expected_ref
                or not isinstance(definition, Mapping)
                or definition.get("complex_mode") not in {"real", "imag", "abs"}
                or requested_unit != definition.get("expected_unit")):
            return None
        items = evaluation.get("items")
        if not isinstance(items, list):
            return None
        matching_items = [item for item in items if isinstance(item, Mapping)
                          and item.get("metric_id") == metric_ref.get("metric_id")]
        if len(matching_items) != 1:
            return None
        item = matching_items[0]
        if (item.get("definition_version") != metric_ref.get("version")
                or item.get("definition_sha256") != metric_ref.get("definition_sha256")
                or item.get("definition") != definition
                or item.get("complex_mode") != definition.get("complex_mode")
                or item.get("unit") != requested_unit):
            return None
        values = item.get("values")
        if not isinstance(values, list):
            return None
        selected = [row for row in values if isinstance(row, Mapping)
                    and type(row.get("outer")) is int and row.get("outer") == tuple_ref.get("outer")
                    and type(row.get("inner")) is int and row.get("inner") == tuple_ref.get("inner")]
        if len(selected) != 1:
            return None
        row = selected[0]
        value = row.get("value")
        solnum = row.get("solnum")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or type(solnum) is not int or solnum < 1:
            return None
        try:
            projected_value = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(projected_value):
            return None
        return {
            "metric_id": metric_ref["metric_id"],
            "version": metric_ref["version"],
            "definition_sha256": metric_ref["definition_sha256"],
            "evaluation_id": evaluation.get("evaluation_id"),
            "evaluation_sha256": evaluation.get("sha256"),
            "tuple": {"outer": row["outer"], "inner": row["inner"], "solnum": solnum},
            "value": projected_value,
            "unit": requested_unit,
        }

    @classmethod
    def _experiment_best_feasible_summary(
        cls,
        *,
        design: Mapping[str, Any],
        planned_case_ids: list[str],
        case_rows: list[Mapping[str, Any]],
        evaluations: Mapping[str, Any],
    ) -> dict[str, Any]:
        definition = design.get("definition")
        if not isinstance(definition, Mapping) or ("objective" not in definition and "constraints" not in definition):
            return {"status": "NOT_DEFINED", "reason_code": "NO_FROZEN_OBJECTIVE_AND_CONSTRAINTS",
                    "case_id": None}
        objective = definition.get("objective")
        constraints = definition.get("constraints")
        references = definition.get("metric_evaluations")
        metric_records = design.get("metric_definition_snapshots")
        if (not isinstance(objective, Mapping) or not isinstance(constraints, list)
                or not isinstance(references, list) or not isinstance(metric_records, list)):
            return {"status": "UNVERIFIED", "reason_code": "FROZEN_OBJECTIVE_CONTRACT_INVALID",
                    "case_id": None}

        def valid_metric_ref(value):
            return (isinstance(value, Mapping)
                    and set(value) == {"metric_id", "version", "definition_sha256"}
                    and isinstance(value.get("metric_id"), str) and bool(value.get("metric_id"))
                    and type(value.get("version")) is int and value["version"] >= 1
                    and isinstance(value.get("definition_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", value["definition_sha256"]) is not None)

        def valid_tuple(value):
            return (isinstance(value, Mapping) and set(value) == {"outer", "inner"}
                    and type(value.get("outer")) is int and value["outer"] >= 1
                    and type(value.get("inner")) is int and value["inner"] >= 1)

        if (set(objective) != {"metric_ref", "tuple", "direction", "unit"}
                or not valid_metric_ref(objective.get("metric_ref"))
                or not valid_tuple(objective.get("tuple"))
                or objective.get("direction") not in {"minimize", "maximize"}
                or not isinstance(objective.get("unit"), str) or not objective["unit"]):
            return {"status": "UNVERIFIED", "reason_code": "FROZEN_OBJECTIVE_CONTRACT_INVALID",
                    "case_id": None}
        seen_constraint_ids: set[str] = set()
        for constraint in constraints:
            if (not isinstance(constraint, Mapping)
                    or set(constraint) != {"constraint_id", "metric_ref", "tuple", "relation", "value", "unit"}
                    or not isinstance(constraint.get("constraint_id"), str)
                    or not constraint["constraint_id"] or constraint["constraint_id"] in seen_constraint_ids
                    or not valid_metric_ref(constraint.get("metric_ref"))
                    or not valid_tuple(constraint.get("tuple"))
                    or constraint.get("relation") not in {"<", "<=", ">", ">="}
                    or isinstance(constraint.get("value"), bool)
                    or not isinstance(constraint.get("value"), (int, float))
                    or not isinstance(constraint.get("unit"), str) or not constraint["unit"]):
                return {"status": "UNVERIFIED", "reason_code": "FROZEN_OBJECTIVE_CONTRACT_INVALID",
                        "case_id": None}
            try:
                if not math.isfinite(float(constraint["value"])):
                    return {"status": "UNVERIFIED", "reason_code": "FROZEN_OBJECTIVE_CONTRACT_INVALID",
                            "case_id": None}
            except (OverflowError, TypeError, ValueError):
                return {"status": "UNVERIFIED", "reason_code": "FROZEN_OBJECTIVE_CONTRACT_INVALID",
                        "case_id": None}
            seen_constraint_ids.add(constraint["constraint_id"])
        if (not references or any(not valid_metric_ref(row) for row in references)
                or any(not isinstance(row, Mapping) for row in metric_records)):
            return {"status": "UNVERIFIED", "reason_code": "FROZEN_METRIC_REFERENCE_MISMATCH",
                    "case_id": None}

        reference_keys = {(row["metric_id"], row["version"], row["definition_sha256"])
                          for row in references}
        records_by_ref = {(row.get("metric_id"), row.get("version"), row.get("definition_sha256")): row
                          for row in metric_records}
        if len(reference_keys) != len(references) or set(records_by_ref) != reference_keys:
            return {"status": "UNVERIFIED", "reason_code": "FROZEN_METRIC_REFERENCE_MISMATCH",
                    "case_id": None}

        def resolve_metric_value(evaluation, metric_ref, tuple_ref, unit):
            if not isinstance(metric_ref, Mapping) or not isinstance(tuple_ref, Mapping):
                return None
            key = (metric_ref.get("metric_id"), metric_ref.get("version"), metric_ref.get("definition_sha256"))
            if key not in reference_keys:
                return None
            return cls._verified_experiment_metric_value(
                evaluation, records_by_ref[key], metric_ref, tuple_ref, unit,
            )

        by_case = {row.get("case_id"): row for row in case_rows if isinstance(row, Mapping)}
        candidates: list[dict[str, Any]] = []
        infeasible_count = 0
        unresolved_count = 0
        not_run_count = 0
        failed_count = 0
        verified_completed_count = 0
        terminal_statuses = {"COMPLETED", "FAILED", "UNKNOWN", "EXECUTION_STATE_UNKNOWN",
                             "CANCELLED", "EXPIRED", "LOST"}
        all_terminal = len(case_rows) == len(planned_case_ids)
        for case_id in planned_case_ids:
            case = by_case.get(case_id)
            if not isinstance(case, Mapping):
                unresolved_count += 1
                not_run_count += 1
                all_terminal = False
                continue
            status = case.get("status")
            if status not in terminal_statuses:
                all_terminal = False
            if status == "FAILED":
                failed_count += 1
            if status in {"NOT_RUN", "NOT_RECORDED"}:
                not_run_count += 1
            if status != "COMPLETED" or not isinstance(case.get("metric_evaluation"), Mapping):
                unresolved_count += 1
                continue
            association = case["metric_evaluation"]
            evaluation_id = association.get("evaluation_id")
            evaluation = evaluations.get(case_id)
            if (not isinstance(evaluation, Mapping)
                    or evaluation.get("evaluation_id") != evaluation_id
                    or evaluation.get("sha256") != association.get("sha256")):
                unresolved_count += 1
                continue
            verified_completed_count += 1
            constraint_results = []
            constraint_unknown = False
            any_failed = False
            for constraint in constraints:
                if not isinstance(constraint, Mapping):
                    constraint_unknown = True
                    break
                evidence = resolve_metric_value(
                    evaluation, constraint.get("metric_ref"), constraint.get("tuple"), constraint.get("unit"),
                )
                if evidence is None:
                    constraint_unknown = True
                    constraint_results.append({"constraint_id": constraint.get("constraint_id"),
                                               "status": "UNVERIFIED"})
                    continue
                relation, bound = constraint.get("relation"), constraint.get("value")
                if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(float(bound)):
                    constraint_unknown = True
                    continue
                passed = {"<": evidence["value"] < float(bound), "<=": evidence["value"] <= float(bound),
                          ">": evidence["value"] > float(bound), ">=": evidence["value"] >= float(bound)}.get(relation)
                if not isinstance(passed, bool):
                    constraint_unknown = True
                    continue
                any_failed = any_failed or not passed
                constraint_results.append({
                    "constraint_id": constraint.get("constraint_id"),
                    "status": "PASS" if passed else "FAIL",
                    "relation": relation,
                    "bound": float(bound),
                    "unit": constraint["unit"],
                    "metric_value": evidence,
                })
            if constraint_unknown:
                unresolved_count += 1
                continue
            if any_failed:
                infeasible_count += 1
                continue
            objective_evidence = resolve_metric_value(
                evaluation, objective.get("metric_ref"), objective.get("tuple"), objective.get("unit"),
            )
            if objective_evidence is None:
                unresolved_count += 1
                continue
            candidates.append({
                "case_id": case_id,
                "case_ordinal": case.get("case_ordinal"),
                "objective": objective_evidence,
                "constraints": constraint_results,
            })

        total = len(planned_case_ids)
        ranking_complete = unresolved_count == 0 and len(candidates) + infeasible_count == total
        comparison_counts = {
            "total_cases": total,
            "verified_completed_cases": verified_completed_count,
            "feasible_cases": len(candidates),
            "infeasible_cases": infeasible_count,
            "unresolved_cases": unresolved_count,
            "not_run_cases": not_run_count,
            "failed_cases": failed_count,
        }
        if candidates:
            reverse = objective.get("direction") == "maximize"
            candidates.sort(key=lambda row: ((-row["objective"]["value"] if reverse
                                               else row["objective"]["value"]),
                                              row["case_ordinal"]))
            best = candidates[0]
            return {
                "status": "FOUND",
                "reason_code": "BEST_VERIFIED_FEASIBLE_CASE",
                "case_id": best["case_id"],
                "objective": best["objective"],
                "constraints": best["constraints"],
                "comparison_scope": "verified_completed_cases",
                "ranking_complete": ranking_complete,
                "tie_break": "case_ordinal_ascending",
                "counts": comparison_counts,
            }
        if (all_terminal and total > 0 and infeasible_count == total
                and unresolved_count == 0 and not candidates):
            return {
                "status": "NONE_FEASIBLE",
                "reason_code": "ALL_TERMINAL_CASES_HAVE_VERIFIED_CONSTRAINT_FAILURES",
                "case_id": None,
                "comparison_scope": "all_planned_terminal_cases",
                "ranking_complete": True,
                "counts": comparison_counts,
            }
        return {
            "status": "UNKNOWN",
            "reason_code": "NO_VERIFIED_FEASIBLE_CASE_WITH_UNRESOLVED_CASES",
            "case_id": None,
            "comparison_scope": "verified_completed_cases",
            "ranking_complete": False,
            "counts": comparison_counts,
        }

    @staticmethod
    def _planned_experiment_cases(
        design_cases: Any,
        planned_case_ids: Any,
        definition: Mapping[str, Any],
    ) -> dict[str, tuple[int, Mapping[str, Any]]] | None:
        """Validate the durable case index against the frozen Cartesian grid."""
        if (not isinstance(planned_case_ids, list) or len(planned_case_ids) > 500
                or any(not isinstance(value, str) or not value for value in planned_case_ids)
                or len(set(planned_case_ids)) != len(planned_case_ids)
                or not isinstance(design_cases, list) or len(design_cases) > 500
                or len(design_cases) != len(planned_case_ids)
                or not isinstance(definition, Mapping)
                or definition.get("sampling") != {"kind": "cartesian_grid"}):
            return None
        parameters = definition.get("parameters")
        if not isinstance(parameters, Mapping) or not parameters:
            return None
        names = sorted(parameters)
        values_by_name: list[list[float]] = []
        for name in names:
            values = parameters[name]
            if (not isinstance(name, str) or not name or not isinstance(values, list) or not values
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           for value in values)):
                return None
            try:
                normalized_values = [float(value) for value in values]
            except (OverflowError, ValueError):
                return None
            if any(not math.isfinite(value) for value in normalized_values):
                return None
            if len(set(normalized_values)) != len(normalized_values):
                return None
            values_by_name.append(normalized_values)
        if math.prod(len(values) for values in values_by_name) > 500:
            return None
        remaining_grid = set(itertools.product(*values_by_name))
        if len(remaining_grid) != len(planned_case_ids):
            return None

        planned: dict[str, tuple[int, Mapping[str, Any]]] = {}
        for ordinal, (row, case_id) in enumerate(zip(design_cases, planned_case_ids), 1):
            row_parameters = row.get("parameters") if isinstance(row, Mapping) else None
            if (not isinstance(row, Mapping) or row.get("case_id") != case_id
                    or ("case_ordinal" in row
                        and (type(row["case_ordinal"]) is not int or row["case_ordinal"] != ordinal))
                    or not isinstance(row_parameters, Mapping) or set(row_parameters) != set(names)):
                return None
            normalized_parameters: dict[str, float] = {}
            for name in names:
                value = row_parameters[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                try:
                    normalized_value = float(value)
                except (OverflowError, ValueError):
                    return None
                if not math.isfinite(normalized_value):
                    return None
                normalized_parameters[name] = normalized_value
            grid_key = tuple(normalized_parameters[name] for name in names)
            if grid_key not in remaining_grid:
                return None
            remaining_grid.remove(grid_key)
            planned[case_id] = (ordinal, normalized_parameters)
        if remaining_grid:
            return None
        return planned

    def _dispatch_experiment_stage_define(
        self, arguments: dict[str, Any], execution: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one immutable W21 stage plan without calling a Worker.

        Admission uses the existing per-endpoint scheduler so declaration
        revision checks cannot race an earlier accepted model write. The
        callback reads only the in-memory managed ledger and the SQLite
        revision binding; it does not invoke model snapshots or Java methods.
        """
        from ._execution_contract import canonical_request_hash, model_ref_from_mapping
        from ._stage_contract import sha256_json, validate_stage_plan_definition
        from ._operation_store import StagePlanStoreConflict
        from ._g2_registry import validate_call

        identity_fields = (
            "project_id", "session_id", "model_ref", "expected_revision",
            "idempotency_key", "request_id",
        )
        scoped_arguments = dict(arguments)
        normalized_execution = dict(execution)
        for field in identity_fields:
            body_value = scoped_arguments.get(field)
            outer_value = normalized_execution.get(field)
            if body_value is not None and outer_value is not None and body_value != outer_value:
                code = "IDEMPOTENCY_CONFLICT" if field == "idempotency_key" else "MODEL_IDENTITY_MISMATCH"
                raise ExecutionContractError(code, f"experiment.stage_define {field} differs between arguments and execution envelope")
            value = outer_value if outer_value is not None else body_value
            if value is not None:
                scoped_arguments[field] = value
                normalized_execution[field] = value

        entry = validate_call("experiment.stage_define", scoped_arguments)
        if entry.effect.upper() != "STATE_WRITE" or entry.scope != "model":
            raise ExecutionContractError("UNSUPPORTED_OPERATION", "stage plan route has an unexpected catalog effect or scope")
        project_id = scoped_arguments.get("project_id")
        session_id = scoped_arguments.get("session_id")
        model_ref_value = scoped_arguments.get("model_ref")
        expected_revision = scoped_arguments.get("expected_revision")
        idempotency_key = scoped_arguments.get("idempotency_key")
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", "experiment.stage_define requires project_id")
        if not isinstance(session_id, str) or not session_id:
            raise ExecutionContractError("MODEL_IDENTITY_REQUIRED", "experiment.stage_define requires session_id")
        if not isinstance(model_ref_value, Mapping):
            raise ExecutionContractError("MODEL_IDENTITY_REQUIRED", "experiment.stage_define requires a production ModelRef object")
        model_ref = model_ref_from_mapping(model_ref_value)
        if model_ref.session_id != session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "ModelRef session_id differs from the execution session")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ExecutionContractError("REVISION_CONFLICT", "experiment.stage_define requires a non-negative expected_revision")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ExecutionContractError("INVALID_REQUEST", "experiment.stage_define requires a non-empty idempotency_key")

        definition = validate_stage_plan_definition(scoped_arguments.get("definition"))
        request_id = scoped_arguments.get("request_id") or normalized_execution.get("request_id") or str(uuid4())
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "request_id must be a non-empty string")
        normalized_execution.update({
            "project_id": project_id,
            "session_id": session_id,
            "model_ref": model_ref.as_dict(),
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
        })
        timeouts = self._timeouts(normalized_execution)
        operation_arguments = {"definition": definition}

        # Enforce project/model attribution and project_write policy before
        # looking up a plan key, so foreign and missing plan IDs are not an
        # enumeration channel.
        self._authorize_project_execution("experiment.stage_define", operation_arguments, normalized_execution)
        timeouts = self.project_authority.apply_timeout_caps(project_id, timeouts)
        digest = canonical_request_hash(
            "experiment.stage_define", operation_arguments, model_ref.as_dict(), expected_revision,
            project_id=project_id, session_id=session_id,
            queue_timeout_s=timeouts["queue_timeout_s"],
            execution_timeout_s=timeouts["execution_timeout_s"],
            no_progress_warning_s=timeouts["no_progress_warning_s"],
        )
        persisted_execution = {
            key: value for key, value in normalized_execution.items()
            if key not in {"rpc_timeout_s", "queue_timeout_s", "execution_timeout_s", "no_progress_warning_s"}
        }
        record_metadata = {
            "operation": "experiment.stage_define",
            "arguments": operation_arguments,
            "execution": persisted_execution,
            "effect": "STATE_WRITE",
            "engine_dispatched": False,
        }
        with self.lock:
            record, reused = self.store.begin(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=digest,
                operation="experiment.stage_define",
                metadata=record_metadata,
                timeouts=timeouts,
            )
            if reused and record.get("result") is not None:
                return record["result"]

        try:
            context = self._execution_session_context(normalized_execution)
        except ExecutionContractError as exc:
            result = self._exception(exc)
            return self._finish(record, result, "FAILED")

        def register_definition() -> dict[str, Any]:
            try:
                # Recheck policy and revision after scheduler admission, when
                # preceding accepted model writes have completed.
                self._authorize_project_execution("experiment.stage_define", operation_arguments, normalized_execution)
                backend = context.backend if context is not None else self._default_backend
                service = context.service if context is not None else getattr(backend, "service", None)
                if service is None or getattr(service, "ledger", None) is None:
                    raise ExecutionContractError("SESSION_NOT_CONNECTED", "stage definition requires a current managed model ledger")
                if context is not None and (context.project_id != project_id or context.session_id != session_id):
                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "stage definition session context differs from its project binding")
                if service.ledger.session_id != session_id:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "ModelRef session does not match the selected managed ledger")
                state = service.ledger._state_for(model_ref)
                if state.active_operation_id is not None:
                    raise ExecutionContractError("ENGINE_BUSY", "model has an active operation; stage plan was not registered")
                if state.dirty:
                    raise ExecutionContractError("REVISION_CONFLICT", "model requires reconciliation before stage plan definition")
                if state.revision != expected_revision:
                    raise ExecutionContractError("REVISION_CONFLICT", "expected_revision does not match the current managed model revision")
                revision_key = backend._model_project_key(model_ref.as_dict())
                revision_record = self.store.get_metadata("revisions", revision_key)
                if (not isinstance(revision_record, Mapping)
                        or revision_record.get("model_ref") != model_ref.as_dict()
                        or revision_record.get("project_id") != project_id
                        or revision_record.get("attribution") != "PROJECT_BOUND"
                        or revision_record.get("revision") != state.revision
                        or revision_record.get("dirty") is not False):
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "durable revision binding does not match the selected project ModelRef")

                plan_payload: dict[str, Any] = {
                    "schema_version": 1,
                    "kind": "w21_stage_plan",
                    "project_id": project_id,
                    "model_ref": model_ref.as_dict(),
                    "plan_id": definition["plan_id"],
                    "declaration_revision": state.revision,
                    "definition": definition,
                    "definition_sha256": sha256_json(definition),
                    "declaration_status": "DECLARED_UNVERIFIED",
                    "declaration_evidence": {
                        "schema_and_internal_plan_consistency": "VALIDATED",
                        "study_solution_variable_existence": "NOT_CHECKED",
                        "mapping_method_execution": "NOT_CHECKED",
                        "worker_rpc_performed": False,
                        "solve_started": False,
                    },
                }
                plan_payload["sha256"] = sha256_json(plan_payload)
                stored, created = self.store.register_stage_plan(
                    project_id=project_id,
                    model_ref=model_ref.as_dict(),
                    plan_record=plan_payload,
                )
                data = {
                    "plan_id": stored["plan_id"],
                    "project_id": project_id,
                    "model_ref": stored["model_ref"],
                    "stable_model_identity": {
                        "session_id": model_ref.session_id,
                        "server_instance_id": model_ref.server_instance_id,
                        "model_tag": model_ref.model_tag,
                        "generation": model_ref.generation,
                    },
                    "declaration_revision": stored["declaration_revision"],
                    "definition_sha256": stored["definition_sha256"],
                    "sha256": stored["sha256"],
                    "stage_ids": [stage["stage_id"] for stage in stored["definition"]["stages"]],
                    "registration": "CREATED" if created else "IDEMPOTENT_EXISTING",
                    "declaration_status": stored["declaration_status"],
                    "declaration_evidence": stored["declaration_evidence"],
                    "worker_rpc_performed": False,
                    "solve_started": False,
                }
                result = {
                    "success": True,
                    "data": data,
                    "execution": {
                        "project_id": project_id,
                        "session_id": session_id,
                        "model_ref": model_ref.as_dict(),
                        "revision": state.revision,
                        "engine_dispatched": False,
                    },
                }
                return self._finish(record, result, "SUCCEEDED")
            except StagePlanStoreConflict as exc:
                error = ExecutionContractError(exc.code, str(exc))
                return self._finish(record, self._exception(error), "FAILED")
            except ExecutionContractError as exc:
                return self._finish(record, self._exception(exc), "FAILED")
            except Exception as exc:
                self._log_exception()
                unknown = self._error(
                    "EXECUTION_STATE_UNKNOWN",
                    "stage plan registration state could not be established; replay with the same idempotency key",
                    engine_dispatched=False,
                    safe_retry=False,
                    type=type(exc).__name__,
                )
                return self._finish(record, unknown, "UNKNOWN")

        try:
            future = self.session_scheduler.submit(context, register_definition)
        except SessionSchedulerClosed:
            result = self._error(
                "SESSION_BINDING_FENCED",
                "selected model session is fenced and cannot admit a stage definition",
                data={"engine_dispatched": False},
                safe_retry=False,
            )
            return self._finish(record, result, "FAILED")
        except SessionIdentityConflict:
            result = self._error(
                "SESSION_IDENTITY_CONFLICT",
                "selected model endpoint conflicts with an existing scheduler identity",
                data={"engine_dispatched": False},
                safe_retry=False,
            )
            return self._finish(record, result, "FAILED")
        try:
            return future.result(timeout=timeouts["rpc_timeout_s"])
        except FutureTimeout:
            active_attempt = self.store.get_stage_attempt_for_operation(project_id, model_ref.as_dict(), record["operation_id"])
            return self._pending(record, rpc_wait_expired=True,
                                 engine_dispatched=bool(active_attempt and active_attempt["engine_dispatched"]),
                                 stage_attempt=active_attempt)

    def _dispatch_experiment_stage_run(
        self, arguments: dict[str, Any], execution: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind one stage attempt, then require backend-owned native admission."""
        from ._execution_contract import canonical_request_hash, model_ref_from_mapping
        from ._stage_contract import canonical_json
        from ._g2_registry import validate_call

        identity_fields = (
            "project_id", "session_id", "model_ref", "expected_revision",
            "idempotency_key", "request_id",
        )
        scoped = dict(arguments)
        normalized_execution = dict(execution)
        for field in identity_fields:
            body_value = scoped.get(field)
            outer_value = normalized_execution.get(field)
            if body_value is not None and outer_value is not None and body_value != outer_value:
                code = "IDEMPOTENCY_CONFLICT" if field == "idempotency_key" else "MODEL_IDENTITY_MISMATCH"
                raise ExecutionContractError("IDEMPOTENCY_CONFLICT" if code == "IDEMPOTENCY_CONFLICT" else code,
                                             f"experiment.stage_run {field} differs between arguments and execution envelope")
            value = outer_value if outer_value is not None else body_value
            if value is not None:
                scoped[field] = value
                normalized_execution[field] = value
        if scoped.get("source_attempt_id") is None:
            scoped.pop("source_attempt_id", None)
        if scoped.get("source") is None:
            scoped.pop("source", None)
        entry = validate_call("experiment.stage_run", scoped)
        if entry.effect.upper() != "COMPUTE" or entry.scope != "model":
            raise ExecutionContractError("UNSUPPORTED_OPERATION", "stage run route has an unexpected catalog effect or scope")

        project_id = scoped.get("project_id")
        session_id = scoped.get("session_id")
        model_ref_value = scoped.get("model_ref")
        stage_id = scoped.get("stage_id")
        expected_revision = scoped.get("expected_revision")
        idempotency_key = scoped.get("idempotency_key")
        source_attempt_id = scoped.get("source_attempt_id")
        source = scoped.get("source")
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", "experiment.stage_run requires project_id")
        if not isinstance(session_id, str) or not session_id:
            raise ExecutionContractError("MODEL_IDENTITY_REQUIRED", "experiment.stage_run requires session_id")
        if not isinstance(model_ref_value, Mapping):
            raise ExecutionContractError("MODEL_IDENTITY_REQUIRED", "experiment.stage_run requires a production ModelRef object")
        model_ref = model_ref_from_mapping(model_ref_value)
        if model_ref.session_id != session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "ModelRef session_id differs from the execution session")
        if not isinstance(stage_id, str) or not stage_id:
            raise ExecutionContractError("INVALID_REQUEST", "stage_id must be a non-empty string")
        if source_attempt_id is not None and (not isinstance(source_attempt_id, str) or not source_attempt_id):
            raise ExecutionContractError("INVALID_REQUEST", "source_attempt_id must be a non-empty string when supplied")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ExecutionContractError("REVISION_CONFLICT", "experiment.stage_run requires a non-negative expected_revision")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ExecutionContractError("INVALID_REQUEST", "experiment.stage_run requires a non-empty idempotency_key")

        request_id = scoped.get("request_id") or normalized_execution.get("request_id") or str(uuid4())
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "request_id must be a non-empty string")
        normalized_execution.update({
            "project_id": project_id, "session_id": session_id,
            "model_ref": model_ref.as_dict(), "expected_revision": expected_revision,
            "idempotency_key": idempotency_key, "request_id": request_id,
        })
        self._authorize_project_execution("experiment.stage_run", scoped, normalized_execution)
        timeouts = self.project_authority.apply_timeout_caps(project_id, self._timeouts(normalized_execution))

        operation_arguments = {key: scoped[key] for key in ("stage_id", "source_attempt_id", "source") if key in scoped}
        digest = canonical_request_hash(
            "experiment.stage_run", operation_arguments, model_ref.as_dict(), expected_revision,
            project_id=project_id, session_id=session_id,
            queue_timeout_s=timeouts["queue_timeout_s"], execution_timeout_s=timeouts["execution_timeout_s"],
            no_progress_warning_s=timeouts["no_progress_warning_s"],
        )
        persisted_execution = {
            key: value for key, value in normalized_execution.items()
            if key not in {"rpc_timeout_s", "queue_timeout_s", "execution_timeout_s", "no_progress_warning_s"}
        }
        metadata = {
            "operation": "experiment.stage_run", "arguments": operation_arguments,
            "execution": persisted_execution, "effect": "COMPUTE",
            "engine_dispatched": False,
        }

        def pending_attempt_details() -> dict[str, Any]:
            try:
                rows = self.store.list_stage_attempts(
                    project_id, model_ref.as_dict(), stage_id=stage_id,
                )
            except Exception:
                return {
                    "engine_dispatched": None,
                    "stage_attempt_id": None,
                    "stage_attempt_status": "UNKNOWN",
                }
            matched = next(
                (row for row in reversed(rows)
                 if row.get("request_id") == request_id
                 and row.get("idempotency_key") == idempotency_key),
                None,
            )
            return {
                "engine_dispatched": bool(matched and matched.get("engine_dispatched")),
                "stage_attempt_id": matched.get("attempt_id") if matched else None,
                "stage_attempt_status": matched.get("status") if matched else None,
            }

        with self.lock:
            record, reused = self.store.begin(
                request_id=request_id, idempotency_key=idempotency_key, request_hash=digest,
                operation="experiment.stage_run", metadata=metadata, timeouts=timeouts,
            )
            if reused:
                if record.get("result") is not None:
                    return record["result"]
                return self._pending(record, reused_pending=True, **pending_attempt_details())

        try:
            context = self._execution_session_context(normalized_execution)
        except ExecutionContractError as exc:
            return self._finish(record, self._exception(exc), "FAILED")

        def preflight_and_record() -> dict[str, Any]:
            attempt: dict[str, Any] | None = None
            try:
                self._authorize_project_execution("experiment.stage_run", scoped, normalized_execution)
                backend = context.backend if context is not None else self._default_backend
                service = context.service if context is not None else getattr(backend, "service", None)
                if service is None or getattr(service, "ledger", None) is None:
                    raise ExecutionContractError("SESSION_NOT_CONNECTED", "stage_run requires a current managed model ledger")
                if context is not None and (context.project_id != project_id or context.session_id != session_id):
                    raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "stage_run session context differs from its project binding")
                if service.ledger.session_id != session_id:
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "ModelRef session does not match the selected managed ledger")
                state = service.ledger._state_for(model_ref)
                if state.active_operation_id is not None:
                    raise ExecutionContractError("ENGINE_BUSY", "model has an active operation; stage was not admitted")
                if state.dirty:
                    raise ExecutionContractError("REVISION_CONFLICT", "model requires reconciliation before stage execution")
                if state.revision != expected_revision:
                    raise ExecutionContractError("REVISION_CONFLICT", "expected_revision does not match the current managed model revision")
                revision_key = backend._model_project_key(model_ref.as_dict())
                revision_record = self.store.get_metadata("revisions", revision_key)
                if (not isinstance(revision_record, Mapping)
                        or revision_record.get("model_ref") != model_ref.as_dict()
                        or revision_record.get("project_id") != project_id
                        or revision_record.get("attribution") != "PROJECT_BOUND"
                        or revision_record.get("revision") != state.revision
                        or revision_record.get("dirty") is not False):
                    raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "durable revision binding does not match the selected project ModelRef")

                resolved = self.store.resolve_stage(project_id, model_ref.as_dict(), stage_id)
                if resolved is None:
                    raise StagePlanStoreConflict("STAGE_NOT_FOUND", "stage_id is not registered in this project/model scope")
                plan, stage = resolved["plan"], resolved["stage"]
                if plan["definition"].get("version") != 2:
                    raise StagePlanStoreConflict("STAGE_PLAN_V1_DECLARATION_ONLY", "version 1 stage plans are declaration-only")
                frozen_source = stage.get("source_selection")
                if source is not None:
                    if not isinstance(frozen_source, Mapping) or frozen_source.get("kind") != "stage" \
                            or canonical_json(source) != canonical_json(frozen_source.get("selection")):
                        raise StagePlanStoreConflict("STAGE_SOURCE_SELECTION_MISMATCH", "caller source selector must exactly match the immutable stage plan")

                attempt, attempt_reused = self.store.begin_stage_attempt(
                    project_id=project_id, model_ref=model_ref.as_dict(), stage_id=stage_id,
                    expected_revision=state.revision, request_id=request_id,
                    operation_id=record["operation_id"], idempotency_key=idempotency_key,
                    request_hash=digest, source_attempt_id=source_attempt_id,
                )
                if attempt_reused:
                    # Operation-level idempotency should already have returned
                    # the authoritative result. A cross-record reuse here is
                    # a durable consistency error, never a second attempt.
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt exists without its matching operation result")
                from ._w21_stage_backend import stage_attempt_binding, validate_native_admission

                binding = stage_attempt_binding(attempt)
                admission_provider = getattr(backend, "stage_native_admission", None)
                admission = (admission_provider(binding=binding, plan=plan, stage=stage)
                             if callable(admission_provider) else None)
                native_admitted, admission_missing = validate_native_admission(admission, binding)
                if not native_admitted:
                    admission_summary = {
                        "kind": "native-stage-admission",
                        "status": "UNVERIFIED",
                        "producer": admission.get("producer") if isinstance(admission, Mapping) else None,
                        "binding_verified": isinstance(admission, Mapping) and admission.get("binding") == binding,
                        "facts": dict(admission.get("facts", {})) if isinstance(admission, Mapping)
                                 and isinstance(admission.get("facts"), Mapping) else {},
                        "missing": admission_missing,
                    }
                    evidence = [{
                        "kind": "pre-solve-admission",
                        "status": "NOT_DISPATCHED_UNVERIFIED",
                        "worker_rpc_performed": False,
                        "solve_started": False,
                        "reason": "managed backend cannot prove the exact target field/unit/mesh/frame profile",
                    }, admission_summary]
                    attempt = self.store.update_stage_attempt(
                        project_id, model_ref.as_dict(), attempt["attempt_id"], expected_version=attempt["version"],
                        status="NOT_DISPATCHED_UNVERIFIED", engine_dispatched=False,
                        execution_status="NOT_DISPATCHED", acceptance_status="UNVERIFIED",
                        evidence=evidence,
                        result={"mapping_status": "UNVERIFIED", "continuity": "NOT_RUN", "conservation": "NOT_RUN",
                                "native_admission": "UNVERIFIED"},
                    )
                    result = self._error(
                        "STAGE_PROFILE_UNVERIFIED",
                        "stage execution was refused before Worker dispatch because the managed backend could not produce complete, exact-bound native field/unit/mesh/frame evidence",
                        data={
                            "stage_id": stage_id,
                            "plan_id": plan["plan_id"],
                            "plan_sha256": plan["sha256"],
                            "attempt": attempt,
                            "execution_status": "NOT_DISPATCHED",
                            "acceptance_status": "UNVERIFIED",
                            "missing_evidence": [
                                "target Variables belongs to the uniquely attached SolverSequence for study_target",
                                "source and target variable identity plus native unit readback",
                                "exact source/target mesh and declared frame identity",
                                "saved output SolutionSpec tuple and same-time continuity or conservation readback",
                            ],
                            "native_admission_missing": admission_missing,
                            "worker_rpc_performed": False,
                            "solve_started": False,
                            "engine_dispatched": False,
                        },
                        engine_dispatched=False,
                        safe_retry=False,
                    )
                    result["execution"] = {
                        "project_id": project_id, "session_id": session_id,
                        "model_ref": model_ref.as_dict(), "revision": state.revision,
                        "engine_dispatched": False,
                    }
                    return self._finish(record, result, "FAILED")

                return self._execute_admitted_experiment_stage(
                    record=record, attempt=attempt, stage=stage, plan=plan, backend=backend,
                    service=service, project_id=project_id, session_id=session_id,
                    model_ref=model_ref.as_dict(), normalized_execution=normalized_execution,
                    binding=binding,
                )
            except StagePlanStoreConflict as exc:
                result = self._error(exc.code, str(exc), data={
                    "stage_id": stage_id, "engine_dispatched": False,
                    "stage_attempt_created": attempt is not None,
                })
                return self._finish(record, result, "FAILED")
            except ExecutionContractError as exc:
                return self._finish(record, self._exception(exc), "FAILED")
            except Exception as exc:
                self._log_exception()
                if attempt is not None:
                    try:
                        attempt = self.store.update_stage_attempt(
                            project_id, model_ref.as_dict(), attempt["attempt_id"],
                            expected_version=attempt["version"], status="UNKNOWN",
                            engine_dispatched=False, execution_status="UNKNOWN",
                            acceptance_status="UNKNOWN",
                            evidence=[*attempt["evidence"], {"kind": "control-state", "error_type": type(exc).__name__}],
                            result={"reason": "durable no-dispatch completion could not be established"},
                        )
                    except Exception:
                        pass
                result = self._error(
                    "EXECUTION_STATE_UNKNOWN",
                    "stage attempt control state could not be durably established; replay with the same idempotency key",
                    data={"stage_id": stage_id, "engine_dispatched": False,
                          "attempt": attempt}, safe_retry=False,
                    type=type(exc).__name__,
                )
                return self._finish(record, result, "UNKNOWN")

        try:
            future = self.session_scheduler.submit(context, preflight_and_record)
        except SessionSchedulerClosed:
            result = self._error("SESSION_BINDING_FENCED", "selected model session is fenced and cannot admit a stage attempt",
                                 data={"engine_dispatched": False}, safe_retry=False)
            return self._finish(record, result, "FAILED")
        except SessionIdentityConflict:
            result = self._error("SESSION_IDENTITY_CONFLICT", "selected model endpoint conflicts with an existing scheduler identity",
                                 data={"engine_dispatched": False}, safe_retry=False)
            return self._finish(record, result, "FAILED")
        try:
            return future.result(timeout=timeouts["rpc_timeout_s"])
        except FutureTimeout:
            return self._pending(record, rpc_wait_expired=True, **pending_attempt_details())

    def _execute_admitted_experiment_stage(
        self, *, record: Mapping[str, Any], attempt: dict[str, Any], stage: Mapping[str, Any],
        plan: Mapping[str, Any], backend: Any, service: Any, project_id: str,
        session_id: str, model_ref: dict[str, Any], normalized_execution: dict[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run one backend-admitted stage through normal COMPUTE and save tickets.

        This path is deliberately dormant for the current production backend:
        its native admission facts are UNVERIFIED. Test-only fake backends can
        exercise the durable dispatch/save mechanics without asserting native
        COMSOL acceptance.
        """
        from ._execution_contract import canonical_project_path
        from ._execution_contract import model_ref_from_mapping
        from ._stage_contract import sha256_json
        from ._w21_stage_backend import (
            StageWorkerDispatchGate, validate_output_readback, hash_saved_artifact,
        )

        segments = stage.get("study_target", {}).get("segments", [])
        if (not isinstance(segments, list) or len(segments) != 1
                or not isinstance(segments[0], Mapping)
                or segments[0].get("collection") != "study"
                or not isinstance(segments[0].get("tag"), str)
                or not segments[0]["tag"]):
            evidence = [*attempt["evidence"], {
                "kind": "pre-solve-admission", "status": "NOT_DISPATCHED_UNVERIFIED",
                "reason": "only one explicitly resolved study tag is supported by this stage route",
                "worker_rpc_performed": False,
            }]
            attempt = self.store.update_stage_attempt(
                project_id, model_ref, attempt["attempt_id"], expected_version=attempt["version"],
                status="NOT_DISPATCHED_UNVERIFIED", engine_dispatched=False,
                execution_status="NOT_DISPATCHED", acceptance_status="UNVERIFIED",
                evidence=evidence, result={"reason": "unsupported study target path"},
            )
            result = self._error("STAGE_TARGET_UNSUPPORTED", "stage target must identify exactly one study tag",
                                 data={"attempt": attempt, "engine_dispatched": False}, safe_retry=False)
            return self._finish(record, result, "FAILED")

        study_tag = segments[0]["tag"]
        ledger_model_ref = model_ref_from_mapping(model_ref)
        expected_revision = attempt["expected_revision"]
        solve_request_id = f"{attempt['attempt_id']}:solve"
        save_request_id = f"{attempt['attempt_id']}:save"
        solve_idempotency = f"{attempt['idempotency_key']}:solve"
        save_idempotency = f"{attempt['idempotency_key']}:save"
        solve_operation_id = record["operation_id"]
        worker_dispatch_gate = StageWorkerDispatchGate(
            operation_id=solve_operation_id, model_tag=model_ref["model_tag"],
            study_tag=study_tag,
        )
        def current_revision(wanted: int) -> None:
            state = service.ledger._state_for(ledger_model_ref)
            if state.dirty or state.revision != wanted:
                raise ExecutionContractError(
                    "REVISION_CONFLICT",
                    "stage callback revision changed before its bound Worker request",
                )

        def validate_backend_reply(reply: Any, *, minimum_revision: int, phase: str) -> int:
            if not isinstance(reply, Mapping) or not isinstance(reply.get("execution"), Mapping):
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"stage {phase} reply has no execution identity")
            reply_execution = reply["execution"]
            backend_binding = backend.model_project_binding(model_ref) if callable(
                getattr(backend, "model_project_binding", None)) else None
            state = service.ledger._state_for(ledger_model_ref)
            revision = reply_execution.get("revision")
            if (reply_execution.get("model_ref") != model_ref
                    or reply_execution.get("session_id") != session_id
                    or reply_execution.get("project_id") != project_id
                    or not isinstance(backend_binding, Mapping)
                    or backend_binding.get("project_id") != project_id
                    or backend_binding.get("attribution") != "PROJECT_BOUND"
                    or type(revision) is not int or revision < minimum_revision
                    or revision != state.revision or state.dirty
                    or reply_execution.get("dirty") is True):
                raise ExecutionContractError(
                    "MODEL_IDENTITY_MISMATCH",
                    f"stage {phase} reply does not match its exact project/ModelRef/session/revision binding",
                )
            return revision

        def store_worker_event(event: Any, *, request_id: str, phase: str, revision: int) -> None:
            if not isinstance(event, Mapping):
                return
            safe_event = self._redact_session_worker_event(dict(event))
            self.store.add_event(record["job_id"], "worker_request", safe_event)
            try:
                dispatch = worker_dispatch_gate.observe(
                    event, phase=phase, stage_request_id=request_id,
                    backend_binding=event.get("w21_backend_binding"),
                )
            except Exception as exc:
                raise ExecutionContractError(
                    "WORKER_STAGE_BINDING_MISMATCH",
                    f"actual Worker RPC does not match the bound stage {phase}: {type(exc).__name__}: {exc}",
                ) from exc
            if dispatch is None:
                return
            if dispatch.get("dispatch") != phase:
                raise ExecutionContractError(
                    "WORKER_STAGE_BINDING_MISMATCH",
                    "actual Worker mutation does not match the active stage phase",
                )
            current_revision(revision)
            current = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
            if phase == "solve":
                if current is None or current.get("status") != "DISPATCH_INTENT":
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage dispatch intent changed before Worker submission")
                status = "RUNNING"
                dispatched = True
                execution_status = "RUNNING"
            else:
                if current is None or current.get("status") != "RUNNING" or not current.get("engine_dispatched"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage save submission has no dispatched solve attempt")
                status = "RUNNING"
                dispatched = True
                execution_status = "RUNNING"
            self.store.update_stage_attempt(
                project_id, model_ref, attempt["attempt_id"], expected_version=current["version"],
                status=status, engine_dispatched=dispatched, execution_status=execution_status,
                acceptance_status="NOT_EVALUATED",
                evidence=[*current["evidence"], {
                    "kind": "worker-dispatch", "operation": "run_study" if phase == "solve" else "save_model",
                    "phase": "submitted", "stage_request_id": request_id,
                    "worker_request_id": dispatch["worker_request_id"],
                    "worker_request_hash": dispatch.get("worker_request_hash"),
                    "worker_receiver": dispatch["worker_receiver"],
                    "worker_generation": dispatch["worker_generation"],
                    "worker_method": dispatch["worker_method"],
                    "worker_args": dispatch["worker_args"], "revision": revision,
                }],
                result={"solve": "DISPATCHED" if phase == "solve" else "SUCCEEDED",
                        "output_readback": "NOT_RUN", "saved_artifact": "NOT_RUN" if phase == "solve" else "DISPATCHED"},
            )

        def unknown_result(exc: Exception) -> dict[str, Any]:
            current = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
            raw_details = getattr(exc, "details", None)
            details = {}
            if isinstance(raw_details, Mapping):
                details = {
                    key: str(raw_details[key])[:300]
                    for key in ("cause_type", "cause_code", "cause_stage", "cause_message")
                    if raw_details.get(key) is not None
                }
            if current is not None and current["status"] in {"DISPATCH_INTENT", "RUNNING"}:
                try:
                    self.store.update_stage_attempt(
                        project_id, model_ref, attempt["attempt_id"], expected_version=current["version"],
                        status="UNKNOWN", engine_dispatched=current["engine_dispatched"],
                        execution_status="UNKNOWN", acceptance_status="UNKNOWN",
                        evidence=[*current["evidence"], {
                            "kind": "stage-interruption", "phase": "after-dispatch-intent",
                            "error_type": type(exc).__name__,
                            "error_code": getattr(exc, "code", None),
                            "error_message": str(exc)[:300],
                            "cause": details,
                        }],
                        result={"reason": "stage response, output, or saved artifact hash is unresolved"},
                    )
                except Exception:
                    pass
            final_attempt = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
            result = self._error(
                "EXECUTION_STATE_UNKNOWN",
                "stage dispatch or output persistence was interrupted; the durable attempt is not replayable",
                data={"attempt": final_attempt, "engine_dispatched": bool(final_attempt and final_attempt["engine_dispatched"])},
                safe_retry=False, type=type(exc).__name__,
            )
            return self._finish(record, result, "UNKNOWN")

        try:
            attempt = self.store.update_stage_attempt(
                project_id, model_ref, attempt["attempt_id"], expected_version=attempt["version"],
                status="DISPATCH_INTENT", engine_dispatched=False,
                execution_status="DISPATCH_INTENT", acceptance_status="NOT_EVALUATED",
                evidence=[*attempt["evidence"], {
                    "kind": "dispatch-intent", "operation": "run_study",
                    "study_tag": study_tag, "revision": expected_revision,
                    "binding_sha256": sha256_json(dict(binding)),
                }],
                result={"solve": "INTENT_RECORDED", "output_readback": "NOT_RUN", "saved_artifact": "NOT_RUN"},
            )
            project = self.project_authority.get_project(project_id)
            if not isinstance(project, Mapping) or not isinstance(project.get("workspace"), str):
                raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "stage project workspace is unavailable")
            solve_execution = dict(normalized_execution)
            solve_execution.update({
                "project_id": project_id, "session_id": session_id, "model_ref": model_ref,
                "expected_revision": expected_revision, "request_id": solve_request_id,
                "idempotency_key": solve_idempotency,
                "_w21_stage_marker": {
                    "attempt_id": attempt["attempt_id"], "phase": "solve",
                    "project_id": project_id, "model_ref": dict(model_ref),
                    "expected_revision": expected_revision, "request_id": solve_request_id,
                    "operation_id": solve_operation_id, "binding_sha256": sha256_json(dict(binding)),
                    "study_tag": study_tag,
                },
            })
            solve_arguments = {"study_tag": study_tag}
            self._authorize_project_execution("run_study", solve_arguments, solve_execution)
            with backend.project_root_scope(project["workspace"]):
                # Intent is committed above before entering the only solve call.
                solve_result = backend.invoke(
                    "run_study", solve_arguments, solve_execution, solve_operation_id,
                    lambda event: store_worker_event(event, request_id=solve_request_id,
                                                     phase="solve", revision=expected_revision),
                )
                if not isinstance(solve_result, Mapping) or solve_result.get("success") is not True:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "stage solve returned no verified success envelope")
                current = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
                if current is None or current.get("status") != "RUNNING" or not current.get("engine_dispatched"):
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "stage solve returned without durable Worker submission evidence")
                solve_revision = validate_backend_reply(
                    solve_result, minimum_revision=expected_revision, phase="solve",
                )

                output_reader = getattr(backend, "stage_output_readback", None)
                try:
                    output_readback = (output_reader(
                        binding=dict(binding), stage=dict(stage), plan=dict(plan),
                        stage_run_operation_id=solve_operation_id, model_revision=solve_revision,
                        solve_result=dict(solve_result),
                    ) if callable(output_reader) else None)
                    output_valid, output_missing, checks_passed = validate_output_readback(
                        output_readback, binding=binding, stage_run_operation_id=solve_operation_id,
                        model_revision=solve_revision, target_selection=stage["target_selection"],
                        checks=stage["checks"],
                    )
                except Exception as output_error:
                    output_valid = False
                    checks_passed = False
                    output_missing = [f"output readback failed: {type(output_error).__name__}"]
                    output_readback = None

                save_path = f"stage_outputs/{attempt['attempt_id']}.mph"
                target_path = canonical_project_path(project["workspace"], save_path)
                worker_dispatch_gate.save_target_path = str(target_path)
                save_arguments = {"path": save_path}
                save_execution = dict(normalized_execution)
                save_execution.update({
                    "project_id": project_id, "session_id": session_id, "model_ref": model_ref,
                    "expected_revision": solve_revision, "request_id": save_request_id,
                    "idempotency_key": save_idempotency,
                    "_w21_stage_marker": {
                        "attempt_id": attempt["attempt_id"], "phase": "save",
                        "project_id": project_id, "model_ref": dict(model_ref),
                        "expected_revision": solve_revision, "request_id": save_request_id,
                        "operation_id": solve_operation_id, "binding_sha256": sha256_json(dict(binding)),
                        "save_path": save_path, "save_target_path": str(target_path),
                    },
                })
                self._authorize_project_execution("save_model", save_arguments, save_execution)
                current = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
                if current is None or current["status"] != "RUNNING":
                    raise ExecutionContractError("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt changed before output save")
                current = self.store.update_stage_attempt(
                    project_id, model_ref, attempt["attempt_id"], expected_version=current["version"],
                    status="RUNNING", engine_dispatched=True, execution_status="RUNNING",
                    acceptance_status="NOT_EVALUATED",
                    evidence=[*current["evidence"], {
                        "kind": "save-intent", "operation": "save_model",
                        "relative_path": save_path, "revision": solve_revision,
                    }],
                    result={"solve": "SUCCEEDED", "output_readback": "VERIFIED" if output_valid else "UNVERIFIED",
                            "output_missing": output_missing, "saved_artifact": "SAVE_INTENT"},
                )
                current_revision(solve_revision)
                save_result = backend.invoke(
                    "save_model", save_arguments, save_execution, solve_operation_id,
                    lambda event: store_worker_event(event, request_id=save_request_id,
                                                     phase="save", revision=solve_revision),
                )
                if not isinstance(save_result, Mapping) or save_result.get("success") is not True:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "stage save returned no verified success envelope")
                save_revision = validate_backend_reply(
                    save_result, minimum_revision=solve_revision, phase="save",
                )
                save_data = save_result.get("data")
                saved_value = save_data.get("saved_path") if isinstance(save_data, Mapping) else None
                observed_path = canonical_project_path(project["workspace"], saved_value) if isinstance(saved_value, str) else None
                if observed_path != target_path or not target_path.is_file() or target_path.stat().st_size <= 0:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "stage output file path or saved bytes could not be verified")
                artifact_sha256, artifact_size = hash_saved_artifact(target_path)
                if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64 or artifact_size <= 0:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "stage output hash is malformed")
                final_state = service.ledger._state_for(ledger_model_ref)
                if final_state.dirty or final_state.revision != save_revision:
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model revision changed while hashing the saved stage output")

                current = self.store.get_stage_attempt(project_id, model_ref, attempt["attempt_id"])
                if current is None or current["status"] != "RUNNING":
                    raise ExecutionContractError("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt changed before result finalization")
                final_revision = save_revision
                output_tuple = output_readback.get("output_tuple") if isinstance(output_readback, Mapping) else None
                observed_checks = output_readback.get("checks") if isinstance(output_readback, Mapping) else None
                artifact = {"path": str(target_path), "sha256": artifact_sha256, "size": artifact_size}
                output_evidence = {
                    "kind": "stage-output-readback",
                    "status": "VERIFIED" if output_valid else "UNVERIFIED",
                    "checks_status": "PASS" if output_valid and checks_passed else
                                     "FAIL" if output_valid else "UNVERIFIED",
                    "missing": output_missing,
                    "output_tuple": dict(output_tuple) if isinstance(output_tuple, Mapping) else None,
                    "checks": [
                        {key: item.get(key) for key in ("check_id", "status", "observed_error", "reference_scale", "unit")}
                        for item in observed_checks if isinstance(item, Mapping)
                    ] if isinstance(observed_checks, list) else [],
                    "target_selection_sha256": sha256_json(stage["target_selection"]),
                }
                attempt = self.store.update_stage_attempt(
                    project_id, model_ref, attempt["attempt_id"], expected_version=current["version"],
                    status="SUCCEEDED_PARTIAL", engine_dispatched=True,
                    execution_status="SOLVE_SUCCEEDED", acceptance_status="PARTIAL",
                    evidence=[*current["evidence"], output_evidence, {
                        "kind": "saved-stage-artifact", **artifact, "model_revision": final_revision,
                    }],
                    result={
                        "solve": "SUCCEEDED", "output_readback": "VERIFIED" if output_valid else "UNVERIFIED",
                        "checks_status": "PASS" if output_valid and checks_passed else
                                        "FAIL" if output_valid else "UNVERIFIED",
                        "output_missing": output_missing, "saved_artifact": artifact,
                        "acceptance": "PARTIAL", "acceptance_reason": "scientific stage acceptance remains unverified",
                    },
                )
                result = self._error(
                    "STAGE_ACCEPTANCE_UNVERIFIED",
                    "the stage solve and saved artifact are recorded, but this route does not certify scientific acceptance",
                    data={
                        "stage_id": attempt["stage_id"], "attempt": attempt,
                        "execution_status": "SOLVE_SUCCEEDED", "acceptance_status": "PARTIAL",
                        "output_readback_status": "VERIFIED" if output_valid else "UNVERIFIED",
                        "checks_status": "PASS" if output_valid and checks_passed else
                                         "FAIL" if output_valid else "UNVERIFIED",
                        "output_missing": output_missing, "saved_artifact": artifact,
                        "engine_dispatched": True,
                    },
                    engine_dispatched=True, safe_retry=False,
                )
                result["execution"] = {
                    "project_id": project_id, "session_id": session_id, "model_ref": model_ref,
                    "revision": final_revision, "engine_dispatched": True,
                }
                return self._finish(record, result, "FAILED")
        except Exception as exc:
            return unknown_result(exc)

    def _dispatch_experiment_durable_read(
        self, operation: str, arguments: dict[str, Any], execution: dict[str, Any],
    ) -> dict[str, Any]:
        """Read project-owned W21 experiment records without Worker or queue access."""
        from . import _g2_registry

        entry = _g2_registry.validate_call(operation, arguments)
        if entry.effect.upper() != "READ" or entry.scope != "project":
            raise ExecutionContractError("UNSUPPORTED_OPERATION", "experiment durable read has an unexpected catalog scope")
        project_id = arguments.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ExecutionContractError("INVALID_REQUEST", "project_id is required")
        if execution.get("project_id") not in (None, project_id):
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "experiment project_id differs from the execution envelope")
        experiment_id = arguments.get("experiment_id")
        case_id = arguments.get("case_id") if operation == "experiment.case_result" else None
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ExecutionContractError("INVALID_REQUEST", "experiment_id is required")
        if operation == "experiment.case_result" and (not isinstance(case_id, str) or not case_id):
            raise ExecutionContractError("INVALID_REQUEST", "case_id is required")

        # Authorize the caller's declared project before any artifact lookup;
        # foreign IDs and absent IDs therefore share the same response.
        authorized_project = self.project_authority.authorize_operation(project_id, "inspect")
        project_workspace = authorized_project.get("workspace")
        if not isinstance(project_workspace, str) or not project_workspace:
            raise ExecutionContractError("PROJECT_STATE_UNKNOWN", "authorized project workspace is unavailable")
        snapshot = self.store.read_experiment_snapshot(project_id, experiment_id, case_id=case_id)
        design = snapshot.get("design")
        if not isinstance(design, dict):
            raise ExecutionContractError("EXPERIMENT_NOT_FOUND", "experiment was not found")

        design_project = design.get("project_id")
        if design_project is not None and design_project != project_id:
            raise ExecutionContractError("EXPERIMENT_NOT_FOUND", "experiment was not found")
        if (type(design.get("schema_version")) is not int or design.get("schema_version") != 1
                or design.get("kind") != "w21experiment"
                or design.get("experiment_id") != experiment_id
                or not isinstance(design.get("sha256"), str)
                or design.get("sha256") != self._experiment_record_sha256(design)
                or not isinstance(design.get("definition"), dict)
                or design.get("definition_sha256") != hashlib.sha256(
                    json.dumps(design["definition"], sort_keys=True, allow_nan=False).encode("utf-8")
                ).hexdigest()):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment design failed identity or integrity validation")
        if (not snapshot.get("metric_version_snapshot_valid")
                or design.get("metric_definition_snapshots", []) != snapshot.get("metric_definition_versions")):
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment metric version snapshot failed project/version validation")

        producer_id = design.get("producer")
        producer = snapshot.get("operations", {}).get(producer_id) if isinstance(producer_id, str) else None
        producer_operation, producer_arguments = (
            self._experiment_producer_arguments(producer) if isinstance(producer, Mapping) else (None, None)
        )
        if (not isinstance(producer, dict) or producer_operation != "experiment.design"
                or not isinstance(producer_arguments, Mapping)
                or not self._experiment_operation_project(producer, project_id)
                or producer_arguments.get("definition") != design["definition"]):
            # When the record itself omitted project_id, do not infer that it
            # belongs to the caller merely because its opaque id was supplied.
            raise ExecutionContractError("EXPERIMENT_NOT_FOUND", "experiment was not found")
        design_status = self._experiment_operation_status(producer)
        if design_status != "SUCCEEDED":
            design_status = "UNKNOWN"

        planned_case_ids = snapshot.get("planned_case_ids")
        design_cases = design.get("cases")
        planned_cases = self._planned_experiment_cases(
            design_cases, planned_case_ids, design["definition"],
        )
        if planned_cases is None:
            raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment case index is malformed")

        run = snapshot.get("run")
        run_producer: dict[str, Any] | None = None
        run_status = "NOT_RUN"
        run_case_rows: dict[str, dict[str, Any]] = {}
        run_id = None
        if run is not None:
            if (not isinstance(run, dict) or type(run.get("schema_version")) is not int
                    or run.get("schema_version") != 1
                    or run.get("kind") != "w21experiment_run"
                    or run.get("experiment_id") != experiment_id
                    or run.get("design_sha256") != design.get("sha256")
                    or not isinstance(run.get("run_id"), str) or not run["run_id"]
                    or not isinstance(run.get("sha256"), str)
                    or run.get("sha256") != self._experiment_record_sha256(run)):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment run failed identity or integrity validation")
            if run.get("project_id") is not None and run.get("project_id") != project_id:
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment run project binding is inconsistent")
            run_id = run["run_id"]
            run_producer_id = run.get("producer")
            run_producer = snapshot.get("operations", {}).get(run_producer_id) if isinstance(run_producer_id, str) else None
            run_producer_operation, run_producer_arguments = (
                self._experiment_producer_arguments(run_producer)
                if isinstance(run_producer, Mapping) else (None, None)
            )
            if (not isinstance(run_producer, dict) or run_producer_operation != "experiment.run"
                    or not isinstance(run_producer_arguments, Mapping)
                    or not self._experiment_operation_project(run_producer, project_id)
                    or run_producer_arguments.get("experiment_id") != experiment_id):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment run producer binding is inconsistent")
            if (design.get("model_ref") is not None and run.get("model_ref") != design.get("model_ref")):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment model lineage is inconsistent")
            run_case_rows = self._experiment_case_rows(run, planned_cases)
            if run_case_rows is None or not set(run_case_rows).issubset(set(planned_case_ids)):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable experiment run case index is malformed")
            run_status = self._experiment_operation_status(run_producer)
            if run_status == "SUCCEEDED":
                declared = run.get("status")
                if declared not in {"COMPLETE", "PARTIAL", "FAILED", "EXECUTION_STATE_UNKNOWN"}:
                    run_status = "UNKNOWN"
                elif declared == "EXECUTION_STATE_UNKNOWN":
                    run_status = "UNKNOWN"
                else:
                    run_status = declared
            elif run_status == "FAILED":
                # The durable producer job is authoritative if a callback
                # failed before it could replace the initial RUNNING artifact.
                run_status = "FAILED"
            elif run_status in {"QUEUED", "RUNNING"}:
                declared = run.get("status")
                run_status = declared if declared == "RUNNING" else "UNKNOWN"

        # A queued experiment.run has no run artifact until its callback claims
        # the immutable single-run key.  Observe only operations whose exact
        # project and experiment arguments are both durably attributable.
        matching_run_attempts: list[dict[str, Any]] = []
        ambiguous_run_attempt = False
        if run is None:
            candidates = snapshot.get("run_operations", [])
            if not isinstance(candidates, list):
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable run-attempt snapshot is malformed")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                candidate_operation, args = self._experiment_producer_arguments(candidate)
                metadata = candidate.get("metadata")
                if candidate_operation != "experiment.run" or not isinstance(args, Mapping) or args.get("experiment_id") != experiment_id:
                    continue
                if not self._experiment_operation_project(candidate, project_id):
                    metadata_project_claims = []
                    for source in (metadata, metadata.get("execution") if isinstance(metadata, Mapping) else None,
                                   args, candidate.get("job_metadata")):
                        if isinstance(source, Mapping) and "project_id" in source:
                            metadata_project_claims.append(source.get("project_id"))
                    if (candidate.get("operation") in {"registry_call", "operation_call"}
                            and isinstance(metadata, Mapping) and isinstance(metadata.get("arguments"), Mapping)
                            and "project_id" in metadata["arguments"].get("arguments", {})):
                        metadata_project_claims.append(metadata["arguments"]["arguments"].get("project_id"))
                    if not metadata_project_claims:
                        ambiguous_run_attempt = True
                    continue
                matching_run_attempts.append(candidate)
            if ambiguous_run_attempt:
                run_status = "UNKNOWN"
            elif matching_run_attempts:
                statuses = [self._experiment_operation_status(row) for row in matching_run_attempts]
                if any(status == "UNKNOWN" for status in statuses):
                    run_status = "UNKNOWN"
                elif any(status == "RUNNING" for status in statuses):
                    run_status = "RUNNING"
                elif any(status == "QUEUED" for status in statuses):
                    run_status = "QUEUED"
                elif any(status == "SUCCEEDED" for status in statuses):
                    # A completed producer without the run artifact it should
                    # have durably written is an inconsistent record.
                    run_status = "UNKNOWN"
                else:
                    terminal_statuses = set(statuses)
                    run_status = next(iter(terminal_statuses)) if len(terminal_statuses) == 1 else "UNKNOWN"

        if operation == "experiment.case_result":
            if case_id not in planned_case_ids:
                raise ExecutionContractError("CASE_NOT_FOUND", "case was not found in this experiment")
            case_record = snapshot.get("cases", {}).get(case_id)
            case_data: dict[str, Any] | None = None
            record_source = None
            model_artifact_summary = {"status": "NOT_AVAILABLE", "reason_code": "NO_SAME_WORKER_MODEL_COPY",
                                      "restore_status": "NOT_AVAILABLE"}
            expected_ordinal, planned_parameters = planned_cases[case_id]
            if case_record is not None:
                if (not isinstance(run, dict) or not isinstance(case_record, dict)
                        or case_record.get("kind") != "w21experiment_case"
                        or case_record.get("experiment_id") != experiment_id
                        or case_record.get("run_id") != run_id
                        or case_record.get("producer") != run.get("producer")
                        or case_record.get("sha256") != self._experiment_record_sha256(case_record)
                        or not self._valid_experiment_case_data(
                            case_record.get("case"), case_id, expected_ordinal, planned_parameters,
                        )):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case result failed identity or integrity validation")
                if case_record.get("project_id") is not None and case_record.get("project_id") != project_id:
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case result project binding is inconsistent")
                if (case_record.get("model_ref") is not None and run is not None
                        and case_record.get("model_ref") != run.get("model_ref")):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case model lineage is inconsistent")
                case_data = case_record["case"]
                recorded_by_run = run_case_rows.get(case_id)
                if (recorded_by_run is not None
                        and self._normalized_experiment_case_data(recorded_by_run, expected_ordinal)
                        != self._normalized_experiment_case_data(case_data, expected_ordinal)):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case result disagrees across durable W21 records")
                self._verify_experiment_case_metric_association(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id,
                    case_id=case_id, design=design, run=run, run_producer=run_producer,
                    case_record=case_record, case_data=case_data,
                    expected_ordinal=expected_ordinal, planned_parameters=planned_parameters,
                )
                model_artifact_summary = self._verify_experiment_case_model_artifact(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id, case_id=case_id, design=design, run=run,
                    run_producer=run_producer, case_record=case_record, case_data=case_data,
                    expected_ordinal=expected_ordinal, planned_parameters=planned_parameters,
                )
                record_source = "case_artifact"
            elif case_id in run_case_rows:
                # The finalized run artifact is itself a hash-bound durable
                # result. Older stores may have the aggregate but no per-case
                # row, so preserve and label that compatible representation.
                case_data = run_case_rows[case_id]
                self._verify_experiment_case_metric_association(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id,
                    case_id=case_id, design=design, run=run, run_producer=run_producer,
                    case_record=None, case_data=case_data,
                    expected_ordinal=expected_ordinal, planned_parameters=planned_parameters,
                )
                model_artifact_summary = self._verify_experiment_case_model_artifact(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id, case_id=case_id, design=design, run=run,
                    run_producer=run_producer, case_record=None, case_data=case_data,
                    expected_ordinal=expected_ordinal, planned_parameters=planned_parameters,
                )
                record_source = "run_artifact"
            return {
                "success": True,
                "data": {
                    "project_id": project_id,
                    "experiment_id": experiment_id,
                    "case_id": case_id,
                    "case_ordinal": expected_ordinal,
                    "status": (case_data.get("status") if isinstance(case_data, Mapping)
                               else "UNKNOWN" if run_status == "UNKNOWN"
                               else "NOT_RECORDED"),
                    "run_status": run_status,
                    "result": case_data,
                    "record_source": record_source,
                    "case_model_artifact": model_artifact_summary,
                },
            }

        case_summaries = []
        best_feasible_rows = []
        for case_id in planned_case_ids:
            ordinal, planned_parameters = planned_cases[case_id]
            result_row = snapshot.get("cases", {}).get(case_id)
            case_data = None
            source = None
            if isinstance(result_row, dict):
                if (result_row.get("kind") != "w21experiment_case"
                        or result_row.get("experiment_id") != experiment_id
                        or result_row.get("run_id") != run_id
                        or result_row.get("producer") != (run.get("producer") if isinstance(run, dict) else None)
                        or result_row.get("sha256") != self._experiment_record_sha256(result_row)
                        or not self._valid_experiment_case_data(
                            result_row.get("case"), case_id, ordinal, planned_parameters,
                        )):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case index failed identity or integrity validation")
                if result_row.get("project_id") is not None and result_row.get("project_id") != project_id:
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case project binding is inconsistent")
                if (result_row.get("model_ref") is not None and isinstance(run, dict)
                        and result_row.get("model_ref") != run.get("model_ref")):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable case model lineage is inconsistent")
                case_data = result_row["case"]
                if (case_id in run_case_rows
                        and self._normalized_experiment_case_data(run_case_rows[case_id], ordinal)
                        != self._normalized_experiment_case_data(case_data, ordinal)):
                    raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "case result disagrees across durable W21 records")
                self._verify_experiment_case_metric_association(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id,
                    case_id=case_id, design=design, run=run, run_producer=run_producer,
                    case_record=result_row, case_data=case_data,
                    expected_ordinal=ordinal, planned_parameters=planned_parameters,
                )
                source = "case_artifact"
            elif case_id in run_case_rows:
                case_data = run_case_rows[case_id]
                self._verify_experiment_case_metric_association(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id,
                    case_id=case_id, design=design, run=run, run_producer=run_producer,
                    case_record=None, case_data=case_data,
                    expected_ordinal=ordinal, planned_parameters=planned_parameters,
                )
                source = "run_artifact"
            case_status = (
                case_data.get("status") if isinstance(case_data, Mapping)
                else "UNKNOWN" if run_status == "UNKNOWN"
                else "NOT_RECORDED"
            )
            model_artifact_summary = (
                self._verify_experiment_case_model_artifact(
                    snapshot=snapshot, project_id=project_id, project_workspace=project_workspace,
                    experiment_id=experiment_id, case_id=case_id, design=design, run=run,
                    run_producer=run_producer, case_record=result_row if isinstance(result_row, Mapping) else None,
                    case_data=case_data, expected_ordinal=ordinal, planned_parameters=planned_parameters,
                )
                if isinstance(case_data, Mapping)
                else {"status": "NOT_AVAILABLE", "reason_code": "NO_SAME_WORKER_MODEL_COPY",
                      "restore_status": "NOT_AVAILABLE"}
            )
            case_summaries.append({
                "case_id": case_id,
                "case_ordinal": ordinal,
                "status": case_status,
                "parameters": planned_parameters,
                "record_source": source,
                "failure_reason": self._experiment_failure_summary(case_status, case_data),
                "cache": self._experiment_cache_summary(case_data),
                "case_model_artifact": model_artifact_summary,
            })
            best_feasible_rows.append({
                "case_id": case_id,
                "case_ordinal": ordinal,
                "status": case_status,
                "metric_evaluation": case_data.get("metric_evaluation") if isinstance(case_data, Mapping) else None,
            })

        case_cache_hits = sum(row["cache"]["hit"] is True for row in case_summaries)
        case_cache_misses = sum(row["cache"]["hit"] is False for row in case_summaries)
        cache_unknown = len(case_summaries) - case_cache_hits - case_cache_misses
        if not isinstance(run, dict):
            cache_state = "NOT_RUN" if run_status == "NOT_RUN" else "UNAVAILABLE"
        elif cache_unknown == 0:
            cache_state = "COMPLETE"
        elif case_cache_hits or case_cache_misses:
            cache_state = "PARTIAL"
        else:
            cache_state = "UNAVAILABLE"
        if isinstance(run, dict) and "cache_hits" in run:
            recorded_hits = run.get("cache_hits")
            if type(recorded_hits) is not int or recorded_hits < case_cache_hits:
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable run cache summary is malformed or inconsistent")
            if cache_unknown == 0 and recorded_hits != case_cache_hits:
                raise ExecutionContractError("EXPERIMENT_STATE_UNKNOWN", "durable run cache hit count disagrees with its case records")
        cache_summary = {
            "state": cache_state,
            "hits": case_cache_hits,
            "misses": case_cache_misses,
            "unknown_cases": cache_unknown,
            "total_cases": len(case_summaries),
        }
        failure_states = {"FAILED", "UNKNOWN", "EXECUTION_STATE_UNKNOWN", "CANCELLED", "EXPIRED", "LOST"}
        if run_status not in failure_states:
            run_failure_reason = self._experiment_failure_summary(run_status, None)
        else:
            reported_case_failure = next((row["failure_reason"] for row in case_summaries
                                          if row["failure_reason"]["state"] == "REPORTED"), None)
            if reported_case_failure is not None:
                run_failure_reason = reported_case_failure
            elif isinstance(run, dict):
                run_failure_reason = self._experiment_failure_summary(run_status, run)
            else:
                failed_attempts = [row for row in matching_run_attempts
                                   if self._experiment_operation_status(row) in failure_states]
                if len(failed_attempts) == 1:
                    run_failure_reason = self._experiment_failure_summary(
                        run_status, failed_attempts[0],
                    )
                elif len(failed_attempts) > 1:
                    run_failure_reason = {"state": "AMBIGUOUS", "code": None, "message": None}
                else:
                    run_failure_reason = self._experiment_failure_summary(run_status, None)

        best_feasible = self._experiment_best_feasible_summary(
            design=design,
            planned_case_ids=planned_case_ids,
            case_rows=best_feasible_rows,
            evaluations=snapshot.get("evaluations", {}),
        )

        if run_status == "NOT_RUN" and design_status != "SUCCEEDED":
            overall_status = "UNKNOWN"
        elif run_status == "NOT_RUN":
            overall_status = "DESIGNED"
        else:
            overall_status = run_status
        return {
            "success": True,
            "data": {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "status": overall_status,
                "design_status": design_status,
                "design": {
                    "study": design.get("study"),
                    "sampling_profile": design.get("sampling_profile"),
                    "definition_sha256": design.get("definition_sha256"),
                    "budget": design.get("budget"),
                    "case_count": len(planned_case_ids),
                },
                "run": ({
                    "run_id": run_id,
                    "status": run_status,
                    "completion_status": run.get("completion_status"),
                    "effective_budget": run.get("effective_budget"),
                    "budget": run.get("budget"),
                    "recorded_case_count": len(run_case_rows),
                    "failure_reason": run_failure_reason,
                    "cache_summary": cache_summary,
                } if isinstance(run, dict) else {
                    "status": run_status,
                    "attempt_count": len(matching_run_attempts),
                    "failure_reason": run_failure_reason,
                    "cache_summary": cache_summary,
                }),
                "cases": case_summaries,
                "best_feasible": best_feasible,
                "result_scope": "durable snapshot only; no current ModelRef validation, Worker RPC, or engine-queue admission",
            },
        }

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
                    if worker is not None:
                        # Preserve exact Popen/PID/birth identity whenever the
                        # uncertain attach still has a live child. Recovery may
                        # use it for a read-only classification, but never
                        # upgrades an identity that this path could not verify.
                        self._remember_session_worker_child((project_id, session_id), worker)
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

    def _session_lifecycle_recovery_resolution_is_valid(self, job: Mapping[str, Any]) -> bool:
        """Validate one atomic, request-quiescence-only lifecycle resolution."""
        job_id, operation_id = job.get("job_id"), job.get("operation_id")
        metadata, operation = job.get("metadata"), job.get("operation")
        if (not isinstance(job_id, str) or not isinstance(operation_id, str)
                or not isinstance(metadata, Mapping) or not isinstance(operation, Mapping)
                or metadata.get("reconciled_quiescent") is not True
                or job.get("status") not in {"UNKNOWN", "RECONCILING"}
                or operation.get("status") not in {"UNKNOWN", "RECONCILING"}
                or operation.get("operation") not in {
                    "session.connect", "session.disconnect", "session.reconnect",
                }):
            return False
        result = job.get("result")
        if not isinstance(result, Mapping):
            return False
        try:
            result_digest = session_recovery_evidence_sha256({"source_result": dict(result)})
            event = self.store.session_lifecycle_recovery_resolution(job_id)
        except Exception:
            return False
        if not isinstance(event, Mapping) or not isinstance(event.get("metadata"), Mapping):
            return False
        proof = dict(event["metadata"])
        supplied_digest = proof.pop("evidence_sha256", None)
        if (not isinstance(supplied_digest, str) or len(supplied_digest) != 64
                or session_recovery_evidence_sha256(proof) != supplied_digest):
            return False
        pointer = metadata.get("session_lifecycle_recovery_resolution")
        transition = proof.get("lifecycle_transition")
        target = transition.get("target_lifecycle") if isinstance(transition, Mapping) else None
        try:
            target_record = validate_lifecycle_record(target) if isinstance(target, Mapping) else None
        except Exception:
            target_record = None
        worker_observation = proof.get("worker_observation")
        source_session = metadata.get("session_id")
        source_project = metadata.get("project_id")
        source_operation = operation.get("operation")
        original_binding = proof.get("original_worker_binding")
        binding = metadata.get("runtime_binding")
        binding_matches = (
            isinstance(binding, Mapping) and binding.get("kind") == "registered_session"
            and isinstance(original_binding, Mapping)
            and original_binding.get("kind") == "registered_session"
            and original_binding.get("project_id") == binding.get("project_id") == source_project
            and original_binding.get("session_id") == binding.get("session_id") == source_session
            and original_binding.get("worker_instance_id") == binding.get("worker_instance_id")
            and original_binding.get("worker_epoch") == binding.get("worker_epoch")
            and type(original_binding.get("worker_epoch")) is int
            and original_binding.get("worker_epoch") > 0
        )
        scope = proof.get("resolution_scope")
        classification = proof.get("classification")
        request_observations = proof.get("request_observations")
        terminal_replies = proof.get("terminal_reply_identities")
        if (not isinstance(worker_observation, Mapping)
                or worker_observation.get("remote_engine_health_claim") is not False
                or not isinstance(request_observations, list)
                or not isinstance(terminal_replies, list)
                or not binding_matches):
            return False
        arguments = metadata.get("arguments") if isinstance(metadata.get("arguments"), Mapping) else {}
        retire_requested = source_operation == "session.disconnect" and arguments.get("retire_worker") is True
        reconnect_baseline = proof.get("reconnect_baseline")
        expected_kinds: list[str]
        if source_operation == "session.connect":
            expected_kinds = ["connect"]
        elif source_operation == "session.disconnect":
            expected_kinds = ([] if retire_requested and not terminal_replies else ["disconnect"])
        else:
            if (not isinstance(reconnect_baseline, Mapping)
                    or type(reconnect_baseline.get("detach_required")) is not bool
                    or reconnect_baseline.get("operation_id") != operation_id
                    or reconnect_baseline.get("project_id") != source_project
                    or reconnect_baseline.get("session_id") != source_session
                    or reconnect_baseline.get("worker_instance_id") != original_binding.get("worker_instance_id")
                    or reconnect_baseline.get("worker_epoch") != original_binding.get("worker_epoch")
                    or not isinstance(reconnect_baseline.get("observed_peer"), Mapping)):
                return False
            expected_kinds = (["disconnect", "connect"]
                              if reconnect_baseline.get("detach_required") else ["connect"])
        if [item.get("kind") for item in terminal_replies if isinstance(item, Mapping)] != expected_kinds:
            return False
        if len(terminal_replies) != len(expected_kinds) or len(request_observations) != len(expected_kinds):
            return False
        expected_request_ids = []
        if source_operation == "session.connect":
            expected_request_ids = [operation.get("request_id")]
        elif source_operation == "session.disconnect":
            expected_request_ids = ([] if not expected_kinds else [f"{operation_id}:disconnect"])
        else:
            expected_request_ids = ([f"{operation_id}:disconnect", f"{operation_id}:connect"]
                                    if reconnect_baseline.get("detach_required")
                                    else [f"{operation_id}:connect"])
        for index, (reply, observation, kind, request_id) in enumerate(zip(
                terminal_replies, request_observations, expected_kinds, expected_request_ids)):
            if (not isinstance(reply, Mapping) or not isinstance(observation, Mapping)
                    or reply.get("kind") != kind or reply.get("status") != "SUCCEEDED"
                    or reply.get("request_id") != request_id
                    or reply.get("worker_instance_id") != original_binding.get("worker_instance_id")
                    or type(reply.get("worker_epoch")) is not int or reply.get("worker_epoch") < 1
                    or not isinstance(reply.get("reply_sha256"), str) or len(reply["reply_sha256"]) != 64
                    or observation.get("request_id") != request_id
                    or observation.get("kind") != kind
                    or observation.get("source_operation_id") != operation_id
                    or observation.get("status") != "SUCCEEDED"
                    or observation.get("terminal") is not True
                    or observation.get("request_id_match") is not True
                    or observation.get("request_type_match") is not True
                    or not isinstance(observation.get("reply_sha256"), str)
                    or len(observation["reply_sha256"]) != 64):
                return False
        if scope == "SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE":
            if source_operation in {"session.connect", "session.reconnect"}:
                connection = proof.get("connection_observation")
                if (not isinstance(connection, Mapping)
                        or classification != "CONNECTED_EXACT_REPLY_WORKER_AND_PEER"
                        or worker_observation.get("state") != "LIVE_EXACT"
                        or worker_observation.get("runtime_pid_matches") is not True
                        or worker_observation.get("process_birth_matches") is not True
                        or type(worker_observation.get("pid")) is not int
                        or worker_observation.get("pid") <= 1
                        or type(worker_observation.get("start_epoch_ms")) is not int
                        or worker_observation.get("start_epoch_ms") <= 0
                        or target_record.get("state") != "CONNECTED"
                        or target_record.get("client_state") != "CONNECTED"
                        or target_record.get("worker_instance_id") != original_binding.get("worker_instance_id")
                        or target_record.get("worker_epoch") != connection.get("worker_epoch")
                        or target_record.get("server_instance_id") != connection.get("server_instance_id")
                        or connection.get("worker_instance_id") != original_binding.get("worker_instance_id")
                        or connection.get("remote_health_claim") is not False
                        or not isinstance(connection.get("endpoint"), Mapping)
                        or connection.get("endpoint") != target_record.get("endpoint")
                        or not isinstance(connection.get("observed_peer"), Mapping)
                        or connection.get("observed_peer", {}).get("port") != target_record.get("endpoint", {}).get("port")
                        or not isinstance(connection.get("remote_engine_version"), str)
                        or not connection.get("remote_engine_version")
                        or not isinstance(connection.get("connect_reply_sha256"), str)
                        or connection.get("connect_reply_sha256") != terminal_replies[-1].get("reply_sha256")
                        or worker_observation.get("instance_id") != connection.get("worker_instance_id")
                        or worker_observation.get("generation") != connection.get("worker_epoch")
                        or worker_observation.get("connected") is not True):
                    return False
                remote_build = connection.get("remote_engine_build")
                expected_build_source = "remote-connect-reply" if isinstance(remote_build, str) and remote_build else "NOT_REPORTED"
                if (connection.get("remote_engine_build_source") != expected_build_source
                        or (remote_build is None and terminal_replies[-1].get("engine_build") is not None)
                        or (remote_build is not None and terminal_replies[-1].get("engine_build") != remote_build)
                        or terminal_replies[-1].get("server") != (
                            f"{connection['endpoint']['host']}:{connection['endpoint']['port']}"
                        )
                        or terminal_replies[-1].get("engine_version") != connection.get("remote_engine_version")):
                    return False
                if source_operation == "session.connect":
                    if terminal_replies[0].get("worker_epoch") != original_binding.get("worker_epoch"):
                        return False
                else:
                    detach_required = reconnect_baseline.get("detach_required")
                    if (connection.get("reconnect_detach_required") is not detach_required
                            or (detach_required and (
                                terminal_replies[0].get("connected") is not False
                                or terminal_replies[0].get("worker_epoch") <= original_binding.get("worker_epoch")
                                or terminal_replies[-1].get("worker_epoch") <= terminal_replies[0].get("worker_epoch")
                            ))
                            or (not detach_required and terminal_replies[-1].get("worker_epoch") <= original_binding.get("worker_epoch"))
                            or connection.get("observed_peer") != reconnect_baseline.get("observed_peer")
                            or (isinstance(reconnect_baseline.get("remote_engine_version"), str)
                                and reconnect_baseline.get("remote_engine_version")
                                != connection.get("remote_engine_version"))
                            or (isinstance(reconnect_baseline.get("remote_engine_build"), str)
                                and reconnect_baseline.get("remote_engine_build")
                                and reconnect_baseline.get("remote_engine_build")
                                != connection.get("remote_engine_build"))):
                        return False
            else:
                reply = terminal_replies[0] if terminal_replies else None
                if (source_operation != "session.disconnect" or not isinstance(reply, Mapping)
                        or reply.get("connected") is not False
                        or reply.get("worker_epoch") <= original_binding.get("worker_epoch")
                        or reply.get("worker_epoch") != target_record.get("worker_epoch")
                        or reply.get("worker_instance_id") != target_record.get("worker_instance_id")):
                    return False
                exited = classification == "DISCONNECT_TERMINAL_WORKER_EXITED_NO_RETIREMENT_PROOF"
                if exited:
                    if (target_record.get("state") != "UNKNOWN"
                            or target_record.get("client_state") != "UNKNOWN"
                            or worker_observation.get("state") != "EXITED_EXACT_UNREAPED"
                            or type(worker_observation.get("pid")) is not int
                            or worker_observation.get("pid") <= 1
                            or type(worker_observation.get("start_epoch_ms")) is not int
                            or worker_observation.get("start_epoch_ms") <= 0):
                        return False
                elif (classification != "DISCONNECTED_EXACT_WORKER_EPOCH"
                      or worker_observation.get("state") != "LIVE_EXACT"
                      or worker_observation.get("runtime_pid_matches") is not True
                      or worker_observation.get("process_birth_matches") is not True
                      or type(worker_observation.get("pid")) is not int
                      or worker_observation.get("pid") <= 1
                      or type(worker_observation.get("start_epoch_ms")) is not int
                      or worker_observation.get("start_epoch_ms") <= 0
                      or target_record.get("state") != "DISCONNECTED"
                      or target_record.get("client_state") != "DISCONNECTED"
                      or worker_observation.get("instance_id") != reply.get("worker_instance_id")
                      or worker_observation.get("generation") != reply.get("worker_epoch")
                      or worker_observation.get("connected") is not False):
                    return False
        elif scope == "SESSION_WORKER_RETIRED_EXACT":
            close_hash = proof.get("worker_close_event_sha256")
            reaped_hash = proof.get("worker_close_reaped_event_sha256")
            try:
                events = self._session_job_events(job_id)
                close_events = [item for item in events if item.get("event") == "WorkerCloseStarted"]
                reaped_events = [item for item in events if item.get("event") == "WorkerCloseReaped"]
            except Exception:
                return False
            if (not retire_requested or classification != "EXACT_WORKER_RETIRED"
                    or len(close_events) != 1 or not isinstance(close_events[0].get("metadata"), Mapping)
                    or len(reaped_events) != 1 or not isinstance(reaped_events[0].get("metadata"), Mapping)
                    or not isinstance(close_hash, str) or len(close_hash) != 64
                    or not isinstance(reaped_hash, str) or len(reaped_hash) != 64
                    or session_recovery_evidence_sha256({
                        "worker_close_started": dict(close_events[0]["metadata"]),
                    }) != close_hash
                    or session_recovery_evidence_sha256({
                        "worker_close_reaped": dict(reaped_events[0]["metadata"]),
                    }) != reaped_hash):
                return False
            close = close_events[0]["metadata"]
            reaped = reaped_events[0]["metadata"]
            if (close.get("operation_id") != operation_id
                    or close.get("project_id") != source_project
                    or close.get("session_id") != source_session
                    or close.get("worker_close_started") is not True
                    or close.get("pre_close_client_state") != "DISCONNECTED"
                    or close.get("pre_close_worker_epoch") != close.get("worker_epoch")
                    or close.get("worker_instance_id") != original_binding.get("worker_instance_id")
                    or target_record.get("state") != "DISCONNECTED"
                    or target_record.get("client_state") != "RETIRED"
                    or target_record.get("worker_instance_id") != close.get("worker_instance_id")
                    or target_record.get("worker_epoch") != close.get("worker_epoch")
                    or target_record.get("server_instance_id") is not None
                    or worker_observation.get("state") != "EXITED_EXACT"
                    or worker_observation.get("wait_confirmed") is not True
                    or worker_observation.get("exact_popen_handle") is not True
                    or worker_observation.get("wait_evidence_source") != "persisted_worker_close_reaped_event"
                    or worker_observation.get("pid") != close.get("pid")
                    or worker_observation.get("start_epoch_ms") != close.get("start_epoch_ms")
                    or reaped.get("operation_id") != operation_id
                    or reaped.get("project_id") != source_project
                    or reaped.get("session_id") != source_session
                    or reaped.get("worker_instance_id") != close.get("worker_instance_id")
                    or reaped.get("worker_epoch") != close.get("worker_epoch")
                    or reaped.get("pid") != close.get("pid")
                    or reaped.get("start_epoch_ms") != close.get("start_epoch_ms")
                    or reaped.get("worker_close_started_sha256") != close_hash
                    or reaped.get("wait_confirmed") is not True
                    or reaped.get("exact_popen_handle") is not True
                    or type(reaped.get("exit_code")) is not int
                    or reaped.get("wait_returncode") != reaped.get("exit_code")
                    or worker_observation.get("exit_code") != reaped.get("exit_code")
                    or worker_observation.get("wait_returncode") != reaped.get("wait_returncode")):
                return False
            if close.get("disconnect_rpc_dispatched") is True:
                reply = terminal_replies[0] if len(terminal_replies) == 1 else None
                if (not isinstance(reply, Mapping) or reply.get("kind") != "disconnect"
                        or reply.get("request_id") != close.get("disconnect_request_id")
                        or reply.get("worker_epoch") != close.get("worker_epoch")
                        or reply.get("reply_sha256") != close.get("disconnect_reply_sha256")):
                    return False
            elif (close.get("disconnect_rpc_dispatched") is not False
                  or terminal_replies or request_observations
                  or close.get("disconnect_request_id") is not None
                  or close.get("disconnect_reply_sha256") is not None
                  or close.get("worker_epoch") != original_binding.get("worker_epoch")):
                return False
        else:
            return False
        return bool(
            isinstance(target_record, Mapping)
            and isinstance(transition, Mapping)
            and isinstance(pointer, Mapping)
            and pointer.get("event_id") == event.get("id")
            and pointer.get("evidence_sha256") == supplied_digest
            and pointer.get("project_id") == source_project
            and pointer.get("session_id") == source_session
            and pointer.get("lifecycle_revision") == target_record.get("revision")
            and proof.get("schema_version") == 1
            and proof.get("source_job_id") == job_id
            and proof.get("source_operation_id") == operation_id
            and proof.get("source_operation") == source_operation
            and proof.get("source_status") == job.get("status")
            and proof.get("source_operation_status") == operation.get("status")
            and proof.get("source_result_sha256") == result_digest
            and proof.get("source_status") in {"UNKNOWN", "RECONCILING"}
            and scope in {"SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE", "SESSION_WORKER_RETIRED_EXACT"}
            and proof.get("replay_performed") is False
            and proof.get("new_worker_created") is False
            and isinstance(worker_observation, Mapping)
            and binding_matches
            and target_record.get("project_id") == source_project
            and target_record.get("session_id") == source_session
            and transition.get("from_state") == "UNKNOWN"
            and type(transition.get("from_revision")) is int
            and type(transition.get("to_revision")) is int
            and transition.get("from_revision") + 1 == transition.get("to_revision")
            and target_record.get("revision") == transition.get("to_revision")
            and proof.get("project_id") == source_project
            and proof.get("session_id") == source_session
        )

    def _job_quiescence_proven(self, job: Mapping[str, Any]) -> bool:
        metadata = job.get("metadata") if isinstance(job, Mapping) else None
        binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
        lifecycle_resolution = self._session_lifecycle_recovery_resolution_is_valid(job)
        if isinstance(binding, Mapping) and binding.get("kind") == "registered_session":
            # `job.reconcile` readback alone does not prove model revision or
            # original Worker epoch. Session-bound UNKNOWN jobs require the
            # stronger append-only `session.recover` event.
            return self._session_recovery_resolution_is_valid(job) or lifecycle_resolution
        operation = job.get("operation") if isinstance(job, Mapping) else None
        if isinstance(operation, Mapping) and operation.get("operation") in {
            "session.connect", "session.disconnect", "session.reconnect",
        }:
            return lifecycle_resolution
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
        terminal_replies: dict[str, dict[str, Any]] = {}
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
                if terminal:
                    terminal_replies[request_id] = dict(reply)
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
                "reason": None,
                "_terminal_replies": terminal_replies}

    def _session_job_observed_replies(
        self, job: Mapping[str, Any], submissions: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Read already-recorded Worker replies without contacting a dead child."""
        job_id, operation_id = job.get("job_id"), job.get("operation_id")
        if not isinstance(job_id, str) or not isinstance(operation_id, str):
            return {}, "SOURCE_JOB_IDENTITY_MALFORMED"
        submitted_by_id = {item["request_id"]: item for item in submissions}
        observed: dict[str, dict[str, Any]] = {}
        for event in self._session_job_events(job_id):
            if event.get("event") != "worker_request":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, Mapping) or metadata.get("phase") != "observed":
                continue
            request_id = metadata.get("request_id")
            if metadata.get("operation_id") != operation_id or request_id not in submitted_by_id:
                return {}, "OBSERVED_WORKER_REPLY_SOURCE_MISMATCH"
            submitted = submitted_by_id[request_id]
            if (metadata.get("kind") != submitted.get("kind")
                    or (submitted.get("request_hash") is not None
                        and metadata.get("request_hash") != submitted.get("request_hash"))):
                return {}, "OBSERVED_WORKER_REPLY_BINDING_MISMATCH"
            reply = metadata.get("reply")
            if not isinstance(reply, Mapping):
                return {}, "OBSERVED_WORKER_REPLY_MISSING"
            status = reply.get("status")
            if (reply.get("request_id") != request_id
                    or reply.get("type") != submitted.get("kind")
                    or status not in {"SUCCEEDED", "FAILED"}
                    or (status == "SUCCEEDED" and "result" not in reply)
                    or (status == "FAILED" and not isinstance(reply.get("failure"), Mapping))):
                return {}, "OBSERVED_WORKER_REPLY_NOT_TERMINAL_OR_MISMATCHED"
            previous = observed.get(request_id)
            candidate = dict(reply)
            if previous is not None and previous != candidate:
                return {}, "OBSERVED_WORKER_REPLY_CONFLICT"
            observed[request_id] = candidate
        return observed, None

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
            "operation": record.get("operation"),
            "project_id": project_id,
            "session_id": session_id,
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

    def _session_worker_process_observation(
        self, key: tuple[str, str], worker: Any, runtime_metadata: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], Any | None]:
        """Classify only the retained Worker handle; never attach, wait, or terminate it."""
        if worker is None:
            return {"state": "MISSING_OR_UNVERIFIABLE"}, None
        saved = self._session_worker_child_identities.get(key)
        if not isinstance(saved, Mapping) or saved.get("worker") is not worker:
            if isinstance(runtime_metadata, Mapping) and isinstance(runtime_metadata.get("instance_id"), str):
                return {
                    "state": "LIVE_WORKER_HEALTH_ONLY",
                    "instance_id": runtime_metadata.get("instance_id"),
                    "generation": runtime_metadata.get("generation"),
                    "remote_engine_health_claim": False,
                    "process_birth": "UNAVAILABLE",
                }, None
            return {"state": "MISSING_OR_UNVERIFIABLE"}, None
        process, pid, birth = saved.get("process"), saved.get("pid"), saved.get("start_epoch_ms")
        if (process is None or type(pid) is not int or pid <= 1
                or type(birth) is not int or birth <= 0
                or getattr(process, "pid", None) != pid
                or not callable(getattr(process, "poll", None))
                or not callable(getattr(process, "wait", None))):
            return {"state": "MISSING_OR_UNVERIFIABLE"}, None
        if getattr(worker, "_process", None) not in (None, process):
            return {"state": "MISSING_OR_UNVERIFIABLE", "reason": "WORKER_POPEN_HANDLE_CHANGED"}, process
        try:
            returncode = process.poll()
        except Exception as exc:
            return {"state": "MISSING_OR_UNVERIFIABLE",
                    "process_error_type": type(exc).__name__}, process
        if returncode is None:
            if getattr(worker, "_process", None) is not process:
                return {"state": "MISSING_OR_UNVERIFIABLE", "reason": "LIVE_POPEN_NOT_RETAINED"}, process
            try:
                identity = process_identity(pid)
            except Exception as exc:
                identity = {"alive": True, "start_epoch_ms": None,
                            "error_type": type(exc).__name__}
            birth_matches = (
                isinstance(identity, Mapping) and identity.get("alive") is True
                and identity.get("start_epoch_ms") == birth
            )
            metadata_matches = (
                isinstance(runtime_metadata, Mapping)
                and type(runtime_metadata.get("pid")) is int
                and runtime_metadata["pid"] > 1
                and runtime_metadata["pid"] == pid
            )
            return {
                "state": "LIVE_EXACT" if birth_matches and metadata_matches else "LIVE_IDENTITY_UNCONFIRMED",
                "pid": pid, "start_epoch_ms": birth,
                "runtime_pid_matches": metadata_matches,
                "process_birth_matches": birth_matches,
                "instance_id": (runtime_metadata.get("instance_id")
                                if isinstance(runtime_metadata, Mapping) else None),
                "generation": (runtime_metadata.get("generation")
                               if isinstance(runtime_metadata, Mapping) else None),
                "connected": (runtime_metadata.get("connected")
                              if isinstance(runtime_metadata, Mapping) else None),
                "remote_engine_health_claim": False,
            }, process
        return {
            "state": "EXITED_EXACT_UNREAPED",
            "pid": pid, "start_epoch_ms": birth,
            "exit_code": returncode, "poll_returncode": returncode,
            "wait_confirmed": False,
            "remote_engine_health_claim": False,
        }, process

    def _session_worker_close_started(self, job: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        job_id, operation_id = job.get("job_id"), job.get("operation_id")
        if not isinstance(job_id, str) or not isinstance(operation_id, str):
            return None, "SOURCE_JOB_IDENTITY_MALFORMED"
        matches = []
        for event in self._session_job_events(job_id):
            if event.get("event") != "WorkerCloseStarted":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, Mapping):
                return None, "WORKER_CLOSE_EVENT_MALFORMED"
            if metadata.get("operation_id") != operation_id:
                return None, "WORKER_CLOSE_EVENT_OPERATION_MISMATCH"
            matches.append(dict(metadata))
        if len(matches) > 1:
            return None, "WORKER_CLOSE_EVENT_DUPLICATE"
        return (matches[0], None) if matches else (None, "WORKER_CLOSE_START_NOT_RECORDED")

    def _session_worker_close_reaped(self, job: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        job_id, operation_id = job.get("job_id"), job.get("operation_id")
        if not isinstance(job_id, str) or not isinstance(operation_id, str):
            return None, "SOURCE_JOB_IDENTITY_MALFORMED"
        matches = []
        for event in self._session_job_events(job_id):
            if event.get("event") != "WorkerCloseReaped":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, Mapping):
                return None, "WORKER_CLOSE_REAP_EVENT_MALFORMED"
            if metadata.get("operation_id") != operation_id:
                return None, "WORKER_CLOSE_REAP_EVENT_OPERATION_MISMATCH"
            matches.append(dict(metadata))
        if len(matches) > 1:
            return None, "WORKER_CLOSE_REAP_EVENT_DUPLICATE"
        return (matches[0], None) if matches else (None, "WORKER_CLOSE_REAP_EVIDENCE_NOT_RECORDED")

    def _session_lifecycle_request_readback(
        self, worker: Any, job: Mapping[str, Any], recovery_record: Mapping[str, Any],
        *, timeout: float, process_exited: bool,
    ) -> dict[str, Any]:
        """Read only the exact lifecycle request, using saved terminal events if the child exited."""
        submissions, reason = self._session_job_worker_submissions(job)
        if reason is not None:
            return {"requests": [], "terminal": False, "reason": reason, "_terminal_replies": {}}
        if process_exited:
            replies, reason = self._session_job_observed_replies(job, submissions)
            if reason is not None:
                return {"requests": [], "terminal": False, "reason": reason, "_terminal_replies": {}}
            observations = []
            for submitted in submissions:
                reply = replies.get(submitted["request_id"])
                terminal = bool(
                    isinstance(reply, Mapping)
                    and reply.get("request_id") == submitted["request_id"]
                    and reply.get("type") == submitted.get("kind")
                    and reply.get("status") in {"SUCCEEDED", "FAILED"}
                    and ((reply.get("status") == "SUCCEEDED" and "result" in reply)
                         or (reply.get("status") == "FAILED" and isinstance(reply.get("failure"), Mapping)))
                )
                safe_reply = self._redact_session_worker_event(dict(reply)) if isinstance(reply, Mapping) else {}
                observations.append({
                    "request_id": submitted["request_id"],
                    "kind": submitted.get("kind"),
                    "source_operation_id": submitted.get("source_operation_id"),
                    "status": reply.get("status") if isinstance(reply, Mapping) else "UNKNOWN",
                    "terminal": terminal,
                    "request_id_match": bool(isinstance(reply, Mapping)
                                             and reply.get("request_id") == submitted["request_id"]),
                    "request_type_match": bool(isinstance(reply, Mapping)
                                               and reply.get("type") == submitted.get("kind")),
                    "reply_sha256": session_recovery_evidence_sha256({"worker_reply": safe_reply}),
                    "operation_id_source": "source_job_event",
                    "reply_source": "persisted_worker_observed_event",
                })
            return {
                "requests": observations,
                "terminal": bool(observations) and all(item["terminal"] for item in observations),
                "reason": None,
                "_terminal_replies": replies,
            }
        return self._read_session_job_requests(worker, job, recovery_record, timeout=timeout)

    @staticmethod
    def _session_lifecycle_original_binding(job: Mapping[str, Any]) -> dict[str, Any] | None:
        metadata = job.get("metadata")
        binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
        if (not isinstance(binding, Mapping)
                or binding.get("kind") != "registered_session"
                or not isinstance(binding.get("project_id"), str)
                or not isinstance(binding.get("session_id"), str)
                or not isinstance(binding.get("worker_instance_id"), str)
                or type(binding.get("worker_epoch")) is not int
                or binding.get("worker_epoch") < 1):
            return None
        return {key: binding.get(key) for key in (
            "kind", "project_id", "session_id", "worker_instance_id", "worker_epoch",
        )}

    def _session_lifecycle_recovery_candidate(
        self, *, job: Mapping[str, Any], recovery_record: Mapping[str, Any],
        lifecycle: Mapping[str, Any], worker: Any, context: SessionRuntimeContext | None,
        runtime_metadata: Mapping[str, Any] | None,
        process_observation: Mapping[str, Any], timeout: float,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str, dict[str, Any]]:
        """Build a source-bound lifecycle proof without replaying or retiring anything."""
        job_id, source_operation_id = job.get("job_id"), job.get("operation_id")
        operation_row = job.get("operation")
        metadata = job.get("metadata")
        if (not isinstance(job_id, str) or not isinstance(source_operation_id, str)
                or not isinstance(operation_row, Mapping) or not isinstance(metadata, Mapping)):
            return None, None, "SOURCE_JOB_IDENTITY_MALFORMED", {}
        operation = operation_row.get("operation")
        if operation not in {"session.connect", "session.disconnect", "session.reconnect"}:
            return None, None, "NOT_A_SUPPORTED_LIFECYCLE_OPERATION", {}
        if (job.get("status") not in {"UNKNOWN", "RECONCILING"}
                or operation_row.get("status") not in {"UNKNOWN", "RECONCILING"}
                or lifecycle.get("state") != "UNKNOWN"):
            return None, None, "SOURCE_OR_SESSION_IS_NOT_UNKNOWN", {}
        original_binding = self._session_lifecycle_original_binding(job)
        project_id, session_id = metadata.get("project_id"), metadata.get("session_id")
        if (original_binding is None or original_binding.get("project_id") != project_id
                or original_binding.get("session_id") != session_id
                or project_id != lifecycle.get("project_id")
                or session_id != lifecycle.get("session_id")
                or worker is None):
            return None, None, "ORIGINAL_WORKER_BINDING_OR_HANDLE_UNAVAILABLE", {}
        if self._session_worker_handles.get((project_id, session_id)) is not worker:
            return None, None, "ORIGINAL_WORKER_HANDLE_MISMATCH", {}

        process_state = process_observation.get("state")
        worker_observation = {
            key: process_observation.get(key) for key in (
                "state", "pid", "start_epoch_ms", "exit_code", "poll_returncode",
                "wait_returncode", "wait_confirmed", "runtime_pid_matches",
                "process_birth_matches", "process_birth", "instance_id", "generation",
                "connected", "remote_engine_health_claim",
            ) if key in process_observation
        }
        worker_observation["remote_engine_health_claim"] = False
        if isinstance(runtime_metadata, Mapping):
            worker_observation.update({key: runtime_metadata.get(key) for key in (
                "instance_id", "generation", "connected", "server",
            )})
            worker_observation["worker_health_scope"] = "CURRENT_WORKER_IDENTITY_ONLY"
        else:
            worker_observation["worker_health_scope"] = "UNAVAILABLE"

        arguments = metadata.get("arguments") if isinstance(metadata.get("arguments"), Mapping) else {}
        retire_requested = operation == "session.disconnect" and arguments.get("retire_worker") is True
        close_event = None
        request_readback: dict[str, Any] = {"requests": [], "terminal": False, "reason": None,
                                            "_terminal_replies": {}}
        close_event_digest = None
        classification = ""
        resolution_scope = "SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE"
        target_state = "UNKNOWN"
        target_client_state = "UNKNOWN"
        target_worker_id = original_binding["worker_instance_id"]
        target_worker_epoch: int | None = original_binding["worker_epoch"]
        target_server_id: str | None = None
        connection_observation: dict[str, Any] | None = None
        reconnect_baseline: dict[str, Any] | None = None
        terminal_reply_identities: list[dict[str, Any]] = []
        close_reaped_event_digest = None

        if retire_requested:
            close_event, close_error = self._session_worker_close_started(job)
            if close_error is not None or not isinstance(close_event, Mapping):
                return None, None, close_error or "WORKER_CLOSE_EVENT_UNAVAILABLE", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            if (close_event.get("operation_id") != source_operation_id
                    or close_event.get("project_id") != project_id
                    or close_event.get("session_id") != session_id
                    or close_event.get("worker_close_started") is not True
                    or close_event.get("pre_close_client_state") != "DISCONNECTED"
                    or close_event.get("pre_close_worker_epoch") != close_event.get("worker_epoch")
                    or close_event.get("worker_instance_id") != original_binding.get("worker_instance_id")):
                return None, None, "WORKER_CLOSE_EVENT_BINDING_MISMATCH", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            if (worker_observation.get("pid") != close_event.get("pid")
                    or worker_observation.get("start_epoch_ms") != close_event.get("start_epoch_ms")):
                return None, None, "WORKER_CLOSE_EVENT_POPEN_BIRTH_MISMATCH", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            reaped_event, reaped_error = self._session_worker_close_reaped(job)
            if reaped_error is not None or not isinstance(reaped_event, Mapping):
                return None, None, reaped_error or "WORKER_CLOSE_REAP_EVIDENCE_UNAVAILABLE", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            close_started_hash = session_recovery_evidence_sha256({
                "worker_close_started": close_event,
            })
            if (reaped_event.get("operation_id") != source_operation_id
                    or reaped_event.get("project_id") != project_id
                    or reaped_event.get("session_id") != session_id
                    or reaped_event.get("worker_instance_id") != close_event.get("worker_instance_id")
                    or reaped_event.get("worker_epoch") != close_event.get("worker_epoch")
                    or reaped_event.get("pid") != close_event.get("pid")
                    or reaped_event.get("start_epoch_ms") != close_event.get("start_epoch_ms")
                    or reaped_event.get("worker_close_started_sha256") != close_started_hash
                    or reaped_event.get("wait_confirmed") is not True
                    or reaped_event.get("exact_popen_handle") is not True
                    or type(reaped_event.get("exit_code")) is not int
                    or reaped_event.get("wait_returncode") != reaped_event.get("exit_code")):
                return None, None, "WORKER_CLOSE_REAP_EVENT_BINDING_OR_WAIT_MISMATCH", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            process_observation, process = self._session_worker_process_observation(
                (project_id, session_id), worker, runtime_metadata,
            )
            worker_observation = {
                key: process_observation.get(key) for key in (
                    "state", "pid", "start_epoch_ms", "poll_returncode", "runtime_pid_matches",
                    "process_birth_matches", "instance_id", "generation", "connected",
                    "remote_engine_health_claim",
                ) if key in process_observation
            }
            worker_observation["remote_engine_health_claim"] = False
            if (process is None or process_observation.get("state") != "EXITED_EXACT_UNREAPED"
                    or process_observation.get("pid") != close_event.get("pid")
                    or process_observation.get("start_epoch_ms") != close_event.get("start_epoch_ms")
                    or process_observation.get("exit_code") != reaped_event.get("exit_code")
                    or getattr(process, "pid", None) != reaped_event.get("pid")
                    or (getattr(worker, "_process", None) is not None
                        and getattr(worker, "_process", None) is not process)
                    or type(close_event.get("worker_epoch")) is not int
                    or close_event.get("worker_epoch") < 1):
                return None, None, "EXACT_WORKER_CLOSE_EXIT_AND_REAP_NOT_CONFIRMED", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            worker_observation.update({
                "state": "EXITED_EXACT",
                "exit_code": reaped_event["exit_code"],
                "wait_returncode": reaped_event["wait_returncode"],
                "wait_confirmed": True,
                "exact_popen_handle": True,
                "wait_evidence_source": "persisted_worker_close_reaped_event",
            })
            submissions, submit_error = self._session_job_worker_submissions(job)
            if close_event.get("disconnect_rpc_dispatched") is True:
                request_readback = self._session_lifecycle_request_readback(
                    worker, job, recovery_record, timeout=timeout, process_exited=True,
                )
                replies = request_readback.get("_terminal_replies", {})
                if (submit_error is not None or len(submissions) != 1
                        or submissions[0].get("kind") != "disconnect"
                        or not request_readback.get("terminal") is True):
                    return None, None, request_readback.get("reason") or "DISCONNECT_TERMINAL_REPLY_REQUIRED_FOR_RETIREMENT", {
                        "classification": "RETIREMENT_NOT_PROVEN",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                request_id = submissions[0]["request_id"]
                wrapper = replies.get(request_id)
                reply = wrapper.get("result") if isinstance(wrapper, Mapping) else None
                if (not isinstance(wrapper, Mapping) or wrapper.get("status") != "SUCCEEDED"
                        or not isinstance(reply, Mapping)
                        or reply.get("connected") is not False
                        or reply.get("instance_id") != close_event.get("worker_instance_id")
                        or reply.get("generation") != close_event.get("worker_epoch")
                        or close_event.get("disconnect_request_id") != request_id
                        or session_recovery_evidence_sha256({"disconnect_reply": dict(reply)})
                           != close_event.get("disconnect_reply_sha256")):
                    return None, None, "DISCONNECT_REPLY_CLOSE_EVENT_BINDING_MISMATCH", {
                        "classification": "RETIREMENT_NOT_PROVEN",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                terminal_reply_identities = [{
                    "request_id": request_id,
                    "kind": "disconnect",
                    "status": "SUCCEEDED",
                    "connected": False,
                    "worker_instance_id": reply.get("instance_id"),
                    "worker_epoch": reply.get("generation"),
                    "reply_sha256": session_recovery_evidence_sha256({
                        "disconnect_reply": self._redact_session_worker_event(dict(reply)),
                    }),
                }]
            elif close_event.get("disconnect_rpc_dispatched") is False:
                # A normal disconnect followed by a separate Worker retirement
                # has no RPC submitted by this retirement operation. The
                # submission reader represents that exact zero-event case with
                # ORIGINAL_WORKER_REQUESTS_UNAVAILABLE; every other reader error
                # and every submitted event remains a hard binding failure.
                if (submissions
                        or submit_error != "ORIGINAL_WORKER_REQUESTS_UNAVAILABLE"):
                    return None, None, "UNEXPECTED_WORKER_RPC_FOR_PREDETACHED_RETIREMENT", {
                        "classification": "RETIREMENT_NOT_PROVEN",
                        "worker_observation": worker_observation,
                    }
                if close_event.get("disconnect_request_id") is not None or close_event.get("disconnect_reply_sha256") is not None:
                    return None, None, "PREDISCONNECTED_RETIREMENT_HAS_RPC_BINDING", {
                        "classification": "RETIREMENT_NOT_PROVEN",
                        "worker_observation": worker_observation,
                    }
            else:
                return None, None, "WORKER_CLOSE_EVENT_DISPATCH_FLAG_INVALID", {
                    "classification": "RETIREMENT_NOT_PROVEN",
                    "worker_observation": worker_observation,
                }
            target_state, target_client_state = "DISCONNECTED", "RETIRED"
            target_worker_epoch = close_event["worker_epoch"]
            classification = "EXACT_WORKER_RETIRED"
            resolution_scope = "SESSION_WORKER_RETIRED_EXACT"
            close_event_digest = session_recovery_evidence_sha256({"worker_close_started": close_event})
            close_reaped_event_digest = session_recovery_evidence_sha256({
                "worker_close_reaped": reaped_event,
            })
        else:
            process_exited = str(process_state or "").startswith("EXITED_")
            if (not process_exited and process_state != "LIVE_EXACT"):
                return None, None, "LIVE_WORKER_POPEN_PID_AND_BIRTH_NOT_EXACT", {
                    "classification": "WORKER_PROCESS_IDENTITY_UNCONFIRMED",
                    "worker_observation": worker_observation,
                }
            if process_exited and operation in {"session.connect", "session.reconnect"}:
                return None, None, "CONNECT_OR_RECONNECT_WORKER_EXITED_WITHOUT_LIVE_IDENTITY_PROOF", {
                    "classification": "WORKER_EXITED_DURING_LIFECYCLE_RECOVERY",
                    "worker_observation": worker_observation,
                }
            if operation == "session.reconnect":
                baseline_events = [event for event in self._session_job_events(job_id)
                                   if event.get("event") == "SessionReconnectBaseline"]
                if len(baseline_events) != 1 or not isinstance(baseline_events[0].get("metadata"), Mapping):
                    return None, None, "RECONNECT_BASELINE_MISSING_OR_DUPLICATE", {
                        "classification": "RECONNECT_BASELINE_INVALID",
                        "worker_observation": worker_observation,
                    }
                reconnect_baseline = dict(baseline_events[0]["metadata"])
                event_rows = self._session_job_events(job_id)
                baseline_id = baseline_events[0].get("id")
                if any(event.get("event") == "worker_request"
                       and isinstance(event.get("metadata"), Mapping)
                       and event["metadata"].get("phase") == "submitted"
                       and event.get("id", 0) < baseline_id for event in event_rows):
                    return None, None, "RECONNECT_BASELINE_WAS_NOT_PRE_RPC", {
                        "classification": "RECONNECT_BASELINE_INVALID",
                        "worker_observation": worker_observation,
                    }
                if (reconnect_baseline.get("operation_id") != source_operation_id
                        or reconnect_baseline.get("project_id") != project_id
                        or reconnect_baseline.get("session_id") != session_id
                        or reconnect_baseline.get("worker_instance_id") != original_binding["worker_instance_id"]
                        or reconnect_baseline.get("worker_epoch") != original_binding["worker_epoch"]
                        or not isinstance(reconnect_baseline.get("observed_peer"), Mapping)
                        or reconnect_baseline.get("endpoint") != lifecycle.get("endpoint")
                        or type(reconnect_baseline.get("detach_required")) is not bool):
                    return None, None, "RECONNECT_BASELINE_SOURCE_OR_PEER_MISMATCH", {
                        "classification": "RECONNECT_BASELINE_INVALID",
                        "worker_observation": worker_observation,
                    }
            expected_kinds = {
                "session.connect": ["connect"],
                "session.disconnect": ["disconnect"],
                "session.reconnect": (["disconnect", "connect"]
                                       if (isinstance(reconnect_baseline, Mapping)
                                           and reconnect_baseline.get("detach_required")) else ["connect"]),
            }[operation]
            request_readback = self._session_lifecycle_request_readback(
                worker, job, recovery_record, timeout=timeout, process_exited=process_exited,
            )
            if not request_readback.get("terminal"):
                return None, None, request_readback.get("reason") or "ORIGINAL_LIFECYCLE_REQUEST_NOT_TERMINAL", {
                    "classification": "LIFECYCLE_REQUEST_UNRESOLVED",
                    "request_observations": request_readback.get("requests", []),
                    "worker_observation": worker_observation,
                }
            if not process_exited:
                try:
                    post_readback_metadata = worker.runtime_metadata()
                except Exception as exc:
                    return None, None, f"WORKER_IDENTITY_READBACK_FAILED:{type(exc).__name__}", {
                        "classification": "WORKER_IDENTITY_CHANGED_DURING_LIFECYCLE_RECOVERY",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                post_process_observation, _post_process = self._session_worker_process_observation(
                    (project_id, session_id), worker, post_readback_metadata,
                )
                if (not isinstance(post_readback_metadata, Mapping)
                        or post_process_observation.get("state") != "LIVE_EXACT"
                        or not isinstance(runtime_metadata, Mapping)
                        or post_readback_metadata.get("instance_id") != runtime_metadata.get("instance_id")
                        or post_readback_metadata.get("generation") != runtime_metadata.get("generation")):
                    return None, None, "WORKER_POPEN_OR_EPOCH_CHANGED_DURING_LIFECYCLE_READBACK", {
                        "classification": "WORKER_IDENTITY_CHANGED_DURING_LIFECYCLE_RECOVERY",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": post_process_observation,
                    }
                worker_observation.update({key: post_process_observation.get(key) for key in (
                    "state", "pid", "start_epoch_ms", "runtime_pid_matches",
                    "process_birth_matches", "instance_id", "generation", "connected",
                ) if key in post_process_observation})
            submissions, submit_error = self._session_job_worker_submissions(job)
            if (submit_error is not None or [item.get("kind") for item in submissions] != expected_kinds):
                return None, None, submit_error or "LIFECYCLE_REQUEST_SEQUENCE_MISMATCH", {
                    "classification": "LIFECYCLE_REQUEST_SEQUENCE_INVALID",
                    "request_observations": request_readback.get("requests", []),
                    "worker_observation": worker_observation,
                }
            replies = request_readback.get("_terminal_replies", {})
            request_ids = [item["request_id"] for item in submissions]
            decoded: list[dict[str, Any]] = []
            for index, (submitted, expected_kind) in enumerate(zip(submissions, expected_kinds)):
                wrapper = replies.get(submitted["request_id"])
                result = wrapper.get("result") if isinstance(wrapper, Mapping) else None
                if (not isinstance(wrapper, Mapping) or wrapper.get("request_id") != submitted["request_id"]
                        or wrapper.get("type") != expected_kind or wrapper.get("status") != "SUCCEEDED"
                        or not isinstance(result, Mapping)):
                    return None, None, "ORIGINAL_LIFECYCLE_REPLY_NOT_EXACT_SUCCESS", {
                        "classification": "LIFECYCLE_REQUEST_REPLY_INVALID",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                decoded.append(dict(result))
            terminal_reply_identities = []
            for submitted, expected_kind, reply_result in zip(submissions, expected_kinds, decoded):
                identity = {
                    "request_id": submitted["request_id"],
                    "kind": expected_kind,
                    "status": "SUCCEEDED",
                    "connected": reply_result.get("connected"),
                    "worker_instance_id": reply_result.get("instance_id"),
                    "worker_epoch": reply_result.get("generation"),
                    "reply_sha256": session_recovery_evidence_sha256({
                        f"{expected_kind}_reply": self._redact_session_worker_event(reply_result),
                    }),
                }
                for field in ("server", "engine_version", "engine_build"):
                    if field in reply_result:
                        identity[field] = reply_result.get(field)
                terminal_reply_identities.append(identity)

            if operation == "session.disconnect":
                reply = decoded[0]
                if (reply.get("connected") is not False
                        or reply.get("instance_id") != original_binding["worker_instance_id"]
                        or type(reply.get("generation")) is not int
                        or reply.get("generation") <= original_binding["worker_epoch"]):
                    return None, None, "DISCONNECT_REPLY_IDENTITY_OR_EPOCH_MISMATCH", {
                        "classification": "DISCONNECT_NOT_CONFIRMED",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                if process_exited:
                    classification = "DISCONNECT_TERMINAL_WORKER_EXITED_NO_RETIREMENT_PROOF"
                    target_state, target_client_state = "UNKNOWN", "UNKNOWN"
                    target_worker_epoch = reply["generation"]
                    target_server_id = None
                else:
                    if (not isinstance(runtime_metadata, Mapping)
                            or runtime_metadata.get("instance_id") != reply.get("instance_id")
                            or runtime_metadata.get("generation") != reply.get("generation")
                            or runtime_metadata.get("connected") is not False):
                        return None, None, "CURRENT_WORKER_DOES_NOT_CONFIRM_DISCONNECTED_EPOCH", {
                            "classification": "DISCONNECT_CURRENT_WORKER_MISMATCH",
                            "request_observations": request_readback.get("requests", []),
                            "worker_observation": worker_observation,
                        }
                    if context is not None and context.worker is worker and context.client_connected is True:
                        return None, None, "STALE_CONNECTED_CONTEXT_CONFLICTS_WITH_DISCONNECT", {
                            "classification": "DISCONNECT_CONTEXT_CONFLICT",
                            "request_observations": request_readback.get("requests", []),
                            "worker_observation": worker_observation,
                        }
                    classification = "DISCONNECTED_EXACT_WORKER_EPOCH"
                    target_state, target_client_state = "DISCONNECTED", "DISCONNECTED"
                    target_worker_epoch = reply["generation"]
                    target_server_id = None
            else:
                expected_endpoint = metadata.get("endpoint")
                if operation == "session.reconnect":
                    baseline = reconnect_baseline
                    expected_endpoint = baseline.get("endpoint")
                    if baseline.get("detach_required"):
                        detach_reply, connect_reply = decoded
                        if (detach_reply.get("connected") is not False
                                or detach_reply.get("instance_id") != original_binding["worker_instance_id"]
                                or type(detach_reply.get("generation")) is not int
                                or detach_reply.get("generation") <= original_binding["worker_epoch"]
                                or connect_reply.get("connected") is not True
                                or connect_reply.get("instance_id") != original_binding["worker_instance_id"]
                                or type(connect_reply.get("generation")) is not int
                                or connect_reply.get("generation") <= detach_reply["generation"]):
                            return None, None, "RECONNECT_REPLY_EPOCH_SEQUENCE_MISMATCH", {
                                "classification": "RECONNECT_SEQUENCE_INVALID",
                                "request_observations": request_readback.get("requests", []),
                                "worker_observation": worker_observation,
                            }
                        connect_reply_selected = connect_reply
                        detached_epoch = detach_reply["generation"]
                    else:
                        connect_reply_selected = decoded[0]
                        if (connect_reply_selected.get("connected") is not True
                                or connect_reply_selected.get("instance_id") != original_binding["worker_instance_id"]
                                or type(connect_reply_selected.get("generation")) is not int
                                or connect_reply_selected.get("generation") <= original_binding["worker_epoch"]):
                            return None, None, "RECONNECT_REPLY_EPOCH_SEQUENCE_MISMATCH", {
                                "classification": "RECONNECT_SEQUENCE_INVALID",
                                "request_observations": request_readback.get("requests", []),
                                "worker_observation": worker_observation,
                            }
                    prior_peer = baseline.get("observed_peer")
                    prior_version = baseline.get("remote_engine_version")
                    prior_build = baseline.get("remote_engine_build")
                else:
                    if not isinstance(expected_endpoint, Mapping):
                        expected_endpoint = metadata.get("arguments", {}).get("endpoint") if isinstance(metadata.get("arguments"), Mapping) else None
                    connect_reply_selected = decoded[0]
                    prior_peer = None
                    prior_version = None
                    prior_build = None

                reply = connect_reply_selected
                endpoint = expected_endpoint
                if (not isinstance(endpoint, Mapping)
                        or not isinstance(endpoint.get("host"), str)
                        or type(endpoint.get("port")) is not int
                        or not 1 <= endpoint["port"] <= 65535
                        or reply.get("connected") is not True
                        or reply.get("server") != f"{endpoint['host']}:{endpoint['port']}"
                        or reply.get("instance_id") != original_binding["worker_instance_id"]
                        or type(reply.get("generation")) is not int
                        or not isinstance(reply.get("engine_version"), str)
                        or not reply.get("engine_version")):
                    return None, None, "CONNECT_REPLY_REMOTE_IDENTITY_INCOMPLETE", {
                        "classification": "ATTACHED_BUT_CONTEXT_UNAVAILABLE",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                if operation == "session.connect" and reply.get("generation") != original_binding["worker_epoch"]:
                    return None, None, "CONNECT_REPLY_DOES_NOT_MATCH_BOUND_WORKER_EPOCH", {
                        "classification": "ATTACHED_BUT_CONTEXT_UNAVAILABLE",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                if (not isinstance(runtime_metadata, Mapping)
                        or runtime_metadata.get("instance_id") != reply.get("instance_id")
                        or runtime_metadata.get("generation") != reply.get("generation")
                        or runtime_metadata.get("connected") is not True
                        or runtime_metadata.get("server") != reply.get("server")):
                    return None, None, "CURRENT_WORKER_DOES_NOT_CONFIRM_CONNECT_EPOCH", {
                        "classification": "ATTACHED_BUT_CONTEXT_UNAVAILABLE",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                if (context is None or context.worker is not worker
                        or context.worker_instance_id != reply.get("instance_id")
                        or context.worker_epoch != reply.get("generation")
                        or context.client_connected is not True
                        or context.connected_host != endpoint.get("host")
                        or context.connected_port != endpoint.get("port")
                        or context.endpoint.host != endpoint.get("host")
                        or context.endpoint.port != endpoint.get("port")
                        or not isinstance(context.endpoint.observed_peer, CanonicalSocket)):
                    return None, None, "ATTACHED_BUT_CONTEXT_UNAVAILABLE", {
                        "classification": "ATTACHED_BUT_CONTEXT_UNAVAILABLE",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                peer = context.endpoint.observed_peer
                if (peer.port != endpoint["port"]
                        or (isinstance(prior_peer, Mapping)
                            and (peer.address != prior_peer.get("address")
                                 or peer.port != prior_peer.get("port")))
                        or (isinstance(prior_version, str)
                            and reply.get("engine_version") != prior_version)
                        or (isinstance(prior_build, str) and prior_build.strip()
                            and reply.get("engine_build") != prior_build)):
                    return None, None, "REMOTE_PEER_OR_VERSION_CHANGED_SINCE_BASELINE", {
                        "classification": "REMOTE_CONNECTION_IDENTITY_MISMATCH",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                backend_identity = getattr(context.backend, "worker_identity", None)
                try:
                    from ._managed_backend import ManagedBackend
                    computed_server_id = ManagedBackend._session_server_instance_id(peer, reply)
                except Exception:
                    computed_server_id = None
                remote_build = reply.get("engine_build")
                remote_build = remote_build.strip() if isinstance(remote_build, str) and remote_build.strip() else None
                if (not isinstance(backend_identity, Mapping)
                        or backend_identity.get("worker_instance_id") != reply.get("instance_id")
                        or backend_identity.get("connection_epoch") != reply.get("generation")
                        or backend_identity.get("server_instance_id") != computed_server_id
                        or backend_identity.get("remote_engine_version") != reply.get("engine_version")
                        or backend_identity.get("remote_engine_build") != remote_build
                        or backend_identity.get("remote_engine_build_source") != (
                            "remote-connect-reply" if remote_build is not None else "NOT_REPORTED")):
                    return None, None, "REMOTE_CONNECT_IDENTITY_NOT_BOUND_TO_CONTEXT_BACKEND", {
                        "classification": "ATTACHED_BUT_CONTEXT_UNAVAILABLE",
                        "request_observations": request_readback.get("requests", []),
                        "worker_observation": worker_observation,
                    }
                connection_observation = {
                    "endpoint": dict(endpoint),
                    "observed_peer": {"address": peer.address, "port": peer.port},
                    "worker_instance_id": reply["instance_id"],
                    "worker_epoch": reply["generation"],
                    "remote_engine_version": reply["engine_version"],
                    "remote_engine_build": remote_build,
                    "remote_engine_build_source": "remote-connect-reply" if remote_build is not None else "NOT_REPORTED",
                    "connect_reply_sha256": terminal_reply_identities[-1]["reply_sha256"],
                    "reconnect_detach_required": (
                        reconnect_baseline.get("detach_required")
                        if isinstance(reconnect_baseline, Mapping) else None
                    ),
                    "server_instance_id": computed_server_id,
                    "remote_health_claim": False,
                }
                target_state, target_client_state = "CONNECTED", "CONNECTED"
                target_worker_epoch = reply["generation"]
                target_server_id = computed_server_id
                classification = "CONNECTED_EXACT_REPLY_WORKER_AND_PEER"

        result = job.get("result")
        source_result = dict(result) if isinstance(result, Mapping) else None
        if source_result is None:
            return None, None, "SOURCE_UNKNOWN_RESULT_UNAVAILABLE", {
                "classification": classification or "LIFECYCLE_REQUEST_UNRESOLVED",
                "request_observations": request_readback.get("requests", []),
                "worker_observation": worker_observation,
            }
        error = source_result.get("error") if isinstance(source_result.get("error"), Mapping) else {}
        safe_retry = error.get("safe_retry") if type(error.get("safe_retry")) is bool else False
        target = self._updated_session_lifecycle(
            lifecycle, state=target_state, client_state=target_client_state,
            worker_instance_id=target_worker_id, worker_epoch=target_worker_epoch,
            server_instance_id=target_server_id,
            health={"status": "UNKNOWN", "observed_at": None, "source": None},
        )
        target["revision"] = lifecycle["revision"] + 1
        evidence = {
            "schema_version": 1,
            "source_job_id": job_id,
            "source_operation_id": source_operation_id,
            "session_recovery_operation_id": recovery_record["operation_id"],
            "project_id": project_id,
            "session_id": session_id,
            "source_operation": operation,
            "source_status": job.get("status"),
            "source_operation_status": operation_row.get("status"),
            "source_result_sha256": session_recovery_evidence_sha256({"source_result": source_result}),
            "original_unknown_reason": {
                "code": error.get("code") if isinstance(error.get("code"), str) else "UNKNOWN",
                "safe_retry": safe_retry,
                "cause_type": ((source_result.get("data") or {}).get("cause_type")
                               if isinstance(source_result.get("data"), Mapping)
                               and isinstance(source_result["data"].get("cause_type"), str) else None),
            },
            "original_worker_binding": original_binding,
            "reconnect_baseline": reconnect_baseline,
            "worker_observation": worker_observation,
            "request_observations": list(request_readback.get("requests", [])),
            "terminal_reply_identities": terminal_reply_identities,
            "resolution_scope": resolution_scope,
            "classification": classification,
            "connection_observation": connection_observation,
            "worker_close_event_sha256": close_event_digest,
            "worker_close_reaped_event_sha256": close_reaped_event_digest,
            "replay_performed": False,
            "new_worker_created": False,
            "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
        }
        return evidence, target, "", {
            "classification": classification,
            "request_observations": request_readback.get("requests", []),
            "worker_observation": worker_observation,
            "resolution_scope": resolution_scope,
        }

    @contextmanager
    def _session_worker_recovery_admission_guard(self, worker: Any, epochs: set[int]):
        """Fence every known epoch for one retained Worker while recovery reads history."""
        if worker is None or not epochs:
            yield {"admission_fenced": False, "accepted_task_count": None}
            return
        with ExitStack() as stack:
            admissions = [
                stack.enter_context(self.session_scheduler.worker_recovery_admission_guard(worker, epoch))
                for epoch in sorted(epochs)
            ]
            yield {
                "admission_fenced": bool(admissions)
                    and all(item.get("admission_fenced") is True for item in admissions),
                "accepted_task_count": sum(item.get("accepted_task_count", 0) for item in admissions),
                "guarded_epochs": sorted(epochs),
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
                                            disconnect_rpc_dispatched: bool,
                                            disconnect_reply: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
            if disconnect_rpc_dispatched and not isinstance(disconnect_reply, Mapping):
                raise RuntimeError("disconnect RPC reply is unavailable for Worker retirement audit")
            close_started = {
                "operation_id": record.get("operation_id"),
                "project_id": project_id,
                "session_id": session_id,
                "worker_instance_id": proof["worker_instance_id"],
                "worker_epoch": proof["worker_epoch"],
                "pid": pid,
                "start_epoch_ms": birth,
                "disconnect_rpc_dispatched": disconnect_rpc_dispatched,
                "disconnect_request_id": (
                    f"{record['operation_id']}:disconnect" if disconnect_rpc_dispatched else None
                ),
                "disconnect_reply_sha256": (
                    session_recovery_evidence_sha256({"disconnect_reply": dict(disconnect_reply)})
                    if disconnect_rpc_dispatched else None
                ),
                "pre_close_client_state": "DISCONNECTED",
                "pre_close_worker_epoch": proof["worker_epoch"],
                "worker_close_started": True,
            }
            # This immutable event precedes the only close call. Recovery may
            # verify an already-exited exact child from it, but never retries
            # close or treats a missing event as evidence that close began.
            self.store.add_event(record["job_id"], "WorkerCloseStarted", close_started)
            cleanup_started = True
            worker.close()
            # close() owns graceful/forced termination policy; this adapter
            # independently waits on the saved Popen object and verifies that
            # exact child, rather than trusting a return from close().
            waited = process.wait(timeout=5.0)
            polled = process.poll()
            if type(polled) is not int:
                raise RuntimeError("exact Worker child remained live after close/wait")
            if type(waited) is not int or waited != polled:
                raise RuntimeError("exact Worker child wait and poll exit codes disagree")
            if getattr(worker, "_process", None) is not None and getattr(worker, "_process", None) is not process:
                raise RuntimeError("Worker handle changed to a different child during retirement")
            close_reaped = {
                "operation_id": record.get("operation_id"),
                "project_id": project_id,
                "session_id": session_id,
                "worker_instance_id": proof["worker_instance_id"],
                "worker_epoch": proof["worker_epoch"],
                "pid": pid,
                "start_epoch_ms": birth,
                "worker_close_started_sha256": session_recovery_evidence_sha256({
                    "worker_close_started": close_started,
                }),
                "exit_code": polled,
                "wait_returncode": waited,
                "wait_confirmed": True,
                "exact_popen_handle": True,
            }
            # Persist the original close path's successful exact wait before
            # the lifecycle CAS. Recovery is read-only and may only consume
            # this evidence; it never calls wait() to manufacture it later.
            self.store.add_event(record["job_id"], "WorkerCloseReaped", close_reaped)
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
                        disconnect_reply=reply,
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
            old_peer = context.endpoint.observed_peer if context is not None else None
            if old_peer is None:
                raw_peer = old_cached.get("observed_peer")
                if isinstance(raw_peer, Mapping):
                    try:
                        old_peer = CanonicalSocket(raw_peer.get("address"), raw_peer.get("port"))
                    except Exception:
                        old_peer = None
            old_version = old_backend_identity.get("remote_engine_version") or old_cached.get("remote_engine_version")
            old_build = old_backend_identity.get("remote_engine_build")
            if not isinstance(old_build, str) or not old_build.strip():
                old_build = old_cached.get("remote_engine_build")
            child_identity = self._session_worker_child_identities.get(key)
            baseline = {
                "operation_id": record["operation_id"],
                "project_id": project_id,
                "session_id": session_id,
                "worker_instance_id": lifecycle.get("worker_instance_id"),
                "worker_epoch": lifecycle.get("worker_epoch"),
                "server_instance_id": lifecycle.get("server_instance_id"),
                "server_ownership": lifecycle.get("server_ownership"),
                "prior_lifecycle_state": lifecycle.get("state"),
                "detach_required": lifecycle.get("state") == "CONNECTED",
                "endpoint": {"host": host, "port": port},
                "observed_peer": ({"address": old_peer.address, "port": old_peer.port}
                                  if isinstance(old_peer, CanonicalSocket) else None),
                "remote_engine_version": old_version if isinstance(old_version, str) else None,
                "remote_engine_build": old_build if isinstance(old_build, str) and old_build.strip() else None,
                "remote_engine_build_source": old_backend_identity.get(
                    "remote_engine_build_source", old_cached.get("remote_engine_build_source", "NOT_REPORTED")
                ),
                "worker_process_identity": ({
                    "pid": child_identity.get("pid"),
                    "start_epoch_ms": child_identity.get("start_epoch_ms"),
                } if isinstance(child_identity, Mapping) and child_identity.get("worker") is worker else None),
                "baseline_recorded_before_worker_rpc": True,
            }
            # Recovery may later need to compare a reconnect's peer and remote
            # version with the detached connection. Persist only opaque worker
            # identity and observed endpoint facts, never credentials.
            self.store.add_event(record["job_id"], "SessionReconnectBaseline", baseline)
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
                            and (self._session_recovery_resolution_is_valid(job)
                                 or self._session_lifecycle_recovery_resolution_is_valid(job)))
                    )
                ]
                unresolved_items: list[dict[str, Any]] = []
                resolved_jobs: list[dict[str, Any]] = []
                observations: list[dict[str, Any]] = []
                runtime_metadata = None
                runtime_error = None
                process_observation: dict[str, Any] = {"state": "MISSING_OR_UNVERIFIABLE"}
                guard_epochs: set[int] = set()
                if context is not None and context.worker is worker and type(context.worker_epoch) is int:
                    guard_epochs.add(context.worker_epoch)
                if type(lifecycle.get("worker_epoch")) is int and lifecycle["worker_epoch"] > 0:
                    guard_epochs.add(lifecycle["worker_epoch"])
                for job in unresolved:
                    binding = (job.get("metadata", {}).get("runtime_binding")
                               if isinstance(job.get("metadata"), Mapping) else None)
                    epoch = binding.get("worker_epoch") if isinstance(binding, Mapping) else None
                    if type(epoch) is int and epoch > 0:
                        guard_epochs.add(epoch)
                if worker is not None and not guard_epochs:
                    unresolved_items.append({
                        "kind": "SESSION_WORKER", "status": "UNKNOWN",
                        "reason": "ORIGINAL_WORKER_EPOCH_UNAVAILABLE",
                    })
                guard_context = self._session_worker_recovery_admission_guard(worker, guard_epochs)
                admission_fenced = False
                exact_instance = False

                try:
                    with guard_context as admission:
                        admission_fenced = bool(
                            worker is not None and guard_epochs
                            and isinstance(admission, Mapping)
                            and admission.get("admission_fenced") is True
                        )
                        if admission_fenced:
                            # No health/status RPC runs until all known Worker
                            # epochs have been fenced against accepted work.
                            try:
                                runtime_metadata = worker.runtime_metadata()
                            except Exception as exc:
                                runtime_error = type(exc).__name__
                            process_observation, _process = self._session_worker_process_observation(
                                (project_id, session_id), worker, runtime_metadata,
                            )
                            expected_instances = {
                                lifecycle.get("worker_instance_id"),
                                *(binding.get("worker_instance_id") for job in unresolved
                                  if isinstance((binding := (job.get("metadata", {}).get("runtime_binding")
                                                              if isinstance(job.get("metadata"), Mapping) else None)), Mapping)),
                            }
                            expected_instances.discard(None)
                            exact_instance = bool(
                                isinstance(runtime_metadata, Mapping)
                                and runtime_metadata.get("instance_id") in expected_instances
                            )
                            stable_generation = (runtime_metadata.get("generation")
                                                 if isinstance(runtime_metadata, Mapping) else None)
                            if worker is None:
                                unresolved_items.append({
                                    "kind": "SESSION_WORKER", "status": "UNKNOWN",
                                    "reason": "ORIGINAL_WORKER_HANDLE_UNAVAILABLE",
                                })
                            elif not exact_instance and process_observation.get("state") not in {
                                "EXITED_EXACT_UNREAPED", "EXITED_EXACT",
                            }:
                                unresolved_items.append({
                                    "kind": "SESSION_WORKER", "status": "UNKNOWN",
                                    "reason": "ORIGINAL_WORKER_IDENTITY_UNCONFIRMED",
                                    "runtime_readback_error": runtime_error,
                                })
                            for job in unresolved:
                                job_id = job.get("job_id")
                                job_meta = job.get("metadata")
                                job_binding = job_meta.get("runtime_binding") if isinstance(job_meta, Mapping) else None
                                job_execution = job_meta.get("execution") if isinstance(job_meta, Mapping) else None
                                source_model_ref = job_execution.get("model_ref") if isinstance(job_execution, Mapping) else None
                                source_operation_row = job.get("operation")
                                source_operation_name = (source_operation_row.get("operation")
                                                         if isinstance(source_operation_row, Mapping) else None)
                                if self._session_lifecycle_recovery_resolution_is_valid(job):
                                    prior = self.store.session_lifecycle_recovery_resolution(job_id)
                                    prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                    resolved_jobs.append({
                                        "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                        "quiescence_resolution": "ALREADY_PROVEN",
                                        "resolution_scope": prior_meta.get("resolution_scope"),
                                        "classification": prior_meta.get("classification"),
                                        "evidence_sha256": prior_meta.get("evidence_sha256"),
                                        "replayed": False,
                                    })
                                    continue
                                if source_operation_name in {
                                    "session.connect", "session.disconnect", "session.reconnect",
                                }:
                                    if self.store.session_lifecycle_recovery_resolution(job_id) is not None:
                                        unresolved_items.append({
                                            "kind": "UNKNOWN_JOB", "job_id": job_id,
                                            "historical_status": job.get("status"),
                                            "reason": "EXISTING_LIFECYCLE_PROOF_INVALID",
                                        })
                                        continue
                                    evidence, target_lifecycle, reason, diagnostic = (
                                        self._session_lifecycle_recovery_candidate(
                                            job=job, recovery_record=record, lifecycle=lifecycle,
                                            worker=worker, context=context,
                                            runtime_metadata=(runtime_metadata if isinstance(runtime_metadata, Mapping) else None),
                                            process_observation=process_observation,
                                            timeout=self._timeouts(execution)["rpc_timeout_s"],
                                        )
                                    )
                                    observations.append({
                                        "job_id": job_id,
                                        "operation_id": job.get("operation_id"),
                                        "historical_status": job.get("status"),
                                        "classification": diagnostic.get("classification"),
                                        "worker_observation": diagnostic.get("worker_observation"),
                                        "worker_requests": diagnostic.get("request_observations", []),
                                        "resolution_scope": diagnostic.get("resolution_scope"),
                                        "reason": reason or None,
                                    })
                                    if isinstance(diagnostic.get("worker_observation"), Mapping):
                                        process_observation = dict(diagnostic["worker_observation"])
                                    if evidence is not None and target_lifecycle is not None:
                                        recorded = self.store.record_session_lifecycle_recovery_resolution(
                                            job_id, job["operation_id"], lifecycle["revision"],
                                            target_lifecycle, evidence,
                                        )
                                        if recorded.get("recorded") is True:
                                            source_now = self.store.job(job_id)
                                            if source_now and self._session_lifecycle_recovery_resolution_is_valid(source_now):
                                                lifecycle = target_lifecycle
                                                prior = recorded.get("resolution") or {}
                                                prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                                resolved_jobs.append({
                                                    "job_id": job_id,
                                                    "historical_status": job.get("status"),
                                                    "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                                    "quiescence_resolution": "PROVEN_AND_AUDITED",
                                                    "resolution_scope": prior_meta.get("resolution_scope"),
                                                    "classification": prior_meta.get("classification"),
                                                    "evidence_sha256": prior_meta.get("evidence_sha256"),
                                                    "lifecycle_state_after": target_lifecycle.get("state"),
                                                    "historical_unknown_preserved": True,
                                                    "replayed": False,
                                                })
                                                if prior_meta.get("resolution_scope") == "SESSION_WORKER_RETIRED_EXACT":
                                                    self._session_worker_handles.pop((project_id, session_id), None)
                                                    self._session_worker_child_identities.pop((project_id, session_id), None)
                                            else:
                                                reason = "RECORDED_LIFECYCLE_PROOF_FAILED_VALIDATION"
                                        elif recorded.get("reason") == "ALREADY_RESOLVED":
                                            source_now = self.store.job(job_id)
                                            if source_now and self._session_lifecycle_recovery_resolution_is_valid(source_now):
                                                prior = recorded.get("resolution") or {}
                                                prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                                resolved_jobs.append({
                                                    "job_id": job_id,
                                                    "historical_status": job.get("status"),
                                                    "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                                    "quiescence_resolution": "ALREADY_PROVEN",
                                                    "resolution_scope": prior_meta.get("resolution_scope"),
                                                    "classification": prior_meta.get("classification"),
                                                    "evidence_sha256": prior_meta.get("evidence_sha256"),
                                                    "replayed": False,
                                                })
                                            else:
                                                reason = "EXISTING_LIFECYCLE_PROOF_INVALID"
                                        else:
                                            reason = recorded.get("reason") or "LIFECYCLE_RESOLUTION_NOT_RECORDED"
                                    if not any(item.get("job_id") == job_id for item in resolved_jobs):
                                        unresolved_items.append({
                                            "kind": "UNKNOWN_JOB", "job_id": job_id,
                                            "historical_status": job.get("status"),
                                            "reason": reason or "LIFECYCLE_EVIDENCE_INCOMPLETE",
                                            "classification": diagnostic.get("classification"),
                                        })
                                    continue
                                if not exact_instance:
                                    unresolved_items.append({
                                        "kind": "UNKNOWN_JOB", "job_id": job_id,
                                        "historical_status": job.get("status"),
                                        "reason": "ORIGINAL_WORKER_IDENTITY_UNCONFIRMED",
                                    })
                                    continue
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
                                if self._session_recovery_resolution_is_valid(job):
                                    prior = self.store.session_recovery_resolution(job.get("job_id"))
                                    prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                    resolved_jobs.append({
                                        "job_id": job.get("job_id"),
                                        "historical_status": job.get("status"),
                                        "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                        "quiescence_resolution": "ALREADY_PROVEN",
                                        "evidence_sha256": prior_meta.get("evidence_sha256"),
                                        "replayed": False,
                                    })
                                    continue
                                if self._session_lifecycle_recovery_resolution_is_valid(job):
                                    prior = self.store.session_lifecycle_recovery_resolution(job.get("job_id"))
                                    prior_meta = prior.get("metadata", {}) if isinstance(prior, Mapping) else {}
                                    resolved_jobs.append({
                                        "job_id": job.get("job_id"),
                                        "historical_status": job.get("status"),
                                        "outcome_resolution": "UNVERIFIED_HISTORICAL_UNKNOWN",
                                        "quiescence_resolution": "ALREADY_PROVEN",
                                        "resolution_scope": prior_meta.get("resolution_scope"),
                                        "classification": prior_meta.get("classification"),
                                        "evidence_sha256": prior_meta.get("evidence_sha256"),
                                        "replayed": False,
                                    })
                                    continue
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
                        "reason": "LIFECYCLE_STATE_REMAINS_UNKNOWN",
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
                    "worker_process_observation": dict(process_observation),
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
