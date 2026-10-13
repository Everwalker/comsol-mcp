"""Read-only native Windows T037 SCM/WFP observation.

All APIs are late-bound.  The adapter never creates or changes a service,
AppContainer profile, WFP object, ACL, account, quota, or process.  A complete
configuration snapshot is evidence for review; it is not an isolation proof.
Unknown ABI values, access failures, external callouts, and incomplete pages
fail closed.  Injected APIs are SOFTWARE_ONLY and cannot create a native binding.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes as C
import hashlib
import json
import math
import ntpath
import os
import re
import struct
import sys
import time
import uuid
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError

MAX_ENUM_PAGE = 256
MAX_ENUM_ROWS = 100_000
MAX_ENUM_PAGES = (MAX_ENUM_ROWS + MAX_ENUM_PAGE - 1) // MAX_ENUM_PAGE
MAX_ENUM_SECONDS = 20
MAX_ENUM_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CONDITIONS = 256
MAX_BLOB_BYTES = 256 * 1024
MAX_NATIVE_TEXT = 32_768
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_GUID_BODY = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
              r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_GUID_RE = re.compile(r"^(?:\{" + _GUID_BODY + r"\}|" + _GUID_BODY + r")$")
_SID_RE = re.compile(r"^S-1-(?:[0-9]{1,2})-(?:[0-9]{1,10})(?:-[0-9]{1,10}){1,14}$", re.I)

# ctypes.wintypes follows the host C ABI on non-Windows Python builds.  On
# Darwin, for example, wintypes.DWORD aliases an eight-byte c_ulong.  Use the
# fixed-width SDK types explicitly so layout checks are meaningful everywhere.
DWORD = C.c_uint32
LONG = C.c_int32
BOOL = C.c_int32
WORD = C.c_uint16
BYTE = C.c_ubyte
HANDLE = C.c_void_p
LPCWSTR = C.c_wchar_p
LPWSTR = C.c_wchar_p


@dataclass
class _WfpBudget:
    started_ns: int
    deadline_ns: int
    pages: int = 0
    pages_by_kind: dict[str, int] | None = None
    rows: int = 0
    row_bytes: int = 0
    kind_count: int = 4

    @classmethod
    def start(cls, *, now_ns: int | None = None, kind_count: int = 4) -> "_WfpBudget":
        if type(kind_count) is not int or kind_count not in {4, 5}:
            _fail("T037_NATIVE_WFP_BUDGET_INVALID", "WFP enumeration kind count is unsupported")
        start = time.monotonic_ns() if now_ns is None else now_ns
        return cls(start, start + MAX_ENUM_SECONDS * 1_000_000_000, pages_by_kind={}, kind_count=kind_count)

    def check_time(self, *, now_ns: int | None = None) -> None:
        current = time.monotonic_ns() if now_ns is None else now_ns
        if current > self.deadline_ns:
            _fail("T037_NATIVE_WFP_TIME_LIMIT", "WFP snapshot exceeded its total monotonic-time bound",
                  max_seconds=MAX_ENUM_SECONDS)

    def add_page(self, kind: str, *, now_ns: int | None = None) -> None:
        self.check_time(now_ns=now_ns)
        self.pages += 1
        if self.pages > MAX_ENUM_PAGES * self.kind_count:
            _fail("T037_NATIVE_WFP_PAGE_LIMIT", "WFP snapshot exceeded its total page bound",
                  max_pages=MAX_ENUM_PAGES * self.kind_count)
        if self.pages_by_kind is None:
            self.pages_by_kind = {}
        self.pages_by_kind[kind] = self.pages_by_kind.get(kind, 0) + 1
        if self.pages_by_kind[kind] > MAX_ENUM_PAGES:
            _fail("T037_NATIVE_WFP_PAGE_LIMIT", f"WFP {kind} enumeration exceeded its page bound",
                  max_pages=MAX_ENUM_PAGES)

    def add_row(self, row: Mapping[str, Any], *, now_ns: int | None = None) -> None:
        self.check_time(now_ns=now_ns)
        self.rows += 1
        if self.rows > MAX_ENUM_ROWS:
            _fail("T037_NATIVE_WFP_ROW_LIMIT", "WFP snapshot exceeded its total record bound",
                  max_records=MAX_ENUM_ROWS)
        try:
            row_bytes = len(_canonical(row))
        except (TypeError, ValueError) as exc:
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP row is not canonical JSON",
                  cause_type=type(exc).__name__)
        self.row_bytes += row_bytes
        if self.row_bytes > MAX_ENUM_TOTAL_BYTES:
            _fail("T037_NATIVE_WFP_BYTE_LIMIT", "WFP snapshot exceeded its canonical byte bound",
                  max_bytes=MAX_ENUM_TOTAL_BYTES)

    def elapsed_ms(self, *, now_ns: int | None = None) -> int:
        current = time.monotonic_ns() if now_ns is None else now_ns
        return max(0, (current - self.started_ns) // 1_000_000)

class NativePolicyError(ExecutionContractError):
    """Typed fail-closed error from the read-only Win32 policy adapter."""


def _fail(code: str, message: str, **details: Any) -> None:
    raise NativePolicyError(code, message, details=details or None, stage="pre_dispatch")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_normalized_fwp_value(value: Any, *, depth: int = 0) -> None:
    """Validate only the normalized, bounded FWP value shapes emitted here."""
    if not isinstance(value, Mapping) or type(value.get("type")) is not int or depth > 2:
        _fail("T037_NATIVE_WFP_ABI_INVALID", "normalized FWP value has an invalid shape")
    kind = value["type"]
    fields = ({0: {"type", "value"}, 1: {"type", "value"}, 2: {"type", "value"},
               3: {"type", "value"}, 4: {"type", "value"}, 5: {"type", "value"},
               6: {"type", "value"}, 7: {"type", "value"}, 8: {"type", "value"},
               9: {"type", "value"}, 10: {"type", "value"}, 11: {"type", "value"},
               12: {"type", "size", "sha256"}, 13: {"type", "value"},
               14: {"type", "size", "sha256"}, 16: {"type", "size", "sha256"},
               17: {"type", "value"}, 18: {"type", "value"},
               256: {"type", "address_u32", "mask_u32"},
               257: {"type", "address", "prefix_length"},
               258: {"type", "low", "high"}}).get(kind)
    if fields is None or set(value) != fields:
        _fail("T037_NATIVE_WFP_ABI_INVALID", "normalized FWP data type/fields are unknown")
    if kind == 0:
        valid = value["value"] is None
    elif kind in {1, 2, 3, 4, 11, 18}:
        raw = value["value"]
        if kind == 11:
            valid = isinstance(raw, str) and bool(re.fullmatch(r"[0-9a-f]{32}", raw))
        elif kind == 18:
            valid = isinstance(raw, str) and bool(re.fullmatch(r"[0-9a-f]{12}", raw))
        else:
            maximum = {1: 0xff, 2: 0xffff, 3: 0xffffffff, 4: 0xffffffffffffffff}[kind]
            valid = type(raw) is int and 0 <= raw <= maximum
    elif kind in {5, 6, 7, 8}:
        raw = value["value"]
        bits = {5: 8, 6: 16, 7: 32, 8: 64}[kind]
        valid = type(raw) is int and -(1 << (bits - 1)) <= raw < (1 << (bits - 1))
    elif kind in {9, 10}:
        valid = type(value["value"]) in {int, float} and math.isfinite(value["value"])
    elif kind in {12, 14, 16}:
        valid = (type(value["size"]) is int and 0 <= value["size"] <= MAX_BLOB_BYTES
                 and isinstance(value["sha256"], str) and bool(_SHA_RE.fullmatch(value["sha256"])))
    elif kind == 13:
        valid = isinstance(value["value"], str) and bool(_SID_RE.fullmatch(value["value"]))
    elif kind == 17:
        valid = isinstance(value["value"], str) and len(value["value"]) <= MAX_NATIVE_TEXT
    elif kind == 256:
        valid = all(type(value[key]) is int and 0 <= value[key] <= 0xffffffff
                    for key in ("address_u32", "mask_u32"))
    elif kind == 257:
        valid = (isinstance(value["address"], str)
                 and bool(re.fullmatch(r"[0-9a-f]{32}", value["address"]))
                 and type(value["prefix_length"]) is int and 0 <= value["prefix_length"] <= 128)
    elif kind == 258:
        _validate_normalized_fwp_value(value["low"], depth=depth + 1)
        _validate_normalized_fwp_value(value["high"], depth=depth + 1)
        valid = True
    else:
        valid = False
    if not valid:
        _fail("T037_NATIVE_WFP_ABI_INVALID", "normalized FWP value has invalid member values",
              data_type=kind)


# Win32 SDK layouts (fwptypes.h/fwpmtypes.h).  The field order is kept next
# to each corresponding SDK structure name and checked on 64-bit Windows at
# bind time.  Pointer-bearing layouts are not used on a non-Windows host.
class GUID(C.Structure):
    _fields_ = [("Data1", C.c_uint32), ("Data2", C.c_uint16), ("Data3", C.c_uint16),
                ("Data4", C.c_ubyte * 8)]

class FWP_BYTE_BLOB(C.Structure):
    _fields_ = [("size", C.c_uint32), ("data", C.POINTER(C.c_ubyte))]

class _FWP_VALUE_UNION(C.Union):
    _fields_ = [("uint8", C.c_uint8), ("uint16", C.c_uint16), ("uint32", C.c_uint32),
                ("uint64", C.POINTER(C.c_uint64)), ("int8", C.c_int8),
                ("int16", C.c_int16), ("int32", C.c_int32),
                ("int64", C.POINTER(C.c_int64)), ("float32", C.c_float),
                ("double64", C.POINTER(C.c_double)), ("byteArray16", C.c_void_p),
                ("byteBlob", C.POINTER(FWP_BYTE_BLOB)), ("sid", C.c_void_p),
                ("sd", C.POINTER(FWP_BYTE_BLOB)), ("tokenInformation", C.c_void_p),
                ("tokenAccessInformation", C.POINTER(FWP_BYTE_BLOB)),
                ("unicodeString", C.c_void_p), ("byteArray6", C.c_void_p),
                ("v4AddrMask", C.c_void_p), ("v6AddrMask", C.c_void_p),
                ("rangeValue", C.c_void_p)]

class FWP_CONDITION_VALUE0(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", C.c_uint32), ("value", _FWP_VALUE_UNION)]

class FWP_VALUE0(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", C.c_uint32), ("value", _FWP_VALUE_UNION)]

class FWPM_DISPLAY_DATA0(C.Structure):
    _fields_ = [("name", C.c_void_p), ("description", C.c_void_p)]

class FWPM_FIELD0(C.Structure):
    _fields_ = [("fieldKey", C.POINTER(GUID)), ("type", C.c_uint32), ("dataType", C.c_uint32)]

class FWPM_LAYER0(C.Structure):
    _fields_ = [("layerKey", GUID), ("displayData", FWPM_DISPLAY_DATA0),
                ("flags", DWORD), ("numFields", DWORD), ("field", C.POINTER(FWPM_FIELD0)),
                ("defaultSubLayerKey", GUID), ("layerId", WORD)]

class FWPM_FILTER_CONDITION0(C.Structure):
    _fields_ = [("fieldKey", GUID), ("matchType", C.c_uint32),
                ("conditionValue", FWP_CONDITION_VALUE0)]

class FWPM_ACTION_UNION(C.Union):
    _fields_ = [("calloutKey", GUID)]

class FWPM_ACTION0(C.Structure):
    _anonymous_ = ("action",)
    _fields_ = [("type", C.c_uint32), ("action", FWPM_ACTION_UNION)]

class FWPM_FILTER_CONTEXT_UNION(C.Union):
    _fields_ = [("rawContext", C.c_uint64), ("providerContextKey", GUID)]

class FWPM_FILTER0(C.Structure):
    _anonymous_ = ("context",)
    _fields_ = [("filterKey", GUID), ("displayData", FWPM_DISPLAY_DATA0),
                ("flags", C.c_uint32), ("providerKey", C.POINTER(GUID)),
                ("providerData", FWP_BYTE_BLOB), ("layerKey", GUID),
                ("subLayerKey", GUID), ("weight", FWP_VALUE0),
                ("numFilterConditions", C.c_uint32),
                ("filterCondition", C.POINTER(FWPM_FILTER_CONDITION0)),
                ("action", FWPM_ACTION0), ("context", FWPM_FILTER_CONTEXT_UNION),
                ("reserved", C.POINTER(GUID)), ("filterId", C.c_uint64),
                ("effectiveWeight", FWP_VALUE0)]

class FWPM_PROVIDER0(C.Structure):
    _fields_ = [("providerKey", GUID), ("displayData", FWPM_DISPLAY_DATA0),
                ("flags", C.c_uint32), ("providerData", FWP_BYTE_BLOB),
                ("serviceName", C.c_void_p)]

class FWPM_SUBLAYER0(C.Structure):
    _fields_ = [("subLayerKey", GUID), ("displayData", FWPM_DISPLAY_DATA0),
                ("flags", C.c_uint32), ("providerKey", C.POINTER(GUID)),
                ("providerData", FWP_BYTE_BLOB), ("weight", C.c_uint16)]

class FWPM_PROVIDER_CONTEXT0(C.Structure):
    """Opaque pointer target; provider-context semantics remain unclassified."""
    pass

class FWPM_RANGE0(C.Structure):
    _fields_ = [("valueLow", FWP_VALUE0), ("valueHigh", FWP_VALUE0)]

class FWP_V4_ADDR_AND_MASK(C.Structure):
    _fields_ = [("addr", C.c_uint32), ("mask", C.c_uint32)]

class FWP_V6_ADDR_AND_MASK(C.Structure):
    _fields_ = [("addr", C.c_ubyte * 16), ("prefixLength", C.c_uint8)]

class FWP_BYTE_ARRAY16(C.Structure):
    _fields_ = [("byteArray16", C.c_ubyte * 16)]

class FWP_BYTE_ARRAY6(C.Structure):
    _fields_ = [("byteArray6", C.c_ubyte * 6)]


def _bounded_utf16(pointer: Any, label: str, *, nullable: bool = False) -> str | None:
    """Read a Win32 UTF-16 pointer with a fixed code-unit bound."""
    if not pointer:
        if nullable:
            return None
        _fail("T037_NATIVE_ABI_INVALID", f"{label} is null")
    address = int(pointer) if isinstance(pointer, int) else C.cast(pointer, C.c_void_p).value
    if not address:
        if nullable:
            return None
        _fail("T037_NATIVE_ABI_INVALID", f"{label} is null")
    units = bytearray()
    for index in range(MAX_NATIVE_TEXT + 1):
        value = C.c_uint16.from_address(address + index * 2).value
        if value == 0:
            try:
                return bytes(units).decode("utf-16-le", errors="strict")
            except UnicodeDecodeError as exc:
                _fail("T037_NATIVE_ABI_INVALID", f"{label} is malformed UTF-16")
        if index == MAX_NATIVE_TEXT:
            break
        units.extend(value.to_bytes(2, "little"))
    _fail("T037_NATIVE_ABI_INVALID", f"{label} exceeds its UTF-16 bound",
          max_code_units=MAX_NATIVE_TEXT)


def canonical_security_descriptor(value: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Normalize a self-relative OWNER/GROUP/DACL descriptor without losing ACE order.

    Security descriptors may place equivalent components at different byte
    offsets.  The canonical value binds descriptor/control flags, owner and
    group SID bytes, DACL absent/null state, ACL revision, each ordered ACE's
    exact bytes, and any ACL tail bytes.  It intentionally does not claim that
    the descriptor is a safe allowlist; callers compare its digest to trusted
    profile intent and retain UNVERIFIED until policy semantics are issued.
    """
    raw = bytes(value)
    if not 20 <= len(raw) <= MAX_BLOB_BYTES:
        raise ValueError("security descriptor length is outside the supported bound")
    revision, sbz1, control, owner_offset, group_offset, _sacl_offset, dacl_offset = struct.unpack_from(
        "<BBHIIII", raw, 0)
    if revision != 1 or not control & 0x8000:
        raise ValueError("security descriptor is not a supported self-relative revision-1 descriptor")
    if _sacl_offset != 0:
        raise ValueError("SACL data was not requested and cannot be silently omitted from the normalized descriptor")

    def sid_at(offset: int, label: str) -> str | None:
        if offset == 0:
            return None
        if offset < 20 or offset + 8 > len(raw) or offset % 4:
            raise ValueError(f"{label} SID offset is invalid")
        sid_revision, subauth_count = raw[offset], raw[offset + 1]
        size = 8 + subauth_count * 4
        if sid_revision != 1 or subauth_count > 15 or offset + size > len(raw):
            raise ValueError(f"{label} SID body is invalid")
        return raw[offset:offset + size].hex()

    owner = sid_at(owner_offset, "owner")
    group = sid_at(group_offset, "group")
    dacl_present = bool(control & 0x0004)
    dacl: dict[str, Any]
    if not dacl_present:
        if dacl_offset != 0:
            raise ValueError("DACL offset is set while SE_DACL_PRESENT is clear")
        dacl = {"present": False, "null": False, "revision": None, "aces": [], "tail_hex": ""}
    elif dacl_offset == 0:
        dacl = {"present": True, "null": True, "revision": None, "aces": [], "tail_hex": ""}
    else:
        if dacl_offset < 20 or dacl_offset + 8 > len(raw) or dacl_offset % 4:
            raise ValueError("DACL offset is invalid")
        acl_revision, acl_sbz1, acl_size, ace_count, acl_sbz2 = struct.unpack_from(
            "<BBHHH", raw, dacl_offset)
        if acl_revision not in {2, 4} or acl_size < 8 or dacl_offset + acl_size > len(raw) or ace_count > 4096:
            raise ValueError("DACL header is outside the supported bounds")
        cursor = dacl_offset + 8
        end = dacl_offset + acl_size
        aces: list[str] = []
        for _ in range(ace_count):
            if cursor + 4 > end:
                raise ValueError("DACL ACE header is truncated")
            ace_size = struct.unpack_from("<H", raw, cursor + 2)[0]
            if ace_size < 4 or ace_size % 4 or cursor + ace_size > end:
                raise ValueError("DACL ACE size is invalid")
            aces.append(raw[cursor:cursor + ace_size].hex())
            cursor += ace_size
        dacl = {"present": True, "null": False, "revision": acl_revision,
                "sbz1": acl_sbz1, "sbz2": acl_sbz2, "aces": aces,
                "tail_hex": raw[cursor:end].hex()}
    return {"revision": revision, "sbz1": sbz1, "control": control,
            "owner_sid_bytes": owner, "group_sid_bytes": group, "dacl": dacl}


