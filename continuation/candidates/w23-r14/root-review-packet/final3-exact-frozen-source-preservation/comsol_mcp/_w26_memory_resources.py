"""Host-wide memory-admission profile and read-only OS observations.

This module separates trusted admission policy, native available-memory
observations, and hard enforcement.  The macOS adapter uses Mach VM
statistics/sysctl; Windows uses GlobalMemoryStatusEx and GetTickCount64.  Both
adapters report host-wide physical memory only.  Neither adapter creates a
kernel limit, Job Object limit, quota, or sandbox.

The OS adapters are read-only. The trusted coordinator records reservations
in the existing OperationStore ledger, and ControlDaemon commits disk and
memory reservations together before RUNNING or Worker.send.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError


MEMORY_PROFILE_SCHEMA = "comsol-mcp.memory-resource-profile/v1"
MEMORY_RESERVATION_SCHEMA = "comsol-mcp.memory-reservation/v1"
MEMORY_CONFIG_MAX_BYTES = 262_144
MEMORY_OBSERVATION_MAX_AGE_NS = 5_000_000_000
MEMORY_RESERVATION_EVENT = "W26MemoryReservation"
SQLITE_INT_MAX = (1 << 63) - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_OPERATION = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRUSTED_PROFILE_ATTESTATION = object()
_TRUSTED_CONFIG_ATTESTATION = object()
_NATIVE_OBSERVATION_ATTESTATION = object()
_MEMORY_PRODUCING_EFFECTS = frozenset({
    "COMPUTE", "FILE_WRITE", "WRITE", "STATE_WRITE", "TRUSTED_CODE", "EVALUATE", "DYNAMIC",
})


class MemoryResourceError(ExecutionContractError):
    """Typed fail-closed refusal for memory resource configuration/observation."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, message, safe_retry=True, details=details, stage="pre_dispatch")


def _refuse(code: str, message: str, **details: Any) -> None:
    raise MemoryResourceError(code, message, details=details or None)


def _checked_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a conservative identifier")
    return value


def _checked_memory_bytes(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= SQLITE_INT_MAX:
        raise ValueError(f"{label} must be an integer in [{minimum}, {SQLITE_INT_MAX}]")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("memory resource data is not canonical JSON") from exc


def _strict_json(raw: bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                          parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MemoryResourceError("MEMORY_PROFILE_INVALID", "memory profile must be strict UTF-8 JSON") from exc


def _reject_secret_fields(value: Any) -> None:
    forbidden = {"token", "accesstoken", "password", "passwd", "secret", "credential",
                 "credentials", "apikey", "privatekey", "authorization"}
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in forbidden or normalized.endswith(("token", "password", "secret", "credential")):
                raise MemoryResourceError("MEMORY_PROFILE_INVALID", "memory profile cannot contain secrets")
            _reject_secret_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_fields(child)


@dataclass(frozen=True)
class MemoryBudgetPolicy:
    """Admission-only host policy; these values do not impose a kernel cap."""

    max_total_reserved_bytes: int
    reserve_available_bytes: int
    max_task_reservation_bytes: int

    def __post_init__(self) -> None:
        _checked_memory_bytes(self.max_total_reserved_bytes, "max_total_reserved_bytes", minimum=1)
        _checked_memory_bytes(self.reserve_available_bytes, "reserve_available_bytes")
        _checked_memory_bytes(self.max_task_reservation_bytes, "max_task_reservation_bytes", minimum=1)
        if self.max_task_reservation_bytes > self.max_total_reserved_bytes:
            raise ValueError("max task reservation cannot exceed the host reservation budget")

    def as_dict(self) -> dict[str, int]:
        return {"max_total_reserved_bytes": self.max_total_reserved_bytes,
                "reserve_available_bytes": self.reserve_available_bytes,
                "max_task_reservation_bytes": self.max_task_reservation_bytes}


@dataclass(frozen=True)
class MemoryOperationEstimate:
    """Trusted startup estimate for one canonical inner operation."""

    operation_id: str
    estimate_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not _OPERATION.fullmatch(self.operation_id):
            raise ValueError("operation_id must be a canonical operation identifier")
        if self.operation_id in {"registry_call", "operation_call"}:
            raise ValueError("memory estimates require canonical inner operation IDs")
        _checked_memory_bytes(self.estimate_bytes, "estimate_bytes", minimum=1)

    def as_dict(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, "estimate_bytes": self.estimate_bytes}


@dataclass(frozen=True)
class MemoryResourceProfile:
    """Immutable host-wide memory admission profile loaded at daemon startup."""

    profile_id: str
    host_id: str
    platform: str
    budget: MemoryBudgetPolicy
    allowed_operations: tuple[str, ...]
    estimates: tuple[MemoryOperationEstimate, ...]
    source_sha256: str
    _trusted_attestation: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _checked_identifier(self.profile_id, "profile_id")
        if not isinstance(self.host_id, str) or not _SHA256.fullmatch(self.host_id):
            raise ValueError("host_id must be an OS-derived SHA-256 fingerprint")
        if self.platform not in {"macos", "windows"}:
            raise ValueError("memory profiles are supported only on macOS and Windows")
        if not isinstance(self.budget, MemoryBudgetPolicy):
            raise ValueError("typed memory budget is required")
        if not isinstance(self.source_sha256, str) or not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.allowed_operations, tuple) or not self.allowed_operations:
            raise ValueError("allowed_operations must be a nonempty tuple")
        if len(self.allowed_operations) > 512 or len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("allowed_operations must be bounded and unique")
        for operation in self.allowed_operations:
            if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
                raise ValueError("allowed operation is not a canonical inner operation ID")
            if operation in {"registry_call", "operation_call"}:
                raise ValueError("generic registry wrappers cannot be budget profile operations")
        if not isinstance(self.estimates, tuple) or any(not isinstance(row, MemoryOperationEstimate)
                                                        for row in self.estimates):
            raise ValueError("estimates must be typed MemoryOperationEstimate rows")
        estimated = [row.operation_id for row in self.estimates]
        if len(estimated) != len(set(estimated)) or set(estimated) != set(self.allowed_operations):
            raise ValueError("each allowed operation must have exactly one trusted estimate")
        if any(row.estimate_bytes > self.budget.max_task_reservation_bytes for row in self.estimates):
            raise ValueError("operation estimate exceeds max_task_reservation_bytes")

    def as_dict(self) -> dict[str, Any]:
        return {"schema": MEMORY_PROFILE_SCHEMA, "profile_id": self.profile_id,
                "host_id": self.host_id, "platform": self.platform,
                "budget": self.budget.as_dict(),
                "allowed_operations": list(self.allowed_operations),
                "trusted_estimates": [item.as_dict() for item in self.estimates]}

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.as_dict())).hexdigest()

    @property
    def trusted_startup_config(self) -> bool:
        return self._trusted_attestation is _TRUSTED_PROFILE_ATTESTATION

    def estimate_for(self, operation_id: str) -> int:
        if operation_id not in self.allowed_operations:
            _refuse("MEMORY_OPERATION_NOT_CONFIGURED", "operation is outside the trusted memory profile",
                    operation_id=operation_id)
        for row in self.estimates:
            if row.operation_id == operation_id:
                return row.estimate_bytes
        _refuse("MEMORY_OPERATION_ESTIMATE_MISSING", "trusted memory estimate is missing",
                operation_id=operation_id)


