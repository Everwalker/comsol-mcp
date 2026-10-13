"""Fail-closed host isolation and disk-resource primitives for W26/T037.

This module is deliberately not wired into the public registry or daemon yet.
It defines the trusted-host-provider contract, validates exact host/process/
storage bindings, reserves estimated disk use in the existing OperationStore,
and prepares reviewable host-change plans.  No native macOS/Windows provider or
host mutation adapter is implemented here; software fixtures never establish
OS isolation or enforceable quota acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import ipaddress
import json
import ntpath
import os
import posixpath
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


PROFILE_SCHEMA = "comsol-mcp.host-resource-profile/v1"
SNAPSHOT_SCHEMA = "comsol-mcp.host-resource-snapshot/v1"
PLAN_SCHEMA = "comsol-mcp.host-change-plan/v1"
RESERVATION_SCHEMA = "comsol-mcp.disk-reservation/v1"
_MAX_SQLITE_INT = (1 << 63) - 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_NATIVE_STORAGE_ATTESTATION = object()
_SENSITIVE_NAMES = frozenset({
    "authorization", "token", "accesstoken", "password", "passwd", "secret",
    "credential", "credentials", "apikey", "privatekey", "licensefilecontents",
})


class CapabilityState(str, Enum):
    IMPLEMENTED_UNVERIFIED = "IMPLEMENTED_UNVERIFIED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNSUPPORTED = "UNSUPPORTED"
    VERIFIED = "VERIFIED"


class QuotaState(str, Enum):
    VERIFIED_HARD_QUOTA = "VERIFIED_HARD_QUOTA"
    ADMISSION_ONLY = "ADMISSION_ONLY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNSUPPORTED = "UNSUPPORTED"


class ResourcePolicyError(ExecutionContractError):
    """Typed fail-closed refusal raised before an operation is dispatched."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, message, safe_retry=True, details=details, stage="pre_dispatch")


def _fail(code: str, message: str, **details: Any) -> None:
    raise ResourcePolicyError(code, message, details=details or None)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("resource-policy value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 1024 and not any(ord(c) < 32 for c in value)


def _checked_int(value: Any, label: str, *, minimum: int = 0, maximum: int = _MAX_SQLITE_INT) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _checked_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must be a conservative identifier")
    return value


def _path_key(path: str, platform: str) -> str:
    if not _nonempty(path):
        raise ValueError("path must be a nonempty string")
    if platform == "windows":
        normalized = ntpath.normpath(path)
        drive, tail = ntpath.splitdrive(normalized)
        if not drive or not tail.startswith(("\\", "/")):
            raise ValueError("Windows policy paths must be absolute drive or UNC paths")
        return ntpath.normcase(normalized)
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        raise ValueError("POSIX policy paths must be absolute")
    return normalized


def _path_is_within(path: str, root: str, platform: str) -> bool:
    p = _path_key(path, platform)
    r = _path_key(root, platform)
    module = ntpath if platform == "windows" else posixpath
    try:
        return module.commonpath((p, r)) == r
    except ValueError:
        return False


def _is_secret_key(key: str) -> bool:
    folded = re.sub(r"[^a-z0-9]", "", key.casefold())
    return folded in _SENSITIVE_NAMES or folded.endswith(("token", "password", "secret", "credential", "privatekey"))


def _reject_secrets(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or _is_secret_key(key):
                raise ValueError(f"{label} cannot contain credentials or secret material")
            _reject_secrets(child, label)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child, label)


@dataclass(frozen=True)
class PathIdentity:
    path: str
    volume_id: str
    file_id: str
    owner_id: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "volume_id": self.volume_id, "file_id": self.file_id, "owner_id": self.owner_id}


@dataclass(frozen=True)
class PathGrant:
    path: str
    rights: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "rights": list(self.rights)}


@dataclass(frozen=True)
class NetworkGrant:
    kind: str
    endpoint: str
    port: int

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "endpoint": self.endpoint, "port": self.port}


@dataclass(frozen=True)
class LaunchImage:
    role: str
    path: str
    image_sha256: str
    argv_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "path": self.path, "image_sha256": self.image_sha256,
                "argv_sha256": self.argv_sha256}


@dataclass(frozen=True)
class DiskBudgetPolicy:
    """Trusted daemon-start budget; request data cannot alter these limits."""

    mode: str
    reserve_free_bytes: int
    max_task_reservation_bytes: int
    quota_profile_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "reserve_free_bytes": self.reserve_free_bytes,
                "max_task_reservation_bytes": self.max_task_reservation_bytes,
                "quota_profile_id": self.quota_profile_id}


@dataclass(frozen=True)
class HostResourceProfile:
    """Immutable trusted host policy loaded by startup, never from execution JSON."""

    profile_id: str
    project_id: str
    platform: str
    host_id: str
    root: PathIdentity
    launcher_account_id: str
    service_account_id: str
    interactive_account_id: str
    applied_policy_id: str
    launch_images: tuple[LaunchImage, ...]
    file_grants: tuple[PathGrant, ...]
    network_grants: tuple[NetworkGrant, ...]
    disk_budget: DiskBudgetPolicy
    allowed_operations: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_profile(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "platform": self.platform,
            "host_id": self.host_id,
            "root": self.root.as_dict(),
            "launcher_account_id": self.launcher_account_id,
            "service_account_id": self.service_account_id,
            "interactive_account_id": self.interactive_account_id,
            "applied_policy_id": self.applied_policy_id,
            "launch_images": [item.as_dict() for item in self.launch_images],
            "file_grants": [item.as_dict() for item in self.file_grants],
            "network_grants": [item.as_dict() for item in self.network_grants],
            "disk_budget": self.disk_budget.as_dict(),
            "allowed_operations": list(self.allowed_operations),
        }

    @property
    def sha256(self) -> str:
        return _sha256(self.as_dict())


def validate_profile(profile: HostResourceProfile) -> None:
    if not isinstance(profile, HostResourceProfile):
        raise TypeError("profile must be a host-configured HostResourceProfile")
    for value, label in ((profile.profile_id, "profile_id"), (profile.project_id, "project_id"),
                         (profile.host_id, "host_id"), (profile.applied_policy_id, "applied_policy_id"),
                         (profile.launcher_account_id, "launcher_account_id"),
                         (profile.service_account_id, "service_account_id"),
                         (profile.interactive_account_id, "interactive_account_id")):
        _checked_identifier(value, label)
    if profile.platform not in {"windows", "macos"}:
        raise ValueError("platform must be windows or macos")
    if profile.service_account_id == profile.interactive_account_id:
        raise ValueError("service and interactive accounts must be distinct")
    root = profile.root
    if not isinstance(root, PathIdentity):
        raise ValueError("root identity is required")
    _path_key(root.path, profile.platform)
    for value, label in ((root.volume_id, "root.volume_id"), (root.file_id, "root.file_id"),
                         (root.owner_id, "root.owner_id")):
        _checked_identifier(value, label)
    if not isinstance(profile.launch_images, tuple) or len(profile.launch_images) < 3:
        raise ValueError("launcher, server, and worker image expectations are required")
    roles = [item.role for item in profile.launch_images if isinstance(item, LaunchImage)]
    if len(roles) != len(profile.launch_images) or len(set(roles)) != len(roles):
        raise ValueError("launch roles must be typed and unique")
    if not {"launcher", "server", "worker"} <= set(roles):
        raise ValueError("launcher, server, and worker must be bound")
    for item in profile.launch_images:
        _checked_identifier(item.role, "launch role")
        _path_key(item.path, profile.platform)
        if not _is_sha256(item.image_sha256) or not _is_sha256(item.argv_sha256):
            raise ValueError("launch image and argv must have SHA-256 bindings")
    if not isinstance(profile.file_grants, tuple):
        raise ValueError("file_grants must be a tuple")
    file_grant_keys: set[tuple[str, tuple[str, ...]]] = set()
    for grant in profile.file_grants:
        if not isinstance(grant, PathGrant) or not grant.rights or any(right not in {"READ", "WRITE", "EXECUTE"} for right in grant.rights):
            raise ValueError("file grants contain unsupported rights")
        _path_key(grant.path, profile.platform)
        if len(set(grant.rights)) != len(grant.rights):
            raise ValueError("file grants contain duplicate rights")
        grant_key = _grant_key(grant, profile.platform)
        if grant_key in file_grant_keys:
            raise ValueError("file grants contain duplicate path/right scopes")
        file_grant_keys.add(grant_key)
        if "WRITE" in grant.rights and not _path_is_within(grant.path, root.path, profile.platform):
            raise ValueError("writable file grants must stay inside the exact project root")
    if not isinstance(profile.network_grants, tuple):
        raise ValueError("network_grants must be a tuple")
    network_grant_keys: set[tuple[str, str, int]] = set()
    for grant in profile.network_grants:
        _validate_network_grant(grant)
        grant_key = _network_key(grant)
        if grant_key in network_grant_keys:
            raise ValueError("network grants contain duplicate endpoint scopes")
        network_grant_keys.add(grant_key)
    if not isinstance(profile.disk_budget, DiskBudgetPolicy):
        raise ValueError("disk_budget policy is required")
    if profile.disk_budget.mode not in {"ADMISSION_ONLY", "REQUIRE_HARD_QUOTA"}:
        raise ValueError("unsupported disk budget mode")
    _checked_int(profile.disk_budget.reserve_free_bytes, "reserve_free_bytes")
    _checked_int(profile.disk_budget.max_task_reservation_bytes, "max_task_reservation_bytes", minimum=1)
    if profile.disk_budget.mode == "REQUIRE_HARD_QUOTA":
        _checked_identifier(profile.disk_budget.quota_profile_id, "quota_profile_id")
    elif profile.disk_budget.quota_profile_id is not None:
        _checked_identifier(profile.disk_budget.quota_profile_id, "quota_profile_id")
    if not profile.allowed_operations or any(not _checked_identifier(name, "allowed operation") for name in profile.allowed_operations):
        raise ValueError("allowed_operations must be explicit")
    if len(set(profile.allowed_operations)) != len(profile.allowed_operations):
        raise ValueError("allowed_operations contains duplicates")


