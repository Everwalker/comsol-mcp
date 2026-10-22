"""Windows pinned-root and private startup-config readers for ADMISSION_ONLY.

Only the built-in SDK path can attach Windows observation/config provenance.
Capacity is advisory caller-available space, not physical capacity, a quota or
isolation proof. No setter, service operation, privilege adjustment or process
launch is implemented here. Original path/SD adapters own every SDK resource.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path
import re
import threading
import time

from ._execution_contract import ExecutionContractError
from ._windows_sid import is_canonical_sid

_WINDOWS_STORAGE_OBSERVATION_ATTESTATION = object()
_WINDOWS_DISK_CONFIG_ATTESTATION = object()
_WINDOWS_DISK_PROFILE_ATTESTATION = object()
_read_lock = threading.RLock()
_pending = []
_MAX_CAPACITY = (1 << 63) - 1


def _fail(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage="pre_dispatch")


def _resources():
    from . import _w26_os_resources
    return _w26_os_resources


def _windows_host():
    r = _resources()
    if os.name != "nt" or r._current_platform_name() != "windows":
        _fail("DISK_STORAGE_PROVIDER_UNSUPPORTED", "Windows SDK disk reader requires Windows")
    return r._current_host_id()


def _path(value):
    value = os.fspath(value)
    if (type(value) is not str or len(value) >= 240 or not re.fullmatch(r"[A-Za-z]:\\.*", value)
            or ntpath.normpath(value) != value):
        _fail("DISK_RESOURCE_CONFIG_INVALID", "ordinary canonical absolute Windows drive path required")
    from ._windows_parameter_publish import _component
    try:
        for part in value[3:].split("\\") if value[3:] else ():
            _component(part)
    except Exception as exc:
        raise ExecutionContractError("DISK_RESOURCE_CONFIG_INVALID", "unsafe Windows path component") from exc
    return value


def _expected_root(root):
    r = _resources()
    if type(root) is not r.PathIdentity:
        _fail("DISK_ROOT_IDENTITY_MISMATCH", "exact typed root identity required")
    _path(root.path)
    for value, bits, minimum in ((root.volume_id, 64, 0), (root.file_id, 128, 1)):
        if (type(value) is not str or not re.fullmatch(r"0|[1-9][0-9]*", value)
                or len(value) > 39 or not minimum <= int(value) < (1 << bits)):
            _fail("DISK_ROOT_IDENTITY_MISMATCH", "full Windows root identifiers must be bounded canonical decimals")
    if not is_canonical_sid(root.owner_id):
        _fail("DISK_ROOT_IDENTITY_MISMATCH", "Windows root owner must be a canonical SID")


class _ReleaseFact:
    """Original release binding and one normalized SDK result, held until ack."""
    def __init__(self, entry):
        self.entry, self.registry = entry, entry.registry
        self.binding = (entry.adapter, entry.handle, entry.closer, entry.mode,
                        entry.seq, entry.domain, entry.value, entry.thread_id)
        self.keepalive = entry.keepalive
        self.confirmed = False
        self.local_failure = False
        self.result = None

    def check(self):
        e = self.entry
        actual = (e.adapter, e.handle, e.closer, e.mode, e.seq, e.domain, e.value, e.thread_id)
        if (e.registry is not self.registry or any(a is not b for a, b in zip(actual[:3], self.binding[:3]))
                or actual[3:] != self.binding[3:]):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk release entry/binding changed")
        slot = self.registry._entries.get(e.seq)
        if slot is not e and not (slot is None and e.state == "RELEASED" and self.confirmed):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk registry slot changed")
        if e.keepalive is not self.keepalive and not (
                slot is None and e.state == "RELEASED" and type(e.keepalive) is tuple and not e.keepalive):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk keepalive binding changed")

    def complete(self):
        self.check()
        e = self.entry
        return (self.confirmed and e.state == "RELEASED"
                and self.registry._entries.get(e.seq) is None and not e.keepalive)

    def acknowledge(self):
        # No closer, native query, retry_owned, new wrapper or adoption here.
        self.check()
        if not self.confirmed:
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "unconfirmed SDK result has no release retry authority")
        e = self.entry
        e.state = "RELEASED"
        if self.registry._entries.get(e.seq) is e:
            self.registry._entries.pop(e.seq)
        e.keepalive = ()


class _ReleaseGuard:
    """Per-original-entry SDK outcome; UNKNOWN can never repeat an opaque free."""
    def __init__(self, scope, name, function):
        self.scope, self.name = scope, name
        self.function = function
        self._original_function = function
        self.outcomes = {}
        self.facts = {}

    def __call__(self, handle):
        scope = self.scope
        scope.check_binding()
        scope.owner.check_owner()
        value = getattr(handle, "value", handle)
        domain = "native" if self.name == "CloseHandle" else "host_preflight_local"
        entry = scope.owner.registry.find(scope.api, handle, domain=domain)
        if entry is None or type(value) is not int or value <= 0 or entry.value != value:
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "disk cleanup requires its original registered native entry")
        fact = self.facts.get(entry.seq)
        if fact is not None:
            if not (self.name == "LocalFree" and fact.local_failure and scope.local_retry_entry is entry):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "opaque SDK release has no second-call authority")
            fact.check()
        else:
            if entry.seq in self.outcomes:
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "allocation has an unconfirmed original SDK result")
            fact = self.facts[entry.seq] = _ReleaseFact(entry)
        if any(isinstance(v, BaseException) for v in entry.keepalive):
            self.outcomes[entry.seq] = "RESULT_UNKNOWN"
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "allocation lost its SDK return; original output retained without free authority")
        if self.name == "LocalFree" and not scope.local_allocation(entry):
            self.outcomes[entry.seq] = "RESULT_UNKNOWN"
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "LocalFree requires a confirmed original SDK allocation")
        # BOOL zero/opaque outcomes are sticky UNKNOWN. LocalFree alone has a
        # documented retained-allocation failure, restricted below to the
        # strict original HLOCAL and this scope's confirmed SDK allocation.
        fact.local_failure = False
        fact.result = None
        self.outcomes[entry.seq] = "RESULT_UNKNOWN"
        result = self._original_function(handle)
        if self.name == "CloseHandle":
            if type(result) is not int or not -(1 << 31) <= result < (1 << 31):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "CloseHandle returned no strict SDK BOOL")
            success = result != 0
        else:
            if result is not None and (type(result) is not int or not 0 <= result < (1 << (8 * C.sizeof(C.c_void_p)))):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "LocalFree returned no strict SDK HLOCAL")
            success = result is None or result == 0
            if not success and result != value:
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "LocalFree returned an unrelated HLOCAL")
        if success:
            fact.result, fact.confirmed = result, True
            self.outcomes[entry.seq] = "RELEASED"
        elif self.name == "LocalFree":
            fact.result, fact.local_failure = result, True
            self.outcomes[entry.seq] = "LOCAL_ALLOCATION_RETAINED"
        return result


class _ReadScope:
    """Strong reference to the existing typed preflight owner, not new authority."""
    def __init__(self, path, kind, host, expected_identity=None):
        self.owner = None
        self.api = None
        self.guards = {}
        self.releases, self.ack_errors = {}, {}
        self.allocations, self.local_retry_entry = {}, None
        self.path, self.kind, self.host = ntpath.normcase(path), kind, host
        self.identities = set()
        self.retirement_started = self.retirement_returned = False
        self._check_conflict(expected_identity)

    def _check_conflict(self, identity=None):
        # A finite fence over this reader's actual pending roots/configs.
        # Kind cannot rename the same path/object into a new cleanup scope.
        for old in _pending:
            if old.path == self.path or (old.host == self.host and identity is not None and identity in old.identities):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "conflicting original disk root/config remains unresolved")

    def bind_identity(self, identity):
        self._check_conflict(identity)
        self.identities.add(identity)

    def capture(self, owner):
        from . import _windows_host_preflight_owner as owners
        _PreflightOwner = owners._PreflightOwner
        if type(owner) is not _PreflightOwner or self.owner is not None:
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original SDK owner callback required exactly once")
        owner.check_owner()
        self.owner, self.api = owner, owner.api
        self.registry, self.entries = owner.registry, owner.registry._entries
        self.roots, self.sdk = owners._ROOTS, owner.sdk
        self.owner_scope, self.owner_seal = owner.scope, owner._seal
        self.original_reconcile = owner.reconcile
        self.retire_callback = self._retire
        owner.reconcile = self.retire_callback
        self.original_acquire = self.registry.acquire
        self.acquire_callback = self._acquire
        self.registry.acquire = self.acquire_callback
        self.original_allocation_call = owner.allocation_call
        self.allocation_callback = self._allocation_call
        owner.allocation_call = self.allocation_callback
        self.closers = {}
        for name in ("CloseHandle", "LocalFree"):
            original = owner.function(name, owner.api.k)
            self.guards[name] = _ReleaseGuard(self, name, original)
            owner.sdk[name] = self.guards[name]
            self.closers[name] = original

    def _allocation_call(self, name, dll, storage, *args, family, keepalive=()):
        self.check_binding()
        function = self.owner.function(name, dll)
        result = self.original_allocation_call(name, dll, storage, *args, family=family, keepalive=keepalive)
        # Both documented producers return LocalFree-owned storage. Cache a
        # strict successful return only after the original registry captured
        # its output. Partial/opaque/raised returns convey no free authority.
        success = type(result) is int and (name == "GetSecurityInfo" and result == 0
            or name == "ConvertStringSidToSidW" and -(1 << 31) <= result < (1 << 31) and result != 0)
        if family == "local" and success and getattr(storage, "value", None):
            entry = self.registry.find(self.api, storage, domain="host_preflight_local")
            self.allocations[entry.seq] = (entry, storage, entry.value, name, function, result)
        return result

    def local_allocation(self, entry):
        record = self.allocations.get(entry.seq)
        return (record is not None and record[0] is entry and entry.domain == "host_preflight_local"
            and any(v is record[1] for v in entry.keepalive)
            and type(getattr(record[1], "value", None)) is int and record[1].value == record[2] == entry.value
            and self.sdk.get(record[3]) is record[4])

    def _acquire(self, *args, **kwargs):
        # Delegate to the unchanged original registry. Observe completion of
        # that same entry's Python release bookkeeping, including a setter
        # that takes effect then raises. No new resource wrapper or closer.
        self.check_binding()
        entry = self.original_acquire(*args, **kwargs)
        if entry.seq not in self.releases:
            original_release = entry.release_owned
            def release(*a, **kw):
                try:
                    return original_release(*a, **kw)
                except BaseException as error:
                    if any(f.entry is entry and f.confirmed for f in self._facts()):
                        self.ack_errors[entry.seq] = error
                    raise
            self.releases[entry.seq] = (entry, original_release, release)
            entry.release_owned = release
        return entry

    def check_binding(self):
        from . import _windows_host_preflight_owner as owners
        o = self.owner
        if (type(o) is not owners._PreflightOwner or o.api is not self.api or o._seal is not self.owner_seal
                or o.scope is not self.owner_scope or threading.get_ident() != self.owner_scope.thread_id
                or o.registry is not self.registry or self.registry.owner is not self.api
                or getattr(self.api, "_native_owned_registry", None) is not self.registry
                or getattr(self.api, "_readonly_preflight_owner", None) is not o
                or self.registry._entries is not self.entries or owners._ROOTS is not self.roots
                or self.registry.acquire is not self.acquire_callback
                or o.allocation_call is not self.allocation_callback
                or o.sdk is not self.sdk or o.reconcile is not self.retire_callback):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk owner/registry continuation binding changed")
        root = self.roots.get(id(self.api))
        if root is not o and not (root is None and self.retirement_started):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk owner root changed")
        for name, guard in self.guards.items():
            if (self.sdk.get(name) is not guard or guard.scope is not self
                    or guard.function is not self.closers[name] or guard._original_function is not self.closers[name]):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk SDK closer binding changed")
        if any(entry.release_owned is not callback for entry, original, callback in self.releases.values()):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk entry release bookkeeping binding changed")

    def _facts(self):
        return tuple(f for guard in self.guards.values() for f in guard.facts.values())

    def _retire(self):
        self.check_binding()
        if self.registry.unresolved() or self.ack_errors or not all(f.complete() for f in self._facts()):
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk resource acknowledgements remain incomplete")
        # Record entry into this exact owner's bookkeeping before its pop or
        # retired write can fail. No authority to create/re-adopt an owner.
        self.retirement_started = True
        self.original_reconcile()
        self.retirement_returned = True

    def retain(self, cause=None):
        self.owner.remember(cause)
        if self not in _pending:
            _pending.append(self)
        error = ExecutionContractError("DISK_NATIVE_RESOURCE_UNKNOWN",
            "original disk SDK owner retained; conflicting root/config reads are blocked",
            safe_retry=False, stage="pre_dispatch")
        error._original_disk_owner = self.owner
        raise error from (cause or self.owner.first_cause)

    def recover(self):
        self.check_binding()
        with self.registry._lock:
            facts = self._facts()
            for f in facts:
                f.check()
                if not f.confirmed and not (f.local_failure and self.local_allocation(f.entry)):
                    _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "SDK-entered or unconfirmed allocation remains sticky UNKNOWN")
            if any(not any(e is f.entry for f in facts) for e in self.registry.unresolved()):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "resource lacks this reader's original normalized success")
            for f in facts:
                if f.local_failure:
                    # Only this documented LocalFree failure can enter its
                    # fixed original closer again, explicitly through owner.
                    self.local_retry_entry = f.entry
                    try:
                        self.registry.retry_owned(f.entry, self.api)
                    finally:
                        self.local_retry_entry = None
                if not f.complete():
                    f.acknowledge()
                self.ack_errors.pop(f.entry.seq, None)
            if self.registry.unresolved():
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original registry acknowledgement remains incomplete")
            if not self.retirement_started:
                self._retire()
            else:
                # Original reconcile may have popped then raised, or its
                # retired assignment may have failed. Complete only that ack.
                from ._windows_host_preflight_owner import _ROOT_LOCK
                with _ROOT_LOCK:
                    self.check_binding()
                    if self.roots.get(id(self.api)) is self.owner:
                        self.roots.pop(id(self.api))
                    self.owner.retired = True
                    self.retirement_returned = True

    def finish(self):
        if self.owner is None:
            return
        from ._windows_host_preflight_owner import confirmed_original_cleanup
        try:
            self.check_binding()
            if self.registry.unresolved() or self.ack_errors or not all(f.complete() for f in self._facts()):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk resource bookkeeping remains incomplete")
            if not self.retirement_started:
                self._retire()
            if not self.retirement_returned or not confirmed_original_cleanup(self.owner):
                _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "original disk owner retirement remains incomplete")
        except BaseException as error:
            self.retain(error)


def recover_windows_disk_resources(original_owner):
    """Ack cached success, or explicitly coordinate original LocalFree failure.

    No native query/CloseHandle retry; opaque LocalFree outcomes cannot retry.
    """
    with _read_lock:
        matches = [s for s in _pending if s.owner is original_owner]
        if len(matches) != 1:
            _fail("DISK_NATIVE_RESOURCE_UNKNOWN", "recovery requires the retained original disk SDK owner")
        scope = matches[0]
        try:
            scope.recover()
            scope.finish()
        except BaseException as error:
            scope.retain(error)
        _pending.remove(scope)


@dataclass(frozen=True)
class WindowsRootObservation:
    """Readonly facts; serializable data conveys no owner or admission authority."""
    root_path: str
    volume_id: str
    root_file_id: str
    owner_id: str
    storage_id: str
    security_sha256: str
    full_fingerprint: tuple
    free_bytes: int | None = None
    total_bytes: int | None = None
    capacity_semantics: str = "CALLER_AVAILABLE_ADVISORY_NOT_HARD_QUOTA"


def _capacity(api, handle, path):
    # Documented fileapi.h ABI: BOOL, LPCWSTR, three PULARGE_INTEGER outputs.
    # https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdiskfreespaceexw
    from ._windows_host_preflight_owner import scalar
    owner = api._readonly_preflight_owner
    owner.check_owner()
    entry = owner.registry.find(api, handle)
    if entry is None or entry.state != "ACQUIRED":
        _fail("DISK_ROOT_IDENTITY_UNKNOWN", "capacity read requires the live original root HANDLE")
    getter = owner.function("GetDiskFreeSpaceExW", api.k)
    getter.argtypes = [C.c_wchar_p, C.POINTER(C.c_uint64), C.POINTER(C.c_uint64), C.POINTER(C.c_uint64)]
    getter.restype = C.c_int32
    available, total, total_free = C.c_uint64(), C.c_uint64(), C.c_uint64()
    owner.hold(handle, available, total, total_free, path)
    if not scalar(getter(path, C.byref(available), C.byref(total), C.byref(total_free)), "BOOL"):
        _fail("DISK_STORAGE_OBSERVATION_UNAVAILABLE", "original GetDiskFreeSpaceExW failed")
    a, t, f = available.value, total.value, total_free.value
    if not (0 <= a <= t <= _MAX_CAPACITY and t > 0 and a <= f <= _MAX_CAPACITY):
        _fail("DISK_STORAGE_OBSERVATION_INVALID", "SDK caller-available capacity is invalid or out of bounds")
    return a, t


def _owner_sid(api, handle):
    """Read the actual owner SID, not the ACL verifier's 'daemon' role label."""
    from ._windows_host_preflight_owner import scalar
    owner = api._readonly_preflight_owner
    advapi = C.WinDLL("advapi32", use_last_error=True)
    query = owner.function("GetSecurityInfo", advapi)
    query.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, *([C.POINTER(C.c_void_p)] * 5)]
    query.restype = C.c_uint32
    length = owner.function("GetSecurityDescriptorLength", advapi)
    length.argtypes, length.restype = [C.c_void_p], C.c_uint32
    sid, descriptor = C.c_void_p(), C.c_void_p()
    status = None
    try:
        status = scalar(owner.allocation_call("GetSecurityInfo", advapi, descriptor,
            handle, 1, 1, C.byref(sid), None, None, None, C.byref(descriptor),
            family="local", keepalive=(sid,)), "DWORD")
        if status != 0 or not descriptor.value or not sid.value:
            _fail("DISK_ROOT_IDENTITY_UNKNOWN", "original OWNER_SECURITY_INFORMATION read failed")
        size = scalar(length(descriptor), "DWORD")
        if not (20 <= size <= 1024 * 1024 and descriptor.value <= sid.value <= descriptor.value + size - 8):
            _fail("DISK_ROOT_IDENTITY_UNKNOWN", "owner SID pointer escapes its original bounded descriptor")
        header = C.string_at(sid, 8)
        extent = 8 + 4 * header[1]
        if header[0] != 1 or not 1 <= header[1] <= 15 or sid.value + extent > descriptor.value + size:
            _fail("DISK_ROOT_IDENTITY_UNKNOWN", "owner SID has an unsupported revision/count/extent")
        raw = C.string_at(sid, extent)
        authority = int.from_bytes(raw[2:8], "big")
        authority_text = str(authority) if authority < 2**32 else f"0x{authority:012x}"
        text = "S-1-" + authority_text + "".join(
            "-" + str(int.from_bytes(raw[i:i + 4], "little")) for i in range(8, extent, 4))
        if not is_canonical_sid(text):
            _fail("DISK_ROOT_IDENTITY_UNKNOWN", "native owner SID is noncanonical")
        return text
    finally:
        if descriptor.value:
            if status != 0:
                entry = owner.registry.find(api, descriptor, domain="host_preflight_local")
                error = ExecutionContractError("DISK_NATIVE_RESOURCE_UNKNOWN", "unconfirmed descriptor allocation retained", safe_retry=False)
                entry.state, entry.error = "UNKNOWN", error
                owner.sdk["LocalFree"].outcomes[entry.seq] = "RESULT_UNKNOWN"
            owner.release(descriptor, family="local")


