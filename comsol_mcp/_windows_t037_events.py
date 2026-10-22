"""Bounded, read-only WFP classify-drop event observation for T037 diagnostics.

This module never changes WFP options or policy and never upgrades an event
match into an isolation or resume claim. Enum2 is a synchronous snapshot: it
returns events logged before its enum handle was created, not a subscription.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import ipaddress
import ntpath
import os
import threading
from typing import Any

from ._windows_owned_resources import NativeResourceError, registry_for


# Values and layouts follow fwpmu.h/fwpmtypes.h. GUIDs shared with the existing
# WFP observer are copied verbatim from its already-reviewed SDK bindings.
SDK_SOURCE = "Windows SDK fwpmu.h/fwpmtypes.h; FWPM_NET_EVENT2 / FwpmNetEventEnum2"
FWPM_ENGINE_COLLECT_NET_EVENTS = 0
FWPM_NET_EVENT_TYPE_CLASSIFY_DROP = 3
FWP_MATCH_EQUAL = 0
FWP_UINT8 = 1
FWP_BYTE_BLOB_TYPE = 12
FWP_V4_ADDR_MASK = 0x100
FWP_IP_VERSION_V4 = 0
FWP_IP_VERSION_V6 = 1
FWP_DIRECTION_IN = 0x3900
FWP_DIRECTION_OUT = 0x3901
FWP_DIRECTION_FORWARD = 0x3902
FWP_E_NET_EVENTS_DISABLED = 0x80320013

FWPM_NET_EVENT_FLAG_IP_PROTOCOL_SET = 0x00000001
FWPM_NET_EVENT_FLAG_LOCAL_ADDR_SET = 0x00000002
FWPM_NET_EVENT_FLAG_REMOTE_ADDR_SET = 0x00000004
FWPM_NET_EVENT_FLAG_LOCAL_PORT_SET = 0x00000008
FWPM_NET_EVENT_FLAG_REMOTE_PORT_SET = 0x00000010
FWPM_NET_EVENT_FLAG_APP_ID_SET = 0x00000020
FWPM_NET_EVENT_FLAG_USER_ID_SET = 0x00000040
FWPM_NET_EVENT_FLAG_IP_VERSION_SET = 0x00000100
FWPM_NET_EVENT_FLAG_PACKAGE_ID_SET = 0x00000400
_KNOWN_HEADER_FLAGS = (
    FWPM_NET_EVENT_FLAG_IP_PROTOCOL_SET | FWPM_NET_EVENT_FLAG_LOCAL_ADDR_SET |
    FWPM_NET_EVENT_FLAG_REMOTE_ADDR_SET | FWPM_NET_EVENT_FLAG_LOCAL_PORT_SET |
    FWPM_NET_EVENT_FLAG_REMOTE_PORT_SET | FWPM_NET_EVENT_FLAG_APP_ID_SET |
    FWPM_NET_EVENT_FLAG_USER_ID_SET | FWPM_NET_EVENT_FLAG_IP_VERSION_SET |
    FWPM_NET_EVENT_FLAG_PACKAGE_ID_SET | 0x00000080 | 0x00000200
)

MAX_WINDOW_SECONDS = 30
MAX_APP_ID_BYTES = 65_536
MAX_SID_BYTES = 68
PAGE_SIZE = 8
MAX_PAGES = 9
MAX_EVENTS = 64
MAX_EVENTS_READ = MAX_EVENTS + 1  # one bounded sentinel detects truncation
_FILETIME_TICKS_PER_SECOND = 10_000_000
_FILETIME_UNIX_EPOCH = 116_444_736_000_000_000
_MAX_IMAGE_PATH_CHARS = 32_767
_OWNER_SEAL = object()
_owner_lock = threading.RLock()
_active_owner: "_WfpEventOwner | None" = None


class FILETIME(C.Structure):
    _fields_ = [("dwLowDateTime", C.c_uint32), ("dwHighDateTime", C.c_uint32)]


class GUID(C.Structure):
    _fields_ = [
        ("Data1", C.c_uint32), ("Data2", C.c_uint16), ("Data3", C.c_uint16),
        ("Data4", C.c_ubyte * 8),
    ]


class FWP_BYTE_BLOB(C.Structure):
    _fields_ = [("size", C.c_uint32), ("data", C.POINTER(C.c_ubyte))]


class _FWP_VALUE_UNION(C.Union):
    _fields_ = [("uint32", C.c_uint32), ("uint64", C.c_uint64), ("pointer", C.c_void_p)]


class FWP_VALUE0(C.Structure):
    _fields_ = [("type", C.c_uint32), ("value", _FWP_VALUE_UNION)]


class FWP_V4_ADDR_AND_MASK(C.Structure):
    _fields_ = [("addr", C.c_uint32), ("mask", C.c_uint32)]


class _FWP_CONDITION_UNION(C.Union):
    _fields_ = [
        ("uint8", C.c_uint8), ("uint32", C.c_uint32), ("uint64", C.c_uint64),
        ("byteBlob", C.c_void_p), ("v4AddrMask", C.c_void_p),
    ]


class FWP_CONDITION_VALUE0(C.Structure):
    _fields_ = [("type", C.c_uint32), ("value", _FWP_CONDITION_UNION)]


class FWPM_FILTER_CONDITION0(C.Structure):
    _fields_ = [
        ("fieldKey", GUID), ("matchType", C.c_uint32),
        ("conditionValue", FWP_CONDITION_VALUE0),
    ]


class FWPM_NET_EVENT_ENUM_TEMPLATE0(C.Structure):
    _fields_ = [
        ("startTime", FILETIME), ("endTime", FILETIME),
        ("numFilterConditions", C.c_uint32),
        ("filterCondition", C.POINTER(FWPM_FILTER_CONDITION0)),
    ]


class FWP_BYTE_ARRAY16(C.Structure):
    _fields_ = [("byteArray16", C.c_ubyte * 16)]


class _LOCAL_ADDRESS_UNION(C.Union):
    _fields_ = [("localAddrV4", C.c_uint32), ("localAddrV6", FWP_BYTE_ARRAY16)]


class _REMOTE_ADDRESS_UNION(C.Union):
    _fields_ = [("remoteAddrV4", C.c_uint32), ("remoteAddrV6", FWP_BYTE_ARRAY16)]


class FWPM_NET_EVENT_HEADER2(C.Structure):
    _fields_ = [
        ("timeStamp", FILETIME), ("flags", C.c_uint32),
        ("ipVersion", C.c_int32), ("ipProtocol", C.c_uint8),
        ("localAddress", _LOCAL_ADDRESS_UNION),
        ("remoteAddress", _REMOTE_ADDRESS_UNION),
        ("localPort", C.c_uint16), ("remotePort", C.c_uint16),
        ("scopeId", C.c_uint32), ("appId", FWP_BYTE_BLOB),
        ("userId", C.c_void_p), ("addressFamily", C.c_uint32),
        ("packageSid", C.c_void_p),
    ]


class FWPM_NET_EVENT_CLASSIFY_DROP2(C.Structure):
    _fields_ = [
        ("filterId", C.c_uint64), ("layerId", C.c_uint16),
        ("reauthReason", C.c_uint32), ("originalProfile", C.c_uint32),
        ("currentProfile", C.c_uint32), ("msFwpDirection", C.c_uint32),
        ("isLoopback", C.c_int32), ("vSwitchId", FWP_BYTE_BLOB),
        ("vSwitchSourcePort", C.c_uint32), ("vSwitchDestinationPort", C.c_uint32),
    ]


class _FWPM_NET_EVENT_DATA_UNION(C.Union):
    _fields_ = [("classifyDrop", C.c_void_p), ("otherEvent", C.c_void_p)]


class FWPM_NET_EVENT2(C.Structure):
    _fields_ = [
        ("header", FWPM_NET_EVENT_HEADER2), ("type", C.c_uint32),
        ("eventData", _FWPM_NET_EVENT_DATA_UNION),
    ]


def _guid(text: str) -> GUID:
    import uuid
    return GUID.from_buffer_copy(uuid.UUID(text.strip("{}")).bytes_le)


_CONDITION_APP_ID = _guid("D78E1E87-8644-4EA5-9437-D809ECEFC971")
_CONDITION_LOCAL_ADDRESS = _guid("D9EE00DE-C1EF-4617-BFE3-FFD8F5A08957")
_CONDITION_REMOTE_ADDRESS = _guid("B235AE9A-1D64-49B8-A44C-5FF3D9095045")
_CONDITION_IP_PROTOCOL = _guid("3971EF2B-623E-4F9A-8CB1-6E79B806B9A7")


@dataclass(frozen=True)
class SdkCallStatus:
    function: str
    status: int | None
    win32_error: int | None

    @property
    def status_hex(self) -> str | None:
        return None if self.status is None else f"0x{self.status:08x}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "status": self.status,
            "status_hex": None if self.status is None else f"0x{self.status:08x}",
            "win32_error": self.win32_error,
        }


@dataclass(frozen=True)
class WfpDropEvent:
    event_type: int
    flags: int
    timestamp_filetime_100ns: int
    ip_version: int | None
    ip_protocol: int | None
    local_address: str | None
    remote_address: str | None
    local_address_sdk_u32: int | None
    remote_address_sdk_u32: int | None
    local_port: int | None
    remote_port: int | None
    app_id_bytes_hex: str | None
    app_id_sha256: str | None
    user_sid: str | None
    user_sid_bytes_hex: str | None
    user_sid_sha256: str | None
    package_sid: str | None
    package_sid_bytes_hex: str | None
    package_sid_sha256: str | None
    filter_id: int | None
    layer_id: int | None
    direction: str | None
    direction_sdk_value: int | None
    is_loopback: bool | None
    event_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "event_reasons": list(self.event_reasons)}


@dataclass(frozen=True)
class WfpNetEventObservation:
    state: str
    diagnostic_state: str
    image_path: str
    app_id_bytes_hex: str | None
    app_id_sha256: str | None
    collection_enabled: bool | None
    window_start_filetime_100ns: int | None
    window_end_filetime_100ns: int | None
    enum_create_started_filetime_100ns: int | None
    enum_create_returned_filetime_100ns: int | None
    pages: int
    records_returned: int
    events: tuple[WfpDropEvent, ...]
    exhausted: bool
    truncated: bool
    reason_codes: tuple[str, ...]
    sdk_statuses: tuple[SdkCallStatus, ...]
    sdk_source: str = SDK_SOURCE
    sdk_metadata: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "comsol-mcp.windows-t037-wfp-net-events/1",
            "state": self.state,
            "diagnostic_state": self.diagnostic_state,
            "blocking_eligible": False,
            "image_path": self.image_path,
            "app_id_bytes_hex": self.app_id_bytes_hex,
            "app_id_sha256": self.app_id_sha256,
            "collection_enabled": self.collection_enabled,
            "window_start_filetime_100ns": self.window_start_filetime_100ns,
            "window_end_filetime_100ns": self.window_end_filetime_100ns,
            "enum_create_started_filetime_100ns": self.enum_create_started_filetime_100ns,
            "enum_create_returned_filetime_100ns": self.enum_create_returned_filetime_100ns,
            "pages": self.pages,
            "records_returned": self.records_returned,
            "events": [event.as_dict() for event in self.events],
            "exhausted": self.exhausted,
            "truncated": self.truncated,
            "reason_codes": list(self.reason_codes),
            "sdk_statuses": [status.as_dict() for status in self.sdk_statuses],
            "sdk_source": self.sdk_source,
            "sdk_metadata": dict(self.sdk_metadata),
        }


@dataclass(frozen=True)
class EventCorrelationExpectation:
    """Untrusted comparison data. It never confers filter ownership or authority."""
    filter_id: int
    layer_id: int
    ip_version: int
    protocol: int
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int
    direction: str
    start_filetime_100ns: int
    end_filetime_100ns: int
    app_id_bytes: bytes
    package_sid: str


@dataclass(frozen=True)
class EventCorrelationDiagnostic:
    state: str
    matched_indexes: tuple[int, ...]
    reason_codes: tuple[str, ...]
    blocking_eligible: bool = False
    qualification_state: str = "UNVERIFIED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "matched_indexes": list(self.matched_indexes),
            "reason_codes": list(self.reason_codes),
            "blocking_eligible": False,
            "qualification_state": "UNVERIFIED",
        }


class _SdkFailure(Exception):
    def __init__(self, status: SdkCallStatus):
        self.call_status = status
        super().__init__(f"{status.function} returned {status.status_hex if hasattr(status, 'status_hex') else status.status!r}")


class WfpEventOwnershipError(NativeResourceError):
    """A native return could not be released; owner and exact handles stay held."""
    def __init__(self, message: str, owner: "_WfpEventOwner"):
        super().__init__(
            "NATIVE_RESOURCE_RELEASE_UNKNOWN", message, safe_retry=False,
            stage="pre_dispatch", details={"owner_state": owner.state},
        )
        self.owner = owner


def _filetime_value(value: FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _filetime_from_value(value: int) -> FILETIME:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("FILETIME tick value must be an unsigned 64-bit integer")
    return FILETIME(value & 0xFFFFFFFF, value >> 32)


def _canonical_image_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > _MAX_IMAGE_PATH_CHARS
            or "\x00" in value or not ntpath.isabs(value)):
        raise ValueError("a bounded absolute Windows image path is required")
    drive, _ = ntpath.splitdrive(value)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        raise ValueError("image path must be an absolute local drive path")
    return ntpath.normpath(value)


def _status(api: Any, function: str, result: Any) -> SdkCallStatus:
    raw = getattr(result, "value", result)
    if type(raw) is not int:
        raise TypeError(f"{function} did not return a DWORD")
    last_error = getattr(api, "last_error", None)
    try:
        last_error_value = last_error() if callable(last_error) else getattr(C, "get_last_error", lambda: 0)()
    except Exception:
        last_error_value = None
    return SdkCallStatus(function, raw & 0xFFFFFFFF, last_error_value)


def _status_or_raise(api: Any, function: str, result: Any, statuses: list[SdkCallStatus]) -> None:
    status = _status(api, function, result)
    statuses.append(status)
    if status.status != 0:
        raise _SdkFailure(status)


def _pointer_value(pointer: Any) -> int | None:
    if not pointer:
        return None
    return C.cast(pointer, C.c_void_p).value


def _release_entry(owner: "_WfpEventOwner", entry: Any) -> None:
    entry.release_owned(owner)


class _WfpOutputSlot:
    """Pre-call strong holder for one SDK output transfer and its exact cleanup ABI."""

    _CLOSER_FOR_KIND = {
        "memory": "FwpmFreeMemory0",
        "engine": "FwpmEngineClose0",
        "enum": "FwpmNetEventDestroyEnumHandle0",
    }

    def __init__(self, owner: "_WfpEventOwner", function: str, sdk_call: Any,
                 args: tuple[Any, ...], slots: tuple[Any, ...], kind: str,
                 *, parent: Any = None, keepalive: tuple[Any, ...] = ()):
        if kind not in self._CLOSER_FOR_KIND:
            raise ValueError("unsupported WFP output ownership kind")
        self.owner = owner
        self.api = owner.api
        self.function = function
        self.sdk_call = sdk_call
        self.args = args
        self.slots = slots
        self.kind = kind
        self.parent = parent
        self.keepalive = tuple(keepalive)
        self.closer_name = self._CLOSER_FOR_KIND[kind]
        self.closer = getattr(self.api, self.closer_name)
        if not callable(self.closer):
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP SDK closer is unavailable",
                safe_retry=False, stage="pre_dispatch",
            )
        self.free_argument = (
            C.cast(C.byref(slots[0]), C.POINTER(C.c_void_p)) if kind == "memory" else None
        )
        self.original_value: int | None = None
        self.handle_argument: Any = None
        self.release_invoked = False
        self.release_outcome_unknown = False
        self.release_call_state = "NOT_INVOKED"
        self.release_status: int | None = None
        self.state = "PENDING"
        self.raw_result: Any = None
        self.raw_slots: tuple[Any, ...] = ()
        self.failure_slots: tuple[Any, ...] = ()
        self.failure: str | None = None

    def read_slots(self) -> tuple[Any, ...]:
        values = []
        for slot in self.slots:
            try:
                if isinstance(slot, C.c_uint32):
                    values.append(int(slot.value))
                else:
                    values.append(_pointer_value(slot))
            except BaseException as exc:
                values.append(("unreadable", type(exc).__name__))
        return tuple(values)

    def snapshot(self) -> tuple[Any, ...]:
        self.raw_slots = self.read_slots()
        return self.raw_slots

    def mark_unknown(self, reason: str, raw_result: Any = None) -> None:
        self.raw_result = raw_result
        self.failure_slots = self.read_slots()
        if not self.raw_slots:
            self.raw_slots = self.failure_slots
        self.failure = reason
        self.state = "UNKNOWN"
        self.owner.state = "UNKNOWN"

    def _check_closer_binding(self) -> None:
        current = getattr(self.api, self.closer_name, None)
        original_self = getattr(self.closer, "__self__", None)
        original_func = getattr(self.closer, "__func__", None)
        same_bound_method = (
            original_self is not None and original_func is not None
            and getattr(current, "__self__", None) is original_self
            and getattr(current, "__func__", None) is original_func
        )
        if current is not self.closer and not same_bound_method:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP SDK closer binding drifted after acquisition",
                safe_retry=False, stage="pre_dispatch",
                details={"closer": self.closer_name, "output_function": self.function},
            )

    def _release_memory(self, registry_value: Any) -> Any:
        if type(registry_value) is not int or registry_value != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP allocation registry value drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        self._check_closer_binding()
        if _pointer_value(self.slots[0]) != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP allocation output slot drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        if self.release_invoked:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN",
                "FwpmFreeMemory0 outcome is uncertain; the original output slot will not be freed again",
                safe_retry=False, stage="pre_dispatch",
            )
        self.release_invoked = True
        try:
            return self.closer(self.free_argument)
        except BaseException:
            # FwpmFreeMemory0 has a void ABI. If its call raises after clearing
            # the SDK slot, the native transfer cannot be distinguished from a
            # completed free, so explicit recovery must retain this owner.
            self.release_outcome_unknown = True
            raise

    def _invoke_dword_release(self, *args: Any) -> int:
        """Normalize one SDK DWORD once and cache success for registry recovery."""
        if self.release_call_state == "SUCCEEDED":
            if (not self.release_outcome_unknown and type(self.release_status) is int
                    and self.release_status == 0):
                # A bookkeeping retry consumes the confirmed scalar, never
                # the original SDK object and never calls the native closer.
                return self.release_status
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "confirmed WFP release result was corrupted",
                safe_retry=False, stage="pre_dispatch",
            )
        if self.release_outcome_unknown or self.release_call_state in {"UNKNOWN", "INVOKING"}:
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP release outcome is uncertain",
                safe_retry=False, stage="pre_dispatch",
            )
        if self.release_call_state not in {"NOT_INVOKED", "KNOWN_FAILURE"}:
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP release call state is invalid",
                safe_retry=False, stage="pre_dispatch",
            )

        # Record dispatch before entering native code so a lost return cannot
        # be mistaken for a call that never happened.
        self.release_call_state = "INVOKING"
        try:
            result = self.closer(*args)
        except BaseException:
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise
        try:
            status = getattr(result, "value", result)
        except BaseException:
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise
        if type(status) is not int or not 0 <= status <= 0xFFFFFFFF:
            self.release_call_state = "UNKNOWN"
            self.release_outcome_unknown = True
            raise TypeError("WFP release closer did not return a valid DWORD scalar")

        self.release_status = status
        self.release_call_state = "SUCCEEDED" if status == 0 else "KNOWN_FAILURE"
        return status

    def _release_engine(self, registry_value: Any) -> Any:
        if type(registry_value) is not int or registry_value != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP engine registry value drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        self._check_closer_binding()
        if _pointer_value(self.slots[0]) != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP engine output slot drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        if self.handle_argument.value != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP engine close argument drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        return self._invoke_dword_release(self.handle_argument)

    def _release_enum(self, registry_value: Any) -> Any:
        if type(registry_value) is not int or registry_value != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP enum registry value drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        self._check_closer_binding()
        engine = self.parent
        if engine is None or engine.value is None or engine.handle != engine.value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP engine owner is unavailable",
                safe_retry=False, stage="pre_dispatch",
            )
        engine_output = next(
            (item for item in engine.keepalive if isinstance(item, _WfpOutputSlot)), None
        )
        if engine_output is None:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP engine output owner is unavailable",
                safe_retry=False, stage="pre_dispatch",
            )
        engine_slot = engine_output.slots[0]
        if _pointer_value(engine_slot) != engine.value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "parent WFP engine output slot drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        if _pointer_value(self.slots[0]) != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP enum output slot drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        engine_argument = self.keepalive[0]
        if engine_argument.value != engine.value or self.handle_argument.value != self.original_value:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP enum destroy argument drifted",
                safe_retry=False, stage="pre_dispatch",
            )
        return self._invoke_dword_release(engine_argument, self.handle_argument)


class _WfpEventOwner:
    def __init__(self, api: Any, *, seal: object):
        if seal is not _OWNER_SEAL:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "private original WFP API owner required",
                safe_retry=False, stage="pre_dispatch",
            )
        self.api = api
        self.registry = registry_for(self)
        self.state = "ACQUIRED"
        self.operation_error: BaseException | None = None
        self._pending_outputs: list[_WfpOutputSlot] = []
        self._seal = seal

    def begin_output(self, function: str, args: tuple[Any, ...], slots: tuple[Any, ...],
                     kind: str, *, parent: Any = None,
                     keepalive: tuple[Any, ...] = ()) -> _WfpOutputSlot:
        if self._pending_outputs:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "an earlier WFP output transfer is still unresolved",
                safe_retry=False, stage="pre_dispatch",
            )
        sdk_call = getattr(self.api, function)
        if not callable(sdk_call):
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP SDK acquisition binding is unavailable",
                safe_retry=False, stage="pre_dispatch",
            )
        output = _WfpOutputSlot(
            self, function, sdk_call, args, slots, kind,
            parent=parent, keepalive=keepalive,
        )
        self._pending_outputs.append(output)
        return output

    def invoke_output(self, function: str, args: tuple[Any, ...], slots: tuple[Any, ...],
                      kind: str, *, parent: Any = None,
                      keepalive: tuple[Any, ...] = ()) -> tuple[SdkCallStatus, Any]:
        output = self.begin_output(function, args, slots, kind,
                                   parent=parent, keepalive=keepalive)
        try:
            raw_result = output.sdk_call(*output.args)
        except BaseException:
            output.mark_unknown("SDK_CALL_RAISED")
            raise
        output.snapshot()
        try:
            status = _status(self.api, function, raw_result)
        except BaseException:
            output.mark_unknown("SDK_STATUS_SHAPE_INVALID", raw_result)
            raise
        try:
            entry = self._complete_output(output)
        except BaseException:
            output.mark_unknown("OUTPUT_OWNERSHIP_TRANSFER_UNKNOWN", raw_result)
            raise
        return status, entry

    def _complete_output(self, output: _WfpOutputSlot) -> Any:
        raw_slots = output.raw_slots
        current_slots = output.read_slots()
        if current_slots != raw_slots:
            output.failure_slots = current_slots
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP SDK output slot changed before ownership registration",
                safe_retry=False, stage="pre_dispatch",
                details={"function": output.function, "returned_slots": raw_slots,
                         "current_slots": current_slots},
            )
        value = raw_slots[0]
        if value is None:
            output.state = "COMPLETE_NO_OUTPUT"
            self._pending_outputs.remove(output)
            return None
        if type(value) is not int or value <= 0:
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP SDK returned an invalid native output value",
                safe_retry=False, stage="pre_dispatch",
            )
        output.original_value = value
        if output.kind == "memory":
            closer = output._release_memory
            mode = "VOID"
            label = {
                "FwpmGetAppIdFromFileName0": "wfp_application_id",
                "FwpmEngineGetOption0": "wfp_collect_net_events_option",
                "FwpmNetEventEnum2": "wfp_net_event_page",
            }.get(output.function, "wfp_output_allocation")
            keepalive = (self, self.api, output, output.slots, output.args,
                         output.sdk_call, output.closer, output.free_argument, *output.keepalive)
            domain = f"wfp_free:{id(output.slots[0])}"
        elif output.kind == "engine":
            output.handle_argument = C.c_void_p(value)
            closer = output._release_engine
            mode = "DWORD0"
            label = "wfp_engine"
            keepalive = (self, self.api, output, output.slots, output.args,
                         output.sdk_call, output.closer, output.handle_argument, *output.keepalive)
            domain = "wfp_engine"
        else:
            if output.parent is None:
                raise NativeResourceError(
                    "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP enum has no original engine owner",
                    safe_retry=False, stage="pre_dispatch",
                )
            engine_value = output.parent.value
            if type(engine_value) is not int or engine_value <= 0:
                raise NativeResourceError(
                    "NATIVE_RESOURCE_RELEASE_UNKNOWN", "WFP enum parent handle is invalid",
                    safe_retry=False, stage="pre_dispatch",
                )
            output.keepalive = (C.c_void_p(engine_value), *output.keepalive)
            output.handle_argument = C.c_void_p(value)
            closer = output._release_enum
            mode = "DWORD0"
            label = "wfp_net_event_enum"
            keepalive = (self, self.api, output.parent, output, output.slots, output.args,
                         output.sdk_call, output.closer, *output.keepalive, output.handle_argument)
            domain = "wfp_enum"
        entry = self.registry.acquire(
            self.api, value, closer, mode=mode, label=label,
            keepalive=keepalive, domain=domain,
        )
        output.state = "ACQUIRED"
        output.entry = entry
        self._pending_outputs.remove(output)
        return entry

    def release_scope(self, scope: Any) -> None:
        if scope.registry is not self.registry or scope.thread_id != threading.get_ident():
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP owner and thread required",
                safe_retry=False, stage="pre_dispatch",
            )
        if self._pending_outputs:
            output = self._pending_outputs[0]
            raise NativeResourceError(
                "NATIVE_RESOURCE_RELEASE_UNKNOWN",
                "WFP SDK output slot transfer is unresolved; parent resources remain retained",
                safe_retry=False, stage="pre_dispatch",
                details={"function": output.function, "kind": output.kind,
                         "failure": output.failure, "raw_slots": output.raw_slots},
            )
        for entry in sorted(self.registry.unresolved(), key=lambda item: item.seq, reverse=True):
            if entry.seq <= scope.seq:
                continue
            _release_entry(self, entry)
        self.state = "RELEASED"

    def recover_owned(self, original_owner: Any) -> None:
        global _active_owner
        with _owner_lock:
            if (self._seal is not _OWNER_SEAL or original_owner is not self
                    or _active_owner is not self or self.state != "UNKNOWN"):
                raise NativeResourceError(
                    "NATIVE_RESOURCE_RELEASE_UNKNOWN", "explicit recovery needs the original retained WFP owner",
                    safe_retry=False, stage="pre_dispatch",
                )
            if self._pending_outputs:
                output = self._pending_outputs[0]
                raise NativeResourceError(
                    "NATIVE_RESOURCE_RELEASE_UNKNOWN",
                    "original WFP output transfer is unknown and cannot be reconstructed during recovery",
                    safe_retry=False, stage="pre_dispatch",
                    details={"function": output.function, "kind": output.kind,
                             "failure": output.failure, "raw_slots": output.raw_slots},
                )
            for entry in sorted(self.registry.unresolved(), key=lambda item: item.seq, reverse=True):
                try:
                    self.registry.retry_owned(entry, self)
                except BaseException:
                    self.state = "UNKNOWN"
                    raise
            if self.registry.unresolved():
                raise NativeResourceError(
                    "NATIVE_RESOURCE_RELEASE_UNKNOWN", "original WFP owner still retains native resources",
                    safe_retry=False, stage="pre_dispatch",
                )
            self.state = "RELEASED"
            _active_owner = None


class CtypesWindowsNetEventApi:
    """Concrete WFP SDK ABI owner, instantiated only on Windows."""
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows WFP SDK is unavailable on this platform")
        self._fwpu = C.WinDLL("fwpuclnt.dll", use_last_error=True)
        self._kernel = C.WinDLL("kernel32.dll", use_last_error=True)
        self._advapi = C.WinDLL("advapi32.dll", use_last_error=True)

        self.FwpmEngineOpen0 = self._bind(
            self._fwpu, "FwpmEngineOpen0",
            [C.c_wchar_p, C.c_uint32, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)], C.c_uint32,
        )
        self.FwpmEngineClose0 = self._bind(
            self._fwpu, "FwpmEngineClose0", [C.c_void_p], C.c_uint32,
        )
        self.FwpmEngineGetOption0 = self._bind(
            self._fwpu, "FwpmEngineGetOption0",
            [C.c_void_p, C.c_uint32, C.POINTER(C.POINTER(FWP_VALUE0))], C.c_uint32,
        )
        self.FwpmGetAppIdFromFileName0 = self._bind(
            self._fwpu, "FwpmGetAppIdFromFileName0",
            [C.c_wchar_p, C.POINTER(C.POINTER(FWP_BYTE_BLOB))], C.c_uint32,
        )
        self.FwpmNetEventCreateEnumHandle0 = self._bind(
            self._fwpu, "FwpmNetEventCreateEnumHandle0",
            [C.c_void_p, C.POINTER(FWPM_NET_EVENT_ENUM_TEMPLATE0), C.POINTER(C.c_void_p)], C.c_uint32,
        )
        self.FwpmNetEventEnum2 = self._bind(
            self._fwpu, "FwpmNetEventEnum2",
            [C.c_void_p, C.c_void_p, C.c_uint32,
             C.POINTER(C.POINTER(C.POINTER(FWPM_NET_EVENT2))), C.POINTER(C.c_uint32)], C.c_uint32,
        )
        self.FwpmNetEventDestroyEnumHandle0 = self._bind(
            self._fwpu, "FwpmNetEventDestroyEnumHandle0", [C.c_void_p, C.c_void_p], C.c_uint32,
        )
        self.FwpmFreeMemory0 = self._bind(
            self._fwpu, "FwpmFreeMemory0", [C.POINTER(C.c_void_p)], None,
        )
        self._get_system_time = self._bind(
            self._kernel, "GetSystemTimeAsFileTime", [C.POINTER(FILETIME)], None,
        )
        self._is_valid_sid = self._bind(
            self._advapi, "IsValidSid", [C.c_void_p], C.c_int,
        )
        self._get_length_sid = self._bind(
            self._advapi, "GetLengthSid", [C.c_void_p], C.c_uint32,
        )
        self.sdk_metadata = (("library", "fwpuclnt.dll"), ("binding", "ctypes; Windows SDK ABI"))

    @staticmethod
    def _bind(library: Any, name: str, argtypes: list[Any], restype: Any) -> Any:
        fn = getattr(library, name)
        fn.argtypes = argtypes
        fn.restype = restype
        return fn

    @staticmethod
    def last_error() -> int:
        return C.get_last_error()

    def system_time_filetime(self) -> FILETIME:
        value = FILETIME()
        self._get_system_time(C.byref(value))
        return value

    def read_sid(self, pointer: Any) -> tuple[str, bytes]:
        address = C.c_void_p(pointer)
        if not self._is_valid_sid(address):
            raise ValueError("SDK event SID pointer failed IsValidSid")
        length = int(self._get_length_sid(address))
        if length < 8 or length > MAX_SID_BYTES:
            raise ValueError("SDK event SID length is outside 8..68 bytes")
        raw = C.string_at(address, length)
        return _sid_to_string(raw), raw


def _sid_to_string(raw: bytes) -> str:
    if type(raw) is not bytes or not 8 <= len(raw) <= MAX_SID_BYTES:
        raise ValueError("bounded binary SID required")
    revision, count = raw[0], raw[1]
    expected = 8 + 4 * count
    if revision != 1 or count > 15 or expected != len(raw):
        raise ValueError("SDK SID bytes have an invalid revision or subauthority length")
    authority = int.from_bytes(raw[2:8], "big")
    sub_authorities = [
        int.from_bytes(raw[8 + 4 * i:12 + 4 * i], "little") for i in range(count)
    ]
    return "S-" + "-".join(str(value) for value in (revision, authority, *sub_authorities))


def _build_enum_template(start_ticks: int, end_ticks: int, app_id: bytes) -> tuple[Any, ...]:
    if type(app_id) is not bytes or not 1 <= len(app_id) <= MAX_APP_ID_BYTES:
        raise ValueError("bounded non-empty AppID bytes are required")
    app_storage = C.create_string_buffer(app_id, len(app_id))
    app_blob = FWP_BYTE_BLOB(len(app_id), C.cast(app_storage, C.POINTER(C.c_ubyte)))
    local = FWP_V4_ADDR_AND_MASK(0x7F000001, 0xFFFFFFFF)
    remote = FWP_V4_ADDR_AND_MASK(0x7F000001, 0xFFFFFFFF)
    rows = (FWPM_FILTER_CONDITION0 * 4)()

    rows[0].fieldKey = _guid("D78E1E87-8644-4EA5-9437-D809ECEFC971")
    rows[0].matchType = FWP_MATCH_EQUAL
    rows[0].conditionValue.type = FWP_BYTE_BLOB_TYPE
    rows[0].conditionValue.value.byteBlob = C.cast(C.pointer(app_blob), C.c_void_p).value

    rows[1].fieldKey = _guid("3971EF2B-623E-4F9A-8CB1-6E79B806B9A7")
    rows[1].matchType = FWP_MATCH_EQUAL
    rows[1].conditionValue.type = FWP_UINT8
    rows[1].conditionValue.value.uint8 = 6  # IPPROTO_TCP

    rows[2].fieldKey = _guid("D9EE00DE-C1EF-4617-BFE3-FFD8F5A08957")
    rows[2].matchType = FWP_MATCH_EQUAL
    rows[2].conditionValue.type = FWP_V4_ADDR_MASK
    rows[2].conditionValue.value.v4AddrMask = C.cast(C.pointer(local), C.c_void_p).value

    rows[3].fieldKey = _guid("B235AE9A-1D64-49B8-A44C-5FF3D9095045")
    rows[3].matchType = FWP_MATCH_EQUAL
    rows[3].conditionValue.type = FWP_V4_ADDR_MASK
    rows[3].conditionValue.value.v4AddrMask = C.cast(C.pointer(remote), C.c_void_p).value

    template = FWPM_NET_EVENT_ENUM_TEMPLATE0(
        _filetime_from_value(start_ticks), _filetime_from_value(end_ticks), 4,
        C.cast(rows, C.POINTER(FWPM_FILTER_CONDITION0)),
    )
    return template, rows, local, remote, app_storage, app_blob


def _capture_app_id(owner: _WfpEventOwner, image_path: str, statuses: list[SdkCallStatus]) -> bytes:
    slot = C.POINTER(FWP_BYTE_BLOB)()
    args = (image_path, C.byref(slot))
    status, entry = owner.invoke_output(
        "FwpmGetAppIdFromFileName0", args, (slot,), "memory",
    )
    statuses.append(status)
    if status.status != 0:
        raise _SdkFailure(status)
    if not _pointer_value(slot):
        raise ValueError("FwpmGetAppIdFromFileName0 succeeded without an AppID allocation")
    try:
        blob = slot.contents
        size = int(blob.size)
        if not 1 <= size <= MAX_APP_ID_BYTES or not blob.data:
            raise ValueError("SDK AppID blob has a null pointer or exceeds the strict byte bound")
        result = C.string_at(blob.data, size)
    finally:
        if entry is not None:
            _release_entry(owner, entry)
    return result


def _read_collection_option(owner: _WfpEventOwner, engine: Any,
                            statuses: list[SdkCallStatus]) -> bool:
    slot = C.POINTER(FWP_VALUE0)()
    args = (engine.handle, FWPM_ENGINE_COLLECT_NET_EVENTS, C.byref(slot))
    status, entry = owner.invoke_output(
        "FwpmEngineGetOption0", args, (slot,), "memory", parent=engine,
    )
    statuses.append(status)
    if status.status != 0:
        raise _SdkFailure(status)
    if not _pointer_value(slot):
        raise ValueError("FwpmEngineGetOption0 succeeded without an FWP_VALUE0 allocation")
    try:
        value = slot.contents
        if int(value.type) != 3:  # FWP_UINT32 from the documented GetOption0 contract.
            raise ValueError(f"COLLECT_NET_EVENTS returned FWP type {int(value.type)}, expected FWP_UINT32")
        enabled = int(value.value.uint32)
        if enabled not in (0, 1):
            raise ValueError("COLLECT_NET_EVENTS returned a value outside 0 or 1")
        return enabled == 1
    finally:
        if entry is not None:
            _release_entry(owner, entry)


def _read_sid(api: Any, pointer: Any) -> tuple[str, bytes]:
    if not pointer:
        raise ValueError("SID flag was set but SDK pointer is null")
    reader = getattr(api, "read_sid", None)
    if not callable(reader):
        raise ValueError("SDK API owner has no bounded SID reader")
    text, raw = reader(pointer)
    if type(text) is not str or type(raw) is not bytes or not 8 <= len(raw) <= MAX_SID_BYTES:
        raise ValueError("SDK SID reader returned an invalid bounded result")
    if _sid_to_string(raw) != text:
        raise ValueError("SDK SID text does not match its original binary bytes")
    return text, raw


def _decode_event(api: Any, event_pointer: Any, start_ticks: int,
                  end_ticks: int) -> WfpDropEvent:
    if not event_pointer:
        raise ValueError("FwpmNetEventEnum2 returned a null event pointer")
    event = event_pointer.contents
    header = event.header
    flags = int(header.flags)
    reasons: list[str] = []
    if flags & ~_KNOWN_HEADER_FLAGS:
        reasons.append("UNKNOWN_HEADER_FLAGS")
    timestamp = _filetime_value(header.timeStamp)
    if not start_ticks <= timestamp <= end_ticks:
        reasons.append("EVENT_TIMESTAMP_OUTSIDE_REQUESTED_WINDOW")

    event_type = int(event.type)
    ip_version: int | None = None
    protocol: int | None = None
    local_address: str | None = None
    remote_address: str | None = None
    local_raw: int | None = None
    remote_raw: int | None = None
    local_port: int | None = None
    remote_port: int | None = None
    app_hex: str | None = None
    app_digest: str | None = None
    user_sid: str | None = None
    user_raw: bytes | None = None
    package_sid: str | None = None
    package_raw: bytes | None = None
    filter_id: int | None = None
    layer_id: int | None = None
    direction: str | None = None
    direction_value: int | None = None
    is_loopback: bool | None = None

    if event_type != FWPM_NET_EVENT_TYPE_CLASSIFY_DROP:
        reasons.append("UNSUPPORTED_NET_EVENT_TYPE")
        return WfpDropEvent(
            event_type=event_type, flags=flags,
            timestamp_filetime_100ns=timestamp, ip_version=None, ip_protocol=None,
            local_address=None, remote_address=None, local_address_sdk_u32=None,
            remote_address_sdk_u32=None, local_port=None, remote_port=None,
            app_id_bytes_hex=None, app_id_sha256=None, user_sid=None,
            user_sid_bytes_hex=None, user_sid_sha256=None, package_sid=None,
            package_sid_bytes_hex=None, package_sid_sha256=None, filter_id=None,
            layer_id=None, direction=None, direction_sdk_value=None,
            is_loopback=None, event_reasons=tuple(reasons),
        )

    if not event.eventData.classifyDrop:
        reasons.append("CLASSIFY_DROP_POINTER_MISSING")
    else:
        drop = C.cast(event.eventData.classifyDrop, C.POINTER(FWPM_NET_EVENT_CLASSIFY_DROP2)).contents
        filter_id = int(drop.filterId)
        layer_id = int(drop.layerId)
        direction_value = int(drop.msFwpDirection)
        direction = {
            FWP_DIRECTION_IN: "IN", FWP_DIRECTION_OUT: "OUT",
            FWP_DIRECTION_FORWARD: "FORWARD",
        }.get(direction_value)
        if direction is None:
            reasons.append("UNKNOWN_CLASSIFY_DROP_DIRECTION")
        raw_loopback = int(drop.isLoopback)
        if raw_loopback in (0, 1):
            is_loopback = raw_loopback == 1
        else:
            reasons.append("INVALID_CLASSIFY_DROP_LOOPBACK_BOOL")

    def present(flag: int, field: str) -> bool:
        if flags & flag:
            return True
        reasons.append("MISSING_FLAG_" + field)
        return False

    version_is_present = present(FWPM_NET_EVENT_FLAG_IP_VERSION_SET, "IP_VERSION")
    if version_is_present:
        ip_version = int(header.ipVersion)
        if ip_version not in (FWP_IP_VERSION_V4, FWP_IP_VERSION_V6):
            reasons.append("UNKNOWN_IP_VERSION")

    if present(FWPM_NET_EVENT_FLAG_IP_PROTOCOL_SET, "IP_PROTOCOL"):
        protocol = int(header.ipProtocol)

    local_is_present = present(FWPM_NET_EVENT_FLAG_LOCAL_ADDR_SET, "LOCAL_ADDRESS")
    remote_is_present = present(FWPM_NET_EVENT_FLAG_REMOTE_ADDR_SET, "REMOTE_ADDRESS")
    if (local_is_present or remote_is_present) and not version_is_present:
        reasons.append("ADDRESS_FLAGS_WITHOUT_IP_VERSION_FLAG")
    elif ip_version == FWP_IP_VERSION_V4:
        if local_is_present:
            local_raw = int(header.localAddress.localAddrV4)
            local_address = str(ipaddress.IPv4Address(local_raw))
        if remote_is_present:
            remote_raw = int(header.remoteAddress.remoteAddrV4)
            remote_address = str(ipaddress.IPv4Address(remote_raw))
    elif ip_version == FWP_IP_VERSION_V6:
        if local_is_present:
            local_bytes = bytes(header.localAddress.localAddrV6.byteArray16)
            local_address = str(ipaddress.IPv6Address(local_bytes))
        if remote_is_present:
            remote_bytes = bytes(header.remoteAddress.remoteAddrV6.byteArray16)
            remote_address = str(ipaddress.IPv6Address(remote_bytes))
    elif local_is_present or remote_is_present:
        reasons.append("ADDRESS_UNION_NOT_READ_FOR_UNKNOWN_IP_VERSION")

    if present(FWPM_NET_EVENT_FLAG_LOCAL_PORT_SET, "LOCAL_PORT"):
        local_port = int(header.localPort)
    if present(FWPM_NET_EVENT_FLAG_REMOTE_PORT_SET, "REMOTE_PORT"):
        remote_port = int(header.remotePort)

    if present(FWPM_NET_EVENT_FLAG_APP_ID_SET, "APP_ID"):
        blob = header.appId
        size = int(blob.size)
        if size < 1 or size > MAX_APP_ID_BYTES:
            reasons.append("APP_ID_SIZE_OUT_OF_BOUNDS")
        elif not blob.data:
            reasons.append("APP_ID_POINTER_NULL")
        else:
            raw = C.string_at(blob.data, size)
            app_hex = raw.hex()
            app_digest = hashlib.sha256(raw).hexdigest()

    if present(FWPM_NET_EVENT_FLAG_USER_ID_SET, "USER_ID"):
        try:
            user_sid, user_raw = _read_sid(api, header.userId)
        except (TypeError, ValueError, OSError) as exc:
            reasons.append("USER_SID_INVALID:" + type(exc).__name__)
    if present(FWPM_NET_EVENT_FLAG_PACKAGE_ID_SET, "PACKAGE_ID"):
        try:
            package_sid, package_raw = _read_sid(api, header.packageSid)
        except (TypeError, ValueError, OSError) as exc:
            reasons.append("PACKAGE_SID_INVALID:" + type(exc).__name__)

    return WfpDropEvent(
        event_type=event_type, flags=flags, timestamp_filetime_100ns=timestamp,
        ip_version=ip_version, ip_protocol=protocol,
        local_address=local_address, remote_address=remote_address,
        local_address_sdk_u32=local_raw, remote_address_sdk_u32=remote_raw,
        local_port=local_port, remote_port=remote_port,
        app_id_bytes_hex=app_hex, app_id_sha256=app_digest,
        user_sid=user_sid, user_sid_bytes_hex=None if user_raw is None else user_raw.hex(),
        user_sid_sha256=None if user_raw is None else hashlib.sha256(user_raw).hexdigest(),
        package_sid=package_sid,
        package_sid_bytes_hex=None if package_raw is None else package_raw.hex(),
        package_sid_sha256=None if package_raw is None else hashlib.sha256(package_raw).hexdigest(),
        filter_id=filter_id, layer_id=layer_id, direction=direction,
        direction_sdk_value=direction_value, is_loopback=is_loopback,
        event_reasons=tuple(reasons),
    )


def _observation(image_path: str, *, app_bytes: bytes | None = None,
                 collection_enabled: bool | None = None, start: int | None = None,
                 end: int | None = None, enum_started: int | None = None,
                 enum_returned: int | None = None, pages: int = 0,
                 records_returned: int = 0, events: tuple[WfpDropEvent, ...] = (),
                 exhausted: bool = False, truncated: bool = False,
                 reason_codes: tuple[str, ...] = (),
                 statuses: tuple[SdkCallStatus, ...] = (),
                 sdk_metadata: tuple[tuple[str, str], ...] = ()) -> WfpNetEventObservation:
    if truncated:
        diagnostic = "TRUNCATED_UNVERIFIED"
    elif not events:
        diagnostic = "NOT_OBSERVED"
    else:
        diagnostic = "EVENTS_OBSERVED_UNVERIFIED"
    return WfpNetEventObservation(
        state="UNVERIFIED", diagnostic_state=diagnostic, image_path=image_path,
        app_id_bytes_hex=None if app_bytes is None else app_bytes.hex(),
        app_id_sha256=None if app_bytes is None else hashlib.sha256(app_bytes).hexdigest(),
        collection_enabled=collection_enabled,
        window_start_filetime_100ns=start, window_end_filetime_100ns=end,
        enum_create_started_filetime_100ns=enum_started,
        enum_create_returned_filetime_100ns=enum_returned,
        pages=pages, records_returned=records_returned, events=events,
        exhausted=exhausted, truncated=truncated, reason_codes=reason_codes,
        sdk_statuses=statuses, sdk_metadata=sdk_metadata,
    )


def observe_recent_classify_drop_events(
    image_path: str, *, window_seconds: int = 5, _api: Any = None,
) -> WfpNetEventObservation:
    """Read a bounded AppID/TCP/IPv4-loopback event snapshot.

    ``_api`` exists for software-only tests. No returned observation is proof,
    even when every event field matches a caller's expected values.
    """
    path = _canonical_image_path(image_path)
    if type(window_seconds) is not int or not 1 <= window_seconds <= MAX_WINDOW_SECONDS:
        raise ValueError(f"window_seconds must be an integer in 1..{MAX_WINDOW_SECONDS}")
    if _api is None:
        if os.name != "nt":
            return _observation(path, reason_codes=("WINDOWS_WFP_SDK_UNAVAILABLE",))
        try:
            _api = CtypesWindowsNetEventApi()
        except (OSError, AttributeError, TypeError) as exc:
            return _observation(path, reason_codes=("WINDOWS_WFP_SDK_UNAVAILABLE:" + type(exc).__name__,))

    global _active_owner
    with _owner_lock:
        if _active_owner is not None:
            raise WfpEventOwnershipError(
                "previous WFP event read retains its original owner; explicit recovery is required",
                _active_owner,
            )
        owner = _WfpEventOwner(_api, seal=_OWNER_SEAL)
        _active_owner = owner

    scope = owner.registry.scope()
    statuses: list[SdkCallStatus] = []
    reasons: list[str] = []
    events: list[WfpDropEvent] = []
    app_bytes: bytes | None = None
    collection_enabled: bool | None = None
    start: int | None = None
    end: int | None = None
    enum_started: int | None = None
    enum_returned: int | None = None
    pages = 0
    records_returned = 0
    exhausted = False
    truncated = False
    engine_entry = None
    enum_entry = None
    operation_failure: BaseException | None = None
    software_api = not isinstance(_api, CtypesWindowsNetEventApi)
    if software_api:
        reasons.append("SOFTWARE_FIXTURE_API_ONLY")

    try:
        app_bytes = _capture_app_id(owner, path, statuses)

        engine_slot = C.c_void_p()
        engine_status, engine_entry = owner.invoke_output(
            "FwpmEngineOpen0", (None, 10, None, None, C.byref(engine_slot)),
            (engine_slot,), "engine",
        )
        statuses.append(engine_status)
        if engine_status.status != 0:
            raise _SdkFailure(engine_status)
        if engine_entry is None:
            raise ValueError("FwpmEngineOpen0 succeeded without an engine HANDLE")

        collection_enabled = _read_collection_option(owner, engine_entry, statuses)
        if not collection_enabled:
            reasons.append("WFP_COLLECTION_DISABLED")
        else:
            end = _filetime_value(_api.system_time_filetime())
            start = end - window_seconds * _FILETIME_TICKS_PER_SECOND
            template_parts = _build_enum_template(start, end, app_bytes)
            template, conditions, local_mask, remote_mask, app_storage, app_blob = template_parts
            enum_slot = C.c_void_p()
            enum_started = _filetime_value(_api.system_time_filetime())
            template.startTime = _filetime_from_value(start)
            template.endTime = _filetime_from_value(end)
            enum_status, enum_entry = owner.invoke_output(
                "FwpmNetEventCreateEnumHandle0",
                (engine_entry.handle, C.byref(template), C.byref(enum_slot)),
                (enum_slot,), "enum", parent=engine_entry,
                keepalive=(template, conditions, local_mask, remote_mask, app_storage, app_blob),
            )
            statuses.append(enum_status)
            enum_returned = _filetime_value(_api.system_time_filetime())
            if enum_status.status != 0:
                if enum_status.status == FWP_E_NET_EVENTS_DISABLED:
                    reasons.append("WFP_NET_EVENTS_DISABLED")
                else:
                    raise _SdkFailure(enum_status)
            elif enum_entry is None:
                raise ValueError("FwpmNetEventCreateEnumHandle0 succeeded without an enum HANDLE")
            else:
                while pages < MAX_PAGES and records_returned < MAX_EVENTS_READ:
                    request_count = min(PAGE_SIZE, MAX_EVENTS_READ - records_returned)
                    array_slot = C.POINTER(C.POINTER(FWPM_NET_EVENT2))()
                    returned_count = C.c_uint32(0)
                    status, array_entry = owner.invoke_output(
                        "FwpmNetEventEnum2",
                        (engine_entry.handle, enum_entry.handle, request_count,
                         C.byref(array_slot), C.byref(returned_count)),
                        (array_slot, returned_count), "memory", parent=enum_entry,
                    )
                    statuses.append(status)
                    pages += 1
                    count = int(returned_count.value)
                    records_returned += count
                    if count > request_count:
                        reasons.append("ENUM_RETURN_COUNT_EXCEEDS_REQUEST")
                        if array_entry is not None:
                            _release_entry(owner, array_entry)
                        raise ValueError("FwpmNetEventEnum2 returned more entries than requested")
                    if count and array_entry is None:
                        raise ValueError("FwpmNetEventEnum2 returned a positive count without an array")
                    if status.status == FWP_E_NET_EVENTS_DISABLED:
                        reasons.append("WFP_NET_EVENTS_DISABLED")
                        if array_entry is not None:
                            _release_entry(owner, array_entry)
                        break
                    if status.status != 0:
                        if array_entry is not None:
                            _release_entry(owner, array_entry)
                        raise _SdkFailure(status)
                    remaining_visible = max(0, MAX_EVENTS - len(events))
                    for index in range(min(count, remaining_visible)):
                        try:
                            events.append(_decode_event(
                                _api, array_slot[index], start, end,
                            ))
                        except (ValueError, TypeError, OSError) as exc:
                            reasons.append("EVENT_DECODE_UNVERIFIED:" + type(exc).__name__)
                    if array_entry is not None:
                        _release_entry(owner, array_entry)
                    if count < request_count:
                        exhausted = True
                        break
                    if records_returned >= MAX_EVENTS_READ:
                        truncated = True
                        reasons.append("EVENT_COUNT_BOUND_REACHED")
                        break
                if not exhausted and not truncated and pages >= MAX_PAGES:
                    truncated = True
                    reasons.append("EVENT_PAGE_BOUND_REACHED")
    except _SdkFailure as exc:
        statuses.append(exc.call_status) if not statuses or statuses[-1] is not exc.call_status else None
        operation_failure = exc
        if exc.call_status.status == FWP_E_NET_EVENTS_DISABLED:
            reasons.append("WFP_NET_EVENTS_DISABLED")
        else:
            reasons.append("WFP_SDK_CALL_FAILED")
    except (NativeResourceError, WfpEventOwnershipError) as exc:
        operation_failure = exc
        reasons.append("NATIVE_RESOURCE_OWNERSHIP_UNKNOWN")
    except Exception as exc:
        operation_failure = exc
        reasons.append("WFP_OBSERVATION_UNVERIFIED:" + type(exc).__name__)
    finally:
        owner.operation_error = operation_failure
        try:
            owner.release_scope(scope)
        except BaseException as cleanup_error:
            owner.state = "UNKNOWN"
            owner.operation_error = cleanup_error
            raise WfpEventOwnershipError(
                "WFP event observation retains its original handles or allocation after release failure",
                owner,
            ) from cleanup_error
        with _owner_lock:
            if _active_owner is owner:
                _active_owner = None

    if operation_failure is not None and "WFP_COLLECTION_DISABLED" not in reasons:
        # The exact failure remains represented by the original SDK status or
        # the explicit reason above. The observer never converts it to a pass.
        pass
    raw_metadata = getattr(_api, "sdk_metadata", ())
    if isinstance(raw_metadata, dict):
        metadata = tuple(sorted((str(k), str(v)) for k, v in raw_metadata.items()))
    else:
        metadata = tuple(sorted((str(k), str(v)) for k, v in raw_metadata))
    if not events and collection_enabled is True and not truncated and not reasons:
        reasons.append("NO_MATCHING_EVENTS_OBSERVED")
    return _observation(
        path, app_bytes=app_bytes, collection_enabled=collection_enabled,
        start=start, end=end, enum_started=enum_started, enum_returned=enum_returned,
        pages=pages, records_returned=records_returned, events=tuple(events),
        exhausted=exhausted, truncated=truncated, reason_codes=tuple(reasons),
        statuses=tuple(statuses), sdk_metadata=metadata,
    )


def diagnose_event_correlation(
    observation: WfpNetEventObservation,
    expected: EventCorrelationExpectation,
) -> EventCorrelationDiagnostic:
    """Pure comparison for diagnostics; its result can never qualify a block."""
    if type(observation) is not WfpNetEventObservation:
        raise TypeError("WfpNetEventObservation required; JSON is not observation provenance")
    if type(expected) is not EventCorrelationExpectation:
        raise TypeError("EventCorrelationExpectation required for pure diagnostics")
    if observation.truncated:
        return EventCorrelationDiagnostic("TRUNCATED_UNVERIFIED", (), ("EVENT_SNAPSHOT_TRUNCATED",))
    if observation.collection_enabled is not True or observation.window_start_filetime_100ns is None or observation.window_end_filetime_100ns is None:
        return EventCorrelationDiagnostic("NOT_OBSERVED", (), ("COLLECTION_OR_TIME_WINDOW_UNAVAILABLE",))
    if not observation.events:
        return EventCorrelationDiagnostic("NOT_OBSERVED", (), ("NO_EVENTS_OBSERVED",))
    if not observation.exhausted:
        return EventCorrelationDiagnostic("UNVERIFIED", (), ("EVENT_SNAPSHOT_NOT_EXHAUSTED",))
    if observation.pages < 1 or observation.records_returned != len(observation.events):
        return EventCorrelationDiagnostic("UNVERIFIED", (), ("EVENT_ARRAY_DECODE_INCOMPLETE",))
    observation_errors = tuple(
        reason for reason in observation.reason_codes
        if reason != "SOFTWARE_FIXTURE_API_ONLY"
    )
    if observation_errors:
        return EventCorrelationDiagnostic(
            "UNVERIFIED", (), ("OBSERVATION_HAS_ERROR_REASONS", *observation_errors)
        )
    sdk_failures = tuple(
        status for status in observation.sdk_statuses
        if status.status != 0
    )
    if sdk_failures:
        return EventCorrelationDiagnostic(
            "UNVERIFIED", (), tuple(
                "SDK_STATUS_FAILURE:" + status.function + ":" + str(status.status_hex)
                for status in sdk_failures
            ),
        )
    reasons: list[str] = ["ORIGINAL_FILTER_SEMANTIC_BINDING_NOT_SUPPLIED"]
    if type(expected.local_address) is not str or type(expected.remote_address) is not str:
        return EventCorrelationDiagnostic("NO_MATCH", (), ("EXPECTED_IPV4_TUPLE_INVALID", *reasons))
    try:
        local = str(ipaddress.IPv4Address(expected.local_address))
        remote = str(ipaddress.IPv4Address(expected.remote_address))
    except (ipaddress.AddressValueError, TypeError):
        return EventCorrelationDiagnostic("NO_MATCH", (), ("EXPECTED_IPV4_TUPLE_INVALID", *reasons))
    if (type(expected.filter_id) is not int or expected.filter_id <= 0
            or type(expected.layer_id) is not int or not 0 <= expected.layer_id <= 0xFFFF
            or type(expected.ip_version) is not int or expected.ip_version != FWP_IP_VERSION_V4
            or type(expected.protocol) is not int or expected.protocol != 6
            or type(expected.local_port) is not int or not 1 <= expected.local_port <= 65535
            or type(expected.remote_port) is not int or not 1 <= expected.remote_port <= 65535
            or type(expected.direction) is not str or expected.direction not in {"IN", "OUT", "FORWARD"}
            or type(expected.app_id_bytes) is not bytes or not 1 <= len(expected.app_id_bytes) <= MAX_APP_ID_BYTES
            or type(expected.package_sid) is not str
            or type(expected.start_filetime_100ns) is not int
            or type(expected.end_filetime_100ns) is not int
            or not 8 <= expected.start_filetime_100ns <= expected.end_filetime_100ns
            or expected.end_filetime_100ns - expected.start_filetime_100ns > MAX_WINDOW_SECONDS * _FILETIME_TICKS_PER_SECOND):
        return EventCorrelationDiagnostic("NO_MATCH", (), ("EXPECTED_FIELDS_OUT_OF_BOUNDS", *reasons))
    if (expected.start_filetime_100ns < observation.window_start_filetime_100ns
            or expected.end_filetime_100ns > observation.window_end_filetime_100ns):
        return EventCorrelationDiagnostic("NO_MATCH", (), ("EXPECTED_TIME_WINDOW_OUTSIDE_OBSERVATION", *reasons))

    matches: list[int] = []
    mismatch_reasons: set[str] = set()
    expected_app_hex = expected.app_id_bytes.hex()
    if observation.app_id_bytes_hex != expected_app_hex:
        return EventCorrelationDiagnostic("NO_MATCH", (), ("OBSERVER_APP_ID_MISMATCH", *reasons))
    for index, event in enumerate(observation.events):
        checks = (
            (event.event_type == FWPM_NET_EVENT_TYPE_CLASSIFY_DROP, "EVENT_TYPE_MISMATCH"),
            (not event.event_reasons, "EVENT_FIELDS_INCOMPLETE_OR_UNKNOWN"),
            (event.filter_id == expected.filter_id, "FILTER_ID_MISMATCH"),
            (event.layer_id == expected.layer_id, "LAYER_ID_MISMATCH"),
            (event.ip_version == expected.ip_version and event.ip_protocol == expected.protocol, "IP_VERSION_OR_PROTOCOL_MISMATCH"),
            (event.local_address == local and event.remote_address == remote, "ADDRESS_OR_BYTE_ORDER_MISMATCH"),
            (event.local_port == expected.local_port and event.remote_port == expected.remote_port, "PORT_MISMATCH"),
            (event.timestamp_filetime_100ns >= expected.start_filetime_100ns
             and event.timestamp_filetime_100ns <= expected.end_filetime_100ns, "EVENT_TIME_MISMATCH"),
            (event.app_id_bytes_hex == expected_app_hex, "APP_ID_MISMATCH"),
            (event.package_sid == expected.package_sid, "PACKAGE_SID_MISMATCH"),
            (event.direction == expected.direction, "DIRECTION_MISMATCH"),
            (event.is_loopback is True, "NOT_LOOPBACK"),
        )
        failed = [reason for passed, reason in checks if not passed]
        if not failed:
            matches.append(index)
        else:
            mismatch_reasons.update(failed)
    if len(matches) == 1:
        return EventCorrelationDiagnostic("MATCHED_DIAGNOSTIC_ONLY", tuple(matches), tuple(reasons))
    if len(matches) > 1:
        return EventCorrelationDiagnostic("AMBIGUOUS", tuple(matches), ("REPEATED_OR_AMBIGUOUS_MATCHES", *reasons))
    reasons.extend(sorted(mismatch_reasons))
    return EventCorrelationDiagnostic("NO_MATCH", (), tuple(dict.fromkeys(reasons)))


def retained_wfp_event_owner_for_recovery() -> Any:
    """Return the exact private owner only when a native release is uncertain."""
    with _owner_lock:
        if _active_owner is None or _active_owner.state != "UNKNOWN":
            return None
        return _active_owner


def recover_wfp_event_owner(owner: Any) -> None:
    """Explicitly retry releases through the same original API owner and handles."""
    if type(owner) is not _WfpEventOwner:
        raise NativeResourceError(
            "NATIVE_RESOURCE_RELEASE_UNKNOWN", "recovery requires the retained original WFP owner",
            safe_retry=False, stage="pre_dispatch",
        )
    owner.recover_owned(owner)
