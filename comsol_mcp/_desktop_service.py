"""Fail-closed Desktop coordination over trusted platform and engine adapters.

This module owns the operation lifecycle and identity checks.  Platform adapters
are explicit constructor dependencies; no fixture or simulated provider is ever
selected implicitly.  Native implementations may report metadata while leaving
controls unsupported until their exact behavior is validated on a real desktop.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from uuid import uuid4

from ._execution_contract import ModelRef, canonical_request_hash, model_ref_from_mapping
from ._operation_store import IdempotencyConflict, OperationStore, TERMINAL


AVAILABLE = "AVAILABLE"
BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
UNSUPPORTED_CONTROL = "UNSUPPORTED_CONTROL"
WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
MODEL_BINDING_UNKNOWN = "MODEL_BINDING_UNKNOWN"

_HOST_CONTROL_OPS = frozenset({
    "desktop.bind", "desktop.show_model", "desktop.select_node", "desktop.capture",
    "desktop.action", "desktop.migrate_standalone",
})
_ALL_OPS = _HOST_CONTROL_OPS | {"desktop.status", "desktop.shell_execute"}
_REQUIRED_ARGUMENTS: dict[str, frozenset[str]] = {
    "desktop.status": frozenset({"project_id"}),
    "desktop.bind": frozenset({"project_id", "idempotency_key", "window_ref", "model_ref"}),
    "desktop.show_model": frozenset({"project_id", "idempotency_key", "window_ref", "model_ref"}),
    "desktop.select_node": frozenset({"project_id", "idempotency_key", "window_ref", "path"}),
    "desktop.capture": frozenset({"project_id", "idempotency_key", "window_ref", "region"}),
    "desktop.action": frozenset({"project_id", "idempotency_key", "window_ref", "action"}),
    "desktop.shell_execute": frozenset({"project_id", "idempotency_key", "window_ref", "source_artifact", "expected_model_ref"}),
    "desktop.migrate_standalone": frozenset({"project_id", "idempotency_key", "window_ref", "target_session_id", "save_policy"}),
}
_ALLOWED_ARGUMENTS: dict[str, frozenset[str]] = {
    "desktop.status": frozenset({"project_id", "request_id", "runtime_id"}),
    "desktop.bind": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "model_ref", "verification"}),
    "desktop.show_model": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "model_ref"}),
    "desktop.select_node": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "path"}),
    "desktop.capture": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "region"}),
    "desktop.action": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "action"}),
    "desktop.shell_execute": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "source_artifact", "expected_model_ref"}),
    "desktop.migrate_standalone": frozenset({"project_id", "idempotency_key", "request_id", "window_ref", "target_session_id", "save_policy"}),
}
_GLOBAL_CAPTURE_REGIONS = frozenset({"desktop", "entire_desktop", "screen", "all_screens"})
_OWNER_EVENT = "DesktopOperationClaimed"


class DesktopOperationError(RuntimeError):
    """Structured failure whose uncertainty is explicit at the raise site."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = UNSUPPORTED_CONTROL,
        side_effect_started: bool = False,
        safe_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.side_effect_started = side_effect_started
        self.safe_retry = safe_retry


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    """Observed OS identity.  Display title and file path are not identity."""

    platform: str
    native_window_id: str
    process_id: int
    process_birth: str
    login_id: str
    desktop_session_id: str
    comsol_version: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v for v in (
            self.platform, self.native_window_id, self.process_birth, self.login_id,
            self.desktop_session_id, self.comsol_version,
        )):
            raise ValueError("window identity requires platform, handle, process birth, login/session, and COMSOL version")
        if isinstance(self.process_id, bool) or not isinstance(self.process_id, int) or self.process_id <= 0:
            raise ValueError("window identity process_id must be positive")

    def fingerprint(self) -> str:
        stable = {
            "platform": self.platform,
            "native_window_id": self.native_window_id,
            "process_id": self.process_id,
            "process_birth": self.process_birth,
            "login_id": self.login_id,
            "desktop_session_id": self.desktop_session_id,
            "comsol_version": self.comsol_version,
        }
        raw = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()

    def same_process_window(self, other: "WindowIdentity") -> bool:
        return self == other


@dataclass(frozen=True, slots=True)
class WindowObservation:
    identity: WindowIdentity
    title: str = ""
    control_status: str = UNSUPPORTED_CONTROL


@dataclass(frozen=True, slots=True)
class DesktopModelObservation:
    mode: str
    server_endpoint: str | None = None
    model_tag: str | None = None
    standalone_model_id: str | None = None
    fingerprint: str | None = None
    dirty: bool | None = None


@dataclass(frozen=True, slots=True)
class ManagedModelSnapshot:
    model_ref: ModelRef
    server_endpoint: str
    model_tag: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SavedCopy:
    path: str
    sha256: str
    source_preserved: bool
    source_dirty_after: bool
    source_fingerprint_after: str


@dataclass(frozen=True, slots=True)
class DesktopActionPlan:
    """A trusted resolver's bounded interpretation of the catalog action."""

    action_id: str
    mutates_model: bool
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TrustedSourceArtifact:
    artifact_id: str
    sha256: str
    size: int
    provenance: str


@dataclass(frozen=True, slots=True)
class SourcePreservation:
    still_open: bool
    standalone_model_id: str
    fingerprint: str
    dirty: bool


@dataclass(frozen=True, slots=True)
class DesktopDispatchContext:
    """Store-issued parent claim supplied to every managed queue callback."""

    operation: str
    request_id: str
    operation_id: str
    job_id: str
    idempotency_key: str
    request_hash: str
    project_id: str