def _native_read(path, *, directory, capacity=False, expected_identity=None):
    host = _windows_host()
    path = _path(path)
    if directory:
        return _native_root(path, host, expected_identity), None
    from ._host_scoped_provider import _verify_windows_private_path, WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS
    with _read_lock:
        scope = _ReadScope(path, "config", host)
        fingerprints, amounts, owners = [], [], []

        def observe(api, handle, getter):
            if api is not scope.api or api._readonly_preflight_owner is not scope.owner:
                _fail("DISK_ROOT_IDENTITY_UNKNOWN", "root observation lost its original SDK owner")
            before = getter(handle, directory=directory)
            scope.bind_identity(before[0])
            owners.append(_owner_sid(api, handle))
            if capacity:
                amounts.append(_capacity(api, handle, path))
            after = getter(handle, directory=directory)
            if before != after:
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "original root changed across its capacity observation")
            fingerprints.append(before)

        def read_file(api, handle, metadata):
            owner = scope.owner
            getter = owner.function("ReadFile", api.k)
            from ._host_scoped_provider import _windows_original_full_getter
            from ._windows_host_preflight_owner import scalar
            fingerprint = _windows_original_full_getter(api)
            before = fingerprint(handle, directory=False)
            maximum = _resources()._DISK_CONFIG_MAX_BYTES
            if before[1] <= 0 or before[1] > maximum or before[5] != 1:
                _fail("DISK_RESOURCE_CONFIG_INVALID", "startup config must be bounded, nonempty and single-link")
            raw = bytearray()
            while len(raw) <= maximum:
                buffer = C.create_string_buffer(min(65536, maximum + 1 - len(raw)))
                count = C.c_uint32()
                owner.hold(handle, buffer, count)
                if not scalar(getter(handle, buffer, len(buffer), C.byref(count), None), "BOOL"):
                    _fail("DISK_RESOURCE_CONFIG_INVALID", "original startup config ReadFile failed")
                if count.value > len(buffer):
                    _fail("DISK_RESOURCE_CONFIG_CHANGED", "SDK read exceeds its original bounded buffer")
                raw.extend(buffer.raw[:count.value])
                if count.value == 0:
                    break
            if fingerprint(handle, directory=False) != before or len(raw) != before[1] or len(raw) > maximum:
                _fail("DISK_RESOURCE_CONFIG_CHANGED", "startup config changed during its original HANDLE read")
            return bytes(raw), metadata

        try:
            answer = _verify_windows_private_path(Path(path), directory=directory, expected_identity=None,
                _original_owner_callback=scope.capture, _internal_stdio_observer=observe,
                **({"_retained_callback": read_file} if not directory else {}))
            raw, metadata = (None, answer) if directory else answer
            if (not fingerprints or any(f != fingerprints[0] for f in fingerprints)
                    or not owners or any(s != owners[0] for s in owners)
                    or metadata.get("security_semantics") != WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS):
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "full identity or private-path security changed")
            full = fingerprints[0]
            owner_sid = owners[0]
            root = _resources().PathIdentity(path, str(full[0][0]), str(full[0][1]), owner_sid)
            _expected_root(root)
            free, total = amounts[0] if amounts else (None, None)
            return WindowsRootObservation(path, root.volume_id, root.file_id, owner_sid,
                "windows-volume:" + root.volume_id, metadata["security_sha256"], full, free, total), raw
        finally:
            scope.finish()


