"""Private prospective owner roots for scoped Windows managed-Server stop.

An origin is created only from the running daemon's original SCM/T037 factory
binding, before the launcher can create a child. It is process-local evidence,
not an authorization issuer and not a reconstruction format.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Mapping
import weakref

from ._execution_contract import ExecutionContractError

_ORIGIN_SEAL = object()
_SCOPE_SCHEMA = "comsol-mcp-managed-stop-scope/1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ORIGINAL_ORIGINS: weakref.WeakSet = weakref.WeakSet()


class ManagedStopOriginError(ExecutionContractError):
    """A prospective owner chain is incomplete or no longer exact."""

    def __init__(self, message: str):
        super().__init__("MANAGED_STOP_ORIGIN_UNKNOWN", message, safe_retry=False,
                         stage="pre_dispatch")


def _class_name(value: Any) -> str:
    kind = type(value)
    return f"{kind.__module__}.{kind.__qualname__}"


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path_identity(value: Path, *, directory: bool) -> tuple[str, int, int, int, int, int]:
    try:
        resolved = value.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ManagedStopOriginError("trusted runtime path identity is unavailable") from exc
    if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
        raise ManagedStopOriginError("trusted runtime path has an unexpected file type")
    return (str(resolved), int(info.st_dev), int(info.st_ino), int(info.st_mode),
            int(info.st_size), int(info.st_mtime_ns))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise ManagedStopOriginError("trusted Server launcher bytes cannot be revalidated") from exc
    return digest.hexdigest()


def _runtime_identity(runtime: Any) -> tuple[Any, ...]:
    from ._session_context import SessionRuntimeConfig

    if type(runtime) is not SessionRuntimeConfig:
        raise ManagedStopOriginError("the exact trusted SessionRuntimeConfig is unavailable")
    if type(runtime.classpath) is not tuple or not 1 <= len(runtime.classpath) <= 512:
        raise ManagedStopOriginError("trusted runtime classpath is outside its finite bound")
    return (
        runtime.runtime_id,
        _class_name(runtime),
        runtime.comsol_version,
        _path_identity(runtime.installation_root, directory=True),
        _path_identity(runtime.java_executable, directory=False),
        tuple(_path_identity(path, directory=False) for path in runtime.classpath),
    )


def _trusted_profile(daemon: Any, factory: Any):
    from ._windows_t037 import WindowsT037ProfileV2
    from ._w26_os_resources import HostResourceProfile
    from ._windows_t037_admission import _require_factory_runtime
    from ._host_scoped_provider import HostScopedProvider

    try:
        scm_runtime, bound_daemon, profile, provider = _require_factory_runtime(factory)
    except Exception as exc:
        raise ManagedStopOriginError("the original live SCM/T037 factory chain is unavailable") from exc
    if (bound_daemon is not daemon or type(profile) is not WindowsT037ProfileV2
            or type(profile.host_resource_profile) is not HostResourceProfile
            or type(provider) is not HostScopedProvider
            or daemon._scoped_host_provider is not provider
            or daemon.project_authority.authorization_verifier is not provider
            or daemon._windows_t037_scm_runtime is not scm_runtime
            or daemon._windows_t037_profile is not profile
            or profile.host_resource_profile.platform != "windows"):
        raise ManagedStopOriginError("SCM, daemon, provider, or Windows source identity differs")
    return scm_runtime, profile, provider


def create_original_managed_stop_origin(
    daemon: Any, *, project_id: str, session_id: str, start_record: Mapping[str, Any],
    runtime: Any, factory: Any, launcher: Any,
) -> "ManagedStopOrigin":
    """Create and root one prospective origin before any launcher call."""
    from ._control_daemon import ControlDaemon
    from ._session_context import SessionRuntimeConfig
    from ._session_server import OwnedServerLauncher
    from ._windows_t037 import T037SessionProcessFactory

    if (type(daemon) is not ControlDaemon or type(factory) is not T037SessionProcessFactory
            or type(launcher) is not OwnedServerLauncher
            or not isinstance(project_id, str) or not project_id
            or not isinstance(session_id, str) or not session_id
            or type(runtime) is not SessionRuntimeConfig):
        raise ManagedStopOriginError("new exact daemon/runtime/factory/launcher objects are required")
    metadata = start_record.get("metadata")
    if (not isinstance(metadata, Mapping) or start_record.get("operation") != "session.start"
            or metadata.get("project_id") != project_id or metadata.get("session_id") != session_id
            or metadata.get("runtime_id") != runtime.runtime_id
            or not all(isinstance(start_record.get(field), str) and start_record[field]
                       for field in ("request_id", "idempotency_key", "operation_id", "job_id"))):
        raise ManagedStopOriginError("durable original session.start identity is incomplete")
    try:
        durable_job = daemon.store.job(start_record["job_id"])
    except Exception as exc:
        raise ManagedStopOriginError("original session.start operation is not durably readable") from exc
    durable_operation = durable_job.get("operation") if isinstance(durable_job, Mapping) else None
    durable_metadata = durable_job.get("metadata") if isinstance(durable_job, Mapping) else None
    if (not isinstance(durable_operation, Mapping) or not isinstance(durable_metadata, Mapping)
            or durable_job.get("job_id") != start_record["job_id"]
            or durable_job.get("operation_id") != start_record["operation_id"]
            or durable_job.get("status") != "RUNNING"
            or durable_operation.get("operation") != "session.start"
            or durable_operation.get("status") != "RUNNING"
            or durable_operation.get("request_id") != start_record["request_id"]
            or durable_operation.get("idempotency_key") != start_record["idempotency_key"]
            or durable_operation.get("request_hash") != start_record.get("request_hash")
            or durable_metadata.get("project_id") != project_id
            or durable_metadata.get("session_id") != session_id
            or durable_metadata.get("runtime_id") != runtime.runtime_id):
        raise ManagedStopOriginError("durable start row differs from the active original birth request")
    key = (project_id, session_id)
    if (daemon._session_runtime_configs.get(key) is not runtime
            or daemon._session_t037_process_factories.get(key) is not factory
            or factory.session_id != session_id):
        raise ManagedStopOriginError("runtime or T037 factory is not the exact registered start owner")
    scm_runtime, profile, provider = _trusted_profile(daemon, factory)
    host = profile.host_resource_profile
    if (not isinstance(host.host_id, str) or not _IDENTIFIER.fullmatch(host.host_id)
            or host.project_id != project_id
            or not isinstance(profile.source_sha256, str) or not _SHA256.fullmatch(profile.source_sha256)
            or not isinstance(host.sha256, str) or not _SHA256.fullmatch(host.sha256)
            or not isinstance(profile.source_identity, Mapping)):
        raise ManagedStopOriginError("trusted Windows source metadata is incomplete")
    if getattr(factory, "_job", None) is not None or getattr(factory, "_api", None) is not None:
        raise ManagedStopOriginError("a newly rooted original factory must not already own a Job or SDK")
    return ManagedStopOrigin(
        _ORIGIN_SEAL, daemon=daemon, project_id=project_id, session_id=session_id,
        start_record=start_record, runtime=runtime, factory=factory, launcher=launcher,
        scm_runtime=scm_runtime, profile=profile, provider=provider,
    )


class ManagedStopOrigin:
    """Sealed same-process evidence for exactly one newly created Server."""

    __slots__ = (
        "_seal", "daemon", "project_id", "session_id", "key", "start_identity",
        "runtime", "runtime_identity", "factory", "launcher", "scm_runtime",
        "scm_binding", "profile", "provider", "provider_identity", "source_identity",
        "scope", "launch_identity", "handle", "process", "job", "api", "handoff_state",
        "__weakref__",
    )

    def __init__(self, seal: object, *, daemon, project_id, session_id, start_record,
                 runtime, factory, launcher, scm_runtime, profile, provider):
        if seal is not _ORIGIN_SEAL:
            raise ManagedStopOriginError("managed-stop origins are not caller-constructible")
        self._seal = seal
        self.daemon = daemon
        self.project_id = project_id
        self.session_id = session_id
        self.key = (project_id, session_id)
        self.start_identity = tuple(start_record[field] for field in (
            "request_id", "idempotency_key", "operation_id", "job_id", "request_hash",
        ))
        self.runtime = runtime
        self.runtime_identity = _runtime_identity(runtime)
        self.factory = factory
        self.launcher = launcher
        self.scm_runtime = scm_runtime
        self.scm_binding = daemon._windows_t037_scm_binding
        self.profile = profile
        self.provider = provider
        self.provider_identity = (
            provider.config_sha256, str(provider._path), tuple(provider._file_fingerprint),
            _canonical_digest(provider._parent_identity), _canonical_digest(provider._config_identity),
        )
        self.source_identity = _canonical_digest(dict(profile.source_identity))
        host = profile.host_resource_profile
        self.scope = {
            "schema": _SCOPE_SCHEMA,
            "runtime_id": {"id": runtime.runtime_id, "class": _class_name(runtime)},
            "platform": "windows",
            "ownership_mode": "originalManagedLifetime",
            "host_source": {
                "t037_source_sha256": profile.source_sha256,
                "host_resource_profile_sha256": host.sha256,
                "host_id": host.host_id,
            },
        }
        self.launch_identity = None
        self.handle = None
        self.process = None
        self.job = None
        self.api = None
        self.handoff_state = "ROOTED_BEFORE_LAUNCH"
        _ORIGINAL_ORIGINS.add(self)

    def __copy__(self):
        raise ManagedStopOriginError("managed-stop roots cannot be copied")

    def __deepcopy__(self, _memo):
        raise ManagedStopOriginError("managed-stop roots cannot be copied")

    def launcher_handoff(self, stage: str, **facts: Any) -> None:
        """Accept only the launcher's ordered pre-create and exact-handle handoffs."""
        if (self._seal is not _ORIGIN_SEAL or self not in _ORIGINAL_ORIGINS
                or self.daemon._managed_stop_origins.get(self.key) is not self):
            raise ManagedStopOriginError("the daemon no longer retains this original root")
        if stage == "PRE_CREATE":
            if self.handoff_state != "ROOTED_BEFORE_LAUNCH" or self.launch_identity is not None:
                raise ManagedStopOriginError("launcher pre-create handoff is duplicate or out of order")
            runtime, process_factory = facts.get("runtime"), facts.get("process_factory")
            command, executable, directories = facts.get("command"), facts.get("executable"), facts.get("directories")
            if (runtime is not self.runtime or process_factory is not self.factory
                    or type(command) is not tuple or not command or any(type(item) is not str for item in command)
                    or not isinstance(executable, str) or not executable
                    or directories is None):
                raise ManagedStopOriginError("pre-create facts differ from the original runtime/factory")
            executable_path = Path(executable).resolve(strict=True)
            identity = {
                "command_sha256": _canonical_digest(list(command)),
                "executable": _path_identity(executable_path, directory=False),
                "executable_sha256": _file_sha256(executable_path),
                "directories_root": str(Path(directories.root).resolve(strict=True)),
                "runtime_identity": _runtime_identity(runtime),
            }
            self.launch_identity = identity
            self.handoff_state = "PRE_CREATE_BOUND"
            return
        if stage == "CHILD_HANDLE":
            handle = facts.get("handle")
            from ._session_server import ManagedServerHandle
            if (self.handoff_state != "PRE_CREATE_BOUND" or self.handle is not None
                    or type(handle) is not ManagedServerHandle or handle.process is None
                    or handle.runtime_id != self.runtime.runtime_id
                    or str(Path(handle.directories.root).resolve(strict=True))
                    != self.launch_identity["directories_root"]):
                raise ManagedStopOriginError("post-create handle handoff is missing, repeated, or mismatched")
            job, api = getattr(self.factory, "_job", None), getattr(self.factory, "_api", None)
            if job is None or api is None:
                raise ManagedStopOriginError("the original factory did not retain its actual Job and SDK owner")
            process = handle.process
            try:
                from ._windows_t037 import is_native_retained_child, is_native_suspended_child
                if is_native_retained_child(process) or is_native_suspended_child(process):
                    if (getattr(process, "_original_factory", None) is not self.factory
                            or getattr(process, "_original_job", None) is not job
                            or getattr(process, "_api", None) is not api):
                        raise ManagedStopOriginError("native child differs from the original factory Job/API")
            except ManagedStopOriginError:
                raise
            except Exception as exc:
                raise ManagedStopOriginError("native child ownership could not be inspected") from exc
            self.handle, self.process, self.job, self.api = handle, process, job, api
            self.handoff_state = "CHILD_HANDLE_RETAINED"
            return
        if stage == "READY":
            if facts.get("handle") is not self.handle or self.handoff_state != "CHILD_HANDLE_RETAINED":
                raise ManagedStopOriginError("ready handoff differs from the captured original child")
            identity = self.handle.process_identity
            if (identity is None or self.handle.endpoint is None
                    or identity.runtime_id != self.runtime.runtime_id
                    or identity.executable_sha256 != self.launch_identity["executable_sha256"]
                    or identity.launch_argv_sha256 != self.launch_identity["command_sha256"]):
                raise ManagedStopOriginError("ready identity differs from the original launch plan")
            self.handoff_state = "READY"
            return
        raise ManagedStopOriginError("unknown owner-handoff stage")

    def is_never_resumed_child(self, handle: Any) -> bool:
        return (self._seal is _ORIGIN_SEAL and handle is self.handle
                and handle is not None and getattr(handle, "process", None) is self.process
                and getattr(handle, "suspended_launch_recovery", None) is self.process)

    def validate_for_stop(self, daemon: Any, handle: Any, *, allow_never_resumed: bool = False) -> None:
        if (self._seal is not _ORIGIN_SEAL or self not in _ORIGINAL_ORIGINS
                or daemon is not self.daemon
                or daemon._managed_stop_origins.get(self.key) is not self
                or self.key != (self.project_id, self.session_id)
                or daemon._session_runtime_configs.get(self.key) is not self.runtime
                or daemon._session_t037_process_factories.get(self.key) is not self.factory
                or daemon._session_server_handles.get(self.key) is not handle
                or handle is not self.handle or getattr(handle, "process", None) is not self.process
                or getattr(handle, "runtime_id", None) != self.runtime.runtime_id
                or self.handoff_state not in {"READY", "CHILD_HANDLE_RETAINED"}):
            raise ManagedStopOriginError("requested session is not bound to its retained original handle/root")
        if (_runtime_identity(self.runtime) != self.runtime_identity
                or type(self.launcher) is not type(daemon.session_server_launcher)
                or daemon.session_server_launcher is not self.launcher
                or self.factory._job is not self.job or self.factory._api is not self.api
                or self.factory._closed is True or self.provider._closed is True
                or daemon._scoped_host_provider is not self.provider
                or daemon.project_authority.authorization_verifier is not self.provider
                or daemon._windows_t037_scm_binding is not self.scm_binding
                or daemon._windows_t037_scm_runtime is not self.scm_runtime
                or self.scm_binding is None):
            raise ManagedStopOriginError("runtime, launcher, factory, provider, or SCM owner drifted")
        provider_identity = (
            self.provider.config_sha256, str(self.provider._path), tuple(self.provider._file_fingerprint),
            _canonical_digest(self.provider._parent_identity), _canonical_digest(self.provider._config_identity),
        )
        if provider_identity != self.provider_identity:
            raise ManagedStopOriginError("pinned provider identity differs from the original owner root")
        from ._windows_t037 import WindowsT037ProfileV2, load_trusted_profile
        from ._windows_t037_admission import _require_factory_runtime
        try:
            scm_runtime, bound_daemon, profile, provider = _require_factory_runtime(self.factory)
            current = load_trusted_profile(daemon._windows_t037_profile_path)
        except Exception as exc:
            raise ManagedStopOriginError("original SCM/T037 source could not be revalidated") from exc
        if (scm_runtime is not self.scm_runtime or bound_daemon is not daemon
                or profile is not self.profile or provider is not self.provider
                or daemon._windows_t037_profile is not self.profile
                or type(current) is not WindowsT037ProfileV2
                or current.source_sha256 != self.profile.source_sha256
                or dict(current.source_identity) != dict(self.profile.source_identity)
                or current.sha256 != self.profile.sha256
                or current.host_resource_profile.sha256 != self.profile.host_resource_profile.sha256
                or current.host_resource_profile.host_id != self.profile.host_resource_profile.host_id
                or _canonical_digest(dict(current.source_identity)) != self.source_identity):
            raise ManagedStopOriginError("current trusted T037 source differs from the original birth source")
        never_resumed = self.is_never_resumed_child(handle)
        if never_resumed:
            if not allow_never_resumed:
                raise ManagedStopOriginError("native never-resumed child requires its separate original cleanup path")
            return
        if (self.handoff_state != "READY" or handle.process_identity is None or handle.endpoint is None
                or self.launch_identity is None):
            raise ManagedStopOriginError("normal stop lacks completed original birth/listener handoff")
        identity = handle.process_identity
        if (handle.runtime_id != self.runtime.runtime_id
                or identity.runtime_id != self.runtime.runtime_id
                or identity.executable != self.launch_identity["executable"][0]
                or identity.executable_sha256 != self.launch_identity["executable_sha256"]
                or identity.launch_argv_sha256 != self.launch_identity["command_sha256"]
                or _path_identity(Path(identity.executable), directory=False) != self.launch_identity["executable"]
                or _file_sha256(Path(identity.executable)) != self.launch_identity["executable_sha256"]):
            raise ManagedStopOriginError("live Server launch identity differs from its original birth binding")

    def audit_digests(self, authorization_ref: str) -> dict[str, str]:
        if not isinstance(authorization_ref, str) or not authorization_ref:
            raise ManagedStopOriginError("authorization reference is unavailable")
        from ._host_scoped_provider import scope_sha256
        return {
            "authorization_ref_sha256": hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest(),
            "scope_sha256": scope_sha256(self.project_id, "session.stop", self.scope),
            "t037_source_sha256": self.profile.source_sha256,
            "host_resource_profile_sha256": self.profile.host_resource_profile.sha256,
            "provider_config_sha256": self.provider.config_sha256,
        }