def parse_memory_profile_document(document: Any, *, actual_host_id: str,
                                  actual_platform: str, source_sha256: str) -> MemoryResourceProfile:
    """Parse intent only. Calling this function never establishes trust."""
    expected = {"schema", "profile_id", "host_id", "platform", "budget",
                "allowed_operations", "trusted_estimates"}
    if not isinstance(document, Mapping) or set(document) != expected:
        _refuse("MEMORY_PROFILE_INVALID", "memory profile fields do not match the closed schema")
    if document.get("schema") != MEMORY_PROFILE_SCHEMA:
        _refuse("MEMORY_PROFILE_INVALID", "memory profile schema version is unsupported")
    _reject_secret_fields(document)
    if document.get("host_id") != actual_host_id or document.get("platform") != actual_platform:
        _refuse("MEMORY_PROFILE_HOST_MISMATCH", "memory profile is bound to another host or platform")
    budget_raw = document.get("budget")
    if (not isinstance(budget_raw, Mapping)
            or set(budget_raw) != {"max_total_reserved_bytes", "reserve_available_bytes",
                                   "max_task_reservation_bytes"}):
        _refuse("MEMORY_PROFILE_INVALID", "memory budget fields do not match the closed schema")
    operations = document.get("allowed_operations")
    rows = document.get("trusted_estimates")
    if (not isinstance(operations, list) or not 1 <= len(operations) <= 512
            or not isinstance(rows, list) or len(rows) != len(operations)):
        _refuse("MEMORY_PROFILE_INVALID", "operation and estimate arrays must be bounded and complete")
    estimates: list[MemoryOperationEstimate] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"operation_id", "estimate_bytes"}:
            _refuse("MEMORY_PROFILE_INVALID", "trusted estimate row fields are invalid")
        try:
            estimates.append(MemoryOperationEstimate(row["operation_id"], row["estimate_bytes"]))
        except (TypeError, ValueError) as exc:
            raise MemoryResourceError("MEMORY_PROFILE_INVALID", "trusted estimate row is invalid") from exc
    try:
        return MemoryResourceProfile(
            profile_id=document["profile_id"], host_id=actual_host_id, platform=actual_platform,
            budget=MemoryBudgetPolicy(**dict(budget_raw)), allowed_operations=tuple(operations),
            estimates=tuple(estimates), source_sha256=source_sha256,
        )
    except (TypeError, ValueError) as exc:
        raise MemoryResourceError("MEMORY_PROFILE_INVALID", "memory profile failed strict validation") from exc


@dataclass(frozen=True)
class TrustedMemoryResourceConfig:
    profile: MemoryResourceProfile
    source_sha256: str
    source_identity: Mapping[str, Any]
    security_semantics: str
    _attestation: object | None = field(default=None, repr=False, compare=False)

    @property
    def trusted_startup_source(self) -> bool:
        return self._attestation is _TRUSTED_CONFIG_ATTESTATION and self.profile.trusted_startup_config

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "TrustedMemoryResourceConfig":
        target = Path(path)
        try:
            raw, identity, security_semantics = _read_trusted_config(target)
            if not 1 <= len(raw) <= MEMORY_CONFIG_MAX_BYTES:
                _refuse("MEMORY_PROFILE_INVALID", "memory profile file is outside the size bound")
            digest = hashlib.sha256(raw).hexdigest()
            document = _strict_json(raw)
            actual_platform = _current_platform()
            actual_host = _current_host_fingerprint(actual_platform)
            profile = parse_memory_profile_document(document, actual_host_id=actual_host,
                                                    actual_platform=actual_platform,
                                                    source_sha256=digest)
            trusted_profile = replace(profile, _trusted_attestation=_TRUSTED_PROFILE_ATTESTATION)
            return cls(trusted_profile, digest, dict(identity), security_semantics,
                       _TRUSTED_CONFIG_ATTESTATION)
        except MemoryResourceError:
            raise
        except ExecutionContractError as exc:
            raise MemoryResourceError("MEMORY_PROFILE_SECURITY_UNVERIFIED",
                                      "trusted memory profile security could not be verified",
                                      details={"cause_code": exc.code}) from exc
        except Exception as exc:
            raise MemoryResourceError("MEMORY_PROFILE_INVALID", "trusted memory profile load failed closed",
                                      details={"cause_type": type(exc).__name__}) from exc


@dataclass(frozen=True)
class HostMemoryObservation:
    """Fresh OS observation, not a hard limit or reservation receipt."""

    platform: str
    host_id: str
    boot_id: str
    boot_identity_semantics: str
    provider_id: str
    observed_monotonic_ns: int
    monotonic_clock_id: str
    total_physical_bytes: int
    available_physical_bytes: int
    available_semantics: str
    hard_cap_state: str = "NOT_OBSERVED"
    hard_cap_bytes: None = None
    evidence_classification: str = "SOFTWARE_TEST_SYNTHETIC"
    _attestation: object | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {"schema": "comsol-mcp.host-memory-observation/v1",
                "platform": self.platform, "host_id": self.host_id,
                "boot_id": self.boot_id, "boot_identity_semantics": self.boot_identity_semantics,
                "provider_id": self.provider_id,
                "observed_monotonic_ns": self.observed_monotonic_ns,
                "monotonic_clock_id": self.monotonic_clock_id,
                "total_physical_bytes": self.total_physical_bytes,
                "available_physical_bytes": self.available_physical_bytes,
                "available_semantics": self.available_semantics,
                "hard_cap_state": self.hard_cap_state, "hard_cap_bytes": self.hard_cap_bytes,
                "evidence_classification": self.evidence_classification}


def _current_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "unsupported"


def _current_host_fingerprint(platform_name: str) -> str:
    if platform_name == "windows":
        try:
            from ._windows_t037 import windows_host_fingerprint
            return windows_host_fingerprint()
        except Exception as exc:
            _refuse("MEMORY_HOST_ID_UNAVAILABLE", "Windows machine identity is unavailable",
                    cause_type=type(exc).__name__)
    if platform_name == "macos":
        return _darwin_host_fingerprint()
    _refuse("MEMORY_OBSERVER_UNSUPPORTED", "memory host identity is unsupported on this platform",
            platform=platform_name)