def _native_root(path, host, expected_identity):
    """Pinned project root: trusted SD owner, then exact configured owner match.

    A project root is not a daemon-private config file. Reuse the existing
    SD policy's trusted-owner mode; never waive any ancestor/ACL/identity check.
    """
    from ._windows_parameter_publish import _API
    from ._windows_host_preflight_owner import hold_original, finish_original
    from ._host_scoped_provider import (
        _windows_volume_has_persistent_acls, _windows_security_context,
        _windows_original_full_getter, _win32_descriptor_digest, _canonical)
    with _read_lock:
        scope = _ReadScope(path, "root", host, expected_identity)
        api = _API()
        scope.capture(hold_original(api))
        primary = None
        try:
            persistent, legacy_volume = _windows_volume_has_persistent_acls(api, Path(path))
            if not persistent:
                _fail("DISK_ROOT_IDENTITY_UNKNOWN", "root filesystem has no persistent ACL capability")
            daemon_sid, trusted, _keepalive = _windows_security_context(api)
            full = _windows_original_full_getter(api)
            def snapshot(chain):
                if not chain:
                    _fail("DISK_ROOT_IDENTITY_UNKNOWN", "root traversal returned no original HANDLEs")
                result = []
                for index, handle in enumerate(chain):
                    entry = scope.owner.registry.find(api, handle)
                    if entry is None or entry.state != "ACQUIRED":
                        _fail("DISK_ROOT_IDENTITY_UNKNOWN", "root chain lost its original live entry")
                    if api.info(handle, directory=True)[0][0] != legacy_volume:
                        _fail("DISK_ROOT_IDENTITY_MISMATCH", "root name/ACL volume differs from the pinned HANDLE")
                    digest, owner_class = _win32_descriptor_digest(api, handle, directory=True,
                        require_daemon_owner=False, daemon_sid=daemon_sid, trusted_sids=trusted,
                        **({"pinned_ancestor_traversal": True} if index < len(chain) - 1 else {}))
                    fingerprint = full(handle, directory=True)
                    if index == len(chain) - 1:
                        scope.bind_identity(fingerprint[0])
                    result.append(dict(full=fingerprint, descriptor_sha256=digest, owner_class=owner_class))
                return result
            first = api.open_security_path(path, directory=True)
            before = snapshot(first)
            second = api.open_security_path(path, directory=True)
            if snapshot(second) != before:
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "full root name/SD/identity changed during traversal")
            sid = _owner_sid(api, first[-1])
            available, total = _capacity(api, first[-1], path)
            if snapshot(first) != before or snapshot(second) != before or _owner_sid(api, first[-1]) != sid:
                _fail("DISK_ROOT_IDENTITY_MISMATCH", "original root chain or owner changed across capacity read")
            fp = before[-1]["full"]
            root = _resources().PathIdentity(path, str(fp[0][0]), str(fp[0][1]), sid)
            _expected_root(root)
            return WindowsRootObservation(path, root.volume_id, root.file_id, sid,
                "windows-volume:" + root.volume_id, hashlib.sha256(_canonical(before)).hexdigest(), fp, available, total)
        except BaseException as error:
            primary = error
            scope.owner.remember(error)
            raise
        finally:
            try:
                finish_original(api, primary)
            finally:
                scope.finish()