def _validate_network_grant(grant: NetworkGrant) -> None:
    if not isinstance(grant, NetworkGrant) or grant.kind not in {"LOOPBACK_ENDPOINT", "LICENSE_ENDPOINT"}:
        raise ValueError("broad or unknown network grants are unsupported")
    if not _nonempty(grant.endpoint):
        raise ValueError("network endpoint is required")
    if type(grant.port) is not int or not 1 <= grant.port <= 65535:
        raise ValueError("network endpoint port is invalid")
    if grant.kind == "LOOPBACK_ENDPOINT":
        try:
            if not ipaddress.ip_address(grant.endpoint).is_loopback:
                raise ValueError("loopback grant must name an exact loopback address")
        except ValueError as exc:
            raise ValueError("loopback grant must name one literal loopback address") from exc
    elif grant.endpoint in {"*", "0.0.0.0", "::", "0.0.0.0/0", "::/0"} or "/" in grant.endpoint:
        raise ValueError("license grant must name one exact endpoint")


@dataclass(frozen=True)
class ProcessWitness:
    role: str
    pid: int
    birth_id: str
    image_path: str
    image_sha256: str
    argv_sha256: str
    account_id: str
    policy_id: str | None
    parent_pid: int | None
    parent_birth_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "pid": self.pid, "birth_id": self.birth_id,
                "image_path": self.image_path, "image_sha256": self.image_sha256,
                "argv_sha256": self.argv_sha256, "account_id": self.account_id,
                "policy_id": self.policy_id, "parent_pid": self.parent_pid,
                "parent_birth_id": self.parent_birth_id}


@dataclass(frozen=True)
class StorageObservation:
    filesystem_id: str
    root_path: str
    volume_id: str
    root_file_id: str
    free_bytes: int
    total_bytes: int
    quota_state: str
    quota_id: str | None
    quota_scope_id: str | None
    quota_limit_bytes: int | None
    quota_used_bytes: int | None
    observed_monotonic_ns: int
    # Only a future in-process native filesystem/quota adapter may attach this
    # exact token. JSON, requests, fixtures and ordinary constructors cannot
    # turn claimed quota values into an enforceable host observation.
    _native_attestation: object | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {"filesystem_id": self.filesystem_id, "root_path": self.root_path,
                "volume_id": self.volume_id, "root_file_id": self.root_file_id,
                "free_bytes": self.free_bytes, "total_bytes": self.total_bytes,
                "quota_state": self.quota_state, "quota_id": self.quota_id,
                "quota_scope_id": self.quota_scope_id, "quota_limit_bytes": self.quota_limit_bytes,
                "quota_used_bytes": self.quota_used_bytes,
                "observed_monotonic_ns": self.observed_monotonic_ns}


@dataclass(frozen=True)
class HostResourceSnapshot:
    """Typed provider observation; a mapping or caller assertion is not accepted."""

    profile_sha256: str
    platform: str
    host_id: str
    project_id: str
    root: PathIdentity
    launcher_account_id: str
    service_account_id: str
    interactive_account_id: str
    applied_policy_id: str
    process_tree_state: str
    file_policy_state: str
    network_policy_state: str
    processes: tuple[ProcessWitness, ...]
    file_grants: tuple[PathGrant, ...]
    network_grants: tuple[NetworkGrant, ...]
    storage: StorageObservation
    observed_monotonic_ns: int

    def as_dict(self) -> dict[str, Any]:
        return {"schema": SNAPSHOT_SCHEMA, "profile_sha256": self.profile_sha256,
                "platform": self.platform, "host_id": self.host_id, "project_id": self.project_id,
                "root": self.root.as_dict(), "launcher_account_id": self.launcher_account_id,
                "service_account_id": self.service_account_id,
                "interactive_account_id": self.interactive_account_id,
                "applied_policy_id": self.applied_policy_id,
                "process_tree_state": self.process_tree_state, "file_policy_state": self.file_policy_state,
                "network_policy_state": self.network_policy_state,
                "processes": [item.as_dict() for item in self.processes],
                "file_grants": [item.as_dict() for item in self.file_grants],
                "network_grants": [item.as_dict() for item in self.network_grants],
                "storage": self.storage.as_dict(), "observed_monotonic_ns": self.observed_monotonic_ns}


@dataclass(frozen=True)
class SnapshotAssessment:
    capability_state: CapabilityState
    structurally_consistent: bool
    reasons: tuple[str, ...]
    snapshot_sha256: str | None


