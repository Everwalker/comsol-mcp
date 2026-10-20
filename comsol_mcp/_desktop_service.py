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
import stat
import tempfile
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
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.side_effect_started = side_effect_started
        self.safe_retry = safe_retry
        # Optional low-level evidence is kept separate from the user-facing
        # message so platform adapters can preserve exact failure context
        # without encouraging callers to retry a terminal operation.
        self.diagnostics = dict(diagnostics or {})


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
    active_model_label: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedModelSnapshot:
    model_ref: ModelRef
    server_endpoint: str
    model_tag: str
    fingerprint: str
    revision: int | None = None


@dataclass(frozen=True, slots=True)
class SavedCopy:
    path: str
    sha256: str | None
    source_preserved: bool
    source_dirty_after: bool
    source_fingerprint_after: str
    size_bytes: int | None = None


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
    content: bytes | None = None


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
    def save_copy_completion_state(self, identity: WindowIdentity, *, side_effect_started: bool = False) -> str: ...
    def save_copy(self, identity: WindowIdentity, destination: Path, source: DesktopModelObservation) -> SavedCopy: ...
    def verify_source_preserved(self, identity: WindowIdentity, source: DesktopModelObservation) -> SourcePreservation: ...
    def shell_execute(self, identity: WindowIdentity, artifact: TrustedSourceArtifact) -> Mapping[str, Any]: ...


class ManagedEngineQueue(Protocol):
    """Injected narrow binding to the existing serialized engine queue."""

    def __call__(self, operation: str, arguments: Mapping[str, Any], model_ref: ModelRef | None,
                 context: DesktopDispatchContext) -> Mapping[str, Any]: ...


def _verify_private_save_copy_directory(path: Path,
                                        expected_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from ._host_scoped_provider import verify_private_path_security
    try:
        evidence = verify_private_path_security(
            path, directory=True,
            expected_identity=dict(expected_identity) if expected_identity is not None else None,
        )
    except Exception as exc:
        raise DesktopOperationError("SAVE_COPY_STAGE_UNTRUSTED", "host-private Save Copy staging directory failed owner/ACL identity verification", side_effect_started=False, safe_retry=True) from exc
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("identity"), Mapping):
        raise DesktopOperationError("SAVE_COPY_STAGE_UNTRUSTED", "staging directory verifier returned no pinned identity", side_effect_started=False, safe_retry=True)
    semantics = evidence.get("security_semantics")
    if semantics not in {
        "posix-owner-mode", "posix-owner-mode+filesystem-ownership+no-extended-acl",
        "windows-read-control-owner-dacl-persistent-acls",
    }:
        raise DesktopOperationError("SAVE_COPY_STAGE_UNTRUSTED", "staging directory has no supported host ownership proof", side_effect_started=False, safe_retry=True)
    return dict(evidence["identity"])


def _create_private_save_copy_stage(context: DesktopDispatchContext) -> tuple[Path, dict[str, Any]]:
    try:
        parent = Path(tempfile.gettempdir()).resolve(strict=True)
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("temporary root is not a real directory")
        operation_suffix = sha256(context.operation_id.encode("utf-8")).hexdigest()[:16]
        stage = Path(tempfile.mkdtemp(prefix=f"comsol-mcp-save-{operation_suffix}-", dir=str(parent)))
        if os.name != "nt":
            os.chmod(stage, 0o700)
        identity = _verify_private_save_copy_directory(stage)
        return stage, identity
    except DesktopOperationError:
        raise
    except Exception as exc:
        raise DesktopOperationError("SAVE_COPY_STAGE_UNAVAILABLE", "a host-owned private Save Copy staging directory could not be created", side_effect_started=False, safe_retry=True) from exc