def _darwin_host_fingerprint() -> str:
    """Read IOPlatformUUID via IOKit/CoreFoundation and expose only its digest."""
    if sys.platform != "darwin":
        _refuse("MEMORY_OBSERVER_UNSUPPORTED", "Darwin host identity is unavailable on this platform")
    try:
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
        iokit.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p,
                                                          ctypes.c_void_p, ctypes.c_uint32]
        iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iokit.IOObjectRelease.restype = ctypes.c_int32
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetTypeID.argtypes = []
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                          ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        matching = iokit.IOServiceMatching(b"IOPlatformExpertDevice")
        if not matching:
            _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformExpertDevice matching failed")
        service = iokit.IOServiceGetMatchingService(0, matching)  # kIOMainPortDefault/MACH_PORT_NULL
        if not service:
            _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformExpertDevice service is unavailable")
        key = cf.CFStringCreateWithCString(None, b"IOPlatformUUID", 0x08000100)
        if not key:
            iokit.IOObjectRelease(service)
            _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformUUID property key allocation failed")
        value = None
        try:
            value = iokit.IORegistryEntryCreateCFProperty(service, key, None, 0)
            if not value or cf.CFGetTypeID(value) != cf.CFStringGetTypeID():
                _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformUUID is missing or not a string")
            encoding = 0x08000100  # kCFStringEncodingUTF8
            length = int(cf.CFStringGetLength(value))
            capacity = int(cf.CFStringGetMaximumSizeForEncoding(length, encoding)) + 1
            if not 1 <= capacity <= 1024:
                _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformUUID length is outside its bound")
            buffer = ctypes.create_string_buffer(capacity)
            if not cf.CFStringGetCString(value, buffer, capacity, encoding):
                _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformUUID could not be encoded")
            raw = buffer.value.decode("ascii").strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", raw):
                _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOPlatformUUID format is invalid")
            return hashlib.sha256(("comsol-mcp-macos-host-v1\0" + raw).encode("ascii")).hexdigest()
        finally:
            if value:
                cf.CFRelease(value)
            cf.CFRelease(key)
            iokit.IOObjectRelease(service)
    except MemoryResourceError:
        raise
    except Exception as exc:
        _refuse("MEMORY_HOST_ID_UNAVAILABLE", "IOKit host identity read failed",
                cause_type=type(exc).__name__)


class _DarwinTimeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_int32)]


class _DarwinVmStatsRev0(ctypes.Structure):
    """ABI prefix ending before `decompressions`, HOST_VM_INFO64_REV0_COUNT."""

    _fields_ = [
        ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
    ]


class _DarwinMemoryApi:
    """Darwin ABI bindings for mach host VM statistics and sysctlbyname."""

    provider_id = "darwin-mach-vm-statistics64/v1"

    def __init__(self):
        if sys.platform != "darwin":
            _refuse("MEMORY_OBSERVER_UNSUPPORTED", "Darwin memory API requested on another platform")
        if ctypes.sizeof(_DarwinTimeval) != 16 or ctypes.sizeof(_DarwinVmStatsRev0) != 96:
            _refuse("MEMORY_OBSERVER_ABI_UNSUPPORTED", "Darwin memory ABI layout differs from verified SDK")
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.lib = lib
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        lib.sysctlbyname.restype = ctypes.c_int
        lib.mach_host_self.argtypes = []
        lib.mach_host_self.restype = ctypes.c_uint32
        lib.host_page_size.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        lib.host_page_size.restype = ctypes.c_int
        lib.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int32,
                                          ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint32)]
        lib.host_statistics64.restype = ctypes.c_int
        lib.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.mach_port_deallocate.restype = ctypes.c_int

    def _sysctl(self, name: bytes, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        length = ctypes.c_size_t(size)
        ctypes.set_errno(0)
        if self.lib.sysctlbyname(name, buffer, ctypes.byref(length), None, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, f"sysctlbyname({name.decode('ascii')}) failed")
        if length.value != size:
            _refuse("MEMORY_OBSERVER_ABI_UNSUPPORTED", "sysctl returned an unexpected value size",
                    selector=name.decode("ascii"), expected=size, actual=length.value)
        return buffer.raw[:size]

    def read(self) -> dict[str, Any]:
        total = int.from_bytes(self._sysctl(b"hw.memsize", 8), byteorder=sys.byteorder, signed=False)
        boot = _DarwinTimeval.from_buffer_copy(self._sysctl(b"kern.boottime", ctypes.sizeof(_DarwinTimeval)))
        if boot.tv_sec <= 0 or not 0 <= boot.tv_usec < 1_000_000 or total <= 0:
            _refuse("MEMORY_OBSERVATION_INVALID", "Darwin boot or physical-memory fields are invalid")
        host = int(self.lib.mach_host_self())
        if host == 0:
            _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "mach_host_self returned a null port")
        try:
            page_size = ctypes.c_uint32()
            if self.lib.host_page_size(host, ctypes.byref(page_size)) != 0 or page_size.value == 0:
                _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "host_page_size failed")
            stats = _DarwinVmStatsRev0()
            count = ctypes.c_uint32(ctypes.sizeof(stats) // ctypes.sizeof(ctypes.c_int32))
            result = self.lib.host_statistics64(host, 4,
                                                ctypes.cast(ctypes.byref(stats), ctypes.POINTER(ctypes.c_int32)),
                                                ctypes.byref(count))
            if result != 0 or count.value != ctypes.sizeof(stats) // ctypes.sizeof(ctypes.c_int32):
                _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "host_statistics64 returned an unknown result",
                        kern_return=int(result), count=int(count.value))
            available = int(stats.free_count) * int(page_size.value)
        finally:
            task = int(ctypes.c_uint32.in_dll(self.lib, "mach_task_self_").value)
            if self.lib.mach_port_deallocate(task, host) != 0:
                _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "mach host port could not be released")
        if available < 0 or available > total:
            _refuse("MEMORY_OBSERVATION_INVALID", "Darwin free-page estimate exceeds physical memory")
        boot_id = hashlib.sha256(
            f"darwin-kern-boottime-v1:{int(boot.tv_sec)}:{int(boot.tv_usec)}".encode("ascii")
        ).hexdigest()
        return {"boot_id": boot_id, "boot_identity_semantics": "KERN_BOOTTIME_SYSCTL",
                "total_physical_bytes": total, "available_physical_bytes": available,
                "available_semantics": "vm_statistics64.free_count * host_page_size (conservative free-only lower bound)"}


class _WinMemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class _WinFileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _WindowsMemoryApi:
    """Strict Win32 GlobalMemoryStatusEx/boot-time observation adapter."""

    provider_id = "win32-globalmemorystatusex/v1"

    def __init__(self):
        if os.name != "nt":
            _refuse("MEMORY_OBSERVER_UNSUPPORTED", "Win32 memory API requested on another platform")
        if ctypes.sizeof(_WinMemoryStatusEx) != 64 or _WinMemoryStatusEx.ullAvailPhys.offset != 16:
            _refuse("MEMORY_OBSERVER_ABI_UNSUPPORTED", "MEMORYSTATUSEX ABI layout is unknown")
        from ctypes import wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_WinMemoryStatusEx)]
        self.k.GlobalMemoryStatusEx.restype = wintypes.BOOL
        self.k.GetTickCount64.argtypes = []
        self.k.GetTickCount64.restype = ctypes.c_uint64
        self.k.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(_WinFileTime)]
        self.k.GetSystemTimeAsFileTime.restype = None

    def read(self) -> dict[str, Any]:
        memory = _WinMemoryStatusEx()
        memory.dwLength = ctypes.sizeof(memory)
        if not self.k.GlobalMemoryStatusEx(ctypes.byref(memory)):
            error = ctypes.get_last_error()
            _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "GlobalMemoryStatusEx failed", winerror=error)
        total, available = int(memory.ullTotalPhys), int(memory.ullAvailPhys)
        if total <= 0 or not 0 <= available <= total:
            _refuse("MEMORY_OBSERVATION_INVALID", "MEMORYSTATUSEX physical memory fields are malformed")
        uptime_ms = int(self.k.GetTickCount64())
        utc = _WinFileTime()
        self.k.GetSystemTimeAsFileTime(ctypes.byref(utc))
        utc_100ns = (int(utc.high) << 32) | int(utc.low)
        boot_100ns = utc_100ns - uptime_ms * 10_000
        if uptime_ms <= 0 or boot_100ns <= 0:
            _refuse("MEMORY_BOOT_ID_UNAVAILABLE", "Windows boot-time derivation returned invalid values")
        # GetTickCount64 is OS uptime and is not affected by wall-clock adjustment.
        # Pairing it with UTC yields a stable within-boot epoch estimate; a later
        # clock correction conservatively changes the fingerprint and fails closed.
        boot_seconds = boot_100ns // 10_000_000
        try:
            from ._windows_t037 import windows_host_fingerprint
            host = windows_host_fingerprint()
        except Exception as exc:
            _refuse("MEMORY_HOST_ID_UNAVAILABLE", "Windows host fingerprint is unavailable",
                    cause_type=type(exc).__name__)
        boot_id = hashlib.sha256(f"windows-uptime-utc-v1:{host}:{boot_seconds}".encode("ascii")).hexdigest()
        return {"host_id": host, "boot_id": boot_id,
                "boot_identity_semantics": "GETTICKCOUNT64_PLUS_UTC_BOOT_EPOCH_ESTIMATE",
                "total_physical_bytes": total, "available_physical_bytes": available,
                "available_semantics": "GlobalMemoryStatusEx.ullAvailPhys (all NUMA nodes, immediately reusable physical pages)"}