def assess_snapshot(profile: HostResourceProfile, snapshot: Any, *, now_monotonic_ns: int,
                    max_age_ns: int = 30_000_000_000) -> SnapshotAssessment:
    """Validate a provider payload without treating fixtures as native proof."""
    validate_profile(profile)
    if not isinstance(snapshot, HostResourceSnapshot):
        return SnapshotAssessment(CapabilityState.IMPLEMENTED_UNVERIFIED, False,
                                  ("TYPED_PROVIDER_SNAPSHOT_REQUIRED",), None)
    reasons: list[str] = []
    if not _is_sha256(snapshot.profile_sha256) or snapshot.profile_sha256 != profile.sha256:
        reasons.append("PROFILE_HASH_MISMATCH")
    if snapshot.platform != profile.platform or snapshot.host_id != profile.host_id:
        reasons.append("HOST_BINDING_MISMATCH")
    if snapshot.project_id != profile.project_id:
        reasons.append("PROJECT_BINDING_MISMATCH")
    if not isinstance(snapshot.root, PathIdentity) or snapshot.root != profile.root:
        reasons.append("ROOT_IDENTITY_MISMATCH")
    if (snapshot.launcher_account_id != profile.launcher_account_id
            or snapshot.service_account_id != profile.service_account_id
            or snapshot.interactive_account_id != profile.interactive_account_id
            or snapshot.service_account_id == snapshot.interactive_account_id):
        reasons.append("ACCOUNT_IDENTITY_MISMATCH")
    if snapshot.applied_policy_id != profile.applied_policy_id:
        reasons.append("APPLIED_POLICY_MISMATCH")
    if snapshot.process_tree_state != "COMPLETE":
        reasons.append("PROCESS_TREE_INCOMPLETE")
    if snapshot.file_policy_state != "COMPLETE":
        reasons.append("FILE_POLICY_INCOMPLETE")
    if snapshot.network_policy_state != "COMPLETE":
        reasons.append("NETWORK_POLICY_INCOMPLETE")
    clock_valid = (type(now_monotonic_ns) is int and type(max_age_ns) is int and max_age_ns > 0)
    if not clock_valid:
        reasons.append("INVALID_CLOCK_WINDOW")
    if clock_valid:
        try:
            _checked_int(snapshot.observed_monotonic_ns, "snapshot.observed_monotonic_ns")
        except ValueError:
            reasons.append("SNAPSHOT_TIMESTAMP_INVALID")
    if clock_valid and "SNAPSHOT_TIMESTAMP_INVALID" not in reasons:
        if now_monotonic_ns < snapshot.observed_monotonic_ns:
            reasons.append("OBSERVATION_FROM_FUTURE")
        elif now_monotonic_ns - snapshot.observed_monotonic_ns > max_age_ns:
            reasons.append("OBSERVATION_STALE")
    _assess_processes(profile, snapshot.processes, reasons)
    _assess_file_grants(profile, snapshot.file_grants, reasons)
    _assess_network_grants(profile, snapshot.network_grants, reasons)
    _assess_storage(profile, snapshot.storage, now_monotonic_ns, max_age_ns, reasons)
    try:
        snapshot_hash = _sha256(snapshot.as_dict())
    except (TypeError, ValueError):
        snapshot_hash = None
        reasons.append("SNAPSHOT_ENCODING_INVALID")
    # This module currently has no native host adapters. Even a fully matching
    # synthetic provider observation remains SOFTWARE_ONLY/UNVERIFIED.
    return SnapshotAssessment(CapabilityState.IMPLEMENTED_UNVERIFIED, not reasons,
                              tuple(dict.fromkeys(reasons)), snapshot_hash)


def _assess_processes(profile: HostResourceProfile, processes: Any, reasons: list[str]) -> None:
    if not isinstance(processes, tuple) or any(not isinstance(p, ProcessWitness) for p in processes):
        reasons.append("PROCESS_WITNESS_SCHEMA_INVALID")
        return
    by_pid: dict[int, ProcessWitness] = {}
    by_role: dict[str, ProcessWitness] = {}
    expected = {item.role: item for item in profile.launch_images}
    for process in processes:
        if type(process.pid) is not int or process.pid <= 0 or not _nonempty(process.birth_id):
            reasons.append("PROCESS_BIRTH_ID_INVALID")
            continue
        if process.pid in by_pid or process.role in by_role:
            reasons.append("PROCESS_IDENTITY_DUPLICATED")
            continue
        by_pid[process.pid] = process
        by_role[process.role] = process
        if process.role not in expected:
            reasons.append("UNDECLARED_PROCESS_ROLE")
            continue
        image = expected[process.role]
        try:
            observed_path = _path_key(process.image_path, profile.platform)
            expected_path = _path_key(image.path, profile.platform)
        except ValueError:
            reasons.append("PROCESS_IMAGE_PATH_INVALID")
            continue
        if (observed_path != expected_path or process.image_sha256 != image.image_sha256
                or process.argv_sha256 != image.argv_sha256):
            reasons.append("PROCESS_IMAGE_OR_LAUNCH_MISMATCH")
        if process.role == "launcher":
            if process.account_id != profile.launcher_account_id or process.policy_id is not None:
                reasons.append("LAUNCHER_IDENTITY_MISMATCH")
            if process.parent_pid is not None or process.parent_birth_id is not None:
                reasons.append("LAUNCHER_PARENT_UNBOUND")
        else:
            if process.account_id != profile.service_account_id or process.policy_id != profile.applied_policy_id:
                reasons.append("PROCESS_POLICY_INHERITANCE_MISSING")
            if (type(process.parent_pid) is not int or process.parent_birth_id is None):
                reasons.append("PROCESS_PARENT_IDENTITY_MISSING")
    if not {"launcher", "server", "worker"} <= set(by_role):
        reasons.append("REQUIRED_PROCESS_MISSING")
    for process in processes:
        if process.role == "launcher":
            continue
        parent = by_pid.get(process.parent_pid)
        if parent is None or parent.birth_id != process.parent_birth_id:
            reasons.append("PROCESS_PARENT_BIRTH_MISMATCH")
            continue
        seen: set[int] = set()
        cursor = process
        while cursor.role != "launcher":
            if cursor.pid in seen:
                reasons.append("PROCESS_TREE_CYCLE")
                break
            seen.add(cursor.pid)
            cursor = by_pid.get(cursor.parent_pid)
            if cursor is None:
                reasons.append("PROCESS_TREE_DISCONNECTED")
                break


def _grant_key(grant: PathGrant, platform: str) -> tuple[str, tuple[str, ...]]:
    return (_path_key(grant.path, platform), tuple(sorted(grant.rights)))


def _assess_file_grants(profile: HostResourceProfile, grants: Any, reasons: list[str]) -> None:
    if not isinstance(grants, tuple) or any(not isinstance(item, PathGrant) for item in grants):
        reasons.append("FILE_GRANT_SCHEMA_INVALID")
        return
    try:
        actual = [_grant_key(item, profile.platform) for item in grants]
        expected = [_grant_key(item, profile.platform) for item in profile.file_grants]
    except ValueError:
        reasons.append("FILE_GRANT_PATH_INVALID")
        return
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        reasons.append("FILE_POLICY_SCOPE_MISMATCH")


def _network_key(grant: NetworkGrant) -> tuple[str, str, int]:
    _validate_network_grant(grant)
    endpoint = grant.endpoint.casefold() if grant.endpoint.casefold() == "localhost" else str(ipaddress.ip_address(grant.endpoint))
    return grant.kind, endpoint, grant.port


def _assess_network_grants(profile: HostResourceProfile, grants: Any, reasons: list[str]) -> None:
    if not isinstance(grants, tuple) or any(not isinstance(item, NetworkGrant) for item in grants):
        reasons.append("NETWORK_GRANT_SCHEMA_INVALID")
        return
    try:
        actual = [_network_key(item) for item in grants]
        expected = [_network_key(item) for item in profile.network_grants]
    except ValueError:
        reasons.append("BROAD_OR_UNKNOWN_NETWORK_RIGHT")
        return
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        reasons.append("NETWORK_POLICY_SCOPE_MISMATCH")


def _assess_storage(profile: HostResourceProfile, storage: Any, now_ns: int,
                    max_age_ns: int, reasons: list[str]) -> None:
    if not isinstance(storage, StorageObservation):
        reasons.append("STORAGE_OBSERVATION_SCHEMA_INVALID")
        return
    try:
        _checked_identifier(storage.filesystem_id, "filesystem_id")
        _checked_int(storage.free_bytes, "free_bytes")
        _checked_int(storage.total_bytes, "total_bytes", minimum=1)
        _checked_int(storage.observed_monotonic_ns, "storage.observed_monotonic_ns")
        if storage.free_bytes > storage.total_bytes:
            reasons.append("STORAGE_CAPACITY_INVALID")
    except ValueError:
        reasons.append("STORAGE_OBSERVATION_INVALID")
    if (storage.root_path != profile.root.path or storage.volume_id != profile.root.volume_id
            or storage.root_file_id != profile.root.file_id):
        reasons.append("STORAGE_ROOT_OR_VOLUME_CHANGED")
    if storage.quota_state == QuotaState.VERIFIED_HARD_QUOTA.value:
        if (not _nonempty(storage.quota_id) or not _nonempty(storage.quota_scope_id)
                or type(storage.quota_limit_bytes) is not int or storage.quota_limit_bytes <= 0
                or type(storage.quota_used_bytes) is not int or storage.quota_used_bytes < 0
                or storage.quota_used_bytes > storage.quota_limit_bytes):
            reasons.append("HARD_QUOTA_EVIDENCE_INVALID")
        if profile.disk_budget.quota_profile_id and storage.quota_id != profile.disk_budget.quota_profile_id:
            reasons.append("HARD_QUOTA_PROFILE_MISMATCH")
        if storage._native_attestation is not _NATIVE_STORAGE_ATTESTATION:
            reasons.append("HARD_QUOTA_NATIVE_PROVIDER_NOT_CONFIGURED")
    elif storage.quota_state in {QuotaState.NOT_CONFIGURED.value, QuotaState.UNSUPPORTED.value,
                                  QuotaState.ADMISSION_ONLY.value}:
        if any(value is not None for value in (storage.quota_id, storage.quota_scope_id,
                                                storage.quota_limit_bytes, storage.quota_used_bytes)):
            reasons.append("UNVERIFIED_QUOTA_HAS_ASSERTED_LIMITS")
        if profile.disk_budget.mode == "REQUIRE_HARD_QUOTA":
            reasons.append("HARD_QUOTA_NOT_VERIFIED")
    else:
        reasons.append("QUOTA_STATE_UNSUPPORTED")
    if type(now_ns) is int and type(max_age_ns) is int:
        if now_ns < storage.observed_monotonic_ns:
            reasons.append("STORAGE_OBSERVATION_FROM_FUTURE")
        elif now_ns - storage.observed_monotonic_ns > max_age_ns:
            reasons.append("STORAGE_OBSERVATION_STALE")