def canonical_security_descriptor_sha256(value: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(_canonical(canonical_security_descriptor(value))).hexdigest()


@dataclass(frozen=True)
class NativePolicyObservation:
    """Immutable metadata for one actual or synthetic native-adapter read."""
    state: str
    evidence_classification: str
    host_fingerprint_sha256: str | None
    profile_sha256: str | None
    scm_snapshot_sha256: str | None
    wfp_snapshot_sha256: str | None
    wfp_counts: Mapping[str, Any]
    service_pid: int | None
    reasons: tuple[str, ...]
    _native_observation_attestation: object | None = None
    effective_catalog_sha256: str | None = None

    def __post_init__(self) -> None:
        # This module only produces observations, never a launch proof. Keep
        # that invariant in the value type too, so caller JSON cannot
        # manufacture a VERIFIED result.
        if self.state != "UNVERIFIED":
            raise ValueError("native policy observations cannot issue a verified binding")
        if self.evidence_classification not in {"SOFTWARE_ONLY", "NATIVE_READ_ONLY_OBSERVATION"}:
            raise ValueError("native policy observation classification is invalid")
        for value in (self.host_fingerprint_sha256, self.profile_sha256,
                      self.scm_snapshot_sha256, self.wfp_snapshot_sha256, self.effective_catalog_sha256):
            if value is not None and (not isinstance(value, str) or not _SHA_RE.fullmatch(value)):
                raise ValueError("native policy observation digest is malformed")
        if self.service_pid is not None and (type(self.service_pid) is not int or self.service_pid <= 0):
            raise ValueError("native policy observation PID is malformed")
        if not isinstance(self.reasons, tuple) or any(not isinstance(item, str) or not item for item in self.reasons):
            raise ValueError("native policy observation reasons are malformed")
        if not isinstance(self.wfp_counts, Mapping):
            raise ValueError("native policy observation counts are malformed")
        try:
            _canonical(dict(self.wfp_counts))
        except (TypeError, ValueError) as exc:
            raise ValueError("native policy observation counts are not canonical JSON") from exc
        if (self.evidence_classification == "NATIVE_READ_ONLY_OBSERVATION"
                and self._native_observation_attestation is not _NATIVE_OBSERVATION_ATTESTATION):
            raise ValueError("native observation classification requires the private producer seal")
        if (self.evidence_classification == "SOFTWARE_ONLY"
                and self._native_observation_attestation is not None):
            raise ValueError("software observations cannot carry a native producer seal")

    @property
    def verified(self) -> bool:
        return False

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "evidence_classification": self.evidence_classification,
            "host_fingerprint_sha256": self.host_fingerprint_sha256,
            "profile_sha256": self.profile_sha256,
            "scm_snapshot_sha256": self.scm_snapshot_sha256,
            "wfp_snapshot_sha256": self.wfp_snapshot_sha256,
            "effective_catalog_sha256": self.effective_catalog_sha256,
            "wfp_counts": dict(self.wfp_counts),
            "service_pid": self.service_pid,
            "reasons": list(self.reasons),
            "policy_binding": "NOT_ISSUED",
        }


_NATIVE_OBSERVATION_ATTESTATION = object()


