"""Durable job control with one serial engine queue and responsive cached reads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from ._operation_store import IdempotencyConflict, JobCleanupError, OperationStore
from ._platform_process import process_identity, terminate_process_tree
from ._desktop_platforms import create_native_metadata_adapter
from ._desktop_service import DesktopCoordinator, DesktopOperationError
from ._project_authority import ProjectAuthority, PROJECT_OPERATIONS
from ._session_context import (
    CanonicalSocket,
    SessionEndpointIdentity,
    SessionContextMissing,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    session_state_directory,
    SessionEndpointScheduler,
    SessionRuntimeRegistry,
    SessionSchedulerClosed,
    active_session_context,
    use_session_context,
)
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
                 session_peer_observer=None, session_credentials_resolver=None):
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
        self._session_connect_lock = threading.RLock()
        self._session_worker_handles: dict[tuple[str, str], Any] = {}
        self._session_backends: dict[tuple[str, str], ManagedBackend] = {}
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

    def _dispatch_session_connect(self, routed: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        from ._execution_contract import canonical_request_hash

        project_id = routed["project_id"]
        idempotency_key = routed["idempotency_key"]
        request_id = routed.get("request_id") or execution.get("request_id") or str(uuid4())
        if not isinstance(request_id, str) or not request_id:
            raise ExecutionContractError("INVALID_REQUEST", "session.connect request_id must be a non-empty string")
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
        session_id = "session-" + uuid4().hex
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
            active = [row for row in self.session_lifecycle.list_for_project(project_id)
                      if row.get("endpoint") == {"host": host, "port": port}
                      and row.get("state") in {"CONNECTING", "CONNECTED", "UNKNOWN", "STOPPING"}]
            if active:
                result = self._error("SESSION_ALREADY_ACTIVE", "an active or uncertain session already targets this endpoint",
                                     data={"session_id": session_id, "existing_session_id": active[0]["session_id"],
                                           "engine_dispatched": False}, safe_retry=False)
                self.store.update_job(record["job_id"], "RUNNING")
                return self._finish(record, result, "FAILED")

            self.store.update_job(record["job_id"], "RUNNING", {"session_id": session_id, "engine_dispatched": False})
            self.store.add_event(record["job_id"], "RUNNING", {"operation_id": record["operation_id"], "session_id": session_id})
            lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                project_id=project_id, session_id=session_id, state="CONNECTING",
                runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                client_state="DISCONNECTED", server_state="SHARED", server_ownership="shared",
            ))
            backend = None
            worker = None
            attached = None
            dispatched = False
            try:
                self.project_authority.authorize_operation(project_id, "project_write")
                runtime = self._resolve_session_runtime(routed["runtime_id"], project_id, session_id, project_root)
                credentials = {}
                if credentials_ref is not None:
                    if self.session_credentials_resolver is None:
                        raise ExecutionContractError("AUTHORIZATION_REQUIRED", "credentials_ref has no trusted local resolver")
                    try:
                        credentials = self.session_credentials_resolver(credentials_ref)
                    except Exception as exc:
                        raise ExecutionContractError("AUTHORIZATION_REQUIRED", "trusted credentials reference could not be resolved") from exc
                    if not isinstance(credentials, Mapping) or set(credentials) - {"user", "password"}:
                        raise ExecutionContractError("AUTHORIZATION_REQUIRED", "trusted credentials resolver returned an invalid secret record")
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
                )
                dispatched = True
                worker = attached["worker"]
                self._session_worker_handles[(project_id, session_id)] = worker
                peer = attached["peer"]
                reply = attached["reply"]
                identity = attached["worker_identity"]
                endpoint_identity = SessionEndpointIdentity(
                    host, port, reply["generation"], observed_peer=peer, owned_process=None,
                )
                context = SessionRuntimeContext(
                    project_id=project_id, session_id=session_id, project_root=project_root,
                    runtime=runtime, endpoint=endpoint_identity, backend=backend,
                    worker_instance_id=reply["instance_id"], worker=worker,
                    service=backend.service, client=worker.client(),
                    remote_client_factory=worker.client, server_ownership="shared",
                    client_connected=True, connected_host=host, connected_port=port,
                    server_started_by_mcp=False,
                    health_snapshot={"status": "HEALTHY", "source": "worker-connect-reply+observed-peer"},
                )
                self.session_registry.register(context)
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="CONNECTED",
                    runtime_id=runtime.runtime_id, endpoint={"host": host, "port": port},
                    client_state="CONNECTED", server_state="SHARED", server_ownership="shared",
                    worker_instance_id=reply["instance_id"], worker_epoch=reply["generation"],
                    server_instance_id=attached["server_instance_id"],
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
                    "server_ownership": "shared", "credentials_configured": credentials_ref is not None,
                }}
                return self._finish(record, result, "SUCCEEDED")
            except SessionConnectFailure as exc:
                worker = exc.worker or worker
                dispatched = dispatched or exc.dispatched
                if worker is not None:
                    self._session_worker_handles[(project_id, session_id)] = worker
                runtime_meta = exc.runtime_metadata or {}
                remote_reply = exc.reply or {}
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
                    lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                        project_id=project_id, session_id=session_id, state=state,
                        runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                        client_state=client_state, server_state="UNKNOWN", server_ownership="shared",
                        worker_instance_id=worker_id if isinstance(worker_id, str) else None,
                        worker_epoch=epoch if type(epoch) is int and epoch > 0 else None,
                        server_instance_id=server_id,
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

                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="DISCONNECTED",
                    runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                    client_state="DISCONNECTED", server_state="UNKNOWN", server_ownership="shared",
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
                    lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                        project_id=project_id, session_id=session_id, state="UNKNOWN",
                        runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                        client_state="CONNECTED", server_state="UNKNOWN", server_ownership="shared",
                        worker_instance_id=reply.get("instance_id"), worker_epoch=reply.get("generation"),
                        server_instance_id=attached.get("server_instance_id"),
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
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="DISCONNECTED",
                    runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                    client_state="DISCONNECTED", server_state="UNKNOWN", server_ownership="shared",
                ), expected_revision=lifecycle["revision"])
                result = self._exception(exc)
                return self._finish(record, result, "FAILED")
            except Exception as exc:
                if worker is not None:
                    self._session_worker_handles[(project_id, session_id)] = worker
                if dispatched:
                    lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                        project_id=project_id, session_id=session_id, state="UNKNOWN",
                        runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                        client_state="UNKNOWN", server_state="UNKNOWN", server_ownership="shared",
                    ), expected_revision=lifecycle["revision"])
                    result = self._error("EXECUTION_STATE_UNKNOWN", "session connect completed without durable identity confirmation",
                                         data={"session_id": session_id, "engine_dispatched": True,
                                               "worker_handle_preserved": worker is not None},
                                         execution_state_unknown=True)
                    return self._finish(record, result, "UNKNOWN")
                self._log_exception()
                lifecycle = self.session_lifecycle.save(new_lifecycle_record(
                    project_id=project_id, session_id=session_id, state="DISCONNECTED",
                    runtime_id=routed["runtime_id"], endpoint={"host": host, "port": port},
                    client_state="DISCONNECTED", server_state="UNKNOWN", server_ownership="shared",
                ), expected_revision=lifecycle["revision"])
                result = self._error("RUNTIME_CONFIGURATION_REQUIRED", "session Worker configuration failed before connect dispatch",
                                     data={"session_id": session_id, "engine_dispatched": False},
                                     cause_type=type(exc).__name__, safe_retry=True)
                return self._finish(record, result, "FAILED")

    def _dispatch_session_control(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        """Handle the implemented durable session snapshots without Worker RPC.

        Lifecycle reads are deliberately control-plane only: they cannot wait
        behind a solve or turn cached evidence into a live-health claim. The
        mutating lifecycle routes stay unadvertised until their runtime and
        exact process adapters are installed.
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
        unresolved = [job for job in self.store.unresolved_jobs() if job["job_id"] != job_id and job["status"] in {"UNKNOWN", "RECONCILING"} and not job["metadata"].get("reconciled_quiescent")]
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

    def close(self):
        with self._admission_lock:
            self._worker_retirement_state = "CLOSED"
            self.closed.set()
        self.session_scheduler.close()
        self.queue.shutdown(wait=True)
        self.monitor.join(timeout=2)
        self.store.close()
        if hasattr(self, "backend") and hasattr(self.backend, "close"):
            try:
                self.backend.close()
            except Exception:
                pass


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