class HostResourceProvider:
    """Startup-injected provider base; no provider implementation ships yet."""

    def read_snapshot(self, profile: HostResourceProfile) -> HostResourceSnapshot:
        raise NotImplementedError


class NativeHostResourceProvider(HostResourceProvider):
    """Marker contract for a future read-only OS adapter installed by startup.

    A subclass must inspect effective OS state and process birth identities.
    Data supplied by an MCP request or a synthetic test provider is never an
    instance of this trusted dependency.
    """

    pass


@dataclass(frozen=True)
class VerifiedLaunchBinding:
    profile_id: str
    profile_sha256: str
    project_id: str
    root_file_id: str
    host_id: str
    snapshot_sha256: str
    capability_state: CapabilityState = CapabilityState.VERIFIED


def require_profile_before_dispatch(*, profile: HostResourceProfile | None,
                                    provider: HostResourceProvider | None,
                                    authority: Any, project_id: str,
                                    request_claims: Mapping[str, Any] | None = None,
                                    now_monotonic_ns: int,
                                    max_age_ns: int = 30_000_000_000) -> VerifiedLaunchBinding:
    """Require trusted startup dependencies; reject wire booleans as proof."""
    if request_claims is not None and "os_sandbox" in request_claims:
        _fail("CALLER_SANDBOX_CLAIM_FORBIDDEN", "request fields cannot prove host isolation")
    if profile is None:
        _fail("OS_RESOURCE_PROFILE_NOT_CONFIGURED", "trusted host resource profile is absent",
              capability_state=CapabilityState.NOT_CONFIGURED.value)
    if not isinstance(profile, HostResourceProfile) or profile.project_id != project_id:
        _fail("OS_RESOURCE_PROFILE_MISMATCH", "trusted profile is not bound to the admitted project")
    if provider is None:
        _fail("OS_RESOURCE_PROVIDER_NOT_CONFIGURED", "native host resource provider is not installed",
              capability_state=CapabilityState.NOT_CONFIGURED.value)
    if not isinstance(provider, NativeHostResourceProvider):
        _fail("OS_RESOURCE_PROVIDER_UNTRUSTED", "only the startup-installed native provider may attest host state",
              capability_state=CapabilityState.IMPLEMENTED_UNVERIFIED.value)
    _authorize_profile_project(authority, profile, project_id, "compute", code_prefix="OS_RESOURCE")
    try:
        snapshot = provider.read_snapshot(profile)
    except Exception as exc:
        _fail("OS_RESOURCE_PROVIDER_UNAVAILABLE", "native host state could not be read",
              capability_state=CapabilityState.IMPLEMENTED_UNVERIFIED.value,
              cause_type=type(exc).__name__)
    assessment = assess_snapshot(profile, snapshot, now_monotonic_ns=now_monotonic_ns,
                                 max_age_ns=max_age_ns)
    if not assessment.structurally_consistent:
        _fail("OS_RESOURCE_PROOF_INVALID", "native host observation does not match the trusted profile",
              capability_state=assessment.capability_state.value, reasons=list(assessment.reasons))
    # No NativeHostResourceProvider subclass is installed or connected in this
    # software unit. This return path is reserved for a real adapter and is
    # deliberately not exercised by synthetic fixtures.
    if assessment.capability_state is not CapabilityState.VERIFIED:
        _fail("OS_RESOURCE_NATIVE_PROOF_UNAVAILABLE", "host evidence is not backed by a production OS verifier",
              capability_state=assessment.capability_state.value)
    return VerifiedLaunchBinding(profile.profile_id, profile.sha256, project_id,
                                 profile.root.file_id, profile.host_id, assessment.snapshot_sha256 or "")


def _authorize_project(authority: Any, project_id: str, permission: str) -> dict[str, Any]:
    from ._project_authority import ProjectAuthority
    if not isinstance(authority, ProjectAuthority):
        _fail("PROJECT_AUTHORITY_REQUIRED", "host resource actions require the existing ProjectAuthority")
    try:
        return authority.authorize_operation(project_id, permission)
    except ExecutionContractError as exc:
        _fail("PROJECT_PERMISSION_REQUIRED", "project authority denied the host resource action",
              permission=permission, reason=exc.code)


def _authorize_profile_project(authority: Any, profile: HostResourceProfile,
                               project_id: str, permission: str, *,
                               code_prefix: str) -> dict[str, Any]:
    project = _authorize_project(authority, project_id, permission)
    if _path_key(project.get("workspace", ""), profile.platform) != _path_key(profile.root.path, profile.platform):
        _fail(f"{code_prefix}_ROOT_AUTHORITY_MISMATCH",
              "trusted profile root differs from ProjectAuthority workspace")
    if os.name == "nt":
        _fail(f"{code_prefix}_ROOT_IDENTITY_PROVIDER_NOT_CONFIGURED",
              "Windows project root identity verification is not connected")
    try:
        info = Path(profile.root.path).lstat()
    except OSError as exc:
        _fail(f"{code_prefix}_ROOT_IDENTITY_UNKNOWN", "project root identity could not be read",
              cause_type=type(exc).__name__)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or str(int(info.st_dev)) != profile.root.volume_id
            or str(int(info.st_ino)) != profile.root.file_id
            or str(int(info.st_uid)) != profile.root.owner_id):
        _fail(f"{code_prefix}_ROOT_IDENTITY_MISMATCH",
              "project root no longer has its configured volume, directory, and owner identity")
    return project


@dataclass(frozen=True)
class DiskAdmission:
    accepted: bool
    requested_bytes: int
    available_bytes: int
    enforcement: str
    scope_id: str
    observation_sha256: str
    evidence_classification: str = "SOFTWARE_ONLY"


@dataclass(frozen=True)
class HostChangePlan:
    """Immutable, reviewable proposal; never an instruction to mutate the host."""

    payload_json: str
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("host change plan payload is not an object")
        return value


@dataclass(frozen=True)
class HostChangeApproval:
    """Explicit host-control reference bound to one exact plan fingerprint."""

    plan_sha256: str
    authorization_ref: str


@dataclass(frozen=True)
class HostChangeVerification:
    status: str
    capability_state: CapabilityState
    plan_sha256: str
    evidence_classification: str = "SOFTWARE_ONLY"
    reasons: tuple[str, ...] = ("NATIVE_HOST_CHANGE_OBSERVER_NOT_CONFIGURED",)


@dataclass(frozen=True)
class AppliedHostChangeReceipt:
    """Typed future-adapter receipt consumed only by the restore-plan builder."""

    plan_sha256: str
    host_id: str
    platform: str
    actions: tuple[Mapping[str, Any], ...]
    receipt_sha256: str