def observe_windows_root(root_path):
    """Read root facts and advisory capacity; never manufacture profile authority."""
    return _native_read(root_path, directory=True, capacity=True)[0]


def verify_windows_root(expected_root):
    _expected_root(expected_root)
    # Incoming identity can only tighten a pending fence, never authorize a
    # read/release. Actual identity still comes from the original live HANDLE.
    observation = _native_read(expected_root.path, directory=True, capacity=True,
        expected_identity=(int(expected_root.volume_id), int(expected_root.file_id)))[0]
    if (observation.volume_id, observation.root_file_id, observation.owner_id) != (
            expected_root.volume_id, expected_root.file_id, expected_root.owner_id):
        _fail("DISK_ROOT_IDENTITY_MISMATCH", "current full volume/file ID/owner differs from the configured root")
    return observation


class WindowsStorageObservationProvider:
    """Exact built-in readonly Windows provider; no caller-supplied adapter."""
    provider_id = "windows-pinned-fileid-getdiskfreespaceex/v1"

    def __init__(self):
        self._host_id = _windows_host()

    @staticmethod
    def current_storage_id(root_path, expected_root):
        _expected_root(expected_root)
        if _path(root_path) != expected_root.path:
            _fail("DISK_ROOT_IDENTITY_MISMATCH", "storage lookup path differs from its exact profile root")
        return verify_windows_root(expected_root).storage_id

    def read_observation(self, profile):
        r = _resources()
        if type(self) is not WindowsStorageObservationProvider or type(profile) is not r.DiskResourceProfile:
            _fail("DISK_PROFILE_REQUIRED", "exact built-in provider and typed disk profile required")
        r.validate_disk_profile(profile)
        if profile.platform != "windows" or profile.host_id != self._host_id or _windows_host() != self._host_id:
            _fail("DISK_STORAGE_HOST_MISMATCH", "Windows disk profile belongs to another platform or host")
        result = verify_windows_root(profile.root)
        if result.storage_id != profile.storage_id:
            _fail("DISK_STORAGE_IDENTITY_MISMATCH", "live Windows volume differs from the configured storage")
        return r.StorageObservation(result.storage_id, result.root_path, result.volume_id, result.root_file_id,
            result.free_bytes, result.total_bytes, r.QuotaState.ADMISSION_ONLY.value, None, None, None, None,
            time.monotonic_ns(), host_id=self._host_id, storage_id=result.storage_id,
            evidence_classification="HOST_OBSERVED_ADMISSION_ONLY",
            _observation_attestation=_WINDOWS_STORAGE_OBSERVATION_ATTESTATION)