class DesktopAdapter(Protocol):
    """Host adapter.  Every identity comes from an actual platform observation."""

    def status(self, runtime_id: str | None = None) -> Mapping[str, Any]: ...
    def enumerate_windows(self, runtime_id: str | None = None) -> Sequence[WindowObservation]: ...
    def revalidate_window(self, identity: WindowIdentity) -> WindowObservation | None: ...
    def observe_model(self, identity: WindowIdentity) -> DesktopModelObservation: ...
    def show_model(self, identity: WindowIdentity, snapshot: ManagedModelSnapshot) -> Mapping[str, Any]: ...
    def select_node(self, identity: WindowIdentity, path: Any) -> Mapping[str, Any]: ...
    def capture(self, identity: WindowIdentity, region: str) -> Mapping[str, Any]: ...
    def perform_action(self, identity: WindowIdentity, plan: DesktopActionPlan) -> Mapping[str, Any]: ...
    def save_copy(self, identity: WindowIdentity, destination: Path) -> SavedCopy: ...
    def verify_source_preserved(self, identity: WindowIdentity, source: DesktopModelObservation) -> SourcePreservation: ...


class ManagedEngineQueue(Protocol):
    """Injected narrow binding to the existing serialized engine queue."""

    def __call__(self, operation: str, arguments: Mapping[str, Any], model_ref: ModelRef | None,
                 context: DesktopDispatchContext) -> Mapping[str, Any]: ...


class AuthorizationCheck(Protocol):
    def __call__(self, project_id: str, permission: str) -> bool: ...


class ActionResolver(Protocol):
    def __call__(self, project_id: str, action: Mapping[str, Any]) -> DesktopActionPlan: ...


class ArtifactResolver(Protocol):
    def __call__(self, project_id: str, artifact_id: str) -> TrustedSourceArtifact: ...


class SaveCopyAuthorizer(Protocol):
    def __call__(self, project_id: str, destination: Path) -> bool: ...


@dataclass(frozen=True, slots=True)
class _WindowLease:
    token: str
    observation: WindowObservation
    expires_at: float


@dataclass(frozen=True, slots=True)
class _VerifiedBinding:
    model_ref: ModelRef
    server_endpoint: str
    model_tag: str
    fingerprint_at_bind: str
    verified_at: float