class HostMemoryObservationProvider:
    """Read one fresh host-wide physical-memory sample from the current OS."""

    def __init__(self, *, _platform_name: str | None = None, _api: Any | None = None,
                 _host_id_reader: Any | None = None, _clock: Any = time.monotonic_ns):
        self.platform = _platform_name or _current_platform()
        if self.platform not in {"macos", "windows"}:
            _refuse("MEMORY_OBSERVER_UNSUPPORTED", "host memory observation is unsupported on this platform",
                    platform=self.platform)
        self._injected = (_api is not None or _host_id_reader is not None
                          or _platform_name is not None or _clock is not time.monotonic_ns)
        if self.platform == "macos":
            if not self._injected and sys.platform != "darwin":
                _refuse("MEMORY_OBSERVER_UNSUPPORTED", "Darwin memory observation requires Darwin")
            self._api = _api if _api is not None else _DarwinMemoryApi()
            self._host_id_reader = _host_id_reader or _darwin_host_fingerprint
        else:
            if not self._injected and os.name != "nt":
                _refuse("MEMORY_OBSERVER_UNSUPPORTED", "Win32 memory observation requires Windows")
            self._api = _api if _api is not None else _WindowsMemoryApi()
            self._host_id_reader = _host_id_reader or (lambda: _current_host_fingerprint("windows"))
        self._clock = _clock

    def read_observation(self) -> HostMemoryObservation:
        try:
            values = self._api.read()
            host = values.get("host_id") or self._host_id_reader()
            expected_host = self._host_id_reader()
            if host != expected_host:
                _refuse("MEMORY_HOST_ID_CHANGED", "host identity changed during memory observation")
            boot_id = values.get("boot_id")
            total = values.get("total_physical_bytes")
            available = values.get("available_physical_bytes")
            if (not isinstance(boot_id, str) or not _SHA256.fullmatch(boot_id)
                    or type(total) is not int or type(available) is not int
                    or total <= 0 or not 0 <= available <= total):
                _refuse("MEMORY_OBSERVATION_INVALID", "native memory provider returned malformed fields")
            stamp = self._clock()
            if type(stamp) is not int or stamp <= 0:
                _refuse("MEMORY_OBSERVATION_CLOCK_UNKNOWN", "monotonic clock returned an invalid value")
            real_native = not self._injected and self.platform == _current_platform()
            return HostMemoryObservation(
                platform=self.platform, host_id=host, boot_id=boot_id,
                boot_identity_semantics=values.get("boot_identity_semantics", "UNSPECIFIED"),
                provider_id=self._api.provider_id, observed_monotonic_ns=stamp,
                monotonic_clock_id=time.get_clock_info("monotonic").implementation,
                total_physical_bytes=total, available_physical_bytes=available,
                available_semantics=values.get("available_semantics", "UNSPECIFIED"),
                hard_cap_state="NOT_OBSERVED", hard_cap_bytes=None,
                evidence_classification=("HOST_OBSERVED_ADMISSION_ONLY" if real_native else "SOFTWARE_TEST_SYNTHETIC"),
                _attestation=_NATIVE_OBSERVATION_ATTESTATION if real_native else None,
            )
        except MemoryResourceError:
            raise
        except ExecutionContractError as exc:
            _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "native memory observation dependency failed",
                    cause_code=exc.code)
        except Exception as exc:
            _refuse("MEMORY_OBSERVATION_UNAVAILABLE", "host memory observation failed closed",
                    cause_type=type(exc).__name__)


@dataclass(frozen=True)
class MemoryAdmission:
    requested_bytes: int
    available_bytes: int
    aggregate_budget_remaining_bytes: int
    evidence_classification: str
    hard_cap_state: str
    observation_sha256: str