def is_windows_storage_observation(profile, observation):
    r = _resources()
    return (type(profile) is r.DiskResourceProfile and type(observation) is r.StorageObservation and profile.platform == "windows"
            and observation._observation_attestation is _WINDOWS_STORAGE_OBSERVATION_ATTESTATION
            and observation.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"
            and observation.host_id == profile.host_id and observation.storage_id == profile.storage_id
            and observation.filesystem_id == profile.storage_id)


def is_windows_disk_profile(profile):
    r = _resources()
    return (type(profile) is r.DiskResourceProfile and profile.platform == "windows"
            and profile._trusted_attestation is _WINDOWS_DISK_PROFILE_ATTESTATION)


def is_windows_disk_config(config):
    r = _resources()
    return (type(config) is r.TrustedDiskResourceConfig and config.platform == "windows"
            and config._attestation is _WINDOWS_DISK_CONFIG_ATTESTATION
            and all(is_windows_disk_profile(p) for p in config.profiles))


def parse_windows_disk_profile(raw, *, host_id, platform_name):
    """Existing strict disk schema and validators, with separate Windows origin."""
    r = _resources()
    fields = {"profile_id", "project_id", "root", "storage_id", "disk_budget", "allowed_operations", "trusted_estimates"}
    if type(raw) is not dict or set(raw) != fields or platform_name != "windows" or host_id != _windows_host():
        _fail("DISK_RESOURCE_CONFIG_INVALID", "Windows startup profile schema or live host does not match")
    if type(raw["root"]) is not dict or set(raw["root"]) != {"path", "volume_id", "file_id", "owner_id"}:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "strict disk root fields required")
    if type(raw["disk_budget"]) is not dict or set(raw["disk_budget"]) != {
            "mode", "reserve_free_bytes", "max_task_reservation_bytes", "quota_profile_id"}:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "strict disk budget fields required")
    if type(raw["allowed_operations"]) is not list or type(raw["trusted_estimates"]) is not list:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "disk operations/estimates must be lists")
    estimates = []
    for row in raw["trusted_estimates"]:
        if type(row) is not dict or set(row) != {"operation_id", "estimate_bytes"}:
            _fail("DISK_RESOURCE_CONFIG_INVALID", "strict estimate fields required")
        estimates.append(r.DiskOperationEstimate(**row))
    try:
        root = r.PathIdentity(**raw["root"])
        _expected_root(root)
        profile = r.DiskResourceProfile(raw["profile_id"], raw["project_id"], "windows", host_id, root,
            raw["storage_id"], r.DiskBudgetPolicy(**raw["disk_budget"]), tuple(raw["allowed_operations"]),
            tuple(estimates), _WINDOWS_DISK_PROFILE_ATTESTATION)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError("DISK_RESOURCE_CONFIG_INVALID", "Windows disk profile failed shared validation") from exc
    if WindowsStorageObservationProvider.current_storage_id(root.path, root) != profile.storage_id:
        _fail("DISK_STORAGE_IDENTITY_MISMATCH", "startup storage ID differs from the pinned Windows root")
    return profile


