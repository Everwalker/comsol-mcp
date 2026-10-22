"""Finite borrowed-file LABEL reads. No MIC enforcement or issuer authority.

Only LABEL_SECURITY_INFORMATION is queried. This deliberately closed parser
does not interpret a full SACL/DACL, inherited-only ACE, or unknown policy.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import asdict, dataclass, field
import hashlib
import os
import struct
import threading
from types import SimpleNamespace

from ._execution_contract import ExecutionContractError
from ._windows_owned_resources import (
    NativeOwnedRegistry, NativeResourceError, _OwnedResource, attach_registry,
)

LABEL_SECURITY_INFORMATION = 0x10
SE_FILE_OBJECT = 1
MAX_DESCRIPTOR = 1024 * 1024
MAX_ACES = 64
_retained = {}  # Original registries/APIs, not a journal or reconstructed owner.
_retention_lock = threading.RLock()
_SEAL = object()


class MicReadError(ExecutionContractError):
    pass


def _bad(code, message, **details):
    raise MicReadError(code, message, safe_retry=False, stage='pre_dispatch', details=details or None)


def _unknown(message):
    raise NativeResourceError('NATIVE_RESOURCE_RELEASE_UNKNOWN', message, safe_retry=False, stage='pre_dispatch')


@dataclass(frozen=True)
class MandatoryLabelAce:
    ace_type: int
    flags: int
    mask: int
    policy_flags: tuple[str, ...]
    sid: str
    rid: int
    inherited: bool


@dataclass(frozen=True)
class LabelDescriptor:
    raw: bytes
    raw_sha256: str
    control: int
    sacl_offset: int
    sacl_state: str
    ace: MandatoryLabelAce | None
    documented_default: str | None
    reasons: tuple[str, ...]
    state: str = field(default='UNVERIFIED', init=False)


def _header(raw):
    if type(raw) is not bytes or not 20 <= len(raw) <= MAX_DESCRIPTOR:
        _bad('MIC_SD_BOUNDS', 'bounded immutable self-relative SD bytes required')
    revision, reserved, control, owner, group, sacl, dacl = struct.unpack_from('<BBHIIII', raw)
    if revision != 1 or reserved or not control & 0x8000 or control & ~0xBF3F:
        _bad('MIC_SD_UNSUPPORTED', 'unsupported SD revision, control or resource-manager layout')
    if owner or group or dacl:
        _bad('MIC_SD_SCOPE_UNSUPPORTED', 'LABEL-only descriptor must not contain unrequested owner/group/DACL components')
    if sacl and (not control & 0x10 or sacl < 20 or sacl % 4 or sacl > MAX_DESCRIPTOR - 8):
        _bad('MIC_SD_OFFSET', 'SACL relative offset is absent, unaligned or outside the finite descriptor')
    return control, sacl


def parse_label_security_descriptor(raw: bytes) -> LabelDescriptor:
    """Closed LABEL-only SD parser. Bytes are facts, never authorization.

    No ACE is manufactured for absent/NULL/empty SACL. Documented Medium
    default is a separate interpretation, not a returned ACE or tested policy.
    """
    control, offset = _header(raw)
    label = None
    reasons = []
    if not offset:
        if len(raw) != 20:
            _bad('MIC_SD_EXTENT', 'unexpected bytes outside the absent/NULL SACL descriptor')
        sacl_state = 'NULL' if control & 0x10 else 'ABSENT'
    else:
        if offset + 8 > len(raw) or any(raw[20:offset]):
            _bad('MIC_SD_OFFSET', 'SACL header/gap is outside the closed byte layout')
        revision, reserved, size, count, reserved2 = struct.unpack_from('<BBHHH', raw, offset)
        if (revision not in (2, 4) or reserved or reserved2 or size < 8 or size % 4
                or offset + size != len(raw) or count > MAX_ACES):
            _bad('MIC_ACL_BOUNDS', 'unsupported ACL header, extent, alignment or ACE budget')
        cursor = offset + 8
        for _ in range(count):
            if cursor + 4 > len(raw):
                _bad('MIC_ACE_BOUNDS', 'ACE header exceeds SACL extent')
            kind, flags, extent = struct.unpack_from('<BBH', raw, cursor)
            if extent < 20 or extent % 4 or cursor + extent > len(raw):
                _bad('MIC_ACE_BOUNDS', 'ACE extent is truncated, unaligned or outside SACL')
            if kind != 0x11:
                _bad('MIC_ACE_UNSUPPORTED', 'LABEL-only SACL contains an unknown/non-label ACE')
            if flags & 0x08:
                _bad('MIC_ACE_INHERIT_ONLY', 'inherit-only label does not apply to this object; no default inferred')
            if flags & ~0x17 or (flags & 4 and not flags & 3):
                _bad('MIC_ACE_FLAGS_UNSUPPORTED', 'unknown or unsupported mandatory-label inheritance flags')
            mask = struct.unpack_from('<I', raw, cursor + 4)[0]
            if mask & ~7:
                _bad('MIC_POLICY_UNSUPPORTED', 'mandatory-label mask has unsupported policy bits')
            sid = raw[cursor + 8:cursor + extent]
            if (len(sid) != 12 or sid[0] != 1 or sid[1] != 1
                    or int.from_bytes(sid[2:8], 'big') != 16):
                _bad('MIC_LABEL_SID_INVALID', 'label SID must be exactly revision1, authority16, one UINT32 RID')
            rid = int.from_bytes(sid[8:12], 'little')
            if label is not None:
                _bad('MIC_MULTIPLE_LABELS', 'duplicate/conflicting mandatory labels are not interpreted')
            policy = tuple(name for bit, name in ((1, 'NO_WRITE_UP'), (2, 'NO_READ_UP'), (4, 'NO_EXECUTE_UP')) if mask & bit)
            label = MandatoryLabelAce(kind, flags, mask, policy, 'S-1-16-' + str(rid), rid, bool(flags & 0x10))
            if rid not in (0, 0x1000, 0x2000, 0x3000, 0x4000, 0x5000):
                reasons.append('RID_PRESERVED_WITHOUT_SUPPORTED_LEVEL_INTERPRETATION')
            if not mask:
                reasons.append('EMPTY_MANDATORY_POLICY_MASK')
            cursor += extent
        if any(raw[cursor:]):
            _bad('MIC_ACL_UNUSED_DATA_UNSUPPORTED', 'nonzero unused ACL bytes are not interpreted')
        sacl_state = 'PRESENT'
    default = None
    if label is None:
        reasons.append('NO_LABEL_ACE_RETURNED')
        default = 'DOCUMENTED_WINDOWS_MEDIUM_DEFAULT_S-1-16-8192_NOT_AN_EXPLICIT_ACE_OR_ENFORCEMENT_TEST'
    return LabelDescriptor(raw, hashlib.sha256(raw).hexdigest(), control, offset, sacl_state, label, default, tuple(reasons))


@dataclass(frozen=True)
class BorrowedFileHandle:
    owner: object
    adapter: object
    entry: object


def _registry(owner):
    registry = getattr(owner, '_native_owned_registry', None)
    if type(registry) is not NativeOwnedRegistry or registry.owner is not owner:
        _bad('MIC_BORROW_INVALID', 'original existing registry owner required')
    attach_registry(SimpleNamespace(), registry)  # Validate its existing private seal.
    return registry


def _borrow(binding):
    if type(binding) is not BorrowedFileHandle:
        _bad('MIC_BORROW_INVALID', 'actual retained file entry/owner/adapter required; no path/PID/dict lookup')
    registry = _registry(binding.owner)
    entry = binding.entry
    if (type(entry) is not _OwnedResource or entry.registry is not registry
            or entry.adapter is not binding.adapter or entry.domain != 'native'
            or entry.state != 'ACQUIRED' or entry.thread_id != threading.get_ident()):
        _bad('MIC_BORROW_INVALID', 'borrowed entry is not live in its original owner registry')
    value = getattr(entry.handle, 'value', entry.handle)
    if (type(value) is not int or not 0 < value < (1 << (8 * C.sizeof(C.c_void_p))) - 16
            or value != entry.value or registry.find(binding.adapter, entry.handle) is not entry):
        _bad('MIC_BORROW_INVALID', 'original HANDLE value/registry entry changed')
    return registry, value


class _FileInfo(C.Structure):
    _pack_ = 4
    _fields_ = [('attributes', C.c_uint32), ('creation', C.c_uint64), ('access', C.c_uint64),
                ('write', C.c_uint64), ('volume', C.c_uint32), ('size_high', C.c_uint32),
                ('size_low', C.c_uint32), ('links', C.c_uint32), ('id_high', C.c_uint32), ('id_low', C.c_uint32)]


class _MemoryInfo(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation_base', C.c_void_p), ('allocation_protect', C.c_uint32),
                ('partition_id', C.c_uint16), ('size', C.c_size_t), ('state', C.c_uint32),
                ('protect', C.c_uint32), ('kind', C.c_uint32)]


@dataclass(frozen=True)
class FileFingerprint:
    volume_serial: int
    file_id: int
    extent: int
    creation_filetime: int
    last_write_filetime: int
    attributes: int
    links: int


@dataclass(frozen=True)
class FileMicObservation:
    before_file: FileFingerprint
    after_file: FileFingerprint
    before_label: LabelDescriptor
    after_label: LabelDescriptor
    state: str = field(default='UNVERIFIED', init=False)
    mic_enforcement: str = field(default='NOT_RUN', init=False)
    issuer: str = field(default='UNCHANGED', init=False)
    limitations: tuple = field(default=('FINITE_BORROWED_OBJECT_ONLY', 'NO_CONTENT_OR_WHOLE_FILESYSTEM_PROOF',
        'NO_TOKEN_COMPARISON_OR_DENY_TEST', 'NO_RESUME_OR_ISOLATION_AUTHORITY', 'LAST_ACCESS_TIME_NOT_A_CONTENT_STABILITY_CHECK'), init=False)

    def as_dict(self):
        return asdict(self)


class _SdAllocation:
    def __init__(self, closer, *, seal):
        if seal is not _SEAL:
            _unknown('original descriptor allocation provenance required')
        self.storage = C.c_void_p()
        self.closer = closer
        self._original_closer = closer
        self.value = None
        self.readable = False
        self.state = 'ACQUIRED'
        self.release_return = None
        self.release_outcome = 'NOT_CALLED'
        self._seal = seal

    def capture(self, storage):
        if storage is not self.storage:
            _unknown('original SDK output storage required')
        self.value = storage.value
        return self.value

    def release(self, pointer):
        if (self._seal is not _SEAL or type(pointer) is not int or pointer != self.value or not self.readable
                or self.closer is not self._original_closer
                or not (self.state == 'ACQUIRED' or
                        self.state == 'UNKNOWN' and self.release_outcome == 'KNOWN_NOT_FREED'
                        and self.storage.value == pointer)):
            _unknown('unvalidated descriptor return cannot acquire replacement LocalFree authority')
        # A lost Python/ctypes return may follow a completed free. Consume any
        # known-failure retry permission before calling the fixed original SDK.
        self.state = 'UNKNOWN'
        self.release_outcome = 'RESULT_UNKNOWN'
        self.release_return = None
        self.release_return = self._original_closer(pointer)
        if self.release_return is not None and not (type(self.release_return) is int and self.release_return == 0):
            if type(self.release_return) is int and self.release_return == pointer:
                self.release_outcome = 'KNOWN_NOT_FREED'
            _unknown('original LocalFree did not confirm release; only the same HLOCAL failure is retryable')
        self.release_outcome = 'RELEASED'
        self.state = 'RELEASED'
        return self.release_return


class NativeFileMicApi:
    """Read-only Windows x64 SDK boundary, attached to an existing owner."""
    def __init__(self, owner):
        self.registry = _registry(owner)
        if os.name != 'nt' or C.sizeof(C.c_void_p) != 8 or C.sizeof(_FileInfo) != 52 or C.sizeof(_MemoryInfo) != 48:
            _bad('MIC_ABI_UNSUPPORTED', 'native Windows x64 SDK layout required')
        self.a = C.WinDLL('advapi32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        pointer = C.c_void_p
        pp = C.POINTER(pointer)
        signatures = (
            (self.a, 'GetSecurityInfo', C.c_uint32, [pointer, C.c_uint32, C.c_uint32, pp, pp, pp, pp, pp]),
            (self.a, 'GetSecurityDescriptorLength', C.c_uint32, [pointer]),
            (self.a, 'GetSecurityDescriptorControl', C.c_int32, [pointer, C.POINTER(C.c_uint16), C.POINTER(C.c_uint32)]),
            (self.a, 'GetSecurityDescriptorSacl', C.c_int32, [pointer, C.POINTER(C.c_int32), pp, C.POINTER(C.c_int32)]),
            (self.k, 'GetFileInformationByHandle', C.c_int32, [pointer, C.POINTER(_FileInfo)]),
            (self.k, 'GetFileType', C.c_uint32, [pointer]),
            (self.k, 'VirtualQuery', C.c_size_t, [pointer, C.POINTER(_MemoryInfo), C.c_size_t]),
            (self.k, 'LocalFree', pointer, [pointer]),
        )
        for library, name, result, args in signatures:
            function = getattr(library, name)
            function.restype, function.argtypes = result, args
        self._get_security = self.a.GetSecurityInfo
        self._local_free = self.k.LocalFree
        self.pending = None
        self.active = False
        self.operation_error = None
        attach_registry(self, self.registry)

    def _check(self, result, operation):
        if type(result) is not int or not result:
            _bad('MIC_SDK_ERROR', operation + ' did not return native success',
                 winerror=getattr(C, 'get_last_error', lambda: None)())

    def _readable(self, pointer, size):
        maximum = (1 << (8 * C.sizeof(C.c_void_p))) - 16
        if type(pointer) is not int or not 65536 <= pointer < maximum or not 0 < size <= MAX_DESCRIPTOR or pointer + size >= maximum:
            _bad('MIC_BUFFER_POINTER', 'descriptor pointer/extent is outside the bounded user address range')
        cursor, end = pointer, pointer + size
        for _ in range(16):
            if cursor >= end:
                return
            info = _MemoryInfo()
            length = self.k.VirtualQuery(cursor, C.byref(info), C.sizeof(info))
            base = info.base or 0
            if (length != C.sizeof(info) or not base <= cursor < base + info.size or base + info.size > maximum
                    or info.state != 0x1000 or info.protect & ~0xFF or info.protect not in (2, 4, 8, 0x20, 0x40, 0x80)):
                _bad('MIC_BUFFER_POINTER', 'SDK descriptor span is not fully readable committed memory')
            cursor = min(end, base + info.size)
        _bad('MIC_BUFFER_POINTER', 'descriptor region budget exceeded')

    def _file(self, handle):
        value = _FileInfo()
        self._check(self.k.GetFileInformationByHandle(handle, C.byref(value)), 'GetFileInformationByHandle')
        if self.k.GetFileType(handle) != 1 or value.attributes & 0x400 or not (value.id_high or value.id_low):
            _bad('MIC_FILE_UNSUPPORTED', 'requires an identified non-reparse disk-file/directory HANDLE')
        return FileFingerprint(value.volume, (value.id_high << 32) | value.id_low,
            (value.size_high << 32) | value.size_low, value.creation, value.write, value.attributes, value.links)

    def _descriptor(self, handle):
        holder = _SdAllocation(self._local_free, seal=_SEAL)
        self.pending = holder  # Retained via the original registry -> API map before SDK can write the slot.
        entry = None
        try:
            try:
                status = self._get_security(handle, SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION,
                    None, None, None, None, C.byref(holder.storage))
            finally:
                if holder.storage.value:
                    entry = self.registry.acquire(self, holder.storage, holder.release, mode='LOCALFREE',
                        label='mic_label_descriptor', domain='mic_sd', value_reader=holder.capture,
                        keepalive=(holder.storage, holder, self, self.a, self.k, self._local_free, self._get_security))
                    self.pending = None
            if type(status) is not int or not 0 <= status <= 0xFFFFFFFF or status:
                _bad('MIC_GET_SECURITY_INFO_FAILED', 'LABEL GetSecurityInfo failed; no label fact or default inferred',
                     native_status=status if type(status) is int else None)
            if entry is None:
                _bad('MIC_SD_MISSING', 'successful GetSecurityInfo returned no descriptor')
            pointer = entry.value
            try:
                self._readable(pointer, 20)
            except BaseException as error:
                entry.state, entry.error = 'UNKNOWN', error
                holder.state = 'UNKNOWN'
                raise
            holder.readable = True
            header = bytes(C.string_at(pointer, 20))
            control, offset = _header(header)
            size = 20
            if offset:
                self._readable(pointer + offset, 8)
                acl_header = bytes(C.string_at(pointer + offset, 8))
                acl_size = struct.unpack_from('<H', acl_header, 2)[0]
                if acl_size < 8 or offset + acl_size > MAX_DESCRIPTOR:
                    _bad('MIC_ACL_BOUNDS', 'SACL extent exceeds the descriptor budget')
                size = offset + acl_size
            self._readable(pointer, size)
            raw = bytes(C.string_at(pointer, size))
            parsed = parse_label_security_descriptor(raw)
            if self.a.GetSecurityDescriptorLength(pointer) != size:
                _bad('MIC_SD_SDK_MISMATCH', 'SDK descriptor length disagrees with the bounded byte layout')
            sdk_control, revision = C.c_uint16(), C.c_uint32()
            self._check(self.a.GetSecurityDescriptorControl(pointer, C.byref(sdk_control), C.byref(revision)), 'GetSecurityDescriptorControl')
            if sdk_control.value != control or revision.value != 1:
                _bad('MIC_SD_SDK_MISMATCH', 'SDK descriptor control/revision differs from raw bytes')
            present, defaulted, sacl = C.c_int32(), C.c_int32(), C.c_void_p()
            self._check(self.a.GetSecurityDescriptorSacl(pointer, C.byref(present), C.byref(sacl), C.byref(defaulted)), 'GetSecurityDescriptorSacl')
            if (present.value not in (0, 1) or bool(present.value) != bool(control & 0x10)
                    or (sacl.value or 0) != (pointer + offset if offset else 0)
                    or (present.value and (defaulted.value not in (0, 1) or bool(defaulted.value) != bool(control & 0x20)))):
                _bad('MIC_SD_SDK_MISMATCH', 'SDK SACL pointer/presence/default flags disagree with original SD')
            if holder.storage.value != pointer or bytes(C.string_at(pointer, size)) != raw:
                _bad('MIC_SD_CHANGED', 'original returned descriptor storage/bytes changed during interpretation')
            return parsed
        finally:
            if entry is not None:
                # An error status may still return an allocated buffer. Validate
                # its base for release, without interpreting its label as valid.
                if not holder.readable and entry.state != 'UNKNOWN':
                    try:
                        self._readable(entry.value, 20)
                        holder.readable = True
                    except BaseException as error:
                        entry.state, entry.error = 'UNKNOWN', error
                        holder.state = 'UNKNOWN'
                entry.release_owned(self.registry.owner)
            elif not holder.storage.value:
                self.pending = None

    def observe(self, binding: BorrowedFileHandle) -> FileMicObservation:
        registry, handle = _borrow(binding)
        if registry is not self.registry:
            _bad('MIC_BORROW_INVALID', 'observer is attached to a different original registry')
        with registry._lock:
            with _retention_lock:
                if registry in _retained or any(e.state == 'UNKNOWN' for e in registry.unresolved()):
                    _unknown('original owner has unresolved native resources; no MIC reacquisition')
                _retained[registry] = self
            self.active = True
            try:
                before_file = self._file(handle)
                before_label = self._descriptor(handle)
                _borrow(binding)
                after_label = self._descriptor(handle)
                after_file = self._file(handle)
                _borrow(binding)
                if before_file != after_file or before_label != after_label:
                    _bad('MIC_OBJECT_CHANGED', 'same retained HANDLE file identity/extent/write/label was not stable')
                return FileMicObservation(before_file, after_file, before_label, after_label)
            except BaseException as error:
                self.operation_error = error
                raise
            finally:
                self.active = False
                remaining = [e for e in registry.unresolved() if e.adapter is self and e.domain == 'mic_sd']
                if self.pending is not None or remaining:
                    _unknown('original MIC owner/storage/LocalFree resource retained; automatic retry prohibited')
                with _retention_lock:
                    _retained.pop(registry)

    def recover_owned(self, owner):
        """Retry only an explicit same-HLOCAL SDK failure via its original owner."""
        registry = self.registry
        with registry._lock, _retention_lock:
            if registry.owner is not owner or _retained.get(registry) is not self or self.active or self.pending is not None:
                _unknown('only the original inactive retained owner can recover confirmed allocation returns')
            entries = tuple(e for e in registry.unresolved() if e.adapter is self and e.domain == 'mic_sd')
            for entry in entries:
                holder = entry.keepalive[1]
                if (type(holder) is not _SdAllocation or holder.state != 'UNKNOWN'
                        or holder.release_outcome != 'KNOWN_NOT_FREED'
                        or entry.keepalive[0] is not holder.storage or entry.keepalive[5] is not holder.closer
                        or entry.handle != holder.value or holder.storage.value != holder.value):
                    _unknown('indeterminate LocalFree result cannot be retried or reconstructed')
            for entry in entries:
                registry.retry_owned(entry, owner)
            if any(e.adapter is self and e.domain == 'mic_sd' for e in registry.unresolved()):
                _unknown('original MIC allocations are still unresolved')
            _retained.pop(registry)


def observe_file_mandatory_label(binding: BorrowedFileHandle, *, api=None) -> FileMicObservation:
    """Read facts for one existing live file entry; never open/close borrowed files."""
    registry, _ = _borrow(binding)
    with registry._lock, _retention_lock:
        if registry in _retained or any(e.state == 'UNKNOWN' for e in registry.unresolved()):
            _unknown('original registry is unresolved; no fresh MIC SDK acquisition')
    selected = NativeFileMicApi(binding.owner) if api is None else api
    if type(selected) is not NativeFileMicApi:
        _bad('MIC_API_INVALID', 'fixed native adapter or explicit software SDK boundary required')
    return selected.observe(binding)
