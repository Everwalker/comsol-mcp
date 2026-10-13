"""Host-only scoped grants and registered local publication sinks.

The configuration is an administrator-managed capability file, never an MCP
request payload. It stores only SHA-256 digests for authorization references
and canonical scopes. There is deliberately no public grant/destination
registration operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError

CONFIG_SCHEMA = "comsol-mcp.host-scoped-provider-config/1"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_DESTINATIONS = 64
MAX_GRANTS = 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TARGET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_GRANT_ACTIONS = frozenset({"model.close", "artifact.publish"})
_MODEL_REF_KEYS = frozenset({"schema_version", "session_id", "server_instance_id", "model_tag", "generation"})


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider data must be finite canonical JSON") from exc


def scope_sha256(project_id: str, action: str, scope: Mapping[str, Any]) -> str:
    """Compute the documented exact-scope digest for host-admin config authoring."""
    normalized = _normalize_scope(project_id, action, scope)
    return hashlib.sha256(_canonical({"project_id": project_id, "action": action, "scope": normalized})).hexdigest()


def destination_registration_sha256(project_id: str, destination_ref: str,
                                   sink_root: str | os.PathLike[str]) -> str:
    """Compute a destination digest after checking its current private directory identity."""
    path, identity = _private_directory(sink_root, "publish sink")
    descriptor = {"schema": "comsol-mcp-local-publish-sink/1", "project_id": project_id,
                  "destination_ref": destination_ref, "sink_path": str(path),
                  "sink_identity": identity}
    return hashlib.sha256(_canonical(descriptor)).hexdigest()


def _normalize_scope(project_id: str, action: str, scope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id or len(project_id) > 128:
        raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "project scope is malformed")
    if action == "model.close":
        if not isinstance(scope, Mapping) or set(scope) != {"model_ref", "expected_revision", "destination_ref"}:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "model.close scope fields are malformed")
        ref = scope.get("model_ref")
        if (not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS
                or type(ref.get("schema_version")) is not int or ref["schema_version"] != 1
                or type(ref.get("generation")) is not int or ref["generation"] < 1
                or any(not isinstance(ref.get(field), str) or not ref[field]
                       for field in ("session_id", "server_instance_id", "model_tag"))
                or type(scope.get("expected_revision")) is not int or scope["expected_revision"] < 0
                or scope.get("destination_ref") is not None):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "model.close identity/revision scope is malformed")
        return {"model_ref": dict(ref), "expected_revision": scope["expected_revision"],
                "destination_ref": None}
    if action == "artifact.publish":
        required = {"project_id", "action", "destination_ref", "destination_registration_sha256", "artifacts"}
        if not isinstance(scope, Mapping) or set(scope) != required:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish scope fields are malformed")
        if (scope.get("project_id") != project_id or scope.get("action") != action
                or not isinstance(scope.get("destination_ref"), str)
                or not _IDENTIFIER.fullmatch(scope["destination_ref"])
                or not isinstance(scope.get("destination_registration_sha256"), str)
                or not _SHA256.fullmatch(scope["destination_registration_sha256"])):
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish destination scope is malformed")
        artifacts = scope.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 128:
            raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish artifact scope is malformed")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in artifacts:
            if (not isinstance(row, Mapping) or set(row) != {"artifact_id", "sha256", "size_bytes"}
                    or not isinstance(row.get("artifact_id"), str) or not _SHA256.fullmatch(row["artifact_id"])
                    or not isinstance(row.get("sha256"), str) or not _SHA256.fullmatch(row["sha256"])
                    or type(row.get("size_bytes")) is not int or row["size_bytes"] <= 0
                    or row["artifact_id"] in seen):
                raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "artifact.publish artifact identity is malformed")
            seen.add(row["artifact_id"])
            normalized.append({"artifact_id": row["artifact_id"], "sha256": row["sha256"],
                               "size_bytes": row["size_bytes"]})
        normalized.sort(key=lambda row: row["artifact_id"])
        return {"project_id": project_id, "action": action,
                "destination_ref": scope["destination_ref"],
                "destination_registration_sha256": scope["destination_registration_sha256"],
                "artifacts": normalized}
    raise ExecutionContractError("AUTHORIZATION_SCOPE_INVALID", "host provider action is not grantable")


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config root must be an object")
    return value


def _safe_absolute(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path is malformed")
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path must be absolute and canonical")
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path contains a symlink")
        resolved = raw.resolve(strict=True)
    except ExecutionContractError:
        raise
    except OSError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path cannot be inspected") from exc
    if resolved != raw:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} path is not canonical")
    return raw


def _private_directory(value: str | os.PathLike[str], field: str) -> tuple[Path, dict[str, int]]:
    path = _safe_absolute(os.fspath(value), field)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} must be a real directory")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} must be owned by the daemon user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", f"{field} permissions must exclude group and other access")
    return path, {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _file_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size),
            int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9))),
            int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))))


class HostScopedProvider:
    """Pinned startup config, exact-scope grants and no-clobber local sinks."""

    def __init__(self, path: Path, fd: int, config: dict[str, Any], raw_sha256: str,
                 file_fingerprint: tuple[int, int, int, int, int], parent_identity: dict[str, int],
                 destinations: dict[tuple[str, str], dict[str, Any]], grants: dict[tuple[str, str, str, str], str]):
        self._path = path
        self._fd = fd
        self._config = config
        self.config_sha256 = raw_sha256
        self._file_fingerprint = file_fingerprint
        self._parent_identity = parent_identity
        self._destinations = destinations
        self._grants = grants
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def load(cls, value: str | os.PathLike[str]) -> "HostScopedProvider":
        path = _safe_absolute(os.fspath(value), "host provider config")
        try:
            parent, parent_identity = _private_directory(path.parent, "host provider config directory")
            if hasattr(os, "geteuid") and path.lstat().st_uid != os.geteuid():
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config must be owned by the daemon user")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
        except ExecutionContractError:
            raise
        except OSError as exc:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config cannot be opened safely") from exc
        try:
            before = os.fstat(fd)
            named = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named.st_mode)
                    or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
                    or (hasattr(os, "geteuid") and before.st_uid != os.geteuid())
                    or stat.S_IMODE(before.st_mode) & 0o077
                    or before.st_size <= 0 or before.st_size > MAX_CONFIG_BYTES):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config identity, permissions, or size is unsafe")
            raw = bytearray()
            while len(raw) <= MAX_CONFIG_BYTES:
                block = os.read(fd, min(64 * 1024, MAX_CONFIG_BYTES + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            named_after = path.lstat()
            if (_file_fingerprint(before) != _file_fingerprint(after)
                    or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
                    or len(raw) != before.st_size or len(raw) > MAX_CONFIG_BYTES):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config changed while pinned read was in progress")
            config = _strict_json(bytes(raw))
            destinations, grants = cls._validate_config(config)
            return cls(path, fd, config, hashlib.sha256(raw).hexdigest(),
                       _file_fingerprint(after), parent_identity, destinations, grants)
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _validate_config(config: dict[str, Any]):
        if set(config) != {"schema", "destinations", "grants"} or config.get("schema") != CONFIG_SCHEMA:
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider config schema or fields are invalid")
        raw_destinations = config.get("destinations")
        raw_grants = config.get("grants")
        if (not isinstance(raw_destinations, list) or len(raw_destinations) > MAX_DESTINATIONS
                or not isinstance(raw_grants, list) or len(raw_grants) > MAX_GRANTS):
            raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "host provider destination/grant inventory exceeds its bound")
        destinations: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in raw_destinations:
            if not isinstance(entry, dict) or set(entry) != {"project_id", "destination_ref", "sink_root"}:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "destination registration fields are invalid")
            project_id, destination_ref = entry.get("project_id"), entry.get("destination_ref")
            if (not isinstance(project_id, str) or not project_id or len(project_id) > 128
                    or not isinstance(destination_ref, str) or not _IDENTIFIER.fullmatch(destination_ref)):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "destination project or reference is malformed")
            key = (project_id, destination_ref)
            if key in destinations:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "duplicate destination registration")
            sink_root, identity = _private_directory(entry.get("sink_root"), "registered publish sink")
            registration = {"schema": "comsol-mcp-local-publish-sink/1", "project_id": project_id,
                            "destination_ref": destination_ref, "sink_path": str(sink_root),
                            "sink_identity": identity}
            registration_sha = hashlib.sha256(_canonical(registration)).hexdigest()
            destinations[key] = {"path": sink_root, "identity": identity,
                                 "registration_sha256": registration_sha}

        grants: dict[tuple[str, str, str, str], str] = {}
        now = datetime.now(timezone.utc)
        for entry in raw_grants:
            required = {"project_id", "action", "authorization_ref_sha256", "scope_sha256", "expires_at_utc"}
            if not isinstance(entry, dict) or set(entry) != required:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "authorization grant fields are invalid")
            project_id, action = entry.get("project_id"), entry.get("action")
            ref_hash, scope_hash, expiry = (entry.get("authorization_ref_sha256"),
                                             entry.get("scope_sha256"), entry.get("expires_at_utc"))
            if (not isinstance(project_id, str) or not project_id or len(project_id) > 128
                    or not isinstance(action, str) or action not in _GRANT_ACTIONS
                    or not isinstance(ref_hash, str) or not _SHA256.fullmatch(ref_hash)
                    or not isinstance(scope_hash, str) or not _SHA256.fullmatch(scope_hash)
                    or not isinstance(expiry, str)):
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "authorization grant identity is malformed")
            try:
                parsed_expiry = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "grant expiry must be canonical UTC YYYY-MM-DDTHH:MM:SSZ") from exc
            if parsed_expiry.strftime("%Y-%m-%dT%H:%M:%SZ") != expiry or (parsed_expiry - now).total_seconds() > 366 * 24 * 3600:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "grant expiry is noncanonical or exceeds the one-year bound")
            key = (project_id, action, ref_hash, scope_hash)
            if key in grants:
                raise ExecutionContractError("HOST_PROVIDER_CONFIG_INVALID", "duplicate authorization grant")
            grants[key] = expiry
        return destinations, grants

    def _assert_stable(self) -> None:
        with self._lock:
            if self._closed:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider is closed")
            try:
                parent_info = self._path.parent.lstat()
                named = self._path.lstat()
                before = os.fstat(self._fd)
                if ({"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)} != self._parent_identity
                        or stat.S_ISLNK(named.st_mode)
                        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
                        or (hasattr(os, "geteuid") and named.st_uid != os.geteuid())
                        or stat.S_IMODE(named.st_mode) & 0o077
                        or _file_fingerprint(before) != self._file_fingerprint):
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration identity or permissions changed")
                digest = hashlib.sha256()
                total = 0
                os.lseek(self._fd, 0, os.SEEK_SET)
                while True:
                    block = os.read(self._fd, 64 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_CONFIG_BYTES:
                        raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration grew beyond its startup bound")
                    digest.update(block)
                after = os.fstat(self._fd)
                if (_file_fingerprint(after) != self._file_fingerprint or total != after.st_size
                        or digest.hexdigest() != self.config_sha256):
                    raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "host provider configuration bytes changed after startup")
            except ExecutionContractError:
                raise
            except OSError as exc:
                raise ExecutionContractError("AUTHORIZATION_STATE_UNKNOWN", "pinned host provider configuration could not be revalidated") from exc

    def __call__(self, _project_id: str, _authorization_ref: str) -> bool:
        # This provider never grants the broader project.policy_set action.
        return False

    def verify_scope(self, project_id: str, action: str, scope: Mapping[str, Any], authorization_ref: str) -> bool:
        self._assert_stable()
        if action not in _GRANT_ACTIONS or not isinstance(authorization_ref, str):
            return False
        if (not authorization_ref or len(authorization_ref) > 512
                or any(ord(char) < 32 for char in authorization_ref)):
            return False
        try:
            normalized = _normalize_scope(project_id, action, scope)
        except ExecutionContractError:
            return False
        ref_hash = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
        scope_hash = hashlib.sha256(_canonical({"project_id": project_id, "action": action,
                                                "scope": normalized})).hexdigest()
        expiry = self._grants.get((project_id, action, ref_hash, scope_hash))
        if expiry is None:
            return False
        deadline = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < deadline

    def resolve_destination(self, project_id: str, destination_ref: str) -> dict[str, Any]:
        self._assert_stable()
        entry = self._destinations.get((project_id, destination_ref))
        if entry is None:
            raise KeyError("destination is not registered for this project")
        path, identity = _private_directory(entry["path"], "registered publish sink")
        if identity != entry["identity"]:
            raise ExecutionContractError("DESTINATION_STATE_UNKNOWN", "registered local sink directory identity changed")
        registration_sha = entry["registration_sha256"]

        def publish_chunks(destination, target_name, chunks, *, expected_sha256, expected_size, artifact_id):
            self._assert_stable()
            if (destination.get("project_id") != project_id
                    or destination.get("destination_ref") != destination_ref
                    or destination.get("registration_sha256") != registration_sha
                    or not isinstance(artifact_id, str) or not _SHA256.fullmatch(artifact_id)
                    or not isinstance(target_name, str) or not _TARGET_NAME.fullmatch(target_name)
                    or not (target_name == artifact_id or target_name.startswith(artifact_id + "."))
                    or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)
                    or type(expected_size) is not int or expected_size <= 0):
                raise ExecutionContractError("DESTINATION_IDENTITY_MISMATCH", "local sink publication identity or byte scope is invalid")
            current_path, current_identity = _private_directory(path, "registered publish sink")
            if current_path != path or current_identity != identity:
                raise ExecutionContractError("DESTINATION_STATE_UNKNOWN", "registered local sink changed before publication")
            from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact, _publish_project_chunks_no_clobber
            _publish_project_chunks_no_clobber(path, target_name, iter(chunks),
                                               expected_sha256=expected_sha256,
                                               expected_size=expected_size)
            target = path / target_name
            readback, before = _open_pinned_artifact(target)
            try:
                digest = hashlib.sha256()
                size = 0
                while True:
                    block = readback.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                _assert_pinned_artifact_stable(target, before, os.fstat(readback.fileno()))
            finally:
                readback.close()
            if digest.hexdigest() != expected_sha256 or size != expected_size:
                raise ExecutionContractError("PUBLISH_READBACK_MISMATCH", "local sink readback differs from authorized artifact hash/size")
            return {"status": "PUBLISHED", "destination_ref": destination_ref,
                    "artifact_id": artifact_id, "sha256": expected_sha256,
                    "size_bytes": expected_size, "no_clobber": True,
                    "readback_verified": True,
                    "receipt_id": hashlib.sha256(_canonical({
                        "config_sha256": self.config_sha256, "project_id": project_id,
                        "destination_ref": destination_ref, "artifact_id": artifact_id,
                        "sha256": expected_sha256, "size_bytes": expected_size,
                        "nonce": str(uuid4()),
                    })).hexdigest()}

        return {"schema": "comsol-mcp-publish-destination/1", "project_id": project_id,
                "destination_ref": destination_ref, "registration_sha256": registration_sha,
                "publish_chunks": publish_chunks}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                os.close(self._fd)


def load_host_scoped_provider(value: str | os.PathLike[str]) -> HostScopedProvider:
    return HostScopedProvider.load(value)
