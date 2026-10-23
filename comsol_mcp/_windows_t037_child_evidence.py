"""Bounded Windows x64 SDK observations of borrowed, originally retained handles.

This module has NO admission, resume, launch, policy setter or issuer interface.
Frozen result objects and caller comparisons are data, never proof authority.
The caller retains the original process/thread entries in NativeOwnedRegistry;
this observer only releases resources it acquires itself. It cannot discover or
adopt a process by PID. A successful query does not prove CREATE_SUSPENDED.

WMI uses local documented COM interfaces, not a PEB/NtQuery fallback. Connect
has WBEM_FLAG_CONNECT_USE_MAX_WAIT (120 s); Next has a 1500 ms bound, two rows
maximum. The local COM transport may still fail. Empty/unavailable properties,
loader state and SDK errors remain UNVERIFIED. No process-wide COM security or
privilege changes are made. Mapping/file correlation is a *backing file* query,
not an assertion about all loaded memory, DLLs or filesystem isolation.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
import threading
import time
from uuid import UUID

from ._windows_owned_resources import (
    NativeResourceError, _OwnedResource, attach_registry, registry_for,
)

U32 = C.c_uint32
I32 = C.c_int32
U64 = C.c_uint64
PTR = C.c_void_p
MAX_BUFFER = 1024 * 1024
MAX_UTF16 = 32768


@dataclass(frozen=True)
class Issue:
    component: str
    api: str
    code: str
    native_code: int | None = None
    detail: str = ""


class ObservationError(RuntimeError):
    def __init__(self, api, code, native_code=None, detail=""):
        self.issue = Issue("", api, code, native_code, detail)
        super().__init__(f"{api}: {code} ({native_code}): {detail}")


def _bad(api, code="INVALID_SDK_BUFFER", detail=""):
    raise ObservationError(api, code, detail=detail)


def decode_utf16(raw: bytes, units: int, *, terminated=False) -> str:
    """Counted UTF16, rejecting empty/truncated/embedded NUL/unpaired surrogate."""
    if (type(units) is not int or not 0 < units < MAX_UTF16
            or units * 2 + (2 if terminated else 0) > len(raw)):
        _bad("UTF16", detail="empty, out of bounds or truncated counted string")
    if terminated and raw[units * 2:units * 2 + 2] != b"\0\0":
        _bad("UTF16", detail="missing terminator")
    try:
        value = raw[:units * 2].decode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        _bad("UTF16", detail=str(exc))
    if "\0" in value:
        _bad("UTF16", detail="embedded NUL")
    return value


@dataclass(frozen=True)
class SdkBuffer:
    """ReturnLength copy. Pointer values are offsets against base, NEVER dereferenced."""
    raw: bytes
    base: int

    def at(self, offset, size):
        if (type(offset) is not int or type(size) is not int or offset < 0
                or size < 0 or offset + size > len(self.raw)
                or len(self.raw) > MAX_BUFFER):
            _bad("GetTokenInformation", detail="field outside ReturnLength")
        return self.raw[offset:offset + size]

    def uint(self, offset, size=4):
        return int.from_bytes(self.at(offset, size), "little")

    def sid(self, pointer):
        offset = pointer - self.base
        header = self.at(offset, 8)
        count = header[1]
        if header[0] != 1 or count > 15:
            _bad("SID", detail="invalid revision/subauthority count")
        body = self.at(offset + 8, count * 4)
        parts = [str(int.from_bytes(body[n:n + 4], "little"))
                 for n in range(0, len(body), 4)]
        authority = int.from_bytes(header[2:8], "big")
        return "S-1-" + str(authority) + ("-" + "-".join(parts) if parts else "")


@dataclass(frozen=True)
class SidAttributes:
    sid: str
    attributes: int


@dataclass(frozen=True)
class Privilege:
    luid: int
    attributes: int
    name: str | None

    @property
    def enabled(self):
        return bool(self.attributes & 2)  # SE_PRIVILEGE_ENABLED, preserve other bits.


def parse_sid_attributes(buf, offset=0):
    buf.at(offset, 16)  # x64 SID_AND_ATTRIBUTES including alignment padding.
    return SidAttributes(buf.sid(buf.uint(offset, 8)), buf.uint(offset + 8))


def parse_groups(buf):
    count = buf.uint(0)
    if count > 4096:
        _bad("TOKEN_GROUPS", detail="group budget exceeded")
    if count:
        buf.at(8, count * 16)
    return tuple(parse_sid_attributes(buf, 8 + i * 16) for i in range(count))


def parse_privileges(buf):
    count = buf.uint(0)
    if count > 4096:
        _bad("TOKEN_PRIVILEGES", detail="privilege budget exceeded")
    buf.at(4, count * 12)
    return tuple(Privilege(buf.uint(4 + i * 12, 8), buf.uint(12 + i * 12), None)
                 for i in range(count))


@dataclass(frozen=True)
class TokenObservation:
    user: SidAttributes
    restricted_sids: tuple[SidAttributes, ...]
    capabilities: tuple[SidAttributes, ...]
    integrity: SidAttributes
    integrity_rid: int
    mandatory_policy: int
    privileges: tuple[Privilege, ...]
    token_id: int
    authentication_id: int
    modified_id: int
    is_appcontainer: bool
    groups: tuple[SidAttributes, ...] | None = None  # Historical constructions did not observe TokenGroups.


@dataclass(frozen=True)
class IdentityObservation:
    pid: int
    thread_id: int
    thread_process_id: int
    birth_filetime: int
    thread_birth_filetime: int
    process_live: bool
    thread_live: bool


@dataclass(frozen=True)
class ChildPolicyObservation:
    flags: int
    no_child_process_creation: bool
    audit_no_child_process_creation: bool
    allow_secure_process_creation: bool


@dataclass(frozen=True)
class CommandLineObservation:
    pid: int
    command_line: str
    creation_date: str
    birth_filetime_microsecond_precision: int
    executable_path: str | None
    source: str = "local COM Win32_Process read-only query"


@dataclass(frozen=True)
class FileObservation:
    native_path: str
    volume_serial: int
    file_id: int
    size: int
    links: int
    attributes: int
    last_write_filetime: int
    sha256: str
    creation_filetime: int = 0
    access_before_filetime: int = 0
    access_after_filetime: int = 0

    @property
    def stability_key(self):
        # Reads can change LastAccessTime. Keep both access observations, but
        # compare actual identity/content and the other metadata independently.
        return (self.native_path.casefold(), self.volume_serial, self.file_id,
                self.size, self.links, self.attributes, self.last_write_filetime,
                self.creation_filetime, self.sha256)


@dataclass(frozen=True)
class ImageObservation:
    win32_image_path: str
    native_image_path: str
    mapped_path: str
    allocation_base: int
    region_base: int
    region_size: int
    memory_state: int
    memory_type: int
    pinned_before: FileObservation
    pinned_after: FileObservation
    mapped_backing_file: FileObservation
    pinned_stable: bool
    backing_file_matches_pin: bool
    scope: str = "MEM_IMAGE name + retained backing file ID/hash, NOT loaded-memory hash"


@dataclass(frozen=True)
class Comparison:
    name: str
    matches: bool | None


@dataclass(frozen=True)
class ChildObservations:
    identity_before: IdentityObservation | None
    identity_after: IdentityObservation | None
    token_before: TokenObservation | None
    token_after: TokenObservation | None
    child_policy: ChildPolicyObservation | None
    command_line: CommandLineObservation | None
    image: ImageObservation | None
    comparisons: tuple[Comparison, ...]
    issues: tuple[Issue, ...]
    source: str
    state: str = field(default="READ_ONLY_OBSERVATIONS_NOT_ADMISSION", init=False)


@dataclass(frozen=True)
class BorrowedChildHandles:
    """Only a borrowing description. This class confers no issuer authority."""
    owner_api: object
    process_entry: object
    thread_entry: object
    expected_pid: int
    expected_thread_id: int
    expected_birth_filetime: int


@dataclass(frozen=True)
class PinnedImage:
    entry: object
    api: object
    original_adapter: object = None


def borrow_original_child(process, *, api):
    """Borrow original live launcher acquisitions for readonly SDK queries.

    No HANDLE is opened, adopted, relabeled or released. The query adapter
    joins the original registry; its own acquisitions retain its own closers.
    These borrowing descriptions remain observations, never Resume authority.
    """
    from ._windows_t037 import _RetainedSuspendedChild
    if type(process) is not _RetainedSuspendedChild or type(api) is not WindowsChildEvidenceApi:
        _bad("original child borrow", "INVALID_BORROW")
    process._require_observable()
    if not process.never_resumed or process._owner is not process._api:
        _bad("original child borrow", "OWNER_NOT_LIVE")
    if type(process.birth_filetime_100ns) is not int or process.birth_filetime_100ns <= 0:
        _bad("original child borrow", "BIRTH_UNVERIFIED")
    p, t = process._process_entry, process._thread_entry
    _entry(p, process._api, process._api)
    _entry(t, process._api, process._api)
    _entry(process._image_entry, process._api, process._image_api)
    attach_registry(api, registry_for(process._api))
    return (BorrowedChildHandles(process._api, p, t, process.pid,
                process.thread_id, process.birth_filetime_100ns),
            PinnedImage(process._image_entry, api, process._image_api))


def _real_handle(value):
    value = getattr(value, "value", value)
    # Reject NULL, INVALID_HANDLE_VALUE and the documented pseudo-handle range.
    if type(value) is not int or not 0 < value < (1 << (8 * C.sizeof(PTR))) - 16:
        _bad("HANDLE", "NOT_REAL_OWNED_HANDLE")
    return value


def _entry(entry, owner_api, adapter):
    registry = registry_for(owner_api)
    if (type(entry) is not _OwnedResource or entry.registry is not registry
            or entry.adapter is not adapter or entry.domain != "native"
            or entry.state != "ACQUIRED" or entry.thread_id != threading.get_ident()
            or registry.find(adapter, entry.handle) is not entry):
        _bad("NativeOwnedRegistry", "OWNER_NOT_LIVE")
    if _real_handle(entry.handle) != entry.value:
        _bad("NativeOwnedRegistry", "HANDLE_BINDING_CHANGED")
    return entry.handle


def _borrow(binding):
    if type(binding) is not BorrowedChildHandles:
        _bad("BorrowedChildHandles", "INVALID_BORROW")
    p = _entry(binding.process_entry, binding.owner_api, binding.owner_api)
    t = _entry(binding.thread_entry, binding.owner_api, binding.owner_api)
    if p == t:
        _bad("BorrowedChildHandles", "HANDLE_ALIAS")
    return p, t


def command_birth(value):
    """DMTF Win32_Process CreationDate -> FILETIME, no wildcard/timezone guessing."""
    if (not isinstance(value, str) or len(value) != 25 or value[14] != "."
            or value[21] not in "+-" or not (value[:14] + value[15:21] + value[22:]).isdigit()):
        _bad("Win32_Process.CreationDate", detail="missing or non-concrete DMTF timestamp")
    try:
        dt = datetime.strptime(value[:21], "%Y%m%d%H%M%S.%f")
        minutes = int(value[22:]) * (1 if value[21] == "+" else -1)
        dt = dt.replace(tzinfo=timezone(timedelta(minutes=minutes))).astimezone(timezone.utc)
        delta = dt - datetime(1601, 1, 1, tzinfo=timezone.utc)
        return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 10
    except (ValueError, OverflowError) as exc:
        _bad("Win32_Process.CreationDate", detail=str(exc))


def compare_observations(binding, before, after, token_before, token_after,
                         command, image, *, expected_command_line=None,
                         expected_modified_id=None):
    def cmp(name, available, fact):
        return Comparison(name, fact() if available else None)
    return (
        cmp("created_pid", before, lambda: before.pid == binding.expected_pid),
        cmp("created_birth", before, lambda: before.birth_filetime == binding.expected_birth_filetime),
        cmp("created_thread", before, lambda: before.thread_id == binding.expected_thread_id),
        cmp("thread_process_association", before, lambda: before.thread_process_id == before.pid),
        cmp("live_before", before, lambda: before.process_live and before.thread_live),
        cmp("identity_stable_and_live", before and after, lambda: before == after and after.process_live and after.thread_live),
        cmp("token_stable", token_before and token_after, lambda: token_before == token_after),
        cmp("expected_modified_id", token_before and expected_modified_id is not None,
            lambda: token_before.modified_id == expected_modified_id),
        cmp("wmi_pid_birth", command and before,
            lambda: command.pid == before.pid and 0 <= before.birth_filetime - command.birth_filetime_microsecond_precision < 10),
        cmp("remote_command_line", command and expected_command_line is not None,
            lambda: command.command_line == expected_command_line),
        cmp("mapped_main_backing_matches_pin", image,
            lambda: image.pinned_stable and image.backing_file_matches_pin),
    )


def observe_child(binding, pin, *, api=None, expected_command_line=None,
                  expected_modified_id=None):
    """Read original handles; return partial facts/errors, never permission to run.

    Borrowed entries must remain acquired on this thread. Caller expectations
    only produce comparisons. api injection is for boundary tests and is labeled.
    Original child and pinned-file entries are never released here.
    """
    p, t = _borrow(binding)
    if api is None and type(pin) is not PinnedImage:
        _bad("PinnedImage", "INVALID_BORROW")
    selected = api if api is not None else pin.api
    if registry_for(selected) is not registry_for(binding.owner_api):
        _bad("NativeOwnedRegistry", "DIFFERENT_OWNER")
    issues = []

    def read(component, fn):
        try:
            _borrow(binding)  # stale/released original owner cannot yield later facts.
            if any(entry.state == "UNKNOWN" for entry in registry_for(selected).unresolved()):
                _bad("NativeOwnedRegistry", "RESOURCE_RELEASE_UNKNOWN", "no new work/release retry after an unresolved release")
            return fn()
        except (ObservationError, NativeResourceError, OSError) as exc:
            detail = getattr(exc, "issue", None)
            context = f"; original={exc.__context__}" if exc.__context__ else ""
            issues.append(Issue(component, detail.api if detail else type(exc).__name__,
                                detail.code if detail else "UNVERIFIED",
                                detail.native_code if detail else getattr(exc, "winerror", None), str(exc) + context))
            return None

    before = read("identity_before", lambda: selected.identity(p, t))
    if before is not None and before.process_live and before.thread_live:
        token = read("token_before", lambda: selected.token(p))
        policy = read("child_policy", lambda: selected.child_policy(p))
        command = read("command_line", lambda: selected.command_line(before.pid))
        image = read("main_image", lambda: selected.image(p, pin))
        token_after = read("token_after", lambda: selected.token(p))
    else:
        token = policy = command = image = token_after = None
        issues.append(Issue("child", "HANDLE", "UNVERIFIED", detail="identity unavailable or child/thread exited"))
    after = read("identity_after", lambda: selected.identity(p, t))
    comparisons = compare_observations(binding, before, after, token, token_after, command, image,
                                      expected_command_line=expected_command_line,
                                      expected_modified_id=expected_modified_id)
    return ChildObservations(before, after, token, token_after, policy, command, image,
                             comparisons, tuple(issues), selected.source if type(selected) is WindowsChildEvidenceApi
                             else "SOFTWARE_BOUNDARY_TEST_NOT_NATIVE")


class _FileInfo(C.Structure):
    _pack_ = 4  # FILETIME is two DWORDs, NOT an aligned ULONGLONG.
    _fields_ = [("attributes", U32), ("creation", U64), ("access", U64), ("write", U64),
                ("volume", U32), ("high", U32), ("low", U32), ("links", U32),
                ("id_high", U32), ("id_low", U32)]


class _MemoryInfo(C.Structure):
    _fields_ = [("base", PTR), ("allocation_base", PTR), ("allocation_protect", U32),
                ("partition_id", C.c_uint16), ("size", C.c_size_t), ("state", U32),
                ("protect", U32), ("type", U32)]


class _Variant(C.Structure):
    # VARIANT x64 has a 16-byte union (BRECORD), starting at offset 8.
    _fields_ = [("vt", C.c_uint16), ("reserved", C.c_uint16 * 3), ("data", U64 * 2)]


class WindowsChildEvidenceApi:
    """Windows x64 SDK boundary. Only query/read/open/allocator operations."""
    source = "WINDOWS_X64_SDK_READ_ONLY"

    def __init__(self, owner_api=None):
        if os.name != "nt" or C.sizeof(PTR) != 8 or C.sizeof(C.c_wchar) != 2:
            _bad("ABI", "UNSUPPORTED", detail="requires native Windows x64 Python")
        if C.sizeof(_FileInfo) != 52 or C.sizeof(_MemoryInfo) != 48 or C.sizeof(_Variant) != 24:
            _bad("ABI", "UNSUPPORTED", detail="unexpected structure packing")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        self.ps = C.WinDLL("psapi", use_last_error=True)
        self.ole = C.WinDLL("ole32")
        self.auto = C.WinDLL("oleaut32")
        if owner_api is not None:
            attach_registry(self, registry_for(owner_api))
        else:
            registry_for(self)
        # Fixed-width SDK types; c_long is intentionally not used on POSIX tests.
        def bind(dll, name, result, *args):
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = list(args), result
        for name in ("GetProcessId", "GetThreadId", "GetProcessIdOfThread"):
            bind(self.k, name, U32, PTR)
        for name in ("GetProcessTimes", "GetThreadTimes"):
            bind(self.k, name, I32, PTR, PTR, PTR, PTR, PTR)
        bind(self.k, "WaitForSingleObject", U32, PTR, U32)
        bind(self.k, "CloseHandle", I32, PTR)
        bind(self.k, "GetProcessMitigationPolicy", I32, PTR, I32, PTR, C.c_size_t)
        bind(self.k, "QueryFullProcessImageNameW", I32, PTR, U32, PTR, PTR)
        bind(self.k, "VirtualQueryEx", C.c_size_t, PTR, PTR, PTR, C.c_size_t)
        bind(self.ps, "GetMappedFileNameW", U32, PTR, PTR, PTR, U32)
        bind(self.k, "CreateFileW", PTR, C.c_wchar_p, U32, U32, PTR, U32, U32, PTR)
        bind(self.k, "GetFileInformationByHandle", I32, PTR, PTR)
        bind(self.k, "GetFinalPathNameByHandleW", U32, PTR, PTR, U32, U32)
        bind(self.k, "GetFileType", U32, PTR)
        bind(self.k, "SetFilePointerEx", I32, PTR, C.c_int64, PTR, U32)
        bind(self.k, "ReadFile", I32, PTR, PTR, U32, PTR, PTR)
        bind(self.a, "OpenProcessToken", I32, PTR, U32, PTR)
        bind(self.a, "GetTokenInformation", I32, PTR, I32, PTR, U32, PTR)
        bind(self.a, "LookupPrivilegeNameW", I32, PTR, PTR, PTR, PTR)
        bind(self.ole, "CoInitializeEx", I32, PTR, U32)
        bind(self.ole, "CoUninitialize", None)
        bind(self.ole, "CoCreateInstance", I32, PTR, PTR, U32, PTR, PTR)
        bind(self.ole, "CoSetProxyBlanket", I32, PTR, U32, U32, PTR, U32, U32, PTR, U32)
        bind(self.auto, "SysAllocString", PTR, C.c_wchar_p)
        bind(self.auto, "SysFreeString", None, PTR)
        bind(self.auto, "SysStringLen", U32, PTR)
        bind(self.auto, "VariantClear", I32, PTR)

    def _check(self, ok, api):
        if not ok:
            raise ObservationError(api, "SDK_ERROR", C.get_last_error())
        return ok

    def _owned_handle(self, handle, label):
        _real_handle(handle)  # Do not register/close a pseudo handle.
        def close(value):
            return self._check(self.k.CloseHandle(value), "CloseHandle(" + label + ")")
        return registry_for(self).acquire(self, handle, close, mode="BOOL", label=label)

    def identity(self, process, thread):
        def number(name, handle):
            value = int(getattr(self.k, name)(handle))
            return int(self._check(value, name))
        def birth(name, handle):
            fields = [U64() for _ in range(4)]
            self._check(getattr(self.k, name)(handle, *(C.byref(x) for x in fields)), name)
            if not fields[0].value:
                _bad(name, "UNVERIFIED", detail="zero creation FILETIME")
            return fields[0].value
        def live(handle):
            result = int(self.k.WaitForSingleObject(handle, 0))
            if result not in (0, 258):
                raise ObservationError("WaitForSingleObject", "SDK_ERROR", C.get_last_error(), str(result))
            return result == 258
        return IdentityObservation(number("GetProcessId", process), number("GetThreadId", thread),
                                   number("GetProcessIdOfThread", thread), birth("GetProcessTimes", process),
                                   birth("GetThreadTimes", thread), live(process), live(thread))

    def _token_buffer(self, token, kind, *, _guard=None):
        size = U32()
        returned = U32()
        context = {"function": self.a.GetTokenInformation, "token": token, "kind": kind,
                   "size_slot": size, "returned_slot": returned, "storage": None, "raw_returns": []}
        if not hasattr(self, "_token_query_contexts"):
            self._token_query_contexts = []
        self._token_query_contexts.append(context)  # Actual slots/function retained before SDK entry.
        if _guard is not None: _guard()
        ok = context["function"](token, kind, None, 0, C.byref(size))
        context["raw_returns"].append(ok)
        if _guard is not None: _guard()
        error = C.get_last_error()
        if ok or error != 122 or not 0 < size.value <= MAX_BUFFER:
            raise ObservationError("GetTokenInformation(size)", "SDK_SIZE_ERROR", error, f"class={kind}, size={size.value}")
        storage = C.create_string_buffer(size.value)
        context["storage"] = storage
        if _guard is not None: _guard()
        ok = context["function"](token, kind, storage, size.value, C.byref(returned))
        context["raw_returns"].append(ok)
        if _guard is not None: _guard()
        self._check(ok, f"GetTokenInformation({kind})")
        if not 0 < returned.value <= size.value:
            _bad("GetTokenInformation", detail=f"class={kind}, capacity={size.value}, returned={returned.value}")
        # Pointers only parsed against this copied range; no dereference after return.
        return SdkBuffer(storage.raw[:returned.value], C.addressof(storage))

    def token(self, process):
        token = PTR()
        self._check(self.a.OpenProcessToken(process, 8, C.byref(token)), "OpenProcessToken(TOKEN_QUERY)")
        entry = self._owned_handle(token.value, "child_evidence_token")
        try:
            return self.read_owned_token(entry)
        finally:
            entry.release_owned(entry.registry.owner)

    def read_owned_token(self, entry):
        """Borrow one actual original TOKEN entry; never open/adopt/close it here."""
        from ._windows_t037_primary import OriginalBirthPreparation
        registry = registry_for(self)
        if type(entry) is not _OwnedResource or entry.registry is not registry:
            _bad("borrowed TOKEN", "INVALID_BORROW")
        binding = (entry.adapter, entry.handle, entry.value, entry.closer, entry.mode, entry.label, entry.seq)
        preparation = next((x for x in entry.keepalive if type(x) is OriginalBirthPreparation), None)
        def guard():
            if (entry.adapter, entry.handle, entry.value, entry.closer, entry.mode, entry.label, entry.seq) != binding:
                _bad("borrowed TOKEN", "HANDLE_BINDING_CHANGED")
            _entry(entry, registry.owner, binding[0])
            if any(x.state == "UNKNOWN" for x in registry.unresolved()):
                _bad("borrowed TOKEN", "RESOURCE_RELEASE_UNKNOWN")
            if preparation is not None:
                preparation._check()
        guard()
        token = entry.handle
        get = lambda kind: self._token_buffer(token, kind, _guard=guard)
        statistics_before = get(10)
        statistics_before.at(0, 56)
        user = parse_sid_attributes(get(1))
        groups = parse_groups(get(2))
        restricted = parse_groups(get(11))
        caps = parse_groups(get(30))
        integrity = parse_sid_attributes(get(25))
        if not integrity.sid.startswith("S-1-16-") or len(integrity.sid.split("-")) != 4:
            _bad("TokenIntegrityLevel", detail="not an integrity authority with one RID")
        policy = get(27).uint(0)
        appcontainer = get(29).uint(0)
        if appcontainer not in (0, 1):
            _bad("TokenIsAppContainer", detail="non-BOOL SDK result")
        privileges = []
        for item in parse_privileges(get(3)):
            luid, size = U64(item.luid), U32(MAX_UTF16)
            name = C.create_unicode_buffer(MAX_UTF16)
            lookup = self.a.LookupPrivilegeNameW
            arguments = (None, C.byref(luid), name, C.byref(size))
            context = {"entry": entry, "function": lookup, "luid_slot": luid,
                       "name_storage": name, "length_slot": size, "arguments": arguments,
                       "raw_return": None, "entered": False, "returned": False}
            if not hasattr(self, "_privilege_name_calls"):
                self._privilege_name_calls = []
            self._privilege_name_calls.append(context)
            guard()
            context["entered"] = True
            context["raw_return"] = lookup(*arguments)
            context["returned"] = True
            guard()
            self._check(context["raw_return"], "LookupPrivilegeNameW")
            text = decode_utf16(C.string_at(C.addressof(name), C.sizeof(name)), size.value, terminated=True)
            privileges.append(Privilege(item.luid, item.attributes, text))
        statistics_after = get(10)
        if statistics_before.raw != statistics_after.raw:
            raise ObservationError("TokenStatistics", "TOKEN_CHANGED_DURING_QUERY", detail=(
                f"before={statistics_before.raw.hex()}, after={statistics_after.raw.hex()}"))
        return TokenObservation(user, restricted, caps, integrity, int(integrity.sid.split("-")[-1]),
                                policy, tuple(privileges), statistics_before.uint(0, 8),
                                statistics_before.uint(8, 8), statistics_before.uint(48, 8), bool(appcontainer), groups=groups)

    def child_policy(self, process):
        flags = U32()
        self._check(self.k.GetProcessMitigationPolicy(process, 13, C.byref(flags), 4),
                    "GetProcessMitigationPolicy(ProcessChildProcessPolicy)")
        return ChildPolicyObservation(flags.value, bool(flags.value & 1), bool(flags.value & 2), bool(flags.value & 4))

    def pin_image(self, path):
        """Open a private read HANDLE denying write/delete sharing, no ACL changes."""
        handle = self.k.CreateFileW(path, 0x80000000, 1, None, 3, 0x00200000, None)
        if not handle or handle == C.c_void_p(-1).value:
            raise ObservationError("CreateFileW(image pin)", "SDK_ERROR", C.get_last_error())
        entry = self._owned_handle(handle, "child_evidence_image_pin")
        return PinnedImage(entry, self)

    def _file(self, entry, *, original_adapter=None):
        handle = _entry(entry, registry_for(self).owner,
                        self if original_adapter is None else original_adapter)
        def info():
            value = _FileInfo()
            self._check(self.k.GetFileInformationByHandle(handle, C.byref(value)), "GetFileInformationByHandle")
            return value
        before = info()
        size = (before.high << 32) | before.low
        if (before.attributes & (0x10 | 0x400) or self.k.GetFileType(handle) != 1
                or not 0 < size <= 256 * 1024 * 1024):
            _bad("image pin", "UNVERIFIED", detail="requires bounded regular non-reparse disk file")
        path = C.create_unicode_buffer(MAX_UTF16)
        count = int(self.k.GetFinalPathNameByHandleW(handle, path, MAX_UTF16, 2))  # VOLUME_NAME_NT
        self._check(count, "GetFinalPathNameByHandleW")
        native = decode_utf16(C.string_at(C.addressof(path), C.sizeof(path)), count, terminated=True)
        self._check(self.k.SetFilePointerEx(handle, 0, None, 0), "SetFilePointerEx")
        digest, total, buf = hashlib.sha256(), 0, C.create_string_buffer(65536)
        for _ in range(size // 65536 + 2):
            used = U32()
            self._check(self.k.ReadFile(handle, buf, 65536, C.byref(used), None), "ReadFile(image pin)")
            if used.value > 65536:
                _bad("ReadFile", detail="returned count exceeds buffer")
            if not used.value:
                break
            total += used.value
            if total > size:
                _bad("ReadFile", "FILE_CHANGED")
            digest.update(buf.raw[:used.value])
        after = info()
        stable_fields = tuple(name for name, _ in _FileInfo._fields_ if name != "access")
        if total != size or any(getattr(before, name) != getattr(after, name) for name in stable_fields):
            _bad("image pin", "FILE_CHANGED", f"read={total}, expected={size}, before={bytes(before).hex()}, after={bytes(after).hex()}")
        return FileObservation(native, before.volume, (before.id_high << 32) | before.id_low,
                               size, before.links, before.attributes, before.write, digest.hexdigest(),
                               before.creation, before.access, after.access)

    def image(self, process, pin):
        if type(pin) is not PinnedImage or pin.api is not self:
            _bad("PinnedImage", "OWNER_NOT_LIVE")
        def read_pin():
            if pin.original_adapter is None:
                return self._file(pin.entry)
            return self._file(pin.entry, original_adapter=pin.original_adapter)
        pinned_before = read_pin()
        def process_path(flags):
            buf, used = C.create_unicode_buffer(MAX_UTF16), U32(MAX_UTF16)
            self._check(self.k.QueryFullProcessImageNameW(process, flags, buf, C.byref(used)), "QueryFullProcessImageNameW")
            return decode_utf16(C.string_at(C.addressof(buf), C.sizeof(buf)), used.value, terminated=True)
        win32, native = process_path(0), process_path(1)
        address, deadline = 0, time.monotonic() + 2
        for _ in range(8192):
            if time.monotonic() > deadline:
                _bad("VirtualQueryEx", "SCAN_BUDGET_EXCEEDED")
            region = _MemoryInfo()
            got = self.k.VirtualQueryEx(process, address, C.byref(region), C.sizeof(region))
            self._check(got, "VirtualQueryEx")
            if got != C.sizeof(region) or not region.size or (region.base or 0) + region.size <= address:
                _bad("VirtualQueryEx", detail="truncated/non-progressing region")
            if region.type == 0x1000000 and region.state == 0x1000:  # MEM_IMAGE / MEM_COMMIT
                buf = C.create_unicode_buffer(MAX_UTF16)
                count = int(self.ps.GetMappedFileNameW(process, region.base, buf, MAX_UTF16))
                self._check(count, "GetMappedFileNameW")
                mapped = decode_utf16(C.string_at(C.addressof(buf), C.sizeof(buf)), count, terminated=True)
                if mapped.casefold() == native.casefold():
                    backing_pin = self.pin_image("\\\\?\\GLOBALROOT" + mapped)
                    try:
                        backing = self._file(backing_pin.entry)
                        pinned_after = read_pin()
                        same = (pinned_before.volume_serial, pinned_before.file_id, pinned_before.sha256) == (
                            backing.volume_serial, backing.file_id, backing.sha256)
                        return ImageObservation(win32, native, mapped, int(region.allocation_base or 0),
                                                int(region.base or 0), region.size, region.state, region.type,
                                                pinned_before, pinned_after, backing,
                                                pinned_before.stability_key == pinned_after.stability_key, same)
                    finally:
                        backing_pin.entry.release_owned(backing_pin.entry.registry.owner)
            address = (region.base or 0) + region.size
        _bad("VirtualQueryEx", "SCAN_BUDGET_EXCEEDED", "main image not correlated within region budget")

    def _method(self, pointer, index, result, *args):
        vtable = C.cast(pointer, C.POINTER(C.POINTER(PTR))).contents
        return C.WINFUNCTYPE(result, PTR, *args)(vtable[index])

    def _com_owned(self, pointer, label):
        if not pointer.value:
            return None
        # COM Release returns a reference count, not a BOOL success result.
        release = self._method(pointer, 2, U32)
        def closer(value):
            release(value)
        return registry_for(self).acquire(self, pointer.value, closer, mode="VOID", label=label,
                                           domain=object(), keepalive=(pointer, release))

    def _hr(self, value, api):
        if value < 0:
            raise ObservationError(api, "HRESULT_ERROR", value & 0xffffffff)

    def command_line(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            _bad("Win32_Process", "INVALID_PID")
        hr = self.ole.CoInitializeEx(None, 0)  # Local thread MTA; balance S_OK and S_FALSE.
        self._hr(hr, "CoInitializeEx")
        acquired = []
        primary_error = None

        def capture(pointer, label):
            entry = self._com_owned(pointer, label)
            if entry is not None:
                acquired.append(entry)
            return pointer

        def bstr(text):
            pointer = self.auto.SysAllocString(text)
            if not pointer:
                _bad("SysAllocString", "ALLOCATION_FAILED")
            entry = registry_for(self).acquire(self, pointer, self.auto.SysFreeString,
                                               mode="VOID", label="wmi_bstr", domain="bstr")
            acquired.append(entry)
            return pointer

        try:
            clsid = C.create_string_buffer(UUID("4590f811-1d3a-11d0-891f-00aa004b2e24").bytes_le)
            iid = C.create_string_buffer(UUID("dc12a687-737f-11cf-884d-00aa004b2e24").bytes_le)
            locator = PTR()
            hr = self.ole.CoCreateInstance(clsid, None, 1, iid, C.byref(locator))
            capture(locator, "IWbemLocator")
            self._hr(hr, "CoCreateInstance(WbemLocator)")
            if not locator.value:
                _bad("IWbemLocator", "NULL_INTERFACE")
            service = PTR()
            connect = self._method(locator, 3, I32, PTR, PTR, PTR, PTR, I32, PTR, PTR, PTR)
            hr = connect(locator, bstr("ROOT\\CIMV2"), None, None, None, 0x80, None, None, C.byref(service))
            capture(service, "IWbemServices")
            self._hr(hr, "IWbemLocator.ConnectServer(max_wait=120s)")
            if not service.value:
                _bad("IWbemServices", "NULL_INTERFACE")
            self._hr(self.ole.CoSetProxyBlanket(service, 10, 0, None, 3, 3, None, 0), "CoSetProxyBlanket(local WMI)")
            enum = PTR()
            query = f"SELECT ProcessId,CommandLine,CreationDate,ExecutablePath FROM Win32_Process WHERE ProcessId = {pid}"
            execute = self._method(service, 20, I32, PTR, PTR, I32, PTR, PTR)
            hr = execute(service, bstr("WQL"), bstr(query), 0x30, None, C.byref(enum))
            capture(enum, "IEnumWbemClassObject")
            self._hr(hr, "IWbemServices.ExecQuery")
            if not enum.value:
                _bad("IEnumWbemClassObject", "NULL_INTERFACE")
            next_row = self._method(enum, 4, I32, I32, U32, PTR, PTR)
            objects, count = (PTR * 2)(), U32()
            hr = next_row(enum, 1500, 2, objects, C.byref(count))
            for pointer in objects:
                if pointer:
                    capture(PTR(pointer), "IWbemClassObject")
            self._hr(hr, "IEnumWbemClassObject.Next")
            if hr != 1 or count.value != 1 or not objects[0] or objects[1]:
                raise ObservationError("IEnumWbemClassObject.Next", "UNVERIFIED", hr,
                                       f"expected one completed PID query, returned={count.value}")
            row = PTR(objects[0])
            get = self._method(row, 4, I32, C.c_wchar_p, I32, PTR, PTR, PTR)
            def property_value(name, optional=False):
                variant = _Variant()  # Initialized VT_EMPTY, Get failure leaves it unchanged.
                hr = get(row, name, 0, C.byref(variant), None, None)
                # Register VariantClear with the exact original storage before validation.
                entry = registry_for(self).acquire(self, C.addressof(variant), self.auto.VariantClear,
                    mode="DWORD0", label="wmi_variant", domain="variant", keepalive=(variant,))
                acquired.append(entry)
                self._hr(hr, "IWbemClassObject.Get(" + name + ")")
                if variant.vt == 1 and optional:  # VT_NULL; no expected substitution.
                    return None
                if variant.vt == 8:  # VT_BSTR, borrow only until VariantClear.
                    pointer = variant.data[0]
                    if not pointer:
                        _bad("Win32_Process." + name, "UNVERIFIED", "NULL BSTR")
                    units = int(self.auto.SysStringLen(pointer))
                    if not 0 < units < MAX_UTF16:
                        _bad("Win32_Process." + name, detail="empty/oversized BSTR")
                    return decode_utf16(C.string_at(pointer, units * 2), units)
                if name == "ProcessId" and variant.vt in (3, 19):
                    return int(variant.data[0] & 0xffffffff)
                _bad("Win32_Process." + name, "UNVERIFIED", f"VARIANT vt={variant.vt}")
            actual_pid = property_value("ProcessId")
            command = property_value("CommandLine")
            created = property_value("CreationDate")
            executable = property_value("ExecutablePath", optional=True)
            if type(actual_pid) is not int or not actual_pid:
                _bad("Win32_Process.ProcessId", "UNVERIFIED")
            return CommandLineObservation(actual_pid, command, created, command_birth(created), executable)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            failures = []
            for entry in reversed(acquired):
                try:
                    entry.release_owned(entry.registry.owner)
                except Exception as exc:
                    failures.append(f"{entry.label}: {exc}")
            # On a release error keep apartment/storage alive and do NOT retry.
            if failures:
                raise ObservationError("WMI release", "RESOURCE_RELEASE_UNKNOWN", detail=(
                    "; ".join(failures) + (f"; original={primary_error}" if primary_error else "")))
            self.ole.CoUninitialize()