def _verified_private_save_copy_file(stage_root: Path, path: Path,
                                     stage_root_identity: Mapping[str, Any],
                                     *, completion_verified: bool = False,
                                     timeout_s: float = 120.0) -> dict[str, Any]:
    """Hash a stable private Save Copy only after an observed UI completion transition.

    File quiet time is a secondary byte-integrity check. It never establishes
    that the native Save Copy operation finished; the coordinator must first
    observe the profiled idle->busy->complete transition.
    """
    if completion_verified is not True:
        raise DesktopOperationError("SAVE_COPY_COMPLETION_UNKNOWN", "native completion state was not observed by the serialized coordinator", side_effect_started=True, safe_retry=False)
    if path.parent != stage_root or path.name != "model.mph":
        raise DesktopOperationError("SAVE_COPY_STAGE_UNTRUSTED", "native Save Copy path escaped its exact task-private staging directory", side_effect_started=True, safe_retry=False)
    deadline = time.monotonic() + timeout_s
    previous = None
    stable_since = None
    while time.monotonic() < deadline:
        _verify_private_save_copy_directory(stage_root, stage_root_identity)
        try:
            named = path.lstat()
        except FileNotFoundError:
            stable_since = None
            previous = None
            time.sleep(0.1)
            continue
        except OSError as exc:
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "private Save Copy output could not be inspected", side_effect_started=True, safe_retry=False) from exc
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "private Save Copy output is not an ordinary regular file", side_effect_started=True, safe_retry=False)
        if named.st_size <= 0:
            previous, stable_since = None, None
            time.sleep(0.1)
            continue
        current = (int(named.st_dev), int(named.st_ino), int(named.st_size),
                   int(getattr(named, "st_mtime_ns", int(named.st_mtime * 1e9))),
                   int(getattr(named, "st_ctime_ns", int(named.st_ctime * 1e9))))
        now = time.monotonic()
        if current == previous:
            if stable_since is None:
                stable_since = now
        else:
            previous, stable_since = current, None
        # After the native profile has explicitly reported completion, require
        # a bounded quiet window as a secondary check against asynchronous
        # flushes. This quiet interval is not the completion signal.
        if stable_since is not None and now - stable_since >= 2.0:
            break
        time.sleep(0.1)
    else:
        raise DesktopOperationError("SAVE_COPY_TIMEOUT", "native Save Copy did not produce a stable private file before its deadline", side_effect_started=True, safe_retry=False)

    _verify_private_save_copy_directory(stage_root, stage_root_identity)
    if os.name == "nt":
        from ._windows_parameter_publish import _API, pinned_parent
        from ._host_scoped_provider import verify_private_path_security
        relative = path.relative_to(stage_root).as_posix()
        with pinned_parent(stage_root, relative, add_file=False, _api=_API()) as (api, parent, parts, binding):
            handle = api.open_source(parent, parts[-1])
            try:
                before = api.source_fingerprint(handle)
                if before[1] <= 0 or before[4] != 1:
                    raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "Windows Save Copy staging file has invalid size or link count", side_effect_started=True, safe_retry=False)
                evidence = verify_private_path_security(path, directory=False)
                identity = evidence.get("identity") if isinstance(evidence, Mapping) else None
                if (not isinstance(identity, Mapping)
                        or (identity.get("device"), identity.get("inode")) != before[0]):
                    raise DesktopOperationError("SAVE_COPY_CHANGED", "Windows staged file differs from its verified ACL identity", side_effect_started=True, safe_retry=False)
                digest = __import__("hashlib").sha256()
                size = 0
                while True:
                    block = api.read_chunk(handle, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block); size += len(block)
                after = api.source_fingerprint(handle)
                if (after != before or size != before[1]
                        or api.child_identity(parent, parts[-1]) != before[0]):
                    raise DesktopOperationError("SAVE_COPY_CHANGED", "Windows staged file changed during its pinned hash", side_effect_started=True, safe_retry=False)
                api.verify_named_binding(binding)
                _verify_private_save_copy_directory(stage_root, stage_root_identity)
                final_evidence = verify_private_path_security(path, directory=False,
                                                              expected_identity=dict(identity))
                return {"sha256": digest.hexdigest(), "size_bytes": size,
                        "file_identity": dict(final_evidence["identity"]),
                        "source_security_semantics": final_evidence.get("security_semantics")}
            finally:
                api.close(handle)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DesktopOperationError("SAVE_COPY_CHANGED", "private Save Copy output could not be pinned for hashing", side_effect_started=True, safe_retry=False) from exc
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode)
                or (int(before.st_dev), int(before.st_ino), int(before.st_size)) != current[:3]):
            raise DesktopOperationError("SAVE_COPY_CHANGED", "private Save Copy output changed before hashing", side_effect_started=True, safe_retry=False)
        current_uid = os.getuid()
        named_before_chmod = path.lstat()
        if (int(before.st_uid) != current_uid or int(before.st_nlink) != 1
                or stat.S_ISLNK(named_before_chmod.st_mode)
                or not stat.S_ISREG(named_before_chmod.st_mode)
                or (int(named_before_chmod.st_dev), int(named_before_chmod.st_ino))
                != (int(before.st_dev), int(before.st_ino))):
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "staged file ownership, link count or named binding is unsafe before permission tightening", side_effect_started=True, safe_retry=False)
        os.fchmod(fd, 0o600)
        # fchmod intentionally tightens a newly-created file inside the
        # already-private stage before ownership verification; bind subsequent
        # hash/readback checks to that final metadata state.
        before = os.fstat(fd)
        from ._host_scoped_provider import verify_private_path_security
        evidence = verify_private_path_security(path, directory=False)
        identity = evidence.get("identity") if isinstance(evidence, Mapping) else None
        if (not isinstance(identity, Mapping)
                or (identity.get("device"), identity.get("inode")) != (before.st_dev, before.st_ino)):
            raise DesktopOperationError("SAVE_COPY_CHANGED", "private Save Copy output changed during ownership verification", side_effect_started=True, safe_retry=False)
        digest = __import__("hashlib").sha256()
        size = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block); size += len(block)
        after = os.fstat(fd)
        named_after = path.lstat()
        if (size != before.st_size or (after.st_dev, after.st_ino, after.st_size,
                                       after.st_mtime_ns, after.st_ctime_ns) !=
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                or (named_after.st_dev, named_after.st_ino, named_after.st_size,
                    named_after.st_mtime_ns, named_after.st_ctime_ns) !=
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)):
            raise DesktopOperationError("SAVE_COPY_CHANGED", "private Save Copy output changed during its pinned hash", side_effect_started=True, safe_retry=False)
        _verify_private_save_copy_directory(stage_root, stage_root_identity)
        final_evidence = verify_private_path_security(path, directory=False,
                                                      expected_identity=dict(identity))
        return {"sha256": digest.hexdigest(), "size_bytes": size,
                "file_identity": dict(final_evidence["identity"]),
                "source_security_semantics": final_evidence.get("security_semantics")}
    finally:
        os.close(fd)


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
    revision: int | None = None
    active_model_label: str | None = None


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
        publish_capture: Callable[[str, DesktopDispatchContext, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        publish_save_copy: Callable[[str, DesktopDispatchContext, Path, Path, Path, Mapping[str, Any]], Mapping[str, Any]] | None = None,
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
        self.publish_capture = publish_capture
        self.publish_save_copy = publish_save_copy
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
        if operation == "desktop.capture":
            return ("HOST_CONTROL", "PROJECT_WRITE")
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
        effects_started = False

        def mark_effect() -> None:
            nonlocal effects_started
            self._mark_effect(record["operation_id"])
            effects_started = True

        try:
            result = self._execute_operation(operation, args, record, mark_effect)
            if result.get("success") is True:
                status = "SUCCEEDED"
            elif effects_started:
                execution = result.get("execution")
                error = result.get("error")
                result = {**result,
                          "execution": {**(execution if isinstance(execution, Mapping) else {}), "status": "UNKNOWN"},
                          "error": {**(error if isinstance(error, Mapping) else {}), "safe_retry": False}}
                status = "UNKNOWN"
            elif result.get("execution", {}).get("status") == "UNKNOWN":
                status = "UNKNOWN"
            else:
                status = "FAILED"
        except DesktopOperationError as exc:
            status = "UNKNOWN" if effects_started or exc.side_effect_started else "FAILED"
            result = self._error(exc.code, str(exc), category=exc.category, safe_retry=False if status == "UNKNOWN" else exc.safe_retry,
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
                return self._bind(project_id, token, observation, snapshot, record, context, mark_effect)
            if operation == "desktop.show_model":
                return self._show_model(project_id, token, observation, snapshot, record, context, mark_effect)
            if operation == "desktop.select_node":
                binding, _ = self._verified_binding(project_id, token, observation, context, mark_effect=mark_effect)
                mark_effect()
                value = self.adapter.select_node(observation.identity, args["path"])
                if not isinstance(value, Mapping) or value.get("verified") is not True or value.get("selected_path") != args["path"]:
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the selected NodePath", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({"selected_path": value["selected_path"], "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.capture":
                capture_preflight = getattr(self.adapter, "preflight_capture", None)
                if callable(capture_preflight):
                    # Platform-specific read permission (notably macOS Screen
                    # Recording) must be checked before an active-label
                    # challenge can mutate the managed revision or the
                    # durable effect marker is advanced. capture() rechecks
                    # permission immediately before issuing the platform API.
                    capture_preflight(observation.identity, str(args["region"]))
                binding, _ = self._verified_binding(project_id, token, observation, context, mark_effect=mark_effect)
                mark_effect()
                value = self.adapter.capture(observation.identity, str(args["region"]))
                receipt = self._validate_capture_result(value, str(args["region"]))
                if isinstance(value, Mapping) and isinstance(value.get("image_bytes"), bytes):
                    if value.get("target_identity_fingerprint") != observation.identity.fingerprint():
                        raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured bytes are not bound to the exact leased window identity", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
                    if self.publish_capture is None:
                        raise DesktopOperationError("CAPTURE_SINK_UNAVAILABLE", "capture bytes have no trusted project ArtifactStore sink", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
                    receipt = self.publish_capture(project_id, context, {
                        **dict(value),
                        "model_ref": binding.model_ref.as_dict(),
                        "window_identity_fingerprint": observation.identity.fingerprint(),
                    })
                    if (not isinstance(receipt, Mapping) or not self._valid_sha256(receipt.get("sha256"))
                            or receipt.get("sha256") != value.get("sha256")
                            or receipt.get("size") != len(value["image_bytes"])
                            or not isinstance(receipt.get("artifact_id"), str)):
                        raise DesktopOperationError("CAPTURE_REGISTRATION_UNKNOWN", "ArtifactStore did not verify the exact captured bytes", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                public_value = {key: item for key, item in dict(value).items() if key != "image_bytes"}
                return self._success({**public_value, **dict(receipt), "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.action":
                platform_action_resolver = getattr(self.adapter, "resolve_action", None)
                if callable(platform_action_resolver):
                    plan = platform_action_resolver(observation.identity, args["action"])
                else:
                    plan = self.resolve_action(project_id, args["action"])
                if not isinstance(plan, DesktopActionPlan) or not plan.action_id:
                    raise DesktopOperationError("ACTION_NOT_AUTHORIZED", "trusted action catalog did not resolve this action", category=BLOCKED_PERMISSION, safe_retry=True)
                binding, _ = self._verified_binding(project_id, token, observation, context, mark_effect=mark_effect)
                mark_effect()
                value = self.adapter.perform_action(observation.identity, plan)
                if not isinstance(value, Mapping) or value.get("verified") is not True or value.get("action_id") != plan.action_id:
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the requested action", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({"action_id": plan.action_id, "declared_model_mutation": plan.mutates_model,
                                      "action_result": dict(value), "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.shell_execute":
                artifact = self.resolve_artifact(project_id, str(args["source_artifact"]))
                if (not isinstance(artifact, TrustedSourceArtifact) or artifact.artifact_id != args["source_artifact"]
                        or not self._valid_sha256(artifact.sha256) or artifact.size < 0 or not artifact.provenance
                        or not isinstance(artifact.content, bytes) or len(artifact.content) != artifact.size
                        or sha256(artifact.content).hexdigest() != artifact.sha256):
                    raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "source_artifact has no trusted immutable provenance", category=BLOCKED_PERMISSION, safe_retry=True)
                binding, _ = self._verified_binding(project_id, token, observation, context, mark_effect=mark_effect)
                if binding.model_ref != snapshot.model_ref:
                    raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "expected_model_ref does not match the current verified binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
                mark_effect()
                value = self.adapter.shell_execute(observation.identity, artifact)
                data = dict(value) if isinstance(value, Mapping) else {}
                if (data.get("executed") is not True or data.get("source_sha256") != artifact.sha256
                        or data.get("window_identity_fingerprint") != observation.identity.fingerprint()
                        or not isinstance(data.get("output_sha256"), str) or len(data["output_sha256"]) != 64):
                    raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "Windows Java Shell adapter did not attest source, target window, completion and output", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
                self._verify_binding_after_action(project_id, token, observation, binding, context, side_effect_started=True)
                return self._success({**data, "source_artifact": artifact.artifact_id,
                                      "source_sha256": artifact.sha256,
                                      "source_size": artifact.size,
                                      "model_ref": binding.model_ref.as_dict()}, operation=operation, record=record)
            if operation == "desktop.migrate_standalone":
                return self._migrate(project_id, token, observation, args, record, context, mark_effect)
        raise DesktopOperationError("UNSUPPORTED_OPERATION", f"no Desktop handler for {operation}")

    def _bind(self, project_id: str, token: str, observation: WindowObservation,
              snapshot: ManagedModelSnapshot, record: Mapping[str, Any], context: DesktopDispatchContext,
              mark_effect: Callable[[], None]) -> dict[str, Any]:
        current = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if current.model_ref != snapshot.model_ref or current.server_endpoint != snapshot.server_endpoint or current.model_tag != snapshot.model_tag:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed model identity changed while binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        snapshot = current
        desktop = self._observe_model(observation.identity)
        if desktop.mode == "SERVER":
            self._assert_model_match(desktop, snapshot)
            active_label = None
        elif desktop.mode == "UNVERIFIED" and isinstance(desktop.active_model_label, str) and desktop.active_model_label:
            self._assert_challenge_server_endpoint_match(desktop, snapshot)
            snapshot, active_label = self._challenge_active_model(
                project_id, token, observation.identity, snapshot, context,
                expected_ui_label=desktop.active_model_label, mark_effect=mark_effect,
            )
        else:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop has no exact current-model binding or readable active label", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        binding = _VerifiedBinding(snapshot.model_ref, snapshot.server_endpoint, snapshot.model_tag,
                                   snapshot.fingerprint, self.clock(), snapshot.revision, active_label)
        self._stage_binding(record, token, binding)
        return self._success({
            "desktop_binding_verified": True,
            "binding_evidence": {
                "window_identity": observation.identity.fingerprint(),
                "server_endpoint": snapshot.server_endpoint,
                "model_tag": snapshot.model_tag,
                "model_ref": snapshot.model_ref.as_dict(),
                "fingerprint_at_bind": snapshot.fingerprint,
                "revision": snapshot.revision,
                "active_model_label": active_label,
                "binding_method": "WORKER_LABEL_NONCE_CHALLENGE" if active_label is not None else "EXACT_SERVER_ENDPOINT_AND_MODEL_TAG",
            },
        }, operation="desktop.bind", record=record)

    def _challenge_active_model(self, project_id: str, token: str, identity: WindowIdentity,
                                snapshot: ManagedModelSnapshot, context: DesktopDispatchContext,
                                *, expected_ui_label: str | None = None,
                                mark_effect: Callable[[], None] | None = None) -> tuple[ManagedModelSnapshot, str]:
        """Prove one native UI label against a clean, exact managed ModelRef.

        The only mutation is a nonce label set and an exact restoration through
        the existing serialized Worker callback.  Any uncertain post-dispatch
        result remains fenced by ExecutionService and this parent OperationStore
        operation; this code never guesses that restoration succeeded.
        """
        if type(snapshot.revision) is not int or snapshot.revision < 0:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "native label challenge requires an exact managed revision", safe_retry=True)
        before = self._engine_call("desktop.binding.label_get", {
            "project_id": project_id, "expected_revision": snapshot.revision,
        }, snapshot.model_ref, context, mutating=False)
        original_label = self._response_data(before).get("active_model_label")
        if not isinstance(original_label, str) or not original_label:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed Worker did not return the current model label", safe_retry=True)
        if expected_ui_label is not None and original_label != expected_ui_label:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop active label differs from the exact managed ModelRef label", safe_retry=True)

        nonce = "MCPBIND:" + secrets.token_hex(16)
        if mark_effect is not None:
            mark_effect()
        set_result = self._engine_call("desktop.binding.label_set", {
            "project_id": project_id, "expected_revision": snapshot.revision, "label": nonce,
        }, snapshot.model_ref, context, mutating=True)
        revision_after_set = self._response_data(set_result).get("revision")
        if type(revision_after_set) is not int or revision_after_set != snapshot.revision + 1:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Worker label challenge did not advance the exact managed revision", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)

        try:
            current = self._revalidate(self._get_lease(token))
            if current.identity != identity:
                raise DesktopOperationError("WINDOW_IDENTITY_CHANGED", "Desktop window identity changed during label challenge", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
            challenge_readback = self._observe_model(current.identity).active_model_label
            if challenge_readback != nonce:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "current Desktop label did not follow the exact Worker nonce", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
        finally:
            restore_result = self._engine_call("desktop.binding.label_set", {
                "project_id": project_id, "expected_revision": revision_after_set, "label": original_label,
            }, snapshot.model_ref, context, mutating=True)
            restored_revision = self._response_data(restore_result).get("revision")
            if type(restored_revision) is not int or restored_revision != revision_after_set + 1:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Worker did not verify the original label restoration revision", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
            restored_observation = self._revalidate(self._get_lease(token))
            if restored_observation.identity != identity:
                raise DesktopOperationError("WINDOW_IDENTITY_CHANGED", "Desktop window identity changed during label restoration", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
            restored_label = self._observe_model(restored_observation.identity).active_model_label
            if restored_label != original_label:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop did not read back the restored active model label", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)

        final = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if (final.model_ref != snapshot.model_ref or final.fingerprint != snapshot.fingerprint
                or final.revision != snapshot.revision + 2):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "model identity or fingerprint changed while restoring the active label challenge", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
        return final, original_label

    def _show_model(self, project_id: str, token: str, observation: WindowObservation,
                    snapshot: ManagedModelSnapshot, record: Mapping[str, Any], context: DesktopDispatchContext,
                    mark_effect: Callable[[], None]) -> dict[str, Any]:
        before = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if before.model_ref != snapshot.model_ref or before.fingerprint != snapshot.fingerprint:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed before show_model", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        show_preflight = getattr(self.adapter, "preflight_show_model", None)
        if callable(show_preflight):
            show_preflight(observation.identity, before)
        mark_effect()
        value = self.adapter.show_model(observation.identity, snapshot)
        if not isinstance(value, Mapping) or value.get("verified") is not True:
            raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "adapter did not verify the displayed server model", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        after_observation = self._revalidate(self._get_lease(token))
        after = self._observe_model(after_observation.identity)
        managed_after = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, snapshot.model_ref, context)
        if managed_after.model_ref != snapshot.model_ref or managed_after.fingerprint != snapshot.fingerprint:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed while showing the model", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        active_label = None
        if after.mode == "SERVER":
            self._assert_model_match(after, snapshot, side_effect_started=True)
        elif after.mode == "UNVERIFIED" and isinstance(after.active_model_label, str) and after.active_model_label:
            self._assert_challenge_server_endpoint_match(after, managed_after, side_effect_started=True)
            managed_after, active_label = self._challenge_active_model(
                project_id, token, after_observation.identity, managed_after, context,
                expected_ui_label=after.active_model_label,
            )
        else:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop current model could not be verified after show_model", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
        binding = _VerifiedBinding(snapshot.model_ref, snapshot.server_endpoint, snapshot.model_tag,
                                   snapshot.fingerprint, self.clock(), managed_after.revision, active_label)
        self._stage_binding(record, token, binding)
        return self._success({"desktop_binding_verified": True, "model_ref": snapshot.model_ref.as_dict(),
                              "server_endpoint": snapshot.server_endpoint, "model_tag": snapshot.model_tag,
                              "binding_method": "WORKER_LABEL_NONCE_CHALLENGE" if active_label is not None else "EXACT_SERVER_ENDPOINT_AND_MODEL_TAG",
                              "active_model_label": active_label, "revision": managed_after.revision},
                             operation="desktop.show_model", record=record)

    def _verified_binding(self, project_id: str, token: str, observation: WindowObservation,
                          context: DesktopDispatchContext, *,
                          mark_effect: Callable[[], None] | None = None) -> tuple[_VerifiedBinding, ManagedModelSnapshot]:
        with self._lease_lock:
            binding = self._bindings.get(token)
        if binding is None:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "window has no verified ModelRef binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        desktop = self._observe_model(observation.identity)
        current = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, binding.model_ref, context)
        if current.model_ref != binding.model_ref or current.fingerprint != binding.fingerprint_at_bind:
            with self._lease_lock:
                self._bindings.pop(token, None)
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef, generation, or fingerprint changed since binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        if desktop.mode == "SERVER":
            if desktop.server_endpoint != binding.server_endpoint or desktop.model_tag != binding.model_tag:
                with self._lease_lock:
                    self._bindings.pop(token, None)
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "current Desktop model observation no longer matches the verified binding", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        elif desktop.mode == "UNVERIFIED" and binding.active_model_label is not None:
            self._assert_challenge_server_endpoint_match(desktop, current)
            if mark_effect is None:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "native binding verification requires a durable effect marker", safe_retry=True)
            challenged, active_label = self._challenge_active_model(
                project_id, token, observation.identity, current, context,
                expected_ui_label=desktop.active_model_label, mark_effect=mark_effect,
            )
            if active_label != binding.active_model_label:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "active Desktop model changed since the previous binding proof", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
            binding = _VerifiedBinding(binding.model_ref, binding.server_endpoint, binding.model_tag,
                                       binding.fingerprint_at_bind, self.clock(), challenged.revision,
                                       binding.active_model_label)
            with self._lease_lock:
                self._bindings[token] = binding
        else:
            with self._lease_lock:
                self._bindings.pop(token, None)
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "current Desktop model binding is no longer observable", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        return binding, current

    def _verify_binding_after_action(self, project_id: str, token: str, observation: WindowObservation,
                                     binding: _VerifiedBinding, context: DesktopDispatchContext,
                                     *, side_effect_started: bool) -> None:
        try:
            current = self._revalidate(self._get_lease(token))
            desktop = self._observe_model(current.identity)
            model = self._engine_snapshot("desktop.binding.inspect", {"project_id": project_id}, binding.model_ref, context)
            if model.model_ref != binding.model_ref or model.fingerprint != binding.fingerprint_at_bind:
                raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "managed ModelRef or fingerprint changed after the Desktop action", category=MODEL_BINDING_UNKNOWN, side_effect_started=side_effect_started)
            if desktop.mode == "SERVER":
                self._assert_model_match(desktop, ManagedModelSnapshot(binding.model_ref, binding.server_endpoint, binding.model_tag, binding.fingerprint_at_bind), side_effect_started=side_effect_started)
            elif desktop.mode == "UNVERIFIED" and binding.active_model_label is not None:
                self._assert_challenge_server_endpoint_match(
                    desktop,
                    ManagedModelSnapshot(binding.model_ref, binding.server_endpoint,
                                         binding.model_tag, binding.fingerprint_at_bind),
                    side_effect_started=side_effect_started,
                )
                _challenged, active_label = self._challenge_active_model(
                    project_id, token, current.identity, model, context,
                    expected_ui_label=desktop.active_model_label,
                )
                if active_label != binding.active_model_label:
                    raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop current model changed while the action ran", category=MODEL_BINDING_UNKNOWN, side_effect_started=side_effect_started, safe_retry=False)
            else:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Desktop current model is no longer observable after the action", category=MODEL_BINDING_UNKNOWN, side_effect_started=side_effect_started, safe_retry=False)
        except DesktopOperationError as exc:
            if side_effect_started:
                exc.side_effect_started = True
            raise

    def _wait_for_save_copy_completion(self, token: str, identity: WindowIdentity,
                                       *, timeout_s: float = 120.0) -> None:
        observer = getattr(self.adapter, "save_copy_completion_state", None)
        if not callable(observer):
            raise DesktopOperationError(
                "SAVE_COPY_COMPLETION_UNAVAILABLE",
                "native adapter has no observed Save Copy completion channel",
                category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False,
            )
        deadline = time.monotonic() + timeout_s
        busy_seen = False
        while time.monotonic() < deadline:
            try:
                current = self._revalidate(self._get_lease(token))
                if current.identity != identity:
                    raise DesktopOperationError(
                        "WINDOW_IDENTITY_CHANGED", "Desktop window identity changed during Save Copy",
                        category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False,
                    )
                state = observer(identity, side_effect_started=True)
            except DesktopOperationError as exc:
                exc.side_effect_started = True
                exc.safe_retry = False
                if exc.category is None:
                    exc.category = MODEL_BINDING_UNKNOWN
                raise
            if state == "busy":
                busy_seen = True
            elif state == "complete" and busy_seen:
                return
            elif state not in {"idle", "busy", "complete"}:
                raise DesktopOperationError(
                    "SAVE_COPY_COMPLETION_UNKNOWN", "profile returned an unrecognized Save Copy state",
                    category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False,
                )
            time.sleep(0.2)
        raise DesktopOperationError(
            "SAVE_COPY_COMPLETION_UNKNOWN",
            "Save Copy did not produce the observed busy-to-complete transition within the bound",
            category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False,
        )

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
        if self.publish_save_copy is None:
            raise DesktopOperationError("SAVE_COPY_PUBLISH_UNAVAILABLE", "migration requires the bound ArtifactStore no-clobber publisher", category=UNSUPPORTED_CONTROL, safe_retry=True)
        stage_root, stage_root_identity = _create_private_save_copy_stage(context)
        stage_path = stage_root / "model.mph"
        if stage_path.exists() or stage_path.is_symlink():
            raise DesktopOperationError("SAVE_COPY_STAGE_UNKNOWN", "private Save Copy staging leaf was not new", category=MODEL_BINDING_UNKNOWN, safe_retry=True)
        completion_observer = getattr(self.adapter, "save_copy_completion_state", None)
        if not callable(completion_observer):
            raise DesktopOperationError(
                "SAVE_COPY_COMPLETION_UNAVAILABLE",
                "native adapter has no observed Save Copy completion channel",
                category=UNSUPPORTED_CONTROL, safe_retry=True,
            )
        initial_completion_state = completion_observer(observation.identity, side_effect_started=False)
        if initial_completion_state != "idle":
            raise DesktopOperationError(
                "SAVE_COPY_NOT_IDLE", "profiled Save Copy control is not in its observed idle state",
                category=UNSUPPORTED_CONTROL, safe_retry=True,
            )
        mark_effect()
        copy = self.adapter.save_copy(observation.identity, stage_path, source)
        if (not isinstance(copy, SavedCopy) or Path(copy.path) != stage_path
                or (copy.sha256 is not None and not self._valid_sha256(copy.sha256))):
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "adapter did not return the exact private staging path and an optional valid SHA-256", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if not copy.source_preserved or copy.source_dirty_after != source.dirty or copy.source_fingerprint_after != source.fingerprint:
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "save-copy changed or discarded the standalone source state", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        self._wait_for_save_copy_completion(token, observation.identity)
        source_preservation = self.adapter.verify_source_preserved(observation.identity, source)
        if (not isinstance(source_preservation, SourcePreservation) or not source_preservation.still_open
                or source_preservation.standalone_model_id != source.standalone_model_id
                or source_preservation.fingerprint != source.fingerprint
                or source_preservation.dirty != source.dirty):
            raise DesktopOperationError(
                "SOURCE_MODEL_NOT_PRESERVED", "source identity or state changed before staged bytes could be trusted",
                category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False,
            )
        stage_receipt = _verified_private_save_copy_file(
            stage_root, stage_path, stage_root_identity, completion_verified=True,
        )
        if ((copy.sha256 is not None and stage_receipt["sha256"] != copy.sha256)
                or (copy.size_bytes is not None and copy.size_bytes != stage_receipt["size_bytes"])):
            raise DesktopOperationError("SAVE_COPY_HASH_MISMATCH", "saved copy bytes differ from the pinned native receipt", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        published = self.publish_save_copy(
            project_id, context, stage_root, stage_path, destination,
            {**stage_receipt, "stage_root_identity": dict(stage_root_identity)},
        )
        if (not isinstance(published, Mapping) or published.get("path") != str(destination)
                or published.get("sha256") != stage_receipt["sha256"]
                or published.get("size_bytes") != stage_receipt["size_bytes"]
                or published.get("operation_id") != context.operation_id
                or published.get("publication") != "ARTIFACTSTORE_NO_CLOBBER"):
            raise DesktopOperationError("SAVE_COPY_PUBLISH_UNKNOWN", "ArtifactStore did not verify the exact staged bytes under this durable migration claim", category=MODEL_BINDING_UNKNOWN, side_effect_started=True, safe_retry=False)
        loaded = self._engine_call("model.load", {
            "project_id": project_id,
            "target_session_id": str(args["target_session_id"]),
            "model_path": str(destination),
            "source_sha256": stage_receipt["sha256"],
            "load_mode": "save_copy",
        }, None, context, mutating=True)
        snapshot = self._snapshot_from_response(loaded)
        if snapshot.model_ref.session_id != args["target_session_id"]:
            raise DesktopOperationError("MODEL_IDENTITY_MISMATCH", "loaded ModelRef belongs to a different target session", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        final_observation = self._revalidate(self._get_lease(token))
        final_model = self._observe_model(final_observation.identity)
        if (final_model.mode != "STANDALONE" or final_model.standalone_model_id != source.standalone_model_id
                or final_model.active_model_label != source.active_model_label
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
        self._cleanup_save_copy_stage(stage_root, stage_root_identity, stage_receipt["file_identity"])
        return self._success({
            "migration_mode": "explicit_save_copy",
            "source_window_identity": final_observation.identity.fingerprint(),
            "source_standalone_model_id": source.standalone_model_id,
            "source_fingerprint": source.fingerprint,
            "source_dirty_preserved": source.dirty,
            "saved_copy": {"path": str(destination), "sha256": stage_receipt["sha256"],
                           "size_bytes": stage_receipt["size_bytes"],
                           "file_identity": published.get("file_identity"),
                           "operation_id": context.operation_id},
            "target_model_ref": snapshot.model_ref.as_dict(),
            "target_model_tag": snapshot.model_tag,
            "target_server_endpoint": snapshot.server_endpoint,
            "target_model_fingerprint": snapshot.fingerprint,
            "managed_model_loaded": True,
            "desktop_binding_verified": False,
            "source_window_preserved": True,
        }, operation="desktop.migrate_standalone", record=record)

    def _cleanup_save_copy_stage(self, stage_root: Path, expected_identity: Mapping[str, Any],
                                 expected_file_identity: Mapping[str, Any]) -> None:
        """Remove a successful private POSIX scratch via pinned directory handles."""
        if os.name == "nt":
            try:
                from ._windows_parameter_publish import delete_exact_private_stage
                delete_exact_private_stage(
                    stage_root, dict(expected_identity), dict(expected_file_identity),
                )
            except Exception:
                # A changed name, ACL, link count or non-empty directory is
                # left in place; deletion never falls back to a re-resolved
                # path operation.
                return
            return
        parent_fd = stage_fd = None
        try:
            _verify_private_save_copy_directory(stage_root, expected_identity)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            parent_fd = os.open(stage_root.parent, flags)
            parent_before = os.fstat(parent_fd)
            stage_fd = os.open(stage_root.name, flags, dir_fd=parent_fd)
            stage_before = os.fstat(stage_fd)
            if (not stat.S_ISDIR(stage_before.st_mode)
                    or (int(stage_before.st_dev), int(stage_before.st_ino)) !=
                    (expected_identity.get("device"), expected_identity.get("inode"))):
                return
            named_stage = os.stat(stage_root.name, dir_fd=parent_fd, follow_symlinks=False)
            if ((named_stage.st_dev, named_stage.st_ino) != (stage_before.st_dev, stage_before.st_ino)
                    or not stat.S_ISDIR(named_stage.st_mode)):
                return
            try:
                staged = os.stat("model.mph", dir_fd=stage_fd, follow_symlinks=False)
            except FileNotFoundError:
                staged = None
            if staged is not None:
                if (not stat.S_ISREG(staged.st_mode)
                        or (int(staged.st_dev), int(staged.st_ino)) !=
                        (expected_file_identity.get("device"), expected_file_identity.get("inode"))):
                    return
                os.unlink("model.mph", dir_fd=stage_fd)
                os.fsync(stage_fd)
            _verify_private_save_copy_directory(stage_root, expected_identity)
            current_parent = os.stat(stage_root.parent, follow_symlinks=False)
            current_stage = os.stat(stage_root.name, dir_fd=parent_fd, follow_symlinks=False)
            if ((current_parent.st_dev, current_parent.st_ino) != (parent_before.st_dev, parent_before.st_ino)
                    or (current_stage.st_dev, current_stage.st_ino) != (stage_before.st_dev, stage_before.st_ino)):
                return
            os.rmdir(stage_root.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except Exception:
            # Leaving a verified private residue is safer than deleting an
            # object that changed during cleanup.
            return
        finally:
            if stage_fd is not None:
                os.close(stage_fd)
            if parent_fd is not None:
                os.close(parent_fd)

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

    def _assert_challenge_server_endpoint_match(
        self, desktop: DesktopModelObservation, managed: ManagedModelSnapshot,
        *, side_effect_started: bool = False,
    ) -> None:
        """Require native endpoint evidence before using the active-label challenge.

        A Worker label nonce proves the UI is showing the managed model only
        after the UI's observed server endpoint has independently matched the
        managed endpoint.  Label-only observations cannot establish that
        server-bound identity.
        """
        endpoint = desktop.server_endpoint
        if not isinstance(endpoint, str) or not endpoint or endpoint != managed.server_endpoint:
            raise DesktopOperationError(
                "MODEL_BINDING_UNKNOWN",
                "active-model label challenge requires an exact observed Desktop server endpoint match",
                category=MODEL_BINDING_UNKNOWN,
                side_effect_started=side_effect_started,
                safe_retry=not side_effect_started,
            )

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
        revision = data.get("revision")
        if revision is not None and (type(revision) is not int or revision < 0):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed engine returned an invalid model revision", category=MODEL_BINDING_UNKNOWN)
        if not all(isinstance(item, str) and item for item in (endpoint, model_tag, fingerprint)) or model_tag != model_ref.model_tag:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "managed engine snapshot lacks endpoint, matching model tag, or fingerprint", category=MODEL_BINDING_UNKNOWN)
        return ManagedModelSnapshot(model_ref, endpoint, model_tag, fingerprint, revision)

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

    def _validate_capture_result(self, value: Mapping[str, Any], region: str) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("verified") is not True:
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "adapter did not verify a capture result", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        if value.get("region") != region or value.get("scope") != "target_window":
            raise DesktopOperationError("CAPTURE_SCOPE_REJECTED", "capture result did not prove the requested target-window scope", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        raw = value.get("image_bytes")
        if raw is not None:
            width, height = value.get("width"), value.get("height")
            if (not isinstance(raw, bytes) or not raw or len(raw) > 64 * 1024 * 1024
                    or type(value.get("size")) is not int or len(raw) != value.get("size")
                    or type(width) is not int or type(height) is not int
                    or width <= 0 or height <= 0 or width > 8192 or height > 8192 or width * height > 12_000_000
                    or value.get("format") not in {"PNG", "BMP"}
                    or not self._valid_sha256(value.get("sha256"))
                    or sha256(raw).hexdigest() != value.get("sha256")):
                raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured bytes do not match their exact size and SHA-256", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
            if value["format"] == "PNG":
                self._validate_png(raw, width, height)
            else:
                try:
                    from ._desktop_capture import _bmp_dimensions
                    actual_dimensions = _bmp_dimensions(raw)
                except Exception as exc:
                    raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured BMP header or dimensions failed validation", category=MODEL_BINDING_UNKNOWN, side_effect_started=True) from exc
                if actual_dimensions != (width, height):
                    raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured BMP dimensions differ from the adapter receipt", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
            if not self._valid_sha256(value.get("target_identity_fingerprint")):
                raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured image lacks a target window identity fingerprint", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
            return {"artifact_id": None, "sha256": value["sha256"], "size": len(raw)}
        if not isinstance(value.get("artifact_id"), str) or not value.get("artifact_id") or not self._valid_sha256(value.get("sha256")):
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "capture result requires an artifact id and SHA-256", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        return {"artifact_id": value["artifact_id"], "sha256": value["sha256"], "size": value.get("size")}

    @staticmethod
    def _validate_png(data: bytes, width: int, height: int) -> None:
        """Check a complete PNG chunk stream and every CRC before registration."""
        import struct
        import zlib

        if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured image does not have the PNG signature", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)
        offset = 8
        first = True
        saw_idat = False
        ended = False
        while offset < len(data):
            if offset + 12 > len(data):
                break
            size = struct.unpack_from(">I", data, offset)[0]
            end = offset + 12 + size
            if end > len(data):
                break
            chunk_type = data[offset + 4:offset + 8]
            chunk = data[offset + 8:offset + 8 + size]
            expected_crc = struct.unpack_from(">I", data, offset + 8 + size)[0]
            if zlib.crc32(chunk_type + chunk) & 0xffffffff != expected_crc:
                break
            if first:
                if chunk_type != b"IHDR" or size != 13:
                    break
                actual_width, actual_height = struct.unpack_from(">II", chunk, 0)
                if (actual_width, actual_height) != (width, height):
                    break
                first = False
            elif chunk_type == b"IHDR":
                break
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                if size != 0 or end != len(data) or not saw_idat:
                    break
                ended = True
                offset = end
                break
            offset = end
        if first or not saw_idat or not ended or offset != len(data):
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "captured PNG has invalid chunk boundaries, CRCs, dimensions, or IEND", category=MODEL_BINDING_UNKNOWN, side_effect_started=True)

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