def build_host_change_plan(profile: HostResourceProfile, *,
                           change_manifest: Sequence[Mapping[str, Any]],
                           operator_inputs: Mapping[str, Any],
                           backup_manifest: Sequence[Mapping[str, Any]] = ()) -> HostChangePlan:
    """Create an exact plan-only artifact with explicit missing OS baselines.

    The manifest describes operator intent. Its baseline and backup fields are
    carried as proposals, never treated as observations of the current host.
    """
    validate_profile(profile)
    if not isinstance(change_manifest, (list, tuple)) or not change_manifest:
        raise ValueError("change_manifest must contain at least one exact action")
    if not isinstance(operator_inputs, Mapping):
        raise ValueError("operator_inputs must be an object")
    if not isinstance(backup_manifest, (list, tuple)):
        raise ValueError("backup_manifest must be a list")
    _reject_secrets(operator_inputs, "operator_inputs")
    _reject_secrets(change_manifest, "change_manifest")
    _reject_secrets(backup_manifest, "backup_manifest")

    allowed_action_fields = {
        "action_id", "resource_kind", "resource_name", "operation", "desired",
        "baseline_identity", "baseline_sha256", "backup_path", "backup_sha256",
    }
    allowed_kinds = {"service_account", "appcontainer_profile", "file_acl",
                     "network_rule", "hard_quota", "launch_policy"}
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in change_manifest:
        if not isinstance(raw, Mapping) or set(raw) != allowed_action_fields:
            raise ValueError("each change action must supply the exact review fields")
        action_id = _checked_identifier(raw["action_id"], "action_id")
        kind = raw["resource_kind"]
        if kind not in allowed_kinds:
            raise ValueError("unsupported host resource kind")
        resource_name = _checked_identifier(raw["resource_name"], "resource_name")
        operation = raw["operation"]
        if operation not in {"CREATE", "UPDATE"}:
            raise ValueError("host plan only supports explicit CREATE or UPDATE proposals")
        if action_id in seen:
            raise ValueError("host change action IDs must be unique")
        seen.add(action_id)
        desired = raw["desired"]
        if not isinstance(desired, Mapping):
            raise ValueError("desired resource state must be an object")
        _reject_secrets(desired, "desired resource state")
        _validate_desired_resource(kind, desired, profile)
        baseline_identity = raw["baseline_identity"]
        baseline_sha256 = raw["baseline_sha256"]
        backup_path = raw["backup_path"]
        backup_sha256 = raw["backup_sha256"]
        if operation == "CREATE":
            if any(value is not None for value in (baseline_identity, baseline_sha256,
                                                   backup_path, backup_sha256)):
                raise ValueError("CREATE proposals cannot claim a prior resource or backup")
        else:
            if (not isinstance(baseline_identity, Mapping) or not baseline_identity
                    or not _is_sha256(baseline_sha256)
                    or not _nonempty(backup_path) or not _is_sha256(backup_sha256)):
                raise ValueError("UPDATE proposal requires exact prior identity, hash and backup reference")
            _reject_secrets(baseline_identity, "baseline identity")
            if not _path_is_within(backup_path, profile.root.path, profile.platform):
                raise ValueError("backup paths must remain inside the exact project root")
        actions.append({
            "action_id": action_id, "resource_kind": kind, "resource_name": resource_name,
            "operation": operation, "desired": dict(desired),
            "proposed_baseline": (dict(baseline_identity) if baseline_identity is not None else None),
            "proposed_baseline_sha256": baseline_sha256,
            "proposed_backup_path": backup_path, "proposed_backup_sha256": backup_sha256,
            "baseline_evidence_state": "NOT_CAPTURED",
        })
    backups: list[dict[str, Any]] = []
    backup_fields = {"resource_name", "path", "sha256", "identity"}
    for raw in backup_manifest:
        if not isinstance(raw, Mapping) or set(raw) != backup_fields:
            raise ValueError("backup entries must bind exact path, identity and hash")
        path = _path_key(raw["path"], profile.platform)
        if not _path_is_within(path, profile.root.path, profile.platform):
            raise ValueError("backup target must remain inside the exact project root")
        if not _is_sha256(raw["sha256"]) or not isinstance(raw["identity"], Mapping) or not raw["identity"]:
            raise ValueError("backup entries require an identity and SHA-256")
        backups.append({"resource_name": _checked_identifier(raw["resource_name"], "backup resource"),
                        "path": path, "sha256": raw["sha256"],
                        "identity": dict(raw["identity"]), "evidence_state": "NOT_CAPTURED"})
    payload = {
        "schema": PLAN_SCHEMA,
        "plan_status": "PLAN_ONLY_UNVERIFIED",
        "evidence_classification": "SOFTWARE_ONLY",
        "host": {"platform": profile.platform, "host_id": profile.host_id},
        "project_id": profile.project_id,
        "profile": profile.as_dict(),
        "profile_sha256": profile.sha256,
        "operator_inputs": dict(operator_inputs),
        "operator_inputs_sha256": _sha256(operator_inputs),
        "launch_image_bindings": [item.as_dict() for item in profile.launch_images],
        "path_rights": [item.as_dict() for item in profile.file_grants],
        "change_manifest": actions,
        "backup_manifest": backups,
        "rollback_status": "NOT_EXECUTABLE_WITHOUT_VERIFIED_BASELINE_AND_NATIVE_ADAPTER",
        "native_baseline_status": "NOT_CAPTURED",
    }
    serialized = _canonical_json(payload)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return HostChangePlan(serialized, digest)


def _validate_desired_resource(kind: str, desired: Mapping[str, Any],
                               profile: HostResourceProfile) -> None:
    fields = set(desired)
    if kind == "file_acl":
        if fields != {"path", "rights"}:
            raise ValueError("file_acl desired state must name one path and exact rights")
        rights = desired["rights"]
        if not isinstance(rights, (list, tuple)) or not rights or any(
                item not in {"READ", "WRITE", "EXECUTE"} for item in rights):
            raise ValueError("file_acl rights are unsupported")
        if len(set(rights)) != len(rights):
            raise ValueError("file_acl rights cannot contain duplicates")
        grant = PathGrant(desired["path"], tuple(rights))
        if "WRITE" in grant.rights and not _path_is_within(grant.path, profile.root.path, profile.platform):
            raise ValueError("writable file grants must stay inside the exact project root")
    elif kind == "network_rule":
        if fields != {"kind", "endpoint", "port"}:
            raise ValueError("network rule desired state must name one exact endpoint")
        _validate_network_grant(NetworkGrant(desired["kind"], desired["endpoint"], desired["port"]))
    elif kind == "hard_quota":
        if fields != {"quota_id", "root_path", "limit_bytes"}:
            raise ValueError("hard quota must name an exact quota, root and byte ceiling")
        _checked_identifier(desired["quota_id"], "quota_id")
        if _path_key(desired["root_path"], profile.platform) != _path_key(profile.root.path, profile.platform):
            raise ValueError("quota path must bind the exact project root")
        _checked_int(desired["limit_bytes"], "quota limit", minimum=1)
    elif kind == "service_account":
        if fields != {"account_id", "role"} or desired["role"] != "service":
            raise ValueError("service account proposal must name one exact service identity")
        account = _checked_identifier(desired["account_id"], "service account")
        if account != profile.service_account_id or account == profile.interactive_account_id:
            raise ValueError("service account proposal does not match the trusted distinct service identity")
    elif kind == "appcontainer_profile":
        if profile.platform != "windows":
            raise ValueError("AppContainer policy is only applicable to Windows profiles")
        if fields != {"profile_id", "policy_sha256"}:
            raise ValueError("AppContainer proposal must bind an exact profile and policy hash")
        if (desired["profile_id"] != profile.applied_policy_id
                or not _is_sha256(desired["policy_sha256"])):
            raise ValueError("AppContainer profile proposal identity/hash is invalid")
    elif kind == "launch_policy":
        if fields != {"role", "image_sha256", "argv_sha256"}:
            raise ValueError("launch policy proposal must bind exact image and argv hashes")
        role = _checked_identifier(desired["role"], "launch role")
        matches = [item for item in profile.launch_images if item.role == role]
        if (len(matches) != 1 or desired["image_sha256"] != matches[0].image_sha256
                or desired["argv_sha256"] != matches[0].argv_sha256):
            raise ValueError("launch policy proposal differs from the trusted image bindings")