def assess_memory_admission(profile: MemoryResourceProfile, observation: HostMemoryObservation,
                            operation_id: str, *, active_reserved_bytes: int,
                            now_monotonic_ns: int,
                            max_age_ns: int = MEMORY_OBSERVATION_MAX_AGE_NS) -> MemoryAdmission:
    """Check a trusted estimate against host-wide admission headroom only.

    This has no durable effect. The caller must reserve this result in the same
    SQL transaction as disk before the queued job can become RUNNING.
    """
    if type(profile) is not MemoryResourceProfile or not profile.trusted_startup_config:
        _refuse("MEMORY_PROFILE_UNTRUSTED", "memory admission requires a trusted startup profile")
    if type(observation) is not HostMemoryObservation or observation._attestation is not _NATIVE_OBSERVATION_ATTESTATION:
        _refuse("MEMORY_OBSERVATION_UNVERIFIED", "memory admission requires a native OS observation")
    if (observation.evidence_classification != "HOST_OBSERVED_ADMISSION_ONLY"
            or observation.hard_cap_state != "NOT_OBSERVED" or observation.hard_cap_bytes is not None):
        _refuse("MEMORY_OBSERVATION_UNVERIFIED", "memory sample claims unsupported enforcement state")
    if observation.platform != profile.platform or observation.host_id != profile.host_id:
        _refuse("MEMORY_HOST_MISMATCH", "memory observation does not bind to the trusted host profile")
    try:
        current_clock_id = time.get_clock_info("monotonic").implementation
    except Exception as exc:
        _refuse("MEMORY_OBSERVATION_CLOCK_UNKNOWN", "current monotonic clock identity is unavailable",
                cause_type=type(exc).__name__)
    if observation.monotonic_clock_id != current_clock_id:
        _refuse("MEMORY_OBSERVATION_CLOCK_UNKNOWN",
                "memory observation uses a different monotonic clock implementation",
                observed_clock_id=observation.monotonic_clock_id,
                current_clock_id=current_clock_id)
    if (type(observation.observed_monotonic_ns) is not int or type(now_monotonic_ns) is not int
            or type(max_age_ns) is not int or max_age_ns <= 0
            or now_monotonic_ns < observation.observed_monotonic_ns
            or now_monotonic_ns - observation.observed_monotonic_ns > max_age_ns):
        _refuse("MEMORY_OBSERVATION_STALE", "memory observation is future-dated or stale")
    if (not _SHA256.fullmatch(observation.boot_id)
            or not isinstance(observation.monotonic_clock_id, str)
            or not 1 <= len(observation.monotonic_clock_id) <= 256
            or any(ord(char) < 0x20 for char in observation.monotonic_clock_id)
            or not isinstance(observation.boot_identity_semantics, str)
            or not observation.boot_identity_semantics
            or not isinstance(observation.available_semantics, str)
            or not observation.available_semantics):
        _refuse("MEMORY_OBSERVATION_INVALID", "memory observation lacks boot or monotonic identity")
    requested_bytes = profile.estimate_for(operation_id)
    if (type(active_reserved_bytes) is not int or active_reserved_bytes < 0
            or active_reserved_bytes > SQLITE_INT_MAX):
        _refuse("MEMORY_RESERVATION_INVALID", "memory reservation values exceed safe integer bounds")
    if requested_bytes > profile.budget.max_task_reservation_bytes:
        _refuse("MEMORY_RESERVATION_EXCEEDS_PROFILE", "requested memory exceeds the per-task profile ceiling",
                maximum_bytes=profile.budget.max_task_reservation_bytes)
    aggregate_remaining = max(0, profile.budget.max_total_reserved_bytes - active_reserved_bytes)
    observed_headroom = max(0, observation.available_physical_bytes - profile.budget.reserve_available_bytes
                            - active_reserved_bytes)
    available = min(aggregate_remaining, observed_headroom)
    if requested_bytes > available:
        _refuse("INSUFFICIENT_MEMORY_BUDGET", "fresh host memory headroom is below the trusted estimate",
                requested_bytes=requested_bytes, available_bytes=available,
                evidence_classification="HOST_OBSERVED_ADMISSION_ONLY",
                hard_cap_state="NOT_OBSERVED")
    return MemoryAdmission(requested_bytes, available, aggregate_remaining,
                           "HOST_OBSERVED_ADMISSION_ONLY", "NOT_OBSERVED",
                           hashlib.sha256(_canonical_json(observation.as_dict())).hexdigest())


def _load_memory_reservations(db) -> dict[str, tuple[str, dict[str, Any]]]:
    from ._w26_os_resources import _event_json

    rows = db.execute(
        "SELECT id,job_id,metadata FROM job_events WHERE event=? ORDER BY id",
        (MEMORY_RESERVATION_EVENT,),
    ).fetchall()
    required = {"schema", "reservation_id", "status", "job_id", "operation_id", "project_id",
                "host_id", "boot_id", "profile_id", "profile_sha256", "requested_bytes",
                "observation_sha256", "at_monotonic_ns", "monotonic_clock_id",
                "available_bytes", "evidence_classification", "hard_cap_state"}
    state: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in rows:
        try:
            item = _event_json(row["metadata"])
            if (set(item) != required or item.get("schema") != MEMORY_RESERVATION_SCHEMA
                    or item.get("job_id") != row["job_id"]
                    or item.get("status") not in {"RESERVED", "RELEASED"}
                    or not isinstance(item.get("reservation_id"), str)
                    or not _SHA256.fullmatch(item.get("reservation_id", ""))
                    or not isinstance(item.get("profile_sha256"), str)
                    or not _SHA256.fullmatch(item.get("profile_sha256", ""))
                    or not isinstance(item.get("observation_sha256"), str)
                    or not _SHA256.fullmatch(item.get("observation_sha256", ""))
                    or not isinstance(item.get("host_id"), str)
                    or not _SHA256.fullmatch(item.get("host_id", ""))
                    or not isinstance(item.get("boot_id"), str)
                    or not _SHA256.fullmatch(item.get("boot_id", ""))
                    or not isinstance(item.get("profile_id"), str)
                    or not _IDENTIFIER.fullmatch(item.get("profile_id", ""))
                    or not isinstance(item.get("operation_id"), str)
                    or not _OPERATION.fullmatch(item.get("operation_id", ""))
                    or not isinstance(item.get("project_id"), str) or not item["project_id"]
                    or type(item.get("requested_bytes")) is not int
                    or not 1 <= item["requested_bytes"] <= SQLITE_INT_MAX
                    or type(item.get("available_bytes")) is not int
                    or not 0 <= item["available_bytes"] <= SQLITE_INT_MAX
                    or type(item.get("at_monotonic_ns")) is not int
                    or item["at_monotonic_ns"] <= 0
                    or not isinstance(item.get("monotonic_clock_id"), str)
                    or not 1 <= len(item["monotonic_clock_id"]) <= 256
                    or item.get("evidence_classification") != "HOST_OBSERVED_ADMISSION_ONLY"
                    or item.get("hard_cap_state") != "NOT_OBSERVED"):
                raise ValueError("memory reservation event fields are invalid")
            reservation_id = item["reservation_id"]
            previous = state.get(reservation_id)
            if previous is None:
                if item["status"] != "RESERVED":
                    raise ValueError("release event has no reservation")
                state[reservation_id] = (row["job_id"], item)
                continue
            previous_job, previous_item = previous
            old = {key: value for key, value in previous_item.items() if key != "status"}
            new = {key: value for key, value in item.items() if key != "status"}
            if previous_job != row["job_id"] or previous_item["status"] != "RESERVED" \
                    or item["status"] != "RELEASED" or old != new:
                raise ValueError("memory reservation transition is duplicated or inconsistent")
            state[reservation_id] = (row["job_id"], item)
        except (TypeError, ValueError) as exc:
            _refuse("MEMORY_RESERVATION_STATE_UNKNOWN", "durable memory reservation ledger is malformed",
                    event_id=row["id"], cause=str(exc))
    return state


def _memory_reservation_id(job_id: str, operation_id: str, profile: MemoryResourceProfile) -> str:
    return hashlib.sha256(_canonical_json({
        "schema": MEMORY_RESERVATION_SCHEMA, "job_id": job_id,
        "operation_id": operation_id, "host_id": profile.host_id,
    })).hexdigest()