class DesktopCoordinator:
    """Production Desktop operation coordinator using the real OperationStore.

    One instance belongs to one ControlDaemon process.  Platform, authority,
    model-reference resolution, action policy, artifact resolution, save-copy
    policy, and the engine queue are mandatory trusted injections.
    """

    def __init__(
        self,
        *,
        store: OperationStore,
        adapter: DesktopAdapter,
        authorize: AuthorizationCheck,
        engine_queue: ManagedEngineQueue,
        resolve_action: ActionResolver,
        resolve_artifact: ArtifactResolver,
        authorize_save_copy: SaveCopyAuthorizer,
        handle_ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(store, OperationStore):
            raise TypeError("DesktopCoordinator requires a real OperationStore instance")
        dependencies = {
            "adapter": adapter,
            "authorize": authorize,
            "engine_queue": engine_queue,
            "resolve_action": resolve_action,
            "resolve_artifact": resolve_artifact,
            "authorize_save_copy": authorize_save_copy,
        }
        missing = sorted(name for name, value in dependencies.items() if value is None)
        if missing:
            raise TypeError("DesktopCoordinator requires trusted dependencies: " + ", ".join(missing))
        if not isinstance(handle_ttl_s, (int, float)) or handle_ttl_s <= 0 or handle_ttl_s > 300:
            raise ValueError("handle_ttl_s must be between 0 and 300 seconds")
        self.store = store
        self.adapter = adapter
        self.authorize = authorize
        self.engine_queue = engine_queue
        self.resolve_action = resolve_action
        self.resolve_artifact = resolve_artifact
        self.authorize_save_copy = authorize_save_copy
        self.handle_ttl_s = float(handle_ttl_s)
        self.clock = clock
        self._leases: dict[str, _WindowLease] = {}
        self._bindings: dict[str, _VerifiedBinding] = {}
        self._pending_bindings: dict[str, tuple[str, _VerifiedBinding]] = {}
        self._lease_lock = threading.RLock()
        self._claim_lock = threading.RLock()
        self._active_operations: set[str] = set()
        self._operation_lock = threading.RLock()
        self._serial_locks: dict[str, threading.RLock] = {}
        self._serial_lock_guard = threading.RLock()

    def dispatch(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Dispatch one frozen Desktop catalog operation."""
        try:
            if operation not in _ALL_OPS:
                raise DesktopOperationError("UNSUPPORTED_OPERATION", f"Desktop operation is not implemented: {operation}")
            args = self._validate_arguments(operation, arguments)
            project_id = args["project_id"]
            required_permissions = self._permissions(operation)
            for permission in required_permissions:
                try:
                    granted = self.authorize(project_id, permission) is True
                except Exception:
                    granted = False
                if not granted:
                    raise DesktopOperationError(
                        "PERMISSION_REQUIRED", f"{permission} permission is required for {operation}",
                        category=BLOCKED_PERMISSION, safe_retry=True,
                    )
            if operation == "desktop.status":
                return self._status(args)
            return self._claim_and_execute(operation, args)
        except IdempotencyConflict as exc:
            return self._error("IDEMPOTENCY_CONFLICT", str(exc), category=MODEL_BINDING_UNKNOWN, safe_retry=False)
        except DesktopOperationError as exc:
            return self._error(exc.code, str(exc), category=exc.category, safe_retry=exc.safe_retry)
        except (TypeError, ValueError, KeyError) as exc:
            return self._error("INVALID_REQUEST", str(exc), category=UNSUPPORTED_CONTROL, safe_retry=True)
        except Exception as exc:
            return self._error("DESKTOP_COORDINATION_FAILED", f"Desktop request could not establish a final outcome: {type(exc).__name__}", category=MODEL_BINDING_UNKNOWN, safe_retry=False)

    def _validate_arguments(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise DesktopOperationError("INVALID_REQUEST", "arguments must be an object", safe_retry=True)
        args = dict(arguments)
        missing = sorted(_REQUIRED_ARGUMENTS[operation] - args.keys())
        extra = sorted(args.keys() - _ALLOWED_ARGUMENTS[operation])
        if missing:
            raise DesktopOperationError("INVALID_REQUEST", "missing required fields: " + ", ".join(missing), safe_retry=True)
        if extra:
            raise DesktopOperationError("INVALID_REQUEST", "unexpected fields: " + ", ".join(extra), safe_retry=True)
        for field in ("project_id", "idempotency_key"):
            if field in _REQUIRED_ARGUMENTS[operation] and (not isinstance(args.get(field), str) or not args[field]):
                raise DesktopOperationError("INVALID_REQUEST", f"{field} must be a non-empty string", safe_retry=True)
        for field in ("window_ref", "model_ref", "expected_model_ref", "source_artifact", "target_session_id", "region"):
            if field in args and (not isinstance(args[field], str) or not args[field]):
                raise DesktopOperationError("INVALID_REQUEST", f"{field} must be a non-empty string", safe_retry=True)
        if "request_id" in args and not isinstance(args["request_id"], str):
            raise DesktopOperationError("INVALID_REQUEST", "request_id must be a string when supplied", safe_retry=True)
        if "runtime_id" in args and not isinstance(args["runtime_id"], str):
            raise DesktopOperationError("INVALID_REQUEST", "runtime_id must be a string when supplied", safe_retry=True)
        if operation == "desktop.select_node" and not self._valid_node_path(args["path"]):
            raise DesktopOperationError("INVALID_REQUEST", "path must match the catalog NodePath schema", safe_retry=True)
        if operation == "desktop.capture" and args["region"].strip().lower().replace("-", "_").replace(" ", "_") in _GLOBAL_CAPTURE_REGIONS:
            raise DesktopOperationError("CAPTURE_SCOPE_REJECTED", "capture region cannot expand to the full desktop or screen", safe_retry=True)
        if operation == "desktop.action" and (not isinstance(args["action"], Mapping) or not args["action"]):
            raise DesktopOperationError("INVALID_REQUEST", "action must be a non-empty object", safe_retry=True)
        if operation == "desktop.bind" and "verification" in args and not isinstance(args["verification"], Mapping):
            raise DesktopOperationError("INVALID_REQUEST", "verification must be an object", safe_retry=True)
        if operation == "desktop.migrate_standalone":
            policy = args["save_policy"]
            if not isinstance(policy, Mapping):
                raise DesktopOperationError("INVALID_REQUEST", "save_policy must be an object", safe_retry=True)
            allowed = {"mode", "destination_path", "overwrite"}
            if set(policy) != allowed or policy.get("mode") != "save_copy" or policy.get("overwrite") is not False:
                raise DesktopOperationError(
                    "SAVE_COPY_REQUIRED", "migration requires {mode: save_copy, destination_path, overwrite: false}", safe_retry=True,
                )
            if not isinstance(policy.get("destination_path"), str) or not policy["destination_path"]:
                raise DesktopOperationError("INVALID_REQUEST", "save_copy destination_path must be non-empty", safe_retry=True)
        if not isinstance(args.get("project_id"), str) or not args["project_id"]:
            raise DesktopOperationError("INVALID_REQUEST", "project_id must be a non-empty string", safe_retry=True)
        return args

    @staticmethod
    def _valid_node_path(value: Any) -> bool:
        # Match the frozen common.schema.json NodePath exactly: an object with
        # a segments array; every segment identifies either collection+tag or
        # one accessor. Loose strings/lists accepted here would bypass the
        # catalog's typed path contract and leave interpretation to adapters.
        if not isinstance(value, Mapping) or set(value) != {"segments"}:
            return False
        segments = value.get("segments")
        if not isinstance(segments, list):
            return False
        for segment in segments:
            if not isinstance(segment, Mapping):
                return False
            keys = set(segment)
            if keys == {"collection", "tag"}:
                if not all(isinstance(segment.get(key), str) for key in ("collection", "tag")):
                    return False
            elif keys == {"accessor"}:
                if not isinstance(segment.get("accessor"), str):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _permissions(operation: str) -> tuple[str, ...]:
        if operation == "desktop.status":
            return ("READ",)
        if operation == "desktop.shell_execute":
            return ("HOST_CONTROL", "TRUSTED_CODE")
        return ("HOST_CONTROL",)

    def _status(self, args: Mapping[str, Any]) -> dict[str, Any]:
        runtime_id = args.get("runtime_id")
        status = dict(self.adapter.status(runtime_id))
        observations = list(self.adapter.enumerate_windows(runtime_id))
        windows: list[dict[str, Any]] = []
        now = self.clock()
        permission_blocked = status.get("permission_status") != "AVAILABLE"
        controls_available = status.get("control_status") == AVAILABLE
        metadata_available = status.get("metadata_status", "AVAILABLE") == "AVAILABLE"
        for item in observations:
            if not isinstance(item, WindowObservation):
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "platform adapter returned an invalid window observation", category=MODEL_BINDING_UNKNOWN)
            identity = item.identity
            binding_status = MODEL_BINDING_UNKNOWN
            token = None
            if permission_blocked or item.control_status == BLOCKED_PERMISSION:
                binding_status = BLOCKED_PERMISSION
            elif not controls_available or item.control_status != AVAILABLE:
                binding_status = UNSUPPORTED_CONTROL
            elif (identity.process_birth.upper() in {"UNKNOWN", "UNVERIFIED"}
                  or identity.comsol_version.upper() in {"UNKNOWN", "UNVERIFIED"}):
                binding_status = MODEL_BINDING_UNKNOWN
            else:
                token = "dw_" + secrets.token_urlsafe(32)
                with self._lease_lock:
                    self._leases[token] = _WindowLease(token, item, now + self.handle_ttl_s)
                binding_status = AVAILABLE
            windows.append({
                "window_ref": token,
                "title": item.title,
                "platform": identity.platform,
                "comsol_version": identity.comsol_version,
                "identity_fingerprint": identity.fingerprint(),
                "identity_status": binding_status,
                "model_binding": "UNKNOWN",
            })
        if status.get("permission_status") == BLOCKED_PERMISSION:
            category = BLOCKED_PERMISSION
        elif not metadata_available:
            category = UNSUPPORTED_CONTROL
        elif not windows:
            category = WINDOW_NOT_FOUND
        elif not controls_available:
            category = UNSUPPORTED_CONTROL
        elif any(row["window_ref"] is None for row in windows):
            category = MODEL_BINDING_UNKNOWN
        else:
            category = AVAILABLE
        return self._success({"status": category, "adapter": status, "windows": windows}, operation="desktop.status")

    def _claim_and_execute(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        request_id = args.get("request_id") or str(uuid4())
        key = args["idempotency_key"]
        semantic_args = {name: value for name, value in args.items() if name not in {"request_id", "idempotency_key"}}
        digest = canonical_request_hash(operation, semantic_args, None, None, project_id=args["project_id"])
        with self._claim_lock:
            record, reused = self.store.begin(
                request_id=request_id,
                idempotency_key=key,
                request_hash=digest,
                operation=operation,
                metadata={"operation": operation, "project_id": args["project_id"], "capability": "desktop"},
            )
            if reused:
                return self._replay_or_pending(record)
            # The durable row and unique idempotency key exist before any adapter,
            # UI, file, artifact, or engine callback can run.
            with self._operation_lock:
                self._active_operations.add(record["operation_id"])
            try:
                self.store.update_job(record["job_id"], "RUNNING", metadata={
                    "desktop_operation": operation,
                    "coordinator_process_id": os.getpid(),
                })
                self.store.add_event(record["job_id"], _OWNER_EVENT, {"operation": operation, "request_hash": digest})
            except Exception:
                with self._operation_lock:
                    self._active_operations.discard(record["operation_id"])
                failed = self._error("DESKTOP_CLAIM_START_FAILED", "durable Desktop claim could not be advanced; no callback was invoked",
                                     category=MODEL_BINDING_UNKNOWN, safe_retry=False,
                                     execution={**self._execution(record), "status": "FAILED"})
                self.store.finish(record["operation_id"], status="FAILED", result=failed)
                return failed
        try:
            result = self._execute_operation(operation, args, record, lambda: self._mark_effect(record["operation_id"]))
            if result.get("success") is True:
                status = "SUCCEEDED"
            elif result.get("execution", {}).get("status") == "UNKNOWN":
                status = "UNKNOWN"
            else:
                status = "FAILED"
        except DesktopOperationError as exc:
            status = "UNKNOWN" if exc.side_effect_started else "FAILED"
            result = self._error(exc.code, str(exc), category=exc.category, safe_retry=exc.safe_retry,
                                 execution={**self._execution(record), "status": status})
        except Exception as exc:
            status = "UNKNOWN"
            result = self._error("DESKTOP_EXECUTION_STATE_UNKNOWN", f"Desktop callback failed after claim: {type(exc).__name__}",
                                 category=MODEL_BINDING_UNKNOWN, safe_retry=False,
                                 execution={**self._execution(record), "status": status})
        try:
            accepted, authoritative = self.store.finish(record["operation_id"], status=status, result=result)
            if not accepted:
                stored = self.store.get_operation(record["operation_id"])
                if stored and stored.get("result") is not None:
                    result = stored["result"]
            self._complete_pending_binding(record["operation_id"], succeeded=bool(
                accepted and authoritative == "SUCCEEDED" and result.get("success") is True
            ))
        finally:
            self._complete_pending_binding(record["operation_id"], succeeded=False)
            with self._operation_lock:
                self._active_operations.discard(record["operation_id"])
        return result

    def _mark_effect(self, operation_id: str) -> None:
        # Kept as a hook for adapter events; the durable RUNNING claim already
        # precedes this marker.  Uncertainty is attached to raised errors below.
        with self._operation_lock:
            if operation_id not in self._active_operations:
                raise DesktopOperationError("OPERATION_OWNERSHIP_LOST", "Desktop operation is no longer owned by this coordinator", category=MODEL_BINDING_UNKNOWN)

    def _replay_or_pending(self, record: Mapping[str, Any]) -> dict[str, Any]:
        status = str(record.get("status") or "UNKNOWN")
        result = record.get("result")
        if status in TERMINAL and isinstance(result, Mapping):
            return dict(result)
        if status in {"UNKNOWN", "RECONCILING"} and isinstance(result, Mapping):
            return dict(result)
        operation_id = str(record["operation_id"])
        with self._operation_lock:
            active = operation_id in self._active_operations
        if status == "RUNNING" and not active:
            result = self._error("DESKTOP_OPERATION_OWNER_UNVERIFIED", "the durable operation has no local owner record; another coordinator may still own it, and it was not replayed",
                                 category=MODEL_BINDING_UNKNOWN, safe_retry=False,
                                 execution={"operation_id": operation_id, "request_hash": record.get("request_hash"),
                                            "stored_status": "RUNNING", "owner_status": "UNVERIFIED", "status": "UNKNOWN"})
            # Absence from this instance's process-local owner set says nothing
            # about another live coordinator. Do not mutate a running claim or
            # prevent its actual owner from recording its terminal result.
            return result
        if status in {"QUEUED", "STARTING"}:
            return self._error(
                "DESKTOP_OPERATION_STATE_UNKNOWN",
                "a durable claim exists without a confirmed live dispatch; the callback was not replayed",
                category=MODEL_BINDING_UNKNOWN,
                safe_retry=False,
                execution={"operation_id": operation_id, "request_hash": record.get("request_hash"), "stored_status": status, "status": "UNKNOWN"},
            )
        if status == "RUNNING" and active:
            return self._error(
                "DESKTOP_OPERATION_IN_PROGRESS",
                "the original Desktop callback is still owned by this coordinator; it was not replayed",
                category=MODEL_BINDING_UNKNOWN,
                safe_retry=False,
                execution={"operation_id": operation_id, "request_hash": record.get("request_hash"), "stored_status": status, "status": "RUNNING"},
            )
        return self._error(
            "DESKTOP_OPERATION_IN_PROGRESS" if active or status == "RUNNING" else "DESKTOP_OPERATION_STATE_UNKNOWN",
            "the durable Desktop claim already exists; the callback was not replayed",
            category=MODEL_BINDING_UNKNOWN,
            safe_retry=False,
            execution={"operation_id": operation_id, "request_hash": record.get("request_hash"), "stored_status": status, "status": status if active else "UNKNOWN"},
        )

    def _execute_operation(
        self, operation: str, args: Mapping[str, Any], record: Mapping[str, Any], mark_effect: Callable[[], None]
    ) -> dict[str, Any]:
        project_id = str(args["project_id"])
        token = str(args["window_ref"])
        context = DesktopDispatchContext(
            operation=str(record["operation"]),
            request_id=str(record["request_id"]),
            operation_id=str(record["operation_id"]),
            job_id=str(record["job_id"]),
            idempotency_key=str(record["idempotency_key"]),
            request_hash=str(record["request_hash"]),
            project_id=project_id,
        )
        lease = self._get_lease(token)
        # Reject a stale or reused OS handle before consulting managed model
        # state or performing any other request-specific callback.
        self._revalidate(lease)
        sessions: list[str] = []
        if operation == "desktop.migrate_standalone":
            sessions.append(str(args["target_session_id"]))
        elif operation not in {"desktop.bind", "desktop.show_model"}:
            binding = self._bindings.get(token)
            if binding:
                sessions.append(binding.model_ref.session_id)
        if operation in {"desktop.bind", "desktop.show_model"}:
            snapshot = self._resolve_model_snapshot(project_id, str(args["model_ref"]), context)
            sessions.append(snapshot.model_ref.session_id)
        elif operation == "desktop.shell_execute":
            snapshot = self._resolve_model_snapshot(project_id, str(args["expected_model_ref"]), context)
            sessions.append(snapshot.model_ref.session_id)

        with self._serialized(lease.observation.identity, sessions):
            observation = self._revalidate(lease)
            if operation == "desktop.bind":
                return self._bind(project_id, token, observation, snapshot, record, context)
            if operation == "desktop.show_model":
                return self._show_model(project_id, token, observation, snapshot, record, context, mark_effect)
            if operation == "desktop.select_node":
                binding, _ = self._verified_binding(project_id, token, observation, context)
                mark_effect()
                value = self.adapter.select_node(observation.identity, args["path"])
                if not isinstance(value, Mapping) or value.get("verified") is not True or value.get("selected_path") != args["path"]:
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the selected NodePath", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({"selected_path": value["selected_path"], "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.capture":
                binding, _ = self._verified_binding(project_id, token, observation, context)
                mark_effect()
                value = self.adapter.capture(observation.identity, str(args["region"]))
                self._validate_capture_result(value, str(args["region"]))
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({**dict(value), "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.action":
                binding, _ = self._verified_binding(project_id, token, observation, context)
                plan = self.resolve_action(project_id, args["action"])
                if not isinstance(plan, DesktopActionPlan) or not plan.action_id:
                    raise DesktopOperationError("ACTION_NOT_AUTHORIZED", "trusted action catalog did not resolve this action", category=BLOCKED_PERMISSION, safe_retry=True)
                mark_effect()
                value = self.adapter.perform_action(observation.identity, plan)
                if not isinstance(value, Mapping) or value.get("verified") is not True or value.get("action_id") != plan.action_id:
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the requested action", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({"action_id": plan.action_id, "declared_model_mutation": plan.mutates_model,
                                      "action_result": dict(value), "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.shell_execute":
                binding, _ = self._verified_binding(project_id, token, observation, context)
                if binding.model_ref != snapshot.model_ref:
                    raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "expected_model_ref does not match the current verified binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
                artifact = self.resolve_artifact(project_id, str(args["source_artifact"]))
                if not isinstance(artifact, TrustedSourceArtifact) or artifact.artifact_id != args["source_artifact"] or not self._valid_sha256(artifact.sha256) or artifact.size < 0 or not artifact.provenance:
                    raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "source_artifact has no trusted immutable provenance", category=BLOCKED_PERMISSION, safe_retry=True)
                mark_effect()
                value = self._engine_call("desktop.shell_execute", {
                    "project_id": project_id,
                    "source_artifact": artifact.artifact_id,
                    "source_sha256": artifact.sha256,
                    "source_size": artifact.size,
                    "source_provenance": artifact.provenance,
                    "window_identity": observation.identity.fingerprint(),
                }, binding.model_ref, context, mutating=True)
                data = self._response_data(value)
                if data.get("executed") is not True or data.get("source_sha256") != artifact.sha256 or data.get("model_ref") != binding.model_ref.as_dict():
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "engine queue did not attest source hash and target ModelRef", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success(dict(data), operation=operation, record=record)
            if operation == "desktop.migrate_standalone":
                return self._migrate(project_id, token, observation, args, record, context, mark_effect)
        raise DesktopOperationError("UNSUPPORTED_OPERATION", f"no Desktop handler for {operation}")

    def _bind(self, project_id: str, token: str, observation: WindowObservation,
              snapshot: ManagedModelSnapshot, record: Mapping[str, Any], context: DesktopDispatchContext) -> dict[str, Any]:
        current = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if current.model_ref != snapshot.model_ref or current.server_endpoint != snapshot.server_endpoint or current.model_tag != snapshot.model_tag:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed model identity changed while binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        snapshot = current
        desktop = self._observe_model(observation.identity)
        self._assert_model_match(desktop, snapshot)
        binding = _VerifiedBinding(snapshot.model_ref, snapshot.server_endpoint, snapshot.model_tag, snapshot.fingerprint, self.clock())
        self._stage_binding(record, token, binding)
        return self._success({
            "desktop_binding_verified": True,
            "binding_evidence": {
                "window_identity": observation.identity.fingerprint(),
                "server_endpoint": snapshot.server_endpoint,
                "model_tag": snapshot.model_tag,
                "model_ref": snapshot.model_ref.as_dict(),
                "fingerprint_at_bind": snapshot.fingerprint,
            },
        }, operation="desktop.bind", record=record)

    def _show_model(self, project_id: str, token: str, observation: WindowObservation,
                    snapshot: ManagedModelSnapshot, record: Mapping[str, Any], context: DesktopDispatchContext,
                    mark_effect: Callable[[], None]) -> dict[str, Any]:
        before = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if before.model_ref != snapshot.model_ref or before.fingerprint != snapshot.fingerprint:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed before show_model", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        mark_effect()
        value = self.adapter.show_model(observation.identity, snapshot)
        if not isinstance(value, Mapping) or value.get("verified") is not True:
            raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the displayed server model", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        after_observation = self._revalidate(self._get_lease(token))
        after = self._observe_model(after_observation.identity)
        self._assert_model_match(after, snapshot, side_effect_started=True)
        managed_after = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if managed_after.model_ref != snapshot.model_ref or managed_after.fingerprint != snapshot.fingerprint:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed while showing the model", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        binding = _VerifiedBinding(snapshot.model_ref, snapshot.server_endpoint, snapshot.model_tag, snapshot.fingerprint, self.clock())
        self._stage_binding(record, token, binding)
        return self._success({"desktop_binding_verified": True, "model_ref": snapshot.model_ref.as_dict(), "server_endpoint": snapshot.server_endpoint, "model_tag": snapshot.model_tag}, operation="desktop.show_model", record=record)

    def _verified_binding(self, project_id: str, token: str, observation: WindowObservation,
                          context: DesktopDispatchContext) -> tuple[_VerifiedBinding, ManagedModelSnapshot]:
        with self._lease_lock:
            binding = self._bindings.get(token)
        if binding is None:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "window has no verified ModelRef binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        desktop = self._observe_model(observation.identity)
        if desktop.mode != "SERVER" or desktop.server_endpoint != binding.server_endpoint or desktop.model_tag != binding.model_tag:
            with self._lease_lock:
                self._bindings.pop(token, None)
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "current Desktop model observation no longer matches the verified binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        current = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, binding.model_ref, context)
        if current.model_ref != binding.model_ref or current.fingerprint != binding.fingerprint_at_bind:
            with self._lease_lock:
                self._bindings.pop(token, None)
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef, generation, or fingerprint changed since binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        return binding, current

    def _verify_binding_after_action(self, project_id: str, token: str, observation: WindowObservation,
                                     binding: _VerifiedBinding, context: DesktopDispatchContext,
                                     *, side_effect_started: bool) -> None:
        try:
            current = self._revalidate(self._get_lease(token))
            desktop = self._observe_model(current.identity)
            self._assert_model_match(desktop, ManagedModelSnapshot(binding.model_ref, binding.server_endpoint, binding.model_tag, binding.fingerprint_at_bind), side_effect_started=side_effect_started)
            model = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, binding.model_ref, context)
            if model.model_ref != binding.model_ref or model.fingerprint != binding.fingerprint_at_bind:
                raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed after the Desktop action", category=MODEL_BINDING_UNKNOWN, side_effect_started=side_effect_started)
        except DesktopOperationError as exc:
            if side_effect_started:
                exc.side_effect_started = True
            raise

    def _migrate(self, project_id: str, token: str, observation: WindowObservation, args: Mapping[str, Any],
                 record: Mapping[str, Any], context: DesktopDispatchContext,
                 mark_effect: Callable[[], None]) -> dict[str, Any]:
        source = self._observe_model(observation.identity)
        if source.mode != "STANDALONE" or not source.standalone_model_id or not source.fingerprint or source.dirty is None:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "adapter cannot identify standalone model identity and dirty state", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        # The observed source is explicitly standalone, so any cached server
        # binding for this lease is stale and must not survive migration.
        with self._lease_lock:
            self._bindings.pop(token, None)
        raw_destination = Path(str(args["save_policy"]["destination_path"])).expanduser()
        if not raw_destination.is_absolute():
            raise DesktopOperationError("SAVE_COPY_PATH_REJECTED", "save-copy destination must be an absolute path", category=BLOCKED_PERMISSION, safe_retry=True)
        destination = raw_destination.resolve(strict=False)
        if destination.exists() or not self.authorize_save_copy(project_id, destination):
            raise DesktopOperationError("SAVE_COPY_PATH_REJECTED", "save-copy destination must be absolute, new, and project-authorized", category=BLOCKED_PERMISSION, safe_retry=True)
        mark_effect()
        copy = self.adapter.save_copy(observation.identity, destination)
        if not isinstance(copy, SavedCopy) or Path(copy.path) != destination or not self._valid_sha256(copy.sha256):
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "adapter did not return an exact path and SHA-256 for the copy", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if not copy.source_preserved or copy.source_dirty_after != source.dirty or copy.source_fingerprint_after != source.fingerprint:
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "save-copy changed or discarded the standalone source state", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if not destination.is_file() or self._file_sha256(destination) != copy.sha256:
            raise DesktopOperationError("SAVE_COPY_HASH_MISMATCH", "saved copy is missing or its bytes do not match the adapter receipt", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        loaded = self._engine_call("model.load", {
            "project_id": project_id,
            "target_session_id": str(args["target_session_id"]),
            "model_path": str(destination),
            "source_sha256": copy.sha256,
            "load_mode": "save_copy",
        }, None, context, mutating=True)
        snapshot = self._snapshot_from_response(loaded)
        if snapshot.model_ref.session_id != args["target_session_id"]:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "loaded ModelRef belongs to a different target session", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        final_observation = self._revalidate(self._get_lease(token))
        final_model = self._observe_model(final_observation.identity)
        if (final_model.mode != "STANDALONE" or final_model.standalone_model_id != source.standalone_model_id
                or final_model.fingerprint != source.fingerprint or final_model.dirty != source.dirty):
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "the original Desktop window no longer shows the same standalone source", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        managed_after = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if managed_after.model_ref != snapshot.model_ref or managed_after.fingerprint != snapshot.fingerprint:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed during migration", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        preserved = self.adapter.verify_source_preserved(final_observation.identity, source)
        if (not isinstance(preserved, SourcePreservation) or not preserved.still_open
                or preserved.standalone_model_id != source.standalone_model_id
                or preserved.fingerprint != source.fingerprint or preserved.dirty != source.dirty):
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "source standalone model is no longer open after migration", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        return self._success({
            "migration_mode": "explicit_save_copy",
            "source_window_identity": final_observation.identity.fingerprint(),
            "source_standalone_model_id": source.standalone_model_id,
            "source_fingerprint": source.fingerprint,
            "source_dirty_preserved": source.dirty,
            "saved_copy": {"path": str(destination), "sha256": copy.sha256},
            "target_model_ref": snapshot.model_ref.as_dict(),
            "target_model_tag": snapshot.model_tag,
            "target_server_endpoint": snapshot.server_endpoint,
            "target_model_fingerprint": snapshot.fingerprint,
            "managed_model_loaded": True,
            "desktop_binding_verified": False,
            "source_window_preserved": True,
        }, operation="desktop.migrate_standalone", record=record)

    def _observe_model(self, identity: WindowIdentity) -> DesktopModelObservation:
        value = self.adapter.observe_model(identity)
        if not isinstance(value, DesktopModelObservation):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "adapter returned no typed current-model observation", category=MODEL_BINDING_UNKNOWN)
        return value

    def _assert_model_match(self, desktop: DesktopModelObservation, managed: ManagedModelSnapshot,
                            *, side_effect_started: bool = False) -> None:
        if (desktop.mode != "SERVER" or desktop.server_endpoint != managed.server_endpoint
                or desktop.model_tag != managed.model_tag):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop current server endpoint/model tag does not match the managed ModelRef", category=MODEL_BINDING_UNKNOWN, side_effect_started=side_effect_started, safe_retry=not side_effect_started)

    def _engine_snapshot(self, operation: str, arguments: Mapping[str, Any], model_ref: ModelRef,
                         context: DesktopDispatchContext) -> ManagedModelSnapshot:
        value = self._engine_call(operation, arguments, model_ref, context, mutating=False)
        return self._snapshot_from_response(value)

    def _resolve_model_snapshot(self, project_id: str, model_ref_id: str,
                                 context: DesktopDispatchContext) -> ManagedModelSnapshot:
        value = self._engine_call("desktop.binding.resolve", {
            "project_id": project_id,
            "model_ref_id": model_ref_id,
        }, None, context, mutating=False)
        return self._snapshot_from_response(value)

    def _engine_call(self, operation: str, arguments: Mapping[str, Any], model_ref: ModelRef | None,
                     context: DesktopDispatchContext, *, mutating: bool) -> Mapping[str, Any]:
        try:
            result = self.engine_queue(operation, dict(arguments), model_ref, context)
        except Exception as exc:
            raise DesktopOperationError("ENGINE_QUEUE_STATE_UNKNOWN", f"managed engine queue did not return an outcome: {type(exc).__name__}", category=MODEL_BINDING_UNKNOWN, side_effect_started=mutating, safe_retry=not mutating) from exc
        if not isinstance(result, Mapping):
            raise DesktopOperationError("ENGINE_QUEUE_STATE_UNKNOWN", "managed engine queue returned a non-object", category=MODEL_BINDING_UNKNOWN, side_effect_started=mutating)
        if result.get("success") is False:
            data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
            error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
            definitely_not_dispatched = data.get("engine_dispatched") is False or error.get("stage") == "validation" or result.get("execution_state") == "NOT_DISPATCHED"
            raise DesktopOperationError(
                str(error.get("code") or "ENGINE_QUEUE_REJECTED"),
                str(error.get("message") or "managed engine queue rejected the request"),
                category=MODEL_BINDING_UNKNOWN,
                side_effect_started=mutating and not definitely_not_dispatched,
                safe_retry=definitely_not_dispatched,
            )
        return result

    @staticmethod
    def _response_data(response: Mapping[str, Any]) -> dict[str, Any]:
        data = response.get("data", response)
        if not isinstance(data, Mapping):
            raise DesktopOperationError("ENGINE_QUEUE_STATE_UNKNOWN", "managed engine response lacks an object result", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        return dict(data)

    def _snapshot_from_response(self, response: Mapping[str, Any]) -> ManagedModelSnapshot:
        data = self._response_data(response)
        raw = data.get("model_ref")
        if not isinstance(raw, Mapping):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed engine did not return ModelRef identity", category=MODEL_BINDING_UNKNOWN)
        try:
            model_ref = model_ref_from_mapping(raw)
        except Exception as exc:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed engine returned invalid ModelRef identity", category=MODEL_BINDING_UNKNOWN) from exc
        endpoint = data.get("server_endpoint")
        model_tag = data.get("model_tag")
        fingerprint = data.get("fingerprint")
        if not all(isinstance(item, str) and item for item in (endpoint, model_tag, fingerprint)) or model_tag != model_ref.model_tag:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed engine snapshot lacks endpoint, matching model tag, or fingerprint", category=MODEL_BINDING_UNKNOWN)
        return ManagedModelSnapshot(model_ref, endpoint, model_tag, fingerprint)

    def _get_lease(self, token: str) -> _WindowLease:
        with self._lease_lock:
            lease = self._leases.get(token)
        if lease is None or self.clock() >= lease.expires_at:
            with self._lease_lock:
                self._leases.pop(token, None)
                self._bindings.pop(token, None)
            raise DesktopOperationError("WINDOW_HANDLE_EXPIRED", "window_ref is missing or expired; call desktop.status again", category=WINDOW_NOT_FOUND, safe_retry=True)
        return lease

    def _revalidate(self, lease: _WindowLease) -> WindowObservation:
        try:
            platform_status = self.adapter.status()
        except Exception as exc:
            raise DesktopOperationError("PLATFORM_PERMISSION_UNKNOWN", f"platform permission probe failed: {type(exc).__name__}", category=BLOCKED_PERMISSION, safe_retry=True) from exc
        if platform_status.get("permission_status") != "AVAILABLE":
            raise DesktopOperationError("PERMISSION_REQUIRED", "platform permission is not currently verified", category=BLOCKED_PERMISSION, safe_retry=True)
        if platform_status.get("control_status") != AVAILABLE:
            raise DesktopOperationError("UNSUPPORTED_CONTROL", "native adapter reports no verified controls", category=UNSUPPORTED_CONTROL, safe_retry=True)
        try:
            current = self.adapter.revalidate_window(lease.observation.identity)
        except DesktopOperationError:
            raise
        except Exception as exc:
            raise DesktopOperationError("WINDOW_REVALIDATION_FAILED", f"platform identity revalidation failed: {type(exc).__name__}", category=MODEL_BINDING_UNKNOWN) from exc
        if current is None or not current.identity.same_process_window(lease.observation.identity):
            with self._lease_lock:
                self._bindings.pop(lease.token, None)
            raise DesktopOperationError("WINDOW_IDENTITY_CHANGED", "window, process birth, login, session, or COMSOL version changed", category=WINDOW_NOT_FOUND, safe_retry=True)
        if current.control_status == BLOCKED_PERMISSION:
            raise DesktopOperationError("PERMISSION_REQUIRED", "platform control permission is not available", category=BLOCKED_PERMISSION, safe_retry=True)
        if current.control_status != AVAILABLE:
            raise DesktopOperationError("UNSUPPORTED_CONTROL", "platform adapter cannot control this window", category=UNSUPPORTED_CONTROL, safe_retry=True)
        return current

    @contextmanager
    def _serialized(self, identity: WindowIdentity, sessions: Sequence[str]) -> Iterator[None]:
        keys = sorted({"window:" + identity.fingerprint(), *("session:" + item for item in sessions if item)})
        with self._serial_lock_guard:
            locks = [self._serial_locks.setdefault(key, threading.RLock()) for key in keys]
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def _validate_capture_result(self, value: Mapping[str, Any], region: str) -> None:
        if not isinstance(value, Mapping) or value.get("verified") is not True:
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "adapter did not verify a capture result", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if value.get("region") != region or value.get("scope") != "target_window":
            raise DesktopOperationError("CAPTURE_SCOPE_REJECTED", "capture result did not prove the requested target-window scope", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if not isinstance(value.get("artifact_id"), str) or not value.get("artifact_id") or not self._valid_sha256(value.get("sha256")):
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "capture result requires an artifact id and SHA-256", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)

    @staticmethod
    def _valid_sha256(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _execution(record: Mapping[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in ("request_id", "operation_id", "request_hash", "idempotency_key", "job_id") if key in record}

    def _success(self, data: Mapping[str, Any], *, operation: str, record: Mapping[str, Any] | None = None) -> dict[str, Any]:
        execution = {"operation": operation}
        if record is not None:
            execution.update(self._execution(record))
            execution["status"] = "SUCCEEDED"
        return {"success": True, "data": dict(data), "execution": execution}

    def _stage_binding(self, record: Mapping[str, Any], token: str, binding: _VerifiedBinding) -> None:
        # A binding only becomes available to later requests after the result is
        # durably SUCCEEDED in OperationStore.
        with self._lease_lock:
            self._pending_bindings[str(record["operation_id"])] = (token, binding)

    def _complete_pending_binding(self, operation_id: str, *, succeeded: bool) -> None:
        with self._lease_lock:
            pending = self._pending_bindings.pop(operation_id, None)
            if pending is None or not succeeded:
                return
            token, binding = pending
            lease = self._leases.get(token)
            if lease is not None and self.clock() < lease.expires_at:
                self._bindings[token] = binding

    def _error(self, code: str, message: str, *, category: str, safe_retry: bool,
               execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "data": {"status": category},
            "error": {"code": code, "message": message, "safe_retry": bool(safe_retry)},
            "execution": dict(execution or {}),
        }


class MetadataOnlyDesktopAdapter:
    """Explicitly limited base for native metadata observers.

    Subclasses can enumerate/revalidate real OS window identities.  All actions
    remain unsupported until a platform-specific control implementation exists.
    """

    def status(self, runtime_id: str | None = None) -> Mapping[str, Any]:
        return {"platform": "unknown", "permission_status": "UNKNOWN", "control_status": UNSUPPORTED_CONTROL}

    def enumerate_windows(self, runtime_id: str | None = None) -> Sequence[WindowObservation]:
        raise DesktopOperationError("UNSUPPORTED_CONTROL", "native window metadata adapter is unavailable", category=UNSUPPORTED_CONTROL)

    def revalidate_window(self, identity: WindowIdentity) -> WindowObservation | None:
        return next((item for item in self.enumerate_windows() if item.identity == identity), None)

    def observe_model(self, identity: WindowIdentity) -> DesktopModelObservation:
        raise DesktopOperationError("UNSUPPORTED_CONTROL", "platform adapter cannot read current COMSOL Desktop model binding", category=UNSUPPORTED_CONTROL)

    def _unsupported(self, operation: str) -> None:
        raise DesktopOperationError("UNSUPPORTED_CONTROL", f"native control adapter does not implement {operation}", category=UNSUPPORTED_CONTROL, safe_retry=True)

    def show_model(self, identity: WindowIdentity, snapshot: ManagedModelSnapshot) -> Mapping[str, Any]:
        self._unsupported("desktop.show_model")

    def select_node(self, identity: WindowIdentity, path: Any) -> Mapping[str, Any]:
        self._unsupported("desktop.select_node")

    def capture(self, identity: WindowIdentity, region: str) -> Mapping[str, Any]:
        self._unsupported("desktop.capture")

    def perform_action(self, identity: WindowIdentity, plan: DesktopActionPlan) -> Mapping[str, Any]:
        self._unsupported("desktop.action")

    def save_copy(self, identity: WindowIdentity, destination: Path) -> SavedCopy:
        self._unsupported("desktop.migrate_standalone save-copy")

    def verify_source_preserved(self, identity: WindowIdentity, source: DesktopModelObservation) -> SourcePreservation:
        self._unsupported("desktop.migrate_standalone source preservation check")