class WindowsNativePolicyAdapter:
    """Collect read-only SCM/WFP/process observations; never attest isolation."""

    def __init__(self, *, api: Any | None = None, platform_name: str | None = None):
        self._injected = api is not None
        self.platform_name = platform_name or sys.platform
        if api is None:
            if os.name != "nt" or self.platform_name not in {"win32", "nt", "windows"}:
                _fail("T037_NATIVE_UNSUPPORTED", "SCM/WFP native observation requires Windows")
            api = CtypesWindowsPolicyApi()
        self.api = api

    @staticmethod
    def _guid_key(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip("{}").casefold()

    @staticmethod
    def _process_identity(process: Mapping[str, Any]) -> dict[str, Any]:
        token = process.get("token")
        if not isinstance(token, Mapping):
            _fail("T037_NATIVE_PROCESS_ABI_INVALID", "process token observation is missing")
        keys = {"state", "user_sid", "group_sids", "logon_sids", "is_appcontainer",
                "appcontainer_sid", "capability_sids", "integrity_sid", "integrity_attributes"}
        if set(token) != keys:
            _fail("T037_NATIVE_PROCESS_ABI_INVALID", "process token observation has unknown or missing fields")
        if (token.get("state") != "OBSERVED" or not isinstance(token.get("user_sid"), str)
                or not isinstance(token.get("group_sids"), list)
                or any(not isinstance(item, str) for item in token["group_sids"])
                or not isinstance(token.get("logon_sids"), list)
                or any(not isinstance(item, str) for item in token["logon_sids"])
                or type(token.get("is_appcontainer")) is not bool
                or (token.get("appcontainer_sid") is not None and not isinstance(token.get("appcontainer_sid"), str))
                or not isinstance(token.get("capability_sids"), list)
                or any(not isinstance(item, str) for item in token["capability_sids"])
                or not isinstance(token.get("integrity_sid"), str)
                or type(token.get("integrity_attributes")) is not int):
            _fail("T037_NATIVE_PROCESS_ABI_INVALID", "process token observation has invalid value types")
        pid = process.get("pid")
        birth = process.get("birth_filetime_100ns")
        exit_time = process.get("exit_filetime_100ns")
        image = process.get("image_path")
        if (type(pid) is not int or pid <= 0 or type(birth) is not int or birth <= 0
                or (exit_time is not None and (type(exit_time) is not int or exit_time <= 0))
                or not isinstance(image, str) or not image or len(image) > MAX_NATIVE_TEXT):
            _fail("T037_NATIVE_PROCESS_ABI_INVALID", "process PID/birth/image observation is malformed")
        return {"pid": pid, "birth_filetime_100ns": birth,
                "exit_filetime_100ns": exit_time, "image_path": ntpath.normcase(ntpath.normpath(image)),
                "token": dict(token), "job_membership_any": process.get("job_membership_any"),
                "job_membership_exact": process.get("job_membership_exact"),
                "job": process.get("job")}

    @staticmethod
    def _require_native_process_observer(process_observer: Any) -> None:
        # A caller-supplied process fixture must never be labelled as native
        # evidence just because SCM/WFP calls use real DLLs. Production enters
        # through WindowsT037Observer, whose exact API object binds process,
        # token, image, birth time, and (currently unavailable) Job semantics.
        from ._windows_t037 import WindowsT037Observer, _Win32ReadOnlyApi
        if (type(process_observer) is not WindowsT037Observer
                or type(getattr(process_observer, "api", None)) is not _Win32ReadOnlyApi):
            _fail("T037_NATIVE_PROCESS_OBSERVER_UNTRUSTED",
                  "native SCM/WFP observation requires the production WindowsT037Observer and Win32 API")

    def observe(self, profile: Any, *, current_host_fingerprint: str,
                process_observer: Any | None = None) -> NativePolicyObservation:
        policy = getattr(profile, "windows_policy", None)
        if policy is None:
            _fail("T037_NATIVE_PROFILE_V2_REQUIRED", "native policy observation requires trusted Windows profile v2")
        if self._injected:
            observed_host = current_host_fingerprint
        else:
            from ._windows_t037 import windows_host_fingerprint
            from ._windows_owned_resources import registry_for
            observed_host = windows_host_fingerprint(resource_registry=registry_for(self.api))
            if observed_host != current_host_fingerprint:
                _fail("T037_NATIVE_HOST_MISMATCH", "caller host fingerprint differs from the native host identity")
        if observed_host != profile.host_fingerprint_sha256:
            _fail("T037_NATIVE_HOST_MISMATCH", "live host fingerprint differs from v2 profile")
        if not self._injected:
            self._require_native_process_observer(process_observer)

        scm_keys = {"service_name", "service_start_name", "service_start_sid", "service_sid_type",
                    "service_sid", "state", "process_id", "binary_path", "service_binary_image_path",
                    "service_binary_argv_sha256", "service_security_sha256", "service_security_semantics"}
        wfp_keys = {"state", "counts", "limits", "snapshot_sha256", "reasons",
                    "providers", "sublayers", "filters", "provider_contexts"}

        def service_snapshot() -> dict[str, Any]:
            row = self.api.observe_service(policy.service_name)
            if not isinstance(row, Mapping) or set(row) != scm_keys:
                _fail("T037_NATIVE_SCM_ABI_INVALID", "SCM adapter returned an incomplete or unknown record")
            string_keys = scm_keys - {"process_id"}
            if (any(not isinstance(row[key], str) or len(row[key]) > MAX_NATIVE_TEXT for key in string_keys)
                    or type(row["process_id"]) is not int or row["process_id"] < 0
                    or not _SHA_RE.fullmatch(row["service_binary_argv_sha256"])
                    or not _SHA_RE.fullmatch(row["service_security_sha256"])):
                _fail("T037_NATIVE_SCM_ABI_INVALID", "SCM adapter returned malformed identity fields")
            return dict(row)

        scm_before = service_snapshot()
        proc_before = None
        if process_observer is not None and type(scm_before["process_id"]) is int and scm_before["process_id"] > 0:
            proc_before = self._process_identity(process_observer.observe_process(scm_before["process_id"]))
        wfp = self.api.observe_wfp_snapshot()
        if not isinstance(wfp, Mapping) or set(wfp) != wfp_keys:
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP adapter returned an incomplete or unknown snapshot")
        required_counts = {"providers", "sublayers", "filters", "provider_contexts", "conditions", "pages",
                           "pages_by_kind", "total_records", "total_bytes", "elapsed_ms"}
        counts = wfp.get("counts")
        limits = wfp.get("limits")
        if (not isinstance(counts, Mapping) or set(counts) != required_counts
                or not isinstance(counts.get("pages_by_kind"), Mapping)
                or set(counts["pages_by_kind"]) != {"Provider", "SubLayer", "Filter", "ProviderContext"}
                or not isinstance(limits, Mapping)
                or set(limits) != {"max_page_size", "max_pages_per_kind", "max_total_pages",
                                   "max_total_records", "max_total_bytes", "max_elapsed_ms"}
                or any(type(counts[key]) is not int or counts[key] < 0
                       for key in required_counts - {"pages_by_kind"})
                or any(type(value) is not int or value < 0 for value in counts["pages_by_kind"].values())
                or any(type(value) is not int or value <= 0 for value in limits.values())):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot bounds/counters are malformed")
        if (counts["total_records"] != counts["providers"] + counts["sublayers"] + counts["filters"]
                + counts["provider_contexts"]
                or counts["total_records"] > limits["max_total_records"]
                or counts["total_bytes"] > limits["max_total_bytes"]
                or counts["elapsed_ms"] > limits["max_elapsed_ms"]
                or counts["pages"] > limits["max_total_pages"]
                or sum(counts["pages_by_kind"].values()) != counts["pages"]
                or not _SHA_RE.fullmatch(wfp.get("snapshot_sha256", ""))):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot did not satisfy its declared completeness bounds")
        if not all(isinstance(wfp.get(key), list) for key in
                   ("reasons", "providers", "sublayers", "filters", "provider_contexts")):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot arrays are malformed")
        if any(not isinstance(item, str) for item in wfp["reasons"]):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot reasons are malformed")
        if any(not isinstance(item, Mapping) or set(item) != {"state"}
               or item.get("state") != "PRESENT_UNCLASSIFIED"
               for item in wfp["provider_contexts"]):
            _fail("T037_NATIVE_WFP_ABI_INVALID",
                  "provider-context snapshot rows must remain opaque and explicitly unclassified")
        expected_limits = {"max_page_size": MAX_ENUM_PAGE, "max_pages_per_kind": MAX_ENUM_PAGES,
                           "max_total_pages": MAX_ENUM_PAGES * 4, "max_total_records": MAX_ENUM_ROWS,
                           "max_total_bytes": MAX_ENUM_TOTAL_BYTES,
                           "max_elapsed_ms": MAX_ENUM_SECONDS * 1000}
        if dict(limits) != expected_limits:
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP adapter changed the compiled snapshot bounds")
        if (counts["providers"] != len(wfp["providers"])
                or counts["sublayers"] != len(wfp["sublayers"])
                or counts["filters"] != len(wfp["filters"])
                or counts["provider_contexts"] != len(wfp["provider_contexts"])
                or counts["conditions"] != sum(
                    len(row.get("conditions", [])) for row in wfp["filters"] if isinstance(row, Mapping))):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot counts differ from its returned rows")
        if any(counts["pages_by_kind"][kind] > MAX_ENUM_PAGES
               for kind in ("Provider", "SubLayer", "Filter", "ProviderContext")):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP per-kind page counter exceeds its compiled bound")
        if any(counts["pages_by_kind"][kind] < 1
               for kind in ("Provider", "SubLayer", "Filter", "ProviderContext")):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot omitted a required complete enumeration")
        filter_fields = {"filter_key", "display_data", "provider_key", "layer_key", "sublayer_key",
                         "flags", "filter_id", "action_type", "action_callout_key", "context", "weight",
                         "effective_weight", "has_provider_context", "provider_data_sha256", "conditions",
                         "security_sha256"}
        for row in wfp["filters"]:
            if not isinstance(row, Mapping) or set(row) != filter_fields:
                _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP filter row is malformed")
            display = row.get("display_data")
            context = row.get("context")
            if (any(not isinstance(row.get(field), str) for field in
                    ("filter_key", "layer_key", "sublayer_key", "provider_data_sha256", "security_sha256"))
                    or not _GUID_RE.fullmatch(row["filter_key"])
                    or not _GUID_RE.fullmatch(row["layer_key"])
                    or not _GUID_RE.fullmatch(row["sublayer_key"])
                    or not _SHA_RE.fullmatch(row["provider_data_sha256"])
                    or not _SHA_RE.fullmatch(row["security_sha256"])
                    or (row.get("provider_key") is not None and
                        (not isinstance(row.get("provider_key"), str) or not _GUID_RE.fullmatch(row["provider_key"])))
                    or (row.get("action_callout_key") is not None and
                        (not isinstance(row.get("action_callout_key"), str)
                         or not _GUID_RE.fullmatch(row["action_callout_key"])))
                    or type(row.get("flags")) is not int or not 0 <= row["flags"] <= 0xffffffff
                    or type(row.get("filter_id")) is not int or not 0 <= row["filter_id"] <= 0xffffffffffffffff
                    or type(row.get("action_type")) is not int or not 0 <= row["action_type"] <= 0xffffffff
                    or type(row.get("has_provider_context")) is not bool
                    or row["has_provider_context"] != bool(
                        row["flags"] & CtypesWindowsPolicyApi.FWP_FILTER_FLAG_HAS_PROVIDER_CONTEXT)
                    or not isinstance(display, Mapping) or set(display) != {"name", "description"}
                    or not isinstance(display.get("name"), str) or not display["name"]
                    or any(value is not None and (not isinstance(value, str) or len(value) > MAX_NATIVE_TEXT)
                           for value in display.values())
                    or not isinstance(row.get("conditions"), list)
                    or len(row["conditions"]) > MAX_CONDITIONS
                    or (row["has_provider_context"] and not isinstance(context, str))
                    or (not row["has_provider_context"] and
                        (not isinstance(context, Mapping) or set(context) != {"raw_context"}
                         or type(context.get("raw_context")) is not int
                         or not 0 <= context["raw_context"] <= 0xffffffffffffffff))):
                _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP filter row has invalid identity/context fields")
            if row["has_provider_context"] and not re.fullmatch(
                    r"provider-context:\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}", context):
                _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP provider-context reference is malformed")
            try:
                _validate_normalized_fwp_value(row.get("weight"))
                _validate_normalized_fwp_value(row.get("effective_weight"))
            except NativePolicyError:
                raise
            for condition in row["conditions"]:
                if (not isinstance(condition, Mapping)
                        or set(condition) != {"field_key", "match_type", "condition_value"}
                        or not isinstance(condition.get("field_key"), str)
                        or not _GUID_RE.fullmatch(condition["field_key"])
                        or type(condition.get("match_type")) is not int
                        or not 0 <= condition["match_type"] <= 12
                        or not isinstance(condition.get("condition_value"), Mapping)):
                    _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP filter condition is malformed or unknown")
                _validate_normalized_fwp_value(condition["condition_value"])
        for kind, field, key_name in (("Provider", "providers", "provider_key"),
                                      ("SubLayer", "sublayers", "sublayer_key")):
            for row in wfp[field]:
                required = ({"provider_key", "display_data", "flags", "provider_data_sha256", "service_name",
                             "security_sha256"} if kind == "Provider" else
                            {"sublayer_key", "display_data", "flags", "provider_key", "provider_data_sha256",
                             "weight", "security_sha256"})
                if not isinstance(row, Mapping) or set(row) != required:
                    _fail("T037_NATIVE_WFP_ABI_INVALID", f"WFP {kind} row is malformed")
                display = row.get("display_data")
                if (not isinstance(row.get(key_name), str) or not _GUID_RE.fullmatch(row[key_name])
                        or not _SHA_RE.fullmatch(row.get("security_sha256", ""))
                        or not _SHA_RE.fullmatch(row.get("provider_data_sha256", ""))
                        or type(row.get("flags")) is not int or not 0 <= row["flags"] <= 0xffffffff
                        or not isinstance(display, Mapping) or set(display) != {"name", "description"}
                        or not isinstance(display.get("name"), str) or not display["name"]
                        or any(value is not None and (not isinstance(value, str) or len(value) > MAX_NATIVE_TEXT)
                               for value in display.values())):
                    _fail("T037_NATIVE_WFP_ABI_INVALID", f"WFP {kind} identity/security fields are malformed")
                if kind == "Provider":
                    service_name = row.get("service_name")
                    if service_name is not None and (not isinstance(service_name, str)
                                                      or len(service_name) > MAX_NATIVE_TEXT):
                        _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP provider service name is malformed")
                else:
                    provider_key = row.get("provider_key")
                    if ((provider_key is not None and (not isinstance(provider_key, str)
                                                       or not _GUID_RE.fullmatch(provider_key)))
                            or type(row.get("weight")) is not int or not 0 <= row["weight"] <= 0xffff):
                        _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP sublayer provider/weight is malformed")
        if (len({self._guid_key(row["filter_key"]) for row in wfp["filters"]}) != len(wfp["filters"])
                or len({self._guid_key(row["provider_key"]) for row in wfp["providers"]}) != len(wfp["providers"])
                or len({self._guid_key(row["sublayer_key"]) for row in wfp["sublayers"]}) != len(wfp["sublayers"])):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP snapshot contains duplicate object keys")
        canonical_snapshot = {
            "providers": sorted(wfp["providers"], key=lambda item: item["provider_key"]),
            "sublayers": sorted(wfp["sublayers"], key=lambda item: item["sublayer_key"]),
            "filters": sorted(wfp["filters"], key=lambda item: (item["layer_key"], item["filter_id"])),
            "provider_contexts": wfp["provider_contexts"],
        }
        canonical_bytes = _canonical(canonical_snapshot)
        if (len(canonical_bytes) != counts["total_bytes"]
                or _sha(canonical_snapshot) != wfp["snapshot_sha256"]):
            _fail("T037_NATIVE_WFP_ABI_INVALID",
                  "WFP rows or total-byte count do not match their exact snapshot digest")
        effective_observation = None
        if callable(getattr(self.api, "observe_wfp_policy_snapshot", None)):
            from ._windows_t037_effective import observe_effective_wfp
            effective_observation = observe_effective_wfp(profile, self.api)
        proc_after = None
        if process_observer is not None and type(scm_before["process_id"]) is int and scm_before["process_id"] > 0:
            proc_after = self._process_identity(process_observer.observe_process(scm_before["process_id"]))
        scm_after = service_snapshot()

        reasons: list[str] = []
        if effective_observation is not None:
            reasons.extend(effective_observation.reasons)
        if scm_before != scm_after:
            reasons.append("SCM_CONFIGURATION_OR_PID_CHANGED_DURING_POLICY_READ")
        scm = scm_after
        try:
            interactive_sids = self.api.observe_active_interactive_sids()
            if (not isinstance(interactive_sids, (tuple, list))
                    or any(not isinstance(item, str) for item in interactive_sids)):
                raise ValueError("interactive session adapter returned an invalid SID list")
            if profile.interactive_account_sid.casefold() not in {item.casefold() for item in interactive_sids}:
                reasons.append("INTERACTIVE_USER_IDENTITY_MISMATCH_OR_UNAVAILABLE")
            if len({item.casefold() for item in interactive_sids}) > 1:
                reasons.append("MULTIPLE_ACTIVE_INTERACTIVE_IDENTITIES_UNRESOLVED")
        except Exception:
            reasons.append("INTERACTIVE_SESSION_OBSERVATION_UNAVAILABLE")
        if scm["service_name"].casefold() != policy.service_name.casefold():
            reasons.append("SCM_SERVICE_NAME_MISMATCH")
        if scm["service_start_name"].casefold() != policy.service_start_name.casefold():
            reasons.append("SCM_SERVICE_START_NAME_MISMATCH")
        if str(scm["service_start_sid"]).casefold() != policy.service_logon_sid.casefold():
            reasons.append("SCM_SERVICE_LOGON_SID_MISMATCH")
        if scm["service_sid_type"] != policy.service_sid_type:
            reasons.append("SCM_SERVICE_SID_TYPE_MISMATCH")
        if str(scm["service_sid"]).casefold() != policy.service_sid.casefold():
            reasons.append("SCM_SERVICE_SID_MISMATCH")
        if scm["state"] != "RUNNING" or type(scm["process_id"]) is not int or scm["process_id"] <= 0:
            reasons.append("SCM_SERVICE_PROCESS_UNAVAILABLE")
        if scm["service_security_semantics"] != "OWNER_DACL_CANONICAL_ACE_ORDER_READ_CONTROL":
            reasons.append("SCM_SERVICE_SECURITY_UNVERIFIED")

        launcher_images = [item for item in profile.launch_images if item.role == "launcher"]
        if len(launcher_images) != 1:
            _fail("T037_NATIVE_PROFILE_INVALID", "v2 profile must contain exactly one SCM launcher image")
        launcher = launcher_images[0]
        expected_image = ntpath.normcase(ntpath.normpath(launcher.path))
        if (ntpath.normcase(ntpath.normpath(scm["service_binary_image_path"])) != expected_image
                or scm["service_binary_argv_sha256"] != launcher.argv_sha256):
            reasons.append("SCM_BINARY_IMAGE_OR_ARGV_MISMATCH")

        if wfp["state"] != "COMPLETE_READ_ONLY":
            reasons.append("WFP_SNAPSHOT_INCOMPLETE_OR_UNVERIFIED")
        reasons.extend(wfp["reasons"])
        if wfp["provider_contexts"] and "WFP_PROVIDER_CONTEXT_ROWS_UNCLASSIFIED" not in reasons:
            reasons.append("WFP_PROVIDER_CONTEXT_ROWS_UNCLASSIFIED")
        if any(row["flags"] != 0 for row in (*wfp["providers"], *wfp["sublayers"])):
            reasons.append("WFP_PROVIDER_OR_SUBLAYER_FLAGS_NOT_CLASSIFIED")
        if any(row["flags"] & ~0x7f for row in wfp["filters"]):
            reasons.append("WFP_UNKNOWN_FILTER_FLAGS_PRESENT")
        if any(row["flags"] & 0x67 for row in wfp["filters"]):
            reasons.append("WFP_FILTER_LIFETIME_OR_DISABLED_FLAGS_UNRESOLVED")
        if any(row["has_provider_context"] for row in wfp["filters"]):
            reasons.append("WFP_PROVIDER_CONTEXT_EFFECTS_UNRESOLVED")
        if any(row["action_type"] not in {
                CtypesWindowsPolicyApi.FWP_ACTION_BLOCK,
                CtypesWindowsPolicyApi.FWP_ACTION_PERMIT} for row in wfp["filters"]):
            reasons.append("WFP_EXTERNAL_CALLOUT_OR_UNKNOWN_ACTION_PRESENT")
        if any(row["flags"] & (CtypesWindowsPolicyApi.FWP_FILTER_FLAG_CLEAR_ACTION_RIGHT |
                               CtypesWindowsPolicyApi.FWP_FILTER_FLAG_PERMIT_IF_CALLOUT_UNREGISTERED)
               for row in wfp["filters"]):
            reasons.append("WFP_FILTER_RIGHTS_OR_CALLOUT_FALLBACK_UNRESOLVED")
        if any(row["conditions"] for row in wfp["filters"]):
            reasons.append("WFP_CONDITION_FIELD_SEMANTICS_NOT_CLASSIFIED")
        reasons.append("WFP_LAYER_CATALOG_NOT_ENUMERATED")
        actual_filter_keys = {self._guid_key(row.get("filter_key")) for row in wfp["filters"]
                              if isinstance(row, Mapping)}
        expected_filter_keys = {self._guid_key(item) for item in policy.wfp_filter_keys}
        if not expected_filter_keys.issubset(actual_filter_keys):
            reasons.append("WFP_CONFIGURED_FILTER_SET_MISMATCH")
        providers = {self._guid_key(row.get("provider_key")) for row in wfp["providers"]
                     if isinstance(row, Mapping)}
        sublayers = {self._guid_key(row.get("sublayer_key")) for row in wfp["sublayers"]
                     if isinstance(row, Mapping)}
        expected_provider = self._guid_key(policy.wfp_provider_key)
        expected_sublayer = self._guid_key(policy.wfp_sublayer_key)
        if expected_provider not in providers or expected_sublayer not in sublayers:
            reasons.append("WFP_CONFIGURED_PROVIDER_OR_SUBLAYER_MISSING")
        allowed_layers = {self._guid_key(grant.layer_key) for grant in policy.wfp_grants}
        for row in wfp["filters"]:
            if not isinstance(row, Mapping):
                reasons.append("WFP_FILTER_ROW_UNVERIFIED")
                continue
            key = self._guid_key(row.get("filter_key"))
            if key in expected_filter_keys:
                if (self._guid_key(row.get("provider_key")) != expected_provider
                        or self._guid_key(row.get("sublayer_key")) != expected_sublayer
                        or self._guid_key(row.get("layer_key")) not in allowed_layers
                        or row.get("action_type") != CtypesWindowsPolicyApi.FWP_ACTION_PERMIT
                        or row.get("has_provider_context") is not False):
                    reasons.append("WFP_CONFIGURED_FILTER_IDENTITY_OR_ACTION_MISMATCH")
            elif self._guid_key(row.get("layer_key")) in allowed_layers:
                reasons.append("WFP_EXTERNAL_FILTER_AT_CONFIGURED_LAYER_UNRESOLVED")

        # The native process is sampled on both sides of the WFP snapshot.  It
        # must have a stable PID, process birth, image and token; a SID alone
        # is not a binding.  No trusted Job handle is currently wired here.
        if proc_before is None or proc_after is None:
            reasons.append("SCM_PROCESS_NATIVE_OBSERVATION_UNAVAILABLE")
            reasons.append("EXACT_SERVER_WORKER_JOB_BINDING_UNAVAILABLE")
        else:
            identity_before = {key: proc_before[key] for key in (
                "pid", "birth_filetime_100ns", "exit_filetime_100ns", "image_path", "token",
                "job_membership_any", "job_membership_exact", "job")}
            identity_after = {key: proc_after[key] for key in (
                "pid", "birth_filetime_100ns", "exit_filetime_100ns", "image_path", "token",
                "job_membership_any", "job_membership_exact", "job")}
            if identity_before != identity_after or proc_before["pid"] != scm_before["process_id"]:
                reasons.append("SCM_PROCESS_PID_BIRTH_IMAGE_OR_TOKEN_CHANGED_DURING_POLICY_READ")
            if proc_before["exit_filetime_100ns"] is not None or proc_after["exit_filetime_100ns"] is not None:
                reasons.append("SCM_PROCESS_EXITED_DURING_POLICY_READ")
            token = proc_after["token"]
            if token["user_sid"].casefold() != policy.service_logon_sid.casefold():
                reasons.append("SCM_PROCESS_TOKEN_USER_MISMATCH")
            groups = {item.casefold() for item in token["group_sids"]}
            if policy.service_sid.casefold() not in groups:
                reasons.append("SCM_PROCESS_SERVICE_SID_GROUP_UNVERIFIED")
            # The own-process broker uses the configured service logon/service
            # SID. AppContainer identity and shared Job membership belong to
            # the actual restricted Server/Worker children, separately.
            if proc_after["image_path"] != expected_image:
                reasons.append("SCM_PROCESS_IMAGE_PATH_MISMATCH")
            reasons.append("LOADED_IMAGE_CONTENT_HASH_NOT_PROVEN")
            reasons.append("EXACT_SERVER_WORKER_JOB_BINDING_UNAVAILABLE")

        reasons.extend(("WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN",
                        "SERVER_WORKER_APPCONTAINER_TOKENS_NOT_PROVEN",
                        "SERVER_WORKER_PARENT_BIRTH_IMAGE_ARGV_NOT_PROVEN",
                        "VENDOR_LOOPBACK_COMPATIBILITY_NOT_PROVEN",
                        "NATIVE_T037_PROOF_ISSUER_NOT_IMPLEMENTED",
                        "FILESYSTEM_ACL_GRANT_COMPARISON_NOT_IMPLEMENTED"))
        scm_digest = _sha({"before": scm_before, "after": scm_after})
        return NativePolicyObservation(
            state="UNVERIFIED",
            evidence_classification="SOFTWARE_ONLY" if self._injected else "NATIVE_READ_ONLY_OBSERVATION",
            host_fingerprint_sha256=observed_host,
            profile_sha256=profile.sha256,
            scm_snapshot_sha256=scm_digest,
            wfp_snapshot_sha256=wfp["snapshot_sha256"],
            wfp_counts=dict(counts),
            service_pid=scm["process_id"] if type(scm["process_id"]) is int else None,
            reasons=tuple(dict.fromkeys(reasons)),
            effective_catalog_sha256=(effective_observation.snapshot_sha256
                                      if effective_observation is not None else None),
            _native_observation_attestation=(
                None if self._injected else _NATIVE_OBSERVATION_ATTESTATION),
        )

class CtypesWindowsPolicyApi:
    """Late-bound documented SCM and WFP query APIs with strict buffers."""

    SERVICE_QUERY_CONFIG = 0x0001
    SERVICE_QUERY_STATUS = 0x0004
    READ_CONTROL = 0x00020000
    SC_MANAGER_CONNECT = 0x0001
    SERVICE_CONFIG_SERVICE_SID_INFO = 5
    SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO = 6
    SERVICE_SID_TYPE_RESTRICTED = 0x00000003
    SERVICE_RUNNING = 0x00000004
    SC_STATUS_PROCESS_INFO = 0
    OWNER_SECURITY_INFORMATION = 0x00000001
    GROUP_SECURITY_INFORMATION = 0x00000002
    DACL_SECURITY_INFORMATION = 0x00000004
    RPC_C_AUTHN_WINNT = 10
    FWPM_TXN_READ_ONLY = 0x00000001
    FWP_ACTION_BLOCK = 0x00001001
    FWP_ACTION_PERMIT = 0x00001002
    FWP_ACTION_CALLOUT_TERMINATING = 0x00005003
    FWP_ACTION_CALLOUT_INSPECTION = 0x00006003
    FWP_ACTION_CALLOUT_UNKNOWN = 0x00004003
    FWP_ACTION_CALLOUT_TYPES = frozenset({FWP_ACTION_CALLOUT_TERMINATING,
                                          FWP_ACTION_CALLOUT_INSPECTION,
                                          FWP_ACTION_CALLOUT_UNKNOWN})
    FWP_FILTER_FLAG_HAS_PROVIDER_CONTEXT = 0x00000004
    FWP_FILTER_FLAG_CLEAR_ACTION_RIGHT = 0x00000008
    FWP_FILTER_FLAG_PERMIT_IF_CALLOUT_UNREGISTERED = 0x00000010

    def __init__(self, *, platform_name: str | None = None, dll_loader: Any | None = None):
        self.platform_name = platform_name or sys.platform
        if os.name != "nt" or self.platform_name not in {"win32", "nt", "windows"}:
            _fail("T037_NATIVE_UNSUPPORTED", "Win32 policy API cannot bind outside Windows")
        loader = dll_loader or C.WinDLL
        self._advapi = loader("advapi32", use_last_error=True)
        # LocalFree is exported by Kernel32.dll.  Do not rely on forwarded or
        # dependency exports being visible through Advapi32's loader handle.
        self._kernel32 = loader("kernel32", use_last_error=True)
        self._fwpu = loader("fwpuclnt", use_last_error=True)
        self._bind_scm()
        self._bind_wfp()
        self._bind_security()
        self._validate_abi()

    def _owned(self, handle, closer, *, mode, label, keepalive=(), domain="policy"):
        from ._windows_owned_resources import registry_for
        return registry_for(self).acquire(self, handle, closer, mode=mode,
                                          label=label, keepalive=keepalive, domain=domain)

    def _owned_memory(self, ptr, *, keepalive=()):
        from ._windows_owned_resources import registry_for
        entry = registry_for(self).find(self, ptr, domain="wfp_memory")
        if entry is not None:
            return entry
        function = self._fwpu.FwpmFreeMemory0
        def original_free(handle):
            local = C.c_void_p(handle.value)
            return function(C.byref(local))
        return self._owned(ptr, original_free, mode="VOID", label="wfp_memory",
                           domain="wfp_memory", keepalive=(function,*keepalive))

    def _owned_engine(self, engine):
        function = self._fwpu.FwpmEngineClose0
        def original_close(handle):
            from ._windows_owned_resources import registry_for
            if any(entry.label.startswith("wfp_enum:") and engine in entry.keepalive
                   for entry in registry_for(self).unresolved()):
                _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN", "enum owner still requires original WFP engine")
            return function(handle)
        return self._owned(engine, original_close, mode="DWORD0", label="wfp_engine",
                           domain="wfp_engine", keepalive=(function,))

    def _bind_scm(self) -> None:
        a = self._advapi
        a.OpenSCManagerW.argtypes = [LPCWSTR, LPCWSTR, DWORD]
        a.OpenSCManagerW.restype = HANDLE
        a.OpenServiceW.argtypes = [HANDLE, LPCWSTR, DWORD]
        a.OpenServiceW.restype = HANDLE
        a.CloseServiceHandle.argtypes = [HANDLE]
        a.CloseServiceHandle.restype = BOOL
        a.QueryServiceConfigW.argtypes = [HANDLE, C.c_void_p, DWORD, C.POINTER(DWORD)]
        a.QueryServiceConfigW.restype = BOOL
        a.QueryServiceConfig2W.argtypes = [HANDLE, DWORD, C.c_void_p, DWORD, C.POINTER(DWORD)]
        a.QueryServiceConfig2W.restype = BOOL
        a.QueryServiceStatusEx.argtypes = [HANDLE, DWORD, C.c_void_p, DWORD, C.POINTER(DWORD)]
        a.QueryServiceStatusEx.restype = BOOL
        a.QueryServiceObjectSecurity.argtypes = [HANDLE, DWORD, C.c_void_p, DWORD, C.POINTER(DWORD)]
        a.QueryServiceObjectSecurity.restype = BOOL
        a.LookupAccountNameW.argtypes = [LPCWSTR, LPCWSTR, C.c_void_p, C.POINTER(DWORD),
                                         LPWSTR, C.POINTER(DWORD), C.POINTER(DWORD)]
        a.LookupAccountNameW.restype = BOOL
        a.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(LPWSTR)]
        a.ConvertSidToStringSidW.restype = BOOL
        a.IsValidSid.argtypes = [C.c_void_p]
        a.IsValidSid.restype = BOOL
        self._kernel32.LocalFree.argtypes = [C.c_void_p]
        self._kernel32.LocalFree.restype = C.c_void_p

    def observe_active_interactive_sids(self) -> tuple[str, ...]:
        """Read every WTS Active session token; any inaccessible session fails closed."""
        wts = C.WinDLL("wtsapi32", use_last_error=True)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        class WTS_SESSION_INFO(C.Structure):
            _fields_ = [("SessionId", DWORD), ("pWinStationName", C.c_void_p),
                        ("State", C.c_int)]
        wts.WTSEnumerateSessionsW.argtypes = [HANDLE, DWORD, DWORD,
                                              C.POINTER(C.POINTER(WTS_SESSION_INFO)), C.POINTER(DWORD)]
        wts.WTSEnumerateSessionsW.restype = BOOL
        wts.WTSFreeMemory.argtypes = [C.c_void_p]
        wts.WTSFreeMemory.restype = None
        wts.WTSQueryUserToken.argtypes = [DWORD, C.POINTER(HANDLE)]
        wts.WTSQueryUserToken.restype = BOOL
        kernel.CloseHandle.argtypes = [HANDLE]
        kernel.CloseHandle.restype = BOOL
        rows = C.POINTER(WTS_SESSION_INFO)()
        count = DWORD(0)
        if not wts.WTSEnumerateSessionsW(None, 0, 1, C.byref(rows), C.byref(count)):
            self._winerr("WTSEnumerateSessionsW")
        rows_owner = self._owned(C.cast(rows,C.c_void_p), wts.WTSFreeMemory, mode="VOID", label="wts_rows", domain="wts_rows", keepalive=(rows,wts,kernel)) if rows else None
        if count.value > 1024 or (count.value and not rows):
            if rows_owner: rows_owner.release_owned(rows_owner.registry.owner)
            _fail("T037_NATIVE_WTS_ABI_INVALID", "WTSEnumerateSessionsW returned an invalid count/pointer")
        try:
            active = [int(rows[index].SessionId) for index in range(count.value)
                      if int(rows[index].State) == 0]  # WTSActive
        finally:
            if rows_owner: rows_owner.release_owned(rows_owner.registry.owner)
        # Reuse the exact TOKEN_USER decoder used by the existing read-only
        # observer, but close the WTS-owned primary token with CloseHandle.
        from ._windows_t037 import _Win32ReadOnlyApi
        token_reader = _Win32ReadOnlyApi()
        from ._windows_owned_resources import registry_for, attach_registry
        if not hasattr(self,"_token_readers"):self._token_readers=[]
        self._token_readers.append(token_reader)
        attach_registry(token_reader,registry_for(self))
        output: list[str] = []
        for session_id in active:
            token = HANDLE()
            if not wts.WTSQueryUserToken(session_id, C.byref(token)):
                self._winerr("WTSQueryUserToken(active session)")
            token_owner = self._owned(token,kernel.CloseHandle,mode="BOOL",label="wts_token",domain="wts_token")
            try:
                row = token_reader.read_token(token)
                if row.get("state") != "OBSERVED" or not row.get("user_sid"):
                    _fail("T037_NATIVE_WTS_TOKEN_UNVERIFIED", "active WTS token lacks an observed user SID")
                output.append(str(row["user_sid"]))
            finally:
                token_owner.release_owned(token_owner.registry.owner)
        return tuple(output)

    def _bind_wfp(self) -> None:
        f = self._fwpu
        f.FwpmEngineOpen0.argtypes = [LPCWSTR, C.c_uint32, C.c_void_p, C.c_void_p, C.POINTER(HANDLE)]
        f.FwpmEngineOpen0.restype = DWORD
        f.FwpmEngineClose0.argtypes = [HANDLE]
        f.FwpmEngineClose0.restype = DWORD
        f.FwpmTransactionBegin0.argtypes = [HANDLE, DWORD]
        f.FwpmTransactionBegin0.restype = DWORD
        f.FwpmTransactionCommit0.argtypes = [HANDLE]
        f.FwpmTransactionCommit0.restype = DWORD
        f.FwpmTransactionAbort0.argtypes = [HANDLE]
        f.FwpmTransactionAbort0.restype = DWORD
        for kind in ("Filter", "Provider", "SubLayer", "ProviderContext", "Layer"):
            create = getattr(f, f"Fwpm{kind}CreateEnumHandle0")
            enum = getattr(f, f"Fwpm{kind}Enum0")
            destroy = getattr(f, f"Fwpm{kind}DestroyEnumHandle0")
            create.argtypes = [HANDLE, C.c_void_p, C.POINTER(HANDLE)]
            create.restype = DWORD
            enum.argtypes = [HANDLE, HANDLE, DWORD, C.POINTER(C.c_void_p), C.POINTER(DWORD)]
            enum.restype = DWORD
            destroy.argtypes = [HANDLE, HANDLE]
            destroy.restype = DWORD
        f.FwpmFreeMemory0.argtypes = [C.POINTER(C.c_void_p)]
        f.FwpmFreeMemory0.restype = None
        f.FwpmGetAppIdFromFileName0.argtypes = [LPCWSTR, C.POINTER(C.POINTER(FWP_BYTE_BLOB))]
        f.FwpmGetAppIdFromFileName0.restype = DWORD
        f.FwpmFilterGetSecurityInfoByKey0.argtypes = [HANDLE, C.POINTER(GUID), DWORD,
            C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        f.FwpmFilterGetSecurityInfoByKey0.restype = DWORD
        f.FwpmProviderGetSecurityInfoByKey0.argtypes = [HANDLE, C.POINTER(GUID), DWORD,
            C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        f.FwpmProviderGetSecurityInfoByKey0.restype = DWORD
        f.FwpmSubLayerGetSecurityInfoByKey0.argtypes = [HANDLE, C.POINTER(GUID), DWORD,
            C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        f.FwpmSubLayerGetSecurityInfoByKey0.restype = DWORD

    def _bind_security(self) -> None:
        self._advapi.GetSecurityDescriptorLength.argtypes = [C.c_void_p]
        self._advapi.GetSecurityDescriptorLength.restype = DWORD

    def _validate_abi(self) -> None:
        if (C.sizeof(C.c_void_p) != 8 or C.sizeof(DWORD) != 4 or C.sizeof(LONG) != 4
                or C.sizeof(BOOL) != 4 or C.sizeof(WORD) != 2):
            _fail("T037_NATIVE_ABI_UNSUPPORTED", "T037 WFP adapter currently requires a 64-bit Windows ABI")
        # Sizes and field offsets follow fwptypes.h/fwpmtypes.h layouts.
        # On a Darwin development host this also catches accidental use of
        # host-width c_long/c_ulong in place of the fixed Win32 DWORD/LONG.
        expected = {"GUID": 16, "FWP_BYTE_BLOB": 16,
                    "FWP_CONDITION_VALUE0": 16, "FWP_VALUE0": 16,
                    "FWPM_DISPLAY_DATA0": 16, "FWPM_FILTER_CONDITION0": 40,
                    "FWPM_ACTION0": 20, "FWPM_FILTER0": 200,
                    "FWPM_PROVIDER0": 64, "FWPM_SUBLAYER0": 72,
                    "FWPM_FIELD0": 16, "FWPM_LAYER0": 72}
        actual = {"GUID": C.sizeof(GUID), "FWP_BYTE_BLOB": C.sizeof(FWP_BYTE_BLOB),
                  "FWP_CONDITION_VALUE0": C.sizeof(FWP_CONDITION_VALUE0),
                  "FWP_VALUE0": C.sizeof(FWP_VALUE0),
                  "FWPM_DISPLAY_DATA0": C.sizeof(FWPM_DISPLAY_DATA0),
                  "FWPM_FILTER_CONDITION0": C.sizeof(FWPM_FILTER_CONDITION0),
                  "FWPM_ACTION0": C.sizeof(FWPM_ACTION0),
                  "FWPM_FILTER0": C.sizeof(FWPM_FILTER0),
                  "FWPM_PROVIDER0": C.sizeof(FWPM_PROVIDER0),
                  "FWPM_SUBLAYER0": C.sizeof(FWPM_SUBLAYER0),
                  "FWPM_FIELD0": C.sizeof(FWPM_FIELD0), "FWPM_LAYER0": C.sizeof(FWPM_LAYER0)}
        offsets = {
            "FWPM_FILTER_CONDITION0": {"fieldKey": 0, "matchType": 16, "conditionValue": 24},
            "FWPM_ACTION0": {"type": 0, "action": 4},
            "FWPM_FILTER0": {"filterKey": 0, "displayData": 16, "flags": 32,
                "providerKey": 40, "providerData": 48, "layerKey": 64, "subLayerKey": 80,
                "weight": 96, "numFilterConditions": 112, "filterCondition": 120,
                "action": 128, "context": 152, "reserved": 168, "filterId": 176,
                "effectiveWeight": 184},
            "FWPM_PROVIDER0": {"providerKey": 0, "displayData": 16, "flags": 32,
                "providerData": 40, "serviceName": 56},
            "FWPM_SUBLAYER0": {"subLayerKey": 0, "displayData": 16, "flags": 32,
                "providerKey": 40, "providerData": 48, "weight": 64},
            "FWPM_FIELD0": {"fieldKey": 0, "type": 8, "dataType": 12},
            "FWPM_LAYER0": {"layerKey": 0, "displayData": 16, "flags": 32, "numFields": 36,
                "field": 40, "defaultSubLayerKey": 48, "layerId": 64},
        }
        actual_offsets = {name: {field: getattr(globals()[name], field).offset for field in fields}
                          for name, fields in offsets.items()}
        if actual != expected or actual_offsets != offsets:
            _fail("T037_NATIVE_ABI_UNSUPPORTED", "ctypes WFP structures do not match the supported 64-bit SDK ABI",
                  expected=expected, actual=actual, expected_offsets=offsets, actual_offsets=actual_offsets)

    def _winerr(self, field: str) -> None:
        code = C.get_last_error()
        _fail("T037_NATIVE_SCM_QUERY_FAILED", f"Win32 {field} failed", winerror=int(code))

    def _wfpcheck(self, status: int, field: str) -> None:
        if status != 0:
            _fail("T037_NATIVE_WFP_QUERY_FAILED", f"WFP {field} failed", status=int(status))

    def _query_buffer(self, call: Any, label: str):
        needed = DWORD(0)
        call(None, 0, C.byref(needed))
        error = C.get_last_error()
        if error != 122 or not 0 < needed.value <= MAX_BLOB_BYTES:
            _fail("T037_NATIVE_SCM_ABI_INVALID", f"{label} size query was unexpected", winerror=int(error))
        buffer = C.create_string_buffer(needed.value)
        returned = DWORD(0)
        if not call(buffer, needed.value, C.byref(returned)) or returned.value != needed.value:
            self._winerr(label)
        return buffer, int(returned.value)

    def _lookup_sid(self, account: str) -> str:
        sid_len, domain_len, use = DWORD(0), DWORD(0), DWORD(0)
        self._advapi.LookupAccountNameW(None, account, None, C.byref(sid_len), None,
                                        C.byref(domain_len), C.byref(use))
        error = C.get_last_error()
        if error != 122 or not 8 <= sid_len.value <= 1024 or domain_len.value > MAX_NATIVE_TEXT:
            _fail("T037_NATIVE_SCM_IDENTITY_UNVERIFIED", "LookupAccountNameW size query failed", winerror=int(error))
        sid = C.create_string_buffer(sid_len.value)
        domain = C.create_unicode_buffer(max(1, domain_len.value))
        if not self._advapi.LookupAccountNameW(None, account, sid, C.byref(sid_len), domain,
                                               C.byref(domain_len), C.byref(use)):
            self._winerr("LookupAccountNameW")
        text = LPWSTR()
        if not self._advapi.ConvertSidToStringSidW(C.cast(sid, C.c_void_p), C.byref(text)):
            self._winerr("ConvertSidToStringSidW")
        text_owner=self._owned(C.cast(text,C.c_void_p),self._kernel32.LocalFree,mode="LOCALFREE",label="policy_sid",domain="localfree",keepalive=(text,sid,domain))
        try:
            value = text.value
        finally:
            text_owner.release_owned(text_owner.registry.owner)
        if not isinstance(value, str) or not re.fullmatch(r"S-1-(?:[0-9]{1,2})-(?:[0-9]{1,10})(?:-[0-9]{1,10}){1,14}", value, re.I):
            _fail("T037_NATIVE_SCM_IDENTITY_UNVERIFIED", "resolved service identity is not a canonical SID")
        return value.upper()

    def _service_security(self, service: Any) -> tuple[str, str]:
        needed = DWORD(0)
        self._advapi.QueryServiceObjectSecurity(service,
            self.OWNER_SECURITY_INFORMATION | self.GROUP_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION,
            None, 0, C.byref(needed))
        error = C.get_last_error()
        if error != 122 or not 20 <= needed.value <= MAX_BLOB_BYTES:
            _fail("T037_NATIVE_SCM_SECURITY_UNVERIFIED", "service security descriptor size query failed",
                  winerror=int(error))
        buffer = C.create_string_buffer(needed.value)
        returned = DWORD(0)
        if not self._advapi.QueryServiceObjectSecurity(service,
                self.OWNER_SECURITY_INFORMATION | self.GROUP_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION,
                buffer, needed.value, C.byref(returned)) or not 20 <= returned.value <= needed.value:
            self._winerr("QueryServiceObjectSecurity")
        try:
            descriptor_sha = canonical_security_descriptor_sha256(buffer.raw[:returned.value])
        except ValueError as exc:
            _fail("T037_NATIVE_SCM_SECURITY_UNVERIFIED", "service security descriptor cannot be normalized",
                  cause=str(exc))
        return descriptor_sha, "OWNER_DACL_CANONICAL_ACE_ORDER_READ_CONTROL"

    def observe_service(self, service_name: str) -> dict[str, Any]:
        class QUERY_SERVICE_CONFIGW(C.Structure):
            _fields_ = [("dwServiceType", DWORD), ("dwStartType", DWORD),
                        ("dwErrorControl", DWORD), ("lpBinaryPathName", C.c_void_p),
                        ("lpLoadOrderGroup", C.c_void_p), ("lpdwTagId", C.POINTER(DWORD)),
                        ("lpDependencies", C.c_void_p), ("lpServiceStartName", C.c_void_p),
                        ("lpDisplayName", C.c_void_p)]
        class SERVICE_SID_INFO(C.Structure):
            _fields_ = [("dwServiceSidType", DWORD)]
        class SERVICE_STATUS_PROCESS(C.Structure):
            _fields_ = [(name, DWORD) for name in ("dwServiceType", "dwCurrentState", "dwControlsAccepted",
                        "dwWin32ExitCode", "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint",
                        "dwProcessId", "dwServiceFlags")]
        manager = self._advapi.OpenSCManagerW(None, None, self.SC_MANAGER_CONNECT)
        if not manager:
            self._winerr("OpenSCManagerW")
        manager_owner=self._owned(manager,self._advapi.CloseServiceHandle,mode="BOOL",label="scm_manager",domain="scm")
        service_owner=None
        service = None
        primary: BaseException | None = None
        try:
            service = self._advapi.OpenServiceW(manager, service_name,
                self.SERVICE_QUERY_CONFIG | self.SERVICE_QUERY_STATUS | self.READ_CONTROL)
            if not service:
                self._winerr("OpenServiceW")
            service_owner=self._owned(service,self._advapi.CloseServiceHandle,mode="BOOL",label="scm_service",domain="scm")
            config_buffer, config_size = self._query_buffer(
                lambda buf, size, needed: self._advapi.QueryServiceConfigW(service, buf, size, needed),
                "QueryServiceConfigW")
            if config_size < C.sizeof(QUERY_SERVICE_CONFIGW):
                _fail("T037_NATIVE_SCM_ABI_INVALID", "QUERY_SERVICE_CONFIGW buffer is truncated")
            # Keep the returned SCM buffer alive while dereferencing its
            # documented in-buffer string pointers.
            config = QUERY_SERVICE_CONFIGW.from_buffer(config_buffer)
            if not config.lpBinaryPathName or not config.lpServiceStartName:
                _fail("T037_NATIVE_SCM_ABI_INVALID", "SCM config lacks binary path or service start name")
            binary = _bounded_utf16(config.lpBinaryPathName, "SCM binary path")
            start_name = _bounded_utf16(config.lpServiceStartName, "SCM service start name")
            binary_argv = self._command_line_to_argv(binary)
            binary_image = binary_argv[0]
            binary_argv_sha = hashlib.sha256(_canonical(binary_argv)).hexdigest()
            sid_buffer, sid_size = self._query_buffer(
                lambda buf, size, needed: self._advapi.QueryServiceConfig2W(
                    service, self.SERVICE_CONFIG_SERVICE_SID_INFO, buf, size, needed),
                "QueryServiceConfig2W(ServiceSidInfo)")
            if sid_size != C.sizeof(SERVICE_SID_INFO):
                _fail("T037_NATIVE_SCM_ABI_INVALID", "SERVICE_SID_INFO size mismatch")
            sid_info = SERVICE_SID_INFO.from_buffer(sid_buffer)
            if sid_info.dwServiceSidType != self.SERVICE_SID_TYPE_RESTRICTED:
                sid_mode = f"UNKNOWN:{sid_info.dwServiceSidType}"
            else:
                sid_mode = "RESTRICTED"
            status = SERVICE_STATUS_PROCESS()
            returned = DWORD(0)
            if not self._advapi.QueryServiceStatusEx(service, self.SC_STATUS_PROCESS_INFO,
                    C.byref(status), C.sizeof(status), C.byref(returned)):
                self._winerr("QueryServiceStatusEx")
            if returned.value != C.sizeof(status):
                _fail("T037_NATIVE_SCM_ABI_INVALID", "SERVICE_STATUS_PROCESS size mismatch")
            security_sha, security_semantics = self._service_security(service)
            service_sid = self._lookup_sid("NT SERVICE\\" + service_name)
            return {"service_name": service_name,
                    "service_start_name": start_name,
                    "service_binary_image_path": binary_image,
                    "service_binary_argv_sha256": binary_argv_sha,
                    "service_start_sid": self._lookup_sid(start_name),
                    "service_sid_type": sid_mode,
                    "service_sid": service_sid,
                    "state": "RUNNING" if status.dwCurrentState == self.SERVICE_RUNNING else str(status.dwCurrentState),
                    "process_id": int(status.dwProcessId), "binary_path": binary,
                    "service_security_sha256": security_sha,
                    "service_security_semantics": security_semantics}
        except BaseException as exc:
            primary = exc
            raise
        finally:
            close_errors = []
            for resource, label in ((service_owner, "service"), (manager_owner, "SCM manager")):
                if resource:
                    try:
                        resource.release_owned(resource.registry.owner)
                        closed = True
                    except BaseException as close_exc:
                        close_errors.append({"handle": label,
                                             "exception_type": type(close_exc).__name__})
                    else:
                        if not closed:
                            close_errors.append({"handle": label,
                                                 "winerror": int(C.get_last_error())})
            if close_errors:
                _fail("T037_NATIVE_SCM_HANDLE_RELEASE_UNKNOWN",
                      "SCM read-only handles could not all be released",
                      close_failures=close_errors,
                      primary_type=type(primary).__name__ if primary is not None else None,
                      primary_code=getattr(primary, "code", None) if primary is not None else None)

    @staticmethod
    def _guid_text(guid: GUID) -> str:
        raw = C.string_at(C.byref(guid), C.sizeof(guid))
        return "{" + str(uuid.UUID(bytes_le=raw)).upper() + "}"

    def _command_line_to_argv(self, command_line: str) -> tuple[str, ...]:
        shell = C.WinDLL("shell32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [LPCWSTR, C.POINTER(C.c_int32)]
        shell.CommandLineToArgvW.restype = C.POINTER(C.c_void_p)
        argc = C.c_int32(0)
        argv = shell.CommandLineToArgvW(command_line, C.byref(argc))
        argv_owner=self._owned(C.cast(argv,C.c_void_p),self._kernel32.LocalFree,mode="LOCALFREE",label="policy_argv",domain="localfree",keepalive=(argv,)) if argv else None
        if not argv or not 1 <= argc.value <= 4096:
            if argv_owner:argv_owner.release_owned(argv_owner.registry.owner)
            _fail("T037_NATIVE_SCM_ABI_INVALID", "CommandLineToArgvW returned an invalid argument vector")
        try:
            rows = tuple(_bounded_utf16(argv[index], "SCM command-line argument")
                         for index in range(argc.value))
        finally:
            argv_owner.release_owned(argv_owner.registry.owner)
        if any(item is None for item in rows):
            _fail("T037_NATIVE_SCM_ABI_INVALID", "SCM command line contains an invalid argument")
        return tuple(str(item) for item in rows)

    def _wfp_security_sha(self, engine: Any, kind: str, key: GUID) -> str:
        output = C.c_void_p()
        fn = getattr(self._fwpu, f"Fwpm{kind}GetSecurityInfoByKey0")
        status = fn(engine, C.byref(key), self.OWNER_SECURITY_INFORMATION |
                    self.GROUP_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION,
                    None, None, None, None, C.byref(output))
        if output.value:self._owned_memory(output)
        if status != 0:
            if output.value:
                self._free(output)
            self._wfpcheck(status, f"{kind}GetSecurityInfoByKey0")
        if not output.value:
            _fail("T037_NATIVE_WFP_ABI_INVALID", f"{kind} security descriptor is null")
        try:
            size = int(self._advapi.GetSecurityDescriptorLength(output))
            if not 20 <= size <= MAX_BLOB_BYTES:
                _fail("T037_NATIVE_WFP_ABI_INVALID", f"{kind} security descriptor length is invalid")
            try:
                return canonical_security_descriptor_sha256(C.string_at(output, size))
            except ValueError as exc:
                _fail("T037_NATIVE_WFP_ACL_UNSUPPORTED", f"{kind} security descriptor cannot be normalized",
                      cause=str(exc))
        finally:
            self._free(output)

    def _free(self, ptr: C.c_void_p) -> None:
        if ptr.value:
            resource=self._owned_memory(ptr)
            resource.release_owned(resource.registry.owner)

    def _guid(self, value: GUID | None) -> str | None:
        if value is None:
            return None
        raw = C.string_at(C.byref(value), C.sizeof(value))
        return "{" + str(uuid.UUID(bytes_le=raw)).upper() + "}"

    def _bounded_blob(self, blob: FWP_BYTE_BLOB) -> str:
        size = int(blob.size)
        if size < 0 or size > MAX_BLOB_BYTES or (size and not blob.data):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_BYTE_BLOB length/pointer is invalid")
        return C.string_at(blob.data, size).hex() if size else ""

    def observe_application_id(self, image_path: str) -> dict[str, Any]:
        """Derive the actual SDK App-ID; retain no borrowed allocation."""
        if not isinstance(image_path, str) or not image_path or "\x00" in image_path:
            _fail("T037_NATIVE_APP_ID_PATH_INVALID", "App-ID requires an exact image path")
        # Reuse the established SDK reader and its allocation/error handling.
        # This bridge exposes read/check/free only; no mutation adapter instance
        # or provider/filter write is constructed by the read-only observer.
        from types import SimpleNamespace
        from ._windows_t037_wfp import CtypesWindowsWfpMutationApi
        function=self._fwpu.FwpmGetAppIdFromFileName0
        def actual_allocate(path, output):
            status=function(path,output)
            address=C.cast(output,C.POINTER(C.c_void_p)).contents.value
            if address:self._owned_memory(C.c_void_p(address),keepalive=(output,function))
            return status
        reader = SimpleNamespace(
            _fwpu=SimpleNamespace(FwpmGetAppIdFromFileName0=actual_allocate), _check=self._wfpcheck,
            _free=lambda address, primary=None: self._free(C.c_void_p(address)))
        raw = CtypesWindowsWfpMutationApi._app_id_blob(reader, image_path)
        return {"image_path": ntpath.normcase(ntpath.normpath(image_path)),
                "type": 12, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def _layer_row(self, item: FWPM_LAYER0) -> dict[str, Any]:
        if int(item.numFields) > MAX_CONDITIONS or (item.numFields and not item.field):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP layer field count/pointer is invalid")
        fields = []
        keys = set()
        supported_types = set(range(19)) | {256, 257, 258}
        for index in range(int(item.numFields)):
            row = item.field[index]
            if not row.fieldKey or row.type not in {0, 1, 2} or row.dataType not in supported_types:
                _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP layer field schema is unsupported")
            key = self._guid(row.fieldKey.contents)
            if key in keys:
                _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP layer has duplicate field keys")
            keys.add(key)
            fields.append({"field_key": key, "field_type": int(row.type), "data_type": int(row.dataType)})
        return {"layer_key": self._guid(item.layerKey), "display_data": self._display_data(item.displayData, "layer"),
                "flags": int(item.flags), "fields": fields,
                "default_sublayer_key": self._guid(item.defaultSubLayerKey), "layer_id": int(item.layerId)}

    def _sid_string(self, sid: Any) -> str:
        if not sid or not self._advapi.IsValidSid(sid):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_SID condition contains an invalid SID")
        text = LPWSTR()
        if not self._advapi.ConvertSidToStringSidW(sid, C.byref(text)):
            self._winerr("ConvertSidToStringSidW(WFP)")
        text_owner=self._owned(C.cast(text,C.c_void_p),self._kernel32.LocalFree,mode="LOCALFREE",label="policy_sid",domain="localfree",keepalive=(text,))
        try:
            return str(text.value).upper()
        finally:
            text_owner.release_owned(text_owner.registry.owner)

    def _display_data(self, value: FWPM_DISPLAY_DATA0, kind: str) -> dict[str, str | None]:
        return {"name": _bounded_utf16(value.name, f"{kind} display name", nullable=True),
                "description": _bounded_utf16(value.description, f"{kind} display description", nullable=True)}

    def _decode_value(self, value: FWP_CONDITION_VALUE0 | FWP_VALUE0, *, depth: int = 0) -> Any:
        if depth > 2:
            _fail("T037_NATIVE_WFP_VALUE_UNSUPPORTED", "nested FWP_RANGE value exceeds supported depth")
        kind = int(value.type)
        if kind == 0: return {"type": kind, "value": None}
        if kind == 1: return {"type": kind, "value": int(value.uint8)}
        if kind == 2: return {"type": kind, "value": int(value.uint16)}
        if kind == 3: return {"type": kind, "value": int(value.uint32)}
        if kind == 4:
            if not value.uint64: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_UINT64 pointer is null")
            return {"type": kind, "value": int(value.uint64.contents.value)}
        if kind == 5: return {"type": kind, "value": int(value.int8)}
        if kind == 6: return {"type": kind, "value": int(value.int16)}
        if kind == 7: return {"type": kind, "value": int(value.int32)}
        if kind == 8:
            if not value.int64: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_INT64 pointer is null")
            return {"type": kind, "value": int(value.int64.contents.value)}
        if kind == 9:
            result = float(value.float32)
            if not math.isfinite(result):
                _fail("T037_NATIVE_WFP_VALUE_UNSUPPORTED", "non-finite FWP_FLOAT cannot be normalized")
            return {"type": kind, "value": result}
        if kind == 10:
            if not value.double64: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_DOUBLE pointer is null")
            result = float(value.double64.contents.value)
            if result != result or result in (float("inf"), float("-inf")):
                _fail("T037_NATIVE_WFP_VALUE_UNSUPPORTED", "non-finite FWP_DOUBLE cannot be normalized")
            return {"type": kind, "value": result}
        if kind == 11:
            if not value.byteArray16: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_BYTE_ARRAY16 pointer is null")
            item = C.cast(value.byteArray16, C.POINTER(FWP_BYTE_ARRAY16)).contents
            return {"type": kind, "value": bytes(item.byteArray16).hex()}
        if kind in {12, 14, 16}:
            pointer = value.byteBlob if kind == 12 else (value.sd if kind == 14 else value.tokenAccessInformation)
            if not pointer: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP blob pointer is null")
            raw = self._bounded_blob(pointer.contents)
            return {"type": kind, "size": len(raw) // 2, "sha256": hashlib.sha256(bytes.fromhex(raw)).hexdigest()}
        if kind == 13:
            return {"type": kind, "value": self._sid_string(value.sid)}
        if kind == 17:
            return {"type": kind, "value": _bounded_utf16(value.unicodeString, "FWP_UNICODE_STRING")}
        if kind == 18:
            if not value.byteArray6: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_BYTE_ARRAY6 pointer is null")
            item = C.cast(value.byteArray6, C.POINTER(FWP_BYTE_ARRAY6)).contents
            return {"type": kind, "value": bytes(item.byteArray6).hex()}
        if kind == 256:
            if not value.v4AddrMask: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_V4_ADDR_AND_MASK pointer is null")
            item = C.cast(value.v4AddrMask, C.POINTER(FWP_V4_ADDR_AND_MASK)).contents
            return {"type": kind, "address_u32": int(item.addr), "mask_u32": int(item.mask)}
        if kind == 257:
            if not value.v6AddrMask: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_V6_ADDR_AND_MASK pointer is null")
            item = C.cast(value.v6AddrMask, C.POINTER(FWP_V6_ADDR_AND_MASK)).contents
            if item.prefixLength > 128: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP IPv6 prefix length is invalid")
            return {"type": kind, "address": bytes(item.addr).hex(), "prefix_length": int(item.prefixLength)}
        if kind == 258:
            if not value.rangeValue: _fail("T037_NATIVE_WFP_ABI_INVALID", "FWP_RANGE pointer is null")
            item = C.cast(value.rangeValue, C.POINTER(FWPM_RANGE0)).contents
            return {"type": kind, "low": self._decode_value(item.valueLow, depth=depth + 1),
                    "high": self._decode_value(item.valueHigh, depth=depth + 1)}
        _fail("T037_NATIVE_WFP_VALUE_UNSUPPORTED", "unknown FWP_DATA_TYPE", data_type=kind)

    def _filter_row(self, engine: Any, filter_row: FWPM_FILTER0) -> dict[str, Any]:
        count = int(filter_row.numFilterConditions)
        if count > MAX_CONDITIONS or (count and not filter_row.filterCondition):
            _fail("T037_NATIVE_WFP_ABI_INVALID", "filter condition count or pointer is invalid")
        conditions = []
        for index in range(count):
            condition = filter_row.filterCondition[index]
            match_type = int(condition.matchType)
            if match_type > 12:
                _fail("T037_NATIVE_WFP_MATCH_UNSUPPORTED", "unknown FWP_MATCH_TYPE", match_type=match_type)
            conditions.append({"field_key": self._guid(condition.fieldKey),
                               "match_type": match_type,
                               "condition_value": self._decode_value(condition.conditionValue)})
        provider = self._guid(filter_row.providerKey.contents) if filter_row.providerKey else None
        action_type = int(filter_row.action.type)
        callout = action_type in self.FWP_ACTION_CALLOUT_TYPES
        return {
            "filter_key": self._guid(filter_row.filterKey),
            "display_data": self._display_data(filter_row.displayData, "filter"),
            "provider_key": provider,
            "layer_key": self._guid(filter_row.layerKey),
            "sublayer_key": self._guid(filter_row.subLayerKey),
            "flags": int(filter_row.flags),
            "filter_id": int(filter_row.filterId),
            "action_type": action_type,
            "action_callout_key": self._guid(filter_row.calloutKey) if callout else None,
            "context": ("provider-context:" + str(self._guid(filter_row.providerContextKey))
                        if int(filter_row.flags) & self.FWP_FILTER_FLAG_HAS_PROVIDER_CONTEXT
                        else {"raw_context": int(filter_row.rawContext)}),
            "weight": self._decode_value(filter_row.weight),
            "effective_weight": self._decode_value(filter_row.effectiveWeight),
            "has_provider_context": bool(int(filter_row.flags) & self.FWP_FILTER_FLAG_HAS_PROVIDER_CONTEXT),
            "provider_data_sha256": hashlib.sha256(bytes.fromhex(self._bounded_blob(filter_row.providerData))).hexdigest(),
            "conditions": conditions,
            "security_sha256": self._wfp_security_sha(engine, "Filter", filter_row.filterKey),
        }

    def _enum(self, engine: Any, kind: str, budget: _WfpBudget) -> list[dict[str, Any]]:
        create = getattr(self._fwpu, f"Fwpm{kind}CreateEnumHandle0")
        enum = getattr(self._fwpu, f"Fwpm{kind}Enum0")
        destroy = getattr(self._fwpu, f"Fwpm{kind}DestroyEnumHandle0")
        row_type = {"Filter": FWPM_FILTER0, "Provider": FWPM_PROVIDER0,
                    "SubLayer": FWPM_SUBLAYER0,
                    "ProviderContext": FWPM_PROVIDER_CONTEXT0, "Layer": FWPM_LAYER0}[kind]
        enum_handle = HANDLE()
        create_status = create(engine, None, C.byref(enum_handle))
        enum_owner=None
        if enum_handle.value:
            enum_owner=self._owned(enum_handle,lambda handle:destroy(engine,handle),mode="DWORD0",
                label="wfp_enum:"+kind,domain="wfp_enum:"+kind,keepalive=(engine,destroy))
        if create_status != 0:
            if enum_handle.value:
                try:
                    enum_owner.release_owned(enum_owner.registry.owner)
                    release_status = 0
                except BaseException as release_exc:
                    _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                          f"WFP {kind} enum-create failure left a handle whose release raised",
                          create_status=int(create_status),
                          release_exception_type=type(release_exc).__name__)
                if release_status != 0:
                    _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                          f"WFP {kind} enum-create failure left a handle that could not be released",
                          create_status=int(create_status), destroy_status=int(release_status))
            self._wfpcheck(create_status, f"{kind}CreateEnumHandle0")
        if not enum_handle.value:
            _fail("T037_NATIVE_WFP_ABI_INVALID", f"WFP {kind} enum creation returned a null handle")
        output: list[dict[str, Any]] = []
        primary: BaseException | None = None
        try:
            for _page in range(MAX_ENUM_PAGES):
                budget.add_page(kind)
                array = C.c_void_p()
                count = DWORD(0)
                page_primary: BaseException | None = None
                try:
                    # Enum0 may allocate its result array even when it reports
                    # failure.  It may also return a valid page just as the
                    # wall-clock budget expires.  Keep status checking, the
                    # time gate, and every operation that reads the page inside
                    # this allocation's single release scope.
                    enum_status = enum(engine, enum_handle, MAX_ENUM_PAGE,
                                       C.byref(array), C.byref(count))
                    if array.value:self._owned_memory(array)
                    self._wfpcheck(enum_status, f"{kind}Enum0")
                    budget.check_time()
                    if count.value > MAX_ENUM_PAGE or (count.value and not array.value):
                        _fail("T037_NATIVE_WFP_ABI_INVALID", f"{kind} page count/pointer is invalid")
                    pointers = C.cast(array, C.POINTER(C.POINTER(row_type))) if count.value else None
                    for index in range(count.value):
                        item = pointers[index].contents
                        if kind == "Layer":
                            row = self._layer_row(item)
                        elif kind == "Filter":
                            row = self._filter_row(engine, item)
                        elif kind == "Provider":
                            row = {"provider_key": self._guid(item.providerKey),
                                "display_data": self._display_data(item.displayData, "provider"),
                                "flags": int(item.flags),
                                "provider_data_sha256": hashlib.sha256(bytes.fromhex(self._bounded_blob(item.providerData))).hexdigest(),
                                "service_name": _bounded_utf16(item.serviceName, "WFP provider service name", nullable=True),
                                "security_sha256": self._wfp_security_sha(engine, "Provider", item.providerKey)}
                        elif kind == "SubLayer":
                            row = {"sublayer_key": self._guid(item.subLayerKey),
                                "display_data": self._display_data(item.displayData, "sublayer"),
                                "flags": int(item.flags),
                                "provider_key": self._guid(item.providerKey.contents) if item.providerKey else None,
                                "provider_data_sha256": hashlib.sha256(bytes.fromhex(self._bounded_blob(item.providerData))).hexdigest(),
                                "weight": int(item.weight),
                                "security_sha256": self._wfp_security_sha(engine, "SubLayer", item.subLayerKey)}
                        else:
                            if not pointers[index]:
                                _fail("T037_NATIVE_WFP_ABI_INVALID",
                                      "provider-context enumeration returned a null record pointer")
                            # Count the complete set without interpreting the versioned union.
                            # Any presence makes parent deletion fail closed in the mutator.
                            row = {"state": "PRESENT_UNCLASSIFIED"}
                        budget.add_row(row)
                        output.append(row)
                except BaseException as exc:
                    page_primary = exc
                    raise
                finally:
                    if array.value:
                        try:
                            self._free(array)
                        except BaseException as release_exc:
                            _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                                  f"WFP {kind} result page allocation could not be released",
                                  release_exception_type=type(release_exc).__name__,
                                  primary_type=type(page_primary).__name__ if page_primary is not None else None,
                                  primary_code=getattr(page_primary, "code", None) if page_primary is not None else None)
                if count.value < MAX_ENUM_PAGE:
                    return output
            _fail("T037_NATIVE_WFP_SNAPSHOT_INCOMPLETE", f"{kind} enumeration exceeded page limit")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                enum_owner.release_owned(enum_owner.registry.owner)
                status = 0
            except BaseException as release_exc:
                _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                      f"WFP {kind} enumeration handle release raised an exception",
                      destroy_status=getattr(release_exc,"details",{}).get("native_release_result"),
                      release_exception_type=type(release_exc).__name__,
                      primary_type=type(primary).__name__ if primary is not None else None,
                      primary_code=getattr(primary, "code", None) if primary is not None else None)
            if status != 0:
                _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                      f"WFP {kind} enumeration handle could not be released",
                      destroy_status=int(status),
                      primary_type=type(primary).__name__ if primary is not None else None,
                      primary_code=getattr(primary, "code", None) if primary is not None else None)

    def observe_wfp_policy_snapshot(self) -> dict[str, Any]:
        """Atomic five-catalog observation; visibility is not an effective proof."""
        return self.observe_wfp_snapshot(_include_layers=True)

    def observe_wfp_snapshot(self, *, _include_layers: bool = False) -> dict[str, Any]:
        if type(_include_layers) is not bool:
            _fail("T037_NATIVE_WFP_SCHEMA_INVALID", "layer snapshot selector must be boolean")
        budget = _WfpBudget.start(kind_count=5 if _include_layers else 4)
        engine = HANDLE()
        open_status = self._fwpu.FwpmEngineOpen0(
            None, self.RPC_C_AUTHN_WINNT, None, None, C.byref(engine))
        engine_owner=self._owned_engine(engine) if engine.value else None
        if open_status != 0:
            if engine.value:
                try:
                    engine_owner.release_owned(engine_owner.registry.owner)
                    close_status = 0
                except BaseException as close_exc:
                    _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                          "failed WFP engine open left a handle whose close raised",
                          open_status=int(open_status),
                          close_exception_type=type(close_exc).__name__)
                if close_status != 0:
                    _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                          "failed WFP engine open left a handle that could not be released",
                          open_status=int(open_status), close_status=int(close_status))
            self._wfpcheck(open_status, "EngineOpen0")
        if not engine.value:
            _fail("T037_NATIVE_WFP_ABI_INVALID", "WFP EngineOpen0 returned a null handle")
        transaction = False
        primary: BaseException | None = None
        result: dict[str, Any] | None = None
        try:
            budget.check_time()
            self._wfpcheck(self._fwpu.FwpmTransactionBegin0(engine, self.FWPM_TXN_READ_ONLY),
                           "TransactionBegin0(READ_ONLY)")
            transaction = True
            providers = self._enum(engine, "Provider", budget)
            sublayers = self._enum(engine, "SubLayer", budget)
            filters = self._enum(engine, "Filter", budget)
            provider_contexts = self._enum(engine, "ProviderContext", budget)
            layers = self._enum(engine, "Layer", budget) if _include_layers else None
            budget.check_time()
            self._wfpcheck(self._fwpu.FwpmTransactionCommit0(engine), "TransactionCommit0(READ_ONLY)")
            transaction = False
            # Filter actions can reference provider contexts/callouts; absent
            # their complete effective semantics, policy remains unverified.
            reasons = []
            if any(item["flags"] != 0 for item in (*providers, *sublayers)):
                reasons.append("WFP_PROVIDER_OR_SUBLAYER_FLAGS_NOT_CLASSIFIED")
            if any(item["flags"] & ~0x7f for item in filters):
                reasons.append("WFP_UNKNOWN_FILTER_FLAGS_PRESENT")
            if any(item["flags"] & 0x67 for item in filters):
                reasons.append("WFP_FILTER_LIFETIME_OR_DISABLED_FLAGS_UNRESOLVED")
            if any(item["has_provider_context"] for item in filters):
                reasons.append("WFP_PROVIDER_CONTEXT_EFFECTS_UNRESOLVED")
            if any(item["action_type"] not in {self.FWP_ACTION_BLOCK, self.FWP_ACTION_PERMIT} for item in filters):
                reasons.append("WFP_EXTERNAL_CALLOUT_OR_UNKNOWN_ACTION_PRESENT")
            if any(item["flags"] & (self.FWP_FILTER_FLAG_CLEAR_ACTION_RIGHT |
                                    self.FWP_FILTER_FLAG_PERMIT_IF_CALLOUT_UNREGISTERED) for item in filters):
                reasons.append("WFP_FILTER_RIGHTS_OR_CALLOUT_FALLBACK_UNRESOLVED")
            if any(item["conditions"] for item in filters):
                reasons.append("WFP_CONDITION_FIELD_SEMANTICS_NOT_CLASSIFIED")
            if provider_contexts:
                reasons.append("WFP_PROVIDER_CONTEXT_ROWS_UNCLASSIFIED")
            reasons.append("WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN")
            reasons.append("WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN" if _include_layers
                           else "WFP_LAYER_CATALOG_NOT_ENUMERATED")
            content = {"providers": sorted(providers, key=lambda item: item["provider_key"] or ""),
                       "sublayers": sorted(sublayers, key=lambda item: item["sublayer_key"] or ""),
                       "filters": sorted(filters, key=lambda item: (item["layer_key"] or "", item["filter_id"])),
                       "provider_contexts": provider_contexts}
            if _include_layers:
                content["layers"] = sorted(layers, key=lambda item: item["layer_key"])
            serialized = _canonical(content)
            if len(serialized) > MAX_ENUM_TOTAL_BYTES:
                _fail("T037_NATIVE_WFP_BYTE_LIMIT", "canonical WFP snapshot exceeds its total byte bound",
                      max_bytes=MAX_ENUM_TOTAL_BYTES, observed_bytes=len(serialized))
            budget.check_time()
            digest = hashlib.sha256(serialized).hexdigest()
            counts = {"providers": len(providers), "sublayers": len(sublayers), "filters": len(filters),
                      "provider_contexts": len(provider_contexts),
                      "conditions": sum(len(item["conditions"]) for item in filters),
                      "pages": budget.pages, "pages_by_kind": dict(budget.pages_by_kind or {}),
                      "total_records": budget.rows, "total_bytes": len(serialized),
                      "elapsed_ms": 0}
            if _include_layers:
                counts["layers"] = len(layers)
            limits = {"max_page_size": MAX_ENUM_PAGE, "max_pages_per_kind": MAX_ENUM_PAGES,
                      "max_total_pages": MAX_ENUM_PAGES * budget.kind_count, "max_total_records": MAX_ENUM_ROWS,
                      "max_total_bytes": MAX_ENUM_TOTAL_BYTES, "max_elapsed_ms": MAX_ENUM_SECONDS * 1000}
            result = {"state": "COMPLETE_READ_ONLY", "counts": counts, "limits": limits,
                      "snapshot_sha256": digest, "reasons": reasons,
                      "providers": providers, "sublayers": sublayers, "filters": filters,
                      "provider_contexts": provider_contexts}
            if _include_layers:
                result["layers"] = layers
                result["schema"] = "comsol-mcp.wfp-five-catalog-readonly/1"
        except BaseException as exc:
            primary = exc
            if transaction:
                try:
                    abort_status = self._fwpu.FwpmTransactionAbort0(engine)
                except BaseException as abort_exc:
                    primary = NativePolicyError(
                        "T037_NATIVE_WFP_TRANSACTION_RELEASE_UNKNOWN",
                        "read-only WFP transaction failed and abort raised before it could be confirmed",
                        details={"abort_exception_type": type(abort_exc).__name__,
                                 "primary_type": type(exc).__name__,
                                 "primary_code": getattr(exc, "code", None)},
                        stage="pre_dispatch",
                    )
                else:
                    if abort_status != 0:
                        primary = NativePolicyError(
                            "T037_NATIVE_WFP_TRANSACTION_RELEASE_UNKNOWN",
                            "read-only WFP transaction failed and abort could not be confirmed",
                            details={"abort_status": int(abort_status),
                                     "primary_type": type(exc).__name__,
                                     "primary_code": getattr(exc, "code", None)},
                            stage="pre_dispatch",
                        )
            transaction = False
        try:
            engine_owner.release_owned(engine_owner.registry.owner)
            close_status = 0
        except BaseException as exc:
            _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                  "WFP engine close raised while releasing the read-only observation handle",
                  close_status=getattr(exc,"details",{}).get("native_release_result"),
                  close_exception_type=type(exc).__name__,
                  primary_type=type(primary).__name__ if primary is not None else None,
                  primary_code=getattr(primary, "code", None) if primary is not None else None)
        if close_status != 0:
            _fail("T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN",
                  "WFP engine handle could not be released after read-only observation",
                  close_status=int(close_status),
                  primary_type=type(primary).__name__ if primary is not None else None,
                  primary_code=getattr(primary, "code", None) if primary is not None else None)
        if primary is not None:
            raise primary
        if result is None:
            _fail("T037_NATIVE_WFP_SNAPSHOT_INCOMPLETE", "WFP observer produced no complete snapshot")
        budget.check_time()
        result["counts"]["elapsed_ms"] = budget.elapsed_ms()
        if result["counts"]["elapsed_ms"] > MAX_ENUM_SECONDS * 1000:
            _fail("T037_NATIVE_WFP_TIME_LIMIT", "WFP snapshot exceeded its total monotonic-time bound",
                  max_seconds=MAX_ENUM_SECONDS, elapsed_ms=result["counts"]["elapsed_ms"])
        return result


__all__ = ["CtypesWindowsPolicyApi", "NativePolicyError", "NativePolicyObservation",
           "WindowsNativePolicyAdapter"]
