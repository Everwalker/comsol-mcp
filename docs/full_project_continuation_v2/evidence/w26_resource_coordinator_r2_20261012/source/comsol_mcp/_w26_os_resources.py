"""Fail-closed host isolation and disk-resource primitives for W26/T037.

The POSIX disk provider observes free capacity through a pinned directory fd
and supports advisory admission only. Windows has no connected root/storage
adapter. The disk coordinator is installed only when the daemon is explicitly
started with a trusted disk-resource config; absent that config, legacy
dispatch remains unchanged. The module also defines the stricter full-host
isolation contract and reviewable plan-only host changes. No hard-quota,
OS-sandbox, or host-mutation adapter is installed, and software fixtures never
establish those forms of acceptance.
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
import socket
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


PROFILE_SCHEMA = "comsol-mcp.host-resource-profile/v1"
SNAPSHOT_SCHEMA = "comsol-mcp.host-resource-snapshot/v1"
PLAN_SCHEMA = "comsol-mcp.host-change-plan/v1"
RESERVATION_SCHEMA = "comsol-mcp.disk-reservation/v1"
QUIESCENCE_SCHEMA = "comsol-mcp.job-quiescence-witness/v1"
_MAX_SQLITE_INT = (1 << 63) - 1
_QUIESCENCE_MAX_AGE_NS = 30_000_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_NATIVE_STORAGE_ATTESTATION = object()
_POSIX_STORAGE_OBSERVATION_ATTESTATION = object()
_TRUSTED_DISK_PROFILE_ATTESTATION = object()
_TRUSTED_DISK_CONFIG_ATTESTATION = object()
_NATIVE_QUIESCENCE_ATTESTATION = object()
DISK_PROFILE_SCHEMA = "comsol-mcp.disk-resource-profile/v1"
DISK_CONFIG_SCHEMA = "comsol-mcp.disk-resource-config/v1"
DISK_ROOT_BINDING_EVENT = "W26DiskRootBinding"
_DISK_CONFIG_MAX_BYTES = 262_144
_DISK_PRODUCING_EFFECTS = frozenset({"COMPUTE", "FILE_WRITE", "WRITE", "STATE_WRITE", "TRUSTED_CODE", "EVALUATE"})
_EFFECT_PERMISSION = {
    "INSPECT": "inspect", "PROJECT_WRITE": "project_write", "READ": "inspect",
    "WRITE": "project_write", "STATE_WRITE": "project_write", "FILE_WRITE": "project_write",
    "COMPUTE": "compute", "EVALUATE": "project_write", "TRUSTED_CODE": "trusted_code",
    "HOST_CONTROL": "host_control",
}
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


@dataclass(frozen=True)
class DiskOperationEstimate:
    """Trusted daemon-start estimate for one canonical disk-producing action."""

    operation_id: str
    estimate_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, "estimate_bytes": self.estimate_bytes}


@dataclass(frozen=True)
class DiskResourceProfile:
    """Admission-only disk policy independent of a separate OS service account.

    Instances constructed by the trusted startup config loader carry a private
    provenance token. The profile is a capacity-admission policy; it does not
    prove quotas, process isolation, or an OS sandbox.
    """

    profile_id: str
    project_id: str
    platform: str
    host_id: str
    root: PathIdentity
    storage_id: str
    disk_budget: DiskBudgetPolicy
    allowed_operations: tuple[str, ...]
    estimates: tuple[DiskOperationEstimate, ...]
    _trusted_attestation: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_disk_profile(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": DISK_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "platform": self.platform,
            "host_id": self.host_id,
            "root": self.root.as_dict(),
            "storage_id": self.storage_id,
            "disk_budget": self.disk_budget.as_dict(),
            "allowed_operations": list(self.allowed_operations),
            "trusted_estimates": [estimate.as_dict() for estimate in self.estimates],
        }

    @property
    def sha256(self) -> str:
        return _sha256(self.as_dict())

    @property
    def trusted_startup_config(self) -> bool:
        return self._trusted_attestation is _TRUSTED_DISK_PROFILE_ATTESTATION

    def estimate_for(self, operation_id: str) -> int:
        for estimate in self.estimates:
            if estimate.operation_id == operation_id:
                return estimate.estimate_bytes
        _fail("DISK_OPERATION_ESTIMATE_MISSING", "trusted startup config has no estimate for this operation",
              operation_id=operation_id)


def _disk_operation_effect(operation_id: str) -> str:
    if not isinstance(operation_id, str) or operation_id in {"registry_call", "operation_call"}:
        raise ValueError("disk profiles require canonical inner operation identifiers")
    effect = _registered_operation_effect(operation_id)
    if effect is None:
        raise ValueError(f"unknown canonical operation in disk profile: {operation_id}")
    effect = str(effect).upper()
    if effect not in _DISK_PRODUCING_EFFECTS:
        raise ValueError(f"operation {operation_id} is not a supported disk-producing effect")
    return effect


def _registered_operation_effect(operation_id: str) -> str | None:
    """Resolve the same canonical effect sources used by production dispatch."""
    from . import _g2_registry

    entry = _g2_registry.BY_ID.get(operation_id)
    if entry is not None:
        return str(entry.effect).upper()
    try:
        from . import _g3_ops
        effect = _g3_ops.EFFECTS.get(operation_id)
    except Exception:
        effect = None
    if effect is not None:
        return str(effect).upper()
    effect = _g2_registry.LEGACY_TOOL_EFFECTS.get(operation_id)
    return str(effect).upper() if effect is not None else None


def validate_disk_profile(profile: DiskResourceProfile) -> None:
    if not isinstance(profile, DiskResourceProfile):
        raise TypeError("profile must be a DiskResourceProfile")
    for value, label in ((profile.profile_id, "profile_id"), (profile.project_id, "project_id"),
                         (profile.host_id, "host_id"), (profile.storage_id, "storage_id")):
        _checked_identifier(value, label)
    if profile.platform not in {"macos", "linux", "windows"}:
        raise ValueError("disk profile platform must be macos, linux, or windows")
    if not isinstance(profile.root, PathIdentity):
        raise ValueError("disk profile root identity is required")
    _path_key(profile.root.path, profile.platform)
    for value, label in ((profile.root.volume_id, "root.volume_id"),
                         (profile.root.file_id, "root.file_id"),
                         (profile.root.owner_id, "root.owner_id")):
        _checked_identifier(value, label)
    if not isinstance(profile.disk_budget, DiskBudgetPolicy):
        raise ValueError("disk budget policy is required")
    if (profile.disk_budget.mode != QuotaState.ADMISSION_ONLY.value
            or profile.disk_budget.quota_profile_id is not None):
        raise ValueError("DiskResourceProfile supports admission-only capacity, never claimed hard quota")
    _checked_int(profile.disk_budget.reserve_free_bytes, "reserve_free_bytes")
    _checked_int(profile.disk_budget.max_task_reservation_bytes,
                 "max_task_reservation_bytes", minimum=1)
    if not isinstance(profile.allowed_operations, tuple) or not profile.allowed_operations:
        raise ValueError("disk profile allowed_operations must be a nonempty tuple")
    if len(set(profile.allowed_operations)) != len(profile.allowed_operations):
        raise ValueError("disk profile allowed_operations contains duplicates")
    for operation_id in profile.allowed_operations:
        _checked_identifier(operation_id, "allowed operation")
        _disk_operation_effect(operation_id)
    if not isinstance(profile.estimates, tuple) or any(not isinstance(item, DiskOperationEstimate)
                                                       for item in profile.estimates):
        raise ValueError("disk profile estimates must be a tuple of typed estimates")
    ids = [item.operation_id for item in profile.estimates]
    if len(ids) != len(set(ids)) or set(ids) != set(profile.allowed_operations):
        raise ValueError("every allowed canonical operation needs exactly one trusted estimate")
    for estimate in profile.estimates:
        _checked_int(estimate.estimate_bytes, "estimate_bytes", minimum=1,
                     maximum=profile.disk_budget.max_task_reservation_bytes)


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
    host_id: str | None = None
    storage_id: str | None = None
    evidence_classification: str = "SOFTWARE_ONLY"
    # A real POSIX free-space observation is distinct from a quota attestation.
    # It supports advisory admission only and has no enforcement authority.
    _observation_attestation: object | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {"filesystem_id": self.filesystem_id, "root_path": self.root_path,
                "volume_id": self.volume_id, "root_file_id": self.root_file_id,
                "free_bytes": self.free_bytes, "total_bytes": self.total_bytes,
                "quota_state": self.quota_state, "quota_id": self.quota_id,
                "quota_scope_id": self.quota_scope_id, "quota_limit_bytes": self.quota_limit_bytes,
                "quota_used_bytes": self.quota_used_bytes,
                "observed_monotonic_ns": self.observed_monotonic_ns,
                "host_id": self.host_id, "storage_id": self.storage_id,
                "evidence_classification": self.evidence_classification}


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


def _assess_storage(profile: HostResourceProfile | DiskResourceProfile, storage: Any, now_ns: int,
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
    if isinstance(profile, DiskResourceProfile):
        if (storage.host_id != profile.host_id or storage.storage_id != profile.storage_id
                or storage.filesystem_id != profile.storage_id):
            reasons.append("STORAGE_HOST_OR_IDENTITY_CHANGED")
        if (storage.quota_state != QuotaState.ADMISSION_ONLY.value
                or storage._observation_attestation is not _POSIX_STORAGE_OBSERVATION_ATTESTATION
                or storage.evidence_classification != "HOST_OBSERVED_ADMISSION_ONLY"):
            reasons.append("POSIX_STORAGE_OBSERVATION_UNVERIFIED")
        if any(value is not None for value in (storage.quota_id, storage.quota_scope_id,
                                                storage.quota_limit_bytes, storage.quota_used_bytes)):
            reasons.append("ADMISSION_ONLY_QUOTA_FIELDS_FORBIDDEN")
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


def _current_platform_name() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unsupported"


def _current_host_id() -> str:
    try:
        value = socket.gethostname()
    except OSError as exc:
        _fail("DISK_HOST_ID_UNAVAILABLE", "current host identity could not be read",
              cause_type=type(exc).__name__)
    if not _nonempty(value) or any(ord(char) < 33 for char in value):
        _fail("DISK_HOST_ID_UNAVAILABLE", "current host identity is malformed")
    return value


def _posix_storage_id(descriptor: int) -> tuple[str, str, str, int, int, int]:
    """Return a stable local filesystem/root binding plus observed capacity."""
    try:
        root = os.fstat(descriptor)
        vfs = os.fstatvfs(descriptor)
    except OSError as exc:
        _fail("DISK_STORAGE_OBSERVATION_UNAVAILABLE", "pinned filesystem capacity could not be read",
              cause_type=type(exc).__name__)
    if not stat.S_ISDIR(root.st_mode):
        _fail("DISK_ROOT_IDENTITY_MISMATCH", "pinned storage root is not a directory")
    device = int(root.st_dev)
    raw_fsid = getattr(vfs, "f_fsid", None)
    storage_id = f"posix:{device}:{int(raw_fsid)}" if type(raw_fsid) is int else f"posix:{device}"
    fragment_size = int(getattr(vfs, "f_frsize", 0) or getattr(vfs, "f_bsize", 0))
    available_blocks = int(vfs.f_bavail)
    total_blocks = int(vfs.f_blocks)
    if fragment_size <= 0 or available_blocks < 0 or total_blocks <= 0:
        _fail("DISK_STORAGE_OBSERVATION_INVALID", "filesystem capacity fields are malformed")
    free_bytes = available_blocks * fragment_size
    total_bytes = total_blocks * fragment_size
    if (free_bytes > _MAX_SQLITE_INT or total_bytes > _MAX_SQLITE_INT
            or free_bytes > total_bytes):
        _fail("DISK_STORAGE_OBSERVATION_INVALID", "filesystem capacity exceeds supported integer bounds")
    return storage_id, str(device), str(int(root.st_ino)), int(root.st_uid), free_bytes, total_bytes


class PosixStorageObservationProvider:
    """Read advisory POSIX capacity through an opened, identity-pinned root.

    This provider does not attest hard quotas or OS sandboxing. Windows is
    deliberately unsupported until a native root/volume/quota adapter exists.
    """

    provider_id = "posix-pinned-fstatvfs/v1"

    def __init__(self) -> None:
        self._platform = _current_platform_name()
        self._host_id = _current_host_id()

    @staticmethod
    def current_storage_id(root_path: str | os.PathLike[str], expected_root: PathIdentity) -> str:
        if os.name == "nt":
            _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED",
                  "Windows disk admission requires a native root and storage identity adapter",
                  capability_state=CapabilityState.UNSUPPORTED.value)
        descriptors: list[int] = []
        try:
            from ._state_backup import _open_posix_directory_chain, _verify_posix_directory_links
            descriptors, links, _ = _open_posix_directory_chain(
                Path(root_path), expected_identity={"device": int(expected_root.volume_id),
                                                    "inode": int(expected_root.file_id)})
            _verify_posix_directory_links(links)
            storage_id, volume, file_id, owner, _free, _total = _posix_storage_id(descriptors[-1])
            if (volume != expected_root.volume_id or file_id != expected_root.file_id
                    or str(owner) != expected_root.owner_id):
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "live root differs from configured disk profile")
            return storage_id
        except ResourcePolicyError:
            raise
        except Exception as exc:
            _fail("DISK_ROOT_IDENTITY_UNKNOWN", "pinned POSIX root could not be verified",
                  cause_type=type(exc).__name__)
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def read_observation(self, profile: DiskResourceProfile) -> StorageObservation:
        if os.name == "nt" or self._platform == "windows":
            _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED",
                  "Windows disk admission requires a native root and storage identity adapter",
                  capability_state=CapabilityState.UNSUPPORTED.value)
        if not isinstance(profile, DiskResourceProfile):
            _fail("DISK_PROFILE_REQUIRED", "POSIX capacity observation requires a typed disk profile")
        validate_disk_profile(profile)
        if self._platform not in {"macos", "linux"}:
            _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED", "this POSIX platform has no reviewed storage adapter",
                  capability_state=CapabilityState.UNSUPPORTED.value)
        if profile.platform != self._platform or profile.host_id != self._host_id:
            _fail("DISK_STORAGE_HOST_MISMATCH", "disk profile is bound to another platform or host")
        descriptors: list[int] = []
        try:
            from ._state_backup import _open_posix_directory_chain, _verify_posix_directory_links
            descriptors, links, _root_identity = _open_posix_directory_chain(
                Path(profile.root.path), expected_identity={"device": int(profile.root.volume_id),
                                                           "inode": int(profile.root.file_id)})
            fd = descriptors[-1]
            storage_id, volume, file_id, owner, free_bytes, total_bytes = _posix_storage_id(fd)
            _verify_posix_directory_links(links)
            if (volume != profile.root.volume_id or file_id != profile.root.file_id
                    or str(owner) != profile.root.owner_id):
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "pinned project root no longer matches disk profile")
            if storage_id != profile.storage_id:
                _fail("DISK_STORAGE_IDENTITY_MISMATCH", "pinned root is on a different configured filesystem",
                      expected_storage_id=profile.storage_id, observed_storage_id=storage_id)
            return StorageObservation(
                filesystem_id=storage_id,
                root_path=profile.root.path,
                volume_id=volume,
                root_file_id=file_id,
                free_bytes=free_bytes,
                total_bytes=total_bytes,
                quota_state=QuotaState.ADMISSION_ONLY.value,
                quota_id=None,
                quota_scope_id=None,
                quota_limit_bytes=None,
                quota_used_bytes=None,
                observed_monotonic_ns=time.monotonic_ns(),
                host_id=self._host_id,
                storage_id=storage_id,
                evidence_classification="HOST_OBSERVED_ADMISSION_ONLY",
                _observation_attestation=_POSIX_STORAGE_OBSERVATION_ATTESTATION,
            )
        except ResourcePolicyError:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) == "RESTORE_TARGET_CHANGED":
                _fail("DISK_ROOT_IDENTITY_MISMATCH",
                      "pinned project root was replaced after the trusted disk profile was loaded")
            _fail("DISK_STORAGE_OBSERVATION_UNAVAILABLE", "pinned POSIX capacity observation failed",
                  cause_type=type(exc).__name__)
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _strict_disk_config(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionContractError("DISK_RESOURCE_CONFIG_INVALID",
                                     "disk resource startup config must be strict UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise ExecutionContractError("DISK_RESOURCE_CONFIG_INVALID",
                                     "disk resource startup config root must be an object")
    return result


@dataclass(frozen=True)
class TrustedDiskResourceConfig:
    """Pinned startup-only disk profile resolver; never constructed from RPC data."""

    host_id: str
    platform: str
    profiles: tuple[DiskResourceProfile, ...]
    source_sha256: str
    _attestation: object | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "TrustedDiskResourceConfig":
        if os.name == "nt":
            _fail("DISK_RESOURCE_CONFIG_PROVIDER_UNSUPPORTED",
                  "Windows disk profile loading is unavailable without its native root identity adapter",
                  capability_state=CapabilityState.UNSUPPORTED.value)
        candidate = Path(path)
        if not candidate.is_absolute() or ".." in candidate.parts or candidate.name in {"", ".", ".."}:
            _fail("DISK_RESOURCE_CONFIG_INVALID", "startup config path must be absolute and canonical")
        candidate = Path(os.path.abspath(candidate))
        parent_fds: list[int] = []
        fd: int | None = None
        try:
            from ._state_backup import _open_posix_directory_chain, _verify_posix_directory_links
            parent_fds, links, _parent_identity = _open_posix_directory_chain(candidate.parent)
            _verify_posix_directory_links(links)
            parent_info = os.fstat(parent_fds[-1])
            if not stat.S_ISDIR(parent_info.st_mode) or stat.S_IMODE(parent_info.st_mode) & 0o022:
                _fail("DISK_RESOURCE_CONFIG_INVALID",
                      "startup config directory must not be group- or other-writable")
            from ._host_scoped_provider import verify_private_path_security
            def verify_config_path(path_value: Path, *, directory: bool, expected_identity: Mapping[str, int]):
                try:
                    return verify_private_path_security(
                        path_value, directory=directory, expected_identity=expected_identity,
                    )
                except ExecutionContractError as exc:
                    code = ("DISK_RESOURCE_CONFIG_CHANGED"
                            if exc.code == "AUTHORIZATION_STATE_UNKNOWN"
                            else "DISK_RESOURCE_CONFIG_INVALID")
                    _fail(code, "startup config ownership and filesystem security could not be verified",
                          cause_code=exc.code)

            parent_security = verify_config_path(
                candidate.parent, directory=True,
                expected_identity={"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)},
            )
            fd = os.open(candidate.name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fds[-1])
            before = os.fstat(fd)
            file_security = verify_config_path(
                candidate, directory=False,
                expected_identity={"device": int(before.st_dev), "inode": int(before.st_ino)},
            )
            named = os.stat(candidate.name, dir_fd=parent_fds[-1], follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named.st_mode)
                    or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
                    or (hasattr(os, "geteuid") and before.st_uid not in {os.geteuid(), 0})
                    or stat.S_IMODE(before.st_mode) & 0o022
                    or before.st_size <= 0 or before.st_size > _DISK_CONFIG_MAX_BYTES):
                _fail("DISK_RESOURCE_CONFIG_INVALID",
                      "startup config must be a bounded, non-writable-by-others regular file")
            raw = bytearray()
            while len(raw) <= _DISK_CONFIG_MAX_BYTES:
                block = os.read(fd, min(64 * 1024, _DISK_CONFIG_MAX_BYTES + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            named_after = os.stat(candidate.name, dir_fd=parent_fds[-1], follow_symlinks=False)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
                    or len(raw) != before.st_size or len(raw) > _DISK_CONFIG_MAX_BYTES):
                _fail("DISK_RESOURCE_CONFIG_CHANGED", "startup config changed during its pinned read")
            _verify_posix_directory_links(links)
            verify_config_path(
                candidate.parent, directory=True,
                expected_identity={"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)},
            )
            verified_file_security = verify_config_path(
                candidate, directory=False,
                expected_identity={"device": int(after.st_dev), "inode": int(after.st_ino)},
            )
            required_security_semantics = (
                "posix-owner-mode+filesystem-ownership+no-extended-acl"
                if sys.platform == "darwin" else "posix-owner-mode"
            )
            if (parent_security.get("security_semantics") != required_security_semantics
                    or file_security.get("security_semantics") != required_security_semantics
                    or file_security.get("security_semantics") != verified_file_security.get("security_semantics")):
                _fail("DISK_RESOURCE_CONFIG_INVALID",
                      "startup config security semantics changed or are unsupported")
            config = _strict_disk_config(bytes(raw))
            _reject_secrets(config, "disk resource startup config")
            expected_fields = {"schema", "host_id", "platform", "profiles"}
            if set(config) != expected_fields or config.get("schema") != DISK_CONFIG_SCHEMA:
                _fail("DISK_RESOURCE_CONFIG_INVALID", "startup config fields or schema are unsupported")
            host_id, platform_name = _current_host_id(), _current_platform_name()
            if config.get("host_id") != host_id or config.get("platform") != platform_name:
                _fail("DISK_RESOURCE_CONFIG_HOST_MISMATCH",
                      "startup disk config is bound to another host or platform")
            raw_profiles = config.get("profiles")
            if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= 256:
                _fail("DISK_RESOURCE_CONFIG_INVALID", "startup config needs a bounded profile inventory")
            profiles = tuple(_parse_disk_profile(item, host_id=host_id, platform_name=platform_name)
                             for item in raw_profiles)
            project_ids = [profile.project_id for profile in profiles]
            profile_ids = [profile.profile_id for profile in profiles]
            if len(set(project_ids)) != len(project_ids) or len(set(profile_ids)) != len(profile_ids):
                _fail("DISK_RESOURCE_CONFIG_INVALID", "startup disk profiles contain duplicate project or profile IDs")
            return cls(host_id, platform_name, profiles,
                       hashlib.sha256(raw).hexdigest(), _TRUSTED_DISK_CONFIG_ATTESTATION)
        except ResourcePolicyError:
            raise
        except ExecutionContractError:
            raise
        except Exception as exc:
            _fail("DISK_RESOURCE_CONFIG_INVALID", "trusted disk config could not be loaded safely",
                  cause_type=type(exc).__name__)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            for descriptor in reversed(parent_fds):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @property
    def trusted_startup_source(self) -> bool:
        return self._attestation is _TRUSTED_DISK_CONFIG_ATTESTATION

    def resolve(self, project_id: str) -> DiskResourceProfile:
        if not self.trusted_startup_source:
            _fail("DISK_RESOURCE_CONFIG_UNTRUSTED", "disk profile resolver did not come from trusted startup loading")
        if not _nonempty(project_id):
            _fail("PROJECT_IDENTITY_REQUIRED", "disk profile resolution requires an authorized project identity")
        for profile in self.profiles:
            if profile.project_id == project_id:
                if not profile.trusted_startup_config:
                    _fail("DISK_RESOURCE_PROFILE_UNTRUSTED", "resolved profile lacks trusted startup provenance")
                return profile
        _fail("DISK_RESOURCE_PROFILE_NOT_CONFIGURED", "no trusted disk profile is configured for this project",
              project_id=project_id)


def _parse_disk_profile(raw: Any, *, host_id: str, platform_name: str) -> DiskResourceProfile:
    fields = {"profile_id", "project_id", "root", "storage_id", "disk_budget",
              "allowed_operations", "trusted_estimates"}
    if not isinstance(raw, dict) or set(raw) != fields:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk profile fields do not match the strict startup schema")
    root_raw = raw["root"]
    if not isinstance(root_raw, dict) or set(root_raw) != {"path", "volume_id", "file_id", "owner_id"}:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk profile root identity fields are invalid")
    root = PathIdentity(**root_raw)
    budget_raw = raw["disk_budget"]
    if not isinstance(budget_raw, dict) or set(budget_raw) != {
            "mode", "reserve_free_bytes", "max_task_reservation_bytes", "quota_profile_id"}:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk profile budget fields are invalid")
    budget = DiskBudgetPolicy(**budget_raw)
    operations = raw["allowed_operations"]
    estimate_rows = raw["trusted_estimates"]
    if (not isinstance(operations, list) or any(not isinstance(item, str) for item in operations)
            or not isinstance(estimate_rows, list)):
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk operation and estimate lists are malformed")
    estimates: list[DiskOperationEstimate] = []
    for item in estimate_rows:
        if not isinstance(item, dict) or set(item) != {"operation_id", "estimate_bytes"}:
            _fail("DISK_RESOURCE_CONFIG_INVALID", "trusted estimate fields are invalid")
        try:
            estimates.append(DiskOperationEstimate(**item))
        except TypeError as exc:
            _fail("DISK_RESOURCE_CONFIG_INVALID", "trusted estimate types are invalid",
                  cause_type=type(exc).__name__)
    try:
        profile = DiskResourceProfile(
            raw["profile_id"], raw["project_id"], platform_name, host_id, root,
            raw["storage_id"], budget, tuple(operations), tuple(estimates),
            _TRUSTED_DISK_PROFILE_ATTESTATION)
    except (TypeError, ValueError) as exc:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk profile failed strict validation",
              cause_type=type(exc).__name__, reason=str(exc))
    if os.name == "nt":
        _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED", "Windows disk storage identity adapter is unavailable",
              capability_state=CapabilityState.UNSUPPORTED.value)
    observed_storage = PosixStorageObservationProvider.current_storage_id(root.path, root)
    if observed_storage != profile.storage_id:
        _fail("DISK_STORAGE_IDENTITY_MISMATCH", "startup profile storage ID differs from the live pinned root",
              expected_storage_id=profile.storage_id, observed_storage_id=observed_storage)
    return profile


class JobQuiescenceProvider:
    """Provider contract for independently proving that one job owns no live work."""

    def prove_quiescence(self, *, store: Any, profile: HostResourceProfile,
                         job_binding: Mapping[str, Any], reservation_id: str) -> "JobQuiescenceWitness":
        raise NotImplementedError


class NativeJobQuiescenceProvider(JobQuiescenceProvider):
    """Startup-installed adapter backed by exact process and write-ownership evidence.

    There is no production implementation connected in this unit. An adapter
    must prove the exact owned process tree has terminated, owned writes have
    stopped, and the existing durable operation ledger is quiescent.
    """

    pass


@dataclass(frozen=True)
class JobQuiescenceWitness:
    """Opaque, job-bound proof returned by a trusted quiescence provider.

    The evidence hashes refer to the provider's exact process-tree, write-scope,
    and durable-ledger observations. This witness releases only the physical
    disk reservation; it never changes the domain operation's UNKNOWN status.
    """

    schema: str
    provider_id: str
    profile_id: str
    profile_sha256: str
    project_id: str
    root_file_id: str
    host_id: str
    job_id: str
    operation_id: str
    reservation_id: str
    domain_status: str
    safe_retry: bool
    process_tree_state: str
    process_tree_evidence_sha256: str
    owned_writes_state: str
    owned_writes_evidence_sha256: str
    operation_ledger_state: str
    operation_ledger_watermark: int
    operation_ledger_evidence_sha256: str
    observed_monotonic_ns: int
    _attestation: object = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "provider_id": self.provider_id,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "project_id": self.project_id,
            "root_file_id": self.root_file_id,
            "host_id": self.host_id,
            "job_id": self.job_id,
            "operation_id": self.operation_id,
            "reservation_id": self.reservation_id,
            "domain_status": self.domain_status,
            "safe_retry": self.safe_retry,
            "process_tree_state": self.process_tree_state,
            "process_tree_evidence_sha256": self.process_tree_evidence_sha256,
            "owned_writes_state": self.owned_writes_state,
            "owned_writes_evidence_sha256": self.owned_writes_evidence_sha256,
            "operation_ledger_state": self.operation_ledger_state,
            "operation_ledger_watermark": self.operation_ledger_watermark,
            "operation_ledger_evidence_sha256": self.operation_ledger_evidence_sha256,
            "observed_monotonic_ns": self.observed_monotonic_ns,
        }


def _issue_native_quiescence_witness(**fields: Any) -> JobQuiescenceWitness:
    """Internal adapter seam; caller-supplied request data must never reach it."""
    return JobQuiescenceWitness(**fields, _attestation=_NATIVE_QUIESCENCE_ATTESTATION)


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


def _authorize_profile_project(authority: Any, profile: HostResourceProfile | DiskResourceProfile,
                               project_id: str, permission: str, *,
                               code_prefix: str) -> dict[str, Any]:
    project = _authorize_project(authority, project_id, permission)
    if _path_key(project.get("workspace", ""), profile.platform) != _path_key(profile.root.path, profile.platform):
        _fail(f"{code_prefix}_ROOT_AUTHORITY_MISMATCH",
              "trusted profile root differs from ProjectAuthority workspace")
    if os.name == "nt":
        _fail(f"{code_prefix}_ROOT_IDENTITY_PROVIDER_NOT_CONFIGURED",
              "Windows project root identity verification is not connected")
    descriptors: list[int] = []
    try:
        from ._state_backup import _open_posix_directory_chain, _verify_posix_directory_links
        descriptors, links, _identity = _open_posix_directory_chain(
            Path(profile.root.path), expected_identity={"device": int(profile.root.volume_id),
                                                       "inode": int(profile.root.file_id)})
        info = os.fstat(descriptors[-1])
        _verify_posix_directory_links(links)
    except Exception as exc:
        if getattr(exc, "code", None) == "RESTORE_TARGET_CHANGED":
            _fail(f"{code_prefix}_ROOT_IDENTITY_MISMATCH",
                  "project root was replaced after its trusted profile was loaded")
        _fail(f"{code_prefix}_ROOT_IDENTITY_UNKNOWN", "pinned project root identity could not be verified",
              cause_type=type(exc).__name__)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    if (not stat.S_ISDIR(info.st_mode)
            or str(int(info.st_dev)) != profile.root.volume_id
            or str(int(info.st_ino)) != profile.root.file_id
            or str(int(info.st_uid)) != profile.root.owner_id):
        _fail(f"{code_prefix}_ROOT_IDENTITY_MISMATCH",
              "project root no longer has its configured volume, directory, and owner identity")
    return project


def _permission_for_disk_operation(operation_id: str) -> str:
    try:
        effect = _disk_operation_effect(operation_id)
        permission = _EFFECT_PERMISSION[effect]
    except (ValueError, KeyError) as exc:
        _fail("RESOURCE_OPERATION_EFFECT_UNAVAILABLE",
              "trusted disk admission cannot resolve the canonical operation effect",
              operation_id=operation_id, cause=str(exc))
    return permission


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
    "DiskOperationEstimate", "DiskResourceCoordinator", "DiskResourceProfile",
    "HostChangeApproval", "HostChangePlan", "HostChangeVerification", "HostResourceProfile",
    "HostResourceProvider", "HostResourceSnapshot", "JobQuiescenceProvider",
    "JobQuiescenceWitness", "LaunchImage", "NativeJobQuiescenceProvider", "NetworkGrant", "PathGrant",
    "PathIdentity", "PosixStorageObservationProvider", "ProcessWitness", "QuotaState",
    "ResourcePolicyError", "SnapshotAssessment",
    "StorageObservation", "VerifiedLaunchBinding", "apply_host_change_plan", "apply_restore_plan",
    "assess_snapshot", "build_host_change_plan", "build_restore_plan", "disk_scope_id",
    "evaluate_disk_admission", "release_disk_for_job", "require_profile_before_dispatch",
    "reserve_disk_for_job", "TrustedDiskResourceConfig", "validate_disk_profile",
    "validate_host_change_plan", "validate_profile", "verify_host_change_plan",
]


def disk_scope_id(profile: HostResourceProfile | DiskResourceProfile,
                  observation: StorageObservation) -> str:
    if observation.quota_state == QuotaState.VERIFIED_HARD_QUOTA.value:
        if not _nonempty(observation.quota_id):
            _fail("RESOURCE_QUOTA_STATE_UNKNOWN", "verified quota has no identity")
        return f"quota:{observation.filesystem_id}:{observation.quota_id}"
    return f"filesystem:{observation.filesystem_id}"


def evaluate_disk_admission(profile: HostResourceProfile | DiskResourceProfile,
                            observation: StorageObservation,
                            requested_bytes: int, *, active_reserved_bytes: int,
                            now_monotonic_ns: int, max_age_ns: int = 30_000_000_000) -> DiskAdmission:
    if not isinstance(profile, (HostResourceProfile, DiskResourceProfile)):
        _fail("DISK_PROFILE_REQUIRED", "disk admission requires a typed trusted resource profile")
    if isinstance(profile, DiskResourceProfile):
        validate_disk_profile(profile)
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
    evidence = ("HOST_OBSERVED_ADMISSION_ONLY"
                if observation._observation_attestation is _POSIX_STORAGE_OBSERVATION_ATTESTATION
                else "SOFTWARE_ONLY")
    return DiskAdmission(True, requested_bytes, available, enforcement,
                         disk_scope_id(profile, observation), _sha256(observation.as_dict()), evidence)


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
    base_fields = {"schema", "reservation_id", "status", "project_id", "job_id", "operation_id",
                   "profile_id", "profile_sha256", "root_file_id", "filesystem_id", "scope_id",
                   "quota_id", "requested_bytes", "observation_sha256", "enforcement", "at_monotonic_ns"}
    quiescence_fields = {"release_basis", "quiescence_witness", "quiescence_witness_sha256"}
    for row in rows:
        try:
            item = _event_json(row["metadata"])
            status = item.get("status")
            expected_fields = (base_fields if status in {"RESERVED", "RELEASED"}
                               else base_fields | quiescence_fields
                               if status == "RELEASED_AFTER_QUIESCENCE" else set())
            if (not expected_fields or set(item) != expected_fields
                    or item.get("schema") != RESERVATION_SCHEMA
                    or item.get("job_id") != row["job_id"]
                    or not _is_sha256(item.get("profile_sha256"))
                    or not _is_sha256(item.get("observation_sha256"))
                    or type(item.get("requested_bytes")) is not int
                    or not 1 <= item["requested_bytes"] <= _MAX_SQLITE_INT
                    or not _nonempty(item.get("reservation_id"))
                    or not _nonempty(item.get("scope_id"))
                    or not _nonempty(item.get("filesystem_id"))
                    or not _nonempty(item.get("root_file_id"))):
                raise ValueError("reservation event fields are invalid")
            if status == "RELEASED_AFTER_QUIESCENCE":
                witness = item.get("quiescence_witness")
                if (item.get("release_basis") != "INDEPENDENT_QUIESCENCE"
                        or not isinstance(witness, dict)
                        or not _is_sha256(item.get("quiescence_witness_sha256"))
                        or _sha256(witness) != item["quiescence_witness_sha256"]):
                    raise ValueError("quiescence release receipt is malformed or has a bad digest")
                _validate_quiescence_witness_dict(witness, item, event_id=row["id"], db=db)
            reservation_id = item["reservation_id"]
            previous = state.get(reservation_id)
            if previous is None:
                if status != "RESERVED":
                    raise ValueError("release event has no reserve event")
                state[reservation_id] = (row["job_id"], item)
                continue
            previous_job, previous_item = previous
            if previous_job != row["job_id"] or status not in {"RELEASED", "RELEASED_AFTER_QUIESCENCE"}:
                raise ValueError("reservation identity is duplicated or transition is invalid")
            base_prev = {key: value for key, value in previous_item.items()
                         if key in base_fields and key != "status"}
            base_next = {key: value for key, value in item.items()
                         if key in base_fields and key != "status"}
            if base_prev != base_next or previous_item["status"] != "RESERVED":
                raise ValueError("reservation release does not match its original reservation")
            state[reservation_id] = (row["job_id"], item)
        except (ValueError, TypeError) as exc:
            _fail("RESOURCE_RESERVATION_STATE_UNKNOWN", "durable disk reservation ledger is malformed",
                  event_id=row["id"], cause=str(exc))
    return state


def _reservation_id(job_id: str, operation_id: str,
                   profile: HostResourceProfile | DiskResourceProfile) -> str:
    return _sha256({"job_id": job_id, "operation_id": operation_id, "project_id": profile.project_id,
                    "profile_id": profile.profile_id, "profile_sha256": profile.sha256})


def _canonical_operation_from_metadata(row_operation: Any, metadata: Mapping[str, Any]) -> str:
    recorded = metadata.get("operation")
    if row_operation in {"registry_call", "operation_call"}:
        arguments = metadata.get("arguments")
        if (recorded != row_operation or not isinstance(arguments, Mapping)
                or not isinstance(arguments.get("operation_id"), str)
                or not isinstance(arguments.get("arguments", {}), Mapping)):
            _fail("RESOURCE_RESERVATION_OPERATION_UNKNOWN",
                  "durable wrapper metadata does not identify one canonical inner operation")
        operation_id = arguments["operation_id"]
        if operation_id in {"registry_call", "operation_call"}:
            _fail("RESOURCE_RESERVATION_OPERATION_UNKNOWN",
                  "nested operation wrappers are not canonical disk-producing operations")
        return operation_id
    if not isinstance(row_operation, str) or recorded != row_operation:
        _fail("RESOURCE_RESERVATION_OPERATION_UNKNOWN",
              "durable operation field and request metadata do not match")
    return row_operation


def _read_job_binding(db: sqlite3.Connection, job_id: str,
                      profile: HostResourceProfile | DiskResourceProfile,
                      operation_names: Sequence[str] | None = None) -> dict[str, Any]:
    row = db.execute(
        "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
        "j.finished_at AS job_finished_at,o.operation,o.status AS operation_status,"
        "o.metadata AS operation_metadata,o.result AS operation_result,o.finished_at AS operation_finished_at "
        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?", (job_id,),
    ).fetchone()
    if row is None:
        _fail("RESOURCE_RESERVATION_JOB_UNKNOWN", "job is not in the durable OperationStore")
    try:
        job_metadata = _event_json(row["job_metadata"])
        operation_metadata = _event_json(row["operation_metadata"])
    except ValueError:
        _fail("RESOURCE_RESERVATION_JOB_UNKNOWN", "job metadata is malformed")
    try:
        operation_result = json.loads(row["operation_result"] or "null")
    except (TypeError, json.JSONDecodeError):
        _fail("RESOURCE_RESERVATION_JOB_UNKNOWN", "job result is malformed")
    operation = _canonical_operation_from_metadata(row["operation"], operation_metadata)
    job_operation = _canonical_operation_from_metadata(row["operation"], job_metadata)
    if job_operation != operation:
        _fail("RESOURCE_RESERVATION_OPERATION_UNKNOWN",
              "job and operation records disagree about their canonical operation")
    if (job_metadata.get("project_id") != profile.project_id
            or operation_metadata.get("project_id") != profile.project_id):
        _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH", "job is not bound to the authorized project root")
    if (job_metadata.get("project_root_identity") != profile.root.file_id
            or operation_metadata.get("project_root_identity") != profile.root.file_id):
        _fail("RESOURCE_RESERVATION_ROOT_IDENTITY_MISMATCH",
              "job metadata root identity differs from the trusted resource profile")
    if isinstance(profile, DiskResourceProfile):
        bound_fields = {"disk_resource_profile_id": profile.profile_id,
                        "disk_resource_profile_sha256": profile.sha256,
                        "disk_storage_id": profile.storage_id}
        if any(job_metadata.get(key) != value or operation_metadata.get(key) != value
               for key, value in bound_fields.items()):
            _fail("RESOURCE_RESERVATION_PROFILE_BINDING_MISMATCH",
                  "job and operation metadata are not bound to the trusted disk startup profile")
    allowed = set(operation_names or profile.allowed_operations)
    if operation not in allowed or operation not in profile.allowed_operations:
        _fail("RESOURCE_RESERVATION_OPERATION_UNSUPPORTED", "job operation is outside the trusted budget profile")
    return {"job_id": row["job_id"], "operation_id": row["operation_id"], "operation": operation,
            "job_status": row["job_status"], "operation_status": row["operation_status"],
            "job_metadata": job_metadata, "operation_result": operation_result,
            "job_finished_at": row["job_finished_at"],
            "operation_finished_at": row["operation_finished_at"]}


def _unknown_result_is_nonretryable(result: Any) -> bool:
    if not isinstance(result, Mapping) or result.get("success") is not False:
        return False
    execution = result.get("execution")
    error = result.get("error")
    return (isinstance(execution, Mapping) and execution.get("status") == "UNKNOWN"
            and isinstance(error, Mapping) and error.get("safe_retry") is False)


def _job_ledger_snapshot(db: sqlite3.Connection, job_id: str,
                         *, before_event_id: int | None = None) -> tuple[int, str]:
    if before_event_id is None:
        rows = db.execute(
            "SELECT id,event,metadata,created_at FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id,event,metadata,created_at FROM job_events "
            "WHERE job_id=? AND id<? ORDER BY id", (job_id, before_event_id)
        ).fetchall()
    payload = [{"id": row["id"], "event": row["event"],
                "metadata": row["metadata"], "created_at": row["created_at"]} for row in rows]
    value = rows[-1]["id"] if rows else 0
    if type(value) is not int or not 0 <= value <= _MAX_SQLITE_INT:
        _fail("RESOURCE_QUIESCENCE_LEDGER_UNKNOWN", "durable job ledger watermark is malformed")
    return value, _sha256(payload)


def _validate_quiescence_witness_dict(witness: Mapping[str, Any],
                                       reservation: Mapping[str, Any], *,
                                       event_id: int | None = None,
                                       db: sqlite3.Connection | None = None) -> None:
    required = {
        "schema", "provider_id", "profile_id", "profile_sha256", "project_id", "root_file_id",
        "host_id", "job_id", "operation_id", "reservation_id", "domain_status", "safe_retry",
        "process_tree_state", "process_tree_evidence_sha256", "owned_writes_state",
        "owned_writes_evidence_sha256", "operation_ledger_state", "operation_ledger_watermark",
        "operation_ledger_evidence_sha256", "observed_monotonic_ns",
    }
    if set(witness) != required:
        raise ValueError("quiescence witness fields do not match the supported schema")
    if (witness.get("schema") != QUIESCENCE_SCHEMA
            or not _nonempty(witness.get("provider_id"))
            or witness.get("domain_status") != "UNKNOWN"
            or witness.get("safe_retry") is not False
            or witness.get("process_tree_state") != "TERMINATED"
            or witness.get("owned_writes_state") != "STOPPED"
            or witness.get("operation_ledger_state") != "QUIESCENT"):
        raise ValueError("quiescence witness lacks exact nonretryable UNKNOWN cleanup states")
    for field_name in ("profile_sha256", "process_tree_evidence_sha256",
                       "owned_writes_evidence_sha256", "operation_ledger_evidence_sha256"):
        if not _is_sha256(witness.get(field_name)):
            raise ValueError(f"quiescence witness {field_name} is invalid")
    bindings = ("profile_id", "profile_sha256", "project_id", "root_file_id", "job_id",
                "operation_id", "reservation_id")
    if any(witness.get(name) != reservation.get(name) for name in bindings):
        raise ValueError("quiescence witness is bound to another reservation or operation")
    if not _nonempty(witness.get("host_id")):
        raise ValueError("quiescence witness host identity is missing")
    try:
        watermark = _checked_int(witness.get("operation_ledger_watermark"),
                                 "operation_ledger_watermark")
        _checked_int(witness.get("observed_monotonic_ns"), "observed_monotonic_ns")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if event_id is not None and watermark >= event_id:
        raise ValueError("quiescence ledger watermark is not before the durable release event")
    if db is not None:
        observed_watermark, observed_digest = _job_ledger_snapshot(
            db, str(witness["job_id"]), before_event_id=event_id)
        if watermark != observed_watermark or witness["operation_ledger_evidence_sha256"] != observed_digest:
            raise ValueError("quiescence witness does not match the durable job ledger snapshot")


def _verify_quiescence_witness(*, provider: JobQuiescenceProvider | None, store: Any,
                               profile: HostResourceProfile, job: Mapping[str, Any],
                               reservation: Mapping[str, Any]) -> dict[str, Any]:
    if provider is None:
        _fail("RESOURCE_QUIESCENCE_PROVIDER_NOT_CONFIGURED",
              "UNKNOWN reservation remains fenced without an independent process/write/ledger verifier",
              evidence_classification="SOFTWARE_ONLY")
    if not isinstance(provider, NativeJobQuiescenceProvider):
        _fail("RESOURCE_QUIESCENCE_PROVIDER_UNTRUSTED",
              "caller-supplied or synthetic providers cannot release UNKNOWN reservations",
              evidence_classification="SOFTWARE_ONLY")
    if not _unknown_result_is_nonretryable(job.get("operation_result")):
        _fail("RESOURCE_QUIESCENCE_DOMAIN_STATE_UNBOUND",
              "durable operation result does not preserve UNKNOWN with safe_retry=false")
    if job.get("job_finished_at") is not None or job.get("operation_finished_at") is not None:
        _fail("RESOURCE_QUIESCENCE_DOMAIN_STATE_UNBOUND",
              "UNKNOWN reservation has terminal finish timestamps")
    try:
        witness = provider.prove_quiescence(store=store, profile=profile,
                                             job_binding=job,
                                             reservation_id=str(reservation["reservation_id"]))
    except Exception as exc:
        _fail("RESOURCE_QUIESCENCE_PROVIDER_UNAVAILABLE",
              "independent process/write/ledger proof could not be obtained",
              cause_type=type(exc).__name__)
    if (not isinstance(witness, JobQuiescenceWitness)
            or witness._attestation is not _NATIVE_QUIESCENCE_ATTESTATION):
        _fail("RESOURCE_QUIESCENCE_WITNESS_UNTRUSTED",
              "provider did not return a native-attested typed witness")
    try:
        payload = witness.as_dict()
        _validate_quiescence_witness_dict(payload, reservation)
    except (TypeError, ValueError) as exc:
        _fail("RESOURCE_QUIESCENCE_WITNESS_INVALID", "quiescence witness is malformed",
              cause=str(exc))
    if (witness.profile_id != profile.profile_id or witness.profile_sha256 != profile.sha256
            or witness.project_id != profile.project_id or witness.root_file_id != profile.root.file_id
            or witness.host_id != profile.host_id or witness.job_id != job.get("job_id")
            or witness.operation_id != job.get("operation_id")):
        _fail("RESOURCE_QUIESCENCE_WITNESS_IDENTITY_MISMATCH",
              "quiescence witness does not match the active host, project, job, and operation")
    expected_watermark, expected_ledger_digest = _job_ledger_snapshot(store.db, str(job["job_id"]))
    if (witness.operation_ledger_watermark != expected_watermark
            or witness.operation_ledger_evidence_sha256 != expected_ledger_digest):
        _fail("RESOURCE_QUIESCENCE_LEDGER_CHANGED",
              "durable job ledger changed or does not match the quiescence observation",
              expected_watermark=expected_watermark,
              observed_watermark=witness.operation_ledger_watermark,
              expected_sha256=expected_ledger_digest)
    verification_time_ns = time.monotonic_ns()
    if (witness.observed_monotonic_ns > verification_time_ns
            or verification_time_ns - witness.observed_monotonic_ns > _QUIESCENCE_MAX_AGE_NS):
        _fail("RESOURCE_QUIESCENCE_WITNESS_STALE",
              "quiescence observation is stale or from the future")
    return payload


def _reserve_disk_for_job_in_transaction(*, store: Any, authority: Any,
                                         profile: HostResourceProfile | DiskResourceProfile,
                                         job_id: str, requested_bytes: int,
                                         observation: StorageObservation,
                                         now_monotonic_ns: int,
                                         max_age_ns: int) -> dict[str, Any]:
    if not store.db.in_transaction:
        _fail("RESOURCE_RESERVATION_TRANSACTION_REQUIRED",
              "disk reservation must be written inside an immediate OperationStore transaction")
    job = _read_job_binding(store.db, job_id, profile)
    permission = _permission_for_disk_operation(job["operation"])
    _authorize_profile_project(authority, profile, profile.project_id, permission,
                               code_prefix="RESOURCE_RESERVATION")
    if isinstance(profile, DiskResourceProfile):
        if not profile.trusted_startup_config:
            _fail("DISK_RESOURCE_PROFILE_UNTRUSTED",
                  "only a profile loaded from trusted startup configuration may reserve disk")
        if requested_bytes != profile.estimate_for(job["operation"]):
            _fail("DISK_ESTIMATE_MISMATCH",
                  "reservation size must equal the trusted estimate for the durable canonical operation",
                  operation_id=job["operation"])
        if observation._observation_attestation is not _POSIX_STORAGE_OBSERVATION_ATTESTATION:
            _fail("POSIX_STORAGE_OBSERVATION_UNVERIFIED",
                  "disk profiles require a fresh pinned POSIX storage observation")
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
        return {"status": "RESERVED", "reservation_id": reservation_id,
                "requested_bytes": requested_bytes, "scope_id": item["scope_id"],
                "enforcement": item["enforcement"], "idempotent": True,
                "evidence_classification": item.get("evidence_classification", "SOFTWARE_ONLY")}
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
    return {"status": "RESERVED", "reservation_id": reservation_id,
            "requested_bytes": requested_bytes, "scope_id": admission.scope_id,
            "enforcement": admission.enforcement, "idempotent": False,
            "evidence_classification": admission.evidence_classification}


def reserve_disk_for_job(*, store: Any, authority: Any,
                         profile: HostResourceProfile | DiskResourceProfile,
                         job_id: str, requested_bytes: int, observation: StorageObservation,
                         now_monotonic_ns: int, max_age_ns: int = 30_000_000_000) -> dict[str, Any]:
    """Atomically reserve a caller-internal amount against the durable ledger.

    Production DiskResourceProfile calls must use its trusted per-operation
    estimate and a POSIX provider observation. Host profiles retain the older
    native-hard-quota policy contract.
    """
    from ._operation_store import OperationStore
    from ._project_authority import ProjectAuthority
    if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) or authority.store is not store:
        _fail("RESOURCE_RESERVATION_AUTHORITY_REQUIRED", "reservation requires the existing bound ProjectAuthority and OperationStore")
    if not isinstance(profile, (HostResourceProfile, DiskResourceProfile)) or not _nonempty(job_id):
        _fail("INVALID_DISK_RESERVATION", "trusted profile and job identity are required")
    if type(requested_bytes) is not int or not 1 <= requested_bytes <= _MAX_SQLITE_INT:
        _fail("INVALID_DISK_RESERVATION", "requested disk bytes must be a positive bounded integer")
    with store.lock:
        try:
            store.db.execute("BEGIN IMMEDIATE")
            result = _reserve_disk_for_job_in_transaction(
                store=store, authority=authority, profile=profile, job_id=job_id,
                requested_bytes=requested_bytes, observation=observation,
                now_monotonic_ns=now_monotonic_ns, max_age_ns=max_age_ns)
            store.db.execute("COMMIT")
            return result
        except BaseException:
            if store.db.in_transaction:
                store.db.execute("ROLLBACK")
            raise


def release_disk_for_job(*, store: Any, authority: Any,
                         profile: HostResourceProfile | DiskResourceProfile,
                         job_id: str,
                         quiescence_provider: JobQuiescenceProvider | None = None) -> dict[str, Any]:
    """Release terminal work, or UNKNOWN only after exact independent quiescence proof.

    Releasing a disk reservation is physical resource accounting only. The
    UNKNOWN operation result and ``safe_retry=false`` remain untouched.
    """
    from ._operation_store import OperationStore
    from ._project_authority import ProjectAuthority
    if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) or authority.store is not store:
        _fail("RESOURCE_RESERVATION_AUTHORITY_REQUIRED", "release requires the existing bound ProjectAuthority and OperationStore")
    if not isinstance(profile, (HostResourceProfile, DiskResourceProfile)):
        _fail("DISK_PROFILE_REQUIRED", "reservation release requires a typed trusted resource profile")
    with store.lock:
        try:
            store.db.execute("BEGIN IMMEDIATE")
            job = _read_job_binding(store.db, job_id, profile)
            permission = _permission_for_disk_operation(job["operation"])
            _authorize_profile_project(authority, profile, profile.project_id, permission,
                                       code_prefix="RESOURCE_RESERVATION")
            reservation_id = _reservation_id(job_id, job["operation_id"], profile)
            states = _load_reservations(store.db)
            current = states.get(reservation_id)
            if current is None:
                _fail("RESOURCE_RESERVATION_NOT_FOUND", "job has no durable disk reservation")
            found_job, item = current
            if found_job != job_id:
                _fail("RESOURCE_RESERVATION_IDENTITY_CONFLICT", "reservation belongs to another job")
            if item["status"] in {"RELEASED", "RELEASED_AFTER_QUIESCENCE"}:
                store.db.execute("COMMIT")
                result = {"status": item["status"], "reservation_id": reservation_id,
                          "idempotent": True, "evidence_classification": "SOFTWARE_ONLY"}
                if item["status"] == "RELEASED_AFTER_QUIESCENCE":
                    result.update({"domain_status_at_release": "UNKNOWN", "safe_retry_at_release": False,
                                   "quiescence_witness_sha256": item["quiescence_witness_sha256"]})
                return result

            if job["job_status"] != job["operation_status"]:
                _fail("RESOURCE_RESERVATION_UNCONFIRMED",
                      "job and operation status do not agree",
                      job_status=job["job_status"], operation_status=job["operation_status"])
            if job["job_status"] == "UNKNOWN":
                witness = _verify_quiescence_witness(
                    provider=quiescence_provider, store=store, profile=profile, job=job,
                    reservation=item)
                witness_sha256 = _sha256(witness)
                released = {**item, "status": "RELEASED_AFTER_QUIESCENCE",
                            "release_basis": "INDEPENDENT_QUIESCENCE",
                            "quiescence_witness": witness,
                            "quiescence_witness_sha256": witness_sha256}
                store.db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                                 (job_id, "W26DiskReservation", _canonical_json(released)))
                store.db.execute("COMMIT")
                return {"status": "RELEASED_AFTER_QUIESCENCE", "reservation_id": reservation_id,
                        "idempotent": False, "domain_status_at_release": "UNKNOWN",
                        "safe_retry_at_release": False,
                        "quiescence_witness_sha256": witness_sha256,
                        "evidence_classification": "SOFTWARE_ONLY"}

            if job["job_status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                _fail("RESOURCE_RESERVATION_UNCONFIRMED",
                      "reservation remains held until terminal status or independently verified UNKNOWN quiescence",
                      job_status=job["job_status"])
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


def _durable_job_context(db: sqlite3.Connection, job_id: str) -> dict[str, Any]:
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
    operation_id = _canonical_operation_from_metadata(row["operation"], operation_metadata)
    job_operation_id = _canonical_operation_from_metadata(row["operation"], job_metadata)
    if operation_id != job_operation_id:
        _fail("RESOURCE_RESERVATION_OPERATION_UNKNOWN",
              "job and operation records disagree about their canonical operation")
    project_ids: list[str] = []
    for metadata in (operation_metadata, job_metadata):
        top_level = metadata.get("project_id")
        execution = metadata.get("execution")
        nested = execution.get("project_id") if isinstance(execution, Mapping) else None
        for value in (top_level, nested):
            if value is not None:
                if not isinstance(value, str) or not value:
                    _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH", "durable project identity is malformed")
                project_ids.append(value)
    if not project_ids or len(set(project_ids)) != 1:
        _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH",
              "job and operation metadata do not bind one exact project identity")
    return {"job_id": row["job_id"], "operation_id": row["operation_id"],
            "canonical_operation_id": operation_id, "project_id": project_ids[0],
            "job_status": row["job_status"], "operation_status": row["operation_status"],
            "job_metadata": job_metadata, "operation_metadata": operation_metadata}


def _bind_disk_root_metadata(db: sqlite3.Connection, context: Mapping[str, Any],
                             profile: DiskResourceProfile) -> None:
    expected = {
        "project_id": profile.project_id,
        "project_root_identity": profile.root.file_id,
        "project_root_volume_id": profile.root.volume_id,
        "project_root_owner_id": profile.root.owner_id,
        "disk_resource_profile_id": profile.profile_id,
        "disk_resource_profile_sha256": profile.sha256,
        "disk_storage_id": profile.storage_id,
    }
    job_metadata = dict(context["job_metadata"])
    operation_metadata = dict(context["operation_metadata"])
    for metadata in (job_metadata, operation_metadata):
        execution = metadata.get("execution")
        execution_project = execution.get("project_id") if isinstance(execution, Mapping) else None
        if execution_project != profile.project_id:
            _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH",
                  "durable request execution does not bind the trusted profile project")
        for key, value in expected.items():
            if key in metadata and metadata[key] != value:
                _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH",
                      "durable job metadata contains a conflicting project-root or profile binding",
                      field=key)
            metadata[key] = value
    for key in expected:
        if job_metadata.get(key) != operation_metadata.get(key):
            _fail("RESOURCE_RESERVATION_PROJECT_MISMATCH",
                  "job and operation project-root bindings disagree", field=key)
    db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
               (_canonical_json(job_metadata), context["job_id"]))
    db.execute("UPDATE operations SET metadata=? WHERE operation_id=?",
               (_canonical_json(operation_metadata), context["operation_id"]))
    existing = db.execute(
        "SELECT metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id DESC LIMIT 1",
        (context["job_id"], DISK_ROOT_BINDING_EVENT),
    ).fetchone()
    event = {"schema": "comsol-mcp.disk-root-binding/v1", "project_id": profile.project_id,
             "operation_id": context["operation_id"], "canonical_operation_id": context["canonical_operation_id"],
             "profile_id": profile.profile_id, "profile_sha256": profile.sha256,
             "host_id": profile.host_id, "storage_id": profile.storage_id,
             "root": profile.root.as_dict()}
    if existing is not None:
        prior = _event_json(existing["metadata"])
        if prior != event:
            _fail("RESOURCE_RESERVATION_PROFILE_BINDING_MISMATCH",
                  "existing durable root binding differs from the selected trusted profile")
        return
    db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
               (context["job_id"], DISK_ROOT_BINDING_EVENT, _canonical_json(event)))


class DiskResourceCoordinator:
    """Trusted startup resolver and pre-dispatch disk reservation controller.

    The production daemon calls this inside its existing serialized lane,
    after permission admission and before RUNNING/Worker dispatch. This unit
    does not add a queue or launch gate. UNKNOWN reservations stay held until
    an independently verified quiescence provider exists.
    """

    def __init__(self, *, config: TrustedDiskResourceConfig, store: Any,
                 authority: Any,
                 storage_provider: PosixStorageObservationProvider | None = None,
                 max_observation_age_ns: int = 30_000_000_000):
        from ._operation_store import OperationStore
        from ._project_authority import ProjectAuthority
        if type(config) is not TrustedDiskResourceConfig or not config.trusted_startup_source:
            _fail("DISK_RESOURCE_CONFIG_UNTRUSTED",
                  "coordinator requires an exact trusted-startup configuration resolver")
        if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) or authority.store is not store:
            _fail("RESOURCE_RESERVATION_AUTHORITY_REQUIRED",
                  "disk coordinator requires the existing bound ProjectAuthority and OperationStore")
        if storage_provider is None and _current_platform_name() in {"macos", "linux"}:
            storage_provider = PosixStorageObservationProvider()
        if storage_provider is not None and type(storage_provider) is not PosixStorageObservationProvider:
            _fail("DISK_STORAGE_PROVIDER_UNTRUSTED",
                  "storage observations require the built-in pinned POSIX provider")
        if type(max_observation_age_ns) is not int or max_observation_age_ns <= 0:
            raise ValueError("max_observation_age_ns must be positive")
        self.config = config
        self.store = store
        self.authority = authority
        self.storage_provider = storage_provider
        self.max_observation_age_ns = max_observation_age_ns

    def requires_reservation(self, *, project_id: str | None,
                             operation_id: str) -> bool:
        """Fail closed for disk-producing routes when this daemon has a profile.

        A daemon with no disk config never constructs this coordinator and keeps
        the historical route. Once a trusted disk config is installed, every
        canonical disk-producing project route must resolve a profile; only
        catalogued non-disk effects bypass this admission path.
        """
        if not isinstance(operation_id, str) or not operation_id:
            _fail("RESOURCE_OPERATION_EFFECT_UNAVAILABLE",
                  "disk admission requires a canonical operation identifier")
        effect = _registered_operation_effect(operation_id)
        if effect is None:
            _fail("RESOURCE_OPERATION_EFFECT_UNAVAILABLE",
                  "configured disk admission cannot classify this registered operation",
                  operation_id=operation_id)
        if effect not in _DISK_PRODUCING_EFFECTS:
            return False
        if not _nonempty(project_id):
            _fail("PROJECT_IDENTITY_REQUIRED",
                  "configured disk-producing operations require an authorized project identity",
                  operation_id=operation_id)
        # resolve() refuses a missing project profile. This prevents a partial
        # trusted startup inventory from silently allowing a disk-producing op.
        self.config.resolve(project_id)
        return True

    def reserve_queued_job(self, job_id: str) -> dict[str, Any]:
        """Derive operation/estimate from durable records and atomically bind+reserve."""
        if not _nonempty(job_id):
            _fail("INVALID_DISK_RESERVATION", "job identity is required")
        if self.storage_provider is None:
            _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED",
                  "no reviewed native storage provider is available on this host",
                  capability_state=CapabilityState.UNSUPPORTED.value)
        with self.store.lock:
            context = _durable_job_context(self.store.db, job_id)
            if context["job_status"] != "QUEUED" or context["operation_status"] != "QUEUED":
                _fail("RESOURCE_RESERVATION_NOT_PREDISPATCH",
                      "disk admission must complete before the durable operation enters RUNNING",
                      job_status=context["job_status"], operation_status=context["operation_status"])
            profile = self.config.resolve(context["project_id"])
            if context["canonical_operation_id"] not in profile.allowed_operations:
                _fail("RESOURCE_RESERVATION_OPERATION_UNSUPPORTED",
                      "canonical operation is outside the trusted disk profile")
            estimate = profile.estimate_for(context["canonical_operation_id"])
            permission = _permission_for_disk_operation(context["canonical_operation_id"])
            _authorize_profile_project(self.authority, profile, profile.project_id, permission,
                                       code_prefix="RESOURCE_RESERVATION")
            observation = self.storage_provider.read_observation(profile)
            now_ns = time.monotonic_ns()
            try:
                self.store.db.execute("BEGIN IMMEDIATE")
                current = _durable_job_context(self.store.db, job_id)
                if (current["job_status"] != "QUEUED" or current["operation_status"] != "QUEUED"
                        or current["operation_id"] != context["operation_id"]
                        or current["canonical_operation_id"] != context["canonical_operation_id"]
                        or current["project_id"] != context["project_id"]):
                    _fail("RESOURCE_RESERVATION_NOT_PREDISPATCH",
                          "durable job changed during disk admission")
                _bind_disk_root_metadata(self.store.db, current, profile)
                result = _reserve_disk_for_job_in_transaction(
                    store=self.store, authority=self.authority, profile=profile, job_id=job_id,
                    requested_bytes=estimate, observation=observation,
                    now_monotonic_ns=now_ns,
                    max_age_ns=self.max_observation_age_ns)
                self.store.db.execute("COMMIT")
            except BaseException:
                if self.store.db.in_transaction:
                    self.store.db.execute("ROLLBACK")
                raise
        return {**result, "operation_id": context["canonical_operation_id"],
                "trusted_estimate_bytes": estimate,
                "disk_resource_profile_id": profile.profile_id,
                "disk_resource_profile_sha256": profile.sha256,
                "host_id": profile.host_id, "storage_id": profile.storage_id,
                "evidence_classification": "HOST_OBSERVED_ADMISSION_ONLY"}

    def release_terminal_job(self, job_id: str) -> dict[str, Any]:
        """Release only after durable terminal status; UNKNOWN remains fenced."""
        with self.store.lock:
            context = _durable_job_context(self.store.db, job_id)
            profile = self.config.resolve(context["project_id"])
        return release_disk_for_job(store=self.store, authority=self.authority,
                                    profile=profile, job_id=job_id)

    def release_terminal_if_reserved(self, job_id: str) -> dict[str, Any] | None:
        """Idempotently release a known reservation; unrelated jobs are a no-op."""
        with self.store.lock:
            reservations = _load_reservations(self.store.db)
            matching = [(reservation_id, item) for reservation_id, (bound_job_id, item)
                        in reservations.items() if bound_job_id == job_id]
            if not matching:
                return None
            if len(matching) != 1:
                _fail("RESOURCE_RESERVATION_IDENTITY_CONFLICT",
                      "job has more than one disk reservation")
            _reservation_id_value, reservation = matching[0]
            if reservation.get("status") in {"RELEASED", "RELEASED_AFTER_QUIESCENCE"}:
                return {"status": reservation["status"], "idempotent": True}
        return self.release_terminal_job(job_id)