def validate_host_change_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, HostChangePlan) or not _is_sha256(plan.sha256):
        _fail("HOST_PLAN_INVALID", "host change plan must be a typed immutable plan")
    if hashlib.sha256(plan.payload_json.encode("utf-8")).hexdigest() != plan.sha256:
        _fail("HOST_PLAN_TAMPERED", "host change plan fingerprint does not match its payload")
    try:
        payload = plan.as_dict()
    except (ValueError, TypeError, json.JSONDecodeError):
        _fail("HOST_PLAN_INVALID", "host change plan payload is malformed")
    if (payload.get("schema") != PLAN_SCHEMA or payload.get("plan_status") != "PLAN_ONLY_UNVERIFIED"
            or payload.get("evidence_classification") != "SOFTWARE_ONLY"
            or payload.get("native_baseline_status") != "NOT_CAPTURED"
            or not isinstance(payload.get("change_manifest"), list)
            or not payload["change_manifest"]):
        _fail("HOST_PLAN_INVALID", "host change plan does not match the supported plan-only contract")
    return payload


def verify_host_change_plan(plan: HostChangePlan) -> HostChangeVerification:
    validate_host_change_plan(plan)
    return HostChangeVerification("NOT_CONFIGURED", CapabilityState.NOT_CONFIGURED, plan.sha256)


def apply_host_change_plan(plan: HostChangePlan, *, authority: Any,
                           approval: HostChangeApproval) -> None:
    """Fail closed: reviewable plan generation does not install an OS adapter."""
    payload = validate_host_change_plan(plan)
    if not isinstance(approval, HostChangeApproval) or approval.plan_sha256 != plan.sha256:
        _fail("HOST_PLAN_APPROVAL_MISMATCH", "explicit approval must bind this exact plan fingerprint")
    project_id = payload["project_id"]
    _verify_host_approval(authority, project_id, approval)
    _fail("HOST_CHANGE_ADAPTER_NOT_IMPLEMENTED",
          "no production OS host-change adapter is installed; no account, ACL, network, quota, or signing change was made",
          plan_sha256=plan.sha256, capability_state=CapabilityState.NOT_CONFIGURED.value)


def build_restore_plan(plan: HostChangePlan, receipt: AppliedHostChangeReceipt, *,
                       current_resource_sha256: Mapping[str, str | None]) -> dict[str, Any]:
    """Prepare exact rollback candidates while preserving resources that drifted."""
    payload = validate_host_change_plan(plan)
    if not isinstance(receipt, AppliedHostChangeReceipt) or receipt.plan_sha256 != plan.sha256:
        _fail("RESTORE_RECEIPT_MISMATCH", "restore requires a typed receipt for the exact host plan")
    if (receipt.host_id != payload["host"]["host_id"] or receipt.platform != payload["host"]["platform"]
            or not _is_sha256(receipt.receipt_sha256) or not isinstance(current_resource_sha256, Mapping)):
        _fail("RESTORE_RECEIPT_MISMATCH", "restore receipt host binding is malformed")
    by_action: dict[str, Mapping[str, Any]] = {}
    for action in receipt.actions:
        if not isinstance(action, Mapping) or set(action) != {
                "action_id", "created_resource_sha256", "prior_resource_sha256", "prior_backup_path"}:
            _fail("RESTORE_RECEIPT_MISMATCH", "applied host action receipt is malformed")
        action_id = _checked_identifier(action["action_id"], "action_id")
        if action_id in by_action:
            _fail("RESTORE_RECEIPT_MISMATCH", "applied host action receipt duplicates an action")
        if not _is_sha256(action["created_resource_sha256"]):
            _fail("RESTORE_RECEIPT_MISMATCH", "created resource identity hash is malformed")
        prior_sha = action["prior_resource_sha256"]
        if prior_sha is not None and not _is_sha256(prior_sha):
            _fail("RESTORE_RECEIPT_MISMATCH", "prior resource hash is malformed")
        prior_backup_path = action["prior_backup_path"]
        if prior_backup_path is not None:
            if not _nonempty(prior_backup_path) or not _path_is_within(prior_backup_path,
                                                                       payload["profile"]["root"]["path"],
                                                                       payload["host"]["platform"]):
                _fail("RESTORE_RECEIPT_MISMATCH", "prior backup path is outside the exact project root")
        original = next((item for item in payload["change_manifest"]
                         if item["action_id"] == action_id), None)
        if original is None:
            _fail("RESTORE_RECEIPT_MISMATCH", "receipt names an unknown host change")
        if original["operation"] == "UPDATE" and (
                prior_sha != original["proposed_baseline_sha256"]
                or prior_backup_path != original["proposed_backup_path"]):
            _fail("RESTORE_RECEIPT_MISMATCH", "prior backup does not match the exact proposed update baseline")
        if original["operation"] == "CREATE" and (prior_sha is not None or prior_backup_path is not None):
            _fail("RESTORE_RECEIPT_MISMATCH", "CREATE action receipt cannot claim prior resource state")
        by_action[action_id] = action
    rollback: list[dict[str, Any]] = []
    recovery_required: list[str] = []
    known_ids = {item["action_id"] for item in payload["change_manifest"]}
    if set(by_action) != known_ids:
        _fail("RESTORE_RECEIPT_MISMATCH", "receipt does not cover the exact original action set")
    for action in payload["change_manifest"]:
        action_id = action["action_id"]
        applied = by_action[action_id]
        current_sha = current_resource_sha256.get(action_id)
        if not _is_sha256(current_sha) or current_sha != applied["created_resource_sha256"]:
            recovery_required.append(action_id)
            continue
        rollback.append({
            "action_id": action_id,
            "resource_name": action["resource_name"],
            "remove_only_if_sha256": applied["created_resource_sha256"],
            "restore_prior_sha256": applied["prior_resource_sha256"],
            "restore_backup_path": applied["prior_backup_path"],
        })
    status = "READY_UNVERIFIED" if not recovery_required else "RECOVERY_REQUIRED"
    body = {"schema": PLAN_SCHEMA.replace("host-change-plan", "host-restore-plan"),
            "status": status, "evidence_classification": "SOFTWARE_ONLY",
            "project_id": payload["project_id"], "host": payload["host"],
            "source_plan_sha256": plan.sha256, "source_receipt_sha256": receipt.receipt_sha256,
            "rollback_actions": rollback, "preserved_foreign_or_changed_resources": recovery_required,
            "native_identity_verification": "NOT_CONFIGURED"}
    body["sha256"] = _sha256(body)
    return body


def apply_restore_plan(restore_plan: Mapping[str, Any], *, authority: Any,
                       approval: HostChangeApproval) -> None:
    if not isinstance(restore_plan, Mapping) or not _is_sha256(restore_plan.get("sha256")):
        _fail("RESTORE_PLAN_INVALID", "restore plan must be a fingerprinted plan-only object")
    expected = {key: value for key, value in restore_plan.items() if key != "sha256"}
    if _sha256(expected) != restore_plan["sha256"]:
        _fail("RESTORE_PLAN_TAMPERED", "restore plan fingerprint does not match its payload")
    if (restore_plan.get("schema") != PLAN_SCHEMA.replace("host-change-plan", "host-restore-plan")
            or restore_plan.get("evidence_classification") != "SOFTWARE_ONLY"
            or not isinstance(restore_plan.get("rollback_actions"), list)):
        _fail("RESTORE_PLAN_INVALID", "restore plan fields do not match the exact plan-only contract")
    _verify_host_approval(authority, restore_plan.get("project_id"), approval,
                          expected_plan_sha256=restore_plan["sha256"])
    _fail("HOST_RESTORE_ADAPTER_NOT_IMPLEMENTED",
          "no production OS rollback adapter is installed; no host changes were made",
          source_plan_sha256=restore_plan.get("source_plan_sha256"))