def _queued_abort_proves_never_dispatched(db: Any, job_id: str) -> bool:
    """Recognize only OperationStore's durable QUEUED cancellation proof.

    A terminal label alone is not evidence that the engine stopped. The
    OperationStore emits this exact event atomically with QUEUED -> CANCELLED;
    the same transaction boundary prevents it from racing past RUNNING.
    """
    try:
        job_row = db.execute("SELECT metadata FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if job_row is None:
            return False
        job_metadata = json.loads(job_row["metadata"] or "{}")
        if not isinstance(job_metadata, Mapping) or job_metadata.get("engine_dispatched") is True:
            return False
        rows = db.execute(
            "SELECT event,metadata FROM job_events WHERE job_id=? ORDER BY id", (job_id,),
        ).fetchall()
        events = []
        proofs = []
        for row in rows:
            event = row["event"]
            metadata = json.loads(row["metadata"] or "{}")
            if not isinstance(event, str) or not isinstance(metadata, Mapping):
                return False
            if metadata.get("engine_dispatched") is True:
                return False
            events.append(event)
            if (event == "CANCELLED" and metadata.get("engine_dispatched") is False
                    and metadata.get("cancelled_while") == "QUEUED"
                    and metadata.get("mode") == "QUEUED_ABORT"):
                proofs.append(metadata)
        return (len(proofs) == 1
                and not any(event in {"RUNNING", "worker_request", "DISPATCH_INTENT"}
                            for event in events))
    except Exception:
        return False


def _reserve_memory_in_transaction(*, store: Any, authority: Any, job_id: str,
                                   profile: MemoryResourceProfile,
                                   observation: HostMemoryObservation,
                                   max_age_ns: int) -> dict[str, Any]:
    from . import _w26_os_resources as resources

    if not store.db.in_transaction:
        _refuse("MEMORY_RESERVATION_TRANSACTION_REQUIRED",
                "memory reservation must join the immediate OperationStore transaction")
    try:
        # Read freshness only after the caller has acquired SQLite's write
        # transaction. Preparation may include a slow host observation or
        # wait behind another writer, so its earlier timestamp is not useful
        # for authorizing this durable reservation.
        now_monotonic_ns = time.monotonic_ns()
        current_clock_id = time.get_clock_info("monotonic").implementation
    except Exception as exc:
        _refuse("MEMORY_OBSERVATION_CLOCK_UNKNOWN", "reservation-time monotonic clock is unavailable",
                cause_type=type(exc).__name__)
    if (type(now_monotonic_ns) is not int or now_monotonic_ns <= 0
            or observation.monotonic_clock_id != current_clock_id):
        _refuse("MEMORY_OBSERVATION_CLOCK_UNKNOWN",
                "reservation-time clock does not match the observed clock implementation",
                observed_clock_id=observation.monotonic_clock_id,
                current_clock_id=current_clock_id)
    if type(profile) is not MemoryResourceProfile or not profile.trusted_startup_config:
        _refuse("MEMORY_PROFILE_UNTRUSTED", "only a trusted startup memory profile may reserve memory")
    context = resources._durable_job_context(store.db, job_id)
    operation_id = context["canonical_operation_id"]
    if context["job_status"] != "QUEUED" or context["operation_status"] != "QUEUED":
        _refuse("MEMORY_RESERVATION_NOT_PREDISPATCH", "memory reservation must precede RUNNING",
                job_status=context["job_status"], operation_status=context["operation_status"])
    if profile.estimate_for(operation_id) <= 0:
        _refuse("MEMORY_ESTIMATE_INVALID", "trusted operation estimate is invalid")
    permission = resources._permission_for_disk_operation(operation_id)
    resources._authorize_project(authority, context["project_id"], permission)
    if observation._attestation is not _NATIVE_OBSERVATION_ATTESTATION:
        _refuse("MEMORY_OBSERVATION_UNVERIFIED", "synthetic observations cannot create reservations")

    reservation_id = _memory_reservation_id(job_id, operation_id, profile)
    states = _load_memory_reservations(store.db)
    prior = states.get(reservation_id)
    own_reserved = 0
    if prior is not None:
        prior_job, prior_item = prior
        if (prior_job != job_id or prior_item["status"] != "RESERVED"
                or prior_item["profile_sha256"] != profile.sha256
                or prior_item["requested_bytes"] != profile.estimate_for(operation_id)
                or prior_item["project_id"] != context["project_id"]):
            _refuse("MEMORY_RESERVATION_IDENTITY_CONFLICT",
                    "job already has an incompatible or released memory reservation")
        own_reserved = prior_item["requested_bytes"]

    active_reserved = 0
    for _bound_job, item in states.values():
        if item["status"] == "RESERVED" and item["host_id"] == profile.host_id:
            active_reserved += item["requested_bytes"]
            if active_reserved > SQLITE_INT_MAX:
                _refuse("MEMORY_RESERVATION_STATE_UNKNOWN",
                        "durable host memory reservations exceed the SQLite integer range")
    if own_reserved:
        active_reserved -= own_reserved
    admission = assess_memory_admission(
        profile, observation, operation_id, active_reserved_bytes=active_reserved,
        now_monotonic_ns=now_monotonic_ns, max_age_ns=max_age_ns,
    )
    if prior is not None:
        return {"status": "RESERVED", "reservation_id": reservation_id,
                "requested_bytes": admission.requested_bytes, "available_bytes": admission.available_bytes,
                "memory_resource_profile_id": profile.profile_id,
                "memory_resource_profile_sha256": profile.sha256, "host_id": profile.host_id,
                "boot_id": observation.boot_id, "evidence_classification": admission.evidence_classification,
                "hard_cap_state": admission.hard_cap_state, "idempotent": True}

    item = {
        "schema": MEMORY_RESERVATION_SCHEMA, "reservation_id": reservation_id,
        "status": "RESERVED", "job_id": job_id, "operation_id": operation_id,
        "project_id": context["project_id"], "host_id": profile.host_id,
        "boot_id": observation.boot_id, "profile_id": profile.profile_id,
        "profile_sha256": profile.sha256, "requested_bytes": admission.requested_bytes,
        "observation_sha256": admission.observation_sha256,
        "at_monotonic_ns": observation.observed_monotonic_ns,
        "monotonic_clock_id": observation.monotonic_clock_id,
        "available_bytes": admission.available_bytes,
        "evidence_classification": admission.evidence_classification,
        "hard_cap_state": admission.hard_cap_state,
    }
    from ._w26_os_resources import _canonical_json
    store.db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                     (job_id, MEMORY_RESERVATION_EVENT, _canonical_json(item)))
    return {"status": "RESERVED", "reservation_id": reservation_id,
            "requested_bytes": admission.requested_bytes, "available_bytes": admission.available_bytes,
            "memory_resource_profile_id": profile.profile_id,
            "memory_resource_profile_sha256": profile.sha256, "host_id": profile.host_id,
            "boot_id": observation.boot_id, "evidence_classification": admission.evidence_classification,
            "hard_cap_state": admission.hard_cap_state, "idempotent": False}


