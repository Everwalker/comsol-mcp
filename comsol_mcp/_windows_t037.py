"""Read-only Windows T037 observations and fail-closed AppContainer launch seam.

This module never creates an AppContainer profile, service account, firewall or
WFP rule, ACL, quota, or persistent host policy.  The launch factory only runs
when a separately installed native policy verifier can attest the exact
profile, Server/Worker images, loopback requirements, and service identity.
That verifier is intentionally unavailable in this unit, so production launch
requests currently stop before process creation.

All Windows APIs are late-bound.  Importing this module on other platforms is
safe; invoking a native observer or launch API there fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

from ._execution_contract import ExecutionContractError
from ._windows_sid import is_canonical_sid

PROFILE_SCHEMA = "comsol-mcp.windows-t037-profile/1"
PROFILE_SCHEMA_V2 = "comsol-mcp.windows-t037-profile/2"
PROFILE_SCHEMA_V3 = "comsol-mcp.windows-t037-profile/3"
MAX_PROFILE_BYTES = 64 * 1024
MAX_SIDS = 64
MAX_JOB_PIDS = 4096
MAX_ACL_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")

ERROR_UNSUPPORTED = "T037_UNSUPPORTED"
ERROR_PROFILE_INVALID = "T037_PROFILE_INVALID"
ERROR_POLICY_UNVERIFIED = "T037_POLICY_UNVERIFIED"
ERROR_HOST_MISMATCH = "T037_HOST_MISMATCH"


class WindowsT037Error(ExecutionContractError):
    """A typed, fail-closed T037 platform/configuration refusal."""


def _refuse(code: str, message: str, **details: Any) -> None:
    raise WindowsT037Error(code, message, details=details or None)


@dataclass(frozen=True)
class WindowsT037Profile:
    profile_id: str
    host_fingerprint_sha256: str
    service_account_sid: str
    interactive_account_sid: str
    appcontainer_sid: str
    capability_sids: tuple[str, ...]
    server_image_sha256: str
    worker_image_sha256: str
    workspace_root: str
    require_hard_quota: bool
    source_sha256: str
    source_identity: Mapping[str, Any]
    security_semantics: str

    def as_safe_dict(self) -> dict[str, Any]:
        """Return credential-free configuration metadata, never OS proof."""
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "host_fingerprint_sha256": self.host_fingerprint_sha256,
            "service_account_sid_sha256": hashlib.sha256(self.service_account_sid.encode("ascii")).hexdigest(),
            "interactive_account_sid_sha256": hashlib.sha256(self.interactive_account_sid.encode("ascii")).hexdigest(),
            "appcontainer_sid_sha256": hashlib.sha256(self.appcontainer_sid.encode("ascii")).hexdigest(),
            "capability_count": len(self.capability_sids),
            "server_image_sha256": self.server_image_sha256,
            "worker_image_sha256": self.worker_image_sha256,
            "workspace_root_sha256": hashlib.sha256(self.workspace_root.encode("utf-8")).hexdigest(),
            "require_hard_quota": self.require_hard_quota,
            "source_sha256": self.source_sha256,
            "source_identity": dict(self.source_identity),
            "security_semantics": self.security_semantics,
            "verification_state": "NOT_VERIFIED",
        }


@dataclass(frozen=True)
class WindowsT037ProfileV2:
    """Windows isolation configuration bridged to the shared host profile."""

    host_resource_profile: Any
    source_sha256: str
    source_identity: Mapping[str, Any]
    security_semantics: str
    job_limits: Any = None
    wrapper_schema: str = PROFILE_SCHEMA_V2

    @property
    def profile_id(self) -> str:
        return self.host_resource_profile.profile_id

    @property
    def host_fingerprint_sha256(self) -> str:
        return self.host_resource_profile.windows_isolation.host_fingerprint_sha256

    @property
    def service_account_sid(self) -> str:
        return self.host_resource_profile.windows_isolation.service_logon_sid

    @property
    def service_sid(self) -> str:
        return self.host_resource_profile.windows_isolation.service_sid

    @property
    def interactive_account_sid(self) -> str:
        return self.host_resource_profile.interactive_account_id

    @property
    def appcontainer_sid(self) -> str:
        return self.host_resource_profile.windows_isolation.appcontainer_sid

    @property
    def capability_sids(self) -> tuple[str, ...]:
        return self.host_resource_profile.windows_isolation.capability_sids

    @property
    def service_name(self) -> str:
        return self.host_resource_profile.windows_isolation.service_name

    @property
    def service_start_name(self) -> str:
        return self.host_resource_profile.windows_isolation.service_start_name

    @property
    def workspace_root(self) -> str:
        return self.host_resource_profile.root.path

    @property
    def require_hard_quota(self) -> bool:
        return self.host_resource_profile.disk_budget.mode == "REQUIRE_HARD_QUOTA"

    @property
    def server_image_sha256(self) -> str:
        return self._launch_image("server").image_sha256

    @property
    def worker_image_sha256(self) -> str:
        return self._launch_image("worker").image_sha256

    @property
    def launch_images(self) -> tuple[Any, ...]:
        return self.host_resource_profile.launch_images

    @property
    def sha256(self) -> str:
        if self.wrapper_schema == PROFILE_SCHEMA_V3:
            from ._windows_job_limits import require_limits
            return hashlib.sha256(json.dumps({"schema": PROFILE_SCHEMA_V3,
                "host_resource_profile_sha256": self.host_resource_profile.sha256,
                "job_limits": require_limits(self).as_dict()}, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return self.host_resource_profile.sha256

    @property
    def windows_policy(self) -> Any:
        return self.host_resource_profile.windows_isolation

    def _launch_image(self, role: str):
        matches = [row for row in self.launch_images if row.role == role]
        if len(matches) != 1:
            _refuse(ERROR_PROFILE_INVALID, f"Windows v2 profile requires exactly one {role} image")
        return matches[0]

    def as_safe_dict(self) -> dict[str, Any]:
        policy = self.host_resource_profile.windows_isolation
        result = {
            "schema": PROFILE_SCHEMA_V2,
            "profile_id": self.profile_id,
            "host_fingerprint_sha256": self.host_fingerprint_sha256,
            "host_resource_profile_sha256": self.host_resource_profile.sha256,
            "service_logon_sid_sha256": hashlib.sha256(self.service_account_sid.encode("ascii")).hexdigest(),
            "service_sid_sha256": hashlib.sha256(self.service_sid.encode("ascii")).hexdigest(),
            "interactive_account_sid_sha256": hashlib.sha256(self.interactive_account_sid.encode("ascii")).hexdigest(),
            "appcontainer_sid_sha256": hashlib.sha256(self.appcontainer_sid.encode("ascii")).hexdigest(),
            "capability_count": len(self.capability_sids),
            "service_name": policy.service_name,
            "service_start_name_sha256": hashlib.sha256(policy.service_start_name.encode("utf-8")).hexdigest(),
            "root_identity": {"volume_id": self.host_resource_profile.root.volume_id,
                              "file_id": self.host_resource_profile.root.file_id},
            "launch_image_roles": sorted(row.role for row in self.launch_images),
            "wfp_grant_count": len(policy.wfp_grants),
            "acl_grant_count": len(policy.acl_grants),
            "require_hard_quota": self.require_hard_quota,
            "source_sha256": self.source_sha256,
            "source_identity": dict(self.source_identity),
            "security_semantics": self.security_semantics,
            "verification_state": "NOT_VERIFIED",
        }

        if self.wrapper_schema == PROFILE_SCHEMA_V3:
            from ._windows_job_limits import require_limits
            targets = require_limits(self)
            result.update(schema=PROFILE_SCHEMA_V3, profile_sha256=self.sha256,
                          job_limits=targets.as_dict(), job_limits_sha256=targets.sha256)
        return result


def parse_profile_document(document: Any, *, source_sha256: str,
                           source_identity: Mapping[str, Any] | None = None,
                           security_semantics: str = "TEST_INPUT_ONLY") -> WindowsT037Profile:
    """Strictly parse intent fields; this function cannot establish trust."""
    if not isinstance(document, Mapping):
        _refuse(ERROR_PROFILE_INVALID, "T037 profile must be a JSON object")
    expected = {
        "schema", "profile_id", "host_fingerprint_sha256", "service_account_sid",
        "interactive_account_sid", "appcontainer_sid", "capability_sids",
        "server_image_sha256", "worker_image_sha256", "workspace_root",
        "require_hard_quota",
    }
    if set(document) != expected or document.get("schema") != PROFILE_SCHEMA:
        _refuse(ERROR_PROFILE_INVALID, "T037 profile fields/schema do not match the closed version-1 contract")
    profile_id = document["profile_id"]
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        _refuse(ERROR_PROFILE_INVALID, "T037 profile_id is malformed")
    for name in ("host_fingerprint_sha256", "server_image_sha256", "worker_image_sha256"):
        value = document[name]
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            _refuse(ERROR_PROFILE_INVALID, f"T037 {name} must be a lowercase SHA-256 digest")
    for name in ("service_account_sid", "interactive_account_sid", "appcontainer_sid"):
        value = document[name]
        if not is_canonical_sid(value):
            _refuse(ERROR_PROFILE_INVALID, f"T037 {name} is not a canonical SID string")
    if document["service_account_sid"].casefold() == document["interactive_account_sid"].casefold():
        _refuse(ERROR_PROFILE_INVALID, "T037 service and interactive account SIDs must be distinct")
    capabilities = document["capability_sids"]
    if (not isinstance(capabilities, list) or len(capabilities) > MAX_SIDS
            or any(not is_canonical_sid(sid) for sid in capabilities)
            or len({sid.casefold() for sid in capabilities}) != len(capabilities)):
        _refuse(ERROR_PROFILE_INVALID, "T037 capability_sids must be a bounded unique SID array")
    root = document["workspace_root"]
    if (not isinstance(root, str) or not root or "\x00" in root
            or not re.match(r"^[A-Za-z]:\\", root) or root.startswith("\\\\")):
        _refuse(ERROR_PROFILE_INVALID, "T037 workspace_root must be a local absolute drive path")
    if type(document["require_hard_quota"]) is not bool:
        _refuse(ERROR_PROFILE_INVALID, "T037 require_hard_quota must be boolean")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        _refuse(ERROR_PROFILE_INVALID, "T037 source digest is malformed")
    return WindowsT037Profile(
        profile_id=profile_id,
        host_fingerprint_sha256=document["host_fingerprint_sha256"],
        service_account_sid=document["service_account_sid"],
        interactive_account_sid=document["interactive_account_sid"],
        appcontainer_sid=document["appcontainer_sid"],
        capability_sids=tuple(capabilities),
        server_image_sha256=document["server_image_sha256"],
        worker_image_sha256=document["worker_image_sha256"],
        workspace_root=root,
        require_hard_quota=document["require_hard_quota"],
        source_sha256=source_sha256,
        source_identity=dict(source_identity or {}),
        security_semantics=security_semantics,
    )


def parse_profile_v2_document(document: Any, *, source_sha256: str,
                              source_identity: Mapping[str, Any] | None = None,
                              security_semantics: str = "TEST_INPUT_ONLY") -> WindowsT037ProfileV2:
    """Parse the closed shared HostResourceProfile v2 bridge.

    Parsing establishes configuration validity only. It cannot mint native
    policy proof, mutate host state, or enable launch.
    """
    if not isinstance(document, Mapping) or set(document) != {"schema", "host_resource_profile"}:
        _refuse(ERROR_PROFILE_INVALID, "T037 v2 profile must use the exact wrapper fields")
    if document.get("schema") != PROFILE_SCHEMA_V2:
        _refuse(ERROR_PROFILE_INVALID, "T037 v2 profile schema is not recognized")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        _refuse(ERROR_PROFILE_INVALID, "T037 v2 source digest is malformed")
    try:
        from ._w26_os_resources import parse_host_resource_profile
        host_profile = parse_host_resource_profile(document["host_resource_profile"])
    except (TypeError, ValueError) as exc:
        raise WindowsT037Error(ERROR_PROFILE_INVALID,
                               "T037 v2 HostResourceProfile failed closed validation") from exc
    if host_profile.platform != "windows" or host_profile.windows_isolation is None:
        _refuse(ERROR_PROFILE_INVALID, "T037 v2 requires the Windows isolation extension")
    for role in ("launcher", "server", "worker"):
        if sum(row.role == role for row in host_profile.launch_images) != 1:
            _refuse(ERROR_PROFILE_INVALID, f"T037 v2 requires exactly one {role} launch binding")
    return WindowsT037ProfileV2(host_profile, source_sha256, dict(source_identity or {}), security_semantics)


def parse_profile_v3_document(document: Any, *, source_sha256: str,
                              source_identity: Mapping[str, Any] | None = None,
                              security_semantics: str = "UNVERIFIED") -> WindowsT037ProfileV2:
    from ._windows_job_limits import parse_job_limits
    if not isinstance(document, Mapping) or document.get("schema") != PROFILE_SCHEMA_V3:
        _refuse(ERROR_PROFILE_INVALID, "T037 v3 profile schema is unsupported")
    if "job_limits" not in document or document["job_limits"] is None:
        _refuse("JOB_LIMITS_NOT_CONFIGURED", "T037 v3 explicit Job targets are not configured")
    if set(document) != {"schema", "host_resource_profile", "job_limits"}:
        _refuse(ERROR_PROFILE_INVALID, "T037 v3 profile must use its exact closed wrapper")
    targets = parse_job_limits(document["job_limits"])
    previous = parse_profile_v2_document({"schema": PROFILE_SCHEMA_V2,
        "host_resource_profile": document["host_resource_profile"]}, source_sha256=source_sha256,
        source_identity=source_identity, security_semantics=security_semantics)
    return WindowsT037ProfileV2(previous.host_resource_profile, previous.source_sha256,
        previous.source_identity, previous.security_semantics, targets, PROFILE_SCHEMA_V3)


def load_trusted_profile(path: str | os.PathLike[str], *,
                         host_fingerprint_reader: Callable[[], str] | None = None) -> WindowsT037Profile | WindowsT037ProfileV2:
    """Read a private, ACL-verified profile through a pinned Windows handle.

    The value read from this file is intent only.  It never contains fields
    which self-assert a VERIFIED state.  The security helper and the pinned
    reader are both rechecked around the read to detect name/ACL replacement.
    """
    if os.name != "nt":
        _refuse(ERROR_UNSUPPORTED, "trusted Windows T037 profile loading requires Windows handle/ACL APIs")
    target = Path(path)
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        _refuse(ERROR_PROFILE_INVALID, "T037 profile path must be an absolute ordinary file path")
    try:
        from ._host_scoped_provider import verify_private_path_security
        from ._windows_parameter_publish import _API
        api = _API()
        before = verify_private_path_security(target, directory=False)
        chain = api.open_security_path(target.parent, directory=True)
        handle = None
        try:
            handle = api.open_source(chain[-1], target.name)
            identity, size, creation, written, links = api.source_fingerprint(handle)
            expected_identity = {"device": identity[0], "inode": identity[1],
                                 "security_sha256": before["security_sha256"]}
            if size < 1 or size > MAX_PROFILE_BYTES or links != 1:
                _refuse(ERROR_PROFILE_INVALID, "T037 profile size or link count is outside the trusted bound")
            if before["identity"] != expected_identity:
                _refuse(ERROR_HOST_MISMATCH, "T037 profile identity changed during pinned open")
            if before["security_semantics"] != "windows-read-control-owner-dacl-persistent-acls":
                _refuse(ERROR_UNSUPPORTED, "T037 profile path lacks the verified Windows ACL semantics")
            payload = bytearray()
            while len(payload) < size:
                block = api.read_chunk(handle, min(16 * 1024, size - len(payload)))
                if not block:
                    _refuse(ERROR_PROFILE_INVALID, "T037 profile ended before its pinned file size")
                payload.extend(block)
            if api.read_chunk(handle, 1):
                _refuse(ERROR_PROFILE_INVALID, "T037 profile grew while being read")
            final_fingerprint = api.source_fingerprint(handle)
            after = verify_private_path_security(target, directory=False,
                                                  expected_identity=expected_identity)
            if final_fingerprint != (identity, size, creation, written, links):
                _refuse(ERROR_HOST_MISMATCH, "T037 profile file identity changed during read")
            if after["security_sha256"] != before["security_sha256"]:
                _refuse(ERROR_HOST_MISMATCH, "T037 profile ACL changed during read")
            try:
                document = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WindowsT037Error(ERROR_PROFILE_INVALID, "T037 profile is not bounded UTF-8 JSON") from exc
            digest = hashlib.sha256(payload).hexdigest()
            if isinstance(document, Mapping) and document.get("schema") == PROFILE_SCHEMA:
                profile = parse_profile_document(
                    document, source_sha256=digest, source_identity=expected_identity,
                    security_semantics=before["security_semantics"],
                )
            elif isinstance(document, Mapping) and document.get("schema") == PROFILE_SCHEMA_V2:
                profile = parse_profile_v2_document(
                    document, source_sha256=digest, source_identity=expected_identity,
                    security_semantics=before["security_semantics"],
                )
            elif isinstance(document, Mapping) and document.get("schema") == PROFILE_SCHEMA_V3:
                profile = parse_profile_v3_document(document, source_sha256=digest,
                    source_identity=expected_identity, security_semantics=before["security_semantics"])
            else:
                _refuse(ERROR_PROFILE_INVALID, "T037 profile schema version is unsupported")
        finally:
            if handle is not None:
                api.close(handle)
            for item in reversed(chain):
                api.close(item)
    except WindowsT037Error:
        raise
    except ExecutionContractError as exc:
        raise WindowsT037Error(exc.code, str(exc)) from exc
    except Exception as exc:
        raise WindowsT037Error(ERROR_UNSUPPORTED, "T037 trusted-profile read failed closed") from exc
    actual_fingerprint = (host_fingerprint_reader or windows_host_fingerprint)()
    if not isinstance(actual_fingerprint, str) or not _SHA256.fullmatch(actual_fingerprint):
        _refuse(ERROR_UNSUPPORTED, "current Windows host fingerprint is unavailable")
    if not _constant_digest_equal(profile.host_fingerprint_sha256, actual_fingerprint):
        _refuse(ERROR_HOST_MISMATCH, "T037 profile is bound to a different Windows host")
    return profile


def _constant_digest_equal(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def windows_host_fingerprint(*, resource_registry=None) -> str:
    """Hash the local MachineGuid and computer name; never return raw values."""
    if os.name != "nt":
        _refuse(ERROR_UNSUPPORTED, "Windows machine identity is unavailable on this platform")
    try:
        import winreg
        from ._windows_host_identity_owner import read_machine_guid
        machine_guid, value_type = read_machine_guid(winreg,registry=resource_registry)
        if value_type != winreg.REG_SZ or not isinstance(machine_guid, str) or not machine_guid:
            _refuse(ERROR_UNSUPPORTED, "Windows MachineGuid registry value is malformed")
        computer = platform.node()
        if not isinstance(computer, str) or not computer:
            _refuse(ERROR_UNSUPPORTED, "Windows computer name is unavailable")
        canonical = ("COMSOL-MCP-WINDOWS-T037-V1\0" + computer.casefold() + "\0"
                     + machine_guid.casefold()).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise WindowsT037Error(ERROR_UNSUPPORTED, "Windows host identity could not be read") from exc


@dataclass(frozen=True)
class T037PolicyAssessment:
    state: str
    reasons: tuple[str, ...]
    profile_sha256: str | None
    host_fingerprint_sha256: str | None
    native_observation_sha256: str | None = None
    evidence_classification: str = "SOFTWARE_ONLY"

    @property
    def verified(self) -> bool:
        return self.state == "VERIFIED" and not self.reasons


def assess_t037_readiness(profile: WindowsT037Profile | WindowsT037ProfileV2 | None, *,
                          current_host_fingerprint: str | None,
                          service_account_sid: str | None,
                          interactive_account_sid: str | None,
                          appcontainer_sid: str | None,
                          appcontainer_capabilities: Sequence[str] | None,
                          job_membership_verified: bool,
                          loopback_policy_verified: bool,
                          wfp_policy_verified: bool,
                          service_identity_verified: bool,
                          vendor_compatibility_verified: bool,
                          quota_required: bool,
                          hard_quota_verified: bool) -> T037PolicyAssessment:
    """Pure assessor over adapter observations; caller booleans are not trust.

    This helper supports deterministic negative tests only.  Production must
    call :func:`WindowsT037Observer.assess_profile`, which sources every value
    from Win32 or an installed native policy adapter.  No public request data
    is accepted by that observer.
    """
    reasons: list[str] = []
    if profile is None:
        reasons.append("TRUSTED_PROFILE_NOT_CONFIGURED")
    else:
        if current_host_fingerprint != profile.host_fingerprint_sha256:
            reasons.append("HOST_IDENTITY_MISMATCH_OR_UNAVAILABLE")
        if service_account_sid != profile.service_account_sid:
            reasons.append("SERVICE_IDENTITY_UNVERIFIED")
        if interactive_account_sid != profile.interactive_account_sid:
            reasons.append("INTERACTIVE_IDENTITY_MISMATCH_OR_UNAVAILABLE")
        if profile.service_account_sid.casefold() == profile.interactive_account_sid.casefold():
            reasons.append("SERVICE_IDENTITY_NOT_DISTINCT")
        if appcontainer_sid is None or appcontainer_sid.casefold() != profile.appcontainer_sid.casefold():
            reasons.append("APPCONTAINER_TOKEN_UNVERIFIED")
        expected_capabilities = {value.casefold() for value in profile.capability_sids}
        actual_capabilities = {value.casefold() for value in (appcontainer_capabilities or ())}
        if not expected_capabilities.issubset(actual_capabilities):
            reasons.append("APPCONTAINER_CAPABILITIES_UNVERIFIED")
    if not job_membership_verified:
        reasons.append("EXACT_JOB_MEMBERSHIP_UNVERIFIED")
    if not loopback_policy_verified:
        reasons.append("LOOPBACK_POLICY_UNVERIFIED")
    if not wfp_policy_verified:
        reasons.append("WFP_POLICY_VERIFIER_NOT_IMPLEMENTED")
    if not service_identity_verified:
        reasons.append("SCM_SERVICE_IDENTITY_VERIFIER_NOT_IMPLEMENTED")
    if not vendor_compatibility_verified:
        reasons.append("VENDOR_RUNTIME_COMPATIBILITY_UNVERIFIED")
    if quota_required and not hard_quota_verified:
        reasons.append("REQUIRED_HARD_QUOTA_UNVERIFIED")
    profile_hash = profile.source_sha256 if profile else None
    # Adapter fields are deliberately insufficient to assert OS isolation.
    # A future native verifier must bind current policy state and fresh
    # process-tree observations before it can issue a dispatch proof.
    reasons.append("NATIVE_POLICY_ATTESTATION_NOT_IMPLEMENTED")
    state = "VERIFIED" if not reasons else "UNVERIFIED"
    return T037PolicyAssessment(state, tuple(dict.fromkeys(reasons)), profile_hash,
                                current_host_fingerprint)


class WindowsT037Observer:
    """Read-only native token/process/job/volume/ACL evidence adapter."""

    def __init__(self, *, platform_name: str | None = None, api: Any | None = None):
        self.platform_name = platform_name or sys.platform
        if self.platform_name not in {"win32", "nt", "windows"}:
            _refuse(ERROR_UNSUPPORTED, "native T037 observations require Windows")
        self.api = api if api is not None else _Win32ReadOnlyApi()

    def observe_process(self, pid: int, *, job_handle: Any | None = None) -> dict[str, Any]:
        if type(pid) is not int or pid <= 0:
            _refuse(ERROR_PROFILE_INVALID, "process observation requires a positive integer PID")
        process = self.api.open_process(pid)
        try:
            token = self.api.open_process_token(process)
            try:
                token_rows = self.api.read_token(token)
            finally:
                self.api.close_handle(token)
            creation, exit_time = self.api.process_times(process)
            image_path = self.api.process_image_path(process)
            in_any_job = self.api.is_process_in_job(process, None)
            membership = None
            job = None
            if job_handle is not None:
                membership = self.api.is_process_in_job(process, job_handle)
                job = self.api.job_snapshot(job_handle)
            return {
                "pid": pid,
                "birth_filetime_100ns": creation,
                "exit_filetime_100ns": exit_time,
                "image_path": image_path,
                "token": token_rows,
                "job_membership_any": in_any_job,
                "job_membership_exact": membership,
                "job": job,
                "observation_state": ("OBSERVED" if in_any_job is not None and token_rows.get("state") == "OBSERVED"
                                      else "UNVERIFIED"),
            }
        finally:
            self.api.close_handle(process)

    def observe_volume(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw:
            _refuse(ERROR_PROFILE_INVALID, "volume observation path is malformed")
        result = self.api.volume_snapshot(raw)
        required = {"volume_guid_path", "filesystem", "serial_number", "flags",
                    "total_bytes", "caller_available_bytes", "free_bytes"}
        if not isinstance(result, Mapping) or set(result) != required:
            _refuse(ERROR_UNSUPPORTED, "Win32 volume adapter returned an incomplete result")
        if (result["filesystem"] not in {"NTFS", "ReFS", "FAT", "FAT32", "exFAT"}
                or type(result["total_bytes"]) is not int or type(result["caller_available_bytes"]) is not int
                or type(result["free_bytes"]) is not int
                or not 0 <= result["caller_available_bytes"] <= result["free_bytes"] <= result["total_bytes"]):
            _refuse(ERROR_UNSUPPORTED, "Win32 volume result has unknown filesystem or invalid capacity semantics")
        return {**dict(result), "quota_state": "UNVERIFIED",
                "hard_quota_state": "NOT_OBSERVED_BY_THIS_ADAPTER",
                "observation_state": "OBSERVED_READ_ONLY"}

    def observe_acl(self, path: str | os.PathLike[str], *, directory: bool) -> dict[str, Any]:
        if type(directory) is not bool:
            _refuse(ERROR_PROFILE_INVALID, "ACL observation directory selector must be boolean")
        try:
            from ._host_scoped_provider import verify_private_path_security
            row = verify_private_path_security(path, directory=directory)
        except ExecutionContractError as exc:
            return {"state": "UNVERIFIED", "reason_code": exc.code}
        chain = row.get("path_chain")
        if not isinstance(chain, list) or not chain:
            return {"state": "UNVERIFIED", "reason_code": "ACL_COMPONENT_CHAIN_UNAVAILABLE"}
        return {
            "state": "OBSERVED_READ_ONLY",
            "identity": row.get("identity"),
            "security_sha256": row.get("security_sha256"),
            "security_semantics": row.get("security_semantics"),
            "components": chain,
            "policy_assessment": "UNVERIFIED_UNTIL_PROFILE_ALLOWLIST_COMPARISON",
        }

    def assess_profile(self, profile: WindowsT037Profile | WindowsT037ProfileV2 | None, *,
                       workspace_root: str | None = None) -> T037PolicyAssessment:
        """Assess current evidence without treating configuration as proof.

        The v2 path additionally samples read-only SCM and WFP state around
        the service PID's process token/birth/image.  These observations are
        never a proof: effective WFP arbitration, exact process-tree Job
        binding, ACL grant comparison and vendor/loopback compatibility still
        lack a trusted issuer, so readiness remains UNVERIFIED.
        """
        from ._windows_owned_resources import registry_for
        host_fp = windows_host_fingerprint(resource_registry=registry_for(self.api))
        current = self.api.current_process_token()
        service_sid = current.get("user_sid")
        # The current process SID is not evidence of the interactive account
        # when the daemon is a service (or vice versa). No WTS/SCM identity
        # adapter is installed in this unit.
        interactive_sid = None
        if workspace_root is not None:
            self.observe_volume(workspace_root)
        assessment = assess_t037_readiness(
            profile,
            current_host_fingerprint=host_fp,
            service_account_sid=service_sid,
            interactive_account_sid=interactive_sid,
            appcontainer_sid=None,
            appcontainer_capabilities=(),
            job_membership_verified=False,
            loopback_policy_verified=False,
            wfp_policy_verified=False,
            service_identity_verified=False,
            vendor_compatibility_verified=False,
            quota_required=bool(profile and profile.require_hard_quota),
            hard_quota_verified=False,
        )
        if not isinstance(profile, WindowsT037ProfileV2):
            return T037PolicyAssessment(
                "UNVERIFIED",
                tuple(dict.fromkeys((*assessment.reasons,
                                     "V1_PROFILE_IS_INTENT_ONLY_NO_NATIVE_POLICY_BINDING"))),
                assessment.profile_sha256, assessment.host_fingerprint_sha256,
            )
        try:
            from ._windows_t037_native import WindowsNativePolicyAdapter
            adapter = WindowsNativePolicyAdapter()
            # Keep the actual nested adapter/API alive before its first SDK read.
            from ._windows_owned_resources import registry_for, attach_registry
            if not hasattr(self, "_native_policy_adapters"):
                self._native_policy_adapters = []
            self._native_policy_adapters.append(adapter)
            attach_registry(adapter.api, registry_for(self.api))
            observation = adapter.observe(
                profile, current_host_fingerprint=host_fp, process_observer=self,
            )
        except ExecutionContractError as exc:
            return T037PolicyAssessment(
                "UNVERIFIED", tuple(dict.fromkeys((*assessment.reasons, exc.code))),
                assessment.profile_sha256, assessment.host_fingerprint_sha256,
            )
        return T037PolicyAssessment(
            "UNVERIFIED", tuple(dict.fromkeys((*assessment.reasons, *observation.reasons))),
            assessment.profile_sha256, assessment.host_fingerprint_sha256,
            observation.wfp_snapshot_sha256, observation.evidence_classification,
        )


class _Win32ReadOnlyApi:
    """Strictly typed Win32 token/process/job/volume queries; read-only only."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    TOKEN_GROUPS = 2
    TOKEN_INTEGRITY_LEVEL = 25
    TOKEN_CAPABILITIES = 30
    TOKEN_APPCONTAINER_SID = 31
    TOKEN_IS_APPCONTAINER = 29
    JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        if os.name != "nt":
            _refuse(ERROR_UNSUPPORTED, "Win32 API binding requested on a non-Windows host")
        import ctypes
        from ctypes import wintypes
        self.C = ctypes
        self.W = wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self._bind()

    def _bind(self) -> None:
        C, W = self.C, self.W
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.GetCurrentProcess.argtypes = []
        self.k.GetCurrentProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.GetProcessTimes.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
        self.k.GetProcessTimes.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)]
        self.k.IsProcessInJob.restype = W.BOOL
        self.k.QueryInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p,
                                                     W.DWORD, C.POINTER(W.DWORD)]
        self.k.QueryInformationJobObject.restype = W.BOOL
        self.k.GetVolumePathNameW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD]
        self.k.GetVolumePathNameW.restype = W.BOOL
        self.k.GetVolumeNameForVolumeMountPointW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD]
        self.k.GetVolumeNameForVolumeMountPointW.restype = W.BOOL
        self.k.GetVolumeInformationW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD,
            C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.POINTER(W.DWORD), W.LPWSTR, W.DWORD]
        self.k.GetVolumeInformationW.restype = W.BOOL
        self.k.GetDiskFreeSpaceExW.argtypes = [W.LPCWSTR, C.POINTER(C.c_ulonglong),
            C.POINTER(C.c_ulonglong), C.POINTER(C.c_ulonglong)]
        self.k.GetDiskFreeSpaceExW.restype = W.BOOL
        self.a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        self.a.OpenProcessToken.restype = W.BOOL
        self.a.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p,
            W.DWORD, C.POINTER(W.DWORD)]
        self.a.GetTokenInformation.restype = W.BOOL
        self.a.IsValidSid.argtypes = [C.c_void_p]
        self.a.IsValidSid.restype = W.BOOL
        self.a.GetLengthSid.argtypes = [C.c_void_p]
        self.a.GetLengthSid.restype = W.DWORD
        self.a.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
        self.a.ConvertSidToStringSidW.restype = W.BOOL
        self.a.GetSidSubAuthorityCount.argtypes = [C.c_void_p]
        self.a.GetSidSubAuthorityCount.restype = C.POINTER(W.BYTE)
        self.a.GetSidSubAuthority.argtypes = [C.c_void_p, W.DWORD]
        self.a.GetSidSubAuthority.restype = C.POINTER(W.DWORD)
        self.k.LocalFree.argtypes = [C.c_void_p]
        self.k.LocalFree.restype = C.c_void_p

    def _check(self, ok: Any, field: str) -> None:
        if not ok:
            error = self.C.get_last_error()
            raise WindowsT037Error(ERROR_UNSUPPORTED, f"Win32 {field} failed with error {error}")

    def open_process(self, pid: int):
        handle = self.k.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            self._check(False, "OpenProcess")
        from ._windows_owned_resources import own
        own(self,handle,self.k.CloseHandle,mode="BOOL",label="observed_process")
        return handle

    def _close_handle_native(self, handle: Any) -> None:
        if handle and not self.k.CloseHandle(handle):
            self._check(False, "CloseHandle")

    def close_handle(self, handle: Any) -> None:
        from ._windows_owned_resources import registry_for
        if handle:
            registry=registry_for(self);entry=registry.find(self,handle)
            if entry is not None:return entry.release_owned(registry.owner)
        return self._close_handle_native(handle)

    def open_process_token(self, process: Any):
        token = self.W.HANDLE()
        self._check(self.a.OpenProcessToken(process, self.TOKEN_QUERY, self.C.byref(token)), "OpenProcessToken")
        if not token.value:
            _refuse(ERROR_UNSUPPORTED, "OpenProcessToken returned a null token handle")
        from ._windows_owned_resources import own
        own(self,token,self.k.CloseHandle,mode="BOOL",label="observed_process_token")
        return token

    def _token_buffer(self, token: Any, information_class: int, *, required: bool = True):
        C, W = self.C, self.W
        needed = W.DWORD(0)
        self.a.GetTokenInformation(token, information_class, None, 0, C.byref(needed))
        error = C.get_last_error()
        if error != 122 or not 0 < needed.value <= MAX_ACL_BYTES:
            if required:
                _refuse(ERROR_UNSUPPORTED, f"GetTokenInformation({information_class}) size probe was not a bounded ERROR_INSUFFICIENT_BUFFER")
            return None
        buffer = C.create_string_buffer(needed.value)
        returned = W.DWORD(0)
        self._check(self.a.GetTokenInformation(token, information_class, buffer, needed.value,
                                                C.byref(returned)), f"GetTokenInformation({information_class})")
        if returned.value != needed.value or returned.value > MAX_ACL_BYTES:
            _refuse(ERROR_UNSUPPORTED, "GetTokenInformation returned an inconsistent buffer length")
        return buffer

    def _sid_text(self, pointer: Any) -> str:
        C, W = self.C, self.W
        if not pointer or not self.a.IsValidSid(pointer):
            _refuse(ERROR_UNSUPPORTED, "Win32 returned an invalid SID pointer")
        length = int(self.a.GetLengthSid(pointer))
        if not 8 <= length <= 1024:
            _refuse(ERROR_UNSUPPORTED, "Win32 returned a SID with an unsupported size")
        text = W.LPWSTR()
        self._check(self.a.ConvertSidToStringSidW(pointer, C.byref(text)), "ConvertSidToStringSidW")
        from ._windows_owned_resources import own
        resource=own(self,C.cast(text,C.c_void_p),self.k.LocalFree,mode="LOCALFREE",label="sid_string",keepalive=(text,))
        try:
            value = text.value
        finally:
            resource.release_owned(resource.registry.owner)
        if not is_canonical_sid(value):
            _refuse(ERROR_UNSUPPORTED, "Win32 SID could not be converted to canonical text")
        return value.upper()

    def _sid_and_attributes(self, address: int, buffer: Any) -> tuple[str, int]:
        C = self.C
        pointer_size = C.sizeof(C.c_void_p)
        if address < C.addressof(buffer) or address + pointer_size + 4 > C.addressof(buffer) + C.sizeof(buffer):
            _refuse(ERROR_UNSUPPORTED, "token SID entry extends outside its Win32 buffer")
        sid_pointer = C.c_void_p.from_address(address).value
        attributes = C.c_uint32.from_address(address + pointer_size).value
        return self._sid_text(C.c_void_p(sid_pointer)), int(attributes)

    def read_token(self, token: Any) -> dict[str, Any]:
        C, W = self.C, self.W
        user = self._token_buffer(token, self.TOKEN_USER)
        user_sid, _user_attributes = self._sid_and_attributes(C.addressof(user), user)
        group_buffer = self._token_buffer(token, self.TOKEN_GROUPS)
        group_rows = self._parse_token_group_rows(group_buffer, maximum=4096)
        group_sids = sorted({row["sid"] for row in group_rows}, key=str.casefold)
        logon_sids = sorted({row["sid"] for row in group_rows
                             if row["attributes"] & 0xC0000000 == 0xC0000000}, key=str.casefold)
        app_value = self._token_buffer(token, self.TOKEN_IS_APPCONTAINER)
        if C.sizeof(app_value) != 4:
            _refuse(ERROR_UNSUPPORTED, "TokenIsAppContainer ABI size differs from DWORD")
        app_raw = C.c_uint32.from_buffer(app_value).value
        if app_raw not in (0, 1):
            _refuse(ERROR_UNSUPPORTED, "TokenIsAppContainer returned an invalid boolean")
        app_info = self._token_buffer(token, self.TOKEN_APPCONTAINER_SID) if app_raw else None
        app_sid = None
        if app_info is not None:
            pointer = C.c_void_p.from_buffer(app_info).value
            if app_raw and not pointer:
                _refuse(ERROR_UNSUPPORTED, "AppContainer token has no AppContainer SID")
            if pointer:
                app_sid = self._sid_text(C.c_void_p(pointer))
        groups_buffer = (self._token_buffer(token, self.TOKEN_CAPABILITIES, required=True)
                         if app_raw else None)
        capability_sids = self._parse_token_groups(groups_buffer) if groups_buffer is not None else []
        integrity = self._token_buffer(token, self.TOKEN_INTEGRITY_LEVEL)
        integrity_sid, integrity_attributes = self._sid_and_attributes(C.addressof(integrity), integrity)
        return {
            "state": "OBSERVED",
            "user_sid": user_sid,
            "group_sids": group_sids,
            "logon_sids": logon_sids,
            "is_appcontainer": bool(app_raw),
            "appcontainer_sid": app_sid,
            "capability_sids": capability_sids,
            "integrity_sid": integrity_sid,
            "integrity_attributes": integrity_attributes,
        }

    def _parse_token_groups(self, buffer: Any) -> list[str]:
        rows = self._parse_token_group_rows(buffer, maximum=MAX_SIDS)
        output = []
        for row in rows:
            if not row["attributes"] & 0x00000004:  # SE_GROUP_ENABLED
                _refuse(ERROR_UNSUPPORTED, "AppContainer capability SID is not enabled")
            output.append(row["sid"])
        if len(set(item.casefold() for item in output)) != len(output):
            _refuse(ERROR_UNSUPPORTED, "TokenCapabilities contains duplicate SIDs")
        return sorted(output, key=str.casefold)

    def _parse_token_group_rows(self, buffer: Any, *, maximum: int) -> list[dict[str, Any]]:
        C = self.C
        count = C.c_uint32.from_buffer(buffer).value
        if count > maximum:
            _refuse(ERROR_UNSUPPORTED, "token group count exceeds the bounded policy")
        # TOKEN_GROUPS has a DWORD count followed by an array whose element
        # contains a pointer, so x64 inserts pointer-alignment padding.
        pointer_size = C.sizeof(C.c_void_p)
        first = C.addressof(buffer) + ((C.sizeof(C.c_uint32) + pointer_size - 1) & ~(pointer_size - 1))
        pointer_size = C.sizeof(C.c_void_p)
        stride = (pointer_size + C.sizeof(C.c_uint32) + pointer_size - 1) & ~(pointer_size - 1)
        if first + count * stride > C.addressof(buffer) + C.sizeof(buffer):
            _refuse(ERROR_UNSUPPORTED, "token group count exceeds its returned buffer")
        output = []
        for index in range(count):
            sid, attributes = self._sid_and_attributes(first + index * stride, buffer)
            output.append({"sid": sid, "attributes": attributes})
        if len({item["sid"].casefold() for item in output}) != len(output):
            _refuse(ERROR_UNSUPPORTED, "token group list contains duplicate SIDs")
        return output

    def process_times(self, process: Any) -> tuple[int, int | None]:
        C = self.C
        class FILETIME(C.Structure):
            _fields_ = [("dwLowDateTime", C.c_uint32), ("dwHighDateTime", C.c_uint32)]
        creation, exit_time, kernel_time, user_time = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        self._check(self.k.GetProcessTimes(process, C.byref(creation), C.byref(exit_time),
                                           C.byref(kernel_time), C.byref(user_time)), "GetProcessTimes")
        def value(item):
            return (int(item.dwHighDateTime) << 32) | int(item.dwLowDateTime)
        start = value(creation)
        ended = value(exit_time)
        if start <= 0:
            _refuse(ERROR_UNSUPPORTED, "GetProcessTimes returned no process birth FILETIME")
        return start, ended or None

    def process_image_path(self, process: Any) -> str:
        C, W = self.C, self.W
        capacity = W.DWORD(32768)
        buffer = C.create_unicode_buffer(capacity.value)
        self._check(self.k.QueryFullProcessImageNameW(process, 0, buffer, C.byref(capacity)), "QueryFullProcessImageNameW")
        result = buffer.value
        if not result or len(result) >= 32768 or "\x00" in result:
            _refuse(ERROR_UNSUPPORTED, "process image path is absent or exceeds the Win32 bound")
        return result

    def is_process_in_job(self, process: Any, job: Any | None) -> bool:
        observed = self.W.BOOL()
        self._check(self.k.IsProcessInJob(process, job, self.C.byref(observed)), "IsProcessInJob")
        if observed.value not in (0, 1):
            _refuse(ERROR_UNSUPPORTED, "IsProcessInJob returned an invalid BOOL")
        return bool(observed.value)

    def job_snapshot(self, job: Any) -> dict[str, Any]:
        C = self.C
        class BASIC_LIMIT(C.Structure):
            _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                        ("LimitFlags", C.c_uint32), ("MinimumWorkingSetSize", C.c_size_t),
                        ("MaximumWorkingSetSize", C.c_size_t), ("ActiveProcessLimit", C.c_uint32),
                        ("Affinity", C.c_size_t), ("PriorityClass", C.c_uint32),
                        ("SchedulingClass", C.c_uint32)]
        class IO_COUNTERS(C.Structure):
            _fields_ = [(name, C.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class EXTENDED_LIMIT(C.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", C.c_size_t), ("JobMemoryLimit", C.c_size_t),
                        ("PeakProcessMemoryUsed", C.c_size_t), ("PeakJobMemoryUsed", C.c_size_t)]
        limits = EXTENDED_LIMIT()
        returned = self.W.DWORD(0)
        self._check(self.k.QueryInformationJobObject(job, self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            C.byref(limits), C.sizeof(limits), C.byref(returned)), "QueryInformationJobObject(ExtendedLimit)")
        if returned.value != C.sizeof(limits):
            _refuse(ERROR_UNSUPPORTED, "Job extended-limit structure size mismatch")
        flags = int(limits.BasicLimitInformation.LimitFlags)
        known_flags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000020 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800 | 0x00001000 | 0x00002000 | 0x00004000 | 0x00008000
        if flags & ~known_flags:
            _refuse(ERROR_UNSUPPORTED, "Job object contains unknown limit flags")
        pids = self._job_process_ids(job)
        process_memory = int(limits.ProcessMemoryLimit) if flags & 0x00000100 else None
        job_memory = int(limits.JobMemoryLimit) if flags & 0x00000200 else None
        return {
            "state": "OBSERVED",
            "limit_flags": flags,
            "kill_on_job_close": bool(flags & 0x00002000),
            "breakaway_allowed": bool(flags & 0x00000800),
            "silent_breakaway_allowed": bool(flags & 0x00001000),
            "active_process_limit": int(limits.BasicLimitInformation.ActiveProcessLimit)
                if flags & 0x00000008 else None,
            "process_memory_limit_bytes": process_memory,
            "job_memory_limit_bytes": job_memory,
            "memory_limit_semantics": "COMMITTED_MEMORY_LIMITS_ONLY",
            "process_ids": pids,
            "process_count": len(pids),
        }

    def _job_process_ids(self, job: Any) -> list[int]:
        C, W = self.C, self.W
        size = 8
        while size <= 1024 * 1024:
            buffer = C.create_string_buffer(size)
            returned = W.DWORD(0)
            ok = self.k.QueryInformationJobObject(job, self.JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                buffer, size, C.byref(returned))
            if ok:
                if returned.value < 8 or returned.value > size:
                    _refuse(ERROR_UNSUPPORTED, "Job PID list returned an invalid byte count")
                count = C.c_uint32.from_buffer(buffer, 0).value
                listed = C.c_uint32.from_buffer(buffer, 4).value
                if count > MAX_JOB_PIDS or listed != count:
                    _refuse(ERROR_UNSUPPORTED, "Job PID list count exceeds the bounded policy")
                stride = C.sizeof(C.c_size_t)
                if 8 + listed * stride > returned.value:
                    _refuse(ERROR_UNSUPPORTED, "Job PID list extends beyond its returned buffer")
                ids = [int(C.c_size_t.from_buffer(buffer, 8 + index * stride).value)
                       for index in range(listed)]
                if len(set(ids)) != len(ids) or any(pid <= 0 for pid in ids):
                    _refuse(ERROR_UNSUPPORTED, "Job PID list contains invalid or duplicate process IDs")
                return ids
            error = C.get_last_error()
            # QueryInformationJobObject may report either insufficient buffer
            # or more-data when the process list grows between sizing and read.
            # Both require a bounded retry; all other errors remain fatal.
            if error not in {122, 234}:
                self._check(False, "QueryInformationJobObject(ProcessIdList)")
            if returned.value > size:
                size = int(returned.value)
            else:
                size *= 2
        _refuse(ERROR_UNSUPPORTED, "Job PID list exceeds the bounded query size")

    def volume_snapshot(self, path: str) -> dict[str, Any]:
        C, W = self.C, self.W
        volume_mount = C.create_unicode_buffer(32768)
        self._check(self.k.GetVolumePathNameW(path, volume_mount, len(volume_mount)), "GetVolumePathNameW")
        volume_guid = C.create_unicode_buffer(128)
        self._check(self.k.GetVolumeNameForVolumeMountPointW(volume_mount.value, volume_guid,
                                                              len(volume_guid)), "GetVolumeNameForVolumeMountPointW")
        filesystem = C.create_unicode_buffer(64)
        serial, max_component, flags = W.DWORD(), W.DWORD(), W.DWORD()
        self._check(self.k.GetVolumeInformationW(volume_mount.value, None, 0,
            C.byref(serial), C.byref(max_component), C.byref(flags), filesystem, len(filesystem)),
            "GetVolumeInformationW")
        caller_available, total, free = C.c_ulonglong(), C.c_ulonglong(), C.c_ulonglong()
        self._check(self.k.GetDiskFreeSpaceExW(path, C.byref(caller_available), C.byref(total), C.byref(free)),
                    "GetDiskFreeSpaceExW")
        return {
            "volume_guid_path": volume_guid.value,
            "filesystem": filesystem.value.upper(),
            "serial_number": int(serial.value),
            "flags": int(flags.value),
            "total_bytes": int(total.value),
            "caller_available_bytes": int(caller_available.value),
            "free_bytes": int(free.value),
        }

    def current_process_token(self) -> dict[str, Any]:
        process = self.k.GetCurrentProcess()
        token = self.open_process_token(process)
        try:
            return self.read_token(token)
        finally:
            self.close_handle(token)


# ---------------------------------------------------------------------------
# Suspended, same-job launch protocol.  It is deliberately unreachable until
# a trusted native policy verifier exists and produces fresh proof.
# ---------------------------------------------------------------------------


class T037SessionProcessFactory:
    """Popen-compatible callable that refuses fallback and shares one Job.

    This release has no native policy verifier or proof issuer, so the
    process-factory preflight always rejects. No mutable proof field or
    caller-provided receipt can unlock process creation. The call protocol
    remains explicit so a future reviewed verifier can add an issuer without
    a `Popen` fallback.
    """

    def __init__(self, profile: WindowsT037Profile | WindowsT037ProfileV2, session_id: str, *,
                 api: Any | None = None):
        if not isinstance(profile, (WindowsT037Profile, WindowsT037ProfileV2)):
            _refuse(ERROR_PROFILE_INVALID, "managed process factory requires a trusted parsed T037 profile")
        if not isinstance(session_id, str) or not _PROFILE_ID.fullmatch(session_id):
            _refuse(ERROR_PROFILE_INVALID, "managed process factory requires a canonical session ID")
        self.profile = profile
        self.session_id = session_id
        self._api = api
        self._job = None
        self._closed = False
        self._prebirth_observers = []
        self._job_key = hashlib.sha256((profile.source_sha256 + "\0" + session_id).encode()).hexdigest()

    def __call__(self, command: Sequence[str], **options: Any):
        self.preflight()
        if os.name != "nt":
            _refuse(ERROR_UNSUPPORTED, "T037 process launch requires Windows")
        api = self._api if self._api is not None else _Win32LaunchApi()
        self._api = api
        role = self._infer_role(command)
        if self._job is None:
            self._job = api.create_job(profile=self.profile)
        return api.create_suspended_appcontainer_child(
            command, options, profile=self.profile, job=self._job, role=role,
            session_factory=self,
        )

    def preflight(self) -> None:
        """Refuse before directory/file/child side effects until native proof exists."""
        if self._closed:
            _refuse(ERROR_POLICY_UNVERIFIED, "T037 session process factory is closed")
        if os.name == "nt":
            from ._windows_t037_resume import refuse_unverified_prebirth
            refuse_unverified_prebirth(self.profile, owner=self)
        _refuse(ERROR_POLICY_UNVERIFIED, "T037 prebirth native policy admission and child-proof issuer remain unverified")

    def _infer_role(self, command: Sequence[str]) -> str:
        if not isinstance(command, Sequence) or isinstance(command, (str, bytes)) or not command:
            _refuse(ERROR_PROFILE_INVALID, "T037 child command must be a nonempty argument array")
        executable = command[0]
        if not isinstance(executable, str) or not executable or "\x00" in executable:
            _refuse(ERROR_PROFILE_INVALID, "T037 executable argument is malformed")
        api = self._api if self._api is not None else (_Win32LaunchApi() if os.name == "nt" else None)
        digest = api.hash_image(executable) if api is not None else None
        if digest == self.profile.server_image_sha256:
            return "server"
        if digest == self.profile.worker_image_sha256:
            return "worker"
        _refuse(ERROR_POLICY_UNVERIFIED, "T037 child image hash is not in the trusted Server/Worker profile")

    @property
    def job_key(self) -> str:
        return self._job_key

    def close(self) -> None:
        from ._windows_owned_resources import registry_for
        if registry_for(self).unresolved():
            _refuse("NATIVE_RESOURCE_RELEASE_UNKNOWN", "factory-owned prebirth native observations remain unresolved")
        if self._api is not None:
            from ._windows_owned_resources import registry_for
            if any(not (entry.adapter is self._api and entry.value == self._job
                        and entry.label == "owned_session_job" and entry.state == "ACQUIRED")
                   for entry in registry_for(self._api).unresolved()):
                _refuse("NATIVE_RESOURCE_RELEASE_UNKNOWN", "actual native acquisition registry has unresolved ancillary ownership")
            from ._windows_t037_child import _PinnedParent, _PARENT_SEAL
            retained_cleanup = getattr(self._api, "_retained_launch_cleanup", [])
            def parent_release_confirmed(entry):
                parent = getattr(entry[1], "__self__", None)
                return (entry[0] == "native_parent" and type(parent) is _PinnedParent
                        and parent._seal is _PARENT_SEAL and parent.launch_api is self._api
                        and parent.closed is True)
            def token_release_confirmed(entry):
                token = getattr(entry[1], "__self__", None)
                return (entry[0] == "child_token" and type(token) is _RetainedChildToken
                        and token._seal is _NATIVE_CHILD_TOKEN_SEAL and token._api is self._api
                        and token.closed is True)
            retained_cleanup[:] = [entry for entry in retained_cleanup
                if not parent_release_confirmed(entry) and not token_release_confirmed(entry)]
            if retained_cleanup:
                _refuse("NATIVE_LAUNCH_RELEASE_UNKNOWN", "actual ancillary launch cleanup remains owned and unconfirmed")
            if any(not parent.closed for parent in getattr(self._api, "_retained_parent_bindings", {}).values()):
                _refuse("NATIVE_PARENT_RELEASE_UNKNOWN", "actual pinned parent HANDLE remains owned after release failure")
            if getattr(self._api, "_unclassified_create_handles", None):
                _refuse("NATIVE_CHILD_CREATION_UNKNOWN", "unclassified native creation handles block Job close")
            for process in getattr(self._api, "_retained_children", {}).values():
                if type(process.poll()) is not int:
                    _refuse("NATIVE_CHILD_CLEANUP_UNKNOWN", "session Job/owner remains retained while child exit is unknown")
        if self._job is not None and self._api is not None:
            self._api.close_job(self._job)
            self._job = None
        self._closed = True


class _Win32ChildProcess:
    """Popen lifecycle surface backed by the retained exact process handle."""

    def __init__(self, api: Any, handle: Any, pid: int, args: Sequence[str]):
        self._api, self._handle = api, handle
        self.pid = int(pid)
        self.args = list(args)
        self.returncode: int | None = None
        self._closed = False

    def poll(self) -> int | None:
        if self.returncode is None:
            self.returncode = self._api.poll_process(self._handle)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = self._api.wait_process(self._handle, timeout, self.args)
        return self.returncode

    def terminate(self) -> None:
        if self.poll() is None:
            self._api.terminate_process(self._handle, 1)

    def kill(self) -> None:
        self.terminate()

    def close(self) -> None:
        if not self._closed:
            self._api.close_handle(self._handle)
            self._closed = True
            owners = getattr(self._api, "_g2_session_launch_owners", None)
            if isinstance(owners, dict):
                owners.pop(id(self), None)

    def __del__(self):  # pragma: no cover - normal lifecycle owns this handle
        try:
            self.close()
        except Exception:
            pass


_G2_SESSION_LAUNCH_OWNER_SEAL = object()


class _Win32G2SessionLaunchOwner:
    """Sealed receipt for a successful exact-handle T037 session launch."""

    __slots__ = ("_seal", "process", "api", "process_handle", "pid",
                 "birth_filetime_100ns", "argv", "argv_sha256", "job_handle",
                 "profile", "profile_sha256", "role", "image_path",
                 "image_sha256", "session_factory")

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError("sealed T037 session launch provenance is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, *, api, process, handle, birth_filetime_100ns, argv,
                 job_handle, profile, role, image_path, image_sha256,
                 session_factory, seal):
        if (seal is not _G2_SESSION_LAUNCH_OWNER_SEAL
                or type(api) is not _Win32LaunchApi
                or type(process) is not _Win32ChildProcess
                or process._api is not api or process._handle is not handle
                or process.pid <= 1 or type(birth_filetime_100ns) is not int
                or birth_filetime_100ns <= 0
                or session_factory is None
                or type(session_factory) is not T037SessionProcessFactory
                or session_factory._api is not api or session_factory._job is not job_handle
                or session_factory.profile is not profile):
            _refuse(ERROR_POLICY_UNVERIFIED, "G2 session provenance requires the successful original T037 HANDLE launch")
        if (type(argv) not in (tuple, list) or not argv
                or any(type(value) is not str or "\x00" in value for value in argv)
                or role not in {"server", "worker"}
                or not isinstance(image_path, str) or not image_path
                or not isinstance(image_sha256, str) or len(image_sha256) != 64):
            _refuse(ERROR_POLICY_UNVERIFIED, "G2 session provenance has malformed native launch identity")
        self._seal = seal
        self.process = process
        self.api = api
        self.process_handle = handle
        self.pid = process.pid
        self.birth_filetime_100ns = birth_filetime_100ns
        self.argv = tuple(argv)
        self.argv_sha256 = hashlib.sha256(json.dumps(
            list(argv), ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self.job_handle = job_handle
        self.profile = profile
        self.profile_sha256 = profile.sha256
        self.role = role
        self.image_path = image_path
        self.image_sha256 = image_sha256
        self.session_factory = session_factory


def _native_g2_session_launch_owner(process):
    """Return only the API-registered owner minted at the successful launch return."""
    if type(process) is not _Win32ChildProcess:
        return None
    api = getattr(process, "_api", None)
    owners = getattr(api, "_g2_session_launch_owners", None)
    owner = owners.get(id(process)) if type(owners) is dict else None
    if (type(owner) is not _Win32G2SessionLaunchOwner
            or owner._seal is not _G2_SESSION_LAUNCH_OWNER_SEAL
            or owner.process is not process or owner.api is not api
            or owner.process_handle is not process._handle or owner.pid != process.pid
            or process._closed or owner.profile_sha256 != owner.profile.sha256
            or type(owner.session_factory) is not T037SessionProcessFactory
            or owner.session_factory._api is not api
            or owner.session_factory._job is not owner.job_handle
            or owner.session_factory.profile is not owner.profile
            or owner.session_factory._closed):
        return None
    return owner


_NATIVE_SUSPENDED_CHILD_SEAL = object()


class NativeSuspendedChildLaunchError(WindowsT037Error):
    """Non-wire ownership handoff for an actual retained CreateProcess child."""
    def __init__(self, process, cause, cleanup_error, *, seal):
        if (seal is not _NATIVE_SUSPENDED_CHILD_SEAL or type(process) is not _RetainedSuspendedChild
                or getattr(process, "_seal", None) is not _NATIVE_SUSPENDED_CHILD_SEAL):
            _refuse(ERROR_POLICY_UNVERIFIED, "native launch recovery requires an actual retained child capability")
        super().__init__("NATIVE_CHILD_CLEANUP_UNKNOWN", "suspended child cleanup is unconfirmed; original owner retained",
                         details={"pid": process.pid, "birth_filetime_100ns": process.birth_filetime_100ns,
                                  "effect_possible": True, "never_resumed": process.never_resumed,
                                  "cause_type": type(cause).__name__, "cleanup_error_type": type(cleanup_error).__name__})
        self._process = process
        self._seal = seal

    def transfer_to(self, owner):
        if self._seal is not _NATIVE_SUSPENDED_CHILD_SEAL:
            _refuse(ERROR_POLICY_UNVERIFIED, "launch recovery capability is invalid")
        self._process.transfer_owner(self._process._api, owner)
        return self._process


_NATIVE_CHILD_TOKEN_SEAL = object()

class _RetainedChildToken:
    """Actual token owner bound to the original native child, never wire-minted."""
    def __init__(self, api, handle, process, *, seal):
        if (seal is not _NATIVE_CHILD_TOKEN_SEAL or type(process) is not _RetainedSuspendedChild
                or process._seal is not _NATIVE_SUSPENDED_CHILD_SEAL or process._api is not api):
            _refuse("NATIVE_CHILD_TOKEN_OWNER_UNKNOWN", "token requires actual native child provenance")
        self._api, self._handle, self._process = api, handle, process
        self._seal = seal
        self.closed = False

    def release_owned(self, owner):
        if owner is not self._api or self._seal is not _NATIVE_CHILD_TOKEN_SEAL:
            _refuse("NATIVE_CHILD_TOKEN_OWNER_UNKNOWN", "only the original native API owner may release this token")
        if not self.closed:
            self._api.close_handle(self._handle)
            self.closed = True
        getattr(self._api, "_retained_child_tokens", {}).pop(id(self), None)


class _RetainedSuspendedChild(_Win32ChildProcess):
    """Unique actual HANDLE owner; metadata cannot enable simplified cleanup."""
    def __init__(self, api, handle, pid, args, thread_handle, job, *, seal, creation_confirmed=True):
        if seal is not _NATIVE_SUSPENDED_CHILD_SEAL:
            _refuse(ERROR_POLICY_UNVERIFIED, "retained child requires native creation provenance")
        self._api, self._handle = api, handle
        self.pid = pid if type(pid) is int and pid > 0 else None
        self.args = list(args)
        self.returncode = None
        self._closed = False
        self._creation_confirmed = creation_confirmed is True
        self._seal = seal
        self._thread_handle = thread_handle
        self._job_handle = job
        self._owner = api
        self._resume_attempted = False
        self._pending_resource_release_jobs = set()
        self._terminal_resource_release_observations = {}
        self._ownership_lock = __import__('threading').RLock()
        try:
            if not self._creation_confirmed:
                raise ValueError("creation response was not confirmed")
            birth, exited = api.process_times(handle)
            self.birth_filetime_100ns = birth if type(birth) is int and birth > 0 else None
        except Exception:
            self.birth_filetime_100ns = None

    @property
    def never_resumed(self):
        return self._seal is _NATIVE_SUSPENDED_CHILD_SEAL and self._creation_confirmed and not self._resume_attempted

    def poll(self):
        if not self._creation_confirmed:
            _refuse("NATIVE_CHILD_CREATION_UNKNOWN", "unconfirmed creation handle is not terminal/ownership evidence")
        return super().poll()

    def transfer_owner(self, expected, owner):
        with self._ownership_lock:
            if self._owner is not expected or owner is None or owner is expected:
                _refuse("NATIVE_CHILD_OWNER_TRANSFER_REFUSED", "actual child has already transferred its owner")
            self._owner = owner

    def note_resume_attempt(self):
        with self._ownership_lock:
            self._resume_attempted = True

    def ownership_observation(self):
        if self._seal is not _NATIVE_SUSPENDED_CHILD_SEAL:
            _refuse(ERROR_POLICY_UNVERIFIED, "native ownership provenance is missing")
        return {"schema": "comsol-mcp.retained-native-child/1", "pid": self.pid,
                "birth_filetime_100ns": self.birth_filetime_100ns,
                "creation_response_confirmed": self._creation_confirmed,
                "resume_attempted": self._resume_attempted, "effect_possible": True,
                "never_resumed": self.never_resumed,
                "terminal_state": "EXIT_CONFIRMED" if type(self.returncode) is int else "UNKNOWN"}

    def cleanup_owned(self, owner, timeout=5):
        with self._ownership_lock:
            if self._owner is not owner or not self.never_resumed:
                _refuse("NATIVE_CHILD_OWNER_REQUIRED", "simplified cleanup requires the exact never-resumed owner")
            if self._closed:
                return self.returncode
            if self.poll() is None:
                self.terminate()
                self.wait(timeout=timeout)
            if type(self.poll()) is not int:
                _refuse("NATIVE_CHILD_CLEANUP_UNKNOWN", "actual child exit has not been confirmed")
            self.close_owned(owner)
            return self.returncode

    def close_owned(self, owner):
        with self._ownership_lock:
            if self._owner is not owner:
                _refuse("NATIVE_CHILD_OWNER_REQUIRED", "only the actual transferred owner may close the child")
            if self._closed:
                return
            if type(self.poll()) is not int:
                _refuse("NATIVE_CHILD_CLEANUP_UNKNOWN", "process handle cannot be closed before confirmed exit")
            if self._thread_handle:
                self._api.close_handle(self._thread_handle)
                self._thread_handle = None
            super().close()

    def close(self):
        _refuse("NATIVE_CHILD_OWNER_REQUIRED", "retained child close requires the explicit unique owner")

    def __del__(self):
        pass # GC is never ownership/recovery evidence and cannot release this handle.


def is_native_suspended_child(process):
    return (type(process) is _RetainedSuspendedChild
            and getattr(process, "_seal", None) is _NATIVE_SUSPENDED_CHILD_SEAL
            and process.never_resumed)


def is_native_retained_child(process):
    return type(process) is _RetainedSuspendedChild and getattr(process, "_seal", None) is _NATIVE_SUSPENDED_CHILD_SEAL


class _Win32LaunchApi(_Win32ReadOnlyApi):
    """Suspended CreateProcessAsUser + AppContainer + exact shared Job.

    This concrete path does no profile/account/ACL/WFP/quota mutation.  Current
    dispatch cannot mint its required native launch proof, so it remains
    unreachable until the independent policy adapter and compatibility checks
    exist.
    """

    CREATE_SUSPENDED = 0x00000004
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    STARTF_USESTDHANDLES = 0x00000100
    DUPLICATE_SAME_ACCESS = 0x00000002
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    INFINITE = 0xFFFFFFFF
    MAX_IMAGE_BYTES = 2 * 1024 * 1024 * 1024

    def __init__(self):
        super().__init__()
        C, W = self.C, self.W
        self.k.GetCurrentProcess.argtypes = []
        self.k.GetCurrentProcess.restype = W.HANDLE
        self.k.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
        self.k.CreateJobObjectW.restype = W.HANDLE
        self.k.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        self.k.SetInformationJobObject.restype = W.BOOL
        self.k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.k.AssignProcessToJobObject.restype = W.BOOL
        self.k.ResumeThread.argtypes = [W.HANDLE]
        self.k.ResumeThread.restype = W.DWORD
        self.k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.k.WaitForSingleObject.restype = W.DWORD
        self.k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        self.k.GetExitCodeProcess.restype = W.BOOL
        self.k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        self.k.TerminateProcess.restype = W.BOOL
        self.k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                       W.DWORD, W.DWORD, W.HANDLE]
        self.k.CreateFileW.restype = W.HANDLE
        self.k.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE,
            C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
        self.k.DuplicateHandle.restype = W.BOOL
        self.k.InitializeProcThreadAttributeList.argtypes = [C.c_void_p, W.DWORD,
            W.DWORD, C.POINTER(C.c_size_t)]
        self.k.InitializeProcThreadAttributeList.restype = W.BOOL
        self.k.UpdateProcThreadAttribute.argtypes = [C.c_void_p, W.DWORD,
            C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]
        self.k.UpdateProcThreadAttribute.restype = W.BOOL
        self.k.DeleteProcThreadAttributeList.argtypes = [C.c_void_p]
        self.k.DeleteProcThreadAttributeList.restype = None
        self.a.DuplicateTokenEx.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, C.c_int,
                                             C.c_int, C.POINTER(W.HANDLE)]
        self.a.DuplicateTokenEx.restype = W.BOOL
        self.a.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(C.c_void_p)]
        self.a.ConvertStringSidToSidW.restype = W.BOOL
        self.a.CreateProcessAsUserW.argtypes = [W.HANDLE, W.LPCWSTR, W.LPWSTR,
            C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR,
            C.c_void_p, C.c_void_p]
        self.a.CreateProcessAsUserW.restype = W.BOOL

    def create_job(self, *, profile=None):
        from ._windows_job_limits import create_owned_job
        return create_owned_job(self, profile)

    def close_job(self, handle: Any) -> None:
        from ._windows_job_limits import close_owned_job
        close_owned_job(self, handle)

    def _open_image(self, path: str):
        if not isinstance(path, str) or "\x00" in path:
            _refuse(ERROR_PROFILE_INVALID, "T037 executable path is malformed")
        canonical = ntpath.abspath(path)
        drive, tail = ntpath.splitdrive(canonical)
        if (not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\")
                or canonical.startswith("\\\\") or canonical.startswith("\\\\?\\")):
            _refuse(ERROR_UNSUPPORTED, "T037 executable must be an ordinary local drive path")
        from ._windows_owned_resources import registry_for,attach_registry,own
        registry=registry_for(self);scope=registry.scope()
        try:
            from ._windows_parameter_publish import _API
            api = _API()
            attach_registry(api,registry)
            parents = api.open_security_path(ntpath.dirname(canonical), directory=True)
            closer=api._resource_closer() if hasattr(api,"_resource_closer") else api.close
            for parent in parents:own(api,parent,closer,label="image_parent")
            image = api.open_source(parents[-1], ntpath.basename(canonical))
            own(api,image,closer,label="pinned_image")
            identity, size, creation, written, links = api.source_fingerprint(image)
            if size <= 0 or size > self.MAX_IMAGE_BYTES or links != 1:
                _refuse(ERROR_POLICY_UNVERIFIED, "T037 executable size/link count is outside its bounded identity check")
            return api, parents, image, (identity, size, creation, written, links), canonical
        except BaseException as exc:
            registry.drain(scope)
            if isinstance(exc,WindowsT037Error):raise
            raise WindowsT037Error(ERROR_UNSUPPORTED, "T037 executable could not be pinned without reparse traversal") from exc

    def _hash_image_handle(self, api: Any, handle: Any, size: int) -> str:
        digest = hashlib.sha256()
        consumed = 0
        while consumed < size:
            block = api.read_chunk(handle, min(1024 * 1024, size - consumed))
            if not block:
                _refuse(ERROR_HOST_MISMATCH, "pinned executable ended before its reported size")
            digest.update(block)
            consumed += len(block)
        if api.read_chunk(handle, 1):
            _refuse(ERROR_HOST_MISMATCH, "pinned executable changed size during digest")
        return digest.hexdigest()

    def hash_image(self, path: str) -> str:
        from ._windows_owned_resources import registry_for,own,attach_registry
        registry=registry_for(self);scope=registry.scope()
        try:
            api, parents, image, fingerprint, _canonical = self._open_image(path)
            attach_registry(api,registry);closer=api._resource_closer() if hasattr(api,"_resource_closer") else api.close
            for parent in parents:own(api,parent,closer,label="image_parent")
            own(api,image,closer,label="pinned_image")
            return self._hash_image_handle(api, image, fingerprint[1])
        finally:
            failures=registry.drain(scope)
            if failures:raise failures[0][1]

    def _stdio_source(self, value: Any, *, input_stream: bool):
        import msvcrt
        if value in (None, subprocess.DEVNULL):
            access = self.GENERIC_READ if input_stream else self.GENERIC_WRITE
            handle = self.k.CreateFileW("NUL", access, self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
                                        None, self.OPEN_EXISTING, self.FILE_ATTRIBUTE_NORMAL, None)
            if not handle or int(handle) == self.C.c_void_p(-1).value:
                self._check(False, "CreateFileW(NUL stdio)")
            from ._windows_owned_resources import own,handle_closer
            closer,mode=handle_closer(self)
            own(self,handle,closer,mode=mode,label="owned_stdio_source")
            return handle, True
        if value in (subprocess.PIPE, subprocess.STDOUT):
            _refuse(ERROR_UNSUPPORTED, "T037 child stdio pipes/sentinel fallback are not supported")
        fileno = getattr(value, "fileno", None)
        if not callable(fileno):
            _refuse(ERROR_PROFILE_INVALID, "T037 stdio must be DEVNULL or an open file object")
        try:
            raw_handle = msvcrt.get_osfhandle(fileno())
        except Exception as exc:
            raise WindowsT037Error(ERROR_PROFILE_INVALID, "T037 stdio file handle is unavailable") from exc
        if raw_handle == -1:
            _refuse(ERROR_PROFILE_INVALID, "T037 stdio file descriptor has no OS handle")
        return self.C.c_void_p(raw_handle), False

    def _duplicate_inheritable(self, source: Any):
        W = self.W
        output = W.HANDLE()
        current = self.k.GetCurrentProcess()
        self._check(self.k.DuplicateHandle(current, source, current, self.C.byref(output),
            0, True, self.DUPLICATE_SAME_ACCESS), "DuplicateHandle(stdio)")
        if not output.value:
            _refuse(ERROR_UNSUPPORTED, "DuplicateHandle returned a null stdio handle")
        from ._windows_owned_resources import own,handle_closer
        closer,mode=handle_closer(self)
        own(self,output,closer,mode=mode,label="inherited_handle")
        return output

    def _environment_buffer(self, environment: Any):
        if environment is None:
            return None
        if not isinstance(environment, Mapping):
            _refuse(ERROR_PROFILE_INVALID, "T037 environment must be a string mapping")
        rows = []
        for key, value in environment.items():
            if (not isinstance(key, str) or not isinstance(value, str) or not key
                    or "\x00" in key or "\x00" in value
                    or ("=" in key and not key.startswith("="))):
                _refuse(ERROR_PROFILE_INVALID, "T037 environment contains an invalid key/value")
            rows.append(f"{key}={value}")
        if len(rows) > 32767:
            _refuse(ERROR_PROFILE_INVALID, "T037 environment exceeds the Win32 variable bound")
        rows.sort(key=lambda item: item.partition("=")[0].casefold())
        return self.C.create_unicode_buffer("\0".join(rows) + "\0\0")

    def _attribute_list(self, security_capabilities: Any, handles: Any):
        C = self.C
        size = C.c_size_t(0)
        self.k.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        if C.get_last_error() != 122 or not 0 < size.value <= 64 * 1024:
            _refuse(ERROR_UNSUPPORTED, "InitializeProcThreadAttributeList size probe has an unexpected ABI result")
        storage = C.create_string_buffer(size.value)
        pointer = C.cast(storage, C.c_void_p)
        self._check(self.k.InitializeProcThreadAttributeList(pointer, 2, 0, C.byref(size)),
                    "InitializeProcThreadAttributeList")
        from ._windows_owned_resources import own
        resource=own(self,pointer,self.k.DeleteProcThreadAttributeList,mode="VOID",label="attribute_list",
                     keepalive=(storage,security_capabilities,handles))
        try:
            self._check(self.k.UpdateProcThreadAttribute(pointer, 0,
                self.PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, C.byref(security_capabilities),
                C.sizeof(security_capabilities), None, None), "UpdateProcThreadAttribute(SecurityCapabilities)")
            self._check(self.k.UpdateProcThreadAttribute(pointer, 0,
                self.PROC_THREAD_ATTRIBUTE_HANDLE_LIST, self.C.cast(handles, C.c_void_p),
                C.sizeof(handles), None, None), "UpdateProcThreadAttribute(HandleList)")
        except BaseException:
            resource.release_owned(resource.registry.owner)
            raise
        return storage, pointer

    def create_suspended_appcontainer_child(self, command: Sequence[str], options: Mapping[str, Any], *,
                                             profile: WindowsT037Profile, job: Any, role: str,
                                             session_factory: Any = None):
        from ._windows_job_limits import require_configured_job
        require_configured_job(self, job, profile)
        if session_factory is not None and (
                type(session_factory) is not T037SessionProcessFactory
                or session_factory._api is not self or session_factory.profile is not profile
                or session_factory._job is not job or session_factory._closed):
            _refuse(ERROR_POLICY_UNVERIFIED, "G2 session owner differs from the exact original T037 factory/profile/job")
        C, W = self.C, self.W
        if role not in {"server", "worker"}:
            _refuse(ERROR_PROFILE_INVALID, "T037 child role is unsupported")
        if (not isinstance(command, Sequence) or isinstance(command, (str, bytes)) or not command
                or any(not isinstance(value, str) or "\x00" in value for value in command)):
            _refuse(ERROR_PROFILE_INVALID, "T037 command must be a nonempty NUL-free string array")
        allowed = {"cwd", "stdin", "stdout", "stderr", "env", "executable", "creationflags",
                   "start_new_session", "text", "close_fds"}
        if set(options) - allowed:
            _refuse(ERROR_UNSUPPORTED, "T037 launch received unsupported Popen options")
        if options.get("close_fds", True) is not True:
            _refuse(ERROR_POLICY_UNVERIFIED, "T037 launch requires close_fds and an exact inherited-handle list")
        if options.get("text", False) not in (True, False):
            _refuse(ERROR_PROFILE_INVALID, "T037 text option must be boolean")
        flags_option = options.get("creationflags", 0)
        if type(flags_option) is not int or flags_option & ~self.CREATE_NEW_PROCESS_GROUP:
            _refuse(ERROR_UNSUPPORTED, "T037 launch creation flags are outside its reviewed subset")
        executable = options.get("executable") or command[0]
        if not isinstance(executable, str) or not executable:
            _refuse(ERROR_PROFILE_INVALID, "T037 lpApplicationName is missing")
        expected_hash = profile.server_image_sha256 if role == "server" else profile.worker_image_sha256
        from ._windows_owned_resources import registry_for,own,attach_registry,handle_closer
        registry=registry_for(self);launch_scope=registry.scope()
        image_api, parent_handles, image_handle, image_fingerprint, image_path = self._open_image(executable)
        attach_registry(image_api,registry)
        image_closer=image_api._resource_closer() if hasattr(image_api,"_resource_closer") else image_api.close
        for parent in parent_handles:own(image_api,parent,image_closer,label="image_parent")
        own(image_api,image_handle,image_closer,label="pinned_image")
        inherited: list[Any] = []
        duplicate_handles: list[Any] = []
        sid_allocations: list[Any] = []
        app_sid = C.c_void_p()
        primary_token = W.HANDLE()
        process_handle = thread_handle = None
        retained_child = None
        native_parent = None
        attribute_pointer = None
        image_verified = False
        launch_release_failures = []
        try:
            if not _constant_digest_equal(self._hash_image_handle(image_api, image_handle,
                                                                   image_fingerprint[1]), expected_hash):
                _refuse(ERROR_POLICY_UNVERIFIED, f"T037 {role} image does not match trusted startup configuration")
            image_verified = True

            # Use the daemon's already-running dedicated service account token;
            # no credentials, LogonUser, service install, or privilege change.
            current_token = W.HANDLE()
            token_access = 0x0008 | 0x0002 | 0x0001 | 0x0080 | 0x0100
            self._check(self.a.OpenProcessToken(self.k.GetCurrentProcess(), token_access,
                                                C.byref(current_token)), "OpenProcessToken(service)")
            native_closer,native_mode=handle_closer(self)
            current_owner=own(self,current_token,native_closer,mode=native_mode,label="current_token")
            try:
                service_observation = self.read_token(current_token)
                if service_observation.get("user_sid", "").casefold() != profile.service_account_sid.casefold():
                    _refuse(ERROR_POLICY_UNVERIFIED, "daemon is not running as the configured dedicated service identity")
                # TOKEN_PRIMARY=1, SecurityImpersonation=2.
                self._check(self.a.DuplicateTokenEx(current_token, token_access, None, 2, 1,
                                                     C.byref(primary_token)), "DuplicateTokenEx(primary)")
                own(self,primary_token,native_closer,mode=native_mode,label="primary_token")
            finally:
                current_owner.release_owned(registry.owner)

            self._check(self.a.ConvertStringSidToSidW(profile.appcontainer_sid, C.byref(app_sid)),
                        "ConvertStringSidToSidW(AppContainer)")
            own(self,app_sid,self.k.LocalFree,mode="LOCALFREE",label="appcontainer_sid")
            class SID_AND_ATTRIBUTES(C.Structure):
                _fields_ = [("Sid", C.c_void_p), ("Attributes", W.DWORD)]
            class SECURITY_CAPABILITIES(C.Structure):
                _fields_ = [("AppContainerSid", C.c_void_p),
                            ("Capabilities", C.POINTER(SID_AND_ATTRIBUTES)),
                            ("CapabilityCount", W.DWORD), ("Reserved", W.DWORD)]
            capability_rows = (SID_AND_ATTRIBUTES * max(1, len(profile.capability_sids)))()
            for index, sid_text in enumerate(profile.capability_sids):
                sid_pointer = C.c_void_p()
                self._check(self.a.ConvertStringSidToSidW(sid_text, C.byref(sid_pointer)),
                            "ConvertStringSidToSidW(Capability)")
                own(self,sid_pointer,self.k.LocalFree,mode="LOCALFREE",label="capability_sid")
                sid_allocations.append(sid_pointer)
                capability_rows[index].Sid = sid_pointer.value
                capability_rows[index].Attributes = 0x4  # SE_GROUP_ENABLED
            security_caps = SECURITY_CAPABILITIES(
                app_sid.value, C.cast(capability_rows, C.POINTER(SID_AND_ATTRIBUTES)),
                len(profile.capability_sids), 0,
            )

            stdio_map: dict[str, Any] = {}
            for name, is_stdin in (("stdin", True), ("stdout", False), ("stderr", False)):
                source_value = options.get(name)
                if name == "stderr" and source_value == subprocess.STDOUT:
                    stdio_map[name] = "stdout"
                    continue
                source, owned = self._stdio_source(source_value, input_stream=is_stdin)
                source_owner=own(self,source,native_closer,mode=native_mode,label="owned_stdio_source") if owned else None
                try:
                    duplicate = self._duplicate_inheritable(source)
                    own(self,duplicate,native_closer,mode=native_mode,label="inherited_handle")
                    duplicate_handles.append(duplicate)
                    stdio_map[name] = duplicate
                finally:
                    if source_owner is not None:
                        source_owner.release_owned(registry.owner)
            for name in ("stdin", "stdout", "stderr"):
                if stdio_map[name] == "stdout":
                    stdio_map[name] = stdio_map["stdout"]
            if stdio_map["stderr"] == "stdout":
                _refuse(ERROR_PROFILE_INVALID, "T037 stderr STDOUT does not resolve to an explicit stdout handle")
            for value in stdio_map.values():
                if not any(int(value.value) == int(existing.value) for existing in inherited):
                    inherited.append(value)
            handles_array = (W.HANDLE * len(inherited))(*inherited)
            attr_storage, attribute_pointer = self._attribute_list(security_caps, handles_array)
            own(self,attribute_pointer,self.k.DeleteProcThreadAttributeList,mode="VOID",label="attribute_list",
                keepalive=(attr_storage,security_caps,handles_array))

            class STARTUPINFOW(C.Structure):
                _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                    ("lpTitle", W.LPWSTR), ("dwX", W.DWORD), ("dwY", W.DWORD),
                    ("dwXSize", W.DWORD), ("dwYSize", W.DWORD), ("dwXCountChars", W.DWORD),
                    ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD), ("dwFlags", W.DWORD),
                    ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                    ("lpReserved2", C.POINTER(W.BYTE)), ("hStdInput", W.HANDLE),
                    ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]
            class STARTUPINFOEXW(C.Structure):
                _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", C.c_void_p)]
            class PROCESS_INFORMATION(C.Structure):
                _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE),
                            ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]
            startup = STARTUPINFOEXW()
            startup.StartupInfo.cb = C.sizeof(STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = self.STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = stdio_map["stdin"]
            startup.StartupInfo.hStdOutput = stdio_map["stdout"]
            startup.StartupInfo.hStdError = stdio_map["stderr"]
            startup.lpAttributeList = attribute_pointer
            environment = self._environment_buffer(options.get("env"))
            cwd = options.get("cwd")
            if cwd is not None and (not isinstance(cwd, str) or not ntpath.isabs(cwd)
                                    or cwd.startswith("\\\\")):
                _refuse(ERROR_UNSUPPORTED, "T037 cwd must be an absolute local Windows path")
            command_line = C.create_unicode_buffer(subprocess.list2cmdline(list(command)))
            flags = (self.CREATE_SUSPENDED | self.EXTENDED_STARTUPINFO_PRESENT
                     | self.CREATE_UNICODE_ENVIRONMENT | flags_option)
            if options.get("start_new_session", False):
                flags |= self.CREATE_NEW_PROCESS_GROUP
            process_info = PROCESS_INFORMATION()
            from ._windows_t037_child import pin_native_parent
            native_parent = pin_native_parent(self)
            creation_parameters = (image_path, tuple(command), command_line.value)
            require_configured_job(self, job, profile)
            created = self.a.CreateProcessAsUserW(
                primary_token, image_path, command_line, None, None, True, flags,
                C.cast(environment, C.c_void_p) if environment is not None else None,
                cwd, C.byref(startup), C.byref(process_info))
            process_handle, thread_handle = process_info.hProcess, process_info.hThread
            complete = bool(created and process_handle and thread_handle and int(process_info.dwProcessId) > 0)
            if process_handle or thread_handle:
                retained_child = _RetainedSuspendedChild(
                    self, process_handle, int(process_info.dwProcessId), command, thread_handle, job,
                    seal=_NATIVE_SUSPENDED_CHILD_SEAL, creation_confirmed=complete)
                retained_child._launch_profile_sha256 = profile.sha256
                retained_child._launch_role = role
                retained_child._resume_epoch = 0
                retained_child._creation_parameters = creation_parameters
                retained_child._creation_parameter_digests = {
                    "classification": "ACTUAL_CREATEPROCESS_PARAMETERS_NOT_REMOTE_READBACK",
                    "argv_sha256": hashlib.sha256(json.dumps(creation_parameters[1],
                        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
                    "command_line_sha256": hashlib.sha256(creation_parameters[2].encode("utf-16-le")).hexdigest(),
                    "application_name_sha256": hashlib.sha256(creation_parameters[0].encode("utf-16-le")).hexdigest(),
                }
                retained_child._parent_binding = native_parent
            self._check(created, "CreateProcessAsUserW(STARTUPINFOEX/SecurityCapabilities)")
            if not complete:
                _refuse(ERROR_UNSUPPORTED, "CreateProcessAsUserW returned incomplete process identity")
            require_configured_job(self, job, profile)
            self._check(self.k.AssignProcessToJobObject(job, process_handle), "AssignProcessToJobObject(shared session job)")

            if image_api.source_fingerprint(image_handle) != image_fingerprint:
                _refuse(ERROR_POLICY_UNVERIFIED, "pinned image file identity changed during native creation")
            observed_path = self.process_image_path(process_handle)
            if ntpath.normcase(ntpath.abspath(observed_path)) != ntpath.normcase(image_path):
                _refuse(ERROR_POLICY_UNVERIFIED, "suspended process image path differs from the pinned executable")
            child_token = W.HANDLE()
            self._check(self.a.OpenProcessToken(process_handle, self.TOKEN_QUERY | 0x2, C.byref(child_token)),
                        "OpenProcessToken(suspended child)")
            token_owner = _RetainedChildToken(self, child_token, retained_child, seal=_NATIVE_CHILD_TOKEN_SEAL)
            if not hasattr(self, "_retained_child_tokens"):
                self._retained_child_tokens = {}
            self._retained_child_tokens[id(token_owner)] = token_owner
            try:
                observed = self.read_token(child_token)
                from ._windows_t037_acl import check_suspended_child_dacl
                self._last_child_dacl_observation = check_suspended_child_dacl(
                    profile, process_handle, child_token, int(process_info.dwProcessId), self)
            finally:
                try:
                    token_owner.release_owned(self)
                except BaseException as error:
                    launch_release_failures.append(("child_token", error))
                    if not hasattr(self, "_retained_launch_cleanup"):
                        self._retained_launch_cleanup = []
                    self._retained_launch_cleanup.append(("child_token", token_owner.release_owned, (self,), error))
                    raise
            if (observed.get("state") != "OBSERVED" or observed.get("is_appcontainer") is not True
                    or observed.get("user_sid", "").casefold() != profile.service_account_sid.casefold()
                    or observed.get("appcontainer_sid", "").casefold() != profile.appcontainer_sid.casefold()
                    or {sid.casefold() for sid in observed.get("capability_sids", [])}
                       != {sid.casefold() for sid in profile.capability_sids}
                    or not self.is_process_in_job(process_handle, job)):
                _refuse(ERROR_POLICY_UNVERIFIED, "suspended child token or exact Job membership failed readback")
            birth, _exit = self.process_times(process_handle)
            if birth <= 0:
                _refuse(ERROR_UNSUPPORTED, "suspended child birth identity is unavailable")
            # DACL observation is not MIC/WFP/quota/vendor or a child-bound
            # launch proof. Even a direct low-level call must fail before Resume.
            from ._windows_t037_child import observe_retained_child
            child_binding = observe_retained_child(retained_child, profile, self, parent=native_parent)
            if (image_api.source_fingerprint(image_handle) != image_fingerprint
                    or not _constant_digest_equal(self._hash_image_handle(image_api, image_handle,
                                                                         image_fingerprint[1]), expected_hash)):
                _refuse(ERROR_POLICY_UNVERIFIED, "pinned image file changed during child observations")
            from ._windows_t037_resume import issue_native_resume, resume_native_child
            capability = issue_native_resume(retained_child, profile, child_binding,
                                             self._last_child_dacl_observation)
            resumed = resume_native_child(retained_child, capability)
            if resumed == 0xFFFFFFFF:
                self._check(False, "ResumeThread(exact suspended main thread)")
            if resumed != 1:
                _refuse(ERROR_POLICY_UNVERIFIED, "suspended child had an unexpected main-thread suspend count")
            self.close_handle(thread_handle)
            thread_handle = None
            process = _Win32ChildProcess(self, process_handle, int(process_info.dwProcessId), command)
            process_handle = None
            if session_factory is not None:
                owner = _Win32G2SessionLaunchOwner(
                    api=self, process=process, handle=process._handle,
                    birth_filetime_100ns=birth, argv=tuple(command), job_handle=job,
                    profile=profile, role=role, image_path=image_path,
                    image_sha256=expected_hash, session_factory=session_factory,
                    seal=_G2_SESSION_LAUNCH_OWNER_SEAL,
                )
                if not hasattr(self, "_g2_session_launch_owners"):
                    self._g2_session_launch_owners = {}
                self._g2_session_launch_owners[id(process)] = owner
            return process
        except BaseException as cause:
            if retained_child is not None:
                try:
                    retained_child.cleanup_owned(self, timeout=5)
                except BaseException as cleanup_error:
                    if not hasattr(self, "_retained_children"):
                        self._retained_children = {}
                    self._retained_children[id(retained_child)] = retained_child
                    raise NativeSuspendedChildLaunchError(
                        retained_child, cause, cleanup_error, seal=_NATIVE_SUSPENDED_CHILD_SEAL) from cause
                finally:
                    # The wrapper owns both native process/thread handles.
                    process_handle = thread_handle = None
            elif process_handle or thread_handle:
                # No validated CreateProcess identity exists for a malformed
                # partial response. Do not infer safe cleanup or PID ownership.
                self._unclassified_create_handles = (process_handle, thread_handle)
                _refuse("NATIVE_CHILD_CREATION_UNKNOWN", "partial native creation handles require owned recovery")
            raise
        finally:
            pending_error = sys.exc_info()[1]
            release_failures = launch_release_failures
            def release(label, callback, *args, free_result=False):
                try:
                    result = callback(*args)
                    if free_result and result:
                        raise RuntimeError("native allocation release was not confirmed")
                except BaseException as error:
                    release_failures.append((label, error))
                    # Retain the actual adapter/call target and original handle,
                    # never a wire/PID reconstruction or a silent retry.
                    if not hasattr(self, "_retained_launch_cleanup"):
                        self._retained_launch_cleanup = []
                    self._retained_launch_cleanup.append((label, callback, args, error))
            release_failures.extend(registry.drain(launch_scope))
            if native_parent is not None:
                release("native_parent", native_parent.close)
            if release_failures:
                diagnostics = tuple({"stage": label, "exception_type": type(error).__name__}
                                    for label, error in release_failures)
                if retained_child is not None:
                    if not hasattr(self, "_retained_children"):
                        self._retained_children = {}
                    self._retained_children[id(retained_child)] = retained_child
                    handoff = NativeSuspendedChildLaunchError(retained_child,
                        pending_error or release_failures[0][1], release_failures[0][1],
                        seal=_NATIVE_SUSPENDED_CHILD_SEAL)
                    handoff.original_launch_error = pending_error
                    handoff.cleanup_failures = diagnostics
                    handoff.details["ancillary_cleanup_failures"] = diagnostics
                    raise handoff from release_failures[0][1]
                _refuse("NATIVE_LAUNCH_RELEASE_UNKNOWN", "native launch ancillary handles remain owned",
                        ancillary_cleanup_failures=diagnostics)

    def poll_process(self, process: Any) -> int | None:
        result = int(self.k.WaitForSingleObject(process, 0))
        if result == self.WAIT_TIMEOUT:
            return None
        if result != self.WAIT_OBJECT_0:
            self._check(False, "WaitForSingleObject(poll)")
        code = self.W.DWORD()
        self._check(self.k.GetExitCodeProcess(process, self.C.byref(code)), "GetExitCodeProcess")
        return int(code.value)

    def wait_process(self, process: Any, timeout: float | None, args: Sequence[str]) -> int:
        if timeout is None:
            millis = self.INFINITE
        else:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
                _refuse(ERROR_PROFILE_INVALID, "Popen timeout must be nonnegative or null")
            millis = min(self.INFINITE - 1, int(timeout * 1000 + 0.999))
        result = int(self.k.WaitForSingleObject(process, millis))
        if result == self.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(args, timeout)
        if result != self.WAIT_OBJECT_0:
            self._check(False, "WaitForSingleObject(wait)")
        code = self.W.DWORD()
        self._check(self.k.GetExitCodeProcess(process, self.C.byref(code)), "GetExitCodeProcess")
        return int(code.value)

    def terminate_process(self, process: Any, exit_code: int) -> None:
        self._check(self.k.TerminateProcess(process, exit_code), "TerminateProcess(exact process handle)")


def build_changes_manifest(profile: WindowsT037Profile | WindowsT037ProfileV2 | None) -> dict[str, Any]:
    """Create a deterministic plan-only host change manifest."""
    if profile is None:
        _refuse(ERROR_PROFILE_INVALID, "a trusted profile is required to render the T037 changes plan")
    result = {
        "schema": "comsol-mcp.windows-t037-change-manifest/1",
        "profile_id": profile.profile_id,
        "profile_sha256": profile.source_sha256,
        "host_fingerprint_sha256": profile.host_fingerprint_sha256,
        "mode": "PLAN_ONLY",
        "changes": [
            {"id": "appcontainer-profile", "state": "PLAN_ONLY",
             "reason": "profile creation changes host state and is not authorized"},
            {"id": "service-identity-scm", "state": "CHANGES_REQUIRED",
             "reason": "dedicated service identity and SCM lifecycle adapter are not implemented"},
            {"id": "appcontainer-acl-grants", "state": "PLAN_ONLY",
             "reason": "workspace/runtime DACL mutations require a reviewed baseline and explicit authorization"},
            {"id": "wfp-network-policy", "state": "CHANGES_REQUIRED",
             "reason": "WFP policy query/apply/restore adapter is not implemented"},
            {"id": "loopback-policy", "state": "UNVERIFIED",
             "reason": "COMSOL Server/Worker vendor compatibility and exact loopback requirements are not proven"},
            {"id": "storage-quota", "state": "UNVERIFIED",
             "reason": "hard quota is independent from admission and must only be required by explicit profile policy",
             "required_by_profile": profile.require_hard_quota},
            {"id": "job-memory", "state": "NOT_CONFIGURED",
             "reason": "Job Object committed-memory limits are distinct from RSS and T037 file/network isolation"},
        ],
        "apply_state": "NOT_RUN",
        "restore_state": "NOT_RUN",
        "native_windows_state": "NOT_RUN",
    }

    if type(profile) is WindowsT037ProfileV2 and profile.wrapper_schema == PROFILE_SCHEMA_V3:
        from ._windows_job_limits import require_limits
        result["profile_wrapper_schema"] = PROFILE_SCHEMA_V3
        result["job_limits_sha256"] = require_limits(profile).sha256
        for item in result["changes"]:
            if item["id"] == "job-memory":
                item.update(state="TARGET_CONFIGURED_NATIVE_NOT_RUN", targets=require_limits(profile).as_dict(),
                    reason="explicit committed-memory/active-process targets; owned Job SDK path implemented, native not run; no isolation proof")
    return result


def apply_changes_manifest(_manifest: Mapping[str, Any], *, _approval: Any = None) -> None:
    """Never mutate the host in this software-only unit."""
    _refuse(ERROR_UNSUPPORTED, "T037 host changes are plan-only; account/ACL/WFP/SCM/quota apply is unavailable")


def restore_changes_manifest(_manifest: Mapping[str, Any], *, _approval: Any = None) -> None:
    """Never restore or remove host state without a proven apply journal."""
    _refuse(ERROR_UNSUPPORTED, "T037 host restore is unavailable because no reviewed native apply journal exists")


def feasibility_snapshot(*, profile_path: str | None = None,
                         observer_factory: Callable[[], WindowsT037Observer] = WindowsT037Observer) -> dict[str, Any]:
    """Read-only, bounded current-process/volume feasibility report."""
    report: dict[str, Any] = {
        "schema": "comsol-mcp.windows-t037-feasibility/1",
        "platform": sys.platform,
        "state": "NOT_RUN",
        "native_policy": "NOT_RUN",
        "host_changes": "NOT_RUN",
        "sensitive_values_included": False,
    }
    if os.name != "nt":
        report.update({"state": "UNSUPPORTED_HOST", "reason_code": ERROR_UNSUPPORTED})
        return report
    try:
        observer = observer_factory()
        token = observer.api.current_process_token()
        report["current_process_token"] = {
            "state": token.get("state"), "user_sid_sha256": hashlib.sha256(
                str(token.get("user_sid", "")).encode("ascii")
            ).hexdigest(), "is_appcontainer": token.get("is_appcontainer"),
            "appcontainer_sid_sha256": hashlib.sha256(
                str(token.get("appcontainer_sid") or "").encode("ascii")
            ).hexdigest() if token.get("appcontainer_sid") else None,
            "capability_count": len(token.get("capability_sids", [])),
            "integrity_sid": token.get("integrity_sid"),
        }
        report["host_fingerprint_sha256"] = windows_host_fingerprint()
        if profile_path is not None:
            profile = load_trusted_profile(profile_path)
            report["profile"] = profile.as_safe_dict()
            report["changes_manifest"] = build_changes_manifest(profile)
            report["workspace_volume"] = observer.observe_volume(profile.workspace_root)
            report["readiness"] = observer.assess_profile(profile).__dict__
        else:
            report["profile_state"] = "NOT_CONFIGURED"
        report["state"] = "READ_ONLY_OBSERVATIONS_COMPLETE"
    except ExecutionContractError as exc:
        report["state"] = "UNVERIFIED"
        report["reason_code"] = exc.code
        report["reason"] = str(exc)
    except Exception as exc:
        report["state"] = "UNVERIFIED"
        report["reason_code"] = ERROR_UNSUPPORTED
        report["reason"] = type(exc).__name__
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Windows T037 feasibility probe")
    parser.add_argument("--profile", default=os.environ.get("COMSOL_MCP_WINDOWS_T037_PROFILE"))
    parser.add_argument("--json", action="store_true", help="write the bounded credential-free JSON report")
    arguments = parser.parse_args(argv)
    report = feasibility_snapshot(profile_path=arguments.profile)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("state") in {"READ_ONLY_OBSERVATIONS_COMPLETE", "UNSUPPORTED_HOST"} else 2


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke tests
    raise SystemExit(main())


__all__ = [
    "PROFILE_SCHEMA", "T037PolicyAssessment", "WindowsT037Error",
    "WindowsT037Observer", "WindowsT037Profile", "WindowsT037ProfileV2", "T037SessionProcessFactory",
    "assess_t037_readiness", "build_changes_manifest", "feasibility_snapshot",
    "load_trusted_profile", "parse_profile_document", "parse_profile_v2_document", "restore_changes_manifest",
    "windows_host_fingerprint", "apply_changes_manifest",
]