def load_windows_disk_config(path):
    r = _resources()
    host_id = _windows_host()
    _facts, raw = _native_read(path, directory=False)
    document = r._strict_disk_config(raw)
    r._reject_secrets(document, "disk resource startup config")
    if set(document) != {"schema", "host_id", "platform", "profiles"} or document["schema"] != r.DISK_CONFIG_SCHEMA:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "strict startup config fields/schema required")
    if document["platform"] != "windows" or document["host_id"] != host_id:
        _fail("DISK_RESOURCE_CONFIG_HOST_MISMATCH", "Windows startup config belongs to another platform or host")
    rows = document["profiles"]
    if type(rows) is not list or not 1 <= len(rows) <= 256:
        _fail("DISK_RESOURCE_CONFIG_INVALID", "bounded nonempty disk profile list required")
    profiles = tuple(parse_windows_disk_profile(p, host_id=host_id, platform_name="windows") for p in rows)
    if len({p.profile_id for p in profiles}) != len(profiles) or len({p.project_id for p in profiles}) != len(profiles):
        _fail("DISK_RESOURCE_CONFIG_INVALID", "duplicate project/profile identity")
    if _windows_host() != host_id:
        _fail("DISK_RESOURCE_CONFIG_HOST_MISMATCH", "live Windows host changed during config loading")
    return r.TrustedDiskResourceConfig(host_id, "windows", profiles, hashlib.sha256(raw).hexdigest(),
                                       _WINDOWS_DISK_CONFIG_ATTESTATION)


def verify_windows_profile_project(profile, project_id, project, *, code_prefix="RESOURCE_RESERVATION"):
    r = _resources()
    if (type(profile) not in (r.DiskResourceProfile, r.HostResourceProfile) or profile.platform != "windows"
            or profile.project_id != project_id or profile.host_id != _windows_host()
            or r._path_key(project.get("workspace", ""), "windows") != r._path_key(profile.root.path, "windows")):
        _fail(code_prefix + "_ROOT_AUTHORITY_MISMATCH", "Windows profile/project/host/workspace must bind exactly")
    verify_windows_root(profile.root)