def _verify_host_approval(authority: Any, project_id: Any,
                          approval: HostChangeApproval, *,
                          expected_plan_sha256: str | None = None) -> None:
    expected = expected_plan_sha256 or approval.plan_sha256
    if (not isinstance(approval, HostChangeApproval) or not _is_sha256(expected)
            or approval.plan_sha256 != expected):
        _fail("HOST_PLAN_APPROVAL_MISMATCH", "explicit approval must bind this exact plan fingerprint")
    if not _nonempty(approval.authorization_ref):
        _fail("HOST_CHANGE_AUTHORIZATION_REQUIRED", "an explicit host authorization reference is required")
    _authorize_project(authority, project_id, "host_control")
    verifier = getattr(authority, "authorization_verifier", None)
    try:
        verified = bool(callable(verifier) and verifier(project_id, approval.authorization_ref))
    except Exception:
        verified = False
    if not verified:
        _fail("HOST_CHANGE_AUTHORIZATION_REQUIRED", "host authorization reference was not verified")


__all__ = [
    "AppliedHostChangeReceipt", "CapabilityState", "DiskAdmission", "DiskBudgetPolicy",
    "HostChangeApproval", "HostChangePlan", "HostChangeVerification", "HostResourceProfile",
    "HostResourceProvider", "HostResourceSnapshot", "LaunchImage", "NetworkGrant", "PathGrant",
    "PathIdentity", "ProcessWitness", "QuotaState", "ResourcePolicyError", "SnapshotAssessment",
    "StorageObservation", "VerifiedLaunchBinding", "apply_host_change_plan", "apply_restore_plan",
    "assess_snapshot", "build_host_change_plan", "build_restore_plan", "disk_scope_id",
    "evaluate_disk_admission", "release_disk_for_job", "require_profile_before_dispatch",
    "reserve_disk_for_job", "validate_host_change_plan", "validate_profile", "verify_host_change_plan",
]


def disk_scope_id(profile: HostResourceProfile, observation: StorageObservation) -> str:
    if observation.quota_state == QuotaState.VERIFIED_HARD_QUOTA.value:
        if not _nonempty(observation.quota_id):
            _fail("RESOURCE_QUOTA_STATE_UNKNOWN", "verified quota has no identity")
        return f"quota:{observation.filesystem_id}:{observation.quota_id}"
    return f"filesystem:{observation.filesystem_id}"


def evaluate_disk_admission(profile: HostResourceProfile, observation: StorageObservation,
                            requested_bytes: int, *, active_reserved_bytes: int,
                            now_monotonic_ns: int, max_age_ns: int = 30_000_000_000) -> DiskAdmission:
    if type(requested_bytes) is not int or not 1 <= requested_bytes <= _MAX_SQLITE_INT:
        _fail("INVALID_DISK_RESERVATION", "requested disk bytes must be a positive bounded integer")
    if requested_bytes > profile.disk_budget.max_task_reservation_bytes:
        _fail("DISK_RESERVATION_EXCEEDS_PROFILE", "requested bytes exceed the trusted task reservation ceiling",
              maximum_bytes=profile.disk_budget.max_task_reservation_bytes)
    if type(active_reserved_bytes) is not int or not 0 <= active_reserved_bytes <= _MAX_SQLITE_INT:
        _fail("RESOURCE_RESERVATION_STATE_UNKNOWN", "active reservation total is malformed")
    issues: list[str] = []
    _assess_storage(profile, observation, now_monotonic_ns, max_age_ns, issues)
    if issues:
        _fail("DISK_OBSERVATION_UNVERIFIED", "fresh storage observation does not bind to the trusted profile",
              reasons=issues)
    filesystem_available = observation.free_bytes - profile.disk_budget.reserve_free_bytes
    if filesystem_available < 0:
        filesystem_available = 0
    hard_quota = observation.quota_state == QuotaState.VERIFIED_HARD_QUOTA.value
    if hard_quota:
        if observation._native_attestation is not _NATIVE_STORAGE_ATTESTATION:
            _fail("RESOURCE_HARD_QUOTA_PROVIDER_NOT_CONFIGURED",
                  "claimed quota fields are not backed by a production native storage verifier",
                  evidence_classification="SOFTWARE_ONLY")
        quota_available = observation.quota_limit_bytes - observation.quota_used_bytes
        available = min(filesystem_available, quota_available)
        enforcement = "VERIFIED_HARD_QUOTA"
    else:
        if profile.disk_budget.mode == "REQUIRE_HARD_QUOTA":
            _fail("RESOURCE_HARD_QUOTA_REQUIRED", "the configured hard quota is not verified",
                  capability_state=observation.quota_state)
        available = filesystem_available
        enforcement = "ADMISSION_ONLY"
    if active_reserved_bytes > available:
        available = 0
    else:
        available -= active_reserved_bytes
    if requested_bytes > available:
        _fail("INSUFFICIENT_DISK_BUDGET", "fresh storage capacity is below the requested reservation",
              requested_bytes=requested_bytes, available_bytes=available,
              enforcement=enforcement)
    return DiskAdmission(True, requested_bytes, available, enforcement,
                         disk_scope_id(profile, observation), _sha256(observation.as_dict()))


