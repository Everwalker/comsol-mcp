"""Bounded NetworkIsolation catalog diagnostics; never profile/delete/launch authority.

ABI: Microsoft netfw.h INET_FIREWALL_APP_CONTAINER and DWORD Enum/Free,
Win64 natural alignment. WCHAR is read as manual UTF-16LE, not host wchar_t.
No integration with Root, provider, AppContainer mutations or admission gates.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import sys
import threading

DWORD = C.c_uint32
SIZE_T = C.c_uint64
PTR = C.c_void_p
_U64 = (1 << 64) - 1
_SEAL, _TEST_SEAL = object(), object()
_roots = {}  # exact original owners, including unknown outputs; no GC cleanup
_roots_lock = threading.RLock()

SDK_SOURCES = (
    "https://learn.microsoft.com/en-us/windows/win32/api/netfw/nf-netfw-networkisolationenumappcontainers",
    "https://learn.microsoft.com/en-us/windows/win32/api/netfw/nf-netfw-networkisolationfreeappcontainers",
    "https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/netfw.h",
    "https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-memory_basic_information",
)


class _Capabilities(C.Structure):
    _fields_ = [("count", DWORD), ("capabilities", PTR)]


class _Binaries(C.Structure):
    _fields_ = [("count", DWORD), ("binaries", PTR)]


class _Container(C.Structure):
    _fields_ = [("appContainerSid", PTR), ("userSid", PTR), ("appContainerName", PTR),
                ("displayName", PTR), ("description", PTR), ("capabilities", _Capabilities),
                ("binaries", _Binaries), ("workingDirectory", PTR), ("packageFullName", PTR)]


class _SidAttributes(C.Structure):
    _fields_ = [("Sid", PTR), ("Attributes", DWORD)]


class _MemoryInfo(C.Structure):
    _fields_ = [("BaseAddress", PTR), ("AllocationBase", PTR), ("AllocationProtect", DWORD),
                ("PartitionId", C.c_uint16), ("RegionSize", SIZE_T), ("State", DWORD),
                ("Protect", DWORD), ("Type", DWORD)]


class CatalogError(RuntimeError):
    def __init__(self, code, message, *, owner=None, native_error=None):
        super().__init__(message)
        self.code, self.owner, self.native_error = code, owner, native_error


def _fail(code, message, **details):
    raise CatalogError(code, message, **details)


def _scalar(raw, bits=32):
    # Access a possibly stateful .value exactly once; never bool(raw), HRESULT,
    # GetLastError, or a second conversion of the original returned object.
    value = getattr(raw, "value", raw)
    if type(value) is not int or not 0 <= value < 1 << bits:
        _fail("SDK_RETURN", "original SDK result is not a strict unsigned scalar")
    return value


@dataclass(frozen=True)
class Bounds:
    rows: int = 256
    capabilities_per_row: int = 64
    capabilities_total: int = 4096
    name_utf16_units: int = 1024
    binary_count: int = 4096
    copied_bytes: int = 2 * 1024 * 1024
    span_queries: int = 8192

    def __post_init__(self):
        maximum = (256, 64, 4096, 1024, 4096, 2 * 1024 * 1024, 8192)
        for value, limit in zip(vars(self).values(), maximum):
            if type(value) is not int or not 1 <= value <= limit:
                _fail("BOUNDS", "only positive finite bounds within the fixed ceiling are permitted")


@dataclass(frozen=True)
class Capability:
    sid: bytes
    attributes: int


@dataclass(frozen=True)
class CatalogRow:
    appcontainer_sid: bytes
    user_sid: bytes
    name: str
    name_utf16le: bytes
    capabilities: tuple[Capability, ...]
    binary_count: int
    binaries: str = "NOT_PARSED"
    display_description_working_directory_package: str = "NOT_PARSED"


@dataclass(frozen=True)
class Observation:
    rows: tuple[CatalogRow, ...]
    flags: int
    state: str
    issues: tuple[str, ...]
    source: str
    generation: str = "UNAVAILABLE"
    completeness: str = "UNVERIFIED"
    visibility: str = "UNVERIFIED"
    absence: str = "UNVERIFIED"
    authority: str = "DIAGNOSTIC_ONLY"
    binaries: str = "NOT_PARSED"
    binary_computation: str = "NOT_REQUESTED_FLAGS0"
    caveats: tuple[str, ...] = ("FLAGS0_MAY_BE_STALE_OR_INCOMPLETE", "FLAG1_IS_NOT_COMPLETENESS_PROOF",
        "CATALOG_NOT_PROFILE_GENERATION_OR_STORAGE_PROOF", "B_C_RESUME_RESTORE_AUTHORITY_UNAVAILABLE")
    sdk_version: str = "UNAVAILABLE"
    sdk_sources: tuple[str, ...] = SDK_SOURCES


@dataclass(frozen=True)
class ReadResult:
    observation: Observation
    owner: _Owner


@dataclass(frozen=True)
class Selection:
    state: str
    rows: tuple[CatalogRow, ...]
    authority: str = "DIAGNOSTIC_ONLY"
    absence: str = "UNVERIFIED"
    generation: str = "UNAVAILABLE"


def select_exact(observation, appcontainer_sid, user_sid, name):
    """Pure exact-tuple diagnosis; even fabricated dataclasses grant no authority."""
    if (type(observation) is not Observation or type(appcontainer_sid) is not bytes
            or type(user_sid) is not bytes or type(name) is not str
            or len(appcontainer_sid) > 68 or len(user_sid) > 68 or len(name) > 1024):
        _fail("SELECTION", "bounded diagnostic observation and exact SID/user/name tuple required")
    selected = tuple(r for r in observation.rows if
                     (r.appcontainer_sid, r.user_sid, r.name) == (appcontainer_sid, user_sid, name))
    state = "NOT_OBSERVED" if not selected else "AMBIGUOUS" if len(selected) != 1 or observation.issues else "MATCH_OBSERVED_UNVERIFIED"
    return Selection(state, selected)


def _abi():
    if (C.sizeof(PTR), C.sizeof(_Container), C.sizeof(_Capabilities), C.sizeof(_Binaries),
            C.sizeof(_SidAttributes), C.sizeof(_MemoryInfo)) != (8, 88, 16, 16, 16, 48):
        _fail("ABI", "Win64 natural-alignment ABI required; no packed/Win32 fallback")


class _Api:
    def __init__(self):
        _abi()
        if sys.platform != "win32":
            _fail("PLATFORM", "native catalog requires Windows; no SDK called")
        self.firewall = C.WinDLL("Firewallapi.dll", use_last_error=True, winmode=0x800)
        self.kernel = C.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
        self.enum = self.firewall.NetworkIsolationEnumAppContainers
        self.enum.restype, self.enum.argtypes = DWORD, [DWORD, C.POINTER(DWORD), C.POINTER(PTR)]
        self.free = self.firewall.NetworkIsolationFreeAppContainers
        self.free.restype, self.free.argtypes = DWORD, [PTR]
        self.query = self.kernel.VirtualQuery
        self.query.restype, self.query.argtypes = SIZE_T, [PTR, C.POINTER(_MemoryInfo), SIZE_T]
        self.source = "NATIVE_READONLY_CATALOG"
        self.keepalive = (self.firewall, self.kernel)

    @classmethod
    def _test(cls, enum, free, query, *, keepalive=(), seal):
        if seal is not _TEST_SEAL or not all(callable(f) for f in (enum, free, query)):
            _fail("TEST_SEAL", "private software exports only")
        _abi()
        api = cls.__new__(cls)
        api.enum, api.free, api.query = enum, free, query
        api.source, api.keepalive = "SOFTWARE_ONLY", tuple(keepalive)
        return api


class _Call:
    """Original callable, argument array, buffers, raw result and once-normalized scalar."""
    def __init__(self, function, args, outputs=()):
        self.function, self.args, self.outputs = function, args, outputs
        self.raw, self.scalar, self.state, self.cause = None, None, "NOT_INVOKED", None

    def invoke(self, bits=32):
        if self.state != "NOT_INVOKED":
            _fail("NO_REPEAT", "the original SDK call cannot be invoked twice")
        self.state = "INVOKING"
        try:
            self.raw = self.function(*self.args)
            self.scalar = _scalar(self.raw, bits)
            self.state = "RETURNED"
            return self.scalar
        except BaseException as exc:
            self.state, self.cause = "UNKNOWN", exc
            raise


class _Reader:
    def __init__(self, owner):
        self.owner, self.copied, self.queries = owner, 0, 0

    def _range(self, pointer, size):
        if (type(pointer) is not int or not 0 < pointer < _U64
                or type(size) is not int or not 0 < size <= self.owner.bounds.copied_bytes
                or size > _U64 - pointer):
            _fail("POINTER_BOUNDS", "null/overflow/over-budget native span")

    def chunks(self, pointer, size):
        self._range(pointer, size)
        cursor, remaining = pointer, size
        while remaining:
            self.owner._check_original()
            self.queries += 1
            if self.queries > self.owner.bounds.span_queries:
                _fail("QUERY_BUDGET", "VirtualQuery span budget exceeded")
            info, argument = _MemoryInfo(), PTR(cursor)
            call = _Call(self.owner.query_fn, (argument, C.byref(info), C.sizeof(info)), (argument, info))
            self.owner.queries.append(call)  # actual CallerBuf retained before dispatch
            returned = call.invoke(64)
            if returned != C.sizeof(info):
                _fail("MEMORY_INFO", "VirtualQuery returned no exact Win64 information extent")
            base, region_size = info.BaseAddress, info.RegionSize
            if (type(base) is not int or not 0 < base <= cursor or not region_size
                    or region_size > _U64 - base or not cursor < base + region_size
                    or info.State != 0x1000 or info.Protect & 0x100
                    or info.Protect & 0xFF not in {2, 4, 8, 0x20, 0x40, 0x80}):
                _fail("MEMORY_UNREADABLE", "native span is not committed and readable")
            amount = min(remaining, base + region_size - cursor)
            yield cursor, amount
            cursor, remaining = cursor + amount, remaining - amount

    def validate(self, pointer, size):
        for _ in self.chunks(pointer, size):
            pass

    def read(self, pointer, size):
        self._range(pointer, size)
        if self.copied + size > self.owner.bounds.copied_bytes:
            _fail("COPY_BUDGET", "total copied native bytes exceed finite budget")
        self.copied += size
        parts = []
        for address, count in self.chunks(pointer, size):
            parts.append(C.string_at(address, count))  # only after that exact span's query
        return b"".join(parts)

    def sid(self, pointer):
        header = self.read(pointer, 8)
        if header[0] != 1 or header[1] > 15:
            _fail("SID", "bounded revision1 SID required")
        raw = self.read(pointer, 8 + 4 * header[1])
        if raw[:8] != header:
            _fail("SID_DRIFT", "SID header changed during copied read")
        return raw

    def name(self, pointer):
        if type(pointer) is not int or pointer % 2:
            _fail("UTF16", "aligned non-null UTF16 pointer required")
        raw = bytearray()
        for index in range(self.owner.bounds.name_utf16_units + 1):
            unit = self.read(pointer + index * 2, 2)
            if unit == b"\0\0":
                if not raw:
                    _fail("UTF16", "nonempty appContainerName required")
                try:
                    return bytes(raw).decode("utf-16-le", errors="strict"), bytes(raw)
                except UnicodeDecodeError as exc:
                    raise CatalogError("UTF16", "malformed native UTF16 name") from exc
            if index == self.owner.bounds.name_utf16_units:
                break
            raw.extend(unit)
        _fail("UTF16_BOUNDS", "name has no terminator within bounded original UTF16 span")


class _Owner:
    def __init__(self, api, flags, bounds, *, seal):
        if seal not in {_SEAL, _TEST_SEAL} or type(api) is not _Api or type(bounds) is not Bounds:
            _fail("ORIGINAL_OWNER", "private actual SDK owner required")
        if type(flags) is not int or flags not in (0, 1):
            _fail("FLAGS", "only NETISO flags0/1 permitted")
        _abi()
        self.api, self.flags, self.bounds = api, flags, bounds
        self._original_self, self._seal = self, seal
        self.original_api, self.original_bounds = api, bounds
        self.enum_fn, self.free_fn, self.query_fn = api.enum, api.free, api.query
        self.original_functions = (api.enum, api.free, api.query)
        self.libraries = api.keepalive
        self.original_libraries = self.libraries
        self.count, self.slot = DWORD(), PTR()
        self.original_count_slot, self.original_output_slot = self.count, self.slot
        self.args = (flags, C.byref(self.count), C.byref(self.slot))
        self.original_args, self.original_flags = self.args, flags
        self.enum_call = _Call(self.enum_fn, self.args, (self.count, self.slot, api, self.libraries))
        self.original_call = self.enum_call
        self.free_call, self.free_args = None, None
        self.original_free_call, self.original_free_args, self.free_outcome = None, None, None
        self.queries, self.original_value, self.returned_count = [], None, None
        self.state, self.base_valid = "NEW", False
        self.no_allocation = False
        self.thread = threading.get_ident()
        self.observation, self.cause = None, None
        with _roots_lock:
            _roots[id(self)] = self

    def _checkpoint(self, event):
        pass  # metadata only; software tests may inject a bookkeeping fault

    def _check_original(self):
        with _roots_lock:
            rooted = _roots.get(id(self)) is self
        if (not rooted or self.thread != threading.get_ident() or self.api is not self.original_api
                or self._original_self is not self
                or self.bounds is not self.original_bounds or self.enum_call is not self.original_call
                or self.count is not self.original_count_slot or self.slot is not self.original_output_slot
                or self.enum_fn is not self.original_functions[0] or self.free_fn is not self.original_functions[1]
                or self.query_fn is not self.original_functions[2]
                or any(actual is not original for actual, original in zip(
                    (self.api.enum, self.api.free, self.api.query), self.original_functions))
                or self.libraries is not self.original_libraries or self.api.keepalive is not self.original_libraries
                or self.enum_call.function is not self.enum_fn or self.enum_call.args is not self.args
                or self.args is not self.original_args or self.flags != self.original_flags
                or type(self.flags) is not int
                or self.args[0] != self.flags or self.args[1]._obj is not self.count or self.args[2]._obj is not self.slot
                or (self.returned_count is not None and (self.slot.value != self.original_value
                    or self.count.value != self.returned_count))):
            self.state = "UNKNOWN"
            _fail("OWNER_DRIFT", "original SDK/slot/argument/owner binding changed", owner=self)
        if self.state == "UNKNOWN":
            _fail("OWNER_UNKNOWN", "unconfirmed original allocation/effect is retained without retry", owner=self)

    def _forget(self):
        self._checkpoint("released")
        with _roots_lock:
            if _roots.get(id(self)) is not self:
                _fail("OWNER_DRIFT", "original strong root changed", owner=self)
            del _roots[id(self)]
        self.state = "RELEASED"

    def release(self):
        """Exact original free once; confirmed DWORD0 permits pure metadata ack only."""
        if getattr(self, "_original_self", None) is not self:
            _fail("ORIGINAL_OWNER", "replacement owner cannot acknowledge the original allocation")
        if self.state == "RELEASED":
            return
        self._check_original()
        if self.no_allocation:
            if self.slot.value is not None or self.free_call is not None or self.enum_call.state != "RETURNED":
                _fail("FREE_UNPROVEN", "empty output acknowledgement has no allocation/free authority", owner=self)
            self._finish_empty()
            return
        if not self.base_valid or self.enum_call.scalar != 0 or self.enum_call.state != "RETURNED":
            _fail("FREE_UNPROVEN", "no confirmed valid original Enum allocation", owner=self)
        if self.free_call is None:
            self.free_args = (self.slot,)
            self.free_call = _Call(self.free_fn, self.free_args, (self.slot, self.count, self.enum_call, self.api))
            self.original_free_call, self.original_free_args = self.free_call, self.free_args
        call = self.free_call
        if (call is not self.original_free_call or self.free_args is not self.original_free_args
                or call.function is not self.free_fn or call.args is not self.free_args
                or len(call.args) != 1 or call.args[0] is not self.slot):
            _fail("OWNER_DRIFT", "original free call/argument drifted", owner=self)
        if call.state == "NOT_INVOKED":
            self.state = "FREE_INVOKING"
            try:
                code = call.invoke()
                self.free_outcome = (call, code)
                if code != 0:
                    _fail("FREE_RESULT", "NetIso Free did not confirm DWORD0", native_error=code)
            except BaseException as exc:
                self.state, self.cause = "UNKNOWN", exc
                raise CatalogError("FREE_UNKNOWN", str(exc), owner=self, native_error=call.scalar) from exc
        if (call.state != "RETURNED" or type(call.scalar) is not int or call.scalar != 0
                or self.free_outcome is None or self.free_outcome[0] is not call or self.free_outcome[1] != 0):
            self.state = "UNKNOWN"
            _fail("FREE_UNKNOWN", "original free outcome is not confirmed", owner=self)
        self.state = "FREE_ACK_PENDING"
        try:
            self._check_original()  # pure metadata, never VirtualQuery freed storage
            self._forget()
        except BaseException as exc:
            self.cause = exc
            raise CatalogError("FREE_ACK_PENDING", str(exc), owner=self) from exc

    def _finish_empty(self):
        self.no_allocation, self.state = True, "NO_ALLOCATION_ACK_PENDING"
        try:
            self._forget()
        except BaseException as exc:
            self.cause = exc
            raise CatalogError("NO_ALLOCATION_ACK_PENDING", str(exc), owner=self) from exc

    def read(self):
        if self.state != "NEW":
            _fail("NO_REPEAT", "one original enumeration only", owner=self)
        self._check_original()
        self.state = "ENUM_INVOKING"
        try:
            code = self.enum_call.invoke()
        except BaseException as exc:
            self.state, self.cause = "UNKNOWN", exc
            raise CatalogError("ENUM_UNKNOWN", str(exc), owner=self) from exc
        self.original_value, self.returned_count = self.slot.value, self.count.value
        if code:
            self.state = "UNKNOWN" if self.original_value is not None else "ENUM_FAILED_NO_ALLOCATION"
            if self.original_value is None:
                self._finish_empty()
            _fail("ENUM_ERROR", "Enum returned a nonzero DWORD", owner=self, native_error=code)
        self.state = "ENUM_RETURNED"
        reader = _Reader(self)
        if self.original_value is None:
            if self.returned_count:
                self.state = "UNKNOWN"
                _fail("ENUM_NULL", "successful nonzero count has no original array", owner=self)
            self.observation = self._observation(())
            self._finish_empty()
            return ReadResult(self.observation, self)
        # A readable aligned base and confirmed SDK success are required even
        # for a malformed/over-budget count; otherwise never free by pointer guess.
        try:
            if self.original_value % C.alignment(_Container):
                _fail("ARRAY_ALIGNMENT", "original array base is not naturally aligned")
            reader.validate(self.original_value, C.sizeof(_Container) if self.returned_count else 1)
            self.base_valid = True
        except BaseException as exc:
            self.state, self.cause = "UNKNOWN", exc
            raise CatalogError("ARRAY_UNKNOWN", str(exc), owner=self) from exc
        try:
            rows = self._copy_rows(reader)
            self.observation = self._observation(rows)
        except BaseException as exc:
            self.cause = exc
            # Unknown pointer-validation calls do not justify more SDK calls.
            if any(q.state == "UNKNOWN" for q in self.queries):
                self.state = "UNKNOWN"
            else:
                self.release()
            raise CatalogError(getattr(exc, "code", "PARSE_ERROR"), str(exc), owner=self) from exc
        self.release()
        return ReadResult(self.observation, self)

    def _copy_rows(self, reader):
        if self.returned_count > self.bounds.rows:
            _fail("ROW_BUDGET", "original row count exceeds finite bound")
        if not self.returned_count:
            return ()
        raw = reader.read(self.original_value, self.returned_count * C.sizeof(_Container))
        rows, total = [], 0
        for index in range(self.returned_count):
            row = _Container.from_buffer_copy(raw, index * C.sizeof(_Container))
            count = row.capabilities.count
            total += count
            if count > self.bounds.capabilities_per_row or total > self.bounds.capabilities_total:
                _fail("CAPABILITY_BUDGET", "capability count exceeds bounded copy budget")
            if row.binaries.count > self.bounds.binary_count:
                _fail("BINARY_COUNT", "unparsed binary count exceeds metadata bound")
            app, user = reader.sid(row.appContainerSid), reader.sid(row.userSid)
            name, name_raw = reader.name(row.appContainerName)
            capabilities = []
            if count:
                if (type(row.capabilities.capabilities) is not int
                        or row.capabilities.capabilities % C.alignment(_SidAttributes)):
                    _fail("CAPABILITY_ALIGNMENT", "naturally aligned original capability array required")
                cap_raw = reader.read(row.capabilities.capabilities, count * C.sizeof(_SidAttributes))
                for cap_index in range(count):
                    item = _SidAttributes.from_buffer_copy(cap_raw, cap_index * C.sizeof(_SidAttributes))
                    capabilities.append(Capability(reader.sid(item.Sid), item.Attributes))
            rows.append(CatalogRow(app, user, name, name_raw, tuple(capabilities), row.binaries.count))
        return tuple(rows)

    def _observation(self, rows):
        # Caveats are separate from ambiguity so normal tuple diagnosis remains useful.
        ambiguous = []
        keys = [(r.appcontainer_sid, r.user_sid, r.name) for r in rows]
        identities = [(r.appcontainer_sid, r.user_sid) for r in rows]
        if len(set(keys)) != len(keys) or len(set(identities)) != len(identities):
            ambiguous.append("DUPLICATE_OR_CONFLICTING_REGISTRATION")
        if any(len({c.sid for c in r.capabilities}) != len(r.capabilities) for r in rows):
            ambiguous.append("DUPLICATE_CAPABILITY")
        state = "NOT_OBSERVED" if not rows else "AMBIGUOUS" if ambiguous else "ROWS_OBSERVED_UNVERIFIED"
        return Observation(rows, self.flags, state, tuple(ambiguous), self.api.source,
                           binary_computation="REQUESTED_FLAGS1" if self.flags else "NOT_REQUESTED_FLAGS0")


def retained_catalog_owners():
    with _roots_lock:
        return tuple(_roots.values())


def recover_catalog_owner(owner):
    if type(owner) is not _Owner:
        _fail("ORIGINAL_OWNER", "only the retained original owner may acknowledge cleanup")
    owner.release()


def observe_catalog(*, flags=0, bounds=None):
    """Native read-only entry. No callable/provider/JSON native source injection."""
    if type(flags) is not int or flags not in (0, 1):
        _fail("FLAGS", "only NETISO flags0/1 permitted")
    selected = Bounds() if bounds is None else bounds
    if type(selected) is not Bounds:
        _fail("BOUNDS", "fixed finite Bounds required")
    return _Owner(_Api(), flags, selected, seal=_SEAL).read()


def _software_owner(enum, free, query, *, flags=0, bounds=None, keepalive=(), seal):
    if seal is not _TEST_SEAL:
        _fail("TEST_SEAL", "software source requires private test seal")
    api = _Api._test(enum, free, query, keepalive=keepalive, seal=seal)
    return _Owner(api, flags, Bounds() if bounds is None else bounds, seal=seal)