class MemoryResourceCoordinator:
    """Trusted host-wide memory admission using the existing durable job lane."""

    def __init__(self, *, config: TrustedMemoryResourceConfig, store: Any, authority: Any,
                 observation_provider: HostMemoryObservationProvider | None = None,
                 max_observation_age_ns: int = MEMORY_OBSERVATION_MAX_AGE_NS):
        from ._operation_store import OperationStore
        from ._project_authority import ProjectAuthority
        if type(config) is not TrustedMemoryResourceConfig or not config.trusted_startup_source:
            _refuse("MEMORY_PROFILE_UNTRUSTED", "coordinator requires a trusted startup memory profile")
        if not isinstance(store, OperationStore) or not isinstance(authority, ProjectAuthority) \
                or authority.store is not store:
            _refuse("MEMORY_RESERVATION_AUTHORITY_REQUIRED",
                    "memory coordinator requires the existing bound ProjectAuthority and OperationStore")
        if observation_provider is None:
            observation_provider = HostMemoryObservationProvider()
        if type(observation_provider) is not HostMemoryObservationProvider:
            _refuse("MEMORY_OBSERVER_UNVERIFIED", "memory coordinator requires the built-in host observer")
        if type(max_observation_age_ns) is not int or max_observation_age_ns <= 0:
            raise ValueError("max_observation_age_ns must be positive")
        self.config = config
        self.store = store
        self.authority = authority
        self.observation_provider = observation_provider
        self.max_observation_age_ns = max_observation_age_ns

    @property
    def profile(self) -> MemoryResourceProfile:
        return self.config.profile

    def requires_reservation(self, *, project_id: str | None, operation_id: str) -> bool:
        from . import _w26_os_resources as resources

        if not isinstance(operation_id, str) or not operation_id:
            _refuse("MEMORY_OPERATION_EFFECT_UNAVAILABLE", "memory admission requires a canonical operation")
        effect = resources._registered_operation_effect(operation_id)
        if effect is None:
            _refuse("MEMORY_OPERATION_EFFECT_UNAVAILABLE",
                    "configured memory admission cannot classify this operation", operation_id=operation_id)
        if effect not in _MEMORY_PRODUCING_EFFECTS:
            return False
        if not isinstance(project_id, str) or not project_id:
            _refuse("MEMORY_PROJECT_IDENTITY_REQUIRED",
                    "configured memory-producing operations require a project identity",
                    operation_id=operation_id)
        # This also refuses a missing trusted estimate before durable admission.
        self.profile.estimate_for(operation_id)
        return True

    def prepare_queued_job(self, job_id: str) -> dict[str, Any]:
        """Read trusted estimate and fresh native observation before the SQL transaction."""
        from . import _w26_os_resources as resources

        with self.store.lock:
            context = resources._durable_job_context(self.store.db, job_id)
            if context["job_status"] != "QUEUED" or context["operation_status"] != "QUEUED":
                _refuse("MEMORY_RESERVATION_NOT_PREDISPATCH",
                        "memory admission must complete while the durable job is QUEUED",
                        job_status=context["job_status"], operation_status=context["operation_status"])
            estimate = self.profile.estimate_for(context["canonical_operation_id"])
            permission = resources._permission_for_disk_operation(context["canonical_operation_id"])
            resources._authorize_project(self.authority, context["project_id"], permission)
            observation = self.observation_provider.read_observation()
            if observation.host_id != self.profile.host_id or observation.platform != self.profile.platform:
                _refuse("MEMORY_HOST_MISMATCH", "fresh host observation differs from trusted startup profile")
            return {"context": context, "profile": self.profile, "estimate_bytes": estimate,
                    "observation": observation}

    def reserve_prepared_in_transaction(self, job_id: str, prepared: Mapping[str, Any]) -> dict[str, Any]:
        """Append a reservation inside the caller's existing immediate transaction."""
        from . import _w26_os_resources as resources

        if not self.store.db.in_transaction:
            _refuse("MEMORY_RESERVATION_TRANSACTION_REQUIRED",
                    "prepared memory reservation requires the caller's active SQL transaction")
        context = prepared.get("context")
        if not isinstance(context, Mapping):
            _refuse("MEMORY_RESERVATION_PREPARED_STATE_INVALID", "prepared job context is malformed")
        current = resources._durable_job_context(self.store.db, job_id)
        if (current["job_status"] != "QUEUED" or current["operation_status"] != "QUEUED"
                or current["job_id"] != context.get("job_id")
                or current["operation_id"] != context.get("operation_id")
                or current["canonical_operation_id"] != context.get("canonical_operation_id")
                or current["project_id"] != context.get("project_id")):
            _refuse("MEMORY_RESERVATION_NOT_PREDISPATCH",
                    "durable job changed while memory admission was being prepared")
        profile = prepared.get("profile")
        observation = prepared.get("observation")
        if profile is not self.profile or not isinstance(observation, HostMemoryObservation):
            _refuse("MEMORY_RESERVATION_PREPARED_STATE_INVALID",
                    "prepared profile or OS observation is not bound to this coordinator")
        result = _reserve_memory_in_transaction(
            store=self.store, authority=self.authority, job_id=job_id, profile=profile,
            observation=observation, max_age_ns=self.max_observation_age_ns,
        )
        return {**result,
                "operation_id": current["canonical_operation_id"],
                "trusted_estimate_bytes": prepared["estimate_bytes"],
                "evidence_classification": "HOST_OBSERVED_ADMISSION_ONLY"}

    def reserve_queued_job(self, job_id: str, *, prepared: Mapping[str, Any] | None = None) -> dict[str, Any]:
        prepared = prepared or self.prepare_queued_job(job_id)
        with self.store.lock:
            try:
                self.store.db.execute("BEGIN IMMEDIATE")
                result = self.reserve_prepared_in_transaction(job_id, prepared)
                self.store.db.execute("COMMIT")
            except BaseException:
                if self.store.db.in_transaction:
                    self.store.db.execute("ROLLBACK")
                raise
        return {**result, "operation_id": prepared["context"]["canonical_operation_id"],
                "trusted_estimate_bytes": prepared["estimate_bytes"],
                "evidence_classification": "HOST_OBSERVED_ADMISSION_ONLY"}

    def release_terminal_if_reserved(self, job_id: str) -> dict[str, Any] | None:
        from . import _w26_os_resources as resources

        with self.store.lock:
            try:
                self.store.db.execute("BEGIN IMMEDIATE")
                states = _load_memory_reservations(self.store.db)
                matches = [(reservation_id, item)
                           for reservation_id, (bound_job, item) in states.items()
                           if bound_job == job_id]
                if not matches:
                    self.store.db.execute("COMMIT")
                    return None
                if len(matches) != 1:
                    _refuse("MEMORY_RESERVATION_STATE_UNKNOWN", "job has multiple memory reservations")
                reservation_id, item = matches[0]
                if item["status"] == "RELEASED":
                    self.store.db.execute("COMMIT")
                    return {"status": "RELEASED", "reservation_id": reservation_id, "idempotent": True}
                context = resources._durable_job_context(self.store.db, job_id)
                if (context["canonical_operation_id"] != item["operation_id"]
                        or context["project_id"] != item["project_id"]):
                    _refuse("MEMORY_RESERVATION_IDENTITY_CONFLICT",
                            "durable job no longer matches its memory reservation")
                latest = self.store.db.execute(
                    "SELECT j.status AS job_status,o.status AS operation_status,j.metadata AS job_metadata "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if latest is None or latest["job_status"] not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"} \
                        or latest["operation_status"] != latest["job_status"]:
                    self.store.db.execute("ROLLBACK")
                    return {"status": "HELD_NONTERMINAL", "reservation_id": reservation_id,
                            "idempotent": False}
                status = latest["job_status"]
                if status in {"LOST", "EXPIRED"}:
                    # These labels can be written by watchdog/recovery paths;
                    # neither proves that an already-dispatched engine stopped.
                    self.store.db.execute("COMMIT")
                    return {"status": "HELD_QUIESCENCE_UNPROVEN",
                            "reservation_id": reservation_id, "idempotent": False,
                            "domain_status": status}
                if status == "CANCELLED" and not _queued_abort_proves_never_dispatched(
                        self.store.db, job_id):
                    self.store.db.execute("COMMIT")
                    return {"status": "HELD_DISPATCH_UNPROVEN",
                            "reservation_id": reservation_id, "idempotent": False,
                            "domain_status": status}
                released = dict(item)
                released["status"] = "RELEASED"
                from ._w26_os_resources import _canonical_json
                self.store.db.execute("INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                                      (job_id, MEMORY_RESERVATION_EVENT, _canonical_json(released)))
                self.store.db.execute("COMMIT")
            except BaseException:
                if self.store.db.in_transaction:
                    self.store.db.execute("ROLLBACK")
                raise
        return {"status": "RELEASED", "reservation_id": reservation_id, "idempotent": False,
                "evidence_classification": "HOST_OBSERVED_ADMISSION_ONLY"}

    def active_reserved_bytes(self) -> int:
        with self.store.lock:
            total = 0
            for _job_id, item in _load_memory_reservations(self.store.db).values():
                if item["status"] == "RESERVED" and item["host_id"] == self.profile.host_id:
                    total += item["requested_bytes"]
                    if total > SQLITE_INT_MAX:
                        _refuse("MEMORY_RESERVATION_STATE_UNKNOWN",
                                "durable host memory reservations overflow")
            return total


def _read_trusted_config(path: Path) -> tuple[bytes, Mapping[str, Any], str]:
    if not path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".."}:
        _refuse("MEMORY_PROFILE_INVALID", "trusted memory profile path must be absolute and canonical")
    target = Path(os.path.abspath(path))
    if os.name == "nt":
        return _read_windows_config(target)
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        return _read_posix_config(target)
    _refuse("MEMORY_CONFIG_PROVIDER_UNSUPPORTED", "trusted memory profile reader is unsupported on this platform")


def _read_posix_config(target: Path) -> tuple[bytes, Mapping[str, Any], str]:
    parent_fds: list[int] = []
    fd: int | None = None
    try:
        from ._state_backup import _open_posix_directory_chain, _verify_posix_directory_links
        from ._host_scoped_provider import verify_private_path_security
        parent_fds, links, _ = _open_posix_directory_chain(target.parent)
        _verify_posix_directory_links(links)
        parent_info = os.fstat(parent_fds[-1])
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_IMODE(parent_info.st_mode) & 0o022:
            _refuse("MEMORY_PROFILE_SECURITY_INVALID", "profile parent must not be group/other writable")
        parent_security = verify_private_path_security(
            target.parent, directory=True,
            expected_identity={"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)},
        )
        fd = os.open(target.name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                     | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fds[-1])
        before = os.fstat(fd)
        file_security = verify_private_path_security(
            target, directory=False,
            expected_identity={"device": int(before.st_dev), "inode": int(before.st_ino)},
        )
        named = os.stat(target.name, dir_fd=parent_fds[-1], follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named.st_mode)
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
                or (hasattr(os, "geteuid") and before.st_uid not in {os.geteuid(), 0})
                or stat.S_IMODE(before.st_mode) & 0o022
                or not 1 <= before.st_size <= MEMORY_CONFIG_MAX_BYTES):
            _refuse("MEMORY_PROFILE_SECURITY_INVALID", "profile must be a bounded private regular file")
        payload = bytearray()
        while len(payload) <= MEMORY_CONFIG_MAX_BYTES:
            part = os.read(fd, min(32 * 1024, MEMORY_CONFIG_MAX_BYTES + 1 - len(payload)))
            if not part:
                break
            payload.extend(part)
        after = os.fstat(fd)
        named_after = os.stat(target.name, dir_fd=parent_fds[-1], follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
                or len(payload) != before.st_size or len(payload) > MEMORY_CONFIG_MAX_BYTES):
            _refuse("MEMORY_PROFILE_CHANGED", "profile changed during its pinned read")
        _verify_posix_directory_links(links)
        final_security = verify_private_path_security(
            target, directory=False,
            expected_identity={"device": int(after.st_dev), "inode": int(after.st_ino)},
        )
        parent_security_after = verify_private_path_security(
            target.parent, directory=True,
            expected_identity={"device": int(parent_info.st_dev), "inode": int(parent_info.st_ino)},
        )
        if (file_security["security_sha256"] != final_security["security_sha256"]
                or parent_security["security_sha256"] != parent_security_after["security_sha256"]
                or file_security["security_semantics"] != final_security["security_semantics"]):
            _refuse("MEMORY_PROFILE_CHANGED", "profile identity or security changed during the read")
        identity = {"device": int(after.st_dev), "inode": int(after.st_ino),
                    "size_bytes": len(payload), "security_sha256": file_security["security_sha256"]}
        return bytes(payload), identity, str(file_security["security_semantics"])
    finally:
        if fd is not None:
            os.close(fd)
        for item in reversed(parent_fds):
            os.close(item)


def _read_windows_config(target: Path) -> tuple[bytes, Mapping[str, Any], str]:
    from ._host_scoped_provider import verify_private_path_security
    from ._windows_parameter_publish import _API
    api = _API()
    before = verify_private_path_security(target, directory=False)
    chain = api.open_security_path(target.parent, directory=True)
    handle = None
    try:
        handle = api.open_source(chain[-1], target.name)
        identity, size, creation, written, links = api.source_fingerprint(handle)
        expected = {"device": identity[0], "inode": identity[1],
                    "security_sha256": before["security_sha256"]}
        if (before.get("identity") != expected or size <= 0 or size > MEMORY_CONFIG_MAX_BYTES
                or links != 1
                or before.get("security_semantics") != "windows-read-control-owner-dacl-persistent-acls"):
            _refuse("MEMORY_PROFILE_SECURITY_INVALID", "Windows profile handle or ACL cannot be trusted")
        payload = bytearray()
        while len(payload) < size:
            block = api.read_chunk(handle, min(16 * 1024, size - len(payload)))
            if not block:
                _refuse("MEMORY_PROFILE_CHANGED", "profile ended before its pinned length")
            payload.extend(block)
        if api.read_chunk(handle, 1):
            _refuse("MEMORY_PROFILE_CHANGED", "profile grew during its pinned read")
        final = api.source_fingerprint(handle)
        after = verify_private_path_security(target, directory=False, expected_identity=expected)
        if final != (identity, size, creation, written, links) or after.get("security_sha256") != before.get("security_sha256"):
            _refuse("MEMORY_PROFILE_CHANGED", "Windows profile identity or ACL changed during its read")
        return bytes(payload), {"device": identity[0], "inode": identity[1], "size_bytes": size,
                               "security_sha256": before["security_sha256"]}, str(before["security_semantics"])
    finally:
        if handle is not None:
            api.close(handle)
        for item in reversed(chain):
            api.close(item)


class _UnavailableMemoryApi:
    provider_id = "unavailable"

    def read(self):
        _refuse("MEMORY_OBSERVER_UNSUPPORTED", "no verified native memory adapter exists")


__all__ = [
    "HostMemoryObservation", "HostMemoryObservationProvider", "MemoryAdmission",
    "MemoryBudgetPolicy", "MemoryOperationEstimate", "MemoryResourceError",
    "MemoryResourceCoordinator", "MemoryResourceProfile", "TrustedMemoryResourceConfig",
    "MEMORY_OBSERVATION_MAX_AGE_NS", "MEMORY_PROFILE_SCHEMA", "MEMORY_RESERVATION_SCHEMA",
    "assess_memory_admission", "parse_memory_profile_document",
]
