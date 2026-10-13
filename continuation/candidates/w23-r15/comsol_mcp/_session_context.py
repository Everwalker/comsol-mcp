"""Explicit per-session Worker state and conservative endpoint scheduling.

This module owns runtime handles only; durable lifecycle authority remains in
OperationStore/ProjectAuthority.  A SessionRuntimeContext is created from that
authority and is never selected by swapping process globals or environment
variables.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import hashlib
import ipaddress
import os
from pathlib import Path
import threading
from typing import Any, Callable, ClassVar, Iterator


class SessionContextError(RuntimeError):
    """Base exception for fail-closed session routing and lifecycle errors."""


class SessionContextMissing(SessionContextError):
    """A managed route accessed Worker state without an explicit session scope."""


class SessionIdentityUnknown(SessionContextError):
    """The Worker connection has no actual socket observation to schedule."""


class SessionIdentityConflict(SessionContextError):
    """The same observed socket was attributed to two live process births."""


class SessionSchedulerClosed(SessionContextError):
    """A closed scheduler cannot accept new work."""


class SessionBindingBusy(SessionContextError):
    """A session binding cannot retire while its accepted work is pending."""


class SessionRegistryConflict(SessionContextError):
    """A live runtime handle conflicts with the registered project/session key."""


def _validate_endpoint(host: str, port: int) -> None:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("endpoint host is required")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("endpoint port must be an integer in range")


@dataclass(frozen=True, order=True)
class CanonicalSocket:
    """Observed TCP peer/listener address and port, never a requested DNS name."""

    address: str
    port: int

    def __post_init__(self) -> None:
        if not isinstance(self.address, str) or not self.address.strip():
            raise ValueError("socket address is required")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("socket port must be an integer in range")
        try:
            normalized = ipaddress.ip_address(self.address.strip().strip("[]")).compressed
        except ValueError:
            raise ValueError("observed socket address must be an IP literal") from None
        object.__setattr__(self, "address", normalized)

    @property
    def lock_key(self) -> str:
        address = f"[{self.address}]" if ":" in self.address else self.address
        return f"socket:{address}:{self.port}"


@dataclass(frozen=True)
class OwnedServerProcessIdentity:
    """Host-adapter evidence for a specific owned process birth and listeners.

    Production callers must construct this only from a complete process-birth
    and listener inventory.  A PID, command name, or caller-provided identity
    string alone is not enough to enable a parallel server lane.
    """

    pid: int
    birth: str
    executable: str
    listener_sockets: tuple[CanonicalSocket, ...]
    start_epoch_ms: int | None = None

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("owned server PID must be a positive integer")
        if not isinstance(self.birth, str) or not self.birth or not isinstance(self.executable, str) or not self.executable:
            raise ValueError("owned process identity requires birth and executable")
        if (type(self.listener_sockets) is not tuple or not self.listener_sockets
                or any(not isinstance(sock, CanonicalSocket) for sock in self.listener_sockets)):
            raise ValueError("owned process identity requires an immutable listener socket tuple")
        if self.start_epoch_ms is not None and (type(self.start_epoch_ms) is not int or self.start_epoch_ms <= 0):
            raise ValueError("owned process start_epoch_ms must be a positive integer or null")

    def attests_peer(self, peer: CanonicalSocket) -> bool:
        """Match exact listeners or same-family wildcard listeners at this port."""
        peer_ip = ipaddress.ip_address(peer.address)
        for listener in self.listener_sockets:
            if listener.port != peer.port:
                continue
            listener_ip = ipaddress.ip_address(listener.address)
            if listener.address == peer.address:
                return True
            if listener_ip.is_unspecified and listener_ip.version == peer_ip.version:
                return True
        return False

    @property
    def lock_key(self) -> str:
        return f"process:{self.pid}:{self.birth}"


@dataclass(frozen=True)
class SessionRuntimeConfig:
    """Immutable COMSOL/Java identity and classpath for one Worker lifetime."""

    runtime_id: str
    comsol_version: str
    installation_root: Path
    java_executable: Path
    classpath: tuple[Path, ...]
    preferences_dir: Path
    session_state_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_id, str) or not self.runtime_id.strip():
            raise ValueError("runtime_id is required")
        if not isinstance(self.comsol_version, str) or not self.comsol_version.strip():
            raise ValueError("COMSOL version is required")
        if type(self.classpath) is not tuple or not self.classpath or any(
            not isinstance(path, Path) for path in self.classpath
        ):
            raise ValueError("Worker classpath must be an actual non-empty tuple of paths")
        if any(not isinstance(path, Path) for path in (
            self.installation_root, self.java_executable, self.preferences_dir,
            self.session_state_root,
        )):
            raise ValueError("runtime paths must be pathlib.Path values")


@dataclass(frozen=True)
class SessionEndpointIdentity:
    """Requested endpoint, Worker epoch, and independently observed socket/process evidence."""

    host: str
    port: int
    worker_epoch: int
    observed_peer: CanonicalSocket | None = None
    owned_process: OwnedServerProcessIdentity | None = None

    def __post_init__(self) -> None:
        _validate_endpoint(self.host, self.port)
        if type(self.worker_epoch) is not int or self.worker_epoch < 1:
            raise ValueError("Worker connection epoch must be a positive integer")
        if self.observed_peer is not None and not isinstance(self.observed_peer, CanonicalSocket):
            raise ValueError("actual peer socket observation is malformed")
        if self.owned_process is not None:
            if not isinstance(self.owned_process, OwnedServerProcessIdentity):
                raise ValueError("owned process evidence is malformed")
            if self.observed_peer is None or not self.owned_process.attests_peer(self.observed_peer):
                raise ValueError("owned process evidence does not attest the observed peer socket")

    @property
    def socket_lock_key(self) -> str:
        if self.observed_peer is None:
            raise SessionIdentityUnknown("actual observed peer socket is required before scheduling")
        return self.observed_peer.lock_key


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    workflow_file: Path
    status_file: Path
    outputs_dir: Path
    operations_file: Path
    server_log: Path


def session_state_directory(state_root: Path, project_id: str, session_id: str) -> Path:
    """Return the hash-scoped project/session directory after symlink checks."""
    if not isinstance(state_root, Path):
        raise SessionContextError("session state root must be a Path")
    if not isinstance(project_id, str) or not project_id or not isinstance(session_id, str) or not session_id:
        raise SessionContextError("project and session identities are required")
    if state_root.is_symlink():
        raise SessionContextError("configured session state root cannot be a symlink")
    root = state_root.resolve()
    sessions_root = root / "sessions"
    if sessions_root.is_symlink():
        raise SessionContextError("session state root cannot use a symlink directory")
    digest = hashlib.sha256(f"{project_id}\0{session_id}".encode("utf-8")).hexdigest()
    candidate = sessions_root / digest
    if candidate.is_symlink():
        raise SessionContextError("session directory cannot be a symlink")
    home = candidate.resolve()
    if not home.is_relative_to(root) or home.parent != sessions_root.resolve():
        raise SessionContextError("derived session directory escapes its state root")
    return home


def runtime_state_root(control_home: Path, *, platform_name: str | None = None) -> Path:
    """Select this control home’s session-state layout without migrating it.

    New Windows homes use the short ``s`` directory because COMSOL creates
    recovery files below the owned Server tree. Existing legacy homes retain
    ``session-runtime-state``. If both layouts exist, callers cannot safely
    infer which one owns a persisted session, so selection fails closed.
    """
    if not isinstance(control_home, Path):
        raise SessionContextError("control home must be a Path")
    name = platform_name if platform_name is not None else os.name
    is_windows = str(name).casefold() in {"nt", "win32", "windows"}
    legacy = control_home / "session-runtime-state"
    if not is_windows:
        if legacy.is_symlink():
            raise SessionContextError("legacy session-state root cannot be a symlink")
        return legacy

    short = control_home / "s"
    for candidate in (legacy, short):
        if candidate.is_symlink():
            raise SessionContextError("session-state root cannot be a symlink")
        if candidate.exists() and not candidate.is_dir():
            raise SessionContextError("session-state root must be a directory")
    legacy_exists = legacy.is_dir()
    short_exists = short.is_dir()
    if legacy_exists and short_exists:
        raise SessionContextError("legacy and short session-state roots are ambiguous")
    if legacy_exists:
        return legacy
    return short


@dataclass
class SessionRuntimeContext:
    """Mutable Worker/model/job state for one authoritative project session."""

    # These values define the Worker binding and project authority for every
    # queued operation.  Reconnects create a new context with a new Worker
    # epoch; mutating these fields in place could make an old-lane callback
    # reach a replacement Worker.
    _IMMUTABLE_BINDING_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "project_id", "session_id", "project_root", "runtime", "endpoint",
        "backend", "worker", "service", "client", "server_handle", "server_ownership",
        "process_identity", "remote_client_factory", "worker_instance_id",
    })

    project_id: str
    session_id: str
    project_root: Path
    runtime: SessionRuntimeConfig
    endpoint: SessionEndpointIdentity
    backend: Any = None
    worker_instance_id: str = ""
    worker: Any = None
    service: Any = None
    client: Any = None
    server_handle: Any = None
    server_ownership: str = "shared"
    process_identity: OwnedServerProcessIdentity | None = None
    remote_client_factory: Any = None
    client_connected: bool = False
    client_retired: bool = False
    connected_host: str = ""
    connected_port: int | None = None
    server_started_by_mcp: bool = False
    current_model: Any = None
    current_model_origin: str = ""
    current_model_path: str = ""
    last_command: str = ""
    last_error: str = ""
    owned_model_tags: set[str] = field(default_factory=set)
    background_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflow_state: dict[str, Any] = field(default_factory=dict)
    health_snapshot: dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    background_jobs_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    _INITIAL_BINDING_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "backend", "worker", "service", "client", "server_handle",
    })

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._IMMUTABLE_BINDING_FIELDS and name in self.__dict__:
            previous = self.__dict__[name]
            if name in self._INITIAL_BINDING_FIELDS and previous is None and value is not None:
                object.__setattr__(self, name, value)
                return
            raise SessionContextError(
                f"{name} is immutable for a Worker binding; create a new session context"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValueError("project_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(self.project_root, Path):
            raise ValueError("project_root must be the registered workspace Path")
        if not isinstance(self.runtime, SessionRuntimeConfig):
            raise ValueError("session requires an immutable runtime config")
        if not isinstance(self.endpoint, SessionEndpointIdentity):
            raise ValueError("session requires endpoint identity evidence")
        if not self.worker_instance_id:
            object.__setattr__(self, "worker_instance_id", self.runtime.runtime_id)
        if not isinstance(self.worker_instance_id, str) or not self.worker_instance_id:
            raise ValueError("session requires an exact Worker instance identity")
        if self.server_ownership not in {"shared", "mcp_managed", "user_owned"}:
            raise ValueError("invalid server ownership")

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_id, self.session_id)

    @property
    def worker_epoch(self) -> int:
        return self.endpoint.worker_epoch

    @property
    def worker_binding_key(self) -> str:
        """Exact in-process Worker object and connection epoch.

        ``runtime_id``/``worker_instance_id`` are descriptive installation or
        protocol metadata and are not sufficient proof that two sessions use
        the same live Worker.  The retained Worker object reference is the
        daemon's exact binding; keeping it in this context also prevents its
        object identity from being recycled while the context is registered.
        A context without a Worker is deliberately isolated from all others.
        """
        worker_identity = id(self.worker) if self.worker is not None else id(self)
        return f"python-worker-object:{worker_identity}:epoch:{self.worker_epoch}"

    @property
    def paths(self) -> SessionPaths:
        """Return per-project/session files without interpolating untrusted IDs."""
        configured_state_root = self.runtime.session_state_root
        state_root = configured_state_root.resolve()
        home = session_state_directory(configured_state_root, self.project_id, self.session_id)
        for child in (
            home / "logs", home / "outputs", home / "workflow_state.json",
            home / "status.json", home / "workflow_state.json.tmp",
            home / "status.json.tmp", home / "logs" / "operations.jsonl",
            home / "logs" / "server.log",
        ):
            if child.is_symlink():
                raise SessionContextError("session state paths cannot use symlinks")
        return SessionPaths(
            root=home,
            workflow_file=home / "workflow_state.json",
            status_file=home / "status.json",
            outputs_dir=home / "outputs",
            operations_file=home / "logs" / "operations.jsonl",
            server_log=home / "logs" / "server.log",
        )


_CURRENT_SESSION: ContextVar[SessionRuntimeContext | None] = ContextVar(
    "comsol_mcp_current_session_context", default=None,
)


@contextmanager
def use_session_context(context: SessionRuntimeContext) -> Iterator[SessionRuntimeContext]:
    """Select one exact project/session runtime for this task and its children."""
    if not isinstance(context, SessionRuntimeContext):
        raise TypeError("an explicit SessionRuntimeContext is required")
    token = _CURRENT_SESSION.set(context)
    try:
        yield context
    finally:
        _CURRENT_SESSION.reset(token)


def current_session_context() -> SessionRuntimeContext:
    context = _CURRENT_SESSION.get()
    if context is None:
        raise SessionContextMissing("managed runtime access requires an explicit session context")
    return context


def active_session_context() -> SessionRuntimeContext | None:
    """Return the selected context for legacy-compatible state accessors."""
    return _CURRENT_SESSION.get()


def capture_session_callback(callback: Callable[..., Any]) -> Callable[..., Any]:
    """Carry the exact active session into Java callbacks and background threads."""
    current_session_context()
    captured = copy_context()
    return lambda *args, **kwargs: captured.copy().run(callback, *args, **kwargs)


class SessionRuntimeRegistry:
    """In-memory Worker-handle index; durable authority remains OperationStore."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._contexts: dict[tuple[str, str], SessionRuntimeContext] = {}

    def register(self, context: SessionRuntimeContext) -> SessionRuntimeContext:
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("registry accepts only SessionRuntimeContext values")
        with self._lock:
            current = self._contexts.get(context.key)
            if current is not None and current is not context:
                raise SessionRegistryConflict("project/session already has a live runtime context")
            self._contexts[context.key] = context
        return context

    def get(self, project_id: str, session_id: str) -> SessionRuntimeContext:
        with self._lock:
            context = self._contexts.get((project_id, session_id))
        if context is None:
            raise SessionContextMissing("project/session has no live runtime context")
        return context

    def list_for_project(self, project_id: str) -> tuple[SessionRuntimeContext, ...]:
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id is required")
        with self._lock:
            return tuple(
                context for (owner, _session), context in self._contexts.items()
                if owner == project_id
            )

    def remove(self, project_id: str, session_id: str, *, expected: SessionRuntimeContext) -> None:
        """Remove only the exact handle that was previously registered."""
        key = (project_id, session_id)
        with self._lock:
            current = self._contexts.get(key)
            if current is not expected:
                raise SessionRegistryConflict("runtime context changed before exact removal")
            del self._contexts[key]