def _event_json(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("reservation event JSON is malformed") from exc
    if not isinstance(value, dict):
        raise ValueError("reservation event metadata must be an object")
    return value


def _load_reservations(db: sqlite3.Connection) -> dict[str, tuple[str, dict[str, Any]]]:
    rows = db.execute(
        "SELECT id,job_id,metadata FROM job_events WHERE event='W26DiskReservation' ORDER BY id"
    ).fetchall()
    state: dict[str, tuple[str, dict[str, Any]]] = {}
    allowed = {"schema", "reservation_id", "status", "project_id", "job_id", "operation_id",
               "profile_id", "profile_sha256", "root_file_id", "filesystem_id", "scope_id",
               "quota_id", "requested_bytes", "observation_sha256", "enforcement", "at_monotonic_ns"}
    for row in rows:
        try:
            item = _event_json(row["metadata"])
            if (set(item) != allowed or item.get("schema") != RESERVATION_SCHEMA
                    or item.get("job_id") != row["job_id"]
                    or item.get("status") not in {"RESERVED", "RELEASED"}
                    or not _is_sha256(item.get("profile_sha256"))
                    or not _is_sha256(item.get("observation_sha256"))
                    or type(item.get("requested_bytes")) is not int
                    or not 1 <= item["requested_bytes"] <= _MAX_SQLITE_INT
                    or not _nonempty(item.get("reservation_id"))
                    or not _nonempty(item.get("scope_id"))
                    or not _nonempty(item.get("filesystem_id"))
                    or not _nonempty(item.get("root_file_id"))):
                raise ValueError("reservation event fields are invalid")
            reservation_id = item["reservation_id"]
            previous = state.get(reservation_id)
            if previous is None:
                if item["status"] != "RESERVED":
                    raise ValueError("release event has no reserve event")
                state[reservation_id] = (row["job_id"], item)
                continue
            previous_job, previous_item = previous
            if previous_job != row["job_id"] or item["status"] != "RELEASED":
                raise ValueError("reservation identity is duplicated or transition is invalid")
            base_prev = {key: value for key, value in previous_item.items() if key != "status"}
            base_next = {key: value for key, value in item.items() if key != "status"}
            if base_prev != base_next or previous_item["status"] != "RESERVED":
                raise ValueError("reservation release does not match its original reservation")
            state[reservation_id] = (row["job_id"], item)
        except (ValueError, TypeError) as exc:
            _fail("RESOURCE_RESERVATION_STATE_UNKNOWN", "durable disk reservation ledger is malformed",
                  event_id=row["id"], cause=str(exc))
    return state


def _reservation_id(job_id: str, operation_id: str, profile: HostResourceProfile) -> str:
    return _sha256({"job_id": job_id, "operation_id": operation_id, "project_id": profile.project_id,
                    "profile_id": profile.profile_id, "profile_sha256": profile.sha256})


def _read_job_binding(db: sqlite3.Connection, job_id: str, profile: HostResourceProfile,
                      operation_names: Sequence[str] | None = None) -> dict[str, Any]:
    row = db.execute(
        "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
        "o.operation,o.status AS operation_status,o.metadata AS operation_metadata "
        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?", (job_id,),
    ).fetchone()
    if row is None:
        _fail("RESOURCE_RESERVATION_JOB_UNKNOWN", "job is not in the durable OperationStore")
    try:
        job_metadata = _event_json(row["job_metadata"])
        operation_metadata = _event_json(row["operation_metadata"])
    except ValueError:
        _fail("RESOURCE_RESERVATION_JOB_UNKNOWN", "job metadata is malformed")
    operation = row["operation"]
    if (job_metadata.get("project_id") != profile.project_id
            or operation_metadata.get("project_id") != profile.project_id
            or job_metadata.get("project_root_identity") != profile.root.file_id
            or operation_metadata.get("project_root_identity") != profile.root.file_id):
        _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH", "job is not bound to the authorized project root")
    allowed = set(operation_names or profile.allowed_operations)
    if operation not in allowed or operation not in profile.allowed_operations:
        _fail("RESOURCE_RESERVATION_OPERATION_UNSUPPORTED", "job operation is outside the trusted budget profile")
    return {"operation_id": row["operation_id"], "operation": operation,
            "job_status": row["job_status"], "operation_status": row["operation_status"],
            "job_metadata": job_metadata}


def reserve_disk_for_job(*, store: Any, authority: Any, profile: HostResourceProfile,
                         job_id: str, requested_bytes: int, observation: StorageObservation,
                         now_monotonic_ns: int, max_age_ns: int = 30_000_000_000) -> dict[str, Any]:
    """Atomically bind and reserve against the existing durable job-event ledger."""
    from ._operation_store import OperationStore
    from ._project_authority import ProjectAuthority
    if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) or authority.store is not store:
        _fail("RESOURCE_RESERVATION_AUTHORITY_REQUIRED", "reservation requires the existing bound ProjectAuthority and OperationStore")
    if not isinstance(profile, HostResourceProfile) or not _nonempty(job_id):
        _fail("INVALID_DISK_RESERVATION", "trusted profile and job identity are required")
    if type(requested_bytes) is not int or not 1 <= requested_bytes <= _MAX_SQLITE_INT:
        _fail("INVALID_DISK_RESERVATION", "requested disk bytes must be a positive bounded integer")
    with store.lock:
        try:
            store.db.execute("BEGIN IMMEDIATE")
            _authorize_profile_project(authority, profile, profile.project_id, "compute",
                                       code_prefix="RESOURCE_RESERVATION")
            job = _read_job_binding(store.db, job_id, profile)
            if job["job_status"] != "QUEUED" or job["operation_status"] != "QUEUED":
                _fail("RESOURCE_RESERVATION_NOT_PREDISPATCH", "reservation must precede durable job dispatch",
                      job_status=job["job_status"], operation_status=job["operation_status"])
            reservation_id = _reservation_id(job_id, job["operation_id"], profile)
            states = _load_reservations(store.db)
            existing = states.get(reservation_id)
            if existing is not None:
                existing_job, item = existing
                if (existing_job != job_id or item["status"] != "RESERVED"
                        or item["requested_bytes"] != requested_bytes
                        or item["profile_sha256"] != profile.sha256
                        or item["project_id"] != profile.project_id):
                    _fail("RESOURCE_RESERVATION_IDENTITY_CONFLICT", "job already has a different or released reservation")
                store.db.execute("COMMIT")
                return {"status": "RESERVED", "reservation_id": reservation_id,
                        "requested_bytes": requested_bytes, "scope_id": item["scope_id"],
                        "enforcement": item["enforcement"], "idempotent": True,
                        "evidence_classification": "SOFTWARE_ONLY"}
            scope = disk_scope_id(profile, observation)
            active_reserved = 0
            for _, item in states.values():
                if item["status"] == "RESERVED" and item["scope_id"] == scope:
                    active_reserved += item["requested_bytes"]
                    if active_reserved > _MAX_SQLITE_INT:
                        _fail("RESOURCE_RESERVATION_STATE_UNKNOWN", "durable reservation total overflows storage integer range")
            admission = evaluate_disk_admission(profile, observation, requested_bytes,
                                                active_reserved_bytes=active_reserved,
                                                now_monotonic_ns=now_monotonic_ns,
                                                max_age_ns=max_age_ns)
            item = {"schema": RESERVATION_SCHEMA, "reservation_id": reservation_id,
                    "status": "RESERVED", "project_id": profile.project_id, "job_id": job_id,
                    "operation_id": job["operation_id"], "profile_id": profile.profile_id,
                    "profile_sha256": profile.sha256, "root_file_id": profile.root.file_id,
                    "filesystem_id": observation.filesystem_id, "scope_id": admission.scope_id,
                    "quota_id": observation.quota_id, "requested_bytes": requested_bytes,
                    "observation_sha256": admission.observation_sha256,
                    "enforcement": admission.enforcement, "at_monotonic_ns": now_monotonic_ns}
            store.db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                             (job_id, "W26DiskReservation", _canonical_json(item)))
            store.db.execute("COMMIT")
            return {"status": "RESERVED", "reservation_id": reservation_id,
                    "requested_bytes": requested_bytes, "scope_id": admission.scope_id,
                    "enforcement": admission.enforcement, "idempotent": False,
                    "evidence_classification": "SOFTWARE_ONLY"}
        except BaseException:
            if store.db.in_transaction:
                store.db.execute("ROLLBACK")
            raise


def release_disk_for_job(*, store: Any, authority: Any, profile: HostResourceProfile,
                         job_id: str) -> dict[str, Any]:
    """Release only a known terminal reservation; UNKNOWN/LOST stay held."""
    from ._operation_store import OperationStore
    from ._project_authority import ProjectAuthority
    if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) or authority.store is not store:
        _fail("RESOURCE_RESERVATION_AUTHORITY_REQUIRED", "release requires the existing bound ProjectAuthority and OperationStore")
    with store.lock:
        try:
            store.db.execute("BEGIN IMMEDIATE")
            _authorize_profile_project(authority, profile, profile.project_id, "compute",
                                       code_prefix="RESOURCE_RESERVATION")
            job = _read_job_binding(store.db, job_id, profile)
            if job["job_status"] != job["operation_status"]:
                _fail("RESOURCE_RESERVATION_UNCONFIRMED",
                      "job and operation terminal states do not agree",
                      job_status=job["job_status"], operation_status=job["operation_status"])
            if job["job_status"] in {"UNKNOWN", "RECONCILING", "RUNNING", "QUEUED", "STARTING", "EXPIRED", "LOST"}:
                _fail("RESOURCE_RESERVATION_UNCONFIRMED", "reservation remains held until job state is durably terminal",
                      job_status=job["job_status"])
            if job["job_status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                _fail("RESOURCE_RESERVATION_UNCONFIRMED", "job state does not prove reservation release is safe",
                      job_status=job["job_status"])
            reservation_id = _reservation_id(job_id, job["operation_id"], profile)
            states = _load_reservations(store.db)
            current = states.get(reservation_id)
            if current is None:
                _fail("RESOURCE_RESERVATION_NOT_FOUND", "job has no durable disk reservation")
            found_job, item = current
            if found_job != job_id:
                _fail("RESOURCE_RESERVATION_IDENTITY_CONFLICT", "reservation belongs to another job")
            if item["status"] == "RELEASED":
                store.db.execute("COMMIT")
                return {"status": "RELEASED", "reservation_id": reservation_id, "idempotent": True,
                        "evidence_classification": "SOFTWARE_ONLY"}
            released = {**item, "status": "RELEASED"}
            store.db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                             (job_id, "W26DiskReservation", _canonical_json(released)))
            store.db.execute("COMMIT")
            return {"status": "RELEASED", "reservation_id": reservation_id, "idempotent": False,
                    "evidence_classification": "SOFTWARE_ONLY"}
        except BaseException:
            if store.db.in_transaction:
                store.db.execute("ROLLBACK")
            raise