class _ReadWriteGate:
    """Known process lanes share; uncertain identity excludes every lane."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextmanager
    def known_server_lease(self) -> Iterator[None]:
        with self._condition:
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def unknown_server_exclusive_lease(self) -> Iterator[None]:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


class SessionEndpointScheduler:
    """FIFO per proven process birth, with one global exclusive unknown lane."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._owned_lanes: dict[str, ThreadPoolExecutor] = {}
        self._unknown_lane: ThreadPoolExecutor | None = None
        self._socket_process_identity: dict[str, str] = {}
        self._closed_lane_keys: set[str] = set()
        self._closed_session_bindings: set[str] = set()
        self._releasable_session_bindings: set[str] = set()
        self._recovery_bindings: set[str] = set()
        self._global_gate = _ReadWriteGate()
        self._next_task_id = 0
        self._scheduled_tasks: dict[int, dict[str, Any]] = {}

    def submit(self, context: SessionRuntimeContext | None,
               callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        if context is not None and not isinstance(context, SessionRuntimeContext):
            raise TypeError("scheduler context must be a session context or None for the legacy default")
        # A missing socket observation is not evidence that the endpoint is
        # independent.  Such requests use the single global exclusive lane;
        # they still may not claim an owned-server lane based on the caller's
        # requested host/port.
        peer = context.endpoint.observed_peer if context is not None else None
        process = context.endpoint.owned_process if context is not None else None
        binding_key = context.worker_binding_key if context is not None else None
        proven_owned = context is not None and (
            peer is not None
            and context.server_ownership == "mcp_managed"
            and process is not None
            and process.attests_peer(peer)
        )
        lane_key = process.lock_key if proven_owned and process is not None else None

        with self._lock:
            if self._closed:
                raise SessionSchedulerClosed("session scheduler is closed")
            if binding_key is not None and binding_key in self._closed_session_bindings:
                raise SessionSchedulerClosed("session Worker binding is fenced for retirement")
            if lane_key is not None:
                if lane_key in self._closed_lane_keys:
                    raise SessionSchedulerClosed("owned server lane is fenced for retirement")
                assert process is not None
                socket_keys = {peer.lock_key, *(sock.lock_key for sock in process.listener_sockets)}
                for socket_key in socket_keys:
                    previous = self._socket_process_identity.get(socket_key)
                    if previous is not None and previous != lane_key:
                        raise SessionIdentityConflict("socket is mapped to another live process birth")
                for socket_key in socket_keys:
                    self._socket_process_identity[socket_key] = lane_key
                executor = self._owned_lanes.get(lane_key)
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"comsol-{process.pid}")
                    self._owned_lanes[lane_key] = executor
            else:
                if self._unknown_lane is None:
                    self._unknown_lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="comsol-unknown")
                executor = self._unknown_lane

            self._next_task_id += 1
            task_id = self._next_task_id
            self._scheduled_tasks[task_id] = {
                "context": context,
                "project_id": context.project_id if context is not None else None,
                "session_id": context.session_id if context is not None else None,
                "worker_epoch": context.worker_epoch if context is not None else None,
                "worker_binding_key": context.worker_binding_key if context is not None else None,
                "server_lane_key": lane_key,
                "unknown_lane": lane_key is None,
                "state": "QUEUED",
            }

            def run_in_scope():
                try:
                    from contextlib import nullcontext
                    session_scope = use_session_context(context) if context is not None else nullcontext()

                    def admit_and_invoke():
                        # The global lease can block after the initial queue
                        # check.  An owned-server retirement fence may reject
                        # an accepted callback while the lane is being retired.
                        # Session binding fences require quiescence, while the
                        # separate soft close deliberately lets already
                        # accepted session work drain.
                        with self._lock:
                            if lane_key is not None and lane_key in self._closed_lane_keys:
                                raise SessionSchedulerClosed("owned server lane was fenced before callback admission")
                            task = self._scheduled_tasks.get(task_id)
                            if task is not None:
                                task["state"] = "RUNNING"
                        return callback(*args, **kwargs)

                    if proven_owned:
                        with self._global_gate.known_server_lease(), session_scope:
                            return admit_and_invoke()
                    with self._global_gate.unknown_server_exclusive_lease(), session_scope:
                        return admit_and_invoke()
                finally:
                    with self._lock:
                        self._scheduled_tasks.pop(task_id, None)

            # Keep submit under the lifecycle lock to make close() linearizable.
            try:
                future = executor.submit(run_in_scope)
            except Exception:
                self._scheduled_tasks.pop(task_id, None)
                raise
            future.add_done_callback(lambda _future, task_id=task_id: self._discard_task(task_id))
            return future

    def _discard_task(self, task_id: int) -> None:
        with self._lock:
            self._scheduled_tasks.pop(task_id, None)

    def quiescence_snapshot(self, context: SessionRuntimeContext) -> dict[str, Any]:
        """Count work across this session, its exact Worker, and conflicting lanes.

        ``worker_status`` covers every accepted task submitted through any
        project/session context that refers to this exact Worker object and
        connection epoch. ``server_lane_status`` separately covers all work
        on the same proven owned server process, even through another Worker.
        Unknown server work shares the global exclusive gate, so it is exposed
        independently and also prevents an all-scope QUIESCENT result.
        """
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("quiescence readback requires an exact session context")
        with self._lock:
            selected = [
                task for task in self._scheduled_tasks.values()
                if task["context"] is context
                and task["project_id"] == context.project_id
                and task["session_id"] == context.session_id
                and task["worker_epoch"] == context.worker_epoch
            ]
            queued = sum(task["state"] == "QUEUED" for task in selected)
            running = sum(task["state"] == "RUNNING" for task in selected)
            worker_tasks = [task for task in self._scheduled_tasks.values()
                            if task["context"] is not None
                            and (task["context"] is context if context.worker is None
                                 else task["context"].worker is context.worker)
                            and task["worker_epoch"] == context.worker_epoch]
            worker_queued = sum(task["state"] == "QUEUED" for task in worker_tasks)
            worker_running = sum(task["state"] == "RUNNING" for task in worker_tasks)
            lane_key = context.endpoint.owned_process.lock_key if (
                context.server_ownership == "mcp_managed" and context.endpoint.owned_process is not None
            ) else None
            lane_tasks = [task for task in self._scheduled_tasks.values()
                          if lane_key is not None and task["server_lane_key"] == lane_key]
            lane_queued = sum(task["state"] == "QUEUED" for task in lane_tasks)
            lane_running = sum(task["state"] == "RUNNING" for task in lane_tasks)
            unknown_tasks = [task for task in self._scheduled_tasks.values() if task["unknown_lane"]]
            unknown_queued = sum(task["state"] == "QUEUED" for task in unknown_tasks)
            unknown_running = sum(task["state"] == "RUNNING" for task in unknown_tasks)
            closed = self._closed
        worker_busy = bool(worker_queued or worker_running)
        server_lane_busy = bool(lane_queued or lane_running)
        unknown_busy = bool(unknown_queued or unknown_running)
        all_counts = (queued, running, worker_queued, worker_running,
                      lane_queued, lane_running, unknown_queued, unknown_running)
        return {
            "status": "UNKNOWN" if closed else ("BUSY" if any(all_counts) else "QUIESCENT"),
            "worker_status": "UNKNOWN" if closed else ("BUSY" if worker_busy else "QUIESCENT"),
            "server_lane_status": (
                "UNKNOWN" if closed else
                ("BUSY" if server_lane_busy else
                 ("QUIESCENT" if lane_key is not None else "UNOWNED_OR_UNKNOWN"))
            ),
            "unknown_global_exclusive_status": "UNKNOWN" if closed else (
                "BUSY" if unknown_busy else "QUIESCENT"
            ),
            "binding": {
                "project_id": context.project_id,
                "session_id": context.session_id,
                "worker_epoch": context.worker_epoch,
                "worker_instance_id": context.worker_instance_id,
                "worker_binding_key": context.worker_binding_key,
            },
            "scope": "session+worker+owned-server-lane+unknown-global-exclusive",
            "counts": {
                "session_queued": queued,
                "session_running": running,
                "worker_queued": worker_queued,
                "worker_running": worker_running,
                "owned_server_lane_queued": lane_queued,
                "owned_server_lane_running": lane_running,
                "unknown_global_exclusive_queued": unknown_queued,
                "unknown_global_exclusive_running": unknown_running,
            },
            "scheduler_closed": closed,
        }

    def lane_keys(self) -> tuple[str, ...]:
        with self._lock:
            keys = list(self._owned_lanes)
            if self._unknown_lane is not None:
                keys.append("unknown-exclusive")
            return tuple(sorted(keys))

    def fence_owned_server_lane(self, context: SessionRuntimeContext) -> str:
        """Reject new ordinary API work for an exact owned process lane.

        This is an admission fence, not a queue wait: emergency lifecycle
        control can terminate a busy server without sitting behind its solve.
        The lane remains closed because a killed process birth cannot be
        silently replaced by a different server at the same socket.
        """
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("lane retirement fence requires a session context")
        process = context.endpoint.owned_process
        peer = context.endpoint.observed_peer
        if (context.server_ownership != "mcp_managed" or process is None or peer is None
                or not process.attests_peer(peer)):
            raise SessionIdentityUnknown("only an observed MCP-owned process lane can be fenced")
        lane_key = process.lock_key
        with self._lock:
            if self._closed:
                raise SessionSchedulerClosed("session scheduler is closed")
            mapped = self._socket_process_identity.get(peer.lock_key)
            if mapped not in (None, lane_key):
                raise SessionIdentityConflict("socket is mapped to a different process birth")
            self._closed_lane_keys.add(lane_key)
        return lane_key

    def fence_session_binding(self, context: SessionRuntimeContext) -> str:
        """Atomically stop admission for one Worker epoch after it is quiescent.

        This fence applies to shared and unknown endpoints as well as owned
        lanes.  The check and fence share the scheduler lock with submission,
        so no new request can slip between a quiescence readback and client
        disconnect/reconnect.  Already accepted work makes retirement refuse.
        """
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("session binding fence requires an exact session context")
        binding_key = context.worker_binding_key
        with self._lock:
            if self._closed:
                raise SessionSchedulerClosed("session scheduler is closed")
            if binding_key in self._closed_session_bindings:
                return binding_key
            selected = [
                task for task in self._scheduled_tasks.values()
                if task.get("worker_binding_key") == binding_key
            ]
            if selected:
                queued = sum(task.get("state") == "QUEUED" for task in selected)
                running = sum(task.get("state") == "RUNNING" for task in selected)
                raise SessionBindingBusy(
                    f"session Worker binding has accepted work (queued={queued}, running={running})"
                )
            self._closed_session_bindings.add(binding_key)
            self._releasable_session_bindings.add(binding_key)
        return binding_key

    @contextmanager
    def worker_recovery_admission_guard(self, worker: Any, worker_epoch: int):
        """Fence one exact Worker object/epoch while recovery reads its history.

        Unlike retirement, this guard reopens only a fence it installed. A
        binding already closed by an earlier uncertain lifecycle operation
        stays closed. Submission and the initial quiescence check share the
        scheduler lock, so no accepted task can slip into recovery.
        """
        if worker is None or type(worker_epoch) is not int or worker_epoch < 1:
            raise SessionIdentityUnknown("recovery requires an exact Worker object and epoch")
        binding_key = f"python-worker-object:{id(worker)}:epoch:{worker_epoch}"
        with self._lock:
            if self._closed:
                raise SessionSchedulerClosed("session scheduler is closed")
            if binding_key in self._recovery_bindings:
                raise SessionBindingBusy("another recovery already holds this Worker epoch")
            selected = [task for task in self._scheduled_tasks.values()
                        if task.get("worker_binding_key") == binding_key]
            if selected:
                queued = sum(task.get("state") == "QUEUED" for task in selected)
                running = sum(task.get("state") == "RUNNING" for task in selected)
                raise SessionBindingBusy(
                    f"Worker epoch has accepted work (queued={queued}, running={running})"
                )
            was_closed = binding_key in self._closed_session_bindings
            if not was_closed:
                self._closed_session_bindings.add(binding_key)
                self._releasable_session_bindings.add(binding_key)
            self._recovery_bindings.add(binding_key)
        try:
            yield {
                "binding_key": binding_key,
                "admission_fenced": True,
                "accepted_task_count": 0,
                "preexisting_fence_preserved": was_closed,
            }
        finally:
            with self._lock:
                self._recovery_bindings.discard(binding_key)
                if not was_closed and binding_key in self._releasable_session_bindings:
                    self._releasable_session_bindings.discard(binding_key)
                    self._closed_session_bindings.discard(binding_key)

    def close_session_binding_admission(self, context: SessionRuntimeContext) -> str:
        """Close future submissions while allowing already accepted work to drain.

        This is used when the durable identity itself becomes uncertain. It is
        not a quiescent retirement proof and cannot be released as a safe
        pre-dispatch abort.
        """
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("session admission close requires an exact session context")
        binding_key = context.worker_binding_key
        with self._lock:
            if self._closed:
                raise SessionSchedulerClosed("session scheduler is closed")
            self._closed_session_bindings.add(binding_key)
            self._releasable_session_bindings.discard(binding_key)
        return binding_key

    def release_session_binding(self, context: SessionRuntimeContext) -> None:
        """Reopen an exact binding only when retirement is proven not dispatched."""
        if not isinstance(context, SessionRuntimeContext):
            raise TypeError("session binding release requires an exact session context")
        with self._lock:
            binding_key = context.worker_binding_key
            if binding_key in self._releasable_session_bindings:
                self._releasable_session_bindings.discard(binding_key)
                self._closed_session_bindings.discard(binding_key)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executors = list(self._owned_lanes.values())
            if self._unknown_lane is not None:
                executors.append(self._unknown_lane)
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=False)
